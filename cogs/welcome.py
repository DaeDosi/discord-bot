import discord
from discord.ext import commands
from database import get_db


class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        db = await get_db()
        row = await (await db.execute(
            "SELECT welcome_channel, auto_role_id FROM guild_config WHERE guild_id=?",
            (member.guild.id,)
        )).fetchone()
        if not row:
            return

        # 자동 역할 부여
        if row["auto_role_id"]:
            role = member.guild.get_role(row["auto_role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="자동 역할 부여")
                except discord.Forbidden:
                    pass

        # 환영 메시지
        if row["welcome_channel"]:
            ch = member.guild.get_channel(row["welcome_channel"])
            if ch:
                embed = discord.Embed(
                    title="👋 새로운 멤버 환영!",
                    description=(
                        f"{member.mention}님이 **{member.guild.name}**에 오셨습니다!\n\n"
                        f"서버의 규칙을 꼭 읽어주세요 😊"
                    ),
                    color=discord.Color.green()
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"멤버 #{member.guild.member_count}")
                await ch.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        db = await get_db()
        row = await (await db.execute(
            "SELECT goodbye_channel FROM guild_config WHERE guild_id=?",
            (member.guild.id,)
        )).fetchone()
        if not row or not row["goodbye_channel"]:
            return

        ch = member.guild.get_channel(row["goodbye_channel"])
        if ch:
            embed = discord.Embed(
                title="👋 멤버 퇴장",
                description=f"**{member}**님이 서버를 떠났습니다.",
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"현재 멤버 수: {member.guild.member_count}명")
            await ch.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
