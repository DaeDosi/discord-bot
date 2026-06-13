import discord
from discord import app_commands
from discord.ext import commands
from dataclasses import dataclass, field
from datetime import datetime

COLOR_START = 0x5865F2  # 보라 - 시작
COLOR_CALL  = 0x00FFA3  # 초록 - 호출
COLOR_END   = 0xED4245  # 빨강 - 종료
COLOR_INFO  = 0xFEE75C  # 노랑 - 정보


@dataclass
class QueueEntry:
    user:          discord.Member
    chzzk_name:    str
    registered_at: datetime
    number:        int


class SichhamSession:
    def __init__(self):
        self.active:    bool             = False
        self.max_size:  int              = 5
        self.condition: str              = "없음"
        self.queue:     list[QueueEntry] = []

    def find(self, user_id: int) -> "QueueEntry | None":
        return next((e for e in self.queue if e.user.id == user_id), None)

    def renumber(self):
        for i, entry in enumerate(self.queue, 1):
            entry.number = i


class SichhamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._sessions: dict[int, SichhamSession] = {}

    def _get(self, guild_id: int) -> SichhamSession:
        if guild_id not in self._sessions:
            self._sessions[guild_id] = SichhamSession()
        return self._sessions[guild_id]

    # ── 시청자용 ─────────────────────────────────────────────────────────────

    @app_commands.command(name="시참등록", description="시청자 참여 대기열에 등록합니다.")
    async def 시참등록(self, interaction: discord.Interaction):
        s = self._get(interaction.guild_id)

        if not s.active:
            return await interaction.response.send_message(
                "❌ 현재 시청자 참여가 활성화되지 않았습니다.", ephemeral=True
            )

        existing = s.find(interaction.user.id)
        if existing:
            return await interaction.response.send_message(
                f"⚠️ 이미 등록되어 있습니다. (**{existing.number}번**)", ephemeral=True
            )

        if len(s.queue) >= s.max_size:
            return await interaction.response.send_message(
                f"❌ 대기열이 꽉 찼습니다. ({len(s.queue)}/{s.max_size}명)", ephemeral=True
            )

        nick   = interaction.user.display_name
        number = len(s.queue) + 1
        s.queue.append(QueueEntry(
            user=interaction.user,
            chzzk_name=nick,
            registered_at=datetime.now(),
            number=number,
        ))

        embed = discord.Embed(
            title="✅ 시참 등록 완료",
            description=f"**{number}번**으로 등록되었습니다!\n닉네임: `{nick}`",
            color=COLOR_START,
        )
        embed.set_footer(text=f"현재 대기 {len(s.queue)}/{s.max_size}명")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="시참취소", description="시청자 참여 등록을 취소합니다.")
    async def 시참취소(self, interaction: discord.Interaction):
        s     = self._get(interaction.guild_id)
        entry = s.find(interaction.user.id)

        if not entry:
            return await interaction.response.send_message(
                "❌ 등록된 내역이 없습니다.", ephemeral=True
            )

        s.queue.remove(entry)
        s.renumber()
        await interaction.response.send_message("✅ 시참 등록이 취소되었습니다.", ephemeral=True)

    @app_commands.command(name="시참확인", description="본인의 대기 번호와 현황을 확인합니다.")
    async def 시참확인(self, interaction: discord.Interaction):
        s     = self._get(interaction.guild_id)
        entry = s.find(interaction.user.id)

        if not s.active:
            return await interaction.response.send_message(
                "❌ 현재 시청자 참여가 활성화되지 않았습니다.", ephemeral=True
            )

        if not entry:
            return await interaction.response.send_message(
                f"등록되지 않았습니다. 현재 대기 중인 인원: **{len(s.queue)}명**",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="🔢 시참 대기 현황",
            description=(
                f"현재 **{entry.number}번** 대기 중\n"
                f"닉네임: `{entry.chzzk_name}`"
            ),
            color=COLOR_INFO,
        )
        embed.set_footer(text=f"전체 대기 {len(s.queue)}/{s.max_size}명")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="시참목록", description="현재 시참 대기열 전체를 확인합니다.")
    async def 시참목록(self, interaction: discord.Interaction):
        s = self._get(interaction.guild_id)

        if not s.active:
            return await interaction.response.send_message(
                "❌ 현재 시청자 참여가 활성화되지 않았습니다.", ephemeral=True
            )

        embed = discord.Embed(
            title=f"📋 시참 대기열 ({len(s.queue)}/{s.max_size}명)",
            color=COLOR_INFO,
        )

        if not s.queue:
            embed.description = "아직 등록된 시청자가 없습니다."
        else:
            lines = [
                f"**{e.number}번** | {e.user.mention} | `{e.chzzk_name}` | "
                f"{e.registered_at.strftime('%H:%M')} 등록"
                for e in s.queue
            ]
            embed.description = "\n".join(lines)

        embed.set_footer(text=f"참여 조건: {s.condition}")
        await interaction.response.send_message(embed=embed)

    # ── 관리자용 ─────────────────────────────────────────────────────────────

    @app_commands.command(name="시참시작", description="시청자 참여 모집을 시작합니다.")
    @app_commands.describe(
        최대인원="최대 참여 인원 (기본값: 5)",
        조건="참여 조건",
    )
    @app_commands.choices(조건=[
        app_commands.Choice(name="없음",   value="없음"),
        app_commands.Choice(name="팔로워", value="팔로워"),
        app_commands.Choice(name="구독자", value="구독자"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def 시참시작(
        self,
        interaction: discord.Interaction,
        최대인원: int = 5,
        조건: app_commands.Choice[str] = None,
    ):
        s           = self._get(interaction.guild_id)
        s.active    = True
        s.max_size  = max(1, 최대인원)
        s.condition = 조건.value if 조건 else "없음"
        s.queue.clear()

        embed = discord.Embed(
            title="🎮 시청자 참여가 시작되었습니다!",
            description=(
                "`/시참등록` 으로 참여하세요\n\n"
                f"**최대 인원:** {s.max_size}명\n"
                f"**참여 조건:** {s.condition}"
            ),
            color=COLOR_START,
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"시작: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="시참종료", description="시청자 참여 모집을 종료하고 대기열을 초기화합니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def 시참종료(self, interaction: discord.Interaction):
        s = self._get(interaction.guild_id)

        if not s.active:
            return await interaction.response.send_message(
                "❌ 활성화된 시참이 없습니다.", ephemeral=True
            )

        count    = len(s.queue)
        s.active = False
        s.queue.clear()

        embed = discord.Embed(
            title="🔴 시청자 참여가 종료되었습니다.",
            description=f"대기열 **{count}명**이 초기화되었습니다.",
            color=COLOR_END,
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"종료: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="시참호출", description="대기열 앞에서부터 n명을 호출합니다.")
    @app_commands.describe(인원수="호출할 인원 수 (기본값: 1)")
    @app_commands.default_permissions(manage_guild=True)
    async def 시참호출(self, interaction: discord.Interaction, 인원수: int = 1):
        s = self._get(interaction.guild_id)

        if not s.active:
            return await interaction.response.send_message(
                "❌ 현재 시청자 참여가 활성화되지 않았습니다.", ephemeral=True
            )

        if not s.queue:
            return await interaction.response.send_message(
                "❌ 대기열이 비어 있습니다.", ephemeral=True
            )

        count     = min(인원수, len(s.queue))
        called    = s.queue[:count]
        s.queue   = s.queue[count:]
        s.renumber()

        mentions  = " ".join(e.user.mention for e in called)
        details   = "\n".join(
            f"**{i+1}번** | {e.user.mention} | `{e.chzzk_name}`"
            for i, e in enumerate(called)
        )

        embed = discord.Embed(
            title=f"📢 시참 호출 ({count}명)",
            description=f"{details}\n\n{mentions} 시참 차례입니다! 🎉",
            color=COLOR_CALL,
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"남은 대기: {len(s.queue)}명")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="시참건너뛰기", description="특정 유저를 대기열에서 제거합니다 (노쇼 처리).")
    @app_commands.describe(유저="대기열에서 제거할 유저")
    @app_commands.default_permissions(manage_guild=True)
    async def 시참건너뛰기(self, interaction: discord.Interaction, 유저: discord.Member):
        s     = self._get(interaction.guild_id)
        entry = s.find(유저.id)

        if not entry:
            return await interaction.response.send_message(
                f"❌ {유저.mention}은 대기열에 없습니다.", ephemeral=True
            )

        s.queue.remove(entry)
        s.renumber()

        embed = discord.Embed(
            title="⏭️ 시참 건너뛰기",
            description=f"{유저.mention} (`{entry.chzzk_name}`)을 대기열에서 제거했습니다.",
            color=COLOR_END,
        )
        embed.set_footer(text=f"남은 대기: {len(s.queue)}명")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── 에러 핸들러 ───────────────────────────────────────────────────────────

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        err: app_commands.AppCommandError,
    ):
        if isinstance(err, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ 이 명령어는 **서버 관리** 권한이 필요합니다.", ephemeral=True
                )
        else:
            raise err


async def setup(bot: commands.Bot):
    await bot.add_cog(SichhamCog(bot))
