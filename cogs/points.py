import json
import time
import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import get_db
from utils import is_mod_or_admin, success, error
from utils.gambling import resolve_gambling_winner, calc_gambling_payout

# discord.Poll 최대 진행 시간 (32일 = 768시간, Discord API 상한)
MAX_POLL_HOURS = 768
# 정산되지 않은 도박 라운드를 주기적으로 확인하는 간격 (poll_result 이벤트를 놓쳤을 때의 안전망)
RECONCILE_INTERVAL_SECONDS = 300


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

    cost = item["points_cost"]

    # 잔액 확인과 차감을 단일 원자적 UPDATE로 처리 — 연타/동시 요청으로 인한
    # 잔액 부족 상태의 중복 구매(TOCTOU 레이스)를 방지한다.
    cur = await db.execute(
        "UPDATE user_points SET points = points - ? WHERE guild_id=? AND user_id=? AND points >= ?",
        (cost, interaction.guild_id, interaction.user.id, cost)
    )
    if cur.rowcount == 0:
        pts_row = await (await db.execute(
            "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, interaction.user.id)
        )).fetchone()
        current_pts = pts_row["points"] if pts_row else 0
        if current_pts < cost:
            await db.commit()
            return await interaction.response.send_message(
                f"포인트가 부족합니다. (보유: **{current_pts:,}** P / 필요: **{cost:,}** P)",
                ephemeral=True
            )
        # cost가 0인데 포인트 기록 자체가 없던 유저 — 기록만 생성
        await db.execute(
            "INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,0)",
            (interaction.guild_id, interaction.user.id)
        )

    if item["stock"] != -1:
        sold = await (await db.execute(
            "SELECT COUNT(*) AS cnt FROM shop_exchanges WHERE item_id=? AND guild_id=?",
            (item_id, interaction.guild_id)
        )).fetchone()
        if sold and sold["cnt"] >= item["stock"]:
            # 재고 마감 — 방금 차감한 포인트 환불 후 취소
            await db.execute(
                "UPDATE user_points SET points = points + ? WHERE guild_id=? AND user_id=?",
                (cost, interaction.guild_id, interaction.user.id)
            )
            await db.commit()
            return await interaction.response.send_message("품절된 아이템입니다.", ephemeral=True)

    await db.execute(
        "INSERT INTO shop_exchanges(guild_id, user_id, item_id, exchanged_at) VALUES(?,?,?,?)",
        (interaction.guild_id, interaction.user.id, item_id, int(time.time()))
    )
    await db.commit()

    row = await (await db.execute(
        "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
        (interaction.guild_id, interaction.user.id)
    )).fetchone()
    remaining = row["points"] if row else 0

    await interaction.response.send_message(
        f"✅ **{item['name']}** 교환 완료!\n"
        f"사용한 포인트: **{cost:,}** P\n"
        f"남은 포인트: **{remaining:,}** P",
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
        self.reconcile_gambling_loop.start()

    def cog_unload(self):
        self.reconcile_gambling_loop.cancel()

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

    # ── 포인트 도박 (discord.Poll 기반) ──────────────────────────────────────
    #
    # 승자는 득표(베팅) 최다 옵션으로 자동 결정되며, 정산은 베팅 시점이 아니라
    # 라운드 종료 시점의 잔액을 기준으로 이뤄진다 (discord.Poll은 투표를 사전에
    # 막을 방법이 없으므로, 잔액이 부족한 참가자는 정산에서 조용히 제외된다).

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.type != discord.MessageType.poll_result:
            return
        ref = message.reference
        if not ref or not ref.message_id:
            return
        await self._settle_poll_session(ref.message_id, message.channel)

    @tasks.loop(seconds=RECONCILE_INTERVAL_SECONDS)
    async def reconcile_gambling_loop(self):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT message_id, channel_id FROM points_poll_sessions WHERE settled=0"
        )).fetchall()
        for row in rows:
            channel = self.bot.get_channel(row["channel_id"])
            if not channel:
                continue
            try:
                message = await channel.fetch_message(row["message_id"])
            except (discord.NotFound, discord.Forbidden):
                continue
            except Exception:
                continue
            if message.poll and message.poll.is_finalized():
                await self._settle_poll_session(row["message_id"], channel)

    @reconcile_gambling_loop.before_loop
    async def before_reconcile_gambling_loop(self):
        await self.bot.wait_until_ready()

    async def _settle_poll_session(self, message_id: int, channel: discord.abc.Messageable):
        db = await get_db()
        row = await (await db.execute(
            "SELECT * FROM points_poll_sessions WHERE message_id=? AND settled=0",
            (message_id,)
        )).fetchone()
        if not row:
            return
        # 동시 실행(정산 중복) 방지를 위해 먼저 settled 플래그부터 세운다.
        await db.execute("UPDATE points_poll_sessions SET settled=1 WHERE id=?", (row["id"],))
        await db.commit()

        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            return
        poll = message.poll
        if not poll:
            return

        guild_id   = row["guild_id"]
        bet_amount = row["bet_amount"]
        answers    = sorted(poll.answers, key=lambda a: a.id)

        # 옵션별 투표자 수집 (봇 제외, 중복 방지)
        voters_by_answer: dict[int, list[int]] = {}
        seen: set[int] = set()
        for answer in answers:
            uids = []
            async for u in answer.voters():
                if u.bot or u.id in seen:
                    continue
                seen.add(u.id)
                uids.append(u.id)
            voters_by_answer[answer.id] = uids

        winner_id = resolve_gambling_winner(
            {a.id: a.vote_count for a in answers},
            discord_poll_victor=poll.victor_answer_id if poll.total_votes > 0 else None,
        )

        # 정산 시점 잔액 기준으로 베팅 차감
        charged_by_answer: dict[int, list[int]] = {a.id: [] for a in answers}
        for answer_id, uids in voters_by_answer.items():
            for uid in uids:
                pts_row = await (await db.execute(
                    "SELECT points FROM user_points WHERE guild_id=? AND user_id=?",
                    (guild_id, uid)
                )).fetchone()
                current = pts_row["points"] if pts_row else 0
                if current < bet_amount:
                    continue
                await db.execute(
                    "UPDATE user_points SET points = points - ? WHERE guild_id=? AND user_id=?",
                    (bet_amount, guild_id, uid)
                )
                charged_by_answer[answer_id].append(uid)

        winners       = charged_by_answer.get(winner_id, []) if winner_id is not None else []
        total_charged = sum(len(v) for v in charged_by_answer.values())
        payout        = calc_gambling_payout(winners, total_charged, bet_amount)

        if winners:
            for uid in winners:
                await db.execute(
                    """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,?)
                       ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points+?""",
                    (guild_id, uid, payout, payout)
                )
        else:
            # 승자 없음(참여 0명 또는 승자 옵션에 유효 참가자 없음) → 차감된 전원 환불
            for uids in charged_by_answer.values():
                for uid in uids:
                    await db.execute(
                        """INSERT INTO user_points(guild_id, user_id, points) VALUES(?,?,?)
                           ON CONFLICT(guild_id, user_id) DO UPDATE SET points=points+?""",
                        (guild_id, uid, bet_amount, bet_amount)
                    )
        await db.commit()

        embed = discord.Embed(title="🏆 포인트 도박 결과", color=discord.Color.gold())
        if winner_id is None:
            embed.description = "참여자가 없어 정산할 내용이 없습니다."
        else:
            winner_answer = next(a for a in answers if a.id == winner_id)
            if winners:
                embed.description = (
                    f"🎊 당첨: **{winner_answer.text}**\n"
                    f"당첨자 **{len(winners)}명**, 각 **+{payout:,}P** 지급!"
                )
            else:
                embed.description = (
                    f"🎰 **{winner_answer.text}**가 최다 득표했지만 유효 참가자가 없어 "
                    "베팅 포인트가 전원 환불되었습니다."
                )
        for answer in answers:
            cnt = len(charged_by_answer.get(answer.id, []))
            mark = "✅" if answer.id == winner_id and winners else "❌"
            embed.add_field(
                name=answer.text,
                value=f"{mark} **{cnt}명** | **{cnt * bet_amount:,}P**",
                inline=False,
            )
        embed.add_field(name="총 베팅 풀", value=f"**{pool:,}P**", inline=True)
        embed.add_field(name="참가자",     value=f"**{total_charged}명**", inline=True)
        embed.set_footer(text="NexBot • nexbot.shop")
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    @app_commands.command(name="포인트도박", description="[관리자] 포인트 도박을 시작합니다.")
    @app_commands.default_permissions(administrator=True)
    @is_mod_or_admin()
    async def start_gambling(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        db = await get_db()

        existing = await (await db.execute(
            "SELECT id FROM points_poll_sessions WHERE guild_id=? AND settled=0",
            (guild_id,)
        )).fetchone()
        if existing:
            return await interaction.response.send_message(
                embed=error("진행 중", "이미 진행 중인 도박이 있습니다. `/포인트도박종료`로 먼저 종료하세요."),
                ephemeral=True,
            )

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

        options    = [r["content"] for r in opts][:10]  # discord.Poll 최대 10개 옵션
        bet_amount = cfg["bet_amount"]
        hours      = max(1, min(MAX_POLL_HOURS, int(cfg["duration"])))
        title      = cfg["title"]

        poll = discord.Poll(question=title, duration=datetime.timedelta(hours=hours))
        for opt in options:
            poll.add_answer(text=opt)

        await interaction.response.send_message("🎰 포인트 도박을 시작합니다!", ephemeral=True)
        msg = await interaction.channel.send(poll=poll)

        await db.execute(
            """INSERT INTO points_poll_sessions(guild_id, channel_id, message_id, bet_amount, options, settled, created_at)
               VALUES(?,?,?,?,?,0,?)""",
            (guild_id, interaction.channel_id, msg.id, bet_amount, json.dumps(options), int(time.time()))
        )
        await db.commit()

    @app_commands.command(name="포인트도박종료", description="[관리자] 진행 중인 포인트 도박을 조기 종료합니다.")
    @app_commands.default_permissions(administrator=True)
    @is_mod_or_admin()
    async def end_gambling(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        db = await get_db()
        row = await (await db.execute(
            "SELECT channel_id, message_id FROM points_poll_sessions WHERE guild_id=? AND settled=0",
            (guild_id,)
        )).fetchone()
        if not row:
            return await interaction.response.send_message(
                embed=error("없음", "현재 진행 중인 도박이 없습니다."), ephemeral=True
            )

        channel = interaction.guild.get_channel(row["channel_id"])
        if not channel:
            return await interaction.response.send_message(
                embed=error("오류", "도박이 게시된 채널을 찾을 수 없습니다."), ephemeral=True
            )

        try:
            message = await channel.fetch_message(row["message_id"])
            await message.end_poll()
        except Exception as e:
            return await interaction.response.send_message(
                embed=error("종료 실패", f"투표 종료 중 오류가 발생했습니다: {e}"), ephemeral=True
            )

        await interaction.response.send_message(
            "✅ 도박을 종료합니다. 잠시 후 정산 결과가 게시됩니다.", ephemeral=True
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
