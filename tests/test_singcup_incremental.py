"""싱드컵 증분 수집 — 카드 API 호출량 제어 검증.

카드 API는 클립 1건당 1회다. 매 사이클 전량 조회하면 태그 클립 500건 기준 500회를
넘기므로, 신규 클립만 조회하고 기존 태그 클립은 일부만 갱신해야 한다.
"""
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
        # 카드 API
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        calls.append(uid)
        if card_status != 200:
            return httpx.Response(card_status, json={"code": card_status})
        desc = "#싱드컵" if uid in tagged_uids else "#노래 #커버"
        return httpx.Response(200, json=card(desc, likes=3, views=10))
    return handler


def _install(h):
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(h))


PAGES = {
    None: ([clip("t1"), clip("t2", owner="o2"), clip("plain", owner="o3")], "c1"),
    "c1": ([clip("old", created=BEFORE)], None),
}
TAGGED = {"t1", "t2"}


def test_first_cycle_scans_all_new_clips(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    res = db(sc.collect_clips_incremental())
    assert res["status"] == "OK"
    assert sorted(calls) == ["plain", "t1", "t2"]     # 후보 3건 모두 카드 조회
    assert res["newTagged"] == 2 and res["streamers"] == 2


def test_second_cycle_makes_no_card_calls(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())
    calls.clear()

    # 같은 목록으로 즉시 재실행 — 신규도 없고 수치도 아직 신선하다
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    res = db(sc.collect_clips_incremental())
    assert res["status"] == "OK"
    assert calls == [], "신규가 없으면 카드 API를 호출하지 않아야 한다"
    assert res["cardCalls"] == 0
    assert res["streamers"] == 2                      # 순위는 그대로 유지


def test_untagged_clip_is_not_rescanned(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())
    calls.clear()

    # 태그 없는 'plain'만 남기고 다시 돌려도 카드를 부르지 않는다
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())
    assert "plain" not in calls


def test_new_clip_appearing_later_is_scanned(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())
    calls.clear()

    pages2 = {
        None: ([clip("fresh", owner="o9")] + PAGES[None][0], "c1"),
        "c1": PAGES["c1"],
    }
    _install(_handler(pages2, tagged_uids=TAGGED | {"fresh"}, calls=calls))
    res = db(sc.collect_clips_incremental())
    assert calls == ["fresh"], "새로 올라온 클립만 카드 조회해야 한다"
    assert res["newTagged"] == 1
    assert res["streamers"] == 3


def test_stale_metrics_are_refreshed(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())
    calls.clear()

    async def age_metrics():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_metrics_at=0")
        await c.commit()
    db(age_metrics())

    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    res = db(sc.collect_clips_incremental())
    assert sorted(calls) == ["t1", "t2"]              # 태그 클립만 갱신, plain은 제외
    assert res["refreshed"] == 2


def test_refresh_is_capped_per_cycle(db, monkeypatch):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())
    calls.clear()

    async def age_metrics():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_metrics_at=0")
        await c.commit()
    db(age_metrics())

    monkeypatch.setattr(sc, "REFRESH_PER_CYCLE", 1)
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    res = db(sc.collect_clips_incremental())
    assert len(calls) == 1 and res["refreshed"] == 1   # 사이클당 상한이 지켜진다


def test_card_failure_keeps_previous_metrics(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())

    async def read(uid):
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT heart_count, view_count FROM singcup_clips WHERE clip_uid=?",
            (uid,))).fetchone()
        return (r["heart_count"], r["view_count"])
    before = db(read("t1"))
    assert before == (3, 10)

    async def age_metrics():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_metrics_at=0")
        await c.commit()
    db(age_metrics())

    # 카드 API가 5xx로 죽어도 기존 수치를 0으로 덮지 않는다
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=[], card_status=503))
    db(sc.collect_clips_incremental())
    assert db(read("t1")) == before


def test_load_main_shape(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())
    d = db(sc.load_main())

    assert set(d) == {"event", "summary", "collector", "streamers"}
    assert d["summary"]["streamerCount"] == 2
    assert d["summary"]["taggedClipCount"] == 2
    top = d["streamers"][0]
    for key in ("rank", "channelId", "channelName", "clipUid", "clipThumbnailUrl",
                "heartCount", "viewCount", "viewScore", "heartScore", "score",
                "taggedClipCount", "heartDelta", "rankDelta", "isNew", "live"):
        assert key in top
    assert 0 <= top["score"] <= 100


def test_rank_and_heart_delta_between_cycles(db):
    calls = []
    _install(_handler(PAGES, tagged_uids=TAGGED, calls=calls))
    db(sc.collect_clips_incremental())
    first = db(sc.load_main())
    assert first["streamers"][0]["isNew"] is True       # 직전 스냅샷이 없다

    async def age_and_bump():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_metrics_at=0")
        # 두 회차가 같은 초에 끝나면 '직전 스냅샷'이 구분되지 않는다.
        # 실제 운영은 4분 주기라 생기지 않는 상황이지만, 테스트에서는 명시적으로 벌린다.
        await c.execute("UPDATE singcup_snapshots SET collected_at = collected_at - 600")
        await c.commit()
    db(age_and_bump())

    # t2의 하트를 크게 올려 순위를 뒤집는다
    def bumped(request):
        url = str(request.url)
        if "/categories/" in url:
            cur = request.url.params.get("clipUID")
            items, nxt = PAGES.get(cur, ([], None))
            page = {"next": {"clipUID": nxt}} if nxt else {}
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": items, "page": page}})
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json=CHANNEL_JSON)
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        likes = 99 if uid == "t2" else 3
        return httpx.Response(200, json=card("#싱드컵", likes=likes, views=10))
    _install(bumped)
    db(sc.collect_clips_incremental())

    d = db(sc.load_main())
    top = d["streamers"][0]
    assert top["clipUid"] == "t2"
    assert top["heartDelta"] == 96                      # 3 -> 99
    assert top["isNew"] is False
    assert top["rankDelta"] is not None


def test_new_scan_is_capped_and_resumes_next_cycle(db, monkeypatch):
    """신규 후보가 많아도 사이클당 상한만 훑고, 나머지는 다음 사이클에 이어서 처리한다."""
    calls = []
    pages = {
        None: ([clip(f"n{i}", owner=f"o{i}") for i in range(5)], None),
    }
    tagged = {f"n{i}" for i in range(5)}

    monkeypatch.setattr(sc, "NEW_SCAN_PER_CYCLE", 2)
    _install(_handler(pages, tagged_uids=tagged, calls=calls))
    first = db(sc.collect_clips_incremental())
    assert len(calls) == 2 and first["pendingNew"] == 3
    assert first["newTagged"] == 2

    calls.clear()
    _install(_handler(pages, tagged_uids=tagged, calls=calls))
    second = db(sc.collect_clips_incremental())
    assert len(calls) == 2 and second["pendingNew"] == 1

    calls.clear()
    _install(_handler(pages, tagged_uids=tagged, calls=calls))
    third = db(sc.collect_clips_incremental())
    assert len(calls) == 1 and third["pendingNew"] == 0
    assert db(sc.load_main())["summary"]["streamerCount"] == 5   # 결국 전부 수집된다


def test_no_deactivation_while_backlog_remains(db, monkeypatch):
    """아직 못 훑은 신규가 남아 있으면 '전체 확인'이 아니므로 비활성 처리를 하지 않는다."""
    calls = []
    pages = {None: ([clip("keep"), clip("a", owner="o2"), clip("b", owner="o3")], None)}
    _install(_handler(pages, tagged_uids={"keep", "a", "b"}, calls=calls))
    monkeypatch.setattr(sc, "NEW_SCAN_PER_CYCLE", 3)
    db(sc.collect_clips_incremental())
    assert db(sc.load_main())["summary"]["streamerCount"] == 3

    # 목록에서 전부 사라졌지만 새 후보가 대량으로 들어와 backlog가 남은 상황
    pages2 = {None: ([clip(f"new{i}", owner=f"x{i}") for i in range(5)], None)}
    monkeypatch.setattr(sc, "NEW_SCAN_PER_CYCLE", 1)
    for _ in range(2):
        _install(_handler(pages2, tagged_uids={f"new{i}" for i in range(5)}, calls=[]))
        res = db(sc.collect_clips_incremental())
        assert res["pendingNew"] > 0
    # 기존 3명이 그대로 살아 있어야 한다(backlog 중에는 비활성화 금지)
    names = {s["clipUid"] for s in db(sc.load_main())["streamers"]}
    assert {"keep", "a", "b"} <= names
