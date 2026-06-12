import os
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from database.db import get_db
from deps import get_current_user

router = APIRouter(prefix="/api/verify", tags=["verify"])

DISCORD_API = "https://discord.com/api/v10"
_BOT_TOKEN  = os.getenv("DISCORD_TOKEN", "")


def _bot_headers() -> dict:
    return {"Authorization": f"Bot {_BOT_TOKEN}", "Content-Type": "application/json"}


async def _add_role(guild_id: str, user_id: str, role_id: str) -> bool:
    url = f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}/roles/{role_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.put(url, headers=_bot_headers())
        return resp.status_code in (200, 204)


async def _remove_role(guild_id: str, user_id: str, role_id: str) -> bool:
    url = f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}/roles/{role_id}"
    async with httpx.AsyncClient() as client:
        resp = await client.delete(url, headers=_bot_headers())
        return resp.status_code in (200, 204)


@router.get("/{guild_id}/status")
async def verify_status(
    guild_id: str,
    user: dict = Depends(get_current_user),
):
    """현재 로그인된 사용자의 Chzzk 인증 상태를 반환합니다."""
    db = await get_db()
    row = await (await db.execute(
        "SELECT 1 FROM chzzk_verifications WHERE guild_id=? AND user_id=?",
        (int(guild_id), int(user["sub"]))
    )).fetchone()
    return {"verified": bool(row), "guild_id": guild_id, "user_id": user["sub"]}


@router.post("/{guild_id}")
async def complete_verification(
    guild_id: str,
    user: dict = Depends(get_current_user),
):
    """Chzzk 인증 완료 처리: DB 기록 후 Discord 역할 업데이트."""
    user_id = int(user["sub"])
    gid     = int(guild_id)

    db = await get_db()
    cfg = await (await db.execute(
        "SELECT verified_role_id, unverified_role_id FROM guild_config WHERE guild_id=?",
        (gid,)
    )).fetchone()

    if not cfg:
        raise HTTPException(status_code=404, detail="서버 설정을 찾을 수 없습니다.")

    # 인증 이력 기록
    await db.execute(
        """INSERT INTO chzzk_verifications(guild_id, user_id, verified_at) VALUES(?,?,?)
           ON CONFLICT(guild_id, user_id) DO UPDATE SET verified_at=excluded.verified_at""",
        (gid, user_id, time.time())
    )
    await db.commit()

    warnings: list[str] = []

    # 미인증 역할 제거
    if cfg["unverified_role_id"]:
        ok = await _remove_role(guild_id, str(user_id), str(cfg["unverified_role_id"]))
        if not ok:
            warnings.append("미인증 역할 제거 실패 (봇 권한 또는 역할 설정 확인 필요)")

    # 인증됨 역할 부여
    if cfg["verified_role_id"]:
        ok = await _add_role(guild_id, str(user_id), str(cfg["verified_role_id"]))
        if not ok:
            warnings.append("인증됨 역할 부여 실패 (봇 권한 또는 역할 설정 확인 필요)")

    return {"success": True, "message": "인증이 완료되었습니다.", "warnings": warnings}
