"""싱드컵 지표 전체 순회 — KST 매시 정각 1회차, 모든 클립을 한 번씩 갱신한다.

예전 구조는 4분마다 상위 80건만 훑었다. 그러면 클립이 5천 건일 때 한 바퀴에
4시간이 넘고, 어떤 클립은 며칠씩 옛 값으로 남았다. 여기서는 회차 단위로 뒤집는다.

  정각 → 대상 UID 확정 → 토큰 버킷으로 고르게 처리 → 55분 내 종료 → 순위 재계산

핵심 설계 두 가지만 기억하면 된다.

1. **대상 = last_metrics_at < scheduled_at 인 모든 활성 클립.**
   클립을 처리하면 last_metrics_at이 now(> scheduled_at)가 되어 대상에서 빠진다.
   덕분에 (a) 같은 클립을 회차 안에서 두 번 부르지 않고, (b) 프로세스가 죽었다
   살아나도 남은 것만 자동으로 이어서 한다 — 별도 진행률 저장이 필요 없다.

2. **회차 소유권은 DB UNIQUE(event_id, scheduled_at)가 정한다.**
   여러 워커가 같은 정각에 깨어나도 INSERT에 성공한 하나만 실행한다.
   메모리 플래그로 관리하면 재시작·다중 replica에서 그대로 깨진다.
"""
import asyncio
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import singcup_clips as sc
from utils.token_bucket import TokenBucket
from singcup_collector import EVENT_ID, ST_OK, event_status, metrics_refresh_open

from database import get_db

KST = timezone(timedelta(hours=9))

# 회차는 55분 안에 끝내고 5분을 여유로 둔다(다음 정각과 겹치지 않게).
TARGET_MINUTES = float(os.getenv("SINGCUP_SWEEP_TARGET_MINUTES", "55"))
# 단계적 상향용 상한. 운영에서 1.0 → 1.5 → 1.7 → 2.0으로 올리며 검증한다.
MAX_RATE = float(os.getenv("SINGCUP_SWEEP_MAX_RATE", "1.0"))
MIN_RATE = float(os.getenv("SINGCUP_SWEEP_MIN_RATE", "0.2"))
CONCURRENCY = int(os.getenv("SINGCUP_SWEEP_CONCURRENCY", "4"))
# 정각 이후 이 시간 안에 살아나면 그 회차를 실행한다. 넘으면 missed.
GRACE_SECONDS = int(os.getenv("SINGCUP_SWEEP_GRACE_SECONDS", "300"))
# 이 시간 넘게 heartbeat가 없으면 죽은 회차로 보고 인수한다.
STALE_RUN_SECONDS = int(os.getenv("SINGCUP_SWEEP_STALE_SECONDS", "600"))
PROGRESS_EVERY = int(os.getenv("SINGCUP_SWEEP_PROGRESS_EVERY", "25"))
NEW_CLIP_WINDOW = int(os.getenv("SINGCUP_SWEEP_NEW_HOURS", "48")) * 3600
# 연속 모드 — 정각 경계 없이 사이클을 이어 붙인다.
# 대상은 "마지막 시도가 이 시간보다 오래된 클립". 처리량이 이보다 빠르면 그만큼
# 자주 갱신되고, 느리면 가장 오래된 것부터 처리되므로 굶는 클립은 없다.
STALENESS_MINUTES = float(os.getenv("SINGCUP_SWEEP_STALENESS_MINUTES", "60"))
CYCLE_GAP_SECONDS = float(os.getenv("SINGCUP_SWEEP_CYCLE_GAP_SECONDS", "5"))
IDLE_SLEEP_SECONDS = float(os.getenv("SINGCUP_SWEEP_IDLE_SECONDS", "60"))
# 예전 정각 모드로 되돌리는 스위치(롤백용)
HOURLY_MODE = os.getenv("SINGCUP_SWEEP_HOURLY", "false").lower() in ("1", "true", "yes")

RUNNING, COMPLETED, PARTIAL = "running", "completed", "partial"
FAILED, MISSED, SKIPPED_OVERLAP = "failed", "missed", "skipped_overlap"


def floor_hour(ts: float) -> int:
    """그 시각이 속한 정각(epoch). KST는 UTC+9 정시라 epoch 절삭으로 충분하다."""
    return int(ts) - int(ts) % 3600


def kst(ts) -> str | None:
    return datetime.fromtimestamp(int(ts), KST).isoformat() if ts else None


# 토큰 버킷은 utils/token_bucket.py로 옮겼다 — 삭제 감사 워커가 같은 것을 쓰는데
# 복사본을 두면 "용량 최소 1.0" 같은 교훈이 한쪽에서만 유지된다. 이름은 유지한다.
def _bucket(rate: float, cap: float, floor: float | None = None) -> TokenBucket:
    return TokenBucket(rate, cap, floor if floor is not None else MIN_RATE,
                       name="sweep", on_log=sc._log)


def required_rate(total: int) -> float:
    """대상이 늘어도 55분 안에 끝나도록 필요한 초당 처리량을 다시 계산한다."""
    if total <= 0:
        return MIN_RATE
    return total / max(1.0, TARGET_MINUTES * 60.0)


# ── 대상 선정 ──────────────────────────────────────────────────────────────
# 우선순위는 '누가 먼저냐'일 뿐이다. 회차가 끝나기 전에 전부 처리된다.
#   0 한 번도 정상 갱신 안 됨 / 1 대표 / 2 최근 48시간 신규
#   3 대표를 추월할 가능성(대표 하트의 절반 이상) / 4 나머지 일반 클립
_TARGET_SQL = """
SELECT c.clip_uid, c.video_id, c.rec_id, c.last_attempt_at, c.owner_channel_id,
       c.thumbnail_image_url,
       (s.representative_clip_uid IS NOT NULL) AS is_rep,
       CASE
         -- 우선순위 0은 '한 번도 정상 수신한 적 없음'이다(시도 여부가 아니라)
         WHEN c.last_metrics_at IS NULL              THEN 0
         WHEN c.last_metrics_at = 0                  THEN 0
         WHEN s.representative_clip_uid IS NOT NULL  THEN 1
         WHEN c.created_at >= ?                      THEN 2
         WHEN rc.heart_count IS NOT NULL
              AND c.heart_count * 2 >= rc.heart_count THEN 3
         ELSE 4
       END AS prio
FROM singcup_clips c
LEFT JOIN singcup_streamers s  ON s.representative_clip_uid = c.clip_uid
LEFT JOIN singcup_streamers so ON so.channel_id = c.owner_channel_id
LEFT JOIN singcup_clips rc     ON rc.clip_uid = so.representative_clip_uid
WHERE c.event_id = ? AND c.active = 1
  -- 삭제가 확정된 클립은 매 회차마다 다시 물어보지 않는다. 복원 확인은 훨씬 긴
  -- 주기로 singcup_clips.run_deletion_checks가 따로 한다(요청·실패율 절감).
  AND c.deletion_state <> 'confirmed_deleted' 
  -- **시도** 시각 기준이다. last_metrics_at(둘 다 정상)으로 걸면 계속 실패하는
  -- 클립은 영원히 대상에 남아 한 회차 안에서 무한 재호출된다.
  AND (c.last_attempt_at IS NULL OR c.last_attempt_at < ?)
ORDER BY prio ASC,
         CASE WHEN c.last_attempt_at IS NULL THEN 0 ELSE 1 END ASC,
         c.last_attempt_at ASC,
         c.clip_uid ASC
"""


async def sweep_targets(scheduled_at: int) -> list[dict]:
    """이 회차에 아직 처리되지 않은 전체 클립. 대표·일반을 모두 포함한다.

    이미 처리된 클립은 last_attempt_at >= scheduled_at 이라 자동으로 빠진다 —
    그래서 이 목록 자체가 '남은 일감'이고, 재시작 후 그대로 이어진다.
    성공이 아니라 **시도**가 기준이라, 계속 실패하는 클립도 회차 안에서 한 번만
    부르고 다음 회차로 넘어간다.
    """
    db = await get_db()
    rows = await (await db.execute(
        _TARGET_SQL, (int(time.time()) - NEW_CLIP_WINDOW, EVENT_ID, scheduled_at)
    )).fetchall()
    out, seen = [], set()
    for r in rows:                       # 방어적 중복 제거(같은 uid 두 번 호출 금지)
        if r["clip_uid"] in seen:
            continue
        seen.add(r["clip_uid"])
        out.append(dict(r))
    return out


# ── 회차 소유권 ────────────────────────────────────────────────────────────
async def _claim(scheduled_at: int) -> str | None:
    """UNIQUE 제약으로 회차를 선점한다. 이미 있으면 None(다른 워커가 소유)."""
    db = await get_db()
    run_id = uuid.uuid4().hex
    now = int(time.time())
    try:
        await db.execute(
            "INSERT INTO singcup_sweep_runs (run_id, event_id, scheduled_at,"
            " started_at, heartbeat_at, status) VALUES (?,?,?,?,?,?)",
            (run_id, EVENT_ID, scheduled_at, now, now, RUNNING))
        await db.commit()
        return run_id
    except Exception:
        return None


async def reap_stale_runs() -> int:
    """heartbeat가 끊긴 running 행을 failed로 닫는다.

    최종 상태 저장 자체가 잠금으로 실패하면(final_status_persist_failed) 그 행이
    running으로 남아 새 사이클을 영구 차단한다. 스케줄러가 매 틱 이 정리를 돌려
    그 상황에서 자력 복구되게 한다.
    """
    cutoff = int(time.time()) - STALE_RUN_SECONDS

    async def work(db):
        await db.execute(
            "UPDATE singcup_sweep_runs SET status=?, note=?, completed_at=? "
            "WHERE event_id=? AND status=? AND COALESCE(heartbeat_at,0) < ?",
            (FAILED, "heartbeat 끊김 — 스케줄러가 정리", int(time.time()),
             EVENT_ID, RUNNING, cutoff))
    db = await get_db()
    before = (await (await db.execute(
        "SELECT COUNT(*) c FROM singcup_sweep_runs WHERE event_id=? AND status=? "
        "AND COALESCE(heartbeat_at,0) < ?", (EVENT_ID, RUNNING, cutoff)
    )).fetchone())["c"]
    if before:
        await db_write(work, what="reap_stale_runs")
        sc._log({"event": "sweep_reaped_stale", "level": "warning", "runs": before})
    return int(before or 0)


async def _active_run(scheduled_at: int) -> dict | None:
    """아직 살아 있는(heartbeat 최신) 이전 회차. 있으면 새 회차를 겹쳐 돌리지 않는다."""
    db = await get_db()
    row = await (await db.execute(
        "SELECT run_id, scheduled_at, heartbeat_at FROM singcup_sweep_runs "
        "WHERE event_id=? AND status=? AND scheduled_at<? "
        "ORDER BY scheduled_at DESC LIMIT 1",
        (EVENT_ID, RUNNING, scheduled_at))).fetchone()
    if row is None:
        return None
    if int(time.time()) - int(row["heartbeat_at"] or 0) > STALE_RUN_SECONDS:
        # 죽은 회차 — 실패로 닫고 진행한다(영원히 막히지 않게)
        await db.execute(
            "UPDATE singcup_sweep_runs SET status=?, note=? WHERE run_id=?",
            (FAILED, "heartbeat 끊김 — 다음 회차가 인수", row["run_id"]))
        await db.commit()
        return None
    return dict(row)


async def _record(scheduled_at: int, status: str, note: str):
    """실행하지 않은 회차(missed / skipped_overlap)를 기록만 해 둔다."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO singcup_sweep_runs (run_id, event_id, scheduled_at,"
            " status, note) VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, EVENT_ID, scheduled_at, status, note[:200]))
        await db.commit()
    except Exception:
        pass                              # 이미 있으면 그대로 둔다


# ── DB 쓰기 (짧은 트랜잭션 + 잠금 재시도) ──────────────────────────────────
# aiosqlite 기본 isolation_level에서는 첫 쓰기가 암묵적 트랜잭션을 열고 commit까지
# 유지된다. 예전 구조는 25건마다 커밋하면서 그 사이 외부 API를 기다려, 쓰기 잠금이
# 25~30초 유지됐다. busy_timeout이 10초라 봇 프로세스와 rising_collector가
# 'database is locked'로 실패했다. 이제 쓰기는 이 헬퍼로만 하고, 열자마자 커밋한다.
DB_RETRY_ATTEMPTS = int(os.getenv("SINGCUP_DB_RETRY_ATTEMPTS", "4"))
DB_RETRY_BASE_SECONDS = float(os.getenv("SINGCUP_DB_RETRY_BASE_SECONDS", "0.05"))
# 클립별 락 — 자동 스윕과 수동 갱신이 같은 clip_uid를 동시에 건드리지 않게 한다.
# 전역 락을 70분 잡으면 그동안 수동 갱신이 전면 차단되므로 쓰지 않는다.
# 클립 락 설정은 singcup_clips가 소유한다(CLIP_LOCK_TTL / _WAIT / _POLL).


# 롤백 호출 횟수 — 테스트가 "잠길 때마다 롤백했는가"를 직접 확인한다
_rollbacks = {"n": 0}


async def _rollback(db) -> None:
    try:
        await db.rollback()
        _rollbacks["n"] += 1
    except Exception:                               # noqa: BLE001
        pass


def _is_locked(e: Exception) -> bool:
    m = str(e).lower()
    return "database is locked" in m or "database is busy" in m


async def db_write(fn, *, what: str, attempts: int = DB_RETRY_ATTEMPTS) -> bool:
    """쓰기 한 단위를 열고 즉시 커밋한다. 잠금이면 지수 백오프+jitter로 재시도.

    성공하면 True. 재시도를 소진하면 **예외를 올리지 않고** False를 돌려준다 —
    한 클립의 잠금 실패가 스윕 전체를 끝내면 안 된다. 잠금이 아닌 오류는 그대로
    올려 상위에서 클립 단위로 처리하게 둔다.
    """
    delay = DB_RETRY_BASE_SECONDS
    last: Exception | None = None
    for i in range(max(1, attempts)):
        db = await get_db()
        try:
            await fn(db)
            await db.commit()
            return True
        except Exception as e:                      # noqa: BLE001
            # 잠금이든 아니든 **반드시** 롤백한다. 되돌리지 않으면 부분 실행된
            # 문장이 다음 시도나 다른 작업의 커밋에 묻어 들어간다.
            await _rollback(db)
            if not _is_locked(e):
                raise
            last = e
            if i + 1 >= attempts:
                break                               # 소진 — 위에서 이미 롤백했다
            # jitter를 넣어 여러 워커가 같은 순간에 몰려 재충돌하는 것을 막는다
            await asyncio.sleep(delay + random.uniform(0, delay))
            delay *= 2
    sc._log({"event": "db_locked_giveup", "level": "warning", "what": what,
             "attempts": attempts, "detail": str(last)[:160]})
    return False


# 클립 락은 singcup_clips가 소유한다(같은 키를 쓰는 경로가 그쪽에 더 많다).
acquire_clip_lock = sc.acquire_clip_lock


async def _persist_clip(t: dict, card: dict | None, detail: dict | None,
                        now: int) -> tuple[str, bool, bool]:
    """클립 한 건의 DB 반영. (상태, 쓰기 성공, 썸네일 실제 보수 여부).

    네트워크는 이미 끝난 뒤에 불린다 — 이 안에서는 어떤 외부 호출도 하지 않는다.
    """
    state = "failed"
    fixed = False
    metrics = {"frozen_rounds": 0}
    hints: list[str] = []

    async def work(_db):
        nonlocal state, fixed
        if card is None:
            await sc._apply_metrics(t["clip_uid"], 0, 0, False, False, now,
                                    out=metrics)
            await sc._queue_retry({"clipUID": t["clip_uid"],
                                   "videoId": t["video_id"],
                                   "recId": t["rec_id"] or "{}"},
                                  "sweep card fetch failed", now)
            # 카드 조회 실패도 약한 신호다. 삭제로 세지 않고 '상세 API로 한 번
            # 확인해 보라'는 표시만 남긴다 — 카드가 아예 응답하지 않는 삭제 클립이
            # 이 경로로만 드러나는 경우가 있다(살아 있으면 첫 확인에서 바로 풀린다).
            await sc._flag_deletion_suspect(t["clip_uid"], "card_failed", now)
            hints.append("card_failed")
            state = "failed"
            return
        state = await sc._apply_metrics(
            t["clip_uid"], card["heart_count"], card["view_count"],
            card["heart_ok"], card["view_ok"], now, out=metrics)
        await sc._clear_retry(t["clip_uid"])
        if not card["heart_ok"] and not card["view_ok"]:
            # 약한 신호다. **여기서 삭제로 세지 않는다** — 카드가 원래 값을 안 주는
            # 경우와 구분되지 않기 때문이다. 상세 API로 따로 확인할 대상으로만 표시한다.
            await sc._flag_deletion_suspect(t["clip_uid"], "card_empty", now)
            hints.append("card_empty")
        if detail:
            # 상세가 빈 썸네일을 줬거나 이미 값이 있으면 False — 그때는 세지 않는다
            fixed = await sc.repair_clip_media(t["clip_uid"], detail)

    ok = await db_write(work, what=f"sweep_clip({t['clip_uid']})")
    if ok:
        # 힌트는 **바깥 트랜잭션이 끝난 뒤에** 건다. 안에서 걸면 쓰기 단위가
        # 하나 더 겹쳐 잠금 경합만 늘고, 힌트는 실패해도 Cold lane이 결국 같은
        # 클립을 검사하므로 재시도할 이유도 없다.
        for reason in hints:
            await sc._audit_hint(t["clip_uid"], reason)
        if not hints:
            await sc._audit_note_frozen(t["clip_uid"],
                                        int(metrics.get("frozen_rounds") or 0))
    return (state if ok else "db_error"), ok, (fixed and ok)


async def _progress_safe(run_id: str, **f) -> bool:
    """진행/상태 기록. **절대 예외를 올리지 않는다.**

    예전에는 실패 경로에서 _progress가 다시 잠금에 걸리면 그 예외가 그대로
    스케줄러까지 올라가(sweep_loop_error) 사이클 자체가 끝나 버렸다. 상태를 못
    남기는 것보다 스윕이 계속 도는 쪽이 낫다.
    """
    f = {k: v for k, v in f.items() if v is not None}
    f["heartbeat_at"] = int(time.time())

    async def work(db):
        sets = ", ".join(f"{k}=?" for k in f)
        await db.execute(f"UPDATE singcup_sweep_runs SET {sets} WHERE run_id=?",
                         (*f.values(), run_id))
    try:
        return await db_write(work, what=f"sweep_progress({run_id[:8]})")
    except Exception as e:                          # noqa: BLE001
        sc._log({"event": "sweep_progress_error", "level": "warning",
                 "run_id": run_id, "detail": str(e)[:160]})
        return False


async def _progress(run_id: str, **f):
    db = await get_db()
    f["heartbeat_at"] = int(time.time())
    sets = ", ".join(f"{k}=?" for k in f)
    await db.execute(f"UPDATE singcup_sweep_runs SET {sets} WHERE run_id=?",
                     (*f.values(), run_id))
    await db.commit()


def _pct(values: list[int], p: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


# ── 회차 실행 ──────────────────────────────────────────────────────────────
async def run_sweep(scheduled_at: int | None = None, *, run_id: str | None = None,
                    cutoff: int | None = None) -> dict:
    """한 사이클 — 대상 전체를 속도 제한 아래 갱신하고 마지막에 순위를 재계산한다.

    scheduled_at 은 **회차 식별자**(UNIQUE 키)이고, cutoff 는 **대상 기준 시각**이다.
    정각 모드에서는 둘이 같지만, 연속 모드에서는 식별자는 사이클 시작 시각이고
    기준은 "now - 허용 신선도"라 서로 다르다.
    """
    scheduled_at = scheduled_at or floor_hour(time.time())
    cutoff = scheduled_at if cutoff is None else cutoff
    if run_id is None:
        busy = await _active_run(scheduled_at)
        if busy:
            # 정각 모드에서 '이번 시간을 건너뛰었다'는 기록은 의미가 있지만,
            # 연속 모드에서는 그냥 앞 사이클이 도는 중이라 매번 남기면 행만 쌓인다.
            if HOURLY_MODE:
                await _record(scheduled_at, SKIPPED_OVERLAP,
                              f"이전 회차({kst(busy['scheduled_at'])})가 아직 실행 중")
                sc._log({"event": "sweep_skipped_overlap", "level": "warning",
                         "scheduled_at": kst(scheduled_at)})
            return {"status": SKIPPED_OVERLAP, "scheduled_at": kst(scheduled_at)}
        run_id = await _claim(scheduled_at)
        if run_id is None:
            return {"status": SKIPPED_OVERLAP, "scheduled_at": kst(scheduled_at),
                    "note": "다른 워커가 이미 이 회차를 소유"}

    started = time.monotonic()
    targets = await sweep_targets(cutoff)
    total = len(targets)
    rate = min(MAX_RATE, max(MIN_RATE, required_rate(total)))
    bucket = _bucket(rate, MAX_RATE)
    await _progress_safe(run_id, total_targets=total, rate_limit=round(rate, 3))
    sc._log({"event": "sweep_start", "run_id": run_id,
             "scheduled_at": kst(scheduled_at), "total_targets": total,
             "rate_per_second": round(rate, 3), "concurrency": CONCURRENCY,
             "eta_minutes": round(total / max(rate, 1e-6) / 60, 1)})

    tally = {"success": 0, "partial": 0, "failed": 0, "fetch_failed": 0}
    repaired = 0
    lat: list[int] = []
    changed_owners: set[str] = set()
    done = 0
    db_giveup = 0
    skipped = 0        # 클립별 락 충돌로 이번 사이클에 건너뛴 수(DB 컬럼 없음)
    sc._take_api_counters()
    client = sc._get_client()
    sem = asyncio.Semaphore(max(1, CONCURRENCY))
    lock = asyncio.Lock()

    async def one(t: dict):
        """클립 한 건. **예외를 밖으로 내보내지 않는다.**

        한 건이 던지면 asyncio.gather가 즉시 그 예외로 완료되는데, 남은 코루틴은
        취소되지 않고 계속 돌아 '고아 워커'가 된다(실측: run_sweep이 반환한 뒤에도
        processed가 300 → 1500까지 증가). 그래서 여기서 전부 삼키고 결과는 tally로만
        남긴다.
        """
        nonlocal done, repaired, db_giveup
        counted = False                             # 결과를 두 번 세지 않기 위한 표시

        async def record(outcome: str):
            """이 클립의 결과를 **정확히 한 번** 집계한다.

            skipped는 singcup_sweep_runs에 컬럼이 없다(스키마 변경 금지). 그래서
            **failed의 부분집합**으로 둔다 — DB에는 failed로 들어가고, 로그·응답에만
            내역으로 따로 보인다. 그래야 저장된 행에서도 영구 불변식
            processed = success + partial + failed 가 성립한다.
            """
            nonlocal done, counted, skipped
            if counted:
                return None
            counted = True
            async with lock:
                if outcome == "skipped":
                    skipped += 1
                    tally["failed"] += 1        # 부분집합: skipped ⊆ failed
                else:
                    tally[outcome] += 1
                done += 1
                return (done, dict(tally), round(bucket.rate, 3))

        lock_token = None
        try:
            # 같은 clip_uid를 수동 갱신이 처리 중이면 건드리지 않는다. 전역 락이
            # 아니라 이 클립에만, 짧게(CLIP_LOCK_TTL) 걸린다.
            lock_token = await acquire_clip_lock(t["clip_uid"])
            if lock_token is None:
                # **DB를 건드리지 않고** 넘어간다. last_attempt_at이 그대로라
                # 다음 사이클 대상에 자동으로 다시 들어온다.
                await record("skipped")
                return
            # ── 1) 네트워크 구간 — DB를 절대 건드리지 않는다 ──────────────
            # 예전에는 _apply_metrics로 트랜잭션을 연 채 다음 클립의 토큰 대기와
            # 카드 조회를 기다렸고, 25건마다 커밋했다. 그 사이 쓰기 잠금이 25~30초
            # 유지돼 봇 프로세스(chzzk_chat, chzzk_monitor)와 rising_collector가
            # busy_timeout(10초)을 넘겨 'database is locked'로 실패했다.
            item = {"clipUID": t["clip_uid"], "videoId": t["video_id"],
                    "recId": t["rec_id"] or "{}"}
            t0 = time.monotonic()
            before_429 = sc._api_counter["http_429"]
            # 카드가 200을 주면서 조회수만 빠뜨리는 회차가 있다. 여기서 짧게 다시
            # 물어보고 필드 단위로 합쳐 **결과 하나**를 받는다 — 시도가 몇 번이든
            # 이 클립은 아래에서 정확히 한 번 집계된다(processed 불변식).
            # 토큰 버킷과 세마포어를 넘겨 재시도도 같은 속도 제한을 통과하게 한다.
            card = await sc.fetch_card_metrics(client, item,
                                               acquire=bucket.acquire, sem=sem)
            dt = int((time.monotonic() - t0) * 1000)

            detail = None
            if card is not None and not t["thumbnail_image_url"]:
                # 썸네일 보수도 네트워크 구간에서 미리 받아 둔다
                await bucket.acquire()
                async with sem:
                    detail = await sc.fetch_clip_detail(client, t["clip_uid"])

            # ── 2) DB 구간 — 짧게 열고 즉시 커밋 ────────────────────────
            now = int(time.time())
            state, wrote, fixed = await _persist_clip(t, card, detail, now)

            async with lock:
                lat.append(dt)
                if sc._api_counter["http_429"] > before_429:
                    bucket.slow_down("http_429")
                elif card is None:
                    bucket.slow_down("fetch_failed")
                else:
                    bucket.recover()
                if not wrote:
                    db_giveup += 1
                if card is None:
                    tally["fetch_failed"] += 1
                elif state != "db_error" and state != "failed":
                    changed_owners.add(t["owner_channel_id"])
                if fixed:
                    repaired += 1
            outcome = ("failed" if (card is None or state == "db_error")
                       else ("success" if state == "ok" else state))
            snapshot = await record(outcome)
            if snapshot and snapshot[0] % PROGRESS_EVERY == 0:
                await _progress_safe(
                    run_id, processed=snapshot[0], rate_limit=snapshot[2],
                    http_429=sc._api_counter["http_429"],
                    api_calls=sc._api_counter["calls"], **snapshot[1])
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # noqa: BLE001 — 고아 방지가 목적
            # record()가 이미 셌으면 무시된다 — 한 클립이 두 번 집계되지 않는다.
            await record("failed")
            sc._log({"event": "sweep_clip_error", "level": "warning",
                     "run_id": run_id, "clip_uid": t.get("clip_uid"),
                     "detail": str(e)[:160]})
        finally:
            await sc.release_clip_lock(t["clip_uid"], lock_token)

    status, note, remaining = FAILED, "", None
    tasks = [asyncio.create_task(one(t)) for t in targets]
    try:
        # one()이 예외를 삼키므로 gather가 깨지지 않는다. return_exceptions까지
        # 두어 어떤 경우에도 **모든 태스크가 끝날 때까지 기다린다** → 고아 0.
        await asyncio.gather(*tasks, return_exceptions=True)
        await sc.recompute_ranking(int(time.time()), client=client,
                                   save_snapshot=True)
        left = await sweep_targets(cutoff)
        remaining = len(left)
        # 락 충돌로 건너뛴 클립은 이번 사이클이 끝내지 못한 것이다 — completed로
        # 보고하면 "전부 갱신됐다"는 잘못된 신호가 된다. 다음 사이클이 다시 집는다.
        status = COMPLETED if (not remaining and not skipped) else PARTIAL
        parts = []
        if remaining:
            parts.append(f"{remaining}건 미처리")
        if skipped:
            parts.append(f"{skipped}건 락 충돌로 건너뜀")
        note = ", ".join(parts)
    except asyncio.CancelledError:
        status, note = FAILED, "취소됨"
        for tk in tasks:
            tk.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    except Exception as e:
        status, note = FAILED, str(e)[:200]
        sc._log({"event": "sweep_failed", "level": "warning", "run_id": run_id,
                 "processed": done, "detail": str(e)[:200]})
    finally:
        # 어떤 경로로 나가든 남은 태스크를 정리하고 최종 상태를 남긴다.
        # 이 블록은 절대 예외를 내보내지 않는다 — 실패 기록의 실패가 스케줄러까지
        # 전파되면 그 사이클뿐 아니라 루프 전체가 무너진다(실측 sweep_loop_error).
        stragglers = [tk for tk in tasks if not tk.done()]
        if stragglers:
            for tk in stragglers:
                tk.cancel()
            await asyncio.gather(*stragglers, return_exceptions=True)
        api = sc._take_api_counters()
        dur = int((time.monotonic() - started) * 1000)
        saved = await _progress_safe(
            run_id, status=status, processed=done,
            completed_at=int(time.time()), duration_ms=dur,
            api_calls=api["calls"], http_429=api["http_429"],
            p50_ms=_pct(lat, 0.5), p95_ms=_pct(lat, 0.95),
            rate_limit=round(bucket.rate, 3), note=note[:200] or None, **tally)
        if not saved:
            # 이 행은 running으로 남는다. 다음 스케줄러 틱의 reap_stale_runs가
            # heartbeat 만료 뒤 정리하므로 새 사이클이 영구 차단되지는 않는다.
            sc._log({"event": "final_status_persist_failed", "level": "warning",
                     "run_id": run_id, "intended_status": status,
                     "processed": done})
        sc._log({"event": "sweep_done", "run_id": run_id, "status": status,
                 "scheduled_at": kst(scheduled_at), "total_targets": total,
                 "processed": done, **tally,
                 "duration_seconds": round(dur / 1000, 1),
                 "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95),
                 "api_calls": api["calls"], "http_429": api["http_429"],
                 "throttled": bucket.throttled, "db_locked_giveup": db_giveup,
                 "skipped": skipped, "remaining": remaining,
                 "thumbnails_repaired": repaired, "stragglers": len(stragglers),
                 "changed_streamers": len(changed_owners)})

    return {"status": status, "run_id": run_id, "scheduled_at": kst(scheduled_at),
            "total_targets": total, "processed": done, "note": note,
            "duration_seconds": round(dur / 1000, 1),
            "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95),
            "http_429": api["http_429"], "thumbnails_repaired": repaired,
            "db_locked_giveup": db_giveup, "skipped": skipped,
            "remaining": remaining, "stragglers": len(stragglers), **tally}


# ── 스케줄러 ───────────────────────────────────────────────────────────────
async def run_cycle() -> dict:
    """연속 갱신 한 사이클 — 정각을 기다리지 않는다.

    대상은 "마지막 시도가 STALENESS 보다 오래된 클립" 전부다. 한 사이클이 끝나면
    곧바로 다음 사이클이 시작되므로, 처리량이 허락하는 만큼 계속 돈다.
    회차 식별자는 사이클 시작 시각이라 매번 고유하고, 이전 사이클이 아직 살아
    있으면 _active_run 이 막는다(다중 워커 포함 single-flight).
    """
    now = int(time.time())
    return await run_sweep(now, cutoff=now - int(STALENESS_MINUTES * 60))


async def sweep_scheduler():
    """지표 갱신 루프.

    기본은 **연속 모드**다. 정각에 맞춰 한 번씩 돌리면 회차가 한 시간을 넘기는
    순간 다음 정각이 통째로 건너뛰어져 실효 주기가 두 시간이 된다(실측: 회차
    100분 → 매 정각 skipped_overlap → 완료 0회). 연속 모드는 회차 경계가 없어
    그 함정이 없고, 끝나는 대로 다음 사이클이 이어진다.

    SINGCUP_SWEEP_HOURLY=true 로 두면 예전 정각 모드로 되돌아간다(롤백용).
    """
    if os.getenv("SINGCUP_ENABLED", "true").lower() in ("0", "false", "no"):
        return
    if os.getenv("SINGCUP_SWEEP_ENABLED", "true").lower() in ("0", "false", "no"):
        sc._log({"event": "sweep_disabled"})
        return
    await asyncio.sleep(float(os.getenv("SINGCUP_SWEEP_START_DELAY", "20")))
    while True:
        pause = CYCLE_GAP_SECONDS
        try:
            if not metrics_refresh_open():
                # 이벤트 **종료 후에도 갱신은 계속**한다(확정 요구). 여기서 쉬는 건
                # 시작 전(UPCOMING)이거나 비상 정지가 걸렸을 때뿐이다.
                # `event_status() != "LIVE"`로 되돌리면 종료와 동시에 지표가 굳는다.
                pause = 300.0
            elif HOURLY_MODE:
                # 최종 상태 저장이 실패해 running으로 남은 행을 먼저 정리한다.
                # 이게 없으면 그 행이 새 사이클을 영구 차단한다.
                await reap_stale_runs()
                pause = await _hourly_tick()
            else:
                await reap_stale_runs()
                res = await run_cycle()
                # 갱신할 대상이 없으면 잠깐 쉰다(빈 사이클을 계속 만들지 않는다)
                if res.get("status") == SKIPPED_OVERLAP or not res.get("total_targets"):
                    pause = IDLE_SLEEP_SECONDS
        except Exception as e:
            sc._log({"event": "sweep_loop_error", "level": "warning",
                     "detail": str(e)[:200]})
            pause = IDLE_SLEEP_SECONDS
        await asyncio.sleep(pause)


async def _hourly_tick() -> float:
    """예전 정각 모드 한 틱. 다음 대기 시간을 돌려준다."""
    now = time.time()
    sched = floor_hour(now)
    if now - sched <= GRACE_SECONDS:
        await run_sweep(sched)
    elif not await _exists(sched):
        await _record(sched, MISSED,
                      f"정각 이후 {int(now - sched)}초 뒤 기동 — 이번 회차 건너뜀")
        sc._log({"event": "sweep_missed", "level": "warning",
                 "scheduled_at": kst(sched), "late_seconds": int(now - sched)})
    return max(5.0, (sched + 3600) - time.time() + 1.0)


async def _exists(scheduled_at: int) -> bool:
    db = await get_db()
    row = await (await db.execute(
        "SELECT 1 FROM singcup_sweep_runs WHERE event_id=? AND scheduled_at=?",
        (EVENT_ID, scheduled_at))).fetchone()
    return row is not None


# ── 상태 ───────────────────────────────────────────────────────────────────
async def sweep_status() -> dict:
    """다음 예정 시각·현재 회차 진행률·마지막 완료 회차·최대 갱신 지연."""
    db = await get_db()
    now = int(time.time())
    cur = await (await db.execute(
        "SELECT * FROM singcup_sweep_runs WHERE event_id=? AND status=? "
        "ORDER BY scheduled_at DESC LIMIT 1", (EVENT_ID, RUNNING))).fetchone()
    last = await (await db.execute(
        "SELECT * FROM singcup_sweep_runs WHERE event_id=? AND status IN (?,?) "
        "ORDER BY scheduled_at DESC LIMIT 1",
        (EVENT_ID, COMPLETED, PARTIAL))).fetchone()
    agg = await (await db.execute(
        "SELECT COUNT(*) n, MIN(last_metrics_at) oldest FROM singcup_clips "
        "WHERE event_id=? AND active=1", (EVENT_ID,))).fetchone()

    current = None
    if cur:
        total = int(cur["total_targets"] or 0)
        proc = int(cur["processed"] or 0)
        elapsed = max(1, now - int(cur["started_at"] or now))
        speed = proc / elapsed                     # 실측 건/초
        remain = max(0, total - proc)
        eta = now + int(remain / speed) if speed > 0 else None
        current = {
            "run_id": cur["run_id"],
            "scheduled_at": kst(cur["scheduled_at"]),
            "started_at": kst(cur["started_at"]),
            "processed": proc, "total_targets": total,
            "progress_percent": round(proc / total * 100, 1) if total else 0.0,
            "rate_per_second": round(speed, 2),
            "rate_limit": round(float(cur["rate_limit"] or 0), 3),
            "estimated_completion_at": kst(eta),
            # 55분 안에 못 끝날 페이스면 운영이 바로 알아야 한다
            # 연속 모드에서는 '이 사이클이 허용 신선도 안에 못 끝난다'는 뜻이다.
            "behind_schedule": bool(
                eta and eta > int(cur["scheduled_at"])
                + (TARGET_MINUTES if HOURLY_MODE else STALENESS_MINUTES) * 60),
            # 단계적 상향(1.0 → 1.5 → 1.7 → 2.0)을 회차가 끝나기 전에 판단하려면
            # 진행 중에도 429·실패율이 보여야 한다. secret 없이 볼 수 있게 여기 둔다.
            "success": int(cur["success"] or 0),
            "partial": int(cur["partial"] or 0),
            "failed": int(cur["failed"] or 0),
            "http_429": int(cur["http_429"] or 0),
            "failure_rate": round(int(cur["failed"] or 0) / proc, 4) if proc else 0.0,
            # 이 회차가 다음 정각을 넘기면 21:00 회차가 skipped_overlap으로 밀린다
            "will_overlap_next_hour": bool(
                eta and eta > int(cur["scheduled_at"]) + 3600),
        }
    oldest = int(agg["oldest"] or 0)
    return {
        "timezone": "Asia/Seoul",
        "mode": "hourly" if HOURLY_MODE else "continuous",
        "schedule": "0 * * * *" if HOURLY_MODE else "continuous",
        # 연속 모드에는 '다음 예정 시각'이 없다 — 앞 사이클이 끝나는 대로 이어진다.
        "next_scheduled_at": kst(floor_hour(now) + 3600) if HOURLY_MODE else None,
        "staleness_minutes": STALENESS_MINUTES,
        "target_minutes": TARGET_MINUTES,
        "max_rate_per_second": MAX_RATE,
        "concurrency": CONCURRENCY,
        "clips": int(agg["n"] or 0),
        "required_rate_per_second": round(required_rate(int(agg["n"] or 0)), 3),
        "current_run": current,
        "last_completed_run": None if not last else {
            "scheduled_at": kst(last["scheduled_at"]),
            "completed_at": kst(last["completed_at"]),
            "status": last["status"],
            "duration_seconds": round(int(last["duration_ms"] or 0) / 1000, 1),
            "total_targets": int(last["total_targets"] or 0),
            "processed": int(last["processed"] or 0),
            "success": int(last["success"] or 0),
            "partial": int(last["partial"] or 0),
            "failed": int(last["failed"] or 0),
            "http_429": int(last["http_429"] or 0),
            "p50_ms": int(last["p50_ms"] or 0), "p95_ms": int(last["p95_ms"] or 0),
        },
        "oldest_last_metrics_at": kst(oldest),
        "max_staleness_seconds": (now - oldest) if oldest else None,
        # 매시 전체 갱신이면 어떤 클립도 1시간 남짓 이상 묵을 수 없다
        "starving": bool(oldest and (now - oldest) > 2 * 3600),
    }


async def recent_runs(limit: int = 24) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT scheduled_at, started_at, completed_at, status, total_targets,"
        " processed, success, partial, failed, http_429, duration_ms, p50_ms, p95_ms"
        " FROM singcup_sweep_runs WHERE event_id=? ORDER BY scheduled_at DESC LIMIT ?",
        (EVENT_ID, max(1, min(168, limit))))).fetchall()
    return [{**dict(r), "scheduled_at": kst(r["scheduled_at"]),
             "started_at": kst(r["started_at"]),
             "completed_at": kst(r["completed_at"]),
             "duration_seconds": round(int(r["duration_ms"] or 0) / 1000, 1)}
            for r in rows]


async def start_sweep_worker():
    # 예전에는 ENDED면 워커 자체를 띄우지 않았다. 이제 종료 후에도 지표를 갱신하므로
    # 게이트가 닫혀 있을 때만 기동을 건너뛴다(그때도 스케줄러가 300초마다 재확인하도록
    # 두는 대신, 완전히 꺼진 경우에만 반환한다).
    if not metrics_refresh_open() and event_status() == "ENDED":
        return
    await sweep_scheduler()


__all__ = ["run_sweep", "sweep_status", "sweep_targets", "recent_runs",
           "start_sweep_worker", "floor_hour", "required_rate", "ST_OK"]
