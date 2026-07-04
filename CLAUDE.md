# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

This is a monorepo for **NexBot**, a Discord bot with a web dashboard, split into independently-run services:

- **Root (`main.py`, `cogs/`, `database/`, `utils/`)** — the Discord bot itself (discord.py). Cogs are loaded from the `COGS` list in `main.py`; each new cog must be added there to load.
- **`web/backend/`** — FastAPI dashboard API (separate process, separate `requirements.txt`, its own `Dockerfile`). Routers live in `web/backend/routers/`. Discord OAuth login issues a JWT (`web/backend/auth.py`); CORS allows `*` because auth uses the `Authorization` header, not cookies.
- **`web/frontend/`** — Next.js dashboard (`app/dashboard/[guildId]/...` per-feature settings pages).
- **`relay/relay.py`** — standalone long-running process meant to run on a Korea-IP host (e.g. Oracle Cloud VM), separate from Railway. It polls Chzzk's unofficial community API (which geo-blocks non-Korean IPs) and forwards new posts to the Railway backend via a webhook secured with `RELAY_SECRET`.
- **`chzzk-repo/`** — separate Expo/React Native app (`chzzk-noti-app`), early-stage, unrelated to the web dashboard's build.

**Chzzk has two independent linkage flows — don't conflate them:**
1. **Streamer OAuth** (`web/backend/routers/chzzk_auth_router.py`, dashboard "방송설정 → 치지직" page): the streamer links their own Chzzk account so the bot can read their follower list and (as of the realtime chat feature) subscribe to/post in their live chat. Tokens land in `chzzk_subscriptions.streamer_access_token`/`streamer_refresh_token`.
2. **Viewer verification** (`web/backend/routers/verify_router.py` + `chzzk_auth_router.py`'s viewer flow, dashboard "입장 인증" page): an individual Discord member links *their own* Chzzk account so the bot can map `discord user_id ↔ chzzk_channel_id`. Rows land in `chzzk_verifications`.

Chat-triggered features (출석체크 등, `cogs/chzzk_chat.py`) only work for viewers who did flow 2 — a streamer who only did flow 1 is not "known" to the checkin logic, even in their own chat.

- **`cogs/chzzk_chat.py`** — runs in the bot process (not web/backend). Subscribes to a streamer's live chat via Chzzk's *official* Session API (not chat scraping) using the [`chzzkpy`](https://pypi.org/project/chzzkpy/) library, reusing the streamer's OAuth token from flow 1. Two library-internals gotchas worth knowing before touching this file again:
  - `Client.connect()`'s return value is the Engine.IO transport id, **not** the `sessionKey` the subscribe REST call needs — the real sessionKey only arrives via the `on_connect` event payload.
  - `chzzkpy` doesn't surface a "the socket died" callback (a failed heartbeat just silently kills a background task), so liveness is checked by peeking at `Client._gateway[transport_sid].is_connected` each sync cycle (`SYNC_INTERVAL_SECONDS`, currently 300s) and forcing a full reconnect if it's gone.
  - Chat commands are configured per guild in `chzzk_chat_commands` (`checkin` type, max 1 per guild; `reply` type, max 5) and enforced once-per-day via `chzzk_checkin_log`. A rolling debug feed of raw in/out chat lines is kept in `chzzk_chat_log` (last 50 per guild) and surfaced in the dashboard's "실시간 채팅 명령어" tab for exactly this kind of "is it actually receiving anything" debugging.

**Critical shared-state detail:** the Discord bot and `web/backend` are two separate Python processes that read/write the **same SQLite file** (`bot.db`, WAL mode via `aiosqlite`). In production (Railway) both run in one container via `start.sh` — bot in the background, backend in the foreground serving `$PORT`. Any schema or data change made by one is immediately visible to the other; don't assume isolated state between bot and backend code.

Slash commands sync **globally only** (`main.py`'s `setup_hook`) — there is deliberately no per-guild copy in normal operation (the owner-only `sync guild` prefix command exists only for fast local testing, and `clearguild` exists to purge accidental per-guild duplicates).

## Database

`database/db.py` owns schema for the entire project (both bot and backend import from here). There is no migration framework:
- Base schema is one `executescript` call in `init_db()`.
- Every schema change made *after* that baseline is appended as a new entry in the trailing list of raw SQL strings (`ALTER TABLE ...` / `CREATE TABLE IF NOT EXISTS ...`), each run individually and wrapped so failures (e.g. column already exists) are silently ignored.
- When adding a column or table, follow this same append-only pattern — don't rewrite the base `executescript` block, and don't introduce a new migration tool.

## Environment variables

No `.env.example` exists; required variables (see `.gitignore` — `.env` is never committed) are: `DISCORD_TOKEN`, `OWNER_ID`, `DATABASE_URL`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_REDIRECT_URI`, `JWT_SECRET`, `CHZZK_POLL_INTERVAL`, `FRONTEND_URL`, `CHZZK_CLIENT_ID`, `CHZZK_CLIENT_SECRET`, `CHZZK_REDIRECT_URI`. The last three are read by both `web/backend` (OAuth flows) and, as of the realtime chat feature, the bot process itself (`cogs/chzzk_chat.py`) — same values, both processes.

## Conventions

- Slash command names and cog user-facing strings are in Korean; keep new commands consistent with that.
- No test suite, linter, or CI currently exists in this repo — there's nothing to run before considering a change complete beyond manual verification.
- No branch/commit convention is enforced; this repo is committed to directly on `main`.
