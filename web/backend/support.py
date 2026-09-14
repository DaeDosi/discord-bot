"""수정 요청 접수 (지원 메뉴).

클립 정보가 틀렸을 때 사용자가 알려 주는 통로다. 지키는 계약은 다섯이다.

1. **서버가 검증한다.** 길이·URL 형식·이메일 형식·분류를 전부 서버에서 본다
   (프런트 검증만 믿으면 그건 검증이 아니라 안내다).
2. **이메일과 문의 내용을 로그에 출력하지 않는다.** 구조화 로그에 담는 것은
   접수 여부·분류·길이뿐이다.
3. **중복 제출을 막는다.** 같은 내용이 연속으로 들어오면 DB의 유니크 인덱스가 막는다
   (애플리케이션 검사만 믿으면 두 요청이 동시에 통과한다).
4. **rate limit** — 익명 접수라 자동화로 부풀리기 쉽다.
5. **평문으로 저장하고 HTML로 해석하지 않는다**(PUBLIC-UX-1a). 서버는 형식·길이·제어문자만 다루고
   태그를 임의로 걷어내 문장을 다시 조립하지 않는다(`a < b > c` 같은 정상 문장이 깨진다). 안전은
   **렌더링 계약**이 지킨다 — 이 값을 보여 주는 곳은 Nexadmin 한 곳이고 React 텍스트 노드로만 그린다
   (`dangerouslySetInnerHTML` 금지, 계약 테스트). 이스케이프해 저장하지도 않는다
   (화면에 `&lt;`가 보인다).

보관 기간은 **이 모듈이 정하지 않는다** — 정본은 `support_retention.py`(SUPPORT-POLICY-1)이고,
개인정보처리방침·공개 폼 문구가 같은 숫자를 쓴다. 여기서는 상태가 **실제로 바뀐 시각**
(`status_changed_at`)을 정확히 남기는 것까지만 책임진다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata

import support_retention as retention

from database import get_db

# ── 제한값 ──────────────────────────────────────────────────────────────────
MAX_CLIP_REF = 200
MAX_DESCRIPTION = 2000
MIN_DESCRIPTION = 10
MAX_DESIRED = 1000
MAX_URL = 300
MAX_EMAIL = 254

CATEGORIES: dict[str, str] = {
    "wrong_owner": "참가자(채널) 정보가 잘못됨",
    "missing_clip": "클립이 목록에 없음",
    "wrong_metric": "지표(하트·조회수)가 실제와 다름",
    "wrong_ranking": "순위 표시가 이상함",
    "takedown": "삭제·비공개 요청",
    "other": "기타",
}

#: 접수 간격(초)과 창당 허용 건수 — 사람이 실제로 낼 수 있는 양보다 넉넉하되
#: 자동 반복은 잡히는 선.
RATE_WINDOW = 600.0
RATE_LIMIT = 3

# ── 제출자 식별용 소금 ──────────────────────────────────────────────────────
#
# **저장소에 고정된 공용 소금을 두지 않는다.** 코드에 박힌 값은 공개된 값이고,
# 공개된 소금은 소금이 아니다 — 후보 IP를 넣어 돌려 보면 해시가 맞춰진다.
#
# 프로세스마다 임의 소금을 만드는 선택지도 있었지만 **쓰지 않았다.** 중복 차단이
# `correction_requests`의 유니크 인덱스에 **영속**되기 때문이다. 소금이 재시작·
# replica마다 달라지면 같은 사람의 같은 제보가 매번 다른 키가 되어 중복 차단이
# 조용히 무력화된다("동작하는 것처럼 보이지만 실제로는 막지 못하는" 상태).
#
# 그래서 **fail-closed**를 택했다. 소금이 설정되지 않으면 접수 자체를 막고,
# 그 사실을 운영자가 읽을 수 있는 문구로 알린다. 이 저장소의 기존 관행과도 같다
# (`singcup_router._require_secret`: "secret이 설정되지 않은 배포에서는 아예 막는다").
#
# ⚠️ 한계: 소금을 바꾸면 그 이후 제출은 이전 것과 중복으로 판정되지 않는다.
# 소금 회전은 곧 중복 이력 초기화다.
SALT_ENV = "SUPPORT_HASH_SALT"
#: 최소 길이. 짧은 값은 사전 공격에 무력하다.
SALT_MIN_LENGTH = 16
#: 문서·예제에 쓰일 법한 값은 **유효한 secret으로 인정하지 않는다.**
#: 설정한 줄 알았는데 예제가 그대로 남아 있는 상태가 가장 위험하다.
_SALT_BLOCKLIST = frozenset({
    "nexbot-support", "changeme", "change-me", "secret", "password",
    "your-secret-here", "todo", "example", "test", "salt", "0" * 16,
})


class SupportUnavailable(RuntimeError):
    """설정이 없어 접수를 받을 수 없다. 라우터가 503으로 바꾼다."""


def salt_configured() -> bool:
    try:
        _salt()
        return True
    except SupportUnavailable:
        return False


def _salt() -> str:
    """설정된 소금. 없거나 형식이 어긋나면 **접수를 막는다.**

    반환값은 호출부에서 해시 재료로만 쓰이고 응답·로그로 나가지 않는다.
    """
    raw = os.getenv(SALT_ENV)
    v = (raw or "").strip()
    if not v:
        raise SupportUnavailable(
            "수정 요청 접수가 설정되지 않아 지금은 이용할 수 없습니다.")
    if len(v) < SALT_MIN_LENGTH or v.lower() in _SALT_BLOCKLIST:
        # **어떤 점이 틀렸는지 사용자에게는 알리지 않는다**(설정값 추측 단서가 된다).
        raise SupportUnavailable(
            "수정 요청 접수가 설정되지 않아 지금은 이용할 수 없습니다.")
    return v

_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[^\s]*)?$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,20}$")
_WS_RE = re.compile(r"[ \t]+")


class SupportError(ValueError):
    """입력이 규칙을 어겼다. 라우터가 400으로 바꾼다."""


class SupportRateLimited(SupportError):
    """너무 잦다. 라우터가 429로 바꾼다(입력 오류와 조치가 다르다 — 고칠 것이 없다)."""


class SupportDuplicate(SupportError):
    """같은 내용이 이미 접수됐다. 라우터가 409로 바꾼다."""


class SupportNotFound(SupportError):
    """없는 접수 번호. 삭제 라우트가 404로 바꾼다(상태 변경은 기존 계약대로 400)."""


class SupportTemporary(RuntimeError):
    """저장소 오류 등 일시 장애. 라우터가 503으로 바꾼다 — **원인 문자열은 밖으로 내지 않는다.**"""


#: 처리 상태. 공개 응답에는 나가지 않는다(OWNER 화면 전용).
STATUSES: dict[str, str] = {
    "received": "접수",
    "in_review": "확인 중",
    "resolved": "반영 완료",
    "rejected": "반영 안 함",
}

#: 클립 ID 형식. 치지직 클립 UID는 영문·숫자 조합이다. **자유 문장은 받지 않는다** —
#: "그 노래 클립" 같은 값은 무엇을 고칠지 특정할 수 없어 운영자가 되물어야 한다.
_CLIP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,100}$")


def _log(event: str, **fields) -> None:
    """**이메일·본문을 절대 담지 않는다.** 길이와 분류만 남긴다."""
    print(f"[support] {json.dumps({'event': event, **fields}, ensure_ascii=False)}",
          flush=True)


def clean_text(value: object, *, field: str, max_len: int,
               min_len: int = 0, required: bool = True) -> str:
    """제어문자를 버리고 공백을 정규화한 뒤 길이를 검사한다. **평문 그대로** 돌려준다.

    HTML을 이스케이프하지도, 태그를 걷어내지도 않는다 — 저장값은 평문이고, 보여 주는 쪽이
    React 텍스트 노드로만 그린다(모듈 상단 계약 5).
    """
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SupportError(f"{field} 형식이 올바르지 않습니다.")
    # 탭은 공백으로 바꾼 뒤(Cc라서 그냥 버리면 앞뒤 단어가 붙는다) 제어문자(Cc)와
    # 서식문자(Cf: 제로폭·RTL override)를 버린다. 줄바꿈은 남긴다.
    value = value.replace("\t", " ")
    v = "".join(c for c in value
                if c in ("\n", "\r") or unicodedata.category(c) not in ("Cc", "Cf"))
    v = _WS_RE.sub(" ", v).replace("\r\n", "\n").strip()
    if not v:
        if required:
            raise SupportError(f"{field}을(를) 입력해 주세요.")
        return ""
    if len(v) > max_len:
        raise SupportError(f"{field}은(는) {max_len}자 이하여야 합니다.")
    if min_len and len(v) < min_len:
        raise SupportError(f"{field}은(는) {min_len}자 이상 입력해 주세요.")
    return v


def clean_url(value: object, *, required: bool = False) -> str:
    """https만 통과시킨다. `javascript:` 같은 스킴이 화면 링크로 나가면 안 된다."""
    v = clean_text(value, field="근거 자료 주소", max_len=MAX_URL, required=required)
    if not v:
        return ""
    if not _URL_RE.match(v):
        raise SupportError("근거 자료 주소는 https:// 로 시작하는 주소여야 합니다.")
    return v


def clean_clip_ref(value: object) -> str:
    """수정 대상 식별자 — `https://` 주소(클립·페이지) 또는 클립 ID만 받는다.

    `http://`·`javascript:` 같은 스킴은 운영자 화면에서 링크가 될 수 있어 거절한다.
    """
    v = clean_text(value, field="클립 주소 또는 ID", max_len=MAX_CLIP_REF, required=True)
    if v.lower().startswith("https://"):
        if not _URL_RE.match(v):
            raise SupportError("클립 주소 형식이 올바르지 않습니다.")
        return v
    if _CLIP_ID_RE.match(v):
        return v
    raise SupportError(
        "클립 주소(https://로 시작) 또는 클립 ID(영문·숫자)를 입력해 주세요.")


def clean_email(value: object) -> str:
    v = clean_text(value, field="답변받을 이메일", max_len=MAX_EMAIL, required=False)
    if not v:
        return ""
    if not _EMAIL_RE.match(v):
        raise SupportError("이메일 형식이 올바르지 않습니다.")
    return v


def clean_category(value: object) -> str:
    v = value if isinstance(value, str) else ""
    v = v.strip()
    if v not in CATEGORIES:
        raise SupportError("분류를 선택해 주세요.")
    return v


def _dedupe_key(submitter: str, category: str, clip_ref: str,
                description: str) -> str:
    """중복 판정 키 — **원문을 담지 않는다**(해시만 저장한다).

    `submitter`는 이미 해시다(`client_ip.resolve()["id"]`). 여기서 소금을 한 번 더
    섞는 이유는 방문 집계용 식별자와 같은 값이 되지 않게 하기 위해서다 —
    두 곳에서 같은 값을 쓰면 한쪽이 유출됐을 때 다른 쪽 기록까지 연결된다.

    제출자 식별자를 섞으므로, 서로 다른 사람이 같은 문제를 신고하는 것은 막지
    않는다(그건 중복이 아니라 같은 문제의 다중 제보다).
    """
    # 구분자를 **반드시** 넣는다. 그냥 이으면 필드 경계가 다른 입력이 같은 키가 된다
    # (clip="ab", desc="c" 와 clip="a", desc="bc"가 충돌한다).
    raw = "\x1f".join([_salt(), submitter, category, clip_ref.lower(),
                       description.strip().lower()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── rate limit (프로세스 내) ────────────────────────────────────────────────
_hits: dict[str, list[float]] = {}


def _rate_limit(key: str) -> None:
    now = time.monotonic()
    q = _hits.setdefault(key, [])
    cutoff = now - RATE_WINDOW
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= RATE_LIMIT:
        raise SupportRateLimited("요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.")
    q.append(now)
    # 메모리 상한 — 키가 무한히 늘지 않게 한다.
    if len(_hits) > 20000:
        _hits.clear()


def reset_state() -> None:
    """테스트용."""
    _hits.clear()


async def submit(body: dict, *, submitter: str) -> dict:
    """수정 요청 접수.

    `submitter`는 **해시된 식별자**다(IP 원문이나 이메일이 아니다). rate limit과
    중복 판정에만 쓰고 저장하지 않는다.

    소금이 설정되지 않았으면 **아무것도 받지 않는다**(fail-closed). 검증보다
    먼저 확인해 입력이 조금이라도 처리되기 전에 끊는다.
    """
    _salt()                       # 미설정이면 여기서 SupportUnavailable
    category = clean_category((body or {}).get("category"))
    clip_ref = clean_clip_ref((body or {}).get("clipRef"))
    description = clean_text((body or {}).get("description"), field="문제 설명",
                             max_len=MAX_DESCRIPTION, min_len=MIN_DESCRIPTION)
    desired = clean_text((body or {}).get("desiredFix"), field="원하는 수정 내용",
                         max_len=MAX_DESIRED, required=False)
    evidence = clean_url((body or {}).get("evidenceUrl"))
    email = clean_email((body or {}).get("email"))

    _rate_limit(submitter)
    key = _dedupe_key(submitter, category, clip_ref, description)

    db = await get_db()
    now = int(time.time())
    try:
        # 신규 행의 보관 기준 시각은 접수 시각이다(`status_changed_at = created_at`).
        cur = await db.execute(
            """INSERT INTO correction_requests
                   (created_at, category, clip_ref, description, desired_fix,
                    evidence_url, contact_email, dedupe_key, status, status_changed_at)
               VALUES (?,?,?,?,?,?,?,?,'received',?)""",
            (now, category, clip_ref, description, desired,
             evidence, email, key, now))
        await db.commit()
    except Exception as e:
        try:
            await db.rollback()
        except Exception:
            pass
        if "UNIQUE" in str(e).upper():
            # **중복 차단은 DB가 한다** — 애플리케이션 검사만 믿으면 동시 두 건이 통과한다.
            raise SupportDuplicate("같은 내용이 이미 접수되었습니다.") from e
        # 원인 문자열(SQL·경로)은 로그에도 응답에도 싣지 않는다 — 종류 이름만 남긴다.
        _log("correction_store_failed", error=type(e).__name__)
        raise SupportTemporary(
            "일시적인 문제로 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.") from e

    # 로그에는 **이메일도 본문도 넣지 않는다.** 길이만 남긴다.
    _log("correction_received", category=category,
         descriptionLength=len(description), hasEmail=bool(email),
         hasEvidence=bool(evidence))
    return {"ok": True, "id": int(cur.lastrowid), "status": "received"}


# ── 운영자(OWNER) 처리 ─────────────────────────────────────────────────────
#
# 접수만 되고 볼 곳이 없으면 기능이 없는 것과 같다. 이 두 함수는 **OWNER 라우트에서만**
# 부른다. 응답에는 `dedupe_key`(해시)를 싣지 않는다 — 운영자에게 필요 없는 값이고
# 소금 추측 재료만 늘린다.

_LIST_COLUMNS = ("id, created_at, category, clip_ref, description, desired_fix,"
                 " evidence_url, contact_email, status, status_changed_at,"
                 " contact_email_cleared_at")


async def list_requests(*, status: str = "", limit: int = 50,
                        before_id: int = 0) -> dict:
    """최신 순 목록. `before_id`로 다음 쪽을 읽는다(OFFSET은 쓰지 않는다)."""
    if status and status not in STATUSES:
        raise SupportError("알 수 없는 처리 상태입니다.")
    limit = max(1, min(int(limit or 50), 100))
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if before_id:
        where.append("id < ?")
        args.append(int(before_id))
    sql = f"SELECT {_LIST_COLUMNS} FROM correction_requests"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    db = await get_db()
    rows = await (await db.execute(sql, (*args, limit + 1))).fetchall()
    counts = {k: 0 for k in STATUSES}
    for r in await (await db.execute(
            "SELECT status, COUNT(*) n FROM correction_requests GROUP BY status")).fetchall():
        if r["status"] in counts:
            counts[r["status"]] = int(r["n"])
    items = [_item(r) for r in rows[:limit]]
    return {"items": items, "hasMore": len(rows) > limit, "counts": counts,
            "statuses": [{"key": k, "label": v} for k, v in STATUSES.items()],
            # 정리 작업이 실제로 켜져 있는지 화면이 **숨기지 않고** 보여 준다 —
            # 방침은 삭제를 약속하는데 dry-run이면 운영자가 알아야 한다.
            "retention": {"mode": retention.mode(), "policy": retention.policy(),
                          "lastRun": retention.last_report()}}


def _item(r) -> dict:
    created = int(r["created_at"])
    changed = int(r["status_changed_at"] or 0)
    sched = retention.schedule(created, changed, r["status"])
    cleared_at = int(r["contact_email_cleared_at"] or 0)
    return {
        "id": int(r["id"]), "createdAt": created,
        # 구 코드가 넣은 행(0)은 접수 시각이 기준이다 — 정리 계산과 같은 규칙.
        "statusChangedAt": max(created, changed),
        "category": r["category"], "categoryLabel": CATEGORIES.get(r["category"], r["category"]),
        "clipRef": r["clip_ref"], "description": r["description"],
        "desiredFix": r["desired_fix"], "evidenceUrl": r["evidence_url"],
        "contactEmail": r["contact_email"], "status": r["status"],
        "emailClearedAt": cleared_at or None,
        # 이메일이 남아 있을 때만 제거 예정일이 의미가 있다.
        "emailRemovalDueAt": sched["emailRemovalAt"] if r["contact_email"] else None,
        "deletionDueAt": sched["deletionAt"],
    }


async def set_status(request_id: int, status: str, *, now: int | None = None) -> dict:
    """상태 변경. **실제로 달라질 때만** `status_changed_at`을 옮긴다.

    · 같은 상태를 다시 저장하면 시각을 바꾸지 않는다 — 그러지 않으면 선택 상자를 한 번
      건드리는 것만으로 보관 기간이 다시 180일 늘어난다.
    · 재오픈(resolved/rejected → received/in_review)도 실제 변경이라 시각이 재오픈 시점이 된다.
      미처리 행의 절대 상한은 여전히 **접수 후 365일**이다(정리 조건이 created_at 기준).
    · 이미 지운 이메일은 되살리지 않는다(지운 값을 보관하지 않으므로 되살릴 수도 없다).
    · 한 문장 UPDATE라 동시 변경은 마지막 쓰기가 이기되, status와 시각이 어긋나지 않는다.
    """
    if status not in STATUSES:
        raise SupportError("알 수 없는 처리 상태입니다.")
    ts = int(time.time() if now is None else now)
    db = await get_db()
    cur = await db.execute(
        "UPDATE correction_requests SET status=?, status_changed_at=?"
        " WHERE id=? AND status<>?",
        (status, ts, int(request_id), status))
    await db.commit()
    if not cur.rowcount:
        row = await (await db.execute(
            "SELECT 1 FROM correction_requests WHERE id=?", (int(request_id),))).fetchone()
        if row is None:
            raise SupportError("접수 건을 찾을 수 없습니다.")
        return {"id": int(request_id), "status": status, "changed": False}
    _log("correction_status_changed", status=status)
    return {"id": int(request_id), "status": status, "changed": True}


async def delete_request(request_id: int) -> dict:
    """OWNER 수동 삭제. **삭제 전 내용을 돌려주지 않는다**(번호만).

    되돌릴 수 없다. 없는 번호·이미 지운 번호는 `SupportNotFound`(404) — 두 번 눌러도
    두 번째는 아무것도 지우지 않고 그 사실을 그대로 알린다.
    """
    db = await get_db()
    cur = await db.execute("DELETE FROM correction_requests WHERE id=?", (int(request_id),))
    await db.commit()
    if not cur.rowcount:
        raise SupportNotFound("접수 건을 찾을 수 없습니다.")
    _log("correction_deleted")
    return {"id": int(request_id), "deleted": True}


def categories() -> list[dict]:
    return [{"key": k, "label": v} for k, v in CATEGORIES.items()]


def limits() -> dict:
    """화면이 같은 한도를 쓰도록 서버 값을 준다(프런트 상수는 조용히 갈라진다)."""
    return {"clipRef": MAX_CLIP_REF, "description": MAX_DESCRIPTION,
            "descriptionMin": MIN_DESCRIPTION, "desiredFix": MAX_DESIRED,
            "evidenceUrl": MAX_URL, "email": MAX_EMAIL}
