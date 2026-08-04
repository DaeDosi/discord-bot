"""AWS 서울 outbound poller — 인증·활성화·비노출 계약.

왜 이 통로가 있나: `krOnlyViewing=true` 클립을 Railway 해외 IP에서 카드 API로
부르면 HTTP 200이면서도 `content.vod` 블록이 통째로 빠진다. 하트는
`interaction.emotion`에 있어 그대로 오므로 **하트만 갱신되고 조회수는 unknown으로
남는다**. 조회수가 0으로 응답된 게 아니라 **컨테이너가 누락된 것**이라 상태가
`observed_zero`가 아니라 `unknown`이다. 한국에서 같은 API를 불러야 복구된다.

여기서 고정하는 계약은 다섯이다.

  ① **전용 secret** — `SINGCUP_ADMIN_SECRET`을 재사용하지 않는다.
  ② **서명** — ts + method + path + sha256(raw body). 하나라도 어긋나면 401.
  ③ **재전송 불가** — timestamp 오차 ±300초, nonce 600초 내 재사용 금지.
  ④ **fail-closed** — secret 미설정이면 503. 단 **앱 기동은 죽지 않는다**.
  ⑤ **비노출** — 인증 실패 응답에 후보·task·clipUid가 새지 않는다.
"""
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
import singcup_kr_poller as krp
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "test-dummy-secret-not-a-real-value"
TASKS = "/api/internal/singcup/kr-poller/tasks"
RESULTS = "/api/internal/singcup/kr-poller/results"
_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(db, monkeypatch):
    import routers.kr_poller_router as kr

    monkeypatch.setenv("SINGCUP_KR_POLLER_SECRET", SECRET)
    monkeypatch.setenv("SINGCUP_KRP_ENABLED", "true")
    # 자체 스로틀은 기본으로 꺼 둔다. 켜 두면 한 테스트에서 두 번 요청하는 계약
    # (nonce 재전송 등)이 스로틀에 먼저 걸려 무엇을 검증하는지 흐려진다.
    # 스로틀 자체는 아래 전용 테스트가 값을 되돌려 놓고 검증한다.
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 0)
    app = FastAPI()
    app.include_router(kr.router)
    return TestClient(app, raise_server_exceptions=True)


def _headers(body: bytes, *, path=TASKS, secret=SECRET, ts=None, nonce=None,
             method="POST", sig=None):
    ts = str(int(time.time())) if ts is None else str(ts)
    nonce = nonce or hashlib.sha256(
        f"{time.time()}{path}{body!r}".encode()).hexdigest()[:32]
    if sig is None:
        msg = f"{ts}\n{method}\n{path}\n{hashlib.sha256(body).hexdigest()}"
        sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"X-KRP-Timestamp": ts, "X-KRP-Nonce": nonce, "X-KRP-Signature": sig,
            "Content-Type": "application/json"}


def _post(client, path=TASKS, payload=None, **kw):
    body = json.dumps(payload if payload is not None else {"limit": 5}).encode()
    return client.post(path, content=body, headers=_headers(body, path=path, **kw))


# ── ① 정상 인증 ────────────────────────────────────────────────────────────
def test_valid_signature_is_accepted(client, db):
    r = _post(client)
    assert r.status_code == 200
    assert "tasks" in r.json()


def test_signing_string_is_exactly_the_documented_shape():
    """계약을 코드가 아니라 테스트가 고정한다 — AWS 쪽과 한 글자도 달라지면 안 된다."""
    body = b'{"a":1}'
    got = krp.signing_string(1700000000, "POST", TASKS, body)
    assert got == "1700000000\nPOST\n%s\n%s" % (
        TASKS, hashlib.sha256(body).hexdigest())


# ── ② 서명 오류 ────────────────────────────────────────────────────────────
def test_wrong_signature_is_rejected(client, db):
    r = _post(client, sig="0" * 64)
    assert r.status_code == 401


def test_signature_from_other_secret_is_rejected(client, db):
    r = _post(client, secret="another-dummy-secret")
    assert r.status_code == 401


def test_admin_secret_is_not_accepted(client, db, monkeypatch):
    """SINGCUP_ADMIN_SECRET 재사용 금지 — 그 값으로 서명해도 통과하면 안 된다."""
    monkeypatch.setenv("SINGCUP_ADMIN_SECRET", "admin-dummy-secret")
    r = _post(client, secret="admin-dummy-secret")
    assert r.status_code == 401


@pytest.mark.parametrize("missing", ["X-KRP-Timestamp", "X-KRP-Nonce",
                                     "X-KRP-Signature"])
def test_missing_header_is_rejected(client, db, missing):
    body = json.dumps({"limit": 5}).encode()
    h = _headers(body)
    h.pop(missing)
    assert client.post(TASKS, content=body, headers=h).status_code == 401


# ── ③ 만료 timestamp ───────────────────────────────────────────────────────
@pytest.mark.parametrize("delta", [-600, 600, -301, 301])
def test_expired_timestamp_is_rejected(client, db, delta):
    r = _post(client, ts=int(time.time()) + delta)
    assert r.status_code == 401


@pytest.mark.parametrize("delta", [-299, 0, 299])
def test_timestamp_within_skew_is_accepted(client, db, delta):
    r = _post(client, ts=int(time.time()) + delta)
    assert r.status_code == 200


def test_non_numeric_timestamp_is_rejected(client, db):
    r = _post(client, ts="not-a-number")
    assert r.status_code == 401


# ── ④ nonce 재전송 ─────────────────────────────────────────────────────────
def test_nonce_replay_is_rejected(client, db):
    body = json.dumps({"limit": 5}).encode()
    h = _headers(body)
    assert client.post(TASKS, content=body, headers=h).status_code == 200
    assert client.post(TASKS, content=body, headers=h).status_code == 401


def test_replay_of_whole_request_is_rejected(client, db):
    """서명이 유효해도 같은 요청을 그대로 다시 보내면 막힌다."""
    body = json.dumps({"limit": 5}).encode()
    h = _headers(body)
    client.post(TASKS, content=body, headers=h)
    r = client.post(TASKS, content=body, headers=h)
    assert r.status_code == 401
    assert "tasks" not in r.json()


# ── ⑤ body 변조 ────────────────────────────────────────────────────────────
def test_tampered_body_is_rejected(client, db):
    body = json.dumps({"limit": 5}).encode()
    h = _headers(body)
    tampered = json.dumps({"limit": 9999}).encode()
    assert client.post(TASKS, content=tampered, headers=h).status_code == 401


def test_body_digest_uses_raw_bytes_not_reserialized_json(client, db):
    """공백만 다른 동등 JSON은 **다른** body다 — 재직렬화로 서명하면 안 된다."""
    body = b'{"limit": 5}'
    h = _headers(body)
    assert client.post(TASKS, content=b'{"limit":5}', headers=h).status_code == 401


# ── ⑥ secret 미설정 fail-closed ────────────────────────────────────────────
@pytest.mark.parametrize("path", [TASKS, RESULTS])
def test_missing_secret_is_fail_closed(client, db, monkeypatch, path):
    monkeypatch.delenv("SINGCUP_KR_POLLER_SECRET", raising=False)
    body = json.dumps({"limit": 5}).encode()
    r = client.post(path, content=body, headers=_headers(body, path=path))
    assert r.status_code == 503


def test_empty_secret_is_fail_closed(client, db, monkeypatch):
    monkeypatch.setenv("SINGCUP_KR_POLLER_SECRET", "")
    body = json.dumps({"limit": 5}).encode()
    r = client.post(TASKS, content=body, headers=_headers(body))
    assert r.status_code == 503


def test_backend_imports_without_secret(monkeypatch):
    """secret이 없어도 **앱 전체 기동이 죽지 않는다**.

    폐기된 relay_router가 없는 함수를 import해 기동을 죽이던 실패를 반복하지 않는다.
    """
    monkeypatch.delenv("SINGCUP_KR_POLLER_SECRET", raising=False)
    import importlib

    import routers.kr_poller_router as kr
    importlib.reload(kr)
    app = FastAPI()
    app.include_router(kr.router)          # 예외가 나지 않아야 한다
    assert any("kr-poller" in r.path for r in app.routes)


# ── ⑦ ENABLED=false 무동작 ─────────────────────────────────────────────────
@pytest.mark.parametrize("path", [TASKS, RESULTS])
def test_disabled_does_nothing(client, db, monkeypatch, path):
    monkeypatch.setenv("SINGCUP_KRP_ENABLED", "false")
    body = json.dumps({"limit": 5}).encode()
    r = client.post(path, content=body, headers=_headers(body, path=path))
    assert r.status_code == 503


def test_default_env_is_disabled(monkeypatch):
    """기본값만으로는 아무 것도 켜지지 않는다."""
    monkeypatch.delenv("SINGCUP_KRP_ENABLED", raising=False)
    assert krp.enabled() is False


def test_disabled_issues_no_lease(client, db, monkeypatch):
    monkeypatch.setenv("SINGCUP_KRP_ENABLED", "false")
    _post(client)
    assert db(_lease_count()) == 0


async def _lease_count():
    import database
    conn = await database.get_db()
    return (await (await conn.execute(
        "SELECT COUNT(*) n FROM singcup_kr_poller_lease")).fetchone())["n"]


# ── 인증 실패 응답에 작업 데이터가 없다 (33) ───────────────────────────────
@pytest.mark.parametrize("kw", [{"sig": "0" * 64}, {"ts": 0},
                                {"secret": "another-dummy-secret"}])
def test_auth_failure_leaks_no_work_data(client, db, kw):
    r = _post(client, **kw)
    text = r.text
    assert r.status_code == 401
    for leaked in ("tasks", "clipUid", "leaseToken", "taskId", "videoId", "recId"):
        assert leaked not in text


def test_auth_failure_leaks_no_secret(client, db):
    r = _post(client, sig="0" * 64)
    assert SECRET not in r.text
    assert "SINGCUP_KR_POLLER_SECRET" not in r.text


# ── 로그 비노출 (32) ───────────────────────────────────────────────────────
def test_logs_never_contain_secret_or_signature(client, db, capsys):
    body = json.dumps({"limit": 5}).encode()
    h = _headers(body)
    client.post(TASKS, content=body, headers=h)
    client.post(TASKS, content=body, headers=_headers(body, sig="0" * 64))
    out = capsys.readouterr().out
    assert SECRET not in out
    assert h["X-KRP-Signature"] not in out
    assert h["X-KRP-Nonce"] not in out


# ── ⑧ systemd 계약 (34) ────────────────────────────────────────────────────
def test_systemd_unit_enforces_runtime_and_single_flight():
    unit = (_ROOT / "aws" / "singcup-kr-poller.service").read_text(encoding="utf-8")
    # `RuntimeMaxSec`은 Type=oneshot에서 **무시된다**(systemd가 경고를 내고 넘어간다).
    # 그동안 실행 상한이 사실상 없었으므로 유효한 `TimeoutStartSec`으로 바꿨다.
    assert "RuntimeMaxSec=" not in unit
    assert "TimeoutStartSec=300" in unit
    assert "Type=oneshot" in unit          # 겹쳐 도는 long-running이 아니다
    assert "User=krpoller" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "ProtectSystem=strict" in unit
    assert "EnvironmentFile=/etc/krpoller/env" in unit
    # secret을 명령행 인자로 넘기지 않는다
    exec_lines = [ln for ln in unit.splitlines() if ln.startswith("ExecStart=")]
    assert exec_lines and "SECRET" not in exec_lines[0]


def test_systemd_timer_exists_and_is_not_a_tight_loop():
    timer = (_ROOT / "aws" / "singcup-kr-poller.timer").read_text(encoding="utf-8")
    assert "OnUnitActiveSec=" in timer
    seconds = {"OnUnitActiveSec=10min": 600}
    assert any(k in timer for k in seconds)


def test_poller_source_has_no_hardcoded_secret_or_clip():
    src = (_ROOT / "aws" / "kr_poller.py").read_text(encoding="utf-8")
    assert SECRET not in src
    # 특정 클립·참가자를 production 코드에 박지 않는다
    for banned in ("Qn64362ayN", "eG6SeoTtau"):
        assert banned not in src


# ── D. 내부 라우트 자체 스로틀 ─────────────────────────────────────────────
# `/api/internal/*`은 `rate_limit.py`(공개 rising API 전용)의 대상이 아니다.
# 그래서 이 통로가 스스로 상한을 갖는다. 기존 named lock(= singcup_locks 행 + TTL)을
# 재사용하므로 새 테이블이 없고 다중 replica에서도 성립한다.
def test_second_immediate_request_is_throttled(client, db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    assert _post(client).status_code == 200
    r = _post(client)
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "30"


def test_throttled_response_has_no_work_data(client, db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    _post(client)
    r = _post(client)
    for leaked in ("tasks", "clipUid", "leaseToken", "taskId", "videoId"):
        assert leaked not in r.text


def test_throttle_recovers_after_the_window(client, db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    assert _post(client).status_code == 200
    assert _post(client).status_code == 429
    # 창이 지나면 다시 열린다(named lock TTL 만료를 시각 이동으로 흉내낸다)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 31)
    assert _post(client).status_code == 200


def test_tasks_and_results_have_separate_windows(client, db, monkeypatch):
    """한 회차는 tasks 직후 results를 부른다 — 서로를 막으면 제출을 못 한다."""
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    assert _post(client).status_code == 200
    r = _post(client, path=RESULTS, payload={"results": []})
    assert r.status_code == 200


def test_throttle_disabled_when_interval_is_zero(client, db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 0)
    assert _post(client).status_code == 200
    assert _post(client).status_code == 200


def test_auth_failure_writes_no_nonce_row(client, db):
    """인증 실패가 DB 쓰기를 유발하면 secret 없는 상대가 부하를 만들 수 있다."""
    for _ in range(5):
        _post(client, sig="0" * 64)
    assert db(_nonce_count()) == 0


async def _nonce_count():
    import database
    conn = await database.get_db()
    return (await (await conn.execute(
        "SELECT COUNT(*) n FROM singcup_krp_nonce")).fetchone())["n"]


# ── E. malformed payload는 400이며 500이 아니다 ────────────────────────────
@pytest.mark.parametrize("bad", [True, False, "5", 5.5, 0, -1, 10 ** 9,
                                 None, [], {}])
def test_malformed_limit_is_400(client, db, bad):
    r = _post(client, payload={"limit": bad})
    assert r.status_code == 400
    assert "Traceback" not in r.text


def test_limit_above_batch_max_is_400(client, db):
    assert _post(client, payload={"limit": krp.BATCH_MAX + 1}).status_code == 400


@pytest.mark.parametrize("ok", [1, 5, 25])
def test_valid_limit_is_accepted(client, db, ok, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 0)
    assert _post(client, payload={"limit": ok}).status_code == 200


def test_non_object_body_is_400(client, db):
    body = b'[1,2,3]'
    assert client.post(TASKS, content=body,
                       headers=_headers(body)).status_code == 400


def test_broken_json_is_400(client, db):
    body = b'{not json'
    assert client.post(TASKS, content=body,
                       headers=_headers(body)).status_code == 400


def test_oversized_body_is_400_without_hashing_cost(client, db):
    body = b'{"limit":5,"pad":"' + b'x' * (krp.MAX_BODY_BYTES + 10) + b'"}'
    r = client.post(TASKS, content=body, headers=_headers(body))
    assert r.status_code == 400


@pytest.mark.parametrize("bad", ["x", 1, None, {}])
def test_results_must_be_a_list(client, db, bad):
    r = _post(client, path=RESULTS, payload={"results": bad})
    assert r.status_code == 400


def test_too_many_results_is_400(client, db):
    payload = {"results": [{"taskId": "a"}] * (krp.BATCH_MAX + 1)}
    assert _post(client, path=RESULTS, payload=payload).status_code == 400


def test_malformed_result_items_do_not_500(client, db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 0)
    payload = {"results": [None, "x", 5, {"taskId": 1}, {"taskId": "a" * 200}]}
    r = _post(client, path=RESULTS, payload=payload)
    assert r.status_code == 200
    assert r.json()["stored"] == 0


# ── C. Railway 측 환경변수 안전 파싱 ───────────────────────────────────────
# 이 모듈은 main.py가 import한다. 여기서 ValueError가 나면 **백엔드 전체가 뜨지
# 않는다**. 그래서 파싱 실패와 범위 이탈을 전부 기본값으로 흡수한다.
@pytest.mark.parametrize("raw", ["abc", "", "   ", "nan", "inf", "-1", "0",
                                 "1e5", "12.5", "999999999"])
def test_bad_int_env_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setenv("SINGCUP_KRP_TEST", raw)
    assert krp._int_env("SINGCUP_KRP_TEST", 25, 1, 25) == 25


def test_int_env_accepts_values_in_range(monkeypatch):
    monkeypatch.setenv("SINGCUP_KRP_TEST", "7")
    assert krp._int_env("SINGCUP_KRP_TEST", 25, 1, 25) == 7


def test_bad_env_never_prints_the_raw_value(monkeypatch, capsys):
    monkeypatch.setenv("SINGCUP_KRP_TEST", "super-secret-looking-value")
    krp._int_env("SINGCUP_KRP_TEST", 25, 1, 25)
    out = capsys.readouterr().out
    assert "super-secret-looking-value" not in out
    assert "SINGCUP_KRP_TEST" in out


def test_backend_module_imports_with_garbage_env(monkeypatch):
    """쓰레기 값이 들어와도 백엔드 기동이 죽지 않는다."""
    import importlib
    for name in ("SINGCUP_KRP_BATCH", "SINGCUP_KRP_LEASE_SECONDS",
                 "SINGCUP_KRP_SKEW_SECONDS", "SINGCUP_KRP_NONCE_TTL_SECONDS",
                 "SINGCUP_KRP_COOLDOWN_SECONDS",
                 "SINGCUP_KRP_MIN_INTERVAL_SECONDS"):
        monkeypatch.setenv(name, "abc")
    mod = importlib.reload(krp)
    try:
        assert (mod.BATCH_MAX, mod.LEASE_SECONDS, mod.SKEW_SECONDS,
                mod.COOLDOWN_SECONDS, mod.MIN_INTERVAL_SECONDS) == \
            (25, 600, 300, 3600, 30)
        assert mod.NONCE_TTL_SECONDS == 600
    finally:
        for name in ("SINGCUP_KRP_BATCH", "SINGCUP_KRP_LEASE_SECONDS",
                     "SINGCUP_KRP_SKEW_SECONDS", "SINGCUP_KRP_NONCE_TTL_SECONDS",
                     "SINGCUP_KRP_COOLDOWN_SECONDS",
                     "SINGCUP_KRP_MIN_INTERVAL_SECONDS"):
            monkeypatch.delenv(name, raising=False)
        importlib.reload(krp)


def test_nonce_ttl_is_never_shorter_than_twice_the_skew(monkeypatch):
    """ts는 -skew~+skew에서 유효하므로 같은 요청이 최대 2*skew 뒤에도 서명상
    유효할 수 있다. nonce 보관이 그보다 짧으면 그 사이에 재전송 창이 생긴다."""
    import importlib
    monkeypatch.setenv("SINGCUP_KRP_SKEW_SECONDS", "900")
    monkeypatch.setenv("SINGCUP_KRP_NONCE_TTL_SECONDS", "60")
    mod = importlib.reload(krp)
    try:
        assert mod.NONCE_TTL_SECONDS >= 2 * mod.SKEW_SECONDS
    finally:
        monkeypatch.delenv("SINGCUP_KRP_SKEW_SECONDS", raising=False)
        monkeypatch.delenv("SINGCUP_KRP_NONCE_TTL_SECONDS", raising=False)
        importlib.reload(krp)


def test_defaults_match_the_documented_ranges():
    assert krp.BATCH_MAX == 25
    assert krp.LEASE_SECONDS == 600
    assert krp.SKEW_SECONDS == 300
    assert krp.NONCE_TTL_SECONDS == 600
    assert krp.COOLDOWN_SECONDS == 3600
    assert krp.MIN_INTERVAL_SECONDS == 30


# ── B. systemd가 상태 디렉터리를 준다 ──────────────────────────────────────
def test_systemd_unit_provides_a_state_directory():
    unit = (_ROOT / "aws" / "singcup-kr-poller.service").read_text(encoding="utf-8")
    assert "StateDirectory=krpoller" in unit
    assert "StateDirectoryMode=0700" in unit
