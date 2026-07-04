import math
import time
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db

XP_PER_MSG = 15          # 메시지당 지급 XP
XP_COOLDOWN = 60         # 초 단위 쿨타임


def xp_for_level(level: int) -> int:
    """레벨 달성에 필요한 누적 XP (MEE6 근사 공식)."""
    return 5 * (level ** 2) + 50 * level + 100


def level_from_xp(xp: int) -> int:
    level = 0
    while xp >= xp_for_level(level):
        xp -= xp_for_level(level)
        level += 1
    return level


def xp_progress(total_xp: int) -> tuple[int, int, int]:
    """(current_level, xp_in_level, xp_needed_for_next) 반환."""
    level = 0
    remaining = total_xp
    while remaining >= xp_for_level(level):
        remaining -= xp_for_level(level)
        level += 1
    return level, remaining, xp_for_level(level)


class LevelingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── XP 획득 이벤트 ────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        db = await get_db()
        now = time.time()

        row = await (await db.execute(
            "SELECT xp, level, last_xp_ts FROM user_xp WHERE guild_id=? AND user_id=?",
            (message.guild.id, message.author.id)
        )).fetchone()

        last_ts = row["last_xp_ts"] if row else 0
        if now - last_ts < XP_COOLDOWN:
            return

        old_xp = row["xp"] if row else 0
        new_xp = old_xp + XP_PER_MSG
        old_level, _, _ = xp_progress(old_xp)
        new_level, xp_in_level, xp_needed = xp_progress(new_xp)

        await db.execute(
            """INSERT INTO user_xp(guild_id, user_id, xp, level, last_xp_ts)
               VALUES(?,?,?,?,?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   xp=excluded.xp, level=excluded.level, last_xp_ts=excluded.last_xp_ts""",
            (message.guild.id, message.author.id, new_xp, new_level, now)
        )
        await db.commit()

        if new_level > old_level:
            await self._on_level_up(message.guild, message.author, new_level, xp_in_level, xp_needed,
                                     fallback_channel=message.channel)
            await self._award_level_points(message.guild.id, message.author.id, new_level - old_level)

    # ── 외부(치지직 출석체크 등)에서 애정도 XP를 지급할 때 사용 ──────────────────
    async def add_xp(self, guild: discord.Guild, user_id: int, amount: int):
        if amount <= 0:
            return
        db = await get_db()
        row = await (await db.execute(
            "SELECT xp, level FROM user_xp WHERE guild_id=? AND user_id=?",
            (guild.id, user_id)
        )).fetchone()
        old_xp = row["xp"] if row else 0
        old_level = row["level"] if row else 0
        new_xp = old_xp + amount
        new_level, xp_in_level, xp_needed = xp_progress(new_xp)

        await db.execute(
            """INSERT INTO user_xp(guild_id, user_id, xp, level, last_xp_ts)
               VALUES(?,?,?,?,0)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   xp=excluded.xp, level=excluded.level""",
            (guild.id, user_id, new_xp, new_level)
        )
        await db.commit()

        if new_level > old_level:
            member = guild.get_member(user_id)
            if member:
                await self._on_level_up(guild, member, new_level, xp_in_level, xp_needed)
            await self._award_level_points(guild.id, user_id, new_level - old_level)

    async def _award_level_points(self, guild_id: int, user_id: int, levels_gained: int):
        db = await get_db()
        cfg = await (await db.execute(
            "SELECT points_per_level FROM guild_config WHERE guild_id=?", (guild_id,)
        )).fetchone()
        per_level = int(cfg["points_per_level"] or 0) if cfg else 0
        if per_level <= 0:
            return
        pts = per_level * levels_gained
        await db.execute(
            """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points + ?""",
            (guild_id, user_id, pts, pts)
        )
        await db.commit()

    async def _on_level_up(self, guild: discord.Guild, member: discord.Member, new_level: int,
                            xp_in_level: int, xp_needed: int, fallback_channel=None):
        db = await get_db()

        # 애정도 레벨 보상 역할 부여
        rewards = await (await db.execute(
            "SELECT role_id FROM level_rewards WHERE guild_id=? AND level<=? ORDER BY level DESC",
            (guild.id, new_level)
        )).fetchall()
        for r in rewards:
            role = guild.get_role(r["role_id"])
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"애정도 레벨 {new_level} 달성 보상")
                except discord.Forbidden:
                    pass

        # 레벨업 알림 채널 결정
        cfg = await (await db.execute(
            "SELECT levelup_channel, levelup_dm FROM guild_config WHERE guild_id=?",
            (guild.id,)
        )).fetchone()

        embed = discord.Embed(
            title="🎉 애정도 상승!",
            description=(
                f"{member.mention}님의 **애정도 레벨**이 **{new_level}**이 되었습니다!\n"
                f"다음 레벨까지: {xp_in_level}/{xp_needed} 애정도"
            ),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        if cfg and cfg["levelup_dm"]:
            try:
                await member.send(embed=embed)
            except discord.Forbidden:
                pass
            return

        target_ch = None
        if cfg and cfg["levelup_channel"]:
            target_ch = guild.get_channel(cfg["levelup_channel"])
        if not target_ch:
            target_ch = fallback_channel
        if not target_ch:
            return

        await target_ch.send(embed=embed)

    # ── /rank ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="랭크", description="자신(또는 다른 멤버)의 애정도 레벨과 경험치를 확인합니다.")
    @app_commands.describe(member="확인할 멤버 (기본: 자신)")
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        db = await get_db()
        row = await (await db.execute(
            "SELECT xp FROM user_xp WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, target.id)
        )).fetchone()

        total_xp = row["xp"] if row else 0
        level, xp_in_level, xp_needed = xp_progress(total_xp)

        # 서버 랭킹 순위
        rank_row = await (await db.execute(
            """SELECT COUNT(*)+1 AS rank FROM user_xp
               WHERE guild_id=? AND xp > ?""",
            (interaction.guild_id, total_xp)
        )).fetchone()
        rank_num = rank_row["rank"] if rank_row else 1

        bar_filled = int((xp_in_level / xp_needed) * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)

        embed = discord.Embed(
            title=f"📊 {target.display_name}의 애정도",
            color=discord.Color.blurple()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="애정도 레벨", value=str(level), inline=True)
        embed.add_field(name="서버 순위", value=f"#{rank_num}", inline=True)
        embed.add_field(name="총 애정도(XP)", value=str(total_xp), inline=True)
        embed.add_field(
            name=f"다음 레벨까지 ({xp_in_level}/{xp_needed})",
            value=f"`{bar}` {xp_in_level}/{xp_needed}",
            inline=False
        )
        await interaction.response.send_message(embed=embed)

    # ── /leaderboard ──────────────────────────────────────────────────────────
    @app_commands.command(name="리더보드", description="서버 애정도 랭킹 상위 10명을 확인합니다.")
    async def leaderboard(self, interaction: discord.Interaction):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT user_id, xp FROM user_xp WHERE guild_id=? ORDER BY xp DESC LIMIT 10",
            (interaction.guild_id,)
        )).fetchall()

        embed = discord.Embed(title="🏆 애정도 리더보드", color=discord.Color.gold())
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            member = interaction.guild.get_member(row["user_id"])
            name = member.display_name if member else f"<@{row['user_id']}>"
            level, xp_in, xp_need = xp_progress(row["xp"])
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            lines.append(f"{medal} **{name}** — 애정도 레벨 {level} ({row['xp']:,})")

        embed.description = "\n".join(lines) if lines else "아직 데이터가 없습니다."
        await interaction.response.send_message(embed=embed)

    # ── /set-xp ───────────────────────────────────────────────────────────────
    @app_commands.command(name="xp설정", description="[관리자] 특정 멤버의 애정도 경험치를 설정합니다.")
    @app_commands.describe(member="대상 멤버", xp="설정할 애정도 경험치")
    @app_commands.default_permissions(administrator=True)
    async def set_xp(self, interaction: discord.Interaction,
                     member: discord.Member, xp: app_commands.Range[int, 0]):
        level, _, _ = xp_progress(xp)
        db = await get_db()
        await db.execute(
            """INSERT INTO user_xp(guild_id, user_id, xp, level, last_xp_ts)
               VALUES(?,?,?,?,0)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                   xp=excluded.xp, level=excluded.level""",
            (interaction.guild_id, member.id, xp, level)
        )
        await db.commit()
        embed = discord.Embed(
            description=f"{member.mention}의 애정도가 **{xp:,}** (레벨 {level})으로 설정되었습니다.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LevelingCog(bot))
