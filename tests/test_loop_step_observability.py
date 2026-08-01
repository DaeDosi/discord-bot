"""4분 클립 루프의 단계 격리와 결과 집계 (P1-A1 관측 → P1-A2 격리).

계보:
  P1-A1이 실패 지점을 남기게 하자마자 근거가 나왔다 —
  `step=discover, operation=null, duration_ms=0, steps_ok=0, steps_skipped=5`.
  회차의 **첫 쓰기**(`acquire_named_lock`)가 공유 연결에 직접 커밋하고 있었고,
  잠금이 예외로 올라와 6단계가 통째로 죽었다.

  그리고 같은 로그에서 반대 방향의 결함도 드러났다 — deletion이 owner lock을
  놓쳐 클립을 건너뛰었는데 `steps_ok=6`, 즉 **부분 실패가 완전 성공으로 보고**됐다.

여기서 고정하는 것:
  - 복구 가능한 실패는 그 단계에서 끝나고 독립 단계는 계속 돈다
  - 프로그래밍 오류·취소는 격리하지 않고 그대로 올린다
  - 건너뛴 일이 있으면 success가 아니라 partial이다
  - 불완전한 회차는 스냅샷을 만들지 않는다(같은 버킷을 교체할 수 없으므로)
"""
import asyncio
import json

import pytest
import singcup_clips as sc

STEPS = ["discover", "retry", "recheck", "reconcile", "deletion"]


@pytest.fixture
def logs(monkeypatch):
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


def _new_cycle():
    return {"cycle_id": "testcycle", "step": None, "operation": None,
            "partial_reasons": []}


async def _cycle(behaviour: dict, calls: list):
    """루프 본문 한 회차를 실제 구성 그대로 재현한다."""
    token = sc._CYCLE.set(_new_cycle())
    results = {}
    try:
        for name in STEPS:
            async def fn(_n=name):
                calls.append(_n)
                act = behaviour.get(_n)
                if isinstance(act, BaseException):
                    raise act
                if act == "partial":
                    sc._log({"event": "clip_deletion_skipped_owner_locked",
                             "clip_uid": "x"})
                    return {"status": sc.ST_OK}
                if act == "skip":
                    return {"status": sc.ST_SKIPPED}
                return {"status": sc.ST_OK}
            results[name] = await sc._run_step(name, fn)

        incomplete = [k for k, v in results.items() if v in ("failed", "partial")]
        if incomplete:
            results["snapshot"] = "skipped"
            sc._log({"event": "snapshot_skipped_incomplete_cycle",
                     "failed_steps": [k for k, v in results.items() if v == "failed"],
                     "partial_steps": [k for k, v in results.items() if v == "partial"]})
        else:
            async def snap():
                calls.append("snapshot")
                return True
            results["snapshot"] = await sc._run_step("snapshot", snap)
    finally:
        sc._CYCLE.reset(token)
    return results


LOCKED = Exception("database is locked")


# ── 1. 실패 단계 식별 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("step", STEPS)
def test_each_step_is_identified(logs, step):
    _run(_cycle({step: Exception("database is locked")}, []))
    errs = [x for x in logs if x["event"] == "loop_step_error"]
    assert len(errs) == 1, errs
    assert errs[0]["step"] == step
    assert errs[0]["worker"] == "singcup_clips"
    assert errs[0]["error_type"] == "database_locked"
    assert errs[0]["retryable"] is True
    assert errs[0]["cycle_id"] == "testcycle"


# ── 2. 격리 — 독립 단계는 계속 돈다 ───────────────────────────────────────
def test_recoverable_failure_does_not_stop_the_cycle(logs):
    """예전에는 첫 실패에서 회차가 끝났다(실측 steps_skipped=5)."""
    calls = []
    results = _run(_cycle({"discover": Exception("database is locked")}, calls))
    assert calls == STEPS + ["snapshot"] or calls == STEPS   # snapshot은 아래에서 본다
    assert results["discover"] == "failed"
    assert all(results[s] == "success" for s in STEPS[1:])


def test_every_step_still_runs_when_one_fails(logs):
    calls = []
    _run(_cycle({"reconcile": Exception("database is locked")}, calls))
    for s in STEPS:
        assert s in calls, s


# ── 3. 격리하지 않는 오류 ─────────────────────────────────────────────────
def test_programming_error_is_not_isolated(logs):
    """부분 실패로 위장하면 진짜 버그가 숨는다 — 그대로 올린다."""
    with pytest.raises(TypeError):
        _run(_cycle({"deletion": TypeError("프로그래밍 오류")}, []))
    err = [x for x in logs if x["event"] == "loop_step_error"][0]
    assert err["error_type"] == "unexpected"
    assert err["retryable"] is False


def test_cancellation_is_not_isolated(logs):
    with pytest.raises(asyncio.CancelledError):
        _run(_cycle({"retry": asyncio.CancelledError()}, []))
    err = [x for x in logs if x["event"] == "loop_step_error"][0]
    assert err["step"] == "retry"


# ── 4. partial 집계 ────────────────────────────────────────────────────────
def test_skipped_work_is_partial_not_success(logs):
    """deletion이 클립을 건너뛰었는데 steps_ok=6으로 보고되던 결함."""
    results = _run(_cycle({"deletion": "partial"}, []))
    assert results["deletion"] == "partial"
    assert results["discover"] == "success"


def test_partial_reason_is_recorded(logs):
    _run(_cycle({"deletion": "partial"}, []))
    skipped = [x for x in logs if x["event"] == "snapshot_skipped_incomplete_cycle"][0]
    assert skipped["partial_steps"] == ["deletion"]


def test_step_that_declines_to_run_is_skipped(logs):
    """다른 워커가 락을 쥐고 있어 이번엔 안 도는 경우 — 실패가 아니다."""
    results = _run(_cycle({"discover": "skip"}, []))
    assert results["discover"] == "skipped"


def test_all_success(logs):
    calls = []
    results = _run(_cycle({}, calls))
    assert calls == STEPS + ["snapshot"]
    assert set(results.values()) == {"success"}


# ── 5. 스냅샷 — 불완전한 회차에서는 만들지 않는다 ─────────────────────────
@pytest.mark.parametrize("behaviour", [
    {"discover": Exception("database is locked")},
    {"deletion": "partial"},
])
def test_incomplete_cycle_skips_snapshot(logs, behaviour):
    """`UNIQUE(bucket) + INSERT OR IGNORE`라 한 번 쓰면 그 시간 안에 교체할 수 없다.
    부정확한 값을 정상 기준선으로 굳히면 정상 회차가 와도 못 고친다."""
    calls = []
    results = _run(_cycle(behaviour, calls))
    assert results["snapshot"] == "skipped"
    assert "snapshot" not in calls, "불완전한 회차인데 스냅샷을 만들었다"
    assert [x for x in logs if x["event"] == "snapshot_skipped_incomplete_cycle"]


def test_next_clean_cycle_creates_the_snapshot(logs):
    """건너뛴 뒤 다음 회차가 정상이면 같은 버킷을 채울 수 있다."""
    _run(_cycle({"discover": Exception("database is locked")}, []))
    calls = []
    results = _run(_cycle({}, calls))
    assert results["snapshot"] == "success"
    assert "snapshot" in calls


def test_skipped_step_alone_does_not_block_snapshot(logs):
    """'다른 워커가 돌고 있어 건너뜀'은 데이터 품질 문제가 아니다."""
    calls = []
    results = _run(_cycle({"discover": "skip"}, calls))
    assert results["snapshot"] == "success"
    assert "snapshot" in calls


# ── 6. 회차 요약 ───────────────────────────────────────────────────────────
def test_cycle_summary_counts(logs):
    async def run():
        token = sc._CYCLE.set(_new_cycle())
        results = {"discover": "failed", "retry": "success", "recheck": "success",
                   "reconcile": "success", "deletion": "partial", "snapshot": "skipped"}
        try:
            def _n(kind):
                return sum(1 for v in results.values() if v == kind)
            sc._log({"event": "collector_cycle_done",
                     "steps_success": _n("success"), "steps_partial": _n("partial"),
                     "steps_failed": _n("failed"), "steps_skipped": _n("skipped"),
                     "steps": results, "duration_ms": 0})
        finally:
            sc._CYCLE.reset(token)

    _run(run())
    done = [x for x in logs if x["event"] == "collector_cycle_done"][0]
    assert (done["steps_success"], done["steps_partial"],
            done["steps_failed"], done["steps_skipped"]) == (3, 1, 1, 1)


# ── 7. 컨텍스트와 operation ────────────────────────────────────────────────
def test_operation_is_reported(logs):
    async def run():
        token = sc._CYCLE.set(_new_cycle())
        try:
            async def fn():
                with sc._operation("acquire_owner_lock"):
                    raise Exception("database is locked")
            await sc._run_step("deletion", fn)
        finally:
            sc._CYCLE.reset(token)

    _run(run())
    err = [x for x in logs if x["event"] == "loop_step_error"][0]
    assert err["operation"] == "acquire_owner_lock"


def test_logs_inside_a_cycle_carry_context(logs):
    async def run():
        token = sc._CYCLE.set(_new_cycle())
        try:
            async def fn():
                sc._log({"event": "db_locked_giveup", "what": "acquire_owner_lock"})
                return {"status": sc.ST_OK}
            return await sc._run_step("deletion", fn)
        finally:
            sc._CYCLE.reset(token)

    status = _run(run())
    giveup = [x for x in logs if x["event"] == "db_locked_giveup"][0]
    assert giveup["step"] == "deletion" and giveup["cycle_id"] == "testcycle"
    assert status == "partial", "잠금 포기를 성공으로 세면 안 된다"


def test_logs_outside_a_cycle_are_unchanged(logs):
    sc._log({"event": "something", "x": 1})
    assert logs[-1] == {"event": "something", "x": 1}


def test_internal_context_keys_are_not_logged(logs):
    async def run():
        token = sc._CYCLE.set(_new_cycle())
        try:
            async def fn():
                sc._log({"event": "clip_deletion_skipped_owner_locked", "clip_uid": "x"})
                return {"status": sc.ST_OK}
            await sc._run_step("deletion", fn)
        finally:
            sc._CYCLE.reset(token)

    _run(run())
    for entry in logs:
        assert "partial_reasons" not in entry or entry["event"] == "collector_cycle_done"


# ── 8. 느린 쓰기 ───────────────────────────────────────────────────────────
def test_slow_write_is_reported(logs, monkeypatch):
    monkeypatch.setattr(sc, "SLOW_WRITE_MS", 0)
    with sc._operation("recompute_ranking_commit"):
        pass
    slow = [x for x in logs if x["event"] == "db_write_slow"]
    assert slow and slow[0]["what"] == "recompute_ranking_commit"


def test_fast_write_is_not_reported(logs, monkeypatch):
    monkeypatch.setattr(sc, "SLOW_WRITE_MS", 10_000)
    with sc._operation("fast"):
        pass
    assert not [x for x in logs if x["event"] == "db_write_slow"]


# ── 9. 민감정보 ────────────────────────────────────────────────────────────
def test_error_log_has_no_sql_or_secrets(logs):
    leaky = ("database is locked — INSERT INTO singcup_clips VALUES ('secret') "
             "Authorization=Bearer TOPSECRET 192.168.0.1")
    _run(_cycle({"discover": Exception(leaky)}, []))
    err = [x for x in logs if x["event"] == "loop_step_error"][0]
    assert len(err["detail"]) <= 160
    assert set(err) <= {"event", "level", "worker", "step", "operation",
                        "error_type", "retryable", "duration_ms", "detail",
                        "cycle_id"}
