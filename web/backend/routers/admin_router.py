import os
import asyncio
import httpx
from datetime import date
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional
from deps import get_current_user
from database import get_db

router = APIRouter(prefix="/api/admin", tags=["admin"])

_OWNER_ID  = os.getenv("OWNER_ID", "")
_BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
_DISCORD   = "https://discord.com/api/v10"
_CHZZK_API = "https://api.chzzk.naver.com"
_CHZZK_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


# ── 오너 전용 권한 검증 ────────────────────────────────────────────────────────

async def _require_owner(user: dict = Depends(get_current_user)) -> dict:
    if not _OWNER_ID or user.get("sub") != str(_OWNER_ID):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
    return user


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

async def _bot_guilds() -> list[dict]:
    guilds, after = [], None
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            params: dict = {"limit": 200}
            if after:
                params["after"] = after
            resp = await client.get(
                f"{_DISCORD}/users/@me/guilds",
                headers={"Authorization": f"Bot {_BOT_TOKEN}"},
                params=params,
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch:
                break
            guilds.extend(batch)
            if len(batch) < 200:
                break
            after = batch[-1]["id"]
    return guilds


async def _guild_member_count(client: httpx.AsyncClient, guild_id: str) -> int:
    try:
        resp = await client.get(
            f"{_DISCORD}/guilds/{guild_id}",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"},
            params={"with_counts": "true"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("approximate_member_count", 0)
    except Exception:
        pass
    return 0


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/overview")
async def overview(user: dict = Depends(_require_owner)):
    db          = await get_db()
    guilds_list = await _bot_guilds()
    guild_count = len(guilds_list)

    # 상위 30개 서버 멤버 수 병렬 조회
    async with httpx.AsyncClient() as client:
        counts = await asyncio.gather(*[
            _guild_member_count(client, g["id"])
            for g in guilds_list[:30]
        ])
    total_users = sum(counts)

    chzzk_count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_subscriptions"
    )).fetchone())[0]

    verify_count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_verifications"
    )).fetchone())[0]

    today = date.today().isoformat()
    tv_row = await (await db.execute(
        "SELECT COUNT(*) FROM daily_visitors WHERE date=?", (today,)
    )).fetchone()
    today_visitors = tv_row[0] if tv_row else 0

    return {
        "guild_count":    guild_count,
        "total_users":    total_users,
        "chzzk_subs":     chzzk_count,
        "verifications":  verify_count,
        "today_visitors": today_visitors,
    }


@router.get("/guilds")
async def guilds(user: dict = Depends(_require_owner)):
    db          = await get_db()
    guilds_list = await _bot_guilds()

    chzzk_rows = await (await db.execute(
        "SELECT guild_id, chzzk_name FROM chzzk_subscriptions"
    )).fetchall()
    chzzk_map = {str(r["guild_id"]): r["chzzk_name"] for r in chzzk_rows}

    return [
        {
            "id":         g["id"],
            "name":       g["name"],
            "icon":       g.get("icon"),
            "chzzk_name": chzzk_map.get(g["id"]),
        }
        for g in guilds_list
    ]


@router.get("/chzzk")
async def chzzk_all(user: dict = Depends(_require_owner)):
    db          = await get_db()
    rows        = await (await db.execute(
        """SELECT id, guild_id, chzzk_channel_id, chzzk_name, chzzk_image_url,
                  discord_channel, mention_everyone, is_live,
                  follow_role_1month, follow_role_3month
           FROM chzzk_subscriptions ORDER BY guild_id"""
    )).fetchall()

    guilds_list    = await _bot_guilds()
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}

    return [
        {**dict(r), "guild_name": guild_name_map.get(str(r["guild_id"]), str(r["guild_id"]))}
        for r in rows
    ]


async def _fetch_member_name(client: httpx.AsyncClient, guild_id: str, user_id: str) -> str:
    try:
        resp = await client.get(
            f"{_DISCORD}/guilds/{guild_id}/members/{user_id}",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            return (
                data.get("nick")
                or data.get("user", {}).get("global_name")
                or data.get("user", {}).get("username")
                or user_id
            )
    except Exception:
        pass
    return user_id


@router.get("/verifications")
async def verifications(
    user: dict = Depends(_require_owner),
    limit: int = Query(100, le=500),
):
    db   = await get_db()
    rows = await (await db.execute(
        """SELECT guild_id, user_id, tier_months, verified_at
           FROM chzzk_verifications
           ORDER BY verified_at DESC LIMIT ?""",
        (limit,)
    )).fetchall()

    guilds_list    = await _bot_guilds()
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}

    async with httpx.AsyncClient() as client:
        user_names = await asyncio.gather(*[
            _fetch_member_name(client, str(r["guild_id"]), str(r["user_id"]))
            for r in rows
        ])

    return [
        {
            **dict(r),
            "guild_name": guild_name_map.get(str(r["guild_id"]), str(r["guild_id"])),
            "user_name":  name,
        }
        for r, name in zip(rows, user_names)
    ]


@router.get("/chzzk/search")
async def admin_search_chzzk(keyword: str, user: dict = Depends(_require_owner)):
    url = f"{_CHZZK_API}/service/v1/search/channels"
    async with httpx.AsyncClient(headers=_CHZZK_HDR, timeout=10) as client:
        resp = await client.get(url, params={"keyword": keyword, "offset": 0, "size": 10})
        if resp.status_code != 200:
            return []
        data = resp.json().get("content", {}).get("data", [])
    return [
        {
            "channelId":       d.get("channel", {}).get("channelId"),
            "channelName":     d.get("channel", {}).get("channelName"),
            "channelImageUrl": d.get("channel", {}).get("channelImageUrl"),
            "followerCount":   d.get("channel", {}).get("followerCount", 0),
            "openLive":        d.get("channel", {}).get("openLive", False),
        }
        for d in data
        if d.get("channel", {}).get("channelId")
    ]


class AdminSubCreate(BaseModel):
    guild_id:         str
    discord_channel:  str
    chzzk_channel_id: str
    chzzk_name:       str
    chzzk_image_url:  Optional[str] = None
    mention_everyone: bool = False


@router.post("/chzzk")
async def admin_add_chzzk(body: AdminSubCreate, user: dict = Depends(_require_owner)):
    db = await get_db()
    count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_subscriptions WHERE guild_id=?",
        (int(body.guild_id),)
    )).fetchone())[0]
    if count >= 1:
        raise HTTPException(status_code=400, detail="이미 구독 중입니다. 기존 구독을 먼저 삭제하세요.")
    try:
        await db.execute(
            """INSERT INTO chzzk_subscriptions
               (guild_id, discord_channel, chzzk_channel_id, chzzk_name, chzzk_image_url, mention_everyone, is_live)
               VALUES (?,?,?,?,?,?,0)""",
            (
                int(body.guild_id), int(body.discord_channel),
                body.chzzk_channel_id, body.chzzk_name,
                body.chzzk_image_url, int(body.mention_everyone),
            ),
        )
        await db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@router.delete("/chzzk/{sub_id}")
async def admin_delete_chzzk(sub_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    await db.execute("DELETE FROM chzzk_subscriptions WHERE id=?", (sub_id,))
    await db.commit()
    return {"ok": True}
