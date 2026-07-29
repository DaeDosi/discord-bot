"""pytest 공통 설정.

이 저장소에는 원래 테스트가 없었다. 첫 방송일 수집기는 외부 비공식 API에 의존하고
캐시/재시도/동시성 규칙이 얽혀 있어 수동 확인만으로는 회귀를 잡기 어려워 여기서만
테스트를 둔다. 외부를 실제로 호출하는 테스트는 tests/integration/ 으로 분리했다.
"""
import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# 실제 bot.db를 건드리지 않도록, database 모듈을 import 하기 *전에* 임시 DB로 돌린다.
_TMP_DB = Path(tempfile.gettempdir()) / f"nexbot-test-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
# 테스트에서 실제로 초·분을 기다리지 않도록 백오프를 0으로 만든다.
os.environ.setdefault("CHZZK_BACKOFF_BASE_SECONDS", "0")
os.environ.setdefault("CHZZK_REQUESTS_PER_SECOND", "0")   # 0 = 속도 제한 비활성
# 싱드컵 — 이벤트 기간과 지연을 테스트용 고정값으로. 모듈이 import 시점에 읽으므로
# 반드시 import 전에 세팅돼야 한다.
os.environ.setdefault("SINGCUP_START_AT", "2026-07-20T00:00:00+09:00")
os.environ.setdefault("SINGCUP_END_AT", "2026-08-09T23:59:59+09:00")
os.environ.setdefault("SINGCUP_BACKOFF_BASE_SECONDS", "0")
os.environ.setdefault("SINGCUP_PAGE_DELAY_SECONDS", "0")
os.environ.setdefault("SINGCUP_START_DELAY_SECONDS", "0")
# 정각 스윕은 토큰 버킷으로 요청을 고르게 흘린다 — 테스트에서 실제 초를 기다리지
# 않도록 속도 제한만 사실상 해제한다(로직은 그대로 검증된다).
# /main 캐시는 운영 부하 대책이다. 대부분의 테스트는 쓰기 직후 값을 확인하므로
# 기본은 꺼 두고, 캐시 동작 자체를 보는 테스트만 TTL을 켠다.
os.environ.setdefault("SINGCUP_MAIN_CACHE_SECONDS", "0")
os.environ.setdefault("SINGCUP_SWEEP_MIN_RATE", "10000")
os.environ.setdefault("SINGCUP_SWEEP_MAX_RATE", "10000")

# 루트(database 패키지)와 web/backend(chzzk_channel_history) 둘 다 import 가능해야 한다.
for p in (str(_ROOT), str(_ROOT / "web" / "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: 실제 치지직 API를 호출한다(기본 skip)")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db(request):
    """매 테스트마다 스키마를 초기화하고 chzzk_channel_history를 비운다."""
    from chzzk_channel_history import reset_state
    from singcup_clips import reset_state as clips_reset
    from singcup_collector import reset_state as singcup_reset

    import database

    async def _setup():
        await database.init_db()
        conn = await database.get_db()
        for t in ("chzzk_channel_history", "singcup_feeds", "singcup_collect_runs",
                  "singcup_clips", "singcup_streamers", "singcup_snapshots",
                  "singcup_clip_scan", "singcup_clip_retry",
                  "singcup_backfill_state", "singcup_locks",
                  "singcup_snapshot_hourly", "singcup_snapshot_daily",
                  "singcup_final_standings", "singcup_refresh_runs",
                  "singcup_sweep_runs"):
            await conn.execute(f"DELETE FROM {t}")
        # 락은 지우지 말고 풀어 둔다(행 자체는 id=1 하나만 존재해야 한다)
        await conn.execute("UPDATE singcup_collect_lock SET locked_until=0, owner=''")
        await conn.commit()
        await reset_state()
        await singcup_reset()
        await clips_reset()

    async def _teardown():
        await reset_state()
        await singcup_reset()
        await clips_reset()
        await database.close_db()

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_setup())

    def run(coro):
        return loop.run_until_complete(coro)

    yield run

    loop.run_until_complete(_teardown())
    loop.close()


def pytest_sessionfinish(session, exitstatus):
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(str(_TMP_DB) + suffix).unlink(missing_ok=True)
        except OSError:
            pass
