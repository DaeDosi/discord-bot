import os
import time as _time
import asyncio
import httpx
from datetime import date, datetime, timezone, timedelta

_KST = timezone(timedelta(hours=9))


def _today_kst() -> date:
    return datetime.now(_KST).date()
from fastapi import APIRouter, HTTPException, Depends, Query, Response
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
