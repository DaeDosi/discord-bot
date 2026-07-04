---
name: add-db-schema
description: Workflow for adding or changing SQLite schema (new table or column) in this project's shared database. Use when asked to add a database table/column, store new persistent data, or otherwise change the schema in database/db.py.
---

All schema lives in `database/db.py`, shared by both the Discord bot and `web/backend` (same `bot.db` file, read/written by both processes). There is no migration framework — follow this append-only pattern instead of introducing one:

1. **Do not edit the base `executescript(...)` block** in `init_db()` unless the table doesn't exist anywhere yet and this is a brand-new base table with no deployed data to preserve.
2. **For anything else** (a new column on an existing table, or a new table added after the initial release), append a new entry to the trailing list of raw SQL strings in `init_db()` — e.g. `"ALTER TABLE guild_config ADD COLUMN new_thing INTEGER DEFAULT 0"` or a `CREATE TABLE IF NOT EXISTS ...` string. Each entry in that list is executed individually inside a try/except that silently ignores failures (so re-running on a DB that already has the column is a no-op).
3. Keep column defaults sensible for existing rows, since `ALTER TABLE ADD COLUMN` applies to every existing record immediately.
4. After editing, both the bot and `web/backend` pick up the new schema automatically the next time each process calls `init_db()` on startup — no separate migration step to run.
5. Don't add a schema-versioning table, ORM, or migration CLI — this project intentionally keeps schema management to this single append-only list.
