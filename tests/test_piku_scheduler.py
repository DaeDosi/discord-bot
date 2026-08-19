"""AUTO-2 — challenge 속도 제한과 자동 수집 **회차 기록** 계약.

AUTO-1이 남긴 잔여 위험 하나가 "challenge 발급에 속도 제한이 없다"였다. 장치가
0대일 때는 아무것도 나오지 않지만, 장치가 등록된 뒤에는 지문을 아는 사람이
challenge 행을 무한히 늘릴 수 있다(토큰은 못 받는다). 여기서 그것을 막는다.

지키는 것:
  · 제한은 **challenge를 만들기 전에** 걸린다 — 막힌 요청은 DB 행을 남기지 않는다.
  · 제한 상태는 **DB에 있다** — 서비스 재시작만으로 초기화되지 않는다.
  · 정상 1시간 수집(부문 3개)과 사람이 누르는 재시도를 막지 않는다.
  · 제한 응답에 nonce·토큰·내부 상태가 실리지 않는다.

그리고 자동 수집 **회차**를 기록한다. AUTO-2에는 Publish가 없으므로 성공한 draft는
남길 수 있지만, 한 부문이라도 실패하면 그 회차를 "세 부문 완료"로 표시하면 안 된다.
전체 성공 / 부분 성공 / 실패를 구분한다.

**실제 PIKU를 호출하지 않는다.**
"""
import asyncio
import base64
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils  # noqa: E402

devices = pytest.importorskip("singcup_piku_devices",
                              reason="AUTO-1 장치 모듈이 없다")
sched = pytest.importorskip("singcup_piku_scheduler",
                            reason="AUTO-2 스케줄러 모듈이 아직 없다")


class FakeDevice:
    def __init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_b64(self) -> str:
        return base64.b64encode(self._key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)).decode()

    def sign(self, message: str) -> str:
        der = self._key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = asym_utils.decode_dss_signature(der)
        return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


_LOOP = None
_ORIG_DB_PATH = None


def run(coro):
    return _LOOP.run_until_complete(coro)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    global _LOOP, _ORIG_DB_PATH
    from database import db as dbmod
    _ORIG_DB_PATH = dbmod.DB_PATH
    db_file = tmp_path / f"auto2-{uuid.uuid4().hex}.db"
    _LOOP = asyncio.new_event_loop()

    async def setup():
        import database
        from database import db as dbmod
        if dbmod._db is not None:
            await dbmod.close_db()
        dbmod.DB_PATH = str(db_file)
        dbmod._db = None
        await database.init_db()

    _LOOP.run_until_complete(setup())
    yield

    async def teardown():
        from database import db as dbmod
        await dbmod.close_db()
        dbmod.DB_PATH = _ORIG_DB_PATH
        dbmod._db = None

    _LOOP.run_until_complete(teardown())
    _LOOP.close()
    _LOOP = None


async def _active_device(name: str = "거실 PC"):
    started = await devices.register_start(name)
    dev = FakeDevice()
    done = await devices.register_finish(started["pairingCode"], dev.public_b64)
    return done["deviceId"], dev


async def _n_challenges(device_id: int, n: int, ip: str = "1.2.3.4"):
    """rate limit을 거쳐 challenge를 n번 요청한다. 성공 수를 돌려준다."""
    ok = 0
    for _ in range(n):
        try:
            await sched.guarded_challenge(device_id, "groups", ip=ip)
            ok += 1
        except devices.DeviceError:
            pass
    return ok


# ── 1) 속도 제한 ────────────────────────────────────────────────────────────
def test_normal_hourly_collection_is_not_throttled():
    """정상 1시간 수집은 부문 3개 = challenge 3회. 절대 막히면 안 된다."""
    async def go():
        dev_id, _ = await _active_device()
        for d in ("female_solo", "male_solo", "groups"):
            c = await sched.guarded_challenge(dev_id, d, ip="1.2.3.4")
            assert c["division"] == d
    run(go())


def test_manual_retries_are_not_throttled():
    """사람이 몇 번 다시 눌러도 막히지 않는다(burst 한도 안)."""
    async def go():
        dev_id, _ = await _active_device()
        assert await _n_challenges(dev_id, sched.BURST_LIMIT) == sched.BURST_LIMIT
    run(go())


def test_burst_beyond_limit_is_rejected():
    async def go():
        dev_id, _ = await _active_device()
        await _n_challenges(dev_id, sched.BURST_LIMIT)
        with pytest.raises(devices.DeviceError) as e:
            await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4")
        assert e.value.code == "rate_limited"
    run(go())


def test_throttled_request_creates_no_challenge_row():
    """**막힌 요청은 challenge 행을 만들지 않는다.** 이게 이 기능의 핵심이다."""
    async def go():
        dev_id, _ = await _active_device()
        await _n_challenges(dev_id, sched.BURST_LIMIT)
        from database import get_db
        conn = await get_db()
        before = (await (await conn.execute(
            "SELECT count(*) FROM piku_collector_challenges")).fetchone())[0]
        for _ in range(5):
            with pytest.raises(devices.DeviceError):
                await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4")
        after = (await (await conn.execute(
            "SELECT count(*) FROM piku_collector_challenges")).fetchone())[0]
        assert after == before, "제한된 요청이 challenge 행을 만들었다"
    run(go())


def test_long_window_limit_applies_beyond_burst():
    """burst가 지나가도 장기 window가 남아 있다."""
    async def go():
        dev_id, _ = await _active_device()
        # burst 창을 넘겨 가며 window 한도까지 채운다.
        for i in range(sched.WINDOW_LIMIT):
            await sched._shift_attempts_for_tests(dev_id, seconds=sched.BURST_SECONDS + 1)
            await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4")
        await sched._shift_attempts_for_tests(dev_id, seconds=sched.BURST_SECONDS + 1)
        with pytest.raises(devices.DeviceError) as e:
            await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4")
        assert e.value.code == "rate_limited"
    run(go())


def test_limit_is_per_device_not_global():
    """한 장치가 막혀도 다른 장치는 정상이어야 한다."""
    async def go():
        a, _ = await _active_device("A")
        b, _ = await _active_device("B")
        await _n_challenges(a, sched.BURST_LIMIT + 3, ip="1.1.1.1")
        with pytest.raises(devices.DeviceError):
            await sched.guarded_challenge(a, "groups", ip="1.1.1.1")
        c = await sched.guarded_challenge(b, "groups", ip="2.2.2.2")
        assert c["challengeId"]
    run(go())


def test_ip_limit_is_a_secondary_guard():
    """장치를 바꿔 가며 두드려도 같은 IP면 결국 막힌다.

    장치 한도(burst 8 / window 40)가 먼저 걸리면 IP 한도를 시험하지 못한다.
    그래서 **장치를 넉넉히** 만들어 device별 한도 안에서만 두드린다 —
    그러면 총합을 막는 것은 IP 한도뿐이다.
    """
    async def go():
        n_dev = (sched.IP_LIMIT // sched.BURST_LIMIT) + 5
        ids = []
        for i in range(n_dev):
            d, _ = await _active_device(f"PC{i}")
            ids.append(d)
        ok = 0
        for d in ids:
            for _ in range(sched.BURST_LIMIT):     # 장치별 한도 안
                try:
                    await sched.guarded_challenge(d, "groups", ip="9.9.9.9")
                    ok += 1
                except devices.DeviceError as e:
                    assert e.code == "rate_limited"
        assert ok > 0
        assert ok <= sched.IP_LIMIT, f"IP 한도를 넘겼다: {ok}"
        # 다른 IP는 여전히 통과해야 한다 — IP 한도가 전역 차단이 되면 안 된다.
        fresh, _ = await _active_device("다른망")
        c = await sched.guarded_challenge(fresh, "groups", ip="10.0.0.1")
        assert c["challengeId"]
    run(go())


def test_rate_limit_survives_process_restart():
    """제한 상태가 메모리가 아니라 DB에 있다."""
    async def go():
        dev_id, _ = await _active_device()
        await _n_challenges(dev_id, sched.BURST_LIMIT)
        # 모듈 상태를 통째로 날려도(재시작 흉내) 제한이 유지돼야 한다.
        sched._reset_process_state_for_tests()
        with pytest.raises(devices.DeviceError) as e:
            await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4")
        assert e.value.code == "rate_limited"
    run(go())


def test_rate_limit_message_leaks_nothing():
    async def go():
        dev_id, _ = await _active_device()
        await _n_challenges(dev_id, sched.BURST_LIMIT)
        with pytest.raises(devices.DeviceError) as e:
            await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4")
        blob = f"{e.value.code} {e.value.message}"
        for bad in ("nonce", "token", "signature", "1.2.3.4", "challengeId"):
            assert bad not in blob, f"{bad}가 제한 응답에 실렸다"
    run(go())


def test_revoked_and_pending_devices_are_blocked_before_rate_limit():
    """폐기·대기 장치는 제한 이전에 막힌다(제한 카운터를 소모하지 않는다)."""
    async def go():
        pending = await devices.register_start("PC")
        with pytest.raises(devices.DeviceError) as e:
            await sched.guarded_challenge(pending["deviceId"], "groups", ip="1.2.3.4")
        assert e.value.code != "rate_limited"
        dev_id, _ = await _active_device("B")
        await devices.revoke(dev_id)
        with pytest.raises(devices.DeviceError) as e:
            await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4")
        assert e.value.code != "rate_limited"
    run(go())


# ── 2) 모드 게이트 ──────────────────────────────────────────────────────────
def test_manual_mode_blocks_automation_challenges():
    async def go():
        dev_id, _ = await _active_device()
        await devices.set_mode("MANUAL")
        with pytest.raises(devices.DeviceError) as e:
            await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4",
                                          automation=True)
        assert e.value.code == "automation_off"
    run(go())


def test_auto_collect_mode_allows_automation():
    async def go():
        dev_id, _ = await _active_device()
        await devices.set_mode("AUTO_COLLECT")
        c = await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4",
                                          automation=True)
        assert c["challengeId"]
    run(go())


def test_auto_publish_is_not_available_in_this_stage():
    """AUTO_PUBLISH는 값으로는 존재해도 **준비되지 않음**으로 막힌다."""
    async def go():
        assert sched.AUTO_PUBLISH_READY is False
        assert await sched.publish_allowed() is False
        await devices.set_mode("AUTO_PUBLISH")
        # 모드가 그 값이어도 자동 공개는 여전히 허용되지 않는다.
        assert await sched.publish_allowed() is False
        # 수집은 허용된다(AUTO_PUBLISH ⊃ AUTO_COLLECT).
        dev_id, _ = await _active_device()
        c = await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4",
                                          automation=True)
        assert c["challengeId"]
    run(go())


# ── 3) 회차 기록 ────────────────────────────────────────────────────────────
def test_run_records_full_success():
    async def go():
        dev_id, _ = await _active_device()
        rid = await sched.run_start(dev_id, trigger="alarm")
        for d, n in (("female_solo", 64), ("male_solo", 64), ("groups", 32)):
            await sched.run_division(rid, d, ok=True, rows=n)
        out = await sched.run_finish(rid)
        assert out["outcome"] == "success"
        assert out["divisions"]["groups"]["rows"] == 32
    run(go())


def test_one_division_failure_is_partial_not_success():
    """**한 부문이 실패하면 그 회차는 '세 부문 완료'가 아니다.**"""
    async def go():
        dev_id, _ = await _active_device()
        rid = await sched.run_start(dev_id, trigger="alarm")
        await sched.run_division(rid, "female_solo", ok=True, rows=64)
        await sched.run_division(rid, "male_solo", ok=True, rows=64)
        await sched.run_division(rid, "groups", ok=False, kind="not_rendered")
        out = await sched.run_finish(rid)
        assert out["outcome"] == "partial"
        assert out["divisions"]["groups"]["kind"] == "not_rendered"
        # 성공한 draft는 보존된다(AUTO-2에는 Publish가 없다).
        assert out["divisions"]["female_solo"]["ok"] is True
    run(go())


def test_all_divisions_failing_is_failed():
    async def go():
        dev_id, _ = await _active_device()
        rid = await sched.run_start(dev_id, trigger="alarm")
        for d in ("female_solo", "male_solo", "groups"):
            await sched.run_division(rid, d, ok=False, kind="no_tab")
        out = await sched.run_finish(rid)
        assert out["outcome"] == "failed"
    run(go())


def test_run_history_is_listed_newest_first():
    async def go():
        dev_id, _ = await _active_device()
        for _ in range(3):
            rid = await sched.run_start(dev_id, trigger="manual")
            await sched.run_finish(rid)
        hist = await sched.recent_runs(limit=10)
        assert len(hist) == 3
        assert hist[0]["id"] > hist[-1]["id"]
        # 회차 기록에도 secret이 없다.
        assert "token" not in repr(hist)
    run(go())


def test_status_reports_mode_device_and_publish_readiness():
    async def go():
        dev_id, _ = await _active_device()
        st = await sched.status()
        assert st["mode"] == "MANUAL"
        assert st["autoPublishReady"] is False
        assert st["activeDeviceCount"] == 1
        assert "token" not in repr(st) and "pairingCode" not in repr(st)
    run(go())


# ── 4) 가벼운 상태 조회 (확장이 회차 전에 부른다) ──────────────────────────
def test_device_state_does_not_create_a_challenge():
    """**상태 조회가 challenge를 만들면 안 된다.**

    예전 설계는 challenge를 하나 발급해 보고 장치 상태를 짐작했다. 그러면 시간당
    발급이 3회가 아니라 4회가 되고 속도 제한 계산이 흐려진다.
    """
    async def go():
        dev_id, _ = await _active_device()
        from database import get_db
        conn = await get_db()
        before = (await (await conn.execute(
            "SELECT count(*) FROM piku_collector_challenges")).fetchone())[0]
        devs = await devices.list_devices()
        fp = devs[0]["fingerprint"]
        st = await sched.device_state(fp)
        assert st["deviceActive"] is True and st["mode"] == "MANUAL"
        assert st["autoPublishReady"] is False
        after = (await (await conn.execute(
            "SELECT count(*) FROM piku_collector_challenges")).fetchone())[0]
        assert after == before, "상태 조회가 challenge를 만들었다"
        # 속도 제한 카운터도 소모하지 않는다.
        att = (await (await conn.execute(
            "SELECT count(*) FROM piku_challenge_attempts")).fetchone())[0]
        assert att == 0, "상태 조회가 제한 카운터를 태웠다"
    run(go())


def test_device_state_reports_unknown_and_revoked_without_leaking():
    async def go():
        st = await sched.device_state("0000-0000-0000-0000")
        assert st["deviceActive"] is False and st["deviceStatus"] == "none"
        dev_id, _ = await _active_device()
        fp = (await devices.list_devices())[0]["fingerprint"]
        await devices.revoke(dev_id)
        st = await sched.device_state(fp)
        assert st["deviceActive"] is False and st["deviceStatus"] == "revoked"
        assert "publicKey" not in repr(st) and "token" not in repr(st)
    run(go())



# ── 5) guarded_challenge 고유 계약 (AUTO-1 검사와 겹치지 않게) ──────────────
#
# `devices.challenge_issue`도 모드·장치 상태를 검사한다(방어 두 겹). 그래서 바깥
# 겹만 지우면 challenge는 여전히 막힌다 — 변이 검사에서 실제로 놓쳤다.
# 여기서는 **`guarded_challenge`만 지는 책임**을 직접 본다: 거절해야 하는 요청이
# 속도 제한 카운터를 태우면 안 된다. 그러지 않으면 남의 지문으로 정상 장치의
# 한도를 소모시킬 수 있다.
async def _attempt_count() -> int:
    from database import get_db
    conn = await get_db()
    return (await (await conn.execute(
        "SELECT count(*) FROM piku_challenge_attempts")).fetchone())[0]


def test_manual_mode_rejection_does_not_burn_rate_limit():
    async def go():
        dev_id, _ = await _active_device()
        await devices.set_mode("MANUAL")
        before = await _attempt_count()
        for _ in range(5):
            with pytest.raises(devices.DeviceError) as e:
                await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4",
                                              automation=True)
            assert e.value.code == "automation_off"
        assert await _attempt_count() == before, "MANUAL 거절이 카운터를 태웠다"
        # 카운터가 멀쩡하므로 모드를 켜면 곧바로 정상 동작한다.
        await devices.set_mode("AUTO_COLLECT")
        assert (await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4",
                                              automation=True))["challengeId"]
    run(go())


def test_inactive_device_rejection_does_not_burn_rate_limit():
    async def go():
        pending = await devices.register_start("대기 PC")
        revoked_id, _ = await _active_device("폐기 PC")
        await devices.revoke(revoked_id)
        before = await _attempt_count()
        for dev_id, want in ((pending["deviceId"], "device_not_active"),
                             (revoked_id, "device_not_active"),
                             (999999, "no_device")):
            for _ in range(4):
                with pytest.raises(devices.DeviceError) as e:
                    await sched.guarded_challenge(dev_id, "groups", ip="1.2.3.4")
                assert e.value.code == want
        assert await _attempt_count() == before, "비활성 거절이 카운터를 태웠다"
    run(go())


def test_guarded_challenge_checks_division_before_anything_else():
    """부문이 틀리면 장치 조회도 카운터 소모도 없이 즉시 거절한다."""
    async def go():
        dev_id, _ = await _active_device()
        before = await _attempt_count()
        with pytest.raises(devices.DeviceError) as e:
            await sched.guarded_challenge(dev_id, "mixed_doubles", ip="1.2.3.4")
        assert e.value.code == "bad_division"
        assert await _attempt_count() == before
    run(go())


# ── 6) IP 식별 계약 — **공통 모듈 하나만 쓴다** ────────────────────────────
#
# AUTO-2는 처음에 자기 `client_key()`를 만들었는데, 저장소에는 이미
# `web/backend/client_ip.py`가 같은 문제를 풀어 두고 있었다(신뢰 홉 수를 IP 대역이
# 아니라 **홉 수**로 못 박고, 원문 대신 날짜 회전 해시를 돌려준다). 중복 구현은
# 두 곳이 갈라져 어느 쪽이 진실인지 알 수 없게 만든다 — 그래서 폐기하고 재사용한다.
#
# 여기서 고정하는 것은 둘이다:
#   1. AUTO-2 경로가 **공통 모듈을 실제로 부른다**(자기 helper로 되돌아가지 않는다).
#   2. 그 모듈이 요구 계약을 만족한다(신뢰 홉 기준·위조 저항·fallback·IPv6).
import client_ip  # noqa: E402


class FakeReq:
    def __init__(self, peer: str, xff: str | None = None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}


def _resolve_with_hops(req, hops: int):
    """`TRUSTED_PROXY_HOPS`는 모듈 로드 시각에 읽힌다 — 값을 바꿔 끼워 확인한다."""
    orig = client_ip.TRUSTED_PROXY_HOPS
    client_ip.TRUSTED_PROXY_HOPS = hops
    try:
        return client_ip.resolve(req)
    finally:
        client_ip.TRUSTED_PROXY_HOPS = orig


def test_auto2_route_uses_the_shared_client_ip_module():
    """**AUTO-2가 자기 IP 파서를 다시 만들면 실패한다.**"""
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[1] / "web" / "backend"
    router = (root / "routers" / "admin_router.py").read_text("utf-8")
    i = router.index("async def piku_device_challenge")
    block = router[i:router.index("@router.post", i + 10)]
    assert "client_ip.resolve(request)" in block,         "challenge 경로가 공통 모듈을 쓰지 않는다"
    assert "x-forwarded-for" not in block.lower(), "라우터가 XFF를 직접 읽는다"
    assert "request.client.host" not in block, "라우터가 peer를 직접 읽는다"
    sched_src = (root / "singcup_piku_scheduler.py").read_text("utf-8")
    for banned in ("def client_key", "TRUSTED_PROXY_HOPS =", "x-forwarded-for"):
        assert banned not in sched_src, f"스케줄러가 IP 판정을 다시 만든다: {banned}"


def test_shared_module_default_is_zero_hops():
    """저장소 기존 계약 — 기본값은 **0**(레거시)이고 AUTO-2가 바꾸지 않는다."""
    import os
    assert client_ip.TRUSTED_PROXY_HOPS == max(0, int(os.getenv("TRUSTED_PROXY_HOPS", "0")))
    src = (Path(__file__).resolve().parents[1] / "web" / "backend"
           / "client_ip.py").read_text("utf-8")
    assert 'os.getenv("TRUSTED_PROXY_HOPS", "0")' in src, "기본값이 0이 아니다"


def test_trusted_hop_is_selected_from_the_right():
    """신뢰 홉이 1이면 **오른쪽에서 1번째**가 진짜 클라이언트다."""
    real = "203.0.113.7"
    got = _resolve_with_hops(FakeReq("10.0.0.1", f"attacker, {real}"), 1)
    assert got["id"] == client_ip.hash_ip(real)
    assert got["source"] == "xff_hop"


def test_forged_left_entries_cannot_change_the_key_when_hops_configured():
    """공격자가 XFF **왼쪽**에 무엇을 넣어도 키가 바뀌지 않는다(홉 설정 시)."""
    real = "203.0.113.7"
    base = _resolve_with_hops(FakeReq("10.0.0.1", real), 1)["id"]
    for forged in ("1.1.1.1", "evil, 2.2.2.2", "::1, 9.9.9.9, 8.8.8.8", "  , ,"):
        got = _resolve_with_hops(FakeReq("10.0.0.1", f"{forged}, {real}"), 1)["id"]
        assert got == base, f"위조 XFF({forged})로 키가 바뀌었다"


def test_zero_hops_is_legacy_and_forgeable_by_design():
    """**기본값 0은 위조 가능하다** — 이것이 저장소가 의도한 트레이드오프다.

    홉 수를 잘못 잡으면 여러 사용자가 한 버킷을 공유해 정상 사용자가 막히므로,
    공통 모듈이 안전한 쪽(판정을 바꾸지 않는 쪽)을 기본값으로 골랐다.
    Railway처럼 엣지가 하나면 **환경변수로 1을 지정해야** 위조가 막힌다.
    이 사실을 테스트로 드러내 둔다 — 모르고 배포하면 IP 한도가 무력해진다.
    """
    a = _resolve_with_hops(FakeReq("10.0.0.1", "1.1.1.1, 203.0.113.7"), 0)
    b = _resolve_with_hops(FakeReq("10.0.0.1", "2.2.2.2, 203.0.113.7"), 0)
    assert a["source"] == "xff_first"
    assert a["id"] != b["id"], "0홉이 레거시(맨 앞) 동작이 아니다"


def test_falls_back_to_peer_without_xff():
    got = client_ip.resolve(FakeReq("198.51.100.9"))
    assert got["id"] == client_ip.hash_ip("198.51.100.9") and got["source"] == "peer"
    got = client_ip.resolve(FakeReq("198.51.100.9", ""))
    assert got["source"] == "peer", "빈 XFF를 신뢰했다"


def test_hop_count_is_actually_applied_not_just_the_last_entry():
    """홉 수가 **실제로 적용**되는지 본다.

    홉이 1일 때는 `parts[-1]`과 `parts[-hops]`가 같아서 "항상 마지막"으로 바꿔도
    티가 나지 않는다(변이 검사에서 실제로 놓쳤다). 프록시가 둘인 구성을 만들어
    **오른쪽에서 두 번째**를 고르는지 확인한다.
    """
    chain = "attacker, 203.0.113.7, 10.1.1.1"   # 클라이언트, 엣지, 내부 프록시
    got = _resolve_with_hops(FakeReq("10.0.0.1", chain), 2)
    assert got["id"] == client_ip.hash_ip("203.0.113.7"), "홉 수를 무시했다"
    assert got["id"] != client_ip.hash_ip("10.1.1.1")
    # 홉이 3이면 맨 앞(공격자 입력)까지 신뢰하게 된다 — 설정이 곧 신뢰 범위다.
    got3 = _resolve_with_hops(FakeReq("10.0.0.1", chain), 3)
    assert got3["id"] == client_ip.hash_ip("attacker")


def test_short_chain_does_not_silently_trust_a_missing_hop():
    """홉이 기대보다 적으면 그 사실을 `source`로 드러낸다(조용히 넘어가지 않는다)."""
    got = _resolve_with_hops(FakeReq("10.0.0.1", "1.1.1.1"), 2)
    assert got["source"] == "xff_short", "홉 부족을 구분하지 않는다"
    assert got["xffHops"] == 1


def test_ipv6_and_whitespace_and_empty_entries():
    v6 = "2001:db8::1"
    got = _resolve_with_hops(FakeReq("10.0.0.1", f"  attacker ,  , {v6}  "), 1)
    assert got["id"] == client_ip.hash_ip(v6), "IPv6·공백·빈 항목 처리가 잘못됐다"
    assert client_ip.resolve(FakeReq(v6))["id"] == client_ip.hash_ip(v6)


def test_distinct_clients_behind_one_proxy_do_not_share_a_bucket():
    a = _resolve_with_hops(FakeReq("10.0.0.1", "203.0.113.1"), 1)["id"]
    b = _resolve_with_hops(FakeReq("10.0.0.1", "203.0.113.2"), 1)["id"]
    assert a != b, "프록시 뒤 클라이언트가 한 버킷으로 합쳐진다"


def test_resolve_never_returns_the_raw_ip():
    """공통 모듈은 **원문 IP를 돌려주지 않는다** — 우리 저장소에 원문이 못 들어온다."""
    got = _resolve_with_hops(FakeReq("10.0.0.1", "attacker, 203.0.113.7"), 1)
    assert "203.0.113.7" not in repr(got) and "10.0.0.1" not in repr(got)


def test_ip_is_stored_hashed_not_in_plaintext():
    async def go():
        dev_id, _ = await _active_device()
        await sched.guarded_challenge(dev_id, "groups", ip="203.0.113.55")
        from database import get_db
        conn = await get_db()
        cur = await conn.execute("SELECT ip_hash FROM piku_challenge_attempts")
        rows = [r[0] for r in await cur.fetchall()]
        assert rows and all("203.0.113.55" not in r for r in rows), "IP가 평문으로 남았다"
        assert all(len(r) == 32 for r in rows)
    run(go())
