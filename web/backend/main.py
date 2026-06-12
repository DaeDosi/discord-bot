import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

load_dotenv()

from urllib.parse import quote
from database import init_db
from auth import exchange_code, get_discord_user, create_jwt, FRONTEND_URL, verify_oauth_state
from chzzk_monitor import start_monitor
from routers.auth_router     import router as auth_router
from routers.guilds_router   import router as guilds_router
from routers.settings_router import router as settings_router
from routers.chzzk_router    import router as chzzk_router
from routers.stats_router    import router as stats_router
from routers.verify_router   import router as verify_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(start_monitor())
    yield


app = FastAPI(title="Discord Bot Dashboard API", lifespan=lifespan)

# JWT는 Authorization 헤더로 전달하므로 credentials 불필요 → allow_origins=["*"] 사용 가능
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(guilds_router)
app.include_router(settings_router)
app.include_router(chzzk_router)
app.include_router(stats_router)
app.include_router(verify_router)


@app.get("/auth/callback")
async def auth_callback_compat(code: str = None, state: str = None, error: str = None):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/login?error={quote(error)}")
    if not verify_oauth_state(state):
        return RedirectResponse(f"{FRONTEND_URL}/login?error=invalid_state")
    if not code:
        return RedirectResponse(f"{FRONTEND_URL}/login?error=no_code")
    try:
        token_data   = await exchange_code(code)
        access_token = token_data["access_token"]
        user         = await get_discord_user(access_token)
        jwt_token    = create_jwt(
            user_id=user["id"],
            username=user["username"],
            avatar=user.get("avatar", ""),
            access_token=access_token,
        )
        return RedirectResponse(f"{FRONTEND_URL}/callback?token={jwt_token}")
    except Exception as e:
        return RedirectResponse(f"{FRONTEND_URL}/login?error={quote(str(e))}")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Discord Bot Dashboard API"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
