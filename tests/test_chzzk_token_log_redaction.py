"""SEC-1b — 치지직 OAuth 토큰 발급·갱신 응답 본문이 로그에 남지 않는다.

여기서 쓰는 `accessToken`/`refreshToken` 값은 전부 **더미**다(`DUMMY_AT`/`DUMMY_RT`).
운영 토큰은 fixture에 넣지 않는다.

핵심 검증은 두 가지다.
  1) 로그 한 줄에 토큰 **값**도 **key 이름**도 나오지 않는다.
  2) 그러면서 OAuth 처리 결과(반환값·저장값)는 예전과 같다.
"""

import asyncio
import inspect
import json
import time

import pytest
import routers.chzzk_auth_router as car

DUMMY_AT = "AAAA-dummy-access"
DUMMY_RT = "BBBB-dummy-refresh"

FORBIDDEN = ["accessToken", "refreshToken", "idToken", DUMMY_AT, DUMMY_RT,
             "Authorization", "authorization"]


class FakeResp:
    """httpx.Response 대역. `.text`를 건드리면 즉시 실패하게 만든다 —
    '본문을 로그에 넘기지 않는다'를 계약으로 고정하기 위해서다."""

    text_touched = False

    def __init__(self, status_code: int, payload=None, *, raw: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self._raw = raw

    @property
    def text(self):
        FakeResp.text_touched = True
        return self._raw if self._raw is not None else json.dumps(self._payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, resp): self._resp = resp
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, *a, **kw):
        if isinstance(self._resp, Exception):
            raise self._resp
        return self._resp


@pytest.fixture
def logs(monkeypatch):
    """print를 가로채 로그 줄을 모은다."""
    out: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **kw: out.append(" ".join(map(str, a))))
    FakeResp.text_touched = False
    return out


def _patch_client(monkeypatch, resp):
    monkeypatch.setattr(car.httpx, "AsyncClient", lambda *a, **kw: FakeClient(resp))


def _assert_clean(lines):
    joined = "\n".join(lines)
    for bad in FORBIDDEN:
        assert bad not in joined, f"로그에 {bad!r} 노출"
    assert "body=" not in joined, "응답 본문이 로그에 남았다"


SUCCESS = {"code": 200, "message": None,
           "content": {"accessToken": DUMMY_AT, "refreshToken": DUMMY_RT,
                       "tokenType": "Bearer", "expiresIn": 86400}}


# ── refresh ────────────────────────────────────────────────────────────────
async def _co_test_refresh_success_logs_no_token(monkeypatch, logs):
    _patch_client(monkeypatch, FakeResp(200, SUCCESS))
    at, rt, exp = await car._refresh_chzzk_token("old-refresh")

    # ① OAuth 결과는 기존과 동일해야 한다
    assert at == DUMMY_AT and rt == DUMMY_RT
    assert exp >= int(time.time()) + 86000

    # ② 로그는 깨끗해야 한다
    _assert_clean(logs)
    assert FakeResp.text_touched is False, "resp.text 를 읽었다"
    assert len(logs) == 1
    payload = json.loads(logs[0].split("] ", 1)[1])
    assert payload["operation"] == "refresh"
    assert payload["status"] == 200
    assert isinstance(payload["duration_ms"], int)


async def _co_test_refresh_keeps_old_refresh_token_when_absent(monkeypatch, logs):
    """계약 불변: 응답에 refreshToken이 없으면 기존 것을 유지한다."""
    _patch_client(monkeypatch, FakeResp(200, {"content": {"accessToken": DUMMY_AT,
                                                          "expiresIn": 100}}))
    at, rt, exp = await car._refresh_chzzk_token("old-refresh")
    assert at == DUMMY_AT and rt == "old-refresh"
    _assert_clean(logs)


async def _co_test_refresh_failure_logs_status_and_provider_code(monkeypatch, logs):
    _patch_client(monkeypatch, FakeResp(401, {"code": 4010, "message": "무언가",
                                              "content": {"accessToken": DUMMY_AT}}))
    at, rt, exp = await car._refresh_chzzk_token("old-refresh")
    assert (at, rt, exp) == (None, None, 0)
    _assert_clean(logs)
    payload = json.loads(logs[0].split("] ", 1)[1])
    assert payload["status"] == 401
    assert payload["provider_code"] == "4010"
    assert "message" not in payload, "provider message는 남기지 않기로 했다"
    assert FakeResp.text_touched is False


async def _co_test_refresh_parse_failure_hides_body(monkeypatch, logs):
    _patch_client(monkeypatch, FakeResp(500, None, raw=f"<html>{DUMMY_AT}</html>"))
    at, rt, exp = await car._refresh_chzzk_token("old-refresh")
    assert (at, rt, exp) == (None, None, 0)
    _assert_clean(logs)
    payload = json.loads(logs[0].split("] ", 1)[1])
    assert payload["error_kind"] == "response_parse_failed"


async def _co_test_refresh_200_but_unparseable_body_hides_body(monkeypatch, logs):
    _patch_client(monkeypatch, FakeResp(200, None, raw=f"garbage {DUMMY_RT}"))
    at, rt, exp = await car._refresh_chzzk_token("old-refresh")
    assert (at, rt, exp) == (None, None, 0)
    _assert_clean(logs)


async def _co_test_refresh_transport_error_logs_only_type(monkeypatch, logs):
    _patch_client(monkeypatch, RuntimeError(f"connect failed to {DUMMY_AT}"))
    at, rt, exp = await car._refresh_chzzk_token("old-refresh")
    assert (at, rt, exp) == (None, None, 0)
    _assert_clean(logs)
    payload = json.loads(logs[0].split("] ", 1)[1])
    assert payload["error_kind"] == "RuntimeError"
    assert payload["status"] is None
    assert "connect failed" not in "\n".join(logs), "예외 메시지 전문을 남기지 않는다"


async def _co_test_logging_failure_does_not_break_refresh(monkeypatch):
    """로깅이 죽어도 OAuth 결과는 그대로여야 한다."""
    def boom(*a, **kw):
        raise RuntimeError("logging is down")

    monkeypatch.setattr("builtins.print", boom)
    _patch_client(monkeypatch, FakeResp(200, SUCCESS))
    at, rt, _ = await car._refresh_chzzk_token("old-refresh")
    assert at == DUMMY_AT and rt == DUMMY_RT


# ── error code 검증 계약 (A-2) ─────────────────────────────────────────────
# provider 응답도 신뢰 경계 밖이다. 짧은 안전 문자열만 통과시키고, 거부된 값은
# **어떤 형태로도** 기록하지 않는다(앞뒤 일부·길이·해시 전부 금지).
@pytest.mark.parametrize("payload,expected", [
    ({"code": 4010}, ("4010", None)),
    ({"code": "INVALID_TOKEN"}, ("INVALID_TOKEN", None)),
    ({"code": "E_BAD"}, ("E_BAD", None)),
    ({"code": "a.b:c-d_e"}, ("a.b:c-d_e", None)),
    ({"code": "A" * 64}, ("A" * 64, None)),                 # 경계: 64자 허용
    ({"code": None}, (None, None)),
    ({}, (None, None)),
])
def test_token_error_code_accepts_safe_values(payload, expected):
    assert car._token_error_code(FakeResp(400, payload)) == expected


@pytest.mark.parametrize("bad", [
    "A" * 65,                                  # 65자 — 경계 초과
    "A" * 500,
    "INVALID\nTOKEN",                        # 줄바꿈(로그 인젝션)
    "INVALID\r\nTOKEN",
    "INVALID TOKEN",                           # 공백
    "INVALID\tTOKEN",                        # 탭
    "INVALID\x00TOKEN",                      # NUL 제어문자
    "INVALID\x1b[31mTOKEN",                  # ANSI 이스케이프
    DUMMY_AT + "/" + DUMMY_RT,                 # 토큰처럼 생긴 값(허용 문자 밖)
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.QQ" * 3,   # JWT 비슷 + 길이 초과
    "",                                        # 빈 문자열
    "코드",                                     # 비 ASCII
])
def test_token_error_code_rejects_unsafe_values(bad):
    code, kind = car._token_error_code(FakeResp(400, {"code": bad}))
    assert code is None
    assert kind == "unsafe_provider_code"


@pytest.mark.parametrize("bad", [
    {"nested": DUMMY_AT}, [DUMMY_AT], True, False, 3.14,
])
def test_token_error_code_rejects_non_scalar_types(bad):
    code, kind = car._token_error_code(FakeResp(400, {"code": bad}))
    assert code is None
    assert kind == "unsafe_provider_code"


def test_token_error_code_on_unparseable():
    assert car._token_error_code(FakeResp(500, None, raw="<html>")) == (
        None, "response_parse_failed")


def test_unsafe_provider_code_never_reaches_the_log(logs):
    """거부된 값이 로그 어디에도 나타나면 안 된다."""
    nasty = "X" * 200 + "\n[chzzk-auth] fake-injected-line " + DUMMY_AT
    code, kind = car._token_error_code(FakeResp(401, {"code": nasty}))
    car._log_token_op("refresh", 401, duration_ms=5, error_kind=kind, error_code=code)
    joined = "\n".join(logs)
    assert DUMMY_AT not in joined
    assert "fake-injected-line" not in joined
    assert "XXXX" not in joined
    payload = json.loads(logs[0].split("] ", 1)[1])
    assert payload["error_kind"] == "unsafe_provider_code"
    assert "provider_code" not in payload, "거부된 값을 필드로 남기면 안 된다"


# ── 로그 헬퍼 자체 ─────────────────────────────────────────────────────────
def test_log_token_op_emits_allowlisted_fields_only(logs):
    car._log_token_op("exchange", 200, duration_ms=12)
    payload = json.loads(logs[0].split("] ", 1)[1])
    assert set(payload) == {"event", "operation", "status", "duration_ms"}
    assert payload["event"] == "chzzk_token_op"


def test_log_token_op_never_raises(monkeypatch):
    monkeypatch.setattr("builtins.print",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("no stdout")))
    car._log_token_op("refresh", 500, duration_ms=1, error_kind="X")   # 예외 없어야 함


# ── 정적 계약: 소스에 본문 로그가 남아 있지 않다 ────────────────────────────
def test_source_has_no_token_body_logging():
    """토큰 발급·갱신 경로에 본문 로그가 남아 있지 않다.

    검사 범위를 이 두 함수로 한정한다 — users/me·팔로워 목록 등 다른 응답 본문
    로그는 개인정보 항목(SEC-1c)이라 이번 P0 범위가 아니다."""
    for name, fn in (("refresh", car._refresh_chzzk_token),
                     ("callback", car.chzzk_callback)):
        src = inspect.getsource(fn)
        # 토큰 key 자체는 여기 남아 있는 게 정상이다 — 응답에서 값을 *꺼내* 저장하는
        # 코드이기 때문이다. 위험한 것은 본문을 로그로 넘기는 두 패턴뿐이고,
        # 실제로 로그에 안 나온다는 것은 위 런타임 테스트가 확인한다.
        for pat in ["resp.text", "body="]:
            assert pat not in src, f"{name}에 {pat!r} 잔존"


def test_callback_never_prints_the_parsed_token_payload():
    """`No accessToken in response: {token_data}` 처럼 파싱된 응답 dict를 통째로
    찍던 경로가 있었다. accessToken이 없어도 같은 응답에 refreshToken이 들어 있을
    수 있어 자격증명이 그대로 샜다."""
    src = inspect.getsource(car.chzzk_callback)
    for pat in ["{token_data", "token_data}", "{content}", "{c}"]:
        assert pat not in src, f"파싱된 응답을 그대로 찍는 코드 잔존: {pat!r}"
    # 그 자리는 allowlist 로그로 대체돼야 한다
    assert "missing_access_token" in src


# ── pytest-asyncio 미설치 → 동기 래퍼로 이벤트 루프를 직접 돌린다 ──────────
def test_test_refresh_success_logs_no_token(monkeypatch, logs):
    asyncio.run(_co_test_refresh_success_logs_no_token(monkeypatch, logs))

def test_test_refresh_keeps_old_refresh_token_when_absent(monkeypatch, logs):
    asyncio.run(_co_test_refresh_keeps_old_refresh_token_when_absent(monkeypatch, logs))

def test_test_refresh_failure_logs_status_and_provider_code(monkeypatch, logs):
    asyncio.run(_co_test_refresh_failure_logs_status_and_provider_code(monkeypatch, logs))

def test_test_refresh_parse_failure_hides_body(monkeypatch, logs):
    asyncio.run(_co_test_refresh_parse_failure_hides_body(monkeypatch, logs))

def test_test_refresh_200_but_unparseable_body_hides_body(monkeypatch, logs):
    asyncio.run(_co_test_refresh_200_but_unparseable_body_hides_body(monkeypatch, logs))

def test_test_refresh_transport_error_logs_only_type(monkeypatch, logs):
    asyncio.run(_co_test_refresh_transport_error_logs_only_type(monkeypatch, logs))

def test_test_logging_failure_does_not_break_refresh(monkeypatch):
    asyncio.run(_co_test_logging_failure_does_not_break_refresh(monkeypatch))

