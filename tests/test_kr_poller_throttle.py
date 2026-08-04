"""AWS 서울 poller — 자체 스로틀의 DB 잠금 안전성.

**왜 별도 구현인가.** `singcup_clips.acquire_named_lock()`은 자기 docstring이
밝히듯 "`db_write`를 지나지 않고 공유 연결에 직접 커밋"하고 `database is locked`를
**예외로 그대로 올린다**(실측 2026-08-01, 4분 루프가 회차 시작과 동시에 죽은
지점). 핫 패스(`acquire_clip_lock`)의 P0 순서 계약 때문에 그 함수 자체는 고칠 수
없다. 그대로 재사용하면 HMAC 인증까지 통과한 poller 요청이 DB가 잠긴 순간 500이
되고, 과거에 수집을 멈췄던 잠금 경로를 신규 API에 다시 연결하게 된다.

그래서 poller 전용으로 `db_write_isolated`(전용 연결 · 짧은 busy_timeout ·
제한된 attempts · 절대 deadline)를 쓴다. 여기서 고정하는 계약:

  ① 세 상태를 **구분**한다 — acquired / already_held / database_busy_giveup.
     DB busy를 호출 빈도 문제로도, 인증 실패로도 위장하지 않는다.
  ② DB가 잠겨도 **500이 아니라 503**이며 열린 트랜잭션이 남지 않는다.
  ③ 잠금이 아닌 예외는 조용히 429/503으로 바꾸지 않는다.
  ④ 기존 `acquire_named_lock`과 그 호출부는 **손대지 않는다**.
"""
import asyncio
import hashlib
import hmac
import inspect
import json
import time

import aiosqlite
import pytest
import singcup_clips as sc
import singcup_kr_poller as krp
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database

SECRET = "test-dummy-secret-not-a-real-value"
TASKS = "/api/internal/singcup/kr-poller/tasks"
RESULTS = "/api/internal/singcup/kr-poller/results"


@pytest.fixture
def client(db, monkeypatch):
    import routers.kr_poller_router as kr

    monkeypatch.setenv("SINGCUP_KR_POLLER_SECRET", SECRET)
    monkeypatch.setenv("SINGCUP_KRP_ENABLED", "true")
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    app = FastAPI()
    app.include_router(kr.router)
    return TestClient(app, raise_server_exceptions=False)


def _headers(body, path=TASKS):
    ts = str(int(time.time()))
    nonce = hashlib.sha256(f"{time.time()}{path}".encode()).hexdigest()[:32]
    msg = f"{ts}\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}"
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"X-KRP-Timestamp": ts, "X-KRP-Nonce": nonce, "X-KRP-Signature": sig,
            "Content-Type": "application/json"}


def _post(client, path=TASKS, payload=None):
    body = json.dumps(payload if payload is not None else {"limit": 5}).encode()
    return client.post(path, content=body, headers=_headers(body, path))


# ── 실제 write lock을 잡는 별도 연결 ───────────────────────────────────────
async def _hold_write_lock():
    """다른 연결이 write 트랜잭션을 붙잡고 있는 상태를 실제로 만든다."""
    conn = await aiosqlite.connect(database.DB_PATH)
    await conn.execute("PRAGMA busy_timeout=0")
    await conn.execute("BEGIN IMMEDIATE")
    await conn.execute(
        "INSERT OR IGNORE INTO singcup_locks (name, locked_until, owner) "
        "VALUES ('blocker',0,'')")
    return conn


async def _release(conn):
    try:
        await conn.rollback()
    finally:
        await conn.close()


# ── ① 세 상태 판정 ─────────────────────────────────────────────────────────
def test_first_request_is_acquired(db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    assert db(krp.throttle_acquire("tasks")) == krp.ACQUIRED


def test_second_request_is_already_held(db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    assert db(krp.throttle_acquire("tasks")) == krp.ACQUIRED
    assert db(krp.throttle_acquire("tasks")) == krp.ALREADY_HELD


def test_window_reopens_after_ttl(db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    assert db(krp.throttle_acquire("tasks")) == krp.ACQUIRED
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 31)
    assert db(krp.throttle_acquire("tasks")) == krp.ACQUIRED


def test_buckets_are_independent(db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    assert db(krp.throttle_acquire("tasks")) == krp.ACQUIRED
    assert db(krp.throttle_acquire("results")) == krp.ACQUIRED


def test_zero_interval_disables_the_throttle(db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 0)
    assert db(krp.throttle_acquire("tasks")) == krp.ACQUIRED
    assert db(krp.throttle_acquire("tasks")) == krp.ACQUIRED


# ── ② 실제 DB 잠금 ─────────────────────────────────────────────────────────
def test_locked_database_gives_up_instead_of_raising(db, monkeypatch):
    """다른 연결이 write lock을 쥔 상태 — 예외가 아니라 판정으로 돌아와야 한다."""
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)

    async def scenario():
        blocker = await _hold_write_lock()
        try:
            return await krp.throttle_acquire("tasks")
        finally:
            await _release(blocker)

    assert db(scenario()) == krp.DB_BUSY


def test_db_busy_is_not_reported_as_already_held(db, monkeypatch):
    """둘은 전혀 다른 사건이다 — 뭉개면 잠금 문제가 호출 빈도 문제로 보인다."""
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)

    async def scenario():
        blocker = await _hold_write_lock()
        try:
            return await krp.throttle_acquire("never-used-bucket")
        finally:
            await _release(blocker)

    verdict = db(scenario())
    assert verdict == krp.DB_BUSY
    assert verdict != krp.ALREADY_HELD


def test_recovers_after_the_lock_is_released(db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)

    async def scenario():
        blocker = await _hold_write_lock()
        busy = await krp.throttle_acquire("tasks")
        await _release(blocker)
        after = await krp.throttle_acquire("tasks")
        return busy, after

    busy, after = db(scenario())
    assert busy == krp.DB_BUSY
    assert after == krp.ACQUIRED


def test_no_transaction_is_left_open_after_db_busy(db, monkeypatch):
    """열린 트랜잭션이 남으면 그 다음 쓰기가 통째로 막힌다."""
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)

    async def scenario():
        blocker = await _hold_write_lock()
        await krp.throttle_acquire("tasks")
        await _release(blocker)
        # 공유 연결로 평범한 쓰기가 즉시 되어야 한다
        conn = await database.get_db()
        await conn.execute(
            "INSERT OR IGNORE INTO singcup_locks (name, locked_until, owner) "
            "VALUES ('after',0,'')")
        await conn.commit()
        row = await (await conn.execute(
            "SELECT COUNT(*) n FROM singcup_locks WHERE name='after'")).fetchone()
        return row["n"]

    assert db(scenario()) == 1


def test_db_busy_respects_the_budget(db, monkeypatch):
    """무한정 기다리지 않는다 — 예산 + 정리 여유 안에서 끝난다."""
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)

    async def scenario():
        blocker = await _hold_write_lock()
        t0 = time.monotonic()
        try:
            await krp.throttle_acquire("tasks")
        finally:
            await _release(blocker)
        return time.monotonic() - t0

    elapsed = db(scenario())
    assert elapsed < (krp.THROTTLE_BUDGET_MS / 1000.0) + 1.5


# ── ③ 잠금이 아닌 예외·취소는 위장하지 않는다 ─────────────────────────────
def test_non_lock_exception_is_not_disguised(db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)

    async def _boom(*a, **kw):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(krp, "db_write_isolated", _boom)
    with pytest.raises(RuntimeError):
        db(krp.throttle_acquire("tasks"))


def test_cancellation_propagates(db, monkeypatch):
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)

    async def _cancel(*a, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(krp, "db_write_isolated", _cancel)
    with pytest.raises(asyncio.CancelledError):
        db(krp.throttle_acquire("tasks"))


# ── ④ HTTP 응답 계약 ───────────────────────────────────────────────────────
def test_http_429_on_already_held(client, db):
    assert _post(client).status_code == 200
    r = _post(client)
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "30"


def test_http_503_on_db_busy(client, db, monkeypatch):
    monkeypatch.setattr(krp, "throttle_acquire",
                        _const(krp.DB_BUSY))
    r = _post(client)
    assert r.status_code == 503
    assert r.headers.get("Retry-After") == str(krp.BUSY_RETRY_AFTER)


def test_db_busy_response_leaks_nothing(client, db, monkeypatch):
    monkeypatch.setattr(krp, "throttle_acquire", _const(krp.DB_BUSY))
    r = _post(client)
    for leaked in ("tasks", "clipUid", "leaseToken", "taskId", "nonce",
                   "Signature", "sqlite", ".db", "Traceback"):
        assert leaked not in r.text


def test_real_lock_produces_503_not_500(client, db):
    """인증까지 통과한 요청이 DB 잠금으로 500이 되면 안 된다."""
    async def scenario():
        return await _hold_write_lock()

    blocker = db(scenario())
    try:
        r = _post(client)
        assert r.status_code == 503
        assert "Traceback" not in r.text
    finally:
        db(_release(blocker))


def test_non_lock_exception_surfaces_as_server_error(client, db, monkeypatch):
    """조용히 429/503으로 바꾸지 않는다."""
    async def _boom(*a, **kw):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(krp, "throttle_acquire", _boom)
    r = _post(client)
    assert r.status_code == 500
    assert "disk gone" not in r.text          # 민감정보·내부 메시지 미노출


# ── ⑤ 기존 함수·호출부 무변경 증명 ─────────────────────────────────────────
_ACQUIRE_NAMED_LOCK_BODY = """    now = int(time.time())
    token = uuid.uuid4().hex[:12]
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO singcup_locks (name, locked_until, owner) VALUES (?,0,'')",
        (name,))
    cur = await db.execute(
        "UPDATE singcup_locks SET locked_until=?, owner=? WHERE name=? AND locked_until < ?",
        (now + ttl, token, name, now))
    await db.commit()
    return token if cur.rowcount == 1 else None
"""


def test_acquire_named_lock_source_is_unchanged():
    """이 함수는 핫 패스의 P0 순서 계약에 묶여 있어 손대면 안 된다."""
    src = inspect.getsource(sc.acquire_named_lock)
    assert src.endswith(_ACQUIRE_NAMED_LOCK_BODY)
    sig = inspect.signature(sc.acquire_named_lock)
    assert list(sig.parameters) == ["name", "ttl"]


def test_poller_does_not_call_acquire_named_lock():
    """설명 주석에는 이름이 나오지만 **호출**은 없어야 한다."""
    src = open(krp.__file__, encoding="utf-8").read()
    for helper in ("acquire_named_lock", "renew_named_lock", "release_named_lock"):
        assert f"sc.{helper}(" not in src
        assert f"await {helper}(" not in src


def test_poller_throttle_uses_an_isolated_connection():
    src = open(krp.__file__, encoding="utf-8").read()
    assert "db_write_isolated" in src
    assert "DB_PATH" in src


def test_clip_lock_hot_path_still_uses_the_shared_helper():
    """기존 호출부는 그대로다 — poller가 그 계약을 바꾸지 않았다."""
    src = inspect.getsource(sc.acquire_clip_lock)
    assert "acquire_named_lock(name, CLIP_LOCK_TTL)" in src


# ── 도우미 ─────────────────────────────────────────────────────────────────
def _const(value):
    async def _f(*a, **kw):
        return value

    return _f
