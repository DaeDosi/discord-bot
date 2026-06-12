import os
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from utils import error, success

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


# ── Persistent View (재시작 후에도 동작) ─────────────────────────────────────

class VerifyView(discord.ui.View):
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

        if row["verified_role_id"]:
            verified_role = interaction.guild.get_role(row["verified_role_id"])
            if verified_role and verified_role in member.roles:
                return await interaction.response.send_message(
                    "이미 인증된 상태입니다.", ephemeral=True
                )

        if row["unverified_role_id"]:
            unverified_role = interaction.guild.get_role(row["unverified_role_id"])
            if unverified_role and unverified_role in member.roles:
                try:
                    await member.remove_roles(unverified_role, reason="입장 인증 완료")
                except discord.Forbidden:
                    pass

        if row["verified_role_id"]:
            verified_role = interaction.guild.get_role(row["verified_role_id"])
            if verified_role:
                try:
                    await member.add_roles(verified_role, reason="입장 인증 완료")
                except discord.Forbidden:
                    return await interaction.response.send_message(
                        "봇 권한이 부족해 역할을 부여할 수 없습니다. 관리자에게 문의하세요.",
                        ephemeral=True,
                    )

        await interaction.response.send_message("✅ 인증이 완료되었습니다!", ephemeral=True)


# ── 인증 임베드 빌더 ──────────────────────────────────────────────────────────

def _build_embed_and_view(guild: discord.Guild, row: dict) -> tuple[discord.Embed, discord.ui.View]:
    msg = (row["verification_message"] or "").strip() or "아래 버튼을 눌러 입장을 확인해 주세요."
    embed = discord.Embed(
        title="🔐 입장 인증",
        description=msg,
        color=0x5865F2,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text=guild.name)

    if row["use_chzzk_verification"]:
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="치지직으로 인증하기",
            url=f"{FRONTEND_URL}/verify",
            style=discord.ButtonStyle.link,
            emoji="📺",
        ))
    else:
        view = VerifyView()

    return embed, view


# ── Cog ──────────────────────────────────────────────────────────────────────

class VerificationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── 신규 멤버 입장 이벤트 ────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        db = await get_db()
        row = await (await db.execute(
            """SELECT verification_channel, unverified_role_id,
                      use_chzzk_verification, verification_message
               FROM guild_config WHERE guild_id=?""",
            (member.guild.id,)
        )).fetchone()

        if not row or not row["verification_channel"]:
            return

        if row["unverified_role_id"]:
            role = member.guild.get_role(row["unverified_role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="신규 입장 - 미인증")
                except discord.Forbidden:
                    pass

        ch = member.guild.get_channel(row["verification_channel"])
        if not ch:
            return

        embed, view = _build_embed_and_view(member.guild, row)
        embed.description = f"{member.mention} {embed.description}"
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            await ch.send(embed=embed, view=view)
        except discord.Forbidden:
            pass

    # ── /setup ───────────────────────────────────────────────────────────────
    @app_commands.command(name="setup", description="입장 채널·미인증 역할·인증됨 역할을 초기 지정합니다.")
    @app_commands.describe(
        channel="입장 인증 메시지를 보낼 채널",
        unverified_role="신규 입장 시 자동 부여할 미인증 역할",
        verified_role="인증 완료 후 부여할 역할",
    )
    @app_commands.default_permissions(administrator=True)
    async def setup_cmd(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        unverified_role: discord.Role,
        verified_role: discord.Role,
    ):
        db = await get_db()

        # 치지직 인증 활성화 여부 + 본인 인증 확인
        row = await (await db.execute(
            "SELECT use_chzzk_verification FROM guild_config WHERE guild_id=?",
            (interaction.guild_id,)
        )).fetchone()

        if row and row["use_chzzk_verification"]:
            is_verified = await (await db.execute(
                "SELECT 1 FROM chzzk_verifications WHERE guild_id=? AND user_id=?",
                (interaction.guild_id, interaction.user.id)
            )).fetchone()

            if not is_verified:
                verify_url = (
                    f"{FRONTEND_URL}/verify"
                    f"?guild_id={interaction.guild_id}&user_id={interaction.user.id}"
                )
                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="치지직으로 인증하기",
                    url=verify_url,
                    style=discord.ButtonStyle.link,
                    emoji="📺",
                ))
                return await interaction.response.send_message(
                    f"{interaction.user.mention} `/setup` 명령어를 사용하려면 먼저 치지직 인증이 필요합니다.",
                    view=view,
                )

        await db.execute(
            """INSERT INTO guild_config
               (guild_id, verification_channel, unverified_role_id, verified_role_id)
               VALUES(?,?,?,?)
               ON CONFLICT(guild_id) DO UPDATE SET
                   verification_channel = excluded.verification_channel,
                   unverified_role_id   = excluded.unverified_role_id,
                   verified_role_id     = excluded.verified_role_id""",
            (interaction.guild_id, channel.id, unverified_role.id, verified_role.id)
        )
        await db.commit()

        await interaction.response.send_message(
            embed=success(
                "✅ 인증 설정 완료",
                f"채널: {channel.mention}\n"
                f"미인증 역할: {unverified_role.mention}\n"
                f"인증됨 역할: {verified_role.mention}\n\n"
                f"나머지 상세 설정은 웹 대시보드에서 변경하세요.",
            ),
            ephemeral=True,
        )

    # ── /embed ───────────────────────────────────────────────────────────────
    @app_commands.command(name="embed", description="입장 채널에 인증 임베드를 전송(또는 수정)합니다.")
    @app_commands.default_permissions(administrator=True)
    async def embed_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        row = await (await db.execute(
            """SELECT verification_channel, unverified_role_id, verified_role_id,
                      use_chzzk_verification, verification_message,
                      verification_embed_msg_id
               FROM guild_config WHERE guild_id=?""",
            (interaction.guild_id,)
        )).fetchone()

        if not row or not row["verification_channel"]:
            return await interaction.followup.send(
                embed=error("설정 없음", "`/setup` 명령어로 먼저 입장 채널을 지정해주세요.")
            )

        ch = interaction.guild.get_channel(row["verification_channel"])
        if not ch:
            return await interaction.followup.send(
                embed=error("채널 없음", "설정된 입장 채널을 찾을 수 없습니다. 대시보드에서 채널을 다시 지정해주세요.")
            )

        embed, view = _build_embed_and_view(interaction.guild, row)
        sent_msg: discord.Message | None = None

        # 기존 메시지가 있으면 수정, 없으면 새로 전송
        if row["verification_embed_msg_id"]:
            try:
                existing = await ch.fetch_message(row["verification_embed_msg_id"])
                await existing.edit(embed=embed, view=view)
                sent_msg = existing
            except (discord.NotFound, discord.Forbidden):
                sent_msg = None

        if sent_msg is None:
            try:
                sent_msg = await ch.send(embed=embed, view=view)
            except discord.Forbidden:
                return await interaction.followup.send(
                    embed=error("권한 없음", f"{ch.mention} 채널에 메시지를 보낼 권한이 없습니다.")
                )

        # 메시지 ID 저장
        await db.execute(
            """INSERT INTO guild_config(guild_id, verification_embed_msg_id) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET
                   verification_embed_msg_id = excluded.verification_embed_msg_id""",
            (interaction.guild_id, sent_msg.id)
        )
        await db.commit()

        await interaction.followup.send(
            embed=success("임베드 전송 완료", f"{ch.mention}에 인증 임베드를 {'수정' if row['verification_embed_msg_id'] else '전송'}했습니다.")
        )

    # ── 에러 핸들러 ───────────────────────────────────────────────────────────
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     err: app_commands.AppCommandError):
        if isinstance(err, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=error("권한 부족", "관리자만 사용 가능합니다."), ephemeral=True
                )
        else:
            raise err


async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))
