"""첫 방송일 수집기 — mock 테스트(외부 네트워크 없음).

httpx.MockTransport 로 치지직 응답을 흉내 낸다. 실제 API를 때리는 테스트는
tests/integration/test_chzzk_channel_history_live.py 에 따로 있다.
"""
import asyncio
import time

import chzzk_channel_history as ch
import httpx
import pytest

CID = "4b8f70248caa6f086ceec07aad69a5cc"

OK_PAYLOAD = {
    "code": 200,
    "message": None,
    "content": {
        "channelHistory": {"firstLiveDate": "2025-01-14 22:19:58", "totalLiveHours": 4},
        "activatedAchievementBadgeIds": [],
    },
}
NO_HISTORY_PAYLOAD = {"code": 200, "message": None,
                      "content": {"channelHistory": None, "activatedAchievementBadgeIds": []}}


def install_transport(handler):
    """모듈이 쓰는 공유 클라이언트를 mock transport 로 갈아 끼운다."""
    ch._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ch._client


# ── 1~5. 채널 ID 정규화 ─────────────────────────────────────────────────────
def test_normalize_plain_id():
    assert ch.normalize_channel_input(CID) == CID
    assert ch.normalize_channel_input(f"  {CID.upper()}  ") == CID


def test_normalize_channel_url():
    assert ch.normalize_channel_input(f"https://chzzk.naver.com/{CID}") == CID


def test_normalize_about_url():
    assert ch.normalize_channel_input(f"https://chzzk.naver.com/{CID}/about") == CID


def test_normalize_live_url():
    assert ch.normalize_channel_input(f"https://chzzk.naver.com/live/{CID}") == CID


@pytest.mark.parametrize("bad", [
    "", "   ", None,
    "4b8f70248caa6f086ceec07aad69a5c",        # 31자
    "4b8f70248caa6f086ceec07aad69a5ccc",      # 33자
    "zzzz70248caa6f086ceec07aad69a5cc",       # 16진수 아님
    f"https://evil.example.com/{CID}",        # 치지직 호스트가 아님
    "https://chzzk.naver.com/",               # 경로에 ID 없음
])
def test_normalize_rejects_invalid(bad):
    assert ch.normalize_channel_input(bad) is None


# ── 6~7. 응답 파싱 ──────────────────────────────────────────────────────────
def test_parse_ok():
    assert ch.parse_channel_history(OK_PAYLOAD) == (ch.ST_OK, "2025-01-14 22:19:58", 4)


def test_parse_no_history():
    assert ch.parse_channel_history(NO_HISTORY_PAYLOAD) == (ch.ST_NO_HISTORY, None, None)


@pytest.mark.parametrize("payload", [
    None,
    {"code": 404, "content": {}},
    {"code": 200, "content": []},
    {"code": 200, "content": {"channelHistory": "nope"}},
    {"code": 200, "content": {"channelHistory": {"firstLiveDate": "2025/01/14"}}},
    {"code": 200, "content": {"channelHistory": {"firstLiveDate": "2025-01-14 22:19:58",
                                                 "totalLiveHours": "네시간"}}},
])
def test_parse_schema_errors(payload):
    with pytest.raises(ch.SchemaError):
        ch.parse_channel_history(payload)


def test_iso_conversion():
    assert ch.first_live_date_to_iso("2025-01-14 22:19:58") == "2025-01-14T22:19:58+09:00"
    assert ch.first_live_date_to_iso(None) is None


# ── 정상 수집 ───────────────────────────────────────────────────────────────
def test_collect_ok_stores_and_returns(db):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=OK_PAYLOAD)

    install_transport(handler)
    res = db(ch.get_channel_history(f"https://chzzk.naver.com/{CID}/about", channel_name="피라냠"))

    assert res["status"] == ch.ST_OK
    assert res["channelId"] == CID
    assert res["channelName"] == "피라냠"
    assert res["firstLiveDate"] == "2025-01-14 22:19:58"
    assert res["firstLiveDateIso"] == "2025-01-14T22:19:58+09:00"
    assert res["totalLiveHours"] == 4
    assert res["source"] == "CHZZK_CHANNEL_HISTORY"
    assert res["cached"] is False
    # 채널명을 넘겼으므로 채널 상세 API는 부르지 않는다 → 채널당 1요청
    assert len(calls) == 1 and "fields=channelHistory" in calls[0]


def test_channel_name_fetched_only_when_unknown(db):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/data"):
            return httpx.Response(200, json=OK_PAYLOAD)
        return httpx.Response(200, json={"code": 200, "content": {"channelName": "피라냠"}})

    install_transport(handler)
    res = db(ch.get_channel_history(CID))          # 이름 힌트 없음 → 2요청
    assert res["channelName"] == "피라냠"
    assert len(calls) == 2


def test_no_history(db):
    install_transport(lambda r: httpx.Response(200, json=NO_HISTORY_PAYLOAD))
    res = db(ch.get_channel_history(CID, channel_name="x"))
    assert res["status"] == ch.ST_NO_HISTORY
    assert res["firstLiveDate"] is None


# ── 8. 404 ──────────────────────────────────────────────────────────────────
def test_not_found(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404, json={"code": 404})

    install_transport(handler)
    res = db(ch.get_channel_history(CID, channel_name="x"))
    assert res["status"] == ch.ST_NOT_FOUND
    assert len(calls) == 1          # 404는 재시도하지 않는다


# ── 9. 429 재시도 + Retry-After ─────────────────────────────────────────────
def test_429_retries_and_honors_retry_after(db):
    calls = []

    def handler(request):
        calls.append(time.monotonic())
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "0.2"}, json={"code": 429})
        return httpx.Response(200, json=OK_PAYLOAD)

    install_transport(handler)
    t0 = time.monotonic()
    res = db(ch.get_channel_history(CID, channel_name="x"))

    assert res["status"] == ch.ST_OK
    assert len(calls) == 3                       # MAX_RETRIES=3 안에서 성공
    assert time.monotonic() - t0 >= 0.4          # Retry-After 0.2초 x 2회를 실제로 기다렸다
    assert ch._metrics["rate_limited"] == 2
    assert ch._metrics["retries"] == 2


def test_429_gives_up_after_max_retries(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"code": 429})

    install_transport(handler)
    res = db(ch.get_channel_history(CID, channel_name="x"))
    assert res["status"] == ch.ST_ERROR
    assert len(calls) == ch.MAX_RETRIES


# ── 10. timeout 재시도 ──────────────────────────────────────────────────────
def test_timeout_retries_then_errors(db):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectTimeout("timeout", request=request)

    install_transport(handler)
    res = db(ch.get_channel_history(CID, channel_name="x"))
    assert res["status"] == ch.ST_ERROR
    assert len(calls) == ch.MAX_RETRIES


def test_timeout_then_success(db):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json=OK_PAYLOAD)

    install_transport(handler)
    res = db(ch.get_channel_history(CID, channel_name="x"))
    assert res["status"] == ch.ST_OK
    assert len(calls) == 2


# ── 403 ─────────────────────────────────────────────────────────────────────
def test_403_marks_blocked_without_retry(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(403, json={"code": 403})

    install_transport(handler)
    res = db(ch.get_channel_history(CID, channel_name="x"))
    assert res["status"] == ch.ST_BLOCKED
    assert len(calls) == 1
    assert ch._metrics["forbidden"] == 1


# ── 11. 캐시 적중 시 외부 호출 없음 ─────────────────────────────────────────
def test_cache_hit_does_not_call_external(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=OK_PAYLOAD)

    install_transport(handler)
    db(ch.get_channel_history(CID, channel_name="x"))
    assert len(calls) == 1

    for _ in range(5):
        res = db(ch.get_channel_history(CID, channel_name="x"))
        assert res["cached"] is True
        assert res["firstLiveDate"] == "2025-01-14 22:19:58"
    assert len(calls) == 1                       # 외부는 여전히 1회
    assert ch._metrics["cache_hits"] == 5


def test_refresh_true_bypasses_cache(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=OK_PAYLOAD)

    install_transport(handler)
    db(ch.get_channel_history(CID, channel_name="x"))
    res = db(ch.get_channel_history(CID, channel_name="x", refresh=True))
    assert res["cached"] is False
    assert len(calls) == 2


def test_stale_total_hours_triggers_refetch_only_when_allowed(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=OK_PAYLOAD)

    install_transport(handler)
    db(ch.get_channel_history(CID, channel_name="x"))
    assert len(calls) == 1

    async def age_total_hours():
        import database
        conn = await database.get_db()
        old = int(time.time()) - int(ch.TOTAL_HOURS_TTL_HOURS * 3600) - 60
        await conn.execute(
            "UPDATE chzzk_channel_history SET total_hours_updated_at=? WHERE channel_id=?",
            (old, CID))
        await conn.commit()

    db(age_total_hours())

    # 공개 페이지 경로: 지연을 만들지 않도록 재조회하지 않는다
    res = db(ch.get_channel_history(CID, channel_name="x", refresh_stale_total=False))
    assert res["cached"] is True and len(calls) == 1

    # 수집 API 경로: 하루 1회 누적 방송시간을 갱신한다
    res = db(ch.get_channel_history(CID, channel_name="x"))
    assert res["cached"] is False and len(calls) == 2


# ── 12. 동시 요청 single-flight ─────────────────────────────────────────────
def test_concurrent_requests_make_one_external_call(db):
    calls = []

    async def slow_handler(request):
        calls.append(1)
        await asyncio.sleep(0.15)
        return httpx.Response(200, json=OK_PAYLOAD)

    ch._client = httpx.AsyncClient(transport=httpx.MockTransport(slow_handler))

    async def go():
        return await asyncio.gather(*[
            ch.get_channel_history(CID, channel_name="x") for _ in range(8)
        ])

    results = db(go())
    assert len(calls) == 1                       # single-flight
    assert all(r["firstLiveDate"] == "2025-01-14 22:19:58" for r in results)


# ── 13. 외부 장애 시 기존 캐시 반환 ─────────────────────────────────────────
def test_external_failure_returns_cached_value(db):
    state = {"fail": False}

    def handler(request):
        if state["fail"]:
            return httpx.Response(503, json={"code": 503})
        return httpx.Response(200, json=OK_PAYLOAD)

    install_transport(handler)
    db(ch.get_channel_history(CID, channel_name="x"))

    state["fail"] = True
    res = db(ch.get_channel_history(CID, channel_name="x", refresh=True))

    assert res["firstLiveDate"] == "2025-01-14 22:19:58"   # 캐시가 지워지지 않았다
    assert res["stale"] is True
    assert res["cached"] is True


def test_schema_change_keeps_existing_data(db):
    state = {"broken": False}

    def handler(request):
        if state["broken"]:
            return httpx.Response(200, json={"code": 200, "content": {"channelHistory": 42}})
        return httpx.Response(200, json=OK_PAYLOAD)

    install_transport(handler)
    db(ch.get_channel_history(CID, channel_name="x"))

    state["broken"] = True
    res = db(ch.get_channel_history(CID, channel_name="x", refresh=True))
    assert res["firstLiveDate"] == "2025-01-14 22:19:58"
    assert ch._metrics["schema_errors"] == 1


# ── 배치 ────────────────────────────────────────────────────────────────────
def test_batch_dedupes_and_reports_invalid(db):
    other = "a" * 32
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/data"):
            return httpx.Response(200, json=OK_PAYLOAD)
        return httpx.Response(200, json={"code": 200, "content": {"channelName": "이름"}})

    install_transport(handler)
    out = db(ch.collect_batch([
        CID,
        f"https://chzzk.naver.com/{CID}/about",   # 같은 채널 → 중복 제거
        f"https://chzzk.naver.com/live/{other}",
        "not-a-channel",
    ], refresh=False))

    assert out["unique"] == 2
    assert out["invalid"] == ["not-a-channel"]
    # 이름 힌트가 없으므로 채널당 2요청(history + 채널명) — 힌트를 주면 1요청으로 줄어든다
    assert len(calls) == 4
    statuses = {r["channelId"]: r["status"] for r in out["results"]}
    assert statuses[CID] == ch.ST_OK
    assert statuses[other] == ch.ST_OK
    assert any(r["status"] == ch.ST_INVALID for r in out["results"])
