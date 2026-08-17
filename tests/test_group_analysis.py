"""그룹 분석 (UI-T 요구 2).

지키려는 계약은 다섯 가지다.

1. **랭킹 제외와 독립이다.** `exclude_from_ranking`은 *랭킹*에서 멤버를 빼는
   정책이지 그룹을 숨기는 뜻이 아니다. 공식 그룹도 그룹 분석에서는 보인다.
2. **활성 + 멤버 1명 이상**만 목록에 든다.
3. **쿼리 수가 멤버 수와 무관하다.** 멤버마다 라이브를 조회하면 N+1이 된다.
4. 그룹·멤버가 바뀌면 **다음 요청이 곧바로 새 값**을 본다(TTL을 기다리지 않는다).
5. `live=False`와 `concurrentViewers=0`은 다른 상태다.
"""
import time

import pytest
import streamer_tags as st

import database


@pytest.fixture
def gdb(db):
    async def _clear():
        c = await database.get_db()
        for t in ("streamer_tag_assignments", "streamer_tags",
                  "rising_live_snapshots", "rising_collect_runs",
                  "rising_channel_stats"):
            try:
                await c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await c.commit()
    db(_clear())
    st.reset_state()
    st.reset_group_cache()
    return db


async def _mk(name, *, exclude=False, active=True):
    t = await st.create_tag(name=name, color_mode="solid", color_start="#38BDF8",
                            color_end=None, gradient_direction="to-right",
                            exclude_from_ranking=exclude)
    if not active:
        await st.update_tag(t["id"], active=False)
    return t


async def _live(cid, viewers, *, ts, name="채널", category="게임"):
    c = await database.get_db()
    await c.execute(
        "INSERT INTO rising_live_snapshots (collected_at, chzzk_channel_id,"
        " channel_name, follower_count, concurrent_viewers, category_id,"
        " category_name, live_title, open_date, adult)"
        " VALUES (?,?,?,0,?,'',?,'제목','2026-08-17 09:00:00',0)",
        (ts, cid, name, viewers, category))
    await c.commit()


async def _run(ts):
    c = await database.get_db()
    await c.execute(
        "INSERT OR REPLACE INTO rising_collect_runs (collected_at, ok)"
        " VALUES (?, 1)", (ts,))
    await c.commit()


# ── 1) 목록 조건 ────────────────────────────────────────────────────────────

class TestGroupList:
    def test_활성이고_멤버가_있는_그룹만_나온다(self, gdb):
        async def _go():
            full = await _mk("멤버있음")
            await _mk("빈그룹")
            # 비활성 태그에는 멤버를 붙일 수 없다(기존 계약) — 붙인 뒤 내린다.
            off = await _mk("비활성")
            await st.assign("a" * 32, full["id"])
            await st.assign("b" * 32, off["id"])
            await st.update_tag(off["id"], active=False)
            st.reset_group_cache()
            return await st.group_list()

        names = [g["name"] for g in gdb(_go())]
        assert names == ["멤버있음"]

    def test_공식_그룹도_그룹_분석에_나온다(self, gdb):
        """랭킹 제외는 *랭킹* 정책이다 — 여기서 숨기면 다른 정책이 된다."""
        async def _go():
            official = await _mk("치지직 공식", exclude=True)
            await st.assign("a" * 32, official["id"])
            st.reset_group_cache()
            return await st.group_list()

        out = gdb(_go())
        assert [g["name"] for g in out] == ["치지직 공식"]

    def test_랭킹_제외는_그대로_유지된다(self, gdb):
        """그룹 분석에 보인다고 랭킹 제외가 풀리면 안 된다."""
        async def _go():
            official = await _mk("치지직 공식", exclude=True)
            await st.assign("a" * 32, official["id"])
            return await st.excluded_channel_ids()

        assert gdb(_go()) == {"a" * 32}

    def test_운영_전용_필드를_공개하지_않는다(self, gdb):
        async def _go():
            t = await _mk("그룹", exclude=True)
            await st.assign("a" * 32, t["id"])
            st.reset_group_cache()
            return await st.group_list()

        for g in gdb(_go()):
            assert "excludeFromRanking" not in g
            assert "active" not in g
            assert "createdAt" not in g

    def test_멤버_수가_함께_온다(self, gdb):
        async def _go():
            t = await _mk("그룹")
            for i in range(3):
                await st.assign(chr(97 + i) * 32, t["id"])
            st.reset_group_cache()
            return await st.group_list()

        assert gdb(_go())[0]["memberCount"] == 3

    def test_다중_gradient가_그대로_실린다(self, gdb):
        stops = [{"color": "#FF0000", "pos": 0}, {"color": "#00FF00", "pos": 50},
                 {"color": "#0000FF", "pos": 100}]

        async def _go():
            t = await st.create_tag(name="3색", color_stops=stops,
                                    gradient_direction="to-right")
            await st.assign("a" * 32, t["id"])
            st.reset_group_cache()
            return await st.group_list()

        # 색상은 저장 시 소문자로 정규화된다(기존 계약).
        got = gdb(_go())[0]
        assert [s["color"] for s in got["colorStops"]] == \
            ["#ff0000", "#00ff00", "#0000ff"]


# ── 2) 상세 ─────────────────────────────────────────────────────────────────

class TestGroupDetail:
    def test_라이브_합계와_그룹_내_순위(self, gdb):
        ts = int(time.time())

        async def _go():
            t = await _mk("그룹")
            for cid, v in (("a" * 32, 10), ("b" * 32, 30), ("c" * 32, 20)):
                await st.assign(cid, t["id"])
                await _live(cid, v, ts=ts)
            await st.assign("d" * 32, t["id"])      # 방송 안 함
            await _run(ts)
            st.reset_group_cache()
            return await st.group_detail(t["id"])

        out = gdb(_go())
        assert out["memberCount"] == 4
        assert out["liveCount"] == 3
        assert out["totalViewers"] == 60
        ranked = {m["channelId"]: m.get("groupRank") for m in out["members"]}
        assert ranked["b" * 32] == 1
        assert ranked["c" * 32] == 2
        assert ranked["a" * 32] == 3
        assert ranked["d" * 32] is None, "꺼져 있는 멤버는 순위를 받지 않는다"

    def test_시청자0과_방송없음을_구분한다(self, gdb):
        ts = int(time.time())

        async def _go():
            t = await _mk("그룹")
            await st.assign("a" * 32, t["id"])
            await _live("a" * 32, 0, ts=ts)
            await st.assign("b" * 32, t["id"])
            await _run(ts)
            st.reset_group_cache()
            return await st.group_detail(t["id"])

        m = {x["channelId"]: x for x in gdb(_go())["members"]}
        assert m["a" * 32]["live"] is True and m["a" * 32]["concurrentViewers"] == 0
        assert m["b" * 32]["live"] is False

    def test_최신_회차_하나만_본다(self, gdb):
        """회차를 섞으면 합계가 서로 다른 시각의 값을 더한 숫자가 된다."""
        old, new = 1000, 2000

        async def _go():
            t = await _mk("그룹")
            await st.assign("a" * 32, t["id"])
            await _live("a" * 32, 999, ts=old)
            await _live("a" * 32, 5, ts=new)
            await _run(old)
            await _run(new)
            st.reset_group_cache()
            return await st.group_detail(t["id"])

        assert gdb(_go())["totalViewers"] == 5

    def test_빈_그룹도_오류가_아니다(self, gdb):
        async def _go():
            t = await _mk("빈그룹")
            st.reset_group_cache()
            return await st.group_detail(t["id"])

        out = gdb(_go())
        assert out["memberCount"] == 0 and out["members"] == []
        assert out["liveCount"] == 0 and out["totalViewers"] == 0

    def test_비활성_그룹은_상세도_막힌다(self, gdb):
        async def _go():
            t = await _mk("그룹")
            await st.assign("a" * 32, t["id"])
            await st.update_tag(t["id"], active=False)
            st.reset_group_cache()
            return await st.group_detail(t["id"])

        assert gdb(_go()) is None

    def test_없는_그룹은_404가_된다(self, gdb):
        from fastapi import HTTPException
        from routers.rising_router import group_detail as ep
        with pytest.raises(HTTPException) as e:
            gdb(ep(999999))
        assert e.value.status_code == 404

    def test_수집_시각을_함께_준다(self, gdb):
        ts = int(time.time())

        async def _go():
            t = await _mk("그룹")
            await st.assign("a" * 32, t["id"])
            await _live("a" * 32, 1, ts=ts)
            await _run(ts)
            st.reset_group_cache()
            return await st.group_detail(t["id"])

        assert gdb(_go())["collectedAt"] == ts


# ── 3) 캐시 무효화 ──────────────────────────────────────────────────────────

class TestCacheInvalidation:
    def test_멤버_변경이_즉시_반영된다(self, gdb):
        """TTL을 기다리지 않는다 — `version()`이 캐시 키에 섞여 있다."""
        async def _go():
            t = await _mk("그룹")
            await st.assign("a" * 32, t["id"])
            first = await st.group_list()
            await st.assign("b" * 32, t["id"])
            second = await st.group_list()
            return first[0]["memberCount"], second[0]["memberCount"]

        assert gdb(_go()) == (1, 2)

    def test_그룹_비활성화가_즉시_반영된다(self, gdb):
        async def _go():
            t = await _mk("그룹")
            await st.assign("a" * 32, t["id"])
            before = await st.group_list()
            await st.update_tag(t["id"], active=False)
            after = await st.group_list()
            return len(before), len(after)

        assert gdb(_go()) == (1, 0)

    def test_상세도_같은_규칙을_따른다(self, gdb):
        async def _go():
            t = await _mk("그룹")
            await st.assign("a" * 32, t["id"])
            before = await st.group_detail(t["id"])
            await st.unassign("a" * 32, t["id"])
            after = await st.group_detail(t["id"])
            return before["memberCount"], after["memberCount"]

        assert gdb(_go()) == (1, 0)


# ── 4) N+1 방지 ─────────────────────────────────────────────────────────────

class TestNoNPlusOne:
    def test_멤버가_늘어도_쿼리_수가_그대로다(self, gdb):
        """멤버마다 라이브를 조회하면 이 테스트가 깨진다."""
        import database as dbmod

        calls = {"n": 0}
        ts = int(time.time())

        async def _prepare():
            t = await _mk("큰그룹")
            for i in range(40):
                cid = f"{i:032d}"
                await st.assign(cid, t["id"])
                await _live(cid, i, ts=ts)
            await _run(ts)
            return t["id"]

        tid = gdb(_prepare())
        st.reset_group_cache()

        async def _measure():
            conn = await dbmod.get_db()
            orig = conn.execute

            async def counting(*a, **k):
                calls["n"] += 1
                return await orig(*a, **k)

            conn.execute = counting
            try:
                return await st.group_detail(tid)
            finally:
                conn.execute = orig

        out = gdb(_measure())
        assert out["memberCount"] == 40
        assert calls["n"] <= 4, f"멤버 40명에 쿼리 {calls['n']}회 — N+1이다"

    def test_목록도_그룹마다_세지_않는다(self, gdb):
        import database as dbmod

        calls = {"n": 0}

        async def _prepare():
            for g in range(10):
                t = await _mk(f"그룹{g}")
                for i in range(3):
                    await st.assign(f"{g:016d}{i:016d}", t["id"])

        gdb(_prepare())
        st.reset_group_cache()

        async def _measure():
            conn = await dbmod.get_db()
            orig = conn.execute

            async def counting(*a, **k):
                calls["n"] += 1
                return await orig(*a, **k)

            conn.execute = counting
            try:
                return await st.group_list()
            finally:
                conn.execute = orig

        out = gdb(_measure())
        assert len(out) == 10
        assert calls["n"] <= 2, f"그룹 10개에 쿼리 {calls['n']}회 — N+1이다"

    def test_멤버_수에_상한이_있다(self):
        assert st.GROUP_MEMBER_MAX <= 500
        assert st.GROUP_LIST_MAX <= 500
