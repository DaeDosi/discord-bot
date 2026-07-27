"""싱드컵 클립 수집·점수 계산 테스트 (외부 네트워크 없음)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import singcup_clips as sc

KST = timezone(timedelta(hours=9))
IN = "2026-07-28 12:00:00"
AT_START = "2026-07-20 00:00:00"
BEFORE = "2026-07-19 23:59:59"
AFTER = "2026-08-10 00:00:01"


def clip(uid="c1", owner="o1", created=IN, **kw):
    d = {"clipUID": uid, "videoId": f"v-{uid}", "recId": "{}", "ownerChannelId": owner,
         "clipTitle": "노래", "thumbnailImageUrl": "https://t/x.jpg",
         "categoryType": "ETC", "clipCategory": "music", "duration": 60,
         "adult": False, "createdDate": created, "blindType": None,
         "ownerChannel": {"channelId": owner, "channelName": "가수",
                          "channelImageUrl": "", "verifiedMark": False}}
    d.update(kw)
    return d


def card(desc="#싱드컵", likes=0, views=0, *, reactions=True, vod=True):
    inter = {"emotion": {"reactions": (
        [{"reactionType": "like", "count": likes}] if reactions else None)}}
    content = {"description": desc}
    if vod:
        content["vod"] = {"count": views}
    return {"card": {"content": content, "interaction": inter}}


def row(uid, owner, hearts, views, created_iso=IN):
    return {"clip_uid": uid, "owner_channel_id": owner, "heart_count": hearts,
            "view_count": views,
            "created_at": int(datetime.strptime(created_iso, "%Y-%m-%d %H:%M:%S")
                              .replace(tzinfo=KST).timestamp())}


# ── 4. #싱드컵 태그 판별 ────────────────────────────────────────────────────
@pytest.mark.parametrize("desc", [
    "#싱드컵", "#버튜버 #싱드컵", "#싱드컵 #노래", "  #싱드컵  ",
])
def test_tag_accepted(desc):
    assert sc.has_singcup_tag(desc)


@pytest.mark.parametrize("desc", [
    "", None, "싱드컵 나갑니다",          # 해시 없음
    "#싱드컵대회",                        # 더 긴 태그
    "#버튜버#싱드컵",                     # 공백 구분 없음
    "싱드컵 #노래",
])
def test_tag_rejected(desc):
    assert not sc.has_singcup_tag(desc)


def test_title_only_singcup_is_not_enough():
    """제목에만 '싱드컵'이 있고 태그가 없으면 제외한다."""
    assert not sc.has_singcup_tag("제목: 싱드컵 도전 #노래 #커버")


# ── 3. 이벤트 기간 필터 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("created,ok", [
    (BEFORE, False), (AT_START, True), (IN, True), (AFTER, False),
])
def test_event_window(created, ok):
    assert sc.is_candidate_clip(clip(created=created),
                                start=sc.START_AT, end=sc.END_AT) is ok


def test_event_start_is_july_20():
    assert sc.START_AT.isoformat() == "2026-07-20T00:00:00+09:00"


@pytest.mark.parametrize("kw", [
    {"categoryType": "GAME"}, {"clipCategory": "talk"}, {"adult": True},
    {"blindType": "BLIND"}, {"clipUID": ""}, {"ownerChannelId": ""},
    {"createdDate": "망가진 날짜"},
])
def test_non_candidates_excluded(kw):
    assert not sc.is_candidate_clip(clip(**kw), start=sc.START_AT, end=sc.END_AT)


# ── 5~6. 하트 / 조회수 추출 ─────────────────────────────────────────────────
def test_heart_and_view_extracted():
    c = card(likes=12, views=345)["card"]
    assert sc.extract_heart(c) == (12, True)
    assert sc.extract_view(c) == (345, True)


def test_missing_reactions_is_distinguished_from_zero():
    """API 오류로 못 읽은 것과 실제 0을 구분한다."""
    broken = card(reactions=False)["card"]
    assert sc.extract_heart(broken) == (0, False)      # 못 읽음
    ok = card(likes=0)["card"]
    assert sc.extract_heart(ok) == (0, True)           # 실제 0


def test_missing_vod_count_flagged():
    assert sc.extract_view(card(vod=False)["card"]) == (0, False)


def test_like_reaction_absent_counts_as_zero():
    c = {"interaction": {"emotion": {"reactions": [{"reactionType": "sad", "count": 3}]}}}
    assert sc.extract_heart(c) == (0, True)


@pytest.mark.parametrize("v,expected", [(None, 0), ("12", 12), (-5, 0), ("x", 0), (True, 0)])
def test_safe_count(v, expected):
    assert sc.safe_count(v) == expected


# ── 7~8. 대표 클립 선택 ─────────────────────────────────────────────────────
def test_representative_is_highest_heart():
    reps = sc.pick_representative([row("a", "o", 3, 100), row("b", "o", 9, 5)])
    assert reps["clip_uid"] == "b"


def test_representative_tie_on_heart_uses_views():
    reps = sc.pick_representative([row("a", "o", 5, 10), row("b", "o", 5, 99)])
    assert reps["clip_uid"] == "b"


def test_representative_tie_uses_created_then_uid():
    older = row("z", "o", 5, 10, "2026-07-28 01:00:00")
    newer = row("a", "o", 5, 10, "2026-07-28 09:00:00")
    assert sc.pick_representative([newer, older])["clip_uid"] == "z"   # 생성 시각 오름차순
    same = [row("b", "o", 5, 10), row("a", "o", 5, 10)]
    assert sc.pick_representative(same)["clip_uid"] == "a"             # clipUID 오름차순


def test_one_representative_per_streamer():
    reps = sc._build_reps([row("a", "o1", 1, 1), row("b", "o1", 9, 1), row("c", "o2", 4, 1)])
    assert len(reps) == 2
    by = {r["owner_channel_id"]: r for r in reps}
    assert by["o1"]["clip_uid"] == "b"
    assert by["o1"]["tagged_clip_count"] == 2          # 카드에 'N개' 표시용
    assert by["o2"]["tagged_clip_count"] == 1


# ── 9~15. 비공식 예상 인기점수 ──────────────────────────────────────────────
def test_score_is_70_view_plus_30_heart():
    reps = [row("a", "o1", 100, 1000), row("b", "o2", 50, 500)]
    ranked = sc.compute_scores(reps)
    top = ranked[0]
    assert top["view_score"] == 70.0 and top["heart_score"] == 30.0
    assert top["score"] == 100.0
    second = ranked[1]
    assert second["view_score"] == 35.0 and second["heart_score"] == 15.0
    assert second["score"] == 50.0


def test_score_uses_max_of_whole_pool():
    """파트 구분 없이 대표 클립 전체의 최댓값을 분모로 쓴다."""
    reps = sc.compute_scores([row("a", "o1", 10, 10), row("b", "o2", 5, 2)])
    b = next(r for r in reps if r["clip_uid"] == "b")
    assert b["view_score"] == round(2 / 10 * 70, 2)
    assert b["heart_score"] == round(5 / 10 * 30, 2)


def test_zero_max_does_not_divide_by_zero():
    reps = sc.compute_scores([row("a", "o1", 0, 0), row("b", "o2", 0, 0)])
    assert all(r["score"] == 0 for r in reps)


def test_score_within_0_100():
    import random
    reps = [row(f"c{i}", f"o{i}", random.randint(0, 500), random.randint(0, 9000))
            for i in range(40)]
    for r in sc.compute_scores(reps):
        assert 0 <= r["score"] <= 100


def test_rank_tiebreakers_are_deterministic():
    # 점수 동점 -> 하트 -> 조회수 -> 생성 시각 -> clipUID
    reps = sc.compute_scores([row("b", "o1", 5, 5), row("a", "o2", 5, 5)])
    assert [r["clip_uid"] for r in reps] == ["a", "b"]
    assert [r["rank"] for r in reps] == [1, 2]


# ── 17. 변화율 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cur,past,expected", [
    (110, 100, 10.0), (90, 100, -10.0), (5, 0, None), (5, -3, None),
])
def test_heart_change_rate(cur, past, expected):
    assert sc.heart_change_rate(cur, past) == expected


# ── 2. 커서 페이지네이션 ────────────────────────────────────────────────────
def _install(handler):
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_cursor_pagination_and_stop_before_event(db):
    seen_cursors = []

    def handler(request):
        if "categories" in str(request.url):
            cur = request.url.params.get("clipUID")
            seen_cursors.append(cur)
            assert int(request.url.params["size"]) == 50
            if cur is None:
                return httpx.Response(200, json={"code": 200, "content": {
                    "data": [clip("a1"), clip("a2")], "page": {"next": {"clipUID": "cur1"}}}})
            if cur == "cur1":
                return httpx.Response(200, json={"code": 200, "content": {
                    "data": [clip("b1", created=BEFORE)], "page": {"next": {"clipUID": "cur2"}}}})
            return httpx.Response(200, json={"code": 200, "content": {"data": [], "page": {}}})
        return httpx.Response(200, json=card("#싱드컵", 1, 1))

    _install(handler)
    res = db(sc.run_backfill())
    assert res["status"] == "completed"
    assert seen_cursors == [None, "cur1"]          # 커서로 이동, 기간 밖 페이지에서 종료
    assert res["tagged"] == 2                       # a1, a2 만


def test_repeated_cursor_is_detected(db):
    def handler(request):
        if "categories" in str(request.url):
            return httpx.Response(200, json={"code": 200, "content": {
                "data": [clip("same")], "page": {"next": {"clipUID": "stuck"}}}})
        return httpx.Response(200, json=card())

    _install(handler)
    res = db(sc.run_backfill())
    assert res["status"] == "failed" and "반복" in res["note"]


def test_untagged_clips_are_excluded(db):
    def handler(request):
        if "categories" in str(request.url):
            cur = request.url.params.get("clipUID")
            if cur is None:
                return httpx.Response(200, json={"code": 200, "content": {
                    "data": [clip("tagged"), clip("plain", owner="o2")], "page": {}}})
            return httpx.Response(200, json={"code": 200, "content": {"data": [], "page": {}}})
        uid = request.url.params.get("referer", "").rsplit("/", 1)[-1]
        desc = "#싱드컵" if uid == "tagged" else "#노래 #커버"
        return httpx.Response(200, json=card(desc, 1, 1))

    _install(handler)
    res = db(sc.run_backfill())
    assert res["tagged"] == 1
    assert db(sc.load_main())["summary"]["streamerCount"] == 1


# ── 24. 수집 실패가 기존 데이터를 비우지 않는다 ─────────────────────────────
def test_failure_keeps_existing_clips(db):
    def ok_handler(request):
        if "categories" in str(request.url):
            cur = request.url.params.get("clipUID")
            if cur is None:
                return httpx.Response(200, json={"code": 200, "content": {
                    "data": [clip("keep")], "page": {}}})
            return httpx.Response(200, json={"code": 200, "content": {"data": [], "page": {}}})
        return httpx.Response(200, json=card("#싱드컵", 5, 5))

    _install(ok_handler)
    first = db(sc.run_backfill())
    assert first["tagged"] == 1
    assert db(sc.load_main())["summary"]["streamerCount"] == 1

    # 완료된 백필은 다시 돌지 않으므로, 재적재 상황을 만들어 실패를 재현한다
    db(sc.reset_backfill())
    _install(lambda r: httpx.Response(500, json={"code": 500}))
    res = db(sc.run_backfill())
    assert res["status"] == "paused"        # 커서를 남기고 멈춘다(데이터는 지우지 않는다)

    async def count():
        import database
        c = await database.get_db()
        r = await (await c.execute(
            "SELECT COUNT(*) n FROM singcup_clips WHERE active=1")).fetchone()
        return r["n"]
    assert db(count()) == 1        # 기존 데이터 유지
