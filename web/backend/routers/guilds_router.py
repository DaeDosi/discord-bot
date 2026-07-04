import os
import httpx
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Depends
from deps import get_current_user, require_guild_admin
from auth import get_discord_guilds

router = APIRouter(prefix="/api/guilds", tags=["guilds"])

DISCORD_API = "https://discord.com/api/v10"
BOT_TOKEN   = os.getenv("DISCORD_TOKEN", "")
BOT_HEADERS = {"Authorization": f"Bot {BOT_TOKEN}"}

# 관리자 권한 플래그 (MANAGE_GUILD)
MANAGE_GUILD = 0x20


def has_manage_guild(permissions: int) -> bool:
    return bool(permissions & MANAGE_GUILD) or bool(permissions & 0x8)  # 0x8 = ADMINISTRATOR


async def get_bot_guilds() -> set[str]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DISCORD_API}/users/@me/guilds", headers=BOT_HEADERS)
        if resp.status_code != 200:
            return set()
        return {g["id"] for g in resp.json()}


@router.get("")
async def list_guilds(user: dict = Depends(get_current_user)):
    user_guilds = await get_discord_guilds(user["access_token"])
    bot_guilds  = await get_bot_guilds()

    admin_guilds = [
        {
            "id":   g["id"],
            "name": g["name"],
            "icon": (
                f"https://cdn.discordapp.com/icons/{g['id']}/{g['icon']}.png"
                if g.get("icon") else None
            ),
            "has_bot": g["id"] in bot_guilds,
        }
        for g in user_guilds
        if g.get("owner") or has_manage_guild(int(g.get("permissions") or 0))
    ]
    return admin_guilds


@router.get("/{guild_id}/channels")
async def get_channels(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="봇 토큰이 설정되지 않았습니다.")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}/channels", headers=BOT_HEADERS
        )
        if resp.status_code == 403:
            raise HTTPException(status_code=403, detail="봇이 해당 서버에 없거나 권한이 없습니다.")
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Discord API 오류: {resp.text}")
        channels = resp.json()
    # 카테고리(4), 텍스트(0), 음성(2), 공지(5), 포럼(15) 포함
    ALLOWED_TYPES = {0, 2, 4, 5, 15}
    return [
        {"id": c["id"], "name": c["name"], "type": c["type"], "position": c.get("position", 0)}
        for c in sorted(channels, key=lambda c: c.get("position", 0))
        if c["type"] in ALLOWED_TYPES
    ]


@router.get("/{guild_id}/roles")
async def get_roles(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}/roles", headers=BOT_HEADERS
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
        roles = resp.json()
    return [
        {"id": r["id"], "name": r["name"], "color": r["color"]}
        for r in roles
        if r["name"] != "@everyone"
    ]


@router.get("/{guild_id}/members/search")
async def search_members(
    guild_id: str,
    query: str = "",
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    if not query.strip():
        return []
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}/members/search?query={quote(query)}&limit=25",
            headers=BOT_HEADERS, timeout=5
        )
        if resp.status_code != 200:
            return []
        members = resp.json()
    return [
        {
            "id": m["user"]["id"],
            "username": m["user"]["username"],
            "global_name": m["user"].get("global_name"),
            "nick": m.get("nick"),
            "display_name": m.get("nick") or m["user"].get("global_name") or m["user"]["username"],
            "avatar": m["user"].get("avatar"),
        }
        for m in members
    ]
