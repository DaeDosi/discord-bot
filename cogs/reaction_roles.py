import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from utils import is_admin, success, error


class ReactionRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /reactionrole add ─────────────────────────────────────────────────────
    @app_commands.command(name="reactionrole", description="특정 메시지에 반응 역할을 추가합니다.")
    @app_commands.describe(
        message_id="대상 메시지 ID",
        emoji="사용할 이모지",
        role="부여할 역할"
    )
    @is_admin()
    async def reactionrole(self, interaction: discord.Interaction,
                            message_id: str, emoji: str, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await interaction.channel.fetch_message(int(message_id))
        except (ValueError, discord.NotFound):
            return await interaction.followup.send(
                embed=error("오류", "메시지를 찾을 수 없습니다. 같은 채널에서 실행해주세요.")
            )

        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            return await interaction.followup.send(
                embed=error("오류", "유효하지 않은 이모지입니다.")
            )

        db = await get_db()
        await db.execute(
            """INSERT INTO reaction_roles(guild_id, message_id, emoji, role_id) VALUES(?,?,?,?)
               ON CONFLICT(guild_id, message_id, emoji) DO UPDATE SET role_id=excluded.role_id""",
            (interaction.guild_id, int(message_id), emoji, role.id)
        )
        await db.commit()
        await interaction.followup.send(
            embed=success("반응 역할 추가", f"{emoji} 반응 시 {role.mention} 역할이 부여됩니다.")
        )

    # ── /reactionrole-remove ──────────────────────────────────────────────────
    @app_commands.command(name="reactionrole-remove", description="메시지의 반응 역할을 제거합니다.")
    @app_commands.describe(message_id="대상 메시지 ID", emoji="제거할 이모지")
    @is_admin()
    async def reactionrole_remove(self, interaction: discord.Interaction,
                                   message_id: str, emoji: str):
        db = await get_db()
        await db.execute(
            "DELETE FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
            (interaction.guild_id, int(message_id), emoji)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("반응 역할 제거", f"메시지의 {emoji} 반응 역할이 제거되었습니다."), ephemeral=True
        )

    # ── /reactionrole-list ────────────────────────────────────────────────────
    @app_commands.command(name="reactionrole-list", description="서버의 반응 역할 목록을 확인합니다.")
    @is_admin()
    async def reactionrole_list(self, interaction: discord.Interaction):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT message_id, emoji, role_id FROM reaction_roles WHERE guild_id=?",
            (interaction.guild_id,)
        )).fetchall()

        embed = discord.Embed(title="🎭 반응 역할 목록", color=discord.Color.blurple())
        if rows:
            lines = [
                f"{r['emoji']} → <@&{r['role_id']}> (메시지 ID: `{r['message_id']}`)"
                for r in rows
            ]
            embed.description = "\n".join(lines)
        else:
            embed.description = "등록된 반응 역할이 없습니다."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── 반응 추가 이벤트 ──────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        db = await get_db()
        emoji_str = str(payload.emoji)
        row = await (await db.execute(
            "SELECT role_id FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
            (payload.guild_id, payload.message_id, emoji_str)
        )).fetchone()
        if not row:
            return

        member = guild.get_member(payload.user_id)
        if member:
            role = guild.get_role(row["role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="반응 역할 자동 부여")
                except discord.Forbidden:
                    pass

    # ── 반응 제거 이벤트 ──────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        db = await get_db()
        emoji_str = str(payload.emoji)
        row = await (await db.execute(
            "SELECT role_id FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?",
            (payload.guild_id, payload.message_id, emoji_str)
        )).fetchone()
        if not row:
            return

        member = guild.get_member(payload.user_id)
        if member:
            role = guild.get_role(row["role_id"])
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="반응 역할 자동 회수")
                except discord.Forbidden:
                    pass

    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     err: app_commands.AppCommandError):
        if isinstance(err, app_commands.MissingPermissions):
            await interaction.response.send_message(
                embed=error("권한 부족", "관리자만 사용 가능합니다."), ephemeral=True
            )
        else:
            raise err


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRolesCog(bot))
