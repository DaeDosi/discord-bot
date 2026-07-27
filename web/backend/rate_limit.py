"""IP 단위 레이트 리밋 미들웨어.

배경: /api/rising/* 는 인증 없는 공개 API인데 무거운 엔드포인트가 섞여 있다
(newcomers 약 3.9초, streamer/{id}/detail 약 4.4초). 캐시가 있지만 채널 ID를 계속 바꿔
요청하면 캐시를 우회해 매번 무거운 쿼리를 돌릴 수 있어, 소수의 요청으로도 서버를
마비시킬 수 있다. 그래서 '비싼 경로'에 더 촘촘한 제한을 건다.

외부 의존성(slowapi 등)을 추가하지 않고 표준 라이브러리만 쓴다 —
Railway에서 컨테이너 1개로 돌아가므로 프로세스 내 메모리 카운터로 충분하다.
(여러 인스턴스로 늘리면 Redis 같은 공유 저장소가 필요해진다.)
"""
import os
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

WINDOW = 60  # 초 단위 슬라이딩 윈도우

# 일반 요청 한도(분당). 대시보드를 열면 여러 API를 동시에 부르므로 넉넉하게 잡는다.
DEFAULT_LIMIT = int(os.getenv("RATE_LIMIT_DEFAULT", "150"))
# 비싼 경로 한도(분당).
# 20으로 잡았다가 자체 테스트에서 정상 사용자(새로고침 5회 + 전 탭 순회)가 걸렸다.
# 사람이 1분에 낼 수 있는 양보다 넉넉히 두되, 자동화된 반복 호출은 잡히는 선으로 40.
HEAVY_LIMIT = int(os.getenv("RATE_LIMIT_HEAVY", "40"))
# 메모리 상한 — 추적 중인 IP 수가 이보다 많아지면 오래된 것부터 정리한다
MAX_TRACKED_IPS = int(os.getenv("RATE_LIMIT_MAX_IPS", "20000"))

# '비싼 경로'는 추측이 아니라 실측 응답 시간으로 고른다(QA 측정값, 롤업 50만 행 기준).
#   newcomers 3.9s / streamer·detail 4.4s / ranking-period 1.05s
#   category-streamers·search 는 SQL은 가볍지만 치지직 외부 API를 호출한다
# 아래는 모두 0.2초 이하라 제외했다:
#   viewer-distribution 0.20s / tags 0.09s / tag-effect 0.03s / traffic-heatmap 0.01s
#   title-keywords 0.01s / tag-streamers 0.01s
_HEAVY_MARKERS = (
    "/api/rising/newcomers",
    "/api/rising/ranking-period",
    "/api/rising/category-streamers",
    # 태그별 스트리머도 팔로워 온디맨드 보강으로 외부 API를 호출한다
    "/api/rising/tag-streamers",
    "/api/rising/search",
    # 기간별 상세 분석: 롤업 전 구간 스캔 + 체급 판정 서브쿼리라 무겁다
    "/api/rising/period-analysis",
    # 첫 방송일 수집: 무인증 POST인데 캐시 미스 시 치지직 외부 호출을 유발한다
    "/api/chzzk/channel-history",
)

# 방문자 집계는 무인증 POST라 자동화로 부풀리기 쉽다. 하루 단위 중복은 DB의
# PRIMARY KEY(date, ip_hash)가 막지만, 그 앞단에서 요청 자체를 촘촘히 제한한다.
# 정상 사용자는 페이지 진입 시 1회만 호출하므로 분당 5회면 충분하다.
_VISIT_PATH = "/api/stats/visit"
VISIT_LIMIT = int(os.getenv("RATE_LIMIT_VISIT", "5"))


def _is_heavy(path: str) -> bool:
    if path.startswith(_HEAVY_MARKERS):
        return True
    # /api/rising/streamer/{id} 와 그 하위(detail, session)는 전부 무겁다
    return path.startswith("/api/rising/streamer/")


def _client_ip(request: Request) -> str:
    """Railway 등 프록시 뒤에서는 원격 주소가 프록시 IP라 X-Forwarded-For를 우선한다.

    이 헤더는 클라이언트가 위조할 수 있지만, 위조하면 '자기 몫의 카운터'만 흩어질 뿐
    남의 요청을 막지는 못한다. 프록시가 붙인 첫 번째 항목을 쓴다.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        # ip -> (일반 요청 타임스탬프 deque, 비싼 요청 타임스탬프 deque)
        self._hits: dict[str, tuple[deque, deque]] = {}
        self._last_sweep = time.monotonic()

    def _sweep(self, now: float):
        """윈도우가 지난 빈 항목을 정리한다. 요청마다 전체를 훑으면 비싸므로 주기적으로만."""
        if now - self._last_sweep < WINDOW:
            return
        self._last_sweep = now
        cutoff = now - WINDOW
        dead = [ip for ip, (a, b) in self._hits.items()
                if (not a or a[-1] < cutoff) and (not b or b[-1] < cutoff)]
        for ip in dead:
            self._hits.pop(ip, None)
        # 그래도 많으면 강제로 비운다(카운터 손실보다 메모리 폭주가 더 위험하다)
        if len(self._hits) > MAX_TRACKED_IPS:
            self._hits.clear()

    async def dispatch(self, request: Request, call_next):
        # CORS 프리플라이트와 헬스체크는 세지 않는다
        if request.method == "OPTIONS" or request.url.path in ("/", "/health"):
            return await call_next(request)

        now = time.monotonic()
        self._sweep(now)

        ip = _client_ip(request)
        path = request.url.path
        heavy = _is_heavy(path)
        visit = path == _VISIT_PATH
        buckets = self._hits.setdefault(ip, (deque(), deque()))
        # 방문 집계는 '비싼 경로' 버킷을 공유하되 훨씬 낮은 상한을 쓴다
        q = buckets[1] if (heavy or visit) else buckets[0]
        limit = VISIT_LIMIT if visit else (HEAVY_LIMIT if heavy else DEFAULT_LIMIT)

        cutoff = now - WINDOW
        while q and q[0] < cutoff:
            q.popleft()

        if len(q) >= limit:
            retry = max(1, int(WINDOW - (now - q[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
                         "retry_after": retry},
                headers={"Retry-After": str(retry),
                         "X-RateLimit-Limit": str(limit),
                         "X-RateLimit-Remaining": "0"},
            )

        q.append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - len(q)))
        return response
