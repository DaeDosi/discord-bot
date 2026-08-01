"""수집 결과 저장의 원자성과 잠금 복구 (P0 긴급).

실측(2026-08-01 23:48:19 KST): 5,582건 수집은 성공했는데 저장이 `database is locked`로
죽었고, 그 예외가 수집 루프 바깥까지 올라가 **수집한 결과가 통째로 버려졌다.**
다음 회차는 10분 뒤라 화면이 `20.4분 전 확인 · 지연`이 됐다.

여기서 고정하는 것:
  - 스냅샷과 성공 회차는 **한 트랜잭션**이다(둘 중 하나만 남는 상태 금지)
  - 저장 실패 시 부분 스냅샷을 남기지 않는다
  - 재시도는 **DB 저장만** 한다 — 외부 API를 다시 부르지 않는다
  - 같은 결과를 다시 저장해도 행이 늘지 않는다(멱등)
  - 롤업·정리 실패가 이미 게시된 스냅샷을 되돌리지 않는다
  - 잠금으로 최종 실패해도 수집 루프는 계속 돈다

**가짜 동시성 금지** — 경합은 `aiosqlite.connect`로 연 독립 연결로 실제로 만든다.
"""
import asyncio

import aiosqlite
import pytest
import rising_collector as rc

import database

NOW = 1_785_600_000


def _lives(n):
    return [{"chzzk_channel_id": f"ch{i:05d}", "channel_name": f"채널{i}",
             "follower_count": 100 + i, "concurrent_viewers": i % 300,
             "category_id": 1, "category_name": "게임", "live_title": f"제목{i}",
             "open_date": "2026-08-01", "adult": 0, "tags": "t",
             "channel_image_url": ""} for i in range(n)]


def _rows(lives, now=NOW):
    return [(now, x["chzzk_channel_id"], x["channel_name"], x["follower_count"],
             x["concurrent_viewers"], x["category_id"], x["category_name"],
             x["live_title"], x["open_date"], x["adult"], x["tags"]) for x in lives]


async def _hold_write_lock():
    conn = await aiosqlite.connect(database.DB_PATH)
    await conn.execute("PRAGMA busy_timeout=100")
    await conn.execute("BEGIN IMMEDIATE")
    await conn.execute(
        "INSERT INTO rising_collect_runs (collected_at, live_count, total_viewers, ok, note)"
        " VALUES (999999999, 0, 0, 0, 'blocker')")
    return conn


async def _release(conn):
    try:
        await conn.rollback()
    finally:
        await conn.close()


async def _counts(now=NOW):
    c = await database.get_db()
    snap = await (await c.execute(
        "SELECT COUNT(*) FROM rising_live_snapshots WHERE collected_at=?", (now,))).fetchone()
    run = await (await c.execute(
        "SELECT COUNT(*), COALESCE(MAX(ok),-1) FROM rising_collect_runs WHERE collected_at=?",
        (now,))).fetchone()
    return snap[0], run[0], run[1]


async def _wipe():
    c = await database.get_db()
    await c.execute("DELETE FROM rising_live_snapshots")
    await c.execute("DELETE FROM rising_collect_runs")
    await c.commit()


async def _persist(rows, now=NOW, budget=8.0):
    from utils.db_write import db_write_isolated
    return await db_write_isolated(
        database.DB_PATH,
        lambda conn: rc._persist_snapshot_and_run(
            conn, collected_at=now, rows=rows, total_viewers=999,
            note="ok", duration_ms=1, pages=114, api_calls=114),
        what="test_persist", busy_timeout_ms=rc.SNAPSHOT_TX_BUSY_TIMEOUT_MS,
        attempts=rc.SNAPSHOT_TX_ATTEMPTS, budget_seconds=budget)


# ── 1. 정상 저장 ───────────────────────────────────────────────────────────
def test_large_snapshot_and_run_are_written_together(db):
    db(_wipe())
    rows = _rows(_lives(5582))
    assert db(_persist(rows)) is True
    snap, runs, ok = db(_counts())
    assert (snap, runs, ok) == (5582, 1, 1)


def test_idempotent_retry_does_not_duplicate(db):
    """같은 결과를 다시 저장해도 행이 늘지 않는다."""
    db(_wipe())
    rows = _rows(_lives(500))
    for _ in range(3):
        assert db(_persist(rows)) is True
    snap, runs, ok = db(_counts())
    assert (snap, runs, ok) == (500, 1, 1)


def test_failed_run_row_is_upgraded_to_success(db):
    """같은 collected_at에 실패 회차가 먼저 있으면 INSERT OR IGNORE는 실패를 남긴다 —
    그러면 스냅샷이 있어도 화면이 이 회차를 계속 무시한다."""
    async def seed_failed():
        c = await database.get_db()
        await c.execute(
            "INSERT INTO rising_collect_runs (collected_at, live_count, total_viewers, ok, note)"
            " VALUES (?,0,0,0,'저장 실패')", (NOW,))
        await c.commit()

    db(_wipe())
    db(seed_failed())
    assert db(_persist(_rows(_lives(10)))) is True
    snap, runs, ok = db(_counts())
    assert (snap, runs, ok) == (10, 1, 1), "실패 회차가 성공으로 갱신되지 않았다"


# ── 2. 잠금 ────────────────────────────────────────────────────────────────
def test_lock_leaves_nothing_behind(db):
    """부분 스냅샷도, 성공 회차만 남는 상태도 만들지 않는다."""
    async def run():
        await _wipe()
        blocker = await _hold_write_lock()
        try:
            return await _persist(_rows(_lives(2000)), budget=0.6)
        finally:
            await _release(blocker)

    assert db(run()) is False
    snap, runs, ok = db(_counts())
    assert (snap, runs) == (0, 0), f"잠금 실패인데 스냅샷 {snap} / 회차 {runs}이 남았다"


def test_lock_released_within_deadline_succeeds(db):
    """deadline 안에 잠금이 풀리면 **같은 결과를 그대로** 저장한다(API 재호출 없음)."""
    async def run():
        await _wipe()
        blocker = await _hold_write_lock()

        async def free_soon():
            await asyncio.sleep(0.4)
            await _release(blocker)

        task = asyncio.ensure_future(free_soon())
        ok = await _persist(_rows(_lives(300)), budget=8.0)
        await task
        return ok

    assert db(run()) is True
    snap, runs, ok = db(_counts())
    assert (snap, runs, ok) == (300, 1, 1)


def test_no_open_transaction_after_giveup(db):
    async def run():
        await _wipe()
        blocker = await _hold_write_lock()
        try:
            await _persist(_rows(_lives(100)), budget=0.6)
        finally:
            await _release(blocker)
        c = await database.get_db()
        assert c.in_transaction is False
        await c.execute("DELETE FROM rising_collect_runs WHERE collected_at=-1")
        await c.commit()

    db(run())


# ── 3. 수집 사이클 (외부 API 재호출 0) ─────────────────────────────────────
@pytest.fixture
def stub_fetch(monkeypatch):
    """네트워크를 대신하고 호출 횟수를 센다."""
    calls = {"fetch": 0, "enrich": 0}
    lives = _lives(1200)

    async def fake_fetch(client):
        calls["fetch"] += 1
        return list(lives), "ok", {"pages": 114, "api_calls": 114}

    async def fake_enrich(client, ls):
        calls["enrich"] += 1

    monkeypatch.setattr(rc, "_fetch_all_lives", fake_fetch)
    monkeypatch.setattr(rc, "_enrich_top", fake_enrich)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)
    return calls


async def _noop(*a, **k):
    return None


def test_cycle_publishes_and_calls_api_once(db, stub_fetch, monkeypatch):
    db(_wipe())
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    n, note = db(rc.collect_once())
    assert n == 1200 and note == "ok"
    assert stub_fetch["fetch"] == 1, "저장 재시도 때문에 API를 다시 불렀다"


def test_cycle_does_not_raise_on_lock(db, stub_fetch, monkeypatch):
    """예외가 올라가면 수집 루프의 바깥 except로 가서 단계도 남지 않는다."""
    monkeypatch.setattr(rc, "SNAPSHOT_TX_BUDGET_SECONDS", 0.6)

    async def run():
        await _wipe()
        blocker = await _hold_write_lock()
        try:
            return await rc.collect_once()
        finally:
            await _release(blocker)

    n, note = db(run())
    assert n == 0
    assert "저장 실패" in note
    assert stub_fetch["fetch"] == 1, "실패했다고 API를 다시 부르면 안 된다"


def test_rollup_failure_keeps_published_snapshot(db, stub_fetch, monkeypatch):
    """롤업이 죽어도 이미 게시된 최신 스냅샷을 되돌리지 않는다."""
    async def boom(_now):
        raise RuntimeError("rollup 실패")

    db(_wipe())
    monkeypatch.setattr(rc, "_build_rollup", boom)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    n, note = db(rc.collect_once())
    assert n == 1200 and note == "ok"

    async def latest_ok():
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT ok, live_count FROM rising_collect_runs ORDER BY collected_at DESC LIMIT 1"
        )).fetchone()
        return int(r["ok"]), int(r["live_count"])

    assert db(latest_ok()) == (1, 1200)


def test_prune_failure_keeps_published_snapshot(db, stub_fetch, monkeypatch):
    async def boom(_now):
        raise RuntimeError("prune 실패")

    db(_wipe())
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", boom)
    n, note = db(rc.collect_once())
    assert n == 1200 and note == "ok"


def test_next_cycle_runs_after_a_failure(db, stub_fetch, monkeypatch):
    """한 회차가 잠금으로 실패해도 다음 회차는 정상 게시된다."""
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "SNAPSHOT_TX_BUDGET_SECONDS", 0.6)

    async def run():
        await _wipe()
        blocker = await _hold_write_lock()
        try:
            first = await rc.collect_once()
        finally:
            await _release(blocker)
        rc.SNAPSHOT_TX_BUDGET_SECONDS = 8.0
        second = await rc.collect_once()
        return first, second

    first, second = db(run())
    assert first[0] == 0 and second[0] == 1200
    assert stub_fetch["fetch"] == 2, "회차마다 한 번씩만 부른다"


# ── 4. 프로필 저장이 회차를 죽이지 않는다 (차단 조건 A) ────────────────────
@pytest.fixture
def fast_recovery(monkeypatch):
    """복구 대기를 짧게 — 스케줄 자체는 아래 별도 테스트가 검증한다."""
    monkeypatch.setattr(rc, "SNAPSHOT_RECOVERY_WAITS", (0.05, 0.05, 0.05))
    return rc.SNAPSHOT_RECOVERY_WAITS


def test_profile_failure_does_not_block_publish(db, stub_fetch, monkeypatch):
    """예전에는 프로필 저장이 게시보다 먼저라, 그게 잠기면 수집 결과가 통째로 버려졌다."""
    async def boom():
        raise RuntimeError("database is locked")

    db(_wipe())
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", boom)
    monkeypatch.setattr(rc, "_LAST_PERSIST_DATE", None)

    n, note = db(rc.collect_once())
    assert n == 1200 and note == "ok", "프로필 저장 실패가 게시를 막았다"

    async def published():
        c = await database.get_db()
        snap = await (await c.execute(
            "SELECT COUNT(*) FROM rising_live_snapshots")).fetchone()
        run = await (await c.execute(
            "SELECT COUNT(*), MAX(ok) FROM rising_collect_runs")).fetchone()
        return snap[0], run[0], run[1]

    assert db(published()) == (1200, 1, 1)


def test_profile_failure_does_not_mark_the_day_done(db, stub_fetch, monkeypatch):
    """실패한 날을 완료로 표시하면 그날 다시 시도하지 않는다."""
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise RuntimeError("database is locked")

    db(_wipe())
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", boom)
    monkeypatch.setattr(rc, "_LAST_PERSIST_DATE", None)

    db(rc.collect_once())
    assert rc._LAST_PERSIST_DATE is None, "실패했는데 날짜를 갱신했다"
    db(rc.collect_once())
    assert calls["n"] == 2, "다음 회차에서 재시도하지 않았다"


def test_profile_success_marks_the_day(db, stub_fetch, monkeypatch):
    ok_calls = {"n": 0}

    async def fine():
        ok_calls["n"] += 1

    db(_wipe())
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", fine)
    monkeypatch.setattr(rc, "_LAST_PERSIST_DATE", None)

    db(rc.collect_once())
    assert rc._LAST_PERSIST_DATE is not None
    db(rc.collect_once())
    assert ok_calls["n"] == 1, "성공한 날에 다시 저장했다"


# ── 5. 첫 예산보다 긴 잠금에서 같은 payload로 복구 (차단 조건 B) ───────────
def test_recovers_with_same_payload_after_long_lock(db, stub_fetch, monkeypatch):
    """8초 예산을 넘겨도 수집 결과를 버리지 않는다 — 같은 rows로 다시 저장한다."""
    monkeypatch.setattr(rc, "SNAPSHOT_TX_BUDGET_SECONDS", 0.4)
    monkeypatch.setattr(rc, "SNAPSHOT_RECOVERY_WAITS", (0.3, 0.3, 0.3))
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)

    async def run():
        await _wipe()
        blocker = await _hold_write_lock()

        async def free_after_first_attempt():
            await asyncio.sleep(0.9)         # 1차(0.4s) 실패 후 풀어 준다
            await _release(blocker)

        task = asyncio.ensure_future(free_after_first_attempt())
        out = await rc.collect_once()
        await task
        return out

    n, note = db(run())
    assert n == 1200 and note == "ok", "복구하지 못했다"
    assert stub_fetch["fetch"] == 1, "복구 재시도가 외부 API를 다시 불렀다"


def test_all_attempts_fail_leaves_nothing(db, stub_fetch, monkeypatch, fast_recovery):
    monkeypatch.setattr(rc, "SNAPSHOT_TX_BUDGET_SECONDS", 0.3)
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)

    async def run():
        await _wipe()
        blocker = await _hold_write_lock()
        try:
            out = await rc.collect_once()
        finally:
            await _release(blocker)
        c = await database.get_db()
        snap = await (await c.execute("SELECT COUNT(*) FROM rising_live_snapshots")).fetchone()
        run_ok = await (await c.execute(
            "SELECT COUNT(*) FROM rising_collect_runs WHERE ok=1")).fetchone()
        return out, snap[0], run_ok[0]

    (n, note), snap, ok_runs = db(run())
    assert n == 0 and "저장 실패" in note
    assert snap == 0, f"부분 스냅샷 {snap}건이 남았다"
    assert ok_runs == 0, "성공 회차가 남았다"
    assert stub_fetch["fetch"] == 1


def test_recovery_produces_exactly_one_set(db, stub_fetch, monkeypatch):
    monkeypatch.setattr(rc, "SNAPSHOT_TX_BUDGET_SECONDS", 0.4)
    monkeypatch.setattr(rc, "SNAPSHOT_RECOVERY_WAITS", (0.3, 0.3, 0.3))
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)

    async def run():
        await _wipe()
        blocker = await _hold_write_lock()

        async def free_soon():
            await asyncio.sleep(0.9)
            await _release(blocker)

        task = asyncio.ensure_future(free_soon())
        await rc.collect_once()
        await task
        c = await database.get_db()
        snap = await (await c.execute("SELECT COUNT(*) FROM rising_live_snapshots")).fetchone()
        runs = await (await c.execute("SELECT COUNT(*) FROM rising_collect_runs")).fetchone()
        return snap[0], runs[0]

    snap, runs = db(run())
    assert (snap, runs) == (1200, 1), f"중복 저장: 스냅샷 {snap} 회차 {runs}"


def test_recovery_is_cancellable(db, stub_fetch, monkeypatch):
    """종료 신호에 즉시 반응해야 한다 — 복구 대기 중이라도."""
    monkeypatch.setattr(rc, "SNAPSHOT_TX_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(rc, "SNAPSHOT_RECOVERY_WAITS", (30.0, 30.0))
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)

    async def run():
        await _wipe()
        blocker = await _hold_write_lock()
        try:
            task = asyncio.ensure_future(rc.collect_once())
            await asyncio.sleep(0.6)          # 첫 시도 실패 후 대기에 들어간 시점
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await _release(blocker)
        c = await database.get_db()
        assert c.in_transaction is False

    db(run())


def test_worst_case_recovery_fits_in_the_cycle():
    """복구가 끝나기 전에 다음 정규 회차가 시작되면 안 된다."""
    worst = (rc.SNAPSHOT_TX_BUDGET_SECONDS * (len(rc.SNAPSHOT_RECOVERY_WAITS) + 1)
             + sum(rc.SNAPSHOT_RECOVERY_WAITS))
    assert worst < rc.COLLECT_INTERVAL / 2, (
        f"복구 최악 {worst}s 가 수집 주기 {rc.COLLECT_INTERVAL}s 의 절반을 넘는다")


# ── 6. rollup/prune 다음 회차 복구 ─────────────────────────────────────────
def test_rollup_and_prune_recover_next_cycle(db, stub_fetch, monkeypatch):
    """실패해도 다음 회차가 멱등하게 다시 한다."""
    calls = {"rollup": 0, "prune": 0}

    async def flaky_rollup(now):
        calls["rollup"] += 1
        if calls["rollup"] == 1:
            raise RuntimeError("database is locked")

    async def flaky_prune(now):
        calls["prune"] += 1
        if calls["prune"] == 1:
            raise RuntimeError("database is locked")

    db(_wipe())
    monkeypatch.setattr(rc, "_build_rollup", flaky_rollup)
    monkeypatch.setattr(rc, "_prune_old", flaky_prune)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)

    assert db(rc.collect_once())[0] == 1200      # 실패해도 게시는 성공
    assert db(rc.collect_once())[0] == 1200
    assert calls == {"rollup": 2, "prune": 2}, calls


# ── 7. 최종 실패 기록(ok=0) 경로 (차단 조건 1) ─────────────────────────────
def test_failed_run_best_effort_never_escapes(db, stub_fetch, monkeypatch, fast_recovery):
    """실패 기록마저 잠겨도 예외가 collect_once 밖으로 나가면 안 된다.
    나가면 수집 루프의 바깥 except로 가서 단계가 사라진다(원래 증상)."""
    monkeypatch.setattr(rc, "SNAPSHOT_TX_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)

    async def run():
        await _wipe()
        blocker = await _hold_write_lock()        # 실패 기록도 같이 막힌다
        try:
            return await rc.collect_once()        # 예외 없이 돌아와야 한다
        finally:
            await _release(blocker)

    n, note = db(run())
    assert n == 0 and "저장 실패" in note
    snap, runs, ok = db(_counts())
    assert snap == 0, "실패 기록이 부분 스냅샷을 만들었다"


def test_failed_run_never_downgrades_existing_success(db):
    """늦게 도착한 ok=0이 이미 게시된 성공을 실패로 강등하면 화면이 데이터를 버린다."""
    async def run():
        await _wipe()
        await _persist(_rows(_lives(50)))          # ok=1 먼저
        from utils.db_write import db_write_isolated
        await db_write_isolated(
            database.DB_PATH,
            lambda conn: conn.execute(
                "INSERT INTO rising_collect_runs (collected_at, live_count, total_viewers,"
                " ok, note, duration_ms, pages, api_calls)"
                " VALUES (?,0,0,0,'늦은 실패',0,0,0)"
                " ON CONFLICT(collected_at) DO NOTHING", (NOW,)),
            what="late_failed_run", busy_timeout_ms=200, attempts=1, budget_seconds=0.5)
        return await _counts()

    snap, runs, ok = db(run())
    assert (snap, runs, ok) == (50, 1, 1), "성공이 실패로 강등됐다"


def test_failed_run_record_lock_does_not_break_next_cycle(db, stub_fetch, monkeypatch,
                                                          fast_recovery):
    """실패 기록이 잠겨도 다음 정규 회차는 정상 게시된다."""
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)

    async def run():
        await _wipe()
        rc.SNAPSHOT_TX_BUDGET_SECONDS = 0.2
        blocker = await _hold_write_lock()
        try:
            first = await rc.collect_once()
        finally:
            await _release(blocker)
        rc.SNAPSHOT_TX_BUDGET_SECONDS = 8.0
        second = await rc.collect_once()
        return first, second

    first, second = db(run())
    assert first[0] == 0 and second[0] == 1200
    assert stub_fetch["fetch"] == 2


# ── 8. 취소 전파 (차단 조건 2) ─────────────────────────────────────────────
def test_cancel_during_wait_propagates_and_stops_retrying(db, stub_fetch, monkeypatch):
    """대기 중 취소는 **즉시 전파**되고, 그 뒤 재시도도 ok=0 기록도 하지 않는다.
    (배포 재시작을 '정상 실패 회차'로 위장하면 안 된다)"""
    monkeypatch.setattr(rc, "SNAPSHOT_TX_BUDGET_SECONDS", 0.2)
    monkeypatch.setattr(rc, "SNAPSHOT_RECOVERY_WAITS", (30.0, 30.0))
    monkeypatch.setattr(rc, "_build_rollup", _noop)
    monkeypatch.setattr(rc, "_prune_old", _noop)
    monkeypatch.setattr(rc, "_persist_profiles", _noop)

    async def run():
        await _wipe()
        blocker = await _hold_write_lock()
        try:
            task = asyncio.ensure_future(rc.collect_once())
            await asyncio.sleep(0.7)              # 1차 실패 후 30초 대기에 진입
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await _release(blocker)
        c = await database.get_db()
        assert c.in_transaction is False
        snap = await (await c.execute("SELECT COUNT(*) FROM rising_live_snapshots")).fetchone()
        runs = await (await c.execute("SELECT COUNT(*) FROM rising_collect_runs")).fetchone()
        return snap[0], runs[0]

    snap, runs = db(run())
    assert snap == 0, "취소됐는데 부분 스냅샷이 남았다"
    assert runs == 0, "취소를 정상 실패 회차로 기록했다"
    assert stub_fetch["fetch"] == 1


# ── 9. 환경변수 안전성 (차단 조건 3) ───────────────────────────────────────
@pytest.mark.parametrize("env,raw,attr,expected", [
    ("RISING_SNAPSHOT_TX_BUDGET_SECONDS", "0", "SNAPSHOT_TX_BUDGET_SECONDS", 8.0),
    ("RISING_SNAPSHOT_TX_BUDGET_SECONDS", "-3", "SNAPSHOT_TX_BUDGET_SECONDS", 8.0),
    ("RISING_SNAPSHOT_TX_BUDGET_SECONDS", "abc", "SNAPSHOT_TX_BUDGET_SECONDS", 8.0),
    ("RISING_SNAPSHOT_TX_BUDGET_SECONDS", "nan", "SNAPSHOT_TX_BUDGET_SECONDS", 8.0),
    ("RISING_SNAPSHOT_TX_BUDGET_SECONDS", "inf", "SNAPSHOT_TX_BUDGET_SECONDS", 8.0),
    ("RISING_SNAPSHOT_TX_BUDGET_SECONDS", "99999", "SNAPSHOT_TX_BUDGET_SECONDS", 8.0),
    ("RISING_SNAPSHOT_TX_BUDGET_SECONDS", "4.5", "SNAPSHOT_TX_BUDGET_SECONDS", 4.5),
    ("RISING_SNAPSHOT_TX_ATTEMPTS", "0", "SNAPSHOT_TX_ATTEMPTS", 3),
    ("RISING_SNAPSHOT_TX_ATTEMPTS", "-1", "SNAPSHOT_TX_ATTEMPTS", 3),
    ("RISING_SNAPSHOT_TX_ATTEMPTS", "999", "SNAPSHOT_TX_ATTEMPTS", 3),
    ("RISING_SNAPSHOT_TX_BUSY_TIMEOUT_MS", "0", "SNAPSHOT_TX_BUSY_TIMEOUT_MS", 2000),
    ("RISING_SNAPSHOT_TX_BUSY_TIMEOUT_MS", "x", "SNAPSHOT_TX_BUSY_TIMEOUT_MS", 2000),
])
def test_env_bounds_fall_back(monkeypatch, env, raw, attr, expected):
    import importlib
    monkeypatch.setenv(env, raw)
    importlib.reload(rc)
    try:
        assert getattr(rc, attr) == expected
    finally:
        monkeypatch.undo()
        importlib.reload(rc)


@pytest.mark.parametrize("raw,expected", [
    ("", (5.0, 15.0, 30.0)),
    ("1,2", (1.0, 2.0)),
    ("-5,10", (5.0, 15.0, 30.0)),
    ("abc", (5.0, 15.0, 30.0)),
    ("inf", (5.0, 15.0, 30.0)),
    ("nan", (5.0, 15.0, 30.0)),
    ("1,2,3,4,5,6,7,8,9,10,11", (5.0, 15.0, 30.0)),
    ("999", (5.0, 15.0, 30.0)),
])
def test_recovery_waits_validation(monkeypatch, raw, expected):
    import importlib
    monkeypatch.setenv("RISING_SNAPSHOT_RECOVERY_WAITS", raw)
    importlib.reload(rc)
    try:
        assert rc.SNAPSHOT_RECOVERY_WAITS == expected
    finally:
        monkeypatch.undo()
        importlib.reload(rc)


def test_recovery_waits_are_trimmed_to_fit_the_cycle(monkeypatch):
    """복구가 수집 주기의 절반을 넘기면 뒤에서부터 잘라낸다."""
    import importlib
    monkeypatch.setenv("RISING_SNAPSHOT_TX_BUDGET_SECONDS", "50")
    monkeypatch.setenv("RISING_SNAPSHOT_RECOVERY_WAITS", "100,100,100")
    importlib.reload(rc)
    try:
        worst = (rc.SNAPSHOT_TX_BUDGET_SECONDS * (len(rc.SNAPSHOT_RECOVERY_WAITS) + 1)
                 + sum(rc.SNAPSHOT_RECOVERY_WAITS))
        assert worst <= rc.COLLECT_INTERVAL / 2, (worst, rc.SNAPSHOT_RECOVERY_WAITS)
    finally:
        monkeypatch.undo()
        importlib.reload(rc)


# ── 10. 트랜잭션 정합성 (차단 조건 4) ──────────────────────────────────────
def test_live_count_matches_snapshot_rows(db):
    db(_wipe())
    db(_persist(_rows(_lives(777))))

    async def check():
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT live_count FROM rising_collect_runs WHERE collected_at=?", (NOW,))).fetchone()
        s = await (await c.execute(
            "SELECT COUNT(*) FROM rising_live_snapshots WHERE collected_at=?", (NOW,))).fetchone()
        return int(r["live_count"]), s[0]

    lc, rows = db(check())
    assert lc == rows == 777


def test_concurrent_same_collected_at_ends_with_one_set(db):
    """두 작업이 같은 collected_at을 동시에 저장해도 최종은 한 세트다."""
    async def run():
        await _wipe()
        rows = _rows(_lives(200))
        await asyncio.gather(_persist(rows), _persist(rows))
        return await _counts()

    snap, runs, ok = db(run())
    assert (snap, runs, ok) == (200, 1, 1), f"중복: 스냅샷 {snap} 회차 {runs}"


def test_empty_lives_never_reaches_persist(db, monkeypatch):
    """빈 수집은 기존 계약대로 **성공으로 기록하지 않는다**(ok=0, 스냅샷 0)."""
    async def empty_fetch(client):
        return [], "라이브 0건", {"pages": 1, "api_calls": 1}

    monkeypatch.setattr(rc, "_fetch_all_lives", empty_fetch)
    monkeypatch.setattr(rc, "_enrich_top", _noop)
    db(_wipe())
    n, note = db(rc.collect_once())
    assert n == 0

    async def check():
        c = await database.get_db()
        s = await (await c.execute("SELECT COUNT(*) FROM rising_live_snapshots")).fetchone()
        r = await (await c.execute(
            "SELECT COUNT(*), MAX(ok) FROM rising_collect_runs")).fetchone()
        return s[0], r[0], r[1]

    snap, runs, ok = db(check())
    assert snap == 0 and runs == 1 and ok == 0, (snap, runs, ok)


# ── 11. 스케줄러 계약 ──────────────────────────────────────────────────────
def test_scheduler_uses_fixed_start_interval():
    """복구 82초가 다음 회차를 미는지 판단하려면 스케줄 방식이 확정돼야 한다.
    `wait = max(MIN_GAP, INTERVAL - elapsed)` = **시작 간격 고정**이다."""
    import inspect
    src = inspect.getsource(rc.start_collector)
    assert "COLLECT_INTERVAL - elapsed" in src, src
    assert "MIN_GAP_SECONDS" in src
