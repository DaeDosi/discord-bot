"""치지직 채널 '첫 방송일 / 누적 방송시간' 수집기.

치지직 **공식 Open API에는 개설일·첫방송일 필드가 없다.** 그래서 이 프로젝트는 지금까지
다시보기(VOD) 목록의 최고령 영상 날짜로 첫 방송을 *추정*해 왔다
(`rising_router._fetch_first_broadcast`).
VOD를 지운 채널은 실제보다 늦게 나오는 한계가 있었다.

여기서는 채널 정보 화면이 실제로 쓰는 **비공식 웹 내부 엔드포인트**를 사용해 치지직이
직접 제공하는 값을 가져온다:

    GET /service/v1/channels/{channelId}/data?fields=channelHistory
    -> content.channelHistory.firstLiveDate  ("YYYY-MM-DD HH:mm:ss", KST)
       content.channelHistory.totalLiveHours (정수 시간)

문서화되지 않은 엔드포인트이므로 예고 없이 형태가 바뀌거나 차단될 수 있다. 그래서
(1) 값은 DB에 캐시해 재호출을 최소화하고, (2) 초당 요청수·동시성을 제한하고,
(3) 스키마가 바뀌면 기존 캐시를 지우지 않고 로그만 남긴다.

차단 회피(프록시/IP 순환/CAPTCHA 우회)는 구현하지 않는다 — 403이 지속되면 수집을 멈추고
운영 로그에 경고를 남기는 것이 이 모듈의 정책이다.
"""
import asyncio
import json
import os
import random
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from database import get_db

CHZZK_API = "https://api.chzzk.naver.com"

# 서비스명·버전을 식별할 수 있는 UA를 쓴다(브라우저 위장을 하지 않는다).
USER_AGENT = os.getenv("CHZZK_USER_AGENT", "NexBot-CHZZKCollector/1.0")
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

REQUESTS_PER_SECOND = float(os.getenv("CHZZK_REQUESTS_PER_SECOND", "2"))
MAX_CONCURRENCY = int(os.getenv("CHZZK_MAX_CONCURRENCY", "3"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("CHZZK_REQUEST_TIMEOUT_SECONDS", "10"))
# '전체 재시도 최대 3회' = 한 요청의 총 시도 횟수 상한(첫 시도 포함).
MAX_RETRIES = max(1, int(os.getenv("CHZZK_MAX_RETRIES", "3")))
NO_HISTORY_TTL_HOURS = float(os.getenv("CHZZK_NO_HISTORY_TTL_HOURS", "24"))
TOTAL_HOURS_TTL_HOURS = float(os.getenv("CHZZK_TOTAL_HOURS_TTL_HOURS", "24"))
# 404/에러를 매 요청마다 다시 두드리지 않게 하는 재시도 간격
NOT_FOUND_TTL_HOURS = float(os.getenv("CHZZK_NOT_FOUND_TTL_HOURS", "24"))
ERROR_RETRY_MINUTES = float(os.getenv("CHZZK_ERROR_RETRY_MINUTES", "10"))
# 403이 연속으로 이만큼 나오면 수집을 일정 시간 멈춘다(차단 우회 대신 후퇴).
BLOCKED_THRESHOLD = int(os.getenv("CHZZK_BLOCKED_THRESHOLD", "3"))
BLOCKED_COOLDOWN_MINUTES = float(os.getenv("CHZZK_BLOCKED_COOLDOWN_MINUTES", "30"))
BACKOFF_BASE_SECONDS = float(os.getenv("CHZZK_BACKOFF_BASE_SECONDS", "0.5"))
BACKOFF_MAX_SECONDS = float(os.getenv("CHZZK_BACKOFF_MAX_SECONDS", "30"))
MAX_BATCH_SIZE = int(os.getenv("CHZZK_MAX_BATCH_SIZE", "100"))

SOURCE = "CHZZK_CHANNEL_HISTORY"

# status 값 — DB와 응답에서 같은 어휘를 쓴다.
ST_OK = "OK"
ST_NO_HISTORY = "NO_HISTORY"      # 방송 기록이 없는 채널(channelHistory=null)
ST_NOT_FOUND = "NOT_FOUND"       # 404
ST_BLOCKED = "BLOCKED"           # 403 — ERROR의 특수한 경우로 따로 기록한다
ST_ERROR = "ERROR"               # 5xx / timeout / 스키마 오류 등
ST_INVALID = "INVALID"           # 채널 ID 형식 자체가 잘못됨(외부 호출 안 함)

_KST = timezone(timedelta(hours=9))

# 치지직 채널 ID = 32자리 16진수. 이 값이 외부 API URL에 그대로 붙으므로
# 형식을 먼저 검증해 경로 조작 여지를 없앤다.
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_FIRST_LIVE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class InvalidChannelError(ValueError):
    """채널 ID/URL 형식이 잘못됨 — 외부 API를 호출하지 않고 400으로 돌려준다."""


class SchemaError(RuntimeError):
    """응답 JSON 구조가 예상과 다름 — 기존 캐시는 유지하고 로그만 남긴다."""


def _log(payload: dict):
    """구조화 로그. 채널 ID·상태·소요시간만 남기고 응답 본문 전체는 남기지 않는다."""
    print(f"[chzzk_history] {json.dumps(payload, ensure_ascii=False, default=str)}", flush=True)


# ── 채널 ID 정규화 ──────────────────────────────────────────────────────────
def normalize_channel_input(raw: str | None) -> str | None:
    """채널 ID 또는 치지직 URL에서 32자리 16진수 채널 ID를 뽑아 소문자로 정규화한다.

    지원 입력: 순수 ID / https://chzzk.naver.com/{id} / .../{id}/about / .../live/{id}
    실패하면 None. 임의의 문자열에서 아무 16진수나 긁어오지 않도록, URL인 경우에는
    호스트가 치지직인지 확인하고 경로 세그먼트가 정확히 32자 16진수인 것만 받는다.
    """
    s = (raw or "").strip()
    if not s:
        return None
    if _HEX32_RE.match(s):
        return s.lower()
    # URL로 보이지 않으면 여기서 끝 — ID 형식이 아니었으므로 무효.
    if "//" not in s and not s.lower().startswith("chzzk.naver.com"):
        return None
    try:
        u = urlparse(s if "//" in s else f"https://{s}")
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host != "chzzk.naver.com" and not host.endswith(".chzzk.naver.com"):
        return None
    for seg in u.path.split("/"):
        if _HEX32_RE.match(seg):
            return seg.lower()
    return None


def first_live_date_to_iso(raw: str | None) -> str | None:
    """"2025-01-14 22:19:58"(KST) -> "2025-01-14T22:19:58+09:00"."""
    if not raw or not _FIRST_LIVE_RE.match(raw):
        return None
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST).isoformat()


def _iso(ts: int | float | None) -> str | None:
    return datetime.fromtimestamp(ts, _KST).isoformat() if ts else None


# ── 지표 ────────────────────────────────────────────────────────────────────
_metrics: dict = {
    "success": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "no_history": 0,
    "not_found": 0,
    "forbidden": 0,
    "rate_limited": 0,
    "errors": 0,
    "schema_errors": 0,
    "retries": 0,
    "stale_served": 0,
    "external_calls": 0,
    "external_ms_total": 0.0,
    "batch_pending": 0,
}


def metrics_snapshot() -> dict:
    m = dict(_metrics)
    calls = m["external_calls"] or 0
    total = m["success"] + m["cache_hits"]
    m["external_avg_ms"] = round(m["external_ms_total"] / calls, 1) if calls else None
    m["cache_hit_rate"] = round(m["cache_hits"] / total, 4) if total else None
    m["blocked_until"] = _iso(_blocked_until[0]) if _blocked_until[0] else None
    return m


# ── 속도 제한 / 동시성 / single-flight ──────────────────────────────────────
class _RateLimiter:
    """전역 초당 요청수 제한. 락을 잡은 채로 sleep 해 호출을 직렬화한다."""

    def __init__(self, rps: float):
        self._interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self):
        if self._interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next = now + self._interval


_limiter: _RateLimiter | None = None
_semaphore: asyncio.Semaphore | None = None
_client: httpx.AsyncClient | None = None
# 같은 채널에 동시 요청이 몰려도 외부 호출은 1회만 — 진행 중 Task를 공유한다.
_inflight: dict[str, asyncio.Task] = {}
# 403 연속 횟수와 차단 쿨다운 종료 시각(epoch). 리스트로 감싼 건 모듈 전역 재바인딩 없이 쓰려고.
_consecutive_403 = [0]
_blocked_until = [0.0]


def _get_limiter() -> _RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = _RateLimiter(REQUESTS_PER_SECOND)
    return _limiter


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, MAX_CONCURRENCY))
    return _semaphore


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_connections=max(1, MAX_CONCURRENCY)),
        )
    return _client


async def reset_state(*, close_client: bool = True):
    """테스트/재초기화용 — 루프에 묶인 객체와 카운터를 비운다."""
    global _limiter, _semaphore, _client
    _limiter = None
    _semaphore = None
    if close_client and _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None
    _inflight.clear()
    _consecutive_403[0] = 0
    _blocked_until[0] = 0.0
    for k in _metrics:
        _metrics[k] = 0 if not isinstance(_metrics[k], float) else 0.0


# ── HTTP ────────────────────────────────────────────────────────────────────
def _retry_delay(attempt: int, retry_after: str | None) -> float:
    """Retry-After가 있으면 그것을 우선 사용하고, 없으면 지수 백오프 + 지터."""
    if retry_after:
        try:
            return min(BACKOFF_MAX_SECONDS, max(0.0, float(retry_after.strip())))
        except ValueError:
            pass  # HTTP-date 형식은 지원하지 않고 백오프로 넘어간다
    base = BACKOFF_BASE_SECONDS * (2 ** attempt)
    return min(BACKOFF_MAX_SECONDS, base) + random.uniform(0, BACKOFF_BASE_SECONDS or 0.1)


async def _get_json(client: httpx.AsyncClient, url: str) -> tuple[int | None, dict | None, int]:
    """(http_status, json, 재시도횟수)를 돌려준다. status=None 이면 timeout/전송 실패.

    429·5xx·timeout만 재시도한다. 400/401/403/404는 재시도해도 결과가 같으므로 즉시 반환.
    """
    retries = 0
    for attempt in range(MAX_RETRIES):
        await _get_limiter().acquire()
        t0 = time.monotonic()
        try:
            r = await client.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        except (httpx.TimeoutException, httpx.TransportError):
            _metrics["external_calls"] += 1
            _metrics["external_ms_total"] += (time.monotonic() - t0) * 1000
            if attempt + 1 >= MAX_RETRIES:
                return (None, None, retries)
            retries += 1
            _metrics["retries"] += 1
            await asyncio.sleep(_retry_delay(attempt, None))
            continue

        _metrics["external_calls"] += 1
        _metrics["external_ms_total"] += (time.monotonic() - t0) * 1000
        status = r.status_code

        if status == 200:
            try:
                return (200, r.json(), retries)
            except (json.JSONDecodeError, ValueError):
                raise SchemaError("응답이 JSON이 아님")

        if status == 429 or 500 <= status < 600:
            if status == 429:
                _metrics["rate_limited"] += 1
            if attempt + 1 >= MAX_RETRIES:
                return (status, None, retries)
            retries += 1
            _metrics["retries"] += 1
            await asyncio.sleep(_retry_delay(attempt, r.headers.get("Retry-After")))
            continue

        return (status, None, retries)

    return (None, None, retries)


def parse_channel_history(payload: dict | None) -> tuple[str, str | None, int | None]:
    """(status, firstLiveDate, totalLiveHours)로 검증·파싱한다.

    구조가 예상과 다르면 SchemaError — 호출부에서 기존 캐시를 유지한 채 로그만 남긴다.
    """
    if not isinstance(payload, dict):
        raise SchemaError("최상위가 객체가 아님")
    if payload.get("code") != 200:
        raise SchemaError(f"code={payload.get('code')!r}")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise SchemaError("content가 객체가 아님")

    hist = content.get("channelHistory")
    if hist is None:
        return (ST_NO_HISTORY, None, None)
    if not isinstance(hist, dict):
        raise SchemaError("channelHistory가 객체/null이 아님")

    raw_date = hist.get("firstLiveDate")
    if raw_date is None:
        # 키가 비어 있으면 스키마 오류가 아니라 '기록 없음'으로 본다.
        return (ST_NO_HISTORY, None, None)
    if not isinstance(raw_date, str) or not _FIRST_LIVE_RE.match(raw_date.strip()):
        raise SchemaError("firstLiveDate 형식이 예상과 다름")

    hours = hist.get("totalLiveHours")
    if hours is not None and (isinstance(hours, bool) or not isinstance(hours, (int, float))):
        raise SchemaError("totalLiveHours가 숫자/null이 아님")

    return (ST_OK, raw_date.strip(), int(hours) if hours is not None else None)


async def _fetch_channel_name(client: httpx.AsyncClient, cid: str) -> str | None:
    """채널명이 필요할 때만 부르는 두 번째 요청. 실패는 치명적이지 않다."""
    try:
        status, payload, _ = await _get_json(client, f"{CHZZK_API}/service/v1/channels/{cid}")
    except SchemaError:
        return None
    if status != 200 or not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, dict):
        return None
    name = content.get("channelName")
    return str(name) if name else None


# ── DB ──────────────────────────────────────────────────────────────────────
async def _load_row(cid: str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM chzzk_channel_history WHERE channel_id=?", (cid,)
    )).fetchone()
    return dict(row) if row else None


async def _save_success(cid: str, status: str, first_live: str | None,
                        hours: int | None, name: str | None) -> dict:
    """정상 수집 결과를 upsert 한다.

    channel_name/total_live_hours는 이번에 못 가져왔으면(None) 기존 값을 유지한다 —
    이름 조회를 생략한 요청이 이미 저장된 이름을 지워버리면 안 된다.
    """
    now = int(time.time())
    db = await get_db()
    await db.execute(
        """INSERT INTO chzzk_channel_history
               (channel_id, channel_name, first_live_date, first_live_date_iso,
                total_live_hours, source, status, collected_at, total_hours_updated_at,
                last_error, last_attempt_at, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,?)
           ON CONFLICT(channel_id) DO UPDATE SET
               channel_name           = COALESCE(excluded.channel_name, channel_name),
               first_live_date        = COALESCE(excluded.first_live_date, first_live_date),
               first_live_date_iso    = COALESCE(excluded.first_live_date_iso, first_live_date_iso),
               total_live_hours       = COALESCE(excluded.total_live_hours, total_live_hours),
               total_hours_updated_at = COALESCE(excluded.total_hours_updated_at,
                                                 total_hours_updated_at),
               source                 = excluded.source,
               status                 = excluded.status,
               collected_at           = excluded.collected_at,
               last_error             = NULL,
               last_attempt_at        = excluded.last_attempt_at,
               updated_at             = excluded.updated_at""",
        (cid, name, first_live, first_live_date_to_iso(first_live), hours, SOURCE, status,
         now, now if hours is not None else None, now, now, now),
    )
    await db.commit()
    return await _load_row(cid) or {}


async def _save_failure(cid: str, status: str, err: str) -> dict | None:
    """실패를 기록한다. **정상 firstLiveDate가 이미 있으면 값과 status(OK)를 보존**하고
    last_error/last_attempt_at만 갱신한다(외부 장애로 캐시를 잃지 않게)."""
    now = int(time.time())
    db = await get_db()
    row = await _load_row(cid)
    if row and row.get("first_live_date"):
        await db.execute(
            "UPDATE chzzk_channel_history SET last_error=?, last_attempt_at=?, updated_at=? "
            "WHERE channel_id=?", (err[:500], now, now, cid),
        )
    elif row:
        await db.execute(
            "UPDATE chzzk_channel_history SET status=?, last_error=?, last_attempt_at=?, "
            "updated_at=? WHERE channel_id=?", (status, err[:500], now, now, cid),
        )
    else:
        await db.execute(
            """INSERT INTO chzzk_channel_history
                   (channel_id, source, status, last_error, last_attempt_at, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (cid, SOURCE, status, err[:500], now, now, now),
        )
    await db.commit()
    return await _load_row(cid)


# ── 캐시 정책 ───────────────────────────────────────────────────────────────
def _needs_fetch(row: dict | None, *, refresh: bool, refresh_stale_total: bool) -> bool:
    if refresh:
        return True
    if row is None:
        return True
    now = time.time()
    last_try = row.get("last_attempt_at") or 0
    status = row.get("status")

    if row.get("first_live_date"):
        # 첫 방송일은 변하지 않으므로 기본적으로 즉시 반환. 누적 방송시간만 하루 1회 갱신.
        if not refresh_stale_total:
            return False
        th_at = row.get("total_hours_updated_at") or 0
        return now - th_at >= TOTAL_HOURS_TTL_HOURS * 3600

    if status == ST_NO_HISTORY:
        return now - last_try >= NO_HISTORY_TTL_HOURS * 3600
    if status == ST_NOT_FOUND:
        return now - last_try >= NOT_FOUND_TTL_HOURS * 3600
    if status == ST_BLOCKED:
        return now - last_try >= BLOCKED_COOLDOWN_MINUTES * 60
    return now - last_try >= ERROR_RETRY_MINUTES * 60


def _response(cid: str, row: dict | None, *, cached: bool,
              status: str | None = None, stale: bool = False) -> dict:
    row = row or {}
    return {
        "channelId": cid,
        "channelName": row.get("channel_name"),
        "firstLiveDate": row.get("first_live_date"),
        "firstLiveDateIso": row.get("first_live_date_iso"),
        "totalLiveHours": row.get("total_live_hours"),
        "source": row.get("source") or SOURCE,
        "cached": cached,
        "collectedAt": _iso(row.get("collected_at")),
        "status": status or row.get("status") or ST_ERROR,
        # 외부 장애로 오래된 캐시를 돌려준 경우 표시(호출자가 신선도를 판단할 수 있게)
        "stale": stale,
    }


async def _collect(cid: str, *, name_hint: str | None, job_id: str) -> dict:
    """실제 외부 호출 1회분. single-flight Task 본체."""
    prev = await _load_row(cid)
    if _blocked_until[0] and time.time() < _blocked_until[0]:
        # 차단 쿨다운 중 — 외부를 두드리지 않고 캐시(있으면)로 답한다.
        if prev and prev.get("first_live_date"):
            _metrics["stale_served"] += 1
            return _response(cid, prev, cached=True, stale=True)
        return _response(cid, prev, cached=bool(prev), status=ST_BLOCKED)

    client = _get_client()
    url = f"{CHZZK_API}/service/v1/channels/{cid}/data?fields=channelHistory"
    t0 = time.monotonic()
    http_status: int | None = None
    retries = 0
    err_kind = None

    try:
        async with _get_semaphore():
            http_status, payload, retries = await _get_json(client, url)
            if http_status == 200:
                status, first_live, hours = parse_channel_history(payload)
                _consecutive_403[0] = 0
                name = name_hint or (prev or {}).get("channel_name")
                if not name and status in (ST_OK, ST_NO_HISTORY):
                    # 이름이 어디에도 없을 때만 두 번째 요청 — 보통 채널당 1회로 끝난다.
                    name = await _fetch_channel_name(client, cid)
                row = await _save_success(cid, status, first_live, hours, name)
                _metrics["success" if status == ST_OK else "no_history"] += 1
                return _response(cid, row, cached=False, status=status)

            if http_status == 404:
                _consecutive_403[0] = 0
                _metrics["not_found"] += 1
                err_kind = "not_found"
                row = await _save_failure(cid, ST_NOT_FOUND, "HTTP 404")
                if row and row.get("first_live_date"):
                    # 예전엔 있던 채널이 사라진 경우 — 캐시는 남기고 상태만 알린다.
                    return _response(cid, row, cached=True, status=ST_NOT_FOUND, stale=True)
                return _response(cid, row, cached=False, status=ST_NOT_FOUND)

            if http_status == 403:
                _metrics["forbidden"] += 1
                err_kind = "forbidden"
                _consecutive_403[0] += 1
                if _consecutive_403[0] >= BLOCKED_THRESHOLD:
                    _blocked_until[0] = time.time() + BLOCKED_COOLDOWN_MINUTES * 60
                    _log({"event": "blocked", "level": "warning", "job_id": job_id,
                          "consecutive_403": _consecutive_403[0],
                          "cooldown_until": _iso(_blocked_until[0]),
                          "note": "치지직이 요청을 거부합니다. 수집을 일시 중단합니다."})
                row = await _save_failure(cid, ST_BLOCKED, "HTTP 403")
                if row and row.get("first_live_date"):
                    _metrics["stale_served"] += 1
                    return _response(cid, row, cached=True, stale=True)
                return _response(cid, row, cached=False, status=ST_BLOCKED)

            # 5xx / timeout(None) / 그 외 — 캐시가 있으면 stale로 돌려준다.
            _metrics["errors"] += 1
            err_kind = "timeout" if http_status is None else f"http_{http_status}"
            detail = f"HTTP {http_status}" if http_status else "timeout"
            row = await _save_failure(cid, ST_ERROR, detail)
            if row and row.get("first_live_date"):
                _metrics["stale_served"] += 1
                return _response(cid, row, cached=True, stale=True)
            return _response(cid, row, cached=False, status=ST_ERROR)

    except SchemaError as e:
        # 스키마가 바뀐 경우 — 기존 데이터를 건드리지 않는다.
        _metrics["schema_errors"] += 1
        _metrics["errors"] += 1
        err_kind = "schema"
        _log({"event": "schema_error", "level": "warning", "job_id": job_id,
              "channel_id": cid, "detail": str(e)})
        row = await _save_failure(cid, ST_ERROR, f"schema: {e}")
        if row and row.get("first_live_date"):
            _metrics["stale_served"] += 1
            return _response(cid, row, cached=True, stale=True)
        return _response(cid, row, cached=False, status=ST_ERROR)
    finally:
        _log({"event": "fetch", "job_id": job_id, "channel_id": cid, "cache_hit": False,
              "http_status": http_status, "duration_ms": round((time.monotonic() - t0) * 1000, 1),
              "retries": retries, "error_kind": err_kind})


async def get_channel_history(channel: str, *, refresh: bool = False,
                              channel_name: str | None = None,
                              refresh_stale_total: bool = True,
                              job_id: str | None = None) -> dict:
    """단일 채널의 첫 방송일을 돌려준다(캐시 우선).

    channel: 채널 ID 또는 치지직 URL.
    channel_name: 이미 알고 있는 채널명 — 넘기면 이름 조회 요청을 생략해 채널당 1회로 줄인다.
    refresh_stale_total: False면 누적 방송시간이 오래됐어도 재조회하지 않는다
        (공개 페이지처럼 외부 호출 지연을 감당하기 싫은 경로용).
    """
    cid = normalize_channel_input(channel)
    if cid is None:
        raise InvalidChannelError(f"유효한 치지직 채널 ID/URL이 아닙니다: {channel!r}")

    job_id = job_id or uuid.uuid4().hex[:8]
    row = await _load_row(cid)
    if not _needs_fetch(row, refresh=refresh, refresh_stale_total=refresh_stale_total):
        _metrics["cache_hits"] += 1
        _log({"event": "fetch", "job_id": job_id, "channel_id": cid, "cache_hit": True,
              "http_status": None, "duration_ms": 0, "retries": 0,
              "final_status": row.get("status") if row else None})
        return _response(cid, row, cached=True)

    _metrics["cache_misses"] += 1
    task = _inflight.get(cid)
    if task is None:
        task = asyncio.create_task(_collect(cid, name_hint=channel_name, job_id=job_id))
        _inflight[cid] = task
        task.add_done_callback(lambda _t, _cid=cid: _inflight.pop(_cid, None))
    # shield: 이 요청이 취소돼도 공유 Task(다른 대기자가 있을 수 있다)는 살려 둔다.
    return await asyncio.shield(task)


async def collect_batch(channels: list[str], *, refresh: bool = False,
                        job_id: str | None = None) -> dict:
    """여러 채널을 중복 제거 후 '동시성 제한 + 초당 요청수 제한'으로 수집한다.

    HTTP 요청 처리 중에 무제한 병렬로 터뜨리지 않는다 — 세마포어(MAX_CONCURRENCY)와
    전역 RateLimiter(REQUESTS_PER_SECOND)를 통과해야 외부 호출이 나간다.
    """
    job_id = job_id or uuid.uuid4().hex[:8]
    seen: set[str] = set()
    order: list[tuple[str, str]] = []   # (원본 입력, 정규화된 cid)
    invalid: list[str] = []
    for raw in channels:
        cid = normalize_channel_input(raw)
        if cid is None:
            invalid.append(raw)
            continue
        if cid in seen:
            continue
        seen.add(cid)
        order.append((raw, cid))

    _metrics["batch_pending"] += len(order)
    t0 = time.monotonic()

    async def one(cid: str) -> dict:
        try:
            return await get_channel_history(cid, refresh=refresh, job_id=job_id)
        except Exception as e:      # 한 채널의 실패가 배치 전체를 죽이지 않게
            _metrics["errors"] += 1
            return _response(cid, await _load_row(cid), cached=False, status=ST_ERROR) | {
                "error": str(e)[:200]
            }
        finally:
            _metrics["batch_pending"] = max(0, _metrics["batch_pending"] - 1)

    results = await asyncio.gather(*[one(cid) for _, cid in order]) if order else []

    out = list(results) + [
        {"channelId": None, "input": raw, "status": ST_INVALID, "cached": False,
         "firstLiveDate": None, "firstLiveDateIso": None, "totalLiveHours": None,
         "channelName": None, "source": SOURCE, "collectedAt": None, "stale": False}
        for raw in invalid
    ]
    _log({"event": "batch", "job_id": job_id, "requested": len(channels),
          "unique": len(order), "invalid": len(invalid),
          "duration_ms": round((time.monotonic() - t0) * 1000, 1)})
    return {"jobId": job_id, "requested": len(channels), "unique": len(order),
            "invalid": invalid, "results": out}


# ── 백필 루프 ───────────────────────────────────────────────────────────────
# '신규 & 초기 분석' 탭의 60일 필터는 채널마다 first_live_date가 있어야 성립한다.
# 요청이 들어올 때 그때그때 모으면 첫 방문자가 수백 번의 외부 호출을 기다려야 하므로,
# 백그라운드에서 '아직 수집 안 된 채널'을 조금씩 채운다. 첫 방송일은 변하지 않으므로
# 채널당 딱 한 번만 성공하면 끝이고, 그 뒤로는 이 루프가 그 채널을 다시 건드리지 않는다.
BACKFILL_ENABLED = os.getenv("CHZZK_HISTORY_BACKFILL", "1") != "0"
BACKFILL_INTERVAL_SECONDS = int(os.getenv("CHZZK_HISTORY_BACKFILL_INTERVAL", "300"))
BACKFILL_BATCH = int(os.getenv("CHZZK_HISTORY_BACKFILL_BATCH", "60"))
# 첫 사이클까지의 유예 — 부팅 직후엔 수집기/DB 초기화가 먼저 끝나야 한다
BACKFILL_START_DELAY_SECONDS = int(os.getenv("CHZZK_HISTORY_BACKFILL_DELAY", "90"))


async def _backfill_candidates(limit: int) -> list[tuple[str, str]]:
    """아직 first_live_date가 없는 채널을 (id, name)으로 돌려준다.

    최근에 본 채널부터 채운다 — 지금 방송 중인 채널이 대시보드에 먼저 필요하다.
    이미 성공했거나(=first_live_date 있음) 아직 재시도 시각이 안 된 실패 건은 제외한다.
    """
    now = int(time.time())
    db = await get_db()
    rows = await (await db.execute(
        """SELECT s.chzzk_channel_id AS cid, s.channel_name AS name
           FROM rising_channel_stats s
           LEFT JOIN chzzk_channel_history h ON h.channel_id = s.chzzk_channel_id
           WHERE h.channel_id IS NULL
              OR (h.first_live_date IS NULL AND (
                    (h.status = ?  AND ? - COALESCE(h.last_attempt_at,0) >= ?) OR
                    (h.status = ?  AND ? - COALESCE(h.last_attempt_at,0) >= ?) OR
                    (h.status NOT IN (?,?) AND ? - COALESCE(h.last_attempt_at,0) >= ?)
                 ))
           ORDER BY s.last_seen DESC
           LIMIT ?""",
        (ST_NO_HISTORY, now, int(NO_HISTORY_TTL_HOURS * 3600),
         ST_NOT_FOUND,  now, int(NOT_FOUND_TTL_HOURS * 3600),
         ST_NO_HISTORY, ST_NOT_FOUND, now, int(ERROR_RETRY_MINUTES * 60),
         max(1, limit)),
    )).fetchall()
    return [(r["cid"], r["name"] or "") for r in rows]


async def _backfill_once() -> dict:
    """한 사이클. 속도 제한·동시성은 get_channel_history 안에서 그대로 적용된다."""
    if _blocked_until[0] and time.time() < _blocked_until[0]:
        return {"skipped": "blocked"}
    todo = await _backfill_candidates(BACKFILL_BATCH)
    if not todo:
        return {"picked": 0}

    job_id = uuid.uuid4().hex[:8]
    t0 = time.monotonic()
    ok = 0

    async def one(cid: str, name: str):
        nonlocal ok
        try:
            # 채널명을 넘겨 이름 조회 요청을 생략한다 → 채널당 외부 요청 1회
            res = await get_channel_history(cid, channel_name=name or None, job_id=job_id)
            if res.get("firstLiveDate"):
                ok += 1
        except Exception:
            pass

    await asyncio.gather(*[one(c, n) for c, n in todo])
    out = {"job_id": job_id, "picked": len(todo), "with_date": ok,
           "duration_ms": round((time.monotonic() - t0) * 1000)}
    _log({"event": "backfill", **out})
    return out


async def start_history_backfill():
    """main.py의 lifespan에서 create_task로 띄운다. 실패해도 프로세스를 죽이지 않는다."""
    if not BACKFILL_ENABLED:
        _log({"event": "backfill_disabled"})
        return
    await asyncio.sleep(BACKFILL_START_DELAY_SECONDS)
    while True:
        try:
            await _backfill_once()
        except Exception as e:
            _log({"event": "backfill_error", "level": "warning", "detail": str(e)[:200]})
        await asyncio.sleep(BACKFILL_INTERVAL_SECONDS)
