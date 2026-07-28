"""전체 시청자 추이 시계열 — 기간(24h/48h/72h) · 데이터 품질 · 공백 처리.

이 그래프는 rising_collect_runs(사이클당 1행, 프루닝하지 않음)에서 읽는다.
실패·부분 실패 사이클이 섞이면 없던 급락이 생기고, 빠진 구간을 0으로 메우거나
양끝을 직선으로 이으면 없던 추세가 생긴다. 그 두 가지를 여기서 고정한다.
"""
import time

import pytest
from routers import rising_router as rr

import database

HOUR = 3600
STEP = 600            # 원본 수집 주기


async def _clear():
    c = await database.get_db()
    await c.execute("DELETE FROM rising_collect_runs")
    await c.commit()
    rr._ts_cache.clear()


async def _add(ts, live=2000, viewers=100_000, ok=1, note=""):
    c = await database.get_db()
    await c.execute(
        "INSERT OR REPLACE INTO rising_collect_runs "
        "(collected_at, live_count, total_viewers, ok, note) VALUES (?,?,?,?,?)",
        (int(ts), live, viewers, ok, note))
    await c.commit()


async def _seed(now, hours, *, step=STEP, **kw):
    """now 기준 과거 hours 시간을 step 간격으로 정상 사이클로 채운다."""
    t = now - hours * HOUR
    while t <= now:
        await _add(t, **kw)
        t += step


def _aligned_now():
    """버킷 경계에서 1시간 지난 시각 — '마지막 버킷이 미완성'인 상태를 확정적으로 만든다."""
    return (int(time.time()) // HOUR) * HOUR + 1800


# ── 기간별 응답 형태 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("rng,window,bucket", [
    ("live", 24 * HOUR, 0),
    ("24h",  24 * HOUR, HOUR),
    ("48h",  48 * HOUR, 1800),
    ("72h",  72 * HOUR, HOUR),
    ("7d",    7 * 86400, HOUR),
])
def test_range_window_and_bucket(db, rng, window, bucket):
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 80))                       # 넉넉히 80시간
    d = db(rr.timeseries(range=rng))
    assert d["range"] == rng
    assert d["window_seconds"] == window
    assert d["bucket_seconds"] == bucket
    assert d["step_seconds"] == (bucket or STEP)
    assert d["points"], "정상 사이클이 있는데 그래프가 비면 안 된다"


def test_point_counts_stay_readable(db):
    """72시간을 10분 원본으로 주면 432포인트라 경로가 뭉개진다 — 버킷으로 줄인다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 80))
    counts = {r: len(db(rr.timeseries(range=r))["points"])
              for r in ("live", "48h", "72h")}
    # 시드 기준시각이 버킷 경계에 맞춰져 있어 창 끝이 최대 30분 어긋난다 → 폭으로 검증한다.
    # 중요한 건 정확한 개수가 아니라 '가독 상한(150) 안이고 버킷이 실제로 줄여준다'는 것.
    assert 138 <= counts["live"] <= 150     # 24h / 10분 ≈ 144
    assert 90 <= counts["48h"] <= 102       # 48h / 30분 ≈ 96
    assert 68 <= counts["72h"] <= 78        # 72h / 1시간 ≈ 72
    assert all(n <= 150 for n in counts.values())
    # 원본 그대로였다면 48h=288, 72h=432포인트였다 — 버킷이 실제로 줄이고 있는지 확인
    assert counts["48h"] < 288 / 2 and counts["72h"] < 432 / 3


def test_48h_and_72h_reach_further_back(db):
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 80))
    span = lambda r: (lambda p: p[-1]["t"] - p[0]["t"])(db(rr.timeseries(range=r))["points"])  # noqa: E731
    assert span("48h") > span("live")
    assert span("72h") > span("48h")
    assert span("72h") >= 70 * HOUR


# ── 쿼리 파라미터 검증 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["", "1h", "96h", "LIVE", "live; DROP TABLE x", "../"])
def test_invalid_range_is_rejected_by_api(bad):
    """검증 없이 24h로 조용히 떨어뜨리면 오타를 영영 모른다 → 422."""
    from fastapi.testclient import TestClient
    from test_security import _load_app
    client = TestClient(_load_app())          # lifespan을 띄우지 않는다(수집기 기동 방지)
    assert client.get("/api/rising/timeseries", params={"range": bad}).status_code == 422


def test_default_range_is_valid():
    from fastapi.testclient import TestClient
    from test_security import _load_app
    client = TestClient(_load_app())
    r = client.get("/api/rising/timeseries")
    assert r.status_code == 200 and r.json()["range"] == "24h"


# ── 데이터 품질: 정상 완료된 사이클만 ───────────────────────────────────────
def test_failed_cycles_are_excluded(db):
    """ok=0(수집 실패)은 (0,0)으로 기록된다 — 그리면 바닥까지 꽂히는 가짜 급락이 된다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 4))
    db(_add(now - 2 * HOUR, live=0, viewers=0, ok=0, note="fetch 실패"))
    d = db(rr.timeseries(range="live"))
    assert all(p["total_viewers"] > 0 for p in d["points"])
    assert d["excluded_points"] >= 1


def test_truncated_cycles_are_excluded(db):
    """page_cap_reached = 목록이 잘린 부분 성공. 합계가 과소 집계라 급락으로 보인다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 4))
    db(_add(now - 90 * 60, live=200, viewers=9_000, note="page_cap_reached"))
    d = db(rr.timeseries(range="live"))
    assert all(p["total_viewers"] >= 100_000 for p in d["points"]), "과소 집계 사이클이 섞였다"
    assert d["excluded_points"] >= 1


@pytest.mark.parametrize("live,viewers", [(0, 100_000), (2000, 0), (0, 0)])
def test_zero_valued_success_rows_are_excluded(db, live, viewers):
    """ok=1인데 0인 행은 정상 경로에서 나올 수 없는 값이다 — 방어적으로 뺀다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 4))
    db(_add(now - 2 * HOUR, live=live, viewers=viewers, ok=1))
    d = db(rr.timeseries(range="live"))
    assert all(p["live_count"] > 0 and p["total_viewers"] > 0 for p in d["points"])


def test_excluded_count_is_reported(db):
    """빈 구간이 '수집 실패였다'고 설명할 수 있어야 한다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 4))
    for i in (1, 2, 3):
        db(_add(now - i * HOUR - 300, live=0, viewers=0, ok=0))
    assert db(rr.timeseries(range="live"))["excluded_points"] == 3


# ── 수집 공백: 0으로 메우지 않는다 ──────────────────────────────────────────
def test_gap_is_left_as_a_hole_not_zero_filled(db):
    now = _aligned_now()
    db(_clear())
    # 6시간 전 ~ 4시간 전을 통째로 비운다
    t = now - 8 * HOUR
    while t <= now:
        if not (now - 6 * HOUR <= t <= now - 4 * HOUR):
            db(_add(t))
        t += STEP
    d = db(rr.timeseries(range="live"))
    assert all(p["total_viewers"] > 0 for p in d["points"]), "0으로 메우면 안 된다"
    ts = [p["t"] for p in d["points"]]
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b - a > d["step_seconds"] * 1.5]
    assert len(gaps) == 1 and gaps[0] >= 2 * HOUR, "공백이 그대로 남아야 선을 끊을 수 있다"


def test_gap_from_failed_cycles_also_leaves_a_hole(db):
    """실패 사이클을 걸러낸 자리도 '연결'이 아니라 '구멍'이어야 한다."""
    now = _aligned_now()
    db(_clear())
    t = now - 6 * HOUR
    while t <= now:
        bad = now - 4 * HOUR <= t <= now - 3 * HOUR
        db(_add(t, live=0, viewers=0, ok=0) if bad else _add(t))
        t += STEP
    d = db(rr.timeseries(range="live"))
    ts = [p["t"] for p in d["points"]]
    assert any(b - a > d["step_seconds"] * 1.5 for a, b in zip(ts, ts[1:]))


# ── 이력 부족: 버튼을 막지 않고 확보된 만큼 ─────────────────────────────────
def test_short_history_is_reported_not_blocked(db):
    """이력이 10시간뿐이어도 72h는 200을 주고, 확보된 구간까지만 그린다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 10))
    d = db(rr.timeseries(range="72h"))
    assert d["points"], "이력이 짧다고 빈 응답을 주면 버튼이 죽은 것처럼 보인다"
    assert d["truncated"] is True
    assert 9.5 <= d["history_hours"] <= 10.6
    assert d["points"][-1]["t"] - d["points"][0]["t"] <= 11 * HOUR


def test_full_history_is_not_flagged_truncated(db):
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 80))
    assert db(rr.timeseries(range="72h"))["truncated"] is False


def test_history_hours_ignores_failed_cycles(db):
    """맨 앞이 실패 사이클이면 '그만큼 이력이 있다'고 말하면 안 된다."""
    now = _aligned_now()
    db(_clear())
    db(_add(now - 50 * HOUR, live=0, viewers=0, ok=0))
    db(_seed(now, 5))
    assert db(rr.timeseries(range="72h"))["history_hours"] <= 5.6


def test_no_data_is_empty_but_well_formed(db):
    db(_clear())
    d = db(rr.timeseries(range="48h"))
    assert d["points"] == [] and d["history_hours"] == 0.0
    assert d["truncated"] is False and d["step_seconds"] == 1800


# ── 버킷 = 구간 평균, 마지막 버킷은 '집계 중' ───────────────────────────────
def test_bucket_value_is_the_interval_average(db):
    now = _aligned_now()
    db(_clear())
    base = (now // HOUR) * HOUR - 3 * HOUR          # 완결된 과거 버킷
    for i, v in enumerate((100, 200, 300)):         # 같은 시간 버킷 안 3개 사이클
        db(_add(base + i * STEP, live=10, viewers=v))
    p = next(p for p in db(rr.timeseries(range="72h"))["points"] if p["t"] == base)
    assert p["total_viewers"] == 200                # (100+200+300)/3
    assert p["samples"] == 3


def test_only_the_latest_bucket_is_partial(db):
    now = _aligned_now()                            # 정시 + 30분 → 현재 버킷은 미완성
    db(_clear())
    db(_seed(now, 6))
    pts = db(rr.timeseries(range="72h"))["points"]
    assert [p["partial"] for p in pts[:-1]] == [False] * (len(pts) - 1)
    assert pts[-1]["partial"] is True


def test_raw_range_has_no_partial_buckets(db):
    """원본(live)은 사이클 하나가 곧 확정값이라 '집계 중' 개념이 없다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 6))
    pts = db(rr.timeseries(range="live"))["points"]
    assert all(p["partial"] is False and p["samples"] == 1 for p in pts)


def test_samples_lets_frontend_recover_the_true_sum(db):
    """버킷은 평균이므로 뷰어쉽(적분)은 samples를 곱해야 원본 합과 같아진다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 20))
    raw = db(rr.timeseries(range="live"))["points"]
    bucketed = db(rr.timeseries(range="72h"))["points"]
    in_window = [p for p in raw if p["t"] >= bucketed[0]["t"]]
    total_raw = sum(p["total_viewers"] for p in in_window)
    total_bucket = sum(p["total_viewers"] * p["samples"] for p in bucketed)
    assert total_bucket == pytest.approx(total_raw, rel=0.01)


# ── 캐시 ────────────────────────────────────────────────────────────────────
def test_cache_is_keyed_per_range(db):
    """한 키로 뭉뚱그리면 48h를 요청했는데 72h 응답이 나온다."""
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 80))
    a = db(rr.timeseries(range="48h"))
    b = db(rr.timeseries(range="72h"))
    assert a["range"] == "48h" and b["range"] == "72h"
    assert a["window_seconds"] != b["window_seconds"]
    assert db(rr.timeseries(range="48h"))["window_seconds"] == 48 * HOUR


def test_cache_hit_avoids_requery(db):
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 4))
    first = db(rr.timeseries(range="live"))
    db(_add(now + STEP))                     # 캐시 유효시간 안의 새 데이터
    assert db(rr.timeseries(range="live")) is first
    rr._ts_cache.clear()
    assert db(rr.timeseries(range="live")) is not first


def test_cache_does_not_grow_unbounded(db):
    now = _aligned_now()
    db(_clear())
    db(_seed(now, 4))
    for r in ("live", "24h", "48h", "72h", "7d"):
        db(rr.timeseries(range=r))
    assert len(rr._ts_cache) <= len(rr._TS_RANGE_KEYS)


# ── 인덱스 ──────────────────────────────────────────────────────────────────
def test_range_scan_uses_the_collected_at_index(db):
    """collected_at의 UNIQUE 자동 인덱스로 범위 스캔이 되어야 한다(풀스캔 금지)."""
    async def plan():
        c = await database.get_db()
        rows = await (await c.execute(
            f"""EXPLAIN QUERY PLAN
                SELECT (collected_at/3600)*3600 t, AVG(total_viewers)
                FROM rising_collect_runs
                WHERE {rr._TS_QUALITY} AND collected_at >= ?
                GROUP BY collected_at/3600""", (int(time.time()) - 72 * HOUR,))).fetchall()
        return " | ".join(r[-1] for r in rows)
    detail = db(plan())
    assert "SCAN rising_collect_runs" not in detail, f"풀스캔이다: {detail}"
    assert "USING INDEX" in detail and "collected_at>" in detail
