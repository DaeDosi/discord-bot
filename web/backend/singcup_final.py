"""싱드컵 **비공식 인기점수 랭킹**의 최종 확정본.

이벤트는 `SINGCUP_END_AT`(기본 2026-08-09 23:59:59 KST)에 끝났는데, 순위·하트·
급상승은 그 뒤로도 계속 재계산됐다. 이미 끝난 대회의 결과가 화면을 볼 때마다
달라졌고, "오후 05:36 계산 · 비교 36분" 같은 문구가 아직 집계 중이라는 인상을 줬다.

여기서 하는 일은 **표시 결과를 한 번 얼리는 것 하나뿐**이다.

── 얼리지 않는 것 (매우 중요) ──────────────────────────────────────────────
수집기·sweep·AWS KR poller·라운지 탐색은 **전혀 건드리지 않는다.** 이 모듈은
`singcup_collector`의 게이트(`registration_open` / `metrics_refresh_open` /
`ranking_refresh_open` / `snapshot_refresh_open`)를 읽지도, 바꾸지도 않는다.
클립 지표는 계속 갱신되고, **공식 예선 참가자 화면(`?view=official`)은 그 최신값을
그대로 쓴다.** 얼리는 대상은 `?view=ranking` 화면이 받는 응답 하나뿐이다.

수집을 멈추는 방식으로 이 요구를 구현하면 참가자 화면의 하트·조회수까지 굳는다 —
그건 다른 기능을 망가뜨리는 것이지 요구를 만족시키는 게 아니다.

── 왜 payload 통째로 저장하는가 ────────────────────────────────────────────
`singcup_final_standings`(행 단위 최종 성적)는 이미 있지만 순위·점수만 담는다.
화면은 대표 클립·썸네일·급상승 목록·요약·기준 시각까지 필요해서, 그 테이블만으로는
응답을 다시 조립해야 하고 조립 과정이 다시 최신 데이터를 참조하게 된다. 그러면
"얼렸다"는 계약이 조용히 깨진다.

그래서 확정 시점에 **응답 dict 하나를 그대로** 직렬화해 한 행에 넣는다:

  · 원자성   — 한 행 UPSERT. 부분 저장이 구조적으로 불가능하다.
  · 재시작   — 프로세스가 죽어도 DB에 남아 있어 같은 bytes가 나온다.
  · 캐시 TTL — 메모리 캐시가 비어도 DB에서 같은 값을 다시 읽는다.
  · ETag     — 지문을 함께 저장한다. 다시 계산하지 않으므로 영원히 같다.
  · sweep    — 원본 clip 지표가 변해도 이 payload는 참조하지 않는다.

── 확정본은 어디서 오는가 ──────────────────────────────────────────────────
값을 새로 만들어 내지 않는다. 우선순위는 다음과 같다.

  1) 이미 저장된 확정본이 있으면 그것 (한 번 확정되면 다시 만들지 않는다)
  2) 없으면 **현재 정상 랭킹 계산 결과를 한 번** 확정본으로 굳힌다
     (`_load_main_uncached` — 평소 화면이 받던 것과 같은 계산 경로다)

2)는 명시적 경로이며 `ensure_finalized()`를 통해서만 실행된다. 운영에서는
**앱 시작 시 이벤트가 ENDED이고 확정본이 아직 없을 때 딱 한 번** 돌아간다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time

from singcup_clips import EVENT_ID, _load_main_uncached
from singcup_collector import event_status

from database import get_db

log = logging.getLogger(__name__)

# 확정본에 담는 참가자 수. 화면이 검색·정렬을 이 응답 안에서만 하므로 전원을 담는다
# (줄이면 하위권이 통째로 사라진다 — `/api/singcup/main`과 같은 이유다).
FINAL_LIMIT = int(os.getenv("SINGCUP_FINAL_RANKING_LIMIT", "2000"))

# 저장 재시도 — 봇 프로세스가 같은 SQLite 파일을 쓰고 있으면 잠금에 걸릴 수 있다.
# 짧고 유한하게만 재시도한다(무한 재시도·긴 startup block 금지).
WRITE_ATTEMPTS = 3
WRITE_BACKOFF_SECONDS = 0.25

# ── feature flag 계약 ───────────────────────────────────────────────────────
#
#   SINGCUP_RANKING_FREEZE_ENABLED=true   → 이벤트 종료 시 확정본 사용, 갱신 중지
#   SINGCUP_RANKING_FREEZE_ENABLED=false  → 기존 실시간 비공식 랭킹 사용
#   미설정                                 → **true**(기본 동결)
#
# 기본을 true로 두는 이유: 확정된 제품 요구가 "갱신 중지"다. 기본이 false면 운영에
# 변수를 새로 넣어야만 요구가 충족되는데, 그 한 단계를 잊으면 요구가 조용히 무시된다.
#
# 알 수 없는 값은 **fail-closed**(동결)로 처리하고 경고를 남긴다. 오타 하나로
# "멈춰 달라"는 요구가 풀리는 것보다, 멈춘 채로 경고를 보는 편이 낫다.
_TRUE = {"true", "1", "yes", "on"}
_FALSE = {"false", "0", "no", "off"}
_FREEZE_RAW = os.getenv("SINGCUP_RANKING_FREEZE_ENABLED")

# ── 자동 회복(확정 재시도) ─────────────────────────────────────────────────
#
# startup에서 3회 재시도가 모두 실패하면(Railway SQLite는 실제로 일시적 잠금이 난다)
# 예전에는 **다음 재시작까지 영영 503**이었다. 그래서 조회 경로에서 재시도를 건다.
#
# background 루프가 아니라 **요청이 촉발하는 singleflight**를 고른 이유:
#  · 이벤트는 이미 끝났다. 아무도 안 보는데 루프가 계속 도는 것은 순수한 낭비다.
#  · 조회 경로는 "확정본이 없다"는 사실을 이미 알고 있다 — 별도 상태 감시가 필요 없다.
#  · 루프는 프로세스마다 하나씩 돌아 replica 수만큼 write가 늘지만, 요청 촉발은
#    cooldown이 곧 상한이 된다.
# 대신 요청이 계산을 기다리게 하지 않는다 — 확정 계산은 참가자 전원을 조인하는
# 무거운 쿼리라 공개 GET을 붙잡으면 안 된다. task를 하나만 띄우고 그 요청은
# 503을 그대로 반환한다. 다음 요청이 200을 받는다.
#
# cooldown은 `Retry-After`와 같은 값이다 — 클라이언트가 그 시간 뒤에 다시 오는데
# 그때 또 cooldown에 걸리면 회복이 영영 미뤄진다.
FINALIZE_COOLDOWN_SECONDS = float(os.getenv("SINGCUP_FINAL_RETRY_COOLDOWN", "30"))
RETRY_AFTER_SECONDS = int(FINALIZE_COOLDOWN_SECONDS)

# 확정 계산·저장을 감싸는 락. 캐시 읽기용 락과 **분리**한다 — 하나로 쓰면 무거운
# 확정 계산이 도는 동안 모든 조회가 막힌다.
_finalize_lock = asyncio.Lock()
_finalize_task: "asyncio.Task | None" = None
_last_attempt_at: float | None = None      # time.monotonic()
# cooldown 스킵은 **창마다 한 번만** 남긴다 — 요청마다 남기면 로그가 폭증한다.
_cooldown_logged_for: float | None = None

_cache_lock = asyncio.Lock()
# 확정본은 변하지 않으므로 TTL이 필요 없다. 한 번 읽으면 프로세스가 살아 있는 동안 유지.
_cache: dict | None = None
_warned_bad_flag = False


def _log(event: str, **fields) -> None:
    """확정 시도 관측 로그.

    payload·스트리머 정보·토큰·SQL 원문·DB 경로는 **절대 싣지 않는다.** 여기 실리는
    것은 이벤트 이름과 숫자, 그리고 예외의 *타입 이름*뿐이다(메시지에는 파일 경로나
    쿼리가 섞여 들어올 수 있어 타입만 남긴다).
    """
    safe = {"event": event, "eventId": EVENT_ID}
    for k in ("attempt", "outcome", "cooldownSeconds", "durationMs", "errorType", "bytes"):
        if k in fields:
            safe[k] = fields[k]
    log.info("[singcup-final] %s", json.dumps(safe, ensure_ascii=False, sort_keys=True))


def freeze_enabled() -> bool:
    global _warned_bad_flag
    if _FREEZE_RAW is None:
        return True                        # 미설정 = 기본 동결
    v = _FREEZE_RAW.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    if not _warned_bad_flag:
        _warned_bad_flag = True
        log.warning(
            "[singcup-final] SINGCUP_RANKING_FREEZE_ENABLED 값을 해석할 수 없습니다(%r). "
            "안전한 쪽인 '동결'로 처리합니다. true/1/yes/on 또는 false/0/no/off를 쓰세요.",
            _FREEZE_RAW)
    return True                            # fail-closed


def ranking_frozen(now=None) -> bool:
    """비공식 인기점수 랭킹을 확정본으로 보여줄 것인가.

    `singcup_collector`의 게이트와 **별개**다. 저쪽은 "수집·계산을 계속 할 것인가",
    이쪽은 "화면에 무엇을 보여줄 것인가"다. 둘을 하나로 합치지 말 것 — 합치면
    수집을 멈추게 되고, 최신 지표를 쓰는 공식 참가자 화면이 함께 굳는다.
    """
    return freeze_enabled() and event_status(now) == "ENDED"


def _build(data: dict) -> tuple[str, str]:
    """응답 dict → (직렬화 문자열, ETag).

    starlette의 JSONResponse와 같은 옵션으로 직렬화한다 — 옵션이 다르면 같은
    데이터가 다른 bytes가 돼 Content-Length와 실제 응답이 어긋난다.
    """
    text = json.dumps(data, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return text, f'W/"{digest}"'


# 확정본에 **담지 않는** 최상위 키. 전부 "지금 운영이 어떤 상태인가"이지
# "랭킹 결과가 무엇인가"가 아니다. 영구 보존하면 과거 상태가 현재처럼 보인다:
#   · live      — 라이브 신선도. 얼리면 방송이 끝나도 계속 최신인 척한다.
#   · collector — 수집기 건강/stale/마지막 성공 시각. 얼리면 장애 안내가 거짓이 된다.
_RUNTIME_KEYS = ("live", "collector")

# 스트리머·급상승 카드에서 빼는 키 — 현재 방송 여부는 랭킹 결과가 아니다.
# 얼린 `isLive=true`를 그대로 그리면 **방송을 끝낸 사람이 영원히 LIVE로 남는다.**
_RUNTIME_ENTRY_KEYS = ("isLive", "live", "liveTitle")

# 종료된 이벤트에서 더 이상 변하지 않는 필드만 남긴다.
_EVENT_KEYS = ("id", "startAt", "endAt", "status")


def _strip_runtime(entries: list | None) -> list:
    if not entries:
        return []
    return [{k: v for k, v in e.items() if k not in _RUNTIME_ENTRY_KEYS} for e in entries]


def _freeze_payload(data: dict, finalized_at: int) -> dict:
    """확정본 payload — **랭킹 결과만** 남긴다.

    "얼린다"는 것은 순위·점수·하트·조회수·대표 클립·급상승과 그 기준 시각을
    고정한다는 뜻이지, 서비스의 현재 상태까지 박제한다는 뜻이 아니다. 둘을 함께
    얼리면 방송이 끝나도 LIVE가 남고, 수집 장애가 나도 '정상'으로 보인다.
    """
    out = {k: v for k, v in data.items() if k not in _RUNTIME_KEYS}

    ev = data.get("event") or {}
    out["event"] = {k: ev[k] for k in _EVENT_KEYS if k in ev}

    out["streamers"] = _strip_runtime(data.get("streamers"))
    out["topHeartMovers1h"] = _strip_runtime(data.get("topHeartMovers1h"))

    # 요약 중 운영 상태에 해당하는 값(현재 라이브 수)은 랭킹 결과가 아니다.
    summary = dict(data.get("summary") or {})
    summary.pop("liveCount", None)
    out["summary"] = summary

    out["rankingFinal"] = True
    out["rankingFinalizedAt"] = finalized_at
    # 확정 이후로는 "지금 계산 중"으로 읽힐 값이 남아 있으면 안 된다.
    out["topHeartMovers1hStale"] = False
    return out


async def _read_row() -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT payload, etag, finalized_at, source FROM singcup_final_ranking "
        "WHERE event_id=?", (EVENT_ID,))).fetchone()
    if not row:
        return None
    return {"body": row["payload"].encode("utf-8"), "etag": row["etag"],
            "finalizedAt": int(row["finalized_at"]), "source": row["source"]}


async def _write_row(text: str, etag: str, finalized_at: int, source: str) -> None:
    """한 행 UPSERT. 여기서 실패하면 아무것도 바뀌지 않는다(부분 저장 없음)."""
    db = await get_db()
    await db.execute(
        """INSERT INTO singcup_final_ranking
               (event_id, payload, etag, finalized_at, source)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(event_id) DO UPDATE SET
               payload=excluded.payload, etag=excluded.etag,
               finalized_at=excluded.finalized_at, source=excluded.source""",
        (EVENT_ID, text, etag, finalized_at, source))
    await db.commit()


async def finalize(*, source: str, force: bool = False) -> dict:
    """현재 정상 랭킹을 확정본으로 한 번 굳힌다.

    이미 확정본이 있으면 **아무것도 하지 않는다** — 확정된 순위가 재실행으로
    바뀌면 그건 얼린 게 아니다. `force`는 운영자가 의도적으로 다시 굳힐 때만.
    """
    t0 = time.perf_counter()
    async with _finalize_lock:
        existing = await _read_row()
        if existing and not force:
            _log("finalize_skipped_existing", outcome="skipped")
            return {"created": False, "reason": "already_finalized",
                    "finalizedAt": existing["finalizedAt"]}
        _log("finalize_attempt", outcome="start")

        # 평소 화면이 받던 것과 **같은 계산 경로**를 쓴다. 별도 공식으로 다시
        # 만들면 확정본이 그동안 보여준 순위와 달라질 수 있다.
        data = await _load_main_uncached(FINAL_LIMIT)

        # 참가자가 한 명도 없으면 확정하지 않는다. 빈 결과를 '최종'으로 박아 두면
        # 되돌릴 수 없고(한 번 확정되면 다시 만들지 않는다), 화면에는 순위가 통째로
        # 사라진 채 '집계 종료'만 남는다. 준비 중 상태로 두고 다음 기동에 다시 시도한다.
        if not (data.get("streamers") or []):
            _log("finalize_empty", outcome="empty",
                 durationMs=int((time.perf_counter() - t0) * 1000))
            return {"created": False, "reason": "empty_result"}

        now = int(time.time())
        text, etag = _build(_freeze_payload(data, now))

        # bounded retry — 다른 프로세스(봇)가 같은 SQLite 파일을 쓰고 있으면 잠금에
        # 걸릴 수 있다. 무한 재시도나 긴 startup block은 두지 않는다.
        last: Exception | None = None
        for attempt in range(WRITE_ATTEMPTS):
            try:
                await _write_row(text, etag, now, source)
                last = None
                break
            except Exception as exc:                    # noqa: BLE001 — 아래에서 다시 던진다
                last = exc
                if attempt < WRITE_ATTEMPTS - 1:
                    await asyncio.sleep(WRITE_BACKOFF_SECONDS * (attempt + 1))
        if last is not None:
            # 예외 **타입 이름만** 남긴다 — 메시지에는 DB 경로나 SQL이 섞일 수 있다.
            _log("finalize_failed", outcome="failed", attempt=WRITE_ATTEMPTS,
                 errorType=type(last).__name__,
                 durationMs=int((time.perf_counter() - t0) * 1000))
            raise last

        global _cache
        _cache = None                      # 다음 읽기에서 DB의 확정본을 싣는다
        _log("finalize_success", outcome="created", bytes=len(text),
             durationMs=int((time.perf_counter() - t0) * 1000))
        return {"created": True, "finalizedAt": now, "bytes": len(text),
                "source": source}


async def ensure_finalized(*, source: str = "startup") -> dict:
    """이벤트가 끝났고 확정본이 없으면 만든다. 그 외에는 아무것도 하지 않는다."""
    if not ranking_frozen():
        return {"created": False, "reason": "not_frozen",
                "eventStatus": event_status()}
    return await finalize(source=source)


def cooldown_remaining() -> float:
    """다음 확정 시도까지 남은 초. 0이면 지금 시도할 수 있다."""
    if _last_attempt_at is None:
        return 0.0
    left = FINALIZE_COOLDOWN_SECONDS - (time.monotonic() - _last_attempt_at)
    return left if left > 0 else 0.0


async def _finalize_bg(source: str) -> None:
    global _finalize_task
    try:
        await finalize(source=source)
    except Exception:                       # noqa: BLE001 — 이미 _log가 남겼다
        pass
    finally:
        _finalize_task = None


def schedule_finalize_if_needed(*, source: str = "request") -> str:
    """확정본이 없을 때 확정 작업을 **한 번만** 예약한다.

    호출자(조회 엔드포인트)는 결과를 기다리지 않는다 — 확정 계산은 참가자 전원을
    조인하는 무거운 쿼리라 공개 GET을 붙잡으면 안 된다. 그 요청은 503을 그대로
    반환하고, 다음 요청이 200을 받는다.

    세 겹으로 중복을 막는다:
      1) 이미 도는 task가 있으면 새로 만들지 않는다(동시 요청 N개 → 실행 1회)
      2) cooldown 안이면 시도조차 하지 않는다(짧은 시간 반복 요청 → DB write 0)
      3) `finalize` 자신이 기존 행을 보면 계산 전에 빠진다
    """
    global _finalize_task, _last_attempt_at
    if _finalize_task is not None and not _finalize_task.done():
        return "in_flight"
    left = cooldown_remaining()
    if left > 0:
        # 같은 cooldown 창 안의 반복 요청은 한 번만 기록한다. 요청마다 남기면
        # 트래픽이 몰릴 때 같은 줄이 수백 개 쌓여 정작 실패 로그가 묻힌다.
        global _cooldown_logged_for
        if _cooldown_logged_for != _last_attempt_at:
            _cooldown_logged_for = _last_attempt_at
            _log("finalize_skipped_cooldown", outcome="skipped",
                 cooldownSeconds=round(left, 1))
        return "cooldown"
    _last_attempt_at = time.monotonic()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return "no_loop"
    _finalize_task = loop.create_task(_finalize_bg(source))
    return "scheduled"


def reset_retry_state() -> None:
    """테스트용 — cooldown과 진행 중 task 표시를 초기화한다."""
    global _finalize_task, _last_attempt_at, _cooldown_logged_for
    _finalize_task = None
    _last_attempt_at = None
    _cooldown_logged_for = None


async def load_entry() -> dict | None:
    """확정본 (body, etag, finalizedAt) — 없으면 None.

    확정본은 변하지 않으므로 프로세스 캐시에 TTL을 두지 않는다. TTL을 두면
    만료될 때마다 DB를 다시 읽을 뿐 값은 같아, 비용만 늘고 얻는 게 없다.
    """
    global _cache
    if _cache is not None:
        return _cache
    async with _cache_lock:
        if _cache is not None:
            return _cache
        row = await _read_row()
        if row:
            _cache = row
        return row


def reset_cache() -> None:
    """테스트·운영자 도구용. 다음 읽기에서 DB를 다시 본다."""
    global _cache
    _cache = None


async def status() -> dict:
    row = await _read_row()
    return {
        "eventStatus": event_status(),
        "freezeEnabled": freeze_enabled(),
        "frozen": ranking_frozen(),
        "finalized": bool(row),
        "finalizedAt": row["finalizedAt"] if row else None,
        "source": row["source"] if row else None,
        "bytes": len(row["body"]) if row else 0,
        "cached": _cache is not None,
        # 자동 회복 관측 — 값은 전부 숫자·불리언이라 민감정보가 없다.
        "retryInFlight": _finalize_task is not None and not _finalize_task.done(),
        "cooldownRemaining": round(cooldown_remaining(), 1),
        "cooldownSeconds": FINALIZE_COOLDOWN_SECONDS,
    }
