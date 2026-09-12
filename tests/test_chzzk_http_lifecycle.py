"""CHZZK-STATS-PERF-3 — 치지직 공유 HTTP 클라이언트의 수명 계약.

## 왜 필요한가

`httpx.AsyncClient()` 생성은 **동기**다. 이 환경 실측으로 264~346ms가 걸리고 그동안
**이벤트 루프가 통째로 멈춘다.** `/api/rising/newcomers`는 캐시가 만료될 때마다 요청
경로에서 이것을 한 번씩 했다.

    수정 전 cold : loop lag 311.9 ms  (생성 315.6 ms)   ← idle 바닥값 13.5 ms
    수정 후 cold : loop lag  20.1 ms  (생성 0회)

고친 방법은 **앱 수명주기에서 한 번 만들고 공유**하는 것이다. 그런데 공유 자원은
잘못 만들면 더 나쁜 문제를 만든다 — 다른 이벤트 루프로 새어 들어가거나, 닫힌
클라이언트를 다시 쓰거나, 요청 하나의 취소가 모두의 풀을 닫거나, 요청별 비밀이
클라이언트에 남거나. 이 파일은 **그 사고들을 고정한다.**

## 측정 방식에 대한 경고

constructor를 monkeypatch해서 "0회"만 확인하면 안 된다 — 생성 시점을 startup으로
옮겼을 뿐인지, 실제로 요청 중 루프가 안 막히는지는 **heartbeat로 따로 재야** 한다.
그 성능 측정은 `docs/작업정리_...PERF-3...md`에 있고, 이 파일은 **계약**만 본다.
"""
import asyncio

import httpx
import pytest

TS = 1_789_000_000


def _cid(i: int) -> str:
    return f"{i:032x}"


class _App:
    """`app.state`만 흉내 내는 최소 객체 — FastAPI 인스턴스를 만들 필요가 없다."""

    def __init__(self):
        self.state = type("S", (), {})()


@pytest.fixture
def http_env(db):
    """모듈 전역과 rising 테이블을 비우고 시작한다.

    `db` 픽스처는 싱드컵 계열만 비우므로 rising 쪽은 여기서 정리한다 —
    안 하면 앞선 테스트가 남긴 수집 회차와 `collected_at`이 충돌한다.
    """
    import chzzk_http

    import database

    async def _clear():
        conn = await database.get_db()
        for t in ("rising_live_snapshots", "rising_hourly_rollup", "rising_channel_stats",
                  "rising_collect_runs", "chzzk_channel_history",
                  "streamer_tag_assignments", "streamer_tags"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.commit()

    chzzk_http.reset_state_for_tests()
    db(_clear())
    _reset_rising()
    yield db
    chzzk_http.reset_state_for_tests()


def _count_ctor(monkeypatch):
    """`httpx.AsyncClient` 생성/종료 횟수를 실제로 센다."""
    import chzzk_http
    calls = {"ctor": 0, "aclose": 0}
    orig_init = httpx.AsyncClient.__init__
    orig_aclose = httpx.AsyncClient.aclose

    def init(self, *a, **kw):
        calls["ctor"] += 1
        return orig_init(self, *a, **kw)

    async def aclose(self):
        calls["aclose"] += 1
        return await orig_aclose(self)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    monkeypatch.setattr(httpx.AsyncClient, "aclose", aclose)
    assert chzzk_http is not None
    return calls


# ── 1. 수명주기 기본 계약 ───────────────────────────────────────────────────

def test_lifespan_creates_one_client_and_closes_it_once(http_env, monkeypatch):
    """수명주기 하나당 생성 1회 · 종료 정확히 1회."""
    import chzzk_http
    calls = _count_ctor(monkeypatch)

    async def _go():
        app = _App()
        async with chzzk_http.chzzk_client_lifespan(app) as client:
            inside = chzzk_http.get_client()
            on_state = app.state.chzzk_http
            closed_inside = client.is_closed
        return client, inside, on_state, closed_inside

    client, inside, on_state, closed_inside = http_env(_go())
    assert calls["ctor"] == 1, f"수명주기 1회인데 {calls['ctor']}번 만들었다"
    assert calls["aclose"] == 1, f"종료가 {calls['aclose']}번 불렸다"
    assert inside is client, "수명주기 안에서 get_client()가 그 클라이언트를 주지 않는다"
    assert on_state is client, "app.state.chzzk_http에 실리지 않았다"
    assert closed_inside is False
    assert client.is_closed is True, "수명주기가 끝났는데 닫히지 않았다"


def test_client_is_unavailable_outside_lifespan(http_env):
    """수명주기 밖에서는 공유 클라이언트를 주지 않는다(import 시점 전역 없음)."""
    import chzzk_http

    async def _go():
        return chzzk_http.get_client()

    assert http_env(_go()) is None


def test_closed_client_is_never_reused(http_env):
    """수명주기가 끝난 뒤 남은 참조가 있어도 **닫힌 클라이언트는 주지 않는다.**"""
    import chzzk_http

    async def _go():
        app = _App()
        async with chzzk_http.chzzk_client_lifespan(app) as client:
            pass
        # 전역을 강제로 되돌려 놓아도(정리 누락 시나리오) 닫힘 검사가 막아야 한다
        chzzk_http._client = client
        chzzk_http._loop = asyncio.get_running_loop()
        return chzzk_http.get_client(), client.is_closed

    got, closed = http_env(_go())
    assert closed is True
    assert got is None, "닫힌 클라이언트를 그대로 돌려줬다"


def test_client_from_another_event_loop_is_not_reused(http_env):
    """**다른 이벤트 루프**에서 만든 클라이언트는 공유하지 않는다.

    pytest는 테스트마다 루프를 새로 만든다. 이전 루프의 커넥션 풀을 그대로 쓰면
    조용히 깨지므로, 루프가 다르면 없는 것으로 취급해야 한다.
    """
    import chzzk_http
    made = {}

    async def _first():
        app = _App()
        async with chzzk_http.chzzk_client_lifespan(app) as client:
            made["client"] = client
            made["loop"] = asyncio.get_running_loop()
            # 수명주기가 끝나면 클라이언트가 닫히므로, 닫히지 않은 상태를 만들기
            # 위해 여기서 전역만 남겨 두고 나간다(아래에서 강제로 복원한다).
            made["was_shared"] = chzzk_http.get_client() is client
    http_env(_first())

    loop2 = asyncio.new_event_loop()
    try:
        async def _second():
            # 다른 루프에서 만든 '열려 있는' 클라이언트를 전역에 심어 둔다
            other = httpx.AsyncClient()
            chzzk_http._client = other
            chzzk_http._loop = made["loop"]          # 이전(다른) 루프
            got = chzzk_http.get_client()
            await other.aclose()
            return got
        got = loop2.run_until_complete(_second())
    finally:
        loop2.close()

    assert made["was_shared"] is True
    assert got is None, "다른 루프에서 만든 클라이언트가 새어 들어왔다"


def test_separate_lifecycles_use_separate_clients(http_env, monkeypatch):
    """서로 다른 앱 수명주기는 **서로 다른** 클라이언트를 쓴다."""
    import chzzk_http
    calls = _count_ctor(monkeypatch)

    async def _go():
        a, b = _App(), _App()
        async with chzzk_http.chzzk_client_lifespan(a) as c1:
            pass
        async with chzzk_http.chzzk_client_lifespan(b) as c2:
            pass
        return c1, c2, a.state.chzzk_http, b.state.chzzk_http

    c1, c2, sa, sb = http_env(_go())
    assert c1 is not c2, "두 수명주기가 같은 클라이언트를 공유했다"
    assert calls["ctor"] == 2 and calls["aclose"] == 2
    assert sa is None and sb is None, "종료 후에도 app.state에 남아 있다"


def test_nested_lifecycles_restore_the_previous_client(http_env):
    """중첩 수명주기가 끝나면 바깥 것이 그대로 복원된다(전역 오염 없음)."""
    import chzzk_http

    async def _go():
        outer_app, inner_app = _App(), _App()
        async with chzzk_http.chzzk_client_lifespan(outer_app) as outer:
            async with chzzk_http.chzzk_client_lifespan(inner_app) as inner:
                during = chzzk_http.get_client()
            after = chzzk_http.get_client()
        return outer, inner, during, after

    outer, inner, during, after = http_env(_go())
    assert during is inner
    assert after is outer, "안쪽 수명주기가 끝난 뒤 바깥 클라이언트가 복원되지 않았다"


def test_startup_failure_does_not_leave_a_half_initialized_state(http_env, monkeypatch):
    """클라이언트 생성이 실패하면 예외를 올리고 **반쪽 상태로 두지 않는다.**"""
    import chzzk_http

    def boom():
        raise RuntimeError("생성 실패")
    monkeypatch.setattr(chzzk_http, "_new_client", boom)

    async def _go():
        app = _App()
        with pytest.raises(RuntimeError):
            async with chzzk_http.chzzk_client_lifespan(app):
                pass
        return chzzk_http.get_client(), getattr(app.state, "chzzk_http", None)

    got, on_state = http_env(_go())
    assert got is None, "생성 실패인데 공유 클라이언트가 남았다"
    assert on_state is None


def test_client_carries_no_default_headers_or_cookies(http_env):
    """요청별 비밀이 다음 요청으로 잔류하지 않도록 **기본 헤더/쿠키를 두지 않는다.**"""
    import chzzk_http

    async def _go():
        async with chzzk_http.chzzk_client_lifespan(_App()) as client:
            return (dict(client.headers), dict(client.cookies),
                    client.follow_redirects)

    headers, cookies, redirects = http_env(_go())
    lowered = {k.lower() for k in headers}
    assert "authorization" not in lowered
    assert "cookie" not in lowered
    assert cookies == {}, f"클라이언트에 쿠키가 남는다: {cookies}"
    assert redirects is False, "follow_redirects 기본값이 바뀌었다"


def test_request_cookies_do_not_persist_on_the_shared_client(http_env):
    """응답이 Set-Cookie를 줘도 **공유 클라이언트에 쌓이지 않는지** 확인한다.

    쌓이면 다음 사용자의 요청에 남의 쿠키가 실린다. httpx 기본 동작을 고정한다.
    """
    import chzzk_http

    async def handler(request):
        return httpx.Response(200, headers={"set-cookie": "sid=secret; Path=/"}, json={})

    async def _go():
        async with chzzk_http.chzzk_client_lifespan(_App()) as client:
            transport = httpx.MockTransport(handler)
            client._transport = transport
            client._mounts = {}
            await client.get("http://example.invalid/a")
            return dict(client.cookies)

    cookies = http_env(_go())
    assert "sid" not in cookies, f"공유 클라이언트에 쿠키가 남았다: {cookies}"


# ── 2. newcomers 경로와의 결합 ──────────────────────────────────────────────

async def _seed(channels=6, hours=24, ts=TS):
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
        [(_cid(i), ts - 5 * 86400, ts, f"채널{i}") for i in range(channels)])
    await conn.executemany(
        "INSERT OR REPLACE INTO rising_hourly_rollup "
        "(hour_ts, chzzk_channel_id, sum_viewers, snaps) VALUES (?,?,?,?)",
        [(ts - h * 3600, _cid(i), 30, 3) for i in range(channels) for h in range(hours)])
    await conn.commit()


def _reset_rising():
    import routers.rising_router as rr
    import streamer_tags as st
    rr._newcomers_cache.clear()
    rr._newcomers_inflight.clear()
    st.reset_state()


def _mock_meta(monkeypatch, seen, *, fail=False, delay=0.0):
    """enrich를 가로채 **어떤 client 객체가 넘어왔는지** 기록한다."""
    import routers.rising_router as rr

    async def fake(client, channel_id):
        seen.append(client)
        if delay:
            await asyncio.sleep(delay)
        if fail:
            raise httpx.ConnectError("transport 실패")
        return (777, "")
    monkeypatch.setattr(rr, "_fetch_channel_meta", fake)


def test_requests_reuse_the_shared_client_without_constructing(http_env, monkeypatch):
    """수명주기가 있으면 여러 요청이 **같은 클라이언트**를 쓰고 새로 만들지 않는다."""
    import chzzk_http
    import routers.rising_router as rr
    seen = []
    _mock_meta(monkeypatch, seen)
    calls = _count_ctor(monkeypatch)

    async def _go():
        await _seed()
        async with chzzk_http.chzzk_client_lifespan(_App()) as client:
            before = calls["ctor"]
            await rr.newcomers(limit=10, group="new")
            rr._newcomers_cache.clear()
            await rr.newcomers(limit=10, group="new")
            return client, calls["ctor"] - before

    _reset_rising()
    client, made_during = http_env(_go())
    assert made_during == 0, f"요청 처리 중 클라이언트를 {made_during}번 만들었다"
    assert seen and all(c is client for c in seen), "공유 클라이언트를 쓰지 않았다"


def test_concurrent_requests_construct_nothing(http_env, monkeypatch):
    """동시 요청에서도 요청 경로 생성은 0회다(single-flight와 함께)."""
    import chzzk_http
    import routers.rising_router as rr
    seen = []
    _mock_meta(monkeypatch, seen)
    calls = _count_ctor(monkeypatch)

    async def _go():
        await _seed()
        async with chzzk_http.chzzk_client_lifespan(_App()) as client:
            before = calls["ctor"]
            rs = await asyncio.gather(*(rr.newcomers(limit=10, group="new") for _ in range(5)))
            return client, calls["ctor"] - before, rs

    _reset_rising()
    client, made, rs = http_env(_go())
    assert made == 0
    assert all(c is client for c in seen)
    assert len({len(r["streamers"]) for r in rs}) == 1


def test_without_lifespan_it_still_works(http_env, monkeypatch):
    """수명주기가 없어도 **동작은 같다** — 임시 클라이언트로 물러선다."""
    import routers.rising_router as rr
    seen = []
    _mock_meta(monkeypatch, seen)

    async def _go():
        await _seed()
        return await rr.newcomers(limit=10, group="new")

    _reset_rising()
    res = http_env(_go())
    assert res["streamers"], "수명주기 없이 호출하면 응답이 비었다"
    assert seen, "enrich 자체가 돌지 않았다"
    assert all(isinstance(c, httpx.AsyncClient) for c in seen)


def test_request_cancellation_does_not_close_the_shared_client(http_env, monkeypatch):
    """요청 하나가 취소돼도 **공유 클라이언트는 살아 있다.**"""
    import chzzk_http
    import routers.rising_router as rr
    seen = []
    _mock_meta(monkeypatch, seen, delay=0.05)

    async def _go():
        await _seed()
        async with chzzk_http.chzzk_client_lifespan(_App()) as client:
            t = asyncio.create_task(rr.newcomers(limit=10, group="new"))
            await asyncio.sleep(0)
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
            # 공유 계산은 살아 있으므로 끝날 때까지 기다린 뒤 상태를 본다
            for _ in range(200):
                if not rr._newcomers_inflight:
                    break
                await asyncio.sleep(0.01)
            alive = not client.is_closed
            again = await rr.newcomers(limit=10, group="new")
        return alive, again

    _reset_rising()
    alive, again = http_env(_go())
    assert alive is True, "요청 취소가 공유 클라이언트를 닫았다"
    assert again["streamers"], "취소 뒤 다음 요청이 실패했다"


def test_transport_error_does_not_poison_the_pool(http_env, monkeypatch):
    """transport 오류가 나도 **다음 요청이 복구**된다(풀 전체를 버리지 않는다)."""
    import chzzk_http
    import routers.rising_router as rr
    seen = []
    state = {"fail": True}

    async def flaky(client, channel_id):
        seen.append(client)
        if state["fail"]:
            raise httpx.ConnectError("일시적 실패")
        return (777, "")
    monkeypatch.setattr(rr, "_fetch_channel_meta", flaky)

    async def _go():
        await _seed()
        async with chzzk_http.chzzk_client_lifespan(_App()) as client:
            first_err = None
            try:
                await rr.newcomers(limit=10, group="new")
            except Exception as e:                       # noqa: BLE001
                first_err = type(e).__name__
            state["fail"] = False
            rr._newcomers_cache.clear()
            ok = await rr.newcomers(limit=10, group="new")
            return first_err, ok, client.is_closed

    _reset_rising()
    _first_err, ok, closed = http_env(_go())
    assert closed is False, "오류 하나가 공유 클라이언트를 닫았다"
    assert ok["streamers"], "오류 뒤 다음 요청이 복구되지 않았다"
    assert ok["streamers"][0]["follower_count"] == 777


def test_enrich_timeout_and_concurrency_contract_unchanged(http_env, monkeypatch):
    """동시성 12 상한과 enrich 타임아웃 계약이 그대로다."""
    import chzzk_http
    import routers.rising_router as rr
    assert rr._NEWCOMER_ENRICH_TIMEOUT == 3.0
    assert rr._NEWCOMER_ENRICH_N == 80

    live = {"now": 0, "peak": 0}

    async def slow(client, channel_id):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.01)
        live["now"] -= 1
        return (5, "")
    monkeypatch.setattr(rr, "_fetch_channel_meta", slow)

    async def _go():
        await _seed(channels=40)
        async with chzzk_http.chzzk_client_lifespan(_App()):
            await rr.newcomers(limit=40, group="new")
        return live["peak"]

    _reset_rising()
    peak = http_env(_go())
    assert peak <= 12, f"동시성 상한 12를 넘었다: {peak}"


def test_enrich_timeout_gives_up_without_failing_the_response(http_env, monkeypatch):
    """enrich가 타임아웃해도 응답은 정상이고 클라이언트도 멀쩡하다."""
    import chzzk_http
    import routers.rising_router as rr

    async def hang(client, channel_id):
        await asyncio.sleep(10)
        return (1, "")
    monkeypatch.setattr(rr, "_fetch_channel_meta", hang)
    monkeypatch.setattr(rr, "_NEWCOMER_ENRICH_TIMEOUT", 0.05)

    async def _go():
        await _seed()
        async with chzzk_http.chzzk_client_lifespan(_App()) as client:
            t0 = asyncio.get_running_loop().time()
            res = await rr.newcomers(limit=10, group="new")
            return res, client.is_closed, asyncio.get_running_loop().time() - t0

    _reset_rising()
    res, closed, took = http_env(_go())
    assert res["streamers"], "타임아웃 때문에 응답이 비었다"
    assert closed is False
    # enrich가 10초를 자게 해 뒀다. 상한(0.05초)이 살아 있으면 곧바로 포기해야 한다.
    # 여유를 크게 둬 느린 머신에서도 흔들리지 않게 하되, 상한이 사라지면 확실히 걸린다.
    assert took < 2.0, f"enrich 상한이 동작하지 않았다({took:.1f}초 걸렸다)"


def test_payload_is_identical_with_and_without_the_shared_client(http_env, monkeypatch):
    """공유 클라이언트 사용 여부와 무관하게 **응답이 완전히 같다.**"""
    import json

    import chzzk_http
    import routers.rising_router as rr
    seen = []
    _mock_meta(monkeypatch, seen)

    async def _go():
        await _seed()
        a = await rr.newcomers(limit=80, group="new")
        rr._newcomers_cache.clear()
        async with chzzk_http.chzzk_client_lifespan(_App()):
            b = await rr.newcomers(limit=80, group="new")
        return a, b

    _reset_rising()
    a, b = http_env(_go())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), \
        "공유 클라이언트 도입으로 응답이 달라졌다"
    assert [s["chzzk_channel_id"] for s in a["streamers"]] == \
           [s["chzzk_channel_id"] for s in b["streamers"]]


def test_scope_is_not_widened_to_the_whole_repo(http_env):
    """공유 클라이언트를 저장소 전체에 퍼뜨리지 않는다.

    이 저장소에는 `httpx.AsyncClient()` 생성 지점이 40곳 넘게 있고(디스코드 OAuth,
    관리자 API, 봇 cog, relay), **그것들은 이번 범위가 아니다.** 치지직 enrich 경로
    하나만 공유한다 — 대상 호스트가 고정이고 요청별 비밀을 싣지 않는 경로다.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    users = []
    for f in root.rglob("*.py"):
        parts = set(f.parts)
        if "tests" in parts or "node_modules" in parts or ".git" in parts:
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if "chzzk_http" in txt and f.name != "chzzk_http.py":
            users.append(f.name)
    assert sorted(users) == ["main.py", "rising_router.py"], \
        f"공유 클라이언트 사용처가 늘었다: {sorted(users)}"


def test_importing_the_module_creates_no_client(http_env, monkeypatch):
    """**import 부작용으로 자원을 만들지 않는다.**

    모듈 전역에서 `httpx.AsyncClient()`를 만들면 (a) 이벤트 루프가 없는 시점에
    생기고 (b) 어느 루프에서든 재사용돼 조용히 깨진다. 재적재하며 실제로 센다.
    """
    import importlib

    import chzzk_http
    calls = _count_ctor(monkeypatch)
    before = calls["ctor"]
    importlib.reload(chzzk_http)
    made = calls["ctor"] - before
    chzzk_http.reset_state_for_tests()
    assert made == 0, f"import만 했는데 클라이언트를 {made}번 만들었다"
    assert chzzk_http.get_client.__module__ == "chzzk_http"


def test_app_lifespan_wraps_serving_with_the_shared_client(http_env):
    """`main.py`의 수명주기가 **서비스 구간 전체를** 공유 클라이언트로 감싼다.

    연결이 빠지면 요청마다 다시 만들게 되어 이 과제가 통째로 무효가 된다.
    실행해서 확인하려면 백그라운드 워커가 전부 뜨므로(수집·싱드컵 등) 구조로 고정한다:
    `lifespan` 안에 `async with chzzk_http.chzzk_client_lifespan(...)`이 있고
    **`yield`가 그 안에 들어 있어야** 한다(= 서비스하는 동안 살아 있다).
    """
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "web" / "backend" / "main.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in tree.body
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"), None)
    assert fn is not None, "main.py에 lifespan이 없다"

    def _is_client_with(node):
        if not isinstance(node, ast.AsyncWith):
            return False
        for item in node.items:
            c = item.context_expr
            if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "chzzk_client_lifespan"):
                return True
        return False

    withs = [n for n in ast.walk(fn) if _is_client_with(n)]
    assert withs, "lifespan이 chzzk_client_lifespan을 감싸지 않는다"
    yields_inside = [n for w in withs for n in ast.walk(w) if isinstance(n, ast.Yield)]
    all_yields = [n for n in ast.walk(fn) if isinstance(n, ast.Yield)]
    assert yields_inside, "yield가 공유 클라이언트 컨텍스트 밖에 있다 — 서비스 중에는 닫혀 있다"
    assert len(yields_inside) == len(all_yields), "컨텍스트 밖의 yield가 남아 있다"
