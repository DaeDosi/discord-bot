import discord
from discord import app_commands
from database import get_db


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
