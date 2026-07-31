"""싱드컵 스냅샷 보존정책 — 롤업 · 검증 · 프루닝.

⚠️ 이 모듈의 프루닝은 **파괴적 변경**이다. 원본 행을 실제로 지우며,
코드를 revert해도 이미 지워진 원본은 돌아오지 않는다. 롤업(시간별/일별)과
최종 성적만 남는다. 그래서 기본값이 '비활성 + dry-run'이다.

켜기 전 순서 (운영 문서와 동일하게 유지할 것):
  1. Railway 백업 완료 확인            (VACUUM INTO 등 일관 백업)
  2. SINGCUP_SNAPSHOT_PRUNE_DRY_RUN=true 로 리포트 확인
  3. 롤업 ↔ 원본 대조 결과 확인
  4. 그 다음에야 SINGCUP_SNAPSHOT_PRUNE_ENABLED=true

왜 필요한가: 원본은 참가자 수 × 4분 주기로 쌓인다. 실측 198B/행이라 800명 기준
55MB/일이고, 21일 이벤트면 1.1GB로 500MB 볼륨을 넘긴다. 반면 원본을 24시간보다
멀리 읽는 코드는 없다(_delta_maps의 24시간 비교가 최대 소급).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import timedelta, timezone

from singcup_collector import EVENT_ID, event_status

from database import get_db

_KST = timezone(timedelta(hours=9))
HOUR = 3600


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ── 안전장치 ────────────────────────────────────────────────────────────────
# 배포만으로 삭제가 시작되면 안 된다 → 둘 다 명시적으로 바꿔야 실제로 지운다.
PRUNE_ENABLED = _env_bool("SINGCUP_SNAPSHOT_PRUNE_ENABLED", False)
PRUNE_DRY_RUN = _env_bool("SINGCUP_SNAPSHOT_PRUNE_DRY_RUN", True)

# 원본 보존 시간. 24시간 비교(_delta_maps)가 최대 소급이라 +2시간 여유.
RETENTION_HOURS = float(os.getenv("SINGCUP_SNAPSHOT_RETENTION_HOURS", "26"))
# 배치당 삭제 행 수. 전체를 한 트랜잭션으로 묶으면 그동안 봇 프로세스가 막힌다.
PRUNE_BATCH = int(os.getenv("SINGCUP_SNAPSHOT_PRUNE_BATCH", "5000"))
# 배치 사이 양보 시간 — 이 틈에 다른 프로세스가 쓰기 잠금을 가져간다.
PRUNE_BATCH_SLEEP = float(os.getenv("SINGCUP_SNAPSHOT_PRUNE_SLEEP", "0.2"))
# 한 번의 유지보수에서 지울 최대 행 수(폭주 방지).
PRUNE_MAX_ROWS = int(os.getenv("SINGCUP_SNAPSHOT_PRUNE_MAX_ROWS", "200000"))

# 시간별 롤업 -> 일별 압축. 이벤트 종료 후 이 일수가 지나면 하루 1행으로 접는다.
HOURLY_RETENTION_DAYS = float(os.getenv("SINGCUP_HOURLY_RETENTION_DAYS", "30"))
COMPACT_HOURLY_ENABLED = _env_bool("SINGCUP_COMPACT_HOURLY_ENABLED", False)

_ROW_BYTES = 198   # 로컬 실측(인덱스 2개 포함). 회수 용량 '추정'에만 쓴다.


def _log(payload: dict):
    print(f"[singcup_retention] {json.dumps(payload, ensure_ascii=False, default=str)}",
          flush=True)


def _cutoff(now: int) -> int:
    return int(now - RETENTION_HOURS * HOUR)


# ── 1) 롤업 ────────────────────────────────────────────────────────────────
async def build_rollup(now: int, *, dry_run: bool) -> dict:
    """보존 시간 이전 구간을 시간당 1행으로 접는다.

    시간 대표값은 '그 시간의 마지막 행'이다(순위·점수는 누적값이라 평균이 무의미).
    동률이면 id가 큰 쪽 — collected_at만으로는 같은 초에 두 행이 있을 때 결과가
    갈리므로 (collected_at DESC, id DESC)로 결정적으로 고른다.
    UPSERT라 몇 번을 다시 돌려도 결과가 같다(멱등).
    """
    db = await get_db()
    cut = _cutoff(now)
    src = """
        SELECT s.event_id, (s.collected_at/3600)*3600 AS hour_ts, s.owner_channel_id,
               s.clip_uid, s.heart_count, s.view_count, s.follower_count, s.score, s.rank
          FROM singcup_snapshots s
          JOIN (SELECT event_id, owner_channel_id, collected_at/3600 AS h,
                       MAX(collected_at) AS mc
                  FROM singcup_snapshots
                 WHERE collected_at < ?
                 GROUP BY event_id, owner_channel_id, collected_at/3600) k
            ON k.event_id = s.event_id AND k.owner_channel_id = s.owner_channel_id
           AND k.h = s.collected_at/3600 AND k.mc = s.collected_at
         WHERE s.collected_at < ?
           AND s.id = (SELECT MAX(x.id) FROM singcup_snapshots x
                        WHERE x.event_id = s.event_id
                          AND x.owner_channel_id = s.owner_channel_id
                          AND x.collected_at = s.collected_at)
    """
    if dry_run:
        r = await (await db.execute(
            f"SELECT COUNT(*) n FROM ({src})", (cut, cut))).fetchone()
        return {"rollup_rows": int(r["n"]), "applied": False}

    cur = await db.execute(
        f"""INSERT INTO singcup_snapshot_hourly
               (event_id, hour_ts, owner_channel_id, clip_uid, heart_count,
                view_count, follower_count, score, rank)
            {src}
            ON CONFLICT(event_id, hour_ts, owner_channel_id) DO UPDATE SET
                 clip_uid=excluded.clip_uid, heart_count=excluded.heart_count,
                 view_count=excluded.view_count, follower_count=excluded.follower_count,
                 score=excluded.score, rank=excluded.rank""",
        (cut, cut))
    await db.commit()
    return {"rollup_rows": cur.rowcount or 0, "applied": True}


# ── 2) 검증 ────────────────────────────────────────────────────────────────
async def verify_rollup(now: int) -> dict:
    """시간 구간별로 '원본의 모든 스트리머가 롤업에 있는지' 확인한다.

    검사할 속성은 '개수가 같다'가 아니라 '롤업이 원본을 덮는다'이다. 부분 삭제가
    한 번 지나가면 롤업에는 이미 지워진 스트리머까지 남아 있어(정상) 개수가 달라진다.
    위험한 건 그 반대 방향뿐이다 — 원본에 있는데 롤업에 없는 스트리머를 지우면
    그 시간의 그 사람 데이터가 통째로 사라진다.

    한 명이라도 빠지면 그 시간대는 '삭제 금지'로 표시한다.
    """
    db = await get_db()
    cut = _cutoff(now)
    rows = await (await db.execute(
        """SELECT s.event_id,
                  (s.collected_at/3600)*3600 AS hour_ts,
                  COUNT(DISTINCT s.owner_channel_id) AS n_raw,
                  COUNT(DISTINCT CASE WHEN h.owner_channel_id IS NULL
                                      THEN s.owner_channel_id END) AS n_missing
             FROM singcup_snapshots s
             LEFT JOIN singcup_snapshot_hourly h
               ON h.event_id = s.event_id
              AND h.hour_ts = (s.collected_at/3600)*3600
              AND h.owner_channel_id = s.owner_channel_id
            WHERE s.collected_at < ?
            GROUP BY s.event_id, (s.collected_at/3600)*3600""",
        (cut,))).fetchall()
    ok, bad = [], []
    for r in rows:
        rec = {"event_id": r["event_id"], "hour_ts": int(r["hour_ts"]),
               "raw_owners": int(r["n_raw"]),
               "missing_in_rollup": int(r["n_missing"])}
        (ok if rec["missing_in_rollup"] == 0 else bad).append(rec)
    if bad:
        _log({"event": "rollup_mismatch", "level": "error", "hours": len(bad),
              "samples": bad[:5],
              "detail": "해당 시간대 원본은 삭제하지 않습니다"})
    return {"verified_hours": len(ok), "mismatched_hours": len(bad),
            "safe_hours": [(r["event_id"], r["hour_ts"]) for r in ok],
            "mismatches": bad[:20]}


# ── 3) 프루닝 ──────────────────────────────────────────────────────────────
async def _wal_bytes() -> int:
    from database import DB_PATH
    try:
        return os.path.getsize(DB_PATH + "-wal")
    except OSError:
        return 0


async def prune(now: int, safe_hours: list, *, dry_run: bool) -> dict:
    """검증을 통과한 시간대의 원본만 배치로 지운다.

    배치마다 COMMIT하고 잠깐 쉰다 — 전체를 한 트랜잭션으로 묶으면 그동안 같은
    파일을 쓰는 봇 프로세스가 계속 막힌다.
    """
    if not safe_hours:
        return {"deleted": 0, "batches": 0, "applied": False,
                "note": "검증을 통과한 시간대가 없습니다"}
    db = await get_db()
    hours = {int(h) for _, h in safe_hours}
    lo, hi = min(hours), max(hours) + HOUR

    if dry_run:
        r = await (await db.execute(
            "SELECT COUNT(*) n, MIN(collected_at) lo, MAX(collected_at) hi "
            "FROM singcup_snapshots WHERE collected_at >= ? AND collected_at < ?",
            (lo, hi))).fetchone()
        n = int(r["n"] or 0)
        return {"deleted": 0, "would_delete": n, "applied": False,
                "oldest_at": r["lo"], "newest_at": r["hi"],
                "estimated_reclaim_bytes": n * _ROW_BYTES,
                "estimated_batches": (n + PRUNE_BATCH - 1) // PRUNE_BATCH}

    total, batches, retries = 0, 0, 0
    wal_before = await _wal_bytes()
    t0 = time.perf_counter()
    while total < PRUNE_MAX_ROWS:
        b0 = time.perf_counter()
        # 남은 예산보다 크게 지우지 않는다 — 이게 없으면 PRUNE_MAX_ROWS가
        # 배치 크기보다 작을 때 상한이 전혀 걸리지 않는다.
        take = min(PRUNE_BATCH, PRUNE_MAX_ROWS - total)
        try:
            cur = await db.execute(
                "DELETE FROM singcup_snapshots WHERE id IN ("
                "  SELECT id FROM singcup_snapshots "
                "   WHERE collected_at >= ? AND collected_at < ? LIMIT ?)",
                (lo, hi, take))
            deleted = cur.rowcount or 0
            await db.commit()            # 배치마다 커밋해야 잠금이 실제로 풀린다
        except Exception as e:
            retries += 1
            _log({"event": "prune_batch_error", "level": "warning",
                  "detail": str(e)[:160], "retries": retries})
            if retries >= 3:
                break
            await asyncio.sleep(1.0)
            continue
        if deleted == 0:
            break
        total += deleted
        batches += 1
        _log({"event": "prune_batch", "batch": batches, "deleted": deleted,
              "elapsed_ms": int((time.perf_counter() - b0) * 1000),
              "wal_bytes": await _wal_bytes(), "lock_retries": retries})
        await asyncio.sleep(PRUNE_BATCH_SLEEP)   # 다른 프로세스에 쓰기 기회를 준다

    return {"deleted": total, "batches": batches, "lock_retries": retries,
            "applied": True,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "wal_before_bytes": wal_before, "wal_after_bytes": await _wal_bytes()}


# ── 4) 최종 성적 보존 / 시간별 → 일별 압축 ─────────────────────────────────
async def save_final_standings(now: int) -> dict:
    """이벤트가 끝났으면 마지막 상태를 영구 보존 테이블에 박아 둔다.

    어떤 압축 단계에서도 이 테이블은 건드리지 않는다.
    """
    if event_status() != "ENDED":
        return {"saved": 0, "note": "이벤트 진행 중"}
    db = await get_db()
    cur = await db.execute(
        """INSERT INTO singcup_final_standings
               (event_id, owner_channel_id, clip_uid, heart_count, view_count,
                follower_count, score, rank, collected_at)
           SELECT s.event_id, s.owner_channel_id, s.clip_uid, s.heart_count,
                  s.view_count, s.follower_count, s.score, s.rank, s.collected_at
             FROM singcup_snapshots s
             JOIN (SELECT owner_channel_id, MAX(collected_at) mc
                     FROM singcup_snapshots WHERE event_id=?
                    GROUP BY owner_channel_id) k
               ON k.owner_channel_id = s.owner_channel_id AND k.mc = s.collected_at
            WHERE s.event_id = ?
           ON CONFLICT(event_id, owner_channel_id) DO UPDATE SET
                clip_uid=excluded.clip_uid, heart_count=excluded.heart_count,
                view_count=excluded.view_count, follower_count=excluded.follower_count,
                score=excluded.score, rank=excluded.rank,
                collected_at=excluded.collected_at""",
        (EVENT_ID, EVENT_ID))
    await db.commit()
    return {"saved": cur.rowcount or 0}


async def compact_hourly(now: int, *, dry_run: bool) -> dict:
    """오래된 시간별 롤업을 '하루의 마지막 값'으로 한 번 더 접는다.

    이게 없으면 시간별 롤업이 그대로 또 하나의 무한 증가 테이블이 된다.
    """
    db = await get_db()
    cut = int(now - HOURLY_RETENTION_DAYS * 86400)
    # KST 자정 기준으로 묶는다 — 화면 표기가 KST라 경계가 어긋나면 하루가 밀린다
    day_expr = "((hour_ts + 32400) / 86400) * 86400 - 32400"
    if dry_run:
        r = await (await db.execute(
            f"SELECT COUNT(*) n FROM (SELECT 1 FROM singcup_snapshot_hourly "
            f" WHERE hour_ts < ? GROUP BY event_id, {day_expr}, owner_channel_id)",
            (cut,))).fetchone()
        src = await (await db.execute(
            "SELECT COUNT(*) n FROM singcup_snapshot_hourly WHERE hour_ts < ?",
            (cut,))).fetchone()
        return {"daily_rows": int(r["n"]), "hourly_rows_to_drop": int(src["n"]),
                "applied": False}
    await db.execute(
        f"""INSERT INTO singcup_snapshot_daily
                (event_id, day_ts, owner_channel_id, clip_uid, heart_count,
                 view_count, follower_count, score, rank)
            SELECT h.event_id, {day_expr.replace('hour_ts', 'h.hour_ts')},
                   h.owner_channel_id, h.clip_uid, h.heart_count, h.view_count,
                   h.follower_count, h.score, h.rank
              FROM singcup_snapshot_hourly h
              JOIN (SELECT event_id, owner_channel_id,
                           {day_expr} AS d, MAX(hour_ts) AS mh
                      FROM singcup_snapshot_hourly WHERE hour_ts < ?
                     GROUP BY event_id, owner_channel_id, {day_expr}) k
                ON k.event_id = h.event_id
               AND k.owner_channel_id = h.owner_channel_id AND k.mh = h.hour_ts
            ON CONFLICT(event_id, day_ts, owner_channel_id) DO UPDATE SET
                 clip_uid=excluded.clip_uid, heart_count=excluded.heart_count,
                 view_count=excluded.view_count, follower_count=excluded.follower_count,
                 score=excluded.score, rank=excluded.rank""",
        (cut,))
    cur = await db.execute(
        "DELETE FROM singcup_snapshot_hourly WHERE hour_ts < ?", (cut,))
    await db.commit()
    return {"dropped_hourly": cur.rowcount or 0, "applied": True}


_ORPHAN_CHECK_NOTE = ("dry-run에서는 롤업을 저장하지 않으므로 "
                      "미롤업 원본 대조를 수행하지 않습니다")


async def _estimate_prune(now: int) -> dict:
    """dry-run 견적 — 보존 시간 이전 전체를 대상으로 '무엇을 지울 것인지' 센다.

    이벤트별 행 수까지 내려주는 이유: 여러 이벤트가 한 테이블에 섞여 있을 때
    어느 쪽이 용량을 먹는지 이 숫자 없이는 알 수 없다.

    **미롤업 원본 대조(`not_rolled_up_rows`)는 여기서 하지 않는다.** 예전에는
    `singcup_snapshots`를 자기 자신과 대조했는데, 같은 행(`x = s`)이 언제나 세 조건을
    만족해 `NOT EXISTS`가 성립할 수 없었다 — 82만 행을 훑어 **항상 0을 돌려주는
    항진식**이었고, 그게 retention 실행시간의 약 97%였다(실측 47,028ms / 48,611ms;
    `collected_at/3600`이 표현식이라 인덱스 탐색으로 좁혀지지 않는다).

    `singcup_snapshot_hourly`와 대조하도록 고치는 선택지는 **일부러 택하지 않았다**:
    dry-run은 롤업을 쓰지 않으므로 아직 만들지 않은 롤업을 세게 되어 '82만 행 미롤업'
    같은 오해를 부르는 숫자가 나온다. 원본 행 수와 owner-hour 롤업 건수는 애초에 단위가
    다르다. 생성 예정 롤업 수는 `rollup.rollup_rows`가 이미 알려주고, 실제 삭제
    안전성은 non-dry 경로의 `verify_rollup`(`safe_hours`)이 책임진다.

    응답 필드는 기존 소비처 호환을 위해 남기되, **의미 없는 0을 계속 돌려주지 않는다** —
    `None` + `not_rolled_up_check_performed=False`로 '재지 않았다'를 명시한다.
    """
    db = await get_db()
    cut = _cutoff(now)
    t = time.perf_counter()
    tot = await (await db.execute(
        "SELECT COUNT(*) n, MIN(collected_at) lo, MAX(collected_at) hi "
        "FROM singcup_snapshots WHERE collected_at < ?", (cut,))).fetchone()
    ms_tot = int((time.perf_counter() - t) * 1000)
    t = time.perf_counter()
    per_event = [{"event_id": r["event_id"], "rows": int(r["n"])}
                 for r in await (await db.execute(
                     "SELECT event_id, COUNT(*) n FROM singcup_snapshots "
                     "WHERE collected_at < ? GROUP BY event_id ORDER BY n DESC",
                     (cut,))).fetchall()]
    ms_pe = int((time.perf_counter() - t) * 1000)
    n = int(tot["n"] or 0)
    batches = (n + PRUNE_BATCH - 1) // PRUNE_BATCH
    return {
        "deleted": 0, "applied": False,
        "would_delete": n,
        "oldest_at": tot["lo"], "newest_at": tot["hi"],
        "per_event": per_event,
        "not_rolled_up_rows": None,
        "not_rolled_up_check_performed": False,
        "not_rolled_up_note": _ORPHAN_CHECK_NOTE,
        "estimated_reclaim_bytes": n * _ROW_BYTES,
        "estimated_batches": batches,
        # 배치당 커밋 + 양보 시간이 실제 소요의 대부분이다
        "estimated_seconds": round(batches * (0.05 + PRUNE_BATCH_SLEEP), 1),
        "phase_ms": {"estimate_total": ms_tot, "estimate_per_event": ms_pe,
                     "estimate_orphan_check": 0},
    }


# ── 오케스트레이션 ─────────────────────────────────────────────────────────
async def run_retention(now: int | None = None, *,
                        force_apply: bool = False) -> dict:
    """롤업 → 검증 → (통과한 시간대만) 프루닝. 기본은 아무것도 지우지 않는다.

    force_apply는 관리자 엔드포인트에서 '이번 한 번만' 실행할 때 쓴다.
    그래도 PRUNE_ENABLED가 꺼져 있으면 삭제는 하지 않는다.
    """
    now = now or int(time.time())
    dry = PRUNE_DRY_RUN and not force_apply
    t0 = time.perf_counter()
    # 단계별 시간. `elapsed_ms` 하나만으로는 어느 단계가 20초를 쓰는지 알 수 없어
    # 운영에서 원인을 짚지 못했다(실측 2026-08-01). 숫자만 남긴다 — 행 값·식별자 없음.
    phase_ms = {"build_rollup": 0, "estimate_total": 0, "estimate_per_event": 0,
                "estimate_orphan_check": 0, "verify": 0, "compact_hourly": 0,
                "final_standings": 0}

    t = time.perf_counter()
    roll = await build_rollup(now, dry_run=dry)
    phase_ms["build_rollup"] = int((time.perf_counter() - t) * 1000)

    report: dict = {
        "cutoff_at": _cutoff(now),
        "retention_hours": RETENTION_HOURS,
        "prune_enabled": PRUNE_ENABLED,
        "dry_run": dry,
        "rollup": roll,
        "destructive_warning": ("프루닝은 원본을 실제로 삭제합니다. "
                                "코드를 revert해도 지워진 원본은 복구되지 않습니다."),
    }

    if dry:
        # dry-run은 롤업을 쓰지 않으므로 '롤업 vs 원본' 검증을 할 수 없다.
        # 검증을 통과한 척하지 말고, 실제 실행 시 수행된다고 밝힌다.
        report["verify"] = {"performed": False,
                            "note": "dry-run에서는 롤업을 쓰지 않아 검증을 수행하지 않습니다"}
        report["prune"] = await _estimate_prune(now)
        phase_ms.update(report["prune"].pop("phase_ms", {}))
        report["prune"]["note"] = "dry-run — 삭제하지 않았습니다"
    else:
        t = time.perf_counter()
        ver = await verify_rollup(now)
        phase_ms["verify"] = int((time.perf_counter() - t) * 1000)
        report["verify"] = {"performed": True,
                            **{k: v for k, v in ver.items() if k != "safe_hours"}}
        if ver["mismatched_hours"]:
            report["prune"] = {"deleted": 0, "applied": False,
                               "note": "롤업 불일치가 있어 원본을 삭제하지 않았습니다"}
        elif not PRUNE_ENABLED:
            # 배포만으로 삭제가 시작되지 않도록 하는 마지막 관문
            report["prune"] = await prune(now, ver["safe_hours"], dry_run=True)
            report["prune"]["note"] = ("SINGCUP_SNAPSHOT_PRUNE_ENABLED=false — "
                                       "삭제하지 않았습니다(견적만 표시)")
        else:
            report["prune"] = await prune(now, ver["safe_hours"], dry_run=False)

    if COMPACT_HOURLY_ENABLED:
        t = time.perf_counter()
        report["compact_hourly"] = await compact_hourly(now, dry_run=dry)
        phase_ms["compact_hourly"] = int((time.perf_counter() - t) * 1000)
    t = time.perf_counter()
    report["final_standings"] = await save_final_standings(now)
    phase_ms["final_standings"] = int((time.perf_counter() - t) * 1000)
    report["phase_ms"] = phase_ms
    report["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    _log({"event": "retention_run", **report})
    return report


# 유지보수 주기 — 수집 주기(4분)와 분리한다. 검증 쿼리가 가벼운 작업이 아니다.
INTERVAL_MINUTES = float(os.getenv("SINGCUP_RETENTION_INTERVAL_MINUTES", "60"))


async def start_retention_worker():
    """백엔드 lifespan에서 도는 유지보수 루프. 삭제 여부는 환경변수가 정한다."""
    if not _env_bool("SINGCUP_RETENTION_WORKER_ENABLED", True):
        return
    await asyncio.sleep(float(os.getenv("SINGCUP_RETENTION_START_DELAY_SECONDS", "120")))
    _log({"event": "worker_start", "prune_enabled": PRUNE_ENABLED,
          "dry_run": PRUNE_DRY_RUN, "interval_minutes": INTERVAL_MINUTES,
          "warning": "프루닝은 파괴적입니다. revert해도 지워진 원본은 복구되지 않습니다."})
    while True:
        try:
            await run_retention()
        except Exception as e:
            _log({"event": "worker_error", "level": "warning", "detail": str(e)[:200]})
        await asyncio.sleep(max(300.0, INTERVAL_MINUTES * 60))
