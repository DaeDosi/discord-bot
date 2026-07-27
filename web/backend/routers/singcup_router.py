"""싱드컵 이벤트 순위 API.

조회(GET)는 공개다 — /stats와 같은 비로그인 통계 페이지에서 쓴다.
수동 수집(POST)은 외부 API 호출을 유발하므로 secret으로 보호한다.

원본(네이버 라운지) 응답 구조에 의존하는 코드는 전부 singcup_collector 안에 있다.
여기서는 정규화된 dict만 다룬다.
"""
import hmac

from fastapi import APIRouter, Header, HTTPException
from singcup_clips import (
    collect_clips_incremental,
    collect_clips_once,
    load_main,
    load_streamer_clips,
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


@router.post("/clips/collect")
async def clips_collect(mode: str = "incremental", dry_run: bool = False,
                        x_singcup_secret: str | None = Header(default=None)):
    """클립 수동 수집.

    mode=incremental  신규 클립만 카드 조회 + 오래된 수치 일부 갱신(정기 수집과 동일)
    mode=full         태그 후보 전체를 카드 조회(초기 적재/검산용, 호출량이 크다)
    """
    _require_secret(x_singcup_secret)
    if mode not in ("incremental", "full"):
        raise HTTPException(status_code=400, detail="mode는 incremental 또는 full 이어야 합니다.")
    if mode == "full":
        return await collect_clips_once(dry_run=dry_run)
    return await collect_clips_incremental(dry_run=dry_run)
