"""싱드컵 이벤트 순위 API.

조회(GET)는 공개다 — /stats와 같은 비로그인 통계 페이지에서 쓴다.
수동 수집(POST)은 외부 API 호출을 유발하므로 secret으로 보호한다.

원본(네이버 라운지) 응답 구조에 의존하는 코드는 전부 singcup_collector 안에 있다.
여기서는 정규화된 dict만 다룬다.
"""
import asyncio
import hmac

from fastapi import APIRouter, Header, HTTPException
from singcup_clips import (
    backfill_status,
    clip_diagnosis,
    discover_new_clips,
    load_main,
    load_streamer_clips,
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
@router.get("/main")
async def main(limit: int = 200):
    """#싱드컵 태그 스트리머 목록 — 대표 클립·비공식 예상 인기점수·변화량·현재 라이브."""
    return await load_main(limit=limit)


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


@router.get("/clips/{clip_uid}/diagnose")
async def clips_diagnose(clip_uid: str,
                         x_singcup_secret: str | None = Header(default=None)):
    """클립 1건의 DB 수치·대기열 위치·제외 사유. 값이 안 도는 클립을 지목해 본다."""
    _require_secret(x_singcup_secret)
    return await clip_diagnosis(clip_uid)
