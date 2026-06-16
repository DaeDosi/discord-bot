import os
import time
import asyncio
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from deps import get_current_user, require_guild_admin
from database import get_db

router = APIRouter(prefix="/api/points", tags=["points"])

_BOT_TOKEN   = os.getenv("DISCORD_TOKEN", "")
_DISCORD_API = "https://discord.com/api/v10"


async def _fetch_name(client: httpx.AsyncClient, guild_id: str, user_id: int) -> str:
    try:
        r = await client.get(
            f"{_DISCORD_API}/guilds/{guild_id}/members/{user_id}",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"}, timeout=5
        )
        if r.status_code == 200:
            d = r.json()
            return d.get("nick") or d.get("user", {}).get("global_name") or d.get("user", {}).get("username") or str(user_id)
    except Exception:
        pass
    return str(user_id)


# ── 리더보드 ──────────────────────────────────────────────────────────────────

@router.get("/{guild_id}/leaderboard")
async def get_points_leaderboard(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT user_id, points FROM user_points WHERE guild_id=? ORDER BY points DESC LIMIT 20",
        (int(guild_id),)
    )).fetchall()
    async with httpx.AsyncClient() as client:
        names = await asyncio.gather(*[_fetch_name(client, guild_id, r["user_id"]) for r in rows])
    return [{"user_id": str(r["user_id"]), "display_name": n, "points": r["points"]}
            for r, n in zip(rows, names)]


# ── 포인트 수동 조정 ─────────────────────────────────────────────────────────

class PointsAdjust(BaseModel):
    user_id: str
    amount:  int
    reason:  Optional[str] = ""


@router.post("/{guild_id}/adjust")
async def adjust_points(
    guild_id: str,
    body: PointsAdjust,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,MAX(0,?))
           ON CONFLICT(guild_id, user_id) DO UPDATE SET points=MAX(0, points + ?)""",
        (int(guild_id), int(body.user_id), max(0, body.amount), body.amount)
    )
    await db.commit()
    row = await (await db.execute(
        "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
        (int(guild_id), int(body.user_id))
    )).fetchone()
    return {"ok": True, "points": row["points"] if row else 0}


# ── 미션 CRUD ────────────────────────────────────────────────────────────────

class MissionCreate(BaseModel):
    title:       str
    description: Optional[str] = ""
    points:      int = 0
    is_active:   bool = True


@router.get("/{guild_id}/missions")
async def list_missions(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, title, description, points, is_active, created_at FROM missions WHERE guild_id=? ORDER BY id DESC",
        (int(guild_id),)
    )).fetchall()
    return [dict(r) for r in rows]


@router.post("/{guild_id}/missions")
async def create_mission(
    guild_id: str,
    body: MissionCreate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO missions(guild_id, title, description, points, is_active, created_at) VALUES(?,?,?,?,?,?)",
        (int(guild_id), body.title, body.description or "", body.points, int(body.is_active), int(time.time()))
    )
    await db.commit()
    return {"ok": True, "id": cur.lastrowid}


@router.put("/{guild_id}/missions/{mission_id}")
async def update_mission(
    guild_id: str,
    mission_id: int,
    body: MissionCreate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        "UPDATE missions SET title=?, description=?, points=?, is_active=? WHERE id=? AND guild_id=?",
        (body.title, body.description or "", body.points, int(body.is_active), mission_id, int(guild_id))
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{guild_id}/missions/{mission_id}")
async def delete_mission(
    guild_id: str,
    mission_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute("DELETE FROM missions WHERE id=? AND guild_id=?", (mission_id, int(guild_id)))
    await db.execute("DELETE FROM mission_completions WHERE mission_id=? AND guild_id=?", (mission_id, int(guild_id)))
    await db.commit()
    return {"ok": True}


# ── 미션 제출 관리 ────────────────────────────────────────────────────────────

@router.get("/{guild_id}/submissions")
async def list_submissions(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        """SELECT mc.id, mc.mission_id, mc.user_id, mc.status, mc.submitted_at,
                  m.title, m.points
           FROM mission_completions mc
           JOIN missions m ON m.id = mc.mission_id
           WHERE mc.guild_id=?
           ORDER BY mc.submitted_at DESC LIMIT 100""",
        (int(guild_id),)
    )).fetchall()
    async with httpx.AsyncClient() as client:
        names = await asyncio.gather(*[_fetch_name(client, guild_id, r["user_id"]) for r in rows])
    return [
        {**dict(r), "user_id": str(r["user_id"]), "user_name": n}
        for r, n in zip(rows, names)
    ]


@router.post("/{guild_id}/submissions/{submission_id}/approve")
async def approve_submission(
    guild_id: str,
    submission_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        """SELECT mc.user_id, mc.status, m.points
           FROM mission_completions mc
           JOIN missions m ON m.id = mc.mission_id
           WHERE mc.id=? AND mc.guild_id=?""",
        (submission_id, int(guild_id))
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="제출을 찾을 수 없습니다.")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail="이미 처리된 제출입니다.")
    await db.execute(
        "UPDATE mission_completions SET status='approved', reviewed_at=? WHERE id=?",
        (int(time.time()), submission_id)
    )
    await db.execute(
        """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,?)
           ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points + ?""",
        (int(guild_id), row["user_id"], row["points"], row["points"])
    )
    await db.commit()
    return {"ok": True, "points_awarded": row["points"]}


@router.post("/{guild_id}/submissions/{submission_id}/reject")
async def reject_submission(
    guild_id: str,
    submission_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        "SELECT status FROM mission_completions WHERE id=? AND guild_id=?",
        (submission_id, int(guild_id))
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="제출을 찾을 수 없습니다.")
    if row["status"] != "pending":
        raise HTTPException(status_code=400, detail="이미 처리된 제출입니다.")
    await db.execute(
        "UPDATE mission_completions SET status='rejected', reviewed_at=? WHERE id=?",
        (int(time.time()), submission_id)
    )
    await db.commit()
    return {"ok": True}


# ── 상점 아이템 CRUD ──────────────────────────────────────────────────────────

class ShopItemCreate(BaseModel):
    name:        str
    description: Optional[str] = ""
    image_url:   Optional[str] = ""
    points_cost: int = 0
    stock:       int = -1  # -1 = 무제한


@router.get("/{guild_id}/shop/items")
async def list_shop_items(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, name, description, image_url, points_cost, stock, is_active, created_at FROM shop_items WHERE guild_id=? ORDER BY id DESC",
        (int(guild_id),)
    )).fetchall()
    return [dict(r) for r in rows]


@router.post("/{guild_id}/shop/items")
async def create_shop_item(
    guild_id: str,
    body: ShopItemCreate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO shop_items(guild_id, name, description, image_url, points_cost, stock, is_active, created_at) VALUES(?,?,?,?,?,?,1,?)",
        (int(guild_id), body.name, body.description or "", body.image_url or "", body.points_cost, body.stock, int(time.time()))
    )
    await db.commit()
    return {"ok": True, "id": cur.lastrowid}


@router.put("/{guild_id}/shop/items/{item_id}")
async def update_shop_item(
    guild_id: str,
    item_id: int,
    body: ShopItemCreate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        "UPDATE shop_items SET name=?, description=?, image_url=?, points_cost=?, stock=? WHERE id=? AND guild_id=?",
        (body.name, body.description or "", body.image_url or "", body.points_cost, body.stock, item_id, int(guild_id))
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{guild_id}/shop/items/{item_id}")
async def delete_shop_item(
    guild_id: str,
    item_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute("DELETE FROM shop_items WHERE id=? AND guild_id=?", (item_id, int(guild_id)))
    await db.commit()
    return {"ok": True}


# ── 상점 교환 내역 ────────────────────────────────────────────────────────────

@router.get("/{guild_id}/shop/exchanges")
async def list_shop_exchanges(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        """SELECT se.id, se.user_id, se.item_id, se.exchanged_at, se.is_used, se.used_at,
                  si.name AS item_name, si.points_cost, si.image_url
           FROM shop_exchanges se
           JOIN shop_items si ON si.id = se.item_id
           WHERE se.guild_id=?
           ORDER BY se.exchanged_at DESC LIMIT 200""",
        (int(guild_id),)
    )).fetchall()
    async with httpx.AsyncClient() as client:
        names = await asyncio.gather(*[_fetch_name(client, guild_id, r["user_id"]) for r in rows])
    return [
        {**dict(r), "user_id": str(r["user_id"]), "user_name": n}
        for r, n in zip(rows, names)
    ]


# ── 포인트 도박 설정 ──────────────────────────────────────────────────────────

class GamblingConfigSave(BaseModel):
    title:      str = "포인트 도박"
    duration:   int = 60
    bet_amount: int = 100
    options:    list[str] = []


@router.get("/{guild_id}/gambling")
async def get_gambling_config(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    cfg = await (await db.execute(
        "SELECT title, duration, bet_amount FROM points_gambling_config WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone()
    opts = await (await db.execute(
        "SELECT content FROM points_gambling_options WHERE guild_id=? ORDER BY opt_index",
        (int(guild_id),)
    )).fetchall()
    return {
        "title":      cfg["title"]      if cfg else "포인트 도박",
        "duration":   cfg["duration"]   if cfg else 60,
        "bet_amount": cfg["bet_amount"] if cfg else 100,
        "options":    [r["content"] for r in opts],
    }


@router.put("/{guild_id}/gambling")
async def save_gambling_config(
    guild_id: str,
    body: GamblingConfigSave,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    options = [o.strip() for o in body.options if o.strip()]
    if len(options) < 2:
        raise HTTPException(status_code=400, detail="옵션은 최소 2개 이상 필요합니다.")
    if len(options) > 5:
        raise HTTPException(status_code=400, detail="옵션은 최대 5개까지 가능합니다.")

    db = await get_db()
    await db.execute(
        """INSERT INTO points_gambling_config(guild_id, title, duration, bet_amount)
           VALUES(?,?,?,?)
           ON CONFLICT(guild_id) DO UPDATE SET
               title=excluded.title,
               duration=excluded.duration,
               bet_amount=excluded.bet_amount""",
        (int(guild_id), body.title or "포인트 도박", max(10, body.duration), max(1, body.bet_amount))
    )
    await db.execute(
        "DELETE FROM points_gambling_options WHERE guild_id=?", (int(guild_id),)
    )
    for i, content in enumerate(options):
        await db.execute(
            "INSERT INTO points_gambling_options(guild_id, opt_index, content) VALUES(?,?,?)",
            (int(guild_id), i, content)
        )
    await db.commit()
    return {"ok": True}


@router.post("/{guild_id}/shop/exchanges/{exchange_id}/use")
async def mark_exchange_used(
    guild_id: str,
    exchange_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        "SELECT id FROM shop_exchanges WHERE id=? AND guild_id=?",
        (exchange_id, int(guild_id))
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="교환 내역을 찾을 수 없습니다.")
    await db.execute(
        "UPDATE shop_exchanges SET is_used=1, used_at=? WHERE id=?",
        (int(time.time()), exchange_id)
    )
    await db.commit()
    return {"ok": True}
