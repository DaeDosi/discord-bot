"""MC(마크 콜라보) 이벤트 제거 계약.

UI만 지우면 치지직 채팅 경로에서 기능이 계속 돌고 **포인트가 차감된다**. 그래서
여기서 고정하는 것은 "화면에 안 보인다"가 아니라 **실행되지 않는다**이다.

- 운영 DB에는 이미 활성 이벤트/트리거/아이템 행이 남아 있을 수 있다. 그 상태에서
  옛 트리거가 채팅으로 들어와도 포인트가 줄지 않고 구매 기록도 생기지 않아야 한다.
- **테이블과 데이터는 지우지 않는다**(rollback은 `git revert`만으로 가능해야 한다).
  그래서 이 테스트는 행이 그대로 남아 있는 것도 함께 확인한다.
- 기존 포인트 기능(`!포인트`)은 손상되지 않아야 한다.
"""
import time

import pytest

import database

GUILD = 886237674665549865
USER = 123456789012345678
CHZZK_CHANNEL = "ch_test"
CHZZK_VIEWER = "viewer_test"
TRIGGER = "버프지급"
COST = 100
START_POINTS = 500


@pytest.fixture(autouse=True)
def _clean(db):
    async def wipe():
        conn = await database.get_db()
        for t in ("chzzk_subscriptions", "chzzk_chat_commands", "chzzk_chat_test_queue",
                  "chzzk_chat_log", "chzzk_verifications", "user_points",
                  "mc_events", "mc_event_guilds", "mc_event_items",
                  "mc_event_commands", "mc_event_purchases"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.commit()
    db(wipe())
    yield
    db(wipe())


async def _seed_active_mc_event():
    """제거 전이라면 !버프지급 한 번으로 COST만큼 포인트가 빠지던 상태 그대로 심는다."""
    conn = await database.get_db()
    now = int(time.time())
    await conn.execute(
        "INSERT INTO chzzk_subscriptions (guild_id, discord_channel, chzzk_channel_id,"
        " chzzk_name, chat_enabled) VALUES (?,?,?,?,1)",
        (GUILD, "1", CHZZK_CHANNEL, "테스트채널"),
    )
    await conn.execute(
        "INSERT INTO chzzk_verifications (guild_id, user_id, verified_at, chzzk_channel_id)"
        " VALUES (?,?,?,?)",
        (GUILD, USER, float(now), CHZZK_VIEWER),
    )
    await conn.execute(
        "INSERT INTO user_points (guild_id, user_id, points) VALUES (?,?,?)",
        (GUILD, USER, START_POINTS),
    )
    cur = await conn.execute(
        "INSERT INTO mc_events (name, is_active, mc_host, mc_port, mc_rcon_password, created_at)"
        " VALUES (?,1,?,?,?,?)",
        ("합방 이벤트", "127.0.0.1", 25575, "pw", now),
    )
    event_id = cur.lastrowid
    await conn.execute(
        "INSERT INTO mc_event_guilds (event_id, guild_id, mc_player_name) VALUES (?,?,?)",
        (event_id, GUILD, "플레이어"),
    )
    await conn.execute(
        "INSERT INTO mc_event_commands (event_id, kind, trigger_text, is_active) VALUES (?,?,?,1)",
        (event_id, "buff", TRIGGER),
    )
    await conn.execute(
        "INSERT INTO mc_event_items (event_id, item_type, name, points_cost, command_template,"
        " in_random_pool, is_active) VALUES (?,?,?,?,?,1,1)",
        (event_id, "buff", "회복 물약", COST, "give {player} potion"),
    )
    await conn.commit()
    return event_id


async def _points() -> int:
    conn = await database.get_db()
    row = await (await conn.execute(
        "SELECT points FROM user_points WHERE guild_id=? AND user_id=?", (GUILD, USER)
    )).fetchone()
    return row["points"] if row else 0


def _cog():
    """루프를 띄우지 않고 채팅 처리 경로만 쓰기 위한 최소 인스턴스."""
    from cogs.chzzk_chat import ChzzkChatCog

    cog = ChzzkChatCog.__new__(ChzzkChatCog)
    cog.bot = None
    cog._channels = {}
    cog._checkin_duplicate_notice_at = {}
    return cog


def test_mc_event_trigger_does_not_spend_points(db):
    """활성 이벤트 행이 그대로 남아 있어도 옛 트리거는 아무 일도 하지 않는다."""
    db(_seed_active_mc_event())
    cog = _cog()

    db(cog._handle_test_message(GUILD, "시청자", CHZZK_VIEWER, f"!{TRIGGER}"))

    assert db(_points()) == START_POINTS


def test_mc_event_trigger_writes_no_purchase(db):
    db(_seed_active_mc_event())
    cog = _cog()

    db(cog._handle_test_message(GUILD, "시청자", CHZZK_VIEWER, f"!{TRIGGER}"))

    async def count():
        conn = await database.get_db()
        row = await (await conn.execute("SELECT COUNT(*) AS n FROM mc_event_purchases")).fetchone()
        return row["n"]

    assert db(count()) == 0


def test_mc_event_trigger_sends_no_chat_reply(db):
    """제거된 명령은 조용히 무시된다 — 예외/500도, 구매 안내 문구도 나가지 않는다."""
    db(_seed_active_mc_event())
    cog = _cog()

    db(cog._handle_test_message(GUILD, "시청자", CHZZK_VIEWER, f"!{TRIGGER}"))

    async def out_lines():
        conn = await database.get_db()
        rows = await (await conn.execute(
            "SELECT content FROM chzzk_chat_log WHERE guild_id=? AND direction='out'", (GUILD,)
        )).fetchall()
        return [r["content"] for r in rows]

    assert db(out_lines()) == []


def test_existing_point_command_still_works(db):
    """제거가 다른 포인트 기능을 건드리지 않았는지 — !포인트는 그대로 응답한다."""
    db(_seed_active_mc_event())
    cog = _cog()

    db(cog._handle_test_message(GUILD, "시청자", CHZZK_VIEWER, "!포인트"))

    async def out_lines():
        conn = await database.get_db()
        rows = await (await conn.execute(
            "SELECT content FROM chzzk_chat_log WHERE guild_id=? AND direction='out'", (GUILD,)
        )).fetchall()
        return [r["content"] for r in rows]

    lines = db(out_lines())
    assert lines and str(START_POINTS) in lines[0]
    assert db(_points()) == START_POINTS


def test_mc_event_rows_are_preserved(db):
    """데이터 삭제 금지 계약 — 제거는 코드에서만 일어난다."""
    db(_seed_active_mc_event())
    cog = _cog()

    db(cog._handle_test_message(GUILD, "시청자", CHZZK_VIEWER, f"!{TRIGGER}"))

    async def counts():
        conn = await database.get_db()
        out = {}
        for t in ("mc_events", "mc_event_guilds", "mc_event_commands", "mc_event_items"):
            row = await (await conn.execute(f"SELECT COUNT(*) AS n FROM {t}")).fetchone()
            out[t] = row["n"]
        return out

    assert db(counts()) == {
        "mc_events": 1, "mc_event_guilds": 1, "mc_event_commands": 1, "mc_event_items": 1
    }


def test_chat_cog_has_no_mc_event_entry_points():
    """숨은 우회 진입점이 남지 않았는지 — 심볼과 RCON import 자체가 없어야 한다."""
    import cogs.chzzk_chat as chat

    for name in ("_handle_mc_event", "_load_mc_event", "_pick_item", "_pick_random_other_guild"):
        assert not hasattr(chat.ChzzkChatCog, name), name
    assert not hasattr(chat, "rcon_command")
