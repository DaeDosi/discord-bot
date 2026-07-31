"""공유 SQLite 쓰기 트랜잭션의 경계 — 열린 채로 외부를 기다리지 않는다.

배경(실측 2026-08-01 Railway): `loop_error: database is locked`가 **46분 이상**
지속되고 그동안 `sweep_start`·`sweep_done`·`streamers_upserted`·
`rising_collector 완료`가 전부 0이었다. 공개 GET은 200이었지만 그건 WAL 읽기가
가능하다는 뜻일 뿐이다.

공유 aiosqlite 연결은 `isolation_level=''`이라 **첫 DML부터 commit까지 SQLite
쓰기 잠금을 붙든다**. 그런데 `_scan_batch`는 DML 사이에서 `acquire_clip_lock`
(최대 2초 `asyncio.sleep` 폴링)을 돌렸고, `refresh_metrics`는 먼저 끝난 클립의
DML을 연 채 나머지 클립의 HTTP를 전부 기다렸다.

여기서 고정하는 불변식:

    쓰기 트랜잭션이 열려 있는 동안 HTTP·sleep·락 폴링을 하지 않는다.
    허용되는 await는 execute / executemany / commit / rollback 뿐이다.

가짜 동시성(같은 연결 순차 실행)을 쓰지 않는다 — 다른 writer는 **독립 연결**이다.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest
import singcup_clips as sc

import database
from utils.db_write import db_write, reset_write_stats, write_stats

IN = "2026-07-28 12:00:00"


def _item(uid, owner="o1"):
    return {"clipUID": uid, "ownerChannelId": owner, "videoId": f"v{uid}",
            "recId": "", "clipTitle": "t", "thumbnailImageUrl": "",
            "createdDate": IN, "duration": 60, "adult": False, "blindType": None,
            "ownerChannel": {"channelName": "n", "channelImageUrl": "",
                             "verifiedMark": False}}


def _card(desc="#싱드컵"):
    return {"description": desc, "heart_count": 10, "view_count": 100,
            "heart_ok": True, "view_ok": True, "metrics_ok": 1}


def _other_process_write(timeout_ms=1000):
    """**독립 연결**의 짧은 쓰기. 잠겨 있으면 locked를 받는다."""
    c = sqlite3.connect(database.DB_PATH)
    try:
        c.execute(f"PRAGMA busy_timeout={timeout_ms}")
        t = time.perf_counter()
        c.execute("INSERT INTO singcup_locks (name, locked_until, owner) "
                  "VALUES (?,0,'') ON CONFLICT(name) DO UPDATE SET locked_until=0",
                  (f"probe-{time.perf_counter_ns()}",))
        c.commit()
        return True, (time.perf_counter() - t) * 1000
    except Exception:
        return False, (time.perf_counter() - t) * 1000
    finally:
        c.close()


# ── A. 외부 API를 오래 기다리는 동안 트랜잭션이 열려 있으면 안 된다 ─────────
def test_no_write_transaction_is_held_during_card_fetch(db, monkeypatch):
    """클립 A의 카드 조회가 오래 걸려도 다른 writer가 즉시 쓸 수 있어야 한다."""
    started = asyncio.Event()

    async def slow_fetch(client, item):
        started.set()
        await asyncio.sleep(1.5)          # 외부 API 지연
        return _card()

    monkeypatch.setattr(sc, "fetch_card", slow_fetch)

    async def go():
        task = asyncio.ensure_future(
            sc._scan_batch(None, [_item("a"), _item("b", "o2")], int(time.time())))
        await started.wait()
        await asyncio.sleep(0.2)          # HTTP 한복판
        ok, ms = await asyncio.get_running_loop().run_in_executor(
            None, _other_process_write)
        res = await task
        return ok, ms, res

    ok, ms, res = db(go())
    assert ok, f"카드 조회 중에 다른 연결의 쓰기가 막혔다 ({ms:.0f}ms)"
    assert ms < 1000, f"쓰기가 {ms:.0f}ms 대기했다 — 트랜잭션이 열려 있었다"
    assert res[0] == 2, "태그된 클립 2건이 저장돼야 한다"


def test_scan_batch_acquires_clip_locks_before_the_transaction(db, monkeypatch):
    """clip lock 폴링이 트랜잭션 안에서 일어나면 안 된다."""
    order: list[str] = []

    async def fetch(client, item):
        return _card()

    orig_acquire = sc.acquire_clip_lock

    async def spy_acquire(uid, **kw):
        order.append("lock")
        return await orig_acquire(uid, **kw)

    orig_write = db_write

    async def spy_write(get_db_fn, fn, **kw):
        order.append("tx")
        return await orig_write(get_db_fn, fn, **kw)

    monkeypatch.setattr(sc, "fetch_card", fetch)
    monkeypatch.setattr(sc, "acquire_clip_lock", spy_acquire)
    monkeypatch.setattr(sc, "db_write", spy_write)

    db(sc._scan_batch(None, [_item("a"), _item("b", "o2")], int(time.time())))
    assert "tx" in order
    assert order.index("tx") > 0, "트랜잭션 전에 락을 받지 않았다"
    assert all(o == "lock" for o in order[:order.index("tx")])
    assert "lock" not in order[order.index("tx"):], "트랜잭션 안에서 락을 받았다"


# ── B. 여러 코루틴이 같은 공유 연결에 동시에 쓰기 ──────────────────────────
def test_concurrent_writers_are_serialized_without_losing_commits(db):
    reset_write_stats()

    async def go():
        async def one(i):
            async def w(conn):
                await conn.execute(
                    "INSERT INTO singcup_locks (name, locked_until, owner) VALUES (?,?,'')"
                    " ON CONFLICT(name) DO UPDATE SET locked_until=excluded.locked_until",
                    (f"n{i}", i))
            return await db_write(database.get_db, w, what=f"t{i}")

        res = await asyncio.gather(*[one(i) for i in range(25)])
        assert all(res)
        conn = await database.get_db()
        return (await (await conn.execute(
            "SELECT COUNT(*) c FROM singcup_locks WHERE name LIKE 'n%'")).fetchone())["c"]

    n = db(go())
    assert n == 25, "커밋 누락 또는 중복"
    s = write_stats()
    assert s["writes"] == 25 and s["rollbacks"] == 0


# ── C. 취소되어도 롤백된다 ─────────────────────────────────────────────────
def test_cancellation_during_dml_rolls_back_and_connection_recovers(db):
    reset_write_stats()

    async def go():
        gate = asyncio.Event()

        async def w(conn):
            await conn.execute(
                "INSERT INTO singcup_locks (name, locked_until, owner) VALUES ('c',1,'')"
                " ON CONFLICT(name) DO UPDATE SET locked_until=1")
            await gate.wait()             # 여기서 취소된다

        task = asyncio.ensure_future(db_write(database.get_db, w, what="cancel-me"))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert write_stats()["rollbacks"] >= 1, "취소 경로에서 롤백하지 않았다"

        # 트랜잭션이 남아 있지 않아야 한다 — 독립 연결이 즉시 쓸 수 있다
        ok, ms = await asyncio.get_running_loop().run_in_executor(
            None, _other_process_write)
        assert ok and ms < 1000, f"취소 후에도 잠금이 남았다 ({ms:.0f}ms)"

        # 다음 쓰기도 정상
        async def w2(conn):
            await conn.execute(
                "INSERT INTO singcup_locks (name, locked_until, owner) VALUES ('after',2,'')"
                " ON CONFLICT(name) DO UPDATE SET locked_until=2")
        assert await db_write(database.get_db, w2, what="after-cancel") is True

    db(go())


# ── D. 다른 연결이 잡고 있을 때 — 제한된 재시도 후 명시적 실패 ─────────────
def test_lock_contention_gives_up_without_leaving_an_open_transaction(db):
    reset_write_stats()
    logs: list[dict] = []

    async def go():
        holder = sqlite3.connect(database.DB_PATH)
        holder.execute("PRAGMA busy_timeout=100")
        holder.execute("INSERT INTO singcup_locks (name, locked_until, owner)"
                       " VALUES ('holder',1,'') ON CONFLICT(name) DO UPDATE SET"
                       " locked_until=1")            # 커밋하지 않는다

        async def w(conn):
            await conn.execute(
                "INSERT INTO singcup_locks (name, locked_until, owner) VALUES ('x',1,'')"
                " ON CONFLICT(name) DO UPDATE SET locked_until=1")

        t = time.perf_counter()
        ok = await db_write(database.get_db, w, what="singcup.scan_batch",
                            attempts=2, log=logs.append)
        elapsed = time.perf_counter() - t
        holder.commit()
        holder.close()
        return ok, elapsed

    ok, elapsed = db(go())
    assert ok is False, "잠겨 있는데 성공으로 보고했다"
    assert elapsed < 60, "영구 대기했다"
    assert write_stats()["rollbacks"] >= 1
    ops = [g for g in logs if g.get("event") == "db_locked"]
    assert ops, "작업명이 담긴 db_locked 로그가 없다"
    assert ops[0]["operation"] == "singcup.scan_batch"
    assert ops[0]["process"] in ("web", "bot") and ops[0]["pid"] > 0
    assert ops[0]["in_transaction"] is False
    give = [g for g in logs if g.get("event") == "db_locked_giveup"]
    assert give and give[0]["operation"] == "singcup.scan_batch"

    # 포기 뒤에도 연결이 깨끗해야 한다
    async def after():
        async def w2(conn):
            await conn.execute(
                "INSERT INTO singcup_locks (name, locked_until, owner) VALUES ('y',1,'')"
                " ON CONFLICT(name) DO UPDATE SET locked_until=1")
        return await db_write(database.get_db, w2, what="after-giveup")
    assert db(after()) is True


# ── E. 잠금으로 못 쓰면 아무것도 저장하지 않고 다음 회차에 다시 본다 ───────
def test_scan_batch_reports_failure_and_persists_nothing_when_locked(db, monkeypatch):
    async def fetch(client, item):
        return _card()

    async def never(*a, **kw):
        return False                      # 잠금으로 포기한 상황

    monkeypatch.setattr(sc, "fetch_card", fetch)
    monkeypatch.setattr(sc, "db_write", never)

    items = [_item("a"), _item("b", "o2")]
    tagged, inserted, failed = db(sc._scan_batch(None, items, int(time.time())))
    assert (tagged, inserted, failed) == (0, 0, 2), "실패를 성공으로 보고했다"

    async def counts():
        conn = await database.get_db()
        a = (await (await conn.execute(
            "SELECT COUNT(*) c FROM singcup_clips")).fetchone())["c"]
        b = (await (await conn.execute(
            "SELECT COUNT(*) c FROM singcup_clip_scan")).fetchone())["c"]
        return a, b

    assert db(counts()) == (0, 0), "저장하지 않았어야 한다"


def test_scan_batch_still_stores_everything_on_the_happy_path(db, monkeypatch):
    """구조를 바꿔도 저장 결과는 같아야 한다(태그·무태그·조회실패)."""
    async def fetch(client, item):
        uid = item["clipUID"]
        if uid == "fail":
            return None
        return _card("#싱드컵" if uid == "tag" else "그냥 설명")

    monkeypatch.setattr(sc, "fetch_card", fetch)
    now = int(time.time())
    res = db(sc._scan_batch(
        None, [_item("tag"), _item("plain", "o2"), _item("fail", "o3")], now))
    assert res == (1, 1, 1)

    async def rows():
        conn = await database.get_db()
        clips = [r["clip_uid"] for r in await (await conn.execute(
            "SELECT clip_uid FROM singcup_clips")).fetchall()]
        scan = {r["clip_uid"]: r["scan_status"] for r in await (await conn.execute(
            "SELECT clip_uid, scan_status FROM singcup_clip_scan")).fetchall()}
        retry = [r["clip_uid"] for r in await (await conn.execute(
            "SELECT clip_uid FROM singcup_clip_retry")).fetchall()]
        return clips, scan, retry

    clips, scan, retry = db(rows())
    assert clips == ["tag"]
    assert scan["tag"] == sc.SCAN_REGISTERED
    assert scan["plain"] == sc.SCAN_UNTAGGED
    assert scan["fail"] == sc.SCAN_FETCH_FAILED
    assert retry == ["fail"]
