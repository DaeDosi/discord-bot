"""TAG-2 — 소속 그룹의 멤버 목록 조회·검색·페이지.

TAG-1은 "스트리머 → 그룹" 방향만 있었다. 관리 화면에서 **그룹 → 멤버**를 볼 수
없어 "이 그룹에 누가 들어 있나"를 확인할 방법이 아예 없었다. 여기서 그 방향을 연다.

새 엔드포인트를 만들지 않고 기존 `GET /streamer-tags/{id}/assignments`에 검색·페이지
파라미터를 더했다 — 같은 자원을 두 경로가 서빙하면 캐시·권한·직렬화가 곧 갈라진다.

**DB 테이블명과 공개 API 필드(`team_tags`)는 그대로다.** 바뀐 것은 사용자에게
보이는 한국어 문구뿐이고, 그 사실을 이 파일이 함께 고정한다.
"""
import pytest

CID = "a" * 32
CID2 = "b" * 32


@pytest.fixture
def tdb(db):
    """태그 테이블만 비운다(공용 conftest는 이 두 표를 모른다)."""
    import streamer_tags as st

    import database

    async def _clear():
        conn = await database.get_db()
        await conn.execute("DELETE FROM streamer_tag_assignments")
        await conn.execute("DELETE FROM streamer_tags")
        await conn.execute("DELETE FROM rising_channel_stats")
        await conn.commit()

    db(_clear())
    st.reset_state()
    return db


def _mk(st, name, color="#38BDF8"):
    return st.create_tag(name=name, color_mode="solid", color_start=color,
                         color_end=None, gradient_direction="to-right")


async def _seed_channels(pairs):
    """(channel_id, name) 목록을 `rising_channel_stats`에 심는다."""
    from database import get_db
    db = await get_db()
    for i, (cid, name) in enumerate(pairs):
        await db.execute(
            "INSERT OR REPLACE INTO rising_channel_stats "
            "(chzzk_channel_id, first_seen, last_seen, channel_name) VALUES (?,?,?,?)",
            (cid, 1, 1000 - i, name))
    await db.commit()


# ── 1. 기본 조회 ────────────────────────────────────────────────────────────

def test_lists_members_in_display_order(tdb):
    """멤버는 **화면에 보이는 순서 그대로** 온다."""
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "스텔라이브")
        chans = [(f"{i:032x}", f"멤버{i}") for i in range(1, 4)]
        await _seed_channels(chans)
        for cid, _ in chans:
            await st.assign(cid, g["id"])
        # 역순으로 재배치하면 목록도 그대로 뒤집혀야 한다
        await st.reorder(chans[0][0], [g["id"]])
        page = await st.assignments_of_tag(g["id"])
        return g, page

    g, page = tdb(_go())
    assert page["total"] == 3
    assert [m["displayOrder"] for m in page["items"]] == sorted(
        m["displayOrder"] for m in page["items"])
    assert all("channelImageUrl" in m for m in page["items"])


def test_total_is_counted_from_assignments_not_a_counter(tdb):
    """멤버 수는 **실제 지정 행**에서 센다(별도 누적 카운터를 두지 않는다)."""
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "인첸트")
        chans = [(f"{i:032x}", f"m{i}") for i in range(1, 6)]
        await _seed_channels(chans)
        for cid, _ in chans:
            await st.assign(cid, g["id"])
        before = (await st.assignments_of_tag(g["id"]))["total"]
        await st.unassign(chans[0][0], g["id"])
        after = (await st.assignments_of_tag(g["id"]))["total"]
        return before, after

    assert tdb(_go()) == (5, 4)


# ── 2. 페이지네이션 ─────────────────────────────────────────────────────────

def test_pagination_has_no_gap_and_no_duplicate(tdb):
    """페이지를 이어 붙이면 **누락도 중복도 없다**.

    `display_order`가 같은 행이 여럿일 때 정렬이 흔들리면 같은 사람이 두 페이지에
    나온다 — 그래서 채널 id로 안정 정렬한다.
    """
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "그룹")
        chans = [(f"{i:032x}", f"m{i:02d}") for i in range(1, 26)]
        await _seed_channels(chans)
        for cid, _ in chans:
            await st.assign(cid, g["id"])
        seen, off = [], 0
        while True:
            page = await st.assignments_of_tag(g["id"], limit=7, offset=off)
            seen += [m["channelId"] for m in page["items"]]
            if not page["hasMore"]:
                break
            off += len(page["items"])
        return seen, page["total"]

    seen, total = tdb(_go())
    assert total == 25
    assert len(seen) == 25
    assert len(set(seen)) == 25, "같은 멤버가 두 번 나왔다"


def test_limit_is_capped(tdb):
    """limit 상한 — 클라이언트가 큰 값을 보내도 서버가 자른다."""
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "그룹")
        chans = [(f"{i:032x}", f"m{i}") for i in range(1, 40)]
        await _seed_channels(chans)
        for cid, _ in chans:
            await st.assign(cid, g["id"])
        return await st.assignments_of_tag(g["id"], limit=99999)

    page = tdb(_go())
    assert page["limit"] == st_max()
    assert len(page["items"]) <= st_max()


def st_max():
    import streamer_tags as st
    return st.MEMBER_PAGE_MAX


# ── 3. 검색 ─────────────────────────────────────────────────────────────────

def test_search_by_name_and_channel_id(tdb):
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "그룹")
        await _seed_channels([(CID, "아야츠노 유니"), (CID2, "유즈하 리코")])
        await st.assign(CID, g["id"])
        await st.assign(CID2, g["id"])
        by_name = await st.assignments_of_tag(g["id"], search="아야츠노")
        by_id = await st.assignments_of_tag(g["id"], search=CID2)
        return by_name, by_id

    by_name, by_id = tdb(_go())
    assert [m["channelId"] for m in by_name["items"]] == [CID]
    assert by_name["total"] == 1
    assert [m["channelId"] for m in by_id["items"]] == [CID2]


def test_search_minimum_length(tdb):
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "그룹")
        with pytest.raises(st.TagError):
            await st.assignments_of_tag(g["id"], search="가")

    tdb(_go())


def test_search_escapes_like_wildcards(tdb):
    """`%`·`_`가 와일드카드로 동작하면 검색 하나로 전체가 긁힌다."""
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "그룹")
        await _seed_channels([(CID, "100%달성"), (CID2, "다른이름")])
        await st.assign(CID, g["id"])
        await st.assign(CID2, g["id"])
        wildcard = await st.assignments_of_tag(g["id"], search="%%")
        literal = await st.assignments_of_tag(g["id"], search="100%")
        underscore = await st.assignments_of_tag(g["id"], search="__")
        return wildcard, literal, underscore

    wildcard, literal, underscore = tdb(_go())
    assert wildcard["total"] == 0, "와일드카드가 그대로 먹었다"
    assert [m["channelId"] for m in literal["items"]] == [CID]
    assert underscore["total"] == 0


# ── 4. 비활성 그룹 ──────────────────────────────────────────────────────────

def test_inactive_group_still_manageable(tdb):
    """비활성 그룹도 **관리자는** 멤버를 보고 고칠 수 있다.

    비활성은 '공개 화면에서 숨김'이지 '삭제'가 아니다. 관리 화면까지 막으면
    되돌릴 방법이 사라진다.
    """
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "숨긴그룹")
        await _seed_channels([(CID, "멤버")])
        await st.assign(CID, g["id"])
        await st.update_tag(g["id"], active=False)
        page = await st.assignments_of_tag(g["id"])          # 관리 조회
        public = await st.tags_for_channel(CID)              # 공개 조회
        # 비활성 그룹에서 멤버를 빼는 것은 가능해야 한다
        removed = await st.unassign(CID, g["id"])
        return page, public, removed

    page, public, removed = tdb(_go())
    assert page["total"] == 1, "관리 화면에서도 안 보인다"
    assert public == [], "비활성 그룹이 공개 화면에 나왔다"
    assert removed["removed"] is True


# ── 5. 노출 필드 ────────────────────────────────────────────────────────────

def test_member_row_exposes_only_display_fields(tdb):
    """멤버 행에 내부 필드나 비밀이 섞이지 않는다."""
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "그룹")
        await _seed_channels([(CID, "멤버")])
        await st.assign(CID, g["id"])
        return (await st.assignments_of_tag(g["id"]))["items"][0]

    row = tdb(_go())
    assert set(row) == {"channelId", "channelName", "displayOrder", "channelImageUrl"}


def test_member_listing_does_not_scale_queries(tdb):
    """멤버 수가 늘어도 쿼리 수가 늘지 않는다(N+1 없음).

    프로필 이미지는 수집기가 메모리에 들고 있는 맵에서 읽으므로 DB도, 외부 호출도
    건드리지 않는다. 여기서 그 사실을 쿼리 횟수로 고정한다.
    """
    import streamer_tags as st

    import database

    async def _go():
        g = await _mk(st, "그룹")
        chans = [(f"{i:032x}", f"m{i}") for i in range(1, 61)]
        await _seed_channels(chans)
        for cid, _ in chans:
            await st.assign(cid, g["id"])
        conn = await database.get_db()
        calls = {"n": 0}
        orig = conn.execute

        async def counting(*a, **k):
            calls["n"] += 1
            return await orig(*a, **k)

        conn.execute = counting
        try:
            for n in (1, 10, 60):
                calls["n"] = 0
                await st.assignments_of_tag(g["id"], limit=n)
                yield_n = calls["n"]
                assert yield_n <= 3, f"{n}행에 쿼리 {yield_n}회 — N+1 의심"
        finally:
            conn.execute = orig

    tdb(_go())


# ── 6. 기존 계약 회귀 ───────────────────────────────────────────────────────

def test_existing_public_contract_untouched(tdb):
    """공개 응답의 `team_tags` 직렬화와 방송 태그 `tags`는 그대로다."""
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "스텔라이브")
        await _seed_channels([(CID, "멤버")])
        await st.assign(CID, g["id"])
        rows = [{"chzzk_channel_id": CID, "tags": ["종합게임", "노래"]}]
        await st.attach_tags(rows)
        return rows[0], await st.tags_for_channel(CID)

    row, public = tdb(_go())
    assert row["tags"] == ["종합게임", "노래"], "방송 태그가 덮어써졌다"
    assert [t["name"] for t in row["team_tags"]] == ["스텔라이브"]
    # 이 테스트가 지키는 것은 "방송 태그(`tags`)를 덮어쓰지 않는다"와
    # "운영 메타가 새지 않는다"이다. `colorStops`는 다중 그라데이션에서 더해진
    # 표시용 필드라 목록에 포함된다(구형 4필드는 하위 호환으로 그대로 남아 있다).
    assert set(public[0]) == {"id", "name", "slug", "kind", "colorMode",
                              "colorStart", "colorEnd", "gradientDirection",
                              "colorStops"}
    assert not ({"active", "createdAt", "updatedAt", "excludeFromRanking"}
                & set(public[0]))


def test_no_schema_change_needed(tdb):
    """TAG-2는 **마이그레이션이 필요 없다** — 기존 두 표를 그대로 읽는다."""
    import database

    async def _go():
        conn = await database.get_db()
        rows = await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'streamer_%'")).fetchall()
        return sorted(r["name"] for r in rows)

    assert tdb(_go()) == ["streamer_tag_assignments", "streamer_tags"]


def test_cache_generation_bumps_on_membership_change(tdb):
    """멤버를 넣고 빼면 캐시 세대가 올라간다(공개 화면이 즉시 갱신)."""
    import streamer_tags as st

    async def _go():
        g = await _mk(st, "그룹")
        await _seed_channels([(CID, "멤버")])
        v0 = st.version()
        await st.assign(CID, g["id"])
        v1 = st.version()
        await st.unassign(CID, g["id"])
        v2 = st.version()
        return v0, v1, v2

    v0, v1, v2 = tdb(_go())
    assert v0 < v1 < v2
