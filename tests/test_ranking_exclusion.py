"""전체 스트리머 랭킹 제외 그룹 (UI-R 요구 6).

여기서 지키려는 계약은 다섯 가지다.

1. **그룹 이름이 아니라 속성으로 판정한다.** 이름을 바꿔도 제외가 유지돼야 한다 —
   이름으로 판정하면 이름 변경 한 번에 정책이 조용히 풀린다.
2. **적용 범위는 전체 스트리머 랭킹 하나뿐이다.** 신규 랭킹·검색·상세·수집은 그대로다.
3. **되돌릴 수 있어야 한다.** 그룹을 비활성화하거나 멤버를 빼면 즉시 랭킹에 돌아온다.
4. **여러 그룹에 속하면 하나라도 제외 그룹이면 제외**된다.
5. 마이그레이션은 append-only라 재실행이 안전하고, 기존 행은 기본값 0을 받는다.
"""
import pytest


@pytest.fixture
def tdb(db):
    """태그 테이블을 이 모듈 안에서만 비운다(공용 conftest는 이 테이블을 모른다)."""
    import streamer_tags as st

    import database

    async def _clear():
        conn = await database.get_db()
        for t in ("streamer_tag_assignments", "streamer_tags"):
            try:
                await conn.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await conn.commit()

    db(_clear())
    st.reset_state()
    return db


async def _mk_tag(name, *, exclude=False):
    import streamer_tags as st
    return await st.create_tag(name=name, color_mode="solid", color_start="#38BDF8",
                               color_end=None, gradient_direction="to-right",
                               exclude_from_ranking=exclude)


class TestMigration:
    def test_컬럼이_생긴다(self, tdb):
        import database

        async def _cols():
            conn = await database.get_db()
            rows = await (await conn.execute("PRAGMA table_info(streamer_tags)")).fetchall()
            return {r[1] for r in rows}

        assert "exclude_from_ranking" in tdb(_cols())

    def test_재실행이_안전하다(self, tdb):
        import database
        # append-only 마이그레이션은 몇 번을 돌려도 같아야 한다.
        tdb(database.init_db())
        tdb(database.init_db())

    def test_기존_행_기본값은_0(self, tdb):
        import database

        async def _go():
            conn = await database.get_db()
            import time
            now = int(time.time())
            # 컬럼을 지정하지 않고 넣으면 DEFAULT 0이 들어가야 한다.
            await conn.execute(
                "INSERT INTO streamer_tags(name, slug, kind, color_mode, color_start,"
                " gradient_direction, active, created_at, updated_at)"
                " VALUES('레거시','legacy','team','solid','#38BDF8','to-right',1,?,?)",
                (now, now))
            await conn.commit()
            r = await (await conn.execute(
                "SELECT exclude_from_ranking FROM streamer_tags WHERE name='레거시'")).fetchone()
            return int(r[0])

        assert tdb(_go()) == 0


class TestExclusionSet:
    def test_제외_그룹_멤버만_모인다(self, tdb):
        import streamer_tags as st

        async def _go():
            excl = await _mk_tag("공식", exclude=True)
            keep = await _mk_tag("이세돌", exclude=False)
            await st.assign("a" * 32, excl["id"])
            await st.assign("b" * 32, keep["id"])
            return await st.excluded_channel_ids()

        assert tdb(_go()) == {"a" * 32}

    def test_그룹_이름을_바꿔도_제외가_유지된다(self, tdb):
        import streamer_tags as st

        async def _go():
            t = await _mk_tag("공식", exclude=True)
            await st.assign("a" * 32, t["id"])
            # 이름만 바꾼다 — 정책은 속성에 붙어 있으므로 그대로여야 한다.
            await st.update_tag(t["id"], name="치지직 공식")
            return await st.excluded_channel_ids()

        assert tdb(_go()) == {"a" * 32}

    def test_그룹을_비활성화하면_돌아온다(self, tdb):
        import streamer_tags as st

        async def _go():
            t = await _mk_tag("공식", exclude=True)
            await st.assign("a" * 32, t["id"])
            await st.update_tag(t["id"], active=False)
            return await st.excluded_channel_ids()

        assert tdb(_go()) == set()

    def test_멤버를_빼면_돌아온다(self, tdb):
        import streamer_tags as st

        async def _go():
            t = await _mk_tag("공식", exclude=True)
            await st.assign("a" * 32, t["id"])
            await st.unassign("a" * 32, t["id"])
            return await st.excluded_channel_ids()

        assert tdb(_go()) == set()

    def test_정책을_해제하면_돌아온다(self, tdb):
        import streamer_tags as st

        async def _go():
            t = await _mk_tag("공식", exclude=True)
            await st.assign("a" * 32, t["id"])
            await st.update_tag(t["id"], exclude_from_ranking=False)
            return await st.excluded_channel_ids()

        assert tdb(_go()) == set()

    def test_한_그룹만_제외여도_제외된다(self, tdb):
        import streamer_tags as st

        async def _go():
            excl = await _mk_tag("공식", exclude=True)
            keep = await _mk_tag("이세돌", exclude=False)
            # 같은 채널이 두 그룹에 속한다.
            await st.assign("a" * 32, excl["id"])
            await st.assign("a" * 32, keep["id"])
            return await st.excluded_channel_ids()

        assert tdb(_go()) == {"a" * 32}


class TestAdminPayload:
    def test_운영_응답에_속성이_실린다(self, tdb):
        async def _go():
            t = await _mk_tag("공식", exclude=True)
            return t

        assert tdb(_go())["excludeFromRanking"] is True

    def test_기본은_꺼짐(self, tdb):
        async def _go():
            return await _mk_tag("이세돌")

        assert tdb(_go())["excludeFromRanking"] is False

    def test_공개_응답에는_없다(self, tdb):
        """공개 화면은 이 정책을 알 필요가 없다 — 운영 정보를 흘리지 않는다."""
        import streamer_tags as st

        async def _go():
            t = await _mk_tag("공식", exclude=True)
            await st.assign("a" * 32, t["id"])
            return await st.tags_for_channel("a" * 32)

        for tag in tdb(_go()):
            assert "excludeFromRanking" not in tag


class TestRankingQuery:
    """랭킹 쿼리가 실제로 서브쿼리를 쓰는지 — 문자열 조립으로 되돌아가지 않게 고정한다."""

    def test_live_ranking이_제외_서브쿼리를_포함한다(self):
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / \
            "web" / "backend" / "routers" / "rising_router.py"
        s = src.read_text(encoding="utf-8")
        head = s.split("@router.get(\"/live-ranking\")")[1].split("@router.get")[0]
        assert "exclude_from_ranking = 1" in head
        assert "streamer_tag_assignments" in head
        assert "t.active = 1" in head

    def test_신규_랭킹에는_제외가_없다(self):
        """요구 6은 전체 랭킹에만 적용한다 — 확대 해석을 코드로 막는다."""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / \
            "web" / "backend" / "routers" / "rising_router.py"
        s = src.read_text(encoding="utf-8")
        newcomers = s.split("@router.get(\"/newcomers\")")[1].split("@router.get")[0]
        assert "exclude_from_ranking" not in newcomers
