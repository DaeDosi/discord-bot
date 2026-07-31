"""잠금에 안전한 DB 쓰기 한 단위.

배경(실측 2026-07-31, Railway): 공개 GET `/api/singcup/main`이 응답을 계산하면서
`_save_top_movers()`로 UPDATE + COMMIT까지 수행했고, 그 쓰기가
`sqlite3.OperationalError: database is locked`에 걸리자 **읽기 요청 전체가 500**이
됐다. 봇 프로세스와 백엔드가 같은 SQLite 파일을 쓰는 구조라 잠금은 정상적으로
일어나는 일이고, 그때 공개 조회가 죽으면 안 된다.

그래서 쓰기는 전부 이 헬퍼를 지난다.
  - 열자마자 커밋한다(트랜잭션을 열어 둔 채 외부 호출을 기다리지 않는다)
  - 실패하면 **잠금이든 아니든 반드시 롤백**한다. 되돌리지 않으면 부분 실행된
    문장이 다음 시도나 다른 작업의 커밋에 묻어 들어간다.
  - 잠금은 제한된 횟수만 지수 백오프+jitter로 재시도하고, 소진하면 예외를 올리지
    않고 False를 돌려준다(호출자가 계속 진행할 수 있게).
  - 잠금이 아닌 오류는 그대로 올린다 — 조용히 삼키면 진짜 버그가 숨는다.

`web/backend/singcup_sweep.py`에도 같은 성격의 헬퍼가 있다(스윕 전용, 먼저 만들어진
것). 봇 프로세스(cogs/)에서도 써야 해서 공용 위치인 여기에 둔다. 스윕 쪽을 이쪽으로
합치는 것은 별도 정리 대상이다.
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import time
import weakref
from typing import Awaitable, Callable

import aiosqlite

DB_RETRY_ATTEMPTS = int(os.getenv("DB_WRITE_RETRY_ATTEMPTS", "4"))
DB_RETRY_BASE_SECONDS = float(os.getenv("DB_WRITE_RETRY_BASE_SECONDS", "0.05"))

# 롤백 횟수 — 테스트가 "잠길 때마다 실제로 롤백했는가"를 직접 확인한다.
_stats = {"rollbacks": 0, "giveups": 0, "writes": 0, "retries": 0}


def write_stats() -> dict:
    return dict(_stats)


def reset_write_stats() -> None:
    for k in _stats:
        _stats[k] = 0


def is_locked(e: BaseException) -> bool:
    m = str(e).lower()
    return "database is locked" in m or "database is busy" in m


async def _rollback(db) -> None:
    try:
        await db.rollback()
        _stats["rollbacks"] += 1
    except Exception:                               # noqa: BLE001 — 롤백 실패는 삼킨다
        pass


# ── 공유 연결의 쓰기 트랜잭션 직렬화 ────────────────────────────────────────
# 공유 aiosqlite 연결은 `isolation_level=''`이라 **첫 DML부터 commit까지 SQLite
# 쓰기 잠금을 붙든다**(실측). 그런데 여러 코루틴이 같은 연결에서 DML을 섞어 실행할
# 수 있었고, 그중 하나가 트랜잭션이 열린 채 외부 await(HTTP·clip lock 폴링)에
# 들어가면 그 시간만큼 **프로세스 전체와 다른 프로세스의 쓰기까지** 멈췄다.
# 한 코루틴의 rollback이 다른 코루틴의 미커밋 작업을 되돌리는 문제도 같은 뿌리다.
#
# 그래서 공유 연결의 쓰기 트랜잭션은 이 락 안에서만 열린다. 락을 잡기 **전에**
# 외부 조회를 끝내는 것은 호출부의 책임이고, 락 안에서는 DB 작업만 한다.
# 이벤트 루프마다 따로 두는 이유: 테스트가 테스트마다 새 루프를 만드는데
# asyncio.Lock은 처음 쓰인 루프에 묶인다.
_shared_locks: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def shared_write_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _shared_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _shared_locks[loop] = lock
    return lock


def process_role() -> str:
    """로그의 `process` 필드 — 봇과 백엔드가 같은 파일을 쓰므로 구분이 필요하다."""
    role = os.getenv("DB_PROCESS_ROLE", "").strip()
    if role:
        return role
    return "web" if any("uvicorn" in str(a) for a in sys.argv) else "bot"


async def db_write(
    get_db: Callable[[], Awaitable],
    fn: Callable[[object], Awaitable],
    *,
    what: str,
    attempts: int = DB_RETRY_ATTEMPTS,
    log: Callable[[dict], None] | None = None,
) -> bool:
    """`fn(db)`를 실행하고 즉시 커밋한다. 성공하면 True.

    잠금으로 재시도를 소진하면 False(예외 없음). 다른 오류는 그대로 올린다.
    """
    delay = DB_RETRY_BASE_SECONDS
    last: BaseException | None = None
    for i in range(max(1, attempts)):
        db = await get_db()
        t0 = time.perf_counter()
        # 트랜잭션 전체가 이 락 안에서 열리고 닫힌다 — 다른 코루틴의 DML이 끼어들지
        # 못하고, 아래 rollback도 **내 작업만** 되돌린다.
        async with shared_write_lock():
            try:
                await fn(db)
                await db.commit()
                _stats["writes"] += 1
                return True
            except BaseException as e:              # noqa: BLE001
                # CancelledError에서도 롤백을 보장한다 — `except Exception`이면
                # 취소된 작업이 **열린 트랜잭션을 그대로 남기고** 사라진다.
                await _rollback(db)
                if not isinstance(e, Exception) or not is_locked(e):
                    raise
                if log is not None:
                    log({"event": "db_locked", "level": "warning", "operation": what,
                         "process": process_role(), "pid": os.getpid(),
                         "connection": "shared", "in_transaction": False,
                         "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                         "retry": i})
                last = e
        # 백오프는 **락 밖에서** 기다린다 — 락을 쥔 채 자면 다른 쓰기까지 멈춘다.
        if i + 1 >= attempts:
            break
        _stats["retries"] += 1
        # jitter — 여러 워커가 같은 순간에 몰려 재충돌하는 것을 막는다
        await asyncio.sleep(delay + random.uniform(0, delay))
        delay *= 2
    _stats["giveups"] += 1
    if log is not None:
        log({"event": "db_locked_giveup", "level": "warning", "what": what,
             "operation": what, "process": process_role(), "pid": os.getpid(),
             "connection": "shared",
             "attempts": attempts, "detail": str(last)[:160]})
    return False


# ── 전용 연결 쓰기 ──────────────────────────────────────────────────────────
# 공유 연결을 쓰면 최악 시간에 상한이 없다. aiosqlite는 연결마다 워커 스레드 하나로
# 모든 작업을 직렬화하므로, 앞선 작업이 끝날 때까지의 **큐 대기**는 busy_timeout이
# 막지 못한다(실측: 앞선 느린 작업 1/2/3개 → 큐 대기 550 / 1,075 / 1,783ms로 선형 증가).
# 그래서 "attempts × busy_timeout + backoff"는 진짜 상한이 아니다.
#
# 시간 상한이 필요한 짧은 트랜잭션은 자기 연결을 열어 쓴다. 자기 큐에는 앞선 작업이
# 없으므로 대기 = 0이고, 남는 변수는 busy_timeout과 재시도뿐이라 상한이 계산된다.
DEFAULT_ISOLATED_BUSY_TIMEOUT_MS = 2000
DEFAULT_ISOLATED_ATTEMPTS = 3
# rollback + close에 남겨 두는 시간. 예산을 전부 시도에 써 버리면 정리할 시간이
# 없어 실제 소요가 예산을 넘는다.
ISOLATED_CLEANUP_RESERVE_SECONDS = 0.25
# 이보다 짧게 남았으면 새 시도를 시작하지 않는다(연결만 열다 끝난다).
ISOLATED_MIN_ATTEMPT_MS = 50


async def db_write_isolated(
    db_path: str,
    fn: Callable[[object], Awaitable],
    *,
    what: str,
    busy_timeout_ms: int = DEFAULT_ISOLATED_BUSY_TIMEOUT_MS,
    attempts: int = DEFAULT_ISOLATED_ATTEMPTS,
    budget_seconds: float = 3.0,
    backoff_base: float = DB_RETRY_BASE_SECONDS,
    cleanup_reserve: float = ISOLATED_CLEANUP_RESERVE_SECONDS,
    log: Callable[[dict], None] | None = None,
) -> bool:
    """`fn(conn)`을 **전용 연결의 한 트랜잭션**으로 실행하고 커밋한다.

    `budget_seconds`는 **절대 deadline**이다. 시도 사이에서만 예산을 보면 남은 시간이
    0.1초여도 새로 2초짜리 시도가 시작돼, "예산 3초 / 실제 최악 7.35초" 같은 모순이
    생긴다. 그래서 매 시도 전에 남은 시간을 계산하고
    **그 시도의 busy_timeout을 남은 시간에 맞춰 줄인다.**

      remaining = deadline - now - cleanup_reserve
      attempt_busy_timeout = min(설정값, remaining)
      remaining < ISOLATED_MIN_ATTEMPT_MS 이면 시도하지 않는다

    이렇게 하면 전체 소요가 `budget_seconds + cleanup_reserve`를 넘지 않고, 그 값이
    곧 코드 상수로 계산되는 하드 상한이 된다.

    - 성공하면 True. 잠금으로 예산을 소진하면 False(예외 없음).
    - 잠금이 아닌 예외는 rollback·close 뒤 **그대로 올린다**.
    - 성공·잠금·예외·취소 어느 경로로 나가도 (미커밋이면) rollback하고 close한다.
    - `asyncio.wait_for`로 취소하지 않는다. aiosqlite의 워커 스레드는 취소되지 않아
      뒤에서 계속 돌고 연결 상태가 어긋난다. 각 시도가 자기 busy_timeout으로
      스스로 끝난다.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_seconds
    delay = backoff_base
    last: BaseException | None = None
    tries = max(1, attempts)
    for i in range(tries):
        remaining = deadline - loop.time() - cleanup_reserve
        if remaining * 1000 < ISOLATED_MIN_ATTEMPT_MS:
            break                                   # 남은 시간으로는 의미 있는 시도 불가
        attempt_busy = min(int(busy_timeout_ms), int(remaining * 1000))
        conn = None
        committed = False
        try:
            conn = await aiosqlite.connect(db_path)
            conn.row_factory = aiosqlite.Row
            await conn.execute(f"PRAGMA busy_timeout={attempt_busy}")
            await fn(conn)
            await conn.commit()
            committed = True
            _stats["writes"] += 1
            return True
        except Exception as e:                      # noqa: BLE001
            last = e
            if not is_locked(e):
                raise
        finally:
            if conn is not None:
                if not committed:
                    try:
                        await conn.rollback()
                        _stats["rollbacks"] += 1
                    except Exception:               # noqa: BLE001
                        pass
                try:
                    await conn.close()
                except Exception:                   # noqa: BLE001
                    pass
        if i + 1 >= tries:
            break
        # backoff도 deadline을 넘지 않는다
        wait = min(delay + random.uniform(0, delay),
                   deadline - loop.time() - cleanup_reserve)
        if wait <= 0:
            break
        _stats["retries"] += 1
        await asyncio.sleep(wait)
        delay *= 2
    _stats["giveups"] += 1
    if log is not None:
        log({"event": "db_locked_giveup", "level": "warning", "what": what,
             "attempts": tries, "budgetSeconds": budget_seconds,
             "detail": str(last)[:160]})
    return False


def isolated_worst_case_seconds(
    *, budget_seconds: float,
    cleanup_reserve: float = ISOLATED_CLEANUP_RESERVE_SECONDS,
) -> float:
    """전용 연결 쓰기의 **하드 상한**.

    절대 deadline을 쓰므로 시도 횟수·busy_timeout과 무관하게
    `예산 + 정리 여유`를 넘지 않는다. 마지막 시도가 남은 시간을 다 쓰고 실패해도
    rollback/close 몫이 미리 확보돼 있다.
    """
    return budget_seconds + cleanup_reserve
