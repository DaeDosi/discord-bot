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
from typing import Awaitable, Callable

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
        try:
            await fn(db)
            await db.commit()
            _stats["writes"] += 1
            return True
        except Exception as e:                      # noqa: BLE001
            await _rollback(db)
            if not is_locked(e):
                raise
            last = e
            if i + 1 >= attempts:
                break
            _stats["retries"] += 1
            # jitter — 여러 워커가 같은 순간에 몰려 재충돌하는 것을 막는다
            await asyncio.sleep(delay + random.uniform(0, delay))
            delay *= 2
    _stats["giveups"] += 1
    if log is not None:
        log({"event": "db_locked_giveup", "level": "warning", "what": what,
             "attempts": attempts, "detail": str(last)[:160]})
    return False
