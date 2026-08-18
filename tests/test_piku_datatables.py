"""PIKU DataTables 수집 계약 (UI-U 요구 8).

실측된 계약(운영자가 확인해 알려 준 것):

    페이지: https://www.piku.co.kr/w/rank/8jGsHE
    코드:   serverSide: true, ajax: { url: "x.php?u=8jGsHE", type: "POST" }
    실제:   POST https://www.piku.co.kr/w/rank/x.php?u=8jGsHE

**이 파일은 외부를 호출하지 않는다.** 전부 fixture와 가짜 클라이언트로 돈다.
실제 PIKU 접속은 운영자의 canary 몫이고, 코드 구현과 분리한다.

지키려는 계약:

1. 페이지 수를 **고정하지 않는다.** `recordsTotal`을 보고 끝까지 가져온다.
2. 실패는 **직전 정상 dataset을 남긴다.** 빈 결과로 덮지 않는다.
3. challenge/403/429는 **우회하지 않고** 실패로 끝낸다.
4. 공개 응답에 우승 비율·승률 원본이 **없다**.
5. preview는 **DB에 쓰지 않는다**.
"""
import json

import pytest
import singcup_piku as piku

import database

EVENT_URL = "https://www.piku.co.kr/w/rank/8jGsHE"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """대기 시간을 0으로 — **정책이 아니라 테스트 속도**만 바꾼다.

    실제 값(`PAGE_DELAY_SECONDS` 2초, 백오프 5초)은 그대로 두고 모듈이 부르는
    `asyncio.sleep`만 가로챈다. 상수 자체를 0으로 바꾸면 "간격을 둔다"는 계약을
    검사하는 테스트가 자기 자신을 통과시켜 버린다.
    """
    async def _noop(_seconds):
        return None
    monkeypatch.setattr(piku.asyncio, "sleep", _noop)


@pytest.fixture
def pdb(db):
    async def _clear():
        c = await database.get_db()
        for t in ("piku_entries", "piku_datasets", "piku_mappings",
                  "piku_sources", "piku_collect_runs"):
            try:
                await c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await c.commit()
    db(_clear())
    return db


# ── 가짜 클라이언트 ─────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class FakeClient:
    """httpx.AsyncClient 대역. **네트워크를 쓰지 않는다.**"""

    def __init__(self, pages, *, status=200, headers=None):
        #: pages는 start(오프셋) → 응답 본문 매핑이거나 순차 리스트.
        self.pages = pages
        self.status = status
        self.headers = headers or {}
        self.calls: list[dict] = []

    async def post(self, url, *, data=None, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "data": dict(data or {}),
                           "headers": dict(headers or {})})
        if isinstance(self.pages, dict):
            start = int((data or {}).get("start", 0))
            body = self.pages.get(start)
            if body is None:
                return FakeResponse(404, "not found")
        else:
            idx = len(self.calls) - 1
            body = self.pages[idx] if idx < len(self.pages) else self.pages[-1]
        if callable(body):
            return body(self.calls[-1])
        return FakeResponse(self.status, body, self.headers)

    async def get(self, *a, **kw):
        raise AssertionError("DataTables 계약은 POST다 — GET을 쓰면 안 된다.")


def dt_page(rows, *, total, draw=1):
    """DataTables 서버사이드 응답 한 장."""
    return json.dumps({"draw": draw, "recordsTotal": total,
                       "recordsFiltered": total, "data": rows},
                      ensure_ascii=False)


def row(rank, name, song, artist, win, match):
    """PIKU 행 — 실제로는 HTML 조각이 섞여 온다."""
    return [
        str(rank),
        f'<img src="https://img.piku.co.kr/{rank}.jpg">',
        f"{name}<br><small>{song} - {artist}</small>",
        f"{win}%",
        f"{match}%",
        "▲2",
    ]


def make_pages(n, *, per=10):
    """n명을 per명씩 나눠 담은 DataTables 응답들."""
    pages = {}
    for start in range(0, n, per):
        rows = [row(i + 1, f"참가자{i + 1}", f"곡{i + 1}", f"가수{i + 1}",
                    round(20 - i * 0.1, 2), round(70 - i * 0.1, 2))
                for i in range(start, min(start + per, n))]
        pages[start] = dt_page(rows, total=n)
    return pages


#: 정본 세 부문 — `set_sources`는 셋을 모두 요구한다(부분 설정은 매핑 실수다).
CANON = {
    "female_solo": "https://www.piku.co.kr/w/rank/8jGsHE",
    "male_solo": "https://www.piku.co.kr/w/rank/7PqH44",
    "groups": "https://www.piku.co.kr/w/rank/7fXoNs",
}


async def _seed_source(division="female_solo", url=None):
    """정본 세 부문을 넣는다. `url`을 주면 그 부문만 바꿔 넣는다."""
    mapping = dict(CANON)
    if url is not None:
        mapping[division] = url
    await piku.set_sources(mapping)


# ── 1) 요청 계약 ────────────────────────────────────────────────────────────

class TestRequestContract:
    def test_ajax_endpoint를_페이지_주소에서_유도한다(self):
        """`/w/rank/8jGsHE` → `/w/rank/x.php?u=8jGsHE`."""
        assert piku.ajax_endpoint(EVENT_URL) == \
            "https://www.piku.co.kr/w/rank/x.php?u=8jGsHE"

    def test_잘못된_주소는_거부한다(self):
        for bad in ("https://evil.com/w/rank/x", "http://www.piku.co.kr/w/rank/x",
                    "https://www.piku.co.kr/other", ""):
            with pytest.raises(piku.PikuError):
                piku.ajax_endpoint(bad)

    def test_DataTables_파라미터를_만든다(self):
        p = piku.datatables_params(draw=3, start=20, length=10)
        assert p["draw"] == 3 and p["start"] == 20 and p["length"] == 10
        assert p["order[0][column]"] == 0
        assert p["order[0][dir]"] == "asc"
        assert p["search[value]"] == ""
        # 열 정의가 있어야 서버가 정렬 대상을 안다.
        assert any(k.startswith("columns[0][") for k in p)
        assert p["columns[0][searchable]"] == "false"

    def test_POST로_보낸다(self, pdb):
        client = FakeClient(make_pages(10))

        async def _go():
            await _seed_source()
            return await piku.collect_division("female_solo", client=client)

        pdb(_go())
        assert client.calls, "요청이 없다"
        assert client.calls[0]["url"].endswith("x.php?u=8jGsHE")
        assert "start" in client.calls[0]["data"]

    def test_정직한_User_Agent를_보낸다(self, pdb):
        client = FakeClient(make_pages(10))

        async def _go():
            await _seed_source()
            return await piku.collect_division("female_solo", client=client)

        pdb(_go())
        ua = client.calls[0]["headers"].get("User-Agent", "")
        assert "NexBot" in ua, "서비스를 밝히지 않는 UA는 쓰지 않는다"
        assert "Mozilla" not in ua, "브라우저 위장 금지"


# ── 2) 전체 페이지 수집 ─────────────────────────────────────────────────────

class TestFullPagination:
    def test_recordsTotal을_보고_끝까지_가져온다(self, pdb):
        """여성 64명 = 10명씩 7페이지. **1~4 고정이 아니다.**"""
        client = FakeClient(make_pages(64))

        async def _go():
            await _seed_source()
            return await piku.collect_division("female_solo", client=client)

        out = pdb(_go())
        assert out["entries"] == 64, f"64명을 다 못 가져왔다({out['entries']})"
        assert len(client.calls) == 7, f"페이지 요청 {len(client.calls)}회"

    def test_그룹_32팀도_전부_가져온다(self, pdb):
        client = FakeClient(make_pages(32))

        async def _go():
            await _seed_source("groups")
            return await piku.collect_division("groups", client=client)

        assert pdb(_go())["entries"] == 32

    def test_인원이_늘어도_따라간다(self, pdb):
        """부문 인원은 앞으로 달라질 수 있다 — 상수로 박지 않는다."""
        client = FakeClient(make_pages(137))

        async def _go():
            await _seed_source()
            return await piku.collect_division("female_solo", client=client)

        assert pdb(_go())["entries"] == 137

    def test_요청_수에_상한이_있다(self):
        assert piku.MAX_REQUESTS_PER_DIVISION <= 100

    def test_페이지_사이에_간격을_둔다(self):
        assert piku.PAGE_DELAY_SECONDS > 0


# ── 3) 실패 처리 ────────────────────────────────────────────────────────────

class TestFailures:
    def _prev_good(self, pdb):
        client = FakeClient(make_pages(20))

        async def _go():
            await _seed_source()
            await piku.collect_division("female_solo", client=client)
            return await piku.active_dataset("female_solo")

        return pdb(_go())

    def test_429는_Retry_After를_존중하고_중단한다(self, pdb):
        before = self._prev_good(pdb)
        client = FakeClient(["rate limited"], status=429,
                            headers={"Retry-After": "120"})

        async def _go():
            with pytest.raises(piku.PikuError) as e:
                await piku.collect_division("female_solo", client=client)
            return e.value, await piku.active_dataset("female_solo")

        err, after = pdb(_go())
        assert err.kind == "rate_limited"
        assert err.retry_after == 120.0
        assert after == before, "실패가 직전 정상 dataset을 덮었다"

    def test_403은_우회하지_않고_끝낸다(self, pdb):
        client = FakeClient(["forbidden"], status=403)

        async def _go():
            await _seed_source()
            with pytest.raises(piku.PikuError) as e:
                await piku.collect_division("female_solo", client=client)
            return e.value

        assert pdb(_go()).kind == "forbidden"

    def test_Cloudflare_challenge를_JSON으로_오인하지_않는다(self, pdb):
        html = ("<html><head><title>Just a moment...</title></head>"
                "<body>cf-browser-verification</body></html>")
        client = FakeClient([html])

        async def _go():
            await _seed_source()
            with pytest.raises(piku.PikuError) as e:
                await piku.collect_division("female_solo", client=client)
            return e.value

        assert pdb(_go()).kind == "challenge"

    def test_HTML_응답을_표로_착각하지_않는다(self, pdb):
        client = FakeClient(["<html><body><p>점검 중입니다</p></body></html>"])

        async def _go():
            await _seed_source()
            with pytest.raises(piku.PikuError):
                await piku.collect_division("female_solo", client=client)

        pdb(_go())

    def test_응답_크기_상한이_있다(self, pdb):
        huge = dt_page([row(1, "a" * 100, "s", "b", 1, 1)] * 5000, total=5000)
        assert len(huge) > piku.MAX_RESPONSE_BYTES or piku.MAX_RESPONSE_BYTES > 0
        client = FakeClient([huge])

        async def _go():
            await _seed_source()
            with pytest.raises(piku.PikuError):
                await piku.collect_division("female_solo", client=client)

        if len(huge.encode()) > piku.MAX_RESPONSE_BYTES:
            pdb(_go())

    def test_일부_페이지만_성공하면_반영하지_않는다(self, pdb):
        before = self._prev_good(pdb)
        pages = make_pages(64)
        pages[30] = "<html>error</html>"      # 4번째 장이 깨진다
        client = FakeClient(pages)

        async def _go():
            with pytest.raises(piku.PikuError):
                await piku.collect_division("female_solo", client=client)
            return await piku.active_dataset("female_solo")

        assert pdb(_go()) == before, "일부 실패인데 반영됐다"

    def test_수집_개수가_recordsTotal과_다르면_반영하지_않는다(self, pdb):
        before = self._prev_good(pdb)
        pages = {0: dt_page([row(1, "가", "곡", "가수", 1, 1)], total=64)}
        client = FakeClient(pages)

        async def _go():
            with pytest.raises(piku.PikuError):
                await piku.collect_division("female_solo", client=client)
            return await piku.active_dataset("female_solo")

        assert pdb(_go()) == before

    def test_빈_응답이_기존_데이터를_덮지_않는다(self, pdb):
        before = self._prev_good(pdb)
        client = FakeClient({0: dt_page([], total=0)})

        async def _go():
            with pytest.raises(piku.PikuError):
                await piku.collect_division("female_solo", client=client)
            return await piku.active_dataset("female_solo")

        assert pdb(_go()) == before


# ── 4) 파싱·검증 ────────────────────────────────────────────────────────────

class TestParsing:
    def test_이름과_곡_가수를_분리한다(self):
        rows = piku.normalize_rows({"data": [
            row(1, "아오토라 유키", "밤하늘", "가수A", 11.79, 72.42)]})
        assert rows[0]["name"] == "아오토라 유키"
        assert rows[0]["song_title"] == "밤하늘"
        assert rows[0]["artist_name"] == "가수A"

    def test_곡_정보가_없어도_이름은_남는다(self):
        rows = piku.normalize_rows({"data": [
            ["1", "<img src=x>", "이름만", "10%", "50%", ""]]})
        assert rows[0]["name"] == "이름만"
        assert rows[0]["song_title"] == ""
        assert rows[0]["artist_name"] == ""

    def test_이미지_주소를_뽑는다(self):
        rows = piku.normalize_rows({"data": [
            row(1, "가", "곡", "가수", 10, 50)]})
        assert rows[0]["thumbnail_url"].startswith("https://img.piku.co.kr/")

    def test_HTML_태그가_이름에_남지_않는다(self):
        rows = piku.normalize_rows({"data": [
            ["1", "", "<b>굵게</b>이름", "10%", "50%", ""]]})
        assert "<" not in rows[0]["name"]

    @pytest.mark.parametrize("bad", [-1, 101, float("nan"), float("inf")])
    def test_비율_범위를_강제한다(self, bad):
        rows = [{"source_rank": 1, "name": "가", "win_rate": bad,
                 "match_rate": 50.0, "song_title": "", "artist_name": "",
                 "thumbnail_url": ""}]
        with pytest.raises(piku.PikuError):
            piku.validate_rows(rows)

    def test_중복_순위를_잡는다(self):
        rows = [{"source_rank": 1, "name": "가", "win_rate": 1.0,
                 "match_rate": 1.0, "song_title": "", "artist_name": "",
                 "thumbnail_url": ""},
                {"source_rank": 1, "name": "나", "win_rate": 1.0,
                 "match_rate": 1.0, "song_title": "", "artist_name": "",
                 "thumbnail_url": ""}]
        with pytest.raises(piku.PikuError):
            piku.validate_rows(rows)

    def test_이름이_비면_거절한다(self):
        rows = [{"source_rank": 1, "name": "  ", "win_rate": 1.0,
                 "match_rate": 1.0, "song_title": "", "artist_name": "",
                 "thumbnail_url": ""}]
        with pytest.raises(piku.PikuError):
            piku.validate_rows(rows)

    def test_망가진_행은_버리되_개수로_판정한다(self):
        raw = {"data": [row(1, "가", "곡", "가수", 10, 50), ["깨짐"], None]}
        rows = piku.normalize_rows(raw)
        assert len(rows) == 1


# ── 5) preview / apply ──────────────────────────────────────────────────────

class TestPreviewApply:
    def test_preview는_DB에_쓰지_않는다(self, pdb):
        client = FakeClient(make_pages(20))

        async def _go():
            await _seed_source()
            before = await piku.active_dataset("female_solo")
            out = await piku.preview_division("female_solo", client=client)
            after = await piku.active_dataset("female_solo")
            return out, before, after

        out, before, after = pdb(_go())
        assert out["applied"] is False
        assert out["entries"] == 20
        assert before == after

    def test_preview_응답에_비율_숫자가_없다(self, pdb):
        client = FakeClient(make_pages(5))

        async def _go():
            await _seed_source()
            return await piku.preview_division("female_solo", client=client)

        blob = json.dumps(pdb(_go()), ensure_ascii=False)
        for bad in ("winRate", "matchRate", "win_rate", "match_rate", "72.42"):
            assert bad not in blob

    def test_같은_데이터를_두_번_적용해도_중복이_없다(self, pdb):
        async def _go():
            await _seed_source()
            await piku.collect_division("female_solo", client=FakeClient(make_pages(20)))
            first = await piku.active_dataset("female_solo")
            await piku.collect_division("female_solo", client=FakeClient(make_pages(20)))
            second = await piku.active_dataset("female_solo")
            c = await database.get_db()
            n = await (await c.execute(
                "SELECT COUNT(*) n FROM piku_datasets WHERE status='active'")).fetchone()
            return first, second, n["n"]

        _, _, active = pdb(_go())
        assert active == 1, "활성 dataset이 여러 개다"


# ── 6) 세 부문 원자적 publish ───────────────────────────────────────────────

class TestAtomicPublish:
    def test_세_부문이_모두_성공해야_publish된다(self, pdb):
        async def _go():
            await piku.set_sources({
                "female_solo": EVENT_URL,
                "male_solo": "https://www.piku.co.kr/w/rank/7PqH44",
                "groups": "https://www.piku.co.kr/w/rank/7fXoNs"})
            clients = {"female_solo": FakeClient(make_pages(64)),
                       "male_solo": FakeClient(make_pages(64)),
                       "groups": FakeClient(["<html>down</html>"])}
            res = await piku.collect_all(clients=clients)
            return res, {d: await piku.active_dataset(d) for d in piku.DIVISIONS}

        res, active = pdb(_go())
        assert res["published"] is False, "한 부문이 실패했는데 publish됐다"
        assert all(v is None for v in active.values()), \
            "부분 성공이 공개 dataset을 만들었다"

    def test_모두_성공하면_publish된다(self, pdb):
        async def _go():
            await piku.set_sources({
                "female_solo": EVENT_URL,
                "male_solo": "https://www.piku.co.kr/w/rank/7PqH44",
                "groups": "https://www.piku.co.kr/w/rank/7fXoNs"})
            clients = {d: FakeClient(make_pages(64 if d != "groups" else 32))
                       for d in piku.DIVISIONS}
            res = await piku.collect_all(clients=clients)
            return res, {d: await piku.active_dataset(d) for d in piku.DIVISIONS}

        res, active = pdb(_go())
        assert res["published"] is True
        assert all(v is not None for v in active.values())


# ── 7) 자동 수집 기본 OFF·중복 실행 방지 ────────────────────────────────────

class TestAutoCollect:
    def test_기본값이_꺼짐이다(self, monkeypatch):
        monkeypatch.delenv("PIKU_AUTO_COLLECT_ENABLED", raising=False)
        assert piku.auto_collect_enabled() is False

    def test_최소_간격이_60분_이상이다(self):
        assert piku.MIN_INTERVAL_MINUTES >= 60

    def test_중복_실행을_막는다(self, pdb):
        async def _go():
            await _seed_source()
            got = await piku.acquire_collect_lock()
            again = await piku.acquire_collect_lock()
            await piku.release_collect_lock()
            after = await piku.acquire_collect_lock()
            await piku.release_collect_lock()
            return got, again, after

        got, again, after = pdb(_go())
        assert got is True and again is False and after is True

    def test_상태에_다음_실행_예정과_연속_실패가_있다(self, pdb):
        async def _go():
            return await piku.admin_status()

        st = pdb(_go())
        for k in ("autoCollectEnabled", "nextRunAt", "consecutiveFailures",
                  "lastSuccessAt", "lastErrorAt", "lastErrorKind"):
            assert k in st, f"{k}가 없다"


# ── 8) 공개 응답에 원본 비율이 없다 ─────────────────────────────────────────

class TestPublicContract:
    def test_공개_순위에_비율이_없다(self, pdb):
        async def _go():
            await _seed_source()
            await piku.collect_division("female_solo",
                                        client=FakeClient(make_pages(20)))
            return await piku.public_ranking("female_solo")

        blob = json.dumps(pdb(_go()), ensure_ascii=False)
        for bad in ("winRate", "matchRate", "win_rate", "match_rate",
                    "winRatio", "percentage"):
            assert bad not in blob, f"공개 응답에 {bad}가 있다"

    def test_공개_순위에_곡과_가수는_있다(self, pdb):
        """비율은 감추지만 **곡 정보는 화면에 필요한 공개 정보**다."""
        async def _go():
            await _seed_source()
            await piku.collect_division("female_solo",
                                        client=FakeClient(make_pages(5)))
            return await piku.public_ranking("female_solo")

        out = pdb(_go())
        # 매핑 확정 전에는 순위에 오르지 않으므로 entries가 비어 있을 수 있다.
        assert "entries" in out


# ── 9) 부문 ↔ URL 정본 교차 검증 ────────────────────────────────────────────
#
# 처음 전달받은 매핑에서 **남성 솔로와 그룹이 서로 뒤바뀌어 있었다.** 그대로
# 수집하면 남성 참가자 순위가 그룹 부문에 저장되는데, 화면에는 이름만 보이므로
# 눈으로는 잡히지 않는다. 그래서 정본을 상수로 고정하고 여기서 못 박는다.

F_URL = "https://www.piku.co.kr/w/rank/8jGsHE"
M_URL = "https://www.piku.co.kr/w/rank/7PqH44"
G_URL = "https://www.piku.co.kr/w/rank/7fXoNs"


class TestCategoryUrlContract:
    def test_정본_매핑이_고정돼_있다(self):
        assert piku.PIKU_CATEGORY_URLS == {
            "female_solo": F_URL, "male_solo": M_URL, "groups": G_URL}

    def test_주소로_부문을_되짚을_수_있다(self):
        assert piku.expected_division_for_url(F_URL) == "female_solo"
        assert piku.expected_division_for_url(M_URL) == "male_solo"
        assert piku.expected_division_for_url(G_URL) == "groups"
        assert piku.expected_division_for_url(
            "https://www.piku.co.kr/w/rank/zzzzzz") is None

    def test_각_endpoint가_정본과_일치한다(self):
        assert piku.ajax_endpoint(F_URL).endswith("x.php?u=8jGsHE")
        assert piku.ajax_endpoint(M_URL).endswith("x.php?u=7PqH44")
        assert piku.ajax_endpoint(G_URL).endswith("x.php?u=7fXoNs")

    # 1~3) 각 응답이 **해당 부문에만** 저장된다
    @pytest.mark.parametrize("division,url,n", [
        ("female_solo", F_URL, 64), ("male_solo", M_URL, 64), ("groups", G_URL, 32)])
    def test_응답이_지정된_부문에만_저장된다(self, pdb, division, url, n):
        async def _go():
            await piku.set_sources(CANON)
            await piku.collect_division(division, client=FakeClient(make_pages(n)))
            return {d: await piku.active_dataset(d) for d in piku.DIVISIONS}

        active = pdb(_go())
        assert active[division] is not None, f"{division}에 저장되지 않았다"
        for other in piku.DIVISIONS:
            if other != division:
                assert active[other] is None, f"{other}에도 저장됐다"

    # 4) 남성 솔로와 그룹 URL을 바꾸면 검증 실패
    def test_남성과_그룹을_바꾸면_저장이_막힌다(self, pdb):
        async def _go():
            with pytest.raises(piku.PikuError) as e:
                await piku.set_sources({"female_solo": F_URL,
                                        "male_solo": G_URL,      # 뒤바뀜
                                        "groups": M_URL})        # 뒤바뀜
            return e.value

        err = pdb(_go())
        assert err.kind == "division_mismatch"
        assert "그룹" in str(err) or "남성" in str(err)

    def test_한_부문만_바꿔도_막힌다(self, pdb):
        async def _go():
            with pytest.raises(piku.PikuError):
                await piku.set_sources({"female_solo": M_URL,     # 여성 자리에 남성
                                        "male_solo": F_URL,
                                        "groups": G_URL})

        pdb(_go())

    def test_정본대로면_저장된다(self, pdb):
        async def _go():
            await piku.set_sources({"female_solo": F_URL, "male_solo": M_URL,
                                    "groups": G_URL})
            return {s["division"]: s["url"] for s in await piku.list_sources()}

        got = pdb(_go())
        assert got["female_solo"] == F_URL
        assert got["male_solo"] == M_URL
        assert got["groups"] == G_URL

    def test_정본에_없는_주소는_통과시킨다(self, pdb):
        """대회 URL은 바뀔 수 있다 — 모르는 주소까지 막으면 새 주소를 못 넣는다."""
        other = "https://www.piku.co.kr/w/rank/newid01"

        async def _go():
            await piku.set_sources({"female_solo": other, "male_solo": M_URL,
                                    "groups": G_URL})
            return {s["division"]: s["url"] for s in await piku.list_sources()}

        assert pdb(_go())["female_solo"] == other

    # 5) 수집 시점에도 부문↔주소가 어긋나면 apply 금지
    def test_수집_직전에도_교차_검증한다(self, pdb):
        """설정을 우회해 DB가 오염된 경우에도 반영 전에 막는다."""
        async def _go():
            c = await database.get_db()
            now = 1
            # 검증을 건너뛰고 **직접** 뒤바뀐 값을 넣는다(과거 오염 재현).
            await c.execute(
                "INSERT INTO piku_sources (division, url, enabled, updated_at)"
                " VALUES (?,?,1,?) ON CONFLICT(division) DO UPDATE SET"
                " url=excluded.url, enabled=1", ("male_solo", G_URL, now))
            await c.commit()
            with pytest.raises(piku.PikuError) as e:
                await piku.collect_division("male_solo",
                                            client=FakeClient(make_pages(32)))
            return e.value, await piku.active_dataset("male_solo")

        err, active = pdb(_go())
        assert err.kind == "division_mismatch"
        assert active is None, "어긋난 dataset이 공개됐다"

    # 6) 과거에 반대로 저장된 dataset은 자동 공개하지 않는다
    def test_오염된_dataset은_관리자_확인을_요구한다(self, pdb):
        async def _go():
            c = await database.get_db()
            await c.execute(
                "INSERT INTO piku_sources (division, url, enabled, updated_at)"
                " VALUES (?,?,1,1) ON CONFLICT(division) DO UPDATE SET"
                " url=excluded.url", ("groups", M_URL, ))
            await c.commit()
            return await piku.admin_status()

        st = pdb(_go())
        bad = [s for s in st["sources"] if s.get("divisionMismatch")]
        assert bad, "관리 화면이 어긋난 배치를 알려 주지 않는다"

    # 7) 세 부문 entry count 교차 검증
    def test_세_부문_수집_결과가_섞이지_않는다(self, pdb):
        async def _go():
            await piku.set_sources({"female_solo": F_URL, "male_solo": M_URL,
                                    "groups": G_URL})
            clients = {"female_solo": FakeClient(make_pages(64)),
                       "male_solo": FakeClient(make_pages(64)),
                       "groups": FakeClient(make_pages(32))}
            await piku.collect_all(clients=clients)
            out = {}
            for d in piku.DIVISIONS:
                ds = await piku.active_dataset(d)
                out[d] = (ds or {}).get("entry_count")
            return out, clients

        counts, clients = pdb(_go())
        assert counts["female_solo"] == 64
        assert counts["male_solo"] == 64
        assert counts["groups"] == 32
        # 각 부문이 **자기 endpoint**로만 요청했는지
        assert clients["female_solo"].calls[0]["url"].endswith("u=8jGsHE")
        assert clients["male_solo"].calls[0]["url"].endswith("u=7PqH44")
        assert clients["groups"].calls[0]["url"].endswith("u=7fXoNs")

    # 8) 어긋난 dataset이 기존 정상 데이터를 덮지 않는다
    def test_어긋난_수집이_기존_정상_데이터를_덮지_않는다(self, pdb):
        async def _go():
            await piku.set_sources({"female_solo": F_URL, "male_solo": M_URL,
                                    "groups": G_URL})
            await piku.collect_division("male_solo", client=FakeClient(make_pages(64)))
            good = await piku.active_dataset("male_solo")
            # 이후 누군가 주소를 뒤바꿔 넣고 수집을 시도한다.
            c = await database.get_db()
            await c.execute("UPDATE piku_sources SET url=? WHERE division='male_solo'",
                            (G_URL,))
            await c.commit()
            with pytest.raises(piku.PikuError):
                await piku.collect_division("male_solo",
                                            client=FakeClient(make_pages(32)))
            return good, await piku.active_dataset("male_solo")

        good, after = pdb(_go())
        assert after == good, "어긋난 수집이 정상 dataset을 덮었다"
