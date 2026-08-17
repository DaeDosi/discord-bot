"""PIKU 사용자 투표 순위 — 수집 · 원자 교체 · 순위 재계산.

PIKU에는 공식 API가 없어 **공개 랭킹 페이지**를 읽는다. 이 모듈이 지키는 계약은 다섯이다.

1. **표시 계약** — 화면과 공개 API에는 **순위만** 나간다. 우승 비율·승률 숫자는
   DB 안에서 정렬에만 쓰고 절대 밖으로 내보내지 않는다(`public_ranking()` 참고).
   조회수·하트도 다루지 않는다.
2. **원자 교체** — 한 부문의 dataset을 통째로 만들고 **전부 정상일 때만** 활성화한다.
   일부 페이지 실패·빈 응답·파싱 오류·403·429·Cloudflare challenge는 활성화하지
   않으므로 직전 정상 데이터가 그대로 남는다. 0이나 빈 목록이 정상값을 덮는 경로가
   구조적으로 없다.
3. **매핑은 관리자가 한다** — PIKU 이름과 공식 참가자 이름이 다를 수 있어
   문자열 유사도로 자동 확정하지 않는다. 정확 일치만 `suggested`로 제안하고,
   `confirmed`가 되기 전에는 순위에 넣지 않는다.
4. **기본 꺼짐** — 자동 수집은 `PIKU_AUTO_COLLECT_ENABLED`가 참일 때만 돈다.
   기본값은 거짓이라 배포만으로 외부 요청이 나가지 않는다.
5. **우회하지 않는다** — 403·429·challenge를 만나면 **중단**한다. User-Agent 위장·
   프록시 회전·CAPTCHA 우회는 이 모듈에 없다. `Retry-After`를 존중한다.

원본 HTML은 보관하지 않는다(파싱 결과만 저장한다).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from database import get_db

# ── 부문 ────────────────────────────────────────────────────────────────────
#: `singcup_qualifiers`·`routers.singcup_router.DIVISIONS`와 **같은 값**이다.
DIVISIONS: tuple[str, ...] = ("female_solo", "male_solo", "groups")
DIVISION_LABELS: dict[str, str] = {
    "female_solo": "여성 솔로",
    "male_solo": "남성 솔로",
    "groups": "그룹",
}

#: 정렬 기준 — **내부 컬럼명**. DB 안에서만 쓴다.
SORT_KEYS: tuple[str, ...] = ("win_rate", "match_rate")

#: 공개 정렬 토큰 → 내부 컬럼. **공개 응답과 브라우저 번들에 내부 컬럼명을 내보내지
#: 않기 위한 매핑**이다.
#:
#: 값 자체(비율·승률 숫자)는 애초에 나가지 않지만, 요구가 "키 이름·숫자·직렬화
#: 문자열 어느 형태로도"이므로 이름까지 분리한다. 사람이 읽을 뜻은 `SORT_LABELS`가
#: 담으므로 API의 자기설명성은 유지된다.
PUBLIC_SORTS: dict[str, str] = {"primary": "win_rate", "secondary": "match_rate"}
SORT_LABELS: dict[str, str] = {"primary": "우승 비율순", "secondary": "승률순"}
DEFAULT_SORT = "primary"


def resolve_sort(public_key: object) -> tuple[str, str]:
    """공개 토큰 → (공개 토큰, 내부 컬럼). 모르는 값은 기본값으로 떨어진다."""
    k = public_key if isinstance(public_key, str) else ""
    k = k.strip()
    if k not in PUBLIC_SORTS:
        k = DEFAULT_SORT
    return k, PUBLIC_SORTS[k]


# ── 설정 ────────────────────────────────────────────────────────────────────
def _flag(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def auto_collect_enabled() -> bool:
    """자동 수집 스케줄러가 도는가. **기본 꺼짐** — 배포만으로 외부 요청이 나가지 않는다."""
    return _flag("PIKU_AUTO_COLLECT_ENABLED", False)


#: 최소 수집 간격(분). 요구가 "최대 1시간에 한 번"이므로 하한을 60분으로 잡는다.
MIN_INTERVAL_MINUTES = max(60.0, float(os.getenv("PIKU_INTERVAL_MINUTES", "60")))
#: 한 부문에서 넘길 최대 페이지 수.
MAX_PAGES = max(1, min(10, int(os.getenv("PIKU_MAX_PAGES", "4"))))
#: 페이지 사이 대기(초). 사람이 넘기는 속도보다 느리게 둔다.
PAGE_DELAY_SECONDS = float(os.getenv("PIKU_PAGE_DELAY_SECONDS", "2.0"))
REQUEST_TIMEOUT = float(os.getenv("PIKU_REQUEST_TIMEOUT", "15"))
#: 지수 백오프 기준(초)과 최대 재시도.
BACKOFF_BASE_SECONDS = float(os.getenv("PIKU_BACKOFF_BASE_SECONDS", "5"))
MAX_RETRIES = max(0, min(5, int(os.getenv("PIKU_MAX_RETRIES", "2"))))
#: User-Agent — 운영 목적과 연락 수단을 **설정으로** 넣을 수 있게 한다.
#: 위장하지 않는다(그게 우회의 첫 단계다).
USER_AGENT = os.getenv(
    "PIKU_USER_AGENT",
    "NexBotStatsBot/1.0 (+https://nexbot.shop/about; contact via https://nexbot.shop/contact)")

#: 한 dataset이 가질 수 있는 최대 항목 수(폭주 방지).
MAX_ENTRIES = 2000


class PikuError(Exception):
    """수집·검증 실패. `kind`로 원인을 구분한다(응답 전문은 담지 않는다)."""

    def __init__(self, kind: str, message: str, *, http_status: int = 0,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status
        self.retry_after = retry_after


def _log(event: str, **fields) -> None:
    """구조화 로그 — **개인정보와 응답 전문을 넣지 않는다.**"""
    payload = {"event": event, **fields}
    print(f"[singcup_piku] {json.dumps(payload, ensure_ascii=False, default=str)}",
          flush=True)


# ── 파싱 ────────────────────────────────────────────────────────────────────
#
# PIKU 랭킹 페이지는 표 형태이고, 초기 HTML에 데이터가 없고 DataTables 요청으로
# 오는 경우가 있다. **두 형태를 같은 정규화 함수로 받는다** — 어느 쪽이 오든
# 파서 하나만 유지하면 되고, 관리자 수동 import도 같은 경로를 탄다.

_PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%?")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
#: Cloudflare challenge 판별 표식. 만나면 **중단**한다(우회하지 않는다).
_CHALLENGE_MARKERS = (
    "cf-browser-verification", "cf_chl_", "__cf_chl", "Just a moment...",
    "challenge-platform", "Attention Required! | Cloudflare",
)


def looks_like_challenge(text: str) -> bool:
    head = (text or "")[:4000]
    return any(m in head for m in _CHALLENGE_MARKERS)


def _clean_text(v: Any) -> str:
    s = "" if v is None else str(v)
    s = _TAG_RE.sub(" ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return _WS_RE.sub(" ", s).strip()


def _pct(v: Any) -> float | None:
    """'62.5%' · '62.5' · 62.5 → 62.5. 범위를 벗어나면 None(그 행은 버려진다)."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = float(v)
    else:
        m = _PCT_RE.search(str(v))
        if not m:
            return None
        n = float(m.group(1))
    # 0~1 스케일로 오는 경우도 받아 준다(0.625 → 62.5). 1.0은 100%로 본다.
    if 0.0 < n <= 1.0:
        n *= 100.0
    return n if 0.0 <= n <= 100.0 else None


def normalize_rows(raw: Any) -> list[dict]:
    """어떤 형태로 오든 `[{source_rank, name, thumbnail_url, win_rate, match_rate}]`로.

    받는 형태:
     · DataTables JSON: `{"data": [[...], ...]}` 또는 `{"data": [{...}, ...]}`
     · 우리 수동 import 형식: `[{"name": ..., "winRate": ..., "matchRate": ...}]`

    **행 하나가 이상하다고 전체를 버리지 않는다** — 대신 유효한 행만 남기고,
    호출부가 개수 검증으로 판단한다(부분 파싱을 성공으로 착각하지 않게).
    """
    if isinstance(raw, dict):
        raw = raw.get("data") or raw.get("rows") or raw.get("items") or []
    if not isinstance(raw, (list, tuple)):
        raise PikuError("parse_failed", "표 데이터를 찾지 못했습니다.")

    out: list[dict] = []
    for i, row in enumerate(raw):
        name = win = match = thumb = None
        rank = None
        if isinstance(row, dict):
            for k in ("name", "title", "participant", "이름", "참가자"):
                if row.get(k):
                    name = _clean_text(row[k])
                    break
            win = _pct(row.get("winRate", row.get("win_rate", row.get("우승비율"))))
            match = _pct(row.get("matchRate", row.get("match_rate", row.get("승률"))))
            rank = row.get("rank", row.get("source_rank", row.get("순위")))
            thumb = _clean_text(row.get("thumbnail", row.get("thumbnailUrl", "")))
        elif isinstance(row, (list, tuple)):
            # DataTables 배열형: [순위, 이름, 우승비율, 승률] 순서를 기대하되
            # 열이 밀려도 **퍼센트로 보이는 두 열**을 찾아 쓴다.
            cells = [_clean_text(c) for c in row]
            nums = [(j, _pct(c)) for j, c in enumerate(cells)]
            pcts = [(j, v) for j, v in nums if v is not None and "%" in cells[j]]
            if len(pcts) < 2:
                pcts = [(j, v) for j, v in nums if v is not None][-2:]
            if len(pcts) >= 2:
                win, match = pcts[0][1], pcts[1][1]
            used = {j for j, _ in pcts[:2]}
            texts = [(j, c) for j, c in enumerate(cells)
                     if j not in used and c and not c.replace(".", "").isdigit()]
            if texts:
                name = texts[0][1]
            if cells and cells[0].isdigit():
                rank = cells[0]
        else:
            continue

        if not name or win is None or match is None:
            continue
        try:
            rank_i = int(rank) if rank not in (None, "") else i + 1
        except (TypeError, ValueError):
            rank_i = i + 1
        out.append({"source_rank": rank_i, "name": name,
                    "thumbnail_url": thumb or "",
                    "win_rate": win, "match_rate": match})
        if len(out) >= MAX_ENTRIES:
            break
    return out


def validate_rows(rows: list[dict], *, min_entries: int = 1) -> list[dict]:
    """중복·필수 필드·비율 범위 검증. 통과하지 못하면 **활성화하지 않는다**."""
    if not rows:
        raise PikuError("empty", "수집 결과가 비어 있습니다.")
    if len(rows) < min_entries:
        raise PikuError("too_few", f"수집 결과가 너무 적습니다({len(rows)}건).")
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        name = r.get("name") or ""
        if not name:
            raise PikuError("parse_failed", "이름이 없는 행이 있습니다.")
        key = name.strip()
        if key in seen:
            # 같은 이름이 두 번 오면 뒤엣것을 버린다(앞엣것이 상위 순위다).
            continue
        seen.add(key)
        for f in ("win_rate", "match_rate"):
            v = r.get(f)
            if v is None or not (0.0 <= float(v) <= 100.0):
                raise PikuError("bad_rate", f"{f} 값이 범위를 벗어났습니다.")
        out.append(r)
    return out


# ── 부문 ↔ URL 매핑 ─────────────────────────────────────────────────────────

async def list_sources() -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM piku_sources ORDER BY division")).fetchall()
    by_div = {r["division"]: r for r in rows}
    return [{
        "division": d,
        "label": DIVISION_LABELS[d],
        "url": by_div[d]["url"] if d in by_div else "",
        "observedTitle": by_div[d]["observed_title"] if d in by_div else "",
        "enabled": bool(by_div[d]["enabled"]) if d in by_div else False,
        "lastAttemptAt": int(by_div[d]["last_attempt_at"]) if d in by_div else 0,
        "lastSuccessAt": int(by_div[d]["last_success_at"]) if d in by_div else 0,
        "lastErrorKind": by_div[d]["last_error_kind"] if d in by_div else "",
    } for d in DIVISIONS]


_URL_RE = re.compile(r"^https://www\.piku\.co\.kr/w/rank/[A-Za-z0-9_-]{1,32}$")


def validate_url(url: str) -> str:
    """PIKU 랭킹 URL 형식만 통과시킨다.

    임의 URL을 받으면 운영자가 실수로(또는 누군가가 secret을 얻어) 아무 사이트나
    긁게 만들 수 있다. 호스트와 경로 형태를 **닫힌 규칙**으로 고정한다.
    """
    u = (url or "").strip()
    if not _URL_RE.match(u):
        raise PikuError("bad_url",
                        "PIKU 랭킹 주소 형식이 아닙니다"
                        " (https://www.piku.co.kr/w/rank/XXXX).")
    return u


async def set_sources(mapping: dict[str, str]) -> list[dict]:
    """부문 → URL 매핑을 통째로 저장한다.

    검증: **세 부문이 모두 있어야 하고**, URL이 서로 달라야 한다.
    같은 URL을 두 부문에 넣으면 두 부문의 순위가 같아지는데, 그건 매핑 실수이지
    정상 상태가 아니다(추측으로 대응을 정하지 말라는 요구의 이행이다).
    """
    got = {d: (mapping.get(d) or "").strip() for d in DIVISIONS}
    missing = [DIVISION_LABELS[d] for d in DIVISIONS if not got[d]]
    if missing:
        raise PikuError("missing_url", f"주소가 비어 있는 부문: {', '.join(missing)}")
    cleaned = {d: validate_url(got[d]) for d in DIVISIONS}
    dupes = [u for u in set(cleaned.values()) if list(cleaned.values()).count(u) > 1]
    if dupes:
        raise PikuError("duplicate_url", "같은 주소를 두 부문에 지정할 수 없습니다.")

    now = int(time.time())
    db = await get_db()
    for d, u in cleaned.items():
        await db.execute(
            """INSERT INTO piku_sources (division, url, enabled, updated_at)
               VALUES (?,?,1,?)
               ON CONFLICT(division) DO UPDATE SET url=excluded.url,
                    enabled=1, updated_at=excluded.updated_at""",
            (d, u, now))
    await db.commit()
    _log("sources_saved", divisions=list(cleaned))
    return await list_sources()


# ── dataset 원자 교체 ───────────────────────────────────────────────────────

async def _begin_dataset(division: str, *, source: str, source_url: str) -> int:
    db = await get_db()
    cur = await db.execute(
        """INSERT INTO piku_datasets (division, status, source, source_url, created_at)
           VALUES (?, 'building', ?, ?, ?)""",
        (division, source, source_url, int(time.time())))
    await db.commit()
    return int(cur.lastrowid)


async def _fill_dataset(dataset_id: int, rows: list[dict]) -> None:
    db = await get_db()
    await db.executemany(
        """INSERT OR REPLACE INTO piku_entries
               (dataset_id, source_rank, name, thumbnail_url, win_rate, match_rate)
           VALUES (?,?,?,?,?,?)""",
        [(dataset_id, r["source_rank"], r["name"], r.get("thumbnail_url", ""),
          r["win_rate"], r["match_rate"]) for r in rows])
    await db.commit()


async def _activate(dataset_id: int, division: str, *, pages: int,
                    entry_count: int) -> None:
    """`building` → `active`. 직전 활성본은 `superseded`로 내린다.

    **여기까지 왔다는 것은 모든 페이지가 정상이었다는 뜻이다.** 그 판단은 호출부가
    하고, 이 함수는 교체만 한다 — 두 책임을 섞으면 "일부만 성공했는데 활성화"가
    가능한 코드 경로가 생긴다.
    """
    now = int(time.time())
    db = await get_db()
    await db.execute(
        "UPDATE piku_datasets SET status='superseded' "
        "WHERE division=? AND status='active'", (division,))
    await db.execute(
        "UPDATE piku_datasets SET status='active', activated_at=?, pages=?, "
        "entry_count=? WHERE id=?", (now, pages, entry_count, dataset_id))
    await db.execute(
        """INSERT INTO piku_sources (division, url, last_success_at, updated_at)
           VALUES (?, '', ?, ?)
           ON CONFLICT(division) DO UPDATE SET last_success_at=excluded.last_success_at,
                last_error_kind='', updated_at=excluded.updated_at""",
        (division, now, now))
    await db.commit()
    _log("dataset_activated", division=division, datasetId=dataset_id,
         entries=entry_count, pages=pages)


async def _discard(dataset_id: int) -> None:
    """실패한 dataset을 버린다. **활성본은 건드리지 않는다.**"""
    db = await get_db()
    await db.execute("DELETE FROM piku_entries WHERE dataset_id=?", (dataset_id,))
    await db.execute("DELETE FROM piku_datasets WHERE id=?", (dataset_id,))
    await db.commit()


async def active_dataset(division: str) -> dict | None:
    db = await get_db()
    r = await (await db.execute(
        "SELECT * FROM piku_datasets WHERE division=? AND status='active'",
        (division,))).fetchone()
    return dict(r) if r else None


async def _record_run(division: str, started: int, *, ok: bool, applied: bool,
                      http_status: int = 0, error_kind: str = "",
                      pages: int = 0, entries: int = 0, note: str = "") -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO piku_collect_runs (division, started_at, finished_at, ok,
               http_status, error_kind, pages, entries, applied, note)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (division, started, int(time.time()), 1 if ok else 0, http_status,
         error_kind, pages, entries, 1 if applied else 0, note[:200]))
    await db.execute(
        """INSERT INTO piku_sources (division, url, last_attempt_at, last_error_kind,
                                     updated_at)
           VALUES (?, '', ?, ?, ?)
           ON CONFLICT(division) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,
                last_error_kind=excluded.last_error_kind,
                updated_at=excluded.updated_at""",
        (division, started, error_kind, int(time.time())))
    await db.commit()


# ── 수집 ────────────────────────────────────────────────────────────────────

async def _fetch_page(client, url: str, page: int) -> tuple[int, str]:
    """한 페이지를 가져온다. 우회 수단은 쓰지 않는다."""
    r = await client.get(url, params={"page": page} if page > 1 else None,
                         headers={"User-Agent": USER_AGENT,
                                  "Accept": "text/html,application/json"},
                         timeout=REQUEST_TIMEOUT, follow_redirects=True)
    return r.status_code, r.text


def _raise_for_status(status: int, text: str, retry_after: str | None) -> None:
    if status == 403:
        raise PikuError("forbidden", "PIKU가 접근을 거부했습니다(403). 중단합니다.",
                        http_status=403)
    if status == 429:
        ra = None
        try:
            ra = float(retry_after) if retry_after else None
        except ValueError:
            ra = None
        raise PikuError("rate_limited", "PIKU가 요청 제한을 알렸습니다(429).",
                        http_status=429, retry_after=ra)
    if status >= 400:
        raise PikuError("http_error", f"PIKU 응답 오류({status}).", http_status=status)
    if looks_like_challenge(text):
        # **우회하지 않는다.** challenge는 운영자의 의사 표시로 다룬다.
        raise PikuError("challenge", "Cloudflare 확인 화면을 만나 중단했습니다.",
                        http_status=status)


def extract_payload(text: str) -> Any:
    """페이지 본문에서 표 데이터를 뽑는다(JSON 응답이면 그대로).

    **HTML 원문을 저장하지 않는다** — 여기서 구조만 꺼내고 버린다.
    """
    t = (text or "").strip()
    if t.startswith("{") or t.startswith("["):
        try:
            return json.loads(t)
        except ValueError:
            pass
    # HTML 안에 표 데이터가 JSON으로 박혀 있는 흔한 형태들
    for pat in (r"<script[^>]*type=\"application/json\"[^>]*>(.*?)</script>",
                r"var\s+rankData\s*=\s*(\[.*?\]|\{.*?\})\s*;",
                r"\"aaData\"\s*:\s*(\[.*?\])"):
        m = re.search(pat, t, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except ValueError:
                continue
    # 표 파싱 — 행/열만 뽑는다
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)
        if cells:
            rows.append(cells)
    if rows:
        return rows
    raise PikuError("parse_failed", "표 데이터를 찾지 못했습니다.")


async def collect_division(division: str, *, client=None) -> dict:
    """한 부문을 수집해 **전부 정상일 때만** 활성화한다.

    실패 종류(`forbidden`/`rate_limited`/`challenge`/`parse_failed`/`empty`/`timeout`)
    와 무관하게, 활성화하지 않으면 **직전 정상 데이터가 그대로 남는다**.
    """
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    started = int(time.time())
    src = {s["division"]: s for s in await list_sources()}[division]
    if not src["url"]:
        await _record_run(division, started, ok=False, applied=False,
                          error_kind="missing_url")
        raise PikuError("missing_url", "이 부문의 PIKU 주소가 설정되지 않았습니다.")
    if not src["enabled"]:
        await _record_run(division, started, ok=False, applied=False,
                          error_kind="disabled")
        raise PikuError("disabled", "이 부문의 수집이 꺼져 있습니다.")

    owns_client = client is None
    if owns_client:
        import httpx
        client = httpx.AsyncClient()

    dataset_id: int | None = None
    rows_all: list[dict] = []
    pages_done = 0
    try:
        for page in range(1, MAX_PAGES + 1):
            attempt = 0
            while True:
                try:
                    status, text = await _fetch_page(client, src["url"], page)
                    _raise_for_status(status, text,
                                      None)  # Retry-After는 아래 except에서 처리
                    break
                except PikuError as e:
                    # 403·challenge는 **재시도하지 않는다**(거부 의사가 분명하다).
                    if e.kind in ("forbidden", "challenge"):
                        raise
                    attempt += 1
                    if attempt > MAX_RETRIES:
                        raise
                    delay = (e.retry_after if e.retry_after is not None
                             else BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                    _log("retry", division=division, page=page, attempt=attempt,
                         kind=e.kind, delaySeconds=delay)
                    await asyncio.sleep(min(60.0, max(0.0, delay)))

            rows = normalize_rows(extract_payload(text))
            pages_done = page
            if not rows:
                # 빈 페이지 = 목록의 끝. 첫 페이지가 비면 아래 검증이 잡는다.
                break
            rows_all.extend(rows)
            if page < MAX_PAGES:
                await asyncio.sleep(PAGE_DELAY_SECONDS)

        valid = validate_rows(rows_all)
        dataset_id = await _begin_dataset(division, source="scrape",
                                          source_url=src["url"])
        await _fill_dataset(dataset_id, valid)
        await _activate(dataset_id, division, pages=pages_done,
                        entry_count=len(valid))
        await _record_run(division, started, ok=True, applied=True,
                          pages=pages_done, entries=len(valid))
        await sync_mappings(division)
        return {"division": division, "entries": len(valid), "pages": pages_done,
                "applied": True}
    except PikuError as e:
        if dataset_id is not None:
            await _discard(dataset_id)
        await _record_run(division, started, ok=False, applied=False,
                          http_status=e.http_status, error_kind=e.kind,
                          pages=pages_done, entries=len(rows_all))
        _log("collect_failed", division=division, kind=e.kind,
             httpStatus=e.http_status, detail=str(e)[:160])
        raise
    except Exception as e:                       # noqa: BLE001 — 마지막 방어선
        if dataset_id is not None:
            await _discard(dataset_id)
        await _record_run(division, started, ok=False, applied=False,
                          error_kind="unexpected", pages=pages_done)
        _log("collect_error", division=division, detail=str(e)[:160])
        raise PikuError("unexpected", "수집 중 오류가 발생했습니다.") from e
    finally:
        if owns_client:
            await client.aclose()


async def import_rows(division: str, raw: Any) -> dict:
    """관리자 수동 import — 수집과 **같은 검증·같은 원자 교체**를 거친다.

    외부 접속 없이 데이터를 넣는 경로다. 수집이 막혀 있을 때의 대체 수단이자,
    파서를 실제 응답으로 검증하는 수단이기도 하다.
    """
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    started = int(time.time())
    rows = validate_rows(normalize_rows(raw))
    dataset_id = await _begin_dataset(division, source="manual_import", source_url="")
    try:
        await _fill_dataset(dataset_id, rows)
        await _activate(dataset_id, division, pages=0, entry_count=len(rows))
    except Exception:
        await _discard(dataset_id)
        await _record_run(division, started, ok=False, applied=False,
                          error_kind="import_failed")
        raise
    await _record_run(division, started, ok=True, applied=True, entries=len(rows),
                      note="manual_import")
    await sync_mappings(division)
    return {"division": division, "entries": len(rows), "applied": True,
            "source": "manual_import"}


def parse_csv(text: str) -> list[dict]:
    """관리자용 CSV — 헤더 `name,winRate,matchRate[,rank][,thumbnail]`."""
    import csv
    import io
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise PikuError("parse_failed", "CSV 헤더가 없습니다.")
    return [dict(r) for r in reader]


# ── 매핑 ────────────────────────────────────────────────────────────────────

async def sync_mappings(division: str) -> dict:
    """활성 dataset의 이름들에 대해 매핑 행을 만들어 둔다.

    **정확 일치만 `suggested`로 제안한다.** 유사도 매칭은 하지 않는다 — 한 글자
    차이로 다른 스트리머에게 붙으면 순위가 통째로 틀어지고, 그 오류는 화면에서
    보이지 않는다. 확정(`confirmed`)은 관리자만 한다.
    """
    import singcup_qualifiers as sq

    ds = await active_dataset(division)
    if not ds:
        return {"division": division, "created": 0}
    db = await get_db()
    names = [r["name"] for r in await (await db.execute(
        "SELECT name FROM piku_entries WHERE dataset_id=?", (ds["id"],))).fetchall()]

    # 공식 명단의 이름 → channel_id (부문 안에서만 찾는다)
    official: dict[str, str] = {}
    if division == "groups":
        for g in sq.QUALIFIERS["groups"]:
            for m in g["members"]:
                official.setdefault(_norm_name(m["name"]), m["channelId"])
    else:
        for r in sq.QUALIFIERS[division]:
            official.setdefault(_norm_name(r["name"]), r["channelId"])

    now = int(time.time())
    created = 0
    for n in names:
        exact = official.get(_norm_name(n))
        # 이미 있는 행은 건드리지 않는다 — 관리자가 확정한 값을 덮어쓰면 안 된다.
        cur = await db.execute(
            """INSERT INTO piku_mappings (division, piku_name, channel_id, state,
                                          updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(division, piku_name) DO NOTHING""",
            (division, n, exact, "suggested" if exact else "unmapped", now))
        created += cur.rowcount or 0
    await db.commit()
    return {"division": division, "created": created, "names": len(names)}


def _norm_name(s: str) -> str:
    """비교용 정규화 — 공백 제거 + 소문자. **매칭 확정에는 쓰지 않는다**(제안만)."""
    return re.sub(r"\s+", "", str(s or "")).lower()


async def list_mappings(division: str | None = None) -> list[dict]:
    db = await get_db()
    if division:
        rows = await (await db.execute(
            "SELECT * FROM piku_mappings WHERE division=? ORDER BY piku_name",
            (division,))).fetchall()
    else:
        rows = await (await db.execute(
            "SELECT * FROM piku_mappings ORDER BY division, piku_name")).fetchall()
    return [{"division": r["division"], "pikuName": r["piku_name"],
             "channelId": r["channel_id"], "state": r["state"],
             "updatedAt": int(r["updated_at"])} for r in rows]


async def set_mapping(division: str, piku_name: str, channel_id: str | None,
                      *, state: str = "confirmed") -> dict:
    """관리자가 매핑을 확정하거나 해제한다."""
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    if state not in ("confirmed", "unmapped", "excluded", "suggested"):
        raise PikuError("bad_state", "알 수 없는 매핑 상태입니다.")
    cid = (channel_id or "").strip().lower() or None
    if state == "confirmed":
        import singcup_qualifiers as sq
        if not cid:
            raise PikuError("missing_channel", "연결할 채널을 선택해 주세요.")
        if cid not in sq.ALL_CHANNEL_IDS:
            # 공식 명단 밖의 채널에는 붙이지 않는다 — 순위는 공식 참가자 화면이다.
            raise PikuError("not_qualifier", "공식 예선 참가자 명단에 없는 채널입니다.")
    else:
        cid = None
    db = await get_db()
    await db.execute(
        """INSERT INTO piku_mappings (division, piku_name, channel_id, state, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(division, piku_name) DO UPDATE SET channel_id=excluded.channel_id,
                state=excluded.state, updated_at=excluded.updated_at""",
        (division, piku_name, cid, state, int(time.time())))
    await db.commit()
    return {"division": division, "pikuName": piku_name, "channelId": cid,
            "state": state}


# ── 순위 재계산 (공개) ──────────────────────────────────────────────────────

def _sorted_entries(rows: list[dict], sort: str) -> list[dict]:
    """선택한 기준 내림차순. **동점 규칙을 고정한다.**

    1순위: 선택한 기준 내림차순
    2순위: 다른 기준 내림차순 — 두 값이 모두 같은 경우가 아니면 여기서 갈린다
    3순위: PIKU 원본 순위 오름차순 — 그래도 같으면 원본이 준 순서를 존중한다
    4순위: 이름 오름차순 — 최종 결정자(모든 값이 같아도 결과가 흔들리지 않게)

    이 규칙이 없으면 같은 데이터로 새로고침할 때마다 순위가 바뀔 수 있다.
    """
    other = "match_rate" if sort == "win_rate" else "win_rate"
    return sorted(rows, key=lambda r: (
        -float(r[sort] or 0.0),
        -float(r[other] or 0.0),
        int(r["source_rank"] or 10**9),
        str(r["name"]),
    ))


async def public_ranking(division: str, *, sort: str = DEFAULT_SORT,
                         limit: int = 0) -> dict:
    """공개 순위 — **순위와 표시용 최소 정보만.**

    ⚠️ `win_rate`/`match_rate`는 이 함수 밖으로 나가지 않는다. 정렬에만 쓰고
    응답에서 버린다. 이 계약은 `tests/test_singcup_piku.py`가 지킨다.

    정렬 기준이 바뀌면 **1위부터 다시 계산**한다(PIKU 원본 순위를 그대로 쓰지 않는다).
    """
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    # 공개 토큰 ↔ 내부 컬럼을 여기서 한 번만 바꾼다. 아래에서는 정렬에만 내부
    # 컬럼을 쓰고, **응답에는 공개 토큰만** 담는다.
    public_sort, column = resolve_sort(sort)
    ds = await active_dataset(division)
    if not ds:
        return {"division": division, "label": DIVISION_LABELS[division],
                "sort": public_sort, "sortLabel": SORT_LABELS[public_sort],
                "entries": [], "available": False,
                "lastSuccessAt": 0, "unmappedCount": 0}

    db = await get_db()
    rows = [dict(r) for r in await (await db.execute(
        """SELECT e.source_rank, e.name, e.thumbnail_url, e.win_rate, e.match_rate,
                  m.channel_id, m.state
             FROM piku_entries e
             LEFT JOIN piku_mappings m
               ON m.division = ? AND m.piku_name = e.name
            WHERE e.dataset_id = ?""",
        (division, ds["id"]))).fetchall()]

    # 관리자가 확정한 매핑만 순위에 넣는다. 미매핑·제안 상태는 **잘못된 스트리머에
    # 연결하지 않기 위해** 제외하고, 개수만 알려 준다(관리 화면이 처리할 수 있게).
    mapped = [r for r in rows if r.get("state") == "confirmed" and r.get("channel_id")]
    unmapped = len(rows) - len(mapped)

    ordered = _sorted_entries(mapped, column)
    entries = [{
        "rank": i + 1,                       # 정렬 기준마다 1위부터 다시 계산
        "channelId": r["channel_id"],
        "name": r["name"],
        "thumbnailUrl": r["thumbnail_url"] or "",
        # 원본 순위는 **참고용으로만** 내보낸다(우리 순위와 다른 값임을 화면이 밝힌다).
        "sourceRank": int(r["source_rank"]) if r["source_rank"] is not None else None,
    } for i, r in enumerate(ordered)]
    if limit and limit > 0:
        entries = entries[:limit]

    src = {s["division"]: s for s in await list_sources()}[division]
    return {
        "division": division,
        "label": DIVISION_LABELS[division],
        "sort": public_sort,
        "sortLabel": SORT_LABELS[public_sort],
        "entries": entries,
        "available": True,
        "total": len(ordered),
        "unmappedCount": unmapped,
        "lastSuccessAt": src["lastSuccessAt"],
        "sourceUrl": src["url"],
    }


async def public_status() -> dict:
    """공개 상태 — 마지막 정상 갱신 시각과 출처. **내부 값은 없다.**"""
    out = {"autoCollectEnabled": auto_collect_enabled(), "divisions": {}}
    for s in await list_sources():
        ds = await active_dataset(s["division"])
        out["divisions"][s["division"]] = {
            "label": s["label"],
            "sourceUrl": s["url"],
            "lastSuccessAt": s["lastSuccessAt"],
            "available": bool(ds),
            "entryCount": int(ds["entry_count"]) if ds else 0,
        }
    return out


async def admin_status() -> dict:
    """관리자 진단 — 실행 이력과 매핑 현황. **여기서도 비율·승률 숫자는 내보내지 않는다.**

    요구는 "관리자 API에서만 진단용 내부 값을 볼 수 있게 하더라도 OWNER 권한을
    강제하고 민감정보 노출을 최소화"다. 비율·승률은 진단에 필요하지 않다 —
    필요한 것은 "몇 건이 들어왔고 매핑이 몇 건 남았는가"이므로 그것만 준다.
    """
    db = await get_db()
    runs = [dict(r) for r in await (await db.execute(
        "SELECT division, started_at, finished_at, ok, http_status, error_kind,"
        " pages, entries, applied, note FROM piku_collect_runs"
        " ORDER BY started_at DESC LIMIT 30")).fetchall()]
    maps = await list_mappings()
    by_state: dict[str, int] = {}
    for m in maps:
        by_state[m["state"]] = by_state.get(m["state"], 0) + 1
    return {
        "sources": await list_sources(),
        "autoCollectEnabled": auto_collect_enabled(),
        "intervalMinutes": MIN_INTERVAL_MINUTES,
        "maxPages": MAX_PAGES,
        "userAgent": USER_AGENT,
        "runs": runs,
        "mappingCounts": by_state,
        "unmapped": [m for m in maps if m["state"] != "confirmed"][:200],
    }


# ── 자동 수집 워커 ──────────────────────────────────────────────────────────

async def start_piku_worker() -> None:
    """자동 수집 루프. **기본값에서는 즉시 반환한다** — 배포만으로 돌지 않는다."""
    if not auto_collect_enabled():
        _log("worker_disabled", reason="PIKU_AUTO_COLLECT_ENABLED=false")
        return
    _log("worker_start", intervalMinutes=MIN_INTERVAL_MINUTES, maxPages=MAX_PAGES)
    await asyncio.sleep(float(os.getenv("PIKU_START_DELAY_SECONDS", "60")))
    while True:
        for d in DIVISIONS:
            if not auto_collect_enabled():        # 실행 중 꺼도 즉시 멈춘다
                _log("worker_stopped", reason="flag_off")
                return
            try:
                await collect_division(d)
            except PikuError as e:
                # 실패해도 다음 부문을 계속 시도한다. 활성본은 그대로다.
                _log("worker_division_failed", division=d, kind=e.kind)
            except Exception as e:                # noqa: BLE001
                _log("worker_error", division=d, detail=str(e)[:160])
            await asyncio.sleep(PAGE_DELAY_SECONDS)
        await asyncio.sleep(MIN_INTERVAL_MINUTES * 60)
