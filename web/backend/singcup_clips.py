"""싱드컵 — 치지직 음악/노래 카테고리의 `#싱드컵` 태그 클립 수집·집계.

자유게시판 수집기(`singcup_collector.py`)와는 **별개 데이터**다.
- 이 파일: 메인/랭킹의 근거가 되는 클립(하트·조회수 → 비공식 예상 인기점수)
- singcup_collector: '자유게시판 홍보글' 보조 화면(버프)

두 개의 비공식 API를 쓴다.

1) 클립 목록 (커서 페이지네이션)
   GET api.chzzk.naver.com/service/v1/categories/ETC/music/clips
       ?filterType=ALL&orderType=RECENT&size=50[&clipUID=<next cursor>]
   다음 커서는 content.page.next.clipUID.

2) 클립 카드 (태그·하트·조회수) — 클립 1건당 1회
   GET api-videohub.naver.com/shortformhub/feeds/v5/card?... (Referer 필요)
   태그   card.content.description        -> (^|\\s)#싱드컵(?=\\s|$)
   하트   card.interaction.emotion.reactions[reactionType=="like"].count
   조회수 card.content.vod.count

**공식 순위가 아니다.** 남/여 솔로·그룹 파트 구분과 네이버폼 제출 여부는 공개 데이터로
알 수 없으므로 구현하지 않고, 태그 클립 전체를 하나의 통합 풀로 계산한다.
"""
import asyncio
import contextlib
import contextvars
import hashlib
import json
import math
import os
import random
import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import aiosqlite
import httpx
from singcup_collector import (
    END_AT,
    EVENT_ID,
    ST_BLOCKED,
    ST_FAILED,
    ST_OK,
    ST_SCHEMA,
    ST_SKIPPED,
    START_AT,
    SchemaError,
    event_status,
    metrics_refresh_open,
    registration_open,
    snapshot_refresh_open,
)

from database import DB_PATH, get_db
from utils.db_write import _rollback as _db_rollback
from utils.db_write import db_write, db_write_isolated, shared_write_lock

CLIPS_API = "https://api.chzzk.naver.com/service/v1/categories/ETC/music/clips"
CARD_API = "https://api-videohub.naver.com/shortformhub/feeds/v5/card"
CHANNEL_API = "https://api.chzzk.naver.com/service/v1/channels"

_KST = timezone(timedelta(hours=9))

PAGE_SIZE = int(os.getenv("SINGCUP_CLIP_PAGE_SIZE", "50"))
# 이벤트 시작(07-20)까지 거슬러 가려면 실측 113페이지가 필요했다(클립 5,650건).
# 여유를 두고 200으로 잡는다 — 목록 조회는 카드와 달리 페이지당 1회라 비용이 작다.
MAX_PAGES = int(os.getenv("SINGCUP_CLIP_MAX_PAGES", "200"))
CARD_CONCURRENCY = int(os.getenv("SINGCUP_CARD_CONCURRENCY", "4"))
REQUEST_TIMEOUT = float(os.getenv("SINGCUP_REQUEST_TIMEOUT_MS", "10000")) / 1000
MAX_RETRIES = max(1, int(os.getenv("SINGCUP_MAX_RETRIES", "3")))
BACKOFF_BASE = float(os.getenv("SINGCUP_BACKOFF_BASE_SECONDS", "1"))
BACKOFF_MAX = float(os.getenv("SINGCUP_BACKOFF_MAX_SECONDS", "30"))
PAGE_DELAY = float(os.getenv("SINGCUP_PAGE_DELAY_SECONDS", "0.3"))
# 채널 정보(팔로워)는 자주 안 변한다 — 채널당 이 주기로만 다시 부른다
CHANNEL_TTL_MINUTES = float(os.getenv("SINGCUP_CHANNEL_TTL_MINUTES", "20"))
MAX_RUN_SECONDS = int(os.getenv("SINGCUP_CLIP_MAX_RUN_SECONDS", "600"))
# (SINGCUP_MISSING_SCANS는 목록 미발견만으로 비활성화하던 시절의 값이다.
#  삭제 판정은 이제 상세 API 404 확인 횟수(SINGCUP_DELETION_CONFIRM_CHECKS)로
#  하고, missing_scan_count 컬럼은 그 확인 횟수를 담는다.)
# 특정 클립 1건만 '갱신 전 DB값 / 새로 읽은 값 / 갱신 후 DB값'을 통째로 로그에 남긴다.
# 값이 안 도는 클립을 지목해 추적할 때만 켠다(빈 값이면 꺼짐).
DEBUG_CLIP_UID = os.getenv("SINGCUP_METRICS_DEBUG_UID", "").strip()

# 정확히 '#싱드컵' 태그만 인정한다. 앞뒤가 공백(또는 문자열 끝)이어야 한다 —
# `[#싱드컵]`, `# 싱드컵`, `#싱드컵(커버)`처럼 다른 글자가 붙은 표기는 참가 태그로
# 보지 않는다(대회 규칙). 제목/본문에 '싱드컵'이라는 낱말만 있는 것도 제외.
_TAG_RE = re.compile(r"(^|\s)#싱드컵(?=\s|$)")
# 재생 불가 상태 — 목록/카드에서 제외한다
_BAD_BLIND = {"BLIND", "DELETE", "DELETED", "PRIVATE"}

_HEADERS = {"User-Agent": os.getenv("SINGCUP_USER_AGENT", "NexBot-SingcupCollector/1.0"),
            "Accept": "application/json"}


# ── 회차 실행 컨텍스트 ──────────────────────────────────────────────────────
# 왜 필요한가 — 실측(2026-08-01): 4분 루프가 `database is locked`로 죽으면
# `loop_error detail="database is locked"` 한 줄만 남았다. 어느 단계인지도,
# 어떤 쓰기였는지도 알 수 없어 원인을 특정할 수 없었다(관측된 4건 전부 미상).
#
# 단계 이름만으로도 부족하다. `recompute_ranking`처럼 **여러 단계가 공유하는
# 쓰기 경로**가 있어서 같은 operation이 discover에서도 deletion에서도 나온다.
# 그래서 step과 operation을 나눠 둔다.
#
# 이 컨텍스트는 로그에만 쓴다 — 제어 흐름·트랜잭션·재시도는 건드리지 않는다.
_CYCLE: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "singcup_cycle", default=None)

# 이보다 오래 걸린 쓰기는 남긴다. "잠금을 맞은 작업"만 봐서는 범인을 못 찾는다 —
# 그 시각에 **락을 오래 쥐고 있던 작업**이 진짜 원인일 수 있다.
SLOW_WRITE_MS = int(os.getenv("SINGCUP_SLOW_WRITE_MS", "1000"))


# 이 이벤트가 회차 안에서 나오면 그 단계는 **부분 성공**이다. 예외 없이 끝났다는
# 이유로 success로 세면(실측: deletion이 owner lock을 놓쳐 클립을 건너뛰었는데
# steps_ok=6) 처리 못 한 일이 있었다는 사실이 통째로 사라진다.
# 여기(로그 한 곳)에서 판정하므로 새 giveup 경로가 생겨도 자동으로 잡힌다.
_PARTIAL_EVENTS = frozenset({
    "db_locked_giveup",
    "clip_deletion_skipped_owner_locked",
    "clip_deletion_skipped_lock_error",
    "owner_lock_release_failed",
    "top_movers_write_giveup",
})

# 회차 컨텍스트에는 싣지 않는(로그에 붙이지 않는) 내부 키.
_CTX_INTERNAL = frozenset({"partial_reasons"})


def _log(payload: dict):
    ctx = _CYCLE.get()
    if ctx is not None:
        event = payload.get("event")
        if event in _PARTIAL_EVENTS and ctx.get("step"):
            ctx["partial_reasons"].append(f"{ctx['step']}:{event}")
        # 회차 안에서 난 로그에는 실행 위치를 붙인다(값이 없으면 붙이지 않는다).
        payload = {**payload,
                   **{k: v for k, v in ctx.items()
                      if v is not None and k not in _CTX_INTERNAL}}
    print(f"[singcup_clips] {json.dumps(payload, ensure_ascii=False, default=str)}", flush=True)


def _error_kind(e: BaseException) -> tuple[str, bool]:
    """(error_type, retryable). 예외 메시지 원문은 그대로 내보내지 않는다."""
    if isinstance(e, Exception) and _is_locked_error(e):
        return "database_locked", True
    if isinstance(e, asyncio.TimeoutError):
        return "timeout", True
    return "unexpected", False


@contextlib.contextmanager
def _operation(name: str):
    """공유 쓰기 경로에 이름을 붙인다. 오래 걸리면 그 사실도 남긴다."""
    ctx = _CYCLE.get()
    prev = ctx.get("operation") if ctx is not None else None
    if ctx is not None:
        ctx["operation"] = name
    t0 = time.perf_counter()
    try:
        yield
    except BaseException:
        # **실패 시에는 이름을 되돌리지 않는다** — 되돌리면 상위 `_step`이
        # 어느 쓰기에서 났는지 모른 채 로그를 남긴다(그게 원래 문제였다).
        raise
    else:
        if ctx is not None:
            ctx["operation"] = prev
    finally:
        total = int((time.perf_counter() - t0) * 1000)
        if total >= SLOW_WRITE_MS:
            # 큐 대기/BEGIN 대기/COMMIT을 따로 재려면 utils/db_write.py(P0 코드)를
            # 손봐야 한다 — 여기서는 총 소요만 남긴다.
            _log({"event": "db_write_slow", "level": "warning",
                  "what": name, "total_ms": total})


async def _run_step(name: str, fn) -> str:
    """루프 단계 하나를 실행하고 결과를 돌려준다: success / partial / failed / skipped.

    **복구 가능한 오류만 격리한다.** SQLite 잠금·타임아웃은 이 단계에서 끝내고
    다른 독립 단계는 계속 돌린다(실측: 회차 첫 쓰기 하나가 잠기자 6단계가 통째로
    건너뛰어졌다). 반대로 프로그래밍 오류·취소·종료 신호는 **그대로 올린다** —
    부분 실패로 위장하면 진짜 버그가 숨는다.
    """
    ctx = _CYCLE.get()
    if ctx is not None:
        ctx["step"] = name
        ctx["operation"] = None
        before = len(ctx["partial_reasons"])
    t0 = time.perf_counter()
    try:
        result = await fn()
    except BaseException as e:                      # noqa: BLE001
        kind, retryable = _error_kind(e)
        _log({"event": "loop_step_error", "level": "warning",
              "worker": "singcup_clips", "step": name,
              "operation": (ctx or {}).get("operation"),
              "error_type": kind, "retryable": retryable,
              "duration_ms": int((time.perf_counter() - t0) * 1000),
              "detail": str(e)[:160]})
        if not retryable:
            raise                                   # 프로그래밍 오류·취소는 올린다
        return "failed"
    finally:
        if ctx is not None:
            ctx["step"] = None
            ctx["operation"] = None

    if isinstance(result, dict) and result.get("status") == ST_SKIPPED:
        return "skipped"
    if ctx is not None and len(ctx["partial_reasons"]) > before:
        return "partial"
    return "success"


# ── 순수 함수 (테스트 대상) ─────────────────────────────────────────────────
def has_singcup_tag(description) -> bool:
    """`#싱드컵` 해시태그가 실제로 있는지. 유니코드 정규화 후 검사한다.

    판정 대상은 **설명(description)뿐이다**. 제목은 보지 않는다 — 제목에는
    `[싱드컵]`처럼 태그가 아닌 표기가 흔해서, 넓히면 참가하지 않은 클립까지
    들어온다.
    """
    if not description:
        return False
    return bool(_TAG_RE.search(unicodedata.normalize("NFKC", str(description))))


def parse_clip_date(raw) -> datetime | None:
    """'YYYY-MM-DD HH:MM:SS'(KST) -> aware datetime. 실패하면 None."""
    s = str(raw or "").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_KST)
    except ValueError:
        return None


def safe_count(value) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, n)


# 지표 상한. JSON이 정확히 표현할 수 있는 정수 한계(2^53)를 넘는 값은 스키마 이상으로
# 본다 — 실제 조회수·하트가 이 규모일 수 없고, sqlite INTEGER 범위를 넘기면 저장 자체가
# 실패해 회차가 통째로 죽는다.
_MAX_COUNT = 2 ** 53


def valid_count(value) -> int | None:
    """유효한 비음수 정수일 때만 값, 아니면 None(= '못 읽음').

    지표에는 `safe_count`를 쓸 수 없다. 그쪽은 malformed·음수를 **0으로 정규화**하는데,
    지표에서 0은 '진짜 0'이라는 뜻이다. 정규화된 0이 그대로 저장되면
    (a) '한 번도 못 읽음'과 구분이 사라지고 (b) 조회수 70% 가중 점수에 진짜 0으로
    들어가 순위를 왜곡한다. 여기서는 읽지 못한 것을 읽지 못한 것으로 둔다.

    판정 계약(테스트 `test_valid_count_contract`가 고정한다):
      None / "" / list / dict / 그 밖의 파싱 불가 → None
      bool                → None. **파이썬에서 bool은 int의 하위 타입**이라 그냥 두면
                            True가 1로, False가 0으로 저장된다(0은 '진짜 0'이 된다).
                            isinstance(value, int)보다 **먼저** 걸러야 한다.
      NaN / ±Infinity     → None (int() 변환에서 ValueError/OverflowError)
      음수                → None (거부. 0으로 깎지 않는다)
      소수(12.5)          → None. 조회수는 정수다 — 소수가 오면 스키마 이상으로 본다.
                            단 12.0처럼 정수와 같은 값은 허용한다.
      정수형 문자열("345") → 345. 기존 `safe_count`가 허용해 온 동작이라 회귀를 만들지
                            않기 위해 유지한다(치지직이 숫자를 문자열로 주는 회차가 있다).
      2^53 초과            → None (위 상한)
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        n = value
    else:
        try:
            f = float(value)                    # str/float 모두 여기로
        except (TypeError, ValueError):
            return None
        # NaN·±Inf는 int()에서 각각 ValueError·OverflowError를 낸다
        try:
            n = int(f)
        except (ValueError, OverflowError):
            return None
        if n != f:                              # 12.5 같은 진짜 소수는 거부
            return None
    if n < 0 or n > _MAX_COUNT:
        return None
    return n


def extract_heart(card: dict) -> tuple[int, bool]:
    """(하트 수, 읽기 성공 여부). reactions/like가 없으면 (0, False)로 구분한다."""
    inter = card.get("interaction")
    if not isinstance(inter, dict):
        return (0, False)
    emo = inter.get("emotion")
    if not isinstance(emo, dict):
        return (0, False)
    reactions = emo.get("reactions")
    if not isinstance(reactions, list):
        return (0, False)
    for r in reactions:
        if isinstance(r, dict) and r.get("reactionType") == "like":
            n = valid_count(r.get("count"))
            return (0, False) if n is None else (n, True)
    # like 리액션 자체가 없는 경우는 '하트 0'으로 본다(구조는 정상)
    return (0, True)


def extract_view(card: dict) -> tuple[int, bool]:
    content = card.get("content")
    if not isinstance(content, dict):
        return (0, False)
    vod = content.get("vod")
    if not isinstance(vod, dict) or "count" not in vod:
        return (0, False)
    n = valid_count(vod.get("count"))
    return (0, False) if n is None else (n, True)


def extract_description(card: dict) -> str:
    content = card.get("content")
    return str((content or {}).get("description") or "") if isinstance(content, dict) else ""


def is_candidate_clip(item: dict, *, start: datetime, end: datetime) -> bool:
    """카드 API를 부르기 전에 목록 정보만으로 거를 수 있는 조건."""
    if not isinstance(item, dict):
        return False
    if item.get("categoryType") != "ETC" or item.get("clipCategory") != "music":
        return False
    if item.get("adult"):
        return False
    if str(item.get("blindType") or "").upper() in _BAD_BLIND:
        return False
    if not item.get("clipUID") or not item.get("ownerChannelId"):
        return False
    d = parse_clip_date(item.get("createdDate"))
    return d is not None and start <= d <= end


def pick_representative(clips: list[dict],
                        override_uid: str | None = None) -> dict | None:
    """스트리머의 대표 클립 — 하트↓ → 조회수↓ → 생성 시각↑ → clipUID↑.

    `override_uid`가 주어지고 그 클립이 **후보 목록 안에 있으면** 그것을 대표로
    쓴다(수동 지정, singcup_overrides 참고). 후보 목록은 이미 active·삭제 아님·
    기간·블라인드로 걸러진 것이므로, 지정한 클립이 그 사이 무효가 됐다면 목록에
    없고 자동 규칙이 그대로 적용된다 — **무효 override는 조용히 자동으로 복귀한다.**

    자동 규칙 자체는 바뀌지 않는다. override는 정렬을 고치는 것이 아니라 정렬
    결과보다 앞서는 '사람의 지정'을 하나 얹는 것이다.
    """
    if not clips:
        return None
    if override_uid:
        for c in clips:
            if str(c["clip_uid"]) == override_uid:
                return c
    return sorted(clips, key=_clip_sort_key)[0]


def _clip_sort_key(c: dict):
    return (-int(c["heart_count"]), -int(c["view_count"]),
            int(c["created_at"]), str(c["clip_uid"]))


def compute_scores(reps: list[dict]) -> list[dict]:
    """비공식 예상 인기점수 = 조회수 비중 70 + 하트 비중 30.

    분모는 '대표 클립 전체'의 최댓값이다(파트 구분 없이 하나의 통합 풀).
    최댓값이 0이면 해당 항목 점수는 0으로 둔다(0으로 나누지 않는다).
    """
    max_view = max((int(r["view_count"]) for r in reps), default=0)
    max_heart = max((int(r["heart_count"]) for r in reps), default=0)
    for r in reps:
        vs = (int(r["view_count"]) / max_view * 70) if max_view > 0 else 0.0
        hs = (int(r["heart_count"]) / max_heart * 30) if max_heart > 0 else 0.0
        r["view_score"] = round(vs, 2)
        r["heart_score"] = round(hs, 2)
        r["score"] = round(vs + hs, 2)
    ranked = sorted(reps, key=lambda r: (-r["score"], -int(r["heart_count"]),
                                         -int(r["view_count"]), int(r["created_at"]),
                                         str(r["clip_uid"])))
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return ranked


def heart_change_rate(current: int, past: int) -> float | None:
    """이전 값이 0이면 퍼센트를 계산하지 않는다(화면에서 NEW로 표시)."""
    if past <= 0:
        return None
    return round((current - past) / past * 100, 1)


# ── HTTP ────────────────────────────────────────────────────────────────────
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            limits=httpx.Limits(max_connections=max(2, CARD_CONCURRENCY + 1)))
    return _client


async def reset_state():
    global _client, _main_lock
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None
    _channel_cache.clear()
    _baseline_cache.clear()
    invalidate_main_cache()
    # 락은 '경합이 일어난 순간'의 이벤트 루프에 묶인다(경합이 없으면 묶이지 않는다).
    # 테스트는 매번 새 루프를 만들므로, 한 번이라도 동시 요청을 재현한 테스트가 있으면
    # 그 뒤의 테스트에서 "bound to a different event loop"로 터진다. 새로 만들어 끊는다.
    _main_lock = asyncio.Lock()
    global _movers_persist_lock
    _movers_persist_lock = asyncio.Lock()


class FetchError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class CallBudget:
    """클립 하나의 **논리 작업**이 쓸 수 있는 실제 transport 호출 예산.

    `_get_json`의 HTTP 재시도와 지표 partial 재시도가 **같은 예산을 나눠 쓴다.**
    두 계층이 각자 상한을 갖게 두면 곱해진다 — 실측으로 500 → 500 → 200(partial)
    뒤에 partial 재시도가 2회 더 붙어 한 클립이 5회를 썼다. 토큰 버킷을 통과하더라도
    한 클립이 5개 토큰을 먹으면 장애와 partial이 겹친 순간 스윕 전체가 밀린다.

    호출 순서 계약(반드시 이 순서):
      1. 예산 확인   → 없으면 토큰도 세마포어도 잡지 않고 HTTP도 쏘지 않는다
      2. 토큰 버킷   → 재시도가 속도 제한을 우회하지 못하게
      3. 세마포어    → 동시성 상한
      4. 실제 HTTP

    그래서 **토큰 획득 수 == 실제 transport 호출 수**가 항상 성립한다.
    """

    def __init__(self, limit: int, *, acquire=None, sem=None):
        self.limit = max(0, int(limit))
        self.remaining = self.limit
        self.used = 0
        self._acquire = acquire
        self._sem = sem

    @property
    def available(self) -> bool:
        return self.remaining > 0

    async def run(self, fn):
        """예산 1을 차감하고 토큰·세마포어를 거쳐 fn()을 실행한다."""
        if self.remaining <= 0:                     # 방어 — 호출부가 이미 확인한다
            raise FetchError(ST_FAILED, "호출 예산 소진")
        self.remaining -= 1
        self.used += 1
        if self._acquire is not None:
            await self._acquire()
        if self._sem is not None:
            async with self._sem:
                return await fn()
        return await fn()


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(BACKOFF_MAX, max(0.0, float(retry_after.strip())))
        except ValueError:
            pass
    return min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt)) + random.uniform(0, BACKOFF_BASE or 0.1)


async def _get_json(client, url, *, params=None, headers=None, what="request",
                    gate: "CallBudget | None" = None):
    """408/429/5xx/timeout만 재시도. 400/401/403/404는 즉시 실패.

    `gate`를 주면 **내부 재시도까지 그 예산에서 차감**한다(기본 None이면 기존
    소비자 계약 그대로 — 이 파일의 다른 호출부는 영향받지 않는다).
    """
    for attempt in range(MAX_RETRIES):
        if gate is not None and not gate.available:
            # 예산 소진 — 토큰도 세마포어도 잡지 않고 여기서 끝낸다.
            raise FetchError(ST_FAILED, f"{what}: 호출 예산 소진")
        _api_counter["calls"] += 1
        try:
            if gate is None:
                r = await client.get(url, params=params,
                                     headers=headers or _HEADERS,
                                     timeout=REQUEST_TIMEOUT)
            else:
                r = await gate.run(lambda: client.get(
                    url, params=params, headers=headers or _HEADERS,
                    timeout=REQUEST_TIMEOUT))
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt + 1 >= MAX_RETRIES:
                raise FetchError(ST_FAILED, f"{what}: {type(e).__name__}")
            await asyncio.sleep(_retry_delay(attempt, None))
            continue
        code = r.status_code
        if code == 200:
            try:
                return r.json()
            except (json.JSONDecodeError, ValueError):
                raise SchemaError(f"{what}: 응답이 JSON이 아님")
        if code == 400:
            raise FetchError(ST_SCHEMA, f"{what}: HTTP 400")
        if code in (401, 403):
            raise FetchError(ST_BLOCKED, f"{what}: HTTP {code}")
        if code == 404:
            raise FetchError(ST_SCHEMA, f"{what}: HTTP 404")
        if code in (408, 429) or 500 <= code < 600:
            if code == 429:
                _api_counter["http_429"] += 1
            if attempt + 1 >= MAX_RETRIES:
                raise FetchError(ST_FAILED, f"{what}: HTTP {code}")
            await asyncio.sleep(_retry_delay(attempt, r.headers.get("Retry-After")))
            continue
        raise FetchError(ST_FAILED, f"{what}: HTTP {code}")
    raise FetchError(ST_FAILED, f"{what}: 재시도 소진")


# 사이클당 외부 호출 수와 429 횟수 — 갱신 예산을 올려도 되는지 판단하는 근거.
# 짐작으로 REFRESH_PER_CYCLE을 올리면 429를 맞고 나서야 알게 된다.
_api_counter = {"calls": 0, "http_429": 0}


def _take_api_counters() -> dict:
    out = dict(_api_counter)
    _api_counter["calls"] = _api_counter["http_429"] = 0
    return out


async def fetch_clip_page(client, cursor: str | None) -> tuple[list[dict], str | None]:
    """(클립 목록, 다음 커서). 커서는 content.page.next.clipUID."""
    params = {"filterType": "ALL", "orderType": "RECENT", "size": PAGE_SIZE}
    if cursor:
        params["clipUID"] = cursor
    payload = await _get_json(client, CLIPS_API, params=params, what="clips")
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise SchemaError(f"clips: code={(payload or {}).get('code')!r}")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise SchemaError("clips: content가 객체가 아님")
    data = content.get("data")
    if not isinstance(data, list):
        raise SchemaError("clips: content.data가 배열이 아님")
    nxt = ((content.get("page") or {}).get("next") or {}).get("clipUID")
    return data, (str(nxt) if nxt else None)


async def fetch_card(client, item: dict, *,
                     gate: "CallBudget | None" = None) -> dict | None:
    """클립 카드에서 태그/하트/조회수를 읽는다. 실패하면 None."""
    clip_uid = str(item.get("clipUID"))
    referer = f"https://chzzk.naver.com/clips/{quote(clip_uid, safe='')}"
    params = {
        "seedType": "SPECIFIC", "serviceType": "CHZZK",
        "seedMediaId": str(item.get("videoId") or ""), "mediaType": "VOD",
        "panelType": "sdk_chzzk", "referer": referer, "recType": "CHZZK",
        "recId": str(item.get("recId") or ""), "enableReverse": "false",
        "adAllowed": "Y", "clickNsc": "chzzk_category_clip",
        "clickArea": "clip_item", "deviceType": "html5_pc",
    }
    headers = dict(_HEADERS)
    headers["Referer"] = referer
    try:
        payload = await _get_json(client, CARD_API, params=params,
                                  headers=headers, what=f"card({clip_uid})",
                                  gate=gate)
    except (FetchError, SchemaError) as e:
        _log({"event": "card_failed", "level": "warning",
              "clip_uid": clip_uid, "detail": str(e)[:160]})
        return None
    card = payload.get("card") if isinstance(payload, dict) else None
    if not isinstance(card, dict):
        _log({"event": "card_schema", "level": "warning", "clip_uid": clip_uid})
        return None
    heart, heart_ok = extract_heart(card)
    view, view_ok = extract_view(card)
    reason = "" if (heart_ok and view_ok) else _missing_reason(card, heart_ok, view_ok)
    if reason:
        # 실제 0과 '못 읽음'을 구분해 남긴다.
        # 어느 쪽이 왜 비었는지까지 남겨야 '카드가 원래 안 주는 값'인지
        # '스키마가 바뀐 것'인지 로그만 보고 판단할 수 있다.
        _log({"event": "card_metrics_missing", "level": "warning", "clip_uid": clip_uid,
              "heart_ok": heart_ok, "view_ok": view_ok, "reason": reason})
    return {"description": extract_description(card), "heart_count": heart,
            "view_count": view, "heart_ok": heart_ok, "view_ok": view_ok,
            "metrics_ok": bool(heart_ok and view_ok),
            # 결손 사유를 반환값에도 싣는다 — 재시도 판정이 로그를 다시 파싱하지
            # 않고 이 값 하나로 끝나게 한다(로그는 사람이 읽는 용도로 남는다).
            "missing_reason": reason,
            "title": extract_title(card),
            "owner_channel_id": extract_owner_channel_id(card)}


# ── 부분 결손(partial) bounded retry ────────────────────────────────────────
# 카드 API가 200을 주면서 `content.vod`만 빠뜨리는 회차가 있다(실측 로그:
# heart_ok=true, view_ok=false, reason=view:no_vod). 저장 계약이 "못 읽은 필드는
# 보존"이라 그 클립의 view_count는 삽입 초기값 0으로 남고, 다음 기회는 다음
# 사이클(70분+)이다. 그동안 0이 조회수 70% 가중 점수에 진짜 0처럼 들어간다.
#
# 그래서 **그 자리에서** 짧게 다시 물어본다. 세 가지를 지킨다.
#   1) 호출부는 결과를 **하나만** 받는다 → run_sweep의 processed 집계가 그대로다
#      (초기 partial을 먼저 세고 나중에 되돌리는 구조가 아니다).
#   2) 재시도 대상은 사유가 전부 leaf 계열일 때뿐이다(아래 판정표).
#   3) 한 번 제대로 받은 필드는 뒤 시도가 비어 와도 버리지 않는다(field-wise merge).
#   4) **HTTP 내부 재시도와 partial 재시도가 같은 transport 예산을 나눠 쓴다.**
#      각자 상한을 가지면 곱해진다 — 실측으로 500 → 500 → 200(partial) 뒤에 partial
#      재시도가 2회 더 붙어 한 클립이 5회를 썼다. 한 클립이 토큰 5개를 먹으면
#      장애와 partial이 겹친 순간 스윕 전체가 밀린다.


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    """정수 환경변수를 안전하게 읽는다. 잘못된 값에 기동을 실패시키지 않는다.

    미설정·공백·숫자 아님·NaN·Infinity → 기본값. 범위를 벗어나면 clamp.
    경고만 남기며 값 외의 정보(비밀정보 등)는 로그하지 않는다.
    """
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        f = float(str(raw).strip())
        v = int(f)
        if v != f and abs(f - v) > 0:               # 12.7 같은 값은 절삭됨을 알린다
            _log({"event": "env_truncated", "level": "warning",
                  "name": name, "to": v})
    except (TypeError, ValueError, OverflowError):  # "abc" / "nan" / "inf"
        _log({"event": "env_invalid", "level": "warning",
              "name": name, "fallback": default})
        return default
    if v < lo or v > hi:
        c = max(lo, min(hi, v))
        _log({"event": "env_clamped", "level": "warning",
              "name": name, "value": v, "clamped_to": c})
        return c
    return v


# 한 클립의 논리 작업이 쓸 수 있는 **실제 transport 호출 총량**.
# HTTP 내부 재시도 + partial 재시도가 여기서 함께 차감된다.
CARD_TRANSPORT_BUDGET = _env_int("SINGCUP_CARD_TRANSPORT_BUDGET", 3, 1, 3)
# 최초 호출 **이후** 허용되는 partial 재시도 최대 수(0~2, 기본 2).
# 0이면 partial 추가 재시도 비활성. HTTP 내부 재시도가 예산을 먼저 쓰면 실제 가능
# 횟수는 그만큼 자동으로 줄어든다 — 상한은 언제나 CARD_TRANSPORT_BUDGET이다.
PARTIAL_RETRY_MAX = _env_int("SINGCUP_PARTIAL_RETRY_MAX", 2, 0, 2)
# 추가 대기의 절대 상한. 이 시간을 넘길 재시도는 아예 시작하지 않는다 —
# 클립 락을 쥔 채 늘어지면 수동 갱신이 그만큼 막힌다.
PARTIAL_RETRY_BUDGET_SECONDS = float(
    os.getenv("SINGCUP_PARTIAL_RETRY_BUDGET_SECONDS", "10") or "10")


def is_retryable_metrics_partial(card: dict | None) -> bool:
    """다시 물어볼 가치가 있는 결손인가. **사유(reason)로만** 판정한다.

    네 조건이 동시에 성립해야 한다.
      1. HTTP fetch 자체는 성공했다   → `card is not None`
         (실패는 `_get_json`이 이미 재시도했다. 여기서 또 하면 이중 중첩이다)
      2. 아직 못 채운 필드가 있다     → `not metrics_ok`
      3. 결손 사유가 **하나도 빠짐없이** leaf 계열이다
         (`leaf_missing` 그릇은 왔는데 숫자만 없음 / `leaf_invalid` 값이 깨짐)
      4. `container_absent`(상위 블록 부재)가 하나라도 섞이면 재시도하지 않는다

    **XOR로 판정하지 않는다.** 하트·조회수가 *둘 다* 없더라도 둘 다 leaf 결손이면
    (예: `heart:no_reactions,view:no_vod`) metrics leaf만 일시적으로 빠진 형태이므로
    다시 물어본다. 반대로 한쪽만 결손이어도 사유가 `view:no_content`처럼 구조적이면
    재시도하지 않는다. 판정의 축은 '몇 개가 비었나'가 아니라 **'왜 비었나'**다.

    잘못된 clip / owner 불일치 / 삭제 / 영구 404는 여기 오지 않는다 — `_get_json`이
    400·401·403·404를 즉시 실패로 확정해 `fetch_card`가 None을 돌려주므로 조건 1에서
    걸러진다.
    """
    if not card or card.get("metrics_ok"):
        return False
    reasons = [r for r in str(card.get("missing_reason") or "").split(",") if r]
    return bool(reasons) and all(r in _RETRYABLE_REASONS for r in reasons)


def _merge_card(prev: dict | None, new: dict) -> dict:
    """두 시도의 결과를 **필드 단위로** 합친다.

    규칙은 둘뿐이다. 뒤 시도가 정상으로 준 필드는 덮고(그 사이 값이 올랐을 수
    있다), 비어 온 필드는 앞 시도 값을 그대로 둔다. 후속 missing이 이미 확보한
    값을 지우면 재시도가 상황을 악화시키는 셈이 된다.
    """
    if prev is None:
        return dict(new)
    out = dict(prev)
    if new.get("heart_ok"):
        out["heart_count"], out["heart_ok"] = new["heart_count"], True
    if new.get("view_ok"):
        out["view_count"], out["view_ok"] = new["view_count"], True
    for k in ("description", "title", "owner_channel_id"):
        if new.get(k):
            out[k] = new[k]
    out["metrics_ok"] = bool(out["heart_ok"] and out["view_ok"])
    # 병합 후 **아직 못 채운 필드**의 사유만 남긴다. 이미 확보한 필드의 사유를 끌고
    # 가면 재시도 판정이 오염된다 — 예를 들어 하트를 이미 받아 둔 상태에서 마지막
    # 시도가 'heart:no_interaction'을 줬다고 재시도를 포기하면 안 된다.
    out["missing_reason"] = ",".join(
        r for r in str(new.get("missing_reason") or "").split(",")
        if r and ((r.startswith("heart:") and not out["heart_ok"])
                  or (r.startswith("view:") and not out["view_ok"])))
    return out


def _observed_fields(card: dict | None) -> str:
    if not card:
        return "none"
    got = [n for n, ok in (("heart", card.get("heart_ok")),
                           ("view", card.get("view_ok"))) if ok]
    return ",".join(got) or "none"


async def fetch_card_metrics(client, item: dict, *, acquire=None, sem=None,
                             max_retries: int | None = None,
                             trace: list | None = None) -> dict | None:
    """카드 지표 한 건 — 부분 결손이면 짧게 다시 물어보고 필드 단위로 합친다.

    반환값은 `fetch_card`와 **같은 모양**이다(실패하면 None). 호출부는 결과를 하나만
    받으므로 집계·저장 계약이 달라지지 않는다. 시도 횟수는 통계·진단용으로
    `attempts`/`retried` 키에만 실린다.

    `acquire`는 전역 토큰 버킷, `sem`은 동시성 세마포어다. 둘 다 `CallBudget`이
    **실제 transport 호출 직전에** 거치므로 `토큰 획득 수 == 실제 HTTP 호출 수`가
    항상 성립한다(내부 재시도도 포함). 백오프 대기는 세마포어 밖에서 한다 —
    안에서 자면 동시성 슬롯 하나가 그동안 놀게 된다.

    **호출 예산은 하나로 공유한다.** `CARD_TRANSPORT_BUDGET`(기본 3)에서 HTTP 내부
    재시도와 partial 재시도가 함께 차감된다. 예산이 0이면 토큰도 세마포어도 잡지
    않고 즉시 종료하며, 그때까지 병합한 결과로 terminal을 정한다.

    `trace`를 주면 시도별 관측 필드를 담아 준다(관리자 Preview가 "어느 시도에서
    무엇을 얻었는지" 보여주는 데 쓴다). 수치 외의 원본 응답은 담지 않는다.
    """
    limit = PARTIAL_RETRY_MAX if max_retries is None else max(0, max_retries)
    clip_uid = str(item.get("clipUID"))
    budget = CallBudget(CARD_TRANSPORT_BUDGET, acquire=acquire, sem=sem)
    merged: dict | None = None
    attempts, waited = 0, 0.0

    for i in range(limit + 1):
        if not budget.available:
            # 예산 소진 — 추가 호출 없이 현재까지의 병합 결과로 끝낸다.
            break
        card = await fetch_card(client, item, gate=budget)
        attempts += 1
        if trace is not None:
            trace.append({
                "attempt": attempts, "ok": card is not None,
                "fieldsObserved": _observed_fields(card),
                "heartCount": card["heart_count"] if card and card["heart_ok"] else None,
                "viewCount": card["view_count"] if card and card["view_ok"] else None,
                "missingReason": (card or {}).get("missing_reason") or "",
            })

        if card is None:
            # 조회 자체의 실패는 `_get_json`이 같은 예산 안에서 이미 재시도했다.
            # 앞 시도에서 받아 둔 필드가 있으면 그것을 살려서 내보낸다.
            break
        merged = _merge_card(merged, card)
        if merged["metrics_ok"]:
            break
        # 판정은 **병합 후** 상태로 한다(이미 채운 필드는 사유에서 빠져 있다).
        if not is_retryable_metrics_partial(merged) or i >= limit:
            break
        if not budget.available:
            break                                   # 남은 예산 없음 → terminal
        reason = merged["missing_reason"]
        delay = _retry_delay(i, None)
        if waited + delay > PARTIAL_RETRY_BUDGET_SECONDS:
            break                                   # 대기 예산 초과 — 다음 사이클에
        _log({"event": "card_metrics_retry", "clip_uid": clip_uid,
              "attempt": attempts, "max_attempts": limit + 1, "reason": reason,
              "wait_ms": int(delay * 1000), "transport_used": budget.used,
              "transport_remaining": budget.remaining,
              "fields_observed": _observed_fields(merged)})
        await asyncio.sleep(delay)
        waited += delay

    if merged is None:
        return None
    merged["attempts"], merged["retried"] = attempts, attempts - 1
    merged["transport_calls"] = budget.used
    if attempts > 1 or budget.used > 1:
        _log({"event": "card_metrics_retry_result", "clip_uid": clip_uid,
              "attempts": attempts, "transport_calls": budget.used,
              "fields_observed": _observed_fields(merged),
              "final_result": "success" if merged["metrics_ok"] else "partial"})
    return merged


def extract_title(card: dict) -> str:
    content = card.get("content")
    return str((content or {}).get("title") or "") if isinstance(content, dict) else ""


def extract_owner_channel_id(card: dict) -> str:
    """카드에서 소유 채널 id. 목록 없이 클립 하나만 알 때 쓴다.

    클립 상세 API(`/clips/{uid}/detail`)의 ownerChannelId는 null로 오는 반면,
    카드의 interaction.subscription.channelId는 방송인 채널 id와 일치한다
    (실측 2건 대조 확인). 뒤늦게 태그가 붙은 클립을 등록할 때 이게 유일한 출처다.
    """
    inter = card.get("interaction")
    sub = inter.get("subscription") if isinstance(inter, dict) else None
    return str((sub or {}).get("channelId") or "") if isinstance(sub, dict) else ""


# 결손 사유 분류. **재시도 판정이 이 분류에만 의존한다** — 그래서 "그릇이 없다"와
# "숫자만 없다"와 "숫자가 깨졌다"를 반드시 서로 다른 사유로 남겨야 한다.
#
#   container_absent  상위 블록(content / interaction / emotion)이 통째로 없다.
#                     실측: 잘못된 videoId로 부르면 vod와 like가 **함께** 사라진다.
#                     입력·대상이 잘못된 쪽이므로 다시 불러도 같은 답이 온다 → 재시도 금지.
#   leaf_missing      그릇은 왔는데 숫자 필드만 없다 → 일시적일 수 있다 → 재시도.
#   leaf_invalid      숫자 필드는 있는데 값이 malformed·음수·NaN 등이다.
#                     일시적 직렬화/집계 오류일 수 있어 재시도하되, **절대 0으로 저장하지
#                     않는다**. 별도 사유로 남겨 로그에서 추적할 수 있게 한다.
_CONTAINER_ABSENT = frozenset({
    "view:no_content", "heart:no_interaction", "heart:no_emotion"})
_LEAF_MISSING = frozenset({"view:no_vod", "view:no_count", "heart:no_reactions"})
_LEAF_INVALID = frozenset({"view:invalid_count", "heart:invalid_count"})
# 재시도해도 되는 사유 = leaf 계열 전부
_RETRYABLE_REASONS = _LEAF_MISSING | _LEAF_INVALID


def _missing_reason(card: dict, heart_ok: bool, view_ok: bool) -> str:
    """어느 단계에서 값이 끊겼는지 표기한다. 재시도 판정의 **유일한** 입력이다."""
    parts = []
    if not heart_ok:
        inter = card.get("interaction")
        emo = inter.get("emotion") if isinstance(inter, dict) else None
        reactions = emo.get("reactions") if isinstance(emo, dict) else None
        if not isinstance(inter, dict):
            parts.append("heart:no_interaction")
        elif not isinstance(emo, dict):
            parts.append("heart:no_emotion")
        elif not isinstance(reactions, list):
            parts.append("heart:no_reactions")
        else:
            # like 리액션이 없으면 extract_heart가 (0, True)를 준다. 여기까지 왔다는 것은
            # like는 있는데 count 값이 유효하지 않다는 뜻이다.
            parts.append("heart:invalid_count")
    if not view_ok:
        content = card.get("content")
        vod = content.get("vod") if isinstance(content, dict) else None
        if not isinstance(content, dict):
            parts.append("view:no_content")
        elif not isinstance(vod, dict):
            parts.append("view:no_vod")
        elif "count" not in vod:
            parts.append("view:no_count")
        else:
            parts.append("view:invalid_count")      # 키는 있는데 값이 깨졌다
    return ",".join(parts)


CLIP_DETAIL_API = "https://api.chzzk.naver.com/service/v1/clips/{uid}/detail"


async def fetch_clip_detail(client, clip_uid: str) -> dict | None:
    """클립 1건의 제목·썸네일. 목록에서 놓친 필드를 메우는 **보수용** 경로다.

    정상 흐름에서는 목록 응답이 이 값을 주므로 부르지 않는다. 과거에 빈 값으로
    저장된 행은 목록 재스캔이 닿지 않아(신규 탐색은 아는 페이지에서 멈춘다)
    스스로 복구되지 않기 때문에 여기서만 따로 채운다.
    """
    url = CLIP_DETAIL_API.format(uid=quote(clip_uid, safe=""))
    headers = dict(_HEADERS)
    headers["Referer"] = f"https://chzzk.naver.com/clips/{quote(clip_uid, safe='')}"
    try:
        payload = await _get_json(client, url, headers=headers,
                                  what=f"detail({clip_uid})")
    except (FetchError, SchemaError) as e:
        _log({"event": "clip_detail_failed", "level": "warning",
              "clip_uid": clip_uid, "detail": str(e)[:160]})
        return None
    c = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(c, dict):
        return None
    return {"clip_title": str(c.get("clipTitle") or ""),
            "thumbnail_image_url": str(c.get("thumbnailImageUrl") or "")}


async def fetch_clip_meta(client, clip_uid: str, *, full: bool = False) -> dict | None:
    """상세 API로 videoId·생성일(·등록에 필요한 나머지)을 얻는다.

    목록 없이 클립 하나만 알 때 등록 재료를 모으는 경로다. `ownerChannelId`는
    이 응답에서 `null`로 오는 일이 잦아 카드 쪽 값을 함께 써야 한다
    (`extract_owner_channel_id` 주석 참고).
    """
    url = CLIP_DETAIL_API.format(uid=quote(clip_uid, safe=""))
    headers = dict(_HEADERS)
    headers["Referer"] = f"https://chzzk.naver.com/clips/{quote(clip_uid, safe='')}"
    try:
        payload = await _get_json(client, url, headers=headers,
                                  what=f"detail({clip_uid})")
    except (FetchError, SchemaError):
        return None
    c = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(c, dict):
        return None
    d = parse_clip_date(c.get("createdDate"))
    out = {"video_id": str(c.get("videoId") or ""),
           "rec_id": str(c.get("recId") or ""),
           "created_at": int(d.timestamp()) if d else None,
           "created_date": c.get("createdDate")}
    if full:
        oc = c.get("ownerChannel") or {}
        out.update({
            "owner_channel_id": str(c.get("ownerChannelId")
                                    or oc.get("channelId") or ""),
            "clip_title": str(c.get("clipTitle") or ""),
            "thumbnail_image_url": str(c.get("thumbnailImageUrl") or ""),
            "duration": safe_count(c.get("duration")),
            "adult": bool(c.get("adult")),
            "blind_type": str(c.get("blindType") or ""),
        })
    return out


async def repair_clip_media(clip_uid: str, detail: dict) -> bool:
    """비어 있던 제목/썸네일만 채운다. 이미 값이 있으면 건드리지 않는다."""
    if not detail or not detail.get("thumbnail_image_url"):
        return False
    db = await get_db()
    cur = await db.execute(
        "UPDATE singcup_clips SET thumbnail_image_url=?, "
        "clip_title = CASE WHEN clip_title='' THEN ? ELSE clip_title END "
        "WHERE clip_uid=? AND (thumbnail_image_url='' OR thumbnail_image_url IS NULL)",
        (detail["thumbnail_image_url"], detail.get("clip_title", ""), clip_uid))
    return cur.rowcount > 0


# 채널 정보는 clip마다 반복 조회하지 않는다 — channelId 기준 메모리 캐시 + DB 기록
_channel_cache: dict[str, tuple[float, dict]] = {}


async def fetch_channel(client, channel_id: str) -> dict | None:
    hit = _channel_cache.get(channel_id)
    if hit and time.time() - hit[0] < CHANNEL_TTL_MINUTES * 60:
        return hit[1]
    try:
        payload = await _get_json(client, f"{CHANNEL_API}/{quote(channel_id, safe='')}",
                                  what=f"channel({channel_id[:8]})")
    except (FetchError, SchemaError):
        return None
    content = (payload or {}).get("content")
    if not isinstance(content, dict):
        return None
    info = {
        "channel_name": str(content.get("channelName") or ""),
        "channel_image_url": str(content.get("channelImageUrl") or ""),
        "follower_count": safe_count(content.get("followerCount")),
        "verified_mark": 1 if content.get("verifiedMark") else 0,
    }
    _channel_cache[channel_id] = (time.time(), info)
    return info


# ── DB ──────────────────────────────────────────────────────────────────────
async def _upsert_clip(c: dict, now: int) -> bool:
    db = await get_db()
    exists = await (await db.execute(
        "SELECT 1 FROM singcup_clips WHERE clip_uid=?", (c["clip_uid"],))).fetchone()
    await db.execute(
        """INSERT INTO singcup_clips
               (clip_uid, event_id, owner_channel_id, video_id, rec_id, clip_title,
                thumbnail_image_url, description, created_at, heart_count, view_count,
                duration, adult, blind_type, metrics_ok,
                owner_channel_name, owner_channel_image_url, owner_verified,
                active, missing_scan_count,
                first_collected_at, last_collected_at, row_updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?)
           ON CONFLICT(clip_uid) DO UPDATE SET
               rec_id              = CASE WHEN excluded.rec_id != '' THEN excluded.rec_id
                                          ELSE rec_id END,
               owner_channel_name  = CASE WHEN excluded.owner_channel_name != ''
                                          THEN excluded.owner_channel_name
                                          ELSE owner_channel_name END,
               owner_channel_image_url = CASE WHEN excluded.owner_channel_image_url != ''
                                          THEN excluded.owner_channel_image_url
                                          ELSE owner_channel_image_url END,
               owner_verified      = excluded.owner_verified,
               -- 닉네임과 같은 이유로 빈 값이 기존 값을 덮지 못하게 한다.
               -- 목록 응답이 어쩌다 이 필드를 빠뜨리면 썸네일이 '' 로 지워지는데,
               -- 카드 API는 썸네일을 주지 않고 신규 탐색은 아는 페이지에서 멈추므로
               -- 한 번 비면 그 클립은 영원히 이미지 없이 남는다(실측 53/967건).
               clip_title          = CASE WHEN excluded.clip_title != ''
                                          THEN excluded.clip_title ELSE clip_title END,
               thumbnail_image_url = CASE WHEN excluded.thumbnail_image_url != ''
                                          THEN excluded.thumbnail_image_url
                                          ELSE thumbnail_image_url END,
               description         = excluded.description,
               -- 카드 조회에 실패한 회차가 기존 수치를 0으로 덮지 않게 한다
               heart_count = CASE WHEN excluded.metrics_ok=1 THEN excluded.heart_count
                                  ELSE heart_count END,
               view_count  = CASE WHEN excluded.metrics_ok=1 THEN excluded.view_count
                                  ELSE view_count END,
               metrics_ok         = excluded.metrics_ok,
               blind_type         = excluded.blind_type,
               active             = 1,
               missing_scan_count = 0,
               last_collected_at  = excluded.last_collected_at,
               row_updated_at     = excluded.row_updated_at""",
        (c["clip_uid"], EVENT_ID, c["owner_channel_id"], c["video_id"],
         c.get("rec_id", ""), c["clip_title"],
         c["thumbnail_image_url"], c["description"], c["created_at"], c["heart_count"],
         c["view_count"], c["duration"], c["adult"], c["blind_type"],
         1 if c["metrics_ok"] else 0,
         c.get("owner_channel_name", ""), c.get("owner_channel_image_url", ""),
         c.get("owner_verified", 0), now, now, now))
    return exists is None


async def _upsert_streamer(s: dict, now: int):
    db = await get_db()
    await db.execute(
        """INSERT INTO singcup_streamers
               (channel_id, event_id, channel_name, channel_image_url, follower_count,
                verified_mark, representative_clip_uid, tagged_clip_count,
                last_channel_updated_at, row_updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(channel_id) DO UPDATE SET
               channel_name            = CASE WHEN excluded.channel_name != ''
                                              THEN excluded.channel_name
                                              ELSE channel_name END,
               channel_image_url       = CASE WHEN excluded.channel_image_url != ''
                                              THEN excluded.channel_image_url
                                              ELSE channel_image_url END,
               follower_count          = CASE WHEN excluded.last_channel_updated_at > 0
                                              THEN excluded.follower_count
                                              ELSE follower_count END,
               verified_mark           = excluded.verified_mark,
               representative_clip_uid = excluded.representative_clip_uid,
               tagged_clip_count       = excluded.tagged_clip_count,
               last_channel_updated_at = CASE WHEN excluded.last_channel_updated_at > 0
                                              THEN excluded.last_channel_updated_at
                                              ELSE last_channel_updated_at END,
               row_updated_at          = excluded.row_updated_at""",
        (s["channel_id"], EVENT_ID, s["channel_name"], s["channel_image_url"],
         s["follower_count"], s["verified_mark"], s["representative_clip_uid"],
         s["tagged_clip_count"], s["last_channel_updated_at"], now))


# 랭킹 재계산은 참가자 전원을 한 번에 쓴다. 예전에는 위 함수를 사람 수만큼
# await 했고 중간 COMMIT이 없어서, **하나의 쓰기 트랜잭션이 그 반복 내내 열려
# 있었다.** 공유 aiosqlite 연결은 작업 하나짜리 큐라 그동안 공개 조회(/main)까지
# 그 뒤에 줄을 서고(실측 10,298ms), 전용 연결로 여는 짧은 쓰기는 잠금에 걸린다.
#
# 중간 COMMIT으로 나누지 않는다 — 그러면 일부 스트리머만 새 랭킹으로 바뀐 상태가
# 사용자에게 보이고, 중단되면 그 혼합 세대가 영구히 남는다. 대신 **쓰는 양 자체를
# 줄인다**: 값이 실제로 바뀐 행만 골라 executemany 한 번 + COMMIT 한 번.
# 원자성은 그대로고(트랜잭션 1개), 대부분의 회차에서 쓰는 행이 몇 개로 줄어든다.
_STREAMER_UPSERT_SQL = """INSERT INTO singcup_streamers
               (channel_id, event_id, channel_name, channel_image_url, follower_count,
                verified_mark, representative_clip_uid, tagged_clip_count,
                last_channel_updated_at, row_updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(channel_id) DO UPDATE SET
               channel_name            = CASE WHEN excluded.channel_name != ''
                                              THEN excluded.channel_name
                                              ELSE channel_name END,
               channel_image_url       = CASE WHEN excluded.channel_image_url != ''
                                              THEN excluded.channel_image_url
                                              ELSE channel_image_url END,
               follower_count          = CASE WHEN excluded.last_channel_updated_at > 0
                                              THEN excluded.follower_count
                                              ELSE follower_count END,
               verified_mark           = excluded.verified_mark,
               representative_clip_uid = excluded.representative_clip_uid,
               tagged_clip_count       = excluded.tagged_clip_count,
               last_channel_updated_at = CASE WHEN excluded.last_channel_updated_at > 0
                                              THEN excluded.last_channel_updated_at
                                              ELSE last_channel_updated_at END,
               row_updated_at          = excluded.row_updated_at"""


def _streamer_target(new: dict, cur: dict | None, now: int) -> dict:
    """UPSERT가 만들어 낼 최종 행. 위 SQL의 CASE 규칙을 그대로 옮긴 것이다.

    규칙이 두 곳에 생기므로 테스트가 둘을 대조해 고정한다 — 한쪽만 고치면
    '바뀌지 않았다'고 판단해 갱신을 건너뛰는 조용한 버그가 된다.
    """
    if cur is None:
        return {"channel_name": new["channel_name"],
                "channel_image_url": new["channel_image_url"],
                "follower_count": new["follower_count"],
                "verified_mark": new["verified_mark"],
                "representative_clip_uid": new["representative_clip_uid"],
                "tagged_clip_count": new["tagged_clip_count"],
                "last_channel_updated_at": new["last_channel_updated_at"]}
    upd = int(new["last_channel_updated_at"] or 0) > 0
    return {
        "channel_name": new["channel_name"] or cur["channel_name"],
        "channel_image_url": new["channel_image_url"] or cur["channel_image_url"],
        "follower_count": new["follower_count"] if upd else cur["follower_count"],
        "verified_mark": new["verified_mark"],
        "representative_clip_uid": new["representative_clip_uid"],
        "tagged_clip_count": new["tagged_clip_count"],
        "last_channel_updated_at": (new["last_channel_updated_at"] if upd
                                    else cur["last_channel_updated_at"]),
    }


# '이 행을 다시 써야 하는가'를 정하는 필드들. **`last_channel_updated_at`은 일부러
# 빠져 있다.** 그 값은 `now if info else 0`이고 `fetch_channel`은 캐시 적중 시에도
# info dict를 돌려주므로 사실상 매 회차 새 `now`가 찍힌다. 여기에 넣어 두면 데이터가
# 하나도 안 바뀌어도 전원이 '바뀐 행'이 되어 written == considered가 영구히 유지된다
# (실측 2026-08-01 운영: 1155/1155가 6회 연속, 1156/1156 포함).
#
# 그래서 이 필드의 의미가 바뀐다: **"채널 API를 마지막으로 확인한 시각"이 아니라
# "의미 있는 값 변경이 있어 이 행을 마지막으로 쓴 시각"**이다. 값이 그대로면 갱신되지
# 않으므로 freshness 지표로 읽으면 안 된다. 지금 이 컬럼을 읽는 곳은 UPSERT의
# `excluded.last_channel_updated_at > 0` 게이트뿐이고 그 값은 항상 새 payload에서
# 오므로(저장값을 읽지 않는다) 안전하다. 진짜 freshness 추적이 필요해지면 별도 설계다.
_STREAMER_FIELDS = ("channel_name", "channel_image_url", "follower_count",
                    "verified_mark", "representative_clip_uid",
                    "tagged_clip_count")


async def _upsert_streamers_bulk(rows: list[dict], now: int) -> dict:
    """값이 바뀐 스트리머만 한 번에 쓴다. **COMMIT하지 않는다**(호출자 트랜잭션).

    반환은 관측용 집계 — 몇 명을 보고 몇 명을 실제로 썼는지.
    """
    if not rows:
        return {"considered": 0, "written": 0}
    db = await get_db()
    cur_rows = {r["channel_id"]: dict(r) for r in await (await db.execute(
        "SELECT channel_id, channel_name, channel_image_url, follower_count, "
        "verified_mark, representative_clip_uid, tagged_clip_count, "
        "last_channel_updated_at FROM singcup_streamers WHERE event_id=?",
        (EVENT_ID,))).fetchall()}

    params = []
    for s in rows:
        cur = cur_rows.get(s["channel_id"])
        target = _streamer_target(s, cur, now)
        if cur is not None and all(
                _norm(target[f]) == _norm(cur[f]) for f in _STREAMER_FIELDS):
            continue                      # 바뀐 값이 없다 — 쓰지 않는다
        params.append((s["channel_id"], EVENT_ID, s["channel_name"],
                       s["channel_image_url"], s["follower_count"],
                       s["verified_mark"], s["representative_clip_uid"],
                       s["tagged_clip_count"], s["last_channel_updated_at"], now))
    if params:
        await db.executemany(_STREAMER_UPSERT_SQL, params)
    return {"considered": len(rows), "written": len(params)}


def _norm(v):
    """0/None/'' 같은 표현 차이 때문에 '바뀌었다'로 오판하지 않게 정규화한다."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return int(v)
    return v


def snapshot_bucket(ts: int) -> int:
    """스냅샷 시간 버킷 — KST 정시 절삭. KST는 UTC+9 정시라 epoch 절삭으로 같다."""
    return int(ts) - int(ts) % 3600


async def _save_snapshots(ranked: list[dict], now: int) -> int:
    """이력 저장. **시간 버킷당 한 세트만** 남는다.

    예전에는 recompute_ranking이 불릴 때마다(코드 9곳, 최대 4분 간격) 전원분을
    무조건 INSERT했다. 값이 그대로여도 쌓여 최악 하루 37만 행이 된다.
    이제 UNIQUE(event_id, owner_channel_id, snapshot_bucket) + INSERT OR IGNORE라
    같은 시간에 몇 번을 불러도 첫 세트만 남는다(동시 실행도 DB가 막는다).
    반환값은 실제로 들어간 행 수.
    """
    db = await get_db()
    bucket = snapshot_bucket(now)
    cur = await db.executemany(
        "INSERT OR IGNORE INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at,"
        " snapshot_bucket) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(EVENT_ID, r["clip_uid"], r["owner_channel_id"], r["heart_count"], r["view_count"],
          r.get("follower_count", 0), r["score"], r["rank"], now, bucket) for r in ranked])
    return max(0, cur.rowcount or 0)


async def ensure_hourly_snapshot(now: int | None = None) -> bool:
    """이 시간 버킷의 이력이 아직 없으면 한 세트 남긴다. 저장했으면 True.

    이력 저장을 '정각 회차 완료'에 묶어 두면, 회차가 한 시간을 넘기는 순간
    이력이 통째로 끊긴다(실측: 회차 소요 117분 → 완료 0회 → 새 스냅샷 0건).
    그러면 1시간 증감의 기준 회차가 사라져 화면의 증감이 전부 굳는다.

    그래서 저장은 **시각**에만 묶는다 — 가벼운 정기 루프가 매 시간 버킷의 첫 틱에
    한 번 남긴다. 버킷당 유니크라 몇 번을 불러도 한 세트다.
    시각 기준이라 회차가 늦어져도 기준선은 제때 생기고, 회차가 도는 동안
    현재값이 그 기준선에서 멀어지므로 증감이 실제로 관측된다.
    """
    now = now or int(time.time())
    bucket = snapshot_bucket(now)
    db = await get_db()
    row = await (await db.execute(
        "SELECT 1 FROM singcup_snapshots WHERE event_id=? AND snapshot_bucket=? LIMIT 1",
        (EVENT_ID, bucket))).fetchone()
    if row is not None:
        return False
    await recompute_ranking(now, save_snapshot=True)
    _log({"event": "hourly_snapshot", "bucket": _iso(bucket)})
    return True


async def snapshot_duplicate_report() -> dict:
    """기존 스냅샷 중복 현황(read-only). 삭제하지 않는다.

    버킷 컬럼 도입 전 행은 snapshot_bucket이 NULL이라 유니크 제약 밖에 있다.
    실제로 얼마나 중복인지 먼저 눈으로 본 뒤에 정리 여부를 결정하기 위한 보고다.
    """
    db = await get_db()
    row = await (await db.execute(
        "SELECT COUNT(*) total,"
        " SUM(CASE WHEN snapshot_bucket IS NULL THEN 1 ELSE 0 END) legacy,"
        " COUNT(DISTINCT collected_at) runs,"
        " MIN(collected_at) oldest, MAX(collected_at) newest"
        " FROM singcup_snapshots WHERE event_id=?", (EVENT_ID,))).fetchone()
    dup = await (await db.execute(
        "SELECT COUNT(*) buckets, SUM(n) rows_in_dup FROM ("
        "  SELECT COUNT(*) n FROM singcup_snapshots WHERE event_id=?"
        "  AND snapshot_bucket IS NULL"
        "  GROUP BY owner_channel_id, (collected_at - collected_at % 3600)"
        "  HAVING n > 1)", (EVENT_ID,))).fetchone()
    total = int(row["total"] or 0)
    legacy = int(row["legacy"] or 0)
    return {
        "total_rows": total, "legacy_rows_without_bucket": legacy,
        "bucketed_rows": total - legacy,
        "distinct_collected_at": int(row["runs"] or 0),
        "legacy_duplicate_buckets": int(dup["buckets"] or 0),
        "legacy_rows_in_duplicate_buckets": int(dup["rows_in_dup"] or 0),
        "oldest": _iso(row["oldest"]), "newest": _iso(row["newest"]),
        "note": "기존 행은 삭제하지 않았습니다. 정리는 별도 승인 후 진행하세요.",
    }


# ── 삭제 상태 기계 ──────────────────────────────────────────────────────────
# (예전의 _reconcile_missing_clips는 여기로 대체됐다. 그 함수는 호출자가 없어
#  운영에서 한 번도 실행되지 않았고, 목록 미발견만으로 비활성화하는 방식이라
#  목록 스캔이 중간에 끊기면 살아 있는 클립을 내려 버릴 위험도 있었다.)
#
# 판정 원칙 — **강한 신호에만 카운터를 올린다.**
#   강함: 상세 API가 HTTP 404/410(본문에 삭제 표시)     → 카운터 +1
#   약함: 카드에 interaction/vod 없음, timeout, 429, 5xx → 카운터 그대로.
#         다만 '한 번 확인해 볼 대상'으로 표시만 해 둔다(의심).
# 확정은 **서로 다른 시점의 명시적 404가 DELETION_CONFIRM_CHECKS회** 모였을 때만.
DEL_ACTIVE = "active"
DEL_SUSPECTED = "suspected_deleted"
DEL_CONFIRMED = "confirmed_deleted"
DEL_RECOVERED = "recovered"
# 이 기능 이전에 이미 active=0이던 행. **삭제로 판정한 적이 없다.**
# 마이그레이션이 기존 비활성 행을 confirmed_deleted로 바꾸면 강한 신호를 한 번도
# 보지 않고 삭제를 확정하는 셈이고 되돌릴 근거도 남지 않는다. 그래서 표시만 한다.
DEL_UNKNOWN_LEGACY = "unknown_legacy"
# 살아 있는 것으로 취급하는 상태(대표 후보·스윕 대상)
DEL_ALIVE_STATES = (DEL_ACTIVE, DEL_SUSPECTED, DEL_RECOVERED)

DELETION_CONFIRM_CHECKS = int(os.getenv("SINGCUP_DELETION_CONFIRM_CHECKS", "2"))
# 두 번째 확인은 전체 스윕 한 바퀴(실측 약 43분)를 기다리지 않는다 — 그만큼
# 삭제 클립이 순위를 차지하는 시간이 길어진다. 의심 클립만 따로, 짧게 다시 본다.
DELETION_MIN_INTERVAL_SECONDS = int(float(
    os.getenv("SINGCUP_DELETION_MIN_INTERVAL_MINUTES", "10")) * 60)
# 확정된 클립도 완전히 잊지는 않는다(복원될 수 있다). 훨씬 긴 주기로만 확인한다.
DELETION_RECHECK_HOURS = float(os.getenv("SINGCUP_DELETION_RECHECK_HOURS", "6"))
# 4분 루프 한 번에 확인할 최대 건수. 요청이 몰리지 않게 작게 둔다.
DELETION_BATCH = int(os.getenv("SINGCUP_DELETION_BATCH", "20"))


async def probe_clip_alive(client, clip_uid: str) -> tuple[str, int | None, str]:
    """이 클립이 실제로 삭제됐는지 **상세 API로만** 확인한다.

    반환: ("deleted" | "alive" | "unknown", http_status, 짧은 사유)

    `_get_json`을 쓰지 않는 이유: 그쪽은 404를 SchemaError로 뭉뚱그리고 재시도까지
    돌려서, 상태코드와 본문을 그대로 볼 수 없다. 삭제 판정은 이 두 가지가 근거다
    (실측 응답: HTTP 404 + {"code":404,"message":"삭제된 클립입니다."}).

    **429/5xx/timeout/401/403은 unknown이다.** 삭제로 세면 일시 장애 한 번에
    멀쩡한 클립이 순위에서 사라진다.
    """
    url = CLIP_DETAIL_API.format(uid=quote(clip_uid, safe=""))
    headers = dict(_HEADERS)
    headers["Referer"] = f"https://chzzk.naver.com/clips/{quote(clip_uid, safe='')}"
    try:
        r = await client.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        return ("unknown", None, type(e).__name__)
    _api_counter["calls"] += 1
    code = r.status_code
    if code == 200:
        try:
            payload = r.json()
        except (json.JSONDecodeError, ValueError):
            return ("unknown", code, "not_json")
        content = payload.get("content") if isinstance(payload, dict) else None
        if isinstance(content, dict):
            # 살아 있지만 블라인드/삭제 표시가 붙은 경우도 노출 대상이 아니다
            blind = str(content.get("blindType") or "").upper()
            if blind in _BAD_BLIND:
                return ("deleted", code, f"blind_{blind.lower()}")
            return ("alive", code, "ok")
        return ("unknown", code, "no_content")
    if code == 404:
        # **상태코드만으로 삭제로 보지 않는다.** 경로 오타·프록시·라우팅 오류도
        # 404를 준다. 치지직의 삭제 응답은 JSON이고 본문에 code=404가 들어 있다
        # (실측: {"code":404,"message":"삭제된 클립입니다."}). HTML이거나 본문이
        # 그 모양이 아니면 '우리가 잘못 부른 것'일 수 있으므로 unknown이다.
        ctype = (r.headers.get("content-type") or "").lower()
        if "json" not in ctype:
            return ("unknown", code, "http_404_not_json")
        try:
            body = r.json()
        except (json.JSONDecodeError, ValueError):
            return ("unknown", code, "http_404_bad_json")
        if not isinstance(body, dict) or int(body.get("code") or 0) != 404:
            return ("unknown", code, "http_404_unexpected_body")
        return ("deleted", code, "http_404")
    if code == 410:
        # 410은 이 API에서 **한 번도 관측된 적이 없다.** 의미가 확인되기 전까지
        # 삭제 확정 근거로 쓰지 않는다 — 별도로 세기만 한다.
        _api_counter["http_410"] = _api_counter.get("http_410", 0) + 1
        return ("unknown", code, "http_410_unverified")
    if code == 429:
        _api_counter["http_429"] += 1
    return ("unknown", code, f"http_{code}")


# 대표 변경은 **owner 단위**로 직렬화한다. clip 락만으로는 부족하다 — 같은
# 스트리머의 서로 다른 클립 두 개가 동시에 삭제 확정되면 각자 다른 대표를 고른다.
#
# 락 순서는 어디서나 **clip → owner** 로 고정한다(교착 방지). 대표 변경 경로만
# 두 락을 함께 쓰고, 나머지 경로(스윕·수동 갱신·정기 작업)는 clip 락만 쓴다.
# 전체 획득 그래프는 tests/test_singcup_deletion.py의 락 순서 테스트가 고정한다.
# 대표 변경 트랜잭션은 **전용 연결**로 돈다. 공유 연결을 쓰면 최악 시간에 상한이
# 없다 — aiosqlite는 연결마다 워커 스레드 하나로 작업을 직렬화하므로 앞선 작업을
# 기다리는 큐 대기가 busy_timeout 밖이고, 실측상 앞선 느린 작업 1/2/3개에 대해
# 550 / 1,075 / 1,783ms로 선형 증가했다(상한 없음).
#
# 삭제 처리는 공개 요청이 아니다. 40초를 기다리느니 짧게 실패하고 다음 4분 회차에
# 다시 시도하는 편이 낫다.
OWNER_TX_BUSY_TIMEOUT_MS = int(os.getenv("SINGCUP_OWNER_TX_BUSY_TIMEOUT_MS", "2000"))
OWNER_TX_ATTEMPTS = int(os.getenv("SINGCUP_OWNER_TX_ATTEMPTS", "3"))
OWNER_TX_BUDGET_SECONDS = float(os.getenv("SINGCUP_OWNER_TX_BUDGET_SECONDS", "3.0"))
# owner 락 자체를 잡고/놓는 쓰기도 전용 연결로 한다. 기존 acquire_named_lock은
# 공유 연결을 쓰는데, 그러면 락 획득이 공유 큐 뒤에서 상한 없이 기다린다
# (실측: 앞선 느린 작업 3개 → 1,783ms, 상한 없음). 그 사이 clip 락은 계속 잡혀 있다.
OWNER_LOCK_TX_BUSY_TIMEOUT_MS = int(
    os.getenv("SINGCUP_OWNER_LOCK_TX_BUSY_TIMEOUT_MS", "500"))
OWNER_LOCK_TX_ATTEMPTS = int(os.getenv("SINGCUP_OWNER_LOCK_TX_ATTEMPTS", "2"))
OWNER_LOCK_TX_BUDGET_SECONDS = float(
    os.getenv("SINGCUP_OWNER_LOCK_TX_BUDGET_SECONDS", "1.0"))
# 확정 로그·후처리 여유
OWNER_POST_SLACK_SECONDS = 0.25


def _owner_lock_hold_worst_seconds() -> float:
    """**락을 실제로 획득한 시점부터** 놓을 때까지의 최악 시간.

    획득을 기다린 시간은 포함하지 않는다(아직 락을 쥐고 있지 않다). 포함하는 것:
      대표 변경 트랜잭션 하드 예산 + 정리 여유
      + owner release 시도 상한(전용 연결, 하드 예산)
      + 로그·짧은 후처리 여유

    전부 절대 deadline으로 묶인 값이라 상한 없는 항이 없다.
    """
    from utils.db_write import isolated_worst_case_seconds
    tx = isolated_worst_case_seconds(budget_seconds=OWNER_TX_BUDGET_SECONDS)
    rel = isolated_worst_case_seconds(budget_seconds=OWNER_LOCK_TX_BUDGET_SECONDS)
    return tx + rel + OWNER_POST_SLACK_SECONDS


# 이전 이름 유지(테스트·로그가 참조한다)
_owner_lock_worst_seconds = _owner_lock_hold_worst_seconds

# 안전 최소값 = 락 보유 최악 × 1.5. 환경변수로 이보다 작게 주면 **clamp**한다.
#
# 왜 거부(예외)가 아니라 clamp인가: 이 값이 너무 작으면 트랜잭션이 도는 중에 락이
# 만료돼 두 워커가 같은 owner의 대표를 동시에 바꾼다 — 조용히 깨지는 종류의 오류라
# 기본값으로 흘려보내면 안 된다. 그렇다고 기동을 막으면 오타 하나로 백엔드 전체가
# 내려간다. 안전한 하한이 계산으로 구해지므로 그 값으로 올리고 경고를 남긴다.
OWNER_LOCK_TTL_MIN = int(_owner_lock_hold_worst_seconds() * 1.5) + 1
_ttl_raw = os.getenv("SINGCUP_OWNER_LOCK_TTL")
if _ttl_raw is None:
    OWNER_LOCK_TTL = OWNER_LOCK_TTL_MIN
else:
    try:
        _ttl = int(_ttl_raw)
    except ValueError:
        _ttl = 0
    OWNER_LOCK_TTL = max(_ttl, OWNER_LOCK_TTL_MIN)
    if OWNER_LOCK_TTL != _ttl:
        print(f"[singcup_clips] SINGCUP_OWNER_LOCK_TTL={_ttl_raw!r} 는 안전 최소값 "
              f"{OWNER_LOCK_TTL_MIN}초보다 작아 {OWNER_LOCK_TTL}초로 올립니다 "
              f"(락 보유 최악 {_owner_lock_hold_worst_seconds():.2f}초 × 1.5).",
              flush=True)


async def acquire_owner_lock(owner_channel_id: str) -> str | None:
    """owner 락을 **전용 연결**로 잡는다. 공유 큐 뒤에서 기다리지 않는다.

    실패(잠금·미획득)하면 None. 호출자는 아무것도 바꾸지 않고 다음 회차에 재시도한다.
    """
    name = owner_lock_name(owner_channel_id)
    token = uuid.uuid4().hex[:12]
    got = {"ok": False}

    async def _work(conn):
        now = int(time.time())
        await conn.execute(
            "INSERT OR IGNORE INTO singcup_locks (name, locked_until, owner) "
            "VALUES (?,0,'')", (name,))
        cur = await conn.execute(
            "UPDATE singcup_locks SET locked_until=?, owner=? "
            "WHERE name=? AND locked_until < ?",
            (now + OWNER_LOCK_TTL, token, name, now))
        got["ok"] = cur.rowcount == 1

    ok = await db_write_isolated(
        DB_PATH, _work, what="acquire_owner_lock",
        busy_timeout_ms=OWNER_LOCK_TX_BUSY_TIMEOUT_MS,
        attempts=OWNER_LOCK_TX_ATTEMPTS,
        budget_seconds=OWNER_LOCK_TX_BUDGET_SECONDS, log=_log)
    return token if (ok and got["ok"]) else None


async def release_owner_lock(owner_channel_id: str, token: str) -> bool:
    """토큰이 일치할 때만 놓는다. 실패해도 TTL이 지나면 회수된다."""
    name = owner_lock_name(owner_channel_id)

    async def _work(conn):
        await conn.execute(
            "UPDATE singcup_locks SET locked_until=0, owner='' "
            "WHERE name=? AND owner=?", (name, token))

    return await db_write_isolated(
        DB_PATH, _work, what="release_owner_lock",
        busy_timeout_ms=OWNER_LOCK_TX_BUSY_TIMEOUT_MS,
        attempts=OWNER_LOCK_TX_ATTEMPTS,
        budget_seconds=OWNER_LOCK_TX_BUDGET_SECONDS, log=_log)


async def renew_owner_lock(owner_channel_id: str, token: str) -> bool:
    """토큰이 일치할 때만 임대를 연장한다(전용 연결)."""
    name = owner_lock_name(owner_channel_id)
    ok = {"n": False}

    async def _work(conn):
        cur = await conn.execute(
            "UPDATE singcup_locks SET locked_until=? WHERE name=? AND owner=?",
            (int(time.time()) + OWNER_LOCK_TTL, name, token))
        ok["n"] = cur.rowcount == 1

    wrote = await db_write_isolated(
        DB_PATH, _work, what="renew_owner_lock",
        busy_timeout_ms=OWNER_LOCK_TX_BUSY_TIMEOUT_MS,
        attempts=OWNER_LOCK_TX_ATTEMPTS,
        budget_seconds=OWNER_LOCK_TX_BUDGET_SECONDS, log=_log)
    return bool(wrote and ok["n"])


class _UnexpectedRowcount(RuntimeError):
    """UPDATE가 예상과 다른 행 수를 건드렸다 — 커밋하지 않고 롤백한다."""


def owner_lock_name(owner_channel_id: str) -> str:
    return f"singcup_owner:{EVENT_ID}:{owner_channel_id}"


# 새 대표 후보. **정렬은 pick_representative와 같아야 한다** — 두 곳이 갈라지면
# 트랜잭션 안에서 고른 대표와 직후 recompute_ranking이 고른 대표가 달라져 대표가
# 두 번 바뀐다(화면이 깜빡이고 증감 기준선도 두 번 끊긴다).
#   하트↓ → 조회수↓ → 생성 시각↑ → clip_uid↑
# (생성 시각은 **오름차순**이다. 같은 지표면 먼저 올린 클립을 대표로 본다.)
#
# 수동 지정(override)은 그 정렬 **앞에** 한 칸 얹는다 — `pick_representative`의
# override 처리와 같은 의미다(자동 규칙 자체는 손대지 않는다). 이 LEFT JOIN이
# 대표를 고르는 두 번째이자 마지막 지점이다.
#
# override 클립이 지금 삭제되는 그 클립이면 `clip_uid <> ?`에 걸려 후보에서 빠지고
# 자동 규칙이 그대로 적용된다 — 무효 override는 여기서도 자동 복귀한다.
_NEW_REP_SQL = """
    SELECT c.clip_uid, c.heart_count, c.view_count
    FROM singcup_clips c
    LEFT JOIN singcup_representative_overrides o
           ON o.event_id = c.event_id
          AND o.owner_channel_id = c.owner_channel_id
          AND o.override_clip_uid = c.clip_uid
          AND o.cleared_at IS NULL
    WHERE c.event_id = ?
      AND c.owner_channel_id = ?
      AND c.active = 1
      AND c.deletion_state <> 'confirmed_deleted'
      AND c.clip_uid <> ?
      AND c.created_at >= ? AND c.created_at <= ?
      AND (c.blind_type IS NULL OR c.blind_type = ''
           OR UPPER(c.blind_type) NOT IN ('BLIND','DELETE','DELETED','PRIVATE'))
    ORDER BY (o.id IS NOT NULL) DESC,
             c.heart_count DESC, c.view_count DESC, c.created_at ASC, c.clip_uid ASC
    LIMIT 1
"""


# ── 권위 감사 힌트 (singcup_audit) ──────────────────────────────────────────
# 순환 import를 피하려고 지연 import한다. 그리고 **감사 실패가 수집·스윕으로
# 번지지 않게** 여기서 삼킨다 — 힌트는 우선순위일 뿐이라 없어도 Cold lane이
# 결국 같은 클립을 검사한다.
async def _audit_hint(clip_uid: str, reason: str) -> bool:
    try:
        import singcup_audit
        return await singcup_audit.hint_clip(clip_uid, reason)
    except Exception as e:
        _log({"event": "audit_hint_failed", "level": "warning",
              "clip_uid": clip_uid, "reason": reason, "detail": str(e)[:120]})
        return False


async def _audit_hint_siblings(owner_channel_id: str, new_clip_uid: str) -> int:
    try:
        import singcup_audit
        return await singcup_audit.hint_owner_siblings(
            owner_channel_id, exclude_uid=new_clip_uid)
    except Exception as e:
        _log({"event": "audit_hint_failed", "level": "warning",
              "owner_channel_id": owner_channel_id, "detail": str(e)[:120]})
        return 0


async def _audit_note_frozen(clip_uid: str, rounds: int) -> bool:
    try:
        import singcup_audit
        return await singcup_audit.note_metrics_frozen(clip_uid, rounds)
    except Exception:
        return False


async def _flag_deletion_suspect(clip_uid: str, reason: str, now: int) -> bool:
    """약한 신호 — '한 번 확인해 보라'는 표시만 남긴다. **카운터는 올리지 않는다.**

    deletion_last_at을 0으로 둬서, 다음 확인 루프가 최소 간격에 걸리지 않고 바로
    한 번 볼 수 있게 한다. 이미 의심/확정인 행은 건드리지 않는다.
    """
    hit = {"n": 0}

    async def _work(db):
        cur = await db.execute(
            "UPDATE singcup_clips SET deletion_state=?, deletion_reason=?, "
            "deletion_first_at=CASE WHEN deletion_first_at=0 THEN ? "
            "                       ELSE deletion_first_at END, "
            "row_updated_at=? WHERE clip_uid=? AND deletion_state IN (?,?)",
            (DEL_SUSPECTED, reason, now, now, clip_uid, DEL_ACTIVE, DEL_RECOVERED))
        hit["n"] = cur.rowcount

    if not await db_write(get_db, _work, what="flag_deletion_suspect", log=_log):
        return False
    if not hit["n"]:
        return False
    _log({"event": "clip_deletion_suspected", "level": "warning",
          "clip_uid": clip_uid, "from": DEL_ACTIVE, "to": DEL_SUSPECTED,
          "checks": 0, "reason": reason})
    return True


async def _deletion_confirm_step(row: dict, now: int, reason: str) -> bool:
    """명시적 삭제 신호 1회를 반영한다. 확정됐으면 True.

    같은 순간에 두 번 세지 않도록 **최소 간격**을 둔다 — "서로 다른 시점의 확인
    2회"가 규칙이지, "한 번의 응답을 두 번 세는 것"이 아니다.
    """
    uid = row["clip_uid"]
    last = int(row["deletion_last_at"] or 0)
    if last and now - last < DELETION_MIN_INTERVAL_SECONDS:
        return False
    checks = int(row["missing_scan_count"] or 0) + 1
    confirmed = checks >= DELETION_CONFIRM_CHECKS
    state = DEL_CONFIRMED if confirmed else DEL_SUSPECTED

    async def _work(db):
        # 상태·카운터·active를 **한 문장으로** 바꾼다. 나눠 쓰면 active=0만 저장되고
        # 상태가 옛 값으로 남는 중간 상태가 생긴다.
        await db.execute(
            "UPDATE singcup_clips SET deletion_state=?, missing_scan_count=?, "
            "deletion_last_at=?, deletion_reason=?, "
            "deletion_first_at=CASE WHEN deletion_first_at=0 THEN ? "
            "                       ELSE deletion_first_at END, "
            "active=CASE WHEN ?=1 THEN 0 ELSE active END, row_updated_at=? "
            "WHERE clip_uid=?",
            (state, checks, now, reason, now, 1 if confirmed else 0, now, uid))

    if not confirmed:
        # 아직 의심 단계 — 대표를 건드리지 않는다. 짧은 UPDATE 하나로 끝난다.
        if not await db_write(get_db, _work, what="deletion_suspect_step", log=_log):
            return False
        _log({"event": "clip_deletion_suspected", "level": "warning", "clip_uid": uid,
              "owner_channel_id": row.get("owner_channel_id"),
              "from": row.get("deletion_state"), "to": state,
              "checks": checks, "reason": reason})
        return False

    # 확정 — 비활성화와 대표 재선정을 **한 트랜잭션**으로 묶는다.
    return await _confirm_deleted_and_reselect(row, now, reason, checks)


async def _confirm_deleted_and_reselect(row: dict, now: int, reason: str,
                                        checks: int) -> bool:
    """삭제 확정 + 대표 재선정. **네트워크 호출은 이미 끝난 뒤에 불린다.**

    트랜잭션 안에서는 DB만 만진다 — 치지직 API를 잡은 채 트랜잭션을 열면 그 시간만큼
    쓰기 잠금이 유지되고, 그게 예전에 'database is locked'를 만든 구조다.

    순서: owner 락 → 트랜잭션 시작 → 상태 재조회 → 확정 → 새 대표 선정 → 대표 갱신
          → commit → (트랜잭션 밖) recompute_ranking / 캐시 무효화

    중간 어느 단계가 실패해도 전부 롤백된다. '기존 대표만 비활성화되고 새 대표가
    지정되지 않은' 부분 상태를 남기지 않는다.
    """
    uid = row["clip_uid"]
    owner = row.get("owner_channel_id") or ""

    # 락 없이 먼저 본다. 이미 끝난 상태면 어차피 아무것도 바꾸지 않는데, 락 획득만
    # 해도 쓰기 트랜잭션이 2건(획득·해제) 나간다. 실측에서 noop이 몰린 구간에
    # `acquire_owner_lock` 잠금 포기가 반복됐다.
    # **이건 최적화일 뿐 최종 판정이 아니다** — 아래 트랜잭션 안에서 다시 확인한다.
    pre = await (await (await get_db()).execute(
        "SELECT deletion_state FROM singcup_clips WHERE clip_uid=? AND event_id=?",
        (uid, EVENT_ID))).fetchone()
    if pre is None:
        _log({"event": "clip_deletion_noop", "clip_uid": uid, "note": "row_gone",
              "precheck": True})
        return False
    if pre["deletion_state"] == DEL_CONFIRMED:
        _log({"event": "clip_deletion_noop", "clip_uid": uid,
              "note": "already_confirmed", "precheck": True})
        return False

    # owner 락도 **전용 연결**로 잡는다. 공유 연결을 쓰면 락 획득이 공유 큐 뒤에서
    # 상한 없이 기다리고, 그동안 clip 락이 계속 잡혀 있다.
    try:
        token = await acquire_owner_lock(owner)
    except Exception as e:                          # noqa: BLE001
        _log({"event": "clip_deletion_skipped_lock_error", "level": "warning",
              "clip_uid": uid, "owner_channel_id": owner, "detail": str(e)[:120]})
        return False
    if token is None:
        # 같은 스트리머의 다른 대표 변경이 진행 중이다. 아무것도 바꾸지 않고
        # 다음 회차에 다시 시도한다(부분 상태를 만들지 않는 것이 우선).
        _log({"event": "clip_deletion_skipped_owner_locked", "clip_uid": uid,
              "owner_channel_id": owner, "reason": reason})
        return False

    outcome = {"confirmed": False, "new_rep": None, "note": "", "streamer_rows": 0}
    try:
        async def _work(db):
            # ④-0 락이 아직 내 것인지 확인한다. TTL이 지나 남이 가져갔다면
            #      이 트랜잭션을 진행하면 안 된다(대표를 둘이 바꾸게 된다).
            lk = await (await db.execute(
                "SELECT owner, locked_until FROM singcup_locks WHERE name=?",
                (owner_lock_name(owner),))).fetchone()
            # 만료 판정은 **벽시계**로 한다. 호출자가 넘긴 논리적 now는 상태 판단용
            # 시각이라 락 TTL과 기준이 다를 수 있고, 그때 멀쩡한 락이 만료로 보인다.
            wall = int(time.time())
            if lk is None or lk["owner"] != token or int(lk["locked_until"]) <= wall:
                outcome["note"] = "owner_lock_lost"
                return
            # ④ probe 이후 상태가 바뀌었을 수 있다 — 트랜잭션 안에서 다시 읽는다
            cur = await (await db.execute(
                "SELECT deletion_state, missing_scan_count, owner_channel_id, active "
                "FROM singcup_clips WHERE clip_uid=? AND event_id=?",
                (uid, EVENT_ID))).fetchone()
            if cur is None:
                outcome["note"] = "row_gone"
                return
            if cur["deletion_state"] == DEL_CONFIRMED:
                outcome["note"] = "already_confirmed"      # 멱등
                return
            if cur["deletion_state"] not in (DEL_SUSPECTED, DEL_UNKNOWN_LEGACY):
                # 그 사이 살아난 것으로 확인됐다(recovered/active) — 되돌리지 않는다
                outcome["note"] = f"state_changed:{cur['deletion_state']}"
                return

            # ⑤ 확정 + 비활성화. **rowcount가 예상과 다르면 커밋하지 않는다.**
            up = await db.execute(
                "UPDATE singcup_clips SET deletion_state=?, missing_scan_count=?, "
                "deletion_last_at=?, deletion_reason=?, "
                "deletion_first_at=CASE WHEN deletion_first_at=0 THEN ? "
                "                       ELSE deletion_first_at END, "
                "active=0, row_updated_at=? WHERE clip_uid=?",
                (DEL_CONFIRMED, checks, now, reason, now, now, uid))
            if up.rowcount != 1:
                raise _UnexpectedRowcount(f"clips update rowcount={up.rowcount}")

            # ⑥ 같은 owner의 새 대표 후보(같은 트랜잭션 안에서)
            cand = await (await db.execute(
                _NEW_REP_SQL,
                (EVENT_ID, cur["owner_channel_id"], uid,
                 int(START_AT.timestamp()), int(END_AT.timestamp())))).fetchone()

            # ⑦/⑧ 대표 갱신. 후보가 없으면 NULL로 비운다.
            #     representative_clip_uid는 NULL 허용이고, `/main`은 이 컬럼을
            #     JOIN하므로 NULL이면 그 스트리머가 목록에서 빠진다 — 삭제된 클립을
            #     대표로 계속 들고 있는 것보다 안전하다. 새 유효 클립이 발견되면
            #     recompute_ranking이 자동으로 다시 채운다(행은 그대로 남는다).
            #
            # 스트리머 행이 아직 없을 수도 있다(한 번도 랭킹에 오르지 않은 owner).
            # 그때는 갱신할 대상이 없는 것이 정상이므로 rowcount 0을 허용한다.
            # channel_id가 PRIMARY KEY라 2 이상은 나올 수 없지만, 나오면 멈춘다.
            exists = await (await db.execute(
                "SELECT COUNT(*) n FROM singcup_streamers "
                "WHERE channel_id=? AND event_id=?",
                (cur["owner_channel_id"], EVENT_ID))).fetchone()
            n_streamer = int(exists["n"])
            if n_streamer > 1:
                raise _UnexpectedRowcount(f"streamer rows={n_streamer}")
            if n_streamer == 1:
                rep_up = await db.execute(
                    "UPDATE singcup_streamers SET representative_clip_uid=?, "
                    "row_updated_at=? WHERE channel_id=? AND event_id=?",
                    (cand["clip_uid"] if cand else None, now,
                     cur["owner_channel_id"], EVENT_ID))
                if rep_up.rowcount != 1:
                    raise _UnexpectedRowcount(
                        f"streamers update rowcount={rep_up.rowcount}")
            outcome["confirmed"] = True
            outcome["new_rep"] = cand["clip_uid"] if cand else None
            outcome["streamer_rows"] = n_streamer

        try:
            # 전용 연결 — 공유 큐 뒤에서 기다리지 않는다. 예산 안에 못 끝내면
            # 아무것도 바꾸지 않고 다음 삭제 검사 회차에서 다시 시도한다.
            if not await db_write_isolated(
                    DB_PATH, _work, what="confirm_and_reselect",
                    busy_timeout_ms=OWNER_TX_BUSY_TIMEOUT_MS,
                    attempts=OWNER_TX_ATTEMPTS,
                    budget_seconds=OWNER_TX_BUDGET_SECONDS, log=_log):
                return False                               # ⑨ 잠금 소진 → 전부 롤백됨
        except _UnexpectedRowcount as e:
            # db_write가 이미 rollback했다. 예상 밖의 rowcount는 데이터가 우리가 아는
            # 모양이 아니라는 뜻이라 커밋하지 않는다 — 다음 회차에 다시 본다.
            _log({"event": "clip_deletion_rowcount_mismatch", "level": "warning",
                  "clip_uid": uid, "owner_channel_id": owner, "detail": str(e)[:120]})
            return False
    finally:
        # 해제도 잠길 수 있다. 실패해도 TTL(OWNER_LOCK_TTL)이 지나면 회수된다.
        try:
            if not await release_owner_lock(owner, token):
                _log({"event": "owner_lock_release_failed", "level": "warning",
                      "clip_uid": uid, "owner_channel_id": owner,
                      "ttlSeconds": OWNER_LOCK_TTL})
        except Exception as e:                      # noqa: BLE001
            _log({"event": "owner_lock_release_failed", "level": "warning",
                  "clip_uid": uid, "owner_channel_id": owner,
                  "ttlSeconds": OWNER_LOCK_TTL, "detail": str(e)[:120]})

    if not outcome["confirmed"]:
        if outcome["note"]:
            _log({"event": "clip_deletion_noop", "clip_uid": uid,
                  "owner_channel_id": owner, "note": outcome["note"]})
        return False

    _log({"event": "clip_deletion_confirmed", "level": "warning", "clip_uid": uid,
          "owner_channel_id": owner, "from": row.get("deletion_state"),
          "to": DEL_CONFIRMED, "checks": checks, "reason": reason})
    _log({"event": "representative_clip_changed", "owner_channel_id": owner,
          "from_clip_uid": uid, "to_clip_uid": outcome["new_rep"],
          "cause": "deleted"})
    # ⑪ 캐시는 트랜잭션 밖에서 즉시 버린다 — TTL(20초) 동안 옛 대표를 보여주지 않는다.
    invalidate_main_cache()
    return True


async def _deletion_clear(row: dict, now: int) -> bool:
    """살아 있는 것이 확인됐다 — 카운터를 지우고 되살린다.

    반환은 '확정 상태에서 복구됐는가'다(대표 재계산이 필요한 경우).
    """
    was_deleted = row.get("deletion_state") == DEL_CONFIRMED
    if row.get("deletion_state") == DEL_ACTIVE and not int(row.get("missing_scan_count") or 0):
        return False
    state = DEL_RECOVERED if was_deleted else DEL_ACTIVE

    async def _work(db):
        await db.execute(
            "UPDATE singcup_clips SET deletion_state=?, missing_scan_count=0, "
            "deletion_first_at=0, deletion_last_at=0, deletion_reason='', "
            "active=1, row_updated_at=? WHERE clip_uid=?",
            (state, now, row["clip_uid"]))

    if not await db_write(get_db, _work, what="deletion_clear", log=_log):
        return False
    _log({"event": "clip_deletion_recovered", "level": "warning",
          "clip_uid": row["clip_uid"], "owner_channel_id": row.get("owner_channel_id"),
          "from": row.get("deletion_state"), "to": state,
          "checks": int(row.get("missing_scan_count") or 0), "reason": "alive"})
    return was_deleted


# 확인 대기열의 우선순위. 한 스윕 회차에서 카드가 비는 클립이 수백 건 나올 수 있어
# (실측: 2,766건 중 349건 실패), 단순히 '오래된 순'으로 두면 **대표 클립이 일반 클립
# 수백 건 뒤에 줄을 선다**. 대표 클립이 굳으면 그 스트리머의 순위가 통째로 틀어지므로
# 가장 먼저 본다.
_DUE_PRIORITY_SQL = """
    CASE
        -- 0) 현재 대표인데 카드가 비었다 — 순위에 바로 영향을 준다
        WHEN c.deletion_state = 'suspected_deleted'
             AND s.representative_clip_uid IS NOT NULL
             AND c.deletion_reason = 'card_empty'                        THEN 0
        -- 1) 현재 대표이고 의심 상태(사유 무관)
        WHEN c.deletion_state = 'suspected_deleted'
             AND s.representative_clip_uid IS NOT NULL                   THEN 1
        -- 2) 이미 한 번 이상 404를 받은 의심 클립(확정까지 한 걸음)
        WHEN c.deletion_state = 'suspected_deleted'
             AND c.missing_scan_count > 0                                THEN 2
        -- 3) 카드가 빈 일반 클립
        WHEN c.deletion_state = 'suspected_deleted'
             AND c.deletion_reason = 'card_empty'                        THEN 3
        -- 4) 나머지 의심(목록 미발견 등)
        WHEN c.deletion_state = 'suspected_deleted'                      THEN 4
        -- 5) 기능 도입 전부터 active=0이던 행 — 분류만 해 둔다
        WHEN c.deletion_state = 'unknown_legacy'                         THEN 5
        -- 6) 확정된 클립의 정기 생존 확인
        ELSE                                                                  6
    END
"""


async def _touch_legacy_check(clip_uid: str, now: int) -> None:
    """legacy 행의 확인 시각만 갱신한다(상태·active는 그대로)."""
    async def _work(db):
        await db.execute(
            "UPDATE singcup_clips SET deletion_last_at=?, deletion_reason=?, "
            "row_updated_at=? WHERE clip_uid=? AND deletion_state=?",
            (now, "legacy_alive", now, clip_uid, DEL_UNKNOWN_LEGACY))
    await db_write(get_db, _work, what="touch_legacy_check", log=_log)


async def _deletion_due(now: int, limit: int) -> list[dict]:
    """지금 확인할 클립. 대표 > 의심 > legacy > 확정 순, 각자 다른 재확인 간격."""
    db = await get_db()
    rows = await (await db.execute(
        "SELECT c.clip_uid, c.owner_channel_id, c.deletion_state, c.deletion_last_at, "
        "       c.missing_scan_count, c.deletion_reason, "
        "       (s.representative_clip_uid IS NOT NULL) AS is_rep, "
        f"      {_DUE_PRIORITY_SQL} AS prio "
        "FROM singcup_clips c "
        "LEFT JOIN singcup_streamers s ON s.representative_clip_uid = c.clip_uid "
        "WHERE c.event_id=? AND c.deletion_state IN (?,?,?) "
        "ORDER BY prio ASC, c.deletion_last_at ASC, c.clip_uid ASC "
        "LIMIT ?",
        (EVENT_ID, DEL_SUSPECTED, DEL_CONFIRMED, DEL_UNKNOWN_LEGACY,
         limit * 4))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        last = int(d["deletion_last_at"] or 0)
        # 상태마다 다시 보는 주기가 다르다.
        #   의심   10분 — 확정까지 오래 끌면 그동안 순위가 틀어져 있다
        #   확정    6시간 — 복원 확인만 하면 되므로 아주 뜸하게
        #   legacy  6시간 — 급할 것이 없다(분류 목적)
        gap = (DELETION_MIN_INTERVAL_SECONDS if d["deletion_state"] == DEL_SUSPECTED
               else DELETION_RECHECK_HOURS * 3600)
        if last and now - last < gap:
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


async def run_deletion_checks(limit: int | None = None) -> dict:
    """의심/확정 클립만 상세 API로 확인하고 상태를 옮긴다.

    새 워커를 만들지 않는다 — 기존 4분 루프에서 소량씩 부른다. 대상이 없으면
    요청이 한 건도 나가지 않는다.
    """
    now = int(time.time())
    due = await _deletion_due(now, limit or DELETION_BATCH)
    if not due:
        return {"status": ST_OK, "checked": 0, "confirmed": 0,
                "recovered": 0, "unknown": 0}

    client = _get_client()
    confirmed = recovered = unknown = skipped = 0
    for row in due:
        # 같은 clip_uid를 스윕·수동 갱신이 처리 중이면 건드리지 않는다. 상태 변경과
        # 지표 갱신이 겹치면 active=0만 저장되고 대표가 옛 UID로 남는 중간 상태가
        # 생길 수 있다. 락은 이 클립에만, 짧게 걸린다.
        token = await acquire_clip_lock(row["clip_uid"])
        if token is None:
            skipped += 1
            continue
        try:
            verdict, code, why = await probe_clip_alive(client, row["clip_uid"])
            if verdict == "deleted":
                if await _deletion_confirm_step(row, now, why):
                    confirmed += 1
            elif verdict == "alive":
                if row["deletion_state"] == DEL_UNKNOWN_LEGACY:
                    # 살아 있는 것은 확인됐지만 **되살리지 않는다.** 이 행이 왜
                    # active=0이 됐는지 우리는 모르고(사람이 내렸을 수도 있다),
                    # 자동 복구는 그 판단을 조용히 뒤집는 셈이다. 확인 시각만 밀어
                    # 두고 사람이 /clips/deleted 감사 목록에서 결정하게 한다.
                    await _touch_legacy_check(row["clip_uid"], now)
                elif await _deletion_clear(row, now):
                    recovered += 1
            else:
                unknown += 1
                _log({"event": "clip_deletion_unknown", "clip_uid": row["clip_uid"],
                      "http_status": code, "reason": why})
        finally:
            await release_clip_lock(row["clip_uid"], token)
        await asyncio.sleep(PAGE_DELAY)

    if confirmed or recovered:
        # 대표·점수·순위·캐시·Split 스냅샷까지 **정상 경로로** 다시 만든다.
        # 대표를 여기서 직접 고르지 않는다 — 규칙이 두 곳으로 갈라지면 안 된다.
        await recompute_ranking(int(time.time()), client=client)
    return {"status": ST_OK, "checked": len(due), "confirmed": confirmed,
            "recovered": recovered, "unknown": unknown, "skipped": skipped}


async def deleted_clip_audit(limit: int = 200) -> dict:
    """삭제로 확정된 행 목록. **롤백 대상을 특정하기 위한 감사 쿼리다.**

    물리 삭제가 없으므로 언제든 되돌릴 수 있다. 다만 되돌릴 때 '전부 active=1'로
    쓸면 진짜 삭제된 클립까지 살아나 순위가 다시 틀어진다. 그래서 확정 시각·근거·
    확인 횟수를 함께 보여 주고, 되돌릴 대상을 골라 넘기게 한다.
    """
    db = await get_db()
    rows = await (await db.execute(
        "SELECT clip_uid, owner_channel_id, deletion_state, deletion_reason, "
        "       missing_scan_count, deletion_first_at, deletion_last_at, "
        "       heart_count, view_count, active "
        "FROM singcup_clips WHERE event_id=? AND deletion_state IN (?,?) "
        "ORDER BY deletion_last_at DESC LIMIT ?",
        (EVENT_ID, DEL_CONFIRMED, DEL_UNKNOWN_LEGACY, limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["deletion_first_at"] = (_iso(d["deletion_first_at"])
                                  if d["deletion_first_at"] else None)
        # 확정 뒤에는 스윕 대상에서 빠져 이 값이 더 움직이지 않는다 = 확정 시각
        d["deleted_at"] = (_iso(d["deletion_last_at"])
                           if d["deletion_last_at"] else None)
        d.pop("deletion_last_at", None)
        out.append(d)
    return {"eventId": EVENT_ID, "count": len(out), "clips": out,
            "note": "행은 물리 삭제되지 않았습니다. restore_deleted_clips로 되돌립니다."}


async def restore_deleted_clips(clip_uids: list[str], *, reason: str = "manual") -> dict:
    """지정한 clip_uid만 되살린다(롤백 경로).

    **전체를 무조건 되돌리지 않는다.** 대상을 명시적으로 받는 이유는, 진짜 삭제된
    클립까지 살아나면 순위가 다시 틀어지기 때문이다. 감사 목록(deleted_clip_audit)에서
    되돌릴 것만 골라 넘긴다. 되돌린 뒤에는 정상 경로로 대표·점수·순위·캐시·스냅샷을
    다시 만든다.
    """
    if not clip_uids:
        return {"restored": 0, "clips": []}
    db = await get_db()
    now = int(time.time())
    qs = ",".join("?" for _ in clip_uids)
    rows = await (await db.execute(
        f"SELECT clip_uid, owner_channel_id, deletion_state FROM singcup_clips "
        f"WHERE event_id=? AND clip_uid IN ({qs})",
        (EVENT_ID, *clip_uids))).fetchall()
    targets = [dict(r) for r in rows
               if r["deletion_state"] in (DEL_CONFIRMED, DEL_UNKNOWN_LEGACY)]
    if not targets:
        return {"restored": 0, "clips": []}

    ids = [t["clip_uid"] for t in targets]
    marks = ",".join("?" for _ in ids)

    async def _work(conn):
        await conn.execute(
            f"UPDATE singcup_clips SET deletion_state=?, missing_scan_count=0, "
            f"deletion_first_at=0, deletion_last_at=0, deletion_reason=?, "
            f"active=1, row_updated_at=? WHERE clip_uid IN ({marks})",
            (DEL_RECOVERED, f"restored:{reason}"[:60], now, *ids))

    if not await db_write(get_db, _work, what="restore_deleted_clips", log=_log):
        return {"restored": 0, "clips": [], "error": "db_locked"}
    for t in targets:
        _log({"event": "clip_deletion_recovered", "level": "warning",
              "clip_uid": t["clip_uid"], "owner_channel_id": t["owner_channel_id"],
              "from": t["deletion_state"], "to": DEL_RECOVERED,
              "checks": 0, "reason": f"restored:{reason}"})
    await recompute_ranking(int(time.time()))
    return {"restored": len(targets), "clips": ids}


async def recheck_clip_deletion(clip_uid: str) -> dict:
    """관리자용 단건 재확인. **DB를 직접 고치지 않고** 정상 판정 경로를 탄다."""
    db = await get_db()
    row = await (await db.execute(
        "SELECT clip_uid, owner_channel_id, deletion_state, deletion_last_at, "
        "       missing_scan_count FROM singcup_clips WHERE clip_uid=?",
        (clip_uid,))).fetchone()
    if row is None:
        return {"clip_uid": clip_uid, "found": False}
    r = dict(row)
    now = int(time.time())
    # 락 순서를 자동 경로와 **똑같이** 맞춘다: clip → owner.
    # (owner 락은 _confirm_deleted_and_reselect 안에서만 잡힌다.)
    clip_token = await acquire_clip_lock(clip_uid, wait=CLIP_LOCK_WAIT_SECONDS)
    if clip_token is None:
        return {"clip_uid": clip_uid, "found": True, "verdict": "skipped",
                "reason": "clip_locked", "changed": False}
    try:
        return await _recheck_locked(r, clip_uid, now)
    finally:
        await release_clip_lock(clip_uid, clip_token)


async def _recheck_locked(r: dict, clip_uid: str, now: int) -> dict:
    db = await get_db()
    verdict, code, why = await probe_clip_alive(_get_client(), clip_uid)
    changed = False
    if verdict == "deleted":
        # 수동 확인은 최소 간격을 우회한다(운영자가 직접 누른 것이다).
        # 그래도 **확인 횟수 규칙은 그대로다** — 1회로 확정되지 않는다.
        r["deletion_last_at"] = 0
        changed = await _deletion_confirm_step(r, now, why)
    elif verdict == "alive":
        changed = await _deletion_clear(r, now)
    if changed:
        await recompute_ranking(int(time.time()))
    after = await (await db.execute(
        "SELECT deletion_state, missing_scan_count, active FROM singcup_clips "
        "WHERE clip_uid=?", (clip_uid,))).fetchone()
    return {"clip_uid": clip_uid, "found": True, "verdict": verdict,
            "http_status": code, "reason": why, "changed": changed,
            "state": after["deletion_state"], "checks": after["missing_scan_count"],
            "active": after["active"]}


async def _representative_overrides() -> dict[str, str]:
    """활성 수동 지정 전부. 실패해도 랭킹 계산을 멈추지 않는다.

    여기서 예외를 올리면 그 회차의 랭킹 재계산이 통째로 건너뛰어지고, 화면은
    **모든 참가자**의 순위가 낡은 채로 남는다. override가 한 회차 빠지는 것은
    해당 스트리머 한 명이 자동 대표로 보이는 것뿐이고 다음 회차에 복구된다.
    범위가 훨씬 좁은 쪽을 택한다(로그로는 드러난다).
    """
    try:
        import singcup_overrides
        return await singcup_overrides.active_override_map(EVENT_ID)
    except Exception as e:                              # noqa: BLE001
        _log({"event": "representative_overrides_load_failed", "level": "warning",
              "detail": str(e)[:160]})
        return {}


def _build_reps(tagged: list[dict],
                overrides: dict[str, str] | None = None) -> list[dict]:
    """스트리머(ownerChannelId)별 대표 클립 1개만 남긴다.

    `overrides`는 {owner_channel_id: clip_uid} 수동 지정이다. 여기서 반영해야
    `recompute_ranking`이 저장하는 `representative_clip_uid`가 곧 effective
    representative가 되고, 그 컬럼을 읽는 모든 소비자(`/main`·점수·movers·스냅샷·
    스윕 `_TARGET_SQL`)가 구조적으로 같은 대표를 본다.
    """
    overrides = overrides or {}
    by_owner: dict[str, list[dict]] = {}
    for c in tagged:
        by_owner.setdefault(c["owner_channel_id"], []).append(c)
    reps = []
    for owner, clips in by_owner.items():
        rep = dict(pick_representative(clips, overrides.get(owner)))
        rep["tagged_clip_count"] = len(clips)
        reps.append(rep)
    return reps


# (예전의 collect_clips_once는 백필 워커(run_backfill)로 대체됐다 —
#  과거 적재와 신규 탐색을 한 작업으로 처리하던 구조를 분리했다.)

async def recompute_ranking(now: int, *, client=None,
                            save_snapshot: bool = False) -> list[dict]:
    """대표 클립·점수·순위를 다시 계산한다.

    **이력(스냅샷) 저장은 기본으로 하지 않는다.** 순위 재계산은 화면을 바로
    맞추기 위해 자주 불러야 하지만, 이력은 시간당 한 세트면 충분하다. 예전에는
    둘이 붙어 있어서 재계산 빈도가 그대로 DB 증가율이 됐다.
    스냅샷은 정각 전체 회차(singcup_sweep)만 save_snapshot=True로 남긴다.
    """
    db = await get_db()
    # 대표 후보 조건: 같은 이벤트 · active · 삭제 확정이 아님.
    # (태그·기간·블라인드는 등록 시점에 이미 걸러져 이 표에 들어오지 않는다.)
    rows = [dict(r) for r in await (await db.execute(
        "SELECT * FROM singcup_clips WHERE event_id=? AND active=1 "
        "AND deletion_state<>?", (EVENT_ID, DEL_CONFIRMED)
    )).fetchall()]
    # 대표가 실제로 바뀌는지 보려면 '바꾸기 전' 값을 알아야 한다. 스트리머 수백 명에
    # 한 번씩 SELECT를 돌리지 않도록 여기서 한 번에 읽어 둔다.
    before_rep = {r["channel_id"]: r["representative_clip_uid"]
                  for r in await (await db.execute(
                      "SELECT channel_id, representative_clip_uid "
                      "FROM singcup_streamers WHERE event_id=?", (EVENT_ID,))).fetchall()}
    # 수동 지정(override)을 대표를 **고르는 시점**에 반영한다. 읽는 쪽에 얹지 않는
    # 이유는 singcup_overrides 모듈 주석 참고 — 소비자마다 JOIN을 복제하면 그
    # 복제본들이 갈라진다(split-brain).
    overrides = await _representative_overrides()
    ranked = compute_scores(_build_reps(rows, overrides))

    client = client or _get_client()

    # 팔로워만 채널 API가 필요하다. 참가자가 수백 명이라 순차로 부르면 루프가 몇 분씩
    # 멈추므로 동시성을 제한해 병렬로 부른다(캐시에 있으면 요청이 나가지 않는다).
    sem = asyncio.Semaphore(max(1, CARD_CONCURRENCY))
    infos: dict[str, dict] = {}

    async def load_channel(cid: str):
        async with sem:
            infos[cid] = await fetch_channel(client, cid) or {}

    await asyncio.gather(*[load_channel(r["owner_channel_id"]) for r in ranked])

    # ── 대표 확정 임계구역 ────────────────────────────────────────────────
    # 새 lock을 만들지 않는다. `shared_write_lock()`은 이미 **이 저장소의 모든 DB
    # 쓰기가 지나는 canonical 게이트**이고(`utils/db_write.py`), 그 주석이 정한
    # 계약이 정확히 우리가 필요한 것이다 — "외부 조회를 끝내는 것은 호출부의
    # 책임이고, 락 안에서는 DB 작업만 한다".
    #
    # 이 안에서 **재조회 → canonical 대표 계산 → upsert → commit**을 한 번에 끝낸다.
    # 재조회와 쓰기가 갈라져 있으면 그 사이에 poller가 저장·재선정을 끝내고,
    # 이쪽이 곧바로 옛 대표로 덮어쓴다(TOCTOU). poller의 `repick_representatives`도
    # `db_write()`를 통해 **같은 락**을 지나므로 두 경로가 서로 끼어들 수 없다.
    #
    # 외부 API·backoff·sleep은 이 안에 없다 — gather는 위에서 이미 끝났고, 여기
    # 남은 것은 read 2회 + executemany + commit뿐이라 hold time이 밀리초다.
    # `_audit_hint`와 변경 로그는 DB 작업이 아니거나 별도 쓰기라 **락 밖**으로 뺐다
    # (`asyncio.Lock`은 재진입이 안 되므로 안에서 `db_write`를 부르면 멈춘다).
    # 위의 `rows`는 gather 전에 읽은 것이고, 그 gather가 참가자 전원의 채널 API를
    # 부르느라 수십 초에서 수백 초까지 걸린다(실측 2026-08-04: 169.692초).
    # 그동안 한국 poller가 조회수를 저장하고 그 owner의 대표를 다시 골랐을 수
    # 있는데, 옛 `rows`로 만든 대표를 그대로 쓰면 **그 결과가 조용히 덮어써진다**
    # (last writer wins). 그러면 `/main`·스윕 `is_rep`·`singcup_streamers`가
    # 서로 다른 대표를 보게 된다.
    #
    # 대표 선정에 필요한 것은 **DB 안의 현재 metrics와 override뿐**이다(팔로워·
    # 닉네임은 정렬에 쓰이지 않는다). 그래서 여기서 그 둘만 다시 읽어 canonical
    # 대표를 다시 고른다 — 외부 호출 0건이라 이 재조회는 밀리초 단위다.
    # `infos`는 그대로 재사용한다(채널 API를 다시 부르지 않는다).
    # **`shared_write_lock()`은 직렬화 장치이지 rollback 장치가 아니다.**
    # 이 경로는 `db_write()`를 지나지 않고 공유 연결에 직접 커밋하므로, 예외나
    # 취소가 나면 미커밋 DML이 연결에 그대로 남는다. 그러면 다음 `db_write()`의
    # commit이 남의 부분 DML까지 함께 커밋하거나, 반대로 그쪽 rollback이 이쪽
    # 작업을 되돌린다. 그래서 여기서 **직접** 트랜잭션 종료를 보장한다.
    async with shared_write_lock():
      try:
          rows = [dict(r) for r in await (await db.execute(
              "SELECT * FROM singcup_clips WHERE event_id=? AND active=1 "
              "AND deletion_state<>?", (EVENT_ID, DEL_CONFIRMED)
          )).fetchall()]
          overrides = await _representative_overrides()
          ranked = compute_scores(_build_reps(rows, overrides))
          # 변경 로그도 재조회 시점 기준이어야 '무엇이 실제로 바뀌었나'가 맞는다.
          before_rep = {r["channel_id"]: r["representative_clip_uid"]
                        for r in await (await db.execute(
                            "SELECT channel_id, representative_clip_uid "
                            "FROM singcup_streamers WHERE event_id=?", (EVENT_ID,))).fetchall()}

          payload = []
          for r in ranked:
              info = infos.get(r["owner_channel_id"]) or {}
              # 닉네임·이미지는 목록 응답(ownerChannel)을 우선한다 — 채널 API가 실패해도
              # 이름이 비지 않아야 한다. 비면 화면에 '-'로 뜨고 검색에도 걸리지 않는다.
              name = r.get("owner_channel_name") or info.get("channel_name", "")
              image = r.get("owner_channel_image_url") or info.get("channel_image_url", "")
              r["follower_count"] = info.get("follower_count", 0)
              payload.append({
                  "channel_id": r["owner_channel_id"],
                  "channel_name": name,
                  "channel_image_url": image,
                  "follower_count": info.get("follower_count", 0),
                  "verified_mark": r.get("owner_verified") or info.get("verified_mark", 0),
                  "representative_clip_uid": r["clip_uid"],
                  "tagged_clip_count": r["tagged_clip_count"],
                  "last_channel_updated_at": now if info else 0,
              })
          # 값이 바뀐 행만 한 번에 쓴다(트랜잭션은 여전히 하나 — 부분 랭킹이 보이지 않는다)
          upsert_stat = await _upsert_streamers_bulk(payload, now)
          _rep_changes = [(r, before_rep.get(r["owner_channel_id"]))
                          for r in ranked
                          if before_rep.get(r["owner_channel_id"])
                          and before_rep.get(r["owner_channel_id"]) != r["clip_uid"]]
          # 여러 단계가 공유하는 쓰기 경로다(discover·recheck·deletion·snapshot).
          # 이름을 붙여 둬야 `loop_step_error`의 operation으로 드러난다.
          # **이 경로는 db_write를 지나지 않고 공유 연결에 직접 커밋한다** — 잠금이 나면
          # False가 아니라 예외로 올라와 그 회차의 뒤 단계가 통째로 건너뛰어진다.
          with _operation("recompute_ranking_commit"):
              if save_snapshot:
                  await _save_snapshots(ranked, now)
              await db.commit()
      except BaseException:
        # `except Exception`이면 **취소된 작업이 열린 트랜잭션을 남기고 사라진다.**
        # 롤백 뒤 원래 예외/취소를 그대로 올린다 — 삼키지 않는다.
        await _db_rollback(db)
        raise

    # 락 밖이다 — `_audit_hint`는 별도 쓰기 경로라 임계구역 안에서 부르면 재진입이 된다.
    for r, prev_uid in _rep_changes:
        # 대표가 바뀌면 1시간·24시간 증감이 새 클립 기준으로 다시 시작한다
        # (이전 클립의 하트를 빼면 서로 다른 영상을 비교하는 셈이다).
        _log({"event": "representative_clip_changed",
              "owner_channel_id": r["owner_channel_id"],
              "from_clip_uid": prev_uid, "to_clip_uid": r["clip_uid"],
              "heart_count": r["heart_count"], "view_count": r["view_count"]})
        # 밀려난 옛 대표를 권위 검사 앞줄에 세운다. 하트 역전으로 바뀐 것이
        # 대부분이지만, 대표가 삭제돼 바뀐 경우도 여기로 온다 — 어느 쪽인지는
        # 상세 API만 안다. **여기서 상태를 바꾸지 않는다.**
        await _audit_hint(prev_uid, "rep_changed")
    if upsert_stat["written"]:
        _log({"event": "streamers_upserted", "considered": upsert_stat["considered"],
              "written": upsert_stat["written"]})
    # 순위가 바뀌었으니 /main 캐시를 즉시 버린다 — TTL이 만료될 때까지 옛 순위를
    # 보여주면 "갱신했는데 화면이 안 바뀐다"는 바로 그 증상이 다시 생긴다.
    invalidate_main_cache()
    # 랭킹 계산이 완전히 끝난 지점 = 영속화의 유일한 시점.
    # 둘 다 best-effort다 — 실패해도 수집·스윕·랭킹 결과를 취소하지 않는다.
    #
    # **스냅샷 게시가 먼저다.** 화면이 보는 최신 랭킹이 부가 영속화보다 우선이기
    # 때문이다. 급상승 저장은 시간 예산(PERSIST_BUDGET_SECONDS) 안에서만 시도하지만,
    # 그마저도 스냅샷 뒤로 두면 게시가 그 예산만큼도 밀리지 않는다.
    # 두 작업은 같은 `/main` 캐시 항목을 공유하므로 계산은 한 번만 돈다.
    await publish_snapshot(source="recompute")
    await persist_top_movers_snapshot(source="recompute")
    return ranked


def event_meta() -> dict:
    return {"id": EVENT_ID, "startAt": START_AT.isoformat(),
            "endAt": END_AT.isoformat(), "status": event_status()}


# ── 수집 파이프라인 ─────────────────────────────────────────────────────────
# 세 작업의 성격이 달라 완전히 분리한다. 예전에는 '신규 탐색'과 '과거 적재'를 한
# 작업으로 처리해서, 초기 적재가 4분 주기에 묶여 수 시간씩 걸리는 구조적 지연이 있었다.
#
#   ① 백필   이벤트 시작일까지 거슬러 가는 1회성 적재.
#            완료될 때까지 배치를 연속 처리하고, 커서를 DB에 저장해 재시작 후에도 잇는다.
#   ② 신규   최신 페이지만 훑다가 '이미 아는 클립만 있는 페이지'를 만나면 즉시 종료.
#            정상 상태에서는 1~2페이지로 끝난다.
#   ③ 지표   이미 발견한 클립의 하트/조회수만 갱신. 목록을 다시 훑지 않고
#            저장해 둔 videoId/recId로 카드 API만 부른다. 대표 클립을 우선한다.
BATCH_SIZE = int(os.getenv("SINGCUP_BACKFILL_BATCH", "300"))
BATCH_PAUSE_SECONDS = float(os.getenv("SINGCUP_BACKFILL_BATCH_PAUSE", "3"))
BACKFILL_LOCK_TTL = int(os.getenv("SINGCUP_BACKFILL_LOCK_TTL", "300"))
# 신규 탐색: 안전장치용 상한(정상적으로는 1~2페이지에서 끝난다)
DISCOVER_MAX_PAGES = int(os.getenv("SINGCUP_DISCOVER_MAX_PAGES", "20"))
# 지표 갱신 — 대표 클립은 자주, 나머지는 느리게
# 화면의 '1시간' 증감 기준. 회차 간격(4분)으로 비교하면 대부분 0이라 의미가 없다.
DELTA_WINDOW_SECONDS = int(float(os.getenv("SINGCUP_DELTA_WINDOW_MINUTES", "60")) * 60)
# 기준 시각(now-1시간)에서 이만큼 안에 실제 수집 회차가 있어야 비교한다.
# 수집이 멈춰 있었으면 훨씬 오래된 회차와 비교하게 되는데, 그걸 '1시간 증감'이라고
# 표시하면 거짓말이 된다 → 회차가 없으면 '비교 데이터 없음'(null)으로 둔다.
# 1시간 전 기준 회차를 찾을 때 허용하는 오차.
# 스냅샷이 시간 버킷당 한 세트(= 시간당 1회)로 줄면서 ±15분으로는 기준 회차를
# 못 찾는 구간이 생긴다(예: 21:30에 20:30±15분을 보면 20:00·21:00 둘 다 밖).
# 35분이면 어떤 시각에서 보더라도 직전 버킷 하나는 반드시 들어온다.
# 실제로 비교한 시각은 응답의 summary.deltaBaseAt으로 그대로 내려간다.
DELTA_TOLERANCE_SECONDS = int(float(
    os.getenv("SINGCUP_DELTA_TOLERANCE_MINUTES", "35")) * 60)
REP_METRICS_TTL_MINUTES = float(os.getenv("SINGCUP_REP_METRICS_TTL_MINUTES", "5"))
METRICS_TTL_MINUTES = float(os.getenv("SINGCUP_METRICS_TTL_MINUTES", "45"))
REFRESH_PER_CYCLE = int(os.getenv("SINGCUP_REFRESH_PER_CYCLE", "80"))
# 한 사이클 예산 중 대표 클립에 예약하는 비율. 나머지는 대표든 아니든
# "가장 오래 갱신 안 된 순"으로 채워, 뒤쪽 클립이 굶지 않게 한다.
# 이 시간 이상 갱신이 비어 있다가 값이 바뀌면 "복구"로 본다(단기 증감에서 제외).
STALE_RECOVERY_SECONDS = int(float(
    os.getenv("SINGCUP_STALE_RECOVERY_MINUTES", "90")) * 60)
REP_SHARE = min(0.9, max(0.0, float(os.getenv("SINGCUP_REP_SHARE", "0.5"))))
RESCAN_UNTAGGED_HOURS = float(os.getenv("SINGCUP_RESCAN_UNTAGGED_HOURS", "24"))
RETRY_MAX_ATTEMPTS = int(os.getenv("SINGCUP_RETRY_MAX_ATTEMPTS", "3"))

BF_IDLE, BF_RUNNING, BF_PAUSED, BF_DONE, BF_FAILED = (
    "idle", "running", "paused", "completed", "failed")


# ── 이름 있는 분산 락 ───────────────────────────────────────────────────────
async def acquire_named_lock(name: str, ttl: int) -> str | None:
    """조건부 UPDATE의 rowcount로 획득을 판정한다(check-then-set 경합 방지).

    **주의 — 여기가 4분 루프 실패의 실제 지점이다(실측 2026-08-01).**
    이 쓰기는 `db_write`를 지나지 않고 공유 연결에 직접 커밋한다. 그래서
    `database is locked`가 예외로 그대로 올라오고, 이 함수가 회차의 **첫 쓰기**라
    회차가 시작하자마자 죽었다(`step=discover, operation=null, duration_ms=0`).

    `db_write`로 감싸 None(=미획득)으로 흘려보내는 것이 자연스러워 보이지만,
    시도해 보니 **P0 불변식 테스트를 건드린다** — `acquire_clip_lock`이 이 함수를
    쓰는 핫 패스라(회차당 수백 건) 락 획득 자체가 트랜잭션이 되어
    "락을 트랜잭션 **전에** 잡는다"는 P0 순서 검증이 깨진다. 별도 설계가 필요해
    여기서는 손대지 않았다. 지금은 단계 격리(P1-A2)가 피해를 그 단계로 가둔다.
    """
    now = int(time.time())
    token = uuid.uuid4().hex[:12]
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO singcup_locks (name, locked_until, owner) VALUES (?,0,'')",
        (name,))
    cur = await db.execute(
        "UPDATE singcup_locks SET locked_until=?, owner=? WHERE name=? AND locked_until < ?",
        (now + ttl, token, name, now))
    await db.commit()
    return token if cur.rowcount == 1 else None


async def renew_named_lock(name: str, token: str, ttl: int) -> bool:
    """장시간 작업이 TTL을 넘겨 다른 워커와 겹치지 않게 주기적으로 연장한다."""
    db = await get_db()
    cur = await db.execute(
        "UPDATE singcup_locks SET locked_until=? WHERE name=? AND owner=?",
        (int(time.time()) + ttl, name, token))
    await db.commit()
    return cur.rowcount == 1


async def release_named_lock(name: str, token: str):
    db = await get_db()
    await db.execute(
        "UPDATE singcup_locks SET locked_until=0, owner='' WHERE name=? AND owner=?",
        (name, token))
    await db.commit()


# ── 클립 단위 락 ────────────────────────────────────────────────────────────
# 같은 clip_uid의 지표를 고치는 경로가 여럿이다(정기 스윕 / 수동 전체 갱신 /
# 관리자 단건 / 신규 탐색·재시도·reconcile / retag·rediscover). 전역 락을 한
# 사이클 내내 잡으면 그동안 수동 조작이 전면 차단되므로, 클립 하나에만 짧게 건다.
# 프로세스 간에 유효해야 하므로 asyncio.Lock이 아니라 DB named lock을 쓴다.
def _worst_clip_seconds() -> float:
    """클립 하나를 잡고 있을 수 있는 최대 시간 — **상수에서 유도한다**.

    락을 쥔 채 벌어지는 일:
      카드 지표(공유 예산 안에서 최대 CARD_TRANSPORT_BUDGET회 호출)
      → 상세 조회(별도 논리 작업, 최대 MAX_RETRIES회) → DB 쓰기

    **호출 수를 중복 합산하지 않는 것**이 이 공식의 핵심이다. 예전 공식은
    `fetches × http_once` 였는데 `http_once` 자체가 `MAX_RETRIES × timeout`을
    품고 있어 카드 호출을 12회분으로 세고 있었다(실제 상한은 3회). 지금은 HTTP
    내부 재시도와 partial 재시도가 **같은 예산**을 쓰므로 호출 수가 곧 예산이다.

    임의로 정한 TTL을 쓰면 상수를 바꿨을 때 조용히 만료돼 두 작업이 같은 클립을
    동시에 만지게 된다. 그래서 상수에서 유도한다.
    """
    from database.db import BUSY_TIMEOUT_MS
    # HTTP 재시도 **사이**의 대기 합(호출 자체의 시간이 아니다)
    http_backoff = sum(min(BACKOFF_MAX, BACKOFF_BASE * (2 ** a)) + BACKOFF_BASE
                       for a in range(max(0, MAX_RETRIES - 1)))
    # 토큰 버킷은 최저 속도일 때 가장 오래 기다린다(1건/최저속도)
    min_rate = max(0.01, float(os.getenv("SINGCUP_SWEEP_MIN_RATE", "0.2")))
    token_wait = 1.0 / min_rate
    per_call = token_wait + REQUEST_TIMEOUT          # 호출 1회의 최악 소요
    db_attempts = max(1, int(os.getenv("SINGCUP_DB_RETRY_ATTEMPTS", "4")))
    db_base = float(os.getenv("SINGCUP_DB_RETRY_BASE_SECONDS", "0.05"))
    db_wait = (db_attempts * (BUSY_TIMEOUT_MS / 1000.0)
               + sum(db_base * (2 ** i) * 2 for i in range(db_attempts - 1)))
    # 카드 지표 — 공유 예산이 실제 호출 상한이다. 그 위에 HTTP 재시도 간 대기와
    # partial 재시도 대기 예산을 더한다(둘 다 '대기'이지 '호출'이 아니다).
    card = (CARD_TRANSPORT_BUDGET * per_call + http_backoff
            + PARTIAL_RETRY_BUDGET_SECONDS)
    # 상세 조회는 별도 논리 작업이라 예산을 공유하지 않는다.
    detail = MAX_RETRIES * per_call + http_backoff
    return card + detail + db_wait


# 유도값의 1.5배(안전계수).
# 상수를 바꾸면 이 값도 따라 움직이고, tests가 TTL ≥ 최악×1.2 를 강제한다.
CLIP_LOCK_TTL = int(os.getenv(
    "SINGCUP_CLIP_LOCK_TTL", str(int(_worst_clip_seconds() * 1.5) + 1)))


async def renew_clip_lock(clip_uid: str, token: str) -> bool:
    """소유 토큰이 맞을 때만 임대를 연장한다(owner 기반 lease).

    유도 TTL이 최악을 덮지만, 상수가 바뀌거나 예외적으로 늘어질 때를 위한
    안전장치로 남겨 둔다.
    """
    return await renew_named_lock(clip_lock_name(clip_uid), token, CLIP_LOCK_TTL)
CLIP_LOCK_WAIT_SECONDS = float(os.getenv("SINGCUP_CLIP_LOCK_WAIT", "2.0"))
CLIP_LOCK_POLL_SECONDS = float(os.getenv("SINGCUP_CLIP_LOCK_POLL", "0.2"))


def clip_lock_name(clip_uid: str) -> str:
    return f"singcup_clip:{clip_uid}"


async def acquire_clip_lock(clip_uid: str, *, wait: float | None = None) -> str | None:
    """clip_uid 락. 충돌 시 곧바로 포기하지 않고 잠깐 기다렸다 None을 돌려준다."""
    name = clip_lock_name(clip_uid)
    deadline = time.monotonic() + max(
        0.0, CLIP_LOCK_WAIT_SECONDS if wait is None else wait)
    while True:
        token = await acquire_named_lock(name, CLIP_LOCK_TTL)
        if token is not None:
            return token
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(CLIP_LOCK_POLL_SECONDS)


async def release_clip_lock(clip_uid: str, token: str | None):
    if token:
        await release_named_lock(clip_lock_name(clip_uid), token)


# ── scan / retry 상태 ───────────────────────────────────────────────────────
async def _scanned_uids() -> set[str]:
    db = await get_db()
    rows = await (await db.execute("SELECT clip_uid FROM singcup_clip_scan")).fetchall()
    return {r["clip_uid"] for r in rows}


async def _scan_state_of(uids: list[str]) -> dict[str, dict]:
    """스캔 기록을 상태까지 함께 읽는다.

    예전에는 (tagged, checked_at) 튜플을 돌려주면서 호출부가 **둘 다 무시하고**
    "기록이 있으면 건너뛴다"만 했다. 그래서 일시적 조회 실패가 영구 제외로 굳었다.
    """
    if not uids:
        return {}
    db = await get_db()
    qs = ",".join("?" for _ in uids)
    rows = await (await db.execute(
        f"SELECT clip_uid, tagged, checked_at, scan_status, next_check_at "
        f"FROM singcup_clip_scan WHERE clip_uid IN ({qs})", tuple(uids))).fetchall()
    return {r["clip_uid"]: {"tagged": int(r["tagged"] or 0),
                            "checked_at": int(r["checked_at"] or 0),
                            "status": r["scan_status"] or "",
                            "next_check_at": r["next_check_at"]} for r in rows}


def _scan_says_skip(st: dict | None, now: int) -> bool:
    """이 클립을 이번 탐색에서 건너뛸지. 기록이 있다는 이유만으로는 안 건너뛴다."""
    if st is None:
        return False                       # 처음 보는 클립 — 확인한다
    status = st["status"]
    if status in _TERMINAL:
        return True                        # 등록 완료 / 기간 밖 / 삭제 — 최종
    if not status:                         # 상태가 없는 예전 행은 기존 동작 유지
        return bool(st["tagged"])
    nxt = st["next_check_at"]
    return nxt is not None and now < int(nxt)   # 재확인 시각 전이면 건너뛴다


async def _metrics_snapshot(clip_uid: str) -> dict | None:
    """디버그용 — 그 클립의 현재 DB 수치와 24h 증감 기준값을 함께 읽는다.

    화면의 '24h 증감'은 heart_count가 아니라 (heart_count - 24시간 전 스냅샷)이라
    heart_count가 굳어 있어도 기준 스냅샷이 흘러가면 값이 계속 변한다.
    두 값을 같이 봐야 그 착시를 구분할 수 있다.
    """
    db = await get_db()
    row = await (await db.execute(
        "SELECT heart_count, view_count, metrics_ok, last_metrics_at, owner_channel_id "
        "FROM singcup_clips WHERE clip_uid=?", (clip_uid,))).fetchone()
    if row is None:
        return None
    base = await (await db.execute(
        "SELECT heart_count FROM singcup_snapshots WHERE event_id=? AND owner_channel_id=? "
        "AND collected_at <= ? ORDER BY collected_at DESC LIMIT 1",
        (EVENT_ID, row["owner_channel_id"], int(time.time()) - 86400))).fetchone()
    b = int(base["heart_count"]) if base else None
    return {"heart_count": int(row["heart_count"] or 0),
            "view_count": int(row["view_count"] or 0),
            "metrics_ok": int(row["metrics_ok"] or 0),
            "last_metrics_at": row["last_metrics_at"],
            "baseline_24h": b,
            "heart_delta_24h": (int(row["heart_count"] or 0) - b) if b is not None else None}


async def _apply_metrics(clip_uid: str, heart: int, view: int,
                         heart_ok: bool, view_ok: bool, now: int,
                         *, out: dict | None = None) -> str:
    """카드에서 읽은 수치를 반영한다. **읽은 필드만** 쓴다.

    예전에는 heart/view 둘 다 성공했을 때만 UPDATE를 돌렸다. 그래서 카드가
    조회수만 안 주는 클립은 하트까지 통째로 갱신이 멈춰 화면에 옛날 값이
    박혀 있었다(24h 증감은 24시간 전 스냅샷 쪽이 흘러가서 계속 변하니
    '하트만 안 변한다'로 보였다). 이제 필드 단위로 나눠 쓴다.

    반환값: "ok" | "partial" | "failed" — 호출부 집계에 그대로 쓴다.
    """
    db = await get_db()
    sets, params = [], []
    # 갱신 공백이 길었는데 하트가 움직였다면, 그 차이는 '최근 1시간 증가'가 아니라
    # 공백 동안 누적된 양이다. 복구 시각을 남겨 단기 증감 계산에서 빼도록 한다.
    prev = await (await db.execute(
        "SELECT heart_count, view_count, metrics_frozen_count, last_metrics_at, "
        "last_heart_at FROM singcup_clips WHERE clip_uid=?", (clip_uid,))).fetchone()
    # 지표가 몇 회차 연속 **완전히** 고정됐는지 센다. 삭제된 클립은 하트도 조회수도
    # 더 이상 움직이지 않아서 이 값이 계속 오른다 — 다만 인기 없는 정상 클립도
    # 마찬가지다. 그래서 이것은 **삭제 근거가 아니라 권위 검사 힌트**로만 쓴다.
    frozen = 0
    if prev is not None and heart_ok and view_ok:
        same = (heart == int(prev["heart_count"] or 0)
                and view == int(prev["view_count"] or 0))
        frozen = (int(prev["metrics_frozen_count"] or 0) + 1) if same else 0
        sets.append("metrics_frozen_count=?")
        params.append(frozen)
    if out is not None:
        out["frozen_rounds"] = frozen
    # 공백 판정 기준은 '하트를 마지막으로 정상 수신한 시각'이다. last_metrics_at을
    # 쓰면 조회수만 실패해 온 클립이 공백으로 오인된다.
    _prev_heart_at = (int(prev["last_heart_at"] or 0) or int(prev["last_metrics_at"] or 0)
                      ) if prev is not None else 0
    if (heart_ok and prev is not None
            and now - _prev_heart_at > STALE_RECOVERY_SECONDS
            and heart != int(prev["heart_count"] or 0)):
        sets.append("metrics_recovered_at=?")
        params.append(now)
        _log({"event": "metrics_recovered", "level": "warning", "clip_uid": clip_uid,
              "gap_hours": round((now - _prev_heart_at) / 3600, 2),
              "heart_from": int(prev["heart_count"] or 0), "heart_to": heart})
    if heart_ok:
        sets.append("heart_count=?")
        params.append(heart)
    if view_ok:
        sets.append("view_count=?")
        params.append(view)
    # metrics_ok는 지금까지처럼 '둘 다 온전한가'를 뜻한다(스키마 의미 유지)
    sets.append("metrics_ok=?")
    params.append(1 if (heart_ok and view_ok) else 0)

    # 시각을 네 개로 나눈다. 예전에는 last_metrics_at 하나가 '시도했다'와
    # '정상으로 받았다'를 겸해서, 둘 다 실패해도 now로 올라갔다. 그러면 실제
    # 값은 며칠 전 것인데 스케줄러는 방금 갱신된 정상 클립으로 판단한다.
    #   last_attempt_at  항상 갱신 — 같은 클립을 무한 재호출하지 않기 위한 시각
    #   last_heart_at    하트를 정상으로 받았을 때만
    #   last_view_at     조회수를 정상으로 받았을 때만
    #   last_metrics_at  **둘 다** 정상일 때만 — 이제 '신선함'의 진짜 근거
    sets.append("last_attempt_at=?")
    params.append(now)
    if heart_ok:
        sets.append("last_heart_at=?")
        params.append(now)
    if view_ok:
        sets.append("last_view_at=?")
        params.append(now)
    if heart_ok and view_ok:
        sets.append("last_metrics_at=?")
        params.append(now)
    sets += ["last_collected_at=?", "row_updated_at=?"]
    params += [now, now, clip_uid]
    await db.execute(
        f"UPDATE singcup_clips SET {', '.join(sets)} WHERE clip_uid=?", params)
    return "ok" if (heart_ok and view_ok) else "failed" if not (heart_ok or view_ok) \
        else "partial"


def _field_state(seen_at, count) -> str:
    """'한 번도 못 읽음'과 '진짜 0'을 구분한다 — **신규 컬럼 없이**.

    `last_view_at`/`last_heart_at`은 NOT NULL DEFAULT 0이라 sentinel이 NULL이
    아니라 0이다. 그리고 두 컬럼은 나중에 추가돼서 그 이전 행은 전부 0이다.
    그래서 시각만으로는 판정할 수 없고 값을 같이 봐야 한다.

      unknown          시각 0 · 값 0   한 번도 정상 수신하지 못함
      observed         시각>0 · 값>0   정상 수신
      observed_zero    시각>0 · 값 0   정상 수신, **진짜 0**
      observed_legacy  시각 0 · 값>0   컬럼 도입 이전에 수신된 값

    legacy 분기가 이 계약을 신규 스키마 없이 성립시키는 핵심이다. 값은 **오직**
    해당 필드를 정상 수신했을 때만 쓰이므로(_apply_metrics의 `if view_ok:` 블록이
    유일한 writer다), 0보다 크면 과거 어느 시점에 반드시 정상 수신된 것이다.
    """
    at, n = int(seen_at or 0), int(count or 0)
    if at > 0:
        return "observed" if n > 0 else "observed_zero"
    return "observed_legacy" if n > 0 else "unknown"


def view_state(row) -> str:
    return _field_state(row["last_view_at"], row["view_count"])


def heart_state(row) -> str:
    return _field_state(row["last_heart_at"], row["heart_count"])


def metrics_state(row, now: int, stale_seconds: int = 2 * 3600) -> str:
    """클립 지표의 신선도 — ok / partial / stale / failed.

    last_metrics_at(둘 다 정상)만 보면 부분 성공 클립이 영원히 stale로 보인다.
    필드별 시각을 함께 봐야 "하트는 최신인데 조회수만 옛날"을 구분할 수 있다.
    """
    h = int(row["last_heart_at"] or 0)
    v = int(row["last_view_at"] or 0)
    a = int(row["last_attempt_at"] or 0)
    fresh_h, fresh_v = (now - h) <= stale_seconds, (now - v) <= stale_seconds
    if h and v and fresh_h and fresh_v:
        return "ok"
    if (h and fresh_h) or (v and fresh_v):
        return "partial"
    if a and not h and not v:
        return "failed"
    return "stale"


async def _queue_retry(item: dict, err: str, now: int):
    """카드 조회 실패는 실패한 클립만 큐에 남긴다(지수 백오프로 재시도)."""
    db = await get_db()
    row = await (await db.execute(
        "SELECT attempts FROM singcup_clip_retry WHERE clip_uid=?",
        (str(item.get("clipUID")),))).fetchone()
    attempts = (int(row["attempts"]) if row else 0) + 1
    delay = min(3600, int(60 * (2 ** (attempts - 1))))
    d = parse_clip_date(item.get("createdDate"))
    await db.execute(
        """INSERT INTO singcup_clip_retry
               (clip_uid, video_id, rec_id, created_at, attempts, next_try_at,
                last_error, item_json)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(clip_uid) DO UPDATE SET
               attempts=excluded.attempts, next_try_at=excluded.next_try_at,
               last_error=excluded.last_error, item_json=excluded.item_json""",
        (str(item.get("clipUID")), str(item.get("videoId") or ""),
         str(item.get("recId") or ""), int(d.timestamp()) if d else None,
         attempts, now + delay, err[:200], json.dumps(item, ensure_ascii=False)))


async def _clear_retry(clip_uid: str):
    db = await get_db()
    await db.execute("DELETE FROM singcup_clip_retry WHERE clip_uid=?", (clip_uid,))


def _to_clip_row(item: dict, card: dict) -> dict:
    d = parse_clip_date(item.get("createdDate"))
    oc = item.get("ownerChannel") or {}
    return {
        # 목록 응답이 이미 채널명·이미지·인증마크를 준다 — 채널 API가 실패해도
        # 닉네임이 비지 않도록 여기서 함께 저장해 둔다(추가 요청이 필요 없다)
        "owner_channel_name": str(oc.get("channelName") or ""),
        "owner_channel_image_url": str(oc.get("channelImageUrl") or ""),
        "owner_verified": 1 if oc.get("verifiedMark") else 0,
        "clip_uid": str(item["clipUID"]),
        "owner_channel_id": str(item["ownerChannelId"]),
        "video_id": str(item.get("videoId") or ""),
        "rec_id": str(item.get("recId") or ""),
        "clip_title": str(item.get("clipTitle") or ""),
        "thumbnail_image_url": str(item.get("thumbnailImageUrl") or ""),
        "description": card["description"],
        "created_at": int(d.timestamp()),
        "heart_count": card["heart_count"],
        "view_count": card["view_count"],
        "duration": safe_count(item.get("duration")),
        "adult": 1 if item.get("adult") else 0,
        "blind_type": str(item.get("blindType") or ""),
        "metrics_ok": card["metrics_ok"],
    }


async def _scan_batch(client, items: list[dict], now: int) -> tuple[int, int, int]:
    """후보 묶음을 카드 조회해 저장한다. (태그된 수, 신규 저장 수, 실패 수).

    **단계가 엄격히 나뉜다: 외부 조회 → 락 획득 → 짧은 DB 트랜잭션 → 락 해제.**

    예전에는 한 루프 안에서 DML과 `acquire_clip_lock`(최대 2초 `asyncio.sleep`
    폴링)이 번갈아 실행됐다. 공유 연결은 `isolation_level=''`이라 첫 DML부터
    SQLite 쓰기 잠금을 붙들므로, **그 폴링 시간만큼 프로세스 전체와 봇 프로세스의
    쓰기까지 멈췄다.** 게다가 폴링 중 예외가 나면 트랜잭션이 열린 채 남아
    (`_scan_batch`에 rollback이 없었다) 그 뒤 모든 쓰기가 영구히 막혔다 —
    실측 2026-08-01: `loop_error: database is locked`가 46분 이상 지속되고
    `sweep_start`·`streamers_upserted`·`rising_collector 완료`가 전부 0이 됐다.

    이제 DB 구간에는 **DB 작업만** 있고, `db_write`가 커밋·롤백·재시도를 소유한다.
    """
    sem = asyncio.Semaphore(max(1, CARD_CONCURRENCY))
    results: list[tuple[dict, dict | None]] = []

    async def one(it):
        async with sem:
            card = await fetch_card(client, it)
        results.append((it, card))

    # ── 1) 외부 조회 (DB 접근 없음)
    await asyncio.gather(*[one(it) for it in items])

    # ── 2) 메모리에서 판정 (DB·네트워크 없음)
    failed_items: list[tuple[str, dict]] = []
    untagged: list[tuple[str, dict]] = []
    tagged_rows: list[tuple[str, dict, dict, dict]] = []
    for it, card in results:
        uid = str(it["clipUID"])
        if card is None:
            failed_items.append((uid, it))
            continue
        if not has_singcup_tag(card["description"]):
            untagged.append((uid, it))
            continue
        tagged_rows.append((uid, it, card, _to_clip_row(it, card)))

    # ── 3) 필요한 클립 락을 **트랜잭션 밖에서** 전부 받는다
    tokens: dict[str, str | None] = {}
    for uid, _it, _card, _row in tagged_rows:
        tokens[uid] = await acquire_clip_lock(uid)

    stat = {"inserted": 0}
    inserted_owners: list[tuple[str, str]] = []

    async def apply(_db):
        # 재시도로 다시 불릴 수 있으므로 누적 상태를 매번 초기화한다.
        stat["inserted"] = 0
        inserted_owners.clear()
        for uid, it in failed_items:
            await _queue_retry(it, "card fetch failed", now)
            # 카드 실패도 **스캔 상태로 남긴다**. 예전에는 재시도 큐에만 넣었는데,
            # 그 큐는 attempts가 한도(RETRY_MAX_ATTEMPTS)에 닿으면 다시 보지 않는다.
            # 그러면 그 클립은 singcup_clips에도 singcup_clip_scan에도 없는
            # '고아'가 되어, 목록에서 1페이지를 벗어나는 순간 영영 사라진다.
            await _scan_upsert(uid, SCAN_FETCH_FAILED, now, item=it,
                               error="discover: card fetch failed")
        for uid, it in untagged:
            await _clear_retry(uid)
            await _record_scan(uid, False, now, it)
        for uid, it, card, row in tagged_rows:
            await _clear_retry(uid)
            await _record_scan(uid, True, now, it)
            if await _upsert_clip(row, now):
                stat["inserted"] += 1
                inserted_owners.append((row["owner_channel_id"], uid))
            await _apply_metrics(uid, row["heart_count"], row["view_count"],
                                 card["heart_ok"], card["view_ok"], now)

    try:
        ok = await db_write(get_db, apply, what="singcup.scan_batch", log=_log)
    finally:
        for uid, tok in tokens.items():
            await release_clip_lock(uid, tok)

    if not ok:
        # 잠금으로 못 썼다 — 아무것도 저장되지 않았고(롤백됨) 다음 회차가 다시 본다.
        return 0, 0, len(items)

    # 새 클립이 들어온 소유자의 **기존** 클립을 권위 검사 앞줄에 세운다.
    # 지우고 다시 올리는 흐름이 흔해 신호가 세지만, 정상 클립을 여러 개 올린
    # 스트리머도 많으므로 **검사 예약만** 한다(비활성화 금지).
    # 자기 트랜잭션을 쓰므로 위 쓰기 구간 **밖에서** 부른다.
    for owner, uid in inserted_owners:
        await _audit_hint_siblings(owner, uid)
    return len(tagged_rows), stat["inserted"], len(failed_items)


# ── ① 백필 ─────────────────────────────────────────────────────────────────
async def get_backfill_state() -> dict:
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO singcup_backfill_state (event_id, status, updated_at) "
        "VALUES (?,?,?)", (EVENT_ID, BF_IDLE, int(time.time())))
    await db.commit()
    row = await (await db.execute(
        "SELECT * FROM singcup_backfill_state WHERE event_id=?", (EVENT_ID,))).fetchone()
    return dict(row)


async def _save_backfill(**fields):
    if not fields:
        return
    fields["updated_at"] = int(time.time())
    sets = ", ".join(f"{k}=?" for k in fields)
    db = await get_db()
    await db.execute(f"UPDATE singcup_backfill_state SET {sets} WHERE event_id=?",
                     (*fields.values(), EVENT_ID))
    await db.commit()


async def reset_backfill() -> dict:
    """처음부터 다시 훑는다(커서·수치 초기화)."""
    await get_backfill_state()
    await _save_backfill(status=BF_IDLE, next_cursor=None, scanned_count=0,
                         tagged_count=0, failed_count=0, pages_done=0,
                         oldest_scanned_created_at=None, started_at=None,
                         completed_at=None, last_error=None)
    return await get_backfill_state()


async def run_backfill() -> dict:
    """이벤트 시작일까지 연속으로 적재한다. 완료될 때까지 배치를 이어서 처리한다.

    - 커서(next_cursor)를 배치마다 DB에 저장하므로 재배포/재시작 후 이어서 진행한다
    - 이미 확인한 clipUID는 건너뛴다(중복 방지)
    - 락을 주기적으로 연장해 여러 워커가 겹치지 않게 한다
    """
    state = await get_backfill_state()
    if state["status"] == BF_DONE:
        return {"status": BF_DONE, "note": "이미 완료됨", **_bf_public(state)}

    token = await acquire_named_lock("singcup_backfill", BACKFILL_LOCK_TTL)
    if token is None:
        return {"status": state["status"], "note": "다른 백필 작업이 실행 중입니다."}

    client = _get_client()
    cursor = state["next_cursor"]
    scanned = int(state["scanned_count"] or 0)
    tagged_n = int(state["tagged_count"] or 0)
    failed_n = int(state["failed_count"] or 0)
    pages = int(state["pages_done"] or 0)
    oldest = state["oldest_scanned_created_at"]
    seen_cursors: set[str] = set()
    batch: list[dict] = []
    status = BF_RUNNING
    note = ""

    await _save_backfill(status=BF_RUNNING, last_error=None,
                         started_at=state["started_at"] or int(time.time()))
    _log({"event": "backfill_start", "cursor": cursor, "scanned": scanned})

    try:
        known = await _scanned_uids()
        while True:
            if not await renew_named_lock("singcup_backfill", token, BACKFILL_LOCK_TTL):
                note = "락을 잃었습니다(다른 워커가 실행 중일 수 있음)"
                status = BF_PAUSED
                break

            items, nxt = await fetch_clip_page(client, cursor)
            pages += 1
            if not items:
                status = BF_DONE
                break

            page_dates = []
            for it in items:
                scanned += 1
                d = parse_clip_date(it.get("createdDate"))
                if d:
                    page_dates.append(d)
                    ts = int(d.timestamp())
                    oldest = ts if oldest is None else min(int(oldest), ts)
                uid = str(it.get("clipUID") or "")
                if not uid or uid in known:
                    continue
                if is_candidate_clip(it, start=START_AT, end=END_AT):
                    known.add(uid)
                    batch.append(it)

            now = int(time.time())
            if len(batch) >= BATCH_SIZE:
                t, _ins, f = await _scan_batch(client, batch, now)
                tagged_n += t
                failed_n += f
                batch = []
                await _save_backfill(next_cursor=nxt, scanned_count=scanned,
                                     tagged_count=tagged_n, failed_count=failed_n,
                                     pages_done=pages, oldest_scanned_created_at=oldest)
                await asyncio.sleep(BATCH_PAUSE_SECONDS)

            # 종료: 페이지 전체가 시작일 이전이거나 커서가 끝났을 때
            if page_dates and all(d < START_AT for d in page_dates):
                status = BF_DONE
                break
            if nxt is None:
                status = BF_DONE
                break
            if nxt in seen_cursors:
                note = "동일 커서 반복 감지"
                status = BF_FAILED
                break
            seen_cursors.add(nxt)
            cursor = nxt
            await asyncio.sleep(PAGE_DELAY)

        # 남은 묶음 처리
        if batch:
            now = int(time.time())
            t, _ins, f = await _scan_batch(client, batch, now)
            tagged_n += t
            failed_n += f

        await _save_backfill(status=status, next_cursor=(None if status == BF_DONE else cursor),
                             scanned_count=scanned, tagged_count=tagged_n,
                             failed_count=failed_n, pages_done=pages,
                             oldest_scanned_created_at=oldest, last_error=note or None,
                             completed_at=int(time.time()) if status == BF_DONE else None)
        if status == BF_DONE:
            await recompute_ranking(int(time.time()), client=client)
        _log({"event": "backfill_end", "status": status, "pages": pages,
              "scanned": scanned, "tagged": tagged_n, "failed": failed_n, "note": note})
        return {"status": status, "pages": pages, "scanned": scanned,
                "tagged": tagged_n, "failed": failed_n, "note": note}

    except (FetchError, SchemaError) as e:
        # 실패해도 커서를 남겨 다음 실행에서 이어서 처리한다
        await _save_backfill(status=BF_PAUSED, next_cursor=cursor, scanned_count=scanned,
                             tagged_count=tagged_n, failed_count=failed_n,
                             pages_done=pages, oldest_scanned_created_at=oldest,
                             last_error=str(e)[:300])
        _log({"event": "backfill_failed", "level": "warning", "detail": str(e)[:200]})
        return {"status": BF_PAUSED, "note": str(e)[:200], "scanned": scanned}
    finally:
        await release_named_lock("singcup_backfill", token)


def _bf_public(s: dict) -> dict:
    oldest = s.get("oldest_scanned_created_at")
    return {
        "scannedCount": s.get("scanned_count") or 0,
        "taggedCount": s.get("tagged_count") or 0,
        "failedCount": s.get("failed_count") or 0,
        "pagesDone": s.get("pages_done") or 0,
        "nextCursor": s.get("next_cursor"),
        "oldestScannedCreatedAt": (datetime.fromtimestamp(int(oldest), _KST).isoformat()
                                   if oldest else None),
        "startedAt": (datetime.fromtimestamp(int(s["started_at"]), _KST).isoformat()
                      if s.get("started_at") else None),
        "updatedAt": (datetime.fromtimestamp(int(s["updated_at"]), _KST).isoformat()
                      if s.get("updated_at") else None),
        "completedAt": (datetime.fromtimestamp(int(s["completed_at"]), _KST).isoformat()
                        if s.get("completed_at") else None),
        "lastError": s.get("last_error"),
    }


async def backfill_status() -> dict:
    s = await get_backfill_state()
    return {"eventId": EVENT_ID, "status": s["status"],
            "targetStartAt": START_AT.isoformat(), **_bf_public(s)}


async def start_backfill_worker():
    """부팅 시 미완료 백필을 자동으로 이어서 돌린다."""
    if os.getenv("SINGCUP_ENABLED", "true").lower() in ("0", "false", "no"):
        return
    await asyncio.sleep(float(os.getenv("SINGCUP_BACKFILL_START_DELAY", "25")))
    while True:
        try:
            s = await get_backfill_state()
            if s["status"] in (BF_DONE,):
                return                       # 끝났으면 더 돌 필요가 없다
            if event_status() == "UPCOMING":
                await asyncio.sleep(600)
                continue
            res = await run_backfill()
            if res.get("status") == BF_DONE:
                return
        except Exception as e:
            _log({"event": "backfill_worker_error", "level": "warning",
                  "detail": str(e)[:200]})
        # 중단·일시정지 상태면 잠시 뒤 이어서 재시도한다
        await asyncio.sleep(float(os.getenv("SINGCUP_BACKFILL_RETRY_SECONDS", "60")))


# ── ② 신규 탐색 (가볍게) ────────────────────────────────────────────────────
async def discover_new_clips() -> dict:
    """최신 페이지만 훑어 새 클립을 찾는다.

    이미 아는 클립만 있는 페이지를 만나면 즉시 종료한다 — 정상 상태에서는 1~2페이지로
    끝나므로, 매번 수천 건을 다시 내려가던 예전 방식과 달리 부담이 거의 없다.
    """
    token = await acquire_named_lock("singcup_discover", 180)
    if token is None:
        return {"status": ST_SKIPPED, "note": "다른 탐색 작업이 실행 중입니다."}

    client = _get_client()
    pages = scanned = 0
    fresh: list[dict] = []
    status = ST_OK
    note = ""
    cursor = None
    try:
        for _ in range(DISCOVER_MAX_PAGES):
            items, nxt = await fetch_clip_page(client, cursor)
            pages += 1
            if not items:
                break
            scanned += len(items)
            uids = [str(it.get("clipUID") or "") for it in items if it.get("clipUID")]
            state = await _scan_state_of(uids)
            now_ts = int(time.time())
            new_here = 0
            for it in items:
                uid = str(it.get("clipUID") or "")
                if not uid or _scan_says_skip(state.get(uid), now_ts):
                    continue
                if is_candidate_clip(it, start=START_AT, end=END_AT):
                    fresh.append(it)
                # 처음 보는 클립만 '이 페이지에 새 게 있다'로 센다 — 재확인 대상까지
                # 세면 조기 종료가 풀려 매번 전 페이지를 훑게 된다.
                if uid not in state:
                    new_here += 1
            # 이 페이지가 전부 '아는 클립'이면 그 뒤는 볼 필요가 없다
            if new_here == 0:
                break
            if nxt is None:
                break
            cursor = nxt
            await asyncio.sleep(PAGE_DELAY)

        tagged = inserted = failed = 0
        if fresh:
            now = int(time.time())
            tagged, inserted, failed = await _scan_batch(client, fresh, now)
            if tagged:
                # 새 참가자가 생겼으므로 대표 클립·점수·순위를 다시 계산한다
                await recompute_ranking(now, client=client)
        _log({"event": "discover", "pages": pages, "scanned": scanned,
              "candidates": len(fresh), "tagged": tagged, "failed": failed})
        return {"status": status, "pages": pages, "scanned": scanned,
                "candidates": len(fresh), "tagged": tagged, "inserted": inserted,
                "failed": failed, "note": note}
    except (FetchError, SchemaError) as e:
        _log({"event": "discover_failed", "level": "warning", "detail": str(e)[:200]})
        return {"status": getattr(e, "status", ST_FAILED), "note": str(e)[:200]}
    finally:
        await release_named_lock("singcup_discover", token)


# ── ③ 지표 갱신 (목록을 훑지 않는다) ────────────────────────────────────────
_DUE_SQL = """SELECT c.clip_uid, c.video_id, c.rec_id, c.last_attempt_at,
                     (s.representative_clip_uid IS NOT NULL) AS is_rep
              FROM singcup_clips c
              LEFT JOIN singcup_streamers s ON s.representative_clip_uid = c.clip_uid
              WHERE c.event_id=? AND c.active=1
                -- 성공이 아니라 **시도** 기준(실패 클립 무한 재호출 방지)
                AND c.last_attempt_at < (CASE WHEN s.representative_clip_uid IS NOT NULL
                                              THEN ? ELSE ? END)
                {extra}
              -- 한 번도 갱신 못 받은 행(NULL/0)을 무조건 맨 앞으로 명시한다.
              -- 컬럼이 NOT NULL DEFAULT 0이라 지금은 NULL이 안 나오지만, 정렬에서
              -- NULL의 위치는 방언마다 달라 의도를 SQL에 박아 둔다.
              -- 그다음은 가장 오래 방치된 것부터. clip_uid는 동률일 때 순서를
              -- 고정해 재시작 후에도 같은 앞머리만 반복해 집지 않게 한다.
              ORDER BY CASE WHEN c.last_attempt_at IS NULL THEN 0
                            WHEN c.last_attempt_at = 0    THEN 0
                            ELSE 1 END ASC,
                       c.last_attempt_at ASC, c.clip_uid ASC
              LIMIT ?"""


async def _metrics_due(now: int, limit: int) -> list[dict]:
    """갱신 대상 — **가장 오래 갱신되지 않은 순**. 대표 클립에 일부 몫을 예약한다.

    예전에는 `ORDER BY is_rep DESC, heart_count DESC` 였다. 이게 이번 사고의
    직접 원인이다: 정렬 키(heart_count)가 곧 갱신 대상 값이라, 하트가 0인
    클립은 큐 맨 뒤로 밀리고 → 갱신을 못 받고 → 계속 0으로 남아 → 영원히 맨
    뒤인 자기강화 굶주림(starvation)이 생긴다. 대상이 사이클 상한보다 많으면
    하위 클립은 확률적으로 늦는 게 아니라 **결코** 선택되지 않았다.

    이제 두 레인으로 나눈다.
      · 대표 레인(REP_SHARE 비율) — 순위를 직접 좌우하므로 짧은 TTL로 우선 확보
      · 공정 레인(나머지)         — 대표든 아니든 가장 오래된 것부터

    두 레인 모두 last_metrics_at ASC이므로, 어떤 클립도 자기보다 최근에 갱신된
    클립에게 계속 추월당하지 않는다(= 굶주림 없음). 갱신되면 last_metrics_at이
    now가 되어 큐 맨 뒤로 가므로 전체 순회가 보장된다.
    """
    db = await get_db()
    limit = max(0, limit)
    if limit == 0:
        return []
    rep_ttl = now - int(REP_METRICS_TTL_MINUTES * 60)
    ttl = now - int(METRICS_TTL_MINUTES * 60)

    rep_budget = min(limit, max(1, int(limit * REP_SHARE)))
    rows = await (await db.execute(
        _DUE_SQL.format(extra="AND s.representative_clip_uid IS NOT NULL"),
        (EVENT_ID, rep_ttl, ttl, rep_budget))).fetchall()
    picked = [dict(r) for r in rows]

    rest = limit - len(picked)
    if rest > 0:
        seen = {r["clip_uid"] for r in picked}
        # 대표 레인에서 이미 집은 만큼 넉넉히 받아 와 중복만 걸러낸다
        rows = await (await db.execute(
            _DUE_SQL.format(extra=""),
            (EVENT_ID, rep_ttl, ttl, rest + len(picked)))).fetchall()
        picked += [dict(r) for r in rows if r["clip_uid"] not in seen][:rest]
    return picked


async def _record_refresh_run(now: int, dur_ms: int, due: int,
                              tally: dict, api: dict):
    db = await get_db()
    await db.execute(
        "INSERT INTO singcup_refresh_runs (event_id, collected_at, duration_ms, due,"
        " ok, partial, failed, api_calls, http_429) VALUES (?,?,?,?,?,?,?,?,?)",
        (EVENT_ID, now, dur_ms, due, tally["ok"], tally["partial"], tally["failed"],
         api["calls"], api["http_429"]))
    # 계측은 최근 구간만 있으면 된다 — 무한 증가를 막는다
    await db.execute(
        "DELETE FROM singcup_refresh_runs WHERE event_id=? AND collected_at < ?",
        (EVENT_ID, now - 7 * 86400))
    await db.commit()


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * 0.95))]


async def metrics_sweep_stats() -> dict:
    """전체 순회가 실제로 도는지 판정하는 운영 지표.

    '한 바퀴 도는 데 몇 시간 걸리는가'와 '가장 오래 방치된 클립은 몇 시간째인가'가
    핵심이다. 후자가 전자보다 훨씬 크면 어딘가에서 굶고 있다는 뜻이다.
    """
    db = await get_db()
    now = int(time.time())
    row = await (await db.execute(
        """SELECT COUNT(*) AS clips,
                  SUM(CASE WHEN last_metrics_at=0 THEN 1 ELSE 0 END) AS never,
                  MIN(last_metrics_at) AS oldest,
                  SUM(CASE WHEN last_metrics_at >= ? THEN 1 ELSE 0 END) AS h1,
                  SUM(CASE WHEN last_metrics_at >= ? THEN 1 ELSE 0 END) AS h6,
                  SUM(CASE WHEN last_metrics_at >= ? THEN 1 ELSE 0 END) AS h24,
                  SUM(CASE WHEN metrics_ok=0 THEN 1 ELSE 0 END) AS not_ok
           FROM singcup_clips WHERE event_id=? AND active=1""",
        (now - 3600, now - 6 * 3600, now - 86400, EVENT_ID))).fetchone()
    reps = await (await db.execute(
        "SELECT COUNT(*) c FROM singcup_streamers WHERE event_id=? "
        "AND representative_clip_uid IS NOT NULL", (EVENT_ID,))).fetchone()
    due = len(await _metrics_due(now, 10 ** 6))
    clips = int(row["clips"] or 0)
    per_hour = REFRESH_PER_CYCLE * (60.0 / max(1e-9, CLIP_INTERVAL_MINUTES))
    oldest = int(row["oldest"] or 0)
    return {
        "clips": clips, "representatives": int(reps["c"] or 0), "due_now": due,
        "never_refreshed": int(row["never"] or 0),
        "metrics_not_ok": int(row["not_ok"] or 0),
        "refreshed_last_1h": int(row["h1"] or 0),
        "refreshed_last_6h": int(row["h6"] or 0),
        "refreshed_last_24h": int(row["h24"] or 0),
        "per_cycle": REFRESH_PER_CYCLE,
        "cycle_minutes": CLIP_INTERVAL_MINUTES,
        "throughput_per_hour": round(per_hour, 1),
        # 이론상 한 바퀴(SLA). 실측 지연(oldest_age_hours)이 이 값보다 크게 길면
        # 정렬/예산 문제로 뒤쪽이 굶고 있다는 신호다.
        "full_sweep_hours": round(clips / per_hour, 2) if per_hour else None,
        "oldest_age_hours": round((now - oldest) / 3600, 2) if oldest else None,
        "starving": bool(oldest and clips
                         and (now - oldest) / 3600 > 2 * (clips / per_hour)),
        # 예산을 올려도 되는지의 근거 — 추측 대신 최근 24시간 실측치를 본다
        "recent_runs": await _refresh_run_stats(now),
    }


async def _refresh_run_stats(now: int) -> dict:
    """최근 24시간 갱신 사이클의 p95 소요시간·429·실패율·API 호출 수."""
    db = await get_db()
    rows = await (await db.execute(
        "SELECT duration_ms, due, ok, partial, failed, api_calls, http_429 "
        "FROM singcup_refresh_runs WHERE event_id=? AND collected_at >= ?",
        (EVENT_ID, now - 86400))).fetchall()
    if not rows:
        return {"runs": 0}
    due = sum(int(r["due"]) for r in rows)
    failed = sum(int(r["failed"]) for r in rows)
    calls = sum(int(r["api_calls"]) for r in rows)
    return {
        "runs": len(rows),
        "p95_duration_ms": _p95([int(r["duration_ms"]) for r in rows]),
        "max_duration_ms": max(int(r["duration_ms"]) for r in rows),
        "http_429_total": sum(int(r["http_429"]) for r in rows),
        "api_calls_total": calls,
        "api_calls_per_run": round(calls / len(rows), 1),
        "processed": due,
        "failure_rate": round(failed / due, 4) if due else 0.0,
        "partial_rate": round(sum(int(r["partial"]) for r in rows) / due, 4)
                        if due else 0.0,
        # 사이클 간격(분)보다 p95가 길면 사이클이 겹쳐 락 경합이 생긴다는 신호
        "cycle_budget_ms": int(CLIP_INTERVAL_MINUTES * 60 * 1000),
    }


async def clip_diagnosis(clip_uid: str) -> dict:
    """문제 클립 1건의 DB 상태 · 갱신 대기열 위치 · 제외 사유를 한 번에 본다."""
    db = await get_db()
    now = int(time.time())
    row = await (await db.execute(
        "SELECT * FROM singcup_clips WHERE clip_uid=?", (clip_uid,))).fetchone()
    if row is None:
        return {"clip_uid": clip_uid, "found": False,
                "excluded_reason": "singcup_clips에 행이 없음(수집 자체가 안 됨)"}
    r = dict(row)
    rep = await (await db.execute(
        "SELECT channel_id FROM singcup_streamers WHERE representative_clip_uid=?",
        (clip_uid,))).fetchone()
    is_rep = rep is not None
    ttl_min = REP_METRICS_TTL_MINUTES if is_rep else METRICS_TTL_MINUTES
    due = r["last_metrics_at"] < now - int(ttl_min * 60)

    reason = None
    if r["deletion_state"] == DEL_CONFIRMED:
        reason = "deletion_state=confirmed_deleted (상세 API가 삭제를 반복 확인)"
    elif not r["active"]:
        reason = "active=0"
    elif r["event_id"] != EVENT_ID:
        reason = f"event_id 불일치({r['event_id']} != {EVENT_ID})"
    elif not due:
        reason = f"TTL({ttl_min}분) 이내라 아직 대상 아님"
    pos = None
    if due and reason is None:
        # 공정 레인 기준 대기열 위치 — 몇 사이클 뒤에 처리되는지 바로 환산된다
        q = await (await db.execute(
            """SELECT COUNT(*) c FROM singcup_clips c2
               LEFT JOIN singcup_streamers s ON s.representative_clip_uid = c2.clip_uid
               WHERE c2.event_id=? AND c2.active=1
                 AND c2.last_metrics_at < (CASE WHEN s.representative_clip_uid
                                                IS NOT NULL THEN ? ELSE ? END)
                 AND (c2.last_metrics_at, c2.clip_uid) < (?, ?)""",
            (EVENT_ID, now - int(REP_METRICS_TTL_MINUTES * 60),
             now - int(METRICS_TTL_MINUTES * 60),
             r["last_metrics_at"], clip_uid))).fetchone()
        pos = int(q["c"] or 0)

    snap = await _metrics_snapshot(clip_uid)
    return {
        "clip_uid": clip_uid, "found": True,
        "owner_channel_id": r["owner_channel_id"],
        "db_before": {
            "heart_count": r["heart_count"], "view_count": r["view_count"],
            "metrics_ok": r["metrics_ok"], "active": r["active"],
            "last_metrics_at": _iso(r["last_metrics_at"]),
            "last_collected_at": _iso(r["last_collected_at"]),
            "first_collected_at": _iso(r["first_collected_at"]),
            "created_at": _iso(r["created_at"]),
            "age_hours": round((now - (r["last_metrics_at"] or 0)) / 3600, 2)
                         if r["last_metrics_at"] else None,
        },
        "deletion": {
            "state": r["deletion_state"],
            # 확인 횟수는 missing_scan_count 컬럼을 재사용한다(옛 이름 유지)
            "checks": r["missing_scan_count"],
            "first_at": _iso(r["deletion_first_at"]) if r["deletion_first_at"] else None,
            "last_at": _iso(r["deletion_last_at"]) if r["deletion_last_at"] else None,
            "reason": r["deletion_reason"] or None,
            "confirm_checks_required": DELETION_CONFIRM_CHECKS,
            "min_interval_seconds": DELETION_MIN_INTERVAL_SECONDS,
        },
        "baseline_24h": snap.get("baseline_24h") if snap else None,
        "heart_delta_24h": snap.get("heart_delta_24h") if snap else None,
        "is_representative": is_rep,
        "refresh_due": due,
        # 대기열 위치를 사이클 수로 환산 — '몇 시간 뒤에나 차례가 오는지'가 보인다
        "refresh_queue_position": pos,
        "eta_hours": (round(pos / REFRESH_PER_CYCLE * CLIP_INTERVAL_MINUTES / 60, 2)
                      if pos is not None else None),
        "excluded_reason": reason,
        "retry": dict(await (await db.execute(
            "SELECT attempts, next_try_at, last_error FROM singcup_clip_retry "
            "WHERE clip_uid=?", (clip_uid,))).fetchone() or {}) or None,
    }


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(int(ts), _KST).isoformat() if ts else None


async def refresh_metrics(limit: int | None = None) -> dict:
    """저장해 둔 videoId/recId로 카드 API만 불러 하트·조회수를 갱신한다."""
    token = await acquire_named_lock("singcup_metrics", 300)
    if token is None:
        return {"status": ST_SKIPPED, "note": "다른 갱신 작업이 실행 중입니다."}

    now = int(time.time())
    client = _get_client()
    try:
        due = await _metrics_due(now, limit or REFRESH_PER_CYCLE)
        if not due:
            return {"status": ST_OK, "refreshed": 0, "failed": 0}

        sem = asyncio.Semaphore(max(1, CARD_CONCURRENCY))
        # 부분 성공을 성공으로 세지 않는다 — failed=0인데 화면 값이 안 도는
        # 상황을 로그만 보고 알아채려면 이 구분이 있어야 한다.
        tally = {"ok": 0, "partial": 0, "failed": 0, "fetch_failed": 0}
        no_heart = no_view = 0

        # **조회와 저장을 분리한다.** 예전에는 `one()`이 카드 조회와 `_apply_metrics`
        # (DML, 커밋은 상위가)를 함께 했고, gather가 끝난 뒤에야 한 번 커밋했다.
        # 즉 **먼저 끝난 클립의 DML이 트랜잭션을 열어 둔 채, 나머지 클립의 HTTP를
        # 전부 기다렸다.** 공유 연결은 첫 DML부터 쓰기 잠금을 붙들므로 그 시간 동안
        # 프로세스 전체와 봇 프로세스의 쓰기가 멈춘다.
        fetched: list[tuple[str, dict | None, dict | None]] = []

        async def one(r):
            uid = r["clip_uid"]
            item = {"clipUID": uid, "videoId": r["video_id"],
                    "recId": r["rec_id"] or "{}"}
            before = await _metrics_snapshot(uid) if uid == DEBUG_CLIP_UID else None
            async with sem:
                card = await fetch_card(client, item)
            fetched.append((uid, card, before))

        started = time.monotonic()
        _take_api_counters()                       # 이 사이클분만 세도록 초기화
        # 1) 외부 조회만
        await asyncio.gather(*[one(r) for r in due])
        # 2) 클립 락은 트랜잭션 밖에서 받는다
        tokens: dict[str, str | None] = {}
        for uid, _card, _before in fetched:
            tokens[uid] = await acquire_clip_lock(uid)
        states: dict[str, str] = {}

        async def apply_metrics_batch(_db):
            for k in tally:
                tally[k] = 0
            states.clear()
            for uid, card, _before in fetched:
                if card is None:
                    tally["fetch_failed"] += 1
                    tally["failed"] += 1
                    await _apply_metrics(uid, 0, 0, False, False, now)
                    continue
                states[uid] = await _apply_metrics(
                    uid, card["heart_count"], card["view_count"],
                    card["heart_ok"], card["view_ok"], now)

        try:
            wrote = await db_write(get_db, apply_metrics_batch,
                                   what="singcup.refresh_metrics", log=_log)
        finally:
            for uid, tok in tokens.items():
                await release_clip_lock(uid, tok)
        if not wrote:
            return {"status": ST_SKIPPED, "note": "DB 잠금으로 저장하지 못했습니다.",
                    "due": len(due)}
        # 3) 집계·로그는 트랜잭션 밖에서
        for uid, card, before in fetched:
            if card is None:
                continue
            state = states.get(uid, "failed")
            tally[state] += 1
            if not card["heart_ok"]:
                no_heart += 1
            if not card["view_ok"]:
                no_view += 1
            if state != "ok":
                _log({"event": "metrics_partial", "level": "warning", "clip_uid": uid,
                      "state": state, "heart_ok": card["heart_ok"],
                      "view_ok": card["view_ok"],
                      "kept_heart": not card["heart_ok"], "kept_view": not card["view_ok"]})
            if before is not None:
                # 지정한 클립 1건만 DB(전)·카드(신규)·DB(후)를 한 줄에 붙여 남긴다
                _log({"event": "metrics_debug", "clip_uid": uid, "state": state,
                      "db_before": before, "fetched": {
                          "heart_count": card["heart_count"], "view_count": card["view_count"],
                          "heart_ok": card["heart_ok"], "view_ok": card["view_ok"]},
                      "db_after": await _metrics_snapshot(uid)})
        await recompute_ranking(now, client=client)
        api = _take_api_counters()
        dur = int((time.monotonic() - started) * 1000)
        await _record_refresh_run(now, dur, len(due), tally, api)
        _log({"event": "refresh_metrics", "due": len(due), "ok": tally["ok"],
              "partial": tally["partial"], "failed": tally["failed"],
              "fetch_failed": tally["fetch_failed"],
              "no_heart": no_heart, "no_view": no_view,
              "duration_ms": dur, "api_calls": api["calls"],
              "http_429": api["http_429"]})
        return {"status": ST_OK, "refreshed": tally["ok"], "partial": tally["partial"],
                "failed": tally["failed"], "fetch_failed": tally["fetch_failed"],
                "due": len(due), "duration_ms": dur, "api_calls": api["calls"],
                "http_429": api["http_429"]}
    except (FetchError, SchemaError) as e:
        _log({"event": "refresh_failed", "level": "warning", "detail": str(e)[:200]})
        return {"status": ST_FAILED, "note": str(e)[:200]}
    finally:
        await release_named_lock("singcup_metrics", token)


_CLIP_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# 단건 갱신은 관리자가 손으로 부르는 경로다. 카드 1건 + 순위 재계산이라 짧지만,
# 외부 API가 늘어질 때 요청을 무한정 붙잡지 않도록 상한을 둔다.
SINGLE_REFRESH_TIMEOUT = float(os.getenv("SINGCUP_SINGLE_REFRESH_TIMEOUT", "30"))


async def refresh_one_clip(clip_uid: str, *, actor: str = "admin") -> dict:
    """클립 1건만 정상 수집 경로로 갱신한다(DB 직접 수정 아님).

    fetch_card → _apply_metrics → recompute_ranking(대표·점수·순위·스냅샷)까지
    정기 사이클과 **완전히 같은 함수**를 탄다. 여기서만 쓰는 우회 경로를 만들면
    "손으로는 되는데 자동으로는 안 되는" 상태를 검증할 수 없게 된다.
    """
    if not _CLIP_UID_RE.match(clip_uid or ""):
        return {"status": ST_FAILED, "note": "clip_uid 형식이 올바르지 않습니다."}

    db = await get_db()
    row = await (await db.execute(
        "SELECT clip_uid, video_id, rec_id, active, event_id FROM singcup_clips "
        "WHERE clip_uid=?", (clip_uid,))).fetchone()
    if row is None:
        return {"status": ST_FAILED, "note": "해당 클립이 DB에 없습니다."}

    # 정기 갱신과 같은 락을 쓴다 — 같은 행을 동시에 UPDATE하고 순위를 두 번
    # 겹쳐 계산하는 것을 막는다(둘 다 recompute_ranking을 부른다).
    token = await acquire_named_lock("singcup_metrics", 120)
    if token is None:
        return {"status": ST_SKIPPED, "note": "다른 갱신 작업이 실행 중입니다."}

    now = int(time.time())
    # 정기 스윕이 같은 클립을 처리 중이면 기다렸다 진행한다(같은 DB named lock).
    clip_token = await acquire_clip_lock(clip_uid, wait=5.0)
    if clip_token is None:
        await release_named_lock("singcup_metrics", token)
        return {"status": ST_SKIPPED,
                "note": "이 클립을 다른 갱신 작업이 처리 중입니다."}
    before = await _metrics_snapshot(clip_uid)
    _log({"event": "single_refresh_start", "clip_uid": clip_uid, "actor": actor,
          "db_before": before})
    try:
        async def work():
            client = _get_client()
            item = {"clipUID": clip_uid, "videoId": row["video_id"],
                    "recId": row["rec_id"] or "{}"}
            card = await fetch_card(client, item)
            if card is None:
                return card, "fetch_failed"
            state = await _apply_metrics(clip_uid, card["heart_count"],
                                         card["view_count"], card["heart_ok"],
                                         card["view_ok"], now)
            await db.commit()
            # 대표 클립·예상 인기점수·순위·스냅샷을 정기 경로 그대로 다시 계산
            await recompute_ranking(now, client=client)
            return card, state

        card, state = await asyncio.wait_for(work(), timeout=SINGLE_REFRESH_TIMEOUT)
    except asyncio.TimeoutError:
        _log({"event": "single_refresh_timeout", "level": "warning",
              "clip_uid": clip_uid, "actor": actor})
        return {"status": ST_FAILED, "note": f"{SINGLE_REFRESH_TIMEOUT}초 안에 끝나지 않았습니다."}
    except (FetchError, SchemaError) as e:
        _log({"event": "single_refresh_failed", "level": "warning",
              "clip_uid": clip_uid, "actor": actor, "detail": str(e)[:200]})
        return {"status": ST_FAILED, "note": str(e)[:200]}
    finally:
        await release_clip_lock(clip_uid, clip_token)
        await release_named_lock("singcup_metrics", token)

    after = await _metrics_snapshot(clip_uid)
    fetched = None if card is None else {
        "heart": card["heart_count"], "heart_ok": card["heart_ok"],
        "view": card["view_count"] if card["view_ok"] else None,
        "view_ok": card["view_ok"],
        "missing_reason": None if card["metrics_ok"] else "카드 응답에 필드 없음",
    }
    # 감사 로그 — 누가, 어떤 클립을, 어떤 값으로 바꿨는지 한 줄에 남긴다
    _log({"event": "single_refresh", "clip_uid": clip_uid, "actor": actor,
          "apply_result": state, "db_before": before, "fetched": fetched,
          "db_after": after})
    return {"status": ST_OK if state != "fetch_failed" else ST_FAILED,
            "clip_uid": clip_uid, "apply_result": state,
            "db_before": before, "fetched": fetched, "db_after": after}


# ── 스캔 상태 모델 ─────────────────────────────────────────────────────────
# 예전에는 tagged=0 하나로 '태그 없음 / 조회 실패 / 파싱 실패 / 소유자 미상'을 전부
# 표현했고, 탐색은 `uid in state`만 보고 영구히 건너뛰었다. 그래서 **일시적 실패가
# 최종 판정으로 굳었다**. 상태를 나누고 각각 다른 재확인 주기를 준다.
SCAN_REGISTERED = "registered"        # 참가작으로 등록됨 — 지표 갱신이 관리
SCAN_UNTAGGED = "untagged"            # 확인했고 태그 없음 — 느린 주기로 재확인
SCAN_FETCH_FAILED = "fetch_failed"    # HTTP/네트워크 실패 — 짧은 백오프
SCAN_PARSE_FAILED = "parse_failed"    # 응답은 왔는데 구조가 깨짐 — 짧은 백오프
SCAN_MISSING_OWNER = "missing_owner"  # 태그는 있는데 소유 채널을 못 정함
SCAN_OUTSIDE_EVENT = "outside_event"  # 기간 밖 — 최종
SCAN_INVALID = "blind_or_invalid"     # 삭제/블라인드 — 최종
_TERMINAL = {SCAN_REGISTERED, SCAN_OUTSIDE_EVENT, SCAN_INVALID}

# 태그가 없던 클립의 재확인 간격(회차별). 뒤로 갈수록 뜸하게 본다.
RETAG_HOURS = float(os.getenv("SINGCUP_RETAG_HOURS", "6"))
RETAG_HOURS_2 = float(os.getenv("SINGCUP_RETAG_HOURS_2", "12"))
RETAG_HOURS_N = float(os.getenv("SINGCUP_RETAG_HOURS_N", "24"))
# 실패는 '아직 모른다'는 뜻이라 훨씬 빨리 다시 본다.
_FAIL_BACKOFF = [300, 900, 1800, 3600]
RETAG_PER_CYCLE = int(os.getenv("SINGCUP_RETAG_PER_CYCLE", "40"))
RETAG_CONCURRENCY = int(os.getenv("SINGCUP_RETAG_CONCURRENCY", "2"))
RETAG_RATE = float(os.getenv("SINGCUP_RETAG_RATE", "1.0"))   # 초당 요청
# 이벤트가 끝난 뒤에도 이 시간까지는 늦게 붙은 태그를 받아 준다.
# `SINGCUP_RETAG_GRACE_HOURS`는 SINGCUP-1에서 쓰이지 않게 됐다. 재확인은 이제 '등록'
# 게이트에 묶여 END_AT에서 정확히 닫힌다 — 유예를 두면 종료 뒤에 태그를 붙인 클립이
# 참가로 편입돼 순위가 소급 변경된다. 환경변수를 지우지는 않는다(설정돼 있어도 무해).


def _next_check_at(status: str, recheck_count: int, now: int) -> int | None:
    """상태별 다음 확인 시각. 최종 상태는 None(다시 보지 않음)."""
    if status in _TERMINAL:
        return None
    if status in (SCAN_FETCH_FAILED, SCAN_PARSE_FAILED, SCAN_MISSING_OWNER):
        i = min(max(0, recheck_count - 1), len(_FAIL_BACKOFF) - 1)
        return now + _FAIL_BACKOFF[i]
    hours = (RETAG_HOURS if recheck_count <= 1
             else RETAG_HOURS_2 if recheck_count == 2 else RETAG_HOURS_N)
    return now + int(hours * 3600)


async def _scan_upsert(clip_uid: str, status: str, now: int, *, item: dict | None = None,
                       video_id: str = "", rec_id: str = "", created_at: int | None = None,
                       owner: str = "", http_status: int | None = None,
                       error: str = "", advance: bool = True):
    """스캔 상태를 한 행에 UPDATE한다(이력 행을 무한히 쌓지 않는다).

    `advance=False`면 recheck_count를 올리지 않는다 — 최초 스캔처럼 '재확인이
    아닌' 경우에 쓴다. 빈 값이 기존 값을 덮지 않게 해 재확인 재료를 잃지 않는다.
    """
    db = await get_db()
    d = parse_clip_date((item or {}).get("createdDate")) if item else None
    vid = video_id or str((item or {}).get("videoId") or "")
    rid = rec_id or str((item or {}).get("recId") or "")
    cat = created_at if created_at is not None else (int(d.timestamp()) if d else None)
    row = await (await db.execute(
        "SELECT recheck_count FROM singcup_clip_scan WHERE clip_uid=?",
        (clip_uid,))).fetchone()
    cnt = (int(row["recheck_count"]) if row else 0) + (1 if advance else 0)
    nxt = _next_check_at(status, cnt, now)
    await db.execute(
        """INSERT INTO singcup_clip_scan
               (clip_uid, tagged, checked_at, first_checked_at, video_id, rec_id,
                created_at, scan_status, next_check_at, recheck_count,
                last_http_status, last_error, owner_channel_id, registered_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(clip_uid) DO UPDATE SET
               tagged        = excluded.tagged,
               checked_at    = excluded.checked_at,
               scan_status   = excluded.scan_status,
               next_check_at = excluded.next_check_at,
               recheck_count = excluded.recheck_count,
               last_http_status = excluded.last_http_status,
               last_error    = excluded.last_error,
               video_id   = CASE WHEN excluded.video_id != ''
                                 THEN excluded.video_id ELSE video_id END,
               rec_id     = CASE WHEN excluded.rec_id != ''
                                 THEN excluded.rec_id ELSE rec_id END,
               created_at = COALESCE(excluded.created_at, created_at),
               owner_channel_id = CASE WHEN excluded.owner_channel_id != ''
                                       THEN excluded.owner_channel_id
                                       ELSE owner_channel_id END,
               registered_at = COALESCE(excluded.registered_at, registered_at)""",
        (clip_uid, 1 if status == SCAN_REGISTERED else 0, now, now, vid, rid, cat,
         status, nxt, cnt, http_status, error[:200] or None, owner,
         now if status == SCAN_REGISTERED else None))


async def _record_scan(clip_uid: str, tagged: bool, now: int, item: dict | None = None,
                       owner: str = ""):
    """최초 스캔 결과. 재확인 횟수는 올리지 않는다."""
    await _scan_upsert(clip_uid, SCAN_REGISTERED if tagged else SCAN_UNTAGGED, now,
                       item=item, owner=owner, advance=False)


# ── 재확인 ─────────────────────────────────────────────────────────────────
def _retag_window() -> tuple[int, int]:
    """재확인을 인정하는 클립 생성 구간(이벤트 기간 + 종료 후 유예)."""
    return int(START_AT.timestamp()), int(END_AT.timestamp())


def retag_enabled() -> bool:
    """무태그 재확인을 계속하는가.

    재확인이 하는 일은 **아직 등록되지 않은 클립을 등록하는 것**이므로 '등록' 축이다.
    그래서 판정은 `registration_open()` 하나로 끝난다 — 종료와 동시에 닫힌다.

    예전에는 `END_AT + RETAG_GRACE_HOURS`까지 열어 뒀다. 그 유예는 **종료 뒤에 태그를
    붙인 클립까지 참가로 편입**시켜 순위를 소급 변경할 수 있어, "종료 후 신규 등록
    중단"이라는 확정 요구와 맞지 않는다. 그래서 유예를 걷어내고 경계를 END_AT에
    정확히 맞췄다(더 엄격해진 방향이다).
    """
    return registration_open()


_DUE_SCAN_SQL = """
SELECT clip_uid, video_id, rec_id, created_at, scan_status, recheck_count
FROM singcup_clip_scan
WHERE scan_status NOT IN (?, ?, ?)
  AND (next_check_at IS NULL OR next_check_at <= ?)
  AND (created_at IS NULL OR (created_at >= ? AND created_at <= ?))
  AND clip_uid NOT IN (SELECT clip_uid FROM singcup_clips WHERE event_id = ?)
ORDER BY CASE WHEN checked_at IS NULL THEN 0 ELSE 1 END,
         checked_at ASC, clip_uid ASC
LIMIT ?
"""


async def _due_scans(now: int, limit: int) -> list[dict]:
    db = await get_db()
    start_ts, end_ts = _retag_window()
    rows = await (await db.execute(
        _DUE_SCAN_SQL, (SCAN_REGISTERED, SCAN_OUTSIDE_EVENT, SCAN_INVALID,
                        now, start_ts, end_ts, EVENT_ID, max(0, limit)))).fetchall()
    return [dict(r) for r in rows]


async def _due_count(now: int) -> int:
    db = await get_db()
    start_ts, end_ts = _retag_window()
    row = await (await db.execute(
        "SELECT COUNT(*) c FROM singcup_clip_scan WHERE scan_status NOT IN (?,?,?) "
        "AND (next_check_at IS NULL OR next_check_at <= ?) "
        "AND (created_at IS NULL OR (created_at >= ? AND created_at <= ?)) "
        "AND clip_uid NOT IN (SELECT clip_uid FROM singcup_clips WHERE event_id=?)",
        (SCAN_REGISTERED, SCAN_OUTSIDE_EVENT, SCAN_INVALID, now,
         start_ts, end_ts, EVENT_ID))).fetchone()
    return int(row["c"] or 0)


async def _register_from_card(uid: str, card: dict, meta: dict | None,
                              now: int) -> str:
    """태그가 확인된 클립을 정상 경로로 등록한다. 반환은 최종 스캔 상태."""
    # 소유 채널 우선순위: 상세의 공식 필드 → 카드의 검증된 채널 필드.
    # 어느 쪽도 못 얻으면 **아무 채널에도 귀속시키지 않는다**(잘못된 스트리머에
    # 클립이 붙으면 순위가 통째로 틀어진다).
    owner = (meta or {}).get("owner_channel_id") or card.get("owner_channel_id") or ""
    if not owner:
        await _scan_upsert(uid, SCAN_MISSING_OWNER, now,
                           video_id=(meta or {}).get("video_id", ""),
                           error="소유 채널을 확정하지 못함")
        return SCAN_MISSING_OWNER
    if meta is None:
        await _scan_upsert(uid, SCAN_FETCH_FAILED, now, owner=owner,
                           error="상세 조회 실패")
        return SCAN_FETCH_FAILED
    if str(meta.get("blind_type") or "").upper() in _BAD_BLIND:
        await _scan_upsert(uid, SCAN_INVALID, now, owner=owner)
        return SCAN_INVALID

    item = {"clipUID": uid, "videoId": meta["video_id"],
            "recId": meta.get("rec_id") or "{}", "ownerChannelId": owner,
            "clipTitle": meta.get("clip_title") or card.get("title") or "",
            "thumbnailImageUrl": meta.get("thumbnail_image_url", ""),
            "duration": meta.get("duration", 0), "adult": meta.get("adult", False),
            "blindType": meta.get("blind_type") or None,
            "createdDate": meta.get("created_date")}
    tok = await acquire_clip_lock(uid)
    try:
        await _upsert_clip(_to_clip_row(item, card), now)
        await _apply_metrics(uid, card["heart_count"], card["view_count"],
                             card["heart_ok"], card["view_ok"], now)
    finally:
        await release_clip_lock(uid, tok)
    await _scan_upsert(uid, SCAN_REGISTERED, now, item=item, owner=owner)
    _log({"event": "retag_found", "clip_uid": uid, "owner": owner,
          "hearts": card["heart_count"], "views": card["view_count"]})
    return SCAN_REGISTERED


async def _recheck_one(client, r: dict, now: int, tally: dict, sem, bucket):
    """스캔 행 1건을 다시 확인한다. 실패는 실패로 기록하고 짧게 재시도한다."""
    uid = r["clip_uid"]
    start_ts, end_ts = _retag_window()
    video_id, rec_id = r["video_id"], r["rec_id"] or "{}"
    created = r["created_at"]

    async with sem:
        await bucket.acquire()
        # videoId나 생성일을 모르면 상세로 먼저 채운다(예전 스캔 행)
        meta = None
        if not video_id or created is None:
            meta = await fetch_clip_meta(client, uid, full=True)
            if meta is None:
                tally["fetch_failed"] += 1
                await _scan_upsert(uid, SCAN_FETCH_FAILED, now, error="상세 조회 실패")
                return
            video_id = video_id or meta["video_id"]
            created = meta["created_at"] if created is None else created
            if created is not None and not (start_ts <= created <= end_ts):
                tally["outside_event"] += 1
                await _scan_upsert(uid, SCAN_OUTSIDE_EVENT, now,
                                   video_id=video_id, created_at=created)
                return
        if not video_id:
            tally["parse_failed"] += 1
            await _scan_upsert(uid, SCAN_PARSE_FAILED, now, error="videoId 없음")
            return
        await bucket.acquire()
        card = await fetch_card(client, {"clipUID": uid, "videoId": video_id,
                                         "recId": rec_id})

    tally["fetched"] += 1
    if card is None:
        tally["fetch_failed"] += 1
        await _scan_upsert(uid, SCAN_FETCH_FAILED, now, video_id=video_id,
                           created_at=created, error="카드 조회 실패")
        return
    if not has_singcup_tag(card["description"]):
        tally["still_untagged"] += 1
        await _scan_upsert(uid, SCAN_UNTAGGED, now, video_id=video_id,
                           created_at=created,
                           owner=card.get("owner_channel_id", ""))
        return

    tally["newly_tagged"] += 1
    if meta is None:
        async with sem:
            await bucket.acquire()
            meta = await fetch_clip_meta(client, uid, full=True)
    if meta is not None:
        meta.setdefault("rec_id", rec_id)
        meta["video_id"] = meta.get("video_id") or video_id
    status = await _register_from_card(uid, card, meta, now)
    if status == SCAN_REGISTERED:
        tally["registered"] += 1
    elif status == SCAN_MISSING_OWNER:
        tally["missing_owner"] += 1
    else:
        tally["fetch_failed"] += 1


async def recheck_untagged_clips(limit: int | None = None) -> dict:
    """태그 판정을 다시 한다 — 목록을 재순회하지 않고 스캔 기록에서만 고른다.

    확정된 결함은 "스캔 기록이 있다는 이유만으로 영구 제외"였다. 처음부터 태그가
    없었는지, 나중에 붙였는지, 그때 API가 실패했는지는 **과거 응답이 없어 알 수
    없다** — 그래서 상태를 나눠 실패는 빨리, 무태그는 느리게 다시 본다.
    """
    limit = limit or RETAG_PER_CYCLE
    if not retag_enabled():
        return {"status": ST_SKIPPED, "note": "이벤트 종료 후 유예기간이 지났습니다."}
    token = await acquire_named_lock("singcup_retag", 300)
    if token is None:
        return {"status": ST_SKIPPED, "note": "다른 재확인 작업이 실행 중입니다."}

    now = int(time.time())
    tally = {"fetched": 0, "newly_tagged": 0, "still_untagged": 0, "registered": 0,
             "missing_owner": 0, "fetch_failed": 0, "parse_failed": 0,
             "outside_event": 0}
    try:
        rows = await _due_scans(now, limit)
        if not rows:
            return {"status": ST_OK, "examined": 0, **tally,
                    "remaining_due": await _due_count(now), "next_cursor": None}

        client = _get_client()
        sem = asyncio.Semaphore(max(1, RETAG_CONCURRENCY))
        # 정각 전체 갱신과 같은 방식으로 요청을 고르게 흘린다(예산을 나눠 쓴다)
        from singcup_sweep import TokenBucket
        bucket = TokenBucket(RETAG_RATE, RETAG_RATE)
        await asyncio.gather(*[_recheck_one(client, r, now, tally, sem, bucket)
                               for r in rows])
        await (await get_db()).commit()
        if tally["registered"]:
            # 새 참가자가 생겼으니 대표·점수·순위·KPI를 다시 계산한다
            await recompute_ranking(now, client=client)
        remaining = await _due_count(now)
        _log({"event": "retag", "examined": len(rows), **tally,
              "remaining_due": remaining})
        return {"status": ST_OK, "examined": len(rows), **tally,
                "remaining_due": remaining,
                # 커서가 필요 없다 — 처리한 행은 next_check_at이 미래로 가서
                # 대상에서 빠지므로, 같은 요청을 반복하면 자연히 다음 묶음이 온다.
                "next_cursor": None}
    except (FetchError, SchemaError) as e:
        _log({"event": "retag_failed", "level": "warning", "detail": str(e)[:200]})
        return {"status": ST_FAILED, "note": str(e)[:200], **tally}
    finally:
        await release_named_lock("singcup_retag", token)


async def rediscover_clip(clip_uid: str) -> dict:
    """클립 1건을 즉시 재탐색·등록한다(관리자용). 전체 백필 차례를 기다리지 않는다."""
    if not _CLIP_UID_RE.match(clip_uid or ""):
        return {"status": ST_FAILED, "note": "clip_uid 형식이 올바르지 않습니다."}
    token = await acquire_named_lock("singcup_retag", 120)
    if token is None:
        return {"status": ST_SKIPPED, "note": "다른 재확인 작업이 실행 중입니다."}
    now = int(time.time())
    start_ts, end_ts = _retag_window()
    try:
        client = _get_client()
        meta = await fetch_clip_meta(client, clip_uid, full=True)
        if meta is None:
            await _scan_upsert(clip_uid, SCAN_FETCH_FAILED, now, error="상세 조회 실패")
            return {"status": ST_FAILED, "note": "클립 상세를 조회하지 못했습니다."}
        created = meta["created_at"]
        if created is None or not (start_ts <= created <= end_ts):
            await _scan_upsert(clip_uid, SCAN_OUTSIDE_EVENT, now,
                               video_id=meta["video_id"], created_at=created)
            return {"status": ST_OK, "scan_status": SCAN_OUTSIDE_EVENT,
                    "note": "이벤트 기간 밖 클립입니다.", "created_at": _iso(created)}
        card = await fetch_card(client, {"clipUID": clip_uid,
                                         "videoId": meta["video_id"], "recId": "{}"})
        if card is None:
            await _scan_upsert(clip_uid, SCAN_FETCH_FAILED, now,
                               video_id=meta["video_id"], created_at=created,
                               error="카드 조회 실패")
            return {"status": ST_FAILED, "note": "카드를 조회하지 못했습니다."}
        tagged = has_singcup_tag(card["description"])
        if not tagged:
            await _scan_upsert(clip_uid, SCAN_UNTAGGED, now,
                               video_id=meta["video_id"], created_at=created)
            return {"status": ST_OK, "scan_status": SCAN_UNTAGGED, "tagged": False,
                    "description": card["description"], "title": card.get("title"),
                    "note": "#싱드컵 태그가 없습니다."}
        status = await _register_from_card(clip_uid, card, meta, now)
        await (await get_db()).commit()
        if status == SCAN_REGISTERED:
            await recompute_ranking(now, client=client)
        return {"status": ST_OK if status == SCAN_REGISTERED else ST_FAILED,
                "scan_status": status, "tagged": True,
                "owner_channel_id": meta.get("owner_channel_id")
                                    or card.get("owner_channel_id"),
                "heart_count": card["heart_count"], "view_count": card["view_count"],
                "db_after": await _metrics_snapshot(clip_uid)}
    except (FetchError, SchemaError) as e:
        return {"status": ST_FAILED, "note": str(e)[:200]}
    finally:
        await release_named_lock("singcup_retag", token)


async def retag_stats() -> dict:
    """재확인 큐 건전성 — 남은 대상과 소진 예상 시간."""
    db = await get_db()
    now = int(time.time())
    by = {r["scan_status"]: int(r["c"]) for r in await (await db.execute(
        "SELECT scan_status, COUNT(*) c FROM singcup_clip_scan GROUP BY scan_status"
    )).fetchall()}
    oldest = await (await db.execute(
        "SELECT MIN(checked_at) m FROM singcup_clip_scan "
        "WHERE scan_status NOT IN (?,?,?)",
        (SCAN_REGISTERED, SCAN_OUTSIDE_EVENT, SCAN_INVALID))).fetchone()
    recent = await (await db.execute(
        "SELECT COUNT(*) c, SUM(CASE WHEN scan_status=? THEN 1 ELSE 0 END) t "
        "FROM singcup_clip_scan WHERE checked_at >= ?",
        (SCAN_REGISTERED, now - 3600))).fetchone()
    due = await _due_count(now)
    per_hour = RETAG_PER_CYCLE * (60.0 / max(1e-9, CLIP_INTERVAL_MINUTES))
    return {
        "enabled": retag_enabled(),
        "untagged_total": by.get(SCAN_UNTAGGED, 0),
        "due_now": due,
        "checked_1h": int(recent["c"] or 0),
        "newly_tagged_1h": int(recent["t"] or 0),
        "missing_owner": by.get(SCAN_MISSING_OWNER, 0),
        "fetch_failed": by.get(SCAN_FETCH_FAILED, 0),
        "parse_failed": by.get(SCAN_PARSE_FAILED, 0),
        "outside_event": by.get(SCAN_OUTSIDE_EVENT, 0),
        "registered": by.get(SCAN_REGISTERED, 0),
        "oldest_checked_at": _iso(oldest["m"] if oldest else None),
        "per_hour": round(per_hour, 1),
        "estimated_backfill_hours": round(due / per_hour, 2) if per_hour else None,
    }



# ── 목록 대조 (안전망) ─────────────────────────────────────────────────────
# 신규 탐색은 '아는 클립만 있는 페이지'를 만나면 즉시 멈춘다 — 평소에는 옳지만,
# 어떤 이유로든 한 번 놓친 클립은 목록에서 1페이지를 벗어나는 순간 영영 못 만난다.
# 실제로 그런 클립이 나왔다(15페이지에 멀쩡히 있는데 우리 DB엔 없음).
#
# 그래서 주기적으로 **끝까지** 훑어 'singcup_clips에도 singcup_clip_scan에도 없는'
# 클립만 찾아 온다. 원인이 재시도 소진이든, 페이지 밀림이든, 일시적 장애든
# 상관없이 되찾는다는 점이 핵심이다 — 원인별 대응은 놓치는 경우가 생긴다.
RECONCILE_INTERVAL_MINUTES = float(
    os.getenv("SINGCUP_RECONCILE_INTERVAL_MINUTES", "60"))
RECONCILE_MAX_PAGES = int(os.getenv("SINGCUP_RECONCILE_MAX_PAGES", "150"))
RECONCILE_MAX_NEW = int(os.getenv("SINGCUP_RECONCILE_MAX_NEW", "300"))


async def _unknown_uids(uids: list[str]) -> list[str]:
    """등록도 스캔도 안 된 uid만 남긴다."""
    if not uids:
        return []
    db = await get_db()
    qs = ",".join("?" for _ in uids)
    known = set()
    for sql in (f"SELECT clip_uid FROM singcup_clips WHERE clip_uid IN ({qs})",
                f"SELECT clip_uid FROM singcup_clip_scan WHERE clip_uid IN ({qs})"):
        for r in await (await db.execute(sql, tuple(uids))).fetchall():
            known.add(r["clip_uid"])
    return [u for u in uids if u not in known]


async def _flag_absent_from_list(seen: set[str], now: int) -> int:
    """완주한 목록 스캔에서 끝내 안 보인 활성 클립을 **의심**으로만 표시한다.

    확정하지 않는 이유: 목록은 정렬·페이지 밀림·지연 반영이 있어 '한 번 안 보였다'가
    곧 삭제는 아니다. 여기서는 "상세 API로 한 번 물어보라"는 표시만 남기고, 실제
    판정은 probe_clip_alive의 명시적 404 2회가 한다.
    """
    if not seen:
        return 0
    db = await get_db()
    rows = await (await db.execute(
        "SELECT clip_uid FROM singcup_clips "
        "WHERE event_id=? AND active=1 AND deletion_state=?",
        (EVENT_ID, DEL_ACTIVE))).fetchall()
    n = 0
    for r in rows:
        if r["clip_uid"] in seen:
            continue
        if await _flag_deletion_suspect(r["clip_uid"], "list_absent", now):
            n += 1
    return n


async def reconcile_from_list(max_pages: int | None = None) -> dict:
    """목록을 끝까지 훑어 우리가 모르는 클립을 찾아 등록한다.

    조기 종료가 없다 — 그게 이 함수의 존재 이유다. 대신 자주 돌리지 않는다
    (기본 60분). 목록은 페이지당 1회 요청이라 150페이지도 부담이 작다.
    """
    token = await acquire_named_lock("singcup_reconcile", 900)
    if token is None:
        return {"status": ST_SKIPPED, "note": "다른 대조 작업이 실행 중입니다."}

    client = _get_client()
    pages = scanned = 0
    missing: list[dict] = []
    cursor = None
    status, note = ST_OK, ""
    # 이번 스캔이 **끝까지 갔는가**. 페이지 상한이나 신규 상한에 걸려 중간에 멈췄다면
    # '목록에 없다'는 사실을 근거로 쓸 수 없다 — 아직 안 본 페이지에 있을 수 있다.
    complete = False
    seen: set[str] = set()
    try:
        for _ in range(max_pages or RECONCILE_MAX_PAGES):
            items, nxt = await fetch_clip_page(client, cursor)
            pages += 1
            if not items:
                complete = True
                break
            scanned += len(items)
            cands = [it for it in items
                     if is_candidate_clip(it, start=START_AT, end=END_AT)]
            uids = [str(it.get("clipUID")) for it in cands]
            seen.update(uids)
            unknown = set(await _unknown_uids(uids))
            missing += [it for it in cands if str(it.get("clipUID")) in unknown]

            oldest = parse_clip_date(items[-1].get("createdDate"))
            if oldest and oldest < START_AT:
                complete = True             # 이벤트 시작 이전 구간에 닿았다
                break
            if nxt is None:
                complete = True             # 목록 끝
                break
            if len(missing) >= RECONCILE_MAX_NEW:
                break                       # 상한에 걸려 중간에 멈춤 — 완주 아님
            cursor = nxt
            await asyncio.sleep(PAGE_DELAY)

        absent = 0
        if complete:
            # 완주했을 때만 '목록에 없음'을 쓴다. 그것도 **의심 표시까지만** —
            # 확정은 언제나 상세 API의 명시적 404 2회가 있어야 한다.
            absent = await _flag_absent_from_list(seen, int(time.time()))

        tagged = inserted = failed = 0
        if missing:
            now = int(time.time())
            tagged, inserted, failed = await _scan_batch(client, missing, now)
            if tagged:
                await recompute_ranking(now, client=client)
        _log({"event": "reconcile", "pages": pages, "scanned": scanned,
              "missing": len(missing), "tagged": tagged, "inserted": inserted,
              "failed": failed, "complete": complete, "absent_flagged": absent})
        return {"status": status, "pages": pages, "scanned": scanned,
                "missing": len(missing), "tagged": tagged, "inserted": inserted,
                "failed": failed, "complete": complete, "absentFlagged": absent,
                "note": note}
    except (FetchError, SchemaError) as e:
        _log({"event": "reconcile_failed", "level": "warning",
              "pages": pages, "detail": str(e)[:200]})
        return {"status": getattr(e, "status", ST_FAILED), "pages": pages,
                "note": str(e)[:200]}
    finally:
        await release_named_lock("singcup_reconcile", token)


_last_reconcile = 0.0


async def maybe_reconcile() -> dict | None:
    """정기 루프에서 호출 — 주기가 됐을 때만 전체 대조를 돌린다."""
    global _last_reconcile
    now = time.monotonic()
    if _last_reconcile and now - _last_reconcile < RECONCILE_INTERVAL_MINUTES * 60:
        return None
    _last_reconcile = now
    return await reconcile_from_list()


async def retry_failed_clips(limit: int = 50) -> dict:
    """카드 조회에 실패해 큐에 남은 클립만 다시 시도한다."""
    now = int(time.time())
    db = await get_db()
    rows = await (await db.execute(
        "SELECT clip_uid, item_json FROM singcup_clip_retry "
        "WHERE next_try_at <= ? AND attempts < ? ORDER BY next_try_at LIMIT ?",
        (now, RETRY_MAX_ATTEMPTS, max(1, limit)))).fetchall()
    if not rows:
        return {"retried": 0}
    items = []
    for r in rows:
        try:
            items.append(json.loads(r["item_json"]))
        except (TypeError, ValueError):
            # 원본이 없으면 재구성이 불가능하다 — 큐에서 빼고 다음 탐색에 맡긴다
            await _clear_retry(r["clip_uid"])
    if not items:
        return {"retried": 0}
    tagged, _ins, failed = await _scan_batch(_get_client(), items, now)
    return {"retried": len(items), "tagged": tagged, "failed": failed}


# ── 조회 (API용) ────────────────────────────────────────────────────────────
# 기준 버킷이 '기대 인원'의 이 비율에 못 미치면 불완전으로 본다.
#
# 기대 인원을 현재 참가자 수로 잡으면 이벤트 초기의 정상적인 증가를 불완전으로
# 오인한다. 대신 **참가자 수가 단조 증가한다**는 성질을 쓴다 — singcup_streamers는
# 행을 지우지 않으므로, 어떤 시각의 완전한 세트는 그보다 앞선 완전한 세트보다 작을
# 수 없다. 그래서 기대 인원 = '후보 구간에서 이 버킷보다 오래된 버킷들의 최대 인원'
# 이다. 이러면 두 상황이 분명히 갈린다.
#   A 정상 증가   90 → 110 → 120   (각 단계가 직전 이상)
#   B 부분 세트   1051 → 7 → 1057  (7이 직전의 0.7%)
# 단조성 덕분에 임계값을 느슨하게 잡을 이유가 없어 기본값을 0.9로 둔다.
# (탈락·비활성으로 소폭 감소하는 경우가 있어 1.0으로는 잡지 않는다.)
_COVERAGE_RAW = os.getenv("SINGCUP_BASELINE_MIN_COVERAGE", "0.9")
try:
    BASELINE_MIN_COVERAGE = float(_COVERAGE_RAW)
    if not 0.0 < BASELINE_MIN_COVERAGE <= 1.0:
        raise ValueError(_COVERAGE_RAW)
except ValueError:
    # 잘못된 값으로 판정이 조용히 무력화되면 이번 사고가 그대로 재발한다.
    print(f"[singcup_clips] SINGCUP_BASELINE_MIN_COVERAGE={_COVERAGE_RAW!r} 는 "
          f"(0,1] 범위의 수가 아니라 기본값 0.9를 씁니다", flush=True)
    BASELINE_MIN_COVERAGE = 0.9

# 후보 버킷을 모을 때 앞뒤로 함께 읽을 여유(판정 비교군). 기본 2시간.
_BASELINE_NEIGHBOR_SECONDS = 2 * 3600


async def find_reference_baseline(now: int, window: int,
                                  tolerance: int = DELTA_TOLERANCE_SECONDS) -> dict | None:
    """기준 시각(now-window)에 가장 가까운 **시간 버킷** 하나.

    예전에는 collected_at 한 점을 회차 ID로 썼다. 그런데 `_save_snapshots`는
    UNIQUE(event_id, owner, snapshot_bucket) + INSERT OR IGNORE라, 같은 시간대의
    두 번째 저장에서는 기존 참가자가 전부 무시되고 **그 사이 새로 들어온 참가자만**
    새 collected_at으로 들어간다. 그 '부분 세트'가 기준으로 뽑히면 나머지 전원이
    기준값 없음(=NEW)이 되고 1시간 증감이 통째로 죽는다
    (실측 2026-07-30: 1,060명 중 1,057명 NEW, 기준선 7명).

    그래서 회차의 단위를 **시간 버킷**으로 바꾼다. 한 버킷 안에 collected_at이 여러
    개 섞여 있는 것을 정상으로 보고, 그 구간 전체를 하나의 기준선으로 읽는다.

    버킷은 `snapshot_bucket` 컬럼을 신뢰하되, 컬럼 도입 전 행은 NULL이라
    collected_at으로 되계산해 채운다(COALESCE). 후보를 collected_at 범위로 먼저
    좁히므로 기존 인덱스(event_id, collected_at)를 그대로 타고, COALESCE는 이미
    좁혀진 소수 행에만 적용된다. 마이그레이션도 데이터 변경도 필요 없다.

    선택 순서가 중요하다. **가까운 것을 먼저 고른 뒤 불완전인지 보는** 방식이면,
    바로 옆에 멀쩡한 버킷이 있는데도 대량 null이 난다. 그래서 **정상 후보를 먼저
    가려내고 그중 가장 가까운 것**을 고른다.
    """
    ref = now - window
    cands = await _bucket_candidates(ref, tolerance)
    if not cands:
        return None
    # 허용 오차는 버킷 시작이 아니라 '그 버킷이 실제로 처음 기록된 시각'으로 잰다.
    # 버킷 시작(정시)으로 재면 판정이 최대 한 시간 앞으로 밀린다.
    for c in cands:
        c["distance"] = abs(c["lo"] - ref)
        c["withinTolerance"] = c["distance"] <= tolerance
    cands.sort(key=lambda c: c["distance"])

    # **허용 오차는 커버리지보다 먼저다.** coverage가 정상이어도 target에서 멀면
    # 그 값을 '1시간 전'이라고 부를 수 없다. 오래된 정상 버킷을 무제한 fallback으로
    # 끌어오면 '30분 증감'이나 '3시간 증감'을 1시간이라고 표시하게 된다.
    near = [c for c in cands if c["withinTolerance"]]
    healthy = [c for c in near if not c["partial"]]
    if healthy:
        chosen, fallback = dict(healthy[0]), False
    elif near:
        # 허용 범위 안이 전부 불완전 — 그중 가장 가까운 것을 partial로 표시해 쓴다.
        # 소비자는 이걸 보고 전원 NEW 대신 baseline_incomplete로 처리한다.
        chosen, fallback = dict(near[0]), True
    else:
        return None          # 허용 범위 안에 후보 자체가 없다 → insufficient_history

    def _why(c: dict) -> str:
        if not c["withinTolerance"]:
            return "outside_tolerance"
        if c["partial"]:
            return "partial_set"
        return "farther_from_target"

    chosen["fallbackUsed"] = fallback
    chosen["toleranceSeconds"] = tolerance
    chosen["rejected"] = [
        {"bucket": c["bucket"], "rows": c["rows"], "expected": c["expected"],
         "coverage": c["coverage"], "distance": c["distance"],
         "withinTolerance": c["withinTolerance"], "reason": _why(c)}
        for c in cands if c["bucket"] != chosen["bucket"]]
    return chosen


async def _bucket_candidates(ref: int, tolerance: int, *,
                             detail: bool = False) -> list[dict]:
    """기준 시각 주변 버킷들 + 각 버킷의 기대 인원·커버리지·불완전 여부.

    기대 인원은 '자기보다 오래된 후보 버킷들의 최대 인원'이다(참가자 수 단조 증가).
    가장 오래된 후보는 비교 대상이 없으므로 자기 자신을 기대치로 둔다(판정 보류).

    `detail=False`(요청 경로)에서는 COUNT(DISTINCT collected_at) 같은 진단 전용
    집계를 빼서 임시 B-tree를 하나 줄인다 — 5,000명 규모에서 실측으로 유의미했다.
    """
    lo = ref - tolerance - _BASELINE_NEIGHBOR_SECONDS
    hi = ref + tolerance + _BASELINE_NEIGHBOR_SECONDS
    extra = (", COUNT(DISTINCT collected_at) AS times,"
             " SUM(CASE WHEN snapshot_bucket IS NULL THEN 1 ELSE 0 END) AS legacy"
             if detail else "")
    rows = await (await (await get_db()).execute(
        "SELECT COALESCE(snapshot_bucket, (collected_at/3600)*3600) AS bucket,"
        " MIN(collected_at) AS lo, MAX(collected_at) AS hi,"
        " COUNT(DISTINCT owner_channel_id) AS owners, COUNT(*) AS raw_rows"
        + extra +
        " FROM singcup_snapshots WHERE event_id=? AND collected_at>=? AND collected_at<?"
        " GROUP BY bucket ORDER BY bucket ASC", (EVENT_ID, lo, hi))).fetchall()
    out: list[dict] = []
    running_max = 0
    for r in rows:
        owners = int(r["owners"])
        expected = running_max or owners
        item = {
            "bucket": int(r["bucket"]), "lo": int(r["lo"]), "hi": int(r["hi"]),
            "rows": owners, "rawRows": int(r["raw_rows"]),
            "expected": expected,
            "coverage": round(owners / expected, 4) if expected else 0.0,
            "partial": owners < expected * BASELINE_MIN_COVERAGE,
        }
        if detail:
            item["distinctCollectedAt"] = int(r["times"])
            item["legacyRows"] = int(r["legacy"])
        out.append(item)
        running_max = max(running_max, owners)
    return out


# 진단은 secret이 있어도 분당 150회까지 열려 있다. 매번 GROUP BY 집계를 돌리면
# 진단이 그 자체로 부하가 된다 — 짧은 TTL 캐시 + single-flight로 실제 SQL은
# 창당 한 번만 돈다. 실패는 캐시하지 않아 다음 호출이 그대로 재시도된다.
BASELINE_REPORT_TTL = float(os.getenv("SINGCUP_BASELINE_REPORT_TTL", "45"))
BASELINE_REPORT_TIMEOUT = float(os.getenv("SINGCUP_BASELINE_REPORT_TIMEOUT", "10"))
_baseline_cache: dict[int, tuple[float, dict]] = {}
_baseline_lock = asyncio.Lock()
_baseline_stats = {"hit": 0, "miss": 0, "coalesced": 0, "timeout": 0}


def baseline_report_stats() -> dict:
    return dict(_baseline_stats)


# ── owner별 비교 간격 분포 (관리자 진단 전용) ──────────────────────────────
# 화면은 기준 시각과 비교 간격을 **하나씩만** 보여준다. 그런데 `deltaBaseAt`은 버킷의
# `MIN(collected_at)`이고 화면의 '비교 N분'은 `now - lo`, 즉 owner 간 **최댓값**이다.
# 한 버킷 안에 collected_at이 흩어져 있으면(실측 2026-08-02: 14:02:13~14:41:04,
# 38.9분 폭) owner마다 실제 비교 창이 다르고, 창이 긴 쪽이 급상승 순위에서 유리하다.
# 그 편차가 실제로 얼마인지 볼 수단이 없어서 여기에 집계값만 더한다.
#
# owner id·clip uid·닉네임은 넣지 않는다. 진단에 필요한 것은 분포이지 명단이 아니다.

# 히스토그램 경계(초). 반개구간 [lo, hi)로 나누고 **마지막 구간만** x >= 5400 이다.
# 경계를 닫힌 구간으로 두면 정확히 600초인 표본이 두 칸에 잡히거나 어디에도 안 잡힌다.
_INTERVAL_BUCKETS = (
    (0, 600), (600, 1200), (1200, 1800), (1800, 2400), (2400, 3000),
    (3000, 3600), (3600, 4500), (4500, 5400), (5400, None),
)


def _percentile(sorted_vals: list[int], q: float) -> int | None:
    """**nearest-rank** 백분위. `statistics`의 암묵적 보간에 기대지 않는다.

    표본 수가 짝수든 홀수든 1개든, 반환값은 **항상 실제 표본 중 하나**이고 같은
    입력이면 같은 값이 나온다. 보간을 쓰면 존재하지 않는 시각이 p50으로 나와
    "이 값이 어느 owner 것이냐"는 질문에 답할 수 없게 된다.

    정의: rank = ceil(q * n), 1-indexed. q=0.5, n=4 → rank 2 → 두 번째로 작은 값.
    """
    n = len(sorted_vals)
    if n == 0:
        return None
    rank = math.ceil(q * n)
    return sorted_vals[max(1, min(n, rank)) - 1]


def _interval_summary(vals: list[int]) -> dict:
    """min/p50/p90/p95/max/average(초). 표본이 없으면 전부 null."""
    if not vals:
        return {"owners": 0, "minSeconds": None, "p50Seconds": None,
                "p90Seconds": None, "p95Seconds": None, "maxSeconds": None,
                "averageSeconds": None}
    s = sorted(vals)
    return {
        "owners": len(s),
        "minSeconds": s[0],
        "p50Seconds": _percentile(s, 0.50),
        "p90Seconds": _percentile(s, 0.90),
        "p95Seconds": _percentile(s, 0.95),
        "maxSeconds": s[-1],
        "averageSeconds": round(sum(s) / len(s), 1),
    }


def _interval_histogram(vals: list[int]) -> list[dict]:
    """반개구간 히스토그램. 모든 표본이 정확히 한 칸에만 들어간다."""
    out = []
    for lo, hi in _INTERVAL_BUCKETS:
        n = sum(1 for v in vals if (v >= lo and (hi is None or v < hi)))
        out.append({"fromSeconds": lo, "toSeconds": hi, "owners": n})
    return out


def _m(sec: int | None) -> float | None:
    """초 → 분(소수 1자리). 값이 없으면 null을 그대로 넘긴다 — 0으로 만들지 않는다."""
    return None if sec is None else round(sec / 60.0, 1)


def _interval_stats(vals: list[int], window: int) -> dict:
    """분포 하나의 전체 계약. 표본이 0이면 백분위는 전부 null이고 카운터만 0이다.

    '계산할 수 없음'과 '0분'은 다른 뜻이다. 표본이 없을 때 0을 넣으면 "모두 즉시
    비교됐다"로 읽히므로 반드시 null을 유지한다.
    """
    s = sorted(vals)
    base = _interval_summary(s)
    return {
        "owners": base["owners"],
        "minMinutes": _m(base["minSeconds"]),
        "p25Minutes": _m(_percentile(s, 0.25) if s else None),
        "p50Minutes": _m(base["p50Seconds"]),
        "p75Minutes": _m(_percentile(s, 0.75) if s else None),
        "p90Minutes": _m(base["p90Seconds"]),
        "p95Minutes": _m(base["p95Seconds"]),
        "maxMinutes": _m(base["maxSeconds"]),
        "averageMinutes": _m(base["averageSeconds"]),
        "histogram": _interval_histogram(s),
        # 목표 창(기본 3600초)과의 관계. 경계는 정확히 window와 같은 값만 exact다.
        "exact60m": sum(1 for v in s if v == window),
        "under60m": sum(1 for v in s if v < window),
        "over60m": sum(1 for v in s if v > window),
    }


# ── 현재 지표 신선도 (기준선 비교 간격과 **다른 지표**) ───────────────────
# 기준선 간격이 정상이어도 현재 하트 값이 오래됐을 수 있다. 실측 2026-08-02:
# 디온·노바 두 대표 클립이 원본보다 22·77 하트 뒤처져 있었는데, 기준선 커버리지는
# 정상이었고 원인은 회차 안에서 아직 처리되지 않은 것이었다. 그 상태를 볼 수단이
# 없어서 이 분포를 더한다. **두 지표를 한 값으로 합치지 않는다.**
#
# 기준 필드는 `last_heart_at`이다. database/db.py 주석이 정의하듯 "하트를 **정상으로
# 받은** 마지막 시각"이고, `_apply_metrics`는 `heart_ok`이면 값이 변하지 않아도
# 갱신한다. `last_attempt_at`은 **실패한 시도에도 갱신되므로** freshness로 쓰면 안 된다.
# `last_success_at` 컬럼은 존재하지 않으며 새로 만들지 않는다(스키마 변경 없음).
#
# 버킷 경계는 baseline 쪽(_INTERVAL_BUCKETS)과 다르다. 신선도는 훨씬 넓게 퍼지므로
# 뒤쪽을 성기게 잡는다. 형식(fromSeconds/toSeconds/owners)은 그대로 재사용한다.
_AGE_BUCKETS = (
    (0, 600), (600, 1200), (1200, 1800), (1800, 2700), (2700, 3600),
    (3600, 5400), (5400, 7200), (7200, None),
)


def _age_histogram(vals: list[int]) -> list[dict]:
    return [{"fromSeconds": lo, "toSeconds": hi,
             "owners": sum(1 for v in vals if v >= lo and (hi is None or v < hi))}
            for lo, hi in _AGE_BUCKETS]


def _metrics_age_stats(ages: list[int], *, count: int, missing: int, future: int,
                       now: int) -> dict:
    """현재 지표 신선도 분포.

    `count`는 모집단 전체(=currentEligible과 같은 수)이고, `observedCount`는 그중
    실제로 age를 계산할 수 있었던 수다. 둘의 차이가 missing + future다 —
    **관측 이력이 없는 대상을 0초로 섞으면 "방금 갱신됨"이 되어버린다.**
    """
    s = sorted(ages)
    def sec(q):
        return _percentile(s, q) if s else None
    out = {
        "unit": "seconds",
        "generatedAt": _iso(now),
        "field": "last_heart_at",
        "percentileRule": "nearest-rank (rank = ceil(q*n), 1-indexed)",
        "histogramRule": "half-open [fromSeconds, toSeconds); last bucket is open-ended",
        "count": count,
        "observedCount": len(s),
        "missingObservedAt": missing,
        "futureObservedAt": future,
        "minSeconds": s[0] if s else None,
        "p50Seconds": sec(0.50), "p90Seconds": sec(0.90),
        "p95Seconds": sec(0.95), "p99Seconds": sec(0.99),
        "maxSeconds": s[-1] if s else None,
        "averageSeconds": round(sum(s) / len(s), 1) if s else None,
        "histogram": _age_histogram(s),
    }
    # 읽는 사람이 초를 분으로 다시 나누지 않도록 같은 값을 분으로도 준다.
    for k in ("min", "p50", "p90", "p95", "p99", "max", "average"):
        out[f"{k}Minutes"] = _m(out[f"{k}Seconds"])
    return out


async def baseline_report(window: int | None = None) -> dict:
    """기준선 진단(read-only). 짧은 TTL 캐시 + single-flight."""
    key = int(DELTA_WINDOW_SECONDS if window is None else window)
    hit = _baseline_cache.get(key)
    mono = time.monotonic()
    if hit and mono - hit[0] < BASELINE_REPORT_TTL:
        _baseline_stats["hit"] += 1
        return {**hit[1], "cached": True}
    async with _baseline_lock:
        hit = _baseline_cache.get(key)
        mono = time.monotonic()
        if hit and mono - hit[0] < BASELINE_REPORT_TTL:
            _baseline_stats["coalesced"] += 1
            return {**hit[1], "cached": True}
        t0 = time.perf_counter()
        try:
            data = await asyncio.wait_for(_baseline_report_uncached(key),
                                          timeout=BASELINE_REPORT_TIMEOUT)
        except TimeoutError:
            _baseline_stats["timeout"] += 1
            raise
        data["computeMs"] = round((time.perf_counter() - t0) * 1000, 2)
        # 성공만 캐시한다(예외는 여기까지 오지 않으므로 실패는 남지 않는다)
        _baseline_cache[key] = (time.monotonic(), data)
        while len(_baseline_cache) > 8:
            _baseline_cache.pop(min(_baseline_cache, key=lambda k: _baseline_cache[k][0]), None)
        _baseline_stats["miss"] += 1
        return {**data, "cached": False}


async def _baseline_report_uncached(window: int | None = None) -> dict:
    """어떤 버킷이 왜 뽑혔는지, 커버리지가 얼마인지 — 실제 집계."""
    now = int(time.time())
    win = DELTA_WINDOW_SECONDS if window is None else window
    ref = now - win
    db = await get_db()
    cur = await (await db.execute(
        "SELECT COUNT(*) n FROM singcup_streamers WHERE event_id=?", (EVENT_ID,))).fetchone()
    current = int(cur["n"] or 0)
    cands = await _bucket_candidates(ref, DELTA_TOLERANCE_SECONDS, detail=True)
    chosen = await find_reference_baseline(now, win)

    # ── owner별 실제 비교 간격 ────────────────────────────────────────────
    # 선택 규칙은 `select_baseline_rows()` 하나로 공유한다. 여기서 비슷한 구현을
    # 다시 쓰면 진단이 화면과 다른 행을 고를 수 있고, 그러면 진단이 거짓말을 한다.
    #
    # 모집단을 셋으로 나눈다. 하나로 뭉쳐 놓으면 "과거 스냅샷에 남아 있을 뿐 지금은
    # 비활성인 owner"까지 현재 랭킹 품질 지표처럼 읽힌다.
    owner_intervals = {"available": False, "reason": "no_baseline_bucket"}
    if chosen is not None:
        b0 = int(chosen["bucket"])
        rows = await (await db.execute(
            "SELECT owner_channel_id, clip_uid, heart_count, collected_at, id "
            "FROM singcup_snapshots WHERE event_id=? AND collected_at>=? AND collected_at<?",
            (EVENT_ID, b0, b0 + 3600))).fetchall()
        picked = select_baseline_rows(rows, ref)

        # 현재 판정 대상 — load_main의 `ranked`와 **같은 조건**이어야 한다.
        # (singcup_streamers JOIN singcup_clips, active=1, 대표 클립 기준)
        cur_rows = await (await db.execute(
            "SELECT s.channel_id, s.representative_clip_uid AS uid, c.heart_count, "
            "       c.metrics_recovered_at, c.last_heart_at "
            "FROM singcup_streamers s "
            "JOIN singcup_clips c ON c.clip_uid = s.representative_clip_uid "
            "WHERE s.event_id=? AND c.active=1", (EVENT_ID,))).fetchall()

        base_gaps, eligible_gaps, positive_gaps = [], [], []
        # 신선도는 **eligible과 정확히 같은 대상**에서만 모은다. 다른 모집단을 쓰면
        # 두 분포의 대상 수가 달라지고 그 차이를 설명할 수 없게 된다.
        eligible_ages: list[int] = []
        age_missing = age_future = 0
        missing_baseline = rep_changed = recovering = 0
        for _k, r in picked.values():
            base_gaps.append(now - int(r["collected_at"]))
        for cr in cur_rows:
            hit = picked.get(cr["channel_id"])
            if hit is None:
                missing_baseline += 1                    # 기준선 없음
                continue
            b = hit[1]
            gap = now - int(b["collected_at"])
            if str(b["clip_uid"]) != str(cr["uid"]):
                rep_changed += 1                         # 대표 클립 교체
                continue
            # load_main과 **완전히 같은 복구 가드**. 이 줄이 빠지면 화면에서는
            # recovering으로 제외된 owner가 진단에서는 양수 급상승으로 잡힌다.
            if ref and int(cr["metrics_recovered_at"] or 0) >= ref:
                recovering += 1
                continue
            eligible_gaps.append(gap)
            lha = int(cr["last_heart_at"] or 0)
            if lha <= 0:
                age_missing += 1              # 하트를 한 번도 정상 수신한 적 없음
            elif lha > now:
                age_future += 1               # 시계 역전 — 0초로 숨기지 않는다
            else:
                eligible_ages.append(now - lha)
            if int(cr["heart_count"]) - int(b["heart_count"]) > 0:
                positive_gaps.append(gap)

        # 창 밖(선택된 버킷에 있으나 지금은 대상이 아닌) owner 수
        cur_ids = {cr["channel_id"] for cr in cur_rows}
        out_of_window = sum(1 for o in picked if o not in cur_ids)

        owner_intervals = {
            "available": True,
            "bucketAt": _iso(b0),
            "targetAt": _iso(ref),
            "targetWindowSeconds": win,
            "rowsScanned": len(rows),
            "percentileRule": "nearest-rank (rank = ceil(q*n), 1-indexed)",
            "histogramRule": "half-open [lo, hi); last bucket is x >= 5400",
            "counters": {
                "outOfWindowOwners": out_of_window,
                "missingBaselineOwners": missing_baseline,
                "representativeChangedOwners": rep_changed,
                "recoveringOwners": recovering,
            },
            # 선택된 스냅샷에 존재하는 **전체** owner (비활성·탈락 포함)
            "baselineOwnerIntervalDistribution": _interval_stats(base_gaps, win),
            # 현재 active 대표 클립이 있고 실제로 증감을 계산할 수 있는 owner
            "currentEligibleOwnerIntervalDistribution": _interval_stats(eligible_gaps, win),
            # 기준선 간격이 아니라 **현재 하트 값이 얼마나 오래됐는가**.
            # count는 currentEligible의 owners와 항상 같다(같은 루프에서 모은다).
            "currentMetricsAgeDistribution": _metrics_age_stats(
                eligible_ages, count=len(eligible_gaps),
                missing=age_missing, future=age_future, now=now),
            # production 판정에서 heartDelta > 0 인 owner
            "positiveMoverIntervalDistribution": {
                **_interval_stats(positive_gaps, win),
                "positiveOwners": len(positive_gaps),
            },
        }

    # 24시간 쪽은 owner별 MAX(collected_at)<=목표라 '얼마나 오래된 샘플을 썼는지'가
    # owner마다 다르다. 이번 부분 세트 버그와는 별개의 위험이라 수치로 드러낸다.
    tgt24 = now - 86400
    d24 = await (await db.execute(
        "SELECT COUNT(*) n, MIN(collected_at) lo, MAX(collected_at) hi,"
        " AVG(? - collected_at) avg_gap,"
        " SUM(CASE WHEN ? - collected_at > ? THEN 1 ELSE 0 END) far"
        " FROM (SELECT owner_channel_id, MAX(collected_at) AS collected_at"
        "       FROM singcup_snapshots WHERE event_id=? AND collected_at<=?"
        "       GROUP BY owner_channel_id)",
        (tgt24, tgt24, DELTA_TOLERANCE_SECONDS, EVENT_ID, tgt24))).fetchone()

    return {
        "now": _iso(now), "windowMinutes": win // 60,
        "toleranceMinutes": DELTA_TOLERANCE_SECONDS // 60,
        "targetAt": _iso(ref),
        "currentStreamers": current,
        "minCoverage": BASELINE_MIN_COVERAGE,
        "toleranceSeconds": DELTA_TOLERANCE_SECONDS,
        "selected": None if chosen is None else {
            "selectedBucket": _iso(chosen["bucket"]),
            "selectedMinCollectedAt": _iso(chosen["lo"]),
            "selectedMaxCollectedAt": _iso(chosen["hi"]),
            "selectedDistanceSeconds": chosen.get("distance"),
            "toleranceSeconds": chosen.get("toleranceSeconds", DELTA_TOLERANCE_SECONDS),
            "withinTolerance": bool(chosen.get("withinTolerance")),
            "selectedRows": chosen["rows"],
            "expectedRows": chosen.get("expected"),
            "coverage": chosen.get("coverage"),
            "partial": bool(chosen.get("partial")),
            "fallbackUsed": bool(chosen.get("fallbackUsed")),
            # 기준선이 현재 참가자를 얼마나 덮는가. 이벤트 초기에는 자연히 낮다.
            "coverageVsCurrent": round(chosen["rows"] / current, 4) if current else None,
            # 실제 비교 간격 — '정확히 1시간'이 아님을 수치로 밝힌다
            "intervalSecondsMin": now - chosen["hi"],
            "intervalSecondsMax": now - chosen["lo"],
            "rejectedBuckets": [{**b, "bucketAt": _iso(b["bucket"])}
                                for b in chosen.get("rejected", [])],
        },
        "candidates": [{
            "bucketAt": _iso(c["bucket"]),
            "minCollectedAt": _iso(c["lo"]), "maxCollectedAt": _iso(c["hi"]),
            "owners": c["rows"], "rows": c["rawRows"],
            "distinctCollectedAt": c.get("distinctCollectedAt"),
            "legacyBucketNullRows": c.get("legacyRows"),
            "expected": c["expected"], "coverage": c["coverage"],
            "partial": c["partial"],
        } for c in cands],
        # V2 Phase 5 개선 대상 — 24시간 기준 샘플이 목표에서 얼마나 떨어져 있는가
        "ownerIntervals": owner_intervals,
        "day24h": {
            "targetAt": _iso(tgt24),
            "owners": int(d24["n"] or 0),
            "oldestBaseAt": _iso(int(d24["lo"])) if d24["lo"] else None,
            "newestBaseAt": _iso(int(d24["hi"])) if d24["hi"] else None,
            "avgGapSeconds": round(float(d24["avg_gap"] or 0), 1),
            "beyondToleranceOwners": int(d24["far"] or 0),
            "note": "owner별 MAX(collected_at)<=목표 방식이라 샘플 시각이 제각각이다",
        },
    }


# 급상승 캐시를 저장할 때 쓰는 응답 크기. publish_snapshot과 같은 값을 써야 두
# 작업이 **같은 캐시 항목 하나**를 나눠 쓴다(계산이 두 번 돌지 않는다).
TOP_MOVERS_LIMIT = int(os.getenv("SINGCUP_SNAPSHOT_LIMIT", "3000"))

# ── best-effort 영속화의 시간 예산 ──────────────────────────────────────────
# 이 저장은 랭킹·응답·스냅샷보다 **덜 중요하다.** 그런데 공용 연결로 쓰면 잠겼을 때
# busy_timeout(10초) × 재시도 4회 = 40초를 기다린다. 그 40초 동안 랭킹 완료·캐시
# 갱신·스냅샷 게시가 전부 멈춘다(실측: 잠긴 UPSERT 한 번에 10.8초).
#
# 그래서 전용 연결로 **짧게** 시도하고 예산을 넘기면 포기한다. 공용 연결의 PRAGMA는
# 건드리지 않는다 — 그건 다른 모든 작업의 대기 시간을 함께 바꿔 버린다.
PERSIST_BUSY_TIMEOUT_MS = int(os.getenv("SINGCUP_PERSIST_BUSY_TIMEOUT_MS", "250"))
PERSIST_ATTEMPTS = int(os.getenv("SINGCUP_PERSIST_ATTEMPTS", "3"))
PERSIST_BUDGET_SECONDS = float(os.getenv("SINGCUP_PERSIST_BUDGET_SECONDS", "2.0"))
PERSIST_BACKOFF_BASE_SECONDS = float(
    os.getenv("SINGCUP_PERSIST_BACKOFF_BASE_SECONDS", "0.05"))
# 내용이 달라도 이 간격 안에는 다시 쓰지 않는 안전판. **기본은 꺼 둔다(0).**
# 중복 쓰기는 아래 read 비교가 이미 없앤다. 여기에 값을 넣으면 '진짜로 바뀐' 급상승이
# 다음 회차(최대 4분)까지 저장되지 않으므로, 관측으로 churn이 확인될 때만 켠다.
PERSIST_MIN_INTERVAL_SECONDS = int(
    os.getenv("SINGCUP_PERSIST_MIN_INTERVAL_SECONDS", "0"))


def persist_worst_case_seconds() -> float:
    """상수만으로 계산한 최악 소요시간. 예산 안인지 테스트가 이 값으로 확인한다.

    시도마다 busy_timeout을 꽉 채우고(잠금), 시도 사이에 backoff+최대 jitter를
    기다리는 경우다. 연결 open/close는 실측 1~2ms라 시도당 5ms로 넉넉히 잡는다.
    """
    attempts = max(1, PERSIST_ATTEMPTS)
    lock_wait = attempts * (PERSIST_BUSY_TIMEOUT_MS / 1000.0)
    backoff = sum(PERSIST_BACKOFF_BASE_SECONDS * (2 ** i) * 2   # ×2 = jitter 상한
                  for i in range(attempts - 1))
    return lock_wait + backoff + attempts * 0.005

_movers_persist_stats = {"written": 0, "unchanged": 0, "throttled": 0, "stale": 0,
                         "failed": 0, "lastAt": None, "lastMs": None}
# 같은 값을 여러 경로가 동시에 저장하려 하면 read 비교가 전부 '다르다'로 통과해
# 중복 write가 난다(실측: 동시 20회 → write 5회). 하나씩만 들어가게 한다.
# 큐를 만들지 않는다 — 대기자는 앞선 저장이 끝난 뒤 read 비교에서 unchanged가 된다.
_movers_persist_lock = asyncio.Lock()


def top_movers_persist_stats() -> dict:
    return dict(_movers_persist_stats)


async def _top_movers_is_current(payload: str, base_at: int | None) -> tuple[bool, int]:
    """저장된 값이 이미 같은가. **읽기만 한다.**

    조건부 UPSERT(`WHERE ... IS NOT excluded...`)로 충분할 줄 알았는데 아니었다.
    실측: 다른 연결이 쓰기 잠금을 쥔 상태에서 **값이 완전히 동일해도** 그 UPSERT는
    쓰기 트랜잭션을 시작하고 busy_timeout을 꽉 채운 뒤 `database is locked`로 실패한다
    (10,770ms). rowcount가 0이 되는 것은 잠금을 잡은 **뒤**의 일이다.

    반면 WAL에서 SELECT는 쓰기 잠금의 영향을 받지 않는다(같은 조건에서 0ms).
    그래서 비교를 먼저 하고, 다를 때만 쓰기를 시도한다.

    반환: (같은가, 마지막 저장 시각)
    """
    db = await get_db()
    row = await (await db.execute(
        "SELECT payload, base_at, computed_at FROM singcup_top_movers WHERE event_id=?",
        (EVENT_ID,))).fetchone()
    if row is None:
        return (False, 0)
    same = (row["payload"] == payload
            and (row["base_at"] if row["base_at"] is not None else None) == base_at)
    return (same, int(row["computed_at"] or 0))


async def _save_top_movers(movers: list[dict], base_at: int | None, now: int) -> str:
    """직전 정상 집계를 덮어쓴다(이력이 아니라 최신 캐시라 행은 이벤트당 하나).

    **요청 경로에서 부르지 않는다.** 랭킹 계산이 끝난 뒤에만 불린다. 예전에는 공개
    GET `/main`이 응답을 계산하면서 여기까지 왔고, 잠금에 걸리면 조회가 500이 됐다.

    쓰기는 **전용 연결**로 하고 시간 예산 안에서만 시도한다. 공용 연결을 쓰면 잠겼을 때
    다른 모든 작업이 함께 기다리게 된다.

    반환: "written" | "unchanged" | "throttled" | "failed"
    """
    payload = json.dumps(movers, ensure_ascii=False)

    # ① 읽기 비교 — 같으면 쓰기를 **시도조차 하지 않는다**(잠금 획득 0회)
    same, last_at = await _top_movers_is_current(payload, base_at)
    if same:
        return "unchanged"
    if (PERSIST_MIN_INTERVAL_SECONDS > 0 and last_at
            and now - last_at < PERSIST_MIN_INTERVAL_SECONDS):
        # 내용은 달라도 너무 잦다 — 다음 회차에 저장된다(값은 어차피 최신으로 다시 계산된다)
        return "throttled"

    # ② 전용 연결로 짧게. 취소(asyncio.wait_for)를 쓰지 않는다 — aiosqlite의 워커
    #    스레드는 취소되지 않아 뒤에서 계속 돌고 연결 상태가 어긋날 수 있다.
    #    대신 busy_timeout 자체를 짧게 잡아 각 시도가 스스로 빨리 끝나게 하고,
    #    예산 초과는 **시도 사이에서** 판단한다.
    deadline = time.monotonic() + PERSIST_BUDGET_SECONDS
    delay = PERSIST_BACKOFF_BASE_SECONDS
    last_err: Exception | None = None
    for attempt in range(max(1, PERSIST_ATTEMPTS)):
        conn = None
        committed = False
        try:
            conn = await aiosqlite.connect(DB_PATH)
            await conn.execute(f"PRAGMA busy_timeout={PERSIST_BUSY_TIMEOUT_MS}")
            await conn.execute(
                "INSERT INTO singcup_top_movers (event_id, payload, base_at, computed_at) "
                "VALUES (?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET "
                "payload=excluded.payload, base_at=excluded.base_at, "
                "computed_at=excluded.computed_at",
                (EVENT_ID, payload, base_at, now))
            await conn.commit()
            committed = True
            return "written"
        except Exception as e:                      # noqa: BLE001
            last_err = e
            if not _is_locked_error(e):
                _log({"event": "top_movers_write_error", "level": "warning",
                      "detail": str(e)[:160]})
                return "failed"
        finally:
            # 성공·잠금·예외·**취소(CancelledError)** 어느 경로로 나가도 여기를 지난다.
            # 커밋하지 않았으면 되돌리고, 무슨 일이 있어도 연결은 닫는다.
            if conn is not None:
                if not committed:
                    try:
                        await conn.rollback()
                    except Exception:               # noqa: BLE001
                        pass
                try:
                    await conn.close()
                except Exception:                   # noqa: BLE001
                    pass
        if attempt + 1 >= PERSIST_ATTEMPTS or time.monotonic() + delay >= deadline:
            break
        await asyncio.sleep(delay + random.uniform(0, delay))
        delay *= 2
    _log({"event": "top_movers_write_giveup", "level": "warning",
          "attempts": attempt + 1, "budgetSeconds": PERSIST_BUDGET_SECONDS,
          "detail": str(last_err)[:160]})
    return "failed"


def _is_locked_error(e: BaseException) -> bool:
    m = str(e).lower()
    return "database is locked" in m or "database is busy" in m


async def persist_top_movers_snapshot(*, source: str) -> str:
    """'직전 정상 급상승'을 갱신한다 — **내부 작업 전용, best-effort.**

    랭킹 계산이 끝난 지점에서만 불린다. 실패해도 예외를 올리지 않는다: 이 저장이
    수집·스윕·랭킹·스냅샷 게시를 취소하면 본말이 전도된다. 다음 주기에 다시 시도된다.

    값은 `load_main_entry`(읽기 전용)가 이미 계산해 둔 것을 그대로 쓴다. 여기서
    다시 계산하지 않으므로 산출물이 응답과 어긋날 수 없다.
    """
    t0 = time.perf_counter()
    try:
        async with _movers_persist_lock:
            return await _persist_top_movers_locked(source, t0)
    except Exception as e:                          # noqa: BLE001 — 여기서 끝낸다
        _movers_persist_stats["failed"] += 1
        _log({"event": "top_movers_persist_failed", "level": "warning",
              "source": source, "detail": str(e)[:200]})
        return "failed"


async def _persist_top_movers_locked(source: str, t0: float) -> str:
    try:
        entry, _src = await load_main_entry(TOP_MOVERS_LIMIT)
        data = entry["data"]
        movers = data.get("topHeartMovers1h") or []
        # stale이면 그건 이미 이 표에서 읽어 온 값이다 — 자기 자신을 다시 쓰지 않는다.
        if not movers or data.get("topHeartMovers1hStale"):
            _movers_persist_stats["stale"] += 1
            return "skipped_stale"
        base_iso = data.get("topHeartMovers1hBaseAt")
        base_at = (int(datetime.fromisoformat(base_iso).timestamp())
                   if base_iso else None)
        result = await _save_top_movers(movers, base_at, int(time.time()))
    except Exception as e:                          # noqa: BLE001 — 여기서 끝낸다
        _movers_persist_stats["failed"] += 1
        _log({"event": "top_movers_persist_failed", "level": "warning",
              "source": source, "detail": str(e)[:200]})
        return "failed"
    ms = round((time.perf_counter() - t0) * 1000, 1)
    _movers_persist_stats[result if result in _movers_persist_stats else "failed"] += 1
    _movers_persist_stats["lastAt"] = _iso(int(time.time()))
    _movers_persist_stats["lastMs"] = ms
    # 이 로그가 '재계산 작업이 살아 있는가'의 관측 근거다. computed_at은 값이 실제로
    # 바뀔 때만 갱신되므로(같은 값이면 쓰기 0회) 생존 판정에 쓰면 안 된다.
    _log({"event": "top_movers_persisted", "source": source, "result": result,
          "movers": len(movers), "ms": ms,
          "persistenceAttemptAt": _iso(int(time.time()))})
    return result


async def _last_top_movers() -> tuple[list[dict], int | None, int | None]:
    """마지막으로 비어 있지 않았던 급상승 집계. 없으면 빈 목록."""
    db = await get_db()
    row = await (await db.execute(
        "SELECT payload, base_at, computed_at FROM singcup_top_movers WHERE event_id=?",
        (EVENT_ID,))).fetchone()
    if row is None:
        return [], None, None
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError):
        return [], None, None
    return (data if isinstance(data, list) else []), row["base_at"], row["computed_at"]


def select_baseline_rows(rows, ref: int) -> dict:
    """owner별 기준 스냅샷 행을 고른다 — `{owner: ((distance, -at, -id), row)}`.

    **`_delta_maps()`와 관리자 진단이 반드시 같은 규칙을 쓰게 하려고 떼어 놓았다.**
    진단 쪽에 비슷한 구현을 하나 더 두면 "화면에 보이는 값"과 "진단이 말하는 값"이
    조용히 갈라진다 — 그러면 진단의 존재 이유가 없어진다.

    동점 기준을 끝까지 고정해 같은 입력이면 항상 같은 행이 뽑히게 한다.
      ① 기준 시각과의 거리   ② collected_at DESC(같은 거리면 더 최근 값)
      ③ id DESC             (같은 시각의 중복 행 — 나중에 쓰인 행)

    이 순서를 SQL의 `ORDER BY ABS(...)`로 만들면 정렬 가능한 인덱스가 없어 임시
    B-tree가 생긴다(실측 5,000명에서 유의미). 대신 인덱스 순서(collected_at ASC)로
    그냥 읽고 승자만 파이썬에서 고른다 — 비교 키가 명시적이라 결정성은 같다.
    """
    best: dict[str, tuple] = {}
    for r in rows:
        owner = r["owner_channel_id"]
        at = int(r["collected_at"])
        key = (abs(at - ref), -at, -int(r["id"]))
        cur = best.get(owner)
        if cur is None or key < cur[0]:
            best[owner] = (key, r)
    return best


async def _delta_maps(now: int) -> tuple[dict, dict, dict | None]:
    """(1시간 전 스냅샷, 24시간 전 스냅샷, 1시간 기준 버킷 정보).

    1시간 쪽은 '기준 시각 ±허용오차 안의 실제 버킷' 하나를 골라 **그 한 시간 구간
    전체**를 읽는다. 같은 버킷에 collected_at이 여럿 섞여 있는 것은 정상이다
    (find_reference_baseline 주석 참고). 예전처럼 owner별 MAX(collected_at)<=기준
    으로 뽑으면 수집이 멈춰 있던 구간에서 며칠 전 값과 비교해 놓고 '1시간 증감'이라고
    표시하게 되므로, 회차를 하나로 고르는 성질 자체는 그대로 둔다.
    """
    db = await get_db()
    prev: dict = {}
    base = await find_reference_baseline(now, DELTA_WINDOW_SECONDS)
    if base is not None:
        b = base["bucket"]
        # owner마다 정확히 한 줄만 쓴다. 동점 기준을 끝까지 고정해 같은 입력이면
        # 항상 같은 행이 뽑히게 한다.
        #   ① 기준 시각과의 거리   ② collected_at DESC(같은 거리면 더 최근 값)
        #   ③ id DESC             (같은 시각의 중복 행 — 나중에 쓰인 행)
        #
        # 이 순서를 SQL의 ORDER BY ABS(...)로 만들면 정렬 가능한 인덱스가 없어
        # 임시 B-tree가 생긴다(실측 5,000명에서 유의미). 대신 인덱스 순서
        # (collected_at ASC)로 그냥 읽고 승자만 파이썬에서 고른다 — 비교 키가
        # 명시적이라 결정성은 SQL로 정렬할 때와 같다.
        ref = now - DELTA_WINDOW_SECONDS
        best = select_baseline_rows(await (await db.execute(
            "SELECT owner_channel_id, clip_uid, heart_count, rank, score, collected_at, id "
            "FROM singcup_snapshots WHERE event_id=? AND collected_at>=? AND collected_at<?",
            (EVENT_ID, b, b + 3600))).fetchall(), ref)
        # clip_uid까지 들고 있어야 '같은 대표 클립끼리만' 하트를 뺄 수 있다.
        # collected_at도 함께 둔다 — owner별 실제 기준 시각을 알아야 '1시간'이
        # 실제로 몇 분인지 말할 수 있다.
        prev = {o: (int(r["heart_count"]), int(r["rank"]), str(r["clip_uid"]),
                    float(r["score"] or 0), int(r["collected_at"]))
                for o, (_k, r) in best.items()}

    # 24시간 쪽도 기준 회차 '시각'을 함께 들고 나온다 — 그 시각 이후에 복구된
    # 클립은 24h 증감 역시 며칠치 누적이라 그대로 보여주면 안 된다.
    day: dict = {}
    for r in await (await db.execute(
        "SELECT owner_channel_id, heart_count, collected_at, clip_uid "
        "FROM singcup_snapshots s WHERE event_id=? "
        "AND collected_at = (SELECT MAX(collected_at) FROM singcup_snapshots "
        "WHERE event_id=s.event_id AND owner_channel_id=s.owner_channel_id "
        "AND collected_at <= ?) GROUP BY owner_channel_id",
        (EVENT_ID, now - 86400))).fetchall():
        # clip_uid까지 들고 온다. 24시간 사이에 대표가 바뀌었다면(삭제 교체 등)
        # 옛 클립의 하트를 새 클립에서 빼는 셈이라 증가율이 통째로 가짜가 된다.
        day[r["owner_channel_id"]] = (int(r["heart_count"]), int(r["collected_at"]),
                                      str(r["clip_uid"]))
    # 24시간 쪽은 owner별 MAX(collected_at)라 부분 세트의 영향을 받지 않는다
    # (각자 자기 최신 스냅샷을 찾으므로 한 회차에 묶이지 않는다). 그래서 이번
    # 수정 대상이 아니고, 대신 그 성질을 테스트로 고정해 둔다.
    return prev, day, base


# /main은 프론트가 아주 자주 부르는데(운영 로그 기준 초당 수 회), 매번 스트리머
# 전원을 조인·정렬하고 증감까지 계산해 1~4초가 걸렸다. 원본 데이터는 4분(탐색)
# 또는 1시간(정각 스윕)에 한 번만 바뀌므로 짧은 TTL 캐시로 충분하다.
# single-flight까지 두어 캐시가 만료된 순간 요청이 몰려도 계산은 한 번만 한다.
#
# 캐시에는 dict뿐 아니라 **직렬화된 body와 ETag까지 함께** 넣는다. 이 응답은 참가자
# 전원(1,000명 이상, 원본 약 850KB)이라 JSON 직렬화 자체가 비싸고, ETag를 요청마다
# 새로 계산하려면 어차피 한 번 더 직렬화해야 한다. 캐시를 채울 때 한 번만 만들어 두면
# 요청 경로에서는 bytes를 그대로 흘려보내기만 하면 된다.
MAIN_CACHE_TTL = float(os.getenv("SINGCUP_MAIN_CACHE_SECONDS", "20"))
# 캐시 항목 수 상한. limit은 호출자가 정하는 값이라(`?limit=1`, `?limit=99999` …)
# 서로 다른 값으로 계속 부르면 항목이 무한히 쌓인다. 항목 하나가 dict + bytes로
# 수 MB라 이건 그냥 메모리 폭주 경로다. 화면이 실제로 쓰는 값은 하나뿐이므로
# 넉넉히 잡아도 이 정도면 충분하다.
MAIN_CACHE_MAX_ENTRIES = int(os.getenv("SINGCUP_MAIN_CACHE_MAX_ENTRIES", "8"))
# limit -> (채운 시각, entry). entry = {"data", "body", "etag"}
_main_cache: dict[int, tuple[float, dict]] = {}
_main_lock = asyncio.Lock()
# 캐시 효율 관측용 — /api/singcup/observability 에서 그대로 노출한다.
_main_stats = {"hit": 0, "miss": 0, "coalesced": 0, "invalidated": 0,
               "waiting": 0, "waitingPeak": 0}


def invalidate_main_cache() -> None:
    """순위·데이터가 바뀌었을 때 즉시 버린다(옛 순위를 TTL 내내 보여주지 않도록)."""
    if _main_cache:
        _main_stats["invalidated"] += 1
    _main_cache.clear()


def main_cache_stats() -> dict:
    return {**_main_stats, "entries": len(_main_cache), "ttlSeconds": MAIN_CACHE_TTL}


def _build_main_entry(data: dict) -> dict:
    """응답 dict → 전송용 bytes + ETag.

    ETag 지문에서 `topHeartMovers1hComputedAt`은 뺀다. 캐시를 채울 때마다(20초)
    값이 바뀌는데 순위와 수치가 그대로인데도 ETag가 달라지면 304가 성립하지 않기
    때문이다.

    `topHeartMovers1hEvaluatedAt`은 **일부러 빼지 않는다.** 화면이 이 값을
    "마지막 재확인 시각"으로 보여주는데, 지문에서 빼면 서버가 다시 계산해 값이
    바뀌어도 ETag가 같아 304가 나가고, 클라이언트는 옛 evaluatedAt을 계속 표시한다
    — 사용자에게 거짓 시각을 보여주게 된다.

    비용은 늘지 않는다. 지문에는 이미 `summary.deltaBaseline.intervalSecondsMin/Max`
    (= `now - base[...]`)가 들어 있어 **uncached 재계산마다 지문이 어차피 달라진다**
    (실측: 같은 내용에 now만 2분 차이 → ETag 불일치). 즉 evaluatedAt을 넣고 빼는
    것으로 200/304 비율이 달라지지 않는다. ComputedAt 제외도 같은 이유로 실효가
    없지만, 기존 계약을 바꾸지 않으려고 그대로 둔다.

    304가 실제로 이득을 주는 구간은 `MAIN_CACHE_TTL`(20초) 안에서 같은 캐시 항목이
    여러 클라이언트에게 나갈 때다. 그 창 안에서는 bytes가 동일하므로 evaluatedAt도
    같고 ETag도 같다 — 넣어도 그 이득은 그대로 유지된다.

    starlette의 JSONResponse와 같은 옵션으로 직렬화한다 — 다른 옵션을 쓰면 같은
    데이터가 다른 bytes가 돼 Content-Length와 실제 응답이 어긋난다.
    """
    body = json.dumps(data, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")
    fingerprint = {k: v for k, v in data.items() if k != "topHeartMovers1hComputedAt"}
    digest = hashlib.sha256(
        json.dumps(fingerprint, ensure_ascii=False, allow_nan=False,
                   separators=(",", ":")).encode("utf-8")).hexdigest()[:32]
    # 약한(weak) ETag를 쓴다 — 앞단 프록시가 gzip으로 표현을 바꾸므로 바이트 단위
    # 동일성(strong)을 주장할 수 없다. 조건부 요청에는 weak로 충분하다.
    return {"data": data, "body": body, "etag": f'W/"{digest}"'}


async def load_main_entry(limit: int = 200) -> tuple[dict, str]:
    """(entry, source) — source는 hit/coalesced/miss. **읽기 전용이다.**

    이 경로에는 DB 쓰기가 없다. 예전에는 `persist_top_movers=True`가 기본이라
    공개 GET이 응답을 만들면서 UPDATE + COMMIT까지 했고, 그 쓰기가 잠금에 걸리자
    조회 요청 전체가 500이 됐다(실측 2026-07-31 Railway).

    플래그의 기본값을 False로 바꾸는 대신 **매개변수를 없앴다.** 기본값 하나에
    안전성을 맡기면 호출자가 실수로 True를 넘길 수 있고, 그 실수는 장애로만 드러난다.
    저장이 필요하면 `persist_top_movers_snapshot()`을 내부 작업에서 부른다.
    """
    hit = _main_cache.get(limit)
    now_m = time.monotonic()
    if hit and now_m - hit[0] < MAIN_CACHE_TTL:
        _main_stats["hit"] += 1
        return hit[1], "hit"

    _main_stats["waiting"] += 1
    _main_stats["waitingPeak"] = max(_main_stats["waitingPeak"], _main_stats["waiting"])
    try:
        async with _main_lock:
            hit = _main_cache.get(limit)      # 대기 중에 다른 요청이 채웠을 수 있다
            now_m = time.monotonic()
            if hit and now_m - hit[0] < MAIN_CACHE_TTL:
                _main_stats["coalesced"] += 1
                return hit[1], "coalesced"
            data = await _load_main_uncached(limit)
            entry = _build_main_entry(data)
            _main_cache[limit] = (time.monotonic(), entry)
            # 분리 API 스냅샷은 **여기서 만들지 않는다.** 조회 경로가 생산자가 되면
            # 프론트가 /main을 그만 부르는 순간 생산이 멈추고, 재시작 후 레지스트리가
            # 영영 비어 있을 수 있다. 생산은 랭킹 계산이 끝나는 지점(publish_snapshot)
            # 하나로 모은다.
            while len(_main_cache) > MAIN_CACHE_MAX_ENTRIES:
                oldest = min(_main_cache, key=lambda k: _main_cache[k][0])
                _main_cache.pop(oldest, None)
            _main_stats["miss"] += 1
            return entry, "miss"
    finally:
        _main_stats["waiting"] -= 1


async def load_main(limit: int = 200) -> dict:
    """메인/랭킹 공용 데이터(dict).

    HTTP 라우터는 직렬화된 bytes와 ETag가 필요해 `load_main_entry`를 직접 쓴다.
    이쪽은 응답 dict만 필요한 호출자(진단·스크립트)를 위한 얇은 래퍼다.
    """
    entry, _ = await load_main_entry(limit)
    return entry["data"]


async def _load_main_uncached(limit: int = 200) -> dict:
    """메인/랭킹 공용 데이터 — 스트리머별 대표 클립 + 점수 + 변화량 + 현재 라이브."""
    db = await get_db()
    rows = [dict(r) for r in await (await db.execute(
        """SELECT s.channel_id, s.channel_name, s.channel_image_url, s.follower_count,
                  s.verified_mark, s.tagged_clip_count,
                  c.clip_uid, c.clip_title, c.thumbnail_image_url, c.heart_count,
                  c.view_count, c.created_at, c.duration, c.metrics_recovered_at,
                  c.first_collected_at
           FROM singcup_streamers s
           JOIN singcup_clips c ON c.clip_uid = s.representative_clip_uid
           WHERE s.event_id=? AND c.active=1""", (EVENT_ID,))).fetchall()]

    reps = [{**r, "owner_channel_id": r["channel_id"]} for r in rows]
    ranked = compute_scores(reps)

    now = int(time.time())
    prev, day, base = await _delta_maps(now)
    ref_ts = base["lo"] if base else None

    # 현재 라이브 — 기존 수집 데이터(rising_live_snapshots)의 최신 사이클과 연결한다
    live: dict = {}
    latest = await (await db.execute(
        "SELECT collected_at FROM rising_collect_runs WHERE ok=1 "
        "ORDER BY collected_at DESC LIMIT 1")).fetchone()
    if latest:
        for r in await (await db.execute(
            "SELECT chzzk_channel_id, live_title, concurrent_viewers, category_name "
            "FROM rising_live_snapshots WHERE collected_at=?", (latest["collected_at"],)
        )).fetchall():
            live[r["chzzk_channel_id"]] = {
                "liveTitle": r["live_title"] or "",
                "concurrentViewers": int(r["concurrent_viewers"] or 0),
                "categoryName": r["category_name"] or "",
            }

    # 라이브 신선도 — 이 값은 싱드컵 수집기가 아니라 전체 라이브 스캔 주기에 묶여 있다.
    # 화면이 60초마다 새로 받아도 여기가 안 바뀌면 같은 값이므로, '언제 확인한
    # 라이브인지'를 같이 내려 화면에서 오해가 없게 한다.
    from rising_collector import COLLECT_INTERVAL as _LIVE_INTERVAL
    live_at = int(latest["collected_at"]) if latest else None
    live_info = {
        "collectedAt": datetime.fromtimestamp(live_at, _KST).isoformat() if live_at else None,
        "nextExpectedAt": (datetime.fromtimestamp(live_at + _LIVE_INTERVAL, _KST).isoformat()
                           if live_at else None),
        "intervalSeconds": int(_LIVE_INTERVAL),
        # 한 주기를 훌쩍 넘겼으면(1.5배) 수집이 밀리고 있다는 뜻
        "isStale": live_at is None or (now - live_at) > _LIVE_INTERVAL * 1.5,
    }

    out = []
    # 상한이 참가자 수보다 낮으면 잘린 뒤쪽 사람들은 화면 검색에 아예 걸리지 않는다
    # (검색은 이 응답 안에서만 이뤄진다). 참가자 전원이 담기도록 넉넉히 잡는다.
    for r in ranked[:max(1, min(3000, limit))]:
        cid = r["channel_id"]
        p = prev.get(cid)
        d24_row = day.get(cid)
        d24 = d24_row[0] if d24_row else None
        rec_at = int(r["metrics_recovered_at"] or 0)
        # 비교 기준 회차 이후에 '복구'된 클립이면 그 사이 증가분은 신뢰할 수 없다.
        # 신선한 스냅샷이 기준 시각을 넘겨 다시 쌓이면 자연히 해제된다.
        # 1시간과 24시간은 기준 시각이 다르므로 각각 따로 판정한다 — 24시간 쪽이
        # 창이 넓어 오염이 더 오래 남는다.
        recovered = bool(ref_ts and rec_at >= ref_ts)
        recovered24 = bool(d24_row and rec_at >= d24_row[1])
        # 24시간 전 스냅샷이 지금과 **다른 클립**을 가리키면(대표 교체) 비교하지 않는다.
        # 삭제된 대표를 새 클립으로 갈아 끼운 직후가 바로 이 경우다.
        rep_changed24 = bool(d24_row and d24_row[2] != r["clip_uid"])
        # 기준값이 없는 이유를 나눈다. '기준 버킷에 없다'가 곧 '신규'는 아니다 —
        # 기준 버킷이 없거나 불완전하면 원래 있던 사람도 빠져 있을 수 있다.
        first_at = int(r["first_collected_at"] or 0)
        # 기준선이 아예 없으면 비교할 대상이 없다. 그래도 '기준 시각 뒤에 처음
        # 발견된 사람'은 기준선 유무와 무관하게 진짜 신규다.
        cutoff = base["hi"] if base is not None else now - DELTA_WINDOW_SECONDS
        if p is not None:
            missing = None
        elif first_at > cutoff:
            missing = "new"            # 기준 시점 뒤에 처음 발견된 참가자
        elif base is None:
            missing = "insufficient_history"  # 비교할 기준선 자체가 없다
        else:
            missing = "baseline_incomplete"   # 그때 있었어야 하는데 기준선에 없다
        out.append({
            "rank": r["rank"], "channelId": cid,
            "channelName": r["channel_name"], "channelImageUrl": r["channel_image_url"],
            "followerCount": r["follower_count"], "verifiedMark": bool(r["verified_mark"]),
            "taggedClipCount": r["tagged_clip_count"],
            "clipUid": r["clip_uid"], "clipTitle": r["clip_title"],
            "clipThumbnailUrl": r["thumbnail_image_url"],
            "heartCount": r["heart_count"], "viewCount": r["view_count"],
            "createdAt": datetime.fromtimestamp(r["created_at"], _KST).isoformat(),
            "viewScore": r["view_score"], "heartScore": r["heart_score"],
            "score": r["score"],
            # 하트 증감은 '같은 대표 클립'끼리만 뺀다. 그 사이 대표 클립이 바뀌었다면
            # 서로 다른 영상의 하트를 빼는 셈이라 증가/감소가 통째로 가짜가 된다.
            # 또 갱신이 오래 멈췄다가 복구된 클립은 그 차이가 며칠치 누적이므로
            # 단기 증감에서 뺀다(0 → 52를 '1시간에 +52'로 보여주지 않기 위해).
            "heartDelta": (r["heart_count"] - p[0])
                          if p and p[2] == r["clip_uid"] and not recovered else None,
            "deltaState": (missing if missing is not None
                           else "recovering" if recovered
                           else "representative_changed" if p[2] != r["clip_uid"]
                           else "ok"),
            "rankDelta": (p[1] - r["rank"]) if p else None,
            "scoreDelta": round(r["score"] - p[3], 2) if p and not recovered else None,
            "heartDelta24h": (r["heart_count"] - d24)
                             if d24 is not None and not recovered24
                             and not rep_changed24 else None,
            "heartChangeRate24h": heart_change_rate(r["heart_count"], d24)
                                  if d24 is not None and not recovered24
                                  and not rep_changed24 else None,
            # 대표가 바뀌었으면 24시간 쪽도 비교하지 않는다 — 1시간 쪽과 같은 이유다.
            "delta24hState": ("representative_changed" if rep_changed24
                              else "recovering" if recovered24
                              else "new" if d24 is None else "ok"),
            # NEW는 '기준 버킷이 닫힌 뒤 처음 발견된 참가자'에만 붙인다. 기준선이
            # 없거나 불완전해서 빠진 사람은 신규가 아니라 '아직 못 세운' 것이다.
            "isNew": missing == "new",
            "live": live.get(cid),
        })

    last_run = await (await db.execute(
        "SELECT MAX(collected_at) c FROM singcup_snapshots WHERE event_id=?",
        (EVENT_ID,))).fetchone()
    last_at = last_run["c"] if last_run and last_run["c"] else None
    # KPI 증감 — 지금과 1시간 전을 '같은 소스(singcup_clips)'에서 세야 뺄셈이 성립한다.
    # first_collected_at은 그 클립을 처음 확인한 시각이라, 그 이하만 세면 1시간 전 상태다.
    # (스냅샷은 스트리머당 대표 클립 1건만 남기므로 '그때의 전체 클립 수'를 알 수 없다.)
    #
    # 다만 기준 시각 근처에 실제 수집 회차가 없었다면(수집 중단 등) 비교 자체를 하지
    # 않는다 — 그 구간에 안 들어온 클립까지 '1시간 사이 증가'로 잡히기 때문이다.
    ref_for_kpi = ref_ts if ref_ts is not None else None
    cnt = await (await db.execute(
        """SELECT COUNT(*)                         AS clips,
                  COUNT(DISTINCT owner_channel_id) AS streamers,
                  SUM(CASE WHEN first_collected_at <= ? THEN 1 ELSE 0 END) AS clips_before,
                  COUNT(DISTINCT CASE WHEN first_collected_at <= ?
                                      THEN owner_channel_id END)           AS streamers_before
           FROM singcup_clips WHERE event_id=? AND active=1""",
        (ref_for_kpi or 0, ref_for_kpi or 0, EVENT_ID))).fetchone()
    clips_now = int(cnt["clips"] or 0)

    # 기준 회차가 없으면(수집 시작 1시간 이내 / 수집 중단) 비교하지 않는다 → null.
    # 이때 0으로 표시하면 '변화 없음'과 구분되지 않는다.
    has_ref = ref_for_kpi is not None and int(cnt["clips_before"] or 0) > 0

    # ── 최근 1시간 하트 급상승 Top 5 ────────────────────────────────────────
    # 스트리머당 '현재 대표 클립' 하나만 본다. 여러 클립의 증가량을 합치면 클립을 많이
    # 올린 사람이 유리해지고, 대표 클립이 바뀐 경우 다른 영상끼리 빼는 일이 생긴다.
    movers = []
    for r in ranked:
        p = prev.get(r["channel_id"])
        if not p or p[2] != r["clip_uid"]:      # 비교 기록 없음 / 대표 클립이 바뀜
            continue
        # 갱신 공백 뒤 복구된 클립은 며칠치 누적이 1시간 급상승으로 둔갑한다 —
        # 급상승 랭킹은 이 오염에 특히 취약하므로 아예 제외한다.
        if ref_ts and int(r["metrics_recovered_at"] or 0) >= ref_ts:
            continue
        d = int(r["heart_count"]) - int(p[0])
        if d <= 0:
            continue
        movers.append((d, r))
    movers.sort(key=lambda t: (-t[0], -int(t[1]["heart_count"]), -float(t[1]["score"]),
                               str(t[1]["channel_id"])))
    top_movers = [{
        "rank": i + 1,
        "channelId": r["channel_id"], "channelName": r["channel_name"],
        "channelImageUrl": r["channel_image_url"],
        "clipUid": r["clip_uid"], "clipTitle": r["clip_title"],
        "clipThumbnailUrl": r["thumbnail_image_url"],
        "heartCount": int(r["heart_count"]), "heartDelta1h": d,
        # 급상승 목록에서도 복구된 클립의 24h 증감은 내리지 않는다
        "heartDelta24h": (int(r["heart_count"]) - day[r["channel_id"]][0])
                         if (r["channel_id"] in day
                             and int(r["metrics_recovered_at"] or 0)
                             < day[r["channel_id"]][1]) else None,
        "score": r["score"],
        "live": live.get(r["channel_id"]),
    } for i, (d, r) in enumerate(movers[:5])]

    # 비교 기준 회차가 없거나(배포 직후·수집 공백) 그 사이 아무도 하트를 못 받으면
    # 목록이 통째로 빈다. 카드가 사라지는 것보다 직전 정상 집계를 '언제 것인지'와
    # 함께 보여주는 편이 낫다 — 현재 값으로 오해되지 않도록 stale 표시를 붙인다.
    if top_movers:
        movers_out, movers_stale = top_movers, False
        movers_base, movers_at = ref_ts, now
        # **여기서 저장하지 않는다.** 이 함수는 공개 GET이 타는 경로이고, 응답을
        # 만들면서 DB를 쓰면 잠금 하나가 조회 실패가 된다. 저장은 랭킹 계산이 끝난
        # 뒤 persist_top_movers_snapshot()이 맡는다(요청 경로 밖).
    else:
        movers_out, movers_base, movers_at = await _last_top_movers()
        movers_stale = bool(movers_out)

    return {
        "event": event_meta(),
        "summary": {
            "taggedClipCount": clips_now,
            "streamerCount": len(ranked),
            "liveCount": sum(1 for r in out if r["live"]),
            "taggedClipDelta": (clips_now - int(cnt["clips_before"] or 0)) if has_ref else None,
            "streamerDelta": (int(cnt["streamers"] or 0) - int(cnt["streamers_before"] or 0))
                             if has_ref else None,
            "deltaWindowMinutes": DELTA_WINDOW_SECONDS // 60,
            # 실제로 비교한 회차 시각 — 툴팁에서 '약 1시간 전'의 실체를 보여줄 수 있다
            "deltaBaseAt": (datetime.fromtimestamp(ref_for_kpi, _KST).isoformat()
                            if has_ref else None),
            # 기준선 진단(요약). 1시간 증감이 통째로 죽는 사고를 응답만 보고 판별할
            # 수 있어야 한다(2026-07-30: 기준선 7명 / 참가자 1,060명).
            #
            # 한 버킷 안에서도 owner마다 collected_at이 다를 수 있으므로 단일
            # '기준 시각'을 정답처럼 주지 않는다 — 구간과 실제 간격 범위를 준다.
            # owner별 상세와 후보 목록은 /snapshots/baseline(관리자)에만 둔다
            # (전원분을 실으면 응답이 약 53KB 늘어 비용 대책과 정면으로 충돌한다).
            "deltaBaseline": None if base is None else {
                "bucketAt": datetime.fromtimestamp(base["bucket"], _KST).isoformat(),
                "minCollectedAt": datetime.fromtimestamp(base["lo"], _KST).isoformat(),
                "maxCollectedAt": datetime.fromtimestamp(base["hi"], _KST).isoformat(),
                "rows": base["rows"], "expectedRows": base.get("expected"),
                "coverage": base.get("coverage"),
                "partial": bool(base.get("partial")),
                "fallbackUsed": bool(base.get("fallbackUsed")),
                # 실제 비교 간격의 범위. '정확히 1시간'이 아님을 데이터로 밝힌다.
                "intervalSecondsMin": now - base["hi"],
                "intervalSecondsMax": now - base["lo"],
            },
        },
        "topHeartMovers1h": movers_out,
        # 지금 계산한 값인지, 직전 정상 집계를 다시 보여주는 것인지 구분한다.
        # 화면에서 "언제 것인지"를 밝히지 않으면 옛 순위를 현재로 오해한다.
        "topHeartMovers1hStale": movers_stale,
        "topHeartMovers1hBaseAt": (datetime.fromtimestamp(movers_base, _KST).isoformat()
                                   if movers_base else None),
        "topHeartMovers1hComputedAt": (datetime.fromtimestamp(movers_at, _KST).isoformat()
                                       if movers_at else None),
        # **후보 계산을 마지막으로 실제 실행한 시각.** ComputedAt과 다르다.
        #
        # 실시간 후보가 0명이면 응답은 직전 정상 집계로 되돌아가고(stale=true),
        # 그때 BaseAt/ComputedAt은 **그 옛 집계 자신의 시각**이라 그대로 멈춰 있다.
        # 실측 2026-08-02: 21:59 집계가 23:05에도 그대로 보였고, 사용자는 계산이
        # 멈춘 것으로 오해했다. 실제로는 매 회차 다시 계산했지만 조건을 만족하는
        # 후보가 없었을 뿐이다. 그 사실을 말할 수 있는 값이 응답에 없었다.
        #
        # 그래서 stale이든 아니든 **이번 계산의 now**를 그대로 싣는다. 이 값은
        # `_load_main_uncached`가 새로 돌 때만 바뀌고, 캐시가 같은 응답을 다시 줄
        # 때는 함께 캐시돼 그대로 나온다 — "요청을 받은 시각"이 아니다.
        "topHeartMovers1hEvaluatedAt": datetime.fromtimestamp(now, _KST).isoformat(),
        # **이번 평가에서 실제로 양수였던 owner 수.** 화면에 보이는 카드 수와 다르다 —
        # fallback 중이면 카드는 옛 결과이고 이 값은 0이다. "이전 결과가 보이는데
        # 지금은 후보가 없다"를 이 숫자 하나로 말할 수 있다.
        # 최대 5개로 자르기 **전** 값이라 6명 이상이면 5보다 클 수 있다.
        "topHeartMovers1hPositiveCount": len(movers),
        "live": live_info,
        "collector": {
            "lastSuccessAt": datetime.fromtimestamp(last_at, _KST).isoformat()
                             if last_at else None,
            "stale": last_at is None or (now - last_at) > 30 * 60,
        },
        "streamers": out,
    }


# ── 분리 API 스냅샷 생산 ────────────────────────────────────────────────────
# 생산은 **조회와 완전히 분리한다.** 조회 경로가 생산자를 겸하면 프론트가 /main을
# 그만 부르는 순간 갱신이 멈추고, 재시작 후에도 아무도 /main을 부르지 않으면
# 레지스트리가 영영 비어 있다. 그래서 랭킹 계산이 끝나는 지점에서만 만든다.
SNAPSHOT_PUBLISH_INTERVAL = float(
    os.getenv("SINGCUP_SNAPSHOT_PUBLISH_INTERVAL_SECONDS", "600"))
# 기동 직후 첫 게시까지의 지연. DB는 lifespan에서 이미 init 되므로 길게 잡을
# 이유가 없다 — 짧게 시도하고 실패할 때만 지수 백오프한다.
SNAPSHOT_WARMUP_DELAY = float(os.getenv("SINGCUP_SNAPSHOT_WARMUP_DELAY", "2"))
SNAPSHOT_RETRY_MAX = float(os.getenv("SINGCUP_SNAPSHOT_RETRY_MAX_SECONDS", "300"))
_publish_stats = {"published": 0, "unchanged": 0, "failed": 0,
                  "lastVersion": None, "lastMs": None, "lastSource": None,
                  "lastAt": None, "lastError": None}


def snapshot_publish_stats() -> dict:
    return dict(_publish_stats)


async def publish_snapshot(*, source: str) -> str | None:
    """완성된 랭킹을 분리 API의 불변 스냅샷으로 게시한다.

    실패해도 예외를 밖으로 내보내지 않는다 — 스냅샷 등록이 수집·스윕을 죽이면
    본말이 전도된다. 검증에 실패하면 아무것도 등록하지 않으므로 **기존 latest가
    그대로 유지**된다(원자적 교체).
    """
    t0 = time.perf_counter()
    try:
        import singcup_split_api as _split
        if not _split.SPLIT_API_ENABLED:
            # 비활성 배포에서는 계산도 메모리도 로그도 쓰지 않는다.
            return None
        # 게시는 순수 생산이다 — DB 쓰기를 하지 않는다.
        entry, _src = await load_main_entry(_split.MAX_SNAPSHOT_LIMIT)
        data = entry["data"]
        rows = data.get("streamers") or []
        # 불완전한 결과는 게시하지 않는다. 참가자가 0명인 응답을 latest로 올리면
        # 화면이 통째로 비어 버린다(수집 실패·DB 잠금 직후에 실제로 나올 수 있다).
        if not rows or len(rows) != int(data.get("summary", {}).get("streamerCount") or 0):
            _publish_stats["failed"] += 1
            _publish_stats["lastError"] = "incomplete_payload"
            _log({"event": "snapshot_publish_skipped", "level": "warning",
                  "source": source, "rows": len(rows),
                  "streamerCount": data.get("summary", {}).get("streamerCount")})
            return None
        before = _split.latest()
        snap = _split.register(data)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        changed = before is None or before.version != snap.version
        _publish_stats["published" if changed else "unchanged"] += 1
        _publish_stats.update(lastVersion=snap.version, lastMs=ms, lastSource=source,
                              lastAt=_iso(int(time.time())), lastError=None)
        _log({"event": "snapshot_published" if changed else "snapshot_unchanged",
              "source": source, "version": snap.version, "items": len(snap.streamers),
              "duration_ms": ms, "versions": _split.stats()["versions"]})
        return snap.version
    except Exception as e:      # noqa: BLE001 — 게시 실패가 수집을 멈추면 안 된다
        _publish_stats["failed"] += 1
        _publish_stats["lastError"] = str(e)[:160]
        _log({"event": "snapshot_publish_failed", "level": "warning",
              "source": source, "detail": str(e)[:200]})
        return None


async def start_snapshot_publisher():
    """기동 직후 warm-up + 느린 안전망 주기.

    평상시 갱신은 recompute_ranking이 맡는다. 이 루프는 두 가지만 담당한다.
      · 재시작 직후 레지스트리가 비어 있는 구간을 줄인다(warm-up)
      · 어떤 이유로든 recompute가 오래 돌지 않을 때의 안전망
    실패하면 지수 백오프로 다시 시도한다 — 요청마다 재시도하지 않는다.
    """
    if os.getenv("SINGCUP_ENABLED", "true").lower() in ("0", "false", "no"):
        return
    import singcup_split_api as _split
    if not _split.SPLIT_API_ENABLED:
        # 비활성 배포에서는 태스크 자체를 시작하지 않는다(추가 부하 0).
        _log({"event": "snapshot_publisher_disabled"})
        return
    await asyncio.sleep(SNAPSHOT_WARMUP_DELAY)
    backoff = 5.0
    while True:
        ok = await publish_snapshot(source="warmup") is not None
        if ok:
            backoff = 5.0
            await asyncio.sleep(SNAPSHOT_PUBLISH_INTERVAL)
        else:
            await asyncio.sleep(backoff)
            backoff = min(SNAPSHOT_RETRY_MAX, backoff * 2)


async def load_streamer_clips(channel_id: str) -> dict:
    """카드에서 '싱드컵 태그 클립 N개'를 눌렀을 때 펼칠 목록."""
    db = await get_db()
    rows = await (await db.execute(
        "SELECT clip_uid, clip_title, thumbnail_image_url, heart_count, view_count, "
        "created_at, duration FROM singcup_clips "
        "WHERE event_id=? AND owner_channel_id=? AND active=1 "
        "ORDER BY heart_count DESC, view_count DESC, created_at ASC, clip_uid ASC",
        (EVENT_ID, channel_id))).fetchall()
    return {"channelId": channel_id, "clips": [
        {"clipUid": r["clip_uid"], "clipTitle": r["clip_title"],
         "clipThumbnailUrl": r["thumbnail_image_url"], "heartCount": r["heart_count"],
         "viewCount": r["view_count"], "duration": r["duration"],
         "createdAt": datetime.fromtimestamp(r["created_at"], _KST).isoformat()}
        for r in rows]}


# ── 스케줄러 ────────────────────────────────────────────────────────────────
CLIP_INTERVAL_MINUTES = float(os.getenv("SINGCUP_CLIP_INTERVAL_MINUTES", "4"))


async def start_clip_collector():
    """정기 루프 — 신규 탐색 + 지표 갱신 + 실패 재시도.

    과거 적재(백필)는 여기서 하지 않는다. 성격이 달라 별도 워커
    (start_backfill_worker)가 완료될 때까지 연속으로 처리한다.
    """
    if os.getenv("SINGCUP_ENABLED", "true").lower() in ("0", "false", "no"):
        return
    await asyncio.sleep(float(os.getenv("SINGCUP_CLIP_START_DELAY_SECONDS", "40")))
    while True:
        wait = CLIP_INTERVAL_MINUTES
        cycle = {"cycle_id": uuid.uuid4().hex[:12], "step": None, "operation": None,
                 "partial_reasons": []}
        token = _CYCLE.set(cycle)
        cycle_t0 = time.perf_counter()
        results: dict[str, str] = {}
        try:
            st = event_status()
            # ── 축이 셋이다. 하나의 `event_status()`로 묶지 말 것 (SINGCUP-1) ──
            #   등록  : 신규 참가자·클립을 새로 들이는 일 → 종료와 함께 닫힌다
            #   지표  : 이미 등록된 클립의 상태를 바로잡는 일 → 종료 후에도 연다
            #   스냅샷: 급상승의 기준선 → 종료 후에도 연다(멈추면 급상승이 0으로 굳는다)
            reg, met, snap = registration_open(), metrics_refresh_open(), snapshot_refresh_open()

            # 지표 갱신(조회수·하트) 자체는 여기서 하지 않는다 — singcup_sweep이
            # 연속 사이클로 전체를 훑는다. 이 루프는 탐색과 상태 정정만 맡는다.
            # 각 단계는 서로 독립이다 — 한 단계가 잠금으로 실패해도 나머지는 돌린다.
            steps: list[tuple[str, object]] = []
            if reg:
                steps += [
                    ("discover",  discover_new_clips),
                    ("retry",     retry_failed_clips),
                    # 설명이 나중에 바뀐 클립을 데려온다(스캔 기록 기준)
                    ("recheck",   recheck_untagged_clips),
                    # 양쪽 표에 다 없는 '고아'를 되찾는다(주기적 전체 대조)
                    ("reconcile", maybe_reconcile),
                ]
            if met:
                # 삭제 확정은 대표 재선정·순위에 직접 영향을 준다 → 지표 축이다.
                # 종료 후에도 열어 둬야 지워진 클립이 순위에 계속 남지 않는다.
                # 대상이 없으면 요청 0건이라 별도 워커를 만들지 않고 이 루프에 얹는다.
                steps.append(("deletion", run_deletion_checks))
            for name, fn in steps:
                results[name] = await _run_step(name, fn)

            if snap:
                # 스냅샷은 다르다. `UNIQUE(bucket)` + `INSERT OR IGNORE`라 한 번
                # 쓰면 그 시간 안에는 **교체할 수 없다.** 불완전한 회차의 값을
                # 정상 기준선으로 굳히면 정상 회차가 와도 못 고친다.
                # 건너뛰면 다음 4분 회차가 같은 버킷을 정상으로 채울 수 있다.
                incomplete = [k for k, v in results.items()
                              if v in ("failed", "partial")]
                if incomplete:
                    results["snapshot"] = "skipped"
                    _log({"event": "snapshot_skipped_incomplete_cycle",
                          "failed_steps": [k for k, v in results.items() if v == "failed"],
                          "partial_steps": [k for k, v in results.items() if v == "partial"]})
                else:
                    results["snapshot"] = await _run_step("snapshot", ensure_hourly_snapshot)

            if st == "UPCOMING":
                wait = 30.0
            elif not (reg or met or snap):
                wait = 360.0          # 전부 닫혔을 때만 사실상 멈춘다
        except Exception as e:
            _log({"event": "loop_error", "level": "warning", "detail": str(e)[:200]})
        finally:
            if results:                         # 이벤트 기간 밖에서는 남기지 않는다
                def _n(kind):
                    return sum(1 for v in results.values() if v == kind)
                _log({"event": "collector_cycle_done",
                      "steps_success": _n("success"), "steps_partial": _n("partial"),
                      "steps_failed": _n("failed"), "steps_skipped": _n("skipped"),
                      "steps": results,
                      "partial_reasons": cycle["partial_reasons"][:8] or None,
                      "duration_ms": int((time.perf_counter() - cycle_t0) * 1000)})
            _CYCLE.reset(token)
        await asyncio.sleep(max(60.0, wait * 60))
