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
        embed.set_footer(text="NexBot • nexbot.shop")

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
        embed.set_footer(text="NexBot • nexbot.shop")
        await ch.send(embed=embed)

    @monitor_loop.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

    # ── /치지직설정 ──────────────────────────────────────────────────────────
    @app_commands.command(name="치지직설정", description="웹 대시보드에서 치지직 알림을 설정합니다.")
    @is_mod_or_admin()
    async def chzzk_settings(self, interaction: discord.Interaction):
        dashboard_url = f"{os.getenv('FRONTEND_URL', 'https://nexbot.shop')}/dashboard/{interaction.guild_id}/chzzk"
        embed = discord.Embed(
            title="치지직 알림 설정",
            description="아래 버튼을 눌러 웹 대시보드에서 치지직 알림을 설정하세요.",
            color=0x03C75A,
        )
        embed.set_footer(text="NexBot Dashboard")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="대시보드 열기",
            url=dashboard_url,
            style=discord.ButtonStyle.link,
        ))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ── /치지직알림테스트 ─────────────────────────────────────────────────────
    @app_commands.command(name="치지직알림테스트", description="등록된 치지직 알림 설정으로 테스트 메시지를 전송합니다.")
    @is_mod_or_admin()
    async def test_chzzk_alert(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        row = await (await db.execute(
            "SELECT discord_channel, chzzk_channel_id, chzzk_name, chzzk_image_url, mention_everyone "
            "FROM chzzk_subscriptions WHERE guild_id=? LIMIT 1",
            (interaction.guild_id,)
        )).fetchone()

        if not row:
            dashboard_url = f"{os.getenv('FRONTEND_URL', 'https://nexbot.shop')}/dashboard/{interaction.guild_id}/chzzk"
            embed = discord.Embed(
                title="등록된 정보가 없습니다.",
                description="치지직 알림을 설정하려면 웹 대시보드를 이용하세요.",
                color=0xED4245,
            )
            embed.set_footer(text="NexBot Dashboard")
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="대시보드 열기",
                url=dashboard_url,
                style=discord.ButtonStyle.link,
            ))
            return await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        ch = interaction.guild.get_channel(row["discord_channel"])
        if not ch:
            return await interaction.followup.send(
                embed=error("오류", "등록된 Discord 채널을 찾을 수 없습니다. 대시보드에서 채널을 다시 설정해주세요."),
                ephemeral=True,
            )

        chzzk_id  = row["chzzk_channel_id"]
        name      = row["chzzk_name"] or chzzk_id
        chzzk_url = f"https://chzzk.naver.com/live/{chzzk_id}"

        live      = await fetch_live_detail(chzzk_id) or {}
        channel_info = live.get("channel", {})
        title     = live.get("liveTitle") or "[테스트] 방송 제목"
        category  = live.get("liveCategoryValue") or "없음"
        thumbnail = live.get("liveImageUrl") or ""
        avatar    = channel_info.get("channelImageUrl") or row["chzzk_image_url"] or ""

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
        embed.set_footer(text="NexBot • nexbot.shop")

        mention = "@everyone " if bool(row["mention_everyone"]) else ""
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="방송 바로가기",
            url=chzzk_url,
            style=discord.ButtonStyle.link,
        ))

        await ch.send(content=f"{mention}[{name}]님이 방송을 시작했습니다!", embed=embed, view=view)
        await interaction.followup.send(
            f"✅ {ch.mention} 채널에 테스트 알림을 전송했습니다.\n"
            f"스트리머: `{name}` | 방송 제목: `{title}`",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ChzzkCog(bot))
