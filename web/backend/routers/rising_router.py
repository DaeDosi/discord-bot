"""CHZZK Rising — 공개(비로그인) 집계 API (Track A 기반).

모든 집계는 rising_live_snapshots(원천 시계열)에서 계산한다. 인증 불필요 —
익명 트래픽/크롤러가 접근하므로 무거운 재계산은 '최신 수집 사이클' 기준으로만 한다.
데이터가 쌓이기 전(수집 시작 직후)에는 일부 지표(라이징/히트맵)가 비어 있을 수 있다.
"""
import time
import asyncio
import httpx
from datetime import datetime, timezone, timedelta
from collections import Counter
from fastapi import APIRouter
from database import get_db
from rising_collector import latest_image, _fetch_channel_meta

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
    return {
        "last_run": dict(last) if last else None,
        "total_snapshots": total_snap["c"] if total_snap else 0,
        "successful_runs": runs["c"] if runs else 0,
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
_TS_RANGES = {
    "live": (24 * 3600, 0),       # 롤링 24시간(NOW-24h ~ NOW), 원본 10분 간격
    "24h":  (24 * 3600, 3600),    # 최근 24시간, 1시간 평균
    "7d":   (7 * 86400, 3600),    # 최근 7일, 1시간 평균
}


@router.get("/timeseries")
async def timeseries(range: str = "24h"):
    """전체 시청자·라이브 방송 수 시계열 — 꺾은선 그래프용.

    rising_collect_runs(사이클당 1행, 영구 보관)에서 읽으므로 이력이 계속 누적된다.
    range=live(롤링 24h, 원본 10분) / 24h(1시간 평균) / 7d(1시간 평균).
    """
    window, bucket = _TS_RANGES.get(range, _TS_RANGES["24h"])
    since = int(time.time()) - window
    db = await get_db()
    if bucket == 0:
        rows = await (await db.execute(
            """SELECT collected_at AS t, live_count, total_viewers
               FROM rising_collect_runs
               WHERE ok=1 AND collected_at >= ?
               ORDER BY collected_at ASC""",
            (since,)
        )).fetchall()
    else:
        rows = await (await db.execute(
            """SELECT (collected_at/?)*?                     AS t,
                      CAST(AVG(live_count) AS INTEGER)       AS live_count,
                      CAST(AVG(total_viewers) AS INTEGER)    AS total_viewers
               FROM rising_collect_runs
               WHERE ok=1 AND collected_at >= ?
               GROUP BY collected_at/?
               ORDER BY t ASC""",
            (bucket, bucket, since, bucket)
        )).fetchall()
    points = [{"t": int(r["t"]), "live_count": r["live_count"], "total_viewers": r["total_viewers"]} for r in rows]
    return {"range": range if range in _TS_RANGES else "24h", "points": points}


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
_newcomers_cache: dict = {"ts": 0, "data": None}


@router.get("/newcomers")
async def newcomers(limit: int = 100):
    """신규 스트리머(하꼬/라이징) — 현재 라이브 중 소형 채널만.

    포함 조건(하나 이상): 신입/하꼬 등 태그 포함 / 최근 평균 시청자 50명 미만.
    최소 3명 이상 컷오프. 채팅(소통 화력)은 미수집이라 잠금.
    """
    now = int(time.time())
    if _newcomers_cache["data"] is not None and now - _newcomers_cache["ts"] < 60:
        return _newcomers_cache["data"]

    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "streamers": []}
    db = await get_db()

    cur = await (await db.execute(
        """SELECT chzzk_channel_id, channel_name, concurrent_viewers, category_name,
                  open_date, follower_count, live_title, tags
           FROM rising_live_snapshots
           WHERE collected_at=? AND concurrent_viewers >= 3""",
        (ts,)
    )).fetchall()

    # 채널별 전체 보관창 평균/첫 관측 + 최근 7일 평균
    agg = {r["chzzk_channel_id"]: (r["avg_all"], r["first_seen"]) for r in await (await db.execute(
        "SELECT chzzk_channel_id, AVG(concurrent_viewers) AS avg_all, MIN(collected_at) AS first_seen "
        "FROM rising_live_snapshots GROUP BY chzzk_channel_id"
    )).fetchall()}
    agg7 = {r["chzzk_channel_id"]: r["avg7"] for r in await (await db.execute(
        "SELECT chzzk_channel_id, AVG(concurrent_viewers) AS avg7 FROM rising_live_snapshots "
        "WHERE collected_at >= ? GROUP BY chzzk_channel_id", (ts - 7 * 86400,)
    )).fetchall()}

    out = []
    for r in cur:
        cid = r["chzzk_channel_id"]
        avg_all, first_seen = agg.get(cid, (r["concurrent_viewers"], ts))
        first_days = round((ts - int(first_seen)) / 86400, 1)
        tags = r["tags"] or ""
        tag_new = any(t in tags for t in _NEW_TAGS)
        # 포함: 신입 태그이거나 최근 평균 시청자 50명 미만(하꼬/라이징)만.
        # (첫 수집 30일 조건은 원천 보관이 14일이라 사실상 모든 채널을 통과시켜 대형 채널이
        #  섞이는 문제가 있어 필터에서 제외 — 데뷔일은 아래 컬럼/뱃지 정보로만 유지)
        if not (tag_new or (avg_all is not None and avg_all < _NEWCOMER_AVG_MAX)):
            continue
        avg7 = agg7.get(cid, avg_all) or avg_all or r["concurrent_viewers"]
        growth = round((r["concurrent_viewers"] - avg7) / avg7 * 100, 1) if avg7 and avg7 > 0 else None
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
            "first_seen_days":    first_days,
            "is_new":             first_days <= 7,
            "tag_new":            tag_new,
            "tags":               [t for t in tags.split(",") if t][:4],
        })

    # 기본 정렬: 급성장순(소통 화력은 채팅 미수집이라 프론트에서 잠금)
    out.sort(key=lambda x: (x["growth_rate"] if x["growth_rate"] is not None else -1e9), reverse=True)

    # 소형 채널은 팔로워가 0(상위 100만 보강)이라, 상위 후보를 온디맨드로 팔로워 보강 후
    # '팔로워 100명 이하'만 남긴다. (보강 실패=0은 배제하지 않음)
    enrich = out[:150]
    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient() as client:
        async def _fill(item):
            async with sem:
                fc, _img = await _fetch_channel_meta(client, item["chzzk_channel_id"])
                if fc is not None:
                    item["follower_count"] = fc
        await asyncio.gather(*[_fill(x) for x in enrich])
    out = [x for x in enrich if x["follower_count"] <= 100]

    # ── KPI 요약 ──────────────────────────────────────────────────────────
    count = len(out)
    total_v = sum(x["concurrent_viewers"] for x in out)
    avg_v = round(total_v / count) if count else 0
    peak_v = max((x["concurrent_viewers"] for x in out), default=0)
    summary = {"count": count, "total_viewers": total_v, "avg_viewers": avg_v, "peak_viewers": peak_v}

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
    hrows = await (await db.execute(
        """SELECT CAST(strftime('%H', collected_at + 32400, 'unixepoch') AS INTEGER) AS h,
                  SUM(concurrent_viewers) AS v,
                  COUNT(*)                AS n
           FROM rising_live_snapshots
           WHERE collected_at >= ?
             AND chzzk_channel_id IN (
                   SELECT chzzk_channel_id FROM rising_live_snapshots
                   GROUP BY chzzk_channel_id
                   HAVING AVG(concurrent_viewers) < ?)
           GROUP BY h""",
        (ts - 86400, _NEWCOMER_AVG_MAX)
    )).fetchall()
    hour_agg = {int(r["h"]): {"v": int(r["v"] or 0), "n": int(r["n"] or 0)} for r in hrows if r["n"]}
    # 표본이 어느 정도 쌓인 시간대만 후보 (한두 개 스냅샷으로 최적 시간대가 뒤집히지 않게)
    pool = {h: e for h, e in hour_agg.items() if e["n"] >= _GOLDEN_HOUR_MIN_SAMPLES} or hour_agg
    if pool:
        h, e = max(pool.items(), key=lambda kv: kv[1]["v"] / kv[1]["n"])
        overall = sum(e2["v"] for e2 in pool.values()) / max(1, sum(e2["n"] for e2 in pool.values()))
        hour_avg = e["v"] / e["n"]
        uplift = round((hour_avg / overall - 1) * 100) if overall > 0 else 0
        golden_hour = {"hour": h, "avg_viewers": round(hour_avg), "uplift_pct": uplift,
                       "samples": e["n"], "hours_covered": len(hour_agg)}

    # 3) 체급 기준선 — 신입 평균 + 상위 20%/10% 컷오프(구체적 목표 수치)
    baseline = None
    if count:
        sv = sorted(x["concurrent_viewers"] for x in out)  # 오름차순
        def _cut(p: float) -> int:
            return int(sv[min(int(count * p), count - 1)])
        top20, top10 = _cut(0.80), _cut(0.90)
        baseline = {
            "avg_viewers": avg_v,
            "top20_cut":   max(top20, avg_v + 1),
            "top10_cut":   max(top10, top20, avg_v + 1),
            "next_target": max(top20, avg_v + 1),  # 하위호환
        }

    insights = {"top_category": top_category, "golden_hour": golden_hour, "baseline": baseline}

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

    result = {"collected_at": ts, "streamers": out[:limit], "summary": summary,
              "insights": insights, "categories": categories}
    _newcomers_cache["ts"] = now
    _newcomers_cache["data"] = result
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


async def _fetch_first_broadcast(channel_id: str) -> str | None:
    """채널 다시보기(VOD) 목록에서 가장 오래된 영상 날짜로 첫 방송을 추정한다(공식 API에
    개설일/첫방송일 필드가 없어 이 방식이 유일). VOD 삭제 등으로 실제보다 늦을 수 있어 '추정'."""
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


@router.get("/streamer/{channel_id}")
async def streamer(channel_id: str, days: int = 30):
    """스트리머 개인 분석 — 최근 days일(보관 한계 14일) 스냅샷 집계.

    각 스냅샷 ≈ 라이브 10분으로 보고 방송시간/뷰어쉽(시청자-시간)을 추정한다.
    치지직 공개 API가 과거 데이터를 안 주므로 이력은 우리가 수집한 기간만큼만 존재한다.
    """
    days = max(1, min(400, days))
    since = int(time.time()) - days * 86400
    db = await get_db()
    rows = await (await db.execute(
        """SELECT collected_at, concurrent_viewers, follower_count, category_name, live_title, channel_name
           FROM rising_live_snapshots
           WHERE chzzk_channel_id=? AND collected_at >= ?
           ORDER BY collected_at ASC""",
        (channel_id, since)
    )).fetchall()

    latest_ts = await _latest_run_ts()
    if not rows:
        return {"found": False, "channel_id": channel_id, "channel_image_url": latest_image(channel_id)}

    last = rows[-1]
    viewers = [r["concurrent_viewers"] for r in rows]
    n = len(rows)
    snap_min = 10  # 스냅샷 간격(분) — 라이브 1구간 근사
    broadcast_hours = round(n * snap_min / 60, 1)
    viewership = round(sum(viewers) * snap_min / 60)  # 시청자-시간(뷰어-hour)
    avg_v = round(sum(viewers) / n) if n else 0

    # 카테고리 비중(스냅샷 수 기준)
    cat_counter = Counter(r["category_name"] for r in rows if r["category_name"])
    cat_total = sum(cat_counter.values()) or 1
    categories = [
        {"category": c, "share": round(cnt / cat_total * 100, 1), "snapshots": cnt}
        for c, cnt in cat_counter.most_common(6)
    ]

    # 일별(잔디용)
    daily_map: dict[str, dict] = {}
    for r in rows:
        d = _kst_date(r["collected_at"])
        e = daily_map.setdefault(d, {"n": 0, "sv": 0, "peak": 0})
        e["n"] += 1
        e["sv"] += r["concurrent_viewers"]
        e["peak"] = max(e["peak"], r["concurrent_viewers"])
    daily = [
        {"date": d, "minutes": e["n"] * snap_min, "avg_viewers": round(e["sv"] / e["n"]),
         "peak": e["peak"], "viewership": round(e["sv"] * snap_min / 60)}
        for d, e in sorted(daily_map.items())
    ]

    # 주별(추이용)
    week_map: dict[str, dict] = {}
    for r in rows:
        wk, _ = _kst_week(r["collected_at"])
        e = week_map.setdefault(wk, {"n": 0, "sv": 0, "peak": 0, "t": r["collected_at"]})
        e["n"] += 1
        e["sv"] += r["concurrent_viewers"]
        e["peak"] = max(e["peak"], r["concurrent_viewers"])
    weekly = [
        {"week": wk, "t": e["t"], "avg_viewers": round(e["sv"] / e["n"]),
         "peak": e["peak"], "viewership": round(e["sv"] * snap_min / 60)}
        for wk, e in sorted(week_map.items())
    ]

    first_broadcast = await _fetch_first_broadcast(channel_id)  # 다시보기 최고령 = 첫 방송 추정

    # 소형 채널은 스냅샷 팔로워가 0(상위 100만 보강)이라, 개인 페이지에선 1회 조회로 보강
    live_follower = None
    try:
        async with httpx.AsyncClient() as _c:
            live_follower, _ = await _fetch_channel_meta(_c, channel_id)
    except Exception:
        live_follower = None
    follower_now = live_follower if live_follower is not None else last["follower_count"]
    max_follower = max([follower_now] + [r["follower_count"] for r in rows])

    return {
        "found": True,
        "channel_id": channel_id,
        "channel_name": last["channel_name"],
        "channel_image_url": latest_image(channel_id),
        "live_title": last["live_title"],
        "follower_count": follower_now,
        "is_live": bool(latest_ts and last["collected_at"] == latest_ts),
        "first_broadcast": first_broadcast,   # 추정(다시보기 기반), 없으면 None
        "window_days": days,
        "history_days": len(daily_map),
        "summary": {
            "peak_viewers": max(viewers),
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
