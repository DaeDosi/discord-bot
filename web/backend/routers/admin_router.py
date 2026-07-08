import os
import time as _time
import asyncio
import httpx
from datetime import date, datetime, timezone, timedelta

_KST = timezone(timedelta(hours=9))


def _today_kst() -> date:
    return datetime.now(_KST).date()
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional
from deps import get_current_user
from database import get_db

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


# 인증자 수가 많은 길드에서 개별 멤버 조회를 동시에 쏘면 Discord의 길드별
# member-fetch 레이트리밋(429)에 걸려 이름 대신 user_id로 조용히 폴백되던 문제가
# 있었다. 동시 요청 수를 제한하고 429는 retry_after만큼 기다렸다가 한 번 재시도한다.
_MEMBER_FETCH_SEMAPHORE = asyncio.Semaphore(5)


async def _fetch_member_name(client: httpx.AsyncClient, guild_id: str, user_id: str) -> str:
    url = f"{_DISCORD}/guilds/{guild_id}/members/{user_id}"
    headers = {"Authorization": f"Bot {_BOT_TOKEN}"}
    async with _MEMBER_FETCH_SEMAPHORE:
        for attempt in range(2):
            try:
                resp = await client.get(url, headers=headers, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    return (
                        data.get("nick")
                        or data.get("user", {}).get("global_name")
                        or data.get("user", {}).get("username")
                        or user_id
                    )
                if resp.status_code == 429 and attempt == 0:
                    retry_after = resp.json().get("retry_after", 1)
                    await asyncio.sleep(min(retry_after, 5))
                    continue
                break
            except Exception:
                break
    return user_id


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

    today = date.today().isoformat()
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
async def guilds(user: dict = Depends(_require_owner)):
    db          = await get_db()
    guilds_list = await _bot_guilds()

    chzzk_rows = await (await db.execute(
        "SELECT guild_id, chzzk_name FROM chzzk_subscriptions"
    )).fetchall()
    chzzk_map = {str(r["guild_id"]): r["chzzk_name"] for r in chzzk_rows}

    return [
        {
            "id":         g["id"],
            "name":       g["name"],
            "icon":       g.get("icon"),
            "chzzk_name": chzzk_map.get(g["id"]),
        }
        for g in guilds_list
    ]


@router.get("/chzzk")
async def chzzk_all(user: dict = Depends(_require_owner)):
    db          = await get_db()
    rows        = await (await db.execute(
        """SELECT id, guild_id, chzzk_channel_id, chzzk_name, chzzk_image_url,
                  discord_channel, mention_everyone, is_live,
                  follow_role_1month, follow_role_3month,
                  follow_months_tier1, follow_months_tier2
           FROM chzzk_subscriptions ORDER BY guild_id"""
    )).fetchall()

    guilds_list    = await _bot_guilds()
    guild_name_map = {g["id"]: g["name"] for g in guilds_list}

    return [
        {**dict(r), "guild_name": guild_name_map.get(str(r["guild_id"]), str(r["guild_id"]))}
        for r in rows
    ]


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

    async with httpx.AsyncClient() as client:
        user_names = await asyncio.gather(*[
            _fetch_member_name(client, str(r["guild_id"]), str(r["user_id"]))
            for r in rows
        ])

    result = []
    for r, name in zip(rows, user_names):
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

    async with httpx.AsyncClient() as client:
        user_names = await asyncio.gather(*[
            _fetch_member_name(client, str(v["guild_id"]), str(v["user_id"]))
            for v in verif_rows
        ])

    verif_by_guild: dict[int, list] = {}
    for v, name in zip(verif_rows, user_names):
        verif_by_guild.setdefault(v["guild_id"], []).append({
            "user_id":     v["user_id"],
            "user_name":   name,
            "tier_months": v["tier_months"],
            "verified_at": v["verified_at"],
        })

    return [
        {
            "sub_id":            s["id"],
            "guild_id":          s["guild_id"],
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
async def guild_detail(guild_id: str, user: dict = Depends(_require_owner)):
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
        """SELECT chzzk_channel_id, chzzk_name, chzzk_image_url,
                  discord_channel, notify_vod, notify_clip, notify_community,
                  is_live, streamer_access_token
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
        "chzzk": dict(chzzk_row) if chzzk_row else None,
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
