import time
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from utils import is_mod_or_admin, success, error


async def _handle_mission_join(interaction: discord.Interaction, mission_id: int):
    db = await get_db()
    existing = await (await db.execute(
        "SELECT status FROM mission_completions WHERE mission_id=? AND guild_id=? AND user_id=?",
        (mission_id, interaction.guild_id, interaction.user.id)
    )).fetchone()
    if existing:
        status_map = {"pending": "검토 중", "approved": "승인됨", "rejected": "거부됨"}
        return await interaction.response.send_message(
            f"이미 참가한 미션입니다. (상태: {status_map.get(existing['status'], '알 수 없음')})",
            ephemeral=True
        )
    mission = await (await db.execute(
        "SELECT title, points FROM missions WHERE id=? AND guild_id=? AND is_active=1",
        (mission_id, interaction.guild_id)
    )).fetchone()
    if not mission:
        return await interaction.response.send_message(
            "미션을 찾을 수 없거나 비활성화되었습니다.", ephemeral=True
        )
    await db.execute(
        "INSERT INTO mission_completions(mission_id, guild_id, user_id, status, submitted_at) VALUES(?,?,?,?,?)",
        (mission_id, interaction.guild_id, interaction.user.id, "pending", int(time.time()))
    )
    await db.commit()
    await interaction.response.send_message(
        f"✅ **{mission['title']}** 미션에 참가했습니다!\n"
        f"관리자 승인 후 **{mission['points']:,}** 포인트가 지급됩니다.",
        ephemeral=True
    )


class MissionButton(discord.ui.Button):
    def __init__(self, mission_id: int):
        super().__init__(
            label="미션 참가 신청",
            style=discord.ButtonStyle.green,
            custom_id=f"mission_join:{mission_id}",
            emoji="✅"
        )
        self.mission_id = mission_id

    async def callback(self, interaction: discord.Interaction):
        await _handle_mission_join(interaction, self.mission_id)


class MissionView(discord.ui.View):
    def __init__(self, mission_id: int):
        super().__init__(timeout=None)
        self.add_item(MissionButton(mission_id=mission_id))


class PointsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if not cid.startswith("mission_join:"):
            return
        # 뷰가 만료된 경우(봇 재시작 후) 여기서 처리
        if interaction.message and interaction.message.id in {v.id for v in getattr(interaction.client, '_connection', {}).get('_view_store', {}).values() if hasattr(v, 'id')}:
            return  # 뷰가 살아있으면 Button.callback이 처리
        try:
            mission_id = int(cid.split(":")[1])
        except (IndexError, ValueError):
            return
        if not interaction.response.is_done():
            await _handle_mission_join(interaction, mission_id)

    @app_commands.command(name="포인트", description="자신 또는 멤버의 포인트를 확인합니다.")
    @app_commands.describe(member="확인할 멤버 (기본: 자신)")
    async def check_points(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        db = await get_db()
        row = await (await db.execute(
            "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, target.id)
        )).fetchone()
        pts = row["points"] if row else 0
        rank_row = await (await db.execute(
            "SELECT COUNT(*)+1 AS rank FROM user_points WHERE guild_id=? AND points > ?",
            (interaction.guild_id, pts)
        )).fetchone()
        rank = rank_row["rank"] if rank_row else 1
        embed = discord.Embed(title=f"💎 {target.display_name}의 포인트", color=discord.Color.gold())
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="보유 포인트", value=f"**{pts:,}** P", inline=True)
        embed.add_field(name="서버 순위", value=f"**#{rank}**", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="포인트순위", description="서버 포인트 리더보드 상위 10명을 확인합니다.")
    async def points_rank(self, interaction: discord.Interaction):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT user_id, points FROM user_points WHERE guild_id=? ORDER BY points DESC LIMIT 10",
            (interaction.guild_id,)
        )).fetchall()
        embed = discord.Embed(title="💎 포인트 리더보드", color=discord.Color.gold())
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            m = interaction.guild.get_member(row["user_id"])
            name = m.display_name if m else f"<@{row['user_id']}>"
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            lines.append(f"{medal} **{name}** — {row['points']:,} P")
        embed.description = "\n".join(lines) if lines else "아직 포인트 데이터가 없습니다."
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="포인트지급", description="[관리자] 멤버에게 포인트를 지급/차감합니다.")
    @app_commands.describe(member="대상 멤버", amount="포인트 수 (음수=차감)", reason="사유")
    @app_commands.default_permissions(administrator=True)
    @is_mod_or_admin()
    async def give_points(self, interaction: discord.Interaction,
                          member: discord.Member, amount: int, reason: str = ""):
        db = await get_db()
        await db.execute(
            """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,MAX(0,?))
               ON CONFLICT(guild_id, user_id) DO UPDATE SET points=MAX(0, points + ?)""",
            (interaction.guild_id, member.id, max(0, amount), amount)
        )
        await db.commit()
        row = await (await db.execute(
            "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id)
        )).fetchone()
        total = row["points"] if row else 0
        sign = "+" if amount >= 0 else ""
        desc = f"{member.mention}에게 **{sign}{amount:,}** 포인트 처리\n현재 보유: **{total:,}** P"
        if reason:
            desc += f"\n사유: {reason}"
        await interaction.response.send_message(embed=success("포인트 지급", desc), ephemeral=True)

    @app_commands.command(name="미션불러오기", description="활성 미션 목록을 현재 채널에 게시합니다.")
    @app_commands.default_permissions(manage_messages=True)
    @is_mod_or_admin()
    async def post_missions(self, interaction: discord.Interaction):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT id, title, description, points FROM missions WHERE guild_id=? AND is_active=1 ORDER BY id",
            (interaction.guild_id,)
        )).fetchall()
        if not rows:
            return await interaction.response.send_message(
                embed=error("미션 없음", "활성 미션이 없습니다. 웹 대시보드 > 포인트 탭에서 미션을 등록하세요."),
                ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        for mission in rows:
            embed = discord.Embed(
                title=f"🎯 {mission['title']}",
                description=mission["description"] or "",
                color=discord.Color.blue()
            )
            embed.add_field(name="보상", value=f"**{mission['points']:,}** 포인트", inline=True)
            embed.set_footer(text="아래 버튼을 눌러 미션에 참가하세요.")
            await interaction.channel.send(embed=embed, view=MissionView(mission_id=mission["id"]))
        await interaction.followup.send(
            embed=success("미션 게시 완료", f"{len(rows)}개의 미션이 게시되었습니다."), ephemeral=True
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, err: app_commands.AppCommandError):
        if isinstance(err, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=error("권한 부족", "이 명령어를 사용할 권한이 없습니다."), ephemeral=True
                )
        else:
            raise err


async def setup(bot: commands.Bot):
    await bot.add_cog(PointsCog(bot))
