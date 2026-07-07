import aiosqlite
import os

# __file__ = discord_workspace/database/db.py  → 프로젝트 루트 = 한 단계 위
_HERE         = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

_raw = os.getenv("DATABASE_URL", "sqlite:///./bot.db").replace("sqlite:///", "")
DB_PATH = _raw if os.path.isabs(_raw) else os.path.normpath(os.path.join(_PROJECT_ROOT, _raw))

_db: aiosqlite.Connection | None = None

# 프로세스 시작 시 DB 파일이 이미 있었는지(볼륨이 유지됨) 새로 생겼는지(볼륨 초기화됨)
# 배포/재시작할 때마다 로그로 확인할 수 있도록 기록
if os.path.exists(DB_PATH):
    print(f"[database] DB_PATH={DB_PATH} (기존 파일 발견, 크기={os.path.getsize(DB_PATH)} bytes)", flush=True)
else:
    print(f"[database] DB_PATH={DB_PATH} (파일 없음 — 새로 생성됩니다. 볼륨이 유지되지 않았을 수 있습니다)", flush=True)


async def _new_connection() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is not None:
        try:
            await _db.execute("SELECT 1")
        except Exception:
            try:
                await _db.close()
            except Exception:
                pass
            _db = None
    if _db is None:
        _db = await _new_connection()
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


async def init_db():
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id        INTEGER PRIMARY KEY,
            mod_role_id     INTEGER,
            welcome_channel INTEGER,
            goodbye_channel INTEGER,
            log_channel     INTEGER,
            auto_role_id    INTEGER,
            levelup_channel INTEGER,
            levelup_dm      INTEGER DEFAULT 0,
            automod_enabled INTEGER DEFAULT 1,
            badwords        TEXT    DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS user_xp (
            guild_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            xp         INTEGER DEFAULT 0,
            level      INTEGER DEFAULT 0,
            last_xp_ts REAL    DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS level_rewards (
            guild_id INTEGER NOT NULL,
            level    INTEGER NOT NULL,
            role_id  INTEGER NOT NULL,
            PRIMARY KEY (guild_id, level)
        );

        CREATE TABLE IF NOT EXISTS warnings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            mod_id     INTEGER NOT NULL,
            reason     TEXT    NOT NULL,
            created_at REAL    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_warnings_guild_user
            ON warnings(guild_id, user_id);

        CREATE TABLE IF NOT EXISTS mutes (
            guild_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            unmute_at  REAL    NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS reaction_roles (
            guild_id   INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            emoji      TEXT    NOT NULL,
            role_id    INTEGER NOT NULL,
            PRIMARY KEY (guild_id, message_id, emoji)
        );

        CREATE TABLE IF NOT EXISTS chzzk_subscriptions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id         INTEGER NOT NULL,
            discord_channel  INTEGER NOT NULL,
            chzzk_channel_id TEXT    NOT NULL,
            chzzk_name       TEXT,
            chzzk_image_url  TEXT,
            is_live          INTEGER DEFAULT 0,
            mention_role_id  INTEGER,
            custom_message   TEXT,
            UNIQUE(guild_id, chzzk_channel_id)
        );

        CREATE TABLE IF NOT EXISTS chzzk_verifications (
            guild_id    INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            verified_at REAL    NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS bot_stats (
            id         INTEGER PRIMARY KEY,
            guilds     INTEGER DEFAULT 0,
            chzzk_subs INTEGER DEFAULT 0,
            updated_at REAL    DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS daily_visitors (
            date     TEXT NOT NULL,
            ip_hash  TEXT NOT NULL,
            PRIMARY KEY (date, ip_hash)
        );
    """)
    await db.commit()

    # 기존 DB에 새 컬럼 추가 (이미 있으면 무시)
    for sql in [
        "ALTER TABLE guild_config ADD COLUMN verification_channel    INTEGER",
        "ALTER TABLE guild_config ADD COLUMN unverified_role_id      INTEGER",
        "ALTER TABLE guild_config ADD COLUMN verified_role_id        INTEGER",
        "ALTER TABLE guild_config ADD COLUMN use_chzzk_verification  INTEGER DEFAULT 0",
        "ALTER TABLE guild_config ADD COLUMN verification_message    TEXT DEFAULT ''",
        "ALTER TABLE guild_config ADD COLUMN verification_embed_msg_id INTEGER",
        "ALTER TABLE guild_config ADD COLUMN embed_color TEXT DEFAULT '#5865F2'",
        "ALTER TABLE guild_config ADD COLUMN embed_title TEXT DEFAULT '🔐 입장 인증'",
        "ALTER TABLE guild_config ADD COLUMN warn_kick_threshold INTEGER DEFAULT 0",
        "ALTER TABLE guild_config ADD COLUMN warn_ban_threshold  INTEGER DEFAULT 0",
        "ALTER TABLE guild_config ADD COLUMN points_per_level    INTEGER DEFAULT 0",
        "ALTER TABLE guild_config ADD COLUMN welcome_message TEXT DEFAULT ''",
        "ALTER TABLE guild_config ADD COLUMN goodbye_message TEXT DEFAULT ''",
        """CREATE TABLE IF NOT EXISTS user_points (
               guild_id INTEGER NOT NULL,
               user_id  INTEGER NOT NULL,
               points   INTEGER DEFAULT 0,
               PRIMARY KEY (guild_id, user_id)
           )""",
        """CREATE TABLE IF NOT EXISTS missions (
               id          INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id    INTEGER NOT NULL,
               title       TEXT NOT NULL,
               description TEXT DEFAULT '',
               points      INTEGER DEFAULT 0,
               created_at  INTEGER NOT NULL,
               is_active   INTEGER DEFAULT 1
           )""",
        """CREATE TABLE IF NOT EXISTS mission_completions (
               id          INTEGER PRIMARY KEY AUTOINCREMENT,
               mission_id  INTEGER NOT NULL,
               guild_id    INTEGER NOT NULL,
               user_id     INTEGER NOT NULL,
               status      TEXT DEFAULT 'pending',
               submitted_at INTEGER NOT NULL,
               reviewed_at  INTEGER,
               reviewer_id  INTEGER,
               UNIQUE(guild_id, mission_id, user_id)
           )""",
        """CREATE TABLE IF NOT EXISTS mod_managers (
               guild_id INTEGER NOT NULL,
               user_id  INTEGER NOT NULL,
               PRIMARY KEY (guild_id, user_id)
           )""",
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_mission_completions_unique
               ON mission_completions(guild_id, mission_id, user_id)""",
        """CREATE TABLE IF NOT EXISTS shop_items (
               id          INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id    INTEGER NOT NULL,
               name        TEXT NOT NULL,
               description TEXT DEFAULT '',
               image_url   TEXT DEFAULT '',
               points_cost INTEGER NOT NULL DEFAULT 0,
               stock       INTEGER DEFAULT -1,
               is_active   INTEGER DEFAULT 1,
               created_at  INTEGER NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS shop_exchanges (
               id           INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id     INTEGER NOT NULL,
               user_id      INTEGER NOT NULL,
               item_id      INTEGER NOT NULL,
               exchanged_at INTEGER NOT NULL,
               is_used      INTEGER DEFAULT 0,
               used_at      INTEGER
           )""",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN notify_vod        INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN notify_clip       INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN notify_community   INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN last_vod_id       TEXT",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN last_clip_id      TEXT",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN last_post_id      TEXT",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN vod_channel       INTEGER",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN clip_channel      INTEGER",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN community_channel  INTEGER",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN mention_everyone   INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN follow_role_1month INTEGER",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN follow_role_3month INTEGER",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN follow_months_tier1 INTEGER DEFAULT 1",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN follow_months_tier2 INTEGER DEFAULT 3",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN streamer_access_token TEXT",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN streamer_refresh_token TEXT",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN streamer_token_expires_at INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_verifications ADD COLUMN tier_months INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_verifications ADD COLUMN follow_months INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_verifications ADD COLUMN follow_date TEXT",
        "ALTER TABLE chzzk_verifications ADD COLUMN follow_days INTEGER DEFAULT -1",
        "ALTER TABLE chzzk_verifications ADD COLUMN chzzk_channel_id TEXT",
        """CREATE TABLE IF NOT EXISTS chzzk_follow_roles (
               id       INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id INTEGER NOT NULL,
               months   INTEGER NOT NULL,
               role_id  INTEGER NOT NULL,
               UNIQUE(guild_id, months)
           )""",
        """CREATE TABLE IF NOT EXISTS points_gambling_config (
               guild_id    INTEGER PRIMARY KEY,
               title       TEXT    NOT NULL DEFAULT '포인트 도박',
               duration    INTEGER NOT NULL DEFAULT 60,
               bet_amount  INTEGER NOT NULL DEFAULT 100
           )""",
        """CREATE TABLE IF NOT EXISTS points_gambling_options (
               id          INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id    INTEGER NOT NULL,
               opt_index   INTEGER NOT NULL,
               content     TEXT    NOT NULL,
               UNIQUE(guild_id, opt_index)
           )""",
        """CREATE TABLE IF NOT EXISTS points_poll_sessions (
               id          INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id    INTEGER NOT NULL,
               channel_id  INTEGER NOT NULL,
               message_id  INTEGER NOT NULL,
               bet_amount  INTEGER NOT NULL,
               options     TEXT    NOT NULL,
               settled     INTEGER NOT NULL DEFAULT 0,
               created_at  INTEGER NOT NULL,
               UNIQUE(guild_id, message_id)
           )""",
        # points_gambling_config.duration was originally seconds (10~3600); the
        # discord.Poll-based rewrite requires whole hours. One-time conversion,
        # gated by duration_unit_migrated so re-running on every startup doesn't
        # keep clobbering admin-set hour values back down to 1.
        "ALTER TABLE points_gambling_config ADD COLUMN duration_unit_migrated INTEGER DEFAULT 0",
        """UPDATE points_gambling_config SET duration = 1, duration_unit_migrated = 1
           WHERE duration_unit_migrated = 0""",
        """CREATE TABLE IF NOT EXISTS site_announcement (
               id         INTEGER PRIMARY KEY,
               message    TEXT    NOT NULL DEFAULT '',
               updated_at INTEGER NOT NULL DEFAULT 0
           )""",
        # 치지직 실시간 채팅 명령어: command_type='checkin'은 guild당 1개(포인트+애정도XP 지급,
        # 1일1회), command_type='reply'는 guild당 최대 5개(자동 응답 텍스트만 전송)로 제한됨(백엔드에서 검사).
        """CREATE TABLE IF NOT EXISTS chzzk_chat_commands (
               id            INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id      INTEGER NOT NULL,
               command_type  TEXT    NOT NULL DEFAULT 'checkin',
               trigger_text  TEXT    NOT NULL,
               reward_points INTEGER NOT NULL DEFAULT 0,
               reward_xp     INTEGER NOT NULL DEFAULT 0,
               reply_text    TEXT    NOT NULL DEFAULT '',
               is_active     INTEGER NOT NULL DEFAULT 1,
               created_at    INTEGER NOT NULL,
               UNIQUE(guild_id, trigger_text)
           )""",
        # 출석체크 중복 지급 방지 — (guild, 치지직 유저, 날짜, 명령어) 조합당 1회만 허용
        """CREATE TABLE IF NOT EXISTS chzzk_checkin_log (
               guild_id         INTEGER NOT NULL,
               chzzk_channel_id TEXT    NOT NULL,
               command_id       INTEGER NOT NULL,
               check_date       TEXT    NOT NULL,
               checked_at       INTEGER NOT NULL,
               PRIMARY KEY (guild_id, chzzk_channel_id, command_id, check_date)
           )""",
        # 대시보드 "실시간 채팅 명령어" 탭에서 연결 상태를 보여주기 위한 필드.
        # chat_last_sync_at: 봇의 동기화 루프가 이 채널 구독을 마지막으로 확인한 시각.
        # chat_last_event_at: 실제 채팅 이벤트를 마지막으로 수신한 시각 (진짜 연결 여부의 근거).
        "ALTER TABLE chzzk_subscriptions ADD COLUMN chat_last_sync_at  INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN chat_last_event_at INTEGER DEFAULT 0",
        # 대시보드에서 실제 치지직 채팅 수신/봇 응답을 실시간으로 확인할 수 있는 디버그용
        # 채팅 로그 (guild당 최근 N개만 유지, 봇이 삽입할 때마다 오래된 것을 정리함).
        """CREATE TABLE IF NOT EXISTS chzzk_chat_log (
               id         INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id   INTEGER NOT NULL,
               direction  TEXT    NOT NULL DEFAULT 'in',
               nickname   TEXT    NOT NULL DEFAULT '',
               content    TEXT    NOT NULL DEFAULT '',
               created_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_chzzk_chat_log_guild ON chzzk_chat_log(guild_id, id)",
        # 마인크래프트 콜라보 이벤트 (10명 스트리머 합방) — nexadmin 전용 크로스길드 설정.
        # 이벤트당 하나의 공유 마크 서버(RCON)에 연결하고, 참가 서버(guild)마다 인게임
        # 플레이어 이름을 등록해둔다. is_active=1인 이벤트가 최대 1개일 때만 chzzk_chat.py가
        # !디버프지급/!버프지급/!랜덤아이템 채팅 명령어를 처리한다.
        """CREATE TABLE IF NOT EXISTS mc_events (
               id               INTEGER PRIMARY KEY AUTOINCREMENT,
               name             TEXT    NOT NULL,
               is_active        INTEGER NOT NULL DEFAULT 0,
               mc_host          TEXT    NOT NULL DEFAULT '',
               mc_port          INTEGER NOT NULL DEFAULT 25575,
               mc_rcon_password TEXT    NOT NULL DEFAULT '',
               created_at       INTEGER NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS mc_event_guilds (
               event_id       INTEGER NOT NULL,
               guild_id       INTEGER NOT NULL,
               mc_player_name TEXT    NOT NULL,
               PRIMARY KEY (event_id, guild_id)
           )""",
        # 이미 team_name으로 테이블이 생성돼 있던 배포본을 위한 정리 — 컬럼이 없으면(신규 설치)
        # 그냥 실패하고 무시된다.
        "ALTER TABLE mc_event_guilds DROP COLUMN team_name",
        # item_type: 'debuff'(디버프지급 → 무작위 다른 참가자에게 적용) | 'buff'(버프지급 → 자기 자신)
        # command_template의 {player} 자리에 실행 시점에 정해진 대상의 mc_player_name이 들어간다.
        # in_random_pool=1인 항목만 !랜덤아이템 추첨 대상이 된다. chat_message_template은 구매 성공 시
        # 치지직 채팅에 공지할 문구, mc_notify_command는 대상 플레이어에게 마크 내에서 귓속말 등으로
        # 추가로 실행할 명령(비어있으면 생략).
        """CREATE TABLE IF NOT EXISTS mc_event_items (
               id                    INTEGER PRIMARY KEY AUTOINCREMENT,
               event_id              INTEGER NOT NULL,
               item_type             TEXT    NOT NULL,
               name                  TEXT    NOT NULL,
               points_cost           INTEGER NOT NULL DEFAULT 0,
               command_template      TEXT    NOT NULL,
               chat_message_template TEXT    NOT NULL DEFAULT '',
               mc_notify_command     TEXT    NOT NULL DEFAULT '',
               in_random_pool        INTEGER NOT NULL DEFAULT 1,
               is_active             INTEGER NOT NULL DEFAULT 1
           )""",
        "ALTER TABLE mc_event_items ADD COLUMN chat_message_template TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE mc_event_items ADD COLUMN mc_notify_command     TEXT NOT NULL DEFAULT ''",
        # 치지직 채팅 명령어(트리거 문구)는 이벤트별로 하나씩 추가 — kind: 'debuff'|'buff'|'random'.
        # 트리거 문구 기본값은 각각 디버프지급/버프지급/랜덤아이템이지만 자유롭게 바꿀 수 있다.
        """CREATE TABLE IF NOT EXISTS mc_event_commands (
               id           INTEGER PRIMARY KEY AUTOINCREMENT,
               event_id     INTEGER NOT NULL,
               kind         TEXT    NOT NULL,
               trigger_text TEXT    NOT NULL,
               is_active    INTEGER NOT NULL DEFAULT 1,
               UNIQUE(event_id, trigger_text)
           )""",
        """CREATE TABLE IF NOT EXISTS mc_event_purchases (
               id              INTEGER PRIMARY KEY AUTOINCREMENT,
               event_id        INTEGER NOT NULL,
               guild_id        INTEGER NOT NULL,
               user_id         INTEGER NOT NULL,
               item_id         INTEGER NOT NULL,
               trigger_text    TEXT    NOT NULL,
               target_guild_id INTEGER,
               points_spent    INTEGER NOT NULL DEFAULT 0,
               applied         INTEGER NOT NULL DEFAULT 0,
               rcon_response   TEXT    NOT NULL DEFAULT '',
               created_at      INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_mc_event_purchases_event ON mc_event_purchases(event_id, created_at)",
        # 실시간 채팅 명령어 연동 ON/OFF 스위치. 예전에는 checkin/reply 명령어가 하나라도
        # 설정되면(또는 mc_event 참가 시) 자동으로 채팅 연결이 활성화됐는데, 이제는 이 값이
        # 유일한 기준이 된다 — 기존 사용자의 연결이 갑자기 끊기지 않도록 기본값은 1(ON).
        "ALTER TABLE chzzk_subscriptions ADD COLUMN chat_enabled INTEGER DEFAULT 1",
        # !도박/!도박종료 권한은 별도 컬럼 없이 서버 관리 > 관리 탭에 이미 있는 매니저 체계
        # (guild_config.mod_role_id + mod_managers)를 그대로 재사용한다 — 아래 DROP은 그
        # 전용 컬럼을 짧게 썼다가 정리한 흔적 (배포된 적이 있어 컬럼이 남아있을 수 있음).
        "ALTER TABLE chzzk_subscriptions DROP COLUMN manager_role_id",
        # OBS 브라우저 소스 오버레이 인증용 토큰. 오버레이 페이지는 대시보드 로그인 세션이
        # 없으므로(OBS는 커스텀 헤더를 못 보냄) URL에 박아넣는 이 토큰으로만 식별한다.
        "ALTER TABLE chzzk_subscriptions ADD COLUMN overlay_token TEXT",
        # 치지직 채팅 기반 포인트 도박. 기존 points_gambling_config/options(웹 대시보드 포인트 탭에서
        # 설정)를 그대로 불러와 채팅에서 !도박으로 시작한다. Discord Poll과 달리 자체적으로 투표를
        # 받으므로, 베팅은 즉시 차감하고(잔액 부족 시 거절) 라운드당 1인 1표만 허용해 번복을 막는다.
        """CREATE TABLE IF NOT EXISTS chzzk_gambling_sessions (
               id           INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id     INTEGER NOT NULL,
               title        TEXT    NOT NULL,
               options      TEXT    NOT NULL,
               bet_amount   INTEGER NOT NULL,
               settled      INTEGER NOT NULL DEFAULT 0,
               winner_index INTEGER,
               created_at   INTEGER NOT NULL,
               settled_at   INTEGER
           )""",
        "CREATE INDEX IF NOT EXISTS idx_chzzk_gambling_sessions_guild ON chzzk_gambling_sessions(guild_id, id)",
        # guild당 진행중(settled=0) 도박 세션은 최대 1개만 — !도박이 거의 동시에 두 번 들어와
        # (실제 채팅 + 웹 테스트 큐 등 서로 다른 asyncio 태스크로) 중복 세션이 생기는 레이스를
        # 애플리케이션 코드의 SELECT-then-INSERT가 아니라 DB 제약으로 막는다.
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_chzzk_gambling_sessions_one_active
               ON chzzk_gambling_sessions(guild_id) WHERE settled=0""",
        # UNIQUE(session_id, chzzk_user_id) — 라운드당 1인 1표, 번복 불가(재투표 INSERT는 그냥 실패).
        """CREATE TABLE IF NOT EXISTS chzzk_gambling_votes (
               session_id      INTEGER NOT NULL,
               chzzk_user_id   TEXT    NOT NULL,
               discord_user_id INTEGER NOT NULL,
               option_index    INTEGER NOT NULL,
               voted_at        INTEGER NOT NULL,
               PRIMARY KEY (session_id, chzzk_user_id)
           )""",
        # 대시보드 "실시간 채팅 미리보기"에서 실제 치지직 방송 없이도 명령어를 테스트할 수 있는
        # 큐. 봇(별도 프로세스)이 짧은 주기로 폴링해 실제 채팅 메시지처럼 동일한 처리 로직을
        # 태운다 — 로컬 개발/테스트 전용이며 실제 치지직 API 호출은 발생하지 않는다.
        """CREATE TABLE IF NOT EXISTS chzzk_chat_test_queue (
               id            INTEGER PRIMARY KEY AUTOINCREMENT,
               guild_id      INTEGER NOT NULL,
               nickname      TEXT    NOT NULL DEFAULT '테스트유저',
               chzzk_user_id TEXT    NOT NULL DEFAULT 'test_viewer',
               content       TEXT    NOT NULL,
               processed     INTEGER NOT NULL DEFAULT 0,
               created_at    INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_chzzk_chat_test_queue_pending ON chzzk_chat_test_queue(processed, id)",
    ]:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()
