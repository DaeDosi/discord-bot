import time
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_db
from utils.mc_rcon import rcon_command
from routers.admin_router import _require_owner, _bot_guilds

router = APIRouter(prefix="/api/admin/mc-events", tags=["mc-events"])


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
    await db.commit()
    return {"ok": True, "id": cur.lastrowid}


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


# ── 참가 서버 ─────────────────────────────────────────────────────────────────

class GuildAdd(BaseModel):
    guild_id:       str
    team_name:      str
    mc_player_name: str


class GuildUpdate(BaseModel):
    team_name:      Optional[str] = None
    mc_player_name: Optional[str] = None


@router.get("/{event_id}/guilds")
async def list_event_guilds(event_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT guild_id, team_name, mc_player_name FROM mc_event_guilds WHERE event_id=?", (event_id,)
    )).fetchall()
    guilds_list    = await _bot_guilds()
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}
    return [
        {**dict(r), "guild_name": guild_name_map.get(str(r["guild_id"]), str(r["guild_id"]))}
        for r in rows
    ]


@router.post("/{event_id}/guilds")
async def add_event_guild(event_id: int, body: GuildAdd, user: dict = Depends(_require_owner)):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO mc_event_guilds(event_id, guild_id, team_name, mc_player_name) VALUES(?,?,?,?)",
            (event_id, int(body.guild_id), body.team_name.strip(), body.mc_player_name.strip())
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
    fields, params = [], []
    if body.team_name is not None:
        fields.append("team_name=?"); params.append(body.team_name.strip())
    if body.mc_player_name is not None:
        fields.append("mc_player_name=?"); params.append(body.mc_player_name.strip())
    if not fields:
        return {"ok": True}
    params += [event_id, guild_id]
    await db.execute(f"UPDATE mc_event_guilds SET {', '.join(fields)} WHERE event_id=? AND guild_id=?", params)
    await db.commit()
    return {"ok": True}


@router.delete("/{event_id}/guilds/{guild_id}")
async def remove_event_guild(event_id: int, guild_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    await db.execute("DELETE FROM mc_event_guilds WHERE event_id=? AND guild_id=?", (event_id, guild_id))
    await db.commit()
    return {"ok": True}


# ── 아이템 카탈로그 ───────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    item_type:        str  # 'buff' | 'debuff'
    name:             str
    points_cost:      int
    command_template: str
    in_random_pool:   bool = True


class ItemUpdate(BaseModel):
    item_type:        Optional[str]  = None
    name:             Optional[str]  = None
    points_cost:      Optional[int]  = None
    command_template: Optional[str]  = None
    in_random_pool:   Optional[bool] = None
    is_active:        Optional[bool] = None


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
        """INSERT INTO mc_event_items(event_id, item_type, name, points_cost, command_template, in_random_pool)
           VALUES(?,?,?,?,?,?)""",
        (event_id, body.item_type, body.name.strip(), body.points_cost,
         body.command_template.strip(), int(body.in_random_pool))
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
            **dict(r),
            "guild_name":  guild_name_map.get(str(r["guild_id"]), str(r["guild_id"])),
            "target_name": guild_name_map.get(str(r["target_guild_id"]), None) if r["target_guild_id"] else None,
        }
        for r in rows
    ]
