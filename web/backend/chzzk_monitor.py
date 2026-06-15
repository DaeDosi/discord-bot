import os
import re
import json as _json
import asyncio
import httpx
from datetime import datetime, timezone
from database import get_db

CHZZK_API     = "https://api.chzzk.naver.com"
DISCORD_API   = "https://discord.com/api/v10"
POLL_INTERVAL = int(os.getenv("CHZZK_POLL_INTERVAL", 60))

CHZZK_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def _naver_cookie() -> str:
    nid_aut = os.getenv("NAVER_NID_AUT", "")
    nid_ses = os.getenv("NAVER_NID_SES", "")
    parts = []
    if nid_aut:
        parts.append(f"NID_AUT={nid_aut}")
    if nid_ses:
        parts.append(f"NID_SES={nid_ses}")
    return "; ".join(parts)


def _log(msg: str):
    print(f"[chzzk_monitor] {msg}", flush=True)


def _discord_headers() -> dict:
    return {
        "Authorization": f"Bot {os.getenv('DISCORD_TOKEN', '')}",
        "Content-Type": "application/json",
    }


# ── Chzzk API fetch ───────────────────────────────────────────────────────────

async def _fetch_channel_info(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}"
    async with httpx.AsyncClient(headers=CHZZK_HEADERS, timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            _log(f"채널 정보 오류 ({chzzk_id}): HTTP {resp.status_code}")
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


async def _fetch_latest_video(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}/videos"
    params = {"sortType": "RECENT", "size": 1, "page": 0}
    async with httpx.AsyncClient(headers=CHZZK_HEADERS, timeout=10) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            _log(f"VOD fetch 오류 ({chzzk_id}): HTTP {resp.status_code}")
            return None
        data = resp.json()
        videos = data.get("content", {}).get("data", [])
        return videos[0] if videos else None


async def _fetch_latest_clip(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}/clips"
    params = {"sortType": "RECENT", "size": 1}
    async with httpx.AsyncClient(headers=CHZZK_HEADERS, timeout=10) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            _log(f"클립 fetch 오류 ({chzzk_id}): HTTP {resp.status_code}")
            return None
        data = resp.json()
        clips = data.get("content", {}).get("data", [])
        return clips[0] if clips else None


async def _fetch_latest_post(chzzk_id: str) -> dict | None:
    cookie = _naver_cookie()
    if not cookie:
        _log("NAVER_NID_AUT / NAVER_NID_SES 환경변수 없음 — 커뮤니티 체크 건너뜀")
        return None

    url = (
        "https://apis.naver.com/nng_main/nng_comment_api/v1"
        f"/type/CHANNEL_POST/id/{chzzk_id}/comments"
    )
    params = {"limit": 1, "offset": 0, "orderType": "DESC", "pagingType": "PAGE"}
    headers = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept":          "application/json",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin":          "https://chzzk.naver.com",
        "Referer":         f"https://chzzk.naver.com/{chzzk_id}/community",
        "Cookie":          cookie,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(url, params=params, headers=headers)
            _log(f"커뮤니티 API → HTTP {resp.status_code}")
            if resp.status_code != 200:
                _log(f"커뮤니티 오류: {resp.text[:200]}")
                return None
            data = resp.json()
            comments_data = (
                data.get("content", {}).get("comments", {}).get("data", [])
            )
            if not comments_data:
                _log("커뮤니티 게시글 없음")
                return None
            comment = comments_data[0].get("comment", comments_data[0])
            _log(f"커뮤니티 성공! commentId={comment.get('commentId')}")
            return comment
        except Exception as e:
            _log(f"커뮤니티 오류: {e}")
            return None


# ── Discord 메시지 전송 ──────────────────────────────────────────────────────

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


# ── 알림 전송 함수들 ──────────────────────────────────────────────────────────

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

    _log(f"  썸네일 URL: {thumbnail!r}")

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

    err = await _send_discord_message(row["discord_channel"], content, embed, "방송 바로가기")
    if err:
        _log(f"라이브 알림 전송 실패 ({name}): {err}")
    else:
        _log(f"라이브 알림 전송 완료: {name} → ch={row['discord_channel']}")


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


async def _send_video_notification(row, video: dict):
    name      = row["chzzk_name"] or "알 수 없음"
    title     = video.get("videoTitle") or "새 다시보기"
    vid_no    = video.get("videoNo", "")
    thumbnail = video.get("thumbnailImageUrl") or ""
    video_url = f"https://chzzk.naver.com/video/{vid_no}" if vid_no else ""
    now_iso   = datetime.now(timezone.utc).isoformat()

    embed: dict = {
        "title":       title,
        "url":         video_url,
        "description": f"**{name}**님이 새 다시보기 영상을 업로드했습니다.",
        "color":       0x03C75A,
        "timestamp":   now_iso,
    }
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}

    ch_id = row["vod_channel"] or row["discord_channel"]
    err = await _send_discord_message(ch_id, "", embed, "영상 바로가기")
    if err:
        _log(f"VOD 알림 전송 실패 ({name}): {err}")
    else:
        _log(f"VOD 알림 전송 완료: {name} '{title}'")


async def _send_clip_notification(row, clip: dict):
    name      = row["chzzk_name"] or "알 수 없음"
    title     = clip.get("clipTitle") or "새 클립"
    clip_no   = clip.get("clipNo") or clip.get("clipUID") or ""
    thumbnail = clip.get("thumbnailImageUrl") or ""
    clip_url  = f"https://chzzk.naver.com/clips/{clip_no}" if clip_no else ""
    now_iso   = datetime.now(timezone.utc).isoformat()

    embed: dict = {
        "title":       title,
        "url":         clip_url,
        "description": f"**{name}**님이 새 클립을 등록했습니다.",
        "color":       0x03C75A,
        "timestamp":   now_iso,
    }
    if thumbnail:
        embed["thumbnail"] = {"url": thumbnail}

    ch_id = row["clip_channel"] or row["discord_channel"]
    err = await _send_discord_message(ch_id, "", embed, "클립 바로가기")
    if err:
        _log(f"클립 알림 전송 실패 ({name}): {err}")
    else:
        _log(f"클립 알림 전송 완료: {name} '{title}'")


async def _send_post_notification(row, post: dict):
    name    = row["chzzk_name"] or "알 수 없음"
    post_no = str(post.get("commentId") or post.get("postNo") or post.get("id") or "")

    # content 필드가 문자열 (게시글 본문)
    content_text = post.get("content") or ""
    if isinstance(content_text, dict):
        title = content_text.get("title") or content_text.get("text") or "새 커뮤니티 게시글"
    elif isinstance(content_text, str) and content_text:
        title = content_text[:80] + ("..." if len(content_text) > 80 else "")
    else:
        title = "새 커뮤니티 게시글"

    channel_id_str = row["chzzk_channel_id"]
    post_url = f"https://chzzk.naver.com/{channel_id_str}/community/detail/{post_no}" if post_no else ""
    now_iso  = datetime.now(timezone.utc).isoformat()

    embed: dict = {
        "title":       title,
        "url":         post_url,
        "description": f"**{name}**님이 새 커뮤니티 게시글을 작성했습니다.",
        "color":       0x03C75A,
        "timestamp":   now_iso,
    }

    ch_id = row["community_channel"] or row["discord_channel"]
    err = await _send_discord_message(ch_id, "", embed, "게시글 바로가기")
    if err:
        _log(f"커뮤니티 알림 전송 실패 ({name}): {err}")
    else:
        _log(f"커뮤니티 알림 전송 완료: {name} '{title}'")


# ── 메인 체크 루프 ────────────────────────────────────────────────────────────

async def check_once_debug() -> list[dict]:
    """디버그용: 현재 상태를 체크하고 결과 반환 (DB 업데이트 없음)"""
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
                entry["error"] = "채널 정보 없음 (채널 ID 확인 필요)"
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
        "is_live, mention_everyone, "
        "notify_vod, notify_clip, notify_community, "
        "last_vod_id, last_clip_id, last_post_id, "
        "vod_channel, clip_channel, community_channel "
        "FROM chzzk_subscriptions"
    )).fetchall()

    if not rows:
        _log("구독 없음 — 건너뜀")
        return

    _log(f"구독 {len(rows)}개 체크 중...")
    for row in rows:
        try:
            name = row["chzzk_name"] or row["chzzk_channel_id"]

            # ── 라이브 체크 ───────────────────────────────────────────────
            info = await _fetch_channel_info(row["chzzk_channel_id"])
            if info is None:
                _log(f"  채널 정보 없음, 건너뜀: {name}")
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

            # ── VOD 체크 ─────────────────────────────────────────────────
            if bool(row["notify_vod"]):
                try:
                    video = await _fetch_latest_video(row["chzzk_channel_id"])
                    if video:
                        vid_id = str(video.get("videoNo") or "")
                        if vid_id:
                            if row["last_vod_id"] and vid_id != row["last_vod_id"]:
                                await _send_video_notification(row, video)
                            if vid_id != (row["last_vod_id"] or ""):
                                await db.execute(
                                    "UPDATE chzzk_subscriptions SET last_vod_id=? WHERE id=?",
                                    (vid_id, row["id"]),
                                )
                except Exception as e:
                    _log(f"  VOD 체크 오류 ({name}): {e}")

            # ── 클립 체크 ─────────────────────────────────────────────────
            if bool(row["notify_clip"]):
                try:
                    clip = await _fetch_latest_clip(row["chzzk_channel_id"])
                    if clip:
                        clip_id = str(clip.get("clipNo") or clip.get("clipUID") or "")
                        if clip_id:
                            if row["last_clip_id"] and clip_id != row["last_clip_id"]:
                                await _send_clip_notification(row, clip)
                            if clip_id != (row["last_clip_id"] or ""):
                                await db.execute(
                                    "UPDATE chzzk_subscriptions SET last_clip_id=? WHERE id=?",
                                    (clip_id, row["id"]),
                                )
                except Exception as e:
                    _log(f"  클립 체크 오류 ({name}): {e}")

            # ── 커뮤니티 게시글 체크 ──────────────────────────────────────
            if bool(row["notify_community"]):
                try:
                    post = await _fetch_latest_post(row["chzzk_channel_id"])
                    if post:
                        post_id = str(
                            post.get("commentId")
                            or post.get("postNo")
                            or post.get("id")
                            or ""
                        )
                        if post_id:
                            if row["last_post_id"] and post_id != str(row["last_post_id"]):
                                await _send_post_notification(row, post)
                            if post_id != str(row["last_post_id"] or ""):
                                await db.execute(
                                    "UPDATE chzzk_subscriptions SET last_post_id=? WHERE id=?",
                                    (post_id, row["id"]),
                                )
                        else:
                            _log(f"  커뮤니티 post_id 없음 ({name})")
                except Exception as e:
                    _log(f"  커뮤니티 체크 오류 ({name}): {e}")

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
