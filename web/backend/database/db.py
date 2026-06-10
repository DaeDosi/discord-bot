import aiosqlite
import os

DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./bot.db").replace("sqlite:///", "")

_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
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
    """)
    await db.commit()
