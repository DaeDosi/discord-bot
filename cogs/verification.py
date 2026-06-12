import os
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from utils import is_admin_verified, success, error

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


class VerifyView(discord.ui.View):
    """봇 재시작 후에도 동작하는 Persistent 입장 확인 버튼."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ 입장 확인",
        style=discord.ButtonStyle.success,
        custom_id="verify_simple_btn",
    )
    async def verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = await get_db()
        row = await (await db.execute(
            "SELECT unverified_role_id, verified_role_id FROM guild_config WHERE guild_id=?",
            (interaction.guild_id,)
        )).fetchone()

        if not row:
            return await interaction.response.send_message(
                "인증 설정을 찾을 수 없습니다. 관리자에게 문의하세요.", ephemeral=True
            )

        member = interaction.user
        changed = False

        if row["unverified_role_id"]:
            unverified = interaction.guild.get_role(row["unverified_role_id"])
            if unverified and unverified in member.roles:
                try:
                    await member.remove_roles(unverified, reason="입장 인증 완료")
                    changed = True
                except discord.Forbidden:
                    pass

        if row["verified_role_id"]:
            verified = interaction.guild.get_role(row["verified_role_id"])
            if verified:
                if verified in member.roles:
                    return await interaction.response.send_message(
                        "이미 인증된 상태입니다.", ephemeral=True
                    )
                try:
                    await member.add_roles(verified, reason="입장 인증 완료")
                    changed = True
                except discord.Forbidden:
                    return await interaction.response.send_message(
                        "봇 권한이 부족해 역할을 부여할 수 없습니다. 관리자에게 문의하세요.",
                        ephemeral=True,
                    )

        if changed:
            await interaction.response.send_message("✅ 인증이 완료되었습니다!", ephemeral=True)
        else:
            await interaction.response.send_message(
                "역할이 설정되어 있지 않습니다. 관리자에게 문의하세요.", ephemeral=True
            )


class VerificationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── 신규 멤버 입장 이벤트 ────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        db = await get_db()
        row = await (await db.execute(
            """SELECT verification_channel, unverified_role_id, verified_role_id,
                      use_chzzk_verification, verification_message
               FROM guild_config WHERE guild_id=?""",
            (member.guild.id,)
        )).fetchone()

        if not row or not row["verification_channel"]:
            return

        # 미인증 역할 자동 부여
        if row["unverified_role_id"]:
            unverified = member.guild.get_role(row["unverified_role_id"])
            if unverified:
                try:
                    await member.add_roles(unverified, reason="신규 입장 - 미인증")
                except discord.Forbidden:
                    pass

        ch = member.guild.get_channel(row["verification_channel"])
        if not ch:
            return

        msg = row["verification_message"] or f"**{member.guild.name}**에 오신 것을 환영합니다!"
        embed = discord.Embed(
            title="🔐 입장 인증",
            description=f"{member.mention}님, {msg}",
            color=0x5865F2,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"멤버 #{member.guild.member_count}")

        if row["use_chzzk_verification"]:
            verify_url = (
                f"{FRONTEND_URL}/verify"
                f"?guild_id={member.guild.id}&user_id={member.id}"
            )
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="치지직으로 인증하기",
                url=verify_url,
                style=discord.ButtonStyle.link,
                emoji="📺",
            ))
        else:
            view = VerifyView()

        try:
            await ch.send(embed=embed, view=view)
        except discord.Forbidden:
            pass

    # ── /인증설정 ─────────────────────────────────────────────────────────────
    @app_commands.command(name="인증설정", description="입장 인증 채널 및 역할을 설정합니다.")
    @app_commands.describe(
        verification_channel="입장 인증 메시지를 보낼 채널",
        unverified_role="신규 입장 시 부여할 미인증 역할",
        verified_role="인증 완료 후 부여할 역할",
        message="입장 임베드에 표시할 커스텀 메시지 (선택)",
    )
    @is_admin_verified()
    async def setup_verification(
        self,
        interaction: discord.Interaction,
        verification_channel: discord.TextChannel,
        unverified_role: discord.Role,
        verified_role: discord.Role,
        message: str = "",
    ):
        db = await get_db()
        await db.execute(
            """INSERT INTO guild_config(guild_id, verification_channel, unverified_role_id,
                                        verified_role_id, verification_message)
               VALUES(?,?,?,?,?)
               ON CONFLICT(guild_id) DO UPDATE SET
                   verification_channel  = excluded.verification_channel,
                   unverified_role_id    = excluded.unverified_role_id,
                   verified_role_id      = excluded.verified_role_id,
                   verification_message  = excluded.verification_message""",
            (interaction.guild_id, verification_channel.id,
             unverified_role.id, verified_role.id, message)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success(
                "인증 설정 완료",
                f"채널: {verification_channel.mention}\n"
                f"미인증 역할: {unverified_role.mention}\n"
                f"인증됨 역할: {verified_role.mention}",
            ),
            ephemeral=True,
        )

    # ── /치지직인증토글 ───────────────────────────────────────────────────────
    @app_commands.command(name="치지직인증토글", description="신규 멤버의 치지직 인증 연동 ON/OFF를 전환합니다.")
    @is_admin_verified()
    async def toggle_chzzk_verification(self, interaction: discord.Interaction):
        db = await get_db()
        row = await (await db.execute(
            "SELECT use_chzzk_verification FROM guild_config WHERE guild_id=?",
            (interaction.guild_id,)
        )).fetchone()

        current = bool(row["use_chzzk_verification"]) if row else False
        new_val = 0 if current else 1

        await db.execute(
            """INSERT INTO guild_config(guild_id, use_chzzk_verification) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET
                   use_chzzk_verification = excluded.use_chzzk_verification""",
            (interaction.guild_id, new_val)
        )
        await db.commit()

        status = "✅ 활성화" if new_val else "❌ 비활성화"
        await interaction.response.send_message(
            embed=success("치지직 인증 전환", f"치지직 인증이 **{status}**되었습니다."),
            ephemeral=True,
        )

    # ── 에러 핸들러 ───────────────────────────────────────────────────────────
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     err: app_commands.AppCommandError):
        if isinstance(err, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=error("권한 부족", "관리자만 사용 가능합니다."), ephemeral=True
                )
        elif isinstance(err, app_commands.CheckFailure):
            pass  # 프레디케이트가 이미 응답을 보냈음
        else:
            raise err


async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))
