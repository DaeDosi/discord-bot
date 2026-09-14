"""SUPPORT-POLICY-1 — 수정 요청 보관 정책(A 균형형 + 보정 2건) 계약.

정책 정본(`web/backend/support_retention.py`):
  · dedupe_key       접수 후 7일 → ''
  · contact_email    min(접수 후 180일, 처리 후 30일) → ''
  · 미처리 행        접수 후 365일 삭제
  · 처리 행          상태 변경 후 180일 삭제

운영 DB를 쓰지 않는다(conftest 임시 DB / 테스트 전용 임시 파일).
시각은 전부 고정 Unix 초 — 벽시계에 기대지 않는다.
"""
import asyncio
import json
import sqlite3
import tempfile
import uuid
from pathlib import Path

import aiosqlite
import pytest
import support
import support_retention as ret
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from database import db as dbmod

DAY = 86400
NOW = 1_800_000_000          # 2027-01-15 UTC 무렵. 고정값.
TEST_SALT = "test-salt-0123456789abcdef"
OWNER = "111111111111111111"


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────

@pytest.fixture
def adb(db, monkeypatch):
    async def _clear():
        c = await database.get_db()
        await c.execute("DELETE FROM correction_requests")
        await c.commit()
    db(_clear())
    support.reset_state()
    ret.reset_state()
    monkeypatch.delenv("SUPPORT_RETENTION_ENABLED", raising=False)
    monkeypatch.delenv("SUPPORT_RETENTION_DRY_RUN", raising=False)
    return db


def _apply_mode(monkeypatch):
    monkeypatch.setenv("SUPPORT_RETENTION_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_RETENTION_DRY_RUN", "false")


def _intake_ready(monkeypatch):
    """접수 준비 완료 상태(1b): 소금 + apply + 워커 가동."""
    monkeypatch.setenv(support.SALT_ENV, TEST_SALT)
    _apply_mode(monkeypatch)
    monkeypatch.setattr(ret, "_worker_running", True)


async def _insert(created, status="received", changed=None, email="me@example.com",
                  dedupe=None, description="하트 수가 실제와 다릅니다 확인 부탁", url="https://ex.com/e"):
    c = await database.get_db()
    cur = await c.execute(
        """INSERT INTO correction_requests
               (created_at, category, clip_ref, description, desired_fix, evidence_url,
                contact_email, dedupe_key, status, status_changed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (created, "wrong_metric", "abcDEF123", description, "", url, email,
         dedupe if dedupe is not None else uuid.uuid4().hex, status,
         created if changed is None else changed))
    await c.commit()
    return cur.lastrowid


async def _row(rid):
    c = await database.get_db()
    r = await (await c.execute(
        "SELECT * FROM correction_requests WHERE id=?", (rid,))).fetchone()
    return dict(r) if r else None


async def _count_rows():
    c = await database.get_db()
    return (await (await c.execute("SELECT COUNT(*) FROM correction_requests")).fetchone())[0]


def _cleanup(adb, now=NOW):
    return adb(ret.run_cleanup(now=now))


# ── migration ────────────────────────────────────────────────────────────────

_LEGACY_TABLE = """CREATE TABLE correction_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL,
    category TEXT NOT NULL, clip_ref TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL, desired_fix TEXT NOT NULL DEFAULT '',
    evidence_url TEXT NOT NULL DEFAULT '', contact_email TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'received')"""
_OLD_INSERT = ("INSERT INTO correction_requests (created_at, category, clip_ref, description,"
               " desired_fix, evidence_url, contact_email, dedupe_key, status)"
               " VALUES (?, 'other', 'abc', '구 코드가 넣은 요청입니다',"
               " '', '', '', ?, 'received')")


class _Mig:
    def __init__(self):
        self.path = Path(tempfile.gettempdir()) / f"nexbot-supmig-{uuid.uuid4().hex}.db"
        self.loop = asyncio.new_event_loop()
        self.conn = self.run(aiosqlite.connect(self.path))

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def exec(self, sql, *args):
        self.run(self.conn.execute(sql, args))
        self.run(self.conn.commit())

    def cols(self):
        cur = self.run(self.conn.execute("PRAGMA table_info(correction_requests)"))
        return [r[1] for r in self.run(cur.fetchall())]

    def rows(self, sql):
        return [tuple(r) for r in self.run(self.run(self.conn.execute(sql)).fetchall())]

    def migrate(self, conn=None):
        return self.run(dbmod._migrate_correction_retention_schema(conn or self.conn))

    def close(self):
        self.run(self.conn.close())
        self.loop.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)


@pytest.fixture
def mig():
    m = _Mig()
    yield m
    m.close()


def test_fresh_DB에_두_컬럼이_생긴다(adb):
    async def _cols():
        c = await database.get_db()
        return [r[1] for r in await (await c.execute(
            "PRAGMA table_info(correction_requests)")).fetchall()]
    cols = adb(_cols())
    assert "status_changed_at" in cols and "contact_email_cleared_at" in cols


def test_init_db_반복_실행은_안전하다(adb):
    rid = adb(_insert(NOW - DAY, changed=NOW - 10))
    for _ in range(3):
        adb(database.init_db())
    row = adb(_row(rid))
    assert row["status_changed_at"] == NOW - 10, "이미 채워진 기준 시각을 덮어쓰지 않는다"


def test_legacy_DB는_컬럼을_더하고_backfill한다(mig):
    mig.exec(_LEGACY_TABLE)
    mig.exec(_OLD_INSERT, NOW - 5 * DAY, "k1")
    mig.migrate()
    assert mig.cols()[-2:] == ["status_changed_at", "contact_email_cleared_at"]
    assert mig.rows("SELECT created_at, status_changed_at, contact_email_cleared_at"
                    " FROM correction_requests") == [(NOW - 5 * DAY, NOW - 5 * DAY, 0)]
    mig.migrate()
    mig.migrate()
    assert mig.cols().count("status_changed_at") == 1


def test_부분_적용_상태에서_없는_컬럼만_채운다(mig):
    mig.exec(_LEGACY_TABLE)
    mig.exec("ALTER TABLE correction_requests"
             " ADD COLUMN status_changed_at INTEGER NOT NULL DEFAULT 0")
    mig.migrate()
    assert mig.cols().count("status_changed_at") == 1
    assert "contact_email_cleared_at" in mig.cols()


def test_중간_실패는_전파되고_재기동하면_끝난다(mig):
    mig.exec(_LEGACY_TABLE)
    mig.exec(_OLD_INSERT, NOW - DAY, "k1")

    class Flaky:
        """두 번째 ALTER에서 실패하는 연결(디스크·잠금 오류 흉내)."""
        def __init__(self, conn):
            self.conn = conn

        async def execute(self, sql, *a):
            if "ADD COLUMN contact_email_cleared_at" in sql:
                raise sqlite3.OperationalError("disk I/O error")
            return await self.conn.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    with pytest.raises(sqlite3.OperationalError):
        mig.migrate(Flaky(mig.conn))
    assert "contact_email_cleared_at" not in mig.cols()
    mig.migrate()          # 재기동
    assert mig.cols()[-2:] == ["status_changed_at", "contact_email_cleared_at"]
    assert mig.rows("SELECT status_changed_at FROM correction_requests") == [(NOW - DAY,)]


def test_테이블이_없으면_조용히_넘어가지_않는다(mig):
    with pytest.raises(sqlite3.OperationalError):
        mig.migrate()


def test_구_코드_INSERT와_호환되고_backfill_전에도_계산이_같다(adb, monkeypatch):
    async def _old_insert():
        c = await database.get_db()
        cur = await c.execute(_OLD_INSERT, (NOW - 366 * DAY, "old-key"))
        await c.commit()
        return cur.lastrowid
    rid = adb(_old_insert())
    assert adb(_row(rid))["status_changed_at"] == 0, "구 INSERT는 기본값 0을 받는다"
    _apply_mode(monkeypatch)
    rep = _cleanup(adb)
    assert rep["rowsDeleted"] == 1 and adb(_row(rid)) is None, "0은 created_at으로 떨어진다"


def test_새_접수는_status_changed_at이_created_at과_같다(adb, monkeypatch):
    _intake_ready(monkeypatch)
    res = adb(support.submit({"category": "other", "clipRef": "abcDEF123",
                              "description": "새 접수 기준 시각 확인용입니다"}, submitter="h"))
    row = adb(_row(res["id"]))
    assert row["status_changed_at"] == row["created_at"] > 0
    assert row["contact_email_cleared_at"] == 0


# ── 상태 전이 ────────────────────────────────────────────────────────────────

def test_실제로_바뀔_때만_시각이_옮겨진다(adb):
    rid = adb(_insert(NOW - 10 * DAY))
    assert adb(support.set_status(rid, "in_review", now=NOW - 9 * DAY))["changed"] is True
    assert adb(_row(rid))["status_changed_at"] == NOW - 9 * DAY
    adb(support.set_status(rid, "resolved", now=NOW - 8 * DAY))
    assert adb(_row(rid))["status_changed_at"] == NOW - 8 * DAY
    same = adb(support.set_status(rid, "resolved", now=NOW))
    assert same["changed"] is False
    assert adb(_row(rid))["status_changed_at"] == NOW - 8 * DAY, (
        "같은 상태 재저장은 시각을 바꾸지 않는다")


def test_재오픈은_시각을_재오픈_시점으로_옮기고_이메일을_되살리지_않는다(adb, monkeypatch):
    rid = adb(_insert(NOW - 100 * DAY, status="resolved", changed=NOW - 60 * DAY))
    _apply_mode(monkeypatch)
    _cleanup(adb, NOW)
    assert adb(_row(rid))["contact_email"] == ""
    adb(support.set_status(rid, "received", now=NOW + 10))
    row = adb(_row(rid))
    assert row["status"] == "received" and row["status_changed_at"] == NOW + 10
    assert row["contact_email"] == "" and row["contact_email_cleared_at"] == NOW


def test_재오픈해도_미처리_절대_상한은_접수_후_365일(adb, monkeypatch):
    rid = adb(_insert(NOW - 365 * DAY, status="received", changed=NOW - DAY))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    assert adb(_row(rid)) is None


def test_잘못된_상태와_없는_번호(adb):
    rid = adb(_insert(NOW - DAY))
    with pytest.raises(support.SupportError):
        adb(support.set_status(rid, "deleted"))
    with pytest.raises(support.SupportError):
        adb(support.set_status(999999, "resolved"))
    assert adb(_row(rid))["status_changed_at"] == NOW - DAY


def test_동시_상태_변경은_상태와_시각이_어긋나지_않는다(adb):
    rid = adb(_insert(NOW - DAY))

    async def _race():
        return await asyncio.gather(
            support.set_status(rid, "in_review", now=NOW - 30),
            support.set_status(rid, "resolved", now=NOW - 20),
            support.set_status(rid, "rejected", now=NOW - 10))
    adb(_race())
    row = adb(_row(rid))
    stamp = {"in_review": NOW - 30, "resolved": NOW - 20, "rejected": NOW - 10}
    assert row["status_changed_at"] == stamp[row["status"]]


# ── dedupe_key ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("age, cleared",
                         [(7 * DAY - 1, False), (7 * DAY, True), (7 * DAY + 1, True)])
def test_dedupe_key_7일_경계(adb, monkeypatch, age, cleared):
    rid = adb(_insert(NOW - age, dedupe="k-" + str(age)))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    assert (adb(_row(rid))["dedupe_key"] == "") is cleared


def test_이미_빈_dedupe_key는_write_0이고_인덱스와_충돌하지_않는다(adb, monkeypatch):
    a = adb(_insert(NOW - 8 * DAY, dedupe=""))
    b = adb(_insert(NOW - 8 * DAY, dedupe="x1"))
    c = adb(_insert(NOW - 9 * DAY, dedupe="x2"))
    _apply_mode(monkeypatch)
    assert _cleanup(adb)["duplicateChecksCleared"] == 2
    assert _cleanup(adb)["duplicateChecksCleared"] == 0
    assert all(adb(_row(r))["dedupe_key"] == "" for r in (a, b, c))


def test_정리된_뒤에도_같은_새_제보는_새_키로_막힌다(adb, monkeypatch):
    """비운 키('')는 부분 unique 인덱스 밖이라 여러 행이 동시에 ''여도 된다."""
    for _ in range(3):
        adb(_insert(NOW - 8 * DAY, dedupe="same-" + uuid.uuid4().hex))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    adb(_insert(NOW, dedupe="fresh"))
    with pytest.raises(sqlite3.IntegrityError):
        adb(_insert(NOW, dedupe="fresh"))


# ── contact_email ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["received", "in_review"])
@pytest.mark.parametrize("age, cleared",
                         [(180 * DAY - 1, False), (180 * DAY, True), (180 * DAY + 1, True)])
def test_미처리_이메일은_접수_후_180일(adb, monkeypatch, status, age, cleared):
    rid = adb(_insert(NOW - age, status=status))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    row = adb(_row(rid))
    assert (row["contact_email"] == "") is cleared
    assert row["contact_email_cleared_at"] == (NOW if cleared else 0)
    assert row["description"] and row["evidence_url"], "본문은 이 단계에서 지우지 않는다"


@pytest.mark.parametrize("status", ["resolved", "rejected"])
@pytest.mark.parametrize("since, cleared",
                         [(30 * DAY - 1, False), (30 * DAY, True), (30 * DAY + 1, True)])
def test_처리_후_30일에_이메일을_지운다(adb, monkeypatch, status, since, cleared):
    rid = adb(_insert(NOW - 40 * DAY, status=status, changed=NOW - since))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    assert (adb(_row(rid))["contact_email"] == "") is cleared


def test_두_기준_중_빠른_것이_이긴다(adb, monkeypatch):
    # 접수 179일, 처리 1일 전 → 처리 기준(30일)은 멀었지만 접수 기준(180일)도 1일 남음 → 보존
    keep = adb(_insert(NOW - 179 * DAY, status="resolved", changed=NOW - DAY))
    # 접수 180일, 방금 처리 → 접수 기준이 먼저 → 제거
    by_created = adb(_insert(NOW - 180 * DAY, status="resolved", changed=NOW - 1))
    # 접수 50일, 처리 30일 전 → 처리 기준이 먼저 → 제거
    by_closed = adb(_insert(NOW - 50 * DAY, status="rejected", changed=NOW - 30 * DAY))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    assert adb(_row(keep))["contact_email"] == "me@example.com"
    assert adb(_row(by_created))["contact_email"] == ""
    assert adb(_row(by_closed))["contact_email"] == ""


def test_이메일이_없던_행은_write_0이고_제거_시각을_남기지_않는다(adb, monkeypatch):
    rid = adb(_insert(NOW - 200 * DAY, email=""))
    _apply_mode(monkeypatch)
    assert _cleanup(adb)["emailCleared"] == 0
    assert adb(_row(rid))["contact_email_cleared_at"] == 0


# ── 행 삭제 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["received", "in_review", "unknown_status"])
@pytest.mark.parametrize("age, gone",
                         [(365 * DAY - 1, False), (365 * DAY, True), (365 * DAY + 1, True)])
def test_미처리_행은_접수_후_365일(adb, monkeypatch, status, age, gone):
    rid = adb(_insert(NOW - age, status=status))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    assert (adb(_row(rid)) is None) is gone


@pytest.mark.parametrize("status", ["resolved", "rejected"])
@pytest.mark.parametrize("since, gone",
                         [(180 * DAY - 1, False), (180 * DAY, True), (180 * DAY + 1, True)])
def test_처리_행은_상태_변경_후_180일(adb, monkeypatch, status, since, gone):
    rid = adb(_insert(NOW - 400 * DAY, status=status, changed=NOW - since))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    assert (adb(_row(rid)) is None) is gone


def test_늦게_처리된_요청은_처리_후_180일까지_남는다(adb, monkeypatch):
    """승인된 정책 그대로 — 접수 364일에 처리하면 처리 후 180일(총 544일)까지 남는다.
    별도 상한은 사용자 승인 없이 추가하지 않았다(보고서 참고)."""
    rid = adb(_insert(NOW - 364 * DAY, status="resolved", changed=NOW))
    _apply_mode(monkeypatch)
    _cleanup(adb, NOW + 180 * DAY - 1)
    assert adb(_row(rid)) is not None
    _cleanup(adb, NOW + 180 * DAY)
    assert adb(_row(rid)) is None


@pytest.mark.parametrize("created, changed", [
    (NOW + 100, None),                    # 미래 접수
    (NOW + 100, 0),                       # 미래 접수 + 구 행(기준 시각 0)
    (NOW - 400 * DAY, NOW + 100),         # 미래 상태 변경
    (-5, None),                           # 음수 접수
    (0, None),                            # 0 접수
    (NOW - 400 * DAY, -1),                # 음수 상태 변경
])
def test_잘못된_시각의_행은_건너뛴다(adb, monkeypatch, created, changed):
    rid = adb(_insert(created, status="resolved", changed=changed, dedupe="bad"))
    _apply_mode(monkeypatch)
    rep = _cleanup(adb)
    row = adb(_row(rid))
    assert row is not None and row["contact_email"] and row["dedupe_key"] == "bad"
    assert rep["invalidSkipped"] == 1
    assert ret.schedule(created, changed if changed is not None else created,
                        "resolved")["valid"] is (created > 0 and (changed is None or changed >= 0))


def test_반복_정리는_멱등이다(adb, monkeypatch):
    for age in (8, 31, 181, 366):
        adb(_insert(NOW - age * DAY, status="received"))
        adb(_insert(NOW - age * DAY, status="resolved"))
    _apply_mode(monkeypatch)
    first = _cleanup(adb)
    snapshot = adb(_dump())
    second = _cleanup(adb)
    assert first["rowsDeleted"] > 0
    assert (second["duplicateChecksCleared"], second["emailCleared"],
            second["rowsDeleted"]) == (0, 0, 0)
    assert adb(_dump()) == snapshot


async def _dump():
    c = await database.get_db()
    rows = await (await c.execute("SELECT * FROM correction_requests ORDER BY id")).fetchall()
    return [tuple(r) for r in rows]


def test_UPDATE와_DELETE_순서를_바꿔도_최종_상태가_같다(adb, monkeypatch):
    for age in (6, 7, 29, 30, 179, 180, 364, 365):
        for st in ("received", "resolved"):
            adb(_insert(NOW - age * DAY, status=st, changed=NOW - (age // 2) * DAY))
    expected_after = None

    async def _run(order):
        c = await database.get_db()
        p = ret._params(NOW)
        stmts = {
            "d": f"UPDATE correction_requests SET dedupe_key='' WHERE {ret.DEDUPE_DUE_SQL}",
            "e": ("UPDATE correction_requests SET contact_email='', contact_email_cleared_at=:now"
                  f" WHERE {ret.EMAIL_DUE_SQL}"),
            "x": f"DELETE FROM correction_requests WHERE {ret.ROW_DUE_SQL}"}
        for k in order:
            await c.execute(stmts[k], p)
        await c.commit()

    base = adb(_dump())
    for order in ("dex", "xed", "exd"):
        async def _restore():
            c = await database.get_db()
            await c.execute("DELETE FROM correction_requests")
            marks = ",".join("?" * len(base[0]))
            await c.executemany(f"INSERT INTO correction_requests VALUES ({marks})", base)
            await c.commit()
        adb(_restore())
        adb(_run(order))
        after = adb(_dump())
        expected_after = expected_after or after
        assert after == expected_after, order


def test_두_연결이_동시에_정리해도_결과가_같다(adb):
    """구·신 컨테이너가 겹친 상황 — 서로 다른 SQLite 연결 두 개가 같은 회차를 동시에 돈다."""
    ids = [adb(_insert(NOW - age * DAY, status=st))
           for age in (1, 8, 181, 366) for st in ("received", "resolved")]

    async def _race():
        a = await aiosqlite.connect(dbmod.DB_PATH)
        b = await aiosqlite.connect(dbmod.DB_PATH)
        for conn in (a, b):
            await conn.execute("PRAGMA busy_timeout=5000")
        try:
            return await asyncio.gather(ret.cleanup_once(a, now=NOW, dry_run=False),
                                        ret.cleanup_once(b, now=NOW, dry_run=False))
        finally:
            await a.close()
            await b.close()
    r1, r2 = adb(_race())
    remaining = {r[0] for r in adb(_dump())}
    # 366일 received·181일 resolved·366일 resolved가 지워진다.
    assert r1["rowsDeleted"] + r2["rowsDeleted"] == 3, "각 행은 정확히 한 번만 지워진다"
    assert len(remaining) == len(ids) - 3


def test_같은_프로세스에서는_겹쳐_돌지_않는다(adb, monkeypatch):
    adb(_insert(NOW - 400 * DAY))
    _apply_mode(monkeypatch)

    async def _both():
        return await asyncio.gather(ret.run_cleanup(now=NOW), ret.run_cleanup(now=NOW))
    a, b = adb(_both())
    assert {"skipped": "in_progress"} in (a, b)


# ── dry-run ──────────────────────────────────────────────────────────────────

def test_기본값은_dry_run이고_write가_0이다(adb, capsys):
    ids = [adb(_insert(NOW - age * DAY, status=st))
           for age in (8, 31, 181, 366) for st in ("received", "resolved")]
    before = adb(_dump())
    assert ret.mode() == "dry_run"
    rep = _cleanup(adb)
    assert rep["mode"] == "dry_run"
    assert adb(_dump()) == before, "dry-run은 UPDATE·DELETE 0"
    # 같은 데이터로 실제 정리를 돌리면 건수가 같아야 한다(같은 SQL 조건을 쓴다).
    async def _apply_copy():
        return await ret.cleanup_once(await database.get_db(), now=NOW, dry_run=False)
    applied = adb(_apply_copy())
    for k in ("duplicateChecksCleared", "emailCleared", "rowsDeleted"):
        assert rep[k] == applied[k], k
    assert len(ids) == 8


@pytest.mark.parametrize("enabled, dry, expected", [
    (None, None, "dry_run"), ("true", None, "dry_run"), (None, "false", "dry_run"),
    ("false", "false", "dry_run"), ("true", "true", "dry_run"), ("true", "false", "apply"),
])
def test_실제_정리는_이중_관문이다(monkeypatch, enabled, dry, expected):
    for name, val in (("SUPPORT_RETENTION_ENABLED", enabled), ("SUPPORT_RETENTION_DRY_RUN", dry)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    assert ret.mode() == expected


@pytest.mark.parametrize("apply", [False, True])
def test_로그에_본문_이메일_URL이_없다(adb, monkeypatch, capsys, apply):
    adb(_insert(NOW - 400 * DAY, email="secret.person@example.com", dedupe="hash-abc-123",
                description="민감한 개인 설명 문장입니다", url="https://private.example.com/p"))
    if apply:
        _apply_mode(monkeypatch)
    _cleanup(adb)
    out = capsys.readouterr().out
    assert "[support_retention]" in out
    for bad in ("secret.person", "example.com", "민감한", "hash-abc-123", "private"):
        assert bad not in out, bad


def test_실패_로그에는_예외_종류만_남는다(adb, monkeypatch, capsys):
    async def boom(*a, **k):
        raise sqlite3.OperationalError("SELECT contact_email FROM correction_requests me@x.com")
    monkeypatch.setattr(ret, "cleanup_once", boom)
    with pytest.raises(sqlite3.OperationalError):
        _cleanup(adb)
    out = capsys.readouterr().out
    assert "OperationalError" in out and "me@x.com" not in out and "SELECT" not in out
    assert ret.last_report() == {"ok": False, "at": NOW, "error": "OperationalError"}


# ── Python 예정일 ↔ SQL 정리 패리티 ──────────────────────────────────────────

def test_화면_예정일과_실제_정리가_같은_계산이다(adb, monkeypatch):
    offsets = [0, 1, 6 * DAY, 7 * DAY, 29 * DAY, 30 * DAY, 179 * DAY, 180 * DAY,
               181 * DAY, 364 * DAY, 365 * DAY, 400 * DAY]
    rows = []
    for st in ("received", "in_review", "resolved", "rejected"):
        for age in offsets:
            for since in (0, 1, 30 * DAY, 180 * DAY):
                created = NOW - age
                changed = max(created, NOW - since)
                rid = adb(_insert(created, status=st, changed=changed))
                rows.append((rid, created, changed, st))
    _apply_mode(monkeypatch)
    _cleanup(adb)
    for rid, created, changed, st in rows:
        s = ret.schedule(created, changed, st)
        row = adb(_row(rid))
        assert (row is None) is (NOW >= s["deletionAt"]), (created, changed, st)
        if row is not None:
            assert (row["contact_email"] == "") is (NOW >= s["emailRemovalAt"]), (
                created, changed, st)
            assert (row["dedupe_key"] == "") is (NOW >= s["duplicateCheckClearAt"])


def test_공개_문구가_쓰는_숫자와_정본이_같다():
    lib = Path(__file__).resolve().parents[1] / "web/frontend/lib/supportRetention.ts"
    ts = lib.read_text("utf-8")
    p = ret.policy()
    for key, val in (("ABSOLUTE_MAX_DAYS", p["absoluteMaxDays"]),
                     ("DUPLICATE_CHECK_CLEAR_DAYS", p["duplicateCheckClearDays"]),
                     ("EMAIL_MAX_DAYS_AFTER_CREATED", p["emailMaxDaysAfterCreated"]),
                     ("EMAIL_DAYS_AFTER_CLOSED", p["emailDaysAfterClosed"]),
                     ("OPEN_MAX_DAYS", p["openMaxDays"]),
                     ("CLOSED_DAYS", p["closedDays"])):
        assert f"export const {key} = {val};" in ts, key
    assert p == {"duplicateCheckClearDays": 7, "emailMaxDaysAfterCreated": 180,
                 "emailDaysAfterClosed": 30, "openMaxDays": 365, "closedDays": 180,
                 "absoluteMaxDays": 545}
    assert p["absoluteMaxDays"] == p["openMaxDays"] + p["closedDays"], "새 기간이 아니라 최대 조합"


# ── 워커 lifecycle ───────────────────────────────────────────────────────────

def test_워커는_기동_후_한_번_이후_하루마다_돈다(adb, monkeypatch):
    sleeps, calls = [], []

    async def fake_sleep(sec):
        sleeps.append(sec)

    async def fake_run(*, now=None):
        calls.append(now)
        return {}
    monkeypatch.setattr(ret, "run_cleanup", fake_run)
    clock = iter([NOW, NOW + DAY, NOW + 2 * DAY])
    adb(ret.start_support_retention_worker(clock=lambda: next(clock), sleep=fake_sleep, max_runs=3))
    assert calls == [NOW, NOW + DAY, NOW + 2 * DAY]
    assert sleeps == [ret.START_DELAY_SECONDS, ret.INTERVAL_SECONDS, ret.INTERVAL_SECONDS]
    assert ret.INTERVAL_SECONDS == 86400


def test_실패해도_곧바로_재시도하지_않고_다음_주기를_기다린다(adb, monkeypatch):
    sleeps, calls = [], []

    async def fake_sleep(sec):
        sleeps.append(sec)

    async def failing(*, now=None):
        calls.append(now)
        raise RuntimeError("boom")
    monkeypatch.setattr(ret, "run_cleanup", failing)
    adb(ret.start_support_retention_worker(clock=lambda: NOW, sleep=fake_sleep, max_runs=2))
    assert len(calls) == 2
    assert sleeps == [ret.START_DELAY_SECONDS, ret.INTERVAL_SECONDS], "실패 사이에 하루를 기다린다"


def test_워커는_취소되면_깔끔하게_끝난다(adb):
    async def _cancel():
        t = asyncio.create_task(ret.start_support_retention_worker(start_delay=3600))
        await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        return t.cancelled()
    assert adb(_cancel()) is True


def test_lifespan이_워커를_띄우고_종료_시_취소한다():
    src = (Path(__file__).resolve().parents[1] / "web/backend/main.py").read_text("utf-8")
    assert "support_retention.launch_worker()" in src
    assert "support_retention_task.cancel()" in src
    assert src.index("support_retention_task.cancel()") < src.index("await close_db()")


# ── OWNER 목록·수동 삭제 ─────────────────────────────────────────────────────

@pytest.fixture
def client(adb, monkeypatch):
    _intake_ready(monkeypatch)
    import routers.account_router as acr
    import routers.admin_router as ar
    from deps import get_current_user
    monkeypatch.setattr(ar, "_OWNER_ID", OWNER)
    a = FastAPI()
    a.include_router(acr.support_router)
    a.include_router(ar.router)
    a.state._dep = get_current_user
    return TestClient(a)


def _as(client, sub=OWNER):
    client.app.dependency_overrides[client.app.state._dep] = lambda: {"sub": sub}
    return client


def _submit(client, **over):
    body = {"category": "wrong_metric", "clipRef": "abcDEF123",
            "description": "하트 수가 실제와 다릅니다. 확인 부탁드립니다.",
            "email": "reply.me@example.com", "evidenceUrl": "https://ex.com/proof"}
    body.update(over)
    support.reset_state()
    r = client.post("/api/support/correction", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_목록에_예정일과_정리_모드가_나온다(client):
    rid = _submit(client)
    item = _as(client).get("/api/admin/support/corrections").json()
    row = item["items"][0]
    assert row["id"] == rid
    assert row["statusChangedAt"] == row["createdAt"]
    assert row["emailRemovalDueAt"] == row["createdAt"] + 180 * DAY
    assert row["deletionDueAt"] == row["createdAt"] + 365 * DAY
    assert row["emailClearedAt"] is None
    assert row["deletionCapped"] is False
    r = item["retention"]
    assert (r["mode"], r["enabled"], r["dryRun"], r["workerRunning"], r["intakeReady"]) == (
        "apply", True, False, True, True)
    assert r["saltConfigured"] is True and r["lastRunAt"] is None
    assert r["policy"]["closedDays"] == 180 and r["policy"]["absoluteMaxDays"] == 545
    assert set(r["candidates"]) == {"candidateDuplicateCheckClearCount", "candidateEmailClearCount",
                                    "candidateDeleteCount", "invalidTimestampCount"}
    assert "dedupe" not in json.dumps(item) and TEST_SALT not in json.dumps(item)


def test_처리_후_예정일은_변경_시각_기준(client, adb):
    rid = _submit(client)
    adb(support.set_status(rid, "resolved", now=NOW))
    row = _as(client).get("/api/admin/support/corrections").json()["items"][0]
    assert row["statusChangedAt"] == NOW
    assert row["emailRemovalDueAt"] == min(row["createdAt"] + 180 * DAY, NOW + 30 * DAY)
    assert row["deletionDueAt"] == NOW + 180 * DAY


def test_이미_지운_이메일은_제거됨으로_표시된다(client, adb, monkeypatch):
    rid = adb(_insert(NOW - 200 * DAY))
    _apply_mode(monkeypatch)
    adb(ret.run_cleanup(now=NOW))
    row = [i for i in _as(client).get("/api/admin/support/corrections").json()["items"]
           if i["id"] == rid][0]
    assert row["contactEmail"] == "" and row["emailClearedAt"] == NOW
    assert row["emailRemovalDueAt"] is None


def test_OWNER가_삭제하면_사라지고_내용을_돌려주지_않는다(client):
    rid = _submit(client)
    keep = _submit(client, clipRef="other1")
    r = _as(client).delete(f"/api/admin/support/corrections/{rid}")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "id": rid, "deleted": True}
    for bad in ("reply.me", "example.com", "하트", "abcDEF123"):
        assert bad not in r.text
    body = client.get("/api/admin/support/corrections").json()
    assert [i["id"] for i in body["items"]] == [keep]
    assert body["counts"]["received"] == 1


def test_같은_번호를_다시_지우면_404(client):
    rid = _submit(client)
    _as(client)
    assert client.delete(f"/api/admin/support/corrections/{rid}").status_code == 200
    again = client.delete(f"/api/admin/support/corrections/{rid}")
    assert again.status_code == 404 and isinstance(again.json()["detail"], str)
    assert client.delete("/api/admin/support/corrections/999999").status_code == 404


def test_인증_없는_삭제는_거절된다(client):
    rid = _submit(client)
    client.app.dependency_overrides.clear()
    r = client.delete(f"/api/admin/support/corrections/{rid}")
    assert r.status_code in (401, 403)
    assert _as(client).get("/api/admin/support/corrections").json()["items"][0]["id"] == rid


def test_OWNER가_아니면_403이고_지우지_않는다(client):
    rid = _submit(client)
    r = _as(client, sub="222222222222222222").delete(f"/api/admin/support/corrections/{rid}")
    assert r.status_code == 403
    assert _as(client).get("/api/admin/support/corrections").json()["items"][0]["id"] == rid


def test_공개_삭제_경로는_없다(client):
    rid = _submit(client)
    client.app.dependency_overrides.clear()
    for path in (f"/api/support/correction/{rid}", "/api/support/correction"):
        assert client.delete(path).status_code in (404, 405)


def test_삭제_라우트는_OWNER_의존성을_쓴다():
    import inspect

    import routers.admin_router as ar
    sig = inspect.signature(ar.support_correction_delete)
    assert sig.parameters["user"].default.dependency is ar._require_owner


# ── SUPPORT-POLICY-1b: 접수 후 545일 절대 상한 ─────────────────────────────

C = NOW - 700 * DAY          # 절대 상한 테스트용 접수 시각(모든 조작 시각이 NOW 이전이 되게)


def _gone_at(adb, rid, when):
    _cleanup(adb, when)
    return adb(_row(rid)) is None


def test_접수_364일에_처리하면_기본_만료는_544일(adb, monkeypatch):
    rid = adb(_insert(C, status="resolved", changed=C + 364 * DAY))
    s = ret.schedule(C, C + 364 * DAY, "resolved")
    assert s["deletionAt"] == C + 544 * DAY and s["deletionCapped"] is False
    _apply_mode(monkeypatch)
    assert not _gone_at(adb, rid, C + 544 * DAY - 1)
    assert _gone_at(adb, rid, C + 544 * DAY)


@pytest.mark.parametrize("offset, gone", [(-1, False), (0, True), (1, True)])
def test_늦게_처리된_행은_접수_후_545일_절대_상한(adb, monkeypatch, offset, gone):
    rid = adb(_insert(C, status="resolved", changed=C + 500 * DAY))
    s = ret.schedule(C, C + 500 * DAY, "resolved")
    assert s["deletionAt"] == C + 545 * DAY and s["deletionCapped"] is True
    _apply_mode(monkeypatch)
    assert _gone_at(adb, rid, C + 545 * DAY + offset) is gone


def test_resolved와_rejected를_반복해도_545일을_넘지_못한다(adb, monkeypatch):
    rid = adb(_insert(C, status="received"))
    for i, day in enumerate(range(100, 541, 40)):
        adb(support.set_status(rid, "resolved" if i % 2 == 0 else "rejected", now=C + day * DAY))
    row = adb(_row(rid))
    assert row["status_changed_at"] >= C + 500 * DAY, "반복 변경마다 기준 시각은 옮겨진다"
    assert ret.schedule(C, row["status_changed_at"], row["status"])["deletionAt"] == C + 545 * DAY
    _apply_mode(monkeypatch)
    assert not _gone_at(adb, rid, C + 545 * DAY - 1)
    assert _gone_at(adb, rid, C + 545 * DAY)


def test_재오픈과_재종료를_반복해도_545일을_넘지_못한다(adb, monkeypatch):
    rid = adb(_insert(C, status="received"))
    for status, day in (("resolved", 100), ("received", 200), ("resolved", 300),
                        ("in_review", 340), ("rejected", 360), ("received", 362),
                        ("resolved", 364), ("rejected", 520)):
        adb(support.set_status(rid, status, now=C + day * DAY))
    _apply_mode(monkeypatch)
    assert not _gone_at(adb, rid, C + 545 * DAY - 1)
    assert _gone_at(adb, rid, C + 545 * DAY)


def test_재오픈된_미처리_행은_여전히_접수_후_365일(adb, monkeypatch):
    rid = adb(_insert(C, status="resolved", changed=C + 100 * DAY))
    adb(support.set_status(rid, "received", now=C + 300 * DAY))
    _apply_mode(monkeypatch)
    assert not _gone_at(adb, rid, C + 365 * DAY - 1)
    assert _gone_at(adb, rid, C + 365 * DAY)


def test_상태_변경_시각이_접수보다_이르면_접수_시각이_기준(adb, monkeypatch):
    rid = adb(_insert(C, status="resolved", changed=C - 100 * DAY))
    s = ret.schedule(C, C - 100 * DAY, "resolved")
    assert s["deletionAt"] == C + 180 * DAY
    _apply_mode(monkeypatch)
    assert not _gone_at(adb, rid, C + 180 * DAY - 1)
    assert _gone_at(adb, rid, C + 180 * DAY)


def test_상태_변경_시각이_미래면_545일이_지나도_건드리지_않는다(adb, monkeypatch):
    """기존 fail-safe 유지 — 시계가 어긋난 행은 지우지 않고 invalid로만 센다(운영자 확인 대상)."""
    rid = adb(_insert(C, status="resolved", changed=NOW + 10 * DAY, dedupe="future"))
    _apply_mode(monkeypatch)
    rep = _cleanup(adb, NOW)
    row = adb(_row(rid))
    assert row is not None and row["contact_email"] and row["dedupe_key"] == "future"
    assert rep["invalidSkipped"] == 1


def test_이메일과_dedupe의_더_짧은_상한은_그대로다(adb, monkeypatch):
    rid = adb(_insert(C, status="resolved", changed=C + 500 * DAY, dedupe="k545"))
    s = ret.schedule(C, C + 500 * DAY, "resolved")
    assert s["emailRemovalAt"] == C + 180 * DAY and s["duplicateCheckClearAt"] == C + 7 * DAY
    _apply_mode(monkeypatch)
    # 처리 시각 이후 — 이메일·dedupe는 이미 기한, 행은 545일까지 남는다.
    _cleanup(adb, C + 500 * DAY)
    row = adb(_row(rid))
    assert row is not None and row["contact_email"] == "" and row["dedupe_key"] == ""


def test_절대_상한_격자에서_화면_예정일과_실제_정리가_같다(adb, monkeypatch):
    rows = []
    for st in ("received", "in_review", "resolved", "rejected", "odd_status"):
        for change_day in (0, 1, 180, 300, 364, 365, 400, 500, 540, 544, 545, 546):
            changed = C + change_day * DAY
            if changed > NOW:
                continue
            rows.append((adb(_insert(C, status=st, changed=changed)), changed, st))
    probes = [C + d * DAY + o for d in (180, 364, 365, 544, 545, 546, 700) for o in (-1, 0, 1)]
    for when in probes:
        async def _snapshot():
            return await ret.candidates(await database.get_db(), now=when)
        cand = adb(_snapshot())
        expected_delete = sum(
            1 for _, changed, st in rows
            if adb(_row(_)) is not None and changed <= when
            and when >= ret.schedule(C, changed, st)["deletionAt"])
        assert cand["candidateDeleteCount"] == expected_delete, when
    _apply_mode(monkeypatch)
    when = C + 545 * DAY - 1
    _cleanup(adb, when)
    for rid, changed, st in rows:
        due = ret.schedule(C, changed, st)["deletionAt"]
        # 상태 변경 시각이 정리 시각보다 뒤인 행은 fail-safe로 건너뛴다(기존 계약).
        assert (adb(_row(rid)) is None) is (changed <= when and when >= due), (changed, st)
    _cleanup(adb, C + 545 * DAY)
    left = [(changed, st) for rid, changed, st in rows if adb(_row(rid)) is not None]
    assert all(changed > C + 545 * DAY for changed, _ in left), (
        "접수 후 545일에는 시각이 정상인 어떤 상태의 행도 남지 않는다", left)


def test_dry_run_후보와_실제_처리_건수가_같다(adb, monkeypatch):
    for st in ("received", "resolved", "rejected"):
        for change_day in (0, 100, 364, 500):
            adb(_insert(C, status=st, changed=C + change_day * DAY))
    when = C + 545 * DAY - 1

    async def _cand():
        return await ret.candidates(await database.get_db(), now=when)
    cand = adb(_cand())
    dry = _cleanup(adb, when)
    assert dry["mode"] == "dry_run"
    _apply_mode(monkeypatch)
    applied = _cleanup(adb, when)
    assert (cand["candidateDuplicateCheckClearCount"], cand["candidateEmailClearCount"],
            cand["candidateDeleteCount"]) == (dry["duplicateChecksCleared"], dry["emailCleared"],
                                             dry["rowsDeleted"]) == (
        applied["duplicateChecksCleared"], applied["emailCleared"], applied["rowsDeleted"])


async def _total_changes():
    c = await database.get_db()
    return (await (await c.execute("SELECT total_changes()")).fetchone())[0]


def test_dry_run과_후보_계산과_목록_조회는_DB_write가_0이다(adb, monkeypatch):
    for age in (8, 31, 181, 366, 600):
        adb(_insert(NOW - age * DAY, status="resolved", changed=NOW - (age // 2) * DAY))
        adb(_insert(NOW - age * DAY, status="received"))
    before_rows = adb(_dump())
    before = adb(_total_changes())
    monkeypatch.setenv("SUPPORT_RETENTION_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_RETENTION_DRY_RUN", "true")
    rep = _cleanup(adb)
    assert rep["mode"] == "dry_run" and rep["rowsDeleted"] > 0
    adb(support.list_requests())
    assert adb(_total_changes()) == before, "dry-run·후보 계산·목록 조회가 DB에 썼다"
    assert adb(_dump()) == before_rows
    assert ret.last_report()["mode"] == "dry_run", "마지막 실행 기록은 프로세스 메모리에만"


# ── SUPPORT-POLICY-1b: 접수 준비 관문 ──────────────────────────────────────

def _meta_and_post(client):
    client.app.dependency_overrides.clear()
    support.reset_state()
    meta = client.get("/api/support/correction/meta").json()["accepting"]
    r = client.post("/api/support/correction", json={
        "category": "wrong_metric", "clipRef": f"gate{uuid.uuid4().hex[:8]}",
        "description": "접수 준비 관문 확인용 요청입니다."})
    return meta, r.status_code


@pytest.mark.parametrize("salt, enabled, dry, worker, accepting", [
    (False, None, None, False, False),        # 기본 설정
    (True, None, None, True, False),          # 소금만 설정 + 정리 기본(dry-run)
    (True, "false", "false", True, False),    # retention 비활성 + 소금
    (True, "true", "true", True, False),      # enabled + dry-run
    (False, "true", "false", True, False),    # 정리 가동 + 소금 없음
    (True, "true", "false", False, False),    # 정리 설정됐지만 워커 미가동
    (True, "true", "false", True, True),      # 전부 준비
])
def test_접수는_소금과_정리_가동이_모두_준비돼야_열린다(client, adb, monkeypatch,
                                                  salt, enabled, dry, worker, accepting):
    for name, val in ((support.SALT_ENV, TEST_SALT if salt else None),
                      ("SUPPORT_RETENTION_ENABLED", enabled),
                      ("SUPPORT_RETENTION_DRY_RUN", dry)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    monkeypatch.setattr(ret, "_worker_running", worker)
    before = adb(_count_rows())
    meta, code = _meta_and_post(client)
    assert meta is accepting
    assert code == (200 if accepting else 503)
    assert adb(_count_rows()) == before + (1 if accepting else 0)


def test_막힌_사유를_공개_응답에_드러내지_않는다(client, monkeypatch):
    monkeypatch.setenv("SUPPORT_RETENTION_DRY_RUN", "true")
    client.app.dependency_overrides.clear()
    r = client.post("/api/support/correction", json={
        "category": "wrong_metric", "clipRef": "abcDEF123",
        "description": "사유 노출 확인용 요청입니다."})
    assert r.status_code == 503
    for bad in ("RETENTION", "retention", "dry", "worker", "salt", "SALT"):
        assert bad not in r.text


def test_정리_한_회차_실패는_접수를_닫지_않는다(client, adb, monkeypatch):
    async def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(ret, "cleanup_once", boom)
    with pytest.raises(sqlite3.OperationalError):
        _cleanup(adb)
    st = ret.status()
    assert st["consecutiveFailures"] == 1 and st["lastRun"]["ok"] is False
    assert st["intakeReady"] is True
    meta, code = _meta_and_post(client)
    assert meta is True and code == 200
    monkeypatch.undo()


def test_성공하면_연속_실패가_초기화된다(adb, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("x")
    original = ret.cleanup_once
    monkeypatch.setattr(ret, "cleanup_once", boom)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            _cleanup(adb)
    assert ret.status()["consecutiveFailures"] == 2
    monkeypatch.setattr(ret, "cleanup_once", original)
    _cleanup(adb)
    assert ret.status()["consecutiveFailures"] == 0 and ret.status()["lastRunAt"] == NOW


def test_워커가_멈추면_접수가_닫힌다(adb, monkeypatch):
    _intake_ready(monkeypatch)
    monkeypatch.setattr(ret, "_worker_running", False)

    async def _life():
        t = ret.launch_worker(start_delay=3600)
        ready_before_first_tick = ret.intake_ready()
        await asyncio.sleep(0)
        running = ret.worker_running()
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        return ready_before_first_tick, running, ret.intake_ready(), support.accepting()
    ready0, running, ready_after, accepting_after = adb(_life())
    assert ready0 is True, "task가 첫 실행되기 전 요청에도 준비 상태가 흔들리지 않는다"
    assert running is True
    assert ready_after is False and accepting_after is False


def test_상태_응답에_민감_정보가_없다(client, adb):
    adb(_insert(NOW - 400 * DAY, email="secret.person@example.com", dedupe="hash-abc-123",
                description="민감한 개인 설명 문장입니다", url="https://private.example.com/p"))
    adb(ret.run_cleanup(now=NOW))
    body = _as(client).get("/api/admin/support/corrections").json()
    blob = json.dumps(body["retention"], ensure_ascii=False)
    for bad in ("secret.person", "example.com", "민감한", "hash-abc-123", "private", TEST_SALT):
        assert bad not in blob, bad
    assert body["retention"]["lastRunAt"] == NOW
