"""SINGCUP-FINAL-1 — campaign migration 감사.

fresh DB · 운영 형태(기존 PIKU 행이 있는) DB · 부분 적용 DB · 반복 init · 중간 실패 뒤 재기동 ·
기본값·index·unique·기존 행 불변·구버전 코드에서의 무해함.
"""
# ruff: noqa: E501 — 공격 시나리오·SQL fixture는 한 줄이 길어야 읽힌다(신규 테스트 파일에만).
import asyncio
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

TABLES_WITH_CAMPAIGN = ("piku_sources", "piku_datasets", "piku_mappings", "piku_collector_state",
                        "piku_collector_teams", "piku_collector_tokens", "piku_collector_challenges")


def _cols(conn, table):
    return {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})")}


def _run(db_file):
    """새 프로세스 흉내: 새 이벤트 루프에서 init_db 한 번."""
    from database import db as dbmod

    async def go():
        import database
        if dbmod._db is not None:
            await dbmod.close_db()
        orig = dbmod.DB_PATH
        dbmod.DB_PATH = str(db_file)
        dbmod._db = None
        try:
            await database.init_db()
        finally:
            await dbmod.close_db()
            dbmod.DB_PATH = orig
            dbmod._db = None

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()


def _legacy_db(path):
    """운영 형태: 컬럼 없는 PIKU 테이블 + 예선 데이터가 이미 있는 DB(구버전 코드가 만든 모양)."""
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE piku_sources (division TEXT PRIMARY KEY, url TEXT NOT NULL,
        observed_title TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
        last_attempt_at INTEGER NOT NULL DEFAULT 0, last_success_at INTEGER NOT NULL DEFAULT 0,
        last_error_kind TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE piku_datasets (id INTEGER PRIMARY KEY AUTOINCREMENT, division TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'building', source TEXT NOT NULL DEFAULT '',
        source_url TEXT NOT NULL DEFAULT '', pages INTEGER NOT NULL DEFAULT 0,
        entry_count INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
        activated_at INTEGER NOT NULL DEFAULT 0);
    CREATE UNIQUE INDEX idx_piku_datasets_one_active ON piku_datasets(division) WHERE status='active';
    CREATE TABLE piku_entries (dataset_id INTEGER NOT NULL, source_rank INTEGER, name TEXT NOT NULL,
        thumbnail_url TEXT NOT NULL DEFAULT '', win_rate REAL, match_rate REAL,
        song_title TEXT NOT NULL DEFAULT '', artist_name TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (dataset_id, name));
    CREATE TABLE piku_mappings (division TEXT NOT NULL, piku_name TEXT NOT NULL, channel_id TEXT,
        state TEXT NOT NULL DEFAULT 'unmapped', updated_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (division, piku_name));
    CREATE TABLE piku_collector_tokens (token_hash TEXT PRIMARY KEY, division TEXT NOT NULL,
        expires_at INTEGER NOT NULL, used_at INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL);
    CREATE TABLE piku_auto_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER NOT NULL,
        trigger TEXT NOT NULL DEFAULT 'alarm', started_at INTEGER NOT NULL,
        finished_at INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT 'running',
        female_ok INTEGER NOT NULL DEFAULT 0, female_kind TEXT NOT NULL DEFAULT '',
        female_rows INTEGER NOT NULL DEFAULT 0, male_ok INTEGER NOT NULL DEFAULT 0,
        male_kind TEXT NOT NULL DEFAULT '', male_rows INTEGER NOT NULL DEFAULT 0,
        groups_ok INTEGER NOT NULL DEFAULT 0, groups_kind TEXT NOT NULL DEFAULT '',
        groups_rows INTEGER NOT NULL DEFAULT 0);
    INSERT INTO piku_sources (division, url, last_success_at, updated_at)
      VALUES ('female_solo','https://www.piku.co.kr/w/rank/8jGsHE',1755607901,1755607901),
             ('male_solo','https://www.piku.co.kr/w/rank/7PqH44',1755607901,1755607901),
             ('groups','https://www.piku.co.kr/w/rank/7fXoNs',1755607901,1755607901);
    INSERT INTO piku_datasets (id, division, status, source, entry_count, created_at, activated_at)
      VALUES (11,'female_solo','active','browser_collector',64,1755600000,1755607901),
             (12,'male_solo','active','browser_collector',64,1755600000,1755607901),
             (13,'groups','active','browser_collector',32,1755600000,1755607901),
             (9,'groups','superseded','browser_collector',32,1755500000,1755500001);
    INSERT INTO piku_entries VALUES (13,1,'조별하','https://img/1.png',18.6,67.2,'기도','비투비'),
                                    (13,2,'유람 Yuram','',14.3,65.5,'Lost Stars','Adam Levine');
    INSERT INTO piku_mappings VALUES ('groups','조별하','abc','confirmed',1755607000);
    INSERT INTO piku_auto_runs (id, device_id, started_at, finished_at, outcome, female_ok, female_rows)
      VALUES (1, 1, 1755600000, 1755600100, 'partial', 1, 64);
    """)
    conn.commit()
    conn.close()


def _snapshot(path):
    conn = sqlite3.connect(path)
    out = {}
    for t in ("piku_sources", "piku_datasets", "piku_entries", "piku_mappings", "piku_auto_runs"):
        cols = [c for c in _cols(conn, t) if c not in ("campaign", "scheduled_at")]
        out[t] = conn.execute(f"SELECT {', '.join(cols)} FROM {t} ORDER BY 1, 2").fetchall()
    conn.close()
    return out


def test_fresh_db_has_all_campaign_columns_and_new_table(tmp_path):
    db = tmp_path / f"fresh-{uuid.uuid4().hex}.db"
    _run(db)
    conn = sqlite3.connect(db)
    for t in TABLES_WITH_CAMPAIGN:
        c = _cols(conn, t)["campaign"]
        assert c[2].upper() == "TEXT" and c[3] == 1 and c[4] == "'qualifier'", t   # NOT NULL DEFAULT
    runs = _cols(conn, "piku_auto_runs")
    assert runs["campaign"][4] == "''" and runs["scheduled_at"][4] == "0"
    src = _cols(conn, "piku_auto_run_sources")
    assert set(src) == {"run_id", "campaign", "source", "ok", "kind", "row_count"}
    pk = [r for r in conn.execute("PRAGMA table_info(piku_auto_run_sources)") if r[5]]
    assert [r[1] for r in sorted(pk, key=lambda r: r[5])] == ["run_id", "campaign", "source"]
    # 기존 unique 제약(부문당 활성 dataset 1개)은 그대로다.
    idx = {r[1] for r in conn.execute("PRAGMA index_list(piku_datasets)")}
    assert "idx_piku_datasets_one_active" in idx
    conn.close()


def test_legacy_db_migrates_in_place_and_values_are_unchanged(tmp_path):
    db = tmp_path / f"legacy-{uuid.uuid4().hex}.db"
    _legacy_db(db)
    before = _snapshot(db)
    _run(db)
    after = _snapshot(db)
    assert after == before, "기존 행·값이 바뀌었다"
    conn = sqlite3.connect(db)
    # 기존 행은 전부 qualifier(기본값)로 읽힌다. NULL은 없다.
    for t in TABLES_WITH_CAMPAIGN:
        assert conn.execute(f"SELECT count(*) FROM {t} WHERE campaign IS NULL OR campaign<>'qualifier'"
                            ).fetchone()[0] == 0, t
    assert conn.execute("SELECT campaign, scheduled_at FROM piku_auto_runs").fetchone() == ("", 0)
    # 활성 dataset·마지막 공개 시각 그대로.
    assert conn.execute("SELECT id FROM piku_datasets WHERE division='groups' AND status='active'"
                        ).fetchone() == (13,)
    assert conn.execute("SELECT last_success_at FROM piku_sources WHERE division='groups'"
                        ).fetchone() == (1755607901,)
    conn.close()


def test_repeated_init_is_idempotent_on_legacy_db(tmp_path):
    db = tmp_path / f"rep-{uuid.uuid4().hex}.db"
    _legacy_db(db)
    for _ in range(4):
        _run(db)
    conn = sqlite3.connect(db)
    for t in TABLES_WITH_CAMPAIGN:
        assert sum(1 for c in _cols(conn, t) if c == "campaign") == 1
    assert conn.execute("SELECT count(*) FROM piku_entries").fetchone()[0] == 2
    conn.close()


def test_partial_migration_db_completes(tmp_path):
    """일부 테이블에만 컬럼이 있는 DB(중간 실패·이중 기동)도 나머지를 채운다."""
    db = tmp_path / f"part-{uuid.uuid4().hex}.db"
    _legacy_db(db)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE piku_sources ADD COLUMN campaign TEXT NOT NULL DEFAULT 'qualifier'")
    conn.execute("ALTER TABLE piku_auto_runs ADD COLUMN scheduled_at INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()
    _run(db)
    conn = sqlite3.connect(db)
    for t in TABLES_WITH_CAMPAIGN:
        assert "campaign" in _cols(conn, t), t
    assert "campaign" in _cols(conn, "piku_auto_runs")
    assert "piku_auto_run_sources" in {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()


def test_failure_midway_leaves_no_open_transaction_and_recovers(tmp_path, monkeypatch):
    """strict 구간이 도중에 실패해도 열린 트랜잭션이 남지 않고, 다음 기동이 이어서 완성한다."""
    db = tmp_path / f"fail-{uuid.uuid4().hex}.db"
    _legacy_db(db)
    from database import db as dbmod
    orig = dbmod._PIKU_CAMPAIGN_COLUMNS
    # 세 번째 항목에서 문법 오류를 일으킨다.
    broken = orig[:2] + (("piku_collector_tokens", "campaign", "TEXT NOT NULL DEFAULT 'qualifier' ZZZ"),) + orig[3:]
    monkeypatch.setattr(dbmod, "_PIKU_CAMPAIGN_COLUMNS", broken)
    with pytest.raises(Exception):
        _run(db)
    monkeypatch.setattr(dbmod, "_PIKU_CAMPAIGN_COLUMNS", orig)
    conn = sqlite3.connect(db)
    assert not conn.in_transaction
    conn.close()
    _run(db)                                  # 재기동
    conn = sqlite3.connect(db)
    for t in TABLES_WITH_CAMPAIGN:
        assert "campaign" in _cols(conn, t), t
    assert _snapshot(db)["piku_entries"] == [(13, 1, "조별하", "https://img/1.png", 18.6, 67.2, "기도", "비투비"),
                                            (13, 2, "유람 Yuram", "", 14.3, 65.5, "Lost Stars", "Adam Levine")]
    conn.close()


def test_new_columns_are_harmless_to_old_code_shapes(tmp_path):
    """rollback(구버전 코드)이 되돌아와도 append-only 잔재는 무해하다 — 구 INSERT 형태가 그대로 통한다."""
    db = tmp_path / f"old-{uuid.uuid4().hex}.db"
    _legacy_db(db)
    _run(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO piku_collector_tokens (token_hash, division, expires_at, created_at)"
                 " VALUES ('h','groups',1,1)")
    conn.execute("INSERT INTO piku_auto_runs (device_id, trigger, started_at, outcome)"
                 " VALUES (1,'alarm',1,'running')")
    conn.execute("INSERT INTO piku_mappings (division, piku_name, channel_id, state, updated_at)"
                 " VALUES ('groups','x',NULL,'unmapped',0)")
    conn.commit()
    assert conn.execute("SELECT campaign FROM piku_collector_tokens WHERE token_hash='h'").fetchone() == ("qualifier",)
    assert conn.execute("SELECT campaign, scheduled_at FROM piku_auto_runs ORDER BY id DESC LIMIT 1").fetchone() == ("", 0)
    conn.close()
