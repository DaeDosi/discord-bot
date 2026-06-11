from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from deps import get_current_user, require_guild_admin
from database import get_db

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── 공통 설정 ────────────────────────────────────────────────────────────────
class GuildConfig(BaseModel):
    mod_role_id:     Optional[str] = None
    welcome_channel: Optional[str] = None
    goodbye_channel: Optional[str] = None
    log_channel:     Optional[str] = None
    auto_role_id:    Optional[str] = None
    levelup_channel: Optional[str] = None
    levelup_dm:      bool = False
    automod_enabled: bool = True
    badwords:        str  = ""


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
            auto_role_id, levelup_channel, levelup_dm, automod_enabled, badwords)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(guild_id) DO UPDATE SET
               mod_role_id=excluded.mod_role_id,
               welcome_channel=excluded.welcome_channel,
               goodbye_channel=excluded.goodbye_channel,
               log_channel=excluded.log_channel,
               auto_role_id=excluded.auto_role_id,
               levelup_channel=excluded.levelup_channel,
               levelup_dm=excluded.levelup_dm,
               automod_enabled=excluded.automod_enabled,
               badwords=excluded.badwords""",
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


# ── 리더보드 ─────────────────────────────────────────────────────────────────
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
    return [dict(r) for r in rows]
