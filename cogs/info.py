import discord
from discord import app_commands
from discord.ext import commands

_INVITE_PERMS = 1099914472662


class InfoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="핑", description="봇의 응답 속도를 확인합니다.")
    async def 핑(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        color = 0x57F287 if latency < 100 else (0xFEE75C if latency < 250 else 0xED4245)
        embed = discord.Embed(
            title="🏓 퐁!",
            description=f"응답속도: **{latency}ms**",
            color=color,
        )
        embed.set_footer(text="NexBot • nexbot.shop")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="봇정보", description="NexBot의 상태와 기본 정보를 확인합니다.")
    async def 봇정보(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        guilds  = len(self.bot.guilds)
        users   = sum(g.member_count or 0 for g in self.bot.guilds)
        bot_id  = self.bot.user.id
        invite  = (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={bot_id}&permissions={_INVITE_PERMS}&scope=bot%20applications.commands"
        )
        embed = discord.Embed(
            title="🤖 NexBot 정보",
            description=(
                "치지직 알림, 서버 관리, 레벨링, 입장 인증까지 한 번에!\n"
                "웹 대시보드로 간편하게 설정하세요."
            ),
            color=0x5865F2,
        )
        embed.add_field(name="🏓 핑",       value=f"{latency}ms",                        inline=True)
        embed.add_field(name="📡 서버 수",  value=f"{guilds}개",                          inline=True)
        embed.add_field(name="👥 유저 수",  value=f"{users:,}명",                         inline=True)
        embed.add_field(name="🌐 웹사이트", value="[nexbot.shop](https://nexbot.shop)",   inline=True)
        embed.add_field(name="➕ 봇 초대",  value=f"[초대 링크]({invite})",               inline=True)
        embed.add_field(name="📋 명령어",   value="`/명령어` `/관리명령어`",               inline=True)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="NexBot • nexbot.shop")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="도움말", description="NexBot의 모든 기능과 명령어를 한눈에 확인합니다.")
    async def 도움말(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 NexBot 도움말",
            description=(
                "치지직 알림, 서버 관리, 레벨링, 입장 인증까지!\n"
                "모든 설정은 **웹 대시보드**에서 쉽게 관리하세요.\n"
                "👉 [nexbot.shop](https://nexbot.shop)"
            ),
            color=0x5865F2,
        )
        embed.add_field(name="📺 치지직",    value="`/치지직설정` `/치지직알림테스트`",               inline=False)
        embed.add_field(name="⭐ 레벨링",    value="`/랭크` `/리더보드` `/xp설정`",                  inline=False)
        embed.add_field(name="🛡️ 서버 관리", value="`/경고` `/추방` `/차단` `/뮤트` `/청소`",         inline=False)
        embed.add_field(name="🎭 리액션 역할",value="`/반응역할` `/반응역할제거` `/반응역할목록`",     inline=False)
        embed.add_field(name="🔐 입장 인증", value="`/입장메시지설정`",                               inline=False)
        embed.add_field(name="📋 시참",      value="`/시참등록` `/시참취소` `/시참목록` `/시참시작`",  inline=False)
        embed.add_field(name="ℹ️ 기타",      value="`/봇정보` `/핑` `/명령어` `/관리명령어`",         inline=False)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="NexBot • nexbot.shop  |  /명령어 로 간단 목록 확인")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(InfoCog(bot))
