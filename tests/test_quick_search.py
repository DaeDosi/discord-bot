"""전역 헤더 검색 (`/api/rising/quick-search`).

지키려는 계약은 다섯이다.

1. **외부 API를 부르지 않는다.** 전역 헤더는 모든 페이지에 있으므로, 기존
   `/search`(치지직 외부 호출 + heavy 버킷 40/분)를 쓰면 정상 사용자가 몇 번
   타이핑하는 것만으로 한도가 소진되고 외부 부하도 페이지 수만큼 곱해진다.
2. **입력 길이와 결과 수에 상한이 있다.**
3. **LIKE 와일드카드가 무력화된다** — `%` 하나로 전체 스캔이 되면 안 된다.
4. 공개 응답에 내부 지표를 싣지 않는다.
5. 지금 방송 중인 채널이 먼저 나온다(찾는 사람은 대개 지금 켜져 있는 쪽이다).
"""
import time

import pytest
from routers import rising_router as rr

import database


@pytest.fixture
def sdb(db):
    async def _clear():
        c = await database.get_db()
        for t in ("rising_channel_stats", "rising_live_snapshots", "rising_collect_runs"):
            try:
                await c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await c.commit()
    db(_clear())
    return db


async def _seed(cid, name, *, now, live=False, viewers=0, last_seen=None):
    c = await database.get_db()
    await c.execute(
        "INSERT OR REPLACE INTO rising_channel_stats (chzzk_channel_id, first_seen,"
        " last_seen, channel_name) VALUES (?,?,?,?)",
        (cid, now - 86400, last_seen if last_seen is not None else now, name))
    if live:
        await c.execute(
            "INSERT OR REPLACE INTO rising_collect_runs (collected_at, ok) VALUES (?,1)",
            (now,))
        await c.execute(
            "INSERT OR REPLACE INTO rising_live_snapshots (chzzk_channel_id,"
            " collected_at, channel_name, concurrent_viewers, category_name,"
            " open_date, follower_count, live_title, adult, tags)"
            " VALUES (?,?,?,?,'게임','',0,'',0,'')",
            (cid, now, name, viewers))
    await c.commit()


A, B, C = "a" * 32, "b" * 32, "c" * 32


def test_이름_부분일치로_찾는다(sdb):
    now = int(time.time())

    async def _go():
        await _seed(A, "구독하는연습생", now=now)
        await _seed(B, "전혀다른이름", now=now)
        return await rr.quick_search(q="연습")

    res = sdb(_go())
    assert [r["channel_id"] for r in res["results"]] == [A]


def test_방송_중인_채널이_먼저_나온다(sdb):
    now = int(time.time())

    async def _go():
        await _seed(A, "테스트하나", now=now, last_seen=now)
        await _seed(B, "테스트둘", now=now, live=True, viewers=5, last_seen=now - 999)
        return await rr.quick_search(q="테스트")

    ids = [r["channel_id"] for r in sdb(_go())["results"]]
    assert ids[0] == B, "지금 켜져 있는 채널을 먼저 보여 준다"


def test_빈_검색어는_아무것도_돌려주지_않는다(sdb):
    now = int(time.time())

    async def _go():
        await _seed(A, "무언가", now=now)
        return [await rr.quick_search(q=""), await rr.quick_search(q="   ")]

    for res in sdb(_go()):
        assert res["results"] == []


def test_입력_길이_상한을_넘으면_거절한다(sdb):
    now = int(time.time())

    async def _go():
        await _seed(A, "무언가", now=now)
        return await rr.quick_search(q="가" * (rr.QUICK_SEARCH_MAX_LEN + 1))

    res = sdb(_go())
    assert res["results"] == []
    assert len(res["query"]) <= rr.QUICK_SEARCH_MAX_LEN, "질의를 그대로 되돌려 주지 않는다"


def test_결과_수_상한이_강제된다(sdb):
    now = int(time.time())

    async def _go():
        for i in range(20):
            await _seed(f"{i:032x}", f"채널{i}", now=now)
        return [await rr.quick_search(q="채널", limit=999),
                await rr.quick_search(q="채널", limit=0),
                await rr.quick_search(q="채널", limit=3)]

    big, zero, three = sdb(_go())
    assert len(big["results"]) == rr.QUICK_SEARCH_MAX_RESULTS
    assert len(zero["results"]) == 1, "0 이하는 1로 올린다"
    assert len(three["results"]) == 3


def test_like_와일드카드가_무력화된다(sdb):
    """`%` 하나로 전체 목록이 나오면 상한이 있어도 사실상 덤프다."""
    now = int(time.time())

    async def _go():
        await _seed(A, "가나다", now=now)
        await _seed(B, "라마바", now=now)
        await _seed(C, "100%완성", now=now)
        return [await rr.quick_search(q="%"), await rr.quick_search(q="_"),
                await rr.quick_search(q="100%")]

    pct, under, literal = sdb(_go())
    # `%`가 와일드카드였다면 3건 전부가 나온다. 리터럴로 취급되므로 이름에 실제로
    # `%`가 든 채널 하나만 나오는 것이 **정상 동작의 증거**다.
    assert [r["channel_id"] for r in pct["results"]] == [C], \
        "%가 와일드카드로 동작하면 목록이 통째로 덤프된다"
    assert under["results"] == [], "_도 리터럴이어야 한다(어떤 이름에도 _가 없다)"
    assert [r["channel_id"] for r in literal["results"]] == [C], \
        "리터럴 %는 정상적으로 찾아져야 한다"


def test_공개_응답에_내부_지표가_없다(sdb):
    now = int(time.time())

    async def _go():
        await _seed(A, "테스트", now=now, live=True, viewers=7)
        return await rr.quick_search(q="테스트")

    row = sdb(_go())["results"][0]
    assert set(row) == {"channel_id", "channel_name", "channel_image_url",
                        "open_live", "concurrent_viewers"}
    # 팔로워·first_seen·last_seen 같은 값은 헤더 드롭다운이 쓰지 않는다
    assert not ({"follower_count", "first_seen", "last_seen"} & set(row))


def test_수집_전에도_500이_나지_않는다(sdb):
    """`_latest_run_ts()`가 None인 초기 상태에서도 동작해야 한다."""
    async def _go():
        await _seed(A, "테스트", now=int(time.time()))
        return await rr.quick_search(q="테스트")

    res = sdb(_go())
    assert len(res["results"]) == 1
    assert res["results"][0]["open_live"] is False


def test_heavy_버킷에_들어가지_않는다():
    """헤더 검색이 분당 40회로 묶이면 정상 사용자가 즉시 걸린다."""
    import rate_limit as rl
    assert not rl._is_heavy("/api/rising/quick-search")
    # 기존 /search 는 그대로 heavy여야 한다(외부 API를 호출한다)
    assert rl._is_heavy("/api/rising/search")


def test_외부_호출_경로가_없다():
    """소스에 httpx 호출이 섞여 들어가지 않았는지 — 전역 헤더의 핵심 계약이다."""
    import inspect
    src = inspect.getsource(rr.quick_search)
    for bad in ("httpx", "AsyncClient", "_CHZZK_API", "_fetch_channel_meta"):
        assert bad not in src, f"quick_search가 외부를 호출한다: {bad}"
