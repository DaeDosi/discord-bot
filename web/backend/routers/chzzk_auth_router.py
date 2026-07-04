import os
import json
import time
import secrets
import traceback
import httpx
from urllib.parse import urlencode, quote
from datetime import datetime, timedelta
from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import RedirectResponse
from jose import jwt, JWTError
from auth import JWT_SECRET, JWT_ALGORITHM, FRONTEND_URL
from database.db import get_db
from deps import get_current_user, require_guild_admin
from routers.verify_router import _add_role, _remove_role

router = APIRouter(prefix="/api/chzzk-auth", tags=["chzzk-auth"])

CHZZK_CLIENT_ID     = os.getenv("CHZZK_CLIENT_ID", "")
CHZZK_CLIENT_SECRET = os.getenv("CHZZK_CLIENT_SECRET", "")
CHZZK_REDIRECT_URI  = os.getenv(
    "CHZZK_REDIRECT_URI",
    "http://localhost:8000/api/chzzk-auth/callback",
)

CHZZK_AUTH_URL   = "https://chzzk.naver.com/account-interlock"
CHZZK_TOKEN_URL  = "https://openapi.chzzk.naver.com/auth/v1/token"
CHZZK_USER_URL   = "https://openapi.chzzk.naver.com/open/v1/users/me"
CHZZK_FOLLOWERS_URL = "https://openapi.chzzk.naver.com/open/v1/channels/followers"
DISCORD_API      = "https://discord.com/api/v10"
_BOT_TOKEN       = os.getenv("DISCORD_TOKEN", "")


# ── 유틸 헬퍼 ─────────────────────────────────────────────────────────────────

def _days_since(date_str: str) -> int:
    """ISO8601 날짜 문자열 → 현재까지 경과 일수."""
    from datetime import timezone
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - dt).days)
    except Exception:
        return 0


async def _get_chzzk_channel_info(access_token: str) -> dict:
    """로그인한 치지직 유저의 채널 정보(channelId, channelName, channelImageUrl) 반환."""
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
                return {
                    "channelId":       content.get("channelId") or content.get("userIdHash"),
                    "channelName":     content.get("nickname") or content.get("channelName") or "",
                    "channelImageUrl": content.get("profileImageUrl") or content.get("channelImageUrl") or "",
                }
    except Exception as e:
        print(f"[chzzk-auth] users/me error: {repr(e)}")
        traceback.print_exc()
    return {}


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
            print(f"[chzzk-auth] Discord PATCH nick status={resp.status_code}")
    except Exception as e:
        print(f"[chzzk-auth] Discord PATCH nick error: {e}")


# ── 치지직 토큰 갱신 ──────────────────────────────────────────────────────────

async def _refresh_chzzk_token(refresh_token: str) -> tuple[str | None, str | None, int]:
    """Returns (access_token, new_refresh_token, expires_at_unix)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                CHZZK_TOKEN_URL,
                json={
                    "grantType":    "refresh_token",
                    "clientId":     CHZZK_CLIENT_ID,
                    "clientSecret": CHZZK_CLIENT_SECRET,
                    "refreshToken": refresh_token,
                },
            )
            print(f"[chzzk-auth] token refresh status={resp.status_code} body={resp.text[:300]}")
            if resp.status_code == 200:
                c  = resp.json().get("content", {})
                at = c.get("accessToken")
                rt = c.get("refreshToken") or refresh_token
                ei = c.get("expiresIn", 86400)
                return at, rt, int(time.time()) + ei
    except Exception as e:
        print(f"[chzzk-auth] token refresh error: {repr(e)}")
    return None, None, 0


async def _get_fresh_streamer_token(guild_id: str) -> str | None:
    """스트리머 OAuth 토큰 반환. 만료 임박 시 자동 갱신."""
    db = await get_db()
    sub = await (await db.execute(
        """SELECT streamer_access_token, streamer_refresh_token, streamer_token_expires_at
           FROM chzzk_subscriptions WHERE guild_id=?""",
        (int(guild_id),)
    )).fetchone()
    if not sub or not sub["streamer_refresh_token"]:
        print(f"[chzzk-auth] no streamer tokens for guild {guild_id}")
        return None

    expires_at = sub["streamer_token_expires_at"] or 0
    if expires_at > int(time.time()) + 300:
        return sub["streamer_access_token"]

    at, rt, new_exp = await _refresh_chzzk_token(sub["streamer_refresh_token"])
    if not at:
        return None
    await db.execute(
        """UPDATE chzzk_subscriptions
           SET streamer_access_token=?, streamer_refresh_token=?, streamer_token_expires_at=?
           WHERE guild_id=?""",
        (at, rt, new_exp, int(guild_id))
    )
    await db.commit()
    return at


# ── 팔로우 체크 공통 헬퍼 ─────────────────────────────────────────────────────

def _extract_date_from_item(item: dict) -> str | None:
    return (
        item.get("createdDate")
        or item.get("followDate")
        or item.get("createdAt")
        or item.get("followedAt")
    )


async def _search_followers_page(
    search_channel_id: str,
    token: str,
    label: str,
) -> tuple[str | None, int, bool]:
    """
    GET /open/v1/channels/followers 를 페이지네이션하여 search_channel_id 검색.
    이 엔드포인트는 "인증된 채널을 팔로우하는 사람 목록(팔로워)"을 반환함.
    반드시 streamer 토큰으로 호출해 viewer channelId를 검색해야 함.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Client-Id": CHZZK_CLIENT_ID,
        "Accept": "application/json",
    }
    page      = 0
    page_size = 50
    max_pages = 40

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            while page < max_pages:
                resp = await client.get(
                    CHZZK_FOLLOWERS_URL,
                    headers=headers,
                    params={"page": page, "size": page_size},
                )
                print(f"[chzzk-auth][{label}] page={page} status={resp.status_code} body={resp.text[:600]}")

                if resp.status_code != 200:
                    print(f"[chzzk-auth][{label}] API error HTTP {resp.status_code}")
                    break

                data    = resp.json()
                content = data.get("content") or {}
                if isinstance(content, list):
                    items = content
                elif isinstance(content, dict):
                    items = content.get("data") or []
                else:
                    items = []

                if not items:
                    print(f"[chzzk-auth][{label}] no more items at page={page}")
                    break

                for item in items:
                    if item.get("channelId") == search_channel_id:
                        date_str = _extract_date_from_item(item)
                        if date_str:
                            days = _days_since(date_str)
                            print(f"[chzzk-auth][{label}] FOUND id={search_channel_id} date={date_str} days={days}")
                            return date_str, days, True
                        print(f"[chzzk-auth][{label}] FOUND but no date: {item}")
                        return None, 0, True

                if len(items) < page_size:
                    break
                page += 1

        print(f"[chzzk-auth][{label}] id={search_channel_id} NOT FOUND in list")

    except Exception as e:
        print(f"[chzzk-auth][{label}] error: {repr(e)}")

    return None, -1, False


async def _check_unofficial_follow(
    streamer_channel_id: str,
    viewer_access_token: str,
) -> tuple[str | None, int, bool]:
    """
    비공식 채널 API (api.chzzk.naver.com) 로 팔로우 여부 확인.
    응답 JSON에서 가능한 follow 관련 필드 모두 시도.
    """
    url = f"https://api.chzzk.naver.com/service/v1/channels/{streamer_channel_id}"
    headers = {
        "Authorization": f"Bearer {viewer_access_token}",
        "Client-Id": CHZZK_CLIENT_ID,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
            print(f"[chzzk-auth][unofficial] status={resp.status_code} body={resp.text[:1000]}")
            if resp.status_code == 200:
                data    = resp.json()
                content = data.get("content") or {}
                print(f"[chzzk-auth][unofficial] content keys={list(content.keys())}")

                # following이 dict인 경우 (streaming info 포함 가능)
                following = content.get("following") or content.get("streamingProperty", {}).get("following")
                if isinstance(following, dict):
                    date_str = _extract_date_from_item(following) or following.get("followDate")
                    if date_str:
                        days = _days_since(date_str)
                        print(f"[chzzk-auth][unofficial] follow via dict date={date_str} days={days}")
                        return date_str, days, True
                    if following:
                        return None, 0, True

                # 직접 필드
                is_following = bool(content.get("isFollowing") or content.get("isFollowed"))
                date_str = (
                    content.get("followDate")
                    or content.get("followedAt")
                    or content.get("followStartDate")
                )
                if is_following or date_str:
                    days = _days_since(date_str) if date_str else 0
                    print(f"[chzzk-auth][unofficial] follow via direct field date={date_str} days={days}")
                    return date_str, days, True

    except Exception as e:
        print(f"[chzzk-auth][unofficial] error: {repr(e)}")

    return None, -1, False


async def _get_follow_info_for_guild(
    guild_id: str,
    viewer_channel_id: str | None,
    viewer_access_token: str,
) -> tuple[str | None, int, bool]:
    """
    2가지 방법으로 팔로우 여부를 순차 시도:
      1. 비공식 채널 API (viewer 토큰) — follow 필드 직접 파싱 (미지원)
      2. 공식 팔로워 API (streamer 토큰) — streamer의 팔로워 목록에서 viewer channelId 검색
         ※ GET /open/v1/channels/followers 는 "나를 팔로우하는 목록" 반환.
           viewer 토큰으로 호출하면 viewer 팔로워 목록 → streamer를 찾을 수 없음.
           반드시 streamer 토큰으로 호출해 viewer를 검색해야 함.
    """
    if not viewer_channel_id:
        print(f"[chzzk-auth] viewer channelId unknown → skip follow check")
        return None, -1, False

    db = await get_db()
    sub = await (await db.execute(
        "SELECT chzzk_channel_id FROM chzzk_subscriptions WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone()
    if not sub:
        print(f"[chzzk-auth] no streamer for guild {guild_id}")
        return None, -1, False

    streamer_channel_id = sub["chzzk_channel_id"]

    # ── 방법 1: 비공식 채널 API ───────────────────────────────────────────────
    result = await _check_unofficial_follow(streamer_channel_id, viewer_access_token)
    if result[2]:
        return result

    # ── 방법 2: 공식 API - streamer 토큰으로 팔로워 목록에서 viewer 검색 ─────
    streamer_token = await _get_fresh_streamer_token(guild_id)
    if streamer_token:
        result = await _search_followers_page(viewer_channel_id, streamer_token, "streamer-followers")
        if result[2]:
            return result
    else:
        print(f"[chzzk-auth] no streamer tokens → streamer must re-link OAuth at dashboard")

    print(f"[chzzk-auth] follow check failed guild={guild_id} viewer={viewer_channel_id} streamer={streamer_channel_id}")
    return None, -1, False


# ── 스트리머 등록 플로우 ──────────────────────────────────────────────────────

async def _handle_streamer_registration(
    state_data: dict,
    access_token: str,
    refresh_token: str | None,
    token_expires_at: int,
    guild_id: str,
) -> RedirectResponse:
    discord_channel  = state_data.get("discord_channel", "")
    mention_everyone = int(state_data.get("mention_everyone", 0))
    dashboard_url    = f"{FRONTEND_URL}/dashboard/{guild_id}/chzzk"

    channel_info = await _get_chzzk_channel_info(access_token)
    if not channel_info.get("channelId"):
        return RedirectResponse(f"{dashboard_url}?error=chzzk_channel_not_found")

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
            # 이미 등록됨 → 토큰만 갱신 (재연동)
            await db.execute(
                """UPDATE chzzk_subscriptions
                   SET streamer_access_token=?, streamer_refresh_token=?, streamer_token_expires_at=?
                   WHERE guild_id=?""",
                (access_token, refresh_token, token_expires_at, int(guild_id))
            )
            await db.commit()
            print(f"[chzzk-auth] streamer tokens refreshed for guild={guild_id}")
            return RedirectResponse(f"{dashboard_url}?success=token_refreshed")

        await db.execute(
            """INSERT INTO chzzk_subscriptions
               (guild_id, discord_channel, chzzk_channel_id, chzzk_name, chzzk_image_url,
                mention_everyone, is_live, streamer_access_token, streamer_refresh_token, streamer_token_expires_at)
               VALUES (?,?,?,?,?,?,0,?,?,?)""",
            (
                int(guild_id), int(discord_channel),
                chzzk_id, chzzk_name, image_url, mention_everyone,
                access_token, refresh_token, token_expires_at,
            ),
        )
        await db.commit()
        print(f"[chzzk-auth] streamer registered: guild={guild_id} channel={chzzk_name}({chzzk_id})")
    except Exception as e:
        print(f"[chzzk-auth] streamer DB error: {e}")
        return RedirectResponse(f"{dashboard_url}?error=db_error")

    return RedirectResponse(f"{dashboard_url}?success=streamer_added")


# ── 팔로우 역할 부여 ──────────────────────────────────────────────────────────

async def _assign_follower_roles(guild_id: str, discord_user_id: str, months: int) -> None:
    """팔로우 개월 수(months)를 기준으로 역할 부여. 이미 계산된 값을 받아 API 중복 호출 방지."""
    db = await get_db()

    tiers = await (await db.execute(
        "SELECT months, role_id FROM chzzk_follow_roles WHERE guild_id=? ORDER BY months ASC",
        (int(guild_id),)
    )).fetchall()

    if not tiers:
        sub = await (await db.execute(
            "SELECT follow_role_1month, follow_role_3month, "
            "follow_months_tier1, follow_months_tier2 "
            "FROM chzzk_subscriptions WHERE guild_id=?",
            (int(guild_id),)
        )).fetchone()
        if not sub or (not sub["follow_role_1month"] and not sub["follow_role_3month"]):
            return
        t1 = int(sub["follow_months_tier1"] or 1)
        t2 = int(sub["follow_months_tier2"] or 3)
        if months >= t2 and sub["follow_role_3month"]:
            await _add_role(guild_id, discord_user_id, str(sub["follow_role_3month"]))
        elif months >= t1 and sub["follow_role_1month"]:
            await _add_role(guild_id, discord_user_id, str(sub["follow_role_1month"]))
        return

    print(f"[chzzk-auth] follow months={months}, tiers={[(t['months'], t['role_id']) for t in tiers]}")
    for tier in tiers:
        if months >= tier["months"]:
            await _add_role(guild_id, discord_user_id, str(tier["role_id"]))
            print(f"[chzzk-auth] assigned follow role {tier['role_id']} ({tier['months']}개월+)")


# ── JWT state 빌더 ────────────────────────────────────────────────────────────

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


# ── OAuth 엔드포인트 ──────────────────────────────────────────────────────────

@router.get("/streamer-login-url")
async def chzzk_streamer_login_url(
    guild_id:         str = Query(...),
    discord_channel:  str = Query(...),
    mention_everyone: int = Query(0),
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    """스트리머 계정 연동용 치지직 OAuth URL 발급. 대시보드 관리자 세션(JWT)으로만 호출 가능하며,
    discord_user_id는 클라이언트 입력이 아닌 인증된 세션에서 직접 가져온다."""
    if not CHZZK_CLIENT_ID:
        raise HTTPException(status_code=400, detail="chzzk_not_configured")
    state  = _build_streamer_state(guild_id, discord_channel, mention_everyone, user["sub"])
    params = {
        "clientId":    CHZZK_CLIENT_ID,
        "redirectUri": CHZZK_REDIRECT_URI,
        "state":       state,
        "scope":       "채널 팔로워 조회",
    }
    return {"url": f"{CHZZK_AUTH_URL}?{urlencode(params)}"}


@router.get("/login-url")
async def chzzk_login_url(
    guild_id: str = Query(...),
    user: dict = Depends(get_current_user),
):
    """시청자 인증용 치지직 OAuth URL 발급. discord_user_id는 인증된 세션(JWT)에서 가져온다 —
    클라이언트가 임의의 유저를 지정해 인증/역할을 대신 완료시키는 것을 방지."""
    if not CHZZK_CLIENT_ID:
        raise HTTPException(status_code=400, detail="chzzk_not_configured")
    state  = _build_state(guild_id, user["sub"])
    params = {
        "clientId":    CHZZK_CLIENT_ID,
        "redirectUri": CHZZK_REDIRECT_URI,
        "state":       state,
        "scope":       "채널 팔로워 조회",
    }
    return {"url": f"{CHZZK_AUTH_URL}?{urlencode(params)}"}


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

    # ── Chzzk code → token 교환 ──────────────────────────────────────────────
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
            token_data = token_resp.json()
    except Exception as e:
        print(f"[chzzk-auth] token request failed: {e}")
        return _err("oauth_failed", guild_id)

    content          = token_data.get("content", {})
    access_token     = content.get("accessToken")
    refresh_token    = content.get("refreshToken")
    expires_in       = content.get("expiresIn", 86400)
    token_expires_at = int(time.time()) + expires_in

    if not access_token:
        print(f"[chzzk-auth] No accessToken in response: {token_data}")
        return _err("token_failed", guild_id)

    # ── 스트리머 등록 플로우 ──────────────────────────────────────────────────
    if flow_type == "streamer":
        return await _handle_streamer_registration(
            state_data, access_token, refresh_token, token_expires_at, guild_id
        )

    # ── 시청자 인증 플로우 ────────────────────────────────────────────────────

    # 치지직 채널 정보 (channelId + channelName) 조회
    viewer_info       = await _get_chzzk_channel_info(access_token)
    channel_name      = viewer_info.get("channelName") or viewer_info.get("channelId")
    viewer_channel_id = viewer_info.get("channelId")

    # 3단계 방식으로 팔로우 여부 확인
    follow_date, follow_days, is_following = await _get_follow_info_for_guild(
        guild_id, viewer_channel_id, access_token
    )
    tier_months = max(0, follow_days // 30) if follow_days >= 0 else 0

    # ── DB에 인증 이력 + 팔로우 정보 기록 ───────────────────────────────────
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO chzzk_verifications
                   (guild_id, user_id, verified_at, tier_months, follow_months,
                    follow_date, follow_days, chzzk_channel_id)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   verified_at=excluded.verified_at,
                   tier_months=excluded.tier_months,
                   follow_months=excluded.follow_months,
                   follow_date=excluded.follow_date,
                   follow_days=excluded.follow_days,
                   chzzk_channel_id=excluded.chzzk_channel_id""",
            (int(guild_id), int(discord_user_id), time.time(),
             tier_months, tier_months, follow_date, follow_days, viewer_channel_id),
        )
        await db.commit()
        print(f"[chzzk-auth] saved: date={follow_date} days={follow_days} is_following={is_following} chzzk_ch={viewer_channel_id}")
    except Exception as e:
        print(f"[chzzk-auth] DB write failed: {e}")
        return _err("db_error", guild_id)

    # ── Discord 역할 교체 ────────────────────────────────────────────────────
    cfg = await (await db.execute(
        "SELECT verified_role_id, unverified_role_id FROM guild_config WHERE guild_id=?",
        (int(guild_id),),
    )).fetchone()

    if not cfg:
        return _err("guild_not_configured", guild_id)
    if not cfg["verified_role_id"]:
        return _err("role_not_configured", guild_id)

    if cfg["unverified_role_id"]:
        await _remove_role(guild_id, discord_user_id, str(cfg["unverified_role_id"]))

    ok = await _add_role(guild_id, discord_user_id, str(cfg["verified_role_id"]))
    if not ok:
        return _err("role_assign_failed", guild_id)

    # 치지직 채널명 → Discord 서버 닉네임
    if channel_name:
        await _set_discord_nickname(guild_id, discord_user_id, channel_name)

    # 팔로우 역할 부여
    if is_following:
        await _assign_follower_roles(guild_id, discord_user_id, tier_months)
    else:
        print(f"[chzzk-auth] user {discord_user_id} not following → no follow roles")

    follow_param = f"&follow_days={follow_days}" if is_following else "&follow=none"
    return RedirectResponse(f"{FRONTEND_URL}/verify?success=1&guild_id={quote(guild_id)}{follow_param}")
