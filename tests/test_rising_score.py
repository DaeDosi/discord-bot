"""급상승 점수 공식 — 순수 함수 단위 테스트.

핵심 회귀 방지: 시청자 1명 -> 8명(+700%)이 '퍼센트가 크다'는 이유만으로
200명 -> 300명을 제치고 1위가 되는 일이 없어야 한다.
"""
import pytest
import rising_score as rs


# ── 구간 중앙값 ─────────────────────────────────────────────────────────────
def test_median_ignores_a_single_spike():
    """스냅샷 하나가 튀어도 구간 대표값은 흔들리지 않아야 한다."""
    assert rs.window_median([50, 52, 900]) == 52
    assert rs.window_median([50, 52]) == 51


def test_median_needs_minimum_samples():
    """표본이 모자라면 0으로 대신하지 않고 '없음'이다."""
    assert rs.window_median([]) is None
    assert rs.window_median([42]) is None          # 1개는 부족
    assert rs.window_median([42, 44]) == 43


def test_median_does_not_assume_row_count():
    """수집 주기가 5분으로 바뀌어 표본이 6개가 되어도 그대로 동작한다."""
    assert rs.window_median([10, 12, 14, 16, 18, 20]) == 15


# ── 증가율 ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cur,base,raw,adj", [
    (8, 1, 700.0, 35.0),        # 소규모 폭증 — 보정하면 확 눌린다
    (20, 2, 900.0, 90.0),
    (60, 20, 200.0, 200.0),     # 하한 이상이면 보정값 = 실제값
    (300, 200, 50.0, 50.0),
])
def test_growth_rates(cur, base, raw, adj):
    assert rs.raw_growth_rate(cur, base) == raw
    assert rs.adjusted_growth_rate(cur, base) == adj


def test_raw_rate_is_none_when_baseline_zero():
    """0에서 시작한 증가는 퍼센트로 표현할 수 없다 → NEW로 다룬다."""
    assert rs.raw_growth_rate(30, 0) is None


def test_adjusted_rate_can_be_negative():
    assert rs.adjusted_growth_rate(10, 50) < 0


# ── 유지 계수 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("m,f", [(30, 1.00), (39, 1.00), (40, 1.05),
                                 (50, 1.10), (60, 1.15), (600, 1.15)])
def test_persistence_factor_has_a_cap(m, f):
    """상한이 없으면 오래 켜둔 방송이 상승과 무관하게 계속 유리해진다."""
    assert rs.persistence_factor(m) == f


# ── 신뢰도 ──────────────────────────────────────────────────────────────────
def test_confidence_is_bounded_0_1():
    assert rs.confidence_factor(1.0, 6, 6) == 1.0
    assert rs.confidence_factor(0.0, 0, 6) == 0.0
    assert 0 < rs.confidence_factor(0.8, 3, 6) < 1


def test_confidence_prefers_denser_samples():
    a = rs.confidence_factor(0.9, 6, 6)
    b = rs.confidence_factor(0.9, 3, 6)
    assert a > b


# ── 점수: 핵심 회귀 ─────────────────────────────────────────────────────────
def _score(cur, base, minutes=40, coverage=1.0, samples=6, expected=6):
    return rs.rising_score(cur, base, minutes=minutes, coverage=coverage,
                           samples=samples, expected_samples=expected)


def test_tiny_percent_spike_does_not_beat_real_growth():
    """1 -> 8 (+700%)가 200 -> 300 (+50%)을 이기면 안 된다."""
    assert _score(8, 1) < _score(300, 200)


def test_absolute_growth_dominates_but_ratio_still_counts():
    """같은 절대 증가량이면 배수가 큰 쪽이 조금 더 높다."""
    a = _score(60, 20)     # +40, 3배
    b = _score(240, 200)   # +40, 1.2배
    assert a > b
    # 그렇다고 소규모가 대규모 증가를 압도하지는 않는다
    assert _score(60, 20) < _score(500, 200)


def test_decrease_scores_zero():
    assert _score(10, 50) == 0


def test_score_rises_with_persistence_and_confidence():
    assert _score(126, 48, minutes=60) > _score(126, 48, minutes=30)
    assert _score(126, 48, coverage=1.0) > _score(126, 48, coverage=0.85)


def test_ranking_order_is_sensible():
    """실제 분포를 흉내 낸 표본에서 상위권이 상식적인지."""
    cases = {"1->8": (8, 1), "3->29": (29, 3), "48->126": (126, 48),
             "200->300": (300, 200), "2000->2100": (2100, 2000)}
    ranked = sorted(cases.items(), key=lambda kv: -_score(*kv[1]))
    names = [k for k, _ in ranked]
    assert names[0] in ("2000->2100", "48->126", "200->300")
    assert names[-1] == "1->8", "소규모 폭증이 꼴찌여야 한다"


# ── 상승 흐름 ───────────────────────────────────────────────────────────────
def test_trend_needs_sustained_increase():
    assert rs.is_rising_trend([10, 12, 14, 16]) is True
    assert rs.is_rising_trend([10, 11, 10, 11]) is True     # 상승 2회
    assert rs.is_rising_trend([10, 30, 9, 8]) is False      # 순간 급등 1회뿐
    assert rs.is_rising_trend([10, 9]) is False             # 표본 부족


# ── 상태 분류 ───────────────────────────────────────────────────────────────
def _cls(**kw):
    base = dict(current=126, baseline=48, minutes=40, coverage=1.0,
                trend_ok=True, same_session=True)
    base.update(kw)
    return rs.classify(**base)


def test_classify_valid():
    assert _cls() == rs.ST_VALID


def test_classify_collecting_before_30_minutes():
    assert _cls(minutes=20) == rs.ST_COLLECTING


def test_classify_partial_on_low_coverage():
    assert _cls(coverage=0.5) == rs.ST_PARTIAL


def test_classify_offline():
    assert _cls(current=None) == rs.ST_OFFLINE


def test_small_baseline_becomes_new_breakout_not_excluded():
    """작은 채널을 잘라내지 않고 별도 그룹으로 보낸다."""
    assert _cls(current=29, baseline=3) == rs.ST_NEW_BREAKOUT


def test_small_baseline_with_tiny_delta_is_insufficient():
    assert _cls(current=21, baseline=18) == rs.ST_INSUFFICIENT


def test_no_baseline_with_enough_size_is_breakout():
    assert _cls(baseline=None, current=40) == rs.ST_NEW_BREAKOUT
    assert _cls(baseline=None, current=5) == rs.ST_NO_BASELINE


def test_different_session_is_not_compared_directly():
    """방송이 끊겼다 다시 켜졌으면 같은 방송의 상승이 아니다."""
    assert _cls(same_session=False, current=126, baseline=48) == rs.ST_NEW_BREAKOUT
    assert _cls(same_session=False, current=25, baseline=24) == rs.ST_INSUFFICIENT


def test_thresholds_are_enforced():
    assert _cls(current=15, baseline=20) == rs.ST_INSUFFICIENT   # 현재 20 미만
    assert _cls(current=25, baseline=20) == rs.ST_INSUFFICIENT   # 절대 증가 10 미만
    assert _cls(trend_ok=False) == rs.ST_INSUFFICIENT


# ── 정렬 ────────────────────────────────────────────────────────────────────
def test_breakout_is_not_sorted_by_percent():
    """신규 급부상을 퍼센트로 줄 세우면 다시 '1 -> 8'이 1위가 된다."""
    items = [
        {"channelId": "a", "absoluteDelta": 7,  "currentViewers": 8,
         "rawGrowthRate": 700.0, "persistenceMinutes": 30},
        {"channelId": "b", "absoluteDelta": 26, "currentViewers": 29,
         "rawGrowthRate": 866.7, "persistenceMinutes": 30},
        {"channelId": "c", "absoluteDelta": 26, "currentViewers": 40,
         "rawGrowthRate": 185.7, "persistenceMinutes": 30},
    ]
    order = [x["channelId"] for x in sorted(items, key=rs.sort_key_breakout)]
    assert order == ["c", "b", "a"], "절대 증가량 -> 현재 시청자 순"


def test_rising_sort_is_stable_on_ties():
    items = [{"channelId": "b", "risingScore": 10, "absoluteDelta": 5, "currentViewers": 50},
             {"channelId": "a", "risingScore": 10, "absoluteDelta": 5, "currentViewers": 50}]
    assert [x["channelId"] for x in sorted(items, key=rs.sort_key_rising)] == ["a", "b"]
