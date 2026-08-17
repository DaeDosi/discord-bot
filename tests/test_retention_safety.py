"""보존정책 안전장치 — '배포만으로 원본이 지워지는 일'이 없어야 한다.

프루닝은 파괴적이다(코드를 revert해도 지워진 원본은 돌아오지 않는다).
그래서 기본 비활성 + dry-run + 롤업 검증 통과가 모두 만족돼야만 삭제된다.
"""
import time
from datetime import datetime, timedelta, timezone

import pytest
import singcup_clips as sc
import singcup_retention as sr

import database

HOUR = 3600
DAY = 86400
_KST = timezone(timedelta(hours=9))


def _kst(stamp: str) -> int:
    """KST 벽시계 문자열('2026-08-17T18:00:00')을 epoch로."""
    return int(datetime.fromisoformat(stamp).replace(tzinfo=_KST).timestamp())


def _kst_midnight(ts: int) -> int:
    """그 시각이 속한 KST 하루의 자정 epoch. (KST는 서머타임이 없다)"""
    return int(datetime.fromtimestamp(ts, _KST)
               .replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


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
# 이 절의 시각은 **전부 고정**이다. 벽시계(`time.time()`)를 기준으로 시드하면 안 된다 —
# `compact_hourly`는 하루를 **KST 자정**으로 묶는데(`(hour_ts+32400)/86400`), 예전 시드는
# `now-10h ~ now-5h`의 6시간이라 실행 시각이 KST 05:00~09:59면 그 구간이 자정을 넘어
# daily가 2행이 됐다. 24시간 중 정확히 5시간대에서만 실패하는 시간 의존 flaky였다
# (실측 2026-08-17: 24개 시각 전수 스윕에서 05~09시만 daily=2).
# 프로덕션 로직이 아니라 시드가 틀렸던 것이므로, 시각을 고정하고 "하루 안"과
# "자정을 넘김"을 **각각 따로** 검증한다.

async def _seed_hours(first_ts: int, n: int, owner="o1", base_heart=100):
    """first_ts부터 1시간 간격으로 n개의 시간별 롤업 행을 넣는다."""
    c = await database.get_db()
    for i in range(n):
        await c.execute(
            "INSERT INTO singcup_snapshot_hourly (event_id, hour_ts,"
            " owner_channel_id, clip_uid, heart_count, view_count,"
            " follower_count, score, rank) VALUES (?,?,?,'c',?,0,0,0,1)",
            (sc.EVENT_ID, first_ts + i * HOUR, owner, base_heart + i))
    await c.commit()


async def _daily_rows():
    c = await database.get_db()
    return [dict(r) for r in await (await c.execute(
        "SELECT day_ts, heart_count FROM singcup_snapshot_daily ORDER BY day_ts"
    )).fetchall()]


# 6시간이 통째로 **하루의 KST 날짜 안에** 들어가는 경우 → daily 1행.
_ONE_DAY_CASES = [
    # (설명, 첫 시간의 KST 벽시계)
    ("kst_자정직전까지",       "2026-08-17T18:00:00"),  # 18~23시, 자정 직전에서 끝
    ("kst_자정직후부터",       "2026-08-17T00:00:00"),  # 00~05시, 자정 직후에서 시작
    ("utc날짜가_하루_이름",    "2026-08-17T01:00:00"),  # KST 8/17, UTC는 8/16
    ("utc날짜와_같은_구간",    "2026-08-17T14:00:00"),  # KST·UTC 모두 8/17
    ("월말",                   "2026-08-31T18:00:00"),
    ("월초_utc는_전월",        "2026-09-01T00:00:00"),  # KST 9/1, UTC 8/31
    ("연말",                   "2026-12-31T18:00:00"),
    ("연초_utc는_전년",        "2027-01-01T00:00:00"),  # KST 2027, UTC 2026
    ("윤일",                   "2028-02-29T18:00:00"),
    ("윤일_utc는_2월28일",     "2028-02-29T00:00:00"),  # KST 2/29, UTC 2/28
    ("윤년_3월1일",            "2028-03-01T00:00:00"),  # KST 3/1, UTC 2/29
]


@pytest.mark.parametrize("label,first", _ONE_DAY_CASES,
                         ids=[c[0] for c in _ONE_DAY_CASES])
def test_hourly_is_compacted_to_daily(db, monkeypatch, label, first):
    """하루 안의 시간별 롤업 6행 → daily 1행(그날의 마지막 값)."""
    monkeypatch.setattr(sr, "HOURLY_RETENTION_DAYS", 0.0)
    first_ts = _kst(first)
    now = first_ts + 6 * HOUR          # 6행 모두 cut(=now) 이전
    db(_seed_hours(first_ts, 6))
    assert db(_count("singcup_snapshot_hourly")) == 6

    r = db(sr.compact_hourly(now, dry_run=False))
    assert r["applied"] is True and r["dropped_hourly"] == 6
    assert db(_count("singcup_snapshot_hourly")) == 0
    rows = db(_daily_rows())
    assert len(rows) == 1, f"{label}: KST 하루 안이면 daily는 1행이어야 한다 {rows}"
    assert rows[0]["heart_count"] == 105, "하루의 마지막 값을 남긴다"
    assert rows[0]["day_ts"] == _kst_midnight(first_ts), "day_ts는 KST 자정이다"


# 6시간이 **KST 자정을 넘는** 경우 → 날짜별로 나뉘어 daily 2행. 위와 같은 로직의
# 반대편 계약이다. 예전 테스트는 이 경우를 우연히 실행 시각으로 밟아 실패했다.
_SPLIT_CASES = [
    ("자정_넘김",   "2026-08-16T21:00:00"),  # 21,22,23 | 00,01,02
    ("월말_넘김",   "2026-08-31T21:00:00"),  # 8/31 | 9/1
    ("연말_넘김",   "2026-12-31T21:00:00"),  # 2026 | 2027
    ("윤일_넘김",   "2028-02-28T21:00:00"),  # 2/28 | 2/29
    ("윤일_다음날", "2028-02-29T21:00:00"),  # 2/29 | 3/1
]


@pytest.mark.parametrize("label,first", _SPLIT_CASES,
                         ids=[c[0] for c in _SPLIT_CASES])
def test_compact_splits_on_kst_midnight(db, monkeypatch, label, first):
    monkeypatch.setattr(sr, "HOURLY_RETENTION_DAYS", 0.0)
    first_ts = _kst(first)
    now = first_ts + 6 * HOUR
    db(_seed_hours(first_ts, 6))

    r = db(sr.compact_hourly(now, dry_run=False))
    assert r["dropped_hourly"] == 6
    rows = db(_daily_rows())
    assert len(rows) == 2, f"{label}: KST 자정을 넘으면 daily가 나뉜다 {rows}"
    assert rows[0]["day_ts"] == _kst_midnight(first_ts)
    assert rows[1]["day_ts"] == _kst_midnight(first_ts) + DAY
    # 각 날짜의 마지막 값 — 23시(=102)와 02시(=105)
    assert [x["heart_count"] for x in rows] == [102, 105]


def test_compact_boundary_is_strictly_before_cut(db, monkeypatch):
    """보존 경계: cut 직전만 접고, 정확히 cut과 그 이후는 남긴다."""
    monkeypatch.setattr(sr, "HOURLY_RETENTION_DAYS", 1.0)
    now = _kst("2026-08-17T12:00:00")
    cut = now - DAY                      # compact_hourly가 쓰는 것과 같은 식
    db(_seed_hours(cut - HOUR, 1, owner="before", base_heart=1))
    db(_seed_hours(cut, 1, owner="at", base_heart=2))
    db(_seed_hours(cut + HOUR, 1, owner="after", base_heart=3))

    r = db(sr.compact_hourly(now, dry_run=False))
    assert r["dropped_hourly"] == 1, "hour_ts < cut 인 행만 대상이다"

    async def left():
        c = await database.get_db()
        return [r["owner_channel_id"] for r in await (await c.execute(
            "SELECT owner_channel_id FROM singcup_snapshot_hourly"
            " ORDER BY hour_ts")).fetchall()]
    assert db(left()) == ["at", "after"], "경계값과 그 이후는 보존한다"
    assert [x["heart_count"] for x in db(_daily_rows())] == [1]


def test_compact_result_does_not_depend_on_wall_clock(db, monkeypatch):
    """시스템 시각을 KST 자정 전후로 흔들어도 결과가 같아야 한다.

    `compact_hourly`는 `now`를 인자로 받으므로 벽시계를 보지 않는다. 그 계약이
    깨지면(내부에서 `time.time()`을 다시 읽으면) 여기서 바로 드러난다.
    """
    monkeypatch.setattr(sr, "HOURLY_RETENTION_DAYS", 0.0)
    first_ts = _kst("2026-08-17T00:00:00")
    now = first_ts + 6 * HOUR
    fakes = ["2026-08-17T23:59:59", "2026-08-18T00:00:00", "2026-08-18T00:00:01",
             "2026-08-17T05:30:00", "2026-08-17T09:30:00", "2026-12-31T23:59:59",
             "2028-02-29T00:00:00"]
    seen = set()
    for stamp in fakes:
        db(_wipe_rollups())
        monkeypatch.setattr(time, "time", lambda s=stamp: float(_kst(s)))
        db(_seed_hours(first_ts, 6))
        db(sr.compact_hourly(now, dry_run=False))
        seen.add(tuple((x["day_ts"], x["heart_count"]) for x in db(_daily_rows())))
    assert len(seen) == 1, f"벽시계에 따라 결과가 갈렸다: {seen}"
    assert seen == {((_kst_midnight(first_ts), 105),)}


async def _wipe_rollups():
    c = await database.get_db()
    await c.execute("DELETE FROM singcup_snapshot_hourly")
    await c.execute("DELETE FROM singcup_snapshot_daily")
    await c.commit()


def test_compact_hourly_dry_run_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(sr, "HOURLY_RETENTION_DAYS", 0.0)
    now = _kst("2026-08-17T12:00:00")
    db(_seed_hours(now - 5 * HOUR, 1, base_heart=1))
    r = db(sr.compact_hourly(now, dry_run=True))
    assert r["applied"] is False
    assert db(_count("singcup_snapshot_daily")) == 0
    assert db(_count("singcup_snapshot_hourly")) == 1


def test_compact_hourly_leaves_recent_rows_alone(db, monkeypatch):
    """압축 비대상(보존 기간 안)은 hourly에 그대로 남고 daily가 생기지 않는다."""
    monkeypatch.setattr(sr, "HOURLY_RETENTION_DAYS", 30.0)
    now = _kst("2026-08-17T12:00:00")
    db(_seed_hours(now - 6 * HOUR, 6))
    r = db(sr.compact_hourly(now, dry_run=False))
    assert r["dropped_hourly"] == 0
    assert db(_count("singcup_snapshot_hourly")) == 6
    assert db(_count("singcup_snapshot_daily")) == 0


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
