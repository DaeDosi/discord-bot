"""토큰 버킷 — **운영과 같은 속도 범위**로 검증한다.

conftest는 다른 테스트가 실초를 기다리지 않도록 SINGCUP_SWEEP_MIN/MAX_RATE를
10000으로 올려 둔다. 그 설정에서는 이 파일이 잡으려는 결함이 전혀 드러나지
않는다(rate가 1보다 크면 언제나 통과하므로). 그래서 여기서는 환경변수를 쓰지 않고
TokenBucket에 rate/cap/floor를 직접 넘겨 0.2~1.7 구간을 그대로 시험한다.

재현했던 결함: 토큰 상한을 충전 속도(rate)로 잡아서, rate가 1 미만이면 토큰이
rate에서 멈춰 1.0에 영영 도달하지 못했다. 요청 하나에 토큰 1개가 필요하므로
429나 카드 실패로 한 번 감속(×0.5)되는 순간 스윕이 첫 클립에서 멈췄다.
"""
import asyncio
import time

import pytest
import singcup_sweep as sw

# 운영에서 실제로 나타나는 값들. 0.749는 장애 로그에 찍힌 실측 rate다.
PROD_RATES = [1.7, 1.5, 1.0, 0.749, 0.5, 0.2]


def _bucket(rate, cap=2.0, floor=0.1):
    return sw.TokenBucket(rate, cap, floor=floor)


@pytest.mark.parametrize("rate", PROD_RATES)
def test_acquire_repeats_at_production_rate(db, rate):
    """rate가 1 미만이어도 연속 acquire가 유한 시간에 끝난다."""
    n = 3

    async def go():
        b = _bucket(rate)
        t0 = time.monotonic()
        for _ in range(n):
            # 넉넉한 상한을 걸어 '영구 대기'를 실패로 잡는다
            await asyncio.wait_for(b.acquire(), timeout=(n + 2) / rate)
        return time.monotonic() - t0
    elapsed = db(go())
    ideal = (n - 1) / rate
    assert elapsed >= ideal * 0.8, "속도 제한이 사라졌다(너무 빠름)"
    assert elapsed <= ideal + 2.0, f"{rate}/s에서 {elapsed:.2f}s — 너무 느리다"


def test_capacity_is_independent_of_rate():
    """저장 용량과 충전 속도는 다른 값이다. 용량은 최소 1.0."""
    for rate in PROD_RATES:
        b = _bucket(rate)
        assert b.capacity >= 1.0
        assert b.rate == pytest.approx(min(rate, 2.0))
    # cap이 1보다 작아도 토큰 하나는 담을 수 있어야 한다
    assert _bucket(0.2, cap=0.5).capacity >= 1.0


def test_resumes_after_429_slow_down(db):
    """429로 1.0 → 0.5로 감속된 뒤에도 계속 진행한다(예전엔 여기서 멈췄다)."""
    async def go():
        b = _bucket(1.0)
        await asyncio.wait_for(b.acquire(), timeout=3)
        b.slow_down("http_429")
        assert b.rate == pytest.approx(0.5)
        t0 = time.monotonic()
        await asyncio.wait_for(b.acquire(), timeout=8)
        return time.monotonic() - t0
    waited = db(go())
    assert 1.0 <= waited <= 4.0, f"감속 후 대기 {waited:.2f}s"


def test_resumes_after_fetch_failure_slow_down(db):
    """카드 조회 실패로 감속돼도 다음 클립으로 넘어간다."""
    async def go():
        b = _bucket(1.0)
        for _ in range(3):
            b.slow_down("fetch_failed")          # 1.0 → 0.5 → 0.25 → 0.125
        assert b.rate < 0.2
        await asyncio.wait_for(b.acquire(), timeout=15)
        return b.rate
    assert db(go()) > 0


def test_floor_stops_runaway_slow_down():
    """감속이 0으로 내려가 무한 대기가 되지 않는다."""
    b = _bucket(1.0, floor=0.2)
    for _ in range(20):
        b.slow_down("http_429")
    assert b.rate == pytest.approx(0.2)
    assert b.throttled == 20


def test_recover_never_exceeds_cap():
    b = _bucket(0.5, cap=1.7)
    for _ in range(200):
        b.recover()
    assert b.rate <= 1.7 + 1e-9


def test_burst_is_bounded_by_capacity(db):
    """오래 쉰 뒤에도 용량 이상으로 몰아치지 않는다."""
    async def go():
        b = _bucket(1.0, cap=2.0)
        b.tokens = 0.0
        b.updated = time.monotonic() - 3600     # 한 시간 방치
        n = 0
        try:
            while True:
                await asyncio.wait_for(b.acquire(), timeout=0.05)
                n += 1
                if n > 10:
                    break
        except asyncio.TimeoutError:
            pass
        return n
    assert db(go()) <= 2, "용량을 넘겨 즉시 통과했다"


def test_cancelled_acquire_releases_the_lock(db):
    """대기 중 취소돼도 락을 물고 있지 않는다(다음 대기자가 진행)."""
    async def go():
        b = _bucket(0.2)
        await b.acquire()                        # 초기 토큰 소진
        slow = asyncio.create_task(b.acquire())  # 5초 대기에 들어간다
        await asyncio.sleep(0.05)
        slow.cancel()
        try:
            await slow
        except asyncio.CancelledError:
            pass
        assert not b._lock.locked(), "취소 후에도 락이 잡혀 있다"
        b.rate = 100.0                           # 충전을 빠르게 해 두고
        await asyncio.wait_for(b.acquire(), timeout=2)
        return True
    assert db(go()) is True


def test_sweep_completes_at_sub_one_rate(db, monkeypatch):
    """스윕 전체가 rate<1 에서도 끝난다 — 이번 장애의 최종 회귀."""
    import httpx
    import singcup_clips as sc
    from test_singcup_sweep import _cards, _install, _seed

    monkeypatch.setattr(sw, "MIN_RATE", 0.1)
    monkeypatch.setattr(sw, "MAX_RATE", 2.0)
    # 대상이 적으면 required_rate가 1 미만이 된다 — 예전엔 이때 곧바로 멈췄다
    monkeypatch.setattr(sw, "TARGET_MINUTES", 55.0)
    db(_seed(2, 1))

    calls = []

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        calls.append(url)
        return httpx.Response(200, json=__import__("test_singcup_clips").card(
            "#싱드컵", likes=7, views=11))
    _install(h)

    async def go():
        return await asyncio.wait_for(sw.run_cycle(), timeout=30)
    res = db(go())
    assert res["status"] == sw.COMPLETED
    assert res["processed"] == 2 and res["success"] == 2
    assert len(calls) == 2
    assert sc is not None


def test_sweep_finishes_even_when_every_card_fails(db, monkeypatch):
    """전부 실패해 계속 감속돼도 회차가 끝난다(무한 대기 금지)."""
    import httpx
    from test_singcup_sweep import _cards, _install, _seed

    monkeypatch.setattr(sw, "MIN_RATE", 0.5)
    monkeypatch.setattr(sw, "MAX_RATE", 2.0)
    db(_seed(2, 1))

    def h(request):
        url = str(request.url)
        if "/service/v1/channels/" in url or "/categories/" in url:
            return _cards()(request)
        return httpx.Response(503, json={"code": 503})
    _install(h)

    async def go():
        return await asyncio.wait_for(sw.run_cycle(), timeout=40)
    res = db(go())
    assert res["status"] == sw.COMPLETED
    assert res["failed"] == 2 and res["processed"] == 2
