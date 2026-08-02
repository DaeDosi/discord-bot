"""Uvicorn access log에서 민감한 query 값을 가린다.

배경(실측): OAuth 인가 코드 교환은 `GET /auth/callback?code=...&state=...`로 돌아온다.
uvicorn은 기본값으로 access log를 켜고, 그 포맷의 request line에는 **쿼리 스트링이
그대로** 들어간다. 그래서 일회용 코드와 CSRF state가 운영 로그에 평문으로 남았다.

access log를 끄는 선택지는 쓰지 않는다. 경로·상태 코드·응답 시간은 장애 진단의 기본
관측 수단이고, 그것까지 잃으면 보안 하나 고치려다 운영을 못 보게 된다. 대신 **값만**
가린다.

## 왜 정규식으로 URL 전체를 치환하지 않는가

uvicorn이 남기는 레코드는 이런 모양이다(`uvicorn/protocols/http/*_impl.py`):

    access_logger.info('%s - "%s %s HTTP/%s" %d',
                       client_addr, method, path_with_query, http_version, status)

즉 `record.args`는 **5-tuple**이고 쿼리는 **인덱스 2 하나에만** 있다
(`uvicorn/logging.py`의 `AccessFormatter.formatMessage`가 같은 순서로 언팩한다).
포맷된 메시지 문자열을 통째로 정규식 치환하면 client_addr이나 status까지 건드릴 위험이
있고, 무엇보다 값이 이미 한 문자열로 합쳐진 뒤라 어디까지가 쿼리인지 다시 추측해야 한다.
인덱스 2만 바꾸면 나머지 네 값은 손대지 않는다는 것이 구조적으로 보장된다.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote_plus

# 대소문자를 무시하고 비교한다. 값이 아니라 **key**만 본다 — 값에 'code'가 들어 있다고
# 가리면 정상 쿼리가 깨진다.
SENSITIVE_QUERY_KEYS = frozenset({
    "code", "state", "token", "access_token", "refresh_token", "id_token",
    "client_secret", "secret", "authorization", "password", "api_key",
})

REDACTED = "[REDACTED]"
# 예상 밖 레코드를 만났을 때 대신 남기는 **정적** 문자열. 원본 msg/args/repr을
# 절대 여기에 끼워 넣지 않는다 — 그 안에 무엇이 들었는지 모르는 것이 문제의 전부다.
UNEXPECTED = "[REDACTED_ACCESS_LOG_UNEXPECTED_FORMAT]"

_ACCESS_LOGGER = "uvicorn.access"
# 같은 필터를 두 번 붙이면 두 번 돈다(결과는 같지만 낭비이고, 로거를 재설정하는
# 테스트에서 누적된다). 인스턴스에 표식을 달아 두고 설치 시 확인한다.
_MARKER = "_nexbot_query_redaction"


def _is_sensitive(raw_key: str) -> bool:
    """percent-encoded key도 잡는다. `%63ode=`처럼 인코딩해 필터를 피하는 형태가
    실제로 존재하고, 서버는 어차피 디코딩해서 읽는다."""
    try:
        decoded = unquote_plus(raw_key)
    except Exception:
        decoded = raw_key
    return decoded.strip().lower() in SENSITIVE_QUERY_KEYS


def redact_query(full_path: str) -> str:
    """`/path?a=1&code=xyz` → `/path?a=1&code=[REDACTED]`.

    쿼리가 없으면 입력을 그대로 돌려준다(로그를 건드리지 않는 것이 기본값이다).

    민감하지 않은 pair는 **원문 그대로** 되돌린다 — 순서도 인코딩도 바꾸지 않는다.
    `parse_qsl`로 파싱해 다시 조립하면 `+`와 `%20`이 뒤섞이고 빈 값이 사라져,
    로그를 눈으로 비교하던 사람이 다른 요청이라고 오해하게 된다.
    """
    head, sep, query = full_path.partition("?")
    if not sep or not query:
        return full_path
    # fragment는 서버에 오지 않는다(브라우저가 떼고 보낸다). 없는 것을 새로 만들지
    # 않도록 여기서도 `#`를 특별 취급하지 않는다.
    out = []
    for pair in query.split("&"):
        # `key`(값 없음), `key=`(빈 값), `key=value` 세 가지가 모두 온다.
        raw_key, eq, _raw_value = pair.partition("=")
        if _is_sensitive(raw_key):
            # 값이 없던 pair에도 `=`를 붙여 '가려졌다'는 사실이 보이게 한다.
            out.append(f"{raw_key}={REDACTED}")
        else:
            out.append(pair)
    return f"{head}?{'&'.join(out)}"


def _blank(record: logging.LogRecord) -> None:
    """레코드를 정적 안전 문자열로 통째로 교체한다(fail-closed의 최종 수단)."""
    record.msg = UNEXPECTED
    record.args = None


class QueryRedactionFilter(logging.Filter):
    """access 레코드의 `args[2]`(쿼리 포함 경로)만 가린다.

    **핸들러가 아니라 로거에 붙인다.** 로거 필터는 어떤 핸들러보다 먼저 돌기 때문에,
    핸들러가 여러 개여도 원문이 다른 핸들러로 새지 않는다. 핸들러마다 붙이는 방식은
    나중에 추가되는 핸들러를 놓친다.

    ## 모르는 구조는 통과시키지 않는다

    예전 구현은 `args`가 5-tuple이 아니면 그대로 `True`를 반환했다. 그건 uvicorn이
    포맷을 바꾸거나 예상 밖 레코드가 들어오는 순간 **보안 필터가 조용히 꺼지는**
    fail-open이다. 이 필터는 `uvicorn.access` 로거에만 붙고 그 로거는 요청 라인만
    내보내므로, 우리가 아는 모양이 아니면 그 레코드에 무엇이 들었는지 알 수 없다.
    그래서 원문 대신 정적 문자열로 갈아 끼운다. 진단 정보를 잃더라도 자격증명을
    흘리지 않는 쪽을 택한다 — 포맷이 바뀐 사실 자체는 이 문자열이 뜨는 것으로 안다.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            args = record.args
            if (not isinstance(args, tuple) or len(args) != 5
                    or not isinstance(args[2], str)):
                _blank(record)
                return True
            full_path = args[2]
            if "?" not in full_path:
                return True          # 쿼리가 없으면 가릴 것도 없다
            record.args = (args[0], args[1], redact_query(full_path), args[3], args[4])
        except Exception:
            # 경로 문자열을 이해하지 못했다는 뜻이다. 경로는 살리고 쿼리만 통째로
            # 가려 보되, 그 시도조차 실패하면 레코드를 통째로 비운다. 어느 경로에서도
            # 원본 URL·msg·args·repr을 다시 로그에 넣지 않는다.
            try:
                args = record.args
                if (isinstance(args, tuple) and len(args) == 5
                        and isinstance(args[2], str)):
                    head = args[2].partition("?")[0]
                    record.args = (args[0], args[1], f"{head}?{REDACTED}",
                                   args[3], args[4])
                else:
                    _blank(record)
            except Exception:
                try:
                    _blank(record)
                except Exception:
                    pass
        # 필터는 레코드를 거르는 용도가 아니다. 어떤 경우에도 access log를 삼키지 않는다.
        return True


def install_query_redaction(logger_name: str = _ACCESS_LOGGER) -> bool:
    """`uvicorn.access` 로거에 필터를 한 번만 설치한다.

    이미 설치돼 있으면 아무것도 하지 않고 False를 돌려준다 — 테스트가 모듈을 여러 번
    import하거나 reload할 때 필터가 쌓이지 않게 한다.
    """
    logger = logging.getLogger(logger_name)
    if any(getattr(f, _MARKER, False) for f in logger.filters):
        return False
    f = QueryRedactionFilter()
    setattr(f, _MARKER, True)
    logger.addFilter(f)
    return True
