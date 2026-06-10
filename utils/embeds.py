import discord
from datetime import datetime


def success(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(title=f"✅ {title}", description=description,
                         color=discord.Color.green(), timestamp=datetime.utcnow())


def error(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(title=f"❌ {title}", description=description,
                         color=discord.Color.red(), timestamp=datetime.utcnow())


def info(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(title=f"ℹ️ {title}", description=description,
                         color=discord.Color.blurple(), timestamp=datetime.utcnow())


def mod_log(action: str, target: discord.Member, moderator: discord.Member,
            reason: str, color: discord.Color = discord.Color.orange()) -> discord.Embed:
    embed = discord.Embed(title=f"🔨 {action}", color=color, timestamp=datetime.utcnow())
    embed.add_field(name="대상", value=f"{target.mention} (`{target.id}`)", inline=True)
    embed.add_field(name="처리자", value=f"{moderator.mention}", inline=True)
    embed.add_field(name="사유", value=reason, inline=False)
    embed.set_footer(text=f"User ID: {target.id}")
    return embed
