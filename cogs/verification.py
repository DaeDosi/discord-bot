import os
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from utils import error, success

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

_DEFAULT_COLOR = 0x5865F2
_DEFAULT_TITLE = "🔐 입장 인증"


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

        member     = interaction.user
        bot_member = interaction.guild.me

        unverified_role = interaction.guild.get_role(row["unverified_role_id"]) if row["unverified_role_id"] else None
        verified_role   = interaction.guild.get_role(row["verified_role_id"])   if row["verified_role_id"]   else None

        if verified_role and verified_role in member.roles:
            return await interaction.response.send_message(
                "이미 인증된 상태입니다.", ephemeral=True
            )

        if not bot_member.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                "⚠️ 봇에 **역할 관리** 권한이 없습니다.\n"
                "서버 설정 > 역할에서 봇 역할의 **역할 관리** 권한을 활성화해주세요.",
                ephemeral=True,
            )

        if verified_role and bot_member.top_role <= verified_role:
            return await interaction.response.send_message(
                f"⚠️ 봇의 역할이 **{verified_role.name}** 보다 낮습니다.\n"
                "서버 설정 > 역할 목록에서 봇 역할을 인증 역할보다 **위로** 이동해주세요.",
                ephemeral=True,
            )

        if unverified_role and unverified_role in member.roles:
            try:
                await member.remove_roles(unverified_role, reason="입장 인증 완료")
            except discord.Forbidden:
                pass

        if verified_role:
            try:
                await member.add_roles(verified_role, reason="입장 인증 완료")
            except discord.Forbidden:
                return await interaction.response.send_message(
                    "⚠️ 역할 부여에 실패했습니다.\n"
                    "봇 역할이 인증 역할보다 위에 있는지 서버 관리자에게 확인을 요청하세요.",
                    ephemeral=True,
                )

        await interaction.response.send_message("✅ 인증이 완료되었습니다!", ephemeral=True)


# ── 인증 임베드 빌더 ──────────────────────────────────────────────────────────

def _parse_color(color_str: str | None) -> int:
    if not color_str:
        return _DEFAULT_COLOR
    try:
        return int(color_str.lstrip("#"), 16)
    except (ValueError, AttributeError):
        return _DEFAULT_COLOR


def _build_embed_and_view(guild: discord.Guild, row) -> tuple[discord.Embed, discord.ui.View]:
    row   = dict(row)  # sqlite3.Row → dict (supports .get())
    msg   = (row.get("verification_message") or "").strip() or "아래 버튼을 눌러 입장을 확인해 주세요."
    title = (row.get("embed_title") or "").strip() or _DEFAULT_TITLE
    color = _parse_color(row.get("embed_color"))

    embed = discord.Embed(
        title=title,
        description=msg,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text=guild.name)

    if row.get("use_chzzk_verification"):
        verify_url = f"{FRONTEND_URL}/verify?guild_id={guild.id}"
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="인증하기",
            url=verify_url,
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
                      use_chzzk_verification, verification_message,
                      embed_color, embed_title
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

    # ── /입장메시지설정 ──────────────────────────────────────────────────────
    @app_commands.command(
        name="입장메시지설정",
        description="웹 대시보드 설정값으로 입장 인증 임베드를 채널에 전송(또는 수정)합니다.",
    )
    @app_commands.default_permissions(administrator=True)
    async def 입장메시지설정_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        row = await (await db.execute(
            """SELECT verification_channel, unverified_role_id, verified_role_id,
                      use_chzzk_verification, verification_message,
                      verification_embed_msg_id, embed_color, embed_title
               FROM guild_config WHERE guild_id=?""",
            (interaction.guild_id,)
        )).fetchone()

        if not row or not row["verification_channel"]:
            return await interaction.followup.send(
                embed=error("설정 없음", "웹 대시보드 > **입장 인증**에서 먼저 채널을 설정해주세요.")
            )

        ch = interaction.guild.get_channel(row["verification_channel"])
        if not ch:
            return await interaction.followup.send(
                embed=error("채널 없음", "설정된 입장 채널을 찾을 수 없습니다. 대시보드에서 채널을 다시 지정해주세요.")
            )

        embed, view = _build_embed_and_view(interaction.guild, row)
        sent_msg: discord.Message | None = None

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

        await db.execute(
            """INSERT INTO guild_config(guild_id, verification_embed_msg_id) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET
                   verification_embed_msg_id = excluded.verification_embed_msg_id""",
            (interaction.guild_id, sent_msg.id)
        )
        await db.commit()

        action = "수정" if row["verification_embed_msg_id"] else "전송"
        await interaction.followup.send(
            embed=success("임베드 전송 완료", f"{ch.mention}에 인증 임베드를 {action}했습니다.")
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
