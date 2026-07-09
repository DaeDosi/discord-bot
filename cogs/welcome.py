import discord
from discord.ext import commands
from database import get_db

_DEFAULT_WELCOME = "{mention}님이 **{server}**에 오셨습니다!\n\n서버의 규칙을 꼭 읽어주세요 😊"
_DEFAULT_GOODBYE = "**{username}**님이 서버를 떠났습니다."


def _fmt(template: str, member: discord.Member) -> str:
    return (template
            .replace("{mention}", member.mention)
            .replace("{username}", str(member))
            .replace("{server}", member.guild.name))


class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        db = await get_db()
        row = await (await db.execute(
            "SELECT welcome_channel, auto_role_id, welcome_message FROM guild_config WHERE guild_id=?",
            (member.guild.id,)
        )).fetchone()
        if not row:
            return

        if row["auto_role_id"]:
            role = member.guild.get_role(row["auto_role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="자동 역할 부여")
                except discord.Forbidden:
                    pass

        if row["welcome_channel"]:
            ch = member.guild.get_channel(row["welcome_channel"])
            if ch:
                msg = (row["welcome_message"] or "").strip() or _DEFAULT_WELCOME
                embed = discord.Embed(
                    title="👋 새로운 멤버 환영!",
                    description=_fmt(msg, member),
                    color=discord.Color.green(),
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"멤버 #{member.guild.member_count}")
                await ch.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        db = await get_db()
        await db.execute(
            "DELETE FROM chzzk_verifications WHERE guild_id=? AND user_id=?",
            (member.guild.id, member.id)
        )
        await db.commit()

        row = await (await db.execute(
            "SELECT goodbye_channel, goodbye_message FROM guild_config WHERE guild_id=?",
            (member.guild.id,)
        )).fetchone()
        if not row or not row["goodbye_channel"]:
            return

        ch = member.guild.get_channel(row["goodbye_channel"])
        if ch:
            msg = (row["goodbye_message"] or "").strip() or _DEFAULT_GOODBYE
            embed = discord.Embed(
                title="👋 멤버 퇴장",
                description=_fmt(msg, member),
                color=discord.Color.red(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"현재 멤버 수: {member.guild.member_count}명")
            await ch.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
