import os
import re
import time
import uuid
import json as _json
import asyncio
import httpx
from datetime import datetime, timezone
from database import get_db

# 서버 고유 device ID (Chzzk front-client 식별용)
_DEVICE_ID = str(uuid.uuid4())

# NID_AUT → NID_SES 갱신 캐시 (30분)
_nid_ses_cache: dict = {"value": "", "expires": 0.0}

CHZZK_API     = "https://api.chzzk.naver.com"
DISCORD_API   = "https://discord.com/api/v10"
POLL_INTERVAL = int(os.getenv("CHZZK_POLL_INTERVAL", 60))

CHZZK_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def _get_nid_aut() -> str:
    """환경변수에서 NID_AUT 추출 (NAVER_COOKIE 우선, NAVER_NID_AUT 폴백)"""
    full = os.getenv("NAVER_COOKIE", "")
    m = re.search(r'NID_AUT=([^;]+)', full)
    if m:
        return m.group(1).strip()
    return os.getenv("NAVER_NID_AUT", "").strip()


def _get_ba_uuid() -> str:
    full = os.getenv("NAVER_COOKIE", "")
    m = re.search(r'ba\.uuid=([^;]+)', full)
    return m.group(1).strip() if m else _DEVICE_ID


async def _get_fresh_nid_ses() -> str:
    """NID_AUT로 이 서버 IP에서 새 NID_SES를 발급받아 캐시 (30분)."""
    global _nid_ses_cache
    if _nid_ses_cache["value"] and time.time() < _nid_ses_cache["expires"]:
        return _nid_ses_cache["value"]

    nid_aut = _get_nid_aut()
    if not nid_aut:
        return ""

    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/125.0.0.0 Safari/537.36")
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        await client.get("https://www.naver.com/", headers={
            "User-Agent":    ua,
            "Accept":        "text/html",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Cookie":        f"NID_AUT={nid_aut}",
        })
        nid_ses = client.cookies.get("NID_SES")

    if nid_ses:
        _nid_ses_cache["value"]   = nid_ses
        _nid_ses_cache["expires"] = time.time() + 1800
        _log(f"Naver 세션 갱신 성공 (NID_SES 앞 10자: {nid_ses[:10]}…)")
    else:
        _log("Naver 세션 갱신 실패 — NID_SES 미획득")

    return _nid_ses_cache["value"]


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


async def _get_streamer_token(chzzk_id: str) -> str | None:
    """DB에서 스트리머 OAuth 액세스 토큰 조회. 만료 임박 시 갱신."""
    client_id     = os.getenv("CHZZK_CLIENT_ID", "")
    client_secret = os.getenv("CHZZK_CLIENT_SECRET", "")
    try:
        db  = await get_db()
        sub = await (await db.execute(
            "SELECT streamer_access_token, streamer_refresh_token, streamer_token_expires_at "
            "FROM chzzk_subscriptions "
            "WHERE chzzk_channel_id=? AND streamer_access_token IS NOT NULL "
            "ORDER BY streamer_token_expires_at DESC LIMIT 1",
            (chzzk_id,)
        )).fetchone()
        if not sub or not sub["streamer_access_token"]:
            return None

        expires_at = sub["streamer_token_expires_at"] or 0
        if expires_at > int(time.time()) + 300:
            return sub["streamer_access_token"]

        # 만료 임박 → refresh
        if not sub["streamer_refresh_token"] or not client_id:
            return sub["streamer_access_token"]  # 그냥 현재 토큰 사용

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://openapi.chzzk.naver.com/auth/v1/token",
                json={
                    "grantType":    "refresh_token",
                    "clientId":     client_id,
                    "clientSecret": client_secret,
                    "refreshToken": sub["streamer_refresh_token"],
                },
            )
        if resp.status_code == 200:
            c  = resp.json().get("content", {})
            at = c.get("accessToken")
            rt = c.get("refreshToken") or sub["streamer_refresh_token"]
            ei = c.get("expiresIn", 86400)
            if at:
                new_exp = int(time.time()) + ei
                await db.execute(
                    "UPDATE chzzk_subscriptions SET streamer_access_token=?, streamer_refresh_token=?, streamer_token_expires_at=? WHERE chzzk_channel_id=?",
                    (at, rt, new_exp, chzzk_id),
                )
                await db.commit()
                _log(f"스트리머 토큰 갱신 완료 ({chzzk_id})")
                return at

        return sub["streamer_access_token"]
    except Exception as e:
        _log(f"스트리머 토큰 조회 오류: {e}")
        return None


async def _fetch_latest_post(chzzk_id: str) -> dict | None:
    # ── 1차 시도: NNG 공개 API (쿠키 불필요, 브라우저 헤더 스푸핑) ────────────
    # 참조: github.com/azestkingscrown/Chzzk-Streammer-Noti-Discord-Bot
    nng_url = (
        "https://apis.naver.com/nng_main/nng_comment_api/v1"
        f"/type/CHANNEL_POST/id/{chzzk_id}/comments"
    )
    nng_headers = {
        "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type":  "application/xml",
        "Referer":       f"https://chzzk.naver.com/{chzzk_id}/community",
        "Origin":        "https://chzzk.naver.com",
        "Accept":        "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    nng_params = {"limit": 1, "offset": 0, "orderType": "DESC", "pagingType": "PAGE"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(nng_url, params=nng_params, headers=nng_headers)
        _log(f"커뮤니티 NNG → HTTP {resp.status_code} {resp.text[:150].replace(chr(10), ' ')}")
        if resp.status_code == 200:
            data     = resp.json()
            comments = data.get("content", {}).get("comments", {}).get("data", [])
            if comments:
                c = comments[0]
                return {
                    "commentId":       c["comment"].get("commentId"),
                    "content":         c["comment"].get("content", ""),
                    "userNickname":    c["user"].get("userNickname", ""),
                    "profileImageUrl": c["user"].get("profileImageUrl"),
                    "attaches":        c["comment"].get("attaches"),
                }
            _log(f"커뮤니티 NNG → 200 but 게시글 없음")
            return None
    except Exception as e:
        _log(f"커뮤니티 NNG 오류: {e}")

    _log(f"커뮤니티: 모든 엔드포인트 실패 ({chzzk_id})")
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
    post_no = str(post.get("postNo") or post.get("communityPostNo") or post.get("commentId") or post.get("id") or "")

    content_text = post.get("content") or ""
    if isinstance(content_text, dict):
        title = content_text.get("title") or content_text.get("text") or "새 커뮤니티 게시글"
    elif isinstance(content_text, str) and content_text:
        title = content_text[:80] + ("..." if len(content_text) > 80 else "")
    else:
        title = "새 커뮤니티 게시글"

    author_nick = post.get("userNickname") or name
    channel_id_str = row["chzzk_channel_id"]
    post_url = f"https://chzzk.naver.com/{channel_id_str}/community/detail/{post_no}" if post_no else ""
    now_iso  = datetime.now(timezone.utc).isoformat()

    embed: dict = {
        "title":       title,
        "url":         post_url,
        "description": f"**{author_nick}**님이 새 커뮤니티 게시글을 작성했습니다.",
        "color":       0x03C75A,
        "timestamp":   now_iso,
        "author":      {"name": name},
    }

    # 첨부 이미지 (NNG attaches 필드)
    attaches = post.get("attaches")
    if attaches and isinstance(attaches, list) and attaches:
        img_url = attaches[0].get("attachValue")
        if img_url:
            embed["image"] = {"url": img_url}

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

            _log(f"  {name}: DB={was_live} openLive={now_live} | vod={bool(row['notify_vod'])} clip={bool(row['notify_clip'])} community={bool(row['notify_community'])}")

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
                            post.get("postNo")
                            or post.get("communityPostNo")
                            or post.get("commentId")
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
