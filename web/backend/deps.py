import asyncio
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

# 캐시가 비어있는 상태에서 대시보드 한 페이지가 여러 API를 동시에 호출하면(예: 치지직 탭이
# subscriptions/channels/roles/follow-tiers/verifications를 한꺼번에 요청) 각 요청이 독립적으로
# Discord의 /users/@me/guilds 를 중복 호출해 체감 로딩이 느려졌다. 같은 유저·서버에 대한
# 동시 요청은 아래 in-flight 테이블로 실제 호출을 1회만 공유한다.
_guild_auth_inflight: dict[str, "asyncio.Task[bool]"] = {}

_DISCORD_API = "https://discord.com/api/v10"


async def _resolve_guild_admin(cache_key: str, guild_id: str, user: dict) -> bool:
    headers = {"Authorization": f"Bearer {user['access_token']}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{_DISCORD_API}/users/@me/guilds", headers=headers)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="권한 확인 서비스에 일시적으로 연결할 수 없습니다.")

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
    return is_admin


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

    task = _guild_auth_inflight.get(cache_key)
    if task is None:
        task = asyncio.ensure_future(_resolve_guild_admin(cache_key, guild_id, user))
        _guild_auth_inflight[cache_key] = task

        def _cleanup(_t, key=cache_key, own_task=task):
            if _guild_auth_inflight.get(key) is own_task:
                _guild_auth_inflight.pop(key, None)
        task.add_done_callback(_cleanup)

    try:
        is_admin = await asyncio.shield(task)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="권한 확인 서비스에 일시적으로 연결할 수 없습니다.")

    if not is_admin:
        raise HTTPException(status_code=403, detail="이 서버에 대한 관리자 권한이 없습니다.")
