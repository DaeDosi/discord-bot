"""보존정책 안전장치 — '배포만으로 원본이 지워지는 일'이 없어야 한다.

프루닝은 파괴적이다(코드를 revert해도 지워진 원본은 돌아오지 않는다).
그래서 기본 비활성 + dry-run + 롤업 검증 통과가 모두 만족돼야만 삭제된다.
"""
import time

import singcup_clips as sc
import singcup_retention as sr

import database

HOUR = 3600


async def _add(owner, at, hearts=1, clip="c1", rank=1):
    c = await database.get_db()
    await c.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at)"
        " VALUES (?,?,?,?,0,0,0,?,?)",
        (sc.EVENT_ID, clip, owner, hearts, rank, int(at)))
    await c.commit()


async def _count(t):
    c = await database.get_db()
    return (await (await c.execute(f"SELECT COUNT(*) n FROM {t}")).fetchone())["n"]


async def _seed_old(n=12, owners=3):
    now = int(time.time())
    for i in range(n):
        await _add(f"o{i % owners}", now - 40 * HOUR + i * 240, hearts=i)
    return now


# ── 1) 기본값: 아무것도 지우지 않는다 ───────────────────────────────────────
def test_defaults_are_disabled_and_dry_run():
    assert sr.PRUNE_ENABLED is False, "배포만으로 삭제가 시작되면 안 된다"
    assert sr.PRUNE_DRY_RUN is True
    assert sr.COMPACT_HOURLY_ENABLED is False


def test_run_retention_deletes_nothing_by_default(db):
    now = db(_seed_old())
    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now))
    assert db(_count("singcup_snapshots")) == before, "기본값에서는 한 행도 지우면 안 된다"
    assert rep["prune"]["deleted"] == 0
    assert rep["prune"]["applied"] is False
    assert rep["prune_enabled"] is False


def test_dry_run_reports_without_writing(db):
    """dry-run은 INSERT/DELETE 없이 '무엇을 할 것인지'만 알려준다."""
    now = db(_seed_old())
    rep = db(sr.run_retention(now))
    assert db(_count("singcup_snapshot_hourly")) == 0, "dry-run은 롤업도 쓰지 않는다"
    assert rep["dry_run"] is True
    assert rep["rollup"]["applied"] is False
    assert rep["rollup"]["rollup_rows"] > 0, "생성 예정 롤업 행 수를 알려줘야 한다"
    p = rep["prune"]
    assert p["would_delete"] >= 0 and "estimated_reclaim_bytes" in p
    assert "elapsed_ms" in rep


def test_enabled_flag_alone_still_respects_dry_run(db, monkeypatch):
    """ENABLED만 켜고 DRY_RUN을 안 끄면 여전히 지우지 않는다(이중 관문)."""
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    now = db(_seed_old())
    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now))
    assert db(_count("singcup_snapshots")) == before
    assert rep["prune"]["applied"] is False


def test_actual_prune_requires_both_flags(db, monkeypatch):
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())
    rep = db(sr.run_retention(now))
    assert rep["prune"]["applied"] is True
    assert rep["prune"]["deleted"] > 0
    assert db(_count("singcup_snapshot_hourly")) > 0, "지우기 전에 롤업이 있어야 한다"


# ── 2) 검증 실패 시 삭제 금지 ───────────────────────────────────────────────
def test_missing_rollup_blocks_deletion(db, monkeypatch):
    """롤업이 비어 있는 시간대의 원본을 지우면 그 시간이 통째로 사라진다."""
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())

    async def broken(*a, **kw):        # 롤업이 아무것도 만들지 못한 상황
        return {"rollup_rows": 0, "applied": True}
    monkeypatch.setattr(sr, "build_rollup", broken)

    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now))
    assert db(_count("singcup_snapshots")) == before, "검증 실패 시 원본을 지우면 안 된다"
    assert rep["verify"]["mismatched_hours"] > 0
    assert rep["prune"]["applied"] is False


def test_verify_detects_owner_count_mismatch(db):
    """원본에 있는 스트리머가 롤업에 없으면 그 시간대는 삭제 금지다."""
    now = db(_seed_old(owners=3))
    db(sr.build_rollup(now, dry_run=False))
    v = db(sr.verify_rollup(now))
    assert v["mismatched_hours"] == 0 and v["verified_hours"] > 0

    async def drop_one():
        c = await database.get_db()
        await c.execute("DELETE FROM singcup_snapshot_hourly WHERE owner_channel_id='o0'")
        await c.commit()
    db(drop_one())
    v2 = db(sr.verify_rollup(now))
    # 시드가 두 시간대에 걸쳐 있으므로 o0가 빠지면 두 시간 모두 삭제 금지가 된다
    assert v2["mismatched_hours"] >= 1
    assert all(m["missing_in_rollup"] == 1 for m in v2["mismatches"])
    assert v2["safe_hours"] == [] or all(
        h not in [(m["event_id"], m["hour_ts"]) for m in v2["mismatches"]]
        for h in v2["safe_hours"]), "불일치 시간대는 safe_hours에 없어야 한다"


def test_partial_failure_is_safe_to_rerun(db, monkeypatch):
    """중간에 실패해도 다시 돌리면 정상 완료돼야 한다(멱등)."""
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old(n=30, owners=5))
    monkeypatch.setattr(sr, "PRUNE_MAX_ROWS", 5)       # 도중에 멈춘 것처럼
    first = db(sr.run_retention(now))
    assert first["prune"]["deleted"] == 5
    monkeypatch.setattr(sr, "PRUNE_MAX_ROWS", 200000)
    second = db(sr.run_retention(now))
    assert second["prune"]["deleted"] == 25
    assert db(_count("singcup_snapshots")) == 0
    assert second["verify"]["mismatched_hours"] == 0


# ── 3) 결정적 대표값 선택 ───────────────────────────────────────────────────
def test_rollup_picks_last_row_deterministically(db):
    """같은 초에 두 행이 있어도 결과가 흔들리면 안 된다 → id로 최종 결정."""
    now = int(time.time())
    at = now - 40 * HOUR
    db(_add("o1", at, hearts=10))
    db(_add("o1", at, hearts=99))       # 같은 collected_at, 나중에 들어온 행
    db(sr.build_rollup(now, dry_run=False))

    async def one():
        c = await database.get_db()
        return dict(await (await c.execute(
            "SELECT heart_count FROM singcup_snapshot_hourly")).fetchone())
    assert db(one())["heart_count"] == 99


def test_unique_constraint_makes_rerun_an_upsert(db):
    now = int(time.time())
    db(_add("o1", now - 40 * HOUR, hearts=5))
    db(sr.build_rollup(now, dry_run=False))
    db(sr.build_rollup(now, dry_run=False))
    assert db(_count("singcup_snapshot_hourly")) == 1


# ── 4) 시간별 롤업도 무한히 늘지 않는다 ─────────────────────────────────────
def test_hourly_is_compacted_to_daily(db, monkeypatch):
    monkeypatch.setattr(sr, "HOURLY_RETENTION_DAYS", 0.0)
    now = int(time.time())

    async def seed_hourly():
        c = await database.get_db()
        base = (now // HOUR) * HOUR - 10 * HOUR
        for i in range(6):
            await c.execute(
                "INSERT INTO singcup_snapshot_hourly (event_id, hour_ts,"
                " owner_channel_id, clip_uid, heart_count, view_count,"
                " follower_count, score, rank) VALUES (?,?,?,'c',?,0,0,0,1)",
                (sc.EVENT_ID, base + i * HOUR, "o1", 100 + i))
        await c.commit()
    db(seed_hourly())
    assert db(_count("singcup_snapshot_hourly")) == 6

    r = db(sr.compact_hourly(now, dry_run=False))
    assert r["applied"] is True and r["dropped_hourly"] == 6
    assert db(_count("singcup_snapshot_daily")) == 1

    async def val():
        c = await database.get_db()
        return (await (await c.execute(
            "SELECT heart_count h FROM singcup_snapshot_daily")).fetchone())["h"]
    assert db(val()) == 105, "하루의 마지막 값을 남긴다"


def test_compact_hourly_dry_run_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(sr, "HOURLY_RETENTION_DAYS", 0.0)
    now = int(time.time())

    async def seed():
        c = await database.get_db()
        await c.execute(
            "INSERT INTO singcup_snapshot_hourly (event_id, hour_ts,"
            " owner_channel_id, clip_uid, heart_count, view_count,"
            " follower_count, score, rank) VALUES (?,?,?,'c',1,0,0,0,1)",
            (sc.EVENT_ID, (now // HOUR) * HOUR - 5 * HOUR, "o1"))
        await c.commit()
    db(seed())
    r = db(sr.compact_hourly(now, dry_run=True))
    assert r["applied"] is False
    assert db(_count("singcup_snapshot_daily")) == 0
    assert db(_count("singcup_snapshot_hourly")) == 1


def test_rollup_may_be_a_superset_after_partial_prune(db, monkeypatch):
    """부분 삭제가 지나간 뒤 롤업에 이미 지운 스트리머가 남는 건 정상이다.

    검사 기준이 '개수 일치'였다면 여기서 재실행이 영영 막힌다.
    """
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old(n=20, owners=4))
    db(sr.build_rollup(now, dry_run=False))
    v1 = db(sr.verify_rollup(now))
    db(sr.prune(now, v1["safe_hours"], dry_run=False))
    # 원본이 비었어도 롤업은 남아 있다 → 검증은 여전히 통과해야 한다
    v2 = db(sr.verify_rollup(now))
    assert v2["mismatched_hours"] == 0
