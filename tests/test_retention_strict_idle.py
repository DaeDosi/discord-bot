"""자동 retention 워커의 strict idle — 무거운 집계를 아예 돌지 않는다.

배경(실측 2026-08-01 Railway): 항진식 쿼리를 제거해 retention이 23.4초 → 3.55초가
됐지만, 남은 `estimate_total`만으로도 2,919ms 동안 **공유 aiosqlite 연결**을 붙들어
직후 Rising API가 3.0~4.1초 밀렸다.

`PRUNE_ENABLED=false`이고 `PRUNE_DRY_RUN=true`인 동안 이 집계의 산출물은 로그 한 줄뿐이고
화면 어디에도 쓰이지 않는다. 그래서 **자동 워커만** 건너뛴다. 관리자가 명시적으로 부른
진단 보고서는 그대로 전체 경로를 돈다.

절대 잃으면 안 되는 것은 하나다 — **최종 성적 저장.**
strict idle에서도 그 경로만은 살아 있다는 것을 여기서 고정한다.

⚠️ **SINGCUP-1로 조건이 하나 늘었다.** 예전에는 "이벤트가 끝나면(ENDED)" 저장했지만,
이제는 종료 후에도 순위를 계속 계산하므로(`ranking_refresh_open`) **순위 갱신이 아직
열려 있는 동안에는 저장하지 않는다** — 매 회차 참가자 수만큼 UPSERT가 반복되고
'최종'이라는 이름도 사실과 달라진다. 그래서 최종 성적 경로를 검사하는 테스트는
아래 `ranking_closed` 픽스처로 **순위 갱신을 명시적으로 닫고** 확인한다.
날짜(END_AT)를 조작하지 않는다 — END_AT은 참가 판정 창이기도 하다.
"""
from __future__ import annotations

import asyncio
import time

import pytest
import singcup_clips as sc
import singcup_retention as sr

import database

HOUR = 3600


@pytest.fixture
def ranking_closed(monkeypatch):
    """순위 갱신을 닫아 '최종 성적을 확정할 수 있는 상태'를 만든다(SINGCUP-1).

    이벤트 종료만으로는 부족하다 — 종료 후에도 순위가 계속 갱신되는 것이 확정 요구라,
    그 갱신을 실제로 멈춘 뒤에야 최종 성적이 의미를 갖는다.
    """
    monkeypatch.setenv("SINGCUP_RANKING_REFRESH_ENABLED", "false")


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


def _spy(monkeypatch, conn) -> list[str]:
    seen: list[str] = []
    orig = conn.execute

    async def spy(sql, *a, **kw):
        seen.append(" ".join(str(sql).split()))
        return await orig(sql, *a, **kw)

    monkeypatch.setattr(conn, "execute", spy)
    return seen


def _snapshot_aggregates(seen: list[str]) -> list[str]:
    """원본 스냅샷을 통째로 훑는 집계만 골라낸다(MAX 인덱스 조회는 제외)."""
    out = []
    for s in seen:
        if "singcup_snapshots" not in s:
            continue
        if "COUNT(" in s or "GROUP BY" in s or "MIN(collected_at)" in s:
            out.append(s)
    return out


def _idle_config(monkeypatch):
    monkeypatch.setattr(sr, "PRUNE_ENABLED", False)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", True)


# ── 1. 진입 조건은 두 플래그를 모두 본다 ───────────────────────────────────
@pytest.mark.parametrize("enabled,dry,expected", [
    (False, True, True),     # 현재 운영 상태 — idle
    (False, False, False),   # 활성화 절차 3단계: 롤업 작성 + 검증. idle이면 안 된다
    (True, True, False),     # ENABLED만 켠 상태 — 여전히 dry지만 idle이 아니다
    (True, False, False),    # 실제 삭제 단계
])
def test_strict_idle_requires_both_flags(monkeypatch, enabled, dry, expected):
    monkeypatch.setattr(sr, "PRUNE_ENABLED", enabled)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", dry)
    assert sr.strict_idle_active() is expected


# ── 2. idle 회차는 무거운 것을 하나도 하지 않는다 ──────────────────────────
def test_idle_run_skips_every_expensive_stage(db, monkeypatch):
    _idle_config(monkeypatch)
    calls = {"rollup": 0, "estimate": 0}

    async def no_rollup(*a, **kw):
        calls["rollup"] += 1
        return {"rollup_rows": 0, "applied": False}

    async def no_estimate(*a, **kw):
        calls["estimate"] += 1
        return {}

    monkeypatch.setattr(sr, "build_rollup", no_rollup)
    monkeypatch.setattr(sr, "_estimate_prune", no_estimate)

    async def go():
        now = await _seed_old()
        before = await _count("singcup_snapshots")
        conn = await database.get_db()
        seen = _spy(monkeypatch, conn)
        rep = await sr.run_retention(now, automatic=True)
        captured = list(seen)      # 아래 검증용 COUNT가 섞이기 전에 떠 둔다
        return rep, captured, before, await _count("singcup_snapshots")

    rep, seen, before, after = db(go())

    assert calls == {"rollup": 0, "estimate": 0}
    assert _snapshot_aggregates(seen) == [], "원본 전체 집계 SQL이 나갔다"
    assert rep["event"] == "retention_idle"
    assert rep["reason"] == "prune_disabled_and_dry_run"
    assert rep["deleted"] == 0
    assert rep["prune_enabled"] is False and rep["dry_run"] is True
    assert rep["next_run_at"] > rep["last_run_at"]
    assert after == before, "idle 회차가 행을 지웠다"


def test_idle_reports_no_rollup_or_prune_sections(db, monkeypatch):
    """idle은 전체 보고서를 흉내 내지 않는다 — 없는 값을 0으로 위장하면 오해를 부른다."""
    _idle_config(monkeypatch)
    now = db(_seed_old())
    rep = db(sr.run_retention(now, automatic=True))
    assert "rollup" not in rep and "prune" not in rep and "verify" not in rep
    assert rep["deleted"] == 0


def test_idle_does_not_scale_with_snapshot_rows(db, monkeypatch):
    """행 수가 늘어도 하는 일이 늘지 않는다 — 절대시간이 아니라 SQL로 검증한다."""
    _idle_config(monkeypatch)

    async def go():
        c = await database.get_db()
        now = int(time.time())
        base = now - 40 * HOUR
        rows = [(sc.EVENT_ID, "c1", f"o{o:04d}", 1, 0, 0, 0.0, 1, base + k)
                for o in range(500) for k in range(200)]
        await c.executemany(
            "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
            " heart_count, view_count, follower_count, score, rank, collected_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)", rows)
        await c.commit()
        seen = _spy(monkeypatch, c)
        rep = await sr.run_retention(now, automatic=True)
        return rep, seen, len(rows)

    rep, seen, n = db(go())
    assert n == 100_000
    assert [s for s in seen if "singcup_snapshots" in s] == [], \
        "이벤트 진행 중 idle은 원본 스냅샷을 아예 건드리지 않는다"
    assert rep["elapsed_ms"] < 1000


# ── 3. 최종 성적 보존 — strict idle이 이걸 누락시키면 안 된다 ──────────────
def test_running_event_does_not_touch_final_standings(db, monkeypatch):
    _idle_config(monkeypatch)
    monkeypatch.setattr(sr, "event_status", lambda: "RUNNING")
    now = db(_seed_old())
    rep = db(sr.run_retention(now, automatic=True))
    assert rep["event_status"] == "RUNNING"
    assert rep["final_standings"] == {"attempted": False, "saved": 0}
    assert db(_count("singcup_final_standings")) == 0


def test_ended_event_saves_final_standings_even_in_idle(db, monkeypatch, ranking_closed):
    _idle_config(monkeypatch)
    now = db(_seed_old(n=9, owners=3))
    monkeypatch.setattr(sr, "event_status", lambda: "ENDED")
    rep = db(sr.run_retention(now, automatic=True))
    assert rep["final_standings"]["attempted"] is True
    assert rep["final_standings"]["saved"] == 3
    assert rep["final_standings"]["reason"] == "not_saved_yet"
    assert db(_count("singcup_final_standings")) == 3


def test_final_standings_is_idempotent_and_not_resaved(db, monkeypatch, ranking_closed):
    """성공 이후에는 같은 데이터로 매시간 다시 쓰지 않는다."""
    _idle_config(monkeypatch)
    now = db(_seed_old(n=9, owners=3))
    monkeypatch.setattr(sr, "event_status", lambda: "ENDED")
    db(sr.run_retention(now, automatic=True))
    second = db(sr.run_retention(now + 3600, automatic=True))
    assert second["final_standings"]["attempted"] is False
    assert second["final_standings"]["reason"] == "already_saved"
    assert db(_count("singcup_final_standings")) == 3, "중복 행이 생기면 안 된다"


def test_new_snapshot_after_save_triggers_another_save(db, monkeypatch, ranking_closed):
    """저장 뒤에 더 새로운 원본이 들어오면 다시 저장한다."""
    _idle_config(monkeypatch)
    now = db(_seed_old(n=9, owners=3))
    monkeypatch.setattr(sr, "event_status", lambda: "ENDED")
    db(sr.run_retention(now, automatic=True))
    db(_add("o0", now - HOUR, hearts=777))
    rep = db(sr.run_retention(now + 60, automatic=True))
    assert rep["final_standings"]["attempted"] is True
    assert rep["final_standings"]["reason"] == "newer_snapshot"

    async def hearts():
        c = await database.get_db()
        return (await (await c.execute(
            "SELECT heart_count h FROM singcup_final_standings "
            "WHERE owner_channel_id='o0'")).fetchone())["h"]
    assert db(hearts()) == 777


def test_failed_save_is_not_marked_partial_and_retries_next_cycle(db, monkeypatch, ranking_closed):
    _idle_config(monkeypatch)
    now = db(_seed_old(n=9, owners=3))
    monkeypatch.setattr(sr, "event_status", lambda: "ENDED")

    async def boom(*a, **kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(sr, "save_final_standings", boom)

    rep = db(sr.run_retention(now, automatic=True))
    assert rep["final_standings"]["attempted"] is True
    assert rep["final_standings"]["saved"] == 0, "부분 완료로 표시하면 안 된다"
    assert "disk on fire" in rep["final_standings"]["error"]
    assert db(_count("singcup_final_standings")) == 0

    monkeypatch.undo()                       # 다음 회차에는 정상으로 돌아온다
    _idle_config(monkeypatch)
    monkeypatch.setattr(sr, "event_status", lambda: "ENDED")
    # `undo()`는 `ranking_closed` 픽스처가 건 env까지 함께 되돌린다. 최종 성적 저장은
    # 순위 갱신이 닫혀 있을 때만 일어나므로(SINGCUP-1) 여기서 다시 닫아 준다 —
    # 바로 위의 `_idle_config`·`event_status` 재적용과 같은 이유다.
    monkeypatch.setenv("SINGCUP_RANKING_REFRESH_ENABLED", "false")
    again = db(sr.run_retention(now + 3600, automatic=True))
    assert again["final_standings"]["saved"] == 3, "다음 회차에서 재시도해야 한다"


def test_worker_restart_still_saves_final_standings(db, monkeypatch, ranking_closed):
    """워커가 재시작해도(모듈 상태에 의존하지 않으므로) 판단이 같아야 한다."""
    _idle_config(monkeypatch)
    now = db(_seed_old(n=9, owners=3))
    monkeypatch.setattr(sr, "event_status", lambda: "ENDED")
    db(sr.run_retention(now, automatic=True))
    # '재시작' = 모듈 전역 어디에도 저장 여부를 캐시해 두지 않았다는 뜻
    needed, why = db(sr._final_standings_needed())
    assert needed is False and why == "already_saved"


# ── 4. 활성화 절차는 그대로 살아 있다 ─────────────────────────────────────
def test_dry_run_false_runs_the_full_path_for_the_worker(db, monkeypatch):
    """DRY_RUN=false + ENABLED=false → 롤업 작성 + 검증, 삭제는 0."""
    monkeypatch.setattr(sr, "PRUNE_ENABLED", False)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())
    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now, automatic=True))
    assert rep.get("event") != "retention_idle", "idle로 빠지면 활성화 준비가 막힌다"
    assert db(_count("singcup_snapshot_hourly")) > 0
    assert rep["verify"]["performed"] is True
    assert rep["verify"]["mismatched_hours"] == 0
    assert rep["prune"]["deleted"] == 0
    assert db(_count("singcup_snapshots")) == before


def test_both_flags_set_deletes_only_verified_hours(db, monkeypatch):
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())
    rep = db(sr.run_retention(now, automatic=True))
    assert rep["prune"]["applied"] is True
    assert rep["prune"]["deleted"] > 0
    assert db(_count("singcup_snapshot_hourly")) > 0


def test_verify_failure_blocks_deletion_for_the_worker(db, monkeypatch):
    monkeypatch.setattr(sr, "PRUNE_ENABLED", True)
    monkeypatch.setattr(sr, "PRUNE_DRY_RUN", False)
    now = db(_seed_old())

    async def bad_verify(*a, **kw):
        return {"verified_hours": 0, "mismatched_hours": 2,
                "safe_hours": [], "mismatches": []}
    monkeypatch.setattr(sr, "verify_rollup", bad_verify)

    before = db(_count("singcup_snapshots"))
    rep = db(sr.run_retention(now, automatic=True))
    assert rep["prune"]["deleted"] == 0
    assert db(_count("singcup_snapshots")) == before


# ── 5. 관리자 수동 경로는 그대로 전체 보고서 ───────────────────────────────
def test_admin_manual_run_still_returns_the_full_report(db, monkeypatch):
    """`automatic`을 넘기지 않는 호출(관리자 엔드포인트)은 idle이 아니다."""
    _idle_config(monkeypatch)
    now = db(_seed_old())
    rep = db(sr.run_retention(now))          # admin_router가 부르는 형태 그대로
    assert rep.get("event") != "retention_idle"
    assert rep["rollup"]["rollup_rows"] > 0
    assert rep["prune"]["would_delete"] > 0
    assert "phase_ms" in rep
    assert rep["prune"]["deleted"] == 0


def test_worker_stays_idle_while_an_admin_report_is_running(db, monkeypatch):
    """자동 워커와 관리자 실행이 겹쳐도 무거운 작업이 두 번 돌지 않는다.

    중복 방지는 락이 아니라 **경로 분리**로 이뤄진다 — 워커는 애초에 무거운 경로에
    들어가지 않는다.
    """
    _idle_config(monkeypatch)

    async def go():
        now = await _seed_old(n=60, owners=6)
        conn = await database.get_db()
        seen_admin, seen_idle = [], []
        orig = conn.execute
        target = seen_admin

        async def spy(sql, *a, **kw):
            target.append(" ".join(str(sql).split()))
            return await orig(sql, *a, **kw)

        monkeypatch.setattr(conn, "execute", spy)
        admin = asyncio.ensure_future(sr.run_retention(now))
        await asyncio.sleep(0)
        target = seen_idle
        idle = await sr.run_retention(now, automatic=True)
        target = seen_admin
        rep = await admin
        return idle, rep, seen_idle

    idle, admin_rep, seen_idle = db(go())
    assert idle["event"] == "retention_idle"
    assert _snapshot_aggregates(seen_idle) == []
    assert admin_rep["prune"]["would_delete"] > 0
    assert admin_rep["prune"]["deleted"] == 0


# ── 6. 취소·재시작 ────────────────────────────────────────────────────────
def test_cancelled_idle_leaves_the_connection_usable(db, monkeypatch):
    _idle_config(monkeypatch)

    async def go():
        now = await _seed_old()
        c = await database.get_db()
        task = asyncio.ensure_future(sr.run_retention(now, automatic=True))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await c.execute("SELECT 1")
        rep = await sr.run_retention(now, automatic=True)
        assert rep["event"] == "retention_idle"

    db(go())


def test_worker_actually_asks_for_automatic_mode(db, monkeypatch):
    """워커가 `automatic=True`를 넘기지 않으면 strict idle은 통째로 죽은 코드가 된다."""
    seen: dict = {}

    async def fake(*a, **kw):
        seen.update(kw)
        raise asyncio.CancelledError

    monkeypatch.setenv("SINGCUP_RETENTION_START_DELAY_SECONDS", "0")
    monkeypatch.setattr(sr, "run_retention", fake)

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await sr.start_retention_worker()

    db(go())
    assert seen.get("automatic") is True
