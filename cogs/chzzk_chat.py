"""
치지직 실시간 채팅 명령어 (출석체크 + 커스텀 자동응답)

치지직 공식 오픈 API의 세션(Session) 기능으로 스트리머의 실시간 채팅을 구독한다.
비공식 쿠키 스크래핑 방식이 아니라, 이미 스트리머 연동 시 저장해둔 OAuth
access_token(채팅 메시지 조회/쓰기 스코프 필요)을 그대로 재사용한다.

프로토콜 구현은 직접 만들지 않고 chzzkpy 라이브러리(Engine.IO v3 기반 세션
프로토콜을 이미 구현해 둔 공식 API 클라이언트)를 사용한다. chzzkpy가 없으면
이 cog는 조용히 비활성화된다. CHZZK_CLIENT_ID/SECRET이 없으면 실제 채팅 세션
연결(sync_loop)만 비활성화되고, 대시보드 웹 테스트 메시지 큐(test_queue_loop)는
chzzkpy만 설치돼 있으면 별도로 계속 동작한다 — 로컬에서 치지직 앱을 등록하기
전에도 명령어 로직을 검증할 수 있도록 하기 위함.
"""
import os
import json
import time
import sqlite3
import random
import asyncio
import logging
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
from database import get_db
from utils.mc_rcon import rcon_command
from utils.checks import member_is_mod_or_admin
from utils.gambling import resolve_gambling_winner, calc_gambling_payout

log = logging.getLogger("chzzk_chat")

_KST = timezone(timedelta(hours=9))

_CHZZK_CLIENT_ID     = os.getenv("CHZZK_CLIENT_ID", "")
_CHZZK_CLIENT_SECRET = os.getenv("CHZZK_CLIENT_SECRET", "")

SYNC_INTERVAL_SECONDS = 60  # 구독 대상/토큰/명령어 설정 재동기화 + 연결 생존 확인 주기.
# 예전엔 5분이었는데, 스트리머 연동 직후나 명령어/이벤트 설정을 바꾼 직후 봇이 반영할 때까지
# 최대 5분씩 걸려서 "방금 한 설정이 안 먹히는" 것처럼 보이는 문제가 잦았다. 여기서 하는 일이
# DB 조회 위주(토큰 만료 임박 시에만 치지직 API 호출)라 1분 주기로도 부담이 크지 않다.
CHAT_LOG_KEEP = 50  # guild당 디버그 채팅 로그 보관 개수
TEST_QUEUE_INTERVAL_SECONDS = 2  # 대시보드 웹 테스트 메시지 큐 폴링 주기 (로컬 테스트 전용, 저부하)
# 오늘 이미 출석한 유저가 !출석체크를 연타해도 매번 채팅으로 응답하면 스팸/치지직 채팅
# 도배 제재 위험이 있어, 동일 (guild, 유저) 조합에 대해 이 시간(초) 안에는 재안내하지 않는다.
CHECKIN_DUPLICATE_NOTICE_COOLDOWN_SECONDS = 60

try:
    from chzzkpy import Client as ChzzkSessionClient
    from chzzkpy.authorization import AccessToken
    from chzzkpy.flags import UserPermission
    from chzzkpy.message import Message
    from chzzkpy.error import HTTPException as ChzzkHTTPException
    _CHZZKPY_AVAILABLE = True
except Exception as e:  # pragma: no cover - 선택적 의존성
    log.warning(f"chzzkpy 라이브러리를 불러올 수 없어 실시간 채팅 기능이 비활성화됩니다: {e}")
    _CHZZKPY_AVAILABLE = False


def _today_kst() -> str:
    return datetime.now(_KST).date().isoformat()


class _NullChatClient:
    """대시보드 웹 테스트 메시지용 가짜 user_client. 실제 치지직 API를 호출하지 않고
    조용히 성공 처리해, _send_chat()이 남기는 "out" 로그만으로 미리보기를 갱신한다."""
    async def send_message(self, content: str):
        return None


class ChzzkChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = None
        self.session_id: str | None = None       # 치지직 sessionKey (subscribe API용)
        self._transport_sid: str | None = None   # Engine.IO transport id (생존 확인용, client._gateway 조회 키)
        self._session_ready = asyncio.Event()
        # chzzk_channel_id -> {"guild_id": int, "user_client": UserClient, "commands": {trigger_text: row}}
        self._channels: dict[str, dict] = {}
        # (guild_id, chzzk_user_id) -> 마지막으로 "이미 출석했습니다" 안내를 보낸 시각(monotonic)
        self._checkin_duplicate_notice_at: dict[tuple[int, str], float] = {}
        self._enabled = bool(_CHZZKPY_AVAILABLE and _CHZZK_CLIENT_ID and _CHZZK_CLIENT_SECRET)
        # 웹 테스트 메시지는 실제 치지직 세션 연결이 필요 없다 — chzzkpy의 Message 모델만
        # 만들 수 있으면 되므로, CHZZK_CLIENT_ID/SECRET 없이(로컬에서 치지직 앱 등록 전에도)
        # 명령어 로직만 검증할 수 있도록 _enabled와 별도로 게이팅한다.
        self._test_queue_available = _CHZZKPY_AVAILABLE

        if not self._enabled:
            log.warning(
                "치지직 실시간 채팅 cog 비활성화 "
                f"(chzzkpy={_CHZZKPY_AVAILABLE}, client_id={'있음' if _CHZZK_CLIENT_ID else '없음'})"
            )
        else:
            self.sync_loop.start()

        if self._test_queue_available:
            self.test_queue_loop.start()

    def cog_unload(self):
        if self.sync_loop.is_running():
            self.sync_loop.cancel()
        if self.test_queue_loop.is_running():
            self.test_queue_loop.cancel()

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

    # ── 대시보드 웹 테스트 메시지 큐 폴링 (실제 치지직 방송 없이 명령어 테스트용) ──
    @tasks.loop(seconds=TEST_QUEUE_INTERVAL_SECONDS)
    async def test_queue_loop(self):
        try:
            await self._process_test_queue()
        except Exception:
            log.exception("치지직 채팅 테스트 큐 처리 중 오류")

    @test_queue_loop.before_loop
    async def before_test_queue_loop(self):
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
               WHERE streamer_access_token IS NOT NULL AND streamer_refresh_token IS NOT NULL
                 AND chat_enabled=1"""
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
            mc_event = await self._load_mc_event(row["guild_id"])

            if chzzk_id in self._channels:
                # 이미 구독 중 — 명령어/이벤트 설정만 최신화 (토큰은 만료 임박한 경우에만 갱신)
                self._channels[chzzk_id]["commands"] = commands_map
                self._channels[chzzk_id]["mc_event"] = mc_event
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
                try:
                    await user_client.subscribe(UserPermission(chat=True), session_id=self.session_id)
                except ChzzkHTTPException as e:
                    # 치지직 서버 쪽에는 이전 세션의 구독이 이미 살아있는 경우 발생.
                    # 실패로 취급해 매번 재시도하면 이 채널은 영원히 로컬 목록에 못 들어가고
                    # 60초마다 같은 에러만 반복 로깅하게 되므로, 이미 구독된 상태를 정상으로 간주한다.
                    if "이미 구독 중인 이벤트" not in str(e):
                        raise
                    log.info(f"치지직 채팅 이미 구독 중(정상 처리): guild={row['guild_id']}")
            except Exception:
                log.exception(f"치지직 채팅 구독 실패 guild={row['guild_id']}")
                continue

            self._channels[chzzk_id] = {
                "guild_id":    row["guild_id"],
                "user_client": user_client,
                "commands":    commands_map,
                "mc_event":    mc_event,
            }
            await self._mark_synced(row["guild_id"])
            log.info(f"치지직 채팅 구독 시작: guild={row['guild_id']} channel={chzzk_id}")

        # 더 이상 등록되어 있지 않은(연동 해제된) 채널은 로컬 목록에서만 정리 (명령어 매칭 대상에서 제외)
        for stale_id in list(self._channels.keys()):
            if stale_id not in active_chzzk_ids:
                self._channels.pop(stale_id, None)

    async def _load_mc_event(self, guild_id: int) -> dict | None:
        """이 guild가 참가 등록된 활성 MC 이벤트가 있으면 서버 접속 정보 + 인게임 이름 +
        (트리거 문구 → kind) 명령어 매핑을 합쳐서 반환."""
        db = await get_db()
        row = await (await db.execute(
            """SELECT eg.event_id, eg.mc_player_name,
                      e.mc_host, e.mc_port, e.mc_rcon_password
               FROM mc_event_guilds eg
               JOIN mc_events e ON e.id = eg.event_id
               WHERE eg.guild_id=? AND e.is_active=1
               LIMIT 1""",
            (guild_id,)
        )).fetchone()
        if not row:
            return None
        event = dict(row)

        cmd_rows = await (await db.execute(
            "SELECT kind, trigger_text FROM mc_event_commands WHERE event_id=? AND is_active=1",
            (event["event_id"],)
        )).fetchall()
        event["triggers"] = {c["trigger_text"]: c["kind"] for c in cmd_rows}
        return event

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

    # ── 대시보드 웹 테스트 메시지 ─────────────────────────────────────────────────
    async def _process_test_queue(self):
        db = await get_db()
        rows = await (await db.execute(
            """SELECT id, guild_id, nickname, chzzk_user_id, content
               FROM chzzk_chat_test_queue WHERE processed=0 ORDER BY id ASC LIMIT 20"""
        )).fetchall()
        for row in rows:
            await db.execute("UPDATE chzzk_chat_test_queue SET processed=1 WHERE id=?", (row["id"],))
            await db.commit()
            try:
                await self._handle_test_message(
                    row["guild_id"], row["nickname"], row["chzzk_user_id"], row["content"]
                )
            except Exception:
                log.exception(f"치지직 채팅 테스트 메시지 처리 실패 guild={row['guild_id']}")

    async def _handle_test_message(self, guild_id: int, nickname: str, chzzk_user_id: str, content: str):
        db = await get_db()
        sub = await (await db.execute(
            "SELECT chzzk_channel_id, chat_enabled FROM chzzk_subscriptions WHERE guild_id=?",
            (guild_id,)
        )).fetchone()
        if not sub:
            return
        # chat_enabled는 실시간 채팅 기능 전체의 마스터 스위치 — 꺼져 있으면 테스트 메시지도
        # 실제 채팅과 동일하게 아무 명령어도 처리하지 않는다. 이 체크를 self._channels 조회보다
        # 먼저 해야, 토글을 막 끈 직후(다음 sync_loop 전까지 로컬 목록이 아직 안 지워진 순간)
        # 실제 라이브 치지직 채팅으로 테스트 메시지가 새어나가는 것도 함께 막을 수 있다.
        if not sub["chat_enabled"]:
            log.info(f"치지직 채팅 테스트 메시지 무시(실시간 채팅 연동 OFF): guild={guild_id}")
            return
        chzzk_channel_id = sub["chzzk_channel_id"]

        # 이미 실제로 구독 중인 채널이면 sync_loop가 이미 최신 상태로 유지해주는 entry(명령어
        # 맵/mc_event/진짜 user_client)를 그대로 재사용한다 — 이러면 실제 채팅에도 응답이
        # 나가고, _sync_channels와 동일한 조회를 여기서 중복으로 다시 할 필요도 없다. 아직
        # 구독 전(치지직 방송 없이 로직만 검증하는 경우)에만 DB에서 직접 불러오고 가짜
        # 클라이언트를 쓴다.
        real_entry = self._channels.get(chzzk_channel_id)
        if real_entry:
            entry = real_entry
        else:
            cmd_rows = await (await db.execute(
                """SELECT id, command_type, trigger_text, reward_points, reward_xp, reply_text
                   FROM chzzk_chat_commands WHERE guild_id=? AND is_active=1""",
                (guild_id,)
            )).fetchall()
            entry = {
                "guild_id":    guild_id,
                "user_client": _NullChatClient(),
                "commands":    {c["trigger_text"]: dict(c) for c in cmd_rows},
                "mc_event":    await self._load_mc_event(guild_id),
            }
        message = Message(
            senderChannelId=chzzk_user_id,
            profile={"nickname": nickname, "badges": [], "verified_mark": False},
            content=content,
            channelId=chzzk_channel_id,
            chatChannelId="test",
            messageTime=datetime.now(timezone.utc),
        )
        await self._process_message(entry, message)
        log.info(f"치지직 채팅 테스트 메시지 처리: guild={guild_id} content={content!r}")

    # ── 채팅 이벤트 처리 ──────────────────────────────────────────────────────
    async def _on_chat(self, message: "Message"):
        entry = self._channels.get(message.channel)
        if not entry:
            return
        await self._process_message(entry, message)

    async def _process_message(self, entry: dict, message: "Message"):
        """실제 치지직 채팅과 대시보드 웹 테스트 메시지(_process_test_queue)가
        공유하는 처리 로직 — 명령어 매칭/디스패치가 두 경로에서 항상 동일하게 동작한다."""
        # 명령어 매칭 여부와 무관하게, 채팅이 실제로 들어오고 있다는 사실 자체를 기록
        # (대시보드 "실시간 채팅 명령어" 탭의 연결 상태 표시용)
        await self._mark_event_received(entry["guild_id"])
        nickname = message.profile.nickname if message.profile else "익명"
        await self._log_chat(entry["guild_id"], "in", nickname, message.content or "")

        content = (message.content or "").strip()
        if not content.startswith("!"):
            return
        trigger = content[1:].strip()
        if not trigger:
            return
        # "!투표 1"처럼 인자가 붙는 명령어를 지원하기 위해 첫 단어만 명령어로 보고 나머지는 인자로 분리.
        # 기존에는 "!" 뒤 전체 문자열을 그대로 트리거와 비교해서 "!투표 1"이 "투표"와 매치되지 않았다.
        cmd_name, _, arg = trigger.partition(" ")
        arg = arg.strip()

        mc_event = entry.get("mc_event")
        if mc_event and cmd_name in mc_event["triggers"]:
            await self._handle_mc_event(entry, message, cmd_name, mc_event["triggers"][cmd_name])
            return

        cmd = entry["commands"].get(cmd_name)
        if not cmd:
            if cmd_name == "포인트":
                await self._handle_points(entry, message)
                return
            if cmd_name == "도박":
                await self._handle_gambling_start(entry, message)
                return
            if cmd_name == "도박종료":
                await self._handle_gambling_end(entry, message)
                return
            if cmd_name == "투표":
                await self._handle_gambling_vote(entry, message, arg)
                return
            mc_event_desc = (
                f"있음(등록된 트리거={list(mc_event['triggers'].keys())})" if mc_event
                else "없음(참가 서버 미등록 또는 이벤트 비활성 상태)"
            )
            log.info(
                f"치지직 채팅 명령어 매칭 실패: guild={entry['guild_id']} "
                f"입력=\"!{trigger}\" 등록된 명령어={list(entry['commands'].keys())} mc_event={mc_event_desc}"
            )
            return

        if cmd["command_type"] == "reply":
            await self._send_chat(entry, cmd["reply_text"])
            log.info(f"치지직 채팅 자동응답 전송: guild={entry['guild_id']} trigger=!{cmd_name}")
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
            log.info(
                f"치지직 출석체크 무시(미인증 유저): guild={guild_id} chzzk_user={chzzk_user_id} "
                f"— 대시보드 입장 인증에서 치지직 계정을 연동한 유저가 아님"
            )
            return
        discord_user_id = verif["user_id"]
        nickname = message.profile.nickname if message.profile else "익명"

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
            log.info(f"치지직 출석체크 무시(오늘 이미 출석함): guild={guild_id} chzzk_user={chzzk_user_id}")
            # 이미 출석한 유저가 !출석체크를 연타하면 매번 응답하지 않고 쿨다운 안에서는 조용히 무시 —
            # 채팅 도배/치지직 제재 위험 방지.
            cooldown_key = (guild_id, chzzk_user_id)
            now = time.monotonic()
            last_notice = self._checkin_duplicate_notice_at.get(cooldown_key, 0.0)
            if now - last_notice >= CHECKIN_DUPLICATE_NOTICE_COOLDOWN_SECONDS:
                self._checkin_duplicate_notice_at[cooldown_key] = now
                already_text = f"{nickname}님, 이미 출석을 하였습니다."
                await self._send_chat(entry, already_text)
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
        announce_text = f"{nickname}님이 오늘 {today_count}번째 출석 하셨습니다!"
        await self._send_chat(entry, announce_text)

        log.info(
            f"치지직 출석체크 지급: guild={guild_id} chzzk_user={chzzk_user_id} "
            f"discord_user={discord_user_id} points={cmd['reward_points']} xp={cmd['reward_xp']} "
            f"today_count={today_count}"
        )

    async def _handle_points(self, entry: dict, message: "Message"):
        """!포인트 — 대시보드 설정 없이 항상 동작하는 내장 명령어. 디코 연동이
        안 된 유저나 포인트 기록이 아직 없는 유저도 채팅으로 안내만 하고 조용히 처리한다."""
        guild_id = entry["guild_id"]
        chzzk_user_id = message.user_id
        nickname = message.profile.nickname if message.profile else "익명"

        db = await get_db()
        verif = await (await db.execute(
            "SELECT user_id FROM chzzk_verifications WHERE guild_id=? AND chzzk_channel_id=?",
            (guild_id, chzzk_user_id)
        )).fetchone()

        if not verif:
            reply = f"{nickname}님, 디스코드 계정 연동 후 이용할 수 있습니다. (대시보드 입장 인증에서 치지직 계정을 연동해주세요)"
        else:
            pts_row = await (await db.execute(
                "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
                (guild_id, verif["user_id"])
            )).fetchone()
            points = pts_row["points"] if pts_row else 0
            reply = f"{nickname}님의 보유 포인트는 {points:,}P 입니다."

        await self._send_chat(entry, reply)

    # ── 치지직 채팅 도박: !도박 / !투표 <번호> / !도박종료 ─────────────────────────
    # 웹 대시보드 "포인트 > 포인트 도박" 탭에 이미 있는 설정(제목/옵션/베팅액)을 그대로
    # 불러와 채팅에서 시작한다. Discord Poll 기반 /포인트도박과 달리 투표를 직접 받으므로
    # 베팅은 투표 즉시 차감(잔액 부족 시 거절)하고, 세션당 1인 1표만 허용해 번복을 막는다.

    async def _send_chat(self, entry: dict, text: str):
        # 실제 전송 성공 여부와 무관하게 "봇이 뭐라고 응답했는지"를 항상 로그에 남긴다.
        # 예전엔 전송 성공 시에만 로그를 남겨서, 대시보드 웹 테스트 메시지(실제 치지직에
        # 보낼 수 없는 가짜 user_client)의 응답이 미리보기에 전혀 안 보이는 문제가 있었다.
        await self._log_chat(entry["guild_id"], "out", "NexBot", text)
        try:
            await entry["user_client"].send_message(text)
        except Exception:
            log.exception(f"치지직 채팅 메시지 전송 실패 guild={entry['guild_id']}")

    async def _is_gambling_manager(self, entry: dict, message: "Message") -> bool:
        """스트리머 본인이거나, 서버 관리 > 관리 탭에서 이미 등록해둔 매니저(관리자 권한,
        지정된 매니저 역할, 또는 개별 등록된 mod_managers)여야 !도박/!도박종료를 사용할 수 있다.
        치지직 전용 권한 체계를 새로 만드는 대신 기존 매니저 시스템을 그대로 재사용한다."""
        if message.user_id == message.channel:
            return True

        guild_id = entry["guild_id"]
        db = await get_db()
        verif = await (await db.execute(
            "SELECT user_id FROM chzzk_verifications WHERE guild_id=? AND chzzk_channel_id=?",
            (guild_id, message.user_id)
        )).fetchone()
        if not verif:
            return False

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return False
        member = guild.get_member(verif["user_id"])
        if member is None:
            try:
                member = await guild.fetch_member(verif["user_id"])
            except Exception:
                return False
        return await member_is_mod_or_admin(guild_id, member)

    async def _handle_gambling_start(self, entry: dict, message: "Message"):
        guild_id = entry["guild_id"]
        if not await self._is_gambling_manager(entry, message):
            log.info(f"치지직 도박 시작 거부(권한 없음): guild={guild_id} chzzk_user={message.user_id}")
            return

        db = await get_db()
        existing = await (await db.execute(
            "SELECT id FROM chzzk_gambling_sessions WHERE guild_id=? AND settled=0",
            (guild_id,)
        )).fetchone()
        if existing:
            await self._send_chat(entry, "이미 진행 중인 도박이 있습니다. !도박종료로 먼저 종료해주세요.")
            return

        cfg = await (await db.execute(
            "SELECT title, bet_amount FROM points_gambling_config WHERE guild_id=?",
            (guild_id,)
        )).fetchone()
        opt_rows = await (await db.execute(
            "SELECT content FROM points_gambling_options WHERE guild_id=? ORDER BY opt_index",
            (guild_id,)
        )).fetchall()
        options = [r["content"] for r in opt_rows]
        if len(options) < 2:
            await self._send_chat(
                entry, "도박 옵션이 설정되지 않았습니다. 웹 대시보드 > 포인트 > 포인트 도박 탭에서 먼저 설정해주세요."
            )
            return

        title      = (cfg["title"] if cfg else None) or "포인트 도박"
        bet_amount = (cfg["bet_amount"] if cfg else None) or 100

        try:
            cur = await db.execute(
                """INSERT INTO chzzk_gambling_sessions(guild_id, title, options, bet_amount, created_at)
                   VALUES(?,?,?,?,?)""",
                (guild_id, title, json.dumps(options, ensure_ascii=False), bet_amount, int(time.time()))
            )
            await db.commit()
        except sqlite3.IntegrityError:
            # guild당 진행중 세션 1개 제약(UNIQUE 부분 인덱스)에 걸림 — 위 존재 확인과 이
            # INSERT 사이에 다른 태스크(실제 채팅 + 웹 테스트 큐 등)가 먼저 세션을 만든 경우.
            await self._send_chat(entry, "이미 진행 중인 도박이 있습니다. !도박종료로 먼저 종료해주세요.")
            return

        options_text = " ".join(f"{i+1}.{opt}" for i, opt in enumerate(options))
        announce = f"🎰 {title} (베팅 {bet_amount:,}P) {options_text} — !투표 <번호>로 참여! (1인 1회, 번복 불가)"
        await self._send_chat(entry, announce)
        log.info(f"치지직 채팅 도박 시작: guild={guild_id} session={cur.lastrowid} options={options}")

    async def _handle_gambling_vote(self, entry: dict, message: "Message", arg: str):
        guild_id = entry["guild_id"]
        chzzk_user_id = message.user_id
        nickname = message.profile.nickname if message.profile else "익명"

        db = await get_db()
        session = await (await db.execute(
            "SELECT id, options, bet_amount FROM chzzk_gambling_sessions WHERE guild_id=? AND settled=0",
            (guild_id,)
        )).fetchone()
        if not session:
            return  # 진행 중인 도박이 없으면 조용히 무시 — 아무 채팅에나 반응하면 스팸이 됨

        options = json.loads(session["options"])
        if not arg.isdigit():
            await self._send_chat(entry, f"{nickname}님, !투표 <번호> 형식으로 입력해주세요. (1~{len(options)})")
            return
        choice = int(arg)
        if choice < 1 or choice > len(options):
            await self._send_chat(entry, f"{nickname}님, 올바른 번호를 입력해주세요. (1~{len(options)})")
            return
        option_index = choice - 1

        verif = await (await db.execute(
            "SELECT user_id FROM chzzk_verifications WHERE guild_id=? AND chzzk_channel_id=?",
            (guild_id, chzzk_user_id)
        )).fetchone()
        if not verif:
            await self._send_chat(
                entry, f"{nickname}님, 디스코드 계정 연동 후 참여할 수 있습니다. (대시보드 입장 인증에서 치지직 계정을 연동해주세요)"
            )
            return
        discord_user_id = verif["user_id"]

        already = await (await db.execute(
            "SELECT 1 FROM chzzk_gambling_votes WHERE session_id=? AND chzzk_user_id=?",
            (session["id"], chzzk_user_id)
        )).fetchone()
        if already:
            await self._send_chat(entry, f"{nickname}님, 이미 투표하셨습니다. (번복 불가)")
            return

        bet_amount = session["bet_amount"]
        cur = await db.execute(
            "UPDATE user_points SET points = points - ? WHERE guild_id=? AND user_id=? AND points >= ?",
            (bet_amount, guild_id, discord_user_id, bet_amount)
        )
        if cur.rowcount == 0:
            pts_row = await (await db.execute(
                "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
                (guild_id, discord_user_id)
            )).fetchone()
            balance = pts_row["points"] if pts_row else 0
            await db.commit()
            await self._send_chat(entry, f"{nickname}님, 포인트가 부족합니다. (필요 {bet_amount:,}P / 보유 {balance:,}P)")
            return

        try:
            await db.execute(
                """INSERT INTO chzzk_gambling_votes(session_id, chzzk_user_id, discord_user_id, option_index, voted_at)
                   VALUES(?,?,?,?,?)""",
                (session["id"], chzzk_user_id, discord_user_id, option_index, int(time.time()))
            )
            await db.commit()
        except Exception:
            # 동시 중복 투표로 UNIQUE 제약에 걸린 경우 — 방금 차감한 베팅을 환불
            await db.execute(
                "UPDATE user_points SET points = points + ? WHERE guild_id=? AND user_id=?",
                (bet_amount, guild_id, discord_user_id)
            )
            await db.commit()
            await self._send_chat(entry, f"{nickname}님, 이미 투표하셨습니다. (번복 불가)")
            return

        await self._send_chat(entry, f"{nickname}님이 [{options[option_index]}]에 {bet_amount:,}P 베팅했습니다!")

    async def _handle_gambling_end(self, entry: dict, message: "Message"):
        guild_id = entry["guild_id"]
        if not await self._is_gambling_manager(entry, message):
            log.info(f"치지직 도박 종료 거부(권한 없음): guild={guild_id} chzzk_user={message.user_id}")
            return

        db = await get_db()
        session = await (await db.execute(
            "SELECT id, title, options, bet_amount FROM chzzk_gambling_sessions WHERE guild_id=? AND settled=0",
            (guild_id,)
        )).fetchone()
        if not session:
            await self._send_chat(entry, "진행 중인 도박이 없습니다.")
            return

        await self._settle_gambling_session(entry, session)

    async def _settle_gambling_session(self, entry: dict, session):
        guild_id = entry["guild_id"]
        options    = json.loads(session["options"])
        bet_amount = session["bet_amount"]

        db = await get_db()
        vote_rows = await (await db.execute(
            "SELECT discord_user_id, option_index FROM chzzk_gambling_votes WHERE session_id=?",
            (session["id"],)
        )).fetchall()

        voters_by_option: dict[int, list[int]] = {i: [] for i in range(len(options))}
        for r in vote_rows:
            idx = r["option_index"]
            if 0 <= idx < len(options):
                voters_by_option[idx].append(r["discord_user_id"])

        total_voters = len(vote_rows)
        vote_counts  = {i: len(voters_by_option[i]) for i in range(len(options))}
        winner_index = resolve_gambling_winner(vote_counts) if total_voters > 0 else None
        winners      = voters_by_option[winner_index] if winner_index is not None else []
        payout       = calc_gambling_payout(winners, total_voters, bet_amount)

        if winners:
            # 당첨자 수만큼 INSERT를 반복하는 대신 한 번의 executemany로 일괄 처리 — 대규모
            # 당첨자 수에서도 DB 왕복이 늘어나지 않는다.
            await db.executemany(
                """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,?)
                   ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points+?""",
                [(guild_id, uid, payout, payout) for uid in winners]
            )

        await db.execute(
            "UPDATE chzzk_gambling_sessions SET settled=1, winner_index=?, settled_at=? WHERE id=?",
            (winner_index, int(time.time()), session["id"])
        )
        await db.commit()

        if total_voters == 0:
            result_text = f"🏁 {session['title']} 종료 — 참여자가 없어 정산할 내용이 없습니다."
        else:
            result_text = (
                f"🏁 {session['title']} 종료! 당첨: [{options[winner_index]}] "
                f"당첨자 {len(winners)}명, 각 +{payout:,}P 지급! (총 참여 {total_voters}명)"
            )
        await self._send_chat(entry, result_text)
        log.info(
            f"치지직 채팅 도박 정산: guild={guild_id} session={session['id']} "
            f"winner={winner_index} voters={total_voters} payout={payout}"
        )

    # ── 마크 콜라보 이벤트: !디버프지급 / !버프지급 / !랜덤아이템 ──────────────────
    async def _pick_item(self, event_id: int, item_type: str | None) -> dict | None:
        db = await get_db()
        if item_type:
            rows = await (await db.execute(
                "SELECT * FROM mc_event_items WHERE event_id=? AND item_type=? AND is_active=1",
                (event_id, item_type)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM mc_event_items WHERE event_id=? AND is_active=1 AND in_random_pool=1",
                (event_id,)
            )).fetchall()
        if not rows:
            return None
        return dict(random.choice(rows))

    async def _pick_random_other_guild(self, event_id: int, exclude_guild_id: int) -> dict | None:
        db = await get_db()
        rows = await (await db.execute(
            "SELECT guild_id, mc_player_name FROM mc_event_guilds WHERE event_id=? AND guild_id != ?",
            (event_id, exclude_guild_id)
        )).fetchall()
        if not rows:
            return None
        return dict(random.choice(rows))

    @staticmethod
    def _render(template: str, *, user: str, item: str, player: str) -> str:
        return (
            template.replace("{user}", user)
                    .replace("{item}", item)
                    .replace("{player}", player)
        )

    async def _handle_mc_event(self, entry: dict, message: "Message", trigger_text: str, kind: str):
        guild_id = entry["guild_id"]
        mc_event = entry["mc_event"]
        chzzk_user_id = message.user_id
        nickname = message.profile.nickname if message.profile else "익명"

        db = await get_db()

        # 대시보드에서 치지직 계정을 연동(인증)한 유저만 대상 — 출석체크와 동일한 규칙
        verif = await (await db.execute(
            "SELECT user_id FROM chzzk_verifications WHERE guild_id=? AND chzzk_channel_id=?",
            (guild_id, chzzk_user_id)
        )).fetchone()
        if not verif:
            log.info(
                f"MC 이벤트 무시(미인증 유저): guild={guild_id} trigger=!{trigger_text} chzzk_user={chzzk_user_id} "
                f"— 대시보드 입장 인증에서 치지직 계정을 연동한 유저가 아님"
            )
            return
        discord_user_id = verif["user_id"]

        item_type = None if kind == "random" else kind
        item = await self._pick_item(mc_event["event_id"], item_type)
        if not item:
            log.info(f"MC 이벤트 아이템 없음: guild={guild_id} kind={kind}")
            return

        bal_row = await (await db.execute(
            "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
            (guild_id, discord_user_id)
        )).fetchone()
        balance = bal_row["points"] if bal_row else 0
        if balance < item["points_cost"]:
            reply = f"{nickname}님, 포인트가 부족합니다. (필요 {item['points_cost']} / 보유 {balance})"
            await self._send_chat(entry, reply)
            return

        # 타겟 결정: buff는 자기 자신, debuff(랜덤아이템으로 뽑힌 debuff 포함)는 참가자 중 무작위 1명.
        # 단, 참가 서버가 아직 자기 자신뿐(테스트 단계 등)이면 대상이 없어 조용히 무시되던 것을,
        # 자기 자신에게 적용하는 것으로 대체해 참가자 수와 무관하게 항상 파이프라인을 검증할 수 있게 한다.
        target = None
        if item["item_type"] != "buff":
            target = await self._pick_random_other_guild(mc_event["event_id"], guild_id)

        if target:
            target_guild_id = target["guild_id"]
            target_player   = target["mc_player_name"]
        else:
            target_guild_id = None
            target_player   = mc_event["mc_player_name"]

        await db.execute(
            "UPDATE user_points SET points = points - ? WHERE guild_id=? AND user_id=?",
            (item["points_cost"], guild_id, discord_user_id)
        )
        await db.commit()

        command = self._render(item["command_template"], user=nickname, item=item["name"], player=target_player)
        applied = False
        rcon_response = ""
        try:
            rcon_response = await rcon_command(
                mc_event["mc_host"], mc_event["mc_port"], mc_event["mc_rcon_password"], command
            )
            applied = True
        except Exception as e:
            rcon_response = str(e)[:200]
            log.exception(f"MC 이벤트 RCON 실행 실패 guild={guild_id} item={item['id']} command={command!r}")

        # 대상 플레이어에게 마크 내에서 별도로 알려주는 명령(예: 귓속말) — 효과가 실제로
        # 적용됐을 때만, 그리고 문구가 설정돼 있을 때만 추가로 실행한다.
        if applied and item["mc_notify_command"].strip():
            notify_command = self._render(
                item["mc_notify_command"], user=nickname, item=item["name"], player=target_player
            )
            try:
                await rcon_command(
                    mc_event["mc_host"], mc_event["mc_port"], mc_event["mc_rcon_password"], notify_command
                )
            except Exception:
                log.exception(f"MC 이벤트 대상 알림 명령 실패 guild={guild_id} item={item['id']}")

        await db.execute(
            """INSERT INTO mc_event_purchases
                   (event_id, guild_id, user_id, item_id, trigger_text, target_guild_id,
                    points_spent, applied, rcon_response, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (mc_event["event_id"], guild_id, discord_user_id, item["id"], trigger_text, target_guild_id,
             item["points_cost"], int(applied), rcon_response, int(time.time()))
        )
        await db.commit()

        chat_template = item["chat_message_template"].strip() or "{user}님이 [{item}]을(를) 사용했습니다!"
        reply = (
            self._render(chat_template, user=nickname, item=item["name"], player=target_player)
            if applied else
            f"{nickname}님의 [{item['name']}] 적용에 실패했습니다. (마크 서버 연결 확인 필요)"
        )
        await self._send_chat(entry, reply)

        log.info(
            f"MC 이벤트 처리: guild={guild_id} trigger=!{trigger_text} item={item['name']} "
            f"target_guild={target_guild_id or guild_id} applied={applied}"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChzzkChatCog(bot))
