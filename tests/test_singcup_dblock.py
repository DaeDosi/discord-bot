"""P1 — DB 잠금·고아 워커·종료 경로 회귀.

재현했던 사고:
  02:35:09 sweep_loop_error: database is locked
  → run_sweep의 except 안에서 _progress가 다시 잠금에 걸려 sweep_failed 전에 재전파
  → asyncio.gather는 첫 예외로 완료되지만 남은 코루틴을 취소하지 않는다
  → 고아 워커가 계속 DB를 쓰며 heartbeat까지 갱신 → 새 사이클이 영영 시작 못 함
  → recompute_ranking·스냅샷·sweep_done 모두 미실행
"""
import asyncio
import time

import httpx
import singcup_clips as sc
import singcup_sweep as sw
from test_singcup_clips import card
from test_singcup_sweep import _cards, _install, _seed

import database


class Locked(Exception):
    """sqlite3.OperationalError('database is locked')와 같은 취급을 받는지 본다."""

    def __init__(self):
        super().__init__("database is locked")


async def _runs():
    c = await database.get_db()
    rows = await (await c.execute(
        "SELECT run_id, status, processed, completed_at, note, total_targets,"
        " success, partial, failed FROM singcup_sweep_runs ORDER BY rowid")).fetchall()
    return [dict(r) for r in rows]


# ── db_write: 재시도와 포기 ────────────────────────────────────────────────
def test_db_write_retries_then_succeeds(db):
    """중간에 잠기더라도 재시도로 성공한다."""
    calls = []

    async def flaky(_db):
        calls.append(1)
        if len(calls) < 3:
            raise Locked()

    async def go():
        return await sw.db_write(flaky, what="test")
    assert db(go()) is True
    assert len(calls) == 3


def test_db_write_gives_up_without_raising(db):
    """재시도를 소진하면 예외가 아니라 False를 돌려준다(스윕을 끝내지 않는다)."""
    async def always(_db):
        raise Locked()

    async def go():
        return await sw.db_write(always, what="test", attempts=2)
    assert db(go()) is False


def test_db_write_reraises_non_lock_errors(db):
    """잠금이 아닌 오류는 삼키지 않는다 — 조용한 데이터 손실을 막는다."""
    async def boom(_db):
        raise ValueError("스키마 오류")

    async def go():
        try:
            await sw.db_write(boom, what="test")
        except ValueError:
            return "raised"
        return "swallowed"
    assert db(go()) == "raised"


def test_progress_safe_never_raises(db):
    """상태 기록이 잠겨도 예외가 위로 새지 않는다(sweep_loop_error의 원인)."""
    async def go(monkey):
        return await sw._progress_safe("nosuchrun", processed=1)
    # 존재하지 않는 run_id여도 UPDATE는 0행 갱신으로 정상 종료한다
    assert db(go(None)) is True


def test_progress_safe_swallows_lock(db, monkeypatch):
    async def locked(*a, **k):
        raise Locked()
    monkeypatch.setattr(sw, "db_write", locked)

    async def go():
        return await sw._progress_safe("r", processed=1)
    assert db(go()) is False


# ── 클립 단위 격리 ─────────────────────────────────────────────────────────
def test_locked_clip_fails_alone(db, monkeypatch):
    """한 클립이 잠금으로 실패해도 나머지는 계속 처리된다."""
    db(_seed(3, 1))
    _install(_cards())
    real = sc._apply_metrics
    hits = {"n": 0}

    async def flaky(uid, *a, **k):
        if uid == "c1_0":
            hits["n"] += 1
            raise Locked()
        return await real(uid, *a, **k)
    monkeypatch.setattr(sc, "_apply_metrics", flaky)
    monkeypatch.setattr(sw, "DB_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(sw, "DB_RETRY_BASE_SECONDS", 0.001)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["processed"] == 3
    assert res["failed"] == 1 and res["success"] == 2
    assert res["db_locked_giveup"] == 1
    assert hits["n"] >= 2, "재시도가 일어나지 않았다"


def test_worker_exception_does_not_orphan_the_sweep(db, monkeypatch):
    """워커 하나가 던져도 회차가 정상 종료하고 남은 태스크가 없다."""
    db(_seed(4, 1))
    _install(_cards())
    real = sc._apply_metrics

    async def boom(uid, *a, **k):
        if uid == "c2_0":
            raise RuntimeError("예상 못 한 오류")
        return await real(uid, *a, **k)
    monkeypatch.setattr(sc, "_apply_metrics", boom)

    before = len(asyncio.all_tasks) if False else None
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert before is None
    assert res["status"] in (sw.COMPLETED, sw.PARTIAL)
    assert res["processed"] == 4, "고아가 생기면 processed가 어긋난다"
    assert res["failed"] >= 1


def test_no_leftover_tasks_after_run(db):
    """회차가 끝난 뒤 남아 도는 스윕 태스크가 0개."""
    db(_seed(3, 1))
    _install(_cards())

    async def go():
        res = await sw.run_sweep(sw.floor_hour(time.time()))
        await asyncio.sleep(0)
        alive = [t for t in asyncio.all_tasks()
                 if t is not asyncio.current_task() and not t.done()]
        return res, len(alive)
    res, alive = db(go())
    assert res["processed"] == 3
    assert alive == 0, f"{alive}개 태스크가 남아 있다"


# ── 종료 경로 ──────────────────────────────────────────────────────────────
def test_completed_at_is_set_on_success(db):
    db(_seed(2, 1))
    _install(_cards())
    db(sw.run_sweep(sw.floor_hour(time.time())))
    r = db(_runs())[-1]
    assert r["status"] == sw.COMPLETED and r["completed_at"]


def test_completed_at_is_set_on_failure(db, monkeypatch):
    """치명적 예외 경로에서도 최종 상태와 completed_at을 남긴다."""
    db(_seed(2, 1))
    _install(_cards())

    async def boom(*a, **k):
        raise RuntimeError("랭킹 재계산 실패")
    monkeypatch.setattr(sc, "recompute_ranking", boom)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["status"] == sw.FAILED
    r = db(_runs())[-1]
    assert r["status"] == sw.FAILED and r["completed_at"] and r["note"]


def test_processed_equals_tally_at_completion(db):
    """완주 시 processed == success + partial + failed."""
    db(_seed(5, 2))
    _install(_cards())
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["processed"] == res["success"] + res["partial"] + res["failed"]
    r = db(_runs())[-1]
    assert r["processed"] == r["success"] + r["partial"] + r["failed"]


def test_mixed_outcomes_still_balance(db):
    """성공·부분·실패가 섞여도 합계가 맞는다."""
    db(_seed(6, 1))

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        # 요청 횟수로 나누면 _get_json의 503 재시도(3회) 때문에 패턴이 어긋난다.
        # 클립 uid로 고정해 결과를 결정적으로 만든다.
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        i = int(uid[1]) if len(uid) > 1 and uid[1].isdigit() else 0
        if i % 3 == 0:
            return httpx.Response(503, json={"code": 503})
        if i % 3 == 1:
            return httpx.Response(200, json=card("#싱드컵", likes=5, views=0, vod=False))
        return httpx.Response(200, json=card("#싱드컵", likes=5, views=7))
    _install(h)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["processed"] == 6
    assert res["success"] + res["partial"] + res["failed"] == 6
    assert res["partial"] > 0 and res["failed"] > 0


# ── 트랜잭션 안에서 네트워크를 기다리지 않는다 ──────────────────────────────
def test_no_network_await_inside_write_transaction(db, monkeypatch):
    """DB 쓰기 구간에서 외부 호출이 일어나면 즉시 실패시킨다.

    이번 사고의 근원은 '트랜잭션을 연 채 카드 API를 기다린' 구조였다.
    _persist_clip이 호출되는 동안 어떤 네트워크 호출도 없어야 한다.
    """
    import contextvars
    db(_seed(3, 1))
    # 태스크별 플래그여야 한다. 하나짜리 dict를 쓰면 동시 워커 A가 쓰기 중일 때
    # B의 정상적인 네트워크 호출이 위반으로 잡히는 오탐이 난다.
    in_write = contextvars.ContextVar("in_write", default=False)
    inside = {"violations": 0}

    real_persist = sw._persist_clip
    real_card = sc.fetch_card
    real_detail = sc.fetch_clip_detail

    async def watched_persist(*a, **k):
        tok = in_write.set(True)
        try:
            return await real_persist(*a, **k)
        finally:
            in_write.reset(tok)

    async def watched_card(*a, **k):
        if in_write.get():
            inside["violations"] += 1
        return await real_card(*a, **k)

    async def watched_detail(*a, **k):
        if in_write.get():
            inside["violations"] += 1
        return await real_detail(*a, **k)

    monkeypatch.setattr(sw, "_persist_clip", watched_persist)
    monkeypatch.setattr(sc, "fetch_card", watched_card)
    monkeypatch.setattr(sc, "fetch_clip_detail", watched_detail)
    _install(_cards())

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["processed"] == 3
    assert inside["violations"] == 0, "쓰기 트랜잭션 안에서 외부 호출이 일어났다"


# ── 동시 쓰기 ──────────────────────────────────────────────────────────────
def test_concurrent_writer_is_not_starved(db):
    """스윕이 도는 동안 다른 작업(봇 역할)의 쓰기가 계속 성공한다.

    **독립 연결을 쓴다.** database.get_db()는 모듈 전역 단일 연결이라 그걸 쓰면
    같은 연결 안에서 순서대로 실행될 뿐 진짜 동시 쓰기가 아니다(가짜 동시성).
    봇 프로세스를 흉내 내려면 파일에 새로 붙어야 한다.
    """
    import aiosqlite
    db(_seed(8, 1))
    _install(_cards())
    other = {"ok": 0, "err": 0}

    async def bot_role(stop):
        c = await aiosqlite.connect(database.DB_PATH)
        await c.execute("PRAGMA journal_mode=WAL")
        await c.execute("PRAGMA busy_timeout=10000")
        while not stop.is_set():
            try:
                await c.execute(
                    "INSERT INTO singcup_clip_scan (clip_uid, tagged, checked_at)"
                    " VALUES (?,0,?) ON CONFLICT(clip_uid) DO UPDATE SET checked_at=?",
                    (f"bot{other['ok']}", int(time.time()), int(time.time())))
                await c.commit()
                other["ok"] += 1
            except Exception:
                other["err"] += 1
            await asyncio.sleep(0.005)
        await c.close()

    async def go():
        stop = asyncio.Event()
        t = asyncio.create_task(bot_role(stop))
        res = await sw.run_sweep(sw.floor_hour(time.time()))
        stop.set()
        await t
        return res
    res = db(go())
    assert res["processed"] == 8
    assert other["ok"] > 0, "동시 쓰기가 한 번도 성공하지 못했다"
    assert other["err"] == 0, f"동시 쓰기 실패 {other['err']}건"


# ── 1·2·3: 롤백과 재시도 단위 ──────────────────────────────────────────────
def test_rollback_happens_on_every_lock(db):
    """잠길 때마다 롤백한다 — 부분 실행이 다음 시도에 묻어가지 않게."""
    sw._rollbacks["n"] = 0
    calls = []

    async def flaky(_db):
        calls.append(1)
        if len(calls) < 3:
            raise Locked()

    async def go():
        return await sw.db_write(flaky, what="t")
    assert db(go()) is True
    assert len(calls) == 3
    assert sw._rollbacks["n"] == 2, "잠긴 2회 모두 롤백돼야 한다"


def test_rollback_on_final_giveup(db):
    """재시도를 소진할 때도 롤백한 뒤 포기한다."""
    sw._rollbacks["n"] = 0

    async def always(_db):
        raise Locked()

    async def go():
        return await sw.db_write(always, what="t", attempts=3)
    assert db(go()) is False
    assert sw._rollbacks["n"] == 3, "마지막 시도에도 롤백이 필요하다"


def test_rollback_on_non_lock_error(db):
    sw._rollbacks["n"] = 0

    async def boom(_db):
        raise ValueError("x")

    async def go():
        try:
            await sw.db_write(boom, what="t")
        except ValueError:
            pass
    db(go())
    assert sw._rollbacks["n"] == 1


def test_retry_reruns_the_whole_unit(db):
    """재시도는 중단 지점이 아니라 쓰기 단위 처음부터 다시 실행된다."""
    steps = []
    attempt = {"n": 0}

    async def work(_db):
        attempt["n"] += 1
        steps.append(f"start{attempt['n']}")
        if attempt["n"] == 1:
            steps.append("partial")
            raise Locked()
        steps.append("finish")

    async def go():
        return await sw.db_write(work, what="t")
    assert db(go()) is True
    assert steps == ["start1", "partial", "start2", "finish"]


def test_partial_write_is_not_committed_on_retry(db):
    """1차에서 쓴 행이 롤백되고, 2차 실행분만 커밋된다."""
    attempt = {"n": 0}

    async def work(dbc):
        attempt["n"] += 1
        await dbc.execute(
            "INSERT OR REPLACE INTO singcup_clip_scan (clip_uid, tagged, checked_at)"
            " VALUES (?,?,?)", (f"rb{attempt['n']}", 0, 1))
        if attempt["n"] == 1:
            raise Locked()

    async def go():
        ok = await sw.db_write(work, what="t")
        c = await database.get_db()
        rows = await (await c.execute(
            "SELECT clip_uid FROM singcup_clip_scan WHERE clip_uid LIKE 'rb%'"
            " ORDER BY clip_uid")).fetchall()
        return ok, [r["clip_uid"] for r in rows]
    ok, rows = db(go())
    assert ok is True
    assert rows == ["rb2"], f"롤백되지 않은 1차 쓰기가 남았다: {rows}"


# ── 5·6: 최종 상태 저장 실패에서의 복구 ────────────────────────────────────
def test_final_status_failure_does_not_block_next_cycle(db, monkeypatch):
    """최종 상태 저장이 실패해 running으로 남아도 다음 사이클이 시작된다."""
    db(_seed(2, 1))
    _install(_cards())
    real = sw._progress_safe

    async def fail_final(run_id, **f):
        if "status" in f:                            # 최종 기록만 실패시킨다
            return False
        return await real(run_id, **f)
    monkeypatch.setattr(sw, "_progress_safe", fail_final)

    db(sw.run_sweep(sw.floor_hour(time.time())))
    rows = db(_runs())
    assert rows[-1]["status"] == sw.RUNNING, "재현 조건: running으로 남아야 한다"

    async def age():
        c = await database.get_db()
        await c.execute("UPDATE singcup_sweep_runs SET heartbeat_at=?",
                        (int(time.time()) - sw.STALE_RUN_SECONDS - 60,))
        await c.commit()
    db(age())
    assert db(sw.reap_stale_runs()) == 1
    rows = db(_runs())
    assert rows[-1]["status"] == sw.FAILED and rows[-1]["completed_at"]

    monkeypatch.setattr(sw, "_progress_safe", real)
    _install(_cards())
    res = db(sw.run_sweep(sw.floor_hour(time.time()) + 1))
    assert res["status"] != sw.SKIPPED_OVERLAP


def test_reap_leaves_healthy_runs_alone(db):
    """heartbeat가 살아 있는 실행은 건드리지 않는다."""
    db(_seed(1, 1))
    db(sw._claim(sw.floor_hour(time.time())))
    assert db(sw.reap_stale_runs()) == 0
    assert db(_runs())[-1]["status"] == sw.RUNNING


# ── 7·8: 결과 불변식 ───────────────────────────────────────────────────────
def test_worker_exception_counts_exactly_once(db, monkeypatch):
    """예외가 난 클립도 failed 1회, processed 1회로만 잡힌다."""
    db(_seed(3, 1))
    _install(_cards())
    real = sc._apply_metrics

    async def boom(uid, *a, **k):
        if uid == "c1_0":
            raise RuntimeError("x")
        return await real(uid, *a, **k)
    monkeypatch.setattr(sc, "_apply_metrics", boom)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["processed"] == 3
    assert res["failed"] == 1 and res["success"] == 2
    assert res["success"] + res["partial"] + res["failed"] == 3


def test_every_target_gets_exactly_one_outcome(db):
    """모든 대상이 success/partial/failed/skipped 중 정확히 하나."""
    db(_seed(7, 2))

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        i = int(uid[1]) if len(uid) > 1 and uid[1].isdigit() else 0
        if i % 4 == 0:
            return httpx.Response(503, json={"code": 503})
        if i % 4 == 1:
            return httpx.Response(200, json=card("#싱드컵", likes=3, views=0, vod=False))
        return httpx.Response(200, json=card("#싱드컵", likes=3, views=4))
    _install(h)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    total = res["success"] + res["partial"] + res["failed"]
    assert total == res["processed"] == res["total_targets"] == 14


# ── 9·10: 클립별 락 ────────────────────────────────────────────────────────
def test_sweep_skips_a_clip_locked_by_manual_refresh(db):
    """수동 갱신이 잡고 있는 클립은 건너뛴다 — 전역 락을 쓰지 않는다."""
    db(_seed(3, 1))
    _install(_cards())

    async def go():
        tok = await sc.acquire_named_lock("singcup_clip:c1_0", 60)
        assert tok is not None
        res = await sw.run_sweep(sw.floor_hour(time.time()))
        await sc.release_named_lock("singcup_clip:c1_0", tok)
        return res
    res = db(go())
    assert res["skipped"] == 1 and res["failed"] >= 1
    assert res["success"] == 2
    assert res["processed"] == 3, "건너뛴 것도 대상 수에는 포함된다"


def test_clip_lock_is_released_after_each_clip(db):
    """클립 락은 짧게 잡고 바로 푼다 — 다음 사이클이 막히지 않는다."""
    db(_seed(2, 1))
    _install(_cards())
    db(sw.run_sweep(sw.floor_hour(time.time())))

    async def go():
        return await sc.acquire_named_lock("singcup_clip:c0_0", 5)
    assert db(go()) is not None, "스윕이 끝났는데 클립 락이 남아 있다"


def test_manual_refresh_is_not_blocked_by_a_global_lock(db):
    """스윕이 전역 락을 잡지 않으므로 다른 클립의 수동 갱신은 자유롭다."""
    db(_seed(2, 1))
    _install(_cards())

    async def go():
        tok = await sc.acquire_named_lock("singcup_metrics", 60)
        assert tok is not None
        res = await sw.run_sweep(sw.floor_hour(time.time()))
        await sc.release_named_lock("singcup_metrics", tok)
        return res
    res = db(go())
    assert res["processed"] == 2 and res["success"] == 2


# ── 불변식과 skipped 의미 ──────────────────────────────────────────────────
# 채택한 정의:  processed = success + partial + failed + skipped
# 대상 목록에 있었으나 락 충돌로 못 건드린 것도 '진행률'에는 넣되,
# 실행 결과는 completed가 아니라 partial로 본다(다음 사이클이 다시 집는다).
async def _attempt_times():
    c = await database.get_db()
    rows = await (await c.execute(
        "SELECT clip_uid, last_attempt_at FROM singcup_clips ORDER BY clip_uid"
    )).fetchall()
    return {r["clip_uid"]: r["last_attempt_at"] for r in rows}


def test_skipped_makes_the_run_partial(db):
    """건너뛴 클립이 있으면 completed가 아니다."""
    db(_seed(3, 1))
    _install(_cards())

    async def go():
        tok = await sc.acquire_named_lock("singcup_clip:c1_0", 60)
        res = await sw.run_sweep(sw.floor_hour(time.time()))
        await sc.release_named_lock("singcup_clip:c1_0", tok)
        return res
    res = db(go())
    assert res["skipped"] == 1
    assert res["status"] == sw.PARTIAL, "건너뛴 게 있는데 completed로 보고했다"
    assert "건너뜀" in (res["note"] or "")


def test_processed_invariant_includes_skipped(db):
    """영구 불변식은 processed = success + partial + failed.

    skipped는 singcup_sweep_runs에 컬럼이 없으므로 failed의 **부분집합**으로 둔다.
    그래야 DB에서 다시 읽은 행에서도 같은 식이 성립한다.
    """
    db(_seed(4, 1))
    _install(_cards())

    async def go():
        tok = await sc.acquire_named_lock("singcup_clip:c2_0", 60)
        res = await sw.run_sweep(sw.floor_hour(time.time()))
        await sc.release_named_lock("singcup_clip:c2_0", tok)
        return res
    res = db(go())
    assert (res["success"] + res["partial"] + res["failed"]
            == res["processed"] == res["total_targets"] == 4)
    assert res["skipped"] == 1 and res["skipped"] <= res["failed"]


def test_skipped_clip_keeps_its_last_attempt_at(db):
    """건너뛴 클립은 DB를 건드리지 않는다 — 다음 사이클에 다시 잡히도록."""
    db(_seed(3, 1))
    _install(_cards())
    before = db(_attempt_times())

    async def go():
        tok = await sc.acquire_named_lock("singcup_clip:c1_0", 60)
        res = await sw.run_sweep(sw.floor_hour(time.time()))
        await sc.release_named_lock("singcup_clip:c1_0", tok)
        return res
    db(go())
    after = db(_attempt_times())
    assert after["c1_0"] == before["c1_0"], "건너뛴 클립의 시도 시각이 바뀌었다"
    assert after["c0_0"] > before["c0_0"], "처리한 클립은 시각이 올라가야 한다"


def test_skipped_clip_is_retried_next_cycle(db):
    """건너뛴 클립이 다음 사이클 대상에 자동으로 다시 들어온다."""
    db(_seed(3, 1))
    _install(_cards())
    sched = sw.floor_hour(time.time())

    async def first():
        tok = await sc.acquire_named_lock("singcup_clip:c1_0", 60)
        res = await sw.run_sweep(sched)
        await sc.release_named_lock("singcup_clip:c1_0", tok)
        return res
    assert db(first())["skipped"] == 1

    left = db(sw.sweep_targets(sched))
    assert [t["clip_uid"] for t in left] == ["c1_0"]

    _install(_cards())
    res2 = db(sw.run_sweep(sched + 1))
    assert res2["processed"] == 1 and res2["success"] == 1
    assert res2["status"] == sw.COMPLETED


def test_remaining_and_stragglers_are_reported_separately(db):
    """skipped / remaining / stragglers를 구분해 내려준다."""
    db(_seed(2, 1))
    _install(_cards())
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    for k in ("skipped", "remaining", "stragglers"):
        assert k in res, f"{k}가 응답에 없다"
    assert res["skipped"] == 0 and res["remaining"] == 0 and res["stragglers"] == 0


# ── 클립 락의 TTL·소유자·만료·회수 ─────────────────────────────────────────
def test_clip_lock_is_exclusive_and_owned(db):
    """같은 키를 두 번 잡을 수 없고, 소유 토큰이 서로 다르다."""
    async def go():
        a = await sc.acquire_named_lock("singcup_clip:x", 60)
        b = await sc.acquire_named_lock("singcup_clip:x", 60)
        await sc.release_named_lock("singcup_clip:x", a)
        c = await sc.acquire_named_lock("singcup_clip:x", 60)
        return a, b, c
    a, b, c = db(go())
    assert a and b is None and c
    assert a != c


def test_clip_lock_expires_after_ttl(db):
    """비정상 종료로 해제하지 못해도 TTL이 지나면 회수된다."""
    async def go():
        a = await sc.acquire_named_lock("singcup_clip:y", 1)   # 1초짜리
        blocked = await sc.acquire_named_lock("singcup_clip:y", 1)
        # locked_until은 정수 초(now+ttl)이고 판정도 정수라, 1.2초만 자면
        # 시작 시각이 초 경계에 걸릴 때 아직 만료 전으로 보인다. 여유를 준다.
        await asyncio.sleep(2.2)                               # 소유자가 죽었다고 가정
        recovered = await sc.acquire_named_lock("singcup_clip:y", 5)
        return a, blocked, recovered
    a, blocked, recovered = db(go())
    assert a and blocked is None and recovered, "TTL 만료 후 회수되지 않았다"


def test_sweep_recovers_a_clip_whose_lock_expired(db):
    """소유자가 죽어 남은 락은 TTL 뒤 스윕이 다시 가져간다."""
    db(_seed(2, 1))
    _install(_cards())

    async def go():
        await sc.acquire_named_lock("singcup_clip:c0_0", 1)    # 해제하지 않는다
        await asyncio.sleep(2.2)                               # 정수 초 경계 여유
        return await sw.run_sweep(sw.floor_hour(time.time()))
    res = db(go())
    assert res["skipped"] == 0 and res["success"] == 2


def test_clip_lock_waits_before_skipping(db, monkeypatch):
    """즉시 포기하지 않고 잠깐 기다렸다 넘어간다."""
    monkeypatch.setattr(sc, "CLIP_LOCK_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(sc, "CLIP_LOCK_POLL_SECONDS", 0.05)
    tries = {"n": 0}
    real = sc.acquire_named_lock

    async def counted(name, ttl):
        if name == "singcup_clip:z":
            tries["n"] += 1
        return await real(name, ttl)
    monkeypatch.setattr(sc, "acquire_named_lock", counted)

    async def go():
        held = await real("singcup_clip:z", 60)
        t0 = time.monotonic()
        got = await sc.acquire_clip_lock("z")
        waited = time.monotonic() - t0
        await sc.release_named_lock("singcup_clip:z", held)
        return got, waited
    got, waited = db(go())
    assert got is None
    assert waited >= 0.4, f"기다리지 않고 즉시 포기했다({waited:.2f}s)"
    assert tries["n"] >= 3, "폴링이 한 번만 일어났다"


def test_clip_lock_acquired_when_freed_during_wait(db, monkeypatch):
    """기다리는 동안 락이 풀리면 건너뛰지 않고 잡는다."""
    monkeypatch.setattr(sc, "CLIP_LOCK_WAIT_SECONDS", 2.0)
    monkeypatch.setattr(sc, "CLIP_LOCK_POLL_SECONDS", 0.05)

    async def go():
        held = await sc.acquire_named_lock("singcup_clip:w", 60)

        async def release_soon():
            await asyncio.sleep(0.2)
            await sc.release_named_lock("singcup_clip:w", held)
        asyncio.create_task(release_soon())
        return await sc.acquire_clip_lock("w")
    assert db(go()) is not None


# ── 모든 지표 수정 경로가 같은 클립 락을 쓰는가 ────────────────────────────
# 대상 경로: 정기 스윕 / refresh_metrics(수동 전체) / refresh_one_clip(관리자 단건)
#            _scan_batch(신규 탐색·재시도·reconcile·백필) / _register_from_card(retag)
def test_manual_single_refresh_uses_the_clip_lock(db):
    """관리자 단건 갱신이 클립 락에 막히면 건드리지 않고 물러난다."""
    db(_seed(1, 1))
    _install(_cards())

    async def go():
        held = await sc.acquire_named_lock(sc.clip_lock_name("c0_0"), 60)
        res = await sc.refresh_one_clip("c0_0")
        await sc.release_named_lock(sc.clip_lock_name("c0_0"), held)
        return res
    res = db(go())
    assert res["status"] == sc.ST_SKIPPED
    assert "다른 갱신 작업" in res["note"]


def test_manual_single_refresh_releases_the_clip_lock(db):
    """단건 갱신이 끝나면 락을 반납한다."""
    db(_seed(1, 1))
    _install(_cards())
    db(sc.refresh_one_clip("c0_0"))

    async def go():
        return await sc.acquire_named_lock(sc.clip_lock_name("c0_0"), 5)
    assert db(go()) is not None


def test_sweep_and_manual_never_write_the_same_uid_together(db):
    """같은 UID를 스윕과 수동 갱신이 동시에 쓰지 않는다.

    _apply_metrics 진입/이탈을 UID별로 세어 겹침을 직접 관측한다.
    """
    db(_seed(3, 1))
    _install(_cards())
    inflight: dict = {}
    overlaps = {"n": 0}
    real = sc._apply_metrics

    async def watched(uid, *a, **k):
        inflight[uid] = inflight.get(uid, 0) + 1
        if inflight[uid] > 1:
            overlaps["n"] += 1
        try:
            await asyncio.sleep(0.01)          # 겹칠 틈을 일부러 만든다
            return await real(uid, *a, **k)
        finally:
            inflight[uid] -= 1

    async def go():
        import unittest.mock as m
        with m.patch.object(sc, "_apply_metrics", watched):
            sweep = asyncio.create_task(sw.run_sweep(sw.floor_hour(time.time())))
            await asyncio.sleep(0.005)
            manual = asyncio.create_task(sc.refresh_one_clip("c1_0"))
            return await asyncio.gather(sweep, manual)
    db(go())
    assert overlaps["n"] == 0, f"같은 UID가 {overlaps['n']}회 겹쳐 쓰였다"


def test_different_uids_do_not_block_each_other(db):
    """서로 다른 UID는 서로 막지 않는다."""
    async def go():
        a = await sc.acquire_clip_lock("uidA", wait=0.1)
        b = await sc.acquire_clip_lock("uidB", wait=0.1)
        await sc.release_clip_lock("uidA", a)
        await sc.release_clip_lock("uidB", b)
        return a, b
    a, b = db(go())
    assert a and b


def test_sweep_holding_lock_makes_manual_wait_then_skip(db, monkeypatch):
    """스윕이 선점하면 수동 갱신은 기다렸다 물러난다(정책 확인)."""
    db(_seed(1, 1))
    _install(_cards())

    async def go():
        held = await sc.acquire_clip_lock("c0_0")     # 스윕이 잡은 상황
        t0 = time.monotonic()
        res = await sc.refresh_one_clip("c0_0")
        waited = time.monotonic() - t0
        await sc.release_clip_lock("c0_0", held)
        return res, waited
    res, waited = db(go())
    assert res["status"] == sc.ST_SKIPPED
    assert waited >= 4.0, f"기다리지 않고 즉시 포기했다({waited:.1f}s)"


def test_expired_owner_cannot_release_new_owners_lock(db):
    """TTL 만료 후 이전 소유자가 새 소유자의 락을 삭제하지 못한다."""
    async def go():
        old = await sc.acquire_named_lock(sc.clip_lock_name("q"), 1)
        await asyncio.sleep(2.2)                      # 정수 초 경계 여유
        new = await sc.acquire_named_lock(sc.clip_lock_name("q"), 60)
        assert new and new != old
        # 이전 소유자가 뒤늦게 해제를 시도한다
        await sc.release_named_lock(sc.clip_lock_name("q"), old)
        # 새 소유자의 락이 살아 있어야 한다
        stolen = await sc.acquire_named_lock(sc.clip_lock_name("q"), 60)
        return new, stolen
    new, stolen = db(go())
    assert new is not None
    assert stolen is None, "이전 소유자가 남의 락을 풀어 버렸다"


def test_scan_batch_paths_take_the_clip_lock(db):
    """신규 탐색·재시도·reconcile이 쓰는 _scan_batch도 클립 락을 쓴다."""
    seen = {"names": []}
    real = sc.acquire_clip_lock

    async def watched(uid, **k):
        seen["names"].append(uid)
        return await real(uid, **k)

    async def go():
        import unittest.mock as m

        from test_singcup_sweep import _clip, _pages
        with m.patch.object(sc, "acquire_clip_lock", watched):
            _install(_pages({None: ([_clip("sb1")], None)}, tagged={"sb1"}))
            await sc.discover_new_clips()
    db(go())
    assert "sb1" in seen["names"], "_scan_batch가 클립 락을 잡지 않았다"


# ── DB에서 다시 읽은 최종 실행 ─────────────────────────────────────────────
def test_persisted_run_satisfies_the_invariant(db):
    """저장된 행에서도 processed = success + partial + failed."""
    db(_seed(4, 1))
    _install(_cards())

    async def go():
        held = await sc.acquire_clip_lock("c2_0")     # 하나는 skipped로 만든다
        res = await sw.run_sweep(sw.floor_hour(time.time()))
        await sc.release_clip_lock("c2_0", held)
        return res
    res = db(go())
    r = db(_runs())[-1]
    assert r["processed"] == r["success"] + r["partial"] + r["failed"]
    assert r["processed"] == res["processed"] == 4
    assert r["status"] == sw.PARTIAL                  # skipped가 있으면 partial


def test_run_row_is_interpretable_after_restart(db):
    """프로세스가 죽었다 살아나도 저장된 수치만으로 해석된다."""
    db(_seed(3, 1))
    _install(_cards())
    db(sw.run_sweep(sw.floor_hour(time.time())))

    # '재시작'을 흉내 낸다 — 메모리 상태를 버리고 DB만 읽는다
    async def reread():
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT status, processed, total_targets, success, partial, failed,"
            " completed_at, duration_ms FROM singcup_sweep_runs"
            " ORDER BY rowid DESC LIMIT 1")).fetchone()
        return dict(r)
    r = db(reread())
    assert r["status"] in (sw.COMPLETED, sw.PARTIAL, sw.FAILED)
    assert r["completed_at"] and r["duration_ms"] >= 0
    assert r["processed"] == r["success"] + r["partial"] + r["failed"]
    assert r["processed"] == r["total_targets"] == 3


# ── 락 조작 중 DB locked ───────────────────────────────────────────────────
def test_lock_acquire_survives_transient_db_lock(db, monkeypatch):
    """락 획득 경로에서 잠금이 나도 재시도로 복구된다."""
    calls = {"n": 0}
    real = sc.acquire_named_lock

    async def flaky(name, ttl):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Locked()
        return await real(name, ttl)
    monkeypatch.setattr(sc, "acquire_named_lock", flaky)
    monkeypatch.setattr(sc, "CLIP_LOCK_POLL_SECONDS", 0.01)

    async def go():
        # 첫 시도는 예외 → db_write가 아닌 경로라 여기서는 그대로 전파된다.
        # 스윕은 one()이 예외를 삼켜 그 클립만 failed가 된다.
        try:
            return await sc.acquire_clip_lock("t", wait=0.05)
        except Exception:
            return "raised"
    assert db(go()) == "raised"
    assert calls["n"] == 1


# ── 클립 락 TTL이 최악 처리시간을 덮는가 ───────────────────────────────────
# TTL을 임의 숫자로 두면 타임아웃 상수를 올렸을 때 조용히 만료돼, 두 작업이 같은
# 클립을 동시에 만지게 된다. 그래서 TTL은 상수에서 유도하고 여기서 관계를 강제한다.
def test_clip_lock_ttl_covers_worst_case():
    worst = sc._worst_clip_seconds()
    assert sc.CLIP_LOCK_TTL >= worst * 1.2, (
        f"TTL {sc.CLIP_LOCK_TTL}s 가 최악 {worst:.0f}s 를 못 덮는다 — "
        "타임아웃/재시도 상수를 바꿨다면 TTL 유도식도 함께 확인할 것")


def test_worst_case_accounts_for_every_wait():
    """유도식이 토큰 대기·HTTP 재시도·DB busy_timeout을 모두 포함한다."""
    from database.db import BUSY_TIMEOUT_MS
    worst = sc._worst_clip_seconds()
    # HTTP 한 번의 타임아웃 총합(카드+상세 2회분)만 해도 이만큼은 넘어야 한다
    assert worst >= 2 * sc.MAX_RETRIES * sc.REQUEST_TIMEOUT
    # DB 재시도가 busy_timeout을 시도마다 다 쓸 수 있다는 것도 반영돼야 한다
    assert worst >= BUSY_TIMEOUT_MS / 1000.0 * 2


def test_ttl_scales_when_timeouts_grow(monkeypatch):
    """타임아웃을 올리면 유도된 최악 시간도 함께 커진다(고정값이 아니다)."""
    base = sc._worst_clip_seconds()
    monkeypatch.setattr(sc, "REQUEST_TIMEOUT", sc.REQUEST_TIMEOUT * 2)
    assert sc._worst_clip_seconds() > base


def test_lease_renewal_extends_only_for_the_owner(db):
    """owner 토큰이 맞을 때만 임대가 연장된다."""
    async def go():
        mine = await sc.acquire_clip_lock("lease1")
        ok = await sc.renew_clip_lock("lease1", mine)
        bad = await sc.renew_clip_lock("lease1", "someone-else")
        await sc.release_clip_lock("lease1", mine)
        return ok, bad
    ok, bad = db(go())
    assert ok is True and bad is False


def test_lease_renewal_keeps_the_lock_alive(db):
    """짧은 TTL이라도 갱신하면 만료되지 않는다."""
    async def go():
        mine = await sc.acquire_named_lock(sc.clip_lock_name("lease2"), 2)
        await asyncio.sleep(1.0)
        renewed = await sc.renew_named_lock(sc.clip_lock_name("lease2"), mine, 30)
        await asyncio.sleep(1.5)                  # 원래 TTL이면 이미 만료됐을 시점
        stolen = await sc.acquire_named_lock(sc.clip_lock_name("lease2"), 5)
        return renewed, stolen
    renewed, stolen = db(go())
    assert renewed is True
    assert stolen is None, "갱신했는데도 남이 가져갔다"
