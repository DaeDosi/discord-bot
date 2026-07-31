"""DB 쓰기 경합 개선 — 랭킹·프로필·모니터·chat.

배경(실측 2026-07-31 Railway):
  13:11:16  rising_collector 프로필 20,963행 저장
  13:12:19  chzzk_monitor: database is locked
  같은 시각 GET /api/singcup/main 10,298ms

백엔드는 **하나의 공유 aiosqlite 연결**을 쓴다. 연결마다 워커 스레드가 하나라
모든 작업이 그 큐에서 직렬화되므로, 긴 쓰기 하나가 공개 조회까지 통째로 뒤로
민다. 그래서 고치는 방향은 "트랜잭션을 쪼개기"가 아니라 **한 트랜잭션이 붙드는
시간을 줄이기**다 — 쪼개면 부분 랭킹이 사용자에게 보인다.
"""
from __future__ import annotations

import asyncio
import time

import pytest
import rising_collector as rc
import singcup_clips as sc


def _rows(n: int, *, rep="c", follower_base=0) -> list[dict]:
    return [{"channel_id": f"o{i:05d}", "channel_name": f"n{i}",
             "channel_image_url": "", "follower_count": follower_base + i,
             "verified_mark": 0, "representative_clip_uid": f"{rep}{i:05d}",
             "tagged_clip_count": 1, "last_channel_updated_at": 0}
            for i in range(n)]


async def _read_all(db):
    return {r["channel_id"]: dict(r) for r in await (await db.execute(
        "SELECT channel_id, channel_name, channel_image_url, follower_count, "
        "verified_mark, representative_clip_uid, tagged_clip_count, "
        "last_channel_updated_at FROM singcup_streamers")).fetchall()}


# ── 1. 벌크 UPSERT가 기존 반복 UPSERT와 **완전히 같은 결과**를 낸다 ─────────
@pytest.mark.parametrize("n", [50, 300])
def test_bulk_upsert_matches_per_row_loop(db, n):
    """규칙이 SQL과 파이썬 두 곳에 생겼다 — 어긋나면 조용히 갱신을 건너뛴다."""

    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        rows = _rows(n)
        # ① 기존 방식으로 한 번
        for s in rows:
            await sc._upsert_streamer(s, now)
        await conn.commit()
        expected = await _read_all(conn)

        # ② 같은 입력을 벌크로 — 이미 같은 값이므로 아무것도 쓰지 않아야 한다
        stat = await sc._upsert_streamers_bulk(rows, now)
        await conn.commit()
        assert stat == {"considered": n, "written": 0}
        assert await _read_all(conn) == expected

        # ③ 값이 바뀌면 그 행만 쓰고 결과는 반복 방식과 같다
        changed = _rows(n, rep="z", follower_base=1000)
        stat = await sc._upsert_streamers_bulk(changed, now + 1)
        await conn.commit()
        assert stat["written"] == n
        bulk_result = await _read_all(conn)
        for s in changed:                     # 반복 방식으로 다시 덮어써도 동일
            await sc._upsert_streamer(s, now + 2)
        await conn.commit()
        assert await _read_all(conn) == bulk_result

    db(go())


def test_case_rules_are_mirrored_exactly(db):
    """빈 문자열·last_channel_updated_at=0 분기를 SQL과 파이썬이 같게 처리한다."""

    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        base = {"channel_id": "o1", "channel_name": "이름", "channel_image_url": "img",
                "follower_count": 10, "verified_mark": 1,
                "representative_clip_uid": "c1", "tagged_clip_count": 2,
                "last_channel_updated_at": now}
        await sc._upsert_streamer(base, now)
        await conn.commit()

        # 이름·이미지가 비고 last_channel_updated_at=0 → 기존 값이 유지돼야 한다
        blank = dict(base, channel_name="", channel_image_url="",
                     follower_count=999, last_channel_updated_at=0)
        stat = await sc._upsert_streamers_bulk([blank], now + 1)
        await conn.commit()
        assert stat["written"] == 0, "유지되는 값인데 '바뀌었다'고 판단했다"

        got = (await _read_all(conn))["o1"]
        # 반복 방식으로 같은 입력을 넣어도 결과가 같아야 한다(규칙 일치 확인)
        await sc._upsert_streamer(blank, now + 2)
        await conn.commit()
        after = (await _read_all(conn))["o1"]
        assert got == after
        assert after["channel_name"] == "이름"
        assert after["follower_count"] == 10

    db(go())


def test_new_streamer_is_inserted(db):
    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        stat = await sc._upsert_streamers_bulk(_rows(3), now)
        await conn.commit()
        assert stat["written"] == 3
        assert len(await _read_all(conn)) == 3

    db(go())


# ── 2. 부분 랭킹이 보이지 않는다(트랜잭션 하나) ────────────────────────────
def test_ranking_write_has_no_intermediate_commit():
    """중간 COMMIT을 넣으면 일부만 새 랭킹인 상태가 사용자에게 보인다."""
    import inspect
    src = inspect.getsource(sc._upsert_streamers_bulk)
    assert "commit(" not in src, "벌크 UPSERT가 스스로 커밋하면 원자성이 깨진다"
    rank_src = inspect.getsource(sc.recompute_ranking)
    assert rank_src.count("await db.commit()") == 1, "랭킹 쓰기 COMMIT은 하나여야 한다"


async def _count_mixed(conn, writer, n: int, samples: int = 40) -> int:
    """쓰기가 도는 동안 '옛 값과 새 값이 섞여 보인' 횟수."""
    seen = []

    async def reader():
        for _ in range(samples):
            rows = await (await conn.execute(
                "SELECT representative_clip_uid FROM singcup_streamers")).fetchall()
            seen.append({r[0][:3] for r in rows})
            await asyncio.sleep(0)

    await asyncio.gather(reader(), writer())
    return sum(1 for k in seen if len(k) > 1)


def test_bulk_write_shrinks_the_partial_ranking_window(db):
    """부분 랭킹 노출 창을 줄인다.

    주의: 백엔드는 공유 연결 하나를 쓰고 SQLite 커서는 지연 평가라, **같은
    연결에서** 읽으면 자기 쓰기가 보인다(read-your-own-writes). 그래서 "절대
    섞여 보이지 않는다"는 이 구조에서 성립하지 않는다 — 성립하는 것은
    "커밋되지 않은 결과는 남지 않는다"(아래 rollback 테스트)와 "노출 창이
    반복 UPSERT보다 훨씬 짧다"이다. 중간 COMMIT을 넣으면 후자마저 무너지고,
    중단 시 혼합 세대가 **영구히** 남는다.
    """

    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        n = 200
        await conn.execute("DELETE FROM singcup_streamers")
        await sc._upsert_streamers_bulk(_rows(n, rep="old"), now)
        await conn.commit()

        async def per_row():
            for s in _rows(n, rep="aaa"):
                await sc._upsert_streamer(s, now + 1)
            await conn.commit()

        mixed_a = await _count_mixed(conn, per_row, n)

        async def bulk():
            await sc._upsert_streamers_bulk(_rows(n, rep="new"), now + 2)
            await conn.commit()

        mixed_c = await _count_mixed(conn, bulk, n)
        assert mixed_c <= mixed_a, f"벌크가 더 오래 섞여 보였다 A={mixed_a} C={mixed_c}"
        assert mixed_c <= 1, f"벌크에서도 여러 번 섞여 보였다: {mixed_c}"

    db(go())


def test_no_duplicate_or_missing_streamers(db):
    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        n = 500
        await sc._upsert_streamers_bulk(_rows(n), now)
        await conn.commit()
        rows = await (await conn.execute(
            "SELECT channel_id FROM singcup_streamers")).fetchall()
        ids = [r[0] for r in rows]
        assert len(ids) == n == len(set(ids))

    db(go())


def test_restart_recovers_without_partial_state(db):
    """커밋 전에 끊기면 아무것도 반영되지 않는다(전부 아니면 전무)."""

    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        await sc._upsert_streamers_bulk(_rows(20, rep="old"), now)
        await conn.commit()
        await sc._upsert_streamers_bulk(_rows(20, rep="new"), now + 1)
        await conn.rollback()                  # 프로세스 중단과 같은 효과
        rows = await (await conn.execute(
            "SELECT representative_clip_uid FROM singcup_streamers")).fetchall()
        assert {r[0][:3] for r in rows} == {"old"}

    db(go())


# ── 3. 프로필 저장 ─────────────────────────────────────────────────────────
def test_profile_persist_writes_only_changes(db, monkeypatch):
    async def go():
        conn = await sc.get_db()
        await conn.execute("DELETE FROM channel_profiles")
        await conn.commit()
        rc._LATEST_IMAGES.clear()
        rc._LATEST_IMAGES.update({f"c{i}": f"url{i}" for i in range(100)})
        await rc._persist_profiles()
        rows = await (await conn.execute(
            "SELECT chzzk_channel_id, image_url, updated_at "
            "FROM channel_profiles ORDER BY 1")).fetchall()
        assert len(rows) == 100
        before = {r[0]: (r[1], r[2]) for r in rows}

        # 두 번째 실행 — 값이 같은 행은 **한 글자도 쓰지 않는다.**
        # updated_at까지 매번 밀면 2만 행을 그대로 다시 쓰는 셈이라(실측: 소요가
        # 줄지 않았다) 갱신 간격 안에서는 건드리지 않는다.
        await asyncio.sleep(1.1)
        rc._LATEST_IMAGES.update({f"c{i}": f"url{i}" for i in range(100)})
        rc._LATEST_IMAGES["c0"] = "CHANGED"
        await rc._persist_profiles()
        rows = await (await conn.execute(
            "SELECT chzzk_channel_id, image_url, updated_at "
            "FROM channel_profiles ORDER BY 1")).fetchall()
        after = {r[0]: (r[1], r[2]) for r in rows}
        assert after["c0"][0] == "CHANGED"
        assert after["c1"][0] == before["c1"][0]
        assert after["c1"][1] == before["c1"][1], "안 바뀐 행을 다시 썼다"

    db(go())


def test_profile_touch_keeps_rows_alive_before_cleanup(db, monkeypatch):
    """시각 갱신을 건너뛰어도 30일 정리에 걸리지 않는다.

    이 둘은 한 쌍이다 — 갱신 간격이 정리 기준에 가까워지면 살아 있는 프로필이
    지워진다. 간격이 지난 행은 반드시 다시 밀려야 한다.
    """
    assert rc.PROFILE_TOUCH_INTERVAL_SECONDS * 2 < rc.PROFILE_RETENTION_SECONDS

    async def go():
        conn = await sc.get_db()
        await conn.execute("DELETE FROM channel_profiles")
        old = int(time.time()) - rc.PROFILE_TOUCH_INTERVAL_SECONDS - 100
        await conn.executemany(
            "INSERT INTO channel_profiles(chzzk_channel_id,image_url,updated_at) "
            "VALUES(?,?,?)", [(f"c{i}", f"url{i}", old) for i in range(10)])
        await conn.commit()
        rc._LATEST_IMAGES.clear()
        rc._LATEST_IMAGES.update({f"c{i}": f"url{i}" for i in range(10)})
        await rc._persist_profiles()
        rows = await (await conn.execute(
            "SELECT updated_at FROM channel_profiles")).fetchall()
        assert len(rows) == 10
        assert all(r[0] > old for r in rows), "간격이 지났는데 갱신하지 않았다"

    db(go())


def test_profile_persist_is_idempotent(db):
    async def go():
        conn = await sc.get_db()
        await conn.execute("DELETE FROM channel_profiles")
        await conn.commit()
        rc._LATEST_IMAGES.clear()
        rc._LATEST_IMAGES.update({f"c{i}": f"url{i}" for i in range(50)})
        for _ in range(3):
            await rc._persist_profiles()
            rc._LATEST_IMAGES.update({f"c{i}": f"url{i}" for i in range(50)})
        n = await (await conn.execute(
            "SELECT COUNT(*) FROM channel_profiles")).fetchone()
        assert n[0] == 50

    db(go())


def test_profile_persist_and_cleanup_are_one_transaction():
    """갱신과 30일 정리가 갈라지면 중단 시 '지웠는데 갱신은 안 된' 상태가 남는다."""
    import inspect
    src = inspect.getsource(rc._persist_profiles)
    body = src[:src.index("_log(")]
    assert body.count("await db.commit()") == 1
    assert "DELETE FROM channel_profiles" in body
    # chunk 루프 안에 COMMIT이 들어가면 부분 상태가 오래 노출된다
    loop = body[body.index("for i in range(0, len(touch)"):
                body.index('await db.execute("DELETE FROM channel_profiles')]
    assert "commit" not in loop, "chunk마다 커밋하고 있다"


# ── 3-b. SQL 바인드 변수 한도 — 운영 규모(20,963) ─────────────────────────
#
# 런타임 한도를 믿지 않는 이유: 같은 파이썬에서도 재는 형태에 따라 값이 다르다
# (실측 SELECT ?,?,… → 2,000 = SQLITE_MAX_COLUMN / IN 절 → 250,000).
# 배포 이미지가 바뀌면 999로 떨어질 수 있으므로 코드는 고정 chunk를 쓴다.
def test_touch_chunk_is_safe_on_the_most_pessimistic_build():
    """어떤 SQLite 빌드에서도 안전한 값인가 — 가장 낮은 한도 999로 검사한다."""
    assert rc.PROFILE_TOUCH_CHUNK + 2 <= 999, (
        f"chunk {rc.PROFILE_TOUCH_CHUNK} + 부가 변수 2개가 999를 넘는다")
    assert rc.PROFILE_TOUCH_CHUNK >= 100, "너무 잘게 나누면 문장 수가 폭증한다"


@pytest.mark.parametrize("n", [20963, 40000])
def test_profile_persist_at_production_scale(db, n):
    """운영 규모(20,963)와 확장 규모(40,000)에서 too many SQL variables 0."""

    async def go():
        conn = await sc.get_db()
        await conn.execute("DELETE FROM channel_profiles")
        old = int(time.time()) - rc.PROFILE_TOUCH_INTERVAL_SECONDS - 100
        imgs = {f"c{i:06d}": f"url{i}" for i in range(n)}
        await conn.executemany(
            "INSERT INTO channel_profiles(chzzk_channel_id,image_url,updated_at) "
            "VALUES(?,?,?)", [(k, v, old) for k, v in imgs.items()])
        await conn.commit()

        rc._LATEST_IMAGES.clear()
        rc._LATEST_IMAGES.update(imgs)
        rc._LATEST_IMAGES["c000000"] = "CHANGED"    # 1건은 URL 변경 경로로
        await rc._persist_profiles()

        rows = await (await conn.execute(
            "SELECT chzzk_channel_id, image_url, updated_at "
            "FROM channel_profiles")).fetchall()
        assert len(rows) == n, "누락·중복이 생겼다"
        assert len({r[0] for r in rows}) == n
        got = {r[0]: (r[1], r[2]) for r in rows}
        assert got["c000000"][0] == "CHANGED"
        # 간격이 지났으므로 **전원** 갱신돼야 한다(마지막 불완전 chunk 포함)
        assert all(v[1] > old for v in got.values()), "일부 대상이 갱신되지 않았다"

    db(go())


def test_profile_persist_handles_empty_and_partial_chunks(db):
    """빈 목록·정확히 나누어떨어지는 경우·마지막 불완전 chunk."""

    async def go():
        conn = await sc.get_db()
        old = int(time.time()) - rc.PROFILE_TOUCH_INTERVAL_SECONDS - 100
        for n in (0, 1, rc.PROFILE_TOUCH_CHUNK,
                  rc.PROFILE_TOUCH_CHUNK + 1, rc.PROFILE_TOUCH_CHUNK * 2 + 7):
            await conn.execute("DELETE FROM channel_profiles")
            imgs = {f"c{i:06d}": f"url{i}" for i in range(n)}
            if imgs:
                await conn.executemany(
                    "INSERT INTO channel_profiles"
                    "(chzzk_channel_id,image_url,updated_at) VALUES(?,?,?)",
                    [(k, v, old) for k, v in imgs.items()])
            await conn.commit()
            rc._LATEST_IMAGES.clear()
            rc._LATEST_IMAGES.update(imgs)
            await rc._persist_profiles()        # 예외 없이 끝나야 한다
            rows = await (await conn.execute(
                "SELECT updated_at FROM channel_profiles")).fetchall()
            assert len(rows) == n, n
            assert all(r[0] > old for r in rows), n

    db(go())


def test_profile_ids_are_unique_by_construction(db):
    """중복 id가 IN 절에 들어가지 않는다 — 출처가 dict라 키가 곧 유일하다."""

    async def go():
        rc._LATEST_IMAGES.clear()
        rc._LATEST_IMAGES.update({f"c{i}": "u" for i in range(10)})
        rc._LATEST_IMAGES["c0"] = "u"           # 같은 키를 다시 넣어도 1건
        assert len(rc._LATEST_IMAGES) == 10
        assert len(set(rc._LATEST_IMAGES)) == len(rc._LATEST_IMAGES)

    db(go())


def test_mid_chunk_failure_rolls_back_everything(db, monkeypatch):
    """두 번째 chunk에서 실패하면 첫 chunk도 남지 않고, 다음 실행이 재시도한다."""

    async def go():
        conn = await sc.get_db()
        await conn.execute("DELETE FROM channel_profiles")
        old = int(time.time()) - rc.PROFILE_TOUCH_INTERVAL_SECONDS - 100
        n = rc.PROFILE_TOUCH_CHUNK * 2 + 5
        imgs = {f"c{i:06d}": f"url{i}" for i in range(n)}
        await conn.executemany(
            "INSERT INTO channel_profiles(chzzk_channel_id,image_url,updated_at) "
            "VALUES(?,?,?)", [(k, v, old) for k, v in imgs.items()])
        await conn.commit()
        rc._LATEST_IMAGES.clear()
        rc._LATEST_IMAGES.update(imgs)

        real_execute = conn.execute
        calls = {"n": 0}

        async def flaky(sql, *a, **kw):
            if sql.startswith("UPDATE channel_profiles SET updated_at"):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("chunk 2 실패")
            return await real_execute(sql, *a, **kw)

        monkeypatch.setattr(conn, "execute", flaky)
        await rc._persist_profiles()            # 내부에서 삼키고 로그만 남긴다
        monkeypatch.undo()
        await conn.rollback()                   # 커밋 안 된 트랜잭션을 정리

        rows = await (await conn.execute(
            "SELECT updated_at FROM channel_profiles")).fetchall()
        assert len(rows) == n, "실패했는데 행이 사라졌다"
        assert all(r[0] == old for r in rows), "커밋 전 chunk가 남았다"

        # 다음 실행은 정상적으로 전부 처리한다(멱등 재시도)
        await rc._persist_profiles()
        rows = await (await conn.execute(
            "SELECT updated_at FROM channel_profiles")).fetchall()
        assert len(rows) == n
        assert all(r[0] > old for r in rows)

    db(go())


# ── 4. chat / monitor 잠금 복구 ────────────────────────────────────────────
def test_mark_synced_retries_on_a_dedicated_connection():
    import inspect

    from cogs import chzzk_chat
    src = inspect.getsource(chzzk_chat.ChzzkChatCog._mark_synced)
    assert "db_write_isolated" in src, "재시도·rollback 래퍼 없이 직접 커밋하고 있다"
    assert "db_write(" not in src, _SHARED_RETRY_IS_WORSE


def test_monitor_writes_once_after_notifications():
    """알림 전송 중에 쓰기 트랜잭션을 열어 두지 않는다."""
    import inspect

    import chzzk_monitor
    src = inspect.getsource(chzzk_monitor._check_once)
    assert "pending.append" in src
    assert "db_write_isolated" in src
    assert "db_write(" not in src, _SHARED_RETRY_IS_WORSE
    # 루프 안에서 직접 UPDATE하지 않는다
    loop_part = src[src.index("for row in rows"):src.index("if pending")]
    assert "UPDATE chzzk_subscriptions" not in loop_part


_SHARED_RETRY_IS_WORSE = (
    "공유 연결에서 재시도하면 안 된다 — busy_timeout 10초 × 4회 + 백오프 = 실측 "
    "43.3초 동안 그 연결의 작업 큐가 통째로 막힌다(공개 /main이 그 뒤에 줄을 선다). "
    "고치려던 증상을 오히려 키운다. 짧고 독립적인 쓰기는 db_write_isolated로."
)


def test_isolated_write_has_a_hard_time_bound():
    """전용 연결 쓰기의 최악 소요가 코드 상수로 계산된다."""
    from utils.db_write import isolated_worst_case_seconds
    worst = isolated_worst_case_seconds(budget_seconds=3.0)
    assert worst <= 3.5, worst
    # 공유 연결 재시도(4회 × busy_timeout 10초)보다 한 자릿수 작아야 의미가 있다
    from database import db as database_db
    shared_worst = database_db.BUSY_TIMEOUT_MS / 1000 * 4
    assert worst * 5 < shared_worst, (worst, shared_worst)


def test_isolated_write_gives_up_within_budget_and_recovers(db):
    """잠금 → rollback → 예산 안에서 포기 → 잠금이 풀리면 다음 시도에 성공."""
    import sqlite3

    import database
    from utils.db_write import db_write_isolated, reset_write_stats, write_stats

    async def go():
        reset_write_stats()

        async def work(conn):
            await conn.execute(
                "INSERT INTO chzzk_subscriptions (guild_id, chzzk_channel_id, "
                "discord_channel, is_live) VALUES (99,'ch99','1',1) "
                "ON CONFLICT DO NOTHING")

        blocker = sqlite3.connect(str(database.DB_PATH), isolation_level=None)
        blocker.execute("PRAGMA busy_timeout=0")
        blocker.execute("BEGIN EXCLUSIVE")
        t0 = time.perf_counter()
        ok = await db_write_isolated(database.DB_PATH, work, what="test",
                                     budget_seconds=1.0)
        held = time.perf_counter() - t0
        blocker.execute("COMMIT")
        blocker.close()

        assert ok is False, "잠겨 있는데 성공했다고 보고했다"
        assert held < 2.0, f"예산 1초를 크게 넘겼다: {held:.2f}s"
        s = write_stats()
        assert s["rollbacks"] >= 1, "잠금 실패 후 롤백하지 않았다"
        assert s["giveups"] == 1

        # 잠금이 풀리면 다음 주기가 그대로 성공한다(자동 복구)
        assert await db_write_isolated(database.DB_PATH, work, what="test")
        got = await (await (await sc.get_db()).execute(
            "SELECT COUNT(*) FROM chzzk_subscriptions WHERE guild_id=99"
        )).fetchone()
        assert got[0] == 1

    db(go())


def test_monitor_is_live_write_is_idempotent(db):
    async def go():
        conn = await sc.get_db()
        await conn.execute(
            "INSERT INTO chzzk_subscriptions (guild_id, chzzk_channel_id, "
            "discord_channel, is_live) VALUES (1,'ch','1',0)")
        await conn.commit()
        row = await (await conn.execute(
            "SELECT id FROM chzzk_subscriptions")).fetchone()
        for _ in range(3):
            await conn.executemany(
                "UPDATE chzzk_subscriptions SET is_live=? WHERE id=?",
                [(1, row[0])])
            await conn.commit()
        got = await (await conn.execute(
            "SELECT is_live FROM chzzk_subscriptions")).fetchone()
        assert got[0] == 1

    db(go())


def test_other_connection_never_sees_partial_ranking(db):
    """**외부 요청 관점**에서 부분 랭킹이 보이지 않는다.

    위의 `test_bulk_write_shrinks_...`는 같은 공유 연결에서 읽으므로
    read-your-own-writes 때문에 섞여 보일 수 있다 — 그건 SQLite의 정상 동작이고
    측정 대상도 '노출 창의 길이'였다. 진짜로 보장해야 하는 것은
    **다른 연결(= 다른 프로세스/다른 요청)에서는 커밋 전 값이 전혀 보이지 않는다**는
    것이다. 여기서는 별도 연결을 열어 그걸 직접 확인한다.
    """
    import sqlite3

    import database

    def _generations() -> set[str]:
        # 공유 연결이 아닌, 완전히 별개의 연결로 읽는다.
        conn = sqlite3.connect(database.DB_PATH)
        try:
            conn.execute("PRAGMA busy_timeout=2000")
            return {r[0][:3] for r in conn.execute(
                "SELECT representative_clip_uid FROM singcup_streamers")}
        finally:
            conn.close()

    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        await conn.execute("DELETE FROM singcup_streamers")
        await sc._upsert_streamers_bulk(_rows(200, rep="old"), now)
        await conn.commit()
        assert _generations() == {"old"}

        # 커밋하지 않은 채로 새 세대를 전부 쓴다
        await sc._upsert_streamers_bulk(_rows(200, rep="new"), now + 1)
        assert _generations() == {"old"}, "커밋 전 값이 다른 연결에 보였다"

        await conn.commit()
        assert _generations() == {"new"}

        # 중간에 끊기면(rollback) 새 세대는 흔적도 남지 않는다
        await sc._upsert_streamers_bulk(_rows(200, rep="xyz"), now + 2)
        await conn.rollback()
        assert _generations() == {"new"}

    db(go())


# ── 6. 공개 조회는 여전히 쓰기가 없다 ──────────────────────────────────────
def test_public_main_still_writes_nothing(db):
    import sqlite3

    import database

    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        await conn.execute(
            "INSERT INTO singcup_clips (clip_uid,event_id,owner_channel_id,video_id,"
            "clip_title,thumbnail_image_url,description,created_at,heart_count,"
            "view_count,duration,adult,metrics_ok,active,missing_scan_count,"
            "first_collected_at,last_collected_at,row_updated_at) "
            "VALUES ('c1',?,'o1','v','t','','#싱드컵',?,1,1,60,0,1,1,0,?,?,?)",
            (sc.EVENT_ID, now, now, now, now))
        await conn.execute(
            "INSERT INTO singcup_streamers (channel_id,event_id,channel_name,"
            "representative_clip_uid,tagged_clip_count,row_updated_at) "
            "VALUES ('o1',?,'o1','c1',1,?)", (sc.EVENT_ID, now))
        await conn.commit()
        snap = "SELECT * FROM singcup_streamers"
        before = [tuple(r) for r in sqlite3.connect(database.DB_PATH).execute(snap)]
        await sc.load_main_entry(limit=10)
        after = [tuple(r) for r in sqlite3.connect(database.DB_PATH).execute(snap)]
        assert before == after

    db(go())
