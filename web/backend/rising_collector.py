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
from datetime import datetime, timezone, timedelta

from database import get_db

_KST = timezone(timedelta(hours=9))

CHZZK_API = "https://api.chzzk.naver.com"
LIVES_URL = f"{CHZZK_API}/service/v1/lives"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

# 수집 주기(초). 기본 10분 — 너무 짧으면 원천 테이블이 급팽창하고 API 부담이 커진다.
COLLECT_INTERVAL = int(os.getenv("RISING_COLLECT_INTERVAL", "600"))
# 한 사이클에서 순회할 최대 페이지 수(페이지당 PAGE_SIZE개) — 폭주 방지 안전장치.
#
# 실측(2026-07-26): 치지직 동시 라이브 총 5,754개 = 117페이지에서 목록 소진.
# 기존 80페이지(4,000개)는 하위 ~1,750개(전체의 약 30%)를 잘라내고 있었고, 잘린 구간은
# 대부분 시청자 0~4명대 소규모 방송이라 '하꼬/신입' 분석에서 정확히 필요한 표본이었다.
# 200페이지(10,000개)로 올려 소진까지 돌 여유를 둔다 — 상한에 걸리면 note에 기록된다.
MAX_PAGES = int(os.getenv("RISING_MAX_PAGES", "200"))
# PAGE_SIZE는 API 하드 상한이 50이다(100 이상 요청 시 HTTP 400). 올려도 소용없다.
PAGE_SIZE = int(os.getenv("RISING_PAGE_SIZE", "50"))
# 페이지 간 간격(ms). 무간격으로 몰아치면 읽기 타임아웃이 난다(실측: ~17req/s에서 발생,
# 120ms 간격에서는 117페이지 완주). 페이지 수를 늘린 만큼 페이싱이 필요하다.
PAGE_DELAY_MS = int(os.getenv("RISING_PAGE_DELAY_MS", "120"))
# 페이지 단위 재시도 횟수 — 일시적 네트워크 오류로 사이클 전체를 버리지 않기 위함.
PAGE_RETRIES = int(os.getenv("RISING_PAGE_RETRIES", "3"))
# 원천 스냅샷 보관 기간(일). 이보다 오래된 행은 매 사이클 정리한다(시간대 히트맵/라이징
# 24h 비교에 필요한 만큼만 남기면 되므로 기본 14일).
RAW_RETENTION_DAYS = int(os.getenv("RISING_RAW_RETENTION_DAYS", "14"))
# 팔로워 수는 라이브 목록 API가 주지 않으므로, 시청자 상위 N개 채널만 채널 상세 API로
# 보강한다(전체 수천 개를 매 사이클 조회하면 과부하). 랭킹 상위가 곧 노출되는 부분이라 충분.
FOLLOWER_ENRICH_N = int(os.getenv("RISING_FOLLOWER_ENRICH_N", "100"))
FOLLOWER_CONCURRENCY = int(os.getenv("RISING_FOLLOWER_CONCURRENCY", "12"))


def _log(msg: str):
    print(f"[rising_collector] {msg}", flush=True)


# 채널 프로필 이미지 URL. 메모리로 실시간 유지(라이브 목록에서 매 사이클 누적)하되,
# channel_profiles 테이블에 영구 저장하고 매일 00시(자정 이후 첫 수집)에 DB로 갱신한다.
# 서버 시작 시 DB에서 로드해 재시작 직후에도 이미지가 바로 보인다.
_LATEST_IMAGES: dict[str, str] = {}
_LAST_PERSIST_DATE = None  # 마지막으로 DB에 저장한 KST 날짜


def latest_image(channel_id: str) -> str:
    return _LATEST_IMAGES.get(channel_id, "")


async def _load_profiles():
    """서버 시작 시 DB의 프로필 이미지를 메모리로 로드."""
    try:
        db = await get_db()
        rows = await (await db.execute("SELECT chzzk_channel_id, image_url FROM channel_profiles")).fetchall()
        for r in rows:
            if r["image_url"]:
                _LATEST_IMAGES[r["chzzk_channel_id"]] = r["image_url"]
        _log(f"프로필 이미지 {len(_LATEST_IMAGES)}개 DB에서 로드")
    except Exception as e:
        _log(f"프로필 로드 실패: {e}")


async def _persist_profiles():
    """현재 메모리의 프로필 이미지를 DB에 저장하고 30일 이상 미갱신 행을 정리한다(일 1회)."""
    try:
        db = await get_db()
        now = int(time.time())
        await db.executemany(
            """INSERT INTO channel_profiles(chzzk_channel_id, image_url, updated_at) VALUES(?,?,?)
               ON CONFLICT(chzzk_channel_id) DO UPDATE SET image_url=excluded.image_url, updated_at=excluded.updated_at""",
            [(cid, url, now) for cid, url in _LATEST_IMAGES.items() if url],
        )
        await db.execute("DELETE FROM channel_profiles WHERE updated_at < ?", (now - 30 * 86400,))
        await db.commit()
        # 메모리도 DB(최근 30일)에 맞춰 재로드 — 무한 증가 방지
        rows = await (await db.execute("SELECT chzzk_channel_id, image_url FROM channel_profiles")).fetchall()
        _LATEST_IMAGES.clear()
        for r in rows:
            if r["image_url"]:
                _LATEST_IMAGES[r["chzzk_channel_id"]] = r["image_url"]
        _log(f"프로필 이미지 {len(_LATEST_IMAGES)}개 DB 저장(일일 갱신)")
    except Exception as e:
        _log(f"프로필 저장 실패: {e}")


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


async def _fetch_all_lives(client: httpx.AsyncClient) -> tuple[list[dict], str]:
    """커서 페이지네이션으로 현재 라이브 목록 전체(최대 MAX_PAGES*PAGE_SIZE)를 수집한다.

    치지직 응답: content.data[](방송 목록), content.page.next(다음 페이지 커서 dict).
    next dict의 키/값을 그대로 다음 요청의 쿼리 파라미터로 넘기면 다음 페이지가 나온다.

    (수집 목록, 종료 사유 메모)를 반환한다. 종료 사유를 남기는 이유: 예전에는 목록이
    소진돼 끝났는지 MAX_PAGES 상한에 걸려 잘렸는지 구분할 기록이 전혀 없어서, 4,000개에서
    잘리고 있다는 사실 자체를 알 수 없었다. 이제 rising_collect_runs.note로 확인 가능하다.

    페이지 단위로 재시도하고, 이미 모은 게 있으면 부분 성공으로 반환한다 — 페이지 수가
    많아진 만큼 한 페이지의 일시적 타임아웃으로 사이클 전체를 버리면 손실이 크다.
    """
    lives: list[dict] = []
    seen: set[str] = set()
    params: dict = {"size": PAGE_SIZE, "sortType": "POPULAR"}
    note = "page_cap_reached"  # 루프를 다 돌면(=상한 도달) 이 값이 남는다

    for page in range(MAX_PAGES):
        if page and PAGE_DELAY_MS > 0:
            await asyncio.sleep(PAGE_DELAY_MS / 1000)

        resp = None
        last_err: Exception | None = None
        for attempt in range(PAGE_RETRIES):
            try:
                resp = await client.get(LIVES_URL, params=params, headers=HEADERS, timeout=15)
                break
            except Exception as e:  # 읽기/연결 타임아웃 등 일시적 오류
                last_err = e
                await asyncio.sleep(0.8 * (attempt + 1))
        if resp is None:
            if lives:  # 부분 성공 — 여기까지 모은 것은 살린다
                return (lives, f"partial: page {page + 1} 재시도 실패 ({type(last_err).__name__})")
            raise RuntimeError(f"page {page + 1} 재시도 실패: {last_err}")

        if resp.status_code != 200:
            if lives:
                return (lives, f"partial: page {page + 1} HTTP {resp.status_code}")
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:150]}")

        content = (resp.json() or {}).get("content") or {}
        data = content.get("data") or []
        if not data:
            note = "exhausted: 빈 응답"
            break

        for item in data:
            parsed = _parse_live(item)
            if parsed and parsed["chzzk_channel_id"] not in seen:
                seen.add(parsed["chzzk_channel_id"])
                lives.append(parsed)

        nxt = (content.get("page") or {}).get("next")
        if not nxt:
            note = "exhausted: next 커서 없음"
            break
        # 다음 페이지 커서를 그대로 쿼리 파라미터로 승계
        params = {"size": PAGE_SIZE, "sortType": "POPULAR", **nxt}

    return (lives, note)


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
            lives, fetch_note = await _fetch_all_lives(client)
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

    # 프로필 이미지는 메모리에 누적(사이클마다 갱신)하고, 매일 00시 이후 첫 수집에 DB로 저장.
    _LATEST_IMAGES.update({l["chzzk_channel_id"]: l["channel_image_url"] for l in lives if l.get("channel_image_url")})
    global _LAST_PERSIST_DATE
    today_kst = datetime.now(_KST).date()
    if _LAST_PERSIST_DATE != today_kst:
        _LAST_PERSIST_DATE = today_kst
        await _persist_profiles()

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
    # 종료 사유를 note에 남긴다 — page_cap_reached면 MAX_PAGES를 더 올려야 한다는 신호다.
    await _record_run(now, len(lives), total_viewers, ok=1, note=fetch_note)
    await _prune_old(now)

    if fetch_note == "page_cap_reached":
        _log(f"경고: MAX_PAGES({MAX_PAGES}) 상한에 도달 — 목록이 잘렸을 수 있습니다. "
             f"RISING_MAX_PAGES를 올리세요.")
    _log(f"수집 완료: {len(lives)}개 라이브, 총 시청자 {total_viewers:,}명 ({fetch_note})")
    return (len(lives), "ok")


async def start_collector():
    """백엔드 lifespan에서 백그라운드 태스크로 실행 — COLLECT_INTERVAL마다 수집."""
    _log(f"시작 (interval={COLLECT_INTERVAL}s, page_size={PAGE_SIZE}, max_pages={MAX_PAGES})")
    await _load_profiles()  # DB에 저장된 프로필 이미지를 메모리로 복원(재시작 대응)
    while True:
        try:
            await collect_once()
        except Exception as e:
            _log(f"수집 루프 예외: {e}")
        await asyncio.sleep(COLLECT_INTERVAL)
