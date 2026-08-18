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

import singcup_audit
import singcup_final
import singcup_obs
import singcup_split_api as split
from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from singcup_clips import (
    backfill_status,
    baseline_report,
    clip_diagnosis,
    deleted_clip_audit,
    discover_new_clips,
    load_main_entry,
    load_streamer_clips,
    main_cache_stats,
    metrics_sweep_stats,
    recheck_clip_deletion,
    recheck_untagged_clips,
    rediscover_clip,
    refresh_metrics,
    refresh_one_clip,
    reset_backfill,
    restore_deleted_clips,
    retag_stats,
    run_backfill,
    snapshot_duplicate_report,
)
import singcup_piku as piku
import singcup_qualifiers as sq
from singcup_collector import (
    ADMIN_SECRET,
    MODES,
    ST_FAILED,
    collect_once,
    load_rankings,
    load_status,
    prune_out_of_range,
)
from singcup_collector import (
    applications_open as collector_applications_open,
)
from singcup_collector import (
    live_feature_open as collector_live_feature_open,
)
# 게이트는 **함수로** 가져온다 — 값으로 읽으면 import 시점에 굳어 환경변수를
# 바꿔도 재시작 전까지 반영되지 않는다.
from singcup_collector import (
    unofficial_ranking_open as collector_unofficial_ranking_open,
)
from singcup_sweep import recent_runs, run_sweep, sweep_status

router = APIRouter(prefix="/api/singcup", tags=["singcup"])


# ── 기능 종료 응답 ──────────────────────────────────────────────────────────
#
# 비공식 인기점수 랭킹과 싱드컵 LIVE는 **기능이 내려간** 상태다(기본값).
# 오류(4xx/5xx)로 돌려주지 않는 이유: 이건 장애가 아니라 확정된 제품 상태이고,
# 프런트가 `catch`로 받으면 "불러오지 못했습니다"라는 잘못된 문구가 나간다.
# 200 + `retired: true`로 명시하고 **기록을 볼 곳**을 함께 알린다.
#
# 데이터는 지우지 않았다 — 확정본(`/final-ranking`)이 그대로 있고 원본 테이블도 남아 있다.

_ARCHIVE_URL = "/api/singcup/final-ranking"

#: 부문 키 — **`singcup_qualifiers`·`singcup_piku`와 같은 값**이다.
#: 세 곳이 갈라지면 PIKU 매핑이 조용히 어긋나므로 여기서 한 번만 정의하고 가져다 쓴다.
DIVISIONS: tuple[str, ...] = ("female_solo", "male_solo", "groups")
DIVISION_LABELS: dict[str, str] = {
    "female_solo": "여성 솔로",
    "male_solo": "남성 솔로",
    "groups": "그룹",
}


def _retired(feature: str, reason: str) -> JSONResponse:
    return JSONResponse(status_code=200, content={
        "retired": True,
        "feature": feature,
        "reason": reason,
        # 프런트가 "기록 보기"를 그릴 수 있게 목적지를 응답에 담는다 —
        # 화면에 URL을 하드코딩하면 경로가 바뀔 때 한쪽만 고쳐진다.
        "archiveUrl": _ARCHIVE_URL,
        # 소비처가 배열을 기대하므로 빈 배열을 함께 준다(형태 파괴 방지).
        "streamers": [], "items": [], "rows": [],
    })


def _unofficial_retired_or_none():
    """비공식 랭킹이 내려가 있으면 종료 응답, 살아 있으면 None."""
    if collector_unofficial_ranking_open():
        return None
    return _retired("unofficial_ranking",
                    "싱드컵 비공식 인기점수 랭킹 제공이 종료되었습니다.")


@router.get("/rankings")
async def rankings(limit: int = 200):
    """버프 순 랭킹. 수집이 실패해도 DB의 마지막 정상 데이터를 그대로 돌려준다."""
    retired = _unofficial_retired_or_none()
    if retired is not None:
        return retired
    return await load_rankings(limit=limit)


@router.get("/status")
async def status():
    """수집기 헬스체크 + **기능 게이트 현재 상태**.

    게이트를 응답에 실어 보내는 이유: 화면이 "신청이 종료되었습니다"를 스스로
    판단하면 서버가 다시 열렸을 때 화면만 닫힌 채로 남는다. 판정은 서버가 하고
    화면은 그대로 그린다.
    """
    base = await load_status()
    base["gates"] = {
        "applicationsOpen": collector_applications_open(),
        "unofficialRankingOpen": collector_unofficial_ranking_open(),
        "liveFeatureOpen": collector_live_feature_open(),
    }
    # 닫힌 기능마다 화면에 그대로 쓸 문구를 함께 준다 — 프런트가 문구를 만들면
    # 같은 규칙에 설명이 두 벌이 된다.
    base["notices"] = {
        "applications": None if base["gates"]["applicationsOpen"]
                        else "싱드컵 신청이 종료되었습니다.",
        "unofficialRanking": None if base["gates"]["unofficialRankingOpen"]
                             else "싱드컵 비공식 인기점수 랭킹 제공이 종료되었습니다.",
        "live": None if base["gates"]["liveFeatureOpen"]
                else "싱드컵 LIVE 화면 제공이 종료되었습니다.",
    }
    base["archiveUrl"] = _ARCHIVE_URL
    return base


# 관리자 API는 공용 미들웨어(기본 150 req/60s)와 별도로 더 좁은 한도를 둔다.
# secret이 유출됐을 때 외부 API를 대량으로 때리는 통로가 되지 않게 하기 위한 것이라
# IP가 아니라 **엔드포인트 단위**로 센다.
_ADMIN_WINDOW = 60.0
_ADMIN_LIMIT = int(os.getenv("SINGCUP_ADMIN_RATE_LIMIT", "20"))
_admin_hits: dict[str, list[float]] = {}


def _admin_rate_limit(key: str):
    now = time.monotonic()
    q = _admin_hits.setdefault(key, [])
    cutoff = now - _ADMIN_WINDOW
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= _ADMIN_LIMIT:
        raise HTTPException(status_code=429,
                            detail="관리 요청이 너무 잦습니다. 잠시 후 다시 시도하세요.")
    q.append(now)


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
    retired = _unofficial_retired_or_none()
    if retired is not None:
        return retired
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


@router.get("/final-ranking")
async def final_ranking(request: Request):
    """비공식 인기점수 랭킹 **확정본**. 이벤트 종료 시점에 한 번 얼린 응답이다.

    `/main`과 나눠 둔 이유가 요구의 핵심이다. 두 화면이 `/main` 하나를 공유하는데,
    `/main`을 얼리면 **공식 예선 참가자 화면의 하트·조회수까지 굳는다.** 그쪽은
    최신값을 계속 써야 하므로, 얼릴 응답만 별도 경로로 뺐다.

    부수 효과로 전송 비용도 준다: 내용이 영원히 같아 `immutable` 캐시를 걸 수 있고,
    조건부 요청은 항상 304로 끝난다(ETag가 다시 계산되지 않는다).

    ── 확정본이 아직 없을 때 (fail-closed) ─────────────────────────────────
    **실시간 값으로 물러서지 않는다.** 확정본을 못 만들었다고 실시간 랭킹을 대신
    내보내면, 사용자가 "멈춰 달라"고 한 그 화면이 계속 갱신된다 — 게다가 실패가
    정상 응답에 가려져 아무도 눈치채지 못한다. 그래서 **503 + `finalizing`**으로
    명시하고, 화면은 '최종 집계 준비 중'을 보여준다.

    동결이 꺼져 있을 때(`SINGCUP_RANKING_FREEZE_ENABLED=false`)만 `frozen: false`를
    주고, 그때는 프론트가 기존 실시간 경로를 쓴다.
    """
    if not singcup_final.ranking_frozen():
        return JSONResponse({"frozen": False, "reason": "freeze_disabled"})
    entry = await singcup_final.load_entry()
    if not entry:
        # startup 확정이 일시적 DB 잠금으로 실패했을 수 있다. 그대로 두면 다음
        # 재시작까지 영영 503이므로, 여기서 **한 번만** 재시도를 예약한다.
        # 이 요청은 기다리지 않고 503을 반환한다 — 확정 계산은 참가자 전원을
        # 조인하는 무거운 쿼리라 공개 GET을 붙잡으면 안 된다. 다음 요청이 200을 받는다.
        singcup_final.schedule_finalize_if_needed(source="request")
        return JSONResponse(
            {"status": "finalizing", "frozen": True,
             "detail": "최종 집계를 준비하고 있습니다."},
            status_code=503,
            headers={"Retry-After": str(singcup_final.RETRY_AFTER_SECONDS),
                     # 프론트가 이 값으로 자동 재확인 간격을 정한다. 교차 출처라
                     # 명시적으로 열어 주지 않으면 브라우저가 읽지 못하고, 그러면
                     # 안전한 기본 간격으로 떨어진다(동작은 하되 서버 의도가 무시된다).
                     "Access-Control-Expose-Headers": "Retry-After",
                     "Cache-Control": "no-store"})

    etag = entry["etag"]
    headers = {
        "ETag": etag,
        # 확정본은 변하지 않는다. 재검증조차 필요 없다.
        "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400",
        "Vary": "Accept-Encoding",
    }
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return Response(content=entry["body"], media_type="application/json",
                    headers=headers)


@router.get("/final-ranking/status")
async def final_ranking_status():
    """확정 여부·확정 시각 — 운영 점검용(무인증, 값은 메타데이터뿐)."""
    return await singcup_final.status()


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
    _admin_rate_limit("recheck-deletion")
    if not singcup_audit.valid_clip_uid(clip_uid):
        raise HTTPException(status_code=400, detail="clip_uid 형식이 올바르지 않습니다.")
    return await recheck_clip_deletion(clip_uid)


@router.get("/audit/status")
async def audit_status():
    """권위 감사(anti-entropy) 진행 상태. 공개 조회 — **쓰기가 없다.**

    락 owner token·secret·외부 응답 본문은 절대 넣지 않는다.
    """
    return await singcup_audit.audit_status()


@router.post("/clips/{clip_uid}/audit")
async def clips_audit_now(clip_uid: str,
                          x_singcup_secret: str | None = Header(default=None)):
    """단건 권위 검사를 지금 예약한다(Hot lane). **판정은 자동 경로와 동일하다.**

    임의 URL이나 헤더를 받지 않는다 — 입력은 clip_uid 하나이고, 호출 대상은
    치지직 고정 호스트·경로다.
    """
    _require_secret(x_singcup_secret)
    _admin_rate_limit("audit")
    if not singcup_audit.valid_clip_uid(clip_uid):
        raise HTTPException(status_code=400, detail="clip_uid 형식이 올바르지 않습니다.")
    if not singcup_audit.enabled():
        # master OFF에서는 DB도 건드리지 않는다(strict OFF).
        return {"clipUid": clip_uid, "hinted": False,
                "note": "SINGCUP_DELETION_RECONCILE_ENABLED가 꺼져 있습니다."}
    hinted = await singcup_audit.hint_clip(clip_uid, singcup_audit.HINT_MANUAL)
    return {"clipUid": clip_uid, "hinted": hinted}


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

    **맨 앞에 기능 종료 검사가 있다.** 분리 API(`/summary`·`/rankings-page`·
    `/search`·`/live`·`/movers`)는 전부 비공식 랭킹 화면의 부품이므로, 한 곳에서
    막지 않으면 새 엔드포인트가 추가될 때마다 검사를 빠뜨리게 된다.

    **조회 경로에서 스냅샷을 만들지 않는다.** 스냅샷 생산은 랭킹 계산이 끝나는
    지점(recompute_ranking → publish_snapshot) 하나로 모여 있다. 조회가 생산자가
    되면 트래픽이 없을 때 생산이 멈추고, 재시작 직후 레지스트리가 영영 빌 수 있다.
    아직 스냅샷이 없으면 503으로 명확히 알린다.

    (P1.5로 `/main` 계산 자체에서는 DB 쓰기가 사라졌지만, 조회를 생산자로 만들지
    않는다는 규칙은 위 이유로 그대로 유지한다.)
    """
    retired = _unofficial_retired_or_none()
    if retired is not None:
        return retired
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
    """방송 중인 참가자만. 실측 344명 전체는 gzip 약 89KB라 페이지로 나눈다.

    **이 경로는 '싱드컵 LIVE' 기능 게이트를 따로 본다.** 비공식 랭킹과 축이 다르므로
    한쪽만 되살릴 수 있어야 한다. 공식 예선 참가자 화면의 LIVE 배지는 여기가 아니라
    `/qualifiers`가 서빙하며, 이 게이트와 무관하게 계속 동작한다.
    """
    if not collector_live_feature_open():
        return _retired("singcup_live", "싱드컵 LIVE 화면 제공이 종료되었습니다.")
    return await _split_call(split.live, size=size, cursor=cursor,
                             snapshot_version=snapshotVersion)


@router.get("/qualifiers")
async def qualifiers(division: str | None = None):
    """**공식 예선 참가자만** — 부문별 명단 + 대표 클립 썸네일 + 현재 라이브 여부.

    이 엔드포인트가 따로 있는 이유는 하나다: **"LIVE 표시는 공식 예선 참가자에게만"을
    서버에서 강제하기 위해서**다. `/main`을 필터링해 쓰면 응답에는 비참가자가 계속
    들어 있어, 화면 한 줄만 바뀌면 정책이 조용히 풀린다. 여기서는 명단 밖 채널이
    **구조적으로** 들어올 수 없다(참가자 id 집합에서 출발한다).

    부가 효과로 응답이 작아진다 — `/main`은 참가자 전원 약 850KB인데 이쪽은
    공식 명단(솔로 128 + 그룹 32팀)에 한정된다.

    비공식 랭킹·LIVE 기능 게이트와 **무관하게 동작한다.** 공식 명단은 대회의 확정
    산출물이라 기능 종료와 함께 감출 대상이 아니다.
    """
    div = division if division in DIVISIONS else None
    keys = [div] if div else list(DIVISIONS)

    # 라이브 여부·클립은 우리가 이미 수집해 둔 값에서만 읽는다(외부 호출 0).
    ids = sq.channel_ids(div) if div else set(sq.ALL_CHANNEL_IDS)
    live_map, clip_map, song_map = await _qualifier_side_data(ids)

    def solo_row(r: dict) -> dict:
        cid = r["channelId"]
        clip = clip_map.get(cid) or {}
        return {
            "channelId": cid,
            "announcedName": r["name"],
            "officialOrder": r["order"],
            "channelName": (live_map.get(cid) or {}).get("channelName")
                           or clip.get("channelName") or r["name"],
            "channelImageUrl": clip.get("channelImageUrl") or "",
            "clipUid": clip.get("clipUid"),
            "clipTitle": clip.get("clipTitle") or "",
            "clipThumbnailUrl": clip.get("clipThumbnailUrl") or "",
            # 운영자가 대표 클립을 직접 지정했는지 — 화면에서 자동 선정과 구분한다.
            "clipIsOverride": bool(clip.get("isOverride")),
            # 카드 2줄째. **없으면 빈 문자열이고, 화면은 그 줄을 그리지 않는다.**
            "songTitle": (song_map.get(cid) or {}).get("songTitle", ""),
            "artistName": (song_map.get(cid) or {}).get("artistName", ""),
            # 공식 예선 참가자만 이 값을 받을 수 있다(위 주석).
            "live": live_map.get(cid),
        }

    out: dict = {"divisions": {}, "counts": {}}
    for k in keys:
        if k == "groups":
            rows = [{
                "teamNumber": g["teamNumber"],
                "groupEntryId": g["groupEntryId"],
                "members": [solo_row(m) for m in g["members"]],
            } for g in sq.QUALIFIERS["groups"]]
        else:
            rows = [solo_row(r) for r in sq.QUALIFIERS[k]]
        out["divisions"][k] = rows
        out["counts"][k] = len(rows)
    out["divisionLabels"] = dict(DIVISION_LABELS)
    # 공식 발표 명단이라는 것을 응답에서도 분명히 한다 — 프런트 문구가 이 값을 쓴다.
    out["source"] = "CHZZK_OFFICIAL_ANNOUNCEMENT"
    return out


# ── PIKU 사용자 투표 순위 ───────────────────────────────────────────────────
#
# 공개 경로는 **순위와 표시용 최소 정보만** 돌려준다. 우승 비율·승률 숫자는
# `singcup_piku.public_ranking()`이 정렬에만 쓰고 응답에서 버린다.
# 관리 경로(매핑·수집·import)는 OWNER secret을 강제한다.


@router.get("/piku/status")
async def piku_status():
    """공개 상태 — 부문별 마지막 정상 갱신 시각·출처·항목 수."""
    return await piku.public_status()


@router.get("/piku/ranking")
async def piku_ranking(division: str | None = None,
                       sort: str = piku.DEFAULT_SORT, limit: int = 0):
    """공개 순위. `division`을 생략하면 세 부문을 모두 돌려준다.

    **응답에 우승 비율·승률이 숫자로도 이름으로도 없다.** `sort`는 공개 토큰
    (`primary`/`secondary`)이고 내부 컬럼명은 서버 밖으로 나가지 않는다.
    정렬은 **서버가** 하며, 기준이 바뀌면 1위부터 다시 계산된다.
    """
    keys = [division] if division in piku.DIVISIONS else list(piku.DIVISIONS)
    public_sort, _ = piku.resolve_sort(sort)
    out = {"sort": public_sort, "divisions": {}}
    for d in keys:
        out["divisions"][d] = await piku.public_ranking(d, sort=public_sort,
                                                        limit=limit)
    out["sortOptions"] = [{"key": k, "label": piku.SORT_LABELS[k]}
                          for k in piku.PUBLIC_SORTS]
    out["autoCollectEnabled"] = piku.auto_collect_enabled()
    return out


# 관리 경로(매핑·수집·import)는 **`admin_router`에 있다** — Nexadmin의 다른 기능과
# 같은 OWNER JWT를 쓰기 위해서다. 인증 방식이 화면마다 다르면 권한 검사가 갈라진다.


def qualifier_candidates(division: str | None) -> list[dict]:
    keys = [division] if division in DIVISIONS else list(DIVISIONS)
    out: list[dict] = []
    for k in keys:
        if k == "groups":
            for g in sq.QUALIFIERS["groups"]:
                out.extend({"division": k, "channelId": m["channelId"],
                            "name": m["name"], "team": g["teamNumber"]}
                           for m in g["members"])
        else:
            out.extend({"division": k, "channelId": r["channelId"],
                        "name": r["name"]} for r in sq.QUALIFIERS[k])
    return out


async def _qualifier_side_data(ids: set[str]) -> tuple[dict, dict]:
    """참가자 id 집합에 대한 (라이브 정보, 대표 클립 정보).

    **id 집합으로 좁혀서 조회한다.** 전체를 읽고 파이썬에서 거르면 응답이 작아져도
    DB 부하는 그대로다.
    """
    from database import get_db
    if not ids:
        return {}, {}
    db = await get_db()
    marks = ",".join("?" * len(ids))
    id_list = list(ids)

    live: dict[str, dict] = {}
    try:
        ts_row = await (await db.execute(
            "SELECT MAX(collected_at) t FROM rising_collect_runs WHERE ok=1")).fetchone()
        ts = ts_row["t"] if ts_row else None
        if ts is not None:
            for r in await (await db.execute(
                f"""SELECT chzzk_channel_id, channel_name, concurrent_viewers,
                           category_name, live_title
                      FROM rising_live_snapshots
                     WHERE collected_at = ? AND chzzk_channel_id IN ({marks})""",
                    (ts, *id_list))).fetchall():
                live[r["chzzk_channel_id"]] = {
                    "channelName": r["channel_name"],
                    "concurrentViewers": int(r["concurrent_viewers"] or 0),
                    "categoryName": r["category_name"] or "",
                    "liveTitle": r["live_title"] or "",
                }
    except Exception as e:      # noqa: BLE001
        # 라이브 정보는 부가 값이다 — 없으면 배지만 빠지고 명단은 나온다.
        # 다만 **무엇이 실패했는지는 남긴다**(아래 클립 쪽 주석 참고).
        print(f"[singcup] qualifier live join failed: {type(e).__name__}: {e}",
              flush=True)

    clips: dict[str, dict] = {}
    try:
        # `singcup_streamers`의 키는 **`channel_id`**다(`owner_channel_id`는
        # `singcup_clips`/`singcup_snapshots` 쪽 이름). 예전에 이 이름을 잘못 써서
        # SQLite가 `no such column`을 던졌고, 아래 except가 그걸 삼켜 **참가자
        # 전원의 프로필·클립이 조용히 빈 값**이 됐다. 이름을 섞지 말 것.
        #
        # override를 LEFT JOIN으로 먼저 붙이고 `COALESCE`로 우선순위를 준다 —
        # 운영자가 지정한 대표 클립이 자동 선정보다 앞선다. `cleared_at IS NULL`이
        # 활성 조건이다(해제는 행 삭제가 아니라 시각 기록이다).
        # 프로필 fallback(`f`)은 **파생 테이블 한 번**으로 만든다. 참가자마다
        # 따로 조회하면 N+1이 된다. 대표 클립이 없는 참가자도 얼굴은 나와야 하므로
        # 그 채널의 아무 클립에 실려 온 프로필 URL을 예비로 쓴다.
        for r in await (await db.execute(
            f"""SELECT s.channel_id, s.channel_name,
                       COALESCE(NULLIF(s.channel_image_url, ''), f.img, '')
                           AS channel_image_url,
                       c.clip_uid, c.clip_title, c.thumbnail_image_url,
                       o.override_clip_uid IS NOT NULL AS is_override
                  FROM singcup_streamers s
                  LEFT JOIN singcup_representative_overrides o
                    ON o.owner_channel_id = s.channel_id
                   AND o.event_id = s.event_id
                   AND o.cleared_at IS NULL
                  LEFT JOIN singcup_clips c
                    ON c.clip_uid = COALESCE(o.override_clip_uid,
                                             s.representative_clip_uid)
                   AND c.active = 1
                  LEFT JOIN (SELECT owner_channel_id,
                                    MAX(owner_channel_image_url) img
                               FROM singcup_clips
                              WHERE owner_channel_image_url <> ''
                                AND owner_channel_id IN ({marks})
                              GROUP BY owner_channel_id) f
                    ON f.owner_channel_id = s.channel_id
                 WHERE s.channel_id IN ({marks})""",
                (*id_list, *id_list))).fetchall():
            clips[r["channel_id"]] = {
                "channelName": r["channel_name"],
                "channelImageUrl": r["channel_image_url"] or "",
                "clipUid": r["clip_uid"],
                # 컬럼명은 `clip_title`/`thumbnail_image_url`이다. 예전 코드가
                # `title`/`thumbnail_url`로 읽어 이 조인이 통째로 죽어 있었다.
                "clipTitle": r["clip_title"] or "",
                "clipThumbnailUrl": r["thumbnail_image_url"] or "",
                "isOverride": bool(r["is_override"]),
            }
    except Exception as e:      # noqa: BLE001
        # 명단 자체는 정적이라 항상 나와야 하므로 삼키되, **조용히 삼키지는 않는다.**
        # 이 침묵이 위 컬럼명 오류를 오래 숨겼다.
        print(f"[singcup] qualifier clip join failed: {type(e).__name__}: {e}",
              flush=True)

    songs: dict[str, dict] = {}
    try:
        # 곡·가수는 **명시적으로 저장된 값만** 쓴다(클립 제목 파싱 금지 —
        # 운영 데이터의 형식이 제각각이라 순서조차 일정하지 않다).
        # 운영자 입력(`admin`)이 PIKU에서 가져온 값(`piku`)보다 우선한다.
        for r in await (await db.execute(
            f"""SELECT channel_id, song_title, artist_name, source
                  FROM singcup_qualifier_songs
                 WHERE channel_id IN ({marks})
                 ORDER BY CASE source WHEN 'admin' THEN 0 ELSE 1 END""",
                tuple(id_list))).fetchall():
            songs.setdefault(r["channel_id"], {
                "songTitle": r["song_title"] or "",
                "artistName": r["artist_name"] or "",
                "songSource": r["source"] or "",
            })
    except Exception as e:      # noqa: BLE001
        print(f"[singcup] qualifier song join failed: {type(e).__name__}: {e}",
              flush=True)

    return live, clips, songs


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
