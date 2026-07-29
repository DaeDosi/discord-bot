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
import time
import uuid
from datetime import datetime, timedelta, timezone

import singcup_clips as sc
from singcup_collector import EVENT_ID, ST_OK, event_status

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

RUNNING, COMPLETED, PARTIAL = "running", "completed", "partial"
FAILED, MISSED, SKIPPED_OVERLAP = "failed", "missed", "skipped_overlap"


def floor_hour(ts: float) -> int:
    """그 시각이 속한 정각(epoch). KST는 UTC+9 정시라 epoch 절삭으로 충분하다."""
    return int(ts) - int(ts) % 3600


def kst(ts) -> str | None:
    return datetime.fromtimestamp(int(ts), KST).isoformat() if ts else None


class TokenBucket:
    """요청을 회차 내내 고르게 뿌린다 + 429를 만나면 스스로 감속한다.

    4분마다 수백 건을 몰아치는 대신 초당 rate건으로 흘린다. 상대 API가 429나
    5xx를 주기 시작하면 곱셈 감소(×0.5)로 즉시 물러서고, 조용하면 아주 천천히
    (+5%/회) 회복한다 — 감속은 빠르게, 증속은 느리게가 안전한 쪽이다.
    """

    def __init__(self, rate: float, cap: float, floor: float | None = None):
        self.floor = MIN_RATE if floor is None else floor
        self.rate = max(self.floor, min(rate, cap))
        self.cap = cap
        self.tokens = 1.0
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()
        self.throttled = 0

    async def acquire(self):
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.rate,
                                  self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self.tokens) / max(self.rate, 1e-6))

    def slow_down(self, why: str):
        self.rate = max(self.floor, self.rate * 0.5)
        self.throttled += 1
        sc._log({"event": "sweep_throttle", "level": "warning",
                 "reason": why, "new_rate": round(self.rate, 3)})

    def recover(self):
        if self.rate < self.cap:
            self.rate = min(self.cap, self.rate * 1.05)


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
async def run_sweep(scheduled_at: int | None = None, *, run_id: str | None = None) -> dict:
    """한 회차 — 대상 전체를 속도 제한 아래 갱신하고 마지막에 순위를 재계산한다."""
    scheduled_at = scheduled_at or floor_hour(time.time())
    if run_id is None:
        busy = await _active_run(scheduled_at)
        if busy:
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
    targets = await sweep_targets(scheduled_at)
    total = len(targets)
    rate = min(MAX_RATE, max(MIN_RATE, required_rate(total)))
    bucket = TokenBucket(rate, MAX_RATE)
    await _progress(run_id, total_targets=total, rate_limit=round(rate, 3))
    sc._log({"event": "sweep_start", "run_id": run_id,
             "scheduled_at": kst(scheduled_at), "total_targets": total,
             "rate_per_second": round(rate, 3), "concurrency": CONCURRENCY,
             "eta_minutes": round(total / max(rate, 1e-6) / 60, 1)})

    tally = {"success": 0, "partial": 0, "failed": 0, "fetch_failed": 0}
    repaired = 0
    lat: list[int] = []
    changed_owners: set[str] = set()
    done = 0
    sc._take_api_counters()
    client = sc._get_client()
    sem = asyncio.Semaphore(max(1, CONCURRENCY))
    lock = asyncio.Lock()

    async def one(t: dict):
        nonlocal done, repaired
        await bucket.acquire()
        item = {"clipUID": t["clip_uid"], "videoId": t["video_id"],
                "recId": t["rec_id"] or "{}"}
        t0 = time.monotonic()
        before_429 = sc._api_counter["http_429"]
        async with sem:
            card = await sc.fetch_card(client, item)
        dt = int((time.monotonic() - t0) * 1000)
        now = int(time.time())
        async with lock:
            lat.append(dt)
            if sc._api_counter["http_429"] > before_429:
                bucket.slow_down("http_429")
            elif card is None:
                bucket.slow_down("fetch_failed")
            else:
                bucket.recover()
            if card is None:
                tally["fetch_failed"] += 1
                tally["failed"] += 1
                # last_metrics_at을 올려 이 회차에서 무한 재시도하지 않게 하고,
                # 실패 큐에 넣어 백오프로 따로 다시 시도한다.
                await sc._apply_metrics(t["clip_uid"], 0, 0, False, False, now)
                await sc._queue_retry({"clipUID": t["clip_uid"],
                                       "videoId": t["video_id"],
                                       "recId": t["rec_id"] or "{}"},
                                      "sweep card fetch failed", now)
            else:
                state = await sc._apply_metrics(
                    t["clip_uid"], card["heart_count"], card["view_count"],
                    card["heart_ok"], card["view_ok"], now)
                tally["success" if state == "ok" else state] += 1
                if state != "failed":
                    changed_owners.add(t["owner_channel_id"])
                await sc._clear_retry(t["clip_uid"])
        # 썸네일이 비어 있으면 이 기회에 메운다. 카드 API는 썸네일을 주지 않고
        # 목록 재스캔은 아는 페이지에서 멈추므로, 전체를 도는 여기가 유일한 기회다.
        # 이미 값이 있는 클립에는 추가 요청이 나가지 않는다.
        if not t["thumbnail_image_url"]:
            await bucket.acquire()
            detail = await sc.fetch_clip_detail(client, t["clip_uid"])
            async with lock:
                if detail and await sc.repair_clip_media(t["clip_uid"], detail):
                    repaired += 1

        async with lock:
            done += 1
            if done % PROGRESS_EVERY == 0:
                await (await get_db()).commit()
                # 429·호출 수도 같이 흘려 둔다 — 회차가 끝나야만 보이면 단계적
                # 상향을 판단할 수 없다(한 회차가 한 시간이다).
                await _progress(run_id, processed=done, rate_limit=round(bucket.rate, 3),
                                http_429=sc._api_counter["http_429"],
                                api_calls=sc._api_counter["calls"], **tally)

    try:
        await asyncio.gather(*[one(t) for t in targets])
        await (await get_db()).commit()
        # 대표 클립 재선정·점수·순위·스냅샷은 배치가 끝난 뒤 한 번만 계산한다.
        # 일반 클립이 대표를 추월했다면 여기서 대표가 바뀐다(_build_reps가 전체
        # 클립에서 스트리머별 최고 하트를 다시 고른다).
        # 이력 스냅샷은 **여기서만** 남긴다(시간 버킷당 한 세트).
        # 다른 경로는 순위만 즉시 맞추고 이력은 건드리지 않는다.
        await sc.recompute_ranking(int(time.time()), client=client, save_snapshot=True)
    except Exception as e:
        api = sc._take_api_counters()
        await _progress(run_id, status=FAILED, processed=done,
                        completed_at=int(time.time()), note=str(e)[:200],
                        api_calls=api["calls"], http_429=api["http_429"], **tally)
        sc._log({"event": "sweep_failed", "level": "warning", "run_id": run_id,
                 "processed": done, "detail": str(e)[:200]})
        return {"status": FAILED, "run_id": run_id, "processed": done,
                "note": str(e)[:200]}

    api = sc._take_api_counters()
    dur = int((time.monotonic() - started) * 1000)
    left = await sweep_targets(scheduled_at)
    status = COMPLETED if not left else PARTIAL
    await _progress(run_id, status=status, processed=done,
                    completed_at=int(time.time()), duration_ms=dur,
                    api_calls=api["calls"], http_429=api["http_429"],
                    p50_ms=_pct(lat, 0.5), p95_ms=_pct(lat, 0.95),
                    rate_limit=round(bucket.rate, 3),
                    note=("" if not left else f"{len(left)}건 미처리"), **tally)
    sc._log({"event": "sweep_done", "run_id": run_id, "status": status,
             "scheduled_at": kst(scheduled_at), "total_targets": total,
             "processed": done, "remaining": len(left), **tally,
             "duration_seconds": round(dur / 1000, 1),
             "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95),
             "api_calls": api["calls"], "http_429": api["http_429"],
             "throttled": bucket.throttled, "thumbnails_repaired": repaired,
             "changed_streamers": len(changed_owners)})
    return {"status": status, "run_id": run_id, "scheduled_at": kst(scheduled_at),
            "total_targets": total, "processed": done, "remaining": len(left),
            "duration_seconds": round(dur / 1000, 1),
            "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95),
            "http_429": api["http_429"], "thumbnails_repaired": repaired, **tally}


# ── 스케줄러 ───────────────────────────────────────────────────────────────
async def sweep_scheduler():
    """KST 매시 정각(0 * * * *)에 회차를 시작한다. 시작하자마자 돌지 않는다."""
    if os.getenv("SINGCUP_ENABLED", "true").lower() in ("0", "false", "no"):
        return
    await asyncio.sleep(float(os.getenv("SINGCUP_SWEEP_START_DELAY", "20")))
    while True:
        now = time.time()
        sched = floor_hour(now)
        try:
            if event_status() != "LIVE":
                pass                                  # 이벤트 기간 밖에서는 쉰다
            elif now - sched <= GRACE_SECONDS:
                # 배포/재시작이 정각 직후에 걸린 경우까지 그 회차를 살린다
                await run_sweep(sched)
            elif not await _exists(sched):
                # 정각을 놓쳤다 — 몰아서 실행하지 않고 기록만 남기고 다음 정각을 기다린다
                await _record(sched, MISSED,
                              f"정각 이후 {int(now - sched)}초 뒤 기동 — 이번 회차 건너뜀")
                sc._log({"event": "sweep_missed", "level": "warning",
                         "scheduled_at": kst(sched),
                         "late_seconds": int(now - sched)})
        except Exception as e:
            sc._log({"event": "sweep_loop_error", "level": "warning",
                     "detail": str(e)[:200]})
        await asyncio.sleep(max(5.0, (sched + 3600) - time.time() + 1.0))


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
            "behind_schedule": bool(
                eta and eta > int(cur["scheduled_at"]) + TARGET_MINUTES * 60),
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
        "schedule": "0 * * * *",
        "next_scheduled_at": kst(floor_hour(now) + 3600),
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
    if event_status() == "ENDED":
        return
    await sweep_scheduler()


__all__ = ["run_sweep", "sweep_status", "sweep_targets", "recent_runs",
           "start_sweep_worker", "floor_hour", "required_rate", "ST_OK"]
