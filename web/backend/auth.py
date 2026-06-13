import os
import secrets as _secrets
import httpx
from datetime import datetime, timedelta
from jose import jwt, JWTError

DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/auth/callback")
FRONTEND_URL          = os.getenv("FRONTEND_URL", "http://localhost:3000")
JWT_ALGORITHM         = "HS256"
JWT_EXPIRE_HOURS      = 24 * 7   # Discord access_token lifetime = 7일이므로 맞춤

_jwt_secret_env = os.getenv("JWT_SECRET")
if _jwt_secret_env:
    JWT_SECRET = _jwt_secret_env
else:
    JWT_SECRET = _secrets.token_hex(32)
    print("WARNING: JWT_SECRET 환경변수가 설정되지 않았습니다. "
          "임의 시크릿을 사용합니다 — 서버 재시작 시 모든 세션이 만료됩니다.")

DISCORD_API = "https://discord.com/api/v10"
OAUTH_URL   = "https://discord.com/api/oauth2/authorize"
TOKEN_URL   = "https://discord.com/api/oauth2/token"


def build_oauth_url() -> str:
    from urllib.parse import urlencode
    # CSRF 방지: 서명된 JWT를 state로 사용 (stateless, 10분 유효)
    state = jwt.encode(
        {"exp": datetime.utcnow() + timedelta(minutes=10), "nonce": _secrets.token_hex(8)},
        JWT_SECRET, algorithm=JWT_ALGORITHM,
    )
    params = {
        "client_id":     DISCORD_CLIENT_ID,
        "redirect_uri":  DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope":         "identify guilds",
        "state":         state,
    }
    return f"{OAUTH_URL}?{urlencode(params)}"


def verify_oauth_state(state: str | None) -> bool:
    """OAuth2 state JWT 서명·만료 검증. CSRF 방지용."""
    if not state:
        return False
    try:
        jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except JWTError:
        return False


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
    """Discord /users/@me/guilds를 페이지네이션으로 전부 수집."""
    headers = {"Authorization": f"Bearer {access_token}"}
    guilds: list[dict] = []
    after: str | None = None
    async with httpx.AsyncClient() as client:
        while True:
            params: dict = {"limit": 200}
            if after:
                params["after"] = after
            resp = await client.get(
                f"{DISCORD_API}/users/@me/guilds",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            batch: list[dict] = resp.json()
            if not batch:
                break
            guilds.extend(batch)
            if len(batch) < 200:
                break
            after = batch[-1]["id"]
    return guilds


def create_jwt(user_id: str, username: str, global_name: str, avatar: str, access_token: str) -> str:
    payload = {
        "sub":          user_id,
        "username":     username,
        "global_name":  global_name,
        "avatar":       avatar,
        "access_token": access_token,
        "exp":          datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
