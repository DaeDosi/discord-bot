"""SEC-1a — uvicorn access log의 민감 query 마스킹.

여기서 쓰는 토큰 비슷한 문자열은 전부 **더미**다. 운영 토큰을 fixture에 넣지 않는다.
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                                "web", "backend")))

from log_redaction import (  # noqa: E402
    REDACTED,
    UNEXPECTED,
    QueryRedactionFilter,
    install_query_redaction,
    redact_query,
)

DUMMY = "AAAA-not-a-real-token"


def _record(full_path: str, *, client="1.2.3.4:5", method="GET",
            version="1.1", status=200) -> logging.LogRecord:
    """uvicorn이 실제로 만드는 것과 같은 모양의 access 레코드."""
    r = logging.LogRecord("uvicorn.access", logging.INFO, "(unknown file)", 0,
                          '%s - "%s %s HTTP/%s" %d',
                          (client, method, full_path, version, status), None)
    return r


def _run(full_path: str, **kw) -> tuple:
    r = _record(full_path, **kw)
    assert QueryRedactionFilter().filter(r) is True, "필터가 레코드를 삼켰다"
    return r.args


def _path(full_path: str, **kw) -> str:
    return _run(full_path, **kw)[2]


# ── 1~2. 민감 key 마스킹 ────────────────────────────────────────────────────
@pytest.mark.parametrize("key", [
    "code", "state", "token", "access_token", "refresh_token", "id_token",
    "client_secret", "secret", "authorization", "password", "api_key",
])
def test_sensitive_keys_are_redacted(key):
    out = _path(f"/auth/callback?{key}={DUMMY}")
    assert out == f"/auth/callback?{key}={REDACTED}"
    assert DUMMY not in out


def test_callback_code_and_state_both_redacted():
    out = _path(f"/auth/callback?code={DUMMY}&state={DUMMY}2")
    assert out == f"/auth/callback?code={REDACTED}&state={REDACTED}"
    assert DUMMY not in out


# ── 3. 대소문자 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", ["CODE", "Code", "cOdE", "ACCESS_TOKEN",
                                 "Client_Secret", "Authorization"])
def test_key_case_is_ignored(key):
    out = _path(f"/x?{key}={DUMMY}")
    assert out == f"/x?{key}={REDACTED}", "key 원문 대소문자는 보존하되 값은 가려야 한다"
    assert DUMMY not in out


# ── 4. 중복 key ─────────────────────────────────────────────────────────────
def test_every_duplicate_sensitive_key_is_redacted():
    out = _path(f"/x?code={DUMMY}1&code={DUMMY}2&CODE={DUMMY}3")
    assert out == f"/x?code={REDACTED}&code={REDACTED}&CODE={REDACTED}"
    assert DUMMY not in out


# ── 5. percent-encoding ─────────────────────────────────────────────────────
def test_percent_encoded_key_is_still_caught():
    """`%63ode`는 서버가 디코딩해 읽으므로 필터도 디코딩해서 봐야 한다."""
    out = _path(f"/x?%63ode={DUMMY}")
    assert out == f"/x?%63ode={REDACTED}"
    assert DUMMY not in out


def test_percent_encoded_value_never_leaks():
    out = _path("/x?code=AAAA%2Dabc%20def")
    assert out == f"/x?code={REDACTED}"
    assert "AAAA" not in out and "%2D" not in out


def test_plus_encoded_key_is_caught():
    out = _path(f"/x?access+token={DUMMY}")
    # `+`는 unquote_plus에서 공백이 되고 strip 후에도 'access token'이라 목록에 없다.
    # 잡히지 않는 것이 정상 — 값이 그대로 남는지만 확인해 동작을 고정한다.
    assert out == f"/x?access+token={DUMMY}"


# ── 6. 값 없는 key ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("q", ["code", "code=", "code=&state=", "state"])
def test_valueless_sensitive_key_does_not_raise(q):
    out = _path(f"/x?{q}")
    assert REDACTED in out
    for part in out.split("?", 1)[1].split("&"):
        k = part.split("=")[0]
        if k.lower() in ("code", "state"):
            assert part == f"{k}={REDACTED}"


# ── 7. malformed query ──────────────────────────────────────────────────────
@pytest.mark.parametrize("q", [
    "&&&", "=", "=value", "a=1&&b=2", "%", "%zz=1", "code=%", "a=b=c&code=x",
    "?", "#frag", "code", "&code=x&",
])
def test_malformed_query_never_raises(q):
    out = _path(f"/x?{q}")               # 예외가 나면 여기서 실패한다
    assert out.startswith("/x?")


def test_malformed_query_still_redacts_sensitive_pairs():
    out = _path("/x?a=b=c&code=" + DUMMY)
    assert DUMMY not in out
    assert f"code={REDACTED}" in out
    assert "a=b=c" in out, "민감하지 않은 pair는 원형 그대로 남아야 한다"


# ── 8. 일반 query 보존 ──────────────────────────────────────────────────────
def test_ordinary_query_is_untouched():
    p = "/stats?tab=singcup&sort=heart1h"
    assert _path(p) == p


def test_ordinary_query_encoding_and_order_preserved():
    """parse_qsl로 재조립하면 `+`/`%20`이 뒤섞이고 순서가 바뀐다 — 그러면 안 된다."""
    p = "/x?b=2&a=1&q=hello+world&z=%ED%95%9C"
    assert _path(p) == p


def test_mixed_query_only_masks_sensitive_part():
    out = _path(f"/x?tab=singcup&code={DUMMY}&sort=heart1h")
    assert out == f"/x?tab=singcup&code={REDACTED}&sort=heart1h"


def test_value_containing_word_code_is_not_masked():
    """값에 'code'가 들어 있다고 가리면 정상 쿼리가 깨진다 — key만 본다."""
    p = "/x?q=barcode&name=decode"
    assert _path(p) == p


# ── 9. 나머지 필드 보존 ─────────────────────────────────────────────────────
def test_other_record_args_are_preserved():
    args = _run(f"/auth/callback?code={DUMMY}",
                client="10.0.0.1:443", method="POST", version="2", status=302)
    assert args[0] == "10.0.0.1:443"
    assert args[1] == "POST"
    assert args[3] == "2"
    assert args[4] == 302
    assert len(args) == 5


def test_formatted_message_keeps_shape_and_hides_value():
    r = _record(f"/auth/callback?code={DUMMY}")
    QueryRedactionFilter().filter(r)
    msg = r.getMessage()
    assert DUMMY not in msg
    assert '"GET /auth/callback?code=[REDACTED] HTTP/1.1" 200' in msg
    assert msg.startswith("1.2.3.4:5 - ")


# ── 10. query 없음 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("p", ["/", "/stats", "/api/singcup/main", "/auth/callback"])
def test_no_query_means_no_change(p):
    r = _record(p)
    before = r.args
    QueryRedactionFilter().filter(r)
    assert r.args is before, "쿼리가 없으면 레코드를 건드리지 않는다"


def test_empty_query_string_is_untouched():
    r = _record("/x?")
    QueryRedactionFilter().filter(r)
    assert r.args[2] == "/x?"


# ── 11. 예상 밖 record.args ─────────────────────────────────────────────────
@pytest.mark.parametrize("args", [
    None, (), ("a",), ("a", "b", "c"), ("a", "b", "c", "d", "e", "f"),
    ("a", "b", None, "d", 200), ("a", "b", 123, "d", 200),
    ["1.2.3.4", "GET", "/x?code=1", "1.1", 200],       # tuple이 아닌 list
    # `%(path)s` 스타일 매핑 인자. LogRecord가 이걸 dict로 풀어 두므로 5-tuple이 아니다.
    ({"path": "/x?code=1"},),
])
def test_unexpected_args_shape_is_blanked_not_passed_through(args):
    """**fail-closed.** 아는 모양이 아니면 원문을 통과시키지 않는다.

    예전 구현은 그대로 True를 반환했는데, 그러면 uvicorn이 포맷을 바꾸거나 예상 밖
    레코드가 들어오는 순간 보안 필터가 조용히 꺼진다."""
    r = logging.LogRecord("uvicorn.access", logging.INFO, "f", 0, "%s", args, None)
    assert QueryRedactionFilter().filter(r) is True
    assert r.args is None
    assert r.msg == UNEXPECTED
    assert r.getMessage() == UNEXPECTED


def test_blanked_record_never_echoes_the_original():
    """정적 문자열만 남는다 — 원본 msg·args·repr(args)를 다시 넣지 않는다."""
    r = logging.LogRecord("uvicorn.access", logging.INFO, "f", 0,
                          f"raw path /auth/callback?code={DUMMY}", ("only-one",), None)
    QueryRedactionFilter().filter(r)
    msg = r.getMessage()
    assert msg == UNEXPECTED
    assert DUMMY not in msg
    assert "code=" not in msg
    assert "only-one" not in msg


@pytest.mark.parametrize("secret_key", [
    "code", "state", "token", "access_token", "refresh_token", "secret",
])
def test_no_sensitive_value_survives_any_unexpected_shape(secret_key):
    """예상 밖 구조 어디에 민감 값이 있어도 로그로 나가지 않는다."""
    payloads = [
        (f"/x?{secret_key}={DUMMY}",),                                  # 1-tuple
        ("a", "b", f"/x?{secret_key}={DUMMY}"),                         # 3-tuple
        ("a", "b", f"/x?{secret_key}={DUMMY}", "1.1", 200, "extra"),    # 6-tuple
        ["a", "b", f"/x?{secret_key}={DUMMY}", "1.1", 200],             # list
        ("a", "b", None, "1.1", 200),
        ("a", "b", 12345, "1.1", 200),
    ]
    for args in payloads:
        r = logging.LogRecord("uvicorn.access", logging.INFO, "f", 0, "%s", args, None)
        assert QueryRedactionFilter().filter(r) is True
        msg = r.getMessage()
        assert DUMMY not in msg, f"{args!r} 에서 값이 샜다"
        assert f"{secret_key}=" not in msg


def test_args_none_is_blanked():
    r = logging.LogRecord("uvicorn.access", logging.INFO, "f", 0,
                          f"/auth/callback?code={DUMMY}", None, None)
    assert QueryRedactionFilter().filter(r) is True
    assert r.getMessage() == UNEXPECTED
    assert DUMMY not in r.getMessage()


# ── 12. fail-closed ─────────────────────────────────────────────────────────
def test_internal_failure_masks_whole_query(monkeypatch):
    """필터가 경로를 이해하지 못했다면 원문을 남기는 쪽으로 실패하면 안 된다."""
    import log_redaction as lr

    def boom(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(lr, "redact_query", boom)
    r = _record(f"/auth/callback?code={DUMMY}&state={DUMMY}2")
    assert lr.QueryRedactionFilter().filter(r) is True
    assert r.args[2] == f"/auth/callback?{REDACTED}"
    assert DUMMY not in r.getMessage()
    # 경로·메서드·상태는 그대로여야 진단이 가능하다
    assert r.args[1] == "GET" and r.args[4] == 200


def test_fail_closed_does_not_reraise(monkeypatch):
    """필터 예외가 HTTP 요청 처리로 전파되면 안 된다 → 항상 True 반환, 예외 없음."""
    import log_redaction as lr

    class Weird(str):
        def partition(self, sep):
            raise ValueError("nope")

    monkeypatch.setattr(lr, "redact_query",
                        lambda _: (_ for _ in ()).throw(ValueError("x")))
    r = _record("/x?code=1")
    r.args = (r.args[0], r.args[1], Weird("/x?code=1"), r.args[3], r.args[4])
    assert lr.QueryRedactionFilter().filter(r) is True   # 예외가 새면 실패


# ── 13. 중복 설치 방지 ──────────────────────────────────────────────────────
def test_install_is_idempotent():
    name = "test.uvicorn.access.idem"
    logger = logging.getLogger(name)
    logger.filters.clear()
    assert install_query_redaction(name) is True
    assert install_query_redaction(name) is False
    assert install_query_redaction(name) is False
    assert len(logger.filters) == 1
    logger.filters.clear()


def test_main_import_installs_exactly_one_filter():
    """main.py를 다시 import해도 필터가 쌓이지 않는다."""
    import importlib

    import log_redaction as lr
    logger = logging.getLogger("uvicorn.access")
    logger.filters.clear()
    lr.install_query_redaction()
    lr.install_query_redaction()
    importlib.reload(lr)          # 모듈 reload로 표식이 사라지지 않는지
    lr.install_query_redaction()
    marked = [f for f in logger.filters if getattr(f, "_nexbot_query_redaction", False)]
    assert len(marked) == 1, f"필터 {len(marked)}개 누적"
    logger.filters.clear()


# ── 14. 여러 handler ────────────────────────────────────────────────────────
def test_multiple_handlers_never_see_the_original():
    """로거 필터는 어떤 핸들러보다 먼저 돈다 — 핸들러 수와 무관하게 원문이 안 샌다."""
    name = "test.uvicorn.access.multi"
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    install_query_redaction(name)

    seen: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    for _ in range(3):
        logger.addHandler(Capture())

    logger.info('%s - "%s %s HTTP/%s" %d',
                "1.2.3.4:5", "GET", f"/auth/callback?code={DUMMY}", "1.1", 200)

    assert len(seen) == 3
    for m in seen:
        assert DUMMY not in m
        assert "code=[REDACTED]" in m
    logger.handlers.clear()
    logger.filters.clear()


# ── redact_query 단독 계약 ──────────────────────────────────────────────────
def test_redact_query_returns_input_when_no_query():
    assert redact_query("/a/b") == "/a/b"


def test_redact_query_does_not_invent_a_fragment():
    out = redact_query(f"/x?code={DUMMY}")
    assert "#" not in out


# ── A-1 추가: 정상 레코드의 관측성은 그대로 ────────────────────────────────
def test_normal_record_keeps_full_observability():
    """fail-closed를 넣었다고 정상 요청의 관측 정보를 잃으면 안 된다."""
    r = _record("/api/singcup/main?limit=3000",
                client="203.0.113.9:52344", method="GET", version="1.1", status=200)
    QueryRedactionFilter().filter(r)
    msg = r.getMessage()
    assert msg == '203.0.113.9:52344 - "GET /api/singcup/main?limit=3000 HTTP/1.1" 200'


def test_normal_callback_keeps_method_path_status_client():
    r = _record(f"/auth/callback?code={DUMMY}&state={DUMMY}2",
                client="198.51.100.7:1", method="GET", version="1.1", status=302)
    QueryRedactionFilter().filter(r)
    assert r.args[0] == "198.51.100.7:1"
    assert r.args[1] == "GET"
    assert r.args[2].startswith("/auth/callback?")   # 경로는 살아 있다
    assert r.args[3] == "1.1"
    assert r.args[4] == 302
    assert DUMMY not in r.getMessage()


def test_internal_error_on_non_five_tuple_blanks_record(monkeypatch):
    """예외 경로에서도 5-tuple이 아니면 통째로 비운다."""
    import log_redaction as lr
    monkeypatch.setattr(lr, "redact_query",
                        lambda _: (_ for _ in ()).throw(ValueError("x")))
    r = logging.LogRecord("uvicorn.access", logging.INFO, "f", 0,
                          f"/x?code={DUMMY}", ("a", "b", f"/x?code={DUMMY}"), None)
    assert lr.QueryRedactionFilter().filter(r) is True
    assert DUMMY not in r.getMessage()
