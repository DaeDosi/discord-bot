"""AUTO-2 — challenge **속도 제한**과 자동 수집 **회차 기록**.

## 이 모듈이 더하는 것

AUTO-1이 남긴 잔여 위험 하나가 "challenge 발급에 속도 제한이 없다"였다. 장치가
0대일 때는 아무것도 나오지 않지만, 장치가 등록된 뒤에는 지문을 아는 사람이
challenge 행을 무한히 늘릴 수 있다(토큰은 개인키가 없으면 못 받는다). 여기서 막는다.

핵심 설계 두 가지:

1. **제한은 challenge를 만들기 *전에* 건다.** 막힌 요청이 challenge 행을 남기면
   제한이 곧 저장소 증가가 되어 막는 의미가 없다. 그래서 시도(`attempts`)를
   challenge와 **따로** 적고, 통과한 요청만 `challenge_issue`로 넘긴다.
2. **제한 상태는 DB에 있다.** 메모리 카운터는 서비스 재시작 한 번으로 초기화된다 —
   Railway는 배포마다 재시작하므로 그건 사실상 제한이 없는 것과 같다.

한도는 "정상 1시간 수집(부문 3개)과 사람이 누르는 재시도를 막지 않는" 선으로 잡았다.

## 회차 기록

AUTO-2의 종착점은 **draft**다. 공개(Publish)는 여기에 없다. 그래서 한 부문이
실패해도 성공한 draft는 남길 수 있는데, 그 회차를 "세 부문 완료"로 표시하면
운영자가 공개해도 되는 줄 안다. **전체 성공 / 부분 성공 / 실패를 구분한다.**

`AUTO_PUBLISH`는 이 단계에서 **준비되지 않음**이다. 모드 값으로는 존재해도
`publish_allowed()`가 항상 False를 돌려준다 — AUTO-3이 게이트를 붙일 때까지.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

import singcup_piku_devices as devices

from database import get_db

log = logging.getLogger(__name__)

DIVISIONS = devices.DIVISIONS

#: AUTO-3이 자동 공개를 구현하기 전까지 **항상 False**. 값을 바꿔 켜지 말 것 —
#: 안전 게이트(64/64/32·전원 매핑 확정·변동량 임계값)가 아직 없다.
AUTO_PUBLISH_READY = False

# ── 속도 제한 한도 ──────────────────────────────────────────────────────────
#
# 정상 사용량: 1시간에 부문 3개 = challenge 3회. 사람이 실패를 보고 몇 번 다시
# 누르는 것까지 감안해 burst를 넉넉히 둔다. 자동화가 폭주해도 시간당 한도에서 걸린다.
BURST_SECONDS = int(os.getenv("PIKU_CHALLENGE_BURST_SECONDS", "60"))
BURST_LIMIT = int(os.getenv("PIKU_CHALLENGE_BURST_LIMIT", "8"))
WINDOW_SECONDS = int(os.getenv("PIKU_CHALLENGE_WINDOW_SECONDS", "3600"))
WINDOW_LIMIT = int(os.getenv("PIKU_CHALLENGE_WINDOW_LIMIT", "40"))
#: IP는 **보조** 방어다. 장치를 바꿔 가며 두드리는 것을 막는다.
IP_LIMIT = int(os.getenv("PIKU_CHALLENGE_IP_LIMIT", "120"))

#: 오래된 시도 기록을 지우는 기준. 카운트에 쓰이지 않는 것은 남길 이유가 없다.
_ATTEMPT_TTL = max(WINDOW_SECONDS, 3600) * 2

def _log(event: str, **fields: Any) -> None:
    """구조화 로그. **nonce·토큰·IP 원문·지문을 넣지 않는다.**"""
    try:
        log.info("piku_sched %s", json.dumps({"event": event, **fields},
                                             ensure_ascii=False, default=str))
    except Exception:
        log.info("piku_sched %s", event)


def _ip_hash(value: Any) -> str:
    """속도 제한 버킷 키를 저장 가능한 형태로 만든다.

    **신뢰 프록시 판정은 여기서 하지 않는다.** 그건 공통 모듈
    `client_ip.resolve()`가 `TRUSTED_PROXY_HOPS`로 이미 정해 두었고, 라우터가 그
    결과(`["id"]`, 이미 날짜 회전 해시)를 넘긴다. 같은 로직을 여기서 다시 쓰면
    두 곳이 갈라져 어느 쪽이 진실인지 알 수 없게 된다.

    그런데도 한 번 더 해시하는 이유는 **호출부가 실수로 원문 IP를 넘겨도 저장소에는
    남지 않게** 하기 위해서다. 이미 해시된 값을 다시 해시해도 카운트에는 지장이 없다
    (같은 입력 → 같은 출력이면 충분하다)."""
    raw = (value or "").strip() if isinstance(value, str) else ""
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


async def _count(where: str, params: tuple, since: int) -> int:
    db = await get_db()
    cur = await db.execute(
        f"SELECT count(*) FROM piku_challenge_attempts WHERE {where} AND created_at >= ?",
        (*params, since))
    return (await cur.fetchone())[0]


async def _record_attempt(device_id: int, ip_hash: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO piku_challenge_attempts (device_id, ip_hash, created_at)"
        " VALUES (?,?,?)", (device_id, ip_hash, int(time.time())))
    await db.commit()


async def purge_attempts() -> int:
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM piku_challenge_attempts WHERE created_at < ?",
        (int(time.time()) - _ATTEMPT_TTL,))
    await db.commit()
    return cur.rowcount or 0


async def guarded_challenge(device_id: Any, division: Any, *, ip: str = "",
                            automation: bool = False) -> dict:
    """속도 제한을 거친 challenge 발급.

    순서가 중요하다:
      1. **장치 상태를 먼저 본다.** 폐기·대기 장치는 제한 카운터를 소모하지 않는다 —
         그러지 않으면 남의 지문으로 정상 장치의 한도를 태울 수 있다.
      2. 모드 게이트(자동 실행은 MANUAL에서 거절).
      3. **속도 제한.** 여기서 막히면 challenge 행이 생기지 않는다.
      4. 통과한 것만 `devices.challenge_issue`로 넘긴다.
    """
    if division not in DIVISIONS:
        raise devices.DeviceError("bad_division", "알 수 없는 부문입니다.")

    row = await devices._device_row(device_id)
    if not row:
        raise devices.DeviceError("no_device", "장치를 찾을 수 없습니다.")
    if row["status"] != "active":
        raise devices.DeviceError("device_not_active",
                                  "등록이 끝나지 않았거나 폐기된 장치입니다.")

    if automation and await devices.get_mode() == "MANUAL":
        raise devices.DeviceError(
            "automation_off",
            "자동 수집이 꺼져 있습니다(MANUAL). Nexadmin에서 모드를 바꿔 주세요.")

    now = int(time.time())
    ip_hash = _ip_hash(ip)

    burst = await _count("device_id = ?", (row["id"],), now - BURST_SECONDS)
    window = await _count("device_id = ?", (row["id"],), now - WINDOW_SECONDS)
    ip_n = (await _count("ip_hash = ?", (ip_hash,), now - WINDOW_SECONDS)
            if ip_hash else 0)

    if burst >= BURST_LIMIT or window >= WINDOW_LIMIT or ip_n >= IP_LIMIT:
        # 시도 자체는 기록한다 — 두드린 만큼 창이 늦게 열려야 한다.
        await _record_attempt(row["id"], ip_hash)
        _log("rate_limited", device_id=row["id"], burst=burst, window=window)
        # **응답에 nonce·토큰·IP·내부 카운터를 담지 않는다.**
        raise devices.DeviceError(
            "rate_limited",
            "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.")

    await _record_attempt(row["id"], ip_hash)
    return await devices.challenge_issue(row["id"], division, automation=automation)


async def device_state(fingerprint: Any) -> dict:
    """확장이 회차 시작 전에 부르는 **가벼운 상태 조회**.

    challenge를 발급하지 않는다 — 상태를 보려고 challenge를 하나 태우면 시간당
    발급이 3회가 아니라 4회가 되고, 속도 제한 계산이 흐려진다. 여기서 돌려주는 것은
    전부 **비밀이 아니다**(장치는 자기 지문을 이미 알고, 모드는 운영자가 정한 값이다).
    """
    row = await devices.device_by_fingerprint(fingerprint)
    mode = await devices.get_mode()
    return {
        "deviceActive": bool(row and row["status"] == "active"),
        "deviceStatus": (row or {}).get("status", "none"),
        "mode": mode,
        # 확장이 "자동 공개가 곧 켜질 것"으로 오해하지 않게 명시한다.
        "autoPublishReady": AUTO_PUBLISH_READY,
        "periodMinutes": 60,
    }


async def publish_allowed() -> bool:
    """자동 공개 허용 여부. **AUTO-2에서는 항상 False.**

    모드가 `AUTO_PUBLISH`여도 False다 — 안전 게이트가 아직 없기 때문이다.
    AUTO-3이 게이트를 붙이면서 이 함수를 바꾼다.
    """
    return bool(AUTO_PUBLISH_READY) and await devices.get_mode() == "AUTO_PUBLISH"


# ── 회차 기록 ───────────────────────────────────────────────────────────────
_COLS = {"female_solo": "female", "male_solo": "male", "groups": "groups"}


async def run_start(device_id: Any, *, trigger: str = "alarm") -> int:
    if trigger not in ("alarm", "manual"):
        raise devices.DeviceError("bad_trigger", "알 수 없는 실행 종류입니다.")
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO piku_auto_runs (device_id, trigger, started_at, outcome)"
        " VALUES (?,?,?, 'running')",
        (int(device_id or 0), trigger, int(time.time())))
    await db.commit()
    _log("run_started", run_id=cur.lastrowid, trigger=trigger)
    return cur.lastrowid


async def run_division(run_id: int, division: str, *, ok: bool,
                       rows: int = 0, kind: str = "") -> None:
    if division not in DIVISIONS:
        raise devices.DeviceError("bad_division", "알 수 없는 부문입니다.")
    c = _COLS[division]
    db = await get_db()
    await db.execute(
        f"UPDATE piku_auto_runs SET {c}_ok=?, {c}_kind=?, {c}_rows=? WHERE id=?",
        (1 if ok else 0, str(kind or "")[:40], int(rows or 0), int(run_id)))
    await db.commit()


def _outcome(row: dict) -> str:
    oks = sum(1 for d in DIVISIONS if row[f"{_COLS[d]}_ok"])
    if oks == len(DIVISIONS):
        return "success"
    return "failed" if oks == 0 else "partial"


def _shape(row: Any) -> dict:
    r = dict(row)
    return {
        "id": r["id"], "deviceId": r["device_id"], "trigger": r["trigger"],
        "startedAt": r["started_at"], "finishedAt": r["finished_at"],
        "outcome": r["outcome"],
        "divisions": {d: {
            "ok": bool(r[f"{_COLS[d]}_ok"]),
            "kind": r[f"{_COLS[d]}_kind"] or "",
            "rows": r[f"{_COLS[d]}_rows"],
        } for d in DIVISIONS},
    }


async def run_finish(run_id: int) -> dict:
    """회차 마감. **부분 성공을 성공으로 뭉치지 않는다.**"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM piku_auto_runs WHERE id=?", (int(run_id),))
    row = await cur.fetchone()
    if not row:
        raise devices.DeviceError("no_run", "회차를 찾을 수 없습니다.")
    outcome = _outcome(dict(row))
    await db.execute(
        "UPDATE piku_auto_runs SET finished_at=?, outcome=? WHERE id=?",
        (int(time.time()), outcome, int(run_id)))
    await db.commit()
    cur = await db.execute("SELECT * FROM piku_auto_runs WHERE id=?", (int(run_id),))
    out = _shape(await cur.fetchone())
    _log("run_finished", run_id=run_id, outcome=outcome)
    return out


async def recent_runs(limit: int = 20) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM piku_auto_runs ORDER BY id DESC LIMIT ?",
        (max(1, min(100, int(limit))),))
    return [_shape(r) for r in await cur.fetchall()]


async def status() -> dict:
    """Nexadmin 자동화 패널용 요약. **secret을 담지 않는다.**"""
    devs = await devices.list_devices()
    active = [d for d in devs if d["status"] == "active"]
    runs = await recent_runs(limit=10)
    return {
        "mode": await devices.get_mode(),
        "activeDeviceCount": len(active),
        "activeDevices": [{"id": d["id"], "name": d["name"],
                           "fingerprint": d["fingerprint"],
                           "lastSeenAt": d["lastSeenAt"]} for d in active],
        # AUTO-3 전까지 이 값은 False로 고정이다. 화면이 이걸 보고 옵션을 막는다.
        "autoPublishReady": AUTO_PUBLISH_READY,
        "periodMinutes": 60,
        "burstLimit": BURST_LIMIT,
        "windowLimit": WINDOW_LIMIT,
        "lastRun": runs[0] if runs else None,
        "recentRuns": runs,
    }


# ── 테스트 전용 ─────────────────────────────────────────────────────────────
async def _shift_attempts_for_tests(device_id: int, *, seconds: int) -> None:
    """시도 기록을 과거로 민다 — 실제 초를 기다리지 않고 창을 넘기기 위한 것."""
    db = await get_db()
    await db.execute(
        "UPDATE piku_challenge_attempts SET created_at = created_at - ? WHERE device_id=?",
        (int(seconds), int(device_id)))
    await db.commit()


def _reset_process_state_for_tests() -> None:
    """프로세스 재시작 흉내. **여기서 지울 메모리 상태가 없다는 것이 요점이다** —
    제한은 전부 DB에 있으므로 재시작해도 그대로다."""
    return None
