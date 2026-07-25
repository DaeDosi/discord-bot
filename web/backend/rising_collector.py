"""CHZZK Rising 데이터 수집기.

치지직의 전체 라이브 방송 목록을 주기적으로 스냅샷해 `rising_live_snapshots`에 저장한다.
이 원천 시계열이 체급 분포 / 틈새 게임 / 라이징 랭킹 / 시간대 히트맵 집계의 바탕이 된다.

배포 메모:
- 봇의 `cogs/chzzk.py` monitor_loop이 이미 Railway에서 `api.chzzk.naver.com`의 개별 채널
  엔드포인트를 정상 호출하고 있으므로, 전체 '라이브 목록' 엔드포인트도 Railway에서 될
  가능성이 높다고 보고 우선 여기(web/backend) lifespan의 asyncio 태스크로 돌린다.
- 만약 이 목록 API가 지역차단/레이트리밋으로 막히면 각 수집 사이클이 `rising_collect_runs`에
  `ok=0`과 사유(note)를 남긴다 → 그때 relay처럼 Korea VM로 옮긴다.
"""
import os
import time
import asyncio
import httpx

from database import get_db

CHZZK_API = "https://api.chzzk.naver.com"
LIVES_URL = f"{CHZZK_API}/service/v1/lives"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

# 수집 주기(초). 기본 10분 — 너무 짧으면 원천 테이블이 급팽창하고 API 부담이 커진다.
COLLECT_INTERVAL = int(os.getenv("RISING_COLLECT_INTERVAL", "600"))
# 한 사이클에서 순회할 최대 페이지 수(페이지당 PAGE_SIZE개) — 폭주 방지 안전장치.
MAX_PAGES = int(os.getenv("RISING_MAX_PAGES", "80"))
PAGE_SIZE = int(os.getenv("RISING_PAGE_SIZE", "50"))
# 원천 스냅샷 보관 기간(일). 이보다 오래된 행은 매 사이클 정리한다(시간대 히트맵/라이징
# 24h 비교에 필요한 만큼만 남기면 되므로 기본 14일).
RAW_RETENTION_DAYS = int(os.getenv("RISING_RAW_RETENTION_DAYS", "14"))
# 팔로워 수는 라이브 목록 API가 주지 않으므로, 시청자 상위 N개 채널만 채널 상세 API로
# 보강한다(전체 수천 개를 매 사이클 조회하면 과부하). 랭킹 상위가 곧 노출되는 부분이라 충분.
FOLLOWER_ENRICH_N = int(os.getenv("RISING_FOLLOWER_ENRICH_N", "100"))
FOLLOWER_CONCURRENCY = int(os.getenv("RISING_FOLLOWER_CONCURRENCY", "12"))


def _log(msg: str):
    print(f"[rising_collector] {msg}", flush=True)


# 채널 프로필 이미지 URL을 DB에 저장하지 않고 메모리에만 유지한다(수집 사이클마다 갱신).
# 수집기와 web/backend가 같은 프로세스라 rising_router가 latest_image()로 바로 읽는다.
_LATEST_IMAGES: dict[str, str] = {}


def latest_image(channel_id: str) -> str:
    return _LATEST_IMAGES.get(channel_id, "")


def _parse_live(item: dict) -> dict | None:
    """치지직 lives 응답의 항목 1건 → 스냅샷 dict. 방어적으로 파싱한다."""
    ch = item.get("channel") or {}
    channel_id = ch.get("channelId") or item.get("channelId")
    if not channel_id:
        return None
    tags = item.get("tags") or []
    return {
        "chzzk_channel_id":   str(channel_id),
        "channel_name":       ch.get("channelName") or "",
        "channel_image_url":  ch.get("channelImageUrl") or "",
        "follower_count":     int(ch.get("followerCount") or 0),  # 목록 API엔 보통 없음 → _enrich_top에서 보강
        "concurrent_viewers": int(item.get("concurrentUserCount") or 0),
        "category_id":        item.get("liveCategory") or "",
        "category_name":      item.get("liveCategoryValue") or "",
        "live_title":         item.get("liveTitle") or "",
        "open_date":          item.get("openDate") or "",
        "adult":              1 if item.get("adult") else 0,
        "tags":               ",".join(str(t) for t in tags if t) if isinstance(tags, list) else "",
    }


async def _fetch_channel_meta(client: httpx.AsyncClient, channel_id: str) -> tuple[int | None, str | None]:
    """채널 상세 API로 (followerCount, channelImageUrl)를 가져온다. 실패 시 (None, None)."""
    try:
        r = await client.get(f"{CHZZK_API}/service/v1/channels/{channel_id}", headers=HEADERS, timeout=8)
        if r.status_code == 200:
            c = (r.json() or {}).get("content") or {}
            return (int(c.get("followerCount") or 0), c.get("channelImageUrl") or "")
    except Exception:
        pass
    return (None, None)


async def _enrich_top(client: httpx.AsyncClient, lives: list[dict]):
    """시청자 상위 FOLLOWER_ENRICH_N개 채널의 팔로워 수(+이미지)를 채널 상세 API로 보강한다."""
    top = sorted(lives, key=lambda l: l["concurrent_viewers"], reverse=True)[:FOLLOWER_ENRICH_N]
    sem = asyncio.Semaphore(FOLLOWER_CONCURRENCY)

    async def one(l: dict):
        async with sem:
            fc, img = await _fetch_channel_meta(client, l["chzzk_channel_id"])
            if fc is not None:
                l["follower_count"] = fc
            if img:
                l["channel_image_url"] = img

    await asyncio.gather(*[one(l) for l in top])


async def _fetch_all_lives(client: httpx.AsyncClient) -> list[dict]:
    """커서 페이지네이션으로 현재 라이브 목록 전체(최대 MAX_PAGES*PAGE_SIZE)를 수집한다.

    치지직 응답: content.data[](방송 목록), content.page.next(다음 페이지 커서 dict).
    next dict의 키/값을 그대로 다음 요청의 쿼리 파라미터로 넘기면 다음 페이지가 나온다.
    """
    lives: list[dict] = []
    seen: set[str] = set()
    params: dict = {"size": PAGE_SIZE, "sortType": "POPULAR"}

    for _ in range(MAX_PAGES):
        resp = await client.get(LIVES_URL, params=params, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:150]}")
        content = (resp.json() or {}).get("content") or {}
        data = content.get("data") or []
        if not data:
            break

        for item in data:
            parsed = _parse_live(item)
            if parsed and parsed["chzzk_channel_id"] not in seen:
                seen.add(parsed["chzzk_channel_id"])
                lives.append(parsed)

        nxt = (content.get("page") or {}).get("next")
        if not nxt:
            break
        # 다음 페이지 커서를 그대로 쿼리 파라미터로 승계
        params = {"size": PAGE_SIZE, "sortType": "POPULAR", **nxt}

    return lives


async def _record_run(collected_at: int, live_count: int, total_viewers: int, ok: int, note: str = ""):
    db = await get_db()
    await db.execute(
        """INSERT OR IGNORE INTO rising_collect_runs
               (collected_at, live_count, total_viewers, ok, note)
           VALUES (?,?,?,?,?)""",
        (collected_at, live_count, total_viewers, ok, note[:500]),
    )
    await db.commit()


async def _prune_old(now: int):
    # 원천 스냅샷(사이클당 수천 행)만 롤링 정리한다. 콤팩트한 사이클 요약
    # (rising_collect_runs, 사이클당 1행)은 영구 보관 — 시계열 차트의 장기 이력이
    # 계속 누적되도록 한다. (runs는 하루 ~144행이라 장기 보관해도 부담 없음)
    cutoff = now - RAW_RETENTION_DAYS * 86400
    db = await get_db()
    await db.execute("DELETE FROM rising_live_snapshots WHERE collected_at < ?", (cutoff,))
    await db.commit()


async def collect_once() -> tuple[int, str]:
    """한 번의 수집 사이클. (수집된 라이브 수, 상태 메모) 반환."""
    now = int(time.time())
    try:
        async with httpx.AsyncClient() as client:
            lives = await _fetch_all_lives(client)
            if lives:
                # 팔로워 수는 목록 API에 없으므로 상위 채널만 상세 API로 보강
                await _enrich_top(client, lives)
    except Exception as e:
        note = f"fetch 실패: {e}"
        _log(note)
        await _record_run(now, 0, 0, ok=0, note=note)
        return (0, note)

    if not lives:
        note = "라이브 0건 (API 응답 비었거나 방송 없음)"
        await _record_run(now, 0, 0, ok=0, note=note)
        return (0, note)

    # 프로필 이미지는 DB가 아니라 메모리 맵으로만 유지(사이클마다 통째로 갱신)
    global _LATEST_IMAGES
    _LATEST_IMAGES = {l["chzzk_channel_id"]: l["channel_image_url"] for l in lives if l.get("channel_image_url")}

    db = await get_db()
    total_viewers = sum(l["concurrent_viewers"] for l in lives)
    await db.executemany(
        """INSERT INTO rising_live_snapshots
               (collected_at, chzzk_channel_id, channel_name, follower_count,
                concurrent_viewers, category_id, category_name, live_title, open_date, adult, tags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (now, l["chzzk_channel_id"], l["channel_name"], l["follower_count"],
             l["concurrent_viewers"], l["category_id"], l["category_name"],
             l["live_title"], l["open_date"], l["adult"], l.get("tags", ""))
            for l in lives
        ],
    )
    await db.commit()
    await _record_run(now, len(lives), total_viewers, ok=1)
    await _prune_old(now)

    _log(f"수집 완료: {len(lives)}개 라이브, 총 시청자 {total_viewers:,}명")
    return (len(lives), "ok")


async def start_collector():
    """백엔드 lifespan에서 백그라운드 태스크로 실행 — COLLECT_INTERVAL마다 수집."""
    _log(f"시작 (interval={COLLECT_INTERVAL}s, page_size={PAGE_SIZE}, max_pages={MAX_PAGES})")
    while True:
        try:
            await collect_once()
        except Exception as e:
            _log(f"수집 루프 예외: {e}")
        await asyncio.sleep(COLLECT_INTERVAL)
