#!/usr/bin/env python3
"""
Chzzk 커뮤니티 게시글 릴레이
Oracle Cloud VM (한국 IP) 에서 실행 — NNG API 폴링 후 Railway에 웹훅 전송
"""
import asyncio
import httpx
import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [relay] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RAILWAY_URL   = os.environ["RAILWAY_URL"].rstrip("/")   # https://your-app.up.railway.app
RELAY_SECRET  = os.environ["RELAY_SECRET"]

NNG_HEADERS = {
    "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Content-Type":  "application/xml",
    "Accept":        "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Origin":        "https://chzzk.naver.com",
}
RELAY_HEADERS = {"x-relay-secret": RELAY_SECRET, "Content-Type": "application/json"}

# 채널별 마지막 확인 게시글 ID { chzzk_id: post_id }
last_ids: dict[str, str] = {}
# 모니터링 채널 목록 캐시
channels: list[dict] = []


async def fetch_channels(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(
        f"{RAILWAY_URL}/api/relay/channels",
        headers=RELAY_HEADERS,
        timeout=10,
    )
    if resp.status_code != 200:
        log.warning(f"채널 목록 조회 실패: HTTP {resp.status_code}")
        return []
    data = resp.json()
    log.info(f"모니터링 채널 {len(data)}개: {[d['chzzk_name'] for d in data]}")
    return data


async def fetch_latest_post(client: httpx.AsyncClient, chzzk_id: str) -> dict | None:
    url = (
        "https://apis.naver.com/nng_main/nng_comment_api/v1"
        f"/type/CHANNEL_POST/id/{chzzk_id}/comments"
    )
    headers = {**NNG_HEADERS, "Referer": f"https://chzzk.naver.com/{chzzk_id}/community"}
    resp = await client.get(
        url,
        params={"limit": 1, "offset": 0, "orderType": "DESC", "pagingType": "PAGE"},
        headers=headers,
        timeout=10,
    )
    if resp.status_code != 200:
        log.warning(f"NNG API {chzzk_id[:8]}... → HTTP {resp.status_code} {resp.text[:80]}")
        return None
    data = resp.json()
    comments = data.get("content", {}).get("comments", {}).get("data", [])
    if not comments:
        return None
    c = comments[0]
    return {
        "commentId":       c["comment"].get("commentId"),
        "content":         c["comment"].get("content", ""),
        "userNickname":    c["user"].get("userNickname", ""),
        "profileImageUrl": c["user"].get("profileImageUrl"),
        "attaches":        c["comment"].get("attaches"),
    }


async def notify(client: httpx.AsyncClient, chzzk_id: str, post: dict):
    resp = await client.post(
        f"{RAILWAY_URL}/api/relay/notify",
        headers=RELAY_HEADERS,
        json={"chzzk_id": chzzk_id, "post": post},
        timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        log.info(f"알림 전송 완료: {chzzk_id[:8]}... sent={data.get('sent')}")
    else:
        log.warning(f"알림 전송 실패: HTTP {resp.status_code} {resp.text[:80]}")


async def poll_loop():
    global channels, last_ids

    async with httpx.AsyncClient() as client:
        # 시작 시 채널 목록 로드 + last_post_id 초기화 (DB 값 사용)
        channels = await fetch_channels(client)
        for ch in channels:
            if ch["last_post_id"]:
                last_ids[ch["chzzk_id"]] = ch["last_post_id"]

        tick = 0
        while True:
            # 5분마다 채널 목록 갱신
            if tick % 5 == 0:
                updated = await fetch_channels(client)
                if updated:
                    channels = updated

            for ch in channels:
                chzzk_id = ch["chzzk_id"]
                try:
                    post = await fetch_latest_post(client, chzzk_id)
                    if not post:
                        continue
                    post_id = str(post.get("commentId") or "")
                    if not post_id:
                        continue

                    prev_id = last_ids.get(chzzk_id)
                    if prev_id is None:
                        # 첫 실행: ID만 저장, 알림은 보내지 않음
                        last_ids[chzzk_id] = post_id
                        log.info(f"초기화: {ch['chzzk_name']} last_post={post_id}")
                    elif post_id != prev_id:
                        log.info(f"새 게시글 발견: {ch['chzzk_name']} {post_id}")
                        await notify(client, chzzk_id, post)
                        last_ids[chzzk_id] = post_id

                except Exception as e:
                    log.error(f"체크 오류 ({chzzk_id[:8]}...): {e}")

                await asyncio.sleep(1)  # 채널 간 1초 간격

            tick += 1
            await asyncio.sleep(60)  # 1분마다 폴링


if __name__ == "__main__":
    if not os.environ.get("RAILWAY_URL") or not os.environ.get("RELAY_SECRET"):
        print("환경변수 필요: RAILWAY_URL, RELAY_SECRET")
        sys.exit(1)
    log.info("Chzzk 커뮤니티 릴레이 시작")
    asyncio.run(poll_loop())
