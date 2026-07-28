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
# 사이클이 주기를 넘겼을 때 다음 시작까지 최소한 비워 둘 시간(초).
# 0이면 외부 API를 쉬는 구간 없이 계속 때리게 된다.
MIN_GAP_SECONDS = int(os.getenv("RISING_MIN_GAP_SECONDS", "30"))
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
# 다운샘플링: 원본(10분)은 짧게, 롤업(채널×시간)은 길게 보관한다.
# 실측 303B/행 × 5,718행/사이클 기준 원본은 238MB/일이라 14일이면 3.25GB가 된다.
# RAW_RETENTION_HOURS가 설정되면 RAW_RETENTION_DAYS보다 우선한다(시간 단위 제어).
# 26시간(24h + 2h 여유): live_ranking의 24h 전 팔로워, rising_stars의 24h 전 비교,
# categories range=24h 는 '약 24시간 전 원본 스냅샷'을 점 조회한다. 정확히 24시간으로
# 자르면 이 세 곳이 경계에서 조용히 빈 값이 되므로 여유를 둔다.
RAW_RETENTION_HOURS = int(os.getenv("RISING_RAW_RETENTION_HOURS", "26"))
# 롤업 보관 일수. 성장률이 '최근 7일 평균'을 쓰므로 7일 + 여유 1일 = 8일을 기본으로 둔다.
# 실측 기준 용량: 원본 26h(258MB) + 롤업 8일(210MB) = 약 468MB → Railway 500MB 안에 들어간다.
# (14일로 늘리면 약 625MB로 초과한다. 저장 공간이 늘어나면 이 값을 올리면 된다.)
ROLLUP_RETENTION_DAYS = int(os.getenv("RISING_ROLLUP_RETENTION_DAYS", "8"))
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


async def _fetch_all_lives(client: httpx.AsyncClient) -> tuple[list[dict], str, dict]:
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
    # 계측 — 주기를 줄여도 되는지는 '한 사이클이 외부 API를 몇 번 부르고 얼마나
    # 걸리는가'로 판단해야 한다. 재시도까지 포함해 실제 호출 수를 센다.
    stats = {"pages": 0, "api_calls": 0}

    for page in range(MAX_PAGES):
        if page and PAGE_DELAY_MS > 0:
            await asyncio.sleep(PAGE_DELAY_MS / 1000)

        resp = None
        last_err: Exception | None = None
        for attempt in range(PAGE_RETRIES):
            try:
                stats["api_calls"] += 1
                resp = await client.get(LIVES_URL, params=params, headers=HEADERS, timeout=15)
                break
            except Exception as e:  # 읽기/연결 타임아웃 등 일시적 오류
                last_err = e
                await asyncio.sleep(0.8 * (attempt + 1))
        if resp is None:
            if lives:  # 부분 성공 — 여기까지 모은 것은 살린다
                return (lives, f"partial: page {page + 1} 재시도 실패 ({type(last_err).__name__})", stats)
            raise RuntimeError(f"page {page + 1} 재시도 실패: {last_err}")

        if resp.status_code != 200:
            if lives:
                return (lives, f"partial: page {page + 1} HTTP {resp.status_code}", stats)
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:150]}")

        stats["pages"] += 1
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

    return (lives, note, stats)


async def _record_run(collected_at: int, live_count: int, total_viewers: int, ok: int,
                      note: str = "", duration_ms: int = 0, pages: int = 0, api_calls: int = 0):
    db = await get_db()
    await db.execute(
        """INSERT OR IGNORE INTO rising_collect_runs
               (collected_at, live_count, total_viewers, ok, note,
                duration_ms, pages, api_calls)
           VALUES (?,?,?,?,?,?,?,?)""",
        (collected_at, live_count, total_viewers, ok, note[:500],
         int(duration_ms), int(pages), int(api_calls)),
    )
    await db.commit()


async def _build_rollup(now: int):
    """현재/직전 시간 버킷을 원본에서 재집계해 rising_hourly_rollup에 upsert한다.

    매 사이클 두 버킷을 다시 계산하는 이유: 진행 중인 시간은 스냅샷이 계속 늘어나고,
    사이클이 정시 경계를 넘나들 때 직전 시간이 마지막 1~2개 스냅샷을 놓칠 수 있다.
    원본 보관이 최소 2시간 이상이면 항상 정확한 값으로 덮어써진다(멱등).
    """
    db = await get_db()
    cur_hour = now - (now % 3600)
    for hour_ts in (cur_hour - 3600, cur_hour):
        await db.execute(
            """INSERT OR REPLACE INTO rising_hourly_rollup
                   (hour_ts, chzzk_channel_id, channel_name, category_name,
                    snaps, avg_viewers, peak_viewers, sum_viewers, max_follower)
               SELECT ?,
                      chzzk_channel_id,
                      channel_name,      -- bare column: MAX(collected_at)와 같은 행의 값
                      category_name,
                      COUNT(*),
                      AVG(concurrent_viewers),
                      MAX(concurrent_viewers),
                      SUM(concurrent_viewers),
                      MAX(follower_count)
               FROM (SELECT * FROM rising_live_snapshots
                     WHERE collected_at >= ? AND collected_at < ?
                     ORDER BY collected_at)
               GROUP BY chzzk_channel_id""",
            (hour_ts, hour_ts, hour_ts + 3600),
        )
    # 채널별 최초/최종 관측 — 원본을 짧게 자르면 first_seen을 복원할 수 없으므로 누적 보관
    await db.execute(
        """INSERT INTO rising_channel_stats (chzzk_channel_id, first_seen, last_seen, channel_name)
           SELECT chzzk_channel_id, MIN(collected_at), MAX(collected_at), channel_name
           FROM (SELECT * FROM rising_live_snapshots WHERE collected_at = ?)
           GROUP BY chzzk_channel_id
           ON CONFLICT(chzzk_channel_id) DO UPDATE SET
               last_seen    = excluded.last_seen,
               first_seen   = MIN(rising_channel_stats.first_seen, excluded.first_seen),
               channel_name = excluded.channel_name""",
        (now,),
    )
    await db.commit()


async def _prune_old(now: int):
    # 원본 스냅샷은 짧게(기본 24시간), 롤업은 길게(기본 14일) 정리한다.
    # 콤팩트한 사이클 요약(rising_collect_runs, 사이클당 1행)과 채널 통계는 영구 보관 —
    # 시계열 차트의 장기 이력과 데뷔일(first_seen)이 계속 유지되도록 한다.
    raw_cutoff = now - (RAW_RETENTION_HOURS * 3600 if RAW_RETENTION_HOURS > 0
                        else RAW_RETENTION_DAYS * 86400)
    db = await get_db()
    await db.execute("DELETE FROM rising_live_snapshots WHERE collected_at < ?", (raw_cutoff,))
    await db.execute("DELETE FROM rising_hourly_rollup WHERE hour_ts < ?",
                     (now - ROLLUP_RETENTION_DAYS * 86400,))
    await db.commit()


async def collect_once() -> tuple[int, str]:
    """한 번의 수집 사이클. (수집된 라이브 수, 상태 메모) 반환."""
    now = int(time.time())
    t0 = time.perf_counter()
    stats = {"pages": 0, "api_calls": 0}
    el = lambda: int((time.perf_counter() - t0) * 1000)   # noqa: E731
    try:
        async with httpx.AsyncClient() as client:
            lives, fetch_note, stats = await _fetch_all_lives(client)
            if lives:
                # 팔로워 수는 목록 API에 없으므로 상위 채널만 상세 API로 보강
                await _enrich_top(client, lives)
    except Exception as e:
        note = f"fetch 실패: {e}"
        _log(note)
        await _record_run(now, 0, 0, ok=0, note=note, duration_ms=el(),
                          pages=stats["pages"], api_calls=stats["api_calls"])
        return (0, note)

    if not lives:
        note = "라이브 0건 (API 응답 비었거나 방송 없음)"
        await _record_run(now, 0, 0, ok=0, note=note, duration_ms=el(),
                          pages=stats["pages"], api_calls=stats["api_calls"])
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
    # 소요시간은 여기까지(수집 + 보강 + 원본 저장). 롤업/prune은 아래에서 따로 잰다.
    fetch_ms = el()
    await _record_run(now, len(lives), total_viewers, ok=1, note=fetch_note,
                      duration_ms=fetch_ms, pages=stats["pages"], api_calls=stats["api_calls"])
    # 롤업은 prune '전에' 만들어야 한다 — prune이 원본을 지운 뒤면 집계할 소스가 없다.
    await _build_rollup(now)
    await _prune_old(now)

    if fetch_note == "page_cap_reached":
        _log(f"경고: MAX_PAGES({MAX_PAGES}) 상한에 도달 — 목록이 잘렸을 수 있습니다. "
             f"RISING_MAX_PAGES를 올리세요.")
    total_ms = el()
    # 주기 단축 판단에 필요한 값 — 사이클 전체 소요시간과 외부 호출 수를 매번 남긴다
    _log(f"수집 완료: {len(lives)}개 라이브, 총 시청자 {total_viewers:,}명 ({fetch_note}) "
         f"| {stats['pages']}페이지 {stats['api_calls']}호출 "
         f"| 수집 {fetch_ms}ms 롤업·정리 포함 {total_ms}ms")
    return (len(lives), "ok")


async def start_collector():
    """백엔드 lifespan에서 백그라운드 태스크로 실행 — COLLECT_INTERVAL마다 수집."""
    _log(f"시작 (interval={COLLECT_INTERVAL}s, page_size={PAGE_SIZE}, max_pages={MAX_PAGES})")
    await _load_profiles()  # DB에 저장된 프로필 이미지를 메모리로 복원(재시작 대응)
    while True:
        started = time.perf_counter()
        try:
            await collect_once()
        except Exception as e:
            _log(f"수집 루프 예외: {e}")
        # '완료 후 COLLECT_INTERVAL 대기'가 아니라 '시작 간격 고정'이다.
        # 예전 방식은 사이클이 2분 걸리면 실제 시작 간격이 interval+2분이 되어,
        # 환경변수를 300으로 낮춰도 5분 주기가 되지 않는다(약 7분).
        elapsed = time.perf_counter() - started
        # 사이클이 주기를 넘겨도 곧바로 다시 시작하지는 않는다 — 외부 API를 쉬지 않고
        # 때리게 되므로 최소 간격을 남긴다.
        wait = max(MIN_GAP_SECONDS, COLLECT_INTERVAL - elapsed)
        if elapsed > COLLECT_INTERVAL:
            _log(f"경고: 사이클 {elapsed:.0f}s > 주기 {COLLECT_INTERVAL}s "
                 f"— 주기가 너무 짧습니다(다음 시작까지 {wait:.0f}s 대기)")
        await asyncio.sleep(wait)
