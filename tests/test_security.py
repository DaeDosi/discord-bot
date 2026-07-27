"""보안 회귀 테스트 — 헤더 / CORS allowlist / 문서 노출 / 인증 상태코드.

외부 점검에서 나온 항목이 배포 중에 조용히 되돌아가지 않게 고정한다.
"""
import importlib.util
import os
import sys

import pytest
from fastapi.testclient import TestClient

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "web", "backend")


def _load_app(**env):
    """환경변수를 바꿔 백엔드 앱을 새로 로드한다(보안 설정이 import 시점에 굳는다).

    모듈명 'main'은 저장소 루트의 봇 main.py와 겹치므로 반드시 파일 경로로 로드한다.
    """
    old = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items() if v is not None})
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
    for mod in ("backend_main", "security", "timing", "rate_limit"):
        sys.modules.pop(mod, None)
    try:
        spec = importlib.util.spec_from_file_location(
            "backend_main", os.path.join(BACKEND, "main.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules["backend_main"] = module
        spec.loader.exec_module(module)
        return module.app
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for mod in ("backend_main", "security", "timing", "rate_limit"):
            sys.modules.pop(mod, None)


# TestClient를 컨텍스트 매니저(with)로 쓰면 lifespan이 실행돼 수집기 백그라운드 태스크가
# 전부 뜬다(치지직/네이버 실호출 + 무한 루프). 미들웨어·라우팅만 검증하면 되므로
# lifespan 없이 클라이언트만 만든다. DB 초기화는 db 픽스처가 이미 해 준다.
@pytest.fixture
def prod_client(db):
    return TestClient(_load_app(PRODUCTION="1", FRONTEND_URL="https://nexbot.shop",
                                CORS_ALLOW_ORIGINS="https://nexbot.shop"))


@pytest.fixture
def dev_client(db):
    return TestClient(_load_app(PRODUCTION=None, ENV="development",
                                RAILWAY_ENVIRONMENT=None,
                                FRONTEND_URL="http://localhost:3000",
                                CORS_ALLOW_ORIGINS=None))


# ── 보안 헤더 ───────────────────────────────────────────────────────────────
def test_security_headers_present(prod_client):
    r = prod_client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in r.headers["Permissions-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"          # 클릭재킹
    assert "max-age=" in r.headers["Strict-Transport-Security"]


def test_csp_starts_as_report_only(prod_client):
    r = prod_client.get("/")
    # AdSense가 있으므로 기본은 Report-Only. 강제는 CSP_ENFORCE=1 로 전환한다.
    assert "Content-Security-Policy-Report-Only" in r.headers
    assert "Content-Security-Policy" not in r.headers
    csp = r.headers["Content-Security-Policy-Report-Only"]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "googlesyndication.com" in csp                   # 광고 도메인 allowlist


def test_csp_can_be_enforced(db):
    c = TestClient(_load_app(PRODUCTION="1", CSP_ENFORCE="1"))
    r = c.get("/")
    assert "Content-Security-Policy" in r.headers
    assert "Content-Security-Policy-Report-Only" not in r.headers


def test_server_timing_header(prod_client):
    r = prod_client.get("/")
    assert "total;dur=" in r.headers.get("Server-Timing", "")


# ── 문서 노출 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_hidden_in_production(prod_client, path):
    assert prod_client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_api_docs_available_in_dev(dev_client, path):
    assert dev_client.get(path).status_code == 200


# ── CORS allowlist ──────────────────────────────────────────────────────────
def test_allowed_origin_gets_cors_header(prod_client):
    r = prod_client.get("/api/rising/status", headers={"Origin": "https://nexbot.shop"})
    assert r.headers.get("access-control-allow-origin") == "https://nexbot.shop"


@pytest.mark.parametrize("origin", [
    "https://evil.example.com",
    "https://nexbot.shop.evil.com",        # 부분 일치 공격
    "http://nexbot.shop",                  # 스킴 불일치
    "null",                                # sandboxed iframe
])
def test_disallowed_origins_get_no_cors_header(prod_client, origin):
    r = prod_client.get("/api/rising/status", headers={"Origin": origin})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_preflight_from_disallowed_origin_is_rejected(prod_client):
    r = prod_client.options("/api/guilds", headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "GET",
    })
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_wildcard_origin_is_gone(prod_client):
    r = prod_client.get("/api/rising/status", headers={"Origin": "https://nexbot.shop"})
    assert r.headers.get("access-control-allow-origin") != "*"


# ── 인증 상태 코드 ──────────────────────────────────────────────────────────
def test_missing_auth_returns_401_not_422(prod_client):
    # 예전에는 Header(...) 때문에 422(검증 오류)가 나왔다
    r = prod_client.get("/api/guilds")
    assert r.status_code == 401


def test_malformed_bearer_returns_401(prod_client):
    assert prod_client.get("/api/guilds",
                           headers={"Authorization": "Basic abc"}).status_code == 401
    assert prod_client.get("/api/guilds",
                           headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_internal_timing_requires_secret(prod_client):
    # secret 미설정 배포에서는 아예 막힌다
    assert prod_client.get("/api/internal/timing").status_code in (401, 503)


# ── 방문자 집계 남용 방어 ───────────────────────────────────────────────────
def test_visit_endpoint_is_rate_limited(prod_client):
    codes = [prod_client.post("/api/stats/visit").status_code for _ in range(12)]
    assert 429 in codes, "무인증 POST /api/stats/visit 에 촘촘한 레이트 리밋이 필요하다"


# ── 프론트엔드 정적 검사 ────────────────────────────────────────────────────
def test_callback_no_longer_accepts_token_query_param():
    """?token= 폴백이 남아 있으면 로그·Referer·히스토리에 토큰이 남는다."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "web" / "frontend" / "app" / "callback" / "page.tsx").read_text(encoding="utf-8")
    assert 'params.get("token")' not in src
    assert 'hashParams.get("token")' in src
