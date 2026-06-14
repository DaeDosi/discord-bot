import os
import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from database import init_db, close_db, get_db

load_dotenv()

TOKEN         = os.getenv("DISCORD_TOKEN")
OWNER_ID      = int(os.getenv("OWNER_ID", 0))
TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID", 0))

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

        # 특정 길드에 즉시 동기화 (TEST_GUILD_ID 설정 시)
        if TEST_GUILD_ID:
            guild_obj = discord.Object(id=TEST_GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            print(f"길드 {TEST_GUILD_ID}에 슬래시 커맨드 즉시 동기화 완료")
        # 글로벌 동기화 (전파에 최대 1시간 소요)
        synced = await self.tree.sync()
        print(f"슬래시 커맨드 {len(synced)}개 글로벌 동기화 완료")

        self.update_stats.start()

    async def on_ready(self):
        print(f"\n봇 준비 완료: {self.user} (ID: {self.user.id})")
        print(f"등록된 슬래시 커맨드: {[c.name for c in self.tree.get_commands()]}")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{len(self.guilds)}개의 서버"
            )
        )

    async def on_guild_join(self, guild: discord.Guild):
        print(f"서버 참가: {guild.name} (ID: {guild.id})")
        # 봇이 서버에 (재)참가할 때 해당 서버에 즉시 슬래시 커맨드 등록
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"[guild_join] {guild.name} 슬래시 커맨드 {len(synced)}개 즉시 동기화")
        except Exception as e:
            print(f"[guild_join] 동기화 실패: {e}")

    # 봇 오너 전용: @봇이름 sync  →  슬래시 커맨드 강제 동기화
    @commands.command(name="sync")
    @commands.is_owner()
    async def sync_commands(self, ctx: commands.Context, guild_id: str = ""):
        if guild_id:
            guild_obj = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            await ctx.send(f"✅ 길드 {guild_id}에 {len(synced)}개 커맨드 즉시 동기화 완료")
        else:
            synced = await self.tree.sync()
            await ctx.send(f"✅ 글로벌 {len(synced)}개 커맨드 동기화 완료 (Discord 반영까지 최대 1시간)")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CheckFailure):
            msg = "이 명령어를 사용할 권한이 없습니다."
        elif isinstance(error, app_commands.MissingPermissions):
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
