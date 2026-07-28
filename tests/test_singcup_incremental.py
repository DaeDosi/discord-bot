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

    assert set(d) == {"event", "summary", "collector", "streamers"}
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


# ── 변화량 기준: 직전 회차가 아니라 1시간 전 ───────────────────────────────
async def _snap(owner, hearts, rank, at):
    c = await database.get_db()
    await c.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at)"
        " VALUES (?,?,?,?,0,0,0,?,?)",
        (sc.EVENT_ID, f"u-{owner}", owner, hearts, rank, int(at)))
    await c.commit()


def test_delta_compares_against_one_hour_ago(db):
    """회차 간격(4분)으로 비교하면 변화량이 대부분 0이라 의미가 없다."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    owner = db(sc.load_main())["streamers"][0]["channelId"]
    db(_snap(owner, 10, 5, now - 3900))      # 65분 전 = 1시간 기준점
    db(_snap(owner, 90, 2, now - 300))       # 5분 전(직전 회차) — 기준이 되면 안 된다
    s = next(x for x in db(sc.load_main())["streamers"] if x["channelId"] == owner)
    # 현재 하트는 카드 응답의 3
    assert s["heartDelta"] == 3 - 10, "1시간 전(10) 기준이어야 한다"
    assert s["rankDelta"] == 5 - s["rank"]


def test_delta_window_is_configurable_and_one_hour_by_default():
    assert sc.DELTA_WINDOW_SECONDS == 3600


def test_delta_is_none_without_old_enough_history(db):
    """1시간 이전 기록이 없으면 비교 대상이 없다 → isNew."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    owner = db(sc.load_main())["streamers"][0]["channelId"]
    db(_snap(owner, 50, 1, now - 600))       # 10분 전뿐 — 1시간 기준에는 안 잡힌다
    s = next(x for x in db(sc.load_main())["streamers"] if x["channelId"] == owner)
    assert s["heartDelta"] is None and s["isNew"] is True


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
    # t1, t2는 1시간 전부터 있었고 t3만 방금 들어왔다 → 클립 +1, 스트리머 +1
    db(_age_clip("t1", now - 7200))
    db(_age_clip("t2", now - 7200))
    db(_age_clip("t3", now - 60))
    s = db(sc.load_main())["summary"]
    assert s["taggedClipCount"] == 3
    assert s["taggedClipDelta"] == 1
    assert s["streamerDelta"] == 1
    assert s["deltaWindowMinutes"] == 60


def test_kpi_delta_is_none_when_everything_is_brand_new(db):
    """수집을 막 시작하면 전부 신규라 '증가분'이 의미가 없다 → null."""
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    s = db(sc.load_main())["summary"]
    assert s["taggedClipDelta"] is None and s["streamerDelta"] is None


def test_kpi_delta_zero_when_nothing_new(db):
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[]))
    db(sc.run_backfill())
    now = int(time.time())
    for uid in ("t1", "t2", "t3", "plain"):
        db(_age_clip(uid, now - 7200))
    s = db(sc.load_main())["summary"]
    assert s["taggedClipDelta"] == 0 and s["streamerDelta"] == 0
