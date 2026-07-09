import os
import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from database import init_db, close_db, get_db

load_dotenv()

TOKEN    = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))

COGS = [
    "cogs.welcome",
    "cogs.leveling",
    "cogs.moderation",
    "cogs.points",
    "cogs.reaction_roles",
    "cogs.chzzk",
    "cogs.chzzk_chat",
    "cogs.verification",
    "cogs.sichham",
    "cogs.info",
]


class AllInOneBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members         = True
        intents.message_content = True
        intents.reactions       = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            owner_id=OWNER_ID,
            help_command=None,
        )
        self._last_refresh_request_handled = 0.0

    async def setup_hook(self):
        await init_db()
        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"  ✓ {cog}")
            except Exception as e:
                print(f"  ✗ {cog}: {e}")

        # Persistent View 등록 (봇 재시작 후에도 버튼이 동작하도록)
        from cogs.verification import VerifyView
        self.add_view(VerifyView())

        # 글로벌 동기화 — guild 복사 없이 global 하나만 등록해 중복 방지
        synced = await self.tree.sync()
        print(f"슬래시 커맨드 {len(synced)}개 글로벌 동기화 완료")

        self.update_stats.start()
        self.poll_manual_refresh.start()
        self.reconcile_verifications.start()

    async def on_ready(self):
        print(f"\n봇 준비 완료: {self.user} (ID: {self.user.id})")
        await self._refresh_stats_and_presence()

    async def on_guild_join(self, guild: discord.Guild):
        print(f"서버 참가: {guild.name} (ID: {guild.id})")

    async def on_guild_remove(self, guild: discord.Guild):
        print(f"서버 퇴장: {guild.name} (ID: {guild.id})")
        db = await get_db()
        await db.execute("DELETE FROM chzzk_verifications WHERE guild_id = ?", (guild.id,))
        await db.commit()

    # 봇 오너 전용: @봇이름 sync        → 글로벌 동기화
    #              @봇이름 sync guild  → 이 서버에 즉시 반영 (테스트용)
    @commands.command(name="sync")
    @commands.is_owner()
    async def sync_commands(self, ctx: commands.Context, scope: str = "global"):
        if scope == "guild":
            self.tree.copy_global_to(guild=ctx.guild)
            synced = await self.tree.sync(guild=ctx.guild)
            await ctx.send(f"✅ 이 서버에 {len(synced)}개 커맨드 즉시 동기화 완료 (테스트용)")
        else:
            synced = await self.tree.sync()
            await ctx.send(f"✅ 글로벌 {len(synced)}개 커맨드 동기화 완료")

    # 봇 오너 전용: @봇이름 clearguild  →  이 서버의 guild 명령어 전부 삭제 (중복 제거)
    @commands.command(name="clearguild")
    @commands.is_owner()
    async def clear_guild_commands(self, ctx: commands.Context):
        self.tree.clear_commands(guild=ctx.guild)
        await self.tree.sync(guild=ctx.guild)
        await ctx.send("✅ 이 서버의 guild 명령어 전부 삭제됨 (global 명령어만 남음)")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, (app_commands.CheckFailure, app_commands.MissingPermissions)):
            msg = "이 명령어를 사용할 권한이 없습니다."
        elif isinstance(error, app_commands.BotMissingPermissions):
            perms = ", ".join(error.missing_permissions)
            msg = f"봇에 필요한 권한이 없습니다: **{perms}**"
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"잠시 후 다시 시도해주세요. ({error.retry_after:.1f}초)"
        elif isinstance(error, app_commands.CommandNotFound):
            return
        else:
            print(f"[app_command_error] {error}")
            msg = "명령어 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    async def close(self):
        self.update_stats.cancel()
        self.poll_manual_refresh.cancel()
        self.reconcile_verifications.cancel()
        await close_db()
        await super().close()

    async def _refresh_stats_and_presence(self):
        db = await get_db()
        guilds = len(self.guilds)
        row = await (await db.execute(
            "SELECT COUNT(*) FROM chzzk_subscriptions"
        )).fetchone()
        chzzk_subs = int(row[0]) if row else 0
        await db.execute(
            """INSERT INTO bot_stats(id, guilds, chzzk_subs, updated_at) VALUES(1,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   guilds     = excluded.guilds,
                   chzzk_subs = excluded.chzzk_subs,
                   updated_at = excluded.updated_at""",
            (guilds, chzzk_subs, time.time())
        )
        await db.commit()
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{guilds}개의 서버"
            )
        )

    # ── 30분 통계/presence 자동 업데이트 ──────────────────────────────────────
    @tasks.loop(minutes=30)
    async def update_stats(self):
        try:
            await self._refresh_stats_and_presence()
        except Exception as e:
            print(f"[stats] 업데이트 실패: {e}")

    @update_stats.before_loop
    async def before_update_stats(self):
        await self.wait_until_ready()

    # ── nexadmin "새로고침" 버튼 감지 (10초 주기 폴링) ────────────────────────
    # 봇과 web/backend는 별도 프로세스라 직접 호출이 불가능 — bot_stats.refresh_requested_at에
    # 찍힌 타임스탬프를 신호로 삼아, 바뀌었을 때만 즉시 presence/통계를 재계산한다.
    @tasks.loop(seconds=10)
    async def poll_manual_refresh(self):
        try:
            db = await get_db()
            row = await (await db.execute(
                "SELECT refresh_requested_at FROM bot_stats WHERE id=1"
            )).fetchone()
            requested_at = row[0] if row else 0
            if requested_at and requested_at > self._last_refresh_request_handled:
                self._last_refresh_request_handled = requested_at
                await self._refresh_stats_and_presence()
        except Exception as e:
            print(f"[refresh] 수동 새로고침 처리 실패: {e}")

    @poll_manual_refresh.before_loop
    async def before_poll_manual_refresh(self):
        await self.wait_until_ready()

    # ── 6시간마다 치지직 인증 정리 (봇 오프라인 중 놓친 on_member_remove 보정) ────
    # on_member_remove/on_guild_remove로 대부분 즉시 정리되지만, 봇이 다운된 사이 나간
    # 유저는 이벤트를 못 받으므로 실제 서버 멤버 목록과 대조해 남은 인증 기록을 정리한다.
    @tasks.loop(hours=6)
    async def reconcile_verifications(self):
        try:
            db = await get_db()
            rows = await (await db.execute(
                "SELECT DISTINCT guild_id FROM chzzk_verifications"
            )).fetchall()
            for row in rows:
                guild_id = row[0]
                guild = self.get_guild(guild_id)
                if guild is None:
                    await db.execute(
                        "DELETE FROM chzzk_verifications WHERE guild_id=?", (guild_id,)
                    )
                    continue
                member_ids = {m.id for m in guild.members}
                verif_rows = await (await db.execute(
                    "SELECT user_id FROM chzzk_verifications WHERE guild_id=?", (guild_id,)
                )).fetchall()
                stale_ids = [r[0] for r in verif_rows if r[0] not in member_ids]
                if stale_ids:
                    placeholders = ",".join("?" * len(stale_ids))
                    await db.execute(
                        f"DELETE FROM chzzk_verifications WHERE guild_id=? AND user_id IN ({placeholders})",
                        (guild_id, *stale_ids)
                    )
            await db.commit()
        except Exception as e:
            print(f"[reconcile] 인증 정리 실패: {e}")

    @reconcile_verifications.before_loop
    async def before_reconcile_verifications(self):
        await self.wait_until_ready()


async def main():
    if not TOKEN:
        raise RuntimeError(".env 파일에 DISCORD_TOKEN을 설정해주세요.")

    bot = AllInOneBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
