"""급상승 스트리머 점수 — 순수 함수 모음(DB·네트워크 없음).

왜 다시 만들었나
----------------
기존 방식은 '한 시점의 스냅샷 하나 vs 24시간 전 스냅샷 하나'를 퍼센트로만 비교해
정렬했다. 그래서 시청자 1명 -> 8명(+700%)이 200명 -> 300명(+50%)을 언제나 이겼다.
표본이 1개뿐이라 잡음에도 취약했다(마침 그 순간 1명이었을 뿐일 수 있다).

바꾼 원칙
- 값은 '시간 구간의 중앙값'으로 본다. 스냅샷 하나의 튐에 흔들리지 않는다.
- 화면에는 실제 증가율(rawGrowthRate)을 사실 그대로 보여준다. 지우지 않는다.
- 순위는 절대 증가량 + 보정 증가율 + 지속 시간 + 데이터 신뢰도를 함께 본다.
- 작은 채널을 배제하지 않는다. 기준이 너무 작으면 '신규 급부상'으로 따로 세운다.

수집 주기(현재 10분)가 바뀔 수 있으므로 이 모듈은 '행 개수'를 가정하지 않는다.
호출자가 collected_at 시간 범위로 뽑은 표본 리스트를 넘긴다.
"""
from __future__ import annotations

import os
from math import log2
from statistics import median

# ── 임계값 (운영 중 조정 가능) ──────────────────────────────────────────────
MIN_CURRENT_VIEWERS = int(os.getenv("RISING_MIN_CURRENT_VIEWERS", "20"))
MIN_BASELINE_VIEWERS = int(os.getenv("RISING_MIN_BASELINE_VIEWERS", "20"))
MIN_ABSOLUTE_DELTA = int(os.getenv("RISING_MIN_ABSOLUTE_DELTA", "10"))
MIN_DURATION_MINUTES = int(os.getenv("RISING_MIN_DURATION_MINUTES", "30"))
MIN_COVERAGE = float(os.getenv("RISING_MIN_COVERAGE", "0.8"))
# 신규 급부상은 '작아서 퍼센트가 커진' 경우라 절대 증가량 문턱을 더 높인다
NEW_BREAKOUT_MIN_DELTA = int(os.getenv("RISING_NEW_BREAKOUT_MIN_DELTA", "15"))
# 각 구간에 최소 몇 개의 정상 표본이 있어야 중앙값을 믿을 수 있는가
MIN_SAMPLES_PER_WINDOW = int(os.getenv("RISING_MIN_SAMPLES_PER_WINDOW", "2"))

# 상태값 — 화면에서 '0명'과 '데이터 없음'을 절대 섞지 않기 위해 문자열로 구분한다
ST_VALID = "VALID"                    # 일반 급상승 조건을 모두 통과
ST_NEW_BREAKOUT = "NEW_BREAKOUT"      # 기준이 너무 작거나 없음 → 별도 그룹
ST_COLLECTING = "COLLECTING"          # 방송 시작 30분 미만 → 아직 판단 불가
ST_NO_BASELINE = "NO_BASELINE"        # 비교할 과거 구간이 없음
ST_PARTIAL = "PARTIAL"                # 수집 커버리지 부족
ST_OFFLINE = "OFFLINE"                # 현재 방송 중이 아님
ST_INSUFFICIENT = "INSUFFICIENT_DATA"  # 표본 부족 / 최소 조건 미달


def window_median(samples: list[int]) -> int | None:
    """구간 중앙값. 표본이 모자라면 None — 0으로 대신하지 않는다.

    평균이 아니라 중앙값을 쓰는 이유: 수집 한 번이 튀거나(부분 실패로 과소 집계)
    방송 종료 직전 급락이 섞여도 구간 대표값이 흔들리지 않는다.
    """
    vals = [int(v) for v in samples if v is not None]
    if len(vals) < MIN_SAMPLES_PER_WINDOW:
        return None
    return int(median(vals))


def raw_growth_rate(current: int, baseline: int) -> float | None:
    """사용자에게 보여줄 '있는 그대로'의 증가율. 기준이 0이면 계산하지 않는다."""
    if baseline <= 0:
        return None
    return round((current - baseline) / baseline * 100, 1)


def adjusted_growth_rate(current: int, baseline: int,
                         floor: int = MIN_BASELINE_VIEWERS) -> float:
    """랭킹용 보정 증가율 — 분모에 하한을 둬 작은 기준값의 폭주를 막는다.

    1 -> 8   : raw 700%  / adjusted  35%
    2 -> 20  : raw 900%  / adjusted  90%
    20 -> 60 : raw 200%  / adjusted 200%   (하한 이상이면 raw와 같다)
    200 -> 300: raw 50%  / adjusted  50%
    """
    return round((current - baseline) / max(baseline, floor) * 100, 1)


def persistence_factor(minutes: float) -> float:
    """상승이 얼마나 오래 유지됐는지에 대한 가중치. 60분에서 상한을 둔다.

    상한이 없으면 '오래 켜둔 방송'이 상승과 무관하게 계속 유리해진다.
    """
    if minutes >= 60:
        return 1.15
    if minutes >= 50:
        return 1.10
    if minutes >= 40:
        return 1.05
    return 1.00


def confidence_factor(coverage: float, samples: int,
                      expected_samples: int) -> float:
    """0~1. 커버리지(정상 수집 비율)와 표본 수를 함께 본다.

    최소 조건을 통과했더라도 표본이 더 촘촘한 채널을 약간 우선한다 —
    같은 증가량이면 '더 확실히 관측된' 쪽이 신뢰할 만하다.
    """
    cov = max(0.0, min(1.0, float(coverage)))
    if expected_samples <= 0:
        return round(cov, 3)
    dens = max(0.0, min(1.0, samples / expected_samples))
    # 커버리지를 주로 보고 표본 밀도를 보조로 반영한다
    return round(cov * 0.7 + dens * 0.3, 3)


def rising_score(current: int, baseline: int, *, minutes: float,
                 coverage: float, samples: int, expected_samples: int) -> float:
    """급상승 점수. 절대 증가량을 뼈대로 하고 상대 성장을 로그로 눌러 얹는다.

    - max(absoluteDelta, 0): 감소한 채널은 0점(순위에 오르지 않는다)
    - log2(2 + current/max(baseline, floor)): 배수 성장을 반영하되 로그로 눌러
      1 -> 8 같은 소규모 폭증이 절대량을 압도하지 못하게 한다
    - persistence / confidence: 오래 유지되고 촘촘히 관측된 상승에 가중
    """
    delta = max(current - baseline, 0)
    ratio = current / max(baseline, MIN_BASELINE_VIEWERS)
    return round(
        delta
        * log2(2 + ratio)
        * persistence_factor(minutes)
        * confidence_factor(coverage, samples, expected_samples),
        3)


def is_rising_trend(series: list[int], min_ups: int = 2) -> bool:
    """최근 표본들이 '계속 오르는 흐름'인지. 한 번의 순간 급등만으로는 통과 못 한다.

    연속한 값들의 차이 중 양수가 min_ups개 이상이어야 한다.
    (기본: 최근 4개 표본 -> 3개 변화 중 2개 이상 상승)
    """
    vals = [int(v) for v in series if v is not None]
    if len(vals) < min_ups + 1:
        return False
    ups = sum(1 for a, b in zip(vals, vals[1:]) if b > a)
    return ups >= min_ups


def classify(*, current: int | None, baseline: int | None, minutes: float,
             coverage: float, trend_ok: bool, same_session: bool) -> str:
    """이 채널을 어떤 상태로 볼 것인가. 0과 '데이터 없음'을 반드시 구분한다."""
    if current is None:
        return ST_OFFLINE
    if minutes < MIN_DURATION_MINUTES:
        return ST_COLLECTING
    if coverage < MIN_COVERAGE:
        return ST_PARTIAL
    if baseline is None:
        # 비교할 과거 구간이 아예 없다 — 지금 규모가 충분하면 '신규 급부상' 후보
        return (ST_NEW_BREAKOUT if current >= MIN_CURRENT_VIEWERS
                else ST_NO_BASELINE)
    delta = current - baseline
    # 방송이 끊겼다 다시 켜진 경우 직접 비교하지 않는다(다른 방송이다)
    if not same_session:
        return (ST_NEW_BREAKOUT
                if current >= MIN_CURRENT_VIEWERS and delta >= NEW_BREAKOUT_MIN_DELTA
                else ST_INSUFFICIENT)
    if baseline < MIN_BASELINE_VIEWERS:
        # 기준이 작아 퍼센트가 폭주하는 구간 — 배제하지 않고 따로 세운다
        return (ST_NEW_BREAKOUT
                if current >= MIN_CURRENT_VIEWERS and delta >= NEW_BREAKOUT_MIN_DELTA
                else ST_INSUFFICIENT)
    if current < MIN_CURRENT_VIEWERS or delta < MIN_ABSOLUTE_DELTA:
        return ST_INSUFFICIENT
    if not trend_ok:
        return ST_INSUFFICIENT
    return ST_VALID


def sort_key_rising(item: dict):
    """일반 급상승 정렬 — 점수 내림, 동률은 절대 증가량 -> 현재 -> id로 안정 정렬."""
    return (-float(item.get("risingScore") or 0),
            -int(item.get("absoluteDelta") or 0),
            -int(item.get("currentViewers") or 0),
            str(item.get("channelId") or ""))


def sort_key_breakout(item: dict):
    """신규 급부상 정렬 — 퍼센트로 줄 세우지 않는다(작을수록 퍼센트가 커지므로).

    절대 증가량 -> 현재 시청자 -> 상승 유지 시간 순.
    """
    return (-int(item.get("absoluteDelta") or 0),
            -int(item.get("currentViewers") or 0),
            -float(item.get("persistenceMinutes") or 0),
            str(item.get("channelId") or ""))
