"""Discord 스노플레이크를 API 경계에서 문자열로 넘기기 위한 헬퍼.

왜 필요한가 — 실측(2026-08-01): `/api/admin/guilds`가 guild_id를 JSON number로
내보내자 브라우저에서 정밀도가 깎여(886237674665549865 → …549800) 서버 목록과
치지직 구독 조인이 **전부 실패**했다(관리자 패널의 모든 서버가 "연결 안 됨"으로
보였다). JavaScript의 안전 정수 한계는 2^53(9,007,199,254,740,992)인데
스노플레이크는 19자리라 항상 그 위다.

  1234567890123456789  →  1234567890123456768   (21 손실)

DB 컬럼은 INTEGER로 두어도 상관없다. **경계에서만 문자열로 바꾼다** — 값이
JSON number가 되는 순간 손실이 확정되고, 그 뒤로는 어떤 프론트 코드도 복구할 수
없기 때문이다(String()으로 감싸도 이미 깎인 값이다).
"""
from __future__ import annotations


def snowflake_str(value) -> str | None:
    """스노플레이크를 문자열로. NULL·0·빈 값은 None(=미설정)으로 정규화한다."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s == "0":
        return None
    return s
