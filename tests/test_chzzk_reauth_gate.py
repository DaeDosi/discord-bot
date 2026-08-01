"""재연동 필요 상태에서의 API 보호와 상태 노출.

프론트에서 버튼을 비활성화하는 것만으로는 부족하다 — API를 직접 부르면 그만이고,
그러면 무효 토큰을 쓰는 경로(테스트 메시지 큐 → 봇의 실제 채팅 전송)가 되살아난다.
여기서 고정하는 것: **쓰기는 막고 조회는 막지 않는다.** 상태를 못 보면 재연동을
해야 한다는 사실 자체를 알 수 없기 때문이다.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from utils import oauth_backoff as ob

GUILD = 886237674665549865
REAUTH_CODE = "CHZZK_REAUTH_REQUIRED"


@pytest.fixture(autouse=True)
def _clean(db):
    """conftest의 db 픽스처는 치지직 채팅 테이블을 비우지 않는다(다른 테스트가 안 써서다)."""
    async def wipe():
        conn = await database.get_db()
        for t in ("chzzk_subscriptions", "chzzk_chat_commands", "chzzk_chat_test_queue",
                  "chzzk_chat_log"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.commit()
    db(wipe())
    yield
    db(wipe())


@pytest.fixture
def client(db, monkeypatch):
    import routers.chzzk_router as cr
    from deps import get_current_user, require_guild_admin

    app = FastAPI()
    app.include_router(cr.router)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "1"}
    app.dependency_overrides[require_guild_admin] = lambda: None
    # 출석 기록 조회가 디스코드로 나가지 않게 한다(이 테스트의 관심사가 아니다).
    monkeypatch.setattr(cr, "_fetch_member_name", _fake_member_name)
    return TestClient(app)


async def _fake_member_name(_client, _gid, _uid):
    return "테스터"


async def _seed(*, state="ok", fail_count=0, error_code="", linked=True,
                last_fail_at=0, next_try_at=0, last_success_at=0):
    conn = await database.get_db()
    await conn.execute("DELETE FROM chzzk_subscriptions WHERE guild_id=?", (GUILD,))
    await conn.execute(
        "INSERT INTO chzzk_subscriptions (guild_id, discord_channel, chzzk_channel_id,"
        " chzzk_name, streamer_access_token, streamer_refresh_token, chat_enabled,"
        " token_state, token_fail_count, token_last_error_code, token_last_fail_at,"
        " token_next_try_at, token_last_success_at) VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?)",
        (GUILD, "1", "ch1", "만화소녀",
         "AT_SECRET" if linked else None, "RT_SECRET" if linked else None,
         state, fail_count, error_code, last_fail_at, next_try_at, last_success_at))
    await conn.commit()


async def _commands_count():
    conn = await database.get_db()
    r = await (await conn.execute(
        "SELECT COUNT(*) FROM chzzk_chat_commands WHERE guild_id=?", (GUILD,))).fetchone()
    return r[0]


async def _seed_command():
    conn = await database.get_db()
    cur = await conn.execute(
        "INSERT INTO chzzk_chat_commands (guild_id, command_type, trigger_text,"
        " reward_points, reward_xp, reply_text, is_active, created_at)"
        " VALUES (?,'reply','안녕',0,0,'반가워요',1,0)", (GUILD,))
    await conn.commit()
    return cur.lastrowid


async def _queue_count():
    conn = await database.get_db()
    r = await (await conn.execute(
        "SELECT COUNT(*) FROM chzzk_chat_test_queue WHERE guild_id=?", (GUILD,))).fetchone()
    return r[0]


# ── 1. 쓰기 차단 ───────────────────────────────────────────────────────────
def test_chat_test_is_blocked(db, client):
    """이 큐는 봇이 실제 채팅 전송까지 태우는 경로다 — 무효 토큰 상태에서 열려 있으면 안 된다."""
    db(_seed(state=ob.STATE_REAUTH))
    r = client.post(f"/api/chzzk/{GUILD}/chat-test", json={"content": "!포인트"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == REAUTH_CODE
    assert r.json()["detail"]["reauthRequired"] is True
    assert db(_queue_count()) == 0, "차단됐는데 큐에 들어갔다"


def test_command_create_is_blocked(db, client):
    db(_seed(state=ob.STATE_REAUTH))
    r = client.post(f"/api/chzzk/{GUILD}/chat-commands",
                    json={"command_type": "reply", "trigger_text": "테스트",
                          "reward_points": 0, "reward_xp": 0, "reply_text": "응답",
                          "is_active": True})
    assert r.status_code == 409
    assert db(_commands_count()) == 0


def test_command_update_is_blocked(db, client):
    db(_seed(state=ob.STATE_REAUTH))
    cid = db(_seed_command())
    r = client.put(f"/api/chzzk/{GUILD}/chat-commands/{cid}",
                   json={"trigger_text": "바뀜", "reward_points": 0, "reward_xp": 0,
                         "reply_text": "바뀐 응답", "is_active": True})
    assert r.status_code == 409

    async def trigger():
        conn = await database.get_db()
        row = await (await conn.execute(
            "SELECT trigger_text FROM chzzk_chat_commands WHERE id=?", (cid,))).fetchone()
        return row["trigger_text"]

    assert db(trigger()) == "안녕", "차단됐는데 값이 바뀌었다"


def test_command_delete_is_blocked(db, client):
    """설정은 **보존**한다 — 인증이 만료됐다고 사용자 설정을 지우면 안 된다."""
    db(_seed(state=ob.STATE_REAUTH))
    cid = db(_seed_command())
    r = client.delete(f"/api/chzzk/{GUILD}/chat-commands/{cid}")
    assert r.status_code == 409
    assert db(_commands_count()) == 1


def test_blocked_response_leaks_nothing(db, client):
    db(_seed(state=ob.STATE_REAUTH, error_code="INVALID_TOKEN"))
    r = client.post(f"/api/chzzk/{GUILD}/chat-test", json={"content": "x"})
    text = r.text
    for secret in ("INVALID_TOKEN", "AT_SECRET", "RT_SECRET", "Authorization"):
        assert secret not in text, secret


# ── 2. 조회는 막지 않는다 ──────────────────────────────────────────────────
def test_reads_are_still_allowed(db, client):
    db(_seed(state=ob.STATE_REAUTH))
    db(_seed_command())
    assert client.get(f"/api/chzzk/{GUILD}/chat-commands").status_code == 200
    assert client.get(f"/api/chzzk/{GUILD}/chat-status").status_code == 200
    assert client.get(f"/api/chzzk/{GUILD}/chat-log").status_code == 200


# ── 3. 정상 상태는 그대로 동작한다 ─────────────────────────────────────────
@pytest.mark.parametrize("state", [ob.STATE_OK, ob.STATE_RETRYING])
def test_writes_pass_when_not_reauth(db, client, state):
    """일시 오류(retrying)에서 기능을 막으면 멀쩡한 서버가 죽는다."""
    db(_seed(state=state))
    r = client.post(f"/api/chzzk/{GUILD}/chat-test", json={"content": "!포인트"})
    assert r.status_code == 200, r.text
    assert db(_queue_count()) == 1


def test_other_guild_is_unaffected(db, client):
    """한 guild의 만료가 다른 guild를 막으면 안 된다."""
    db(_seed(state=ob.STATE_REAUTH))

    async def seed_other():
        conn = await database.get_db()
        await conn.execute(
            "INSERT INTO chzzk_subscriptions (guild_id, discord_channel,"
            " chzzk_channel_id, chzzk_name, streamer_access_token,"
            " streamer_refresh_token, chat_enabled, token_state)"
            " VALUES (222,'1','ch2','다른채널','AT','RT',1,'ok')")
        await conn.commit()

    db(seed_other())
    assert client.post("/api/chzzk/222/chat-test",
                       json={"content": "!포인트"}).status_code == 200
    assert client.post(f"/api/chzzk/{GUILD}/chat-test",
                       json={"content": "!포인트"}).status_code == 409


# ── 4. chat-status의 상태 필드 ─────────────────────────────────────────────
def test_status_reports_reauth(db, client):
    db(_seed(state=ob.STATE_REAUTH, fail_count=2, error_code="INVALID_TOKEN",
             last_fail_at=1_785_554_706))
    body = client.get(f"/api/chzzk/{GUILD}/chat-status").json()
    assert body["token_state"] == "reauth_required"
    assert body["reauth_required"] is True
    assert body["streamer_linked"] is True
    assert body["token_fail_count"] == 2
    assert body["token_last_error_code"] == "INVALID_TOKEN"
    assert body["token_last_fail_at"] == 1_785_554_706


def test_status_distinguishes_unlinked_from_ok(db, client):
    """구독 행은 있지만 스트리머 OAuth를 한 적 없는 경우. 컬럼 기본값이 'ok'라
    그대로 내보내면 '연동한 적 없음'이 '정상'으로 보인다."""
    db(_seed(linked=False, state=ob.STATE_OK))
    body = client.get(f"/api/chzzk/{GUILD}/chat-status").json()
    assert body["streamer_linked"] is False
    assert body["token_state"] is None
    assert body["reauth_required"] is False


def test_status_for_missing_subscription(db, client):
    body = client.get("/api/chzzk/999/chat-status").json()
    assert body["registered"] is False
    assert body["token_state"] is None
    assert body["streamer_linked"] is False
    assert body["reauth_required"] is False


def test_status_never_contains_tokens(db, client):
    db(_seed(state=ob.STATE_REAUTH))
    text = client.get(f"/api/chzzk/{GUILD}/chat-status").text
    for key in ("AT_SECRET", "RT_SECRET", "streamer_access_token",
                "streamer_refresh_token", "has_streamer_token"):
        assert key not in text, key


def test_retrying_status_exposes_next_try(db, client):
    db(_seed(state=ob.STATE_RETRYING, fail_count=1, next_try_at=1_785_554_766))
    body = client.get(f"/api/chzzk/{GUILD}/chat-status").json()
    assert body["token_state"] == "retrying"
    assert body["reauth_required"] is False
    assert body["token_next_try_at"] == 1_785_554_766
