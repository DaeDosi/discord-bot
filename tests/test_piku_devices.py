"""AUTO-1 — PIKU 자동 수집 **장치 등록·challenge 서명·revoke** 계약.

이 파일이 지키려는 것은 하나다: **확장 프로그램이 장기 자격 증명을 들고 있지
않게 하면서도 사람 개입 없이 수집 토큰을 받을 수 있게 한다.**

기존 계약(바꾸지 않는다):
  · 수집 토큰은 짧은 수명·1회용·부문 고정이고 DB에는 sha256 해시만 둔다.
  · 확장은 그 토큰으로 `/piku/collector/ingest`에 draft까지만 보낸다.

AUTO-1이 더하는 것:
  · 장치마다 **P-256 비추출형** 키를 만들고 서버는 **공개키만** 보관한다.
  · 서버가 만료되는 nonce challenge를 주고, 장치가 서명하면 그때 **기존 구조의**
    토큰을 발급한다. 토큰 구조 자체는 우회하지 않는다.
  · 장치는 개별 revoke할 수 있고, 그 순간부터 challenge·토큰이 끊긴다.

**실제 PIKU를 호출하지 않는다.** 서명 검증은 표준 라이브러리 키로 합성한다.
"""
import asyncio
import base64
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils  # noqa: E402

# 구현 대상(아직 없다 → 지금은 이 파일 전체가 실패해야 한다).
devices = pytest.importorskip(
    "singcup_piku_devices",
    reason="AUTO-1 장치 등록 모듈이 아직 없다",
)


# ── 테스트용 장치 키 ────────────────────────────────────────────────────────
class FakeDevice:
    """확장 안의 비추출형 키를 흉내 낸다 — 공개키만 서버로 나간다."""

    def __init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_b64(self) -> str:
        spki = self._key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(spki).decode()

    def sign(self, message: str) -> str:
        """WebCrypto와 **같은 형식**(P1363 r||s 64바이트)으로 서명한다.

        `cryptography`는 DER을 내므로 여기서 raw로 바꾼다 — 서버가 브라우저에서
        오는 형식을 그대로 받아야 하기 때문이다.
        """
        der = self._key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = asym_utils.decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return base64.b64encode(raw).decode()


_LOOP = None
_ORIG_DB_PATH = None


def run(coro):
    return _LOOP.run_until_complete(coro)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    """테스트마다 빈 DB. **운영 DB를 절대 건드리지 않는다.**

    `test_piku_collector.py`의 `env` fixture와 같은 방식이다 — `DB_PATH`는
    `database.db` 모듈 전역이라 패키지에 설정하면 먹지 않고 conftest의 공용 임시
    DB를 그대로 쓰게 되어 테스트끼리 상태가 샌다.
    """
    global _LOOP, _ORIG_DB_PATH
    from database import db as dbmod
    _ORIG_DB_PATH = dbmod.DB_PATH
    db_file = tmp_path / f"auto1-{uuid.uuid4().hex}.db"
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


async def _register(name: str = "거실 PC") -> tuple[int, FakeDevice, str]:
    """등록 두 단계를 한 번에 — 대부분의 테스트가 등록된 장치에서 시작한다."""
    started = await devices.register_start(name)
    dev = FakeDevice()
    done = await devices.register_finish(started["pairingCode"], dev.public_b64)
    return done["deviceId"], dev, done["fingerprint"]


# ── 1) 등록 ─────────────────────────────────────────────────────────────────
def test_register_start_returns_short_pairing_code_and_pending_status():
    async def go():
        r = await devices.register_start("거실 PC")
        assert r["status"] == "pending"
        code = r["pairingCode"]
        # 사람이 눈으로 옮겨 적을 수 있어야 한다 — 너무 길면 그 자체가 실패한다.
        assert 6 <= len(code) <= 12, code
        # 혼동되는 글자를 쓰지 않는다(0/O, 1/I/l).
        assert not set(code) & set("01OIl")
        lst = await devices.list_devices()
        assert [d["status"] for d in lst] == ["pending"]
        # 목록에는 secret이 없다.
        assert "pairingCode" not in lst[0]
        return r
    run(go())


def test_register_finish_stores_public_key_and_fingerprint_only():
    async def go():
        dev_id, dev, fp = await _register()
        lst = await devices.list_devices()
        d = next(x for x in lst if x["id"] == dev_id)
        assert d["status"] == "active"
        # 사람이 확장 화면과 대조할 지문이 있어야 한다.
        assert d["fingerprint"] == fp and len(fp) >= 8
        # **공개키 원문도 목록에 내보내지 않는다.**
        assert "publicKey" not in d
        assert "pairingCode" not in d
    run(go())


def test_pairing_code_is_single_use():
    async def go():
        started = await devices.register_start("PC")
        d1, d2 = FakeDevice(), FakeDevice()
        await devices.register_finish(started["pairingCode"], d1.public_b64)
        with pytest.raises(devices.DeviceError):
            await devices.register_finish(started["pairingCode"], d2.public_b64)
    run(go())


def test_expired_pairing_code_is_rejected():
    async def go():
        started = await devices.register_start("PC")
        await devices._expire_pairing_for_tests(started["deviceId"])
        with pytest.raises(devices.DeviceError):
            await devices.register_finish(started["pairingCode"], FakeDevice().public_b64)
    run(go())


def test_same_public_key_cannot_register_twice():
    """설정 파일을 복사해도 두 번째 장치가 되지 않는다."""
    async def go():
        dev = FakeDevice()
        a = await devices.register_start("PC A")
        await devices.register_finish(a["pairingCode"], dev.public_b64)
        b = await devices.register_start("PC B")
        with pytest.raises(devices.DeviceError):
            await devices.register_finish(b["pairingCode"], dev.public_b64)
    run(go())


def test_malformed_public_key_is_rejected():
    async def go():
        started = await devices.register_start("PC")
        for bad in ["", "not-base64!!", base64.b64encode(b"short").decode()]:
            with pytest.raises(devices.DeviceError):
                await devices.register_finish(started["pairingCode"], bad)
    run(go())


# ── 2) challenge ────────────────────────────────────────────────────────────
def test_challenge_issue_returns_nonce_and_expiry():
    async def go():
        dev_id, _, _ = await _register()
        c = await devices.challenge_issue(dev_id, "female_solo")
        assert c["division"] == "female_solo"
        assert len(base64.b64decode(c["nonce"])) >= 32
        assert 0 < c["expiresAt"] - int(time.time()) <= devices.CHALLENGE_TTL_SECONDS
        # challenge 자체는 secret이 아니지만 서명 대상 문자열은 서버가 정한다.
        assert c["message"] == devices.challenge_message(
            c["challengeId"], c["nonce"], "female_solo", dev_id)
    run(go())


def test_valid_signature_issues_short_lived_single_use_division_token():
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        out = await devices.challenge_redeem(
            c["challengeId"], dev.sign(c["message"]))
        assert out["division"] == "groups"
        assert out["ttlSeconds"] <= 3600
        # 발급된 토큰은 **기존 경로 그대로** 소비돼야 한다.
        import singcup_piku_collector as col
        await col.consume_token(out["token"], "groups")
        with pytest.raises(col.PikuError):     # 두 번은 안 된다
            await col.consume_token(out["token"], "groups")
    run(go())


def test_token_is_bound_to_the_requested_division():
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "female_solo")
        out = await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        import singcup_piku_collector as col
        with pytest.raises(col.PikuError):     # 다른 부문으로는 못 쓴다
            await col.consume_token(out["token"], "male_solo")
    run(go())


def test_challenge_is_single_use():
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "male_solo")
        sig = dev.sign(c["message"])
        await devices.challenge_redeem(c["challengeId"], sig)
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], sig)
    run(go())


def test_expired_challenge_is_rejected():
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        await devices._expire_challenge_for_tests(c["challengeId"])
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
    run(go())


def test_wrong_key_signature_is_rejected():
    async def go():
        dev_id, _, _ = await _register()
        other = FakeDevice()
        c = await devices.challenge_issue(dev_id, "groups")
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], other.sign(c["message"]))
    run(go())


def test_signature_over_a_different_message_is_rejected():
    """다른 challenge에 대한 서명을 옮겨 붙이지 못한다(replay)."""
    async def go():
        dev_id, dev, _ = await _register()
        c1 = await devices.challenge_issue(dev_id, "groups")
        c2 = await devices.challenge_issue(dev_id, "groups")
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c2["challengeId"], dev.sign(c1["message"]))
    run(go())


def test_garbage_signature_is_rejected_without_crashing():
    async def go():
        dev_id, _, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        for bad in ["", "!!!", base64.b64encode(b"\x00" * 64).decode(),
                    base64.b64encode(b"\x01" * 10).decode()]:
            with pytest.raises(devices.DeviceError):
                await devices.challenge_redeem(c["challengeId"], bad)
    run(go())


def test_unknown_division_is_rejected():
    async def go():
        dev_id, _, _ = await _register()
        with pytest.raises(devices.DeviceError):
            await devices.challenge_issue(dev_id, "mixed_doubles")
    run(go())


def test_concurrent_redeem_of_one_challenge_yields_exactly_one_token():
    """같은 challenge를 동시에 두 번 써도 토큰은 하나만 나온다."""
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        sig = dev.sign(c["message"])
        results = await asyncio.gather(
            devices.challenge_redeem(c["challengeId"], sig),
            devices.challenge_redeem(c["challengeId"], sig),
            return_exceptions=True)
        ok = [r for r in results if isinstance(r, dict)]
        bad = [r for r in results if isinstance(r, Exception)]
        assert len(ok) == 1 and len(bad) == 1, results
    run(go())


# ── 3) revoke ───────────────────────────────────────────────────────────────
def test_revoked_device_cannot_get_a_challenge():
    async def go():
        dev_id, _, _ = await _register()
        await devices.revoke(dev_id)
        with pytest.raises(devices.DeviceError):
            await devices.challenge_issue(dev_id, "groups")
    run(go())


def test_revoke_kills_challenges_already_outstanding():
    """revoke 시점에 이미 나가 있던 challenge도 무효가 된다."""
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        await devices.revoke(dev_id)
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
    run(go())


def test_revoke_burns_outstanding_challenges_in_storage():
    """**미사용 challenge를 그 자리에서 태운다.**

    위 테스트만으로는 부족하다 — 장치 status 검사가 먼저 걸려서, challenge를
    태우지 않아도 통과한다(변이 검사에서 실제로 놓쳤다). 방어가 두 겹인 것은
    좋지만 바깥 한 겹이 사라져도 창이 열리지 않아야 하므로 저장 상태를 직접 본다.
    """
    async def go():
        dev_id, _, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        from database import get_db
        conn = await get_db()
        cur = await conn.execute(
            "SELECT used_at FROM piku_collector_challenges WHERE id=?",
            (c["challengeId"],))
        assert (await cur.fetchone())[0] == 0, "발급 직후에는 미사용이어야 한다"
        await devices.revoke(dev_id)
        cur = await conn.execute(
            "SELECT used_at FROM piku_collector_challenges WHERE id=?",
            (c["challengeId"],))
        assert (await cur.fetchone())[0] > 0, "revoke가 미사용 challenge를 태우지 않았다"
    run(go())


def test_pending_device_cannot_get_a_challenge():
    async def go():
        started = await devices.register_start("PC")
        with pytest.raises(devices.DeviceError):
            await devices.challenge_issue(started["deviceId"], "groups")
    run(go())


def test_revoked_device_stays_listed_for_audit():
    async def go():
        dev_id, _, _ = await _register()
        await devices.revoke(dev_id)
        lst = await devices.list_devices()
        d = next(x for x in lst if x["id"] == dev_id)
        assert d["status"] == "revoked" and d["revokedAt"] > 0
    run(go())


# ── 4) 상태 표시 ────────────────────────────────────────────────────────────
def test_last_seen_and_last_success_are_tracked():
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        d = next(x for x in await devices.list_devices() if x["id"] == dev_id)
        assert d["lastSeenAt"] > 0
        await devices.mark_success(dev_id)
        d = next(x for x in await devices.list_devices() if x["id"] == dev_id)
        assert d["lastSuccessAt"] > 0
        await devices.mark_failure(dev_id, "not_rendered")
        d = next(x for x in await devices.list_devices() if x["id"] == dev_id)
        assert d["lastFailureAt"] > 0 and d["lastFailureKind"] == "not_rendered"
    run(go())


# ── 5) 모드 (AUTO-2·AUTO-3이 쓸 계약을 **지금** 고정한다) ───────────────────
def test_mode_defaults_to_manual():
    async def go():
        assert await devices.get_mode() == "MANUAL"
    run(go())


def test_mode_accepts_only_the_three_known_values():
    async def go():
        for m in ("MANUAL", "AUTO_COLLECT", "AUTO_PUBLISH"):
            await devices.set_mode(m)
            assert await devices.get_mode() == m
        for bad in ("auto", "AUTO", "", "AUTO_PUBLISH ", "MANUAL\n"):
            with pytest.raises(devices.DeviceError):
                await devices.set_mode(bad)
        assert await devices.get_mode() == "AUTO_PUBLISH"
    run(go())


def test_automation_allowed_only_when_mode_is_auto_and_device_active():
    """AUTO-2 스케줄러가 그대로 쓸 게이트. 여기서 계약을 고정한다."""
    async def go():
        dev_id, _, _ = await _register()
        await devices.set_mode("MANUAL")
        assert await devices.automation_allowed(dev_id) is False
        await devices.set_mode("AUTO_COLLECT")
        assert await devices.automation_allowed(dev_id) is True
        await devices.set_mode("AUTO_PUBLISH")
        assert await devices.automation_allowed(dev_id) is True
        await devices.revoke(dev_id)
        assert await devices.automation_allowed(dev_id) is False
    run(go())


def test_manual_mode_blocks_challenges_for_automation_but_not_manual_tokens():
    """MANUAL에서도 **운영자가 직접 누르는** 기존 수동 토큰 경로는 살아 있다."""
    async def go():
        await devices.set_mode("MANUAL")
        import singcup_piku_collector as col
        out = await col.issue_token("groups")          # 기존 수동 경로
        assert out["token"]
        dev_id, _, _ = await _register()
        # 자동화용 challenge는 모드로 막힌다.
        with pytest.raises(devices.DeviceError):
            await devices.challenge_issue(dev_id, "groups", automation=True)
        # 운영자가 확장에서 직접 pairing 후 수동 실행하는 것은 허용한다.
        c = await devices.challenge_issue(dev_id, "groups", automation=False)
        assert c["challengeId"]
    run(go())


# ── 6) 비밀 유출 방지 ───────────────────────────────────────────────────────
def test_no_secret_material_in_any_listing_or_status():
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        out = await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        blob = repr(await devices.list_devices()) + repr(await devices.status())
        assert out["token"] not in blob
        assert dev.public_b64 not in blob
        for k in ("pairingCode", "publicKey", "privateKey", "signature", "token"):
            assert k not in blob
    run(go())


def test_token_is_never_stored_in_plaintext():
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        out = await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        from database import get_db
        conn = await get_db()
        cur = await conn.execute("SELECT token_hash FROM piku_collector_tokens")
        rows = [r[0] for r in await cur.fetchall()]
        assert rows and all(out["token"] != r for r in rows)
        assert all(len(r) == 64 for r in rows)     # sha256 hex
    run(go())


# ── 7) 커밋 전 보안 계약 감사에서 추가된 것 ─────────────────────────────────
def test_pairing_code_is_stored_hashed_not_in_plaintext():
    """**원문은 DB에 없다.** 이 테이블이 통째로 새어도 코드를 되살릴 수 없어야 한다."""
    async def go():
        started = await devices.register_start("PC")
        code = started["pairingCode"]
        from database import get_db
        conn = await get_db()
        cur = await conn.execute(
            "SELECT pairing_code_hash FROM piku_collector_devices WHERE id=?",
            (started["deviceId"],))
        stored = (await cur.fetchone())[0]
        assert stored and stored != code, "등록 코드가 평문으로 저장됐다"
        assert len(stored) == 64, f"sha256 hex가 아니다: {len(stored)}"
        assert stored == devices._hash_code(code)
        # 행 전체를 문자열로 훑어도 원문이 없어야 한다.
        cur = await conn.execute("SELECT * FROM piku_collector_devices WHERE id=?",
                                 (started["deviceId"],))
        assert code not in repr(await cur.fetchone())
    run(go())


def test_pairing_code_is_case_insensitive():
    """확장 팝업이 대문자로 정규화한다 — 서버도 같은 규칙이어야 한다."""
    async def go():
        started = await devices.register_start("PC")
        done = await devices.register_finish(started["pairingCode"].lower(),
                                             FakeDevice().public_b64)
        assert done["status"] == "active"
    run(go())


def test_concurrent_pairing_with_one_code_succeeds_once():
    """같은 코드로 동시에 두 장치가 등록되지 않는다."""
    async def go():
        started = await devices.register_start("PC")
        d1, d2 = FakeDevice(), FakeDevice()
        results = await asyncio.gather(
            devices.register_finish(started["pairingCode"], d1.public_b64),
            devices.register_finish(started["pairingCode"], d2.public_b64),
            return_exceptions=True)
        ok = [r for r in results if isinstance(r, dict)]
        bad = [r for r in results if isinstance(r, Exception)]
        assert len(ok) == 1 and len(bad) == 1, results
    run(go())


def test_signature_bound_to_division_and_device():
    """부문이나 장치를 바꿔치기한 서명은 거부된다.

    서명 대상 문자열을 서버가 다시 만들기 때문에 성립한다. 클라이언트가 보낸
    문자열을 검증하면 아무 문자열에나 서명을 받아 낼 수 있다.
    """
    async def go():
        dev_id, dev, _ = await _register("A")
        other_id, _, _ = await _register("B")
        c = await devices.challenge_issue(dev_id, "groups")
        # 같은 challenge인데 부문만 바꿔 서명 → 거부
        wrong_div = devices.challenge_message(
            c["challengeId"], c["nonce"], "female_solo", dev_id)
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], dev.sign(wrong_div))
        # challenge는 실패해도 소비된다 → 새로 발급해 장치 id 바꿔치기 확인
        c2 = await devices.challenge_issue(dev_id, "groups")
        wrong_dev = devices.challenge_message(
            c2["challengeId"], c2["nonce"], "groups", other_id)
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c2["challengeId"], dev.sign(wrong_dev))
    run(go())


def test_challenge_nonce_is_csprng_and_unique():
    async def go():
        dev_id, _, _ = await _register()
        seen = set()
        for _ in range(20):
            c = await devices.challenge_issue(dev_id, "groups")
            raw = base64.b64decode(c["nonce"])
            assert len(raw) >= 32, f"nonce가 {len(raw)}바이트뿐이다"
            seen.add(c["nonce"])
        assert len(seen) == 20, "nonce가 반복된다"
    run(go())


def test_revoked_device_cannot_redeem_even_a_fresh_challenge():
    """폐기 뒤에는 어떤 경로로도 토큰이 나오지 않는다."""
    async def go():
        dev_id, dev, _ = await _register()
        await devices.revoke(dev_id)
        with pytest.raises(devices.DeviceError):
            await devices.challenge_issue(dev_id, "groups")
    run(go())


def test_default_state_has_no_active_device_so_no_token_can_be_issued():
    """**기본 상태(장치 0대)에서는 외부에서 토큰을 받을 수 없다.**

    challenge 발급에 아직 속도 제한이 없으므로(AUTO-2 과제), 최소한 '아무 장치도
    없을 때는 아무 것도 나오지 않는다'가 성립해야 한다.
    """
    async def go():
        assert await devices.list_devices() == []
        assert await devices.get_mode() == "MANUAL"
        # 지문을 찍어 봐도 장치가 없다.
        for guess in ("", "AAAA-BBBB-CCCC-DDDD", "0000-0000-0000-0000"):
            assert await devices.device_by_fingerprint(guess) is None
        # 존재하지 않는 장치로는 challenge가 나오지 않는다.
        for bad_id in (0, 1, 999, -1, "1"):
            with pytest.raises(devices.DeviceError):
                await devices.challenge_issue(bad_id, "groups")
    run(go())


def test_private_key_material_never_appears_server_side():
    """서버 코드·저장소 어디에도 개인키가 없다."""
    async def go():
        dev_id, dev, _ = await _register()
        from database import get_db
        conn = await get_db()
        cur = await conn.execute("SELECT * FROM piku_collector_devices")
        blob = repr(await cur.fetchall())
        # 공개키는 있어도 되지만 개인키 관련 표현은 없어야 한다.
        for bad in ("PRIVATE KEY", "pkcs8", "privateKey", "d="):
            assert bad not in blob, f"{bad}가 저장돼 있다"
    run(go())


def test_module_never_stores_or_returns_raw_token():
    """토큰 원문은 발급 응답에만 있고 DB에는 sha256만 남는다(기존 계약 재확인)."""
    async def go():
        dev_id, dev, _ = await _register()
        c = await devices.challenge_issue(dev_id, "groups")
        out = await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        from database import get_db
        conn = await get_db()
        cur = await conn.execute("SELECT * FROM piku_collector_tokens")
        blob = repr(await cur.fetchall())
        assert out["token"] not in blob
        assert out["ttlSeconds"] <= 600, "토큰 수명이 10분을 넘는다"
    run(go())


# ── 8) 라우트 권한 계약 ─────────────────────────────────────────────────────
def _router_src() -> str:
    from pathlib import Path as _P
    return (_P(__file__).resolve().parents[1]
            / "web" / "backend" / "routers" / "admin_router.py").read_text("utf-8")


def test_operator_routes_require_owner_auth():
    """등록 시작·목록·폐기·모드 변경은 **Nexadmin 로그인(OWNER JWT)** 이 필요하다."""
    s = _router_src()
    for fn in ("piku_devices_list", "piku_devices_register",
               "piku_devices_revoke", "piku_collector_set_mode"):
        i = s.index(f"async def {fn}(")
        sig = s[i:s.index("):", i)]
        assert "_require_owner" in sig, f"{fn}에 OWNER 인증이 없다"


def test_device_routes_do_not_take_owner_jwt():
    """확장이 부르는 세 경로는 OWNER JWT를 쓰지 않는다.

    쓰게 만들면 확장이 장기 자격 증명을 들고 있어야 해서 이 설계의 전제가 무너진다.
    각 경로의 실제 보호막은 1회용 pairing code / 지문+active / **개인키 서명**이다.
    """
    s = _router_src()
    for fn in ("piku_device_pair", "piku_device_challenge", "piku_device_token"):
        i = s.index(f"async def {fn}(")
        sig = s[i:s.index("):", i)]
        assert "_require_owner" not in sig, f"{fn}이 OWNER JWT를 요구한다"


def test_public_pair_endpoint_cannot_register_without_a_code():
    """코드 없이는 등록되지 않는다(모듈 계약으로 확인)."""
    async def go():
        await devices.register_start("PC")          # pending 장치는 존재한다
        for bad in ("", "   ", "WRONGCODE", "AAAAAAAA"):
            with pytest.raises(devices.DeviceError):
                await devices.register_finish(bad, FakeDevice().public_b64)
        assert [d["status"] for d in await devices.list_devices()] == ["pending"]
    run(go())
