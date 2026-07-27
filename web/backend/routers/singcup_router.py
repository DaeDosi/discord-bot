"""싱드컵 이벤트 순위 API.

조회(GET)는 공개다 — /stats와 같은 비로그인 통계 페이지에서 쓴다.
수동 수집(POST)은 외부 API 호출을 유발하므로 secret으로 보호한다.

원본(네이버 라운지) 응답 구조에 의존하는 코드는 전부 singcup_collector 안에 있다.
여기서는 정규화된 dict만 다룬다.
"""
import hmac

from fastapi import APIRouter, Header, HTTPException
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
