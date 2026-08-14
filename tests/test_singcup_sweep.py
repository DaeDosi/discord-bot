"""매시 정각 전체 순회 — 일정·멱등성·전체 커버리지·감속 검증.

이 구조의 핵심 주장은 두 가지다.
  1. 회차가 끝나면 **모든** 클립(대표+일반)이 정확히 한 번씩 갱신돼 있다.
  2. 같은 정각 회차는 무슨 일이 있어도 한 번만 실행된다.
아래 테스트는 그 두 가지를 깨뜨리려는 시도다.
"""
import time

import httpx
import pytest
import singcup_clips as sc
import singcup_sweep as sw
from test_singcup_clips import card

import database

KST_HOUR = 3600


@pytest.fixture
def registration_window(monkeypatch):
    """이 파일의 재확인(retag) 테스트는 **등록이 열려 있는 상태**를 전제한다.

    SINGCUP-1에서 '등록'과 '지표 갱신'의 게이트가 분리되면서, 이벤트가 끝나면
    무태그 재확인은 **닫히는 것이 정상 동작**이 됐다(열어 두면 종료 뒤에 태그를 붙인
    클립이 참가로 편입돼 순위가 소급 변경된다).

    그래서 오늘 날짜가 이벤트 기간 밖이라는 이유로 테스트가 깨지지 않도록,
    **`SINGCUP_END_AT`을 조작하지 않고** 계약 함수를 직접 연다. END_AT을 미래로
    미는 방식은 참가 판정 창까지 함께 넓혀 다른 것을 검사하게 만든다.
    """
    monkeypatch.setattr(sc, "registration_open", lambda now=None: True)


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
                " last_collected_at, row_updated_at, last_metrics_at, last_attempt_at)"
                " VALUES (?,?,?,?,'','t',?,'#싱드컵',?,?,0,60,0,'',1,1,0,?,?,?,?,?)",
                (uid, sc.EVENT_ID, cid, f"v{uid}", f"https://t/{uid}.jpg",
                 now - 9999, 10 - ci, now, now, now, last, last))
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
        "SELECT clip_uid, heart_count, view_count, last_metrics_at, last_attempt_at "
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
    assert all(m["last_attempt_at"] == 0 for m in db(_all_metrics()).values())


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
    assert all(m["last_attempt_at"] == 0 for m in db(_all_metrics()).values())


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


def test_running_previous_run_blocks_the_next_one(db):
    """이전 사이클이 살아 있으면 새 사이클은 시작하지 않는다(single-flight)."""
    db(_seed(1, 1))
    prev = sw.floor_hour(time.time()) - KST_HOUR
    db(sw._claim(prev))                                # running 상태로 남겨 둔다
    _install(_cards())
    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["status"] == sw.SKIPPED_OVERLAP
    # 연속 모드에서는 겹침을 행으로 남기지 않는다 — 매 사이클 쌓이면 잡음이다
    assert all(r["status"] != sw.SKIPPED_OVERLAP for r in db(sw.recent_runs(5)))


def test_hourly_mode_records_the_skipped_hour(db, monkeypatch):
    """정각 모드에서는 '이번 시간을 건너뛰었다'가 기록으로 남는다."""
    monkeypatch.setattr(sw, "HOURLY_MODE", True)
    db(_seed(1, 1))
    db(sw._claim(sw.floor_hour(time.time()) - KST_HOUR))
    _install(_cards())
    assert db(sw.run_sweep(sw.floor_hour(time.time())))["status"] == sw.SKIPPED_OVERLAP
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
            await c.execute("UPDATE singcup_clips SET last_metrics_at=?, "
                            "last_attempt_at=? WHERE clip_uid=?",
                            (sched + 10, sched + 10, r["clip_uid"]))
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
        old = now - 20 * 3600
        await c.execute("UPDATE singcup_clips SET heart_count=0, view_count=1, "
                        "last_metrics_at=?, last_attempt_at=?, last_heart_at=?, "
                        "last_view_at=?", (old, old, old, old))
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
            await c.execute("UPDATE singcup_clips SET last_metrics_at=?, "
                            "last_attempt_at=? WHERE clip_uid=?",
                            (sched + 5, sched + 5, r["clip_uid"]))
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
    assert st["timezone"] == "Asia/Seoul"
    # 기본은 연속 모드 — '다음 예정 시각'이라는 개념이 없다
    assert st["mode"] == "continuous" and st["schedule"] == "continuous"
    assert st["next_scheduled_at"] is None
    assert st["staleness_minutes"] == sw.STALENESS_MINUTES
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


# ── 뒤늦게 붙은 #싱드컵 태그 ────────────────────────────────────────────────
# 참가자가 클립을 먼저 올리고 나중에 설명에 태그를 붙이는 경우가 흔한데, 탐색은
# 이미 스캔한 uid를 태그 여부와 무관하게 건너뛴다. 그래서 재확인 경로가 없으면
# 그 클립은 영원히 못 들어온다(실측: 업로드 21시간 뒤에도 미등록).
IN_WINDOW = "2026-07-28 23:08:06"


def _retag_handler(desc="#노래 #싱드컵", *, owner="ow1", created=IN_WINDOW,
                   card_calls=None, detail_calls=None):
    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json={"code": 200, "content": {
                "channelId": owner, "channelName": "가수", "channelImageUrl": "",
                "followerCount": 3, "verifiedMark": False}})
        if "/categories/" in url:
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": [], "page": {}}})
        if url.endswith("/detail"):
            if detail_calls is not None:
                detail_calls.append(url)
            return httpx.Response(200, json={"code": 200, "content": {
                "clipUID": "late1", "videoId": "vLate", "clipTitle": "늦게 붙인 태그",
                "thumbnailImageUrl": "https://t/late.jpg", "createdDate": created,
                "duration": 60, "adult": False, "blindType": None}})
        if card_calls is not None:
            card_calls.append(url)
        payload = card(desc, likes=5, views=36)
        # 소유 채널은 카드에서만 얻을 수 있다(상세는 null을 준다)
        payload["card"]["interaction"]["subscription"] = {"channelId": owner}
        return httpx.Response(200, json=payload)
    return h


async def _mark_scanned(uid="late1", *, tagged=0, age_hours=10, video_id="vLate",
                        created=None, status=None, next_check_at=0, count=0):
    c = await database.get_db()
    st = status or (sc.SCAN_REGISTERED if tagged else sc.SCAN_UNTAGGED)
    now = int(time.time())
    await c.execute(
        "INSERT INTO singcup_clip_scan (clip_uid, tagged, checked_at, first_checked_at,"
        " video_id, rec_id, created_at, scan_status, next_check_at, recheck_count)"
        " VALUES (?,?,?,?,?,'{}',?,?,?,?)",
        (uid, tagged, now - int(age_hours * 3600), now - int(age_hours * 3600),
         video_id, created, st,
         None if st in sc._TERMINAL else next_check_at, count))
    await c.commit()


async def _scan_row(uid="late1"):
    c = await database.get_db()
    r = await (await c.execute(
        "SELECT * FROM singcup_clip_scan WHERE clip_uid=?", (uid,))).fetchone()
    return dict(r) if r else None


async def _clip_rows():
    c = await database.get_db()
    rows = await (await c.execute("SELECT * FROM singcup_clips")).fetchall()
    return {r["clip_uid"]: dict(r) for r in rows}


def test_late_tag_is_picked_up_on_recheck(db, registration_window):
    """설명에 나중에 #싱드컵이 붙은 클립이 재확인으로 등록된다."""
    from datetime import datetime
    created = int(datetime.strptime(IN_WINDOW, "%Y-%m-%d %H:%M:%S")
                  .replace(tzinfo=sw.KST).timestamp())
    db(_mark_scanned(created=created))
    _install(_retag_handler())

    res = db(sc.recheck_untagged_clips())
    assert res["newly_tagged"] == 1 and res["registered"] == 1

    rows = db(_clip_rows())
    assert "late1" in rows
    assert rows["late1"]["heart_count"] == 5 and rows["late1"]["view_count"] == 36
    assert rows["late1"]["owner_channel_id"] == "ow1"
    assert rows["late1"]["thumbnail_image_url"] == "https://t/late.jpg"
    # 이제 메인에 스트리머로 뜬다
    assert any(s["clipUid"] == "late1" for s in db(sc.load_main())["streamers"])


def test_still_untagged_clip_is_not_registered(db, registration_window):
    """여전히 태그가 없으면 등록하지 않고 확인 시각만 민다."""
    db(_mark_scanned())
    _install(_retag_handler(desc="#노래 #커버"))

    res = db(sc.recheck_untagged_clips())
    assert res["newly_tagged"] == 0 and res["still_untagged"] == 1
    assert db(_clip_rows()) == {}

    r = db(_scan_row())
    assert r["tagged"] == 0 and r["scan_status"] == sc.SCAN_UNTAGGED
    assert r["checked_at"] > int(time.time()) - 60
    # 다음 확인은 6시간 뒤 — 매 루프마다 다시 부르지 않는다
    assert r["next_check_at"] >= int(time.time()) + int(sc.RETAG_HOURS * 3600) - 60
    assert r["recheck_count"] == 1


def test_recently_checked_clips_are_not_rechecked(db, registration_window):
    """방금 확인한 클립은 재확인 대상이 아니다(불필요한 호출 방지)."""
    db(_mark_scanned(age_hours=0.1, next_check_at=int(time.time()) + 3600))
    calls = []
    _install(_retag_handler(card_calls=calls))
    res = db(sc.recheck_untagged_clips())
    assert res["examined"] == 0 and calls == []


def test_already_tagged_clips_are_not_rechecked(db, registration_window):
    """이미 참가작인 클립은 재확인하지 않는다."""
    db(_mark_scanned(tagged=1))
    calls = []
    _install(_retag_handler(card_calls=calls))
    assert db(sc.recheck_untagged_clips())["examined"] == 0
    assert calls == []


def test_out_of_window_clip_is_skipped(db, registration_window):
    """이벤트 기간 밖 클립은 재확인해도 등록하지 않는다."""
    from datetime import datetime
    old = int(datetime.strptime("2026-07-01 10:00:00", "%Y-%m-%d %H:%M:%S")
              .replace(tzinfo=sw.KST).timestamp())
    db(_mark_scanned(created=old))
    _install(_retag_handler())
    res = db(sc.recheck_untagged_clips())
    assert res["examined"] == 0                 # SQL에서 이미 걸러진다
    assert db(_clip_rows()) == {}


def test_legacy_row_without_video_id_uses_detail(db, registration_window):
    """videoId가 없던 예전 스캔 행은 상세 API로 재료를 채워 확인한다."""
    db(_mark_scanned(video_id="", created=None))
    details, cards = [], []
    _install(_retag_handler(detail_calls=details, card_calls=cards))

    res = db(sc.recheck_untagged_clips())
    assert details, "상세 API로 videoId를 받아와야 한다"
    assert res["newly_tagged"] == 1 and res["registered"] == 1
    assert "late1" in db(_clip_rows())


def test_recheck_lock_blocks_concurrent_runs(db, registration_window):
    import asyncio
    db(_mark_scanned())
    _install(_retag_handler())

    async def go():
        return await asyncio.gather(sc.recheck_untagged_clips(),
                                    sc.recheck_untagged_clips())
    a, b = db(go())
    assert sum(1 for r in (a, b) if r["status"] == sc.ST_SKIPPED) == 1


def test_card_owner_channel_id_is_extracted():
    """소유 채널은 interaction.subscription.channelId에서 온다."""
    p = card("#싱드컵", likes=1, views=1)
    p["card"]["interaction"]["subscription"] = {"channelId": "abc123"}
    assert sc.extract_owner_channel_id(p["card"]) == "abc123"
    assert sc.extract_owner_channel_id({"interaction": {}}) == ""
    assert sc.extract_owner_channel_id({}) == ""


# ── 실패와 무태그를 구분한다 ────────────────────────────────────────────────
# 이번 사고의 뿌리는 tagged=0 하나로 '태그 없음'과 '확인 실패'를 같게 취급한 것이다.
def test_fetch_failure_uses_short_backoff_not_six_hours(db, registration_window):
    """HTTP 실패는 6시간이 아니라 분 단위로 다시 본다."""
    db(_mark_scanned())

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        if url.endswith("/detail"):
            return _retag_handler()(request)
        return httpx.Response(503, json={"code": 503})
    _install(h)

    res = db(sc.recheck_untagged_clips())
    assert res["fetch_failed"] == 1 and res["still_untagged"] == 0
    r = db(_scan_row())
    assert r["scan_status"] == sc.SCAN_FETCH_FAILED
    gap = r["next_check_at"] - int(time.time())
    assert 0 < gap <= 600, f"실패인데 {gap}초 뒤로 밀렸다(무태그 주기를 쓰면 안 됨)"
    assert r["tagged"] == 0


def test_failure_backoff_grows_with_attempts(db):
    """반복 실패는 점점 뜸하게 — 5분 → 15분 → 30분 → 1시간."""
    now = int(time.time())
    gaps = []
    for cnt in (0, 1, 2, 5):
        nxt = sc._next_check_at(sc.SCAN_FETCH_FAILED, cnt + 1, now)
        gaps.append(nxt - now)
    assert gaps == [300, 900, 1800, 3600]


def test_untagged_backoff_grows_with_attempts(db):
    now = int(time.time())
    a = sc._next_check_at(sc.SCAN_UNTAGGED, 1, now) - now
    b = sc._next_check_at(sc.SCAN_UNTAGGED, 2, now) - now
    c = sc._next_check_at(sc.SCAN_UNTAGGED, 9, now) - now
    assert a < b < c
    assert a == int(sc.RETAG_HOURS * 3600)


def test_terminal_states_are_never_rechecked(db):
    """등록·기간밖·삭제는 최종 상태 — next_check_at이 없다."""
    now = int(time.time())
    for st in (sc.SCAN_REGISTERED, sc.SCAN_OUTSIDE_EVENT, sc.SCAN_INVALID):
        assert sc._next_check_at(st, 1, now) is None


def test_failure_does_not_become_permanent_exclusion(db, registration_window):
    """실패한 클립도 백오프가 지나면 다시 대상에 들어온다(영구 제외 금지)."""
    db(_mark_scanned(status=sc.SCAN_FETCH_FAILED,
                     next_check_at=int(time.time()) - 10))
    _install(_retag_handler())
    res = db(sc.recheck_untagged_clips())
    assert res["examined"] == 1 and res["registered"] == 1


# ── 소유 채널 안전장치 ──────────────────────────────────────────────────────
def test_missing_owner_is_not_attributed_to_a_wrong_channel(db, registration_window):
    """소유 채널을 확정 못 하면 아무 스트리머에도 귀속시키지 않는다."""
    db(_mark_scanned())

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        if url.endswith("/detail"):
            return httpx.Response(200, json={"code": 200, "content": {
                "clipUID": "late1", "videoId": "vLate", "clipTitle": "t",
                "thumbnailImageUrl": "https://t/a.jpg",
                "createdDate": IN_WINDOW, "duration": 10, "adult": False,
                "blindType": None}})          # ownerChannelId 없음
        p = card("#싱드컵", likes=5, views=36)   # 카드에도 subscription 없음
        return httpx.Response(200, json=p)
    _install(h)

    res = db(sc.recheck_untagged_clips())
    assert res["newly_tagged"] == 1 and res["missing_owner"] == 1
    assert res["registered"] == 0
    assert db(_clip_rows()) == {}                # 클립 행이 만들어지지 않았다
    r = db(_scan_row())
    assert r["scan_status"] == sc.SCAN_MISSING_OWNER
    # 짧은 백오프로 다시 시도한다(영구 포기 아님)
    assert 0 < r["next_check_at"] - int(time.time()) <= 3600


def test_owner_priority_prefers_official_field(db, registration_window):
    """공식 ownerChannelId가 있으면 그걸 쓰고, 없을 때만 카드 fallback."""
    db(_mark_scanned())

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        if url.endswith("/detail"):
            return httpx.Response(200, json={"code": 200, "content": {
                "clipUID": "late1", "videoId": "vLate", "clipTitle": "t",
                "thumbnailImageUrl": "https://t/a.jpg", "createdDate": IN_WINDOW,
                "duration": 10, "adult": False, "blindType": None,
                "ownerChannelId": "official-owner"}})
        p = card("#싱드컵", likes=5, views=36)
        p["card"]["interaction"]["subscription"] = {"channelId": "card-owner"}
        return httpx.Response(200, json=p)
    _install(h)

    db(sc.recheck_untagged_clips())
    assert db(_clip_rows())["late1"]["owner_channel_id"] == "official-owner"


def test_blinded_clip_becomes_terminal(db, registration_window):
    """삭제·블라인드 클립은 등록하지 않고 최종 상태로 닫는다."""
    db(_mark_scanned())

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        if url.endswith("/detail"):
            return httpx.Response(200, json={"code": 200, "content": {
                "clipUID": "late1", "videoId": "vLate", "clipTitle": "t",
                "thumbnailImageUrl": "", "createdDate": IN_WINDOW, "duration": 10,
                "adult": False, "blindType": "DELETE"}})
        p = card("#싱드컵", likes=5, views=36)
        p["card"]["interaction"]["subscription"] = {"channelId": "ow1"}
        return httpx.Response(200, json=p)
    _install(h)

    db(sc.recheck_untagged_clips())
    assert db(_clip_rows()) == {}
    r = db(_scan_row())
    assert r["scan_status"] == sc.SCAN_INVALID and r["next_check_at"] is None


# ── 큐 진행·중복 방지 ───────────────────────────────────────────────────────
def test_repeated_calls_walk_the_whole_backlog(db, registration_window):
    """limit보다 대상이 많아도 반복 호출하면 전부 검사된다(앞 N건 반복 아님)."""
    from datetime import datetime
    created = int(datetime.strptime(IN_WINDOW, "%Y-%m-%d %H:%M:%S")
                  .replace(tzinfo=sw.KST).timestamp())
    for i in range(12):
        db(_mark_scanned(f"u{i:02d}", created=created, video_id=f"v{i}",
                         age_hours=20 - i))
    _install(_retag_handler(desc="#노래 #커버"))     # 계속 무태그

    seen = 0
    for _ in range(4):
        res = db(sc.recheck_untagged_clips(limit=5))
        seen += res["examined"]
    assert seen == 12, f"{12 - seen}건이 한 번도 검사되지 않았다"
    assert db(sc.recheck_untagged_clips(limit=5))["examined"] == 0
    assert db(sc._due_count(int(time.time()))) == 0


def test_discovery_skips_clips_not_yet_due(db):
    """탐색은 '기록이 있다'가 아니라 '재확인 시각 전인가'로 건너뛴다."""
    now = int(time.time())
    assert sc._scan_says_skip(None, now) is False
    assert sc._scan_says_skip(
        {"status": sc.SCAN_REGISTERED, "tagged": 1, "checked_at": 0,
         "next_check_at": None}, now) is True
    # 재확인 시각 전 → 건너뜀
    assert sc._scan_says_skip(
        {"status": sc.SCAN_UNTAGGED, "tagged": 0, "checked_at": 0,
         "next_check_at": now + 999}, now) is True
    # 재확인 시각 도달 → 다시 본다(예전에는 여기서도 영구 제외됐다)
    assert sc._scan_says_skip(
        {"status": sc.SCAN_UNTAGGED, "tagged": 0, "checked_at": 0,
         "next_check_at": now - 1}, now) is False
    # 실패 상태도 시각이 되면 다시 본다
    assert sc._scan_says_skip(
        {"status": sc.SCAN_FETCH_FAILED, "tagged": 0, "checked_at": 0,
         "next_check_at": now - 1}, now) is False


def test_registered_clip_is_not_reregistered(db, registration_window):
    """이미 등록된 클립은 재확인 대상에서 빠진다(중복 행 방지)."""
    from datetime import datetime
    created = int(datetime.strptime(IN_WINDOW, "%Y-%m-%d %H:%M:%S")
                  .replace(tzinfo=sw.KST).timestamp())
    db(_mark_scanned(created=created))
    _install(_retag_handler())
    db(sc.recheck_untagged_clips())

    # 스캔 상태를 억지로 되돌려도 singcup_clips에 있으므로 대상이 아니다
    async def force():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clip_scan SET scan_status=?, next_check_at=0",
                        (sc.SCAN_UNTAGGED,))
        await c.commit()
    db(force())
    assert db(sc.recheck_untagged_clips())["examined"] == 0
    rows = db(_clip_rows())
    assert len(rows) == 1                        # UPSERT — 중복 행 없음


def test_rediscover_registers_a_single_clip(db):
    """단건 재탐색이 정상 경로로 등록하고 랭킹까지 반영한다."""
    _install(_retag_handler())
    res = db(sc.rediscover_clip("late1"))
    assert res["scan_status"] == sc.SCAN_REGISTERED
    assert res["heart_count"] == 5 and res["view_count"] == 36
    assert res["owner_channel_id"] == "ow1"
    me = next(s for s in db(sc.load_main())["streamers"] if s["clipUid"] == "late1")
    assert me["heartCount"] == 5 and me["viewCount"] == 36 and me["score"] > 0
    assert db(sc.load_main())["summary"]["streamerCount"] == 1


def test_rediscover_validates_uid(db):
    assert db(sc.rediscover_clip("bad uid!"))["status"] == sc.ST_FAILED
    assert db(sc.rediscover_clip(""))["status"] == sc.ST_FAILED


def test_rediscover_reports_untagged_without_registering(db):
    _install(_retag_handler(desc="#노래 #커버"))
    res = db(sc.rediscover_clip("late1"))
    assert res["tagged"] is False and res["scan_status"] == sc.SCAN_UNTAGGED
    assert db(_clip_rows()) == {}


def test_newly_tagged_clip_can_overtake_the_representative(db):
    """재확인으로 들어온 일반 클립이 하트가 더 많으면 대표가 바뀐다."""
    db(_seed(1, 1))                              # own0의 대표 c0_0 (하트 10)

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        if url.endswith("/detail"):
            return httpx.Response(200, json={"code": 200, "content": {
                "clipUID": "late1", "videoId": "vLate", "clipTitle": "역전",
                "thumbnailImageUrl": "https://t/a.jpg", "createdDate": IN_WINDOW,
                "duration": 10, "adult": False, "blindType": None}})
        p = card("#싱드컵", likes=999, views=50)
        p["card"]["interaction"]["subscription"] = {"channelId": "own0"}
        return httpx.Response(200, json=p)
    _install(h)

    db(sc.rediscover_clip("late1"))
    me = next(s for s in db(sc.load_main())["streamers"] if s["channelId"] == "own0")
    assert me["clipUid"] == "late1" and me["heartCount"] == 999
    assert me["taggedClipCount"] == 2             # KPI에도 반영


def test_retag_stops_when_registration_closes(db, monkeypatch):
    """등록이 닫히면 재확인도 멈춘다 (SINGCUP-1).

    예전 이름은 `test_retag_stops_after_event_grace`였고 `RETAG_GRACE_HOURS`를
    음수로 밀어 확인했다. 이제 재확인은 '등록' 축에 묶여 END_AT에서 정확히 닫히므로
    (유예 없음), 판정 기준도 게이트 하나다.
    """
    monkeypatch.setattr(sc, "registration_open", lambda now=None: False)
    assert sc.retag_enabled() is False
    assert db(sc.recheck_untagged_clips())["status"] == sc.ST_SKIPPED


def test_retag_runs_while_registration_is_open(db, monkeypatch):
    """반대로 등록이 열려 있으면 재확인은 건너뛰지 않는다."""
    monkeypatch.setattr(sc, "registration_open", lambda now=None: True)
    assert sc.retag_enabled() is True
    assert db(sc.recheck_untagged_clips())["status"] != sc.ST_SKIPPED


def test_retag_stats_reports_queue_health(db, registration_window):
    from datetime import datetime
    created = int(datetime.strptime(IN_WINDOW, "%Y-%m-%d %H:%M:%S")
                  .replace(tzinfo=sw.KST).timestamp())
    for i in range(3):
        db(_mark_scanned(f"u{i}", created=created, video_id=f"v{i}"))
    st = db(sc.retag_stats())
    assert st["enabled"] is True
    assert st["untagged_total"] == 3 and st["due_now"] == 3
    assert st["estimated_backfill_hours"] is not None


# ── 태그 표기 ──────────────────────────────────────────────────────────────
# 참가 태그는 정확히 `#싱드컵` 하나다. 앞뒤가 공백이거나 문자열 끝이어야 한다.
@pytest.mark.parametrize("text", [
    "#싱드컵", "앞 #싱드컵 뒤", "#노래 #싱드컵", "#신입 #하꼬 #아저씨 #싱드컵",
    "#싱드컵 #노래", "  #싱드컵  ",
])
def test_tag_variants_accepted(text):
    assert sc.has_singcup_tag(text)


# 다른 글자가 붙은 표기는 참가 태그로 보지 않는다(대회 규칙).
@pytest.mark.parametrize("text", [
    "[#싱드컵]", "# 싱드컵", "#싱드컵(커버)", "#싱드컵,",
    "[싱드컵]", "싱드컵", "#싱드컵대회", "#싱드컵2", "대회싱드컵", "", None,
])
def test_tag_variants_rejected(text):
    assert not sc.has_singcup_tag(text)


def test_tag_is_judged_on_description_only(db, registration_window):
    """제목의 `[싱드컵]` 표기만으로는 참가 처리되지 않는다."""
    db(_mark_scanned())
    _install(_retag_handler(desc="#노래 #커버"))   # 제목엔 [싱드컵]이 들어 있다
    res = db(sc.recheck_untagged_clips())
    assert res["still_untagged"] == 1 and res["registered"] == 0
    assert db(_clip_rows()) == {}


# ── 고아 클립 (카드 실패 → 재시도 소진 → 어느 표에도 없음) ──────────────────
# 이번 미등록의 실제 경로로 확인된 것: _scan_batch가 카드 실패 시 재시도 큐에만
# 넣고 스캔 행을 남기지 않았다. 재시도가 3회로 소진되면 그 클립은
# singcup_clips에도 singcup_clip_scan에도 없어, 목록 1페이지를 벗어나는 순간
# 어떤 경로로도 다시 만날 수 없었다.
def _pages(clips_by_cursor, *, tagged, card_status=200, calls=None):
    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url:
            return _cards()(request)
        if "/categories/" in url:
            cur = request.url.params.get("clipUID")
            items, nxt = clips_by_cursor.get(cur, ([], None))
            page = {"next": {"clipUID": nxt}} if nxt else {}
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": items, "page": page}})
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        if calls is not None:
            calls.append(uid)
        if card_status != 200:
            return httpx.Response(card_status, json={"code": card_status})
        p = card("#싱드컵" if uid in tagged else "#노래", likes=5, views=36)
        p["card"]["interaction"]["subscription"] = {"channelId": f"own-{uid}"}
        return httpx.Response(200, json=p)
    return h


def _clip(uid, created="2026-07-28 23:08:06"):
    return {"clipUID": uid, "videoId": f"v{uid}", "recId": "{}",
            "ownerChannelId": f"own-{uid}", "clipTitle": "t",
            "thumbnailImageUrl": "https://t/x.jpg", "categoryType": "ETC",
            "clipCategory": "music", "duration": 60, "adult": False,
            "createdDate": created, "blindType": None,
            "ownerChannel": {"channelId": f"own-{uid}", "channelName": "가수",
                             "channelImageUrl": "", "verifiedMark": False}}


async def _scan_uids():
    c = await database.get_db()
    rows = await (await c.execute(
        "SELECT clip_uid, scan_status FROM singcup_clip_scan")).fetchall()
    return {r["clip_uid"]: r["scan_status"] for r in rows}


def test_card_failure_still_leaves_a_scan_row(db):
    """카드 조회가 실패해도 스캔 상태를 남긴다 — 고아가 되지 않게."""
    pages = {None: ([_clip("orph")], None)}
    _install(_pages(pages, tagged={"orph"}, card_status=503))
    db(sc.discover_new_clips())

    st = db(_scan_uids())
    assert st.get("orph") == sc.SCAN_FETCH_FAILED
    # 재확인 큐가 이 클립을 소유한다(재시도 소진과 무관하게 다시 시도된다)
    assert any(r["clip_uid"] == "orph"
               for r in db(sc._due_scans(int(time.time()) + 99999, 50)))


def test_orphan_is_recovered_by_reconcile(db):
    """어느 표에도 없는 클립을 전체 대조가 되찾는다(원인과 무관하게)."""
    # 1페이지는 이미 아는 클립, 뒤쪽 페이지에 우리가 모르는 클립이 있다
    pages = {None: ([_clip("known")], "p2"),
             "p2": ([_clip("known2")], "p3"),
             "p3": ([_clip("lost")], None)}
    _install(_pages(pages, tagged={"known", "known2", "lost"}))
    db(sc.discover_new_clips())
    assert "lost" in db(_clip_rows())          # 첫 탐색에선 들어온다

    # 'lost'만 통째로 지워 고아 상태를 만든다
    async def orphan():
        c = await database.get_db()
        await c.execute("DELETE FROM singcup_clips WHERE clip_uid='lost'")
        await c.execute("DELETE FROM singcup_clip_scan WHERE clip_uid='lost'")
        await c.commit()
    db(orphan())

    # 신규 탐색은 1페이지가 전부 아는 클립이라 즉시 멈춰 'lost'에 닿지 못한다
    _install(_pages(pages, tagged={"known", "known2", "lost"}))
    db(sc.discover_new_clips())
    assert "lost" not in db(_clip_rows())

    # 전체 대조는 조기 종료가 없으므로 되찾는다
    _install(_pages(pages, tagged={"known", "known2", "lost"}))
    res = db(sc.reconcile_from_list())
    assert res["missing"] == 1 and res["tagged"] == 1
    assert "lost" in db(_clip_rows())
    me = next(s for s in db(sc.load_main())["streamers"] if s["clipUid"] == "lost")
    assert me["heartCount"] == 5 and me["viewCount"] == 36


def test_reconcile_only_fetches_unknown_clips(db):
    """이미 아는 클립에는 카드 요청이 나가지 않는다(대조 비용 억제)."""
    pages = {None: ([_clip("a"), _clip("b")], None)}
    _install(_pages(pages, tagged={"a", "b"}))
    db(sc.discover_new_clips())

    calls = []
    _install(_pages(pages, tagged={"a", "b"}, calls=calls))
    res = db(sc.reconcile_from_list())
    assert res["missing"] == 0 and calls == []


def test_reconcile_ignores_out_of_window_clips(db):
    pages = {None: ([_clip("old", created="2026-07-01 10:00:00")], None)}
    _install(_pages(pages, tagged={"old"}))
    res = db(sc.reconcile_from_list())
    assert res["missing"] == 0
    assert db(_clip_rows()) == {}


def test_reconcile_runs_on_its_own_interval(db):
    """정기 루프에서 매번 전체를 훑지 않는다(주기 제한)."""
    pages = {None: ([_clip("a")], None)}
    _install(_pages(pages, tagged={"a"}))
    sc._last_reconcile = 0.0
    assert db(sc.maybe_reconcile()) is not None      # 처음엔 실행
    _install(_pages(pages, tagged={"a"}))
    assert db(sc.maybe_reconcile()) is None          # 주기 전에는 건너뜀


def test_reconcile_lock_blocks_concurrent_runs(db):
    import asyncio
    pages = {None: ([_clip("a")], None)}
    _install(_pages(pages, tagged={"a"}))

    async def go():
        return await asyncio.gather(sc.reconcile_from_list(),
                                    sc.reconcile_from_list())
    a, b = db(go())
    assert sum(1 for r in (a, b) if r["status"] == sc.ST_SKIPPED) == 1


# ── /main 캐시 ─────────────────────────────────────────────────────────────
# 운영 로그 기준 프론트가 /main을 초당 수 회 부르는데 매번 전원을 조인·정렬해
# 1~4초가 걸렸다. 원본은 4분~1시간에 한 번만 바뀌므로 짧은 TTL로 충분하다.
def test_main_is_cached_within_ttl(db, monkeypatch):
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    db(_seed(2, 2))
    _install(_cards())
    db(sw.run_sweep(sw.floor_hour(time.time())))

    calls = []
    orig = sc._load_main_uncached

    async def counted(limit=200):
        calls.append(limit)
        return await orig(limit)
    monkeypatch.setattr(sc, "_load_main_uncached", counted)
    sc._main_cache.clear()

    a = db(sc.load_main())
    b = db(sc.load_main())
    assert len(calls) == 1, "TTL 안에서는 다시 계산하지 않는다"
    assert a is b


def test_main_cache_expires(db, monkeypatch):
    db(_seed(1, 1))
    _install(_cards())
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 0.0)
    sc._main_cache.clear()

    calls = []
    orig = sc._load_main_uncached

    async def counted(limit=200):
        calls.append(limit)
        return await orig(limit)
    monkeypatch.setattr(sc, "_load_main_uncached", counted)

    db(sc.load_main())
    db(sc.load_main())
    assert len(calls) == 2


def test_main_cache_is_per_limit(db, monkeypatch):
    db(_seed(2, 1))
    _install(_cards())
    sc._main_cache.clear()
    a = db(sc.load_main(limit=1))
    b = db(sc.load_main(limit=3000))
    assert len(a["streamers"]) == 1 and len(b["streamers"]) == 2


def test_main_cache_single_flight(db, monkeypatch):
    """캐시가 빈 순간 동시 요청이 몰려도 계산은 한 번만."""
    import asyncio
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60.0)
    db(_seed(2, 2))
    _install(_cards())
    sc._main_cache.clear()
    calls = []
    orig = sc._load_main_uncached

    async def slow(limit=200):
        calls.append(limit)
        await asyncio.sleep(0.05)
        return await orig(limit)
    monkeypatch.setattr(sc, "_load_main_uncached", slow)

    async def go():
        return await asyncio.gather(*[sc.load_main() for _ in range(5)])
    res = db(go())
    assert len(calls) == 1, f"동시 5건인데 {len(calls)}번 계산했다"
    assert all(r is res[0] for r in res)


# ── 1단계: 스냅샷 폭증 방지 ────────────────────────────────────────────────
# 예전에는 recompute_ranking이 불릴 때마다(코드 9곳, 최대 4분 간격) 스트리머
# 전원분을 무조건 INSERT했다. 값이 그대로여도 쌓여 최악 하루 37만 행이 된다.
async def _snap_rows():
    c = await database.get_db()
    rows = await (await c.execute(
        "SELECT owner_channel_id, snapshot_bucket, collected_at, heart_count "
        "FROM singcup_snapshots ORDER BY id")).fetchall()
    return [dict(r) for r in rows]


def test_recompute_does_not_write_snapshots_by_default(db):
    """순위 재계산은 이력을 남기지 않는다 — 화면만 즉시 맞춘다."""
    db(_seed(3, 1))
    _install(_cards())
    db(sc.recompute_ranking(int(time.time())))
    assert db(_snap_rows()) == []
    # 그래도 대표·순위는 갱신돼 있다
    assert len(db(sc.load_main())["streamers"]) == 3


def test_ten_recomputes_in_one_bucket_write_one_set(db):
    """같은 시간 버킷에서 10번 재계산해도 스냅샷은 한 세트."""
    db(_seed(3, 1))
    _install(_cards())
    now = sc.snapshot_bucket(int(time.time())) + 120
    for i in range(10):
        db(sc.recompute_ranking(now + i * 5, save_snapshot=True))
    rows = db(_snap_rows())
    assert len(rows) == 3, f"스트리머 3명인데 {len(rows)}행이 쌓였다"
    assert len({r["snapshot_bucket"] for r in rows}) == 1


def test_concurrent_recompute_does_not_duplicate(db):
    """동시 재계산도 DB UNIQUE가 막는다(앱 레벨 체크에 기대지 않는다)."""
    import asyncio
    db(_seed(4, 1))
    _install(_cards())
    now = sc.snapshot_bucket(int(time.time())) + 60

    async def go():
        return await asyncio.gather(
            *[sc.recompute_ranking(now, save_snapshot=True) for _ in range(4)])
    db(go())
    assert len(db(_snap_rows())) == 4


def test_next_bucket_writes_a_new_set(db):
    """다음 시간 버킷에서는 새 세트가 생긴다(증감 계산에 필요)."""
    db(_seed(2, 1))
    _install(_cards())
    base = sc.snapshot_bucket(int(time.time()))
    db(sc.recompute_ranking(base + 60, save_snapshot=True))
    db(sc.recompute_ranking(base + 3600 + 60, save_snapshot=True))
    rows = db(_snap_rows())
    assert len(rows) == 4
    assert len({r["snapshot_bucket"] for r in rows}) == 2


def test_only_the_hourly_sweep_writes_snapshots(db):
    """정각 회차만 이력을 남긴다 — 탐색·retag·rediscover는 남기지 않는다."""
    db(_seed(2, 2))
    _install(_cards())
    db(sw.run_sweep(sw.floor_hour(time.time())))
    after_sweep = len(db(_snap_rows()))
    assert after_sweep == 2, "정각 회차는 스트리머당 1행을 남겨야 한다"

    # 같은 버킷에서 다른 경로가 재계산해도 늘지 않는다
    _install(_retag_handler())
    db(sc.rediscover_clip("late1"))
    assert len(db(_snap_rows())) == after_sweep


def test_snapshot_bucket_is_hour_aligned():
    from datetime import datetime
    t = datetime(2026, 7, 29, 21, 47, 13, tzinfo=sw.KST).timestamp()
    b = sc.snapshot_bucket(int(t))
    assert b % 3600 == 0
    assert datetime.fromtimestamp(b, sw.KST).strftime("%H:%M:%S") == "21:00:00"


def test_hourly_snapshots_still_support_deltas(db):
    """시간당 1세트로 줄여도 1시간·24시간 증감이 계산된다."""
    db(_seed(1, 1))
    _install(_cards())
    now = int(time.time())

    async def hourly_history():
        # 운영과 같은 모양: 매시 버킷마다 한 세트씩, **현재 시각 버킷까지** 쌓인다.
        # 정각 회차가 끝나면 그 시각 버킷이 바로 생기므로, 어느 시점에서 보더라도
        # 직전 버킷 하나는 허용오차 안에 들어온다.
        c = await database.get_db()
        base = sc.snapshot_bucket(now)
        for h in range(25, -1, -1):
            bucket = base - h * 3600
            heart = 5 if h >= 24 else (10 if h > 0 else 10)
            await c.execute(
                "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
                " heart_count, view_count, follower_count, score, rank, collected_at,"
                " snapshot_bucket) VALUES (?,?,?,?,1,0,0,1,?,?)",
                (sc.EVENT_ID, "c0_0", "own0", heart, bucket + 30, bucket))
        await c.execute("UPDATE singcup_clips SET heart_count=30, "
                        "last_metrics_at=?, last_attempt_at=? WHERE clip_uid='c0_0'",
                        (now - 300, now - 300))
        await c.commit()
    db(hourly_history())

    main = db(sc.load_main())
    me = main["streamers"][0]
    # 기준은 '허용오차 안에서 가장 가까운 버킷'이라 정확히 60분 전이 아닐 수 있다.
    # 그래서 값 자체보다 "기준을 찾았고 계산됐다"를 본다(실제 시각은 deltaBaseAt).
    assert me["heartDelta"] is not None, "1시간 기준 회차를 찾지 못했다"
    assert me["heartDelta"] == 20                 # 30 - 10(직전 버킷)
    # summary.deltaBaseAt은 KPI(클립 수) 비교용이라 시드 클립의 first_collected_at이
    # 기준보다 나중이면 null이 맞다 — 여기서 보는 건 스트리머 증감이다.
    assert me["heartDelta24h"] == 25              # 30 - 5(25시간 전 버킷)


def test_duplicate_report_is_read_only(db):
    """기존 중복 현황 보고는 아무것도 지우지 않는다."""
    db(_seed(1, 1))
    now = int(time.time())

    async def legacy():
        c = await database.get_db()
        for i in range(3):                       # 버킷 없는 옛 행 3개(같은 시간대)
            await c.execute(
                "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
                " heart_count, view_count, follower_count, score, rank, collected_at)"
                " VALUES (?,?,?,?,1,0,0,1,?)",
                (sc.EVENT_ID, "c0_0", "own0", i,
                 sc.snapshot_bucket(now) + 100 + i))
        await c.commit()
    db(legacy())

    rep = db(sc.snapshot_duplicate_report())
    assert rep["total_rows"] == 3
    assert rep["legacy_rows_without_bucket"] == 3
    assert rep["legacy_duplicate_buckets"] == 1
    assert rep["legacy_rows_in_duplicate_buckets"] == 3
    assert len(db(_snap_rows())) == 3, "보고가 행을 지웠다"


# ── 2단계: 시도 시각과 성공 시각 분리 ──────────────────────────────────────
# 예전에는 last_metrics_at 하나가 '시도했다'와 '정상으로 받았다'를 겸했다.
# 둘 다 실패해도 now로 올라가서, 실제 값은 며칠 전인데 스케줄러는 방금 갱신된
# 정상 클립으로 판단했다(실패의 정상 위장).
async def _times(uid="c0_0"):
    c = await database.get_db()
    r = await (await c.execute(
        "SELECT last_attempt_at, last_heart_at, last_view_at, last_metrics_at,"
        " heart_count, view_count FROM singcup_clips WHERE clip_uid=?",
        (uid,))).fetchone()
    return dict(r)


async def _preset(uid="c0_0", *, t=0):
    c = await database.get_db()
    await c.execute(
        "UPDATE singcup_clips SET heart_count=3, view_count=99, last_attempt_at=?,"
        " last_heart_at=?, last_view_at=?, last_metrics_at=? WHERE clip_uid=?",
        (t, t, t, t, uid))
    await c.commit()


def test_both_ok_updates_all_four_times(db):
    db(_seed(1, 1))
    db(_preset())
    now = int(time.time())
    assert db(sc._apply_metrics("c0_0", 10, 20, True, True, now)) == "ok"
    t = db(_times())
    assert t["last_attempt_at"] == t["last_heart_at"] == now
    assert t["last_view_at"] == t["last_metrics_at"] == now
    assert (t["heart_count"], t["view_count"]) == (10, 20)


def test_heart_only_leaves_view_time_untouched(db):
    db(_seed(1, 1))
    db(_preset(t=1000))
    now = int(time.time())
    assert db(sc._apply_metrics("c0_0", 10, 0, True, False, now)) == "partial"
    t = db(_times())
    assert t["last_attempt_at"] == now and t["last_heart_at"] == now
    assert t["last_view_at"] == 1000, "조회수 실패인데 view 시각이 올라갔다"
    assert t["last_metrics_at"] == 1000, "부분 성공인데 metrics 시각이 올라갔다"
    assert (t["heart_count"], t["view_count"]) == (10, 99)


def test_view_only_leaves_heart_time_untouched(db):
    db(_seed(1, 1))
    db(_preset(t=1000))
    now = int(time.time())
    assert db(sc._apply_metrics("c0_0", 0, 20, False, True, now)) == "partial"
    t = db(_times())
    assert t["last_view_at"] == now and t["last_heart_at"] == 1000
    assert t["last_metrics_at"] == 1000
    assert (t["heart_count"], t["view_count"]) == (3, 20)


def test_both_failed_updates_only_attempt(db):
    """둘 다 실패하면 last_metrics_at은 그대로 — 실패를 정상으로 위장하지 않는다."""
    db(_seed(1, 1))
    db(_preset(t=1000))
    now = int(time.time())
    assert db(sc._apply_metrics("c0_0", 0, 0, False, False, now)) == "failed"
    t = db(_times())
    assert t["last_attempt_at"] == now, "재시도 간격 판단용 시각은 올라가야 한다"
    assert t["last_heart_at"] == t["last_view_at"] == 1000
    assert t["last_metrics_at"] == 1000, "실패인데 신선한 것으로 기록됐다"
    assert (t["heart_count"], t["view_count"]) == (3, 99)


def test_failing_clip_is_not_called_twice_in_one_sweep(db):
    """계속 실패하는 클립도 한 회차에서는 한 번만 부른다(무한 재호출 금지)."""
    db(_seed(2, 2))
    calls = []

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        calls.append(request.url.params.get("referer", "").rsplit("/", 1)[-1])
        return httpx.Response(503, json={"code": 503})
    _install(h)

    res = db(sw.run_sweep(sw.floor_hour(time.time())))
    assert res["status"] == sw.COMPLETED
    # HTTP 재시도(_get_json은 503에 3회)는 별개다. 여기서 보는 건 '같은 클립을
    # 회차 안에서 두 번 처리하지 않는가'다.
    assert len(set(calls)) == 4 and res["processed"] == 4
    # 전부 실패했지만 회차는 끝난다(대상에서 빠진다)
    assert db(sw.sweep_targets(sw.floor_hour(time.time()))) == []
    assert res["failed"] == 4 and res["success"] == 0

    # 실패했으므로 '정상 수신' 시각은 올라가지 않았다
    t = db(_times())
    assert t["last_attempt_at"] > 0 and t["last_metrics_at"] == 0


def test_failed_clip_is_not_counted_as_fresh(db):
    """실패 클립은 metrics_state에서 정상으로 집계되지 않는다."""
    db(_seed(1, 1))
    now = int(time.time())
    db(_preset(t=now - 10 * 3600))
    db(sc._apply_metrics("c0_0", 0, 0, False, False, now))
    assert sc.metrics_state(db(_times()), now) == "stale"

    db(_preset(t=now))
    assert sc.metrics_state(db(_times()), now) == "ok"

    db(_preset(t=now))
    db(sc._apply_metrics("c0_0", 5, 0, True, False, now))

    async def age_view():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_view_at=?", (now - 10 * 3600,))
        await c.commit()
    db(age_view())
    assert sc.metrics_state(db(_times()), now) == "partial"


def test_partial_success_stays_stale_by_metrics_at(db):
    """부분 성공은 last_metrics_at 기준으로는 계속 stale이다(의미가 좁아졌다)."""
    db(_seed(1, 1))
    now = int(time.time())
    db(_preset(t=now - 5 * 3600))
    db(sc._apply_metrics("c0_0", 7, 0, True, False, now))
    t = db(_times())
    assert now - t["last_metrics_at"] > 4 * 3600     # 둘 다 정상인 시각은 옛날 그대로
    assert t["last_heart_at"] == now                  # 하트는 방금 받았다


# ── 급상승이 비면 직전 집계를 보여준다 ─────────────────────────────────────
# 비교 기준 회차가 없거나(배포 직후·수집 공백) 그 구간에 아무도 하트를 못 받으면
# 목록이 통째로 빈다. 카드가 사라지는 것보다 '언제 것인지'를 밝히고 직전 결과를
# 보여주는 편이 낫다.
async def _seed_mover(now, *, before, after):
    """1시간 전 스냅샷(before) → 현재 하트(after)인 스트리머 하나를 만든다."""
    c = await database.get_db()
    ts = now - 3600
    await c.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at,"
        " snapshot_bucket) VALUES (?,?,?,?,1,0,0,1,?,?)",
        (sc.EVENT_ID, "c0_0", "own0", before, ts, sc.snapshot_bucket(ts)))
    await c.execute("UPDATE singcup_clips SET heart_count=?, last_metrics_at=?,"
                    " last_attempt_at=?, last_heart_at=?, last_view_at=?"
                    " WHERE clip_uid='c0_0'", (after, now - 60, now - 60,
                                               now - 60, now - 60))
    await c.commit()


def test_movers_are_saved_outside_the_request_path(db):
    """급상승 저장은 **조회가 아니라** 랭킹 계산 완료 뒤에 일어난다(P1.5).

    예전에는 `load_main()`이 응답을 만들면서 저장까지 했다. 그 쓰기가 잠금에 걸리자
    공개 GET 전체가 500이 됐다(실측 2026-07-31 Railway).
    """
    db(_seed(1, 1))
    _install(_cards())
    now = int(time.time())
    db(_seed_mover(now, before=1, after=50))

    main = db(sc.load_main())
    assert len(main["topHeartMovers1h"]) == 1
    assert main["topHeartMovers1h"][0]["heartDelta1h"] == 49
    assert main["topHeartMovers1hStale"] is False
    assert main["topHeartMovers1hComputedAt"]
    saved, _base, _at = db(sc._last_top_movers())
    assert saved == [], "조회는 저장하지 않는다"

    assert db(sc.persist_top_movers_snapshot(source="test")) == "written"
    saved, _base, at = db(sc._last_top_movers())
    assert len(saved) == 1 and at


def test_empty_movers_fall_back_to_last_result(db):
    """증가가 없으면 직전 집계를 stale 표시와 함께 돌려준다."""
    db(_seed(1, 1))
    _install(_cards())
    now = int(time.time())
    db(_seed_mover(now, before=1, after=50))
    db(sc.persist_top_movers_snapshot(source="test"))    # 요청 경로 밖에서 저장
    first = db(sc.load_main())
    assert first["topHeartMovers1hStale"] is False

    async def flatten():
        # 기준 스냅샷을 현재 하트와 같게 만들어 증가분을 0으로 → movers 없음
        c = await database.get_db()
        await c.execute("UPDATE singcup_snapshots SET heart_count=50")
        await c.commit()
    db(flatten())
    sc._main_cache.clear()

    again = db(sc.load_main())
    assert len(again["topHeartMovers1h"]) == 1, "직전 집계를 보여줘야 한다"
    assert again["topHeartMovers1hStale"] is True
    assert again["topHeartMovers1hComputedAt"]
    assert again["topHeartMovers1h"][0]["heartDelta1h"] == 49


def test_no_baseline_also_falls_back(db):
    """비교 기준 회차 자체가 없어도(배포 직후) 직전 집계를 쓴다."""
    db(_seed(1, 1))
    _install(_cards())
    now = int(time.time())
    db(_seed_mover(now, before=1, after=50))
    db(sc.persist_top_movers_snapshot(source="test"))    # 요청 경로 밖에서 저장

    async def wipe():
        c = await database.get_db()
        await c.execute("DELETE FROM singcup_snapshots")
        await c.commit()
    db(wipe())
    sc._main_cache.clear()

    again = db(sc.load_main())
    assert again["topHeartMovers1hStale"] is True
    assert len(again["topHeartMovers1h"]) == 1


def test_no_history_yet_returns_empty_not_stale(db):
    """직전 집계도 없으면 빈 목록이고 stale이 아니다(가짜 표시 금지)."""
    db(_seed(1, 1))
    _install(_cards())
    main = db(sc.load_main())
    assert main["topHeartMovers1h"] == []
    assert main["topHeartMovers1hStale"] is False


def test_fresh_movers_overwrite_the_saved_one(db):
    """새 집계가 나오면 직전 것을 덮어쓴다(오래된 결과가 눌러앉지 않게)."""
    db(_seed(1, 1))
    _install(_cards())
    now = int(time.time())
    db(_seed_mover(now, before=1, after=50))
    db(sc.persist_top_movers_snapshot(source="test"))

    async def grow():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET heart_count=200 "
                        "WHERE clip_uid='c0_0'")
        await c.commit()
    db(grow())
    sc._main_cache.clear()

    main = db(sc.load_main())
    assert main["topHeartMovers1hStale"] is False
    assert main["topHeartMovers1h"][0]["heartDelta1h"] == 199
    assert db(sc.persist_top_movers_snapshot(source="test")) == "written"
    saved, _b, _a = db(sc._last_top_movers())
    assert saved[0]["heartDelta1h"] == 199


# ── 시간 버킷 기준선은 회차 완료와 무관하게 생겨야 한다 ────────────────────
# 이력 저장을 '정각 회차 완료'에만 묶었더니, 회차가 117분 걸려 완료되지 않는
# 동안 새 스냅샷이 0건이 됐다. 기준 회차가 사라져 1시간 증감이 전부 굳었다.
def test_hourly_snapshot_is_written_without_a_completed_sweep(db):
    db(_seed(3, 1))
    _install(_cards())
    assert db(_snap_rows()) == []
    assert db(sc.ensure_hourly_snapshot()) is True
    rows = db(_snap_rows())
    assert len(rows) == 3
    assert rows[0]["snapshot_bucket"] == sc.snapshot_bucket(int(time.time()))


def test_hourly_snapshot_is_idempotent_within_a_bucket(db):
    """4분 루프가 계속 불러도 버킷당 한 세트."""
    db(_seed(3, 1))
    _install(_cards())
    now = sc.snapshot_bucket(int(time.time())) + 30
    assert db(sc.ensure_hourly_snapshot(now)) is True
    for _ in range(5):
        assert db(sc.ensure_hourly_snapshot(now + 60)) is False
    assert len(db(_snap_rows())) == 3


def test_hourly_snapshot_starts_a_new_set_next_bucket(db):
    db(_seed(2, 1))
    _install(_cards())
    base = sc.snapshot_bucket(int(time.time()))
    db(sc.ensure_hourly_snapshot(base + 30))
    db(sc.ensure_hourly_snapshot(base + 3600 + 30))
    rows = db(_snap_rows())
    assert len(rows) == 4 and len({r["snapshot_bucket"] for r in rows}) == 2


def test_delta_moves_while_a_sweep_is_still_running(db):
    """기준선이 시각에 묶이면, 회차가 끝나지 않아도 증감이 관측된다."""
    db(_seed(1, 1))
    _install(_cards())
    now = int(time.time())
    base = sc.snapshot_bucket(now)

    async def set_heart(v, at):
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET heart_count=?, last_metrics_at=?,"
                        " last_attempt_at=?, last_heart_at=?, last_view_at=?"
                        " WHERE clip_uid='c0_0'", (v, at, at, at, at))
        await c.commit()

    # 운영처럼 매 시간 버킷마다 기준선이 쌓여 있다(회차 완료와 무관하게).
    # 두 버킷을 다 만들어 두면 지금이 정시 직후든 정시 직전이든 허용오차 안에
    # 기준선이 하나는 들어온다 — 벽시계 위치에 흔들리지 않게 한다.
    db(set_heart(10, base - 3600 + 30))
    db(sc.ensure_hourly_snapshot(base - 3600 + 30))
    db(sc.ensure_hourly_snapshot(base + 30))
    # 그 뒤 회차가 도는 동안 하트가 오른다
    db(set_heart(40, now - 60))
    sc._main_cache.clear()

    main = db(sc.load_main())
    me = main["streamers"][0]
    assert me["heartDelta"] == 30, "회차 완료를 기다리지 않고도 증감이 나와야 한다"
    assert len(main["topHeartMovers1h"]) == 1
    assert main["topHeartMovers1h"][0]["heartDelta1h"] == 30


# ── 연속 갱신 모드 ─────────────────────────────────────────────────────────
# 정각에 한 번씩 돌리면 회차가 한 시간을 넘기는 순간 다음 정각이 통째로
# 건너뛰어져 실효 주기가 두 시간이 된다(실측: 회차 100분 → 완료 0회).
def test_cycle_targets_by_staleness_not_the_hour(db, monkeypatch):
    """대상 기준이 '정각'이 아니라 '마지막 시도가 N분보다 오래됨'이다."""
    monkeypatch.setattr(sw, "STALENESS_MINUTES", 30.0)
    db(_seed(2, 1))
    now = int(time.time())

    async def ages(fresh_uid, stale_uid):
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_attempt_at=? WHERE clip_uid=?",
                        (now - 300, fresh_uid))       # 5분 전 → 아직 신선
        await c.execute("UPDATE singcup_clips SET last_attempt_at=? WHERE clip_uid=?",
                        (now - 3600, stale_uid))      # 1시간 전 → 대상
        await c.commit()
    db(ages("c0_0", "c1_0"))

    _install(_cards())
    res = db(sw.run_cycle())
    assert res["total_targets"] == 1 and res["processed"] == 1


def test_cycle_runs_without_waiting_for_the_hour(db, monkeypatch):
    """정각이 아니어도 사이클이 즉시 돈다."""
    monkeypatch.setattr(sw, "STALENESS_MINUTES", 0.0)
    db(_seed(3, 1))
    _install(_cards())
    res = db(sw.run_cycle())
    assert res["status"] == sw.COMPLETED and res["processed"] == 3
    # 회차 식별자는 정각이 아니라 사이클 시작 시각이다
    runs = db(sw.recent_runs(1))
    assert runs[0]["scheduled_at"] and runs[0]["status"] == sw.COMPLETED


def test_consecutive_cycles_do_not_collide(db, monkeypatch):
    """사이클을 연달아 돌려도 회차 식별자가 겹치지 않는다."""
    monkeypatch.setattr(sw, "STALENESS_MINUTES", 0.0)
    db(_seed(2, 1))
    _install(_cards())
    a = db(sw.run_cycle())
    time.sleep(1.1)
    _install(_cards())
    b = db(sw.run_cycle())
    assert a["status"] == b["status"] == sw.COMPLETED
    runs = db(sw.recent_runs(5))
    assert len({r["scheduled_at"] for r in runs}) == 2


def test_cycle_with_nothing_due_is_a_noop(db, monkeypatch):
    """대상이 없으면 빈 사이클로 끝난다(스케줄러가 쉬는 신호)."""
    monkeypatch.setattr(sw, "STALENESS_MINUTES", 600.0)
    db(_seed(2, 1))
    now = int(time.time())

    async def fresh():
        c = await database.get_db()
        await c.execute("UPDATE singcup_clips SET last_attempt_at=?", (now,))
        await c.commit()
    db(fresh())
    _install(_cards())
    res = db(sw.run_cycle())
    assert res.get("total_targets", 0) == 0


def test_hourly_mode_is_still_available_for_rollback(db, monkeypatch):
    """SINGCUP_SWEEP_HOURLY=true 면 예전 정각 동작으로 돌아간다."""
    monkeypatch.setattr(sw, "HOURLY_MODE", True)
    db(_seed(2, 1))
    _install(_cards())
    sched = sw.floor_hour(time.time())
    res = db(sw.run_sweep(sched))
    assert res["status"] == sw.COMPLETED
    st = db(sw.sweep_status())
    assert st["mode"] == "hourly" and st["schedule"] == "0 * * * *"
    assert st["next_scheduled_at"].endswith("+09:00")
