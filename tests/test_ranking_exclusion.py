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


class TestPeriodRankingBehaviour:
    """기간별 누적 랭킹의 **동작**을 본다 — 소스 문자열만 보면 절이 실제로 걸리는지 모른다."""

    @staticmethod
    async def _seed_rollup(cid: str, hour_ts: int, viewers: int):
        import database
        conn = await database.get_db()
        await conn.execute(
            "INSERT OR REPLACE INTO rising_hourly_rollup (chzzk_channel_id, hour_ts,"
            " channel_name, category_name, snaps, sum_viewers, peak_viewers,"
            " max_follower) VALUES (?,?,?,?,?,?,?,?)",
            (cid, hour_ts, f"ch-{cid[:4]}", "게임", 6, viewers * 6, viewers, 100))
        await conn.execute(
            "INSERT OR REPLACE INTO rising_collect_runs (collected_at, ok)"
            " VALUES (?, 1)", (hour_ts + 60,))
        await conn.commit()

    def test_제외_그룹_멤버가_기간_랭킹에서_빠지고_되돌아온다(self, tdb):
        import time

        import streamer_tags as st
        from routers import rising_router as rr

        keep, drop = "1" * 32, "2" * 32
        now = int(time.time())
        hour = (now // 3600) * 3600 - 3600

        async def _go():
            await self._seed_rollup(keep, hour, 50)
            await self._seed_rollup(drop, hour, 900)
            tag = await _mk_tag("공식", exclude=True)
            await st.assign(drop, tag["id"])
            rr._period_cache.clear()
            excluded = await rr.ranking_period(range="24h", limit=100)
            # 그룹을 내리면 즉시 되돌아와야 한다
            await st.update_tag(tag["id"], active=False)
            rr._period_cache.clear()
            restored = await rr.ranking_period(range="24h", limit=100)
            return excluded, restored

        excluded, restored = tdb(_go())
        ids_excluded = {s["chzzk_channel_id"] for s in excluded["streamers"]}
        ids_restored = {s["chzzk_channel_id"] for s in restored["streamers"]}
        assert keep in ids_excluded
        assert drop not in ids_excluded, "제외 그룹 멤버가 기간 랭킹에 남아 있다"
        assert drop in ids_restored, "그룹을 내리면 즉시 돌아와야 한다"


def _router_src() -> str:
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[1] / "web" / "backend"
            / "routers" / "rising_router.py").read_text(encoding="utf-8")


def _endpoint(name: str) -> str:
    return _router_src().split(f'@router.get("/{name}")')[1].split("@router.get")[0]


class TestRankingQuery:
    """랭킹 쿼리가 실제로 제외 절을 쓰는지 — 문자열 조립으로 되돌아가지 않게 고정한다.

    **SQL 텍스트가 아니라 공통 헬퍼 호출을 본다.** 적용 화면이 셋으로 늘면서
    서브쿼리를 라우터마다 복사해 두면 복사본끼리 갈라지는 것이 실제 위험이 됐다.
    조각을 만드는 지점은 `streamer_tags.ranking_exclusion_clause` 하나뿐이다.
    """

    def test_live_ranking이_제외_절을_쓴다(self):
        head = _endpoint("live-ranking")
        assert "st.ranking_exclusion_clause(" in head
        assert '"n.chzzk_channel_id"' in head
        # 인라인 복사로 되돌아가지 않았는지도 함께 본다
        assert "exclude_from_ranking = 1" not in head, "SQL을 복사해 넣지 말 것"

    def test_기간별_누적_랭킹도_제외한다(self):
        """예전에는 전체 랭킹에만 있어 두 화면의 명단이 서로 달랐다."""
        head = _endpoint("ranking-period")
        assert "st.ranking_exclusion_clause(" in head
        assert "exclude_from_ranking = 1" not in head

    def test_신규_통계에는_제외가_없다(self):
        """찾기·분석 화면에는 적용하지 않는다 — 확대 해석을 코드로 막는다."""
        newcomers = _endpoint("newcomers")
        assert "exclude_from_ranking" not in newcomers
        assert "ranking_exclusion_clause" not in newcomers

    def test_검색과_상세에는_제외가_없다(self):
        """여기서 빼면 '검색해도 안 나온다'가 된다."""
        for name in ("search", "category-streamers"):
            body = _endpoint(name)
            assert "ranking_exclusion_clause" not in body, name
            assert "exclude_from_ranking" not in body, name

    def test_적용_범위가_한_곳에_적혀_있다(self):
        import streamer_tags as st
        assert set(st.RANKING_EXCLUSION_APPLIES_TO) == {
            "live-ranking", "ranking-period", "small-ranking"}

    def test_컬럼_식별자만_통과한다(self):
        """조각 조립 지점에 검증이 없으면 나중에 그게 주입 경로가 된다."""
        import pytest
        import streamer_tags as st

        assert "n.chzzk_channel_id NOT IN" in \
            st.ranking_exclusion_clause("n.chzzk_channel_id")
        for bad in ("x); DROP TABLE t;--", "a b", "1", "", "a.b.c", "a-b"):
            with pytest.raises(ValueError):
                st.ranking_exclusion_clause(bad)
