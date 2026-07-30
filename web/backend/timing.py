"""요청별 소요 시간 계측 — Server-Timing 헤더 + 느린 요청 로그.

'느리다'를 감으로 고치지 않기 위한 최소 관측 장치다. 구간을 나눠 재려면 핸들러 안에서
`track(request, "db", ms)`를 부르면 되고, 부르지 않아도 total은 항상 기록된다.

응답 헤더에는 SQL이나 내부 경로 같은 민감 정보를 넣지 않는다(구간 이름과 ms만).
"""
import os
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware

# 이보다 오래 걸린 요청만 로그로 남긴다(정상 요청까지 남기면 로그가 소음이 된다)
SLOW_MS = float(os.getenv("SLOW_REQUEST_MS", "1000"))
# Server-Timing 노출 여부. 기본은 켠다 — 값 자체는 민감하지 않고 진단에 크게 도움이 된다.
EXPOSE = os.getenv("SERVER_TIMING", "1").lower() not in ("0", "false", "no")

# 최근 요청 시간 표본(경로별). P50/P95/P99 산출용 — 외부 APM 없이 자체 확인하려고 둔다.
_MAX_SAMPLES = 500
_samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_SAMPLES))
_counts: dict[str, int] = defaultdict(int)
_errors: dict[str, int] = defaultdict(int)


def track(request, name: str, ms: float):
    """핸들러 안에서 구간 시간을 기록한다(예: DB 쿼리)."""
    marks = getattr(request.state, "_timing", None)
    if marks is None:
        marks = {}
        request.state._timing = marks
    marks[name] = marks.get(name, 0.0) + ms


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * p))], 1)


def stats_snapshot(limit: int = 25) -> dict:
    """경로별 P50/P95/P99 요약. /api/internal/timing 에서 노출한다."""
    rows = []
    for path, vals in _samples.items():
        v = list(vals)
        if not v:
            continue
        rows.append({
            "path": path, "count": _counts[path], "errors": _errors[path],
            "p50": _pct(v, 0.50), "p95": _pct(v, 0.95), "p99": _pct(v, 0.99),
            "max": round(max(v), 1), "samples": len(v),
        })
    rows.sort(key=lambda r: r["p95"], reverse=True)
    return {"slowMs": SLOW_MS, "paths": rows[:limit]}


def reset_stats():
    _samples.clear()
    _counts.clear()
    _errors.clear()


class ServerTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        t0 = time.perf_counter()
        # 요청 ID — 느린 요청 로그와 사용자가 본 응답을 이어 붙일 수 있게 한다.
        # 클라이언트가 보낸 값이 있으면 그대로 쓰되 길이를 자른다(로그 오염 방지).
        rid = (request.headers.get("x-request-id") or uuid.uuid4().hex[:12])[:36]
        request.state.request_id = rid
        try:
            response = await call_next(request)
        except Exception:
            _errors[_key(request)] += 1
            raise
        response.headers["X-Request-Id"] = rid
        total = (time.perf_counter() - t0) * 1000

        key = _key(request)
        _samples[key].append(total)
        _counts[key] += 1
        if response.status_code >= 500:
            _errors[key] += 1

        marks = getattr(request.state, "_timing", None) or {}
        if EXPOSE:
            parts = [f"{n};dur={round(v, 1)}" for n, v in marks.items()]
            parts.append(f"total;dur={round(total, 1)}")
            response.headers["Server-Timing"] = ", ".join(parts)

        if total >= SLOW_MS:
            detail = " ".join(f"{n}={round(v, 1)}ms" for n, v in marks.items())
            print(f"[slow] {request.method} {key} {round(total)}ms rid={rid} "
                  f"{detail}".strip(), flush=True)
        return response


def _key(request) -> str:
    """경로 파라미터가 섞여 표본이 흩어지지 않게 라우트 템플릿을 쓴다."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path
