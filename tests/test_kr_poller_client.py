"""AWS 서울 poller(클라이언트 측) — 속도·재시도·중단·비노출 계약.

이 프로세스는 서버가 아니다. 인바운드를 열지 않고, DB에 접근하지 않으며, 값을
판정하지도 않는다. 하는 일은 "한국 IP에서 치지직을 부르고 최소 필드만 돌려주는
것"뿐이다. 그래서 여기서 고정할 것은 **얼마나 조심스럽게 부르는가**와
**무엇을 남기지 않는가** 두 가지다.

배경: 조회수는 `content.vod.count`에 있고 하트는 `interaction.emotion.reactions`에
있다. 해외 IP에서는 `content.vod`가 통째로 빠지므로 조회수만 못 읽는다. 값이
0으로 온 것이 아니라 **컨테이너가 없는 것**이라, 못 읽으면 0으로 만들지 않고
partial로 보고해야 한다.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def poller(monkeypatch):
    monkeypatch.setenv("KRP_API_BASE", "https://example.invalid")
    monkeypatch.setenv("SINGCUP_KR_POLLER_SECRET", "dummy-secret-for-tests")
    monkeypatch.setenv("KRP_MAX_RETRIES", "2")
    spec = importlib.util.spec_from_file_location(
        "aws_kr_poller", _ROOT / "aws" / "kr_poller.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aws_kr_poller"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    return mod


def _task(uid="c-1"):
    return {"taskId": "t" * 32, "clipUid": uid, "videoId": "vid-1",
            "recId": "{}", "refererUid": uid, "expiresAt": 0}


def _card(views=1927, likes=146, vod=True):
    content = {"title": "t"}
    if vod:
        content["vod"] = {"count": views, "playable": True}
    return {"card": {"content": content, "interaction": {"emotion": {
        "reactions": [{"reactionType": "like", "count": likes}]}}}}


# ── 정상 관측 ──────────────────────────────────────────────────────────────
def test_observes_view_count_when_vod_present(poller, monkeypatch):
    monkeypatch.setattr(poller, "_get", lambda url, ref, timeout=None: (200, _card()))
    r = poller.observe(_task())
    assert r["viewState"] == "observed"
    assert r["viewCount"] == 1927
    assert r["heartCount"] == 146


def test_missing_vod_is_partial_not_zero(poller, monkeypatch):
    """해외 응답 모양 — `content.vod`가 없다. **0으로 만들지 않는다.**"""
    monkeypatch.setattr(poller, "_get", lambda url, ref, timeout=None: (200, _card(vod=False)))
    r = poller.observe(_task())
    assert r["viewState"] == "partial"
    assert r["viewCount"] is None
    assert r["heartCount"] == 146            # 하트는 여전히 읽힌다


def test_task_video_id_avoids_the_detail_call(poller, monkeypatch):
    """videoId가 task에 실려 오면 상세 API를 부르지 않는다(호출 1회 절약)."""
    seen = []

    def _get(url, ref, timeout=None):
        seen.append(url)
        return 200, _card()

    monkeypatch.setattr(poller, "_get", _get)
    poller.observe(_task())
    assert len(seen) == 1
    assert "/detail" not in seen[0]


# ── 30. 429 Retry-After 및 batch 중단 ──────────────────────────────────────
def test_rate_limited_stops_the_batch(poller, monkeypatch):
    calls = {"n": 0}

    def _get(url, ref, timeout=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise poller.RateLimited("30")
        return 200, _card()

    monkeypatch.setattr(poller, "_get", _get)
    monkeypatch.setattr(poller, "call_api", _fake_api(
        [_task("a"), _task("b"), _task("c")]))
    poller.run_once()
    sent = _fake_api.last_submit["results"]
    assert [r["clipUid"] for r in sent] == ["a"]      # b에서 멈췄다


def test_rate_limited_is_not_retried(poller, monkeypatch):
    """429는 bounded retry 대상이 아니다 — 곧바로 위로 올린다."""
    calls = {"n": 0}

    def _get(url, ref, timeout=None):
        calls["n"] += 1
        raise poller.RateLimited("5")

    monkeypatch.setattr(poller, "_get", _get)
    with pytest.raises(poller.RateLimited):
        poller._get_bounded("https://x", "r")
    assert calls["n"] == 1


# ── 31. 5xx / timeout bounded retry ────────────────────────────────────────
def test_5xx_is_retried_a_bounded_number_of_times(poller, monkeypatch):
    calls = {"n": 0}

    def _get(url, ref, timeout=None):
        calls["n"] += 1
        return 503, None

    monkeypatch.setattr(poller, "_get", _get)
    status, _body, attempts = poller._get_bounded("https://x", "r")
    assert status == 503
    assert calls["n"] == poller.MAX_RETRIES + 1        # 무한이 아니다
    assert attempts == poller.MAX_RETRIES + 1


def test_timeout_is_retried_a_bounded_number_of_times(poller, monkeypatch):
    calls = {"n": 0}

    def _get(url, ref, timeout=None):
        calls["n"] += 1
        raise TimeoutError("slow")

    monkeypatch.setattr(poller, "_get", _get)
    poller._get_bounded("https://x", "r")
    assert calls["n"] == poller.MAX_RETRIES + 1


def test_4xx_is_not_retried(poller, monkeypatch):
    calls = {"n": 0}

    def _get(url, ref, timeout=None):
        calls["n"] += 1
        return 404, None

    monkeypatch.setattr(poller, "_get", _get)
    poller._get_bounded("https://x", "r")
    assert calls["n"] == 1


# ── 속도·동시성 ────────────────────────────────────────────────────────────
def test_requests_are_sequential_with_a_minimum_interval(poller, monkeypatch):
    waits = []
    monkeypatch.setattr(poller.time, "sleep", lambda s: waits.append(s))
    monkeypatch.setattr(poller, "_get", lambda url, ref, timeout=None: (200, _card()))
    monkeypatch.setattr(poller, "call_api", _fake_api(
        [_task("a"), _task("b"), _task("c")]))
    poller.run_once()
    assert len(waits) == 2                    # 첫 건 앞에는 대기하지 않는다
    assert all(w >= 1.0 for w in waits)


def test_stop_flag_halts_the_batch(poller, monkeypatch):
    monkeypatch.setattr(poller, "_get", lambda url, ref, timeout=None: (200, _card()))
    monkeypatch.setattr(poller, "call_api", _fake_api([_task("a"), _task("b")]))
    poller._on_term(None, None)               # SIGTERM 흉내
    try:
        poller.run_once()
        assert _fake_api.last_submit is None  # 아무 것도 제출하지 않았다
    finally:
        poller._stop = False


# ── 32. 로그 비노출 ────────────────────────────────────────────────────────
def test_logs_carry_no_secret_url_or_body(poller, monkeypatch, capsys):
    monkeypatch.setattr(poller, "_get", lambda url, ref, timeout=None: (200, _card()))
    monkeypatch.setattr(poller, "call_api", _fake_api([_task("a")]))
    poller.run_once()
    out = capsys.readouterr().out
    assert "dummy-secret-for-tests" not in out
    assert "seedMediaId" not in out           # URL 쿼리 미노출
    assert "X-KRP-Signature" not in out
    assert "reactions" not in out             # 응답 본문 미노출
    assert "c-1" not in out or "clip_uid" in out   # clipUid만 허용


def test_misconfigured_run_does_not_print_values(poller, monkeypatch, capsys):
    monkeypatch.setattr(poller, "API_BASE", "")
    monkeypatch.setattr(poller, "SECRET", "")
    assert poller.main() == 2
    out = capsys.readouterr().out
    assert "has_secret" in out and "dummy-secret-for-tests" not in out


# ── 서명 문자열이 서버와 같다 ──────────────────────────────────────────────
def test_client_signing_string_matches_server(poller):
    import singcup_kr_poller as krp
    body = b'{"limit":25}'
    monkey = "dummy-secret-for-tests"
    assert poller._sign("1700000000", "POST", "/p", body) == krp.sign(
        monkey, "1700000000", "POST", "/p", body)


# ── 도우미 ─────────────────────────────────────────────────────────────────
def _fake_api(tasks):
    _fake_api.last_submit = None
    _fake_api.timeouts = {}

    def call(path, payload, timeout=None):
        key = "tasks" if path.endswith("/tasks") else "results"
        _fake_api.timeouts[key] = timeout
        if key == "tasks":
            return {"tasks": tasks}
        _fake_api.last_submit = payload
        return {"stored": len(payload["results"]), "accepted": 0, "rejected": []}

    return call


_fake_api.last_submit = None
_fake_api.timeouts = {}


# ── B. Retry-After 파싱 ────────────────────────────────────────────────────
def test_delta_seconds_is_honoured(poller):
    assert poller.parse_retry_after("45", 1000) == 1045


def test_http_date_is_honoured(poller):
    import email.utils
    target = 1000 + 120
    assert poller.parse_retry_after(
        email.utils.formatdate(target, usegmt=True), 1000) == target


@pytest.mark.parametrize("bad", [None, "", "abc", "-5", "0", "nan", "inf",
                                 "12.5", " ", "Mon, 99 Xxx 9999"])
def test_malformed_retry_after_falls_back_to_default(poller, bad):
    """상류가 준 값을 못 믿겠다고 곧바로 다시 두드리면 그게 더 나쁘다."""
    assert poller.parse_retry_after(bad, 1000) == 1000 + poller.RETRY_AFTER_DEFAULT


@pytest.mark.parametrize("huge", ["999999999", str(10 ** 12)])
def test_excessive_retry_after_is_capped(poller, huge):
    assert poller.parse_retry_after(huge, 1000) == 1000 + poller.RETRY_AFTER_MAX


def test_past_http_date_falls_back_to_default(poller):
    import email.utils
    past = email.utils.formatdate(500, usegmt=True)
    assert poller.parse_retry_after(past, 1000) == 1000 + poller.RETRY_AFTER_DEFAULT


# ── B. 재시작 이후에도 제한이 유지된다 ─────────────────────────────────────
def test_state_survives_process_restart(poller, tmp_path, monkeypatch):
    """oneshot이라 메모리가 남지 않는다 — 파일이 유일한 전달 수단이다."""
    monkeypatch.setenv("KRP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(poller.os.environ, "get", os_environ_get(tmp_path))
    assert poller.write_next_allowed_at(1234567890) is True
    assert poller.read_next_allowed_at() == 1234567890


def test_state_file_contains_only_the_timestamp(poller, tmp_path, monkeypatch):
    monkeypatch.setattr(poller.os.environ, "get", os_environ_get(tmp_path))
    poller.write_next_allowed_at(1234567890)
    text = (tmp_path / "state.json").read_text(encoding="utf-8")
    import json as _json
    assert set(_json.loads(text)) == {"next_allowed_at"}
    for leaked in ("http", "clip", "secret", "token", "Signature", "."):
        if leaked == ".":
            continue
        assert leaked not in text


def test_backoff_window_skips_all_api_calls(poller, tmp_path, monkeypatch):
    """제한 시각 전에는 치지직도 Railway도 부르지 않고 정상 종료한다."""
    monkeypatch.setattr(poller.os.environ, "get", os_environ_get(tmp_path))
    poller.write_next_allowed_at(int(poller.time.time()) + 300)
    called = []
    monkeypatch.setattr(poller, "call_api",
                        lambda *a, **k: called.append(a) or {"tasks": []})
    monkeypatch.setattr(poller, "_get",
                        lambda *a, **k: called.append(a) or (200, {}))
    assert poller.main() == 0
    assert called == []


def test_expired_backoff_window_allows_the_run(poller, tmp_path, monkeypatch):
    monkeypatch.setattr(poller.os.environ, "get", os_environ_get(tmp_path))
    poller.write_next_allowed_at(int(poller.time.time()) - 10)
    monkeypatch.setattr(poller, "call_api", _fake_api([]))
    assert poller.main() == 0


def test_rate_limit_persists_next_allowed_at(poller, tmp_path, monkeypatch):
    monkeypatch.setattr(poller.os.environ, "get", os_environ_get(tmp_path))

    def _get(url, ref, timeout=None):
        raise poller.RateLimited("120")

    monkeypatch.setattr(poller, "_get", _get)
    monkeypatch.setattr(poller, "call_api", _fake_api([_task("a")]))
    poller.run_once()
    saved = poller.read_next_allowed_at()
    assert saved >= int(poller.time.time()) + 110


def test_missing_state_dir_does_not_crash(poller, monkeypatch):
    monkeypatch.setattr(poller.os.environ, "get",
                        lambda k, d=None: "" if k in ("STATE_DIRECTORY",
                                                      "KRP_STATE_DIR") else d)
    assert poller.read_next_allowed_at() == 0
    assert poller.write_next_allowed_at(123) is False


def os_environ_get(tmp_path):
    real = dict()

    def get(key, default=None):
        if key == "STATE_DIRECTORY":
            return str(tmp_path)
        if key == "KRP_STATE_DIR":
            return str(tmp_path)
        return real.get(key, default)

    return get


# ── C. 환경변수 안전 파싱 ──────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["abc", "", "   ", "nan", "inf", "-1", "0",
                                 "999999", "1e5", "12.5"])
def test_bad_int_env_falls_back_to_default(poller, monkeypatch, raw):
    monkeypatch.setenv("KRP_TEST_INT", raw)
    assert poller._int_env("KRP_TEST_INT", 7, 1, 25) == 7


@pytest.mark.parametrize("raw", ["abc", "", "nan", "inf", "-inf", "-1", "0",
                                 "1000"])
def test_bad_float_env_falls_back_to_default(poller, monkeypatch, raw):
    monkeypatch.setenv("KRP_TEST_FLOAT", raw)
    assert poller._float_env("KRP_TEST_FLOAT", 1.0, 0.01, 1.0) == 1.0


def test_rate_is_never_above_one_per_second(monkeypatch, tmp_path):
    """`KRP_RATE_PER_SECOND=100`을 줘도 상류를 1초에 한 번 넘게 두드리지 않는다."""
    import importlib.util
    monkeypatch.setenv("KRP_RATE_PER_SECOND", "100")
    monkeypatch.setenv("KRP_API_BASE", "https://example.invalid")
    monkeypatch.setenv("SINGCUP_KR_POLLER_SECRET", "dummy-secret-for-tests")
    spec = importlib.util.spec_from_file_location(
        "aws_kr_poller_rate", _ROOT / "aws" / "kr_poller.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aws_kr_poller_rate"] = mod
    spec.loader.exec_module(mod)
    assert mod.RATE <= 1.0

    waits = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: waits.append(s))
    monkeypatch.setattr(mod, "_get", lambda url, ref, timeout=None: (200, _card()))
    monkeypatch.setattr(mod, "call_api", _fake_api([_task("a"), _task("b")]))
    mod.run_once()
    assert all(w >= 1.0 for w in waits)


def test_bad_env_never_prints_the_raw_value(poller, monkeypatch, capsys):
    monkeypatch.setenv("KRP_TEST_INT", "super-secret-looking-value")
    poller._int_env("KRP_TEST_INT", 7, 1, 25)
    out = capsys.readouterr().out
    assert "super-secret-looking-value" not in out
    assert "KRP_TEST_INT" in out


def test_module_import_survives_garbage_env(monkeypatch):
    import importlib.util
    for name in ("KRP_BATCH", "KRP_RATE_PER_SECOND", "KRP_MAX_RETRIES",
                 "KRP_TIMEOUT_SECONDS", "KRP_CHZZK_TIMEOUT_SECONDS",
                 "KRP_CONTROL_TIMEOUT_SECONDS", "KRP_RESULTS_TIMEOUT_SECONDS",
                 "KRP_OBSERVE_BUDGET_SECONDS"):
        monkeypatch.setenv(name, "abc")
    spec = importlib.util.spec_from_file_location(
        "aws_kr_poller_garbage", _ROOT / "aws" / "kr_poller.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aws_kr_poller_garbage"] = mod
    spec.loader.exec_module(mod)                 # 예외 없이 뜬다
    assert (mod.BATCH, mod.RATE, mod.MAX_RETRIES) == (25, 1.0, 2)
    assert (mod.CHZZK_TIMEOUT, mod.CONTROL_TIMEOUT) == (10.0, 10.0)
    # 180은 요청값이고 실제 예산은 `/tasks` 재시도 몫을 뺀 100이다(조합 clamp).
    assert (mod.RESULTS_TIMEOUT, mod.OBSERVE_BUDGET) == (60.0, 100.0)


# ── A. 결과에 leaseToken이 실린다 ──────────────────────────────────────────
def test_results_carry_the_lease_token(poller, monkeypatch):
    monkeypatch.setattr(poller, "_get", lambda url, ref, timeout=None: (200, _card()))
    t = _task("a")
    t["leaseToken"] = "tok-123"
    monkeypatch.setattr(poller, "call_api", _fake_api([t]))
    poller.run_once()
    assert _fake_api.last_submit["results"][0]["leaseToken"] == "tok-123"


def test_partial_result_also_carries_the_lease_token(poller, monkeypatch):
    monkeypatch.setattr(poller, "_get", lambda url, ref, timeout=None: (200, _card(vod=False)))
    t = _task("a")
    t["leaseToken"] = "tok-456"
    monkeypatch.setattr(poller, "call_api", _fake_api([t]))
    poller.run_once()
    r = _fake_api.last_submit["results"][0]
    assert r["leaseToken"] == "tok-456" and r["viewState"] == "partial"
