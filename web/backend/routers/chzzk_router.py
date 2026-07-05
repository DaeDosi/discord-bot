import os
import asyncio
import time as _time
import httpx
from datetime import date, datetime, timezone, timedelta

_KST = timezone(timedelta(hours=9))


def _today_kst() -> date:
    return datetime.now(_KST).date()
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
        """SELECT v.user_id, v.tier_months, v.follow_date, v.follow_days, v.verified_at
           FROM chzzk_verifications v
           LEFT JOIN chzzk_subscriptions s ON s.guild_id = v.guild_id
           WHERE v.guild_id=?
             AND (s.chzzk_channel_id IS NULL OR v.chzzk_channel_id != s.chzzk_channel_id)
           ORDER BY
               CASE WHEN v.follow_days >= 0 THEN v.follow_days ELSE -1 END DESC,
               v.verified_at DESC""",
        (int(guild_id),)
    )).fetchall()

    async with httpx.AsyncClient(timeout=5) as client:
        user_names = await asyncio.gather(*[
            _fetch_member_name(client, guild_id, str(r["user_id"]))
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
            "user_id":      str(r["user_id"]),
            "user_name":    name,
            "tier_months":  r["tier_months"] or 0,
            "follow_date":  r["follow_date"],
            "follow_days":  fd,
            "is_following": fd >= 0,
            "verified_at":  r["verified_at"],
        })
    return result


# ── 콘텐츠 알림 설정 ────────────────────────────────────────────────────────

class ContentNotifyUpdate(BaseModel):
    notify_vod:        bool = False
    notify_clip:       bool = False
    notify_community:  bool = False
    vod_channel:       Optional[str] = None
    clip_channel:      Optional[str] = None
    community_channel: Optional[str] = None


@router.get("/{guild_id}/content-notify")
async def get_content_notify(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    row = await (await db.execute(
        "SELECT notify_vod, notify_clip, notify_community, "
        "vod_channel, clip_channel, community_channel "
        "FROM chzzk_subscriptions WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone()
    if not row:
        return {
            "notify_vod": False, "notify_clip": False, "notify_community": False,
            "vod_channel": None, "clip_channel": None, "community_channel": None,
        }
    return {
        "notify_vod":        bool(row["notify_vod"]),
        "notify_clip":       bool(row["notify_clip"]),
        "notify_community":  bool(row["notify_community"]),
        "vod_channel":       str(row["vod_channel"])       if row["vod_channel"]       else None,
        "clip_channel":      str(row["clip_channel"])      if row["clip_channel"]      else None,
        "community_channel": str(row["community_channel"]) if row["community_channel"] else None,
    }


@router.put("/{guild_id}/content-notify")
async def update_content_notify(
    guild_id: str,
    body: ContentNotifyUpdate,
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
           SET notify_vod=?, notify_clip=?, notify_community=?,
               vod_channel=?, clip_channel=?, community_channel=?
           WHERE guild_id=?""",
        (
            int(body.notify_vod), int(body.notify_clip), int(body.notify_community),
            int(body.vod_channel)       if body.vod_channel       else None,
            int(body.clip_channel)      if body.clip_channel      else None,
            int(body.community_channel) if body.community_channel else None,
            int(guild_id),
        )
    )
    await db.commit()
    return {"ok": True}


# ── 실시간 채팅 명령어 ───────────────────────────────────────────────────────
# command_type: "checkin"(guild당 1개, 포인트+애정도XP 지급, 1일1회) |
#               "reply"(guild당 최대 5개, 자동 응답 텍스트만 전송)

class ChatCommandCreate(BaseModel):
    command_type:  str = "checkin"
    trigger_text:  str
    reward_points: int = 0
    reward_xp:     int = 0
    reply_text:    str = ""
    is_active:     bool = True


class ChatCommandUpdate(BaseModel):
    trigger_text:  str
    reward_points: int = 0
    reward_xp:     int = 0
    reply_text:    str = ""
    is_active:     bool = True


def _normalize_trigger(raw: str) -> str:
    return raw.strip().lstrip("!").strip()


@router.get("/{guild_id}/chat-commands")
async def list_chat_commands(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    rows = await (await db.execute(
        """SELECT id, command_type, trigger_text, reward_points, reward_xp,
                  reply_text, is_active
           FROM chzzk_chat_commands WHERE guild_id=?
           ORDER BY CASE command_type WHEN 'checkin' THEN 0 ELSE 1 END, id""",
        (int(guild_id),)
    )).fetchall()
    return [dict(r) for r in rows]


@router.post("/{guild_id}/chat-commands")
async def create_chat_command(
    guild_id: str,
    body: ChatCommandCreate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    trigger = _normalize_trigger(body.trigger_text)
    if not trigger:
        raise HTTPException(status_code=400, detail="명령어를 입력해주세요.")
    if body.command_type not in ("checkin", "reply"):
        raise HTTPException(status_code=400, detail="잘못된 명령어 종류입니다.")

    db = await get_db()
    count = (await (await db.execute(
        "SELECT COUNT(*) FROM chzzk_chat_commands WHERE guild_id=? AND command_type=?",
        (int(guild_id), body.command_type)
    )).fetchone())[0]
    if body.command_type == "checkin" and count >= 1:
        raise HTTPException(status_code=400, detail="출석체크 명령어는 1개만 등록할 수 있습니다.")
    if body.command_type == "reply" and count >= 5:
        raise HTTPException(status_code=400, detail="자동 응답 명령어는 최대 5개까지 등록할 수 있습니다.")

    try:
        cur = await db.execute(
            """INSERT INTO chzzk_chat_commands
               (guild_id, command_type, trigger_text, reward_points, reward_xp,
                reply_text, is_active, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                int(guild_id), body.command_type, trigger,
                max(0, body.reward_points), max(0, body.reward_xp),
                body.reply_text[:100], int(body.is_active), int(_time.time()),
            )
        )
        await db.commit()
    except Exception:
        raise HTTPException(status_code=409, detail="이미 등록된 명령어입니다.")
    return {"ok": True, "id": cur.lastrowid}


@router.put("/{guild_id}/chat-commands/{command_id}")
async def update_chat_command(
    guild_id: str,
    command_id: int,
    body: ChatCommandUpdate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    trigger = _normalize_trigger(body.trigger_text)
    if not trigger:
        raise HTTPException(status_code=400, detail="명령어를 입력해주세요.")
    db = await get_db()
    row = await (await db.execute(
        "SELECT id FROM chzzk_chat_commands WHERE id=? AND guild_id=?",
        (command_id, int(guild_id))
    )).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="명령어를 찾을 수 없습니다.")
    try:
        await db.execute(
            """UPDATE chzzk_chat_commands
               SET trigger_text=?, reward_points=?, reward_xp=?, reply_text=?, is_active=?
               WHERE id=?""",
            (
                trigger, max(0, body.reward_points), max(0, body.reward_xp),
                body.reply_text[:100], int(body.is_active), command_id,
            )
        )
        await db.commit()
    except Exception:
        raise HTTPException(status_code=409, detail="이미 등록된 명령어입니다.")
    return {"ok": True}


@router.delete("/{guild_id}/chat-commands/{command_id}")
async def delete_chat_command(
    guild_id: str,
    command_id: int,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    result = await db.execute(
        "DELETE FROM chzzk_chat_commands WHERE id=? AND guild_id=?",
        (command_id, int(guild_id))
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="명령어를 찾을 수 없습니다.")
    return {"ok": True}


# 봇의 동기화 주기(30분)보다 넉넉하게 잡아, 정상 동작 중인데도 "끊김"으로 잘못 표시되지 않게 함
_CHAT_SYNC_STALE_AFTER = 45 * 60


@router.get("/{guild_id}/chat-status")
async def get_chat_status(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    db = await get_db()
    sub = await (await db.execute(
        "SELECT chzzk_channel_id, chat_last_sync_at, chat_last_event_at FROM chzzk_subscriptions WHERE guild_id=?",
        (int(guild_id),)
    )).fetchone()
    if not sub:
        return {
            "registered": False, "connected": False,
            "last_sync_at": None, "last_event_at": None,
            "today_checkins": 0, "recent_checkins": [],
        }

    now = _time.time()
    last_sync = sub["chat_last_sync_at"] or 0
    connected = last_sync > 0 and (now - last_sync) < _CHAT_SYNC_STALE_AFTER

    checkin_cmd = await (await db.execute(
        "SELECT id FROM chzzk_chat_commands WHERE guild_id=? AND command_type='checkin'",
        (int(guild_id),)
    )).fetchone()

    today_checkins = 0
    recent_checkins: list = []
    if checkin_cmd:
        today = _today_kst().isoformat()
        count_row = await (await db.execute(
            "SELECT COUNT(*) FROM chzzk_checkin_log WHERE guild_id=? AND command_id=? AND check_date=?",
            (int(guild_id), checkin_cmd["id"], today)
        )).fetchone()
        today_checkins = count_row[0] if count_row else 0

        rows = await (await db.execute(
            """SELECT l.chzzk_channel_id, l.checked_at, v.user_id
               FROM chzzk_checkin_log l
               LEFT JOIN chzzk_verifications v
                 ON v.guild_id = l.guild_id AND v.chzzk_channel_id = l.chzzk_channel_id
               WHERE l.guild_id=? AND l.command_id=? AND l.check_date=?
               ORDER BY l.checked_at DESC LIMIT 10""",
            (int(guild_id), checkin_cmd["id"], today)
        )).fetchall()
        async with httpx.AsyncClient() as client:
            for r in rows:
                name = await _fetch_member_name(client, guild_id, str(r["user_id"])) if r["user_id"] else "알 수 없음"
                recent_checkins.append({"user_name": name, "checked_at": r["checked_at"]})

    return {
        "registered":      True,
        "connected":        connected,
        "last_sync_at":     int(last_sync) or None,
        "last_event_at":    int(sub["chat_last_event_at"] or 0) or None,
        "today_checkins":   today_checkins,
        "recent_checkins":  recent_checkins,
    }


@router.get("/{guild_id}/chat-log")
async def get_chat_log(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    """실제 치지직 채팅 수신/봇 응답을 실시간으로 확인하기 위한 디버그용 로그."""
    db = await get_db()
    rows = await (await db.execute(
        """SELECT direction, nickname, content, created_at
           FROM chzzk_chat_log WHERE guild_id=? ORDER BY id ASC""",
        (int(guild_id),)
    )).fetchall()
    return [dict(r) for r in rows]


# ── 마크 콜라보 이벤트 (참가 초대된 서버만 확인 가능) ─────────────────────────
@router.get("/{guild_id}/mc-event")
async def get_mc_event_status(
    guild_id: str,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    """nexadmin에서 이 서버를 초대한 활성 이벤트가 있으면 카탈로그/연동 상태를 반환.
    초대되지 않았으면 {"invited": false}만 반환 — 대시보드가 이 값으로 탭 자체를 숨긴다."""
    db = await get_db()
    row = await (await db.execute(
        """SELECT eg.mc_player_name, e.id AS event_id, e.name AS event_name, e.is_active
           FROM mc_event_guilds eg
           JOIN mc_events e ON e.id = eg.event_id
           WHERE eg.guild_id=? AND e.is_active=1
           LIMIT 1""",
        (int(guild_id),)
    )).fetchone()
    if not row:
        return {"invited": False}

    sub = await (await db.execute(
        "SELECT streamer_access_token FROM chzzk_subscriptions WHERE guild_id=?", (int(guild_id),)
    )).fetchone()

    cmd_rows = await (await db.execute(
        "SELECT kind, trigger_text FROM mc_event_commands WHERE event_id=? AND is_active=1",
        (row["event_id"],)
    )).fetchall()
    item_rows = await (await db.execute(
        """SELECT item_type, name, points_cost, in_random_pool
           FROM mc_event_items WHERE event_id=? AND is_active=1 ORDER BY item_type, id""",
        (row["event_id"],)
    )).fetchall()

    return {
        "invited":            True,
        "event_name":         row["event_name"],
        "is_active":          bool(row["is_active"]),
        "mc_player_name":     row["mc_player_name"],
        "streamer_connected": bool(sub and sub["streamer_access_token"]),
        "triggers":           [dict(c) for c in cmd_rows],
        "items":              [dict(i) for i in item_rows],
    }


class McEventPlayerUpdate(BaseModel):
    mc_player_name: str


@router.put("/{guild_id}/mc-event")
async def update_mc_event_player(
    guild_id: str,
    body: McEventPlayerUpdate,
    user: dict = Depends(get_current_user),
    _: None = Depends(require_guild_admin),
):
    """초대된 서버의 관리자가 자신의 마크 플레이어 이름만 직접 입력/수정."""
    db = await get_db()
    result = await db.execute(
        """UPDATE mc_event_guilds SET mc_player_name=?
           WHERE guild_id=? AND event_id=(SELECT id FROM mc_events WHERE is_active=1 LIMIT 1)""",
        (body.mc_player_name.strip(), int(guild_id))
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="참가 초대된 이벤트를 찾을 수 없습니다.")
    return {"ok": True}


# ── 디버그: 현재 라이브 상태 체크 ────────────────────────────────────────────
@router.get("/debug/status")
async def debug_status(user: dict = Depends(get_current_user)):
    return await check_once_debug()


@router.get("/debug/community-test/{chzzk_id}")
async def debug_community_test(chzzk_id: str, user: dict = Depends(get_current_user)):
    """현재 환경변수 쿠키로 NNG API 직접 테스트"""
    import re as _re, uuid as _uuid
    cookie = os.getenv("NAVER_COOKIE", "").strip()
    if not cookie:
        parts = []
        if os.getenv("NAVER_NID_AUT"): parts.append(f"NID_AUT={os.getenv('NAVER_NID_AUT')}")
        if os.getenv("NAVER_NID_SES"): parts.append(f"NID_SES={os.getenv('NAVER_NID_SES')}")
        cookie = "; ".join(parts)

    cookie_names = [p.split("=")[0].strip() for p in cookie.split(";") if "=" in p]
    m = _re.search(r'ba\.uuid=([^;]+)', cookie)
    device_id = m.group(1).strip() if m else str(_uuid.uuid4())

    url = (
        "https://apis.naver.com/nng_main/nng_comment_api/v1"
        f"/type/CHANNEL_POST/id/{chzzk_id}/comments"
    )
    headers = {
        "User-Agent":                 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept":                     "application/json, text/plain, */*",
        "Accept-Language":            "ko,en-US;q=0.9,en;q=0.8",
        "Origin":                     "https://chzzk.naver.com",
        "Referer":                    f"https://chzzk.naver.com/{chzzk_id}/community",
        "deviceid":                   device_id,
        "front-client-platform-type": "PC",
        "front-client-product-type":  "web",
        "if-modified-since":          "Mon, 26 Jul 1997 05:00:00 GMT",
        "Cookie":                     cookie,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params={"limit": 1, "offset": 0, "orderType": "DESC", "pagingType": "PAGE"}, headers=headers)

    return {
        "cookie_total_len":  len(cookie),
        "cookie_part_count": len(cookie_names),
        "cookie_names":      cookie_names,
        "has_ba_uuid":       "ba.uuid" in cookie,
        "device_id_used":    device_id[:12] + "...",
        "api_status":        resp.status_code,
        "api_response":      resp.json() if "json" in resp.headers.get("content-type","") else resp.text[:300],
    }


@router.get("/debug/community/{chzzk_id}")
async def debug_community(chzzk_id: str, user: dict = Depends(get_current_user)):
    """커뮤니티 API 엔드포인트 탐색 — 200을 리턴하는 경로 찾기"""
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    candidates = [
        # api.chzzk.naver.com — nng_main
        (f"https://api.chzzk.naver.com/nng_main/v1/channels/{chzzk_id}/community-board", {}),
        (f"https://api.chzzk.naver.com/nng_main/v1/channels/{chzzk_id}/community-boards", {}),
        (f"https://api.chzzk.naver.com/nng_main/v2/channels/{chzzk_id}/community-board", {}),
        (f"https://api.chzzk.naver.com/nng_main/v1/community-boards/{chzzk_id}/articles", {"size": 1}),
        (f"https://api.chzzk.naver.com/nng_main/v2/community-boards/{chzzk_id}/articles", {"size": 1}),
        # community-boards with query param
        (f"https://api.chzzk.naver.com/nng_main/v1/community-boards/articles", {"channelId": chzzk_id, "size": 1}),
        (f"https://api.chzzk.naver.com/nng_main/v2/community-boards/articles", {"channelId": chzzk_id, "size": 1}),
        # comm-api.game.naver.com (Naver 게임 커뮤니티)
        (f"https://comm-api.game.naver.com/nng_main/v1/channels/{chzzk_id}/community-boards", {}),
        (f"https://comm-api.game.naver.com/nng_main/v2/channels/{chzzk_id}/community-boards", {}),
        (f"https://comm-api.game.naver.com/nng_main/v1/community-boards/{chzzk_id}/articles", {"size": 1}),
        (f"https://comm-api.game.naver.com/nng_main/v2/community-boards/{chzzk_id}/articles", {"size": 1}),
        # game.naver.com
        (f"https://apis.naver.com/nng_main/v1/channels/{chzzk_id}/community-boards", {}),
        # chzzk BFF
        (f"https://chzzk.naver.com/nng_main/v1/channels/{chzzk_id}/community-boards", {}),
    ]

    results = []
    async with httpx.AsyncClient(headers=headers, timeout=10, follow_redirects=True) as client:
        for url, params in candidates:
            try:
                resp = await client.get(url, params=params)
                body = resp.json() if resp.status_code == 200 else resp.text[:300]
                results.append({"url": url, "status": resp.status_code, "body": body})
            except Exception as e:
                results.append({"url": url, "status": "error", "body": str(e)})
    return results


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
