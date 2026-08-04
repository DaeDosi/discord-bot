"""AWS 서울 poller — clip lock / nonce의 **실제** DB 잠금 안전성 (차단 조건 H).

monkeypatch로 예외만 흉내 내면 "공유 연결에 미완료 트랜잭션이 남지 않았다"를
증명하지 못한다. 그래서 여기서는 **별도 aiosqlite 연결에서 `BEGIN IMMEDIATE`로
진짜 write lock을 잡고** 검증한다.

배경(이 기능이 존재하는 이유):
하트는 `interaction.emotion.reactions`, 조회수는 `content.vod.count`에 있다.
`krOnlyViewing=true` 클립을 Railway 해외 IP에서 부르면 HTTP 200이면서도
**`content.vod` 블록 전체가 제거**된다. 하트 블록은 남으므로 하트만 갱신되고
조회수는 unknown으로 남는다. **0으로 응답된 게 아니라 컨테이너가 누락된 것**이라
`observed_zero`가 아니라 `unknown`이며, 한국에서 같은 API를 불러야 복구된다.

여기서 고정하는 계약:
  ① poller의 clip lock·nonce는 **전용 연결**을 쓴다. 공유 연결(`get_db()`)은
     봇·백엔드의 다른 모든 쓰기가 함께 쓰므로 거기에 트랜잭션이 남으면 피해가
     이 기능 밖으로 번진다.
  ② 락 **이름과 TTL은 기존과 같다** — 자동 스윕·관리자 단건 갱신과 상호 배제된다.
  ③ DB busy는 인증 실패(401)로도 호출 빈도 문제(429)로도 위장하지 않는다.
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
UID = "c-1"


@pytest.fixture
def client(db, monkeypatch):
    import routers.kr_poller_router as kr

    monkeypatch.setenv("SINGCUP_KR_POLLER_SECRET", SECRET)
    monkeypatch.setenv("SINGCUP_KRP_ENABLED", "true")
    app = FastAPI()
    app.include_router(kr.router)
    return TestClient(app, raise_server_exceptions=False)


def _headers(body, path=TASKS, nonce=None):
    ts = str(int(time.time()))
    nonce = nonce or hashlib.sha256(
        f"{time.time()}{path}".encode()).hexdigest()[:32]
    msg = f"{ts}\nPOST\n{path}\n{hashlib.sha256(body).hexdigest()}"
    sig = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"X-KRP-Timestamp": ts, "X-KRP-Nonce": nonce, "X-KRP-Signature": sig,
            "Content-Type": "application/json"}


def _post(client, nonce=None):
    body = json.dumps({"limit": 5}).encode()
    return client.post(TASKS, content=body, headers=_headers(body, nonce=nonce))


async def _hold_write_lock():
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


async def _shared_in_transaction():
    conn = await database.get_db()
    return conn.in_transaction


# ── ① clip lock: 실제 DB 잠금 ──────────────────────────────────────────────
def test_clip_lock_acquire_gives_up_on_db_busy(db):
    async def scenario():
        blocker = await _hold_write_lock()
        try:
            return await krp.clip_lock_acquire(UID)
        finally:
            await _release(blocker)

    verdict, token = db(scenario())
    assert verdict == krp.CLIP_DB_BUSY
    assert token is None


def test_shared_connection_has_no_open_transaction_after_db_busy(db):
    """**핵심 증명** — 공유 연결에 미완료 트랜잭션이 남으면 안 된다."""
    async def scenario():
        blocker = await _hold_write_lock()
        try:
            await krp.clip_lock_acquire(UID)
            return await _shared_in_transaction()
        finally:
            await _release(blocker)

    assert db(scenario()) is False


def test_shared_connection_is_clean_after_release_db_busy(db):
    async def scenario():
        verdict, token = await krp.clip_lock_acquire(UID)
        assert verdict == krp.CLIP_ACQUIRED
        blocker = await _hold_write_lock()
        try:
            ok = await krp.clip_lock_release(UID, token)
            return ok, await _shared_in_transaction()
        finally:
            await _release(blocker)

    ok, in_tx = db(scenario())
    assert ok is False              # 못 풀었지만
    assert in_tx is False           # 공유 연결은 깨끗하다


def test_acquire_succeeds_immediately_after_lock_is_released(db):
    async def scenario():
        blocker = await _hold_write_lock()
        busy, _ = await krp.clip_lock_acquire(UID)
        await _release(blocker)
        after, token = await krp.clip_lock_acquire(UID)
        return busy, after, token

    busy, after, token = db(scenario())
    assert busy == krp.CLIP_DB_BUSY
    assert after == krp.CLIP_ACQUIRED and token


def test_clip_lock_release_and_reacquire(db):
    async def scenario():
        v1, t1 = await krp.clip_lock_acquire(UID)
        held, _ = await krp.clip_lock_acquire(UID)
        released = await krp.clip_lock_release(UID, t1)
        v2, t2 = await krp.clip_lock_acquire(UID)
        return v1, held, released, v2

    v1, held, released, v2 = db(scenario())
    assert v1 == krp.CLIP_ACQUIRED
    assert held == krp.CLIP_HELD
    assert released is True
    assert v2 == krp.CLIP_ACQUIRED


def test_ttl_expiry_allows_reacquire_without_release(db, monkeypatch):
    """해제를 못 했어도 TTL이 회수한다 — release busy가 영구 교착이 되지 않는다."""
    assert db(krp.clip_lock_acquire(UID))[0] == krp.CLIP_ACQUIRED
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + sc.CLIP_LOCK_TTL + 1)
    assert db(krp.clip_lock_acquire(UID))[0] == krp.CLIP_ACQUIRED


# ── ② 기존 hot path와의 상호 배제 (양방향) ────────────────────────────────
def test_hot_path_lock_is_seen_as_already_held_by_poller(db):
    """자동 스윕이 잡은 클립을 poller가 가로채면 안 된다."""
    async def scenario():
        token = await sc.acquire_clip_lock(UID, wait=0)
        assert token is not None
        try:
            return await krp.clip_lock_acquire(UID)
        finally:
            await sc.release_clip_lock(UID, token)

    verdict, token = db(scenario())
    assert verdict == krp.CLIP_HELD
    assert token is None


def test_poller_lock_blocks_the_hot_path(db):
    """반대 방향도 성립해야 같은 자원을 공유한다고 말할 수 있다."""
    async def scenario():
        verdict, token = await krp.clip_lock_acquire(UID)
        assert verdict == krp.CLIP_ACQUIRED
        try:
            return await sc.acquire_clip_lock(UID, wait=0)
        finally:
            await krp.clip_lock_release(UID, token)

    assert db(scenario()) is None


def test_poller_and_hot_path_use_the_same_lock_name():
    assert sc.clip_lock_name(UID) == f"singcup_clip:{UID}"
    src = inspect.getsource(krp.clip_lock_acquire)
    assert "sc.clip_lock_name(clip_uid)" in src
    assert "sc.CLIP_LOCK_TTL" in src


def test_hot_path_lock_is_released_and_poller_can_take_it(db):
    async def scenario():
        token = await sc.acquire_clip_lock(UID, wait=0)
        await sc.release_clip_lock(UID, token)
        return await krp.clip_lock_acquire(UID)

    assert db(scenario())[0] == krp.CLIP_ACQUIRED


# ── ③ 예외·취소 ───────────────────────────────────────────────────────────
def test_non_lock_exception_propagates_from_acquire(db, monkeypatch):
    async def _boom(*a, **kw):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(krp, "db_write_isolated", _boom)
    with pytest.raises(RuntimeError):
        db(krp.clip_lock_acquire(UID))


def test_cancellation_propagates_from_acquire(db, monkeypatch):
    async def _cancel(*a, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(krp, "db_write_isolated", _cancel)
    with pytest.raises(asyncio.CancelledError):
        db(krp.clip_lock_acquire(UID))


def test_cancellation_propagates_from_release(db, monkeypatch):
    async def _cancel(*a, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(krp, "db_write_isolated", _cancel)
    with pytest.raises(asyncio.CancelledError):
        db(krp.clip_lock_release(UID, "tok"))


def test_shared_connection_is_clean_after_cancellation(db, monkeypatch):
    async def _cancel(*a, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(krp, "db_write_isolated", _cancel)
    with pytest.raises(asyncio.CancelledError):
        db(krp.clip_lock_acquire(UID))
    assert db(_shared_in_transaction()) is False


# ── ④ 결과 반영 경로 전체 (실제 잠금) ──────────────────────────────────────
def _seeded_result(db, uid=UID):
    import test_kr_poller_flow as flow
    db(flow._seed(uid))
    t = db(krp.lease_tasks(int(time.time()), 25))[0]
    return t, flow._ok(uid, task=t, observed_at=int(time.time()))


def test_apply_results_returns_db_locked_under_real_lock(db):
    t, item = _seeded_result(db)

    async def scenario():
        blocker = await _hold_write_lock()
        try:
            return await krp.apply_results([item], int(time.time()))
        finally:
            await _release(blocker)

    out = db(scenario())
    assert out["stored"] == 0
    assert out["rejected"][0]["reason"] == "db_locked"
    assert db(_shared_in_transaction()) is False


def test_apply_results_keeps_the_lease_retryable_under_real_lock(db):
    t, item = _seeded_result(db)

    async def scenario():
        blocker = await _hold_write_lock()
        try:
            await krp.apply_results([item], int(time.time()))
        finally:
            await _release(blocker)
        conn = await database.get_db()
        return dict(await (await conn.execute(
            "SELECT * FROM singcup_kr_poller_lease WHERE task_id=?",
            (item["taskId"],))).fetchone())

    assert db(scenario())["done_at"] == 0


def test_apply_results_succeeds_after_the_lock_is_released(db):
    t, item = _seeded_result(db)

    async def scenario():
        blocker = await _hold_write_lock()
        first = await krp.apply_results([item], int(time.time()))
        await _release(blocker)
        second = await krp.apply_results([item], int(time.time()))
        return first, second

    first, second = db(scenario())
    assert first["stored"] == 0
    assert second["stored"] == 1


def test_release_busy_does_not_undo_a_successful_store(db, monkeypatch):
    t, item = _seeded_result(db)

    async def _busy(uid, token):
        return False                       # 해제 실패(= DB busy)

    monkeypatch.setattr(krp, "clip_lock_release", _busy)
    out = db(krp.apply_results([item], int(time.time())))
    assert out["stored"] == 1              # 저장은 유지된다


# ── ⑤ nonce (H2) ──────────────────────────────────────────────────────────
def test_nonce_states(db):
    now = int(time.time())
    assert db(krp.consume_nonce("n-1", now)) == krp.NONCE_NEW
    assert db(krp.consume_nonce("n-1", now)) == krp.NONCE_REPLAY


def test_nonce_db_busy_is_its_own_state(db):
    async def scenario():
        blocker = await _hold_write_lock()
        try:
            return await krp.consume_nonce("n-2", int(time.time()))
        finally:
            await _release(blocker)

    verdict = db(scenario())
    assert verdict == krp.NONCE_DB_BUSY
    assert verdict != krp.NONCE_REPLAY
    assert db(_shared_in_transaction()) is False


def test_nonce_db_busy_is_503_not_401(client, db):
    async def hold():
        return await _hold_write_lock()

    blocker = db(hold())
    try:
        r = _post(client)
        assert r.status_code == 503
        assert r.headers.get("Retry-After") == str(krp.BUSY_RETRY_AFTER)
    finally:
        db(_release(blocker))


def test_normal_request_succeeds_after_the_lock_is_released(client, db):
    async def hold():
        return await _hold_write_lock()

    blocker = db(hold())
    assert _post(client).status_code == 503
    db(_release(blocker))
    assert _post(client).status_code == 200


def test_replay_does_not_consume_a_throttle_slot(client, db, monkeypatch):
    """이미 쓴 서명을 재전송해도 뒤이어 오는 정상 요청이 손해를 보면 안 된다."""
    monkeypatch.setattr(krp, "MIN_INTERVAL_SECONDS", 30)
    used = "a" * 32
    assert _post(client, nonce=used).status_code == 200       # 슬롯 1회 소비

    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 31)     # 창이 다시 열림
    assert _post(client, nonce=used).status_code == 401        # 재전송 → 401
    # 재전송이 슬롯을 먹지 않았으므로 정상 요청이 통과해야 한다
    assert _post(client, nonce="b" * 32).status_code == 200


def test_replay_is_rejected_before_the_throttle_is_touched(db, monkeypatch):
    """순서 자체를 고정한다 — nonce 판정이 스로틀보다 먼저다."""
    import routers.kr_poller_router as kr
    src = inspect.getsource(kr._guard)
    assert src.index("consume_nonce") < src.index("throttle_acquire")


# ── ⑥ 응답 비노출 ─────────────────────────────────────────────────────────
def test_lock_error_responses_leak_nothing(client, db):
    async def hold():
        return await _hold_write_lock()

    blocker = db(hold())
    try:
        text = _post(client).text
        for leaked in ("taskId", "clipUid", "leaseToken", "Signature", "nonce",
                       "sqlite", ".db", "SELECT", "UPDATE", "Traceback",
                       "database is locked"):
            assert leaked not in text
    finally:
        db(_release(blocker))


# ── ⑦ 소스 계약: 기존 함수·호출부 무변경 ──────────────────────────────────
def test_apply_results_does_not_call_the_shared_clip_lock_helpers():
    """설명 주석에는 이름이 나오지만 **호출**은 없어야 한다."""
    src = inspect.getsource(krp.apply_results)
    assert "sc.acquire_clip_lock(" not in src
    assert "sc.release_clip_lock(" not in src
    assert "await clip_lock_acquire(" in src        # 전용 경로를 쓴다
    assert "await clip_lock_release(" in src


def test_poller_module_never_calls_the_shared_lock_helpers():
    src = open(krp.__file__, encoding="utf-8").read()
    for helper in ("acquire_named_lock", "release_named_lock", "renew_named_lock",
                   "acquire_clip_lock", "release_clip_lock"):
        assert f"sc.{helper}(" not in src
        assert f"await {helper}(" not in src


def test_shared_clip_lock_helper_is_unchanged():
    src = inspect.getsource(sc.acquire_clip_lock)
    assert "acquire_named_lock(name, CLIP_LOCK_TTL)" in src
    assert list(inspect.signature(sc.acquire_clip_lock).parameters) == \
        ["clip_uid", "wait"]


def test_shared_release_helper_is_unchanged():
    src = inspect.getsource(sc.release_clip_lock)
    assert "release_named_lock(clip_lock_name(clip_uid), token)" in src
