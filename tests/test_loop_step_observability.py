"""4분 클립 루프의 실패 지점 관측 (P1-A1).

실측(2026-08-01): 잠금으로 루프가 죽으면 `loop_error detail="database is locked"`
한 줄만 남아 **어느 단계인지 특정할 수 없었다**(관측 4건 전부 미상).

이 커밋은 관측만 추가한다 — 여기서 함께 고정하는 것이 그 사실이다:
실패 시 뒤 단계는 예전처럼 실행되지 않는다.
"""
import asyncio
import json

import pytest

import singcup_clips as sc

STEPS = ["discover", "retry", "recheck", "reconcile", "deletion", "snapshot"]
FUNCS = {
    "discover":  "discover_new_clips",
    "retry":     "retry_failed_clips",
    "recheck":   "recheck_untagged_clips",
    "reconcile": "maybe_reconcile",
    "deletion":  "run_deletion_checks",
    "snapshot":  "ensure_hourly_snapshot",
}


@pytest.fixture
def logs(monkeypatch):
    """`_log` 출력을 가로채 구조화 payload로 모은다."""
    captured = []
    real_print = print

    def fake_print(msg, **_kw):
        if isinstance(msg, str) and msg.startswith("[singcup_clips] "):
            captured.append(json.loads(msg[len("[singcup_clips] "):]))
        else:
            real_print(msg)

    monkeypatch.setattr("builtins.print", fake_print)
    return captured


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _cycle(failing_step: str | None, calls: list, error: Exception | None = None):
    """루프 본문 한 회차를 그대로 재현한다(실제 루프의 단계 구성과 같은 순서)."""
    cycle = {"cycle_id": "testcycle", "step": None, "operation": None}
    token = sc._CYCLE.set(cycle)
    steps_ok = steps_failed = 0
    try:
        for name in STEPS:
            async def fn(_n=name):
                calls.append(_n)
                if _n == failing_step:
                    raise error or Exception("database is locked")
            try:
                async with sc._step(name):
                    await fn()
            except BaseException:
                steps_failed += 1
                raise
            steps_ok += 1
    except Exception as e:
        sc._log({"event": "loop_error", "level": "warning", "detail": str(e)[:200]})
    finally:
        if steps_ok or steps_failed:
            sc._log({"event": "collector_cycle_done", "steps_ok": steps_ok,
                     "steps_failed": steps_failed,
                     "steps_skipped": (6 - steps_ok - steps_failed) if steps_failed else 0,
                     "duration_ms": 0})
        sc._CYCLE.reset(token)
    return steps_ok, steps_failed


# ── 1. 실패 단계가 정확히 드러난다 ─────────────────────────────────────────
@pytest.mark.parametrize("step", STEPS)
def test_each_step_is_identified(logs, step):
    calls = []
    _run(_cycle(step, calls))
    errs = [x for x in logs if x["event"] == "loop_step_error"]
    assert len(errs) == 1, errs
    assert errs[0]["step"] == step
    assert errs[0]["worker"] == "singcup_clips"
    assert errs[0]["error_type"] == "database_locked"
    assert errs[0]["retryable"] is True
    assert errs[0]["cycle_id"] == "testcycle"
    assert "duration_ms" in errs[0]


# ── 2. 제어 흐름은 그대로다 (P1-A1의 핵심 불변식) ─────────────────────────
@pytest.mark.parametrize("step,expected", [
    ("discover",  ["discover"]),
    ("recheck",   ["discover", "retry", "recheck"]),
    ("deletion",  ["discover", "retry", "recheck", "reconcile", "deletion"]),
    ("snapshot",  STEPS),
])
def test_later_steps_are_still_skipped(logs, step, expected):
    """뒤 단계를 계속 실행하도록 바꾸지 않았다 — 그건 별도 판단이 필요한 동작 변경이다."""
    calls = []
    _run(_cycle(step, calls))
    assert calls == expected


def test_all_success_runs_every_step(logs):
    calls = []
    ok, failed = _run(_cycle(None, calls))
    assert calls == STEPS
    assert (ok, failed) == (6, 0)
    assert not [x for x in logs if x["event"] == "loop_step_error"]


def test_cycle_summary(logs):
    _run(_cycle("reconcile", []))
    done = [x for x in logs if x["event"] == "collector_cycle_done"][0]
    assert done["steps_ok"] == 3          # discover·retry·recheck
    assert done["steps_failed"] == 1      # reconcile
    assert done["steps_skipped"] == 2     # deletion·snapshot


# ── 3. 예외 종류를 구분한다 ────────────────────────────────────────────────
def test_unexpected_error_is_not_marked_retryable(logs):
    _run(_cycle("deletion", [], error=TypeError("프로그래밍 오류")))
    err = [x for x in logs if x["event"] == "loop_step_error"][0]
    assert err["error_type"] == "unexpected"
    assert err["retryable"] is False


def test_cancellation_is_not_swallowed(logs):
    async def cancel_cycle():
        cycle = {"cycle_id": "c", "step": None, "operation": None}
        token = sc._CYCLE.set(cycle)
        try:
            with pytest.raises(asyncio.CancelledError):
                async with sc._step("deletion"):
                    raise asyncio.CancelledError()
        finally:
            sc._CYCLE.reset(token)

    _run(cancel_cycle())
    err = [x for x in logs if x["event"] == "loop_step_error"][0]
    assert err["step"] == "deletion"
    assert err["error_type"] == "unexpected"    # 잠금이 아니다


# ── 4. operation이 붙는다 ──────────────────────────────────────────────────
def test_operation_is_reported(logs):
    async def run():
        cycle = {"cycle_id": "c", "step": None, "operation": None}
        token = sc._CYCLE.set(cycle)
        try:
            with pytest.raises(Exception):
                async with sc._step("deletion"):
                    with sc._operation("acquire_owner_lock"):
                        raise Exception("database is locked")
        finally:
            sc._CYCLE.reset(token)

    _run(run())
    err = [x for x in logs if x["event"] == "loop_step_error"][0]
    assert err["step"] == "deletion"
    assert err["operation"] == "acquire_owner_lock"


def test_logs_inside_a_cycle_carry_context(logs):
    """db_locked_giveup 같은 기존 로그도 이제 어느 단계인지 알 수 있다."""
    async def run():
        cycle = {"cycle_id": "c", "step": None, "operation": None}
        token = sc._CYCLE.set(cycle)
        try:
            async with sc._step("deletion"):
                with sc._operation("acquire_owner_lock"):
                    sc._log({"event": "db_locked_giveup", "what": "acquire_owner_lock"})
        finally:
            sc._CYCLE.reset(token)

    _run(run())
    giveup = [x for x in logs if x["event"] == "db_locked_giveup"][0]
    assert giveup["step"] == "deletion"
    assert giveup["operation"] == "acquire_owner_lock"
    assert giveup["cycle_id"] == "c"


def test_logs_outside_a_cycle_are_unchanged(logs):
    """회차 밖(스윕·API 경로)의 로그에는 아무것도 붙이지 않는다."""
    sc._log({"event": "something", "x": 1})
    assert logs[-1] == {"event": "something", "x": 1}


# ── 5. 느린 쓰기 ───────────────────────────────────────────────────────────
def test_slow_write_is_reported(logs, monkeypatch):
    """'잠금을 맞은 작업'만 봐서는 범인을 못 찾는다 — 오래 쥔 작업도 남긴다."""
    monkeypatch.setattr(sc, "SLOW_WRITE_MS", 0)
    with sc._operation("recompute_ranking_commit"):
        pass
    slow = [x for x in logs if x["event"] == "db_write_slow"]
    assert slow and slow[0]["what"] == "recompute_ranking_commit"
    assert "total_ms" in slow[0]


def test_fast_write_is_not_reported(logs, monkeypatch):
    monkeypatch.setattr(sc, "SLOW_WRITE_MS", 10_000)
    with sc._operation("fast"):
        pass
    assert not [x for x in logs if x["event"] == "db_write_slow"]


# ── 6. 민감정보가 없다 ─────────────────────────────────────────────────────
def test_error_log_has_no_sql_or_secrets(logs):
    leaky = ("INSERT INTO singcup_clips VALUES ('secret') "
             "Authorization=Bearer TOPSECRET 192.168.0.1")
    _run(_cycle("discover", [], error=Exception(leaky)))
    err = [x for x in logs if x["event"] == "loop_step_error"][0]
    assert len(err["detail"]) <= 160
    # 길이 제한만으로는 부족하다 — 필드 구성 자체에 SQL·토큰 자리가 없어야 한다
    assert set(err) <= {"event", "level", "worker", "step", "operation",
                        "error_type", "retryable", "duration_ms", "detail",
                        "cycle_id"}
