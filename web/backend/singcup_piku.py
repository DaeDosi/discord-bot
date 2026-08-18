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

#: 부문별 PIKU 랭킹 페이지 **정본**.
#:
#: 운영자가 실측해 확정한 매핑이다. 처음 전달받은 값에서 **남성 솔로와 그룹이
#: 서로 뒤바뀌어 있었고**, 그대로 두면 남성 참가자 순위가 그룹 부문에 저장되는
#: 조용한 오염이 된다(화면에는 이름만 보이므로 눈으로 잡히지 않는다).
#: 그래서 상수로 고정하고 `expected_division_for_url()`로 교차 검증한다.
#:
#: 부문 키는 코드베이스 전역이 쓰는 `groups`(복수)를 그대로 둔다 — DB·공개 API·
#: 프론트가 모두 이 키를 쓰므로 이름만 바꾸면 연쇄 변경이 된다.
PIKU_CATEGORY_URLS: dict[str, str] = {
    "female_solo": "https://www.piku.co.kr/w/rank/8jGsHE",
    "male_solo":   "https://www.piku.co.kr/w/rank/7PqH44",
    "groups":      "https://www.piku.co.kr/w/rank/7fXoNs",
}


def expected_division_for_url(url: str) -> str | None:
    """이 주소가 어느 부문의 정본인지. 정본에 없으면 `None`(판단하지 않는다)."""
    u = (url or "").strip().rstrip("/")
    for d, known in PIKU_CATEGORY_URLS.items():
        if u == known.rstrip("/"):
            return d
    return None


def assert_division_matches_url(division: str, url: str) -> None:
    """설정한 부문과 주소가 정본과 어긋나면 **저장 전에** 막는다.

    정본에 없는 주소는 통과시킨다 — 대회 URL이 바뀔 수 있고, 모르는 주소를
    금지하면 운영자가 새 주소를 넣을 방법이 없어진다. 막는 것은 **아는 주소를
    틀린 부문에 넣는 경우**뿐이다.
    """
    want = expected_division_for_url(url)
    if want is not None and want != division:
        raise PikuError(
            "division_mismatch",
            f"이 주소는 {DIVISION_LABELS[want]} 부문입니다"
            f"({DIVISION_LABELS.get(division, division)}으로 설정할 수 없습니다).")

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
#: 한 번에 요청할 행 수(DataTables `length`). 화면 기본이 10이라 그대로 쓴다.
PAGE_LENGTH = max(10, min(100, int(os.getenv("PIKU_PAGE_LENGTH", "10"))))

#: **페이지 수를 고정하지 않는다.** `recordsTotal`을 보고 끝까지 간다. 이 값은
#: 폭주를 막는 안전 상한일 뿐이며 정상 수집에서 도달하지 않는다(64명 → 7회).
MAX_REQUESTS_PER_DIVISION = max(10, min(100,
                                        int(os.getenv("PIKU_MAX_REQUESTS", "60"))))

#: 한 응답의 최대 바이트. HTML 덤프나 폭주 응답을 파서에 넣지 않기 위한 방어선.
MAX_RESPONSE_BYTES = max(64_000, int(os.getenv("PIKU_MAX_RESPONSE_BYTES",
                                               str(2_000_000))))

#: 예전 `?page=` 추측 방식의 잔재. 이제 쓰지 않지만 설정 호환을 위해 남긴다.
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


#: `src="..."` / `src='...'` 양쪽을 받는다. 따옴표를 문자 클래스로 두면
#: 파이썬 문자열 안에서 이스케이프가 꼬이지 않는다.
_IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
#: "이름<br><small>곡 - 가수</small>" 또는 "이름 (곡 - 가수)" 형태를 가른다.
_BREAK_RE = re.compile(r"<br\s*/?>|</?small[^>]*>|\n", re.I)


def split_name_song(cell_html: str) -> tuple[str, str, str]:
    """이름 셀에서 `(이름, 곡, 가수)`를 가른다.

    **이름 문자열을 추측으로 쪼개지 않는다.** 줄바꿈·`<small>`로 이미 나뉘어 온
    두 번째 조각만 곡 정보로 보고, 그 안에서 마지막 ` - `를 기준으로 가수를 뗀다
    (곡 제목에 하이픈이 들어갈 수 있으므로 **마지막** 구분자를 쓴다).
    조각이 하나뿐이면 곡·가수는 빈 문자열이다 — 없는 정보를 만들어내지 않는다.
    """
    parts = [_clean_text(x) for x in _BREAK_RE.split(cell_html or "")]
    parts = [x for x in parts if x]
    if not parts:
        return "", "", ""
    name = parts[0]
    if len(parts) < 2:
        return name, "", ""
    tail = parts[1]
    if " - " in tail:
        song, artist = tail.rsplit(" - ", 1)
        return name, song.strip(), artist.strip()
    return name, tail, ""


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
        song = artist = ""
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
            song = _clean_text(row.get("songTitle", row.get("song_title",
                                                            row.get("곡", ""))))
            artist = _clean_text(row.get("artistName", row.get("artist_name",
                                                               row.get("가수", ""))))
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
            raw_cells = [c if isinstance(c, str) else "" for c in row]
            # 이미지 주소는 **원본 셀**에서 뽑는다(_clean_text가 태그를 지운다).
            for c in raw_cells:
                m = _IMG_RE.search(c)
                if m:
                    thumb = m.group(1)
                    break
            texts = [(j, c) for j, c in enumerate(cells)
                     if j not in used and c and not c.replace(".", "").isdigit()]
            if texts:
                j = texts[0][0]
                name, song, artist = split_name_song(raw_cells[j])
                if not name:
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
                    # 곡·가수는 **공개 정보**다(화면 2줄 표시에 쓴다).
                    # 비율과 달리 감출 이유가 없다.
                    "song_title": song or "", "artist_name": artist or "",
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
    import math

    seen: set[str] = set()
    seen_ranks: set[int] = set()
    out: list[dict] = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name:
            raise PikuError("parse_failed", "이름이 없는 행이 있습니다.")
        # **중복 순위는 파싱이 어긋났다는 신호다.** 이름 중복과 달리 조용히
        # 넘기지 않는다 — 순위가 겹치면 어느 쪽이 맞는지 알 수 없다.
        rk = r.get("source_rank")
        try:
            rk_i = int(rk)
        except (TypeError, ValueError):
            raise PikuError("parse_failed", "순위를 읽지 못한 행이 있습니다.") from None
        if rk_i <= 0:
            raise PikuError("parse_failed", f"순위가 양수가 아닙니다({rk_i}).")
        if rk_i in seen_ranks:
            raise PikuError("duplicate_rank", f"순위 {rk_i}이(가) 중복됩니다.")
        seen_ranks.add(rk_i)
        if name in seen:
            # 같은 이름이 두 번 오면 뒤엣것을 버린다(앞엣것이 상위 순위다).
            continue
        seen.add(name)
        for f in ("win_rate", "match_rate"):
            v = r.get(f)
            if v is None:
                raise PikuError("bad_rate", f"{f}가 없습니다.")
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                raise PikuError("bad_rate", f"{f}가 숫자가 아닙니다.")
            if not (0.0 <= fv <= 100.0):
                raise PikuError("bad_rate", f"{f} 값이 범위를 벗어났습니다({fv}).")
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
        # 정본과 어긋나게 배치됐는지. 관리 화면이 이 값을 보고 경고를 띄운다 —
        # 과거에 뒤바뀐 채로 저장된 설정을 **조용히 다시 수집하지 않기** 위해서다.
        "divisionMismatch": _mismatch_of(d, by_div[d]["url"] if d in by_div else ""),
        "expectedUrl": PIKU_CATEGORY_URLS.get(d, ""),
    } for d in DIVISIONS]


def _mismatch_of(division: str, url: str) -> str:
    """이 주소가 다른 부문의 정본이면 그 부문 키를, 아니면 빈 문자열."""
    want = expected_division_for_url(url)
    return want if (want is not None and want != division) else ""


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
    # **정본과 어긋난 배치를 저장 전에 막는다.** 남성 솔로와 그룹이 뒤바뀐 채로
    # 수집되면 남성 참가자 순위가 그룹 부문에 들어가는데, 화면에는 이름만 보여
    # 눈으로는 잡히지 않는다.
    for d, u in cleaned.items():
        assert_division_matches_url(d, u)

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
               (dataset_id, source_rank, name, thumbnail_url, win_rate, match_rate,
                song_title, artist_name)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(dataset_id, r["source_rank"], r["name"], r.get("thumbnail_url", ""),
          r["win_rate"], r["match_rate"],
          r.get("song_title", ""), r.get("artist_name", "")) for r in rows])
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

# ── DataTables 요청 계약 ────────────────────────────────────────────────────
#
# 랭킹 페이지(`/w/rank/<id>`)는 표를 **서버사이드 DataTables**로 그린다:
#
#     serverSide: true, ajax: { url: "x.php?u=<id>", type: "POST" }
#
# 즉 표 데이터는 페이지 HTML이 아니라 `POST /w/rank/x.php?u=<id>`가 준다.
# 예전 구현은 `GET ...?page=N`으로 **추측**했고 그래서 아무것도 못 가져왔다.
# 여기서는 실측된 계약을 그대로 쓴다.

_RANK_ID_RE = re.compile(r"^https://www\.piku\.co\.kr/w/rank/([A-Za-z0-9_-]{1,32})/?$")


def ajax_endpoint(page_url: str) -> str:
    """랭킹 **페이지** 주소 → 표 데이터 **엔드포인트** 주소.

    호스트를 고정한 정규식으로만 유도한다 — 임의 URL을 받아 POST를 쏘는 함수가
    되면 그 자체가 SSRF 통로가 된다.
    """
    m = _RANK_ID_RE.match((page_url or "").strip())
    if not m:
        raise PikuError("bad_url", "PIKU 랭킹 주소 형식이 아닙니다.")
    return f"https://www.piku.co.kr/w/rank/x.php?u={m.group(1)}"


#: 표의 열 순서(실측): 순위 · 이미지 · 이름/곡 · 우승 비율 · 승률 · 추이
_COLUMNS = ("rank", "image", "name", "win", "match", "trend")


def datatables_params(*, draw: int, start: int, length: int) -> dict:
    """표준 DataTables 서버사이드 파라미터.

    서버가 정렬 대상을 알아야 하므로 `columns[n][*]`까지 채운다. 정렬은 **순위
    오름차순 고정**이다 — 우리가 원하는 것은 PIKU가 정한 순서 그대로이고,
    정렬을 바꾸면 `source_rank`의 의미가 흔들린다.
    """
    p: dict[str, Any] = {
        "draw": draw,
        "start": start,
        "length": length,
        "search[value]": "",
        "search[regex]": "false",
        "order[0][column]": 0,
        "order[0][dir]": "asc",
    }
    for i, name in enumerate(_COLUMNS):
        p[f"columns[{i}][data]"] = i
        p[f"columns[{i}][name]"] = name
        p[f"columns[{i}][searchable]"] = "false"
        p[f"columns[{i}][orderable]"] = "true" if name == "rank" else "false"
        p[f"columns[{i}][search][value]"] = ""
        p[f"columns[{i}][search][regex]"] = "false"
    return p


async def _fetch_rows(client, endpoint: str, *, draw: int,
                      start: int) -> tuple[int, str, str | None]:
    """표 한 묶음을 가져온다. **우회 수단은 쓰지 않는다.**

    쿠키·세션·프록시·브라우저 위장 없이, 서비스를 밝히는 User-Agent로만 요청한다.
    """
    r = await client.post(
        endpoint,
        data=datatables_params(draw=draw, start=start, length=PAGE_LENGTH),
        headers={"User-Agent": USER_AGENT,
                 "Accept": "application/json, text/javascript",
                 "X-Requested-With": "XMLHttpRequest",
                 "Content-Type": "application/x-www-form-urlencoded"},
        timeout=REQUEST_TIMEOUT)
    retry_after = None
    try:
        retry_after = r.headers.get("Retry-After")
    except Exception:      # noqa: BLE001 — 헤더가 없는 대역 클라이언트도 받는다
        retry_after = None
    return r.status_code, r.text, retry_after


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
    # 응답이 지나치게 크면 파서에 넣지 않는다 — HTML 덤프나 폭주 응답을
    # 정규식으로 훑다가 시간을 다 쓰는 것을 막는 방어선이다.
    if len(t.encode("utf-8", "ignore")) > MAX_RESPONSE_BYTES:
        raise PikuError("too_large", "응답이 너무 큽니다. 반영하지 않습니다.")
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


async def _fetch_all_rows(client, page_url: str, division: str) -> list[dict]:
    """한 부문의 **전체** 행을 가져온다.

    페이지 수를 고정하지 않는다. 첫 응답의 `recordsTotal`이 전체 인원을 알려 주므로
    그 수를 채울 때까지 `start`를 늘려 간다(여성 64명 = 10명씩 7회). 예전 구현은
    1~4페이지로 박아 두어 뒷사람이 통째로 빠졌다.

    **부분 성공을 성공으로 착각하지 않는다** — 도중에 실패하면 예외가 그대로 올라가고
    호출부가 활성화하지 않으므로 직전 정상 dataset이 남는다.
    """
    endpoint = ajax_endpoint(page_url)
    rows_all: list[dict] = []
    total: int | None = None
    start = 0

    for draw in range(1, MAX_REQUESTS_PER_DIVISION + 1):
        attempt = 0
        while True:
            try:
                status, text, retry_after = await _fetch_rows(
                    client, endpoint, draw=draw, start=start)
                _raise_for_status(status, text, retry_after)
                break
            except PikuError as e:
                # 403·challenge·부문 불일치는 **재시도하지 않는다**(거부가 분명하다).
                if e.kind in ("forbidden", "challenge"):
                    raise
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise
                delay = (e.retry_after if e.retry_after is not None
                         else BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
                _log("retry", division=division, start=start, attempt=attempt,
                     kind=e.kind, delaySeconds=delay)
                await asyncio.sleep(min(300.0, max(0.0, delay)))

        payload = extract_payload(text)
        if total is None:
            total = _records_total(payload)
        chunk = normalize_rows(payload)
        if not chunk:
            break
        rows_all.extend(chunk)
        start += PAGE_LENGTH
        if total is not None and len(rows_all) >= total:
            break
        await asyncio.sleep(PAGE_DELAY_SECONDS)
    else:
        raise PikuError("too_many_requests",
                        "요청 상한에 도달했습니다(응답이 끝나지 않습니다).")

    if total is not None and len(rows_all) != total:
        # 서버가 알려 준 전체 수와 실제로 받은 수가 다르면 **부분 수집**이다.
        raise PikuError(
            "incomplete",
            f"전체 {total}건 중 {len(rows_all)}건만 받았습니다. 반영하지 않습니다.")
    return rows_all


def _records_total(payload: Any) -> int | None:
    """DataTables가 알려 주는 전체 행 수. 없으면 `None`(끝까지 읽어 판단한다)."""
    if isinstance(payload, dict):
        for k in ("recordsFiltered", "recordsTotal", "iTotalRecords"):
            v = payload.get(k)
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n >= 0:
                return n
    return None


async def collect_division(division: str, *, client=None,
                           apply: bool = True) -> dict:
    """한 부문을 수집해 **전부 정상일 때만** 활성화한다.

    실패 종류(`forbidden`/`rate_limited`/`challenge`/`parse_failed`/`empty`/
    `incomplete`/`division_mismatch`)와 무관하게, 활성화하지 않으면 **직전 정상
    데이터가 그대로 남는다**.

    `apply=False`는 미리보기 — 검증까지만 하고 DB에 쓰지 않는다.
    """
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    started = int(time.time())
    src = {s["division"]: s for s in await list_sources()}[division]
    if not src["url"]:
        if apply:
            await _record_run(division, started, ok=False, applied=False,
                              error_kind="missing_url")
        raise PikuError("missing_url", "이 부문의 PIKU 주소가 설정되지 않았습니다.")
    if not src["enabled"]:
        if apply:
            await _record_run(division, started, ok=False, applied=False,
                              error_kind="disabled")
        raise PikuError("disabled", "이 부문의 수집이 꺼져 있습니다.")

    # **수집 직전에 다시 교차 검증한다.** 설정 경로를 우회해 DB가 오염된 경우에도
    # 어긋난 데이터가 공개되면 안 된다(남성 순위가 그룹 부문에 들어가는 사고).
    assert_division_matches_url(division, src["url"])

    owns_client = client is None
    if owns_client:
        import httpx
        client = httpx.AsyncClient()

    dataset_id: int | None = None
    rows_all: list[dict] = []
    try:
        rows_all = await _fetch_all_rows(client, src["url"], division)
        valid = validate_rows(rows_all)
        if not apply:
            return {"division": division, "entries": len(valid), "applied": False}

        dataset_id = await _begin_dataset(division, source="scrape",
                                          source_url=src["url"])
        await _fill_dataset(dataset_id, valid)
        await _activate(dataset_id, division, pages=0, entry_count=len(valid))
        await _record_run(division, started, ok=True, applied=True,
                          entries=len(valid))
        await sync_mappings(division)
        return {"division": division, "entries": len(valid), "applied": True}
    except PikuError as e:
        if dataset_id is not None:
            await _discard(dataset_id)
        if apply:
            await _record_run(division, started, ok=False, applied=False,
                              http_status=e.http_status, error_kind=e.kind,
                              entries=len(rows_all))
        _log("collect_failed", division=division, kind=e.kind,
             httpStatus=e.http_status, detail=str(e)[:160])
        raise
    except Exception as e:                       # noqa: BLE001 — 마지막 방어선
        if dataset_id is not None:
            await _discard(dataset_id)
        if apply:
            await _record_run(division, started, ok=False, applied=False,
                              error_kind="unexpected")
        _log("collect_error", division=division, detail=str(e)[:160])
        raise PikuError("unexpected", "수집 중 오류가 발생했습니다.") from e
    finally:
        if owns_client:
            await client.aclose()


async def preview_division(division: str, *, client=None) -> dict:
    """수집해 보되 **저장하지 않는다**(dry-run).

    응답에 우승 비율·승률 숫자를 담지 않는다 — 미리보기라는 이유로 내부값을
    관리 화면에 흘리면 "비율은 정렬에만 쓴다"는 계약이 거기서 깨진다.
    """
    out = await collect_division(division, client=client, apply=False)
    return {**out, "applied": False}


# ── 자동 수집 lock · 상태 ───────────────────────────────────────────────────
#
# 소유권은 **DB의 조건부 UPDATE**가 정한다. 메모리 플래그로 두면 다중 replica와
# 재시작에서 즉시 깨진다(싱드컵 스윕이 같은 이유로 같은 방식을 쓴다).

#: lock 유효 시간(초). 프로세스가 죽어도 이 시간이 지나면 다른 쪽이 이어받는다.
COLLECT_LOCK_TTL_SECONDS = int(os.getenv("PIKU_LOCK_TTL_SECONDS", "1800"))

_lock_owner = f"piku-{os.getpid()}"


async def acquire_collect_lock(*, ttl: int | None = None) -> bool:
    """수집 lock을 잡는다. 이미 유효한 lock이 있으면 `False`."""
    now = int(time.time())
    until = now + (ttl if ttl is not None else COLLECT_LOCK_TTL_SECONDS)
    db = await get_db()
    cur = await db.execute(
        "UPDATE piku_collect_lock SET locked_until = ?, owner = ?"
        " WHERE id = 1 AND locked_until < ?", (until, _lock_owner, now))
    await db.commit()
    return cur.rowcount > 0


async def release_collect_lock() -> None:
    """내가 잡은 lock만 푼다 — 남의 lock을 풀면 동시 실행이 생긴다."""
    db = await get_db()
    await db.execute(
        "UPDATE piku_collect_lock SET locked_until = 0, owner = ''"
        " WHERE id = 1 AND owner = ?", (_lock_owner,))
    await db.commit()


async def worker_state() -> dict:
    db = await get_db()
    r = await (await db.execute(
        "SELECT * FROM piku_worker_state WHERE id = 1")).fetchone()
    if r is None:
        return {"lastSuccessAt": 0, "lastErrorAt": 0, "lastErrorKind": "",
                "consecutiveFailures": 0, "nextRunAt": 0}
    return {"lastSuccessAt": int(r["last_success_at"]),
            "lastErrorAt": int(r["last_error_at"]),
            "lastErrorKind": r["last_error_kind"] or "",
            "consecutiveFailures": int(r["consecutive_failures"]),
            "nextRunAt": int(r["next_run_at"])}


async def _record_worker(*, ok: bool, kind: str = "") -> None:
    now = int(time.time())
    nxt = now + int(MIN_INTERVAL_MINUTES * 60)
    db = await get_db()
    if ok:
        await db.execute(
            "UPDATE piku_worker_state SET last_success_at=?, consecutive_failures=0,"
            " next_run_at=? WHERE id=1", (now, nxt))
    else:
        await db.execute(
            "UPDATE piku_worker_state SET last_error_at=?, last_error_kind=?,"
            " consecutive_failures = consecutive_failures + 1, next_run_at=?"
            " WHERE id=1", (now, kind[:40], nxt))
    await db.commit()


async def collect_all(*, clients: dict | None = None) -> dict:
    """세 부문을 모두 수집한다. **하나라도 실패하면 아무것도 공개하지 않는다.**

    부문별로 따로 활성화하면 "여성은 새 데이터, 그룹은 어제 데이터"인 화면이 되고,
    사용자는 그 사실을 알 수 없다. 그래서 먼저 세 부문을 전부 검증하고
    (`apply=False`), 전부 통과했을 때만 실제로 반영한다.
    """
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}

    for d in DIVISIONS:
        c = (clients or {}).get(d)
        try:
            results[d] = await collect_division(d, client=c, apply=False)
        except PikuError as e:
            errors[d] = e.kind

    if errors:
        _log("collect_all_aborted", failed=list(errors), kinds=list(errors.values()))
        await _record_worker(ok=False, kind=next(iter(errors.values())))
        return {"published": False, "results": results, "errors": errors}

    applied: dict[str, dict] = {}
    for d in DIVISIONS:
        c = (clients or {}).get(d)
        if hasattr(c, "calls"):
            c.calls.clear()          # 대역 클라이언트 재사용 시 호출 기록 초기화
        applied[d] = await collect_division(d, client=c, apply=True)
    await _record_worker(ok=True)
    _log("collect_all_published", entries={d: applied[d]["entries"] for d in applied})
    return {"published": True, "results": applied, "errors": {}}


async def preview_rows(division: str, raw: Any) -> dict:
    """**저장하지 않고** 검증만 한다(dry-run).

    수집이든 수동 import든, 먼저 이걸로 형태를 확인한 뒤 반영한다. 활성 dataset을
    건드리지 않으므로 실패해도 마지막 정상 데이터가 그대로 남는다.

    응답에 **우승 비율·승률 숫자를 담지 않는다.** 미리보기라는 이유로 내부값을
    화면에 흘리면 "공개 화면에 비율을 쓰지 않는다"는 계약이 관리 화면에서 깨진다.
    검증에 필요한 것은 "몇 건이 통과했고 무엇이 걸렸는가"이지 값 자체가 아니다.
    """
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    rows = validate_rows(normalize_rows(raw))
    known = {q["name"] for q in qualifier_names(division)}
    names = [r["name"] for r in rows]
    return {
        "division": division,
        "entries": len(rows),
        "applied": False,
        # 이름만 돌려준다 — 매핑이 붙을지 미리 보는 것이 목적이다.
        "matched": sorted(n for n in names if n in known),
        "unmatched": sorted(n for n in names if n not in known),
        "duplicates": sorted({n for n in names if names.count(n) > 1}),
    }


def qualifier_names(division: str) -> list[dict]:
    """이 부문 공식 참가자 이름 — preview가 매칭 여부를 미리 보여 줄 때 쓴다."""
    import singcup_qualifiers as sq
    if division == "groups":
        return [{"name": m["name"]} for g in sq.QUALIFIERS["groups"]
                for m in g["members"]]
    return [{"name": q["name"]} for q in sq.QUALIFIERS.get(division, [])]


async def import_rows(division: str, raw: Any) -> dict:
    """관리자 수동 import — 수집과 **같은 검증·같은 원자 교체**를 거친다.

    외부 접속 없이 데이터를 넣는 경로다. 수집이 막혀 있을 때의 대체 수단이자,
    파서를 실제 응답으로 검증하는 수단이기도 하다.
    반영 전에 `preview_rows`로 먼저 확인할 수 있다.
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
                  e.song_title, e.artist_name,
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
        # 곡·가수는 화면 2줄 표시에 쓰는 **공개 정보**다(비율과 다르다).
        "songTitle": r["song_title"] or "",
        "artistName": r["artist_name"] or "",
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
        **await worker_state(),
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
