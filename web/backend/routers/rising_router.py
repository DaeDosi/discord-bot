"""CHZZK Rising — 공개(비로그인) 집계 API (Track A 기반).

모든 집계는 rising_live_snapshots(원천 시계열)에서 계산한다. 인증 불필요 —
익명 트래픽/크롤러가 접근하므로 무거운 재계산은 '최신 수집 사이클' 기준으로만 한다.
데이터가 쌓이기 전(수집 시작 직후)에는 일부 지표(라이징/히트맵)가 비어 있을 수 있다.
"""
import time
from fastapi import APIRouter
from database import get_db

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

    return {
        "collected_at": ts,
        "tiers": tiers,
        "blue_ocean": blue_ocean,
        "summary": {"live_count": summ["lives"], "total_viewers": summ["viewers"]} if summ else None,
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
