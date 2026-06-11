import time
import httpx
from fastapi import Depends, HTTPException, Header
from jose import JWTError
from auth import decode_jwt

# ── 기본 인증 ──────────────────────────────────────────────────────────────────

async def get_current_user(authorization: str = Header(...)) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    token = authorization[7:]
    try:
        payload = decode_jwt(token)
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 토큰입니다.")


# ── 서버 관리자 권한 검증 ──────────────────────────────────────────────────────

_MANAGE_GUILD  = 0x20
_ADMINISTRATOR = 0x8

# {f"{user_id}:{guild_id}": (monotonic_time, is_admin_bool)}
_guild_auth_cache: dict[str, tuple[float, bool]] = {}
_GUILD_AUTH_TTL = 300  # 5분 캐싱

_DISCORD_API = "https://discord.com/api/v10"


async def require_guild_admin(
    guild_id: str,
    user: dict = Depends(get_current_user),
) -> None:
    """현재 유저가 guild_id 서버의 관리자(MANAGE_GUILD/ADMINISTRATOR)인지 검증합니다."""
    cache_key = f"{user['sub']}:{guild_id}"

    cached = _guild_auth_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _GUILD_AUTH_TTL:
        if not cached[1]:
            raise HTTPException(status_code=403, detail="이 서버에 대한 관리자 권한이 없습니다.")
        return

    headers = {"Authorization": f"Bearer {user['access_token']}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{_DISCORD_API}/users/@me/guilds", headers=headers)

        if resp.status_code == 401:
            raise HTTPException(
                status_code=401,
                detail="Discord 인증이 만료되었습니다. 다시 로그인해주세요.",
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=503, detail="권한 확인 서비스에 일시적으로 연결할 수 없습니다.")

        is_admin = False
        for g in resp.json():
            if g["id"] == guild_id:
                perms = int(g.get("permissions", 0))
                if perms & _MANAGE_GUILD or perms & _ADMINISTRATOR:
                    is_admin = True
                break

        _guild_auth_cache[cache_key] = (time.monotonic(), is_admin)

        if not is_admin:
            raise HTTPException(status_code=403, detail="이 서버에 대한 관리자 권한이 없습니다.")

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="권한 확인 서비스에 일시적으로 연결할 수 없습니다.")
