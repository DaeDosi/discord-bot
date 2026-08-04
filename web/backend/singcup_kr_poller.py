"""싱드컵 — 한국(AWS 서울) outbound poller의 Railway 측 로직.

**왜 필요한가.** 하트는 카드 응답의 `interaction.emotion.reactions`에 있고
조회수는 `content.vod.count`에 있다. `krOnlyViewing=true`(한국 전용 재생) 클립을
Railway 해외 리전에서 카드 API로 부르면 HTTP는 200인데 **`content.vod` 블록이
통째로 제거**된다. 하트 블록은 그대로 오므로 하트만 갱신되고 조회수는 계속
못 읽는다. 중요한 구분: **조회수가 0으로 응답된 것이 아니라 조회수 컨테이너가
응답에서 빠진 것**이다. 그래서 상태가 `observed_zero`(진짜 0)가 아니라
`unknown`이고, 0으로 저장해서는 안 된다. 한국에서 같은 API를 불러
`content.vod.count`를 받아야만 복구된다.

**구조.** 한국 호스트가 인바운드 서버 없이 Railway를 부른다.

    AWS 서울 EC2 ──서명──► POST .../kr-poller/tasks    → 후보 clip UID + lease
                 (치지직 detail/card를 한국 IP로 호출)
                 ──서명──► POST .../kr-poller/results  → 검증 후 기존 저장 경로

DB는 Railway만 만진다. AWS에는 DB 경로도 접속 문자열도 SQL도 가지 않고,
task에는 외부 호출에 필요한 최소값(clipUid/videoId/recId)만 실린다. 그 값들은
**이미 `singcup_clips`에 저장돼 있으므로 Railway가 task를 만들려고 치지직을
부를 필요가 없다**.

저장은 새 UPDATE 경로를 만들지 않고 기존 `singcup_clips._apply_metrics`를 쓴다 —
"읽은 필드만 쓴다"는 계약이 이미 그 안에 있고, 그것이 view_count의 유일한
writer라서 여기서 우회하면 계약이 두 벌이 된다.
"""
import asyncio
import hashlib
import hmac
import os
import secrets
import time

import singcup_clips as sc

from database import DB_PATH, get_db
from utils.db_write import db_write, db_write_isolated

_TRUE = {"1", "true", "yes", "on"}


def _log(payload: dict):
    print(f"[singcup_krp] {payload}", flush=True)


# ── 환경변수 파싱 ──────────────────────────────────────────────────────────
# **아래 상수들은 import 시점에 한 번 고정된다**(지연 조회가 아니다). 값을 바꾸려면
# 재배포가 필요하다. 대신 어떤 쓰레기 값이 들어와도 기동을 죽이지 않는다 —
# 이 모듈은 `main.py`가 import하므로 여기서 ValueError가 나면 **백엔드 전체가
# 뜨지 않는다**. 그래서 파싱 실패와 범위 이탈을 전부 기본값으로 흡수한다.
#
# 잘못된 값의 **원문은 로그에 남기지 않는다**. 환경변수에 실수로 다른 비밀이
# 들어갔을 때 그것이 로그로 새는 통로가 되기 때문이다 — 이름과 사유만 남긴다.
def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        _log({"event": "krp_bad_env", "level": "warning", "name": name,
              "reason": "not_an_integer", "using_default": default})
        return default
    if not (lo <= value <= hi):
        _log({"event": "krp_bad_env", "level": "warning", "name": name,
              "reason": "out_of_range", "min": lo, "max": hi,
              "using_default": default})
        return default
    return value


# batch 1~25 — 25는 한 회차(RuntimeMaxSec=300) 안에 끝나는 실측 상한이다.
BATCH_MAX = _int_env("SINGCUP_KRP_BATCH", 25, 1, 25)
# lease는 한국 쪽 한 회차보다 넉넉해야 하고, 너무 길면 실패한 클립이 오래 묶인다.
LEASE_SECONDS = _int_env("SINGCUP_KRP_LEASE_SECONDS", 600, 60, 3600)
# 시계 오차 허용. 너무 크면 재전송 창이 커진다.
SKEW_SECONDS = _int_env("SINGCUP_KRP_SKEW_SECONDS", 300, 30, 900)
# nonce 보관 기간. **skew의 2배 이상이어야 한다** — ts가 -skew ~ +skew 안에서
# 유효하므로 같은 요청이 최대 2*skew 뒤에도 서명상 유효할 수 있다. 보관이 그보다
# 짧으면 그 사이에 nonce가 지워져 재전송 창이 생긴다.
NONCE_TTL_SECONDS = max(_int_env("SINGCUP_KRP_NONCE_TTL_SECONDS", 600, 60, 7200),
                        2 * SKEW_SECONDS)
# 실패한 클립을 다시 물어보기까지의 간격.
COOLDOWN_SECONDS = _int_env("SINGCUP_KRP_COOLDOWN_SECONDS", 3600, 0, 86400)
# 인증에 성공한 요청에 대한 endpoint별 최소 간격(자체 스로틀).
# 정상 운영은 timer 10분 주기라 이 값의 20배 간격으로 온다 — 방해하지 않는다.
MIN_INTERVAL_SECONDS = _int_env("SINGCUP_KRP_MIN_INTERVAL_SECONDS", 30, 0, 600)
# lease 이력 보존 기간과 한 번에 지우는 최대 행 수(best-effort prune).
LEASE_RETENTION_DAYS = _int_env("SINGCUP_KRP_LEASE_RETENTION_DAYS", 14, 1, 365)
PRUNE_LIMIT = _int_env("SINGCUP_KRP_PRUNE_LIMIT", 500, 1, 5000)
PRUNE_INTERVAL_SECONDS = _int_env("SINGCUP_KRP_PRUNE_INTERVAL_SECONDS", 3600, 60, 86400)

# 입력 길이 상한 — 손상된 payload가 DB 조회 키로 그대로 들어가지 않게 한다.
MAX_ID_LENGTH = 64
MAX_BODY_BYTES = 65536


def secret() -> str:
    """전용 secret. `SINGCUP_ADMIN_SECRET`을 재사용하지 않는다 — 권한 범위가 다르다.

    이 셋(secret/enabled/allow_decrease)만 지연 조회다. 나머지 숫자 설정은 위에서
    import 시점에 고정된다.
    """
    return os.getenv("SINGCUP_KR_POLLER_SECRET", "")


def enabled() -> bool:
    """기본 false. 코드 배포와 기능 활성화를 분리해 롤백을 환경변수 하나로 끝낸다."""
    return os.getenv("SINGCUP_KRP_ENABLED", "false").strip().lower() in _TRUE


def allow_decrease() -> bool:
    return os.getenv("SINGCUP_KRP_ALLOW_DECREASE", "false").strip().lower() in _TRUE


# ── 입력 검증 ──────────────────────────────────────────────────────────────
def safe_int(value, *, lo: int, hi: int):
    """정수만 통과. `bool`은 거부한다 — 파이썬에서 bool은 int의 하위 타입이라
    그냥 두면 True가 1로 들어간다. 소수·문자열·NaN·과대값도 전부 거부한다.

    인증된 poller가 보낸 값이라도 무조건 믿지 않는다. 손상된 payload가 500을
    만들면 그 자체로 가용성 문제이고, traceback이 새면 정보 노출이다.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if lo <= value <= hi else None


def safe_id(value) -> str | None:
    """식별자 문자열. 타입·길이만 본다(내용은 DB 조회가 판정한다)."""
    if not isinstance(value, str):
        return None
    if not (1 <= len(value) <= MAX_ID_LENGTH):
        return None
    return value


# ── 서명 ───────────────────────────────────────────────────────────────────
def signing_string(ts, method: str, path: str, raw_body: bytes) -> str:
    """서명 대상 문자열. **AWS 쪽 구현과 한 글자도 달라지면 안 된다.**

    body는 **받은 바이트 그대로** 해시한다. 파싱 후 재직렬화하면 공백·키 순서가
    달라져 서명이 깨진다(그리고 그 실패는 재현이 어렵다).
    """
    return f"{ts}\n{method}\n{path}\n{hashlib.sha256(raw_body).hexdigest()}"


def sign(key: str, ts, method: str, path: str, raw_body: bytes) -> str:
    return hmac.new(key.encode(),
                    signing_string(ts, method, path, raw_body).encode(),
                    hashlib.sha256).hexdigest()


def verify(ts_raw: str | None, nonce: str | None, sig: str | None,
           method: str, path: str, raw_body: bytes, now: int) -> str:
    """검증에 실패하면 사유 문자열, 통과하면 빈 문자열.

    사유는 호출부가 로그로만 쓴다 — 응답 본문에 세부 사유를 실어 주면 서명 오라클이
    된다(어느 조건에서 막혔는지 알려주는 셈이다).

    **DB를 건드리지 않는다.** 인증 실패가 쓰기를 유발하면 secret 없는 상대가
    DB 부하를 만들 수 있다. nonce 기록은 이 검증을 통과한 뒤에만 한다.
    """
    key = secret()
    if not key:
        return "no_secret"
    if not ts_raw or not nonce or not sig:
        return "missing_header"
    try:
        ts = int(ts_raw)
    except (TypeError, ValueError):
        return "bad_timestamp"
    if abs(now - ts) > SKEW_SECONDS:
        return "expired_timestamp"
    if not (8 <= len(nonce) <= 128):
        return "bad_nonce"
    if not hmac.compare_digest(sig, sign(key, ts_raw, method, path, raw_body)):
        return "bad_signature"
    return ""


NONCE_NEW = "new"
NONCE_REPLAY = "replay"
NONCE_DB_BUSY = "database_busy_giveup"


async def consume_nonce(nonce: str, now: int) -> str:
    """`new` / `replay` / `database_busy_giveup` 중 하나.

    PRIMARY KEY 충돌 자체가 재전송 판정이다 — SELECT 후 INSERT로 나누면 두 요청이
    겹칠 때 둘 다 통과한다.

    **DB 잠금을 `replay`로 뭉개지 않는다.** 뭉개면 DB 경합이 인증 실패(401)로
    기록되어, 로그만 보면 누가 서명을 위조한 것처럼 보인다. 그래서 여기도
    스로틀과 같은 전용 연결·유한 예산을 쓴다(공유 연결 raw commit 금지).
    """
    hit = {"rows": 0}

    async def _work(conn):
        await conn.execute("DELETE FROM singcup_krp_nonce WHERE seen_at < ?",
                           (now - NONCE_TTL_SECONDS,))
        cur = await conn.execute(
            "INSERT OR IGNORE INTO singcup_krp_nonce (nonce, seen_at) VALUES (?,?)",
            (nonce, now))
        hit["rows"] = cur.rowcount

    ok = await db_write_isolated(
        DB_PATH, _work, what="krp_nonce",
        busy_timeout_ms=NONCE_BUSY_TIMEOUT_MS,
        attempts=NONCE_ATTEMPTS,
        budget_seconds=NONCE_BUDGET_MS / 1000.0,
        log=_log)
    if not ok:
        _log({"event": "krp_nonce_db_busy", "level": "warning"})
        return NONCE_DB_BUSY
    return NONCE_NEW if hit["rows"] == 1 else NONCE_REPLAY


# ── 자체 스로틀 ────────────────────────────────────────────────────────────
# `/api/internal/*`은 `rate_limit.py`의 대상이 아니다(그쪽은 공개 rising API 전용).
# 그래서 이 통로가 스스로 상한을 갖는다. `singcup_locks` 테이블은 재사용하되
# **`sc.acquire_named_lock`은 쓰지 않는다.**
#
# 그 함수는 자기 docstring이 밝히듯 "공유 연결에 직접 커밋"하고
# `database is locked`를 **예외로 그대로 올린다**(실측 2026-08-01, 4분 루프가
# 회차 시작과 동시에 죽은 지점이다). 핫 패스(`acquire_clip_lock`)의 P0 순서
# 계약 때문에 그 함수 자체를 고칠 수도 없다. 그대로 쓰면 인증까지 통과한 poller
# 요청이 DB가 잠긴 순간 500이 되고, 과거에 수집을 멈췄던 잠금 경로를 신규 API에
# 다시 연결하는 셈이 된다.
#
# 그래서 poller 전용으로 `db_write_isolated`(전용 연결 · 짧은 busy_timeout ·
# 제한된 attempts · 절대 deadline · 모든 경로에서 rollback/close)를 쓴다.
# 잠금은 예외가 아니라 **판정 결과**로 돌아온다.
ACQUIRED = "acquired"
ALREADY_HELD = "already_held"
DB_BUSY = "database_busy_giveup"

# 스로틀은 요청 경로에 있다 — 예산을 짧게 잡아 응답이 늘어지지 않게 한다.
THROTTLE_BUSY_TIMEOUT_MS = _int_env("SINGCUP_KRP_THROTTLE_BUSY_MS", 300, 50, 2000)
THROTTLE_ATTEMPTS = _int_env("SINGCUP_KRP_THROTTLE_ATTEMPTS", 2, 1, 5)
THROTTLE_BUDGET_MS = _int_env("SINGCUP_KRP_THROTTLE_BUDGET_MS", 800, 100, 5000)
# DB가 바쁠 때 돌려줄 짧고 고정된 재시도 안내(초).
BUSY_RETRY_AFTER = _int_env("SINGCUP_KRP_BUSY_RETRY_AFTER", 5, 1, 60)

# 결과 반영 경로의 clip lock도 같은 이유로 전용 연결을 쓴다(아래 참조).
CLIP_LOCK_BUSY_TIMEOUT_MS = _int_env("SINGCUP_KRP_CLIP_LOCK_BUSY_MS", 300, 50, 2000)
CLIP_LOCK_ATTEMPTS = _int_env("SINGCUP_KRP_CLIP_LOCK_ATTEMPTS", 2, 1, 5)
CLIP_LOCK_BUDGET_MS = _int_env("SINGCUP_KRP_CLIP_LOCK_BUDGET_MS", 800, 100, 5000)

# nonce 기록도 전용 연결이다 — DB 잠금을 '재전송'으로 오인하면 안 된다.
NONCE_BUSY_TIMEOUT_MS = _int_env("SINGCUP_KRP_NONCE_BUSY_MS", 300, 50, 2000)
NONCE_ATTEMPTS = _int_env("SINGCUP_KRP_NONCE_ATTEMPTS", 2, 1, 5)
NONCE_BUDGET_MS = _int_env("SINGCUP_KRP_NONCE_BUDGET_MS", 800, 100, 5000)


async def throttle_acquire(bucket: str) -> str:
    """`acquired` / `already_held` / `database_busy_giveup` 중 하나.

    **DB busy를 `already_held`로 뭉개지 않는다.** 둘은 운영상 전혀 다른 사건이다 —
    앞은 정상적인 호출 간격 제한이고 뒤는 DB 경합이다. 뭉개면 잠금 문제가
    "poller가 너무 자주 부른다"로 보여 진단이 어긋난다.

    획득한 락은 **놓아주지 않는다**. TTL 만료가 곧 다음 창이다.
    """
    if MIN_INTERVAL_SECONDS <= 0:
        return ACQUIRED
    name = f"krp_rate:{bucket}"
    now = int(time.time())
    token = secrets.token_hex(6)
    hit = {"rows": 0}

    async def _work(conn):
        # INSERT OR IGNORE + 조건부 UPDATE를 **하나의 짧은 트랜잭션**으로.
        # rowcount가 곧 판정이라 check-then-set 경합이 없다.
        await conn.execute(
            "INSERT OR IGNORE INTO singcup_locks (name, locked_until, owner) "
            "VALUES (?,0,'')", (name,))
        cur = await conn.execute(
            "UPDATE singcup_locks SET locked_until=?, owner=? "
            "WHERE name=? AND locked_until < ?",
            (now + MIN_INTERVAL_SECONDS, token, name, now))
        hit["rows"] = cur.rowcount

    # 잠금이면 False(예외 없음), 잠금이 아닌 예외는 그대로 올라온다 —
    # 조용히 429/503으로 위장하지 않는다. 취소도 그대로 전파된다.
    ok = await db_write_isolated(
        DB_PATH, _work, what=f"krp_throttle({bucket})",
        busy_timeout_ms=THROTTLE_BUSY_TIMEOUT_MS,
        attempts=THROTTLE_ATTEMPTS,
        budget_seconds=THROTTLE_BUDGET_MS / 1000.0,
        log=_log)
    if not ok:
        _log({"event": "krp_throttle_db_busy", "level": "warning",
              "bucket": bucket})
        return DB_BUSY
    return ACQUIRED if hit["rows"] == 1 else ALREADY_HELD


# ── 결과 반영용 clip lock (poller 전용) ────────────────────────────────────
# 스로틀과 **정확히 같은 이유**로 전용 연결을 쓴다. `sc.acquire_clip_lock`은
# `acquire_named_lock` → 공유 연결 raw commit 경로라, 잠금 예외를 잡아 판정으로
# 바꾸더라도 **execute/commit 중간에 끊긴 공유 연결에 미완료 트랜잭션이 남지
# 않는다는 보장이 없다**. 공유 연결은 봇·백엔드의 다른 모든 쓰기가 함께 쓰므로
# 거기에 트랜잭션이 걸려 있으면 피해가 이 기능 밖으로 번진다.
#
# 락 **이름과 TTL은 기존과 동일**하다(`singcup_clip:{uid}`, `CLIP_LOCK_TTL`).
# 그래야 자동 스윕·관리자 단건 갱신과 같은 자원을 두고 **상호 배제**된다.
# 저장소는 그대로 두고 연결만 분리하는 것이 요점이다.
CLIP_ACQUIRED = "acquired"
CLIP_HELD = "already_held"
CLIP_DB_BUSY = "database_busy_giveup"


async def clip_lock_acquire(clip_uid: str) -> tuple[str, str | None]:
    """(판정, token). 판정이 `acquired`일 때만 token이 있다."""
    name = sc.clip_lock_name(clip_uid)           # 이름 규칙은 기존 헬퍼를 그대로 쓴다
    now = int(time.time())
    token = secrets.token_hex(6)
    hit = {"rows": 0}

    async def _work(conn):
        await conn.execute(
            "INSERT OR IGNORE INTO singcup_locks (name, locked_until, owner) "
            "VALUES (?,0,'')", (name,))
        cur = await conn.execute(
            "UPDATE singcup_locks SET locked_until=?, owner=? "
            "WHERE name=? AND locked_until < ?",
            (now + sc.CLIP_LOCK_TTL, token, name, now))
        hit["rows"] = cur.rowcount

    ok = await db_write_isolated(
        DB_PATH, _work, what=f"krp_clip_lock({clip_uid})",
        busy_timeout_ms=CLIP_LOCK_BUSY_TIMEOUT_MS,
        attempts=CLIP_LOCK_ATTEMPTS,
        budget_seconds=CLIP_LOCK_BUDGET_MS / 1000.0,
        log=_log)
    if not ok:
        return CLIP_DB_BUSY, None
    return (CLIP_ACQUIRED, token) if hit["rows"] == 1 else (CLIP_HELD, None)


async def clip_lock_release(clip_uid: str, token: str | None) -> bool:
    """해제 성공이면 True. DB가 바쁘면 False(경고만) — TTL이 대신 회수한다.

    해제 실패로 **본문의 성공한 저장을 뒤집지 않는다.** 락은 시한부이므로
    최악의 경우에도 `CLIP_LOCK_TTL` 뒤에 저절로 풀린다.
    """
    if not token:
        return True
    name = sc.clip_lock_name(clip_uid)

    async def _work(conn):
        await conn.execute(
            "UPDATE singcup_locks SET locked_until=0, owner='' "
            "WHERE name=? AND owner=?", (name, token))

    ok = await db_write_isolated(
        DB_PATH, _work, what=f"krp_clip_unlock({clip_uid})",
        busy_timeout_ms=CLIP_LOCK_BUSY_TIMEOUT_MS,
        attempts=CLIP_LOCK_ATTEMPTS,
        budget_seconds=CLIP_LOCK_BUDGET_MS / 1000.0,
        log=_log)
    if not ok:
        _log({"event": "krp_lock_release_deferred", "level": "warning",
              "clip_uid": clip_uid})
    return ok


# ── 후보 선정 ──────────────────────────────────────────────────────────────
# "하트는 정상인데 조회수는 한 번도 못 받았다"가 지역 차단의 지문이다.
# 카드가 아예 실패했다면 하트도 없었을 것이고, 하트가 있다는 것은 200을 받았다는
# 뜻이며, 그런데도 조회수를 한 번도 못 받았다는 것은 content.vod가 계속 없었다는
# 뜻이다. `view:no_vod` 사유 자체는 로그에만 남고 컬럼에 없어서 이 조합으로 대신한다.
#
# `last_view_at > 0 AND view_count = 0`(observed_zero)은 **정상 관측된 진짜 0**이라
# 반드시 제외한다. 여기를 느슨하게 잡으면 멀쩡한 관측을 덮어쓰게 된다.
_CANDIDATE_SQL = """
    SELECT c.clip_uid, c.video_id, c.rec_id
      FROM singcup_clips c
      LEFT JOIN singcup_kr_poller_lease l
             ON l.clip_uid = c.clip_uid AND l.done_at = 0 AND l.expires_at > ?
      LEFT JOIN (SELECT clip_uid, MAX(done_at) done_at
                   FROM singcup_kr_poller_lease
                  -- 쿨다운은 **실제로 답을 받아 본 실패**에만 건다. 'expired'는
                  -- 한국 쪽이 보고를 못 한 것(재배포·중지 등)이라 벌을 줄 이유가
                  -- 없다 — 그러면 프로세스가 한 번 죽을 때마다 한 시간을 잃는다.
                  WHERE last_result NOT IN ('ok', 'expired')
                  GROUP BY clip_uid) f
             ON f.clip_uid = c.clip_uid AND f.done_at > ?
     WHERE c.event_id = ?
       AND c.active = 1
       AND c.deletion_state <> 'confirmed_deleted'
       AND c.last_attempt_at > 0
       AND c.last_heart_at > 0 AND c.heart_count > 0
       AND c.last_view_at = 0 AND c.view_count = 0
       AND l.clip_uid IS NULL
       AND f.clip_uid IS NULL
     ORDER BY c.heart_count DESC, c.clip_uid ASC
     LIMIT ?
"""

# 후보 조회와 lease 발급이 같은 쓰기 트랜잭션 안에서 끝나야 중복 발급이 없다.
# 프로세스 안에서는 아래 락이, 프로세스 밖(다중 replica)에서는
# `idx_krp_lease_one_open` 부분 유니크가 같은 것을 보장한다.
_lease_gate = asyncio.Lock()
_last_prune_at = 0.0


async def lease_tasks(now: int, limit: int) -> list[dict]:
    """후보를 골라 lease를 발급한다. 반환값에는 외부 호출 최소값만 담는다."""
    global _last_prune_at
    n = max(1, min(BATCH_MAX, int(limit or BATCH_MAX)))
    out: list[dict] = []
    prune = (now - _last_prune_at) >= PRUNE_INTERVAL_SECONDS

    async with _lease_gate:
        async def _work(db):
            # 만료된 열린 lease를 먼저 닫는다 — 별도 reaper 없이 여기서 회수된다.
            await db.execute(
                "UPDATE singcup_kr_poller_lease SET done_at=?, last_result='expired' "
                "WHERE done_at=0 AND expires_at<=?", (now, now))
            if prune:
                # best-effort. **열린 lease는 절대 지우지 않고**(done_at=0 제외)
                # 보존 기간이 지난 닫힌 행만 상한을 두고 지운다. 전체 scan을 피하려고
                # done_at 인덱스를 타는 조건만 쓴다. 재전송 판정은 lease 만료
                # (기본 600초)보다 훨씬 오래된 행만 지우므로 idempotency를 깨지 않는다.
                await db.execute(
                    "DELETE FROM singcup_kr_poller_lease WHERE task_id IN ("
                    "  SELECT task_id FROM singcup_kr_poller_lease"
                    "   WHERE done_at > 0 AND done_at < ? LIMIT ?)",
                    (now - LEASE_RETENTION_DAYS * 86400, PRUNE_LIMIT))
            rows = await (await db.execute(
                _CANDIDATE_SQL,
                (now, now - COOLDOWN_SECONDS, sc.EVENT_ID, n))).fetchall()
            for r in rows:
                task_id = secrets.token_hex(16)
                token = secrets.token_hex(16)
                try:
                    await db.execute(
                        "INSERT INTO singcup_kr_poller_lease (task_id, event_id,"
                        " clip_uid, lease_token, issued_at, expires_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (task_id, sc.EVENT_ID, r["clip_uid"], token, now,
                         now + LEASE_SECONDS))
                except Exception:                       # noqa: BLE001
                    # 부분 유니크에 걸렸다 = 다른 replica가 방금 임대했다. 건너뛴다.
                    continue
                out.append({"taskId": task_id, "leaseToken": token,
                            "clipUid": r["clip_uid"],
                            "videoId": r["video_id"] or "",
                            "recId": r["rec_id"] or "",
                            "refererUid": r["clip_uid"],
                            "expiresAt": now + LEASE_SECONDS})

        # prune·만료정리·발급이 한 트랜잭션이라, DB 잠금이면 셋 다 취소되고 이번
        # 회차는 task 없이 끝난다(다음 회차가 가져간다). **그 경우와 "후보가 정말
        # 없어서 빈 응답"을 로그에서 구분할 수 있어야 한다** — 아니면 잠금으로
        # 일이 멈춘 것이 "할 일 없음"으로 보인다. 건수·상태만 남기고 후보 UID는
        # 남기지 않는다.
        if not await db_write(get_db, _work, what="krp_lease", log=_log):
            _log({"event": "krp_lease_write_failed", "level": "warning",
                  "pruned_in_same_tx": prune, "issued": 0})
            return []
    if prune:
        _last_prune_at = now
    if out:
        _log({"event": "krp_tasks_issued", "count": len(out)})
    else:
        _log({"event": "krp_no_candidates", "count": 0})
    return out


async def _issue_lease_for_test(clip_uid: str, now: int) -> dict:
    """테스트 전용 — 후보 조건을 우회해 lease 하나를 만든다.

    freshness·단조성처럼 "이미 observed인 클립"에서만 검증할 수 있는 계약이 있어서
    필요하다. production 경로에서는 호출하지 않는다.
    """
    task_id, token = secrets.token_hex(16), secrets.token_hex(16)

    async def _work(db):
        await db.execute(
            "INSERT INTO singcup_kr_poller_lease (task_id, event_id, clip_uid,"
            " lease_token, issued_at, expires_at) VALUES (?,?,?,?,?,?)",
            (task_id, sc.EVENT_ID, clip_uid, token, now, now + LEASE_SECONDS))

    await db_write(get_db, _work, what="krp_lease_test", log=_log)
    return {"taskId": task_id, "leaseToken": token, "clipUid": clip_uid,
            "expiresAt": now + LEASE_SECONDS}


# ── 결과 반영 ──────────────────────────────────────────────────────────────
async def _close_lease(task_id: str, now: int, result: str):
    async def _work(db):
        await db.execute(
            "UPDATE singcup_kr_poller_lease SET done_at=?, last_result=?, "
            "attempts=attempts+1 WHERE task_id=?", (now, result, task_id))

    await db_write(get_db, _work, what="krp_lease_close", log=_log)


async def _bump_attempt(task_id: str):
    async def _work(db):
        await db.execute(
            "UPDATE singcup_kr_poller_lease SET attempts=attempts+1 "
            "WHERE task_id=?", (task_id,))

    await db_write(get_db, _work, what="krp_lease_attempt", log=_log)


async def repick_representatives(owners: set, now: int) -> int:
    """저장된 클립의 **owner만** 대표를 다시 고른다. 바뀐 수를 돌려준다.

    전체 `recompute_ranking()`을 부르지 않는 이유는 그 함수가 참가자 전원의 채널
    API를 훑기 때문이다(실측 169.692초). 대표 선정에 필요한 것은 **DB 안의 현재
    metrics와 override뿐**이므로 여기서는 외부 호출이 **0건**이고, 대상도 이번
    batch가 실제로 전진시킨 owner(최대 batch 크기)로 한정된다.

    선정 규칙은 새로 쓰지 않고 canonical 함수(`_build_reps` → `pick_representative`)
    를 그대로 재사용한다 — 규칙을 복제하면 그 복제본이 갈라진다(split-brain).

    팔로워·닉네임·`tagged_clip_count` 같은 무관한 집계는 건드리지 않는다. 그것들은
    조회수 복구와 무관하고 정기 경로가 갱신한다.
    """
    if not owners:
        return 0
    marks = ",".join("?" * len(owners))
    owner_list = list(owners)
    # **재조회와 쓰기가 한 트랜잭션 안에 있어야 한다.** 밖에서 읽고 안에서 쓰면
    # 그 사이에 전체 `recompute_ranking()`이 대표를 확정해 버리고, 이쪽이 곧바로
    # 옛 값으로 덮어쓴다(TOCTOU). `db_write()`는 `shared_write_lock()` 안에서
    # `fn(db)`와 `commit()`을 함께 돌리므로, 이 함수 본문 전체가 그 임계구역이 된다.
    # 전체 recompute도 같은 락을 쓴다 — 두 경로가 서로 끼어들 수 없다.
    # 외부 API 호출은 여기에 없다(대표 선정은 DB만 본다).
    outcome: dict = {"changed": [], "prev": {}}

    async def _work(conn):
        rows = [dict(r) for r in await (await conn.execute(
            "SELECT * FROM singcup_clips WHERE event_id=? AND active=1 "
            f"AND deletion_state<>? AND owner_channel_id IN ({marks})",
            (sc.EVENT_ID, sc.DEL_CONFIRMED, *owner_list))).fetchall()]
        if not rows:
            return
        # 유효한 수동 지정은 언제나 최우선이다. 무효 override(삭제·비활성·owner
        # 불일치)는 후보 목록에 없으므로 `pick_representative`가 자동 규칙으로
        # 조용히 복귀시킨다 — 그 계약을 여기서 다시 구현하지 않는다.
        overrides = await sc._representative_overrides()
        reps = {r["owner_channel_id"]: r for r in sc._build_reps(rows, overrides)}
        cur = {r["channel_id"]: r["representative_clip_uid"]
               for r in await (await conn.execute(
                   "SELECT channel_id, representative_clip_uid FROM singcup_streamers "
                   f"WHERE event_id=? AND channel_id IN ({marks})",
                   (sc.EVENT_ID, *owner_list))).fetchall()}
        changed = []
        for owner, rep in reps.items():
            prev = cur.get(owner)
            # 아직 스트리머 행이 없으면 정기 경로가 만든다 — 여기서 만들지 않는다
            # (이름·팔로워를 모르는 채로 행을 만들면 화면에 '-'로 뜬다).
            if prev is None or prev == rep["clip_uid"]:
                continue
            changed.append((rep["clip_uid"], now, sc.EVENT_ID, owner))
        if not changed:
            return                            # 바뀐 게 없으면 쓰지 않는다
        # 대표에 필요한 컬럼만 쓴다. follower_count·channel_name·tagged_clip_count는
        # 건드리지 않는다 — 이 경로는 그 값들의 최신본을 갖고 있지 않다.
        await conn.executemany(
            "UPDATE singcup_streamers SET representative_clip_uid=?, row_updated_at=? "
            "WHERE event_id=? AND channel_id=?", changed)
        outcome["changed"] = changed
        outcome["prev"] = cur

    if not await db_write(get_db, _work, what="krp_repick", log=_log):
        # 쓰지 못했어도 **저장된 지표는 그대로다.** 대표는 다음 정기 회차가 맞춘다.
        _log({"event": "krp_repick_db_busy", "level": "warning",
              "owners": len(owner_list)})
        return 0

    for uid, _ts, _ev, owner in outcome["changed"]:
        # 기존 관측 로그 계약을 그대로 쓴다 — 대표 변경은 한 이름으로만 보여야 한다.
        _log({"event": "representative_clip_changed", "owner_channel_id": owner,
              "from_clip_uid": outcome["prev"].get(owner), "to_clip_uid": uid,
              "source": "kr_poller"})
    return len(outcome["changed"])


async def apply_results(items: list, now: int) -> dict:
    """제출된 관측을 검증하고 기존 저장 경로로 반영한다.

    한 건의 실패가 다른 건을 되돌리지 않도록 **클립 단위로 독립**시킨다. 대신 각
    저장은 그 자체로 원자적이다(`_apply_metrics`의 UPDATE 한 번).

    `recompute_ranking`과 캐시 무효화는 **batch 전체가 끝난 뒤 각각 1회**만 한다.
    재계산은 외부 채널 API를 부르므로 클립마다 돌리면 비용이 선형으로 늘고, 저장이
    한 건도 없었으면 아예 부를 이유가 없다.
    """
    accepted = 0
    stored = 0
    rejected: list[dict] = []
    # 실제로 **저장된** 클립의 owner만 모은다. accepted no-op(재전송)·rejected·
    # stale·lock 충돌은 DB를 전진시키지 않았으므로 대표를 다시 고를 이유가 없다.
    stored_owners: set[str] = set()

    for item in (items or []):
        if not isinstance(item, dict):
            rejected.append({"clipUid": "", "reason": "malformed_item"})
            continue
        task_id = safe_id(item.get("taskId"))
        uid = safe_id(item.get("clipUid"))
        token = safe_id(item.get("leaseToken"))
        if task_id is None or uid is None:
            rejected.append({"clipUid": "", "reason": "malformed_item"})
            continue
        db = await get_db()
        lease = await (await db.execute(
            "SELECT * FROM singcup_kr_poller_lease WHERE task_id=?",
            (task_id,))).fetchone()
        if lease is None:
            rejected.append({"clipUid": uid, "reason": "unknown_task"})
            continue
        if lease["clip_uid"] != uid:
            rejected.append({"clipUid": uid, "reason": "clip_mismatch"})
            continue
        # leaseToken은 "이 task를 실제로 발급받은 쪽인가"를 묻는다. 서명은 요청
        # 단위 인증이라, 서명 키를 가진 쪽이 **자기가 받지 않은 taskId**에 결과를
        # 밀어 넣는 것까지는 막지 못한다. 그래서 task 단위로 한 겹 더 묶는다.
        # 실패는 저장도, lease 종료도, attempt 증가도 하지 않는다 —
        # 잘못된 token으로 남의 lease를 소진시킬 수 있으면 그것이 곧 공격이다.
        # 바이트로 비교한다 — `compare_digest`는 비ASCII 문자열을 받으면
        # TypeError를 낸다(손상된 payload 하나가 500이 된다).
        if token is None or not hmac.compare_digest(
                token.encode("utf-8", "surrogatepass"),
                str(lease["lease_token"] or "").encode()):
            # token 값은 로그에도 응답에도 남기지 않는다.
            rejected.append({"clipUid": uid, "reason": "bad_lease_token"})
            continue
        if int(lease["done_at"] or 0) > 0:
            # 같은 결과가 다시 왔다. 오류가 아니라 no-op이다(네트워크 재전송).
            accepted += 1
            continue
        if int(lease["expires_at"] or 0) <= now:
            await _close_lease(task_id, now, "expired")
            rejected.append({"clipUid": uid, "reason": "lease_expired"})
            continue

        # 조회수를 못 받은 회차는 **저장하지 않는다**. 0으로 바꾸면 unknown이
        # observed_zero로 굳어 순위에 진짜 0으로 들어간다.
        status = safe_int(item.get("httpStatus"), lo=0, hi=599)
        if status != 200 or str(item.get("viewState") or "") != "observed":
            await _close_lease(task_id, now, "no_view")
            rejected.append({"clipUid": uid, "reason": "no_view"})
            continue
        view = sc.valid_count(item.get("viewCount"))
        if view is None:
            await _close_lease(task_id, now, "invalid_view")
            rejected.append({"clipUid": uid, "reason": "invalid_view"})
            continue
        observed_at = sc.valid_count(item.get("observedAt"))
        if observed_at is None:
            await _close_lease(task_id, now, "invalid_observed_at")
            rejected.append({"clipUid": uid, "reason": "invalid_observed_at"})
            continue

        # **전용 연결 경로**를 쓴다. 기존 `sc.acquire_clip_lock`은 공유 연결에
        # 직접 커밋해서, 잠금 예외를 잡아 판정으로 바꾸더라도 공유 연결에
        # 미완료 트랜잭션이 남지 않는다는 보장이 없다. 락 이름·TTL은 같으므로
        # 자동 스윕·관리자 단건 갱신과는 그대로 상호 배제된다.
        verdict, lock = await clip_lock_acquire(uid)
        if verdict == CLIP_HELD:
            # 자동 스윕이나 관리자 단건 갱신이 이 클립을 잡고 있다. 실패로 굳히지
            # 않는다 — lease를 열어 둔 채 두면 만료 후 자연히 재시도된다.
            await _bump_attempt(task_id)
            rejected.append({"clipUid": uid, "reason": "locked"})
            continue
        if verdict == CLIP_DB_BUSY:
            # DB가 바쁘다. 여기서 attempts까지 올리려 하면 그 쓰기도 같은 경합에
            # 부딪혀 응답만 늘어진다 — lease를 그대로 두고 물러난다.
            rejected.append({"clipUid": uid, "reason": "db_locked"})
            continue
        try:
            row = await (await db.execute(
                "SELECT view_count, last_view_at, owner_channel_id "
                "FROM singcup_clips WHERE clip_uid=?", (uid,))).fetchone()
            if row is None:
                await _close_lease(task_id, now, "missing_clip")
                rejected.append({"clipUid": uid, "reason": "missing_clip"})
                continue
            cur_view = int(row["view_count"] or 0)
            cur_at = int(row["last_view_at"] or 0)
            if observed_at <= cur_at:
                # 늦게 도착한 옛 관측이 최신값을 되돌리면 안 된다.
                await _close_lease(task_id, now, "stale")
                rejected.append({"clipUid": uid, "reason": "stale_observation"})
                continue
            if view < cur_view and not allow_decrease():
                await _close_lease(task_id, now, "decrease")
                rejected.append({"clipUid": uid, "reason": "decrease"})
                continue

            async def _work(_db, _uid=uid, _view=view):
                # heart_ok=False — 하트는 Railway가 이미 정상 수집한다. 여기서 받은
                # heartCount는 교차검증 참고용이라 덮어쓰지 않는다.
                await sc._apply_metrics(_uid, 0, _view, False, True, now)

            if not await db_write(get_db, _work, what=f"krp_apply({uid})", log=_log):
                await _bump_attempt(task_id)
                rejected.append({"clipUid": uid, "reason": "db_locked"})
                continue
        finally:
            # 해제도 전용 연결이다. DB가 바빠 못 풀어도 **성공한 저장을 뒤집지
            # 않는다** — 락은 시한부라 CLIP_LOCK_TTL 뒤 저절로 회수된다.
            await clip_lock_release(uid, lock)

        await _close_lease(task_id, now, "ok")
        accepted += 1
        stored += 1
        if row["owner_channel_id"]:
            stored_owners.add(str(row["owner_channel_id"]))
        _log({"event": "krp_view_recovered", "clip_uid": uid,
              "view_from": cur_view, "view_to": view})

    if stored:
        # **여기서 `recompute_ranking()`을 부르지 않는다.** 실측(2026-08-04 운영):
        # 25건 저장은 약 1초에 끝났는데 응답은 169.692초 걸렸고, 한국 poller가
        # 60초에 끊겨 결과가 불명확한 성공(ambiguous success)이 됐다. 저장은 이미
        # 끝난 뒤였으므로 데이터는 멀쩡했지만 그 회차는 통째로 헛돌았다.
        #
        # 병목은 `recompute_ranking()` 안의
        # `asyncio.gather(*[load_channel(...) for r in ranked])`다 — 참가자
        # **전원**(약 1,400명)의 채널 API를 `CARD_CONCURRENCY`(4)로 부른다.
        # 조회수 25건을 반영하려고 참가자 전원의 팔로워를 다시 읽을 이유가 없다.
        #
        # 재계산을 건너뛰어도 화면은 맞는다: `_load_main_uncached()`가
        # `singcup_clips.view_count`를 **직접 읽고** `compute_scores()`를 조회
        # 시점에 돌린다. 즉 캐시만 버리면 다음 요청이 새 조회수로 점수·순위를
        # 다시 만든다(실측: 김 재 우 view 0→1945, viewScore 0.0→2.86,
        # rank 188→94, 대표 clipUid는 그대로).
        #
        # `recompute_ranking()`의 고유 산출물(대표 재선정·스트리머 upsert·
        # 스냅샷·급상승)은 조회수 복구와 무관하며, 주기 경로가 확실히 수행한다 —
        # `singcup_sweep.run_cycle()`이 회차 완료마다 `save_snapshot=True`로
        # 부르고(연속 사이클), discover·recheck 등 코드 여러 곳이 더 있다.
        #
        # `asyncio.create_task`로 뒤로 미루지 않는 이유: 이 프로세스는 재배포로
        # 언제든 죽고(실측: 스윕 회차가 두 번 고아가 됐다) 그러면 그 태스크는
        # 흔적 없이 사라진다. 주기 경로에 맡기는 편이 유실 지점이 없다.
        #
        # 다만 **대표 재선정만은 미루지 않는다.** 주기 경로의 recompute는 전부
        # 조건부라(discover는 `if tagged`, hourly snapshot은 '5단계 전부 성공'),
        # 무조건 도는 것은 스윕 회차뿐이고 그건 80~100분이다. 그동안 `/main`과
        # 스윕 `is_rep`가 다른 대표를 볼 수 있다. 아래는 이번에 저장된 owner만
        # 보는 가벼운 경로로 외부 호출이 0건이다.
        await repick_representatives(stored_owners, now)
        sc.invalidate_main_cache()

    # `recomputed`는 계약 유지를 위해 남긴다. 이 경로는 재계산을 하지 않으므로
    # 항상 False다 — 순위는 다음 정기 회차가 맞춘다.
    return {"accepted": accepted, "stored": stored, "rejected": rejected,
            "recomputed": False}
