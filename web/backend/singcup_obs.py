"""`/api/singcup/main` 호출 관측 — 비용 대응의 '기준선'과 '전후 비교'를 위한 계측.

왜 별도 모듈인가: `timing.py`는 경로별 응답시간만 본다. 이번 문제(전송량 폭주)는
**호출 횟수 × 응답 bytes**가 본질이라, 캐시 적중 여부·304 비율·화면 구분·동시
진행 요청 수까지 같이 봐야 원인과 효과를 말할 수 있다.

개인정보: 원문 IP도 UA 전문도 저장하지 않는다. IP는 날짜별로 회전하는 해시
(`client_ip.hash_ip`), UA는 브라우저 종류 문자열 하나로 줄여 넣는다. 저장 기간도
메모리 안 최근 `WINDOW_MINUTES`분뿐이고 디스크에 쓰지 않는다 — 프로세스가 죽으면
같이 사라진다. '지금 이 폭주가 몇 명에서 나오는가'를 답하는 데 그 이상은 필요 없다.
"""
import os
import time
from collections import defaultdict, deque

from client_ip import browser_family, resolve

# 메모리에 유지할 분 단위 버킷 수. 30분 관찰을 두 번 비교할 수 있으면 충분하다.
WINDOW_MINUTES = int(os.getenv("SINGCUP_OBS_WINDOW_MINUTES", "180"))
# 분당 응답시간 표본 상한 — 폭주 중에도 메모리가 늘지 않게 자른다.
_MAX_SAMPLES = 300
# 분당 유니크 클라이언트 해시 상한 — 같은 이유.
_MAX_CLIENTS = 2000

_ENABLED = os.getenv("SINGCUP_OBS_ENABLED", "true").lower() not in ("0", "false", "no")

_buckets: dict[int, dict] = {}
_inflight = 0
_inflight_peak = 0


def _new_bucket() -> dict:
    return {
        "count": 0,
        "status": defaultdict(int),
        "cache": defaultdict(int),
        "screen": defaultdict(int),
        "browser": defaultdict(int),
        "ipSource": defaultdict(int),
        "limit": defaultdict(int),
        "bytes": 0,
        "bytesSaved": 0,      # 304로 아낀 양(보냈다면 나갔을 bytes)
        "ms": deque(maxlen=_MAX_SAMPLES),
        "msMax": 0.0,
        "clients": set(),
        "clientsTruncated": False,
        "inflightPeak": 0,
        "privatePeer": 0,
    }


def _bucket(now: float | None = None) -> dict:
    minute = int((now or time.time()) // 60)
    b = _buckets.get(minute)
    if b is None:
        b = _buckets[minute] = _new_bucket()
        # 오래된 분은 여기서만 정리한다(요청마다 전체를 훑지 않는다)
        cutoff = minute - WINDOW_MINUTES
        for m in [m for m in _buckets if m < cutoff]:
            _buckets.pop(m, None)
    return b


def begin() -> float:
    """요청 시작 — 동시 진행 수를 센다. 반환값은 종료 시각 계산용 기준."""
    global _inflight, _inflight_peak
    _inflight += 1
    _inflight_peak = max(_inflight_peak, _inflight)
    return time.perf_counter()


def end() -> None:
    global _inflight
    _inflight = max(0, _inflight - 1)


def inflight() -> int:
    return _inflight


def record(request, *, status: int, bytes_out: int, ms: float,
           cache: str, limit: int, full_bytes: int = 0) -> None:
    """요청 1건을 기록한다. 관측이 요청을 깨뜨리면 안 되므로 절대 예외를 내지 않는다."""
    if not _ENABLED:
        return
    try:
        b = _bucket()
        b["count"] += 1
        b["status"][str(status)] += 1
        b["cache"][cache] += 1
        b["limit"][str(limit)] += 1
        b["bytes"] += bytes_out
        if status == 304:
            b["bytesSaved"] += full_bytes
        b["ms"].append(ms)
        b["msMax"] = max(b["msMax"], ms)
        b["inflightPeak"] = max(b["inflightPeak"], _inflight)
        b["screen"][_screen(request.headers.get("referer"))] += 1
        b["browser"][browser_family(request.headers.get("user-agent", ""))] += 1

        who = resolve(request)
        b["ipSource"][f'{who["source"]}:{who["xffHops"]}'] += 1
        if who["peerIsPrivate"]:
            b["privatePeer"] += 1
        if len(b["clients"]) < _MAX_CLIENTS:
            b["clients"].add(who["id"])
        else:
            b["clientsTruncated"] = True
    except Exception:      # noqa: BLE001 — 계측 실패가 API를 죽이면 안 된다
        pass


def _screen(referer: str | None) -> str:
    """어느 화면에서 온 요청인지 — Referer만 본다.

    쿼리 파라미터나 커스텀 헤더로 화면을 붙이지 않은 이유: 파라미터는 URL을 갈라
    브라우저 캐시를 조각내고, 커스텀 헤더는 매 요청마다 CORS 프리플라이트(OPTIONS)를
    한 번 더 발생시킨다 — 요청 수를 줄이려는 작업에서 요청을 늘리게 된다.
    """
    if not referer:
        return "none"
    try:
        path = referer.split("://", 1)[-1].split("/", 1)
        path = "/" + path[1] if len(path) > 1 else "/"
    except Exception:      # noqa: BLE001
        return "other"
    if path.startswith("/stats/singcup/live"):
        return "live"
    if path.startswith("/stats"):
        return "stats"
    return "other"


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * p))], 1)


def snapshot(minutes: int = 30) -> dict:
    """최근 N분 요약 + 분 단위 시계열. 배포 전후 표를 이 응답 하나로 만든다."""
    minutes = max(1, min(minutes, WINDOW_MINUTES))
    now_min = int(time.time() // 60)
    keys = sorted(k for k in _buckets if k > now_min - minutes)

    rows, all_ms = [], []
    total = {"count": 0, "bytes": 0, "bytesSaved": 0}
    agg = {k: defaultdict(int) for k in
           ("status", "cache", "screen", "browser", "ipSource", "limit")}
    clients: set[str] = set()

    for k in keys:
        b = _buckets[k]
        ms = list(b["ms"])
        all_ms.extend(ms)
        total["count"] += b["count"]
        total["bytes"] += b["bytes"]
        total["bytesSaved"] += b["bytesSaved"]
        for name in agg:
            for kk, vv in b[name].items():
                agg[name][kk] += vv
        clients |= b["clients"]
        rows.append({
            "minute": time.strftime("%H:%M", time.localtime(k * 60)),
            "count": b["count"],
            "bytes": b["bytes"],
            "clients": len(b["clients"]),
            "p95": _pct(ms, 0.95),
            "inflightPeak": b["inflightPeak"],
        })

    span = max(1, len(keys))
    return {
        "windowMinutes": minutes,
        "minutesObserved": len(keys),
        "perMinute": {
            "requests": round(total["count"] / span, 1),
            "bytes": round(total["bytes"] / span),
            "megabytes": round(total["bytes"] / span / 1_048_576, 3),
        },
        "totals": {
            **total,
            "uniqueClients": len(clients),
            "megabytes": round(total["bytes"] / 1_048_576, 2),
            "megabytesSavedBy304": round(total["bytesSaved"] / 1_048_576, 2),
        },
        "latencyMs": {
            "p50": _pct(all_ms, 0.50), "p95": _pct(all_ms, 0.95),
            "p99": _pct(all_ms, 0.99), "max": round(max(all_ms), 1) if all_ms else 0.0,
            "samples": len(all_ms),
        },
        "status": dict(agg["status"]),
        "cache": dict(agg["cache"]),
        "screen": dict(agg["screen"]),
        "browser": dict(agg["browser"]),
        # 'xff_first:1'처럼 (판정 근거:홉 수)로 남긴다. peer만 잡히면 프록시 헤더가
        # 아예 안 오고 있다는 뜻이고, 그때는 사용자 구분이 불가능하다.
        "ipSource": dict(agg["ipSource"]),
        "limit": dict(agg["limit"]),
        "inflightNow": _inflight,
        "inflightPeak": _inflight_peak,
        "series": rows,
    }


def reset() -> None:
    global _inflight_peak
    _buckets.clear()
    _inflight_peak = 0
