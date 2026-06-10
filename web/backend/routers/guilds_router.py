import os
import httpx
from fastapi import APIRouter, HTTPException, Depends
from deps import get_current_user
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
        if has_manage_guild(int(g.get("permissions", 0)))
    ]
    return admin_guilds


@router.get("/{guild_id}/channels")
async def get_channels(guild_id: str, user: dict = Depends(get_current_user)):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}/channels", headers=BOT_HEADERS
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="서버를 찾을 수 없습니다.")
        channels = resp.json()
    # 텍스트 채널만 (type=0)
    return [{"id": c["id"], "name": c["name"]} for c in channels if c["type"] == 0]


@router.get("/{guild_id}/roles")
async def get_roles(guild_id: str, user: dict = Depends(get_current_user)):
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
