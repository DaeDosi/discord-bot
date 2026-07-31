import os
import asyncio
import httpx
from datetime import datetime, timezone
from database import DB_PATH, get_db
from utils.db_write import db_write_isolated

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
    pending: list[tuple[int, int]] = []
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

            pending.append((int(now_live), row["id"]))

        except Exception as e:
            _log(f"  오류 ({row['chzzk_channel_id']}): {e}")

    # 라이브 상태 반영은 **알림을 다 보낸 뒤 한 번에** 쓴다. 예전에는 채널마다
    # execute를 쌓아 두고 마지막에 COMMIT 하나였는데, 그 사이에 외부 API 호출과
    # 디스코드 전송이 끼어 있어 쓰기 트랜잭션이 그 시간만큼 열려 있었다.
    # 그 창이 같은 파일을 쓰는 봇 프로세스의 database is locked로 나타났다.
    #
    # **공유 연결이 아니라 전용 연결로 쓴다.** 공유 연결의 busy_timeout은 10초라,
    # 거기서 재시도하면 최악 4회 × 10초 + 백오프 = 실측 43.3초 동안 그 연결의
    # 작업 큐가 통째로 막힌다 — 공개 GET /main이 정확히 그 뒤에 줄을 선다.
    # 고치려던 증상을 오히려 키우는 셈이다. 전용 연결은 자기 큐에 앞선 작업이
    # 없어 예산(3초)이 곧 하드 상한이고, 실패해도 공유 연결은 멈추지 않는다.
    #
    # 쓰기 자체는 멱등이라 다음 폴링이 같은 값을 다시 쓴다. 다만 **끝내 저장하지
    # 못하면 방금 보낸 알림은 다시 나간다** — 다음 폴링이 DB의 옛 is_live를 보고
    # 같은 전이로 판정하기 때문이다. 이건 이 변경으로 생긴 게 아니라 원래 COMMIT이
    # 실패해도 같았고, 알림 중복이 '상태를 놓친 채 계속 도는 것'보다 낫다고 본다.
    if pending:
        async def _work(conn):
            await conn.executemany(
                "UPDATE chzzk_subscriptions SET is_live=? WHERE id=?", pending)

        if not await db_write_isolated(DB_PATH, _work, what="monitor_is_live",
                                       log=lambda p: _log(f"  {p}")):
            _log(f"  is_live 저장 실패 {len(pending)}건 — 다음 폴링에서 다시 씁니다")


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
