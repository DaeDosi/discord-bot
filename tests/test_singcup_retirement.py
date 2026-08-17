"""싱드컵 기능 종료 상태 (SINGCUP-3).

지키려는 계약:

1. **무엇을 멈췄는지 구분된다** — 화면만 숨긴 것이 아니라 API와 워커 게이트가
   실제로 닫힌다. 셋을 각각 확인한다.
2. **날짜 게이트와 기능 종료 게이트를 섞지 않는다.** SINGCUP-1이 나눠 둔 네 축은
   그대로 두고, 기능 종료는 별도 축이다.
3. **데이터는 지우지 않는다** — 확정본 기록 경로는 계속 살아 있고, 종료 응답이
   그 경로를 알려 준다.
4. **공식 예선 참가자에게만 LIVE를 노출한다** — 서버가 강제한다(명단 밖 채널이
   구조적으로 들어올 수 없다).
5. 백엔드 참가자 명단이 프론트 정본과 어긋나지 않는다.
"""
import json
import re
from pathlib import Path

import pytest
import singcup_collector as sc
import singcup_qualifiers as sq
from routers import singcup_router as sr

import database

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_rising(db):
    """`rising_*`는 공용 conftest의 정리 목록에 없다.

    거기에 줄을 더하면 같은 자리를 쓰는 다른 대기 중인 작업과 부딪히므로
    **이 모듈 안에서만** 비운다. 비우지 않으면 앞 테스트가 심은 라이브 스냅샷이
    뒤 테스트로 새어 "수집 데이터가 없을 때"를 검증할 수 없다.
    """
    async def _clear():
        c = await database.get_db()
        for t in ("rising_live_snapshots", "rising_collect_runs"):
            try:
                await c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await c.commit()
    db(_clear())
    return db


# ── 1) 기능 종료 게이트 ─────────────────────────────────────────────────────

def test_기본값에서_세_기능이_모두_닫혀_있다(monkeypatch):
    for v in ("SINGCUP_APPLICATIONS_ENABLED", "SINGCUP_UNOFFICIAL_RANKING_ENABLED",
              "SINGCUP_LIVE_FEATURE_ENABLED"):
        monkeypatch.delenv(v, raising=False)
    assert sc.applications_open() is False
    assert sc.unofficial_ranking_open() is False
    assert sc.live_feature_open() is False


def test_환경변수로_되살릴_수_있다(monkeypatch):
    """코드를 되돌리지 않고 되살릴 수 있어야 한다."""
    monkeypatch.setenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", "true")
    assert sc.unofficial_ranking_open() is True
    monkeypatch.setenv("SINGCUP_LIVE_FEATURE_ENABLED", "1")
    assert sc.live_feature_open() is True


def test_신청은_기능_플래그와_기간을_모두_만족해야_열린다(monkeypatch):
    """종료된 이벤트에 신청을 받으면 명단이 소급 변경된다."""
    monkeypatch.setenv("SINGCUP_APPLICATIONS_ENABLED", "true")
    # conftest가 END_AT을 2026-08-09로 고정해 두었다 → 지금은 ENDED
    assert sc.event_status() == "ENDED"
    assert sc.applications_open() is False


def test_비공식_랭킹이_닫히면_자동_갱신도_멈춘다(monkeypatch):
    """소비처가 없는데 순위·스냅샷을 계속 계산할 이유가 없다."""
    monkeypatch.delenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", raising=False)
    assert sc.ranking_refresh_open() is False
    assert sc.snapshot_refresh_open() is False


def test_비공식_랭킹을_되살리면_갱신도_함께_돌아온다(monkeypatch):
    monkeypatch.setenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", "true")
    monkeypatch.delenv("SINGCUP_RANKING_REFRESH_ENABLED", raising=False)
    monkeypatch.delenv("SINGCUP_SNAPSHOT_REFRESH_ENABLED", raising=False)
    assert sc.ranking_refresh_open() is True
    assert sc.snapshot_refresh_open() is True


def test_LIVE와_랭킹은_서로_다른_축이다(monkeypatch):
    """한쪽만 되살릴 수 있어야 한다."""
    monkeypatch.setenv("SINGCUP_LIVE_FEATURE_ENABLED", "true")
    monkeypatch.delenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", raising=False)
    assert sc.live_feature_open() is True
    assert sc.unofficial_ranking_open() is False


def test_SINGCUP1의_네_축을_합치지_않았다():
    """`event_status()` 하나로 되돌리면 '종료됐지만 지표는 갱신'이 표현되지 않는다."""
    src = (ROOT / "web" / "backend" / "singcup_collector.py").read_text(encoding="utf-8")
    for fn in ("def registration_open", "def metrics_refresh_open",
               "def ranking_refresh_open", "def snapshot_refresh_open"):
        assert fn in src, f"{fn}가 사라졌다"
    # 지표 갱신은 기능 종료와 무관하게 유지된다(스윕을 건드리지 말라는 요구)
    assert "def metrics_refresh_open" in src
    body = src.split("def metrics_refresh_open")[1].split("\ndef ")[0]
    assert "unofficial_ranking_open" not in body, \
        "지표 갱신까지 기능 종료에 묶으면 스윕이 함께 멈춘다"


# ── 2) API가 실제로 닫힌다 ──────────────────────────────────────────────────

def _is_retired(resp) -> bool:
    body = json.loads(bytes(resp.body).decode("utf-8"))
    return body.get("retired") is True


def test_비공식_랭킹_API가_종료_응답을_준다(db, monkeypatch):
    monkeypatch.delenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", raising=False)
    resp = db(sr.rankings(limit=10))
    assert _is_retired(resp)
    body = json.loads(bytes(resp.body).decode("utf-8"))
    # 오류가 아니라 상태다 — 프런트가 catch로 받으면 잘못된 문구가 나간다
    assert resp.status_code == 200
    assert body["archiveUrl"] == "/api/singcup/final-ranking"
    assert body["streamers"] == [], "형태를 깨뜨리지 않는다"


def test_LIVE_API가_별도_게이트로_닫힌다(db, monkeypatch):
    monkeypatch.delenv("SINGCUP_LIVE_FEATURE_ENABLED", raising=False)
    resp = db(sr.split_live())
    body = json.loads(bytes(resp.body).decode("utf-8"))
    assert body["retired"] is True and body["feature"] == "singcup_live"


def test_상태_API가_게이트와_문구를_알려_준다(db, monkeypatch):
    for v in ("SINGCUP_APPLICATIONS_ENABLED", "SINGCUP_UNOFFICIAL_RANKING_ENABLED",
              "SINGCUP_LIVE_FEATURE_ENABLED"):
        monkeypatch.delenv(v, raising=False)
    s = db(sr.status())
    assert s["gates"] == {"applicationsOpen": False,
                          "unofficialRankingOpen": False,
                          "liveFeatureOpen": False}
    assert s["notices"]["applications"] == "싱드컵 신청이 종료되었습니다."
    assert s["notices"]["unofficialRanking"]
    assert s["notices"]["live"]
    assert s["archiveUrl"] == "/api/singcup/final-ranking"


def test_되살리면_문구가_사라진다(db, monkeypatch):
    monkeypatch.setenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", "true")
    s = db(sr.status())
    assert s["gates"]["unofficialRankingOpen"] is True
    assert s["notices"]["unofficialRanking"] is None


def test_확정본_기록_경로는_닫지_않았다():
    """데이터를 지운 것이 아니라 기능을 내린 것이다."""
    src = (ROOT / "web" / "backend" / "routers" / "singcup_router.py").read_text(
        encoding="utf-8")
    final = src.split('@router.get("/final-ranking")')[1].split("@router.")[0]
    assert "_unofficial_retired_or_none" not in final, "확정본까지 막으면 기록이 사라진다"


def test_삭제_경로를_추가하지_않았다():
    src = (ROOT / "web" / "backend" / "singcup_collector.py").read_text(encoding="utf-8")
    added = src.split("기능 종료 게이트")[1]
    for bad in ("DROP TABLE", "DELETE FROM singcup_", "TRUNCATE"):
        assert bad not in added, f"기능 종료가 데이터를 지운다: {bad}"


# ── 3) 공식 예선 참가자 · LIVE 노출 ─────────────────────────────────────────

def test_백엔드_명단이_프론트_정본과_같다():
    """두 파일이 어긋나면 LIVE 노출 판정이 조용히 틀어진다."""
    ts = (ROOT / "web" / "frontend" / "lib" / "singcupQualifiers.ts").read_text(
        encoding="utf-8")
    ts_ids = set(re.findall(r'channelId:\s*"([0-9a-f]{32})"', ts))
    assert ts_ids, "정본에서 채널 id를 찾지 못했다"
    assert set(sq.ALL_CHANNEL_IDS) == ts_ids, "백엔드 사본이 정본과 어긋났다"
    assert len(sq.QUALIFIERS["female_solo"]) == 64
    assert len(sq.QUALIFIERS["male_solo"]) == 64
    assert len(sq.QUALIFIERS["groups"]) == 32


def test_부문_키가_세_곳에서_같다():
    import singcup_piku as piku
    assert set(sq.QUALIFIERS) == set(sr.DIVISIONS) == set(piku.DIVISIONS)


def test_참가자_API는_명단_밖_채널을_돌려주지_않는다(db):
    """LIVE 노출 정책이 **구조적으로** 지켜지는지 — 필터가 아니라 출발점이 명단이다."""
    async def _seed_outsider():
        c = await database.get_db()
        now = 1_800_000_000
        await c.execute(
            "INSERT OR REPLACE INTO rising_collect_runs (collected_at, ok)"
            " VALUES (?,1)", (now,))
        for cid in ("f" * 32, sq.QUALIFIERS["female_solo"][0]["channelId"]):
            await c.execute(
                "INSERT OR REPLACE INTO rising_live_snapshots (chzzk_channel_id,"
                " collected_at, channel_name, concurrent_viewers, category_name,"
                " open_date, follower_count, live_title, adult, tags)"
                " VALUES (?,?,'x',5,'게임','',0,'',0,'')", (cid, now))
        await c.commit()

    db(_seed_outsider())
    res = db(sr.qualifiers())
    ids = set()
    for rows in res["divisions"].values():
        for r in rows:
            if "members" in r:
                ids.update(m["channelId"] for m in r["members"])
            else:
                ids.add(r["channelId"])
    assert "f" * 32 not in ids, "명단 밖 채널이 응답에 들어왔다"
    assert ids <= set(sq.ALL_CHANNEL_IDS)


def test_참가자_API가_부문을_나눠_준다(db):
    res = db(sr.qualifiers())
    assert set(res["divisions"]) == set(sr.DIVISIONS)
    assert res["counts"]["female_solo"] == 64
    assert res["counts"]["male_solo"] == 64
    assert res["counts"]["groups"] == 32
    assert res["divisionLabels"]["female_solo"] == "여성 솔로"
    assert res["source"] == "CHZZK_OFFICIAL_ANNOUNCEMENT"


def test_부문_하나만도_받을_수_있다(db):
    res = db(sr.qualifiers(division="groups"))
    assert set(res["divisions"]) == {"groups"}
    assert res["divisions"]["groups"][0]["members"]


def test_참가자_API는_기능_종료_게이트와_무관하다(db, monkeypatch):
    """공식 명단은 대회의 확정 산출물이라 기능 종료와 함께 감출 대상이 아니다."""
    for v in ("SINGCUP_UNOFFICIAL_RANKING_ENABLED", "SINGCUP_LIVE_FEATURE_ENABLED"):
        monkeypatch.delenv(v, raising=False)
    res = db(sr.qualifiers())
    assert res["counts"]["female_solo"] == 64


def test_수집_데이터가_없어도_명단은_나온다(db):
    """라이브·클립은 부가 값이다 — 없으면 배지만 빠지고 명단은 그대로다."""
    res = db(sr.qualifiers(division="female_solo"))
    rows = res["divisions"]["female_solo"]
    assert len(rows) == 64
    assert all(r["live"] is None for r in rows)
    assert all("clipThumbnailUrl" in r for r in rows)
