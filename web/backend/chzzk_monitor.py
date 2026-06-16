import os
import asyncio
import httpx
from datetime import datetime, timezone
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


async def _fetch_channel_info(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}"
    async with httpx.AsyncClient(headers=CHZZK_HEADERS, timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return resp.json().get("content")


async def _fetch_live_detail(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v2/channels/{chzzk_id}/live-detail"
    async with httpx.AsyncClient(headers=CHZZK_HEADERS, timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        content = resp.json().get("content")
        if content and content.get("liveImageUrl"):
            img = content["liveImageUrl"]
            img = img.replace("_{type}", "_1080")
            img = img.replace("%7Btype%7D", "1280x720")
            img = img.replace("{type}", "1280x720")
            content["liveImageUrl"] = img
        return content


async def _send_discord_message(
    channel_id: int,
    content: str,
    embed: dict,
    button_label: str = "방송 바로가기",
) -> str | None:
    link_url = embed.get("url", "")
    payload: dict = {
        "embeds": [embed],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type":  2,
                        "style": 5,
                        "label": button_label,
                        "url":   link_url,
                    }
                ],
            }
        ] if link_url else [],
    }
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


async def _send_live_notification(row, live: dict, info: dict):
    channel_info = live.get("channel") or {}
    title     = live.get("liveTitle") or "방송 중"
    category  = live.get("liveCategoryValue") or "없음"
    thumbnail = live.get("liveImageUrl") or ""
    name      = channel_info.get("channelName") or info.get("channelName") or row["chzzk_name"] or "알 수 없음"
    chzzk_url = f"https://chzzk.naver.com/live/{row['chzzk_channel_id']}"
    now_iso   = datetime.now(timezone.utc).isoformat()

    avatar = (live.get("channel") or {}).get("channelImageUrl") or info.get("channelImageUrl") or ""
    author: dict = {"name": name, "url": chzzk_url}
    if avatar:
        author["icon_url"] = avatar

    embed: dict = {
        "author":      author,
        "title":       title,
        "url":         chzzk_url,
        "description": f"[{name}]님이 방송을 시작했습니다.",
        "color":       0x00FFA3,
        "fields": [{"name": "카테고리", "value": category, "inline": False}],
        "timestamp": now_iso,
    }
    if thumbnail:
        embed["image"] = {"url": thumbnail}

    mention = "@everyone " if bool(row["mention_everyone"]) else ""
    content = f"{mention}[{name}]님이 방송을 시작했습니다!"

    err = await _send_discord_message(row["discord_channel"], content, embed)
    if err:
        _log(f"라이브 알림 전송 실패 ({name}): {err}")
    else:
        _log(f"라이브 알림 전송 완료: {name}")


async def _send_offline_notification(row, info: dict):
    name    = info.get("channelName") or row["chzzk_name"] or "알 수 없음"
    now_iso = datetime.now(timezone.utc).isoformat()
    embed = {
        "title":     f"[{name}]님이 방송을 종료했습니다.",
        "color":     0x636E72,
        "timestamp": now_iso,
        "url":       "",
    }
    err = await _send_discord_message(row["discord_channel"], "", embed)
    if err:
        _log(f"종료 알림 전송 실패 ({name}): {err}")
    else:
        _log(f"종료 알림 전송 완료: {name}")


async def check_once_debug() -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, guild_id, discord_channel, chzzk_channel_id, chzzk_name, "
        "is_live, mention_everyone FROM chzzk_subscriptions"
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
            info = await _fetch_channel_info(row["chzzk_channel_id"])
            if info is None:
                entry["error"] = "채널 정보 없음"
            else:
                now_live = bool(info.get("openLive", False))
                entry["open_live"]   = now_live
                entry["api_is_live"] = now_live
                if now_live:
                    detail = await _fetch_live_detail(row["chzzk_channel_id"])
                    entry["live_title"] = detail.get("liveTitle") if detail else None
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)

    return results


async def _check_once():
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, guild_id, discord_channel, chzzk_channel_id, chzzk_name, "
        "is_live, mention_everyone "
        "FROM chzzk_subscriptions"
    )).fetchall()

    if not rows:
        return

    _log(f"구독 {len(rows)}개 체크 중...")
    for row in rows:
        try:
            name = row["chzzk_name"] or row["chzzk_channel_id"]
            info = await _fetch_channel_info(row["chzzk_channel_id"])
            if info is None:
                continue

            now_live = bool(info.get("openLive", False))
            was_live = bool(row["is_live"])

            _log(f"  {name}: DB={was_live} openLive={now_live}")

            if now_live and not was_live:
                detail = await _fetch_live_detail(row["chzzk_channel_id"]) or {}
                await _send_live_notification(row, detail, info)
            elif not now_live and was_live:
                await _send_offline_notification(row, info)

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
