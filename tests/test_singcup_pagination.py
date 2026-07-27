"""싱드컵 — 페이지 순회 / 이벤트 시작일(2026-07-27 20:00) / backfill·dry-run·prune 테스트.

응답 생성 헬퍼는 test_singcup.py 것을 그대로 쓴다(같은 mock 규약을 두 벌 두지 않기 위해).
"""
import httpx
import singcup_collector as sc
from test_singcup import AT_START, BEFORE_EVENT, IN_EVENT, feed_item, install, page

import database


# ── 1~6. 웹 페이지 번호 ↔ API offset 대응 ───────────────────────────────────
def test_web_page_maps_to_api_offset(db):
    """웹 1/2/3페이지 = API offset 0/1/2. 30씩 더하는 방식이 아니다."""
    seen = []

    def handler(request):
        off = int(request.url.params["offset"])
        seen.append(off)
        if off <= 2:                       # 웹 1~3페이지 분량
            base = off * 30 + 1
            return httpx.Response(200, json=page(
                [feed_item(i) for i in range(base, base + 30)]))
        return httpx.Response(200, json=page(
            [feed_item(900 + i, created=BEFORE_EVENT) for i in range(30)]))

    install(handler)
    res = db(sc.collect_once())

    assert seen == [0, 1, 2, 3]            # 0,1,2,3 — 0,30,60 이 아니다
    assert 30 not in seen and 60 not in seen
    assert res["matched"] == 90            # 3페이지 x 30건 전부 수집
    assert res["full_scan"] is True


def test_limit_never_exceeds_30(db):
    limits = []

    def handler(request):
        limits.append(int(request.url.params["limit"]))
        return httpx.Response(200, json=page([feed_item(1, created=BEFORE_EVENT)]))

    install(handler)
    db(sc.collect_once())
    assert limits and all(v <= 30 for v in limits)
    assert sc.PAGE_LIMIT == 30


# ── 7~11. 여러 페이지 / 경계 / 혼재 페이지 ──────────────────────────────────
def test_collects_singcup_across_multiple_pages(db):
    def handler(request):
        off = int(request.url.params["offset"])
        if off == 0:
            return httpx.Response(200, json=page([
                feed_item(1, user_hash="a", buff=5),
                feed_item(2, title="일반 잡담글", user_hash="b"),
            ]))
        if off == 1:
            return httpx.Response(200, json=page([
                feed_item(3, user_hash="c", buff=7),
                feed_item(4, user_hash="d", buff=1),
            ]))
        return httpx.Response(200, json=page([feed_item(9, created=BEFORE_EVENT)]))

    install(handler)
    res = db(sc.collect_once())
    assert res["pages"] == 3 and res["matched"] == 3     # 말머리 없는 2번은 제외
    d = db(sc.load_rankings())
    assert [e["feedId"] for e in d["rankings"]] == [3, 1, 4]


def test_start_boundary_included_and_one_second_before_excluded(db):
    def handler(request):
        off = int(request.url.params["offset"])
        if off == 0:
            return httpx.Response(200, json=page([
                feed_item(1, created=AT_START, user_hash="a"),        # 07-27 20:00:00 포함
                feed_item(2, created=BEFORE_EVENT, user_hash="b"),   # 07-27 19:59:59 제외
            ]))
        return httpx.Response(200, json=page([feed_item(9, created=BEFORE_EVENT)]))

    install(handler)
    res = db(sc.collect_once())
    assert res["matched"] == 1
    assert [e["feedId"] for e in db(sc.load_rankings())["rankings"]] == [1]


def test_mixed_page_processes_new_and_skips_old_then_stops_next_page(db):
    """한 페이지에 이벤트 전후가 섞이면 이후 글만 저장하고, 다음 페이지가 전부
    시작일 이전일 때 종료한다(오래된 글 한 건 발견으로 즉시 끊지 않는다)."""
    seen = []

    def handler(request):
        off = int(request.url.params["offset"])
        seen.append(off)
        if off == 0:
            return httpx.Response(200, json=page([
                feed_item(1, created=IN_EVENT, user_hash="a"),
                feed_item(2, created=BEFORE_EVENT, user_hash="b"),   # 섞여 있음
            ]))
        if off == 1:
            return httpx.Response(200, json=page([
                feed_item(3, created=AT_START, user_hash="c"),       # 아직 남아 있다
                feed_item(4, created=BEFORE_EVENT, user_hash="d"),
            ]))
        return httpx.Response(200, json=page([
            feed_item(5, created=BEFORE_EVENT), feed_item(6, created=BEFORE_EVENT)]))

    install(handler)
    res = db(sc.collect_once())
    assert seen == [0, 1, 2]        # 혼재 페이지에서 끊지 않고 다음 페이지까지 확인
    assert res["matched"] == 2      # 1, 3 만 저장
    assert res["full_scan"] is True


def test_stops_on_empty_page(db):
    seen = []

    def handler(request):
        off = int(request.url.params["offset"])
        seen.append(off)
        if off == 0:
            return httpx.Response(200, json=page([feed_item(1)]))
        return httpx.Response(200, json=page([]))       # 빈 페이지

    install(handler)
    res = db(sc.collect_once())
    assert seen == [0, 1] and res["full_scan"] is True


# ── 17~18. 제목/기간 교차 조건 ──────────────────────────────────────────────
def test_singcup_title_before_start_is_excluded(db):
    def handler(request):
        off = int(request.url.params["offset"])
        if off == 0:
            # 제목은 [싱드컵]이지만 07-27 20시 이전 → 제외돼야 한다
            return httpx.Response(200, json=page([feed_item(1, created=BEFORE_EVENT)]))
        return httpx.Response(200, json=page([]))

    install(handler)
    res = db(sc.collect_once())
    assert res["matched"] == 0
    assert db(sc.load_rankings())["rankings"] == []


def test_after_start_but_no_prefix_is_excluded(db):
    def handler(request):
        off = int(request.url.params["offset"])
        if off == 0:
            return httpx.Response(200, json=page([
                feed_item(1, title="싱드컵 나갑니다", created=IN_EVENT),
                feed_item(2, title="[싱드컵] 참가", created=IN_EVENT, user_hash="b"),
            ]))
        return httpx.Response(200, json=page([]))

    install(handler)
    res = db(sc.collect_once())
    assert res["matched"] == 1
    assert [e["feedId"] for e in db(sc.load_rankings())["rankings"]] == [2]


# ── 15. backfill 재실행 idempotency ─────────────────────────────────────────
def _two_page_handler(buff1=5):
    def handler(request):
        off = int(request.url.params["offset"])
        if off == 0:
            return httpx.Response(200, json=page([
                feed_item(1, user_hash="a", buff=buff1, views=10)]))
        if off == 1:
            return httpx.Response(200, json=page([
                feed_item(2, user_hash="b", buff=2, views=5, created=AT_START)]))
        return httpx.Response(200, json=page([feed_item(9, created=BEFORE_EVENT)]))
    return handler


def test_backfill_is_idempotent_and_updates_counts(db):
    install(_two_page_handler(buff1=5))
    first = db(sc.collect_once(mode="backfill"))
    assert first["matched"] == 2 and first["inserted"] == 2

    # 같은 backfill을 다시 돌려도 중복 insert가 생기지 않는다
    install(_two_page_handler(buff1=9))
    second = db(sc.collect_once(mode="backfill"))
    assert second["matched"] == 2 and second["inserted"] == 0

    d = db(sc.load_rankings())
    assert len(d["rankings"]) == 2
    assert d["rankings"][0]["feedId"] == 1 and d["rankings"][0]["buffCount"] == 9  # 갱신됨


def test_backfill_uses_deeper_page_cap():
    assert sc.BACKFILL_MAX_PAGES >= sc.MAX_PAGES


# ── dry-run ─────────────────────────────────────────────────────────────────
def test_dry_run_writes_nothing(db):
    install(_two_page_handler())
    res = db(sc.collect_once(mode="dry-run"))
    assert res["mode"] == "dry-run" and res["matched"] == 2 and res["inserted"] == 0
    # DB에는 아무것도 남지 않는다(수집 이력도 남기지 않음)
    d = db(sc.load_rankings())
    assert d["rankings"] == []
    assert d["collector"]["lastAttemptAt"] is None


# ── 기간 밖 데이터 정리(prune) ──────────────────────────────────────────────
def test_prune_dry_run_then_apply(db):
    install(_two_page_handler())
    db(sc.collect_once())
    assert len(db(sc.load_rankings())["rankings"]) == 2

    # 한 건을 이벤트 시작 이전으로 조작(예전 기준으로 저장돼 있던 상황 재현)
    async def move_out():
        conn = await database.get_db()
        await conn.execute("UPDATE singcup_feeds SET created_at=? WHERE feed_id=1",
                           (int(sc.START_AT.timestamp()) - 86400,))
        await conn.commit()
    db(move_out())

    dry = db(sc.prune_out_of_range(dry_run=True))
    assert dry["dryRun"] is True and dry["count"] == 1
    assert dry["targets"][0]["feedId"] == 1
    # dry-run은 아무것도 바꾸지 않는다
    assert len(db(sc.load_rankings())["rankings"]) == 2

    applied = db(sc.prune_out_of_range(dry_run=False))
    assert applied["count"] == 1
    remaining = db(sc.load_rankings())["rankings"]
    assert [e["feedId"] for e in remaining] == [2]     # 삭제가 아니라 비활성


def test_event_window_default_is_july_27_20h():
    assert sc.START_AT.isoformat() == "2026-07-27T20:00:00+09:00"
    assert sc.END_AT.isoformat() == "2026-08-09T23:59:59+09:00"
