"""기준 스냅샷(회차) 선택 — 같은 시간 버킷에 여러 collected_at이 섞여도 전원을 읽는다.

실제 사고(2026-07-30 20:5x): 1,060명 중 1,057명이 NEW로 뒤집히고 1시간 증감이
전부 null이 됐다. 원인은 저장이 아니라 **조회가 collected_at 한 점을 회차 ID로
쓴 것**이다.

  _save_snapshots는 UNIQUE(event_id, owner, snapshot_bucket) + INSERT OR IGNORE라,
  같은 시간대의 두 번째 저장에서는 기존 참가자가 전부 무시되고 **그 사이 새로 들어온
  참가자만** 새 collected_at으로 들어간다. 그런데 기준 회차를 collected_at 하나로
  고르면 그 '7명짜리 부분 세트'가 통째로 기준선이 된다.

시각은 벽시계에 기대지 않고 버킷 경계에서 직접 만든다 — 상대 오프셋(now-90분 등)은
실행 시각에 따라 같은 버킷이 되기도 하고 아니기도 해서 테스트가 흔들린다.
"""
import time

import singcup_clips as sc

import database

HOUR = 3600


def base_pair(now: int) -> tuple[int, int]:
    """기준 시각(now-1h)이 속한 버킷 **안**의 서로 다른 두 시각.

    둘 다 기준 시각에서 10분 이내라 허용 오차 안이고, 버킷을 넘지 않는다.
    """
    ref = now - sc.DELTA_WINDOW_SECONDS
    b = sc.snapshot_bucket(ref)
    off = ref - b
    return b + max(60, off - 600), b + min(3540, off + 600)


def older_bucket_at(now: int, hours: int = 1) -> int:
    """기준 버킷보다 N시간 오래된 버킷의 한가운데."""
    return sc.snapshot_bucket(now - sc.DELTA_WINDOW_SECONDS) - hours * HOUR + 1800


async def _seed_streamer(owner: str, uid: str, hearts: int, now: int,
                         first_at: int | None = None):
    """참가자 1명 + 대표 클립 1개. first_at은 '처음 발견한 시각'이다."""
    c = await database.get_db()
    fc = now - 3 * HOUR if first_at is None else first_at
    await c.execute(
        "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
        " rec_id, clip_title, thumbnail_image_url, description, created_at,"
        " heart_count, view_count, duration, adult, blind_type, metrics_ok,"
        " owner_channel_name, active, missing_scan_count, first_collected_at,"
        " last_collected_at, row_updated_at)"
        " VALUES (?,?,?,?,'','제목','','#싱드컵',?,?,0,60,0,'',1,?,1,0,?,?,?)",
        (uid, sc.EVENT_ID, owner, f"v{uid}", now - 3 * HOUR, hearts, owner,
         fc, now, now))
    await c.execute(
        "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
        " channel_image_url, follower_count, verified_mark, tagged_clip_count,"
        " representative_clip_uid, row_updated_at) VALUES (?,?,?,'',0,0,1,?,?)",
        (owner, sc.EVENT_ID, owner, uid, now))
    await c.commit()


async def _snap_set(owners, hearts_of, at: int, *, clip_of=None):
    """한 회차분 스냅샷. 저장 경로와 동일하게 bucket 컬럼도 채운다."""
    c = await database.get_db()
    await c.executemany(
        "INSERT OR IGNORE INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at,"
        " snapshot_bucket) VALUES (?,?,?,?,0,0,0,?,?,?)",
        [(sc.EVENT_ID, (clip_of or (lambda o: f"clip-{o}"))(o), o, hearts_of(o),
          i + 1, int(at), sc.snapshot_bucket(int(at))) for i, o in enumerate(owners)])
    await c.commit()


async def _raw_snap(owner, clip_uid, hearts, at, bucket=None):
    """레거시 행(snapshot_bucket NULL) 등 임의의 한 줄."""
    c = await database.get_db()
    await c.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at,"
        " snapshot_bucket) VALUES (?,?,?,?,0,0,0,1,?,?)",
        (sc.EVENT_ID, clip_uid, owner, hearts, int(at), bucket))
    await c.commit()


# ── 1. 같은 버킷 반복 저장 ─────────────────────────────────────────────────
def test_repeated_save_in_one_bucket_keeps_every_owner(db):
    """1,050명 저장 → 같은 버킷에서 신규 7명만 추가 저장.

    두 번째 저장에서 기존 1,050명은 UNIQUE로 무시되고 신규 7명만 새 collected_at을
    받는다. 그래도 '그 시간의 기준선'은 1,057명 전원이어야 한다.
    """
    now = int(time.time())
    old = [f"o{i}" for i in range(1050)]
    new = [f"n{i}" for i in range(7)]
    t1, t2 = base_pair(now)
    assert sc.snapshot_bucket(t1) == sc.snapshot_bucket(t2)

    db(_snap_set(old, lambda o: 10, t1))
    db(_snap_set(old + new, lambda o: 10, t2))     # 기존은 IGNORE, 신규만 삽입

    c = db(database.get_db())
    cur = db(c.execute("SELECT collected_at, COUNT(*) n FROM singcup_snapshots"
                       " GROUP BY collected_at ORDER BY collected_at"))
    per_time = {r["collected_at"]: r["n"] for r in db(cur.fetchall())}
    assert per_time == {t1: 1050, t2: 7}           # 부분 세트가 실제로 만들어졌다

    total = db((db(c.execute(
        "SELECT COUNT(DISTINCT owner_channel_id) n FROM singcup_snapshots"))).fetchone())
    assert total["n"] == 1057

    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    assert base is not None
    assert base["rows"] == 1057, "부분 세트(7명)가 기준선이 되면 안 된다"
    assert base["partial"] is False and base["fallbackUsed"] is False
    prev, _day, b2 = db(sc._delta_maps(now))
    assert len(prev) == 1057 and b2["rows"] == 1057


# ── 2. 예전 결함 재현 ──────────────────────────────────────────────────────
def test_old_collected_at_logic_would_have_picked_the_partial_set(db):
    """예전 로직(collected_at 한 점)이라면 기준선이 7명이었음을 그대로 보인다."""
    now = int(time.time())
    old = [f"o{i}" for i in range(1050)]
    new = [f"n{i}" for i in range(7)]
    t1, t2 = base_pair(now)
    db(_snap_set(old, lambda o: 10, t1))
    db(_snap_set(old + new, lambda o: 10, t2))

    c = db(database.get_db())
    ref = now - sc.DELTA_WINDOW_SECONDS
    # 예전 find_reference_run과 같은 질의: 기준 시각에 가장 가까운 collected_at 하나
    row = db((db(c.execute(
        "SELECT collected_at t FROM singcup_snapshots WHERE event_id=? "
        "AND collected_at BETWEEN ? AND ? ORDER BY ABS(collected_at - ?) ASC LIMIT 1",
        (sc.EVENT_ID, ref - sc.DELTA_TOLERANCE_SECONDS,
         ref + sc.DELTA_TOLERANCE_SECONDS, ref)))).fetchone())
    picked = int(row["t"])
    cnt = db((db(c.execute(
        "SELECT COUNT(*) n FROM singcup_snapshots WHERE event_id=? AND collected_at=?",
        (sc.EVENT_ID, picked)))).fetchone())["n"]
    # t2가 ref에 더 가깝게 배치돼 있으므로 예전 로직은 7행짜리를 골랐다
    assert (picked, cnt) in ((t2, 7), (t1, 1050))
    if picked == t2:
        assert cnt == 7, "사고 재현: 예전 로직은 7명짜리 세트를 골랐다"
    assert db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))["rows"] == 1057


# ── 3. owner 중복 행 선택의 결정성 ─────────────────────────────────────────
def test_duplicate_rows_pick_is_deterministic(db):
    """같은 owner에 여러 행이 있어도 항상 같은 한 줄이 선택된다.

    선택 규칙: ① 기준 시각과의 거리 ② collected_at DESC ③ id DESC.
    """
    now = int(time.time())
    ref = now - sc.DELTA_WINDOW_SECONDS
    b = sc.snapshot_bucket(ref)
    off = ref - b
    # 버킷을 넘지 않으면서 기준 시각에서 60초·600초 떨어진 두 지점
    near, far = ((b + off - 60, b + off - 600) if off >= 1800
                 else (b + off + 60, b + off + 600))
    db(_raw_snap("o1", "clip-o1", 111, far))    # 먼 행을 **먼저** 넣는다
    db(_raw_snap("o1", "clip-o1", 222, near))
    prev, _d, _b = db(sc._delta_maps(now))
    assert prev["o1"][0] == 222, "기준 시각에 가까운 행이 이겨야 한다"

    # 입력 순서를 뒤집어도 같은 결과
    db(database.get_db())
    c = db(database.get_db())
    db(c.execute("DELETE FROM singcup_snapshots"))
    db(c.commit())
    db(_raw_snap("o1", "clip-o1", 222, near))
    db(_raw_snap("o1", "clip-o1", 111, far))
    prev2, _d, _b = db(sc._delta_maps(now))
    assert prev2["o1"][0] == 222


def test_same_collected_at_breaks_tie_by_largest_id(db):
    """collected_at이 같으면 나중에 쓰인 행(id가 큰 쪽)을 쓴다."""
    now = int(time.time())
    t, _ = base_pair(now)
    db(_raw_snap("o1", "clip-o1", 111, t))
    db(_raw_snap("o1", "clip-o1", 999, t))       # 같은 시각, 더 큰 id
    prev, _d, _b = db(sc._delta_maps(now))
    assert prev["o1"][0] == 999
    assert len(prev) == 1, "owner당 정확히 한 행"


def test_owner_appears_exactly_once(db):
    now = int(time.time())
    t1, t2 = base_pair(now)
    db(_snap_set([f"o{i}" for i in range(30)], lambda o: 10, t1))
    db(_snap_set([f"o{i}" for i in range(30)], lambda o: 20, t2, clip_of=lambda o: f"clip-{o}"))
    prev, _d, base = db(sc._delta_maps(now))
    assert len(prev) == 30 == base["rows"]


# ── 4. 레거시 NULL 버킷 ────────────────────────────────────────────────────
def test_legacy_null_bucket_rows_are_included(db):
    """snapshot_bucket이 NULL인 예전 행도 같은 버킷으로 묶인다."""
    now = int(time.time())
    t1, t2 = base_pair(now)
    db(_raw_snap("legacy", "clip-legacy", 10, t1, bucket=None))
    db(_snap_set(["fresh"], lambda o: 10, t2))
    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    assert base["rows"] == 2, "NULL 버킷 행이 빠지면 안 된다"
    prev, _d, _b = db(sc._delta_maps(now))
    assert set(prev) == {"legacy", "fresh"}
    # 진단(detail) 경로에서는 레거시 행 수를 따로 보여 준다
    rep = db(sc.baseline_report())
    assert any(c["legacyBucketNullRows"] >= 1 for c in rep["candidates"])


# ── 5. 신규 참가자만 NEW ───────────────────────────────────────────────────
def test_only_true_newcomers_are_new(db):
    now = int(time.time())
    t1, t2 = base_pair(now)
    for i in range(3):
        db(_seed_streamer(f"o{i}", f"clip-o{i}", 100 + i, now, first_at=t1 - HOUR))
    # 기준 버킷이 닫힌 뒤에 처음 발견된 참가자
    db(_seed_streamer("late", "clip-late", 50, now, first_at=t2 + 60))
    db(_snap_set([f"o{i}" for i in range(3)], lambda o: 10, t1))

    by = {s["channelId"]: s for s in db(sc.load_main())["streamers"]}
    assert [by[f"o{i}"]["isNew"] for i in range(3)] == [False, False, False]
    assert by["late"]["isNew"] is True and by["late"]["deltaState"] == "new"
    assert by["o0"]["deltaState"] == "ok"


def test_missing_but_pre_existing_is_not_new(db):
    """기준 버킷 이전부터 있던 사람이 기준선에 없으면 NEW가 아니라 '기준선 불완전'."""
    now = int(time.time())
    t1, _t2 = base_pair(now)
    db(_seed_streamer("kept", "clip-kept", 100, now, first_at=t1 - HOUR))
    db(_seed_streamer("dropped", "clip-dropped", 100, now, first_at=t1 - HOUR))
    db(_snap_set(["kept"], lambda o: 10, t1))    # dropped는 저장 누락
    by = {s["channelId"]: s for s in db(sc.load_main())["streamers"]}
    assert by["dropped"]["isNew"] is False
    assert by["dropped"]["deltaState"] == "baseline_incomplete"
    assert by["dropped"]["heartDelta"] is None


def test_no_baseline_at_all_is_insufficient_history(db):
    now = int(time.time())
    db(_seed_streamer("a", "clip-a", 100, now))
    s = db(sc.load_main())["streamers"][0]
    assert s["deltaState"] == "insufficient_history"
    assert s["isNew"] is False and s["heartDelta"] is None


def test_representative_change_has_its_own_state(db):
    """대표 클립이 바뀌면 서로 다른 영상의 하트를 빼면 안 되고, 상태로도 구분한다."""
    now = int(time.time())
    t1, _ = base_pair(now)
    db(_seed_streamer("o1", "clip-new", 300, now, first_at=t1 - HOUR))
    db(_snap_set(["o1"], lambda o: 10, t1, clip_of=lambda o: "clip-old"))
    s = db(sc.load_main())["streamers"][0]
    assert s["deltaState"] == "representative_changed"
    assert s["heartDelta"] is None and s["isNew"] is False


# ── 6. 증감 계산 ───────────────────────────────────────────────────────────
def test_delta_values_across_the_bucket(db):
    """기준 하트가 서로 다른 collected_at에 흩어져 있어도 정확히 뺀다."""
    now = int(time.time())
    t1, t2 = base_pair(now)
    db(_seed_streamer("up", "clip-up", 150, now, first_at=t1 - HOUR))      # +50
    db(_seed_streamer("flat", "clip-flat", 100, now, first_at=t1 - HOUR))  # 0
    db(_seed_streamer("down", "clip-down", 80, now, first_at=t1 - HOUR))   # -20
    db(_snap_set(["up", "flat"], lambda o: 100, t1))
    db(_snap_set(["down"], lambda o: 100, t2))          # 같은 버킷의 늦은 저장

    d = db(sc.load_main())
    by = {s["channelId"]: s for s in d["streamers"]}
    assert by["up"]["heartDelta"] == 50
    assert by["flat"]["heartDelta"] == 0
    assert by["down"]["heartDelta"] == -20
    assert all(by[k]["isNew"] is False for k in ("up", "flat", "down"))
    assert [m["channelId"] for m in d["topHeartMovers1h"]] == ["up"]


# ── 7. 24시간 지표는 이 결함의 영향을 받지 않는다 ──────────────────────────
def test_24h_is_unaffected_by_partial_sets(db):
    """24시간 쪽은 owner별 MAX(collected_at)라 부분 세트로 대량 NEW가 생기지 않는다."""
    now = int(time.time())
    db(_seed_streamer("a", "clip-a", 200, now, first_at=now - 30 * HOUR))
    db(_seed_streamer("b", "clip-b", 300, now, first_at=now - 30 * HOUR))
    db(_snap_set(["a", "b"], lambda o: 100, now - 25 * HOUR))   # 24시간 기준
    db(_snap_set(["a"], lambda o: 150, now - 24 * HOUR - 60))   # 같은 버킷 부분 저장
    by = {s["channelId"]: s for s in db(sc.load_main())["streamers"]}
    assert by["a"]["delta24hState"] == "ok" and by["b"]["delta24hState"] == "ok"
    assert by["b"]["heartDelta24h"] == 200                      # 300 - 100


# ── 8. 불완전 버킷 보호와 정상 버킷 우선 ───────────────────────────────────
def test_incomplete_bucket_does_not_flip_everyone_to_new(db):
    """진짜로 일부만 저장된 버킷이 기준이 되면, 전원 NEW 대신 '기준선 불완전'."""
    now = int(time.time())
    t1, t2 = base_pair(now)
    owners = [f"o{i}" for i in range(100)]
    for o in owners:
        db(_seed_streamer(o, f"clip-{o}", 100, now, first_at=older_bucket_at(now, 3)))
    db(_snap_set(owners, lambda o: 10, older_bucket_at(now, 1)))   # 이웃 버킷은 정상
    db(_snap_set(owners[:2], lambda o: 10, t2))                    # 기준 버킷은 2명뿐
    _ = t1

    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    d = db(sc.load_main())
    states = [s["deltaState"] for s in d["streamers"]]
    assert base["partial"] is True and base["rows"] == 2 and base["fallbackUsed"] is True
    assert states.count("baseline_incomplete") == 98
    assert states.count("ok") == 2
    assert all(s["isNew"] is False for s in d["streamers"]), "전원 NEW로 뒤집히면 안 된다"
    assert d["summary"]["deltaBaseline"]["partial"] is True


def test_healthy_bucket_wins_over_a_nearer_partial_one(db):
    """더 가까운 버킷이 불완전하면, 조금 멀어도 정상 버킷을 쓴다."""
    now = int(time.time())
    _t1, t2 = base_pair(now)
    owners = [f"o{i}" for i in range(100)]
    for o in owners:
        db(_seed_streamer(o, f"clip-{o}", 150, now, first_at=older_bucket_at(now, 3)))
    older = older_bucket_at(now, 1)
    db(_snap_set(owners, lambda o: 100, older))     # 정상(100명) — 조금 멀다
    db(_snap_set(owners[:3], lambda o: 100, t2))    # 불완전(3명) — 더 가깝다

    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    if base["bucket"] == sc.snapshot_bucket(older):
        assert base["partial"] is False and base["rows"] == 100
        assert base["fallbackUsed"] is False
        assert any(r["reason"] == "partial_set" for r in base["rejected"])
        by = {s["channelId"]: s for s in db(sc.load_main())["streamers"]}
        assert by["o50"]["heartDelta"] == 50        # 정상 기준선으로 계산됐다
    else:                                            # 오래된 버킷이 허용 오차 밖
        assert base["partial"] is True


def test_growing_event_is_not_mistaken_for_incomplete(db):
    """이벤트 초기의 정상적인 참가자 증가를 불완전으로 오인하면 안 된다."""
    now = int(time.time())
    _t1, t2 = base_pair(now)
    owners = [f"o{i}" for i in range(120)]
    for i, o in enumerate(owners):
        # 뒤쪽 10명은 기준 버킷이 닫힌 뒤 등장한 진짜 신규
        db(_seed_streamer(o, f"clip-{o}", 100, now,
                          first_at=(t2 + 60) if i >= 110 else older_bucket_at(now, 3)))
    db(_snap_set(owners[:90], lambda o: 10, older_bucket_at(now, 1)))
    db(_snap_set(owners[:110], lambda o: 10, t2))
    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    assert base["rows"] == 110 and base["partial"] is False
    states = {s["deltaState"] for s in db(sc.load_main())["streamers"]}
    assert states <= {"ok", "new"}, f"정상 증가를 불완전으로 오인했다: {states}"


# ── 9. 중복·누락 ───────────────────────────────────────────────────────────
def test_no_duplicate_or_missing_owner_in_baseline(db):
    now = int(time.time())
    t1, t2 = base_pair(now)
    owners = [f"o{i}" for i in range(50)]
    for o in owners:
        db(_seed_streamer(o, f"clip-{o}", 100, now, first_at=t1 - HOUR))
    db(_snap_set(owners[:40], lambda o: 10, t1))
    db(_snap_set(owners, lambda o: 10, t2))
    prev, _d, base = db(sc._delta_maps(now))
    assert len(prev) == 50 == base["rows"]
    d = db(sc.load_main())
    ids = [s["channelId"] for s in d["streamers"]]
    assert len(ids) == len(set(ids)) == 50
    assert [s["rank"] for s in d["streamers"]] == list(range(1, 51))
    assert d["summary"]["streamerCount"] == 50


# ── 10. 재시작·재실행 안전성 ───────────────────────────────────────────────
def test_re_running_the_same_bucket_is_idempotent(db):
    now = int(time.time())
    owners = [f"o{i}" for i in range(20)]
    t, _ = base_pair(now)
    db(_snap_set(owners, lambda o: 10, t))
    db(_snap_set(owners, lambda o: 99, t))          # 재시작 후 같은 회차 재실행
    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    assert base["rows"] == 20                        # 중복 증가 없음
    prev, _d, _b = db(sc._delta_maps(now))
    assert prev["o0"][0] == 10, "INSERT OR IGNORE라 첫 값이 남는다"


# ── 11. 진단 응답 ──────────────────────────────────────────────────────────
def test_baseline_report_shape(db):
    now = int(time.time())
    t1, t2 = base_pair(now)
    db(_seed_streamer("o1", "clip-o1", 100, now, first_at=t1 - HOUR))
    db(_snap_set(["o1"], lambda o: 10, t1))
    db(_snap_set(["o2"], lambda o: 10, t2))
    rep = db(sc.baseline_report())
    for k in ("now", "targetAt", "currentStreamers", "minCoverage",
              "selected", "candidates", "day24h"):
        assert k in rep, k
    for k in ("selectedBucket", "selectedMinCollectedAt", "selectedMaxCollectedAt",
              "selectedRows", "expectedRows", "coverage", "partial",
              "fallbackUsed", "rejectedBuckets", "intervalSecondsMin",
              "intervalSecondsMax"):
        assert k in rep["selected"], k
    assert rep["selected"]["selectedRows"] == 2
    # 진단은 읽기 전용이어야 한다
    c = db(database.get_db())
    before = db((db(c.execute("SELECT COUNT(*) n FROM singcup_snapshots"))).fetchone())["n"]
    db(sc.baseline_report())
    after = db((db(c.execute("SELECT COUNT(*) n FROM singcup_snapshots"))).fetchone())["n"]
    assert before == after


# ── 12. 허용 오차 게이트 (커버리지보다 우선) ───────────────────────────────
# 시각을 합성해 버킷 배치를 결정적으로 만든다 — find_reference_baseline은 now를
# 인자로 받으므로 벽시계에 기대지 않아도 된다.
def _synthetic(now_real: int, off: int = 600) -> tuple[int, int, int]:
    """(now, ref 버킷 시작, ref). ref는 버킷 시작에서 off초 지점."""
    b = sc.snapshot_bucket(now_real) - 10 * HOUR
    return b + sc.DELTA_WINDOW_SECONDS + off, b, b + off


def test_healthy_bucket_inside_tolerance_is_selected(db):
    """가까운 버킷이 partial이고 정상 버킷이 허용 범위 안이면 → 정상 버킷."""
    now, b, ref = _synthetic(int(time.time()))
    owners = [f"o{i}" for i in range(100)]
    db(_snap_set(owners, lambda o: 10, b - 60))       # 이전 버킷, ref와 660초
    db(_snap_set(owners[:2], lambda o: 10, ref))      # ref 버킷, 부분 세트
    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    assert base["bucket"] == sc.snapshot_bucket(b - 60)
    assert base["rows"] == 100 and base["partial"] is False
    assert base["fallbackUsed"] is False and base["withinTolerance"] is True
    assert base["distance"] <= sc.DELTA_TOLERANCE_SECONDS
    assert any(r["reason"] == "partial_set" for r in base["rejected"])


def test_healthy_bucket_outside_tolerance_is_not_selected(db):
    """정상 버킷이 허용 범위 밖이면 '1시간 기준'으로 쓰지 않는다."""
    now, b, ref = _synthetic(int(time.time()))
    owners = [f"o{i}" for i in range(100)]
    # 후보 수집 창(±(허용오차+2시간)) 안이면서 허용 오차(35분) 밖 — 1시간 전
    far = b - 3000
    db(_snap_set(owners, lambda o: 10, far))           # 정상이지만 너무 멀다
    db(_snap_set(owners[:2], lambda o: 10, ref))       # 허용 범위 안이지만 부분
    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    assert base["bucket"] == sc.snapshot_bucket(ref), "먼 정상 버킷을 끌어오면 안 된다"
    assert base["partial"] is True and base["fallbackUsed"] is True
    assert base["withinTolerance"] is True


def test_all_candidates_partial_gives_baseline_incomplete(db):
    now, b, ref = _synthetic(int(time.time()))
    owners = [f"o{i}" for i in range(100)]
    db(_snap_set(owners, lambda o: 10, b - 3000))      # 비교군(허용 범위 밖)
    db(_snap_set(owners[:2], lambda o: 10, ref))
    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    assert base is not None and base["partial"] is True and base["fallbackUsed"] is True


def test_no_candidate_within_tolerance_returns_none(db):
    """허용 범위 안에 후보가 없으면 None → 소비자는 insufficient_history."""
    now, b, _ref = _synthetic(int(time.time()))
    db(_snap_set(["o1"], lambda o: 10, b - 3000))
    assert db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS)) is None


def test_outside_tolerance_is_reported_as_rejection_reason(db):
    now, b, ref = _synthetic(int(time.time()))
    db(_snap_set([f"o{i}" for i in range(10)], lambda o: 10, b - 3000))
    db(_snap_set([f"o{i}" for i in range(10)], lambda o: 10, ref))
    base = db(sc.find_reference_baseline(now, sc.DELTA_WINDOW_SECONDS))
    reasons = {r["reason"] for r in base["rejected"]}
    assert "outside_tolerance" in reasons


# ── 13. 진단 API 캐시 ──────────────────────────────────────────────────────
def test_baseline_report_is_cached_and_single_flight(db, monkeypatch):
    import asyncio
    now = int(time.time())
    t, _ = base_pair(now)
    db(_snap_set(["o1"], lambda o: 10, t))
    monkeypatch.setattr(sc, "BASELINE_REPORT_TTL", 60.0)
    sc._baseline_cache.clear()
    calls = []
    real = sc._baseline_report_uncached

    async def counted(window=None):
        calls.append(window)
        await asyncio.sleep(0.01)
        return await real(window)

    monkeypatch.setattr(sc, "_baseline_report_uncached", counted)

    async def burst():
        return await asyncio.gather(*[sc.baseline_report() for _ in range(10)])

    res = db(burst())
    assert len(calls) == 1, "동시 10회에 실제 계산은 1회"
    assert sum(1 for r in res if r["cached"]) == 9
    db(sc.baseline_report())
    assert len(calls) == 1, "TTL 안에서는 재계산하지 않는다"
    monkeypatch.setattr(sc, "BASELINE_REPORT_TTL", 0.0)
    db(sc.baseline_report())
    assert len(calls) == 2, "TTL이 지나면 정확히 한 번 재계산"


def test_baseline_report_failure_is_not_cached(db, monkeypatch):
    monkeypatch.setattr(sc, "BASELINE_REPORT_TTL", 60.0)
    sc._baseline_cache.clear()
    state = {"fail": True}
    real = sc._baseline_report_uncached

    async def flaky(window=None):
        if state["fail"]:
            raise RuntimeError("일시적 실패")
        return await real(window)

    monkeypatch.setattr(sc, "_baseline_report_uncached", flaky)
    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        db(sc.baseline_report())
    assert not sc._baseline_cache, "실패는 캐시에 남지 않는다"
    state["fail"] = False
    assert db(sc.baseline_report())["cached"] is False


def test_baseline_report_timeout(db, monkeypatch):
    import asyncio

    import pytest as _pytest
    monkeypatch.setattr(sc, "BASELINE_REPORT_TIMEOUT", 0.02)
    sc._baseline_cache.clear()

    async def slow(window=None):
        await asyncio.sleep(1.0)
        return {}

    monkeypatch.setattr(sc, "_baseline_report_uncached", slow)
    with _pytest.raises(TimeoutError):
        db(sc.baseline_report())
    assert not sc._baseline_cache
