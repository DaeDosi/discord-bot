"""싱드컵 이벤트 순위 API.

조회(GET)는 공개다 — /stats와 같은 비로그인 통계 페이지에서 쓴다.
수동 수집(POST)은 외부 API 호출을 유발하므로 secret으로 보호한다.

원본(네이버 라운지) 응답 구조에 의존하는 코드는 전부 singcup_collector 안에 있다.
여기서는 정규화된 dict만 다룬다.
"""
import asyncio
import hmac
import os
import time

import singcup_obs
import singcup_split_api as split
from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from singcup_clips import (
    backfill_status,
    baseline_report,
    clip_diagnosis,
    deleted_clip_audit,
    recheck_clip_deletion,
    restore_deleted_clips,
    discover_new_clips,
    load_main_entry,
    load_streamer_clips,
    main_cache_stats,
    metrics_sweep_stats,
    recheck_untagged_clips,
    rediscover_clip,
    refresh_metrics,
    refresh_one_clip,
    reset_backfill,
    retag_stats,
    run_backfill,
    snapshot_duplicate_report,
)
from singcup_collector import (
    ADMIN_SECRET,
    MODES,
    ST_FAILED,
    collect_once,
    load_rankings,
    load_status,
    prune_out_of_range,
)
from singcup_sweep import recent_runs, run_sweep, sweep_status

router = APIRouter(prefix="/api/singcup", tags=["singcup"])


@router.get("/rankings")
async def rankings(limit: int = 200):
    """버프 순 랭킹. 수집이 실패해도 DB의 마지막 정상 데이터를 그대로 돌려준다."""
    return await load_rankings(limit=limit)


@router.get("/status")
async def status():
    """수집기 헬스체크 — Railway에서 네이버 API 접근이 되는지 확인할 때 쓴다."""
    return await load_status()


def _require_secret(secret: str | None):
    """secret이 설정되지 않은 배포에서는 아예 막는다(빈 값과 일치해 열리는 사고 방지)."""
    if not ADMIN_SECRET:
        raise HTTPException(
            status_code=503,
            detail="SINGCUP_ADMIN_SECRET이 설정되지 않아 관리 기능을 사용할 수 없습니다.")
    if not secret or not _consteq(secret, ADMIN_SECRET):
        raise HTTPException(status_code=401, detail="인증에 실패했습니다.")


@router.post("/collect")
async def collect(mode: str = "normal",
                  x_singcup_secret: str | None = Header(default=None)):
    """수동 수집. SINGCUP_ADMIN_SECRET 헤더가 맞아야 실행된다.

    mode=normal   정기 수집과 동일
    mode=backfill 과거 구간을 깊게 훑는다(이벤트 시작일을 앞당긴 뒤 1회 실행)
    mode=dry-run  DB에 쓰지 않고 몇 건이 잡히는지만 확인
    """
    _require_secret(x_singcup_secret)
    if mode not in MODES:
        raise HTTPException(status_code=400,
                            detail=f"mode는 {', '.join(MODES)} 중 하나여야 합니다.")
    return await collect_once(force=True, mode=mode)


@router.post("/prune")
async def prune(dry_run: bool = True,
                x_singcup_secret: str | None = Header(default=None)):
    """이벤트 기간 밖으로 벗어난 행을 정리(active=0)한다. 기본은 dry-run.

    행을 실제로 삭제하지 않으며 이 이벤트의 singcup_feeds 행만 건드린다.
    """
    _require_secret(x_singcup_secret)
    return await prune_out_of_range(dry_run=dry_run)


def _consteq(a: str, b: str) -> bool:
    """타이밍 공격 여지를 줄이는 상수시간 비교."""
    return hmac.compare_digest(a.encode(), b.encode())


# ── 클립 기반(메인/랭킹) ────────────────────────────────────────────────────
# 자유게시판 버프(/rankings)와는 별개 데이터다. 메인과 랭킹 화면은 이쪽을 쓴다.
# 브라우저·프록시가 이 응답을 재사용해도 되는 시간. 서버 캐시 TTL(20초)과 맞춘다 —
# 더 길게 잡으면 순위 갱신이 화면에 늦게 도착하고, 짧게 잡으면 재마운트마다 네트워크가
# 나간다. 이 응답에는 인증·개인화 요소가 전혀 없으므로(비로그인 공개 통계) public이다.
MAIN_MAX_AGE = int(os.getenv("SINGCUP_MAIN_MAX_AGE", "20"))


def _etag_matches(header: str | None, etag: str) -> bool:
    """If-None-Match 대조. 여러 개가 콤마로 오고, 앞단이 W/를 떼거나 붙일 수 있다."""
    if not header:
        return False
    if header.strip() == "*":
        return True
    bare = etag.removeprefix('W/')
    for candidate in header.split(","):
        c = candidate.strip()
        if c == etag or c.removeprefix('W/') == bare:
            return True
    return False


@router.get("/main")
async def main(request: Request, limit: int = 200):
    """#싱드컵 태그 스트리머 목록 — 대표 클립·비공식 예상 인기점수·변화량·현재 라이브.

    응답이 참가자 전원(약 850KB, gzip 약 240KB)이라 호출 1회의 전송 비용이 크다.
    그래서 여기서는 두 가지를 한다.

    1) 캐시에 미리 만들어 둔 bytes를 그대로 내보낸다 — 요청마다 재직렬화하지 않는다.
    2) ETag로 조건부 요청을 받는다 — 내용이 그대로면 304(본문 0바이트)로 끝낸다.

    프론트의 폴링 주기를 늘리는 것과 별개의 방어선이다. 주기를 늘려도 사용자가 많으면
    호출은 다시 늘어나는데, 그때 실제로 나가는 bytes를 줄이는 건 이쪽이다.
    """
    t0 = singcup_obs.begin()
    try:
        entry, source = await load_main_entry(limit=limit)
        etag, body = entry["etag"], entry["body"]
        headers = {
            "ETag": etag,
            "Cache-Control": f"public, max-age={MAIN_MAX_AGE}, stale-while-revalidate=60",
            # 앞단(Railway 엣지)이 gzip 여부에 따라 다른 표현을 캐시하도록 명시한다
            "Vary": "Accept-Encoding",
        }
        if _etag_matches(request.headers.get("if-none-match"), etag):
            status, out, sent = 304, Response(status_code=304, headers=headers), 0
        else:
            status, sent = 200, len(body)
            out = Response(content=body, media_type="application/json",
                           headers=headers)
        singcup_obs.record(request, status=status, bytes_out=sent,
                           ms=(time.perf_counter() - t0) * 1000,
                           cache=("304:" + source) if status == 304 else source,
                           limit=limit, full_bytes=len(body))
        return out
    finally:
        singcup_obs.end()


@router.get("/observability")
async def observability(minutes: int = 30,
                        x_singcup_secret: str | None = Header(default=None)):
    """/main 호출 관측 — 분당 호출 수·전송 bytes·캐시 적중·304 비율·유니크 클라이언트.

    비용 대응의 배포 전후 비교를 이 응답 하나로 만들 수 있게 모아 둔다.
    원문 IP나 UA 전문은 저장하지 않는다(singcup_obs 참고).
    """
    _require_secret(x_singcup_secret)
    return {"requests": singcup_obs.snapshot(minutes), "serverCache": main_cache_stats()}


@router.get("/streamers/{channel_id}/clips")
async def streamer_clips(channel_id: str):
    """카드의 '싱드컵 태그 클립 N개'를 펼칠 때 쓰는 목록."""
    return await load_streamer_clips(channel_id)


# ── 관리 작업 ───────────────────────────────────────────────────────────────
# 백필은 완료까지 수 분~수십 분이 걸릴 수 있다. 요청을 붙잡고 기다리면 Railway
# 요청 제한 시간에 걸리고 중간 실패 시 어디까지 처리했는지도 알 수 없다.
# 그래서 작업을 백그라운드로 띄우고 즉시 202로 응답한 뒤, 진행 상황은 status로 본다.
@router.post("/clips/backfill", status_code=202)
async def clips_backfill(restart: bool = False,
                         x_singcup_secret: str | None = Header(default=None)):
    """과거 데이터 백필을 시작한다(이미 실행 중이면 락에 막혀 그대로 둔다).

    restart=true 면 커서·수치를 초기화하고 처음부터 다시 훑는다.

    BackgroundTasks가 아니라 create_task로 완전히 떼어 낸다 — BackgroundTasks는
    응답을 보낸 뒤에도 그 요청의 처리 흐름 안에서 실행돼, 수 분짜리 작업이면
    워커를 그동안 붙잡는다. 진행 상황은 /clips/backfill/status 로 확인한다.
    """
    _require_secret(x_singcup_secret)
    if restart:
        await reset_backfill()
    asyncio.create_task(run_backfill())
    return {"accepted": True, "state": await backfill_status()}


@router.get("/clips/backfill/status")
async def clips_backfill_status(x_singcup_secret: str | None = Header(default=None)):
    """백필 진행 상황 — 처리 건수·현재 커서·도달 날짜·실패 수·완료 여부."""
    _require_secret(x_singcup_secret)
    return await backfill_status()


@router.post("/clips/discover")
async def clips_discover(x_singcup_secret: str | None = Header(default=None)):
    """신규 클립 탐색 1회(정기 루프와 동일). 최신 페이지만 훑는다."""
    _require_secret(x_singcup_secret)
    return await discover_new_clips()


@router.post("/clips/refresh")
async def clips_refresh(limit: int | None = None,
                        x_singcup_secret: str | None = Header(default=None)):
    """기존 클립의 하트·조회수 갱신 1회. 목록은 훑지 않는다."""
    _require_secret(x_singcup_secret)
    return await refresh_metrics(limit=limit)


@router.post("/clips/{clip_uid}/refresh")
async def clips_refresh_one(clip_uid: str,
                            x_singcup_secret: str | None = Header(default=None)):
    """클립 1건만 즉시 갱신한다 — 전체 갱신이 그 클립을 집을 때까지 기다리지 않는다.

    DB를 직접 쓰지 않고 fetch_card → _apply_metrics → 대표/점수/순위 재계산까지
    정기 사이클과 같은 경로를 탄다. 응답에 db_before/fetched/db_after가 함께 온다.
    """
    _require_secret(x_singcup_secret)
    res = await refresh_one_clip(clip_uid)
    note = str(res.get("note", ""))
    if res.get("status") == ST_FAILED and "형식" in note:
        raise HTTPException(status_code=400, detail=note)
    if res.get("status") == ST_FAILED and "DB에 없" in note:
        raise HTTPException(status_code=404, detail=note)
    return res


@router.post("/clips/recheck-untagged")
async def clips_recheck(limit: int | None = None,
                        x_singcup_secret: str | None = Header(default=None)):
    """뒤늦게 #싱드컵을 붙인 클립을 찾아 등록한다(정기 루프와 동일).

    "내 클립이 안 올라온다"는 제보가 오면 목록 전체를 다시 훑을 필요 없이 이걸 부른다.
    """
    _require_secret(x_singcup_secret)
    return await recheck_untagged_clips(limit=limit)


@router.post("/clips/{clip_uid}/rediscover")
async def clips_rediscover(clip_uid: str,
                           x_singcup_secret: str | None = Header(default=None)):
    """클립 1건을 즉시 재탐색해 등록한다 — 전체 백필 차례를 기다리지 않는다.

    상세·카드 조회 → 기간/카테고리 확인 → 태그 판정 → 소유 채널 확정 →
    UPSERT → 스트리머 등록 → 대표 재선정 → 점수·랭킹 재계산까지 정상 경로를 탄다.
    """
    _require_secret(x_singcup_secret)
    res = await rediscover_clip(clip_uid)
    if res.get("status") == ST_FAILED and "형식" in str(res.get("note", "")):
        raise HTTPException(status_code=400, detail=res["note"])
    return res


@router.get("/clips/retag-stats")
async def clips_retag_stats(x_singcup_secret: str | None = Header(default=None)):
    """재확인 큐 건전성 — 남은 대상·상태별 건수·소진 예상 시간."""
    _require_secret(x_singcup_secret)
    return await retag_stats()


@router.get("/snapshots/duplicates")
async def snapshot_duplicates(x_singcup_secret: str | None = Header(default=None)):
    """기존 스냅샷 중복 현황(read-only). 아무것도 삭제하지 않는다.

    버킷 도입 전 행은 snapshot_bucket이 NULL이라 유니크 제약 밖에 있다.
    정리 여부는 이 보고를 보고 별도로 판단한다.
    """
    _require_secret(x_singcup_secret)
    return await snapshot_duplicate_report()


@router.get("/snapshots/baseline")
async def snapshot_baseline(window_minutes: int | None = None,
                            x_singcup_secret: str | None = Header(default=None)):
    """1시간 증감의 기준선 진단(read-only).

    "왜 전원이 NEW인가"를 이 응답 하나로 판별한다 — 선택된 버킷, 그 버킷의 인원과
    collected_at 최소·최대, 커버리지, partial 여부, 후보 버킷 목록.
    """
    _require_secret(x_singcup_secret)
    return await baseline_report(None if window_minutes is None
                                 else max(1, window_minutes) * 60)


@router.get("/sweep/status")
async def sweep_status_ep():
    """매시 정각 전체 갱신의 진행 상황(공개).

    화면에 '다음 전체 갱신 / 현재 회차 진행률 / 마지막 완료'를 그리는 데 쓴다.
    """
    return await sweep_status()


@router.get("/sweep/runs")
async def sweep_runs(limit: int = 24,
                     x_singcup_secret: str | None = Header(default=None)):
    """최근 회차 이력 — 누락(missed)·중복(skipped_overlap)·429를 한눈에 본다."""
    _require_secret(x_singcup_secret)
    return await recent_runs(limit)


@router.post("/sweep/run")
async def sweep_run_now(scheduled_at: int | None = None,
                        x_singcup_secret: str | None = Header(default=None)):
    """회차 1개를 수동 실행한다(기본은 현재 정각). 이미 실행된 회차는 건너뛴다."""
    _require_secret(x_singcup_secret)
    return await run_sweep(scheduled_at)


@router.get("/clips/sweep-stats")
async def clips_sweep_stats(x_singcup_secret: str | None = Header(default=None)):
    """전체 순회 건전성 — 한 바퀴 SLA vs 실제 최장 방치 시간.

    oldest_age_hours가 full_sweep_hours를 크게 넘으면(starving=true) 어떤 클립이
    갱신 큐에서 굶고 있다는 뜻이다. 정렬/예산 회귀를 잡는 1차 지표다.
    """
    _require_secret(x_singcup_secret)
    return await metrics_sweep_stats()


@router.get("/clips/deleted")
async def clips_deleted(limit: int = 200,
                        x_singcup_secret: str | None = Header(default=None)):
    """삭제로 확정된(또는 legacy 비활성) 클립 목록 — **롤백 대상 특정용 감사 쿼리**."""
    _require_secret(x_singcup_secret)
    return await deleted_clip_audit(limit)


@router.post("/clips/restore")
async def clips_restore(body: dict,
                        x_singcup_secret: str | None = Header(default=None)):
    """지정한 clip_uid만 되살린다(롤백).

    전체를 무조건 되돌리지 않는다 — 진짜 삭제된 클립까지 살아나면 순위가 다시
    틀어진다. `/clips/deleted`에서 대상을 골라 넘긴다. 되돌린 뒤에는 정상 경로로
    대표·점수·순위·캐시·스냅샷이 다시 만들어진다.
    """
    _require_secret(x_singcup_secret)
    uids = body.get("clipUids") if isinstance(body, dict) else None
    if not isinstance(uids, list) or not all(isinstance(u, str) for u in uids):
        raise HTTPException(status_code=400,
                            detail="clipUids는 문자열 배열이어야 합니다.")
    if not uids:
        raise HTTPException(status_code=400, detail="clipUids가 비어 있습니다.")
    return await restore_deleted_clips(uids[:500],
                                       reason=str(body.get("reason") or "manual")[:40])


@router.post("/clips/{clip_uid}/recheck-deletion")
async def clips_recheck_deletion(clip_uid: str,
                                 x_singcup_secret: str | None = Header(default=None)):
    """이 클립이 정말 삭제됐는지 상세 API로 지금 한 번 더 확인한다.

    **DB를 직접 고치지 않는다.** 자동 경로와 같은 판정 규칙(명시적 404 2회)을 타고,
    상태가 바뀐 경우에만 대표·점수·순위·스냅샷을 정상 경로로 다시 만든다.
    """
    _require_secret(x_singcup_secret)
    return await recheck_clip_deletion(clip_uid)


@router.get("/clips/{clip_uid}/diagnose")
async def clips_diagnose(clip_uid: str,
                         x_singcup_secret: str | None = Header(default=None)):
    """클립 1건의 DB 수치·대기열 위치·제외 사유. 값이 안 도는 클립을 지목해 본다."""
    _require_secret(x_singcup_secret)
    return await clip_diagnosis(clip_uid)


# ── 분리 API (Shadow) ───────────────────────────────────────────────────────
# `/main`은 그대로 둔다. 여기는 같은 데이터를 '필요한 만큼만' 내려 주는 경로이며
# SINGCUP_SPLIT_API_ENABLED=false 인 동안에는 아예 열리지 않는다.
#
# 조회 경로에서 DB 쓰기·랭킹 재계산·스냅샷 생성을 하지 않는다. 이미 만들어져 있는
# `/main` 캐시 엔트리를 불변 스냅샷으로 읽기만 한다.
def _require_split_api():
    if not split.SPLIT_API_ENABLED:
        raise HTTPException(status_code=404, detail="분리 API가 비활성화되어 있습니다.")


def _split_error(e: Exception):
    if isinstance(e, split.SnapshotExpired):
        return JSONResponse(status_code=409, content={
            "error": "snapshot_expired", "latestSnapshotVersion": e.latest,
            "retryFromStart": True})
    return JSONResponse(status_code=400, content={"error": str(e)})


async def _split_call(fn, **kw):
    """분리 API 공통 처리.

    **조회 경로에서 스냅샷을 만들지 않는다.** 스냅샷 생산은 랭킹 계산이 끝나는
    지점(recompute_ranking → publish_snapshot) 하나로 모여 있다. 조회가 생산자가
    되면 트래픽이 없을 때 생산이 멈추고, 재시작 직후 레지스트리가 영영 빌 수 있다.
    아직 스냅샷이 없으면 503으로 명확히 알린다.

    (P1.5로 `/main` 계산 자체에서는 DB 쓰기가 사라졌지만, 조회를 생산자로 만들지
    않는다는 규칙은 위 이유로 그대로 유지한다.)
    """
    _require_split_api()
    if split.latest() is None:
        return JSONResponse(status_code=503, content={
            "error": "snapshot_not_ready",
            "detail": "랭킹 스냅샷이 아직 준비되지 않았습니다. 잠시 후 다시 시도하세요."})
    try:
        out = fn(**kw)
    except (split.SnapshotExpired, split.CursorError) as e:
        return _split_error(e)
    # 페이지 응답은 (버전, 필터, 정렬, 커서, size)에 대해 결정적이라 직렬화 결과를
    # 그대로 캐시한다. `/main`이 캐시된 bytes를 내보내는 것과 같은 이유다.
    key = out.pop("_renderKey", None)
    if key is None:
        return out
    return Response(content=split.render(key, out), media_type="application/json")


@router.get("/summary")
async def split_summary(snapshotVersion: str | None = None):
    """첫 화면용 최소 응답 — KPI·급상승·집계 시각. 실측 gzip 약 2.0KB."""
    return await _split_call(split.summary, snapshot_version=snapshotVersion)


@router.get("/rankings-page")
async def split_rankings(size: int | None = None, cursor: str | None = None,
                         sort: str = "score", direction: str = "desc",
                         snapshotVersion: str | None = None):
    """전체 참가자를 서버에서 정렬한 뒤 한 페이지만. 실측 100명 gzip 약 29KB.

    경로 이름이 `/rankings`가 아닌 이유: `/api/singcup/rankings`는 이미 자유게시판
    홍보글(별개 데이터)이 쓰고 있다. 같은 이름으로 덮으면 그 화면이 깨진다.
    """
    return await _split_call(split.rankings, size=size, cursor=cursor, sort=sort,
                             direction=direction, snapshot_version=snapshotVersion)


@router.get("/search")
async def split_search(q: str = "", size: int | None = None, cursor: str | None = None,
                       sort: str = "score", direction: str = "desc",
                       snapshotVersion: str | None = None):
    """**전체 참가자 집합**에서 닉네임 검색. 받아온 페이지 안에서 찾지 않는다."""
    return await _split_call(split.search, q=q, size=size, cursor=cursor, sort=sort,
                             direction=direction, snapshot_version=snapshotVersion)


@router.get("/live")
async def split_live(size: int | None = None, cursor: str | None = None,
                     snapshotVersion: str | None = None):
    """방송 중인 참가자만. 실측 344명 전체는 gzip 약 89KB라 페이지로 나눈다."""
    return await _split_call(split.live, size=size, cursor=cursor,
                             snapshot_version=snapshotVersion)


@router.get("/movers")
async def split_movers(range: str = "1h", size: int | None = None,
                       snapshotVersion: str | None = None):
    return await _split_call(split.movers, range_=range, size=size,
                             snapshot_version=snapshotVersion)


@router.get("/delta")
async def split_delta(sinceVersion: str | None = None):
    """버전 간 변경분 — 인터페이스만 고정하고 아직 구현하지 않는다."""
    _require_split_api()
    return split.delta(since_version=sinceVersion)


@router.get("/snapshots/registry")
async def snapshot_registry(x_singcup_secret: str | None = Header(default=None)):
    """스냅샷 레지스트리 상태(관리자) — 보존 버전 수·항목 수·만료 요청 수."""
    _require_secret(x_singcup_secret)
    return split.stats()
