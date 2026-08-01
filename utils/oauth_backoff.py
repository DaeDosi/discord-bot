"""치지직 OAuth 토큰 갱신 실패의 분류와 재시도 정책.

왜 따로 있나 — 실측(2026-07-31): 한 guild의 refresh token이 무효(INVALID_TOKEN)가
되자 `sync_loop`가 **60초마다 영원히** 같은 요청을 보내고 같은 경고를 남겼다
(24분간 25회). 영구 오류와 일시 오류에 같은 재시도 정책을 쓰면 이렇게 된다.

  영구 오류(refresh token이 폐기·만료됨, 사용자가 권한 취소)
      → 다시 시도해도 결과가 같다. 몇 번 확인한 뒤 **자동 갱신을 멈추고**
        사용자에게 재연동을 요구한다(reauth_required).
  일시 오류(429 / 5xx / timeout / 네트워크)
      → 잠시 뒤면 될 수 있다. 지수 백오프로 계속 시도하되 상태는 바꾸지 않는다.
        여기서 reauth_required로 넘어가면 멀쩡한 연동이 끊긴다.

이 파일에는 DB도 네트워크도 없다 — 정책만 담아 그대로 테스트한다.
토큰·응답 본문은 **절대** 다루지 않는다. 밖으로 나가는 것은 짧은 코드뿐이다.
"""
from __future__ import annotations

import random
import re
from typing import Callable

# ── 상태 ───────────────────────────────────────────────────────────────────
STATE_OK = "ok"
STATE_RETRYING = "retrying"
STATE_REAUTH = "reauth_required"

KIND_PERMANENT = "permanent"
KIND_TRANSIENT = "transient"

# 영구 오류로 볼 코드. 치지직/OAuth2 양쪽 표기를 모두 받는다.
PERMANENT_CODES = frozenset({
    "INVALID_TOKEN", "INVALID_GRANT", "INVALID_REFRESH_TOKEN", "EXPIRED_TOKEN",
    "TOKEN_EXPIRED", "REVOKED_TOKEN", "ACCESS_DENIED", "UNAUTHORIZED_CLIENT",
    "INVALID_CLIENT", "UNAUTHORIZED",
})

# 영구 오류 재시도 간격. 용도는 "정말 영구인가"의 **확인 1회**뿐이다 — 네트워크
# 순간 오류가 INVALID_TOKEN으로 잘못 읽히는 경우만 걸러내면 된다. 같은 토큰으로
# 수십 번 두드려 봐야 답이 달라지지 않으므로 여기를 길게 잡지 말 것.
PERMANENT_BACKOFF_SECONDS = (60,)
# 이 횟수에 도달하면 자동 갱신을 멈춘다. 확인 재시도 1회 = 실패 2회 후 중단.
MAX_PERMANENT_FAILURES = len(PERMANENT_BACKOFF_SECONDS) + 1

# 일시 오류 백오프. 상한을 둬서 무한히 길어지지 않게 한다.
TRANSIENT_BACKOFF_SECONDS = (30, 60, 120, 300, 600)
TRANSIENT_BACKOFF_MAX = TRANSIENT_BACKOFF_SECONDS[-1]

# 백오프에 더하는 무작위 비율(0 ~ +20%). 여러 guild가 같은 사고(치지직 5xx 등)로
# 동시에 실패하면 다음 시도 시각까지 같아져 재시도가 한 순간에 몰린다. 값을 **더하기만**
# 하는 이유: 빼면 서버가 알려준 Retry-After보다 일찍 두드릴 수 있다.
BACKOFF_JITTER_RATIO = 0.2

# 같은 오류를 매분 찍지 않는다. 상태가 바뀔 때 1회 + 이 간격의 요약만 남긴다.
LOG_SUMMARY_INTERVAL_SECONDS = 3600

_CODE_RE = re.compile(r"\b([A-Z][A-Z0-9_]{3,39})\b")
_HTTP_RE = re.compile(r"\b(?:HTTP[ _]?)?(4\d\d|5\d\d)\b")


def safe_error_code(exc: BaseException | str) -> str:
    """로그·DB에 남겨도 되는 짧은 코드만 뽑는다.

    예외 메시지 전체를 저장하면 URL·헤더·토큰 조각이 섞여 들어갈 수 있다. 그래서
    **대문자 코드 하나** 또는 HTTP 상태로 정규화하고, 아무것도 못 찾으면 UNKNOWN이다.
    """
    if isinstance(exc, BaseException):
        name = type(exc).__name__
        text = f"{name}: {exc}"
    else:
        text = str(exc or "")
        name = ""
    for m in _CODE_RE.finditer(text):
        code = m.group(1)
        if code in PERMANENT_CODES:
            return code
    if "timeout" in text.lower() or "Timeout" in name:
        return "TIMEOUT"
    m = _HTTP_RE.search(text)
    if m:
        return f"HTTP_{m.group(1)}"
    for m in _CODE_RE.finditer(text):
        # 알려지지 않은 코드라도 형태가 코드면 그대로 쓴다(길이 제한은 아래에서)
        return m.group(1)[:40]
    if "connect" in text.lower() or "network" in text.lower():
        return "NETWORK"
    return "UNKNOWN"


def classify_token_error(exc: BaseException | str) -> tuple[str, str]:
    """(kind, safe_code). kind는 KIND_PERMANENT 또는 KIND_TRANSIENT."""
    code = safe_error_code(exc)
    if code in PERMANENT_CODES:
        return (KIND_PERMANENT, code)
    if code.startswith("HTTP_4") and code not in ("HTTP_408", "HTTP_429"):
        # 401/403 같은 4xx는 자격증명 문제다. 다만 408/429는 일시 오류다.
        return (KIND_PERMANENT, code)
    return (KIND_TRANSIENT, code)


def _step(seq: tuple[int, ...], n: int) -> int:
    return seq[min(max(0, n - 1), len(seq) - 1)]


def _jittered(delay: float, rand: Callable[[], float] | None = None) -> int:
    """`delay` ~ `delay*(1+비율)` 사이의 정수 초. **아래로는 절대 내려가지 않는다.**"""
    r = (rand or random.random)()
    return int(delay + delay * BACKOFF_JITTER_RATIO * max(0.0, min(1.0, r)))


def next_after_failure(*, kind: str, fail_count: int, now: int,
                       retry_after: float | None = None,
                       rand: Callable[[], float] | None = None) -> dict:
    """실패 1회를 반영한 다음 상태.

    반환: {state, fail_count, next_try_at}
    - 영구 오류: 확인 1회만 하고 임계치에 닿으면 reauth_required(재시도 중단)
    - 일시 오류: 지수 백오프. **상태는 retrying에 머문다.**
    - 429의 Retry-After가 있으면 그 값을 우선한다(서버가 알려준 시간이 정확하다).
    - 모든 간격에 jitter를 **더한다**(`rand`는 테스트용 주입구).
    """
    n = int(fail_count or 0) + 1
    if kind == KIND_PERMANENT:
        if n >= MAX_PERMANENT_FAILURES:
            # 더 시도하지 않는다. next_try_at은 0으로 둬서 '예정 없음'을 분명히 한다.
            return {"state": STATE_REAUTH, "fail_count": n, "next_try_at": 0}
        return {"state": STATE_RETRYING, "fail_count": n,
                "next_try_at": now + _jittered(_step(PERMANENT_BACKOFF_SECONDS, n), rand)}
    if retry_after is not None and retry_after >= 0:
        delay = min(float(retry_after), float(TRANSIENT_BACKOFF_MAX))
    else:
        delay = _step(TRANSIENT_BACKOFF_SECONDS, n)
    return {"state": STATE_RETRYING, "fail_count": n,
            "next_try_at": now + _jittered(delay, rand)}


def after_success() -> dict:
    """정상 갱신 — 실패 이력을 지운다."""
    return {"state": STATE_OK, "fail_count": 0, "next_try_at": 0, "last_error_code": ""}


def should_attempt_refresh(state: str, next_try_at: int, now: int) -> bool:
    """지금 자동 갱신을 시도해도 되는가.

    reauth_required면 **영원히 False**다 — 사용자가 다시 연동해야만 풀린다.
    이것이 매분 재시도를 끊는 지점이다.
    """
    if state == STATE_REAUTH:
        return False
    return now >= int(next_try_at or 0)


def should_log(state_changed: bool, last_logged_at: int, now: int) -> bool:
    """로그를 남길 순간인가 — 상태 전환 시 1회, 그 뒤로는 시간당 요약만."""
    if state_changed:
        return True
    return now - int(last_logged_at or 0) >= LOG_SUMMARY_INTERVAL_SECONDS


def retry_after_seconds(exc: BaseException | str) -> float | None:
    """429 응답의 Retry-After를 뽑는다(있을 때만)."""
    text = str(exc or "")
    m = re.search(r"retry[-_ ]?after[\"'\s:=]+(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    return float(m.group(1)) if m else None
