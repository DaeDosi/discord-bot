"""AWS 서울 outbound poller — 후보 선정 · lease · 결과 검증 · 저장 계약.

배경(이 기능이 존재하는 이유):
하트는 카드 응답의 `interaction.emotion.reactions`에 있고 조회수는
`content.vod.count`에 있다. `krOnlyViewing=true` 클립을 Railway 해외 IP에서
요청하면 HTTP 200이면서도 **`content.vod` 블록 전체가 제거**된다. 하트 블록은
남으므로 하트만 갱신되고 조회수는 unknown으로 남는다. **조회수가 0으로 응답된
것이 아니라 컨테이너가 누락된 것**이라 `observed_zero`가 아니라 `unknown`이다.
한국(AWS 서울)에서 같은 API를 불러 `content.vod.count`를 받아야 복구된다.

여기서 고정하는 계약:
  ① 후보는 "하트는 오는데 조회수는 한 번도 못 받은" 클립뿐이다. `observed_zero`는
     정상 관측된 진짜 0이므로 **절대 후보가 아니다**.
  ② lease는 원자적이고 중복 발급되지 않으며, 만료되면 reaper 없이 재수령된다.
  ③ 결과는 발급된 task와 일치할 때만, 값이 유효할 때만, 더 최신일 때만 저장된다.
  ④ 저장은 기존 `_apply_metrics` 하나만 쓴다(새 UPDATE 경로 없음).
  ⑤ `recompute_ranking`과 캐시 무효화는 **batch당 정확히 1회**, 저장 0건이면 0회.
"""
import time

import pytest
import singcup_clips as sc
import singcup_kr_poller as krp

import database

EV = sc.EVENT_ID
NOW = int(time.time())


# ── seed 도우미 ────────────────────────────────────────────────────────────
async def _seed(uid, *, hearts=141, views=0, last_heart_at=None, last_view_at=0,
                last_attempt_at=None, active=1, deletion="active",
                video_id="vid-1", rec_id="{}", owner="own0"):
    conn = await database.get_db()
    lh = NOW - 60 if last_heart_at is None else last_heart_at
    la = NOW - 60 if last_attempt_at is None else last_attempt_at
    await conn.execute(
        "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
        " rec_id, clip_title, thumbnail_image_url, description, created_at,"
        " heart_count, view_count, duration, adult, blind_type, metrics_ok, active,"
        " deletion_state, missing_scan_count, first_collected_at, last_collected_at,"
        " row_updated_at, last_metrics_at, last_attempt_at, last_heart_at, last_view_at)"
        " VALUES (?,?,?,?,?,?,'','#싱드컵',?,?,?,60,0,'ABROAD',0,?,?,0,?,?,?,0,?,?,?)",
        (uid, EV, owner, video_id, rec_id, "[싱드컵] t", NOW - 9999, hearts, views,
         active, deletion, NOW, NOW, NOW, la, lh, last_view_at))
    await conn.commit()


async def _row(uid):
    conn = await database.get_db()
    r = await (await conn.execute(
        "SELECT * FROM singcup_clips WHERE clip_uid=?", (uid,))).fetchone()
    return dict(r) if r else None


async def _leases():
    conn = await database.get_db()
    return [dict(r) for r in await (await conn.execute(
        "SELECT * FROM singcup_kr_poller_lease")).fetchall()]


@pytest.fixture(autouse=True)
def _no_recompute(db, monkeypatch):
    """`recompute_ranking`은 외부 채널 API를 부른다 — 호출 횟수만 센다.

    `db`를 먼저 요청하는 이유: 그 픽스처의 setup이 `clips_reset()`을 부르고 거기서
    캐시를 무효화한다. 패치가 먼저 걸리면 그 한 번이 카운터에 섞인다.
    """
    calls = {"rank": 0, "cache": 0}

    async def _rank(now, **kw):
        calls["rank"] += 1

    monkeypatch.setattr(sc, "recompute_ranking", _rank)
    monkeypatch.setattr(sc, "invalidate_main_cache",
                        lambda: calls.__setitem__("cache", calls["cache"] + 1))
    krp._TEST_CALLS = calls
    return calls


_UNSET = object()


def _ok(uid, *, task, views=1927, hearts=146, observed_at=None, status=200,
        view_state="observed", token=_UNSET):
    out = {"taskId": task["taskId"], "clipUid": uid,
           "observedAt": NOW if observed_at is None else observed_at,
           "httpStatus": status, "viewState": view_state,
           "viewCount": views, "heartCount": hearts, "attempts": 1}
    # leaseToken은 "이 task를 실제로 발급받은 쪽인가"를 묻는다. 기본은 정상 토큰.
    if token is _UNSET:
        out["leaseToken"] = task["leaseToken"]
    elif token is not None:
        out["leaseToken"] = token
    return out


# ── 8·9·10·11 후보 선정 ────────────────────────────────────────────────────
def test_unknown_view_with_healthy_heart_is_a_candidate(db):
    db(_seed("c-unknown"))
    tasks = db(krp.lease_tasks(NOW, 25))
    assert [t["clipUid"] for t in tasks] == ["c-unknown"]


def test_observed_zero_is_never_a_candidate(db):
    """`last_view_at>0 AND view_count=0`은 **정상 관측된 진짜 0**이다."""
    db(_seed("c-obszero", views=0, last_view_at=NOW - 300))
    assert db(krp.lease_tasks(NOW, 25)) == []


def test_observed_positive_is_excluded(db):
    db(_seed("c-observed", views=500, last_view_at=NOW - 300))
    assert db(krp.lease_tasks(NOW, 25)) == []


def test_clip_without_heart_is_excluded(db):
    """하트도 못 받았다면 지역 차단이 아니라 그냥 조회 실패다."""
    db(_seed("c-noheart", hearts=0, last_heart_at=0))
    assert db(krp.lease_tasks(NOW, 25)) == []


def test_never_attempted_clip_is_excluded(db):
    db(_seed("c-fresh", last_attempt_at=0, last_heart_at=0, hearts=0))
    assert db(krp.lease_tasks(NOW, 25)) == []


def test_inactive_and_deleted_clips_are_excluded(db):
    db(_seed("c-inactive", active=0))
    db(_seed("c-deleted", deletion="confirmed_deleted"))
    assert db(krp.lease_tasks(NOW, 25)) == []


def test_candidate_carries_video_id_and_rec_id_from_db(db):
    """Railway는 task를 만들려고 치지직을 부르지 않는다 — DB 값만 쓴다."""
    db(_seed("c-1", video_id="VID-ABC", rec_id='{"seedClipUID":"c-1"}'))
    t = db(krp.lease_tasks(NOW, 25))[0]
    assert t["videoId"] == "VID-ABC"
    assert t["recId"] == '{"seedClipUID":"c-1"}'
    assert t["refererUid"] == "c-1"


def test_task_payload_has_no_owner_or_internal_fields(db):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    assert set(t) == {"taskId", "leaseToken", "clipUid", "videoId", "recId",
                      "refererUid", "expiresAt"}


def test_batch_is_capped(db):
    for i in range(40):
        db(_seed(f"c-{i:02d}"))
    assert len(db(krp.lease_tasks(NOW, 999))) <= krp.BATCH_MAX


# ── 10 정상 조회수 확보 후 후보에서 빠진다 ─────────────────────────────────
def test_clip_leaves_candidate_pool_after_view_is_stored(db):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t)], NOW))
    assert db(_row("c-1"))["view_count"] == 1927
    assert db(krp.lease_tasks(NOW + 1, 25)) == []


# ── 12·13 lease 원자성·중복 방지·만료 재수령 ───────────────────────────────
def test_active_lease_prevents_duplicate_task(db):
    db(_seed("c-1"))
    assert len(db(krp.lease_tasks(NOW, 25))) == 1
    assert db(krp.lease_tasks(NOW, 25)) == []


def test_expired_lease_is_reissued_without_a_reaper(db):
    db(_seed("c-1"))
    db(krp.lease_tasks(NOW, 25))
    later = NOW + krp.LEASE_SECONDS + 1
    assert [t["clipUid"] for t in db(krp.lease_tasks(later, 25))] == ["c-1"]


def test_lease_rows_are_unique_per_task(db):
    db(_seed("c-1"))
    db(_seed("c-2"))
    tasks = db(krp.lease_tasks(NOW, 25))
    ids = {t["taskId"] for t in tasks}
    tokens = {t["leaseToken"] for t in tasks}
    assert len(ids) == 2 and len(tokens) == 2


def test_concurrent_lease_calls_do_not_double_issue(db):
    """SELECT-then-INSERT 경쟁이 나면 같은 클립에 task가 둘 생긴다."""
    import asyncio
    db(_seed("c-1"))

    async def both():
        return await asyncio.gather(krp.lease_tasks(NOW, 25),
                                    krp.lease_tasks(NOW, 25))

    a, b = db(both())
    assert len(a) + len(b) == 1
    assert len(db(_leases())) == 1


# ── 14·15·16 task 일치 ─────────────────────────────────────────────────────
def test_result_for_unissued_clip_is_rejected(db):
    db(_seed("c-1"))
    db(_seed("c-2"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    out = db(krp.apply_results([_ok("c-2", task=t)], NOW))
    assert out["stored"] == 0
    assert out["rejected"][0]["reason"] == "clip_mismatch"


def test_unknown_task_id_is_rejected(db):
    db(_seed("c-1"))
    out = db(krp.apply_results(
        [{"taskId": "f" * 32, "clipUid": "c-1", "observedAt": NOW,
          "httpStatus": 200, "viewState": "observed", "viewCount": 5}], NOW))
    assert out["stored"] == 0 and out["rejected"][0]["reason"] == "unknown_task"


def test_expired_lease_result_is_rejected(db):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    late = NOW + krp.LEASE_SECONDS + 5
    out = db(krp.apply_results([_ok("c-1", task=t, observed_at=late)], late))
    assert out["stored"] == 0 and out["rejected"][0]["reason"] == "lease_expired"
    assert db(_row("c-1"))["view_count"] == 0


# ── 17 값 검증 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [-1, True, False, 12.5, float("nan"),
                                 float("inf"), "abc", None, [], {},
                                 2 ** 53 + 1])
def test_invalid_view_values_are_rejected(db, bad):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    out = db(krp.apply_results([_ok("c-1", task=t, views=bad)], NOW))
    assert out["stored"] == 0
    assert db(_row("c-1"))["view_count"] == 0
    assert db(_row("c-1"))["last_view_at"] == 0


def test_integer_string_is_accepted_like_the_collector(db):
    """치지직이 숫자를 문자열로 주는 회차가 있다 — 기존 valid_count 계약과 같다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, views="345")], NOW))
    assert db(_row("c-1"))["view_count"] == 345


# ── 18 idempotent ──────────────────────────────────────────────────────────
def test_duplicate_result_is_idempotent_no_op(db):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t)], NOW))
    before = db(_row("c-1"))
    out = db(krp.apply_results([_ok("c-1", task=t, views=9999)], NOW + 1))
    assert out["stored"] == 0
    assert out["accepted"] == 1                 # 거부가 아니라 no-op
    assert db(_row("c-1"))["view_count"] == before["view_count"]


# ── 19·20·21 freshness · 단조성 ────────────────────────────────────────────
def test_stale_observation_is_rejected(db):
    db(_seed("c-1", views=100, last_view_at=NOW))
    # 이미 observed라 후보가 아니므로 lease를 직접 만든다
    t = db(krp._issue_lease_for_test("c-1", NOW))
    out = db(krp.apply_results([_ok("c-1", task=t, views=200,
                                    observed_at=NOW - 10)], NOW))
    assert out["stored"] == 0 and out["rejected"][0]["reason"] == "stale_observation"
    assert db(_row("c-1"))["view_count"] == 100


def test_decrease_is_rejected_by_default(db):
    db(_seed("c-1", views=500, last_view_at=NOW - 100))
    t = db(krp._issue_lease_for_test("c-1", NOW))
    out = db(krp.apply_results([_ok("c-1", task=t, views=400)], NOW))
    assert out["stored"] == 0 and out["rejected"][0]["reason"] == "decrease"
    assert db(_row("c-1"))["view_count"] == 500


def test_unknown_zero_to_positive_is_allowed(db):
    """0 → 1927은 감소가 아니라 **최초 관측**이다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    out = db(krp.apply_results([_ok("c-1", task=t, views=1927)], NOW))
    assert out["stored"] == 1
    row = db(_row("c-1"))
    assert row["view_count"] == 1927 and row["last_view_at"] > 0
    assert sc.view_state(row) == "observed"


# ── 22 누락 값을 0으로 저장하지 않는다 ─────────────────────────────────────
@pytest.mark.parametrize("payload", [
    {"viewState": "no_vod", "viewCount": None},
    {"viewState": "partial", "viewCount": None},
    {"httpStatus": 500, "viewCount": None},
])
def test_missing_view_is_never_stored_as_zero(db, payload):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    item = _ok("c-1", task=t)
    item.update(payload)
    out = db(krp.apply_results([item], NOW))
    row = db(_row("c-1"))
    assert out["stored"] == 0
    assert row["view_count"] == 0 and row["last_view_at"] == 0
    assert sc.view_state(row) == "unknown"      # observed_zero로 굳지 않는다


# ── heart는 교차검증용이며 덮어쓰지 않는다 ─────────────────────────────────
def test_heart_is_not_overwritten(db):
    db(_seed("c-1", hearts=141))
    t = db(krp.lease_tasks(NOW, 25))[0]
    before = db(_row("c-1"))["last_heart_at"]
    db(krp.apply_results([_ok("c-1", task=t, hearts=999)], NOW))
    row = db(_row("c-1"))
    assert row["heart_count"] == 141
    assert row["last_heart_at"] == before


# ── 23·24·25 batch당 recompute/cache 횟수 ──────────────────────────────────
def test_batch_invalidates_cache_once_and_never_recomputes(db, _no_recompute):
    """**응답 경로에서 순위를 재계산하지 않는다.** 캐시 무효화만 batch당 1회다.

    예전에는 여기서 `recompute_ranking()`을 불렀고, 그것이 참가자 전원(약
    1,400명)의 채널 API를 `CARD_CONCURRENCY`로 훑어 응답이 169.692초까지
    늘어났다(실측 2026-08-04). 한국 poller는 60초에 끊겨 결과가 불명확한
    성공이 됐다 — 저장은 이미 1초 만에 끝난 뒤였는데도 그 회차가 헛돌았다.
    캐시만 버리면 `_load_main_uncached()`가 조회 시점에 점수를 다시 만든다.
    """
    for i in range(3):
        db(_seed(f"c-{i}"))
    tasks = db(krp.lease_tasks(NOW, 25))
    items = [_ok(t["clipUid"], task=t, views=100 + i)
             for i, t in enumerate(tasks)]
    out = db(krp.apply_results(items, NOW))
    assert out["stored"] == 3
    assert _no_recompute["rank"] == 0          # 재계산은 주기 경로가 맡는다
    assert _no_recompute["cache"] == 1
    assert out["recomputed"] is False          # 계약은 남기되 항상 False


def test_no_store_means_no_recompute_and_no_cache_invalidation(db, _no_recompute):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, views=-5)], NOW))
    assert _no_recompute["rank"] == 0
    assert _no_recompute["cache"] == 0


def test_empty_batch_touches_nothing(db, _no_recompute):
    out = db(krp.apply_results([], NOW))
    assert out["stored"] == 0
    assert _no_recompute["rank"] == 0 and _no_recompute["cache"] == 0


# ── 26 lock 격리 · 부분 실패 ───────────────────────────────────────────────
def test_locked_clip_is_left_retryable_not_failed(db, monkeypatch):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]

    async def _no_lock(uid):
        return krp.CLIP_HELD, None

    monkeypatch.setattr(krp, "clip_lock_acquire", _no_lock)
    out = db(krp.apply_results([_ok("c-1", task=t)], NOW))
    assert out["stored"] == 0
    assert out["rejected"][0]["reason"] == "locked"
    rows = db(_leases())
    assert rows[0]["done_at"] == 0              # 재시도 가능 상태로 남는다


def test_one_failure_does_not_roll_back_other_successes(db):
    db(_seed("c-1"))
    db(_seed("c-2"))
    tasks = {t["clipUid"]: t for t in db(krp.lease_tasks(NOW, 25))}
    out = db(krp.apply_results([
        _ok("c-1", task=tasks["c-1"], views=111),
        _ok("c-2", task=tasks["c-2"], views=-1),      # 거부
    ], NOW))
    assert out["stored"] == 1
    assert db(_row("c-1"))["view_count"] == 111
    assert db(_row("c-2"))["view_count"] == 0


def test_clip_lock_is_released_even_on_failure(db):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, views=-1)], NOW))
    token = db(sc.acquire_clip_lock("c-1", wait=0))
    assert token is not None
    db(sc.release_clip_lock("c-1", token))


# ── 27 취소 전파 ───────────────────────────────────────────────────────────
def test_cancellation_propagates_and_releases_lock(db, monkeypatch):
    import asyncio
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]

    async def _boom(*a, **kw):
        raise asyncio.CancelledError()

    monkeypatch.setattr(sc, "_apply_metrics", _boom)
    with pytest.raises(asyncio.CancelledError):
        db(krp.apply_results([_ok("c-1", task=t)], NOW))
    token = db(sc.acquire_clip_lock("c-1", wait=0))
    assert token is not None                   # 락이 새지 않았다


# ── 저장은 기존 _apply_metrics 하나만 쓴다 ─────────────────────────────────
def test_storage_goes_through_apply_metrics_only(db, monkeypatch):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    seen = {}

    orig = sc._apply_metrics

    async def _spy(uid, heart, view, heart_ok, view_ok, now, **kw):
        seen.update(uid=uid, heart_ok=heart_ok, view_ok=view_ok, view=view)
        return await orig(uid, heart, view, heart_ok, view_ok, now, **kw)

    monkeypatch.setattr(sc, "_apply_metrics", _spy)
    db(krp.apply_results([_ok("c-1", task=t)], NOW))
    assert seen == {"uid": "c-1", "heart_ok": False, "view_ok": True, "view": 1927}


def test_only_the_representative_column_is_written(db):
    """대표 컬럼은 쓰되 **무관한 집계 컬럼은 건드리지 않는다.**

    예전에는 poller가 `representative_clip_uid`를 아예 쓰지 않는 것이 계약이었고
    대표 재선정을 전적으로 `recompute_ranking()`에 맡겼다. 그런데 그 전체 재계산은
    주기 경로가 전부 조건부라(discover는 `if tagged`, hourly snapshot은 '5단계
    전부 성공') 무조건 도는 것이 스윕 회차(80~100분)뿐이었다. 그동안 `/main`과
    스윕 `is_rep`가 다른 대표를 볼 수 있어, 저장된 owner에 한해 대표만 즉시
    다시 고른다. 팔로워·닉네임·태그 수는 이 경로가 최신본을 갖고 있지 않으므로
    **절대 쓰지 않는다** — 그것들은 정기 경로 몫이다.
    """
    text = open(krp.__file__, encoding="utf-8").read()
    sql = text[text.find("UPDATE singcup_streamers"):][:200]
    assert "representative_clip_uid=?" in sql
    for banned in ("follower_count", "channel_name", "channel_image_url",
                   "tagged_clip_count", "verified_mark"):
        assert banned not in sql, f"{banned}을 덮어쓰면 안 된다"
    # 선정 규칙을 복제하지 않고 canonical 함수를 재사용한다
    assert "sc._build_reps" in text and "sc._representative_overrides" in text
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t)], NOW))


# ── 28 기존 sweep 불변식 무변경 ────────────────────────────────────────────
def test_sweep_module_is_untouched_by_this_feature():
    import singcup_sweep as sw
    text = open(sw.__file__, encoding="utf-8").read()
    assert "kr_poller" not in text
    assert "krp" not in text


# ── A. leaseToken 결과 binding ─────────────────────────────────────────────
# 서명은 **요청** 단위 인증이다. 서명 키를 가진 쪽이 자기가 발급받지 않은 taskId에
# 결과를 밀어 넣는 것까지는 막지 못한다. leaseToken이 그 한 겹을 채운다.
def test_valid_lease_token_is_required_and_accepted(db):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    out = db(krp.apply_results([_ok("c-1", task=t)], NOW))
    assert out["stored"] == 1


@pytest.mark.parametrize("token", [None, "", "0" * 32, "짧음"])
def test_missing_or_wrong_lease_token_is_rejected(db, token):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    item = _ok("c-1", task=t, token=token)
    if token is None:
        item.pop("leaseToken", None)
    out = db(krp.apply_results([item], NOW))
    assert out["stored"] == 0
    assert out["accepted"] == 0
    assert out["rejected"][0]["reason"] in ("bad_lease_token", "malformed_item")
    assert db(_row("c-1"))["view_count"] == 0


def test_wrong_lease_token_does_not_consume_the_lease(db):
    """잘못된 token으로 남의 lease를 소진시킬 수 있으면 그것이 곧 공격이다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t, token="0" * 32)], NOW))
    row = db(_leases())[0]
    assert row["done_at"] == 0          # 종료되지 않았다
    assert row["attempts"] == 0         # 시도 횟수도 오르지 않았다
    # 올바른 token으로는 여전히 반영된다
    assert db(krp.apply_results([_ok("c-1", task=t)], NOW))["stored"] == 1


def test_lease_token_of_another_task_is_rejected(db):
    db(_seed("c-1"))
    db(_seed("c-2"))
    tasks = {t["clipUid"]: t for t in db(krp.lease_tasks(NOW, 25))}
    item = _ok("c-1", task=tasks["c-1"], token=tasks["c-2"]["leaseToken"])
    out = db(krp.apply_results([item], NOW))
    assert out["stored"] == 0 and out["rejected"][0]["reason"] == "bad_lease_token"


def test_completed_task_resubmit_needs_a_valid_token(db):
    """완료 task 재전송은 **유효 token일 때만** idempotent no-op이다."""
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t)], NOW))

    good = db(krp.apply_results([_ok("c-1", task=t)], NOW + 1))
    assert good["accepted"] == 1 and good["stored"] == 0

    bad = db(krp.apply_results([_ok("c-1", task=t, token="0" * 32)], NOW + 2))
    assert bad["accepted"] == 0                    # accepted로 세지 않는다
    assert bad["rejected"][0]["reason"] == "bad_lease_token"


def test_lease_token_never_appears_in_response_or_log(db, capsys):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    out = db(krp.apply_results([_ok("c-1", task=t, token="0" * 32)], NOW))
    assert t["leaseToken"] not in repr(out)
    assert t["leaseToken"] not in capsys.readouterr().out


# ── E. malformed payload가 500을 만들지 않는다 ─────────────────────────────
@pytest.mark.parametrize("bad", [True, False, "200", 200.0, -1, 10 ** 9, None])
def test_malformed_http_status_is_rejected_not_raised(db, bad):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    out = db(krp.apply_results([_ok("c-1", task=t, status=bad)], NOW))
    assert out["stored"] == 0 and out["rejected"][0]["reason"] == "no_view"


@pytest.mark.parametrize("field", ["taskId", "clipUid", "leaseToken"])
@pytest.mark.parametrize("bad", [123, True, None, [], {}, "x" * 65])
def test_malformed_identifiers_are_rejected_not_raised(db, field, bad):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    item = _ok("c-1", task=t)
    item[field] = bad
    out = db(krp.apply_results([item], NOW))
    assert out["stored"] == 0
    assert db(_row("c-1"))["view_count"] == 0


def test_non_dict_items_are_rejected(db):
    out = db(krp.apply_results(["x", 1, None, []], NOW))
    assert out["stored"] == 0
    assert [r["reason"] for r in out["rejected"]] == ["malformed_item"] * 4


# ── safe_int 계약 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [True, False, "5", 5.0, None, [], {},
                                 float("nan"), float("inf")])
def test_safe_int_rejects_non_integers(bad):
    assert krp.safe_int(bad, lo=0, hi=10) is None


@pytest.mark.parametrize("value,ok", [(0, True), (10, True), (-1, False),
                                      (11, False), (5, True)])
def test_safe_int_enforces_range(value, ok):
    assert (krp.safe_int(value, lo=0, hi=10) is not None) is ok


# ── lease 이력 prune ───────────────────────────────────────────────────────
def test_prune_removes_only_old_closed_leases(db, monkeypatch):
    """열린 lease는 절대 지우지 않는다 — 재전송·idempotency 계약이 깨진다."""
    import database
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]          # 열린 lease 1건
    db(krp.apply_results([_ok("c-1", task=t)], NOW))   # 닫힘(ok)

    async def _age(task_id, done_at):
        conn = await database.get_db()
        await conn.execute(
            "UPDATE singcup_kr_poller_lease SET done_at=? WHERE task_id=?",
            (done_at, task_id))
        await conn.commit()

    old = NOW - (krp.LEASE_RETENTION_DAYS + 1) * 86400
    db(_age(t["taskId"], old))
    db(_seed("c-2"))
    open_task = db(krp.lease_tasks(NOW, 25))[0]  # prune과 같은 트랜잭션에서 발급

    krp._last_prune_at = 0
    db(krp.lease_tasks(NOW + krp.PRUNE_INTERVAL_SECONDS + 1, 25))
    rows = {r["task_id"] for r in db(_leases())}
    assert t["taskId"] not in rows               # 오래된 닫힌 행은 지워졌다
    assert open_task["taskId"] in rows           # 열린 행은 남았다


def test_prune_keeps_recent_closed_leases(db):
    db(_seed("c-1"))
    t = db(krp.lease_tasks(NOW, 25))[0]
    db(krp.apply_results([_ok("c-1", task=t)], NOW))
    krp._last_prune_at = 0
    db(krp.lease_tasks(NOW + krp.PRUNE_INTERVAL_SECONDS + 1, 25))
    assert t["taskId"] in {r["task_id"] for r in db(_leases())}
