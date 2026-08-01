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
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from database import DB_PATH, get_db
from utils.db_write import db_write_isolated

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
# 값이 그대로인 행의 updated_at을 다시 밀기까지 두는 간격. 정리 기준(30일)보다
# 훨씬 짧아야 하고(안 그러면 살아 있는 행이 지워진다), 하루 주기보다는 길어야
# 한다(안 그러면 매일 전체 행을 다시 쓴다).
PROFILE_TOUCH_INTERVAL_SECONDS = int(
    float(os.getenv("PROFILE_TOUCH_INTERVAL_DAYS", "7")) * 86400)
PROFILE_RETENTION_SECONDS = int(
    float(os.getenv("PROFILE_RETENTION_DAYS", "30")) * 86400)
assert PROFILE_TOUCH_INTERVAL_SECONDS * 2 < PROFILE_RETENTION_SECONDS, (
    "갱신 간격이 정리 기준에 너무 가까우면 살아 있는 프로필이 지워진다")
# `IN (?,?,...)` 한 문장에 넣을 id 개수. **런타임 한도를 믿고 정하지 않는다.**
# 같은 파이썬에서도 재는 방법에 따라 값이 다르게 나온다(실측: SELECT 형태는
# 2,000 = SQLITE_MAX_COLUMN, IN 절은 250,000). 배포 이미지·SQLite 버전이 바뀌면
# 999로 떨어질 수도 있으므로, 어느 빌드에서도 안전한 값을 고정한다.
# 문장 하나의 총 변수 = PROFILE_TOUCH_CHUNK + 2 (SET의 now, 조건의 touch_before).
PROFILE_TOUCH_CHUNK = 900


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
        # **바뀐 행만 쓴다.** 예전에는 메모리에 있는 전부(약 2만 행)를 매일 한 번에
        # UPSERT했고, 그 한 트랜잭션이 끝날 때까지 공유 연결이 잠겨 있었다. 같은
        # 연결을 쓰는 공개 조회(/main)까지 그 뒤에 줄을 서서 10초를 넘긴 적이 있다.
        # 프로필 이미지는 거의 바뀌지 않으므로 실제로 쓸 행은 매우 적다.
        # 배치 COMMIT으로 쪼개지 않는 이유: 정리(DELETE)와 갱신이 갈라지면 중간
        # 중단 시 '30일 지난 행은 지웠는데 갱신은 안 된' 상태가 남는다. 트랜잭션은
        # 하나로 두고 **쓰는 양 자체를 줄인다.**
        known = {r["chzzk_channel_id"]: r["image_url"] for r in await (
            await db.execute("SELECT chzzk_channel_id, image_url FROM channel_profiles")
        ).fetchall()}
        changed = [(cid, url, now) for cid, url in _LATEST_IMAGES.items()
                   if url and known.get(cid) != url]
        # 값이 같아도 updated_at은 밀어 줘야 30일 정리에 걸리지 않는다. 다만
        # **매일 2만 행을 다시 쓰면 줄인 의미가 없다**(실측: 바뀐 행만 UPSERT해도
        # 시각 갱신 때문에 소요가 그대로였다). 정리 기준이 30일이고 이 함수는
        # 하루 한 번 도니, 최근 7일 안에 이미 밀어 둔 행은 건드리지 않아도
        # 안전하다 — 아래 UPDATE의 `updated_at < ?`가 그 행들을 걸러 낸다.
        touch = [cid for cid, url in _LATEST_IMAGES.items()
                 if url and known.get(cid) == url]
        touch_before = now - PROFILE_TOUCH_INTERVAL_SECONDS
        if changed:
            await db.executemany(
                """INSERT INTO channel_profiles(chzzk_channel_id, image_url, updated_at)
                   VALUES(?,?,?)
                   ON CONFLICT(chzzk_channel_id) DO UPDATE SET
                       image_url=excluded.image_url, updated_at=excluded.updated_at""",
                changed)
        # SQL 문은 chunk로 나누되 **COMMIT은 나누지 않는다** — 아래 commit 하나가
        # 갱신·시각밀기·정리 전체를 덮는다. 중간 chunk에서 실패하면 그때까지의
        # chunk도 함께 되돌아가고, 다음 일일 실행이 처음부터 다시 시도한다.
        touched = 0
        for i in range(0, len(touch), PROFILE_TOUCH_CHUNK):
            chunk = touch[i:i + PROFILE_TOUCH_CHUNK]
            qs = ",".join("?" for _ in chunk)
            cur = await db.execute(
                "UPDATE channel_profiles SET updated_at=? "
                f"WHERE chzzk_channel_id IN ({qs}) AND updated_at < ?",
                (now, *chunk, touch_before))
            touched += cur.rowcount or 0
        await db.execute("DELETE FROM channel_profiles WHERE updated_at < ?",
                         (now - PROFILE_RETENTION_SECONDS,))
        await db.commit()
        _log(f"프로필 저장: 갱신 {len(changed)}건 / 시각만 {touched}건 "
             f"(후보 {len(touch)}건 중) — 실제 쓴 행 {len(changed) + touched}")
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



# ── 스냅샷 + 성공 회차 저장 (원자적) ────────────────────────────────────────
# 실측(2026-08-01 23:48:19 KST): 수집은 성공했는데 저장이 `database is locked`로
# 죽었고, 그 예외가 `start_collector`까지 올라가 **수집한 5,582건이 통째로 버려졌다.**
# 다음 회차는 10분 뒤라 화면은 `20.4분 전 확인 · 지연`이 됐다.
#
# 고친 지점은 세 가지다.
#   1) 네트워크는 이미 끝났으므로 **DB 저장만** 재시도한다. API를 다시 부르지 않는다.
#   2) 스냅샷과 성공 회차 기록을 **한 트랜잭션**으로 묶는다. 예전에는 커밋이 둘로
#      나뉘어 있어, 스냅샷만 있고 성공 회차가 없으면 화면이 그 데이터를 쓰지 않았다.
#   3) 롤업·정리는 화면 게시의 필수 조건이 아니다 — 따로 떼어 실패해도 이미 게시된
#      최신 스냅샷을 되돌리지 않는다.
#
# 예산 근거(실측, 5,582행 10회): DELETE+INSERT p50 42ms / max 48ms,
# 연결·커밋·정리 포함 p50 53ms / max 78ms. 쓰기 자체는 0.1초 안이고 남는 것은
# **다른 연결의 잠금이 풀리기를 기다리는 시간**이다. 관측된 경쟁 쓰기 보유가
# 최대 1.6초이므로 그 창이 몇 번 겹쳐도 넘길 수 있게 8초를 준다.
# 10분 주기에 비하면 짧아 다음 회차를 밀지 않는다.
def _bounded(env: str, default, lo, hi, cast):
    """환경변수를 범위 안에서만 받는다. **잘못된 값은 기본값으로 대체**한다.

    기동 실패로 만들지 않는 이유: 이 값들은 재시도 손잡이라, 오타 하나로 수집기가
    아예 안 뜨는 쪽이 더 위험하다. 대신 무엇을 무시했는지 남긴다(값만, 비밀정보 없음).
    """
    raw = os.getenv(env, "").strip()
    if not raw:
        return default
    try:
        v = cast(raw)
    except (TypeError, ValueError):
        _log(f"{env}={raw!r} 를 해석할 수 없어 {default}를 씁니다.")
        return default
    if v != v or v in (float("inf"), float("-inf")):     # NaN·무한대
        _log(f"{env}={raw!r} 가 유한한 값이 아니라 {default}를 씁니다.")
        return default
    if not (lo <= v <= hi):
        _log(f"{env}={v} 가 허용 범위[{lo}, {hi}] 밖이라 {default}를 씁니다.")
        return default
    return v


SNAPSHOT_TX_BUDGET_SECONDS = _bounded(
    "RISING_SNAPSHOT_TX_BUDGET_SECONDS", 8.0, 0.5, 60.0, float)
SNAPSHOT_TX_BUSY_TIMEOUT_MS = _bounded(
    "RISING_SNAPSHOT_TX_BUSY_TIMEOUT_MS", 2000, 50, 10_000, int)
SNAPSHOT_TX_ATTEMPTS = _bounded("RISING_SNAPSHOT_TX_ATTEMPTS", 3, 1, 10, int)
# 예산(8초)을 넘겨도 **같은 rows로** 다시 저장해 본다. 외부 API는 부르지 않는다.
# 간격 근거: 관측된 경쟁 쓰기 보유가 최대 1.6초라 5초면 대개 풀리고, 그래도 안 되면
# 더 긴 경합(스윕 회차 전환 등)을 넘기도록 15·30초를 둔다.
# 최악 = 8 + 5+8 + 15+8 + 30+8 = 82초. 수집(실측 48초)을 더해도 약 130초로
# 수집 주기 600초의 4분의 1이 안 된다 — 다음 정규 회차를 밀지 않는다.
def _recovery_waits() -> tuple:
    """후속 재시도 간격. **유한한 비음수만** 받고, 총 복구 시간이 수집 주기의
    절반을 넘지 않도록 뒤에서부터 잘라낸다 — 복구가 다음 회차를 잡아먹으면 안 된다."""
    default = (5.0, 15.0, 30.0)
    raw = os.getenv("RISING_SNAPSHOT_RECOVERY_WAITS", "").strip()
    waits = default
    if raw:
        try:
            parsed = [float(x) for x in raw.split(",") if x.strip()]
        except (TypeError, ValueError):
            _log(f"RISING_SNAPSHOT_RECOVERY_WAITS={raw!r} 를 해석할 수 없어 {default}를 씁니다.")
            parsed = None
        if parsed is None or not parsed or len(parsed) > 10 or any(
                w != w or w in (float("inf"), float("-inf")) or not (0 <= w <= 120)
                for w in parsed):
            _log(f"RISING_SNAPSHOT_RECOVERY_WAITS={raw!r} 가 유효하지 않아 {default}를 씁니다.")
        else:
            waits = tuple(parsed)
    # 최악 = 시도 횟수 × 예산 + 대기 합. 주기의 절반을 넘으면 뒤 항목을 버린다.
    ceiling = COLLECT_INTERVAL / 2
    while waits and (SNAPSHOT_TX_BUDGET_SECONDS * (len(waits) + 1) + sum(waits)) > ceiling:
        _log(f"복구 대기 {waits} 는 수집 주기({COLLECT_INTERVAL}s)의 절반을 넘겨 "
             f"마지막 항목을 뺍니다.")
        waits = waits[:-1]
    return waits


SNAPSHOT_RECOVERY_WAITS = _recovery_waits()


def _step_error(step: str, e: BaseException, **extra):
    """단계별 실패를 구조화해 남긴다. 예전에는 `수집 루프 예외: database is locked`
    한 줄이라 어느 단계인지 알 수 없었다."""
    locked = "database is locked" in str(e).lower() or "database is busy" in str(e).lower()
    payload = {"event": "rising_cycle_step_error", "step": step,
               "error_type": "database_locked" if locked else "unexpected",
               "retryable": locked, "detail": str(e)[:160]}
    payload.update(extra)
    print(f"[rising_collector] {json.dumps(payload, ensure_ascii=False)}", flush=True)


async def _persist_snapshot_and_run(conn, *, collected_at: int, rows: list,
                                    total_viewers: int, note: str,
                                    duration_ms: int, pages: int, api_calls: int):
    """스냅샷 전체 + 성공 회차를 **하나의 트랜잭션**으로 쓴다.

    재시도 멱등성은 `DELETE` 후 다시 넣는 것으로 만든다. `rising_live_snapshots`에는
    (collected_at, channel) 유니크가 없어서 `INSERT OR IGNORE`로는 **일부만 들어간
    상태가 성공처럼 보인다** — 그건 부분 스냅샷을 게시하는 것과 같다.
    """
    await conn.execute(
        "DELETE FROM rising_live_snapshots WHERE collected_at=?", (collected_at,))
    await conn.executemany(
        """INSERT INTO rising_live_snapshots
               (collected_at, chzzk_channel_id, channel_name, follower_count,
                concurrent_viewers, category_id, category_name, live_title,
                open_date, adult, tags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    # 같은 collected_at으로 실패 회차(ok=0)가 먼저 기록돼 있을 수 있다 —
    # INSERT OR IGNORE면 그 실패가 남아 화면이 이 회차를 계속 무시한다.
    await conn.execute(
        """INSERT INTO rising_collect_runs
               (collected_at, live_count, total_viewers, ok, note,
                duration_ms, pages, api_calls)
           VALUES (?,?,?,1,?,?,?,?)
           ON CONFLICT(collected_at) DO UPDATE SET
               live_count=excluded.live_count, total_viewers=excluded.total_viewers,
               ok=1, note=excluded.note, duration_ms=excluded.duration_ms,
               pages=excluded.pages, api_calls=excluded.api_calls""",
        (collected_at, len(rows), total_viewers, note[:500],
         int(duration_ms), int(pages), int(api_calls)))


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

    # 프로필 이미지는 메모리에 누적한다(사이클마다). **DB 저장은 게시 뒤로 미룬다** —
    # 예전에는 여기서 먼저 저장했는데, 그게 `database is locked`로 죽으면 이미 수집한
    # lives 전체가 버려졌다. 게다가 날짜를 먼저 갱신해서 그날은 다시 시도하지도 않았다.
    _LATEST_IMAGES.update({l["chzzk_channel_id"]: l["channel_image_url"] for l in lives if l.get("channel_image_url")})

    total_viewers = sum(l["concurrent_viewers"] for l in lives)
    rows = [
        (now, l["chzzk_channel_id"], l["channel_name"], l["follower_count"],
         l["concurrent_viewers"], l["category_id"], l["category_name"],
         l["live_title"], l["open_date"], l["adult"], l.get("tags", ""))
        for l in lives
    ]
    # 종료 사유를 note에 남긴다 — page_cap_reached면 MAX_PAGES를 더 올려야 한다는 신호다.
    # 소요시간은 여기까지(수집 + 보강). 저장·롤업·prune은 아래에서 따로 잰다.
    fetch_ms = el()

    # **네트워크는 끝났다.** 여기서부터는 DB 저장만 재시도한다 — 잠금 때문에
    # 114페이지를 다시 부르는 일은 없다. 전용 연결이라 예산이 곧 상한이다.
    persist_t0 = time.perf_counter()
    ok = False
    for i, wait in enumerate((0.0, *SNAPSHOT_RECOVERY_WAITS)):
        if wait:
            # **여기서 기다리는 동안 외부 API를 부르지 않는다.** 같은 rows를 그대로 쓴다.
            # asyncio.sleep이라 종료·취소 신호에 즉시 반응한다.
            await asyncio.sleep(wait)
        ok = await db_write_isolated(
            DB_PATH,
            lambda conn: _persist_snapshot_and_run(
                conn, collected_at=now, rows=rows, total_viewers=total_viewers,
                note=fetch_note, duration_ms=fetch_ms,
                pages=stats["pages"], api_calls=stats["api_calls"]),
            what="rising_snapshot_and_run",
            busy_timeout_ms=SNAPSHOT_TX_BUSY_TIMEOUT_MS,
            attempts=SNAPSHOT_TX_ATTEMPTS,
            budget_seconds=SNAPSHOT_TX_BUDGET_SECONDS)
        if ok:
            if i:
                _log(json.dumps({"event": "rising_persist_recovered", "attempt": i + 1,
                                 "waited_s": wait, "live_count": len(lives),
                                 "elapsed_ms": int((time.perf_counter() - persist_t0) * 1000)},
                                ensure_ascii=False))
            break
        _step_error("persist_snapshot_and_run", Exception("database is locked"),
                    attempt=i + 1, of=len(SNAPSHOT_RECOVERY_WAITS) + 1,
                    duration_ms=int((time.perf_counter() - persist_t0) * 1000),
                    pages=stats["pages"], live_count=len(lives),
                    api_calls=stats["api_calls"])
    if not ok:
        # 모든 복구 시도가 끝났다. **예외를 올리지 않는다** — 올리면 수집 루프의
        # 바깥 except로 가서 어느 단계였는지도 남지 않는다(실측 23:48:19).
        note = "저장 실패: database is locked"
        # 실패 기록도 **최선 노력**이다 — 이것마저 공유 연결에서 잠기면 예외가
        # 다시 루프 바깥으로 올라간다(그게 원래 증상이었다). 남기지 못해도
        # 다음 회차가 새 collected_at으로 정상 게시하면 화면은 회복된다.
        try:
            await db_write_isolated(
                DB_PATH,
                lambda conn: conn.execute(
                    """INSERT INTO rising_collect_runs
                           (collected_at, live_count, total_viewers, ok, note,
                            duration_ms, pages, api_calls)
                       VALUES (?,?,?,0,?,?,?,?)
                       ON CONFLICT(collected_at) DO NOTHING""",
                    (now, len(lives), total_viewers, note[:500], int(fetch_ms),
                     int(stats["pages"]), int(stats["api_calls"]))),
                what="rising_failed_run", busy_timeout_ms=200,
                attempts=1, budget_seconds=0.5)
        except Exception as e:                      # noqa: BLE001
            _step_error("record_failed_run", e)
        return (0, note)

    # 여기까지 오면 **최신 라이브는 이미 화면에서 쓸 수 있다.**
    # 아래 롤업·정리는 게시의 필수 조건이 아니므로, 실패해도 위 성공을 되돌리지 않는다.
    # 롤업은 prune '전에' 만들어야 한다 — prune이 원본을 지운 뒤면 집계할 소스가 없다.
    for step, fn in (("build_rollup", _build_rollup), ("prune", _prune_old)):
        try:
            await fn(now)
        except Exception as e:                      # noqa: BLE001
            # 다음 회차가 같은 작업을 다시 한다(둘 다 멱등).
            _step_error(step, e, live_count=len(lives))

    # 프로필 저장은 하루 1회면 충분하고 화면 게시와 무관하다.
    # **성공했을 때만 날짜를 갱신한다** — 실패한 날을 완료로 표시하면 그날 다시
    # 시도하지 않아 프로필이 하루 통째로 밀린다.
    global _LAST_PERSIST_DATE
    today_kst = datetime.now(_KST).date()
    if _LAST_PERSIST_DATE != today_kst:
        try:
            await _persist_profiles()
            _LAST_PERSIST_DATE = today_kst
        except Exception as e:                      # noqa: BLE001
            _step_error("persist_profiles", e, live_count=len(lives))

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
