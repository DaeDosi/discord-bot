from fastapi import APIRouter
from database.db import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def get_stats():
    try:
        db = await get_db()

        # 모든 테이블에 등장하는 guild_id를 합산하여 서버 수 계산
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

        return {"guilds": guilds, "chzzk_subscriptions": chzzk}
    except Exception:
        return {"guilds": 0, "chzzk_subscriptions": 0}
