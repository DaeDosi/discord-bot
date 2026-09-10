"""CHZZK-STATS-PERF-2 — `newcomers`의 single-flight·캐시·응답 계약.

## 왜 필요한가

캐시(60초 TTL)가 만료되는 순간 동시에 들어온 요청이 **각자** 계산을 시작하면, 그
무거운 롤업 집계가 요청 수만큼 반복된다. `database.get_db()`는 **전역 단일 aiosqlite
커넥션**이고 aiosqlite는 커넥션당 워커 스레드 하나로 쿼리를 **직렬 처리**하므로,
그 쿼리들이 줄을 서면서 뒤에 온 가벼운 API까지 함께 밀린다.

동일 fixture(롤업 50.4만 행) **동시 5건** 창에서의 실측 — 합류시키기 전 → 후:

    endpoint 계산 본체 실행       5회   →  1회
    롤업 GROUP BY 쿼리 실행      10회   →  2회   (계산 1회가 쿼리 2개를 돈다)
    외부 enrich 호출            400회   → 80회
    같이 돈 가벼운 DB 쿼리 최대  1012ms → 100ms
    event-loop lag 최대          1342ms → 252ms  (아무 일도 안 할 때의 바닥값 12.7ms)

⚠️ "계산 횟수"와 "쿼리 실행 횟수"는 **다른 지표다.** 한 계산이 롤업 GROUP BY를 두 번
   돌기 때문에 동시 5건이 10회로 보인다 — 같은 이름으로 부르지 말 것.

⚠️ 이 보호는 **프로세스 안에서만** 유효하다. 인스턴스가 여럿이면 인스턴스마다 한 번씩
계산한다 — 그 사실을 이 파일의 테스트 이름과 주석에 남겨 둔다.

## 이 파일이 고정하는 것

- 동시 요청이 몇 번 계산하는가 (호출 횟수를 **실제로 센다**)
- 실패가 캐시되거나 진행 중 표시를 영구 점유하지 않는가
- `limit`이 달라도 같은 캐시를 공유하면서 응답 행 수는 정확한가
- 응답 JSON 구조·정렬·요약 값이 그대로인가
"""
import asyncio

import pytest

TS = 1_789_000_000


def _cid(i: int) -> str:
    return f"{i:032x}"


@pytest.fixture
def nc(db):
    """롤업·스냅샷·캐시·진행중 표시를 모두 초기화한다."""
    import routers.rising_router as rr
    import streamer_tags as st

    import database

    async def _clear():
        conn = await database.get_db()
        for t in ("rising_live_snapshots", "rising_hourly_rollup", "rising_channel_stats",
                  "rising_collect_runs", "chzzk_channel_history",
                  "streamer_tag_assignments", "streamer_tags"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.commit()

    db(_clear())
    rr._newcomers_cache.clear()
    rr._newcomers_inflight.clear()
    st.reset_state()
    return db


async def _seed(channels=12, hours=48, *, ts=TS, debut_days=5):
    """전원이 '신규'(60일 이내) 조건을 만족하도록 심는다."""
    from database import get_db
    conn = await get_db()
    await conn.execute(
        "INSERT INTO rising_collect_runs (collected_at, live_count, total_viewers, ok)"
        " VALUES (?,?,?,1)", (ts, channels, 0))
    await conn.executemany(
        "INSERT INTO rising_live_snapshots (collected_at, chzzk_channel_id, channel_name,"
        " concurrent_viewers, category_name, open_date, follower_count, live_title, tags)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        [(ts, _cid(i), f"채널{i}", 10 + i, "talk", "", 0, "제목", "") for i in range(channels)])
    await conn.executemany(
        "INSERT INTO rising_channel_stats (chzzk_channel_id, first_seen, last_seen, channel_name)"
        " VALUES (?,?,?,?)",
        [(_cid(i), ts - debut_days * 86400, ts, f"채널{i}") for i in range(channels)])
    await conn.executemany(
        "INSERT OR REPLACE INTO rising_hourly_rollup "
        "(hour_ts, chzzk_channel_id, sum_viewers, snaps) VALUES (?,?,?,?)",
        [(ts - h * 3600, _cid(i), 30, 3) for i in range(channels) for h in range(hours)])
    await conn.commit()


def _no_external(monkeypatch):
    """외부 치지직 API를 부르지 않는다 — 테스트는 네트워크에 의존하지 않는다."""
    import routers.rising_router as rr

    async def _fake(client, channel_id):
        return (None, None)

    monkeypatch.setattr(rr, "_fetch_channel_meta", _fake)


# ── 1. single-flight ────────────────────────────────────────────────────────

def test_concurrent_cache_miss_computes_once(nc, monkeypatch):
    """같은 키의 동시 캐시 미스는 **계산 1회**로 합쳐진다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    calls = {"n": 0}
    real = rr._newcomers_compute

    async def counting(*a, **kw):
        calls["n"] += 1
        await asyncio.sleep(0.05)      # 계산이 도는 동안 나머지가 도착하게
        return await real(*a, **kw)

    monkeypatch.setattr(rr, "_newcomers_compute", counting)

    async def _go():
        await _seed()
        rs = await asyncio.gather(*(rr.newcomers(limit=80, group="new") for _ in range(6)))
        return rs

    results = nc(_go())
    assert calls["n"] == 1, f"동시 6건인데 {calls['n']}번 계산했다"
    # 합류한 요청도 같은 내용을 받아야 한다
    first = results[0]
    for r in results[1:]:
        assert r["collected_at"] == first["collected_at"]
        assert [x["chzzk_channel_id"] for x in r["streamers"]] == \
               [x["chzzk_channel_id"] for x in first["streamers"]]


def test_inflight_is_cleared_after_success(nc, monkeypatch):
    """성공 뒤 진행 중 표시가 남으면 다음 요청이 영원히 기다린다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    async def _go():
        await _seed()
        await rr.newcomers(limit=10, group="new")
        return dict(rr._newcomers_inflight)

    assert nc(_go()) == {}


def test_failure_is_not_cached_and_lock_is_released(nc, monkeypatch):
    """계산이 실패해도 (a) 캐시에 남지 않고 (b) 진행 중 표시가 풀려 재시도된다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    state = {"fail": True, "calls": 0}
    real = rr._newcomers_compute

    async def flaky(*a, **kw):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("boom")
        return await real(*a, **kw)

    monkeypatch.setattr(rr, "_newcomers_compute", flaky)

    async def _go():
        await _seed()
        with pytest.raises(RuntimeError):
            await rr.newcomers(limit=10, group="new")
        assert rr._newcomers_inflight == {}, "실패가 진행 중 표시를 점유했다"
        assert rr._newcomers_cache == {}, "실패를 캐시했다"
        state["fail"] = False
        ok = await rr.newcomers(limit=10, group="new")     # 곧바로 재시도된다
        return ok, state["calls"]

    ok, calls = nc(_go())
    assert ok["streamers"], "재시도가 정상 결과를 내지 못했다"
    assert calls == 2, f"재시도가 막혔다(호출 {calls}회)"


def test_concurrent_failure_propagates_to_joiners(nc, monkeypatch):
    """합류한 요청도 같은 실패를 받는다 — 조용히 빈 결과를 돌려주지 않는다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    async def boom(*a, **kw):
        await asyncio.sleep(0.05)
        raise RuntimeError("boom")

    monkeypatch.setattr(rr, "_newcomers_compute", boom)

    async def _go():
        await _seed()
        rs = await asyncio.gather(*(rr.newcomers(limit=10, group="new") for _ in range(4)),
                                  return_exceptions=True)
        return rs, dict(rr._newcomers_inflight)

    rs, inflight = nc(_go())
    assert all(isinstance(r, RuntimeError) for r in rs), f"실패가 감춰졌다: {rs}"
    assert inflight == {}, "실패 후 진행 중 표시가 남았다"


# ── 2. 캐시 계약 ────────────────────────────────────────────────────────────

def test_cache_hit_skips_computation(nc, monkeypatch):
    """TTL 안에서는 다시 계산하지 않는다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    calls = {"n": 0}
    real = rr._newcomers_compute

    async def counting(*a, **kw):
        calls["n"] += 1
        return await real(*a, **kw)

    monkeypatch.setattr(rr, "_newcomers_compute", counting)

    async def _go():
        await _seed()
        await rr.newcomers(limit=10, group="new")
        await rr.newcomers(limit=10, group="new")
        await rr.newcomers(limit=10, group="new")

    nc(_go())
    assert calls["n"] == 1, f"캐시 히트인데 {calls['n']}번 계산했다"


def test_cache_expiry_recomputes(nc, monkeypatch):
    """TTL이 지나면 다시 계산한다(캐시 시각을 과거로 돌려 만료를 만든다)."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    calls = {"n": 0}
    real = rr._newcomers_compute

    async def counting(*a, **kw):
        calls["n"] += 1
        return await real(*a, **kw)

    monkeypatch.setattr(rr, "_newcomers_compute", counting)

    async def _go():
        await _seed()
        await rr.newcomers(limit=10, group="new")
        # 캐시 항목의 시각을 61초 전으로 밀어 만료시킨다(벽시계를 기다리지 않는다)
        for k, (at, v) in list(rr._newcomers_cache.items()):
            rr._newcomers_cache[k] = (at - 61, v)
        await rr.newcomers(limit=10, group="new")

    nc(_go())
    assert calls["n"] == 2, f"TTL이 지났는데 재계산하지 않았다({calls['n']})"


def test_tag_version_busts_cache(nc, monkeypatch):
    """태그 세대가 바뀌면 낡은 응답이 나가면 안 된다(기존 계약)."""
    import routers.rising_router as rr
    import streamer_tags as st
    _no_external(monkeypatch)

    calls = {"n": 0}
    real = rr._newcomers_compute

    async def counting(*a, **kw):
        calls["n"] += 1
        return await real(*a, **kw)

    monkeypatch.setattr(rr, "_newcomers_compute", counting)

    async def _go():
        await _seed()
        await rr.newcomers(limit=10, group="new")
        st._bump()                       # 태그가 바뀐 상황
        await rr.newcomers(limit=10, group="new")

    nc(_go())
    assert calls["n"] == 2, "태그 세대가 바뀌었는데 캐시를 그대로 썼다"


# ── 3. 응답 계약 (limit 공유) ───────────────────────────────────────────────

def test_limit_is_applied_per_request_not_cached(nc, monkeypatch):
    """캐시는 **자르지 않은 전체**를 담고 `limit`은 요청마다 적용된다.

    예전처럼 잘린 결과를 캐시하면, 먼저 온 `limit=10` 요청 때문에 뒤이은
    `limit=80` 요청이 10행만 받는다.
    """
    import routers.rising_router as rr
    _no_external(monkeypatch)

    async def _go():
        await _seed(channels=12)
        small = await rr.newcomers(limit=3, group="new")
        big = await rr.newcomers(limit=50, group="new")     # 같은 캐시를 쓴다
        return small, big

    small, big = nc(_go())
    assert len(small["streamers"]) == 3
    assert len(big["streamers"]) == 12, \
        f"캐시된 잘린 목록이 새 limit을 막았다: {len(big['streamers'])}"
    # 요약은 원래도 '자르기 전 전체' 기준이다 — 둘이 같아야 한다
    assert small["summary"] == big["summary"]
    assert small["categories"] == big["categories"]


def test_response_shape_and_order_unchanged(nc, monkeypatch):
    """JSON 구조·정렬 계약을 고정한다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    async def _go():
        await _seed(channels=8)
        return await rr.newcomers(limit=80, group="new")

    r = nc(_go())
    assert set(r) == {"collected_at", "group", "streamers", "summary", "insights",
                      "categories", "criteria"}
    assert r["group"] == "new"
    assert r["criteria"] == {"debut_max_days": rr._NEW_DEBUT_MAX_DAYS,
                             "small_avg_max": rr._SMALL_AVG_MAX}
    keys = {"chzzk_channel_id", "channel_name", "concurrent_viewers", "growth_rate",
            "debut_days", "first_stream_date", "first_stream_source", "is_new", "tags"}
    assert keys <= set(r["streamers"][0])
    # 정렬: 성장률 내림차순(없으면 맨 뒤)
    gr = [x["growth_rate"] if x["growth_rate"] is not None else -1e9 for x in r["streamers"]]
    assert gr == sorted(gr, reverse=True), "정렬 계약이 깨졌다"


def test_no_successful_run_returns_empty_contract(nc, monkeypatch):
    """수집 회차가 없으면 오류가 아니라 빈 목록 계약을 지킨다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    async def _go():
        return await rr.newcomers(limit=10, group="new")

    r = nc(_go())
    assert r == {"collected_at": None, "group": "new", "streamers": []}


def test_unknown_group_falls_back_to_new(nc, monkeypatch):
    """알 수 없는 group은 기존대로 `new`로 떨어진다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    async def _go():
        await _seed(channels=5)
        return await rr.newcomers(limit=10, group="bogus")

    assert nc(_go())["group"] == "new"


def test_groups_do_not_share_cache(nc, monkeypatch):
    """`new`와 `small`은 서로 다른 키다 — 한쪽 결과가 다른 쪽으로 새면 안 된다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)

    calls = {"n": 0}
    real = rr._newcomers_compute

    async def counting(*a, **kw):
        calls["n"] += 1
        return await real(*a, **kw)

    monkeypatch.setattr(rr, "_newcomers_compute", counting)

    async def _go():
        await _seed(channels=6)
        a = await rr.newcomers(limit=10, group="new")
        b = await rr.newcomers(limit=10, group="small")
        return a, b

    a, b = nc(_go())
    assert calls["n"] == 2
    assert a["group"] == "new" and b["group"] == "small"


# ── 4. endpoint를 통과하는 시간창 경계 ──────────────────────────────────────

def test_rollup_window_boundary_through_endpoint(nc, monkeypatch):
    """7일 롤업 창의 `hour_ts >= ?` 경계를 **endpoint 응답으로** 고정한다.

    `tests/test_newcomers_rollup_index.py`의 경계 테스트는 SQL 상수를 테스트 쪽에
    복제해 쓰므로 라우터의 실제 쿼리가 바뀌어도 잡지 못한다(변이 검사에서 확인됐다).
    여기서는 응답의 `avg_viewers`로 판정한다 — 경계 행이 집계에 들어갔는지가 값으로 드러난다.
    """
    import routers.rising_router as rr

    from database import get_db
    _no_external(monkeypatch)

    cutoff = TS - 7 * 86400

    async def _go():
        conn = await get_db()
        await conn.execute(
            "INSERT INTO rising_collect_runs (collected_at, live_count, total_viewers, ok)"
            " VALUES (?,?,?,1)", (TS, 1, 0))
        await conn.execute(
            "INSERT INTO rising_live_snapshots (collected_at, chzzk_channel_id, channel_name,"
            " concurrent_viewers, category_name, open_date, follower_count, live_title, tags)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (TS, _cid(0), "채널0", 10, "talk", "", 0, "제목", ""))
        await conn.execute(
            "INSERT INTO rising_channel_stats (chzzk_channel_id, first_seen, last_seen,"
            " channel_name) VALUES (?,?,?,?)", (_cid(0), TS - 5 * 86400, TS, "채널0"))
        # **경계 시각에 정확히 한 행.** 값을 현재 시청자(10)와 크게 다르게 둬
        # 포함 여부가 평균으로 드러나게 한다.
        await conn.execute(
            "INSERT OR REPLACE INTO rising_hourly_rollup "
            "(hour_ts, chzzk_channel_id, sum_viewers, snaps) VALUES (?,?,?,?)",
            (cutoff, _cid(0), 900, 3))
        # **창 바로 바깥에 한 행.** 창 폭이 7일보다 넓어지면 이 행이 딸려 들어와
        # 평균이 무너진다 — 경계 연산자(`>=`)뿐 아니라 **창 폭 자체**도 고정된다.
        await conn.execute(
            "INSERT OR REPLACE INTO rising_hourly_rollup "
            "(hour_ts, chzzk_channel_id, sum_viewers, snaps) VALUES (?,?,?,?)",
            (cutoff - 3600, _cid(0), 30, 3))
        await conn.commit()
        return await rr.newcomers(limit=10, group="new")

    r = nc(_go())
    assert r["streamers"], "행이 사라졌다"
    avg = r["streamers"][0]["avg_viewers"]
    # `>=` · 7일 폭(정상) : 경계 행만 들어와 avg7 = 900/3 = 300
    # `>`  (회귀)          : 경계 행이 빠져 현재 시청자(10)로 떨어진다
    # 8일 폭(회귀)         : 바깥 행까지 들어와 (900+30)/(3+3) = 155가 된다
    assert avg == 300, f"롤업 창이 어긋났다(avg_viewers={avg}, 기대 300)"


# ── 5. 취소 안전성 / 키 구성 (PERF-2 감사에서 추가) ─────────────────────────
#
# 계산을 요청 코루틴에 그대로 붙여 두면 **최초 요청자의 취소가 합류자 전원에게
# 전파된다.** 브라우저 탭을 닫거나 프록시가 연결을 끊으면 그 순간 대기 중이던
# 다른 사용자까지 500을 받는다는 뜻이다. 그래서 계산은 독립 Task로 돌리고
# 요청 쪽은 `asyncio.shield`로 붙는다. 아래 테스트들이 그 구조를 고정한다.

def _gated_compute(monkeypatch, calls):
    """계산을 게이트가 열릴 때까지 붙잡아 두는 스텁을 설치하고 게이트를 돌려준다."""
    import routers.rising_router as rr
    real = rr._newcomers_compute
    box = {}

    async def gated(*a, **kw):
        calls["n"] += 1
        await box["gate"].wait()
        return await real(*a, **kw)

    monkeypatch.setattr(rr, "_newcomers_compute", gated)
    return box


async def _wait_inflight():
    """진행 중 표시가 등록될 때까지 이벤트 루프를 양보한다."""
    import routers.rising_router as rr
    for _ in range(100):
        if rr._newcomers_inflight:
            return
        await asyncio.sleep(0)
    raise AssertionError("진행 중 표시가 등록되지 않았다")


def test_inflight_entry_is_a_task_not_a_coroutine(nc, monkeypatch):
    """공유 대상은 한 번만 await 가능한 coroutine이 아니라 Task여야 한다.

    coroutine을 그대로 나눠 주면 두 번째 합류자가 `cannot reuse already awaited
    coroutine`으로 죽는다.
    """
    import routers.rising_router as rr
    _no_external(monkeypatch)
    calls = {"n": 0}
    box = _gated_compute(monkeypatch, calls)

    async def _go():
        box["gate"] = asyncio.Event()
        await _seed()
        req = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await _wait_inflight()
        entries = list(rr._newcomers_inflight.values())
        box["gate"].set()
        await req
        return entries

    entries = nc(_go())
    assert entries, "진행 중 표시가 비어 있다"
    assert all(isinstance(e, asyncio.Task) for e in entries), \
        f"공유 대상이 Task가 아니다: {[type(e).__name__ for e in entries]}"


def test_leader_cancellation_does_not_kill_joiners(nc, monkeypatch):
    """**최초** 요청이 취소돼도 합류자는 정상 응답을 받는다(재계산 없이)."""
    import routers.rising_router as rr
    _no_external(monkeypatch)
    calls = {"n": 0}
    box = _gated_compute(monkeypatch, calls)

    async def _go():
        box["gate"] = asyncio.Event()
        await _seed()
        leader = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await _wait_inflight()
        joiner = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await asyncio.sleep(0)
        leader.cancel()
        await asyncio.sleep(0)
        box["gate"].set()
        res = await joiner
        return res, leader.cancelled()

    res, leader_cancelled = nc(_go())
    assert leader_cancelled is True, "최초 요청이 취소되지 않았다 — 시나리오가 성립하지 않는다"
    assert calls["n"] == 1, f"합류자가 다시 계산했다({calls['n']}회)"
    assert res["streamers"], "합류자가 빈 응답을 받았다"
    assert rr._newcomers_inflight == {}


def test_joiner_cancellation_does_not_affect_others(nc, monkeypatch):
    """합류자 하나가 취소돼도 나머지와 최초 요청은 그대로 완료된다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)
    calls = {"n": 0}
    box = _gated_compute(monkeypatch, calls)

    async def _go():
        box["gate"] = asyncio.Event()
        await _seed()
        leader = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await _wait_inflight()
        j1 = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        j2 = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await asyncio.sleep(0)
        j1.cancel()
        await asyncio.sleep(0)
        box["gate"].set()
        return await leader, await j2, j1.cancelled()

    lead, other, j1_cancelled = nc(_go())
    assert j1_cancelled is True
    assert calls["n"] == 1, f"취소 때문에 재계산이 일어났다({calls['n']}회)"
    assert lead["streamers"] and other["streamers"]
    assert [s["chzzk_channel_id"] for s in lead["streamers"]] == \
           [s["chzzk_channel_id"] for s in other["streamers"]]


def test_waiter_cancellation_does_not_clear_inflight(nc, monkeypatch):
    """대기자 하나의 취소가 진행 중 표를 **조기에** 지우면 안 된다.

    지워지면 뒤에 온 요청이 이미 도는 계산을 못 보고 처음부터 다시 계산한다.
    """
    import routers.rising_router as rr
    _no_external(monkeypatch)
    calls = {"n": 0}
    box = _gated_compute(monkeypatch, calls)

    async def _go():
        box["gate"] = asyncio.Event()
        await _seed()
        leader = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await _wait_inflight()
        joiner = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await asyncio.sleep(0)
        joiner.cancel()
        await asyncio.sleep(0)
        still_there = len(rr._newcomers_inflight)
        # 취소 직후 도착한 요청도 같은 계산에 합류해야 한다
        late = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await asyncio.sleep(0)
        box["gate"].set()
        await leader
        await late
        return still_there

    assert nc(_go()) == 1, "대기자 취소가 진행 중 표를 지웠다"
    assert calls["n"] == 1


def test_disconnected_leader_still_populates_cache(nc, monkeypatch):
    """모든 요청이 끊겨도 공유 계산은 끝까지 돌아 캐시를 채운다.

    (HTTP client disconnect가 계산을 영구 중단시키거나 다음 요청을 실패시키지 않는다.)
    """
    import routers.rising_router as rr
    _no_external(monkeypatch)
    calls = {"n": 0}
    box = _gated_compute(monkeypatch, calls)

    async def _go():
        box["gate"] = asyncio.Event()
        await _seed()
        leader = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await _wait_inflight()
        shared = next(iter(rr._newcomers_inflight.values()))
        leader.cancel()
        await asyncio.sleep(0)
        box["gate"].set()
        await shared                      # 아무도 안 기다려도 계산은 완주한다
        cached = len(rr._newcomers_cache)
        res = await rr.newcomers(limit=10, group="new")   # 캐시 히트여야 한다
        return cached, res

    cached, res = nc(_go())
    assert cached == 1, "끊긴 계산이 캐시를 채우지 못했다"
    assert calls["n"] == 1, f"다음 요청이 다시 계산했다({calls['n']}회)"
    assert res["streamers"]


def test_release_of_old_task_does_not_delete_new_entry(nc):
    """이전 Task의 정리가 **같은 키의 새 Task**를 지우는 ABA를 막는다."""
    import routers.rising_router as rr

    async def _go():
        async def _noop():
            return {"streamers": []}
        old = asyncio.create_task(_noop())
        new = asyncio.create_task(_noop())
        await old
        await new
        ck = ("new", 12345)
        rr._newcomers_inflight[ck] = new
        rr._newcomers_release(ck, old)         # 늦게 도착한 이전 Task의 정리
        present = rr._newcomers_inflight.get(ck) is new
        rr._newcomers_release(ck, new)         # 자기 자신은 지운다
        gone = ck not in rr._newcomers_inflight
        return present, gone

    present, gone = nc(_go())
    assert present is True, "이전 Task의 정리가 새 항목을 지웠다"
    assert gone is True, "자기 Task의 정리가 항목을 지우지 않았다"


def test_different_keys_do_not_join_the_same_computation(nc, monkeypatch):
    """group이 다른 동시 요청은 **각자** 계산한다(잘못 합류하지 않는다)."""
    import routers.rising_router as rr
    _no_external(monkeypatch)
    calls = {"n": 0}
    box = _gated_compute(monkeypatch, calls)

    async def _go():
        box["gate"] = asyncio.Event()
        await _seed()
        a = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        b = asyncio.create_task(rr.newcomers(limit=10, group="small"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        keys = len(rr._newcomers_inflight)
        box["gate"].set()
        ra, rb = await a, await b
        return keys, ra["group"], rb["group"]

    keys, ga, gb = nc(_go())
    assert keys == 2, f"서로 다른 키인데 진행 중 항목이 {keys}개다"
    assert calls["n"] == 2, f"서로 다른 키인데 {calls['n']}번만 계산했다"
    assert (ga, gb) == ("new", "small")


def test_concurrent_different_limits_share_one_computation(nc, monkeypatch):
    """`limit`이 달라도 계산은 1회, 응답 행 수는 각자 정확하다.

    `limit`을 키에 넣으면 이 공유가 깨지고, 자른 결과를 캐시하면 행 수가 어긋난다.
    """
    import routers.rising_router as rr
    _no_external(monkeypatch)
    calls = {"n": 0}
    box = _gated_compute(monkeypatch, calls)

    async def _go():
        box["gate"] = asyncio.Event()
        await _seed(channels=12)
        tasks = [asyncio.create_task(rr.newcomers(limit=n, group="new")) for n in (3, 12, 7)]
        await asyncio.sleep(0)
        box["gate"].set()
        return [await t for t in tasks]

    r3, r12, r7 = nc(_go())
    assert calls["n"] == 1, f"limit이 다르다고 {calls['n']}번 계산했다"
    assert len(r3["streamers"]) == 3
    assert len(r12["streamers"]) == 12
    assert len(r7["streamers"]) == 7
    # 자르기는 앞에서부터 — 순서 계약이 유지된다
    ids12 = [s["chzzk_channel_id"] for s in r12["streamers"]]
    assert [s["chzzk_channel_id"] for s in r3["streamers"]] == ids12[:3]
    assert [s["chzzk_channel_id"] for s in r7["streamers"]] == ids12[:7]


def test_concurrent_tag_version_change_starts_a_separate_computation(nc, monkeypatch):
    """태그 세대가 바뀌면 키가 달라져 진행 중 계산에 합류하지 않는다."""
    import routers.rising_router as rr
    import streamer_tags as st
    _no_external(monkeypatch)
    calls = {"n": 0}
    box = _gated_compute(monkeypatch, calls)

    async def _go():
        box["gate"] = asyncio.Event()
        await _seed()
        first = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await _wait_inflight()
        st._bump()
        second = asyncio.create_task(rr.newcomers(limit=10, group="new"))
        await asyncio.sleep(0)
        keys = len(rr._newcomers_inflight)
        box["gate"].set()
        await first
        await second
        return keys

    assert nc(_go()) == 2, "태그 세대가 달라졌는데 같은 계산에 합류했다"
    assert calls["n"] == 2


def test_reentry_right_after_cleanup_starts_a_new_computation(nc, monkeypatch):
    """정리 직후 같은 키로 다시 들어오면 새 계산이 정상적으로 시작된다."""
    import routers.rising_router as rr
    _no_external(monkeypatch)
    calls = {"n": 0}
    real = rr._newcomers_compute

    async def counting(*a, **kw):
        calls["n"] += 1
        return await real(*a, **kw)

    monkeypatch.setattr(rr, "_newcomers_compute", counting)

    async def _go():
        await _seed()
        await rr.newcomers(limit=10, group="new")
        assert rr._newcomers_inflight == {}, "1회차 뒤 진행 중 표시가 남았다"
        rr._newcomers_cache.clear()          # TTL 만료와 같은 상태
        r2 = await rr.newcomers(limit=10, group="new")
        return r2

    r2 = nc(_go())
    assert calls["n"] == 2, f"재진입이 새 계산을 시작하지 않았다({calls['n']}회)"
    assert r2["streamers"]
    assert rr._newcomers_inflight == {}


def test_endpoint_signature_is_limit_and_group_only(nc):
    """요청 인자가 늘어나면 in-flight 키 설계를 다시 봐야 한다.

    현재 키는 `(group, 태그 세대)`이고 `limit`은 응답에서만 적용한다. 시간 범위나
    필터·정렬 파라미터가 추가되는 순간 이 키로는 **서로 다른 요청이 같은 계산에
    잘못 합류**하므로, 그 시점에 이 테스트가 깨져 재설계를 강제한다.
    """
    import inspect

    import routers.rising_router as rr
    assert list(inspect.signature(rr.newcomers).parameters) == ["limit", "group"]
