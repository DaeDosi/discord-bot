"""CHZZK-STATS-PERF-2 — `newcomers`의 7일 롤업 집계가 커버링 인덱스를 타야 한다.

## 무엇이 문제였나

`/api/rising/newcomers`의 cold 응답이 운영에서 2.8~3.0초였고, **그 계산이 도는 동안
다른 빠른 API까지 밀렸다.** 구간별 실측(롤업 100.8만 행 fixture)에서 원인이 갈렸다:

    2_rollup_GROUPBY_쿼리   656.5 ms   ← 전체 676ms의 97%
    나머지 9개 구간 합        19.7 ms

그리고 "다른 API가 밀리는" 메커니즘은 **이 쿼리가 이벤트 루프를 잡아서가 아니다.**
`database.get_db()`는 **전역 단일 aiosqlite 커넥션**을 돌려주고, aiosqlite는 커넥션당
워커 스레드 하나로 쿼리를 **직렬 처리**한다. 그래서 무거운 쿼리가 도는 동안 뒤에 온
쿼리가 큐에서 기다린다. 이 쿼리만 돌렸을 때의 실측이 그 모양 그대로다:

    같이 돈 가벼운 DB 쿼리: 중앙값 1.3ms · **최대 333ms**  ← 뒤에 걸린 것만 막힌다
    event-loop heartbeat  : 최대 12.3ms                    ← 아무 일도 안 할 때의
                                                             바닥값 12.7ms와 같다

⚠️ 바닥값과 같다는 것은 "여유가 있다"가 아니라 **"이 방법으로는 더 못 잰다"**는 뜻이다.
   실제로 endpoint 전체를 돌리면 루프는 250ms 가까이 막히는데, 그 원인은 이 쿼리가
   아니라 외부 enrich가 만드는 `httpx.AsyncClient()`(이 머신 실측 264~346ms의 **동기**
   초기화)다. 이 파일의 인덱스는 그 부분을 건드리지 않는다.

## 무엇을 고쳤나

`(chzzk_channel_id, hour_ts, sum_viewers, snaps)` 커버링 인덱스 하나.
기존 `idx_rising_roll_channel(chzzk_channel_id, hour_ts)`는 GROUP BY 순서는 주지만
`sum_viewers`·`snaps`가 없어 **행마다 테이블을 다시 읽었다**(100만 행 × 룩업).

    PLAN: SCAN ... USING INDEX idx_rising_roll_channel
       →  SCAN ... USING COVERING INDEX idx_rising_roll_channel_cover
    쿼리 단독      605ms → 187ms  (롤업 100.8만 행)
    같이 돈 가벼운 DB 쿼리 최대  333ms → 109ms  (롤업 50.4만 행)

**응답 계약은 한 글자도 바뀌지 않는다** — 인덱스는 같은 결과를 더 빨리 낼 뿐이다.
이 파일은 그 두 가지(계획이 커버링 인덱스를 탄다 / 결과가 동일하다)를 고정한다.
"""
import time

import pytest

AGG_SQL = (
    "SELECT chzzk_channel_id, "
    "       CAST(SUM(sum_viewers) AS REAL) / NULLIF(SUM(snaps),0) AS avg7, "
    "       SUM(snaps) AS snaps7 "
    "FROM rising_hourly_rollup WHERE hour_ts >= ? GROUP BY chzzk_channel_id"
)
COVER_INDEX = "idx_rising_roll_channel_cover"
TS = 1_789_000_000


def _cid(i: int) -> str:
    return f"{i:032x}"


@pytest.fixture
def rollup(db):
    """롤업만 비우고 채운다(공용 conftest는 이 표를 모른다)."""
    import database

    async def _clear():
        conn = await database.get_db()
        await conn.execute("DELETE FROM rising_hourly_rollup")
        await conn.commit()

    db(_clear())
    return db


async def _seed(channels: int, hours: int, *, ts: int = TS):
    """채널 × 시간 격자로 롤업을 채운다. 값은 결정적이다(랜덤 금지 — 결과 비교용)."""
    from database import get_db
    conn = await get_db()
    rows = []
    for i in range(channels):
        c = _cid(i)
        for h in range(hours):
            # sum_viewers를 채널·시간의 함수로 둬 기대값을 손으로 계산할 수 있게 한다
            rows.append((ts - h * 3600, c, (i + 1) * 10 + h, 3))
    await conn.executemany(
        "INSERT OR REPLACE INTO rising_hourly_rollup "
        "(hour_ts, chzzk_channel_id, sum_viewers, snaps) VALUES (?,?,?,?)", rows)
    await conn.commit()


#: `sqlite3`는 **준비된 문을 캐시**한다. 인덱스를 DROP해도 같은 SQL 문자열이면
#: 옛 계획이 그대로 나온다(실측: `sqlite_master`에서 사라졌는데 계획은 COVERING).
#: 그래서 계획을 다시 볼 때는 주석 한 줄로 캐시 키를 바꿔 재컴파일을 강제한다.
_bust = 0


def _fresh(sql: str) -> str:
    global _bust
    _bust += 1
    return "-- cachebust " + str(_bust) + chr(10) + sql


async def _plan(conn, sql, params):
    rows = await (await conn.execute("EXPLAIN QUERY PLAN " + _fresh(sql), params)).fetchall()
    return " | ".join(r[-1] for r in rows)


# ── 1. 인덱스가 존재하고 쿼리가 그것을 탄다 ─────────────────────────────────

def test_covering_index_exists(rollup):
    """`init_db()`가 이 인덱스를 만든다. append-only 목록에 있어야 한다."""
    from database import get_db

    async def _go():
        conn = await get_db()
        row = await (await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            (COVER_INDEX,))).fetchone()
        return row["sql"] if row else None

    sql = rollup(_go())
    assert sql is not None, f"{COVER_INDEX} 인덱스가 없다"
    # 순서가 중요하다: GROUP BY 키가 앞, 필터가 그다음, 집계 컬럼이 마지막.
    norm = sql.replace(" ", "").lower()
    assert "(chzzk_channel_id,hour_ts,sum_viewers,snaps)" in norm, \
        f"컬럼 순서가 계약과 다르다: {sql}"


def test_query_uses_covering_index(rollup):
    """계획이 **COVERING INDEX**여야 한다 — 테이블을 다시 읽으면 개선이 사라진다."""
    from database import get_db

    async def _go():
        await _seed(channels=40, hours=48)
        conn = await get_db()
        await conn.execute("ANALYZE")          # 계획이 통계를 보게 한다
        return await _plan(conn, AGG_SQL, (TS - 7 * 86400,))

    plan = rollup(_go())
    assert "COVERING INDEX" in plan.upper(), f"커버링 인덱스를 타지 않는다: {plan}"
    assert COVER_INDEX in plan, f"의도한 인덱스가 아니다: {plan}"


# ── 2. 결과가 동일하다 (응답 계약 불변) ─────────────────────────────────────

def test_result_is_identical_with_and_without_index(rollup):
    """인덱스는 **같은 결과를 더 빨리** 낼 뿐이다. 값이 달라지면 그건 버그다."""
    from database import get_db

    async def _go():
        await _seed(channels=30, hours=72)
        conn = await get_db()
        with_idx = await (await conn.execute(AGG_SQL, (TS - 7 * 86400,))).fetchall()
        # 인덱스를 잠시 떼고 같은 쿼리를 돌린다
        await conn.execute(f"DROP INDEX IF EXISTS {COVER_INDEX}")
        await conn.commit()
        without = await (await conn.execute(_fresh(AGG_SQL), (TS - 7 * 86400,))).fetchall()
        # 반드시 되돌린다 — 다음 테스트가 이 인덱스를 기대한다
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS {COVER_INDEX} ON rising_hourly_rollup"
            "(chzzk_channel_id, hour_ts, sum_viewers, snaps)")
        await conn.commit()
        def norm(rs):
            return sorted((r["chzzk_channel_id"], r["avg7"], r["snaps7"]) for r in rs)

        return norm(with_idx), norm(without)

    a, b = rollup(_go())
    assert a == b, "인덱스 유무로 집계 결과가 달라졌다"
    assert len(a) == 30


def test_aggregate_values_are_correct(rollup):
    """집계식 자체를 고정한다 — 인덱스를 바꾸다 SQL을 건드리면 여기서 걸린다."""
    from database import get_db

    async def _go():
        await _seed(channels=3, hours=4)
        conn = await get_db()
        rows = await (await conn.execute(AGG_SQL, (TS - 7 * 86400,))).fetchall()
        return {r["chzzk_channel_id"]: (r["avg7"], r["snaps7"]) for r in rows}

    got = rollup(_go())
    for i in range(3):
        base = (i + 1) * 10
        total = sum(base + h for h in range(4))     # sum_viewers 합
        snaps = 3 * 4
        avg, sn = got[_cid(i)]
        assert sn == snaps
        assert abs(avg - total / snaps) < 1e-9, f"채널 {i} 평균이 다르다"


def test_hour_window_boundary_is_inclusive(rollup):
    """`hour_ts >= ?` 경계 — 창 밖 데이터가 섞이거나 경계 행이 빠지면 안 된다."""
    from database import get_db

    async def _go():
        conn = await get_db()
        cutoff = TS - 7 * 86400
        await conn.executemany(
            "INSERT OR REPLACE INTO rising_hourly_rollup "
            "(hour_ts, chzzk_channel_id, sum_viewers, snaps) VALUES (?,?,?,?)",
            [(cutoff, _cid(1), 100, 1),        # 경계 위 — 포함
             (cutoff - 1, _cid(1), 999, 1),    # 경계 밖 — 제외
             (cutoff - 86400, _cid(2), 500, 1)])  # 전부 밖 — 채널 자체가 안 나옴
        await conn.commit()
        rows = await (await conn.execute(AGG_SQL, (cutoff,))).fetchall()
        return {r["chzzk_channel_id"]: (r["avg7"], r["snaps7"]) for r in rows}

    got = rollup(_go())
    assert _cid(2) not in got, "창 밖 채널이 들어왔다"
    assert got[_cid(1)] == (100.0, 1), f"경계 처리가 어긋났다: {got}"


def test_empty_rollup_returns_nothing(rollup):
    """빈 데이터에서 오류가 아니라 빈 결과여야 한다."""
    from database import get_db

    async def _go():
        conn = await get_db()
        return await (await conn.execute(AGG_SQL, (TS - 7 * 86400,))).fetchall()

    assert rollup(_go()) == []


# ── 3. 재실행 안전(migration) ───────────────────────────────────────────────

def test_init_db_is_repeatable(rollup):
    """`init_db()`를 여러 번 불러도 인덱스가 중복 생성되거나 실패하지 않는다."""
    import database

    async def _go():
        await database.init_db()
        await database.init_db()
        conn = await database.get_db()
        rows = await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            (COVER_INDEX,))).fetchall()
        return len(rows)

    assert rollup(_go()) == 1


# ── 4. 성능 — 절대 시간이 아니라 **계획과 상대 비교**로 고정한다 ────────────

def test_covering_index_avoids_table_lookups(rollup):
    """같은 쿼리를 인덱스 있음/없음으로 돌려 **상대 비교**한다.

    절대 ms로 단언하면 CI·부하에 따라 흔들린다. 대신 두 가지를 본다:
      · 계획 문자열이 `COVERING INDEX`인가 (구조적 증거)
      · 커버링 쪽이 더 느리지 않은가 (역행 방지, 여유 있는 상한)
    """
    from database import get_db

    async def _go():
        await _seed(channels=300, hours=168)     # 50,400행 — 테스트에서 감당 가능한 규모
        conn = await get_db()
        await conn.execute("ANALYZE")

        plan_with = await _plan(conn, AGG_SQL, (TS - 7 * 86400,))
        t = time.perf_counter()
        for _ in range(3):
            await (await conn.execute(_fresh(AGG_SQL), (TS - 7 * 86400,))).fetchall()
        with_ms = (time.perf_counter() - t) / 3 * 1000

        await conn.execute(f"DROP INDEX IF EXISTS {COVER_INDEX}")
        await conn.commit()          # DDL을 확정해야 다음 계획에 반영된다
        plan_without = await _plan(conn, AGG_SQL, (TS - 7 * 86400,))
        t = time.perf_counter()
        for _ in range(3):
            await (await conn.execute(_fresh(AGG_SQL), (TS - 7 * 86400,))).fetchall()
        without_ms = (time.perf_counter() - t) / 3 * 1000

        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS {COVER_INDEX} ON rising_hourly_rollup"
            "(chzzk_channel_id, hour_ts, sum_viewers, snaps)")
        await conn.commit()
        return plan_with, plan_without, with_ms, without_ms

    plan_with, plan_without, with_ms, without_ms = rollup(_go())
    assert "COVERING INDEX" in plan_with.upper(), plan_with
    assert "COVERING INDEX" not in plan_without.upper(), \
        f"인덱스를 떼었는데도 커버링이라면 비교가 무의미하다: {plan_without}"
    # 여유 있는 상한: 커버링이 **명백히 느려지면** 회귀다(측정 잡음 흡수).
    assert with_ms <= without_ms * 1.5, \
        f"커버링 인덱스가 더 느리다: {with_ms:.1f}ms vs {without_ms:.1f}ms"


def test_write_path_still_works_with_extra_index(rollup):
    """인덱스가 하나 늘어도 롤업 upsert가 정상 동작한다(쓰기 계약)."""
    from database import get_db

    async def _go():
        conn = await get_db()
        await conn.execute(
            "INSERT OR REPLACE INTO rising_hourly_rollup "
            "(hour_ts, chzzk_channel_id, sum_viewers, snaps) VALUES (?,?,?,?)",
            (TS, _cid(9), 10, 1))
        # 같은 PK로 다시 쓰면 덮어써야 한다
        await conn.execute(
            "INSERT OR REPLACE INTO rising_hourly_rollup "
            "(hour_ts, chzzk_channel_id, sum_viewers, snaps) VALUES (?,?,?,?)",
            (TS, _cid(9), 99, 2))
        await conn.commit()
        rows = await (await conn.execute(AGG_SQL, (TS - 7 * 86400,))).fetchall()
        return [(r["chzzk_channel_id"], r["avg7"], r["snaps7"]) for r in rows]

    got = rollup(_go())
    assert got == [(_cid(9), 49.5, 2)], f"upsert 후 집계가 어긋났다: {got}"
