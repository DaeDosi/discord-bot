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
import json
import os
import random
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

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
    _acquire_lock,
    _release_lock,
    event_status,
)

from database import get_db

CLIPS_API = "https://api.chzzk.naver.com/service/v1/categories/ETC/music/clips"
CARD_API = "https://api-videohub.naver.com/shortformhub/feeds/v5/card"
CHANNEL_API = "https://api.chzzk.naver.com/service/v1/channels"

_KST = timezone(timedelta(hours=9))

PAGE_SIZE = int(os.getenv("SINGCUP_CLIP_PAGE_SIZE", "50"))
MAX_PAGES = int(os.getenv("SINGCUP_CLIP_MAX_PAGES", "60"))
CARD_CONCURRENCY = int(os.getenv("SINGCUP_CARD_CONCURRENCY", "4"))
REQUEST_TIMEOUT = float(os.getenv("SINGCUP_REQUEST_TIMEOUT_MS", "10000")) / 1000
MAX_RETRIES = max(1, int(os.getenv("SINGCUP_MAX_RETRIES", "3")))
BACKOFF_BASE = float(os.getenv("SINGCUP_BACKOFF_BASE_SECONDS", "1"))
BACKOFF_MAX = float(os.getenv("SINGCUP_BACKOFF_MAX_SECONDS", "30"))
PAGE_DELAY = float(os.getenv("SINGCUP_PAGE_DELAY_SECONDS", "0.3"))
# 채널 정보(팔로워)는 자주 안 변한다 — 채널당 이 주기로만 다시 부른다
CHANNEL_TTL_MINUTES = float(os.getenv("SINGCUP_CHANNEL_TTL_MINUTES", "20"))
MAX_RUN_SECONDS = int(os.getenv("SINGCUP_CLIP_MAX_RUN_SECONDS", "600"))
MISSING_SCANS_TO_DEACTIVATE = int(os.getenv("SINGCUP_MISSING_SCANS", "2"))

# 정확히 '#싱드컵' 태그만 인정한다. 제목/본문에 '싱드컵'이라는 단어만 있는 건 제외.
_TAG_RE = re.compile(r"(^|\s)#싱드컵(?=\s|$)")
# 재생 불가 상태 — 목록/카드에서 제외한다
_BAD_BLIND = {"BLIND", "DELETE", "DELETED", "PRIVATE"}

_HEADERS = {"User-Agent": os.getenv("SINGCUP_USER_AGENT", "NexBot-SingcupCollector/1.0"),
            "Accept": "application/json"}


def _log(payload: dict):
    print(f"[singcup_clips] {json.dumps(payload, ensure_ascii=False, default=str)}", flush=True)


# ── 순수 함수 (테스트 대상) ─────────────────────────────────────────────────
def has_singcup_tag(description) -> bool:
    """`#싱드컵` 해시태그가 실제로 있는지. 유니코드 정규화 후 검사한다."""
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
            return (safe_count(r.get("count")), True)
    # like 리액션 자체가 없는 경우는 '하트 0'으로 본다(구조는 정상)
    return (0, True)


def extract_view(card: dict) -> tuple[int, bool]:
    content = card.get("content")
    if not isinstance(content, dict):
        return (0, False)
    vod = content.get("vod")
    if not isinstance(vod, dict) or "count" not in vod:
        return (0, False)
    return (safe_count(vod.get("count")), True)


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


def pick_representative(clips: list[dict]) -> dict | None:
    """스트리머의 대표 클립 — 하트↓ → 조회수↓ → 생성 시각↑ → clipUID↑."""
    if not clips:
        return None
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
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None
    _channel_cache.clear()


class FetchError(RuntimeError):
    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(BACKOFF_MAX, max(0.0, float(retry_after.strip())))
        except ValueError:
            pass
    return min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt)) + random.uniform(0, BACKOFF_BASE or 0.1)


async def _get_json(client, url, *, params=None, headers=None, what="request"):
    """408/429/5xx/timeout만 재시도. 400/401/403/404는 즉시 실패."""
    for attempt in range(MAX_RETRIES):
        try:
            r = await client.get(url, params=params, headers=headers or _HEADERS,
                                 timeout=REQUEST_TIMEOUT)
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
            if attempt + 1 >= MAX_RETRIES:
                raise FetchError(ST_FAILED, f"{what}: HTTP {code}")
            await asyncio.sleep(_retry_delay(attempt, r.headers.get("Retry-After")))
            continue
        raise FetchError(ST_FAILED, f"{what}: HTTP {code}")
    raise FetchError(ST_FAILED, f"{what}: 재시도 소진")


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


async def fetch_card(client, item: dict) -> dict | None:
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
                                  headers=headers, what=f"card({clip_uid})")
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
    if not heart_ok or not view_ok:
        # 실제 0과 '못 읽음'을 구분해 남긴다
        _log({"event": "card_metrics_missing", "level": "warning", "clip_uid": clip_uid,
              "heart_ok": heart_ok, "view_ok": view_ok})
    return {"description": extract_description(card), "heart_count": heart,
            "view_count": view, "metrics_ok": bool(heart_ok and view_ok)}


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
               (clip_uid, event_id, owner_channel_id, video_id, clip_title,
                thumbnail_image_url, description, created_at, heart_count, view_count,
                duration, adult, blind_type, metrics_ok, active, missing_scan_count,
                first_collected_at, last_collected_at, row_updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0,?,?,?)
           ON CONFLICT(clip_uid) DO UPDATE SET
               clip_title          = excluded.clip_title,
               thumbnail_image_url = excluded.thumbnail_image_url,
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
        (c["clip_uid"], EVENT_ID, c["owner_channel_id"], c["video_id"], c["clip_title"],
         c["thumbnail_image_url"], c["description"], c["created_at"], c["heart_count"],
         c["view_count"], c["duration"], c["adult"], c["blind_type"],
         1 if c["metrics_ok"] else 0, now, now, now))
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


async def _save_snapshots(ranked: list[dict], now: int):
    db = await get_db()
    await db.executemany(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id, heart_count,"
        " view_count, follower_count, score, rank, collected_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [(EVENT_ID, r["clip_uid"], r["owner_channel_id"], r["heart_count"], r["view_count"],
          r.get("follower_count", 0), r["score"], r["rank"], now) for r in ranked])


async def _reconcile_missing_clips(seen: set, now: int) -> int:
    """전체 순회에 성공한 회차에서만 호출. 연속 2회 안 보이면 비활성(삭제 아님)."""
    db = await get_db()
    rows = await (await db.execute(
        "SELECT clip_uid FROM singcup_clips WHERE event_id=? AND active=1", (EVENT_ID,)
    )).fetchall()
    missing = [r["clip_uid"] for r in rows if r["clip_uid"] not in seen]
    if not missing:
        return 0
    qs = ",".join("?" for _ in missing)
    await db.execute(
        "UPDATE singcup_clips SET missing_scan_count=missing_scan_count+1, row_updated_at=? "
        f"WHERE clip_uid IN ({qs})", (now, *missing))
    await db.execute(
        f"UPDATE singcup_clips SET active=0 WHERE clip_uid IN ({qs}) "
        "AND missing_scan_count >= ?", (*missing, MISSING_SCANS_TO_DEACTIVATE))
    return len(missing)


def _build_reps(tagged: list[dict]) -> list[dict]:
    """스트리머(ownerChannelId)별 대표 클립 1개만 남긴다."""
    by_owner: dict[str, list[dict]] = {}
    for c in tagged:
        by_owner.setdefault(c["owner_channel_id"], []).append(c)
    reps = []
    for clips in by_owner.values():
        rep = dict(pick_representative(clips))
        rep["tagged_clip_count"] = len(clips)
        reps.append(rep)
    return reps


# ── 수집 ────────────────────────────────────────────────────────────────────
async def collect_clips_once(*, dry_run: bool = False, max_pages: int | None = None) -> dict:
    """클립 목록을 커서로 순회하며 #싱드컵 태그 클립을 모으고 점수를 다시 계산한다."""
    started = int(time.time())
    token = await _acquire_lock(MAX_RUN_SECONDS)
    if token is None:
        return {"status": ST_SKIPPED, "note": "다른 수집 작업이 실행 중입니다."}

    cap = max_pages or MAX_PAGES
    client = _get_client()
    deadline = time.monotonic() + MAX_RUN_SECONDS
    seen_clips: set = set()
    seen_cursors: set = set()
    scanned = 0
    candidates: list[dict] = []
    pages = 0
    full_scan = False
    status = ST_OK
    note = ""

    try:
        cursor = None
        for _ in range(cap):
            if time.monotonic() > deadline:
                status, note = ST_FAILED, "최대 실행 시간 초과"
                break
            items, nxt = await fetch_clip_page(client, cursor)
            pages += 1
            if not items:
                full_scan = True
                break
            scanned += len(items)

            page_dates: list = []
            new_on_page = 0
            for it in items:
                uid = str(it.get("clipUID") or "")
                if not uid or uid in seen_clips:
                    continue
                seen_clips.add(uid)
                new_on_page += 1
                d = parse_clip_date(it.get("createdDate"))
                if d:
                    page_dates.append(d)
                if is_candidate_clip(it, start=START_AT, end=END_AT):
                    candidates.append(it)

            if nxt is None:
                full_scan = True
                break
            # 커서가 안 움직이거나 새 클립이 하나도 없으면 무한 루프다
            if nxt in seen_cursors or new_on_page == 0:
                status, note = ST_FAILED, "동일 커서/페이지 반복 감지"
                _log({"event": "loop_detected", "level": "warning", "cursor": nxt})
                break
            seen_cursors.add(nxt)
            cursor = nxt

            # RECENT 정렬이라 페이지 전체가 시작 이전이면 더 볼 것이 없다
            if page_dates and all(d < START_AT for d in page_dates):
                full_scan = True
                break
            await asyncio.sleep(PAGE_DELAY)
        else:
            note = f"최대 페이지({cap}) 도달"
            _log({"event": "max_pages", "level": "warning", "pages": pages})

        # ── 카드 조회로 태그/하트/조회수 확인(동시성 제한) ──────────────────
        sem = asyncio.Semaphore(max(1, CARD_CONCURRENCY))
        tagged: list[dict] = []

        async def one(it):
            async with sem:
                card = await fetch_card(client, it)
            if card is None or not has_singcup_tag(card["description"]):
                return
            d = parse_clip_date(it.get("createdDate"))
            tagged.append({
                "clip_uid": str(it["clipUID"]),
                "owner_channel_id": str(it["ownerChannelId"]),
                "video_id": str(it.get("videoId") or ""),
                "clip_title": str(it.get("clipTitle") or ""),
                "thumbnail_image_url": str(it.get("thumbnailImageUrl") or ""),
                "description": card["description"],
                "created_at": int(d.timestamp()),
                "heart_count": card["heart_count"],
                "view_count": card["view_count"],
                "duration": safe_count(it.get("duration")),
                "adult": 1 if it.get("adult") else 0,
                "blind_type": str(it.get("blindType") or ""),
                "metrics_ok": card["metrics_ok"],
            })

        await asyncio.gather(*[one(it) for it in candidates])

        if dry_run:
            ranked = compute_scores(_build_reps(tagged))
            return {"status": status, "dryRun": True, "pages": pages, "scanned": scanned,
                    "tagged": len(tagged), "streamers": len(ranked), "note": note}

        inserted = 0
        for c in tagged:
            if await _upsert_clip(c, started):
                inserted += 1
        await (await get_db()).commit()

        deactivated = 0
        if full_scan and status == ST_OK:
            deactivated = await _reconcile_missing_clips(
                {c["clip_uid"] for c in tagged}, started)
            await (await get_db()).commit()

        ranked = await recompute_ranking(started, client=client)

        _log({"event": "run", "status": status, "pages": pages, "scanned": scanned,
              "tagged": len(tagged), "inserted": inserted, "streamers": len(ranked),
              "deactivated": deactivated, "full_scan": full_scan,
              "duration_ms": round((time.time() - started) * 1000)})
        return {"status": status, "pages": pages, "scanned": scanned, "tagged": len(tagged),
                "inserted": inserted, "streamers": len(ranked), "full_scan": full_scan,
                "note": note}

    except SchemaError as e:
        _log({"event": "run_failed", "level": "warning", "status": ST_SCHEMA,
              "detail": str(e)[:200]})
        return {"status": ST_SCHEMA, "note": str(e)}
    except FetchError as e:
        _log({"event": "run_failed", "level": "warning", "status": e.status,
              "detail": e.detail})
        return {"status": e.status, "note": e.detail}
    finally:
        await _release_lock(token)


async def recompute_ranking(now: int, *, client=None) -> list[dict]:
    """DB의 활성 클립으로 대표 클립·점수·순위를 다시 계산하고 스냅샷을 남긴다."""
    db = await get_db()
    rows = [dict(r) for r in await (await db.execute(
        "SELECT * FROM singcup_clips WHERE event_id=? AND active=1", (EVENT_ID,)
    )).fetchall()]
    ranked = compute_scores(_build_reps(rows))

    client = client or _get_client()
    for r in ranked:
        info = await fetch_channel(client, r["owner_channel_id"]) or {}
        r["follower_count"] = info.get("follower_count", 0)
        await _upsert_streamer({
            "channel_id": r["owner_channel_id"],
            "channel_name": info.get("channel_name", ""),
            "channel_image_url": info.get("channel_image_url", ""),
            "follower_count": info.get("follower_count", 0),
            "verified_mark": info.get("verified_mark", 0),
            "representative_clip_uid": r["clip_uid"],
            "tagged_clip_count": r["tagged_clip_count"],
            "last_channel_updated_at": now if info else 0,
        }, now)
    await _save_snapshots(ranked, now)
    await db.commit()
    return ranked


def event_meta() -> dict:
    return {"id": EVENT_ID, "startAt": START_AT.isoformat(),
            "endAt": END_AT.isoformat(), "status": event_status()}
