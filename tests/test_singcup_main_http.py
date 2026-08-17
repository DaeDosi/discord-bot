"""`/api/singcup/main`의 전송 비용 대책 — 캐시 entry·ETag·304 동작.

이 응답은 참가자 전원(운영 기준 약 850KB, gzip 약 240KB)이라 호출 1회가 그대로
전송 비용이다. 여기서 지키려는 성질은 세 가지다.

1. 같은 내용이면 **같은 ETag** — 아니면 조건부 요청이 영영 304가 되지 않는다.
2. 캐시를 채울 때 bytes를 한 번만 만든다 — 요청마다 재직렬화하면 CPU가 그만큼 든다.
3. 동시 요청은 한 번만 계산한다(single-flight).
"""
import asyncio

import pytest
import singcup_clips as sc
from fastapi.responses import JSONResponse
from routers.singcup_router import _etag_matches
from routers.singcup_router import main as main_route
from starlette.requests import Request


@pytest.fixture(autouse=True)
def _unofficial_feature_on(monkeypatch):
    """**이 파일은 라우트 역학을 검사한다**(ETag/304·409·400·503·플래그 off).

    SINGCUP-3에서 비공식 인기점수 랭킹 기능이 내려가 기본값이 '종료 응답'이 됐다.
    여기서 보려는 것은 그 기능이 켜져 있을 때의 전송·페이지네이션 계약이므로
    기능 축을 켠 상태에서 검사한다. 기능이 꺼졌을 때 종료 응답이 나가는지는
    `tests/test_singcup_retirement.py`가 따로 고정한다.
    """
    monkeypatch.setenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", "true")
    monkeypatch.setenv("SINGCUP_LIVE_FEATURE_ENABLED", "true")



def _req(headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": "/api/singcup/main", "raw_path": b"/api/singcup/main",
        "query_string": b"limit=3000", "root_path": "", "scheme": "http",
        "headers": raw, "client": ("203.0.113.9", 4321), "server": ("test", 80),
    })


def _data(**over) -> dict:
    d = {
        "summary": {"streamerCount": 2},
        "topHeartMovers1h": [{"channelId": "c1", "heartDelta1h": 3}],
        "topHeartMovers1hComputedAt": "2026-07-30T10:00:00+09:00",
        "streamers": [{"channelId": "c1", "channelName": "가수"}],
    }
    d.update(over)
    return d


# ── ETag ────────────────────────────────────────────────────────────────────
def test_same_content_same_etag():
    assert sc._build_main_entry(_data())["etag"] == sc._build_main_entry(_data())["etag"]


def test_computed_at_alone_does_not_change_etag():
    """급상승 '계산 시각'은 캐시를 채울 때마다 바뀐다.

    이 필드까지 지문에 넣으면 순위·수치가 그대로여도 20초마다 ETag가 달라져,
    조건부 요청이 전부 850KB 전송으로 되돌아간다.
    """
    a = sc._build_main_entry(_data())
    b = sc._build_main_entry(_data(topHeartMovers1hComputedAt="2026-07-30T23:59:59+09:00"))
    assert a["etag"] == b["etag"]
    assert a["body"] != b["body"]          # 본문에는 그대로 담겨 나간다


def test_real_change_changes_etag():
    a = sc._build_main_entry(_data())
    b = sc._build_main_entry(_data(streamers=[{"channelId": "c1", "channelName": "다른가수"}]))
    assert a["etag"] != b["etag"]


def test_body_is_utf8_json_without_escapes():
    """starlette JSONResponse와 같은 직렬화여야 한다(한글이 \\uXXXX로 부풀지 않게)."""
    entry = sc._build_main_entry(_data())
    assert "가수".encode() in entry["body"]
    assert b"\\u" not in entry["body"]


@pytest.mark.parametrize("header,expected", [
    (None, False), ("", False), ("*", True),
    ('W/"abc"', True), ('"abc"', True),
    ('W/"zzz", W/"abc"', True), ('W/"zzz"', False),
])
def test_etag_matches(header, expected):
    assert _etag_matches(header, 'W/"abc"') is expected


# ── 캐시 · single-flight ────────────────────────────────────────────────────
def test_ttl_serves_from_cache_without_recomputing(db, monkeypatch):
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    calls = []

    async def fake(limit):
        calls.append(limit)
        return _data()

    monkeypatch.setattr(sc, "_load_main_uncached", fake)
    first, src1 = db(sc.load_main_entry(200))
    second, src2 = db(sc.load_main_entry(200))
    assert (src1, src2) == ("miss", "hit")
    assert len(calls) == 1
    assert first is second                       # 같은 bytes를 다시 쓴다


def test_concurrent_requests_compute_once(db, monkeypatch):
    """캐시가 빈 순간 요청이 몰려도 무거운 계산은 한 번만."""
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    calls = []

    async def fake(limit):
        calls.append(limit)
        await asyncio.sleep(0.01)                # 계산 중에 다른 요청이 도착하도록
        return _data()

    async def burst():
        # gather는 실행 중인 루프 안에서 만들어야 한다(루프 밖에서 만들면 다른 루프에 묶인다)
        return await asyncio.gather(*[sc.load_main_entry(3000) for _ in range(10)])

    monkeypatch.setattr(sc, "_load_main_uncached", fake)
    results = db(burst())
    assert len(calls) == 1
    assert [r[1] for r in results].count("miss") == 1
    assert all(r[0] is results[0][0] for r in results)


def test_different_limits_do_not_share_cache(db, monkeypatch):
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    seen = []

    async def fake(limit):
        seen.append(limit)
        return _data(summary={"streamerCount": limit})

    monkeypatch.setattr(sc, "_load_main_uncached", fake)
    a, _ = db(sc.load_main_entry(50))
    b, _ = db(sc.load_main_entry(3000))
    assert seen == [50, 3000]
    assert a["etag"] != b["etag"]


def test_failure_is_not_cached_and_lock_is_released(db, monkeypatch):
    """계산이 실패하면 캐시에 남지 않고, 다음 요청이 정상적으로 다시 시도한다.

    실패한 결과가 캐시에 앉으면 TTL 동안 모든 사용자가 같은 오류를 받는다.
    락이 풀리지 않으면 그 뒤 모든 요청이 영구히 대기한다.
    """
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    calls = []

    async def flaky(limit):
        calls.append(limit)
        if len(calls) == 1:
            raise RuntimeError("일시적 실패")
        return _data()

    monkeypatch.setattr(sc, "_load_main_uncached", flaky)
    with pytest.raises(RuntimeError):
        db(sc.load_main_entry(200))
    assert 200 not in sc._main_cache            # 실패는 캐시되지 않는다
    assert not sc._main_lock.locked()           # 락이 풀려 있다

    entry, source = db(sc.load_main_entry(200))  # 곧바로 재시도된다
    assert source == "miss" and entry["body"]
    assert len(calls) == 2


def test_cache_entries_are_capped(db, monkeypatch):
    """limit은 호출자가 정한다 — 서로 다른 값으로 계속 불러도 메모리가 늘면 안 된다."""
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    monkeypatch.setattr(sc, "MAIN_CACHE_MAX_ENTRIES", 3)
    monkeypatch.setattr(sc, "_load_main_uncached", lambda limit: _async(_data()))
    for limit in range(1, 12):
        db(sc.load_main_entry(limit))
    assert len(sc._main_cache) == 3
    assert max(sc._main_cache) == 11        # 가장 최근 것이 남는다


def test_invalidate_drops_cache(db, monkeypatch):
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    calls = []

    async def fake(limit):
        calls.append(limit)
        return _data()

    monkeypatch.setattr(sc, "_load_main_uncached", fake)
    db(sc.load_main_entry(200))
    sc.invalidate_main_cache()
    db(sc.load_main_entry(200))
    assert len(calls) == 2


# ── 라우터: 200 / 304 ───────────────────────────────────────────────────────
def test_route_sends_body_then_304(db, monkeypatch):
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    monkeypatch.setattr(sc, "_load_main_uncached", lambda limit: _async(_data()))

    full = db(main_route(_req(), limit=3000))
    assert full.status_code == 200
    assert len(full.body) > 0
    assert full.headers["Cache-Control"].startswith("public, max-age=")
    assert full.headers["Vary"] == "Accept-Encoding"
    etag = full.headers["ETag"]
    assert etag.startswith('W/"')

    again = db(main_route(_req({"if-none-match": etag}), limit=3000))
    assert again.status_code == 304
    assert again.body == b""                     # 850KB가 나가지 않는다
    assert again.headers["ETag"] == etag


def test_body_is_byte_identical_to_the_previous_response(db, monkeypatch):
    """캐시가 만든 bytes가 예전(FastAPI가 dict를 직렬화하던) 응답과 **완전히 같아야** 한다.

    구버전 프론트가 그대로 붙어 있으므로, 한 글자라도 달라지면 그게 곧 회귀다.
    """
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    monkeypatch.setattr(sc, "_load_main_uncached", lambda limit: _async(_data()))

    new = db(main_route(_req(), limit=3000)).body
    old = JSONResponse(db(sc.load_main(limit=3000))).body
    assert new == old


def test_route_resends_body_when_data_changed(db, monkeypatch):
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 0.0)   # 매 요청 새로 계산
    state = {"n": 1}
    monkeypatch.setattr(sc, "_load_main_uncached",
                        lambda limit: _async(_data(summary={"streamerCount": state["n"]})))

    first = db(main_route(_req(), limit=3000))
    state["n"] = 2
    second = db(main_route(_req({"if-none-match": first.headers["ETag"]}), limit=3000))
    assert second.status_code == 200                # 바뀌었으면 반드시 본문을 준다
    assert second.headers["ETag"] != first.headers["ETag"]


async def _async(value):
    return value
