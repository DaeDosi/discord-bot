"""
치지직 실시간 채팅 명령어 (출석체크 + 커스텀 자동응답)

치지직 공식 오픈 API의 세션(Session) 기능으로 스트리머의 실시간 채팅을 구독한다.
비공식 쿠키 스크래핑 방식이 아니라, 이미 스트리머 연동 시 저장해둔 OAuth
access_token(채팅 메시지 조회/쓰기 스코프 필요)을 그대로 재사용한다.

프로토콜 구현은 직접 만들지 않고 chzzkpy 라이브러리(Engine.IO v3 기반 세션
프로토콜을 이미 구현해 둔 공식 API 클라이언트)를 사용한다. chzzkpy 또는
CHZZK_CLIENT_ID/SECRET이 없으면 이 cog는 조용히 비활성화된다.
"""
import os
import time
import asyncio
import logging
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
from database import get_db

log = logging.getLogger("chzzk_chat")

_KST = timezone(timedelta(hours=9))

_CHZZK_CLIENT_ID     = os.getenv("CHZZK_CLIENT_ID", "")
_CHZZK_CLIENT_SECRET = os.getenv("CHZZK_CLIENT_SECRET", "")

SYNC_INTERVAL_SECONDS = 300  # 5분마다 구독 대상/토큰/명령어 설정 재동기화 + 연결 생존 확인
CHAT_LOG_KEEP = 50  # guild당 디버그 채팅 로그 보관 개수

try:
    from chzzkpy import Client as ChzzkSessionClient
    from chzzkpy.authorization import AccessToken
    from chzzkpy.flags import UserPermission
    from chzzkpy.message import Message
    _CHZZKPY_AVAILABLE = True
except Exception as e:  # pragma: no cover - 선택적 의존성
    log.warning(f"chzzkpy 라이브러리를 불러올 수 없어 실시간 채팅 기능이 비활성화됩니다: {e}")
    _CHZZKPY_AVAILABLE = False


def _today_kst() -> str:
    return datetime.now(_KST).date().isoformat()


class ChzzkChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = None
        self.session_id: str | None = None       # 치지직 sessionKey (subscribe API용)
        self._transport_sid: str | None = None   # Engine.IO transport id (생존 확인용, client._gateway 조회 키)
        self._session_ready = asyncio.Event()
        # chzzk_channel_id -> {"guild_id": int, "user_client": UserClient, "commands": {trigger_text: row}}
        self._channels: dict[str, dict] = {}
        self._enabled = bool(_CHZZKPY_AVAILABLE and _CHZZK_CLIENT_ID and _CHZZK_CLIENT_SECRET)

        if not self._enabled:
            log.warning(
                "치지직 실시간 채팅 cog 비활성화 "
                f"(chzzkpy={_CHZZKPY_AVAILABLE}, client_id={'있음' if _CHZZK_CLIENT_ID else '없음'})"
            )
            return

        self.sync_loop.start()

    def cog_unload(self):
        if self.sync_loop.is_running():
            self.sync_loop.cancel()

    # ── 주기적 동기화: 어떤 스트리머를 구독할지, 토큰 갱신, 명령어 설정 최신화 ──
    @tasks.loop(seconds=SYNC_INTERVAL_SECONDS)
    async def sync_loop(self):
        try:
            await self._sync_channels()
        except Exception:
            log.exception("치지직 채팅 동기화 중 오류")

    @sync_loop.before_loop
    async def before_sync_loop(self):
        await self.bot.wait_until_ready()

    def _gateway_alive(self) -> bool:
        """실제 소켓 연결이 살아있는지 확인. chzzkpy가 연결 끊김을 알려주는 콜백을
        제공하지 않아서(하트비트 실패가 조용히 백그라운드 태스크만 죽임), 내부
        _gateway 딕셔너리를 직접 조회해서 확인한다 — 라이브러리 내부 구현에 의존하는
        부분이라 향후 chzzkpy 버전이 바뀌면 깨질 수 있음."""
        if self.client is None or self._transport_sid is None:
            return False
        gateway = self.client._gateway.get(self._transport_sid)
        return bool(gateway is not None and gateway.is_connected)

    async def _ensure_client(self) -> bool:
        if self.client is not None:
            if self._gateway_alive():
                return self.session_id is not None
            log.warning("치지직 채팅 게이트웨이 연결이 끊긴 것으로 확인되어 재연결합니다.")
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
            self.session_id = None
            self._transport_sid = None
            self._channels.clear()  # 재구독이 필요하므로 로컬 구독 상태 초기화

        self.client = ChzzkSessionClient(_CHZZK_CLIENT_ID, _CHZZK_CLIENT_SECRET)
        self._session_ready = asyncio.Event()

        @self.client.event
        async def on_chat(message):
            await self._on_chat(message)

        # 주의: Client.connect()의 반환값은 소켓(Engine.IO) transport ID이지
        # 구독 API가 요구하는 치지직 세션 키(sessionKey)가 아니다. 실제 세션 키는
        # "connected" 시스템 이벤트(on_connect)로 별도 전달되므로 그걸 사용해야 한다.
        @self.client.event
        async def on_connect(session_id):
            self.session_id = session_id
            self._session_ready.set()
            log.info(f"치지직 채팅 세션 연결됨 (session={session_id})")

        try:
            # addition_connect=True 로 호출해야 연결 후 즉시 반환됨(그렇지 않으면 연결이 끊길 때까지 블로킹됨)
            self._transport_sid = await self.client.connect(addition_connect=True)
            await asyncio.wait_for(self._session_ready.wait(), timeout=10)
        except Exception:
            log.exception("치지직 채팅 세션 연결 실패")
            self.client = None
            self.session_id = None
            self._transport_sid = None
            return False
        return True

    async def _ensure_fresh_token(self, row) -> tuple[str, str, int] | tuple[None, None, None]:
        now = int(time.time())
        if (row["streamer_token_expires_at"] or 0) > now + 300:
            return row["streamer_access_token"], row["streamer_refresh_token"], row["streamer_token_expires_at"]

        try:
            refreshed = await self.client.refresh_access_token(row["streamer_refresh_token"])
        except Exception as e:
            log.warning(f"치지직 토큰 갱신 실패 guild={row['guild_id']}: {e}")
            return None, None, None

        new_exp = now + refreshed.expires_in
        db = await get_db()
        await db.execute(
            """UPDATE chzzk_subscriptions
               SET streamer_access_token=?, streamer_refresh_token=?, streamer_token_expires_at=?
               WHERE guild_id=?""",
            (refreshed.access_token, refreshed.refresh_token, new_exp, row["guild_id"])
        )
        await db.commit()
        return refreshed.access_token, refreshed.refresh_token, new_exp

    async def _sync_channels(self):
        if not self._enabled:
            return
        if not await self._ensure_client():
            return

        db = await get_db()
        rows = await (await db.execute(
            """SELECT guild_id, chzzk_channel_id, streamer_access_token,
                      streamer_refresh_token, streamer_token_expires_at
               FROM chzzk_subscriptions
               WHERE streamer_access_token IS NOT NULL AND streamer_refresh_token IS NOT NULL"""
        )).fetchall()

        active_chzzk_ids = set()
        for row in rows:
            chzzk_id = row["chzzk_channel_id"]
            active_chzzk_ids.add(chzzk_id)

            cmd_rows = await (await db.execute(
                """SELECT id, command_type, trigger_text, reward_points, reward_xp, reply_text
                   FROM chzzk_chat_commands WHERE guild_id=? AND is_active=1""",
                (row["guild_id"],)
            )).fetchall()
            commands_map = {c["trigger_text"]: dict(c) for c in cmd_rows}

            if not commands_map:
                if chzzk_id in self._channels:
                    self._channels[chzzk_id]["commands"] = {}
                continue

            if chzzk_id in self._channels:
                # 이미 구독 중 — 명령어 설정만 최신화 (토큰은 만료 임박한 경우에만 갱신)
                self._channels[chzzk_id]["commands"] = commands_map
                self._channels[chzzk_id]["guild_id"] = row["guild_id"]
                if (row["streamer_token_expires_at"] or 0) <= int(time.time()) + 300:
                    at, rt, _exp = await self._ensure_fresh_token(row)
                    if at:
                        self._channels[chzzk_id]["user_client"].access_token = AccessToken(
                            access_token=at, refresh_token=rt, expires_in=3600, token_type="Bearer"
                        )
                await self._mark_synced(row["guild_id"])
                continue

            at, rt, exp = await self._ensure_fresh_token(row)
            if not at:
                continue

            access_token_obj = AccessToken(
                access_token=at, refresh_token=rt,
                expires_in=max(60, exp - int(time.time())), token_type="Bearer",
            )
            try:
                user_client = await self.client.get_user_client(access_token_obj)
                await user_client.subscribe(UserPermission(chat=True), session_id=self.session_id)
            except Exception:
                log.exception(f"치지직 채팅 구독 실패 guild={row['guild_id']}")
                continue

            self._channels[chzzk_id] = {
                "guild_id":    row["guild_id"],
                "user_client": user_client,
                "commands":    commands_map,
            }
            await self._mark_synced(row["guild_id"])
            log.info(f"치지직 채팅 구독 시작: guild={row['guild_id']} channel={chzzk_id}")

        # 더 이상 등록되어 있지 않은(연동 해제된) 채널은 로컬 목록에서만 정리 (명령어 매칭 대상에서 제외)
        for stale_id in list(self._channels.keys()):
            if stale_id not in active_chzzk_ids:
                self._channels.pop(stale_id, None)

    async def _mark_synced(self, guild_id: int):
        db = await get_db()
        await db.execute(
            "UPDATE chzzk_subscriptions SET chat_last_sync_at=? WHERE guild_id=?",
            (int(time.time()), guild_id)
        )
        await db.commit()

    async def _mark_event_received(self, guild_id: int):
        db = await get_db()
        await db.execute(
            "UPDATE chzzk_subscriptions SET chat_last_event_at=? WHERE guild_id=?",
            (int(time.time()), guild_id)
        )
        await db.commit()

    async def _log_chat(self, guild_id: int, direction: str, nickname: str, content: str):
        """대시보드 디버그용 채팅 로그. guild당 최근 CHAT_LOG_KEEP개만 유지."""
        db = await get_db()
        await db.execute(
            "INSERT INTO chzzk_chat_log(guild_id, direction, nickname, content, created_at) VALUES(?,?,?,?,?)",
            (guild_id, direction, nickname[:100], content[:200], int(time.time()))
        )
        await db.execute(
            """DELETE FROM chzzk_chat_log WHERE guild_id=? AND id NOT IN (
                   SELECT id FROM chzzk_chat_log WHERE guild_id=? ORDER BY id DESC LIMIT ?
               )""",
            (guild_id, guild_id, CHAT_LOG_KEEP)
        )
        await db.commit()

    # ── 채팅 이벤트 처리 ──────────────────────────────────────────────────────
    async def _on_chat(self, message: "Message"):
        entry = self._channels.get(message.channel)
        if not entry:
            return

        # 명령어 매칭 여부와 무관하게, 채팅이 실제로 들어오고 있다는 사실 자체를 기록
        # (대시보드 "실시간 채팅 명령어" 탭의 연결 상태 표시용)
        await self._mark_event_received(entry["guild_id"])
        nickname = message.profile.nickname if message.profile else "익명"
        await self._log_chat(entry["guild_id"], "in", nickname, message.content or "")

        if not entry["commands"]:
            return

        content = (message.content or "").strip()
        if not content.startswith("!"):
            return
        trigger = content[1:].strip()
        if not trigger:
            return

        cmd = entry["commands"].get(trigger)
        if not cmd:
            return

        if cmd["command_type"] == "reply":
            try:
                await entry["user_client"].send_message(cmd["reply_text"])
                await self._log_chat(entry["guild_id"], "out", "NexBot", cmd["reply_text"])
            except Exception:
                log.exception(f"치지직 채팅 자동응답 전송 실패 guild={entry['guild_id']}")
            return

        if cmd["command_type"] == "checkin":
            await self._handle_checkin(entry, message, cmd)

    async def _handle_checkin(self, entry: dict, message: "Message", cmd: dict):
        guild_id = entry["guild_id"]
        chzzk_user_id = message.user_id

        db = await get_db()

        # 대시보드에서 치지직 계정을 연동(인증)한 유저만 지급 대상
        verif = await (await db.execute(
            "SELECT user_id FROM chzzk_verifications WHERE guild_id=? AND chzzk_channel_id=?",
            (guild_id, chzzk_user_id)
        )).fetchone()
        if not verif:
            return
        discord_user_id = verif["user_id"]

        # 1일 1회 제한 (guild + 치지직 유저 + 명령어 + 오늘 날짜 조합의 PK 충돌로 중복 차단)
        try:
            await db.execute(
                """INSERT INTO chzzk_checkin_log
                       (guild_id, chzzk_channel_id, command_id, check_date, checked_at)
                   VALUES (?,?,?,?,?)""",
                (guild_id, chzzk_user_id, cmd["id"], _today_kst(), int(time.time()))
            )
            await db.commit()
        except Exception:
            return  # 오늘 이미 출석함

        if cmd["reward_points"]:
            await db.execute(
                """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,?)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points+?""",
                (guild_id, discord_user_id, cmd["reward_points"], cmd["reward_points"])
            )
            await db.commit()

        if cmd["reward_xp"]:
            guild = self.bot.get_guild(guild_id)
            leveling_cog = self.bot.get_cog("LevelingCog")
            if guild and leveling_cog:
                await leveling_cog.add_xp(guild, discord_user_id, cmd["reward_xp"])

        # 오늘 몇 번째 출석인지 세어서 채팅으로 안내 (본인 포함)
        today_count_row = await (await db.execute(
            "SELECT COUNT(*) FROM chzzk_checkin_log WHERE guild_id=? AND command_id=? AND check_date=?",
            (guild_id, cmd["id"], _today_kst())
        )).fetchone()
        today_count = today_count_row[0] if today_count_row else 1
        nickname = message.profile.nickname if message.profile else "익명"
        announce_text = f"{nickname}님이 오늘 {today_count}번째 출석 하셨습니다!"
        try:
            await entry["user_client"].send_message(announce_text)
            await self._log_chat(guild_id, "out", "NexBot", announce_text)
        except Exception:
            log.exception(f"출석체크 안내 메시지 전송 실패 guild={guild_id}")

        log.info(
            f"치지직 출석체크 지급: guild={guild_id} chzzk_user={chzzk_user_id} "
            f"discord_user={discord_user_id} points={cmd['reward_points']} xp={cmd['reward_xp']} "
            f"today_count={today_count}"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChzzkChatCog(bot))
