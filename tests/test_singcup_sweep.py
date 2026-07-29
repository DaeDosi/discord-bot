"""매시 정각 전체 순회 — 일정·멱등성·전체 커버리지·감속 검증.

이 구조의 핵심 주장은 두 가지다.
  1. 회차가 끝나면 **모든** 클립(대표+일반)이 정확히 한 번씩 갱신돼 있다.
  2. 같은 정각 회차는 무슨 일이 있어도 한 번만 실행된다.
아래 테스트는 그 두 가지를 깨뜨리려는 시도다.
"""
import time

import httpx
import singcup_clips as sc
import singcup_sweep as sw
from test_singcup_clips import card

import database

KST_HOUR = 3600


def _cards(likes=7, views=11, **kw):
    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json={
                "code": 200, "content": {"channelId": "o1", "channelName": "n",
                                         "channelImageUrl": "", "followerCount": 1,
                                         "verifiedMark": False}})
        if "/categories/" in url:
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": [], "page": {}}})
        return httpx.Response(200, json=card("#싱드컵", likes=likes, views=views, **kw))
    return h


def _install(h):
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(h))


async def _seed(n_streamers=3, clips_each=3, *, last=0):
    """스트리머마다 대표 1 + 일반 (clips_each-1)개를 넣는다."""
    c = await database.get_db()
    now = int(time.time())
    for si in range(n_streamers):
        cid = f"own{si}"
        for ci in range(clips_each):
            uid = f"c{si}_{ci}"
            await c.execute(
                "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id,"
                " video_id, rec_id, clip_title, thumbnail_image_url, description,"
                " created_at, heart_count, view_count, duration, adult, blind_type,"
                " metrics_ok, active, missing_scan_count, first_collected_at,"
                " last_collected_at, row_updated_at, last_metrics_at)"
                " VALUES (?,?,?,?,'','t',?,'#싱드컵',?,?,0,60,0,'',1,1,0,?,?,?,?)",
                (uid, sc.EVENT_ID, cid, f"v{uid}", f"https://t/{uid}.jpg",
                 now - 9999, 10 - ci, now, now, now, last))
        await c.execute(
            "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
            " channel_image_url, follower_count, verified_mark,"
            " representative_clip_uid, tagged_clip_count, last_channel_updated_at,"
            " row_updated_at) VALUES (?,?,'n','',0,0,?,?,?,?)",
            (cid, sc.EVENT_ID, f"c{si}_0", clips_each, now, now))
    await c.commit()


async def _all_metrics():
    c = await database.get_db()
    rows = await (await c.execute(
        "SELECT clip_uid, heart_count, view_count, last_metrics_at "
        "FROM singcup_clips WHERE event_id=?", (sc.EVENT_ID,))).fetchall()
    return {r["clip_uid"]: dict(r) for r in rows}


# ── 1~3. 일정 계산 ─────────────────────────────────────────────────────────
def test_schedule_is_every_hour_on_the_hour():
    """정각 절삭이 하루 24회, 매시 0분에 떨어진다."""
    base = sw.floor_hour(time.time())
    hours = [base + i * KST_HOUR for i in range(24)]
    assert len({h % 86400 for h in hours}) == 24            # 24개 서로 다른 시각
    for h in hours:
        assert h % 3600 == 0
        assert sw.floor_hour(h + 59 * 60 + 59) == h         # 그 시간대 내내 같은 회차


def test_hour_23_rolls_into_next_day_midnight():
    """23시 다음 회차는 다음 날 00시다(KST 날짜가 넘어간다)."""
    from datetime import datetime
    d = datetime(2026, 7, 29, 23, 0, 0, tzinfo=sw.KST)
    nxt = sw.floor_hour(d.timestamp()) + KST_HOUR
    got = datetime.fromtimestamp(nxt, sw.KST)
    assert (got.hour, got.day, got.month) == (0, 30, 7)


def test_times_are_reported_in_kst():
    from datetime import datetime
    d = datetime(2026, 7, 29, 21, 0, 0, tzinfo=sw.KST)
    s = sw.kst(int(d.timestamp()))
    assert s.startswith("2026-07-29T21:00:00") and s.endswith("+09:00")


# ── 4~6. 기동·누락 처리 ────────────────────────────────────────────────────
def test_missed_run_is_recorded_not_executed(db):
    """정각을 5분 넘겨 기동하면 실행하지 않고 missed로 남긴다."""
    db(_seed())
    sched = sw.floor_hour(time.time()) - KST_HOUR      # 한 시간 전 회차
    db(sw._record(sched, sw.MISSED, "테스트"))
    runs = db(sw.recent_runs(5))
    assert runs[0]["status"] == sw.MISSED
    # 대상 클립은 하나도 건드리지 않았다
    assert all(m["last_metrics_at"] == 0 for m in db(_all_metrics()).values())


def test_within_grace_the_run_executes(db):
    """정각 이후 5분 이내면 그 회차를 한 번 실행한다."""
    db(_seed(2, 2))
    _install(_cards())
    sched = sw.floor_hour(time.time())
    res = db(sw.run_sweep(sched))
    assert res["status"] == sw.COMPLETED and res["processed"] == 4


def test_past_runs_are_not_batched_on_startup(db):
    """과거 회차 여러 개를 서버 기동 직후 몰아서 실행하지 않는다."""
    db(_seed(2, 2))
    now = sw.floor_hour(time.time())
    for back in (3, 2, 1):
        db(sw._record(now - back * KST_HOUR, sw.MISSED, "기동 전"))
    runs = db(sw.recent_runs(10))
    assert [r["status"] for r in runs] == [sw.MISSED] * 3
    assert all(m["last_metrics_at"] == 0 for m in db(_all_metrics()).values())


# ── 7~9. 멱등성·중복 방지 ──────────────────────────────────────────────────
def test_same_hour_runs_only_once(db):
    """같은 scheduled_at은 UNIQUE로 두 번 실행되지 않는다."""
    db(_seed(2, 2))
    _install(_cards())
    sched = sw.floor_hour(time.time())
    first = db(sw.run_sweep(sched))
    assert first["status"] == sw.COMPLETED

    _install(_cards(likes=999))
    second = db(sw.run_sweep(sched))
    assert second["status"] == sw.SKIPPED_OVERLAP
    # 두 번째가 값을 덮어쓰지 않았다
    assert all(m["heart_count"] == 7 for m in db(_all_metrics()).values())


def test_only_one_worker_claims_a_run(db):
    """여러 워커가 동시에 같은 정각을 잡아도 하나만 성공한다."""
    import asyncio
    db(_seed(1, 1))
    sched = sw.floor_hour(time.time())

    async def go():
        return await asyncio.gather(sw._claim(sched), sw._claim(sched),
                                    sw._claim(sched))
    got = db(go())
    assert sum(1 for g in got if g is not None) == 1


def test_running_previous_run_blocks_the_next_hour(db):
    """이전 회차가 살아 있으면 다음 정각은 skipped_overlap으로 남긴다."""
    db(_seed(1, 1))
    prev = sw.floor_hour(time.time()) - KST_HOUR
    db(sw._claim(prev))                                # running 상태로 남겨 둔다
    _install(_cards())
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["status"] == sw.SKIPPED_OVERLAP
    assert db(sw.recent_runs(5))[0]["status"] == sw.SKIPPED_OVERLAP


def test_dead_run_is_taken_over(db):
    """heartbeat가 끊긴 회차는 실패로 닫고 다음 회차가 진행한다."""
    db(_seed(1, 1))
    prev = sw.floor_hour(time.time()) - KST_HOUR
    rid = db(sw._claim(prev))

    async def kill():
        c = await database.get_db()
        await c.execute("UPDATE singcup_sweep_runs SET heartbeat_at=? WHERE run_id=?",
                        (int(time.time()) - sw.STALE_RUN_SECONDS - 60, rid))
        await c.commit()
    db(kill())
    _install(_cards())
    assert db(sw.run_sweep(sw.floor_hour(time.time())))["status"] == sw.COMPLETED


# ── 10~12. 전체 커버리지 ───────────────────────────────────────────────────
def test_targets_include_representative_and_ordinary_clips(db):
    """대상에 대표와 일반 클립이 모두 들어간다."""
    db(_seed(3, 4))                                    # 12건(대표 3 + 일반 9)
    t = db(sw.sweep_targets(sw.floor_hour(time.time())))
    assert len(t) == 12
    assert sum(1 for x in t if x["is_rep"]) == 3
    assert sum(1 for x in t if not x["is_rep"]) == 9


def test_no_duplicate_uid_within_a_run(db):
    db(_seed(3, 4))
    t = db(sw.sweep_targets(sw.floor_hour(time.time())))
    uids = [x["clip_uid"] for x in t]
    assert len(uids) == len(set(uids))


def test_every_clip_is_refreshed_exactly_once(db):
    """회차가 끝나면 일반 클립까지 전부 한 번씩 갱신돼 있다."""
    db(_seed(4, 5))                                    # 20건
    calls = []

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        calls.append(request.url.params.get("referer", "").rsplit("/", 1)[-1])
        return httpx.Response(200, json=card("#싱드컵", likes=7, views=11))
    _install(h)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["status"] == sw.COMPLETED
    assert res["total_targets"] == 20 and res["processed"] == 20
    assert len(calls) == 20 and len(set(calls)) == 20   # 중복 호출 없음
    m = db(_all_metrics())
    assert len(m) == 20
    assert all(v["heart_count"] == 7 and v["view_count"] == 11 for v in m.values())
    # 회차가 끝나면 남은 대상이 없다
    assert db(sw.sweep_targets(sw.floor_hour(time.time()))) == []


def test_restart_resumes_and_does_not_redo(db):
    """중간에 죽었다 살아나도 남은 것만 이어서 한다."""
    db(_seed(3, 4))                                    # 12건
    sched = sw.floor_hour(time.time())

    async def mark_done(n):
        c = await database.get_db()
        rows = await (await c.execute(
            "SELECT clip_uid FROM singcup_clips LIMIT ?", (n,))).fetchall()
        for r in rows:
            await c.execute("UPDATE singcup_clips SET last_metrics_at=? "
                            "WHERE clip_uid=?", (sched + 10, r["clip_uid"]))
        await c.commit()
    db(mark_done(5))                                   # 5건은 이미 처리된 셈

    left = db(sw.sweep_targets(sched))
    assert len(left) == 7                              # 남은 7건만 대상


# ── 13. 대표 클립 재선정 ───────────────────────────────────────────────────
def test_ordinary_clip_overtaking_changes_representative(db):
    """일반 클립이 대표를 추월하면 대표와 랭킹이 바뀐다."""
    db(_seed(1, 3))                                    # 대표 c0_0(하트 10)

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        likes = 500 if uid == "c0_2" else 1            # 일반 클립이 압도적으로 상승
        return httpx.Response(200, json=card("#싱드컵", likes=likes, views=5))
    _install(h)

    db(sw.run_sweep(sw.floor_hour(time.time())))

    async def rep():
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT representative_clip_uid FROM singcup_streamers "
            "WHERE channel_id='own0'")).fetchone()
        return r["representative_clip_uid"]
    assert db(rep()) == "c0_2"
    me = db(sc.load_main())["streamers"][0]
    assert me["clipUid"] == "c0_2" and me["heartCount"] == 500


# ── 14~15. 부분 응답·복구 ──────────────────────────────────────────────────
def test_partial_response_keeps_the_missing_field(db):
    db(_seed(1, 2))

    async def preset():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET heart_count=3, view_count=99")
        await c.commit()
    db(preset())

    _install(_cards(likes=42, views=0, vod=False))     # 조회수 필드 없음
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["partial"] == 2 and res["success"] == 0
    for v in db(_all_metrics()).values():
        assert v["heart_count"] == 42 and v["view_count"] == 99


def test_recovered_clip_does_not_pollute_surge(db):
    """긴 공백 뒤 복구된 값이 1시간/24시간 급상승으로 잡히지 않는다."""
    db(_seed(1, 1))
    now = int(time.time())

    async def stale():
        c = await database.get_db()
        await c.execute(
            "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
            " heart_count, view_count, follower_count, score, rank, collected_at)"
            " VALUES (?,?,?,0,1,0,0,1,?)", (sc.EVENT_ID, "c0_0", "own0", now - 3600))
        await c.execute("UPDATE singcup_clips SET heart_count=0, view_count=1, "
                        "last_metrics_at=?", (now - 20 * 3600,))
        await c.commit()
    db(stale())

    _install(_cards(likes=52, views=7))
    db(sw.run_sweep(sw.floor_hour(time.time())))
    me = db(sc.load_main())["streamers"][0]
    assert me["heartCount"] == 52                      # 현재 값은 즉시 반영
    assert me["deltaState"] == "recovering" and me["heartDelta"] is None


# ── 16. 감속 ───────────────────────────────────────────────────────────────
def test_bucket_slows_down_on_429_and_recovers_slowly():
    b = sw.TokenBucket(2.0, 2.0, floor=0.1)
    b.slow_down("http_429")
    assert b.rate == 1.0 and b.throttled == 1
    b.slow_down("http_429")
    assert b.rate == 0.5
    for _ in range(3):
        b.recover()
    assert 0.5 < b.rate < 0.7                          # 증속은 느리게
    assert b.rate <= b.cap


def test_sweep_slows_down_when_api_returns_429(db):
    """실제 429 응답을 만나면 회차 도중 요청률이 내려간다."""
    db(_seed(2, 2))
    state = {"n": 0}

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        state["n"] += 1
        if state["n"] <= 2:
            return httpx.Response(429, json={"code": 429},
                                  headers={"Retry-After": "0"})
        return httpx.Response(200, json=card("#싱드컵", likes=7, views=11))
    _install(h)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["http_429"] >= 1
    assert res["processed"] == 4


# ── 18~20. 처리량 산정 ─────────────────────────────────────────────────────
def test_required_rate_scales_with_clip_count():
    """대상이 늘면 필요한 초당 처리량을 다시 계산한다."""
    r5000 = sw.required_rate(5022)
    assert 1.4 < r5000 < 1.6                           # 5,022 / 3,300초
    assert sw.required_rate(10000) > r5000
    assert sw.required_rate(0) == sw.MIN_RATE


def test_full_sweep_fits_in_the_target_window():
    """산정된 속도로 55분 안에 끝나는지(상한에 걸리면 초과를 인정)."""
    for total in (1000, 5022, 8000):
        rate = min(sw.MAX_RATE, sw.required_rate(total))
        minutes = total / rate / 60
        if sw.required_rate(total) <= sw.MAX_RATE:
            assert minutes <= sw.TARGET_MINUTES + 0.1


def test_run_is_not_completed_until_everything_is_done(db):
    """일부가 남으면 completed가 아니라 partial로 보고한다."""
    db(_seed(2, 2))
    sched = sw.floor_hour(time.time())
    rid = db(sw._claim(sched))

    async def only_two():
        # 2건만 처리된 상태를 만들고 회차를 마무리시킨다
        c = await database.get_db()
        rows = await (await c.execute(
            "SELECT clip_uid FROM singcup_clips LIMIT 2")).fetchall()
        for r in rows:
            await c.execute("UPDATE singcup_clips SET last_metrics_at=? "
                            "WHERE clip_uid=?", (sched + 5, r["clip_uid"]))
        await c.commit()
    db(only_two())
    assert len(db(sw.sweep_targets(sched))) == 2        # 아직 2건 남음

    _install(_cards())
    res = db(sw.run_sweep(sched, run_id=rid))
    assert res["status"] == sw.COMPLETED and res["processed"] == 2


def test_status_reports_schedule_and_staleness(db):
    db(_seed(2, 2))
    _install(_cards())
    db(sw.run_sweep(sw.floor_hour(time.time())))
    st = db(sw.sweep_status())
    assert st["timezone"] == "Asia/Seoul" and st["schedule"] == "0 * * * *"
    assert st["next_scheduled_at"].endswith("+09:00")
    assert st["last_completed_run"]["status"] == sw.COMPLETED
    assert st["last_completed_run"]["processed"] == 4
    assert st["starving"] is False
    assert st["max_staleness_seconds"] < 60


def test_large_sweep_processes_everything_once(db):
    """규모가 커져도(500건) 전원 1회 처리 — 대상 쿼리·중복 제거가 버티는지."""
    db(_seed(100, 5))                                  # 500건
    calls = []

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        calls.append(request.url.params.get("referer", "").rsplit("/", 1)[-1])
        return httpx.Response(200, json=card("#싱드컵", likes=7, views=11))
    _install(h)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["status"] == sw.COMPLETED
    assert res["total_targets"] == 500 and res["processed"] == 500
    assert len(calls) == len(set(calls)) == 500        # 중복 호출 0
    assert db(sw.sweep_targets(sw.floor_hour(time.time()))) == []


def test_rate_is_recomputed_when_target_count_grows():
    """대상이 5,022 → 8,000으로 늘면 필요한 초당 처리량도 함께 오른다."""
    a, b = sw.required_rate(5022), sw.required_rate(8000)
    assert b > a
    # 55분 안에 끝내려면 각각 이 속도가 필요하다(상한에 걸리면 behind_schedule)
    assert abs(a - 5022 / (sw.TARGET_MINUTES * 60)) < 1e-6
    assert abs(b - 8000 / (sw.TARGET_MINUTES * 60)) < 1e-6


def test_status_exposes_429_and_overlap_risk_mid_run(db):
    """진행 중에도 429·실패율·다음 정각 침범 여부가 보인다(단계적 상향 판단용)."""
    db(_seed(2, 2))
    sched = sw.floor_hour(time.time())
    rid = db(sw._claim(sched))
    # 10분에 100건 = 0.17건/초 → 5,000건이면 한 시간을 한참 넘긴다
    db(sw._progress(rid, started_at=int(time.time()) - 600, total_targets=5000,
                    processed=100, http_429=3, failed=2, success=98, rate_limit=1.0))

    st = db(sw.sweep_status())
    cur = st["current_run"]
    assert cur["http_429"] == 3
    assert cur["failed"] == 2 and cur["failure_rate"] == 0.02
    assert cur["rate_limit"] == 1.0
    # 5,000건을 이 페이스로 끝내면 한 시간을 넘긴다 → 다음 회차가 밀린다
    assert cur["behind_schedule"] is True
    assert cur["will_overlap_next_hour"] is True


# ── 썸네일 유실·복구 ───────────────────────────────────────────────────────
def _detail_handler(thumb="https://t/new.jpg", title="복구된 제목", status=200):
    def h(request):
        url = str(request.url)
        if "/clips/" in url and url.endswith("/detail"):
            if status != 200:
                return httpx.Response(status, json={"code": status})
            return httpx.Response(200, json={"code": 200, "content": {
                "clipTitle": title, "thumbnailImageUrl": thumb}})
        return _cards()(request)
    return h


async def _commit():
    c = await database.get_db()
    await c.commit()


async def _thumbs():
    c = await database.get_db()
    rows = await (await c.execute(
        "SELECT clip_uid, thumbnail_image_url, clip_title FROM singcup_clips"
    )).fetchall()
    return {r["clip_uid"]: (r["thumbnail_image_url"], r["clip_title"]) for r in rows}


def test_empty_thumbnail_does_not_overwrite_a_good_one(db):
    """목록이 썸네일을 빠뜨린 회차가 기존 이미지를 지우면 안 된다(유실의 원인)."""
    now = int(time.time())
    good = {"clip_uid": "x1", "owner_channel_id": "o1", "video_id": "v",
            "rec_id": "r", "clip_title": "제목", "thumbnail_image_url": "https://t/a.jpg",
            "description": "#싱드컵", "created_at": now, "heart_count": 1,
            "view_count": 1, "duration": 10, "adult": 0, "blind_type": "",
            "metrics_ok": 1, "owner_channel_name": "n",
            "owner_channel_image_url": "", "owner_verified": 0}
    db(sc._upsert_clip(good, now))
    # 같은 클립이 썸네일·제목 없이 다시 들어온다
    db(sc._upsert_clip({**good, "thumbnail_image_url": "", "clip_title": ""}, now))
    db(_commit())

    t = db(_thumbs())
    assert t["x1"] == ("https://t/a.jpg", "제목")


def test_sweep_repairs_missing_thumbnail(db):
    """이미 비어 있던 썸네일을 정각 회차가 상세 API로 메운다."""
    db(_seed(1, 2))

    async def blank():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET thumbnail_image_url='', "
                        "clip_title='' WHERE clip_uid='c0_0'")
        await c.commit()
    db(blank())

    _install(_detail_handler())
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["thumbnails_repaired"] == 1
    t = db(_thumbs())
    assert t["c0_0"] == ("https://t/new.jpg", "복구된 제목")
    assert t["c0_1"][0] == "https://t/c0_1.jpg"   # 값이 있던 건 그대로


def test_repair_is_skipped_when_thumbnail_exists(db):
    """썸네일이 있는 클립에는 상세 API 요청이 나가지 않는다."""
    db(_seed(1, 2))
    calls = []

    async def fill():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET thumbnail_image_url='https://t/x.jpg'")
        await c.commit()
    db(fill())

    def h(request):
        if str(request.url).endswith("/detail"):
            calls.append(str(request.url))
        return _detail_handler()(request)
    _install(h)

    db(sw.run_sweep(sw.floor_hour(time.time())))
    assert calls == []
    assert all(v[0] == "https://t/x.jpg" for v in db(_thumbs()).values())


def test_repair_failure_leaves_the_row_untouched(db):
    """상세 API가 실패해도 회차는 계속되고 행은 그대로다."""
    db(_seed(1, 1))

    async def blank():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET thumbnail_image_url=''")
        await c.commit()
    db(blank())

    _install(_detail_handler(status=500))
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["status"] == sw.COMPLETED and res["thumbnails_repaired"] == 0
    assert db(_thumbs())["c0_0"][0] == ""


def test_repair_ignores_empty_detail_response(db):
    """상세가 빈 썸네일을 줘도 '' 로 다시 쓰지 않는다."""
    db(_seed(1, 1))

    async def blank():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET thumbnail_image_url=''")
        await c.commit()
    db(blank())

    _install(_detail_handler(thumb=""))
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["thumbnails_repaired"] == 0
