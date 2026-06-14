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
    "cogs.reaction_roles",
    "cogs.chzzk",
    "cogs.verification",
    "cogs.sichham",
    "cogs.help",
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

    async def on_ready(self):
        print(f"\n봇 준비 완료: {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)}개의 서버"
            )
        )

    async def on_guild_join(self, guild: discord.Guild):
        print(f"서버 참가: {guild.name} (ID: {guild.id})")

    # 봇 오너 전용: @봇이름 sync  →  글로벌 슬래시 커맨드 강제 동기화
    @commands.command(name="sync")
    @commands.is_owner()
    async def sync_commands(self, ctx: commands.Context):
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
        await close_db()
        await super().close()

    # ── 30분 통계 자동 업데이트 ───────────────────────────────────────────────
    @tasks.loop(minutes=30)
    async def update_stats(self):
        try:
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
        except Exception as e:
            print(f"[stats] 업데이트 실패: {e}")

    @update_stats.before_loop
    async def before_update_stats(self):
        await self.wait_until_ready()


async def main():
    if not TOKEN:
        raise RuntimeError(".env 파일에 DISCORD_TOKEN을 설정해주세요.")

    bot = AllInOneBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
