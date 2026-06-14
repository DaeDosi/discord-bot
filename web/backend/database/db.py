import aiosqlite
import os

# __file__ = web/backend/database/db.py
# 프로젝트 루트 = 세 단계 위 (database → backend → web → discord_workspace)
_HERE         = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))

_raw = os.getenv("DATABASE_URL", "sqlite:///./bot.db").replace("sqlite:///", "")
# 상대 경로면 프로젝트 루트 기준으로 절대 경로로 변환
DB_PATH = _raw if os.path.isabs(_raw) else os.path.normpath(os.path.join(_PROJECT_ROOT, _raw))

_db: aiosqlite.Connection | None = None


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
            # 연결이 끊긴 경우 재연결
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
            guild_id                INTEGER PRIMARY KEY,
            mod_role_id             INTEGER,
            welcome_channel         INTEGER,
            goodbye_channel         INTEGER,
            log_channel             INTEGER,
            auto_role_id            INTEGER,
            levelup_channel         INTEGER,
            levelup_dm              INTEGER DEFAULT 0,
            automod_enabled         INTEGER DEFAULT 1,
            badwords                TEXT    DEFAULT '',
            welcome_message         TEXT    DEFAULT '',
            goodbye_message         TEXT    DEFAULT '',
            verification_channel    INTEGER,
            unverified_role_id      INTEGER,
            verified_role_id        INTEGER,
            use_chzzk_verification  INTEGER DEFAULT 0,
            verification_message    TEXT    DEFAULT ''
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
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id           INTEGER NOT NULL,
            discord_channel    INTEGER NOT NULL,
            chzzk_channel_id   TEXT    NOT NULL,
            chzzk_name         TEXT,
            chzzk_image_url    TEXT,
            is_live            INTEGER DEFAULT 0,
            mention_role_id    INTEGER,
            custom_message     TEXT,
            mention_everyone   INTEGER DEFAULT 0,
            follow_role_1month INTEGER,
            follow_role_3month INTEGER,
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
        "ALTER TABLE chzzk_subscriptions ADD COLUMN mention_everyone   INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN follow_role_1month INTEGER",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN follow_role_3month INTEGER",
        "ALTER TABLE guild_config ADD COLUMN verification_channel      INTEGER",
        "ALTER TABLE guild_config ADD COLUMN unverified_role_id        INTEGER",
        "ALTER TABLE guild_config ADD COLUMN verified_role_id          INTEGER",
        "ALTER TABLE guild_config ADD COLUMN use_chzzk_verification    INTEGER DEFAULT 0",
        "ALTER TABLE guild_config ADD COLUMN verification_message      TEXT DEFAULT ''",
        "ALTER TABLE guild_config ADD COLUMN verification_embed_msg_id INTEGER",
        "ALTER TABLE guild_config ADD COLUMN embed_color TEXT DEFAULT '#5865F2'",
        "ALTER TABLE guild_config ADD COLUMN embed_title TEXT DEFAULT '🔐 입장 인증'",
        "ALTER TABLE guild_config ADD COLUMN welcome_message TEXT DEFAULT ''",
        "ALTER TABLE guild_config ADD COLUMN goodbye_message TEXT DEFAULT ''",
        "ALTER TABLE chzzk_verifications ADD COLUMN tier_months INTEGER DEFAULT 0",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN follow_months_tier1 INTEGER DEFAULT 1",
        "ALTER TABLE chzzk_subscriptions ADD COLUMN follow_months_tier2 INTEGER DEFAULT 3",
    ]:
        try:
            await db.execute(sql)
        except Exception:
            pass
    await db.commit()
