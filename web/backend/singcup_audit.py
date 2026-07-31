"""삭제 클립 권위 감사(anti-entropy) — 카드 API 사각지대를 구조적으로 메운다.

## 왜 필요한가

Change Set B는 삭제 확인을 **상세 API**로만 했다(그게 유일한 권위다). 그런데
확인 대상은 이미 `suspected_deleted`인 행뿐이었고, 의심으로 들어가는 문은
스윕의 카드 조회 실패 하나였다. 카드는 `seedMediaId=videoId`로 부르고 **원본
VOD는 클립이 삭제돼도 남는다.** 그래서 카드가 계속 정상 응답하는 삭제 클립은
영원히 확인받지 못했다 — 실측 2026-07-31, 79xM38ged7은 상세 API가 404
("삭제된 클립입니다")인데 스윕 2786/2786 완주 후에도 `active=1`이었다.

특정 UID를 예외 처리하는 것은 답이 아니다. 같은 구조의 누락이 앞으로도 계속
생긴다. 그래서 **카드 상태와 무관하게 모든 활성 클립이 결국 상세 API 검사를
한 번씩 받도록** 저속 전체 순회를 둔다.

## 구조 (2 lane)

- **Hot lane** — 삭제 가능성이 높다는 *힌트*가 붙은 클립. 힌트는 우선순위일
  뿐 **삭제 근거가 아니다**: 새 형제 클립 등장, 대표 교체로 밀려난 옛 대표,
  카드 지표 결측, 지표 장기 고정. 힌트만으로 상태를 바꾸지 않는다.
- **Cold lane** — `confirmed_deleted`가 아닌 모든 활성 클립을 목표 커버리지
  시간 안에 한 바퀴. 한 번도 검사받지 않은 행이 먼저다.

판정 규칙은 Change Set B 그대로다(서로 다른 시점의 명시적 404 2회). 이 모듈은
**대상 선정만 바꾼다** — 판정을 완화하면 오탐이 곧바로 순위 왜곡이 된다.

## 영속성

진행 상태를 메모리에 두지 않는다. `audit_next_at`이 곧 커서다: 처리한 행은
미래로 밀리므로 다음 질의에서 자동으로 빠지고, 재시작하면 그 시점부터 이어진다.
별도 진행률 표나 offset 페이지네이션이 없는 게 정상이다(offset은 행이 밀릴 때
건너뛰기·중복을 만든다).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import singcup_clips as sc

from database import get_db
from utils.token_bucket import TokenBucket

KST = timezone(timedelta(hours=9))

# ── 기능 플래그 ─────────────────────────────────────────────────────────────
# 기본값은 OFF다. 켜더라도 SHADOW가 기본이라 상태를 바꾸지 않고 후보 선정과
# 판정까지만 돈다(Phase 1). kill switch는 ENABLED 하나로 충분하다 —
# 공용 SINGCUP_ENABLED를 내리면 수집까지 멈춘다.
def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "")


def enabled() -> bool:
    return _flag("SINGCUP_DELETION_RECONCILE_ENABLED", "false")


def shadow() -> bool:
    return _flag("SINGCUP_DELETION_RECONCILE_SHADOW", "true")


def hot_enabled() -> bool:
    """**Hot lane을 실행할지**만 정한다(상태 변경 여부는 SHADOW가 정한다).

    예전에는 이 값이 '상태 변경 허용'을 뜻해서, 둘 다 false인데도 두 레인이
    상세 API를 호출했다. 이름과 동작이 어긋나면 Phase 1을 켜는 순간 예상보다
    많은 요청이 나간다.
    """
    return _flag("SINGCUP_DELETION_HOT_ENABLED", "false")


def cold_enabled() -> bool:
    """**Cold lane(전체 순회)을 실행할지**만 정한다."""
    return _flag("SINGCUP_DELETION_COLD_ENABLED", "false")


def circuit_enabled() -> bool:
    return _flag("SINGCUP_DELETION_CIRCUIT_ENABLED", "true")


# 목표 커버리지: 이 시간 안에 모든 활성 클립을 한 번씩 본다.
COVERAGE_HOURS = float(os.getenv("SINGCUP_DELETION_COVERAGE_HOURS", "12"))
# Cold lane 초당 상한. 6,358건 / 12시간 = 0.147건/초이므로 0.2면 여유가 있다.
COLD_RATE_CAP = float(os.getenv("SINGCUP_DELETION_COLD_RATE", "0.2"))
# Hot lane은 대기열이 있을 때만 이 속도까지 낸다.
HOT_RATE_CAP = float(os.getenv("SINGCUP_DELETION_HOT_RATE", "1.0"))
# 회로가 열려도 Hot lane이 무제한 우회하지 못하게 하는 최저 속도.
CIRCUIT_HOT_RATE = float(os.getenv("SINGCUP_DELETION_CIRCUIT_HOT_RATE", "0.05"))
MIN_RATE = float(os.getenv("SINGCUP_DELETION_MIN_RATE", "0.02"))

# 한 사이클에 처리할 최대 건수(사이클은 연속으로 이어진다).
BATCH = int(os.getenv("SINGCUP_DELETION_AUDIT_BATCH", "25"))
LOOP_SLEEP_SECONDS = float(os.getenv("SINGCUP_DELETION_AUDIT_SLEEP", "5"))
# 힌트는 영원히 남지 않는다. 한 번 검사받으면 지워지고, 검사 못 받은 채
# 이 시간이 지나면 만료된다(Hot lane이 무한히 부풀지 않게).
HINT_TTL_SECONDS = int(float(os.getenv("SINGCUP_DELETION_HINT_TTL_HOURS", "6")) * 3600)
# 지표가 이만큼 연속으로 완전히 고정되면 힌트를 준다(삭제 근거 아님).
FROZEN_HINT_THRESHOLD = int(os.getenv("SINGCUP_DELETION_FROZEN_ROUNDS", "6"))
# 일시 오류 백오프
BACKOFF_BASE_SECONDS = float(os.getenv("SINGCUP_DELETION_BACKOFF_BASE", "300"))
BACKOFF_MAX_SECONDS = float(os.getenv("SINGCUP_DELETION_BACKOFF_MAX", "21600"))

AUDIT_LOCK_TTL = int(os.getenv("SINGCUP_DELETION_AUDIT_LOCK_TTL", "300"))
AUDIT_LOCK_NAME = "singcup_audit"

V_ALIVE, V_DELETED, V_INCONCLUSIVE = "alive", "deleted", "inconclusive"

# 힌트 사유 — 자유 문자열이 아니다. 로그·지표 카디널리티를 묶어 둔다.
HINT_NEW_SIBLING = "new_sibling"
HINT_REP_CHANGED = "rep_changed"
HINT_CARD_EMPTY = "card_empty"
HINT_CARD_FAILED = "card_failed"
HINT_METRICS_FROZEN = "metrics_frozen"
HINT_MANUAL = "manual"
HINTS = (HINT_NEW_SIBLING, HINT_REP_CHANGED, HINT_CARD_EMPTY, HINT_CARD_FAILED,
         HINT_METRICS_FROZEN, HINT_MANUAL)


def _log(payload: dict):
    print(f"[singcup_audit] {json.dumps(payload, ensure_ascii=False, default=str)}",
          flush=True)


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(int(ts), KST).isoformat() if ts else None


# ── 결정적 jitter ───────────────────────────────────────────────────────────
def stable_offset(clip_uid: str, period: int) -> int:
    """clip_uid로만 정해지는 0..period-1 오프셋.

    파이썬 내장 `hash()`는 PYTHONHASHSEED 때문에 프로세스마다 값이 달라진다 —
    그걸 쓰면 재시작할 때마다 순서가 뒤집혀 어떤 행은 계속 앞, 어떤 행은 계속
    뒤로 가는 편향이 생긴다. SHA-256은 재시작·replica와 무관하게 같다.
    """
    if period <= 0:
        return 0
    digest = hashlib.sha256(clip_uid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % period


def next_cold_at(clip_uid: str, now: int, coverage_seconds: int) -> int:
    """다음 정기 검사 시각 — 커버리지 주기 + 결정적 분산.

    now + period 로만 잡으면 지금 한 바퀴 돈 순서 그대로 다음 바퀴가 몰려 온다.
    특히 재시작 직후 대량 선택이 그대로 재현된다. uid 해시로 최대 한 주기까지
    흩뿌려 두면 어느 시점에도 대상 수가 평평해진다.
    """
    period = max(60, int(coverage_seconds))
    return now + period // 2 + stable_offset(clip_uid, period)


def backoff_seconds(fail_count: int) -> float:
    """일시 오류 지수 백오프(상한 있음)."""
    n = max(1, int(fail_count))
    return min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** (n - 1)))


def required_rate(total: int, coverage_hours: float = COVERAGE_HOURS) -> float:
    """전체를 목표 시간 안에 한 바퀴 돌기 위한 초당 처리량."""
    if total <= 0:
        return MIN_RATE
    return total / max(1.0, coverage_hours * 3600.0)


def effective_cold_rate(total: int, cap: float | None = None,
                        coverage_hours: float | None = None,
                        floor: float | None = None) -> float:
    """필요 속도를 바닥과 상한 사이로 자른다. **0이 되지 않는다.**

    상한이 마지막에 온다 — 바닥을 상한보다 크게 설정해도 상한을 넘지 않는다.
    상한을 근거 없이 올리지 않기 위한 것이므로 순서가 중요하다.
    """
    cap = COLD_RATE_CAP if cap is None else cap
    coverage_hours = COVERAGE_HOURS if coverage_hours is None else coverage_hours
    floor = MIN_RATE if floor is None else floor
    return max(1e-6, min(cap, max(floor, required_rate(total, coverage_hours))))


# ── 회로 차단기 ─────────────────────────────────────────────────────────────
class Circuit:
    """429/5xx가 몰리면 Cold lane을 멈춘다.

    프로세스 재시작 후 상태를 유지할 필요는 없지만, **재시작 직후 버스트는
    안 된다** — 그래서 열림 여부와 무관하게 토큰 버킷이 항상 앞단에 있고,
    시작 시 토큰은 1개뿐이다(버스트 용량을 쌓아 두지 않는다).
    """

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, *, threshold: int | None = None,
                 window_seconds: float | None = None,
                 cooldown_seconds: float | None = None,
                 probes: int | None = None):
        self.threshold = threshold if threshold is not None else int(
            os.getenv("SINGCUP_DELETION_CIRCUIT_THRESHOLD", "5"))
        self.window = window_seconds if window_seconds is not None else float(
            os.getenv("SINGCUP_DELETION_CIRCUIT_WINDOW", "120"))
        self.cooldown = cooldown_seconds if cooldown_seconds is not None else float(
            os.getenv("SINGCUP_DELETION_CIRCUIT_COOLDOWN", "300"))
        self.probes = probes if probes is not None else int(
            os.getenv("SINGCUP_DELETION_CIRCUIT_PROBES", "3"))
        self.state = self.CLOSED
        self.opened_count = 0
        self._events: list[float] = []
        self._opened_at = 0.0
        self._probe_ok = 0

    def _prune(self, now: float):
        cutoff = now - self.window
        self._events = [t for t in self._events if t >= cutoff]

    def record_failure(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        if self.state == self.HALF_OPEN:
            self._open(now)
            return
        self._events.append(now)
        self._prune(now)
        if len(self._events) >= self.threshold:
            self._open(now)

    def _open(self, now: float):
        if self.state != self.OPEN:
            self.opened_count += 1
            _log({"event": "audit_circuit_open", "level": "warning",
                  "failures": len(self._events), "window_seconds": self.window})
        self.state = self.OPEN
        self._opened_at = now
        self._probe_ok = 0
        self._events = []

    def record_success(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        if self.state == self.HALF_OPEN:
            self._probe_ok += 1
            if self._probe_ok >= self.probes:
                self.state = self.CLOSED
                self._events = []
                _log({"event": "audit_circuit_close"})
        elif self.state == self.CLOSED:
            self._prune(now)

    def allow_cold(self, now: float | None = None) -> bool:
        return self.current_state(now) == self.CLOSED

    def current_state(self, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        if self.state == self.OPEN and now - self._opened_at >= self.cooldown:
            self.state = self.HALF_OPEN
            self._probe_ok = 0
        return self.state


_circuit = Circuit()

# 관측 지표 — 클립별 시계열은 두지 않는다(카디널리티가 클립 수만큼 늘어난다).
_stats: dict = {
    "authoritative_checks": 0, "alive": 0, "suspected": 0, "confirmed": 0,
    "recovered": 0, "inconclusive": 0, "shadow_deleted": 0,
    "http_404": 0, "http_410": 0, "http_429": 0, "http_5xx": 0, "timeout": 0,
    "hot_processed": 0, "cold_processed": 0, "skipped_locked": 0,
    "db_lock_giveup": 0,
    "last_progress_at": 0, "cycles": 0,
}
_latency_ms: list[float] = []
LATENCY_SAMPLES = 200


def _record_latency(ms: float):
    _latency_ms.append(ms)
    if len(_latency_ms) > LATENCY_SAMPLES:
        del _latency_ms[:len(_latency_ms) - LATENCY_SAMPLES]


def _pct(values: list[float], q: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return int(s[min(len(s) - 1, int(len(s) * q))])


# ── 힌트(우선순위 상승) ─────────────────────────────────────────────────────
# 힌트는 **삭제 근거가 아니다.** 상태를 바꾸지 않고 audit_hint/audit_hint_at만
# 채운다. 이 구분을 흐리면 "새 클립을 올렸더니 예전 클립이 사라졌다"가 된다.
#
# 두 가지를 반드시 지킨다.
#  1) **master OFF면 DB를 건드리지 않는다.** 예전에는 enabled() 검사가
#     run_audit_cycle에만 있어서, 기능이 꺼진 상태에서도 신규 클립·대표 변경·
#     카드 결측마다 UPDATE+COMMIT이 나갔다(운영 로그의 audit_hint_siblings).
#     꺼진 기능이 다른 작업의 쓰기 잠금 경합에 끼어들 이유가 없다. 힌트를
#     건너뛰어도 Cold lane이 결국 모든 활성 클립을 검사하므로 영구 누락은 없다.
#  2) **같은 힌트를 다시 쓰지 않는다.** 먼저 읽어 보고 바뀔 것이 있을 때만 쓴다.
#     카드가 계속 실패하거나 지표가 계속 고정된 클립은 회차마다 같은 힌트를
#     다시 걸어, 쓸모없는 UPDATE+COMMIT이 회차당 수백~수천 건이 된다.
async def hint_clip(clip_uid: str, reason: str, now: int | None = None) -> bool:
    if reason not in HINTS:
        raise ValueError(f"unknown hint reason: {reason}")
    if not enabled():
        return False
    now = int(time.time()) if now is None else now
    db = await get_db()
    cur = await (await db.execute(
        "SELECT audit_hint, audit_hint_at FROM singcup_clips "
        "WHERE clip_uid=? AND active=1 AND deletion_state<>?",
        (clip_uid, sc.DEL_CONFIRMED))).fetchone()
    if cur is None:
        return False
    if (cur["audit_hint"] == reason
            and int(cur["audit_hint_at"] or 0) > hint_cutoff(now)):
        return False                      # 이미 같은 힌트가 살아 있다 — 쓰지 않는다
    hit = {"n": 0}

    async def _work(db):
        c = await db.execute(
            "UPDATE singcup_clips SET audit_hint=?, audit_hint_at=?, "
            # 힌트가 붙으면 백오프를 풀어 준다 — 힌트는 '지금 보라'는 뜻이다.
            "audit_next_at=0, row_updated_at=? "
            "WHERE clip_uid=? AND active=1 AND deletion_state<>?",
            (reason, now, now, clip_uid, sc.DEL_CONFIRMED))
        hit["n"] = c.rowcount

    if not await sc.db_write(get_db, _work, what="audit_hint", log=_log):
        return False
    return bool(hit["n"])


async def hint_owner_siblings(owner_channel_id: str, *, exclude_uid: str,
                              reason: str = HINT_NEW_SIBLING,
                              now: int | None = None) -> int:
    """새 클립이 등록된 소유자의 **기존** 활성 클립을 Hot lane에 예약한다.

    삭제하고 다시 올리는 흐름이 흔해서 신호가 세다. 그렇다고 비활성화하면
    정상 클립을 여러 개 올린 스트리머가 통째로 지워진다 — 검사 예약만 한다.
    """
    if not enabled():
        return 0
    now = int(time.time()) if now is None else now
    db = await get_db()
    # 쓸 것이 있는지 먼저 읽는다. 없으면 쓰기 트랜잭션 자체를 열지 않는다.
    pending = await (await db.execute(
        "SELECT COUNT(*) FROM singcup_clips "
        "WHERE event_id=? AND owner_channel_id=? AND clip_uid<>? "
        "  AND active=1 AND deletion_state<>? AND audit_hint_at=0",
        (sc.EVENT_ID, owner_channel_id, exclude_uid, sc.DEL_CONFIRMED))).fetchone()
    if not int(pending[0] or 0):
        return 0
    hit = {"n": 0}

    async def _work(db):
        cur = await db.execute(
            "UPDATE singcup_clips SET audit_hint=?, audit_hint_at=?, "
            "audit_next_at=0, row_updated_at=? "
            "WHERE event_id=? AND owner_channel_id=? AND clip_uid<>? "
            "  AND active=1 AND deletion_state<>? AND audit_hint_at=0",
            (reason, now, now, sc.EVENT_ID, owner_channel_id, exclude_uid,
             sc.DEL_CONFIRMED))
        hit["n"] = cur.rowcount

    if not await sc.db_write(get_db, _work, what="audit_hint_siblings", log=_log):
        return 0
    if hit["n"]:
        _log({"event": "audit_hint_siblings", "owner_channel_id": owner_channel_id,
              "reason": reason, "clips": hit["n"]})
    return int(hit["n"])


async def note_metrics_frozen(clip_uid: str, rounds: int,
                              now: int | None = None) -> bool:
    """지표가 연속 고정된 클립에 힌트를 준다. 임계 미만이면 아무것도 안 한다.

    **임계를 넘는 순간에만** 건다. `>=`로 두면 오래 고정된 클립이 회차마다 다시
    힌트를 받아 같은 값을 계속 쓰게 된다(인기 없는 정상 클립이 대부분이라 그 수가
    수천 건이 된다). hint_clip의 읽기-우선 검사가 한 겹 더 막지만, 여기서 끊는
    편이 읽기 한 번조차 나가지 않는다.
    """
    if rounds != FROZEN_HINT_THRESHOLD:
        return False
    return await hint_clip(clip_uid, HINT_METRICS_FROZEN, now)


# ── 대상 선정 ───────────────────────────────────────────────────────────────
# 우선순위. 낮을수록 먼저다. `audit_next_at`이 커서라 처리한 행은 다음 질의에서
# 자동으로 빠진다 — offset 페이지네이션을 쓰지 않는 이유다(행이 밀리면 offset은
# 건너뛰기와 중복을 동시에 만든다).
_PRIORITY_SQL = """
    CASE
        -- 0) 이미 404를 한 번 받은 의심 클립 — 확정까지 한 걸음
        WHEN c.deletion_state = 'suspected_deleted'
             AND c.missing_scan_count > 0                       THEN 0
        -- 1) 나머지 의심 클립
        WHEN c.deletion_state = 'suspected_deleted'             THEN 1
        -- 2) Hot 힌트(새 형제 클립·대표 교체·카드 결측·지표 고정).
        --    검사받지 못한 채 TTL이 지난 힌트는 만료시킨다 — 그러지 않으면
        --    Hot lane이 계속 부풀어 Cold lane이 굶는다.
        WHEN c.audit_hint_at > :hint_cutoff                     THEN 2
        -- 3) 기능 도입 전부터 active=0이던 행(분류만)
        WHEN c.deletion_state = 'unknown_legacy'                THEN 3
        -- 4) 권위 검사를 **한 번도** 받지 않은 활성 클립
        WHEN c.audit_last_at = 0                                THEN 4
        -- 5) 정기 순회(가장 오래 안 본 것부터)
        ELSE                                                         5
    END
"""

# Hot / Cold를 **따로** 뽑는다. 하나의 질의에 LIMIT을 걸면 Hot이 그 창을 다
# 채우는 동안 Cold는 한 건도 뽑히지 않는다 — 초기 Hot 큐가 377건이라 실제로
# 일어난다. 레인마다 슬롯을 나눠 두면 어느 쪽도 굶지 않는다.
_LANE_HOT = ("(c.deletion_state = 'suspected_deleted' "
             " OR c.audit_hint_at > :hint_cutoff)")
_LANE_COLD = ("(c.deletion_state <> 'suspected_deleted' "
              " AND c.audit_hint_at <= :hint_cutoff)")


def _target_sql(lane_filter: str) -> str:
    return f"""
SELECT c.clip_uid, c.owner_channel_id, c.deletion_state, c.deletion_last_at,
       c.missing_scan_count, c.deletion_reason, c.audit_last_at, c.audit_next_at,
       c.audit_fail_count, c.audit_hint, c.audit_hint_at, c.active,
       {_PRIORITY_SQL} AS prio
FROM singcup_clips c
WHERE c.event_id = :event_id
  AND c.deletion_state <> 'confirmed_deleted'
  AND (c.active = 1 OR c.deletion_state = 'unknown_legacy')
  AND c.audit_next_at <= :now
  AND {lane_filter}
ORDER BY prio ASC, c.audit_next_at ASC, c.audit_last_at ASC, c.clip_uid ASC
LIMIT :limit
"""


_HOT_SQL = _target_sql(_LANE_HOT)
_COLD_SQL = _target_sql(_LANE_COLD)
# 한 사이클에서 Cold에 반드시 남겨 두는 슬롯 비율. 0이면 Hot이 전부 가져간다.
COLD_RESERVE_RATIO = float(os.getenv("SINGCUP_DELETION_COLD_RESERVE", "0.2"))


def hint_cutoff(now: int) -> int:
    """이 시각보다 오래된 힌트는 만료로 본다."""
    return now - HINT_TTL_SECONDS


def is_hot(row, now: int | None = None) -> bool:
    """Hot lane 판정 — 의심 상태이거나 **만료되지 않은** 힌트가 붙은 행."""
    now = int(time.time()) if now is None else now
    return (row["deletion_state"] == sc.DEL_SUSPECTED
            or int(row["audit_hint_at"] or 0) > hint_cutoff(now))


async def _lane_targets(sql: str, lane: str, now: int, limit: int) -> list[dict]:
    if limit <= 0:
        return []
    db = await get_db()
    rows = await (await db.execute(
        sql, {"event_id": sc.EVENT_ID, "now": now, "limit": limit,
              "hint_cutoff": hint_cutoff(now)})).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 의심 클립은 최소 간격 전에는 두 번째로 세지 않는다. 대상에서 빼는 게
        # 아니라 여기서 걸러 요청 자체를 아낀다.
        if d["deletion_state"] == sc.DEL_SUSPECTED:
            last = int(d["deletion_last_at"] or 0)
            if last and now - last < sc.DELETION_MIN_INTERVAL_SECONDS:
                continue
        d["lane"] = lane
        out.append(d)
    return out


async def select_targets(now: int, limit: int, *, hot: bool = True,
                         cold: bool = True) -> list[dict]:
    """Hot 우선, 단 Cold 슬롯을 남겨 둔다. Hot이 비면 그 몫도 Cold가 쓴다."""
    reserve = (max(1, int(limit * COLD_RESERVE_RATIO))
               if (limit > 1 and cold) else 0)
    hot_rows = (await _lane_targets(_HOT_SQL, "hot", now, limit - reserve)
                if hot else [])
    cold_rows = (await _lane_targets(_COLD_SQL, "cold", now, limit - len(hot_rows))
                 if cold else [])
    return hot_rows + cold_rows


# ── 판정 적용 ───────────────────────────────────────────────────────────────
async def _schedule_next(clip_uid: str, now: int, *, verdict: str,
                         fail_count: int, clear_hint: bool,
                         next_at: int) -> bool:
    async def _work(db):
        await db.execute(
            "UPDATE singcup_clips SET audit_last_at=?, audit_next_at=?, "
            "audit_verdict=?, audit_fail_count=?, "
            "audit_hint=CASE WHEN ?=1 THEN '' ELSE audit_hint END, "
            "audit_hint_at=CASE WHEN ?=1 THEN 0 ELSE audit_hint_at END, "
            "row_updated_at=? WHERE clip_uid=?",
            (now, next_at, verdict, fail_count,
             1 if clear_hint else 0, 1 if clear_hint else 0, now, clip_uid))

    ok = await sc.db_write(get_db, _work, what="audit_schedule", log=_log)
    if not ok:
        _stats["db_lock_giveup"] += 1
    return ok


def _coverage_seconds() -> int:
    return int(max(1.0, COVERAGE_HOURS) * 3600)


async def apply_verdict(row: dict, verdict: str, code: int | None, why: str,
                        now: int, *, allow_state_change: bool) -> str:
    """판정 하나를 반영한다. 반환은 결과 라벨(로그·지표용).

    `allow_state_change=False`(Shadow)이면 **어떤 상태도 바꾸지 않고** 감사
    스케줄만 갱신한다. 그래서 Phase 1에서 후보 선정과 실제 404 관측을 그대로
    측정할 수 있다.
    """
    uid = row["clip_uid"]
    if verdict == V_ALIVE:
        outcome = "alive"
        if allow_state_change and row["deletion_state"] == sc.DEL_SUSPECTED:
            if await sc._deletion_clear(row, now):
                outcome = "recovered"
                _stats["recovered"] += 1
        elif allow_state_change and row["deletion_state"] == sc.DEL_UNKNOWN_LEGACY:
            # 살아 있는 것은 확인됐지만 **되살리지 않는다** — 왜 내려갔는지 모르고,
            # 자동 복구는 사람의 판단을 조용히 뒤집는다.
            await sc._touch_legacy_check(uid, now)
        _stats["alive"] += 1
        await _schedule_next(uid, now, verdict=V_ALIVE, fail_count=0,
                             clear_hint=True,
                             next_at=next_cold_at(uid, now, _coverage_seconds()))
        return outcome

    if verdict == V_DELETED:
        _stats["http_404"] += 1
        if not allow_state_change:
            _stats["shadow_deleted"] += 1
            _log({"event": "audit_shadow_deleted", "level": "warning",
                  "clip_uid": uid, "owner_channel_id": row.get("owner_channel_id"),
                  "http_status": code, "reason": why, "lane": row.get("lane"),
                  "hint": row.get("audit_hint") or None,
                  "state": row.get("deletion_state")})
            # Shadow에서도 재확인은 정상 간격으로 계속한다(같은 행을 매 사이클
            # 다시 부르지 않기 위해). 상태는 그대로다.
            await _schedule_next(uid, now, verdict=V_DELETED, fail_count=0,
                                 clear_hint=False,
                                 next_at=now + sc.DELETION_MIN_INTERVAL_SECONDS)
            return "shadow_deleted"
        confirmed = await sc._deletion_confirm_step(row, now, why)
        # 확정이면 스윕·감사 대상에서 빠지므로 먼 미래로 밀어도 무해하다.
        await _schedule_next(
            uid, now, verdict=V_DELETED, fail_count=0, clear_hint=True,
            next_at=(now + int(sc.DELETION_RECHECK_HOURS * 3600) if confirmed
                     else now + sc.DELETION_MIN_INTERVAL_SECONDS))
        if confirmed:
            _stats["confirmed"] += 1
            return "confirmed"
        _stats["suspected"] += 1
        return "suspected"

    # inconclusive — 429/5xx/timeout/형식 오류. **삭제 확인 횟수에 넣지 않는다.**
    _stats["inconclusive"] += 1
    if code == 429:
        _stats["http_429"] += 1
    elif code == 410:
        # 이 API에서 관측된 적이 없다. 별도로만 센다 — 삭제 근거가 아니다.
        _stats["http_410"] += 1
    elif code is not None and 500 <= code < 600:
        _stats["http_5xx"] += 1
    elif code is None:
        _stats["timeout"] += 1
    fails = int(row["audit_fail_count"] or 0) + 1
    await _schedule_next(uid, now, verdict=V_INCONCLUSIVE, fail_count=fails,
                         clear_hint=False,
                         next_at=now + int(backoff_seconds(fails)))
    return "inconclusive"


# ── 사이클 ──────────────────────────────────────────────────────────────────
async def _active_total() -> int:
    db = await get_db()
    row = await (await db.execute(
        "SELECT COUNT(*) FROM singcup_clips WHERE event_id=? AND active=1 "
        "AND deletion_state<>?", (sc.EVENT_ID, sc.DEL_CONFIRMED))).fetchone()
    return int(row[0] or 0)


# 상세 API(api.chzzk.naver.com)로 나가는 **모든** 요청은 이 공용 버킷을 먼저
# 통과한다. 레인마다 버킷만 두면 합산이 상한을 넘는다(Hot 1.0 + Cold 0.2 = 1.2/s).
# 예산은 하나, 그 아래에서 Cold만 추가로 느리게 흐른다 — Hot이 먼저 뽑히므로
# 자연스럽게 Hot 우선 할당이 되고, 남은 예산을 Cold가 쓴다.
TOTAL_RATE_CAP = float(os.getenv("SINGCUP_DELETION_TOTAL_RATE",
                                 str(HOT_RATE_CAP)))
_total_bucket = TokenBucket(TOTAL_RATE_CAP, TOTAL_RATE_CAP, MIN_RATE,
                            name="audit_total", on_log=_log)
_hot_bucket = TokenBucket(HOT_RATE_CAP, HOT_RATE_CAP, MIN_RATE,
                          name="audit_hot", on_log=_log)
_cold_bucket = TokenBucket(COLD_RATE_CAP, COLD_RATE_CAP, MIN_RATE,
                           name="audit_cold", on_log=_log)


async def run_audit_cycle(limit: int | None = None, *,
                          client=None, now: int | None = None) -> dict:
    """한 사이클. 대상이 없으면 외부 요청이 한 건도 나가지 않는다."""
    if not enabled():
        return {"status": "disabled", "checked": 0}
    now = int(time.time()) if now is None else now
    total = await _active_total()
    # 대상 수가 변하면 필요 속도도 변한다. 매 사이클 다시 계산한다 —
    # 고정 숫자를 박아 두면 참가자가 늘 때 커버리지 목표가 조용히 깨진다.
    _cold_bucket.cap = COLD_RATE_CAP
    _cold_bucket.rate = effective_cold_rate(total)

    # 레인 실행 여부는 여기서 정한다. 꺼진 레인은 **대상 조회조차 하지 않는다** —
    # 뽑아 놓고 건너뛰면 그만큼 쓸모없는 DB 읽기가 남는다.
    targets = await select_targets(now, limit or BATCH,
                                   hot=hot_enabled(), cold=cold_enabled())
    if not targets:
        return {"status": "idle", "checked": 0, "total_active": total}

    client = client or sc._get_client()
    tally: dict[str, int] = {}
    changed = 0
    for row in targets:
        hot = row["lane"] == "hot"
        if not hot and circuit_enabled() and not _circuit.allow_cold():
            # 회로가 열려 있으면 Cold lane은 쉰다. 삭제로 판정하지 않는다.
            continue
        bucket = _hot_bucket if hot else _cold_bucket
        if hot and circuit_enabled() and _circuit.current_state() != Circuit.CLOSED:
            # Hot lane도 무제한 우회하지 못한다.
            bucket.rate = min(bucket.rate, CIRCUIT_HOT_RATE)
        # 공용 예산 → 레인 예산 순서. 두 레인이 같은 회로 차단기와 같은 상한을
        # 공유하므로 합산 요청량이 TOTAL_RATE_CAP을 넘지 않는다.
        await _total_bucket.acquire()
        await bucket.acquire()

        token = await sc.acquire_clip_lock(row["clip_uid"])
        if token is None:
            _stats["skipped_locked"] += 1
            continue
        try:
            t0 = time.monotonic()
            verdict, code, why = await probe_deleted(client, row["clip_uid"])
            _record_latency((time.monotonic() - t0) * 1000.0)
            _stats["authoritative_checks"] += 1
            if verdict == V_INCONCLUSIVE and (code == 429 or (code or 0) >= 500
                                              or code is None):
                _circuit.record_failure()
                bucket.slow_down(why)
                _total_bucket.slow_down(why)
            else:
                _circuit.record_success()
                bucket.recover()
                _total_bucket.recover()
            # 상태 변경 여부는 SHADOW **하나만** 정한다.
            outcome = await apply_verdict(row, verdict, code, why, now,
                                          allow_state_change=not shadow())
        except Exception as e:                      # 한 클립 실패를 격리한다
            outcome = "error"
            _log({"event": "audit_clip_error", "level": "warning",
                  "clip_uid": row["clip_uid"], "detail": str(e)[:160]})
        finally:
            await sc.release_clip_lock(row["clip_uid"], token)
        tally[outcome] = tally.get(outcome, 0) + 1
        _stats["hot_processed" if hot else "cold_processed"] += 1
        if outcome in ("confirmed", "recovered"):
            changed += 1

    _stats["cycles"] += 1
    _stats["last_progress_at"] = now
    if changed:
        # 대표·점수·순위·캐시·Split 스냅샷을 **정상 경로로** 다시 만든다.
        await sc.recompute_ranking(int(time.time()), client=client)
    return {"status": "ok", "checked": len(targets), "total_active": total,
            "outcomes": tally, "circuit": _circuit.current_state(),
            "cold_rate": round(_cold_bucket.rate, 4)}


# ── 상세 API 판정 ───────────────────────────────────────────────────────────
_ALLOWED_HOST = "api.chzzk.naver.com"
# 클립 UID 형식 — 치지직은 영숫자 10자다. 임의 문자열을 받아 경로에 넣으면
# 경로 조작이나 다른 엔드포인트 호출이 된다.
_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def valid_clip_uid(clip_uid: str) -> bool:
    return bool(clip_uid) and bool(_UID_RE.match(clip_uid))


async def probe_deleted(client, clip_uid: str) -> tuple[str, int | None, str]:
    """(verdict, http_status, reason). 삭제는 **의미가 확인된 404**만 인정한다.

    `sc.probe_clip_alive`를 감싸 두 가지를 더 본다.
      1) uid 형식 검증 — 임의 문자열로 경로를 만들지 않는다.
      2) 호출 대상이 치지직 고정 호스트·경로인지 — 프록시나 라우팅 오류가 만든
         404를 삭제 근거로 쓰면 멀쩡한 클립이 통째로 사라진다.
    """
    if not valid_clip_uid(clip_uid):
        return (V_INCONCLUSIVE, None, "bad_uid")
    if _ALLOWED_HOST not in sc.CLIP_DETAIL_API:
        return (V_INCONCLUSIVE, None, "bad_endpoint")
    verdict, code, why = await sc.probe_clip_alive(client, clip_uid)
    if verdict == "deleted":
        return (V_DELETED, code, why)
    if verdict == "alive":
        return (V_ALIVE, code, why)
    return (V_INCONCLUSIVE, code, why)


# ── 관측 ────────────────────────────────────────────────────────────────────
async def audit_status() -> dict:
    """공개 상태. **락 owner token이나 secret은 절대 넣지 않는다.**"""
    db = await get_db()
    now = int(time.time())
    agg = await (await db.execute(
        "SELECT COUNT(*) AS total,"
        " SUM(CASE WHEN audit_last_at=0 THEN 1 ELSE 0 END) AS never_audited,"
        " SUM(CASE WHEN audit_next_at<=? THEN 1 ELSE 0 END) AS due,"
        " SUM(CASE WHEN audit_hint_at>0 THEN 1 ELSE 0 END) AS hinted,"
        " SUM(CASE WHEN audit_last_at>=? THEN 1 ELSE 0 END) AS cov1,"
        " SUM(CASE WHEN audit_last_at>=? THEN 1 ELSE 0 END) AS cov6,"
        " SUM(CASE WHEN audit_last_at>=? THEN 1 ELSE 0 END) AS cov12,"
        " SUM(CASE WHEN audit_last_at>=? THEN 1 ELSE 0 END) AS cov24,"
        " MIN(CASE WHEN audit_next_at<=? THEN audit_next_at END) AS oldest_due"
        " FROM singcup_clips WHERE event_id=? AND active=1 AND deletion_state<>?",
        (now, now - 3600, now - 6 * 3600, now - 12 * 3600, now - 24 * 3600, now,
         sc.EVENT_ID, sc.DEL_CONFIRMED))).fetchone()
    states = dict(await (await db.execute(
        "SELECT deletion_state, COUNT(*) FROM singcup_clips WHERE event_id=? "
        "GROUP BY 1", (sc.EVENT_ID,))).fetchall())
    hot_q = await (await db.execute(
        "SELECT COUNT(*) FROM singcup_clips WHERE event_id=? AND active=1 "
        "AND deletion_state<>? AND (deletion_state=? OR audit_hint_at>0) "
        "AND audit_next_at<=?",
        (sc.EVENT_ID, sc.DEL_CONFIRMED, sc.DEL_SUSPECTED, now))).fetchone()

    total = int(agg["total"] or 0)
    cold_rate = effective_cold_rate(total)
    due = int(agg["due"] or 0)
    hot_size = int(hot_q[0] or 0)
    eta = None
    if total:
        eta = _iso(now + int(total / max(cold_rate, 1e-6)))
    last_progress = int(_stats["last_progress_at"] or 0)
    return {
        "enabled": enabled(), "shadow": shadow(),
        # 꺼져 있어도 아래 집계는 **실제 DB를 읽은 값**이다(조회 전용).
        # 읽지 않고 0을 내보내면 "큐가 비었다"로 오해할 수 있어 출처를 함께 밝힌다.
        "status": "running" if enabled() else "disabled",
        "counts_source": "db",
        "hot_enabled": hot_enabled(), "cold_enabled": cold_enabled(),
        "coverage_hours": COVERAGE_HOURS,
        "active_clips": total,
        "hot_queue_size": hot_size,
        "cold_queue_size": max(0, due - hot_size),
        "due_count": due,
        "hinted_count": int(agg["hinted"] or 0),
        "never_audited_count": int(agg["never_audited"] or 0),
        "oldest_due_age_seconds": (now - int(agg["oldest_due"]))
                                  if agg["oldest_due"] else None,
        "coverage_1h": int(agg["cov1"] or 0),
        "coverage_6h": int(agg["cov6"] or 0),
        "coverage_12h": int(agg["cov12"] or 0),
        "coverage_24h": int(agg["cov24"] or 0),
        "deletion_states": {k: int(v) for k, v in states.items()},
        "authoritative_checks": _stats["authoritative_checks"],
        "alive": _stats["alive"], "suspected": _stats["suspected"],
        "confirmed": _stats["confirmed"], "recovered": _stats["recovered"],
        "inconclusive": _stats["inconclusive"],
        "shadow_deleted": _stats["shadow_deleted"],
        "http_404": _stats["http_404"], "http_429": _stats["http_429"],
        "http_5xx": _stats["http_5xx"], "timeout": _stats["timeout"],
        "http_410_unverified": _stats["http_410"],
        "hot_processed": _stats["hot_processed"],
        "cold_processed": _stats["cold_processed"],
        "skipped_locked": _stats["skipped_locked"],
        "db_lock_giveup": _stats["db_lock_giveup"],
        # db_write가 재시도 횟수를 밖으로 내주지 않는다. 0으로 적으면 "재시도가
        # 없었다"로 읽히므로 미계측임을 명시한다.
        "db_lock_retry": None,
        "current_rate": round(_cold_bucket.rate, 4),
        "total_rate": round(_total_bucket.rate, 4),
        "total_rate_cap": TOTAL_RATE_CAP,
        "required_rate": round(required_rate(total), 4),
        "cold_rate_cap": COLD_RATE_CAP,
        "hot_rate": round(_hot_bucket.rate, 4),
        "circuit_state": _circuit.current_state(),
        "circuit_open_count": _circuit.opened_count,
        "p50_ms": _pct(_latency_ms, 0.5), "p95_ms": _pct(_latency_ms, 0.95),
        "cycles": _stats["cycles"],
        "last_progress_at": _iso(last_progress),
        # 진행이 멈춘 것을 밖에서 알 수 있어야 한다(대상이 있는데 안 도는 상태).
        "stalled": bool(enabled() and due and last_progress
                        and now - last_progress > 900),
        "estimated_full_sweep_at": eta,
    }


# ── 워커 ────────────────────────────────────────────────────────────────────
async def start_audit_worker():
    """연속 사이클. 정각에 묶지 않는다 — 회차가 늦어져도 다음이 곧바로 이어진다."""
    if os.getenv("SINGCUP_ENABLED", "true").lower() in ("0", "false", "no"):
        return
    await asyncio.sleep(float(os.getenv("SINGCUP_AUDIT_START_DELAY_SECONDS", "70")))
    while True:
        try:
            if enabled() and sc.event_status() == "LIVE":
                token = await sc.acquire_named_lock(AUDIT_LOCK_NAME, AUDIT_LOCK_TTL)
                if token:
                    try:
                        await run_audit_cycle()
                    finally:
                        await sc.release_named_lock(AUDIT_LOCK_NAME, token)
        except Exception as e:
            # 감사 실패가 스윕·공개 API로 번지지 않게 여기서 끊는다.
            _log({"event": "audit_loop_error", "level": "warning",
                  "detail": str(e)[:200]})
        await asyncio.sleep(LOOP_SLEEP_SECONDS)
