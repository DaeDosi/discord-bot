"""실제 네이버 게임 라운지 API를 호출하는 통합 테스트 — 기본은 skip.

    SINGCUP_LIVE_TESTS=1 pytest tests/integration -m integration

Railway 배포 환경에서 "이 IP로 네이버 라운지 API가 실제로 열리는가"를 확인하는
진단 용도이기도 하다. 401/403/429가 나오면 그 상태 그대로 실패 메시지에 드러난다.
"""
import os

import pytest
import singcup_collector as sc

pytestmark = pytest.mark.integration

live_only = pytest.mark.skipif(
    os.getenv("SINGCUP_LIVE_TESTS") != "1",
    reason="SINGCUP_LIVE_TESTS=1 일 때만 실제 API를 호출한다",
)


@live_only
def test_real_feed_page_shape(db):
    async def go():
        client = sc._get_client()
        return await sc.fetch_page(client, 0)

    feeds = db(go())
    assert isinstance(feeds, list) and len(feeds) > 0
    # 응답 스키마가 우리가 기대하는 모양인지(필드명이 바뀌지 않았는지) 확인
    parsed = [p for p in (sc.parse_feed_item(f) for f in feeds) if p]
    assert parsed, "한 건도 파싱되지 않았다면 응답 스키마가 바뀐 것이다"
    first = parsed[0]
    assert first["feed_id"] > 0
    assert first["author_id_hash"]
    assert first["created_dt"].tzinfo is not None


@live_only
def test_real_collect_and_rank(db):
    res = db(sc.collect_once())
    assert res["status"] == "OK", f"수집 실패: {res}"

    d = db(sc.load_rankings(limit=10))
    assert d["event"]["id"] == sc.EVENT_ID
    assert d["collector"]["lastSuccessAt"]
    # 이벤트 기간 중이면 참가작이 있어야 한다(시작 전/종료 직후에는 0일 수 있다)
    if d["event"]["status"] == "LIVE" and d["summary"]["submissionCount"] > 0:
        top = d["rankings"][0]
        assert top["rank"] == 1
        assert top["title"].startswith("[싱드컵]")
        # 작성자 중복 제거 결과가 참가작 수보다 많을 수는 없다
        assert d["summary"]["participantCount"] <= d["summary"]["submissionCount"]
