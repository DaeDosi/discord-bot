"""요청을 시간에 고르게 흘리는 토큰 버킷 + 곱셈 감소 회복.

`singcup_sweep`이 쓰던 것을 그대로 꺼내 왔다. 삭제 감사(anti-entropy) 워커도
같은 것이 필요한데, 복사해 두면 아래 P0 교훈이 한쪽에서만 유지되다 갈라진다.

**저장 용량과 충전 속도는 다른 값이다.** 예전에는 토큰 상한을 rate로 잡아서
rate가 1 미만이면 토큰이 rate에서 멈춰 1.0에 영영 도달하지 못했다(요청 하나에
토큰 1개가 필요하다). 그래서 429 한 번에 감속(×0.5)되는 순간 회차가 첫 건에서
통째로 멈췄다. 용량은 항상 최소 1.0을 보장한다 — 감사 워커는 0.15~0.2건/초로
도는 것이 정상이라 이 불변식이 없으면 처음부터 한 건도 못 보낸다.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable


class TokenBucket:
    """초당 rate건으로 흘리고, 429/5xx를 만나면 스스로 감속한다.

    감속은 빠르게(×0.5), 증속은 느리게(+5%/회) — 안전한 쪽으로 기운다.
    """

    def __init__(self, rate: float, cap: float, floor: float | None = None,
                 *, name: str = "sweep",
                 on_log: Callable[[dict], None] | None = None):
        self.name = name
        self._on_log = on_log
        self.floor = 0.05 if floor is None else floor
        self.rate = max(self.floor, min(rate, cap))
        self.cap = cap
        self.capacity = max(1.0, cap)
        self.tokens = 1.0
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()
        self.throttled = 0

    async def acquire(self):
        # 락은 '순서대로 한 명씩 통과'시키는 용도다. 대기 중 취소되면 async with가
        # 락을 풀어 주므로 다음 대기자가 막히지 않는다.
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity,
                                  self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                # 남은 토큰만큼만 기다린다. rate는 항상 floor(>0) 이상이라 유한하다.
                await asyncio.sleep((1.0 - self.tokens) / max(self.rate, 1e-6))

    def slow_down(self, why: str):
        self.rate = max(self.floor, self.rate * 0.5)
        self.throttled += 1
        if self._on_log:
            self._on_log({"event": f"{self.name}_throttle", "level": "warning",
                          "reason": why, "new_rate": round(self.rate, 3)})

    def recover(self):
        if self.rate < self.cap:
            self.rate = min(self.cap, self.rate * 1.05)
