"""CHZZK Rising — 공개(비로그인) 집계 API (Track A 기반).

모든 집계는 rising_live_snapshots(원천 시계열)에서 계산한다. 인증 불필요 —
익명 트래픽/크롤러가 접근하므로 무거운 재계산은 '최신 수집 사이클' 기준으로만 한다.
데이터가 쌓이기 전(수집 시작 직후)에는 일부 지표(라이징/히트맵)가 비어 있을 수 있다.
"""
import asyncio
import hashlib
import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import httpx
from chzzk_channel_history import get_channel_history
from fastapi import APIRouter, Query, Request, Response
from rising_collector import _fetch_channel_meta, latest_image

from database import get_db

_CHZZK_API = "https://api.chzzk.naver.com"
_CHZZK_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

_KST = timezone(timedelta(hours=9))


def _kst_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, _KST).date().isoformat()


def _kst_hour(ts: int) -> int:
    return datetime.fromtimestamp(ts, _KST).hour


def _kst_week(ts: int) -> tuple[str, int]:
    d = datetime.fromtimestamp(ts, _KST).isocalendar()
    return (f"{d[0]}-W{d[1]:02d}", int(datetime.fromtimestamp(ts, _KST).timestamp()))

router = APIRouter(prefix="/api/rising", tags=["rising"])

# 체급 구간(동시 시청자 기준) — 기획서 정의
TIERS = [
    ("large",  "대기업",        1000, None),   # 1000명+
    ("mid",    "허리층",         100,  999),   # 100~999
    ("rising", "라이징/하꼬",      1,   99),    # 1~99
]


async def _latest_run_ts() -> int | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT collected_at FROM rising_collect_runs WHERE ok=1 ORDER BY collected_at DESC LIMIT 1"
    )).fetchone()
    return int(row["collected_at"]) if row else None


def _collect_interval() -> int:
    """수집기의 현재 주기(초). 수집기 모듈이 import 시점에 확정한 값을 그대로 읽는다."""
    from rising_collector import COLLECT_INTERVAL
    return int(COLLECT_INTERVAL)


@router.get("/status")
async def status():
    """수집기 헬스체크 — 마지막 수집 시각/건수/성공 여부와 누적 스냅샷 수."""
    db = await get_db()
    last = await (await db.execute(
        "SELECT collected_at, live_count, total_viewers, ok, note "
        "FROM rising_collect_runs ORDER BY collected_at DESC LIMIT 1"
    )).fetchone()
    total_snap = await (await db.execute(
        "SELECT COUNT(*) AS c FROM rising_live_snapshots"
    )).fetchone()
    runs = await (await db.execute(
        "SELECT COUNT(*) AS c FROM rising_collect_runs WHERE ok=1"
    )).fetchone()
    # 수집 주기를 줄여도 되는지 판단할 근거 — 사이클 소요시간 분포와 외부 호출 수.
    # 주기(초)보다 p95가 크면 주기를 줄이면 안 된다(사이클이 겹치거나 계속 밀린다).
    since = int(time.time()) - 24 * 3600
    dur = [int(r["d"]) for r in await (await db.execute(
        "SELECT duration_ms AS d FROM rising_collect_runs "
        "WHERE ok=1 AND duration_ms > 0 AND collected_at >= ? ORDER BY duration_ms",
        (since,))).fetchall()]

    def pct(p: float) -> int | None:
        if not dur:
            return None
        return dur[min(len(dur) - 1, int(len(dur) * p))]

    calls = await (await db.execute(
        "SELECT AVG(api_calls) AS a, MAX(api_calls) AS m, AVG(pages) AS p "
        "FROM rising_collect_runs WHERE ok=1 AND api_calls > 0 AND collected_at >= ?",
        (since,))).fetchone()
    interval = _collect_interval()
    p95 = pct(0.95)
    return {
        "last_run": dict(last) if last else None,
        "total_snapshots": total_snap["c"] if total_snap else 0,
        "successful_runs": runs["c"] if runs else 0,
        "cycle_24h": {
            "samples": len(dur),
            "interval_seconds": interval,
            "duration_ms": {"p50": pct(0.5), "p95": p95, "max": dur[-1] if dur else None},
            "avg_api_calls": round(calls["a"], 1) if calls and calls["a"] else None,
            "max_api_calls": int(calls["m"]) if calls and calls["m"] else None,
            "avg_pages": round(calls["p"], 1) if calls and calls["p"] else None,
            # 주기를 절반으로 줄였을 때 p95가 여전히 안전한지 — 판단을 화면에 맡기지 않는다
            "p95_uses_pct_of_interval": round(p95 / 10 / interval, 1) if p95 and interval else None,
            "safe_to_halve": (p95 is not None and interval > 0
                              and p95 / 1000 < interval / 2 * 0.5),
        },
        "server_time": int(time.time()),
    }


@router.get("/overview")
async def overview():
    """최신 수집 사이클 기준: 체급 분포 + 틈새(블루오션) 게임 TOP + 전체 규모 요약."""
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "tiers": [], "blue_ocean": [], "summary": None}

    db = await get_db()

    # ── 체급 분포 ──────────────────────────────────────────────────────────
    tiers = []
    total_channels = 0
    for key, label, lo, hi in TIERS:
        if hi is None:
            cond, args = "concurrent_viewers >= ?", (ts, lo)
        else:
            cond, args = "concurrent_viewers BETWEEN ? AND ?", (ts, lo, hi)
        row = await (await db.execute(
            f"SELECT COUNT(*) AS c, COALESCE(SUM(concurrent_viewers),0) AS v "
            f"FROM rising_live_snapshots WHERE collected_at=? AND {cond}",
            args
        )).fetchone()
        cnt = row["c"] if row else 0
        total_channels += cnt
        tiers.append({"key": key, "label": label, "channels": cnt, "viewers": row["v"] if row else 0})

    for t in tiers:
        t["channel_share"] = round(t["channels"] / total_channels * 100, 1) if total_channels else 0.0

    # ── 틈새(블루오션) 게임 TOP10 ─────────────────────────────────────────
    # 블루오션 지수 = 카테고리 시청자 합 / 방송 수 (방송 1개당 시청 유입 효율).
    # 방송 수가 너무 적은(<3) 카테고리는 표본 노이즈라 제외.
    cat_rows = await (await db.execute(
        """SELECT category_name,
                  COUNT(*)                       AS lives,
                  COALESCE(SUM(concurrent_viewers),0) AS viewers
           FROM rising_live_snapshots
           WHERE collected_at=? AND category_name != ''
           GROUP BY category_name
           HAVING lives >= 3
           ORDER BY (CAST(viewers AS REAL) / lives) DESC
           LIMIT 10""",
        (ts,)
    )).fetchall()
    blue_ocean = [
        {
            "category": r["category_name"],
            "lives": r["lives"],
            "viewers": r["viewers"],
            "blue_ocean_index": round(r["viewers"] / r["lives"], 1) if r["lives"] else 0.0,
        }
        for r in cat_rows
    ]

    # ── 전체 규모 요약 ────────────────────────────────────────────────────
    summ = await (await db.execute(
        "SELECT COUNT(*) AS lives, COALESCE(SUM(concurrent_viewers),0) AS viewers "
        "FROM rising_live_snapshots WHERE collected_at=?",
        (ts,)
    )).fetchone()

    # ── KPI 증감(직전 수집 / 24시간 전 동시간) + 수집 이력 범위 ────────────
    def pct(cur, prev):
        return round((cur - prev) / prev * 100, 1) if prev else None

    last2 = await (await db.execute(
        "SELECT total_viewers, live_count FROM rising_collect_runs WHERE ok=1 ORDER BY collected_at DESC LIMIT 2"
    )).fetchall()
    cur_tv = last2[0]["total_viewers"] if last2 else 0
    cur_lc = last2[0]["live_count"]    if last2 else 0
    prev_tv = last2[1]["total_viewers"] if len(last2) > 1 else None
    prev_lc = last2[1]["live_count"]    if len(last2) > 1 else None

    target = ts - 86400
    r24 = await (await db.execute(
        "SELECT total_viewers, live_count FROM rising_collect_runs "
        "WHERE ok=1 AND ABS(collected_at - ?) <= 5400 ORDER BY ABS(collected_at - ?) ASC LIMIT 1",
        (target, target)
    )).fetchone()

    deltas = {
        "total_viewers": {"prev": pct(cur_tv, prev_tv), "d24h": pct(cur_tv, r24["total_viewers"]) if r24 else None},
        "live_count":    {"prev": pct(cur_lc, prev_lc), "d24h": pct(cur_lc, r24["live_count"])    if r24 else None},
    }

    first = await (await db.execute(
        "SELECT MIN(collected_at) AS first_at FROM rising_collect_runs WHERE ok=1"
    )).fetchone()
    history_hours = round((ts - int(first["first_at"])) / 3600, 1) if first and first["first_at"] else 0.0

    return {
        "collected_at": ts,
        "tiers": tiers,
        "blue_ocean": blue_ocean,
        "summary": {"live_count": summ["lives"], "total_viewers": summ["viewers"]} if summ else None,
        "deltas": deltas,
        "history_hours": history_hours,
    }


# 기간 필터: (윈도우 초, 버킷 초 — 0이면 원본 10분 그대로)
# 버킷을 나누는 기준은 '포인트 수'다. 원본 10분 그대로 72시간을 그리면 432포인트라
# 경로가 톱니처럼 뭉개져 추세가 오히려 안 보인다(가독 상한은 150포인트 부근).
_TS_RANGES: dict[str, tuple[int, int]] = {
    "live": (24 * 3600, 0),       # 롤링 24시간, 원본 10분 간격 → 144포인트
    "24h":  (24 * 3600, 3600),    # 최근 24시간, 1시간 평균     → 24포인트
    "48h":  (48 * 3600, 1800),    # 최근 48시간, 30분 평균      → 96포인트
    "72h":  (72 * 3600, 3600),    # 최근 72시간, 1시간 평균     → 72포인트
    "7d":   (7 * 86400, 3600),    # 최근 7일, 1시간 평균        → 168포인트
}
_TS_RANGE_KEYS = tuple(_TS_RANGES)
# 원본 수집 주기(초). 경로를 끊을 간격 판정에 쓴다.
_TS_RAW_STEP = 600

# 그래프에 넣을 수 있는 사이클의 조건.
#   ok=0        : fetch 실패 / 라이브 0건 → (0,0)으로 기록된다
#   live_count  : 성공 경로는 항상 1 이상. 0이면 비정상 기록이므로 뺀다
#   note        : page_cap_reached = MAX_PAGES 상한에 걸려 목록이 잘린 부분 성공.
#                 합계가 과소 집계되어 그래프에 '가짜 급락'을 만든다.
# 이 조건으로 빠진 사이클은 0으로 메우지 않고 '구멍'으로 남긴다(경로를 끊는다).
_TS_QUALITY = ("ok = 1 AND live_count > 0 AND total_viewers > 0 "
               "AND note <> 'page_cap_reached'")

_ts_cache: dict[str, tuple[float, dict]] = {}
_TS_TTL = 60   # 수집 주기(기본 600초)보다 훨씬 짧아 최신성이 상하지 않는다


@router.get("/timeseries")
async def timeseries(
    range: str = Query("24h", pattern=r"^(live|24h|48h|72h|7d)$",
                       description="live|24h|48h|72h|7d"),
):
    """전체 시청자·라이브 방송 수 시계열 — 꺾은선 그래프용.

    rising_collect_runs(사이클당 1행, 영구 보관)에서 읽으므로 이력이 계속 누적된다.
    rising_live_snapshots/rising_hourly_rollup과 달리 이 테이블은 프루닝하지 않아
    48h/72h도 추가 수집 없이 바로 나온다.

    정상 완료된 사이클만 포함한다(_TS_QUALITY). 빠진 구간은 0으로 채우지 않고
    포인트 자체를 비워, 프론트가 step_seconds 간격을 보고 선을 끊게 한다.
    """
    # pattern 검증을 통과한 값만 오지만, 캐시 키는 정규화된 값으로 한 번 더 좁힌다
    key = range if range in _TS_RANGES else "24h"
    hit = _ts_cache.get(key)
    now_s = time.time()
    if hit and now_s - hit[0] < _TS_TTL:
        return hit[1]

    window, bucket = _TS_RANGES[key]
    now = int(now_s)
    since = now - window
    db = await get_db()
    if bucket == 0:
        rows = await (await db.execute(
            f"""SELECT collected_at AS t, live_count, total_viewers, 1 AS samples
                FROM rising_collect_runs
                WHERE {_TS_QUALITY} AND collected_at >= ?
                ORDER BY collected_at ASC""",
            (since,)
        )).fetchall()
    else:
        rows = await (await db.execute(
            f"""SELECT (collected_at/?)*?                     AS t,
                       CAST(AVG(live_count) AS INTEGER)       AS live_count,
                       CAST(AVG(total_viewers) AS INTEGER)    AS total_viewers,
                       COUNT(*)                               AS samples
                FROM rising_collect_runs
                WHERE {_TS_QUALITY} AND collected_at >= ?
                GROUP BY collected_at/?
                ORDER BY t ASC""",
            (bucket, bucket, since, bucket)
        )).fetchall()

    step = bucket or _TS_RAW_STEP
    points = [{
        "t": int(r["t"]),
        "live_count": int(r["live_count"]),
        "total_viewers": int(r["total_viewers"]),
        "samples": int(r["samples"]),
        # 마지막 버킷은 아직 채워지는 중이라 평균이 확정값이 아니다 → '집계 중'으로 구분
        "partial": bool(bucket) and int(r["t"]) + bucket > now,
    } for r in rows]

    # 확보된 이력 — 요청 창보다 짧으면 있는 구간까지만 그리고 그 사실을 알린다
    first = await (await db.execute(
        f"SELECT MIN(collected_at) AS f FROM rising_collect_runs WHERE {_TS_QUALITY}"
    )).fetchone()
    first_at = int(first["f"]) if first and first["f"] is not None else None
    history_hours = round((now - first_at) / 3600, 1) if first_at else 0.0

    # 품질 조건으로 제외한 사이클 수 — 그래프의 빈 구간이 '수집 실패'였음을 설명한다
    ex = await (await db.execute(
        f"""SELECT COUNT(*) AS n FROM rising_collect_runs
            WHERE collected_at >= ? AND NOT ({_TS_QUALITY})""", (since,)
    )).fetchone()

    result = {
        "range": key,
        "window_seconds": window,
        "bucket_seconds": bucket,
        "step_seconds": step,          # 이 간격을 크게 벗어나면 프론트가 선을 끊는다
        "history_hours": history_hours,
        # 이력이 요청 창보다 짧다 — 버튼을 막지 않고 확보된 구간까지만 보여준다
        "truncated": bool(first_at) and first_at > since,
        "excluded_points": int(ex["n"]) if ex else 0,
        "points": points,
    }
    if len(_ts_cache) > len(_TS_RANGE_KEYS):
        _ts_cache.clear()
    _ts_cache[key] = (now_s, result)
    return result


@router.get("/live-ranking")
async def live_ranking(limit: int = 200):
    """최신 수집 사이클의 실시간 방송 랭킹 — 동시 시청자 내림차순 상위 N개.

    소프트콘식 랭킹 테이블의 데이터 소스. 프론트에서 정렬/검색/페이지네이션하도록
    상위 N개를 한 번에 내려준다.
    """
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "streamers": []}
    db = await get_db()

    # 직전 수집 사이클(시청자 증감용) + 약 24시간 전 사이클(팔로워 신규 유입용)
    last2 = await (await db.execute(
        "SELECT collected_at FROM rising_collect_runs WHERE ok=1 ORDER BY collected_at DESC LIMIT 2"
    )).fetchall()
    prev_ts = int(last2[1]["collected_at"]) if len(last2) > 1 else None
    t24row = await (await db.execute(
        "SELECT collected_at FROM rising_collect_runs "
        "WHERE ok=1 AND ABS(collected_at - ?) <= 5400 ORDER BY ABS(collected_at - ?) ASC LIMIT 1",
        (ts - 86400, ts - 86400)
    )).fetchone()
    t24 = int(t24row["collected_at"]) if t24row else None

    rows = await (await db.execute(
        """SELECT n.chzzk_channel_id, n.channel_name, n.concurrent_viewers,
                  n.category_name, n.open_date, n.follower_count, n.live_title, n.adult,
                  p.concurrent_viewers AS viewers_prev,
                  f.follower_count     AS follower_prev24h
           FROM rising_live_snapshots n
           LEFT JOIN rising_live_snapshots p
             ON p.chzzk_channel_id = n.chzzk_channel_id AND p.collected_at = ?
           LEFT JOIN rising_live_snapshots f
             ON f.chzzk_channel_id = n.chzzk_channel_id AND f.collected_at = ?
           WHERE n.collected_at = ?
           ORDER BY n.concurrent_viewers DESC
           LIMIT ?""",
        (prev_ts if prev_ts is not None else -1, t24 if t24 is not None else -1, ts, limit)
    )).fetchall()
    streamers = [
        {
            "rank": i + 1,
            "chzzk_channel_id":   r["chzzk_channel_id"],
            "channel_name":       r["channel_name"],
            "channel_image_url":  latest_image(r["chzzk_channel_id"]),  # DB 아님 — 메모리 맵
            "concurrent_viewers": r["concurrent_viewers"],
            "viewers_prev":       r["viewers_prev"],       # 직전 사이클(없으면 None)
            "category_name":      r["category_name"],
            "open_date":          r["open_date"],
            "follower_count":     r["follower_count"],
            "follower_prev24h":   r["follower_prev24h"],   # 약 24h 전(없으면 None)
            "live_title":         r["live_title"],
            "adult":              bool(r["adult"]),
        }
        for i, r in enumerate(rows)
    ]
    return {"collected_at": ts, "streamers": streamers}


# 카테고리 집계 시간창: live(현재 스냅샷) / 1h(1시간 평균) / 24h(24시간 평균)
_CAT_WINDOWS = {"live": 0, "1h": 3600, "24h": 86400}


@router.get("/categories")
async def categories(range: str = "1h", limit: int = 60):
    """카테고리(게임)별 집계 — 시간창(range) 평균 + 점유율 + 1시간 전 대비 증감 + 순위.

    range=live(현재 스냅샷)/1h(최근 1시간 평균)/24h(최근 24시간 평균). 각 창의 카테고리
    동시 시청자(창 내 스냅샷 평균)를 기준으로 점유율/순위를 매기고, 1시간 앞선 창과 비교해 증감.
    """
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "range": range, "categories": []}
    win = _CAT_WINDOWS.get(range, 3600)
    db = await get_db()

    async def agg(a: int, b: int) -> dict:
        rows = await (await db.execute(
            """SELECT category_name, COALESCE(SUM(concurrent_viewers),0) AS sv, COUNT(*) AS cnt
               FROM rising_live_snapshots
               WHERE collected_at BETWEEN ? AND ? AND category_name != ''
               GROUP BY category_name""",
            (a, b)
        )).fetchall()
        nrow = await (await db.execute(
            "SELECT COUNT(*) AS n FROM rising_collect_runs WHERE ok=1 AND collected_at BETWEEN ? AND ?",
            (a, b)
        )).fetchone()
        n = max(1, nrow["n"] if nrow and nrow["n"] else 1)
        # viewers=창 내 평균 동시시청자, lives=평균 방송 수, sv/cnt=방송당 평균
        return {r["category_name"]: {"viewers": r["sv"] / n, "lives": r["cnt"] / n, "sv": r["sv"], "cnt": r["cnt"]}
                for r in rows}

    cur  = await agg(ts - win, ts)
    prev = await agg(ts - win - 3600, ts - 3600)  # 1시간 앞선 동일 창

    total_cur = sum(c["viewers"] for c in cur.values()) or 1
    items = []
    for name, c in cur.items():
        viewers = c["viewers"]
        avg = (c["sv"] / c["cnt"]) if c["cnt"] else 0.0
        p = prev.get(name)
        change = round((viewers - p["viewers"]) / p["viewers"] * 100, 1) if p and p["viewers"] > 0 else None
        items.append({
            "category": name,
            "viewers": round(viewers),
            "lives": round(c["lives"]),
            "avg_viewers": round(avg, 1),
            "blue_ocean_index": round(avg, 1),
            "share": round(viewers / total_cur * 100, 1),
            "change": change,
        })
    items.sort(key=lambda x: x["viewers"], reverse=True)
    for i, it in enumerate(items):
        it["rank"] = i + 1
    return {"collected_at": ts, "range": range if range in _CAT_WINDOWS else "1h", "categories": items[:limit]}


_NEW_TAGS = ("신입", "신규", "첫방송", "하꼬", "뉴비", "초보")

# 신입/하꼬 판정 기준: 보관창 평균 동시 시청자 상한
_NEWCOMER_AVG_MAX = 50
# 인사이트 표본 하한 — 아웃라이어(1~2개 표본)가 대표값이 되는 것을 막는다
_CAT_MIN_LIVES = 3            # 인기 카테고리: 신입 라이브 최소 개수
_GOLDEN_HOUR_MIN_SAMPLES = 10  # 최적 시간대: 시간 버킷당 최소 스냅샷 수
# 팔로워 온디맨드 보강 — 외부 API 호출이라 응답 시간을 좌우한다. 개수와 총 시간을 모두 제한.
_NEWCOMER_ENRICH_N = 80
_NEWCOMER_ENRICH_TIMEOUT = 3.0
# 제목 유입 키워드 — 신입이 자주 쓰는 소통/참여 유도 표현
_TITLE_KEYWORD_LIST = ["시참", "훈수", "소통", "티어", "뉴비", "초보"]
_TITLE_KEYWORDS = re.compile("|".join(_TITLE_KEYWORD_LIST))
_TITLE_MIN_SAMPLES = 5   # 양쪽 그룹이 이보다 적으면 비교하지 않는다
# 사이트맵에 넣을 최소 스냅샷 수 — 한두 번 잡힌 채널은 페이지가 거의 비어 색인 가치가 없다
_SITEMAP_MIN_SNAPS = 12

# ── '신규 & 초기 분석' 두 그룹의 정의 ────────────────────────────────────────
# new   : 첫 방송 후 60일 이내 (방송 경력 기준 — 시청자 규모와 무관)
# small : 최근 평균 동시 시청자 10명 이하 (경력과 무관 — 규모 기준)
# 두 축이 서로 독립이라 한 채널이 양쪽에 다 나올 수 있고, 그게 의도된 동작이다.
_NEW_DEBUT_MAX_DAYS = 60
_SMALL_AVG_MAX = 10
# 성장률 분모 하한 / 표본 하한 — 하꼬 채널의 0.x명 평균이 만드는 +1600% 잡음 방지.
# 스냅샷 간격이 10분이므로 6개 = 최근 7일 중 최소 1시간은 방송한 이력.
_GROWTH_MIN_BASE = 1.0
_GROWTH_MIN_SNAPS = 6
# 빈집 타임 분석에서 '대기업'으로 볼 시간당 평균 시청자 하한과 집계 창
_BIG_AVG_MIN = 1000
_VACANCY_WINDOW_DAYS = 7
_GROUPS = ("new", "small")
# 그룹별로 결과가 다르므로 캐시도 그룹별로 나눈다
_newcomers_cache: dict = {}


async def _first_stream_map(db) -> dict[str, int]:
    """channel_id -> 첫 방송 시각(epoch). chzzk_channel_history(정확한 값) 기준.

    비어 있는 채널은 호출부에서 rising_channel_stats.first_seen(NexBot 최초 트랙킹
    일자)으로 보완한다 — 백필이 아직 안 닿은 채널이 목록에서 통째로 사라지지 않게.
    """
    rows = await (await db.execute(
        "SELECT channel_id, first_live_date_iso FROM chzzk_channel_history "
        "WHERE first_live_date_iso IS NOT NULL"
    )).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        try:
            out[r["channel_id"]] = int(datetime.fromisoformat(r["first_live_date_iso"]).timestamp())
        except (TypeError, ValueError):
            continue
    return out


@router.get("/newcomers")
async def newcomers(limit: int = 100, group: str = "new"):
    """신규 & 초기 스트리머 분석 — 현재 라이브 중인 채널을 두 기준 중 하나로 거른다.

    group=new   : (지금 - 첫 방송일) <= 60일. 첫 방송일은 chzzk_channel_history의
                  정확한 값을 쓰고, 아직 백필되지 않았으면 NexBot 최초 트랙킹 일자로 보완한다.
    group=small : 최근 7일 평균 동시 시청자 10명 이하(방송 경력 무관).

    채팅(소통 화력)은 미수집이라 잠금.
    """
    group = group if group in _GROUPS else "new"
    is_small = group == "small"
    # 시간대 집계에서 '이 그룹에 해당하는 방송'으로 볼 시간당 평균 시청자 상한
    hour_avg_max = _SMALL_AVG_MAX if is_small else _NEWCOMER_AVG_MAX

    now = int(time.time())
    hit = _newcomers_cache.get(group)
    if hit and now - hit[0] < 60:
        return hit[1]

    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "group": group, "streamers": []}
    db = await get_db()

    cur = await (await db.execute(
        """SELECT chzzk_channel_id, channel_name, concurrent_viewers, category_name,
                  open_date, follower_count, live_title, tags
           FROM rising_live_snapshots
           WHERE collected_at=?""",
        (ts,)
    )).fetchall()

    # 채널별 보관창 평균/최근 7일 평균 — 롤업에서 읽는다(원본은 24시간만 보관하므로
    # 원본으로는 7일 평균을 낼 수 없다). 시간 가중 평균이 되도록 sum/snaps로 재집계한다.
    # 예전에는 (a) 롤업 '전체'를 GROUP BY 하는 avg_all과 (b) 7일 GROUP BY 하는 avg7을
    # 따로 실행했다. 롤업 보관이 8일이라 두 결과가 사실상 같은데도 100만 행을 두 번 훑어
    # 각각 2.5초/2.1초가 걸렸다. 7일 기준 한 번만 계산해 둘 다에 쓴다.
    # snaps7(성장률 표본 하한 판정용)도 같은 쿼리에서 함께 뽑는다 — 이걸 위해 롤업을
    # 한 번 더 스캔하면 위에서 없앤 중복 스캔을 그대로 되살리는 셈이 된다.
    _agg7_rows = await (await db.execute(
        "SELECT chzzk_channel_id, "
        "       CAST(SUM(sum_viewers) AS REAL) / NULLIF(SUM(snaps),0) AS avg7, "
        "       SUM(snaps) AS snaps7 "
        "FROM rising_hourly_rollup WHERE hour_ts >= ? GROUP BY chzzk_channel_id",
        (ts - 7 * 86400,)
    )).fetchall()
    agg7 = {r["chzzk_channel_id"]: r["avg7"] for r in _agg7_rows}
    snaps7_map = {r["chzzk_channel_id"]: int(r["snaps7"] or 0) for r in _agg7_rows}
    agg = agg7
    # 데뷔일 소스 두 가지:
    #  ① chzzk_channel_history.first_live_date — 치지직이 주는 정확한 첫 방송일(우선)
    #  ② rising_channel_stats.first_seen      — NexBot이 이 채널을 처음 본 시각(보완)
    # ②는 수집 시작 이후만 알 수 있어 실제보다 늦을 수 있다. 어느 쪽을 썼는지
    # first_stream_source로 함께 내려보내 프론트가 '추정' 여부를 표시할 수 있게 한다.
    first_map = {r["chzzk_channel_id"]: r["first_seen"] for r in await (await db.execute(
        "SELECT chzzk_channel_id, first_seen FROM rising_channel_stats"
    )).fetchall()}
    exact_map = await _first_stream_map(db)

    out = []
    for r in cur:
        cid = r["chzzk_channel_id"]
        avg_all = agg.get(cid, r["concurrent_viewers"])
        exact_first = exact_map.get(cid)
        first_ts = exact_first if exact_first is not None else int(first_map.get(cid, ts))
        debut_days = round((ts - first_ts) / 86400, 1)
        tags = r["tags"] or ""
        tag_new = any(t in tags for t in _NEW_TAGS)

        if is_small:
            # 소형(하꼬): 방송 경력과 무관하게 최근 평균 동시 시청자 10명 이하
            if avg_all is None or avg_all > _SMALL_AVG_MAX:
                continue
        else:
            # 신규: 첫 방송 후 60일 이내
            if debut_days > _NEW_DEBUT_MAX_DAYS:
                continue

        # 성장률 = (현재 시청자 - 최근 7일 평균) / 최근 7일 평균.
        #
        # 분모에 하한(_GROWTH_MIN_BASE)을 둔다. 하꼬 채널은 7일 평균이 0.4명 같은 값이
        # 나오는데, 그대로 나누면 지금 7명일 때 +1650% 처럼 사실상 무한대에 가까운 수치가
        # 찍힌다(수식은 맞지만 분모가 0에 가까워 생기는 잡음이라 순위로 못 쓴다).
        # 하한을 걸면 최대치가 (현재 시청자-1)*100% 로 묶이고, 평균 1명 이상인 채널은
        # 값이 전혀 달라지지 않는다.
        # 표본(_GROWTH_MIN_SNAPS)도 요구한다 — 어제 처음 켠 채널의 몇 개 스냅샷으로
        # 만든 평균은 '평소'가 아니다.
        avg7 = agg7.get(cid, avg_all) or avg_all or r["concurrent_viewers"]
        if avg7 and avg7 > 0 and snaps7_map.get(cid, 0) >= _GROWTH_MIN_SNAPS:
            base = max(float(avg7), _GROWTH_MIN_BASE)
            growth = round((r["concurrent_viewers"] - base) / base * 100, 1)
        else:
            growth = None
        out.append({
            "chzzk_channel_id":   cid,
            "channel_name":       r["channel_name"],
            "channel_image_url":  latest_image(cid),
            "concurrent_viewers": r["concurrent_viewers"],
            "category_name":      r["category_name"],
            "open_date":          r["open_date"],
            "follower_count":     r["follower_count"],
            "avg_viewers":        round(avg_all) if avg_all is not None else r["concurrent_viewers"],
            "growth_rate":        growth,
            "first_seen_days":    debut_days,   # 하위 호환(이름은 유지, 값은 데뷔일 기준)
            "debut_days":         debut_days,
            "first_stream_date":  _kst_date(first_ts),
            "first_stream_source": "CHZZK" if exact_first is not None else "TRACKED",
            "is_new":             debut_days <= 7,
            "tag_new":            tag_new,
            "live_title":         r["live_title"] or "",
            "tags":               [t for t in tags.split(",") if t][:4],
        })

    # 기본 정렬: 급성장순(소통 화력은 채팅 미수집이라 프론트에서 잠금)
    out.sort(key=lambda x: (x["growth_rate"] if x["growth_rate"] is not None else -1e9), reverse=True)

    # 소형 채널은 스냅샷 팔로워가 0(상위 100만 보강)이라, 목록에 실제로 보일 상위 후보만
    # 온디맨드로 보강한다. 예전에는 여기서 '팔로워 100명 이하'로 한 번 더 걸렀지만,
    # 이제 그룹 정의(60일 이내 / 평균 10명 이하)가 필터를 전담하므로 팔로워는 표시용이다.
    # out 자체는 자르지 않는다 — 요약/카테고리/체급 분포는 필터를 통과한 전체 기준이어야 한다.
    enrich = out[:_NEWCOMER_ENRICH_N]
    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient() as client:
        async def _fill(item):
            async with sem:
                fc, _img = await _fetch_channel_meta(client, item["chzzk_channel_id"])
                if fc is not None:
                    item["follower_count"] = fc
        # 외부 API가 느리면 응답 전체가 그만큼 끌려간다 — 상한을 두고 초과분은 포기한다
        # (다음 사이클에 다시 시도되고, 팔로워는 부가 정보라 없어도 목록은 정상이다)
        try:
            await asyncio.wait_for(asyncio.gather(*[_fill(x) for x in enrich]),
                                   timeout=_NEWCOMER_ENRICH_TIMEOUT)
        except asyncio.TimeoutError:
            pass

    # ── KPI 요약 ──────────────────────────────────────────────────────────
    count = len(out)
    total_v = sum(x["concurrent_viewers"] for x in out)
    avg_v = round(total_v / count) if count else 0
    peak_v = max((x["concurrent_viewers"] for x in out), default=0)
    # 신규 탭 KPI: 평균 방송 경력(데뷔 N일차) — 정확한 첫 방송일이 있는 채널만으로 낸다.
    # 트랙킹 보완값(TRACKED)은 수집 시작 이후만 알 수 있어 섞으면 평균이 실제보다 짧아진다.
    exact_days = [x["debut_days"] for x in out if x["first_stream_source"] == "CHZZK"]
    avg_debut_days = round(sum(exact_days) / len(exact_days), 1) if exact_days else None
    # 소형 탭 KPI: 시청자 3명 초과 비중 — '0~2명 벽'을 넘은 채널이 얼마나 되는지
    over3 = sum(1 for x in out if x["concurrent_viewers"] > 3)
    summary = {"count": count, "total_viewers": total_v, "avg_viewers": avg_v,
               "peak_viewers": peak_v,
               "avg_debut_days": avg_debut_days, "debut_sample": len(exact_days),
               "over3_count": over3,
               "over3_share": round(over3 / count * 100, 1) if count else 0.0}

    # ── 인사이트 ──────────────────────────────────────────────────────────
    # 1) 인기 카테고리(방송당 평균 시청자 최고) — 채팅 미수집이라 소통 화력 대신 시청자 기반
    cat_agg: dict = {}
    for x in out:
        c = x["category_name"] or "기타"
        e = cat_agg.setdefault(c, {"v": 0, "n": 0})
        e["v"] += x["concurrent_viewers"]; e["n"] += 1
    # 표본 필터: 신입 라이브가 3개 이상인 카테고리만 후보. 1명이 마이너 게임을 켜서
    # 시청자 16명을 모으면 '방송당 평균 16명'으로 대표 카테고리가 되는 아웃라이어 방지.
    cat_pool = {k: v for k, v in cat_agg.items() if v["n"] >= _CAT_MIN_LIVES}
    top_category = None
    if cat_pool:
        nm, e = max(cat_pool.items(), key=lambda kv: kv[1]["v"] / kv[1]["n"])
        top_category = {"name": nm, "avg_viewers": round(e["v"] / e["n"]), "lives": e["n"]}

    # 2) 빈집(노출 최적) 시간대 — 최근 24시간 누적, KST 1시간 단위 (신입 총 시청자 / 신입 라이브 수)
    #
    # 이전 구현은 '지금 라이브 중인' 채널의 보관 전체 스냅샷을 봤다. 지금 켜져 있는 채널은
    # 현재 시각 버킷에 100% 기여하는 반면 과거 시각에는 일부만 남아 있어, 접속한 시각이
    # 늘 최적 시간대로 뽑히는 편향이 있었다. 이제 (a) 최근 24시간으로 창을 자르고
    # (b) 채널 집합을 '보관창 평균 시청자 50명 미만'(신입/하꼬 기준)으로 잡아 지금 라이브
    # 여부와 무관하게 24시간을 고르게 반영한다. 시간 변환은 KST(UTC+9) 오프셋으로 SQL에서 처리.
    golden_hour = None
    # 롤업에서 집계한다 — 원본은 26시간만 보관하므로 경계에서 표본이 잘릴 수 있고,
    # 시간대 버킷은 애초에 시간 단위라 롤업이 정보 손실 없이 정확히 같은 값을 준다.
    #
    # 신입 필터를 'IN (채널별 평균<50 서브쿼리)'로 걸면 롤업 전체를 한 번 더 스캔해
    # 2.7초가 걸렸다. 롤업 행에 그 시간의 avg_viewers가 이미 저장돼 있으므로
    # 행 단위로 직접 거른다 — 24시간 구간만 스캔하면 되고, '그 시간에 소규모였던 방송'
    # 이라는 의미도 시간대 분석 목적에는 오히려 더 정확하다.
    hrows = await (await db.execute(
        """SELECT ((hour_ts + 32400) / 3600) % 24 AS h,
                  SUM(sum_viewers) AS v,
                  SUM(snaps)       AS n,
                  COUNT(DISTINCT chzzk_channel_id) AS ch
           FROM rising_hourly_rollup
           WHERE hour_ts >= ? AND avg_viewers <= ?
           GROUP BY h""",
        (ts - 86400, hour_avg_max)
    )).fetchall()
    hour_agg = {int(r["h"]): {"v": int(r["v"] or 0), "n": int(r["n"] or 0), "ch": int(r["ch"] or 0)}
                for r in hrows if r["n"]}
    # 표본이 어느 정도 쌓인 시간대만 후보 (한두 개 스냅샷으로 최적 시간대가 뒤집히지 않게)
    pool = {h: e for h, e in hour_agg.items() if e["n"] >= _GOLDEN_HOUR_MIN_SAMPLES} or hour_agg
    if pool:
        h, e = max(pool.items(), key=lambda kv: kv[1]["v"] / kv[1]["n"])
        overall = sum(e2["v"] for e2 in pool.values()) / max(1, sum(e2["n"] for e2 in pool.values()))
        hour_avg = e["v"] / e["n"]
        uplift = round((hour_avg / overall - 1) * 100) if overall > 0 else 0
        golden_hour = {"hour": h, "avg_viewers": round(hour_avg), "uplift_pct": uplift,
                       "samples": e["n"], "hours_covered": len(hour_agg)}

    # 2-1) 24시간 골든타임 히트맵용 — 비어 있는 시간도 0으로 채워 항상 24칸을 만든다.
    #
    # 소수점 1자리까지 준다. 정수로 반올림하면 수천 채널을 시간별로 평균낸 값이
    # 24시간 전부 같은 정수(예: 7)로 붙어버려 히트맵의 24칸이 완전히 동일해지고,
    # 결과적으로 전 구간이 최고 색(진한 초록)으로 칠해졌다. 이 지표는 애초에 값 자체보다
    # 시간대 간 '상대차'를 보는 용도라 그 상대차를 반올림으로 날리면 안 된다.
    hourly = []
    for h in range(24):
        e = hour_agg.get(h)
        hourly.append({
            "hour": h,
            "avg_viewers": round(e["v"] / e["n"], 1) if e and e["n"] else 0,
            "channels": e["ch"] if e else 0,
            "snaps": e["n"] if e else 0,
        })

    # 3) 체급 기준선 — 신입 평균 + 상위 20%/10% 컷오프(구체적 목표 수치)
    baseline = None
    if count:
        # '상위 p%에 들려면 최소 몇 명이어야 하는가' = 내림차순 정렬에서 상위 k등의 시청자 수.
        #
        # 예전에는 오름차순 백분위 값에 max(..., avg+1)을 씌웠는데, 신입 그룹은 0~2명에
        # 표본이 몰려 있어 80/90 분위가 같은 값이 되고 거기에 하한까지 걸리면서
        # "N명 상위 20% / N명 상위 10%"로 두 문구가 똑같이 나오는 일이 잦았다.
        # 하한 보정을 걷어내고 실제 컷을 그대로 준다. 동률이라 두 값이 같아질 수는 있는데,
        # 그건 실제로 같은 것이므로 프론트가 문장을 하나로 합쳐 보여 준다.
        sv_desc = sorted((x["concurrent_viewers"] for x in out), reverse=True)

        def _cut(p: float) -> int:
            k = max(1, min(count, round(count * p)))   # 상위 k등
            return int(sv_desc[k - 1])

        top20, top10 = _cut(0.20), _cut(0.10)   # 정렬상 항상 top10 >= top20
        baseline = {
            "avg_viewers": avg_v,
            "top20_cut":   top20,
            "top10_cut":   top10,
            "next_target": top20,  # 하위호환
        }

    # 체급 구간 분포 — 신입이 지금 어느 단계에 몰려 있는지. summary와 같이
    # streamers[:limit]가 아니라 필터를 통과한 전체(out)로 집계해야 비율이 정확하다.
    _TIER_BANDS = [(0, 2, "0~2명", "초기 단계"), (3, 5, "3~5명", "기반 확보"),
                   (6, 9, "6~9명", "상위 20% 진입"), (10, None, "10명+", "상위 10% 메인 노출")]
    tiers = []
    for lo, hi, label, desc in _TIER_BANDS:
        n = sum(1 for x in out
                if x["concurrent_viewers"] >= lo and (hi is None or x["concurrent_viewers"] <= hi))
        tiers.append({"label": label, "desc": desc, "count": n,
                      "share": round(n / count * 100, 1) if count else 0.0})

    # ── 제목 키워드 효율 ──────────────────────────────────────────────────
    # 신입 방송 제목에 유입 키워드가 있는 그룹과 없는 그룹의 평균 시청자를 비교한다.
    # 상관관계일 뿐 인과가 아니라는 점은 프론트 안내 문구에 명시한다.
    kw_hit, kw_miss = [], []
    for x in out:
        title = (x.get("live_title") or "")
        (kw_hit if _TITLE_KEYWORDS.search(title) else kw_miss).append(x["concurrent_viewers"])
    title_keyword = None
    if len(kw_hit) >= _TITLE_MIN_SAMPLES and len(kw_miss) >= _TITLE_MIN_SAMPLES:
        a = sum(kw_hit) / len(kw_hit)
        b = sum(kw_miss) / len(kw_miss)
        title_keyword = {
            "with_count": len(kw_hit), "without_count": len(kw_miss),
            "with_avg": round(a, 1), "without_avg": round(b, 1),
            "lift_pct": round((a / b - 1) * 100, 1) if b > 0 else None,
            "keywords": _TITLE_KEYWORD_LIST,
        }

    # ── 대기업 방종 '빈집 타임' (소형 탭 전용) ────────────────────────────
    # 대형 채널이 적게 켜져 있는 시간대일수록 그 시청자가 다른 방송으로 흩어진다는 가설을
    # 실제 데이터로 확인한다. 시간대별로 (a) 대형 채널 평균 동시 라이브 수와
    # (b) 소형 채널의 방송당 평균 시청자를 함께 내고, 대형이 중앙값 이하로 적은 시간 중
    # 소형 평균 시청자가 가장 높은 시각을 고른다.
    #
    # 최근 24시간은 요일 편향이 커서(주중 1일치만 잡히면 그날의 특성이 그대로 나옴)
    # 7일 창으로 집계하고, 시간대별 '일수'로 나눠 하루 평균 동시 라이브 수를 만든다.
    vacancy_hourly: list[dict] = []
    vacancy_best = None
    if is_small:
        vrows = await (await db.execute(
            """SELECT ((hour_ts + 32400) / 3600) % 24 AS h,
                      SUM(CASE WHEN avg_viewers >= ? THEN 1 ELSE 0 END)          AS big_rows,
                      SUM(CASE WHEN avg_viewers <= ? THEN sum_viewers ELSE 0 END) AS small_v,
                      SUM(CASE WHEN avg_viewers <= ? THEN snaps ELSE 0 END)       AS small_n,
                      COUNT(DISTINCT (hour_ts + 32400) / 86400) AS days
               FROM rising_hourly_rollup
               WHERE hour_ts >= ?
               GROUP BY h""",
            (_BIG_AVG_MIN, _SMALL_AVG_MAX, _SMALL_AVG_MAX,
             ts - _VACANCY_WINDOW_DAYS * 86400)
        )).fetchall()
        vmap = {int(r["h"]): r for r in vrows}
        for h in range(24):
            r = vmap.get(h)
            days = max(1, int(r["days"] or 1)) if r else 1
            sn = int(r["small_n"] or 0) if r else 0
            vacancy_hourly.append({
                "hour": h,
                # 그 시각에 평균 몇 개의 대형 채널이 동시에 켜져 있었는지
                "big_lives": round(int(r["big_rows"] or 0) / days, 1) if r else 0.0,
                # 히트맵과 같은 이유로 소수점 1자리 — 정수 반올림은 시간대 간 차이를 지운다
                "small_avg_viewers": round(int(r["small_v"] or 0) / sn, 1) if sn else 0,
                "snaps": sn,
            })
        usable = [v for v in vacancy_hourly if v["snaps"] > 0]
        if len(usable) >= 6:
            bigs = sorted(v["big_lives"] for v in usable)
            median_big = bigs[len(bigs) // 2]
            quiet = [v for v in usable if v["big_lives"] <= median_big] or usable
            best = max(quiet, key=lambda v: v["small_avg_viewers"])
            overall = sum(v["small_avg_viewers"] for v in usable) / len(usable)
            vacancy_best = {
                "hour": best["hour"],
                "small_avg_viewers": best["small_avg_viewers"],
                "big_lives": best["big_lives"],
                "uplift_pct": round((best["small_avg_viewers"] / overall - 1) * 100)
                              if overall > 0 else 0,
                "window_days": _VACANCY_WINDOW_DAYS,
                "big_threshold": _BIG_AVG_MIN,
            }

    insights = {"top_category": top_category, "golden_hour": golden_hour,
                "baseline": baseline, "hourly": hourly, "tiers": tiers,
                "title_keyword": title_keyword,
                "vacancy_hourly": vacancy_hourly, "vacancy_best": vacancy_best}

    # ── 카테고리 점유율(신입 기준) ─────────────────────────────────────────
    # cat_agg는 필터를 통과한 신입 '전체'로 집계돼 있으므로, streamers[:limit]로 자른
    # 목록이 아니라 이걸 써야 점유율 합이 실제 100%가 된다.
    cat_total_v = sum(e["v"] for e in cat_agg.values()) or 1
    categories = sorted(
        (
            {
                "category":    name,
                "viewers":     e["v"],
                "lives":       e["n"],
                "avg_viewers": round(e["v"] / e["n"]) if e["n"] else 0,
                "share":       round(e["v"] / cat_total_v * 100, 1),
            }
            for name, e in cat_agg.items()
        ),
        key=lambda x: x["viewers"], reverse=True,
    )[:20]

    result = {"collected_at": ts, "group": group, "streamers": out[:limit],
              "summary": summary, "insights": insights, "categories": categories,
              "criteria": {"debut_max_days": _NEW_DEBUT_MAX_DAYS,
                           "small_avg_max": _SMALL_AVG_MAX}}
    _newcomers_cache[group] = (now, result)
    return result


@router.get("/search")
async def search(keyword: str, size: int = 8):
    """치지직 채널 검색(공개, 무인증) — 개인 분석 대시보드로 이동할 스트리머를 찾는다."""
    kw = (keyword or "").strip()
    if not kw:
        return {"results": []}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{_CHZZK_API}/service/v1/search/channels",
                params={"keyword": kw, "offset": 0, "size": max(1, min(20, size))},
                headers=_CHZZK_HEADERS, timeout=8,
            )
        if r.status_code != 200:
            return {"results": []}
        data = ((r.json() or {}).get("content") or {}).get("data") or []
    except Exception:
        return {"results": []}

    results = []
    for item in data:
        ch = item.get("channel") or item
        cid = ch.get("channelId")
        if not cid:
            continue
        results.append({
            "channel_id":        cid,
            "channel_name":      ch.get("channelName") or "",
            "channel_image_url": ch.get("channelImageUrl") or "",
            "follower_count":    int(ch.get("followerCount") or 0),
            "open_live":         bool(ch.get("openLive")),
        })
    return {"results": results}


async def _first_broadcast_info(channel_id: str, channel_name: str | None) -> dict:
    """첫 방송일 — 치지직이 직접 주는 값(정확)을 쓰고, 없을 때만 VOD 추정으로 후퇴한다.

    1순위: chzzk_channel_history 캐시(비공식 channelHistory 엔드포인트). 첫 방송일은 변하지
           않으므로 채널당 사실상 1회만 외부를 부른다. 이미 아는 채널명을 넘겨 이름 조회
           요청을 생략하므로 최초 수집도 채널당 1요청이다.
    2순위: 다시보기 최고령 영상 날짜(기존 방식) — VOD를 지운 채널은 실제보다 늦게 나온다.

    반환: {"date", "iso", "total_live_hours", "source"} — source는 "CHZZK_CHANNEL_HISTORY"
    또는 "VOD_ESTIMATE", 둘 다 실패하면 None.
    """
    try:
        h = await get_channel_history(
            channel_id, channel_name=channel_name,
            # 공개 페이지라 응답 지연을 만들지 않는다 — 누적 방송시간이 오래됐어도
            # 여기서 외부를 다시 부르지 않고, 배치/단일 수집 API가 갱신하도록 맡긴다.
            refresh_stale_total=False,
        )
        if h.get("firstLiveDate"):
            return {"date": h["firstLiveDate"], "iso": h.get("firstLiveDateIso"),
                    "total_live_hours": h.get("totalLiveHours"),
                    "source": "CHZZK_CHANNEL_HISTORY"}
    except Exception:
        pass  # 비공식 엔드포인트가 막히거나 바뀌어도 페이지는 떠야 한다 → VOD 추정으로 후퇴

    vod = await _fetch_first_broadcast(channel_id)
    if vod:
        return {"date": vod, "iso": None, "total_live_hours": None, "source": "VOD_ESTIMATE"}
    return {"date": None, "iso": None, "total_live_hours": None, "source": None}


async def _fetch_first_broadcast(channel_id: str) -> str | None:
    """채널 다시보기(VOD) 목록에서 가장 오래된 영상 날짜로 첫 방송을 추정한다.

    channelHistory(정확한 값)를 못 가져올 때만 쓰는 폴백. VOD 삭제 등으로 실제보다 늦을 수
    있어 '추정'이다."""
    # 채널 ID가 URL에 그대로 들어가므로 형식을 먼저 검증한다
    if not _valid_channel_id(channel_id):
        return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{_CHZZK_API}/service/v1/channels/{channel_id}/videos",
                params={"sortType": "LATEST", "pagingType": "PAGE", "page": 0, "size": 30},
                headers=_CHZZK_HEADERS, timeout=8,
            )
            if r.status_code != 200:
                return None
            content = (r.json() or {}).get("content") or {}
            data = content.get("data") or []
            total_pages = content.get("totalPages")
            if isinstance(total_pages, int) and total_pages > 1:
                r2 = await client.get(
                    f"{_CHZZK_API}/service/v1/channels/{channel_id}/videos",
                    params={"sortType": "LATEST", "pagingType": "PAGE", "page": total_pages - 1, "size": 30},
                    headers=_CHZZK_HEADERS, timeout=8,
                )
                if r2.status_code == 200:
                    data = ((r2.json() or {}).get("content") or {}).get("data") or data
        dates = [str(v.get("publishDate") or v.get("liveOpenDate") or "") for v in data]
        dates = [d for d in dates if d]
        return min(dates) if dates else None
    except Exception:
        return None


META_WINDOW_DAYS = 30
# 스냅샷 간격(분) — 라이브 1구간 근사. 아래 streamer()의 snap_min과 같은 값이어야 한다.
_META_SNAP_MIN = 10


def _meta_etag(payload: dict) -> str:
    """응답 전체의 canonical JSON을 해시한다.

    일부 필드(최신 hour_ts 등)로만 만들면 채널명·이미지·집계값이 바뀌어도 ETag가
    그대로라 크롤러가 옛 메타를 계속 쓴다. **본문이 1바이트라도 다르면 다른 ETag**여야 한다.
    """
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return '"' + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32] + '"'


@router.get("/streamer/{channel_id}/meta")
async def streamer_meta(channel_id: str, request: Request, response: Response):
    """SEO 메타데이터 전용 최소 응답.

    왜 따로 있나 — 실측(2026-08-01): 스트리머 페이지의 SSR(`layout.tsx`)이 메타데이터를
    만들려고 **전체 대시보드**(30일 시계열 + 일간/주간 집계 + 카테고리 + 첫방송일)를
    받아 갔다. 실제로 쓰는 값은 채널명·이미지·요약 3개뿐이다.
    게다가 모든 방문자·크롤러의 SSR이 하나의 rate-limit 버킷으로 합쳐져, 크롤러가
    페이지를 훑으면 429가 났고 그때 **`robots: index=false` 폴백**이 붙었다 —
    크롤링당하는 순간 색인에서 빠지는 구조였다.

    여기서는 롤업을 한 번 집계한다. 외부 호출·첫방송일 수집·시계열 생성을 하지 않는다.
    """
    since = int(time.time()) - META_WINDOW_DAYS * 86400
    db = await get_db()
    # idx_rising_roll_channel(chzzk_channel_id, hour_ts) 사용 — 30일이면 최대 720행
    row = await (await db.execute(
        """SELECT SUM(snaps) AS snaps, SUM(sum_viewers) AS sv,
                  MAX(peak_viewers) AS peak, MAX(hour_ts) AS last_ts
           FROM rising_hourly_rollup
           WHERE chzzk_channel_id=? AND hour_ts >= ?""",
        (channel_id, since)
    )).fetchone()

    snaps = int(row["snaps"] or 0) if row else 0
    if not snaps:
        payload = {"found": False, "channel_id": channel_id, "channel_name": None,
                   "channel_image_url": latest_image(channel_id) or None,
                   "summary": None, "updated_at": None}
    else:
        # 채널명은 최신 원본 스냅샷이 가장 정확하다(롤업에도 있지만 갱신이 늦다).
        # idx_rising_snap_channel(chzzk_channel_id, collected_at) 사용, 1행만 읽는다.
        live = await (await db.execute(
            """SELECT channel_name FROM rising_live_snapshots
               WHERE chzzk_channel_id=? ORDER BY collected_at DESC LIMIT 1""",
            (channel_id,)
        )).fetchone()
        name_row = await (await db.execute(
            """SELECT channel_name FROM rising_hourly_rollup
               WHERE chzzk_channel_id=? AND hour_ts >= ?
               ORDER BY hour_ts DESC LIMIT 1""",
            (channel_id, since)
        )).fetchone()
        sv = int(row["sv"] or 0)
        payload = {
            "found": True,
            "channel_id": channel_id,
            "channel_name": ((live["channel_name"] if live else None)
                             or (name_row["channel_name"] if name_row else None)),
            "channel_image_url": latest_image(channel_id) or None,
            "summary": {
                "avg_viewers": round(sv / snaps) if snaps else 0,
                "peak_viewers": int(row["peak"] or 0),
                "broadcast_hours": round(snaps * _META_SNAP_MIN / 60, 1),
            },
            "updated_at": int(row["last_ts"] or 0) or None,
        }

    etag = _meta_etag(payload)
    # 사용자별 데이터가 없으므로 공유 캐시에 올려도 된다(테스트로 고정).
    cache_control = "public, max-age=60, s-maxage=600, stale-while-revalidate=60"
    if request.headers.get("if-none-match") == etag:
        # 304에서도 캐시 헤더를 유지해야 중간 캐시가 만료를 갱신한다.
        return Response(status_code=304,
                        headers={"ETag": etag, "Cache-Control": cache_control})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = cache_control
    return payload


@router.get("/streamer/{channel_id}")
async def streamer(channel_id: str, days: int = 30):
    """스트리머 개인 분석 — 최근 days일(보관 한계 14일) 스냅샷 집계.

    각 스냅샷 ≈ 라이브 10분으로 보고 방송시간/뷰어쉽(시청자-시간)을 추정한다.
    치지직 공개 API가 과거 데이터를 안 주므로 이력은 우리가 수집한 기간만큼만 존재한다.
    """
    days = max(1, min(400, days))
    since = int(time.time()) - days * 86400
    db = await get_db()
    # 롤업(채널×시간)에서 읽는다 — 원본은 짧게만 보관하므로 30일 이력의 유일한 소스다.
    # snaps/sum_viewers/peak_viewers를 그대로 합산하면 원본 순회와 동일한 값이 나온다.
    rows = await (await db.execute(
        """SELECT hour_ts, snaps, sum_viewers, peak_viewers, max_follower,
                  category_name, channel_name
           FROM rising_hourly_rollup
           WHERE chzzk_channel_id=? AND hour_ts >= ?
           ORDER BY hour_ts ASC""",
        (channel_id, since)
    )).fetchall()

    latest_ts = await _latest_run_ts()
    if not rows:
        return {"found": False, "channel_id": channel_id, "channel_image_url": latest_image(channel_id)}

    # live_title / is_live 는 시각 단위 롤업으로 알 수 없어 최신 원본 스냅샷에서 가져온다
    # (보관창 안이면 존재. 방송을 안 켠 지 오래됐으면 None → is_live=False).
    live_row = await (await db.execute(
        """SELECT collected_at, live_title, channel_name, follower_count
           FROM rising_live_snapshots
           WHERE chzzk_channel_id=? ORDER BY collected_at DESC LIMIT 1""",
        (channel_id,)
    )).fetchone()

    last = rows[-1]
    n = sum(int(r["snaps"]) for r in rows)                 # 총 스냅샷 수
    sv_total = sum(int(r["sum_viewers"]) for r in rows)    # 시청자 합
    snap_min = 10  # 스냅샷 간격(분) — 라이브 1구간 근사
    broadcast_hours = round(n * snap_min / 60, 1)
    viewership = round(sv_total * snap_min / 60)           # 시청자-시간(뷰어-hour)
    avg_v = round(sv_total / n) if n else 0
    peak_all = max(int(r["peak_viewers"]) for r in rows)

    # 카테고리 비중(스냅샷 수 기준) — 시간 버킷의 대표 카테고리에 그 시간의 스냅샷 수를 가중
    cat_counter: Counter = Counter()
    for r in rows:
        if r["category_name"]:
            cat_counter[r["category_name"]] += int(r["snaps"])
    cat_total = sum(cat_counter.values()) or 1
    categories = [
        {"category": c, "share": round(cnt / cat_total * 100, 1), "snapshots": cnt}
        for c, cnt in cat_counter.most_common(6)
    ]

    # 일별(잔디용)
    daily_map: dict[str, dict] = {}
    for r in rows:
        d = _kst_date(r["hour_ts"])
        e = daily_map.setdefault(d, {"n": 0, "sv": 0, "peak": 0})
        e["n"] += int(r["snaps"])
        e["sv"] += int(r["sum_viewers"])
        e["peak"] = max(e["peak"], int(r["peak_viewers"]))
    daily = [
        {"date": d, "minutes": e["n"] * snap_min, "avg_viewers": round(e["sv"] / e["n"]) if e["n"] else 0,
         "peak": e["peak"], "viewership": round(e["sv"] * snap_min / 60)}
        for d, e in sorted(daily_map.items())
    ]

    # 주별(추이용)
    week_map: dict[str, dict] = {}
    for r in rows:
        wk, _ = _kst_week(r["hour_ts"])
        e = week_map.setdefault(wk, {"n": 0, "sv": 0, "peak": 0, "t": r["hour_ts"]})
        e["n"] += int(r["snaps"])
        e["sv"] += int(r["sum_viewers"])
        e["peak"] = max(e["peak"], int(r["peak_viewers"]))
    weekly = [
        {"week": wk, "t": e["t"], "avg_viewers": round(e["sv"] / e["n"]) if e["n"] else 0,
         "peak": e["peak"], "viewership": round(e["sv"] * snap_min / 60)}
        for wk, e in sorted(week_map.items())
    ]

    channel_name = (live_row["channel_name"] if live_row else None) or last["channel_name"]
    # 이미 아는 채널명을 넘겨 첫 방송일 수집이 채널명 조회 요청을 생략하게 한다.
    fb = await _first_broadcast_info(channel_id, channel_name)

    # 소형 채널은 스냅샷 팔로워가 0(상위 100만 보강)이라, 개인 페이지에선 1회 조회로 보강
    live_follower = None
    try:
        async with httpx.AsyncClient() as _c:
            if _valid_channel_id(channel_id):
                live_follower, _ = await _fetch_channel_meta(_c, channel_id)
    except Exception:
        live_follower = None
    snap_follower = int(live_row["follower_count"]) if live_row else int(last["max_follower"] or 0)
    follower_now = live_follower if live_follower is not None else snap_follower
    max_follower = max([follower_now] + [int(r["max_follower"] or 0) for r in rows])

    return {
        "found": True,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "channel_image_url": latest_image(channel_id),
        "live_title": (live_row["live_title"] if live_row else "") or "",
        "follower_count": follower_now,
        "is_live": bool(latest_ts and live_row and live_row["collected_at"] == latest_ts),
        # first_broadcast: 기존 필드(하위 호환). source가 CHZZK_CHANNEL_HISTORY면 정확한 값,
        # VOD_ESTIMATE면 예전과 같은 다시보기 기반 추정치다.
        "first_broadcast": fb["date"],
        "first_broadcast_iso": fb["iso"],
        "first_broadcast_source": fb["source"],
        "total_live_hours": fb["total_live_hours"],
        "window_days": days,
        "history_days": len(daily_map),
        "summary": {
            "peak_viewers": peak_all,
            "avg_viewers": avg_v,
            "max_follower": max_follower,
            "broadcast_hours": broadcast_hours,
            "viewership": viewership,
            "active_days": len(daily_map),
            "categories": categories,
        },
        "daily": daily,
        "weekly": weekly,
    }


@router.get("/rising-stars")
async def rising_stars(limit: int = 20):
    """이주의 라이징 — 약 24시간 전 대비 동시시청자 성장률 상위 채널.

    데이터가 24시간 미만 쌓였으면 빈 목록. 라이징 취지(중소형 급상승)에 맞춰
    현재 동시시청자 1000명 미만 채널만 대상으로 한다.
    """
    now_ts = await _latest_run_ts()
    if now_ts is None:
        return {"collected_at": None, "stars": []}

    db = await get_db()
    # 24시간 전에 가장 가까운(±3h) 성공 수집 사이클
    target = now_ts - 86400
    past = await (await db.execute(
        "SELECT collected_at FROM rising_collect_runs "
        "WHERE ok=1 AND ABS(collected_at - ?) <= 10800 "
        "ORDER BY ABS(collected_at - ?) ASC LIMIT 1",
        (target, target)
    )).fetchone()
    if not past:
        return {"collected_at": now_ts, "stars": [], "note": "24시간치 데이터가 아직 부족합니다."}
    past_ts = int(past["collected_at"])

    rows = await (await db.execute(
        """SELECT n.chzzk_channel_id, n.channel_name, n.category_name,
                  n.concurrent_viewers AS now_v, p.concurrent_viewers AS past_v,
                  n.follower_count
           FROM rising_live_snapshots n
           JOIN rising_live_snapshots p
             ON p.chzzk_channel_id = n.chzzk_channel_id AND p.collected_at = ?
           WHERE n.collected_at = ? AND n.concurrent_viewers < 1000 AND p.concurrent_viewers >= 1
           ORDER BY (CAST(n.concurrent_viewers AS REAL) - p.concurrent_viewers) / p.concurrent_viewers DESC
           LIMIT ?""",
        (past_ts, now_ts, limit)
    )).fetchall()

    stars = [
        {
            "chzzk_channel_id": r["chzzk_channel_id"],
            "channel_name": r["channel_name"],
            "category": r["category_name"],
            "viewers_now": r["now_v"],
            "viewers_past": r["past_v"],
            "growth_rate": round((r["now_v"] - r["past_v"]) / r["past_v"] * 100, 1),
            "follower_count": r["follower_count"],
        }
        for r in rows
    ]
    return {"collected_at": now_ts, "compared_to": past_ts, "stars": stars}


# ── 누적(기간) 랭킹 ──────────────────────────────────────────────────────────
# 실시간 랭킹(live_ranking)은 '최신 수집 사이클 한 장'의 동시 시청자 순위라, 마침 그 순간
# 방송 중이었는지에 크게 좌우된다. 이 엔드포인트는 기간 전체를 누적해 순위를 낸다:
# 잠깐 스파이크가 뜬 방송과 꾸준히 오래 방송한 채널을 구분할 수 있다.
#
# 스냅샷 1개 ≈ 라이브 10분으로 보고 방송시간/시청시간(hours watched)을 추정한다 —
# streamer() 개인 분석과 동일한 근사이므로 두 화면의 수치가 어긋나지 않는다.
_PERIOD_RANGES = {"24h": 24 * 3600, "7d": 7 * 86400}
_PERIOD_SORTS = {
    "viewership":      "viewership",       # 시청 시간(누적) — 기본
    "avg_viewers":     "avg_viewers",
    "peak_viewers":    "peak_viewers",
    "broadcast_hours": "broadcast_hours",
}


# 기간 누적 랭킹 캐시 — 스테이징 실측에서 매 요청 약 3.0초(P95 3.2초)로 이 서비스에서
# 가장 느린 공개 엔드포인트였는데 캐시가 없었다(newcomers/detail에는 있었다).
# 수집 주기가 10분이라 60초 캐시는 신선도 손실이 사실상 없다.
_period_cache: dict = {}
_PERIOD_TTL = 60


@router.get("/ranking-period")
async def ranking_period(range: str = "24h", sort: str = "viewership", limit: int = 100):
    """기간 누적 랭킹 — 시청 시간/평균 시청자/최고 동접/방송 시간 기준.

    range=24h|7d, sort=viewership|avg_viewers|peak_viewers|broadcast_hours.
    """
    window = _PERIOD_RANGES.get(range, _PERIOD_RANGES["24h"])
    sort_key = _PERIOD_SORTS.get(sort, "viewership")
    limit = max(1, min(300, limit))
    # 캐시 키는 정규화된 값만 쓴다 — 사용자 입력을 그대로 키에 넣으면 캐시가 무한히 늘어난다
    ck = (range if range in _PERIOD_RANGES else "24h", sort_key, limit)
    hit = _period_cache.get(ck)
    now_s = time.time()
    if hit and now_s - hit[0] < _PERIOD_TTL:
        return hit[1]
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "range": range, "sort": sort_key, "streamers": [], "history_hours": 0.0}

    since = ts - window
    db = await get_db()

    # 채널별 집계. channel_name/category_name은 구간 내 '마지막' 값을 쓴다
    # (MAX(collected_at)와 함께 뽑으면 SQLite의 bare-column 규칙으로 같은 행 값이 선택된다).
    # 롤업 기반 — 원본은 24시간만 남으므로 7d 집계는 롤업이 유일한 소스다.
    # 24h도 롤업으로 통일해 두 기간의 산출 방식이 갈리지 않게 한다.
    rows = await (await db.execute(
        """SELECT chzzk_channel_id,
                  channel_name,
                  category_name,
                  MAX(hour_ts)        AS last_at,
                  SUM(snaps)          AS snaps,
                  CAST(SUM(sum_viewers) AS REAL) / NULLIF(SUM(snaps),0) AS avg_v,
                  MAX(peak_viewers)   AS peak_v,
                  SUM(sum_viewers)    AS sum_v,
                  MAX(max_follower)   AS follower
           FROM (SELECT * FROM rising_hourly_rollup WHERE hour_ts >= ? ORDER BY hour_ts)
           GROUP BY chzzk_channel_id""",
        (since,)
    )).fetchall()

    snap_min = 10  # 스냅샷 간격(분) — 라이브 1구간 근사
    out = []
    for r in rows:
        snaps = int(r["snaps"] or 0)
        if snaps <= 0:
            continue
        out.append({
            "chzzk_channel_id":  r["chzzk_channel_id"],
            "channel_name":      r["channel_name"] or "",
            "channel_image_url": latest_image(r["chzzk_channel_id"]),  # DB 아님 — 메모리 맵
            "category_name":     r["category_name"] or "",
            "avg_viewers":       round(r["avg_v"] or 0),
            "peak_viewers":      int(r["peak_v"] or 0),
            "viewership":        round((r["sum_v"] or 0) * snap_min / 60),  # 시청 시간(시간)
            "broadcast_hours":   round(snaps * snap_min / 60, 1),
            "follower_count":    int(r["follower"] or 0),
            "snapshots":         snaps,
            "last_at":           int(r["last_at"] or 0),
        })

    out.sort(key=lambda x: x[sort_key], reverse=True)

    # 이 기간 데이터가 실제로 얼마나 쌓였는지 — 프론트에서 '집계 부족' 안내에 쓴다
    first = await (await db.execute(
        "SELECT MIN(collected_at) AS f FROM rising_collect_runs WHERE ok=1 AND collected_at >= ?",
        (since,)
    )).fetchone()
    history_hours = round((ts - int(first["f"])) / 3600, 1) if first and first["f"] else 0.0

    result = {
        "collected_at": ts,
        "range": range if range in _PERIOD_RANGES else "24h",
        "sort": sort_key,
        "history_hours": history_hours,
        "streamers": out[:limit],
    }
    # range x sort x limit 조합은 유한하지만(limit이 1~300) 상한을 둬 메모리 폭주를 막는다
    if len(_period_cache) > 100:
        _period_cache.clear()
    _period_cache[ck] = (now_s, result)
    return result


# ── 카테고리별 스트리머 (전체) ───────────────────────────────────────────────
# live_ranking(limit=200)을 프론트에서 필터링하던 방식은 두 가지 한계가 있었다:
#  1) 상위 200명 밖의 저시청자 방송(예: 마인크래프트 1~2명)이 목록에서 통째로 빠진다.
#     수집기는 최대 MAX_PAGES*PAGE_SIZE(기본 4000)개를 저장하므로 DB엔 있는데 안 보였다.
#  2) 수집기는 시청자 상위 FOLLOWER_ENRICH_N(기본 100)개만 팔로워를 보강하므로
#     그 밖의 채널은 follower_count=0으로 저장된다 → 화면에 '-'로 보인다.
# 그래서 카테고리 하나로 범위를 좁혀 (1) 시청자 하한·상위 N 제한 없이 전부 내려주고
# (2) 팔로워가 0인 채널만 온디맨드로 보강한다(요청 수가 카테고리 규모로 제한됨).
_CAT_FOLLOWER_ENRICH_MAX = 120


@router.get("/category-streamers")
async def category_streamers(category: str, enrich: bool = True):
    """특정 카테고리로 현재 방송 중인 스트리머 전체 — 시청자 0명도 포함."""
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "category": category, "streamers": [], "enriched": 0}

    db = await get_db()
    last2 = await (await db.execute(
        "SELECT collected_at FROM rising_collect_runs WHERE ok=1 ORDER BY collected_at DESC LIMIT 2"
    )).fetchall()
    prev_ts = int(last2[1]["collected_at"]) if len(last2) > 1 else None

    rows = await (await db.execute(
        """SELECT n.chzzk_channel_id, n.channel_name, n.concurrent_viewers, n.category_name,
                  n.open_date, n.follower_count, n.live_title, n.adult,
                  p.concurrent_viewers AS viewers_prev
           FROM rising_live_snapshots n
           LEFT JOIN rising_live_snapshots p
             ON p.chzzk_channel_id = n.chzzk_channel_id AND p.collected_at = ?
           WHERE n.collected_at = ? AND n.category_name = ?
           ORDER BY n.concurrent_viewers DESC""",
        (prev_ts if prev_ts is not None else -1, ts, category)
    )).fetchall()

    out = [
        {
            "chzzk_channel_id":   r["chzzk_channel_id"],
            "channel_name":       r["channel_name"],
            "channel_image_url":  latest_image(r["chzzk_channel_id"]),  # DB 아님 — 메모리 맵
            "concurrent_viewers": r["concurrent_viewers"],
            "viewers_prev":       r["viewers_prev"],
            "category_name":      r["category_name"],
            "open_date":          r["open_date"],
            "follower_count":     r["follower_count"],
            "live_title":         r["live_title"],
            "adult":              bool(r["adult"]),
        }
        for r in rows
    ]

    # 팔로워가 비어 있는 채널만 보강. 카테고리 하나라 호출 수가 제한적이고, 상한도 둔다.
    enriched = 0
    if enrich:
        todo = [x for x in out if not x["follower_count"]][:_CAT_FOLLOWER_ENRICH_MAX]
        if todo:
            sem = asyncio.Semaphore(12)
            async with httpx.AsyncClient() as client:
                async def _fill(item):
                    async with sem:
                        fc, img = await _fetch_channel_meta(client, item["chzzk_channel_id"])
                        if fc is not None:
                            item["follower_count"] = fc
                        if img and not item["channel_image_url"]:
                            item["channel_image_url"] = img
                await asyncio.gather(*[_fill(x) for x in todo])
            enriched = sum(1 for x in todo if x["follower_count"])

    return {"collected_at": ts, "category": category, "streamers": out, "enriched": enriched}


# ── 스트리머 상세: 시간대/세션/랭킹 추이 ──────────────────────────────────────
# 개인 분석 페이지의 서브 탭(통계·방송기록·랭킹)에 필요한 집계.
# 전부 rising_hourly_rollup에서 계산한다 — 원본은 26시간만 보관하므로 장기 이력의
# 유일한 소스이며, 시간 단위 지표는 롤업으로 정보 손실 없이 재현된다.
# 채널별 detail 캐시 — rank_daily의 윈도우 함수가 무거워(실측 약 4.5초) 새로고침마다
# 다시 계산하면 체감이 크게 나빠진다. 수집 주기가 10분이라 60초 캐시는 신선도 손실이 없다.
_detail_cache: dict = {}
_DETAIL_TTL = 60


@router.get("/streamer/{channel_id}/detail")
async def streamer_detail(channel_id: str, days: int = 30):
    days = max(1, min(400, days))
    ck = (channel_id, days)
    hit = _detail_cache.get(ck)
    now_s = time.time()
    if hit and now_s - hit[0] < _DETAIL_TTL:
        return hit[1]
    ts = await _latest_run_ts()
    if ts is None:
        return {"channel_id": channel_id, "hourly": [], "sessions": [], "rank_daily": []}
    since = ts - days * 86400
    db = await get_db()
    snap_min = 10

    # ① 시간대별 유입 — KST 시각별 평균 시청자(그 시간에 방송을 켰을 때의 성과)
    hrows = await (await db.execute(
        """SELECT ((hour_ts + 32400) / 3600) % 24 AS h,
                  SUM(snaps) AS n, SUM(sum_viewers) AS v, MAX(peak_viewers) AS p
           FROM rising_hourly_rollup
           WHERE chzzk_channel_id=? AND hour_ts >= ?
           GROUP BY h ORDER BY h""",
        (channel_id, since)
    )).fetchall()
    hourly = [
        {"hour": int(r["h"]), "snaps": int(r["n"]),
         "avg_viewers": round((r["v"] or 0) / r["n"]) if r["n"] else 0,
         "peak_viewers": int(r["p"] or 0),
         "hours": round(int(r["n"]) * snap_min / 60, 1)}
        for r in hrows
    ]

    # ② 방송 세션 — 연속된 시간 버킷을 하나의 방송으로 묶는다.
    # 롤업은 시간 단위라 정확한 시작/종료 '분'은 알 수 없다(원본 26시간 밖은 복원 불가).
    # 한 시간 이상 공백이 생기면 다른 방송으로 끊는다.
    srows = await (await db.execute(
        """SELECT hour_ts, snaps, sum_viewers, peak_viewers, category_name
           FROM rising_hourly_rollup
           WHERE chzzk_channel_id=? AND hour_ts >= ?
           ORDER BY hour_ts ASC""",
        (channel_id, since)
    )).fetchall()
    sessions: list[dict] = []
    for r in srows:
        h, snaps = int(r["hour_ts"]), int(r["snaps"])
        cur = sessions[-1] if sessions else None
        if cur and h == cur["_next"]:
            cur["_next"] = h + 3600
            cur["end"] = h + 3600
            cur["snaps"] += snaps
            cur["_sv"] += int(r["sum_viewers"])
            cur["peak_viewers"] = max(cur["peak_viewers"], int(r["peak_viewers"]))
            if r["category_name"]:
                cur["_cats"][r["category_name"]] = cur["_cats"].get(r["category_name"], 0) + snaps
        else:
            sessions.append({
                "start": h, "end": h + 3600, "_next": h + 3600,
                "snaps": snaps, "_sv": int(r["sum_viewers"]),
                "peak_viewers": int(r["peak_viewers"]),
                "_cats": {r["category_name"]: snaps} if r["category_name"] else {},
            })
    out_sessions = []
    for s in reversed(sessions):  # 최신 방송부터
        cats = sorted(s["_cats"].items(), key=lambda kv: kv[1], reverse=True)
        out_sessions.append({
            "start": s["start"], "end": s["end"],
            "hours": round(s["snaps"] * snap_min / 60, 1),
            "avg_viewers": round(s["_sv"] / s["snaps"]) if s["snaps"] else 0,
            "peak_viewers": s["peak_viewers"],
            "viewership": round(s["_sv"] * snap_min / 60),
            "category": cats[0][0] if cats else "",
            "categories": [c for c, _ in cats[:3]],
        })

    # ③ 일별 랭킹 추이 — 그날 방송한 전체 채널 중 평균 시청자 순위.
    # 별도 랭킹 이력 테이블 없이 롤업에서 매번 계산한다(윈도우 함수).
    # 순위는 롤업 전체를 채널×일로 집계한 뒤 윈도우 함수로 매긴다.
    # 비용이 큰 구간(실측 약 3.5초)이지만, 대상 날짜만 뽑아 HAVING으로 거르는 방식은
    # GROUP BY 이후에 필터가 걸려 집계량이 줄지 않고 쿼리만 늘어 오히려 느려졌다(5.3초).
    # 근본 해결은 채널×일 단위 롤업 테이블을 따로 두는 것 — 별도 작업으로 남긴다.
    rrows = await (await db.execute(
        """WITH daily AS (
               SELECT chzzk_channel_id,
                      (hour_ts + 32400) / 86400 AS d,
                      CAST(SUM(sum_viewers) AS REAL) / NULLIF(SUM(snaps),0) AS avg_v,
                      SUM(snaps) AS snaps
               FROM rising_hourly_rollup
               WHERE hour_ts >= ?
               GROUP BY chzzk_channel_id, d
           ), ranked AS (
               SELECT d, chzzk_channel_id, avg_v, snaps,
                      RANK() OVER (PARTITION BY d ORDER BY avg_v DESC) AS rk,
                      COUNT(*) OVER (PARTITION BY d) AS total
               FROM daily
           )
           SELECT strftime('%Y-%m-%d', d * 86400, 'unixepoch') AS d,
                  rk, total, avg_v, snaps FROM ranked
           WHERE chzzk_channel_id = ? ORDER BY d ASC""",
        (since, channel_id)
    )).fetchall()

    rank_daily = [
        {"date": r["d"], "rank": int(r["rk"]), "total": int(r["total"]),
         "avg_viewers": round(r["avg_v"] or 0),
         "percentile": round(int(r["rk"]) / int(r["total"]) * 100, 1) if r["total"] else None}
        for r in rrows
    ]

    result = {"channel_id": channel_id, "window_days": days,
              "hourly": hourly, "sessions": out_sessions, "rank_daily": rank_daily}
    # 캐시가 무한정 커지지 않도록 상한을 둔다(채널 수만큼 늘어날 수 있다)
    if len(_detail_cache) > 200:
        _detail_cache.clear()
    _detail_cache[ck] = (now_s, result)
    return result


@router.get("/streamer/{channel_id}/session")
async def streamer_session(channel_id: str, start: int, end: int):
    """구간분석 — 방송 1건의 시청자 추이.

    원본(10분 간격)은 RAW_RETENTION_HOURS(기본 26시간)만 보관하므로, 그 안의 방송은
    10분 해상도로, 그보다 오래된 방송은 롤업의 1시간 해상도로 응답한다.
    resolution 필드로 어느 쪽인지 알려 프론트가 안내를 띄울 수 있게 한다.
    """
    db = await get_db()
    rows = await (await db.execute(
        """SELECT collected_at AS t, concurrent_viewers AS v, category_name, live_title
           FROM rising_live_snapshots
           WHERE chzzk_channel_id=? AND collected_at BETWEEN ? AND ?
           ORDER BY collected_at ASC""",
        (channel_id, start, end)
    )).fetchall()
    if rows:
        return {"resolution": "10m", "points": [
            {"t": int(r["t"]), "viewers": int(r["v"]),
             "category": r["category_name"] or "", "title": r["live_title"] or ""}
            for r in rows
        ]}

    rrows = await (await db.execute(
        """SELECT hour_ts AS t, snaps, sum_viewers, peak_viewers, category_name
           FROM rising_hourly_rollup
           WHERE chzzk_channel_id=? AND hour_ts BETWEEN ? AND ?
           ORDER BY hour_ts ASC""",
        (channel_id, start, end)
    )).fetchall()
    return {"resolution": "1h", "points": [
        {"t": int(r["t"]),
         "viewers": round((r["sum_viewers"] or 0) / r["snaps"]) if r["snaps"] else 0,
         "peak": int(r["peak_viewers"] or 0),
         "category": r["category_name"] or "", "title": ""}
        for r in rrows
    ]}


# ── 태그 검색 ────────────────────────────────────────────────────────────────
# 스트리머가 방송에 붙인 태그(rising_live_snapshots.tags, 쉼표 구분)로 방송을 찾는다.
# 태그는 정규화되지 않은 자유 입력이라 SQL로 쪼개기보다 최신 스냅샷을 한 번 읽어
# 파이썬에서 분해하는 편이 단순하고 빠르다(최신 사이클 1장 = 수천 행).
def _split_tags(raw: str) -> list[str]:
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


@router.get("/tags")
async def tags(limit: int = 60):
    """현재 라이브에서 많이 쓰인 태그 — 태그 검색 페이지의 추천 목록."""
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "tags": []}
    db = await get_db()
    rows = await (await db.execute(
        "SELECT tags, concurrent_viewers FROM rising_live_snapshots "
        "WHERE collected_at=? AND tags != ''", (ts,)
    )).fetchall()
    agg: dict[str, dict] = {}
    for r in rows:
        for t in _split_tags(r["tags"]):
            e = agg.setdefault(t, {"lives": 0, "viewers": 0})
            e["lives"] += 1
            e["viewers"] += int(r["concurrent_viewers"] or 0)
    items = sorted(
        ({"tag": k, "lives": v["lives"], "viewers": v["viewers"],
          "avg_viewers": round(v["viewers"] / v["lives"]) if v["lives"] else 0}
         for k, v in agg.items()),
        key=lambda x: (x["lives"], x["viewers"]), reverse=True,
    )
    return {"collected_at": ts, "tags": items[:max(1, min(300, limit))]}


# 태그 목록의 팔로워 온디맨드 보강 상한 — 카테고리별 스트리머와 같은 방식/이유다.
_TAG_FOLLOWER_ENRICH_MAX = 120


@router.get("/tag-streamers")
async def tag_streamers(tag: str, exact: bool = False, enrich: bool = True):
    """특정 태그를 단 방송 전체. exact=false면 부분 일치(대소문자 무시).

    팔로워는 스냅샷에 0으로 저장돼 있는 채널이 많다 — 수집기가 시청자 상위
    FOLLOWER_ENRICH_N(기본 100)개만 보강하기 때문이다. 태그로 걸러진 목록은
    대부분 그 밖의 소형 채널이라 화면에서 팔로워가 통째로 '-'로 보였다.
    카테고리별 스트리머와 동일하게, 0인 채널만 골라 온디맨드로 채운다.
    """
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "tag": tag, "streamers": []}
    kw = tag.strip().lower()
    if not kw:
        return {"collected_at": ts, "tag": tag, "streamers": []}

    db = await get_db()
    last2 = await (await db.execute(
        "SELECT collected_at FROM rising_collect_runs WHERE ok=1 ORDER BY collected_at DESC LIMIT 2"
    )).fetchall()
    prev_ts = int(last2[1]["collected_at"]) if len(last2) > 1 else None

    rows = await (await db.execute(
        """SELECT n.chzzk_channel_id, n.channel_name, n.concurrent_viewers, n.category_name,
                  n.open_date, n.follower_count, n.live_title, n.tags, n.adult,
                  p.concurrent_viewers AS viewers_prev
           FROM rising_live_snapshots n
           LEFT JOIN rising_live_snapshots p
             ON p.chzzk_channel_id = n.chzzk_channel_id AND p.collected_at = ?
           WHERE n.collected_at = ? AND n.tags != ''
           ORDER BY n.concurrent_viewers DESC""",
        (prev_ts if prev_ts is not None else -1, ts)
    )).fetchall()

    out = []
    for r in rows:
        tl = _split_tags(r["tags"])
        hit = any(t.lower() == kw for t in tl) if exact else any(kw in t.lower() for t in tl)
        if not hit:
            continue
        out.append({
            "chzzk_channel_id":   r["chzzk_channel_id"],
            "channel_name":       r["channel_name"],
            "channel_image_url":  latest_image(r["chzzk_channel_id"]),  # DB 아님 — 메모리 맵
            "concurrent_viewers": r["concurrent_viewers"],
            "viewers_prev":       r["viewers_prev"],
            "category_name":      r["category_name"],
            "open_date":          r["open_date"],
            "follower_count":     r["follower_count"],
            "live_title":         r["live_title"],
            "tags":               tl,
            "adult":              bool(r["adult"]),
        })

    # 팔로워가 0인 채널만 채널 상세 API로 보강한다(요청 수는 상한으로 묶는다).
    # 이미지 URL도 비어 있으면 함께 채운다 — 같은 응답에서 얻을 수 있다.
    enriched = 0
    if enrich:
        todo = [x for x in out if not x["follower_count"]][:_TAG_FOLLOWER_ENRICH_MAX]
        if todo:
            sem = asyncio.Semaphore(12)
            async with httpx.AsyncClient() as client:
                async def _fill(item):
                    async with sem:
                        fc, img = await _fetch_channel_meta(client, item["chzzk_channel_id"])
                        if fc is not None:
                            item["follower_count"] = fc
                        if img and not item["channel_image_url"]:
                            item["channel_image_url"] = img
                # 외부 API가 느리면 응답 전체가 끌려간다 — 상한을 두고 초과분은 포기한다
                # (팔로워는 부가 정보라 없어도 목록 자체는 정상이다)
                try:
                    await asyncio.wait_for(asyncio.gather(*[_fill(x) for x in todo]),
                                           timeout=_NEWCOMER_ENRICH_TIMEOUT)
                except asyncio.TimeoutError:
                    pass
            enriched = sum(1 for x in todo if x["follower_count"])

    return {"collected_at": ts, "tag": tag, "streamers": out, "enriched": enriched}


# ── 태그 유입 효과 비교 ──────────────────────────────────────────────────────
# 태그를 단 방송과 안 단 방송의 지표를 비교한다.
# 비교 대상을 '같은 카테고리'로 한정하는 이유: 카테고리마다 시청자 규모가 크게 달라
# 전체와 비교하면 태그 효과가 아니라 카테고리 차이를 재게 된다.
def _hours_since(open_date: str, now: int) -> float | None:
    if not open_date:
        return None
    try:
        dt = datetime.strptime(open_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST)
    except ValueError:
        return None
    h = (now - int(dt.timestamp())) / 3600
    return h if 0 <= h <= 48 else None


def _group_stats(rows: list, now: int) -> dict:
    n = len(rows)
    if n == 0:
        return {"channels": 0, "avg_viewers": 0, "avg_hours": 0.0, "avg_follower": 0, "avg_follower_gain": None}
    hrs = [h for h in (_hours_since(r["open_date"], now) for r in rows) if h is not None]
    gains = [int(r["follower_count"]) - int(r["follower_prev24h"])
             for r in rows if r["follower_prev24h"] is not None and r["follower_count"]]
    return {
        "channels": n,
        "avg_viewers": round(sum(int(r["concurrent_viewers"] or 0) for r in rows) / n, 1),
        "avg_hours": round(sum(hrs) / len(hrs), 1) if hrs else 0.0,
        "avg_follower": round(sum(int(r["follower_count"] or 0) for r in rows) / n),
        "avg_follower_gain": round(sum(gains) / len(gains), 1) if gains else None,
    }


@router.get("/tag-effect")
async def tag_effect(tag: str | None = None):
    """태그 사용/미사용 그룹 비교. tag가 없으면 '태그를 하나라도 단 방송' 전체로 비교한다."""
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "tag": tag, "tagged": None, "untagged": None}

    db = await get_db()
    t24row = await (await db.execute(
        "SELECT collected_at FROM rising_collect_runs "
        "WHERE ok=1 AND ABS(collected_at - ?) <= 5400 ORDER BY ABS(collected_at - ?) ASC LIMIT 1",
        (ts - 86400, ts - 86400)
    )).fetchone()
    t24 = int(t24row["collected_at"]) if t24row else -1

    rows = await (await db.execute(
        """SELECT n.chzzk_channel_id, n.concurrent_viewers, n.category_name, n.open_date,
                  n.follower_count, n.tags,
                  f.follower_count AS follower_prev24h
           FROM rising_live_snapshots n
           LEFT JOIN rising_live_snapshots f
             ON f.chzzk_channel_id = n.chzzk_channel_id AND f.collected_at = ?
           WHERE n.collected_at = ?""",
        (t24, ts)
    )).fetchall()

    kw = (tag or "").strip().lower()
    if kw:
        tagged = [r for r in rows if any(kw in t.lower() for t in _split_tags(r["tags"]))]
        # 같은 카테고리 안에서, 그 태그를 안 단 방송이 비교군
        cats = {r["category_name"] for r in tagged}
        untagged = [r for r in rows
                    if r["category_name"] in cats
                    and not any(kw in t.lower() for t in _split_tags(r["tags"]))]
    else:
        tagged = [r for r in rows if _split_tags(r["tags"])]
        untagged = [r for r in rows if not _split_tags(r["tags"])]

    a, b = _group_stats(tagged, ts), _group_stats(untagged, ts)

    def lift(x, y):
        return round((x / y - 1) * 100, 1) if y else None

    return {
        "collected_at": ts, "tag": tag,
        "tagged": a, "untagged": b,
        "lift": {
            "viewers": lift(a["avg_viewers"], b["avg_viewers"]),
            "hours": lift(a["avg_hours"], b["avg_hours"]),
            "follower": lift(a["avg_follower"], b["avg_follower"]),
            "follower_gain": (lift(a["avg_follower_gain"], b["avg_follower_gain"])
                              if a["avg_follower_gain"] is not None and b["avg_follower_gain"] else None),
        },
    }


# ── 전체 분석 탭 시각화 3종 ──────────────────────────────────────────────────
# 체급 구간(개요 탭 TIERS와 별개) — 소규모 구간을 더 잘게 나눠 분포를 본다
_VIEWER_BANDS = [(0, 5, "0~5명"), (6, 20, "6~20명"), (21, 100, "21~100명"),
                 (101, 500, "101~500명"), (501, None, "500명+")]


@router.get("/viewer-distribution")
async def viewer_distribution():
    """시청자 체급 구간별 채널 수 — 최신 수집 사이클 기준."""
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "bands": []}
    db = await get_db()
    rows = await (await db.execute(
        "SELECT concurrent_viewers FROM rising_live_snapshots WHERE collected_at=?", (ts,)
    )).fetchall()
    vs = [int(r["concurrent_viewers"] or 0) for r in rows]
    total = len(vs) or 1
    bands = []
    for lo, hi, label in _VIEWER_BANDS:
        n = sum(1 for v in vs if v >= lo and (hi is None or v <= hi))
        bands.append({"label": label, "channels": n, "share": round(n / total * 100, 1)})
    return {"collected_at": ts, "total": len(vs), "bands": bands}


@router.get("/traffic-heatmap")
async def traffic_heatmap(days: int = 14):
    """요일×시간대 평균 시청자 — 7x24 히트맵.

    rising_collect_runs(사이클당 1행, 영구 보관)에서 읽으므로 원본 보관 기간과 무관하게
    이력이 계속 누적된다. KST 기준 요일(월=0)과 시(0~23)로 버킷팅한다.
    """
    days = max(1, min(90, days))
    since = int(time.time()) - days * 86400
    db = await get_db()
    rows = await (await db.execute(
        """SELECT ((collected_at + 32400) / 86400 + 4) % 7 AS dow,
                  ((collected_at + 32400) / 3600) % 24 AS h,
                  AVG(total_viewers) AS v, COUNT(*) AS n
           FROM rising_collect_runs
           WHERE ok=1 AND collected_at >= ?
           GROUP BY dow, h""",
        (since,)
    )).fetchall()
    # strftime %w는 일=0 → 월=0이 되도록 이동(화면에서 월~일 순으로 보이게)
    cells = {(int(r["dow"]) + 6) % 7: {} for r in rows}
    for r in rows:
        cells.setdefault((int(r["dow"]) + 6) % 7, {})[int(r["h"])] = {
            "avg_viewers": round(r["v"] or 0), "samples": int(r["n"] or 0)}
    grid = [[(cells.get(d, {}).get(h) or {"avg_viewers": 0, "samples": 0}) for h in range(24)]
            for d in range(7)]
    return {"days": days, "grid": grid}


# 제목 키워드 추출용 불용어 — 의미 없는 조사/일반어를 걸러 낸다
_TITLE_STOP = {
    "방송", "오늘", "지금", "같이", "우리", "그냥", "다시", "진짜", "제발", "이제",
    "하는", "하고", "해서", "합니다", "해요", "이제부터", "여러분", "안녕하세요",
    "with", "the", "and", "for", "live", "new",
}
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")

# 치지직 채널 ID는 32자리 16진수다. 이 값은 경로 파라미터로 들어와 외부 API URL에
# 그대로 붙으므로(f"{_CHZZK_API}/service/v1/channels/{channel_id}/videos"),
# 형식을 검증해 URL 조작(경로 이탈 등) 여지를 없앤다.
_CHANNEL_ID_RE = re.compile(r"^[0-9a-fA-F]{8,64}$")


def _valid_channel_id(cid: str) -> bool:
    return bool(_CHANNEL_ID_RE.match(cid or ""))


@router.get("/title-keywords")
async def title_keywords(limit: int = 10):
    """현재 라이브 방송 제목에서 많이 쓰인 키워드 TOP N."""
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "keywords": []}
    db = await get_db()
    rows = await (await db.execute(
        "SELECT live_title, concurrent_viewers FROM rising_live_snapshots "
        "WHERE collected_at=? AND live_title != ''", (ts,)
    )).fetchall()
    agg: dict[str, dict] = {}
    for r in rows:
        seen = set()
        for tok in _TOKEN_RE.findall(r["live_title"]):
            w = tok.lower()
            # 1글자와 순수 숫자는 노이즈라 제외. 같은 제목에서 중복 집계도 막는다.
            if len(w) < 2 or w.isdigit() or w in _TITLE_STOP or w in seen:
                continue
            seen.add(w)
            e = agg.setdefault(w, {"lives": 0, "viewers": 0})
            e["lives"] += 1
            e["viewers"] += int(r["concurrent_viewers"] or 0)
    items = sorted(
        ({"keyword": k, "lives": v["lives"], "viewers": v["viewers"],
          "avg_viewers": round(v["viewers"] / v["lives"]) if v["lives"] else 0}
         for k, v in agg.items() if v["lives"] >= 3),
        key=lambda x: x["lives"], reverse=True,
    )
    return {"collected_at": ts, "keywords": items[:max(1, min(50, limit))]}


@router.get("/sitemap-channels")
async def sitemap_channels(limit: int = 2000):
    """사이트맵에 넣을 채널 목록.

    streamer 페이지는 데이터가 없으면 layout.tsx에서 robots noindex를 내보내므로,
    빈 페이지를 사이트맵에 넣으면 색인 품질만 떨어진다. 그래서 '롤업에 실제 방송 이력이
    쌓인 채널'만 고른다. 최근 활동순으로 정렬해 상위 N개만 반환한다.
    """
    limit = max(1, min(20000, limit))
    db = await get_db()
    rows = await (await db.execute(
        # HAVING에는 SUM(snaps)를 직접 쓴다. 별칭을 snaps로 두고 'HAVING snaps >= ?'라고
        # 쓰면 SQLite가 집계값이 아니라 원본 컬럼 snaps로 해석해 조건이 항상 거짓이 된다
        # (자체 테스트에서 결과가 0행으로 나와 발견).
        """SELECT chzzk_channel_id, MAX(hour_ts) AS last_at, SUM(snaps) AS total_snaps
           FROM rising_hourly_rollup
           GROUP BY chzzk_channel_id
           HAVING SUM(snaps) >= ?
           ORDER BY last_at DESC
           LIMIT ?""",
        (_SITEMAP_MIN_SNAPS, limit)
    )).fetchall()
    return {"channels": [{"id": r["chzzk_channel_id"], "last_at": int(r["last_at"] or 0)}
                         for r in rows]}


# ── 기간별 상세 분석 (다중 필터 추이) ────────────────────────────────────────
# 기간 + 카테고리 + 태그 + 체급을 조합해 시계열/시간대/요일/카테고리표를 한 번에 낸다.
#
# 소스는 rising_hourly_rollup 하나다. 한 시간 버킷 안에서
#   · 그 시간의 동시 시청자 총합 ≈ SUM(avg_viewers)   (채널별 시간 평균의 합)
#   · 그 시간에 방송한 채널 수    = COUNT(*)
#   · 뷰어쉽(시청 시간)           = SUM(sum_viewers) × 스냅샷간격(10분) / 60
# 으로 계산한다. sum_viewers는 '스냅샷 시청자 합'이라 시간 단위로 환산해야 의미가 있다.
_PA_TTL = 60
_pa_cache: dict[tuple, tuple[float, dict]] = {}

# 체급 구간(기간 내 채널 평균 동시 시청자 기준). 상단 TIERS와 경계가 다른데,
# 저쪽은 '실시간 한 장'의 체급 분포용이고 여기는 기획서가 지정한 필터 구간이다.
_PA_TIERS: dict[str, tuple[float, float]] = {
    "all":    (0.0, 1e12),
    "rookie": (0.0, 10.0),      # 신입/라이징 — 10명 이하
    "small":  (10.0001, 100.0),  # 중소형 — 11~100명
    "large":  (100.0001, 1e12),  # 대기업 — 100명 초과
}
_PA_PRESETS = {"today": 1, "7d": 7, "30d": 30}
_PA_MAX_DAYS = 90


def _pa_day_bounds(day: str) -> int | None:
    """'YYYY-MM-DD'(KST) → 그 날 00:00 KST의 epoch. 형식이 어긋나면 None."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=_KST)
    except (ValueError, TypeError):
        return None
    return int(d.timestamp())


@router.get("/period-analysis")
async def period_analysis(
    # 쿼리 이름은 다른 엔드포인트와 맞춰 range로 두되, 파이썬 쪽 이름은 period로 받는다.
    # 파라미터를 range로 두면 내장 range()가 가려져 함수 안에서 range(24)가 터진다
    # (실제로 500이 났다 — ranking_period는 range()를 안 써서 드러나지 않았을 뿐이다).
    period: str = Query("7d", alias="range"),
    start: str | None = None,
    end: str | None = None,
    category: str | None = None,
    tags: str | None = None,
    tier: str = "all",
):
    """기간별 상세 분석 — 필터 조건 아래의 시청자/채널 수 추이와 카테고리 집계.

    range=today|7d|30d|custom (custom이면 start/end를 'YYYY-MM-DD'(KST)로 준다),
    tags는 쉼표 구분 다중 선택(하나라도 해당하면 포함, OR).
    """
    now = int(time.time())
    tier = tier if tier in _PA_TIERS else "all"
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()][:8]

    if period == "custom" and start and end:
        s, e = _pa_day_bounds(start), _pa_day_bounds(end)
        if s is None or e is None:
            return {"error": "invalid_date", "detail": "날짜 형식은 YYYY-MM-DD여야 합니다."}
        if e < s:
            s, e = e, s
        e += 86400  # 종료일을 포함하도록 그 날 끝까지
        s = max(s, e - _PA_MAX_DAYS * 86400)
        range_key = "custom"
    else:
        range_key = period if period in _PA_PRESETS else "7d"
        days = _PA_PRESETS[range_key]
        if range_key == "today":
            # '오늘'은 최근 24시간이 아니라 KST 자정부터 — 화면 문구와 일치시킨다
            s = int(datetime.fromtimestamp(now, _KST).replace(
                hour=0, minute=0, second=0, microsecond=0).timestamp())
        else:
            s = now - days * 86400
        e = now + 3600

    ck = (range_key, s // 300, e // 300, category or "", ",".join(tag_list), tier)
    hit = _pa_cache.get(ck)
    if hit and time.time() - hit[0] < _PA_TTL:
        return hit[1]

    db = await get_db()
    lo, hi = _PA_TIERS[tier]
    params: list = [s, e]
    where = ["hour_ts >= ?", "hour_ts < ?"]
    if category:
        where.append("category_name = ?")
        params.append(category)

    # 태그는 롤업에 없다(용량 때문에 컬럼을 늘리지 않았다) — 원본 스냅샷에서
    # 최근에 그 태그를 단 채널 집합을 뽑아 필터로 쓴다. 원본 보관이 약 26시간이므로
    # '최근 하루 안에 그 태그로 방송한 채널의 과거 추이'라는 뜻이 된다(응답의 tag_scope_hours).
    tag_scope_hours = 0
    if tag_list:
        tag_scope_hours = 26
        like = " OR ".join(["tags LIKE ?"] * len(tag_list))
        where.append(
            f"chzzk_channel_id IN (SELECT DISTINCT chzzk_channel_id FROM rising_live_snapshots"
            f" WHERE collected_at >= ? AND ({like}))")
        params.append(now - tag_scope_hours * 3600)
        params.extend(f"%{t}%" for t in tag_list)

    base_where = " AND ".join(where)
    # 체급은 '기간 내 채널 평균 동시 시청자'로 판정하므로 같은 필터를 두 번 쓴다
    # (CTE로 묶고 싶지만 파라미터 순서가 뒤엉켜 가독성이 떨어져 서브쿼리로 둔다).
    sql = f"""
        SELECT hour_ts, category_name,
               COUNT(*)          AS chans,
               SUM(avg_viewers)  AS total_v,
               SUM(sum_viewers)  AS sum_v,
               SUM(snaps)        AS sn
        FROM rising_hourly_rollup
        WHERE {base_where}
          AND chzzk_channel_id IN (
              SELECT chzzk_channel_id FROM rising_hourly_rollup
              WHERE {base_where}
              GROUP BY chzzk_channel_id
              HAVING SUM(sum_viewers) * 1.0 / NULLIF(SUM(snaps),0) BETWEEN ? AND ?)
        GROUP BY hour_ts, category_name
    """
    rows = await (await db.execute(sql, (*params, *params, lo, hi))).fetchall()

    if not rows:
        empty = {"range": range_key, "start": s, "end": min(e, now), "tier": tier,
                 "category": category or "", "tags": tag_list,
                 "tag_scope_hours": tag_scope_hours, "bucket": "hour",
                 "summary": None, "series": [], "hourly": [], "dow": [], "table": []}
        _pa_cache[ck] = (time.time(), empty)
        return empty

    snap_min = 10
    span = e - s
    bucket = "hour" if span <= 3 * 86400 else "day"

    hours: dict[int, dict] = {}          # hour_ts -> 시간 단위 합계
    cats: dict[str, dict] = {}           # category -> 누적
    cat_hours: dict[str, dict[int, dict]] = {}  # category -> hour -> 합계(최고/평균 채널·시청자용)

    for r in rows:
        h = int(r["hour_ts"])
        cat = r["category_name"] or "기타"
        chans, tv = int(r["chans"] or 0), float(r["total_v"] or 0)
        sv, sn = int(r["sum_v"] or 0), int(r["sn"] or 0)

        b = hours.setdefault(h, {"chans": 0, "v": 0.0, "sv": 0, "sn": 0})
        b["chans"] += chans; b["v"] += tv; b["sv"] += sv; b["sn"] += sn

        c = cats.setdefault(cat, {"sv": 0, "sn": 0, "peak_ch": 0, "peak_v": 0.0})
        c["sv"] += sv; c["sn"] += sn
        c["peak_ch"] = max(c["peak_ch"], chans)
        c["peak_v"] = max(c["peak_v"], tv)
        ch = cat_hours.setdefault(cat, {})
        ch[h] = {"chans": chans, "v": tv}

    # ① 시계열 — 시간 또는 일 단위로 다시 묶는다
    buckets: dict[int, dict] = {}
    for h, b in hours.items():
        key = h if bucket == "hour" else (h + 32400) // 86400 * 86400 - 32400  # KST 자정 기준
        g = buckets.setdefault(key, {"v": 0.0, "chans": 0, "n": 0, "sv": 0})
        g["v"] += b["v"]; g["chans"] += b["chans"]; g["n"] += 1; g["sv"] += b["sv"]
    series = [{"t": k,
               "viewers": round(g["v"] / g["n"]),
               "channels": round(g["chans"] / g["n"]),
               "viewership": round(g["sv"] * snap_min / 60)}
              for k, g in sorted(buckets.items())]

    # ② 요약 — 피크는 '시간 버킷의 동시 시청자 합'이 가장 컸던 시각
    peak_h = max(hours.items(), key=lambda kv: kv[1]["v"])
    total_sv = sum(b["sv"] for b in hours.values())
    summary = {
        "viewership":     round(total_sv * snap_min / 60),
        "avg_viewers":    round(sum(b["v"] for b in hours.values()) / len(hours)),
        "peak_viewers":   round(peak_h[1]["v"]),
        "peak_at":        peak_h[0],
        "avg_channels":   round(sum(b["chans"] for b in hours.values()) / len(hours)),
        "total_channels": None,   # 아래에서 채운다
        "top_category":   "",
        "top_category_share": 0.0,
    }

    # ③ 시간대별 / 요일별 평균 — 시간 버킷을 KST 시/요일로 되묶는다
    hb: dict[int, list] = {h: [] for h in range(24)}
    db_: dict[int, list] = {d: [] for d in range(7)}
    for h, b in hours.items():
        dt = datetime.fromtimestamp(h, _KST)
        hb[dt.hour].append(b["v"])
        db_[dt.weekday()].append(b["v"])   # weekday(): 월=0 — 화면 순서와 같다
    hourly = [{"hour": h, "avg_viewers": round(sum(v) / len(v)) if v else 0, "samples": len(v)}
              for h, v in hb.items()]
    dow = [{"dow": d, "avg_viewers": round(sum(v) / len(v)) if v else 0, "samples": len(v)}
           for d, v in db_.items()]

    # ④ 카테고리 표
    table = []
    for cat, c in cats.items():
        hs = cat_hours.get(cat, {})
        n = len(hs) or 1
        table.append({
            "category":       cat,
            "hours":          round(c["sn"] * snap_min / 60, 1),
            "peak_channels":  c["peak_ch"],
            "avg_channels":   round(sum(x["chans"] for x in hs.values()) / n, 1),
            "peak_viewers":   round(c["peak_v"]),
            "avg_viewers":    round(sum(x["v"] for x in hs.values()) / n),
            "viewership":     round(c["sv"] * snap_min / 60),
        })
    table.sort(key=lambda x: x["viewership"], reverse=True)
    if table:
        tot = sum(t["viewership"] for t in table) or 1
        summary["top_category"] = table[0]["category"]
        summary["top_category_share"] = round(table[0]["viewership"] / tot * 100, 1)

    # 기간 내 순 채널 수 — 시간 합계로는 알 수 없어 따로 센다
    crow = await (await db.execute(
        f"""SELECT COUNT(*) AS n FROM (
                SELECT chzzk_channel_id FROM rising_hourly_rollup WHERE {base_where}
                GROUP BY chzzk_channel_id
                HAVING SUM(sum_viewers) * 1.0 / NULLIF(SUM(snaps),0) BETWEEN ? AND ?)""",
        (*params, lo, hi)
    )).fetchone()
    summary["total_channels"] = int(crow["n"] or 0) if crow else 0

    out = {"range": range_key, "start": s, "end": min(e, now), "tier": tier,
           "category": category or "", "tags": tag_list,
           "tag_scope_hours": tag_scope_hours, "bucket": bucket,
           "summary": summary, "series": series, "hourly": hourly,
           "dow": dow, "table": table}
    _pa_cache[ck] = (time.time(), out)
    if len(_pa_cache) > 200:   # 필터 조합이 무한하므로 상한을 둔다
        for k in list(_pa_cache)[:100]:
            _pa_cache.pop(k, None)
    return out


@router.get("/period-filters")
async def period_filters():
    """필터 드롭다운 채우기용 — 최근 이력에 실제로 존재하는 카테고리/태그 목록."""
    now = int(time.time())
    db = await get_db()
    crows = await (await db.execute(
        """SELECT category_name AS c, SUM(sum_viewers) AS v FROM rising_hourly_rollup
           WHERE hour_ts >= ? AND category_name <> ''
           GROUP BY category_name ORDER BY v DESC LIMIT 200""",
        (now - 8 * 86400,)
    )).fetchall()
    trows = await (await db.execute(
        "SELECT tags FROM rising_live_snapshots WHERE collected_at >= ? AND tags <> ''",
        (now - 26 * 3600,)
    )).fetchall()
    tc: Counter = Counter()
    for r in trows:
        for t in _split_tags(r["tags"]):
            tc[t] += 1
    return {"categories": [r["c"] for r in crows],
            "tags": [{"tag": t, "lives": n} for t, n in tc.most_common(40)]}
