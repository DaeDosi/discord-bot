"""수정 요청 보관 정책 (SUPPORT-POLICY-1, A 균형형 + 보정 2건).

정본 — 공개 폼·개인정보처리방침(`web/frontend/lib/supportRetention.ts`)과 **같은 숫자**다.
한쪽만 바꾸면 계약 테스트가 깨진다.

| 대상 | 제거 시점 |
|---|---|
| `dedupe_key` | 접수 후 7일 → `''` |
| `contact_email` | 접수 후 180일, 또는 처리(resolved/rejected) 후 30일 중 **먼저 오는 때** → `''` |
| 행 전체(미처리 received/in_review) | 접수 후 365일 |
| 행 전체(처리 resolved/rejected) | min(마지막 상태 변경 후 180일, **접수 후 545일**) |

545일 절대 상한(SUPPORT-POLICY-1b)은 새 기간이 아니라 기존 두 규칙의 최대 조합(365 + 180)이다.
처리 상태끼리(resolved↔rejected) 바꾸거나 재오픈·재종료를 반복해도 기준 시각만 옮겨질 뿐
**접수 후 545일을 넘겨 남지 않는다.** 미처리는 365일이라 이미 이 안에 있다.

설계 결정:

* **시각은 Unix UTC 초만 쓴다.** KST 날짜 경계·`date.today()`에 기대면 벽시계 시간대에 따라
  하루씩 흔들린다(retention-kst-fixture와 같은 함정).
* **정확히 기준 시각에 도달하면 제거 대상**이다(`now >= 기준`). 1초 전은 보존.
* **미래 시각·음수 시각인 행은 건드리지 않는다**(fail-closed). 시계가 어긋난 행을 지우는 것보다
  남겨 두고 운영자가 보게 하는 편이 되돌릴 수 있다. 건수만 `invalid`로 보고한다.
* 알 수 없는 status는 **미처리로 본다** — 그래야 접수 후 365일 절대 상한에 걸려 무기한 남지 않는다.
* 처리 기준 시각은 `MAX(created_at, status_changed_at)` — 구 코드가 넣은 행(`status_changed_at=0`)은
  `created_at`으로 떨어진다. migration backfill이 늦어도 계산이 달라지지 않는다.
* 한 회차는 UPDATE(dedupe) → UPDATE(email) → DELETE 세 문장이고 **각각 원자적**이다.
  조건이 전부 "기준 시각이 지났는가"라 몇 번을 돌려도, 두 프로세스가 겹쳐 돌아도 결과가 같다.
  순서를 바꿔도 최종 상태가 같다(DELETE 대상이면 앞선 UPDATE 여부와 무관하게 사라진다).
* 배포만으로 지우기 시작하면 안 된다 → `SUPPORT_RETENTION_ENABLED=true` **그리고**
  `SUPPORT_RETENTION_DRY_RUN=false`일 때만 실제로 쓴다(`singcup_retention`과 같은 이중 관문).
  그 밖에는 대상 건수만 센다(write 0).
* 로그에는 **건수만** 남긴다. 본문·이메일·URL·해시는 어떤 경로로도 싣지 않는다.
* **접수 준비(`intake_ready`)** — 수정 요청 접수는 이 모듈의 정리가 *실제로* 켜져 있고(apply) 워커가
  이 프로세스에서 살아 있을 때만 열린다. 소금만 실수로 설정돼도 접수가 열리지 않게 하는 관문이다.
  **설정 준비**와 **한 회차 실패**는 구분한다: 한 회차가 실패해도 접수는 닫지 않는다
  (워커는 다음 주기에 다시 돌고, 실패는 OWNER 화면의 `lastRun`·`consecutiveFailures`로
  드러난다). 워커 task 자체가 끝나거나 취소되면 준비 상태가 풀려 접수가 닫힌다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time

from database import get_db

DAY = 86400

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
            "absoluteMaxDays": ABSOLUTE_MAX_RETENTION // DAY}


# ── 한 행의 예정 시각(화면 표시용) ────────────────────────────────────────────
def schedule(created_at: int, status_changed_at: int, status: str) -> dict:
    """아래 SQL 조건과 **같은 계산**을 파이썬으로 한다(패리티 테스트로 고정).

    시각이 잘못된 행은 `valid=False`이고 예정일이 없다 — 정리도 하지 않는다.
    """
    c, s = int(created_at or 0), int(status_changed_at or 0)
    if c <= 0 or s < 0:
        return {"valid": False, "duplicateCheckClearAt": None,
                "emailRemovalAt": None, "deletionAt": None, "deletionCapped": False}
    base = max(c, s)
    closed = status in CLOSED_STATUSES
    email_at = c + EMAIL_MAX_AFTER_CREATED
    if closed:
        email_at = min(email_at, base + EMAIL_AFTER_CLOSED)
    if closed:
        by_status, cap = base + CLOSED_RETENTION, c + ABSOLUTE_MAX_RETENTION
        deletion_at, capped = min(by_status, cap), cap < by_status
    else:
        deletion_at, capped = c + OPEN_MAX_RETENTION, False
    return {"valid": True,
            "duplicateCheckClearAt": c + DEDUPE_CLEAR_AFTER,
            "emailRemovalAt": email_at,
            "deletionAt": deletion_at,
            # 처리 후 180일보다 접수 후 545일 상한이 먼저 온 행(화면에 "상한" 표시)
            "deletionCapped": capped}


# ── SQL 조건 (dry-run 집계와 실제 정리가 **같은 문자열**을 쓴다) ──────────────
_CLOSED_SQL = "status IN ('resolved', 'rejected')"
_BASE_SQL = "MAX(created_at, status_changed_at)"
_VALID_SQL = ("created_at > 0 AND created_at <= :now"
              " AND status_changed_at >= 0 AND status_changed_at <= :now")

DEDUPE_DUE_SQL = (f"dedupe_key <> '' AND {_VALID_SQL}"
                  " AND created_at <= :now - :dedupe_after")
EMAIL_DUE_SQL = (f"contact_email <> '' AND {_VALID_SQL}"
                 " AND (created_at <= :now - :email_max"
                 f" OR ({_CLOSED_SQL} AND {_BASE_SQL} <= :now - :email_closed))")
ROW_DUE_SQL = (f"{_VALID_SQL}"
               f" AND ((NOT {_CLOSED_SQL} AND created_at <= :now - :open_max)"
               f" OR ({_CLOSED_SQL} AND ({_BASE_SQL} <= :now - :closed_keep"
               " OR created_at <= :now - :absolute_max)))")
INVALID_SQL = f"NOT ({_VALID_SQL})"


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


async def candidates(db, *, now: int) -> dict:
    """정리 **후보 건수**(읽기 전용). dry-run·apply와 같은 WHERE 문자열을 쓴다."""
    p = _params(now)
    return {"candidateDuplicateCheckClearCount": await _count(db, DEDUPE_DUE_SQL, p),
            "candidateEmailClearCount": await _count(db, EMAIL_DUE_SQL, p),
            "candidateDeleteCount": await _count(db, ROW_DUE_SQL, p),
            "invalidTimestampCount": await _count(db, INVALID_SQL, p)}


async def cleanup_once(db, *, now: int, dry_run: bool) -> dict:
    """한 회차. `db`를 받는 이유는 테스트가 **다른 연결(=다른 프로세스)**을 흉내 내기 위해서다.

    각 문장 뒤에 바로 commit한다 — 이 커넥션은 프로세스 전역 공유라, 다른 코루틴의 commit/rollback이
    끼어들 수 있는 긴 트랜잭션을 만들지 않는다. 문장 하나하나가 원자적이고 멱등이라 중간에
    끊겨도 다음 회차가 이어서 끝낸다.
    """
    p = _params(now)
    invalid = await _count(db, INVALID_SQL, p)
    if dry_run:
        return {"mode": "dry_run", "now": int(now),
                "duplicateChecksCleared": await _count(db, DEDUPE_DUE_SQL, p),
                "emailCleared": await _count(db, EMAIL_DUE_SQL, p),
                "rowsDeleted": await _count(db, ROW_DUE_SQL, p),
                "invalidSkipped": invalid}
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
            "invalidSkipped": invalid}


# ── 프로세스 내 실행 ─────────────────────────────────────────────────────────
_lock = asyncio.Lock()
_last: dict | None = None
_consecutive_failures = 0
#: 이 프로세스에서 정리 워커 task가 살아 있는가(접수 준비 조건). DB에 쓰지 않는다.
_worker_running = False


def last_report() -> dict | None:
    return dict(_last) if _last else None


def worker_running() -> bool:
    return _worker_running


def intake_ready() -> bool:
    """접수를 열어도 되는 **설정 준비** 상태 — apply 모드 + 워커 가동.

    한 회차 실패(`_consecutive_failures`)는 보지 않는다(모듈 docstring 참고).
    """
    return mode() == "apply" and _worker_running


def status() -> dict:
    """OWNER 화면용 비민감 상태. 요청 본문·이메일·URL·해시는 담지 않는다."""
    last = last_report()
    return {"enabled": enabled(), "dryRun": dry_run_flag(), "mode": mode(),
            "workerRunning": _worker_running, "intakeReady": intake_ready(),
            "lastRunAt": last["at"] if last else None,
            "consecutiveFailures": _consecutive_failures,
            "lastRun": last}


def reset_state() -> None:
    """테스트용."""
    global _last, _lock, _consecutive_failures, _worker_running
    _last = None
    _consecutive_failures = 0
    _worker_running = False
    _lock = asyncio.Lock()


async def run_cleanup(*, now: int | None = None) -> dict:
    """같은 프로세스에서 **겹쳐 돌지 않는다**(두 번째 호출은 건너뛴다).

    다른 프로세스와의 겹침은 SQL 쪽 멱등성이 막는다.
    """
    global _last, _consecutive_failures
    if _lock.locked():
        return {"skipped": "in_progress"}
    async with _lock:
        ts = int(time.time() if now is None else now)
        db = await get_db()
        try:
            report = await cleanup_once(db, now=ts, dry_run=mode() != "apply")
        except Exception as e:
            with contextlib.suppress(Exception):
                await db.rollback()
            _consecutive_failures += 1
            _last = {"ok": False, "at": ts, "error": type(e).__name__}
            _log({"event": "cleanup_failed", "error": type(e).__name__})
            raise
        _consecutive_failures = 0
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
    """lifespan용. task를 만들기 **전에** 가동 표시를 켠다 — 첫 요청이 task의 첫 실행보다
    먼저 와도 접수 준비 판정이 흔들리지 않게 한다. task가 끝나면 워커의 finally가 끈다."""
    global _worker_running
    _worker_running = True
    return asyncio.get_running_loop().create_task(start_support_retention_worker(**kwargs))
