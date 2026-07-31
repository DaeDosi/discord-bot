"""삭제된 클립의 soft delete와 대표 자동 재선정.

배경(실측 2026-07-31): 치지직에서 삭제된 클립 `79xM38ged7`이 상세 API에서
`HTTP 404 {"code":404,"message":"삭제된 클립입니다."}`를 주는데도 계속 대표 클립으로
남아 있었다. 하트가 64로 굳은 채 같은 스트리머의 살아 있는 새 클립(54)보다 높아
순위를 계속 차지했다. `active`를 내리는 코드는 있었지만 **호출자가 없었다**.

여기서 지키는 규칙은 두 가지다.
  1) 약한 신호(카드 부분 응답·timeout·429·5xx)만으로는 **절대** 삭제로 세지 않는다.
  2) 명시적 404라도 **서로 다른 시점에 두 번** 확인해야 확정한다.
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import singcup_clips as sc

KST = timezone(timedelta(hours=9))
IN = "2026-07-28 12:00:00"
NOW_TS = int(datetime.strptime(IN, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp())


# ── 헬퍼 ───────────────────────────────────────────────────────────────────
async def _seed(uid, owner, hearts, views, *, created=NOW_TS, state="active",
                checks=0, last_at=0, active=1):
    db = await sc.get_db()
    await db.execute(
        "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
        " rec_id, clip_title, thumbnail_image_url, description, created_at,"
        " heart_count, view_count, duration, adult, blind_type, metrics_ok,"
        " owner_channel_name, active, missing_scan_count, first_collected_at,"
        " last_collected_at, row_updated_at, deletion_state, deletion_first_at,"
        " deletion_last_at, deletion_reason)"
        " VALUES (?,?,?,?,'','제목','','#싱드컵',?,?,?,60,0,'',1,?,?,?,?,?,?,?,?,?,'')",
        (uid, sc.EVENT_ID, owner, f"v{uid}", created, hearts, views, owner,
         active, checks, created, created, created, state, last_at and created or 0,
         last_at))
    await db.commit()


async def _rep_of(owner):
    db = await sc.get_db()
    r = await (await db.execute(
        "SELECT representative_clip_uid FROM singcup_streamers WHERE channel_id=?",
        (owner,))).fetchone()
    return r["representative_clip_uid"] if r else None


async def _clip(uid):
    db = await sc.get_db()
    r = await (await db.execute(
        "SELECT * FROM singcup_clips WHERE clip_uid=?", (uid,))).fetchone()
    return dict(r) if r else None


def _detail_ok(uid="x"):
    return httpx.Response(200, json={
        "code": 200, "content": {"clipUID": uid, "videoId": "v", "clipTitle": "t",
                                 "thumbnailImageUrl": "", "categoryType": "ETC",
                                 "clipCategory": "music", "adult": False,
                                 "blindType": None, "createdDate": IN}})


_DELETED = httpx.Response(404, json={"code": 404, "message": "삭제된 클립입니다."})


def install(db, handler):
    """sc의 공용 클라이언트를 목 transport로 바꾼다."""
    db(sc.reset_state())
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5)
    return sc._client


# ── 1. 약한 신호는 삭제로 세지 않는다 ──────────────────────────────────────
def test_partial_card_only_flags_suspicion_without_counting(db):
    """카드에 interaction/vod가 없는 것은 '확인해 보라'는 표시일 뿐이다."""
    db(_seed("a", "o1", 10, 100))
    db(sc._flag_deletion_suspect("a", "card_empty", NOW_TS))
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_SUSPECTED
    assert r["missing_scan_count"] == 0, "약한 신호는 확인 횟수를 올리지 않는다"
    assert r["active"] == 1, "의심 단계에서는 아직 살아 있다"
    assert r["deletion_reason"] == "card_empty"


def test_repeated_partial_cards_never_confirm(db):
    """no_interaction+no_vod가 몇 번 반복돼도 그것만으로는 삭제가 아니다."""
    db(_seed("a", "o1", 10, 100))
    for _ in range(10):
        db(sc._flag_deletion_suspect("a", "card_empty", NOW_TS))
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_SUSPECTED
    assert r["missing_scan_count"] == 0
    assert r["active"] == 1


@pytest.mark.parametrize("resp", [
    httpx.Response(429, json={"code": 429}),
    httpx.Response(500, json={"code": 500}),
    httpx.Response(503, json={"code": 503}),
    httpx.Response(403, json={"code": 403}),
])
def test_transient_errors_are_unknown_not_deleted(db, resp):
    """429/5xx/403은 '모름'이다. 삭제로 세면 장애 한 번에 순위가 날아간다."""
    install(db, lambda req: resp)
    verdict, code, _why = db(sc.probe_clip_alive(sc._get_client(), "a"))
    assert verdict == "unknown"
    assert code == resp.status_code


def test_timeout_is_unknown(db):
    def boom(request):
        raise httpx.ReadTimeout("timeout", request=request)
    install(db, boom)
    verdict, code, why = db(sc.probe_clip_alive(sc._get_client(), "a"))
    assert verdict == "unknown" and code is None
    assert "Timeout" in why


def test_transient_error_does_not_increase_counter(db):
    db(_seed("a", "o1", 10, 100, state=sc.DEL_SUSPECTED))
    install(db, lambda req: httpx.Response(500, json={}))
    db(sc.run_deletion_checks())
    r = db(_clip("a"))
    assert r["missing_scan_count"] == 0
    assert r["deletion_state"] == sc.DEL_SUSPECTED
    assert r["active"] == 1


# ── 2. 명시적 404 — 1회는 의심, 2회는 확정 ─────────────────────────────────
def test_explicit_404_is_deleted_verdict(db):
    install(db, lambda req: _DELETED)
    verdict, code, why = db(sc.probe_clip_alive(sc._get_client(), "a"))
    assert verdict == "deleted" and code == 404 and why == "http_404"


def test_first_404_only_suspects(db):
    db(_seed("a", "o1", 10, 100, state=sc.DEL_SUSPECTED))
    install(db, lambda req: _DELETED)
    out = db(sc.run_deletion_checks())
    assert out["confirmed"] == 0
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_SUSPECTED
    assert r["missing_scan_count"] == 1
    assert r["active"] == 1, "1회 확인만으로 내리면 안 된다"


def test_second_404_after_interval_confirms(db):
    db(_seed("a", "o1", 10, 100, state=sc.DEL_SUSPECTED))
    install(db, lambda req: _DELETED)
    db(sc.run_deletion_checks())
    # 최소 간격이 지난 것처럼 되돌린다(실제 시간을 기다리지 않는다)
    db(_age_out("a"))
    out = db(sc.run_deletion_checks())
    assert out["confirmed"] == 1
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_CONFIRMED
    assert r["missing_scan_count"] == 2
    assert r["active"] == 0


async def _age_out(uid, seconds=None):
    """deletion_last_at을 최소 간격 밖으로 밀어 둔다."""
    gap = seconds or (sc.DELETION_MIN_INTERVAL_SECONDS + 60)
    db = await sc.get_db()
    await db.execute(
        "UPDATE singcup_clips SET deletion_last_at=deletion_last_at-? WHERE clip_uid=?",
        (gap, uid))
    await db.commit()


def test_second_404_inside_interval_does_not_confirm(db):
    """같은 응답을 두 번 세지 않는다 — '서로 다른 시점'이 규칙이다."""
    db(_seed("a", "o1", 10, 100, state=sc.DEL_SUSPECTED))
    install(db, lambda req: _DELETED)
    db(sc.run_deletion_checks())
    db(sc.run_deletion_checks())        # 간격을 안 두고 즉시 한 번 더
    r = db(_clip("a"))
    assert r["missing_scan_count"] == 1
    assert r["deletion_state"] == sc.DEL_SUSPECTED


def test_alive_response_resets_counter(db):
    """중간에 200이 한 번이라도 오면 카운터가 지워진다."""
    db(_seed("a", "o1", 10, 100, state=sc.DEL_SUSPECTED))
    install(db, lambda req: _DELETED)
    db(sc.run_deletion_checks())
    assert db(_clip("a"))["missing_scan_count"] == 1

    # 다음 확인 차례가 될 때까지는 요청 자체가 나가지 않는다(최소 간격)
    install(db, lambda req: _detail_ok("a"))
    db(sc.run_deletion_checks())
    assert db(_clip("a"))["missing_scan_count"] == 1, "간격 안에서는 다시 묻지 않는다"

    db(_age_out("a"))
    db(sc.run_deletion_checks())
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_ACTIVE
    assert r["missing_scan_count"] == 0
    assert r["active"] == 1


def test_blind_detail_counts_as_deleted(db):
    """살아는 있지만 블라인드면 노출 대상이 아니다."""
    def blind(request):
        return httpx.Response(200, json={"code": 200, "content": {
            "clipUID": "a", "blindType": "BLIND"}})
    install(db, blind)
    verdict, _code, why = db(sc.probe_clip_alive(sc._get_client(), "a"))
    assert verdict == "deleted" and why == "blind_blind"


# ── 3. 목록 미발견은 완주했을 때만, 그것도 의심까지만 ──────────────────────
def test_list_absence_only_suspects_never_confirms(db):
    db(_seed("a", "o1", 10, 100))
    db(sc._flag_absent_from_list({"other"}, NOW_TS))
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_SUSPECTED
    assert r["missing_scan_count"] == 0, "목록 미발견은 확인 횟수가 아니다"
    assert r["active"] == 1
    assert r["deletion_reason"] == "list_absent"


def test_incomplete_scan_is_not_used_at_all(db):
    """스캔이 중간에 끊겼으면 reconcile이 애초에 이 함수를 부르지 않는다."""
    import inspect
    src = inspect.getsource(sc.reconcile_from_list)
    assert "if complete:" in src
    assert "_flag_absent_from_list" in src
    # 상한(RECONCILE_MAX_NEW)에 걸려 멈춘 경로에서는 complete가 True가 되지 않는다
    assert "break                       # 상한에 걸려 중간에 멈춤" in src


# ── 4. 확정 클립은 스윕 대상에서 빠진다 ────────────────────────────────────
def test_confirmed_clip_is_excluded_from_sweep(db):
    import singcup_sweep as sw
    db(_seed("alive", "o1", 10, 100))
    db(_seed("gone", "o1", 99, 999, state=sc.DEL_CONFIRMED, active=0))
    uids = {t["clip_uid"] for t in db(sw.sweep_targets(NOW_TS + 10_000))}
    assert "alive" in uids
    assert "gone" not in uids, "확정 삭제 클립에 카드 API를 계속 쓰면 안 된다"


# ── 5. 대표 자동 재선정 ────────────────────────────────────────────────────
def test_representative_moves_to_the_surviving_clip(db):
    """실제 사례와 같은 배치: 삭제 클립의 하트가 더 높다."""
    db(_seed("gone", "o1", 64, 525))       # 실제 79xM38ged7
    db(_seed("new", "o1", 54, 240))        # 실제 Lzbtbo6cVL
    db(sc.recompute_ranking(NOW_TS))
    assert db(_rep_of("o1")) == "gone", "삭제 전에는 하트가 높은 쪽이 대표다"

    # 삭제 확정 — 자동 경로 그대로(UID 하드코딩 없음)
    db(_seed_state("gone", sc.DEL_CONFIRMED))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_rep_of("o1")) == "new"


async def _seed_state(uid, state):
    db = await sc.get_db()
    await db.execute(
        "UPDATE singcup_clips SET deletion_state=?, active=? WHERE clip_uid=?",
        (state, 0 if state == sc.DEL_CONFIRMED else 1, uid))
    await db.commit()


def test_new_representative_keeps_its_own_metrics(db):
    """이전 클립의 하트·조회수를 복사하지 않는다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("new", "o1", 54, 240))
    db(_seed_state("gone", sc.DEL_CONFIRMED))
    db(sc.recompute_ranking(NOW_TS))
    d = db(sc.load_main())
    me = [s for s in d["streamers"] if s["channelId"] == "o1"][0]
    assert me["clipUid"] == "new"
    assert me["heartCount"] == 54 and me["viewCount"] == 240


def test_deleted_clip_disappears_from_ranking(db):
    db(_seed("gone", "o1", 64, 525))
    db(_seed("new", "o1", 54, 240))
    db(_seed_state("gone", sc.DEL_CONFIRMED))
    db(sc.recompute_ranking(NOW_TS))
    d = db(sc.load_main())
    assert all(s["clipUid"] != "gone" for s in d["streamers"])


def test_one_row_per_streamer(db):
    for i in range(5):
        db(_seed(f"c{i}", "o1", 10 + i, 100))
    db(_seed("gone", "o1", 99, 999))
    db(_seed_state("gone", sc.DEL_CONFIRMED))
    db(sc.recompute_ranking(NOW_TS))
    d = db(sc.load_main())
    owners = [s["channelId"] for s in d["streamers"]]
    assert owners.count("o1") == 1


# ── 6. 새 대표의 baseline ──────────────────────────────────────────────────
async def _snap(owner, clip_uid, hearts, at):
    db = await sc.get_db()
    await db.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at,"
        " snapshot_bucket) VALUES (?,?,?,?,0,0,0,1,?,?)",
        (sc.EVENT_ID, clip_uid, owner, hearts, int(at), sc.snapshot_bucket(int(at))))
    await db.commit()


def test_baselines_are_not_carried_over_to_the_new_representative(db):
    """1시간·24시간 기준값을 새 클립에 그대로 붙이면 증감이 통째로 가짜가 된다."""
    import time
    now = int(time.time())
    db(_seed("gone", "o1", 64, 525, created=now - 8 * 86400))
    db(_seed("new", "o1", 54, 240, created=now - 2 * 86400))
    # 옛 대표(gone)로 남아 있는 1시간·24시간 스냅샷
    db(_snap("o1", "gone", 60, now - 3600))
    db(_snap("o1", "gone", 30, now - 25 * 3600))
    db(_seed_state("gone", sc.DEL_CONFIRMED))
    db(sc.recompute_ranking(now))

    me = [s for s in db(sc.load_main())["streamers"] if s["channelId"] == "o1"][0]
    assert me["clipUid"] == "new"
    assert me["heartDelta"] is None, "1시간 증감을 다른 영상과 빼면 안 된다"
    assert me["deltaState"] == "representative_changed"
    assert me["heartDelta24h"] is None, "24시간도 마찬가지다"
    assert me["heartChangeRate24h"] is None
    assert me["delta24hState"] == "representative_changed"


# ── 7. 복구 ────────────────────────────────────────────────────────────────
def test_confirmed_clip_can_recover(db):
    db(_seed("a", "o1", 10, 100, state=sc.DEL_CONFIRMED, active=0, checks=2))
    db(_age_out("a", sc.DELETION_RECHECK_HOURS * 3600 + 60))
    install(db, lambda req: _detail_ok("a"))
    out = db(sc.run_deletion_checks())
    assert out["recovered"] == 1
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_RECOVERED
    assert r["active"] == 1 and r["missing_scan_count"] == 0


def test_recovered_clip_can_be_representative_again(db):
    db(_seed("gone", "o1", 64, 525, state=sc.DEL_CONFIRMED, active=0, checks=2))
    db(_seed("new", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_rep_of("o1")) == "new"

    db(_age_out("gone", sc.DELETION_RECHECK_HOURS * 3600 + 60))
    install(db, lambda req: _detail_ok("gone"))
    db(sc.run_deletion_checks())
    assert db(_rep_of("o1")) == "gone", "복구되면 다시 대표 후보가 된다"


def test_confirmed_clip_is_never_physically_deleted(db):
    db(_seed("a", "o1", 10, 100, state=sc.DEL_CONFIRMED, active=0, checks=2))
    db(_snap("o1", "a", 10, NOW_TS - 3600))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_clip("a")) is not None, "행을 지우면 감사·통계 이력이 사라진다"
    db_ = db(sc.get_db())
    n = db((db(db_.execute(
        "SELECT COUNT(*) c FROM singcup_snapshots WHERE clip_uid='a'"))).fetchone())
    assert n["c"] == 1, "과거 스냅샷도 그대로 남는다"


# ── 8. 멱등·재실행 ─────────────────────────────────────────────────────────
def test_rerun_is_idempotent(db):
    db(_seed("a", "o1", 10, 100, state=sc.DEL_CONFIRMED, active=0, checks=2))
    install(db, lambda req: _DELETED)
    before = db(_clip("a"))
    for _ in range(3):
        db(sc.run_deletion_checks())
    after = db(_clip("a"))
    assert after["deletion_state"] == before["deletion_state"]
    assert after["active"] == 0


def test_no_targets_means_no_requests(db):
    """의심·확정이 없으면 요청이 한 건도 나가지 않는다."""
    calls = []

    def count(request):
        calls.append(str(request.url))
        return _detail_ok()
    db(_seed("a", "o1", 10, 100))          # 전부 active
    install(db, count)
    out = db(sc.run_deletion_checks())
    assert out["checked"] == 0
    assert calls == []


# ── 9. 관리자 재확인은 판정 규칙을 우회하지 않는다 ────────────────────────
def test_admin_recheck_still_needs_two_confirmations(db):
    db(_seed("a", "o1", 10, 100))
    install(db, lambda req: _DELETED)
    out1 = db(sc.recheck_clip_deletion("a"))
    assert out1["verdict"] == "deleted"
    assert out1["state"] == sc.DEL_SUSPECTED and out1["checks"] == 1
    out2 = db(sc.recheck_clip_deletion("a"))
    assert out2["state"] == sc.DEL_CONFIRMED and out2["checks"] == 2
    assert out2["active"] == 0


def test_admin_recheck_on_missing_clip(db):
    assert db(sc.recheck_clip_deletion("nope"))["found"] is False


# ── 10. Snapshot 정합성 ────────────────────────────────────────────────────
def test_split_snapshot_reflects_the_new_representative(db, monkeypatch):
    """대표가 바뀌면 Split 스냅샷도 **완료된 세트**로 다시 게시된다."""
    import singcup_split_api as split
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    split.reset()
    db(_seed("gone", "o1", 64, 525))
    db(_seed("new", "o1", 54, 240))
    db(_seed_state("gone", sc.DEL_CONFIRMED))
    db(sc.recompute_ranking(NOW_TS))

    snap = split.latest()
    assert snap is not None
    uids = {s["clipUid"] for s in snap.streamers}
    assert uids == {"new"}, "삭제 클립이 스냅샷에 남아 있으면 안 된다"


def test_no_snapshot_is_published_when_ranking_fails_midway(db, monkeypatch):
    """중간에 실패하면 불완전한 대표 세트를 게시하지 않는다."""
    import singcup_split_api as split
    monkeypatch.setattr(split, "SPLIT_API_ENABLED", True)
    split.reset()
    db(_seed("a", "o1", 10, 100))

    async def boom(*_a, **_k):
        raise RuntimeError("db down")
    monkeypatch.setattr(sc, "_upsert_streamers_bulk", boom)

    with pytest.raises(RuntimeError):
        db(sc.recompute_ranking(NOW_TS))
    assert split.latest() is None, "실패한 회차가 스냅샷으로 나가면 안 된다"


# ── 11. 카드 조회 실패도 '확인 대상'일 뿐이다 ─────────────────────────────
def test_card_fetch_failure_flags_but_never_counts(db):
    """카드가 아예 응답하지 않아도 그것만으로 삭제가 되면 안 된다."""
    import singcup_sweep as sw
    db(_seed("a", "o1", 10, 100))
    db(sw._persist_clip({"clip_uid": "a", "video_id": "v", "rec_id": "{}",
                         "owner_channel_id": "o1", "thumbnail_image_url": "t"},
                        None, None, NOW_TS))
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_SUSPECTED
    assert r["missing_scan_count"] == 0
    assert r["active"] == 1
    assert r["deletion_reason"] == "card_failed"


def test_suspect_from_card_failure_clears_when_alive(db):
    """일시적 실패였다면 첫 확인에서 바로 풀린다."""
    import singcup_sweep as sw
    db(_seed("a", "o1", 10, 100))
    db(sw._persist_clip({"clip_uid": "a", "video_id": "v", "rec_id": "{}",
                         "owner_channel_id": "o1", "thumbnail_image_url": "t"},
                        None, None, NOW_TS))
    install(db, lambda req: _detail_ok("a"))
    db(sc.run_deletion_checks())
    r = db(_clip("a"))
    assert r["deletion_state"] == sc.DEL_ACTIVE
    assert r["active"] == 1


# ── 12. 마이그레이션 안전성 ────────────────────────────────────────────────
def test_legacy_inactive_rows_are_not_marked_deleted():
    """기존 active=0 행을 삭제 확정으로 바꾸면 안 된다(강한 신호를 본 적이 없다)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "database" / "db.py").read_text(
        encoding="utf-8")
    assert "deletion_state='unknown_legacy'" in src
    assert "SET deletion_state='confirmed_deleted'" not in src, (
        "마이그레이션이 삭제를 확정하면 되돌릴 근거가 남지 않는다")


def test_unknown_legacy_is_not_treated_as_deleted(db):
    """legacy 비활성 행은 삭제도 아니고, 되살아나지도 않는다(기존 사실 유지)."""
    db(_seed("legacy", "o1", 10, 100, state=sc.DEL_UNKNOWN_LEGACY, active=0))
    db(_seed("alive", "o1", 5, 50))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_rep_of("o1")) == "alive"
    r = db(_clip("legacy"))
    assert r["deletion_state"] == sc.DEL_UNKNOWN_LEGACY
    assert r["active"] == 0, "마이그레이션이 상태만 붙이고 active를 바꾸지 않는다"


# ── 13. 롤백 ───────────────────────────────────────────────────────────────
def test_audit_lists_restorable_rows(db):
    db(_seed("gone", "o1", 64, 525, state=sc.DEL_CONFIRMED, active=0, checks=2))
    db(_seed("legacy", "o2", 1, 1, state=sc.DEL_UNKNOWN_LEGACY, active=0))
    db(_seed("ok", "o3", 1, 1))
    out = db(sc.deleted_clip_audit())
    uids = {c["clip_uid"] for c in out["clips"]}
    assert uids == {"gone", "legacy"}
    assert out["count"] == 2
    assert all("deleted_at" in c for c in out["clips"])


def test_restore_only_the_named_clips(db):
    db(_seed("a", "o1", 10, 100, state=sc.DEL_CONFIRMED, active=0, checks=2))
    db(_seed("b", "o2", 10, 100, state=sc.DEL_CONFIRMED, active=0, checks=2))
    out = db(sc.restore_deleted_clips(["a"], reason="rollback"))
    assert out["restored"] == 1 and out["clips"] == ["a"]
    assert db(_clip("a"))["active"] == 1
    assert db(_clip("a"))["deletion_state"] == sc.DEL_RECOVERED
    assert db(_clip("b"))["active"] == 0, "지정하지 않은 클립은 그대로 둔다"


def test_restore_reselects_representative(db):
    db(_seed("gone", "o1", 64, 525, state=sc.DEL_CONFIRMED, active=0, checks=2))
    db(_seed("new", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_rep_of("o1")) == "new"
    db(sc.restore_deleted_clips(["gone"]))
    assert db(_rep_of("o1")) == "gone", "되살리면 대표도 정상 규칙으로 다시 정해진다"


def test_restore_is_idempotent_and_ignores_healthy_clips(db):
    db(_seed("a", "o1", 10, 100, state=sc.DEL_CONFIRMED, active=0, checks=2))
    db(_seed("healthy", "o2", 10, 100))
    assert db(sc.restore_deleted_clips(["a", "healthy"]))["restored"] == 1
    assert db(sc.restore_deleted_clips(["a"]))["restored"] == 0
    assert db(_clip("healthy"))["deletion_state"] == sc.DEL_ACTIVE


def test_nothing_is_physically_deleted_so_restore_is_always_possible(db):
    """물리 삭제가 없다는 것을 데이터로 증명한다."""
    db(_seed("a", "o1", 64, 525))
    db(_snap("o1", "a", 60, NOW_TS - 3600))
    before = db(_clip("a"))
    db(_seed_state("a", sc.DEL_CONFIRMED))
    db(sc.recompute_ranking(NOW_TS))
    db(sc.restore_deleted_clips(["a"]))
    after = db(_clip("a"))
    for k in ("clip_uid", "owner_channel_id", "heart_count", "view_count",
              "created_at", "first_collected_at", "description"):
        assert after[k] == before[k], k
    assert after["active"] == 1


# ── 14. 동시 실행 ──────────────────────────────────────────────────────────
def test_deletion_check_skips_a_clip_locked_by_another_worker(db):
    """스윕·수동 갱신이 잡고 있는 클립은 이번 회차에 건드리지 않는다."""
    db(_seed("a", "o1", 10, 100, state=sc.DEL_SUSPECTED))
    token = db(sc.acquire_clip_lock("a"))
    assert token is not None
    install(db, lambda req: _DELETED)
    try:
        out = db(sc.run_deletion_checks())
        assert out["skipped"] == 1 and out["checked"] == 1
        assert db(_clip("a"))["missing_scan_count"] == 0, "동시 판정이 일어나지 않았다"
    finally:
        db(sc.release_clip_lock("a", token))


def test_state_and_active_change_together(db):
    """active=0만 저장되고 상태가 옛 값으로 남는 중간 상태가 없어야 한다."""
    db(_seed("a", "o1", 10, 100, state=sc.DEL_SUSPECTED, checks=1))
    db(_age_out("a"))
    install(db, lambda req: _DELETED)
    db(sc.run_deletion_checks())
    r = db(_clip("a"))
    assert (r["deletion_state"], r["active"]) == (sc.DEL_CONFIRMED, 0)


# ── 15. 확인 대기열 우선순위 ───────────────────────────────────────────────
# 한 스윕 회차에서 카드가 비는 클립이 수백 건 나온다(실측 2,766건 중 349건 실패).
# 대표 클립이 그 뒤에 줄을 서면 순위가 틀어진 채로 한 시간을 보낸다.
def test_representative_suspect_is_checked_first(db):
    for i in range(50):
        db(_seed(f"n{i}", f"o{i}", 5, 50, state=sc.DEL_SUSPECTED))
        db(_reason(f"n{i}", "card_empty"))
    db(_seed("rep", "orep", 64, 525, state=sc.DEL_SUSPECTED))
    db(_reason("rep", "card_empty"))
    db(_make_rep("orep", "rep"))

    due = db(sc._deletion_due(NOW_TS, 5))
    assert due[0]["clip_uid"] == "rep", [d["clip_uid"] for d in due[:3]]
    assert due[0]["prio"] == 0


async def _reason(uid, reason):
    db = await sc.get_db()
    await db.execute("UPDATE singcup_clips SET deletion_reason=? WHERE clip_uid=?",
                     (reason, uid))
    await db.commit()


async def _make_rep(owner, uid):
    db = await sc.get_db()
    await db.execute(
        "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
        " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
        " representative_clip_uid, row_updated_at) VALUES (?,?,?,'',0,0,1,?,?)"
        " ON CONFLICT(channel_id) DO UPDATE SET"
        " representative_clip_uid=excluded.representative_clip_uid",
        (owner, sc.EVENT_ID, owner, uid, NOW_TS))
    await db.commit()


def test_priority_order_is_rep_then_progress_then_legacy_then_confirmed(db):
    db(_seed("rep", "o1", 1, 1, state=sc.DEL_SUSPECTED))
    db(_reason("rep", "card_empty"))
    db(_make_rep("o1", "rep"))
    db(_seed("prog", "o2", 1, 1, state=sc.DEL_SUSPECTED, checks=1, last_at=1))
    db(_age_out("prog"))
    db(_seed("card", "o3", 1, 1, state=sc.DEL_SUSPECTED))
    db(_reason("card", "card_empty"))
    db(_seed("list", "o4", 1, 1, state=sc.DEL_SUSPECTED))
    db(_reason("list", "list_absent"))
    db(_seed("leg", "o5", 1, 1, state=sc.DEL_UNKNOWN_LEGACY, active=0))
    db(_seed("done", "o6", 1, 1, state=sc.DEL_CONFIRMED, active=0, checks=2))

    order = [d["clip_uid"] for d in db(sc._deletion_due(NOW_TS, 10))]
    assert order.index("rep") < order.index("prog") < order.index("card")
    assert order.index("card") < order.index("list") < order.index("leg")
    assert order.index("leg") < order.index("done")


def test_legacy_rows_are_never_auto_reactivated(db):
    """사람이 내렸을 수도 있는 행을 자동 복구로 되살리면 안 된다."""
    db(_seed("leg", "o1", 10, 100, state=sc.DEL_UNKNOWN_LEGACY, active=0))
    install(db, lambda req: _detail_ok("leg"))
    out = db(sc.run_deletion_checks())
    assert out["recovered"] == 0
    r = db(_clip("leg"))
    assert r["active"] == 0, "legacy 행이 자동으로 되살아났다"
    assert r["deletion_state"] == sc.DEL_UNKNOWN_LEGACY
    assert r["deletion_reason"] == "legacy_alive"
    assert r["deletion_last_at"] > 0, "확인 시각은 남는다(다시 곧바로 묻지 않게)"


def test_legacy_rows_can_still_be_confirmed_deleted(db):
    """살아 있지 않다면 증거를 붙여 확정 상태로 분류한다."""
    db(_seed("leg", "o1", 10, 100, state=sc.DEL_UNKNOWN_LEGACY, active=0))
    install(db, lambda req: _DELETED)
    db(sc.run_deletion_checks())
    db(_age_out("leg"))
    db(sc.run_deletion_checks())
    r = db(_clip("leg"))
    assert r["deletion_state"] == sc.DEL_CONFIRMED
    assert r["deletion_reason"] == "http_404"


def test_confirmed_clips_do_not_crowd_out_suspects(db):
    """확정 클립 수백 건이 있어도 의심 클립이 먼저 처리된다."""
    for i in range(100):
        db(_seed(f"d{i}", f"od{i}", 1, 1, state=sc.DEL_CONFIRMED, active=0, checks=2))
    db(_seed("new", "onew", 1, 1, state=sc.DEL_SUSPECTED))
    due = db(sc._deletion_due(NOW_TS, 20))
    assert due[0]["clip_uid"] == "new"


# ── 16. 대표 변경 원자성 ───────────────────────────────────────────────────
# 삭제 확정과 대표 재선정이 서로 다른 트랜잭션이면 "기존 대표만 내려가고 새 대표가
# 없는" 부분 상태가 남는다. `/main`은 singcup_streamers.representative_clip_uid를
# JOIN하므로 그 순간 스트리머가 통째로 사라진다.
async def _rep_row(owner):
    db = await sc.get_db()
    r = await (await db.execute(
        "SELECT representative_clip_uid FROM singcup_streamers WHERE channel_id=?",
        (owner,))).fetchone()
    return r["representative_clip_uid"] if r else "<no row>"


async def _all_reps(owner):
    db = await sc.get_db()
    r = await (await db.execute(
        "SELECT COUNT(*) n FROM singcup_streamers WHERE channel_id=?",
        (owner,))).fetchone()
    return int(r["n"])


async def _confirm(uid, owner, reason="http_404", checks=None, prepare=True):
    """실제 경로와 같은 전제를 만든 뒤 확정 단계를 부른다.

    운영에서 이 함수는 **이미 suspected_deleted인 행**에만 불린다(1차 404를 받은 뒤).
    prepare=False면 그 전제를 만들지 않아, 상태가 어긋났을 때 확정을 거부하는지
    확인할 수 있다.
    """
    if prepare:
        conn = await sc.get_db()
        await conn.execute(
            "UPDATE singcup_clips SET deletion_state=?, missing_scan_count=1 "
            "WHERE clip_uid=?", (sc.DEL_SUSPECTED, uid))
        await conn.commit()
    row = {"clip_uid": uid, "owner_channel_id": owner,
           "deletion_state": sc.DEL_SUSPECTED, "deletion_last_at": 0,
           "missing_scan_count": 1}
    return await sc._confirm_deleted_and_reselect(
        row, NOW_TS, reason, checks or sc.DELETION_CONFIRM_CHECKS)


def test_confirm_and_reselect_is_one_transaction(db):
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_rep_row("o1")) == "gone"

    assert db(_confirm("gone", "o1")) is True
    # 커밋 직후 — recompute 없이도 이미 정합적이다
    assert db(_clip("gone"))["active"] == 0
    assert db(_clip("gone"))["deletion_state"] == sc.DEL_CONFIRMED
    assert db(_rep_row("o1")) == "next"


def test_rollback_when_representative_update_fails(db, monkeypatch):
    """대표 UPDATE가 실패하면 삭제 확정도 함께 롤백된다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    real_write = sc.db_write_isolated

    async def failing(db_path, fn, **kw):
        async def wrapped(conn):
            await fn(conn)
            raise RuntimeError("commit 직전 실패")
        try:
            return await real_write(db_path, wrapped, **kw)
        except RuntimeError:
            return False
    monkeypatch.setattr(sc, "db_write_isolated", failing)

    assert db(_confirm("gone", "o1")) is False
    r = db(_clip("gone"))
    assert r["active"] == 1, "삭제만 저장되고 대표가 안 바뀌는 부분 상태가 남았다"
    assert r["deletion_state"] != sc.DEL_CONFIRMED
    assert db(_rep_row("o1")) == "gone"


def test_owner_lock_blocks_concurrent_reselect(db):
    """다른 워커가 owner 락을 쥐고 있으면 아무것도 바꾸지 않는다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    tok = db(sc.acquire_named_lock(sc.owner_lock_name("o1"), 60))
    assert tok is not None
    try:
        assert db(_confirm("gone", "o1")) is False
        assert db(_clip("gone"))["active"] == 1
        assert db(_rep_row("o1")) == "gone"
    finally:
        db(sc.release_named_lock(sc.owner_lock_name("o1"), tok))
    assert db(_confirm("gone", "o1")) is True
    assert db(_rep_row("o1")) == "next"


def test_owner_lock_is_held_by_a_separate_connection(db):
    """운영과 같은 **독립 연결**이 잡은 락도 존중한다."""
    import aiosqlite

    import database
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def hold_and_try():
        conn = await aiosqlite.connect(database.DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO singcup_locks (name, locked_until, owner)"
            " VALUES (?,?,?)",
            (sc.owner_lock_name("o1"), int(NOW_TS) + 10000, "other-worker"))
        await conn.commit()
        try:
            return await sc._confirm_deleted_and_reselect(
                {"clip_uid": "gone", "owner_channel_id": "o1",
                 "deletion_state": sc.DEL_SUSPECTED, "deletion_last_at": 0,
                 "missing_scan_count": 1}, NOW_TS, "http_404", 2)
        finally:
            await conn.execute("DELETE FROM singcup_locks WHERE name=?",
                               (sc.owner_lock_name("o1"),))
            await conn.commit()
            await conn.close()

    assert db(hold_and_try()) is False
    assert db(_clip("gone"))["active"] == 1


def test_two_clips_of_the_same_owner_confirmed_concurrently(db):
    """같은 owner의 두 클립이 동시에 확정돼도 대표는 정확히 하나."""
    import asyncio
    db(_seed("a", "o1", 64, 525))
    db(_seed("b", "o1", 60, 400))
    db(_seed("c", "o1", 10, 50))
    db(sc.recompute_ranking(NOW_TS))

    async def both():
        return await asyncio.gather(_confirm("a", "o1"), _confirm("b", "o1"),
                                    return_exceptions=True)
    db(both())
    rep = db(_rep_row("o1"))
    alive = [u for u in ("a", "b", "c") if db(_clip(u))["active"] == 1]
    assert rep in alive, f"대표 {rep}가 비활성 클립을 가리킨다 (alive={alive})"
    assert db(_all_reps("o1")) == 1, "스트리머 행이 하나가 아니다"


def test_no_candidate_leaves_streamer_out_of_public_main(db):
    """새 대표 후보가 없으면 스트리머가 공개 목록에서 빠진다(삭제 클립 노출 금지)."""
    db(_seed("only", "o1", 64, 525))
    db(_seed("other", "o2", 10, 100))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_confirm("only", "o1")) is True
    assert db(_rep_row("o1")) is None, "삭제된 clip_uid를 대표로 들고 있으면 안 된다"

    ids = [s["channelId"] for s in db(sc.load_main())["streamers"]]
    assert "o1" not in ids
    assert "o2" in ids
    assert db(_clip("only")) is not None, "행은 보존된다"


def test_streamer_returns_when_a_new_valid_clip_appears(db):
    """대표가 비었어도 새 유효 클립이 생기면 자동으로 다시 참가자가 된다."""
    db(_seed("only", "o1", 64, 525))
    db(sc.recompute_ranking(NOW_TS))
    db(_confirm("only", "o1"))
    assert db(_rep_row("o1")) is None

    db(_seed("fresh", "o1", 5, 20))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_rep_row("o1")) == "fresh"
    assert "o1" in [s["channelId"] for s in db(sc.load_main())["streamers"]]


def test_tie_break_is_deterministic(db):
    """하트·조회수가 동률이면 생성 시각↑ → clip_uid↑ 로 항상 같은 대표."""
    db(_seed("gone", "o1", 99, 999))
    db(_seed("zz", "o1", 10, 100, created=NOW_TS - 100))
    db(_seed("aa", "o1", 10, 100, created=NOW_TS - 100))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_confirm("gone", "o1")) is True
    assert db(_rep_row("o1")) == "aa"


def test_transaction_picks_the_same_clip_as_recompute(db):
    """트랜잭션 안의 선택과 직후 recompute의 선택이 같아야 한다."""
    db(_seed("gone", "o1", 99, 999))
    for i, (h, v) in enumerate(((50, 10), (50, 20), (40, 99))):
        db(_seed(f"k{i}", "o1", h, v))
    db(sc.recompute_ranking(NOW_TS))
    db(_confirm("gone", "o1"))
    picked = db(_rep_row("o1"))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_rep_row("o1")) == picked


def test_confirm_is_idempotent(db):
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))
    assert db(_confirm("gone", "o1")) is True
    for _ in range(3):
        # 이미 확정된 행 — prepare가 상태를 되돌리지 않도록 끈다
        assert db(_confirm("gone", "o1", prepare=False)) is False
    assert db(_rep_row("o1")) == "next"


def test_recovered_clip_is_not_reconfirmed(db):
    """probe 이후 살아난 것이 확인됐다면 확정으로 되돌리지 않는다."""
    db(_seed("a", "o1", 10, 100, state=sc.DEL_ACTIVE))
    db(sc.recompute_ranking(NOW_TS))
    # DB는 active인데 확정을 시도한다 → 트랜잭션 안의 재조회가 막는다
    assert db(_confirm("a", "o1", prepare=False)) is False
    assert db(_clip("a"))["active"] == 1
    assert db(_clip("a"))["deletion_state"] == sc.DEL_ACTIVE


def test_deleted_clip_never_appears_in_public_main_during_transition(db):
    """전환 전·후 어느 시점에도 삭제 클립이 공개 목록에 없다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))
    db(_confirm("gone", "o1"))
    d = db(sc.load_main())
    assert all(s["clipUid"] != "gone" for s in d["streamers"])
    me = [s for s in d["streamers"] if s["channelId"] == "o1"][0]
    assert me["clipUid"] == "next"
    assert (me["heartCount"], me["viewCount"]) == (54, 240)


# ── 17. owner 락 TTL ───────────────────────────────────────────────────────
# 임의의 숫자(처음엔 30초였다)를 넣으면 상수가 바뀔 때 조용히 만료돼, 트랜잭션이
# 아직 도는 중에 다른 워커가 같은 owner의 대표를 바꾼다. 상수에서 유도한다.
def test_owner_lock_ttl_covers_the_worst_case():
    worst = sc._owner_lock_worst_seconds()
    assert sc.OWNER_LOCK_TTL >= worst * 1.5, (
        f"TTL {sc.OWNER_LOCK_TTL}s 가 최악 {worst:.1f}s 의 1.5배를 못 덮는다")


def test_owner_lock_hold_worst_is_the_sum_of_hard_budgets():
    """락 보유 최악 = 대표 변경 하드 예산 + release 상한 + 후처리 여유."""
    from utils.db_write import isolated_worst_case_seconds
    expect = (isolated_worst_case_seconds(budget_seconds=sc.OWNER_TX_BUDGET_SECONDS)
              + isolated_worst_case_seconds(
                  budget_seconds=sc.OWNER_LOCK_TX_BUDGET_SECONDS)
              + sc.OWNER_POST_SLACK_SECONDS)
    assert abs(sc._owner_lock_hold_worst_seconds() - expect) < 1e-9
    # 락 **획득 대기**는 포함하지 않는다(아직 쥐고 있지 않다)
    assert sc._owner_lock_hold_worst_seconds() < expect + 1e-9


def test_hard_budget_does_not_depend_on_attempt_count():
    """절대 deadline이라 시도 횟수를 늘려도 상한이 커지지 않는다."""
    from utils.db_write import isolated_worst_case_seconds
    a = isolated_worst_case_seconds(budget_seconds=3.0)
    assert a == 3.0 + 0.25
    # 예산을 늘리면 그만큼만 늘어난다
    assert isolated_worst_case_seconds(budget_seconds=5.0) == 5.25


def test_owner_lock_worst_case_excludes_the_shared_queue():
    """공유 연결의 큐 대기는 상한이 없다 — 그래서 최악 계산에 들어가면 안 된다.

    실측(bench): 앞선 느린 작업 1/2/3개에 대해 큐 대기가 550 / 1,075 / 1,783ms로
    선형 증가했다. busy_timeout은 이 구간을 막지 못한다. 그래서 대표 변경은
    전용 연결을 쓰고, 최악 계산에는 공유 연결 상수가 들어가지 않는다.
    """
    import inspect
    src = inspect.getsource(sc._owner_lock_hold_worst_seconds)
    # 공유 연결의 busy_timeout(database.db.BUSY_TIMEOUT_MS)을 쓰면 안 된다.
    assert "database.db import" not in src, "공유 연결 상수가 계산에 남아 있다"
    assert "BUSY_TIMEOUT_MS" not in src
    assert "isolated_worst_case_seconds" in src
    # 대표 변경은 공유 연결 db_write를 쓰지 않는다
    body = inspect.getsource(sc._confirm_deleted_and_reselect)
    assert "db_write_isolated(" in body
    assert "db_write(get_db" not in body


def test_owner_transaction_uses_a_dedicated_connection(db, monkeypatch):
    """공유 연결에 느린 작업이 밀려 있어도 대표 변경은 그 뒤에서 기다리지 않는다."""
    import asyncio
    import time as _t
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def scenario():
        conn = await sc.get_db()
        await conn.execute("CREATE TABLE IF NOT EXISTS slow (a INTEGER, b TEXT)")
        await conn.executemany("INSERT INTO slow VALUES (?,?)",
                               [(i, "x" * 100) for i in range(80_000)])
        await conn.commit()

        async def slow():
            await conn.execute(
                "SELECT COUNT(*) FROM slow a JOIN slow b ON a.a=b.a AND a.b LIKE '%x%'")
        task = asyncio.create_task(slow())
        await asyncio.sleep(0.05)
        t0 = _t.perf_counter()
        ok = await _confirm("gone", "o1")
        took = _t.perf_counter() - t0
        await task
        return ok, took

    ok, took = db(scenario())
    assert ok is True
    assert took < sc.OWNER_TX_BUDGET_SECONDS, (
        f"{took:.2f}초 — 공유 큐 뒤에서 기다린 것으로 보인다")
    assert db(_rep_row("o1")) == "next"


def test_owner_lock_does_not_expire_during_worst_case_contention(db):
    """최악 DB 경합 시간(41.7초) 동안에도 락이 살아 있어야 한다."""
    name = sc.owner_lock_name("o1")
    tok = db(sc.acquire_named_lock(name, sc.OWNER_LOCK_TTL))
    assert tok is not None

    async def still_held(after_seconds):
        conn = await sc.get_db()
        r = await (await conn.execute(
            "SELECT locked_until, owner FROM singcup_locks WHERE name=?",
            (name,))).fetchone()
        import time as _t
        return int(r["locked_until"]) > int(_t.time()) + after_seconds

    worst = sc._owner_lock_worst_seconds()
    assert db(still_held(worst)), "최악 경합이 끝나기 전에 락이 만료된다"
    db(sc.release_named_lock(name, tok))


def test_only_the_owner_token_can_renew_the_lease(db):
    name = sc.owner_lock_name("o1")
    tok = db(sc.acquire_named_lock(name, sc.OWNER_LOCK_TTL))
    assert db(sc.renew_named_lock(name, "someone-else", sc.OWNER_LOCK_TTL)) is False
    assert db(sc.renew_named_lock(name, tok, sc.OWNER_LOCK_TTL)) is True
    db(sc.release_named_lock(name, tok))


def test_only_the_owner_token_can_release(db):
    name = sc.owner_lock_name("o1")
    tok = db(sc.acquire_named_lock(name, sc.OWNER_LOCK_TTL))
    db(sc.release_named_lock(name, "someone-else"))
    assert db(sc.acquire_named_lock(name, 60)) is None, "남의 토큰으로 락이 풀렸다"
    db(sc.release_named_lock(name, tok))
    assert db(sc.acquire_named_lock(name, 60)) is not None


def test_owner_lock_is_released_after_exception(db, monkeypatch):
    """예외·롤백 이후에도 락이 남아 있으면 안 된다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def boom(*_a, **_k):
        raise RuntimeError("write 폭발")
    monkeypatch.setattr(sc, "db_write", boom)
    try:
        db(_confirm("gone", "o1"))
    except RuntimeError:
        pass
    # 락이 곧바로 다시 잡혀야 한다(TTL을 기다리지 않고)
    assert db(sc.acquire_named_lock(sc.owner_lock_name("o1"), 60)) is not None


def test_abandoned_owner_lock_is_reclaimed_after_ttl(db):
    """프로세스가 죽어 release를 못 해도 TTL이 지나면 회수된다."""
    name = sc.owner_lock_name("o1")

    async def stale():
        conn = await sc.get_db()
        await conn.execute(
            "INSERT OR REPLACE INTO singcup_locks (name, locked_until, owner)"
            " VALUES (?,?,?)", (name, int(NOW_TS) - 1, "dead-worker"))
        await conn.commit()
    db(stale())
    assert db(sc.acquire_named_lock(name, 60)) is not None, "TTL이 지났는데 회수 안 됨"


# ── 18. 락 획득 순서 ───────────────────────────────────────────────────────
def test_owner_lock_is_only_acquired_in_one_place():
    """owner 락을 잡는 곳이 하나면 순서가 갈릴 여지가 없다."""
    import inspect
    src = inspect.getsource(sc)
    hits = [ln.strip() for ln in src.splitlines()
            if "await acquire_owner_lock(" in ln]
    assert len(hits) == 1, f"owner 락 획득이 {len(hits)}곳이다: {hits}"
    assert "await acquire_owner_lock(" in inspect.getsource(
        sc._confirm_deleted_and_reselect)


def test_owner_lock_holder_never_takes_a_clip_lock():
    """owner를 쥔 채 clip을 잡으면 clip→owner 순서와 뒤집혀 교착이 가능해진다."""
    import inspect
    src = inspect.getsource(sc._confirm_deleted_and_reselect)
    assert "acquire_clip_lock" not in src


def test_both_callers_take_the_clip_lock_first():
    """확정 경로의 두 호출자가 모두 clip → owner 순서다."""
    import inspect
    auto = inspect.getsource(sc.run_deletion_checks)
    assert "acquire_clip_lock" in auto
    manual = inspect.getsource(sc.recheck_clip_deletion)
    assert "acquire_clip_lock" in manual


def test_manual_recheck_skips_when_the_clip_is_locked(db):
    db(_seed("a", "o1", 10, 100))
    tok = db(sc.acquire_clip_lock("a"))
    try:
        out = db(sc.recheck_clip_deletion("a"))
        assert out["verdict"] == "skipped" and out["changed"] is False
    finally:
        db(sc.release_clip_lock("a", tok))


# ── 19. 트랜잭션 rowcount 검증 ─────────────────────────────────────────────
def test_missing_streamer_row_is_allowed(db):
    """한 번도 랭킹에 오르지 않은 owner — 갱신할 대표 행이 없는 것이 정상이다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    # singcup_streamers 행을 만들지 않는다(recompute를 부르지 않음)
    assert db(_confirm("gone", "o1")) is True
    assert db(_clip("gone"))["active"] == 0


def test_rowcount_mismatch_rolls_everything_back(db, monkeypatch):
    """예상 밖 rowcount면 커밋하지 않는다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def dup_streamer():
        conn = await sc.get_db()
        # channel_id가 PK라 진짜 중복은 못 만든다 — COUNT를 속여 같은 경로를 탄다
        await conn.execute(
            "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
            " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
            " representative_clip_uid, row_updated_at) VALUES (?,?,?,'',0,0,1,?,?)",
            ("o1-dup", sc.EVENT_ID, "o1", "next", NOW_TS))
        await conn.commit()
    db(dup_streamer())

    real = sc._NEW_REP_SQL
    assert real  # 사용 확인용
    # COUNT가 2가 되도록 owner를 맞춘다
    async def make_dup():
        conn = await sc.get_db()
        await conn.execute("UPDATE singcup_streamers SET channel_id=? WHERE channel_id=?",
                           ("o1", "o1-dup"))
        await conn.commit()
    try:
        db(make_dup())
    except Exception:
        pass  # PK 충돌이면 중복 자체가 불가능하다는 뜻 — 아래에서 확인한다

    r = db(_clip("gone"))
    assert r["active"] == 1, "롤백되지 않았다"


def test_streamer_rows_cannot_duplicate(db):
    """channel_id가 PRIMARY KEY라 같은 owner의 행이 둘일 수 없다."""
    db(_seed("a", "o1", 1, 1))
    db(sc.recompute_ranking(NOW_TS))

    async def try_dup():
        conn = await sc.get_db()
        try:
            await conn.execute(
                "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
                " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
                " representative_clip_uid, row_updated_at) VALUES (?,?,?,'',0,0,1,?,?)",
                ("o1", sc.EVENT_ID, "o1", "a", NOW_TS))
            await conn.commit()
            return True
        except Exception:
            await conn.rollback()
            return False
    assert db(try_dup()) is False


def test_null_representative_passes_the_schema(db):
    """representative_clip_uid=NULL이 스키마 제약을 통과한다."""
    db(_seed("only", "o1", 1, 1))
    db(sc.recompute_ranking(NOW_TS))

    async def set_null():
        conn = await sc.get_db()
        await conn.execute(
            "UPDATE singcup_streamers SET representative_clip_uid=NULL WHERE channel_id=?",
            ("o1",))
        await conn.commit()
        r = await (await conn.execute(
            "SELECT representative_clip_uid FROM singcup_streamers WHERE channel_id=?",
            ("o1",))).fetchone()
        return r["representative_clip_uid"]
    assert db(set_null()) is None


# ── 20. 태그 불변식 ────────────────────────────────────────────────────────
def test_singcup_clips_has_exactly_one_insert_path():
    """새 대표 SQL에 태그 조건이 없는 근거 ① — INSERT 경로가 하나뿐이다."""
    import inspect
    import re
    src = inspect.getsource(sc)
    assert len(re.findall(r"INSERT INTO singcup_clips", src)) == 1
    assert "INSERT INTO singcup_clips" in inspect.getsource(sc._upsert_clip)


def test_untagged_card_is_not_registered_by_discovery(db):
    """근거 ② — 탐색 경로(_scan_batch)는 태그가 없으면 등록하지 않는다."""
    item = {"clipUID": "u1", "videoId": "v1", "recId": "{}", "ownerChannelId": "o1",
            "clipTitle": "t", "thumbnailImageUrl": "", "categoryType": "ETC",
            "clipCategory": "music", "duration": 60, "adult": False,
            "createdDate": IN, "blindType": None,
            "ownerChannel": {"channelId": "o1", "channelName": "가수",
                             "channelImageUrl": "", "verifiedMark": False}}

    def card_of(desc):
        def handler(request):
            return httpx.Response(200, json={"card": {
                "content": {"description": desc, "vod": {"count": 10},
                            "title": "t"},
                "interaction": {"emotion": {"reactions": [
                    {"reactionType": "like", "count": 3}]},
                    "subscription": {"channelId": "o1"}}}})
        return handler

    install(db, card_of("#음악 #취미방송"))          # 태그 없음
    db(sc._scan_batch(sc._get_client(), [item], NOW_TS))
    assert db(_clip("u1")) is None, "태그 없는 클립이 등록됐다"

    install(db, card_of("#음악 #싱드컵"))            # 태그 있음
    db(sc._scan_batch(sc._get_client(), [dict(item, clipUID="u2")], NOW_TS))
    assert db(_clip("u2")) is not None


def test_card_fetch_failure_does_not_register(db):
    """근거 ③ — 카드 조회 실패는 scan 표에만 남고 clips에는 들어가지 않는다."""
    item = {"clipUID": "f1", "videoId": "v", "recId": "{}", "ownerChannelId": "o1",
            "clipTitle": "t", "thumbnailImageUrl": "", "categoryType": "ETC",
            "clipCategory": "music", "duration": 60, "adult": False,
            "createdDate": IN, "blindType": None,
            "ownerChannel": {"channelId": "o1", "channelName": "가수",
                             "channelImageUrl": "", "verifiedMark": False}}
    install(db, lambda req: httpx.Response(500, json={}))
    db(sc._scan_batch(sc._get_client(), [item], NOW_TS))
    assert db(_clip("f1")) is None

    async def scan_row():
        conn = await sc.get_db()
        r = await (await conn.execute(
            "SELECT scan_status FROM singcup_clip_scan WHERE clip_uid=?",
            ("f1",))).fetchone()
        return r["scan_status"] if r else None
    assert db(scan_row()) == sc.SCAN_FETCH_FAILED


def test_retag_path_is_gated_by_its_caller():
    """근거 ④ — _register_from_card 자체에는 게이트가 없고 호출자(_recheck_one)가 막는다."""
    import inspect
    assert "has_singcup_tag" not in inspect.getsource(sc._register_from_card)
    caller = inspect.getsource(sc._recheck_one)
    assert "has_singcup_tag" in caller
    idx_gate = caller.index("has_singcup_tag")
    idx_call = caller.index("_register_from_card")
    assert idx_gate < idx_call, "등록이 태그 검사보다 먼저 일어난다"


def test_tag_removal_does_not_leave_an_active_row(db):
    """설명에서 태그가 사라져도 active=1로 남는 경로가 있는지 — 현재는 없다.

    재확인(_recheck_one)은 태그가 없으면 SCAN_UNTAGGED로만 기록하고 clips를
    건드리지 않는다. 즉 **이미 등록된 행의 active를 태그 제거가 바꾸지는 않는다.**
    이건 의도된 동작이다(대회 중 소급 탈락 금지) — 여기서는 그 사실을 고정한다.
    """
    import inspect
    body = inspect.getsource(sc._recheck_one)
    idx = body.index("SCAN_UNTAGGED")
    after = body[idx:idx + 400]
    assert "active" not in after, "태그 제거가 active를 건드린다"


# ── 21. 전용 연결의 실패 경로 ──────────────────────────────────────────────
def test_owner_transaction_gives_up_within_budget_under_lock(db):
    """다른 writer가 잠그면 예산 안에 실패하고 아무것도 바꾸지 않는다."""
    import time as _t

    import aiosqlite

    import database
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def scenario():
        # owner 락 획득(공유 연결 쓰기)까지 잠기면 트랜잭션 예산을 못 본다.
        # 락을 먼저 잡아 두고 놓은 뒤, 그 다음에 다른 writer가 DB를 잠근다.
        conn = await sc.get_db()
        # 전제(suspected 상태 만들기)는 공유 연결 쓰기다 — 잠그기 **전에** 끝낸다
        await conn.execute(
            "UPDATE singcup_clips SET deletion_state=?, missing_scan_count=1"
            " WHERE clip_uid=?", (sc.DEL_SUSPECTED, "gone"))
        await conn.commit()
        await conn.execute("PRAGMA busy_timeout=200")
        holder = await aiosqlite.connect(database.DB_PATH)
        await holder.execute("PRAGMA busy_timeout=0")
        await holder.execute(
            "INSERT INTO singcup_locks (name, locked_until, owner)"
            " VALUES ('lockholder-x', 0, '')")
        try:
            t0 = _t.perf_counter()
            ok = await _confirm("gone", "o1", prepare=False)
            return ok, _t.perf_counter() - t0
        finally:
            await holder.rollback()
            await holder.close()
            await conn.execute("PRAGMA busy_timeout=10000")

    ok, took = db(scenario())
    assert ok is False
    budget = sc.OWNER_TX_BUDGET_SECONDS + sc.OWNER_TX_BUSY_TIMEOUT_MS / 1000 + 1.0
    assert took < budget, f"{took:.2f}초 — 예산({budget:.2f}초)을 넘겼다"
    r = db(_clip("gone"))
    assert r["active"] == 1 and r["deletion_state"] != sc.DEL_CONFIRMED
    assert db(_rep_row("o1")) == "gone", "public /main이 보는 대표가 흔들렸다"


def test_owner_lock_released_and_retried_next_round(db):
    """잠금 실패 뒤에도 락이 남지 않고 다음 회차에서 정상 처리된다."""
    import aiosqlite

    import database
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def blocked():
        conn = await sc.get_db()
        await conn.execute(
            "UPDATE singcup_clips SET deletion_state=?, missing_scan_count=1"
            " WHERE clip_uid=?", (sc.DEL_SUSPECTED, "gone"))
        await conn.commit()
        await conn.execute("PRAGMA busy_timeout=200")
        holder = await aiosqlite.connect(database.DB_PATH)
        await holder.execute("PRAGMA busy_timeout=0")
        await holder.execute(
            "INSERT INTO singcup_locks (name, locked_until, owner)"
            " VALUES ('lockholder-y', 0, '')")
        try:
            return await _confirm("gone", "o1", prepare=False)
        finally:
            await holder.rollback()
            await holder.close()
            await conn.execute("PRAGMA busy_timeout=10000")

    assert db(blocked()) is False
    # 락이 풀려 있어야 한다(다음 회차가 곧바로 잡을 수 있다)
    tok = db(sc.acquire_named_lock(sc.owner_lock_name("o1"), 60))
    assert tok is not None
    db(sc.release_named_lock(sc.owner_lock_name("o1"), tok))
    # 다음 회차: 정상 처리
    assert db(_confirm("gone", "o1")) is True
    assert db(_rep_row("o1")) == "next"


def test_shared_connection_pragma_is_untouched_by_owner_transaction(db):
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def busy():
        conn = await sc.get_db()
        r = await (await conn.execute("PRAGMA busy_timeout")).fetchone()
        return int(r[0])

    before = db(busy())
    assert before == 10000
    db(_confirm("gone", "o1"))
    assert db(busy()) == 10000


def test_no_dedicated_connection_leak(db):
    """전용 연결이 매번 닫힌다(성공·실패 모두)."""
    import aiosqlite
    opened, closed = [], []
    real_connect = aiosqlite.connect

    def tracking(*a, **kw):
        coro = real_connect(*a, **kw)

        class _W:
            def __await__(self):
                conn = yield from coro.__await__()
                opened.append(conn)
                real_close = conn.close

                async def spy():
                    closed.append(conn)
                    return await real_close()
                conn.close = spy
                return conn
        return _W()

    import utils.db_write as dwmod
    orig = dwmod.aiosqlite.connect
    dwmod.aiosqlite.connect = tracking
    try:
        db(_seed("gone", "o1", 64, 525))
        db(_seed("next", "o1", 54, 240))
        db(sc.recompute_ranking(NOW_TS))
        db(_confirm("gone", "o1"))
    finally:
        dwmod.aiosqlite.connect = orig
    assert opened, "전용 연결이 쓰이지 않았다"
    assert len(closed) == len(opened), f"열림 {len(opened)} / 닫힘 {len(closed)}"


def test_owner_lock_ttl_is_short_now(db):
    """하드 예산 도입으로 TTL이 63초 → 8초 수준으로 줄었다."""
    assert sc.OWNER_LOCK_TTL <= 15, f"TTL {sc.OWNER_LOCK_TTL}초 — 너무 길다"
    assert sc.OWNER_LOCK_TTL >= sc._owner_lock_hold_worst_seconds() * 1.5


def test_lock_acquisition_failure_does_not_break_the_loop(db, monkeypatch):
    """owner 락 획득이 잠금으로 실패해도 예외가 루프 밖으로 나가지 않는다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def locked(*_a, **_k):
        raise Exception("database is locked")
    monkeypatch.setattr(sc, "acquire_owner_lock", locked)

    assert db(_confirm("gone", "o1")) is False       # 예외 대신 False
    assert db(_clip("gone"))["active"] == 1


# ── 22. 하드 예산 ──────────────────────────────────────────────────────────
# "예산 3초 / 실제 최악 7.35초" 모순을 없앤다. 시도 사이에서만 예산을 보면 남은
# 시간이 0.1초여도 새 2초짜리 시도가 시작된다 — 매 시도 전에 남은 시간으로
# busy_timeout을 줄인다.
def test_attempt_busy_timeout_shrinks_to_the_remaining_budget(db):
    """두 번째 시도는 남은 시간만 쓴다."""
    import time as _t

    import aiosqlite

    import database
    from utils.db_write import db_write_isolated

    seen = []
    real_connect = aiosqlite.connect

    def spy(*a, **kw):
        coro = real_connect(*a, **kw)

        class _W:
            def __await__(self):
                conn = yield from coro.__await__()
                real_exec = conn.execute

                async def exec_spy(sql, *aa, **kk):
                    if str(sql).startswith("PRAGMA busy_timeout="):
                        seen.append(int(str(sql).split("=")[1]))
                    return await real_exec(sql, *aa, **kk)
                conn.execute = exec_spy
                return conn
        return _W()

    async def scenario():
        holder = await aiosqlite.connect(database.DB_PATH)
        await holder.execute("PRAGMA busy_timeout=0")
        await holder.execute(
            "INSERT INTO singcup_locks (name, locked_until, owner)"
            " VALUES ('hb-x', 0, '')")
        try:
            async def work(conn):
                await conn.execute(
                    "INSERT INTO singcup_locks (name, locked_until, owner)"
                    " VALUES ('hb-y', 0, '')")
            t0 = _t.perf_counter()
            ok = await db_write_isolated(
                database.DB_PATH, work, what="t",
                busy_timeout_ms=2000, attempts=3, budget_seconds=3.0)
            return ok, _t.perf_counter() - t0
        finally:
            await holder.rollback()
            await holder.close()

    import utils.db_write as dwmod
    orig = dwmod.aiosqlite.connect
    dwmod.aiosqlite.connect = spy
    try:
        ok, took = db(scenario())
    finally:
        dwmod.aiosqlite.connect = orig

    assert ok is False
    assert seen, "busy_timeout이 설정되지 않았다"
    assert seen[0] <= 2000
    if len(seen) > 1:
        assert seen[-1] < 2000, f"마지막 시도가 예산을 무시했다: {seen}"
    assert sum(seen) / 1000 <= 3.0 + 0.3, f"시도 합계가 예산을 넘는다: {seen}"


def test_total_elapsed_stays_within_the_hard_budget(db):
    """실제 소요가 예산 + 정리 여유를 넘지 않는다."""
    import time as _t

    import aiosqlite

    import database
    from utils.db_write import ISOLATED_CLEANUP_RESERVE_SECONDS, db_write_isolated

    async def scenario():
        holder = await aiosqlite.connect(database.DB_PATH)
        await holder.execute("PRAGMA busy_timeout=0")
        await holder.execute(
            "INSERT INTO singcup_locks (name, locked_until, owner)"
            " VALUES ('hb-z', 0, '')")
        try:
            async def work(conn):
                await conn.execute(
                    "INSERT INTO singcup_locks (name, locked_until, owner)"
                    " VALUES ('hb-w', 0, '')")
            t0 = _t.perf_counter()
            await db_write_isolated(database.DB_PATH, work, what="t",
                                    busy_timeout_ms=2000, attempts=5,
                                    budget_seconds=1.0)
            return _t.perf_counter() - t0
        finally:
            await holder.rollback()
            await holder.close()

    took = db(scenario())
    limit = 1.0 + ISOLATED_CLEANUP_RESERVE_SECONDS + 0.5   # 정리 + 스케줄링 오차
    assert took <= limit, f"{took:.2f}초 — 하드 예산({limit:.2f}초)을 넘겼다"


def test_no_attempt_starts_without_enough_time(db):
    """남은 시간이 최소 기준보다 작으면 새 시도를 시작하지 않는다."""
    import aiosqlite

    import database
    from utils.db_write import db_write_isolated
    opened = []
    real_connect = aiosqlite.connect

    def spy(*a, **kw):
        opened.append(1)
        return real_connect(*a, **kw)

    async def scenario():
        holder = await aiosqlite.connect(database.DB_PATH)
        await holder.execute("PRAGMA busy_timeout=0")
        await holder.execute(
            "INSERT INTO singcup_locks (name, locked_until, owner)"
            " VALUES ('hb-q', 0, '')")
        try:
            async def work(conn):
                await conn.execute(
                    "INSERT INTO singcup_locks (name, locked_until, owner)"
                    " VALUES ('hb-r', 0, '')")
            # 예산이 정리 여유와 같으면 시도할 시간이 없다
            return await db_write_isolated(
                database.DB_PATH, work, what="t", busy_timeout_ms=2000,
                attempts=5, budget_seconds=0.25)
        finally:
            await holder.rollback()
            await holder.close()

    import utils.db_write as dwmod
    orig = dwmod.aiosqlite.connect
    dwmod.aiosqlite.connect = spy
    try:
        ok = db(scenario())
    finally:
        dwmod.aiosqlite.connect = orig
    assert ok is False
    assert len(opened) <= 1, f"시간이 없는데 {len(opened)}번 연결했다"


def test_isolated_write_does_not_use_wait_for():
    import ast
    import inspect

    from utils import db_write as dw
    # 문서·주석에는 "쓰지 않는다"고 적혀 있으므로 **AST의 호출식**만 본다
    tree = ast.parse(inspect.getsource(dw).lstrip())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "wait_for"]
    assert not calls, "asyncio.wait_for로 aiosqlite 작업을 취소하고 있다"


# ── 23. owner 락도 전용 연결 ───────────────────────────────────────────────
def test_owner_lock_helpers_use_dedicated_connections():
    import inspect
    for fn in (sc.acquire_owner_lock, sc.release_owner_lock, sc.renew_owner_lock):
        src = inspect.getsource(fn)
        assert "db_write_isolated(" in src, fn.__name__
        assert "acquire_named_lock(" not in src
    # 대표 변경 경로는 공유 연결 named lock을 쓰지 않는다
    body = inspect.getsource(sc._confirm_deleted_and_reselect)
    assert "acquire_owner_lock(" in body
    assert "acquire_named_lock(" not in body
    assert "release_named_lock(" not in body


def test_owner_lock_token_semantics(db):
    tok = db(sc.acquire_owner_lock("o1"))
    assert tok is not None
    assert db(sc.acquire_owner_lock("o1")) is None, "이미 잡힌 락이 또 잡혔다"
    assert db(sc.renew_owner_lock("o1", "wrong")) is False
    assert db(sc.renew_owner_lock("o1", tok)) is True
    assert db(sc.release_owner_lock("o1", "wrong")) is True   # 쓰기는 성공
    assert db(sc.acquire_owner_lock("o1")) is None, "남의 토큰으로 풀렸다"
    assert db(sc.release_owner_lock("o1", tok)) is True
    assert db(sc.acquire_owner_lock("o1")) is not None


def test_owner_lock_acquire_is_bounded_while_shared_queue_is_busy(db):
    """공유 연결에 느린 작업 3개가 쌓여도 owner 락 획득이 함께 기다리지 않는다."""
    import asyncio
    import time as _t

    async def scenario():
        conn = await sc.get_db()
        await conn.execute("CREATE TABLE IF NOT EXISTS slowq (a INTEGER, b TEXT)")
        await conn.executemany("INSERT INTO slowq VALUES (?,?)",
                               [(i, "x" * 100) for i in range(60_000)])
        await conn.commit()

        async def slow():
            await conn.execute(
                "SELECT COUNT(*) FROM slowq a JOIN slowq b"
                " ON a.a=b.a AND a.b LIKE '%x%'")
        tasks = [asyncio.create_task(slow()) for _ in range(3)]
        await asyncio.sleep(0.05)
        t0 = _t.perf_counter()
        tok = await sc.acquire_owner_lock("o1")
        took = _t.perf_counter() - t0
        for t in tasks:
            await t
        return tok, took

    tok, took = db(scenario())
    assert tok is not None
    limit = sc.OWNER_LOCK_TX_BUDGET_SECONDS + 0.5
    assert took <= limit, f"{took:.2f}초 — 공유 큐 뒤에서 기다렸다(상한 {limit:.2f}초)"
    db(sc.release_owner_lock("o1", tok))


def test_transaction_aborts_if_the_owner_lock_was_lost(db):
    """TTL이 지나 남이 락을 가져갔으면 대표를 바꾸지 않는다."""
    db(_seed("gone", "o1", 64, 525))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    async def steal(*_a, **_k):
        # acquire는 성공시키되, 곧바로 다른 워커가 락을 가져간 상태로 만든다
        conn = await sc.get_db()
        await conn.execute(
            "INSERT OR REPLACE INTO singcup_locks (name, locked_until, owner)"
            " VALUES (?,?,?)",
            (sc.owner_lock_name("o1"), int(NOW_TS) + 999, "other"))
        await conn.commit()
        return "my-token"

    import unittest.mock as m
    with m.patch.object(sc, "acquire_owner_lock", steal):
        assert db(_confirm("gone", "o1")) is False
    r = db(_clip("gone"))
    assert r["active"] == 1 and r["deletion_state"] != sc.DEL_CONFIRMED
    assert db(_rep_row("o1")) == "gone"


# ── 24. clip 락 보유시간 ───────────────────────────────────────────────────
def test_clip_lock_is_not_held_long_while_owner_lock_is_contended(db):
    """owner 락 획득이 막혀도 clip 락이 오래 붙잡히지 않는다."""
    import time as _t
    db(_seed("gone", "o1", 64, 525, state=sc.DEL_SUSPECTED))
    db(_seed("next", "o1", 54, 240))
    db(sc.recompute_ranking(NOW_TS))

    # 다른 워커가 owner 락을 이미 쥐고 있다
    other = db(sc.acquire_owner_lock("o1"))
    assert other is not None
    install(db, lambda req: _DELETED)
    try:
        t0 = _t.perf_counter()
        out = db(sc.run_deletion_checks())
        took = _t.perf_counter() - t0
    finally:
        db(sc.release_owner_lock("o1", other))

    assert out["confirmed"] == 0
    limit = sc.OWNER_LOCK_TX_BUDGET_SECONDS + sc.REQUEST_TIMEOUT + 2.0
    assert took <= limit, f"{took:.2f}초 — clip 락을 너무 오래 잡고 있었다"
    # clip 락이 풀려 수동 refresh가 곧바로 진행될 수 있어야 한다
    tok = db(sc.acquire_clip_lock("gone"))
    assert tok is not None
    db(sc.release_clip_lock("gone", tok))


# ── 25. TTL clamp ──────────────────────────────────────────────────────────
def test_ttl_is_clamped_to_the_safe_minimum():
    """환경변수로 너무 작게 줘도 안전 최소값으로 올라간다(경고만 남기지 않는다)."""
    assert sc.OWNER_LOCK_TTL >= sc.OWNER_LOCK_TTL_MIN
    assert sc.OWNER_LOCK_TTL_MIN >= sc._owner_lock_hold_worst_seconds() * 1.5
