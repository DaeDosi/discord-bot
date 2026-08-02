"""owner별 기준선 비교 간격 분포(관리자 진단) — 선택 규칙 공유와 집계 결정성.

가장 중요한 계약: **진단이 고르는 baseline 행은 `_delta_maps()`가 고르는 것과
완전히 같아야 한다.** 진단 쪽에 비슷한 구현을 하나 더 두면 화면과 진단이 조용히
갈라지고, 그러면 진단을 믿을 수 없다. 그래서 둘 다 `select_baseline_rows()`를 쓰고
여기서 그 사실을 고정한다.
"""

import json
import time

import pytest
import singcup_clips as sc


# ── 순수 집계: percentile 정의 ──────────────────────────────────────────────
# nearest-rank: rank = ceil(q*n), 1-indexed. 보간 없음 → 반환값은 항상 실제 표본.
@pytest.mark.parametrize("vals,q,expected", [
    ([10], 0.5, 10),
    ([10], 0.95, 10),
    ([10, 20], 0.5, 10),                  # 짝수 — 보간이면 15가 나왔을 것
    ([10, 20], 0.9, 20),
    ([10, 20, 30], 0.5, 20),              # 홀수
    ([10, 20, 30, 40], 0.5, 20),          # ceil(0.5*4)=2 → 두 번째
    ([10, 20, 30, 40], 0.95, 40),
    ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5, 5),
    ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9, 9),
    ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95, 10),
    ([5, 5, 5, 5], 0.5, 5),               # 동일 값 다수
])
def test_percentile_is_nearest_rank(vals, q, expected):
    assert sc._percentile(sorted(vals), q) == expected


def test_percentile_empty_is_none():
    assert sc._percentile([], 0.5) is None


def test_percentile_always_returns_a_real_sample():
    vals = sorted([3, 7, 11, 19, 23])
    for q in (0.0, 0.1, 0.5, 0.9, 0.95, 1.0):
        v = sc._percentile(vals, q)
        assert v in vals, f"q={q} 에서 표본에 없는 값 {v}"


# ── 요약 ────────────────────────────────────────────────────────────────────
def test_summary_empty():
    s = sc._interval_summary([])
    assert s["owners"] == 0
    assert all(s[k] is None for k in
               ("minSeconds", "p50Seconds", "p90Seconds", "p95Seconds",
                "maxSeconds", "averageSeconds"))


def test_summary_single_owner():
    s = sc._interval_summary([1234])
    assert s == {"owners": 1, "minSeconds": 1234, "p50Seconds": 1234,
                 "p90Seconds": 1234, "p95Seconds": 1234, "maxSeconds": 1234,
                 "averageSeconds": 1234.0}


def test_summary_is_deterministic_regardless_of_input_order():
    vals = [3600, 152, 2483, 900, 900]
    a = sc._interval_summary(vals)
    b = sc._interval_summary(list(reversed(vals)))
    assert a == b


# ── 히스토그램: 반개구간, 경계값 ────────────────────────────────────────────
def test_histogram_boundaries_are_half_open():
    """정확히 경계에 있는 값이 두 칸에 잡히거나 어디에도 안 잡히면 안 된다."""
    edges = [0, 600, 1200, 1800, 2400, 3000, 3600, 4500, 5400]
    h = sc._interval_histogram(edges)
    assert sum(b["owners"] for b in h) == len(edges)
    for i, e in enumerate(edges):
        # 각 경계값은 자기 구간의 시작에 들어간다
        assert h[i]["fromSeconds"] == e
        assert h[i]["owners"] == 1, (e, h[i])


def test_histogram_last_bucket_is_open_ended():
    h = sc._interval_histogram([5400, 9999, 100000])
    assert h[-1]["toSeconds"] is None
    assert h[-1]["owners"] == 3


def test_histogram_total_matches_sample_count():
    vals = [0, 1, 599, 600, 601, 3599, 3600, 5399, 5400, 5401]
    h = sc._interval_histogram(vals)
    assert sum(b["owners"] for b in h) == len(vals)


def test_histogram_empty():
    h = sc._interval_histogram([])
    assert sum(b["owners"] for b in h) == 0
    assert len(h) == len(sc._INTERVAL_BUCKETS)


# ── 선택 규칙: _delta_maps 와 동일한 helper ────────────────────────────────
class Row(dict):
    """sqlite3.Row 대역 — 인덱싱만 쓰므로 dict로 충분하다."""


def _row(owner, at, rid, uid="c", heart=0):
    return Row(owner_channel_id=owner, collected_at=at, id=rid,
               clip_uid=uid, heart_count=heart)


def test_selector_picks_closest_to_ref():
    ref = 1000
    rows = [_row("a", 500, 1), _row("a", 990, 2), _row("a", 1500, 3)]
    picked = sc.select_baseline_rows(rows, ref)
    assert picked["a"][1]["id"] == 2       # |990-1000|=10 이 가장 가깝다


def test_selector_tiebreak_prefers_more_recent_collected_at():
    """거리가 같으면 더 최근 값(② 규칙)."""
    ref = 1000
    rows = [_row("a", 900, 1), _row("a", 1100, 2)]   # 둘 다 거리 100
    picked = sc.select_baseline_rows(rows, ref)
    assert picked["a"][1]["collected_at"] == 1100


def test_selector_tiebreak_prefers_larger_id():
    """거리도 시각도 같으면 더 큰 id(③ 규칙) — 나중에 쓰인 행."""
    ref = 1000
    rows = [_row("a", 1000, 5), _row("a", 1000, 9), _row("a", 1000, 7)]
    picked = sc.select_baseline_rows(rows, ref)
    assert picked["a"][1]["id"] == 9


def test_selector_three_level_tiebreak_is_order_independent():
    ref = 1000
    rows = [_row("a", 900, 1), _row("a", 1100, 2), _row("a", 1100, 8),
            _row("a", 1100, 5)]
    a = sc.select_baseline_rows(rows, ref)["a"][1]["id"]
    b = sc.select_baseline_rows(list(reversed(rows)), ref)["a"][1]["id"]
    assert a == b == 8


def test_selector_handles_zero_owners():
    assert sc.select_baseline_rows([], 1000) == {}


def test_selector_keeps_owners_separate():
    ref = 1000
    rows = [_row("a", 900, 1), _row("b", 1500, 2), _row("b", 1010, 3)]
    picked = sc.select_baseline_rows(rows, ref)
    assert set(picked) == {"a", "b"}
    assert picked["b"][1]["id"] == 3


def test_selector_matches_delta_maps_source():
    """`_delta_maps()`가 이 helper를 쓰는지 소스로 고정한다 — 별도 구현이 다시
    생기면 여기서 깨진다."""
    import inspect
    src = inspect.getsource(sc._delta_maps)
    assert "select_baseline_rows(" in src
    # 예전의 인라인 구현이 되살아나지 않았는지
    assert "abs(at - ref)" not in src, "_delta_maps 안에 선택 규칙이 다시 생겼다"


# ── 집계가 선택 결과와 일치하는지 ──────────────────────────────────────────
def test_summary_matches_selected_rows():
    now, ref = 10_000, 6_400
    rows = [_row("a", 6_000, 1), _row("a", 6_390, 2),   # a → 6390 (거리 10)
            _row("b", 9_000, 3),                        # b → 9000
            _row("c", 6_400, 4), _row("c", 6_400, 9)]   # c → id 9
    picked = sc.select_baseline_rows(rows, ref)
    gaps = sorted(now - int(r["collected_at"]) for _k, r in picked.values())
    assert gaps == [1000, 3600, 3610]
    s = sc._interval_summary(gaps)
    assert s["owners"] == 3
    assert s["minSeconds"] == 1000 and s["maxSeconds"] == 3610
    assert s["p50Seconds"] == 3600          # ceil(0.5*3)=2 → 두 번째
    assert s["p95Seconds"] == 3610


# ── 응답 계약 (개인정보 미노출) ────────────────────────────────────────────
_FORBIDDEN_KEYS = {"owner_channel_id", "ownerChannelId", "channelId",
                   "channel_id", "clipUid", "clip_uid", "channelName",
                   "channel_name", "nickname", "authorNickname"}


def _walk(o, seen_keys, seen_vals):
    if isinstance(o, dict):
        for k, v in o.items():
            seen_keys.add(k)
            _walk(v, seen_keys, seen_vals)
    elif isinstance(o, list):
        for v in o:
            _walk(v, seen_keys, seen_vals)
    else:
        seen_vals.append(o)


def test_owner_intervals_shape_has_no_identifiers(db):
    """진단 응답에 owner id·clip uid·닉네임이 들어가면 안 된다."""
    payload = {
        "available": True, "bucketAt": "2026-08-02T14:00:00+09:00",
        "targetAt": "2026-08-02T13:43:36+09:00", "rowsScanned": 1256,
        "percentileRule": "nearest-rank (rank = ceil(q*n), 1-indexed)",
        "ownerIntervalDistribution": sc._interval_summary([152, 2483, 900]),
        "histogram": sc._interval_histogram([152, 2483, 900]),
        "targetDistanceDistribution": sc._interval_summary([10, 20]),
        "positiveMoverIntervalDistribution": {
            **sc._interval_summary([2483]), "positiveOwners": 1},
    }
    keys, vals = set(), []
    _walk(payload, keys, vals)
    assert not (keys & _FORBIDDEN_KEYS), keys & _FORBIDDEN_KEYS


def test_response_size_stays_small():
    """집계만 담으므로 응답이 참가자 수에 비례해 커지면 안 된다."""
    import json
    big = list(range(1, 6501))
    payload = {
        "ownerIntervalDistribution": sc._interval_summary(big),
        "histogram": sc._interval_histogram(big),
        "targetDistanceDistribution": sc._interval_summary(big),
        "positiveMoverIntervalDistribution": {
            **sc._interval_summary(big[:100]), "positiveOwners": 100},
    }
    assert len(json.dumps(payload).encode()) < 2048


# ── 규모 벤치마크 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("n", [1300, 6500])
def test_aggregate_is_fast_at_scale(n):
    rows = [_row(f"o{i}", 6000 + (i % 2400), i + 1) for i in range(n)]
    t0 = time.perf_counter()
    picked = sc.select_baseline_rows(rows, 7200)
    gaps = [10_000 - int(r["collected_at"]) for _k, r in picked.values()]
    sc._interval_summary(gaps)
    sc._interval_histogram(gaps)
    took = (time.perf_counter() - t0) * 1000
    assert len(picked) == n
    assert took < 500, f"{n}명 집계 {took:.1f}ms"

# ── _interval_stats 계약 (분 단위 · 창 카운터) ─────────────────────────────
WIN = 3600


def test_stats_empty_keeps_nulls_not_zeros():
    """'계산 불가'와 '0분'은 다른 뜻이다 — 표본이 없으면 백분위는 null이어야 한다."""
    s = sc._interval_stats([], WIN)
    assert s["owners"] == 0
    for k in ("minMinutes", "p25Minutes", "p50Minutes", "p75Minutes",
              "p90Minutes", "p95Minutes", "maxMinutes", "averageMinutes"):
        assert s[k] is None, k
    assert s["exact60m"] == s["under60m"] == s["over60m"] == 0
    assert sum(b["owners"] for b in s["histogram"]) == 0


def test_stats_window_counters_are_exclusive():
    vals = [3599, 3600, 3601, 3600, 100]
    s = sc._interval_stats(vals, WIN)
    assert s["exact60m"] == 2
    assert s["under60m"] == 2          # 3599, 100
    assert s["over60m"] == 1           # 3601
    assert s["exact60m"] + s["under60m"] + s["over60m"] == len(vals)


def test_stats_minutes_rounding():
    s = sc._interval_stats([152, 2483], WIN)
    assert s["minMinutes"] == 2.5      # 152s
    assert s["maxMinutes"] == 41.4     # 2483s


def test_stats_quartiles_are_nearest_rank():
    vals = [60, 120, 180, 240]
    s = sc._interval_stats(vals, WIN)
    assert s["p25Minutes"] == 1.0      # ceil(.25*4)=1 → 60s
    assert s["p50Minutes"] == 2.0      # ceil(.50*4)=2 → 120s
    assert s["p75Minutes"] == 3.0      # ceil(.75*4)=3 → 180s


def test_stats_single_sample():
    s = sc._interval_stats([600], WIN)
    assert all(s[k] == 10.0 for k in ("minMinutes", "p25Minutes", "p50Minutes",
                                      "p75Minutes", "p90Minutes", "p95Minutes",
                                      "maxMinutes", "averageMinutes"))
    assert s["under60m"] == 1


# ── production 등가성: 관측 판정이 load_main과 같은 조건을 쓰는가 ──────────
def _classify(picked, cur_rows, ref, now):
    """`_baseline_report_uncached`의 분류를 그대로 옮긴 참조 구현.

    소스 검사(아래)로 실제 코드가 이 조건을 갖고 있는지 고정하고, 여기서는
    조건 자체가 load_main의 movers 판정과 같은 결과를 내는지 확인한다.
    """
    eligible, positive = [], []
    missing = repchg = recov = 0
    for cr in cur_rows:
        hit = picked.get(cr["channel_id"])
        if hit is None:
            missing += 1
            continue
        b = hit[1]
        gap = now - int(b["collected_at"])
        if str(b["clip_uid"]) != str(cr["uid"]):
            repchg += 1
            continue
        if ref and int(cr["metrics_recovered_at"] or 0) >= ref:
            recov += 1
            continue
        eligible.append(gap)
        if int(cr["heart_count"]) - int(b["heart_count"]) > 0:
            positive.append(gap)
    return eligible, positive, missing, repchg, recov


def _cur(cid, uid, heart, recovered_at=0):
    return Row(channel_id=cid, uid=uid, heart_count=heart,
               metrics_recovered_at=recovered_at)


def test_positive_population_matches_production_rules():
    now, ref = 10_000, 6_400
    picked = sc.select_baseline_rows([
        _row("ok",   6_400, 1, uid="u1", heart=10),   # 정상 양수
        _row("zero", 6_400, 2, uid="u2", heart=10),   # 증가 0
        _row("down", 6_400, 3, uid="u3", heart=10),   # 감소
        _row("rep",  6_400, 4, uid="old", heart=10),  # 대표 교체
        _row("rec",  6_400, 5, uid="u5", heart=10),   # recovering
    ], ref)
    cur_rows = [
        _cur("ok", "u1", 15),
        _cur("zero", "u2", 10),
        _cur("down", "u3", 5),
        _cur("rep", "new", 99),
        _cur("rec", "u5", 50, recovered_at=ref + 1),   # 기준선 이후 복구
        _cur("nobase", "u9", 40),                      # 기준선 없음
    ]
    eligible, positive, missing, repchg, recov = _classify(picked, cur_rows, ref, now)
    assert len(positive) == 1, "양수는 'ok' 한 명뿐이어야 한다"
    assert len(eligible) == 3, "ok/zero/down 만 계산 가능"
    assert (missing, repchg, recov) == (1, 1, 1)


def test_recovering_owner_is_excluded_from_positive():
    now, ref = 10_000, 6_400
    picked = sc.select_baseline_rows([_row("a", 6_400, 1, uid="u", heart=1)], ref)
    cur = [_cur("a", "u", 999, recovered_at=ref)]      # 정확히 ref == 경계
    _e, positive, _m2, _r, recov = _classify(picked, cur, ref, now)
    assert positive == [] and recov == 1, "ref 경계는 제외 쪽(>=)이다"


def test_recovered_before_ref_is_included():
    now, ref = 10_000, 6_400
    picked = sc.select_baseline_rows([_row("a", 6_400, 1, uid="u", heart=1)], ref)
    cur = [_cur("a", "u", 5, recovered_at=ref - 1)]
    _e, positive, _m2, _r, recov = _classify(picked, cur, ref, now)
    assert len(positive) == 1 and recov == 0


def test_source_has_active_and_recovery_guards():
    """진단 SQL이 `active=1`을, 분류가 복구 가드를 갖고 있는지 고정한다.
    둘 중 하나라도 빠지면 화면과 진단의 모집단이 갈라진다."""
    import inspect
    src = inspect.getsource(sc._baseline_report_uncached)
    assert "c.active=1" in src, "진단이 비활성 클립을 포함하고 있다"
    assert "metrics_recovered_at" in src, "복구 가드가 없다"
    assert "representative_clip_uid" in src
    # load_main과 같은 조인 형태인지
    assert "singcup_streamers" in src and "singcup_clips" in src


def test_production_movers_criteria_unchanged():
    """load_main 쪽 판정식이 바뀌면 진단도 같이 바뀌어야 한다 — 여기서 알린다."""
    import inspect
    src = inspect.getsource(sc._load_main_uncached)
    assert 'p[2] != r["clip_uid"]' in src
    assert 'int(r["metrics_recovered_at"] or 0) >= ref_ts' in src
    assert "if d <= 0:" in src
    assert "c.active=1" in src


# ── currentMetricsAgeDistribution (Change Set B) ────────────────────────────
# 기준선 비교 간격과 **다른 지표**다. `last_heart_at` = 하트를 정상 수신한 마지막
# 시각이며, 값이 변하지 않아도 갱신된다.
NOW = 100_000


def _age(ages, count=None, missing=0, future=0, now=NOW):
    return sc._metrics_age_stats(list(ages), count=count if count is not None
                                 else len(ages) + missing + future,
                                 missing=missing, future=future, now=now)


def test_age_unit_and_metadata_are_explicit():
    s = _age([600])
    assert s["unit"] == "seconds"
    assert s["field"] == "last_heart_at"
    assert "nearest-rank" in s["percentileRule"]
    assert s["generatedAt"]                      # 언제 계산한 값인지 응답에 있다


def test_age_empty_population():
    s = _age([])
    assert s["count"] == 0 and s["observedCount"] == 0
    for k in ("minSeconds","p50Seconds","p90Seconds","p95Seconds","p99Seconds",
              "maxSeconds","averageSeconds","p50Minutes","maxMinutes"):
        assert s[k] is None, k
    assert sum(b["owners"] for b in s["histogram"]) == 0


def test_age_single_sample():
    s = _age([1800])
    assert s["observedCount"] == 1
    assert s["minSeconds"] == s["p50Seconds"] == s["p99Seconds"] == s["maxSeconds"] == 1800
    assert s["p50Minutes"] == 30.0


def test_age_missing_is_not_counted_as_zero():
    """관측 이력이 없는 대상을 0초로 섞으면 '방금 갱신됨'이 된다."""
    s = _age([3600], count=3, missing=2)
    assert s["count"] == 3 and s["observedCount"] == 1
    assert s["missingObservedAt"] == 2
    assert s["minSeconds"] == 3600, "missing이 0초로 섞였다"


def test_age_future_is_counted_separately():
    s = _age([600], count=2, future=1)
    assert s["futureObservedAt"] == 1
    assert s["observedCount"] == 1
    assert s["minSeconds"] == 600


def test_age_counts_reconcile():
    s = _age([100, 200], count=7, missing=3, future=2)
    assert s["observedCount"] + s["missingObservedAt"] + s["futureObservedAt"] == s["count"]


def test_age_never_negative():
    s = _age([0, 1, 5])
    assert s["minSeconds"] >= 0
    assert all(b["owners"] >= 0 for b in s["histogram"])


@pytest.mark.parametrize("q,key,expected", [
    (0.50, "p50Seconds", 300), (0.90, "p90Seconds", 500),
    (0.95, "p95Seconds", 500), (0.99, "p99Seconds", 500),
])
def test_age_percentiles_are_nearest_rank(q, key, expected):
    s = _age([100, 200, 300, 400, 500])
    assert s[key] == expected


def test_age_percentiles_equal_values():
    s = _age([777] * 9)
    for k in ("p50Seconds","p90Seconds","p95Seconds","p99Seconds","maxSeconds"):
        assert s[k] == 777


def test_age_histogram_boundaries_half_open():
    edges = [0, 600, 1200, 1800, 2700, 3600, 5400, 7200]
    s = _age(edges)
    assert sum(b["owners"] for b in s["histogram"]) == len(edges)
    for i, e in enumerate(edges):
        assert s["histogram"][i]["fromSeconds"] == e
        assert s["histogram"][i]["owners"] == 1, (e, s["histogram"][i])
    assert s["histogram"][-1]["toSeconds"] is None


def test_age_histogram_last_bucket_open_ended():
    s = _age([7200, 20000, 999999])
    assert s["histogram"][-1]["owners"] == 3


def test_age_minutes_mirror_seconds():
    s = _age([90, 3600])
    assert s["minMinutes"] == 1.5 and s["maxMinutes"] == 60.0


def test_age_response_has_no_identifiers():
    keys, vals = set(), []
    _walk(_age([100, 200]), keys, vals)
    assert not (keys & _FORBIDDEN_KEYS), keys & _FORBIDDEN_KEYS


def test_age_response_size_is_bounded():
    import json
    big = list(range(1, 6501))
    assert len(json.dumps(_age(big)).encode()) < 2048


@pytest.mark.parametrize("n", [1300, 6500])
def test_age_aggregate_is_fast(n):
    vals = [(i * 7) % 9000 for i in range(n)]
    t0 = time.perf_counter()
    _age(vals)
    took = (time.perf_counter() - t0) * 1000
    assert took < 300, f"{n}건 {took:.1f}ms"


# ── 모집단 일치 · 소스 계약 ────────────────────────────────────────────────
def test_age_population_matches_current_eligible_source():
    """`count`가 currentEligible의 owners와 같은 루프에서 나오는지 소스로 고정한다.
    다른 모집단을 쓰면 두 분포의 대상 수가 달라지고 설명할 수 없게 된다."""
    import inspect
    src = inspect.getsource(sc._baseline_report_uncached)
    assert "count=len(eligible_gaps)" in src, "count가 eligible과 다른 값에서 나온다"
    assert "eligible_ages.append(now - lha)" in src
    # eligible에 들어간 뒤에만 age를 모으는가(= 필터를 모두 통과한 대상)
    i_gap = src.index("eligible_gaps.append(gap)")
    i_age = src.index("lha = int(cr[\"last_heart_at\"] or 0)")
    assert i_age > i_gap, "age를 eligible 필터 통과 전에 모으고 있다"


def test_age_uses_last_heart_at_not_last_attempt_at():
    import inspect
    src = inspect.getsource(sc._baseline_report_uncached)
    assert 'cr["last_heart_at"]' in src
    assert "last_attempt_at" not in src, "실패에도 갱신되는 필드를 freshness로 쓰고 있다"


def test_age_query_adds_no_extra_scan():
    """추가 SELECT 없이 기존 cur_rows 조회에 컬럼만 더했는지 확인한다."""
    import inspect
    src = inspect.getsource(sc._baseline_report_uncached)
    assert src.count("FROM singcup_streamers s ") == 1, "대표 조회가 두 번 돈다"
    assert "c.last_heart_at" in src


def test_baseline_report_is_read_only():
    """진단 경로에 쓰기 SQL이 없다."""
    import inspect
    import re
    src = inspect.getsource(sc._baseline_report_uncached)
    for kw in ("INSERT", "UPDATE", "DELETE", "COMMIT", "db.commit"):
        assert not re.search(kw, src, re.I), f"쓰기 연산 발견: {kw}"


def test_existing_three_distributions_are_kept():
    import inspect
    src = inspect.getsource(sc._baseline_report_uncached)
    for k in ("baselineOwnerIntervalDistribution",
              "currentEligibleOwnerIntervalDistribution",
              "positiveMoverIntervalDistribution",
              "currentMetricsAgeDistribution"):
        assert f'"{k}"' in src, k


# ── 응답 계약 실측 고정 (문자열 검사에만 의존하지 않는다) ───────────────────
def test_generated_at_matches_the_pinned_now():
    """generatedAt은 요청에서 한 번 고정한 now를 그대로 쓴다(재계산하지 않는다)."""
    now = 1785666000                       # 2026-08-02 19:20:00 KST
    s = sc._metrics_age_stats([600], count=1, missing=0, future=0, now=now)
    assert s["generatedAt"] == sc._iso(now)
    assert s["generatedAt"].startswith("2026-08-02T19:20:00")
    assert s["generatedAt"].endswith("+09:00")


@pytest.mark.parametrize("ages,count,missing,future", [
    ([], 0, 0, 0),
    ([1], 1, 0, 0),
    ([1, 2, 3], 5, 1, 1),
    ([10] * 50, 60, 7, 3),
    (list(range(0, 9000, 7)), 1300, 12, 5),
])
def test_count_invariants_hold(ages, count, missing, future):
    n = len(ages) + missing + future
    c = max(count, n)
    s = sc._metrics_age_stats(list(ages), count=c, missing=missing, future=future,
                              now=1785666000)
    assert s["observedCount"] == len(ages)
    assert s["observedCount"] + s["missingObservedAt"] + s["futureObservedAt"] <= s["count"]
    assert sum(b["owners"] for b in s["histogram"]) == s["observedCount"], \
        "histogram 합이 observedCount와 다르다"


def test_histogram_sum_equals_observed_not_count():
    s = sc._metrics_age_stats([100, 200], count=10, missing=5, future=3, now=1785666000)
    assert sum(b["owners"] for b in s["histogram"]) == 2 == s["observedCount"]
    assert s["count"] == 10


def test_age_distribution_has_no_secret_like_fields():
    s = sc._metrics_age_stats([100], count=1, missing=0, future=0, now=1785666000)
    joined = json.dumps(s, ensure_ascii=False).lower()
    for bad in ("secret", "token", "authorization", "channelid", "clipuid",
                "owner_channel_id", "nickname", "guild"):
        assert bad not in joined, bad


# ── 관리자 엔드포인트 계약 (실제 함수·DB 상태로 고정) ───────────────────────
def _rows_snapshot(conn):
    out = {}
    for t in ("singcup_clips", "singcup_streamers", "singcup_snapshots",
              "singcup_sweep_runs"):
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception:
            out[t] = None
    return out


def test_baseline_report_does_not_write_rows(db):
    """호출 전후 DB 행 수가 같아야 한다 — 진단은 읽기 전용이다."""
    import sqlite3

    import database
    before = after = None
    con = sqlite3.connect(database.DB_PATH)
    try:
        before = _rows_snapshot(con)
    finally:
        con.close()
    db(sc.baseline_report())
    con = sqlite3.connect(database.DB_PATH)
    try:
        after = _rows_snapshot(con)
    finally:
        con.close()
    assert before == after, f"행 수가 변했다: {before} → {after}"


def test_baseline_report_shape_without_baseline(db):
    """기준 버킷이 없으면 ownerIntervals는 available=false 로만 나타난다.
    이때 currentMetricsAgeDistribution 키는 존재하지 않는다 — 계약을 고정한다."""
    d = db(sc.baseline_report())
    oi = d.get("ownerIntervals")
    assert oi is not None, "ownerIntervals 키가 사라졌다"
    if not oi.get("available"):
        assert oi.get("reason") == "no_baseline_bucket"
        assert "currentMetricsAgeDistribution" not in oi
    else:
        cm = oi["currentMetricsAgeDistribution"]
        assert cm["count"] == oi["currentEligibleOwnerIntervalDistribution"]["owners"]


def test_baseline_report_makes_no_external_calls(db, monkeypatch):
    """진단이 외부 HTTP를 부르면 즉시 실패한다."""
    import httpx

    def boom(*a, **kw):
        raise AssertionError("외부 HTTP 호출이 발생했다")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    monkeypatch.setattr(httpx, "get", boom, raising=False)
    d = db(sc.baseline_report())
    assert "ownerIntervals" in d


def test_baseline_report_response_has_no_identifiers(db):
    keys, vals = set(), []
    _walk(db(sc.baseline_report()).get("ownerIntervals") or {}, keys, vals)
    assert not (keys & _FORBIDDEN_KEYS), keys & _FORBIDDEN_KEYS


def test_admin_secret_gate_is_unchanged():
    """인증 계약: secret 미설정이면 503, 틀리면 401. 데이터는 나가지 않는다."""
    import routers.singcup_router as r
    from fastapi import HTTPException
    orig = r.ADMIN_SECRET
    try:
        r.ADMIN_SECRET = ""
        with pytest.raises(HTTPException) as e1:
            r._require_secret("anything")
        assert e1.value.status_code == 503
        r.ADMIN_SECRET = "s3cr3t-dummy-not-real"
        with pytest.raises(HTTPException) as e2:
            r._require_secret("wrong")
        assert e2.value.status_code == 401
        with pytest.raises(HTTPException) as e3:
            r._require_secret(None)
        assert e3.value.status_code == 401
        r._require_secret("s3cr3t-dummy-not-real")          # 통과해야 한다
    finally:
        r.ADMIN_SECRET = orig


def test_admin_baseline_route_sets_no_public_cache():
    """공개 캐시 헤더를 붙이지 않는다 — /main의 public 헤더와 달라야 한다."""
    import inspect

    import routers.singcup_router as r
    src = inspect.getsource(r.snapshot_baseline)
    assert "public" not in src.lower()
    assert "Cache-Control" not in src
