"""CHZZK Rising — 공개(비로그인) 집계 API (Track A 기반).

모든 집계는 rising_live_snapshots(원천 시계열)에서 계산한다. 인증 불필요 —
익명 트래픽/크롤러가 접근하므로 무거운 재계산은 '최신 수집 사이클' 기준으로만 한다.
데이터가 쌓이기 전(수집 시작 직후)에는 일부 지표(라이징/히트맵)가 비어 있을 수 있다.
"""
import time
from fastapi import APIRouter
from database import get_db
from rising_collector import latest_image

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
    "live": (6 * 3600, 0),        # 최근 6시간, 원본 10분 간격
    "24h":  (24 * 3600, 3600),    # 최근 24시간, 1시간 평균
    "7d":   (7 * 86400, 3600),    # 최근 7일, 1시간 평균
}


@router.get("/timeseries")
async def timeseries(range: str = "24h"):
    """전체 시청자·라이브 방송 수 시계열 — 꺾은선 그래프용.

    rising_collect_runs(사이클당 1행, 영구 보관)에서 읽으므로 이력이 계속 누적된다.
    range=live(6h 원본) / 24h(1시간 평균) / 7d(1시간 평균).
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
    rows = await (await db.execute(
        """SELECT chzzk_channel_id, channel_name, concurrent_viewers,
                  category_name, open_date, follower_count, live_title, adult
           FROM rising_live_snapshots
           WHERE collected_at=?
           ORDER BY concurrent_viewers DESC
           LIMIT ?""",
        (ts, limit)
    )).fetchall()
    streamers = [
        {
            "rank": i + 1,
            "chzzk_channel_id":   r["chzzk_channel_id"],
            "channel_name":       r["channel_name"],
            "channel_image_url":  latest_image(r["chzzk_channel_id"]),  # DB 아님 — 메모리 맵
            "concurrent_viewers": r["concurrent_viewers"],
            "category_name":      r["category_name"],
            "open_date":          r["open_date"],
            "follower_count":     r["follower_count"],
            "live_title":         r["live_title"],
            "adult":              bool(r["adult"]),
        }
        for i, r in enumerate(rows)
    ]
    return {"collected_at": ts, "streamers": streamers}


@router.get("/categories")
async def categories(limit: int = 60):
    """최신 사이클의 카테고리(게임)별 집계 — 시청자 내림차순 전체 목록.

    카테고리 탭용. 방송 수 필터 없이 전 카테고리를 내려주되, 블루오션 지수도 함께.
    """
    ts = await _latest_run_ts()
    if ts is None:
        return {"collected_at": None, "categories": []}
    db = await get_db()
    rows = await (await db.execute(
        """SELECT category_name,
                  COUNT(*)                            AS lives,
                  COALESCE(SUM(concurrent_viewers),0) AS viewers
           FROM rising_live_snapshots
           WHERE collected_at=? AND category_name != ''
           GROUP BY category_name
           ORDER BY viewers DESC
           LIMIT ?""",
        (ts, limit)
    )).fetchall()
    cats = [
        {
            "category": r["category_name"],
            "lives": r["lives"],
            "viewers": r["viewers"],
            "avg_viewers": round(r["viewers"] / r["lives"], 1) if r["lives"] else 0.0,
            "blue_ocean_index": round(r["viewers"] / r["lives"], 1) if r["lives"] else 0.0,
        }
        for r in rows
    ]
    return {"collected_at": ts, "categories": cats}


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
