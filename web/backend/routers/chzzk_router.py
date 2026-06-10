import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from deps import get_current_user
from database import get_db
from chzzk_monitor import check_once_debug

router = APIRouter(prefix="/api/chzzk", tags=["chzzk"])

CHZZK_API = "https://api.chzzk.naver.com"
HEADERS   = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


# ── 검색 ─────────────────────────────────────────────────────────────────────
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
async def list_subscriptions(guild_id: str, user: dict = Depends(get_current_user)):
    db = await get_db()
    rows = await (await db.execute(
        "SELECT id, discord_channel, chzzk_channel_id, chzzk_name, "
        "chzzk_image_url, is_live, mention_role_id, custom_message "
        "FROM chzzk_subscriptions WHERE guild_id=?",
        (int(guild_id),)
    )).fetchall()
    return [dict(r) for r in rows]


# ── 구독 추가 ─────────────────────────────────────────────────────────────────
class SubCreate(BaseModel):
    discord_channel:  str
    chzzk_channel_id: str
    chzzk_name:       str
    chzzk_image_url:  Optional[str] = None
    mention_role_id:  Optional[str] = None
    custom_message:   Optional[str] = None


@router.post("/{guild_id}/subscriptions")
async def add_subscription(guild_id: str, body: SubCreate,
                            user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO chzzk_subscriptions
               (guild_id, discord_channel, chzzk_channel_id, chzzk_name,
                chzzk_image_url, mention_role_id, custom_message)
               VALUES (?,?,?,?,?,?,?)""",
            (
                int(guild_id), int(body.discord_channel),
                body.chzzk_channel_id, body.chzzk_name,
                body.chzzk_image_url,
                int(body.mention_role_id) if body.mention_role_id else None,
                body.custom_message,
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
async def update_subscription(guild_id: str, sub_id: int, body: SubUpdate,
                               user: dict = Depends(get_current_user)):
    db = await get_db()
    row = await (await db.execute(
        "SELECT id FROM chzzk_subscriptions WHERE id=? AND guild_id=?",
        (sub_id, int(guild_id))
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="구독을 찾을 수 없습니다.")

    updates = {}
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
async def delete_subscription(guild_id: str, sub_id: int,
                               user: dict = Depends(get_current_user)):
    db = await get_db()
    result = await db.execute(
        "DELETE FROM chzzk_subscriptions WHERE id=? AND guild_id=?",
        (sub_id, int(guild_id))
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="구독을 찾을 수 없습니다.")
    return {"ok": True}


# ── 디버그: 현재 라이브 상태 체크 ────────────────────────────────────────────
@router.get("/debug/status")
async def debug_status(user: dict = Depends(get_current_user)):
    """각 구독의 DB 상태와 치지직 API 실시간 상태를 비교해서 반환"""
    return await check_once_debug()


@router.get("/debug/raw/{chzzk_id}")
async def debug_raw(chzzk_id: str, user: dict = Depends(get_current_user)):
    """치지직 API 응답 원본을 그대로 반환"""
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
