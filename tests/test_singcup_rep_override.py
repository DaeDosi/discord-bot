"""싱드컵 대표 클립 수동 지정(override) 테스트.

검증하는 계약은 넷이다.

  ① 자동 선정 규칙은 **바뀌지 않는다** — override가 없으면 예전과 동일하다.
  ② override는 대표를 *고르는* 두 지점(파이썬 `pick_representative`,
     SQL `_NEW_REP_SQL`)에만 들어가고, 그 결과가 저장된
     `singcup_streamers.representative_clip_uid`가 곧 effective가 된다.
  ③ 그래서 그 컬럼을 읽는 모든 소비자(`/main`·점수·movers·스냅샷·스윕
     `_TARGET_SQL`)가 **구조적으로** 같은 대표를 본다(split-brain 없음).
  ④ 무효해진 override는 조용히 자동 대표로 복귀하고, 되살아나면 다시 걸린다.

외부 네트워크를 쓰지 않는다 — 채널 조회는 전부 스텁으로 막는다.
"""
from datetime import datetime, timedelta, timezone

import pytest
import singcup_clips as sc
import singcup_overrides as so

KST = timezone(timedelta(hours=9))
EV = sc.EVENT_ID
OWNER = "owner-a" + "0" * 22
OWNER2 = "owner-b" + "0" * 22


def ts(s="2026-07-28 12:00:00") -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
               .replace(tzinfo=KST).timestamp())


async def _insert_clip(uid, owner=OWNER, *, hearts=0, views=0, created=None,
                       active=1, deletion_state="active", blind=""):
    from database import get_db
    db = await get_db()
    await db.execute(
        "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
        " rec_id, clip_title, thumbnail_image_url, duration, created_at, heart_count,"
        " view_count, active, deletion_state, blind_type, first_collected_at,"
        " last_collected_at, row_updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, EV, owner, f"v-{uid}", "{}", f"title-{uid}", "", 60,
         created if created is not None else ts(), hearts, views, active,
         deletion_state, blind, ts(), ts(), ts()))
    await db.commit()


async def _insert_streamer(owner=OWNER, name="유엘 Yuel", rep=None):
    from database import get_db
    db = await get_db()
    await db.execute(
        "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
        " channel_image_url, follower_count, verified_mark, representative_clip_uid,"
        " tagged_clip_count, row_updated_at) VALUES (?,?,?,'',0,0,?,0,?)",
        (owner, EV, name, rep, ts()))
    await db.commit()


async def _rep_of(owner=OWNER):
    from database import get_db
    db = await get_db()
    r = await (await (await get_db()).execute(
        "SELECT representative_clip_uid FROM singcup_streamers WHERE channel_id=?",
        (owner,))).fetchone()
    assert db is not None
    return r["representative_clip_uid"] if r else None


async def _recompute():
    """외부 채널 API 없이 재계산한다(팔로워는 이 테스트의 관심사가 아니다)."""
    class _StubClient:
        pass

    async def _fake_fetch_channel(client, cid):
        return {"channel_name": "가수", "channel_image_url": "",
                "follower_count": 0, "verified_mark": 0}

    orig = sc.fetch_channel
    sc.fetch_channel = _fake_fetch_channel
    try:
        return await sc.recompute_ranking(ts(), client=_StubClient())
    finally:
        sc.fetch_channel = orig


# ── 1. 마이그레이션과 유니크 ────────────────────────────────────────────────
def test_migration_creates_table_and_partial_unique_index(db):
    async def go():
        from database import get_db
        conn = await get_db()
        t = await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='singcup_representative_overrides'")).fetchone()
        assert t is not None, "override 표가 만들어져야 한다"
        i = await (await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='idx_singcup_rep_override_active'")).fetchone()
        assert i is not None
        # 부분 인덱스여야 한다 — 해제(cleared_at) 이력이 남아야 하므로.
        assert "cleared_at IS NULL" in i["sql"]
    db(go())


def test_only_one_active_override_per_owner(db):
    """활성 override는 (이벤트, 스트리머)당 1개 — DB 제약으로 강제된다."""
    async def go():
        from database import get_db
        conn = await get_db()
        await conn.execute(
            "INSERT INTO singcup_representative_overrides (event_id,"
            " owner_channel_id, override_clip_uid, created_at, updated_at)"
            " VALUES (?,?,?,?,?)", (EV, OWNER, "c1", ts(), ts()))
        await conn.commit()
        import aiosqlite
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO singcup_representative_overrides (event_id,"
                " owner_channel_id, override_clip_uid, created_at, updated_at)"
                " VALUES (?,?,?,?,?)", (EV, OWNER, "c2", ts(), ts()))
            await conn.commit()
        await conn.rollback()
    db(go())


def test_cleared_rows_do_not_block_new_override(db):
    """해제된 행은 유니크 밖이라 재지정이 막히지 않는다(이력은 남는다)."""
    async def go():
        from database import get_db
        await _insert_clip("c1")
        await _insert_clip("c2")
        await so.set_override(OWNER, "c1")
        await so.clear_override(OWNER)
        await so.set_override(OWNER, "c2")
        rows = await (await (await get_db()).execute(
            "SELECT override_clip_uid, cleared_at FROM "
            "singcup_representative_overrides WHERE owner_channel_id=? "
            "ORDER BY id", (OWNER,))).fetchall()
        assert [r["override_clip_uid"] for r in rows] == ["c1", "c2"]
        assert rows[0]["cleared_at"] is not None and rows[1]["cleared_at"] is None
    db(go())


def test_set_override_replaces_previous_in_one_step(db):
    """교체 시 활성 행이 두 개가 되는 순간이 없어야 한다."""
    async def go():
        from database import get_db
        await _insert_clip("c1")
        await _insert_clip("c2")
        await so.set_override(OWNER, "c1")
        res = await so.set_override(OWNER, "c2", reason="제출본")
        assert res["previousOverrideClipUid"] == "c1"
        n = await (await (await get_db()).execute(
            "SELECT COUNT(*) n FROM singcup_representative_overrides "
            "WHERE owner_channel_id=? AND cleared_at IS NULL", (OWNER,))).fetchone()
        assert n["n"] == 1
        cur = await so.get_override(OWNER)
        assert cur["override_clip_uid"] == "c2" and cur["reason"] == "제출본"
    db(go())


# ── 2. URL·UID 파싱과 SSRF 방어 ─────────────────────────────────────────────
@pytest.mark.parametrize("raw,uid", [
    ("D4jrS5O2Lc", "D4jrS5O2Lc"),
    ("https://chzzk.naver.com/clips/D4jrS5O2Lc", "D4jrS5O2Lc"),
    ("https://www.chzzk.naver.com/clips/D4jrS5O2Lc", "D4jrS5O2Lc"),
    ("  https://chzzk.naver.com/clips/D4jrS5O2Lc  ", "D4jrS5O2Lc"),
    # 공유 링크에 흔히 붙는 쿼리/프래그먼트는 무시한다
    ("https://chzzk.naver.com/clips/D4jrS5O2Lc?from=share", "D4jrS5O2Lc"),
    ("https://chzzk.naver.com/clips/D4jrS5O2Lc#t=10", "D4jrS5O2Lc"),
    ("https://chzzk.naver.com/clips/a_b-C9", "a_b-C9"),
])
def test_parse_clip_uid_accepts(raw, uid):
    assert so.parse_clip_uid(raw) == uid


@pytest.mark.parametrize("raw", [
    "", "   ", None,
    # 다른 호스트 / 서브도메인 사칭
    "https://evil.com/clips/abc",
    "https://chzzk.naver.com.evil.com/clips/abc",
    "https://evil.com/chzzk.naver.com/clips/abc",
    # 내부 주소 — 호스트 화이트리스트가 먼저 막는다
    "https://127.0.0.1/clips/abc",
    "https://localhost/clips/abc",
    "https://169.254.169.254/clips/abc",
    "https://[::1]/clips/abc",
    "https://10.0.0.5/clips/abc",
    # 스킴
    "http://chzzk.naver.com/clips/abc",
    "file:///etc/passwd",
    "ftp://chzzk.naver.com/clips/abc",
    "javascript:alert(1)",
    # 사용자정보 트릭 — 눈에는 chzzk가 호스트로 보이지만 실제는 evil.com
    "https://chzzk.naver.com@evil.com/clips/abc",
    # 포트 지정
    "https://chzzk.naver.com:8080/clips/abc",
    # 경로 조작 / 다른 경로
    "https://chzzk.naver.com/clips/abc/../../admin",
    "https://chzzk.naver.com/video/abc",
    "https://chzzk.naver.com/clips/",
    "https://chzzk.naver.com/clips/abc/extra",
    # uid 문자 집합 밖
    "clip uid",
    "abc!@#",
    "../../etc/passwd",
    "a" * 40,
])
def test_parse_clip_uid_rejects(raw):
    with pytest.raises(so.InvalidClipInput):
        so.parse_clip_uid(raw)


def test_parse_clip_uid_never_returns_a_url():
    """반환값은 언제나 uid다 — 호출자가 이 값을 고정 endpoint에 끼워 쓴다."""
    uid = so.parse_clip_uid("https://chzzk.naver.com/clips/D4jrS5O2Lc?x=1")
    assert "/" not in uid and ":" not in uid and so.valid_clip_uid(uid)


def test_detail_endpoint_is_a_fixed_chzzk_host():
    """외부 호출 대상은 서버가 고정한다 — 사용자 입력이 호스트를 정하지 않는다."""
    assert sc.CLIP_DETAIL_API.startswith("https://api.chzzk.naver.com/")
    assert "{uid}" in sc.CLIP_DETAIL_API


# ── 3. owner/event/active/deletion 검증 ─────────────────────────────────────
def test_eligible_ok(db):
    async def go():
        await _insert_clip("c1", hearts=9)
        r, row = await so.check_clip_eligible(OWNER, "c1")
        assert r == so.REASON_OK and row["clip_uid"] == "c1"
    db(go())


def test_eligible_rejects_unknown_clip(db):
    async def go():
        r, row = await so.check_clip_eligible(OWNER, "nope")
        assert r == so.REASON_NOT_FOUND and row is None
    db(go())


def test_eligible_rejects_other_owner(db):
    """다른 사람 클립을 대표로 붙이면 순위가 통째로 틀어진다 — 가장 중요한 거부."""
    async def go():
        await _insert_clip("c1", owner=OWNER2)
        r, _ = await so.check_clip_eligible(OWNER, "c1")
        assert r == so.REASON_OWNER_MISMATCH
    db(go())


def test_eligible_rejects_inactive_deleted_blind_and_out_of_window(db):
    async def go():
        await _insert_clip("inact", active=0)
        await _insert_clip("del", deletion_state="confirmed_deleted")
        await _insert_clip("blind", blind="BLIND")
        await _insert_clip("early", created=ts("2026-07-19 23:59:59"))
        await _insert_clip("late", created=ts("2026-08-10 00:00:01"))
        assert (await so.check_clip_eligible(OWNER, "inact"))[0] == so.REASON_INACTIVE
        assert (await so.check_clip_eligible(OWNER, "del"))[0] == so.REASON_DELETED
        assert (await so.check_clip_eligible(OWNER, "blind"))[0] == so.REASON_BLIND
        assert (await so.check_clip_eligible(OWNER, "early"))[0] == so.REASON_OUT_OF_EVENT
        assert (await so.check_clip_eligible(OWNER, "late"))[0] == so.REASON_OUT_OF_EVENT
    db(go())


def test_event_boundaries_are_inclusive(db):
    async def go():
        await _insert_clip("start", created=int(sc.START_AT.timestamp()))
        await _insert_clip("end", created=int(sc.END_AT.timestamp()))
        assert (await so.check_clip_eligible(OWNER, "start"))[0] == so.REASON_OK
        assert (await so.check_clip_eligible(OWNER, "end"))[0] == so.REASON_OK
    db(go())


# ── 4. 자동 규칙과 override의 분리 ──────────────────────────────────────────
def _c(uid, hearts, views, created=None):
    return {"clip_uid": uid, "owner_channel_id": OWNER, "heart_count": hearts,
            "view_count": views, "created_at": created if created is not None else ts()}


def test_auto_rule_unchanged_without_override():
    """override를 넘기지 않으면 예전 동작 그대로다(하트↓ → 조회↓ → 생성↑ → uid↑)."""
    clips = [_c("k1G6QGNv0w", 16, 66), _c("D4jrS5O2Lc", 9, 36), _c("z", 3, 3)]
    assert sc.pick_representative(clips)["clip_uid"] == "k1G6QGNv0w"


def test_override_wins_over_auto_rule():
    """유엘 사례 — 하트가 낮은 제출본을 대표로 지정한다."""
    clips = [_c("k1G6QGNv0w", 16, 66), _c("D4jrS5O2Lc", 9, 36)]
    assert sc.pick_representative(clips, "D4jrS5O2Lc")["clip_uid"] == "D4jrS5O2Lc"
    # 자동 규칙 자체는 그대로다
    assert sc.pick_representative(clips)["clip_uid"] == "k1G6QGNv0w"


def test_override_pointing_at_missing_clip_falls_back_to_auto():
    """후보에 없는 uid(삭제·비활성 등)는 무시하고 자동으로 돌아간다."""
    clips = [_c("a", 16, 66), _c("b", 9, 36)]
    assert sc.pick_representative(clips, "gone")["clip_uid"] == "a"


def test_build_reps_applies_override_per_owner():
    tagged = [
        {**_c("a", 16, 66), "owner_channel_id": OWNER},
        {**_c("b", 9, 36), "owner_channel_id": OWNER},
        {**_c("c", 5, 5), "owner_channel_id": OWNER2},
        {**_c("d", 1, 1), "owner_channel_id": OWNER2},
    ]
    reps = sc._build_reps(tagged, {OWNER: "b"})
    got = {r["owner_channel_id"]: r["clip_uid"] for r in reps}
    assert got[OWNER] == "b", "지정한 쪽은 override"
    assert got[OWNER2] == "c", "지정 없는 쪽은 자동 그대로"


def test_python_and_sql_selection_stay_symmetric():
    """`pick_representative`와 `_NEW_REP_SQL`은 같은 규칙이어야 한다.

    주석의 계약이 코드에서 지켜지는지 본다 — 두 곳이 갈라지면 대표가 한 회차에
    두 번 바뀐다. override 도입으로 정렬 앞에 한 칸이 붙었으므로 그것도 양쪽에 있다.
    """
    sql = sc._NEW_REP_SQL
    assert "(o.id IS NOT NULL) DESC" in sql, "SQL에도 override 우선이 있어야 한다"
    assert ("c.heart_count DESC, c.view_count DESC, c.created_at ASC, c.clip_uid ASC"
            in sql), "자동 정렬 순서가 파이썬과 같아야 한다"
    assert "o.cleared_at IS NULL" in sql, "해제된 override는 붙지 않아야 한다"


# ── 5. 재계산 이후에도 override가 유지된다 ──────────────────────────────────
def test_override_survives_recompute(db):
    """upsert가 representative_clip_uid를 무조건 덮어써도 지정이 살아남아야 한다.

    (직접 UPDATE 방식이 채택 불가였던 바로 그 이유를 회귀로 고정한다.)
    """
    async def go():
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)

        await _recompute()
        assert await _rep_of() == "hi", "먼저 자동 대표가 잡힌다"

        await so.set_override(OWNER, "sub", reason="제출본")
        await _recompute()
        assert await _rep_of() == "sub", "지정이 반영된다"

        # 정기 회차가 여러 번 더 돌아도 되돌아가지 않아야 한다
        for _ in range(3):
            await _recompute()
        assert await _rep_of() == "sub"
    db(go())


def test_clear_override_returns_to_auto(db):
    async def go():
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await so.set_override(OWNER, "sub")
        await _recompute()
        assert await _rep_of() == "sub"

        await so.clear_override(OWNER)
        await _recompute()
        assert await _rep_of() == "hi", "해제하면 자동 대표로 복귀한다"
    db(go())


def test_invalid_override_falls_back_and_recovers(db):
    """override 클립이 무효가 되면 자동으로 복귀하고, 되살아나면 다시 걸린다.

    행을 지우지 않는 이유가 이것이다 — 지우면 복구 시 사람이 다시 지정해야 한다.
    """
    async def go():
        from database import get_db
        conn = await get_db()
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await so.set_override(OWNER, "sub")
        await _recompute()
        assert await _rep_of() == "sub"

        # 지정한 클립이 삭제 확정됐다
        await conn.execute("UPDATE singcup_clips SET active=0, "
                           "deletion_state='confirmed_deleted' WHERE clip_uid='sub'")
        await conn.commit()
        await _recompute()
        assert await _rep_of() == "hi", "무효 override는 자동으로 복귀한다"
        assert await so.get_override(OWNER) is not None, "행은 남아 있어야 한다"

        # 복원되면 지정이 다시 산다
        await conn.execute("UPDATE singcup_clips SET active=1, "
                           "deletion_state='active' WHERE clip_uid='sub'")
        await conn.commit()
        await _recompute()
        assert await _rep_of() == "sub"
    db(go())


def test_override_ignored_for_other_event(db):
    """다른 이벤트의 override는 이 이벤트 대표에 영향을 주지 않는다."""
    async def go():
        from database import get_db
        conn = await get_db()
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await conn.execute(
            "INSERT INTO singcup_representative_overrides (event_id,"
            " owner_channel_id, override_clip_uid, created_at, updated_at)"
            " VALUES ('other-event',?,?,?,?)", (OWNER, "sub", ts(), ts()))
        await conn.commit()
        await _recompute()
        assert await _rep_of() == "hi"
    db(go())


# ── 6. 소비자 일관성 (/main · sweep · 스냅샷 · 급상승) ──────────────────────
def test_main_and_sweep_priority_use_the_same_representative(db):
    """`/main`과 스윕 `_TARGET_SQL`이 같은 대표를 본다.

    둘 다 `singcup_streamers.representative_clip_uid`를 읽으므로, 대표를 고르는
    시점에 override를 반영하면 일치는 구조적으로 보장된다. 그 구조가 유지되는지를
    실제 두 경로로 확인한다.
    """
    async def go():
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await so.set_override(OWNER, "sub")
        await _recompute()

        main = await sc.load_main(limit=200)
        rows = [r for r in main["streamers"] if r["channelId"] == OWNER]
        assert rows and rows[0]["clipUid"] == "sub", "/main이 지정한 대표를 보여준다"

        # 스윕은 대표 여부(prio 1)를 SQL 안에서 JOIN으로 판단한다.
        from database import get_db
        is_rep = await (await (await get_db()).execute(
            "SELECT c.clip_uid, (s.representative_clip_uid IS NOT NULL) AS is_rep "
            "FROM singcup_clips c LEFT JOIN singcup_streamers s "
            "  ON s.representative_clip_uid = c.clip_uid "
            "WHERE c.event_id=? AND c.owner_channel_id=?", (EV, OWNER))).fetchall()
        flags = {r["clip_uid"]: r["is_rep"] for r in is_rep}
        assert flags["sub"] == 1 and flags["hi"] == 0, \
            "스윕 우선순위도 같은 대표를 가리켜야 한다(split-brain 없음)"
    db(go())


def test_score_and_rank_follow_the_override(db):
    """점수(조회 70 + 하트 30)가 지정된 클립 기준으로 계산된다."""
    async def go():
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await so.set_override(OWNER, "sub")
        await _recompute()
        main = await sc.load_main(limit=200)
        me = next(r for r in main["streamers"] if r["channelId"] == OWNER)
        assert me["heartCount"] == 9 and me["viewCount"] == 36, \
            "화면 수치가 지정한 클립의 것이어야 한다"
    db(go())


def test_snapshot_records_the_override_representative(db):
    """이력 스냅샷도 같은 대표로 남는다 — 증감 기준선이 갈라지지 않는다."""
    async def go():
        from database import get_db
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await so.set_override(OWNER, "sub")

        class _StubClient:
            pass

        async def _fake(client, cid):
            return {"channel_name": "가수", "channel_image_url": "",
                    "follower_count": 0, "verified_mark": 0}
        orig = sc.fetch_channel
        sc.fetch_channel = _fake
        try:
            await sc.recompute_ranking(ts(), client=_StubClient(), save_snapshot=True)
        finally:
            sc.fetch_channel = orig

        rows = await (await (await get_db()).execute(
            "SELECT clip_uid FROM singcup_snapshots WHERE owner_channel_id=?",
            (OWNER,))).fetchall()
        assert rows and all(r["clip_uid"] == "sub" for r in rows)
    db(go())


def test_deletion_fallback_prefers_the_override(db):
    """대표가 아닌 클립이 삭제돼 재선정이 돌아도 지정이 유지된다.

    `_NEW_REP_SQL`은 SQL 한 방으로 대표를 고르는 **두 번째** 지점이다.
    여기가 override를 모르면 삭제 한 번에 지정이 조용히 풀린다.
    """
    async def go():
        from database import get_db
        conn = await get_db()
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await _insert_clip("mid", hearts=12, views=50)
        await so.set_override(OWNER, "sub")
        await _recompute()
        assert await _rep_of() == "sub"

        # 'mid'가 삭제 확정되며 재선정이 돈다 — 자동이라면 'hi'가 뽑힐 자리다.
        row = dict(await (await conn.execute(
            "SELECT * FROM singcup_clips WHERE clip_uid='mid'")).fetchone())
        await conn.execute("UPDATE singcup_clips SET deletion_state='suspected_deleted' "
                           "WHERE clip_uid='mid'")
        await conn.commit()
        row["deletion_state"] = "suspected_deleted"
        await sc._confirm_deleted_and_reselect(row, ts(), "test", 2)
        assert await _rep_of() == "sub", "재선정이 지정을 존중해야 한다"
    db(go())


def test_deletion_of_the_override_clip_falls_back_to_auto(db):
    """지정한 클립 자체가 삭제되면 자동 규칙으로 넘어간다(빈 대표가 되지 않는다)."""
    async def go():
        from database import get_db
        conn = await get_db()
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await so.set_override(OWNER, "sub")
        await _recompute()
        assert await _rep_of() == "sub"

        row = dict(await (await conn.execute(
            "SELECT * FROM singcup_clips WHERE clip_uid='sub'")).fetchone())
        await conn.execute("UPDATE singcup_clips SET deletion_state='suspected_deleted' "
                           "WHERE clip_uid='sub'")
        await conn.commit()
        row["deletion_state"] = "suspected_deleted"
        await sc._confirm_deleted_and_reselect(row, ts(), "test", 2)
        assert await _rep_of() == "hi"
    db(go())


# ── 7. 캐시 무효화와 ETag ───────────────────────────────────────────────────
def test_apply_changes_main_body_and_etag(db, monkeypatch):
    """지정이 반영되면 `/main`의 bytes와 ETag가 달라진다.

    ETag가 그대로면 브라우저가 304를 받아 옛 대표를 계속 보여준다.
    """
    async def go():
        monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 300.0)
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await _recompute()

        before, _ = await sc.load_main_entry(limit=200)
        etag_before, body_before = before["etag"], before["body"]
        # 캐시가 살아 있는지 먼저 확인한다(그래야 무효화 검증이 의미를 갖는다)
        again, source = await sc.load_main_entry(limit=200)
        assert source == "hit" and again["etag"] == etag_before

        await so.set_override(OWNER, "sub")
        await _recompute()                       # 정상 경로가 캐시를 버린다

        after, source2 = await sc.load_main_entry(limit=200)
        assert source2 != "hit", "적용 후에는 캐시가 비어 있어야 한다"
        assert after["etag"] != etag_before, "ETag가 바뀌어야 304로 굳지 않는다"
        assert after["body"] != body_before
    db(go())


def test_clear_also_invalidates_cache(db, monkeypatch):
    async def go():
        monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 300.0)
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)
        await so.set_override(OWNER, "sub")
        await _recompute()
        entry, _ = await sc.load_main_entry(limit=200)
        etag = entry["etag"]

        await so.clear_override(OWNER)
        await _recompute()
        after, _ = await sc.load_main_entry(limit=200)
        assert after["etag"] != etag
    db(go())


# ── 8. 장애 격리 ────────────────────────────────────────────────────────────
def test_override_load_failure_does_not_break_ranking(db, monkeypatch):
    """override 조회가 실패해도 랭킹 재계산 전체가 멈추지 않는다.

    범위가 좁은 쪽(그 스트리머가 한 회차 자동 대표로 보임)을 택한 설계다.
    """
    async def go():
        await _insert_streamer()
        await _insert_clip("hi", hearts=16, views=66)
        await _insert_clip("sub", hearts=9, views=36)

        async def _boom(event_id=EV):
            raise RuntimeError("db locked")
        monkeypatch.setattr(so, "active_override_map", _boom)

        await _recompute()                       # 예외가 밖으로 새면 안 된다
        assert await _rep_of() == "hi"
    db(go())


def test_check_eligible_does_no_external_call(db):
    """검증은 DB만 본다 — 외부 호출은 호출자가 트랜잭션 밖에서 따로 한다."""
    async def go():
        await _insert_clip("c1")

        def _boom(*a, **kw):
            raise AssertionError("검증 경로에서 외부 클라이언트를 만들면 안 된다")

        orig = sc._get_client
        sc._get_client = _boom
        try:
            r, _ = await so.check_clip_eligible(OWNER, "c1")
            assert r == so.REASON_OK
        finally:
            sc._get_client = orig
    db(go())


# ── 9. 개인정보·secret 비노출 ───────────────────────────────────────────────
def test_override_table_stores_no_personal_data(db):
    """문의자 이메일·토큰 같은 것이 들어갈 자리가 없어야 한다."""
    async def go():
        from database import get_db
        cols = {r["name"] for r in await (await (await get_db()).execute(
            "PRAGMA table_info(singcup_representative_overrides)")).fetchall()}
        assert cols == {"id", "event_id", "owner_channel_id", "override_clip_uid",
                        "reason", "created_at", "updated_at", "cleared_at"}
        # 컬럼 집합이 위와 정확히 같다는 것이 1차 방어다. 아래는 나중에 컬럼이
        # 추가될 때 걸리도록 둔 이름 검사다(clip_uid의 'ip' 같은 우연한 일치를
        # 피하려고 단어 단위로 본다).
        words = {w for c in cols for w in c.split("_")}
        assert not (words & {"email", "mail", "token", "secret", "ip",
                             "discord", "user", "requester"})
    db(go())


def test_module_source_has_no_secret_or_contact_handling():
    """override 모듈이 secret·이메일을 다루지 않는다(정적 확인)."""
    import inspect
    src = inspect.getsource(so).lower()
    for bad in ("smtp", "sendmail", "gmail", "@gmail", "admin_secret", "jwt_secret"):
        assert bad not in src
