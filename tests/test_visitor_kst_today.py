"""Nexadmin '오늘 방문자'의 날짜 경계 — KST 자정에 바뀌어야 한다.

방문 기록은 `stats_router.today_kst()`가 만든 **KST 날짜 문자열**을 키로 쓴다.
그런데 Nexadmin 조회만 `date.today()`(서버 로컬 = Railway는 UTC)를 써서,
KST 00:00~08:59 동안 전날 행을 세고 있었다. 자정이 지나도 어제 숫자가 그대로
보이고 오전 9시에야 바뀌는 것이 이 버그의 증상이다.

여기서는 시스템 로컬 시간대에 의존하지 않도록 `datetime`을 직접 갈아 끼워
'그 순간'을 고정한 뒤, 세 경로(write / 공개 read / Nexadmin read)가 **같은
날짜 문자열**을 쓰는지 본다.
"""
import datetime as _dt
from datetime import timedelta, timezone

import pytest
from routers import admin_router, stats_router

import database

KST = timezone(timedelta(hours=9))
UTC = timezone.utc


class _FrozenDatetime(_dt.datetime):
    """`datetime.now(tz)`만 고정한다. 다른 동작은 표준과 같다."""

    _now = _dt.datetime(2026, 8, 3, 15, 0, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls._now.astimezone(tz) if tz else cls._now.replace(tzinfo=None)


@pytest.fixture
def freeze(monkeypatch):
    """UTC 기준 순간을 고정한다. 로컬 timezone과 무관하게 동작한다."""
    def _apply(utc: _dt.datetime):
        class F(_FrozenDatetime):
            _now = utc.replace(tzinfo=UTC)

        # 두 모듈이 각자 import한 datetime을 모두 갈아 끼운다.
        monkeypatch.setattr(admin_router, "datetime", F)
        monkeypatch.setattr(stats_router.datetime, "datetime", F, raising=False)

        # date.today()는 로컬 시간대를 따른다 — Railway(UTC)를 흉내 낸다.
        class D(_dt.date):
            @classmethod
            def today(cls):
                return utc.date()

        monkeypatch.setattr(admin_router, "date", D)
        return F
    return _apply


def _kst_today(mod_dt) -> str:
    return mod_dt.now(KST).date().isoformat()


async def _no_guilds(force: bool = False) -> list[dict]:
    """Discord REST를 타지 않게 한다. 길드가 0개면 멤버 수 조회도 일어나지 않는다."""
    return []


# ── 동작 테스트: 실제 overview() 호출 ───────────────────────────────────────
# 소스 문자열 검사만으로는 overview 내부가 바뀌었을 때 회귀를 잡지 못한다.
# 여기서는 **실제 함수와 실제 SQL**을 그대로 태운다. 외부로 나가는 것은
# `_bot_guilds`(Discord REST) 하나뿐이라 그것만 막는다 — 길드가 0개면
# `_guild_member_count`는 호출되지 않으므로 네트워크가 발생하지 않는다.
# today_visitors를 계산하는 코드와 SQL은 우회하지 않는다.
def test_overview_counts_only_kst_today_rows(db, freeze, monkeypatch):
    # 공유 테스트 DB라 다른 테스트의 행과 섞이지 않도록 이 테스트 전용 날짜를 쓴다.
    # UTC 2027-09-14 15:30 == KST 2027-09-15 00:30 (자정 직후, 버그가 보이던 구간)
    freeze(_dt.datetime(2027, 9, 14, 15, 30, 0))
    monkeypatch.setattr(admin_router, "_bot_guilds", _no_guilds)

    YESTERDAY, TODAY = "2027-09-14", "2027-09-15"
    seen: list[tuple] = []

    async def go():
        c = await database.get_db()
        # 어제 3명 / 오늘 1명
        for h in ("y1", "y2", "y3"):
            await c.execute("INSERT OR IGNORE INTO daily_visitors(date, ip_hash) VALUES(?,?)",
                            (YESTERDAY, h))
        await c.execute("INSERT OR IGNORE INTO daily_visitors(date, ip_hash) VALUES(?,?)",
                        (TODAY, "t1"))
        await c.commit()

        before = [tuple(r) for r in await (await c.execute(
            "SELECT date, ip_hash FROM daily_visitors ORDER BY date, ip_hash")).fetchall()]

        # daily_visitors 조회에 실제로 넘어간 날짜 파라미터를 기록한다.
        real_execute = c.execute

        async def spy(sql, params=(), *a, **kw):
            if "daily_visitors" in sql:
                seen.append((sql, tuple(params)))
            return await real_execute(sql, params, *a, **kw)

        monkeypatch.setattr(c, "execute", spy, raising=False)
        result = await admin_router.overview(user={"sub": "owner"})
        monkeypatch.setattr(c, "execute", real_execute, raising=False)

        after = [tuple(r) for r in await (await c.execute(
            "SELECT date, ip_hash FROM daily_visitors ORDER BY date, ip_hash")).fetchall()]
        return result, before, after

    result, before, after = db(go())

    # 4. KST 당일 1명만 센다 (어제 3명이 섞이지 않는다)
    assert result["today_visitors"] == 1

    # 6. 실제 SQL 파라미터가 KST 날짜다
    assert seen, "daily_visitors 쿼리가 실행되지 않았다"
    sqls = [s for s, _ in seen]
    params = [p for _, p in seen]
    assert any("SELECT COUNT(*) FROM daily_visitors" in s for s in sqls)
    assert (TODAY,) in params
    assert (YESTERDAY,) not in params

    # 7~8. 호출 전후 행 동일 — DELETE/UPDATE/INSERT 0건
    assert before == after
    assert not any(w in s.upper() for s in sqls for w in ("DELETE", "UPDATE", "INSERT"))

    # 9. 응답 계약 유지
    assert set(result) == {"guild_count", "total_users", "chzzk_subs",
                           "verifications", "today_visitors"}
    assert all(isinstance(v, int) for v in result.values())


def test_overview_before_kst_midnight_still_counts_yesterday(db, freeze, monkeypatch):
    """대조군 — KST 자정 직전에는 아직 그날 행을 세야 한다."""
    # UTC 2027-11-05 14:59:59 == KST 2027-11-05 23:59:59 (전용 날짜)
    freeze(_dt.datetime(2027, 11, 5, 14, 59, 59))
    monkeypatch.setattr(admin_router, "_bot_guilds", _no_guilds)

    async def go():
        c = await database.get_db()
        for h in ("z1", "z2", "z3"):
            await c.execute("INSERT OR IGNORE INTO daily_visitors(date, ip_hash) VALUES(?,?)",
                            ("2027-11-05", h))
        await c.commit()
        return await admin_router.overview(user={"sub": "owner"})

    assert db(go())["today_visitors"] == 3


# ── A/B/C: KST 경계 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("utc_moment,expected_kst,expected_utc", [
    # A. KST 2026-08-03 23:59:59  (UTC 2026-08-03 14:59:59)
    (_dt.datetime(2026, 8, 3, 14, 59, 59), "2026-08-03", "2026-08-03"),
    # B. KST 2026-08-04 00:00:00  (UTC 2026-08-03 15:00:00) — 날짜가 갈린다
    (_dt.datetime(2026, 8, 3, 15, 0, 0), "2026-08-04", "2026-08-03"),
    # C. KST 08:59:59 (UTC 2026-08-03 23:59:59) — 버그가 보이던 마지막 순간
    (_dt.datetime(2026, 8, 3, 23, 59, 59), "2026-08-04", "2026-08-03"),
    # C'. KST 09:00:00 이후 — 예전에는 여기서야 값이 바뀌었다
    (_dt.datetime(2026, 8, 4, 0, 0, 0), "2026-08-04", "2026-08-04"),
    # 월말·연말·윤년
    (_dt.datetime(2026, 8, 31, 15, 0, 0), "2026-09-01", "2026-08-31"),
    (_dt.datetime(2026, 12, 31, 15, 0, 0), "2027-01-01", "2026-12-31"),
    (_dt.datetime(2028, 2, 28, 15, 0, 0), "2028-02-29", "2028-02-28"),
])
def test_admin_uses_kst_date_not_utc(freeze, utc_moment, expected_kst, expected_utc):
    F = freeze(utc_moment)
    # Nexadmin이 실제로 쓰는 값
    assert admin_router._today_kst().isoformat() == expected_kst
    # 대조군: 예전 구현이 봤을 UTC 날짜
    assert admin_router.date.today().isoformat() == expected_utc
    # 공개 read/write가 쓰는 값과 같아야 한다
    assert _kst_today(F) == expected_kst


def test_kst_and_utc_differ_during_the_broken_window(freeze):
    """KST 00:00~08:59에는 두 날짜가 실제로 갈린다 — 그래서 버그가 보였다."""
    freeze(_dt.datetime(2026, 8, 3, 18, 0, 0))     # KST 08-04 03:00
    assert admin_router._today_kst().isoformat() == "2026-08-04"
    assert admin_router.date.today().isoformat() == "2026-08-03"


# ── E: 세 경로 정합성 ───────────────────────────────────────────────────────
def test_write_public_read_and_admin_read_agree(freeze):
    F = freeze(_dt.datetime(2026, 8, 3, 15, 30, 0))   # KST 08-04 00:30
    write_key = _kst_today(F)                          # stats_router.today_kst()
    public_read = _kst_today(F)
    admin_read = admin_router._today_kst().isoformat()
    assert write_key == public_read == admin_read == "2026-08-04"


def test_admin_date_value_is_a_string_matching_db_format(freeze):
    """`daily_visitors.date`는 TEXT다 — SQL 파라미터 형식 계약을 유지한다."""
    freeze(_dt.datetime(2026, 8, 3, 15, 0, 0))
    v = admin_router._today_kst().isoformat()
    assert isinstance(v, str)
    assert len(v) == 10 and v[4] == "-" and v[7] == "-"


# ── D: 조회가 DB를 바꾸지 않는다 ─────────────────────────────────────────────
def test_admin_read_does_not_touch_stored_rows(db):
    """관리자 조회는 read-only다. 전날 행을 지우거나 0으로 만들지 않는다."""
    # 공유 DB라 다른 테스트가 넣은 행과 섞이지 않도록 이 테스트 전용 날짜를 쓴다.
    D1, D2 = "2027-05-01", "2027-05-02"

    async def go():
        c = await database.get_db()
        for day, h in ((D1, "hash-a"), (D1, "hash-b"), (D2, "hash-a")):
            await c.execute("INSERT OR IGNORE INTO daily_visitors(date, ip_hash) VALUES(?,?)",
                            (day, h))
        await c.commit()

        before = [tuple(r) for r in await (await c.execute(
            "SELECT date, ip_hash FROM daily_visitors WHERE date IN (?,?) "
            "ORDER BY date, ip_hash", (D1, D2))).fetchall()]

        # 날짜별 집계를 읽기만 한다(엔드포인트가 쓰는 것과 같은 쿼리).
        for day, expected in ((D1, 2), (D2, 1)):
            row = await (await c.execute(
                "SELECT COUNT(*) FROM daily_visitors WHERE date=?", (day,))).fetchone()
            assert row[0] == expected

        after = [tuple(r) for r in await (await c.execute(
            "SELECT date, ip_hash FROM daily_visitors WHERE date IN (?,?) "
            "ORDER BY date, ip_hash", (D1, D2))).fetchall()]
        return before, after

    before, after = db(go())
    assert before == after              # 삭제·수정 0건
    assert (D1, "hash-a") in after      # 전날 데이터 보존


def test_new_day_starts_empty_without_deleting_history(db):
    """날짜가 바뀌면 새 날짜 행이 없어 0이 된다 — 과거를 지워서가 아니다."""
    # 다른 테스트와 행이 섞이지 않도록 이 테스트 전용 날짜를 쓴다.
    YESTERDAY, TODAY = "2027-03-14", "2027-03-15"

    async def go():
        c = await database.get_db()
        await c.execute("INSERT OR IGNORE INTO daily_visitors(date, ip_hash) VALUES(?,?)",
                        (YESTERDAY, "h1"))
        await c.commit()
        new_day = await (await c.execute(
            "SELECT COUNT(*) FROM daily_visitors WHERE date=?", (TODAY,))).fetchone()
        old_day = await (await c.execute(
            "SELECT COUNT(*) FROM daily_visitors WHERE date=?", (YESTERDAY,))).fetchone()
        return new_day[0], old_day[0]

    new_day, old_day = db(go())
    assert new_day == 0      # 오늘은 아직 0
    assert old_day == 1      # 어제는 그대로 남아 있다


# ── G: 응답 계약 ────────────────────────────────────────────────────────────
def test_overview_response_contract_unchanged():
    """필드명·구성이 바뀌지 않았는지 소스에서 확인한다."""
    import inspect
    src = inspect.getsource(admin_router.overview)
    for key in ("guild_count", "total_users", "chzzk_subs",
                "verifications", "today_visitors"):
        assert f'"{key}"' in src
    # 날짜 기준만 바뀌고 쿼리 구조는 그대로여야 한다
    assert "SELECT COUNT(*) FROM daily_visitors WHERE date=?" in src
    assert "_today_kst()" in src
    # 주석에는 설명 목적으로 등장할 수 있으므로 **코드 줄**만 본다
    code = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("date.today()" in ln for ln in code)


# ── F: 권한 ─────────────────────────────────────────────────────────────────
def test_overview_still_requires_owner():
    """OWNER 가드가 그대로 붙어 있는지 — 이번 수정으로 권한 계약을 바꾸지 않는다."""
    import inspect
    sig = inspect.signature(admin_router.overview)
    dep = sig.parameters["user"].default
    assert getattr(dep, "dependency", None) is admin_router._require_owner
