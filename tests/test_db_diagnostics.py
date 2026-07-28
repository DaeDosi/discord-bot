"""DB 진단 — 읽기 전용이어야 하고, 오너만 볼 수 있어야 한다."""
import time

import db_diagnostics as dg

import database


async def _seed(n=50):
    c = await database.get_db()
    now = int(time.time())
    await c.executemany(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at)"
        " VALUES ('e','c','o',1,0,0,0,1,?)",
        [(now - i * 60,) for i in range(n)])
    await c.commit()


def test_collect_reports_sizes_and_pragmas(db):
    db(_seed())
    d = db(dg.collect())
    assert d["files"]["db_bytes"] > 0
    p = d["pragmas"]
    assert p["page_size"] > 0 and p["page_count"] > 0
    assert p["journal_mode"] == "wal"
    assert p["used_bytes"] + p["reclaimable_bytes"] == p["page_size"] * p["page_count"]


def test_growth_projection_uses_measured_rows(db):
    db(_seed(120))
    g = db(dg.collect())["growth"]
    assert g["rows_per_day"] >= 120
    assert g["projected_30d"] >= g["projected_7d"] >= g["projected_1d"]
    assert g["days_until_full"] is None or g["days_until_full"] >= 0


def test_table_stats_include_row_counts_and_window_counts(db):
    db(_seed(30))
    t = next(x for x in db(dg.collect())["tables"] if x["table"] == "singcup_snapshots")
    assert t["rows"] == 30
    assert t["added_1h"] == 30 and t["added_24h"] == 30
    assert t["pruned"] is True, "보존정책이 걸린 테이블로 표시돼야 한다"


def test_unbounded_tables_are_flagged(db):
    """프루닝이 없는데 계속 늘어나는 테이블을 짚어줘야 다음 사고를 막는다."""
    d = db(dg.collect())
    assert "unbounded_tables" in d
    assert "singcup_snapshots" not in d["unbounded_tables"], "이제 프루닝이 있다"


def test_collect_does_not_modify_data(db):
    """진단은 읽기 전용이어야 한다 — 행 수가 변하면 안 된다."""
    db(_seed(40))

    async def count():
        c = await database.get_db()
        r = await (await c.execute("SELECT COUNT(*) n FROM singcup_snapshots")).fetchone()
        return r["n"]

    before = db(count())
    db(dg.collect())
    assert db(count()) == before


def test_response_has_no_file_path_or_url(db):
    """경로·접속 문자열이 응답에 섞이면 그 자체가 정보 노출이다."""
    import json
    body = json.dumps(db(dg.collect()), default=str)
    assert database.DB_PATH not in body
    assert "sqlite:///" not in body and "://" not in body


def test_integrity_check_passes(db):
    r = db(dg.integrity_check())
    assert r["ok"] is True and r["messages"] == ["ok"]


def test_endpoints_require_owner():
    """오너가 아니면 403 — 테이블 구성과 증가 속도는 공개 대상이 아니다."""
    from fastapi.testclient import TestClient
    from test_security import _load_app
    client = TestClient(_load_app())
    for path in ("/api/admin/db/diagnostics", "/api/admin/db/integrity"):
        assert client.get(path).status_code in (401, 403)


# ── 운영 부하 제한 ─────────────────────────────────────────────────────────
def test_result_is_cached(db):
    dg.reset_cache()
    a = db(dg.collect_cached())
    b = db(dg.collect_cached())
    assert a["cached"] is False and b["cached"] is True
    assert b["cache_age_seconds"] >= 0
    dg.reset_cache()


def test_force_bypasses_cache(db):
    dg.reset_cache()
    db(dg.collect_cached())
    assert db(dg.collect_cached(force=True))["cached"] is False
    dg.reset_cache()


def test_elapsed_is_recorded(db):
    dg.reset_cache()
    assert db(dg.collect_cached())["elapsed_ms"] >= 0
    dg.reset_cache()


def test_quick_check_is_the_default(db):
    r = db(dg.integrity_check())
    assert r["mode"] == "quick" and r["ok"] is True
    full = db(dg.integrity_check(quick=False))
    assert full["mode"] == "full" and full["ok"] is True
