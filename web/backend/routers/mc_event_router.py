import time
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_db
from utils.mc_rcon import rcon_command
from routers.admin_router import _require_owner, _bot_guilds

router = APIRouter(prefix="/api/admin/mc-events", tags=["mc-events"])

DEFAULT_TRIGGER = {"debuff": "디버프지급", "buff": "버프지급", "random": "랜덤아이템"}


# ── 이벤트 ────────────────────────────────────────────────────────────────────

class EventCreate(BaseModel):
    name:             str
    mc_host:          str = ""
    mc_port:          int = 25575
    mc_rcon_password: str = ""


class EventUpdate(BaseModel):
    name:             Optional[str]  = None
    mc_host:          Optional[str]  = None
    mc_port:          Optional[int]  = None
    mc_rcon_password: Optional[str]  = None
    is_active:        Optional[bool] = None


@router.get("")
async def list_events(user: dict = Depends(_require_owner)):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, name, is_active, mc_host, mc_port, mc_rcon_password, created_at FROM mc_events ORDER BY id DESC"
    )).fetchall()
    return [dict(r) for r in rows]


@router.post("")
async def create_event(body: EventCreate, user: dict = Depends(_require_owner)):
    db = await get_db()
    cur = await db.execute(
        """INSERT INTO mc_events(name, mc_host, mc_port, mc_rcon_password, created_at)
           VALUES(?,?,?,?,?)""",
        (body.name.strip(), body.mc_host.strip(), body.mc_port, body.mc_rcon_password, int(time.time()))
    )
    event_id = cur.lastrowid

    # 디버프지급/버프지급/랜덤아이템 3개 명령어를 기본 트리거로 미리 만들어두되,
    # nexadmin에서 트리거 문구를 자유롭게 바꾸거나 비활성화할 수 있다.
    for kind, trigger in DEFAULT_TRIGGER.items():
        await db.execute(
            "INSERT INTO mc_event_commands(event_id, kind, trigger_text) VALUES(?,?,?)",
            (event_id, kind, trigger)
        )
    await db.commit()
    return {"ok": True, "id": event_id}


@router.patch("/{event_id}")
async def update_event(event_id: int, body: EventUpdate, user: dict = Depends(_require_owner)):
    db = await get_db()
    fields, params = [], []
    for col, val in (
        ("name", body.name), ("mc_host", body.mc_host),
        ("mc_port", body.mc_port), ("mc_rcon_password", body.mc_rcon_password),
    ):
        if val is not None:
            fields.append(f"{col}=?")
            params.append(val)
    if body.is_active is not None:
        fields.append("is_active=?")
        params.append(int(body.is_active))

    if fields:
        params.append(event_id)
        await db.execute(f"UPDATE mc_events SET {', '.join(fields)} WHERE id=?", params)

    # 한 번에 하나의 이벤트만 활성화 — 켤 때 나머지는 자동으로 끔
    if body.is_active:
        await db.execute("UPDATE mc_events SET is_active=0 WHERE id != ?", (event_id,))

    await db.commit()
    return {"ok": True}


@router.delete("/{event_id}")
async def delete_event(event_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    await db.execute("DELETE FROM mc_events WHERE id=?", (event_id,))
    await db.execute("DELETE FROM mc_event_guilds WHERE event_id=?", (event_id,))
    await db.execute("DELETE FROM mc_event_items WHERE event_id=?", (event_id,))
    await db.execute("DELETE FROM mc_event_commands WHERE event_id=?", (event_id,))
    await db.commit()
    return {"ok": True}


@router.post("/{event_id}/test")
async def test_connection(event_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    row = await (await db.execute(
        "SELECT mc_host, mc_port, mc_rcon_password FROM mc_events WHERE id=?", (event_id,)
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")
    try:
        response = await rcon_command(row["mc_host"], row["mc_port"], row["mc_rcon_password"], "list")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"연결 실패: {e}")
    return {"ok": True, "response": response}


class RunCommand(BaseModel):
    command: str


@router.post("/{event_id}/run")
async def run_command(event_id: int, body: RunCommand, user: dict = Depends(_require_owner)):
    """아이템/명령어 등록 없이 임의의 마크 명령어를 즉시 실행해보는 테스트용 엔드포인트."""
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="명령어를 입력하세요.")
    db = await get_db()
    row = await (await db.execute(
        "SELECT mc_host, mc_port, mc_rcon_password FROM mc_events WHERE id=?", (event_id,)
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="이벤트를 찾을 수 없습니다.")
    try:
        response = await rcon_command(row["mc_host"], row["mc_port"], row["mc_rcon_password"], command)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"실행 실패: {e}")
    return {"ok": True, "response": response}


# ── 참가 서버 ─────────────────────────────────────────────────────────────────

class GuildAdd(BaseModel):
    guild_id:       str
    mc_player_name: str


class GuildUpdate(BaseModel):
    mc_player_name: Optional[str] = None


@router.get("/{event_id}/guilds")
async def list_event_guilds(event_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT guild_id, mc_player_name FROM mc_event_guilds WHERE event_id=?", (event_id,)
    )).fetchall()
    guilds_list    = await _bot_guilds()
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}
    return [
        {
            "guild_id":       str(r["guild_id"]),
            "mc_player_name": r["mc_player_name"],
            "guild_name":     guild_name_map.get(str(r["guild_id"]), str(r["guild_id"])),
        }
        for r in rows
    ]


@router.post("/{event_id}/guilds")
async def add_event_guild(event_id: int, body: GuildAdd, user: dict = Depends(_require_owner)):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO mc_event_guilds(event_id, guild_id, mc_player_name) VALUES(?,?,?)",
            (event_id, int(body.guild_id), body.mc_player_name.strip())
        )
        await db.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"추가 실패 (이미 등록된 서버일 수 있음): {e}")
    return {"ok": True}


@router.patch("/{event_id}/guilds/{guild_id}")
async def update_event_guild(
    event_id: int, guild_id: int, body: GuildUpdate, user: dict = Depends(_require_owner)
):
    db = await get_db()
    if body.mc_player_name is None:
        return {"ok": True}
    await db.execute(
        "UPDATE mc_event_guilds SET mc_player_name=? WHERE event_id=? AND guild_id=?",
        (body.mc_player_name.strip(), event_id, guild_id)
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{event_id}/guilds/{guild_id}")
async def remove_event_guild(event_id: int, guild_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    result = await db.execute(
        "DELETE FROM mc_event_guilds WHERE event_id=? AND guild_id=?", (event_id, guild_id)
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="참가 서버를 찾을 수 없습니다.")
    return {"ok": True}


# ── 채팅 명령어(트리거) ───────────────────────────────────────────────────────

class CommandCreate(BaseModel):
    kind:         str  # 'debuff' | 'buff' | 'random'
    trigger_text: str


class CommandUpdate(BaseModel):
    trigger_text: Optional[str]  = None
    is_active:    Optional[bool] = None


@router.get("/{event_id}/commands")
async def list_commands(event_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, kind, trigger_text, is_active FROM mc_event_commands WHERE event_id=? ORDER BY id",
        (event_id,)
    )).fetchall()
    return [dict(r) for r in rows]


@router.post("/{event_id}/commands")
async def add_command(event_id: int, body: CommandCreate, user: dict = Depends(_require_owner)):
    if body.kind not in DEFAULT_TRIGGER:
        raise HTTPException(status_code=400, detail="kind는 debuff/buff/random 중 하나여야 합니다.")
    trigger = body.trigger_text.strip() or DEFAULT_TRIGGER[body.kind]
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO mc_event_commands(event_id, kind, trigger_text) VALUES(?,?,?)",
            (event_id, body.kind, trigger)
        )
        await db.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"추가 실패 (이미 등록된 명령어일 수 있음): {e}")
    return {"ok": True}


@router.patch("/{event_id}/commands/{command_id}")
async def update_command(
    event_id: int, command_id: int, body: CommandUpdate, user: dict = Depends(_require_owner)
):
    db = await get_db()
    fields, params = [], []
    if body.trigger_text is not None and body.trigger_text.strip():
        fields.append("trigger_text=?"); params.append(body.trigger_text.strip())
    if body.is_active is not None:
        fields.append("is_active=?"); params.append(int(body.is_active))
    if not fields:
        return {"ok": True}
    params += [event_id, command_id]
    try:
        await db.execute(
            f"UPDATE mc_event_commands SET {', '.join(fields)} WHERE event_id=? AND id=?", params
        )
        await db.commit()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"수정 실패 (이미 등록된 명령어일 수 있음): {e}")
    return {"ok": True}


@router.delete("/{event_id}/commands/{command_id}")
async def delete_command(event_id: int, command_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    result = await db.execute(
        "DELETE FROM mc_event_commands WHERE event_id=? AND id=?", (event_id, command_id)
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="명령어를 찾을 수 없습니다.")
    return {"ok": True}


# ── 아이템 카탈로그 ───────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    item_type:             str  # 'buff' | 'debuff'
    name:                  str
    points_cost:           int
    command_template:      str
    chat_message_template: str = ""
    mc_notify_command:     str = ""
    in_random_pool:        bool = True


class ItemUpdate(BaseModel):
    item_type:             Optional[str]  = None
    name:                  Optional[str]  = None
    points_cost:           Optional[int]  = None
    command_template:      Optional[str]  = None
    chat_message_template: Optional[str]  = None
    mc_notify_command:     Optional[str]  = None
    in_random_pool:        Optional[bool] = None
    is_active:             Optional[bool] = None


@router.get("/{event_id}/items")
async def list_items(event_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM mc_event_items WHERE event_id=? ORDER BY item_type, id", (event_id,)
    )).fetchall()
    return [dict(r) for r in rows]


@router.post("/{event_id}/items")
async def add_item(event_id: int, body: ItemCreate, user: dict = Depends(_require_owner)):
    if body.item_type not in ("buff", "debuff"):
        raise HTTPException(status_code=400, detail="item_type은 buff 또는 debuff여야 합니다.")
    db = await get_db()
    cur = await db.execute(
        """INSERT INTO mc_event_items(
               event_id, item_type, name, points_cost, command_template,
               chat_message_template, mc_notify_command, in_random_pool)
           VALUES(?,?,?,?,?,?,?,?)""",
        (event_id, body.item_type, body.name.strip(), body.points_cost,
         body.command_template.strip(), body.chat_message_template.strip(),
         body.mc_notify_command.strip(), int(body.in_random_pool))
    )
    await db.commit()
    return {"ok": True, "id": cur.lastrowid}


@router.patch("/{event_id}/items/{item_id}")
async def update_item(event_id: int, item_id: int, body: ItemUpdate, user: dict = Depends(_require_owner)):
    db = await get_db()
    fields, params = [], []
    for col, val in (
        ("item_type", body.item_type), ("name", body.name),
        ("points_cost", body.points_cost), ("command_template", body.command_template),
        ("chat_message_template", body.chat_message_template),
        ("mc_notify_command", body.mc_notify_command),
    ):
        if val is not None:
            fields.append(f"{col}=?")
            params.append(val)
    if body.in_random_pool is not None:
        fields.append("in_random_pool=?"); params.append(int(body.in_random_pool))
    if body.is_active is not None:
        fields.append("is_active=?"); params.append(int(body.is_active))
    if not fields:
        return {"ok": True}
    params += [event_id, item_id]
    await db.execute(f"UPDATE mc_event_items SET {', '.join(fields)} WHERE event_id=? AND id=?", params)
    await db.commit()
    return {"ok": True}


@router.delete("/{event_id}/items/{item_id}")
async def delete_item(event_id: int, item_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    await db.execute("DELETE FROM mc_event_items WHERE event_id=? AND id=?", (event_id, item_id))
    await db.commit()
    return {"ok": True}


# ── 구매/적용 로그 ────────────────────────────────────────────────────────────

@router.get("/{event_id}/purchases")
async def list_purchases(event_id: int, user: dict = Depends(_require_owner), limit: int = 100):
    db = await get_db()
    rows = await (await db.execute(
        """SELECT p.*, i.name AS item_name
           FROM mc_event_purchases p
           LEFT JOIN mc_event_items i ON i.id = p.item_id
           WHERE p.event_id=?
           ORDER BY p.id DESC LIMIT ?""",
        (event_id, min(limit, 500))
    )).fetchall()

    guilds_list    = await _bot_guilds()
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}

    return [
        {
            **{k: v for k, v in dict(r).items() if k not in ("guild_id", "user_id", "target_guild_id")},
            "guild_id":        str(r["guild_id"]),
            "user_id":         str(r["user_id"]),
            "target_guild_id": str(r["target_guild_id"]) if r["target_guild_id"] else None,
            "guild_name":      guild_name_map.get(str(r["guild_id"]), str(r["guild_id"])),
            "target_name":     guild_name_map.get(str(r["target_guild_id"]), None) if r["target_guild_id"] else None,
        }
        for r in rows
    ]
