"""싱드컵 스냅샷 보존정책 — 롤업 후 원본 정리.

원본은 참가자 수 × 4분 주기로 쌓여 실측 55MB/일이다. 21일 이벤트면 1.1GB로
500MB 볼륨을 넘긴다. 24시간보다 오래된 원본을 읽는 코드는 없으므로(=_delta_maps의
24시간 비교가 최대 소급) 시간당 1행으로 접고 원본은 버린다.
"""
import time

import singcup_clips as sc

import database

HOUR = 3600


async def _add(owner, clip, hearts, rank, at, score=0.0):
    c = await database.get_db()
    await c.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at)"
        " VALUES (?,?,?,?,0,0,?,?,?)",
        (sc.EVENT_ID, clip, owner, hearts, score, rank, int(at)))
    await c.commit()


async def _count(table):
    c = await database.get_db()
    r = await (await c.execute(f"SELECT COUNT(*) n FROM {table}")).fetchone()
    return r["n"]


async def _hourly():
    c = await database.get_db()
    return [dict(r) for r in await (await c.execute(
        "SELECT * FROM singcup_snapshot_hourly ORDER BY hour_ts, owner_channel_id"
    )).fetchall()]


def test_old_snapshots_are_rolled_up_then_pruned(db):
    now = int(time.time())
    old = now - 40 * HOUR
    for i in range(6):                       # 같은 시간대 안의 6개 회차
        db(_add("o1", "c1", 100 + i, 3, old + i * 240))
    db(_add("o1", "c1", 999, 1, now - HOUR))  # 보존 구간 — 남아야 한다

    assert db(_count("singcup_snapshots")) == 7
    db(sc._rollup_snapshots(now))
    db(sc._prune_snapshots(now))

    assert db(_count("singcup_snapshots")) == 1, "오래된 원본은 지워진다"
    h = db(_hourly())
    assert len(h) == 1
    assert h[0]["heart_count"] == 105, "그 시간의 마지막 값을 대표로 남긴다"
    assert h[0]["hour_ts"] == (old // HOUR) * HOUR


def test_recent_snapshots_are_untouched(db):
    """24시간 비교가 읽는 구간은 절대 건드리지 않는다."""
    now = int(time.time())
    for h in (1, 12, 23, 25):
        db(_add("o1", "c1", h, 1, now - h * HOUR))
    db(sc._rollup_snapshots(now))
    db(sc._prune_snapshots(now))
    assert db(_count("singcup_snapshots")) == 4
    assert db(_hourly()) == []


def test_delta_still_works_after_pruning(db):
    """정리 후에도 1시간·24시간 증감 계산이 살아 있어야 한다."""
    now = int(time.time())
    db(_add("o1", "c1", 10, 5, now - 40 * HOUR))    # 지워질 구간
    db(_add("o1", "c1", 50, 4, now - 24 * HOUR))    # 24시간 비교용
    db(_add("o1", "c1", 80, 2, now - HOUR))         # 1시간 비교용
    db(sc._rollup_snapshots(now))
    db(sc._prune_snapshots(now))
    prev, day, ref = db(sc._delta_maps(now))
    assert ref is not None and prev["o1"][0] == 80
    assert day["o1"] == 50


def test_rollup_is_idempotent(db):
    now = int(time.time())
    old = now - 40 * HOUR
    db(_add("o1", "c1", 100, 3, old))
    db(sc._rollup_snapshots(now))
    db(sc._rollup_snapshots(now))            # 두 번 돌려도 결과가 같아야 한다
    assert len(db(_hourly())) == 1


def test_rollup_keeps_each_streamer_separately(db):
    now = int(time.time())
    old = now - 40 * HOUR
    for o in ("o1", "o2", "o3"):
        db(_add(o, f"c-{o}", 7, 1, old))
    db(sc._rollup_snapshots(now))
    assert len(db(_hourly())) == 3


def test_prune_is_batched(db):
    """대량 DELETE는 WAL을 급증시키고 봇 프로세스를 오래 막는다 → 나눠 지운다."""
    now = int(time.time())
    old = now - 40 * HOUR
    for i in range(25):
        db(_add(f"o{i}", "c1", 1, 1, old))
    limit = sc.SNAPSHOT_PRUNE_BATCH
    sc.SNAPSHOT_PRUNE_BATCH = 10
    try:
        assert db(sc._prune_snapshots(now)) == 10
        assert db(_count("singcup_snapshots")) == 15
    finally:
        sc.SNAPSHOT_PRUNE_BATCH = limit


def test_retention_covers_the_24h_lookback(db):
    """보존 시간이 24시간 비교보다 짧아지면 안 된다(설정 실수 방지)."""
    assert sc.SNAPSHOT_RETENTION_HOURS >= 25
