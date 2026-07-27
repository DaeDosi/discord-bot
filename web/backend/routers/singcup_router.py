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
    discover_new_clips,
    load_main,
    load_streamer_clips,
    refresh_metrics,
    reset_backfill,
    run_backfill,
)
from singcup_collector import (
    ADMIN_SECRET,
    MODES,
    collect_once,
    load_rankings,
    load_status,
    prune_out_of_range,
)

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
