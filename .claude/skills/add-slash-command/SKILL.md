---
name: add-slash-command
description: Workflow for adding a new Discord slash command to NexBot — which cog to put it in, how it gets loaded/synced, and whether it needs a corresponding web dashboard settings page. Use when asked to add, create, or implement a new slash command (Korean-named app command) for this bot.
---

Follow these steps when adding a new slash command:

1. **Pick or create the cog.** Commands live in `cogs/*.py`, grouped by feature (`moderation.py`, `leveling.py`, `points.py`, `chzzk.py`, `sichham.py`, `verification.py`, `reaction_roles.py`, `info.py`). Put the new command in the cog matching its feature; only create a new cog file for a genuinely new feature area.
2. **Match existing conventions in that file:**
   - Command name and `description=` are in Korean (e.g. `@app_commands.command(name="포인트", description="...")`), matching the rest of the codebase.
   - Any DB access goes through `database.get_db()` (aiosqlite), not a new connection.
   - Permission checks follow the pattern already used in that cog (e.g. `utils/checks.py` helpers, or `@app_commands.checks.has_permissions(...)`).
3. **New cog files must be registered** in the `COGS` list in `main.py`, or `setup_hook` will never load them.
4. **Command sync is global-only.** Don't add per-guild `copy_global_to` calls for normal features — the bot syncs once globally in `setup_hook`. Per-guild sync only exists as an owner-only manual command for fast testing.
5. **If the command exposes something an admin would want to configure** (channel IDs, roles, messages, thresholds), check whether it should also get a settings surface in `web/backend/routers/` + a page under `web/frontend/app/dashboard/[guildId]/`, consistent with how `points`, `chzzk`, `moderation`, `leveling`, and `verification` each have both a bot-side command and a dashboard settings page. Not every command needs this — only ones with persistent per-guild configuration.
6. **Schema changes** needed to support the command follow the `add-db-schema` skill's workflow — don't invent a new migration mechanism.
