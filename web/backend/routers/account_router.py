"""계정 설정 · 회원탈퇴 · 수정 요청 접수.

**회원탈퇴는 기본적으로 아무것도 지우지 않는다.** 이유는 `web/backend/account.py`
상단 주석에 있다(공개된 개인정보처리방침이 '삭제는 이메일로만'이라고 못박고 있다).
여기서는 요청을 받아 기록하고, **지우지 않았다는 사실을 응답에 그대로 담는다.**
"""

import account as acct
import client_ip
import support
from deps import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter(prefix="/api/account", tags=["account"])
support_router = APIRouter(prefix="/api/support", tags=["support"])


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """내 계정과 **저장된 데이터의 종류·건수**.

    값 자체(포인트 수치·경고 사유)는 주지 않는다 — 무엇이 저장돼 있는지 아는 데
    필요한 것은 종류와 건수이고, 내용까지 API로 흘리면 그 자체가 노출 경로다.
    """
    uid = str(user.get("sub") or user.get("user_id") or "")
    return {
        "user": {"id": uid,
                 "username": user.get("username", ""),
                 "globalName": user.get("global_name", ""),
                 "avatar": user.get("avatar", "")},
        "data": await acct.inventory(uid),
        "deletion": {
            # **화면이 스스로 판단하지 않게** 서버가 상태를 준다.
            "enabled": acct.deletion_enabled(),
            "blockedClasses": acct.blocked_classes(),
            "lastRequest": await acct.recent_request(uid),
        },
    }


@router.post("/delete")
async def delete_account(body: dict, request: Request,
                         user: dict = Depends(get_current_user)):
    """회원탈퇴 요청.

    보호 장치:
     · **본인 확인** — JWT의 사용자로만 동작한다(요청 본문의 id를 신뢰하지 않는다).
     · **재확인** — 사용자 이름을 그대로 입력해야 통과한다(오조작 방지).
     · **rate limit** — 같은 사용자의 반복 호출을 막는다.
     · CSRF — 이 API는 쿠키가 아니라 `Authorization` 헤더로 인증하므로 브라우저가
       자동으로 자격증명을 붙이지 않는다. 즉 교차 출처 폼 제출로는 호출되지 않는다.
       (쿠키 인증으로 바꾸는 순간 이 성질이 사라지므로 그때 토큰이 필요하다.)

    **응답의 `status`를 반드시 읽을 것.** `ok: true`는 접수 성공이지 탈퇴 완료가
    아니다. 완료는 `status == "completed"`뿐이다.
    """
    uid = str(user.get("sub") or user.get("user_id") or "")
    if not uid:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    username = str(user.get("username") or "")
    typed = str((body or {}).get("confirm") or "").strip()
    # 사용자 이름을 그대로 치게 한다 — 체크박스 하나보다 오조작이 훨씬 적다.
    if not username or typed != username:
        raise HTTPException(
            status_code=400,
            detail=f"확인을 위해 사용자 이름 '{username}'을(를) 정확히 입력해 주세요.")

    try:
        support._rate_limit(f"account_delete:{uid}")
    except support.SupportError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e

    return await acct.request_deletion(uid, username,
                                       reason=str((body or {}).get("reason") or ""))


# ── 수정 요청 ───────────────────────────────────────────────────────────────

@support_router.get("/correction/meta")
async def correction_meta():
    """분류 목록과 **서버가 정한 길이 한도**, 그리고 접수 가능 여부.

    메타는 접수가 막혀 있어도 응답한다 — 화면이 폼 대신 **안내를 그릴 수 있어야**
    하기 때문이다. 폼을 띄워 놓고 제출할 때 503을 주면 사용자는 자기가 뭘 잘못
    입력했다고 읽는다.
    """
    return {"categories": support.categories(), "limits": support.limits(),
            "accepting": support.salt_configured()}


def _submitter_key(request: Request) -> str:
    """제출자 식별용 값.

    **`client_ip.resolve`는 원문 IP를 돌려주지 않는다** — 이미 날짜별로 회전하는
    해시(`id`, 12자)만 준다. 그 값을 그대로 넘긴다. 여기서 IP를 다시 뽑으면
    원문이 이 모듈로 들어오고, 그때부터 로그·예외 메시지로 샐 경로가 생긴다.

    **소금은 여기서 섞지 않는다.** 섞는 곳은 `support._dedupe_key` 하나뿐이다 —
    두 곳에서 섞으면 어느 쪽이 진짜 소금인지가 갈라지고, 한쪽만 fail-closed가 된다.
    """
    try:
        base = (client_ip.resolve(request) or {}).get("id") or ""
    except Exception:
        base = ""
    # resolve가 실패해도 rate limit을 포기하지 않는다(공용 버킷으로 떨어진다).
    return base or "unknown"


@support_router.post("/correction")
async def submit_correction(body: dict, request: Request):
    """수정 요청 접수(익명 가능).

    검증·중복 차단·rate limit은 전부 `support` 모듈이 한다 — 라우터가 다시 하면
    같은 규칙에 두 벌의 판정이 생긴다.
    **응답에 내부 처리 상태나 관리자 정보를 담지 않는다.**
    """
    try:
        res = await support.submit(body or {}, submitter=_submitter_key(request))
    except support.SupportUnavailable as e:
        # 설정 누락은 사용자 입력 오류가 아니다 — 503으로 구분한다.
        raise HTTPException(status_code=503, detail=str(e)) from e
    except support.SupportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # 접수 번호(id)는 사용자가 문의할 때 쓸 수 있게 주되, 그 외 내부 정보는 없다.
    return {"ok": True, "id": res["id"]}
