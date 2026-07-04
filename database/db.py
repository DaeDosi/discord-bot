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
    ]:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()
