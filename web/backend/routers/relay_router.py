"""
커뮤니티 릴레이 API
Oracle VM (한국 IP) 에서 NNG API를 폴링하고 이 엔드포인트로 웹훅을 보내면
Railway가 Discord 알림을 전송한다.
"""
import os
import hmac
import hashlib
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from database import get_db
from chzzk_monitor import _send_post_notification

router = APIRouter(prefix="/api/relay", tags=["relay"])

_SECRET = os.getenv("RELAY_SECRET", "")


def _verify_secret(secret: str) -> bool:
    if not _SECRET:
        return False
    return hmac.compare_digest(secret, _SECRET)


# ── 현재 커뮤니티 알림 활성화된 채널 목록 ─────────────────────────────────────
@router.get("/channels")
async def get_monitored_channels(x_relay_secret: str = Header(...)):
    if not _verify_secret(x_relay_secret):
        raise HTTPException(status_code=403, detail="인증 실패")
    db = await get_db()
    rows = await (await db.execute(
        """SELECT id, guild_id, chzzk_channel_id, chzzk_name, last_post_id,
                  discord_channel, community_channel
           FROM chzzk_subscriptions
           WHERE notify_community=1"""
    )).fetchall()
    return [
        {
            "sub_id":          row["id"],
            "guild_id":        str(row["guild_id"]),
            "chzzk_id":        row["chzzk_channel_id"],
            "chzzk_name":      row["chzzk_name"],
            "last_post_id":    row["last_post_id"],
        }
        for row in rows
    ]


# ── Oracle VM이 새 게시글 발견 시 호출 ────────────────────────────────────────
class PostPayload(BaseModel):
    chzzk_id:   str
    post:       dict
    x_relay_secret: Optional[str] = None


@router.post("/notify")
async def community_notify(body: PostPayload, x_relay_secret: str = Header(...)):
    if not _verify_secret(x_relay_secret):
        raise HTTPException(status_code=403, detail="인증 실패")

    db = await get_db()
    rows = await (await db.execute(
        """SELECT id, guild_id, discord_channel, chzzk_channel_id, chzzk_name,
                  community_channel, last_post_id, mention_everyone
           FROM chzzk_subscriptions
           WHERE chzzk_channel_id=? AND notify_community=1""",
        (body.chzzk_id,)
    )).fetchall()

    if not rows:
        return {"ok": True, "sent": 0}

    post     = body.post
    post_id  = str(
        post.get("commentId") or post.get("postNo")
        or post.get("communityPostNo") or post.get("id") or ""
    )
    if not post_id:
        raise HTTPException(status_code=400, detail="post_id 없음")

    sent = 0
    for row in rows:
        if str(row["last_post_id"] or "") == post_id:
            continue
        await _send_post_notification(row, post)
        await db.execute(
            "UPDATE chzzk_subscriptions SET last_post_id=? WHERE id=?",
            (post_id, row["id"]),
        )
        sent += 1

    await db.commit()
    return {"ok": True, "sent": sent}
