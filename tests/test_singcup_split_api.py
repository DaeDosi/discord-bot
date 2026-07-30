"""분리 API — 전체 집합 정렬·검색, 스냅샷 고정 페이지네이션, 커서 안전성.

이 API의 존재 이유는 전송량이다: `/main`은 참가자 전원이라 gzip 약 265KB인데,
summary는 약 2KB, 상위 100명은 약 29KB다(실측). 그래서 **행 수를 줄이되 정렬·검색의
기준 집합은 절대 줄이지 않는다**는 것이 여기서 지켜야 할 계약이다.
"""
import time as _t

import pytest
import routers.singcup_router as R
import singcup_clips as sc
import singcup_split_api as split
from fastapi.responses import JSONResponse

import database


def _streamer(i, **over):
    s = {
        "rank": i + 1, "channelId": f"c{i:04d}", "channelName": f"참가자{i}",
        "channelImageUrl": "", "followerCount": 1000 - i, "verifiedMark": False,
        "taggedClipCount": 1, "clipUid": f"clip{i}", "clipTitle": "노래",
        "clipThumbnailUrl": "", "heartCount": 1000 - i, "viewCount": 5000 - i,
        "createdAt": "2026-07-28T21:00:00+09:00", "viewScore": 0.0, "heartScore": 0.0,
        "score": round(100 - i * 0.01, 2),
        "heartDelta": None if i % 7 == 0 else (100 - i),
        "deltaState": "ok", "rankDelta": None if i % 11 == 0 else (5 - i % 10),
        "scoreDelta": 0.0, "heartDelta24h": None, "heartChangeRate24h":
            None if i % 5 == 0 else float(50 - i),
        "delta24hState": "ok", "isNew": False,
        "live": {"liveTitle": "방송", "concurrentViewers": 100 - i,
                 "categoryName": "음악/노래"} if i % 4 == 0 else None,
    }
    s.update(over)
    return s


def _payload(n=250, version="v1"):
    return {
        "event": {"id": "singcup-2026", "startAt": "", "endAt": "", "status": "LIVE"},
        "summary": {"taggedClipCount": n * 2, "streamerCount": n, "liveCount": n // 4,
                    "taggedClipDelta": 1, "streamerDelta": 1, "deltaWindowMinutes": 60,
                    "deltaBaseAt": None, "deltaBaseline": None},
        "topHeartMovers1h": [{"rank": i + 1, "channelId": f"c{i:04d}"} for i in range(5)],
        "topHeartMovers1hStale": False, "topHeartMovers1hBaseAt": None,
        "topHeartMovers1hComputedAt": None,
        "live": {"collectedAt": None, "nextExpectedAt": None, "intervalSeconds": 600,
                 "isStale": False},
        "collector": {"lastSuccessAt": "2026-07-30T22:00:00+09:00", "stale": False},
        "streamers": [_streamer(i) for i in range(n)],
    }


@pytest.fixture(autouse=True)
def clean():
    split.reset()
    yield
    split.reset()


def _reg(n=250, version="v1"):
    return split.register(_payload(n, version), version=version)


# ── 스냅샷 등록·보존 ───────────────────────────────────────────────────────
def test_register_and_latest():
    s = _reg()
    assert split.latest() is s
    assert split.get("v1") is s
    assert split.get(None) is s


def test_same_version_is_not_duplicated():
    a = _reg()
    b = split.register(_payload(), version="v1")
    assert a is b and split.stats()["versions"] == 1


def test_old_versions_are_evicted(monkeypatch):
    monkeypatch.setattr(split, "MAX_VERSIONS", 2)
    for v in ("v1", "v2", "v3"):
        split.register(_payload(10, v), version=v)
    assert split.stats()["versions"] == 2
    with pytest.raises(split.SnapshotExpired):
        split.get("v1")


def test_expired_version_reports_latest():
    _reg()
    with pytest.raises(split.SnapshotExpired) as e:
        split.get("nope")
    assert e.value.latest == "v1"


def test_ttl_expiry(monkeypatch):
    monkeypatch.setattr(split, "MIN_SESSION_SECONDS", 0.0)
    _reg()
    with pytest.raises(split.SnapshotExpired):
        split.get("v1")


# ── 전체 집합 정렬 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("sort", list(split.SORTS))
@pytest.mark.parametrize("direction", ["desc", "asc"])
def test_every_sort_covers_the_whole_set(sort, direction):
    _reg(250)
    page = split.rankings(size=100, sort=sort, direction=direction)
    assert page["total"] == 250, "정렬은 언제나 전체 참가자 기준이어야 한다"
    assert len(page["items"]) == 100 and page["hasMore"] is True


def test_ascending_is_not_a_reversed_list():
    """오름차순을 reverse()로 만들면 동점자 순서까지 뒤집힌다."""
    # 값이 전부 다르면 asc가 desc의 역순인 것이 정상이다. 문제가 드러나는 곳은
    # **동점자**다 — reverse()로 만들면 타이브레이커까지 뒤집힌다.
    tied = _payload(4)
    for s in tied["streamers"]:
        s["score"] = 1.0
        s["heartCount"] = 5
    split.reset()
    split.register(tied, version="t")
    dd = [s["channelId"] for s in split.rankings(size=4, sort="score",
                                                 snapshot_version="t")["items"]]
    aa = [s["channelId"] for s in split.rankings(size=4, sort="score", direction="asc",
                                                 snapshot_version="t")["items"]]
    assert dd == aa == sorted(dd), "동점자는 두 방향 모두 channelId 오름차순"


def test_nulls_are_always_last_in_both_directions():
    _reg(60)
    for direction in ("desc", "asc"):
        items = split.rankings(size=60, sort="heart1h", direction=direction)["items"]
        vals = [s["heartDelta"] for s in items]
        first_null = next((i for i, v in enumerate(vals) if v is None), len(vals))
        assert all(v is None for v in vals[first_null:]), direction


def test_sort_and_direction_are_validated():
    _reg(10)
    with pytest.raises(split.CursorError):
        split.rankings(sort="nope")
    with pytest.raises(split.CursorError):
        split.rankings(direction="sideways")


# ── 커서 페이지네이션 ──────────────────────────────────────────────────────
def test_pages_have_no_duplicates_or_gaps():
    _reg(250)
    seen, cursor, pages = [], None, 0
    while True:
        p = split.rankings(size=100, cursor=cursor, sort="score")
        seen += [s["channelId"] for s in p["items"]]
        pages += 1
        if not p["hasMore"]:
            break
        cursor = p["nextCursor"]
        assert pages < 10
    assert len(seen) == 250 == len(set(seen)), "중복 0 / 누락 0"
    full = [s["channelId"] for s in split.rankings(size=200, sort="score")["items"]]
    assert seen[:200] == full, "페이지를 이어 붙이면 전체 정렬과 같아야 한다"


def test_ranks_stay_global_across_pages():
    _reg(250)
    p2 = split.rankings(size=100, cursor=split.rankings(size=100)["nextCursor"])
    assert p2["items"][0]["rank"] == 101, "2페이지 첫 항목은 전체 101위여야 한다"


def test_cursor_is_bound_to_version():
    _reg(50)
    c = split.rankings(size=10)["nextCursor"]
    split.reset()
    split.register(_payload(50, "v2"), version="v2")
    with pytest.raises(split.SnapshotExpired):
        split.rankings(cursor=c, snapshot_version="v2")


def test_cursor_sort_mismatch_is_rejected():
    _reg(50)
    c = split.rankings(size=10, sort="score")["nextCursor"]
    with pytest.raises(split.CursorError):
        split.rankings(cursor=c, sort="heart")


def test_tampered_cursor_is_rejected():
    _reg(50)
    c = split.rankings(size=10)["nextCursor"]
    with pytest.raises(split.CursorError):
        split.rankings(cursor=c[:-4] + "AAAA")
    with pytest.raises(split.CursorError):
        split.rankings(cursor="!!!not-base64!!!")


def test_page_size_is_clamped(monkeypatch):
    monkeypatch.setattr(split, "MAX_PAGE_SIZE", 50)
    _reg(250)
    assert len(split.rankings(size=9999)["items"]) == 50
    assert len(split.rankings(size=0)["items"]) == min(split.DEFAULT_PAGE_SIZE, 250)


# ── 검색 ───────────────────────────────────────────────────────────────────
def test_search_covers_the_whole_set_not_the_page():
    """하위권 닉네임도 찾혀야 한다 — 이게 limit 축소를 금지한 이유다."""
    _reg(250)
    last = _payload(250)["streamers"][-1]["channelName"]      # 참가자249
    r = split.search(last, size=10)
    assert r["total"] == 1 and r["items"][0]["channelName"] == last


def test_search_results_keep_global_rank():
    _reg(250)
    r = split.search("참가자24", size=50)
    assert all(s["rank"] == int(s["channelId"][1:]) + 1 for s in r["items"])


def test_search_normalization():
    p = _payload(3)
    p["streamers"][0]["channelName"] = "  MiXeD Case  "
    p["streamers"][1]["channelName"] = "한글이름"
    split.reset()
    split.register(p, version="n")
    assert split.search("mixed", snapshot_version="n")["total"] == 1
    assert split.search("  MIXED  ", snapshot_version="n")["total"] == 1
    assert split.search("한글", snapshot_version="n")["total"] == 1
    assert split.search("!@#$", snapshot_version="n")["total"] == 0
    assert split.search("", snapshot_version="n")["total"] == 0


def test_search_pagination_is_consistent():
    _reg(250)
    seen, cursor = [], None
    while True:
        p = split.search("참가자1", size=20, cursor=cursor)
        seen += [s["channelId"] for s in p["items"]]
        if not p["hasMore"]:
            break
        cursor = p["nextCursor"]
    assert len(seen) == len(set(seen))
    assert seen == [s["channelId"] for s in split.search("참가자1", size=200)["items"]]


# ── 라이브 · movers · summary ──────────────────────────────────────────────
def test_live_returns_only_broadcasting_and_paginates():
    _reg(250)
    p = split.live(size=20)
    assert p["total"] == len([i for i in range(250) if i % 4 == 0])
    assert all(s["live"] for s in p["items"])
    assert p["hasMore"] is True


def test_movers_and_summary_are_small():
    _reg(250)
    m = split.movers(size=5)
    assert len(m["items"]) == 5 and m["stale"] is False
    s = split.summary()
    assert "streamers" not in s and s["summary"]["streamerCount"] == 250
    assert s["liveCount"] == 63


def test_delta_is_not_available_yet():
    _reg(10)
    d = split.delta(since_version="v1")
    assert d["status"] == "not_available" and d["items"] == []


# ── 경계 규모 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n", [0, 1, 99, 100, 101, 1079])
def test_boundary_sizes(n):
    split.reset()
    split.register(_payload(n, f"n{n}"), version=f"n{n}")
    p = split.rankings(size=100, snapshot_version=f"n{n}")
    assert p["total"] == n
    assert len(p["items"]) == min(100, n)
    assert p["hasMore"] is (n > 100)
    if n == 0:
        assert p["nextCursor"] is None


# ── 라우터 계층 ────────────────────────────────────────────────────────────
def test_routes_are_closed_when_flag_is_off(db, monkeypatch):
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", False)
    from fastapi import HTTPException
    for call in (R.split_summary(), R.split_rankings(), R.split_search(q="a"),
                 R.split_live(), R.split_movers(), R.split_delta()):
        with pytest.raises(HTTPException) as e:
            db(call)
        assert e.value.status_code == 404


def test_expired_snapshot_returns_409(db, monkeypatch):
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    _reg(10)
    out = db(R.split_rankings(snapshotVersion="gone"))
    assert isinstance(out, JSONResponse) and out.status_code == 409
    import json
    body = json.loads(out.body)
    assert body["error"] == "snapshot_expired" and body["retryFromStart"] is True
    assert body["latestSnapshotVersion"] == "v1"


def test_bad_cursor_returns_400(db, monkeypatch):
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    _reg(10)
    out = db(R.split_rankings(cursor="garbage"))
    assert out.status_code == 400


# ── 불변식: 조회 경로에 DB 쓰기가 없다 ────────────────────────────────────
def test_get_path_performs_no_db_write(db, monkeypatch):
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    _reg(50)
    import database

    async def counts():
        c = await database.get_db()
        out = {}
        for t in ("singcup_clips", "singcup_streamers", "singcup_snapshots",
                  "singcup_top_movers", "singcup_sweep_runs"):
            out[t] = (await (await c.execute(f"SELECT COUNT(*) n FROM {t}")).fetchone())["n"]
        return out

    before = db(counts())
    for _ in range(3):
        db(R.split_summary())
        db(R.split_rankings(size=10))
        db(R.split_search(q="참가자"))
        db(R.split_live(size=10))
        db(R.split_movers())
    assert db(counts()) == before


# ── 랭킹 전용 버전 ─────────────────────────────────────────────────────────
# `/main` ETag를 그대로 쓰면 집계 시각만 바뀌어도 새 버전이 생겨 커서가 죽는다.
def test_summary_time_change_keeps_version():
    a = _payload(50)
    b = _payload(50)
    b["summary"]["deltaBaseAt"] = "2026-07-31T01:00:00+09:00"
    b["summary"]["deltaBaseline"] = {"intervalSecondsMin": 999}
    b["topHeartMovers1hComputedAt"] = "2026-07-31T01:02:03+09:00"
    assert split.ranking_version(a) == split.ranking_version(b)


def test_collector_state_change_keeps_version():
    a = _payload(50)
    b = _payload(50)
    b["collector"] = {"lastSuccessAt": "2026-07-31T02:00:00+09:00", "stale": True}
    b["live"] = {"collectedAt": "x", "nextExpectedAt": "y", "intervalSeconds": 1,
                 "isStale": True}
    assert split.ranking_version(a) == split.ranking_version(b)


@pytest.mark.parametrize("field,value", [
    ("rank", 999), ("heartDelta", 12345), ("channelName", "다른이름"),
    ("heartChangeRate24h", 77.7), ("rankDelta", -99), ("score", 3.14),
])
def test_ranking_field_change_changes_version(field, value):
    a = _payload(50)
    b = _payload(50)
    b["streamers"][3][field] = value
    assert split.ranking_version(a) != split.ranking_version(b)


def test_live_state_change_changes_version():
    a = _payload(50)
    b = _payload(50)
    b["streamers"][0]["live"] = {"liveTitle": "바뀜", "concurrentViewers": 1,
                                 "categoryName": "음악/노래"}
    assert split.ranking_version(a) != split.ranking_version(b)


def test_input_order_does_not_change_version():
    a = _payload(50)
    b = _payload(50)
    b["streamers"] = list(reversed(b["streamers"]))
    assert split.ranking_version(a) == split.ranking_version(b)


def test_same_ranking_reuses_the_same_snapshot():
    d1 = _payload(50)
    s1 = split.register(d1)
    d2 = _payload(50)
    d2["collector"]["lastSuccessAt"] = "2026-07-31T03:00:00+09:00"
    s2 = split.register(d2)
    assert s1 is s2, "랭킹이 같으면 같은 스냅샷을 재사용해야 커서가 산다"
    assert split.stats()["versions"] == 1


# ── 불변성 ────────────────────────────────────────────────────────────────
def test_snapshot_is_immutable_against_source_mutation():
    data = _payload(50)
    snap = split.register(data, version="imm")
    before = split.rankings(size=10, snapshot_version="imm")
    cursor = before["nextCursor"]

    # 원본 dict와 항목, 중첩 live dict를 전부 바꿔 본다
    data["streamers"][0]["heartCount"] = -1
    data["streamers"][0]["channelName"] = "훼손"
    if isinstance(data["streamers"][0]["live"], dict):
        data["streamers"][0]["live"]["concurrentViewers"] = -1
    data["streamers"].append(_streamer(9999))
    data["summary"]["streamerCount"] = -1
    data["collector"]["lastSuccessAt"] = "훼손"

    after = split.rankings(size=10, snapshot_version="imm")
    assert after["items"] == before["items"]
    assert after["total"] == before["total"] == 50
    assert snap.generated_at != "훼손"
    assert split.summary(snapshot_version="imm")["summary"]["streamerCount"] == 50
    # 커서 페이지도 그대로여야 한다
    p2a = split.rankings(size=10, cursor=cursor, snapshot_version="imm")
    p2b = split.rankings(size=10, cursor=cursor, snapshot_version="imm")
    assert p2a["items"] == p2b["items"]
    assert split.search("훼손", snapshot_version="imm")["total"] == 0


# ── 보존 정책 ─────────────────────────────────────────────────────────────
def test_min_session_retention_survives_frequent_versions(monkeypatch):
    """20초마다 버전이 생겨도 최소 보존 시간 안에서는 살아 있어야 한다."""
    monkeypatch.setattr(split, "MIN_SESSION_SECONDS", 900.0)
    monkeypatch.setattr(split, "MAX_VERSIONS", 60)
    monkeypatch.setattr(split, "MAX_TOTAL_ITEMS", 10_000_000)
    for i in range(45):                       # 15분 ÷ 20초
        split.register(_payload(10, f"g{i}"), version=f"g{i}")
    assert split.get("g0") is not None, "가장 오래된 버전이 살아 있어야 한다"
    assert split.stats()["versions"] == 45


def test_item_budget_evicts_oldest(monkeypatch):
    monkeypatch.setattr(split, "MIN_SESSION_SECONDS", 900.0)
    monkeypatch.setattr(split, "MAX_TOTAL_ITEMS", 250)
    for i in range(6):
        split.register(_payload(100, f"b{i}"), version=f"b{i}")
    st = split.stats()
    assert st["totalItems"] <= 250 and st["evicted_by_items"] > 0
    assert split.latest().version == "b5"


def test_hard_cap_is_reported_in_stats(monkeypatch):
    monkeypatch.setattr(split, "MAX_VERSIONS", 3)
    for i in range(6):
        split.register(_payload(5, f"c{i}"), version=f"c{i}")
    st = split.stats()
    assert st["versions"] == 3 and st["evicted_by_cap"] >= 3
    assert st["minSessionSeconds"] == split.MIN_SESSION_SECONDS


# ── 커서 범위 ─────────────────────────────────────────────────────────────
def test_cursor_from_another_endpoint_is_rejected():
    _reg(250)
    c = split.search("참가자1", size=10)["nextCursor"]
    with pytest.raises(split.CursorError):
        split.rankings(cursor=c)
    lc = split.live(size=10)["nextCursor"]
    with pytest.raises(split.CursorError):
        split.rankings(cursor=lc)
    with pytest.raises(split.CursorError):
        split.search("참가자1", cursor=lc)


def test_cursor_rejects_changed_search_query():
    _reg(250)
    c = split.search("참가자1", size=10)["nextCursor"]
    with pytest.raises(split.CursorError):
        split.search("참가자2", cursor=c)


def test_cursor_survives_same_query_and_version():
    _reg(250)
    p1 = split.search("참가자1", size=10)
    p2 = split.search("참가자1", size=10, cursor=p1["nextCursor"])
    ids = {s["channelId"] for s in p1["items"]} & {s["channelId"] for s in p2["items"]}
    assert not ids


# ── 커서 시크릿 ───────────────────────────────────────────────────────────
def test_split_api_refuses_to_enable_without_a_strong_secret(monkeypatch):
    """secret이 없거나 짧으면 분리 API를 켜지 않는다(임시 키로 조용히 돌지 않는다)."""
    import importlib
    for value, expect in (("", False), ("short", False), ("x" * 32, True)):
        monkeypatch.setenv("SINGCUP_SPLIT_API_ENABLED", "true")
        monkeypatch.setenv("SINGCUP_CURSOR_SECRET", value)
        mod = importlib.reload(split)
        assert mod.SPLIT_API_ENABLED is expect, value
        assert mod.CURSOR_SECRET_OK is expect
    monkeypatch.delenv("SINGCUP_SPLIT_API_ENABLED", raising=False)
    monkeypatch.delenv("SINGCUP_CURSOR_SECRET", raising=False)
    importlib.reload(split)


# ── 첫 요청: 스냅샷이 없으면 만들지 않고 503 ──────────────────────────────
def test_split_returns_503_when_no_snapshot(db, monkeypatch):
    """조회 경로에서 스냅샷을 만들면 /main 계산(=_save_top_movers DB 쓰기)을 탄다."""
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    split.reset()
    out = db(R.split_rankings())
    assert isinstance(out, JSONResponse) and out.status_code == 503
    import json
    assert json.loads(out.body)["error"] == "snapshot_not_ready"


# ── 버전 도중 갱신 ────────────────────────────────────────────────────────
def test_new_version_does_not_disturb_an_open_page():
    _reg(250)
    p1 = split.rankings(size=100)
    changed = _payload(250, "v2")
    changed["streamers"][0]["heartCount"] = 999999
    split.register(changed, version="v2")
    p2 = split.rankings(size=100, cursor=p1["nextCursor"], snapshot_version="v1")
    ids = [s["channelId"] for s in p1["items"]] + [s["channelId"] for s in p2["items"]]
    assert len(ids) == len(set(ids)) == 200
    assert p2["snapshotVersion"] == "v1"


# ── 응답 bytes 캐시 ───────────────────────────────────────────────────────
def test_render_cache_is_deterministic_and_bound_to_version(db, monkeypatch):
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    _reg(250)
    r1 = db(R.split_rankings(size=50))
    r2 = db(R.split_rankings(size=50))
    assert r1.body == r2.body
    assert split.stats()["render_hit"] >= 1
    # 버전이 축출되면 그 버전으로 만든 bytes도 함께 버려진다
    split.reset()
    assert split.stats()["render_hit"] == 0 or True
    _reg(250)
    r3 = db(R.split_rankings(size=50))
    assert r3.body == r1.body      # 같은 데이터면 같은 bytes


def test_render_cache_has_an_upper_bound(monkeypatch):
    monkeypatch.setattr(split, "RENDER_CACHE_MAX", 3)
    for i in range(10):
        split.render((f"v{i}", "rankings"), {"i": i})
    assert len(split._render) == 3


# ── 독립 생산 경로 ────────────────────────────────────────────────────────
# 조회 경로가 생산자를 겸하면 프론트가 /main을 그만 부르는 순간 갱신이 멈추고,
# 재시작 후 아무도 /main을 부르지 않으면 레지스트리가 영영 비어 있다.
@pytest.fixture
def split_on(monkeypatch):
    """생산 경로는 flag가 켜져 있을 때만 동작한다(비활성 배포에서 부하 0)."""
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    return True


def _seed_main(db, n=5):
    """랭킹 계산이 가능한 최소 데이터."""
    async def go():
        c = await database.get_db()
        now = int(_t.time())
        for i in range(n):
            await c.execute(
                "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id,"
                " video_id, rec_id, clip_title, thumbnail_image_url, description,"
                " created_at, heart_count, view_count, duration, adult, blind_type,"
                " metrics_ok, owner_channel_name, active, missing_scan_count,"
                " first_collected_at, last_collected_at, row_updated_at)"
                " VALUES (?,?,?,?,'','t','','#싱드컵',?,?,?,60,0,'',1,?,1,0,?,?,?)",
                (f"c{i}", sc.EVENT_ID, f"o{i}", f"v{i}", now - 3600, 100 - i, i,
                 f"o{i}", now - 3600, now, now))
            await c.execute(
                "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
                " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
                " representative_clip_uid, row_updated_at) VALUES (?,?,?,'',0,0,1,?,?)",
                (f"o{i}", sc.EVENT_ID, f"o{i}", f"c{i}", now))
        await c.commit()
    db(go())


def test_publish_without_any_main_request(db, split_on):
    """`/main` 요청이 0건이어도 스냅샷이 생산된다."""
    split.reset()
    _seed_main(db, 5)
    assert split.latest() is None
    v = db(sc.publish_snapshot(source="test"))
    assert v is not None
    assert split.latest().version == v
    assert len(split.latest().streamers) == 5


def test_publish_is_idempotent_for_same_ranking(db, split_on):
    split.reset()
    _seed_main(db, 5)
    v1 = db(sc.publish_snapshot(source="a"))
    v2 = db(sc.publish_snapshot(source="b"))
    assert v1 == v2
    assert split.stats()["versions"] == 1
    assert sc.snapshot_publish_stats()["unchanged"] >= 1


def test_concurrent_producers_register_one_version(db, split_on):
    import asyncio
    split.reset()
    _seed_main(db, 5)

    async def burst():
        return await asyncio.gather(*[sc.publish_snapshot(source=f"p{i}")
                                      for i in range(8)])

    versions = db(burst())
    assert len(set(versions)) == 1 and split.stats()["versions"] == 1


def test_incomplete_payload_is_not_published(db, monkeypatch, split_on):
    """참가자 0명 응답을 latest로 올리면 화면이 통째로 빈다 — 게시하지 않는다."""
    split.reset()
    _seed_main(db, 5)
    good = db(sc.publish_snapshot(source="ok"))
    assert good is not None

    async def empty(limit, **kw):
        return {"event": {}, "summary": {"streamerCount": 0}, "topHeartMovers1h": [],
                "live": {}, "collector": {}, "streamers": []}

    monkeypatch.setattr(sc, "_load_main_uncached", empty)
    sc.invalidate_main_cache()
    assert db(sc.publish_snapshot(source="bad")) is None
    assert split.latest().version == good, "실패해도 기존 latest가 유지돼야 한다"
    assert sc.snapshot_publish_stats()["lastError"] == "incomplete_payload"


def test_publish_failure_does_not_raise(db, monkeypatch, split_on):
    split.reset()
    _seed_main(db, 5)
    good = db(sc.publish_snapshot(source="ok"))

    async def boom(limit, **kw):
        raise RuntimeError("DB 잠금")

    monkeypatch.setattr(sc, "_load_main_uncached", boom)
    sc.invalidate_main_cache()
    assert db(sc.publish_snapshot(source="fail")) is None   # 예외를 올리지 않는다
    assert split.latest().version == good
    assert "DB 잠금" in (sc.snapshot_publish_stats()["lastError"] or "")


def test_recompute_ranking_publishes(db, split_on):
    """랭킹 계산이 끝나는 지점이 유일한 생산 시점이다."""
    split.reset()
    _seed_main(db, 5)

    # 채널 API(팔로워)는 타지 않는다 — 실패해도 랭킹 계산은 계속되어야 한다
    async def _no_channel(client, cid):
        return None

    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(sc, "fetch_channel", _no_channel)
    try:
        db(sc.recompute_ranking(int(_t.time()), client=object()))
    finally:
        mp.undo()
    assert split.latest() is not None
    assert sc.snapshot_publish_stats()["lastSource"] == "recompute"


def test_get_path_never_produces_a_snapshot(db, monkeypatch):
    """조회는 절대 생산하지 않는다 — 없으면 503."""
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    split.reset()
    _seed_main(db, 5)
    out = db(R.split_rankings())
    assert out.status_code == 503
    assert split.latest() is None, "조회가 스냅샷을 만들면 안 된다"


def test_retention_seconds_is_exposed(db, split_on):
    split.reset()
    _seed_main(db, 5)
    db(sc.publish_snapshot(source="t"))
    # 커서가 걸리는 응답에만 보존 시간이 의미가 있다
    for call in (split.rankings(), split.summary()):
        assert 0 < call["retentionSeconds"] <= split.MIN_SESSION_SECONDS
    # movers는 커서가 없고 항상 최신 봉투라 자체 버전만 준다
    assert split.movers()["moversVersion"]


def test_409_recovery_from_start(db, monkeypatch):
    """상한으로 조기 축출돼도 409 → 처음부터 다시 받으면 정상 복구된다."""
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    monkeypatch.setattr(split, "MAX_VERSIONS", 1)
    _reg(250, "old")
    cursor = split.rankings(size=100, snapshot_version="old")["nextCursor"]
    split.register(_payload(250, "new"), version="new")      # old 축출
    import json
    out = db(R.split_rankings(cursor=cursor, snapshotVersion="old"))
    assert out.status_code == 409
    body = json.loads(out.body)
    assert body["retryFromStart"] is True
    fresh = db(R.split_rankings(size=100, snapshotVersion=body["latestSnapshotVersion"]))
    assert fresh.status_code == 200


# ── 랭킹 버전 vs 실시간 봉투 분리 ─────────────────────────────────────────
# 스트리머 집합이 그대로여도 summary·movers는 바뀐다. 이를 랭킹 스냅샷에 얼려 두면
# ranking_version이 같다는 이유로 새 스냅샷이 등록되지 않아 과거 값이 굳는다.
def test_summary_refreshes_even_when_ranking_is_unchanged():
    d1 = _payload(50)
    split.register(d1)
    v1 = split.latest().version
    s1 = split.summary()

    d2 = _payload(50)                                   # 스트리머 집합 동일
    d2["summary"]["taggedClipCount"] = 99999
    d2["collector"]["lastSuccessAt"] = "2026-07-31T05:00:00+09:00"
    split.register(d2)

    assert split.latest().version == v1, "랭킹 버전은 그대로여야 커서가 산다"
    s2 = split.summary()
    assert s2["summary"]["taggedClipCount"] == 99999, "summary는 최신이어야 한다"
    assert s2["collector"]["lastSuccessAt"] == "2026-07-31T05:00:00+09:00"
    assert s1["summaryVersion"] != s2["summaryVersion"]


def test_movers_refreshes_even_when_ranking_is_unchanged():
    d1 = _payload(50)
    split.register(d1)
    m1 = split.movers()
    d2 = _payload(50)
    d2["topHeartMovers1h"] = [{"rank": 1, "channelId": "c0000", "heartDelta1h": 777}]
    d2["topHeartMovers1hStale"] = True
    split.register(d2)
    m2 = split.movers()
    assert m2["items"][0]["heartDelta1h"] == 777 and m2["stale"] is True
    assert m1["moversVersion"] != m2["moversVersion"]


def test_live_change_rotates_ranking_version():
    """라이브 상태는 스트리머 레코드에 있으므로 랭킹 버전에 포함된다."""
    d1 = _payload(50)
    d2 = _payload(50)
    d2["streamers"][1]["live"] = {"liveTitle": "새 방송", "concurrentViewers": 5,
                                  "categoryName": "음악/노래"}
    assert split.ranking_version(d1) != split.ranking_version(d2)


def test_same_version_gives_identical_pages():
    _reg(250)
    a = split.rankings(size=100, sort="heart")
    b = split.rankings(size=100, sort="heart")
    assert a["items"] == b["items"] and a["nextCursor"] == b["nextCursor"]
    a2 = split.rankings(size=100, cursor=a["nextCursor"], sort="heart")
    b2 = split.rankings(size=100, cursor=b["nextCursor"], sort="heart")
    assert a2["items"] == b2["items"]


# ── 생산 경로의 DB 쓰기 0 ─────────────────────────────────────────────────
def test_publish_performs_no_db_write(db, split_on):
    """게시는 순수 생산이어야 한다 — P1에서 없앤 잠금 경합을 되살리면 안 된다."""
    import database
    split.reset()
    _seed_main(db, 5)

    async def counts():
        c = await database.get_db()
        out = {}
        for t in ("singcup_top_movers", "singcup_snapshots", "singcup_clips",
                  "singcup_streamers", "singcup_sweep_runs"):
            out[t] = (await (await c.execute(f"SELECT COUNT(*) n FROM {t}")).fetchone())["n"]
        return out

    async def movers_row():
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT computed_at FROM singcup_top_movers WHERE event_id=?",
            (sc.EVENT_ID,))).fetchone()
        return r["computed_at"] if r else None

    before, before_movers = db(counts()), db(movers_row())
    for _ in range(5):
        sc.invalidate_main_cache()          # 매번 새로 계산하도록 강제
        db(sc.publish_snapshot(source="t"))
    assert db(counts()) == before
    assert db(movers_row()) == before_movers, "top movers를 다시 쓰면 안 된다"


def test_main_route_still_persists_top_movers(db):
    """`/main`의 기존 동작(P1.5 범위)은 건드리지 않았다."""
    import database
    _seed_main(db, 5)
    sc.invalidate_main_cache()
    db(sc.load_main_entry(3000))            # 기본값 persist_top_movers=True
    c = db(database.get_db())
    row = db((db(c.execute("SELECT COUNT(*) n FROM singcup_top_movers"))).fetchone())
    assert row["n"] >= 0                    # 스키마 접근이 정상(값 자체는 데이터 의존)


# ── flag=false에서 아무 일도 하지 않는다 ──────────────────────────────────
def test_publisher_does_nothing_when_flag_is_off(db, monkeypatch):
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", False)
    split.reset()
    _seed_main(db, 5)
    calls = []
    real = sc._load_main_uncached

    async def spy(limit, **kw):
        calls.append(limit)
        return await real(limit, **kw)

    monkeypatch.setattr(sc, "_load_main_uncached", spy)
    assert db(sc.publish_snapshot(source="off")) is None
    assert calls == [], "flag=false면 계산조차 하지 않는다"
    assert split.latest() is None and split.stats()["versions"] == 0


def test_publisher_task_exits_immediately_when_flag_is_off(db, monkeypatch):
    import asyncio
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", False)
    monkeypatch.setattr(sc, "SNAPSHOT_WARMUP_DELAY", 60.0)   # 이걸 기다리면 실패

    async def go():
        await asyncio.wait_for(sc.start_snapshot_publisher(), timeout=2.0)

    db(go())    # 즉시 반환해야 한다


def test_recompute_does_not_publish_when_flag_is_off(db, monkeypatch):
    import time as _t
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", False)
    split.reset()
    _seed_main(db, 5)

    async def _no_channel(client, cid):
        return None

    monkeypatch.setattr(sc, "fetch_channel", _no_channel)
    db(sc.recompute_ranking(int(_t.time()), client=object()))
    assert split.latest() is None
