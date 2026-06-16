import time
import random
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from utils import is_mod_or_admin, success, error


# ── 포인트 도박 세션 ──────────────────────────────────────────────────────────

_NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


class GamblingSession:
    def __init__(self, message: discord.Message, view: "GamblingView",
                 title: str, options: list[str], bet_amount: int, duration: int):
        self.message    = message
        self.view       = view
        self.title      = title
        self.options    = options
        self.bet_amount = bet_amount
        self.end_time   = time.time() + duration
        self.votes: dict[int, int] = {}  # user_id -> opt_index (0-based)
        self.task: asyncio.Task | None = None
        self.ended      = False


def _build_gambling_embed(session: GamblingSession, ended=False,
                          winner_idx: int | None = None, winner_payout: int = 0) -> discord.Embed:
    total = len(session.votes)
    pool  = total * session.bet_amount
    counts = [0] * len(session.options)
    for v in session.votes.values():
        counts[v] += 1

    if ended:
        embed = discord.Embed(title=f"🏆 {session.title} — 결과", color=discord.Color.gold())
        if winner_idx is not None:
            embed.description = f"🎊 당첨: **{_NUMBER_EMOJIS[winner_idx]} {session.options[winner_idx]}**"
        for i, (opt, cnt) in enumerate(zip(session.options, counts)):
            em   = _NUMBER_EMOJIS[i] if i < len(_NUMBER_EMOJIS) else str(i + 1)
            pct  = f"{cnt / total * 100:.0f}%" if total else "0%"
            pts  = cnt * session.bet_amount
            if i == winner_idx:
                val = f"✅ **{cnt}명** ({pct}) | **{pts:,}P** → 각 **+{winner_payout:,}P**"
            else:
                val = f"❌ **{cnt}명** ({pct}) | **{pts:,}P**"
            embed.add_field(name=f"{em} {opt}", value=val, inline=False)
        embed.add_field(name="총 베팅 풀", value=f"**{pool:,}P**", inline=True)
        embed.add_field(name="참가자",     value=f"**{total}명**", inline=True)
        if winner_idx is not None and counts[winner_idx] == 0:
            embed.add_field(name="⚠️ 당첨자 없음", value="전원 베팅 포인트 환불됨", inline=False)
        embed.set_footer(text="도박 종료")
    else:
        remaining = max(0, int(session.end_time - time.time()))
        embed = discord.Embed(
            title=f"🎰 {session.title}",
            description=f"베팅 금액: **{session.bet_amount:,}P** | 총 풀: **{pool:,}P** | 참가: **{total}명**",
            color=discord.Color.blurple(),
        )
        for i, (opt, cnt) in enumerate(zip(session.options, counts)):
            em  = _NUMBER_EMOJIS[i] if i < len(_NUMBER_EMOJIS) else str(i + 1)
            pct = f"{cnt / total * 100:.0f}%" if total else "0%"
            embed.add_field(
                name=f"{em} {opt}",
                value=f"**{cnt}명** ({pct}) | **{cnt * session.bet_amount:,}P**",
                inline=False,
            )
        embed.set_footer(text=f"⏰ 남은 시간: {remaining}초 | 버튼을 눌러 베팅하세요")
    return embed


class GamblingButton(discord.ui.Button):
    def __init__(self, opt_index: int, content: str):
        em = _NUMBER_EMOJIS[opt_index] if opt_index < len(_NUMBER_EMOJIS) else str(opt_index + 1)
        super().__init__(
            label=content[:60],
            style=discord.ButtonStyle.primary,
            custom_id=f"gb_opt_{opt_index}",
            emoji=em,
            row=opt_index // 3,
        )
        self.opt_index = opt_index

    async def callback(self, interaction: discord.Interaction):
        cog: "PointsCog | None" = interaction.client.cogs.get("PointsCog")  # type: ignore
        session = cog.active_gambling.get(interaction.guild_id) if cog else None

        if not session or session.ended:
            return await interaction.response.send_message("현재 진행 중인 도박이 없습니다.", ephemeral=True)
        if interaction.user.id in session.votes:
            return await interaction.response.send_message("이미 베팅했습니다.", ephemeral=True)

        db = await get_db()
        pts_row = await (await db.execute(
            "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, interaction.user.id)
        )).fetchone()
        current = pts_row["points"] if pts_row else 0
        if current < session.bet_amount:
            return await interaction.response.send_message(
                f"포인트가 부족합니다. (보유: **{current:,}P** / 필요: **{session.bet_amount:,}P**)",
                ephemeral=True,
            )

        await db.execute(
            "UPDATE user_points SET points = MAX(0, points - ?) WHERE guild_id=? AND user_id=?",
            (session.bet_amount, interaction.guild_id, interaction.user.id)
        )
        await db.commit()
        session.votes[interaction.user.id] = self.opt_index
        opt_name = session.options[self.opt_index]
        await interaction.response.send_message(
            f"✅ **{opt_name}**에 **{session.bet_amount:,}P** 베팅 완료!", ephemeral=True
        )


class GamblingView(discord.ui.View):
    def __init__(self, options: list[str]):
        super().__init__(timeout=None)
        for i, content in enumerate(options):
            self.add_item(GamblingButton(opt_index=i, content=content))


async def _handle_shop_buy(interaction: discord.Interaction, item_id: int):
    db = await get_db()
    item = await (await db.execute(
        "SELECT * FROM shop_items WHERE id=? AND guild_id=? AND is_active=1",
        (item_id, interaction.guild_id)
    )).fetchone()
    if not item:
        return await interaction.response.send_message("아이템을 찾을 수 없거나 비활성화되었습니다.", ephemeral=True)

    if item["stock"] != -1:
        sold = await (await db.execute(
            "SELECT COUNT(*) AS cnt FROM shop_exchanges WHERE item_id=? AND guild_id=?",
            (item_id, interaction.guild_id)
        )).fetchone()
        if sold and sold["cnt"] >= item["stock"]:
            return await interaction.response.send_message("품절된 아이템입니다.", ephemeral=True)

    pts_row = await (await db.execute(
        "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
        (interaction.guild_id, interaction.user.id)
    )).fetchone()
    current_pts = pts_row["points"] if pts_row else 0

    if current_pts < item["points_cost"]:
        return await interaction.response.send_message(
            f"포인트가 부족합니다. (보유: **{current_pts:,}** P / 필요: **{item['points_cost']:,}** P)",
            ephemeral=True
        )

    await db.execute(
        "UPDATE user_points SET points = MAX(0, points - ?) WHERE guild_id=? AND user_id=?",
        (item["points_cost"], interaction.guild_id, interaction.user.id)
    )
    await db.execute(
        "INSERT INTO shop_exchanges(guild_id, user_id, item_id, exchanged_at) VALUES(?,?,?,?)",
        (interaction.guild_id, interaction.user.id, item_id, int(time.time()))
    )
    await db.commit()
    await interaction.response.send_message(
        f"✅ **{item['name']}** 교환 완료!\n"
        f"사용한 포인트: **{item['points_cost']:,}** P\n"
        f"남은 포인트: **{current_pts - item['points_cost']:,}** P",
        ephemeral=True
    )


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
        "INSERT OR IGNORE INTO mission_completions(mission_id, guild_id, user_id, status, submitted_at) VALUES(?,?,?,?,?)",
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


class ShopBuyButton(discord.ui.Button):
    def __init__(self, item_id: int, points_cost: int):
        super().__init__(
            label=f"교환하기 ({points_cost:,}P)",
            style=discord.ButtonStyle.blurple,
            custom_id=f"shop_buy:{item_id}",
            emoji="🛒"
        )
        self.item_id = item_id

    async def callback(self, interaction: discord.Interaction):
        await _handle_shop_buy(interaction, self.item_id)


class ShopView(discord.ui.View):
    def __init__(self, item_id: int, points_cost: int):
        super().__init__(timeout=None)
        self.add_item(ShopBuyButton(item_id=item_id, points_cost=points_cost))


class PointsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_gambling: dict[int, GamblingSession] = {}  # guild_id -> session

    @commands.Cog.listener()
    async def on_ready(self):
        db = await get_db()
        missions = await (await db.execute("SELECT id FROM missions")).fetchall()
        for m in missions:
            self.bot.add_view(MissionView(mission_id=m["id"]))
        items = await (await db.execute("SELECT id, points_cost FROM shop_items")).fetchall()
        for item in items:
            self.bot.add_view(ShopView(item_id=item["id"], points_cost=item["points_cost"]))

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

    @app_commands.command(name="포인트상점", description="포인트 상점에서 아이템을 확인하고 교환합니다.")
    async def point_shop(self, interaction: discord.Interaction):
        db = await get_db()
        items = await (await db.execute(
            "SELECT * FROM shop_items WHERE guild_id=? AND is_active=1 ORDER BY points_cost ASC",
            (interaction.guild_id,)
        )).fetchall()
        if not items:
            return await interaction.response.send_message(
                "현재 상점에 등록된 아이템이 없습니다. 웹 대시보드 > 포인트 탭에서 아이템을 등록하세요.",
                ephemeral=True
            )
        pts_row = await (await db.execute(
            "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, interaction.user.id)
        )).fetchone()
        current_pts = pts_row["points"] if pts_row else 0
        await interaction.response.defer()
        for item in items:
            embed = discord.Embed(title=f"🛒 {item['name']}", color=discord.Color.gold())
            if item["description"]:
                embed.description = item["description"]
            embed.add_field(name="필요 포인트", value=f"**{item['points_cost']:,}** P", inline=True)
            stock_val = "무제한" if item["stock"] == -1 else f"**{item['stock']}**개"
            embed.add_field(name="재고", value=stock_val, inline=True)
            if item["image_url"]:
                embed.set_thumbnail(url=item["image_url"])
            await interaction.channel.send(
                embed=embed,
                view=ShopView(item_id=item["id"], points_cost=item["points_cost"])
            )
        await interaction.followup.send(
            f"💎 내 보유 포인트: **{current_pts:,}** P", ephemeral=True
        )

    # ── 포인트 도박 내부 메서드 ──────────────────────────────────────────────

    async def _gambling_update_loop(self, guild_id: int):
        while True:
            await asyncio.sleep(5)
            session = self.active_gambling.get(guild_id)
            if not session or session.ended:
                return
            if time.time() >= session.end_time:
                await self._end_gambling(guild_id, winner_idx=None)
                return
            try:
                embed = _build_gambling_embed(session)
                await session.message.edit(embed=embed)
            except Exception:
                pass

    async def _end_gambling(self, guild_id: int, winner_idx: int | None):
        session = self.active_gambling.pop(guild_id, None)
        if not session or session.ended:
            return
        session.ended = True
        if session.task:
            session.task.cancel()

        for item in session.view.children:
            item.disabled = True  # type: ignore

        total   = len(session.votes)
        pool    = total * session.bet_amount
        counts  = [0] * len(session.options)
        for v in session.votes.values():
            counts[v] += 1

        if winner_idx is None:
            winner_idx = random.randint(0, len(session.options) - 1)

        winner_count   = counts[winner_idx]
        winner_payout  = pool // winner_count if winner_count > 0 else 0
        winner_users   = [uid for uid, opt in session.votes.items() if opt == winner_idx]

        db = await get_db()
        if winner_count == 0:
            # 당첨자 없음 → 전원 환불
            for uid in session.votes:
                await db.execute(
                    """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,?)
                       ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points+?""",
                    (guild_id, uid, session.bet_amount, session.bet_amount)
                )
        else:
            for uid in winner_users:
                await db.execute(
                    """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,?)
                       ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points+?""",
                    (guild_id, uid, winner_payout, winner_payout)
                )
        await db.commit()

        embed = _build_gambling_embed(session, ended=True, winner_idx=winner_idx, winner_payout=winner_payout)
        try:
            await session.message.edit(embed=embed, view=session.view)
        except Exception:
            pass

    # ── 도박 슬래시 명령어 ────────────────────────────────────────────────────

    @app_commands.command(name="포인트도박", description="[관리자] 포인트 도박을 시작합니다.")
    @app_commands.default_permissions(administrator=True)
    @is_mod_or_admin()
    async def start_gambling(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if guild_id in self.active_gambling:
            return await interaction.response.send_message(
                embed=error("진행 중", "이미 진행 중인 도박이 있습니다. `/포인트도박종료`로 먼저 종료하세요."),
                ephemeral=True,
            )

        db = await get_db()
        cfg = await (await db.execute(
            "SELECT title, duration, bet_amount FROM points_gambling_config WHERE guild_id=?",
            (guild_id,)
        )).fetchone()
        opts = await (await db.execute(
            "SELECT content FROM points_gambling_options WHERE guild_id=? ORDER BY opt_index",
            (guild_id,)
        )).fetchall()

        if not cfg or not opts or len(opts) < 2:
            return await interaction.response.send_message(
                embed=error("설정 없음", "도박 설정이 없습니다. 웹 대시보드 > 포인트 > 포인트 도박 탭에서 설정하세요."),
                ephemeral=True,
            )

        options    = [r["content"] for r in opts]
        bet_amount = cfg["bet_amount"]
        duration   = cfg["duration"]
        title      = cfg["title"]

        view    = GamblingView(options)
        session = GamblingSession(
            message=None, view=view, title=title,
            options=options, bet_amount=bet_amount, duration=duration,
        )
        embed = _build_gambling_embed(session)
        await interaction.response.send_message("🎰 포인트 도박을 시작합니다!", ephemeral=True)
        msg = await interaction.channel.send(embed=embed, view=view)
        session.message = msg
        self.active_gambling[guild_id] = session
        session.task = asyncio.create_task(self._gambling_update_loop(guild_id))

    @app_commands.command(name="포인트도박종료", description="[관리자] 진행 중인 포인트 도박을 종료합니다.")
    @app_commands.describe(번호="당첨 옵션 번호 (1-based). 미입력 시 랜덤 추첨")
    @app_commands.default_permissions(administrator=True)
    @is_mod_or_admin()
    async def end_gambling(self, interaction: discord.Interaction, 번호: int | None = None):
        guild_id = interaction.guild_id
        if guild_id not in self.active_gambling:
            return await interaction.response.send_message(
                embed=error("없음", "현재 진행 중인 도박이 없습니다."), ephemeral=True
            )
        session = self.active_gambling[guild_id]
        n_opts  = len(session.options)

        if 번호 is not None:
            if not (1 <= 번호 <= n_opts):
                return await interaction.response.send_message(
                    embed=error("범위 오류", f"번호는 1 ~ {n_opts} 사이여야 합니다."), ephemeral=True
                )
            winner_idx = 번호 - 1
        else:
            winner_idx = None

        await interaction.response.send_message("✅ 도박을 종료합니다.", ephemeral=True)
        await self._end_gambling(guild_id, winner_idx=winner_idx)

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
