import os
import discord
from discord import app_commands
from database import get_db

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


def is_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        raise app_commands.MissingPermissions(["administrator"])
    return app_commands.check(predicate)


def is_mod_or_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        db = await get_db()
        row = await db.execute(
            "SELECT mod_role_id FROM guild_config WHERE guild_id=?",
            (interaction.guild_id,)
        )
        cfg = await row.fetchone()
        if cfg and cfg["mod_role_id"]:
            mod_role = interaction.guild.get_role(cfg["mod_role_id"])
            if mod_role and mod_role in interaction.user.roles:
                return True
        raise app_commands.MissingPermissions(["manage_messages"])
    return app_commands.check(predicate)


def is_admin_verified():
    """관리자 권한 + 치지직 인증(서버 설정 시) 복합 체크."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.administrator:
            raise app_commands.MissingPermissions(["administrator"])

        db = await get_db()
        row = await (await db.execute(
            "SELECT use_chzzk_verification FROM guild_config WHERE guild_id=?",
            (interaction.guild_id,)
        )).fetchone()

        if not (row and row["use_chzzk_verification"]):
            return True

        verified = await (await db.execute(
            "SELECT 1 FROM chzzk_verifications WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, interaction.user.id)
        )).fetchone()

        if verified:
            return True

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
        embed = discord.Embed(
            title="⚠️ 치지직 인증 필요",
            description="이 명령어를 사용하려면 먼저 치지직 인증을 완료해야 합니다.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        raise app_commands.CheckFailure("chzzk_required")
    return app_commands.check(predicate)
