import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from log_redaction import install_query_redaction
from rate_limit import RateLimitMiddleware
from security import IS_PROD, SecurityHeadersMiddleware, allowed_origins
from timing import ServerTimingMiddleware, stats_snapshot

# OAuth 콜백의 `?code=`·`?state=`가 uvicorn access log에 평문으로 남던 것을 막는다.
# **import 시점**에 설치한다. lifespan도 첫 요청보다는 먼저 돌지만, import 시점이면
# 이 모듈이 로드되는 모든 경로(테스트·`uvicorn main:app` 모두)에서 동일하게 보장되고
# 첫 콜백 요청부터 확실히 걸린다. 봇 프로세스는 이 모듈을 import하지 않으므로 영향이
# 없고, 설치 함수가 중복 추가를 스스로 막는다(reload·반복 import에서 누적되지 않는다).
install_query_redaction()

load_dotenv()

# 프로젝트 루트를 sys.path에 추가 — 봇과 스키마를 공유하는 루트의 database 모듈을 사용
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from urllib.parse import quote

from auth import FRONTEND_URL, create_jwt, exchange_code, get_discord_user, verify_oauth_state
from chzzk_channel_history import start_history_backfill
from chzzk_monitor import start_monitor
from rising_collector import start_collector
from routers.admin_router import router as admin_router
from routers.auth_router import router as auth_router
from routers.chzzk_auth_router import router as chzzk_auth_router
from routers.chzzk_history_router import router as chzzk_history_router
from routers.chzzk_router import router as chzzk_router
from routers.guilds_router import router as guilds_router
from routers.kr_poller_router import router as kr_poller_router  # noqa: E402
from routers.points_router import router as points_router
from routers.rising_router import router as rising_router
from routers.settings_router import router as settings_router
from routers.singcup_router import router as singcup_router
from routers.stats_router import router as stats_router
from routers.verify_router import router as verify_router
from routers.account_router import router as account_router
from routers.account_router import support_router
from singcup_clips import (
    start_backfill_worker,
    start_clip_collector,
    start_snapshot_publisher,
)
from singcup_audit import start_audit_worker
from singcup_sweep import start_sweep_worker
from singcup_collector import ADMIN_SECRET, start_singcup_collector
from singcup_retention import start_retention_worker

# 이 파일의 import는 전부 위쪽 `sys.path.insert` 뒤에 와야 해서 E402가 난다.
# 기존 줄들의 기존 오류는 이번 작업에서 건드리지 않고, 새로 들이는 이 한 줄만
# 정확히 표시해 신규 유입을 0으로 유지한다(파일 전체 noqa는 쓰지 않는다).
import singcup_final  # noqa: E402

from database import close_db, get_db, init_db


async def _ensure_final_ranking() -> None:
    """이벤트가 끝났고 확정본이 없으면 한 번 만든다.

    실패해도 서비스는 계속 떠야 한다 — 확정본이 없으면 프론트가 기존 `/main`
    경로로 물러서므로 화면이 비지 않는다.
    """
    try:
        res = await singcup_final.ensure_finalized(source="startup")
        if res.get("created"):
            logging.getLogger(__name__).info("[singcup-final] 확정본 생성 %s", res)
    except Exception:                     # noqa: BLE001 — 기동을 막지 않는다
        logging.getLogger(__name__).exception("[singcup-final] 확정본 생성 실패")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(start_monitor())
    asyncio.create_task(start_collector())
    # 첫 방송일 백필 — '신규 & 초기 분석'의 60일 필터가 성립하려면 채널마다 한 번씩
    # first_live_date를 채워 둬야 한다. 요청 경로에서 모으면 첫 방문자가 다 기다린다.
    asyncio.create_task(start_history_backfill())
    # 싱드컵 이벤트 수집 — 이벤트 기간에만 돌고, 여러 replica가 떠도 DB 락으로
    # 한 번에 하나만 실행된다. 실패해도 메인 통계 서비스에는 영향이 없다.
    asyncio.create_task(start_singcup_collector())
    # 싱드컵 클립(메인/랭킹) — 신규 탐색 전용 루프(지표 갱신은 아래 정각 스윕이 맡는다)
    asyncio.create_task(start_clip_collector())
    # 지표 전체 갱신 — KST 매시 정각 1회차로 대표·일반 클립 전부를 한 번씩 훑는다.
    # 예전처럼 4분마다 상위 N건만 집으면 클립이 수천 건일 때 한 바퀴가 몇 시간이었다.
    asyncio.create_task(start_sweep_worker())
    # 삭제 클립 권위 감사 — 카드 API가 정상 응답해도 상세 API로 결국 한 번씩
    # 확인한다. 기본값은 OFF이고, 켜도 SHADOW가 기본이라 상태를 바꾸지 않는다
    # (SINGCUP_DELETION_RECONCILE_ENABLED / _SHADOW / _HOT_ENABLED / _COLD_ENABLED).
    asyncio.create_task(start_audit_worker())
    # 과거 적재는 성격이 달라 별도 워커가 완료될 때까지 연속 처리한다(커서는 DB에 저장)
    asyncio.create_task(start_backfill_worker())
    # 분리 API 스냅샷 게시 — 평상시 갱신은 recompute_ranking이 맡고, 이 루프는
    # 재시작 직후 warm-up과 느린 안전망만 담당한다(조회 경로는 생산하지 않는다).
    asyncio.create_task(start_snapshot_publisher())
    # 보존정책 유지보수 — 기본은 dry-run이라 아무것도 지우지 않는다.
    # 실제 삭제는 SINGCUP_SNAPSHOT_PRUNE_ENABLED=true + DRY_RUN=false 일 때만.
    asyncio.create_task(start_retention_worker())
    # 비공식 인기점수 랭킹 확정본 — 이벤트가 끝났는데 확정본이 아직 없을 때만
    # 한 번 만든다. 이미 있으면 아무것도 하지 않으므로 재시작마다 순위가 달라지지
    # 않는다. **수집은 멈추지 않는다** — 얼리는 것은 랭킹 화면이 받는 응답 하나뿐이고,
    # 공식 예선 참가자 화면은 계속 최신 지표를 쓴다.
    asyncio.create_task(_ensure_final_ranking())
    yield
    # 종료 훅 — 미커밋 트랜잭션을 남긴 채 프로세스가 사라지면 같은 파일을 쓰는
    # 봇 프로세스가 그 잠금에 걸린다. 되돌린 뒤 연결을 닫는다.
    try:
        conn = await get_db()
        await conn.rollback()
    except Exception:                     # noqa: BLE001 — 종료 경로는 막지 않는다
        pass
    try:
        await close_db()
    except Exception:                     # noqa: BLE001
        pass


# 프로덕션에서는 API 문서를 노출하지 않는다 — 전체 엔드포인트 목록과 스키마가
# 그대로 공개되면 공격 표면 파악이 쉬워진다. 개발 환경에서는 그대로 쓴다.
app = FastAPI(
    title="Discord Bot Dashboard API",
    lifespan=lifespan,
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
)

# 레이트 리밋 — /api/rising/* 는 인증이 없는 공개 API라 남용 방어가 필요하다.
# CORS보다 먼저 add_middleware 하면 바깥쪽(=나중 실행)이 되므로, 429 응답에도
# CORS 헤더가 붙도록 CORS를 나중에 추가한다(Starlette는 나중에 추가한 것이 바깥쪽).
app.add_middleware(RateLimitMiddleware)

# CORS — 예전에는 allow_origins=["*"] 였다. JWT를 헤더로 보내므로 브라우저가 남의
# 쿠키를 실어 보내지는 않지만, 임의 사이트가 사용자의 토큰을 탈취했을 때 그대로
# 우리 API를 부를 수 있고 관리자 API까지 열려 있었다. 정확한 Origin allowlist로 좁힌다.
# (부분 일치·정규식·null origin은 쓰지 않는다. security.allowed_origins 참고)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Singcup-Secret"],
    max_age=600,
)

# 보안 헤더는 가장 바깥에 둬서 CORS/레이트리밋이 만든 응답에도 붙게 한다
app.add_middleware(SecurityHeadersMiddleware)
# 계측은 최외곽 — 미들웨어 체인 전체를 포함한 실제 응답 시간을 재야 한다
app.add_middleware(ServerTimingMiddleware)


@app.get("/api/internal/timing")
async def internal_timing(x_singcup_secret: str | None = Header(default=None)):
    """경로별 P50/P95/P99. secret으로 보호한다(운영 진단용)."""
    import hmac
    if not ADMIN_SECRET:
        raise HTTPException(status_code=503, detail="ADMIN secret이 설정되지 않았습니다.")
    if not x_singcup_secret or not hmac.compare_digest(
            x_singcup_secret.encode(), ADMIN_SECRET.encode()):
        raise HTTPException(status_code=401, detail="인증에 실패했습니다.")
    return stats_snapshot()

app.include_router(auth_router)
app.include_router(guilds_router)
app.include_router(settings_router)
# chzzk_history_router 를 chzzk_router 보다 먼저 등록한다 — 같은 /api/chzzk prefix를
# 쓰므로, 리터럴 경로(/channel-history)가 /{guild_id}/... 패턴보다 앞서 매칭되게 한다.
app.include_router(chzzk_history_router)
app.include_router(chzzk_router)
app.include_router(stats_router)
app.include_router(verify_router)
app.include_router(chzzk_auth_router)
app.include_router(admin_router)
app.include_router(points_router)
app.include_router(rising_router)
app.include_router(singcup_router)
# 한국(AWS 서울) outbound poller 전용. secret 미설정이거나 SINGCUP_KRP_ENABLED가
# false면 두 endpoint가 503만 돌려주므로, 등록 자체는 조건 없이 해 둔다 —
# 폐기된 relay_router처럼 import 시점 결합으로 기동을 죽이지 않기 위해서다.
app.include_router(kr_poller_router)
# 계정 설정·회원탈퇴 요청과 수정 요청 접수.
app.include_router(account_router)
app.include_router(support_router)


@app.get("/auth/callback")
async def auth_callback_compat(code: str = None, state: str = None, error: str = None):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/login?error={quote(error)}")
    if not verify_oauth_state(state):
        return RedirectResponse(f"{FRONTEND_URL}/login?error=invalid_state")
    if not code:
        return RedirectResponse(f"{FRONTEND_URL}/login?error=no_code")
    try:
        token_data   = await exchange_code(code)
        access_token = token_data["access_token"]
        user         = await get_discord_user(access_token)
        jwt_token    = create_jwt(
            user_id=user["id"],
            username=user["username"],
            avatar=user.get("avatar", ""),
            access_token=access_token,
        )
        # URL 프래그먼트(#)로 전달 — 쿼리스트링과 달리 서버 접근 로그/Referer에 남지 않음
        return RedirectResponse(f"{FRONTEND_URL}/callback#token={jwt_token}")
    except Exception as e:
        return RedirectResponse(f"{FRONTEND_URL}/login?error={quote(str(e))}")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Discord Bot Dashboard API"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
