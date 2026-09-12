"""SINGCUP-FINAL-1 — challenge/token v2 **공격 테스트**.

운영 레지스트리 그대로(예선 frozen · 본선 active). 실제 PIKU·운영 DB에 닿지 않는다.
하나라도 통과(우회)하면 커밋하지 않는다.
"""
# ruff: noqa: E501 — 공격 시나리오·SQL fixture는 한 줄이 길어야 읽힌다(신규 테스트 파일에만).
import asyncio
import base64
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

import singcup_piku_campaigns as camps  # noqa: E402
import singcup_piku_collector as col  # noqa: E402
import singcup_piku_devices as devices  # noqa: E402
import singcup_piku_scheduler as sched  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils  # noqa: E402
from singcup_piku import PikuError  # noqa: E402
from test_singcup_final import final_payload, qual_payload  # noqa: E402

_ORIG_DB_PATH = None


@pytest.fixture
def env(tmp_path):
    global _ORIG_DB_PATH
    from database import db as dbmod
    _ORIG_DB_PATH = dbmod.DB_PATH
    db_file = tmp_path / f"sec-{uuid.uuid4().hex}.db"
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


async def _register():
    started = await devices.register_start("공격 테스트 PC")
    dev = FakeDevice()
    done = await devices.register_finish(started["pairingCode"], dev.public_b64)
    await devices.set_mode("AUTO_COLLECT")
    return done["deviceId"], dev, done["fingerprint"]


async def _issue(device_id):
    return await devices.challenge_issue(device_id, "final", automation=True, protocol=2)


# ── canonical 형식 ──────────────────────────────────────────────────────────
def test_v2_canonical_string_is_exact_and_unambiguous(env):
    async def go():
        device_id, dev, _ = await _register()
        c = await _issue(device_id)
        parts = c["message"].split("|")
        assert parts == ["nexbot-piku-collector-v2", c["challengeId"], c["nonce"],
                         "final", "final", "2ut8Li", str(device_id)]
        # 어느 필드에도 구분자가 들어갈 수 없다(uuid hex · base64 · ASCII 상수 · 정수).
        assert all("|" not in p for p in parts)
        assert len(c["challengeId"]) == 32 and set(c["challengeId"]) <= set("0123456789abcdef")
        base64.b64decode(c["nonce"], validate=True)
        # v1은 예선 형식 그대로(기존 계약).
        assert devices.challenge_message("c", "n", "groups", 7) == "nexbot-piku-collector-v1|c|n|groups|7"
        # challenge 행에 campaign이 저장된다.
        from database import get_db
        db = await get_db()
        cur = await db.execute("SELECT campaign, division FROM piku_collector_challenges WHERE id=?",
                               (c["challengeId"],))
        assert tuple(await cur.fetchone()) == ("final", "final")
    env.run_until_complete(go())


# ── 서명 변조 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("mutate", [
    lambda m, cid, nonce, did: m.replace("|final|final|2ut8Li|", "|qualifier|final|2ut8Li|"),   # campaign 변조
    lambda m, cid, nonce, did: m.replace("|2ut8Li|", "|8jGsHE|"),                                # sourceId 변조
    lambda m, cid, nonce, did: m.replace("|final|final|", "|final|groups|"),                     # source 변조
    lambda m, cid, nonce, did: m[: m.rfind("|") + 1] + str(did + 1),                            # deviceId 변조
    lambda m, cid, nonce, did: m.replace(cid, uuid.uuid4().hex),                                 # challengeId 변조
    lambda m, cid, nonce, did: m.replace(nonce, base64.b64encode(b"x" * 32).decode()),           # nonce 변조
    lambda m, cid, nonce, did: m.replace("nexbot-piku-collector-v2", "nexbot-piku-collector-v1")
        .replace("|final|final|2ut8Li|", "|final|"),                                             # v1 downgrade
    lambda m, cid, nonce, did: m.replace("|final|final|", "|FINAL|final|"),                      # 대소문자
    lambda m, cid, nonce, did: m.replace("|final|final|", "| final|final|"),                     # 공백
    lambda m, cid, nonce, did: m.replace("|final|final|", "|fınal|final|"),                      # Unicode 유사 문자
    lambda m, cid, nonce, did: m.replace("|2ut8Li|", "|2ut8L%69|"),                              # URL 인코딩
])
def test_signature_over_mutated_message_is_rejected(env, mutate):
    async def go():
        device_id, dev, _ = await _register()
        c = await _issue(device_id)
        forged = mutate(c["message"], c["challengeId"], c["nonce"], device_id)
        assert forged != c["message"], "변조가 적용되지 않았다"
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_redeem(c["challengeId"], dev.sign(forged))
        assert ei.value.code == "bad_signature"
        # 실패해도 challenge는 소비됐다 — 정상 서명으로도 더는 못 쓴다.
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        assert ei.value.code == "bad_challenge"
        from database import get_db
        cur = await (await get_db()).execute("SELECT count(*) FROM piku_collector_tokens")
        assert (await cur.fetchone())[0] == 0, "변조 서명으로 토큰이 발급됐다"
    env.run_until_complete(go())


def test_missing_or_malformed_signature(env):
    async def go():
        device_id, dev, _ = await _register()
        for bad in [None, "", "   ", "not-base64!", base64.b64encode(b"short").decode(),
                    base64.b64encode(b"\x00" * 64).decode()]:
            c = await _issue(device_id)
            with pytest.raises(devices.DeviceError) as ei:
                await devices.challenge_redeem(c["challengeId"], bad)
            assert ei.value.code == "bad_signature"
        # 다른 장치의 키로 서명
        other = FakeDevice()
        c = await _issue(device_id)
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], other.sign(c["message"]))
    env.run_until_complete(go())


# ── challenge 수명 ──────────────────────────────────────────────────────────
def test_challenge_single_use_expiry_and_replay(env):
    async def go():
        device_id, dev, _ = await _register()
        c = await _issue(device_id)
        t = await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        assert t["campaign"] == "final"
        # 같은 challenge 재사용(nonce 재사용) 불가
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        assert ei.value.code == "bad_challenge"
        # 만료된 challenge
        c2 = await _issue(device_id)
        await devices._expire_challenge_for_tests(c2["challengeId"])
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_redeem(c2["challengeId"], dev.sign(c2["message"]))
        assert ei.value.code == "bad_challenge"
        # 존재하지 않는 challenge
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(uuid.uuid4().hex, dev.sign("x"))
    env.run_until_complete(go())


def test_revoked_device_and_mode_gates(env):
    async def go():
        device_id, dev, fp = await _register()
        c = await _issue(device_id)
        await devices.revoke(device_id)
        # revoke는 미사용 challenge를 소각한다.
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(device_id, "final", automation=True, protocol=2)
        assert ei.value.code == "device_not_active"
        # 새 장치: MANUAL이면 자동 challenge 거절, 수동은 허용
        device_id2, dev2, _ = await _register()
        await devices.set_mode("MANUAL")
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(device_id2, "final", automation=True, protocol=2)
        assert ei.value.code == "automation_off"
        c = await devices.challenge_issue(device_id2, "final", automation=False, protocol=2)
        assert c["campaign"] == "final"
    env.run_until_complete(go())


def test_old_extension_cannot_get_final_challenge(env):
    """protocol 미선언(구버전 확장 1.0.x)은 본선 challenge를 받지 못한다."""
    async def go():
        device_id, dev, fp = await _register()
        for proto in [None, 0, 1, True, "2", 1.5]:
            with pytest.raises(devices.DeviceError) as ei:
                kwargs = {} if proto is None else {"protocol": proto}
                await devices.challenge_issue(device_id, "final", automation=True, **kwargs)
            assert ei.value.code == "protocol_too_old", proto
        # 라우터 경로(guarded_challenge)도 같은 기본값 1을 쓴다.
        with pytest.raises(devices.DeviceError) as ei:
            await sched.guarded_challenge(device_id, "final", automation=True)
        assert ei.value.code == "protocol_too_old"
        c = await sched.guarded_challenge(device_id, "final", automation=True, protocol=2)
        assert c["campaign"] == "final"
    env.run_until_complete(go())


def test_frozen_campaign_rejects_v1_challenge_and_redeem_rechecks(env):
    async def go():
        device_id, dev, _ = await _register()
        for d in ("female_solo", "male_solo", "groups"):
            with pytest.raises(devices.DeviceError) as ei:
                await devices.challenge_issue(device_id, d, automation=False)
            assert ei.value.code == "campaign_frozen"
        # 발급 뒤 단계가 동결되면 redeem도 거절한다.
        c = await _issue(device_id)
        snap = camps._set_for_tests(statuses={"final": "frozen"})
        try:
            with pytest.raises(devices.DeviceError) as ei:
                await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
            assert ei.value.code == "campaign_frozen"
        finally:
            camps._restore_for_tests(snap)
    env.run_until_complete(go())


# ── 토큰 결합 ───────────────────────────────────────────────────────────────
def test_token_hash_binds_campaign_and_source(env):
    async def go():
        device_id, dev, _ = await _register()
        c = await _issue(device_id)
        t = await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        raw = t["token"]
        from database import get_db
        db = await get_db()
        cur = await db.execute("SELECT token_hash, division, campaign FROM piku_collector_tokens")
        h, d, cp = await cur.fetchone()
        assert (d, cp) == ("final", "final")
        assert h == col._hash_token(raw, "final", "final")
        assert h != col._hash_token(raw) and h != col._hash_token(raw, "groups", "qualifier")
        assert raw not in h
        # final 토큰 → 예선 source 불가(세 부문 + 모르는 키)
        for d in ("female_solo", "male_solo", "groups", "FINAL", "final ", "fınal", "2ut8Li", None, 7):
            with pytest.raises(PikuError):
                await col.consume_token(raw, d)
        # 아직 소비되지 않았다 → 본선에 1회 통과 → 재사용 불가
        await col.consume_token(raw, "final")
        with pytest.raises(PikuError):
            await col.consume_token(raw, "final")
    env.run_until_complete(go())


def test_qualifier_token_cannot_be_used_for_final(env):
    """예선(동결) 토큰이 어떻게든 존재해도(동결 전 발급분) 본선에는 못 쓴다."""
    async def go():
        snap = camps._set_for_tests(statuses={"qualifier": "active"})
        try:
            t = await col.issue_token("groups")
        finally:
            camps._restore_for_tests(snap)
        with pytest.raises(PikuError):
            await col.consume_token(t["token"], "final")
        # 동결된 예선 source로도 소비 자체는 되지만 이어지는 save_draft가 campaign_frozen이다.
        await col.consume_token(t["token"], "groups")
        with pytest.raises(PikuError) as ei:
            await col.save_draft(qual_payload("groups"))
        assert ei.value.kind == "campaign_frozen"
    env.run_until_complete(go())


def test_token_expiry(env):
    async def go():
        t = await col.issue_token("final")
        from database import get_db
        db = await get_db()
        await db.execute("UPDATE piku_collector_tokens SET expires_at=?", (int(time.time()) - 1,))
        await db.commit()
        with pytest.raises(PikuError):
            await col.consume_token(t["token"], "final")
    env.run_until_complete(go())


# ── ingest 경로: 토큰 ↔ payload ────────────────────────────────────────────
async def _ingest(token, body):
    """`admin_router.piku_collector_ingest`와 같은 순서(소비 → 검증·저장)."""
    division = body.get("division") if isinstance(body, dict) else None
    await col.consume_token(token, division)
    return await col.save_draft(body)


def test_ingest_rejects_payload_that_does_not_match_token_binding(env):
    async def go():
        cases = [
            ("campaign 변조", {"campaign": "qualifier"}, "bad_campaign"),
            ("campaign 대문자", {"campaign": "FINAL"}, "bad_campaign"),
            ("campaign 공백", {"campaign": "final "}, "bad_campaign"),
            ("campaign Unicode", {"campaign": "fınal"}, "bad_campaign"),
            ("sourceId 변조", {"sourceId": "8jGsHE"}, "bad_source"),
            ("sourceId URL 인코딩", {"sourceId": "2ut8L%69"}, "bad_source"),
            ("sourceId 대소문자", {"sourceId": "2UT8LI"}, "bad_source"),
            ("sourceUrl 다른 페이지", {"sourceUrl": "https://www.piku.co.kr/w/rank/8jGsHE"}, "bad_source"),
            ("sourceUrl 쿼리", {"sourceUrl": "https://www.piku.co.kr/w/rank/2ut8Li?x=1"}, "bad_source"),
            ("sourceUrl 인코딩", {"sourceUrl": "https://www.piku.co.kr/w/rank/2ut8L%69"}, "bad_source"),
            ("sourceUrl 슬래시", {"sourceUrl": "https://www.piku.co.kr/w/rank/2ut8Li/"}, "bad_source"),
            ("제목 변조", {"pageTitle": "다른 월드컵"}, "bad_title"),
            ("허용 밖 키", {"cookie": "sid=1"}, "parse_failed"),
        ]
        for name, patch, kind in cases:
            t = await col.issue_token("final")
            body = final_payload()
            body.update(patch)
            with pytest.raises(PikuError) as ei:
                await _ingest(t["token"], body)
            assert ei.value.kind == kind, name
            # 토큰은 소비됐다(실패로 토큰을 무한히 시험할 수 없다).
            with pytest.raises(PikuError):
                await col.consume_token(t["token"], "final")
        # 다른 campaign의 payload(예선 groups)를 본선 토큰으로: division이 groups라 토큰 불일치.
        t = await col.issue_token("final")
        with pytest.raises(PikuError) as ei:
            await _ingest(t["token"], qual_payload("groups"))
        assert ei.value.kind == "bad_token"
        # 정상 payload는 통과하고 draft만 만든다(공개 아님).
        t = await col.issue_token("final")
        r = await _ingest(t["token"], final_payload())
        assert r["published"] is False and r["campaign"] == "final"
        from database import get_db
        cur = await (await get_db()).execute(
            "SELECT count(*) FROM piku_datasets WHERE status='active'")
        assert (await cur.fetchone())[0] == 0
    env.run_until_complete(go())


def test_legacy_rows_without_campaign_mean_qualifier_only(env):
    """campaign 컬럼 기본값(qualifier)인 legacy 토큰·challenge 행은 본선에 닿지 못한다."""
    async def go():
        from database import get_db
        db = await get_db()
        now = int(time.time())
        raw = "legacy-token"
        # 구버전 코드가 남겼을 형태: campaign 미기재(기본값) + 구 해시(raw만)
        await db.execute(
            "INSERT INTO piku_collector_tokens (token_hash, division, expires_at, created_at)"
            " VALUES (?,?,?,?)", (col._hash_token(raw), "groups", now + 600, now))
        await db.commit()
        cur = await db.execute("SELECT campaign FROM piku_collector_tokens")
        assert (await cur.fetchone())[0] == "qualifier"
        with pytest.raises(PikuError):
            await col.consume_token(raw, "final")
        with pytest.raises(PikuError):      # 구 해시는 새 결합 해시와도 다르다
            await col.consume_token(raw, "groups")
    env.run_until_complete(go())


def test_no_secrets_in_logs_or_db(env, capsys, caplog):
    """운영 로그 레벨(INFO)에서 토큰·nonce·서명 대상이 남지 않고, DEBUG(aiosqlite가 SQL
    파라미터를 그대로 찍는다)에서도 **토큰 원문**은 어디에도 없다. nonce는 설계상 DB에
    저장되는 값(비밀이 아니라 1회성)이라 aiosqlite DEBUG 파라미터 echo에는 나올 수 있다."""
    import logging
    caplog.set_level(logging.DEBUG)

    async def go():
        device_id, dev, fp = await _register()
        c = await _issue(device_id)
        t = await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        await col.consume_token(t["token"], "final")
        return c, t

    c, t = env.run_until_complete(go())
    cap = capsys.readouterr()
    debug_out = cap.out + cap.err + caplog.text
    info_out = cap.out + cap.err + "\n".join(
        r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    assert t["token"] not in debug_out, "토큰 원문이 로그(DEBUG 포함)에 남았다"
    assert "PRIVATE" not in debug_out
    for secret in (t["token"], c["nonce"], c["message"]):
        assert secret not in info_out, "운영 로그 레벨(INFO)에 비밀이 남았다"

    async def db_check():
        from database import get_db
        db = await get_db()
        rows = []
        for table in ("piku_collector_tokens", "piku_collector_devices", "piku_auto_runs"):
            cur = await db.execute(f"SELECT * FROM {table}")
            rows += [tuple(r) for r in await cur.fetchall()]
        blob = repr(rows)
        assert t["token"] not in blob
        assert "BEGIN" not in blob and "PRIVATE" not in blob
    env.run_until_complete(db_check())


def test_run_report_cannot_touch_data(env):
    """회차 보고 경로는 draft·공개·토큰에 닿지 못한다 — 이력 행만 만든다."""
    async def go():
        device_id, dev, fp = await _register()
        from database import get_db
        db = await get_db()
        before = {}
        for tbl in ("piku_datasets", "piku_entries", "piku_collector_tokens", "piku_mappings"):
            cur = await db.execute(f"SELECT count(*) FROM {tbl}")
            before[tbl] = (await cur.fetchone())[0]
        await sched.report_run(fp, {"trigger": "alarm", "campaign": "final",
                                    "sources": {"final": {"ok": True, "kind": "sent", "rows": 32,
                                                          "token": "x", "rowsData": [1]}}})
        for tbl, n in before.items():
            cur = await db.execute(f"SELECT count(*) FROM {tbl}")
            assert (await cur.fetchone())[0] == n, tbl
    env.run_until_complete(go())
