import os
import httpx
import discord
from discord import app_commands
from discord.ext import commands, tasks
from database import get_db
from utils import is_mod_or_admin, success, error

CHZZK_API = "https://api.chzzk.naver.com"
POLL_INTERVAL = int(os.getenv("CHZZK_POLL_INTERVAL", 60))

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}


async def fetch_channel_info(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}"
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("content")


async def fetch_live_detail(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v2/channels/{chzzk_id}/live-detail"
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        content = data.get("content")
        if content and content.get("liveImageUrl"):
            url = content["liveImageUrl"]
            url = url.replace("_{type}", "_1080")
            url = url.replace("%7Btype%7D", "1280x720")
            url = url.replace("{type}", "1280x720")
            content["liveImageUrl"] = url
        return content


async def search_channels(keyword: str) -> list[dict]:
    url = f"{CHZZK_API}/service/v1/search/channels"
    params = {"keyword": keyword, "offset": 0, "size": 10}
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("content", {}).get("data", [])


class ChzzkCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.monitor_loop.start()

    def cog_unload(self):
        self.monitor_loop.cancel()

    # ── 라이브 모니터링 루프 ────────────────────────────────────────────────
    @tasks.loop(seconds=POLL_INTERVAL)
    async def monitor_loop(self):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT id, guild_id, discord_channel, chzzk_channel_id, chzzk_name, "
            "is_live, mention_role_id, custom_message FROM chzzk_subscriptions"
        )).fetchall()

        for row in rows:
            try:
                # live-detail은 방송 중일 때만 유효 → openLive로 온/오프 판단
                info = await fetch_channel_info(row["chzzk_channel_id"])
                if info is None:
                    continue

                now_live = bool(info.get("openLive", False))
                was_live = bool(row["is_live"])

                if now_live and not was_live:
                    live = await fetch_live_detail(row["chzzk_channel_id"]) or {}
                    await self._send_live_notification(row, live)
                elif not now_live and was_live:
                    await self._send_offline_notification(row)

                await db.execute(
                    "UPDATE chzzk_subscriptions SET is_live=? WHERE id=?",
                    (int(now_live), row["id"])
                )
            except Exception:
                continue

        await db.commit()

    async def _send_live_notification(self, row, live: dict):
        guild = self.bot.get_guild(row["guild_id"])
        if not guild:
            return
        ch = guild.get_channel(row["discord_channel"])
        if not ch:
            return

        channel_info = live.get("channel", {})
        title     = live.get("liveTitle") or "방송 중"
        category  = live.get("liveCategoryValue") or "없음"
        thumbnail = live.get("liveImageUrl") or ""
        name      = channel_info.get("channelName") or row["chzzk_name"] or "알 수 없음"
        chzzk_url = f"https://chzzk.naver.com/live/{row['chzzk_channel_id']}"

        avatar = channel_info.get("channelImageUrl") or ""
        embed = discord.Embed(
            title=title,
            url=chzzk_url,
            description=f"[{name}]님이 방송을 시작했습니다.",
            color=0x00FFA3,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=name, url=chzzk_url, icon_url=avatar or None)
        embed.add_field(name="카테고리", value=category, inline=False)
        if thumbnail:
            embed.set_image(url=thumbnail)
        embed.set_footer(text="chzzk.junah.dev")

        mention = "@everyone " if bool(row.get("mention_everyone")) else ""
        content = f"{mention}[{name}]님이 방송을 시작했습니다!"

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="방송 바로가기",
            url=chzzk_url,
            style=discord.ButtonStyle.link,
        ))

        await ch.send(content=content, embed=embed, view=view)

    async def _send_offline_notification(self, row):
        guild = self.bot.get_guild(row["guild_id"])
        if not guild:
            return
        ch = guild.get_channel(row["discord_channel"])
        if not ch:
            return

        name = row["chzzk_name"] or "알 수 없음"
        embed = discord.Embed(
            title=f"[{name}]님이 방송을 종료했습니다.",
            color=0x636E72,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="chzzk.junah.dev")
        await ch.send(embed=embed)

    @monitor_loop.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

    # ── /chzzk-subscribe ─────────────────────────────────────────────────────
    @app_commands.command(name="치지직구독", description="치지직 채널 방송 알림을 구독합니다.")
    @app_commands.describe(
        chzzk_id="치지직 채널 ID (URL의 마지막 부분)",
        notify_channel="알림을 받을 Discord 채널",
        mention_role="방송 시작 시 멘션할 역할 (선택)"
    )
    @is_mod_or_admin()
    async def chzzk_subscribe(
        self,
        interaction: discord.Interaction,
        chzzk_id: str,
        notify_channel: discord.TextChannel,
        mention_role: discord.Role | None = None
    ):
        await interaction.response.defer(ephemeral=True)
        info = await fetch_channel_info(chzzk_id)
        if not info:
            return await interaction.followup.send(
                embed=error("오류", "치지직 채널을 찾을 수 없습니다. 채널 ID를 확인해주세요.")
            )

        name = info.get("channelName", chzzk_id)
        image = info.get("channelImageUrl", "")
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO chzzk_subscriptions
                   (guild_id, discord_channel, chzzk_channel_id, chzzk_name, chzzk_image_url, mention_role_id)
                   VALUES (?,?,?,?,?,?)""",
                (interaction.guild_id, notify_channel.id, chzzk_id, name, image,
                 mention_role.id if mention_role else None)
            )
            await db.commit()
        except Exception:
            return await interaction.followup.send(
                embed=error("이미 구독 중", f"**{name}** 채널은 이미 구독 중입니다.")
            )

        embed = discord.Embed(
            title="✅ 치지직 구독 완료",
            description=f"**{name}** 방송이 {notify_channel.mention}에 알림됩니다.",
            color=0x03C75A
        )
        if image:
            embed.set_thumbnail(url=image)
        await interaction.followup.send(embed=embed)

    # ── /chzzk-unsubscribe ────────────────────────────────────────────────────
    @app_commands.command(name="치지직구독해제", description="치지직 채널 알림 구독을 해제합니다.")
    @app_commands.describe(chzzk_id="해제할 치지직 채널 ID")
    @is_mod_or_admin()
    async def chzzk_unsubscribe(self, interaction: discord.Interaction, chzzk_id: str):
        db = await get_db()
        result = await db.execute(
            "DELETE FROM chzzk_subscriptions WHERE guild_id=? AND chzzk_channel_id=?",
            (interaction.guild_id, chzzk_id)
        )
        await db.commit()
        if result.rowcount == 0:
            return await interaction.response.send_message(
                embed=error("없음", "해당 채널을 구독하고 있지 않습니다."), ephemeral=True
            )
        await interaction.response.send_message(
            embed=success("구독 해제", f"치지직 채널 구독이 해제되었습니다."), ephemeral=True
        )

    # ── /test-chzzk-alert ────────────────────────────────────────────────────
    @app_commands.command(name="치지직알림테스트", description="치지직 방송 알림 테스트 메시지를 전송합니다.")
    @app_commands.describe(
        channel="테스트 알림을 보낼 Discord 채널",
        streamer="테스트할 스트리머 이름 (치지직 채널 ID 또는 이름)",
    )
    @is_mod_or_admin()
    async def test_chzzk_alert(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        streamer: str,
    ):
        await interaction.response.defer(ephemeral=True)

        # 이름으로 검색 → channelId 획득
        results   = await search_channels(streamer)
        info      = next(
            (r.get("channel", {}) for r in results
             if r.get("channel", {}).get("channelName", "").lower() == streamer.lower()),
            results[0].get("channel", {}) if results else {},
        )
        name     = info.get("channelName") or streamer
        chzzk_id = info.get("channelId") or streamer
        chzzk_url = f"https://chzzk.naver.com/live/{chzzk_id}"

        # 실제 방송 정보 조회 (v2 API)
        live      = await fetch_live_detail(chzzk_id) or {}
        title     = live.get("liveTitle") or "[테스트] 방송 제목"
        category  = live.get("liveCategoryValue") or "없음"
        thumbnail = live.get("liveImageUrl") or ""

        avatar = info.get("channelImageUrl") or ""
        embed = discord.Embed(
            title=title,
            url=chzzk_url,
            description=f"[{name}]님이 방송을 시작했습니다.",
            color=0x00FFA3,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=name, url=chzzk_url, icon_url=avatar or None)
        embed.add_field(name="카테고리", value=category, inline=False)
        if thumbnail:
            embed.set_image(url=thumbnail)
        embed.set_footer(text="chzzk.junah.dev")

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="방송 바로가기",
            url=chzzk_url,
            style=discord.ButtonStyle.link,
        ))

        await channel.send(
            content=f"[{name}]님이 방송을 시작했습니다!",
            embed=embed,
            view=view,
        )
        await interaction.followup.send(
            f"✅ {channel.mention} 채널에 테스트 알림을 전송했습니다.\n"
            f"방송 제목: `{title}` | 카테고리: `{category}`",
            ephemeral=True,
        )

    # ── /chzzk-list ───────────────────────────────────────────────────────────
    @app_commands.command(name="치지직목록", description="구독 중인 치지직 채널 목록을 확인합니다.")
    async def chzzk_list(self, interaction: discord.Interaction):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT chzzk_name, chzzk_channel_id, discord_channel, is_live "
            "FROM chzzk_subscriptions WHERE guild_id=?",
            (interaction.guild_id,)
        )).fetchall()

        embed = discord.Embed(title="📺 치지직 구독 목록", color=0x03C75A)
        if rows:
            lines = []
            for r in rows:
                status = "🔴 라이브" if r["is_live"] else "⚫ 오프라인"
                lines.append(
                    f"{status} **{r['chzzk_name']}** → <#{r['discord_channel']}>\n"
                    f"  └ ID: `{r['chzzk_channel_id']}`"
                )
            embed.description = "\n".join(lines)
        else:
            embed.description = "구독 중인 채널이 없습니다."
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChzzkCog(bot))
