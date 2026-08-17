"""스트리머 팀/소속 태그 (TAG-1).

여기서 지키려는 계약은 네 가지다.

1. **운영자 입력이 스타일 문자열로 새지 않는다.** 색상은 `#RRGGBB`, 방향은 닫힌
   목록만 통과한다. 이게 뚫리면 화면의 인라인 스타일이 곧 주입 경로가 된다.
2. **중복 지정과 중복 이름은 DB가 막는다.** 애플리케이션 검사만 믿지 않는다.
3. **태그가 없는 응답은 이전과 같다.** 기존 화면 회귀가 없어야 한다.
4. **태그를 바꾸면 캐시가 즉시 비켜난다.** TTL이 끝나기를 기다리지 않는다.

마이그레이션은 append-only라 재실행이 안전해야 한다 — 그것도 여기서 확인한다.
"""
import pytest


@pytest.fixture
def tdb(db):
    """`db` 위에 태그 테이블 초기화를 얹는다.

    공용 `conftest.py`의 정리 목록은 이 두 테이블을 모른다. 거기에 줄을 더하면
    같은 자리를 쓰는 다른 대기 중인 작업과 부딪히므로, **이 모듈 안에서만** 비운다.
    비우지 않으면 유니크 이름 때문에 두 번째 테스트부터 전부 깨진다.
    """
    import streamer_tags as st

    import database

    async def _clear():
        conn = await database.get_db()
        await conn.execute("DELETE FROM streamer_tag_assignments")
        await conn.execute("DELETE FROM streamer_tags")
        await conn.commit()

    db(_clear())
    st.reset_state()
    return db


# ── 마이그레이션 ────────────────────────────────────────────────────────────

def test_migration_is_rerunnable(tdb):
    """init_db를 다시 돌려도 실패하지 않고 데이터도 남는다."""
    import streamer_tags as st

    import database

    async def _go():
        tag = await st.create_tag(name="이세돌", color_mode="solid",
                                  color_start="#38BDF8", color_end=None,
                                  gradient_direction="to-right")
        await database.init_db()          # 두 번째 실행
        await database.init_db()          # 세 번째 실행
        again = await st.list_tags()
        return tag, again

    tag, again = tdb(_go())
    assert [t["id"] for t in again] == [tag["id"]]


def test_tables_and_indexes_exist(tdb):
    import database

    async def _go():
        conn = await database.get_db()
        rows = await (await conn.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE name LIKE 'streamer_tag%' OR name LIKE 'idx_streamer_tag%'"
        )).fetchall()
        return {r["name"] for r in rows}

    names = tdb(_go())
    assert "streamer_tags" in names
    assert "streamer_tag_assignments" in names
    # 목록 화면(채널 기준)과 관리 화면(태그 기준)이 서로 반대 방향으로 읽는다.
    assert "idx_streamer_tag_assign_channel" in names
    assert "idx_streamer_tag_assign_tag" in names


# ── 색상/입력 검증 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "red", "#fff", "#GGGGGG", "rgb(255,0,0)", "#38BDF8; background:url(x)",
    "var(--x)", "url(javascript:alert(1))", "", None, 123,
    "#38BDF8 !important", "linear-gradient(red,blue)",
])
def test_color_rejects_anything_but_hex(bad):
    import streamer_tags as st
    with pytest.raises(st.TagError):
        st.clean_color(bad, field="시작 색상")


def test_color_accepts_hex_and_lowercases():
    import streamer_tags as st
    assert st.clean_color("#38BDF8", field="x") == "#38bdf8"


@pytest.mark.parametrize("bad", ["to right", "to-left", "45deg", "",
                                 "to-right, url(x)", None])
def test_gradient_direction_is_a_closed_list(bad):
    import streamer_tags as st
    with pytest.raises(st.TagError):
        st.clean_style("gradient", "#38BDF8", "#C084FC", bad)


def test_gradient_requires_end_color():
    """mode만 gradient로 두고 끝 색을 빼면 화면에서 태그가 투명해진다."""
    import streamer_tags as st
    with pytest.raises(st.TagError):
        st.clean_style("gradient", "#38BDF8", None, "to-right")


def test_solid_discards_end_color_and_direction():
    import streamer_tags as st
    out = st.clean_style("solid", "#38BDF8", "#C084FC", "to-bottom")
    assert out["color_end"] is None
    assert out["color_mode"] == "solid"


@pytest.mark.parametrize("payload", [
    "<b>이세돌</b>",          # 11자
    "a<script>x",             # 10자
    "'; DROP--",              # 9자
    "<img onerror=x>",        # 15자
])
def test_name_keeps_text_as_text(payload):
    """이름은 **이스케이프하지 않는다.**

    React가 텍스트 노드로 렌더하므로 이스케이프는 그쪽 몫이고, 여기서 `&lt;`로
    바꿔 두면 화면에 그 글자가 그대로 보인다. 저장되는 것은 어디까지나 문자열이고
    SQL은 파라미터 바인딩으로 나간다 — 그 두 가지가 방어선이다.
    (길이 상한에 걸리지 않는 페이로드로 확인한다. 긴 것은 아래 길이 테스트가 막는다.)
    """
    import streamer_tags as st
    assert st.clean_name(payload) == payload


@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>",
    "</span><img src=x onerror=alert(1)>",
    "'; DROP TABLE streamer_tags; --",
])
def test_long_injection_payloads_hit_the_length_limit(payload):
    """전형적인 주입 문자열은 대부분 20자를 넘어 길이 상한에서 먼저 걸린다.

    이건 보안 대책이 아니라 **부수 효과**다 — 방어선은 어디까지나 바인딩과
    텍스트 렌더다. 길이만 믿지 말 것.
    """
    import streamer_tags as st
    assert len(payload) > st.NAME_MAX
    with pytest.raises(st.TagError):
        st.clean_name(payload)


def test_name_strips_control_and_format_chars():
    import streamer_tags as st
    assert st.clean_name("이세​돌\n\t 팀") == "이세돌 팀"


def test_name_length_and_blank():
    import streamer_tags as st
    with pytest.raises(st.TagError):
        st.clean_name("   ")
    with pytest.raises(st.TagError):
        st.clean_name("가" * (st.NAME_MAX + 1))


def test_sql_injection_in_name_does_not_execute(tdb):
    import streamer_tags as st

    import database

    # 길이 상한(20자) 안에 들어오는 주입 문자열을 쓴다 — 길이에서 걸러지면
    # "바인딩이 막았다"를 확인한 게 아니라 "짧았을 뿐"이 된다.
    payload = "a'; DROP TABLE x--"
    assert len(payload) <= st.NAME_MAX

    async def _go():
        created = await st.create_tag(name=payload,
                                      color_mode="solid", color_start="#38BDF8",
                                      color_end=None, gradient_direction="to-right")
        conn = await database.get_db()
        row = await (await conn.execute(
            "SELECT COUNT(*) n FROM streamer_tags")).fetchone()
        return int(row["n"]), created["name"]

    count, name = tdb(_go())
    assert count == 1        # 테이블이 살아 있다
    assert name == payload   # 값은 그대로 저장됐다(문자열로만 다뤄졌다)


def test_slug_keeps_hangul():
    import streamer_tags as st
    # ASCII 변환을 하면 한글 이름의 슬러그가 전부 비어 버린다.
    assert st.slugify("이세돌 · 팀!") == "이세돌-팀"


# ── CRUD ────────────────────────────────────────────────────────────────────

def test_create_and_list(tdb):
    import streamer_tags as st

    async def _go():
        await st.create_tag(name="스텔라이브", color_mode="gradient",
                            color_start="#38BDF8", color_end="#C084FC",
                            gradient_direction="to-bottom-right")
        return await st.list_tags()

    tags = tdb(_go())
    assert len(tags) == 1
    t = tags[0]
    assert t["name"] == "스텔라이브" and t["colorMode"] == "gradient"
    assert t["colorEnd"] == "#c084fc" and t["active"] is True
    assert t["assignedCount"] == 0


def test_duplicate_name_is_rejected_by_db(tdb):
    import streamer_tags as st

    async def _go():
        await st.create_tag(name="이세돌", color_mode="solid",
                            color_start="#38BDF8", color_end=None,
                            gradient_direction="to-right")
        with pytest.raises(st.TagError):
            await st.create_tag(name="이세돌", color_mode="solid",
                                color_start="#FF0000", color_end=None,
                                gradient_direction="to-right")
        # 대소문자만 다른 슬러그도 같은 것으로 본다
        await st.create_tag(name="ISEDOL", color_mode="solid",
                            color_start="#FF0000", color_end=None,
                            gradient_direction="to-right")
        with pytest.raises(st.TagError):
            await st.create_tag(name="isedol", color_mode="solid",
                                color_start="#00FF00", color_end=None,
                                gradient_direction="to-right")
        return await st.list_tags()

    assert len(tdb(_go())) == 2


def test_update_rename_and_deactivate(tdb):
    import streamer_tags as st

    async def _go():
        t = await st.create_tag(name="픽셀", color_mode="solid",
                                color_start="#38BDF8", color_end=None,
                                gradient_direction="to-right")
        renamed = await st.update_tag(t["id"], name="픽셀네트워크")
        off = await st.update_tag(t["id"], active=False)
        visible = await st.list_tags()
        allt = await st.list_tags(include_inactive=True)
        return renamed, off, visible, allt

    renamed, off, visible, allt = tdb(_go())
    assert renamed["name"] == "픽셀네트워크" and renamed["slug"] == "픽셀네트워크"
    assert off["active"] is False
    assert visible == []          # 비활성은 기본 목록에서 빠진다
    assert len(allt) == 1         # 지워지지 않았다


def test_update_missing_tag(tdb):
    import streamer_tags as st

    async def _go():
        with pytest.raises(st.TagError):
            await st.update_tag(9999, name="없음")

    tdb(_go())


# ── 지정 / 해제 / 순서 ──────────────────────────────────────────────────────

CID_A = "a" * 32
CID_B = "b" * 32


def _mk(st, name, color="#38BDF8"):
    return st.create_tag(name=name, color_mode="solid", color_start=color,
                         color_end=None, gradient_direction="to-right")


def test_assign_is_idempotent_and_blocks_duplicates(tdb):
    import streamer_tags as st

    async def _go():
        t = await _mk(st, "이세돌")
        first = await st.assign(CID_A, t["id"])
        second = await st.assign(CID_A, t["id"])
        tags = await st.tags_for_channel(CID_A)
        return first, second, tags

    first, second, tags = tdb(_go())
    assert first["created"] is True
    assert second["created"] is False      # 두 번 눌러도 500이 아니다
    assert len(tags) == 1                  # 행은 하나뿐


def test_assign_rejects_unknown_tag_and_bad_channel(tdb):
    import streamer_tags as st

    async def _go():
        with pytest.raises(st.TagError):
            await st.assign(CID_A, 12345)
        t = await _mk(st, "이세돌")
        with pytest.raises(st.TagError):
            await st.assign("not-a-channel-id", t["id"])

    tdb(_go())


def test_inactive_tag_cannot_be_newly_assigned(tdb):
    import streamer_tags as st

    async def _go():
        t = await _mk(st, "이세돌")
        await st.update_tag(t["id"], active=False)
        with pytest.raises(st.TagError):
            await st.assign(CID_A, t["id"])

    tdb(_go())


def test_inactive_tag_disappears_from_public_but_row_survives(tdb):
    import streamer_tags as st

    import database

    async def _go():
        t = await _mk(st, "이세돌")
        await st.assign(CID_A, t["id"])
        await st.update_tag(t["id"], active=False)
        public = await st.tags_for_channel(CID_A)
        conn = await database.get_db()
        row = await (await conn.execute(
            "SELECT COUNT(*) n FROM streamer_tag_assignments")).fetchone()
        return public, int(row["n"])

    public, rows = tdb(_go())
    assert public == []       # 공개 화면에서는 사라진다
    assert rows == 1          # 지정 이력은 남아 되돌릴 수 있다


def test_max_tags_per_streamer(tdb):
    import streamer_tags as st

    async def _go():
        ids = []
        for i in range(st.MAX_TAGS_PER_STREAMER + 1):
            t = await _mk(st, f"태그{i}")
            ids.append(t["id"])
        for tid in ids[:st.MAX_TAGS_PER_STREAMER]:
            await st.assign(CID_A, tid)
        with pytest.raises(st.TagError):
            await st.assign(CID_A, ids[-1])
        return await st.tags_for_channel(CID_A)

    assert len(tdb(_go())) == st.MAX_TAGS_PER_STREAMER


def test_unassign_only_removes_the_link(tdb):
    import streamer_tags as st

    import database

    async def _go():
        t = await _mk(st, "이세돌")
        await st.assign(CID_A, t["id"])
        res = await st.unassign(CID_A, t["id"])
        again = await st.unassign(CID_A, t["id"])
        conn = await database.get_db()
        row = await (await conn.execute(
            "SELECT COUNT(*) n FROM streamer_tags")).fetchone()
        return res, again, int(row["n"]), await st.tags_for_channel(CID_A)

    res, again, tag_rows, tags = tdb(_go())
    assert res["removed"] is True
    assert again["removed"] is False    # 두 번째는 조용히 no-op
    assert tag_rows == 1                # 태그 자체는 살아 있다
    assert tags == []


def test_reorder_changes_display_order(tdb):
    import streamer_tags as st

    async def _go():
        a = await _mk(st, "가")
        b = await _mk(st, "나")
        c = await _mk(st, "다")
        for t in (a, b, c):
            await st.assign(CID_A, t["id"])
        before = [t["name"] for t in await st.tags_for_channel(CID_A)]
        await st.reorder(CID_A, [c["id"], a["id"], b["id"]])
        after = [t["name"] for t in await st.tags_for_channel(CID_A)]
        return before, after

    before, after = tdb(_go())
    assert before == ["가", "나", "다"]
    assert after == ["다", "가", "나"]


def test_reorder_rejects_empty(tdb):
    import streamer_tags as st

    async def _go():
        with pytest.raises(st.TagError):
            await st.reorder(CID_A, [])

    tdb(_go())


# ── 조회 / 직렬화 ───────────────────────────────────────────────────────────

def test_public_serialization_has_no_operational_fields(tdb):
    """공개 응답에 운영 메타(active·생성시각)나 비밀이 섞이면 안 된다."""
    import streamer_tags as st

    async def _go():
        t = await _mk(st, "이세돌")
        await st.assign(CID_A, t["id"])
        return (await st.tags_for_channel(CID_A))[0]

    pub = tdb(_go())
    # `colorStops`는 다중 그라데이션(ADMIN-GROUP 3-1)에서 더해진 **표시용** 필드다.
    # 운영 메타가 아니므로 이 목록에 들어간다. 구형 4필드는 하위 호환으로 남아 있다.
    assert set(pub) == {"id", "name", "slug", "kind", "colorMode",
                        "colorStart", "colorEnd", "gradientDirection", "colorStops"}
    # 진짜로 새면 안 되는 것들 — 이 검사가 이 테스트의 본체다.
    assert not ({"active", "createdAt", "updatedAt", "excludeFromRanking",
                 "assignedCount"} & set(pub))


def test_tags_for_channels_is_one_query_shaped(tdb):
    """여러 채널을 한 번에 받아 dict로 돌려준다(호출부가 N+1을 만들 수 없게)."""
    import streamer_tags as st

    async def _go():
        a = await _mk(st, "가")
        b = await _mk(st, "나")
        await st.assign(CID_A, a["id"])
        await st.assign(CID_B, b["id"])
        return await st.tags_for_channels([CID_A, CID_B, "z" * 32, "쓰레기"])

    m = tdb(_go())
    assert [t["name"] for t in m[CID_A]] == ["가"]
    assert [t["name"] for t in m[CID_B]] == ["나"]
    assert "z" * 32 not in m          # 태그 없는 채널은 키 자체가 없다


def test_attach_tags_gives_empty_list_when_no_tags(tdb):
    """태그 없는 스트리머는 `[]`가 된다 — `undefined`면 프론트 분기가 늘어난다."""
    import streamer_tags as st

    async def _go():
        rows = [{"chzzk_channel_id": CID_A, "channel_name": "테스트"}]
        await st.attach_tags(rows)
        return rows[0]

    row = tdb(_go())
    assert row["team_tags"] == []
    # 기존 필드는 그대로다(회귀 없음)
    assert row["channel_name"] == "테스트"


def test_attach_tags_does_not_clobber_broadcast_tags(tdb):
    """**`tags`를 덮어쓰지 않는다.**

    `/api/rising/newcomers`는 이미 `tags`로 치지직 **방송 태그**(문자열 배열)를
    내보낸다. 팀 태그를 같은 키로 붙이면 그 값이 조용히 사라져 기존 화면이 깨진다 —
    실제로 한 번 그렇게 만들었다가 QA에서 잡았고, 그래서 기본 필드를 `team_tags`로
    두었다. 이 테스트가 그 결정을 고정한다.
    """
    import streamer_tags as st

    async def _go():
        t = await _mk(st, "이세돌")
        await st.assign(CID_A, t["id"])
        rows = [{"chzzk_channel_id": CID_A, "tags": ["롤", "종합게임"]}]
        await st.attach_tags(rows)
        return rows[0]

    row = tdb(_go())
    assert row["tags"] == ["롤", "종합게임"]          # 방송 태그가 살아 있다
    assert [t["name"] for t in row["team_tags"]] == ["이세돌"]


# ── 검색 ────────────────────────────────────────────────────────────────────

def test_search_requires_min_length(tdb):
    import streamer_tags as st

    async def _go():
        with pytest.raises(st.TagError):
            await st.search_streamers("가")

    tdb(_go())


def test_search_by_name_and_id_and_escapes_wildcards(tdb):
    import streamer_tags as st

    import database

    async def _go():
        conn = await database.get_db()
        for cid, name in ((CID_A, "테스트스트리머"), (CID_B, "100%달성")):
            await conn.execute(
                "INSERT OR REPLACE INTO rising_channel_stats "
                "(chzzk_channel_id, first_seen, last_seen, channel_name) "
                "VALUES (?,?,?,?)", (cid, 1, 2, name))
        await conn.commit()
        by_name = await st.search_streamers("테스트")
        by_id = await st.search_streamers(CID_A)
        # `%`만 넣으면 전부 긁히던 것을 이스케이프로 막는다
        wildcard = await st.search_streamers("%%")
        literal = await st.search_streamers("100%")
        return by_name, by_id, wildcard, literal

    by_name, by_id, wildcard, literal = tdb(_go())
    assert [s["channelId"] for s in by_name] == [CID_A]
    assert [s["channelId"] for s in by_id] == [CID_A]
    assert wildcard == []                       # 와일드카드가 먹지 않는다
    assert [s["channelId"] for s in literal] == [CID_B]


# ── 캐시 세대 ───────────────────────────────────────────────────────────────

def test_version_bumps_on_every_mutation(tdb):
    """태그가 바뀌면 세대가 올라간다 — 랭킹 TTL 캐시가 즉시 비켜나는 근거."""
    import streamer_tags as st

    async def _go():
        seen = [st.version()]
        t = await _mk(st, "이세돌")
        seen.append(st.version())
        await st.assign(CID_A, t["id"])
        seen.append(st.version())
        await st.reorder(CID_A, [t["id"]])
        seen.append(st.version())
        await st.unassign(CID_A, t["id"])
        seen.append(st.version())
        await st.update_tag(t["id"], name="이세돌팀")
        seen.append(st.version())
        return seen

    seen = tdb(_go())
    assert seen == sorted(seen) and len(set(seen)) == len(seen)
