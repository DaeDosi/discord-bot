import os
import asyncio
import time
import httpx
from database import get_db

CHZZK_API     = "https://api.chzzk.naver.com"
DISCORD_API   = "https://discord.com/api/v10"
BOT_TOKEN     = os.getenv("DISCORD_TOKEN", "")
POLL_INTERVAL = int(os.getenv("CHZZK_POLL_INTERVAL", 60))

CHZZK_HEADERS   = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _discord_headers() -> dict:
    # BOT_TOKEN은 런타임에 읽어야 env 반영됨
    return {
        "Authorization": f"Bot {os.getenv('DISCORD_TOKEN', '')}",
        "Content-Type": "application/json",
    }


async def _fetch_live_detail(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}/live-detail"
    async with httpx.AsyncClient(headers=CHZZK_HEADERS, timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return resp.json().get("content")


async def _send_discord_message(channel_id: int, content: str, embed: dict):
    async with httpx.AsyncClient(headers=_discord_headers(), timeout=10) as client:
        await client.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            json={"content": content, "embeds": [embed]},
        )


async def _send_live_notification(row, live: dict):
    channel_info = live.get("channel", {})
    title     = live.get("liveTitle", "방송 시작!")
    category  = live.get("liveCategoryValue", "")
    viewers   = live.get("concurrentUserCount", 0)
    thumbnail = live.get("liveImageUrl", "") or ""
    avatar    = channel_info.get("channelImageUrl", "") or ""
    name      = channel_info.get("channelName") or row["chzzk_name"] or "알 수 없음"
    chzzk_url = f"https://chzzk.naver.com/live/{row['chzzk_channel_id']}"

    embed = {
        "title":       f"🔴 {name} 방송 시작!",
        "description": f"**[{title}]({chzzk_url})**",
        "url":         chzzk_url,
        "color":       0x03C75A,
        "fields": [
            {"name": "카테고리", "value": category or "없음", "inline": True},
            {"name": "시청자",   "value": f"{viewers:,}명",   "inline": True},
        ],
        "footer": {"text": "치지직 라이브 알림"},
    }
    if thumbnail:
        embed["image"] = {"url": f"{thumbnail}?t={int(time.time())}"}
    if avatar:
        embed["thumbnail"] = {"url": avatar}

    mention_id = row["mention_role_id"]
    if row["custom_message"]:
        content = row["custom_message"]
    elif mention_id:
        content = f"<@&{mention_id}> **{name}**님이 방송을 시작했습니다!"
    else:
        content = f"**{name}**님이 방송을 시작했습니다!"

    await _send_discord_message(row["discord_channel"], content, embed)


async def _send_offline_notification(row, live: dict):
    name  = row["chzzk_name"] or "알 수 없음"
    embed = {
        "title":  f"⚫ {name} 방송 종료",
        "color":  0x636E72,
        "footer": {"text": "치지직 라이브 알림"},
    }
    await _send_discord_message(row["discord_channel"], "", embed)


async def _check_once():
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, guild_id, discord_channel, chzzk_channel_id, chzzk_name, "
        "is_live, mention_role_id, custom_message FROM chzzk_subscriptions"
    )).fetchall()

    for row in rows:
        try:
            live = await _fetch_live_detail(row["chzzk_channel_id"])
            if live is None:
                continue

            now_live = live.get("status") == "OPEN"
            was_live = bool(row["is_live"])

            if now_live and not was_live:
                await _send_live_notification(row, live)
            elif not now_live and was_live:
                await _send_offline_notification(row, live)

            await db.execute(
                "UPDATE chzzk_subscriptions SET is_live=? WHERE id=?",
                (int(now_live), row["id"]),
            )
        except Exception:
            continue

    await db.commit()


async def start_monitor():
    """FastAPI lifespan에서 asyncio.create_task()로 호출"""
    if not os.getenv("DISCORD_TOKEN"):
        print("[chzzk_monitor] DISCORD_TOKEN 없음 — 모니터링 비활성화")
        return
    print(f"[chzzk_monitor] 시작 (폴링 간격: {POLL_INTERVAL}초)")
    while True:
        try:
            await _check_once()
        except Exception as e:
            print(f"[chzzk_monitor] 오류: {e}")
        await asyncio.sleep(POLL_INTERVAL)
