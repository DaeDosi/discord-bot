"""싱드컵 이벤트 순위 API.

조회(GET)는 공개다 — /stats와 같은 비로그인 통계 페이지에서 쓴다.
수동 수집(POST)은 외부 API 호출을 유발하므로 secret으로 보호한다.

원본(네이버 라운지) 응답 구조에 의존하는 코드는 전부 singcup_collector 안에 있다.
여기서는 정규화된 dict만 다룬다.
"""
import hmac

from fastapi import APIRouter, Header, HTTPException
from singcup_collector import ADMIN_SECRET, collect_once, load_rankings, load_status

router = APIRouter(prefix="/api/singcup", tags=["singcup"])


@router.get("/rankings")
async def rankings(limit: int = 200):
    """버프 순 랭킹. 수집이 실패해도 DB의 마지막 정상 데이터를 그대로 돌려준다."""
    return await load_rankings(limit=limit)


@router.get("/status")
async def status():
    """수집기 헬스체크 — Railway에서 네이버 API 접근이 되는지 확인할 때 쓴다."""
    return await load_status()


@router.post("/collect")
async def collect(x_singcup_secret: str | None = Header(default=None)):
    """수동 수집. SINGCUP_ADMIN_SECRET 헤더가 맞아야 실행된다.

    secret이 설정되지 않은 배포에서는 아예 막는다(빈 값과 일치해 열리는 사고 방지).
    """
    if not ADMIN_SECRET:
        raise HTTPException(
            status_code=503,
            detail="SINGCUP_ADMIN_SECRET이 설정되지 않아 수동 수집을 사용할 수 없습니다.")
    if not x_singcup_secret or not _consteq(x_singcup_secret, ADMIN_SECRET):
        raise HTTPException(status_code=401, detail="인증에 실패했습니다.")
    return await collect_once(force=True)


def _consteq(a: str, b: str) -> bool:
    """타이밍 공격 여지를 줄이는 상수시간 비교."""
    return hmac.compare_digest(a.encode(), b.encode())
