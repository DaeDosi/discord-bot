import discord
from discord import app_commands
from discord.ext import commands

INVITE_PERMISSIONS = 1099914472662  # kick, ban, manage roles/nicknames/channels/messages, embed, reactions, timeout 등

# ── 일반 사용자 명령어 (1페이지) ──────────────────────────────────────────────

USER_EMBED = {
    "title": "📋 NexBot 일반 명령어",
    "color": 0x5865F2,
    "fields": [
        (
            "레벨링",
            "`/랭크` — 자신(또는 멤버)의 레벨·XP를 확인합니다\n\n"
            "`/리더보드` — 서버 레벨 랭킹 상위 10명을 확인합니다",
        ),
        (
            "리액션 역할",
            "`/반응역할목록` — 서버의 리액션 역할 목록을 확인합니다",
        ),
        (
            "시청자 참여 (시참)",
            "`/시참등록` — 시참 대기열에 등록합니다\n\n"
            "`/시참취소` — 시참 등록을 취소합니다\n\n"
            "`/시참확인` — 본인의 대기 번호·현황을 확인합니다\n\n"
            "`/시참목록` — 현재 시참 대기열 전체를 확인합니다",
        ),
        (
            "기타",
            "`/명령어` — 일반 명령어 목록을 확인합니다\n\n"
            "`/관리명령어` — 관리자 명령어 목록을 확인합니다",
        ),
    ],
}

# ── 관리자 명령어 (2페이지 페이징) ───────────────────────────────────────────

ADMIN_PAGES = [
    {
        "title": "📋 NexBot 관리 명령어 (1/2)",
        "color": 0xED4245,
        "fields": [
            (
                "서버 관리",
                "`/경고` — 멤버에게 경고를 부여합니다\n\n"
                "`/경고내역` — 멤버의 경고 내역을 확인합니다\n\n"
                "`/경고초기화` — 멤버의 경고를 모두 초기화합니다\n\n"
                "`/추방` — 멤버를 서버에서 추방합니다\n\n"
                "`/차단` — 멤버를 서버에서 영구 차단합니다\n\n"
                "`/차단해제` — 차단된 유저를 해제합니다\n\n"
                "`/뮤트` — 멤버에게 타임아웃을 적용합니다\n\n"
                "`/뮤트해제` — 멤버의 타임아웃을 해제합니다\n\n"
                "`/청소` — 채널의 메시지를 일괄 삭제합니다",
            ),
            (
                "리액션 역할",
                "`/반응역할` — 메시지에 리액션 역할을 추가합니다\n\n"
                "`/반응역할제거` — 메시지의 리액션 역할을 제거합니다",
            ),
            (
                "입장 인증",
                "`/입장메시지설정` — 입장 인증 임베드를 채널에 전송합니다",
            ),
        ],
    },
    {
        "title": "📋 NexBot 관리 명령어 (2/2)",
        "color": 0xED4245,
        "fields": [
            (
                "레벨링",
                "`/xp설정` — 멤버의 XP를 직접 설정합니다",
            ),
            (
                "치지직",
                "`/치지직설정` — 웹 대시보드 치지직 알림 설정 링크를 표시합니다\n\n"
                "`/치지직알림테스트` — 등록된 치지직 알림을 테스트합니다",
            ),
            (
                "시참 관리",
                "`/시참시작` — 시참 모집을 시작합니다\n\n"
                "`/시참종료` — 시참 모집을 종료하고 대기열을 초기화합니다\n\n"
                "`/시참호출` — 대기열에서 n명을 호출합니다\n\n"
                "`/시참건너뛰기` — 유저를 대기열에서 제거합니다",
            ),
        ],
    },
]


def _build_user_embed() -> discord.Embed:
    embed = discord.Embed(title=USER_EMBED["title"], color=USER_EMBED["color"])
    for name, value in USER_EMBED["fields"]:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text="/명령어 · 관리자 명령어는 /관리명령어")
    return embed


def _build_admin_embed(page: int) -> discord.Embed:
    data = ADMIN_PAGES[page]
    embed = discord.Embed(title=data["title"], color=data["color"])
    for name, value in data["fields"]:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text=f"페이지 {page + 1} / {len(ADMIN_PAGES)}  ·  /관리명령어")
    return embed


class AdminHelpView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=120)
        self.page = page
        self._refresh()

    def _refresh(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page == len(ADMIN_PAGES) - 1

    @discord.ui.button(label="◀ 이전", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.page -= 1
        self._refresh()
        await interaction.response.edit_message(embed=_build_admin_embed(self.page), view=self)

    @discord.ui.button(label="다음 ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        self.page += 1
        self._refresh()
        await interaction.response.edit_message(embed=_build_admin_embed(self.page), view=self)


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="명령어", description="NexBot 일반 명령어 목록을 확인합니다.")
    async def cmd_list(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=_build_user_embed(), ephemeral=True
        )

    @app_commands.command(name="관리명령어", description="NexBot 관리자 명령어 목록을 확인합니다.")
    async def admin_cmd_list(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=_build_admin_embed(0), view=AdminHelpView(0), ephemeral=True
        )

    @app_commands.command(name="핑", description="봇의 응답 속도를 확인합니다.")
    async def ping(self, interaction: discord.Interaction):
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
    async def bot_info(self, interaction: discord.Interaction):
        latency = round(self.bot.latency * 1000)
        guilds  = len(self.bot.guilds)
        users   = sum(g.member_count or 0 for g in self.bot.guilds)
        bot_id  = self.bot.user.id

        invite_url = (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={bot_id}&permissions={INVITE_PERMISSIONS}&scope=bot%20applications.commands"
        )

        embed = discord.Embed(
            title="🤖 NexBot 정보",
            description="치지직 알림, 서버 관리, 레벨링, 입장 인증까지 한 번에!\n웹 대시보드로 간편하게 설정하세요.",
            color=0x5865F2,
        )
        embed.add_field(name="🏓 핑",      value=f"{latency}ms",      inline=True)
        embed.add_field(name="📡 서버 수", value=f"{guilds}개",        inline=True)
        embed.add_field(name="👥 유저 수", value=f"{users:,}명",       inline=True)
        embed.add_field(name="🌐 웹사이트",
                        value="[nexbot.shop](https://nexbot.shop)",    inline=True)
        embed.add_field(name="➕ 봇 초대",
                        value=f"[초대 링크]({invite_url})",            inline=True)
        embed.add_field(name="📋 명령어 목록",
                        value="`/명령어` `/관리명령어`",                inline=True)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_footer(text="NexBot • nexbot.shop")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
