import time
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import get_db
from utils import is_mod_or_admin, mod_log, error, success

WARN_MUTE_THRESHOLD = 3      # 경고 누적 시 자동 뮤트
AUTO_MUTE_DURATION  = 600    # 자동 뮤트 지속 시간(초) = 10분
MAX_MENTIONS        = 5      # 멘션 도배 임계값
SPAM_WINDOW         = 5      # 스팸 감지 시간 창(초)
SPAM_COUNT          = 5      # 시간 창 내 동일 메시지 반복 임계값


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # {(guild_id, user_id): [timestamp, ...]}
        self._msg_cache: dict[tuple, list[tuple[float, str]]] = {}
        self.check_unmutes.start()
        self.cleanup_msg_cache.start()

    def cog_unload(self):
        self.check_unmutes.cancel()
        self.cleanup_msg_cache.cancel()

    # ── 로그 헬퍼 ────────────────────────────────────────────────────────────
    async def send_log(self, guild: discord.Guild, embed: discord.Embed):
        db = await get_db()
        row = await (await db.execute(
            "SELECT log_channel FROM guild_config WHERE guild_id=?", (guild.id,)
        )).fetchone()
        if row and row["log_channel"]:
            ch = guild.get_channel(row["log_channel"])
            if ch:
                await ch.send(embed=embed)

    # ── 뮤트 타임아웃 헬퍼 ──────────────────────────────────────────────────
    async def apply_timeout(self, member: discord.Member, duration_sec: int, reason: str):
        import datetime
        until = discord.utils.utcnow() + datetime.timedelta(seconds=duration_sec)
        await member.timeout(until, reason=reason)
        db = await get_db()
        await db.execute(
            """INSERT INTO mutes(guild_id, user_id, unmute_at) VALUES(?,?,?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET unmute_at=excluded.unmute_at""",
            (member.guild.id, member.id, time.time() + duration_sec)
        )
        await db.commit()

    # ── /kick ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="kick", description="멤버를 서버에서 추방합니다.")
    @app_commands.describe(member="대상 멤버", reason="사유")
    @is_mod_or_admin()
    async def kick(self, interaction: discord.Interaction,
                   member: discord.Member, reason: str = "사유 없음"):
        if member.top_role >= interaction.user.top_role and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                embed=error("권한 부족", "자신보다 높거나 동등한 역할의 멤버는 추방할 수 없습니다."), ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:
            return await interaction.followup.send(embed=error("오류", "봇 권한이 부족합니다."))

        embed = mod_log("킥(Kick)", member, interaction.user, reason, discord.Color.orange())
        await self.send_log(interaction.guild, embed)
        await interaction.followup.send(
            embed=success("추방 완료", f"{member} 님이 추방되었습니다.\n사유: {reason}")
        )

    # ── /ban ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="ban", description="멤버를 서버에서 영구 차단합니다.")
    @app_commands.describe(member="대상 멤버", reason="사유", delete_days="삭제할 메시지 기간(일, 0-7)")
    @is_mod_or_admin()
    async def ban(self, interaction: discord.Interaction, member: discord.Member,
                  reason: str = "사유 없음",
                  delete_days: app_commands.Range[int, 0, 7] = 1):
        await interaction.response.defer(ephemeral=True)
        try:
            await member.ban(reason=reason, delete_message_days=delete_days)
        except discord.Forbidden:
            return await interaction.followup.send(embed=error("오류", "봇 권한이 부족합니다."))

        embed = mod_log("밴(Ban)", member, interaction.user, reason, discord.Color.red())
        await self.send_log(interaction.guild, embed)
        await interaction.followup.send(
            embed=success("차단 완료", f"{member} 님이 차단되었습니다.\n사유: {reason}")
        )

    # ── /unban ────────────────────────────────────────────────────────────────
    @app_commands.command(name="unban", description="차단된 유저를 해제합니다.")
    @app_commands.describe(user_id="차단 해제할 유저 ID", reason="사유")
    @is_mod_or_admin()
    async def unban(self, interaction: discord.Interaction,
                    user_id: str, reason: str = "사유 없음"):
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason=reason)
        except (ValueError, discord.NotFound):
            return await interaction.followup.send(embed=error("오류", "유효하지 않은 유저 ID입니다."))
        except discord.Forbidden:
            return await interaction.followup.send(embed=error("오류", "봇 권한이 부족합니다."))

        await interaction.followup.send(
            embed=success("차단 해제", f"<@{user_id}> 님의 차단이 해제되었습니다.")
        )

    # ── /mute ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="mute", description="멤버에게 타임아웃을 적용합니다.")
    @app_commands.describe(member="대상 멤버", duration="지속 시간(분)", reason="사유")
    @is_mod_or_admin()
    async def mute(self, interaction: discord.Interaction, member: discord.Member,
                   duration: app_commands.Range[int, 1, 40320] = 10,
                   reason: str = "사유 없음"):
        await interaction.response.defer(ephemeral=True)
        try:
            await self.apply_timeout(member, duration * 60, reason)
        except discord.Forbidden:
            return await interaction.followup.send(embed=error("오류", "봇 권한이 부족합니다."))

        embed = mod_log("뮤트(Mute)", member, interaction.user,
                        f"{reason} ({duration}분)", discord.Color.yellow())
        await self.send_log(interaction.guild, embed)
        await interaction.followup.send(
            embed=success("뮤트 완료", f"{member.mention} 님이 {duration}분 동안 뮤트되었습니다.")
        )

    # ── /unmute ───────────────────────────────────────────────────────────────
    @app_commands.command(name="unmute", description="멤버의 타임아웃을 해제합니다.")
    @app_commands.describe(member="대상 멤버")
    @is_mod_or_admin()
    async def unmute(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        try:
            await member.timeout(None, reason=f"뮤트 해제 by {interaction.user}")
        except discord.Forbidden:
            return await interaction.followup.send(embed=error("오류", "봇 권한이 부족합니다."))

        db = await get_db()
        await db.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?",
                         (interaction.guild_id, member.id))
        await db.commit()
        await interaction.followup.send(
            embed=success("뮤트 해제", f"{member.mention} 님의 뮤트가 해제되었습니다.")
        )

    # ── /warn ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="warn", description="멤버에게 경고를 부여합니다.")
    @app_commands.describe(member="대상 멤버", reason="사유")
    @is_mod_or_admin()
    async def warn(self, interaction: discord.Interaction,
                   member: discord.Member, reason: str = "사유 없음"):
        await interaction.response.defer(ephemeral=True)
        db = await get_db()
        await db.execute(
            "INSERT INTO warnings(guild_id, user_id, mod_id, reason, created_at) VALUES(?,?,?,?,?)",
            (interaction.guild_id, member.id, interaction.user.id, reason, time.time())
        )
        await db.commit()

        count_row = await (await db.execute(
            "SELECT COUNT(*) AS cnt FROM warnings WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id)
        )).fetchone()
        warn_count = count_row["cnt"]

        embed = mod_log("경고(Warn)", member, interaction.user,
                        f"{reason} (누적 경고: {warn_count}회)", discord.Color.yellow())
        await self.send_log(interaction.guild, embed)

        # 경고 누적 자동 뮤트
        if warn_count >= WARN_MUTE_THRESHOLD:
            try:
                await self.apply_timeout(member, AUTO_MUTE_DURATION,
                                         f"경고 {warn_count}회 누적 자동 뮤트")
                auto_embed = mod_log(
                    f"자동 뮤트 ({WARN_MUTE_THRESHOLD}회 경고)", member, self.bot.user,
                    f"경고 누적 {warn_count}회 달성 ({AUTO_MUTE_DURATION//60}분 뮤트)",
                    discord.Color.dark_red()
                )
                await self.send_log(interaction.guild, auto_embed)
            except discord.Forbidden:
                pass

        await interaction.followup.send(
            embed=success("경고 부여", f"{member.mention} 님에게 경고가 부여되었습니다. (누적 {warn_count}회)")
        )

    # ── /warnings ─────────────────────────────────────────────────────────────
    @app_commands.command(name="warnings", description="멤버의 경고 내역을 확인합니다.")
    @app_commands.describe(member="대상 멤버")
    @is_mod_or_admin()
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT reason, created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC LIMIT 10",
            (interaction.guild_id, member.id)
        )).fetchall()

        embed = discord.Embed(
            title=f"⚠️ {member.display_name}의 경고 내역",
            color=discord.Color.yellow()
        )
        if rows:
            for i, row in enumerate(rows, 1):
                ts = int(row["created_at"])
                embed.add_field(
                    name=f"#{i}",
                    value=f"{row['reason']}\n<t:{ts}:R>",
                    inline=False
                )
        else:
            embed.description = "경고 내역이 없습니다."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /clearwarns ───────────────────────────────────────────────────────────
    @app_commands.command(name="clearwarns", description="멤버의 경고를 모두 초기화합니다.")
    @app_commands.describe(member="대상 멤버")
    @app_commands.default_permissions(administrator=True)
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member):
        db = await get_db()
        await db.execute(
            "DELETE FROM warnings WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("경고 초기화", f"{member.mention}의 경고가 모두 삭제되었습니다."), ephemeral=True
        )

    # ── /purge ─────────────────────────────────────────────────────────────────
    @app_commands.command(name="purge", description="채널의 메시지를 일괄 삭제합니다.")
    @app_commands.describe(amount="삭제할 메시지 수 (최대 100)")
    @is_mod_or_admin()
    async def purge(self, interaction: discord.Interaction,
                    amount: app_commands.Range[int, 1, 100]):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(
            embed=success("메시지 삭제", f"{len(deleted)}개의 메시지가 삭제되었습니다."),
            ephemeral=True
        )

    # ── Auto-Mod ────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.author.guild_permissions.administrator:
            return

        db = await get_db()
        row = await (await db.execute(
            "SELECT automod_enabled, badwords FROM guild_config WHERE guild_id=?",
            (message.guild.id,)
        )).fetchone()

        if row and not row["automod_enabled"]:
            return

        # 1. 금지어 필터링
        if row and row["badwords"]:
            content_lower = message.content.lower()
            for word in row["badwords"].split(","):
                if word and word.strip() in content_lower:
                    try:
                        await message.delete()
                    except discord.NotFound:
                        pass
                    await self._auto_warn(message, f"금지어 사용: '{word.strip()}'")
                    return

        # 2. 멘션 도배 감지
        if len(message.mentions) + len(message.role_mentions) >= MAX_MENTIONS:
            try:
                await message.delete()
            except discord.NotFound:
                pass
            await self._auto_warn(message, f"멘션 도배 ({len(message.mentions)}회)")
            return

        # 3. 동일 메시지 반복 스팸 감지
        key = (message.guild.id, message.author.id)
        now = time.time()
        history = self._msg_cache.setdefault(key, [])
        history.append((now, message.content))
        # 오래된 항목 제거
        self._msg_cache[key] = [(t, c) for t, c in history if now - t < SPAM_WINDOW]

        same = sum(1 for _, c in self._msg_cache[key] if c == message.content)
        if same >= SPAM_COUNT:
            self._msg_cache[key] = []
            # 해당 메시지들 삭제
            def is_spam(m: discord.Message):
                return m.author == message.author and m.content == message.content
            try:
                await message.channel.purge(limit=20, check=is_spam)
            except discord.Forbidden:
                pass
            await self._auto_warn(message, "동일 메시지 반복 도배")

    async def _auto_warn(self, message: discord.Message, reason: str):
        db = await get_db()
        await db.execute(
            "INSERT INTO warnings(guild_id, user_id, mod_id, reason, created_at) VALUES(?,?,?,?,?)",
            (message.guild.id, message.author.id, self.bot.user.id, f"[자동] {reason}", time.time())
        )
        await db.commit()

        count_row = await (await db.execute(
            "SELECT COUNT(*) AS cnt FROM warnings WHERE guild_id=? AND user_id=?",
            (message.guild.id, message.author.id)
        )).fetchone()
        warn_count = count_row["cnt"]

        embed = mod_log("자동 관리", message.author, self.bot.user,
                        f"[자동] {reason} (누적 경고: {warn_count}회)", discord.Color.dark_orange())
        await self.send_log(message.guild, embed)

        try:
            await message.channel.send(
                f"{message.author.mention} ⚠️ 규정 위반이 감지되었습니다: **{reason}**",
                delete_after=5
            )
        except discord.Forbidden:
            pass

        if warn_count >= WARN_MUTE_THRESHOLD:
            try:
                await self.apply_timeout(message.author, AUTO_MUTE_DURATION,
                                         f"경고 {warn_count}회 누적 자동 뮤트")
            except discord.Forbidden:
                pass

    # ── 뮤트 자동 해제 루프 ────────────────────────────────────────────────────
    @tasks.loop(seconds=30)
    async def check_unmutes(self):
        db = await get_db()
        now = time.time()
        rows = await (await db.execute(
            "SELECT guild_id, user_id FROM mutes WHERE unmute_at <= ?", (now,)
        )).fetchall()
        for row in rows:
            guild = self.bot.get_guild(row["guild_id"])
            if guild:
                member = guild.get_member(row["user_id"])
                if member and member.is_timed_out():
                    try:
                        await member.timeout(None, reason="뮤트 시간 만료 자동 해제")
                    except discord.Forbidden:
                        pass
            await db.execute("DELETE FROM mutes WHERE guild_id=? AND user_id=?",
                             (row["guild_id"], row["user_id"]))
        if rows:
            await db.commit()

    @check_unmutes.before_loop
    async def before_check_unmutes(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def cleanup_msg_cache(self):
        """스팸 감지 캐시에서 1시간 이상 메시지가 없는 키를 정리합니다."""
        now = time.time()
        stale = [
            k for k, msgs in self._msg_cache.items()
            if not msgs or now - max(t for t, _ in msgs) > 3600
        ]
        for k in stale:
            del self._msg_cache[k]

    @cleanup_msg_cache.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # ── 오류 핸들러 ───────────────────────────────────────────────────────────
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     err: app_commands.AppCommandError):
        if isinstance(err, app_commands.MissingPermissions):
            msg = "이 명령어를 사용할 권한이 없습니다."
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=error("권한 부족", msg), ephemeral=True)
            else:
                await interaction.followup.send(embed=error("권한 부족", msg), ephemeral=True)
        else:
            raise err


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
