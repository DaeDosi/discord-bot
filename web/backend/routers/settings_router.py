import os
import asyncio
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from deps import get_current_user, require_guild_admin
from database import get_db

_BOT_TOKEN   = os.getenv("DISCORD_TOKEN", "")
_DISCORD_API = "https://discord.com/api/v10"

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── 공통 설정 ────────────────────────────────────────────────────────────────
class GuildConfig(BaseModel):
    mod_role_id:          Optional[str] = None
    welcome_channel:      Optional[str] = None
    goodbye_channel:      Optional[str] = None
    log_channel:          Optional[str] = None
    auto_role_id:         Optional[str] = None
    levelup_channel:      Optional[str] = None
    levelup_dm:           bool = False
    automod_enabled:      bool = True
    badwords:             str  = ""
    welcome_message:      str  = ""
    goodbye_message:      str  = ""
    warn_kick_threshold:  int  = 0
    warn_ban_threshold:   int  = 0
    points_per_level:     int  = 0


@router.get("/{guild_id}")
async def get_config(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM guild_config WHERE guild_id=?", (int(guild_id),)
    )).fetchone()
    if not row:
        return {}
    return dict(row)


@router.put("/{guild_id}")
async def update_config(
    guild_id: str,
    cfg: GuildConfig,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        """INSERT INTO guild_config
           (guild_id, mod_role_id, welcome_channel, goodbye_channel, log_channel,
            auto_role_id, levelup_channel, levelup_dm, automod_enabled, badwords,
            welcome_message, goodbye_message,
            warn_kick_threshold, warn_ban_threshold, points_per_level)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(guild_id) DO UPDATE SET
               mod_role_id=excluded.mod_role_id,
               welcome_channel=excluded.welcome_channel,
               goodbye_channel=excluded.goodbye_channel,
               log_channel=excluded.log_channel,
               auto_role_id=excluded.auto_role_id,
               levelup_channel=excluded.levelup_channel,
               levelup_dm=excluded.levelup_dm,
               automod_enabled=excluded.automod_enabled,
               badwords=excluded.badwords,
               welcome_message=excluded.welcome_message,
               goodbye_message=excluded.goodbye_message,
               warn_kick_threshold=excluded.warn_kick_threshold,
               warn_ban_threshold=excluded.warn_ban_threshold,
               points_per_level=excluded.points_per_level""",
        (
            int(guild_id),
            int(cfg.mod_role_id)     if cfg.mod_role_id     else None,
            int(cfg.welcome_channel) if cfg.welcome_channel else None,
            int(cfg.goodbye_channel) if cfg.goodbye_channel else None,
            int(cfg.log_channel)     if cfg.log_channel     else None,
            int(cfg.auto_role_id)    if cfg.auto_role_id    else None,
            int(cfg.levelup_channel) if cfg.levelup_channel else None,
            int(cfg.levelup_dm),
            int(cfg.automod_enabled),
            cfg.badwords,
            cfg.welcome_message,
            cfg.goodbye_message,
            cfg.warn_kick_threshold,
            cfg.warn_ban_threshold,
            cfg.points_per_level,
        )
    )
    await db.commit()
    return {"ok": True}


# ── 레벨 보상 ────────────────────────────────────────────────────────────────
class LevelReward(BaseModel):
    level:   int
    role_id: str


@router.get("/{guild_id}/level-rewards")
async def get_level_rewards(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT level, role_id FROM level_rewards WHERE guild_id=? ORDER BY level",
        (int(guild_id),)
    )).fetchall()
    return [dict(r) for r in rows]


@router.post("/{guild_id}/level-rewards")
async def add_level_reward(
    guild_id: str,
    reward: LevelReward,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        """INSERT INTO level_rewards(guild_id, level, role_id) VALUES(?,?,?)
           ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id""",
        (int(guild_id), reward.level, int(reward.role_id))
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{guild_id}/level-rewards/{level}")
async def delete_level_reward(
    guild_id: str,
    level: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        "DELETE FROM level_rewards WHERE guild_id=? AND level=?",
        (int(guild_id), level)
    )
    await db.commit()
    return {"ok": True}


# ── 인증 설정 ────────────────────────────────────────────────────────────────
class VerificationConfig(BaseModel):
    verification_channel:   Optional[str] = None
    unverified_role_id:     Optional[str] = None
    verified_role_id:       Optional[str] = None
    use_chzzk_verification: bool = False
    verification_message:   str  = ""
    embed_color:            str  = "#5865F2"
    embed_title:            str  = "🔐 입장 인증"


@router.get("/{guild_id}/verification")
async def get_verification_config(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        """SELECT verification_channel, unverified_role_id, verified_role_id,
                  use_chzzk_verification, verification_message,
                  embed_color, embed_title
           FROM guild_config WHERE guild_id=?""",
        (int(guild_id),)
    )).fetchone()
    if not row:
        return {}
    d = dict(row)
    # Discord 스노우플레이크는 JS Number.MAX_SAFE_INTEGER 초과 → 문자열로 반환
    for key in ("verification_channel", "unverified_role_id", "verified_role_id"):
        if d.get(key) is not None:
            d[key] = str(d[key])
    # NULL 기본값 처리
    d["embed_color"] = d.get("embed_color") or "#5865F2"
    d["embed_title"] = d.get("embed_title") or "🔐 입장 인증"
    return d


@router.put("/{guild_id}/verification")
async def update_verification_config(
    guild_id: str,
    cfg: VerificationConfig,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        """INSERT INTO guild_config
           (guild_id, verification_channel, unverified_role_id, verified_role_id,
            use_chzzk_verification, verification_message, embed_color, embed_title)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(guild_id) DO UPDATE SET
               verification_channel   = excluded.verification_channel,
               unverified_role_id     = excluded.unverified_role_id,
               verified_role_id       = excluded.verified_role_id,
               use_chzzk_verification = excluded.use_chzzk_verification,
               verification_message   = excluded.verification_message,
               embed_color            = excluded.embed_color,
               embed_title            = excluded.embed_title""",
        (
            int(guild_id),
            int(cfg.verification_channel)   if cfg.verification_channel   else None,
            int(cfg.unverified_role_id)     if cfg.unverified_role_id     else None,
            int(cfg.verified_role_id)       if cfg.verified_role_id       else None,
            int(cfg.use_chzzk_verification),
            cfg.verification_message,
            cfg.embed_color or "#5865F2",
            cfg.embed_title or "🔐 입장 인증",
        )
    )
    await db.commit()
    return {"ok": True}


# ── 리더보드 ─────────────────────────────────────────────────────────────────
async def _fetch_display_name(client: httpx.AsyncClient, guild_id: str, user_id: int) -> str:
    try:
        resp = await client.get(
            f"{_DISCORD_API}/guilds/{guild_id}/members/{user_id}",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            return (
                data.get("nick")
                or data.get("user", {}).get("global_name")
                or data.get("user", {}).get("username")
                or str(user_id)
            )
    except Exception:
        pass
    return str(user_id)


@router.get("/{guild_id}/leaderboard")
async def get_leaderboard(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT user_id, xp, level FROM user_xp WHERE guild_id=? ORDER BY xp DESC LIMIT 20",
        (int(guild_id),)
    )).fetchall()

    async with httpx.AsyncClient() as client:
        names = await asyncio.gather(*[
            _fetch_display_name(client, guild_id, r["user_id"]) for r in rows
        ])

    return [
        {**dict(r), "display_name": name}
        for r, name in zip(rows, names)
    ]


# ── 경고 관리 ────────────────────────────────────────────────────────────────

@router.get("/{guild_id}/warnings")
async def get_warnings(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        """SELECT user_id, COUNT(*) AS count, MAX(created_at) AS latest_at
           FROM warnings WHERE guild_id=?
           GROUP BY user_id ORDER BY count DESC, latest_at DESC""",
        (int(guild_id),)
    )).fetchall()
    async with httpx.AsyncClient() as client:
        names = await asyncio.gather(*[
            _fetch_display_name(client, guild_id, r["user_id"]) for r in rows
        ])
    return [
        {"user_id": str(r["user_id"]), "display_name": n, "count": r["count"], "latest_at": r["latest_at"]}
        for r, n in zip(rows, names)
    ]


@router.get("/{guild_id}/warnings/{user_id}")
async def get_user_warnings(
    guild_id: str,
    user_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, reason, created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
        (int(guild_id), int(user_id))
    )).fetchall()
    return [dict(r) for r in rows]


@router.delete("/{guild_id}/warnings/{user_id}")
async def clear_user_warnings(
    guild_id: str,
    user_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        "DELETE FROM warnings WHERE guild_id=? AND user_id=?",
        (int(guild_id), int(user_id))
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{guild_id}/warnings/{user_id}/{warn_id}")
async def delete_single_warning(
    guild_id: str,
    user_id: str,
    warn_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        "DELETE FROM warnings WHERE id=? AND guild_id=? AND user_id=?",
        (warn_id, int(guild_id), int(user_id))
    )
    await db.commit()
    return {"ok": True}
