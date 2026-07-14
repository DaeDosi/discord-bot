import os
import time as _time
import httpx
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from deps import get_current_user, require_guild_admin
from database import get_db

router = APIRouter(prefix="/api/community", tags=["community"])

_BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
_DISCORD   = "https://discord.com/api/v10"

_DESCRIPTION_MAX_LEN = 300

# 공개 목록 엔드포인트는 로그인 없이 누구나(크롤러 포함) 호출할 수 있어 Discord API를
# 매 요청마다 때리면 안 된다 — 봇 서버 목록을 짧게 캐싱한다 (admin_router._bot_guilds와
# 동일한 패턴, 별도 프로세스/모듈이라 공유하지 않고 로컬로 둔다).
_guilds_cache: list[dict] = []
_guilds_cache_ts: float = 0.0
_GUILDS_TTL = 120  # seconds


async def _bot_guilds() -> list[dict]:
    global _guilds_cache, _guilds_cache_ts
    now = _time.monotonic()
    if _guilds_cache and now - _guilds_cache_ts < _GUILDS_TTL:
        return _guilds_cache
    guilds: list[dict] = []
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_DISCORD}/users/@me/guilds",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"},
            params={"limit": 200},
        )
        if resp.status_code == 200:
            guilds = resp.json()
    _guilds_cache = guilds
    _guilds_cache_ts = now
    return guilds


class CommunitySettings(BaseModel):
    is_public:   bool
    description: str = Field(default="", max_length=_DESCRIPTION_MAX_LEN)
    invite_url:  str = Field(default="", max_length=300)


@router.get("/{guild_id}/settings")
async def get_settings(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db  = await get_db()
    row = await (await db.execute(
        "SELECT is_public, description, invite_url FROM community_listing WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone()
    if not row:
        return {"is_public": False, "description": "", "invite_url": ""}
    return {
        "is_public":   bool(row["is_public"]),
        "description": row["description"] or "",
        "invite_url":  row["invite_url"] or "",
    }


@router.put("/{guild_id}/settings")
async def save_settings(
    guild_id: str,
    body: CommunitySettings,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        """INSERT INTO community_listing(guild_id, is_public, description, invite_url, updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(guild_id) DO UPDATE SET
               is_public=excluded.is_public,
               description=excluded.description,
               invite_url=excluded.invite_url,
               updated_at=excluded.updated_at""",
        (
            int(guild_id), int(body.is_public), body.description.strip(), body.invite_url.strip(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    await db.commit()
    return {"ok": True}


@router.get("/list")
async def public_list():
    """로그인 불필요 — /community 공개 페이지가 그대로 노출하는 목록."""
    db   = await get_db()
    rows = await (await db.execute(
        """SELECT guild_id, description, invite_url FROM community_listing
           WHERE is_public=1 ORDER BY updated_at DESC"""
    )).fetchall()
    if not rows:
        return []

    guilds_list = await _bot_guilds()
    guild_map   = {g["id"]: g for g in guilds_list}

    chzzk_rows = await (await db.execute(
        "SELECT guild_id, chzzk_channel_id, chzzk_name, chzzk_image_url, is_live FROM chzzk_subscriptions"
    )).fetchall()
    chzzk_map = {str(r["guild_id"]): r for r in chzzk_rows}

    result = []
    for r in rows:
        gid = str(r["guild_id"])
        g = guild_map.get(gid)
        if not g:
            continue  # 봇이 더 이상 없는 서버는 제외
        chzzk = chzzk_map.get(gid)
        result.append({
            "guild_id":    gid,
            "name":        g["name"],
            "icon":        (
                f"https://cdn.discordapp.com/icons/{gid}/{g['icon']}.png"
                if g.get("icon") else None
            ),
            "description": r["description"] or "",
            "invite_url":  r["invite_url"] or None,
            "chzzk_channel_id": chzzk["chzzk_channel_id"] if chzzk else None,
            "chzzk_name":       chzzk["chzzk_name"] if chzzk else None,
            "chzzk_image_url":  chzzk["chzzk_image_url"] if chzzk else None,
            "chzzk_is_live":    bool(chzzk["is_live"]) if chzzk else False,
        })
    return result
