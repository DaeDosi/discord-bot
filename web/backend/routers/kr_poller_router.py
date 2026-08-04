"""싱드컵 한국(AWS 서울) outbound poller — 전용 내부 API.

이 두 endpoint는 사람이 쓰는 화면이 아니라 **한국 호스트 하나**가 쓰는 통로다.
브라우저에서 부르지 않으므로 CORS도 쿠키도 관여하지 않고, 인증은 JWT나 OWNER가
아니라 전용 secret 기반 HMAC 서명이다(`SINGCUP_ADMIN_SECRET`은 재사용하지 않는다 —
권한 범위가 다르고, 유출 시 피해 범위도 다르다).

기존 인증들과 달리 timestamp·nonce·body digest까지 서명에 넣는 이유는 이 통로가
**DB를 전진시키기 때문**이다. 정적 secret만 있으면 한 번 캡처된 요청을 그대로
다시 보내는 것으로 값을 되돌리거나 반복 적용할 수 있다.

`GET`이 아니라 둘 다 `POST`인 것도 같은 이유다 — 서명 대상에 body digest를
포함하려면 body가 있어야 한다.

**비용 순서**를 지킨다: secret/활성 확인 → 서명 검증(DB 없음) → 자체 스로틀 →
nonce 기록(DB 쓰기) → 실제 작업. 인증에 실패한 요청은 DB를 전혀 건드리지 못한다.
"""
import json
import time

import singcup_kr_poller as krp
from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/api/internal/singcup/kr-poller", tags=["kr-poller"])

_TASKS_PATH = "/api/internal/singcup/kr-poller/tasks"
_RESULTS_PATH = "/api/internal/singcup/kr-poller/results"


async def _guard(request: Request, path: str, bucket: str,
                 ts: str | None, nonce: str | None, sig: str | None) -> bytes:
    """공통 관문. 통과하면 raw body를 돌려준다.

    실패 응답에는 **사유 코드도 작업 데이터도 싣지 않는다.** 세부 사유를 알려주면
    서명 오라클이 되고, 후보·clipUid가 새면 인증 없이 이벤트 내부 상태를 읽는
    통로가 된다.
    """
    # secret 미설정과 비활성은 같은 503이다. "설정은 됐는데 꺼져 있다"를 구분해
    # 알려줄 이유가 없다.
    if not krp.secret():
        raise HTTPException(status_code=503, detail="사용할 수 없습니다.")
    if not krp.enabled():
        raise HTTPException(status_code=503, detail="사용할 수 없습니다.")

    raw = await request.body()
    # 서명 계산 전에 크기를 자른다 — 거대한 body로 해시 비용을 만들 수 없게 한다.
    if len(raw) > krp.MAX_BODY_BYTES:
        raise HTTPException(status_code=400, detail="요청이 너무 큽니다.")

    now = int(time.time())
    reason = krp.verify(ts, nonce, sig, "POST", path, raw, now)
    if reason:
        # 사유는 로그에만 남긴다. 서명·nonce 원문은 남기지 않는다.
        krp._log({"event": "krp_auth_rejected", "level": "warning",
                  "path": path, "reason": reason})
        raise HTTPException(status_code=401, detail="인증 실패")

    # **nonce를 스로틀보다 먼저 본다.** 순서를 뒤집으면 이미 쓴 서명을 나중에
    # 재전송하는 것만으로 스로틀 슬롯을 선점할 수 있고, 뒤이어 오는 정상 요청이
    # 429를 받는다(재전송 요청은 어차피 401인데 정상 요청만 손해를 본다).
    nonce_verdict = await krp.consume_nonce(nonce or "", now)
    if nonce_verdict == krp.NONCE_REPLAY:
        krp._log({"event": "krp_auth_rejected", "level": "warning",
                  "path": path, "reason": "nonce_replay"})
        raise HTTPException(status_code=401, detail="인증 실패")
    if nonce_verdict == krp.NONCE_DB_BUSY:
        # DB 경합을 인증 실패로 기록하면 로그만 보고 "서명 위조"로 오인한다.
        raise HTTPException(
            status_code=503, detail="일시적으로 사용할 수 없습니다.",
            headers={"Retry-After": str(krp.BUSY_RETRY_AFTER)})

    # `/api/internal/*`은 공개 API용 rate_limit 미들웨어의 대상이 아니다.
    # 인증에 성공한 요청에만 endpoint별 최소 간격을 건다(DB 기반이라 replica 공통).
    #
    # 세 상태를 구분한다. "DB가 잠겼다"를 "너무 자주 불렀다"로 뭉개면 잠금 문제가
    # 호출 빈도 문제로 보여 진단이 어긋난다. 인증 실패로 위장하지도 않는다.
    verdict = await krp.throttle_acquire(bucket)
    if verdict == krp.ALREADY_HELD:
        raise HTTPException(
            status_code=429, detail="잠시 후 다시 시도하세요.",
            headers={"Retry-After": str(krp.MIN_INTERVAL_SECONDS)})
    if verdict == krp.DB_BUSY:
        raise HTTPException(
            status_code=503, detail="일시적으로 사용할 수 없습니다.",
            headers={"Retry-After": str(krp.BUSY_RETRY_AFTER)})
    return raw


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except Exception:                                    # noqa: BLE001
        raise HTTPException(status_code=400, detail="본문을 읽을 수 없습니다.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="본문 형식이 올바르지 않습니다.")
    return data


@router.post("/tasks")
async def kr_poller_tasks(request: Request,
                          x_krp_timestamp: str | None = Header(default=None),
                          x_krp_nonce: str | None = Header(default=None),
                          x_krp_signature: str | None = Header(default=None)):
    """갱신 후보를 임대한다.

    응답에는 외부 호출에 필요한 최소값만 담는다 — owner·개인정보·SQL·DB 경로는
    나가지 않는다. referer는 URL이 아니라 `refererUid`만 주고 한국 쪽이 고정
    형식으로 조립한다(임의 URL을 건네면 그게 곧 SSRF 통로다).
    """
    raw = await _guard(request, _TASKS_PATH, "tasks", x_krp_timestamp,
                       x_krp_nonce, x_krp_signature)
    body = _json(raw)
    limit = body.get("limit", krp.BATCH_MAX)
    # 인증된 상대라도 손상된 값을 그대로 int()에 넣지 않는다 — 500이 나면 그 자체로
    # 가용성 문제이고 traceback이 새면 정보 노출이다.
    n = krp.safe_int(limit, lo=1, hi=krp.BATCH_MAX)
    if n is None:
        raise HTTPException(status_code=400, detail="limit 값이 올바르지 않습니다.")
    now = int(time.time())
    tasks = await krp.lease_tasks(now, n)
    return {"issuedAt": now, "leaseSeconds": krp.LEASE_SECONDS, "tasks": tasks}


@router.post("/results")
async def kr_poller_results(request: Request,
                            x_krp_timestamp: str | None = Header(default=None),
                            x_krp_nonce: str | None = Header(default=None),
                            x_krp_signature: str | None = Header(default=None)):
    """한국에서 관측한 결과를 반영한다. 검증은 전부 여기(서버)에서 한다."""
    raw = await _guard(request, _RESULTS_PATH, "results", x_krp_timestamp,
                       x_krp_nonce, x_krp_signature)
    body = _json(raw)
    results = body.get("results")
    if not isinstance(results, list):
        raise HTTPException(status_code=400, detail="results가 배열이 아닙니다.")
    if len(results) > krp.BATCH_MAX:
        raise HTTPException(status_code=400, detail="한 번에 보낼 수 있는 수를 넘었습니다.")
    out = await krp.apply_results(results, int(time.time()))
    return {"ok": True, **out}
