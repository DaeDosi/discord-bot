import os
import json
import time
import secrets
import traceback
import httpx
from urllib.parse import urlencode, quote
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from jose import jwt, JWTError
from auth import JWT_SECRET, JWT_ALGORITHM, FRONTEND_URL
from database.db import get_db
from routers.verify_router import _add_role, _remove_role

router = APIRouter(prefix="/api/chzzk-auth", tags=["chzzk-auth"])

CHZZK_CLIENT_ID     = os.getenv("CHZZK_CLIENT_ID", "")
CHZZK_CLIENT_SECRET = os.getenv("CHZZK_CLIENT_SECRET", "")
CHZZK_REDIRECT_URI  = os.getenv(
    "CHZZK_REDIRECT_URI",
    "http://localhost:8000/api/chzzk-auth/callback",
)

CHZZK_AUTH_URL  = "https://chzzk.naver.com/account-interlock"
CHZZK_TOKEN_URL = "https://openapi.chzzk.naver.com/auth/v1/token"
CHZZK_USER_URL  = "https://openapi.chzzk.naver.com/open/v1/users/me"
DISCORD_API     = "https://discord.com/api/v10"
_BOT_TOKEN      = os.getenv("DISCORD_TOKEN", "")


async def _get_chzzk_channel_name(access_token: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                CHZZK_USER_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Client-Id": CHZZK_CLIENT_ID,
                    "Accept": "application/json",
                },
            )
            print(f"[chzzk-auth] users/me status={resp.status_code} body={resp.text[:400]}")
            if resp.status_code == 200:
                data    = resp.json()
                content = data.get("content") or data
                name    = content.get("nickname") or content.get("channelName")
                print(f"[chzzk-auth] nickname={name!r}")
                return name or None
    except Exception as e:
        print(f"[chzzk-auth] users/me error type={type(e).__name__} repr={repr(e)}")
        traceback.print_exc()
    return None


async def _set_discord_nickname(guild_id: str, user_id: str, nickname: str) -> None:
    url = f"{DISCORD_API}/guilds/{guild_id}/members/{user_id}"
    headers = {
        "Authorization": f"Bot {_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.patch(
                url, headers=headers, content=json.dumps({"nick": nickname})
            )
            print(f"[chzzk-auth] Discord PATCH nick status={resp.status_code} body={resp.text[:300]}")
    except Exception as e:
        print(f"[chzzk-auth] Discord PATCH nick error: {e}")


CHZZK_SUBSCRIPTION_URL = "https://openapi.chzzk.naver.com/open/v1/channels/{channel_id}/subscriptions/me"
CHZZK_FOLLOW_URL       = "https://openapi.chzzk.naver.com/open/v1/channels/{channel_id}/follows/me"


def _months_since(date_str: str) -> int:
    """ISO8601 날짜 문자열 → 현재까지 개월 수 (30일 기준)."""
    from datetime import timezone
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta_days = (datetime.now(timezone.utc) - dt).days
        return max(0, int(delta_days / 30))
    except Exception:
        return 0


async def _get_follow_months(channel_id: str, access_token: str) -> int:
    """팔로우 날짜 기반 개월 수 반환. 실패 시 0."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": CHZZK_CLIENT_ID,
        "Accept": "application/json",
    }
    try:
        url = CHZZK_FOLLOW_URL.format(channel_id=channel_id)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
            print(f"[chzzk-auth] follow check status={resp.status_code} body={resp.text[:300]}")
            if resp.status_code == 200:
                content = resp.json().get("content") or {}
                date_str = (
                    content.get("followDate")
                    or content.get("createdAt")
                    or content.get("followedAt")
                    or content.get("followingDate")
                )
                if date_str:
                    months = _months_since(date_str)
                    print(f"[chzzk-auth] follow date={date_str} → {months} months")
                    return months
    except Exception as e:
        print(f"[chzzk-auth] follow check error: {repr(e)}")
    return 0


async def _get_subscription_months(channel_id: str, access_token: str) -> int:
    """유료 구독 개월 수 반환 (없으면 0)."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": CHZZK_CLIENT_ID,
        "Accept": "application/json",
    }
    try:
        url = CHZZK_SUBSCRIPTION_URL.format(channel_id=channel_id)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                content = resp.json().get("content") or {}
                return int(content.get("tierMonths") or content.get("month") or 0)
    except Exception:
        pass
    return 0


async def _get_tier_months(guild_id: str, access_token: str) -> int:
    """팔로우 날짜 기반 개월 수. 실패 시 구독 개월 수로 fallback."""
    db = await get_db()
    sub = await (await db.execute(
        "SELECT chzzk_channel_id FROM chzzk_subscriptions WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone()
    if not sub:
        return 0
    channel_id = sub["chzzk_channel_id"]

    months = await _get_follow_months(channel_id, access_token)
    if months > 0:
        return months
    # fallback: 유료 구독 개월
    return await _get_subscription_months(channel_id, access_token)


async def _get_chzzk_channel_info(access_token: str) -> dict:
    """로그인한 치지직 유저의 채널 정보를 반환."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                CHZZK_USER_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Client-Id": CHZZK_CLIENT_ID,
                    "Accept": "application/json",
                },
            )
            if resp.status_code == 200:
                data    = resp.json()
                content = data.get("content") or data
                return {
                    "channelId":       content.get("channelId") or content.get("userIdHash"),
                    "channelName":     content.get("nickname") or content.get("channelName") or "",
                    "channelImageUrl": content.get("profileImageUrl") or content.get("channelImageUrl") or "",
                }
    except Exception as e:
        print(f"[chzzk-auth] _get_chzzk_channel_info error: {e}")
    return {}


async def _handle_streamer_registration(
    state_data: dict,
    access_token: str,
    guild_id: str,
) -> RedirectResponse:
    discord_channel  = state_data.get("discord_channel", "")
    mention_everyone = int(state_data.get("mention_everyone", 0))
    dashboard_url    = f"{FRONTEND_URL}/dashboard/{guild_id}/chzzk"

    channel_info = await _get_chzzk_channel_info(access_token)
    if not channel_info.get("channelId"):
        return RedirectResponse(f"{dashboard_url}?error=chzzk_channel_not_found")

    # 채널 상세 정보 (라이브 여부 포함)
    chzzk_id   = channel_info["channelId"]
    chzzk_name = channel_info["channelName"]
    image_url  = channel_info["channelImageUrl"]

    try:
        db = await get_db()
        count = (await (await db.execute(
            "SELECT COUNT(*) FROM chzzk_subscriptions WHERE guild_id=?",
            (int(guild_id),)
        )).fetchone())[0]
        if count >= 1:
            return RedirectResponse(f"{dashboard_url}?error=already_subscribed")

        await db.execute(
            """INSERT INTO chzzk_subscriptions
               (guild_id, discord_channel, chzzk_channel_id, chzzk_name, chzzk_image_url, mention_everyone, is_live)
               VALUES (?,?,?,?,?,?,0)""",
            (int(guild_id), int(discord_channel), chzzk_id, chzzk_name, image_url, mention_everyone),
        )
        await db.commit()
        print(f"[chzzk-auth] streamer registered: guild={guild_id} channel={chzzk_name}({chzzk_id})")
    except Exception as e:
        print(f"[chzzk-auth] streamer DB insert failed: {e}")
        return RedirectResponse(f"{dashboard_url}?error=db_error")

    return RedirectResponse(f"{dashboard_url}?success=streamer_added")


async def _assign_follower_roles(guild_id: str, discord_user_id: str, access_token: str) -> None:
    db = await get_db()

    # 새 다중 티어 테이블 우선 사용
    tiers = await (await db.execute(
        "SELECT months, role_id FROM chzzk_follow_roles WHERE guild_id=? ORDER BY months ASC",
        (int(guild_id),)
    )).fetchall()

    if not tiers:
        # 구버전 fallback: follow_role_1month / follow_role_3month
        sub = await (await db.execute(
            "SELECT chzzk_channel_id, follow_role_1month, follow_role_3month, "
            "follow_months_tier1, follow_months_tier2 "
            "FROM chzzk_subscriptions WHERE guild_id=?",
            (int(guild_id),)
        )).fetchone()
        if not sub or (not sub["follow_role_1month"] and not sub["follow_role_3month"]):
            return
        months = await _get_tier_months(guild_id, access_token)
        t1 = int(sub["follow_months_tier1"] or 1)
        t2 = int(sub["follow_months_tier2"] or 3)
        if months >= t2 and sub["follow_role_3month"]:
            await _add_role(guild_id, discord_user_id, str(sub["follow_role_3month"]))
        elif months >= t1 and sub["follow_role_1month"]:
            await _add_role(guild_id, discord_user_id, str(sub["follow_role_1month"]))
        return

    months = await _get_tier_months(guild_id, access_token)
    print(f"[chzzk-auth] follow months={months}, tiers={[(t['months'], t['role_id']) for t in tiers]}")
    for tier in tiers:
        if months >= tier["months"]:
            await _add_role(guild_id, discord_user_id, str(tier["role_id"]))
            print(f"[chzzk-auth] assigned follow role {tier['role_id']} ({tier['months']}개월+)")


def _build_state(guild_id: str, discord_user_id: str) -> str:
    payload = {
        "type":     "user",
        "guild_id": guild_id,
        "user_id":  discord_user_id,
        "exp":      datetime.utcnow() + timedelta(minutes=15),
        "nonce":    secrets.token_hex(8),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _build_streamer_state(
    guild_id: str,
    discord_channel: str,
    mention_everyone: int,
    discord_user_id: str,
) -> str:
    payload = {
        "type":             "streamer",
        "guild_id":         guild_id,
        "discord_channel":  discord_channel,
        "mention_everyone": mention_everyone,
        "user_id":          discord_user_id,
        "exp":              datetime.utcnow() + timedelta(minutes=15),
        "nonce":            secrets.token_hex(8),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _err(msg: str, guild_id: str = "") -> RedirectResponse:
    gid = f"&guild_id={quote(guild_id)}" if guild_id else ""
    return RedirectResponse(f"{FRONTEND_URL}/verify?error={quote(msg)}{gid}")


@router.get("/streamer-login")
async def chzzk_streamer_login(
    guild_id:         str = Query(...),
    discord_channel:  str = Query(...),
    mention_everyone: int = Query(0),
    discord_user_id:  str = Query(...),
):
    if not CHZZK_CLIENT_ID:
        return _err("chzzk_not_configured", guild_id)
    state  = _build_streamer_state(guild_id, discord_channel, mention_everyone, discord_user_id)
    params = {
        "clientId":    CHZZK_CLIENT_ID,
        "redirectUri": CHZZK_REDIRECT_URI,
        "state":       state,
    }
    return RedirectResponse(f"{CHZZK_AUTH_URL}?{urlencode(params)}")


@router.get("/login")
async def chzzk_login(
    guild_id:        str = Query(...),
    discord_user_id: str = Query(...),
):
    if not CHZZK_CLIENT_ID:
        return _err("chzzk_not_configured", guild_id)
    if not discord_user_id:
        return _err("discord_not_logged_in", guild_id)

    state  = _build_state(guild_id, discord_user_id)
    params = {
        "clientId":    CHZZK_CLIENT_ID,
        "redirectUri": CHZZK_REDIRECT_URI,
        "state":       state,
    }
    return RedirectResponse(f"{CHZZK_AUTH_URL}?{urlencode(params)}")


@router.get("/callback")
async def chzzk_callback(
    code:  str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/verify?error={quote(error)}")
    if not code or not state:
        return _err("missing_params")

    # ── state 검증 ──────────────────────────────────────────────────────────
    try:
        state_data      = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        guild_id        = state_data["guild_id"]
        discord_user_id = state_data["user_id"]
        flow_type       = state_data.get("type", "user")
    except JWTError:
        return _err("invalid_state")

    if not discord_user_id:
        return _err("discord_user_missing", guild_id)

    # ── Chzzk code → access_token 교환 ──────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                CHZZK_TOKEN_URL,
                json={
                    "grantType":    "authorization_code",
                    "clientId":     CHZZK_CLIENT_ID,
                    "clientSecret": CHZZK_CLIENT_SECRET,
                    "code":         code,
                    "state":        state,
                },
            )
            print(f"[chzzk-auth] token status={token_resp.status_code} body={token_resp.text[:300]}")
            token_data   = token_resp.json()
            access_token = token_data.get("content", {}).get("accessToken")
    except Exception as e:
        print(f"[chzzk-auth] token request failed: {e}")
        return _err("oauth_failed", guild_id)

    if not access_token:
        print(f"[chzzk-auth] No accessToken in response: {token_data}")
        return _err("token_failed", guild_id)

    # ── 스트리머 등록 플로우 ──────────────────────────────────────────────────
    if flow_type == "streamer":
        return await _handle_streamer_registration(
            state_data, access_token, guild_id
        )

    # ── 치지직 채널명 조회 ────────────────────────────────────────────────────
    channel_name = await _get_chzzk_channel_name(access_token)

    # ── DB에 인증 이력 + 구독 개월 수 기록 ─────────────────────────────────
    try:
        db = await get_db()
        # 팔로우 개월 수 조회 (날짜 기반 우선, fallback 구독 개월)
        tier_months = await _get_tier_months(guild_id, access_token)
        await db.execute(
            """INSERT INTO chzzk_verifications(guild_id, user_id, verified_at, tier_months, follow_months)
               VALUES(?,?,?,?,?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   verified_at=excluded.verified_at,
                   tier_months=excluded.tier_months,
                   follow_months=excluded.follow_months""",
            (int(guild_id), int(discord_user_id), time.time(), tier_months, tier_months),
        )
        await db.commit()
    except Exception as e:
        print(f"[chzzk-auth] DB write failed: {e}")
        return _err("db_error", guild_id)

    # ── Discord 역할 교체 ────────────────────────────────────────────────────
    cfg = await (await db.execute(
        "SELECT verified_role_id, unverified_role_id FROM guild_config WHERE guild_id=?",
        (int(guild_id),),
    )).fetchone()

    if not cfg:
        print(f"[chzzk-auth] guild_config not found for guild {guild_id}")
        return _err("guild_not_configured", guild_id)

    if not cfg["verified_role_id"]:
        print(f"[chzzk-auth] verified_role_id not set for guild {guild_id}")
        return _err("role_not_configured", guild_id)

    if cfg["unverified_role_id"]:
        ok = await _remove_role(guild_id, discord_user_id, str(cfg["unverified_role_id"]))
        if not ok:
            print(f"[chzzk-auth] Failed to remove unverified role for user {discord_user_id}")

    ok = await _add_role(guild_id, discord_user_id, str(cfg["verified_role_id"]))
    if not ok:
        print(f"[chzzk-auth] Failed to add verified role for user {discord_user_id}")
        return _err("role_assign_failed", guild_id)

    # ── 치지직 채널명 → Discord 서버 닉네임 설정 ────────────────────────────
    if channel_name:
        await _set_discord_nickname(guild_id, discord_user_id, channel_name)
    else:
        print(f"[chzzk-auth] No channel name found, skipping nickname change")

    # ── 팔로워 역할 부여 ─────────────────────────────────────────────────────
    await _assign_follower_roles(guild_id, discord_user_id, access_token)

    return RedirectResponse(f"{FRONTEND_URL}/verify?success=1&guild_id={quote(guild_id)}")
