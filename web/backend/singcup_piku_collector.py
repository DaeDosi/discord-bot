"""브라우저 기반 PIKU Collector — 수신 · 검증 · draft · 원자 Publish.

**왜 이 모듈이 있는가.** Railway와 AWS 서울 EC2에서 PIKU에 접속하면 둘 다
HTTP 403이 온다. 우회(프록시·UA 위장·쿠키 재사용·CAPTCHA 대응)는 하지 않기로
했으므로, PIKU가 정상적으로 열리는 **사용자 브라우저**가 이미 렌더된 공개 표를
읽어 우리에게 보내는 경로로 바꾼다. 서버는 그 결과를 받기만 하고, PIKU에
직접 요청하지 않는다.

**받는 것과 받지 않는 것.**
  · 받는다  — 정규화된 랭킹 행(순위·이름·곡·가수·비율 두 개·이미지 URL)
  · 안 받는다 — 쿠키·세션·헤더·원문 HTML. 스키마 자체에 자리를 두지 않는다.

**이름이 겹치는 함정.** PIKU 표의 `win_rate`는 *승률*이고 우리 DB의 `win_rate`는
*우승 비율*이다. 뜻이 반대인데 이름이 같다. 기존 `normalize_rows`는
`row["winRate"] or row["win_rate"]`를 읽으므로 원본 행을 그대로 넘기면 두 값이
조용히 뒤바뀐다. 그래서 이 모듈은 그 함수를 **쓰지 않고** 명시적으로 번역한다:

    PIKU win_ratio → 내부 win_rate   (우승 비율)
    PIKU win_rate  → 내부 match_rate (승률)

**공개까지 두 단계.** 수신은 draft에서 멈춘다. 세 부문이 모두 검증되고 운영자가
Publish를 눌렀을 때만 한 번에 활성화한다. 한 부문만 새 데이터가 보이는 상태는
만들지 않는다.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any

import singcup_piku as piku
from singcup_piku import PikuError

from database import get_db

DIVISIONS = ("female_solo", "male_solo", "groups")

#: 부문 ↔ 정본 source id. **male과 groups를 뒤바꾸지 말 것** — 한 번 뒤집힌 적이
#: 있고, 값이 섞이면 화면에서는 정상으로 보이면서 순위만 통째로 틀어진다.
SOURCE_IDS: dict[str, str] = {
    "female_solo": "8jGsHE",
    "male_solo":   "7PqH44",
    "groups":      "7fXoNs",
}
SOURCE_URLS: dict[str, str] = {
    d: f"https://www.piku.co.kr/w/rank/{sid}" for d, sid in SOURCE_IDS.items()
}

#: 부문별 기대 행 수. 공식 발표 기준이며, 모자라거나 넘치면 받지 않는다.
EXPECTED_ROWS: dict[str, int] = {"female_solo": 64, "male_solo": 64, "groups": 32}

SCHEMA_VERSION = 1

#: 수신 스키마에 **허용된 키만** 둔다. 쿠키·헤더·원문 HTML은 자리 자체가 없다 —
#: "보내지 말라"는 규칙보다 "받을 곳이 없다"가 확실하다.
ALLOWED_PAYLOAD_KEYS = frozenset({
    "schemaVersion", "division", "sourceId", "sourceUrl", "collectedAt",
    "rowCount", "rows",
})
ALLOWED_ROW_KEYS = frozenset({
    "rank", "streamer", "song_title", "artist", "win_ratio", "win_rate",
    "image_url",
})

#: 자동 수집 최소 간격. 요구가 "1시간마다"이므로 하한을 60분으로 둔다.
MIN_INTERVAL_MINUTES = max(60, int(os.getenv("PIKU_COLLECTOR_INTERVAL_MIN", "60")))

#: 한 번에 받을 수 있는 최대 행 수(폭주 방지). 기대치의 넉넉한 배수.
MAX_ROWS = 500
#: 문자열 하나의 상한. 원문 HTML이 이름 자리에 실려 오는 것을 막는다.
MAX_TEXT = 200


def auto_publish_enabled() -> bool:
    """수집이 끝나면 자동으로 공개할 것인가. **기본 꺼짐.**

    켜져 있으면 사람이 한 번도 보지 않은 데이터가 그대로 공개된다. 이름 매핑이
    확정되지 않은 상태에서 공개되면 순위가 비어 보이거나 엉뚱한 프로필이 붙는다.
    """
    return os.getenv("PIKU_AUTO_PUBLISH_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


def _log(event: str, **fields: Any) -> None:
    """구조화 로그. **본문·쿠키·헤더는 넣지 않는다** — 남길 값은 개수와 종류뿐이다."""
    print(json.dumps({"event": f"piku_collector_{event}", **fields},
                     ensure_ascii=False), flush=True)


# ── 대표자 규칙 ─────────────────────────────────────────────────────────────
def group_lead(streamer: str) -> str:
    """그룹 행의 대표자 — **쉼표로 나눈 첫 번째 비어 있지 않은 이름**.

    PIKU의 그룹 `streamer`는 팀원 전체 문자열이다("조별하, 김니디, 슈향, 이 선").
    공식 명단의 `memberOrder`를 쓰지 않는다 — 사용자가 확정한 규칙은 "PIKU 문자열의
    첫 사람"이고, 두 순서가 다를 수 있다. 유사도 매칭도 하지 않는다.
    """
    for part in (streamer or "").split(","):
        name = " ".join(part.split())      # 내부 연속 공백도 한 칸으로
        if name:
            return name
    return ""


# ── 검증 ────────────────────────────────────────────────────────────────────
def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise PikuError("parse_failed", f"{field}가 문자열이 아닙니다.")
    s = " ".join(value.split())
    if not s:
        raise PikuError("parse_failed", f"{field}가 비어 있습니다.")
    if len(s) > MAX_TEXT:
        raise PikuError("parse_failed", f"{field}가 너무 깁니다.")
    return s


def _ratio(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PikuError("bad_rate", f"{field}가 숫자가 아닙니다.")
    v = float(value)
    if not math.isfinite(v):
        raise PikuError("bad_rate", f"{field}가 유한한 값이 아닙니다.")
    if not (0.0 <= v <= 100.0):
        raise PikuError("bad_rate", f"{field}가 범위를 벗어났습니다({v}).")
    return v


def parse_payload(body: Any) -> dict:
    """브라우저가 보낸 페이로드를 검증해 **내부 행 형식**으로 바꾼다.

    실패하면 예외다. 부분 성공을 만들지 않는다 — 한 행이라도 이상하면 그 수집은
    통째로 버린다(개수 계약이 있으므로 "유효한 것만 남기기"가 오히려 위험하다).
    """
    if not isinstance(body, dict):
        raise PikuError("parse_failed", "페이로드 형식이 올바르지 않습니다.")

    unknown = set(body) - ALLOWED_PAYLOAD_KEYS
    if unknown:
        # 쿠키·HTML이 딸려 오는 것을 여기서 끊는다.
        raise PikuError("parse_failed",
                        f"허용하지 않는 항목이 있습니다: {sorted(unknown)[:3]}")

    if body.get("schemaVersion") != SCHEMA_VERSION:
        raise PikuError("schema_changed",
                        "수집 형식이 서버가 아는 버전과 다릅니다.")

    division = body.get("division")
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")

    if body.get("sourceId") != SOURCE_IDS[division]:
        raise PikuError("bad_source", "부문과 출처 ID가 맞지 않습니다.")
    if body.get("sourceUrl") != SOURCE_URLS[division]:
        raise PikuError("bad_source", "부문과 출처 주소가 맞지 않습니다.")

    rows = body.get("rows")
    if not isinstance(rows, list):
        raise PikuError("parse_failed", "행 목록을 찾지 못했습니다.")
    if not rows:
        raise PikuError("empty", "수집 결과가 비어 있습니다.")
    if len(rows) > MAX_ROWS:
        raise PikuError("too_large", "행이 너무 많습니다.")

    expected = EXPECTED_ROWS[division]
    if len(rows) != expected:
        raise PikuError("row_count",
                        f"{expected}행이어야 하는데 {len(rows)}행입니다.")
    declared = body.get("rowCount")
    if declared is not None and declared != len(rows):
        raise PikuError("row_count", "선언한 행 수와 실제 행 수가 다릅니다.")

    out: list[dict] = []
    seen_names: set[str] = set()
    seen_ranks: set[int] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise PikuError("parse_failed", "행 형식이 올바르지 않습니다.")
        extra = set(raw) - ALLOWED_ROW_KEYS
        if extra:
            raise PikuError("parse_failed",
                            f"행에 허용하지 않는 항목이 있습니다: {sorted(extra)[:3]}")

        rank = raw.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
            raise PikuError("parse_failed", "순위를 읽지 못한 행이 있습니다.")
        if rank in seen_ranks:
            raise PikuError("duplicate_rank", f"순위 {rank}이(가) 중복됩니다.")
        seen_ranks.add(rank)

        streamer = _text(raw.get("streamer"), "스트리머")
        # 그룹만 대표자를 뽑는다. 솔로 이름에 쉼표가 있어도 쪼개지 않는다.
        if division == "groups":
            name = group_lead(streamer)
            if not name:
                raise PikuError("missing_lead", "대표자를 찾지 못한 팀이 있습니다.")
        else:
            name = streamer
        if name in seen_names:
            raise PikuError("duplicate_name", f"'{name}'이(가) 중복됩니다.")
        seen_names.add(name)

        out.append({
            "source_rank": rank,
            "name": name,
            "team_members": streamer if division == "groups" else "",
            "song_title": _text(raw.get("song_title"), "노래 제목"),
            "artist_name": _text(raw.get("artist"), "가수"),
            # ⚠️ 여기가 뒤바뀌기 쉬운 지점이다. 위 모듈 주석 참조.
            "win_rate": _ratio(raw.get("win_ratio"), "우승 비율"),
            "match_rate": _ratio(raw.get("win_rate"), "승률"),
            "thumbnail_url": _image(raw.get("image_url")),
        })

    missing = sorted(set(range(1, expected + 1)) - seen_ranks)
    if missing:
        raise PikuError("rank_gap", f"순위가 빠졌습니다: {missing[:3]}")

    out.sort(key=lambda r: r["source_rank"])
    return {"division": division, "sourceId": body["sourceId"],
            "sourceUrl": body["sourceUrl"],
            "collectedAt": body.get("collectedAt") or "", "rows": out}


def _image(value: Any) -> str:
    """이미지 URL — 없으면 빈 문자열. http(s)가 아니면 버린다(데이터 URI 차단)."""
    if not value:
        return ""
    if not isinstance(value, str):
        raise PikuError("parse_failed", "이미지 주소가 문자열이 아닙니다.")
    s = value.strip()
    if len(s) > 500 or not s.startswith(("https://", "http://")):
        raise PikuError("parse_failed", "이미지 주소 형식이 올바르지 않습니다.")
    return s


# ── 상태 저장 ───────────────────────────────────────────────────────────────
async def _ensure_tables() -> None:
    """Collector 전용 테이블. append-only 원칙대로 `IF NOT EXISTS`만 쓴다."""
    db = await get_db()
    await db.execute(
        """CREATE TABLE IF NOT EXISTS piku_collector_state (
               division        TEXT PRIMARY KEY,
               last_result     TEXT    NOT NULL DEFAULT '',
               last_error_kind TEXT    NOT NULL DEFAULT '',
               last_at         INTEGER NOT NULL DEFAULT 0,
               row_count       INTEGER NOT NULL DEFAULT 0,
               draft_id        INTEGER
           )""")
    # 그룹의 전체 팀원 문자열. `piku_entries`에는 대표자만 들어가므로 원본을
    # 여기에 남긴다 — 운영자가 공식 명단과 대조할 때 필요하다.
    await db.execute(
        """CREATE TABLE IF NOT EXISTS piku_collector_teams (
               division     TEXT NOT NULL,
               piku_name    TEXT NOT NULL,
               team_members TEXT NOT NULL DEFAULT '',
               PRIMARY KEY (division, piku_name)
           )""")
    await db.commit()


async def debug_counts() -> dict:
    """DB write가 있었는지 비교하기 위한 행 수 스냅샷(테스트용)."""
    await _ensure_tables()
    db = await get_db()
    out = {}
    for t in ("piku_datasets", "piku_entries", "piku_collector_state"):
        cur = await db.execute(f"SELECT count(*) FROM {t}")
        out[t] = (await cur.fetchone())[0]
    return out


async def preview(body: Any) -> dict:
    """검증만 하고 **아무것도 쓰지 않는다.**

    형식이 틀려도 기존 데이터가 그대로 남는다는 것이 이 단계의 존재 이유다.
    """
    parsed = parse_payload(body)
    division = parsed["division"]
    current = await piku.active_dataset(division)
    return {
        "division": division,
        "rowCount": len(parsed["rows"]),
        "expected": EXPECTED_ROWS[division],
        "applied": False,
        "hasActive": current is not None,
        # 비율은 담지 않는다 — 관리 화면에서도 값은 쓰지 않는다.
        "sample": [{"rank": r["source_rank"], "name": r["name"],
                    "songTitle": r["song_title"], "artistName": r["artist_name"]}
                   for r in parsed["rows"][:3]],
    }


async def save_draft(body: Any) -> dict:
    """검증 후 **draft로만** 저장한다. 공개 데이터는 그대로다.

    draft는 `piku_datasets`의 `building` 상태를 쓴다 — 공개 조회가
    `status='active'`만 보므로 격리는 상태 하나로 이미 성립한다.
    같은 부문 draft가 이미 있으면 **갈아 끼운다**(쌓이지 않는다).
    """
    parsed = parse_payload(body)
    return await _store_draft(parsed["division"], parsed["rows"],
                              source="browser_collector",
                              source_url=parsed["sourceUrl"])


async def _store_draft(division: str, rows: list[dict], *,
                       source: str, source_url: str) -> dict:
    """draft 저장의 **단 하나의 경로.** 브라우저 수집과 수동 import가 함께 쓴다.

    같은 부문 draft가 이미 있으면 갈아 끼운다(쌓이지 않는다). 그룹의 전체 팀
    문자열은 `piku_entries`에 넣을 자리가 없으므로 옆 테이블에 보관한다 —
    공개 연결은 대표자만 쓰지만, 운영자가 대조하려면 원본이 남아 있어야 한다.
    """
    await _ensure_tables()
    old = await _draft_id(division)
    if old is not None:
        await piku._discard(old)

    dataset_id = await piku._begin_dataset(
        division, source=source, source_url=source_url)
    try:
        await piku._fill_dataset(dataset_id, rows)
    except Exception:
        await piku._discard(dataset_id)
        raise

    db = await get_db()
    now = int(time.time())
    await db.execute("DELETE FROM piku_collector_teams WHERE division=?",
                     (division,))
    for r in rows:
        team = (r.get("team_members") or "").strip()
        if team:
            await db.execute(
                "INSERT OR REPLACE INTO piku_collector_teams"
                " (division, piku_name, team_members) VALUES (?,?,?)",
                (division, r["name"], team))
    await db.execute(
        """INSERT INTO piku_collector_state
               (division, last_result, last_error_kind, last_at, row_count, draft_id)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(division) DO UPDATE SET
               last_result=excluded.last_result, last_error_kind='',
               last_at=excluded.last_at, row_count=excluded.row_count,
               draft_id=excluded.draft_id""",
        (division, "draft", "", now, len(rows), dataset_id))
    await db.commit()
    _log("draft_saved", division=division, rows=len(rows), source=source)
    return {"division": division, "draftId": dataset_id,
            "rowCount": len(rows), "published": False}


async def _draft_id(division: str) -> int | None:
    await _ensure_tables()
    db = await get_db()
    cur = await db.execute(
        "SELECT draft_id FROM piku_collector_state WHERE division=?", (division,))
    row = await cur.fetchone()
    if not row or row[0] is None:
        return None
    # 실제로 남아 있는지 확인한다(버려졌을 수 있다).
    cur = await db.execute(
        "SELECT id FROM piku_datasets WHERE id=? AND status='building'", (row[0],))
    return int(row[0]) if await cur.fetchone() else None


async def _activate_draft(division: str, dataset_id: int, rows: int) -> None:
    """draft 하나를 활성으로 올린다. **호출부가 세 부문을 모두 확인한 뒤에만** 부른다."""
    await piku._activate(dataset_id, division, pages=0, entry_count=rows)


async def publish_drafts() -> dict:
    """세 부문 draft를 **한 번에** 공개한다.

    하나라도 없거나 실패하면 아무것도 바꾸지 않는다 — "여성만 새 데이터"인 화면은
    사용자가 그 사실을 알 수 없어서 가장 위험하다. 자동으로 부르지 않는다.
    """
    await _ensure_tables()
    drafts: dict[str, int] = {}
    for d in DIVISIONS:
        did = await _draft_id(d)
        if did is None:
            raise PikuError("missing_draft",
                            f"{piku.DIVISION_LABELS[d]} 수집본이 없습니다.")
        drafts[d] = did

    db = await get_db()
    counts: dict[str, int] = {}
    for d, did in drafts.items():
        cur = await db.execute(
            "SELECT count(*) FROM piku_entries WHERE dataset_id=?", (did,))
        n = (await cur.fetchone())[0]
        if n != EXPECTED_ROWS[d]:
            raise PikuError("incomplete",
                            f"{piku.DIVISION_LABELS[d]} 행 수가 맞지 않습니다({n}).")
        counts[d] = n

    # **매핑이 다 확정되지 않으면 공개하지 않는다.** `public_ranking`이
    # `confirmed`만 순위에 넣으므로, 확정 전에 공개하면 화면이 통째로 빈다.
    blockers = await publish_blockers()
    if blockers:
        raise PikuError("unconfirmed", " · ".join(blockers))

    # 되돌리기 위해 현재 활성본을 먼저 기억한다.
    previous = {d: (await piku.active_dataset(d)) for d in DIVISIONS}
    done: list[str] = []
    try:
        for d, did in drafts.items():
            await _activate_draft(d, did, counts[d])
            done.append(d)
    except Exception:
        # 이미 올린 부문을 되돌린다 — 부분 공개 상태로 남기지 않는다.
        for d in done:
            await db.execute(
                "UPDATE piku_datasets SET status='building' WHERE id=?", (drafts[d],))
            prev = previous.get(d)
            if prev:
                await db.execute(
                    "UPDATE piku_datasets SET status='active' WHERE id=?", (prev["id"],))
        await db.commit()
        _log("publish_rolled_back", divisions=done)
        raise

    for d in DIVISIONS:
        await db.execute(
            "UPDATE piku_collector_state SET draft_id=NULL, last_result='published' "
            "WHERE division=?", (d,))
    await db.commit()
    for d in DIVISIONS:
        await piku.sync_mappings(d)
    _log("published", rows=counts)
    return {"published": True, "rows": counts}


async def record_client_failure(division: str, kind: str) -> dict:
    """브라우저 쪽 실패(차단 화면·CAPTCHA·미렌더·중단)를 **실패로** 남긴다.

    성공으로 위장하지 않는 것이 핵심이다 — 실패를 조용히 넘기면 오래된 데이터가
    최신인 것처럼 보인다.
    """
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    await _ensure_tables()
    db = await get_db()
    now = int(time.time())
    await db.execute(
        """INSERT INTO piku_collector_state
               (division, last_result, last_error_kind, last_at, row_count)
           VALUES (?,?,?,?,0)
           ON CONFLICT(division) DO UPDATE SET
               last_result='failed', last_error_kind=excluded.last_error_kind,
               last_at=excluded.last_at, row_count=0""",
        (division, "failed", str(kind)[:40], now))
    await db.commit()
    _log("client_failed", division=division, kind=str(kind)[:40])
    return {"division": division, "lastResult": "failed"}


async def status() -> dict:
    """관리 화면용 상태. **비율값과 원문은 담지 않는다.**"""
    await _ensure_tables()
    db = await get_db()
    rows = {r["division"]: dict(r) for r in await (await db.execute(
        "SELECT * FROM piku_collector_state")).fetchall()}
    out: dict[str, Any] = {
        "autoCollectEnabled": piku.auto_collect_enabled(),
        "autoPublishEnabled": auto_publish_enabled(),
        "minIntervalMinutes": MIN_INTERVAL_MINUTES,
        "divisions": {},
    }
    ready = True
    for d in DIVISIONS:
        r = rows.get(d) or {}
        did = await _draft_id(d)
        n = 0
        if did is not None:
            cur = await db.execute(
                "SELECT count(*) FROM piku_entries WHERE dataset_id=?", (did,))
            n = (await cur.fetchone())[0]
        active = await piku.active_dataset(d)
        ok = did is not None and n == EXPECTED_ROWS[d]
        ready = ready and ok
        out["divisions"][d] = {
            "label": piku.DIVISION_LABELS[d],
            "sourceId": SOURCE_IDS[d], "sourceUrl": SOURCE_URLS[d],
            "expected": EXPECTED_ROWS[d],
            "lastResult": r.get("last_result") or None,
            "lastErrorKind": r.get("last_error_kind") or "",
            "lastAt": r.get("last_at") or 0,
            "rowCount": r.get("row_count") or 0,
            "draftRows": n,
            "draftCount": 1 if did is not None else 0,
            "activeEntryCount": (active or {}).get("entry_count") or 0,
            "draftReady": ok,
        }
    # 준비 여부만 주면 운영자는 무엇을 더 해야 하는지 알 수 없다.
    out["blockers"] = await publish_blockers()
    out["publishReady"] = ready and not out["blockers"]
    return out


# ── 브라우저 토큰 ───────────────────────────────────────────────────────────
#
# 확장 프로그램에 **어떤 secret도 넣지 않는다.** 대신 운영자가 Nexadmin에서
# 버튼을 눌러 그때마다 **짧고 한 번만 쓰는 토큰**을 발급받아 확장에 넘긴다.
#
# 이렇게 하는 이유:
#   · 확장 번들이 유출돼도 재사용할 값이 없다(발급 시점에만 존재).
#   · 토큰이 새어도 수명이 짧고 1회용이라 피해 범위가 닫힌다.
#   · 부문까지 묶어 두면 여성용 토큰으로 그룹 데이터를 밀어 넣을 수 없다.
#
# 저장은 **해시만** 한다 — DB가 유출돼도 토큰 원문을 복원할 수 없다.

#: 토큰 수명(초). 사람이 버튼을 누르고 수집이 끝나기까지면 충분하다.
TOKEN_TTL_SECONDS = max(60, min(3600,
                                int(os.getenv("PIKU_COLLECTOR_TOKEN_TTL", "600"))))


def _hash_token(raw: str) -> str:
    import hashlib
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _ensure_token_table() -> None:
    db = await get_db()
    await db.execute(
        """CREATE TABLE IF NOT EXISTS piku_collector_tokens (
               token_hash TEXT PRIMARY KEY,
               division   TEXT    NOT NULL,
               expires_at INTEGER NOT NULL,
               used_at    INTEGER NOT NULL DEFAULT 0,
               created_at INTEGER NOT NULL
           )""")
    await db.commit()


async def issue_token(division: str) -> dict:
    """수집 토큰 발급. **원문은 이 반환값에서 딱 한 번만 나온다.**"""
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    import secrets
    await _ensure_token_table()
    raw = secrets.token_urlsafe(32)
    now = int(time.time())
    db = await get_db()
    await db.execute(
        "INSERT INTO piku_collector_tokens (token_hash, division, expires_at,"
        " created_at) VALUES (?,?,?,?)",
        (_hash_token(raw), division, now + TOKEN_TTL_SECONDS, now))
    await db.commit()
    _log("token_issued", division=division, ttl=TOKEN_TTL_SECONDS)
    return {"token": raw, "division": division,
            "expiresAt": now + TOKEN_TTL_SECONDS,
            "ttlSeconds": TOKEN_TTL_SECONDS}


async def consume_token(raw: str, division: str) -> None:
    """토큰을 **한 번만** 통과시킨다. 만료·재사용·부문 불일치는 전부 거부."""
    if not raw or not isinstance(raw, str):
        raise PikuError("bad_token", "수집 토큰이 없습니다.")
    await _ensure_token_table()
    db = await get_db()
    now = int(time.time())
    # 조건부 UPDATE의 rowcount로 소비를 판정한다 — SELECT 후 UPDATE로 나누면
    # 두 요청이 같은 토큰을 동시에 통과할 수 있다.
    cur = await db.execute(
        "UPDATE piku_collector_tokens SET used_at=? "
        "WHERE token_hash=? AND division=? AND used_at=0 AND expires_at>?",
        (now, _hash_token(raw), division, now))
    await db.commit()
    if not cur.rowcount:
        raise PikuError("bad_token", "토큰이 만료됐거나 이미 사용됐습니다.")


async def purge_expired_tokens() -> int:
    """만료 토큰 정리. 남겨 둘 이유가 없다."""
    await _ensure_token_table()
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM piku_collector_tokens WHERE expires_at < ?",
        (int(time.time()) - 3600,))
    await db.commit()
    return cur.rowcount or 0


# ── 이름 매핑 ───────────────────────────────────────────────────────────────
#
# **이 구간이 지금까지 가장 큰 차단 조건이었다.** `sync_mappings`는 정확 일치도
# `suggested`까지만 만들고 `public_ranking`은 `confirmed`만 순위에 넣는다. 그래서
# 데이터를 잘 받아 Publish해도 공개 순위가 통째로 비었다.
#
# 자동 확정은 하지 않는다 — 한 글자 차이로 다른 스트리머에게 붙으면 순위가
# 통째로 틀어지고 그 오류는 화면에서 보이지 않는다. 대신 **정확히 일치한 것만
# 골라 운영자가 한 번에 확정**할 수 있게 한다(유사도 매칭은 어디에도 없다).

def _official_index(division: str) -> dict[str, str]:
    """부문 안의 `정규화된 이름 → channel_id`.

    **그룹은 팀의 모든 멤버를 담는다.** 대표자는 PIKU 문자열의 첫 이름이고, 공식
    명단의 표기 순서는 그와 다를 수 있다. 예전에는 공식 첫 멤버만 인덱스에 넣어서,
    두 순서가 반대인 팀은 대표자를 제대로 뽑고도 연결할 곳이 없어 전부 미매칭이
    됐다(실측: 32팀 중 32팀 미매칭). 이름은 **그 사람 본인의 channel_id**로
    연결한다 — 팀의 첫 멤버가 아니다.
    """
    import singcup_qualifiers as sq
    out: dict[str, str] = {}
    if division == "groups":
        for g in sq.QUALIFIERS["groups"]:
            for m in (g.get("members") or []):
                out.setdefault(piku._norm_name(m["name"]), m["channelId"])
    else:
        for r in sq.QUALIFIERS[division]:
            out.setdefault(piku._norm_name(r["name"]), r["channelId"])
    return out


def _official_names_by_channel(division: str) -> dict[str, str]:
    """`channel_id → 공식 표기 이름`.

    그룹도 **모든 멤버**를 담는다. 후보 목록이 첫 멤버만 담으면 운영자가 PIKU
    대표자를 고를 수 없다(그 사람이 목록에 아예 없다).
    """
    import singcup_qualifiers as sq
    out: dict[str, str] = {}
    if division == "groups":
        for g in sq.QUALIFIERS["groups"]:
            for m in (g.get("members") or []):
                out[m["channelId"]] = m["name"]
    else:
        for r in sq.QUALIFIERS[division]:
            out[r["channelId"]] = r["name"]
    return out


async def _draft_rows(division: str) -> list[dict]:
    did = await _draft_id(division)
    if did is None:
        return []
    db = await get_db()
    return [dict(r) for r in await (await db.execute(
        "SELECT source_rank, name, song_title, artist_name, thumbnail_url"
        " FROM piku_entries WHERE dataset_id=? ORDER BY source_rank", (did,)
    )).fetchall()]


async def _team_strings(division: str) -> dict[str, str]:
    await _ensure_tables()
    db = await get_db()
    return {r[0]: r[1] for r in await (await db.execute(
        "SELECT piku_name, team_members FROM piku_collector_teams"
        " WHERE division=?", (division,))).fetchall()}


async def draft_mappings(division: str) -> dict:
    """**draft 기준** 매핑 목록. 활성본이 아니라 지금 공개하려는 것을 본다.

    응답에 우승 비율·승률을 담지 않는다 — 관리 목록에서도 값은 쓰지 않는다.
    """
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    rows = await _draft_rows(division)
    empty = {"confirmed": 0, "suggested": 0, "unmatched": 0, "duplicate": 0}
    if not rows:
        return {"division": division, "label": piku.DIVISION_LABELS[division],
                "expected": EXPECTED_ROWS[division], "rows": [], "counts": empty}

    db = await get_db()
    saved = {r["piku_name"]: dict(r) for r in await (await db.execute(
        "SELECT piku_name, channel_id, state FROM piku_mappings WHERE division=?",
        (division,))).fetchall()}
    teams = await _team_strings(division)
    index = _official_index(division)
    names_by_ch = _official_names_by_channel(division)

    # 같은 공식 참가자에 두 행이 붙었는지 — 확정된 것끼리만 센다.
    used: dict[str, int] = {}
    for r in rows:
        m = saved.get(r["name"])
        if m and m["state"] == "confirmed" and m["channel_id"]:
            used[m["channel_id"]] = used.get(m["channel_id"], 0) + 1

    out: list[dict] = []
    counts = dict(empty)
    for r in rows:
        name = r["name"]
        m = saved.get(name)
        state = (m or {}).get("state") or "unmapped"
        channel = (m or {}).get("channel_id")
        # **저장된 행이 아예 없을 때만** 정확 일치를 제안한다(확정하지 않는다).
        # 운영자가 명시적으로 해제한 행(state='unmapped'로 저장됨)을 다시
        # 제안하면 그 해제가 되돌려져, 지운 연결이 조용히 살아난다.
        if m is None:
            exact = index.get(piku._norm_name(name))
            if exact:
                state, channel = "suggested", exact
        dup = bool(channel and state == "confirmed" and used.get(channel, 0) > 1)
        if dup:
            counts["duplicate"] += 1
        elif state == "confirmed":
            counts["confirmed"] += 1
        elif state == "suggested":
            counts["suggested"] += 1
        else:
            counts["unmatched"] += 1
        out.append({
            "rank": r["source_rank"],
            "pikuName": name,
            "teamMembers": teams.get(name, ""),
            "lead": name,          # 그룹은 저장 시점에 이미 대표자로 정규화됐다
            "songTitle": r["song_title"] or "",
            "artistName": r["artist_name"] or "",
            "state": state,
            "channelId": channel,
            "officialName": names_by_ch.get(channel or "", ""),
            "duplicate": dup,
        })
    return {"division": division, "label": piku.DIVISION_LABELS[division],
            "expected": EXPECTED_ROWS[division], "rows": out, "counts": counts}


async def official_candidates(division: str) -> list[dict]:
    """후보 검색용 공식 명단. 이미 확정에 쓰인 채널은 표시해 둔다."""
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    db = await get_db()
    taken = {r[0] for r in await (await db.execute(
        "SELECT channel_id FROM piku_mappings"
        " WHERE division=? AND state='confirmed' AND channel_id IS NOT NULL",
        (division,))).fetchall()}
    return [{"channelId": cid, "name": nm, "taken": cid in taken}
            for cid, nm in _official_names_by_channel(division).items()]


async def _write_mapping(division: str, piku_name: str,
                         channel_id: str | None, state: str) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO piku_mappings (division, piku_name, channel_id, state,
                                      updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(division, piku_name) DO UPDATE SET
               channel_id=excluded.channel_id, state=excluded.state,
               updated_at=excluded.updated_at""",
        (division, piku_name, channel_id, state, int(time.time())))


async def set_mapping(division: str, piku_name: str,
                      channel_id: str | None) -> dict:
    """한 행의 매핑을 **확정**하거나 해제한다.

    `channel_id`가 없으면 `unmapped`로 되돌린다. 다른 부문의 채널이나 이미 다른
    행이 쓰고 있는 채널은 거부한다 — 두 PIKU 행이 한 참가자에 붙으면 순위가
    조용히 어긋난다.
    """
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    db = await get_db()
    if channel_id is None:
        await _write_mapping(division, piku_name, None, "unmapped")
        await db.commit()
        return {"division": division, "pikuName": piku_name, "state": "unmapped"}

    if channel_id not in _official_names_by_channel(division):
        raise PikuError("not_qualifier", "이 부문의 공식 참가자가 아닙니다.")
    cur = await db.execute(
        "SELECT piku_name FROM piku_mappings"
        " WHERE division=? AND channel_id=? AND state='confirmed'"
        " AND piku_name<>?", (division, channel_id, piku_name))
    other = await cur.fetchone()
    if other:
        raise PikuError("duplicate_channel",
                        f"'{other[0]}'이(가) 이미 같은 참가자에 연결돼 있습니다.")
    await _write_mapping(division, piku_name, channel_id, "confirmed")
    await db.commit()
    return {"division": division, "pikuName": piku_name, "state": "confirmed"}


async def confirm_exact(division: str) -> dict:
    """**정확히 일치한 것만** 한 번에 확정한다.

    유사도·부분 일치는 대상이 아니다. 하나라도 실패하면 전부 되돌린다 — 절반만
    확정된 상태는 운영자가 무엇을 더 해야 하는지 알 수 없게 만든다.
    """
    m = await draft_mappings(division)
    targets = [r for r in m["rows"] if r["state"] == "suggested" and r["channelId"]]
    if not targets:
        return {"division": division, "confirmed": 0}

    db = await get_db()
    before = {r["piku_name"]: dict(r) for r in await (await db.execute(
        "SELECT piku_name, channel_id, state FROM piku_mappings WHERE division=?",
        (division,))).fetchall()}
    done: list[str] = []
    try:
        seen: set[str] = set()
        for r in targets:
            cid = r["channelId"]
            if cid in seen:
                raise PikuError("duplicate_channel",
                                f"'{r['pikuName']}'이(가) 중복 참가자를 가리킵니다.")
            seen.add(cid)
            await _write_mapping(division, r["pikuName"], cid, "confirmed")
            done.append(r["pikuName"])
        await db.commit()
    except Exception:
        # 되돌린다 — 없던 행은 지우고, 있던 행은 이전 값으로.
        for n in done:
            prev = before.get(n)
            if prev:
                await _write_mapping(division, n, prev["channel_id"], prev["state"])
            else:
                await db.execute(
                    "DELETE FROM piku_mappings WHERE division=? AND piku_name=?",
                    (division, n))
        await db.commit()
        _log("confirm_rolled_back", division=division, count=len(done))
        raise
    _log("confirmed_exact", division=division, count=len(done))
    return {"division": division, "confirmed": len(done)}


# ── Publish 게이트 · Preview ────────────────────────────────────────────────
async def publish_blockers() -> list[str]:
    """공개를 막고 있는 **구체적인 이유**들. 비어 있으면 공개할 수 있다.

    "준비되지 않음" 한 줄로 뭉치지 않는다 — 운영자가 무엇을 더 해야 하는지
    알 수 없으면 그 화면은 막다른 길이다.
    """
    out: list[str] = []
    for d in DIVISIONS:
        label = piku.DIVISION_LABELS[d]
        did = await _draft_id(d)
        if did is None:
            out.append(f"{label} 수집본 없음")
            continue
        m = await draft_mappings(d)
        n = len(m["rows"])
        if n != EXPECTED_ROWS[d]:
            out.append(f"{label} {n}/{EXPECTED_ROWS[d]}행")
            continue
        c = m["counts"]
        if c["duplicate"]:
            out.append(f"{label} 중복 연결 {c['duplicate']}건")
        pending = c["suggested"] + c["unmatched"]
        if pending:
            out.append(f"{label} 미확정 {pending}건")
    return out


async def publish_preview() -> dict:
    """공개하면 무엇이 바뀌는지. **DB write 0건.**

    내부 정렬 기준(우승 비율/승률)을 밝힌다 — 값은 담지 않지만 어느 기준으로
    줄을 세웠는지는 운영자가 알아야 검증할 수 있다.
    """
    sort, _ = piku.resolve_sort(piku.DEFAULT_SORT)
    out: dict[str, Any] = {
        "sort": sort, "sortLabel": piku.SORT_LABELS[sort], "divisions": {}}
    for d in DIVISIONS:
        m = await draft_mappings(d)
        draft_names = {r["pikuName"]: r["rank"] for r in m["rows"]}
        active = await piku.active_dataset(d)
        active_rows: dict[str, int] = {}
        if active:
            db = await get_db()
            active_rows = {r[0]: r[1] for r in await (await db.execute(
                "SELECT name, source_rank FROM piku_entries WHERE dataset_id=?",
                (active["id"],))).fetchall()}
        added = sorted(set(draft_names) - set(active_rows))
        removed = sorted(set(active_rows) - set(draft_names))
        changed = sum(1 for n, rk in draft_names.items()
                      if n in active_rows and active_rows[n] != rk)
        c = m["counts"]
        out["divisions"][d] = {
            "label": piku.DIVISION_LABELS[d],
            "expected": EXPECTED_ROWS[d],
            "draftRows": len(draft_names),
            "activeRows": len(active_rows),
            "added": len(added), "removed": len(removed), "changed": changed,
            "addedSample": added[:5], "removedSample": removed[:5],
            "confirmed": c["confirmed"], "unconfirmed": c["suggested"] + c["unmatched"],
            "duplicate": c["duplicate"],
            # 프로필·썸네일 연결 상태 — 확정된 행만 공개에 들어간다.
            "linked": c["confirmed"],
            "rows": [{"rank": r["rank"], "pikuName": r["pikuName"],
                      "lead": r["lead"], "songTitle": r["songTitle"],
                      "artistName": r["artistName"],
                      "officialName": r["officialName"],
                      "state": r["state"]} for r in m["rows"][:10]],
        }
    out["blockers"] = await publish_blockers()
    out["publishReady"] = not out["blockers"]
    return out


# ── 수동 import — draft까지만 ───────────────────────────────────────────────
async def import_manual(body: Any) -> dict:
    """관리자 JSON/CSV import를 **draft로만** 저장한다.

    기존 `piku.import_rows`는 검증 직후 곧바로 활성화한다. 한 부문만 넣어도 그
    부문이 즉시 공개되므로, Collector의 "세 부문 원자 공개" 계약을 우회하는
    뒷문이 된다. 그래서 관리 화면은 이 경로만 쓴다.
    """
    if not isinstance(body, dict):
        raise PikuError("parse_failed", "형식이 올바르지 않습니다.")
    division = body.get("division")
    if division not in DIVISIONS:
        raise PikuError("bad_division", "알 수 없는 부문입니다.")
    raw = body.get("rows")
    if raw is None and body.get("csv"):
        raw = piku.parse_csv(body["csv"])
    if raw is None:
        raise PikuError("empty", "가져올 데이터가 없습니다.")

    rows = piku.validate_rows(piku.normalize_rows(raw))
    expected = EXPECTED_ROWS[division]
    if len(rows) != expected:
        raise PikuError("row_count",
                        f"{expected}행이어야 하는데 {len(rows)}행입니다.")
    if division == "groups":
        # 수동 입력도 같은 대표자 규칙을 따른다.
        for r in rows:
            lead = group_lead(r["name"])
            if not lead:
                raise PikuError("missing_lead", "대표자를 찾지 못한 팀이 있습니다.")
            r["team_members"] = r["name"] if "," in r["name"] else ""
            r["name"] = lead
    return await _store_draft(division, rows, source="manual_import",
                              source_url="")
