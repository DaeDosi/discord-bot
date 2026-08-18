"""이번 기능(PIKU 순위 + 싱드컵 곡 정보)이 추가한 스키마의 **엄격 migration** 계약.

`database/db.py`에는 예전부터 "새 컬럼 추가 SQL을 리스트에 append하고 실패는 무시"
하는 legacy 루프가 있다(`except Exception: pass`). 그 루프는 lock·I/O·손상·문법
오류까지 전부 조용히 삼키므로, **이번에 추가한 7개 SQL은 그 경로를 쓰지 않는다.**

여기서 검증하는 계약:
  · 빈 DB에서 테이블 3 · 컬럼 2 · 초기 행 2를 만든다
  · 몇 번을 돌려도 중복이 생기지 않고 기존 값이 보존된다
  · 부분 적용 상태에서 **없는 것만** 채운다
  · lock·문법 오류 등 진짜 실패는 **삼키지 않고 전파**한다
  · 컬럼 존재 판정은 예외 메시지가 아니라 `PRAGMA table_info` 조회로 한다

이 저장소에는 pytest-asyncio가 없어서, 다른 테스트와 같이 명시적 이벤트 루프를
쥐고 `env.run(...)`으로 코루틴을 돌린다.
"""
import asyncio
import inspect
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

import aiosqlite
import pytest

from database import db as dbmod

MIGRATE = dbmod._migrate_piku_and_qualifier_schema

NEW_TABLES = ("piku_collect_lock", "piku_worker_state", "singcup_qualifier_songs")
NEW_COLUMNS = ("song_title", "artist_name")

# 이번 변경이 legacy 루프에서 **빼낸** SQL 실행 단위 — 표와 코드가 어긋나면 실패한다.
EXPECTED_SQL_UNITS = 7

# 이전 커밋에서 이미 들어간 테이블이라 이번 strict migration의 대상이 아니다
# (여기에 컬럼 2개만 추가한다). 부분 적용 상태를 만들려면 미리 있어야 한다.
_BASE_TABLE = """
    CREATE TABLE piku_entries (
        dataset_id    INTEGER NOT NULL,
        source_rank   INTEGER,
        name          TEXT    NOT NULL,
        thumbnail_url TEXT    NOT NULL DEFAULT '',
        win_rate      REAL,
        match_rate    REAL,
        PRIMARY KEY (dataset_id, name)
    )
"""


class Env:
    """운영 DB와 완전히 분리된 임시 파일 연결 + 동기 헬퍼."""

    def __init__(self, loop, conn):
        self.loop, self.conn = loop, conn

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def migrate(self, conn=None):
        return self.run(MIGRATE(self.conn if conn is None else conn))

    def exec(self, sql, *args):
        self.run(self.conn.execute(sql, args))
        self.run(self.conn.commit())

    def rows(self, sql):
        cur = self.run(self.conn.execute(sql))
        return [tuple(r) for r in self.run(cur.fetchall())]

    def one(self, sql):
        return self.rows(sql)[0]

    def tables(self):
        return {r[0] for r in
                self.rows("SELECT name FROM sqlite_master WHERE type='table'")}

    def columns(self, table="piku_entries"):
        return [r[1] for r in self.rows(f"PRAGMA table_info({table})")]

    def count(self, table):
        return self.one(f"SELECT count(*) FROM {table}")[0]


@pytest.fixture
def env():
    path = Path(tempfile.gettempdir()) / f"nexbot-strictmig-{uuid.uuid4().hex}.db"
    loop = asyncio.new_event_loop()
    conn = loop.run_until_complete(aiosqlite.connect(path))
    loop.run_until_complete(conn.execute(_BASE_TABLE))
    loop.run_until_complete(conn.commit())
    e = Env(loop, conn)
    try:
        yield e
    finally:
        loop.run_until_complete(conn.close())
        loop.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(str(path) + suffix)
            except OSError:
                pass


# ── A. 빈 DB ────────────────────────────────────────────────────────────────
def test_a_empty_db_creates_tables(env):
    env.migrate()
    have = env.tables()
    for t in NEW_TABLES:
        assert t in have, f"{t} 미생성"


def test_a_empty_db_creates_columns(env):
    env.migrate()
    cols = env.columns()
    for col in NEW_COLUMNS:
        assert col in cols, f"piku_entries.{col} 미생성"


def test_a_empty_db_seeds_single_rows(env):
    env.migrate()
    assert env.count("piku_collect_lock") == 1
    assert env.count("piku_worker_state") == 1


def test_a_seed_rows_use_id_one(env):
    env.migrate()
    for t in ("piku_collect_lock", "piku_worker_state"):
        assert env.rows(f"SELECT id FROM {t}") == [(1,)]


def test_a_qualifier_songs_not_seeded(env):
    """곡 정보는 운영자가 넣는다 — 초기 행을 만들면 안 된다."""
    env.migrate()
    assert env.count("singcup_qualifier_songs") == 0


# ── B. 두 번 실행 ────────────────────────────────────────────────────────────
def test_b_second_run_succeeds(env):
    env.migrate()
    env.migrate()          # 예외가 나면 실패


def test_b_no_duplicate_rows_or_columns(env):
    for _ in range(3):
        env.migrate()
    assert env.count("piku_collect_lock") == 1
    assert env.count("piku_worker_state") == 1
    cols = env.columns()
    assert cols.count("song_title") == 1
    assert cols.count("artist_name") == 1


def test_b_existing_values_preserved(env):
    env.migrate()
    env.exec("UPDATE piku_collect_lock SET locked_until=987, owner='worker-a'"
             " WHERE id=1")
    env.exec("UPDATE piku_worker_state SET consecutive_failures=7,"
             " last_error_kind='http' WHERE id=1")
    env.migrate()
    assert env.one("SELECT locked_until, owner FROM piku_collect_lock"
                   " WHERE id=1") == (987, "worker-a")
    assert env.one("SELECT consecutive_failures, last_error_kind"
                   " FROM piku_worker_state WHERE id=1") == (7, "http")


def test_b_qualifier_song_rows_preserved(env):
    env.migrate()
    env.exec("INSERT INTO singcup_qualifier_songs (channel_id, song_title,"
             " artist_name, source, updated_at)"
             " VALUES ('ch1','어른','손디아','admin',10)")
    env.migrate()
    assert env.one("SELECT song_title, artist_name, source"
                   " FROM singcup_qualifier_songs") == ("어른", "손디아", "admin")


# ── C. 부분 적용 DB ─────────────────────────────────────────────────────────
def test_c_only_tables_exist(env):
    """테이블만 있고 컬럼이 없는 상태 → 컬럼만 채운다."""
    env.exec("CREATE TABLE piku_collect_lock (id INTEGER PRIMARY KEY CHECK (id = 1),"
             " locked_until INTEGER NOT NULL DEFAULT 0,"
             " owner TEXT NOT NULL DEFAULT '')")
    env.migrate()
    assert set(NEW_TABLES) <= env.tables()
    assert set(NEW_COLUMNS) <= set(env.columns())


def test_c_only_one_column_exists(env):
    """컬럼 하나만 있는 상태 → 나머지 하나만 추가한다."""
    env.exec("ALTER TABLE piku_entries ADD COLUMN song_title TEXT NOT NULL"
             " DEFAULT ''")
    env.migrate()
    cols = env.columns()
    assert cols.count("song_title") == 1
    assert cols.count("artist_name") == 1


def test_c_only_seed_rows_missing(env):
    """테이블은 있는데 초기 행이 지워진 상태 → 행만 복구한다."""
    env.migrate()
    env.exec("DELETE FROM piku_worker_state")
    assert env.count("piku_worker_state") == 0
    env.migrate()
    assert env.count("piku_worker_state") == 1


def test_c_tables_and_columns_all_present(env):
    """전부 적용된 상태 → 아무것도 바꾸지 않고 성공한다."""
    env.migrate()
    before = (sorted(env.tables()), env.columns())
    env.migrate()
    assert (sorted(env.tables()), env.columns()) == before


def test_c_existing_piku_entries_rows(env):
    """기존 데이터가 있는 상태에서 컬럼을 추가해도 행이 살아 있다."""
    env.exec("INSERT INTO piku_entries (dataset_id, source_rank, name,"
             " thumbnail_url, win_rate, match_rate)"
             " VALUES (1, 3, '가수A', 'http://x/i.png', 12.5, 40.0)")
    env.migrate()
    assert env.one("SELECT name, source_rank, win_rate, match_rate, song_title,"
                   " artist_name FROM piku_entries") == (
        "가수A", 3, 12.5, 40.0, "", "")


# ── D. 실제 오류 전파 ───────────────────────────────────────────────────────
class _FailingConn:
    """지정한 SQL 조각에서만 터지는 얇은 래퍼. 나머지는 실제 연결로 위임한다."""

    def __init__(self, real, needle, exc):
        self._real, self._needle, self._exc = real, needle, exc
        self.rolled_back = False

    async def execute(self, sql, *a, **kw):
        if self._needle in sql:
            raise self._exc
        return await self._real.execute(sql, *a, **kw)

    async def commit(self):
        return await self._real.commit()

    async def rollback(self):
        self.rolled_back = True
        return await self._real.rollback()


_CREATE_FAILURES = [
    sqlite3.OperationalError("database is locked"),
    sqlite3.OperationalError('near "CREAT": syntax error'),
    sqlite3.OperationalError("attempt to write a readonly database"),
    sqlite3.DatabaseError("database disk image is malformed"),
    sqlite3.IntegrityError("constraint failed"),
    MemoryError("out of memory"),
]


@pytest.mark.parametrize("exc", _CREATE_FAILURES, ids=lambda e: type(e).__name__)
def test_d_real_errors_propagate(env, exc):
    """CREATE 단계의 실패는 어떤 종류든 호출자에게 그대로 올라와야 한다."""
    bad = _FailingConn(env.conn, "piku_worker_state", exc)
    with pytest.raises(type(exc)):
        env.migrate(bad)


@pytest.mark.parametrize("exc", [
    sqlite3.OperationalError("database is locked"),
    sqlite3.OperationalError("attempt to write a readonly database"),
    sqlite3.DatabaseError("database disk image is malformed"),
], ids=["locked", "readonly", "malformed"])
def test_d_alter_errors_propagate(env, exc):
    """ALTER 단계의 실패도 '이미 있는 컬럼'이 아니면 삼키면 안 된다."""
    bad = _FailingConn(env.conn, "ADD COLUMN song_title", exc)
    with pytest.raises(type(exc)):
        env.migrate(bad)


def test_d_insert_errors_propagate(env):
    bad = _FailingConn(env.conn, "INSERT OR IGNORE INTO piku_worker_state",
                       sqlite3.OperationalError("database is locked"))
    with pytest.raises(sqlite3.OperationalError):
        env.migrate(bad)


def test_d_failure_rolls_back_open_transaction(env):
    """실패해도 열린 transaction을 남기지 않는다."""
    bad = _FailingConn(env.conn, "singcup_qualifier_songs",
                       sqlite3.OperationalError("database is locked"))
    with pytest.raises(sqlite3.OperationalError):
        env.migrate(bad)
    assert bad.rolled_back, "실패 후 rollback이 호출되지 않았다"
    assert env.conn._conn.in_transaction is False


def test_d_cancellation_is_not_success(env):
    """CancelledError를 '성공'으로 처리하면 안 된다."""
    bad = _FailingConn(env.conn, "piku_worker_state", asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        env.migrate(bad)


def test_d_no_open_transaction_after_success(env):
    env.migrate()
    assert env.conn._conn.in_transaction is False


def test_d_missing_base_table_is_reported(env):
    """`piku_entries`가 없으면 조용히 넘어가지 말고 실패해야 한다."""
    env.exec("DROP TABLE piku_entries")
    with pytest.raises(sqlite3.Error):
        env.migrate()


# ── E. 컬럼 race 처리 ───────────────────────────────────────────────────────
class _RaceConn:
    """ALTER 직전에 다른 실행이 같은 컬럼을 만든 상황을 흉내낸다."""

    def __init__(self, real, column, *, really_add):
        self._real, self._column, self._really_add = real, column, really_add
        self.alter_attempts = 0

    async def execute(self, sql, *a, **kw):
        if f"ADD COLUMN {self._column}" in sql:
            self.alter_attempts += 1
            if self._really_add:
                # 경쟁 실행이 실제로 컬럼을 만들어 둔 뒤 우리 ALTER가 충돌한 상황
                await self._real.execute(sql)
                await self._real.commit()
            raise sqlite3.OperationalError(
                f"duplicate column name: {self._column}")
        return await self._real.execute(sql, *a, **kw)

    async def commit(self):
        return await self._real.commit()

    async def rollback(self):
        return await self._real.rollback()


def test_e_race_with_column_actually_created_succeeds(env):
    """재조회에서 컬럼이 실제로 생긴 것이 확인되면 완료로 본다."""
    race = _RaceConn(env.conn, "song_title", really_add=True)
    env.migrate(race)
    assert race.alter_attempts == 1
    assert "song_title" in env.columns()


def test_e_duplicate_message_without_column_still_raises(env):
    """메시지에 duplicate가 있어도 컬럼이 없으면 원래 예외를 전파한다."""
    race = _RaceConn(env.conn, "song_title", really_add=False)
    with pytest.raises(sqlite3.OperationalError):
        env.migrate(race)


def test_e_column_check_is_schema_query_not_message(env):
    """존재 판정은 PRAGMA 조회로 한다 — 이미 있으면 ALTER를 아예 실행하지 않는다."""
    env.exec("ALTER TABLE piku_entries ADD COLUMN song_title TEXT NOT NULL"
             " DEFAULT ''")
    env.exec("ALTER TABLE piku_entries ADD COLUMN artist_name TEXT NOT NULL"
             " DEFAULT ''")
    race = _RaceConn(env.conn, "song_title", really_add=False)
    env.migrate(race)                  # ALTER가 돌면 여기서 예외가 났을 것
    assert race.alter_attempts == 0


# ── F. 기존 데이터 보존 ─────────────────────────────────────────────────────
def test_f_row_count_and_values_unchanged(env):
    rows = [(1, i, f"가수{i}", f"http://x/{i}.png", i * 1.5, i * 2.5)
            for i in range(1, 21)]
    env.run(env.conn.executemany(
        "INSERT INTO piku_entries (dataset_id, source_rank, name, thumbnail_url,"
        " win_rate, match_rate) VALUES (?,?,?,?,?,?)", rows))
    env.run(env.conn.commit())
    env.migrate()
    assert env.count("piku_entries") == 20
    assert env.rows(
        "SELECT dataset_id, source_rank, name, thumbnail_url, win_rate,"
        " match_rate FROM piku_entries ORDER BY source_rank") == rows


def test_f_new_columns_default_only(env):
    env.exec("INSERT INTO piku_entries (dataset_id, source_rank, name)"
             " VALUES (1,1,'가수A')")
    env.migrate()
    assert env.one("SELECT song_title, artist_name FROM piku_entries") == ("", "")


def test_f_no_destructive_statements():
    """DELETE/DROP/대량 UPDATE가 strict migration 본문에 없어야 한다."""
    src = (inspect.getsource(MIGRATE)
           + "\n".join(dbmod._PIKU_TABLES)
           + "\n".join(dbmod._PIKU_SEED_ROWS)
           + "\n".join(f"{c} {d}" for c, d in dbmod._PIKU_ENTRY_COLUMNS)).upper()
    for bad in ("DROP TABLE", "DELETE FROM", "TRUNCATE", "UPDATE "):
        assert bad not in src, f"파괴적 구문 발견: {bad}"


def test_f_statement_units_match_report():
    """SQL 실행 단위가 보고한 7개와 일치하는지 — 표와 코드의 동기화 장치."""
    assert len(dbmod._PIKU_TABLES) == 3
    assert len(dbmod._PIKU_SEED_ROWS) == 2
    assert len(dbmod._PIKU_ENTRY_COLUMNS) == 2
    units = (len(dbmod._PIKU_TABLES) + len(dbmod._PIKU_SEED_ROWS)
             + len(dbmod._PIKU_ENTRY_COLUMNS))
    assert units == EXPECTED_SQL_UNITS
    # 재실행 안전성은 예외 무시가 아니라 구문 자체로 얻는다.
    assert all("CREATE TABLE IF NOT EXISTS" in s for s in dbmod._PIKU_TABLES)
    assert all(s.startswith("INSERT OR IGNORE INTO") for s in dbmod._PIKU_SEED_ROWS)


# ── G. 자동 수집 OFF ────────────────────────────────────────────────────────
def test_g_migration_does_not_touch_network(env, monkeypatch):
    """strict migration은 외부 호출을 하지 않는다."""
    import httpx
    calls = []

    async def boom(*a, **kw):
        calls.append(a)
        raise AssertionError("migration이 외부 요청을 시도했다")

    monkeypatch.setattr(httpx.AsyncClient, "request", boom, raising=False)
    env.migrate()
    assert calls == []


def test_g_auto_collect_default_off(monkeypatch):
    """자동 수집은 기본 OFF — migration과 무관하게 켜지지 않는다."""
    from web.backend import singcup_piku as piku
    monkeypatch.delenv("PIKU_AUTO_COLLECT_ENABLED", raising=False)
    assert piku.auto_collect_enabled() is False


# ── 5. legacy 루프 불변 · 신규 SQL 분리 증거 ────────────────────────────────
def _db_source():
    return Path(dbmod.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_legacy_loop_still_has_single_swallowing_block():
    """legacy 루프는 이번 커밋에서 손대지 않는다(동작 변경 금지)."""
    assert _db_source().count("except Exception:\n            pass") == 1


def test_new_sql_not_in_legacy_list():
    """신규 7개 SQL이 legacy `for sql in [...]` 리스트에 남아 있지 않아야 한다."""
    src = _db_source()
    start = src.index("for sql in [")
    end = src.index("\n    ]:", start)
    legacy = src[start:end]
    for needle in ("piku_collect_lock", "piku_worker_state",
                   "singcup_qualifier_songs", "ADD COLUMN song_title",
                   "ADD COLUMN artist_name"):
        assert needle not in legacy, f"legacy 리스트에 아직 남아 있다: {needle}"


def test_strict_migration_called_from_init_db():
    assert "_migrate_piku_and_qualifier_schema(db)" in _db_source()


def test_strict_migration_has_no_bare_swallow():
    src = inspect.getsource(MIGRATE).replace("\r\n", "\n")
    assert "except Exception:\n        pass" not in src
    assert "except BaseException" not in src
