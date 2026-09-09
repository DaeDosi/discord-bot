"""VTUBER-1 — '버튜버' 소속 그룹과 라이브 태그 수집.

운영자가 Nexadmin에서 버튼을 눌렀을 때만 도는 **추가 전용** 수집이다.

이 파일이 고정하는 것은 네 가지다.

1. **그룹은 하나뿐이다.** 없으면 요청 안에서 한 번만 만들고, 있으면 그대로 쓴다.
   `버튜버`가 두 개 생기면 멤버가 갈라져 어느 쪽이 진짜인지 알 수 없게 된다.
2. **판정 근거가 정확하다.** '지금 라이브'는 `rising_live_snapshots`의 최신 회차,
   태그는 `버튜버` **완전 일치**다. 방송 제목·닉네임·부분 문자열은 근거가 아니다.
3. **추가만 한다.** 오프라인이 된 멤버도, 태그를 뗀 멤버도, 손으로 넣은 멤버도
   지우지 않는다. 이 기능으로 사람이 사라지면 복구할 방법이 없다.
4. **거짓 성공이 없다.** 건너뛴 것은 건너뛴 것으로 센다.
"""
import asyncio

import pytest

TAG_COL = "버튜버"

# **고정 시계.** 벽시계 `time.time()`을 쓰면 신선도 판정이 실행 시각에 따라 흔들려
# flaky가 된다(이 저장소에는 이미 그렇게 깨지는 테스트가 있다). 수집기 시각과
# '지금'을 둘 다 상수로 고정하고, `collect_vtuber_live_members(now=...)`로 주입한다.
NOW = 1_800_000_000          # 2027-01-15T13:20:00Z / 2027-01-15T22:20:00+09:00
TS = NOW - 60                # 1분 전 회차 = 확실히 fresh


def max_age():
    """허용 상한(초) — 구현과 같은 출처에서 읽는다(테스트가 값을 다시 정의하지 않는다)."""
    import streamer_tags as st
    return st.live_max_age_seconds()


@pytest.fixture
def tdb(db):
    """태그·스냅샷 표를 비운다(공용 conftest는 이 표들을 모른다)."""
    import streamer_tags as st

    import database

    async def _clear():
        conn = await database.get_db()
        for t in ("streamer_tag_assignments", "streamer_tags",
                  "rising_live_snapshots", "rising_collect_runs",
                  "rising_channel_stats"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.commit()

    db(_clear())
    st.reset_state()
    st.reset_group_cache()
    return db


async def _seed_live(rows, *, ts=TS, ok=True):
    """(channel_id, tags) 목록을 한 수집 회차로 심는다.

    `tags`는 치지직이 주는 그대로의 **쉼표 join 문자열**이다(수집기가 저장하는 모양).
    """
    from database import get_db
    conn = await get_db()
    await conn.execute(
        "INSERT INTO rising_collect_runs (collected_at, live_count, total_viewers, ok)"
        " VALUES (?,?,?,?)", (ts, len(rows), 0, 1 if ok else 0))
    for cid, tags in rows:
        await conn.execute(
            "INSERT INTO rising_live_snapshots (collected_at, chzzk_channel_id,"
            " channel_name, tags, live_title) VALUES (?,?,?,?,?)",
            (ts, cid, f"채널-{cid[:4]}", tags, "제목"))
    await conn.commit()


def _cid(n: int) -> str:
    return f"{n:032x}"


async def _members(tag_id):
    from database import get_db
    conn = await get_db()
    rows = await (await conn.execute(
        "SELECT streamer_channel_id FROM streamer_tag_assignments WHERE tag_id=?",
        (tag_id,))).fetchall()
    return {r["streamer_channel_id"] for r in rows}


async def _group_rows(name=TAG_COL):
    from database import get_db
    conn = await get_db()
    return await (await conn.execute(
        "SELECT * FROM streamer_tags WHERE name=? COLLATE NOCASE", (name,))).fetchall()


# ── 1. 그룹 생성·재사용 ──────────────────────────────────────────────────────

def test_creates_group_exactly_once(tdb):
    """그룹이 없으면 이번 요청 안에서 **정확히 한 번** 만든다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL)])
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _group_rows()

    res, rows = tdb(_go())
    assert res["groupCreated"] is True
    assert len(rows) == 1
    assert rows[0]["name"] == TAG_COL
    assert res["groupId"] == rows[0]["id"]


def test_reuses_existing_group(tdb):
    """이미 있으면 새로 만들지 않는다 — 이름이 같은 그룹이 둘이 되면 안 된다."""
    import streamer_tags as st

    async def _go():
        g = await st.create_tag(name=TAG_COL, color_mode="solid",
                                color_start="#8B5CF6", color_end=None,
                                gradient_direction="to-right")
        await _seed_live([(_cid(1), TAG_COL)])
        res = await st.collect_vtuber_live_members(now=NOW)
        return g, res, await _group_rows()

    g, res, rows = tdb(_go())
    assert res["groupCreated"] is False
    assert res["groupId"] == g["id"]
    assert len(rows) == 1


def test_created_group_is_not_ranking_excluded(tdb):
    """랭킹 제외 그룹이 **아니다.** 기본값이 켜지면 기존 랭킹이 조용히 바뀐다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL)])
        await st.collect_vtuber_live_members(now=NOW)
        return (await _group_rows())[0]

    row = tdb(_go())
    assert int(row["exclude_from_ranking"]) == 0


def test_inactive_group_is_reported_not_silently_recreated(tdb):
    """비활성 그룹을 만나면 **실패로 알린다.**

    새로 만들면 같은 이름이 둘이 되고, 몰래 활성화하면 운영자가 내린 결정을
    코드가 되돌리는 것이다. 둘 다 하지 않는다.
    """
    import streamer_tags as st

    async def _go():
        g = await st.create_tag(name=TAG_COL, color_mode="solid",
                                color_start="#8B5CF6", color_end=None,
                                gradient_direction="to-right")
        await st.update_tag(g["id"], active=False)
        await _seed_live([(_cid(1), TAG_COL)])
        with pytest.raises(st.TagError):
            await st.collect_vtuber_live_members(now=NOW)
        return await _group_rows()

    rows = tdb(_go())
    assert len(rows) == 1
    assert int(rows[0]["active"]) == 0


# ── 2. 후보 판정 ────────────────────────────────────────────────────────────

def test_only_live_rows_with_exact_tag(tdb):
    """최신 회차 + `버튜버` 완전 일치만 후보다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([
            (_cid(1), TAG_COL),                  # ○ 정확히 일치
            (_cid(2), f"게임,{TAG_COL},노래"),     # ○ 목록 안에 있음
            (_cid(3), f" {TAG_COL} "),           # ○ 공백만 다름 → trim
            (_cid(4), "버튜버지망생"),             # × 부분 문자열
            (_cid(5), "신입버튜버"),               # × 부분 문자열
            (_cid(6), ""),                       # × 태그 없음
            (_cid(7), "게임,토크"),                # × 다른 태그만
        ])
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(res["groupId"])

    res, members = tdb(_go())
    assert members == {_cid(1), _cid(2), _cid(3)}
    assert res["liveCandidates"] == 3
    assert res["added"] == 3


def test_title_or_name_containing_vtuber_is_not_enough(tdb):
    """방송 제목·닉네임에 '버튜버'가 있다고 넣지 않는다."""
    import streamer_tags as st

    async def _go():
        from database import get_db
        await _seed_live([(_cid(1), "게임")])
        conn = await get_db()
        await conn.execute(
            "UPDATE rising_live_snapshots SET live_title=?, channel_name=?"
            " WHERE chzzk_channel_id=?", ("버튜버 방송입니다", "버튜버지망생", _cid(1)))
        await conn.commit()
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(res["groupId"])

    res, members = tdb(_go())
    assert members == set()
    assert res["liveCandidates"] == 0
    assert res["added"] == 0


def test_offline_streamers_are_excluded(tdb):
    """이전 회차에만 있던(=지금 꺼진) 스트리머는 후보가 아니다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL)], ts=TS - 1200)   # 이전 회차
        await _seed_live([(_cid(2), TAG_COL)], ts=TS)          # 최신 회차
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(res["groupId"])

    res, members = tdb(_go())
    assert members == {_cid(2)}
    assert res["collectedAt"] == TS


def test_duplicate_channel_ids_in_one_request_are_deduped(tdb):
    """한 요청 안에 같은 channel_id가 두 번 와도 한 번만 센다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL), (_cid(1), f"{TAG_COL},노래")])
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(res["groupId"])

    res, members = tdb(_go())
    assert res["liveCandidates"] == 1
    assert res["added"] == 1
    assert members == {_cid(1)}


def test_channel_id_is_the_dedupe_key_not_the_name(tdb):
    """닉네임이 같아도 channel_id가 다르면 서로 다른 사람이다."""
    import streamer_tags as st

    async def _go():
        from database import get_db
        await _seed_live([(_cid(1), TAG_COL), (_cid(2), TAG_COL)])
        conn = await get_db()
        await conn.execute("UPDATE rising_live_snapshots SET channel_name='같은이름'")
        await conn.commit()
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(res["groupId"])

    res, members = tdb(_go())
    assert members == {_cid(1), _cid(2)}
    assert res["added"] == 2


def test_malformed_channel_id_is_counted_as_skipped(tdb):
    """식별자가 깨진 행은 조용히 버리지 않고 **건너뛴 수로 센다.**"""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL), ("not-a-channel-id", TAG_COL)])
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(res["groupId"])

    res, members = tdb(_go())
    assert members == {_cid(1)}
    assert res["added"] == 1
    assert res["invalidOrSkipped"] == 1
    assert any(e["kind"] == "invalid_channel_id" and e["count"] == 1
               for e in res["errors"])


# ── 3. 멱등·동시성 ──────────────────────────────────────────────────────────

def test_second_run_adds_nothing(tdb):
    """두 번 연속 실행하면 두 번째의 신규 추가는 0이다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL), (_cid(2), TAG_COL)])
        first = await st.collect_vtuber_live_members(now=NOW)
        second = await st.collect_vtuber_live_members(now=NOW)
        return first, second, await _members(first["groupId"])

    first, second, members = tdb(_go())
    assert first["added"] == 2
    assert second["added"] == 0
    assert second["alreadyPresent"] == 2
    assert second["groupCreated"] is False
    assert len(members) == 2


def test_existing_member_is_not_added_again(tdb):
    """이미 멤버면 다시 붙이지 않는다(행도 늘지 않는다)."""
    import streamer_tags as st

    async def _go():
        g = await st.create_tag(name=TAG_COL, color_mode="solid",
                                color_start="#8B5CF6", color_end=None,
                                gradient_direction="to-right")
        await st.assign(_cid(1), g["id"])
        await _seed_live([(_cid(1), TAG_COL), (_cid(2), TAG_COL)])
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(g["id"])

    res, members = tdb(_go())
    assert res["alreadyPresent"] == 1
    assert res["added"] == 1
    assert members == {_cid(1), _cid(2)}


def test_concurrent_runs_do_not_duplicate_rows(tdb):
    """동시에 눌러도 그룹도 멤버도 중복 생성되지 않는다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(i), TAG_COL) for i in range(1, 6)])
        results = await asyncio.gather(*(st.collect_vtuber_live_members(now=NOW)
                                         for _ in range(4)))
        groups = await _group_rows()
        return results, groups, await _members(results[0]["groupId"])

    results, groups, members = tdb(_go())
    assert len(groups) == 1, "그룹이 여러 번 만들어졌다"
    assert len(members) == 5
    # 새로 만들었다고 보고한 요청은 많아야 하나다.
    assert sum(1 for r in results if r["groupCreated"]) == 1
    # 추가했다고 보고한 총합이 실제 행 수와 같다(거짓 성공 금지).
    assert sum(r["added"] for r in results) == 5


# ── 4. 추가 전용 ────────────────────────────────────────────────────────────

def test_never_removes_existing_members(tdb):
    """오프라인이 됐거나 태그를 뗐거나 손으로 넣은 멤버를 지우지 않는다."""
    import streamer_tags as st

    async def _go():
        g = await st.create_tag(name=TAG_COL, color_mode="solid",
                                color_start="#8B5CF6", color_end=None,
                                gradient_direction="to-right")
        # 손으로 넣은 멤버 + 지금은 꺼져 있는 멤버
        await st.assign(_cid(90), g["id"])   # 이번 회차에 아예 없음(오프라인)
        await st.assign(_cid(91), g["id"])   # 라이브지만 태그를 뗌
        await _seed_live([(_cid(91), "게임"), (_cid(1), TAG_COL)])
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(g["id"])

    res, members = tdb(_go())
    assert members == {_cid(90), _cid(91), _cid(1)}
    assert res["added"] == 1


def test_other_groups_are_untouched(tdb):
    """다른 그룹의 멤버·설정을 건드리지 않는다."""
    import streamer_tags as st

    async def _go():
        other = await st.create_tag(name="이세돌", color_mode="solid",
                                    color_start="#38BDF8", color_end=None,
                                    gradient_direction="to-right")
        await st.assign(_cid(50), other["id"])
        await _seed_live([(_cid(1), TAG_COL)])
        await st.collect_vtuber_live_members(now=NOW)
        return await _members(other["id"])

    assert tdb(_go()) == {_cid(50)}


# ── 5. 실패를 성공으로 표시하지 않는다 ───────────────────────────────────────

def test_tag_limit_is_reported_as_skipped(tdb):
    """태그 상한에 걸린 스트리머는 **추가된 것으로 세지 않는다.**"""
    import streamer_tags as st

    async def _go():
        full = [await st.create_tag(name=f"그룹{i}", color_mode="solid",
                                    color_start="#38BDF8", color_end=None,
                                    gradient_direction="to-right")
                for i in range(st.MAX_TAGS_PER_STREAMER)]
        for g in full:
            await st.assign(_cid(1), g["id"])
        await _seed_live([(_cid(1), TAG_COL), (_cid(2), TAG_COL)])
        res = await st.collect_vtuber_live_members(now=NOW)
        return res, await _members(res["groupId"])

    res, members = tdb(_go())
    assert res["added"] == 1
    assert res["invalidOrSkipped"] == 1
    assert members == {_cid(2)}
    assert any(e["kind"] == "tag_limit" for e in res["errors"])


def test_fresh_run_with_no_candidates_is_success_not_error(tdb):
    """fresh 회차인데 버튜버가 0명이면 **정상 처리**다(added=0). 오류가 아니다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), "게임"), (_cid(2), "노래")])
        return await st.collect_vtuber_live_members(now=NOW)

    res = tdb(_go())
    assert res["liveCandidates"] == 0
    assert res["added"] == 0
    assert res["alreadyPresent"] == 0
    assert res["errors"] == []
    assert res["collectedAt"] == TS
    assert res["groupCreated"] is True     # fresh면 그룹은 만든다


def test_response_carries_no_secrets(tdb):
    """응답에 원시 외부 응답·토큰·쿠키가 섞이지 않는다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL)])
        return await st.collect_vtuber_live_members(now=NOW)

    res = tdb(_go())
    assert set(res) == {
        "groupId", "groupName", "groupCreated", "collectedAt",
        "ageSeconds", "maxAgeSeconds",
        "liveCandidates", "added", "alreadyPresent", "invalidOrSkipped",
        "memberCount", "errors",
    }
    for e in res["errors"]:
        assert set(e) == {"kind", "count"}


# ── 6. 라이브 스냅샷 신선도 (fail-closed) ───────────────────────────────────
#
# 이 기능이 답하는 질문은 "**지금** 방송 중이면서 버튜버 태그인 사람"이다. 수집기가
# 멈춰 있으면 최신 회차는 몇 시간 전 라이브 목록이고, 그걸 그대로 넣으면 "지금
# 방송 중"이라는 전제가 조용히 거짓이 된다. 추가 전용이라 되돌리려면 사람이 손으로
# 지워야 하므로, **의심스러우면 아무것도 하지 않는다**(fail-closed).


def _n_groups_and_members():
    """DB write가 진짜 0인지 세는 헬퍼 — 그룹·멤버 둘 다 본다."""
    from database import get_db

    async def _go():
        conn = await get_db()
        g = (await (await conn.execute(
            "SELECT COUNT(*) n FROM streamer_tags")).fetchone())["n"]
        a = (await (await conn.execute(
            "SELECT COUNT(*) n FROM streamer_tag_assignments")).fetchone())["n"]
        return int(g), int(a)

    return _go()


def test_freshness_threshold_reuses_collector_interval(tdb):
    """상한을 새로 정의하지 않는다 — 수집 주기 × 1.5(저장소 기존 판정식)다."""
    import streamer_tags as st
    from rising_collector import COLLECT_INTERVAL

    # `singcup_clips`의 라이브 신선도(`(now - live_at) > _LIVE_INTERVAL * 1.5`)와
    # **같은 출처·같은 배수**여야 한다. 여기서만 다른 숫자를 쓰면 같은 데이터에
    # 대해 화면과 이 기능이 서로 다른 판정을 내린다.
    assert st.LIVE_STALE_FACTOR == 1.5
    assert st.live_max_age_seconds() == int(COLLECT_INTERVAL * 1.5)


def test_no_successful_run_fails_closed(tdb):
    """성공 회차가 하나도 없으면 오류. 그룹도 멤버도 만들지 않는다."""
    import streamer_tags as st

    async def _go():
        with pytest.raises(st.LiveDataError) as ei:
            await st.collect_vtuber_live_members(now=NOW)
        return ei.value, await _n_groups_and_members()

    err, (groups, members) = tdb(_go())
    assert err.code == "no_live_snapshot"
    assert err.collected_at is None
    assert err.stale is False              # '오래됨'이 아니라 '없음'이다
    assert groups == 0 and members == 0    # DB write 0


def test_failed_run_only_fails_closed(tdb):
    """ok=0 회차만 있으면 성공 회차가 없는 것과 같다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL)], ok=False)
        with pytest.raises(st.LiveDataError) as ei:
            await st.collect_vtuber_live_members(now=NOW)
        return ei.value, await _n_groups_and_members()

    err, (groups, members) = tdb(_go())
    assert err.code == "no_live_snapshot"
    assert groups == 0 and members == 0


def test_stale_run_fails_closed_and_reports_state(tdb):
    """오래된 회차면 오류 + `collectedAt`/stale을 안전한 형태로 알린다."""
    import streamer_tags as st

    async def _go():
        ts = NOW - max_age() - 3600        # 상한보다 1시간 더 오래됨
        await _seed_live([(_cid(1), TAG_COL)], ts=ts)
        with pytest.raises(st.LiveDataError) as ei:
            await st.collect_vtuber_live_members(now=NOW)
        return ts, ei.value, await _n_groups_and_members()

    ts, err, (groups, members) = tdb(_go())
    assert err.code == "stale_live_snapshot"
    assert err.stale is True
    assert err.collected_at == ts
    assert err.age_seconds == NOW - ts
    assert err.max_age_seconds == max_age()
    assert groups == 0 and members == 0, "stale 요청이 빈 그룹을 만들면 안 된다"
    # 응답에 실릴 형태에도 원시 데이터·비밀이 없어야 한다.
    assert set(err.as_detail()) == {
        "code", "message", "stale", "collectedAt", "ageSeconds", "maxAgeSeconds"}


def test_stale_run_with_vtubers_adds_nothing(tdb):
    """오래된 회차에 버튜버가 가득해도 **한 명도 추가하지 않는다.**"""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(i), TAG_COL) for i in range(1, 26)],
                         ts=NOW - max_age() - 1)
        with pytest.raises(st.LiveDataError):
            await st.collect_vtuber_live_members(now=NOW)
        return await _n_groups_and_members()

    groups, members = tdb(_go())
    assert groups == 0 and members == 0


def test_boundary_just_inside_is_allowed(tdb):
    """경계 직전(나이 == 상한)은 허용한다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL)], ts=NOW - max_age())
        return await st.collect_vtuber_live_members(now=NOW)

    res = tdb(_go())
    assert res["added"] == 1
    assert res["ageSeconds"] == max_age()
    assert res["maxAgeSeconds"] == max_age()


def test_boundary_one_second_over_is_rejected(tdb):
    """경계를 1초라도 넘으면 거절한다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL)], ts=NOW - max_age() - 1)
        with pytest.raises(st.LiveDataError) as ei:
            await st.collect_vtuber_live_members(now=NOW)
        return ei.value, await _n_groups_and_members()

    err, (groups, members) = tdb(_go())
    assert err.code == "stale_live_snapshot"
    assert groups == 0 and members == 0


@pytest.mark.parametrize("label,now_ts", [
    # KST 자정 직후 — 회차 쪽은 전날(KST)이 된다
    ("kst-midnight", 1_803_827_410),
    # UTC 자정 직후 — 회차 쪽은 전날(UTC)이 된다
    ("utc-midnight", 1_803_859_210),
])
def test_boundary_is_epoch_difference_not_calendar_day(tdb, label, now_ts):
    """판정은 **에포크 차이 하나**다 — 날짜/시간대 경계를 넘어도 결과가 같다.

    저장 값(`rising_collect_runs.collected_at`)이 Unix 에포크 정수이므로 비교에
    datetime이 끼어들지 않는다. naive/aware 혼용 사고가 날 자리가 없다는 뜻이고,
    이 테스트가 그 성질을 고정한다.
    """
    import streamer_tags as st

    import database

    async def _ok():
        await _seed_live([(_cid(1), TAG_COL)], ts=now_ts - max_age())
        return await st.collect_vtuber_live_members(now=now_ts)

    res = tdb(_ok())
    assert res["added"] == 1, f"{label}: 경계 직전인데 거절됐다"

    async def _reject():
        conn = await database.get_db()
        for t in ("streamer_tag_assignments", "streamer_tags",
                  "rising_live_snapshots", "rising_collect_runs"):
            await conn.execute(f"DELETE FROM {t}")
        await conn.commit()
        st.reset_state()
        st.reset_group_cache()
        await _seed_live([(_cid(1), TAG_COL)], ts=now_ts - max_age() - 1)
        with pytest.raises(st.LiveDataError):
            await st.collect_vtuber_live_members(now=now_ts)
        return await _n_groups_and_members()

    groups, members = tdb(_reject())
    assert (groups, members) == (0, 0), f"{label}: 경계 초과인데 통과했다"


def test_recovers_after_fresh_run_arrives(tdb):
    """오류가 난 뒤 fresh 회차가 들어오면 다음 실행은 정상 동작한다."""
    import streamer_tags as st

    async def _go():
        await _seed_live([(_cid(1), TAG_COL)], ts=NOW - max_age() - 60)
        with pytest.raises(st.LiveDataError):
            await st.collect_vtuber_live_members(now=NOW)
        before = await _n_groups_and_members()
        # 수집기가 회복해 새 회차를 남겼다
        await _seed_live([(_cid(1), TAG_COL), (_cid(2), TAG_COL)], ts=NOW - 30)
        res = await st.collect_vtuber_live_members(now=NOW)
        return before, res, await _members(res["groupId"])

    before, res, members = tdb(_go())
    assert before == (0, 0)
    assert res["groupCreated"] is True
    assert res["added"] == 2
    assert members == {_cid(1), _cid(2)}


def test_stale_check_runs_before_group_creation(tdb):
    """이미 그룹이 있어도 stale이면 멤버를 붙이지 않는다(검사가 먼저다)."""
    import streamer_tags as st

    async def _go():
        g = await st.create_tag(name=TAG_COL, color_mode="solid",
                                color_start="#8B5CF6", color_end=None,
                                gradient_direction="to-right")
        await st.assign(_cid(50), g["id"])            # 기존 수동 멤버
        await _seed_live([(_cid(1), TAG_COL)], ts=NOW - max_age() - 1)
        with pytest.raises(st.LiveDataError):
            await st.collect_vtuber_live_members(now=NOW)
        return await _members(g["id"])

    # 기존 멤버는 그대로 — stale 오류가 멤버를 지우지도 않는다.
    assert tdb(_go()) == {_cid(50)}


# ── 6. 라우터 ───────────────────────────────────────────────────────────────

def test_route_requires_owner():
    """수집 엔드포인트는 OWNER 전용 POST다."""
    import inspect

    import routers.admin_router as ar

    fn = ar.streamer_tags_collect_vtuber
    assert "_require_owner" in str(inspect.signature(fn))
    # 조회가 아니라 mutation이다 — GET으로 열어 두면 링크 클릭으로도 돈다.
    routes = [r for r in ar.router.routes if getattr(r, "endpoint", None) is fn]
    assert routes and routes[0].methods == {"POST"}


def test_route_maps_stale_to_409_with_structured_detail():
    """신선도 실패는 400이 아니라 409 + 구조화된 detail이다.

    요청 자체는 올바르고 **지금 판정할 수 있는 상태가 아닐 뿐**이다. 화면이
    '입력이 잘못됐다'와 '수집기가 밀렸다'를 구분해야 하므로 코드로 나눈다.
    detail 모양은 `lib/api.ts`의 기존 계약(`{code, message}`)을 그대로 쓴다.
    """
    import inspect

    import routers.admin_router as ar

    src = inspect.getsource(ar.streamer_tags_collect_vtuber)
    assert "LiveDataError" in src, "신선도 오류를 따로 잡아야 한다"
    assert "409" in src
    assert "as_detail()" in src
    # 순서가 뒤집히면 TagError 분기가 먼저 삼켜 409가 영영 나오지 않는다.
    assert src.index("LiveDataError") < src.index("except st.TagError")


# ── 7. 라우터 실동작 (TestClient) ────────────────────────────────────────────
#
# 위의 소스 검사만으로는 "실제로 403/405가 나가는가"를 알 수 없다. 이 API는 운영
# 데이터를 바꾸는 유일한 새 경로이므로 인증·메서드·응답 본문을 실제로 태워 본다.

OWNER = "111111111111111111"
COLLECT_PATH = "/api/admin/streamer-tags/vtuber/collect"


@pytest.fixture
def client(tdb, monkeypatch):
    import routers.admin_router as ar
    from deps import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(ar, "_OWNER_ID", OWNER)
    app = FastAPI()
    app.include_router(ar.router)
    app.state._dep = get_current_user
    c = TestClient(app, raise_server_exceptions=True)
    yield c
    c.app.dependency_overrides.clear()


def _as(client, sub):
    client.app.dependency_overrides[client.app.state._dep] = lambda: {"sub": sub}
    return client


def test_route_rejects_non_owner(client, tdb):
    """OWNER가 아니면 403. 그리고 **DB를 건드리지 않는다.**"""
    r = _as(client, "999").post(COLLECT_PATH)
    assert r.status_code == 403
    assert tdb(_n_groups_and_members()) == (0, 0)


def test_route_rejects_get(client):
    """GET은 405 — 링크 클릭·프리페치로 그룹이 만들어지면 안 된다."""
    assert _as(client, OWNER).get(COLLECT_PATH).status_code == 405


def test_route_returns_409_and_writes_nothing_when_no_snapshot(client):
    """수집 회차가 없으면 409 + 구조화 detail, DB write 0."""
    r = _as(client, OWNER).post(COLLECT_PATH)
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "no_live_snapshot"
    assert d["collectedAt"] is None
    assert d["stale"] is False
    assert set(d) == {"code", "message", "stale", "collectedAt",
                      "ageSeconds", "maxAgeSeconds"}
    assert r.headers["content-type"].startswith("application/json")


def test_route_response_has_no_secrets(client, tdb):
    """성공 응답에 토큰·쿠키·Authorization·원시 외부 데이터가 없다."""
    import time

    now = int(time.time())

    async def _seed():
        await _seed_live([(_cid(1), TAG_COL)], ts=now - 30)

    tdb(_seed())
    r = _as(client, OWNER).post(COLLECT_PATH)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert set(body) == {
        "ok", "groupId", "groupName", "groupCreated", "collectedAt",
        "ageSeconds", "maxAgeSeconds", "liveCandidates", "added",
        "alreadyPresent", "invalidOrSkipped", "memberCount", "errors",
    }
    blob = r.text.lower()
    for word in ("token", "secret", "authorization", "cookie", "bearer",
                 "password", "set-cookie"):
        assert word not in blob, f"응답에 {word}가 들어 있다"
    assert "set-cookie" not in {k.lower() for k in r.headers}
