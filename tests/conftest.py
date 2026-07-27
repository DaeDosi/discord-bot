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

    import database

    async def _setup():
        await database.init_db()
        conn = await database.get_db()
        await conn.execute("DELETE FROM chzzk_channel_history")
        await conn.commit()
        await reset_state()

    async def _teardown():
        await reset_state()
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
