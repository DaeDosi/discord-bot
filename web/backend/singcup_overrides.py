"""싱드컵 대표 클립 수동 지정(override).

## 왜 필요한가

자동 선정 규칙(하트↓ → 조회수↓ → 생성↑ → uid↑)은 **정상이며 바꾸지 않는다.**
문제는 참가자가 제출본을 나중에 따로 올리는 경우다 — 그때 하트가 앞선 옛 클립이
대표로 잡힌다. 규칙을 고쳐서 해결하면 순위가 소급으로 통째 바뀌므로, 사람이
개별 건을 지정하는 통로를 따로 둔다.

## 왜 별도 표인가 (직접 UPDATE 금지)

`singcup_streamers.representative_clip_uid`는 랭킹 재계산의 upsert가
`representative_clip_uid = excluded.representative_clip_uid`로 **조건 없이
덮어쓴다**. 직접 UPDATE한 값은 다음 재계산에서 사라진다. 그래서 '사람의 의도'는
이 표에 영속화하고, 대표를 *고르는* 지점이 이 표를 함께 본다.

## split-brain을 어떻게 막는가

대표를 읽는 곳은 많다(`/main` 조립 SQL, 점수, movers, 스냅샷, 스윕 `_TARGET_SQL`
prio 1·3, 삭제 fallback). 그 **전부가 `singcup_streamers.representative_clip_uid`
한 컬럼을 읽는다.** 반대로 그 컬럼에 쓰는 곳은 둘뿐이다.

  ① `recompute_ranking` → `_build_reps` → `pick_representative`
  ② 삭제 확정 시 재선정 → `_NEW_REP_SQL`

그래서 override는 **읽는 쪽이 아니라 이 두 '고르는' 지점**에 넣는다. 그러면 저장된
컬럼 자체가 곧 effective representative가 되고, 모든 소비자는 구조적으로 같은 값을
본다. 소비자마다 JOIN을 복제하면 그 복제본들이 갈라지는 것이 정확히 split-brain이다.

## 무효 override

override 클립이 삭제·비활성이 되면 행을 지우지 않고 **효력만 잃는다**(자동 대표로
복귀). 클립이 되살아나면 override가 그대로 다시 걸린다. 지워 버리면 복구 시
사람이 다시 지정해야 한다.
"""
import re
import time
from urllib.parse import urlparse

from singcup_collector import END_AT, EVENT_ID, START_AT

from database import get_db

# 치지직 클립 uid. singcup_audit._UID_RE와 같은 문자 집합이다 — 경로에 끼워 넣는
# 값이므로 여기서 좁히지 않으면 아래 SSRF 방어가 의미를 잃는다.
_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# 입력으로 URL을 받되 **URL을 그대로 요청하지 않는다.** 우리가 뽑는 것은 uid 하나이고,
# 실제 호출 대상은 서버가 고정한 치지직 호스트·경로다(singcup_clips.CLIP_DETAIL_API).
# 그래서 임의 호스트·포트·내부 IP·리다이렉트로 유도할 여지가 없다.
_ALLOWED_HOSTS = frozenset({"chzzk.naver.com", "www.chzzk.naver.com"})
_CLIP_PATH_RE = re.compile(r"^/clips/([A-Za-z0-9_-]{1,32})$")


class InvalidClipInput(ValueError):
    """사용자 입력(URL 또는 uid)이 규칙에 맞지 않는다."""


def valid_clip_uid(uid: str) -> bool:
    return bool(uid) and bool(_UID_RE.match(uid))


def parse_clip_uid(raw: str) -> str:
    """URL 또는 uid에서 **clip_uid만** 엄격하게 뽑는다.

    허용하는 형태는 둘뿐이다.
      1) uid 그 자체 — `^[A-Za-z0-9_-]{1,32}$`
      2) `https://chzzk.naver.com/clips/<uid>` (www 허용, https만, 기본 포트만)

    쿼리스트링·프래그먼트는 무시한다(공유 링크에 `?from=...`이 흔히 붙는다).
    그 외에는 전부 거부한다 — 특히 다음을 의도적으로 막는다.

      * http/파일/기타 스킴, `@`를 이용한 사용자정보 트릭
      * 명시 포트(`chzzk.naver.com:8080`) — 호스트가 맞아도 포트는 고정이어야 한다
      * 다른 호스트(`evil.com/clips/x`), 서브도메인 사칭(`chzzk.naver.com.evil.com`)
      * 내부 주소(`127.0.0.1`, `169.254.169.254`, `[::1]`) — 위 호스트 화이트리스트가
        먼저 걸러내므로 별도 IP 판정이 필요 없다
      * `/clips/<uid>/../..` 같은 경로 조작 — 경로를 정규식으로 **완전 일치**시킨다

    반환값은 uid이며, 호출자는 이 값을 고정 endpoint에 끼워 쓴다.
    """
    s = (raw or "").strip()
    if not s:
        raise InvalidClipInput("클립 URL 또는 UID를 입력하세요.")
    if len(s) > 300:
        raise InvalidClipInput("입력이 너무 깁니다.")

    if "/" not in s and ":" not in s:
        if not valid_clip_uid(s):
            raise InvalidClipInput("클립 UID 형식이 올바르지 않습니다.")
        return s

    try:
        u = urlparse(s)
    except Exception:
        raise InvalidClipInput("URL을 해석할 수 없습니다.") from None
    if u.scheme != "https":
        raise InvalidClipInput("https 치지직 클립 주소만 허용합니다.")
    # `username@host` 형태를 막는다. netloc에 '@'가 있으면 hostname은 뒤쪽을
    # 가리키지만, 사람 눈에는 앞쪽이 호스트로 보인다.
    if "@" in (u.netloc or ""):
        raise InvalidClipInput("허용되지 않는 주소 형식입니다.")
    if u.port is not None:
        raise InvalidClipInput("포트를 지정한 주소는 허용하지 않습니다.")
    if (u.hostname or "").lower() not in _ALLOWED_HOSTS:
        raise InvalidClipInput("치지직 클립 주소(chzzk.naver.com)만 허용합니다.")
    m = _CLIP_PATH_RE.match(u.path or "")
    if not m:
        raise InvalidClipInput("클립 주소 형식이 올바르지 않습니다. "
                               "예: https://chzzk.naver.com/clips/XXXXXXXXXX")
    return m.group(1)


# ── 조회 ────────────────────────────────────────────────────────────────────
async def active_override_map(event_id: str = EVENT_ID) -> dict[str, str]:
    """{owner_channel_id: override_clip_uid} — 활성 override 전부.

    랭킹 재계산이 참가자 전원을 한 번에 처리하므로 건별 SELECT를 돌리지 않는다.
    """
    db = await get_db()
    rows = await (await db.execute(
        "SELECT owner_channel_id, override_clip_uid "
        "FROM singcup_representative_overrides "
        "WHERE event_id=? AND cleared_at IS NULL", (event_id,))).fetchall()
    return {r["owner_channel_id"]: r["override_clip_uid"] for r in rows}


async def get_override(owner_channel_id: str,
                       event_id: str = EVENT_ID) -> dict | None:
    db = await get_db()
    r = await (await db.execute(
        "SELECT id, event_id, owner_channel_id, override_clip_uid, reason, "
        "       created_at, updated_at "
        "FROM singcup_representative_overrides "
        "WHERE event_id=? AND owner_channel_id=? AND cleared_at IS NULL",
        (event_id, owner_channel_id))).fetchone()
    return dict(r) if r else None


# ── 검증 ────────────────────────────────────────────────────────────────────
# 자동 대표 후보와 **같은 조건**이어야 한다(singcup_clips._NEW_REP_SQL 참고).
# 여기가 느슨하면 지정은 성공하는데 대표로는 안 잡히는 상태가 된다 — 화면에는
# "적용됨"이라고 뜨는데 순위는 안 바뀌는, 가장 나쁜 실패 모양이다.
_ELIGIBLE_SQL = """
    SELECT clip_uid, owner_channel_id, clip_title, heart_count, view_count,
           created_at, active, deletion_state, blind_type, thumbnail_image_url
    FROM singcup_clips
    WHERE event_id = ? AND clip_uid = ?
"""

REASON_OK = "ok"
REASON_NOT_FOUND = "not_found"
REASON_OWNER_MISMATCH = "owner_mismatch"
REASON_INACTIVE = "inactive"
REASON_DELETED = "deleted"
REASON_BLIND = "blind"
REASON_OUT_OF_EVENT = "out_of_event"
REASON_ALREADY_AUTO = "already_auto"

_BAD_BLIND = {"BLIND", "DELETE", "DELETED", "PRIVATE"}

_REASON_TEXT = {
    REASON_NOT_FOUND: "이 클립은 싱드컵 DB에 없습니다(수집되지 않았거나 태그 미인정).",
    REASON_OWNER_MISMATCH: "이 클립의 소유 채널이 선택한 참가자와 다릅니다.",
    REASON_INACTIVE: "이 클립은 비활성 상태라 대표로 지정할 수 없습니다.",
    REASON_DELETED: "이 클립은 삭제로 확정된 상태입니다.",
    REASON_BLIND: "이 클립은 블라인드/비공개 상태입니다.",
    REASON_OUT_OF_EVENT: "이 클립의 생성 시각이 이벤트 기간 밖입니다.",
}


def reason_text(reason: str) -> str:
    return _REASON_TEXT.get(reason, "대표로 지정할 수 없는 클립입니다.")


async def check_clip_eligible(owner_channel_id: str, clip_uid: str,
                              event_id: str = EVENT_ID) -> tuple[str, dict | None]:
    """(reason, clip_row) — reason이 REASON_OK일 때만 지정 가능하다.

    **DB만 본다.** 외부 API 호출은 호출자가 트랜잭션 밖에서 따로 한다.
    """
    db = await get_db()
    r = await (await db.execute(_ELIGIBLE_SQL, (event_id, clip_uid))).fetchone()
    if r is None:
        return REASON_NOT_FOUND, None
    row = dict(r)
    if row["owner_channel_id"] != owner_channel_id:
        return REASON_OWNER_MISMATCH, row
    if not int(row["active"] or 0):
        return REASON_INACTIVE, row
    if str(row["deletion_state"] or "") == "confirmed_deleted":
        return REASON_DELETED, row
    if str(row["blind_type"] or "").upper() in _BAD_BLIND:
        return REASON_BLIND, row
    created = int(row["created_at"] or 0)
    if not (int(START_AT.timestamp()) <= created <= int(END_AT.timestamp())):
        return REASON_OUT_OF_EVENT, row
    return REASON_OK, row


# ── 쓰기 ────────────────────────────────────────────────────────────────────
async def set_override(owner_channel_id: str, clip_uid: str, *, reason: str = "",
                       event_id: str = EVENT_ID) -> dict:
    """override를 지정(또는 교체)한다. **외부 호출을 하지 않는다.**

    기존 활성 행은 `cleared_at`으로 닫고 새 행을 넣는다 — 같은 트랜잭션이라
    부분 유니크 인덱스(활성 1개)를 위반하는 순간이 없다. 이력이 남으므로 나중에
    "누가 언제 무엇으로 바꿨나"를 그대로 볼 수 있다.
    """
    now = int(time.time())
    db = await get_db()
    prev = await get_override(owner_channel_id, event_id)
    await db.execute(
        "UPDATE singcup_representative_overrides SET cleared_at=?, updated_at=? "
        "WHERE event_id=? AND owner_channel_id=? AND cleared_at IS NULL",
        (now, now, event_id, owner_channel_id))
    await db.execute(
        "INSERT INTO singcup_representative_overrides "
        "(event_id, owner_channel_id, override_clip_uid, reason, "
        " created_at, updated_at, cleared_at) VALUES (?,?,?,?,?,?,NULL)",
        (event_id, owner_channel_id, clip_uid, reason[:200], now, now))
    await db.commit()
    return {"ownerChannelId": owner_channel_id, "overrideClipUid": clip_uid,
            "previousOverrideClipUid": prev["override_clip_uid"] if prev else None,
            "updatedAt": now}


async def clear_override(owner_channel_id: str,
                         event_id: str = EVENT_ID) -> dict:
    """override를 해제한다 → 다음 재계산에서 자동 대표로 복귀한다."""
    now = int(time.time())
    db = await get_db()
    prev = await get_override(owner_channel_id, event_id)
    cur = await db.execute(
        "UPDATE singcup_representative_overrides SET cleared_at=?, updated_at=? "
        "WHERE event_id=? AND owner_channel_id=? AND cleared_at IS NULL",
        (now, now, event_id, owner_channel_id))
    await db.commit()
    return {"ownerChannelId": owner_channel_id, "cleared": cur.rowcount > 0,
            "previousOverrideClipUid": prev["override_clip_uid"] if prev else None}
