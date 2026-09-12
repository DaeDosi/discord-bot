"""SINGCUP-FINAL-1b — MANUAL 테스트 수집 허가(방식 B)와 회차 멱등성.

운영 사건(2026-09-12 20:18~20:19): MANUAL 모드에서 확장의 "지금 테스트 수집"이
challenge 단계에서 `automation_off`로 거절됐고(확장은 alarm 경로와 같은
`automation: true`를 보냈다), 팝업은 그것을 `수집 토큰을 받지 못함` 하나로 뭉갰다.
또 같은 클릭 흐름에서 run 행이 두 건 생겼다.

여기서 고정하는 계약:
  · MANUAL에서는 **자동도 수동도** challenge를 못 받는다.
  · 단, 운영자가 Nexadmin에서 발급한 **장치·단계에 묶인 1회용 허가**가 있으면 한 번 통과.
  · AUTO_COLLECT에서는 허가 없이 동작(기존 그대로).
  · 한 invocation = 서버 run 1건.
"""
# ruff: noqa: E501 — 공격·시나리오 표는 한 줄이 길어야 읽힌다(신규 테스트 파일).
import asyncio
import base64
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

import singcup_piku_devices as devices  # noqa: E402
import singcup_piku_scheduler as sched  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils  # noqa: E402

_ORIG_DB_PATH = None


@pytest.fixture
def env(tmp_path):
    global _ORIG_DB_PATH
    from database import db as dbmod
    _ORIG_DB_PATH = dbmod.DB_PATH
    db_file = tmp_path / f"grant-{uuid.uuid4().hex}.db"
    loop = asyncio.new_event_loop()

    async def setup():
        import database
        from database import db as dbmod
        if dbmod._db is not None:
            await dbmod.close_db()
        dbmod.DB_PATH = str(db_file)
        dbmod._db = None
        await database.init_db()

    loop.run_until_complete(setup())
    yield loop

    async def teardown():
        from database import db as dbmod
        await dbmod.close_db()
        dbmod.DB_PATH = _ORIG_DB_PATH
        dbmod._db = None

    loop.run_until_complete(teardown())
    loop.close()


class FakeDevice:
    def __init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_b64(self) -> str:
        spki = self._key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(spki).decode()

    def sign(self, message: str) -> str:
        der = self._key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = asym_utils.decode_dss_signature(der)
        return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


async def _register(name="노트북"):
    started = await devices.register_start(name)
    dev = FakeDevice()
    done = await devices.register_finish(started["pairingCode"], dev.public_b64)
    return done["deviceId"], dev, done["fingerprint"]


# ── 1) 운영 사건 재현 ───────────────────────────────────────────────────────
def test_reproduces_production_failure_automation_true_in_manual(env):
    """확장이 alarm과 같은 `automation=True`를 보내면 MANUAL에서 `automation_off`."""
    async def go():
        device_id, _, fp = await _register()
        assert await devices.get_mode() == "MANUAL"       # 행이 없으면 MANUAL
        with pytest.raises(devices.DeviceError) as ei:
            await sched.guarded_challenge(device_id, "final", automation=True, protocol=2)
        assert ei.value.code == "automation_off"
        from database import get_db
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_collector_challenges")
        assert (await cur.fetchone())[0] == 0, "거절인데 challenge 행이 생겼다"
    env.run_until_complete(go())


# ── 2) 방식 B: 테스트 허가 ──────────────────────────────────────────────────
def test_manual_test_requires_owner_issued_grant(env):
    async def go():
        device_id, dev, _ = await _register()
        # 허가 없는 수동 → 명확한 코드로 거절(토큰 실패로 뭉개지 않는다).
        with pytest.raises(devices.DeviceError) as ei:
            await sched.guarded_challenge(device_id, "final", automation=False, protocol=2)
        assert ei.value.code == "test_grant_required"
        g = await devices.test_grant_issue(device_id, "final")
        assert 6 <= len(g["grant"]) <= 12 and not set(g["grant"]) & set("01OIl")
        assert g["campaign"] == "final" and g["ttlSeconds"] >= 60
        c = await sched.guarded_challenge(device_id, "final", automation=False, protocol=2,
                                          test_grant=g["grant"])
        t = await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        assert t["campaign"] == "final"
        # 허가는 1회용이다(이미 썼다고 분명히 말해 준다 → 재발급하면 된다).
        with pytest.raises(devices.DeviceError) as ei:
            await sched.guarded_challenge(device_id, "final", automation=False, protocol=2,
                                          test_grant=g["grant"])
        assert ei.value.code == "test_grant_used"
    env.run_until_complete(go())


def test_grant_is_bound_to_device_and_campaign_and_expires(env):
    async def go():
        dev_a, _, _ = await _register("노트북")
        dev_b, _, _ = await _register("다른 PC")
        g = await devices.test_grant_issue(dev_a, "final")
        # 다른 장치는 못 쓴다. 해시에 장치가 묶여 있어 **없는 코드**로 보인다
        # (다른 장치에 허가가 있는지 알려 주지 않는다).
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(dev_b, "final", automation=False, protocol=2,
                                          test_grant=g["grant"])
        assert ei.value.code == "test_grant_invalid"
        # 대소문자·공백은 정규화되지만 다른 코드는 통하지 않는다.
        ok = await devices.challenge_issue(dev_a, "final", automation=False, protocol=2,
                                           test_grant=f"  {g['grant'].lower()} ")
        assert ok["challengeId"]
        g2 = await devices.test_grant_issue(dev_a, "final")
        for bad in (None, "", "   ", "ZZZZZZZZ", g2["grant"][:-1] + "X"):
            with pytest.raises(devices.DeviceError):
                await devices.challenge_issue(dev_a, "final", automation=False, protocol=2,
                                              test_grant=bad)
        # 만료
        from database import get_db
        db = await get_db()
        await db.execute("UPDATE piku_test_grants SET expires_at=? WHERE used_at=0",
                         (int(time.time()) - 1,))
        await db.commit()
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(dev_a, "final", automation=False, protocol=2,
                                          test_grant=g2["grant"])
        assert ei.value.code == "test_grant_expired"
    env.run_until_complete(go())


def test_grant_issue_requires_active_device_and_live_campaign(env):
    async def go():
        device_id, _, _ = await _register()
        with pytest.raises(devices.DeviceError) as ei:
            await devices.test_grant_issue(device_id, "qualifier")
        assert ei.value.code == "campaign_frozen"
        with pytest.raises(devices.DeviceError) as ei:
            await devices.test_grant_issue(device_id, "nope")
        assert ei.value.code == "bad_campaign"
        await devices.revoke(device_id)
        with pytest.raises(devices.DeviceError) as ei:
            await devices.test_grant_issue(device_id, "final")
        assert ei.value.code == "device_not_active"
    env.run_until_complete(go())


def test_revoke_burns_outstanding_grants(env):
    async def go():
        device_id, _, _ = await _register()
        g = await devices.test_grant_issue(device_id, "final")
        await devices.revoke(device_id)
        from database import get_db
        cur = await (await get_db()).execute("SELECT used_at FROM piku_test_grants")
        assert (await cur.fetchone())[0] > 0, "폐기했는데 허가가 남아 있다"
        assert g["grant"]
    env.run_until_complete(go())


def test_grant_is_stored_hashed_only(env):
    async def go():
        device_id, _, _ = await _register()
        g = await devices.test_grant_issue(device_id, "final")
        from database import get_db
        cur = await (await get_db()).execute("SELECT * FROM piku_test_grants")
        blob = repr([tuple(r) for r in await cur.fetchall()])
        assert g["grant"] not in blob
        assert devices._hash_grant(g["grant"], device_id, "final") in blob
        # 해시 자체가 장치·단계에 묶인다(컬럼 비교에 더한 두 번째 겹).
        assert devices._hash_grant(g["grant"], device_id + 1, "final") !=             devices._hash_grant(g["grant"], device_id, "final")
        assert devices._hash_grant(g["grant"], device_id, "qualifier") !=             devices._hash_grant(g["grant"], device_id, "final")
        assert devices._hash_grant("OTHER123", device_id, "final") !=             devices._hash_grant(g["grant"], device_id, "final")
    env.run_until_complete(go())


def test_auto_collect_needs_no_grant(env):
    async def go():
        device_id, _, _ = await _register()
        await devices.set_mode("AUTO_COLLECT")
        c1 = await sched.guarded_challenge(device_id, "final", automation=True, protocol=2)
        c2 = await sched.guarded_challenge(device_id, "final", automation=False, protocol=2)
        assert c1["challengeId"] and c2["challengeId"]
    env.run_until_complete(go())


def test_manual_mode_never_runs_on_alarm_even_with_a_grant(env):
    """허가는 **사람이 누른 실행**만 연다. alarm(automation=True)은 여전히 0건."""
    async def go():
        device_id, _, _ = await _register()
        g = await devices.test_grant_issue(device_id, "final")
        with pytest.raises(devices.DeviceError) as ei:
            await sched.guarded_challenge(device_id, "final", automation=True, protocol=2,
                                          test_grant=g["grant"])
        assert ei.value.code == "automation_off"
        # 거절이 허가를 태우지 않았다 — 사람이 누르면 그대로 쓸 수 있다.
        c = await sched.guarded_challenge(device_id, "final", automation=False, protocol=2,
                                          test_grant=g["grant"])
        assert c["challengeId"]
    env.run_until_complete(go())


# ── 3) 회차 멱등성 ─────────────────────────────────────────────────────────
def test_same_invocation_creates_one_run(env):
    async def go():
        device_id, _, fp = await _register()
        body = {"trigger": "manual", "campaign": "final", "invocationId": "manual-1757-abcd1234",
                "sources": {"final": {"ok": False, "kind": "test_grant_required", "rows": 0}}}
        a = await sched.report_run(fp, body)
        b = await sched.report_run(fp, body)          # 보고 재시도
        # **끝난 회차는 다시 쓰이지 않는다.** 재전송이 실패를 성공으로 바꾸지 못한다
        # (반대 방향도 마찬가지) — run 행 재사용만으로는 source upsert가 덮어썼다.
        c = await sched.report_run(fp, dict(body, sources={"final": {"ok": True, "kind": "sent", "rows": 32}}))
        assert a["id"] == b["id"] == c["id"]
        assert a["outcome"] == b["outcome"] == c["outcome"] == "failed"
        assert c["sources"]["final"]["kind"] == "test_grant_required", "재보고가 결과를 덮어썼다"
        assert c["sources"]["final"]["rows"] == 0
        from database import get_db
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_auto_runs")
        assert (await cur.fetchone())[0] == 1
        # 다른 invocation은 새 회차다.
        d = await sched.report_run(fp, dict(body, invocationId="manual-1757-zzzz9999"))
        assert d["id"] != a["id"]
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_auto_runs")
        assert (await cur.fetchone())[0] == 2
    env.run_until_complete(go())


def test_invocation_id_is_optional_and_validated(env):
    async def go():
        device_id, _, fp = await _register()
        base = {"trigger": "manual", "campaign": "final",
                "sources": {"final": {"ok": True, "kind": "sent", "rows": 32}}}
        # 없거나 형식이 틀리면 멱등 대상이 아니다(구 확장 호환) — 매번 새 회차.
        for inv in (None, "", "짧음", "bad id!", "x" * 200):
            body = dict(base) if inv is None else dict(base, invocationId=inv)
            await sched.report_run(fp, body)
        from database import get_db
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_auto_runs")
        assert (await cur.fetchone())[0] == 5
        # 200자는 라우터 단에서 64자로 잘려 들어오므로 여기서는 형식 검사만 본다:
        # 8~64자 [A-Za-z0-9_-]만 멱등 키가 된다.
        cur = await (await get_db()).execute(
            "SELECT count(*) FROM piku_auto_runs WHERE invocation_id<>''")
        assert (await cur.fetchone())[0] == 1, "형식이 맞는 xxxx…(64자)만 키가 되어야 한다"
        assert sched._INVOCATION_RE.match("manual-1757-abcd1234")
        for bad in ("짧음", "bad id!", "x" * 65, "abc"):
            assert not sched._INVOCATION_RE.match(bad), bad
    env.run_until_complete(go())


def test_concurrent_reports_of_one_invocation_yield_one_run(env):
    async def go():
        device_id, _, fp = await _register()
        body = {"trigger": "manual", "campaign": "final", "invocationId": "manual-race-0001",
                "sources": {"final": {"ok": True, "kind": "sent", "rows": 32}}}
        outs = await asyncio.gather(sched.report_run(fp, body), sched.report_run(fp, body),
                                    return_exceptions=True)
        ids = {o["id"] for o in outs if isinstance(o, dict)}
        assert len(ids) == 1, outs
        from database import get_db
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_auto_runs")
        assert (await cur.fetchone())[0] == 1
    env.run_until_complete(go())


def test_normalized_failure_kinds_are_recorded_and_surfaced(env):
    async def go():
        device_id, _, fp = await _register()
        for kind in ("manual_mode", "test_grant_required", "device_not_active",
                     "protocol_too_old", "bad_signature", "challenge_expired",
                     "token_rejected", "relay_unavailable", "timeout", "campaign_frozen",
                     "rate_limited"):
            out = await sched.report_run(fp, {
                "trigger": "manual", "campaign": "final",
                "invocationId": f"manual-kind-{kind}",
                "sources": {"final": {"ok": False, "kind": kind, "rows": 0}}})
            assert out["sources"]["final"]["kind"] == kind, kind
        # 모르는 종류·원문은 여전히 접힌다.
        out = await sched.report_run(fp, {
            "trigger": "manual", "campaign": "final", "invocationId": "manual-kind-html",
            "sources": {"final": {"ok": False, "kind": "<html>Traceback tok_x</html>", "rows": 0}}})
        assert out["sources"]["final"]["kind"] == "other"
        st = await sched.status()
        blob = repr(st)
        assert "tok_x" not in blob and "<html" not in blob
    env.run_until_complete(go())


def test_admin_grant_route_is_owner_only_and_returns_code_once(env):
    from routers.admin_router import DeviceTestGrantBody, piku_device_test_grant

    async def go():
        device_id, _, _ = await _register()
        r = await piku_device_test_grant(DeviceTestGrantBody(deviceId=device_id, campaign="final"),
                                         user={"id": 1})
        assert r["ok"] and r["grant"] and r["campaign"] == "final"
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await piku_device_test_grant(DeviceTestGrantBody(deviceId=device_id, campaign="qualifier"),
                                         user={"id": 1})
        assert "[campaign_frozen]" in str(ei.value.detail)
    env.run_until_complete(go())


def test_challenge_route_passes_the_grant_through(env):
    from routers.admin_router import DeviceChallengeBody, piku_device_challenge

    class Req:
        headers = {}
        client = type("C", (), {"host": "1.2.3.4"})()
        scope = {"type": "http", "headers": []}

    async def go():
        device_id, _, fp = await _register()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await piku_device_challenge(DeviceChallengeBody(fingerprint=fp, division="final",
                                                            automation=False, protocol=2), Req())
        assert "[test_grant_required]" in str(ei.value.detail)
        g = await devices.test_grant_issue(device_id, "final")
        out = await piku_device_challenge(DeviceChallengeBody(
            fingerprint=fp, division="final", automation=False, protocol=2,
            testGrant=g["grant"]), Req())
        assert out["ok"] and out["campaign"] == "final"
        assert "grant" not in repr(out)
    env.run_until_complete(go())


def test_route_model_keeps_invocation_id(env):
    """라우터 모델이 `invocationId`를 버리지 않는다.

    처음 구현에서 `DeviceRunReportBody`에 이 필드를 선언하지 않아 pydantic이 조용히
    떨어뜨렸고, 서버는 멱등 키를 영영 못 받았다(직접 `report_run`을 부르는 단위 테스트는
    통과했고 실제 Chrome E2E만 잡았다). 라우터 경로로 다시 고정한다."""
    from routers.admin_router import DeviceRunReportBody, piku_device_run_report

    async def go():
        _, _, fp = await _register()
        body = DeviceRunReportBody(
            fingerprint=fp, trigger="manual", campaign="final",
            invocationId="manual-route-abcd1234",
            sources={"final": {"ok": True, "kind": "sent", "rows": 32}})
        assert body.invocationId == "manual-route-abcd1234"
        a = await piku_device_run_report(body)
        b = await piku_device_run_report(body)
        assert a["id"] == b["id"] and a["invocationId"] == "manual-route-abcd1234"
        from database import get_db
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_auto_runs")
        assert (await cur.fetchone())[0] == 1
    env.run_until_complete(go())


# ── 6) 감사(2026-09-12): 끝난 회차 불변 · 식별자 충돌 · 허가 실패 종류 ──────
def test_finished_run_is_immutable_in_both_directions(env):
    """성공을 실패로도, 실패를 성공으로도 바꾸지 못한다. 쓰기 자체가 없다."""
    async def go():
        from database import get_db
        _, _, fp = await _register()
        ok_body = {"trigger": "manual", "campaign": "final", "invocationId": "manual-imm-0001",
                   "sources": {"final": {"ok": True, "kind": "sent", "rows": 32}}}
        first = await sched.report_run(fp, ok_body)
        assert first["outcome"] == "success"
        db = await get_db()
        cur = await db.execute("SELECT finished_at FROM piku_auto_runs WHERE id=?", (first["id"],))
        finished_at = (await cur.fetchone())[0]
        # 실패로 덮어쓰려는 재전송.
        again = await sched.report_run(fp, dict(
            ok_body, sources={"final": {"ok": False, "kind": "not_rendered", "rows": 0}}))
        assert again["id"] == first["id"]
        assert again["outcome"] == "success"
        assert again["sources"]["final"]["kind"] == "sent"
        cur = await db.execute("SELECT finished_at FROM piku_auto_runs WHERE id=?", (first["id"],))
        assert (await cur.fetchone())[0] == finished_at, "마감 시각까지 다시 쓰였다"
        cur = await db.execute("SELECT count(*) FROM piku_auto_runs")
        assert (await cur.fetchone())[0] == 1
    env.run_until_complete(go())


def test_same_invocation_on_another_campaign_or_trigger_is_refused(env):
    """같은 식별자를 다른 회차 성격에 재사용하면 섞지 않고 거절한다."""
    async def go():
        from database import get_db
        _, _, fp = await _register()
        body = {"trigger": "manual", "campaign": "final", "invocationId": "manual-mix-0001",
                "sources": {"final": {"ok": True, "kind": "sent", "rows": 32}}}
        await sched.report_run(fp, body)
        for bad in (dict(body, campaign="qualifier",
                         sources={"female_solo": {"ok": True, "kind": "sent", "rows": 64}}),
                    dict(body, trigger="alarm")):
            with pytest.raises(devices.DeviceError) as ei:
                await sched.report_run(fp, bad)
            assert ei.value.code == "invocation_conflict"
            assert "SQL" not in ei.value.message and "piku_" not in ei.value.message
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_auto_runs")
        assert (await cur.fetchone())[0] == 1
    env.run_until_complete(go())


def test_same_invocation_on_another_device_is_a_separate_run(env):
    """멱등 키는 장치별이다 — 다른 장치의 같은 문자열은 충돌이 아니라 별개 회차."""
    async def go():
        from database import get_db
        _, _, fp_a = await _register("노트북")
        _, _, fp_b = await _register("다른 PC")
        body = {"trigger": "manual", "campaign": "final", "invocationId": "manual-dup-0001",
                "sources": {"final": {"ok": True, "kind": "sent", "rows": 32}}}
        a = await sched.report_run(fp_a, body)
        b = await sched.report_run(fp_b, body)
        assert a["id"] != b["id"] and a["deviceId"] != b["deviceId"]
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_auto_runs")
        assert (await cur.fetchone())[0] == 2
    env.run_until_complete(go())


def test_grant_failures_are_distinguishable_but_leak_nothing(env):
    """없음·틀림·만료·사용됨을 구분한다(운영자가 취할 조치가 다르다)."""
    async def go():
        from database import get_db
        device_id, dev, _ = await _register()
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(device_id, "final", automation=False, protocol=2)
        assert ei.value.code == "test_grant_required"

        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(device_id, "final", automation=False, protocol=2,
                                          test_grant="ZZZZ9999")
        assert ei.value.code == "test_grant_invalid"

        g = await devices.test_grant_issue(device_id, "final")
        c = await sched.guarded_challenge(device_id, "final", automation=False, protocol=2,
                                          test_grant=g["grant"])
        await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(device_id, "final", automation=False, protocol=2,
                                          test_grant=g["grant"])
        assert ei.value.code == "test_grant_used"

        g2 = await devices.test_grant_issue(device_id, "final")
        db = await get_db()
        await db.execute("UPDATE piku_test_grants SET expires_at=? WHERE used_at=0",
                         (int(time.time()) - 1,))
        await db.commit()
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(device_id, "final", automation=False, protocol=2,
                                          test_grant=g2["grant"])
        assert ei.value.code == "test_grant_expired"

        # 어느 문구에도 코드 원문·해시·SQL이 없다.
        for code in devices.GRANT_FAIL_CODES:
            msg = devices._GRANT_FAIL_MESSAGES[code]
            assert g["grant"] not in msg and g2["grant"] not in msg
            assert "piku_" not in msg and "SELECT" not in msg and "sha256" not in msg
    env.run_until_complete(go())


def test_only_one_of_two_concurrent_consumers_wins(env):
    """조건부 UPDATE — 동시에 같은 허가를 내밀면 정확히 하나만 통과한다."""
    async def go():
        device_id, _, _ = await _register()
        g = await devices.test_grant_issue(device_id, "final")
        outs = await asyncio.gather(
            devices._consume_test_grant(device_id, "final", g["grant"]),
            devices._consume_test_grant(device_id, "final", g["grant"]))
        assert sorted(outs) == ["", "test_grant_used"], outs
    env.run_until_complete(go())
