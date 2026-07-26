import os
import sys
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from rate_limit import RateLimitMiddleware
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트를 sys.path에 추가 — 봇과 스키마를 공유하는 루트의 database 모듈을 사용
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from urllib.parse import quote
from database import init_db
from auth import exchange_code, get_discord_user, create_jwt, FRONTEND_URL, verify_oauth_state
from chzzk_monitor import start_monitor
from rising_collector import start_collector
from routers.auth_router       import router as auth_router
from routers.guilds_router     import router as guilds_router
from routers.settings_router   import router as settings_router
from routers.chzzk_router      import router as chzzk_router
from routers.stats_router      import router as stats_router
from routers.verify_router     import router as verify_router
from routers.chzzk_auth_router import router as chzzk_auth_router
from routers.admin_router      import router as admin_router
from routers.points_router     import router as points_router
from routers.mc_event_router   import router as mc_event_router
from routers.rising_router      import router as rising_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(start_monitor())
    asyncio.create_task(start_collector())
    yield


app = FastAPI(title="Discord Bot Dashboard API", lifespan=lifespan)

# 레이트 리밋 — /api/rising/* 는 인증이 없는 공개 API라 남용 방어가 필요하다.
# CORS보다 먼저 add_middleware 하면 바깥쪽(=나중 실행)이 되므로, 429 응답에도
# CORS 헤더가 붙도록 CORS를 나중에 추가한다(Starlette는 나중에 추가한 것이 바깥쪽).
app.add_middleware(RateLimitMiddleware)

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
app.include_router(chzzk_auth_router)
app.include_router(admin_router)
app.include_router(points_router)
app.include_router(mc_event_router)
app.include_router(rising_router)


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
        # URL 프래그먼트(#)로 전달 — 쿼리스트링과 달리 서버 접근 로그/Referer에 남지 않음
        return RedirectResponse(f"{FRONTEND_URL}/callback#token={jwt_token}")
    except Exception as e:
        return RedirectResponse(f"{FRONTEND_URL}/login?error={quote(str(e))}")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Discord Bot Dashboard API"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
