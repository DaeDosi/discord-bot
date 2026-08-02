"""`topHeartMovers1hEvaluatedAt` — 후보 계산을 마지막으로 실제 실행한 시각.

`ComputedAt`과 다르다. stale fallback일 때 `ComputedAt`은 **옛 집계 자신의 시각**이라
멈춰 있지만 이 값은 계속 전진한다. "계산이 멈춘 것"과 "계산했지만 조건을 만족한 후보가
없는 것"을 구분하는 유일한 근거다.
"""

import json
import time

import pytest
import singcup_clips as sc


# ── 응답 계약 ──────────────────────────────────────────────────────────────
def test_fresh_response_has_evaluated_at(db):
    d = db(sc.load_main())
    assert "topHeartMovers1hEvaluatedAt" in d
    assert d["topHeartMovers1hEvaluatedAt"], "값이 비어 있다"
    assert d["topHeartMovers1hEvaluatedAt"].endswith("+09:00")


def test_evaluated_at_is_close_to_now(db):
    before = int(time.time())
    d = db(sc.load_main())
    after = int(time.time())
    from datetime import datetime
    ts = int(datetime.fromisoformat(d["topHeartMovers1hEvaluatedAt"]).timestamp())
    assert before - 2 <= ts <= after + 2, "이번 계산의 now가 아니다"


def test_existing_fields_are_preserved(db):
    d = db(sc.load_main())
    for k in ("topHeartMovers1h", "topHeartMovers1hStale",
              "topHeartMovers1hBaseAt", "topHeartMovers1hComputedAt"):
        assert k in d, k


# ── 캐시 계약: hit마다 바뀌면 안 된다 ──────────────────────────────────────
def test_cache_hit_keeps_the_same_evaluated_at(db, monkeypatch):
    """같은 캐시 항목을 다시 주면서 현재 시각으로 바꾸면 안 된다 —
    그러면 '요청 시각'이 되어 계산 생존 판정에 쓸 수 없다."""
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60)
    sc.invalidate_main_cache()
    first = db(sc.load_main())["topHeartMovers1hEvaluatedAt"]
    time.sleep(1.1)
    second = db(sc.load_main())["topHeartMovers1hEvaluatedAt"]
    assert first == second, "캐시 hit인데 evaluatedAt이 바뀌었다"


def test_invalidate_then_recompute_advances_evaluated_at(db, monkeypatch):
    monkeypatch.setattr(sc, "MAIN_CACHE_TTL", 60)
    sc.invalidate_main_cache()
    first = db(sc.load_main())["topHeartMovers1hEvaluatedAt"]
    time.sleep(1.1)
    sc.invalidate_main_cache()
    second = db(sc.load_main())["topHeartMovers1hEvaluatedAt"]
    assert second > first, "무효화 후 새 계산인데 값이 그대로다"


# ── ETag 계약 ─────────────────────────────────────────────────────────────
def test_etag_reflects_evaluated_at():
    """**지문에 포함한다.** 빼면 서버가 다시 계산해 evaluatedAt이 바뀌어도 ETag가
    같아 304가 나가고, 클라이언트는 옛 '마지막 재확인' 시각을 계속 보여준다 —
    사용자에게 거짓 시각이 된다."""
    base = {"streamers": [{"a": 1}], "topHeartMovers1h": [],
            "topHeartMovers1hEvaluatedAt": "2026-08-02T21:00:00+09:00"}
    other = {**base, "topHeartMovers1hEvaluatedAt": "2026-08-02T23:59:00+09:00"}
    assert sc._build_main_entry(base)["etag"] != sc._build_main_entry(other)["etag"]


def test_etag_still_ignores_computed_at():
    """기존 계약은 그대로 둔다."""
    base = {"streamers": [{"a": 1}],
            "topHeartMovers1hComputedAt": "2026-08-02T21:00:00+09:00"}
    other = {**base, "topHeartMovers1hComputedAt": "2026-08-02T23:00:00+09:00"}
    assert sc._build_main_entry(base)["etag"] == sc._build_main_entry(other)["etag"]


def test_fingerprint_already_varies_with_now_via_delta_baseline():
    """evaluatedAt을 포함해도 200/304 비율이 나빠지지 않는 근거.

    지문에는 이미 `deltaBaseline.intervalSecondsMin/Max`(= now - base[...])가 있어
    uncached 재계산마다 지문이 어차피 달라진다. 이 성질이 사라지면 위 판단의
    전제가 무너지므로 테스트로 고정한다."""
    def entry(now):
        return sc._build_main_entry({
            "summary": {"deltaBaseline": {"intervalSecondsMin": now - 1000,
                                          "intervalSecondsMax": now - 2000}},
            "streamers": [{"a": 1}]})
    assert entry(1_785_686_000)["etag"] != entry(1_785_686_120)["etag"]


def test_etag_stable_within_one_cache_entry():
    """같은 캐시 항목(같은 now)이면 ETag도 같다 — TTL 창 안의 304 이득은 유지된다."""
    d = {"summary": {"deltaBaseline": {"intervalSecondsMin": 10}},
         "streamers": [{"a": 1}], "topHeartMovers1hEvaluatedAt": "T"}
    assert sc._build_main_entry(d)["etag"] == sc._build_main_entry(dict(d))["etag"]


def test_etag_changes_when_content_changes():
    a = {"streamers": [{"a": 1}], "topHeartMovers1hEvaluatedAt": "x"}
    b = {"streamers": [{"a": 2}], "topHeartMovers1hEvaluatedAt": "x"}
    assert sc._build_main_entry(a)["etag"] != sc._build_main_entry(b)["etag"]


# ── stale fallback 계약 ────────────────────────────────────────────────────
def test_stale_fallback_keeps_stored_meta_but_advances_evaluated_at(db, monkeypatch):
    """후보 0명 + 저장된 fallback이 있으면:
    - stale=true
    - payload / BaseAt / ComputedAt 은 **저장된 옛 값 그대로**
    - EvaluatedAt 만 이번 계산 시각으로 전진
    """
    stored = [{"rank": 1, "channelId": "c1", "channelName": "n", "channelImageUrl": "",
               "clipUid": "u1", "clipTitle": "t", "clipThumbnailUrl": "",
               "heartCount": 10, "heartDelta1h": 5, "score": 1.0, "live": None}]
    OLD_BASE, OLD_COMPUTED = 1_785_672_000, 1_785_675_600     # 21:00 / 22:00 KST

    async def fake_last():
        return stored, OLD_BASE, OLD_COMPUTED

    monkeypatch.setattr(sc, "_last_top_movers", fake_last)
    d = db(sc.load_main())

    assert d["topHeartMovers1hStale"] is True
    assert d["topHeartMovers1h"] == stored, "fallback payload가 변형됐다"
    assert d["topHeartMovers1hBaseAt"].startswith("2026-08-02T21:00")
    assert d["topHeartMovers1hComputedAt"].startswith("2026-08-02T22:00")

    from datetime import datetime
    ev = int(datetime.fromisoformat(d["topHeartMovers1hEvaluatedAt"]).timestamp())
    assert ev > OLD_COMPUTED, "stale인데 EvaluatedAt이 전진하지 않았다"
    assert abs(ev - int(time.time())) <= 3


def test_stale_fallback_does_not_write_to_db(db, monkeypatch):
    """조회 경로는 읽기 전용이다 — fallback을 반환해도 저장하지 않는다."""
    import sqlite3

    import database

    async def fake_last():
        return [], None, None

    monkeypatch.setattr(sc, "_last_top_movers", fake_last)
    con = sqlite3.connect(database.DB_PATH)
    try:
        before = con.execute("SELECT COUNT(*) FROM singcup_top_movers").fetchone()[0]
    finally:
        con.close()
    db(sc.load_main())
    con = sqlite3.connect(database.DB_PATH)
    try:
        after = con.execute("SELECT COUNT(*) FROM singcup_top_movers").fetchone()[0]
    finally:
        con.close()
    assert before == after


# ── 후보 수 계약: 억지로 채우지 않는다 ────────────────────────────────────
@pytest.mark.parametrize("n,expected", [(1, 1), (2, 2), (5, 5), (8, 5), (0, 0)])
def test_movers_count_is_never_padded(n, expected):
    """양수 후보가 n명이면 min(n, 5)명만 나온다. 0으로 채우지 않는다."""
    movers = [(10 - i, {"channel_id": f"c{i}", "clip_uid": f"u{i}",
                        "heart_count": 100 - i, "score": 1.0}) for i in range(n)]
    movers.sort(key=lambda t: (-t[0], -int(t[1]["heart_count"]),
                               -float(t[1]["score"]), str(t[1]["channel_id"])))
    out = movers[:5]
    assert len(out) == expected
    assert [d for d, _ in out] == sorted([d for d, _ in out], reverse=True)


def test_evaluated_at_is_json_serializable(db):
    d = db(sc.load_main())
    json.dumps({"topHeartMovers1hEvaluatedAt": d["topHeartMovers1hEvaluatedAt"]})


# ── topHeartMovers1hPositiveCount ──────────────────────────────────────────
# **이번 평가의 양수 owner 수.** 화면 카드 수와 다르다 — fallback 중이면 카드는
# 옛 결과이고 이 값은 0이다. 상위 5개로 자르기 전 값이다.
def test_positive_count_present_and_int(db):
    d = db(sc.load_main())
    assert "topHeartMovers1hPositiveCount" in d
    assert isinstance(d["topHeartMovers1hPositiveCount"], int)
    assert d["topHeartMovers1hPositiveCount"] >= 0


def test_positive_count_is_zero_while_fallback_shows_old_cards(db, monkeypatch):
    """현재 사례 그대로: 카드 2명(옛 결과) + 현재 양수 0명."""
    stored = [{"rank": i + 1, "channelId": f"c{i}", "channelName": "n",
               "channelImageUrl": "", "clipUid": f"u{i}", "clipTitle": "t",
               "clipThumbnailUrl": "", "heartCount": 10, "heartDelta1h": 5,
               "score": 1.0, "live": None} for i in range(2)]

    async def fake_last():
        return stored, 1_785_672_000, 1_785_675_600

    monkeypatch.setattr(sc, "_last_top_movers", fake_last)
    d = db(sc.load_main())
    assert d["topHeartMovers1hStale"] is True
    assert len(d["topHeartMovers1h"]) == 2, "카드는 옛 결과 2건"
    assert d["topHeartMovers1hPositiveCount"] == 0, "현재 양수는 0명이어야 한다"


def test_positive_count_zero_without_fallback(db, monkeypatch):
    async def fake_last():
        return [], None, None

    monkeypatch.setattr(sc, "_last_top_movers", fake_last)
    d = db(sc.load_main())
    assert d["topHeartMovers1h"] == []
    assert d["topHeartMovers1hPositiveCount"] == 0
    assert d["topHeartMovers1hStale"] is False, "보여줄 옛 결과가 없으면 stale이 아니다"


def test_positive_count_counts_before_top5_slice():
    """6명 이상이면 카드는 5개지만 PositiveCount는 실제 수다."""
    movers = [(10 - i, {"channel_id": f"c{i}", "heart_count": 100 - i, "score": 1.0})
              for i in range(8)]
    assert len(movers[:5]) == 5
    assert len(movers) == 8, "PositiveCount는 자르기 전 길이(len(movers))를 쓴다"


def test_positive_count_in_etag_fingerprint():
    """값이 바뀌면 기능 데이터가 바뀐 것이므로 ETag에 반영돼야 한다."""
    a = {"streamers": [{"x": 1}], "topHeartMovers1hPositiveCount": 0}
    b = {"streamers": [{"x": 1}], "topHeartMovers1hPositiveCount": 3}
    assert sc._build_main_entry(a)["etag"] != sc._build_main_entry(b)["etag"]


def test_positive_count_is_not_personal_data(db):
    d = db(sc.load_main())
    v = d["topHeartMovers1hPositiveCount"]
    assert isinstance(v, int) and not isinstance(v, bool)
