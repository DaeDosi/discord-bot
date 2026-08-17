"""소형 스트리머 랭킹 (NAV-STATS).

지키려는 계약은 넷이다.

1. **정의는 한 곳에서만 온다.** '소형'은 `_SMALL_AVG_MAX`(최근 7일 평균 동시 시청자)
   하나로 정해지고, 분석 화면(`/newcomers?group=small`)과 같은 값을 쓴다.
   여기서 숫자를 다시 적으면 두 화면의 명단이 조용히 갈라진다.
2. **랭킹이므로 공식 그룹 제외가 적용된다.** 분석 화면은 적용하지 않는다 —
   두 화면이 같은 사람을 다르게 다루는 것이 정상이고, 그 이유가 이 차이다.
3. 정렬은 **동시 시청자 내림차순**(전체 랭킹과 같은 기준)이다.
4. 응답 형태가 `/live-ranking`과 호환된다(프론트 랭킹 표를 재사용한다).
"""
import time

import pytest
import streamer_tags as st
from routers import rising_router as rr

import database

HOUR = 3600


@pytest.fixture
def rdb(db):
    async def _clear():
        c = await database.get_db()
        for t in ("rising_live_snapshots", "rising_hourly_rollup",
                  "rising_collect_runs", "streamer_tag_assignments", "streamer_tags"):
            try:
                await c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await c.commit()
    db(_clear())
    st.reset_state()
    return db


async def _seed(cid, *, now, live_viewers, avg7):
    """스냅샷 1건 + 7일치 롤업(평균이 정확히 avg7이 되도록)."""
    c = await database.get_db()
    await c.execute(
        "INSERT OR REPLACE INTO rising_collect_runs (collected_at, ok) VALUES (?,1)",
        (now,))
    await c.execute(
        "INSERT OR REPLACE INTO rising_live_snapshots (chzzk_channel_id, collected_at,"
        " channel_name, concurrent_viewers, category_name, open_date, follower_count,"
        " live_title, adult, tags) VALUES (?,?,?,?,'게임','',0,'',0,'')",
        (cid, now, f"ch-{cid[:4]}", live_viewers))
    hour = (now // HOUR) * HOUR - HOUR
    await c.execute(
        "INSERT OR REPLACE INTO rising_hourly_rollup (chzzk_channel_id, hour_ts,"
        " channel_name, category_name, snaps, sum_viewers, peak_viewers, max_follower)"
        " VALUES (?,?,?,'게임',10,?,?,0)",
        (cid, hour, f"ch-{cid[:4]}", int(avg7 * 10), int(avg7)))
    await c.commit()


A, B, C = "a" * 32, "b" * 32, "c" * 32


def test_소형_기준_이하만_들어온다(rdb):
    now = int(time.time())

    async def _go():
        await _seed(A, now=now, live_viewers=7, avg7=3)      # 소형
        await _seed(B, now=now, live_viewers=500, avg7=400)  # 대형
        return await rr.small_ranking(limit=50)

    res = rdb(_go())
    ids = {s["chzzk_channel_id"] for s in res["streamers"]}
    assert A in ids
    assert B not in ids, "평균이 기준을 넘는 채널이 소형 랭킹에 들어왔다"


def test_경계값은_포함이다(rdb):
    """`<=` 계약 — 정확히 기준값인 채널은 소형이다."""
    now = int(time.time())

    async def _go():
        await _seed(A, now=now, live_viewers=5, avg7=rr._SMALL_AVG_MAX)
        await _seed(B, now=now, live_viewers=5, avg7=rr._SMALL_AVG_MAX + 1)
        return await rr.small_ranking(limit=50)

    ids = {s["chzzk_channel_id"] for s in rdb(_go())["streamers"]}
    assert A in ids and B not in ids


def test_동시_시청자_내림차순이다(rdb):
    now = int(time.time())

    async def _go():
        await _seed(A, now=now, live_viewers=2, avg7=1)
        await _seed(B, now=now, live_viewers=9, avg7=2)
        await _seed(C, now=now, live_viewers=5, avg7=3)
        return await rr.small_ranking(limit=50)

    res = rdb(_go())["streamers"]
    assert [s["chzzk_channel_id"] for s in res] == [B, C, A]
    assert [s["rank"] for s in res] == [1, 2, 3], "순위는 1부터 다시 매긴다"


def test_공식_그룹은_제외되고_되돌릴_수_있다(rdb):
    now = int(time.time())

    async def _go():
        await _seed(A, now=now, live_viewers=5, avg7=2)
        await _seed(B, now=now, live_viewers=8, avg7=2)
        tag = await st.create_tag(name="공식", color_stops=["#112233"],
                                  exclude_from_ranking=True)
        await st.assign(B, tag["id"])
        excluded = await rr.small_ranking(limit=50)
        await st.update_tag(tag["id"], active=False)
        restored = await rr.small_ranking(limit=50)
        return excluded, restored

    excluded, restored = rdb(_go())
    assert B not in {s["chzzk_channel_id"] for s in excluded["streamers"]}
    assert B in {s["chzzk_channel_id"] for s in restored["streamers"]}, \
        "그룹을 내리면 즉시 돌아와야 한다"


def test_분석_화면은_같은_사람을_제외하지_않는다(rdb):
    """랭킹과 통계는 축이 다르다 — 통계에서 빼면 '찾아도 안 나온다'가 된다."""
    now = int(time.time())

    async def _go():
        await _seed(A, now=now, live_viewers=5, avg7=2)
        tag = await st.create_tag(name="공식", color_stops=["#112233"],
                                  exclude_from_ranking=True)
        await st.assign(A, tag["id"])
        rr._newcomers_cache.clear()
        return (await rr.small_ranking(limit=50),
                await rr.newcomers(limit=80, group="small"))

    ranking, analysis = rdb(_go())
    assert A not in {s["chzzk_channel_id"] for s in ranking["streamers"]}
    assert A in {s["chzzk_channel_id"] for s in analysis["streamers"]}, \
        "분석 화면에는 남아 있어야 한다"


def test_응답_형태가_전체_랭킹과_호환된다(rdb):
    now = int(time.time())

    async def _go():
        await _seed(A, now=now, live_viewers=5, avg7=2)
        return await rr.small_ranking(limit=50)

    res = rdb(_go())
    assert set(res) >= {"collected_at", "streamers", "criteria"}
    row = res["streamers"][0]
    for k in ("rank", "chzzk_channel_id", "channel_name", "channel_image_url",
              "concurrent_viewers", "category_name", "open_date", "follower_count",
              "live_title", "adult", "team_tags"):
        assert k in row, f"{k}가 없으면 랭킹 표를 재사용할 수 없다"
    # 기준은 서버가 준다 — 프론트에 숫자를 복사해 두면 설명만 옛날 값이 된다.
    assert res["criteria"]["small_avg_max"] == rr._SMALL_AVG_MAX
    assert res["criteria"]["window_days"] == 7


def test_수집_전에는_빈_목록이다(rdb):
    res = rdb(rr.small_ranking(limit=50))
    assert res["collected_at"] is None and res["streamers"] == []


def test_limit_상한이_있다(rdb):
    now = int(time.time())

    async def _go():
        await _seed(A, now=now, live_viewers=5, avg7=2)
        return await rr.small_ranking(limit=99999)

    assert rdb(_go())["streamers"], "상한을 넘겨도 500이 나면 안 된다"
