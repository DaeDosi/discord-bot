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
import uuid
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
    event_status,
)

from database import get_db

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


# (예전의 collect_clips_once는 백필 워커(run_backfill)로 대체됐다 —
#  과거 적재와 신규 탐색을 한 작업으로 처리하던 구조를 분리했다.)

async def recompute_ranking(now: int, *, client=None) -> list[dict]:
    """DB의 활성 클립으로 대표 클립·점수·순위를 다시 계산하고 스냅샷을 남긴다."""
    db = await get_db()
    rows = [dict(r) for r in await (await db.execute(
        "SELECT * FROM singcup_clips WHERE event_id=? AND active=1", (EVENT_ID,)
    )).fetchall()]
    ranked = compute_scores(_build_reps(rows))

    client = client or _get_client()

    # 팔로워만 채널 API가 필요하다. 참가자가 수백 명이라 순차로 부르면 루프가 몇 분씩
    # 멈추므로 동시성을 제한해 병렬로 부른다(캐시에 있으면 요청이 나가지 않는다).
    sem = asyncio.Semaphore(max(1, CARD_CONCURRENCY))
    infos: dict[str, dict] = {}

    async def load_channel(cid: str):
        async with sem:
            infos[cid] = await fetch_channel(client, cid) or {}

    await asyncio.gather(*[load_channel(r["owner_channel_id"]) for r in ranked])

    for r in ranked:
        info = infos.get(r["owner_channel_id"]) or {}
        # 닉네임·이미지는 목록 응답(ownerChannel)을 우선한다 — 채널 API가 실패해도
        # 이름이 비지 않아야 한다. 비면 화면에 '-'로 뜨고 검색에도 걸리지 않는다.
        name = r.get("owner_channel_name") or info.get("channel_name", "")
        image = r.get("owner_channel_image_url") or info.get("channel_image_url", "")
        r["follower_count"] = info.get("follower_count", 0)
        await _upsert_streamer({
            "channel_id": r["owner_channel_id"],
            "channel_name": name,
            "channel_image_url": image,
            "follower_count": info.get("follower_count", 0),
            "verified_mark": r.get("owner_verified") or info.get("verified_mark", 0),
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
REP_METRICS_TTL_MINUTES = float(os.getenv("SINGCUP_REP_METRICS_TTL_MINUTES", "5"))
METRICS_TTL_MINUTES = float(os.getenv("SINGCUP_METRICS_TTL_MINUTES", "45"))
REFRESH_PER_CYCLE = int(os.getenv("SINGCUP_REFRESH_PER_CYCLE", "80"))
RESCAN_UNTAGGED_HOURS = float(os.getenv("SINGCUP_RESCAN_UNTAGGED_HOURS", "24"))
RETRY_MAX_ATTEMPTS = int(os.getenv("SINGCUP_RETRY_MAX_ATTEMPTS", "3"))

BF_IDLE, BF_RUNNING, BF_PAUSED, BF_DONE, BF_FAILED = (
    "idle", "running", "paused", "completed", "failed")


# ── 이름 있는 분산 락 ───────────────────────────────────────────────────────
async def acquire_named_lock(name: str, ttl: int) -> str | None:
    """조건부 UPDATE의 rowcount로 획득을 판정한다(check-then-set 경합 방지)."""
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


# ── scan / retry 상태 ───────────────────────────────────────────────────────
async def _scanned_uids() -> set[str]:
    db = await get_db()
    rows = await (await db.execute("SELECT clip_uid FROM singcup_clip_scan")).fetchall()
    return {r["clip_uid"] for r in rows}


async def _scan_state_of(uids: list[str]) -> dict[str, tuple[int, int]]:
    if not uids:
        return {}
    db = await get_db()
    qs = ",".join("?" for _ in uids)
    rows = await (await db.execute(
        f"SELECT clip_uid, tagged, checked_at FROM singcup_clip_scan WHERE clip_uid IN ({qs})",
        tuple(uids))).fetchall()
    return {r["clip_uid"]: (int(r["tagged"]), int(r["checked_at"])) for r in rows}


async def _record_scan(clip_uid: str, tagged: bool, now: int):
    db = await get_db()
    await db.execute(
        "INSERT INTO singcup_clip_scan (clip_uid, tagged, checked_at) VALUES (?,?,?) "
        "ON CONFLICT(clip_uid) DO UPDATE SET tagged=excluded.tagged, "
        "checked_at=excluded.checked_at", (clip_uid, 1 if tagged else 0, now))


async def _apply_metrics(clip_uid: str, heart: int, view: int, ok: bool, now: int):
    """카드에서 읽은 수치를 반영한다. 못 읽었으면(ok=False) 기존 값을 건드리지 않는다."""
    db = await get_db()
    if ok:
        await db.execute(
            "UPDATE singcup_clips SET heart_count=?, view_count=?, metrics_ok=1, "
            "last_metrics_at=?, last_collected_at=?, row_updated_at=? WHERE clip_uid=?",
            (heart, view, now, now, now, clip_uid))
    else:
        # 실패한 회차도 last_metrics_at은 올려 같은 클립만 계속 재시도하지 않게 한다
        await db.execute(
            "UPDATE singcup_clips SET metrics_ok=0, last_metrics_at=?, "
            "last_collected_at=?, row_updated_at=? WHERE clip_uid=?",
            (now, now, now, clip_uid))


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
    """후보 묶음을 카드 조회해 저장한다. (태그된 수, 신규 저장 수, 실패 수)."""
    sem = asyncio.Semaphore(max(1, CARD_CONCURRENCY))
    results: list[tuple[dict, dict | None]] = []

    async def one(it):
        async with sem:
            card = await fetch_card(client, it)
        results.append((it, card))

    await asyncio.gather(*[one(it) for it in items])

    tagged = inserted = failed = 0
    for it, card in results:
        uid = str(it["clipUID"])
        if card is None:
            failed += 1
            await _queue_retry(it, "card fetch failed", now)
            continue
        await _clear_retry(uid)
        is_tag = has_singcup_tag(card["description"])
        await _record_scan(uid, is_tag, now)
        if not is_tag:
            continue
        tagged += 1
        row = _to_clip_row(it, card)
        if await _upsert_clip(row, now):
            inserted += 1
        await _apply_metrics(uid, row["heart_count"], row["view_count"],
                             row["metrics_ok"], now)
    await (await get_db()).commit()
    return tagged, inserted, failed


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
            new_here = 0
            for it in items:
                uid = str(it.get("clipUID") or "")
                if not uid or uid in state:
                    continue
                if is_candidate_clip(it, start=START_AT, end=END_AT):
                    fresh.append(it)
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
async def _metrics_due(now: int, limit: int) -> list[dict]:
    """갱신 대상 — 대표 클립을 먼저, 그다음 하트가 많은 것, 오래된 것 순.

    대표 클립은 순위를 직접 좌우하므로 짧은 주기(REP_METRICS_TTL)로,
    나머지는 긴 주기(METRICS_TTL)로 돌린다.
    """
    db = await get_db()
    rows = await (await db.execute(
        """SELECT c.clip_uid, c.video_id, c.rec_id,
                  (s.representative_clip_uid IS NOT NULL) AS is_rep
           FROM singcup_clips c
           LEFT JOIN singcup_streamers s ON s.representative_clip_uid = c.clip_uid
           WHERE c.event_id=? AND c.active=1
             AND c.last_metrics_at < (CASE WHEN s.representative_clip_uid IS NOT NULL
                                           THEN ? ELSE ? END)
           ORDER BY is_rep DESC, c.heart_count DESC, c.last_metrics_at ASC
           LIMIT ?""",
        (EVENT_ID, now - int(REP_METRICS_TTL_MINUTES * 60),
         now - int(METRICS_TTL_MINUTES * 60), max(0, limit))
    )).fetchall()
    return [dict(r) for r in rows]


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
        ok = fail = 0

        async def one(r):
            nonlocal ok, fail
            item = {"clipUID": r["clip_uid"], "videoId": r["video_id"],
                    "recId": r["rec_id"] or "{}"}
            async with sem:
                card = await fetch_card(client, item)
            if card is None:
                fail += 1
                await _apply_metrics(r["clip_uid"], 0, 0, False, now)
                return
            await _apply_metrics(r["clip_uid"], card["heart_count"],
                                 card["view_count"], card["metrics_ok"], now)
            ok += 1

        await asyncio.gather(*[one(r) for r in due])
        await (await get_db()).commit()
        await recompute_ranking(now, client=client)
        _log({"event": "refresh_metrics", "due": len(due), "ok": ok, "failed": fail})
        return {"status": ST_OK, "refreshed": ok, "failed": fail, "due": len(due)}
    except (FetchError, SchemaError) as e:
        _log({"event": "refresh_failed", "level": "warning", "detail": str(e)[:200]})
        return {"status": ST_FAILED, "note": str(e)[:200]}
    finally:
        await release_named_lock("singcup_metrics", token)


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
async def _delta_maps(now: int) -> tuple[dict, dict]:
    """(1시간 전 스냅샷, 24시간 전 스냅샷) — owner_channel_id 기준.

    예전에는 '직전 수집 회차'와 비교했는데, 회차 간격이 4분이라 변화량이 대부분 0이고
    기준 시점도 들쭉날쭉했다(갱신 대상이 없는 회차는 스냅샷을 남기지 않는다).
    24시간과 같은 방식으로 '1시간 전 이하의 마지막 스냅샷'을 기준으로 잡는다.
    """
    db = await get_db()
    prev: dict = {}
    for r in await (await db.execute(
        "SELECT owner_channel_id, heart_count, rank FROM singcup_snapshots s "
        "WHERE event_id=? AND collected_at = (SELECT MAX(collected_at) "
        "FROM singcup_snapshots WHERE event_id=s.event_id "
        "AND owner_channel_id=s.owner_channel_id AND collected_at <= ?) "
        "GROUP BY owner_channel_id", (EVENT_ID, now - DELTA_WINDOW_SECONDS))).fetchall():
        prev[r["owner_channel_id"]] = (int(r["heart_count"]), int(r["rank"]))

    day: dict = {}
    for r in await (await db.execute(
        "SELECT owner_channel_id, heart_count FROM singcup_snapshots s WHERE event_id=? "
        "AND collected_at = (SELECT MAX(collected_at) FROM singcup_snapshots "
        "WHERE event_id=s.event_id AND owner_channel_id=s.owner_channel_id "
        "AND collected_at <= ?) GROUP BY owner_channel_id",
        (EVENT_ID, now - 86400))).fetchall():
        day[r["owner_channel_id"]] = int(r["heart_count"])
    return prev, day


async def load_main(limit: int = 200) -> dict:
    """메인/랭킹 공용 데이터 — 스트리머별 대표 클립 + 점수 + 변화량 + 현재 라이브."""
    db = await get_db()
    rows = [dict(r) for r in await (await db.execute(
        """SELECT s.channel_id, s.channel_name, s.channel_image_url, s.follower_count,
                  s.verified_mark, s.tagged_clip_count,
                  c.clip_uid, c.clip_title, c.thumbnail_image_url, c.heart_count,
                  c.view_count, c.created_at, c.duration
           FROM singcup_streamers s
           JOIN singcup_clips c ON c.clip_uid = s.representative_clip_uid
           WHERE s.event_id=? AND c.active=1""", (EVENT_ID,))).fetchall()]

    reps = [{**r, "owner_channel_id": r["channel_id"]} for r in rows]
    ranked = compute_scores(reps)

    now = int(time.time())
    prev, day = await _delta_maps(now)

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
        d24 = day.get(cid)
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
            "heartDelta": (r["heart_count"] - p[0]) if p else None,
            "rankDelta": (p[1] - r["rank"]) if p else None,
            "heartDelta24h": (r["heart_count"] - d24) if d24 is not None else None,
            "heartChangeRate24h": heart_change_rate(r["heart_count"], d24)
                                  if d24 is not None else None,
            "isNew": p is None,
            "live": live.get(cid),
        })

    last_run = await (await db.execute(
        "SELECT MAX(collected_at) c FROM singcup_snapshots WHERE event_id=?",
        (EVENT_ID,))).fetchone()
    last_at = last_run["c"] if last_run and last_run["c"] else None
    # KPI 증감 — 지금과 1시간 전을 '같은 소스(singcup_clips)'에서 세야 뺄셈이 성립한다.
    # first_collected_at은 그 클립을 처음 확인한 시각이라, 그 이하만 세면 1시간 전 상태다.
    cnt = await (await db.execute(
        """SELECT COUNT(*)                                              AS clips,
                  COUNT(DISTINCT owner_channel_id)                      AS streamers,
                  SUM(CASE WHEN first_collected_at <= ? THEN 1 ELSE 0 END) AS clips_before
           FROM singcup_clips WHERE event_id=? AND active=1""",
        (now - DELTA_WINDOW_SECONDS, EVENT_ID))).fetchone()
    before = await (await db.execute(
        """SELECT COUNT(DISTINCT owner_channel_id) AS n FROM singcup_clips
           WHERE event_id=? AND active=1 AND first_collected_at <= ?""",
        (EVENT_ID, now - DELTA_WINDOW_SECONDS))).fetchone()
    clips_now = int(cnt["clips"] or 0)
    streamers_before = int(before["n"] or 0)

    return {
        "event": event_meta(),
        "summary": {
            "taggedClipCount": clips_now,
            "streamerCount": len(ranked),
            "liveCount": sum(1 for r in out if r["live"]),
            # 수집을 시작한 지 1시간이 안 됐으면 '전부 신규'라 증감이 무의미하다 → null
            "taggedClipDelta": (clips_now - int(cnt["clips_before"] or 0))
                               if int(cnt["clips_before"] or 0) > 0 else None,
            "streamerDelta": (int(cnt["streamers"] or 0) - streamers_before)
                             if streamers_before > 0 else None,
            "deltaWindowMinutes": DELTA_WINDOW_SECONDS // 60,
        },
        "live": live_info,
        "collector": {
            "lastSuccessAt": datetime.fromtimestamp(last_at, _KST).isoformat()
                             if last_at else None,
            "stale": last_at is None or (now - last_at) > 30 * 60,
        },
        "streamers": out,
    }


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
        try:
            st = event_status()
            if st == "LIVE":
                await discover_new_clips()
                await refresh_metrics()
                await retry_failed_clips()
            elif st == "UPCOMING":
                wait = 30.0
            else:
                wait = 360.0          # 종료 후에는 사실상 멈춘다
        except Exception as e:
            _log({"event": "loop_error", "level": "warning", "detail": str(e)[:200]})
        await asyncio.sleep(max(60.0, wait * 60))
