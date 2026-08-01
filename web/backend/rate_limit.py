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
import re
import secrets
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


# ── SSR 메타데이터 ─────────────────────────────────────────────────────────
# `/streamer/{id}/meta`는 롤업 집계 한 번이라 heavy가 아니다. 문제는 비용이 아니라
# **호출자가 전부 하나**라는 점이었다 — Next.js 서버가 모든 방문자·크롤러를 대신해
# 부르므로 IP 버킷 하나에 합쳐져, 크롤러가 페이지를 훑으면 40/분이 즉시 소진됐다.
# 그래서 경로를 heavy에서 빼고, **서버임이 증명된 요청만** 별도 버킷으로 보낸다.
# 경로는 **정확히** 매칭한다. substring 검사면 `/api/rising/metadata/...`나
# 쿼리스트링에 `/meta`가 든 요청까지 SSR 그룹으로 새어 들어간다.
# channel_id는 치지직 채널 해시 형식(영숫자)만 받는다.
_META_RE = re.compile(r"^/api/rising/streamer/[A-Za-z0-9_-]{1,64}/meta$")


def _ssr_limit() -> int:
    """실측 근거: 크롤러 버스트 22요청/6초 ≈ 220/분(관측 최대). 그 위 첫 단계로 240.

    면제가 아니다 — 이 값을 넘으면 SSR도 429를 받는다.
    잘못된 값(0·음수·비정수·과도한 값)은 기본값으로 되돌린다 — 설정 실수 하나로
    제한이 사실상 풀리면 안 된다.
    """
    raw = os.getenv("RATE_LIMIT_SSR", "").strip()
    if not raw:
        return 240
    try:
        v = int(raw)
    except ValueError:
        return 240
    return v if 1 <= v <= 10_000 else 240


SSR_LIMIT = _ssr_limit()
# 서버 증명용 공유 시크릿. **비어 있으면 헤더를 신뢰하지 않는다**(자동 면제 금지).
_SSR_SECRET = os.getenv("SSR_SHARED_SECRET", "").strip()
_SSR_HEADER = "x-internal-ssr"
_ssr_warned = False


def _is_meta(path: str) -> bool:
    return _META_RE.match(path) is not None


def _is_trusted_ssr(request: Request) -> bool:
    """이 요청이 우리 Next.js 서버에서 온 것인가.

    시크릿이 없으면 항상 False다 — 헤더만 보고 통과시키면 아무나 붙일 수 있다.
    비교는 상수시간으로 한다(길이·내용 유출 방지).
    """
    global _ssr_warned
    if not _SSR_SECRET:
        if not _ssr_warned:
            _ssr_warned = True          # 요청마다 반복하지 않는다
            print("[rate_limit] SSR_SHARED_SECRET 미설정 — SSR 요청도 일반 버킷을 씁니다.",
                  flush=True)
        return False
    got = request.headers.get(_SSR_HEADER)
    return bool(got) and secrets.compare_digest(got, _SSR_SECRET)


def _is_heavy(path: str) -> bool:
    if path.startswith(_HEAVY_MARKERS):
        return True
    if _is_meta(path):
        return False                    # 메타는 가볍다 — 아래에서 따로 센다
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

        path = request.url.path
        # SSR 메타 요청은 **서버임이 증명될 때만** 전용 키를 쓴다. 헤더가 없거나
        # 틀리면 평범한 사용자 요청으로 취급한다(자동 면제 없음).
        ssr = _is_meta(path) and _is_trusted_ssr(request)
        ip = "__ssr__" if ssr else _client_ip(request)
        heavy = _is_heavy(path)
        visit = path == _VISIT_PATH
        buckets = self._hits.setdefault(ip, (deque(), deque()))
        # 방문 집계는 '비싼 경로' 버킷을 공유하되 훨씬 낮은 상한을 쓴다
        q = buckets[1] if (heavy or visit or ssr) else buckets[0]
        limit = (SSR_LIMIT if ssr
                 else VISIT_LIMIT if visit
                 else HEAVY_LIMIT if heavy else DEFAULT_LIMIT)

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
