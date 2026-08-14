"""dry-run 견적의 비용 — 항진식 쿼리 제거와 단계별 관측.

배경(실측 2026-08-01 Railway): `retention_run`이 매시간 19.8~23.4초 동안 **공유
aiosqlite 연결**을 붙들었고, 그 직후 공개 API가 한꺼번에 풀렸다(rising-stars 17.6s ·
live-ranking 17.7s · categories 17.8s · overview 18.4s).

원인은 `_estimate_prune`의 `not_rolled_up_rows` 쿼리 하나였다(실측 47,028ms /
48,611ms = 96.7%). 그 쿼리는 `singcup_snapshots`를 **자기 자신과** 대조했고, 같은
행(`x = s`)이 언제나 조건을 만족하므로 `NOT EXISTS`가 성립할 수 없었다 — 82만 행을
훑어 상수 0을 돌려주는 항진식이었다.

여기서 지키는 것:
  1) 그 자기상관 서브쿼리가 **실행되지 않는다**(절대시간이 아니라 구조로 검증한다 —
     로컬 속도는 Railway를 대변하지 못한다).
  2) 그렇다고 **의미 없는 0을 계속 돌려주지 않는다**. `None` + `performed=False`다.
  3) 삭제 안전장치(이중 관문 · 롤업 검증 · 불일치 차단 · 최종 성적)는 **그대로**다.
"""
from __future__ import annotations

import asyncio
import inspect
import sqlite3
import time

import pytest
import singcup_clips as sc
import singcup_retention as sr

import database

HOUR = 3600

# 예전 쿼리 원문. 테스트는 이 문자열이 **실행되지 않는 것**을 확인하고,
# 성능 비교에서만 직접 실행한다.
_OLD_ORPHAN_SQL = (
    "SELECT COUNT(*) n FROM singcup_snapshots s WHERE s.collected_at < ? "
    "AND NOT EXISTS (SELECT 1 FROM singcup_snapshots x "
    "  WHERE x.event_id=s.event_id AND x.owner_channel_id=s.owner_channel_id "
    "    AND x.collected_at/3600 = s.collected_at/3600)")


async def _add(owner, at, hearts=1, clip="c1", rank=1):
    c = await database.get_db()
    await c.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at)"
        " VALUES (?,?,?,?,0,0,0,?,?)",
        (sc.EVENT_ID, clip, owner, hearts, rank, int(at)))
    await c.commit()


async def _seed_old(n=12, owners=3):
    now = int(time.time())
    for i in range(n):
        await _add(f"o{i % owners}", now - 40 * HOUR + i * 240, hearts=i)
    return now


async def _count(t):
    c = await database.get_db()
    return (await (await c.execute(f"SELECT COUNT(*) n FROM {t}")).fetchone())["n"]


def _spy_sql(monkeypatch, conn) -> list[str]:
    """공유 연결에 나가는 SQL을 전부 기록한다."""
    seen: list[str] = []
    orig = conn.execute

    async def spy(sql, *a, **kw):
        seen.append(str(sql))
        return await orig(sql, *a, **kw)

    monkeypatch.setattr(conn, "execute", spy)
    return seen


def _is_the_removed_query(sql: str) -> bool:
    """제거한 쿼리만 골라낸다.

    `FROM singcup_snapshots x`만으로 보면 안 된다 — `build_rollup`도 같은 별칭으로
    자기 자신을 참조하지만 그쪽은 `x.collected_at = s.collected_at`(인덱스 탐색)인
    결정적 대표값 선택이고 **정상이자 건드리면 안 되는 로직**이다. 비쌌던 것은
    시각을 3600으로 나눈 **표현식** 비교 쪽이다(인덱스로 좁혀지지 않는다).
    """
    s = " ".join(sql.split())
    return "x.collected_at/3600 = s.collected_at/3600" in s


# ── 1. 자기상관 서브쿼리가 실행되지 않는다 (구조 검증) ──────────────────────
def test_dry_run_never_issues_the_self_correlated_subquery(db, monkeypatch):
    """합격 기준은 '빨라졌다'가 아니라 '그 쿼리 호출이 0'이다."""

    async def go():
        now = await _seed_old()
        conn = await database.get_db()
        seen = _spy_sql(monkeypatch, conn)
        await sr.run_retention(now)
        offenders = [s for s in seen if _is_the_removed_query(s)]
        assert offenders == [], f"자기상관 서브쿼리가 {len(offenders)}회 실행됐다"
        assert seen, "SQL을 하나도 안 보냈다면 스파이가 잘못 걸린 것이다"

    db(go())


def test_estimate_prune_source_has_no_self_join_on_snapshots():
    """되돌리기 방지 — 소스에 그 형태가 다시 들어오면 실패한다."""
    src = " ".join(inspect.getsource(sr._estimate_prune).split())
    assert not _is_the_removed_query(src)
    assert "singcup_snapshots x" not in src, "dry-run 견적에 자기 대조를 다시 넣지 말 것"


# ── 2. 응답 계약 — 의미 없는 0 대신 '재지 않았다' ──────────────────────────
def test_dry_run_reports_orphan_check_as_not_performed(db):
    now = db(_seed_old())
    rep = db(sr.run_retention(now))
    p = rep["prune"]
    assert p["not_rolled_up_rows"] is None, "거짓 0을 돌려주면 안 된다"
    assert p["not_rolled_up_check_performed"] is False
    assert "롤업" in p["not_rolled_up_note"]
    # 기존 소비처 호환: 키 자체는 사라지지 않는다
    assert "not_rolled_up_rows" in p


def test_dry_run_still_reports_planned_rollup_rows_and_deletes_nothing(db):
    """생성 예정 롤업 수는 rollup.rollup_rows가 계속 알려준다."""
    now = db(_seed_old())
    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now))
    assert rep["rollup"]["rollup_rows"] > 0
    assert rep["rollup"]["applied"] is False
    assert rep["prune"]["deleted"] == 0
    assert rep["prune"]["applied"] is False
    assert rep["prune"]["would_delete"] > 0, "삭제 예정 행 수는 계속 보여준다"
    assert db(_count("singcup_snapshots")) == before
    assert db(_count("singcup_snapshot_hourly")) == 0, "dry-run은 롤업도 쓰지 않는다"


# ── 3. 단계별 관측 로그 ────────────────────────────────────────────────────
def test_phase_ms_is_reported_for_every_stage(db):
    now = db(_seed_old())
    rep = db(sr.run_retention(now))
    ph = rep["phase_ms"]
    assert set(ph) == {"build_rollup", "estimate_total", "estimate_per_event",
                       "estimate_orphan_check", "verify", "compact_hourly",
                       "final_standings"}
    assert all(isinstance(v, int) and v >= 0 for v in ph.values())
    assert ph["estimate_orphan_check"] == 0, "제거된 단계는 0으로 구분된다"
    assert ph["verify"] == 0, "dry-run에서는 verify를 하지 않는다"
    assert rep["elapsed_ms"] >= ph["build_rollup"]
    # 개인정보·원본 값이 섞이지 않는다 — 전부 숫자다
    assert all(isinstance(v, int) for v in ph.values())


def test_phase_ms_records_verify_and_final_standings_on_non_dry(db, monkeypatch):
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())
    rep = db(sr.run_retention(now))
    assert rep["verify"]["performed"] is True
    assert "verify" in rep["phase_ms"]
    assert rep["phase_ms"]["estimate_orphan_check"] == 0


# ── 4. 안전장치 회귀 — 이번 변경이 삭제 경로를 건드리지 않았다 ─────────────
def test_non_dry_with_prune_disabled_verifies_but_deletes_nothing(db, monkeypatch):
    """DRY_RUN=false + ENABLED=false → 롤업은 쓰고 검증은 하되 삭제는 0."""
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())
    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now))
    assert db(_count("singcup_snapshot_hourly")) > 0, "non-dry는 롤업을 쓴다"
    assert rep["verify"]["performed"] is True
    assert rep["verify"]["mismatched_hours"] == 0
    assert rep["prune"]["deleted"] == 0
    assert rep["prune"]["applied"] is False
    assert db(_count("singcup_snapshots")) == before


def test_rollup_mismatch_still_blocks_deletion(db, monkeypatch):
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())

    async def broken(*a, **kw):
        return {"rollup_rows": 0, "applied": True}
    monkeypatch.setattr(sr, "build_rollup", broken)

    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now))
    assert rep["verify"]["mismatched_hours"] > 0
    assert rep["prune"]["applied"] is False
    assert db(_count("singcup_snapshots")) == before


def test_enabled_true_but_verification_failed_deletes_nothing(db, monkeypatch):
    """PRUNE_ENABLED=true라도 검증이 깨지면 한 행도 지우지 않는다."""
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())

    async def bad_verify(*a, **kw):
        return {"verified_hours": 0, "mismatched_hours": 3,
                "safe_hours": [], "mismatches": []}
    monkeypatch.setattr(sr, "verify_rollup", bad_verify)

    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now))
    assert rep["prune"]["deleted"] == 0
    assert db(_count("singcup_snapshots")) == before


def test_final_standings_path_survives(db, monkeypatch):
    """최종 성적 저장 경로는 그대로 동작해야 한다.

    SINGCUP-1로 조건이 하나 늘었다 — 이벤트가 끝난 것만으로는 부족하고 **순위 갱신도
    닫혀 있어야** 한다. 종료 후에도 순위를 계속 계산하는 것이 확정 요구라, 그 동안
    '최종' 성적을 박아 두면 매 회차 UPSERT가 반복되고 이름도 사실과 달라진다.
    날짜(END_AT)를 조작하지 않고 계약 함수를 닫아 확인한다.
    """
    now = db(_seed_old(n=9, owners=3))
    monkeypatch.setattr(sr, "event_status", lambda: "ENDED")
    monkeypatch.setenv("SINGCUP_RANKING_REFRESH_ENABLED", "false")
    rep = db(sr.run_retention(now))
    assert rep["final_standings"]["saved"] == 3
    assert db(_count("singcup_final_standings")) == 3
    assert rep["phase_ms"]["final_standings"] >= 0


def test_final_standings_waits_while_ranking_still_refreshes(db, monkeypatch):
    """반대로 순위가 아직 갱신 중이면 저장하지 않는다(SINGCUP-1)."""
    now = db(_seed_old(n=9, owners=3))
    monkeypatch.setattr(sr, "event_status", lambda: "ENDED")
    rep = db(sr.run_retention(now))
    assert rep["final_standings"]["saved"] == 0
    assert "순위" in rep["final_standings"].get("note", "")
    assert db(_count("singcup_final_standings")) == 0, "얼리지 말아야 할 값이 저장됐다"


# ── 5. 규모 — 제거된 쿼리가 얼마나 비쌌는지 같은 DB에서 대조한다 ───────────
@pytest.mark.parametrize("owners,hours,per_hour", [(200, 20, 50)])
def test_removed_query_dominated_the_cost_at_scale(db, owners, hours, per_hour):
    """절대시간이 아니라 **같은 DB에서의 비율**로 본다(머신 속도에 의존하지 않게).

    운영 규모(82만 행)를 스위트에서 매번 만들 수는 없어 20만 행으로 줄였다.
    항진성(항상 0)은 행 수와 무관하므로 여기서 같이 고정한다.
    """
    now = int(time.time())
    cut = int(now - sr.RETENTION_HOURS * HOUR)

    async def seed():
        c = await database.get_db()
        rows = []
        for h in range(hours):
            base = cut - (h + 1) * HOUR
            for o in range(owners):
                for k in range(per_hour):
                    rows.append((sc.EVENT_ID, "c1", f"o{o:04d}", 1, 0, 0, 0.0, 1,
                                 base + k * 60))
        await c.executemany(
            "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
            " heart_count, view_count, follower_count, score, rank, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)", rows)
        await c.commit()
        return len(rows)

    n = db(seed())
    assert n == owners * hours * per_hour

    async def measure():
        c = await database.get_db()
        t = time.perf_counter()
        est = await sr._estimate_prune(now)
        new_ms = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        old = await (await c.execute(_OLD_ORPHAN_SQL, (cut,))).fetchone()
        old_ms = (time.perf_counter() - t) * 1000
        return est, new_ms, old_ms, int(old["n"])

    est, new_ms, old_ms, old_n = db(measure())
    assert est["would_delete"] == n
    assert old_n == 0, "제거한 쿼리는 어떤 데이터에서도 0만 돌려준다(항진식)"
    assert old_ms > new_ms * 5, (
        f"제거한 쿼리가 지배적이지 않다: old={old_ms:.0f}ms new={new_ms:.0f}ms")


def test_removed_query_is_a_tautology_even_with_a_lone_row(db):
    """'그 시간대에 자기 혼자뿐인' 행을 넣어도 0이다 — 탐지 능력이 없었다."""
    now = db(_seed_old())
    cut = int(now - sr.RETENTION_HOURS * HOUR)
    db(_add("LONELY", cut - 999_000))

    async def run():
        c = await database.get_db()
        r = await (await c.execute(_OLD_ORPHAN_SQL, (cut,))).fetchone()
        return int(r["n"])

    assert db(run()) == 0


# ── 6. 공유 연결 큐 대기가 재현되지 않는다 ────────────────────────────────
def test_light_query_is_not_stuck_behind_retention(db):
    """retention과 동시에 던진 가벼운 조회가 오래 막히면 안 된다.

    예전에는 같은 공유 연결에서 43,245ms였다(별도 연결은 3ms). 제거 후에는
    retention 자체가 짧아 큐 대기가 생기지 않는다.
    """
    now = int(time.time())
    cut = int(now - sr.RETENTION_HOURS * HOUR)

    async def go():
        c = await database.get_db()
        rows = [(sc.EVENT_ID, "c1", f"o{o:04d}", 1, 0, 0, 0.0, 1,
                 cut - (h + 1) * HOUR + k * 60)
                for h in range(20) for o in range(200) for k in range(50)]
        await c.executemany(
            "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
            " heart_count, view_count, follower_count, score, rank, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)", rows)
        await c.commit()

        waited = {}

        async def light():
            await asyncio.sleep(0.05)
            t = time.perf_counter()
            await (await c.execute(
                "SELECT COUNT(*) n FROM singcup_streamers")).fetchone()
            waited["ms"] = (time.perf_counter() - t) * 1000

        await asyncio.gather(sr.run_retention(now), light())
        return waited["ms"]

    ms = db(go())
    assert ms < 2000, f"공유 연결에서 {ms:.0f}ms 대기 — 큐 점유가 남아 있다"


# ── 7. 취소돼도 연결이 정상 상태로 남는다 ─────────────────────────────────
def test_cancelled_retention_leaves_the_connection_usable(db):
    """dry-run은 읽기뿐이라 되돌릴 쓰기가 없다. 연결이 살아 있는지가 핵심이다."""

    async def go():
        now = await _seed_old()
        c = await database.get_db()
        task = asyncio.ensure_future(sr.run_retention(now))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # 취소 후에도 같은 연결로 읽고 쓸 수 있어야 한다
        await c.execute("SELECT 1")
        await _add("after-cancel", now - 40 * HOUR)
        assert await _count("singcup_snapshots") > 0
        assert isinstance(c, object) and c is await database.get_db()

    db(go())


def test_dry_run_holds_no_open_transaction_afterwards(db):
    """읽기만 하므로 실행 후 열린 트랜잭션이 남으면 안 된다."""

    async def go():
        now = await _seed_old()
        await sr.run_retention(now)
        c = await database.get_db()
        raw = getattr(c, "_conn", None)
        if isinstance(raw, sqlite3.Connection):
            assert raw.in_transaction is False

    db(go())
