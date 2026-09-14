"""수정 요청 보관 정책 (SUPPORT-POLICY-1, A 균형형 + 보정 2건 · 1b 절대 상한 · 1c 건강 상태 게이트).

정본 — 공개 폼·개인정보처리방침(`web/frontend/lib/supportRetention.ts`)과 **같은 숫자**다.
한쪽만 바꾸면 계약 테스트가 깨진다.

| 대상 | 제거 시점 |
|---|---|
| `dedupe_key` | 접수 후 7일 → `''` |
| `contact_email` | 접수 후 180일, 또는 처리(resolved/rejected) 후 30일 중 **먼저 오는 때** → `''` |
| 행 전체(미처리 received/in_review/알 수 없는 상태) | 접수 후 365일 |
| 행 전체(처리 resolved/rejected) | min(마지막 상태 변경 후 180일, 접수 후 545일) |
| **행 전체(모든 상태 · 최상위 계약)** | **접수 후 545일** |

최상위 계약(1c): **`created_at`이 유효한 요청은 어떤 status·`status_changed_at`을 가져도
접수 후 545일을 넘겨 보관하지 않는다.** 545일은 새 기간이 아니라 기존 두 규칙의
최대 조합(365 + 180)이다.

시각 유효성은 둘로 나눈다.

* `created_at` — `0 < created_at <= now`여야 한다. **이게 잘못된 행은 어떤 규칙으로도
  자동으로 지우지 않는다**(접수 시각을 모르면 보관 기간을 셀 수 없다).
  `invalidCreatedAtCount`로 운영자에게 드러낸다.
* `status_changed_at` — `0 <= status_changed_at <= now`. 잘못되면 **처리 기준 규칙
  (처리 후 180일·처리 후 30일 이메일)만 건너뛴다.** 접수 기준 규칙(545일·365일·
  이메일 180일·dedupe 7일)은 그대로 적용한다. `invalidStatusChangedAtCount`로 드러낸다.

설계 결정:

* **시각은 Unix UTC 초만 쓴다.** KST 날짜 경계·`date.today()`에 기대지 않는다
  (retention-kst-fixture 함정).
* **정확히 기준 시각에 도달하면 제거 대상**이다(`now >= 기준`). 1초 전은 보존.
* 처리 기준 시각은 `MAX(created_at, status_changed_at)` — 구 코드가 넣은 행(`status_changed_at=0`)은
  `created_at`으로 떨어진다.
* 화면용 `schedule()`은 `now`를 받지 않는다. 기준 시각(`x + N일`)은 항상 `x`보다
  뒤라, 그 시각이 되면 `x <= now`도 성립하므로 "미래 시각이라 건너뜀"이 예정일을
  바꾸지 않는다(패리티 테스트로 고정).
* 한 회차는 UPDATE(dedupe) → UPDATE(email) → DELETE 세 문장이고 **각각 원자적**이다. 몇 번을 돌려도,
  두 프로세스가 겹쳐 돌아도 결과가 같다.
* 배포만으로 지우기 시작하면 안 된다 → `SUPPORT_RETENTION_ENABLED=true` **그리고**
  `SUPPORT_RETENTION_DRY_RUN=false`일 때만 실제로 쓴다. 그 밖에는 대상 건수만 센다(write 0).
* 로그에는 **건수만** 남긴다. 본문·이메일·URL·해시는 어떤 경로로도 싣지 않는다.

접수 게이트(1c) — **정리가 실제로 건강하게 돌고 있을 때만** 공개 접수를 연다.

* `intake_ready` = apply 모드 AND 워커 가동 AND `cleanup_healthy`.
* `cleanup_healthy` = 이 프로세스에서 **apply 모드 정리가 성공**한 적이 있고, 그 마지막
  성공이 **26시간(`HEALTH_FRESHNESS_SECONDS`) 이내**.
  26시간 = 정리 주기 24시간 + 기동 지연·실행 지연 여유 2시간.
* dry-run 성공은 성공으로 치지 않는다. 한 문장이라도 실패한 회차도 성공이 아니다.
* 한 번 실패해도 마지막 성공이 신선하면 접수는 유지된다. 실패가 이어져 마지막 성공이
  26시간을 넘기면 닫힌다. `consecutiveFailures`는 관측용이고 폐쇄 기준이 아니다.
* 상태는 프로세스 메모리다 → 재기동하면 첫 성공 전까지 닫힌다(기동 후 약 120초). 워커가 끝나거나
  취소되면 즉시 닫힌다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time

from database import get_db

DAY = 86400
HOUR = 3600

DEDUPE_CLEAR_AFTER = 7 * DAY
EMAIL_MAX_AFTER_CREATED = 180 * DAY
EMAIL_AFTER_CLOSED = 30 * DAY
OPEN_MAX_RETENTION = 365 * DAY
CLOSED_RETENTION = 180 * DAY
#: 요청 행 절대 상한 — 기존 두 규칙의 최대 조합(미처리 365 + 처리 후 180).
ABSOLUTE_MAX_RETENTION = OPEN_MAX_RETENTION + CLOSED_RETENTION

OPEN_STATUSES = ("received", "in_review")
CLOSED_STATUSES = ("resolved", "rejected")

#: 기동 직후 다른 워커·migration과 겹치지 않게 잠시 기다린 뒤 첫 회차를 돈다.
START_DELAY_SECONDS = 120.0
#: 하루 1회. 재배포로 프로세스가 다시 뜨면 그때도 한 번 돈다(멱등이라 무해하다).
INTERVAL_SECONDS = float(DAY)
#: 마지막 실제 정리 성공이 이보다 오래되면 접수를 닫는다(24시간 주기 + 2시간 여유).
HEALTH_FRESHNESS_SECONDS = 26 * HOUR


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _env_bool("SUPPORT_RETENTION_ENABLED", False)


def dry_run_flag() -> bool:
    return _env_bool("SUPPORT_RETENTION_DRY_RUN", True)


def mode() -> str:
    """`apply`(실제 정리) 또는 `dry_run`(건수만). **호출 시점에 읽는다**(테스트·재설정 대비)."""
    return "apply" if enabled() and not dry_run_flag() else "dry_run"


def policy() -> dict:
    """화면이 같은 숫자를 쓰도록 서버 값을 준다(일 단위)."""
    return {"duplicateCheckClearDays": DEDUPE_CLEAR_AFTER // DAY,
            "emailMaxDaysAfterCreated": EMAIL_MAX_AFTER_CREATED // DAY,
            "emailDaysAfterClosed": EMAIL_AFTER_CLOSED // DAY,
            "openMaxDays": OPEN_MAX_RETENTION // DAY,
            "closedDays": CLOSED_RETENTION // DAY,
            "absoluteMaxDays": ABSOLUTE_MAX_RETENTION // DAY,
            "healthFreshnessHours": HEALTH_FRESHNESS_SECONDS // HOUR}


# ── 한 행의 예정 시각(화면 표시용) ────────────────────────────────────────────
def schedule(created_at: int, status_changed_at: int, status: str) -> dict:
    """아래 SQL 조건과 **같은 계산**을 파이썬으로 한다(패리티 테스트로 고정).

    `created_at`이 0 이하면 예정일이 없다(자동 정리 안 함). `status_changed_at`이 음수면
    처리 기준 규칙만 빠지고 접수 기준 규칙(545일 포함)은 남는다.
    """
    c, s = int(created_at or 0), int(status_changed_at or 0)
    if c <= 0:
        return {"valid": False, "createdValid": False, "statusTimeValid": s >= 0,
                "duplicateCheckClearAt": None, "emailRemovalAt": None,
                "deletionAt": None, "deletionCapped": False}
    status_ok = s >= 0
    closed = status in CLOSED_STATUSES
    base = max(c, s)
    cap = c + ABSOLUTE_MAX_RETENTION

    email_at = c + EMAIL_MAX_AFTER_CREATED
    if closed and status_ok:
        email_at = min(email_at, base + EMAIL_AFTER_CLOSED)

    if closed:
        by_rule = base + CLOSED_RETENTION if status_ok else None
    else:
        by_rule = c + OPEN_MAX_RETENTION
    deletion_at = cap if by_rule is None else min(by_rule, cap)
    return {"valid": status_ok, "createdValid": True, "statusTimeValid": status_ok,
            "duplicateCheckClearAt": c + DEDUPE_CLEAR_AFTER,
            "emailRemovalAt": email_at,
            "deletionAt": deletion_at,
            # 일반 규칙보다 접수 후 545일 상한이 먼저 온(또는 상한만 남은) 행
            "deletionCapped": deletion_at == cap and (by_rule is None or cap < by_rule)}


# ── SQL 조건 (dry-run 집계와 실제 정리가 **같은 문자열**을 쓴다) ──────────────
_CLOSED_SQL = "status IN ('resolved', 'rejected')"
_BASE_SQL = "MAX(created_at, status_changed_at)"
_CREATED_OK_SQL = "created_at > 0 AND created_at <= :now"
_STATUS_OK_SQL = "status_changed_at >= 0 AND status_changed_at <= :now"

DEDUPE_DUE_SQL = (f"dedupe_key <> '' AND {_CREATED_OK_SQL}"
                  " AND created_at <= :now - :dedupe_after")
EMAIL_DUE_SQL = (f"contact_email <> '' AND {_CREATED_OK_SQL}"
                 " AND (created_at <= :now - :email_max"
                 f" OR ({_CLOSED_SQL} AND {_STATUS_OK_SQL}"
                 f" AND {_BASE_SQL} <= :now - :email_closed))")
#: 1) 접수 시각 유효 → 2) 545일이면 무조건 → 3) 상태별 일반 규칙
#:    (처리 규칙은 상태 시각이 유효할 때만)
ROW_DUE_SQL = (f"{_CREATED_OK_SQL}"
               " AND (created_at <= :now - :absolute_max"
               f" OR (NOT {_CLOSED_SQL} AND created_at <= :now - :open_max)"
               f" OR ({_CLOSED_SQL} AND {_STATUS_OK_SQL}"
               f" AND {_BASE_SQL} <= :now - :closed_keep))")
INVALID_CREATED_SQL = f"NOT ({_CREATED_OK_SQL})"
INVALID_STATUS_TIME_SQL = f"({_CREATED_OK_SQL}) AND NOT ({_STATUS_OK_SQL})"


def _params(now: int) -> dict:
    return {"now": int(now), "dedupe_after": DEDUPE_CLEAR_AFTER,
            "email_max": EMAIL_MAX_AFTER_CREATED, "email_closed": EMAIL_AFTER_CLOSED,
            "open_max": OPEN_MAX_RETENTION, "closed_keep": CLOSED_RETENTION,
            "absolute_max": ABSOLUTE_MAX_RETENTION}


def _log(payload: dict) -> None:
    """**건수·모드·예외 종류만.** 행 내용은 이 함수로 들어오지 않는다."""
    print(f"[support_retention] {json.dumps(payload, ensure_ascii=False)}", flush=True)


async def _count(db, where: str, params: dict) -> int:
    row = await (await db.execute(
        f"SELECT COUNT(*) FROM correction_requests WHERE {where}", params)).fetchone()
    return int(row[0])


async def _invalid_counts(db, p: dict) -> dict:
    created = await _count(db, INVALID_CREATED_SQL, p)
    status_time = await _count(db, INVALID_STATUS_TIME_SQL, p)
    return {"invalidCreatedAtCount": created, "invalidStatusChangedAtCount": status_time,
            "invalidTimestampCount": created + status_time}


async def candidates(db, *, now: int) -> dict:
    """정리 **후보 건수**(읽기 전용). dry-run·apply와 같은 WHERE 문자열을 쓴다."""
    p = _params(now)
    return {"candidateDuplicateCheckClearCount": await _count(db, DEDUPE_DUE_SQL, p),
            "candidateEmailClearCount": await _count(db, EMAIL_DUE_SQL, p),
            "candidateDeleteCount": await _count(db, ROW_DUE_SQL, p),
            **await _invalid_counts(db, p)}


async def cleanup_once(db, *, now: int, dry_run: bool) -> dict:
    """한 회차. `db`를 받는 이유는 테스트가 **다른 연결(=다른 프로세스)**을 흉내 내기 위해서다.

    각 문장 뒤에 바로 commit한다 — 이 커넥션은 프로세스 전역 공유라, 다른 코루틴의 commit/rollback이
    끼어들 수 있는 긴 트랜잭션을 만들지 않는다. 문장 하나하나가 원자적이고 멱등이라 중간에
    끊겨도 다음 회차가 이어서 끝낸다(끊긴 회차는 성공으로 기록하지 않는다).
    """
    p = _params(now)
    invalid = await _invalid_counts(db, p)
    if dry_run:
        return {"mode": "dry_run", "now": int(now),
                "duplicateChecksCleared": await _count(db, DEDUPE_DUE_SQL, p),
                "emailCleared": await _count(db, EMAIL_DUE_SQL, p),
                "rowsDeleted": await _count(db, ROW_DUE_SQL, p),
                "invalidSkipped": invalid["invalidTimestampCount"], **invalid}
    cur = await db.execute(
        f"UPDATE correction_requests SET dedupe_key = '' WHERE {DEDUPE_DUE_SQL}", p)
    dedupe = cur.rowcount
    await db.commit()
    cur = await db.execute(
        "UPDATE correction_requests SET contact_email = '', contact_email_cleared_at = :now"
        f" WHERE {EMAIL_DUE_SQL}", p)
    email = cur.rowcount
    await db.commit()
    cur = await db.execute(f"DELETE FROM correction_requests WHERE {ROW_DUE_SQL}", p)
    deleted = cur.rowcount
    await db.commit()
    return {"mode": "apply", "now": int(now), "duplicateChecksCleared": max(0, dedupe),
            "emailCleared": max(0, email), "rowsDeleted": max(0, deleted),
            "invalidSkipped": invalid["invalidTimestampCount"], **invalid}


# ── 프로세스 내 실행·건강 상태 (DB에 쓰지 않는다) ─────────────────────────────
_lock = asyncio.Lock()
_last: dict | None = None
_consecutive_failures = 0
#: 이 프로세스에서 **apply 모드** 정리가 마지막으로 성공한 시각. dry-run 성공은 여기에 오지 않는다.
_last_successful_apply_at: int | None = None
#: 이 프로세스에서 정리 워커 task가 살아 있는가.
_worker_running = False
#: 건강 판정용 시계(테스트가 바꾼다).
_clock = time.time


def last_report() -> dict | None:
    return dict(_last) if _last else None


def worker_running() -> bool:
    return _worker_running


def cleanup_healthy(now: float | None = None) -> bool:
    """apply 모드 정리가 성공한 적이 있고, 마지막 성공이 26시간 이내(경계 포함)인가.

    마지막 성공 시각이 현재보다 미래(시계 이상)면 건강하지 않은 것으로 본다(fail-closed).
    """
    if mode() != "apply" or _last_successful_apply_at is None:
        return False
    age = (_clock() if now is None else now) - _last_successful_apply_at
    return 0 <= age <= HEALTH_FRESHNESS_SECONDS


def intake_ready() -> bool:
    """공개 접수를 열어도 되는가(소금 제외) — apply 모드 + 워커 가동 + 건강한 정리."""
    return mode() == "apply" and _worker_running and cleanup_healthy()


def phase() -> str:
    """OWNER 화면용 한 단어 상태. 우선순위대로 첫 번째 사유를 돌려준다."""
    if not enabled():
        return "disabled"
    if dry_run_flag():
        return "dry_run"
    if not _worker_running:
        return "worker_stopped"
    if _last_successful_apply_at is None:
        return "awaiting_first_cleanup"
    if not cleanup_healthy():
        return "stale"
    return "ready"


def status() -> dict:
    """OWNER 화면용 비민감 상태. 요청 본문·이메일·URL·해시·salt는 담지 않는다."""
    last = last_report()
    return {"enabled": enabled(), "dryRun": dry_run_flag(), "mode": mode(),
            "workerRunning": _worker_running,
            "lastRunAt": last["at"] if last else None,
            "lastSuccessfulRunAt": _last_successful_apply_at,
            "consecutiveFailures": _consecutive_failures,
            "cleanupHealthy": cleanup_healthy(),
            "intakeReady": intake_ready(),
            "phase": phase(),
            "lastRun": last}


def reset_state() -> None:
    """테스트용(프로세스 재기동을 흉내 낸다)."""
    global _last, _lock, _consecutive_failures, _worker_running, _last_successful_apply_at
    _last = None
    _consecutive_failures = 0
    _worker_running = False
    _last_successful_apply_at = None
    _lock = asyncio.Lock()


async def run_cleanup(*, now: int | None = None) -> dict:
    """같은 프로세스에서 **겹쳐 돌지 않는다**(두 번째 호출은 건너뛴다).

    다른 프로세스와의 겹침은 SQL 쪽 멱등성이 막는다. **apply 회차가 세 문장 모두 끝났을 때만**
    `_last_successful_apply_at`을 갱신한다.
    """
    global _last, _consecutive_failures, _last_successful_apply_at
    if _lock.locked():
        return {"skipped": "in_progress"}
    async with _lock:
        ts = int(time.time() if now is None else now)
        db = await get_db()
        run_mode = mode()
        try:
            report = await cleanup_once(db, now=ts, dry_run=run_mode != "apply")
        except Exception as e:
            with contextlib.suppress(Exception):
                await db.rollback()
            _consecutive_failures += 1
            _last = {"ok": False, "at": ts, "mode": run_mode, "error": type(e).__name__}
            _log({"event": "cleanup_failed", "mode": run_mode, "error": type(e).__name__})
            raise
        _consecutive_failures = 0
        if report["mode"] == "apply":
            _last_successful_apply_at = ts
        _last = {"ok": True, "at": ts, **report}
        _log({"event": "cleanup", **report})
        return report


async def start_support_retention_worker(*, clock=time.time, sleep=asyncio.sleep,
                                         start_delay: float = START_DELAY_SECONDS,
                                         interval: float = INTERVAL_SECONDS,
                                         max_runs: int | None = None) -> None:
    """기동 후 한 번, 이후 하루마다 한 번.

    실패해도 **곧바로 재시도하지 않는다** — 다음 주기에 다시 본다(무한 재시도·로그 폭주 방지).
    기동을 막지 않도록 lifespan은 이 코루틴을 task로 띄우고, 종료 시 취소한다.
    """
    global _worker_running
    _worker_running = True
    try:
        await sleep(start_delay)
        runs = 0
        while True:
            try:
                await run_cleanup(now=int(clock()))
            except asyncio.CancelledError:
                raise
            except Exception:                 # noqa: BLE001 — 이미 run_cleanup이 종류를 기록했다
                pass
            runs += 1
            if max_runs is not None and runs >= max_runs:
                return
            await sleep(interval)
    finally:
        # 취소·종료되면 접수 준비 상태가 풀린다(접수가 닫힌다).
        _worker_running = False


def launch_worker(**kwargs) -> asyncio.Task:
    """lifespan용. task를 만들기 **전에** 가동 표시를 켠다. task가 끝나면 워커의 finally가 끈다.

    가동 표시만으로는 접수가 열리지 않는다 — 첫 apply 정리 성공(`cleanup_healthy`)이 필요하다.
    """
    global _worker_running
    _worker_running = True
    return asyncio.get_running_loop().create_task(start_support_retention_worker(**kwargs))
