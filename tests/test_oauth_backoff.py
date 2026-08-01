"""치지직 OAuth 토큰 갱신 실패의 분류·재시도 정책.

실측 배경(2026-07-31): guild 886237674665549865의 refresh token이 무효가 되자
`sync_loop`가 60초마다 영원히 같은 요청을 보내고 같은 경고를 남겼다(24분 25회).
영구 오류와 일시 오류에 같은 정책을 쓰면 이렇게 된다.

여기서 고정하는 불변식:
  - INVALID_TOKEN 같은 영구 오류는 몇 번 확인한 뒤 **자동 갱신을 멈춘다.**
  - 429/5xx/timeout은 상태를 바꾸지 않는다(멀쩡한 연동을 끊으면 안 된다).
  - 토큰·응답 본문은 어디에도 남기지 않는다.
"""
import httpx
import pytest

from utils import oauth_backoff as ob


# ── 1. 분류 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("msg", [
    "INVALID_TOKEN",
    "HTTPException: {'code': 'INVALID_TOKEN', 'message': '...'}",
    "invalid_grant 어쩌고 INVALID_GRANT",
    "EXPIRED_TOKEN",
    "ACCESS_DENIED",
])
def test_permanent_errors(msg):
    kind, code = ob.classify_token_error(Exception(msg))
    assert kind == ob.KIND_PERMANENT
    assert code in ob.PERMANENT_CODES


@pytest.mark.parametrize("msg,expected", [
    ("HTTP 429 Too Many Requests", "HTTP_429"),
    ("HTTP 500 Internal Server Error", "HTTP_500"),
    ("HTTP 503", "HTTP_503"),
    ("HTTP 408", "HTTP_408"),
])
def test_transient_errors(msg, expected):
    kind, code = ob.classify_token_error(Exception(msg))
    assert kind == ob.KIND_TRANSIENT
    assert code == expected


def test_timeout_is_transient():
    kind, code = ob.classify_token_error(httpx.ReadTimeout("timed out"))
    assert kind == ob.KIND_TRANSIENT
    assert code == "TIMEOUT"


def test_401_403_are_permanent():
    for st in (401, 403):
        kind, code = ob.classify_token_error(Exception(f"HTTP {st}"))
        assert kind == ob.KIND_PERMANENT, st
        assert code == f"HTTP_{st}"


def test_unknown_error_is_transient_not_permanent():
    """모르는 오류를 영구로 취급하면 멀쩡한 연동이 끊긴다 — 안전한 쪽으로 둔다."""
    kind, code = ob.classify_token_error(Exception("무슨 일이 났는지 모르겠음"))
    assert kind == ob.KIND_TRANSIENT
    assert code == "UNKNOWN"


# ── 2. 안전한 코드만 남긴다 ────────────────────────────────────────────────
def test_error_code_never_leaks_secrets():
    leaky = ("HTTPException: POST https://openapi.chzzk.naver.com/auth/v1/token "
             "Authorization=Bearer abcdef123456 refresh_token=ZZZZSECRETZZZZ "
             "{'code':'INVALID_TOKEN'}")
    code = ob.safe_error_code(Exception(leaky))
    assert code == "INVALID_TOKEN"
    for secret in ("abcdef123456", "ZZZZSECRETZZZZ", "Bearer", "openapi.chzzk"):
        assert secret not in code


def test_error_code_is_short():
    code = ob.safe_error_code(Exception("X" * 500))
    assert len(code) <= 40


# ── 3. 영구 오류 재시도 → reauth_required ─────────────────────────────────
def test_permanent_backoff_then_stops():
    now = 1_000_000
    st = {"state": ob.STATE_OK, "fail_count": 0}
    gaps = []
    for _ in range(4):
        st = ob.next_after_failure(kind=ob.KIND_PERMANENT,
                                   fail_count=st["fail_count"], now=now)
        gaps.append(st["next_try_at"] - now if st["next_try_at"] else None)
    # 확인 1회(1분) → 중단. 같은 토큰을 더 두드려도 답은 같다.
    assert 60 <= gaps[0] <= 72
    assert st["state"] == ob.STATE_REAUTH
    assert gaps[1:] == [None, None, None]


def test_permanent_gives_up_within_a_few_minutes():
    """무효 토큰이 reauth_required가 되기까지 걸리는 시간의 상한을 못 박는다."""
    now = 0
    worst = 0
    fc = 0
    state = ob.STATE_OK
    while state != ob.STATE_REAUTH:
        st = ob.next_after_failure(kind=ob.KIND_PERMANENT, fail_count=fc, now=worst)
        fc, state = st["fail_count"], st["state"]
        worst = st["next_try_at"] or worst
    assert worst - now <= 120, f"{worst}초 — 확인 재시도가 길어지면 그동안 계속 호출된다"
    assert fc == ob.MAX_PERMANENT_FAILURES


def test_reauth_required_stops_all_automatic_refresh():
    now = 2_000_000
    assert ob.should_attempt_refresh(ob.STATE_REAUTH, 0, now) is False
    # 아무리 시간이 지나도 스스로 풀리지 않는다
    assert ob.should_attempt_refresh(ob.STATE_REAUTH, 0, now + 10 ** 9) is False


def test_backoff_blocks_requests_between_tries():
    now = 3_000_000
    st = ob.next_after_failure(kind=ob.KIND_PERMANENT, fail_count=0, now=now)
    assert ob.should_attempt_refresh(st["state"], st["next_try_at"], now) is False
    assert ob.should_attempt_refresh(st["state"], st["next_try_at"], now + 59) is False
    assert ob.should_attempt_refresh(st["state"], st["next_try_at"],
                                     st["next_try_at"]) is True


# ── 3-1. jitter ────────────────────────────────────────────────────────────
def test_jitter_only_adds_never_subtracts():
    """서버가 알려준 시간보다 **일찍** 두드리면 안 된다 — jitter는 더하기만 한다."""
    now = 3_500_000
    for r in (0.0, 0.5, 1.0):
        st = ob.next_after_failure(kind=ob.KIND_TRANSIENT, fail_count=0, now=now,
                                   retry_after=100, rand=lambda: r)
        delay = st["next_try_at"] - now
        assert 100 <= delay <= 120, (r, delay)


def test_jitter_spreads_simultaneous_failures():
    """같은 사고로 여러 guild가 동시에 실패해도 다음 시도가 한 순간에 몰리면 안 된다."""
    now = 3_600_000
    delays = {ob.next_after_failure(kind=ob.KIND_TRANSIENT, fail_count=3,
                                    now=now)["next_try_at"] - now
              for _ in range(200)}
    assert len(delays) > 1, "jitter가 없다 — 200번이 전부 같은 시각이다"
    assert min(delays) >= 300, "기본 간격 아래로 내려가면 안 된다"
    assert max(delays) <= 300 * (1 + ob.BACKOFF_JITTER_RATIO) + 1


# ── 4. 일시 오류 ───────────────────────────────────────────────────────────
def test_transient_backoff_grows_and_caps():
    now = 4_000_000
    st = {"fail_count": 0}
    gaps = []
    for _ in range(8):
        st = ob.next_after_failure(kind=ob.KIND_TRANSIENT,
                                   fail_count=st["fail_count"], now=now,
                                   rand=lambda: 0.0)      # jitter 없이 계단만 본다
        gaps.append(st["next_try_at"] - now)
    assert gaps[:5] == [30, 60, 120, 300, 600]
    assert gaps[5:] == [600, 600, 600], "상한을 넘어 무한히 길어지면 안 된다"
    # jitter가 붙어도 상한을 크게 넘지 않는다
    hi = ob.next_after_failure(kind=ob.KIND_TRANSIENT, fail_count=99, now=now,
                               rand=lambda: 1.0)["next_try_at"] - now
    assert hi <= ob.TRANSIENT_BACKOFF_MAX * (1 + ob.BACKOFF_JITTER_RATIO) + 1


def test_transient_never_becomes_reauth_required():
    """5xx가 아무리 반복돼도 연동을 끊지 않는다."""
    now = 5_000_000
    fc = 0
    for _ in range(50):
        st = ob.next_after_failure(kind=ob.KIND_TRANSIENT, fail_count=fc, now=now)
        fc = st["fail_count"]
        assert st["state"] == ob.STATE_RETRYING


def test_retry_after_is_respected():
    now = 6_000_000
    st = ob.next_after_failure(kind=ob.KIND_TRANSIENT, fail_count=0, now=now,
                              retry_after=42, rand=lambda: 0.0)
    assert st["next_try_at"] - now == 42


def test_retry_after_is_capped():
    now = 6_000_000
    st = ob.next_after_failure(kind=ob.KIND_TRANSIENT, fail_count=0, now=now,
                              retry_after=99999, rand=lambda: 0.0)
    assert st["next_try_at"] - now == ob.TRANSIENT_BACKOFF_MAX


def test_retry_after_parsing():
    assert ob.retry_after_seconds("429 Retry-After: 30") == 30.0
    assert ob.retry_after_seconds("retry_after=7.5") == 7.5
    assert ob.retry_after_seconds("HTTP 500") is None


# ── 5. 복구 ────────────────────────────────────────────────────────────────
def test_success_clears_everything():
    st = ob.after_success()
    assert st == {"state": ob.STATE_OK, "fail_count": 0, "next_try_at": 0,
                  "last_error_code": ""}
    assert ob.should_attempt_refresh(st["state"], st["next_try_at"], 0) is True


# ── 6. 로그 억제 ───────────────────────────────────────────────────────────
def test_log_once_on_transition_then_hourly():
    now = 7_000_000
    assert ob.should_log(True, now, now) is True, "상태가 바뀌면 무조건 남긴다"
    assert ob.should_log(False, now, now + 60) is False, "매분 같은 로그 금지"
    assert ob.should_log(False, now, now + 3599) is False
    assert ob.should_log(False, now, now + 3600) is True, "시간당 요약은 남긴다"


def test_no_log_spam_over_24_hours():
    """예전에는 24분에 25줄이었다. 같은 조건에서 몇 줄이 되는지 세어 본다."""
    now = 0
    logged = 0
    last = 0
    state = ob.STATE_OK
    fc = 0
    for minute in range(24 * 60):          # 24시간, 1분마다 sync_loop
        t = minute * 60
        if not ob.should_attempt_refresh(state, now, t):
            continue
        st = ob.next_after_failure(kind=ob.KIND_PERMANENT, fail_count=fc, now=t)
        changed = st["state"] != state
        state, fc, now = st["state"], st["fail_count"], st["next_try_at"]
        if ob.should_log(changed, last, t):
            logged += 1
            last = t
    assert logged <= 3, f"24시간에 {logged}줄 — 예전 방식은 1,440줄이었다"
    assert state == ob.STATE_REAUTH


# ── 7. guild 격리 ──────────────────────────────────────────────────────────
def test_states_are_per_guild():
    """한 guild의 상태가 다른 guild에 새지 않는다(정책은 순수 함수라 공유 상태가 없다)."""
    now = 8_000_000
    a = ob.next_after_failure(kind=ob.KIND_PERMANENT, fail_count=2, now=now)
    b = ob.next_after_failure(kind=ob.KIND_TRANSIENT, fail_count=0, now=now)
    assert a["state"] == ob.STATE_REAUTH
    assert b["state"] == ob.STATE_RETRYING
    assert ob.should_attempt_refresh(b["state"], b["next_try_at"],
                                     b["next_try_at"]) is True
