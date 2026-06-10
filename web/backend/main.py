import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from dotenv import load_dotenv

load_dotenv()

from database import init_db
from routers.auth_router     import router as auth_router
from routers.guilds_router   import router as guilds_router
from routers.settings_router import router as settings_router
from routers.chzzk_router    import router as chzzk_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Discord Bot Dashboard API", lifespan=lifespan)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(guilds_router)
app.include_router(settings_router)
app.include_router(chzzk_router)


@app.get("/auth/callback")
async def auth_callback_compat(request: Request):
    return RedirectResponse(url=f"/api/auth/callback?{request.url.query}", status_code=307)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Discord Bot Dashboard API"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
