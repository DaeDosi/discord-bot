"""`_ensure_fresh_token`의 DB 반영 — 상태 저장·격리·원자성.

정책 자체(무엇이 영구 오류인가, 언제 멈추는가)는 test_oauth_backoff.py가 본다.
여기서는 그 정책이 **실제 DB와 루프에 어떻게 반영되는가**만 확인한다.
"""
import asyncio
import logging
import time

import pytest

import database
from utils import oauth_backoff as ob

chat_mod = pytest.importorskip("cogs.chzzk_chat")


class _Refreshed:
    def __init__(self, at="AT", rt="RT", expires_in=3600):
        self.access_token, self.refresh_token, self.expires_in = at, rt, expires_in


class _FakeClient:
    """chzzkpy Client 대역. 호출 횟수를 세고 지정한 결과를 돌려준다."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def refresh_access_token(self, _refresh_token):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Cog:
    """cog 인스턴스를 만들지 않고 대상 메서드만 빌려 쓴다(discord 런타임 불필요)."""

    _token_log_at: dict = {}
    _token_next_try_mem: dict = {}
    _token_locks = chat_mod.ChzzkChatCog._token_locks

    def __init__(self, client):
        self.client = client
        self.bot = None

    _token_lock = chat_mod.ChzzkChatCog._token_lock
    _still_valid = staticmethod(chat_mod.ChzzkChatCog._still_valid)
    _reload_token_row = chat_mod.ChzzkChatCog._reload_token_row
    _save_token_state = chat_mod.ChzzkChatCog._save_token_state
    _refresh_token_locked = chat_mod.ChzzkChatCog._refresh_token_locked
    _ensure_fresh_token = chat_mod.ChzzkChatCog._ensure_fresh_token


async def _seed(guild_id, *, expires_at=0, state="ok", fail_count=0, next_try_at=0):
    db = await database.get_db()
    await db.execute(
        "INSERT INTO chzzk_subscriptions (guild_id, discord_channel, chzzk_channel_id,"
        " chzzk_name, streamer_access_token, streamer_refresh_token,"
        " streamer_token_expires_at, chat_enabled, token_state, token_fail_count,"
        " token_next_try_at) VALUES (?,?,?,?,?,?,?,1,?,?,?)",
        (guild_id, "1", f"ch{guild_id}", "n", "OLD_AT", "OLD_RT", expires_at,
         state, fail_count, next_try_at))
    await db.commit()


async def _row(guild_id):
    db = await database.get_db()
    r = await (await db.execute(
        "SELECT * FROM chzzk_subscriptions WHERE guild_id=?", (guild_id,))).fetchone()
    return dict(r)


def _call(db, cog, row):
    return db(cog._ensure_fresh_token(row))


@pytest.fixture(autouse=True)
def _clean_subscriptions(db):
    """conftest의 db 픽스처는 chzzk_subscriptions를 비우지 않는다(다른 테스트가
    쓰지 않아서다). 이 파일에서만 필요하므로 여기서 치운다."""
    async def wipe():
        conn = await database.get_db()
        await conn.execute("DELETE FROM chzzk_subscriptions")
        await conn.commit()
    db(wipe())
    _Cog._token_log_at.clear()
    _Cog._token_next_try_mem.clear()
    yield
    db(wipe())


# ── 1. 정상 갱신 ───────────────────────────────────────────────────────────
def test_valid_token_is_reused_without_a_request(db):
    db(_seed(1, expires_at=int(time.time()) + 4000))
    cog = _Cog(_FakeClient(_Refreshed()))
    at, _rt, _exp = _call(db, cog, db(_row(1)))
    assert at == "OLD_AT"
    assert cog.client.calls == 0, "아직 안 만료된 토큰으로 요청을 보내면 안 된다"


def test_expired_token_is_refreshed_and_saved(db):
    db(_seed(1, expires_at=int(time.time()) - 10))
    cog = _Cog(_FakeClient(_Refreshed("NEW_AT", "NEW_RT")))
    at, rt, _exp = _call(db, cog, db(_row(1)))
    assert (at, rt) == ("NEW_AT", "NEW_RT")
    r = db(_row(1))
    assert r["streamer_access_token"] == "NEW_AT"
    assert r["streamer_refresh_token"] == "NEW_RT"
    assert r["token_state"] == ob.STATE_OK
    assert r["token_fail_count"] == 0


def test_refresh_writes_tokens_and_state_atomically(db):
    """토큰만 저장되고 실패 상태가 남는 중간 상태가 생기면 안 된다."""
    db(_seed(1, expires_at=0, state=ob.STATE_RETRYING, fail_count=2))
    cog = _Cog(_FakeClient(_Refreshed("NEW_AT", "NEW_RT")))
    _call(db, cog, db(_row(1)))
    r = db(_row(1))
    assert r["streamer_access_token"] == "NEW_AT"
    assert r["token_state"] == ob.STATE_OK and r["token_fail_count"] == 0
    assert r["token_next_try_at"] == 0 and r["token_last_error_code"] == ""


# ── 2. INVALID_TOKEN ───────────────────────────────────────────────────────
def test_first_invalid_token_records_state(db):
    db(_seed(1, expires_at=0))
    cog = _Cog(_FakeClient(Exception("INVALID_TOKEN")))
    at, _rt, _exp = _call(db, cog, db(_row(1)))
    assert at is None
    r = db(_row(1))
    assert r["token_state"] == ob.STATE_RETRYING
    assert r["token_fail_count"] == 1
    assert r["token_last_error_code"] == "INVALID_TOKEN"
    assert r["token_next_try_at"] > int(time.time())
    assert r["streamer_refresh_token"] == "OLD_RT", "실패했다고 토큰을 지우지 않는다"


def test_backoff_blocks_the_next_request(db):
    db(_seed(1, expires_at=0))
    cog = _Cog(_FakeClient(Exception("INVALID_TOKEN")))
    _call(db, cog, db(_row(1)))
    assert cog.client.calls == 1
    _call(db, cog, db(_row(1)))            # 백오프 안이라 요청이 나가면 안 된다
    assert cog.client.calls == 1, "60초 안에 다시 두드리면 안 된다"


def test_repeated_invalid_token_reaches_reauth_required(db):
    db(_seed(1, expires_at=0))
    cog = _Cog(_FakeClient(Exception("INVALID_TOKEN")))
    for _ in range(ob.MAX_PERMANENT_FAILURES):
        r = db(_row(1))
        # 백오프를 지난 것처럼 되돌린다(실제 시간을 기다리지 않는다)
        db(_clear_next_try(1))
        r = db(_row(1))
        _call(db, cog, r)
    r = db(_row(1))
    assert r["token_state"] == ob.STATE_REAUTH
    assert r["token_fail_count"] == ob.MAX_PERMANENT_FAILURES


async def _clear_next_try(guild_id):
    db = await database.get_db()
    await db.execute(
        "UPDATE chzzk_subscriptions SET token_next_try_at=0 WHERE guild_id=?",
        (guild_id,))
    await db.commit()


def test_reauth_required_stops_requests_forever(db):
    db(_seed(1, expires_at=0, state=ob.STATE_REAUTH, fail_count=3))
    cog = _Cog(_FakeClient(Exception("INVALID_TOKEN")))
    for _ in range(20):
        db(_clear_next_try(1))
        _call(db, cog, db(_row(1)))
    assert cog.client.calls == 0, "재연동 대기 상태에서는 한 번도 부르지 않는다"


# ── 3. 일시 오류는 연동을 끊지 않는다 ──────────────────────────────────────
@pytest.mark.parametrize("msg", ["HTTP 429", "HTTP 500", "HTTP 503"])
def test_transient_errors_keep_retrying(db, msg):
    db(_seed(1, expires_at=0))
    cog = _Cog(_FakeClient(Exception(msg)))
    for _ in range(10):
        db(_clear_next_try(1))
        _call(db, cog, db(_row(1)))
    r = db(_row(1))
    assert r["token_state"] == ob.STATE_RETRYING
    assert r["token_state"] != ob.STATE_REAUTH
    assert cog.client.calls == 10


def test_recovery_after_transient_failures(db):
    db(_seed(1, expires_at=0, state=ob.STATE_RETRYING, fail_count=4))
    cog = _Cog(_FakeClient(_Refreshed("NEW_AT", "NEW_RT")))
    _call(db, cog, db(_row(1)))
    r = db(_row(1))
    assert r["token_state"] == ob.STATE_OK and r["token_fail_count"] == 0


# ── 4. guild 격리 ──────────────────────────────────────────────────────────
def test_one_failing_guild_does_not_affect_another(db):
    db(_seed(1, expires_at=0))
    db(_seed(2, expires_at=0))
    bad = _Cog(_FakeClient(Exception("INVALID_TOKEN")))
    good = _Cog(_FakeClient(_Refreshed("G2_AT", "G2_RT")))
    _call(db, bad, db(_row(1)))
    at, _rt, _exp = _call(db, good, db(_row(2)))
    assert at == "G2_AT"
    assert db(_row(1))["token_state"] == ob.STATE_RETRYING
    assert db(_row(2))["token_state"] == ob.STATE_OK


# ── 5. 로그에 토큰이 없다 ──────────────────────────────────────────────────
def test_tokens_never_appear_in_logs(db, caplog):
    db(_seed(1, expires_at=0))
    leaky = ("HTTPException Authorization=Bearer SUPERSECRETACCESS "
             "refresh_token=SUPERSECRETREFRESH {'code':'INVALID_TOKEN'}")
    cog = _Cog(_FakeClient(Exception(leaky)))
    with caplog.at_level(logging.INFO):
        _call(db, cog, db(_row(1)))
    text = caplog.text
    assert "INVALID_TOKEN" in text, "무슨 오류인지는 알 수 있어야 한다"
    for secret in ("SUPERSECRETACCESS", "SUPERSECRETREFRESH", "OLD_AT", "OLD_RT"):
        assert secret not in text, secret
    assert "SUPERSECRETREFRESH" not in db(_row(1))["token_last_error_code"]


def test_same_error_is_not_logged_every_minute(db, caplog):
    db(_seed(1, expires_at=0))
    cog = _Cog(_FakeClient(Exception("INVALID_TOKEN")))
    with caplog.at_level(logging.WARNING):
        for _ in range(30):
            db(_clear_next_try(1))
            _call(db, cog, db(_row(1)))
    lines = [r for r in caplog.records if "토큰 갱신 실패" in r.getMessage()]
    # 상태 전환(retrying → reauth_required)마다 1회 수준. 예전에는 30회였다.
    assert len(lines) <= 3, f"{len(lines)}줄 — 매 시도마다 찍으면 안 된다"


# ── 6. 재연동 콜백이 상태를 초기화한다 ────────────────────────────────────
def test_reauth_callback_resets_state(db):
    """OAuth 콜백의 UPDATE 문이 상태 컬럼까지 함께 초기화하는지 소스로 확인한다."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "web" / "backend" / "routers"
           / "chzzk_auth_router.py").read_text(encoding="utf-8")
    assert "token_state='ok'" in src
    assert "token_fail_count=0" in src
    assert "token_next_try_at=0" in src


# ── 7. DB 잠금 ─────────────────────────────────────────────────────────────
def test_locked_state_write_does_not_return_to_per_minute_retry(db, monkeypatch):
    """상태 저장이 잠금으로 실패해도 매분 재시도로 되돌아가면 안 된다."""
    from utils import db_write as dw
    db(_seed(1, expires_at=0))
    cog = _Cog(_FakeClient(Exception("INVALID_TOKEN")))

    async def always_locked(*_a, **_k):
        return False
    monkeypatch.setattr(chat_mod, "db_write", always_locked)

    _call(db, cog, db(_row(1)))
    assert cog.client.calls == 1
    # DB에는 아무것도 안 남았지만(잠금), 메모리 백오프가 다음 시도를 막는다
    assert db(_row(1))["token_state"] == ob.STATE_OK
    for _ in range(10):
        _call(db, cog, db(_row(1)))
    assert cog.client.calls == 1, "잠금 때문에 매분 재시도로 되돌아갔다"
    assert dw is not None


def test_token_is_not_used_when_its_write_failed(db, monkeypatch):
    """저장에 실패한 새 토큰을 메모리에서만 쓰면 DB와 어긋난다 — 쓰지 않는다."""
    db(_seed(1, expires_at=0))
    cog = _Cog(_FakeClient(_Refreshed("NEW_AT", "NEW_RT")))

    async def always_locked(*_a, **_k):
        return False
    monkeypatch.setattr(chat_mod, "db_write", always_locked)

    at, _rt, _exp = _call(db, cog, db(_row(1)))
    assert at is None
    assert db(_row(1))["streamer_access_token"] == "OLD_AT"


# ── 8. 동시 갱신 ───────────────────────────────────────────────────────────
class _SlowClient(_FakeClient):
    """요청이 진행되는 동안 다른 코루틴이 끼어들 틈을 만든다."""

    async def refresh_access_token(self, refresh_token):
        self.calls += 1
        await asyncio.sleep(0.05)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def test_concurrent_refresh_calls_the_api_once(db):
    """치지직은 갱신 성공 시 refresh token을 회전시킨다 — 두 번 부르면 두 번째가
    이미 폐기된 토큰을 쓰게 되고, 그것이 곧 INVALID_TOKEN이다."""
    db(_seed(1, expires_at=0))
    cog = _Cog(_SlowClient(_Refreshed("NEW_AT", "NEW_RT")))

    async def both():
        row = await _row(1)
        return await asyncio.gather(cog._ensure_fresh_token(row),
                                    cog._ensure_fresh_token(row))

    (a1, _, _), (a2, _, _) = db(both())
    assert cog.client.calls == 1, f"{cog.client.calls}회 — 같은 guild를 동시에 갱신했다"
    assert a1 == a2 == "NEW_AT", "뒤늦은 쪽도 갱신된 토큰을 받아야 한다"
    assert db(_row(1))["streamer_access_token"] == "NEW_AT"


def test_concurrent_failures_record_one_failure(db):
    """동시 호출이 실패 횟수를 두 배로 세면 백오프가 실제보다 빨리 커진다."""
    db(_seed(1, expires_at=0))
    cog = _Cog(_SlowClient(Exception("HTTP 503")))

    async def both():
        row = await _row(1)
        await asyncio.gather(cog._ensure_fresh_token(row),
                             cog._ensure_fresh_token(row))

    db(both())
    assert cog.client.calls == 1
    assert db(_row(1))["token_fail_count"] == 1


def test_a_second_guild_is_not_blocked_by_a_slow_refresh(db):
    """guild별 락이다 — 한 guild의 느린 갱신이 다른 guild를 막으면 안 된다."""
    db(_seed(1, expires_at=0))
    db(_seed(2, expires_at=0))
    cog = _Cog(_SlowClient(_Refreshed("NEW_AT", "NEW_RT")))

    async def both():
        r1, r2 = await _row(1), await _row(2)
        t0 = time.perf_counter()
        await asyncio.gather(cog._ensure_fresh_token(r1),
                             cog._ensure_fresh_token(r2))
        return time.perf_counter() - t0

    elapsed = db(both())
    assert cog.client.calls == 2
    assert elapsed < 0.09, f"{elapsed:.3f}s — 직렬화됐다(0.05s×2)"


# ── 9. 취소 ────────────────────────────────────────────────────────────────
def test_cancel_during_the_api_call_leaves_no_stuck_state(db):
    """외부 호출 중 취소돼도 락이 남거나 갱신 불가 상태가 되면 안 된다."""
    db(_seed(1, expires_at=0))
    cog = _Cog(_SlowClient(_Refreshed("NEW_AT", "NEW_RT")))

    async def cancel_then_retry():
        row = await _row(1)
        task = asyncio.ensure_future(cog._ensure_fresh_token(row))
        await asyncio.sleep(0.01)              # 요청이 나간 뒤
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # 락이 풀렸는지 = 다음 호출이 그대로 진행되는지
        return await cog._ensure_fresh_token(await _row(1))

    at, _rt, _exp = db(cancel_then_retry())
    assert at == "NEW_AT", "취소 뒤 다음 시도가 막히면 영구히 갱신 불가가 된다"
    assert db(_row(1))["token_state"] == ob.STATE_OK


def test_cancel_during_the_state_write_rolls_back(db):
    """상태 저장 중 취소 — 열린 트랜잭션을 남기면 P0 장애가 그대로 재현된다.

    Change Set C의 쓰기는 전부 `db_write`를 지나므로, 그 경계에 취소를 넣어 본다.
    """
    from utils.db_write import db_write as real_db_write
    db(_seed(1, expires_at=0))

    async def cancelled_mid_write():
        async def work(conn):
            await conn.execute(                 # 여기서 트랜잭션이 열린다
                "UPDATE chzzk_subscriptions SET token_fail_count=99 WHERE guild_id=1")
            raise asyncio.CancelledError()      # 갱신 도중 취소가 도착한 상황
        with pytest.raises(asyncio.CancelledError):
            await real_db_write(database.get_db, work, what="test_cancel")
        conn = await database.get_db()
        assert conn.in_transaction is False, "트랜잭션이 열린 채 남았다"

    db(cancelled_mid_write())
    assert db(_row(1))["token_fail_count"] == 0, "취소된 쓰기가 남았다"

    # 취소 뒤에도 정상 경로가 그대로 동작한다
    cog = _Cog(_FakeClient(_Refreshed("NEW_AT", "NEW_RT")))
    at, _rt, _exp = _call(db, cog, db(_row(1)))
    assert at == "NEW_AT"


# ── 10. 성공 시각 ──────────────────────────────────────────────────────────
def test_success_clears_failure_history(db):
    db(_seed(1, expires_at=0, state=ob.STATE_RETRYING, fail_count=1))

    async def mark_failed():
        conn = await database.get_db()
        await conn.execute(
            "UPDATE chzzk_subscriptions SET token_last_fail_at=?,"
            " token_last_error_code='INVALID_TOKEN' WHERE guild_id=1",
            (int(time.time()) - 10,))
        await conn.commit()

    db(mark_failed())
    cog = _Cog(_FakeClient(_Refreshed("NEW_AT", "NEW_RT")))
    _call(db, cog, db(_row(1)))
    r = db(_row(1))
    assert r["token_last_fail_at"] == 0 and r["token_last_error_code"] == ""
    assert r["token_last_success_at"] >= int(time.time()) - 5


def test_state_write_is_a_single_statement(db):
    """토큰과 상태를 나눠 쓰면 중간 상태가 생긴다 — 한 UPDATE인지 소스로 확인."""
    import inspect
    src = inspect.getsource(chat_mod.ChzzkChatCog._refresh_token_locked)
    assert src.count("UPDATE chzzk_subscriptions") == 1
    assert "db_write(" in src
