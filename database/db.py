import aiosqlite
import os

# __file__ = discord_workspace/database/db.py  → 프로젝트 루트 = 한 단계 위
_HERE         = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

_raw = os.getenv("DATABASE_URL", "sqlite:///./bot.db").replace("sqlite:///", "")
DB_PATH = _raw if os.path.isabs(_raw) else os.path.normpath(os.path.join(_PROJECT_ROOT, _raw))

_db: aiosqlite.Connection | None = None

# ── 동시 접근 튜닝 (봇 프로세스 + FastAPI 프로세스가 같은 파일을 공유한다) ──────
# 값은 임의가 아니라 이 서비스의 쓰기 패턴에 맞춘 것이다:
#   쓰기는 수집기가 몰아서 하고(사이클당 수천 행 executemany), 읽기는 API가 상시.
#   가장 흔한 실패는 수집 커밋과 API 쓰기가 겹치는 순간의 'database is locked'다.
BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "10000"))
SYNCHRONOUS = os.getenv("SQLITE_SYNCHRONOUS", "NORMAL").upper()
WAL_AUTOCHECKPOINT = int(os.getenv("SQLITE_WAL_AUTOCHECKPOINT", "1000"))

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
    # 봇과 백엔드가 같은 파일을 쓰므로 쓰기 잠금이 겹친다. 기본값(0)이면 잠긴 순간
    # 바로 'database is locked'로 실패한다 → 그 시간만큼 기다렸다 재시도하게 한다.
    await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    # WAL에서 synchronous=NORMAL은 '체크포인트 시점에만 fsync'라 쓰기가 크게 빨라진다.
    # 잃을 수 있는 건 OS 크래시 직전의 마지막 트랜잭션 몇 개뿐이고(파일 손상은 아니다),
    # 이 DB의 쓰기 대부분은 재수집으로 복구되는 통계 스냅샷이라 그 절충이 맞다.
    await conn.execute(f"PRAGMA synchronous={SYNCHRONOUS}")
    # WAL이 이 페이지 수를 넘으면 자동으로 체크포인트(기본 1000페이지 ≈ 4MB).
    await conn.execute(f"PRAGMA wal_autocheckpoint={WAL_AUTOCHECKPOINT}")
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
        # nexadmin의 수동 새로고침 버튼이 찍는 타임스탬프. 봇(별도 프로세스)이 짧은 주기로
        # 폴링해서 이 값이 갱신되면 즉시 presence(서버 개수 표시)와 통계를 다시 계산한다.
        "ALTER TABLE bot_stats ADD COLUMN refresh_requested_at REAL DEFAULT 0",
        # 공개 커뮤니티 홍보 페이지(/community)에 노출할 서버를 관리자가 opt-in으로 등록.
        # 기본값 비공개 — 명시적으로 켜야 로그인 없이 누구나 보는 페이지에 노출된다.
        """CREATE TABLE IF NOT EXISTS community_listing (
               guild_id     INTEGER PRIMARY KEY,
               is_public    INTEGER NOT NULL DEFAULT 0,
               description  TEXT,
               updated_at   TEXT
           )""",
        # 치지직 연동이 안 된 서버는 커뮤니티 카드에 이동할 곳이 전혀 없었다 —
        # 관리자가 직접 붙여넣는 디스코드 초대 링크로 항상 이동 버튼을 보장한다.
        # (커뮤니티 홍보 페이지 기능은 2026-07-25 제거됨 — community_listing 테이블은
        #  append-only 관례상 drop하지 않고 dormant 상태로 남겨둔다.)
        "ALTER TABLE community_listing ADD COLUMN invite_url TEXT",
        # ── CHZZK Rising 분석 (라이징/중소형 방송 트렌드) ────────────────────────
        # web/backend/rising_collector.py가 치지직 전체 라이브 목록을 주기적으로 스냅샷.
        # 한 수집 사이클에서 관측된 라이브 방송 1건이 1행. 모든 집계(체급분포/틈새게임/
        # 라이징/히트맵)의 원천 시계열 데이터.
        """CREATE TABLE IF NOT EXISTS rising_live_snapshots (
               id                 INTEGER PRIMARY KEY AUTOINCREMENT,
               collected_at       INTEGER NOT NULL,
               chzzk_channel_id   TEXT    NOT NULL,
               channel_name       TEXT    NOT NULL DEFAULT '',
               follower_count     INTEGER NOT NULL DEFAULT 0,
               concurrent_viewers INTEGER NOT NULL DEFAULT 0,
               category_id        TEXT    NOT NULL DEFAULT '',
               category_name      TEXT    NOT NULL DEFAULT '',
               live_title         TEXT    NOT NULL DEFAULT '',
               open_date          TEXT    NOT NULL DEFAULT '',
               adult              INTEGER NOT NULL DEFAULT 0
           )""",
        "CREATE INDEX IF NOT EXISTS idx_rising_snap_time    ON rising_live_snapshots(collected_at)",
        "CREATE INDEX IF NOT EXISTS idx_rising_snap_channel ON rising_live_snapshots(chzzk_channel_id, collected_at)",
        "CREATE INDEX IF NOT EXISTS idx_rising_snap_category ON rising_live_snapshots(category_name, collected_at)",
        # (프로필 이미지 URL은 DB에 저장하지 않고 수집 사이클마다 메모리에만 유지한다 —
        #  rising_collector._LATEST_IMAGES. 팔로워 수는 상위 채널만 상세 API로 보강해 저장.)
        # 라이브 태그(신입/하꼬 등) — 신규/라이징 탭 필터/뱃지용. 쉼표로 join해 저장.
        "ALTER TABLE rising_live_snapshots ADD COLUMN tags TEXT NOT NULL DEFAULT ''",
        # 스트리머 프로필 이미지 URL 영구 저장(서버 재시작에도 유지). 수집기가 메모리로
        # 실시간 유지하되, 매일 00시(자정 이후 첫 수집) 이 테이블로 갱신한다. 30일 이상
        # 미갱신 행은 정리. (이미지 파일 자체는 저장하지 않음 — 브라우저가 치지직 CDN에서 로드)
        """CREATE TABLE IF NOT EXISTS channel_profiles (
               chzzk_channel_id TEXT PRIMARY KEY,
               image_url        TEXT NOT NULL DEFAULT '',
               updated_at       INTEGER NOT NULL DEFAULT 0
           )""",
        # 각 수집 사이클의 메타(관측 총량/성공 여부) — 집계의 '최신 사이클' 앵커이자
        # 수집기 헬스체크(대시보드/디버그에서 마지막 수집 시각·건수 확인)에 쓰인다.
        """CREATE TABLE IF NOT EXISTS rising_collect_runs (
               id            INTEGER PRIMARY KEY AUTOINCREMENT,
               collected_at  INTEGER NOT NULL UNIQUE,
               live_count    INTEGER NOT NULL DEFAULT 0,
               total_viewers INTEGER NOT NULL DEFAULT 0,
               ok            INTEGER NOT NULL DEFAULT 1,
               note          TEXT    NOT NULL DEFAULT ''
           )""",
        # ── 다운샘플링(용량 절감) ────────────────────────────────────────────
        # 10분 원본 스냅샷을 14일 들고 있으면 3.25GB까지 커진다(실측 303B/행).
        # 대신 원본은 짧게(기본 24시간)만 두고, 채널×시간 단위로 롤업해 장기 보관한다.
        # 같은 채널의 한 시간은 6행 -> 1행이 되므로 장기 구간이 약 1/6로 줄어든다.
        """CREATE TABLE IF NOT EXISTS rising_hourly_rollup (
               hour_ts          INTEGER NOT NULL,       -- 정시 epoch(UTC 기준 시각 버킷)
               chzzk_channel_id TEXT    NOT NULL,
               channel_name     TEXT    NOT NULL DEFAULT '',
               category_name    TEXT    NOT NULL DEFAULT '',
               snaps            INTEGER NOT NULL DEFAULT 0,
               avg_viewers      REAL    NOT NULL DEFAULT 0,
               peak_viewers     INTEGER NOT NULL DEFAULT 0,
               sum_viewers      INTEGER NOT NULL DEFAULT 0,
               max_follower     INTEGER NOT NULL DEFAULT 0,
               PRIMARY KEY (hour_ts, chzzk_channel_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_rising_roll_time    ON rising_hourly_rollup(hour_ts)",
        "CREATE INDEX IF NOT EXISTS idx_rising_roll_channel ON rising_hourly_rollup(chzzk_channel_id, hour_ts)",
        # 채널별 최초/최종 관측 시각 — 원본을 짧게 자르면 MIN(collected_at)으로
        # '데뷔일(first_seen)'을 알 수 없어진다. 채널 수만큼(수천 행)이라 영구 보관해도 가볍다.
        """CREATE TABLE IF NOT EXISTS rising_channel_stats (
               chzzk_channel_id TEXT PRIMARY KEY,
               first_seen       INTEGER NOT NULL,
               last_seen        INTEGER NOT NULL,
               channel_name     TEXT    NOT NULL DEFAULT ''
           )""",
        # 치지직 '첫 방송일 / 누적 방송시간' 캐시.
        # 다시보기(VOD) 최고령 영상으로 첫 방송을 '추정'하던 것을, 치지직 채널정보 화면이
        # 쓰는 비공식 엔드포인트(/channels/{id}/data?fields=channelHistory)가 주는 정확한
        # 값으로 대체하기 위한 테이블. 첫 방송일은 변하지 않으므로 사실상 영구 캐시이며,
        # 매 요청마다 외부를 다시 부르지 않도록 여기서 읽는다.
        """CREATE TABLE IF NOT EXISTS chzzk_channel_history (
               channel_id             TEXT PRIMARY KEY,          -- 32자 16진수(소문자 정규화)
               channel_name           TEXT,
               first_live_date        TEXT,                      -- 원본 "YYYY-MM-DD HH:mm:ss" (KST)
               first_live_date_iso    TEXT,                      -- "....T..+09:00" (timezone-aware)
               total_live_hours       INTEGER,
               source                 TEXT NOT NULL DEFAULT 'CHZZK_CHANNEL_HISTORY',
               status                 TEXT NOT NULL DEFAULT 'OK',-- OK|NO_HISTORY|NOT_FOUND|BLOCKED|ERROR
               collected_at           INTEGER,                   -- 마지막 '정상' 수집 시각(epoch)
               total_hours_updated_at INTEGER,                   -- 누적 방송시간 갱신 시각(epoch)
               last_error             TEXT,
               last_attempt_at        INTEGER,                   -- 실패 포함 마지막 시도(TTL 판정용)
               created_at             INTEGER NOT NULL,
               updated_at             INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_chzzk_channel_history_status "
        "ON chzzk_channel_history(status, last_attempt_at)",
        # ── 싱드컵 이벤트 (네이버 게임 치지직 라운지 자유게시판 수집) ──────────
        # 비공식 라운지 API에서 '[싱드컵]' 말머리 게시글만 골라 담는다.
        # 수집 실패로 기존 순위가 사라지면 안 되므로 행을 지우지 않고 active 플래그로
        # 관리한다(전체 페이지 수집에 성공한 회차에서 연속 2회 안 보일 때만 비활성).
        """CREATE TABLE IF NOT EXISTS singcup_feeds (
               feed_id                  INTEGER PRIMARY KEY,   -- 라운지 게시글 ID
               event_id                 TEXT    NOT NULL,
               author_id_hash           TEXT    NOT NULL,      -- user.userIdHash (닉네임은 바뀔 수 있다)
               author_nickname          TEXT    NOT NULL DEFAULT '',
               author_profile_image_url TEXT    NOT NULL DEFAULT '',
               author_verified          INTEGER NOT NULL DEFAULT 0,
               title                    TEXT    NOT NULL DEFAULT '',
               created_at               INTEGER NOT NULL,      -- epoch (KST 문자열을 파싱)
               post_updated_at          INTEGER,
               buff_count               INTEGER NOT NULL DEFAULT 0,
               nerf_count               INTEGER NOT NULL DEFAULT 0,
               view_count               INTEGER NOT NULL DEFAULT 0,
               comment_count            INTEGER NOT NULL DEFAULT 0,
               clip_url                 TEXT,                  -- 대표(첫 번째) 클립
               clip_urls                TEXT    NOT NULL DEFAULT '',  -- 개행 구분 전체 목록
               post_url                 TEXT    NOT NULL DEFAULT '',
               mobile_post_url          TEXT    NOT NULL DEFAULT '',
               board_id                 INTEGER,
               board_name               TEXT    NOT NULL DEFAULT '',
               lounge_id                TEXT    NOT NULL DEFAULT '',
               original_lounge_id       TEXT    NOT NULL DEFAULT '',
               raw_contents             TEXT,                  -- 본문 원본 JSON 문자열
               hidden_by_clean_bot      INTEGER NOT NULL DEFAULT 0,
               pinned                   INTEGER NOT NULL DEFAULT 0,
               active                   INTEGER NOT NULL DEFAULT 1,
               missing_scan_count       INTEGER NOT NULL DEFAULT 0,
               first_collected_at       INTEGER NOT NULL,
               last_collected_at        INTEGER NOT NULL,
               row_updated_at           INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_singcup_feeds_rank "
        "ON singcup_feeds(event_id, active, buff_count DESC, view_count DESC, created_at, feed_id)",
        "CREATE INDEX IF NOT EXISTS idx_singcup_feeds_author "
        "ON singcup_feeds(event_id, author_id_hash)",
        """CREATE TABLE IF NOT EXISTS singcup_collect_runs (
               id          INTEGER PRIMARY KEY AUTOINCREMENT,
               event_id    TEXT    NOT NULL,
               started_at  INTEGER NOT NULL,
               finished_at INTEGER,
               ok          INTEGER NOT NULL DEFAULT 0,
               full_scan   INTEGER NOT NULL DEFAULT 0,  -- 이벤트 구간 전체를 확인했는가
               pages       INTEGER NOT NULL DEFAULT 0,
               feeds_seen  INTEGER NOT NULL DEFAULT 0,
               matched     INTEGER NOT NULL DEFAULT 0,
               status      TEXT    NOT NULL DEFAULT 'OK',
               note        TEXT    NOT NULL DEFAULT ''
           )""",
        "CREATE INDEX IF NOT EXISTS idx_singcup_runs_time ON singcup_collect_runs(started_at)",
        # 분산 락 — Railway replica가 늘어나도 같은 시각에 한 프로세스만 수집하게 한다.
        # 조건부 UPDATE의 rowcount로 획득 여부를 판정한다(check-then-set 경합 방지).
        """CREATE TABLE IF NOT EXISTS singcup_collect_lock (
               id           INTEGER PRIMARY KEY CHECK (id = 1),
               locked_until INTEGER NOT NULL DEFAULT 0,
               owner        TEXT    NOT NULL DEFAULT ''
           )""",
        "INSERT OR IGNORE INTO singcup_collect_lock (id, locked_until, owner) VALUES (1, 0, '')",
        # 롤업 시간구간 집계용 커버링 인덱스.
        # 근거(롤업 47.9만 행 스테이징 실측): 시간대/빈집 집계는 idx_rising_roll_time으로
        # hour_ts 범위만 찾고 행마다 테이블을 다시 읽어야 했다. 필요한 컬럼을 인덱스에
        # 모두 담으면 테이블 접근 없이 인덱스만으로 끝난다.
        #   빈집 타임(7일) 2878ms -> 392ms (-86%)
        # 채널별 집계(agg7/rank_daily)는 기존 idx_rising_roll_channel을 쓰므로 영향 없음.
        # 쓰기 비용: 수집 1회당 롤업 upsert 약 3천 행에 인덱스 1개가 추가된다.
        "CREATE INDEX IF NOT EXISTS idx_rising_roll_cover ON rising_hourly_rollup"
        "(hour_ts, avg_viewers, snaps, sum_viewers, chzzk_channel_id)",
        # ── 싱드컵: 음악/노래 카테고리 클립 기반 (자유게시판 버프와는 별개 데이터) ──
        # 자유게시판(singcup_feeds)은 '홍보글' 보조 화면으로 남기고, 메인/랭킹은 아래
        # #싱드컵 태그 클립을 기준으로 한다.
        """CREATE TABLE IF NOT EXISTS singcup_clips (
               clip_uid            TEXT PRIMARY KEY,
               event_id            TEXT    NOT NULL,
               owner_channel_id    TEXT    NOT NULL,
               video_id            TEXT    NOT NULL DEFAULT '',
               clip_title          TEXT    NOT NULL DEFAULT '',
               thumbnail_image_url TEXT    NOT NULL DEFAULT '',
               description         TEXT    NOT NULL DEFAULT '',
               created_at          INTEGER NOT NULL,          -- epoch (KST 파싱)
               heart_count         INTEGER NOT NULL DEFAULT 0,-- card.interaction.emotion like
               view_count          INTEGER NOT NULL DEFAULT 0,-- card.content.vod.count
               duration            INTEGER NOT NULL DEFAULT 0,
               adult               INTEGER NOT NULL DEFAULT 0,
               blind_type          TEXT,
               -- 하트/조회수를 '실제 0'과 'API 오류로 못 읽음'으로 구분하기 위한 플래그
               metrics_ok          INTEGER NOT NULL DEFAULT 0,
               active              INTEGER NOT NULL DEFAULT 1,
               missing_scan_count  INTEGER NOT NULL DEFAULT 0,
               first_collected_at  INTEGER NOT NULL,
               last_collected_at   INTEGER NOT NULL,
               row_updated_at      INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_singcup_clips_owner "
        "ON singcup_clips(event_id, owner_channel_id, active, heart_count DESC, view_count DESC)",
        "CREATE INDEX IF NOT EXISTS idx_singcup_clips_created "
        "ON singcup_clips(event_id, created_at)",
        """CREATE TABLE IF NOT EXISTS singcup_streamers (
               channel_id                TEXT PRIMARY KEY,
               event_id                  TEXT    NOT NULL,
               channel_name              TEXT    NOT NULL DEFAULT '',
               channel_image_url         TEXT    NOT NULL DEFAULT '',
               follower_count            INTEGER NOT NULL DEFAULT 0,
               verified_mark             INTEGER NOT NULL DEFAULT 0,
               representative_clip_uid   TEXT,
               tagged_clip_count         INTEGER NOT NULL DEFAULT 0,
               last_channel_updated_at   INTEGER NOT NULL DEFAULT 0,
               row_updated_at            INTEGER NOT NULL
           )""",
        # 하트/순위 변화량 계산용 시계열. 수집 회차마다 대표 클립 기준으로 1행씩 쌓인다.
        """CREATE TABLE IF NOT EXISTS singcup_snapshots (
               id               INTEGER PRIMARY KEY AUTOINCREMENT,
               event_id         TEXT    NOT NULL,
               clip_uid         TEXT    NOT NULL,
               owner_channel_id TEXT    NOT NULL,
               heart_count      INTEGER NOT NULL DEFAULT 0,
               view_count       INTEGER NOT NULL DEFAULT 0,
               follower_count   INTEGER NOT NULL DEFAULT 0,
               score            REAL    NOT NULL DEFAULT 0,
               rank             INTEGER NOT NULL DEFAULT 0,
               collected_at     INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_singcup_snap_owner "
        "ON singcup_snapshots(event_id, owner_channel_id, collected_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_singcup_snap_time "
        "ON singcup_snapshots(event_id, collected_at)",
        # 자유게시판 게시글 ↔ 클립 연결(홍보글 화면에서 '대표 클립과 연결됨' 배지용)
        "ALTER TABLE singcup_feeds ADD COLUMN clip_uid TEXT",
        # 증분 수집용 — 카드 API는 클립 1건당 1회라 매 사이클 전량 조회하면 500회를 넘는다.
        # '이 클립을 카드까지 확인해 봤는가'와 그 결과(태그 여부)를 기록해 두고,
        # 태그가 없는 클립은 다시 조회하지 않는다(오래되면 한 번 재확인).
        """CREATE TABLE IF NOT EXISTS singcup_clip_scan (
               clip_uid   TEXT PRIMARY KEY,
               tagged     INTEGER NOT NULL DEFAULT 0,
               checked_at INTEGER NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_singcup_scan_tagged "
        "ON singcup_clip_scan(tagged, checked_at)",
        # 하트/조회수를 마지막으로 갱신한 시각 — 갱신 대상 선정에 쓴다
        "ALTER TABLE singcup_clips ADD COLUMN last_metrics_at INTEGER NOT NULL DEFAULT 0",
        # 카드 API 재조회에 필요한 값 — 지표 갱신 때 목록을 다시 훑지 않기 위해 저장한다
        "ALTER TABLE singcup_clips ADD COLUMN rec_id TEXT NOT NULL DEFAULT ''",
        # 목록 응답의 ownerChannel — 채널 API가 실패해도 닉네임이 비지 않게 하는 근거값.
        # (이름이 비면 화면에 '-'로 뜨고 검색에도 절대 걸리지 않는다)
        "ALTER TABLE singcup_clips ADD COLUMN owner_channel_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE singcup_clips ADD COLUMN owner_channel_image_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE singcup_clips ADD COLUMN owner_verified INTEGER NOT NULL DEFAULT 0",
        # ── 과거 데이터 백필 상태 ──────────────────────────────────────────
        # 초기 적재(이벤트 시작일까지 거슬러 가기)는 정기 수집과 성격이 다르다.
        # 커서와 진행 수치를 DB에 두어 재배포/재시작 후에도 이어서 처리한다.
        """CREATE TABLE IF NOT EXISTS singcup_backfill_state (
               event_id                  TEXT PRIMARY KEY,
               status                    TEXT    NOT NULL DEFAULT 'idle',
               next_cursor               TEXT,
               scanned_count             INTEGER NOT NULL DEFAULT 0,
               tagged_count              INTEGER NOT NULL DEFAULT 0,
               failed_count              INTEGER NOT NULL DEFAULT 0,
               pages_done                INTEGER NOT NULL DEFAULT 0,
               oldest_scanned_created_at INTEGER,
               started_at                INTEGER,
               updated_at                INTEGER,
               completed_at              INTEGER,
               last_error                TEXT
           )""",
        # 카드 조회에 실패한 클립만 따로 큐에 남겨 나중에 재시도한다
        """CREATE TABLE IF NOT EXISTS singcup_clip_retry (
               clip_uid    TEXT PRIMARY KEY,
               video_id    TEXT NOT NULL DEFAULT '',
               rec_id      TEXT NOT NULL DEFAULT '',
               created_at  INTEGER,
               attempts    INTEGER NOT NULL DEFAULT 0,
               next_try_at INTEGER NOT NULL DEFAULT 0,
               last_error  TEXT,
               -- 목록 응답 원본. 재시도할 때 제목·썸네일·채널ID를 다시 조회하지 않아도 된다
               item_json   TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_singcup_retry_due ON singcup_clip_retry(next_try_at)",
        # 이름 있는 분산 락 — 백필/신규탐색/지표갱신이 서로를 막지 않도록 키를 나눈다
        # (기존 singcup_collect_lock은 자유게시판 수집기 전용으로 그대로 둔다)
        """CREATE TABLE IF NOT EXISTS singcup_locks (
               name         TEXT PRIMARY KEY,
               locked_until INTEGER NOT NULL DEFAULT 0,
               owner        TEXT    NOT NULL DEFAULT ''
           )""",
        # 싱드컵 스냅샷 시간 롤업 — 원본은 24시간만 쓰고 버리되, 이벤트 순위 추이는
        # 남겨야 한다. 원본(참가자 800명 × 4분 주기 = 일 28.8만 행, 실측 198B/행이라
        # 55MB/일)을 그대로 두면 21일 이벤트에서 1.1GB가 되어 500MB 볼륨을 넘긴다.
        # 시간당 1행으로 줄이면 1/15(하루 1.9만 행 ≈ 3.7MB)이 된다.
        """CREATE TABLE IF NOT EXISTS singcup_snapshot_hourly (
               event_id         TEXT    NOT NULL,
               hour_ts          INTEGER NOT NULL,        -- 정시 epoch
               owner_channel_id TEXT    NOT NULL,
               clip_uid         TEXT    NOT NULL DEFAULT '',
               heart_count      INTEGER NOT NULL DEFAULT 0,
               view_count       INTEGER NOT NULL DEFAULT 0,
               follower_count   INTEGER NOT NULL DEFAULT 0,
               score            REAL    NOT NULL DEFAULT 0,
               rank             INTEGER NOT NULL DEFAULT 0,
               PRIMARY KEY (event_id, hour_ts, owner_channel_id)
           )""",
        "CREATE INDEX IF NOT EXISTS idx_singcup_snap_hourly_owner "
        "ON singcup_snapshot_hourly(event_id, owner_channel_id, hour_ts)",
        # 수집 사이클 계측 — 주기를 줄여도 되는지 판단하려면 '한 사이클이 얼마나
        # 걸리는지'를 알아야 하는데, 지금까지 소요시간도 호출 수도 남기지 않아
        # p95를 계산할 방법이 아예 없었다(collected_at 간격은 sleep을 포함한 값이라
        # 사이클 자체의 소요시간이 아니다).
        "ALTER TABLE rising_collect_runs ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE rising_collect_runs ADD COLUMN pages INTEGER NOT NULL DEFAULT 0",
        # 목록 API 실제 호출 횟수(재시도 포함) — 주기를 절반으로 줄일 때 늘어날 부하의 근거
        "ALTER TABLE rising_collect_runs ADD COLUMN api_calls INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()
