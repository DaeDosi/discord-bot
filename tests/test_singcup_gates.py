"""SINGCUP-1 — 등록 게이트와 갱신 게이트의 분리.

확정된 제품 요구는 이렇다.

    이벤트가 끝나면 **신규 참가자·클립 등록만** 멈춘다.
    기존 클립의 조회수·하트·순위·급상승·시간별 스냅샷은 **계속 간다.**

예전에는 이 전부가 `event_status()` 하나에 묶여 함께 멈췄다. 이 파일은 그 분리가
실제로 성립하는지, 그리고 **다시 하나로 합쳐지지 않는지**를 고정한다.

`SINGCUP_END_AT`을 늘려서 통과시키면 안 된다 — `END_AT`은 참가 판정 창의 정의라
늘리는 순간 종료 후 업로드된 클립이 참가로 편입된다. 그래서 이 테스트들은
**게이트 함수를 직접 검사**하거나, 시각을 인자로 넘겨 판정한다.
"""
from datetime import timedelta

import pytest

# ── 게이트 자체 ─────────────────────────────────────────────────────────────

def _times():
    import singcup_collector as scol
    before = scol.START_AT - timedelta(days=1)
    during = scol.START_AT + timedelta(days=1)
    after = scol.END_AT + timedelta(days=5)
    return before, during, after


def test_gate_matrix_before_during_after():
    """종료 전후로 **등록만** 닫히고 나머지 셋은 열린 채로 남는다."""
    import singcup_collector as scol
    before, during, after = _times()

    assert scol.event_status(before) == "UPCOMING"
    assert scol.event_status(during) == "LIVE"
    assert scol.event_status(after) == "ENDED"

    # 시작 전 — 전부 닫힘(등록할 것도, 갱신할 대상도 없다)
    assert scol.registration_open(before) is False
    assert scol.metrics_refresh_open(before) is False
    assert scol.ranking_refresh_open(before) is False
    assert scol.snapshot_refresh_open(before) is False

    # 이벤트 기간 — 전부 열림
    assert scol.registration_open(during) is True
    assert scol.metrics_refresh_open(during) is True
    assert scol.ranking_refresh_open(during) is True
    assert scol.snapshot_refresh_open(during) is True

    # 종료 후 — **등록만** 닫히고 나머지는 그대로
    assert scol.registration_open(after) is False, "종료 후 신규 등록은 닫혀야 한다"
    assert scol.metrics_refresh_open(after) is True, "종료 후에도 지표는 갱신한다"
    assert scol.ranking_refresh_open(after) is True, "종료 후에도 순위는 계산한다"
    assert scol.snapshot_refresh_open(after) is True, "종료 후에도 스냅샷은 만든다"


def test_gates_are_not_one_switch():
    """네 게이트가 **같은 함수의 별칭이 아니어야** 한다.

    누군가 `metrics_refresh_open = registration_open`처럼 되돌리면 요구가 조용히
    깨진다. 종료 후 시각에서 값이 갈리는지로 확인한다.
    """
    import singcup_collector as scol
    _, _, after = _times()
    assert scol.registration_open(after) != scol.metrics_refresh_open(after)


@pytest.mark.parametrize("env,fn", [
    ("SINGCUP_REGISTRATION_ENABLED", "registration_open"),
    ("SINGCUP_METRICS_REFRESH_ENABLED", "metrics_refresh_open"),
    ("SINGCUP_RANKING_REFRESH_ENABLED", "ranking_refresh_open"),
    ("SINGCUP_SNAPSHOT_REFRESH_ENABLED", "snapshot_refresh_open"),
])
def test_each_gate_has_an_independent_kill_switch(env, fn, monkeypatch):
    """비상 정지는 게이트마다 따로 걸린다(하나를 끄면 하나만 닫힌다)."""
    import singcup_collector as scol
    _, during, _ = _times()
    assert getattr(scol, fn)(during) is True
    monkeypatch.setenv(env, "false")
    assert getattr(scol, fn)(during) is False
    others = {"registration_open", "metrics_refresh_open",
              "ranking_refresh_open", "snapshot_refresh_open"} - {fn}
    for o in others:
        assert getattr(scol, o)(during) is True, f"{env}가 {o}까지 껐다"


def test_default_needs_no_env(monkeypatch):
    """운영에 새 환경변수를 설정하지 않아도 확정 요구대로 동작한다."""
    import singcup_collector as scol
    for e in ("SINGCUP_REGISTRATION_ENABLED", "SINGCUP_METRICS_REFRESH_ENABLED",
              "SINGCUP_RANKING_REFRESH_ENABLED", "SINGCUP_SNAPSHOT_REFRESH_ENABLED"):
        monkeypatch.delenv(e, raising=False)
    _, _, after = _times()
    assert scol.registration_open(after) is False
    assert scol.metrics_refresh_open(after) is True


# ── 소비자가 새 게이트를 쓰는지 ─────────────────────────────────────────────

def test_no_consumer_gates_refresh_on_event_status():
    """지표·순위·스냅샷 경로가 `event_status()`로 직접 분기하지 않는다.

    이게 이 작업의 핵심 회귀 방어선이다. 누군가 편의로 `event_status() == "LIVE"`를
    다시 넣으면 종료와 동시에 갱신이 멈춘다.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "web" / "backend"
    offenders = []
    for name in ("singcup_sweep.py", "singcup_audit.py", "singcup_kr_poller.py"):
        for i, line in enumerate((root / name).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#")[0]
            if "event_status()" in code and ("==" in code or "!=" in code):
                offenders.append(f"{name}:{i}: {line.strip()[:80]}")
    # sweep의 start_sweep_worker는 '완전히 꺼진 경우'를 가리려고 함께 본다 —
    # 그 한 줄은 metrics_refresh_open()과 AND로 묶여 있어야 한다.
    allowed = [o for o in offenders if "metrics_refresh_open" in o]
    assert not [o for o in offenders if o not in allowed], offenders


def test_registration_paths_still_gated():
    """등록 경로는 여전히 닫혀야 한다 — 갱신을 열면서 같이 열리면 안 된다."""
    import singcup_clips as sc
    import singcup_collector as scol
    _, _, after = _times()
    assert scol.registration_open(after) is False
    # retag(무태그 재확인)는 미등록 클립을 등록하는 일이라 '등록' 축이다
    assert sc.retag_enabled() is False, "종료 후 재확인이 열려 있다(신규 편입 위험)"


# ── 종료 후 동작 (실제 DB) ──────────────────────────────────────────────────

CID = "c" * 32
UID = "GATECLIP01"


def _seed_clip(sc, now, *, heart=10, view=100, last_view_at=None, age=7200):
    """이벤트 기간에 등록된 것처럼 클립 1개 + 소유 스트리머를 심는다.

    컬럼 구성은 `test_singcup_sweep.py`의 시드와 같다 — 기본 스키마에 없는
    `last_metrics_at`/`last_attempt_at`은 append-only 마이그레이션으로 붙은 컬럼이다.
    """
    from database import get_db

    async def _go():
        db = await get_db()
        await db.execute(
            "INSERT OR REPLACE INTO singcup_clips (clip_uid, event_id, owner_channel_id,"
            " video_id, rec_id, clip_title, thumbnail_image_url, description,"
            " created_at, heart_count, view_count, duration, adult, blind_type,"
            " metrics_ok, active, missing_scan_count, first_collected_at,"
            " last_collected_at, row_updated_at, last_metrics_at, last_attempt_at,"
            " last_heart_at, last_view_at)"
            " VALUES (?,?,?,?,'','게이트 테스트','','#싱드컵',?,?,?,60,0,'',1,1,0,?,?,?,?,?,?,?)",
            (UID, sc.EVENT_ID, CID, "vid1", now - 86400, heart, view,
             now - age, now - age, now - age, now - age, now - age,
             now - age, now - age if last_view_at is None else last_view_at))
        await db.execute(
            "INSERT OR REPLACE INTO singcup_streamers (channel_id, event_id, channel_name,"
            " channel_image_url, follower_count, verified_mark, representative_clip_uid,"
            " tagged_clip_count, last_channel_updated_at, row_updated_at)"
            " VALUES (?,?,'게이트채널','',0,0,?,1,?,?)",
            (CID, sc.EVENT_ID, UID, now, now))
        await db.commit()
    return _go()


def test_metrics_update_works_after_event_end(db):
    """**종료 후에도** 기존 클립의 조회수·하트가 갱신된다."""
    import time as _t

    import singcup_clips as sc
    import singcup_collector as scol
    _, _, after = _times()
    assert scol.metrics_refresh_open(after) is True

    now = int(_t.time())

    async def _go():
        await _seed_clip(sc, now)
        before = await _read(sc)
        await sc._apply_metrics(UID, 999, 5555, True, True, now)
        return before, await _read(sc)

    async def _read(sc):
        from database import get_db
        db = await get_db()
        r = await (await db.execute(
            "SELECT heart_count, view_count FROM singcup_clips WHERE clip_uid=?",
            (UID,))).fetchone()
        return (int(r["heart_count"]), int(r["view_count"])) if r else None

    before, after_v = db(_go())
    assert before == (10, 100)
    assert after_v == (999, 5555), "종료 후 지표 갱신이 막혀 있다"


def test_hourly_snapshot_advances_after_event_end(db):
    """**종료 후에도** 시간별 스냅샷이 만들어진다 — 급상승의 기준선이다."""
    import time as _t

    import singcup_clips as sc
    import singcup_collector as scol
    _, _, after = _times()
    assert scol.snapshot_refresh_open(after) is True

    now = int(_t.time())

    async def _go():
        from database import get_db
        await _seed_clip(sc, now)
        await sc.recompute_ranking(now)
        db_ = await get_db()
        n0 = (await (await db_.execute(
            "SELECT COUNT(*) n FROM singcup_snapshot_hourly")).fetchone())["n"]
        made = await sc.ensure_hourly_snapshot(now)
        n1 = (await (await db_.execute(
            "SELECT COUNT(*) n FROM singcup_snapshot_hourly")).fetchone())["n"]
        return n0, made, n1

    n0, made, n1 = db(_go())
    assert n1 >= n0, "종료 후 스냅샷이 줄었다"
    assert made is not None


def test_final_standings_is_not_frozen_while_ranking_is_open(db):
    """순위가 계속 갱신되는 동안 `final_standings`로 얼리지 않는다."""
    import time as _t

    import singcup_collector as scol
    import singcup_retention as sr
    assert scol.event_status() == "ENDED", "이 테스트는 종료 상태를 전제한다"
    assert scol.ranking_refresh_open() is True

    async def _go():
        return await sr.save_final_standings(int(_t.time()))

    res = db(_go())
    assert res["saved"] == 0
    assert "순위" in res.get("note", ""), res


def test_final_standings_saves_once_ranking_is_closed(db, monkeypatch):
    """순위 갱신을 **명시적으로 닫으면** 그때는 최종 성적을 저장한다."""
    import time as _t

    import singcup_collector as scol
    import singcup_retention as sr
    monkeypatch.setenv("SINGCUP_RANKING_REFRESH_ENABLED", "false")
    assert scol.ranking_refresh_open() is False

    async def _go():
        return await sr.save_final_standings(int(_t.time()))

    res = db(_go())
    assert "note" not in res or "순위" not in res["note"], res


def test_kr_poller_keeps_leasing_after_event_end(db):
    """종료 후에도 KR poller가 **기존** 클립의 조회수 복구 작업을 받는다."""
    import time as _t

    import singcup_clips as sc
    import singcup_collector as scol
    import singcup_kr_poller as krp
    assert scol.metrics_refresh_open() is True

    now = int(_t.time())

    async def _go():
        # 하트는 있는데 조회수를 한 번도 못 받은 클립 = 지역 차단 지문
        await _seed_clip(sc, now, heart=7, view=0, last_view_at=0)
        return await krp.lease_tasks(now, 5)

    tasks = db(_go())
    assert any(t["clipUid"] == UID for t in tasks), f"종료 후 후보가 비었다: {tasks}"


def test_kr_poller_stops_when_metrics_gate_closed(db, monkeypatch):
    """비상 정지를 걸면 KR poller도 함께 멈춘다."""
    import time as _t

    import singcup_clips as sc
    import singcup_kr_poller as krp
    monkeypatch.setenv("SINGCUP_METRICS_REFRESH_ENABLED", "false")
    now = int(_t.time())

    async def _go():
        await _seed_clip(sc, now)
        return await krp.lease_tasks(now, 5)

    assert db(_go()) == []


def test_no_new_owner_or_clip_leaks_in_after_end(db):
    """종료 후 등록 경로가 **아무 행도 만들지 않는다**.

    갱신을 열면서 등록까지 새어 들어오면 순위가 소급 변경된다 — 이 작업에서
    가장 위험한 실패 모드다.
    """
    import singcup_clips as sc
    import singcup_collector as scol
    assert scol.registration_open() is False

    async def _go():
        from database import get_db
        db_ = await get_db()

        async def counts():
            c = (await (await db_.execute(
                "SELECT COUNT(*) n FROM singcup_clips")).fetchone())["n"]
            s = (await (await db_.execute(
                "SELECT COUNT(*) n FROM singcup_streamers")).fetchone())["n"]
            return int(c), int(s)

        before = await counts()
        res = await sc.recheck_untagged_clips()
        return before, await counts(), res

    before, after_c, res = db(_go())
    assert before == after_c, f"종료 후 등록 경로가 행을 만들었다: {before} → {after_c}"
    assert res.get("status") == sc_skipped(), res


def sc_skipped():
    import singcup_clips as sc
    return sc.ST_SKIPPED


def test_screen_stops_saying_stale_after_end(db):
    """**화면의 '수집 지연'이 데이터만으로 풀린다** — UI 문구를 고치지 않아도 된다.

    `/api/singcup/main`의 `collector.stale`은 클립 수집기가 아니라
    `MAX(singcup_snapshots.collected_at)`에서 나온다. 종료 후 스냅샷이 계속 만들어지면
    이 값이 전진하므로 상단 '집계 지연'과 급상승의 '수집 지연' 배지가 저절로 사라진다.
    이 테스트가 그 연결을 고정한다 — 스냅샷 게이트를 닫으면 다시 stale이 된다.
    """
    import time as _t

    import singcup_clips as sc
    now = int(_t.time())

    async def _go():
        await _seed_clip(sc, now)
        await sc.recompute_ranking(now)
        await sc.ensure_hourly_snapshot(now)
        sc.invalidate_main_cache()
        return await sc.load_main()

    main = db(_go())
    assert main["collector"]["stale"] is False, \
        "스냅샷이 전진했는데도 화면이 '수집 지연'이라고 말한다"
    assert main["collector"]["lastSuccessAt"] is not None


def test_movers_persist_path_runs_after_event_end(db, monkeypatch):
    """**종료 후에도 하트 급상승 저장 경로가 돈다** — computed_at이 새로 찍힌다.

    급상승 *내용*은 1시간 전 기준선 스냅샷이 있어야 나오고, 그 계산 규칙은 이미
    `test_singcup_snapshot_baseline.py`가 검사한다. 여기서 보는 것은 **게이트**다 —
    종료됐다는 이유로 저장 경로가 통째로 막히지 않는지.
    막히면 화면은 '이전 집계' 배지를 단 채 영원히 굳는다.
    """
    import datetime as _dt
    import time as _t

    import singcup_clips as sc
    import singcup_collector as scol
    assert scol.ranking_refresh_open() is True
    assert scol.snapshot_refresh_open() is True

    now = int(_t.time())
    payload = [{"channelId": CID, "channelName": "게이트채널", "delta": 42, "rank": 1}]
    base_iso = _dt.datetime.fromtimestamp(now - 3600, sc._KST).isoformat()

    # 저장 경로는 `load_main_entry`(캐시 엔트리)를 읽는다 — 여기서 다시 계산하지 않는다.
    async def fake_entry(*a, **kw):
        return ({"data": {"topHeartMovers1h": payload,
                          "topHeartMovers1hStale": False,
                          "topHeartMovers1hBaseAt": base_iso}}, "test")

    monkeypatch.setattr(sc, "load_main_entry", fake_entry)

    async def _go():
        from database import get_db
        await _seed_clip(sc, now, age=60)
        result = await sc.persist_top_movers_snapshot(source="gate-test")
        db_ = await get_db()
        row = await (await db_.execute(
            "SELECT computed_at FROM singcup_top_movers WHERE event_id=?",
            (sc.EVENT_ID,))).fetchone()
        return result, (int(row["computed_at"]) if row else None)

    result, computed_at = db(_go())
    assert result not in (None, "gated"), f"저장 경로가 막혔다: {result}"
    assert computed_at is not None, "종료 후 급상승이 한 번도 저장되지 않았다"
    assert computed_at >= now - 5
