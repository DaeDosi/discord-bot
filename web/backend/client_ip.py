"""프록시 뒤에서의 클라이언트 식별 — '몇 홉을 신뢰할지'를 명시적으로 정한다.

배경: Railway는 요청을 엣지 프록시가 받아 백엔드로 넘긴다. uvicorn의
`--forwarded-allow-ips` 기본값은 `127.0.0.1`이라 엣지 프록시 주소가 여기에 들어맞지
않고, 그래서 `request.client.host`에는 **프록시 내부 주소만** 찍힌다. 액세스 로그에서
사용자를 구분할 수 없었던 이유가 이것이다.

그렇다고 `--forwarded-allow-ips=*` 로 모든 X-Forwarded-For를 신뢰하면 안 된다.
XFF는 클라이언트가 자유롭게 위조할 수 있는 헤더라, 전면 신뢰는 곧 "아무나 남의
IP를 사칭할 수 있다"는 뜻이 된다(레이트리밋 우회·로그 오염).

그래서 여기서는 신뢰 범위를 IP 대역이 아니라 **홉 수**로 못 박는다.
XFF는 `클라이언트, 프록시1, 프록시2, ...` 순으로 왼쪽부터 쌓이므로, 우리 앞에 붙은
신뢰 가능한 프록시가 N개라면 진짜 클라이언트는 **오른쪽에서 N번째** 항목이다.
왼쪽 끝(`xff[0]`)은 클라이언트가 직접 써 넣을 수 있어 위조에 무방비다.

- `TRUSTED_PROXY_HOPS=0` (기본): 기존 동작과 동일하게 맨 앞 항목을 쓴다.
  위조 가능하지만 **레이트리밋의 판정 기준을 바꾸지 않기 위한 안전한 기본값**이다
  (홉 수를 잘못 잡으면 여러 사용자가 한 버킷을 공유해 정상 사용자가 429를 맞는다).
- `TRUSTED_PROXY_HOPS=1`: Railway 엣지 하나만 신뢰 — 오른쪽에서 1번째를 쓴다.

이 모듈은 식별자만 만든다. 실제 레이트리밋 판정(`rate_limit.py`)은 이번 변경에서
건드리지 않았다 — 429 회귀 위험을 지지 않기 위해서다.
"""
import hashlib
import os
import secrets
from datetime import date

# 우리 앞에 있는 '신뢰하는' 프록시 수. 0이면 레거시(맨 앞 항목) 동작.
TRUSTED_PROXY_HOPS = max(0, int(os.getenv("TRUSTED_PROXY_HOPS", "0")))

# IP 해시용 소금. 지정하지 않으면 프로세스마다 새로 만든다 — 재시작하면 과거 해시와
# 이어지지 않지만, 그 대신 소금이 유출될 경로 자체가 없다(원문 IP 역산 차단).
_SALT = os.getenv("CLIENT_IP_SALT") or secrets.token_hex(16)


def resolve(request) -> dict:
    """클라이언트 식별 정보를 만든다.

    반환값에는 **원문 IP를 넣지 않는다** — 로그·API 응답으로 새어 나가는 것을
    구조적으로 막기 위해서다. 필요한 것은 '같은 사람인지'뿐이라 해시로 충분하다.
    """
    xff = request.headers.get("x-forwarded-for") or ""
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    peer = request.client.host if request.client else ""

    if not parts:
        ip, source = peer, "peer"
    elif TRUSTED_PROXY_HOPS <= 0:
        ip, source = parts[0], "xff_first"
    elif len(parts) >= TRUSTED_PROXY_HOPS:
        ip, source = parts[-TRUSTED_PROXY_HOPS], "xff_hop"
    else:
        # 기대보다 홉이 적다 — 프록시 구성이 바뀌었거나 직접 들어온 요청이다.
        ip, source = parts[0], "xff_short"

    return {
        "id": hash_ip(ip),
        "source": source,
        "xffHops": len(parts),
        "peerIsPrivate": _is_private(peer),
    }


def hash_ip(ip: str) -> str:
    """IP를 날짜별로 회전하는 해시로 바꾼다.

    날짜를 섞으므로 어제의 해시와 오늘의 해시는 이어지지 않는다 — 장기 추적이
    불가능하도록 만드는 것이 목적이다. 관측에 필요한 건 '같은 1분 안에서 같은
    사람인가'뿐이라 이 정도 수명이면 충분하다.
    """
    if not ip:
        return "unknown"
    raw = f"{_SALT}:{date.today().isoformat()}:{ip}".encode()
    return hashlib.sha256(raw).hexdigest()[:12]


def _is_private(ip: str) -> bool:
    """사설/루프백 대역인지 — '프록시 주소만 보인다'는 진단을 데이터로 확인하려고 둔다."""
    if not ip:
        return False
    if ip.startswith(("10.", "127.", "192.168.", "169.254.", "::1", "fd", "fc")):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def browser_family(user_agent: str) -> str:
    """UA 전문을 남기지 않고 브라우저 종류만 남긴다(핑거프린팅 여지 축소)."""
    ua = (user_agent or "").lower()
    if not ua:
        return "none"
    for token in ("googlebot", "bingbot", "yeti", "bot", "crawler", "spider",
                  "curl", "python-httpx", "python-requests", "wget", "postman"):
        if token in ua:
            return "bot" if token in ("bot", "crawler", "spider") else token
    if "edg/" in ua:
        return "edge"
    if "opr/" in ua or "opera" in ua:
        return "opera"
    if "whale" in ua:
        return "whale"
    if "samsungbrowser" in ua:
        return "samsung"
    if "firefox" in ua:
        return "firefox"
    if "chrome" in ua or "crios" in ua:
        return "chrome"
    if "safari" in ua:
        return "safari"
    return "other"
