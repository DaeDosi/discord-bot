"""실제 치지직 API를 호출하는 통합 테스트 — 기본은 skip.

    CHZZK_LIVE_TESTS=1 pytest tests/integration -m integration

비공식 엔드포인트라 네트워크/차단/스키마 변경에 영향을 받으므로 CI에 넣지 않는다.
운영자가 "지금도 이 엔드포인트가 살아 있나"를 확인할 때 수동으로 돌린다.
Railway가 아닌 곳(해외 IP 등)에서는 실패할 수 있다.
"""
import os

import chzzk_channel_history as ch
import pytest

pytestmark = pytest.mark.integration

CID = "4b8f70248caa6f086ceec07aad69a5cc"

live_only = pytest.mark.skipif(
    os.getenv("CHZZK_LIVE_TESTS") != "1",
    reason="CHZZK_LIVE_TESTS=1 일 때만 실제 API를 호출한다",
)


@live_only
def test_real_channel_first_live_date(db):
    res = db(ch.get_channel_history(CID, refresh=True))

    assert res["status"] == ch.ST_OK
    assert res["channelId"] == CID
    # 첫 방송일은 과거 사실이라 변하지 않는다 — 고정값으로 검증할 수 있다.
    assert res["firstLiveDate"] == "2025-01-14 22:19:58"
    assert res["firstLiveDateIso"] == "2025-01-14T22:19:58+09:00"
    # totalLiveHours 는 방송할수록 늘어나므로 고정값으로 검증하지 않는다.
    assert res["totalLiveHours"] is None or res["totalLiveHours"] >= 4
    assert res["channelName"]


@live_only
def test_real_channel_second_call_is_cached(db):
    db(ch.get_channel_history(CID, refresh=True))
    res = db(ch.get_channel_history(CID))
    assert res["cached"] is True
    assert res["firstLiveDate"] == "2025-01-14 22:19:58"
