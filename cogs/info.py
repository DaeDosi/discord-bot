import discord
from discord import app_commands
from discord.ext import commands


class InfoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="핑", description="봇의 응답 속도를 확인합니다.")
    async def 핑(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        await interaction.response.send_message(f"🏓 퐁! 응답속도: **{latency}ms**")


async def setup(bot: commands.Bot):
    await bot.add_cog(InfoCog(bot))
