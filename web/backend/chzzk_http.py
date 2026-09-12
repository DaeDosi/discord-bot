"""치지직 공개 API 전용 **공유 HTTP 클라이언트**의 수명 관리.

## 왜 필요한가

`httpx.AsyncClient()` 생성은 **동기**다. SSL 컨텍스트와 전송 계층을 만드는 데
이 환경 실측 기준 **264~346ms**가 걸리고, 그동안 **이벤트 루프가 통째로 멈춘다**
(실측: cold 요청 중 loop lag 311.9ms ≈ 생성 315.6ms, 아무 일도 안 할 때의 바닥값은 13.5ms).
`/api/rising/newcomers`는 캐시가 만료될 때마다 이 생성을 한 번씩 했다.

그래서 **요청 경로가 아니라 애플리케이션 수명주기에서 한 번만** 만들고 공유한다.

## 범위를 넓히지 않는다

이 저장소에는 `httpx.AsyncClient()` 생성 지점이 40곳 넘게 있다(디스코드 OAuth,
관리자 API, 봇 cog, relay 등). **그것들은 건드리지 않는다.** 이 모듈은 치지직 공개
API의 enrich 경로 하나만 대상으로 한다 — 대상 호스트가 고정이고, 요청별 비밀
(쿠키·Authorization)을 싣지 않으며, 호출 빈도가 높아 이득이 분명한 경로다.

## 클라이언트에 상태를 싣지 않는다

`headers`·`cookies`를 **기본값으로 두지 않는다.** 헤더와 타임아웃은 호출부가
요청마다 넘긴다(`_fetch_channel_meta`가 `headers=HEADERS, timeout=8`). 그래야 한
요청의 값이 다음 요청으로 새지 않는다. `follow_redirects`·`verify`·`trust_env`·
프록시·HTTP/2도 전부 httpx 기본값 그대로다 — 기존 `httpx.AsyncClient()`와 동일하다.
바뀌는 것은 **누가 언제 만들고 닫는가** 하나뿐이다.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx

log = logging.getLogger(__name__)

#: 현재 살아 있는 공유 클라이언트와 그것이 만들어진 이벤트 루프.
#: 모듈 import 시점에는 **아무것도 만들지 않는다** — 루프가 없는 시점에 만든
#: 클라이언트는 다른 루프에서 쓰면 깨지고, import 부작용으로 자원이 생기면
#: 테스트·스크립트가 예측 불가능해진다.
_client: httpx.AsyncClient | None = None
_loop: asyncio.AbstractEventLoop | None = None
_warned = False


class _NoStoreCookies(httpx.Cookies):
    """응답의 `Set-Cookie`를 **저장하지 않는** 쿠키 저장소."""

    def extract_cookies(self, response) -> None:      # noqa: D102 — 의도적 no-op
        return None


class _ChzzkAsyncClient(httpx.AsyncClient):
    """치지직 enrich 전용 클라이언트 — **쿠키를 담지 않는다.**

    왜 필요한가: httpx 기본 클라이언트는 응답의 `Set-Cookie`를 클라이언트에 쌓아
    두고 다음 요청에 실어 보낸다. 요청마다 새로 만들 때는 클라이언트가 곧 버려져
    무해했지만, **수명주기 내내 공유**하면 한 번 받은 쿠키가 그 뒤 모든 요청에
    붙는다 — 요청 사이에 상태가 새는 것이다. 이 경로는 쿠키가 필요 없다.

    구현 주의: `httpx.AsyncClient(cookies=...)`로는 막을 수 없다. httpx가 생성자에서
    `Cookies(cookies)`로 **복사**하므로 서브클래스 인스턴스가 버려진다. 그래서
    `cookies` 프로퍼티 자체를 덮는다(httpx는 저장·전송 모두 이 프로퍼티를 쓴다).
    `tests/test_chzzk_http_lifecycle.py`가 실제 응답으로 이 동작을 고정하므로,
    httpx가 내부 구조를 바꾸면 그 테스트가 먼저 깨진다.
    """

    def __init__(self, *a, **kw) -> None:
        # httpx는 `__init__`에서 `self._cookies = Cookies(...)`를 **직접** 대입하므로
        # 아래 setter가 안 불릴 수 있다. 먼저 만들어 둔다.
        self._no_store_cookies = _NoStoreCookies()
        super().__init__(*a, **kw)

    @property
    def cookies(self) -> httpx.Cookies:
        return self._no_store_cookies

    @cookies.setter
    def cookies(self, value) -> None:
        self._no_store_cookies = _NoStoreCookies()


def _new_client() -> httpx.AsyncClient:
    """기존 `httpx.AsyncClient()`와 같은 설정 + 쿠키 비저장.

    `timeout`·`follow_redirects`·`verify`·`trust_env`·프록시·HTTP/2는 전부 httpx
    기본값 그대로다(헤더와 타임아웃은 호출부가 요청마다 넘긴다). 공유하기 때문에
    새로 필요해진 것은 쿠키 비저장 하나뿐이다.
    """
    return _ChzzkAsyncClient()


def get_client() -> httpx.AsyncClient | None:
    """지금 이 루프에서 **안전하게 쓸 수 있는** 공유 클라이언트, 없으면 None.

    다음 경우에는 공유하지 않고 None을 준다 — 호출부가 임시 클라이언트를 만든다.

    * 수명주기가 붙지 않은 앱/스크립트(단위 테스트에서 라우터 함수를 직접 부르는 경우)
    * **다른 이벤트 루프**에서 만들어진 클라이언트(pytest는 테스트마다 루프를 새로
      만든다 — 이전 루프의 커넥션 풀을 그대로 쓰면 조용히 깨진다)
    * 이미 닫힌 클라이언트
    """
    if _client is None:
        return None
    if _client.is_closed:
        return None
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        return None
    if running is not _loop:
        return None
    return _client


def warn_once_if_missing() -> None:
    """공유 클라이언트가 없어 임시 생성으로 물러설 때 **한 번만** 알린다.

    조용히 물러서면 수명주기 연결이 빠진 배포에서 성능 회귀가 눈에 띄지 않는다.
    """
    global _warned
    if not _warned:
        _warned = True
        log.warning("[chzzk-http] 공유 클라이언트가 없어 요청 경로에서 새로 만든다 "
                    "— 앱 수명주기에 chzzk_client_lifespan이 연결됐는지 확인할 것")


@asynccontextmanager
async def chzzk_client_lifespan(app=None):
    """앱 수명주기 하나당 클라이언트 하나를 만들고, 끝날 때 **정확히 한 번** 닫는다.

    `app`을 주면 `app.state.chzzk_http`에도 실어 둔다(앱별로 무엇이 붙어 있는지
    확인할 수 있게). 중첩/연속 실행에서도 이전 값을 복원하므로, 서로 다른 앱
    수명주기는 **서로 다른 클라이언트**를 쓴다.

    생성에 실패하면 예외를 그대로 올린다 — 반쪽 상태로 서비스하지 않는다.
    """
    global _client, _loop
    prev_client, prev_loop = _client, _loop
    client = _new_client()
    _client, _loop = client, asyncio.get_running_loop()
    if app is not None:
        app.state.chzzk_http = client
    try:
        yield client
    finally:
        _client, _loop = prev_client, prev_loop
        if app is not None and getattr(app.state, "chzzk_http", None) is client:
            app.state.chzzk_http = None
        try:
            await client.aclose()
        except Exception:                 # noqa: BLE001 — 종료 경로는 막지 않는다
            log.exception("[chzzk-http] 공유 클라이언트 종료 실패")


def reset_state_for_tests() -> None:
    """테스트 전용 — 모듈 전역을 비운다(클라이언트를 닫지는 않는다)."""
    global _client, _loop, _warned
    _client, _loop, _warned = None, None, False
