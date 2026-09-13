"""PUBLIC-UX-1 — 싱드컵 **시즌 → 단계(campaign)** 계층과 공개 상태 계약.

지키는 것:
1. campaign은 반드시 한 시즌에 속한다(시즌끼리 데이터가 섞일 경로가 없다).
2. 공개 단계 상태(`stageState`)는 **코드 정본**이고, 수집 쓰기 게이트(`status`)와 별개다.
3. `/piku/campaigns` 응답은 기존 `campaigns` 형태를 유지하고 `seasons`를 **추가**한다.
4. 공개하지 않는 시즌은 공개 응답에서 통째로 빠지지만 관리 화면은 계속 본다.
5. "약 1시간마다"는 **실제 수집 모드가 자동일 때만**, 공개 반영(검토 후)과 구분해 알린다.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

import singcup_piku as piku  # noqa: E402
import singcup_piku_campaigns as camps  # noqa: E402
import singcup_piku_scheduler as sched  # noqa: E402


def test_모든_campaign이_등록된_시즌에_속한다():
    for key, c in camps.CAMPAIGNS.items():
        assert c["season"] in camps.SEASONS, key
        assert c["stage"] in camps.STAGES, key
        assert c["stageState"] in camps.STAGE_STATES, key


def test_현재_시즌_이름과_단계_상태():
    s = camps.SEASONS[camps.SEASON_2026_GALAXY]
    assert s["label"] == "2026 싱드컵 갤럭시"
    assert camps.CAMPAIGNS["final"]["stageState"] == "in_progress"
    assert camps.CAMPAIGNS["qualifier"]["stageState"] == "ended"
    # 쓰기 게이트는 그대로다 — 상태 모델을 더했다고 예선이 풀리면 안 된다.
    assert camps.CAMPAIGNS["qualifier"]["status"] == "frozen"
    assert camps.CAMPAIGNS["final"]["status"] == "active"
    assert camps.SOURCES["final"]["sourceId"] == "2ut8Li"


def test_시즌_목록은_자기_campaign만_본선_먼저_담는다():
    seasons = camps.public_seasons()
    assert [s["season"] for s in seasons] == [camps.SEASON_2026_GALAXY]
    assert seasons[0]["campaigns"] == ["final", "qualifier"]
    assert seasons[0]["label"] == "2026 싱드컵 갤럭시"


def test_새_시즌을_더해도_기존_시즌과_섞이지_않는다(monkeypatch):
    monkeypatch.setitem(camps.SEASONS, "2027-next",
                        {"label": "2027 다음 시즌", "public": True, "order": 202701})
    monkeypatch.setitem(camps.CAMPAIGNS, "next_final", {
        **camps.CAMPAIGNS["final"], "season": "2027-next", "sources": []})
    seasons = {s["season"]: s for s in camps.public_seasons()}
    assert list(seasons)[0] == "2027-next", "최근 시즌이 먼저"
    assert seasons["2027-next"]["campaigns"] == ["next_final"]
    assert "next_final" not in seasons[camps.SEASON_2026_GALAXY]["campaigns"]


def test_기존_season_정보가_없는_행도_campaign으로_시즌이_정해진다():
    # DB 행은 campaign 컬럼만 갖는다(기본 qualifier). 시즌은 거기서 결정된다.
    assert camps.season_of("qualifier") == camps.SEASON_2026_GALAXY
    with pytest.raises(camps.CampaignError):
        camps.season_of("nope")


def test_campaign_status가_시즌과_상태를_싣는다(db):
    async def go():
        return {c["campaign"]: c for c in await piku.campaign_status()}
    cs = db(go())
    assert cs["final"]["season"] == camps.SEASON_2026_GALAXY
    assert cs["final"]["stage"] == "final" and cs["final"]["stageState"] == "in_progress"
    assert cs["qualifier"]["stageState"] == "ended"
    # 기존 필드는 그대로 있다(구 화면 호환).
    for k in ("status", "sources", "collectionStartAt", "lastPublishedAt", "available"):
        assert k in cs["final"]


def _status_with_mode(db, mode):
    import singcup_piku_devices as devices

    async def go():
        await devices.set_mode(mode)
        return {c["campaign"]: c for c in await piku.campaign_status()}
    return db(go())


def _upd(c):
    return (c["collectionMode"], c["collectionIntervalMinutes"], c["publicUpdatePolicy"])


def test_MANUAL이면_수집_주기를_약속하지_않는다(db):
    cs = _status_with_mode(db, "MANUAL")
    assert _upd(cs["final"]) == ("manual", 0, "reviewed")
    assert _upd(cs["qualifier"]) == ("none", 0, "none"), "종료·동결 단계는 수집하지 않는다"


def test_AUTO_COLLECT면_60분_확인이지만_공개는_검토_후다(db):
    cs = _status_with_mode(db, "AUTO_COLLECT")
    assert _upd(cs["final"]) == ("auto", sched.PERIOD_MINUTES, "reviewed")
    assert sched.PERIOD_MINUTES == 60
    assert _upd(cs["qualifier"]) == ("none", 0, "none")


def test_AUTO_PUBLISH_모드여도_준비_전이면_검토_후_공개다(db):
    assert sched.AUTO_PUBLISH_READY is False
    cs = _status_with_mode(db, "AUTO_PUBLISH")
    assert _upd(cs["final"]) == ("auto", 60, "reviewed")


def test_자동_공개_여부는_수집_모드와_독립이다(db, monkeypatch):
    async def yes():
        return True
    monkeypatch.setattr(sched, "publish_allowed", yes)
    cs = _status_with_mode(db, "MANUAL")
    assert _upd(cs["final"]) == ("manual", 0, "automatic")


def test_옛_갱신_필드는_남기지_않는다(db):
    cs = _status_with_mode(db, "MANUAL")
    assert "publicRefreshMinutes" not in cs["final"], "자동 공개 간격처럼 읽히는 이름"


def test_확장_주기_상수를_한_곳에서_쓴다():
    import inspect
    src = inspect.getsource(sched)
    assert '"periodMinutes": PERIOD_MINUTES' in src
    assert '"periodMinutes": 60' not in src


def test_비공개_시즌은_공개_응답에서_빠지고_관리는_본다(db, monkeypatch):
    monkeypatch.setitem(camps.SEASONS[camps.SEASON_2026_GALAXY], "public", False)

    async def both():
        return (await piku.campaign_status(public_only=True),
                await piku.campaign_status())
    pub, admin = db(both())
    assert pub == [] and len(admin) == 2
    assert camps.public_seasons() == []


@pytest.fixture
def client(db):
    import routers.singcup_router as sr
    app = FastAPI()
    app.include_router(sr.router)
    return TestClient(app)


def test_공개_라우트는_campaigns를_유지하고_seasons를_더한다(client):
    r = client.get("/api/singcup/piku/campaigns")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"campaigns", "seasons"}
    assert [c["campaign"] for c in body["campaigns"]] == ["qualifier", "final"]
    assert body["seasons"][0]["label"] == "2026 싱드컵 갤럭시"
    # 공개 응답에 비밀·내부 이름이 없다.
    raw = r.text
    for bad in ("win_rate", "match_rate", "token", "fingerprint", "grant"):
        assert bad not in raw
