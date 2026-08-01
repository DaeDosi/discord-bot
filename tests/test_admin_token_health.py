"""OWNER 전용 `/api/admin/chzzk`의 토큰 건강 상태 노출.

이 응답은 운영자가 "어느 서버가 재연동이 필요한가"를 보는 **유일한 경로**다
(치지직 대시보드의 chat-status는 그 서버 관리자만 볼 수 있다). 그래서 두 가지를
동시에 고정한다: 상태가 정확히 나오는가, 그리고 **토큰이 절대 새지 않는가.**

응답은 `dict(row)` 전개라 SELECT 목록이 곧 응답 필드다 — `SELECT *`로 바꾸는
순간 streamer_access_token이 그대로 나간다. 아래 test_no_secret_fields가 그
회귀를 잡는다.
"""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from utils import oauth_backoff as ob

OWNER = "111111111111111111"
GUILD = 886237674665549865


@pytest.fixture
def client(db, monkeypatch):
    import routers.admin_router as ar
    from deps import get_current_user

    monkeypatch.setattr(ar, "_OWNER_ID", OWNER)

    async def _fake_guilds(force: bool = False):
        return [{"id": str(GUILD), "name": "만화소녀의 사탕월드", "icon": None}]

    monkeypatch.setattr(ar, "_bot_guilds", _fake_guilds)

    app = FastAPI()
    app.include_router(ar.router)
    app.state._dep = get_current_user
    return TestClient(app, raise_server_exceptions=True)


def _as_owner(client, sub=OWNER):
    client.app.dependency_overrides[client.app.state._dep] = lambda: {"sub": sub}
    return client


async def _seed(guild_id=GUILD, *, state="ok", fail_count=0, error_code="",
                last_fail_at=0, next_try_at=0, last_success_at=0,
                refresh_token="RT_SECRET_VALUE"):
    conn = await database.get_db()
    await conn.execute("DELETE FROM chzzk_subscriptions WHERE guild_id=?", (guild_id,))
    await conn.execute(
        "INSERT INTO chzzk_subscriptions (guild_id, discord_channel, chzzk_channel_id,"
        " chzzk_name, streamer_access_token, streamer_refresh_token, token_state,"
        " token_fail_count, token_last_error_code, token_last_fail_at,"
        " token_next_try_at, token_last_success_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (guild_id, "1", "ch1", "만화소녀",
         "AT_SECRET_VALUE" if refresh_token else None, refresh_token,
         state, fail_count, error_code, last_fail_at, next_try_at, last_success_at))
    await conn.commit()


def _row(client):
    r = _as_owner(client).get("/api/admin/chzzk")
    assert r.status_code == 200, r.text
    body = r.json()
    return r, next(x for x in body if int(x["guild_id"]) == GUILD)


# ── 1. 상태 매핑 ───────────────────────────────────────────────────────────
def test_reauth_required_is_visible(db, client):
    db(_seed(state=ob.STATE_REAUTH, fail_count=2, error_code="INVALID_TOKEN",
             last_fail_at=1_754_000_000))
    _r, item = _row(client)
    assert item["token_state"] == "reauth_required"
    assert item["reauth_required"] is True
    assert item["token_fail_count"] == 2
    assert item["token_last_error_code"] == "INVALID_TOKEN"
    assert item["token_last_fail_at"] == 1_754_000_000
    assert item["token_next_try_at"] is None      # 예정 없음
    assert item["guild_name"] == "만화소녀의 사탕월드"
    assert item["chzzk_name"] == "만화소녀"


@pytest.mark.parametrize("state,expected_reauth", [
    (ob.STATE_OK, False), (ob.STATE_RETRYING, False), (ob.STATE_REAUTH, True),
    ("disabled", False),
])
def test_state_round_trips(db, client, state, expected_reauth):
    db(_seed(state=state))
    _r, item = _row(client)
    assert item["token_state"] == state
    assert item["reauth_required"] is expected_reauth


def test_unlinked_subscription_is_not_reported_as_healthy(db, client):
    """스트리머 OAuth를 한 적 없는 구독은 컬럼 기본값이 'ok'다 — 그대로 내보내면
    연동한 적 없는 서버가 '정상'으로 보인다."""
    db(_seed(refresh_token=None))
    _r, item = _row(client)
    assert item["streamer_linked"] is False
    assert item["token_state"] is None
    assert item["reauth_required"] is False
    assert item["token_fail_count"] == 0
    assert item["token_last_error_code"] is None


def test_zero_timestamps_become_null(db, client):
    db(_seed(state=ob.STATE_OK))
    _r, item = _row(client)
    for k in ("token_last_fail_at", "token_next_try_at", "token_last_success_at"):
        assert item[k] is None, k


def test_success_timestamp_is_reported(db, client):
    now = int(time.time())
    db(_seed(state=ob.STATE_OK, last_success_at=now))
    _r, item = _row(client)
    assert item["token_last_success_at"] == now


# ── 2. 비밀정보 미노출 ─────────────────────────────────────────────────────
def test_no_secret_fields(db, client):
    db(_seed(state=ob.STATE_REAUTH, error_code="INVALID_TOKEN"))
    r, _item = _row(client)
    text = r.text
    for secret in ("AT_SECRET_VALUE", "RT_SECRET_VALUE"):
        assert secret not in text, secret
    lowered = text.lower()
    for key in ("streamer_access_token", "streamer_refresh_token", "access_token",
                "refresh_token", "authorization", "cookie", "client_secret",
                "has_streamer_token"):
        assert key not in lowered, key
    # 안전한 상태 필드는 있어야 한다
    assert "token_state" in text and "token_fail_count" in text


# ── 3. 권한 ────────────────────────────────────────────────────────────────
def test_owner_gets_200(db, client):
    db(_seed())
    assert _as_owner(client).get("/api/admin/chzzk").status_code == 200


def test_non_owner_gets_403(db, client):
    db(_seed())
    assert _as_owner(client, sub="999").get("/api/admin/chzzk").status_code == 403


def test_unauthenticated_is_rejected(db, client):
    db(_seed())
    client.app.dependency_overrides.clear()
    assert client.get("/api/admin/chzzk").status_code in (401, 403)


# ── 4. 캐시 정책 ───────────────────────────────────────────────────────────
def test_response_is_not_publicly_cacheable(db, client):
    db(_seed())
    r, _item = _row(client)
    cc = r.headers.get("Cache-Control", "")
    assert "no-store" in cc and "private" in cc
    assert "public" not in cc


# ── 5. read-only ───────────────────────────────────────────────────────────
def test_endpoint_writes_nothing(db, client):
    db(_seed(state=ob.STATE_REAUTH, fail_count=2, error_code="INVALID_TOKEN",
             last_fail_at=1_754_000_000, next_try_at=0, last_success_at=17))

    async def snapshot():
        conn = await database.get_db()
        r = await (await conn.execute(
            "SELECT token_state, token_fail_count, token_last_error_code,"
            " token_last_fail_at, token_next_try_at, token_last_success_at,"
            " streamer_access_token, streamer_refresh_token,"
            " (SELECT COUNT(*) FROM chzzk_subscriptions) AS n"
            " FROM chzzk_subscriptions WHERE guild_id=?", (GUILD,))).fetchone()
        return dict(r)

    before = db(snapshot())
    _as_owner(client).get("/api/admin/chzzk")
    _as_owner(client).get("/api/admin/chzzk")
    assert db(snapshot()) == before


# ── 6. 기존 응답 호환 ──────────────────────────────────────────────────────
def test_existing_fields_are_preserved(db, client):
    db(_seed())
    _r, item = _row(client)
    for k in ("id", "guild_id", "chzzk_channel_id", "chzzk_name", "chzzk_image_url",
              "discord_channel", "mention_everyone", "is_live",
              "follow_role_1month", "follow_role_3month",
              "follow_months_tier1", "follow_months_tier2", "guild_name"):
        assert k in item, k
