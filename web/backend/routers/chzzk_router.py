import os
import asyncio
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from deps import get_current_user, require_guild_admin
from database import get_db
from chzzk_monitor import check_once_debug

router = APIRouter(prefix="/api/chzzk", tags=["chzzk"])

CHZZK_API    = "https://api.chzzk.naver.com"
HEADERS      = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_BOT_TOKEN   = os.getenv("DISCORD_TOKEN", "")
_DISCORD_API = "https://discord.com/api/v10"


async def _fetch_member_name(client: httpx.AsyncClient, guild_id: str, user_id: str) -> str:
    try:
        resp = await client.get(
            f"{_DISCORD_API}/guilds/{guild_id}/members/{user_id}",
            headers={"Authorization": f"Bot {_BOT_TOKEN}"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            return (
                data.get("nick")
                or data.get("user", {}).get("global_name")
                or data.get("user", {}).get("username")
                or user_id
            )
    except Exception:
        pass
    return user_id


# ── 검색 (로그인만 필요, 서버 관리자 불필요) ──────────────────────────────────
@router.get("/search")
async def search(keyword: str, user: dict = Depends(get_current_user)):
    url = f"{CHZZK_API}/service/v1/search/channels"
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
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


# ── 구독 목록 ─────────────────────────────────────────────────────────────────
@router.get("/{guild_id}/subscriptions")
async def list_subscriptions(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, discord_channel, chzzk_channel_id, chzzk_name, "
        "chzzk_image_url, is_live, mention_role_id, custom_message, "
        "follow_role_1month, follow_role_3month, "
        "follow_months_tier1, follow_months_tier2 "
        "FROM chzzk_subscriptions WHERE guild_id=?",
        (int(guild_id),)
    )).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("follow_role_1month") is not None:
            d["follow_role_1month"] = str(d["follow_role_1month"])
        if d.get("follow_role_3month") is not None:
            d["follow_role_3month"] = str(d["follow_role_3month"])
        result.append(d)
    return result


# ── 구독 추가 ─────────────────────────────────────────────────────────────────
class SubCreate(BaseModel):
    discord_channel:  str
    chzzk_channel_id: str
    chzzk_name:       str
    chzzk_image_url:  Optional[str] = None
    mention_everyone: bool = False
    is_live:          bool = False


@router.post("/{guild_id}/subscriptions")
async def add_subscription(
    guild_id: str,
    body: SubCreate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_subscriptions WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone())[0]
    if count >= 1:
        raise HTTPException(status_code=400, detail="서버당 치지직 알림은 1명만 등록할 수 있습니다.")
    try:
        await db.execute(
            """INSERT INTO chzzk_subscriptions
               (guild_id, discord_channel, chzzk_channel_id, chzzk_name,
                chzzk_image_url, mention_everyone, is_live)
               VALUES (?,?,?,?,?,?,?)""",
            (
                int(guild_id), int(body.discord_channel),
                body.chzzk_channel_id, body.chzzk_name,
                body.chzzk_image_url,
                int(body.mention_everyone),
                int(body.is_live),
            )
        )
        await db.commit()
    except Exception:
        raise HTTPException(status_code=409, detail="이미 구독 중인 채널입니다.")
    return {"ok": True}


# ── 구독 수정 ─────────────────────────────────────────────────────────────────
class SubUpdate(BaseModel):
    discord_channel: Optional[str] = None
    mention_role_id: Optional[str] = None
    custom_message:  Optional[str] = None


@router.patch("/{guild_id}/subscriptions/{sub_id}")
async def update_subscription(
    guild_id: str,
    sub_id: int,
    body: SubUpdate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        "SELECT id FROM chzzk_subscriptions WHERE id=? AND guild_id=?",
        (sub_id, int(guild_id))
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="구독을 찾을 수 없습니다.")

    # 컬럼명은 Pydantic 모델 필드명에서만 결정되므로 SQL injection 없음
    updates: dict = {}
    if body.discord_channel is not None:
        updates["discord_channel"] = int(body.discord_channel)
    if body.mention_role_id is not None:
        updates["mention_role_id"] = int(body.mention_role_id) if body.mention_role_id else None
    if body.custom_message is not None:
        updates["custom_message"] = body.custom_message

    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        await db.execute(
            f"UPDATE chzzk_subscriptions SET {set_clause} WHERE id=?",
            (*updates.values(), sub_id)
        )
        await db.commit()
    return {"ok": True}


# ── 구독 삭제 ─────────────────────────────────────────────────────────────────
@router.delete("/{guild_id}/subscriptions/{sub_id}")
async def delete_subscription(
    guild_id: str,
    sub_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    result = await db.execute(
        "DELETE FROM chzzk_subscriptions WHERE id=? AND guild_id=?",
        (sub_id, int(guild_id))
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="구독을 찾을 수 없습니다.")
    return {"ok": True}


# ── 팔로워 역할 설정 ──────────────────────────────────────────────────────────
class FollowerRoles(BaseModel):
    follow_role_1month:   Optional[str] = None
    follow_role_3month:   Optional[str] = None
    follow_months_tier1:  Optional[int] = 1
    follow_months_tier2:  Optional[int] = 3


@router.get("/{guild_id}/follower-roles")
async def get_follower_roles(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        """SELECT follow_role_1month, follow_role_3month,
                  follow_months_tier1, follow_months_tier2
           FROM chzzk_subscriptions WHERE guild_id=?""",
        (int(guild_id),)
    )).fetchone()
    if not row:
        return {
            "follow_role_1month": None, "follow_role_3month": None,
            "follow_months_tier1": 1,   "follow_months_tier2": 3,
        }
    return {
        "follow_role_1month":  str(row["follow_role_1month"])  if row["follow_role_1month"]  else None,
        "follow_role_3month":  str(row["follow_role_3month"])  if row["follow_role_3month"]  else None,
        "follow_months_tier1": row["follow_months_tier1"] or 1,
        "follow_months_tier2": row["follow_months_tier2"] or 3,
    }


@router.put("/{guild_id}/follower-roles")
async def update_follower_roles(
    guild_id: str,
    body: FollowerRoles,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        "SELECT id FROM chzzk_subscriptions WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="먼저 치지직 채널을 등록해주세요.")
    await db.execute(
        """UPDATE chzzk_subscriptions
           SET follow_role_1month=?, follow_role_3month=?,
               follow_months_tier1=?, follow_months_tier2=?
           WHERE guild_id=?""",
        (
            int(body.follow_role_1month)  if body.follow_role_1month  else None,
            int(body.follow_role_3month)  if body.follow_role_3month  else None,
            body.follow_months_tier1 or 1,
            body.follow_months_tier2 or 3,
            int(guild_id),
        )
    )
    await db.commit()
    return {"ok": True}


# ── 팔로우 역할 다중 티어 ────────────────────────────────────────────────────

class FollowTierCreate(BaseModel):
    months:  int
    role_id: str


@router.get("/{guild_id}/follow-tiers")
async def get_follow_tiers(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, months, role_id FROM chzzk_follow_roles WHERE guild_id=? ORDER BY months ASC",
        (int(guild_id),)
    )).fetchall()
    return [{"id": r["id"], "months": r["months"], "role_id": str(r["role_id"])} for r in rows]


@router.post("/{guild_id}/follow-tiers")
async def add_follow_tier(
    guild_id: str,
    body: FollowTierCreate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_follow_roles WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone())[0]
    if count >= 5:
        raise HTTPException(status_code=400, detail="최대 5개의 티어까지만 추가할 수 있습니다.")
    try:
        await db.execute(
            "INSERT INTO chzzk_follow_roles (guild_id, months, role_id) VALUES (?,?,?)",
            (int(guild_id), body.months, int(body.role_id))
        )
        await db.commit()
    except Exception:
        raise HTTPException(status_code=409, detail="이미 같은 개월 수 티어가 존재합니다.")
    return {"ok": True}


@router.delete("/{guild_id}/follow-tiers/{tier_id}")
async def delete_follow_tier(
    guild_id: str,
    tier_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    await db.execute(
        "DELETE FROM chzzk_follow_roles WHERE id=? AND guild_id=?",
        (tier_id, int(guild_id))
    )
    await db.commit()
    return {"ok": True}


# ── 팔로우 인증 유저 목록 (대시보드용) ───────────────────────────────────────

@router.get("/{guild_id}/verifications")
async def get_guild_verifications(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        """SELECT user_id, tier_months, follow_date, follow_days, verified_at
           FROM chzzk_verifications WHERE guild_id=?
           ORDER BY
               CASE WHEN follow_days >= 0 THEN follow_days ELSE -1 END DESC,
               verified_at DESC""",
        (int(guild_id),)
    )).fetchall()

    async with httpx.AsyncClient(timeout=5) as client:
        user_names = await asyncio.gather(*[
            _fetch_member_name(client, guild_id, str(r["user_id"]))
            for r in rows
        ])

    result = []
    for r, name in zip(rows, user_names):
        fd = r["follow_days"] if r["follow_days"] is not None else -1
        result.append({
            "user_id":      str(r["user_id"]),
            "user_name":    name,
            "tier_months":  r["tier_months"] or 0,
            "follow_date":  r["follow_date"],
            "follow_days":  fd,
            "is_following": fd >= 0,
            "verified_at":  r["verified_at"],
        })
    return result


# ── 디버그: 현재 라이브 상태 체크 ────────────────────────────────────────────
@router.get("/debug/status")
async def debug_status(user: dict = Depends(get_current_user)):
    return await check_once_debug()


@router.get("/debug/raw/{chzzk_id}")
async def debug_raw(chzzk_id: str, user: dict = Depends(get_current_user)):
    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10
    ) as client:
        live_resp = await client.get(
            f"https://api.chzzk.naver.com/service/v1/channels/{chzzk_id}/live-detail"
        )
        info_resp = await client.get(
            f"https://api.chzzk.naver.com/service/v1/channels/{chzzk_id}"
        )
    return {
        "live_detail": {
            "status_code": live_resp.status_code,
            "body": live_resp.json() if live_resp.status_code == 200 else live_resp.text[:500],
        },
        "channel_info": {
            "status_code": info_resp.status_code,
            "body": info_resp.json() if info_resp.status_code == 200 else info_resp.text[:500],
        },
    }
