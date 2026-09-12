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

import singcup_piku_campaigns as camps
import singcup_piku_devices as devices

from database import get_db

log = logging.getLogger(__name__)

DIVISIONS = devices.DIVISIONS
SOURCES = devices.SOURCES

#: 한 장치가 한 시간에 보고할 수 있는 회차 수. 정상은 1~2회(alarm + 수동 테스트).
RUN_REPORT_WINDOW_LIMIT = int(os.getenv("PIKU_RUN_REPORT_WINDOW_LIMIT", "12"))

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
                            automation: bool = False, protocol: int = 1) -> dict:
    """속도 제한을 거친 challenge 발급.

    순서가 중요하다:
      1. **장치 상태를 먼저 본다.** 폐기·대기 장치는 제한 카운터를 소모하지 않는다 —
         그러지 않으면 남의 지문으로 정상 장치의 한도를 태울 수 있다.
      2. 모드 게이트(자동 실행은 MANUAL에서 거절).
      3. **속도 제한.** 여기서 막히면 challenge 행이 생기지 않는다.
      4. 통과한 것만 `devices.challenge_issue`로 넘긴다.
    """
    if division not in SOURCES:
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
    return await devices.challenge_issue(row["id"], division, automation=automation,
                                         protocol=protocol)


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
        # **활성 collection plan.** 확장은 여기 적힌 source만 찾는다 — 예선 탭이
        # 없다는 이유로 본선 회차가 실패하면 안 된다(SINGCUP-FINAL-1).
        "plan": camps.plan(),
    }


async def publish_allowed() -> bool:
    """자동 공개 허용 여부. **AUTO-2에서는 항상 False.**

    모드가 `AUTO_PUBLISH`여도 False다 — 안전 게이트가 아직 없기 때문이다.
    AUTO-3이 게이트를 붙이면서 이 함수를 바꾼다.
    """
    return bool(AUTO_PUBLISH_READY) and await devices.get_mode() == "AUTO_PUBLISH"


# ── 회차 기록 ───────────────────────────────────────────────────────────────
#
# AUTO-2 시점에는 `run_start/run_division/run_finish`를 부르는 경로가 **없었다** —
# 확장의 `report()`가 no-op이어서 `piku_auto_runs`는 운영에서 영원히 비어 있었고,
# Nexadmin은 "회차가 돌았는지"를 알 길이 없었다(SINGCUP-FINAL-1 진단 결과 3번).
# 이제 확장이 회차가 끝날 때 `report_run()`(장치 지문 인증)으로 결과를 보낸다.
#
# 부문 결과는 예선 3개 컬럼 대신 `piku_auto_run_sources` 행으로 둔다(본선은 source가
# 하나라 컬럼에 자리가 없다). 예선 컬럼은 예선 source일 때 함께 채워 호환을 지킨다.
_COLS = {"female_solo": "female", "male_solo": "male", "groups": "groups"}
_TRIGGERS = ("alarm", "manual")
#: 확장이 보고할 수 있는 실패 종류 — **이 목록 밖 문자열은 `other`로 접는다.**
#: HTML·쿠키·토큰이 "종류" 자리에 실려 오는 것을 막는다.
RUN_KINDS = frozenset({
    "sent", "unchanged", "no_tab", "ambiguous_tab", "loading", "wrong_page",
    "source_mismatch", "title_mismatch", "row_count", "rank_gap", "partial",
    "not_rendered", "blocked", "parse_failed", "aborted", "token_failed",
    "ingest_failed", "campaign_mismatch", "no_plan", "pager_failed",
})


def _kind(value: Any) -> str:
    k = str(value or "")[:40]
    return k if k in RUN_KINDS else ("" if not k else "other")


async def run_start(device_id: Any, *, trigger: str = "alarm",
                    campaign: str = "", scheduled_at: int = 0,
                    started_at: int | None = None) -> int:
    if trigger not in _TRIGGERS:
        raise devices.DeviceError("bad_trigger", "알 수 없는 실행 종류입니다.")
    db = await get_db()
    cur = await db.execute(
        "INSERT INTO piku_auto_runs (device_id, trigger, started_at, outcome,"
        " campaign, scheduled_at) VALUES (?,?,?, 'running', ?, ?)",
        (int(device_id or 0), trigger, int(started_at or time.time()),
         str(campaign or "")[:40], int(scheduled_at or 0)))
    await db.commit()
    _log("run_started", run_id=cur.lastrowid, trigger=trigger, campaign=campaign)
    return cur.lastrowid


async def run_division(run_id: int, division: str, *, ok: bool,
                       rows: int = 0, kind: str = "") -> None:
    """한 source의 결과. 예선 source는 기존 컬럼에도 함께 적는다."""
    if division not in SOURCES:
        raise devices.DeviceError("bad_division", "알 수 없는 부문입니다.")
    db = await get_db()
    await db.execute(
        """INSERT INTO piku_auto_run_sources (run_id, campaign, source, ok, kind, row_count)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(run_id, campaign, source) DO UPDATE SET
               ok=excluded.ok, kind=excluded.kind, row_count=excluded.row_count""",
        (int(run_id), camps.campaign_of(division), division, 1 if ok else 0,
         _kind(kind), int(rows or 0)))
    if division in _COLS:
        c = _COLS[division]
        await db.execute(
            f"UPDATE piku_auto_runs SET {c}_ok=?, {c}_kind=?, {c}_rows=? WHERE id=?",
            (1 if ok else 0, _kind(kind), int(rows or 0), int(run_id)))
    await db.commit()


async def _source_rows(run_id: int) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT campaign, source, ok, kind, row_count FROM piku_auto_run_sources"
        " WHERE run_id=? ORDER BY source", (int(run_id),))
    return [dict(r) for r in await cur.fetchall()]


def _outcome_of(sources: list[dict], expected: int) -> str:
    """결과 요약 — 부분 성공을 성공으로 뭉치지 않는다.

    `expected`는 plan이 요구한 source 수다. 하나라도 빠지면 success가 아니다.
    전부 `unchanged`(같은 표)면 `unchanged`로 따로 표시한다 — "성공했는데 draft가
    안 바뀐" 상태를 성공과 구분해야 운영자가 새 표가 없었음을 안다.
    """
    if expected <= 0:
        return "failed"
    oks = [r for r in sources if r["ok"]]
    if len(oks) == expected and len(sources) >= expected:
        return "unchanged" if all(r["kind"] == "unchanged" for r in oks) else "success"
    return "failed" if not oks else "partial"


def _shape(row: Any, sources: list[dict]) -> dict:
    r = dict(row)
    src = {x["source"]: {"campaign": x["campaign"], "ok": bool(x["ok"]),
                         "kind": x["kind"] or "", "rows": x["row_count"]}
           for x in sources}
    # 예선 회차(구 스키마)에는 source 행이 없을 수 있다 — 컬럼에서 복원한다.
    if not src:
        for d, c in _COLS.items():
            if r.get(f"{c}_ok") or r.get(f"{c}_kind"):
                src[d] = {"campaign": "qualifier", "ok": bool(r[f"{c}_ok"]),
                          "kind": r[f"{c}_kind"] or "", "rows": r[f"{c}_rows"]}
    return {
        "id": r["id"], "deviceId": r["device_id"], "trigger": r["trigger"],
        "campaign": r.get("campaign") or "",
        "scheduledAt": r.get("scheduled_at") or 0,
        "startedAt": r["started_at"], "finishedAt": r["finished_at"],
        "outcome": r["outcome"],
        "sources": src,
        # 예전 화면 호환 — 예선 세 부문 키는 그대로 둔다.
        "divisions": {d: src.get(d, {"ok": False, "kind": "", "rows": 0})
                      for d in DIVISIONS},
    }


async def run_finish(run_id: int, *, expected_sources: int | None = None,
                     finished_at: int | None = None) -> dict:
    """회차 마감. **부분 성공을 성공으로 뭉치지 않는다.**"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM piku_auto_runs WHERE id=?", (int(run_id),))
    row = await cur.fetchone()
    if not row:
        raise devices.DeviceError("no_run", "회차를 찾을 수 없습니다.")
    sources = await _source_rows(run_id)
    if expected_sources is None:
        camp = dict(row).get("campaign") or ""
        expected_sources = (len(camps.sources_of(camp)) if camp in camps.CAMPAIGNS
                            else len(DIVISIONS))
    outcome = _outcome_of(sources, expected_sources)
    await db.execute(
        "UPDATE piku_auto_runs SET finished_at=?, outcome=? WHERE id=?",
        (int(finished_at or time.time()), outcome, int(run_id)))
    await db.commit()
    cur = await db.execute("SELECT * FROM piku_auto_runs WHERE id=?", (int(run_id),))
    out = _shape(await cur.fetchone(), sources)
    _log("run_finished", run_id=run_id, outcome=outcome)
    return out


async def recent_runs(limit: int = 20) -> list[dict]:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM piku_auto_runs ORDER BY id DESC LIMIT ?",
        (max(1, min(100, int(limit))),))
    rows = await cur.fetchall()
    return [_shape(r, await _source_rows(r["id"])) for r in rows]


def _ts(v: Any, now: int) -> int:
    """확장이 보낸 시각(ms 또는 s). 미래·터무니없는 값은 0(= 서버 시각 사용)."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    if n > 10**12:
        n //= 1000
    return n if 0 < n <= now + 300 else 0


async def report_run(fingerprint: Any, body: Any) -> dict:
    """확장이 회차가 끝날 때 보내는 결과 보고. **데이터가 아니라 종류만** 받는다.

    인증은 장치 지문(active 장치여야 함)이다 — 이 경로는 draft를 만들지도 공개하지도
    않으며, 남길 수 있는 것은 "언제 어떤 종류로 끝났는가"뿐이라 서명까지 요구하지
    않는다. 대신 장치당 시간당 횟수를 제한해 지문을 아는 사람이 이력을 채우지 못하게 한다.
    """
    row = await devices.device_by_fingerprint(fingerprint)
    if not row or row["status"] != "active":
        raise devices.DeviceError("device_not_active", "등록되지 않았거나 폐기된 장치입니다.")
    if not isinstance(body, dict):
        raise devices.DeviceError("bad_report", "보고 형식이 올바르지 않습니다.")
    trigger = body.get("trigger")
    if trigger not in _TRIGGERS:
        raise devices.DeviceError("bad_trigger", "알 수 없는 실행 종류입니다.")
    campaign = body.get("campaign")
    if campaign not in camps.CAMPAIGNS:
        raise devices.DeviceError("bad_campaign", "알 수 없는 단계입니다.")
    sources = body.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise devices.DeviceError("bad_report", "source 결과가 없습니다.")
    now = int(time.time())
    db = await get_db()
    cur = await db.execute(
        "SELECT count(*) FROM piku_auto_runs WHERE device_id=? AND started_at>=?",
        (row["id"], now - 3600))
    if (await cur.fetchone())[0] >= RUN_REPORT_WINDOW_LIMIT:
        raise devices.DeviceError("rate_limited", "보고가 너무 잦습니다.")

    run_id = await run_start(row["id"], trigger=trigger, campaign=campaign,
                             scheduled_at=_ts(body.get("scheduledAt"), now),
                             started_at=_ts(body.get("startedAt"), now) or now)
    plan_sources = camps.sources_of(campaign)
    for key, res in sources.items():
        if key not in SOURCES or camps.campaign_of(key) != campaign:
            continue
        if not isinstance(res, dict):
            continue
        rows = res.get("rows")
        ok_rows = int(rows) if isinstance(rows, int) and 0 <= rows <= 1000 else 0
        await run_division(run_id, key, ok=bool(res.get("ok")), rows=ok_rows,
                           kind=res.get("kind"))
    out = await run_finish(run_id, expected_sources=len(plan_sources),
                           finished_at=_ts(body.get("finishedAt"), now) or now)
    if out["outcome"] in ("success", "unchanged"):
        await devices.mark_success(row["id"])
    else:
        bad = [v["kind"] for v in out["sources"].values() if not v["ok"]]
        await devices.mark_failure(row["id"], (bad or ["failed"])[0])
    return out


async def status() -> dict:
    """Nexadmin 자동화 패널용 요약. **secret을 담지 않는다.**"""
    devs = await devices.list_devices()
    active = [d for d in devs if d["status"] == "active"]
    runs = await recent_runs(limit=10)
    return {
        "mode": await devices.get_mode(),
        "plan": camps.plan(),
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
