"""싱드컵 수집 파이프라인 — 백필 / 신규 탐색 / 지표 갱신 분리 검증.

카드 API는 클립 1건당 1회다. 세 작업을 한 덩어리로 돌리면 과거 적재가 정기 주기에
묶여 몇 시간씩 걸리므로, 역할을 나눠 각자 필요한 만큼만 호출하게 한다.
"""
import time

import httpx
import singcup_clips as sc
from test_singcup_clips import BEFORE, card, clip

import database

CHANNEL_JSON = {"code": 200, "content": {"channelId": "o1", "channelName": "가수",
                                         "channelImageUrl": "https://i/p.png",
                                         "followerCount": 100, "verifiedMark": False}}


def _handler(clips_by_cursor, *, tagged_uids, calls, card_status=200):
    """클립 목록 / 카드 / 채널 API를 흉내 내고 **카드 호출만** 센다."""
    def handler(request):
        url = str(request.url)
        if "/categories/" in url:
            cur = request.url.params.get("clipUID")
            items, nxt = clips_by_cursor.get(cur, ([], None))
            page = {"next": {"clipUID": nxt}} if nxt else {}
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": items, "page": page}})
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json=CHANNEL_JSON)
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        calls.append(uid)
        if card_status != 200:
            return httpx.Response(card_status, json={"code": card_status})
        desc = "#싱드컵" if uid in tagged_uids else "#노래 #커버"
        return httpx.Response(200, json=card(desc, likes=3, views=10))
    return handler


def _install(h):
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(h))


# 3페이지: 마지막 페이지는 전부 이벤트 시작 이전 → 백필 종료 지점
PAGES = {
    None: ([clip("t1"), clip("t2", owner="o2"), clip("plain", owner="o3")], "c1"),
    "c1": ([clip("t3", owner="o4")], "c2"),
    "c2": ([clip("old", created=BEFORE)], None),
}
TAGGED = {"t1", "t2", "t3"}


async def _age_metrics(uids=None):
    c = await database.get_db()
    if uids is None:
        await c.execute("UPDATE singcup_clips SET last_metrics_at=0")
    else:
        qs = ",".join("?" for _ in uids)
        await c.execute(
            f"UPDATE singcup_clips SET last_metrics_at=0 WHERE clip_uid IN ({qs})",
            tuple(uids))
    await c.commit()


# ── ① 백필 ─────────────────────────────────────────────────────────────────
def test_backfill_runs_to_completion_in_one_go(db):
    """정기 주기를 기다리지 않고 시작일에 닿을 때까지 연속 처리한다."""
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    res = db(sc.run_backfill())

    assert res["status"] == "completed"
    assert res["pages"] == 3                      # 시작일 이전 페이지에서 종료
    assert res["tagged"] == 3
    assert sorted(calls) == ["plain", "t1", "t2", "t3"]
    assert db(sc.load_main())["summary"]["streamerCount"] == 3


def test_backfill_state_is_persisted(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())
    st = db(sc.backfill_status())

    assert st["status"] == "completed"
    assert st["scannedCount"] == 5                # 전체 훑은 클립 수
    assert st["taggedCount"] == 3
    assert st["failedCount"] == 0
    assert st["completedAt"] and st["oldestScannedCreatedAt"]
    assert st["nextCursor"] is None               # 완료되면 커서를 비운다


def test_completed_backfill_is_not_rerun(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())
    calls.clear()

    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    res = db(sc.run_backfill())
    assert res["status"] == "completed" and calls == []


def test_backfill_resumes_from_saved_cursor(db):
    """중단된 백필은 저장된 커서부터 이어서 처리한다(재배포 시나리오)."""
    calls = []

    def flaky(request):
        url = str(request.url)
        if "/categories/" in url:
            cur = request.url.params.get("clipUID")
            if cur == "c1":                        # 2페이지에서 실패
                return httpx.Response(503, json={"code": 503})
            items, nxt = PAGES.get(cur, ([], None))
            page = {"next": {"clipUID": nxt}} if nxt else {}
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": items, "page": page}})
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json=CHANNEL_JSON)
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        calls.append(uid)
        return httpx.Response(200, json=card(
            "#싱드컵" if uid in TAGGED else "#노래", likes=3, views=10))

    _install(flaky)
    first = db(sc.run_backfill())
    assert first["status"] == "paused"
    st = db(sc.backfill_status())
    assert st["nextCursor"] == "c1"                # 여기서부터 다시
    assert st["lastError"]

    # 정상 응답으로 복구하면 이어서 완료된다
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    second = db(sc.run_backfill())
    assert second["status"] == "completed"
    assert db(sc.backfill_status())["nextCursor"] is None


def test_backfill_reset_starts_over(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())
    db(sc.reset_backfill())
    st = db(sc.backfill_status())
    assert st["status"] == "idle" and st["scannedCount"] == 0


def test_backfill_lock_blocks_second_worker(db):
    async def go():
        import asyncio
        _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
        return await asyncio.gather(sc.run_backfill(), sc.run_backfill())

    a, b = db(go())
    # 하나만 실제로 돌고 다른 하나는 락에 막힌다
    assert sum(1 for r in (a, b) if r["status"] == "completed") == 1


# ── ② 신규 탐색 ────────────────────────────────────────────────────────────
def test_discover_stops_at_first_fully_known_page(db):
    """이미 아는 클립만 있는 페이지를 만나면 즉시 멈춘다 — 수천 건을 다시 안 내려간다."""
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())                          # 전부 알고 있는 상태로 만든다
    calls.clear()

    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    res = db(sc.discover_new_clips())
    assert res["pages"] == 1                       # 1페이지에서 종료
    assert res["candidates"] == 0 and calls == []


def test_discover_picks_up_only_new_clips(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())
    calls.clear()

    pages2 = dict(PAGES)
    pages2[None] = ([clip("fresh", owner="o9")] + PAGES[None][0], "c1")
    _install(_handler(pages2, tagged_uids=TAGGED | {"fresh"}, calls=calls))
    res = db(sc.discover_new_clips())

    assert calls == ["fresh"]                      # 새 클립만 카드 조회
    assert res["tagged"] == 1
    assert db(sc.load_main())["summary"]["streamerCount"] == 4


def test_discover_ignores_clips_outside_event_window(db):
    calls = []
    pages = {None: ([clip("old2", created=BEFORE)], None)}
    _install(_handler(pages, tagged_uids={"old2"}, calls=calls))
    res = db(sc.discover_new_clips())
    assert res["candidates"] == 0 and calls == []


# ── ③ 지표 갱신 ────────────────────────────────────────────────────────────
def test_refresh_updates_metrics_without_listing(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())
    calls.clear()
    db(_age_metrics())

    # 목록 API는 부르지 않고 카드만 부른다
    def cards_only(request):
        url = str(request.url)
        if "/categories/" in url:
            raise AssertionError("지표 갱신은 목록을 훑지 않아야 한다")
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json=CHANNEL_JSON)
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        calls.append(uid)
        return httpx.Response(200, json=card("#싱드컵", likes=99, views=10))

    _install(cards_only)
    res = db(sc.refresh_metrics())
    assert res["refreshed"] == 3 and sorted(calls) == ["t1", "t2", "t3"]
    assert db(sc.load_main())["streamers"][0]["heartCount"] == 99


def test_refresh_prefers_representative_clips(db):
    """대표 클립은 짧은 주기로, 나머지는 긴 주기로 갱신한다."""
    calls = []
    # 같은 스트리머의 클립 2개 — 하트가 높은 rep이 대표가 된다
    pages = {None: ([clip("rep"), clip("sub")], None)}

    def handler(request):
        url = str(request.url)
        if "/categories/" in url:
            cur = request.url.params.get("clipUID")
            items, nxt = pages.get(cur, ([], None))
            page = {"next": {"clipUID": nxt}} if nxt else {}
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": items, "page": page}})
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json=CHANNEL_JSON)
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        calls.append(uid)
        likes = 50 if uid == "rep" else 1
        return httpx.Response(200, json=card("#싱드컵", likes=likes, views=10))

    _install(handler)
    db(sc.run_backfill())
    calls.clear()

    async def stale_between_ttls():
        # 대표 TTL(5분)은 넘고 일반 TTL(45분)은 안 넘은 시점으로 맞춘다
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_metrics_at=?",
                        (int(time.time()) - 10 * 60,))
        await c.commit()
    db(stale_between_ttls())

    _install(handler)
    res = db(sc.refresh_metrics())
    assert res["refreshed"] == 1 and calls == ["rep"]   # 대표만 갱신


def test_card_failure_keeps_previous_metrics(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())

    async def read(uid):
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT heart_count, view_count FROM singcup_clips WHERE clip_uid=?",
            (uid,))).fetchone()
        return (r["heart_count"], r["view_count"])
    before = db(read("t1"))
    assert before == (3, 10)

    db(_age_metrics())
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[], card_status=503))
    db(sc.refresh_metrics())
    assert db(read("t1")) == before                # 0으로 덮지 않는다


def _read_metrics(uid):
    async def go():
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT heart_count, view_count FROM singcup_clips WHERE clip_uid=?",
            (uid,))).fetchone()
        return (r["heart_count"], r["view_count"])
    return go()


def _partial_cards(**card_kw):
    def handler(request):
        url = str(request.url)
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json=CHANNEL_JSON)
        if "/categories/" in url:
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": [], "page": {}}})
        return httpx.Response(200, json=card("#싱드컵", **card_kw))
    return handler


def test_missing_view_still_updates_hearts(db):
    """조회수만 못 읽어도 하트는 갱신된다 — 예전엔 둘 다 멈춰 값이 굳었다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    assert db(_read_metrics("t1")) == (3, 10)

    db(_age_metrics())
    _install(_partial_cards(likes=99, views=0, vod=False))
    res = db(sc.refresh_metrics())

    assert db(_read_metrics("t1")) == (99, 10)     # 하트는 새 값, 조회수는 보존
    assert res["partial"] == 3 and res["refreshed"] == 0 and res["failed"] == 0


def test_missing_heart_still_updates_views(db):
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())

    db(_age_metrics())
    _install(_partial_cards(likes=0, views=77, reactions=False))
    res = db(sc.refresh_metrics())

    assert db(_read_metrics("t1")) == (3, 77)
    assert res["partial"] == 3


def test_both_metrics_missing_counts_as_failed(db):
    """부분 실패도 전체 실패도 refreshed로 세지 않는다(failed=0 착시 방지)."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())

    db(_age_metrics())
    _install(_partial_cards(likes=0, views=0, reactions=False, vod=False))
    res = db(sc.refresh_metrics())

    assert db(_read_metrics("t1")) == (3, 10)      # 둘 다 보존
    assert res["failed"] == 3 and res["refreshed"] == 0 and res["partial"] == 0


# ── 실패 재시도 큐 ─────────────────────────────────────────────────────────
def test_failed_cards_are_queued_and_retried(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls, card_status=503))
    res = db(sc.run_backfill())
    assert res["failed"] == 4                      # 후보 4건 모두 카드 실패

    async def queued():
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT clip_uid, attempts FROM singcup_clip_retry ORDER BY clip_uid")).fetchall()
        return [(x["clip_uid"], x["attempts"]) for x in r]
    q = db(queued())
    assert len(q) == 4 and all(a == 1 for _, a in q)

    # 재시도 시각을 당기고 정상 응답으로 바꾸면 큐가 비워진다
    async def make_due():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clip_retry SET next_try_at=0")
        await c.commit()
    db(make_due())
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    r = db(sc.retry_failed_clips())
    assert r["retried"] == 4 and r["tagged"] == 3
    assert db(queued()) == []


def test_load_main_shape(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())
    d = db(sc.load_main())

    assert set(d) == {"event", "summary", "collector", "live",
                      "topHeartMovers1h", "streamers"}
    # 라이브 신선도 — 화면이 '언제 확인한 라이브인지' 표시할 수 있어야 한다
    assert set(d["live"]) == {"collectedAt", "nextExpectedAt", "intervalSeconds", "isStale"}
    assert d["summary"]["streamerCount"] == 3
    top = d["streamers"][0]
    for key in ("rank", "channelId", "channelName", "clipUid", "clipThumbnailUrl",
                "heartCount", "viewCount", "viewScore", "heartScore", "score",
                "taggedClipCount", "heartDelta", "rankDelta", "isNew", "live"):
        assert key in top
    assert 0 <= top["score"] <= 100


# ── 닉네임이 비지 않아야 한다(검색 가능 조건) ───────────────────────────────
def test_nickname_falls_back_to_list_owner_channel(db):
    """채널 API가 실패해도 목록 응답의 ownerChannel로 닉네임을 채운다.

    이름이 비면 화면에 '-'로 뜨고 검색에도 절대 걸리지 않는다.
    """
    def no_channel_api(request):
        url = str(request.url)
        if "/categories/" in url:
            cur = request.url.params.get("clipUID")
            items, nxt = PAGES.get(cur, ([], None))
            page = {"next": {"clipUID": nxt}} if nxt else {}
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": items, "page": page}})
        if "/service/v1/channels/" in url:
            return httpx.Response(503, json={"code": 503})   # 채널 API 장애
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        return httpx.Response(200, json=card(
            "#싱드컵" if uid in TAGGED else "#노래", likes=3, views=10))

    _install(no_channel_api)
    db(sc.run_backfill())

    d = db(sc.load_main())
    assert d["summary"]["streamerCount"] == 3
    # clip() 헬퍼의 ownerChannel.channelName == "가수"
    assert all(s["channelName"] == "가수" for s in d["streamers"])
    assert all(s["channelName"] for s in d["streamers"]), "닉네임이 비면 검색이 불가능하다"


def test_list_owner_channel_wins_over_channel_api(db):
    """목록의 ownerChannel을 우선한다(둘 다 있을 때 이름이 비는 일이 없도록)."""
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.run_backfill())
    d = db(sc.load_main())
    assert all(s["channelName"] == "가수" for s in d["streamers"])


# ── 목록 상한이 검색 범위를 깎지 않는다 ─────────────────────────────────────
async def _seed_streamers(n):
    """대표 클립을 가진 참가자 n명을 직접 넣는다(카드/목록 API 없이)."""
    c = await database.get_db()
    now = int(time.time())
    created = int(sc.START_AT.timestamp()) + 3600
    for i in range(n):
        cid, uid = f"bulk{i}", f"bu{i}"
        await c.execute(
            "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
            " rec_id, clip_title, thumbnail_image_url, description, created_at,"
            " heart_count, view_count, duration, adult, blind_type, metrics_ok,"
            " owner_channel_name, active, missing_scan_count, first_collected_at,"
            " last_collected_at, row_updated_at)"
            " VALUES (?,?,?,?,'','제목','','#싱드컵',?,?,?,60,0,'',1,?,1,0,?,?,?)",
            (uid, sc.EVENT_ID, cid, f"v{i}", created, i, i, f"참가자{i}", now, now, now))
        await c.execute(
            "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
            " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
            " representative_clip_uid, row_updated_at) VALUES (?,?,?,'',0,0,1,?,?)",
            (cid, sc.EVENT_ID, f"참가자{i}", uid, now))
    await c.commit()


def test_main_returns_every_participant_at_frontend_limit(db):
    """화면이 쓰는 상한에서는 참가자가 한 명도 잘리지 않아야 한다.

    검색은 이 응답 안에서만 이뤄지므로, 잘려나간 뒤쪽 스트리머는 닉네임을 정확히
    쳐도 "없습니다"가 뜬다(참가자 수 > 상한이었던 실제 버그).
    """
    db(_seed_streamers(600))
    d = db(sc.load_main(limit=3000))
    assert d["summary"]["streamerCount"] == 600
    assert len(d["streamers"]) == 600            # 예전 상한(500)이면 여기서 깎였다
    assert d["streamers"][-1]["channelName"]     # 꼬리쪽도 닉네임이 있어야 검색된다


def test_main_limit_argument_is_still_honored(db):
    """상한만 올렸을 뿐, 요청한 개수는 그대로 지킨다."""
    db(_seed_streamers(600))
    assert len(db(sc.load_main(limit=50))["streamers"]) == 50



# ── 변화량 기준: '기준 시각 ±허용오차 안의 실제 회차' ───────────────────────
async def _snap(owner, clip_uid, hearts, rank, at, score=0.0):
    """그 시각에 수집 회차가 있었던 것처럼 스냅샷 한 줄을 넣는다."""
    c = await database.get_db()
    await c.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at)"
        " VALUES (?,?,?,?,0,0,?,?,?)",
        (sc.EVENT_ID, clip_uid, owner, hearts, score, rank, int(at)))
    await c.commit()


def _top(db):
    """백필 후 1위 스트리머의 (channelId, clipUid)."""
    s = db(sc.load_main())["streamers"][0]
    return s["channelId"], s["clipUid"]


def test_delta_compares_against_one_hour_ago(db):
    """회차 간격(4분)으로 비교하면 변화량이 대부분 0이라 의미가 없다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    cid, uid = _top(db)
    db(_snap(cid, uid, 10, 5, now - 3900))     # 65분 전 — 기준 시각(60분) 안
    db(_snap(cid, uid, 90, 2, now - 300))      # 5분 전(직전 회차) — 기준이 되면 안 된다
    s = next(x for x in db(sc.load_main())["streamers"] if x["channelId"] == cid)
    assert s["heartDelta"] == 3 - 10, "1시간 전(10) 기준이어야 한다"
    assert s["rankDelta"] == 5 - s["rank"]


def test_reference_run_must_be_within_tolerance(db):
    """기준 시각에서 너무 떨어진 회차와 비교해 놓고 '1시간'이라고 하면 거짓말이다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    cid, uid = _top(db)
    db(_snap(cid, uid, 10, 5, now - 3600 - sc.DELTA_TOLERANCE_SECONDS - 120))
    s = next(x for x in db(sc.load_main())["streamers"] if x["channelId"] == cid)
    assert s["heartDelta"] is None and s["isNew"] is True


def test_reference_run_picks_the_nearest(db):
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    cid, uid = _top(db)
    db(_snap(cid, uid, 10, 9, now - 4400))     # 기준에서 800초
    db(_snap(cid, uid, 20, 7, now - 3700))     # 기준에서 100초 → 이쪽
    s = next(x for x in db(sc.load_main())["streamers"] if x["channelId"] == cid)
    assert s["heartDelta"] == 3 - 20


def test_heart_delta_is_none_when_representative_clip_changed(db):
    """대표 클립이 바뀌면 서로 다른 영상의 하트를 빼게 된다 → 비교하지 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    cid, uid = _top(db)
    db(_snap(cid, "old-rep-clip", 1, 3, now - 3700))
    s = next(x for x in db(sc.load_main())["streamers"] if x["channelId"] == cid)
    assert s["heartDelta"] is None
    assert s["rankDelta"] == 3 - s["rank"], "순위 변동은 클립과 무관하므로 남아 있어야 한다"


def test_delta_window_defaults(db):
    assert sc.DELTA_WINDOW_SECONDS == 3600
    # 스냅샷이 시간 버킷당 한 세트가 되면서 ±15분으로는 기준 회차를 못 찾는
    # 구간이 생긴다(21:30에 20:30±15분 → 20:00·21:00 둘 다 밖). 35분이면
    # 어느 시각에서 보더라도 직전 버킷 하나는 반드시 들어온다.
    assert sc.DELTA_TOLERANCE_SECONDS == 2100
    assert sc.DELTA_TOLERANCE_SECONDS > 1800, "시간 버킷 간격의 절반은 넘어야 한다"


# ── KPI 증감(태그 클립 / 참가 스트리머) ─────────────────────────────────────
async def _age_clip(uid, first_at):
    c = await database.get_db()
    await c.execute("UPDATE singcup_clips SET first_collected_at=? WHERE clip_uid=?",
                    (int(first_at), uid))
    await c.commit()


def test_kpi_delta_counts_only_the_last_hour(db):
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    cid, uid = _top(db)
    db(_snap(cid, uid, 1, 1, now - 3700))          # 1시간 전 회차가 실제로 있었다
    db(_age_clip("t1", now - 7200))
    db(_age_clip("t2", now - 7200))
    db(_age_clip("t3", now - 60))                  # 최근 1시간에 새로 들어온 것
    s = db(sc.load_main())["summary"]
    assert s["taggedClipCount"] == 3
    assert s["taggedClipDelta"] == 1
    assert s["streamerDelta"] == 1
    assert s["deltaWindowMinutes"] == 60
    assert s["deltaBaseAt"], "실제 비교한 회차 시각을 알려줘야 한다"


def test_kpi_delta_is_none_without_reference_run(db):
    """1시간 전 근처에 수집 회차가 없으면 0이 아니라 '비교 데이터 없음'이다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    for uid in ("t1", "t2", "t3", "plain"):
        db(_age_clip(uid, now - 7200))             # 클립은 오래됐지만
    s = db(sc.load_main())["summary"]              # 기준 회차가 없다
    assert s["taggedClipDelta"] is None and s["streamerDelta"] is None
    assert s["deltaBaseAt"] is None


def test_kpi_delta_zero_when_nothing_new(db):
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    cid, uid = _top(db)
    db(_snap(cid, uid, 1, 1, now - 3700))
    for u in ("t1", "t2", "t3", "plain"):
        db(_age_clip(u, now - 7200))
    s = db(sc.load_main())["summary"]
    assert s["taggedClipDelta"] == 0 and s["streamerDelta"] == 0


def test_streamer_count_is_distinct_channels(db):
    """참가 스트리머는 클립 수가 아니라 고유 채널 수여야 한다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    d = db(sc.load_main())
    assert d["summary"]["streamerCount"] == 3          # t1,t2,t3 -> o1,o2,o4
    assert d["summary"]["taggedClipCount"] == 3
    assert len({s["channelId"] for s in d["streamers"]}) == 3


# ── 최근 1시간 하트 급상승 Top 5 ────────────────────────────────────────────
def test_top_movers_ranks_by_heart_increase(db):
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    d = db(sc.load_main())
    by = {s["channelId"]: s for s in d["streamers"]}
    ids = list(by)
    # 현재 하트는 전원 3. 1시간 전 값을 다르게 줘서 증가량 차이를 만든다
    db(_snap(ids[0], by[ids[0]]["clipUid"], 1, 1, now - 3700))   # +2
    db(_snap(ids[1], by[ids[1]]["clipUid"], 2, 2, now - 3700))   # +1
    db(_snap(ids[2], by[ids[2]]["clipUid"], 3, 3, now - 3700))   # 0 -> 제외
    m = db(sc.load_main())["topHeartMovers1h"]
    assert [x["channelId"] for x in m] == [ids[0], ids[1]]
    assert [x["heartDelta1h"] for x in m] == [2, 1]
    assert [x["rank"] for x in m] == [1, 2]


def test_top_movers_excludes_new_and_non_positive(db):
    """비교 기록이 없거나(NEW) 증가량이 0 이하면 순위에 넣지 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    d = db(sc.load_main())
    ids = [s["channelId"] for s in d["streamers"]]
    uid0 = next(s["clipUid"] for s in d["streamers"] if s["channelId"] == ids[0])
    db(_snap(ids[0], uid0, 9, 1, now - 3700))      # 하트가 줄었다 -> 제외
    assert db(sc.load_main())["topHeartMovers1h"] == []


def test_top_movers_ignores_other_clips_of_same_streamer(db):
    """대표 클립이 바뀌었으면 다른 영상끼리 빼지 않는다 -> 급상승에서 제외."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    ids = [s["channelId"] for s in db(sc.load_main())["streamers"]]
    db(_snap(ids[0], "another-clip", 0, 1, now - 3700))
    assert db(sc.load_main())["topHeartMovers1h"] == []


def test_top_movers_is_capped_at_five(db):
    db(_seed_streamers(12))
    now = int(time.time())
    for i, s in enumerate(db(sc.load_main())["streamers"]):
        db(_snap(s["channelId"], s["clipUid"], 0, i + 1, now - 3700))
    m = db(sc.load_main())["topHeartMovers1h"]
    assert len(m) == 5
    assert [x["rank"] for x in m] == [1, 2, 3, 4, 5]
    assert all(x["heartDelta1h"] > 0 for x in m)


def test_top_movers_tiebreak_is_stable(db):
    """증가량이 같으면 현재 하트 -> 점수 -> channelId 순으로 안정 정렬한다."""
    db(_seed_streamers(4))
    now = int(time.time())
    for s in db(sc.load_main())["streamers"]:
        db(_snap(s["channelId"], s["clipUid"], s["heartCount"] - 5, 1, now - 3700))
    a = [x["channelId"] for x in db(sc.load_main())["topHeartMovers1h"]]
    b = [x["channelId"] for x in db(sc.load_main())["topHeartMovers1h"]]
    assert a == b and len(a) == 4
    hearts = [x["heartCount"] for x in db(sc.load_main())["topHeartMovers1h"]]
    assert hearts == sorted(hearts, reverse=True), "동률이면 현재 하트 내림차순"


def test_top_movers_empty_without_reference(db):
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    assert db(sc.load_main())["topHeartMovers1h"] == []


# ── 순위 변동 ───────────────────────────────────────────────────────────────
def test_rank_delta_direction(db):
    """12위 -> 5위면 +7(상승)."""
    db(_seed_streamers(12))
    now = int(time.time())
    d = db(sc.load_main())
    target = next(s for s in d["streamers"] if s["rank"] == 5)
    db(_snap(target["channelId"], target["clipUid"], 0, 12, now - 3700))
    s = next(x for x in db(sc.load_main())["streamers"]
             if x["channelId"] == target["channelId"])
    assert s["rankDelta"] == 7 and s["rank"] == 5


def test_rank_delta_negative_when_dropped(db):
    """3위 -> 8위면 -5(하락)."""
    db(_seed_streamers(12))
    now = int(time.time())
    target = next(s for s in db(sc.load_main())["streamers"] if s["rank"] == 8)
    db(_snap(target["channelId"], target["clipUid"], 0, 3, now - 3700))
    s = next(x for x in db(sc.load_main())["streamers"]
             if x["channelId"] == target["channelId"])
    assert s["rankDelta"] == -5


def test_rank_delta_is_none_for_new(db):
    db(_seed_streamers(3))
    for s in db(sc.load_main())["streamers"]:
        assert s["rankDelta"] is None and s["isNew"] is True


# ── 갱신 큐 공정성 (굶주림 회귀) ──────────────────────────────────────────────
# 사고 원인: ORDER BY is_rep DESC, heart_count DESC 였다. 정렬 키가 곧 갱신 대상
# 값이라 하트 0인 클립은 큐 맨 뒤 → 갱신 못 받음 → 계속 0 → 영원히 맨 뒤.
async def _seed(n, *, hearts, aged=0):
    """대표 클립 n개를 직접 넣는다(카드 API를 타지 않는 큐 단위 검증용)."""
    c = await database.get_db()
    now = int(time.time())
    for i in range(n):
        uid = f"q{i:03d}"
        await c.execute(
            "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
            " rec_id, clip_title, thumbnail_image_url, description, created_at,"
            " heart_count, view_count, duration, adult, blind_type, metrics_ok, active,"
            " missing_scan_count, first_collected_at, last_collected_at, row_updated_at,"
            " last_metrics_at)"
            " VALUES (?,?,?,?,'','t','','#tag',?,?,0,60,0,'',1,1,0,?,?,?,?)",
            (uid, sc.EVENT_ID, f"o{i:03d}", f"v{i}", now, hearts(i), now, now, now, aged))
        await c.execute(
            "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
            " channel_image_url, follower_count, verified_mark, representative_clip_uid,"
            " tagged_clip_count, last_channel_updated_at, row_updated_at)"
            " VALUES (?,?,'name','',0,0,?,1,?,?)",
            (f"o{i:03d}", sc.EVENT_ID, uid, now, now))
    await c.commit()


def test_zero_heart_clip_is_not_starved(db):
    """하트 0인 클립이 상위 하트 클립에 영원히 밀리지 않는다(핵심 회귀)."""
    # q000만 하트 0, 나머지 199개는 하트가 많다. 예산은 20건뿐.
    db(_seed(200, hearts=lambda i: 0 if i == 0 else 1000 - i))
    due = db(sc._metrics_due(int(time.time()), 20))
    uids = [r["clip_uid"] for r in due]
    assert "q000" in uids, "하트 0 클립이 갱신 대기열에 들어와야 한다"
    assert len(uids) == 20


def test_sweep_covers_every_clip_over_repeated_cycles(db):
    """예산보다 대상이 많아도 사이클을 반복하면 전원이 한 번씩 갱신된다."""
    db(_seed(100, hearts=lambda i: 1000 - i))
    now = int(time.time())
    seen, cursor = set(), now

    async def mark(uids, at):
        c = await database.get_db()
        qs = ",".join("?" for _ in uids)
        await c.execute(
            f"UPDATE singcup_clips SET last_metrics_at=? WHERE clip_uid IN ({qs})",
            (at, *uids))
        await c.commit()

    for _ in range(10):                       # 10 사이클 × 10건 = 100건
        batch = [r["clip_uid"] for r in db(sc._metrics_due(cursor, 10))]
        assert batch, "아직 대상이 남았는데 큐가 비었다"
        seen.update(batch)
        db(mark(batch, cursor))
        cursor += 1                           # 갱신된 것은 큐 뒤로 밀린다
    assert len(seen) == 100, f"{100 - len(seen)}건이 한 번도 선택되지 않았다"


def test_queue_is_deterministic_across_restarts(db):
    """재시작해도 항상 같은 앞머리만 집지 않는다 — 갱신되면 뒤로 밀린다."""
    db(_seed(50, hearts=lambda i: 1000 - i))
    now = int(time.time())
    first = [r["clip_uid"] for r in db(sc._metrics_due(now, 10))]
    again = [r["clip_uid"] for r in db(sc._metrics_due(now, 10))]
    assert first == again                     # 결정적 순서(clip_uid 타이브레이커)

    async def refresh(uids):
        c = await database.get_db()
        qs = ",".join("?" for _ in uids)
        await c.execute(
            f"UPDATE singcup_clips SET last_metrics_at=? WHERE clip_uid IN ({qs})",
            (now, *uids))
        await c.commit()
    db(refresh(first))
    nxt = [r["clip_uid"] for r in db(sc._metrics_due(now, 10))]
    assert not set(nxt) & set(first), "갱신한 건이 다음 사이클에 또 잡혔다"


def test_sweep_stats_flags_starvation(db):
    """가장 오래된 클립이 한 바퀴 SLA를 크게 넘으면 starving으로 보고한다."""
    db(_seed(10, hearts=lambda i: 1, aged=1))      # last_metrics_at=1 (사실상 1970)
    st = db(sc.metrics_sweep_stats())
    assert st["clips"] == 10 and st["never_refreshed"] == 0
    assert st["full_sweep_hours"] is not None
    assert st["starving"] is True


# ── 문제 클립(k1DqN2X3Mh) 형태 회귀 ─────────────────────────────────────────
def test_new_low_heart_clip_gets_refreshed_and_reranked(db):
    """신규·하트 0으로 들어온 클립이 갱신되어 하트 52가 반영되고 순위에 반영된다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())

    async def make_it_look_like_the_bug():
        c = await database.get_db()
        # 최초 수집값에서 멈춘 상태 재현: 하트 0, 조회수 1
        await c.execute("UPDATE singcup_clips SET heart_count=0, view_count=1, "
                        "last_metrics_at=0 WHERE clip_uid='t1'")
        await c.commit()
    db(make_it_look_like_the_bug())

    # 카드는 하트 52를 주지만 조회수 필드가 없다(= 이번 사고의 응답 형태)
    _install(_partial_cards(likes=52, views=0, vod=False))
    res = db(sc.refresh_metrics())
    assert res["partial"] >= 1

    assert db(_read_metrics("t1")) == (52, 1)      # 하트 반영, 조회수 보존
    main = db(sc.load_main())
    me = next(s for s in main["streamers"] if s["clipUid"] == "t1")
    assert me["heartCount"] == 52
    assert me["score"] > 0                         # 예상 인기점수 재계산됨


def test_recovered_clip_is_excluded_from_1h_surge(db):
    """긴 공백 뒤 복구된 값이 '1시간 +52 급상승'으로 둔갑하지 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())

    async def stale_with_baseline():
        c = await database.get_db()
        # 1시간 전 비교 기준 회차를 만들어 둔다(하트 0 시점)
        await c.execute(
            "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
            " heart_count, view_count, follower_count, score, rank, collected_at)"
            " VALUES (?,?,?,0,1,0,0,1,?)", (sc.EVENT_ID, "t1", "o1", now - 3600))
        # 20시간 동안 갱신이 멈춰 있었다
        await c.execute("UPDATE singcup_clips SET heart_count=0, view_count=1, "
                        "last_metrics_at=? WHERE clip_uid='t1'", (now - 20 * 3600,))
        await c.commit()
    db(stale_with_baseline())

    _install(_partial_cards(likes=52, views=0, vod=False))
    db(sc.refresh_metrics())

    main = db(sc.load_main())
    me = next(s for s in main["streamers"] if s["clipUid"] == "t1")
    assert me["heartCount"] == 52                  # 현재 하트·랭킹에는 즉시 반영
    assert me["deltaState"] == "recovering"
    assert me["heartDelta"] is None                # 단기 증감은 계산하지 않는다
    assert all(m["clipUid"] != "t1" for m in main["topHeartMovers1h"])


def test_normal_update_still_reports_1h_delta(db):
    """복구 가드가 정상 갱신의 1시간 증감까지 막지는 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())

    async def fresh_baseline():
        c = await database.get_db()
        await c.execute(
            "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
            " heart_count, view_count, follower_count, score, rank, collected_at)"
            " VALUES (?,?,?,3,10,0,0,1,?)", (sc.EVENT_ID, "t1", "o1", now - 3600))
        # 공백 없이 최근까지 갱신되던 클립
        await c.execute("UPDATE singcup_clips SET last_metrics_at=? "
                        "WHERE clip_uid='t1'", (now - 600,))
        await c.commit()
    db(fresh_baseline())

    _install(_partial_cards(likes=20, views=10))
    db(sc.refresh_metrics(limit=200))

    main = db(sc.load_main())
    me = next(s for s in main["streamers"] if s["clipUid"] == "t1")
    assert me["deltaState"] == "ok" and me["heartDelta"] == 17


def test_list_zero_does_not_overwrite_card_metrics(db):
    """목록 응답의 축약/기본값 0이 카드로 받은 정상 수치를 덮지 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    assert db(_read_metrics("t1")) == (3, 10)

    # 목록을 다시 훑되 카드는 실패시킨다 → 기존 수치가 0으로 덮이면 안 된다
    db(_age_metrics())
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[], card_status=500))
    db(sc.discover_new_clips())
    assert db(_read_metrics("t1")) == (3, 10)


def test_partial_clip_returns_to_the_queue(db):
    """부분 성공 클립도 TTL이 지나면 다시 대상에 들어온다(영구 제외 아님)."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    db(_age_metrics())
    _install(_partial_cards(likes=99, views=0, vod=False))
    assert db(sc.refresh_metrics())["partial"] == 3

    db(_age_metrics())                             # TTL 경과
    assert "t1" in [r["clip_uid"] for r in db(sc._metrics_due(int(time.time()), 80))]


# ── 단건 강제 갱신 ─────────────────────────────────────────────────────────
def test_single_clip_refresh_uses_normal_path(db):
    """단건 갱신이 카드 조회 → 수치 반영 → 대표/점수/순위 재계산까지 태운다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())

    async def freeze():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET heart_count=0, view_count=1, "
                        "last_metrics_at=0 WHERE clip_uid='t1'")
        await c.commit()
    db(freeze())

    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    res = db(sc.refresh_one_clip("t1", actor="test"))

    assert calls == ["t1"], "그 클립의 카드만 조회해야 한다"
    assert res["apply_result"] == "ok"
    assert res["db_before"]["heart_count"] == 0
    assert res["db_after"]["heart_count"] == 3 and res["db_after"]["view_count"] == 10
    assert res["fetched"]["heart"] == 3 and res["fetched"]["heart_ok"] is True
    # 순위·점수가 다시 계산되어 화면 응답에 반영된다
    me = next(s for s in db(sc.load_main())["streamers"] if s["clipUid"] == "t1")
    assert me["heartCount"] == 3 and me["score"] > 0


def test_single_clip_refresh_reports_partial(db):
    """조회수 필드가 없으면 partial로 보고하고 조회수는 보존한다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    _install(_partial_cards(likes=52, views=0, vod=False))
    res = db(sc.refresh_one_clip("t1"))
    assert res["apply_result"] == "partial"
    assert res["fetched"]["view"] is None and res["fetched"]["view_ok"] is False
    assert res["db_after"]["heart_count"] == 52 and res["db_after"]["view_count"] == 10


def test_single_clip_refresh_validates_uid(db):
    assert db(sc.refresh_one_clip("bad uid!"))["status"] == sc.ST_FAILED
    assert db(sc.refresh_one_clip("a" * 100))["status"] == sc.ST_FAILED
    assert "없" in db(sc.refresh_one_clip("nosuchclip"))["note"]


def test_single_clip_refresh_respects_the_lock(db):
    """정기 갱신과 같은 락을 쓴다 — 동시에 같은 행을 건드리지 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())

    async def go():
        tok = await sc.acquire_named_lock("singcup_metrics", 60)
        try:
            return await sc.refresh_one_clip("t1")
        finally:
            await sc.release_named_lock("singcup_metrics", tok)
    assert db(go())["status"] == sc.ST_SKIPPED


def test_single_clip_refresh_survives_card_failure(db):
    """카드 조회가 실패해도 기존 수치를 0으로 덮지 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[], card_status=503))
    res = db(sc.refresh_one_clip("t1"))
    assert res["status"] == sc.ST_FAILED and res["apply_result"] == "fetch_failed"
    assert db(_read_metrics("t1")) == (3, 10)


# ── 24시간 급상승 오염 ─────────────────────────────────────────────────────
def test_recovered_clip_is_excluded_from_24h_delta(db):
    """복구된 값이 24시간 증감에도 잘못 포함되지 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())

    async def stale():
        c = await database.get_db()
        for ago, h in ((25 * 3600, 0), (3600, 0)):     # 24h 기준 + 1h 기준 모두 0
            await c.execute(
                "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
                " heart_count, view_count, follower_count, score, rank, collected_at)"
                " VALUES (?,?,?,?,1,0,0,1,?)",
                (sc.EVENT_ID, "t1", "o1", h, now - ago))
        await c.execute("UPDATE singcup_clips SET heart_count=0, view_count=1, "
                        "last_metrics_at=? WHERE clip_uid='t1'", (now - 20 * 3600,))
        await c.commit()
    db(stale())

    _install(_partial_cards(likes=52, views=10))
    db(sc.refresh_metrics())

    me = next(s for s in db(sc.load_main())["streamers"] if s["clipUid"] == "t1")
    assert me["heartCount"] == 52                  # 현재 값·순위는 즉시 반영
    assert me["delta24hState"] == "recovering"
    assert me["heartDelta24h"] is None             # 24h 증감은 집계 복구 상태
    assert me["heartChangeRate24h"] is None


def test_normal_clip_still_reports_24h_delta(db):
    """복구 가드가 정상 클립의 24시간 증감까지 막지는 않는다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())

    async def baseline():
        c = await database.get_db()
        await c.execute(
            "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
            " heart_count, view_count, follower_count, score, rank, collected_at)"
            " VALUES (?,?,?,1,1,0,0,1,?)", (sc.EVENT_ID, "t1", "o1", now - 25 * 3600))
        await c.execute("UPDATE singcup_clips SET last_metrics_at=? "
                        "WHERE clip_uid='t1'", (now - 600,))
        await c.commit()
    db(baseline())

    _install(_partial_cards(likes=21, views=10))
    db(sc.refresh_metrics(limit=200))
    me = next(s for s in db(sc.load_main())["streamers"] if s["clipUid"] == "t1")
    assert me["delta24hState"] == "ok" and me["heartDelta24h"] == 20


# ── 레인별 oldest-first ────────────────────────────────────────────────────
def test_each_lane_keeps_oldest_first(db):
    """대표 할당량과 일반 할당량 각각에서 가장 오래된 것부터 나온다."""
    db(_seed(40, hearts=lambda i: 1000 - i))       # 40개 전부 대표
    now = int(time.time())

    async def spread():
        c = await database.get_db()
        # 대표에서 뺀 20개를 일반 클립으로 만든다(대표 지정 해제)
        await c.execute("UPDATE singcup_streamers SET representative_clip_uid=NULL "
                        "WHERE channel_id >= 'o020'")
        # last_metrics_at을 역순으로 흩뿌린다 — uid 순서와 일부러 어긋나게
        for i in range(40):
            await c.execute("UPDATE singcup_clips SET last_metrics_at=? "
                            "WHERE clip_uid=?", (100 + (40 - i), f"q{i:03d}"))
        await c.commit()
    db(spread())

    due = db(sc._metrics_due(now, 10))
    reps = [r["last_metrics_at"] for r in due if r["is_rep"]]
    others = [r["last_metrics_at"] for r in due if not r["is_rep"]]
    assert reps and others, "두 레인 모두에서 뽑혀야 한다"
    assert reps == sorted(reps), "대표 레인이 oldest-first가 아니다"
    assert others == sorted(others), "일반 레인이 oldest-first가 아니다"


def test_never_refreshed_clips_come_first(db):
    """한 번도 갱신 못 받은 행(last_metrics_at=0)이 항상 맨 앞이다."""
    db(_seed(30, hearts=lambda i: 1000 - i))
    now = int(time.time())

    async def mixed():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_metrics_at=?", (now - 3 * 3600,))
        await c.execute("UPDATE singcup_clips SET last_metrics_at=0 "
                        "WHERE clip_uid IN ('q029','q015')")
        await c.commit()
    db(mixed())

    head = [r["clip_uid"] for r in db(sc._metrics_due(now, 5))][:2]
    assert sorted(head) == ["q015", "q029"]
