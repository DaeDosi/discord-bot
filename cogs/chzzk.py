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


async def fetch_latest_video(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}/videos"
    params = {"sortType": "RECENT", "size": 1, "page": 0}
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        videos = data.get("content", {}).get("data", [])
        return videos[0] if videos else None


async def fetch_latest_clip(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}/clips"
    params = {"sortType": "RECENT", "size": 1}
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        clips = data.get("content", {}).get("data", [])
        return clips[0] if clips else None


async def fetch_latest_post(chzzk_id: str) -> dict | None:
    url = f"{CHZZK_API}/service/v1/channels/{chzzk_id}/community/posts"
    params = {"page": 0, "size": 1}
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        posts = data.get("content", {}).get("data", [])
        return posts[0] if posts else None


# ── 치지직 설정 채널 선택 View ────────────────────────────────────────────────
class ChzzkChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, sub_id: int, guild_id: int):
        super().__init__(
            placeholder="알림 채널 선택...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )
        self.sub_id = sub_id
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        db = await get_db()
        await db.execute(
            "UPDATE chzzk_subscriptions SET discord_channel=? WHERE id=? AND guild_id=?",
            (channel.id, self.sub_id, self.guild_id)
        )
        await db.commit()
        await interaction.response.send_message(
            f"✅ 치지직 알림 채널이 {channel.mention}(으)로 변경되었습니다.",
            ephemeral=True,
        )


class ChzzkSettingsView(discord.ui.View):
    def __init__(self, sub_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.add_item(ChzzkChannelSelect(sub_id=sub_id, guild_id=guild_id))


class ChzzkCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.monitor_loop.start()

    def cog_unload(self):
        self.monitor_loop.cancel()

    # ── 라이브 + 콘텐츠 모니터링 루프 ────────────────────────────────────────
    @tasks.loop(seconds=POLL_INTERVAL)
    async def monitor_loop(self):
        db = await get_db()
        rows = await (await db.execute(
            "SELECT id, guild_id, discord_channel, chzzk_channel_id, chzzk_name, "
            "is_live, mention_role_id, custom_message, mention_everyone, "
            "notify_vod, notify_clip, notify_community, "
            "last_vod_id, last_clip_id, last_post_id "
            "FROM chzzk_subscriptions"
        )).fetchall()

        for row in rows:
            try:
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

                # ── VOD 알림 ──────────────────────────────────────────────
                if bool(row["notify_vod"]):
                    try:
                        video = await fetch_latest_video(row["chzzk_channel_id"])
                        if video:
                            vid_id = str(video.get("videoNo", ""))
                            if vid_id:
                                if row["last_vod_id"] and vid_id != row["last_vod_id"]:
                                    await self._send_video_notification(row, video)
                                if vid_id != (row["last_vod_id"] or ""):
                                    await db.execute(
                                        "UPDATE chzzk_subscriptions SET last_vod_id=? WHERE id=?",
                                        (vid_id, row["id"])
                                    )
                    except Exception:
                        pass

                # ── 클립 알림 ─────────────────────────────────────────────
                if bool(row["notify_clip"]):
                    try:
                        clip = await fetch_latest_clip(row["chzzk_channel_id"])
                        if clip:
                            clip_id = str(clip.get("clipNo", clip.get("clipUID", "")))
                            if clip_id:
                                if row["last_clip_id"] and clip_id != row["last_clip_id"]:
                                    await self._send_clip_notification(row, clip)
                                if clip_id != (row["last_clip_id"] or ""):
                                    await db.execute(
                                        "UPDATE chzzk_subscriptions SET last_clip_id=? WHERE id=?",
                                        (clip_id, row["id"])
                                    )
                    except Exception:
                        pass

                # ── 커뮤니티 게시글 알림 ────────────────────────────────
                if bool(row["notify_community"]):
                    try:
                        post = await fetch_latest_post(row["chzzk_channel_id"])
                        if post:
                            post_id = str(post.get("postNo", post.get("id", "")))
                            if post_id:
                                if row["last_post_id"] and post_id != row["last_post_id"]:
                                    await self._send_post_notification(row, post)
                                if post_id != (row["last_post_id"] or ""):
                                    await db.execute(
                                        "UPDATE chzzk_subscriptions SET last_post_id=? WHERE id=?",
                                        (post_id, row["id"])
                                    )
                    except Exception:
                        pass

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

    async def _send_video_notification(self, row, video: dict):
        guild = self.bot.get_guild(row["guild_id"])
        if not guild:
            return
        ch = guild.get_channel(row["discord_channel"])
        if not ch:
            return

        name      = row["chzzk_name"] or "알 수 없음"
        title     = video.get("videoTitle", "새 다시보기")
        vid_no    = video.get("videoNo", "")
        thumbnail = video.get("thumbnailImageUrl", "")
        video_url = f"https://chzzk.naver.com/video/{vid_no}" if vid_no else ""

        embed = discord.Embed(
            title=title,
            url=video_url or None,
            description=f"**{name}**님이 새 다시보기 영상을 업로드했습니다.",
            color=0x03C75A,
            timestamp=discord.utils.utcnow(),
        )
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        embed.set_footer(text="NexBot • nexbot.shop")

        view = discord.ui.View()
        if video_url:
            view.add_item(discord.ui.Button(label="영상 바로가기", url=video_url, style=discord.ButtonStyle.link))

        await ch.send(embed=embed, view=view)

    async def _send_clip_notification(self, row, clip: dict):
        guild = self.bot.get_guild(row["guild_id"])
        if not guild:
            return
        ch = guild.get_channel(row["discord_channel"])
        if not ch:
            return

        name      = row["chzzk_name"] or "알 수 없음"
        title     = clip.get("clipTitle", "새 클립")
        clip_no   = clip.get("clipNo", clip.get("clipUID", ""))
        thumbnail = clip.get("thumbnailImageUrl", "")
        clip_url  = f"https://chzzk.naver.com/clips/{clip_no}" if clip_no else ""

        embed = discord.Embed(
            title=title,
            url=clip_url or None,
            description=f"**{name}**님이 새 클립을 등록했습니다.",
            color=0x03C75A,
            timestamp=discord.utils.utcnow(),
        )
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        embed.set_footer(text="NexBot • nexbot.shop")

        view = discord.ui.View()
        if clip_url:
            view.add_item(discord.ui.Button(label="클립 바로가기", url=clip_url, style=discord.ButtonStyle.link))

        await ch.send(embed=embed, view=view)

    async def _send_post_notification(self, row, post: dict):
        guild = self.bot.get_guild(row["guild_id"])
        if not guild:
            return
        ch = guild.get_channel(row["discord_channel"])
        if not ch:
            return

        name    = row["chzzk_name"] or "알 수 없음"
        post_no = post.get("postNo", post.get("id", ""))
        content_data = post.get("content", {})
        if isinstance(content_data, dict):
            title = content_data.get("title") or "새 커뮤니티 게시글"
        elif isinstance(content_data, str):
            title = content_data[:50] + ("..." if len(content_data) > 50 else "")
        else:
            title = "새 커뮤니티 게시글"

        channel_id = row["chzzk_channel_id"]
        post_url = f"https://chzzk.naver.com/community/{channel_id}/post/{post_no}" if post_no else ""

        embed = discord.Embed(
            title=title,
            url=post_url or None,
            description=f"**{name}**님이 새 커뮤니티 게시글을 작성했습니다.",
            color=0x03C75A,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="NexBot • nexbot.shop")

        view = discord.ui.View()
        if post_url:
            view.add_item(discord.ui.Button(label="게시글 바로가기", url=post_url, style=discord.ButtonStyle.link))

        await ch.send(embed=embed, view=view)

    @monitor_loop.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

    # ── /팔로우불러오기 ──────────────────────────────────────────────────────
    @app_commands.command(name="팔로우불러오기", description="[관리자] 저장된 팔로우 데이터로 구독자 역할을 재적용합니다.")
    @app_commands.default_permissions(manage_guild=True)
    async def 팔로우불러오기(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = await get_db()

        tier_rows = await (await db.execute(
            "SELECT months, role_id FROM chzzk_follow_roles WHERE guild_id=? ORDER BY months DESC",
            (interaction.guild_id,)
        )).fetchall()

        if not tier_rows:
            sub = await (await db.execute(
                "SELECT follow_role_1month, follow_role_3month, "
                "follow_months_tier1, follow_months_tier2 "
                "FROM chzzk_subscriptions WHERE guild_id=?",
                (interaction.guild_id,)
            )).fetchone()
            if not sub or (not sub["follow_role_1month"] and not sub["follow_role_3month"]):
                return await interaction.followup.send(
                    "❌ 팔로워 역할이 설정되지 않았습니다. 대시보드 > 치지직에서 역할 티어를 먼저 추가해주세요.",
                    ephemeral=True,
                )
            tier1_months = int(sub["follow_months_tier1"] or 1)
            tier2_months = int(sub["follow_months_tier2"] or 3)
            tiers = []
            if sub["follow_role_3month"]:
                tiers.append((tier2_months, int(sub["follow_role_3month"])))
            if sub["follow_role_1month"]:
                tiers.append((tier1_months, int(sub["follow_role_1month"])))
            tiers.sort(key=lambda x: x[0], reverse=True)
        else:
            tiers = [(int(r["months"]), int(r["role_id"])) for r in tier_rows]

        verified_rows = await (await db.execute(
            "SELECT user_id, tier_months FROM chzzk_verifications WHERE guild_id=?",
            (interaction.guild_id,)
        )).fetchall()

        if not verified_rows:
            return await interaction.followup.send(
                "❌ 치지직 인증을 완료한 유저가 없습니다.", ephemeral=True
            )

        tier_counts: dict[int, int] = {months: 0 for months, _ in tiers}
        skipped = 0

        for row in verified_rows:
            member = interaction.guild.get_member(int(row["user_id"]))
            if not member:
                skipped += 1
                continue
            months = int(row["tier_months"] or 0)
            try:
                for req_months, role_id in tiers:
                    if months >= req_months:
                        role = interaction.guild.get_role(role_id)
                        if role and role not in member.roles:
                            await member.add_roles(role, reason="/팔로우불러오기")
                        tier_counts[req_months] = tier_counts.get(req_months, 0) + 1
                        break
            except discord.Forbidden:
                skipped += 1

        embed = discord.Embed(
            title="✅ 팔로워 역할 최신화 완료",
            description="저장된 팔로우 기간 기준으로 역할을 재적용했습니다.",
            color=0x57F287,
        )
        for req_months, _ in sorted(tiers, key=lambda x: x[0]):
            embed.add_field(
                name=f"{req_months}개월+ 역할 부여",
                value=f"{tier_counts.get(req_months, 0)}명",
                inline=True,
            )
        embed.add_field(name="건너뜀(미가입 등)", value=f"{skipped}명", inline=True)
        embed.set_footer(text="팔로우 기간 갱신은 유저가 재인증(치지직 OAuth)하면 자동 업데이트됩니다.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /치지직설정 ──────────────────────────────────────────────────────────
    @app_commands.command(name="치지직설정", description="웹 대시보드에서 치지직 알림을 설정합니다.")
    @app_commands.default_permissions(manage_guild=True)
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

    # ── /치지직설정불러오기 ───────────────────────────────────────────────────
    @app_commands.command(name="치지직설정불러오기", description="[관리자] 대시보드의 치지직 설정을 불러와 알림 채널을 적용합니다.")
    @app_commands.default_permissions(manage_guild=True)
    @is_mod_or_admin()
    async def 치지직설정불러오기(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        row = await (await db.execute(
            "SELECT id, discord_channel, chzzk_name, mention_everyone, "
            "notify_vod, notify_clip, notify_community "
            "FROM chzzk_subscriptions WHERE guild_id=? LIMIT 1",
            (interaction.guild_id,)
        )).fetchone()

        if not row:
            return await interaction.followup.send(
                "❌ 등록된 치지직 채널이 없습니다. 웹 대시보드에서 먼저 치지직 계정을 연동해주세요.",
                ephemeral=True,
            )

        current_ch = interaction.guild.get_channel(row["discord_channel"])

        content_items = []
        if row["notify_vod"]:       content_items.append("동영상(다시보기)")
        if row["notify_clip"]:      content_items.append("클립")
        if row["notify_community"]: content_items.append("커뮤니티")

        embed = discord.Embed(
            title="치지직 설정 현황",
            description="아래 채널 선택으로 알림 채널을 변경할 수 있습니다.",
            color=0x03C75A,
        )
        embed.add_field(name="스트리머", value=row["chzzk_name"] or "알 수 없음", inline=True)
        embed.add_field(
            name="현재 알림 채널",
            value=current_ch.mention if current_ch else "채널 없음",
            inline=True,
        )
        embed.add_field(name="@everyone 멘션", value="켜짐" if row["mention_everyone"] else "꺼짐", inline=True)
        embed.add_field(
            name="콘텐츠 알림",
            value=", ".join(content_items) if content_items else "없음",
            inline=False,
        )
        embed.set_footer(text="채널 선택 드롭다운으로 알림 채널을 변경하세요.")

        view = ChzzkSettingsView(sub_id=row["id"], guild_id=interaction.guild_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # ── /치지직알림테스트 ─────────────────────────────────────────────────────
    @app_commands.command(name="치지직알림테스트", description="등록된 치지직 알림 설정으로 테스트 메시지를 전송합니다.")
    @app_commands.default_permissions(manage_guild=True)
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
