"""Change Set B2 — 권위 감사(anti-entropy) 테스트.

핵심은 **79xM38ged7 구조**다: 카드 API는 200을 주는데 상세 API는 삭제 404인 클립.
Change Set B는 이 클립을 영원히 확인하지 못했다. 여기서는 특정 UID를 하드코딩하지
않고 그 *구조*를 재현해, Hot/Cold 어느 레인으로든 자동 선택되는지 본다.

conftest의 공용 `db` 픽스처를 쓴다. sys.modules에서 모듈을 지워 새로 import하면
같은 세션의 다른 테스트 파일이 붙들고 있는 모듈 객체가 갈라져(연결이 닫힌 옛
database를 계속 쓰게 되어) 전혀 무관한 테스트 수백 개가 무너진다 — 실측으로 확인했다.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import tokenize
from pathlib import Path

import pytest
import singcup_audit as audit
import singcup_clips as sc

import database

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(db, monkeypatch):
    """플래그를 켠 상태의 (db, sc, audit).

    플래그는 전부 호출 시점에 os.getenv로 읽으므로 monkeypatch.setenv로 충분하다
    (모듈을 다시 import할 필요가 없다).
    """
    monkeypatch.setenv("SINGCUP_DELETION_RECONCILE_ENABLED", "true")
    monkeypatch.setenv("SINGCUP_DELETION_RECONCILE_SHADOW", "false")
    monkeypatch.setenv("SINGCUP_DELETION_HOT_ENABLED", "true")
    monkeypatch.setenv("SINGCUP_DELETION_COLD_ENABLED", "true")
    _reset_audit_runtime()
    yield db
    _reset_audit_runtime()


def _reset_audit_runtime():
    """프로세스 안 상태만 초기화한다(진행 상태는 DB에 있다)."""
    audit._circuit.state = audit.Circuit.CLOSED
    audit._circuit._events = []
    audit._circuit._opened_at = 0.0
    audit._circuit._probe_ok = 0
    audit._circuit.opened_count = 0
    audit._latency_ms.clear()
    for k in audit._stats:
        audit._stats[k] = 0
    audit._hot_bucket.rate = audit.HOT_RATE_CAP
    audit._total_bucket.rate = audit.TOTAL_RATE_CAP
    audit._cold_bucket.rate = audit.COLD_RATE_CAP


# ── 도우미 ─────────────────────────────────────────────────────────────────
_INS = (
    "INSERT INTO singcup_clips (clip_uid,event_id,owner_channel_id,video_id,"
    "clip_title,thumbnail_image_url,description,created_at,heart_count,view_count,"
    "duration,adult,metrics_ok,active,missing_scan_count,first_collected_at,"
    "last_collected_at,row_updated_at,deletion_state,audit_last_at,audit_next_at) "
    "VALUES (?,?,?,'v','t','','#싱드컵',?,?,?,60,0,1,?,?,?,?,?,?,?,?)")


async def _seed(db, sc, uid, owner="o1", *, heart=10, view=100, active=1,
                state="active", checks=0, audit_last=0, audit_next=0, now=None):
    now = now or int(time.time())
    await db.execute(_INS, (uid, sc.EVENT_ID, owner, now, heart, view, active,
                            checks, now, now, now, state, audit_last, audit_next))
    await db.commit()


async def _streamer(db, sc, cid, rep, now=None):
    now = now or int(time.time())
    await db.execute(
        "INSERT OR REPLACE INTO singcup_streamers (channel_id,event_id,channel_name,"
        "representative_clip_uid,tagged_clip_count,row_updated_at) VALUES (?,?,?,?,2,?)",
        (cid, sc.EVENT_ID, cid, rep, now))
    await db.commit()


class FakeResponse:
    def __init__(self, status, payload=None, ctype="application/json"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = {"content-type": ctype}

    def json(self):
        return self._payload


class FakeClient:
    """상세 API만 흉내낸다. 카드 API는 부르지 않는다(그게 이 기능의 요지다)."""

    def __init__(self, table: dict, *, record: list | None = None):
        self.table = table
        self.record = record if record is not None else []

    async def get(self, url, params=None, headers=None, timeout=None, **kw):
        # 확정 뒤 recompute_ranking이 채널 API를 부른다 — 감사와 무관하므로
        # 빈 200으로 받아 넘긴다(여기서 세지도 않는다).
        if "/channels/" in url:
            return FakeResponse(200, {"code": 200, "content": {}})
        uid = url.rstrip("/").split("/")[-2]
        self.record.append(uid)
        r = self.table.get(uid, ("alive", None))
        kind = r[0]
        if kind == "deleted":
            return FakeResponse(404, {"code": 404, "message": "삭제된 클립입니다."})
        if kind == "gone":
            return FakeResponse(410, {"code": 410})
        if kind == "429":
            return FakeResponse(429, {})
        if kind == "500":
            return FakeResponse(500, {})
        if kind == "timeout":
            import httpx
            raise httpx.ReadTimeout("timeout")
        return FakeResponse(200, {"code": 200, "content": {
            "clipUID": uid, "blindType": None}})


# ── 1. 79xM38ged7 구조 ─────────────────────────────────────────────────────
def test_card_ok_but_detail_404_clip_is_selected_and_retired(env):
    """카드는 200인데 상세는 404인 클립 — B가 놓쳤던 정확한 구조."""
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        # 카드가 정상이라 의심 표시가 하나도 붙지 않은 상태 그대로 심는다.
        await _seed(db, sc, "ghost", "o1", heart=64, view=525, now=now)
        await _seed(db, sc, "live1", "o1", heart=69, view=300, now=now)
        await _streamer(db, sc, "o1", "live1", now)

        client = FakeClient({"ghost": ("deleted",)})
        # 첫 사이클 — 명시적 404 1회 → 의심 상태(확정 아님)
        r1 = await audit.run_audit_cycle(client=client, now=now)
        row = await (await db.execute(
            "SELECT deletion_state, active, missing_scan_count FROM singcup_clips "
            "WHERE clip_uid='ghost'")).fetchone()
        assert row["deletion_state"] == sc.DEL_SUSPECTED, dict(row)
        assert row["active"] == 1, "404 한 번으로 비활성화하면 안 된다"
        assert row["missing_scan_count"] == 1

        # 최소 간격 전에는 두 번째로 세지 않는다
        r2 = await audit.run_audit_cycle(
            client=client, now=now + sc.DELETION_MIN_INTERVAL_SECONDS - 1)
        row = await (await db.execute(
            "SELECT deletion_state, active FROM singcup_clips "
            "WHERE clip_uid='ghost'")).fetchone()
        assert row["deletion_state"] == sc.DEL_SUSPECTED
        assert row["active"] == 1

        # 간격 이후 두 번째 독립 404 → 확정 + 비활성화
        await audit.run_audit_cycle(
            client=client, now=now + sc.DELETION_MIN_INTERVAL_SECONDS + 1)
        row = await (await db.execute(
            "SELECT deletion_state, active FROM singcup_clips "
            "WHERE clip_uid='ghost'")).fetchone()
        assert row["deletion_state"] == sc.DEL_CONFIRMED, dict(row)
        assert row["active"] == 0
        return r1, r2

    run(go())


def _code_only(path: Path) -> str:
    """주석과 문자열 리터럴을 걷어낸 실행 코드.

    docstring에 실측 사례(UID)를 적는 것은 문서이고, 코드에서 그 UID를 분기
    조건으로 쓰는 것은 하드코딩이다. 둘을 구분해야 문서를 지우지 않고 검사할 수 있다.
    """
    out = []
    with open(path, encoding="utf-8") as f:
        for tok in tokenize.generate_tokens(f.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)


def test_no_hardcoded_uid_anywhere(env):
    """특정 UID 하드코딩 금지 — 같은 구조를 자동으로 고쳐야 한다."""
    for name in ("web/backend/singcup_audit.py", "web/backend/singcup_clips.py",
                 "web/backend/singcup_sweep.py"):
        code = _code_only(ROOT / name)
        assert "79xM38ged7" not in code, name
        assert "Lzbtbo6cVL" not in code, name


# ── 2. 힌트는 삭제 근거가 아니다 ────────────────────────────────────────────
def test_hints_never_change_state_or_active(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "c1", "o1", now=now)
        for reason in (audit.HINT_NEW_SIBLING, audit.HINT_REP_CHANGED,
                       audit.HINT_CARD_EMPTY, audit.HINT_METRICS_FROZEN):
            assert await audit.hint_clip("c1", reason, now)
            row = await (await db.execute(
                "SELECT active, deletion_state, missing_scan_count, audit_hint "
                "FROM singcup_clips WHERE clip_uid='c1'")).fetchone()
            assert row["active"] == 1
            assert row["deletion_state"] == sc.DEL_ACTIVE
            assert row["missing_scan_count"] == 0
            assert row["audit_hint"] == reason

    run(go())


def test_unknown_hint_reason_is_rejected(env):
    run = env
    with pytest.raises(ValueError):
        run(audit.hint_clip("c1", "made_up_reason"))


def test_frozen_metrics_alone_never_confirms(env):
    """지표 고정은 힌트일 뿐 — 상세 API가 200이면 아무 일도 없어야 한다."""
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "quiet", "o1", now=now)
        await audit.note_metrics_frozen("quiet", audit.FROZEN_HINT_THRESHOLD, now)
        row = await (await db.execute(
            "SELECT audit_hint, audit_hint_at FROM singcup_clips "
            "WHERE clip_uid='quiet'")).fetchone()
        assert row["audit_hint"] == audit.HINT_METRICS_FROZEN
        assert row["audit_hint_at"] > 0

        await audit.run_audit_cycle(client=FakeClient({}), now=now)
        row = await (await db.execute(
            "SELECT active, deletion_state, audit_verdict, audit_hint_at "
            "FROM singcup_clips WHERE clip_uid='quiet'")).fetchone()
        assert row["active"] == 1
        assert row["deletion_state"] == sc.DEL_ACTIVE
        assert row["audit_verdict"] == audit.V_ALIVE
        assert row["audit_hint_at"] == 0, "검사받은 힌트는 지워져야 한다"

    run(go())


def test_frozen_below_threshold_does_not_hint(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "c1", now=now)
        assert not await audit.note_metrics_frozen(
            "c1", audit.FROZEN_HINT_THRESHOLD - 1, now)
        row = await (await db.execute(
            "SELECT audit_hint_at FROM singcup_clips WHERE clip_uid='c1'")).fetchone()
        assert row["audit_hint_at"] == 0

    run(go())


# ── 3. 이벤트 기반 예약 ────────────────────────────────────────────────────
def test_new_clip_schedules_siblings_but_never_deactivates(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        for uid in ("old1", "old2", "old3"):
            await _seed(db, sc, uid, "o1", now=now)
        await _seed(db, sc, "brand_new", "o1", now=now)
        n = await audit.hint_owner_siblings("o1", exclude_uid="brand_new", now=now)
        assert n == 3
        rows = {r["clip_uid"]: dict(r) for r in await (await db.execute(
            "SELECT clip_uid, active, audit_hint, audit_hint_at FROM singcup_clips"
        )).fetchall()}
        for uid in ("old1", "old2", "old3"):
            assert rows[uid]["audit_hint"] == audit.HINT_NEW_SIBLING
            assert rows[uid]["active"] == 1
        assert rows["brand_new"]["audit_hint_at"] == 0, "새 클립 자신은 제외"

    run(go())


def test_healthy_multi_clip_owner_is_not_retired(env):
    """정상 클립을 여러 개 올린 스트리머 — 힌트가 붙어도 하나도 안 지워진다."""
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        uids = [f"ok{i}" for i in range(5)]
        for u in uids:
            await _seed(db, sc, u, "o1", now=now)
        await _seed(db, sc, "newest", "o1", now=now)
        await audit.hint_owner_siblings("o1", exclude_uid="newest", now=now)
        await audit.run_audit_cycle(client=FakeClient({}), now=now)
        rows = await (await db.execute(
            "SELECT COUNT(*) FROM singcup_clips WHERE active=1")).fetchone()
        assert rows[0] == 6
        states = await (await db.execute(
            "SELECT COUNT(*) FROM singcup_clips WHERE deletion_state<>'active'"
        )).fetchone()
        assert states[0] == 0

    run(go())


def test_representative_change_schedules_previous_rep(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "oldrep", "o1", now=now)
        assert await sc._audit_hint("oldrep", audit.HINT_REP_CHANGED)
        row = await (await db.execute(
            "SELECT audit_hint, active FROM singcup_clips "
            "WHERE clip_uid='oldrep'")).fetchone()
        assert row["audit_hint"] == audit.HINT_REP_CHANGED
        assert row["active"] == 1

    run(go())


def test_hinted_clip_is_selected_before_cold_ones(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        for i in range(30):
            await _seed(db, sc, f"cold{i:02d}", "o1", now=now)
        await _seed(db, sc, "hinted", "o2", now=now)
        await audit.hint_clip("hinted", audit.HINT_NEW_SIBLING, now)
        targets = await audit.select_targets(now, 3)
        assert targets[0]["clip_uid"] == "hinted"
        assert targets[0]["lane"] == "hot"
        assert all(t["lane"] == "cold" for t in targets[1:])

    run(go())


# ── 4. Cold lane 전체 순회 ─────────────────────────────────────────────────
def test_every_active_clip_is_eventually_selected(env):
    """카드 상태와 무관하게 모든 활성 클립이 한 번씩 뽑힌다 — 누락·기아 0."""
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        uids = [f"c{i:04d}" for i in range(300)]
        for u in uids:
            await _seed(db, sc, u, f"o{u[-2:]}", now=now)

        seen: set[str] = set()
        client = FakeClient({}, record=[])
        for _ in range(20):
            r = await audit.run_audit_cycle(limit=50, client=client, now=now)
            if r.get("status") == "idle":
                break
            seen |= set(client.record)
            client.record.clear()
        assert seen == set(uids), f"누락 {len(set(uids) - seen)}건"

    run(go())


def test_no_duplicate_within_one_coverage_period(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        for i in range(120):
            await _seed(db, sc, f"c{i:04d}", now=now)
        client = FakeClient({}, record=[])
        for _ in range(6):
            await audit.run_audit_cycle(limit=50, client=client, now=now)
        assert len(client.record) == len(set(client.record)), "같은 회차에 중복 호출"

    run(go())


def test_simulated_full_event_scale_has_no_starvation():
    """6,358건 규모 시뮬레이션 — 선택 규칙만 반복해 누락·중복·기아를 본다.

    DB 없이 돌린다(선택 규칙은 순수 함수라 이게 가능하다). 우선순위 큐로 돌려
    O(n log n)이다 — 초 단위로 전수를 다시 훑으면 시뮬레이션 자체가 안 끝난다.
    """
    import heapq

    import singcup_audit as audit

    n = 6358
    coverage = 12 * 3600
    now = 1_800_000_000
    uids = [f"clip{i:05d}" for i in range(n)]
    rate = audit.effective_cold_rate(n, cap=0.2, coverage_hours=12, floor=0.02)
    assert rate > 0
    gap = 1.0 / rate                       # 요청 간 평균 간격(초)

    # (next_at, uid) — 처음에는 전부 0(한 번도 검사 안 함)
    heap = [(0, u) for u in uids]
    heapq.heapify(heap)
    seen: dict[str, int] = {}
    picks: list[str] = []
    t = float(now)
    deadline = now + coverage
    while heap and t <= deadline:
        next_at, uid = heap[0]
        if next_at > t:                    # 아직 기한 전 — 시간을 당겨 준다
            t = float(next_at)
            continue
        heapq.heappop(heap)
        picks.append(uid)
        if uid not in seen:
            seen[uid] = int(t)
        heapq.heappush(heap, (audit.next_cold_at(uid, int(t), coverage), uid))
        t += gap

    assert len(seen) == n, f"12시간 안에 {n - len(seen)}건이 안 뽑혔다"
    # 첫 바퀴 안에서는 중복이 없다(각자 최소 반 주기 뒤로 밀린다)
    first_round = picks[:n]
    assert len(set(first_round)) == n, "첫 바퀴에 중복 선택이 있다"
    # 기아 없음 — 가장 늦게 뽑힌 행도 커버리지 안에 든다
    assert max(seen.values()) - now <= coverage

    # 다음 바퀴가 한 시점에 몰리지 않는다(결정적 jitter)
    buckets: dict[int, int] = {}
    for next_at, _ in heap:
        buckets[next_at // 3600] = buckets.get(next_at // 3600, 0) + 1
    assert max(buckets.values()) < n * 0.35, f"다음 바퀴가 몰린다: {buckets}"


def test_stable_offset_is_process_independent():
    """PYTHONHASHSEED가 달라도 같은 값이어야 한다(내장 hash 금지)."""
    import subprocess
    # import 시점에 한국어 로그를 stdout에 찍는 모듈이 있으므로 (1) 파이프를 UTF-8로
    # 고정하고 — 부모의 로케일이 cp949면 text=True 기본 디코딩이 깨진다 — (2) 값은
    # 로그와 섞이지 않게 마커로 뽑는다.
    code = ("import sys; sys.path[:0]=['.','web/backend'];"
            "import singcup_audit as a;"
            "print('STABLE_OFFSET=%d' % a.stable_offset('abcDEF123', 43200))")
    outs = set()
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed,
                   PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="strict", env=env)
        assert r.returncode == 0, r.stderr
        values = [ln.split("=", 1)[1].strip() for ln in r.stdout.splitlines()
                  if ln.startswith("STABLE_OFFSET=")]
        assert len(values) == 1, r.stdout
        outs.add(values[0])
    assert len(outs) == 1, outs


def test_no_builtin_hash_in_audit():
    """내장 hash()는 PYTHONHASHSEED에 따라 달라져 순서 편향을 만든다."""
    import ast
    tree = ast.parse((ROOT / "web/backend/singcup_audit.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "hash", f"line {node.lineno}"


# ── 5. 속도 계산 / 토큰 버킷 ───────────────────────────────────────────────
@pytest.mark.parametrize("rate", [0.15, 0.2, 0.5])
def test_token_bucket_progresses_at_low_rates(rate):
    """rate<1에서도 진행해야 한다 — 용량을 rate로 잡던 P0 회귀 방지."""
    from utils.token_bucket import TokenBucket

    async def go():
        b = TokenBucket(rate, rate, 0.01)
        assert b.capacity >= 1.0
        t0 = time.monotonic()
        await b.acquire()          # 초기 토큰 1개는 즉시
        await asyncio.wait_for(b.acquire(), timeout=1.0 / rate + 5)
        return time.monotonic() - t0

    took = asyncio.run(go())
    assert took >= 1.0 / rate - 0.5


def test_token_bucket_still_progresses_after_slow_down():
    from utils.token_bucket import TokenBucket

    async def go():
        b = TokenBucket(0.2, 0.2, 0.05)
        b.slow_down("429")
        b.slow_down("429")
        assert b.rate >= b.floor > 0
        assert b.capacity >= 1.0
        await asyncio.wait_for(b.acquire(), timeout=2.0)

    asyncio.run(go())


def test_effective_rate_never_zero_and_respects_cap():
    import singcup_audit as audit
    assert audit.effective_cold_rate(0, cap=0.2, floor=0.02) > 0
    assert audit.effective_cold_rate(
        6358, cap=0.2, coverage_hours=12, floor=0.02) == pytest.approx(
        6358 / (12 * 3600), rel=1e-6)
    # 대상이 많아 필요 속도가 상한을 넘으면 상한에서 멈춘다(임의 증속 금지)
    assert audit.effective_cold_rate(
        10 ** 7, cap=0.2, coverage_hours=12, floor=0.02) == 0.2
    # 바닥이 상한보다 커도 상한을 넘지 않는다
    assert audit.effective_cold_rate(1, cap=0.2, floor=99.0) == 0.2


def test_required_rate_matches_documented_numbers():
    import singcup_audit as audit
    assert audit.required_rate(6358, 12) == pytest.approx(0.1472, abs=1e-4)


# ── 6. 판정 상태 머신 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("kind", ["429", "500", "timeout"])
def test_transient_errors_never_delete(env, kind):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "c1", now=now)
        for i in range(5):
            await audit.run_audit_cycle(
                client=FakeClient({"c1": (kind,)}), now=now + i * 100000)
        row = await (await db.execute(
            "SELECT active, deletion_state, missing_scan_count, audit_fail_count, "
            "audit_verdict FROM singcup_clips WHERE clip_uid='c1'")).fetchone()
        assert row["active"] == 1
        assert row["deletion_state"] == sc.DEL_ACTIVE
        assert row["missing_scan_count"] == 0, "일시 오류를 삭제 확인으로 세면 안 된다"
        assert row["audit_verdict"] == audit.V_INCONCLUSIVE
        assert row["audit_fail_count"] >= 1

    run(go())


def test_alive_response_recovers_suspected_clip(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "c1", now=now, state=sc.DEL_SUSPECTED, checks=1)
        await audit.run_audit_cycle(client=FakeClient({}), now=now + 100000)
        row = await (await db.execute(
            "SELECT active, deletion_state, missing_scan_count FROM singcup_clips "
            "WHERE clip_uid='c1'")).fetchone()
        assert row["active"] == 1
        # suspected → active (확정에서 복구된 경우에만 recovered다)
        assert row["deletion_state"] == sc.DEL_ACTIVE
        assert row["missing_scan_count"] == 0

    run(go())


def test_backoff_grows_and_caps():
    import singcup_audit as audit
    a = [audit.backoff_seconds(i) for i in range(1, 12)]
    assert a == sorted(a)
    assert a[0] == audit.BACKOFF_BASE_SECONDS
    assert a[-1] <= audit.BACKOFF_MAX_SECONDS


def test_bad_uid_is_inconclusive_not_deleted(env):
    run = env

    async def go():
        v, code, why = await audit.probe_deleted(FakeClient({}), "../../etc/passwd")
        assert v == audit.V_INCONCLUSIVE and why == "bad_uid"
        v, _, _ = await audit.probe_deleted(FakeClient({}), "")
        assert v == audit.V_INCONCLUSIVE

    run(go())


def test_only_chzzk_host_and_path_is_called():
    assert sc.CLIP_DETAIL_API.startswith("https://api.chzzk.naver.com/service/v1/clips/")
    assert audit._ALLOWED_HOST in sc.CLIP_DETAIL_API
    # 외부에서 URL을 받아 부르는 자리가 없어야 한다
    text = (ROOT / "web/backend/singcup_audit.py").read_text(encoding="utf-8")
    assert "client.get(url" not in text


def test_redirects_are_not_followed():
    """리디렉션을 따라가면 임의 호스트의 404가 삭제 근거가 된다."""
    import httpx
    import singcup_clips as sc
    c = sc._get_client()
    assert isinstance(c, httpx.AsyncClient)
    assert c.follow_redirects is False


def test_deleted_verdict_requires_meaningful_404(env):
    """3xx나 형식 오류는 삭제가 아니다."""
    run = env

    class Weird(FakeClient):
        async def get(self, url, headers=None, timeout=None):
            return FakeResponse(302, {})

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "c1", now=now)
        await audit.run_audit_cycle(client=Weird({}), now=now)
        row = await (await db.execute(
            "SELECT active, deletion_state FROM singcup_clips "
            "WHERE clip_uid='c1'")).fetchone()
        assert row["active"] == 1 and row["deletion_state"] == sc.DEL_ACTIVE

    run(go())


# ── 7. Shadow / 단계적 활성화 ──────────────────────────────────────────────
def test_shadow_mode_changes_nothing(env, monkeypatch):
    run = env
    monkeypatch.setenv("SINGCUP_DELETION_RECONCILE_SHADOW", "true")

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "ghost", now=now)
        snap = "SELECT active, deletion_state, missing_scan_count FROM singcup_clips"
        before = [dict(r) for r in await (await db.execute(snap)).fetchall()]
        client = FakeClient({"ghost": ("deleted",)})
        for i in range(4):
            await audit.run_audit_cycle(client=client, now=now + i * 100000)
        after = [dict(r) for r in await (await db.execute(snap)).fetchall()]
        assert before == after, "Shadow에서 상태가 바뀌었다"
        assert len(client.record) >= 2, "Shadow에서도 실제 판정은 해야 한다"

    run(go())


def test_disabled_makes_no_requests(env, monkeypatch):
    run = env
    monkeypatch.setenv("SINGCUP_DELETION_RECONCILE_ENABLED", "false")

    async def go():
        db = await sc.get_db()
        await _seed(db, sc, "ghost")
        client = FakeClient({"ghost": ("deleted",)})
        r = await audit.run_audit_cycle(client=client)
        assert r["status"] == "disabled"
        assert client.record == []

    run(go())


def test_hot_only_phase_does_not_touch_cold(env, monkeypatch):
    """Phase 2 — Hot lane만 상태를 바꾼다."""
    run = env
    monkeypatch.setenv("SINGCUP_DELETION_COLD_ENABLED", "false")

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "hot1", now=now)
        await _seed(db, sc, "cold1", now=now)
        await audit.hint_clip("hot1", audit.HINT_CARD_EMPTY, now)
        client = FakeClient({"hot1": ("deleted",), "cold1": ("deleted",)})
        for i in range(4):
            await audit.run_audit_cycle(client=client, now=now + i * 100000)
        rows = {r["clip_uid"]: dict(r) for r in await (await db.execute(
            "SELECT clip_uid, active, deletion_state FROM singcup_clips")).fetchall()}
        assert rows["hot1"]["deletion_state"] == sc.DEL_CONFIRMED
        assert rows["hot1"]["active"] == 0
        assert rows["cold1"]["deletion_state"] == sc.DEL_ACTIVE
        assert rows["cold1"]["active"] == 1

    run(go())


# ── 8. 회로 차단기 ─────────────────────────────────────────────────────────
def test_circuit_opens_then_half_opens_then_closes():
    from singcup_audit import Circuit
    c = Circuit(threshold=3, window_seconds=60, cooldown_seconds=10, probes=2)
    t = 1000.0
    assert c.allow_cold(t)
    for _ in range(3):
        c.record_failure(t)
    assert c.current_state(t) == Circuit.OPEN
    assert not c.allow_cold(t)
    assert c.current_state(t + 5) == Circuit.OPEN
    assert c.current_state(t + 11) == Circuit.HALF_OPEN
    c.record_success(t + 11)
    assert c.current_state(t + 11) == Circuit.HALF_OPEN
    c.record_success(t + 12)
    assert c.current_state(t + 12) == Circuit.CLOSED
    assert c.opened_count == 1


def test_circuit_half_open_failure_reopens():
    from singcup_audit import Circuit
    c = Circuit(threshold=2, window_seconds=60, cooldown_seconds=5, probes=2)
    t = 0.0
    c.record_failure(t)
    c.record_failure(t)
    assert c.current_state(t + 6) == Circuit.HALF_OPEN
    c.record_failure(t + 6)
    assert c.current_state(t + 6) == Circuit.OPEN


def test_old_failures_expire_from_window():
    from singcup_audit import Circuit
    c = Circuit(threshold=3, window_seconds=10, cooldown_seconds=5, probes=1)
    c.record_failure(0.0)
    c.record_failure(1.0)
    c.record_failure(100.0)          # 앞의 둘은 창을 벗어났다
    assert c.current_state(100.0) == Circuit.CLOSED


def test_open_circuit_pauses_cold_lane(env, monkeypatch):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "cold1", now=now)
        audit._circuit.state = audit.Circuit.OPEN
        audit._circuit._opened_at = time.monotonic()
        client = FakeClient({"cold1": ("deleted",)})
        await audit.run_audit_cycle(client=client, now=now)
        assert client.record == [], "회로가 열렸는데 Cold lane이 요청했다"
        audit._circuit.state = audit.Circuit.CLOSED

    run(go())


# ── 9. 영속성 / 재시작 ─────────────────────────────────────────────────────
def test_progress_survives_restart(env):
    """진행 상태가 프로세스 메모리가 아니라 DB(audit_next_at)에 있다.

    '재시작'은 프로세스 안의 모든 가변 상태(지표·버킷·회로)를 초기값으로 되돌려
    흉내낸다. 진짜 재시작과 같은 조건이려면 그 상태 어디에도 clip_uid가 없어야
    하므로, 아래에서 그것도 함께 확인한다.
    """
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        for i in range(20):
            await _seed(db, sc, f"c{i:02d}", now=now)
        client = FakeClient({}, record=[])
        await audit.run_audit_cycle(limit=8, client=client, now=now)
        first = list(client.record)
        assert len(first) == 8

        _reset_audit_runtime()          # ← 재시작
        client2 = FakeClient({}, record=[])
        await audit.run_audit_cycle(limit=8, client=client2, now=now)
        assert not (set(first) & set(client2.record)), "재시작 후 처음부터 다시 돌았다"

    run(go())


def test_no_clip_progress_is_held_in_process_memory():
    """모듈 전역 어디에도 clip_uid별 진행 상태가 없어야 한다."""
    blob = repr({k: v for k, v in vars(audit).items()
                 if not k.startswith("__") and isinstance(v, (dict, set, list))})
    assert "clip_uid" not in blob
    for name in ("_stats",):
        for v in getattr(audit, name).values():
            assert isinstance(v, int), f"{name}에 집계가 아닌 값이 있다: {v!r}"


def test_no_offset_pagination_in_target_sql():
    """offset 페이지네이션 금지 — 행이 밀리면 건너뛰기와 중복을 동시에 만든다."""
    import re
    text = (ROOT / "web/backend/singcup_audit.py").read_text(encoding="utf-8")
    assert not re.search(r"OFFSET", text, re.IGNORECASE)


# ── 10. 동시성·원자성 (Change Set B의 보장 유지) ───────────────────────────
def test_clip_lock_prevents_double_processing(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "c1", now=now)
        token = await sc.acquire_clip_lock("c1")
        assert token
        client = FakeClient({"c1": ("deleted",)}, record=[])
        await audit.run_audit_cycle(client=client, now=now)
        assert client.record == [], "락이 걸린 클립을 건드렸다"
        await sc.release_clip_lock("c1", token)

    run(go())


def test_confirm_and_reselect_are_atomic(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "gone", "o1", heart=64, now=now)
        await _seed(db, sc, "next", "o1", heart=10, now=now)
        await _streamer(db, sc, "o1", "gone", now)
        client = FakeClient({"gone": ("deleted",)})
        await audit.run_audit_cycle(client=client, now=now)
        await audit.run_audit_cycle(
            client=client, now=now + sc.DELETION_MIN_INTERVAL_SECONDS + 1)
        row = await (await db.execute(
            "SELECT active, deletion_state FROM singcup_clips "
            "WHERE clip_uid='gone'")).fetchone()
        rep = await (await db.execute(
            "SELECT representative_clip_uid FROM singcup_streamers "
            "WHERE channel_id='o1'")).fetchone()
        assert row["active"] == 0 and row["deletion_state"] == sc.DEL_CONFIRMED
        assert rep[0] == "next", "비활성화만 되고 대표가 옛 UID로 남았다"

    run(go())


def test_lock_order_is_clip_then_owner():
    """교착 방지 — 두 락을 함께 쓰는 경로는 항상 clip → owner 순이다."""
    text = (ROOT / "web/backend/singcup_audit.py").read_text(encoding="utf-8")
    assert "acquire_owner_lock" not in text, (
        "감사 모듈은 owner 락을 직접 잡지 않는다 — 대표 재선정은 "
        "_confirm_deleted_and_reselect 안에서만 일어난다(clip → owner 순서 유지)")


# ── 11. 마이그레이션 / 공개 API ────────────────────────────────────────────
def test_migration_is_idempotent_and_preserves_rows(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        for i in range(5):
            await _seed(db, sc, f"c{i}", now=now)
        await _seed(db, sc, "z", active=0, state="unknown_legacy", now=now)
        snap = ("SELECT clip_uid, active, deletion_state, heart_count, view_count "
                "FROM singcup_clips ORDER BY 1")
        before = [tuple(r) for r in await (await db.execute(snap)).fetchall()]
        # 마이그레이션은 append-only + 멱등이다. 같은 연결에서 다시 돌려도
        # 행이 사라지거나 active가 바뀌면 안 된다.
        for _ in range(2):
            await database.init_db()
            after = [tuple(r) for r in await (await db.execute(snap)).fetchall()]
            assert after == before

    run(go())


def test_new_columns_default_safely(env):
    run = env

    async def go():
        db = await sc.get_db()
        await _seed(db, sc, "c1")
        row = await (await db.execute(
            "SELECT audit_last_at, audit_next_at, audit_verdict, audit_fail_count, "
            "audit_hint, audit_hint_at, metrics_frozen_count FROM singcup_clips "
            "WHERE clip_uid='c1'")).fetchone()
        assert row["audit_last_at"] == 0 and row["audit_next_at"] == 0
        assert row["audit_verdict"] == "" and row["audit_fail_count"] == 0
        assert row["audit_hint"] == "" and row["audit_hint_at"] == 0
        assert row["metrics_frozen_count"] == 0

    run(go())


def test_public_main_is_unaffected_and_writes_nothing(env):
    run = env

    async def go():
        import sqlite3
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "a", "o1", heart=10, now=now)
        await _seed(db, sc, "b", "o2", heart=5, now=now)
        await _streamer(db, sc, "o1", "a", now)
        await _streamer(db, sc, "o2", "b", now)
        path = database.DB_PATH
        snap = "SELECT * FROM singcup_clips ORDER BY clip_uid"
        before = [tuple(r) for r in sqlite3.connect(path).execute(snap)]
        entry, _src = await sc.load_main_entry(limit=50)
        after = [tuple(r) for r in sqlite3.connect(path).execute(snap)]
        assert before == after, "공개 조회가 DB에 썼다"
        assert {s["channelId"] for s in entry["data"]["streamers"]} == {"o1", "o2"}

    run(go())


def test_audit_status_exposes_no_secrets(env):
    run = env

    async def go():
        db = await sc.get_db()
        await _seed(db, sc, "c1")
        st = await audit.audit_status()
        blob = repr(st).lower()
        for bad in ("token", "secret", "owner=", "authorization", "cookie"):
            assert bad not in blob, bad
        for k in ("hot_queue_size", "cold_queue_size", "due_count",
                  "never_audited_count", "coverage_12h", "circuit_state",
                  "current_rate", "required_rate", "stalled",
                  "estimated_full_sweep_at", "p50_ms", "p95_ms"):
            assert k in st, k

    run(go())


def test_stats_have_bounded_cardinality():
    """clip_uid별 시계열을 메모리에 무제한 쌓지 않는다."""
    import singcup_audit as audit
    assert audit.LATENCY_SAMPLES <= 1000
    for _ in range(audit.LATENCY_SAMPLES * 3):
        audit._record_latency(1.0)
    assert len(audit._latency_ms) <= audit.LATENCY_SAMPLES
    assert all(not isinstance(v, dict) for v in audit._stats.values()), (
        "지표에 clip_uid 키를 가진 dict를 두면 카디널리티가 클립 수만큼 늘어난다")


def test_logs_never_carry_response_bodies_or_secrets():
    text = (ROOT / "web/backend/singcup_audit.py").read_text(encoding="utf-8")
    assert "r.text" not in text and "response.text" not in text
    assert "ADMIN_SECRET" not in text


def test_expired_hint_falls_back_to_cold_lane(env):
    """검사받지 못한 힌트는 TTL이 지나면 만료된다 — Hot lane이 무한히 부풀지 않게."""
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "old_hint", now=now)
        await audit.hint_clip("old_hint", audit.HINT_NEW_SIBLING, now)
        fresh = await audit.select_targets(now, 5)
        assert fresh[0]["lane"] == "hot"
        later = now + audit.HINT_TTL_SECONDS + 1
        stale = await audit.select_targets(later, 5)
        assert stale[0]["lane"] == "cold", "만료된 힌트가 아직 Hot이다"

    run(go())


def test_unmeasured_metrics_are_null_not_zero(env):
    """측정하지 않는 값을 0으로 내보내면 '없었다'로 읽힌다."""
    run = env

    async def go():
        st = await audit.audit_status()
        assert st["db_lock_retry"] is None

    run(go())


# ── 12. B2.1: strict OFF / 플래그 진리표 / 합산 예산 / 404 의미 ─────────────
@pytest.fixture()
def off(db, monkeypatch):
    """master OFF 상태(기본값)."""
    monkeypatch.setenv("SINGCUP_DELETION_RECONCILE_ENABLED", "false")
    _reset_audit_runtime()
    yield db
    _reset_audit_runtime()


async def _audit_cols(db, uid):
    r = await (await db.execute(
        "SELECT audit_last_at, audit_next_at, audit_verdict, audit_fail_count, "
        "audit_hint, audit_hint_at FROM singcup_clips WHERE clip_uid=?",
        (uid,))).fetchone()
    return dict(r)


async def _op_cols(db):
    rows = await (await db.execute(
        "SELECT clip_uid, active, deletion_state, missing_scan_count, "
        "heart_count, view_count FROM singcup_clips ORDER BY clip_uid")).fetchall()
    reps = await (await db.execute(
        "SELECT channel_id, representative_clip_uid FROM singcup_streamers "
        "ORDER BY channel_id")).fetchall()
    return [tuple(r) for r in rows], [tuple(r) for r in reps]


def test_strict_off_writes_nothing_and_calls_nothing(off):
    """master OFF면 힌트 DB UPDATE 0, 상세 API 요청 0."""
    run = off

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        for uid in ("a", "b", "c"):
            await _seed(db, sc, uid, "o1", now=now)
        before = await _audit_cols(db, "a")
        row_before = await _op_cols(db)

        # 실제 운영 경로 그대로 — 신규 클립·대표 변경·카드 결측·지표 고정
        assert await sc._audit_hint_siblings("o1", "c") == 0
        assert await sc._audit_hint("a", audit.HINT_REP_CHANGED) is False
        assert await sc._audit_hint("a", audit.HINT_CARD_EMPTY) is False
        assert await sc._audit_note_frozen("a", audit.FROZEN_HINT_THRESHOLD) is False
        assert await audit.hint_clip("a", audit.HINT_MANUAL) is False

        assert await _audit_cols(db, "a") == before, "OFF에서 감사 컬럼이 바뀌었다"
        assert await _op_cols(db) == row_before

        client = FakeClient({"a": ("deleted",)}, record=[])
        r = await audit.run_audit_cycle(client=client, now=now)
        assert r["status"] == "disabled"
        assert client.record == []

    run(go())


def test_repeated_same_hint_does_not_write_again(env):
    """같은 힌트를 회차마다 다시 걸지 않는다(쓰기 증폭 방지)."""
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "a", now=now)
        assert await audit.hint_clip("a", audit.HINT_CARD_EMPTY, now) is True
        for _ in range(10):
            assert await audit.hint_clip("a", audit.HINT_CARD_EMPTY, now) is False
        # 다른 사유로는 갱신된다
        assert await audit.hint_clip("a", audit.HINT_REP_CHANGED, now) is True

    run(go())


def test_frozen_hint_fires_only_at_the_crossing(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "a", now=now)
        th = audit.FROZEN_HINT_THRESHOLD
        assert await audit.note_metrics_frozen("a", th - 1, now) is False
        assert await audit.note_metrics_frozen("a", th, now) is True
        for extra in range(1, 20):
            assert await audit.note_metrics_frozen("a", th + extra, now) is False

    run(go())


def test_sibling_hint_makes_no_write_when_nothing_pending(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "new1", "o1", now=now)
        assert await audit.hint_owner_siblings("o1", exclude_uid="new1", now=now) == 0
        await _seed(db, sc, "old1", "o1", now=now)
        assert await audit.hint_owner_siblings("o1", exclude_uid="new1", now=now) == 1
        assert await audit.hint_owner_siblings("o1", exclude_uid="new1", now=now) == 0

    run(go())


# 진리표 — (ENABLED, SHADOW, HOT, COLD) → (힌트, 상세API 요청, 운영 상태 변경)
@pytest.mark.parametrize(
    "enabled_,shadow_,hot_,cold_,hint,requests,state_change",
    [
        ("false", "true", "false", "false", False, False, False),
        ("true", "true", "false", "false", True, False, False),
        ("true", "true", "true", "false", True, True, False),
        ("true", "true", "false", "true", True, True, False),
        ("true", "true", "true", "true", True, True, False),
        ("true", "false", "true", "false", True, True, True),
        ("true", "false", "false", "true", True, True, False),
        ("true", "false", "true", "true", True, True, True),
    ])
def test_feature_flag_truth_table(db, monkeypatch, enabled_, shadow_, hot_, cold_,
                                  hint, requests, state_change):
    """Hot 힌트가 붙은 클립 하나로 8개 조합을 그대로 시험한다."""
    monkeypatch.setenv("SINGCUP_DELETION_RECONCILE_ENABLED", enabled_)
    monkeypatch.setenv("SINGCUP_DELETION_RECONCILE_SHADOW", shadow_)
    monkeypatch.setenv("SINGCUP_DELETION_HOT_ENABLED", hot_)
    monkeypatch.setenv("SINGCUP_DELETION_COLD_ENABLED", cold_)
    _reset_audit_runtime()

    async def go():
        conn = await sc.get_db()
        now = int(time.time())
        await _seed(conn, sc, "ghost", "o1", now=now)
        await _seed(conn, sc, "alive1", "o1", heart=1, now=now)
        await _streamer(conn, sc, "o1", "alive1", now)

        # 힌트 — ENABLED=false면 기록되지 않아야 한다
        hinted = await audit.hint_clip("ghost", audit.HINT_CARD_EMPTY, now)
        assert hinted is hint, f"힌트 기대 {hint}"

        before = await _op_cols(conn)
        client = FakeClient({"ghost": ("deleted",)}, record=[])
        for i in range(3):                       # 2회 404를 만들 만큼 돌린다
            await audit.run_audit_cycle(
                client=client, now=now + i * (sc.DELETION_MIN_INTERVAL_SECONDS + 1))
        assert bool(client.record) is requests, f"요청 기대 {requests}"
        assert (await _op_cols(conn) != before) is state_change, \
            f"운영 상태 변경 기대 {state_change}"

    db(go())
    _reset_audit_runtime()


def test_shadow_advances_checkpoints_but_not_operational_state(env, monkeypatch):
    """Shadow가 바꿔도 되는 것과 안 되는 것을 분리해 고정한다."""
    run = env
    monkeypatch.setenv("SINGCUP_DELETION_RECONCILE_SHADOW", "true")

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "ghost", "o1", now=now)
        await _seed(db, sc, "alive1", "o1", heart=1, now=now)
        await _streamer(db, sc, "o1", "alive1", now)
        before_ops = await _op_cols(db)
        before_ck = await _audit_cols(db, "ghost")

        client = FakeClient({"ghost": ("deleted",)})
        for i in range(3):
            await audit.run_audit_cycle(
                client=client, now=now + i * (sc.DELETION_MIN_INTERVAL_SECONDS + 1))

        assert await _op_cols(db) == before_ops, "Shadow가 운영 상태를 바꿨다"
        after_ck = await _audit_cols(db, "ghost")
        assert after_ck != before_ck, "Shadow인데 체크포인트가 전혀 안 움직였다"
        assert after_ck["audit_verdict"] == audit.V_DELETED
        assert after_ck["audit_last_at"] > 0

    run(go())


# ── 합산 요청 예산 ─────────────────────────────────────────────────────────
def test_hot_and_cold_share_one_total_budget():
    """레인 버킷만 두면 합산이 상한을 넘는다 — 공용 버킷을 먼저 통과해야 한다."""
    import inspect
    src = inspect.getsource(audit.run_audit_cycle)
    i_total = src.index("_total_bucket.acquire()")
    i_lane = src.index("await bucket.acquire()")
    assert i_total < i_lane, "공용 예산을 레인 예산보다 먼저 통과해야 한다"
    assert audit.TOTAL_RATE_CAP <= audit.HOT_RATE_CAP + audit.COLD_RATE_CAP


def test_total_budget_caps_combined_rate():
    from utils.token_bucket import TokenBucket

    async def go():
        total = TokenBucket(2.0, 2.0, 0.1)
        t0 = time.monotonic()
        for _ in range(5):
            await total.acquire()
        return time.monotonic() - t0

    took = asyncio.run(go())
    # 토큰 1개로 시작하므로 5건에 최소 4/2초
    assert took >= 4 / 2.0 - 0.3


def test_hot_queue_does_not_starve_cold(env):
    """Hot이 배치를 다 채워도 Cold 슬롯이 남는다."""
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        for i in range(100):                       # Hot 후보를 배치보다 많이
            await _seed(db, sc, f"h{i:03d}", "o1", now=now)
            await audit.hint_clip(f"h{i:03d}", audit.HINT_CARD_EMPTY, now)
        for i in range(50):
            await _seed(db, sc, f"c{i:03d}", "o2", now=now)
        picked = await audit.select_targets(now, 20)
        lanes = [p["lane"] for p in picked]
        assert lanes.count("cold") >= 1, "Cold가 한 건도 못 뽑혔다"
        assert lanes.count("hot") >= 1
        assert lanes == sorted(lanes, key=lambda x: 0 if x == "hot" else 1)

    run(go())


def test_cold_takes_all_slots_when_hot_is_empty(env):
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        for i in range(30):
            await _seed(db, sc, f"c{i:03d}", "o1", now=now)
        picked = await audit.select_targets(now, 10)
        assert len(picked) == 10
        assert all(p["lane"] == "cold" for p in picked)

    run(go())


def test_no_burst_right_after_restart():
    """재시작 직후 토큰을 쌓아 두지 않는다(초기 토큰 1개)."""
    from utils.token_bucket import TokenBucket
    b = TokenBucket(1.0, 1.0, 0.1)
    assert b.tokens == 1.0


# ── 404 / 410 의미 검증 ────────────────────────────────────────────────────
class _RawClient:
    """상태코드·본문·Content-Type을 그대로 지정하는 클라이언트."""

    def __init__(self, status, body, ctype="application/json"):
        self.status, self.body, self.ctype = status, body, ctype

    async def get(self, url, params=None, headers=None, timeout=None, **kw):
        outer = self

        class R:
            status_code = outer.status
            headers = {"content-type": outer.ctype}

            def json(self):
                if isinstance(outer.body, Exception):
                    raise outer.body
                return outer.body

        return R()


def test_meaningful_chzzk_404_is_deleted(env):
    run = env

    async def go():
        v, code, why = await audit.probe_deleted(
            _RawClient(404, {"code": 404, "message": "삭제된 클립입니다."}), "abc123")
        assert (v, code, why) == (audit.V_DELETED, 404, "http_404")

    run(go())


def test_html_404_is_not_deleted(env):
    """경로 오타·프록시가 준 HTML 404를 삭제로 오인하면 안 된다."""
    run = env

    async def go():
        v, _code, why = await audit.probe_deleted(
            _RawClient(404, {}, ctype="text/html"), "abc123")
        assert v == audit.V_INCONCLUSIVE and why == "http_404_not_json"

    run(go())


def test_404_with_unexpected_body_is_not_deleted(env):
    run = env

    async def go():
        v, _c, why = await audit.probe_deleted(
            _RawClient(404, {"error": "not found"}), "abc123")
        assert v == audit.V_INCONCLUSIVE and why == "http_404_unexpected_body"
        v, _c, why = await audit.probe_deleted(
            _RawClient(404, ValueError("bad json")), "abc123")
        assert v == audit.V_INCONCLUSIVE and why == "http_404_bad_json"

    run(go())


def test_410_is_not_treated_as_deleted(env):
    """운영에서 관측된 적이 없다 — 의미 확인 전에는 확정 근거로 쓰지 않는다."""
    run = env

    async def go():
        v, code, why = await audit.probe_deleted(_RawClient(410, {"code": 410}),
                                                 "abc123")
        assert v == audit.V_INCONCLUSIVE
        assert code == 410 and why == "http_410_unverified"

        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "g1", now=now)
        for i in range(4):
            await audit.run_audit_cycle(
                client=_RawClient(410, {"code": 410}), now=now + i * 100000)
        row = await (await db.execute(
            "SELECT active, deletion_state FROM singcup_clips "
            "WHERE clip_uid='g1'")).fetchone()
        assert row["active"] == 1 and row["deletion_state"] == sc.DEL_ACTIVE
        st = await audit.audit_status()
        assert st["http_410_unverified"] >= 1

    run(go())


class _CountingConn:
    """실행된 SQL을 세는 얇은 래퍼. strict OFF에서 0인지 증명한다."""

    def __init__(self, inner):
        self._inner = inner
        self.sql: list[str] = []
        self.commits = 0

    async def execute(self, sql, params=None):
        self.sql.append(sql.strip().split()[0].upper())
        return await (self._inner.execute(sql, params) if params is not None
                      else self._inner.execute(sql))

    async def commit(self):
        self.commits += 1
        return await self._inner.commit()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_strict_off_issues_no_sql_at_all(off, monkeypatch):
    """OFF에서 SELECT/UPDATE/COMMIT이 **한 건도** 나가지 않아야 한다."""
    run = off

    async def go():
        real = await sc.get_db()
        counter = _CountingConn(real)

        async def fake_get_db():
            return counter

        monkeypatch.setattr(sc, "get_db", fake_get_db)
        monkeypatch.setattr(audit, "get_db", fake_get_db)

        assert await sc._audit_hint_siblings("o1", "x") == 0
        assert await sc._audit_hint("x", audit.HINT_REP_CHANGED) is False
        assert await sc._audit_note_frozen("x", audit.FROZEN_HINT_THRESHOLD) is False
        assert await audit.hint_clip("x", audit.HINT_MANUAL) is False
        r = await audit.run_audit_cycle(client=FakeClient({}), now=int(time.time()))
        assert r["status"] == "disabled"

        assert counter.sql == [], f"OFF인데 SQL이 나갔다: {counter.sql}"
        assert counter.commits == 0

    run(go())


def test_status_marks_itself_disabled(off):
    run = off

    async def go():
        st = await audit.audit_status()
        assert st["enabled"] is False
        assert st["status"] == "disabled"
        # 집계는 실제 DB에서 읽은 값이다 — 출처를 밝혀 오해를 막는다
        assert st["counts_source"] == "db"

    run(go())


def test_same_hint_is_not_duplicated_under_concurrency(env):
    """읽고-쓰기 사이에 끼어들어도 같은 힌트가 두 번 저장되지 않는다."""
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "a", now=now)
        results = await asyncio.gather(*[
            audit.hint_clip("a", audit.HINT_CARD_EMPTY, now) for _ in range(8)])
        # 경합으로 여러 번 써도 최종 상태는 하나이고, 값이 뒤섞이지 않는다
        row = await (await db.execute(
            "SELECT audit_hint, audit_hint_at FROM singcup_clips "
            "WHERE clip_uid='a'")).fetchone()
        assert row["audit_hint"] == audit.HINT_CARD_EMPTY
        assert int(row["audit_hint_at"]) == now
        # 그리고 이후 호출은 전부 읽기에서 걸러진다
        assert await audit.hint_clip("a", audit.HINT_CARD_EMPTY, now) is False
        assert any(results)

    run(go())


def test_owner_lock_failure_skips_without_corrupting_state(env):
    """owner 락을 못 잡으면 아무것도 바꾸지 않고, 다음 회차에 다시 뽑힌다.

    운영에서 실제로 났다(db_locked_giveup what=acquire_owner_lock). 이것이
    '짧은 실패 격리'로 끝나는지 '영구 누락'인지는 다음 두 가지에 달려 있다.
      1) 실패 경로가 missing_scan_count / deletion_last_at을 건드리지 않는다
      2) 그래서 _deletion_due / select_targets가 같은 행을 다시 고른다
    """
    run = env

    async def go():
        db = await sc.get_db()
        now = int(time.time())
        await _seed(db, sc, "gone", "o1", heart=9, now=now)
        await _seed(db, sc, "next", "o1", heart=1, now=now)
        await _streamer(db, sc, "o1", "gone", now)

        client = FakeClient({"gone": ("deleted",)})
        await audit.run_audit_cycle(client=client, now=now)      # 404 1회 → 의심
        snap = ("SELECT active, deletion_state, missing_scan_count, deletion_last_at "
                "FROM singcup_clips WHERE clip_uid='gone'")
        after_first = dict(await (await db.execute(snap)).fetchone())
        assert after_first["missing_scan_count"] == 1

        # 두 번째 404 — 그런데 owner 락 획득이 실패한다(잠금 경합 재현)
        # monkeypatch.undo()는 이 픽스처의 setenv까지 되돌리므로 쓰지 않는다.
        real_acquire = sc.acquire_owner_lock
        sc.acquire_owner_lock = lambda owner: _none()
        later = now + sc.DELETION_MIN_INTERVAL_SECONDS + 1
        await audit.run_audit_cycle(client=client, now=later)
        blocked = dict(await (await db.execute(snap)).fetchone())
        assert blocked == after_first, f"실패 경로가 상태를 바꿨다: {blocked}"
        rep = await (await db.execute(
            "SELECT representative_clip_uid FROM singcup_streamers "
            "WHERE channel_id='o1'")).fetchone()
        assert rep[0] == "gone", "대표가 부분 상태로 바뀌었다"

        # 락이 풀리면 다시 처리된다 — **영구 누락이 아니다.** 복구 경로는 둘이다.
        sc.acquire_owner_lock = real_acquire
        # (a) Change Set B의 4분 루프: audit_next_at과 무관하게 곧바로 다시 뽑는다.
        #     운영에서 실제로 이 경로가 복구했다.
        due = await sc._deletion_due(later + 1, 10)
        assert any(d["clip_uid"] == "gone" for d in due), "B 경로가 다시 뽑지 않았다"

        # (b) 감사 레인: 실패 뒤 최소 간격만큼 미뤄 두므로 그 뒤에 다시 뽑힌다.
        assert not any(p["clip_uid"] == "gone"
                       for p in await audit.select_targets(later + 1, 10))
        retry_at = later + sc.DELETION_MIN_INTERVAL_SECONDS + 1
        assert any(p["clip_uid"] == "gone"
                   for p in await audit.select_targets(retry_at, 10))

        await audit.run_audit_cycle(client=client, now=retry_at)
        final = dict(await (await db.execute(snap)).fetchone())
        assert final["deletion_state"] == sc.DEL_CONFIRMED
        assert final["active"] == 0

    run(go())


async def _none():
    return None
