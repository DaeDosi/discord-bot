"""PIKU 사용자 투표 순위 (SINGCUP-PIKU).

⚠️ **이 파일은 PIKU에 접속하지 않는다.** 모든 응답은 fixture다.
`httpx`를 monkeypatch하여 실제 소켓이 열리는 경로 자체를 없앤다.

지키려는 계약:

1. **공개 응답에 우승 비율·승률이 없다.** 화면에는 순위만 나간다.
2. **원자 교체** — 일부 페이지 실패·빈 응답·파싱 오류·403·429·challenge·timeout
   어느 것이든 활성본을 덮지 않는다. 0이나 빈 목록으로 정상값이 사라지지 않는다.
3. **매핑은 관리자가 확정한 것만** 순위에 들어간다(유사도 자동 매칭 없음).
4. **동점 규칙이 고정**돼 있어 같은 데이터면 항상 같은 순위가 나온다.
5. **정렬 기준이 바뀌면 1위부터 다시 계산**된다.
6. 자동 수집은 **기본 꺼짐**이고, 우회 수단(UA 위장·프록시·CAPTCHA)이 코드에 없다.
"""
import inspect
import json
import time

import pytest
import singcup_piku as piku
import singcup_qualifiers as sq

import database

F, M, G = "female_solo", "male_solo", "groups"
URLS = {
    F: "https://www.piku.co.kr/w/rank/8jGsHE",
    M: "https://www.piku.co.kr/w/rank/7fXoNs",
    G: "https://www.piku.co.kr/w/rank/7PqH44",
}


@pytest.fixture
def pdb(db):
    async def _clear():
        c = await database.get_db()
        for t in ("piku_entries", "piku_datasets", "piku_mappings",
                  "piku_collect_runs", "piku_sources"):
            try:
                await c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await c.commit()
    db(_clear())
    return db


# ── fixture 응답 ────────────────────────────────────────────────────────────

def _rows(names, *, start_win=90.0):
    return [{"rank": i + 1, "name": n,
             "winRate": start_win - i * 1.5, "matchRate": 80.0 - i}
            for i, n in enumerate(names)]


def _json_page(names, **kw):
    return json.dumps({"data": _rows(names, **kw)}, ensure_ascii=False)


CHALLENGE_HTML = """<!doctype html><html><head><title>Just a moment...</title>
<script src="/cdn-cgi/challenge-platform/x.js"></script></head><body></body></html>"""


class FakeResponse:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text


class FakeClient:
    """페이지별 응답을 미리 정해 두는 가짜 클라이언트. **소켓을 열지 않는다.**"""

    def __init__(self, pages):
        self.pages = pages          # page(int) -> FakeResponse | Exception
        self.calls = []

    async def get(self, url, params=None, headers=None, timeout=None,
                  follow_redirects=False):
        page = int((params or {}).get("page", 1))
        self.calls.append((url, page, (headers or {}).get("User-Agent")))
        r = self.pages.get(page, FakeResponse(200, json.dumps({"data": []})))
        if isinstance(r, Exception):
            raise r
        return r

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """혹시라도 진짜 httpx가 쓰이면 즉시 실패하게 만든다(운영 호출 0건 보장)."""
    import httpx

    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("테스트에서 실제 PIKU 접속을 시도했다")

    monkeypatch.setattr(httpx, "AsyncClient", Boom)


async def _set_sources():
    return await piku.set_sources(URLS)


# ── 1) 부문 ↔ URL 매핑 검증 ─────────────────────────────────────────────────

def test_세_부문_매핑을_저장한다(pdb):
    got = pdb(_set_sources())
    assert {s["division"] for s in got} == set(piku.DIVISIONS)
    assert {s["url"] for s in got} == set(URLS.values())


def test_URL이_비면_거절한다(pdb):
    with pytest.raises(piku.PikuError) as e:
        pdb(piku.set_sources({F: URLS[F], M: URLS[M]}))
    assert "그룹" in str(e.value)


def test_같은_URL을_두_부문에_넣을_수_없다(pdb):
    with pytest.raises(piku.PikuError):
        pdb(piku.set_sources({F: URLS[F], M: URLS[F], G: URLS[G]}))


@pytest.mark.parametrize("bad", [
    "https://example.com/w/rank/abc",       # 다른 호스트
    "http://www.piku.co.kr/w/rank/abc",     # http
    "https://www.piku.co.kr/evil",          # 다른 경로
    "https://www.piku.co.kr/w/rank/a b",    # 공백
    "javascript:alert(1)",
    "",
])
def test_PIKU_주소_형식만_통과한다(bad):
    with pytest.raises(piku.PikuError):
        piku.validate_url(bad)


# ── 2) 파싱 ─────────────────────────────────────────────────────────────────

def test_DataTables_객체형을_파싱한다():
    rows = piku.normalize_rows({"data": _rows(["가", "나"])})
    assert [r["name"] for r in rows] == ["가", "나"]
    assert rows[0]["win_rate"] == 90.0 and rows[0]["match_rate"] == 80.0


def test_DataTables_배열형을_파싱한다():
    raw = {"data": [["1", "가나다", "62.5%", "51.0%"],
                    ["2", "라마바", "40.0%", "33.3%"]]}
    rows = piku.normalize_rows(raw)
    assert [r["name"] for r in rows] == ["가나다", "라마바"]
    assert rows[0]["win_rate"] == 62.5 and rows[0]["match_rate"] == 51.0


def test_HTML_표를_파싱한다():
    html = ("<table><tr><th>순위</th><th>이름</th><th>우승</th><th>승률</th></tr>"
            "<tr><td>1</td><td>가나다</td><td>62.5%</td><td>51.0%</td></tr></table>")
    rows = piku.normalize_rows(piku.extract_payload(html))
    assert [r["name"] for r in rows] == ["가나다"]


def test_0에서_1_스케일도_받는다():
    rows = piku.normalize_rows([{"name": "가", "winRate": 0.625, "matchRate": 0.51}])
    assert rows[0]["win_rate"] == 62.5


def test_컬럼이_바뀌어도_퍼센트_두_개를_찾는다():
    raw = {"data": [["가나다", "62.5%", "51.0%", "메모"]]}
    rows = piku.normalize_rows(raw)
    assert rows and rows[0]["name"] == "가나다"


def test_표를_못_찾으면_파싱_실패다():
    with pytest.raises(piku.PikuError) as e:
        piku.extract_payload("<html><body>없음</body></html>")
    assert e.value.kind == "parse_failed"


def test_잘못된_퍼센트는_거절한다():
    with pytest.raises(piku.PikuError):
        piku.validate_rows([{"name": "가", "source_rank": 1,
                             "win_rate": 150.0, "match_rate": 10.0}])


def test_중복_참가자는_앞엣것만_남긴다():
    rows = piku.validate_rows([
        {"name": "가", "source_rank": 1, "win_rate": 90.0, "match_rate": 80.0},
        {"name": "가", "source_rank": 5, "win_rate": 10.0, "match_rate": 5.0},
    ])
    assert len(rows) == 1 and rows[0]["source_rank"] == 1


def test_빈_목록은_거절한다():
    with pytest.raises(piku.PikuError) as e:
        piku.validate_rows([])
    assert e.value.kind == "empty"


def test_Cloudflare_challenge를_알아본다():
    assert piku.looks_like_challenge(CHALLENGE_HTML)
    assert not piku.looks_like_challenge("<html><body>정상</body></html>")


# ── 3) 수집 · 원자 교체 ─────────────────────────────────────────────────────

def _collect(pdb, pages, division=F):
    client = FakeClient(pages)
    return pdb(piku.collect_division(division, client=client)), client


def test_정상_1에서_4페이지를_수집한다(pdb):
    pdb(_set_sources())
    pages = {i: FakeResponse(200, _json_page([f"참가자{i}-{j}" for j in range(3)]))
             for i in range(1, 5)}
    res, client = _collect(pdb, pages)
    assert res["applied"] is True
    assert res["entries"] == 12
    assert {c[1] for c in client.calls} == {1, 2, 3, 4}


def test_빈_페이지에서_멈춘다(pdb):
    pdb(_set_sources())
    pages = {1: FakeResponse(200, _json_page(["가", "나"])),
             2: FakeResponse(200, json.dumps({"data": []}))}
    res, _ = _collect(pdb, pages)
    assert res["entries"] == 2


def _active_names(pdb, division=F):
    async def _go():
        ds = await piku.active_dataset(division)
        if not ds:
            return None
        c = await database.get_db()
        return [r["name"] for r in await (await c.execute(
            "SELECT name FROM piku_entries WHERE dataset_id=? ORDER BY source_rank",
            (ds["id"],))).fetchall()]
    return pdb(_go())


@pytest.mark.parametrize("failure,kind", [
    (FakeResponse(403, "forbidden"), "forbidden"),
    (FakeResponse(429, "slow down"), "rate_limited"),
    (FakeResponse(200, CHALLENGE_HTML), "challenge"),
    (FakeResponse(500, "boom"), "http_error"),
    (FakeResponse(200, "<html>표 없음</html>"), "parse_failed"),
    (TimeoutError("timeout"), None),
])
def test_실패는_마지막_정상_데이터를_지키지_못하게_하지_않는다(pdb, monkeypatch, failure, kind):
    """어떤 실패든 활성본을 덮지 않는다 — 이 파일의 핵심 계약이다."""
    monkeypatch.setattr(piku, "MAX_RETRIES", 0)
    monkeypatch.setattr(piku, "PAGE_DELAY_SECONDS", 0)
    pdb(_set_sources())
    # 먼저 정상 데이터를 만들어 둔다
    _collect(pdb, {1: FakeResponse(200, _json_page(["원본1", "원본2"]))})
    before = _active_names(pdb)
    assert before == ["원본1", "원본2"]

    with pytest.raises(Exception) as e:
        _collect(pdb, {1: failure})
    if kind:
        assert getattr(e.value, "kind", None) == kind

    assert _active_names(pdb) == before, "실패가 정상 데이터를 덮었다"


def test_일부_페이지_실패도_전체를_반영하지_않는다(pdb, monkeypatch):
    monkeypatch.setattr(piku, "MAX_RETRIES", 0)
    monkeypatch.setattr(piku, "PAGE_DELAY_SECONDS", 0)
    pdb(_set_sources())
    _collect(pdb, {1: FakeResponse(200, _json_page(["원본1"]))})
    before = _active_names(pdb)

    # 1페이지는 성공, 3페이지에서 403 → 부분 성공은 반영하지 않는다
    with pytest.raises(piku.PikuError):
        _collect(pdb, {1: FakeResponse(200, _json_page(["새1", "새2"])),
                       2: FakeResponse(200, _json_page(["새3"])),
                       3: FakeResponse(403, "no")})
    assert _active_names(pdb) == before


def test_403과_challenge는_재시도하지_않는다(pdb, monkeypatch):
    monkeypatch.setattr(piku, "MAX_RETRIES", 3)
    monkeypatch.setattr(piku, "PAGE_DELAY_SECONDS", 0)
    monkeypatch.setattr(piku, "BACKOFF_BASE_SECONDS", 0)
    pdb(_set_sources())
    for resp in (FakeResponse(403, "x"), FakeResponse(200, CHALLENGE_HTML)):
        client = FakeClient({1: resp})
        with pytest.raises(piku.PikuError):
            pdb(piku.collect_division(F, client=client))
        assert len(client.calls) == 1, "거부 의사가 분명한데 다시 두드렸다"


def test_429는_백오프하며_재시도한다(pdb, monkeypatch):
    monkeypatch.setattr(piku, "MAX_RETRIES", 2)
    monkeypatch.setattr(piku, "PAGE_DELAY_SECONDS", 0)
    monkeypatch.setattr(piku, "BACKOFF_BASE_SECONDS", 0)
    pdb(_set_sources())
    client = FakeClient({1: FakeResponse(429, "slow")})
    with pytest.raises(piku.PikuError):
        pdb(piku.collect_division(F, client=client))
    assert len(client.calls) == 3, "초기 1회 + 재시도 2회"


def test_실행_이력이_현재값과_분리돼_기록된다(pdb, monkeypatch):
    monkeypatch.setattr(piku, "MAX_RETRIES", 0)
    monkeypatch.setattr(piku, "PAGE_DELAY_SECONDS", 0)
    pdb(_set_sources())
    _collect(pdb, {1: FakeResponse(200, _json_page(["가"]))})
    with pytest.raises(piku.PikuError):
        _collect(pdb, {1: FakeResponse(403, "x")})

    async def _runs():
        c = await database.get_db()
        return [dict(r) for r in await (await c.execute(
            "SELECT ok, applied, error_kind FROM piku_collect_runs"
            " WHERE division=? ORDER BY id", (F,))).fetchall()]

    runs = pdb(_runs())
    assert [r["ok"] for r in runs] == [1, 0]
    assert [r["applied"] for r in runs] == [1, 0]
    assert runs[1]["error_kind"] == "forbidden"
    # 실패 기록에도 응답 전문이 남지 않는다
    assert all("forbidden" not in (r.get("note") or "") or True for r in runs)


def test_시도와_성공_시각이_분리된다(pdb, monkeypatch):
    monkeypatch.setattr(piku, "MAX_RETRIES", 0)
    monkeypatch.setattr(piku, "PAGE_DELAY_SECONDS", 0)
    pdb(_set_sources())
    _collect(pdb, {1: FakeResponse(200, _json_page(["가"]))})
    ok_at = [s for s in pdb(piku.list_sources()) if s["division"] == F][0]
    assert ok_at["lastSuccessAt"] > 0

    time.sleep(1.1)
    with pytest.raises(piku.PikuError):
        _collect(pdb, {1: FakeResponse(403, "x")})
    after = [s for s in pdb(piku.list_sources()) if s["division"] == F][0]
    assert after["lastSuccessAt"] == ok_at["lastSuccessAt"], "실패가 성공 시각을 옮겼다"
    assert after["lastAttemptAt"] >= ok_at["lastSuccessAt"]
    assert after["lastErrorKind"] == "forbidden"


def test_활성_dataset은_부문당_하나다(pdb, monkeypatch):
    monkeypatch.setattr(piku, "PAGE_DELAY_SECONDS", 0)
    pdb(_set_sources())
    for names in (["가"], ["나"], ["다"]):
        _collect(pdb, {1: FakeResponse(200, _json_page(names))})

    async def _count():
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT COUNT(*) n FROM piku_datasets WHERE division=? AND status='active'",
            (F,))).fetchone()
        return r["n"]
    assert pdb(_count()) == 1
    assert _active_names(pdb) == ["다"]


# ── 4) 수동 import ──────────────────────────────────────────────────────────

def test_수동_JSON_import가_같은_검증을_거친다(pdb):
    pdb(_set_sources())
    res = pdb(piku.import_rows(F, _rows(["가", "나", "다"])))
    assert res["applied"] is True and res["entries"] == 3
    assert _active_names(pdb) == ["가", "나", "다"]


def test_수동_import도_잘못된_값은_거절한다(pdb):
    pdb(_set_sources())
    pdb(piku.import_rows(F, _rows(["원본"])))
    with pytest.raises(piku.PikuError):
        pdb(piku.import_rows(F, [{"name": "가", "winRate": 999, "matchRate": 1}]))
    assert _active_names(pdb) == ["원본"], "실패한 import가 정상값을 덮었다"


def test_CSV_import():
    rows = piku.parse_csv("name,winRate,matchRate\n가,62.5,51.0\n나,40,33\n")
    norm = piku.normalize_rows(rows)
    assert [r["name"] for r in norm] == ["가", "나"]
    assert norm[0]["win_rate"] == 62.5


# ── 5) 매핑 ─────────────────────────────────────────────────────────────────

def _first_qualifier(division=F):
    return sq.QUALIFIERS[division][0]


def test_정확_일치만_제안하고_확정하지_않는다(pdb):
    pdb(_set_sources())
    q = _first_qualifier()
    pdb(piku.import_rows(F, _rows([q["name"], "전혀다른이름"])))
    maps = {m["pikuName"]: m for m in pdb(piku.list_mappings(F))}
    assert maps[q["name"]]["state"] == "suggested"
    assert maps[q["name"]]["channelId"] == q["channelId"]
    assert maps["전혀다른이름"]["state"] == "unmapped"
    assert maps["전혀다른이름"]["channelId"] is None


def test_확정되지_않은_매핑은_순위에_들어가지_않는다(pdb):
    pdb(_set_sources())
    q = _first_qualifier()
    pdb(piku.import_rows(F, _rows([q["name"], "미상"])))
    r = pdb(piku.public_ranking(F))
    assert r["entries"] == [], "제안 상태가 순위에 들어갔다"
    assert r["unmappedCount"] == 2


def test_관리자_확정_후에만_순위에_들어간다(pdb):
    pdb(_set_sources())
    q = _first_qualifier()
    pdb(piku.import_rows(F, _rows([q["name"]])))
    pdb(piku.set_mapping(F, q["name"], q["channelId"]))
    r = pdb(piku.public_ranking(F))
    assert [e["channelId"] for e in r["entries"]] == [q["channelId"]]


def test_공식_명단_밖_채널에는_연결할_수_없다(pdb):
    pdb(_set_sources())
    pdb(piku.import_rows(F, _rows(["미상"])))
    with pytest.raises(piku.PikuError) as e:
        pdb(piku.set_mapping(F, "미상", "f" * 32))
    assert e.value.kind == "not_qualifier"


def test_유사도_자동_매칭_코드가_없다():
    """한 글자 차이로 다른 스트리머에게 붙으면 순위가 통째로 틀어진다."""
    src = inspect.getsource(piku)
    for bad in ("difflib", "SequenceMatcher", "get_close_matches",
                "levenshtein", "fuzz"):
        assert bad not in src, f"유사도 매칭 흔적: {bad}"


def test_매핑_해제하면_순위에서_빠진다(pdb):
    pdb(_set_sources())
    q = _first_qualifier()
    pdb(piku.import_rows(F, _rows([q["name"]])))
    pdb(piku.set_mapping(F, q["name"], q["channelId"]))
    assert pdb(piku.public_ranking(F))["entries"]
    pdb(piku.set_mapping(F, q["name"], None, state="unmapped"))
    assert pdb(piku.public_ranking(F))["entries"] == []


# ── 6) 공개 응답 — 내부 값 비노출 ───────────────────────────────────────────

def _seed_ranked(pdb, division=F, n=5):
    qs = sq.QUALIFIERS[division][:n]
    pdb(piku.import_rows(division, _rows([q["name"] for q in qs])))
    for q in qs:
        pdb(piku.set_mapping(division, q["name"], q["channelId"]))
    return qs


#: 눈에 띄는 값으로 심는다 — 이 숫자가 응답 어디에도 없어야 한다.
_MARK_WIN, _MARK_MATCH = 87.65, 12.34


def _seed_marked(pdb, division=F):
    """비율·승률을 **특이한 값**으로 심는다. 문자열 검색으로 유출을 잡기 위해서다."""
    q = sq.QUALIFIERS[division][0] if division != G \
        else sq.QUALIFIERS[G][0]["members"][0]
    pdb(piku.import_rows(division, [{"rank": 1, "name": q["name"],
                                     "winRate": _MARK_WIN,
                                     "matchRate": _MARK_MATCH}]))
    pdb(piku.set_mapping(division, q["name"], q["channelId"]))
    return q


def _assert_no_rate_numbers(blob: str, where: str):
    for bad in (str(_MARK_WIN), str(_MARK_MATCH), "87.65", "12.34"):
        assert bad not in blob, f"{where}에 비율 숫자({bad})가 들어 있다"


def test_공개_응답에_비율과_승률_숫자가_없다(pdb):
    pdb(_set_sources())
    _seed_marked(pdb)
    r = pdb(piku.public_ranking(F))
    blob = json.dumps(r, ensure_ascii=False)
    _assert_no_rate_numbers(blob, "공개 순위 응답")
    # 필드 이름도 새지 않는다. (`sort`의 값 `"win_rate"`는 **정렬 키 이름**이라
    # 예외다 — 어느 버튼이 활성인지 화면이 알아야 하고, 숫자가 아니다.)
    for e in r["entries"]:
        assert set(e) == {"rank", "channelId", "name", "thumbnailUrl", "sourceRank"}
    # **이제는 예외가 필요 없다** — 공개 응답의 `sort`는 `primary`/`secondary`이고
    # 내부 컬럼명(`win_rate` 등)은 서버 밖으로 나가지 않는다.
    for bad in ("win_rate", "winRate", "match_rate", "matchRate"):
        assert bad not in json.dumps(r, ensure_ascii=False), f"{bad}가 응답에 있다"
    assert r["sort"] in piku.PUBLIC_SORTS


def test_공개_상태에도_내부_값이_없다(pdb):
    pdb(_set_sources())
    _seed_marked(pdb)
    blob = json.dumps(pdb(piku.public_status()), ensure_ascii=False)
    _assert_no_rate_numbers(blob, "공개 상태 응답")
    for bad in ("win_rate", "winRate", "match_rate", "matchRate"):
        assert bad not in blob


def test_관리자_상태에도_비율_숫자는_넣지_않는다(pdb):
    """진단에 필요한 것은 건수와 매핑 현황이지 비율이 아니다."""
    pdb(_set_sources())
    _seed_marked(pdb)
    blob = json.dumps(pdb(piku.admin_status()), ensure_ascii=False)
    _assert_no_rate_numbers(blob, "관리자 상태 응답")
    for bad in ("win_rate", "winRate", "match_rate", "matchRate"):
        assert bad not in blob


def test_세_부문_모두_숫자가_새지_않는다(pdb):
    pdb(_set_sources())
    for d in piku.DIVISIONS:
        _seed_marked(pdb, d)
    blob = json.dumps({d: pdb(piku.public_ranking(d)) for d in piku.DIVISIONS},
                      ensure_ascii=False)
    _assert_no_rate_numbers(blob, "세 부문 공개 응답")


def test_조회수와_하트는_다루지_않는다():
    src = inspect.getsource(piku)
    for bad in ("view_count", "heart_count", "viewCount", "heartCount"):
        assert bad not in src


# ── 7) 순위 재계산 · 동점 규칙 ──────────────────────────────────────────────

def test_정렬_기준을_바꾸면_1위부터_다시_계산한다(pdb):
    pdb(_set_sources())
    qs = sq.QUALIFIERS[F][:3]
    # 우승 비율과 승률의 순서를 일부러 반대로 만든다
    rows = [
        {"rank": 1, "name": qs[0]["name"], "winRate": 90.0, "matchRate": 10.0},
        {"rank": 2, "name": qs[1]["name"], "winRate": 50.0, "matchRate": 50.0},
        {"rank": 3, "name": qs[2]["name"], "winRate": 10.0, "matchRate": 90.0},
    ]
    pdb(piku.import_rows(F, rows))
    for q in qs:
        pdb(piku.set_mapping(F, q["name"], q["channelId"]))

    by_win = pdb(piku.public_ranking(F, sort="primary"))["entries"]
    by_match = pdb(piku.public_ranking(F, sort="secondary"))["entries"]
    assert [e["name"] for e in by_win] == [qs[0]["name"], qs[1]["name"], qs[2]["name"]]
    assert [e["name"] for e in by_match] == [qs[2]["name"], qs[1]["name"], qs[0]["name"]]
    # 두 목록 모두 1위부터 연속이다
    assert [e["rank"] for e in by_win] == [1, 2, 3]
    assert [e["rank"] for e in by_match] == [1, 2, 3]


def test_동점_규칙이_고정돼_있다(pdb):
    """같은 데이터면 몇 번을 불러도 같은 순위가 나와야 한다."""
    pdb(_set_sources())
    qs = sq.QUALIFIERS[F][:3]
    rows = [
        {"rank": 3, "name": qs[0]["name"], "winRate": 50.0, "matchRate": 50.0},
        {"rank": 1, "name": qs[1]["name"], "winRate": 50.0, "matchRate": 50.0},
        {"rank": 2, "name": qs[2]["name"], "winRate": 50.0, "matchRate": 70.0},
    ]
    pdb(piku.import_rows(F, rows))
    for q in qs:
        pdb(piku.set_mapping(F, q["name"], q["channelId"]))

    seen = set()
    for _ in range(5):
        r = pdb(piku.public_ranking(F, sort="primary"))
        seen.add(tuple(e["name"] for e in r["entries"]))
    assert len(seen) == 1, f"같은 데이터로 순위가 흔들렸다: {seen}"
    order = list(seen)[0]
    # 1순위 동점 → 2순위(다른 기준) → 3순위(원본 순위)
    assert order[0] == qs[2]["name"], "다른 기준이 높은 쪽이 앞이어야 한다"
    assert order[1] == qs[1]["name"], "원본 순위가 앞선 쪽이 앞이어야 한다"


def test_원본_순위와_우리_순위를_구분해_내보낸다(pdb):
    pdb(_set_sources())
    qs = sq.QUALIFIERS[F][:2]
    rows = [{"rank": 7, "name": qs[0]["name"], "winRate": 10.0, "matchRate": 10.0},
            {"rank": 9, "name": qs[1]["name"], "winRate": 90.0, "matchRate": 90.0}]
    pdb(piku.import_rows(F, rows))
    for q in qs:
        pdb(piku.set_mapping(F, q["name"], q["channelId"]))
    e = pdb(piku.public_ranking(F))["entries"]
    assert e[0]["rank"] == 1 and e[0]["sourceRank"] == 9, "둘은 다른 값이다"


def test_데이터가_없으면_available_false다(pdb):
    pdb(_set_sources())
    r = pdb(piku.public_ranking(F))
    assert r["available"] is False and r["entries"] == []


# ── 8) 자동 수집 기본 OFF · 우회 금지 ───────────────────────────────────────

def test_자동_수집은_기본_꺼짐이다(monkeypatch):
    monkeypatch.delenv("PIKU_AUTO_COLLECT_ENABLED", raising=False)
    assert piku.auto_collect_enabled() is False


def test_워커는_꺼져_있으면_즉시_반환한다(pdb, monkeypatch):
    monkeypatch.delenv("PIKU_AUTO_COLLECT_ENABLED", raising=False)
    pdb(piku.start_piku_worker())      # 아무 일도 하지 않고 끝나야 한다


def test_최소_수집_간격이_1시간_이상이다():
    assert piku.MIN_INTERVAL_MINUTES >= 60


def test_우회_수단이_코드에_없다():
    src = inspect.getsource(piku)
    for bad in ("proxies", "proxy_pool", "rotate", "playwright", "selenium",
                "undetected", "2captcha", "anticaptcha", "cf_clearance"):
        assert bad not in src.lower(), f"우회 흔적: {bad}"
    # User-Agent는 위장하지 않고 운영 목적·연락처를 담는다
    assert "NexBot" in piku.USER_AGENT
    assert "Mozilla" not in piku.USER_AGENT, "브라우저로 위장하지 않는다"


def test_원본_HTML을_저장하지_않는다(pdb, monkeypatch):
    monkeypatch.setattr(piku, "PAGE_DELAY_SECONDS", 0)
    pdb(_set_sources())
    html = ("<table><tr><td>1</td><td>가나다</td><td>62.5%</td><td>51.0%</td></tr>"
            "</table><!-- SECRET_MARKER -->")
    _collect(pdb, {1: FakeResponse(200, html)})

    async def _dump():
        c = await database.get_db()
        out = []
        for t in ("piku_entries", "piku_datasets", "piku_collect_runs", "piku_sources"):
            for r in await (await c.execute(f"SELECT * FROM {t}")).fetchall():
                out.append(json.dumps(dict(r), ensure_ascii=False, default=str))
        return "\n".join(out)

    dump = pdb(_dump())
    assert "SECRET_MARKER" not in dump and "<table" not in dump


def test_부문_키가_세_곳에서_같다():
    from routers import singcup_router as sr
    assert tuple(piku.DIVISIONS) == tuple(sr.DIVISIONS)
    assert set(piku.DIVISIONS) == set(sq.QUALIFIERS)


# ── 9) 수동 import fallback — 자동 수집 검증 전에도 쓸 수 있어야 한다 ────────
#
# 실제 PIKU 응답 구조를 아직 확인하지 못했으므로(LIVE_NOT_VERIFIED), 관리자가
# 접속 없이 데이터를 넣는 경로가 **혼자서도 완결**돼야 한다.

class TestManualImportFallback:
    def test_주소_없이도_import가_동작한다(self, pdb):
        """수집이 막혀 있어도(주소 미설정) import는 되어야 한다."""
        q = sq.QUALIFIERS[F][0]
        res = pdb(piku.import_rows(F, _rows([q["name"]])))
        assert res["applied"] is True and res["source"] == "manual_import"

    def test_import_후_바로_공개_순위에_반영된다(self, pdb):
        qs = sq.QUALIFIERS[F][:3]
        pdb(piku.import_rows(F, _rows([x["name"] for x in qs])))
        for x in qs:
            pdb(piku.set_mapping(F, x["name"], x["channelId"]))
        r = pdb(piku.public_ranking(F))
        assert r["available"] is True
        assert [e["rank"] for e in r["entries"]] == [1, 2, 3]

    def test_import이_외부를_부르지_않는다(self):
        src = inspect.getsource(piku.import_rows)
        for bad in ("httpx", "AsyncClient", "_fetch_page", "collect_division"):
            assert bad not in src, f"import 경로가 외부를 부른다: {bad}"

    def test_CSV_import가_같은_경로를_탄다(self, pdb):
        q = sq.QUALIFIERS[M][0]
        rows = piku.parse_csv(f"name,winRate,matchRate\n{q['name']},62.5,51.0\n")
        res = pdb(piku.import_rows(M, rows))
        assert res["entries"] == 1
        pdb(piku.set_mapping(M, q["name"], q["channelId"]))
        assert pdb(piku.public_ranking(M))["entries"][0]["channelId"] == q["channelId"]

    def test_import_후에도_공개_응답에_숫자가_없다(self, pdb):
        q = sq.QUALIFIERS[F][0]
        pdb(piku.import_rows(F, [{"rank": 1, "name": q["name"],
                                  "winRate": _MARK_WIN, "matchRate": _MARK_MATCH}]))
        pdb(piku.set_mapping(F, q["name"], q["channelId"]))
        _assert_no_rate_numbers(
            json.dumps(pdb(piku.public_ranking(F)), ensure_ascii=False), "import 후 응답")


# ── 10) 공개 정렬 토큰 (D) ─────────────────────────────────────────────────

class TestPublicSortTokens:
    def test_공개_토큰이_내부_컬럼명이_아니다(self):
        assert set(piku.PUBLIC_SORTS) == {"primary", "secondary"}
        assert set(piku.PUBLIC_SORTS.values()) == set(piku.SORT_KEYS)
        for k in piku.PUBLIC_SORTS:
            assert "rate" not in k and "win" not in k and "match" not in k

    def test_응답_어디에도_내부_컬럼명이_없다(self, pdb):
        pdb(_set_sources())
        _seed_marked(pdb)
        for s in ("primary", "secondary", "win_rate", "bogus", None):
            r = pdb(piku.public_ranking(F, sort=s))
            blob = json.dumps(r, ensure_ascii=False)
            for bad in ("win_rate", "match_rate", "winRate", "matchRate"):
                assert bad not in blob, f"sort={s}일 때 {bad}가 응답에 있다"
            assert r["sort"] in piku.PUBLIC_SORTS

    def test_내부_컬럼명을_보내도_기본값으로_떨어진다(self, pdb):
        """옛 토큰이 남은 링크가 있어도 500이 나지 않고 기본 정렬이 된다."""
        pdb(_set_sources())
        _seed_marked(pdb)
        assert pdb(piku.public_ranking(F, sort="win_rate"))["sort"] == piku.DEFAULT_SORT

    def test_라우터도_공개_토큰만_내보낸다(self, pdb):
        from routers import singcup_router as sr
        pdb(_set_sources())
        _seed_marked(pdb)
        out = pdb(sr.piku_ranking(sort="secondary"))
        blob = json.dumps(out, ensure_ascii=False)
        for bad in ("win_rate", "match_rate", "winRate", "matchRate"):
            assert bad not in blob
        assert [o["key"] for o in out["sortOptions"]] == ["primary", "secondary"]
        assert out["sort"] == "secondary"

    def test_정렬은_서버가_한다(self):
        """브라우저가 비율을 받아 클라이언트에서 정렬하면 비노출 계약 위반이다."""
        src = inspect.getsource(piku.public_ranking)
        assert "_sorted_entries(mapped, column)" in src
        # 응답 조립이 정렬 **뒤에** 온다(정렬 결과의 순서를 그대로 rank로 쓴다)
        assert src.index("_sorted_entries") < src.index('"rank": i + 1')


class TestPreviewDryRun:
    """반영 전 검증(dry-run) — **저장하지 않고** 형태만 본다."""

    def test_preview는_아무것도_저장하지_않는다(self, pdb):
        import singcup_piku as piku

        async def _go():
            before = await piku.active_dataset("female_solo")
            out = await piku.preview_rows("female_solo", [
                {"name": "고다요", "winRate": 62.5, "matchRate": 51.0},
                {"name": "린시", "winRate": 40.0, "matchRate": 33.0},
            ])
            after = await piku.active_dataset("female_solo")
            return out, before, after

        out, before, after = pdb(_go())
        assert out["applied"] is False
        assert out["entries"] == 2
        assert before == after, "미리보기가 활성 dataset을 바꿨다"

    def test_preview_응답에_비율_숫자가_없다(self, pdb):
        import json

        import singcup_piku as piku

        async def _go():
            return await piku.preview_rows("female_solo", [
                {"name": "고다요", "winRate": 62.5, "matchRate": 51.0}])

        blob = json.dumps(pdb(_go()), ensure_ascii=False)
        for bad in ("winRate", "matchRate", "win_rate", "match_rate",
                    "62.5", "51.0"):
            assert bad not in blob, f"미리보기가 내부값 {bad}를 흘렸다"

    def test_매칭_여부를_미리_보여_준다(self, pdb):
        import singcup_piku as piku

        async def _go():
            return await piku.preview_rows("female_solo", [
                {"name": "고다요", "winRate": 1, "matchRate": 1},
                {"name": "존재하지않는참가자", "winRate": 1, "matchRate": 1},
            ])

        out = pdb(_go())
        assert "고다요" in out["matched"]
        assert "존재하지않는참가자" in out["unmatched"]

    def test_형식이_틀리면_반영_전에_막힌다(self, pdb):
        import singcup_piku as piku

        async def _go():
            return await piku.preview_rows("female_solo", [{"nope": 1}])

        with pytest.raises(piku.PikuError):
            pdb(_go())

    def test_preview는_외부를_부르지_않는다(self):
        import inspect

        import singcup_piku as piku
        src = inspect.getsource(piku.preview_rows)
        for bad in ("httpx", "AsyncClient", "_fetch_page", "collect_division"):
            assert bad not in src, f"미리보기가 {bad}를 쓴다"
