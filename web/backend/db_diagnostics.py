"""DB 용량·증가 속도 진단.

목적은 '500MB를 언제 넘기는가'를 추측이 아니라 측정으로 답하는 것이다.

⚠️ 완전한 읽기 전용은 아니다. wal_checkpoint(PASSIVE)는 조건이 맞으면 실제로
WAL 내용을 본 DB 파일로 옮기고 WAL을 줄인다(다른 읽기/쓰기를 막지는 않는다).
데이터 자체는 바뀌지 않지만 '읽기만 한다'는 표현은 부정확하다.
VACUUM/DELETE/TRUNCATE 등 되돌리기 어려운 작업은 여기에 두지 않는다.

응답에 파일 경로·접속 문자열을 넣지 않는다 — 진단값만 필요하고, 경로가 로그나
스크린샷으로 흘러나가면 그 자체가 정보 노출이다.
"""
from __future__ import annotations

import asyncio
import os
import time

from database import DB_PATH, get_db

# 증가 속도를 잴 대상. (테이블, 시각 컬럼) — 시각 컬럼이 없으면 행 수만 센다.
_TIME_COLUMNS: dict[str, str | None] = {
    "rising_live_snapshots": "collected_at",
    "rising_hourly_rollup": "hour_ts",
    "rising_collect_runs": "collected_at",
    "rising_channel_stats": "last_seen",
    "singcup_snapshots": "collected_at",
    "singcup_snapshot_hourly": "hour_ts",
    "singcup_clips": "first_collected_at",
    "singcup_clip_scan": "checked_at",
    "singcup_streamers": "row_updated_at",
    "singcup_feeds": "first_collected_at",
    "singcup_collect_runs": "started_at",
    "chzzk_chat_log": "created_at",
    "channel_profiles": "updated_at",
}
# 프루닝이 걸려 있는 테이블 — 없는 곳이 곧 '무한 증가 후보'다
_PRUNED = {"rising_live_snapshots", "rising_hourly_rollup", "channel_profiles",
           "singcup_snapshots", "chzzk_chat_log"}


def _file_sizes() -> dict:
    out = {}
    for label, suffix in (("db", ""), ("wal", "-wal"), ("shm", "-shm")):
        try:
            out[f"{label}_bytes"] = os.path.getsize(DB_PATH + suffix)
        except OSError:
            out[f"{label}_bytes"] = 0
    out["total_bytes"] = sum(v for k, v in out.items() if k.endswith("_bytes"))
    return out


async def _pragmas(db) -> dict:
    async def one(sql):
        r = await (await db.execute(sql)).fetchone()
        return r[0] if r else None
    page_size = int(await one("PRAGMA page_size") or 0)
    page_count = int(await one("PRAGMA page_count") or 0)
    freelist = int(await one("PRAGMA freelist_count") or 0)
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist,
        "used_bytes": (page_count - freelist) * page_size,
        # DELETE만으로는 파일이 줄지 않는다. 이 값이 크면 VACUUM으로 회수 가능한 양이다
        # (VACUUM은 위험 작업이라 여기서 실행하지 않는다).
        "reclaimable_bytes": freelist * page_size,
        "reclaimable_ratio": round(freelist / page_count, 4) if page_count else 0.0,
        "journal_mode": await one("PRAGMA journal_mode"),
        "synchronous": await one("PRAGMA synchronous"),
        "busy_timeout": await one("PRAGMA busy_timeout"),
        "wal_autocheckpoint": await one("PRAGMA wal_autocheckpoint"),
    }


async def _object_sizes(db) -> tuple[list[dict], bool]:
    """dbstat가 있으면 정확한 객체별 크기, 없으면 None(호출자가 추정으로 대체)."""
    try:
        rows = await (await db.execute(
            "SELECT name, SUM(pgsize) AS bytes FROM dbstat "
            "GROUP BY name ORDER BY SUM(pgsize) DESC")).fetchall()
        return ([{"name": r["name"], "bytes": int(r["bytes"] or 0)} for r in rows], True)
    except Exception:
        return ([], False)


async def _table_stats(db, now: int) -> list[dict]:
    names = [r["name"] for r in await (await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")).fetchall()]
    out = []
    for t in names:
        try:
            n = (await (await db.execute(f'SELECT COUNT(*) c FROM "{t}"')).fetchone())["c"]
        except Exception:
            continue
        row: dict = {"table": t, "rows": int(n), "pruned": t in _PRUNED}
        col = _TIME_COLUMNS.get(t)
        if col and n:
            try:
                r = await (await db.execute(
                    f'SELECT MIN("{col}") lo, MAX("{col}") hi FROM "{t}"')).fetchone()
                row["oldest_at"], row["newest_at"] = r["lo"], r["hi"]
                for label, secs in (("1h", 3600), ("24h", 86400), ("7d", 7 * 86400)):
                    c = await (await db.execute(
                        f'SELECT COUNT(*) c FROM "{t}" WHERE "{col}" >= ?',
                        (now - secs,))).fetchone()
                    row[f"added_{label}"] = int(c["c"])
            except Exception:
                pass
        out.append(row)
    out.sort(key=lambda r: -r["rows"])
    return out


async def collect(volume_bytes: int | None = None) -> dict:
    """진단 스냅샷 하나. 읽기 + PASSIVE 체크포인트만 수행한다."""
    db = await get_db()
    now = int(time.time())

    # PASSIVE는 다른 읽기/쓰기를 막지 않는다(FULL/TRUNCATE와 다르다).
    # 다만 '아무 일도 안 한다'는 뜻은 아니다 — 조건이 맞으면 WAL을 본 파일로 옮긴다.
    try:
        wal = await (await db.execute("PRAGMA wal_checkpoint(PASSIVE)")).fetchone()
        checkpoint = {"busy": wal[0], "wal_pages": wal[1], "checkpointed": wal[2]}
    except Exception as e:
        checkpoint = {"error": str(e)[:120]}

    files = _file_sizes()
    prag = await _pragmas(db)
    objects, dbstat_ok = await _object_sizes(db)
    tables = await _table_stats(db, now)

    # 하루 증가량 → 소진일. 24시간 표본이 있는 테이블만 근거로 삼는다(추측 금지).
    daily_rows = sum(t.get("added_24h", 0) for t in tables)
    bytes_per_row = (prag["used_bytes"] / sum(t["rows"] for t in tables)
                     if sum(t["rows"] for t in tables) else 0)
    daily_bytes = daily_rows * bytes_per_row
    vol = volume_bytes or int(os.getenv("DB_VOLUME_BYTES", str(500 * 1024 * 1024)))
    remaining = max(0, vol - files["total_bytes"])
    growth = {
        "rows_per_day": daily_rows,
        "bytes_per_row_estimate": round(bytes_per_row, 1),
        "bytes_per_day_estimate": round(daily_bytes),
        "projected_1d": round(files["total_bytes"] + daily_bytes),
        "projected_7d": round(files["total_bytes"] + daily_bytes * 7),
        "projected_30d": round(files["total_bytes"] + daily_bytes * 30),
        "volume_bytes": vol,
        "remaining_bytes": remaining,
        "days_until_full": (round(remaining / daily_bytes, 1)
                            if daily_bytes > 0 else None),
    }
    # 프루닝이 없는데 계속 늘어나는 테이블 = 다음 용량 사고의 후보
    unbounded = [t["table"] for t in tables
                 if not t["pruned"] and t.get("added_24h", 0) > 0]
    return {
        "collected_at": now,
        "files": files,
        "pragmas": prag,
        "wal_checkpoint": checkpoint,
        "dbstat_available": dbstat_ok,
        "objects": objects[:40],
        "tables": tables,
        "growth": growth,
        "unbounded_tables": unbounded,
    }


async def integrity_check(quick: bool = True) -> dict:
    """무결성 검사.

    quick_check는 integrity_check보다 훨씬 가볍다(인덱스 정합성까지는 안 본다).
    일상 점검은 quick, 백업 직후 검증처럼 확실히 봐야 할 때만 full을 쓴다.
    full은 큰 DB에서 수십 초 동안 페이지를 전부 훑으므로 자주 돌리면 안 된다.
    """
    db = await get_db()
    t0 = time.perf_counter()
    sql = "PRAGMA quick_check" if quick else "PRAGMA integrity_check"
    rows = await (await db.execute(sql)).fetchall()
    msgs = [r[0] for r in rows]
    return {"mode": "quick" if quick else "full", "ok": msgs == ["ok"],
            "messages": msgs[:20],
            "elapsed_ms": int((time.perf_counter() - t0) * 1000)}


# ── 운영 부하 제한 ─────────────────────────────────────────────────────────
# COUNT(*)와 dbstat는 테이블 전체를 훑는다. 진단을 자주 부르면 그 자체가 부하가
# 되므로 결과를 캐시하고, 동시에 두 개가 돌지 않게 막고, 시간을 제한한다.
_CACHE_TTL = float(os.getenv("DB_DIAG_CACHE_TTL_SECONDS", "600"))
_TIMEOUT = float(os.getenv("DB_DIAG_TIMEOUT_SECONDS", "30"))
_cache: tuple[float, dict] | None = None
_lock = asyncio.Lock()


async def collect_cached(force: bool = False) -> dict:
    """10분 캐시 + 동시 실행 1개 + 타임아웃."""
    global _cache
    now = time.time()
    if not force and _cache and now - _cache[0] < _CACHE_TTL:
        return {**_cache[1], "cached": True,
                "cache_age_seconds": round(now - _cache[0], 1)}
    if _lock.locked():
        # 이미 도는 중이면 새로 시작하지 않는다 — 있으면 오래된 값이라도 준다
        if _cache:
            return {**_cache[1], "cached": True, "stale": True,
                    "note": "다른 진단이 실행 중입니다"}
        raise RuntimeError("진단이 이미 실행 중입니다. 잠시 후 다시 시도하세요.")
    async with _lock:
        t0 = time.perf_counter()
        try:
            data = await asyncio.wait_for(collect(), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            raise RuntimeError(f"진단이 {_TIMEOUT}초 안에 끝나지 않았습니다.") from None
        data["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        data["cached"] = False
        _cache = (time.time(), data)
        return data


def reset_cache():
    global _cache
    _cache = None
