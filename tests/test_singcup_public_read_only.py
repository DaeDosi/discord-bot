"""P1.5 — 공개 GET `/api/singcup/main`은 읽기 전용이어야 한다.

실제 장애(2026-07-31 Railway):

    GET /api/singcup/main
      → singcup_router.py  main()
      → load_main_entry()
      → _load_main_uncached()
      → _save_top_movers()
      → db.execute()
      → sqlite3.OperationalError: database is locked
      → 500

봇 프로세스와 백엔드가 같은 SQLite 파일을 쓰는 구조라 잠금은 **정상적으로 일어나는
일**이다. 그때 공개 조회가 죽으면 안 된다. 그래서 조회 경로에서 쓰기 자체를 없앴다.

여기서는 진짜 잠금을 재현한다 — 운영과 같은 **별도 aiosqlite 연결**로 쓰기
트랜잭션을 열어 둔 채 검증한다. 같은 전역 연결에서만 도는 가짜 동시성이 아니다.
"""
import asyncio
import json
import time

import aiosqlite
import pytest
import singcup_clips as sc

import database
from utils import db_write as dw

HOUR = 3600


# ── 헬퍼 ───────────────────────────────────────────────────────────────────
async def _seed(owners=3, *, with_baseline=True):
    """참가자 N명 + (선택) 1시간 전 기준선. 급상승이 실제로 계산되게 만든다."""
    now = int(time.time())
    db = await database.get_db()
    for i in range(owners):
        o, uid = f"o{i}", f"clip{i}"
        await db.execute(
            "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
            " rec_id, clip_title, thumbnail_image_url, description, created_at,"
            " heart_count, view_count, duration, adult, blind_type, metrics_ok,"
            " owner_channel_name, active, missing_scan_count, first_collected_at,"
            " last_collected_at, row_updated_at)"
            " VALUES (?,?,?,?,'','제목','','#싱드컵',?,?,?,60,0,'',1,?,1,0,?,?,?)",
            (uid, sc.EVENT_ID, o, f"v{uid}", now - 3 * HOUR, 100 + i * 10, 500 + i,
             o, now - 3 * HOUR, now, now))
        await db.execute(
            "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
            " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
            " representative_clip_uid, row_updated_at) VALUES (?,?,?,'',0,0,1,?,?)",
            (o, sc.EVENT_ID, o, uid, now))
        if with_baseline:
            at = now - HOUR + 120
            await db.execute(
                "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
                " heart_count, view_count, follower_count, score, rank, collected_at,"
                " snapshot_bucket) VALUES (?,?,?,?,0,0,0,?,?,?)",
                (sc.EVENT_ID, uid, o, 10, i + 1, at, sc.snapshot_bucket(at)))
    await db.commit()


async def _movers_row():
    db = await database.get_db()
    r = await (await db.execute(
        "SELECT payload, base_at, computed_at FROM singcup_top_movers "
        "WHERE event_id=?", (sc.EVENT_ID,))).fetchone()
    return dict(r) if r else None


async def _count(table):
    db = await database.get_db()
    r = await (await db.execute(f"SELECT COUNT(*) n FROM {table}")).fetchone()
    return int(r["n"])


class _WriteSpy:
    """공유 연결의 execute/commit을 감시해 '쓰기 문장'과 커밋 횟수를 센다."""

    WRITE_HEADS = ("insert", "update", "delete", "replace", "create", "drop", "alter")

    def __init__(self):
        self.writes, self.commits = [], 0

    async def install(self, monkeypatch):
        db = await database.get_db()
        real_exec, real_commit = db.execute, db.commit

        async def exec_spy(sql, *a, **kw):
            head = str(sql).strip().split(None, 1)[0].lower() if str(sql).strip() else ""
            if head in self.WRITE_HEADS:
                self.writes.append(str(sql)[:80])
            return await real_exec(sql, *a, **kw)

        async def commit_spy(*a, **kw):
            self.commits += 1
            return await real_commit(*a, **kw)

        monkeypatch.setattr(db, "execute", exec_spy)
        monkeypatch.setattr(db, "commit", commit_spy)
        return self


# ── 1. 공개 조회는 쓰지 않는다 ─────────────────────────────────────────────
def test_cache_miss_performs_no_db_write(db, monkeypatch):
    db(_seed())
    sc.invalidate_main_cache()
    spy = db(_WriteSpy().install(monkeypatch))

    entry, source = db(sc.load_main_entry(3000))

    assert source == "miss"
    assert entry["data"]["summary"]["streamerCount"] == 3
    assert spy.writes == [], f"조회가 DB에 썼다: {spy.writes}"
    assert spy.commits == 0, "조회가 COMMIT했다"


def test_cache_hit_performs_no_db_write(db, monkeypatch):
    db(_seed())
    sc.invalidate_main_cache()
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    db(sc.load_main_entry(3000))                      # 캐시 채우기
    spy = db(_WriteSpy().install(monkeypatch))

    _entry, source = db(sc.load_main_entry(3000))
    assert source == "hit"
    assert spy.writes == [] and spy.commits == 0


def test_read_path_does_not_touch_top_movers_table(db):
    db(_seed())
    sc.invalidate_main_cache()
    d = db(sc.load_main())
    assert d["topHeartMovers1h"], "급상승은 계산돼서 응답에 들어간다"
    assert db(_count("singcup_top_movers")) == 0, "그런데 저장하지는 않는다"


def test_load_main_entry_has_no_persist_switch():
    """플래그 기본값이 아니라 **구조**로 막는다 — 넘길 인자 자체가 없어야 한다."""
    import inspect
    params = inspect.signature(sc.load_main_entry).parameters
    assert "persist_top_movers" not in params
    assert "persist_top_movers" not in inspect.signature(sc._load_main_uncached).parameters


# ── 2. 진짜 잠금 재현 ──────────────────────────────────────────────────────
async def _hold_write_lock():
    """운영과 같은 **별도 연결**로 쓰기 트랜잭션을 열어 둔다.

    aiosqlite 기본 isolation_level에서 첫 쓰기가 암묵적 트랜잭션을 열고 커밋까지
    RESERVED 잠금을 유지한다 → 다른 연결의 쓰기는 'database is locked'가 된다.

    공용 연결의 busy_timeout도 0으로 내린다. 운영값(10초)을 그대로 두면 테스트가
    잠금이 아니라 **대기**를 재현하게 되어 한 건에 40초씩 걸린다. 검증 대상은
    "잠겼을 때 어떻게 되는가"이므로 대기 없이 즉시 잠금을 보게 만든다.
    """
    shared = await database.get_db()
    await shared.execute("PRAGMA busy_timeout=0")
    conn = await aiosqlite.connect(database.DB_PATH)
    await conn.execute("PRAGMA busy_timeout=0")
    await conn.execute(
        "INSERT INTO singcup_top_movers (event_id, payload, base_at, computed_at)"
        " VALUES ('lockholder','[]',0,0)")
    return conn


async def _release_lock(conn):
    await conn.rollback()
    await conn.close()
    shared = await database.get_db()
    await shared.execute(f"PRAGMA busy_timeout={database.db.BUSY_TIMEOUT_MS}")


def test_public_read_succeeds_while_another_connection_holds_the_write_lock(db):
    """다른 프로세스가 쓰기 잠금을 쥐고 있어도 공개 조회는 200이어야 한다."""
    db(_seed())
    sc.invalidate_main_cache()
    conn = db(_hold_write_lock())
    try:
        entry, _src = db(sc.load_main_entry(3000))
        assert entry["data"]["summary"]["streamerCount"] == 3
        assert entry["etag"].startswith('W/"')
    finally:
        db(_release_lock(conn))


def test_old_behaviour_would_have_failed_under_the_same_lock(db):
    """수정 전 경로(조회 중 저장)가 실제로 잠금에 걸리는지 확인한다.

    이 테스트가 통과한다는 것은 위 테스트의 잠금 재현이 **진짜**라는 뜻이다.
    (잠금이 재현되지 않으면 위 테스트는 아무것도 증명하지 못한다.)
    """
    db(_seed())
    conn = db(_hold_write_lock())
    try:
        with pytest.raises(Exception) as ei:
            async def direct_write():
                d = await database.get_db()
                await d.execute(
                    "INSERT INTO singcup_top_movers (event_id, payload, base_at,"
                    " computed_at) VALUES (?,?,?,?)", (sc.EVENT_ID, "[]", 0, 0))
                await d.commit()
            db(direct_write())
        assert dw.is_locked(ei.value), f"잠금이 재현되지 않았다: {ei.value}"
    finally:
        db(_release_lock(conn))


# ── 3. 영속화 실패의 격리 ──────────────────────────────────────────────────
def test_persist_failure_does_not_break_recompute(db, monkeypatch):
    db(_seed())

    async def boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sc, "_save_top_movers", boom)

    ranked = db(sc.recompute_ranking(int(time.time())))
    assert len(ranked) == 3, "영속화가 실패해도 랭킹 계산은 끝난다"


def test_persist_failure_does_not_block_snapshot_publish(db, monkeypatch):
    import singcup_split_api as split
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    split.reset()
    db(_seed())

    async def boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sc, "_save_top_movers", boom)

    db(sc.recompute_ranking(int(time.time())))
    assert split.latest() is not None, "급상승 저장 실패가 스냅샷 게시를 막으면 안 된다"


def test_persist_under_lock_gives_up_without_raising(db):
    """잠금이 풀리지 않아도 예외를 올리지 않고 다음 주기로 넘긴다."""
    db(_seed())
    conn = db(_hold_write_lock())
    try:
        assert db(sc.persist_top_movers_snapshot(source="test")) == "failed"
    finally:
        db(_release_lock(conn))


def test_db_is_usable_again_after_a_locked_write(db):
    """롤백이 제대로 됐다면 다음 DB 작업이 정상이어야 한다."""
    db(_seed())
    conn = db(_hold_write_lock())
    try:
        db(sc.persist_top_movers_snapshot(source="test"))
    finally:
        db(_release_lock(conn))
    # 잠금이 풀린 뒤 같은 연결로 읽고 쓰는 것이 모두 정상이어야 한다
    assert db(_count("singcup_clips")) == 3
    assert db(sc.persist_top_movers_snapshot(source="after")) == "written"


# ── 4. 영속화는 요청 경로 밖에서만 ─────────────────────────────────────────
def test_recompute_persists_top_movers(db):
    db(_seed())
    assert db(_movers_row()) is None
    db(sc.recompute_ranking(int(time.time())))
    row = db(_movers_row())
    assert row is not None
    assert json.loads(row["payload"]), "급상승이 저장됐다"


def test_same_bucket_writes_at_most_once(db):
    """같은 값이면 UPDATE 자체가 일어나지 않는다."""
    db(_seed())
    assert db(sc.persist_top_movers_snapshot(source="1")) == "written"
    for _ in range(5):
        assert db(sc.persist_top_movers_snapshot(source="n")) == "unchanged"


def test_stale_movers_are_not_written_back(db):
    """직전 값을 다시 읽어 그대로 쓰는 자기복사를 막는다."""
    db(_seed(with_baseline=False))          # 기준선이 없어 급상승이 계산되지 않는다
    out = db(sc.persist_top_movers_snapshot(source="test"))
    assert out == "skipped_stale"
    assert db(_movers_row()) is None


# ── 5. 동시 요청 ───────────────────────────────────────────────────────────
def test_twenty_concurrent_reads_write_nothing(db, monkeypatch):
    db(_seed())
    sc.invalidate_main_cache()
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    spy = db(_WriteSpy().install(monkeypatch))

    async def burst():
        return await asyncio.gather(*[sc.load_main_entry(3000) for _ in range(20)])

    results = db(burst())
    assert len(results) == 20
    assert all(r[0]["data"]["summary"]["streamerCount"] == 3 for r in results)
    assert spy.writes == [], f"동시 조회가 DB에 썼다: {spy.writes}"
    assert spy.commits == 0
    sources = [r[1] for r in results]
    assert sources.count("miss") == 1, "대형 계산은 한 번만 돈다"


def test_concurrent_reads_succeed_while_a_writer_holds_the_lock(db, monkeypatch):
    db(_seed())
    sc.invalidate_main_cache()
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    conn = db(_hold_write_lock())
    try:
        async def burst():
            return await asyncio.gather(
                *[sc.load_main_entry(3000) for _ in range(20)],
                return_exceptions=True)
        results = db(burst())
        bad = [r for r in results if isinstance(r, BaseException)]
        assert not bad, f"수집 writer가 잠근 동안 조회가 실패했다: {bad[:1]}"
    finally:
        db(_release_lock(conn))


# ── 6. 캐시 동작 ───────────────────────────────────────────────────────────
def test_cache_is_filled_even_if_persistence_fails(db, monkeypatch):
    db(_seed())
    sc.invalidate_main_cache()
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)

    async def boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sc, "_save_top_movers", boom)

    db(sc.persist_top_movers_snapshot(source="test"))   # 저장은 실패한다
    _entry, source = db(sc.load_main_entry(3000))
    assert source == "hit", "실패해도 계산 결과는 캐시에 남아야 한다"


def test_etag_and_body_are_consistent(db, monkeypatch):
    db(_seed())
    sc.invalidate_main_cache()
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    e1, _ = db(sc.load_main_entry(3000))
    e2, _ = db(sc.load_main_entry(3000))
    assert e1["etag"] == e2["etag"]
    assert json.loads(e1["body"].decode())["summary"]["streamerCount"] == 3


def test_payload_is_never_emptied_by_a_persistence_failure(db, monkeypatch):
    db(_seed())

    async def boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sc, "_save_top_movers", boom)
    db(sc.recompute_ranking(int(time.time())))
    d = db(sc.load_main())
    assert d["summary"]["streamerCount"] == 3
    assert len(d["streamers"]) == 3


# ── 7. 응답에 내부 정보가 새지 않는다 ─────────────────────────────────────
def test_response_never_leaks_sql_or_paths(db, monkeypatch):
    db(_seed())
    sc.invalidate_main_cache()

    async def boom(*_a, **_k):
        raise RuntimeError(
            "sqlite3.OperationalError: database is locked while executing "
            "INSERT INTO singcup_top_movers at /app/web/backend/singcup_clips.py")
    monkeypatch.setattr(sc, "_save_top_movers", boom)
    db(sc.persist_top_movers_snapshot(source="test"))

    body = db(sc.load_main_entry(3000))[0]["body"].decode()
    for leak in ("sqlite3", "OperationalError", "INSERT INTO", "/app/",
                 "singcup_clips.py", "Traceback"):
        assert leak not in body, leak


# ── 8. 동일 값이면 쓰기 잠금을 시도조차 하지 않는다 ────────────────────────
# 조건부 UPSERT만으로는 부족하다는 것이 실측으로 확인됐다: 다른 연결이 쓰기 잠금을
# 쥔 상태에서 **값이 완전히 같아도** UPSERT는 쓰기 트랜잭션을 시작하고
# busy_timeout을 꽉 채운 뒤 실패한다(10,770ms). rowcount=0은 잠금을 잡은 뒤의 일이다.
# 그래서 읽기 비교를 먼저 한다(WAL의 SELECT는 잠금 영향을 받지 않는다 — 실측 0ms).
def test_conditional_upsert_alone_would_still_take_the_write_lock(db):
    """수정의 전제를 증명한다 — 동일 값 UPSERT도 잠금을 잡으려 한다."""
    db(_seed())
    db(sc.persist_top_movers_snapshot(source="seed"))
    row = db(_movers_row())
    conn = db(_hold_write_lock())
    try:
        async def same_value_upsert():
            d = await database.get_db()
            await d.execute(
                "INSERT INTO singcup_top_movers (event_id, payload, base_at, computed_at)"
                " VALUES (?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET"
                " payload=excluded.payload, base_at=excluded.base_at,"
                " computed_at=excluded.computed_at"
                " WHERE singcup_top_movers.payload IS NOT excluded.payload",
                (sc.EVENT_ID, row["payload"], row["base_at"], row["computed_at"]))
            await d.commit()
        with pytest.raises(Exception) as ei:
            db(same_value_upsert())
        assert dw.is_locked(ei.value), (
            "동일 값인데도 잠금을 잡으려 한다 — 그래서 read 비교가 먼저 필요하다")
    finally:
        db(_release_lock(conn))


def test_same_payload_performs_no_write_and_no_commit(db, monkeypatch):
    db(_seed())
    assert db(sc.persist_top_movers_snapshot(source="1")) == "written"
    monkeypatch.setattr(sc, "PERSIST_MIN_INTERVAL_SECONDS", 0)
    spy = db(_WriteSpy().install(monkeypatch))
    assert db(sc.persist_top_movers_snapshot(source="2")) == "unchanged"
    assert spy.writes == [], f"동일 payload인데 썼다: {spy.writes}"
    assert spy.commits == 0


def test_same_payload_returns_unchanged_immediately_under_lock(db, monkeypatch):
    """잠금이 걸려 있어도 동일 값이면 즉시 unchanged — 기다리지 않는다."""
    db(_seed())
    db(sc.persist_top_movers_snapshot(source="1"))
    monkeypatch.setattr(sc, "PERSIST_MIN_INTERVAL_SECONDS", 0)
    conn = db(_hold_write_lock())
    try:
        t0 = time.perf_counter()
        assert db(sc.persist_top_movers_snapshot(source="2")) == "unchanged"
        took = time.perf_counter() - t0
        assert took < 0.5, f"동일 값인데 {took:.2f}초를 기다렸다"
    finally:
        db(_release_lock(conn))


def test_changed_payload_gives_up_within_the_budget(db, monkeypatch):
    """값이 달라 쓰기를 시도해도 예산 안에서 포기한다."""
    db(_seed())
    monkeypatch.setattr(sc, "PERSIST_MIN_INTERVAL_SECONDS", 0)
    conn = db(_hold_write_lock())
    try:
        t0 = time.perf_counter()
        out = db(sc.persist_top_movers_snapshot(source="test"))
        took = time.perf_counter() - t0
        assert out == "failed"
        budget = sc.PERSIST_BUDGET_SECONDS + sc.PERSIST_BUSY_TIMEOUT_MS / 1000 + 0.5
        assert took < budget, f"{took:.2f}초 — 예산({budget:.2f}초)을 넘겼다"
    finally:
        db(_release_lock(conn))


def test_dedicated_connection_does_not_change_shared_pragmas(db, monkeypatch):
    """전용 연결을 써도 공용 연결의 busy_timeout은 그대로여야 한다."""
    async def read_timeout():
        d = await database.get_db()
        r = await (await d.execute("PRAGMA busy_timeout")).fetchone()
        return int(r[0])
    db(_seed())
    before = db(read_timeout())
    monkeypatch.setattr(sc, "PERSIST_MIN_INTERVAL_SECONDS", 0)
    db(sc.persist_top_movers_snapshot(source="test"))
    assert db(read_timeout()) == before == 10000


def test_shared_connection_is_healthy_after_a_giveup(db, monkeypatch):
    """포기 후에도 공용 연결로 읽고 쓰는 것이 정상이어야 한다."""
    db(_seed())
    monkeypatch.setattr(sc, "PERSIST_MIN_INTERVAL_SECONDS", 0)
    conn = db(_hold_write_lock())
    try:
        assert db(sc.persist_top_movers_snapshot(source="test")) == "failed"
    finally:
        db(_release_lock(conn))
    assert db(_count("singcup_clips")) == 3
    assert db(sc.persist_top_movers_snapshot(source="after")) == "written"


def test_min_interval_throttles_churn(db, monkeypatch):
    """안전판은 기본으로 꺼져 있다(0). 켜면 잦은 재저장을 막는다."""
    assert sc.PERSIST_MIN_INTERVAL_SECONDS == 0, "기본값은 꺼짐이어야 한다"
    monkeypatch.setattr(sc, "PERSIST_MIN_INTERVAL_SECONDS", 30)
    db(_seed())
    assert db(sc.persist_top_movers_snapshot(source="1")) == "written"

    async def bump():
        d = await database.get_db()
        await d.execute("UPDATE singcup_clips SET heart_count=heart_count+50")
        await d.commit()
    db(bump())
    sc.invalidate_main_cache()
    assert db(sc.persist_top_movers_snapshot(source="2")) == "throttled"


# ── 9. Snapshot 지연 격리 ──────────────────────────────────────────────────
def test_snapshot_is_published_before_persistence(db, monkeypatch):
    """게시가 먼저다 — 최신 랭킹이 부가 영속화보다 우선."""
    import singcup_split_api as split
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    split.reset()
    db(_seed())
    order = []
    real_pub, real_persist = sc.publish_snapshot, sc.persist_top_movers_snapshot

    async def pub(**kw):
        order.append("publish")
        return await real_pub(**kw)

    async def per(**kw):
        order.append("persist")
        return await real_persist(**kw)
    monkeypatch.setattr(sc, "publish_snapshot", pub)
    monkeypatch.setattr(sc, "persist_top_movers_snapshot", per)

    db(sc.recompute_ranking(int(time.time())))
    assert order == ["publish", "persist"]


def test_snapshot_publish_is_not_delayed_by_a_slow_persistence(db, monkeypatch):
    """영속화가 예산을 다 써도 스냅샷 게시는 그 전에 끝나 있어야 한다.

    (여기서는 저장이 느린 상황만 만든다. 실제 잠금은
    test_changed_payload_gives_up_within_the_budget이 본다 — 진짜 잠금을 recompute
    전체에 걸면 recompute 자신의 쓰기부터 막혀서 검증 대상이 달라진다.)
    """
    import singcup_split_api as split
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    split.reset()
    db(_seed())
    published_at = {}
    real_pub = sc.publish_snapshot

    async def pub(**kw):
        out = await real_pub(**kw)
        published_at["t"] = time.perf_counter()
        return out

    async def slow_save(*_a, **_k):
        await asyncio.sleep(sc.PERSIST_BUDGET_SECONDS)
        return "failed"
    monkeypatch.setattr(sc, "publish_snapshot", pub)
    monkeypatch.setattr(sc, "_save_top_movers", slow_save)

    t0 = time.perf_counter()
    db(sc.recompute_ranking(int(time.time())))
    total = time.perf_counter() - t0
    assert split.latest() is not None, "게시가 되지 않았다"
    # 게시는 느린 저장을 기다리지 않았다
    assert published_at["t"] - t0 < sc.PERSIST_BUDGET_SECONDS
    assert total >= sc.PERSIST_BUDGET_SECONDS      # 저장은 뒤에서 예산을 다 썼다


def test_recompute_completes_even_when_persistence_gives_up(db, monkeypatch):
    db(_seed())

    async def failing_save(*_a, **_k):
        return "failed"
    monkeypatch.setattr(sc, "_save_top_movers", failing_save)
    ranked = db(sc.recompute_ranking(int(time.time())))
    assert len(ranked) == 3
    assert db(sc.load_main())["summary"]["streamerCount"] == 3


# ── 10. 동시 호출 병합 ─────────────────────────────────────────────────────
def test_twenty_concurrent_persists_write_once(db, monkeypatch):
    """같은 값이면 20회를 동시에 불러도 실제 write는 1회다."""
    db(_seed())
    monkeypatch.setattr(sc, "PERSIST_MIN_INTERVAL_SECONDS", 0)

    async def burst():
        return await asyncio.gather(
            *[sc.persist_top_movers_snapshot(source=f"c{i}") for i in range(20)])
    out = db(burst())
    assert out.count("written") == 1, out.count("written")
    assert out.count("unchanged") == 19
    assert db(_count("singcup_top_movers")) == 1


def test_no_background_tasks_are_left_behind(db):
    """무한 background task나 큐를 만들지 않는다 — 남는 task가 없어야 한다."""
    db(_seed())

    async def run():
        before = len(asyncio.all_tasks())
        await sc.persist_top_movers_snapshot(source="test")
        return before, len(asyncio.all_tasks())
    before, after = db(run())
    assert after <= before, f"task가 남았다: {before} → {after}"


# ── 11. 최악 지연이 예산 안이라는 것을 상수로 고정 ────────────────────────
def test_worst_case_latency_fits_the_budget():
    """재시도 횟수·busy_timeout·backoff·jitter·연결 비용을 전부 더해도 예산 안."""
    worst = sc.persist_worst_case_seconds()
    assert worst <= sc.PERSIST_BUDGET_SECONDS, (
        f"최악 {worst:.2f}초 > 예산 {sc.PERSIST_BUDGET_SECONDS}초 — "
        "PERSIST_ATTEMPTS나 PERSIST_BUSY_TIMEOUT_MS를 줄이거나 예산을 늘려야 한다")
    # 계산이 상수와 실제로 이어져 있는지(상수를 바꾸면 값이 따라 움직이는지) 확인
    expect = (sc.PERSIST_ATTEMPTS * sc.PERSIST_BUSY_TIMEOUT_MS / 1000
              + sum(sc.PERSIST_BACKOFF_BASE_SECONDS * (2 ** i) * 2
                    for i in range(sc.PERSIST_ATTEMPTS - 1))
              + sc.PERSIST_ATTEMPTS * 0.005)
    assert abs(worst - expect) < 1e-9


def test_attempt_count_matches_the_constant(db, monkeypatch):
    """실제 시도 횟수가 상수와 같은지 — 계산과 코드가 어긋나면 안 된다."""
    db(_seed())
    tries = []
    real_connect = aiosqlite.connect

    def counting_connect(*a, **kw):
        tries.append(1)
        return real_connect(*a, **kw)
    conn = db(_hold_write_lock())        # 이 연결은 세지 않는다
    monkeypatch.setattr(sc.aiosqlite, "connect", counting_connect)
    try:
        t0 = time.perf_counter()
        assert db(sc.persist_top_movers_snapshot(source="test")) == "failed"
        took = time.perf_counter() - t0
    finally:
        db(_release_lock(conn))
    # 계산한 최악값 안에서 끝났다(실측이 상수 계산을 뒷받침한다)
    assert took <= sc.persist_worst_case_seconds() + 0.3, f"{took:.2f}초"
    # 시도 횟수는 상수를 **넘지 않는다.** 다만 재시도 예산은 절대 마감시한이라,
    # 머신이 바쁘면(전체 스위트 동시 실행 등) 마지막 시도를 시작하기 전에 예산이
    # 먼저 소진돼 한 번 덜 시도할 수 있다 — 그게 이 설계의 의도다.
    # 그래서 "예산이 남아 있었다면 상수만큼 시도했다"를 검사한다.
    assert 1 <= len(tries) <= sc.PERSIST_ATTEMPTS, f"{len(tries)}회 시도"
    budget_left = sc.PERSIST_BUDGET_SECONDS - took
    if budget_left > 0.3:
        assert len(tries) == sc.PERSIST_ATTEMPTS, (
            f"예산이 {budget_left:.2f}초 남았는데 {len(tries)}회만 시도했다")


def test_dedicated_connection_is_closed_on_every_path(db, monkeypatch):
    """성공·잠금·예외 어느 경로로 나가도 전용 연결이 닫힌다."""
    db(_seed())
    opened, closed = [], []
    real_connect = aiosqlite.connect

    def tracking_connect(*a, **kw):
        coro = real_connect(*a, **kw)

        class _Wrap:
            def __await__(self):
                conn = yield from coro.__await__()
                opened.append(conn)
                real_close = conn.close

                async def close_spy():
                    closed.append(conn)
                    return await real_close()
                conn.close = close_spy
                return conn
        return _Wrap()
    monkeypatch.setattr(sc.aiosqlite, "connect", tracking_connect)

    # 성공
    db(sc.persist_top_movers_snapshot(source="ok"))
    # 잠금
    conn = db(_hold_write_lock())
    try:
        async def bump():
            d = await database.get_db()
            await d.execute("UPDATE singcup_clips SET heart_count=heart_count+7")
            await d.commit()
        db(sc.persist_top_movers_snapshot(source="locked"))
    finally:
        db(_release_lock(conn))
    assert opened, "전용 연결이 쓰이지 않았다"
    assert len(closed) == len(opened), f"열림 {len(opened)} / 닫힘 {len(closed)}"


def test_shared_connection_pragmas_are_untouched(db, monkeypatch):
    """공유 연결의 busy_timeout·journal_mode·synchronous가 그대로여야 한다."""
    async def pragmas():
        d = await database.get_db()
        out = {}
        for p in ("busy_timeout", "journal_mode", "synchronous"):
            r = await (await d.execute(f"PRAGMA {p}")).fetchone()
            out[p] = r[0]
        return out
    db(_seed())
    before = db(pragmas())
    assert before["busy_timeout"] == 10000
    db(sc.persist_top_movers_snapshot(source="test"))
    conn = db(_hold_write_lock())
    try:
        db(sc.persist_top_movers_snapshot(source="locked"))
    finally:
        db(_release_lock(conn))
    assert db(pragmas()) == before
