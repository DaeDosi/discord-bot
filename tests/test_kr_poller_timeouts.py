"""AWS 서울 poller — **상대별 제한 시간** 계약.

첫 운영 회차가 죽은 자리다. 치지직 조회 25건은 전부 200으로 성공했는데
마지막 `POST /results` 응답을 기다리다 공통 10초를 넘겨 `TimeoutError`가 났고,
systemd는 `ExecMainStatus=1`로 끝냈다. 결과 endpoint는 25건을 순차 검증하며
clip lock을 잡고 DB에 반영한 뒤 `recompute_ranking()`까지 돌린다 — 조회 한 건과
시간 규모가 다르다.

그래서 여기서 고정하는 것은 두 가지다.

1. **결과 제출만** 긴 제한을 쓴다. 치지직 제한을 같이 늘리면 응답 없는 상류
   하나가 회차를 잡아먹고 그 시간이 `_get_bounded`의 재시도와 곱해진다.
2. 관측 단계에 예산이 있어 **결과를 제출하지 못한 채 죽지 않는다.**
   예산이 끝나면 그때까지 모은 결과를 제출하고 정상 종료한다.
"""
import importlib.util
import io
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SERVICE = _ROOT / "aws" / "singcup-kr-poller.service"


def _load(monkeypatch, name="aws_kr_poller_to", **env):
    monkeypatch.setenv("KRP_API_BASE", "https://example.invalid")
    monkeypatch.setenv("SINGCUP_KR_POLLER_SECRET", "dummy-secret-for-tests")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(
        name, _ROOT / "aws" / "kr_poller.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    return mod


@pytest.fixture
def poller(monkeypatch):
    for n in ("KRP_TIMEOUT_SECONDS", "KRP_CHZZK_TIMEOUT_SECONDS",
              "KRP_CONTROL_TIMEOUT_SECONDS", "KRP_RESULTS_TIMEOUT_SECONDS",
              "KRP_OBSERVE_BUDGET_SECONDS"):
        monkeypatch.delenv(n, raising=False)
    return _load(monkeypatch)


def _task(uid="c-1"):
    return {"taskId": "t" * 32, "clipUid": uid, "videoId": "vid-1",
            "recId": "{}", "refererUid": uid, "expiresAt": 0,
            "leaseToken": "tok"}


def _card(views=1927, likes=146):
    return {"card": {"content": {"title": "t",
                                 "vod": {"count": views, "playable": True}},
                     "interaction": {"emotion": {"reactions": [
                         {"emojiId": "like", "count": likes}]}}}}


class _Resp:
    def __init__(self, body=b'{"tasks": []}'):
        self._b = body

    def read(self):
        return self._b

    def getcode(self):
        return 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── 1. 기본값 ──────────────────────────────────────────────────────────────
def test_defaults_are_split_and_safe(poller):
    """환경변수를 하나도 주지 않아도 안전한 기본값으로 뜬다."""
    assert poller.CHZZK_TIMEOUT == 10.0
    assert poller.CONTROL_TIMEOUT == 10.0
    assert poller.RESULTS_TIMEOUT == 60.0
    # `/tasks` 503 재시도 예산(최대 3회 × CONTROL 10초 + 대기 60초)을 확보하느라
    # 관측 예산이 180 → 100으로 잘린다. 25건 관측에는 rate 간격 24초 + 응답이면
    # 충분하고, 불변식(합계 ≤ TimeoutStartSec 300)이 유지된다.
    assert poller.OBSERVE_BUDGET == 100.0


def test_results_limit_is_longer_than_the_chzzk_limit(poller):
    """이 부등식이 깨지면 사고가 그대로 재현된다."""
    assert poller.RESULTS_TIMEOUT > poller.CHZZK_TIMEOUT
    assert poller.RESULTS_TIMEOUT > poller.CONTROL_TIMEOUT


# ── 2. tasks와 results가 서로 다른 제한을 쓴다 ─────────────────────────────
def test_tasks_and_results_use_different_timeouts(poller, monkeypatch):
    seen = {}

    def call(path, payload, timeout=None):
        seen["tasks" if path.endswith("/tasks") else "results"] = timeout
        if path.endswith("/tasks"):
            return {"tasks": [_task("a")]}
        return {"stored": 1, "accepted": 1, "rejected": []}

    monkeypatch.setattr(poller, "_get", lambda url, ref: (200, _card()))
    monkeypatch.setattr(poller, "call_api", call)
    poller.run_once()
    assert seen["tasks"] == poller.CONTROL_TIMEOUT
    assert seen["results"] == poller.RESULTS_TIMEOUT
    assert seen["tasks"] != seen["results"]


def test_chzzk_requests_keep_the_short_limit(poller, monkeypatch):
    """치지직 조회는 기존 10초 수준을 유지한다 — 여기를 늘리면 최악 시간이 커진다."""
    seen = []

    def _urlopen(req, timeout=None, context=None):
        seen.append(timeout)
        return _Resp(b'{"card": {}}')

    monkeypatch.setattr(poller.urllib.request, "urlopen", _urlopen)
    poller._get("https://api.chzzk.naver.com/x", "https://chzzk.naver.com/clips/a")
    assert seen == [poller.CHZZK_TIMEOUT]
    assert seen[0] == 10.0


def test_call_api_defaults_to_the_control_limit(poller, monkeypatch):
    seen = []

    def _urlopen(req, timeout=None, context=None):
        seen.append(timeout)
        return _Resp()

    monkeypatch.setattr(poller.urllib.request, "urlopen", _urlopen)
    poller.call_api(poller.TASKS_PATH, {"limit": 25})
    assert seen == [poller.CONTROL_TIMEOUT]


def test_call_api_honours_an_explicit_timeout(poller, monkeypatch):
    seen = []

    def _urlopen(req, timeout=None, context=None):
        seen.append(timeout)
        return _Resp()

    monkeypatch.setattr(poller.urllib.request, "urlopen", _urlopen)
    poller.call_api(poller.RESULTS_PATH, {"results": []}, timeout=77.0)
    assert seen == [77.0]


# ── 3. 환경변수 경계값 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["abc", "", "   ", "0", "-1", "-0.5",
                                 "nan", "NaN", "inf", "Infinity", "-inf",
                                 "1e9", "99999", "None", "null"])
def test_bad_results_timeout_falls_back_to_the_default(monkeypatch, raw):
    """0·음수·NaN·Infinity·과대값 모두 기본값으로 떨어진다(기동은 죽지 않는다)."""
    mod = _load(monkeypatch, name="aws_kr_poller_to_bad_%s" % abs(hash(raw)),
                KRP_RESULTS_TIMEOUT_SECONDS=raw)
    assert mod.RESULTS_TIMEOUT == 60.0


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "nan", "inf", "1e9"])
def test_bad_observe_budget_falls_back_to_the_default(monkeypatch, raw):
    mod = _load(monkeypatch, name="aws_kr_poller_to_bud_%s" % abs(hash(raw)),
                KRP_OBSERVE_BUDGET_SECONDS=raw)
    assert mod.OBSERVE_BUDGET == 100.0      # 기본값 180이 조합 clamp로 130이 된다


@pytest.mark.parametrize("raw", ["abc", "0", "-1", "nan", "inf", "1e9", "61"])
def test_bad_chzzk_timeout_falls_back_to_the_default(monkeypatch, raw):
    mod = _load(monkeypatch, name="aws_kr_poller_to_ch_%s" % abs(hash(raw)),
                KRP_CHZZK_TIMEOUT_SECONDS=raw)
    assert mod.CHZZK_TIMEOUT == 10.0


@pytest.mark.parametrize("raw,expected", [("5", 5.0), ("180", 180.0),
                                          ("60", 60.0), ("12.5", 12.5)])
def test_in_range_results_timeout_is_honoured(monkeypatch, raw, expected):
    mod = _load(monkeypatch, name="aws_kr_poller_to_ok_%s" % raw.replace(".", "_"),
                KRP_RESULTS_TIMEOUT_SECONDS=raw)
    assert mod.RESULTS_TIMEOUT == expected


@pytest.mark.parametrize("raw", ["4.9", "180.1", "181"])
def test_results_timeout_bounds_are_enforced(monkeypatch, raw):
    """상·하한 밖은 조용히 기본값이 된다 — 5초 미만은 사고 재현, 180초 초과는
    systemd 상한(300초)과 lease(600초) 계산을 깬다."""
    mod = _load(monkeypatch, name="aws_kr_poller_to_b_%s" % raw.replace(".", "_"),
                KRP_RESULTS_TIMEOUT_SECONDS=raw)
    assert mod.RESULTS_TIMEOUT == 60.0


# ── 4. 구 이름 하위호환 ────────────────────────────────────────────────────
def test_legacy_env_still_sets_chzzk_and_control(monkeypatch):
    """이미 배포된 env 파일이 `KRP_TIMEOUT_SECONDS`를 갖고 있어도 동작한다."""
    mod = _load(monkeypatch, name="aws_kr_poller_to_legacy",
                KRP_TIMEOUT_SECONDS="7")
    assert mod.CHZZK_TIMEOUT == 7.0
    assert mod.CONTROL_TIMEOUT == 7.0


def test_legacy_env_never_shortens_the_results_limit(monkeypatch):
    """구 이름이 결과 제출까지 10초로 되돌리면 사고가 그대로 재현된다."""
    mod = _load(monkeypatch, name="aws_kr_poller_to_legacy2",
                KRP_TIMEOUT_SECONDS="10")
    assert mod.RESULTS_TIMEOUT == 60.0


def test_explicit_names_win_over_the_legacy_name(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_to_legacy3",
                KRP_TIMEOUT_SECONDS="7", KRP_CHZZK_TIMEOUT_SECONDS="12")
    assert mod.CHZZK_TIMEOUT == 12.0
    assert mod.CONTROL_TIMEOUT == 7.0


# ── 5. 비노출 ──────────────────────────────────────────────────────────────
def test_bad_timeout_env_never_prints_the_raw_value(poller, monkeypatch, capsys):
    monkeypatch.setenv("KRP_RESULTS_TIMEOUT_SECONDS", "secret-looking-garbage")
    poller._float_env("KRP_RESULTS_TIMEOUT_SECONDS", 60.0, 5.0, 180.0)
    out = capsys.readouterr().out
    assert "secret-looking-garbage" not in out
    assert "KRP_RESULTS_TIMEOUT_SECONDS" in out


def test_results_timeout_failure_leaks_nothing(poller, monkeypatch, capsys):
    """제출이 timeout으로 죽어도 secret·서명·nonce·본문은 남지 않는다."""
    monkeypatch.setattr(poller, "_get", lambda url, ref: (200, _card()))

    def call(path, payload, timeout=None):
        if path.endswith("/tasks"):
            return {"tasks": [_task("a")]}
        raise TimeoutError("timed out")

    monkeypatch.setattr(poller, "call_api", call)
    assert poller.main() == 1
    out = capsys.readouterr().out
    assert "krp_failed" in out and "TimeoutError" in out
    assert "dummy-secret-for-tests" not in out
    for banned in ("X-KRP-Signature", "signature", "nonce", "leaseToken", "tok"):
        assert banned not in out


# ── 6. 관측 예산 ───────────────────────────────────────────────────────────
def test_budget_exhaustion_still_submits_what_was_observed(monkeypatch):
    """예산이 끝나면 **제출하고** 끝낸다 — 관측을 통째로 버리지 않는다."""
    mod = _load(monkeypatch, name="aws_kr_poller_to_budget",
                KRP_OBSERVE_BUDGET_SECONDS="30")
    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    submitted = {}

    def call(path, payload, timeout=None):
        if path.endswith("/tasks"):
            return {"tasks": [_task("a"), _task("b"), _task("c")]}
        submitted["payload"] = payload
        return {"stored": 1, "accepted": 1, "rejected": []}

    def _get(url, ref, timeout=None):
        clock["t"] += 20.0                       # 2건째에서 예산을 넘긴다
        return 200, _card()

    monkeypatch.setattr(mod, "_get", _get)
    monkeypatch.setattr(mod, "call_api", call)
    mod.run_once()
    assert submitted, "예산 소진 후에도 결과는 제출돼야 한다"
    assert len(submitted["payload"]["results"]) < 3


def test_budget_exhaustion_is_logged_without_task_data(monkeypatch, capsys):
    mod = _load(monkeypatch, name="aws_kr_poller_to_budget2",
                KRP_OBSERVE_BUDGET_SECONDS="30")
    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    def _get(url, ref, timeout=None):
        clock["t"] += 40.0
        return 200, _card()

    monkeypatch.setattr(mod, "_get", _get)
    monkeypatch.setattr(mod, "call_api", lambda p, pl, timeout=None: (
        {"tasks": [_task("a"), _task("b")]} if p.endswith("/tasks")
        else {"stored": 1, "accepted": 1, "rejected": []}))
    mod.run_once()
    out = capsys.readouterr().out
    assert "krp_budget_exhausted" in out
    assert "tok" not in out                      # leaseToken은 로그에 없다


def test_full_batch_fits_inside_the_budget_and_the_unit_limit(poller):
    """최악 시간 = 관측 예산 + 결과 제출 < systemd 상한 < 서버 lease."""
    worst = poller.OBSERVE_BUDGET + poller.RESULTS_TIMEOUT
    unit_limit = 300                             # TimeoutStartSec
    lease_seconds = 600                          # SINGCUP_KRP_LEASE_SECONDS 기본
    assert worst < unit_limit < lease_seconds


# ── 7. systemd 유닛 계약 ───────────────────────────────────────────────────
def test_unit_does_not_use_runtimemaxsec_with_oneshot():
    """`RuntimeMaxSec`은 Type=oneshot에서 무시된다 — 상한이 없는 것과 같았다."""
    text = _SERVICE.read_text(encoding="utf-8")
    assert "Type=oneshot" in text
    assert "RuntimeMaxSec=" not in text


def test_unit_applies_a_real_start_limit():
    text = _SERVICE.read_text(encoding="utf-8")
    assert "TimeoutStartSec=300" in text
    assert "TimeoutStopSec=20" in text


def test_unit_keeps_its_hardening():
    text = _SERVICE.read_text(encoding="utf-8")
    for line in ("User=krpoller", "EnvironmentFile=/etc/krpoller/env",
                 "StateDirectory=krpoller", "StateDirectoryMode=0700",
                 "NoNewPrivileges=yes", "PrivateTmp=yes",
                 "ProtectSystem=strict", "ProtectHome=yes",
                 "ProtectKernelTunables=yes", "ProtectControlGroups=yes",
                 "RestrictSUIDSGID=yes", "LockPersonality=yes",
                 "MemoryDenyWriteExecute=yes"):
        assert line in text


def test_unit_start_limit_exceeds_the_worst_case(poller):
    text = _SERVICE.read_text(encoding="utf-8")
    limit = int(text.split("TimeoutStartSec=")[1].split()[0])
    assert limit > poller.OBSERVE_BUDGET + poller.RESULTS_TIMEOUT


# ── 8. 회귀 — 변경 금지 상수 ───────────────────────────────────────────────
def test_batch_and_rate_are_unchanged(poller):
    """이번 핫픽스는 속도·배치를 건드리지 않는다."""
    assert poller.BATCH == 25
    assert poller.RATE == 1.0
    assert poller.MAX_RETRIES == 2


# ══════════════════════════════════════════════════════════════════════════
# 하드 예산 — 이미 시작된 observe가 예산을 넘기지 못한다
#
# 소프트 예산("task 시작 전에만 확인")은 상한이 아니다. 마지막 task가 deadline
# 직전에 시작하면 그 안에서 detail 3회 + card 3회 + backoff가 통째로 더 돈다.
# videoId가 없는 클립은 두 경로를 모두 거치므로 초과분이 가장 크다.
# wall clock 변경에 흔들리지 않도록 예산은 `time.monotonic()`으로 잰다.
# ══════════════════════════════════════════════════════════════════════════
class _Clock:
    """가짜 단조 시계. 실제로 자지 않고 호출마다 시간을 밀어 준다."""

    def __init__(self, start=1000.0):
        self.t = start

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


def _slow(mod, monkeypatch, clock, per_call):
    """모든 외부 요청이 주어진 timeout을 **끝까지** 쓰고 실패하는 상황."""
    seen = []

    def _get(url, ref, timeout=None):
        seen.append(timeout)
        clock.t += (per_call if timeout is None else min(per_call, timeout))
        raise TimeoutError("slow upstream")

    monkeypatch.setattr(mod, "_get", _get)
    return seen


def _wire(mod, monkeypatch, clock):
    monkeypatch.setattr(mod.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(mod.time, "sleep", clock.sleep)


def _task_no_video(uid="nv-1"):
    t = _task(uid)
    t["videoId"] = ""                            # detail 경로를 강제한다
    return t


# ── 9. videoId 없는 클립의 최악 경로 ───────────────────────────────────────
def test_observe_without_video_id_respects_the_deadline(poller, monkeypatch):
    """detail 3회 + backoff + card 3회 + backoff가 예산을 넘기지 못한다."""
    clock = _Clock()
    _wire(poller, monkeypatch, clock)
    _slow(poller, monkeypatch, clock, per_call=poller.CHZZK_TIMEOUT)
    deadline = clock.monotonic() + 25.0
    with pytest.raises(poller.BudgetExhausted):
        poller.observe(_task_no_video(), deadline)
    assert clock.monotonic() <= deadline + 0.001


def test_in_flight_timeout_is_clamped_to_the_remaining_budget(poller, monkeypatch):
    """남은 시간이 3초면 요청 timeout도 3초여야 한다 — 10초를 그대로 쓰면 초과한다."""
    clock = _Clock()
    _wire(poller, monkeypatch, clock)
    seen = _slow(poller, monkeypatch, clock, per_call=poller.CHZZK_TIMEOUT)
    deadline = clock.monotonic() + 3.0
    with pytest.raises(poller.BudgetExhausted):
        poller._get_bounded("https://x", "https://r", deadline)
    assert seen and max(seen) <= 3.0
    assert clock.monotonic() <= deadline + 0.001


def test_backoff_never_sleeps_past_the_deadline(poller, monkeypatch):
    clock = _Clock()
    _wire(poller, monkeypatch, clock)
    monkeypatch.setattr(poller, "_get",
                        lambda url, ref, timeout=None: (500, None))
    deadline = clock.monotonic() + 1.5
    with pytest.raises(poller.BudgetExhausted):
        poller._get_bounded("https://x", "https://r", deadline)
    assert clock.monotonic() <= deadline + 0.001


def test_no_new_request_starts_without_remaining_budget(poller, monkeypatch):
    clock = _Clock()
    _wire(poller, monkeypatch, clock)
    seen = _slow(poller, monkeypatch, clock, per_call=poller.CHZZK_TIMEOUT)
    with pytest.raises(poller.BudgetExhausted):
        poller._get_bounded("https://x", "https://r", clock.monotonic())
    assert seen == []                            # 요청을 아예 시작하지 않았다


# ── 10. 회차 전체가 하드 예산 안에 들어온다 ────────────────────────────────
def test_full_cycle_never_exceeds_the_observe_budget(monkeypatch):
    """마지막 task가 deadline 직전에 시작해도 회차 관측이 예산을 넘지 않는다."""
    mod = _load(monkeypatch, name="aws_kr_poller_hard_cycle",
                KRP_OBSERVE_BUDGET_SECONDS="30")
    clock = _Clock()
    _wire(mod, monkeypatch, clock)
    _slow(mod, monkeypatch, clock, per_call=mod.CHZZK_TIMEOUT)
    start = clock.monotonic()

    def call(path, payload, timeout=None):
        if path.endswith("/tasks"):
            return {"tasks": [_task_no_video("a"), _task_no_video("b"),
                              _task_no_video("c"), _task_no_video("d")]}
        return {"stored": 0, "accepted": 0, "rejected": []}

    monkeypatch.setattr(mod, "call_api", call)
    mod.run_once()
    assert clock.monotonic() - start <= mod.OBSERVE_BUDGET + 0.001


def test_rate_sleep_does_not_overrun_the_deadline(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_hard_rate",
                KRP_OBSERVE_BUDGET_SECONDS="30")
    clock = _Clock()
    _wire(mod, monkeypatch, clock)
    start = clock.monotonic()

    def _get(url, ref, timeout=None):
        clock.t += 9.0
        return 200, _card()

    monkeypatch.setattr(mod, "_get", _get)
    monkeypatch.setattr(mod, "call_api", lambda p, pl, timeout=None: (
        {"tasks": [_task("a"), _task("b"), _task("c"), _task("d"), _task("e")]}
        if p.endswith("/tasks") else {"stored": 1, "accepted": 1, "rejected": []}))
    mod.run_once()
    assert clock.monotonic() - start <= mod.OBSERVE_BUDGET + 0.001


# ── 11. 예산이 끝나도 이미 관측한 것은 제출한다 ────────────────────────────
def test_partial_batch_is_submitted_after_budget_exhaustion(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_hard_submit",
                KRP_OBSERVE_BUDGET_SECONDS="30")
    clock = _Clock()
    _wire(mod, monkeypatch, clock)
    calls = {"n": 0}
    submitted = {}

    def _get(url, ref, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            clock.t += 5.0
            return 200, _card()
        clock.t += (timeout or mod.CHZZK_TIMEOUT)
        raise TimeoutError("slow")

    def call(path, payload, timeout=None):
        if path.endswith("/tasks"):
            return {"tasks": [_task("a"), _task("b"), _task("c"), _task("d")]}
        submitted["payload"] = payload
        return {"stored": 2, "accepted": 2, "rejected": []}

    monkeypatch.setattr(mod, "_get", _get)
    monkeypatch.setattr(mod, "call_api", call)
    mod.run_once()
    assert submitted, "예산이 끝나도 이미 관측한 결과는 제출한다"
    assert len(submitted["payload"]["results"]) >= 1


def test_budget_exhaustion_is_not_recorded_as_a_zero_view(monkeypatch):
    """예산 종료를 조회수 0이나 정상 관측으로 기록하면 unknown이 굳어 버린다."""
    mod = _load(monkeypatch, name="aws_kr_poller_hard_zero",
                KRP_OBSERVE_BUDGET_SECONDS="30")
    clock = _Clock()
    _wire(mod, monkeypatch, clock)
    _slow(mod, monkeypatch, clock, per_call=mod.CHZZK_TIMEOUT)
    submitted = {"payload": {"results": []}}

    def call(path, payload, timeout=None):
        if path.endswith("/tasks"):
            return {"tasks": [_task("a"), _task("b")]}
        submitted["payload"] = payload
        return {"stored": 0, "accepted": 0, "rejected": []}

    monkeypatch.setattr(mod, "call_api", call)
    mod.run_once()
    for r in submitted["payload"]["results"]:
        assert r["viewCount"] != 0               # 0으로 만들지 않는다
        assert r["viewState"] != "observed"


# ── 12. 결과 제출 예산은 관측과 분리돼 있다 ────────────────────────────────
def test_results_submission_gets_its_own_full_budget(monkeypatch):
    """관측이 예산을 다 써도 제출에는 RESULTS_TIMEOUT 전체가 주어진다."""
    mod = _load(monkeypatch, name="aws_kr_poller_hard_split",
                KRP_OBSERVE_BUDGET_SECONDS="30")
    clock = _Clock()
    _wire(mod, monkeypatch, clock)
    seen = {}

    def _get(url, ref, timeout=None):
        clock.t += 29.0
        return 200, _card()

    def call(path, payload, timeout=None):
        seen["results" if path.endswith("/results") else "tasks"] = timeout
        if path.endswith("/tasks"):
            return {"tasks": [_task("a"), _task("b")]}
        return {"stored": 1, "accepted": 1, "rejected": []}

    monkeypatch.setattr(mod, "_get", _get)
    monkeypatch.setattr(mod, "call_api", call)
    mod.run_once()
    assert seen["results"] == mod.RESULTS_TIMEOUT   # 남은 시간으로 깎이지 않는다


# ── 13. 모든 허용 조합에서 불변식이 성립한다 ───────────────────────────────
@pytest.mark.parametrize("control,results,budget", [
    ("1", "5", "30"), ("60", "180", "600"), ("60", "180", "30"),
    ("1", "180", "600"), ("60", "5", "600"), ("10", "60", "180"),
])
def test_every_allowed_combination_fits_the_unit_and_lease(monkeypatch, control,
                                                           results, budget):
    """개별 범위만 검사하고 조합을 방치하면 유닛 상한과 lease를 넘긴다."""
    mod = _load(monkeypatch,
                name="aws_kr_poller_combo_%s_%s_%s" % (control, results, budget),
                KRP_CONTROL_TIMEOUT_SECONDS=control,
                KRP_RESULTS_TIMEOUT_SECONDS=results,
                KRP_OBSERVE_BUDGET_SECONDS=budget)
    worst = (mod.CONTROL_TIMEOUT + mod.OBSERVE_BUDGET + mod.RESULTS_TIMEOUT
             + mod.SAFETY_MARGIN)
    assert worst <= mod.UNIT_START_LIMIT
    assert mod.UNIT_START_LIMIT < mod.LEASE_SECONDS_HINT
    assert mod.OBSERVE_BUDGET > 0


def test_extreme_combination_clamps_the_observe_budget(monkeypatch):
    """상한을 다 올린 조합에서는 관측 예산이 잘려 나간다(조용히 넘기지 않는다)."""
    mod = _load(monkeypatch, name="aws_kr_poller_combo_clamp",
                KRP_CONTROL_TIMEOUT_SECONDS="60",
                KRP_RESULTS_TIMEOUT_SECONDS="180",
                KRP_OBSERVE_BUDGET_SECONDS="600")
    assert mod.OBSERVE_BUDGET < 600
    assert (mod.CONTROL_TIMEOUT + mod.OBSERVE_BUDGET + mod.RESULTS_TIMEOUT
            + mod.SAFETY_MARGIN) <= mod.UNIT_START_LIMIT


def test_default_combination_keeps_the_intended_budget(poller):
    """기본값 조합에서 관측 예산이 100초로 확정된다(과도한 clamp 금지)."""
    assert poller.OBSERVE_BUDGET == 100.0
    assert poller.OBSERVE_BUDGET > 60.0     # 25건 관측에 충분하다


def test_unit_limit_constant_matches_the_service_file(poller):
    """코드의 상한과 유닛 파일이 어긋나면 계산이 거짓말이 된다."""
    text = _SERVICE.read_text(encoding="utf-8")
    assert "TimeoutStartSec=%d" % int(poller.UNIT_START_LIMIT) in text


# ── 14. 예산 로그 비노출 ───────────────────────────────────────────────────
def test_budget_log_carries_no_identifiers(monkeypatch, capsys):
    mod = _load(monkeypatch, name="aws_kr_poller_hard_log",
                KRP_OBSERVE_BUDGET_SECONDS="30")
    clock = _Clock()
    _wire(mod, monkeypatch, clock)
    _slow(mod, monkeypatch, clock, per_call=mod.CHZZK_TIMEOUT)
    monkeypatch.setattr(mod, "call_api", lambda p, pl, timeout=None: (
        {"tasks": [_task("qn-secret-uid"), _task("b")]} if p.endswith("/tasks")
        else {"stored": 0, "accepted": 0, "rejected": []}))
    mod.run_once()
    out = capsys.readouterr().out
    assert "krp_budget_exhausted" in out
    line = [ln for ln in out.splitlines() if "krp_budget_exhausted" in ln][0]
    for banned in ("qn-secret-uid", "t" * 32, "tok",
                   "dummy-secret-for-tests", "signature", "nonce"):
        assert banned not in line


# ══════════════════════════════════════════════════════════════════════════
# `/tasks` 503 제한 재시도 — nonce write lock 경합 대응
#
# 실측(2026-08-05 UTC): 03:24:37 / 03:25:21 / 03:36:22 세 번 모두
# `db_locked_giveup what=krp_nonce attempts=2 budgetSeconds=0.8
# detail="database is locked"` → `krp_nonce_db_busy` → `/tasks` 503.
# 같은 시각 일반 collector도 discover/recheck/deletion이 database_locked로
# 실패했다(collector_cycle_done success 2 / failed 3 / skipped 1) — KRP만의
# 문제가 아니라 **전역 SQLite 쓰기 경합**이다.
#
# nonce가 기록되지 않았으므로 lease도 발급되지 않았다 → 데이터 손상·중복 0건.
# 그래서 여기서는 **일시적 503만** 짧게 다시 두드린다. 근본 원인(장기 write
# lock 보유자)은 별도 감사 대상이고, busy timeout 상향이나 nonce 우회로 덮지
# 않는다.
# ══════════════════════════════════════════════════════════════════════════
class _Resp503(Exception):
    pass


def _http_error(mod, code, retry_after=None):
    hdrs = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return mod.urllib.error.HTTPError("u", code, "e", hdrs, None)


def _tasks_seq(mod, monkeypatch, outcomes):
    """`call_api`를 순서대로 흉내낸다. 각 호출의 nonce/서명을 기록한다."""
    seen = {"nonces": [], "sigs": [], "n": 0}
    real_sign = mod._sign

    def _call(path, payload, timeout=None):
        if not path.endswith("/tasks"):
            return {"stored": 0, "accepted": 0, "rejected": []}
        i = seen["n"]
        seen["n"] += 1
        ts = str(int(mod.time.time()))
        nonce = mod.hashlib.sha256(mod.os.urandom(32)).hexdigest()[:32]
        seen["nonces"].append(nonce)
        seen["sigs"].append(real_sign(ts, "POST", path, b"{}"))
        out = outcomes[min(i, len(outcomes) - 1)]
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(mod, "call_api", _call)
    return seen


def test_503_with_retry_after_is_retried_and_then_succeeds(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_r1")
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    seen = _tasks_seq(mod, monkeypatch, [_http_error(mod, 503, 2), {"tasks": []}])
    assert mod.run_once() == 0
    assert seen["n"] == 2
    assert clock["t"] >= 2


def test_two_503s_then_success(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_r2")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    seen = _tasks_seq(mod, monkeypatch,
                      [_http_error(mod, 503, 1), _http_error(mod, 503, 1), {"tasks": []}])
    mod.run_once()
    assert seen["n"] == 3


def test_every_attempt_uses_a_fresh_nonce_and_signature(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_r3")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    seen = _tasks_seq(mod, monkeypatch,
                      [_http_error(mod, 503, 1), _http_error(mod, 503, 1), {"tasks": []}])
    mod.run_once()
    assert len(seen["nonces"]) == 3
    assert len(set(seen["nonces"])) == 3          # 같은 nonce 재전송 금지


@pytest.mark.parametrize("ra", [None, "abc", "-5", "0", "99999"])
def test_bad_retry_after_fails_closed(monkeypatch, ra):
    """Retry-After가 없거나 비정상·음수·과도하면 재시도하지 않는다."""
    mod = _load(monkeypatch, name="aws_kr_poller_r4_%s" % (ra or "none"))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    seen = _tasks_seq(mod, monkeypatch, [_http_error(mod, 503, ra)])
    assert mod.main() == 1
    assert seen["n"] == 1


@pytest.mark.parametrize("code", [400, 401, 403, 409, 429])
def test_other_status_codes_are_never_retried(monkeypatch, code):
    mod = _load(monkeypatch, name="aws_kr_poller_r5_%d" % code)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    seen = _tasks_seq(mod, monkeypatch, [_http_error(mod, code, 1)])
    assert mod.main() == 1
    assert seen["n"] == 1


def test_exhausted_retries_exit_one_and_never_fake_success(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_r6")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    seen = _tasks_seq(mod, monkeypatch, [_http_error(mod, 503, 1)])
    assert mod.main() == 1                        # 빈 작업/성공 위장 금지
    assert seen["n"] == mod.TASKS_RETRY_MAX + 1


def test_retry_budget_is_bounded_and_fits_the_unit_limit(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_r7")
    worst = (mod.CONTROL_TIMEOUT * (mod.TASKS_RETRY_MAX + 1)
             + mod.TASKS_RETRY_BUDGET + mod.OBSERVE_BUDGET
             + mod.RESULTS_TIMEOUT + mod.SAFETY_MARGIN)
    assert worst <= mod.UNIT_START_LIMIT
    assert mod.UNIT_START_LIMIT < mod.LEASE_SECONDS_HINT


def test_retry_log_carries_no_sensitive_fields(monkeypatch, capsys):
    mod = _load(monkeypatch, name="aws_kr_poller_r8")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    _tasks_seq(mod, monkeypatch, [_http_error(mod, 503, 1), {"tasks": []}])
    mod.run_once()
    out = capsys.readouterr().out
    assert "krp_tasks_retry" in out
    for banned in ("dummy-secret-for-tests", "signature", "X-KRP", "nonce",
                   "Authorization"):
        assert banned not in out


def test_successful_retry_keeps_the_tasks_observe_submit_contract(monkeypatch):
    mod = _load(monkeypatch, name="aws_kr_poller_r9")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    submitted = {}

    def _call(path, payload, timeout=None):
        if path.endswith("/tasks"):
            if not submitted.get("first"):
                submitted["first"] = True
                raise _http_error(mod, 503, 1)
            return {"tasks": [_task("a")]}
        submitted["payload"] = payload
        return {"stored": 1, "accepted": 1, "rejected": []}

    monkeypatch.setattr(mod, "call_api", _call)
    monkeypatch.setattr(mod, "_get", lambda u, r, timeout=None: (200, _card()))
    mod.run_once()
    assert len(submitted["payload"]["results"]) == 1


# ══════════════════════════════════════════════════════════════════════════
# 회차 하드 상한 — 전역 monotonic deadline
#
# 예전에는 각 단계 최악값의 **합**이 곧 상한이었고, 그 합이 systemd
# `TimeoutStartSec=300`과 **정확히 같았다**. 여유가 0이면 프로세스 기동·인터프리터
# 로드·JSON 직렬화·systemd 스케줄링 오차만으로 정상 실행도 강제 종료된다.
# 이제 `RUN_BUDGET`(= 300 − SAFETY_MARGIN 50 = 250초) 하나가 상한이고, 모든
# 단계가 거기서 남은 시간을 받아 쓴다.
# ══════════════════════════════════════════════════════════════════════════
def test_hard_ceiling_is_clearly_below_the_systemd_limit(poller):
    """① 모든 단계가 최악이어도 하드 상한이 300초보다 **명확히** 작다."""
    assert poller.RUN_BUDGET == 250.0
    assert poller.RUN_BUDGET < poller.UNIT_START_LIMIT
    assert poller.UNIT_START_LIMIT - poller.RUN_BUDGET >= 30.0


def test_safety_margin_is_explicit_and_at_least_thirty_seconds(poller):
    """② 안전 여유가 코드 상수로 명시돼 있다."""
    assert poller.SAFETY_MARGIN == 50.0
    assert poller.SAFETY_MARGIN >= 30.0


def test_stage_worst_cases_fit_inside_the_run_budget(poller):
    """단계 최악값의 합조차 RUN_BUDGET을 넘지 않는다."""
    worst = (poller.CONTROL_TIMEOUT * (poller.TASKS_RETRY_MAX + 1)
             + poller.TASKS_RETRY_BUDGET + poller.OBSERVE_BUDGET
             + poller.RESULTS_TIMEOUT)
    assert worst <= poller.RUN_BUDGET
    assert worst + poller.SAFETY_MARGIN <= poller.UNIT_START_LIMIT


def test_observe_budget_shrinks_after_retry_waits(monkeypatch):
    """③ Retry-After로 시간을 쓰면 관측 예산이 그만큼 줄어든다."""
    mod = _load(monkeypatch, name="aws_kr_poller_d1")
    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    seen = {}

    def _call(path, payload, timeout=None):
        if path.endswith("/tasks"):
            if not seen.get("first"):
                seen["first"] = True
                raise _http_error(mod, 503, 20)
            return {"tasks": [_task("a")]}
        seen["submit_timeout"] = timeout
        return {"stored": 1, "accepted": 1, "rejected": []}

    monkeypatch.setattr(mod, "call_api", _call)
    monkeypatch.setattr(mod, "_get", lambda u, r, timeout=None: (200, _card()))
    start = clock["t"]
    mod.run_once()
    assert clock["t"] - start <= mod.RUN_BUDGET


def test_no_request_starts_without_room_left(monkeypatch):
    """④·⑥ 남은 시간이 없으면 새 요청을 시작하지 않고, 0·음수 timeout도 없다."""
    mod = _load(monkeypatch, name="aws_kr_poller_d2")
    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    calls = []

    def _call(path, payload, timeout=None):
        calls.append(timeout)
        return {"tasks": []}

    monkeypatch.setattr(mod, "call_api", _call)
    mod._run_deadline[0] = clock["t"]          # 이미 소진
    with pytest.raises(mod.BudgetExhausted):
        mod.lease_tasks_with_retry()
    assert calls == []


def test_every_issued_timeout_is_positive(monkeypatch):
    """⑥ 어떤 경계에서도 0초·음수 timeout으로 요청하지 않는다."""
    mod = _load(monkeypatch, name="aws_kr_poller_d3")
    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    issued = []

    def _call(path, payload, timeout=None):
        issued.append(timeout)
        if path.endswith("/tasks"):
            return {"tasks": [_task("a")]}
        return {"stored": 1, "accepted": 1, "rejected": []}

    monkeypatch.setattr(mod, "call_api", _call)
    monkeypatch.setattr(mod, "_get", lambda u, r, timeout=None: (200, _card()))
    mod.run_once()
    assert issued and all(t is None or t > 0 for t in issued)


def test_uses_monotonic_not_wall_clock(poller, monkeypatch):
    """⑤ 예산은 monotonic으로 잰다 — 시각 변경이 상한을 거짓말로 만들지 않는다."""
    src = io.open(_ROOT / "aws" / "kr_poller.py", encoding="utf-8").read()
    body = src[src.index("def run_once():"):src.index("def main():")]
    assert "time.monotonic()" in body
    assert "_run_deadline[0] = time.monotonic()" in src


def test_a_normal_twenty_five_clip_cycle_still_fits(monkeypatch):
    """⑦ 정상 canary 규모(25건, rate 1.0/s)를 여전히 처리한다."""
    mod = _load(monkeypatch, name="aws_kr_poller_d4")
    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    sent = {}

    def _call(path, payload, timeout=None):
        if path.endswith("/tasks"):
            return {"tasks": [_task("c-%d" % i) for i in range(25)]}
        sent["n"] = len(payload["results"])
        return {"stored": sent["n"], "accepted": sent["n"], "rejected": []}

    def _get(u, r, timeout=None):
        clock["t"] += 0.4                      # 실측 수준 응답
        return 200, _card()

    monkeypatch.setattr(mod, "call_api", _call)
    monkeypatch.setattr(mod, "_get", _get)
    start = clock["t"]
    mod.run_once()
    assert sent["n"] == 25                     # 25건 전부 제출
    assert clock["t"] - start < mod.RUN_BUDGET


def test_poller_exits_before_systemd_kills_it(monkeypatch):
    """⑧ 상류가 전부 느려도 systemd 상한 전에 poller가 스스로 끝낸다."""
    mod = _load(monkeypatch, name="aws_kr_poller_d5")
    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))

    def _call(path, payload, timeout=None):
        # 상류가 느려도 요청은 주어진 timeout에서 끊긴다 — 실제 소켓과 같은 계약.
        clock["t"] += (timeout or mod.CONTROL_TIMEOUT)
        if path.endswith("/tasks"):
            return {"tasks": [_task("c-%d" % i) for i in range(25)]}
        return {"stored": 0, "accepted": 0, "rejected": []}

    def _get(u, r, timeout=None):
        clock["t"] += (timeout or mod.CHZZK_TIMEOUT)
        raise TimeoutError("slow")

    monkeypatch.setattr(mod, "call_api", _call)
    monkeypatch.setattr(mod, "_get", _get)
    mod.main()
    elapsed = clock["t"] - 1000.0
    assert elapsed <= mod.RUN_BUDGET           # 상한 안에서 끝난다
    assert elapsed < mod.UNIT_START_LIMIT       # systemd가 죽이기 전이다


def test_long_contention_gives_up_instead_of_hammering_nonce(monkeypatch, capsys):
    """긴 경합은 한 회차에서 버티지 않는다 — timer가 10분 뒤 다시 시도한다.

    운영에서 약 44초 이상 이어진 DB 경합 구간이 있었다. nonce 쓰기를 반복하면
    경합을 **더 악화시키므로**, 짧은 경합만 흡수하고 나머지는 exit 1로 넘긴다.
    """
    mod = _load(monkeypatch, name="aws_kr_poller_d6")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    seen = _tasks_seq(mod, monkeypatch, [_http_error(mod, 503, 5)])
    assert mod.main() == 1
    assert seen["n"] == mod.TASKS_RETRY_MAX + 1        # 유한하고 작다
    assert mod.TASKS_RETRY_MAX <= 3
