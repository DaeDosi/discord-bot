import os
import time
import secrets
import httpx
from urllib.parse import urlencode, quote
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from jose import jwt, JWTError
from auth import JWT_SECRET, JWT_ALGORITHM, FRONTEND_URL
from database.db import get_db
from routers.verify_router import _add_role, _remove_role

router = APIRouter(prefix="/api/chzzk-auth", tags=["chzzk-auth"])

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
NAVER_REDIRECT_URI  = os.getenv(
    "NAVER_REDIRECT_URI",
    "http://localhost:8000/api/chzzk-auth/callback",
)

NAVER_AUTH_URL    = "https://nid.naver.com/oauth2.0/authorize"
NAVER_TOKEN_URL   = "https://nid.naver.com/oauth2.0/token"
NAVER_PROFILE_URL = "https://openapi.naver.com/v1/nid/me"
DISCORD_API       = "https://discord.com/api/v10"
_BOT_TOKEN        = os.getenv("DISCORD_TOKEN", "")


async def _get_naver_nickname(access_token: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                NAVER_PROFILE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            print(f"[chzzk-auth] Naver profile status={resp.status_code} body={resp.text[:300]}")
            if resp.status_code == 200:
                profile = resp.json().get("response", {})
                # nickname 우선, 없으면 name 사용
                nick = profile.get("nickname") or profile.get("name")
                print(f"[chzzk-auth] Naver nickname={nick!r}")
                return nick or None
    except Exception as e:
        print(f"[chzzk-auth] Naver profile fetch error: {e}")
    return None


async def _set_discord_nickname(guild_id: str, user_id: str, nickname: str) -> None:
    import json
    url = f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}"
    headers = {
        "Authorization": f"Bot {_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.patch(
                url, headers=headers, content=json.dumps({"nick": nickname})
            )
            print(f"[chzzk-auth] Discord nick PATCH status={resp.status_code} body={resp.text[:200]}")
    except Exception as e:
        print(f"[chzzk-auth] Discord nick PATCH error: {e}")


def _build_state(guild_id: str, discord_user_id: str) -> str:
    payload = {
        "guild_id": guild_id,
        "user_id":  discord_user_id,
        "exp":      datetime.utcnow() + timedelta(minutes=15),
        "nonce":    secrets.token_hex(8),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _err(msg: str, guild_id: str = "") -> RedirectResponse:
    gid = f"&guild_id={quote(guild_id)}" if guild_id else ""
    return RedirectResponse(f"{FRONTEND_URL}/verify?error={quote(msg)}{gid}")


@router.get("/login")
async def chzzk_login(
    guild_id:        str = Query(...),
    discord_user_id: str = Query(...),
):
    if not NAVER_CLIENT_ID:
        return _err("naver_not_configured", guild_id)
    if not discord_user_id:
        return _err("discord_not_logged_in", guild_id)

    state  = _build_state(guild_id, discord_user_id)
    params = {
        "response_type": "code",
        "client_id":     NAVER_CLIENT_ID,
        "redirect_uri":  NAVER_REDIRECT_URI,
        "state":         state,
    }
    return RedirectResponse(f"{NAVER_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
async def chzzk_callback(
    code:  str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/verify?error={quote(error)}")
    if not code or not state:
        return _err("missing_params")

    # ── state 검증 ──────────────────────────────────────────────────────────
    try:
        state_data      = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        guild_id        = state_data["guild_id"]
        discord_user_id = state_data["user_id"]
    except JWTError:
        return _err("invalid_state")

    if not discord_user_id:
        return _err("discord_user_missing", guild_id)

    # ── 네이버 code → access_token 교환 ─────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.get(
                NAVER_TOKEN_URL,
                params={
                    "grant_type":    "authorization_code",
                    "client_id":     NAVER_CLIENT_ID,
                    "client_secret": NAVER_CLIENT_SECRET,
                    "code":          code,
                    "state":         state,
                },
            )
            token_data   = token_resp.json()
            access_token = token_data.get("access_token")
    except Exception as e:
        print(f"[chzzk-auth] Naver token request failed: {e}")
        return _err("oauth_failed", guild_id)

    if not access_token:
        print(f"[chzzk-auth] No access_token in response: {token_data}")
        return _err("token_failed", guild_id)

    # ── DB에 인증 이력 기록 ──────────────────────────────────────────────────
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO chzzk_verifications(guild_id, user_id, verified_at) VALUES(?,?,?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET verified_at=excluded.verified_at""",
            (int(guild_id), int(discord_user_id), time.time()),
        )
        await db.commit()
    except Exception as e:
        print(f"[chzzk-auth] DB write failed: {e}")
        return _err("db_error", guild_id)

    # ── Discord 역할 교체 ────────────────────────────────────────────────────
    cfg = await (await db.execute(
        "SELECT verified_role_id, unverified_role_id FROM guild_config WHERE guild_id=?",
        (int(guild_id),),
    )).fetchone()

    if not cfg:
        print(f"[chzzk-auth] guild_config not found for guild {guild_id}")
        return _err("guild_not_configured", guild_id)

    if not cfg["verified_role_id"]:
        print(f"[chzzk-auth] verified_role_id not set for guild {guild_id}")
        return _err("role_not_configured", guild_id)

    if cfg["unverified_role_id"]:
        ok = await _remove_role(guild_id, discord_user_id, str(cfg["unverified_role_id"]))
        if not ok:
            print(f"[chzzk-auth] Failed to remove unverified role for user {discord_user_id}")

    ok = await _add_role(guild_id, discord_user_id, str(cfg["verified_role_id"]))
    if not ok:
        print(f"[chzzk-auth] Failed to add verified role for user {discord_user_id}")
        return _err("role_assign_failed", guild_id)

    # ── Naver 닉네임 → Discord 서버 닉네임 자동 변경 ─────────────────────────
    naver_nick = await _get_naver_nickname(access_token)
    if naver_nick:
        await _set_discord_nickname(guild_id, discord_user_id, naver_nick)
    else:
        print(f"[chzzk-auth] Could not fetch Naver nickname for user {discord_user_id}")

    return RedirectResponse(f"{FRONTEND_URL}/verify?success=1&guild_id={quote(guild_id)}")
