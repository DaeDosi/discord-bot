"""치지직 방송 상태는 **바뀐 채널만** 쓴다 (P1-C).

실측(2026-08-01): `monitor_is_live`가 3회 연속 `db_locked_giveup`으로 실패했다.
그런데 이 쓰기는 방송 상태가 그대로여도 매 폴링마다 모든 구독 채널을 UPDATE하고
있었다 — 같은 값을 다시 쓰면서 같은 SQLite 파일을 쓰는 다른 워커와 경합한 셈이다.

저장에 실패하면 다음 폴링이 DB의 옛 값을 보고 같은 전이를 다시 판정해 **알림이
중복 발송**될 수 있으므로, 이 쓰기를 줄이는 것은 로그 노이즈 문제가 아니다.
"""
import chzzk_monitor as cm
import pytest


class _Row(dict):
    """aiosqlite.Row 대역 — 컬럼 접근만 쓴다."""
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


def _rows(states):
    return [_Row(id=i + 1, chzzk_channel_id=f"ch{i}", chzzk_name=f"n{i}",
                 is_live=int(s), discord_channel=1, mention_everyone=0,
                 mention_role_id=None, custom_message=None, guild_id=1)
            for i, s in enumerate(states)]


@pytest.fixture
def captured(monkeypatch):
    """실제 쓰기 대신 pending 목록만 가로챈다."""
    seen = {"pending": None, "calls": 0}

    async def fake_write(_path, work, **_kw):
        seen["calls"] += 1

        class _Conn:
            async def executemany(self, _sql, params):
                seen["pending"] = list(params)

        await work(_Conn())
        return True

    monkeypatch.setattr(cm, "db_write_isolated", fake_write)
    monkeypatch.setattr(cm, "_send_live_notification", _noop)
    monkeypatch.setattr(cm, "_send_offline_notification", _noop)
    monkeypatch.setattr(cm, "_fetch_live_detail", _none)
    return seen


async def _noop(*_a, **_kw):
    return None


async def _none(*_a, **_kw):
    return None


def _setup(monkeypatch, db_states, live_states):
    async def fake_subs():
        return _rows(db_states)

    async def fake_info(channel_id):
        idx = int(channel_id.replace("ch", ""))
        return {"openLive": live_states[idx]}

    monkeypatch.setattr(cm, "_fetch_channel_info", fake_info)
    return fake_subs


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


async def _check(monkeypatch, db_states, live_states):
    fake_subs = _setup(monkeypatch, db_states, live_states)
    rows = await fake_subs()

    async def fake_get_db():
        class _C:
            async def execute(self, *_a, **_kw):
                class _Cur:
                    async def fetchall(self_inner):
                        return rows
                return _Cur()
        return _C()

    monkeypatch.setattr(cm, "get_db", fake_get_db)
    await cm._check_once()


# ── 변화 없으면 쓰지 않는다 ────────────────────────────────────────────────
def test_no_change_means_no_write(captured, monkeypatch):
    _run(_check(monkeypatch, db_states=[1, 0, 1, 0], live_states=[True, False, True, False]))
    assert captured["calls"] == 0, "값이 그대로인데 UPDATE가 나갔다"


def test_one_change_writes_one_row(captured, monkeypatch):
    _run(_check(monkeypatch, db_states=[0, 0, 1, 0], live_states=[True, False, True, False]))
    assert captured["pending"] == [(1, 1)]


def test_all_changed_writes_all(captured, monkeypatch):
    _run(_check(monkeypatch, db_states=[0, 1, 0, 1], live_states=[True, False, True, False]))
    assert sorted(captured["pending"]) == [(0, 2), (0, 4), (1, 1), (1, 3)]


def test_offline_transition_is_written(captured, monkeypatch):
    _run(_check(monkeypatch, db_states=[1], live_states=[False]))
    assert captured["pending"] == [(0, 1)]


def test_api_failure_writes_nothing(captured, monkeypatch):
    async def fail_info(_cid):
        return None                      # 조회 실패를 offline으로 오판하면 안 된다

    monkeypatch.setattr(cm, "_fetch_channel_info", fail_info)

    rows = _rows([1, 1])

    async def fake_get_db():
        class _C:
            async def execute(self, *_a, **_kw):
                class _Cur:
                    async def fetchall(self_inner):
                        return rows
                return _Cur()
        return _C()

    monkeypatch.setattr(cm, "get_db", fake_get_db)
    _run(cm._check_once())
    assert captured["calls"] == 0
