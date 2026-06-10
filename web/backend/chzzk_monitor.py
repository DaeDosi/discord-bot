import os
import asyncio
import time
import httpx
from database import get_db

CHZZK_API     = "https://api.chzzk.naver.com"
DISCORD_API   = "https://discord.com/api/v10"
POLL_INTERVAL = int(os.getenv("CHZZK_POLL_INTERVAL", 60))

CHZZK_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _log(msg: str):
    print(f"[chzzk_monitor] {msg}", flush=True)


def _discord_headers() -> dict:
    return {
        "Authorization": f"Bot {os.getenv('DISCORD_TOKEN', '')}",
        "Content-Type": "application/json",
    }


async def _fetch_live_detail(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}/live-detail"
    async with httpx.AsyncClient(headers=CHZZK_HEADERS, timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            _log(f"치지직 API 오류 ({chzzk_id}): HTTP {resp.status_code}")
            return None
        content = resp.json().get("content")
        if content is None:
            _log(f"치지직 응답에 content 없음 ({chzzk_id}): {resp.text[:200]}")
        return content


async def _send_discord_message(channel_id: int, content: str, embed: dict) -> str | None:
    """메시지 전송 후 오류 문자열 반환 (성공 시 None)"""
    payload: dict = {"embeds": [embed]}
    if content:
        payload["content"] = content
    async with httpx.AsyncClient(headers=_discord_headers(), timeout=10) as client:
        resp = await client.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            json=payload,
        )
        if resp.status_code not in (200, 201):
            return f"Discord API {resp.status_code}: {resp.text[:300]}"
        return None


async def _send_live_notification(row, live: dict):
    channel_info = live.get("channel", {})
    title     = live.get("liveTitle", "방송 시작!")
    category  = live.get("liveCategoryValue", "")
    viewers   = live.get("concurrentUserCount", 0)
    thumbnail = live.get("liveImageUrl") or ""
    avatar    = channel_info.get("channelImageUrl") or ""
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

    err = await _send_discord_message(row["discord_channel"], content, embed)
    if err:
        _log(f"라이브 알림 전송 실패 ({name}, ch={row['discord_channel']}): {err}")
    else:
        _log(f"라이브 알림 전송 완료: {name} → ch={row['discord_channel']}")


async def _send_offline_notification(row, live: dict):
    name  = row["chzzk_name"] or "알 수 없음"
    embed = {
        "title":  f"⚫ {name} 방송 종료",
        "color":  0x636E72,
        "footer": {"text": "치지직 라이브 알림"},
    }
    err = await _send_discord_message(row["discord_channel"], "", embed)
    if err:
        _log(f"종료 알림 전송 실패 ({name}): {err}")
    else:
        _log(f"종료 알림 전송 완료: {name}")


async def check_once_debug() -> list[dict]:
    """디버그용: 현재 상태를 체크하고 결과 반환 (DB 업데이트 없음)"""
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, guild_id, discord_channel, chzzk_channel_id, chzzk_name, "
        "is_live, mention_role_id, custom_message FROM chzzk_subscriptions"
    )).fetchall()

    results = []
    for row in rows:
        entry: dict = {
            "id":              row["id"],
            "chzzk_name":      row["chzzk_name"],
            "chzzk_id":        row["chzzk_channel_id"],
            "discord_channel": row["discord_channel"],
            "db_is_live":      bool(row["is_live"]),
        }
        try:
            live = await _fetch_live_detail(row["chzzk_channel_id"])
            if live is None:
                entry["error"] = "치지직 API 응답 없음"
            else:
                entry["api_status"] = live.get("status")
                entry["api_is_live"] = live.get("status") == "OPEN"
                entry["live_title"]  = live.get("liveTitle")
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)

    return results


async def _check_once():
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, guild_id, discord_channel, chzzk_channel_id, chzzk_name, "
        "is_live, mention_role_id, custom_message FROM chzzk_subscriptions"
    )).fetchall()

    if not rows:
        _log("구독 없음 — 건너뜀")
        return

    _log(f"구독 {len(rows)}개 체크 중...")
    for row in rows:
        try:
            live = await _fetch_live_detail(row["chzzk_channel_id"])
            if live is None:
                continue

            now_live = live.get("status") == "OPEN"
            was_live = bool(row["is_live"])
            name     = row["chzzk_name"] or row["chzzk_channel_id"]

            _log(f"  {name}: DB={was_live} API={now_live} (status={live.get('status')!r})")

            if now_live and not was_live:
                await _send_live_notification(row, live)
            elif not now_live and was_live:
                await _send_offline_notification(row, live)

            await db.execute(
                "UPDATE chzzk_subscriptions SET is_live=? WHERE id=?",
                (int(now_live), row["id"]),
            )
        except Exception as e:
            _log(f"  오류 ({row['chzzk_channel_id']}): {e}")

    await db.commit()


async def start_monitor():
    if not os.getenv("DISCORD_TOKEN"):
        _log("DISCORD_TOKEN 없음 — 모니터링 비활성화")
        return
    _log(f"시작 (폴링 간격: {POLL_INTERVAL}초)")
    while True:
        try:
            await _check_once()
        except Exception as e:
            _log(f"루프 오류: {e}")
        await asyncio.sleep(POLL_INTERVAL)
