import os
import httpx
from datetime import datetime, timedelta
from jose import jwt, JWTError

DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/auth/callback")
FRONTEND_URL          = os.getenv("FRONTEND_URL", "http://localhost:3000")
JWT_SECRET            = os.getenv("JWT_SECRET", "change-me")
JWT_ALGORITHM         = "HS256"
JWT_EXPIRE_HOURS      = 24 * 30  # 30일

DISCORD_API = "https://discord.com/api/v10"
OAUTH_URL   = "https://discord.com/api/oauth2/authorize"
TOKEN_URL   = "https://discord.com/api/oauth2/token"


def build_oauth_url() -> str:
    from urllib.parse import urlencode
    params = {
        "client_id":     DISCORD_CLIENT_ID,
        "redirect_uri":  DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope":         "identify guilds",
    }
    return f"{OAUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    data = {
        "client_id":     DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  DISCORD_REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, data=data)
        resp.raise_for_status()
        return resp.json()


async def get_discord_user(access_token: str) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DISCORD_API}/users/@me", headers=headers)
        resp.raise_for_status()
        return resp.json()


async def get_discord_guilds(access_token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{DISCORD_API}/users/@me/guilds", headers=headers)
        resp.raise_for_status()
        return resp.json()


def create_jwt(user_id: str, username: str, avatar: str, access_token: str) -> str:
    payload = {
        "sub":          user_id,
        "username":     username,
        "avatar":       avatar,
        "access_token": access_token,
        "exp":          datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
