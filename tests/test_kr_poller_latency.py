"""AWS 서울 poller — **결과 제출 응답이 저장 시간에만 묶인다**는 계약.

실측(2026-08-04 운영). tasks 25건 발급 → 한국에서 25건 관측 → Railway가
18:19:04부터 `krp_view_recovered` 25건을 **약 1초 만에** 전부 저장. 그런데 응답은
`[slow] POST /api/internal/singcup/kr-poller/results 169692ms`로 끝났고, 한국
poller는 `RESULTS_TIMEOUT=60초`에 끊겨 `TimeoutError`로 죽었다. `database is
locked` 0건, nonce/throttle busy 0건, 5xx 0건 — 저장은 멀쩡했고 **응답만 늦었다**.

병목은 저장 뒤에 붙어 있던 `recompute_ranking()`이다. 그 안의
`asyncio.gather(*[load_channel(...) for r in ranked])`가 참가자 **전원**(약
1,400명)의 채널 API를 `CARD_CONCURRENCY`(4)로 훑는다. 조회수 25건을 반영하려고
참가자 전원의 팔로워를 다시 읽을 이유가 없다.

그래서 이 파일이 고정하는 것은 두 가지다.

  ① 응답 경로는 **저장 + 캐시 무효화**까지만 한다. 순위 재계산은 하지 않는다.
  ② 재계산이 아무리 느리거나 실패해도 **이미 저장된 결과와 응답을 건드리지
     못한다.**

건너뛴 재계산은 주기 경로가 맡는다 — `singcup_sweep.run_cycle()`이 회차 완료마다
`recompute_ranking(save_snapshot=True)`를 부르고(연속 사이클), discover·recheck
등 여러 곳이 더 있다. 화면이 맞는 이유는 `_load_main_uncached()`가
`singcup_clips.view_count`를 **직접 읽고** `compute_scores()`를 조회 시점에 돌기
때문이다 — 캐시만 버리면 다음 요청이 새 조회수로 점수·순위를 다시 만든다.
"""
import time

import pytest
import singcup_clips as sc
import singcup_kr_poller as krp

import database

EV = sc.EVENT_ID
NOW = int(time.time())


async def _seed(uid, *, hearts=141, views=0, last_view_at=0, owner="own0"):
    conn = await database.get_db()
    await conn.execute(
        "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
        " rec_id, clip_title, thumbnail_image_url, description, created_at,"
        " heart_count, view_count, duration, adult, blind_type, metrics_ok, active,"
        " deletion_state, missing_scan_count, first_collected_at, last_collected_at,"
        " row_updated_at, last_metrics_at, last_attempt_at, last_heart_at, last_view_at)"
        " VALUES (?,?,?,?,?,?,'','#싱드컵',?,?,?,60,0,'ABROAD',0,1,'active',0,?,?,?,0,?,?,?)",
        (uid, EV, owner, "vid-1", "{}", "[싱드컵] t", NOW - 9999, hearts, views,
         NOW, NOW, NOW, NOW - 60, NOW - 60, last_view_at))
    await conn.commit()


async def _row(uid):
    conn = await database.get_db()
    r = await (await conn.execute(
        "SELECT * FROM singcup_clips WHERE clip_uid=?", (uid,))).fetchone()
    return dict(r) if r else None


async def _lease_row(uid):
    conn = await database.get_db()
    r = await (await conn.execute(
        "SELECT * FROM singcup_kr_poller_lease WHERE clip_uid=?", (uid,))).fetchone()
    return dict(r) if r else None


def _ok(uid, *, task, views=1927, hearts=146, observed_at=None):
    return {"taskId": task["taskId"], "clipUid": uid,
            "leaseToken": task["leaseToken"],
            "observedAt": NOW if observed_at is None else observed_at,
            "httpStatus": 200, "viewState": "observed",
            "viewCount": views, "heartCount": hearts, "attempts": 1}


@pytest.fixture
def slow_rank(db, monkeypatch):
    """재계산을 **느리게** 만들어 둔다. 응답 경로가 부르면 그 즉시 드러난다."""
    calls = {"rank": 0, "cache": 0, "slept": 0.0}

    async def _rank(now, **kw):
        calls["rank"] += 1
        calls["slept"] += 169.692            # 실측된 그 시간
        raise AssertionError("응답 경로에서 recompute_ranking을 부르면 안 된다")

    monkeypatch.setattr(sc, "recompute_ranking", _rank)
    monkeypatch.setattr(sc, "invalidate_main_cache",
                        lambda: calls.__setitem__("cache", calls["cache"] + 1))
    return calls


# ── ① 저장 25건 뒤에도 응답이 빠르다 ───────────────────────────────────────
def test_full_batch_response_does_not_wait_for_recompute(db, slow_rank):
    """25건을 저장해도 재계산을 부르지 않으므로 응답이 저장 시간에만 묶인다."""
    for i in range(25):
        db(_seed(f"c-{i}"))
    tasks = db(krp.lease_tasks(NOW, 25))
    assert len(tasks) == 25
    items = [_ok(t["clipUid"], task=t, views=1000 + i) for i, t in enumerate(tasks)]

    t0 = time.perf_counter()
    out = db(krp.apply_results(items, NOW))
    took = time.perf_counter() - t0

    assert out["stored"] == 25 and out["accepted"] == 25
    assert out["rejected"] == []
    assert slow_rank["rank"] == 0             # 한 번도 부르지 않았다
    assert slow_rank["slept"] == 0.0
    assert took < 5.0, f"{took:.2f}초 — 저장 시간에만 묶여야 한다"


def test_response_stays_well_under_the_client_timeout(db, slow_rank):
    """poller의 RESULTS_TIMEOUT(60초)보다 충분히 짧아야 한다."""
    for i in range(25):
        db(_seed(f"c-{i}"))
    tasks = db(krp.lease_tasks(NOW, 25))
    t0 = time.perf_counter()
    db(krp.apply_results([_ok(t["clipUid"], task=t) for t in tasks], NOW))
    assert (time.perf_counter() - t0) < 60.0 / 4


# ── ② 재계산 실패·부재가 저장을 되돌리지 않는다 ────────────────────────────
def test_recompute_failure_cannot_fail_an_already_stored_batch(db, slow_rank):
    """재계산이 예외를 던지도록 해 둬도 저장과 응답은 멀쩡하다.

    응답 경로가 재계산을 아예 부르지 않으므로 성립한다 — try/except로 삼키는
    것과 달리 **실패할 코드 자체가 경로에 없다.**
    """
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    out = db(krp.apply_results([_ok("c-1", task=t, views=555)], NOW))
    assert out["stored"] == 1
    assert db(_row("c-1"))["view_count"] == 555
    assert slow_rank["rank"] == 0


def test_stored_rows_survive_regardless_of_ranking(db, slow_rank):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, views=777)], NOW))
    row = db(_row("c-1"))
    assert row["view_count"] == 777 and row["last_view_at"] > 0


# ── ③ 캐시 무효화는 응답 전에 반드시 일어난다 ──────────────────────────────
def test_cache_is_invalidated_before_returning(db, slow_rank):
    """즉시 필요한 무효화는 응답 경로에 남긴다 — 이게 없으면 화면이 안 바뀐다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t)], NOW))
    assert slow_rank["cache"] == 1


def test_cache_is_not_invalidated_when_nothing_stored(db, slow_rank):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, views=-5)], NOW))
    assert slow_rank["cache"] == 0 and slow_rank["rank"] == 0


# ── ④ /main과 ETag가 새 조회수를 반영한다 (재계산 없이) ────────────────────
def test_main_reflects_the_new_view_count_without_recompute(db, monkeypatch):
    """`_load_main_uncached`가 view_count를 직접 읽고 점수를 조회 시점에 만든다."""
    calls = {"rank": 0}

    async def _rank(now, **kw):
        calls["rank"] += 1

    monkeypatch.setattr(sc, "recompute_ranking", _rank)
    db(_seed("c-1", hearts=146, owner="own-x"))
    conn = db(database.get_db())
    db(conn.execute(
        "INSERT INTO singcup_streamers (event_id, channel_id, channel_name,"
        " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
        " representative_clip_uid, last_channel_updated_at, row_updated_at)"
        " VALUES (?,?,?,'',0,0,1,?,?,?)", (EV, "own-x", "n", "c-1", NOW, NOW)))
    db(conn.commit())

    before = db(sc.load_main(200))
    b = [s for s in before["streamers"] if s["channelId"] == "own-x"][0]
    assert b["viewCount"] == 0 and b["viewScore"] == 0.0

    t = db(krp.lease_tasks(NOW, 25))[0]
    assert db(krp.apply_results([_ok("c-1", task=t, views=1945)], NOW))["stored"] == 1

    after = db(sc.load_main(200))
    a = [s for s in after["streamers"] if s["channelId"] == "own-x"][0]
    assert a["viewCount"] == 1945            # 재계산 없이 반영된다
    assert a["viewScore"] > 0
    assert a["clipUid"] == "c-1"             # 대표는 바뀌지 않는다
    assert calls["rank"] == 0


def test_etag_changes_after_a_stored_result(db, monkeypatch):
    monkeypatch.setattr(sc, "recompute_ranking", lambda now, **kw: None)
    db(_seed("c-1", owner="own-y"))
    conn = db(database.get_db())
    db(conn.execute(
        "INSERT INTO singcup_streamers (event_id, channel_id, channel_name,"
        " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
        " representative_clip_uid, last_channel_updated_at, row_updated_at)"
        " VALUES (?,?,?,'',0,0,1,?,?,?)", (EV, "own-y", "n", "c-1", NOW, NOW)))
    db(conn.commit())

    e1, _ = db(sc.load_main_entry(200))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, views=4242)], NOW))
    e2, src = db(sc.load_main_entry(200))
    assert src == "miss"                     # 캐시가 실제로 버려졌다
    assert e1["etag"] != e2["etag"]


# ── ⑤ 저장·lease·멱등성 계약은 그대로다 ────────────────────────────────────
def test_lease_is_closed_with_ok_after_store(db, slow_rank):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t)], NOW))
    lease = db(_lease_row("c-1"))
    assert lease["done_at"] > 0 and lease["last_result"] == "ok"


def test_resubmitting_the_same_payload_is_a_noop(db, slow_rank):
    """불명확한 성공 뒤 재제출이 와도 중복 저장·중복 증가가 없다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    item = _ok("c-1", task=t, views=1945)
    first = db(krp.apply_results([item], NOW))
    second = db(krp.apply_results([item], NOW))
    assert first["stored"] == 1
    assert second["stored"] == 0 and second["accepted"] == 1
    assert db(_row("c-1"))["view_count"] == 1945
    assert slow_rank["cache"] == 1           # 두 번째는 캐시도 건드리지 않는다


def test_heart_is_never_overwritten_by_the_poller(db, slow_rank):
    db(_seed("c-1", hearts=141))
    before = db(_row("c-1"))["last_heart_at"]
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, hearts=999)], NOW))
    row = db(_row("c-1"))
    assert row["heart_count"] == 141 and row["last_heart_at"] == before


def test_view_count_and_last_view_at_move_forward(db, slow_rank):
    db(_seed("c-1"))
    assert db(_row("c-1"))["last_view_at"] == 0
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, views=1945)], NOW))
    row = db(_row("c-1"))
    assert row["view_count"] == 1945 and row["last_view_at"] >= NOW


def test_no_view_never_writes_a_zero(db, slow_rank):
    """관측 실패를 0으로 굳히지 않는다 — unknown 상태가 유지된다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    item = _ok("c-1", task=t)
    item["viewState"] = "partial"
    item["viewCount"] = None
    out = db(krp.apply_results([item], NOW))
    row = db(_row("c-1"))
    assert out["stored"] == 0
    assert row["view_count"] == 0 and row["last_view_at"] == 0
    assert sc.view_state(row) == "unknown"


# ── ⑥ collector와 동시에 돌아도 안전하다 ───────────────────────────────────
def test_clip_lock_still_blocks_a_concurrent_collector(db, slow_rank):
    """스윕/관리자 갱신이 그 클립을 잡고 있으면 저장하지 않고 물러난다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    held = db(sc.acquire_clip_lock("c-1"))
    assert held is not None
    try:
        out = db(krp.apply_results([_ok("c-1", task=t)], NOW))
    finally:
        db(sc.release_clip_lock("c-1", held))
    assert out["stored"] == 0
    assert [r["reason"] for r in out["rejected"]] == ["locked"]
    assert db(_row("c-1"))["view_count"] == 0      # 0 퇴행 없음
    assert slow_rank["rank"] == 0


def test_lease_stays_open_when_the_clip_is_locked(db, slow_rank):
    """잠겨서 물러난 건은 lease를 닫지 않는다 — 만료 후 다음 회차가 가져간다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    held = db(sc.acquire_clip_lock("c-1"))
    try:
        db(krp.apply_results([_ok("c-1", task=t)], NOW))
    finally:
        db(sc.release_clip_lock("c-1", held))
    assert db(_lease_row("c-1"))["done_at"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 가벼운 대표 재선정 — 저장된 owner만, 외부 호출 0건
#
# 전체 `recompute_ranking()`을 응답 경로에서 뺀 대가로 자동 대표가 최대 80~100분
# 지연될 수 있었다. 주기 recompute 경로는 전부 조건부이기 때문이다 — discover는
# `if tagged:`, hourly snapshot은 '5단계 전부 성공'이고, 무조건 도는 것은 스윕
# 회차뿐이다. 그래서 저장된 owner에 한해 대표만 즉시 다시 고른다.
#
# 정렬 계약은 `_clip_sort_key = (-heart, -view, created_at, uid)`다. 조회수는
# **하트가 동점일 때만** 대표를 바꾼다 — 그 경계를 여기서 고정한다.
# ══════════════════════════════════════════════════════════════════════════
def _streamer(uid, owner, name="n"):
    async def _w():
        conn = await database.get_db()
        await conn.execute(
            "INSERT INTO singcup_streamers (event_id, channel_id, channel_name,"
            " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
            " representative_clip_uid, last_channel_updated_at, row_updated_at)"
            " VALUES (?,?,?,'',777,0,2,?,?,?)", (EV, owner, name, uid, NOW, NOW))
        await conn.commit()
    return _w()


async def _rep_of(owner):
    conn = await database.get_db()
    r = await (await conn.execute(
        "SELECT representative_clip_uid, follower_count, channel_name,"
        " tagged_clip_count FROM singcup_streamers WHERE channel_id=?",
        (owner,))).fetchone()
    return dict(r) if r else None


async def _add_override(owner, uid):
    conn = await database.get_db()
    await conn.execute(
        "INSERT INTO singcup_representative_overrides (event_id, owner_channel_id,"
        " override_clip_uid, reason, created_at, updated_at)"
        " VALUES (?,?,?,'test',?,?)", (EV, owner, uid, NOW, NOW))
    await conn.commit()


def _task_for(db_, uid):
    return [x for x in db_(krp.lease_tasks(NOW, 25)) if x["clipUid"] == uid][0]


def test_tied_hearts_higher_recovered_view_becomes_representative(db, slow_rank):
    """하트가 동점이면 복구된 조회수가 높은 쪽이 **즉시** 대표가 된다."""
    db(_seed("lo", hearts=200, views=10, last_view_at=NOW - 100, owner="own-t"))
    db(_seed("hi", hearts=200, owner="own-t"))
    db(_streamer("lo", "own-t"))
    t = _task_for(db, "hi")
    db(krp.apply_results([_ok("hi", task=t, views=9999)], NOW))
    assert db(_rep_of("own-t"))["representative_clip_uid"] == "hi"


def test_lower_hearts_never_win_on_view_alone(db, slow_rank):
    """하트가 다르면 조회수만으로 대표가 바뀌지 않는다(하트가 1순위)."""
    db(_seed("king", hearts=300, views=5, last_view_at=NOW - 100, owner="own-k"))
    db(_seed("poor", hearts=10, owner="own-k"))
    db(_streamer("king", "own-k"))
    t = _task_for(db, "poor")
    db(krp.apply_results([_ok("poor", task=t, views=999999)], NOW))
    assert db(_rep_of("own-k"))["representative_clip_uid"] == "king"


def test_valid_override_is_never_overwritten(db, slow_rank):
    """유효한 수동 지정은 자동 규칙보다 항상 앞선다."""
    db(_seed("auto", hearts=500, views=1, last_view_at=NOW - 100, owner="own-o"))
    db(_seed("pick", hearts=1, owner="own-o"))
    db(_streamer("pick", "own-o"))
    db(_add_override("own-o", "pick"))
    t = _task_for(db, "pick")
    db(krp.apply_results([_ok("pick", task=t, views=42)], NOW))
    assert db(_rep_of("own-o"))["representative_clip_uid"] == "pick"


def test_invalid_override_falls_back_to_the_automatic_rule(db, slow_rank):
    """무효 override(비활성 클립)는 조용히 자동 후보로 복귀한다."""
    db(_seed("gone", hearts=900, owner="own-i"))
    db(_seed("real", hearts=100, owner="own-i"))
    conn = db(database.get_db())
    db(conn.execute("UPDATE singcup_clips SET active=0 WHERE clip_uid=?", ("gone",)))
    db(conn.commit())
    db(_add_override("own-i", "gone"))
    db(_streamer("gone", "own-i"))
    t = _task_for(db, "real")
    db(krp.apply_results([_ok("real", task=t, views=7)], NOW))
    assert db(_rep_of("own-i"))["representative_clip_uid"] == "real"


def test_owners_are_deduplicated_across_the_batch(db, slow_rank, monkeypatch):
    """같은 owner의 클립이 여러 건이어도 재선정은 한 번, owner는 유일하다."""
    calls = {"n": 0, "owners": None}
    orig = krp.repick_representatives

    async def _spy(owners, now):
        calls["n"] += 1
        calls["owners"] = set(owners)
        return await orig(owners, now)

    monkeypatch.setattr(krp, "repick_representatives", _spy)
    for i in range(4):
        db(_seed(f"m-{i}", owner="own-d"))
    db(_streamer("m-0", "own-d"))
    tasks = db(krp.lease_tasks(NOW, 25))
    db(krp.apply_results([_ok(t["clipUid"], task=t) for t in tasks], NOW))
    assert calls["n"] == 1
    assert calls["owners"] == {"own-d"}


def test_rejected_results_never_enter_the_repick_set(db, slow_rank, monkeypatch):
    """저장되지 않은 건의 owner는 대상이 아니다."""
    seen = {}

    async def _spy(owners, now):
        seen["owners"] = set(owners)
        return 0

    monkeypatch.setattr(krp, "repick_representatives", _spy)
    db(_seed("good", owner="own-g"))
    db(_seed("bad", owner="own-b"))
    tasks = {t["clipUid"]: t for t in db(krp.lease_tasks(NOW, 25))}
    out = db(krp.apply_results(
        [_ok("good", task=tasks["good"], views=10),
         _ok("bad", task=tasks["bad"], views=-1)], NOW))     # invalid_view
    assert out["stored"] == 1
    assert seen["owners"] == {"own-g"}


def test_repick_makes_no_external_channel_calls(db, slow_rank, monkeypatch):
    """대표 선정에는 DB만 쓴다 — 채널 API를 부르면 그 자리에서 터진다."""
    def _boom(*a, **kw):
        raise AssertionError("외부 채널 API를 부르면 안 된다")

    monkeypatch.setattr(sc, "fetch_channel", _boom)
    db(_seed("a", hearts=200, owner="own-n"))
    db(_seed("b", hearts=200, views=1, last_view_at=NOW - 5, owner="own-n"))
    db(_streamer("b", "own-n"))
    t = _task_for(db, "a")
    db(krp.apply_results([_ok("a", task=t, views=5000)], NOW))
    assert db(_rep_of("own-n"))["representative_clip_uid"] == "a"


def test_unrelated_streamer_columns_are_preserved(db, slow_rank):
    """팔로워·닉네임·태그 수는 이 경로가 건드리지 않는다."""
    db(_seed("x1", hearts=200, owner="own-p"))
    db(_seed("x2", hearts=200, views=1, last_view_at=NOW - 5, owner="own-p"))
    db(_streamer("x2", "own-p", name="원래이름"))
    t = _task_for(db, "x1")
    db(krp.apply_results([_ok("x1", task=t, views=8888)], NOW))
    row = db(_rep_of("own-p"))
    assert row["representative_clip_uid"] == "x1"
    assert row["follower_count"] == 777
    assert row["channel_name"] == "원래이름"
    assert row["tagged_clip_count"] == 2


def test_no_write_when_the_representative_does_not_change(db, slow_rank):
    """대표가 그대로면 UPDATE를 내지 않는다."""
    db(_seed("only", hearts=100, owner="own-s"))
    db(_streamer("only", "own-s"))
    assert db(krp.repick_representatives({"own-s"}, NOW)) == 0


def test_repick_is_a_noop_for_an_empty_owner_set(db):
    assert db(krp.repick_representatives(set(), NOW)) == 0


def test_main_and_sweep_agree_on_the_representative(db, slow_rank):
    """`/main`과 저장 컬럼이 같은 대표를 본다 — 소비자는 모두 같은 컬럼을 읽는다."""
    db(_seed("s1", hearts=200, views=1, last_view_at=NOW - 9, owner="own-w"))
    db(_seed("s2", hearts=200, owner="own-w"))
    db(_streamer("s1", "own-w"))
    t = _task_for(db, "s2")
    db(krp.apply_results([_ok("s2", task=t, views=50000)], NOW))
    stored_rep = db(_rep_of("own-w"))["representative_clip_uid"]
    main = db(sc.load_main(200))
    row = [s for s in main["streamers"] if s["channelId"] == "own-w"][0]
    assert stored_rep == "s2" == row["clipUid"]


async def _poller_stores(uid, views):
    """poller가 한 건을 저장하고 대표를 다시 고른다(테스트용 시나리오 조각)."""
    t = [x for x in await krp.lease_tasks(NOW, 25) if x["clipUid"] == uid][0]
    return await krp.apply_results([_ok(uid, task=t, views=views)], NOW)


def _canonical_rep(owner):
    """지금 DB 상태만으로 계산한 정답 대표 — 어떤 순서든 최종값은 이것이어야 한다."""
    async def _w():
        conn = await database.get_db()
        rows = [dict(r) for r in await (await conn.execute(
            "SELECT * FROM singcup_clips WHERE event_id=? AND active=1 "
            "AND deletion_state<>? AND owner_channel_id=?",
            (EV, sc.DEL_CONFIRMED, owner))).fetchall()]
        ov = await sc._representative_overrides()
        return {r["owner_channel_id"]: r["clip_uid"]
                for r in sc._build_reps(rows, ov)}.get(owner)
    return _w()


def test_full_recompute_cannot_overwrite_during_the_channel_gather(db, monkeypatch):
    """① gather(외부 채널 API)가 도는 **도중** poller가 저장하는 기존 race.

    `asyncio.gather` 자체를 갈아끼우지 않는다 — 그러면 이미 만들어진 coroutine이
    버려져 `never awaited` 경고가 난다. 대신 `fetch_channel`을 hook해 실제 실행
    흐름을 그대로 두고 그 안에서 poller를 돌린다.
    """
    db(_seed("old", hearts=200, views=1, last_view_at=NOW - 9, owner="own-r1"))
    db(_seed("new", hearts=200, owner="own-r1"))
    db(_streamer("old", "own-r1"))
    fired = {"n": 0}

    async def _fetch(client, cid):
        if fired["n"] == 0:                  # gather 한가운데서 딱 한 번
            fired["n"] += 1
            await _poller_stores("new", 70000)
        return {}

    monkeypatch.setattr(sc, "fetch_channel", _fetch)
    db(sc.recompute_ranking(NOW))
    assert fired["n"] == 1
    assert db(_rep_of("own-r1"))["representative_clip_uid"] == "new"
    assert db(_rep_of("own-r1"))["representative_clip_uid"] == db(_canonical_rep("own-r1"))


def test_poller_cannot_slip_between_the_refetch_and_the_upsert(db, monkeypatch):
    """② **최종 재조회 직후·upsert 직전**의 정확한 TOCTOU 창.

    임계구역이 재조회와 쓰기를 함께 감싸므로, 그 사이에 들어오려는 poller는
    `shared_write_lock()`에서 대기했다가 recompute가 커밋한 뒤에 실행된다.
    따라서 최종 대표는 **나중에 끝난 쪽이 본 canonical 값**이 된다.
    """
    db(_seed("old", hearts=200, views=1, last_view_at=NOW - 9, owner="own-r2"))
    db(_seed("new", hearts=200, owner="own-r2"))
    db(_streamer("old", "own-r2"))
    started = {"task": None}
    orig = sc._build_reps

    def _hook(tagged, overrides=None):
        out = orig(tagged, overrides)
        # 재조회가 끝난 직후 = upsert 직전. 여기서 poller를 '출발'시킨다.
        # 이 시점 recompute는 이미 락을 쥐고 있으므로 poller는 대기해야 한다.
        if started["task"] is None:
            started["task"] = sc.asyncio.ensure_future(_poller_stores("new", 88000))
        return out

    monkeypatch.setattr(sc, "fetch_channel", lambda c, cid: _none())
    monkeypatch.setattr(sc, "_build_reps", _hook)

    async def _both():
        await sc.recompute_ranking(NOW)
        assert started["task"] is not None
        await started["task"]                # 락이 풀린 뒤 poller가 끝난다

    db(_both())
    assert db(_rep_of("own-r2"))["representative_clip_uid"] == db(_canonical_rep("own-r2"))
    assert db(_rep_of("own-r2"))["representative_clip_uid"] == "new"


async def _none():
    return {}


def test_poller_finishing_first_still_ends_canonical(db, monkeypatch):
    """③ 반대 순서 — poller가 먼저 끝나고 전체 recompute가 뒤에 끝난다."""
    db(_seed("old", hearts=200, views=1, last_view_at=NOW - 9, owner="own-r3"))
    db(_seed("new", hearts=200, owner="own-r3"))
    db(_streamer("old", "own-r3"))
    db(_poller_stores("new", 99000))         # poller가 먼저 완료
    monkeypatch.setattr(sc, "fetch_channel", lambda c, cid: _none())
    db(sc.recompute_ranking(NOW))            # 전체 recompute가 뒤에
    assert db(_rep_of("own-r3"))["representative_clip_uid"] == "new"
    assert db(_rep_of("own-r3"))["representative_clip_uid"] == db(_canonical_rep("own-r3"))


def test_critical_section_wait_keeps_the_response_under_the_timeout(db, monkeypatch):
    """⑥ 임계구역 대기 때문에 results 응답이 60초를 넘지 않는다.

    락 안에는 DB 작업만 있다 — 외부 API·backoff·sleep이 없으므로 최대 대기는
    상대 트랜잭션의 DB 시간뿐이다.
    """
    db(_seed("old", hearts=200, views=1, last_view_at=NOW - 9, owner="own-r4"))
    db(_seed("new", hearts=200, owner="own-r4"))
    db(_streamer("old", "own-r4"))
    monkeypatch.setattr(sc, "fetch_channel", lambda c, cid: _none())

    async def _race():
        t0 = time.perf_counter()
        await sc.asyncio.gather(sc.recompute_ranking(NOW),
                                _poller_stores("new", 12345))
        return time.perf_counter() - t0

    took = db(_race())
    assert took < 15.0, f"{took:.2f}초 — 임계구역 대기가 응답을 늘리면 안 된다"
    assert db(_rep_of("own-r4"))["representative_clip_uid"] == db(_canonical_rep("own-r4"))


def test_no_write_lock_is_held_across_the_external_gather(db, monkeypatch):
    """⑦ 외부 API 호출 중에는 쓰기 락도 write 트랜잭션도 잡지 않는다.

    gather 도중 다른 코루틴이 `db_write`를 정상적으로 획득할 수 있어야 한다.
    잡고 있었다면 아래 `_poller_stores`가 gather 안에서 영원히 대기한다.
    """
    db(_seed("old", hearts=200, views=1, last_view_at=NOW - 9, owner="own-r5"))
    db(_seed("new", hearts=200, owner="own-r5"))
    db(_streamer("old", "own-r5"))
    ok = {"acquired": False}

    async def _fetch(client, cid):
        if not ok["acquired"]:
            await _poller_stores("new", 4321)   # 락을 잡을 수 있어야 한다
            ok["acquired"] = True
        return {}

    monkeypatch.setattr(sc, "fetch_channel", _fetch)
    db(sc.recompute_ranking(NOW))
    assert ok["acquired"] is True


def test_override_survives_every_ordering(db, monkeypatch):
    """⑧ 어떤 순서에서도 유효 override가 유지된다."""
    db(_seed("auto", hearts=900, views=1, last_view_at=NOW - 9, owner="own-r6"))
    db(_seed("pick", hearts=1, owner="own-r6"))
    db(_streamer("pick", "own-r6"))
    db(_add_override("own-r6", "pick"))
    monkeypatch.setattr(sc, "fetch_channel", lambda c, cid: _none())

    async def _race():
        await sc.asyncio.gather(sc.recompute_ranking(NOW),
                                _poller_stores("pick", 777))

    db(_race())
    assert db(_rep_of("own-r6"))["representative_clip_uid"] == "pick"


def test_db_busy_repick_preserves_metrics_and_lease(db, slow_rank, monkeypatch):
    """⑨ 재선정 쓰기가 DB busy로 실패해도 지표 저장과 lease 계약은 그대로다."""
    db(_seed("m1", hearts=200, owner="own-r7"))
    db(_seed("m2", hearts=200, views=1, last_view_at=NOW - 9, owner="own-r7"))
    db(_streamer("m2", "own-r7"))

    async def _busy(get_db_, fn, *, what, **kw):
        if what == "krp_repick":
            return False                      # 쓰기 실패를 흉내낸다
        return await krp.db_write.__wrapped__(get_db_, fn, what=what, **kw) \
            if hasattr(krp.db_write, "__wrapped__") else True

    monkeypatch.setattr(krp, "db_write", _busy)
    changed = db(krp.repick_representatives({"own-r7"}, NOW))
    assert changed == 0
    row = db(_row("m1"))
    assert row["view_count"] == 0             # 지표는 건드리지 않았다
    assert db(_rep_of("own-r7"))["representative_clip_uid"] == "m2"


# ══════════════════════════════════════════════════════════════════════════
# 트랜잭션 종료 보장 — `shared_write_lock()`은 직렬화 장치이지 rollback 장치가 아니다
#
# `recompute_ranking()`은 `db_write()`를 지나지 않고 공유 연결에 직접 커밋한다.
# 그래서 DML 뒤 예외나 취소가 나면 미커밋 트랜잭션이 연결에 남고, 다음
# `db_write()`의 commit이 남의 부분 DML까지 함께 커밋하거나 그쪽 rollback이
# 이쪽 작업을 되돌린다. 아래 두 테스트가 그 경계를 고정한다.
# ══════════════════════════════════════════════════════════════════════════
def _setup_rollback_case(db_, owner, monkeypatch):
    db_(_seed("keepA", hearts=200, views=1, last_view_at=NOW - 9, owner=owner))
    db_(_seed("newB", hearts=200, owner=owner))
    db_(_streamer("keepA", owner))
    monkeypatch.setattr(sc, "fetch_channel", lambda c, cid: _none())


def test_exception_after_dml_rolls_back_and_keeps_the_old_representative(
        db, monkeypatch):
    """A. DML 이후 일반 예외 — 롤백되고 대표 A가 유지된다."""
    _setup_rollback_case(db, "own-x1", monkeypatch)

    async def _boom(ranked, now):
        raise RuntimeError("snapshot 폭발")

    monkeypatch.setattr(sc, "_save_snapshots", _boom)
    with pytest.raises(RuntimeError):
        db(sc.recompute_ranking(NOW, save_snapshot=True))

    assert db(_rep_of("own-x1"))["representative_clip_uid"] == "keepA"
    conn = db(database.get_db())
    assert conn.in_transaction is False
    # 후속 쓰기가 정상 동작하고, 실패한 recompute의 대표 B를 몰래 커밋하지 않는다
    db(_poller_stores("newB", 5555))
    assert db(_rep_of("own-x1"))["representative_clip_uid"] == "newB"
    assert db(_row("keepA"))["view_count"] == 1        # 되살아난 값이 없다


def test_cancellation_after_dml_rolls_back_and_propagates(db, monkeypatch):
    """B. DML 이후 CancelledError — 삼키지 않고 전파하며 롤백한다."""
    _setup_rollback_case(db, "own-x2", monkeypatch)

    async def _cancel(ranked, now):
        raise sc.asyncio.CancelledError()

    monkeypatch.setattr(sc, "_save_snapshots", _cancel)
    with pytest.raises(sc.asyncio.CancelledError):
        db(sc.recompute_ranking(NOW, save_snapshot=True))

    assert db(_rep_of("own-x2"))["representative_clip_uid"] == "keepA"
    conn = db(database.get_db())
    assert conn.in_transaction is False
    rows = db(_snapshot_count())
    assert rows == 0                                   # 스냅샷도 남지 않았다
    db(_poller_stores("newB", 6666))                   # 후속 쓰기 정상
    assert db(_rep_of("own-x2"))["representative_clip_uid"] == "newB"


async def _snapshot_count():
    conn = await database.get_db()
    r = await (await conn.execute(
        "SELECT COUNT(*) c FROM singcup_snapshots WHERE event_id=?", (EV,))).fetchone()
    return int(dict(r)["c"])


def test_successful_recompute_leaves_no_open_transaction(db, monkeypatch):
    """정상 경로도 트랜잭션을 열어 두지 않는다."""
    _setup_rollback_case(db, "own-x3", monkeypatch)
    db(sc.recompute_ranking(NOW))
    conn = db(database.get_db())
    assert conn.in_transaction is False
