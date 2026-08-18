import os
import time as _time
import asyncio
import httpx
from datetime import date, datetime, timezone, timedelta

_KST = timezone(timedelta(hours=9))


def _today_kst() -> date:
    return datetime.now(_KST).date()
from fastapi import (APIRouter, Depends, HTTPException, Query, Request,
                     Response)
from pydantic import BaseModel
from typing import Optional
from deps import get_current_user
from database import get_db
from utils import oauth_backoff as ob
from utils.ids import snowflake_str

router = APIRouter(prefix="/api/admin", tags=["admin"])

_OWNER_ID  = os.getenv("OWNER_ID", "")
_BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
_DISCORD   = "https://discord.com/api/v10"
_CHZZK_API = "https://api.chzzk.naver.com"
_CHZZK_HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ── 봇 서버 목록 캐시 (2분 TTL) ───────────────────────────────────────────────
_guilds_cache: list[dict] = []
_guilds_cache_ts: float = 0.0
_guilds_lock: asyncio.Lock | None = None
_GUILDS_TTL = 120  # seconds


def _get_lock() -> asyncio.Lock:
    global _guilds_lock
    if _guilds_lock is None:
        _guilds_lock = asyncio.Lock()
    return _guilds_lock


async def _bot_guilds(force: bool = False) -> list[dict]:
    global _guilds_cache, _guilds_cache_ts
    now = _time.monotonic()
    if not force and _guilds_cache and now - _guilds_cache_ts < _GUILDS_TTL:
        return _guilds_cache
    async with _get_lock():
        now = _time.monotonic()
        if not force and _guilds_cache and now - _guilds_cache_ts < _GUILDS_TTL:
            return _guilds_cache
        guilds: list[dict] = []
        after: str | None = None
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                params: dict = {"limit": 200}
                if after:
                    params["after"] = after
                resp = await client.get(
                    f"{_DISCORD}/users/@me/guilds",
                    headers={"Authorization": f"Bot {_BOT_TOKEN}"},
                    params=params,
                )
                if resp.status_code != 200:
                    break
                batch = resp.json()
                if not batch:
                    break
                guilds.extend(batch)
                if len(batch) < 200:
                    break
                after = batch[-1]["id"]
        _guilds_cache = guilds
        _guilds_cache_ts = _time.monotonic()
        return guilds


# ── 오너 전용 권한 검증 ────────────────────────────────────────────────────────

async def _require_owner(user: dict = Depends(get_current_user)) -> dict:
    if not _OWNER_ID or user.get("sub") != str(_OWNER_ID):
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
    return user


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

async def _guild_member_count(client: httpx.AsyncClient, guild_id: str) -> int:
    try:
        resp = await client.get(
            f"{_DISCORD}/guilds/{guild_id}",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"},
            params={"with_counts": "true"},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("approximate_member_count", 0)
    except Exception:
        pass
    return 0


# follow-stats/verifications는 인증자 수만큼 개별 멤버 조회(1 req/user)를 했었는데,
# 인증자가 많은 길드에서는 이게 N번의 순차 REST 호출(+429 재시도)로 쌓여 응답이 수십 초~
# 수 분까지 걸렸고, nexadmin 프론트가 그 실패를 조용히 삼켜서 "집계 중..."에 멈춘 것처럼
# 보이는 원인이었다. 길드당 1회 멤버 목록 조회(최대 1000명씩 페이지네이션)로 바꿔
# N req -> (길드 수) req로 줄인다. 길드별로 짧게 캐시해 같은 요청 내 여러 엔드포인트가
# 같은 길드를 반복 조회해도 한 번만 호출한다.
_member_map_cache: dict[str, tuple[float, dict[str, str]]] = {}
_MEMBER_MAP_TTL = 120  # seconds


async def _guild_member_map(client: httpx.AsyncClient, guild_id: str) -> dict[str, str]:
    now = _time.monotonic()
    cached = _member_map_cache.get(guild_id)
    if cached and now - cached[0] < _MEMBER_MAP_TTL:
        return cached[1]

    name_map: dict[str, str] = {}
    headers = {"Authorization": f"Bot {_BOT_TOKEN}"}
    after = "0"
    try:
        while True:
            resp = await client.get(
                f"{_DISCORD}/guilds/{guild_id}/members",
                headers=headers,
                params={"limit": 1000, "after": after},
                timeout=10,
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch:
                break
            for m in batch:
                uid = str(m["user"]["id"])
                name_map[uid] = (
                    m.get("nick")
                    or m.get("user", {}).get("global_name")
                    or m.get("user", {}).get("username")
                    or uid
                )
            if len(batch) < 1000:
                break
            after = str(batch[-1]["user"]["id"])
    except Exception:
        pass

    _member_map_cache[guild_id] = (now, name_map)
    return name_map


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/overview")
async def overview(user: dict = Depends(_require_owner)):
    db          = await get_db()
    guilds_list = await _bot_guilds()
    guild_count = len(guilds_list)

    async with httpx.AsyncClient() as client:
        counts = await asyncio.gather(*[
            _guild_member_count(client, g["id"])
            for g in guilds_list[:30]
        ])
    total_users = sum(counts)

    chzzk_count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_subscriptions"
    )).fetchone())[0]

    verify_count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_verifications"
    )).fetchone())[0]

    # daily_visitors는 KST 날짜를 키로 쓴다(stats_router.today_kst). Railway 로컬은
    # UTC라 date.today()를 쓰면 KST 00:00~08:59 동안 전날 행을 세게 된다.
    today = _today_kst().isoformat()
    tv_row = await (await db.execute(
        "SELECT COUNT(*) FROM daily_visitors WHERE date=?", (today,)
    )).fetchone()
    today_visitors = tv_row[0] if tv_row else 0

    return {
        "guild_count":    guild_count,
        "total_users":    total_users,
        "chzzk_subs":     chzzk_count,
        "verifications":  verify_count,
        "today_visitors": today_visitors,
    }


@router.post("/refresh")
async def force_refresh(user: dict = Depends(_require_owner)):
    """nexadmin 새로고침 버튼: 서버 목록 캐시(2분 TTL)를 즉시 무효화하고, 봇 프로세스에도
    presence("N개의 서버") 재계산을 요청한다. 봇은 별도 프로세스라 bot_stats.refresh_requested_at
    타임스탬프로 신호를 보내고, 봇이 짧은 주기로 이를 폴링해서 반영한다."""
    db = await get_db()
    await db.execute(
        """INSERT INTO bot_stats(id, refresh_requested_at) VALUES(1, ?)
           ON CONFLICT(id) DO UPDATE SET refresh_requested_at = excluded.refresh_requested_at""",
        (_time.time(),)
    )
    await db.commit()
    guilds_list = await _bot_guilds(force=True)
    return {"ok": True, "guild_count": len(guilds_list)}


@router.get("/guilds")
async def guilds(response: Response, user: dict = Depends(_require_owner)):
    """서버 목록 + 치지직 인증 상태.

    상태를 **여기서 붙여 준다.** 프론트에서 guild_id로 조인하면 안 된다 —
    discord 스노플레이크는 2^53을 넘어서 JSON number로 오가는 순간 정밀도가
    깎이고(886237674665549865 → …800), 그러면 어떤 서버와도 매칭되지 않는다.
    """
    db          = await get_db()
    guilds_list = await _bot_guilds()

    chzzk_rows = await (await db.execute(
        # 토큰 값은 꺼내지 않는다 — 보유 여부만 SQL에서 boolean으로 만든다.
        "SELECT guild_id, chzzk_name, token_state, token_fail_count,"
        " token_last_fail_at, token_next_try_at, token_last_success_at,"
        " (streamer_refresh_token IS NOT NULL) AS has_streamer_token"
        " FROM chzzk_subscriptions"
    )).fetchall()
    chzzk_map = {str(r["guild_id"]): r for r in chzzk_rows}

    out = []
    for g in guilds_list:
        r = chzzk_map.get(g["id"])
        auth = None
        if r is not None:
            linked = bool(r["has_streamer_token"])
            state = (r["token_state"] or ob.STATE_OK) if linked else None
            auth = {
                "streamer_linked":       linked,
                "token_state":           state,
                "reauth_required":       state == ob.STATE_REAUTH,
                "token_fail_count":      int(r["token_fail_count"] or 0) if linked else 0,
                "token_last_fail_at":    _epoch_or_none(r["token_last_fail_at"]) if linked else None,
                "token_next_try_at":     _epoch_or_none(r["token_next_try_at"]) if linked else None,
                "token_last_success_at": _epoch_or_none(r["token_last_success_at"]) if linked else None,
            }
        out.append({
            "id":         g["id"],
            "name":       g["name"],
            "icon":       g.get("icon"),
            "chzzk_name": r["chzzk_name"] if r is not None else None,
            "auth":       auth,
        })
    response.headers["Cache-Control"] = "private, no-store"
    return out


def _epoch_or_none(v) -> int | None:
    """0은 '없음'을 뜻한다(컬럼이 NOT NULL DEFAULT 0이라 NULL이 못 들어온다)."""
    return int(v or 0) or None


@router.get("/chzzk")
async def chzzk_all(response: Response, user: dict = Depends(_require_owner)):
    """치지직 구독 목록 + **안전한** OAuth 토큰 건강 상태.

    컬럼은 하나씩 명시한다 — `SELECT *`로 바꾸면 아래 `dict(r)` 전개에
    `streamer_access_token`·`streamer_refresh_token`이 그대로 실려 나간다.
    토큰 보유 여부는 값을 꺼내지 않고 SQL에서 boolean으로만 만든다.
    """
    db          = await get_db()
    rows        = await (await db.execute(
        """SELECT id, guild_id, chzzk_channel_id, chzzk_name, chzzk_image_url,
                  discord_channel, mention_everyone, is_live,
                  follow_role_1month, follow_role_3month,
                  follow_months_tier1, follow_months_tier2,
                  token_state, token_fail_count, token_last_error_code,
                  token_last_fail_at, token_next_try_at, token_last_success_at,
                  (streamer_refresh_token IS NOT NULL) AS has_streamer_token
           FROM chzzk_subscriptions ORDER BY guild_id"""
    )).fetchall()

    guilds_list    = await _bot_guilds()
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}

    out = []
    for r in rows:
        row = dict(r)
        # 스트리머 OAuth를 한 적이 없는 구독이다. 컬럼 기본값이 'ok'라 그대로
        # 내보내면 **연동한 적 없는 서버가 '정상'으로 보인다** — null로 구분한다.
        linked = bool(row.pop("has_streamer_token", 0))
        state = (row["token_state"] or ob.STATE_OK) if linked else None
        # 스노플레이크는 경계에서 전부 문자열로 (utils/ids.py 참고).
        for key in ("guild_id", "discord_channel",
                    "follow_role_1month", "follow_role_3month"):
            if key in row:
                row[key] = snowflake_str(row[key])
        row.update({
            "guild_name": guild_name_map.get(str(r["guild_id"]), str(r["guild_id"])),
            "streamer_linked":        linked,
            "token_state":           state,
            "reauth_required":       state == ob.STATE_REAUTH,
            "token_fail_count":      int(row["token_fail_count"] or 0) if linked else 0,
            "token_last_error_code": (row["token_last_error_code"] or None) if linked else None,
            "token_last_fail_at":    _epoch_or_none(row["token_last_fail_at"]),
            "token_next_try_at":     _epoch_or_none(row["token_next_try_at"]),
            "token_last_success_at": _epoch_or_none(row["token_last_success_at"]),
        })
        if not linked:
            # 미연동 행에 남아 있는 값은 의미가 없다 — 전부 비운다.
            for k in ("token_last_fail_at", "token_next_try_at", "token_last_success_at"):
                row[k] = None
        out.append(row)

    # 인증 상태가 담긴 응답이다 — 공유 캐시에 올라가면 안 된다.
    response.headers["Cache-Control"] = "private, no-store"
    return out


@router.get("/verifications")
async def verifications(
    user: dict = Depends(_require_owner),
    limit: int = Query(100, le=500),
):
    db   = await get_db()
    rows = await (await db.execute(
        """SELECT guild_id, user_id, tier_months, follow_date, follow_days, verified_at
           FROM chzzk_verifications
           ORDER BY verified_at DESC LIMIT ?""",
        (limit,)
    )).fetchall()

    guilds_list    = await _bot_guilds()
    guild_id_set   = {g["id"] for g in guilds_list}
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}

    # 봇이 더 이상 없는 서버의 인증 기록은 제외
    rows = [r for r in rows if str(r["guild_id"]) in guild_id_set]

    unique_guild_ids = {str(r["guild_id"]) for r in rows}
    async with httpx.AsyncClient() as client:
        member_maps = await asyncio.gather(*[
            _guild_member_map(client, gid) for gid in unique_guild_ids
        ])
    member_map_by_guild = dict(zip(unique_guild_ids, member_maps))

    result = []
    for r in rows:
        uid = str(r["user_id"])
        name = member_map_by_guild.get(str(r["guild_id"]), {}).get(uid, uid)
        if r["follow_date"]:
            try:
                fd = (_today_kst() - date.fromisoformat(str(r["follow_date"])[:10])).days
            except Exception:
                fd = r["follow_days"] if r["follow_days"] is not None else -1
        else:
            fd = r["follow_days"] if r["follow_days"] is not None else -1
        result.append({
            "guild_id":     str(r["guild_id"]),
            "user_id":      str(r["user_id"]),
            "tier_months":  r["tier_months"],
            "follow_date":  r["follow_date"],
            "verified_at":  r["verified_at"],
            "guild_name":   guild_name_map.get(str(r["guild_id"]), str(r["guild_id"])),
            "user_name":    name,
            "follow_days":  fd,
            "is_following": fd >= 0,
        })
    return result


@router.delete("/verifications/{guild_id}/{user_id}")
async def delete_verification(
    guild_id: int,
    user_id:  int,
    user: dict = Depends(_require_owner),
):
    db = await get_db()
    result = await db.execute(
        "DELETE FROM chzzk_verifications WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="인증 기록을 찾을 수 없습니다.")
    return {"ok": True}


@router.get("/follow-stats")
async def follow_stats(user: dict = Depends(_require_owner)):
    """서버별 치지직 구독 + 인증 유저 팔로우 현황을 묶어서 반환."""
    db = await get_db()

    subs = await (await db.execute(
        """SELECT id, guild_id, chzzk_name, chzzk_image_url,
                  follow_months_tier1, follow_months_tier2
           FROM chzzk_subscriptions"""
    )).fetchall()

    if not subs:
        return []

    guild_ids    = [s["guild_id"] for s in subs]
    placeholders = ",".join("?" * len(guild_ids))

    verif_rows = await (await db.execute(
        f"""SELECT guild_id, user_id, tier_months, verified_at
            FROM chzzk_verifications
            WHERE guild_id IN ({placeholders})
            ORDER BY tier_months DESC, verified_at DESC""",
        guild_ids,
    )).fetchall()

    guilds_list    = await _bot_guilds()
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}

    unique_guild_ids = {str(v["guild_id"]) for v in verif_rows}
    async with httpx.AsyncClient() as client:
        member_maps = await asyncio.gather(*[
            _guild_member_map(client, gid) for gid in unique_guild_ids
        ])
    member_map_by_guild = dict(zip(unique_guild_ids, member_maps))

    verif_by_guild: dict[int, list] = {}
    for v in verif_rows:
        uid = str(v["user_id"])
        name = member_map_by_guild.get(str(v["guild_id"]), {}).get(uid, uid)
        verif_by_guild.setdefault(v["guild_id"], []).append({
            "user_id":     uid,          # 스노플레이크 — 문자열 (utils/ids.py)
            "user_name":   name,
            "tier_months": v["tier_months"],
            "verified_at": v["verified_at"],
        })

    return [
        {
            "sub_id":            s["id"],
            # 사용처가 없어도 문자열로 낸다 — 나중에 조인에 쓰이는 순간
            # 같은 정밀도 문제가 조용히 재발한다(실제로 아래 프론트 필터가 그랬다).
            "guild_id":          snowflake_str(s["guild_id"]),
            "guild_name":        guild_name_map.get(str(s["guild_id"])),
            "chzzk_name":        s["chzzk_name"],
            "chzzk_image_url":   s["chzzk_image_url"],
            "follow_months_tier1": s["follow_months_tier1"] or 1,
            "follow_months_tier2": s["follow_months_tier2"] or 3,
            "users":             verif_by_guild.get(s["guild_id"], []),
        }
        for s in subs
    ]


@router.get("/chzzk/search")
async def admin_search_chzzk(keyword: str, user: dict = Depends(_require_owner)):
    url = f"{_CHZZK_API}/service/v1/search/channels"
    async with httpx.AsyncClient(headers=_CHZZK_HDR, timeout=10) as client:
        resp = await client.get(url, params={"keyword": keyword, "offset": 0, "size": 10})
        if resp.status_code != 200:
            return []
        data = resp.json().get("content", {}).get("data", [])
    return [
        {
            "channelId":       d.get("channel", {}).get("channelId"),
            "channelName":     d.get("channel", {}).get("channelName"),
            "channelImageUrl": d.get("channel", {}).get("channelImageUrl"),
            "followerCount":   d.get("channel", {}).get("followerCount", 0),
            "openLive":        d.get("channel", {}).get("openLive", False),
        }
        for d in data
        if d.get("channel", {}).get("channelId")
    ]


class AdminSubCreate(BaseModel):
    guild_id:         str
    discord_channel:  str
    chzzk_channel_id: str
    chzzk_name:       str
    chzzk_image_url:  Optional[str] = None
    mention_everyone: bool = False


@router.post("/chzzk")
async def admin_add_chzzk(body: AdminSubCreate, user: dict = Depends(_require_owner)):
    db = await get_db()
    count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_subscriptions WHERE guild_id=?",
        (int(body.guild_id),)
    )).fetchone())[0]
    if count >= 1:
        raise HTTPException(status_code=400, detail="이미 구독 중입니다. 기존 구독을 먼저 삭제하세요.")
    try:
        await db.execute(
            """INSERT INTO chzzk_subscriptions
               (guild_id, discord_channel, chzzk_channel_id, chzzk_name, chzzk_image_url, mention_everyone, is_live)
               VALUES (?,?,?,?,?,?,0)""",
            (
                int(body.guild_id), int(body.discord_channel),
                body.chzzk_channel_id, body.chzzk_name,
                body.chzzk_image_url, int(body.mention_everyone),
            ),
        )
        await db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@router.delete("/chzzk/{sub_id}")
async def admin_delete_chzzk(sub_id: int, user: dict = Depends(_require_owner)):
    db = await get_db()
    await db.execute("DELETE FROM chzzk_subscriptions WHERE id=?", (sub_id,))
    await db.commit()
    return {"ok": True}


@router.get("/guilds/{guild_id}")
async def guild_detail(guild_id: str, response: Response,
                       user: dict = Depends(_require_owner)):
    # 인증 상태와 서버 설정이 담긴 응답이다 — 공유 캐시에 올라가면 안 된다.
    response.headers["Cache-Control"] = "private, no-store"
    db = await get_db()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{_DISCORD}/guilds/{guild_id}",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"},
            params={"with_counts": "true"},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail="길드 정보를 가져올 수 없습니다.")
    g = resp.json()

    chzzk_row = await (await db.execute(
        # `streamer_access_token`을 그대로 SELECT해 dict(row)로 내려보내고 있었다 —
        # 프론트는 진위 여부만 쓰는데 토큰 원문이 브라우저까지 갔다. boolean으로 바꾼다.
        """SELECT chzzk_channel_id, chzzk_name, chzzk_image_url,
                  discord_channel, notify_vod, notify_clip, notify_community,
                  is_live, token_state,
                  (streamer_access_token IS NOT NULL) AS streamer_connected
           FROM chzzk_subscriptions WHERE guild_id=?""",
        (int(guild_id),)
    )).fetchone()

    verif_count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_verifications WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone())[0]

    return {
        "id":           g["id"],
        "name":         g["name"],
        "icon":         g.get("icon"),
        "owner_id":     g.get("owner_id"),
        "member_count": g.get("approximate_member_count", 0),
        "description":  g.get("description"),
        "chzzk": ({**dict(chzzk_row),
                   "streamer_connected": bool(chzzk_row["streamer_connected"]),
                   "discord_channel": snowflake_str(chzzk_row["discord_channel"])}
                  if chzzk_row else None),
        "verif_count": verif_count,
    }


@router.get("/announcement")
async def get_announcement(user: dict = Depends(_require_owner)):
    db = await get_db()
    row = await (await db.execute(
        "SELECT message FROM site_announcement WHERE id=1"
    )).fetchone()
    return {"message": row["message"] if row else ""}


class AnnouncementSave(BaseModel):
    message: str = ""


@router.put("/announcement")
async def save_announcement(body: AnnouncementSave, user: dict = Depends(_require_owner)):
    message = body.message.strip()[:200]
    db = await get_db()
    await db.execute(
        """INSERT INTO site_announcement(id, message, updated_at) VALUES(1,?,?)
           ON CONFLICT(id) DO UPDATE SET message=excluded.message, updated_at=excluded.updated_at""",
        (message, int(_time.time()))
    )
    await db.commit()
    return {"ok": True, "message": message}


@router.delete("/guilds/{guild_id}/leave")
async def leave_guild(guild_id: str, user: dict = Depends(_require_owner)):
    global _guilds_cache, _guilds_cache_ts
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(
            f"{_DISCORD}/users/@me/guilds/{guild_id}",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"},
        )
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=f"Discord API 오류: {resp.text[:200]}")
    _guilds_cache = []
    _guilds_cache_ts = 0.0
    return {"ok": True}


# ── DB 진단 (오너 전용, 읽기 전용) ───────────────────────────────────────────
# 공개 API로 두지 않는다 — 테이블 구성과 증가 속도는 공격자에게 유용한 정보다.
# 되돌리기 어려운 작업(VACUUM/DELETE/파일 조작)은 이 엔드포인트에 두지 않는다.

@router.get("/db/diagnostics")
async def db_diagnostics(force: bool = False, user: dict = Depends(_require_owner)):
    """DB 파일·페이지·테이블별 행 수와 증가 속도, 예상 소진일.

    COUNT(*)와 dbstat가 전체를 훑으므로 10분 캐시 + 동시 실행 1개 + 타임아웃이 걸려
    있다. force=true는 캐시를 무시한다(자주 쓰지 말 것).
    """
    from db_diagnostics import collect_cached
    try:
        return await collect_cached(force=force)
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e)) from None


@router.get("/db/integrity")
async def db_integrity(full: bool = False, user: dict = Depends(_require_owner)):
    """무결성 검사. 기본은 가벼운 quick_check, full=true는 백업 직후 검증용."""
    from db_diagnostics import integrity_check
    return await integrity_check(quick=not full)


@router.get("/db/retention/report")
async def db_retention_report(user: dict = Depends(_require_owner)):
    """보존정책 dry-run 리포트 — 무엇을 지울 것인지만 보여준다(삭제 안 함).

    프루닝 활성화 전에 이 결과를 먼저 확인한다.
    """
    from singcup_retention import run_retention
    return await run_retention()


# ── 싱드컵 대표 클립 수동 지정 (OWNER 전용) ──────────────────────────────────
# 자동 선정 규칙은 **바꾸지 않는다.** 참가자가 제출본을 나중에 올려 하트가 앞선 옛
# 클립이 대표로 잡히는 경우를 사람이 개별로 바로잡는 통로다. 규칙을 넓혀서 해결하면
# 순위가 소급으로 통째 바뀐다(태그 판정 완화를 금지하는 것과 같은 이유).
#
# 이 영역이 지키는 것 세 가지:
#   1) 입력은 clip_uid 하나로 축소된다 — 임의 URL을 그대로 요청하지 않는다(SSRF).
#   2) 외부 API 호출은 DB 쓰기 트랜잭션 **밖**에서만 한다.
#   3) 적용은 `representative_clip_uid` 직접 UPDATE가 아니라 override 표 + 정상
#      재계산 경로를 탄다 — 그래야 다음 재계산에 지워지지 않는다.

class RepOverrideApply(BaseModel):
    channelId: str
    clipInput: str          # 클립 URL 또는 UID
    reason: Optional[str] = ""


class RepOverrideClear(BaseModel):
    channelId: str


# preview는 외부(치지직) 호출을 유발한다. OWNER 전용이라도 실수로 반복 호출되면
# 그대로 외부 요청이 되므로 엔드포인트 단위로 좁은 한도를 둔다(singcup_router와 같은 방식).
_REP_PREVIEW_WINDOW = 60.0
_REP_PREVIEW_LIMIT = int(os.getenv("SINGCUP_REP_PREVIEW_RATE_LIMIT", "20"))
_rep_preview_hits: list[float] = []


def _rep_preview_rate_limit():
    now = _time.monotonic()
    cutoff = now - _REP_PREVIEW_WINDOW
    while _rep_preview_hits and _rep_preview_hits[0] < cutoff:
        _rep_preview_hits.pop(0)
    if len(_rep_preview_hits) >= _REP_PREVIEW_LIMIT:
        raise HTTPException(status_code=429,
                            detail="미리보기 요청이 너무 잦습니다. 잠시 후 다시 시도하세요.")
    _rep_preview_hits.append(now)


def _parse_clip_input(raw: str) -> str:
    import singcup_overrides as so
    try:
        return so.parse_clip_uid(raw)
    except so.InvalidClipInput as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


async def _rep_state(channel_id: str) -> dict:
    """한 참가자의 자동 대표 / override / effective 대표를 한 번에 만든다.

    **effective를 따로 계산하지 않는다** — 저장된 `representative_clip_uid`가 곧
    effective다(override는 대표를 고르는 시점에 이미 반영된다). 화면에 셋을 나눠
    보여주기 위해 '자동이었다면 무엇이었을지'만 별도로 계산한다.
    """
    import singcup_clips as sc
    import singcup_overrides as so
    db = await get_db()
    s = await (await db.execute(
        "SELECT channel_id, channel_name, channel_image_url, "
        "       representative_clip_uid, tagged_clip_count "
        "FROM singcup_streamers WHERE channel_id=? AND event_id=?",
        (channel_id, sc.EVENT_ID))).fetchone()
    if s is None:
        raise HTTPException(status_code=404, detail="참가자를 찾을 수 없습니다.")

    clips = [dict(r) for r in await (await db.execute(
        "SELECT clip_uid, clip_title, heart_count, view_count, created_at, "
        "       thumbnail_image_url, active, deletion_state, blind_type "
        "FROM singcup_clips WHERE event_id=? AND owner_channel_id=? AND active=1 "
        "  AND deletion_state<>'confirmed_deleted'",
        (sc.EVENT_ID, channel_id))).fetchall()]
    auto = sc.pick_representative(clips)          # override를 넘기지 않는다 = 순수 자동
    ov = await so.get_override(channel_id, sc.EVENT_ID)

    def _clip(c):
        if not c:
            return None
        return {"clipUid": c["clip_uid"], "clipTitle": c.get("clip_title") or "",
                "heartCount": int(c.get("heart_count") or 0),
                "viewCount": int(c.get("view_count") or 0),
                "createdAt": int(c.get("created_at") or 0),
                "thumbnailImageUrl": c.get("thumbnail_image_url") or ""}

    by_uid = {c["clip_uid"]: c for c in clips}
    effective_uid = s["representative_clip_uid"]
    return {
        "channelId": s["channel_id"],
        "channelName": s["channel_name"] or "",
        "channelImageUrl": s["channel_image_url"] or "",
        "taggedClipCount": int(s["tagged_clip_count"] or 0),
        "autoRepresentative": _clip(auto),
        "effectiveRepresentative": _clip(by_uid.get(effective_uid)),
        "effectiveRepresentativeClipUid": effective_uid,
        "override": ({"clipUid": ov["override_clip_uid"],
                      "reason": ov["reason"] or "",
                      "updatedAt": int(ov["updated_at"] or 0),
                      # 지정한 클립이 그 사이 무효가 되면 행은 남되 효력을 잃는다.
                      "active": ov["override_clip_uid"] in by_uid}
                     if ov else None),
        "clips": sorted((_clip(c) for c in clips),
                        key=lambda c: (-c["heartCount"], -c["viewCount"],
                                       c["createdAt"], c["clipUid"])),
    }


@router.get("/singcup/representative/search")
async def singcup_rep_search(q: str = Query("", max_length=60),
                             limit: int = Query(20, ge=1, le=50),
                             user: dict = Depends(_require_owner)):
    """참가자 검색(닉네임 부분 일치). override가 걸린 참가자를 함께 표시한다."""
    import singcup_clips as sc
    db = await get_db()
    kw = q.strip()
    # LIKE 와일드카드를 사용자 입력에서 제거한다 — '%'만 넣으면 전체 스캔이 된다.
    safe = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = await (await db.execute(
        "SELECT s.channel_id, s.channel_name, s.channel_image_url, "
        "       s.representative_clip_uid, s.tagged_clip_count, "
        "       (SELECT o.override_clip_uid FROM singcup_representative_overrides o "
        "         WHERE o.event_id=s.event_id AND o.owner_channel_id=s.channel_id "
        "           AND o.cleared_at IS NULL) AS override_clip_uid "
        "FROM singcup_streamers s "
        "WHERE s.event_id=? AND (?='' OR s.channel_name LIKE ? ESCAPE '\\') "
        "ORDER BY s.channel_name COLLATE NOCASE LIMIT ?",
        (sc.EVENT_ID, kw, f"%{safe}%", limit))).fetchall()
    return {"eventId": sc.EVENT_ID, "query": kw, "items": [
        {"channelId": r["channel_id"], "channelName": r["channel_name"] or "",
         "channelImageUrl": r["channel_image_url"] or "",
         "taggedClipCount": int(r["tagged_clip_count"] or 0),
         "effectiveRepresentativeClipUid": r["representative_clip_uid"],
         "hasOverride": bool(r["override_clip_uid"]),
         "overrideClipUid": r["override_clip_uid"]}
        for r in rows]}


@router.get("/singcup/representative/{channel_id}")
async def singcup_rep_state(channel_id: str, user: dict = Depends(_require_owner)):
    """현재 자동 대표 / override / effective 대표를 구분해서 보여준다."""
    return await _rep_state(channel_id)


@router.post("/singcup/representative/preview")
async def singcup_rep_preview(body: RepOverrideApply,
                              user: dict = Depends(_require_owner)):
    """지정 전 검증. **아무것도 쓰지 않는다.**

    ① URL/UID에서 uid만 추출(임의 주소로 나가지 않는다)
    ② DB에서 owner·이벤트 기간·active·삭제 상태 재검증
    ③ 치지직 상세 API로 한 번 더 확인 — 고정 호스트·경로에 uid만 끼워 넣는다.
       실패해도 400으로 막지 않고 `liveCheck`로 알린다(외부 장애가 지정을 막지
       않도록). 단 DB 검증이 실패하면 그건 그대로 거부다.
    """
    import singcup_clips as sc
    import singcup_overrides as so
    _rep_preview_rate_limit()
    uid = _parse_clip_input(body.clipInput)
    state = await _rep_state(body.channelId)
    reason, _row = await so.check_clip_eligible(body.channelId, uid, sc.EVENT_ID)

    # 외부 확인은 **트랜잭션 밖**이다. 여기서는 DB를 쓰지 않으므로 트랜잭션 자체가 없다.
    live = {"checked": False, "ok": None, "note": ""}
    if reason == so.REASON_OK:
        try:
            meta = await sc.fetch_clip_meta(sc._get_client(), uid, full=True)
            if meta is None:
                live = {"checked": True, "ok": False, "note": "상세 조회에 실패했습니다."}
            else:
                owner_ok = (not meta.get("owner_channel_id")
                            or meta["owner_channel_id"] == body.channelId)
                live = {"checked": True, "ok": bool(owner_ok),
                        "note": "" if owner_ok else "치지직 상세의 소유 채널이 다릅니다.",
                        "clipTitle": meta.get("clip_title") or "",
                        "blindType": meta.get("blind_type") or ""}
        except Exception as e:                          # noqa: BLE001
            live = {"checked": True, "ok": None,
                    "note": f"외부 확인 실패: {str(e)[:80]}"}

    cur_uid = state["effectiveRepresentativeClipUid"]
    target = next((c for c in state["clips"] if c["clipUid"] == uid), None)
    current = next((c for c in state["clips"] if c["clipUid"] == cur_uid), None)
    # 지정하면 점수(조회 70% + 하트 30%)가 달라져 순위가 움직인다. 값이 내려가는
    # 경우가 흔하므로(제출본이 나중에 올라와 하트가 적다) 미리 경고로 보여준다.
    impact = None
    if target and current and target["clipUid"] != current["clipUid"]:
        impact = {
            "heartDelta": target["heartCount"] - current["heartCount"],
            "viewDelta": target["viewCount"] - current["viewCount"],
            "rankLikelyDrops": (target["heartCount"] < current["heartCount"]
                                or target["viewCount"] < current["viewCount"]),
        }
    return {
        "clipUid": uid, "channelId": body.channelId,
        "eligible": reason == so.REASON_OK, "reason": reason,
        "reasonText": "" if reason == so.REASON_OK else so.reason_text(reason),
        "noop": bool(cur_uid == uid and state["override"]),
        "currentRepresentative": current, "targetClip": target,
        "impact": impact, "liveCheck": live, "state": state,
    }


@router.post("/singcup/representative/apply")
async def singcup_rep_apply(body: RepOverrideApply,
                            user: dict = Depends(_require_owner)):
    """수동 지정을 적용한다.

    순서: 입력 축소 → DB 재검증 → override 기록(commit) → **정상 재계산 경로** →
    `/main` 캐시 무효화. 재계산이 `representative_clip_uid`를 다시 쓰므로 이 값이
    곧 effective가 되고, 그 컬럼을 읽는 모든 소비자가 같은 대표를 본다.
    """
    import singcup_clips as sc
    import singcup_overrides as so
    uid = _parse_clip_input(body.clipInput)
    await _rep_state(body.channelId)                # 참가자 존재 확인(404)
    reason, _row = await so.check_clip_eligible(body.channelId, uid, sc.EVENT_ID)
    if reason != so.REASON_OK:
        raise HTTPException(status_code=400, detail=so.reason_text(reason))

    await so.set_override(body.channelId, uid, reason=(body.reason or "")[:200],
                          event_id=sc.EVENT_ID)
    # 재계산은 외부 채널 API를 부르지만 트랜잭션 밖이다(recompute_ranking 참고).
    # 실패해도 override는 이미 영속화됐으므로 다음 정기 회차에 반영된다.
    try:
        await sc.recompute_ranking(int(_time.time()))
    except Exception as e:                              # noqa: BLE001
        sc.invalidate_main_cache()
        return {"ok": True, "clipUid": uid, "recomputed": False,
                "note": f"지정은 저장했으나 즉시 재계산에 실패했습니다: {str(e)[:120]}",
                "state": await _rep_state(body.channelId)}
    state = await _rep_state(body.channelId)
    return {"ok": True, "clipUid": uid, "recomputed": True,
            "effectiveRepresentativeClipUid": state["effectiveRepresentativeClipUid"],
            "state": state}


# ── 싱드컵 클립 지표 단건 갱신 (OWNER 전용) ─────────────────────────────────
# 위의 대표 클립 지정과는 **다른 동작**이다. 여기서는 대표를 바꾸지 않는다 —
# 한 클립의 하트·조회수를 지금 다시 읽어 오는 것뿐이다.
#
# 있어야 하는 이유: 카드 API가 200을 주면서 조회수만 빠뜨리는 회차가 있고, 그때
# 저장 계약("못 읽은 필드는 보존")대로 값이 삽입 초기값 0으로 남는다. 자동 복구는
# 다음 사이클(70분+) 뒤라, 그동안 0이 조회수 70% 가중 점수에 진짜 0처럼 들어간다.
#
# 이 영역이 지키는 것:
#   1) 숫자를 **직접 입력받지 않는다**. 값의 출처는 언제나 카드 API다.
#   2) 입력은 clip_uid 하나로 축소된다(SSRF) — 대표 지정과 같은 파서를 쓴다.
#   3) 외부 호출은 DB 트랜잭션 **밖**이고, 자동 스윕과 **같은** bounded fetch
#      경로를 재사용한다(두 경로가 서로 다른 계약을 갖지 않게).
#   4) 같은 clip_uid에 대해 자동 스윕과 겹치지 않는다 — 스윕이 쓰는 것과
#      **같은** 클립 락을 잡는다(중복 클릭 방지도 여기서 같이 해결된다).

class ClipMetricsRefresh(BaseModel):
    clipInput: str          # 클립 URL 또는 UID


_METRICS_WINDOW = 60.0
_METRICS_LIMIT = int(os.getenv("SINGCUP_METRICS_REFRESH_RATE_LIMIT", "10"))
_metrics_hits: list[float] = []


def _metrics_rate_limit():
    """Preview·Apply 공용 한도. 둘 다 외부 호출을 유발하므로 같은 예산에서 쓴다."""
    now = _time.monotonic()
    cutoff = now - _METRICS_WINDOW
    while _metrics_hits and _metrics_hits[0] < cutoff:
        _metrics_hits.pop(0)
    if len(_metrics_hits) >= _METRICS_LIMIT:
        raise HTTPException(status_code=429,
                            detail="갱신 요청이 너무 잦습니다. 잠시 후 다시 시도하세요.")
    _metrics_hits.append(now)


async def _clip_metrics_row(uid: str) -> dict:
    """DB에 저장된 현재 상태. 없으면 404."""
    import singcup_clips as sc
    db = await get_db()
    r = await (await db.execute(
        "SELECT c.clip_uid, c.event_id, c.owner_channel_id, c.clip_title,"
        "       c.video_id, c.rec_id, c.heart_count, c.view_count, c.metrics_ok,"
        "       c.active, c.deletion_state, c.blind_type, c.last_attempt_at,"
        "       c.last_heart_at, c.last_view_at, c.last_metrics_at,"
        "       c.metrics_recovered_at, s.channel_name,"
        "       s.representative_clip_uid,"
        "       (s.representative_clip_uid = c.clip_uid) AS is_rep,"
        "       (SELECT o.override_clip_uid FROM singcup_representative_overrides o"
        "         WHERE o.event_id=c.event_id AND o.owner_channel_id=c.owner_channel_id"
        "           AND o.cleared_at IS NULL) AS override_clip_uid "
        "FROM singcup_clips c "
        "LEFT JOIN singcup_streamers s ON s.channel_id = c.owner_channel_id "
        "WHERE c.clip_uid=? AND c.event_id=?", (uid, sc.EVENT_ID))).fetchone()
    if r is None:
        raise HTTPException(status_code=404,
                            detail="이 이벤트에 등록된 클립이 아닙니다.")
    row = dict(r)
    return {
        "clipUid": row["clip_uid"], "eventId": row["event_id"],
        "ownerChannelId": row["owner_channel_id"],
        "channelName": row["channel_name"] or "",
        "clipTitle": row["clip_title"] or "",
        "heartCount": int(row["heart_count"] or 0),
        "viewCount": int(row["view_count"] or 0),
        "metricsOk": bool(row["metrics_ok"]),
        "lastAttemptAt": int(row["last_attempt_at"] or 0),
        "lastHeartAt": int(row["last_heart_at"] or 0),
        "lastViewAt": int(row["last_view_at"] or 0),
        "lastMetricsAt": int(row["last_metrics_at"] or 0),
        "metricsRecoveredAt": int(row["metrics_recovered_at"] or 0),
        # '한 번도 못 읽음'과 '진짜 0'을 구분해 보여준다 — 이걸 못 보면 조회수 0을
        # 앞에 두고 고장인지 정상인지 판단할 수 없다.
        "viewState": sc.view_state(row), "heartState": sc.heart_state(row),
        "active": bool(row["active"]),
        "deletionState": row["deletion_state"] or "",
        "blindType": row["blind_type"] or "",
        "isRepresentative": bool(row["is_rep"]),
        # 이 참가자의 **현재 대표**와 수동 override 유무. 지표를 갱신하면 자동 선정
        # 규칙(하트↓ → 조회수↓)의 1등이 달라질 수 있어서, 화면이 그 가능성을 미리
        # 경고하고 결과에서 전후를 대조할 수 있어야 한다.
        "ownerRepresentativeClipUid": row["representative_clip_uid"],
        "hasOverride": bool(row["override_clip_uid"]),
        "overrideClipUid": row["override_clip_uid"],
        "_video_id": row["video_id"], "_rec_id": row["rec_id"],
    }


async def _fetch_clip_metrics(stored: dict) -> tuple[dict | None, list[dict]]:
    """자동 스윕과 **같은** bounded fetch 경로. (병합 결과, 시도별 관측)."""
    import singcup_clips as sc
    trace: list[dict] = []
    item = {"clipUID": stored["clipUid"], "videoId": stored["_video_id"] or "",
            "recId": stored["_rec_id"] or "{}"}
    card = await sc.fetch_card_metrics(sc._get_client(), item, trace=trace)
    return card, trace


def _external_view(card: dict | None, trace: list[dict]) -> dict:
    import singcup_clips as sc
    return {
        "ok": card is not None,
        "attempts": len(trace),
        "maxAttempts": 1 + sc.PARTIAL_RETRY_MAX,
        "heartCount": card["heart_count"] if card and card["heart_ok"] else None,
        "viewCount": card["view_count"] if card and card["view_ok"] else None,
        "heartOk": bool(card and card["heart_ok"]),
        "viewOk": bool(card and card["view_ok"]),
        "partial": bool(card and not card["metrics_ok"]),
        "missingReason": (card or {}).get("missing_reason") or "",
        "attemptTrace": trace,
    }


@router.post("/singcup/clips/metrics/preview")
async def singcup_clip_metrics_preview(body: ClipMetricsRefresh,
                                       user: dict = Depends(_require_owner)):
    """갱신 전 확인. **아무것도 쓰지 않는다.**

    외부 호출 횟수는 bounded retry 계약을 그대로 따른다(최대 1+PARTIAL_RETRY_MAX회).
    응답의 `external.attempts`가 실제 호출 수다.
    """
    _metrics_rate_limit()
    uid = _parse_clip_input(body.clipInput)
    stored = await _clip_metrics_row(uid)
    card, trace = await _fetch_clip_metrics(stored)

    # 저장한다면 어떤 값이 되는가 — 읽지 못한 필드는 그대로 보존된다.
    pending = {
        "heartCount": (card["heart_count"] if card and card["heart_ok"]
                       else stored["heartCount"]),
        "viewCount": (card["view_count"] if card and card["view_ok"]
                      else stored["viewCount"]),
        "heartWillChange": bool(card and card["heart_ok"]
                                and card["heart_count"] != stored["heartCount"]),
        "viewWillChange": bool(card and card["view_ok"]
                               and card["view_count"] != stored["viewCount"]),
    }
    # 갱신하면 자동 대표가 움직일 수 있는가. override가 걸려 있으면 재계산이
    # override를 우선하므로 대표는 유지된다.
    rep_risk = {
        "hasOverride": stored["hasOverride"],
        "overrideClipUid": stored["overrideClipUid"],
        "currentRepresentativeClipUid": stored["ownerRepresentativeClipUid"],
        # override가 없고 값이 실제로 바뀔 예정이면 순서가 뒤집힐 수 있다
        "mayChangeAutoRepresentative": bool(
            not stored["hasOverride"]
            and (pending["heartWillChange"] or pending["viewWillChange"])),
    }
    stored = {k: v for k, v in stored.items() if not k.startswith("_")}
    return {"clipUid": uid, "stored": stored,
            "external": _external_view(card, trace), "pending": pending,
            "representativeRisk": rep_risk,
            "note": ("" if card else "외부 조회에 실패했습니다. 저장할 값이 없습니다.")}


@router.post("/singcup/clips/metrics/apply")
async def singcup_clip_metrics_apply(body: ClipMetricsRefresh,
                                     user: dict = Depends(_require_owner)):
    """지표를 지금 갱신한다. 대표 클립은 **바꾸지 않는다.**

    순서: 입력 축소 → DB 재검증 → 클립 락 → 외부 bounded fetch(트랜잭션 밖) →
    DB 재검증 → `_apply_metrics`(읽은 필드만) → 순위 재계산 → `/main` 캐시 무효화.

    Preview의 값을 그대로 저장하지 않는다 — 오래된 미리보기가 되살아나 최신 값을
    덮는 것을 막기 위해, Apply는 자기 몫의 조회를 새로 한다.
    """
    import singcup_clips as sc
    import singcup_sweep as sw
    _metrics_rate_limit()
    uid = _parse_clip_input(body.clipInput)
    await _clip_metrics_row(uid)                    # 존재 확인(404)

    # 자동 스윕이 쓰는 것과 **같은** 락이다. 잡히면 그 클립은 지금 스윕이 처리
    # 중이거나 다른 Apply가 진행 중이다 — 중복 클릭도 여기서 막힌다.
    token = await sc.acquire_clip_lock(uid)
    if token is None:
        raise HTTPException(status_code=409,
                            detail="이 클립을 다른 작업이 처리 중입니다. 잠시 후 다시 시도하세요.")
    try:
        stored = await _clip_metrics_row(uid)       # 락을 잡은 뒤 다시 읽는다
        card, trace = await _fetch_clip_metrics(stored)
        if card is None or not (card["heart_ok"] or card["view_ok"]):
            raise HTTPException(
                status_code=502,
                detail="외부 조회에서 유효한 값을 받지 못했습니다. 저장하지 않았습니다.")

        now = int(_time.time())

        async def work(_db):
            await sc._apply_metrics(uid, card["heart_count"], card["view_count"],
                                    card["heart_ok"], card["view_ok"], now)

        if not await sw.db_write(work, what=f"admin_metrics({uid})"):
            raise HTTPException(status_code=503,
                                detail="DB 잠금으로 저장하지 못했습니다. 잠시 후 다시 시도하세요.")
    finally:
        await sc.release_clip_lock(uid, token)

    # 감사 기록. 값과 시도 횟수만 남긴다 — 토큰·시크릿·원본 응답은 남기지 않는다.
    sc._log({"event": "admin_clip_metrics_applied", "clip_uid": uid,
             "actor": str(user.get("sub") or "")[:32], "attempts": len(trace),
             "heart_ok": card["heart_ok"], "view_ok": card["view_ok"],
             "heart_from": stored["heartCount"], "heart_to": card["heart_count"],
             "view_from": stored["viewCount"], "view_to": card["view_count"],
             "view_state_from": stored["viewState"]})

    # 재계산은 외부 채널 API를 부르지만 트랜잭션 밖이다. 실패해도 지표는 이미
    # 저장됐으므로 다음 정기 회차가 순위를 맞춘다.
    try:
        await sc.recompute_ranking(now)
        recomputed = True
    except Exception:                                   # noqa: BLE001
        recomputed = False
    sc.invalidate_main_cache()                          # ETag는 다음 요청에 새로 만들어진다

    after = await _clip_metrics_row(uid)
    rep_before = stored["ownerRepresentativeClipUid"]
    rep_after = after["ownerRepresentativeClipUid"]
    changed = rep_before != rep_after
    if changed:
        sc._log({"event": "admin_clip_metrics_auto_rep_changed", "clip_uid": uid,
                 "owner_channel_id": after["ownerChannelId"],
                 "rep_from": rep_before, "rep_to": rep_after,
                 "had_override": after["hasOverride"]})
    return {"ok": True, "clipUid": uid, "recomputed": recomputed,
            "before": {k: v for k, v in stored.items() if not k.startswith("_")},
            "after": {k: v for k, v in after.items() if not k.startswith("_")},
            "external": _external_view(card, trace),
            # 이 동작은 대표를 **지정**하지 않는다. 다만 갱신된 지표로 순위를 다시
            # 계산하므로 자동 선정 규칙의 1등이 바뀌면 대표도 따라 움직인다.
            # 전후 clip UID를 함께 돌려줘 화면이 그 전이를 설명할 수 있게 한다.
            "autoRepresentativeChanged": changed,
            "representativeBeforeClipUid": rep_before,
            "representativeAfterClipUid": rep_after,
            "hasOverride": after["hasOverride"],
            # 하위 호환 — 기존 필드명 유지
            "representativeUnchanged": not changed}


@router.post("/singcup/representative/clear")
async def singcup_rep_clear(body: RepOverrideClear,
                            user: dict = Depends(_require_owner)):
    """수동 지정을 해제한다 → 자동 대표로 복귀한다."""
    import singcup_clips as sc
    import singcup_overrides as so
    await _rep_state(body.channelId)
    res = await so.clear_override(body.channelId, sc.EVENT_ID)
    try:
        await sc.recompute_ranking(int(_time.time()))
        recomputed = True
    except Exception:                                   # noqa: BLE001
        sc.invalidate_main_cache()
        recomputed = False
    return {"ok": True, "cleared": res["cleared"], "recomputed": recomputed,
            "state": await _rep_state(body.channelId)}


# ── 스트리머 팀/소속 태그 (TAG-1) ────────────────────────────────────────────
#
# 이 블록은 **파일 끝에 붙인다.** 다른 대기 중인 작업(V2 시간별 방문자)이
# `overview()` 바로 뒤를 삽입 지점으로 쓰고 있어, 같은 자리를 노리면 두 변경이
# 서로를 밀어낸다. 꼬리는 비어 있다.
#
# 전부 OWNER 전용이다 — `_require_owner`가 미인증은 401(`get_current_user`),
# 비OWNER는 403을 낸다. 공개 화면이 쓰는 조회 경로는 여기가 아니라
# `rising_router`의 기존 응답에 필드로 실린다(요청 수를 늘리지 않는다).

class TagCreate(BaseModel):
    name: str
    # 구형 4필드는 **기본값째로** 남겨 둔다 — 이름만 보내는 기존 클라이언트가
    # 그대로 동작해야 한다. `colorStops`가 함께 오면 그쪽이 이긴다
    # (`streamer_tags._style_for_write`).
    colorMode: str = "solid"
    colorStart: str = "#38BDF8"
    colorEnd: Optional[str] = None
    gradientDirection: str = "to-right"
    # 신형 — `[{"color": "#rrggbb", "pos": 0..100}, ...]`. 값 검증은 라우터가 아니라
    # `streamer_tags.clean_stops`가 한다(색 검증 지점이 둘로 갈라지면 안 된다).
    colorStops: Optional[list] = None
    kind: str = "team"
    # 이 그룹의 멤버를 **전체 스트리머 랭킹에서만** 뺄지. 기본은 끔.
    excludeFromRanking: bool = False


class TagUpdate(BaseModel):
    name: Optional[str] = None
    colorMode: Optional[str] = None
    colorStart: Optional[str] = None
    colorEnd: Optional[str] = None
    gradientDirection: Optional[str] = None
    colorStops: Optional[list] = None
    active: Optional[bool] = None
    excludeFromRanking: Optional[bool] = None


class TagAssign(BaseModel):
    channelId: str
    tagId: int


class TagReorder(BaseModel):
    channelId: str
    tagIds: list[int]


class GroupMemberReorder(BaseModel):
    """그룹 안에서 멤버 순서를 다시 매긴다(스트리머 기준 reorder와 축이 다르다)."""
    channelIds: list[str]


def _tag_400(exc: Exception) -> HTTPException:
    """검증 실패를 400으로 바꾼다.

    메시지는 `streamer_tags`가 만든 한국어 문구를 그대로 쓴다 — 라우터가 다시
    쓰면 같은 규칙에 두 가지 설명이 생긴다.
    """
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/streamer-tags")
async def streamer_tags_list(includeInactive: bool = False,
                             user: dict = Depends(_require_owner)):
    """태그 목록 + 각 태그의 지정 수."""
    import streamer_tags as st
    return {"tags": await st.list_tags(include_inactive=includeInactive),
            "maxPerStreamer": st.MAX_TAGS_PER_STREAMER,
            "gradientDirections": list(st.GRADIENT_DIRECTIONS),
            # 편집기가 "더 추가" 버튼을 언제 막을지 서버 값으로 정한다 —
            # 프론트 상수로 두면 서버 상한과 조용히 갈라진다.
            "maxColorStops": st.MAX_COLOR_STOPS,
            "minColorStops": st.MIN_COLOR_STOPS,
            "version": st.version()}


@router.post("/streamer-tags")
async def streamer_tags_create(body: TagCreate,
                               user: dict = Depends(_require_owner)):
    import streamer_tags as st
    try:
        return {"ok": True, "tag": await st.create_tag(
            name=body.name, color_mode=body.colorMode,
            color_start=body.colorStart, color_end=body.colorEnd,
            gradient_direction=body.gradientDirection, kind=body.kind,
            color_stops=body.colorStops,
            exclude_from_ranking=body.excludeFromRanking)}
    except st.TagError as e:
        raise _tag_400(e) from e


@router.patch("/streamer-tags/{tag_id}")
async def streamer_tags_update(tag_id: int, body: TagUpdate,
                               user: dict = Depends(_require_owner)):
    """이름·색상·활성 여부를 고친다.

    **삭제 경로는 두지 않는다.** `active=false`로 내리면 공개 화면에서 즉시 사라지고
    지정 이력은 남아, 잘못 내렸을 때 되돌리는 데 아무 데이터도 필요하지 않다.
    """
    import streamer_tags as st
    try:
        return {"ok": True, "tag": await st.update_tag(
            tag_id, name=body.name, color_mode=body.colorMode,
            color_start=body.colorStart, color_end=body.colorEnd,
            gradient_direction=body.gradientDirection, active=body.active,
            color_stops=body.colorStops,
            exclude_from_ranking=body.excludeFromRanking)}
    except st.TagError as e:
        raise _tag_400(e) from e


@router.get("/streamer-tags/{tag_id}/assignments")
async def streamer_tags_assignments(tag_id: int,
                                    q: str | None = None,
                                    limit: int | None = None,
                                    offset: int = 0,
                                    user: dict = Depends(_require_owner)):
    """이 소속 그룹의 멤버 목록(관리 화면 전용).

    **새 엔드포인트를 만들지 않고** 기존 경로에 검색·페이지 파라미터만 더했다 —
    같은 자원을 두 경로가 서빙하면 캐시·권한·직렬화가 곧 갈라진다.

    비활성 그룹도 그대로 조회된다. 공개 화면에서는 숨기지만 **관리자는 계속
    멤버를 확인하고 고칠 수 있어야** 하기 때문이다(비활성 = 숨김이지 삭제가 아니다).
    """
    import streamer_tags as st
    tag = await st.get_tag(tag_id)
    if tag is None:
        raise HTTPException(status_code=404, detail="존재하지 않는 소속 그룹입니다.")
    try:
        page = await st.assignments_of_tag(
            tag_id, limit=limit or st.MEMBER_PAGE_DEFAULT, offset=offset, search=q)
    except st.TagError as e:
        raise _tag_400(e) from e
    return {"tagId": tag_id,
            "tag": {"id": int(tag["id"]), "name": tag["name"],
                    "active": bool(tag["active"]),
                    "colorMode": tag["color_mode"], "colorStart": tag["color_start"],
                    "colorEnd": tag["color_end"],
                    "gradientDirection": tag["gradient_direction"],
                    # 이 응답으로도 배지를 그리므로 신형 표현을 함께 준다 —
                    # 빠지면 멤버 드로어의 배지만 2색으로 근사돼 목록과 달라 보인다.
                    "colorStops": st.stops_of(tag),
                    "slug": tag["slug"], "kind": tag["kind"]},
            # 하위 호환 — 기존 키를 유지한다(이미 쓰는 코드가 있으면 깨지지 않게)
            "streamers": page["items"],
            **page}


@router.get("/streamer-tags/search")
async def streamer_tags_search(keyword: str, limit: int = 20,
                               user: dict = Depends(_require_owner)):
    """스트리머 검색(이름 부분일치 또는 채널 ID 완전일치) + 현재 지정 태그."""
    import streamer_tags as st
    try:
        return {"streamers": await st.search_streamers(keyword, limit)}
    except st.TagError as e:
        raise _tag_400(e) from e


@router.post("/streamer-tags/assign")
async def streamer_tags_assign(body: TagAssign,
                               user: dict = Depends(_require_owner)):
    import streamer_tags as st
    try:
        res = await st.assign(body.channelId, body.tagId)
    except st.TagError as e:
        raise _tag_400(e) from e
    return {"ok": True, **res,
            "tags": await st.tags_for_channel(body.channelId)}


@router.post("/streamer-tags/unassign")
async def streamer_tags_unassign(body: TagAssign,
                                 user: dict = Depends(_require_owner)):
    import streamer_tags as st
    try:
        res = await st.unassign(body.channelId, body.tagId)
    except st.TagError as e:
        raise _tag_400(e) from e
    return {"ok": True, **res,
            "tags": await st.tags_for_channel(body.channelId)}


@router.post("/streamer-tags/reorder")
async def streamer_tags_reorder(body: TagReorder,
                                user: dict = Depends(_require_owner)):
    import streamer_tags as st
    try:
        res = await st.reorder(body.channelId, body.tagIds)
    except st.TagError as e:
        raise _tag_400(e) from e
    return {"ok": True, **res,
            "tags": await st.tags_for_channel(body.channelId)}


@router.post("/streamer-tags/{tag_id}/members/reorder")
async def streamer_tags_member_reorder(tag_id: int, body: GroupMemberReorder,
                                       user: dict = Depends(_require_owner)):
    """그룹 안 멤버 순서 변경. 스트리머 기준 `/reorder`와 **축이 다르다** —
    그쪽은 "한 스트리머의 그룹 순서", 이쪽은 "한 그룹의 멤버 순서"다."""
    import streamer_tags as st
    if await st.get_tag(tag_id) is None:
        raise HTTPException(status_code=404, detail="존재하지 않는 소속 그룹입니다.")
    try:
        return {"ok": True, **await st.reorder_members(tag_id, body.channelIds)}
    except st.TagError as e:
        raise _tag_400(e) from e


# ── PIKU 사용자 투표 순위 (관리) ────────────────────────────────────────────
#
# **OWNER JWT를 강제한다** — Nexadmin의 다른 기능과 같은 인증이다. 인증 방식이
# 화면마다 다르면 권한 검사가 갈라지고, 어느 쪽이 진짜 관문인지 알 수 없게 된다.
#
# 이 경로들은 진단용이지만 **비율·승률 숫자는 여기서도 내보내지 않는다**
# (`singcup_piku.admin_status()` 참고). 진단에 필요한 것은 건수와 매핑 현황이다.


class PikuSources(BaseModel):
    female_solo: Optional[str] = None
    male_solo: Optional[str] = None
    groups: Optional[str] = None


class PikuMappingBody(BaseModel):
    division: str
    pikuName: str
    channelId: Optional[str] = None
    state: str = "confirmed"


class PikuImportBody(BaseModel):
    division: str
    #: JSON 행 배열 또는 CSV 원문 중 하나. 둘 다 없으면 400.
    rows: Optional[list] = None
    csv: Optional[str] = None


def _piku_400(exc: Exception) -> HTTPException:
    """모듈이 만든 한국어 문구를 그대로 쓴다 — 라우터가 다시 쓰면 설명이 둘이 된다."""
    kind = getattr(exc, "kind", "")
    return HTTPException(status_code=400,
                         detail=f"[{kind}] {exc}" if kind else str(exc))


@router.get("/piku/status")
async def piku_admin_status(user: dict = Depends(_require_owner)):
    """부문 매핑·실행 이력·미매핑 목록."""
    import singcup_piku as piku
    return await piku.admin_status()


@router.post("/piku/sources")
async def piku_sources(body: PikuSources, user: dict = Depends(_require_owner)):
    """부문 ↔ URL 매핑. **세 부문 전부** 필요하고 중복 URL은 거절된다.

    세 URL의 부문 대응을 추측하지 않기 위한 화면이다 — 운영자가 직접 정한다.
    """
    import singcup_piku as piku
    try:
        return {"ok": True, "sources": await piku.set_sources(body.model_dump())}
    except piku.PikuError as e:
        raise _piku_400(e) from e


# ── 걷어낸 PIKU 경로 (되살리지 말 것) ──────────────────────────────────────
#
# `/piku/collect`, `/piku/collect-all`, `/piku/preview-live`는 **서버가 PIKU에
# 직접 요청**하는 경로였다. Railway·AWS 서울 EC2 모두 403을 받으므로 지금은
# 어떤 경우에도 성공하지 않는다. `/piku/import`는 검증 직후 곧바로 활성화해서
# **한 부문만 공개되는 상태**를 만들 수 있었다 — Collector의 "세 부문 원자 공개"
# 계약을 우회하는 뒷문이었다.
#
# 대체 경로는 아래 `/piku/collector/*` 하나뿐이다. 수동 JSON/CSV도 draft로만
# 들어가고, 이름 매핑을 확정한 뒤 세 부문을 함께 공개한다.


@router.post("/piku/preview")
async def piku_preview(body: PikuImportBody, user: dict = Depends(_require_owner)):
    """**저장하지 않는 검증**(dry-run) — 반영 전에 형태를 먼저 본다.

    활성 dataset을 건드리지 않으므로 실패해도 마지막 정상 데이터가 남는다.
    응답에 우승 비율·승률 숫자는 담지 않는다(관리 화면에서도 값은 쓰지 않는다).
    """
    import singcup_piku as piku
    try:
        raw = piku.parse_csv(body.csv) if body.csv else body.rows
        if raw is None:
            raise piku.PikuError("empty", "확인할 데이터가 없습니다.")
        return {"ok": True, **await piku.preview_rows(body.division, raw)}
    except piku.PikuError as e:
        raise _piku_400(e) from e


@router.get("/piku/mappings")
async def piku_mappings(division: Optional[str] = None,
                        user: dict = Depends(_require_owner)):
    """PIKU 이름 ↔ 공식 참가자 매핑과 **연결 후보 목록**.

    후보를 함께 주는 이유: 관리 화면이 채널 id를 손으로 입력하게 하면 오타 하나로
    엉뚱한 사람에게 붙는다. 그 오류는 화면에서 보이지 않는다.
    """
    import singcup_piku as piku

    from routers.singcup_router import qualifier_candidates

    return {"mappings": await piku.list_mappings(division),
            "divisions": [{"key": d, "label": piku.DIVISION_LABELS[d]}
                          for d in piku.DIVISIONS],
            "candidates": qualifier_candidates(division)}


@router.post("/piku/mappings")
async def piku_set_mapping(body: PikuMappingBody,
                           user: dict = Depends(_require_owner)):
    """매핑 확정/해제. **자동 유사도 매칭은 없다** — 확정은 사람이 한다."""
    import singcup_piku as piku
    try:
        return {"ok": True, **await piku.set_mapping(
            body.division, body.pikuName, body.channelId, state=body.state)}
    except piku.PikuError as e:
        raise _piku_400(e) from e


# ── 브라우저 기반 PIKU Collector ────────────────────────────────────────────
#
# Railway·AWS 서울 EC2 모두 PIKU에서 403을 받는다. 우회하지 않고, PIKU가 정상
# 열리는 **운영자 브라우저**가 이미 렌더된 공개 표를 읽어 보내는 경로를 쓴다.
# 서버는 받기만 하고 PIKU에 직접 요청하지 않는다.
#
# 확장 프로그램에는 어떤 secret도 넣지 않는다. 운영자가 아래 `token`을 눌러
# 그때마다 **짧고 한 번만 쓰는** 토큰을 발급받아 확장에 넘긴다.

class CollectorTokenBody(BaseModel):
    division: str


@router.post("/piku/collector/token")
async def piku_collector_token(body: CollectorTokenBody,
                               user: dict = Depends(_require_owner)):
    """수집 토큰 발급 — **원문은 이 응답에서 한 번만** 나온다(DB에는 해시만)."""
    import singcup_piku_collector as col
    try:
        return {"ok": True, **await col.issue_token(body.division)}
    except Exception as e:
        raise _piku_400(e) from e


@router.post("/piku/collector/ingest")
async def piku_collector_ingest(request: Request):
    """브라우저가 읽은 랭킹 행을 받는다. **draft까지만** 간다(공개 안 함).

    OWNER JWT 대신 단기 토큰을 쓴다 — 확장이 장기 자격 증명을 들고 있지 않게
    하기 위해서다. 토큰은 부문에 묶여 있고 1회용이라, 새어도 재사용되지 않는다.
    """
    import singcup_piku_collector as col
    token = request.headers.get("X-Collector-Token", "")
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="본문을 읽지 못했습니다.") from e
    division = body.get("division") if isinstance(body, dict) else None
    try:
        # 토큰을 **먼저** 소비한다 — 검증 실패한 요청으로 토큰을 무한히 시험하지
        # 못하게 한다(1회용이므로 실패해도 그 토큰은 끝난다).
        await col.consume_token(token, division)
        return {"ok": True, **await col.save_draft(body)}
    except col.PikuError as e:
        raise _piku_400(e) from e


@router.post("/piku/collector/preview")
async def piku_collector_preview(request: Request,
                                 user: dict = Depends(_require_owner)):
    """검증만 — **DB write 0건.** 형식이 틀려도 기존 데이터가 그대로 남는다."""
    import singcup_piku_collector as col
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="본문을 읽지 못했습니다.") from e
    try:
        return {"ok": True, **await col.preview(body)}
    except col.PikuError as e:
        raise _piku_400(e) from e


@router.get("/piku/collector/status")
async def piku_collector_status(user: dict = Depends(_require_owner)):
    """Collector 상태 — 부문별 최근 수집·draft 행 수·Publish 가능 여부."""
    import singcup_piku_collector as col
    return await col.status()


class CollectorFailureBody(BaseModel):
    division: str
    kind: str


@router.post("/piku/collector/failure")
async def piku_collector_failure(body: CollectorFailureBody):
    """브라우저 쪽 실패(차단 화면·CAPTCHA·미렌더·중단)를 **실패로** 남긴다.

    성공으로 위장하지 않는 것이 요점이다. 인증을 요구하지 않는 대신 상태만
    기록하고 데이터는 받지 않는다.
    """
    import singcup_piku_collector as col
    try:
        return {"ok": True, **await col.record_client_failure(body.division,
                                                              body.kind)}
    except col.PikuError as e:
        raise _piku_400(e) from e


@router.post("/piku/collector/publish")
async def piku_collector_publish(user: dict = Depends(_require_owner)):
    """세 부문 draft를 **한 번에** 공개한다. 하나라도 없으면 아무것도 바꾸지 않는다.

    자동으로 불리지 않는다 — 운영자가 눌러야만 실행된다.
    """
    import singcup_piku_collector as col
    try:
        return {"ok": True, **await col.publish_drafts()}
    except col.PikuError as e:
        raise _piku_400(e) from e


@router.post("/piku/collector/import")
async def piku_collector_import(body: PikuImportBody,
                                user: dict = Depends(_require_owner)):
    """수동 JSON/CSV — **draft로만** 저장한다(공개하지 않는다).

    예전 `/piku/import`는 검증 직후 곧바로 활성화해서 한 부문만 공개되는 상태를
    만들 수 있었다. 이 경로는 같은 검증을 거치되 draft에서 멈춘다.
    """
    import singcup_piku_collector as col
    try:
        return {"ok": True, **await col.import_manual(body.model_dump())}
    except col.PikuError as e:
        raise _piku_400(e) from e


@router.get("/piku/collector/mappings")
async def piku_collector_mappings(division: str,
                                  user: dict = Depends(_require_owner)):
    """draft 기준 매핑 목록 + 후보. **비율값은 담지 않는다.**"""
    import singcup_piku_collector as col
    try:
        return {"ok": True, **await col.draft_mappings(division),
                "candidates": await col.official_candidates(division)}
    except col.PikuError as e:
        raise _piku_400(e) from e


class CollectorMappingBody(BaseModel):
    division: str
    pikuName: str
    #: None이면 연결 해제(운영자가 명시적으로 지운 것으로 본다).
    channelId: Optional[str] = None


@router.post("/piku/collector/mapping")
async def piku_collector_set_mapping(body: CollectorMappingBody,
                                     user: dict = Depends(_require_owner)):
    """한 행의 매핑을 확정하거나 해제한다."""
    import singcup_piku_collector as col
    try:
        return {"ok": True, **await col.set_mapping(body.division, body.pikuName,
                                                    body.channelId)}
    except col.PikuError as e:
        raise _piku_400(e) from e


class CollectorConfirmBody(BaseModel):
    division: str


@router.post("/piku/collector/confirm-exact")
async def piku_collector_confirm_exact(body: CollectorConfirmBody,
                                       user: dict = Depends(_require_owner)):
    """**정확히 일치한 것만** 일괄 확정한다. 유사도 매칭은 하지 않는다."""
    import singcup_piku_collector as col
    try:
        return {"ok": True, **await col.confirm_exact(body.division)}
    except col.PikuError as e:
        raise _piku_400(e) from e


@router.get("/piku/collector/publish-preview")
async def piku_collector_publish_preview(user: dict = Depends(_require_owner)):
    """공개하면 무엇이 바뀌는지. **DB write 0건.**"""
    import singcup_piku_collector as col
    return await col.publish_preview()
