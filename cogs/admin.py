"""
설정 관련 명령어는 웹 대시보드로 이전되었습니다.
이 cog는 하위 호환을 위해 빈 상태로 유지됩니다.
"""
from discord.ext import commands


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
