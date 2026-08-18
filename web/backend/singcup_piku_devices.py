"""AUTO-1 — PIKU 자동 수집 **장치 등록 · challenge 서명 · revoke**.

## 이 모듈이 푸는 문제

기존 수집 경로는 안전하지만 **사람이 매번 붙어 있어야** 한다: 운영자가 Nexadmin에서
10분짜리 1회용 토큰을 발급하고, 확장 팝업에 붙여 넣고, 세 부문을 각각 실행한다.
자동화하려면 확장이 스스로 토큰을 받아야 하는데, 그렇다고 **장기 bearer 토큰을
`chrome.storage`에 넣어 두면** 평문이라 복제되고, 어느 PC에서 샜는지 알 수 없어
개별 폐기도 불가능하다.

그래서 자격 증명을 **장치에 묶인 키**로 바꾼다:

    등록: Nexadmin이 짧은 pairing code 발급 → 확장이 P-256 **비추출형** 키를 만들고
          공개키만 보내 code를 소진 → 서버는 공개키·지문만 보관
    사용: 확장이 부문을 지정해 challenge 요청 → 서버가 nonce·만료 부여 →
          확장이 서명 → 서버가 검증 → **기존 구조 그대로의** 10분·1회용·부문 고정
          수집 토큰 발급

**기존 토큰 구조를 우회하지 않는다.** 바뀌는 것은 "누가 그 토큰을 받느냐"뿐이고,
발급 이후 경로(`consume_token` → `save_draft` → draft에서 정지)는 그대로다.

## 잔여 위험 — 과장하지 않는다

비추출형 키는 **바이트를 꺼낼 수 없을 뿐**이다. 악성 코드가 같은 확장 컨텍스트를
잡으면 그 키로 서명을 시킬 수 있다. 정확한 표현은 "복제 없이 그 장치에서만 오용
가능하고, 드러나면 그 장치만 즉시 폐기 가능"이다. "절대 탈취 불가"가 아니다.

또 하나 실측으로 확인한 사실: **Chrome의 host_permissions 경로(path)는
`scripting.executeScript`와 탭 URL 가시성을 제한하지 못한다.** `/w/rank/*`로 좁혀
적어도 실제로는 그 오리진 전체에 주입할 수 있다. 따라서 "정본 URL만 읽는다"는
보장은 Chrome이 아니라 **우리 코드**가 해야 한다(수집 단계는 AUTO-2).

## 모드

`MANUAL`(기본) / `AUTO_COLLECT` / `AUTO_PUBLISH`. 설정 행이 **없으면 MANUAL**이다 —
비어 있는 상태와 가장 안전한 상태가 같아야 한다. 이 모듈은 모드를 읽고 게이트만
제공하고, 실제 스케줄러(AUTO-2)와 자동 공개(AUTO-3)는 여기에 없다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils

from database import get_db

log = logging.getLogger(__name__)

DIVISIONS = ("female_solo", "male_solo", "groups")
MODES = ("MANUAL", "AUTO_COLLECT", "AUTO_PUBLISH")
DEFAULT_MODE = "MANUAL"

#: pairing code 수명. 사람이 화면을 보고 확장에 옮겨 적는 시간이면 충분하다.
PAIRING_TTL_SECONDS = max(60, min(3600,
                                  int(os.getenv("PIKU_DEVICE_PAIRING_TTL", "600"))))
#: challenge 수명. 서명은 즉시 이뤄지므로 짧게 둔다.
CHALLENGE_TTL_SECONDS = max(30, min(600,
                                    int(os.getenv("PIKU_DEVICE_CHALLENGE_TTL", "120"))))

#: pairing code 문자 집합 — `0/O`, `1/I/l`처럼 눈으로 헷갈리는 글자를 뺀다.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8

#: 서명 대상 문자열의 접두사. 다른 용도의 서명을 이 자리에 옮겨 붙이지 못하게 한다.
_SIGN_PREFIX = "nexbot-piku-collector-v1"


class DeviceError(Exception):
    """운영자에게 그대로 보여 줘도 되는 실패. **secret을 담지 않는다.**"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _log(event: str, **fields: Any) -> None:
    """구조화 로그. **토큰·서명·공개키 원문·pairing code를 절대 넣지 않는다.**"""
    try:
        log.info("piku_device %s", json.dumps({"event": event, **fields},
                                              ensure_ascii=False, default=str))
    except Exception:      # 로깅이 흐름을 끊지 않는다
        log.info("piku_device %s", event)


# ── 설정(모드) ──────────────────────────────────────────────────────────────
async def get_mode() -> str:
    """현재 모드. 행이 없거나 값이 이상하면 **MANUAL**로 떨어진다."""
    db = await get_db()
    cur = await db.execute(
        "SELECT value FROM piku_collector_settings WHERE key='mode'")
    row = await cur.fetchone()
    value = row[0] if row else None
    return value if value in MODES else DEFAULT_MODE


async def set_mode(mode: Any) -> str:
    """모드 변경. **아는 세 값만** 받는다(공백·대소문자 변형도 거부)."""
    if not isinstance(mode, str) or mode not in MODES:
        raise DeviceError("bad_mode",
                          f"알 수 없는 모드입니다. {' / '.join(MODES)} 중 하나여야 합니다.")
    db = await get_db()
    await db.execute(
        "INSERT INTO piku_collector_settings (key, value, updated_at) VALUES ('mode',?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (mode, int(time.time())))
    await db.commit()
    _log("mode_changed", mode=mode)
    return mode


async def automation_allowed(device_id: int) -> bool:
    """스케줄러(AUTO-2)가 그대로 쓸 게이트.

    자동 실행은 **모드가 자동이고 장치가 active일 때만** 허용된다. 둘 중 하나라도
    아니면 False다 — 켜져 있는지 애매한 상태를 만들지 않는다.
    """
    if await get_mode() == "MANUAL":
        return False
    row = await _device_row(device_id)
    return bool(row and row["status"] == "active")


# ── 장치 등록 ───────────────────────────────────────────────────────────────
def _hash_code(raw: str) -> str:
    """등록 코드 해시. **원문은 DB에 넣지 않는다.**

    대문자로 맞춘 뒤 해시한다 — 확장 팝업이 입력을 대문자로 정규화하므로,
    여기서 같은 규칙을 쓰지 않으면 사람이 소문자로 적었을 때만 조용히 실패한다.
    """
    return hashlib.sha256(raw.strip().upper().encode("utf-8")).hexdigest()


def _fingerprint(spki: bytes) -> str:
    """사람이 확장 화면과 대조할 지문. 공개키 해시라 비밀이 아니다."""
    digest = hashlib.sha256(spki).hexdigest().upper()
    return "-".join(digest[i:i + 4] for i in range(0, 16, 4))    # 예: A1B2-C3D4-...


def _parse_public_key(public_key_b64: Any) -> tuple[bytes, ec.EllipticCurvePublicKey]:
    """확장이 보낸 SPKI(base64)를 검증한다. **P-256만** 받는다."""
    if not isinstance(public_key_b64, str) or not public_key_b64.strip():
        raise DeviceError("bad_public_key", "공개키가 없습니다.")
    try:
        spki = base64.b64decode(public_key_b64, validate=True)
    except Exception as e:
        raise DeviceError("bad_public_key", "공개키 형식이 올바르지 않습니다.") from e
    try:
        key = serialization.load_der_public_key(spki)
    except Exception as e:
        raise DeviceError("bad_public_key", "공개키를 읽지 못했습니다.") from e
    if not isinstance(key, ec.EllipticCurvePublicKey) \
            or not isinstance(key.curve, ec.SECP256R1):
        raise DeviceError("bad_public_key", "P-256 공개키만 등록할 수 있습니다.")
    return spki, key


async def register_start(name: Any) -> dict:
    """등록 1단계 — pending 장치와 **1회용 pairing code**를 만든다.

    code 원문은 이 반환값에서 한 번만 나온다. 목록·상태 API에는 실리지 않는다.
    """
    label = (name or "").strip() if isinstance(name, str) else ""
    if not label:
        raise DeviceError("bad_name", "장치 이름을 입력해 주세요.")
    if len(label) > 40:
        raise DeviceError("bad_name", "장치 이름이 너무 깁니다(40자 이하).")
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    now = int(time.time())
    db = await get_db()
    # **해시만 저장한다.** 원문은 아래 반환값에서 한 번 나가고 여기서 사라진다.
    cur = await db.execute(
        "INSERT INTO piku_collector_devices (name, status, pairing_code_hash,"
        " pairing_expires_at, created_at) VALUES (?,'pending',?,?,?)",
        (label, _hash_code(code), now + PAIRING_TTL_SECONDS, now))
    await db.commit()
    _log("register_started", device_id=cur.lastrowid, name=label)
    return {"deviceId": cur.lastrowid, "name": label, "status": "pending",
            "pairingCode": code, "expiresAt": now + PAIRING_TTL_SECONDS,
            "ttlSeconds": PAIRING_TTL_SECONDS}


async def register_finish(pairing_code: Any, public_key_b64: Any) -> dict:
    """등록 2단계 — 공개키를 묶고 code를 소진한다.

    **공개키 검증을 먼저 한다.** 형식이 틀린 요청으로 code가 타 버리면 운영자가
    이유도 모른 채 다시 발급받아야 한다.
    """
    spki, _ = _parse_public_key(public_key_b64)
    if not isinstance(pairing_code, str) or not pairing_code.strip():
        raise DeviceError("bad_pairing", "등록 코드가 없습니다.")
    fp = _fingerprint(spki)
    now = int(time.time())
    db = await get_db()

    # 같은 공개키가 이미 있으면 거절한다 — 설정 파일 복사로 장치를 늘리지 못한다.
    cur = await db.execute(
        "SELECT id FROM piku_collector_devices WHERE fingerprint=?", (fp,))
    if await cur.fetchone():
        raise DeviceError("duplicate_key",
                          "이미 등록된 키입니다. 다른 PC는 새 키로 등록해 주세요.")

    # 조건부 UPDATE의 rowcount로 소진을 판정한다 — SELECT 후 UPDATE로 나누면
    # 두 요청이 같은 code를 동시에 통과할 수 있다.
    cur = await db.execute(
        "UPDATE piku_collector_devices SET status='active', public_key=?,"
        " fingerprint=?, registered_at=?, pairing_code_hash=NULL,"
        " pairing_expires_at=0 "
        "WHERE pairing_code_hash=? AND status='pending' AND pairing_expires_at>?",
        (base64.b64encode(spki).decode(), fp, now,
         _hash_code(pairing_code), now))
    await db.commit()
    if not cur.rowcount:
        raise DeviceError("bad_pairing", "등록 코드가 만료됐거나 이미 사용됐습니다.")

    cur = await db.execute(
        "SELECT id, name FROM piku_collector_devices WHERE fingerprint=?", (fp,))
    row = await cur.fetchone()
    _log("register_finished", device_id=row[0], fingerprint=fp)
    return {"deviceId": row[0], "name": row[1], "status": "active", "fingerprint": fp}


async def _device_row(device_id: Any) -> dict | None:
    if not isinstance(device_id, int):
        try:
            device_id = int(device_id)
        except Exception:
            return None
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, status, public_key, fingerprint FROM piku_collector_devices"
        " WHERE id=?", (device_id,))
    row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "status": row[2],
            "public_key": row[3], "fingerprint": row[4]}


async def device_by_fingerprint(fingerprint: Any) -> dict | None:
    """지문으로 장치를 찾는다.

    확장이 challenge를 요청할 때 **정수 id 대신 지문**을 쓰게 하려는 것이다.
    id는 1, 2, 3…이라 아무나 훑을 수 있다. 지문은 비밀이 아니지만 추측할 수는
    없어서, 인증 없는 challenge 발급 경로가 통째로 열려 보이지 않는다.
    (challenge는 그 자체로 secret이 아니고 소진에는 개인키가 필요하므로, 이것은
    권한 경계가 아니라 잡음·열거를 줄이는 조치다.)
    """
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        return None
    db = await get_db()
    cur = await db.execute(
        "SELECT id FROM piku_collector_devices WHERE fingerprint=?",
        (fingerprint.strip().upper(),))
    row = await cur.fetchone()
    return await _device_row(row[0]) if row else None


async def list_devices() -> list[dict]:
    """Nexadmin 목록. **pairing code·공개키 원문은 실리지 않는다.**"""
    db = await get_db()
    cur = await db.execute(
        "SELECT id, name, status, fingerprint, created_at, registered_at,"
        " revoked_at, last_seen_at, last_success_at, last_failure_at,"
        " last_failure_kind FROM piku_collector_devices ORDER BY id")
    return [{
        "id": r[0], "name": r[1], "status": r[2], "fingerprint": r[3] or "",
        "createdAt": r[4], "registeredAt": r[5], "revokedAt": r[6],
        "lastSeenAt": r[7], "lastSuccessAt": r[8], "lastFailureAt": r[9],
        "lastFailureKind": r[10] or "",
    } for r in await cur.fetchall()]


async def revoke(device_id: Any) -> dict:
    """장치 폐기. **이미 나가 있던 challenge도 그 자리에서 무효**가 된다."""
    row = await _device_row(device_id)
    if not row:
        raise DeviceError("no_device", "장치를 찾을 수 없습니다.")
    now = int(time.time())
    db = await get_db()
    await db.execute(
        "UPDATE piku_collector_devices SET status='revoked', revoked_at=? WHERE id=?",
        (now, row["id"]))
    # 미사용 challenge를 즉시 태운다 — revoke 직전에 받아 둔 challenge로
    # 토큰을 하나 더 받아 가는 창을 남기지 않는다.
    await db.execute(
        "UPDATE piku_collector_challenges SET used_at=? WHERE device_id=? AND used_at=0",
        (now, row["id"]))
    await db.commit()
    _log("revoked", device_id=row["id"])
    return {"deviceId": row["id"], "status": "revoked", "revokedAt": now}


async def mark_success(device_id: Any) -> None:
    row = await _device_row(device_id)
    if not row:
        return
    db = await get_db()
    now = int(time.time())
    await db.execute(
        "UPDATE piku_collector_devices SET last_success_at=?, last_seen_at=? WHERE id=?",
        (now, now, row["id"]))
    await db.commit()


async def mark_failure(device_id: Any, kind: Any) -> None:
    """실패를 실패로 남긴다. `kind`는 짧은 분류어이고 **본문은 받지 않는다.**"""
    row = await _device_row(device_id)
    if not row:
        return
    label = str(kind or "unknown")[:40]
    db = await get_db()
    now = int(time.time())
    await db.execute(
        "UPDATE piku_collector_devices SET last_failure_at=?, last_failure_kind=?,"
        " last_seen_at=? WHERE id=?", (now, label, now, row["id"]))
    await db.commit()


async def _touch_seen(device_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE piku_collector_devices SET last_seen_at=? WHERE id=?",
        (int(time.time()), device_id))
    await db.commit()


# ── challenge ───────────────────────────────────────────────────────────────
def challenge_message(challenge_id: str, nonce: str, division: str,
                      device_id: int) -> str:
    """서명 대상 문자열. **서버가 정하고 서버가 다시 만든다.**

    클라이언트가 보낸 문자열을 그대로 검증하면 아무 문자열에나 서명을 받아 낼 수
    있다. 여기에 challenge id·nonce·부문·장치를 모두 묶어 두어, 다른 challenge나
    다른 부문의 서명을 옮겨 붙일 수 없게 한다.
    """
    return f"{_SIGN_PREFIX}|{challenge_id}|{nonce}|{division}|{device_id}"


async def challenge_issue(device_id: Any, division: Any, *,
                          automation: bool = False) -> dict:
    """challenge 발급.

    `automation=True`는 스케줄러가 부르는 경로다 — 모드가 MANUAL이면 거절한다.
    운영자가 확장에서 직접 누르는 수동 경로(`automation=False`)는 모드와 무관하게
    허용한다. 자동화를 꺼 둔다고 수동 수집까지 막히면 안 된다.
    """
    if division not in DIVISIONS:
        raise DeviceError("bad_division", "알 수 없는 부문입니다.")
    row = await _device_row(device_id)
    if not row:
        raise DeviceError("no_device", "장치를 찾을 수 없습니다.")
    if row["status"] != "active":
        raise DeviceError(
            "device_not_active",
            "등록이 끝나지 않았거나 폐기된 장치입니다." if row["status"] != "active" else "")
    if automation and await get_mode() == "MANUAL":
        raise DeviceError("automation_off",
                          "자동 수집이 꺼져 있습니다(MANUAL). Nexadmin에서 모드를 바꿔 주세요.")

    cid = uuid.uuid4().hex
    nonce = base64.b64encode(secrets.token_bytes(32)).decode()
    now = int(time.time())
    db = await get_db()
    await db.execute(
        "INSERT INTO piku_collector_challenges (id, device_id, division, nonce,"
        " expires_at, created_at) VALUES (?,?,?,?,?,?)",
        (cid, row["id"], division, nonce, now + CHALLENGE_TTL_SECONDS, now))
    await db.commit()
    await _touch_seen(row["id"])
    _log("challenge_issued", device_id=row["id"], division=division,
         automation=automation)
    return {"challengeId": cid, "nonce": nonce, "division": division,
            "deviceId": row["id"], "expiresAt": now + CHALLENGE_TTL_SECONDS,
            "message": challenge_message(cid, nonce, division, row["id"])}


def _verify_signature(public_key_b64: str, message: str, signature_b64: Any) -> None:
    """WebCrypto가 내는 **P1363(r||s 64바이트)** 서명을 검증한다.

    `cryptography`는 DER을 기대하므로 여기서 바꾼다. 형식이 틀리면 예외를 그대로
    올리지 않고 전부 같은 실패로 접는다 — 어디까지 맞았는지 알려 주지 않는다.
    """
    if not isinstance(signature_b64, str) or not signature_b64.strip():
        raise DeviceError("bad_signature", "서명이 없습니다.")
    try:
        raw = base64.b64decode(signature_b64, validate=True)
    except Exception as e:
        raise DeviceError("bad_signature", "서명 형식이 올바르지 않습니다.") from e
    if len(raw) != 64:
        raise DeviceError("bad_signature", "서명 형식이 올바르지 않습니다.")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    if r == 0 or s == 0:
        raise DeviceError("bad_signature", "서명을 검증하지 못했습니다.")
    try:
        spki = base64.b64decode(public_key_b64, validate=True)
        key = serialization.load_der_public_key(spki)
        key.verify(asym_utils.encode_dss_signature(r, s), message.encode(),
                   ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as e:
        raise DeviceError("bad_signature", "서명을 검증하지 못했습니다.") from e
    except Exception as e:
        raise DeviceError("bad_signature", "서명을 검증하지 못했습니다.") from e


async def challenge_redeem(challenge_id: Any, signature_b64: Any) -> dict:
    """서명을 검증하고 **기존 구조의** 수집 토큰을 발급한다.

    challenge는 검증 성공 여부와 무관하게 **한 번만** 소비된다 — 하나의 nonce로
    서명을 계속 시험하지 못하게 한다.
    """
    if not isinstance(challenge_id, str) or not challenge_id.strip():
        raise DeviceError("bad_challenge", "challenge가 없습니다.")
    now = int(time.time())
    db = await get_db()

    # 조건부 UPDATE로 **먼저 소비**한다. 동시에 두 요청이 와도 하나만 통과한다.
    cur = await db.execute(
        "UPDATE piku_collector_challenges SET used_at=? "
        "WHERE id=? AND used_at=0 AND expires_at>?",
        (now, challenge_id.strip(), now))
    await db.commit()
    if not cur.rowcount:
        raise DeviceError("bad_challenge", "challenge가 만료됐거나 이미 사용됐습니다.")

    cur = await db.execute(
        "SELECT device_id, division, nonce FROM piku_collector_challenges WHERE id=?",
        (challenge_id.strip(),))
    row = await cur.fetchone()
    device_id, division, nonce = row[0], row[1], row[2]

    dev = await _device_row(device_id)
    if not dev or dev["status"] != "active" or not dev["public_key"]:
        raise DeviceError("device_not_active", "폐기됐거나 등록되지 않은 장치입니다.")

    _verify_signature(dev["public_key"],
                      challenge_message(challenge_id.strip(), nonce, division, device_id),
                      signature_b64)

    import singcup_piku_collector as col
    issued = await col.issue_token(division)
    await _touch_seen(device_id)
    _log("token_issued_for_device", device_id=device_id, division=division)
    return {"deviceId": device_id, "fingerprint": dev["fingerprint"], **issued}


async def purge_expired() -> int:
    """만료 challenge 정리. 남겨 둘 이유가 없다."""
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM piku_collector_challenges WHERE expires_at < ?",
        (int(time.time()) - 3600,))
    await db.commit()
    return cur.rowcount or 0


# ── 상태 ────────────────────────────────────────────────────────────────────
async def status() -> dict:
    """Nexadmin 상단 요약. **secret을 담지 않는다.**"""
    devices = await list_devices()
    active = [d for d in devices if d["status"] == "active"]
    return {
        "mode": await get_mode(),
        "modes": list(MODES),
        "deviceCount": len(devices),
        "activeCount": len(active),
        "pendingCount": len([d for d in devices if d["status"] == "pending"]),
        "revokedCount": len([d for d in devices if d["status"] == "revoked"]),
        "pairingTtlSeconds": PAIRING_TTL_SECONDS,
        "challengeTtlSeconds": CHALLENGE_TTL_SECONDS,
        # AUTO-2가 채운다. 지금은 "아직 없음"을 정직하게 밝힌다.
        "schedulerImplemented": False,
        "autoPublishImplemented": False,
    }


# ── 테스트 전용 ─────────────────────────────────────────────────────────────
# 실제 초를 기다리지 않고 만료 경로를 확인하기 위한 것이다. 운영 코드가 부르지 않는다.
async def _expire_pairing_for_tests(device_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE piku_collector_devices SET pairing_expires_at=? WHERE id=?",
        (int(time.time()) - 1, device_id))
    await db.commit()


async def _expire_challenge_for_tests(challenge_id: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE piku_collector_challenges SET expires_at=? WHERE id=?",
        (int(time.time()) - 1, challenge_id))
    await db.commit()
