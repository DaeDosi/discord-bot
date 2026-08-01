"""스노플레이크는 API 경계에서 문자열이어야 한다.

실측(2026-08-01): `/api/admin/guilds`가 guild_id를 JSON number로 내보내자 브라우저에서
정밀도가 깎여(886237674665549865 → …549800) 관리자 패널의 서버-구독 조인이 전부
실패했다(모든 서버가 "연결 안 됨"). 같은 결함이 `discord_channel`에도 있었다 —
그쪽은 손상된 채널 id가 재연동 URL과 설정 저장 요청에 실려 나갈 수 있었다.

여기서 고정하는 것: **응답 본문에 19자리 정수가 JSON number로 등장하지 않는다.**
문자열이면 브라우저가 무엇을 하든 원본이 보존된다.
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
from utils.ids import snowflake_str

GUILD = 886237674665549865
CHANNEL = 1234567890123456789          # 2^53 초과 — float 왕복 시 …768로 깎인다
ROLE = 1234567890123456999


def test_precision_loss_is_real():
    """이 테스트들이 막으려는 현상 자체를 먼저 고정한다."""
    assert int(float(CHANNEL)) != CHANNEL
    assert json.loads(json.dumps(CHANNEL), parse_int=float) != CHANNEL
    # 문자열이면 무슨 짓을 해도 보존된다
    assert json.loads(json.dumps(str(CHANNEL))) == str(CHANNEL)


def test_snowflake_str_normalizes():
    assert snowflake_str(CHANNEL) == "1234567890123456789"
    assert snowflake_str("1234567890123456789") == "1234567890123456789"
    assert snowflake_str(None) is None
    assert snowflake_str(0) is None          # 0 = 미설정
    assert snowflake_str("") is None


# ── API 경계 ───────────────────────────────────────────────────────────────
@pytest.fixture
def client(db, monkeypatch):
    import routers.admin_router as ar
    import routers.chzzk_router as cr
    from deps import get_current_user, require_guild_admin

    monkeypatch.setattr(ar, "_OWNER_ID", "1")

    async def _fake_guilds(force: bool = False):
        return [{"id": str(GUILD), "name": "만화소녀의 사탕월드", "icon": None}]

    monkeypatch.setattr(ar, "_bot_guilds", _fake_guilds)

    app = FastAPI()
    app.include_router(cr.router)
    app.include_router(ar.router)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "1"}
    app.dependency_overrides[require_guild_admin] = lambda: None
    return TestClient(app)


@pytest.fixture(autouse=True)
def _seeded(db):
    async def seed():
        conn = await database.get_db()
        await conn.execute("DELETE FROM chzzk_subscriptions")
        await conn.execute(
            "INSERT INTO chzzk_subscriptions (guild_id, discord_channel,"
            " chzzk_channel_id, chzzk_name, mention_role_id, follow_role_1month,"
            " streamer_access_token, streamer_refresh_token, chat_enabled)"
            " VALUES (?,?,?,?,?,?,?,?,1)",
            (GUILD, CHANNEL, "ch1", "만화소녀", ROLE, ROLE, "AT", "RT"))
        await conn.commit()
    db(seed())
    yield
    db(_wipe())


async def _wipe():
    conn = await database.get_db()
    await conn.execute("DELETE FROM chzzk_subscriptions")
    await conn.commit()


def _assert_no_big_numbers(raw: str, label: str):
    """응답 본문에 19자리 이상 정수가 **따옴표 없이** 있으면 안 된다."""
    import re
    bare = re.findall(r'(?<!")\b\d{17,}\b(?!")', raw)
    assert not bare, f"{label}: JSON number로 나간 스노플레이크 {bare}"


def test_subscriptions_returns_string_ids(db, client):
    r = client.get(f"/api/chzzk/{GUILD}/subscriptions")
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert row["discord_channel"] == "1234567890123456789"
    assert row["mention_role_id"] == str(ROLE)
    assert row["follow_role_1month"] == str(ROLE)
    _assert_no_big_numbers(r.text, "subscriptions")


def test_admin_chzzk_returns_string_ids(db, client):
    r = client.get("/api/admin/chzzk")
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert row["guild_id"] == "886237674665549865"
    assert row["discord_channel"] == "1234567890123456789"
    _assert_no_big_numbers(r.text, "admin/chzzk")


def test_admin_guilds_returns_string_ids(db, client):
    r = client.get("/api/admin/guilds")
    assert r.status_code == 200, r.text
    assert r.json()[0]["id"] == "886237674665549865"
    _assert_no_big_numbers(r.text, "admin/guilds")


def test_chat_status_has_no_bare_snowflakes(db, client):
    r = client.get(f"/api/chzzk/{GUILD}/chat-status")
    assert r.status_code == 200, r.text
    _assert_no_big_numbers(r.text, "chat-status")


def test_round_trip_through_json_parser(db, client):
    """브라우저가 하는 그대로 — 정수를 float로 파싱해도 값이 살아남아야 한다."""
    raw = client.get(f"/api/chzzk/{GUILD}/subscriptions").text
    parsed = json.loads(raw, parse_int=float)[0]
    assert parsed["discord_channel"] == "1234567890123456789"
    parsed_admin = json.loads(client.get("/api/admin/chzzk").text, parse_int=float)[0]
    assert parsed_admin["guild_id"] == "886237674665549865"


# ── 캐시 정책 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [
    f"/api/chzzk/{GUILD}/chat-status",
    "/api/admin/chzzk",
    "/api/admin/guilds",
])
def test_auth_state_responses_are_not_cacheable(db, client, path):
    cc = client.get(path).headers.get("Cache-Control", "")
    assert "no-store" in cc and "private" in cc, f"{path}: {cc!r}"
    assert "public" not in cc


def test_admin_follow_stats_returns_string_ids(db, client):
    """지금은 조인에 안 쓰여도 문자열로 낸다 — 나중에 쓰이는 순간 조용히 깨진다.
    실제로 nexadmin의 서버 나가기 필터가 String(number)로 비교하고 있었다."""
    async def seed_verif():
        conn = await database.get_db()
        await conn.execute("DELETE FROM chzzk_verifications")
        await conn.execute(
            "INSERT INTO chzzk_verifications (guild_id, user_id, chzzk_channel_id,"
            " tier_months, verified_at) VALUES (?,?,?,?,?)",
            (GUILD, CHANNEL, "ch1", 1, 0))
        await conn.commit()

    db(seed_verif())
    r = client.get("/api/admin/follow-stats")
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert row["guild_id"] == "886237674665549865"
    assert row["users"][0]["user_id"] == "1234567890123456789"
    _assert_no_big_numbers(r.text, "admin/follow-stats")


def test_admin_verifications_returns_string_ids(db, client):
    async def seed_verif():
        conn = await database.get_db()
        await conn.execute("DELETE FROM chzzk_verifications")
        await conn.execute(
            "INSERT INTO chzzk_verifications (guild_id, user_id, chzzk_channel_id,"
            " tier_months, verified_at) VALUES (?,?,?,?,?)",
            (GUILD, CHANNEL, "ch1", 1, 0))
        await conn.commit()

    db(seed_verif())
    r = client.get("/api/admin/verifications")
    assert r.status_code == 200, r.text
    _assert_no_big_numbers(r.text, "admin/verifications")
