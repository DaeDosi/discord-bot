"""싱드컵 수집기 — mock 테스트(외부 네트워크 없음).

httpx.MockTransport로 네이버 라운지 응답을 흉내 낸다. 실제 API를 때리는 테스트는
tests/integration/test_singcup_live.py 에 따로 있다.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import singcup_collector as sc

KST = timezone(timedelta(hours=9))
IN_EVENT = "20260728120000"          # 이벤트 기간 안
BEFORE_EVENT = "20260719235959"      # 시작 1초 전 (2026-07-19 23:59:59)
AT_START = "20260720000000"          # 시작 정각 (2026-07-20 00:00:00)
AT_END = "20260809235959"            # 종료 정각
AFTER_EVENT = "20260810000000"       # 종료 1초 후

CLIP = "https://chzzk.naver.com/clips/oYiRnFkvNj"


def contents(*urls: str) -> str:
    """실제 응답과 같은 모양의 본문 JSON(중첩 components + textNode.link.url)."""
    nodes = []
    for u in urls:
        nodes.append({"id": "n1", "value": u, "link": {"url": u}, "@ctype": "textNode"})
    return json.dumps({"document": {"components": [
        {"id": "c1", "value": [{"id": "p1", "nodes": nodes, "@ctype": "paragraph"}]},
    ]}, "documentId": "d1"})


def feed_item(feed_id=1, *, title="[싱드컵] 노래", created=IN_EVENT, buff=0, nerf=0,
              views=0, comments=0, user_hash="u1", nickname="가수", board_id=4,
              clean_bot=False, body=None):
    return {
        "feedId": feed_id,
        "feed": {
            "feedId": feed_id, "title": title, "createdDate": created,
            "updatedDate": created, "contents": contents(*(body or [CLIP])),
            "loungeId": "chzzk", "originalLoungeId": "chzzk",
            "hideByCleanBot": clean_bot, "pinned": False,
        },
        "user": {"userIdHash": user_hash, "nickname": nickname,
                 "profileImageUrl": "https://img/p.png", "verifiedMark": False},
        "buff": {"buffCount": buff, "nerfCount": nerf},
        "readCount": views,
        "comment": {"totalCount": comments},
        "board": {"boardId": board_id, "boardName": "자유"},
        "feedLink": {"pc": f"https://game.naver.com/lounge/chzzk/board/detail/{feed_id}",
                     "mobile": f"https://m.game.naver.com/lounge/chzzk/board/detail/{feed_id}"},
    }


def page(items):
    return {"code": 200, "content": {"offset": 0, "count": len(items),
                                     "totalCount": 40000, "feeds": items}}


def install(handler):
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return sc._client


# ── 2~4. 제목 판별 / entity 디코딩 ──────────────────────────────────────────
@pytest.mark.parametrize("title", [
    "[싱드컵] 노래 불렀습니다",
    "  [싱드컵] 앞 공백",
    "[ 싱드컵 ] 대괄호 안 공백",
])
def test_singcup_title_accepted(title):
    assert sc.is_singcup_title(title)


@pytest.mark.parametrize("title", [
    "싱드컵 참가합니다",              # 말머리 없음
    "노래 [싱드컵] 뒤에 붙음",         # 맨 앞이 아님
    "[싱드컵대회] 다른 말머리",
    "[싱드 컵] 사이 공백",
    "", None,
])
def test_non_singcup_title_rejected(title):
    assert not sc.is_singcup_title(title)


def test_html_entity_title_decoded():
    raw = "[싱드컵] Dance Monkey &#x1f952;&amp;&#x1f435;"
    assert sc.normalize_title(raw) == "[싱드컵] Dance Monkey 🥒&🐵"
    assert sc.is_singcup_title(raw)
    # entity로 감싸인 말머리도 디코딩 후 인정된다
    assert sc.is_singcup_title("&#91;싱드컵&#93; 노래")


# ── 5. KST 이벤트 기간 경계값 ───────────────────────────────────────────────
@pytest.mark.parametrize("created,expected", [
    (BEFORE_EVENT, False),
    (AT_START, True),
    (IN_EVENT, True),
    (AT_END, True),
    (AFTER_EVENT, False),
])
def test_event_window_boundaries(created, expected):
    parsed = sc.parse_feed_item(feed_item(created=created))
    assert parsed is not None
    assert sc.is_event_entry(parsed, start=sc.START_AT, end=sc.END_AT) is expected


def test_created_date_parsed_as_kst():
    d = sc.parse_created_date("20260727211949")
    assert d == datetime(2026, 7, 27, 21, 19, 49, tzinfo=KST)


@pytest.mark.parametrize("bad", ["", None, "2026-07-27", "2026072721194", "abcdefghijklmn"])
def test_bad_created_date_returns_none(bad):
    assert sc.parse_created_date(bad) is None


def test_bad_created_date_skips_only_that_feed():
    assert sc.parse_feed_item(feed_item(created="깨진값")) is None
    assert sc.parse_feed_item(feed_item(feed_id=2)) is not None


def test_clean_bot_and_other_board_excluded():
    p = sc.parse_feed_item(feed_item(clean_bot=True))
    assert not sc.is_event_entry(p, start=sc.START_AT, end=sc.END_AT)
    p2 = sc.parse_feed_item(feed_item(board_id=7))
    assert not sc.is_event_entry(p2, start=sc.START_AT, end=sc.END_AT)


# ── 8~9. 클립 URL 추출 ──────────────────────────────────────────────────────
def test_clip_url_extracted_and_deduped():
    body = contents(CLIP, CLIP, "https://chzzk.naver.com/clips/AAA_bbb-1")
    urls = sc.extract_clip_urls(body)
    assert urls == [CLIP, "https://chzzk.naver.com/clips/AAA_bbb-1"]


def test_clip_url_found_in_nested_oglink():
    nested = json.dumps({"document": {"components": [
        {"value": [{"nodes": [{"@ctype": "oglink", "link": CLIP}]}]},
    ]}})
    assert sc.extract_clip_urls(nested) == [CLIP]


def test_broken_contents_json_keeps_feed():
    broken = '{"document": {"components": [' + CLIP
    # 파싱은 실패해도 원문에서 URL을 건지고, 게시글 자체는 살아 있어야 한다
    assert sc.extract_clip_urls(broken) == [CLIP]
    item = feed_item()
    item["feed"]["contents"] = "완전히 깨진 문자열"
    parsed = sc.parse_feed_item(item)
    assert parsed is not None and parsed["clip_url"] is None


def test_no_clip_url_is_none():
    item = feed_item()
    item["feed"]["contents"] = contents()
    parsed = sc.parse_feed_item(item)
    assert parsed["clip_url"] is None and parsed["clip_urls"] == ""


# ── 14. null/문자열/음수 숫자 처리 ──────────────────────────────────────────
def test_safe_int_handles_null_string_negative():
    assert sc.safe_int(None) == 0
    assert sc.safe_int("12") == 12
    assert sc.safe_int(-5) == 0
    assert sc.safe_int("abc") == 0
    assert sc.safe_int(True) == 0


def test_null_buff_and_views_become_zero():
    item = feed_item()
    item["buff"] = {"buffCount": None, "nerfCount": None}
    item["readCount"] = None
    item["comment"] = {}
    p = sc.parse_feed_item(item)
    assert p["buff_count"] == 0 and p["view_count"] == 0 and p["comment_count"] == 0


def test_missing_user_hash_uses_feed_scoped_key():
    item = feed_item(feed_id=77)
    item["user"] = {"nickname": "익명"}
    p = sc.parse_feed_item(item)
    assert p["author_id_hash"] == "feed:77"


# ── 스키마 검증 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("payload", [
    None, [], {"code": 500, "content": {}}, {"code": 200},
    {"code": 200, "content": []}, {"code": 200, "content": {"feeds": "nope"}},
])
def test_broken_schema_raises(payload):
    with pytest.raises(sc.SchemaError):
        sc.parse_feed_page(payload)


# ── 11~13. 작성자 중복 제거 및 동률 규칙 ────────────────────────────────────
def row(feed_id, author, buff, views, created):
    return {"feed_id": feed_id, "author_id_hash": author, "author_nickname": author,
            "buff_count": buff, "view_count": views, "created_at": created}


def test_same_author_keeps_highest_buff():
    ranked = sc.rank_entries([row(1, "a", 3, 10, 100), row(2, "a", 9, 5, 200)])
    assert len(ranked) == 1 and ranked[0]["feed_id"] == 2


def test_tie_on_buff_uses_view_count():
    ranked = sc.rank_entries([row(1, "a", 5, 10, 100), row(2, "b", 5, 99, 100)])
    assert [r["feed_id"] for r in ranked] == [2, 1]


def test_tie_on_buff_and_views_uses_created_then_feed_id():
    # 같은 작성자가 버프·조회수가 같은 글을 둘 올린 실제 사례
    ranked = sc.rank_entries([row(9, "a", 5, 10, 500), row(4, "a", 5, 10, 500)])
    assert len(ranked) == 1 and ranked[0]["feed_id"] == 4      # feedId 오름차순
    ranked2 = sc.rank_entries([row(9, "a", 5, 10, 100), row(4, "a", 5, 10, 500)])
    assert ranked2[0]["feed_id"] == 9                          # 작성 시각 오름차순 우선


def test_ranks_are_sequential_and_unique():
    ranked = sc.rank_entries([row(1, "a", 5, 1, 1), row(2, "b", 5, 1, 1), row(3, "c", 9, 1, 1)])
    assert [r["rank"] for r in ranked] == [1, 2, 3]
    assert ranked[0]["feed_id"] == 3


# ── 6~7, 10. 페이지 순회 ────────────────────────────────────────────────────
def test_pagination_uses_page_numbers_and_limit_30(db):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        off = int(request.url.params["offset"])
        if off == 0:
            return httpx.Response(200, json=page([feed_item(i) for i in range(1, 31)]))
        if off == 1:
            return httpx.Response(200, json=page([feed_item(i) for i in range(31, 61)]))
        # 3번째 페이지는 전부 이벤트 시작 이전 → 순회 종료
        return httpx.Response(200, json=page(
            [feed_item(i, created=BEFORE_EVENT) for i in range(61, 91)]))

    install(handler)
    res = db(sc.collect_once())

    assert res["status"] == "OK" and res["full_scan"] is True
    # offset은 0,1,2 (30씩 더하지 않는다), limit은 항상 30
    assert [int(p["offset"]) for p in seen] == [0, 1, 2]
    assert all(int(p["limit"]) == 30 for p in seen)
    assert all(p["order"] == "NEW" and p["buffFilteringYN"] == "N" for p in seen)
    assert res["matched"] == 60


def test_duplicate_feed_ids_deduped_and_loop_guarded(db):
    calls = []

    def handler(request):
        calls.append(1)
        # 항상 같은 페이지를 준다 → 무한 루프. 가드가 잡아야 한다.
        return httpx.Response(200, json=page([feed_item(1), feed_item(2)]))

    install(handler)
    res = db(sc.collect_once())
    assert res["status"] == "FAILED"
    assert "반복" in res["note"]
    assert len(calls) < sc.MAX_PAGES        # MAX_PAGES까지 가지 않고 조기 중단
    assert res["feeds_seen"] == 2           # feedId 기준 중복 제거


# ── 15~17. HTTP 예외 처리 ───────────────────────────────────────────────────
def test_400_is_not_retried(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"code": 400})

    install(handler)
    res = db(sc.collect_once())
    assert res["status"] == "SCHEMA_ERROR"
    assert len(calls) == 1                  # 무한 재시도 방지


@pytest.mark.parametrize("code,expected", [(401, "BLOCKED"), (403, "BLOCKED"),
                                           (404, "SCHEMA_ERROR")])
def test_auth_and_notfound_not_retried(db, code, expected):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(code, json={"code": code})

    install(handler)
    res = db(sc.collect_once())
    assert res["status"] == expected and len(calls) == 1


def test_429_honors_retry_after(db):
    import time
    calls = []

    def handler(request):
        calls.append(time.monotonic())
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "0.2"}, json={"code": 429})
        return httpx.Response(200, json=page([feed_item(1, created=BEFORE_EVENT)]))

    install(handler)
    t0 = time.monotonic()
    res = db(sc.collect_once())
    assert res["status"] == "OK"
    assert len(calls) == 3
    assert time.monotonic() - t0 >= 0.4     # Retry-After 0.2초 x 2회를 실제로 기다렸다


def test_5xx_and_timeout_retry_then_fail(db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"code": 503})

    install(handler)
    res = db(sc.collect_once())
    assert res["status"] == "FAILED"
    assert len(calls) == sc.MAX_RETRIES


def test_timeout_retries(db):
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ReadTimeout("timeout", request=request)

    install(handler)
    res = db(sc.collect_once())
    assert res["status"] == "FAILED" and len(calls) == sc.MAX_RETRIES


# ── 18~19. 실패해도 기존 데이터 유지 ────────────────────────────────────────
def _seed_ok(db):
    def handler(request):
        off = int(request.url.params["offset"])
        if off == 0:
            return httpx.Response(200, json=page([
                feed_item(1, user_hash="a", nickname="가", buff=5, views=10),
                feed_item(2, user_hash="b", nickname="나", buff=3, views=20),
            ]))
        return httpx.Response(200, json=page([feed_item(99, created=BEFORE_EVENT)]))

    install(handler)
    return db(sc.collect_once())


def test_schema_break_keeps_existing_rows(db):
    assert _seed_ok(db)["matched"] == 2
    before = db(sc.load_rankings())
    assert len(before["rankings"]) == 2

    # 이후 수집에서 응답 구조가 깨짐 → 실패로 처리하고 DB는 그대로여야 한다
    install(lambda r: httpx.Response(200, json={"code": 200, "content": {"feeds": "?"}}))
    res = db(sc.collect_once())
    assert res["status"] == "SCHEMA_ERROR"

    after = db(sc.load_rankings())
    assert len(after["rankings"]) == 2
    assert after["summary"]["totalBuffCount"] == before["summary"]["totalBuffCount"]
    assert after["collector"]["status"] == "SCHEMA_ERROR"   # 상태는 실패로 알린다


def test_network_failure_does_not_empty_rankings(db):
    _seed_ok(db)
    install(lambda r: httpx.Response(500, json={"code": 500}))
    db(sc.collect_once())
    after = db(sc.load_rankings())
    assert len(after["rankings"]) == 2


def test_missing_feed_needs_two_full_scans_to_deactivate(db):
    _seed_ok(db)

    def only_one(request):
        off = int(request.url.params["offset"])
        if off == 0:
            return httpx.Response(200, json=page([
                feed_item(1, user_hash="a", nickname="가", buff=5, views=10)]))
        return httpx.Response(200, json=page([feed_item(99, created=BEFORE_EVENT)]))

    install(only_one)
    db(sc.collect_once())
    # 1회 누락으로는 사라지지 않는다(원본 API의 일시적 누락 방어)
    assert len(db(sc.load_rankings())["rankings"]) == 2
    install(only_one)
    db(sc.collect_once())
    assert len(db(sc.load_rankings())["rankings"]) == 1


# ── 20. 순위 API 응답 구조 ──────────────────────────────────────────────────
def test_rankings_payload_shape(db):
    _seed_ok(db)
    d = db(sc.load_rankings())

    assert set(d) == {"event", "summary", "collector", "rankings"}
    assert set(d["event"]) >= {"id", "name", "startAt", "endAt", "status"}
    assert d["event"]["status"] in ("UPCOMING", "LIVE", "ENDED")
    assert set(d["summary"]) >= {"submissionCount", "participantCount", "totalBuffCount"}
    assert set(d["collector"]) >= {"lastSuccessAt", "lastAttemptAt", "status", "stale"}
    top = d["rankings"][0]
    assert set(top) >= {"rank", "feedId", "authorIdHash", "authorNickname",
                        "authorProfileImageUrl", "title", "buffCount", "nerfCount",
                        "viewCount", "commentCount", "createdAt", "clipUrl", "postUrl"}
    assert top["rank"] == 1 and top["buffCount"] == 5
    assert top["clipUrl"] == CLIP
    assert d["summary"]["participantCount"] == 2


def test_dedupe_by_author_hash_not_nickname(db):
    def handler(request):
        off = int(request.url.params["offset"])
        if off == 0:
            # 닉네임은 같지만 다른 사람 → 합치면 안 된다
            return httpx.Response(200, json=page([
                feed_item(1, user_hash="a", nickname="같은닉", buff=5),
                feed_item(2, user_hash="b", nickname="같은닉", buff=4),
                feed_item(3, user_hash="a", nickname="같은닉", buff=9),   # 같은 사람의 2번째 글
            ]))
        return httpx.Response(200, json=page([feed_item(99, created=BEFORE_EVENT)]))

    install(handler)
    db(sc.collect_once())
    d = db(sc.load_rankings())
    assert d["summary"]["submissionCount"] == 3
    assert d["summary"]["participantCount"] == 2         # userIdHash 기준 2명
    assert d["rankings"][0]["feedId"] == 3               # a의 대표작은 버프 9짜리


def test_lock_prevents_concurrent_runs(db):
    async def both():
        import asyncio
        install(lambda r: httpx.Response(200, json=page([feed_item(1, created=BEFORE_EVENT)])))
        return await asyncio.gather(sc.collect_once(), sc.collect_once())

    a, b = db(both())
    assert {a["status"], b["status"]} == {"OK", "SKIPPED"}


# ── 22. 외부 링크 보안 속성(정적 검사) ──────────────────────────────────────
def test_external_links_have_noopener_noreferrer():
    """JS 테스트 러너가 없으므로 소스에서 target=_blank 사용처를 정적으로 검사한다."""
    src = (Path(__file__).resolve().parents[1]
           / "web" / "frontend" / "app" / "stats" / "Singcup.tsx").read_text(encoding="utf-8")
    blanks = src.count('target="_blank"')
    assert blanks >= 2, "클립/원문 외부 링크가 있어야 한다"
    assert src.count('rel="noopener noreferrer"') == blanks
