"""보안 헤더 미들웨어 + CORS/문서 노출 정책.

외부 보안 점검에서 나온 항목 중 '응답 헤더/노출 범위'에 해당하는 것들을 한 곳에 모았다.
인증 방식 자체(localStorage 토큰 → HttpOnly 쿠키)는 범위가 커서 여기서 다루지 않는다.

CSP는 처음부터 강제하지 않는다 — 이 사이트는 Google AdSense Auto ads를 쓰고 있어서
차단 정책을 바로 켜면 광고가 죽는다. 기본은 Report-Only로 두고, 실제 위반 보고를 본 뒤
CSP_ENFORCE=1 로 전환한다. frame-ancestors 는 Report-Only에서는 효력이 없으므로
클릭재킹 방어는 X-Frame-Options 로 따로 건다.
"""
import os

from starlette.middleware.base import BaseHTTPMiddleware

IS_PROD = os.getenv("ENV", os.getenv("RAILWAY_ENVIRONMENT", "")).lower() in (
    "production", "prod", "railway",
) or os.getenv("PRODUCTION", "").lower() in ("1", "true")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")

# 인증/관리 API가 신뢰하는 정확한 Origin 목록(부분 일치·정규식 금지, null 금지).
_DEFAULT_ORIGINS = [FRONTEND_URL, "https://nexbot.shop", "https://www.nexbot.shop"]
if not IS_PROD:
    _DEFAULT_ORIGINS += ["http://localhost:3000", "http://127.0.0.1:3000"]


def allowed_origins() -> list[str]:
    """CORS allowlist. CORS_ALLOW_ORIGINS 로 덮어쓸 수 있다(쉼표 구분)."""
    raw = os.getenv("CORS_ALLOW_ORIGINS", "")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()] or _DEFAULT_ORIGINS
    # 중복 제거하되 순서 유지. 빈 문자열과 "null"은 절대 넣지 않는다.
    seen, out = set(), []
    for o in origins:
        if o and o.lower() != "null" and o not in seen:
            seen.add(o)
            out.append(o)
    return out


# AdSense/Google 계열 + 치지직 이미지 CDN. 실제 위반 보고를 보고 좁혀 나간다.
_AD_HOSTS = (
    "https://pagead2.googlesyndication.com https://googleads.g.doubleclick.net "
    "https://tpc.googlesyndication.com https://www.googletagservices.com "
    "https://adservice.google.com https://ep1.adtrafficquality.google "
    "https://ep2.adtrafficquality.google https://fundingchoicesmessages.google.com"
)

CSP = (
    "default-src 'self'; "
    # Next.js와 AdSense가 인라인 스크립트를 쓴다 — 최종 enforce 전에 nonce로 좁힐 대상
    f"script-src 'self' 'unsafe-inline' 'unsafe-eval' {_AD_HOSTS}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    f"connect-src 'self' {_AD_HOSTS} https://api.chzzk.naver.com; "
    f"frame-src 'self' {_AD_HOSTS}; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

CSP_ENFORCE = os.getenv("CSP_ENFORCE", "").lower() in ("1", "true", "yes")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """모든 응답에 보안 헤더를 붙인다."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy",
                     "geolocation=(), microphone=(), camera=(), payment=(), usb=()")
        # Report-Only 에서는 frame-ancestors 가 무시되므로 클릭재킹은 이 헤더로 막는다
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if IS_PROD:
            # HTTPS 종단(Railway)에서만 의미가 있다
            h.setdefault("Strict-Transport-Security",
                         "max-age=31536000; includeSubDomains")
        key = "Content-Security-Policy" if CSP_ENFORCE else "Content-Security-Policy-Report-Only"
        h.setdefault(key, CSP)
        return response
