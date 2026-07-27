"""싱드컵 이벤트 수집기 — 네이버 게임 '치지직 라운지' 자유게시판(boardId=4).

치지직 라운지에서 진행 중인 '싱드컵' 참가 게시글을 모아 버프 수 기준 순위를 만든다.

    GET https://comm-api.game.naver.com/nng_main/v1/community/lounge/chzzk/feed
        ?offset=<페이지번호>&limit=30&order=NEW&boardId=4&buffFilteringYN=N

**offset은 글 개수가 아니라 페이지 번호다** — 다음 페이지는 offset+1이지 offset+30이 아니다.
limit은 30을 넘기면 400이 나므로 올리지 않는다.

공식 Open API가 아니라 웹 프론트가 쓰는 내부 API라서 예고 없이 형태가 바뀌거나 막힐 수
있다. 그래서 (1) 원본 응답 구조에 의존하는 코드는 이 파일 안에만 두고(라우터/프론트는
정규화된 dict만 본다), (2) 수집에 실패해도 DB의 기존 순위는 절대 지우지 않으며,
(3) 스키마가 깨지면 '빈 결과'가 아니라 '수집 실패'로 처리한다.

네이버 로그인 쿠키·세션은 쓰지 않는다(비로그인으로 조회 가능한 API다).
"""
import asyncio
import html
import json
import os
import random
import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from database import get_db

FEED_API = "https://comm-api.game.naver.com/nng_main/v1/community/lounge/chzzk/feed"
BOARD_ID = int(os.getenv("SINGCUP_BOARD_ID", "4"))
PAGE_LIMIT = 30          # API 상한. 30을 넘기면 400이 난다 — 올리지 않는다.

USER_AGENT = os.getenv("SINGCUP_USER_AGENT", "NexBot-SingcupCollector/1.0")
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

_KST = timezone(timedelta(hours=9))


def _env_dt(name: str, default: str) -> datetime:
    raw = (os.getenv(name) or default).strip()
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        d = datetime.fromisoformat(default)
    return d if d.tzinfo else d.replace(tzinfo=_KST)


ENABLED = (os.getenv("SINGCUP_ENABLED", "true").lower() not in ("0", "false", "no"))
EVENT_ID = os.getenv("SINGCUP_EVENT_ID", "singcup-2026")
EVENT_NAME = os.getenv("SINGCUP_EVENT_NAME", "싱드컵")
# 이벤트 기간은 여기(또는 환경변수) 한 곳에서만 관리한다 — 다른 파일에 하드코딩하지 않는다.
# 시작일은 07-20이 아니라 **07-27 20:00 KST**다(운영자 확인). 07-20으로 넓혔다가 되돌렸다.
START_AT = _env_dt("SINGCUP_START_AT", "2026-07-27T20:00:00+09:00")
END_AT = _env_dt("SINGCUP_END_AT", "2026-08-09T23:59:59+09:00")
COLLECT_INTERVAL_MINUTES = float(os.getenv("SINGCUP_COLLECT_INTERVAL_MINUTES", "3"))
MAX_PAGES = int(os.getenv("SINGCUP_MAX_PAGES", "100"))
# 과거 구간을 한 번에 훑는 backfill 모드용 상한(평소 수집보다 깊게 들어갈 수 있게)
BACKFILL_MAX_PAGES = int(os.getenv("SINGCUP_BACKFILL_MAX_PAGES", "300"))
REQUEST_TIMEOUT = float(os.getenv("SINGCUP_REQUEST_TIMEOUT_MS", "10000")) / 1000
MAX_RETRIES = max(1, int(os.getenv("SINGCUP_MAX_RETRIES", "3")))
BACKOFF_BASE_SECONDS = float(os.getenv("SINGCUP_BACKOFF_BASE_SECONDS", "1"))
BACKOFF_MAX_SECONDS = float(os.getenv("SINGCUP_BACKOFF_MAX_SECONDS", "30"))
# 페이지 사이 최소 간격 — 순차 호출이라 병렬 폭주는 없지만 예의상 간격을 둔다
PAGE_DELAY_SECONDS = float(os.getenv("SINGCUP_PAGE_DELAY_SECONDS", "0.3"))
# 수집 작업 최대 실행 시간(초). 락 TTL로도 쓰인다.
MAX_RUN_SECONDS = int(os.getenv("SINGCUP_MAX_RUN_SECONDS", "300"))
# 이벤트 종료 후에도 이 시간 동안은 낮은 빈도로 최종 검산을 돌린다
POST_EVENT_HOURS = float(os.getenv("SINGCUP_POST_EVENT_HOURS", "24"))
POST_EVENT_INTERVAL_MINUTES = float(os.getenv("SINGCUP_POST_EVENT_INTERVAL_MINUTES", "60"))
# 마지막 정상 수집이 이보다 오래되면 프론트에 '집계 지연'을 띄운다
STALE_AFTER_MINUTES = float(os.getenv("SINGCUP_STALE_AFTER_MINUTES", "20"))
# 전체 수집에 성공한 회차에서 연속 이만큼 안 보이면 비활성 처리(일시 누락으로 지우지 않기 위함)
MISSING_SCANS_TO_DEACTIVATE = int(os.getenv("SINGCUP_MISSING_SCANS", "2"))

ADMIN_SECRET = os.getenv("SINGCUP_ADMIN_SECRET", "")

# 상태 값
ST_OK = "OK"
ST_FAILED = "FAILED"          # 네트워크/5xx 등 일시 실패
ST_BLOCKED = "BLOCKED"        # 401/403 — 운영자 확인 필요
ST_SCHEMA = "SCHEMA_ERROR"    # 응답 구조 변경 — 운영자 확인 필요
ST_SKIPPED = "SKIPPED"        # 락 미획득/비활성 구간

# '[싱드컵]' 말머리만 참가작으로 본다. 제목에 '싱드컵'이라는 단어만 들어간 일반 글은 제외.
_SINGCUP_RE = re.compile(r"^\s*\[\s*싱드컵\s*\]")
_CLIP_RE = re.compile(r"https://chzzk\.naver\.com/clips/[A-Za-z0-9_-]+")


class SchemaError(RuntimeError):
    """응답 전체 구조가 예상과 다름 — 빈 결과가 아니라 '수집 실패'로 다뤄야 한다."""


def _log(payload: dict):
    """구조화 로그. secret/쿠키/인증값은 절대 넣지 않는다."""
    print(f"[singcup] {json.dumps(payload, ensure_ascii=False, default=str)}", flush=True)


# ── 순수 파싱 헬퍼 (테스트 대상) ────────────────────────────────────────────
def normalize_title(raw: str | None) -> str:
    """HTML entity 디코딩 → 유니코드 정규화 → 앞뒤 공백 제거."""
    if not raw:
        return ""
    return unicodedata.normalize("NFKC", html.unescape(str(raw))).strip()


def is_singcup_title(raw: str | None) -> bool:
    return bool(_SINGCUP_RE.match(normalize_title(raw)))


def parse_created_date(raw) -> datetime | None:
    """'YYYYMMDDHHmmss'(KST)를 timezone-aware datetime으로. 실패하면 None."""
    s = str(raw or "").strip()
    if len(s) != 14 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=_KST)
    except ValueError:
        return None


def safe_int(value, *, field: str = "", feed_id=None) -> int:
    """null/문자열/음수를 안전하게 0 이상의 정수로. 음수는 0으로 보정하고 경고."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    if n < 0:
        _log({"event": "negative_value", "level": "warning",
              "field": field, "feed_id": feed_id, "value": n})
        return 0
    return n


def extract_clip_urls(contents) -> list[str]:
    """본문에서 치지직 클립 URL을 중복 없이(등장 순서 유지) 뽑는다.

    contents는 JSON 문자열이며 document.components 안에 textNode.value / link.url /
    oglink.link 등 여러 위치에 URL이 들어간다. 배열 인덱스에 의존하지 않도록
    파싱된 구조를 재귀 순회하고, 파싱 자체가 실패하면 원문 문자열에서 정규식으로 훑는다.
    """
    if not contents:
        return []
    found: list[str] = []

    def push(u: str):
        for m in _CLIP_RE.findall(u):
            if m not in found:
                found.append(m)

    def walk(node):
        if isinstance(node, str):
            push(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    try:
        walk(json.loads(contents) if isinstance(contents, str) else contents)
    except (json.JSONDecodeError, TypeError, ValueError):
        # 본문 JSON이 깨져도 게시글 자체는 살린다 — 원문에서라도 URL을 건진다.
        _log({"event": "contents_parse_failed", "level": "warning"})
        if isinstance(contents, str):
            push(contents)
    return found


def parse_feed_item(item: dict) -> dict | None:
    """응답의 feeds[] 한 건을 내부 표현으로 정규화한다.

    이 게시글만의 문제(필드 누락/날짜 오류 등)면 None을 돌려주고 호출부는 건너뛴다.
    전체 응답 구조가 깨진 경우는 여기가 아니라 parse_feed_page에서 SchemaError로 처리한다.
    """
    if not isinstance(item, dict):
        return None
    feed = item.get("feed") or {}
    user = item.get("user") or {}
    buff = item.get("buff") or {}
    board = item.get("board") or {}
    link = item.get("feedLink") or {}
    comment = item.get("comment") or {}
    if not isinstance(feed, dict) or not isinstance(user, dict):
        return None

    feed_id = feed.get("feedId") or item.get("feedId")
    try:
        feed_id = int(feed_id)
    except (TypeError, ValueError):
        return None
    if feed_id <= 0:
        return None

    created = parse_created_date(feed.get("createdDate"))
    if created is None:
        _log({"event": "bad_created_date", "level": "warning",
              "feed_id": feed_id, "raw": str(feed.get("createdDate"))[:32]})
        return None
    updated = parse_created_date(feed.get("updatedDate"))

    # userIdHash가 없으면 닉네임으로 묶지 않는다 — 다른 사람이 합쳐지는 것이 더 나쁘다.
    author_hash = str(user.get("userIdHash") or "").strip()
    if not author_hash:
        author_hash = f"feed:{feed_id}"
        _log({"event": "missing_user_hash", "level": "warning", "feed_id": feed_id})

    clips = extract_clip_urls(feed.get("contents"))
    contents = feed.get("contents")
    return {
        "feed_id": feed_id,
        "title": normalize_title(feed.get("title")),
        "author_id_hash": author_hash,
        "author_nickname": str(user.get("nickname") or ""),
        "author_profile_image_url": str(user.get("profileImageUrl") or ""),
        "author_verified": 1 if user.get("verifiedMark") else 0,
        "created_at": int(created.timestamp()),
        "created_dt": created,
        "post_updated_at": int(updated.timestamp()) if updated else None,
        # 순위 기준은 바깥 buff.buffCount로 통일한다(feed.buff도 있지만 섞지 않는다)
        "buff_count": safe_int(buff.get("buffCount"), field="buffCount", feed_id=feed_id),
        "nerf_count": safe_int(buff.get("nerfCount"), field="nerfCount", feed_id=feed_id),
        "view_count": safe_int(item.get("readCount"), field="readCount", feed_id=feed_id),
        "comment_count": safe_int(comment.get("totalCount"), field="commentCount", feed_id=feed_id),
        "clip_url": clips[0] if clips else None,
        "clip_urls": "\n".join(clips),
        "post_url": str(link.get("pc") or ""),
        "mobile_post_url": str(link.get("mobile") or ""),
        "board_id": board.get("boardId"),
        "board_name": str(board.get("boardName") or ""),
        "lounge_id": str(feed.get("loungeId") or ""),
        "original_lounge_id": str(feed.get("originalLoungeId") or ""),
        "raw_contents": contents if isinstance(contents, str) else None,
        "hidden_by_clean_bot": 1 if feed.get("hideByCleanBot") else 0,
        "pinned": 1 if feed.get("pinned") else 0,
    }


def parse_feed_page(payload) -> list[dict]:
    """응답 한 페이지를 검증하고 feeds 배열을 돌려준다. 구조가 깨졌으면 SchemaError."""
    if not isinstance(payload, dict):
        raise SchemaError("최상위가 객체가 아님")
    if payload.get("code") != 200:
        raise SchemaError(f"code={payload.get('code')!r}")
    content = payload.get("content")
    if not isinstance(content, dict):
        raise SchemaError("content가 객체가 아님")
    feeds = content.get("feeds")
    if not isinstance(feeds, list):
        raise SchemaError("content.feeds가 배열이 아님")
    return feeds


def is_event_entry(parsed: dict, *, start: datetime, end: datetime) -> bool:
    """싱드컵 참가작 판별 — 말머리 + 게시판 + 기간(KST) + 클린봇 숨김 제외."""
    if not parsed or not is_singcup_title(parsed["title"]):
        return False
    if parsed["hidden_by_clean_bot"]:
        return False
    if parsed["board_id"] is not None and int(parsed["board_id"]) != BOARD_ID:
        return False
    return start <= parsed["created_dt"] <= end


def rank_entries(rows: list[dict]) -> list[dict]:
    """작성자별 대표작 1개만 남기고 순위를 매긴다.

    정렬: 버프 내림차순 → 조회수 내림차순 → 작성 시각 오름차순 → feedId 오름차순.
    같은 작성자가 여러 편을 올렸으면 합산하지 않고 '가장 잘 된 한 편'만 순위에 넣는다
    (같은 작성자가 버프가 동률인 글을 두 개 올린 사례가 실제로 있어 타이브레이커가 필요하다).
    """
    def key(r):
        return (-int(r["buff_count"]), -int(r["view_count"]),
                int(r["created_at"]), int(r["feed_id"]))

    best: dict[str, dict] = {}
    for r in sorted(rows, key=key):
        h = r["author_id_hash"]
        if h not in best:          # 정렬돼 있으므로 처음 만난 것이 그 작성자의 대표작
            best[h] = r
    ranked = sorted(best.values(), key=key)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return ranked


def event_status(now: datetime | None = None) -> str:
    now = now or datetime.now(_KST)
    if now < START_AT:
        return "UPCOMING"
    if now > END_AT:
        return "ENDED"
    return "LIVE"


# ── HTTP ────────────────────────────────────────────────────────────────────
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT,
                                    limits=httpx.Limits(max_connections=2))
    return _client


async def reset_state():
    """테스트/재초기화용."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(BACKOFF_MAX_SECONDS, max(0.0, float(retry_after.strip())))
        except ValueError:
            pass
    base = BACKOFF_BASE_SECONDS * (2 ** attempt)
    return min(BACKOFF_MAX_SECONDS, base) + random.uniform(0, BACKOFF_BASE_SECONDS or 0.1)


class FetchError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def fetch_page(client: httpx.AsyncClient, offset: int) -> list[dict]:
    """한 페이지를 가져와 feeds 배열을 돌려준다. 실패는 FetchError.

    재시도는 408/429/5xx/timeout만 한다. 400/401/403/404는 다시 눌러도 결과가 같고,
    특히 400은 파라미터/스펙 변경 신호라 반복 호출하면 안 된다.
    """
    params = {"offset": offset, "limit": PAGE_LIMIT, "order": "NEW",
              "boardId": BOARD_ID, "buffFilteringYN": "N"}
    for attempt in range(MAX_RETRIES):
        try:
            r = await client.get(FEED_API, params=params, headers=HEADERS,
                                 timeout=REQUEST_TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt + 1 >= MAX_RETRIES:
                raise FetchError(ST_FAILED, f"network: {type(e).__name__}")
            await asyncio.sleep(_retry_delay(attempt, None))
            continue

        code = r.status_code
        if code == 200:
            try:
                payload = r.json()
            except (json.JSONDecodeError, ValueError):
                raise SchemaError("응답이 JSON이 아님")
            return parse_feed_page(payload)

        if code == 400:
            # limit>30 등 파라미터 문제이거나 API 스펙이 바뀐 것 — 반복 재시도 금지
            raise FetchError(ST_SCHEMA, f"HTTP 400 (offset={offset}, limit={PAGE_LIMIT})")
        if code in (401, 403):
            raise FetchError(ST_BLOCKED, f"HTTP {code} — 접근이 거부되었습니다(IP/정책 확인 필요)")
        if code == 404:
            raise FetchError(ST_SCHEMA, "HTTP 404 — API 경로가 바뀌었을 수 있습니다")

        if code in (408, 429) or 500 <= code < 600:
            if attempt + 1 >= MAX_RETRIES:
                raise FetchError(ST_FAILED, f"HTTP {code}")
            await asyncio.sleep(_retry_delay(attempt, r.headers.get("Retry-After")))
            continue

        raise FetchError(ST_FAILED, f"HTTP {code}")

    raise FetchError(ST_FAILED, "재시도 소진")


# ── DB ──────────────────────────────────────────────────────────────────────
async def _upsert(entry: dict, now: int) -> bool:
    """upsert 하고 '새로 추가된 행인지'를 돌려준다(backfill 보고용).

    feed_id가 PK라 재실행해도 중복 insert가 생기지 않는다.
    """
    db = await get_db()
    exists = await (await db.execute(
        "SELECT 1 FROM singcup_feeds WHERE feed_id=?", (entry["feed_id"],)
    )).fetchone()
    await db.execute(
        """INSERT INTO singcup_feeds
               (feed_id, event_id, author_id_hash, author_nickname, author_profile_image_url,
                author_verified, title, created_at, post_updated_at, buff_count, nerf_count,
                view_count, comment_count, clip_url, clip_urls, post_url, mobile_post_url,
                board_id, board_name, lounge_id, original_lounge_id, raw_contents,
                hidden_by_clean_bot, pinned, active, missing_scan_count,
                first_collected_at, last_collected_at, row_updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?)
           ON CONFLICT(feed_id) DO UPDATE SET
               author_nickname          = excluded.author_nickname,
               author_profile_image_url = excluded.author_profile_image_url,
               author_verified          = excluded.author_verified,
               title                    = excluded.title,
               post_updated_at          = excluded.post_updated_at,
               buff_count               = excluded.buff_count,
               nerf_count               = excluded.nerf_count,
               view_count               = excluded.view_count,
               comment_count            = excluded.comment_count,
               -- 클립을 못 찾은 회차가 기존 값을 지우지 않게 COALESCE
               clip_url                 = COALESCE(excluded.clip_url, clip_url),
               clip_urls                = CASE WHEN excluded.clip_urls != ''
                                               THEN excluded.clip_urls ELSE clip_urls END,
               raw_contents             = COALESCE(excluded.raw_contents, raw_contents),
               hidden_by_clean_bot      = excluded.hidden_by_clean_bot,
               pinned                   = excluded.pinned,
               active                   = 1,
               missing_scan_count       = 0,
               last_collected_at        = excluded.last_collected_at,
               row_updated_at           = excluded.row_updated_at""",
        (entry["feed_id"], EVENT_ID, entry["author_id_hash"], entry["author_nickname"],
         entry["author_profile_image_url"], entry["author_verified"], entry["title"],
         entry["created_at"], entry["post_updated_at"], entry["buff_count"],
         entry["nerf_count"], entry["view_count"], entry["comment_count"],
         entry["clip_url"], entry["clip_urls"], entry["post_url"], entry["mobile_post_url"],
         entry["board_id"], entry["board_name"], entry["lounge_id"],
         entry["original_lounge_id"], entry["raw_contents"], entry["hidden_by_clean_bot"],
         entry["pinned"], now, now, now),
    )
    return exists is None


async def prune_out_of_range(*, dry_run: bool = True) -> dict:
    """이벤트 기간 밖으로 벗어난 행을 정리한다.

    이벤트 시작일을 바꾸면 예전 기준으로 저장된 행이 범위 밖이 될 수 있다.
    **실제로 지우지 않고 active=0으로만 내린다**(원본은 보존). 기본값이 dry_run=True라
    무엇이 대상인지 먼저 확인한 뒤 실행하게 되어 있다.
    이 함수는 singcup_feeds의 이 이벤트(event_id) 행만 건드린다 — 다른 테이블/이벤트는
    절대 수정하지 않는다.
    """
    start_ts = int(START_AT.timestamp())
    end_ts = int(END_AT.timestamp())
    db = await get_db()
    rows = await (await db.execute(
        "SELECT feed_id, title, created_at, author_nickname FROM singcup_feeds "
        "WHERE event_id=? AND active=1 AND (created_at < ? OR created_at > ?)",
        (EVENT_ID, start_ts, end_ts)
    )).fetchall()
    targets = [dict(r) for r in rows]

    if targets and not dry_run:
        qs = ",".join("?" for _ in targets)
        await db.execute(
            f"UPDATE singcup_feeds SET active=0, row_updated_at=? WHERE feed_id IN ({qs})",
            (int(time.time()), *[t["feed_id"] for t in targets]))
        await db.commit()

    _log({"event": "prune", "dry_run": dry_run, "count": len(targets),
          "event_id": EVENT_ID,
          "window": [START_AT.isoformat(), END_AT.isoformat()],
          "sample": [t["feed_id"] for t in targets[:10]]})
    return {"dryRun": dry_run, "count": len(targets),
            "window": {"startAt": START_AT.isoformat(), "endAt": END_AT.isoformat()},
            "targets": [
                {"feedId": t["feed_id"], "title": t["title"][:60],
                 "createdAt": datetime.fromtimestamp(t["created_at"], _KST).isoformat(),
                 "authorNickname": t["author_nickname"]}
                for t in targets[:50]
            ]}


async def _reconcile_missing(seen_ids: set[int], now: int) -> int:
    """전체 페이지 수집에 성공한 회차에서만 호출.

    이번에 안 보인 활성 게시글의 missing_scan_count를 올리고, 임계치를 넘으면 비활성화한다.
    원본 API의 일시적 누락으로 순위에서 사라지는 것을 막기 위한 2단계 처리다.
    """
    db = await get_db()
    rows = await (await db.execute(
        "SELECT feed_id FROM singcup_feeds WHERE event_id=? AND active=1", (EVENT_ID,)
    )).fetchall()
    missing = [int(r["feed_id"]) for r in rows if int(r["feed_id"]) not in seen_ids]
    if not missing:
        return 0
    qs = ",".join("?" for _ in missing)
    await db.execute(
        f"UPDATE singcup_feeds SET missing_scan_count = missing_scan_count + 1, "
        f"row_updated_at=? WHERE feed_id IN ({qs})", (now, *missing))
    await db.execute(
        f"UPDATE singcup_feeds SET active=0 WHERE feed_id IN ({qs}) AND missing_scan_count >= ?",
        (*missing, MISSING_SCANS_TO_DEACTIVATE))
    return len(missing)


async def _acquire_lock(ttl_seconds: int) -> str | None:
    """조건부 UPDATE로 분산 락을 잡는다. 성공하면 owner 토큰, 실패하면 None."""
    now = int(time.time())
    token = uuid.uuid4().hex[:12]
    db = await get_db()
    cur = await db.execute(
        "UPDATE singcup_collect_lock SET locked_until=?, owner=? "
        "WHERE id=1 AND locked_until < ?", (now + ttl_seconds, token, now))
    await db.commit()
    return token if cur.rowcount == 1 else None


async def _release_lock(token: str):
    db = await get_db()
    await db.execute(
        "UPDATE singcup_collect_lock SET locked_until=0, owner='' WHERE id=1 AND owner=?",
        (token,))
    await db.commit()


async def _record_run(started: int, *, ok: bool, full_scan: bool, pages: int,
                      feeds_seen: int, matched: int, status: str, note: str = ""):
    db = await get_db()
    await db.execute(
        "INSERT INTO singcup_collect_runs (event_id, started_at, finished_at, ok, full_scan, "
        "pages, feeds_seen, matched, status, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (EVENT_ID, started, int(time.time()), 1 if ok else 0, 1 if full_scan else 0,
         pages, feeds_seen, matched, status, note[:300]))
    await db.commit()


# ── 수집 ────────────────────────────────────────────────────────────────────
MODES = ("normal", "backfill", "dry-run")


async def collect_once(*, force: bool = False, mode: str = "normal") -> dict:
    """한 회차 수집. 락을 못 잡으면 아무것도 하지 않고 SKIPPED를 돌려준다.

    mode:
      normal   — 정기 수집. offset 0부터 이벤트 시작일 이전 페이지가 나올 때까지 순회.
      backfill — 같은 순회지만 페이지 상한을 BACKFILL_MAX_PAGES로 올린다. 이벤트 시작일을
                 앞당긴 뒤 과거 구간을 한 번에 채울 때 쓴다(upsert라 재실행해도 안전).
      dry-run  — 순회와 판별만 하고 **DB에 아무것도 쓰지 않는다**. 몇 건이 잡히는지
                 먼저 확인할 때 쓴다.

    세 모드 모두 순회 로직은 동일하다 — 최적화를 이유로 일부 페이지를 건너뛰지 않으므로
    이벤트 기간 내 게시글의 버프/조회수 갱신이 누락되지 않는다.
    """
    mode = mode if mode in MODES else "normal"
    dry_run = mode == "dry-run"
    page_cap = BACKFILL_MAX_PAGES if mode == "backfill" else MAX_PAGES

    started = int(time.time())
    token = await _acquire_lock(MAX_RUN_SECONDS)
    if token is None:
        _log({"event": "skip", "reason": "lock_held", "mode": mode})
        return {"status": ST_SKIPPED, "mode": mode, "note": "다른 수집 작업이 실행 중입니다."}

    seen_ids: set[int] = set()
    pages = 0
    matched = 0
    inserted = 0
    full_scan = False
    repeat_pages = 0
    status = ST_OK
    note = ""
    client = _get_client()
    deadline = time.monotonic() + MAX_RUN_SECONDS

    try:
        for offset in range(page_cap):
            if time.monotonic() > deadline:
                note = "최대 실행 시간 초과"
                status = ST_FAILED
                break
            feeds = await fetch_page(client, offset)
            pages += 1
            if not feeds:
                full_scan = True
                break

            new_on_page = 0
            page_dts: list[datetime] = []
            for item in feeds:
                try:
                    parsed = parse_feed_item(item)
                except Exception as e:       # 한 건의 오류가 전체를 멈추지 않게
                    _log({"event": "item_error", "level": "warning", "detail": str(e)[:200]})
                    continue
                if parsed is None:
                    continue
                if parsed["feed_id"] not in seen_ids:
                    seen_ids.add(parsed["feed_id"])
                    new_on_page += 1
                page_dts.append(parsed["created_dt"])
                if is_event_entry(parsed, start=START_AT, end=END_AT):
                    matched += 1
                    if not dry_run:
                        if await _upsert(parsed, started):
                            inserted += 1

            # 같은 페이지가 반복되면(=커서가 안 움직이면) 무한 루프이므로 중단
            if new_on_page == 0:
                repeat_pages += 1
                if repeat_pages >= 2:
                    note = "동일 페이지 반복 감지"
                    status = ST_FAILED
                    _log({"event": "loop_detected", "level": "warning", "offset": offset})
                    break
            else:
                repeat_pages = 0

            # order=NEW라 최신순이다 — 페이지 전체가 이벤트 시작 이전이면 더 볼 것이 없다
            if page_dts and all(d < START_AT for d in page_dts):
                full_scan = True
                break

            await asyncio.sleep(PAGE_DELAY_SECONDS)
        else:
            note = f"최대 페이지({page_cap}) 도달 — 이벤트 구간을 다 확인하지 못했습니다."
            _log({"event": "max_pages", "level": "warning", "pages": pages, "mode": mode})

        deactivated = 0
        if not dry_run:
            await (await get_db()).commit()
            if full_scan and status == ST_OK:
                deactivated = await _reconcile_missing(seen_ids, started)
                await (await get_db()).commit()

        ok = status == ST_OK
        if not dry_run:      # dry-run은 이력도 남기지 않는다(순수 조회)
            await _record_run(started, ok=ok, full_scan=full_scan, pages=pages,
                              feeds_seen=len(seen_ids), matched=matched,
                              status=status, note=(f"[{mode}] " + note).strip())
        _log({"event": "run", "mode": mode, "status": status, "pages": pages,
              "feeds": len(seen_ids), "matched": matched, "inserted": inserted,
              "full_scan": full_scan, "missing": deactivated,
              "duration_ms": round((time.time() - started) * 1000)})
        return {"status": status, "mode": mode, "pages": pages,
                "feeds_seen": len(seen_ids), "matched": matched,
                "inserted": inserted, "full_scan": full_scan,
                "deactivated": deactivated, "note": note}

    except SchemaError as e:
        # 스키마가 깨지면 '데이터 없음'이 아니라 실패다 — 기존 DB는 그대로 둔다.
        await _record_run(started, ok=False, full_scan=False, pages=pages,
                          feeds_seen=len(seen_ids), matched=matched,
                          status=ST_SCHEMA, note=str(e))
        _log({"event": "run_failed", "level": "warning",
              "status": ST_SCHEMA, "detail": str(e)[:200]})
        return {"status": ST_SCHEMA, "note": str(e)}
    except FetchError as e:
        await _record_run(started, ok=False, full_scan=False, pages=pages,
                          feeds_seen=len(seen_ids), matched=matched,
                          status=e.status, note=e.detail)
        _log({"event": "run_failed", "level": "warning", "status": e.status, "detail": e.detail})
        return {"status": e.status, "note": e.detail}
    except Exception as e:
        await _record_run(started, ok=False, full_scan=False, pages=pages,
                          feeds_seen=len(seen_ids), matched=matched,
                          status=ST_FAILED, note=str(e)[:200])
        _log({"event": "run_failed", "level": "warning", "detail": str(e)[:200]})
        return {"status": ST_FAILED, "note": str(e)[:200]}
    finally:
        await _release_lock(token)
        _ = force  # force는 스케줄 게이트를 건너뛰는 용도로 호출부에서 쓴다


# ── 조회 ────────────────────────────────────────────────────────────────────
async def load_rankings(limit: int = 200) -> dict:
    """순위 + 요약 + 수집 상태. 수집이 실패해도 DB의 마지막 정상 데이터를 그대로 준다."""
    db = await get_db()
    rows = [dict(r) for r in await (await db.execute(
        "SELECT * FROM singcup_feeds WHERE event_id=? AND active=1", (EVENT_ID,)
    )).fetchall()]
    ranked = rank_entries(rows)

    last_ok = await (await db.execute(
        "SELECT started_at, finished_at, full_scan FROM singcup_collect_runs "
        "WHERE event_id=? AND ok=1 ORDER BY started_at DESC LIMIT 1", (EVENT_ID,)
    )).fetchone()
    last_any = await (await db.execute(
        "SELECT started_at, finished_at, status, note FROM singcup_collect_runs "
        "WHERE event_id=? ORDER BY started_at DESC LIMIT 1", (EVENT_ID,)
    )).fetchone()

    last_ok_at = int(last_ok["finished_at"] or last_ok["started_at"]) if last_ok else None
    stale = (last_ok_at is None or
             time.time() - last_ok_at > STALE_AFTER_MINUTES * 60)

    def iso(ts):
        return datetime.fromtimestamp(ts, _KST).isoformat() if ts else None

    return {
        "event": {
            "id": EVENT_ID, "name": EVENT_NAME,
            "startAt": START_AT.isoformat(), "endAt": END_AT.isoformat(),
            "status": event_status(),
        },
        "summary": {
            "submissionCount": len(rows),
            "participantCount": len(ranked),
            "totalBuffCount": sum(int(r["buff_count"]) for r in rows),
            "topNickname": ranked[0]["author_nickname"] if ranked else None,
        },
        "collector": {
            "lastSuccessAt": iso(last_ok_at),
            "lastAttemptAt": iso(int(last_any["started_at"])) if last_any else None,
            "status": (last_any["status"] if last_any else "OK"),
            "stale": bool(stale),
            "staleAfterMinutes": STALE_AFTER_MINUTES,
        },
        "rankings": [
            {
                "rank": r["rank"], "feedId": r["feed_id"],
                "authorIdHash": r["author_id_hash"],
                "authorNickname": r["author_nickname"],
                "authorProfileImageUrl": r["author_profile_image_url"],
                "authorVerified": bool(r["author_verified"]),
                "title": r["title"],
                "buffCount": r["buff_count"], "nerfCount": r["nerf_count"],
                "viewCount": r["view_count"], "commentCount": r["comment_count"],
                "createdAt": iso(r["created_at"]),
                "clipUrl": r["clip_url"], "postUrl": r["post_url"],
                "mobilePostUrl": r["mobile_post_url"],
            }
            for r in ranked[:max(1, min(500, limit))]
        ],
    }


async def load_status() -> dict:
    """운영 진단용 — 최근 수집 회차 이력. Railway에서 네이버 접근 가능 여부 확인에 쓴다."""
    db = await get_db()
    runs = await (await db.execute(
        "SELECT started_at, finished_at, ok, full_scan, pages, feeds_seen, matched, "
        "status, note FROM singcup_collect_runs WHERE event_id=? "
        "ORDER BY started_at DESC LIMIT 10", (EVENT_ID,)
    )).fetchall()
    total = await (await db.execute(
        "SELECT COUNT(*) AS c FROM singcup_feeds WHERE event_id=? AND active=1", (EVENT_ID,)
    )).fetchone()
    return {
        "enabled": ENABLED,
        "eventId": EVENT_ID,
        "eventStatus": event_status(),
        "startAt": START_AT.isoformat(),
        "endAt": END_AT.isoformat(),
        "intervalMinutes": COLLECT_INTERVAL_MINUTES,
        "maxPages": MAX_PAGES,
        "activeFeeds": total["c"] if total else 0,
        "recentRuns": [dict(r) for r in runs],
    }


# ── 스케줄러 ────────────────────────────────────────────────────────────────
def _should_collect_now() -> tuple[bool, float]:
    """(수집할지, 다음 대기 분)."""
    now = datetime.now(_KST)
    if not ENABLED:
        return (False, 60.0)
    if now < START_AT:
        # 시작 전 — 시작까지 남은 시간만큼(최대 30분) 자고 다시 본다
        wait = min(30.0, max(1.0, (START_AT - now).total_seconds() / 60))
        return (False, wait)
    if now <= END_AT:
        return (True, COLLECT_INTERVAL_MINUTES)
    # 종료 후 — 일정 시간 동안만 낮은 빈도로 최종 검산하고 그 뒤엔 멈춘다
    if (now - END_AT).total_seconds() <= POST_EVENT_HOURS * 3600:
        return (True, POST_EVENT_INTERVAL_MINUTES)
    return (False, 360.0)


async def start_singcup_collector():
    """main.py의 lifespan에서 create_task로 띄운다. 실패해도 프로세스를 죽이지 않는다."""
    if not ENABLED:
        _log({"event": "disabled"})
        return
    await asyncio.sleep(float(os.getenv("SINGCUP_START_DELAY_SECONDS", "20")))
    while True:
        run, wait_minutes = _should_collect_now()
        if run:
            try:
                await collect_once()
            except Exception as e:
                _log({"event": "loop_error", "level": "warning", "detail": str(e)[:200]})
        await asyncio.sleep(max(30.0, wait_minutes * 60))
