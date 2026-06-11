import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from database import init_db, close_db

load_dotenv()

TOKEN         = os.getenv("DISCORD_TOKEN")
OWNER_ID      = int(os.getenv("OWNER_ID", 0))
TEST_GUILD_ID = int(os.getenv("TEST_GUILD_ID", 0))

COGS = [
    "cogs.admin",
    "cogs.welcome",
    "cogs.leveling",
    "cogs.moderation",
    "cogs.reaction_roles",
    "cogs.chzzk",
]


class AllInOneBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members     = True
        intents.message_content = True
        intents.reactions   = True
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
        # 특정 길드에 즉시 동기화 (TEST_GUILD_ID 설정 시)
        if TEST_GUILD_ID:
            guild_obj = discord.Object(id=TEST_GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
            print(f"길드 {TEST_GUILD_ID}에 슬래시 커맨드 즉시 동기화 완료")
        # 글로벌 동기화 (전파에 최대 1시간 소요)
        synced = await self.tree.sync()
        print(f"슬래시 커맨드 {len(synced)}개 글로벌 동기화 완료")

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

    async def close(self):
        await close_db()
        await super().close()


async def main():
    if not TOKEN:
        raise RuntimeError(".env 파일에 DISCORD_TOKEN을 설정해주세요.")

    bot = AllInOneBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
