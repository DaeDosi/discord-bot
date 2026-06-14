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
        embed.add_field(name="📋 명령어",   value="`/도움말`",                            inline=True)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="NexBot • nexbot.shop")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="도움말", description="일반 유저가 사용할 수 있는 명령어 목록을 확인합니다.")
    async def 도움말(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 NexBot 명령어 목록",
            description="누구나 사용할 수 있는 명령어입니다.",
            color=0x5865F2,
        )
        embed.add_field(
            name="⭐ 레벨링",
            value=(
                "`/랭크` — 자신(또는 멤버)의 레벨·XP를 확인합니다\n"
                "`/리더보드` — 서버 레벨 랭킹 상위 10명을 확인합니다"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎭 리액션 역할",
            value="`/반응역할목록` — 서버의 리액션 역할 목록을 확인합니다",
            inline=False,
        )
        embed.add_field(
            name="📋 시청자 참여 (시참)",
            value=(
                "`/시참등록` — 시참 대기열에 등록합니다\n"
                "`/시참취소` — 시참 등록을 취소합니다\n"
                "`/시참확인` — 본인의 대기 번호·현황을 확인합니다\n"
                "`/시참목록` — 현재 시참 대기열 전체를 확인합니다"
            ),
            inline=False,
        )
        embed.add_field(
            name="ℹ️ 기타",
            value=(
                "`/봇정보` — NexBot의 상태와 기본 정보를 확인합니다\n"
                "`/핑` — 봇의 응답 속도를 확인합니다\n"
                "`/도움말` — 이 명령어 목록을 확인합니다"
            ),
            inline=False,
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="NexBot • nexbot.shop")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="도움말관리", description="NexBot의 모든 명령어 목록을 확인합니다. (관리자 전용)")
    @app_commands.default_permissions(manage_guild=True)
    async def 도움말관리(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 NexBot 전체 명령어 목록",
            description="관리자 포함 모든 명령어입니다.",
            color=0xED4245,
        )
        embed.add_field(
            name="⭐ 레벨링",
            value=(
                "`/랭크` — 레벨·XP 확인\n"
                "`/리더보드` — 서버 랭킹 상위 10명\n"
                "`/xp설정` — 멤버 XP 직접 설정 🔒"
            ),
            inline=False,
        )
        embed.add_field(
            name="🎭 리액션 역할",
            value=(
                "`/반응역할목록` — 리액션 역할 목록 확인\n"
                "`/반응역할` — 리액션 역할 추가 🔒\n"
                "`/반응역할제거` — 리액션 역할 제거 🔒"
            ),
            inline=False,
        )
        embed.add_field(
            name="📋 시청자 참여 (시참)",
            value=(
                "`/시참등록` — 대기열 등록\n"
                "`/시참취소` — 등록 취소\n"
                "`/시참확인` — 대기 번호 확인\n"
                "`/시참목록` — 전체 대기열 확인\n"
                "`/시참시작` — 시참 모집 시작 🔒\n"
                "`/시참종료` — 시참 종료 🔒\n"
                "`/시참호출` — n명 호출 🔒\n"
                "`/시참건너뛰기` — 유저 제거 🔒"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛡️ 서버 관리",
            value=(
                "`/경고` — 경고 부여 🔒\n"
                "`/경고내역` — 경고 내역 확인 🔒\n"
                "`/경고초기화` — 경고 초기화 🔒\n"
                "`/추방` — 멤버 추방 🔒\n"
                "`/차단` — 멤버 차단 🔒\n"
                "`/차단해제` — 차단 해제 🔒\n"
                "`/뮤트` — 타임아웃 적용 🔒\n"
                "`/뮤트해제` — 타임아웃 해제 🔒\n"
                "`/청소` — 메시지 일괄 삭제 🔒"
            ),
            inline=False,
        )
        embed.add_field(
            name="📺 치지직",
            value=(
                "`/치지직설정` — 알림 설정 대시보드 링크 🔒\n"
                "`/치지직알림테스트` — 알림 테스트 🔒"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔐 입장 인증",
            value="`/입장메시지설정` — 입장 인증 임베드 전송 🔒",
            inline=False,
        )
        embed.add_field(
            name="ℹ️ 기타",
            value=(
                "`/봇정보` — 봇 상태 정보\n"
                "`/핑` — 응답 속도 확인\n"
                "`/도움말` — 일반 명령어 목록\n"
                "`/도움말관리` — 전체 명령어 목록 🔒"
            ),
            inline=False,
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="NexBot • nexbot.shop  |  🔒 = 관리자 전용")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(InfoCog(bot))
