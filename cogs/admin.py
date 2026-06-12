import discord
from discord import app_commands
from discord.ext import commands
from database import get_db
from utils import is_admin, success, error, info


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /set-modrole ────────────────────────────────────────────────────────
    @app_commands.command(name="중재자역할설정", description="봇 중재 명령어를 사용할 역할을 지정합니다.")
    @app_commands.describe(role="중재자(Moderator) 역할")
    @is_admin()
    async def set_modrole(self, interaction: discord.Interaction, role: discord.Role):
        db = await get_db()
        await db.execute(
            """INSERT INTO guild_config(guild_id, mod_role_id) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET mod_role_id=excluded.mod_role_id""",
            (interaction.guild_id, role.id)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("중재자 역할 설정 완료", f"{role.mention} 역할이 중재자로 지정되었습니다."),
            ephemeral=True
        )

    # ── /set-welcome ────────────────────────────────────────────────────────
    @app_commands.command(name="환영채널설정", description="환영 메시지를 보낼 채널을 설정합니다.")
    @app_commands.describe(channel="환영 메시지 채널")
    @is_admin()
    async def set_welcome(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db = await get_db()
        await db.execute(
            """INSERT INTO guild_config(guild_id, welcome_channel) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET welcome_channel=excluded.welcome_channel""",
            (interaction.guild_id, channel.id)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("환영 채널 설정 완료", f"{channel.mention}에 환영 메시지가 전송됩니다."),
            ephemeral=True
        )

    # ── /set-goodbye ────────────────────────────────────────────────────────
    @app_commands.command(name="퇴장채널설정", description="퇴장 메시지를 보낼 채널을 설정합니다.")
    @app_commands.describe(channel="퇴장 메시지 채널")
    @is_admin()
    async def set_goodbye(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db = await get_db()
        await db.execute(
            """INSERT INTO guild_config(guild_id, goodbye_channel) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET goodbye_channel=excluded.goodbye_channel""",
            (interaction.guild_id, channel.id)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("퇴장 채널 설정 완료", f"{channel.mention}에 퇴장 메시지가 전송됩니다."),
            ephemeral=True
        )

    # ── /set-logchannel ──────────────────────────────────────────────────────
    @app_commands.command(name="로그채널설정", description="중재 로그를 기록할 채널을 설정합니다.")
    @app_commands.describe(channel="로그 채널")
    @is_admin()
    async def set_logchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        db = await get_db()
        await db.execute(
            """INSERT INTO guild_config(guild_id, log_channel) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET log_channel=excluded.log_channel""",
            (interaction.guild_id, channel.id)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("로그 채널 설정 완료", f"{channel.mention}에 중재 로그가 기록됩니다."),
            ephemeral=True
        )

    # ── /set-autorole ────────────────────────────────────────────────────────
    @app_commands.command(name="자동역할설정", description="신규 가입자에게 자동 부여할 역할을 설정합니다.")
    @app_commands.describe(role="자동 부여 역할")
    @is_admin()
    async def set_autorole(self, interaction: discord.Interaction, role: discord.Role):
        db = await get_db()
        await db.execute(
            """INSERT INTO guild_config(guild_id, auto_role_id) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET auto_role_id=excluded.auto_role_id""",
            (interaction.guild_id, role.id)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("자동 역할 설정 완료", f"신규 가입자에게 {role.mention} 역할이 부여됩니다."),
            ephemeral=True
        )

    # ── /set-levelup-channel ─────────────────────────────────────────────────
    @app_commands.command(name="레벨업채널설정", description="레벨업 알림 채널을 설정합니다. (채널 없음=현재 채널)")
    @app_commands.describe(channel="레벨업 알림 채널 (비워두면 현재 채널)")
    @is_admin()
    async def set_levelup_channel(self, interaction: discord.Interaction,
                                  channel: discord.TextChannel | None = None):
        ch_id = channel.id if channel else None
        db = await get_db()
        await db.execute(
            """INSERT INTO guild_config(guild_id, levelup_channel) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET levelup_channel=excluded.levelup_channel""",
            (interaction.guild_id, ch_id)
        )
        await db.commit()
        desc = f"{channel.mention}에 레벨업 알림이 전송됩니다." if channel else "레벨업 알림이 현재 채널에 전송됩니다."
        await interaction.response.send_message(embed=success("레벨업 채널 설정", desc), ephemeral=True)

    # ── /add-level-reward ────────────────────────────────────────────────────
    @app_commands.command(name="레벨보상추가", description="특정 레벨 달성 시 부여할 역할을 설정합니다.")
    @app_commands.describe(level="달성 레벨", role="부여할 역할")
    @is_admin()
    async def add_level_reward(self, interaction: discord.Interaction,
                                level: app_commands.Range[int, 1, 500], role: discord.Role):
        db = await get_db()
        await db.execute(
            """INSERT INTO level_rewards(guild_id, level, role_id) VALUES(?,?,?)
               ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id""",
            (interaction.guild_id, level, role.id)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("레벨 보상 추가", f"레벨 **{level}** 달성 시 {role.mention} 역할이 부여됩니다."),
            ephemeral=True
        )

    # ── /remove-level-reward ─────────────────────────────────────────────────
    @app_commands.command(name="레벨보상제거", description="레벨 보상 역할을 제거합니다.")
    @app_commands.describe(level="제거할 레벨")
    @is_admin()
    async def remove_level_reward(self, interaction: discord.Interaction,
                                   level: app_commands.Range[int, 1, 500]):
        db = await get_db()
        await db.execute(
            "DELETE FROM level_rewards WHERE guild_id=? AND level=?",
            (interaction.guild_id, level)
        )
        await db.commit()
        await interaction.response.send_message(
            embed=success("레벨 보상 제거", f"레벨 **{level}** 보상이 제거되었습니다."), ephemeral=True
        )

    # ── /set-badwords ────────────────────────────────────────────────────────
    @app_commands.command(name="금지어설정", description="자동 필터링할 금지어를 설정합니다. (쉼표 구분)")
    @app_commands.describe(words="금지어 목록 (예: 욕설1,욕설2)")
    @is_admin()
    async def set_badwords(self, interaction: discord.Interaction, words: str):
        cleaned = ",".join(w.strip() for w in words.split(",") if w.strip())
        db = await get_db()
        await db.execute(
            """INSERT INTO guild_config(guild_id, badwords) VALUES(?,?)
               ON CONFLICT(guild_id) DO UPDATE SET badwords=excluded.badwords""",
            (interaction.guild_id, cleaned)
        )
        await db.commit()
        count = len(cleaned.split(",")) if cleaned else 0
        await interaction.response.send_message(
            embed=success("금지어 설정 완료", f"{count}개의 금지어가 등록되었습니다."), ephemeral=True
        )

    # ── /config ──────────────────────────────────────────────────────────────
    @app_commands.command(name="설정확인", description="현재 서버의 봇 설정 상태를 확인합니다.")
    @is_admin()
    async def config(self, interaction: discord.Interaction):
        db = await get_db()
        row = await (await db.execute(
            "SELECT * FROM guild_config WHERE guild_id=?", (interaction.guild_id,)
        )).fetchone()

        embed = info(f"{interaction.guild.name} 봇 설정")

        def ch(col):
            if row and row[col]:
                ch_obj = interaction.guild.get_channel(row[col])
                return ch_obj.mention if ch_obj else f"<#{row[col]}>"
            return "미설정"

        def role(col):
            if row and row[col]:
                r = interaction.guild.get_role(row[col])
                return r.mention if r else f"<@&{row[col]}>"
            return "미설정"

        embed.add_field(name="중재자 역할", value=role("mod_role_id"), inline=True)
        embed.add_field(name="자동 부여 역할", value=role("auto_role_id"), inline=True)
        embed.add_field(name="​", value="​", inline=True)
        embed.add_field(name="환영 채널", value=ch("welcome_channel"), inline=True)
        embed.add_field(name="퇴장 채널", value=ch("goodbye_channel"), inline=True)
        embed.add_field(name="로그 채널", value=ch("log_channel"), inline=True)
        embed.add_field(name="레벨업 채널", value=ch("levelup_channel") if row and row["levelup_channel"] else "현재 채널", inline=True)
        embed.add_field(name="자동 관리", value="✅ 활성화" if not row or row["automod_enabled"] else "❌ 비활성화", inline=True)

        bw_count = len(row["badwords"].split(",")) if row and row["badwords"] else 0
        embed.add_field(name="금지어 수", value=str(bw_count), inline=True)

        # 레벨 보상 목록
        rewards = await (await db.execute(
            "SELECT level, role_id FROM level_rewards WHERE guild_id=? ORDER BY level",
            (interaction.guild_id,)
        )).fetchall()
        if rewards:
            reward_text = "\n".join(
                f"레벨 {r['level']}: <@&{r['role_id']}>" for r in rewards
            )
            embed.add_field(name="레벨 보상", value=reward_text, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── 오류 핸들러 ───────────────────────────────────────────────────────────
    async def cog_app_command_error(self, interaction: discord.Interaction,
                                     err: app_commands.AppCommandError):
        if isinstance(err, app_commands.MissingPermissions):
            await interaction.response.send_message(
                embed=error("권한 부족", "이 명령어는 관리자만 사용할 수 있습니다."), ephemeral=True
            )
        else:
            raise err


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
