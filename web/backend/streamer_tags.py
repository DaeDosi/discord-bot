"""스트리머 팀/소속 태그 (TAG-1).

운영자가 손으로 만드는 소속 라벨이다 — 이세돌·스텔라이브·픽셀네트워크 같은 것.
수집물이 아니고, **`rising_live_snapshots.tags`(치지직 방송 태그)와 다른 축**이다.
이름이 비슷하다고 합치지 말 것.

이 모듈이 단일 진입점인 이유는 두 가지다.

1. **색상 검증을 한 곳에서만 한다.** 태그 색은 화면에서 인라인 스타일로 들어가므로,
   임의 문자열이 저장되는 순간 그게 곧 주입 경로가 된다. `#RRGGBB`와 정해진
   방향 enum만 통과시키고, 그 판정을 라우터마다 복사하지 않는다.
2. **캐시 무효화 지점을 한 곳으로 모은다.** 랭킹 응답 일부는 TTL 캐시를 쓰는데,
   태그만 바뀌었을 때 낡은 캐시가 나가면 "저장했는데 화면이 그대로"가 된다.
   `version()`을 캐시 키에 섞고, 쓰기 경로가 `_bump()`를 부른다.
"""
from __future__ import annotations

import re
import time
import unicodedata

from database import get_db

# ── 상수 ────────────────────────────────────────────────────────────────────

#: 한 스트리머에게 저장할 수 있는 태그 수 상한.
#: 화면이 감당할 수 있는 수를 저장 단계에서 이미 막아 둔다 — "붙일 수는 있는데
#: 안 보인다"가 제일 나쁜 상태다.
MAX_TAGS_PER_STREAMER = 5

#: 목록 행(랭킹 등)에서 한 번에 보여 줄 태그 수. 나머지는 `+N`으로 접는다.
#: 프론트도 이 값을 쓰도록 공개 응답에 실어 보내지 않고, 프론트 상수와 맞춘다.
LIST_VISIBLE_TAGS = 2

#: 색상은 `#RRGGBB` 6자리만 받는다. 3자리 축약(#abc)·rgba()·색이름·var()·url() 전부 거부.
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: 그라데이션 방향은 **닫힌 목록**이다. 자유 문자열이면 `to right, url(...)` 같은 게 들어온다.
GRADIENT_DIRECTIONS: tuple[str, ...] = (
    "to-right", "to-bottom-right", "to-bottom", "to-top-right",
)

COLOR_MODES: tuple[str, ...] = ("solid", "gradient")

#: 지금은 팀 하나뿐이지만 컬럼을 열어 둔다(향후 '소속사', '프로젝트' 등).
KINDS: tuple[str, ...] = ("team",)

NAME_MAX = 20
SLUG_MAX = 40

#: 채널 id는 치지직 32자 소문자 16진수다. 다른 형식이 조회 키로 들어가지 않게 막는다.
_CHANNEL_RE = re.compile(r"^[0-9a-f]{32}$")


class TagError(ValueError):
    """입력이 규칙을 어겼다. 라우터가 400으로 바꾼다."""


# ── 검증 ────────────────────────────────────────────────────────────────────

def valid_channel_id(value: object) -> bool:
    return isinstance(value, str) and bool(_CHANNEL_RE.match(value.strip().lower()))


def norm_channel_id(value: object) -> str:
    v = (value or "") if isinstance(value, str) else ""
    v = v.strip().lower()
    if not _CHANNEL_RE.match(v):
        raise TagError("채널 ID 형식이 올바르지 않습니다(32자 16진수).")
    return v


def clean_name(value: object) -> str:
    """태그 이름 정규화.

    제어문자를 제거하고 공백을 접는다. HTML/CSS를 **이스케이프하지 않는다** —
    이 값은 React가 텍스트 노드로 렌더하므로 이스케이프는 그쪽 몫이고,
    여기서 `&lt;`로 바꿔 두면 화면에 그 글자가 그대로 보인다.
    대신 화면을 깨뜨릴 수 있는 문자(개행·탭·제로폭)만 걷어낸다.
    """
    if not isinstance(value, str):
        raise TagError("태그 이름은 문자열이어야 합니다.")
    # 제어문자(Cc)와 서식문자(Cf: 제로폭·RTL override 등)를 버린다.
    v = "".join(c for c in value if unicodedata.category(c) not in ("Cc", "Cf"))
    v = re.sub(r"\s+", " ", v).strip()
    if not v:
        raise TagError("태그 이름을 입력해 주세요.")
    if len(v) > NAME_MAX:
        raise TagError(f"태그 이름은 {NAME_MAX}자 이하여야 합니다.")
    return v


def slugify(name: str) -> str:
    """이름에서 내부 식별자를 만든다.

    한글이 슬러그의 대부분이므로 ASCII로 옮기지 않는다(옮기면 전부 빈 문자열이 된다).
    영숫자·한글만 남기고 나머지는 `-`로 접는다.
    """
    v = unicodedata.normalize("NFKC", name).strip().lower()
    v = re.sub(r"[^0-9a-z가-힣]+", "-", v).strip("-")
    return v[:SLUG_MAX] or "tag"


def clean_color(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _HEX_RE.match(value.strip()):
        raise TagError(f"{field}은(는) #RRGGBB 형식이어야 합니다.")
    return value.strip().lower()


def clean_style(color_mode: object, color_start: object,
                color_end: object, gradient_direction: object) -> dict:
    """색상 관련 4개 값을 한 번에 검증한다.

    따로 검증하면 `color_mode='gradient'`인데 `color_end`가 없는 조합이 통과한다 —
    그러면 화면에서 그라데이션이 `undefined`로 끝나 태그가 투명해진다.
    """
    mode = color_mode if isinstance(color_mode, str) else ""
    mode = mode.strip().lower()
    if mode not in COLOR_MODES:
        raise TagError("색상 방식은 solid 또는 gradient여야 합니다.")
    start = clean_color(color_start, field="시작 색상")
    if mode == "solid":
        # 단일색이면 끝 색과 방향은 저장하지 않는다. 남겨 두면 나중에 gradient로
        # 바꿨을 때 사용자가 고르지도 않은 옛 색이 되살아난다.
        return {"color_mode": "solid", "color_start": start,
                "color_end": None, "gradient_direction": GRADIENT_DIRECTIONS[0]}
    end = clean_color(color_end, field="끝 색상")
    direction = gradient_direction if isinstance(gradient_direction, str) else ""
    direction = direction.strip().lower()
    if direction not in GRADIENT_DIRECTIONS:
        raise TagError("그라데이션 방향이 올바르지 않습니다.")
    return {"color_mode": "gradient", "color_start": start,
            "color_end": end, "gradient_direction": direction}


def clean_kind(value: object) -> str:
    v = value if isinstance(value, str) else "team"
    v = (v or "team").strip().lower()
    if v not in KINDS:
        raise TagError("지원하지 않는 태그 종류입니다.")
    return v


# ── 캐시 버전 ───────────────────────────────────────────────────────────────

_version = 1


def version() -> int:
    """태그 상태의 세대 번호.

    TTL 캐시를 쓰는 랭킹 응답이 이 값을 캐시 키에 섞는다. 태그가 바뀌면 값이 올라가
    **다음 요청이 곧바로 캐시 미스**가 된다 — TTL이 끝나기를 기다리지 않는다.
    프로세스 안 카운터라 다중 replica에서는 각 프로세스가 따로 올라가지만, 그래도
    각자 자기 캐시를 버리므로 결과는 같다.
    """
    return _version


def _bump() -> None:
    global _version
    _version += 1


def reset_state() -> None:
    """테스트용 — 버전 카운터를 초기화한다."""
    global _version
    _version = 1


# ── 직렬화 ──────────────────────────────────────────────────────────────────

def _public(row) -> dict:
    """공개 화면이 쓰는 최소 필드.

    `created_at`/`updated_at`/`active`는 넣지 않는다 — 공개 응답에 운영 메타를
    실어 보낼 이유가 없고, 목록 응답은 행마다 반복되므로 바이트도 아깝다.
    """
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "slug": row["slug"],
        "kind": row["kind"],
        "colorMode": row["color_mode"],
        "colorStart": row["color_start"],
        "colorEnd": row["color_end"],
        "gradientDirection": row["gradient_direction"],
    }


def _admin(row) -> dict:
    d = _public(row)
    d.update({
        "active": bool(row["active"]),
        "createdAt": int(row["created_at"]),
        "updatedAt": int(row["updated_at"]),
        "assignedCount": int(row["assigned_count"]) if "assigned_count" in row.keys() else 0,
    })
    return d


# ── 조회 ────────────────────────────────────────────────────────────────────

async def tags_for_channels(channel_ids) -> dict[str, list[dict]]:
    """채널 → 태그 목록. **비활성 태그는 빼고** 지정 순서대로 준다.

    목록 화면이 행마다 한 번씩 부르면 그게 N+1이다. 그래서 채널 id 전부를 받아
    **쿼리 한 번**으로 끝낸다. 호출부는 결과 dict를 행에 나눠 붙이기만 하면 된다.
    """
    ids = [c for c in dict.fromkeys(channel_ids or []) if valid_channel_id(c)]
    if not ids:
        return {}
    out: dict[str, list[dict]] = {}
    db = await get_db()
    # SQLite 변수 상한(999)을 넘지 않게 잘라 넣는다. 랭킹 limit이 200이라 보통 1회다.
    CHUNK = 400
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        marks = ",".join("?" * len(chunk))
        rows = await (await db.execute(
            f"""SELECT a.streamer_channel_id AS cid, a.display_order,
                       t.id, t.name, t.slug, t.kind, t.color_mode,
                       t.color_start, t.color_end, t.gradient_direction
                  FROM streamer_tag_assignments a
                  JOIN streamer_tags t ON t.id = a.tag_id
                 WHERE a.streamer_channel_id IN ({marks})
                   AND t.active = 1
                 ORDER BY a.streamer_channel_id, a.display_order, t.id""",
            tuple(chunk)
        )).fetchall()
        for r in rows:
            out.setdefault(r["cid"], []).append(_public(r))
    return out


async def tags_for_channel(channel_id: str) -> list[dict]:
    if not valid_channel_id(channel_id):
        return []
    return (await tags_for_channels([channel_id])).get(
        str(channel_id).strip().lower(), [])


async def attach_tags(rows: list[dict], *, key: str = "chzzk_channel_id",
                      field: str = "team_tags") -> list[dict]:
    """응답 행 목록에 태그를 붙인다(제자리 수정).

    **필드명이 `tags`가 아니라 `team_tags`인 이유가 있다.** `rising_router`의
    일부 응답(`/newcomers`, `/tag-streamers`)은 이미 `tags`로 **치지직 방송 태그**
    (문자열 배열)를 내보낸다. 같은 이름을 쓰면 그걸 조용히 덮어써서 기존 화면이
    깨진다 — 실제로 한 번 그렇게 만들었다가 QA에서 잡았다.
    두 개념은 축이 다르므로 이름도 끝까지 다르게 유지할 것.

    태그가 없는 스트리머에는 **빈 배열**을 넣는다. 키 자체를 빼면 프론트가
    `undefined`와 `[]`를 따로 다뤄야 해서 분기가 늘어난다.
    """
    if not rows:
        return rows
    mapping = await tags_for_channels([r.get(key) for r in rows])
    for r in rows:
        r[field] = mapping.get(r.get(key), [])
    return rows


async def list_tags(*, include_inactive: bool = False) -> list[dict]:
    """관리 화면용 전체 목록 — 지정 수를 함께 센다(별도 쿼리 N번 금지)."""
    db = await get_db()
    where = "" if include_inactive else "WHERE t.active = 1"
    rows = await (await db.execute(
        f"""SELECT t.*, COUNT(a.tag_id) AS assigned_count
              FROM streamer_tags t
              LEFT JOIN streamer_tag_assignments a ON a.tag_id = t.id
              {where}
             GROUP BY t.id
             ORDER BY t.active DESC, t.name COLLATE NOCASE"""
    )).fetchall()
    return [_admin(r) for r in rows]


async def get_tag(tag_id: int):
    db = await get_db()
    return await (await db.execute(
        "SELECT * FROM streamer_tags WHERE id=?", (int(tag_id),))).fetchone()


# ── 쓰기 ────────────────────────────────────────────────────────────────────

async def create_tag(*, name, color_mode, color_start, color_end,
                     gradient_direction, kind="team") -> dict:
    clean = clean_name(name)
    style = clean_style(color_mode, color_start, color_end, gradient_direction)
    k = clean_kind(kind)
    slug = slugify(clean)
    now = int(time.time())
    db = await get_db()
    try:
        cur = await db.execute(
            """INSERT INTO streamer_tags
                   (name, slug, kind, color_mode, color_start, color_end,
                    gradient_direction, active, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,1,?,?)""",
            (clean, slug, k, style["color_mode"], style["color_start"],
             style["color_end"], style["gradient_direction"], now, now))
        await db.commit()
    except Exception as e:
        # 유니크 인덱스가 이름/슬러그 중복을 잡는다. 메시지로 원인을 구분해 준다.
        await db.rollback()
        if "UNIQUE" in str(e).upper():
            raise TagError("이미 같은 이름의 태그가 있습니다.") from e
        raise
    _bump()
    row = await get_tag(cur.lastrowid)
    return _admin(row) | {"assignedCount": 0}


async def update_tag(tag_id: int, *, name=None, color_mode=None, color_start=None,
                     color_end=None, gradient_direction=None, active=None) -> dict:
    row = await get_tag(tag_id)
    if row is None:
        raise TagError("존재하지 않는 태그입니다.")
    fields: dict = {}
    if name is not None:
        clean = clean_name(name)
        fields["name"] = clean
        fields["slug"] = slugify(clean)
    if color_mode is not None or color_start is not None \
            or color_end is not None or gradient_direction is not None:
        # 일부만 온 경우 나머지는 기존 값으로 채워 넣고 **한 세트로** 검증한다.
        fields.update(clean_style(
            color_mode if color_mode is not None else row["color_mode"],
            color_start if color_start is not None else row["color_start"],
            color_end if color_end is not None else row["color_end"],
            gradient_direction if gradient_direction is not None
            else row["gradient_direction"]))
    if active is not None:
        fields["active"] = 1 if active else 0
    if not fields:
        return _admin(row)
    fields["updated_at"] = int(time.time())
    sets = ", ".join(f"{k}=?" for k in fields)
    db = await get_db()
    try:
        await db.execute(f"UPDATE streamer_tags SET {sets} WHERE id=?",
                         (*fields.values(), int(tag_id)))
        await db.commit()
    except Exception as e:
        await db.rollback()
        if "UNIQUE" in str(e).upper():
            raise TagError("이미 같은 이름의 태그가 있습니다.") from e
        raise
    _bump()
    return _admin(await get_tag(tag_id))


async def assign(channel_id: str, tag_id: int) -> dict:
    """스트리머에게 태그를 붙인다.

    **행을 지우는 동작이 아니므로 안전하다.** 이미 붙어 있으면 조용히 성공으로
    끝낸다(멱등) — 두 번 눌렀다고 500을 주면 운영자가 상태를 못 읽는다.
    """
    cid = norm_channel_id(channel_id)
    tag = await get_tag(tag_id)
    if tag is None:
        raise TagError("존재하지 않는 태그입니다.")
    if not int(tag["active"]):
        raise TagError("비활성 태그는 새로 지정할 수 없습니다.")
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) n, COALESCE(MAX(display_order), -1) mx "
        "FROM streamer_tag_assignments WHERE streamer_channel_id=?", (cid,))
    row = await cur.fetchone()
    exists = await (await db.execute(
        "SELECT 1 FROM streamer_tag_assignments WHERE streamer_channel_id=? AND tag_id=?",
        (cid, int(tag_id)))).fetchone()
    if exists:
        return {"channelId": cid, "tagId": int(tag_id), "created": False}
    if int(row["n"]) >= MAX_TAGS_PER_STREAMER:
        raise TagError(f"한 스트리머에게는 태그를 {MAX_TAGS_PER_STREAMER}개까지 붙일 수 있습니다.")
    await db.execute(
        "INSERT OR IGNORE INTO streamer_tag_assignments "
        "(streamer_channel_id, tag_id, display_order, created_at) VALUES (?,?,?,?)",
        (cid, int(tag_id), int(row["mx"]) + 1, int(time.time())))
    await db.commit()
    _bump()
    return {"channelId": cid, "tagId": int(tag_id), "created": True}


async def unassign(channel_id: str, tag_id: int) -> dict:
    """지정을 해제한다.

    지우는 것은 **연결 행 하나**뿐이다 — 태그 자체도, 스트리머 데이터도 건드리지
    않는다. 태그를 없애고 싶으면 `update_tag(active=False)`를 쓴다.
    """
    cid = norm_channel_id(channel_id)
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM streamer_tag_assignments WHERE streamer_channel_id=? AND tag_id=?",
        (cid, int(tag_id)))
    await db.commit()
    _bump()
    return {"channelId": cid, "tagId": int(tag_id), "removed": bool(cur.rowcount)}


async def reorder(channel_id: str, tag_ids: list) -> dict:
    """한 스트리머의 태그 노출 순서를 통째로 다시 매긴다.

    '위로/아래로' 버튼마다 UPDATE를 두 번 쏘는 대신 최종 순서를 한 번에 받는다 —
    중간 상태가 저장되지 않아 새로고침해도 순서가 뒤틀리지 않는다.
    목록에 없는 기존 지정은 **건드리지 않는다**(뒤로 밀린다).
    """
    cid = norm_channel_id(channel_id)
    ids = [int(t) for t in (tag_ids or []) if str(t).strip().lstrip("-").isdigit()]
    if not ids:
        raise TagError("순서를 매길 태그가 없습니다.")
    db = await get_db()
    for order, tid in enumerate(dict.fromkeys(ids)):
        await db.execute(
            "UPDATE streamer_tag_assignments SET display_order=? "
            "WHERE streamer_channel_id=? AND tag_id=?", (order, cid, tid))
    await db.commit()
    _bump()
    return {"channelId": cid, "count": len(set(ids))}


async def search_streamers(keyword: str, limit: int = 20) -> list[dict]:
    """관리 화면의 스트리머 검색 — 이름 부분일치 또는 채널 ID 완전일치.

    `rising_channel_stats`가 우리가 아는 채널의 사실상 전체 목록이다.
    LIKE 앞에 와일드카드를 두면 인덱스를 못 타므로 **최근 본 순으로 상한을 걸고**
    읽는다. 무제한 SELECT를 만들지 않는다.
    """
    kw = (keyword or "").strip()
    if len(kw) < 2:
        raise TagError("검색어는 2자 이상이어야 합니다.")
    n = max(1, min(50, int(limit or 20)))
    db = await get_db()
    if valid_channel_id(kw):
        rows = await (await db.execute(
            "SELECT chzzk_channel_id, channel_name, last_seen FROM rising_channel_stats "
            "WHERE chzzk_channel_id=?", (kw.lower(),))).fetchall()
    else:
        rows = await (await db.execute(
            "SELECT chzzk_channel_id, channel_name, last_seen FROM rising_channel_stats "
            "WHERE channel_name LIKE ? ESCAPE '\\' "
            "ORDER BY last_seen DESC LIMIT ?",
            (f"%{_like_escape(kw)}%", n))).fetchall()
    out = [{"channelId": r["chzzk_channel_id"], "channelName": r["channel_name"],
            "lastSeen": int(r["last_seen"] or 0)} for r in rows]
    mapping = await tags_for_channels([o["channelId"] for o in out])
    for o in out:
        o["tags"] = mapping.get(o["channelId"], [])
    return out


def _like_escape(value: str) -> str:
    """LIKE 와일드카드를 무력화한다 — `%`만 넣어 전체를 긁어 가지 못하게."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def assignments_of_tag(tag_id: int, limit: int = 200) -> list[dict]:
    """이 태그가 붙은 스트리머 목록(관리 화면). 상한을 반드시 건다."""
    n = max(1, min(500, int(limit or 200)))
    db = await get_db()
    rows = await (await db.execute(
        """SELECT a.streamer_channel_id AS cid, a.display_order,
                  c.channel_name
             FROM streamer_tag_assignments a
             LEFT JOIN rising_channel_stats c
                    ON c.chzzk_channel_id = a.streamer_channel_id
            WHERE a.tag_id=?
            ORDER BY a.display_order, a.streamer_channel_id
            LIMIT ?""", (int(tag_id), n))).fetchall()
    return [{"channelId": r["cid"], "channelName": r["channel_name"],
             "displayOrder": int(r["display_order"])} for r in rows]


__all__ = [
    "MAX_TAGS_PER_STREAMER", "LIST_VISIBLE_TAGS", "GRADIENT_DIRECTIONS",
    "COLOR_MODES", "KINDS", "TagError",
    "valid_channel_id", "norm_channel_id", "clean_name", "slugify",
    "clean_color", "clean_style", "clean_kind",
    "version", "reset_state",
    "tags_for_channels", "tags_for_channel", "attach_tags",
    "list_tags", "get_tag", "create_tag", "update_tag",
    "assign", "unassign", "reorder", "search_streamers", "assignments_of_tag",
]
