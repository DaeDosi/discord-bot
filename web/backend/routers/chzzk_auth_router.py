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


def _build_state(guild_id: str, discord_user_id: str) -> str:
    payload = {
        "guild_id": guild_id,
        "user_id":  discord_user_id,
        "exp":      datetime.utcnow() + timedelta(minutes=15),
        "nonce":    secrets.token_hex(8),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


@router.get("/login")
async def chzzk_login(
    guild_id:        str = Query(...),
    discord_user_id: str = Query(...),
):
    if not NAVER_CLIENT_ID:
        return RedirectResponse(
            f"{FRONTEND_URL}/verify?error=naver_not_configured&guild_id={quote(guild_id)}"
        )
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
        return RedirectResponse(f"{FRONTEND_URL}/verify?error=missing_params")

    try:
        state_data      = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        guild_id        = state_data["guild_id"]
        discord_user_id = state_data["user_id"]
    except JWTError:
        return RedirectResponse(f"{FRONTEND_URL}/verify?error=invalid_state")

    # 네이버 code → access_token 교환
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

            if not access_token:
                return RedirectResponse(
                    f"{FRONTEND_URL}/verify?error=token_failed&guild_id={quote(guild_id)}"
                )
    except Exception:
        return RedirectResponse(
            f"{FRONTEND_URL}/verify?error=oauth_failed&guild_id={quote(guild_id)}"
        )

    # DB에 인증 이력 기록
    db = await get_db()
    await db.execute(
        """INSERT INTO chzzk_verifications(guild_id, user_id, verified_at) VALUES(?,?,?)
           ON CONFLICT(guild_id, user_id) DO UPDATE SET verified_at=excluded.verified_at""",
        (int(guild_id), int(discord_user_id), time.time()),
    )
    await db.commit()

    # Discord 역할 교체
    cfg = await (await db.execute(
        "SELECT verified_role_id, unverified_role_id FROM guild_config WHERE guild_id=?",
        (int(guild_id),),
    )).fetchone()

    if cfg:
        if cfg["unverified_role_id"]:
            await _remove_role(guild_id, discord_user_id, str(cfg["unverified_role_id"]))
        if cfg["verified_role_id"]:
            await _add_role(guild_id, discord_user_id, str(cfg["verified_role_id"]))

    return RedirectResponse(
        f"{FRONTEND_URL}/verify?success=1&guild_id={quote(guild_id)}"
    )
