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

import json
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

#: 색상 지점(stop) 개수 상한. 화면에서 구분되지 않는 수를 저장 단계에서 막는다 —
#: 배지는 폭이 10rem이라 8개를 넘으면 사람 눈에 띠가 아니라 얼룩으로 보이고,
#: 응답 바이트도 목록 행마다 반복된다. 하한이 1인 이유는 "색이 하나도 없는 태그"가
#: 곧 투명 배지이기 때문이다(요구: 최소 1개는 항상 남는다).
MAX_COLOR_STOPS = 8
MIN_COLOR_STOPS = 1

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


def clean_stops(value: object) -> list[dict]:
    """색상 지점 배열을 검증한다 — **다중 그라데이션의 단일 진입점**.

    받는 형태는 `[{"color": "#rrggbb", "pos": 0..100}, ...]`이고 `pos`는 생략 가능하다.
    생략되면 **균등 분배**한다(1개는 0, 2개는 0/100, 3개는 0/50/100 …).

    규칙과 그 이유:
    · 개수 1~`MAX_COLOR_STOPS` — 0개는 투명 배지이고, 상한은 화면·바이트 문제다.
    · 색은 `#RRGGBB`만 — `clean_color`와 같은 규칙을 쓴다. 색이 인라인 스타일로
      들어가므로 여기서 느슨해지면 그게 곧 주입 경로다.
    · `pos`는 0~100 정수. **오름차순으로 정렬해 저장한다**(거부하지 않는다) —
      운영자가 지점을 끌어 순서를 바꾸는 것이 정상 조작이고, 그때마다 400을 던지면
      드래그 UI를 만들 수 없다. 다만 **동률은 유지**한다(hard stop 표현이 가능해야 한다).
    · 정렬은 **안정 정렬**이라 같은 pos끼리는 보낸 순서가 유지된다.
    """
    if not isinstance(value, (list, tuple)):
        raise TagError("색상 지점은 배열이어야 합니다.")
    if len(value) < MIN_COLOR_STOPS:
        raise TagError("색상은 최소 1개가 필요합니다.")
    if len(value) > MAX_COLOR_STOPS:
        raise TagError(f"색상 지점은 최대 {MAX_COLOR_STOPS}개까지 지정할 수 있습니다.")

    raw: list[tuple[str, int | None]] = []
    for i, item in enumerate(value):
        if isinstance(item, str):          # 색 문자열만 온 경우도 받아 준다
            raw.append((clean_color(item, field=f"{i + 1}번째 색상"), None))
            continue
        if not isinstance(item, dict):
            raise TagError(f"{i + 1}번째 색상 지점의 형식이 올바르지 않습니다.")
        color = clean_color(item.get("color"), field=f"{i + 1}번째 색상")
        pos = item.get("pos", item.get("position"))
        if pos is None:
            raw.append((color, None))
            continue
        # bool은 int의 하위 타입이라 먼저 걸러낸다(True가 1로 통과하면 안 된다).
        if isinstance(pos, bool) or not isinstance(pos, (int, float)):
            raise TagError(f"{i + 1}번째 색상 위치는 숫자여야 합니다.")
        p = int(round(float(pos)))
        if not (0 <= p <= 100):
            raise TagError(f"{i + 1}번째 색상 위치는 0~100 사이여야 합니다.")
        raw.append((color, p))

    n = len(raw)
    out: list[dict] = []
    for i, (color, pos) in enumerate(raw):
        if pos is None:
            pos = 0 if n == 1 else round(i * 100 / (n - 1))
        out.append({"color": color, "pos": pos})
    # 안정 정렬 — 동률(hard stop)은 보낸 순서를 지킨다.
    out.sort(key=lambda s: s["pos"])
    return out


def stops_from_legacy(row) -> list[dict]:
    """구형 3컬럼(`color_mode`/`color_start`/`color_end`)에서 stop 배열을 합성한다.

    **백필하지 않고 읽을 때 합성하는 이유**: 백필은 파괴적 UPDATE이고, 실패하면
    되돌릴 원본이 없다. 반면 합성은 몇 번을 해도 같은 값이고, 구형 행을 한 번도
    수정하지 않은 채로 새 화면이 정확히 예전과 같은 색을 그린다.
    """
    start = row["color_start"] if _HEX_RE.match(str(row["color_start"] or "")) \
        else "#38bdf8"
    end = row["color_end"] if row["color_end"] and _HEX_RE.match(str(row["color_end"])) \
        else None
    if row["color_mode"] == "gradient" and end:
        return [{"color": start.lower(), "pos": 0}, {"color": end.lower(), "pos": 100}]
    return [{"color": start.lower(), "pos": 0}]


def stops_of(row) -> list[dict]:
    """행의 색상 지점 — 신형 JSON이 있으면 그것, 없으면 구형에서 합성.

    저장된 JSON이 깨져 있어도 **화면을 깨뜨리지 않는다** — 구형 합성으로 떨어진다.
    (색은 장식이라 여기서 500을 내는 것이 더 나쁘다.)
    """
    raw = row["color_stops"] if "color_stops" in row.keys() else None
    if raw:
        try:
            parsed = clean_stops(json.loads(raw))
            if parsed:
                return parsed
        except (TagError, ValueError, TypeError):
            pass
    return stops_from_legacy(row)


def legacy_from_stops(stops: list[dict], gradient_direction: str) -> dict:
    """stop 배열 → 구형 3컬럼. **쓰기 경로는 두 표현을 항상 함께 갱신한다.**

    이 컬럼들을 아직 읽는 코드(관리 응답의 `colorMode` 등)가 마이그레이션 후에도
    똑같이 동작해야 하기 때문이다. 3개 이상이면 구형 표현은 양 끝만 담게 되는데,
    그게 구형 소비처가 표현할 수 있는 최선이고 **신형 소비처는 JSON을 본다**.
    """
    if len(stops) <= 1:
        return {"color_mode": "solid", "color_start": stops[0]["color"],
                "color_end": None, "gradient_direction": gradient_direction}
    return {"color_mode": "gradient", "color_start": stops[0]["color"],
            "color_end": stops[-1]["color"], "gradient_direction": gradient_direction}


def clean_style_v2(color_stops: object, gradient_direction: object) -> dict:
    """신형 입력(stop 배열 + 방향)을 검증해 **DB 컬럼 한 세트**로 만든다."""
    stops = clean_stops(color_stops)
    direction = gradient_direction if isinstance(gradient_direction, str) else ""
    direction = direction.strip().lower() or GRADIENT_DIRECTIONS[0]
    if direction not in GRADIENT_DIRECTIONS:
        raise TagError("그라데이션 방향이 올바르지 않습니다.")
    out = legacy_from_stops(stops, direction)
    out["color_stops"] = json.dumps(stops, separators=(",", ":"))
    return out


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
        # 구형 3필드는 **계속 내보낸다.** 이걸 읽는 소비처가 마이그레이션 후에도
        # 정확히 같은 색을 그려야 한다(3색 이상이면 양 끝으로 근사된다).
        "colorMode": row["color_mode"],
        "colorStart": row["color_start"],
        "colorEnd": row["color_end"],
        "gradientDirection": row["gradient_direction"],
        # 신형 — 화면은 이걸 먼저 본다. 구형 행에서도 합성되므로 **항상 존재한다**
        # (프론트에 "없을 수도 있음" 분기를 만들지 않기 위해서다).
        "colorStops": stops_of(row),
    }


def _admin(row) -> dict:
    d = _public(row)
    d.update({
        "active": bool(row["active"]),
        "createdAt": int(row["created_at"]),
        "updatedAt": int(row["updated_at"]),
        "assignedCount": int(row["assigned_count"]) if "assigned_count" in row.keys() else 0,
        # 전체 스트리머 랭킹에서 이 그룹의 멤버를 뺄지. 운영 화면에서만 쓰는 값이라
        # 공개 응답(`_public`)에는 넣지 않는다.
        "excludeFromRanking": bool(row["exclude_from_ranking"])
            if "exclude_from_ranking" in row.keys() else False,
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
                       t.color_start, t.color_end, t.gradient_direction,
                       -- **빼면 안 된다.** `_public`이 이 컬럼으로 stop 배열을 만든다.
                       -- 없으면 조용히 구형 합성으로 떨어져 3색 그룹이 목록에서만
                       -- 2색으로 보인다(관리 화면과 색이 달라진다).
                       t.color_stops
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

def _style_for_write(*, color_stops, color_mode, color_start, color_end,
                     gradient_direction, current=None) -> dict:
    """입력이 신형(stop 배열)이든 구형(3필드)이든 **컬럼 한 세트**로 정규화한다.

    `color_stops`가 오면 그쪽이 이긴다 — 두 표현이 동시에 오면 사용자가 방금 조작한
    것은 새 편집기 쪽이고, 구형 필드는 폼이 습관적으로 함께 보낸 잔재이기 때문이다.
    구형 입력만 오면 예전 검증(`clean_style`)을 그대로 통과시킨 뒤 **동등한 stop
    배열을 함께 써 준다** — 그래야 두 표현이 갈라지지 않는다.
    """
    if color_stops is not None:
        direction = gradient_direction
        if direction is None and current is not None:
            direction = current["gradient_direction"]
        return clean_style_v2(color_stops, direction)
    if color_mode is None and color_start is None and color_end is None \
            and gradient_direction is None:
        return {}
    # **방향만 바꾸는 경우 기존 stop 배열을 보존한다.** 여기서 구형 3컬럼으로
    # 재구성하면 3색 이상이던 그룹이 방향을 한 번 바꾼 것만으로 2색으로 잘린다
    # (구형 컬럼은 양 끝만 담기 때문이다) — 조용한 데이터 손실이라 가장 나쁘다.
    if current is not None and gradient_direction is not None \
            and color_mode is None and color_start is None and color_end is None:
        return clean_style_v2(stops_of(current), gradient_direction)
    if current is not None:
        color_mode = color_mode if color_mode is not None else current["color_mode"]
        color_start = color_start if color_start is not None else current["color_start"]
        color_end = color_end if color_end is not None else current["color_end"]
        gradient_direction = (gradient_direction if gradient_direction is not None
                              else current["gradient_direction"])
    style = clean_style(color_mode, color_start, color_end, gradient_direction)
    stops = ([{"color": style["color_start"], "pos": 0},
              {"color": style["color_end"], "pos": 100}]
             if style["color_mode"] == "gradient" and style["color_end"]
             else [{"color": style["color_start"], "pos": 0}])
    style["color_stops"] = json.dumps(stops, separators=(",", ":"))
    return style


async def create_tag(*, name, color_mode=None, color_start=None, color_end=None,
                     gradient_direction=None, kind="team",
                     color_stops=None,
                     exclude_from_ranking: bool = False) -> dict:
    clean = clean_name(name)
    style = _style_for_write(color_stops=color_stops, color_mode=color_mode,
                             color_start=color_start, color_end=color_end,
                             gradient_direction=gradient_direction)
    if not style:
        raise TagError("색상을 입력해 주세요.")
    k = clean_kind(kind)
    slug = slugify(clean)
    now = int(time.time())
    db = await get_db()
    try:
        cur = await db.execute(
            """INSERT INTO streamer_tags
                   (name, slug, kind, color_mode, color_start, color_end,
                    gradient_direction, color_stops, active, exclude_from_ranking,
                    created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,1,?,?,?)""",
            (clean, slug, k, style["color_mode"], style["color_start"],
             style["color_end"], style["gradient_direction"], style["color_stops"],
             1 if exclude_from_ranking else 0, now, now))
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
                     color_end=None, gradient_direction=None, active=None,
                     color_stops=None, exclude_from_ranking=None) -> dict:
    row = await get_tag(tag_id)
    if row is None:
        raise TagError("존재하지 않는 태그입니다.")
    fields: dict = {}
    if name is not None:
        clean = clean_name(name)
        fields["name"] = clean
        fields["slug"] = slugify(clean)
    # 일부만 온 경우 나머지는 기존 값으로 채워 넣고 **한 세트로** 검증한다.
    fields.update(_style_for_write(
        color_stops=color_stops, color_mode=color_mode, color_start=color_start,
        color_end=color_end, gradient_direction=gradient_direction, current=row))
    if active is not None:
        fields["active"] = 1 if active else 0
    if exclude_from_ranking is not None:
        fields["exclude_from_ranking"] = 1 if exclude_from_ranking else 0
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


async def reorder_members(tag_id: int, channel_ids: list) -> dict:
    """한 그룹 **안에서** 멤버 순서를 다시 매긴다(관리 화면의 ↑↓).

    ⚠️ **`display_order`는 원래 "그 스트리머의 배지 노출 순서"다.** 같은 컬럼을
    여기서 "그룹 안의 멤버 순서"로도 쓰는데, 한 스트리머가 그룹 하나에만 속하면
    두 의미가 일치해 아무 문제가 없다. **여러 그룹에 속한 스트리머**의 값을 여기서
    바꾸면 그 사람의 공개 배지 순서도 함께 바뀐다 — 화면에서 그 사실을 안내한다.

    별도 컬럼을 만들지 않은 이유: 그룹 내 순서는 관리 화면에서만 쓰이고 공개 화면
    어디에도 나오지 않는다. 그 하나를 위해 마이그레이션을 더하면 운영 데이터에
    되돌리기 어려운 변경이 늘어난다.
    """
    tag = await get_tag(tag_id)
    if tag is None:
        raise TagError("존재하지 않는 소속 그룹입니다.")
    ids = [norm_channel_id(c) for c in (channel_ids or [])]
    if not ids:
        raise TagError("순서를 매길 멤버가 없습니다.")
    db = await get_db()
    for order, cid in enumerate(dict.fromkeys(ids)):
        await db.execute(
            "UPDATE streamer_tag_assignments SET display_order=? "
            "WHERE tag_id=? AND streamer_channel_id=?", (order, int(tag_id), cid))
    await db.commit()
    _bump()
    return {"tagId": int(tag_id), "count": len(set(ids))}


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
            "lastSeen": int(r["last_seen"] or 0),
            "channelImageUrl": _channel_image(r["chzzk_channel_id"])} for r in rows]
    mapping = await tags_for_channels([o["channelId"] for o in out])
    for o in out:
        o["tags"] = mapping.get(o["channelId"], [])
    return out


def _like_escape(value: str) -> str:
    """LIKE 와일드카드를 무력화한다 — `%`만 넣어 전체를 긁어 가지 못하게."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: 멤버 목록 한 페이지 상한. 그룹 하나에 수백 명이 붙어도 화면이 한 번에 다 그리지
#: 않도록 서버가 먼저 자른다 — 500개를 통째로 렌더하면 모바일에서 그대로 멈춘다.
MEMBER_PAGE_MAX = 100
MEMBER_PAGE_DEFAULT = 30


async def assignments_of_tag(tag_id: int, *, limit: int = MEMBER_PAGE_DEFAULT,
                             offset: int = 0, search: str | None = None) -> dict:
    """이 그룹에 속한 스트리머 목록(관리 화면).

    한 그룹의 멤버가 수백 명이 될 수 있어 **페이지로 끊어 준다.** 정렬은 화면에
    보이는 순서 그대로(`display_order`)이고, 그 뒤 채널 id로 안정 정렬한다 —
    같은 순서 값이 여러 개면 페이지마다 순서가 흔들려 같은 사람이 두 번 보인다.

    `search`는 이름 부분일치 또는 32자 채널 ID 완전일치다. `%`·`_`는 이스케이프해
    와일드카드로 동작하지 못하게 한다.

    반환에 `total`을 함께 담는 이유는 화면이 "멤버 N명"을 페이지 크기가 아니라
    **실제 지정 수**로 보여야 하기 때문이다(누적 카운터를 따로 두지 않는다).
    """
    n = max(1, min(MEMBER_PAGE_MAX, int(limit or MEMBER_PAGE_DEFAULT)))
    off = max(0, int(offset or 0))
    kw = (search or "").strip()
    db = await get_db()

    where = ["a.tag_id = ?"]
    params: list = [int(tag_id)]
    if kw:
        if len(kw) < 2:
            raise TagError("검색어는 2자 이상이어야 합니다.")
        if valid_channel_id(kw):
            where.append("a.streamer_channel_id = ?")
            params.append(kw.lower())
        else:
            # ESCAPE 문자는 **한 글자**여야 한다. 파이썬 소스에서 백슬래시 하나를
            # 담으려면 `'\\'`로 쓴다 — `'\''`처럼 되면 빈 문자열이 돼 SQLite가 거부한다.
            where.append("c.channel_name LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(kw)}%")
    cond = " AND ".join(where)

    # 총 개수는 같은 조건으로 한 번만 센다(행마다 세면 그게 N+1이다).
    total = int((await (await db.execute(
        f"""SELECT COUNT(*) n
              FROM streamer_tag_assignments a
              LEFT JOIN rising_channel_stats c
                     ON c.chzzk_channel_id = a.streamer_channel_id
             WHERE {cond}""", tuple(params))).fetchone())["n"])

    rows = await (await db.execute(
        f"""SELECT a.streamer_channel_id AS cid, a.display_order, c.channel_name
              FROM streamer_tag_assignments a
              LEFT JOIN rising_channel_stats c
                     ON c.chzzk_channel_id = a.streamer_channel_id
             WHERE {cond}
             ORDER BY a.display_order, a.streamer_channel_id
             LIMIT ? OFFSET ?""", (*params, n, off))).fetchall()

    items = [{"channelId": r["cid"], "channelName": r["channel_name"],
              "displayOrder": int(r["display_order"]),
              "channelImageUrl": _channel_image(r["cid"])} for r in rows]
    return {"items": items, "total": total, "limit": n, "offset": off,
            "hasMore": off + len(items) < total}


def _channel_image(channel_id: str) -> str:
    """프로필 이미지 — 수집기가 메모리에 들고 있는 맵에서 읽는다(DB 조회 0회).

    없으면 빈 문자열이다. 이걸 위해 외부를 호출하지 않는다 — 멤버 30명을 그리려고
    30번 나가면 그게 곧 N+1이고, 관리 화면 한 번 여는 데 몇 초가 걸린다.
    """
    try:
        from rising_collector import latest_image
        return latest_image(channel_id) or ""
    except Exception:                                   # noqa: BLE001
        return ""


__all__ = [
    "MAX_TAGS_PER_STREAMER", "LIST_VISIBLE_TAGS", "GRADIENT_DIRECTIONS",
    "COLOR_MODES", "KINDS", "TagError",
    "valid_channel_id", "norm_channel_id", "clean_name", "slugify",
    "clean_color", "clean_style", "clean_kind",
    "version", "reset_state",
    "tags_for_channels", "tags_for_channel", "attach_tags",
    "list_tags", "get_tag", "create_tag", "update_tag",
    "assign", "unassign", "reorder", "search_streamers", "assignments_of_tag",
    "MEMBER_PAGE_MAX", "MEMBER_PAGE_DEFAULT", "reorder_members",
]


# ── 전체 스트리머 랭킹 제외 (UI-R 요구 6) ────────────────────────────────────

RANKING_EXCLUSION_SQL = """
    SELECT a.streamer_channel_id
      FROM streamer_tag_assignments a
      JOIN streamer_tags t ON t.id = a.tag_id
     WHERE t.active = 1 AND t.exclude_from_ranking = 1
"""
"""랭킹에서 뺄 채널을 뽑는 서브쿼리.

**쿼리 안에서 직접 쓰라고 문자열로 둔다.** 파이썬으로 목록을 만들어 `NOT IN (?,?,…)`을
조립하면 (1) 목록이 길어질수록 바인딩이 늘고 (2) 조회와 랭킹 쿼리 사이에 값이 바뀌는
창이 생긴다. 서브쿼리는 한 트랜잭션 안에서 항상 최신 상태를 본다.

계약:
 · **활성 그룹만** 본다 — 그룹을 비활성화하면 멤버가 즉시 랭킹에 돌아온다.
 · 지정(assignment) 행이 사라지면 역시 즉시 돌아온다.
 · 여러 그룹에 속하면 **하나라도 제외 그룹이면 제외**된다(EXISTS 의미).
 · 그룹 **이름**을 보지 않으므로 이름을 바꿔도 정책이 유지된다.
"""


def ranking_exclusion_clause(column: str) -> str:
    """`<column> NOT IN (제외 대상)` 절을 만든다 — **랭킹 쿼리는 이 함수만 쓴다.**

    예전에는 서브쿼리를 라우터에 그대로 복사해 넣었다. 그 상태에서 적용 범위를
    넓히자(기간별 누적 랭킹) 곧바로 드러난 문제가 있다: 한쪽만 고치면 두 랭킹이
    **서로 다른 명단**을 쓰게 되고, 그 차이는 화면을 나란히 놓기 전까지 보이지 않는다.
    그래서 SQL 조각을 만드는 지점을 하나로 모은다.

    `column`은 **호출자가 코드에 적어 넣는 컬럼 이름**이다(사용자 입력이 아니다).
    그래도 식별자 형태만 통과시킨다 — 문자열 조립 지점에 검증이 없으면, 나중에
    누군가 여기에 변수를 넘기는 순간 그게 주입 경로가 된다.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", column):
        raise ValueError(f"잘못된 컬럼 식별자: {column!r}")
    return f"{column} NOT IN ({RANKING_EXCLUSION_SQL})"


#: 이 정책이 적용되는 화면. **여기 없는 화면은 제외하지 않는다.**
#:
#: 적용: 순위 경쟁을 보여 주는 화면 — 플랫폼이 직접 운영하는 채널이 상위를 차지하면
#:       그 순위가 커뮤니티의 실제 판도를 나타내지 못한다.
#: 미적용: 찾기·확인·분석 화면 — 여기서 빼면 "검색해도 안 나온다"가 된다.
#:         (검색·스트리머 상세·신규/소형 스트리머 **통계**·싱드컵·수집·저장)
#:
#: 소형 스트리머 **랭킹**을 적용 쪽에 넣은 이유: 이름 그대로 랭킹이고, 정책의 목적이
#: "플랫폼 운영 채널을 순위 경쟁에서 뺀다"이므로 전체·기간별과 갈라 둘 근거가 없다.
#: 반면 소형/신규 스트리머 **통계**(분석 화면)는 기존 신규 카빙아웃과 같은 이유로
#: 적용하지 않는다 — 그쪽은 "누가 있나"를 보는 화면이다.
RANKING_EXCLUSION_APPLIES_TO: tuple[str, ...] = (
    "live-ranking",       # 전체 스트리머 랭킹
    "ranking-period",     # 기간별 누적 랭킹
    "small-ranking",      # 소형 스트리머 랭킹
)


async def excluded_channel_ids() -> set[str]:
    """제외 대상 채널 id 집합. 테스트와 진단용 — 랭킹 쿼리는 위 서브쿼리를 직접 쓴다."""
    db = await get_db()
    rows = await (await db.execute(RANKING_EXCLUSION_SQL)).fetchall()
    return {r["streamer_channel_id"] for r in rows}


# ── 그룹 분석(공개) ─────────────────────────────────────────────────────────
#
# **랭킹 제외 계약과 무관하다.** `exclude_from_ranking`은 *전체·기간별·소형 랭킹*
# 에서 그 그룹 멤버를 빼는 정책이지, 그룹 자체를 숨기는 뜻이 아니다. 공식 그룹도
# 그룹 분석에서는 보여야 한다 — 여기서 그 플래그로 거르면 "공식 그룹을 볼 수 없는"
# 전혀 다른 정책이 조용히 생긴다. 그래서 이 아래 어디에서도 그 컬럼을 읽지 않는다.

#: 그룹 분석 한 화면이 돌려주는 멤버 수 상한(응답 폭주 방지).
GROUP_MEMBER_MAX = 300

#: 그룹 목록 상한.
GROUP_LIST_MAX = 200

#: 그룹 분석 응답 캐시 TTL(초). 무효화는 `version()`이 맡는다.
GROUP_CACHE_TTL = 60

_group_cache: dict = {}


def reset_group_cache() -> None:
    """테스트·진단용."""
    _group_cache.clear()


async def group_list() -> list[dict]:
    """분석 대상 그룹 목록 — **활성이고 멤버가 1명 이상인** 그룹만.

    멤버 수는 그룹마다 따로 세지 않고 한 번의 `GROUP BY`로 얻는다. 그룹 수만큼
    쿼리를 돌리면 그게 N+1이고, 그룹이 늘수록 선형으로 느려진다.
    """
    key = ("list", version())
    hit = _group_cache.get(key)
    if hit is not None and time.time() - hit[0] < GROUP_CACHE_TTL:
        return hit[1]

    db = await get_db()
    rows = await (await db.execute(
        """SELECT t.*, COUNT(a.streamer_channel_id) AS member_count
             FROM streamer_tags t
             JOIN streamer_tag_assignments a ON a.tag_id = t.id
            WHERE t.active = 1
            GROUP BY t.id
           HAVING member_count > 0
            ORDER BY member_count DESC, t.name
            LIMIT ?""", (GROUP_LIST_MAX,))).fetchall()
    out = [{**_public(r), "memberCount": int(r["member_count"])} for r in rows]
    _group_cache[key] = (time.time(), out)
    return out


async def group_detail(tag_id: int) -> dict | None:
    """한 그룹의 멤버와 현재 방송 상태.

    쿼리는 **두 번뿐이다**: 그룹 1행, 멤버+최신 스냅샷 조인 1회. 멤버마다 라이브를
    조회하면 멤버 수만큼 왕복이 생긴다(그게 이 화면에서 가장 쉬운 실수다).

    `live`는 가장 최근 성공한 수집 회차 하나만 본다 — 회차를 섞으면 합계가 서로
    다른 시각의 값을 더한 숫자가 돼 "지금 동시 시청자"라는 의미가 깨진다.
    """
    tid = int(tag_id)
    key = ("detail", tid, version())
    hit = _group_cache.get(key)
    if hit is not None and time.time() - hit[0] < GROUP_CACHE_TTL:
        return hit[1]

    db = await get_db()
    tag = await (await db.execute(
        "SELECT * FROM streamer_tags WHERE id = ? AND active = 1",
        (tid,))).fetchone()
    if tag is None:
        return None

    ts_row = await (await db.execute(
        "SELECT MAX(collected_at) t FROM rising_collect_runs WHERE ok = 1")).fetchone()
    ts = ts_row["t"] if ts_row else None

    rows = await (await db.execute(
        """SELECT a.streamer_channel_id AS cid, a.display_order,
                  COALESCE(s.channel_name, c.channel_name, '') AS channel_name,
                  s.concurrent_viewers, s.category_name, s.live_title,
                  -- 팔로워는 스냅샷에만 있다(`rising_channel_stats`에는 없다).
                  s.open_date, s.follower_count
             FROM streamer_tag_assignments a
             LEFT JOIN rising_channel_stats c
                    ON c.chzzk_channel_id = a.streamer_channel_id
             LEFT JOIN rising_live_snapshots s
                    ON s.chzzk_channel_id = a.streamer_channel_id
                   AND s.collected_at = ?
            WHERE a.tag_id = ?
            ORDER BY a.display_order, a.streamer_channel_id
            LIMIT ?""", (ts, tid, GROUP_MEMBER_MAX))).fetchall()

    members = []
    for r in rows:
        viewers = r["concurrent_viewers"]
        members.append({
            "channelId": r["cid"],
            "channelName": r["channel_name"] or r["cid"][:8],
            "channelImageUrl": _channel_image(r["cid"]),
            "displayOrder": int(r["display_order"]),
            # `live=False`와 `concurrentViewers=0`은 다르다 — 전자는 방송 자체가
            # 없는 것이고 후자는 켜져 있는데 시청자가 0인 것이다.
            "live": viewers is not None,
            "concurrentViewers": int(viewers or 0),
            "categoryName": r["category_name"] or "",
            "liveTitle": r["live_title"] or "",
            "openDate": r["open_date"] or "",
            "followerCount": int(r["follower_count"] or 0),
        })

    # 그룹 내 순위는 **현재 시청자 내림차순**이다. 방송하지 않는 멤버는 순위를
    # 받지 않는다(0명으로 줄 세우면 꺼져 있는 사람이 켜져 있는 사람과 섞인다).
    live_sorted = sorted((m for m in members if m["live"]),
                         key=lambda m: -m["concurrentViewers"])
    for i, m in enumerate(live_sorted):
        m["groupRank"] = i + 1

    out = {
        "group": _public(tag),
        "memberCount": len(members),
        "liveCount": len(live_sorted),
        "totalViewers": sum(m["concurrentViewers"] for m in live_sorted),
        "collectedAt": int(ts) if ts is not None else None,
        "members": members,
        "truncated": len(members) >= GROUP_MEMBER_MAX,
    }
    _group_cache[key] = (time.time(), out)
    return out
