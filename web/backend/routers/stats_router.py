import hashlib
import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request
from database.db import get_db

_KST = ZoneInfo("Asia/Seoul")

def today_kst() -> str:
    return datetime.datetime.now(_KST).date().isoformat()

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def get_stats():
    try:
        db = await get_db()

        # 봇이 30분마다 기록한 bot_stats 우선 사용, 없으면 DB 직접 집계
        stats_row = await (await db.execute(
            "SELECT guilds, chzzk_subs FROM bot_stats WHERE id=1"
        )).fetchone()

        if stats_row:
            guilds = stats_row["guilds"]
            chzzk  = stats_row["chzzk_subs"]
        else:
            row = await (await db.execute("""
                SELECT COUNT(DISTINCT guild_id) FROM (
                    SELECT guild_id FROM guild_config
                    UNION
                    SELECT guild_id FROM chzzk_subscriptions
                )
            """)).fetchone()
            guilds = int(row[0]) if row else 0

            row2 = await (await db.execute(
                "SELECT COUNT(*) FROM chzzk_subscriptions"
            )).fetchone()
            chzzk = int(row2[0]) if row2 else 0

        # 오늘 방문자 수 (KST 기준)
        today = today_kst()
        row3 = await (await db.execute(
            "SELECT COUNT(*) FROM daily_visitors WHERE date=?", (today,)
        )).fetchone()
        today_visitors = int(row3[0]) if row3 else 0

        return {"guilds": guilds, "chzzk_subscriptions": chzzk, "today_visitors": today_visitors}
    except Exception:
        return {"guilds": 0, "chzzk_subscriptions": 0, "today_visitors": 0}


@router.get("/announcement")
async def get_announcement():
    """메인 페이지 상단 공지 배너용 공개 엔드포인트. 인증 불필요."""
    try:
        db = await get_db()
        row = await (await db.execute(
            "SELECT message FROM site_announcement WHERE id=1"
        )).fetchone()
        return {"message": row["message"] if row else ""}
    except Exception:
        return {"message": ""}


@router.post("/visit")
async def record_visit(request: Request):
    """홈페이지 방문을 기록하고 오늘 고유 방문자 수를 반환합니다."""
    forwarded = request.headers.get("X-Forwarded-For")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host or "unknown")
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()
    today = today_kst()

    try:
        db = await get_db()
        await db.execute(
            "INSERT OR IGNORE INTO daily_visitors(date, ip_hash) VALUES(?,?)",
            (today, ip_hash)
        )
        await db.commit()
        row = await (await db.execute(
            "SELECT COUNT(*) FROM daily_visitors WHERE date=?", (today,)
        )).fetchone()
        return {"today_visitors": int(row[0]) if row else 0}
    except Exception:
        return {"today_visitors": 0}
