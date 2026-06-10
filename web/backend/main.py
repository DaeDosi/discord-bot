import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

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


@app.get("/")
async def root():
    return {"status": "ok", "message": "Discord Bot Dashboard API"}
