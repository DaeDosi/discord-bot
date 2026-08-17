"""계정 설정과 회원탈퇴.

⚠️ **이 모듈은 기본적으로 아무 데이터도 지우지 않는다.** 이유가 있다.

현재 공개된 개인정보처리방침(`web/frontend/app/privacy/page.tsx`)은 삭제 경로를
두 가지로 못박고 있다.

    · 자동 삭제 — 봇을 Discord 서버에서 추방하면 그 서버 데이터가 삭제된다
    · 수동 삭제 — **"삭제 요청은 이메일로만 접수합니다"**

즉 지금 방침에는 '웹에서 누르면 즉시 지운다'는 경로가 없다. 화면에 버튼을 만들고
서버가 조용히 지우기 시작하면 **공개한 방침과 실제 처리가 달라진다.** 그건 고쳐야
할 대상이 방침이지 코드가 아니고, 방침 변경은 운영자의 결정이다.

또 하나: 아래 표를 보면 사용자 식별자를 가진 테이블이 전부 **길드 범위**다
(`(guild_id, user_id)`). 경고·뮤트·포인트·미션은 그 서버 운영자의 운영 기록이기도
해서, 개인 요청만으로 지우는 것이 맞는지가 데이터 종류마다 다르다. 그 판단을
코드가 임의로 내리면 안 된다.

**그래서 이 모듈이 하는 일:**
 1. 사용자에게 **무엇이 저장돼 있는지** 정확히 보여 준다(종류·건수).
 2. 탈퇴 **요청을 감사 가능하게 기록**한다.
 3. 실제 삭제는 **정책이 확정되기 전까지 차단**하고, 그 사실을 응답과 화면에
    분명히 밝힌다 — **완료로 꾸미지 않는다.**
 4. 정책이 정해지면 `ACCOUNT_DELETION_ENABLED=true`와 종류별 정책만으로 켜진다
    (코드를 다시 쓰지 않는다).
"""
from __future__ import annotations

import json
import os
import time

from database import get_db


def deletion_enabled() -> bool:
    """실제 삭제를 실행하는가. **기본 꺼짐** — 위 주석의 방침 문제 때문이다."""
    return os.getenv("ACCOUNT_DELETION_ENABLED", "false").strip().lower() \
        in ("1", "true", "yes", "on")


#: 사용자 식별자를 가진 테이블과 **각 종류의 처리 정책**.
#:
#: `policy`
#:   · `pending_policy` — 어떻게 처리할지 아직 정해지지 않았다. **지우지 않는다.**
#:   · `delete`        — 사용자 본인의 데이터로 확정됨. 탈퇴 시 삭제.
#:   · `retain`        — 법적·운영상 보존이 필요하다고 확정됨.
#:
#: 지금은 **전부 `pending_policy`**다. 하나라도 `delete`로 바꾸려면 그것이
#: 개인정보처리방침과 일치하는지 먼저 확인해야 한다.
DATA_CLASSES: tuple[dict, ...] = (
    {"table": "chzzk_verifications", "column": "user_id", "policy": "pending_policy",
     "label": "치지직 계정 연동",
     "note": "본인의 외부 계정 연결 정보지만, 지우면 팔로우 기간 역할이 해제된다."},
    {"table": "user_points", "column": "user_id", "policy": "pending_policy",
     "label": "서버 포인트",
     "note": "서버 운영자의 경제 기록이기도 하다. 개인 요청만으로 지울지 미정."},
    {"table": "user_xp", "column": "user_id", "policy": "pending_policy",
     "label": "서버 활동 레벨/경험치",
     "note": "위와 같다."},
    {"table": "warnings", "column": "user_id", "policy": "pending_policy",
     "label": "경고 기록",
     "note": "서버 운영자의 제재 기록이다. 삭제 여부는 운영 정책 사안."},
    {"table": "mutes", "column": "user_id", "policy": "pending_policy",
     "label": "뮤트 상태",
     "note": "진행 중인 제재를 개인 요청으로 해제하면 우회 수단이 된다."},
    {"table": "mission_completions", "column": "user_id", "policy": "pending_policy",
     "label": "미션 제출/승인 기록", "note": "보상 지급 근거 기록이다."},
    {"table": "shop_exchanges", "column": "user_id", "policy": "pending_policy",
     "label": "상점 교환 내역", "note": "지급 이력이라 분쟁 시 근거가 된다."},
    {"table": "mod_managers", "column": "user_id", "policy": "pending_policy",
     "label": "관리자 지정", "note": "서버 권한 설정이다."},
    {"table": "chzzk_gambling_votes", "column": "discord_user_id",
     "policy": "pending_policy", "label": "채팅 도박 투표 기록",
     "note": "정산 근거다."},
)

#: 애초에 개인 식별자를 담지 않는 것 — 탈퇴와 무관함을 화면에서 밝히기 위해 적어 둔다.
NOT_PERSONAL = (
    {"label": "사이트 방문 집계",
     "note": "IP를 날짜별 솔트와 함께 해시해 저장하며 계정과 연결되지 않는다."},
    {"label": "치지직 방송 통계",
     "note": "치지직 공개 정보를 수집한 것으로 이용자 계정과 무관하다."},
)


async def inventory(user_id: str) -> dict:
    """이 계정과 연결된 데이터 **종류와 건수**.

    값 자체(포인트 수치·경고 사유 등)는 돌려주지 않는다 — 무엇이 저장돼 있는지
    아는 데 필요한 것은 종류와 건수이고, 내용까지 API로 흘리면 그 자체가 노출 경로다.
    """
    db = await get_db()
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return {"classes": [], "total": 0, "notPersonal": list(NOT_PERSONAL)}

    out, total = [], 0
    for spec in DATA_CLASSES:
        n = 0
        try:
            row = await (await db.execute(
                f"SELECT COUNT(*) c FROM {spec['table']} WHERE {spec['column']}=?",
                (uid,))).fetchone()
            n = int(row["c"] or 0)
        except Exception:
            # 테이블이 아직 없을 수 있다(마이그레이션 순서). 0으로 둔다.
            n = 0
        total += n
        out.append({"label": spec["label"], "count": n,
                    "policy": spec["policy"], "note": spec["note"]})
    return {"classes": out, "total": total, "notPersonal": list(NOT_PERSONAL)}


def blocked_classes() -> list[str]:
    """아직 정책이 확정되지 않아 지울 수 없는 종류."""
    return [s["label"] for s in DATA_CLASSES if s["policy"] == "pending_policy"]


async def request_deletion(user_id: str, username: str, *, reason: str = "") -> dict:
    """탈퇴 요청을 기록한다. **정책이 확정되기 전까지 삭제는 실행하지 않는다.**

    반환에서 중요한 것은 `status`다.
      · `blocked_pending_policy` — 접수만 됐고 **아무것도 지우지 않았다**
      · `completed`              — 실제로 지웠다(정책이 켜졌을 때만 가능)
    화면은 이 값을 그대로 읽어 문구를 정한다. **`ok: true`를 완료로 읽지 말 것** —
    요청 접수 성공과 탈퇴 완료는 다른 사건이다.
    """
    now = int(time.time())
    db = await get_db()
    pending = blocked_classes()

    status = "completed" if (deletion_enabled() and not pending) \
        else "blocked_pending_policy"
    # 요청 내용은 감사용 최소 정보만 남긴다. **사유 원문을 길게 저장하지 않는다.**
    await db.execute(
        """INSERT INTO account_deletion_requests
               (user_id, username, requested_at, status, blocked_classes, note)
           VALUES (?,?,?,?,?,?)""",
        (str(user_id), (username or "")[:64], now, status,
         json.dumps(pending, ensure_ascii=False), (reason or "")[:200]))
    await db.commit()

    if status == "completed":
        deleted = await _execute_deletion(user_id)
        return {"ok": True, "status": "completed", "deleted": deleted,
                "blocked": []}

    # **지우지 않았다는 사실을 그대로 돌려준다.**
    return {
        "ok": True,
        "status": "blocked_pending_policy",
        "deleted": {},
        "blocked": pending,
        "reason": ("현재 개인정보처리방침은 삭제 요청을 이메일로만 접수합니다. "
                   "웹에서의 즉시 삭제는 아직 제공하지 않습니다."),
        "nextStep": "privacy_email",
    }


async def _execute_deletion(user_id: str) -> dict:
    """정책이 `delete`인 종류만 지운다. **`pending_policy`는 건드리지 않는다.**

    지금은 모든 종류가 `pending_policy`라 이 함수는 아무것도 지우지 않는다 —
    그래도 남겨 두는 이유는, 정책이 정해졌을 때 **표만 고치면** 되도록 하기 위해서다.
    """
    db = await get_db()
    uid = int(user_id)
    out: dict[str, int] = {}
    for spec in DATA_CLASSES:
        if spec["policy"] != "delete":
            continue
        try:
            cur = await db.execute(
                f"DELETE FROM {spec['table']} WHERE {spec['column']}=?", (uid,))
            out[spec["label"]] = cur.rowcount or 0
        except Exception:
            out[spec["label"]] = 0
    await db.commit()
    return out


async def recent_request(user_id: str) -> dict | None:
    db = await get_db()
    r = await (await db.execute(
        "SELECT requested_at, status FROM account_deletion_requests "
        "WHERE user_id=? ORDER BY requested_at DESC LIMIT 1",
        (str(user_id),))).fetchone()
    return {"requestedAt": int(r["requested_at"]), "status": r["status"]} if r else None
