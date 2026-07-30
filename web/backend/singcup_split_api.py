"""싱드컵 분리 API — 전체 집합 기준 정렬·검색 + 스냅샷 고정 페이지네이션.

왜 필요한가: `/api/singcup/main`은 참가자 전원을 한 번에 내려 gzip 약 265KB이고,
실측상 그 구간 Egress의 약 95%를 차지한다. 필드를 덜어내도 gzip은 2.7%밖에 줄지
않는다(반복 키는 이미 압축된다) — **행 수를 줄여야만** 의미가 있다.
실측: summary 약 2.0KB, 상위 100명 약 29KB(전체의 11.0%).

핵심 원칙 두 가지.

1. **정렬·검색은 언제나 전체 참가자 집합 기준이다.** 받아온 100명 안에서 다시
   정렬하거나 검색하면 하위권이 통째로 사라지고 순위가 왜곡된다. 여기서는 전체를
   먼저 정렬해 두고 필요한 구간만 잘라 낸다.
2. **페이지 사이에 랭킹이 바뀌어도 중복·누락이 없어야 한다.** 그래서 응답 한 벌을
   불변 스냅샷으로 고정하고(snapshotVersion), 커서는 그 버전 안에서만 유효하다.

스냅샷은 새로 계산하지 않는다. 이미 만들어져 있는 `/main` 캐시 엔트리를 그대로
불변 스냅샷으로 취급하고, 버전 식별자도 그 엔트리의 ETag 지문을 재사용한다
(같은 내용 = 같은 버전이 자동으로 성립한다). 따라서 조회 경로에서 DB 쓰기도,
랭킹 재계산도, 스냅샷 생성도 일어나지 않는다.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import unicodedata
from collections import OrderedDict

# ── 보존 정책 ───────────────────────────────────────────────────────────────
# "최근 N개 또는 T초 중 먼저"는 T초 보존을 보장하지 못한다. 버전이 20초마다 생기면
# N=4는 80초 만에 소진돼, 페이지를 넘기는 도중 커서가 죽는다. 그래서 **최소 세션
# 보존 시간을 우선 보장**하고, 개수·항목 수는 메모리 폭주를 막는 상한으로만 쓴다.
MIN_SESSION_SECONDS = float(os.getenv("SINGCUP_SNAPSHOT_MIN_SESSION_SECONDS", "900"))
# 하드 상한(메모리 보호). 여기에 걸려 TTL 안의 버전이 축출되면 클라이언트는 409를
# 받고 처음부터 다시 받는다 — 이 사실은 API 계약에 명시돼 있다.
MAX_VERSIONS = int(os.getenv("SINGCUP_SNAPSHOT_MAX_VERSIONS", "60"))
# 전체 레지스트리가 보관할 스트리머 항목 수 상한(버전 수 × 참가자 수).
# 참가자가 늘면 버전 수가 자동으로 줄어든다.
MAX_TOTAL_ITEMS = int(os.getenv("SINGCUP_SNAPSHOT_MAX_ITEMS", "200000"))
MAX_PAGE_SIZE = int(os.getenv("SINGCUP_PAGE_MAX_SIZE", "200"))
DEFAULT_PAGE_SIZE = int(os.getenv("SINGCUP_PAGE_DEFAULT_SIZE", "100"))

SPLIT_API_ENABLED = os.getenv("SINGCUP_SPLIT_API_ENABLED", "false").lower() in (
    "1", "true", "yes", "on")
# 스냅샷으로 등록하는 /main 상한. 화면이 쓰는 값과 같아야 한다.
MAX_SNAPSHOT_LIMIT = int(os.getenv("SINGCUP_SNAPSHOT_LIMIT", "3000"))

# ── 커서 서명 키 ────────────────────────────────────────────────────────────
# 프로세스마다 랜덤 키를 만들면 재배포·재시작·워커 분산에서 멀쩡한 커서가 거부된다.
# 그래서 정식 환경변수를 쓰고, 없으면 **분리 API 자체를 켜지 않는다**(임시 키로
# 조용히 도는 것보다 명확히 막는 편이 낫다). 기존 JWT_SECRET을 재사용하지 않는 이유:
# 용도가 다른 비밀이 섞이면 한쪽을 교체할 때 다른 쪽이 함께 무효가 된다.
CURSOR_SECRET_MIN_LEN = 16
_cursor_secret = (os.getenv("SINGCUP_CURSOR_SECRET") or "").strip()
CURSOR_SECRET_OK = len(_cursor_secret) >= CURSOR_SECRET_MIN_LEN
if SPLIT_API_ENABLED and not CURSOR_SECRET_OK:
    # 값은 절대 찍지 않는다 — 길이 요건만 알린다.
    print("[singcup_split_api] SINGCUP_SPLIT_API_ENABLED=true 이지만 "
          f"SINGCUP_CURSOR_SECRET이 없거나 {CURSOR_SECRET_MIN_LEN}자 미만이라 "
          "분리 API를 비활성화합니다.", flush=True)
    SPLIT_API_ENABLED = False
# 비활성 상태에서도 모듈은 기동해야 하므로(테스트 포함) 임시 키를 둔다.
_CURSOR_KEY = (_cursor_secret or secrets.token_hex(16)).encode()

# ── 정렬 규칙 ───────────────────────────────────────────────────────────────
# 오름차순을 내림차순 배열의 reverse()로 만들지 않는다 — 그러면 동점자 순서까지
# 뒤집혀 같은 데이터에서 페이지 경계가 달라진다. 방향은 **주 키에만** 적용하고
# 동점 기준과 null 위치는 두 방향에서 동일하게 고정한다.
def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _volatility(s: dict) -> float:
    """'변동이 많은 순' — 순위가 오르든 내리든 움직인 폭으로 본다."""
    rd = s.get("rankDelta")
    return abs(_num(rd)) if rd is not None else 0.0


# name -> (주 키 함수, 값이 없으면 None을 반환할지 여부)
SORTS: dict[str, callable] = {
    "score":     lambda s: _num(s.get("score")),
    "heart":     lambda s: _num(s.get("heartCount")),
    "view":      lambda s: _num(s.get("viewCount")),
    "follower":  lambda s: _num(s.get("followerCount")),
    "heart1h":   lambda s: None if s.get("heartDelta") is None else _num(s["heartDelta"]),
    "heart24h":  lambda s: (None if s.get("heartChangeRate24h") is None
                            else _num(s["heartChangeRate24h"])),
    "rankdelta": lambda s: None if s.get("rankDelta") is None else _num(s["rankDelta"]),
    "volatility": lambda s: None if s.get("rankDelta") is None else _volatility(s),
}
DIRECTIONS = ("desc", "asc")


def _sort_key(name: str, direction: str):
    primary = SORTS[name]
    sign = 1.0 if direction == "asc" else -1.0

    def key(s: dict):
        v = primary(s)
        # 값이 없는 항목은 **방향과 무관하게 항상 뒤로**. 0과 '모름'은 다르다.
        has = 0 if v is None else 1
        # 동점 기준은 두 방향에서 같다: 하트↓ → 점수↓ → channelId↑
        return (-has, sign * (0.0 if v is None else v),
                -_num(s.get("heartCount")), -_num(s.get("score")),
                str(s.get("channelId") or ""))
    return key


# ── 닉네임 정규화 ───────────────────────────────────────────────────────────
def normalize_query(q: str) -> str:
    """검색 비교용 정규화 — 유니코드 NFC + 소문자 + 앞뒤 공백 제거.

    한글은 조합형/완성형이 섞여 들어올 수 있어 NFC로 맞춘다. 자모 분리 검색은
    지원하지 않는다(별도 색인이 필요하고, 지금 화면 요구에도 없다).
    """
    return unicodedata.normalize("NFC", str(q or "")).strip().lower()


# ── 커서 ────────────────────────────────────────────────────────────────────
def filter_hash(**parts) -> str:
    """엔드포인트별 필터를 커서에 묶기 위한 짧은 지문.

    검색어가 바뀌었는데 이전 커서를 그대로 쓰면 전혀 다른 결과 집합의 위치를
    가리키게 되어 중복·누락이 생긴다. 필터가 조금이라도 다르면 커서를 거부한다.
    """
    canon = json.dumps(parts, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str).encode()
    return hashlib.sha256(canon).hexdigest()[:12]


def encode_cursor(version: str, sort: str, direction: str, channel_id: str,
                  *, endpoint: str = "rankings", fhash: str = "") -> str:
    """(버전, 엔드포인트, 필터, 정렬, 방향, 마지막 항목)을 서명해 담는다.

    서명은 위조 방지가 아니라 **잘못된 커서를 조용히 받아들이지 않기 위한** 것이다.
    조작된 커서로 엉뚱한 위치에서 페이지가 시작되면 중복·누락이 생긴다.
    """
    body = json.dumps({"v": version, "s": sort, "d": direction, "k": channel_id,
                       "e": endpoint, "f": fhash},
                      separators=(",", ":"), ensure_ascii=False).encode()
    sig = hmac.new(_CURSOR_KEY, body, hashlib.sha256).hexdigest()[:24]
    # 서명은 **hex**로 붙인다. 원시 바이트를 섞어 넣으면 서명 안에 '.'(0x2e)이
    # 들어가는 경우가 약 4.5% 생기고, 그때 구분자 분리가 어긋나 멀쩡한 커서가
    # 거부된다(테스트에서 실제로 잡혔다). base64url·hex 모두 '.'을 쓰지 않는다.
    return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + sig


def decode_cursor(cursor: str) -> dict | None:
    try:
        b64, sig = cursor.rsplit(".", 1)
        pad = "=" * (-len(b64) % 4)
        body = base64.urlsafe_b64decode(b64 + pad)
        if not hmac.compare_digest(
                sig, hmac.new(_CURSOR_KEY, body, hashlib.sha256).hexdigest()[:24]):
            return None
        d = json.loads(body)
        return d if {"v", "s", "d", "k", "e", "f"} <= set(d) else None
    except Exception:      # noqa: BLE001 — 잘못된 커서는 조용히 거절한다
        return None


class CursorError(ValueError):
    """커서가 위조됐거나 요청한 정렬과 맞지 않는다."""


class SnapshotExpired(LookupError):
    def __init__(self, latest: str | None):
        super().__init__("snapshot_expired")
        self.latest = latest


# ── 랭킹 전용 버전 ──────────────────────────────────────────────────────────
# 전체 `/main` ETag를 그대로 쓰면 페이지네이션과 무관한 변화(집계 시각, 경과 초,
# top movers 계산 시각, collector 상태)만으로도 새 버전이 생겨 커서가 죽는다.
# 그래서 **페이지네이션 대상 집합만** 지문으로 삼는다.
def ranking_version(data: dict) -> str:
    """반환되는 스트리머 집합만으로 버전을 만든다.

    포함: 스트리머 항목 전체 — 정렬 키, 검색 대상 닉네임, live 상태, 화면에 나가는
          필드, 확정된 rank.
    제외: summary·collector·live 메타·movers 계산 시각 등 봉투의 운영 정보.

    입력 순서에 흔들리지 않도록 channelId로 정렬하고 키도 정렬해 직렬화한다.
    """
    rows = sorted((data.get("streamers") or []),
                  key=lambda s: str(s.get("channelId") or ""))
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str).encode()
    return hashlib.sha256(canonical).hexdigest()[:32]


def _freeze(s: dict) -> dict:
    """등록 시점의 값을 복사해 둔다.

    `/main` 캐시의 dict를 그대로 참조하면, 그 객체가 이후 어디선가 바뀌었을 때
    **이미 발급한 버전의 내용이 조용히 달라진다**. 중첩된 live dict까지 복사해
    스냅샷이 실제로 불변임을 코드로 보장한다(나머지 값은 전부 스칼라라 전체 deep
    copy는 필요 없다).
    """
    out = dict(s)
    live = out.get("live")
    if isinstance(live, dict):
        out["live"] = dict(live)
    return out


# ── 스냅샷 ──────────────────────────────────────────────────────────────────
class Snapshot:
    """`/main` 응답 한 벌을 불변으로 고정한 것.

    정렬 목록은 요청이 처음 들어올 때만 만든다(8종 × 2방향을 미리 다 만들면
    쓰지도 않을 목록에 메모리를 쓴다). 만든 뒤에는 바뀌지 않는다.
    """

    __slots__ = ("version", "generated_at", "created_mono", "envelope", "streamers",
                 "_orders", "_live", "_norm")

    def __init__(self, version: str, data: dict):
        self.version = version
        # 스트리머는 복사본으로, 봉투도 필요한 부분만 복사해 고정한다.
        self.streamers: list[dict] = [_freeze(s) for s in (data.get("streamers") or [])]
        self.envelope = {
            "event": dict(data.get("event") or {}),
            "summary": dict(data.get("summary") or {}),
            "topHeartMovers1h": [dict(m) for m in (data.get("topHeartMovers1h") or [])],
            "topHeartMovers1hStale": data.get("topHeartMovers1hStale"),
            "topHeartMovers1hBaseAt": data.get("topHeartMovers1hBaseAt"),
            "live": dict(data.get("live") or {}),
            "collector": dict(data.get("collector") or {}),
        }
        self.generated_at = self.envelope["collector"].get("lastSuccessAt")
        self.created_mono = time.monotonic()
        self._orders: dict[tuple[str, str], tuple[list[dict], dict[str, int]]] = {}
        self._live: list[dict] | None = None
        # 검색용 정규화 닉네임을 한 번만 만들어 둔다(요청마다 다시 만들지 않는다)
        self._norm = {s["channelId"]: normalize_query(s.get("channelName") or "")
                      for s in self.streamers}

    def order(self, sort: str, direction: str) -> tuple[list[dict], dict[str, int]]:
        key = (sort, direction)
        hit = self._orders.get(key)
        if hit is None:
            items = sorted(self.streamers, key=_sort_key(sort, direction))
            pos = {s["channelId"]: i for i, s in enumerate(items)}
            hit = self._orders[key] = (items, pos)
        return hit

    def live(self) -> list[dict]:
        if self._live is None:
            # 라이브도 순위와 무관하므로 전체에서 고른다. 시청자 많은 순 고정.
            self._live = sorted(
                (s for s in self.streamers if s.get("live")),
                key=lambda s: (-_num((s.get("live") or {}).get("concurrentViewers")),
                               -_num(s.get("heartCount")), str(s.get("channelId"))))
        return self._live

    def matches(self, q: str) -> list[str]:
        """전체 참가자에서 닉네임 부분 일치. 반환은 channelId 집합용 목록."""
        nq = normalize_query(q)
        if not nq:
            return []
        return [cid for cid, name in self._norm.items() if nq in name]


_registry: dict[str, Snapshot] = {}
_order: list[str] = []          # 오래된 것 → 최신
_stats = {"registered": 0, "evicted": 0, "evicted_by_cap": 0,
          "evicted_by_items": 0, "expired_requests": 0, "served": 0,
          "render_hit": 0, "render_miss": 0}


def register(data: dict, *, version: str | None = None) -> Snapshot:
    """완성된 `/main` 페이로드를 스냅샷으로 등록한다.

    호출 시점은 `/main` 캐시를 채우는 바로 그 지점이다 — 새로 계산하는 것이 아니라
    이미 만들어진 결과를 복사해 고정하기만 하므로 DB 접근이 없다. 실패했거나
    불완전한 응답은 애초에 캐시에 들어가지 않으므로 여기까지 오지 않는다.

    버전은 **랭킹 집합만의 지문**이다. summary 시각이나 collector 상태만 달라진
    경우에는 같은 버전이 되어 진행 중인 커서가 그대로 살아 있다.
    """
    v = version or ranking_version(data)
    if v in _registry:
        return _registry[v]
    snap = Snapshot(v, data)
    _registry[v] = snap
    _order.append(v)
    _stats["registered"] += 1
    _evict()
    return snap


def _total_items() -> int:
    return sum(len(s.streamers) for s in _registry.values())


def _evict() -> None:
    """최소 세션 보존 시간을 먼저 지키고, 상한에 걸릴 때만 더 버린다."""
    now = time.monotonic()
    for v in list(_order):
        s = _registry.get(v)
        if s is None or now - s.created_mono > MIN_SESSION_SECONDS:
            _drop(v)
    # 아래 두 상한은 메모리 보호용이다. 여기서 버려지는 버전은 아직 보존 시간
    # 안이므로, 그 커서를 쓰던 클라이언트는 409를 받고 처음부터 다시 받는다.
    # 이 동작은 API 계약에 명시돼 있다.
    while len(_order) > max(1, MAX_VERSIONS):
        _stats["evicted_by_cap"] += 1
        _drop(_order[0])
    while len(_order) > 1 and _total_items() > MAX_TOTAL_ITEMS:
        _stats["evicted_by_items"] += 1
        _drop(_order[0])


def _drop(version: str) -> None:
    _drop_rendered(version)
    if version in _registry:
        del _registry[version]
        _stats["evicted"] += 1
    if version in _order:
        _order.remove(version)


def latest() -> Snapshot | None:
    _evict()
    return _registry.get(_order[-1]) if _order else None


def get(version: str | None) -> Snapshot:
    """버전을 고정해 가져온다. 만료됐으면 최신을 섞지 않고 명확히 알린다."""
    _evict()
    if not version:
        cur = latest()
        if cur is None:
            raise SnapshotExpired(None)
        return cur
    snap = _registry.get(version)
    if snap is None:
        _stats["expired_requests"] += 1
        cur = latest()
        raise SnapshotExpired(cur.version if cur else None)
    return snap


def stats() -> dict:
    return {**_stats, "versions": len(_order),
            "minSessionSeconds": MIN_SESSION_SECONDS,
            "maxVersions": MAX_VERSIONS, "maxTotalItems": MAX_TOTAL_ITEMS,
            "totalItems": _total_items(),
            "oldestAgeSeconds": (
                round(time.monotonic() - _registry[_order[0]].created_mono, 1)
                if _order else None),
            "cursorSecretConfigured": CURSOR_SECRET_OK}


def reset() -> None:
    _registry.clear()
    _order.clear()
    _render.clear()


# ── 페이지 자르기 ───────────────────────────────────────────────────────────
# ── 응답 bytes 캐시 ────────────────────────────────────────────────────────
# `/main`은 캐시에 직렬화된 bytes를 들고 있어 요청당 재직렬화가 없다. 분리 API가
# 매번 dict를 직렬화하면 요청당 CPU가 오히려 늘어난다(실측: 1,079명에서 p50
# 3.1ms → 7.2ms). 페이지 응답은 (버전, 엔드포인트, 필터, 정렬, 방향, 커서, size)에
# 대해 완전히 결정적이므로 그대로 캐시할 수 있다. 버전이 축출되면 함께 버린다.
RENDER_CACHE_MAX = int(os.getenv("SINGCUP_RENDER_CACHE_MAX", "128"))
_render: "OrderedDict[tuple, bytes]" = OrderedDict()


def render(key: tuple, obj: dict) -> bytes:
    hit = _render.get(key)
    if hit is not None:
        _render.move_to_end(key)
        _stats["render_hit"] += 1
        return hit
    body = json.dumps(obj, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")
    _render[key] = body
    _stats["render_miss"] += 1
    while len(_render) > max(1, RENDER_CACHE_MAX):
        _render.popitem(last=False)
    return body


def _drop_rendered(version: str) -> None:
    for k in [k for k in _render if k and k[0] == version]:
        _render.pop(k, None)


def _clamp(size: int | None) -> int:
    if not size or size < 1:
        return DEFAULT_PAGE_SIZE
    return min(int(size), MAX_PAGE_SIZE)


def _slice(items: list[dict], pos: dict[str, int], snap: Snapshot, sort: str,
           direction: str, cursor: str | None, size: int,
           *, endpoint: str = "rankings", fhash: str = "") -> dict:
    start = 0
    if cursor:
        c = decode_cursor(cursor)
        if c is None:
            raise CursorError("cursor_invalid")
        if c["v"] != snap.version:
            # 다른 버전의 커서 — 최신을 섞지 않고 처음부터 다시 받게 한다
            raise SnapshotExpired(snap.version)
        if c.get("e") != endpoint:
            # 다른 엔드포인트의 커서(예: search 커서를 rankings에 사용)
            raise CursorError("cursor_endpoint_mismatch")
        if c.get("f") != fhash:
            # 검색어·필터가 바뀌면 결과 집합 자체가 달라진다
            raise CursorError("cursor_filter_mismatch")
        if c["s"] != sort or c["d"] != direction:
            # 정렬을 바꾸면 위치가 의미를 잃는다. 조용히 무시하면 중복·누락이 생긴다.
            raise CursorError("cursor_sort_mismatch")
        at = pos.get(c["k"])
        if at is None:
            raise CursorError("cursor_key_not_found")
        start = at + 1
    page = items[start:start + size]
    nxt = (encode_cursor(snap.version, sort, direction, page[-1]["channelId"],
                         endpoint=endpoint, fhash=fhash)
           if page and start + size < len(items) else None)
    _stats["served"] += 1
    return {
        "snapshotVersion": snap.version,
        "generatedAt": snap.generated_at,
        "total": len(items),
        "items": page,
        "nextCursor": nxt,
        "hasMore": nxt is not None,
        "sort": sort,
        "direction": direction,
    }


def rankings(*, size: int | None = None, cursor: str | None = None,
             sort: str = "score", direction: str = "desc",
             snapshot_version: str | None = None) -> dict:
    """전체 참가자를 서버에서 정렬한 뒤 필요한 구간만 잘라 준다."""
    if sort not in SORTS:
        raise CursorError("sort_unknown")
    if direction not in DIRECTIONS:
        raise CursorError("direction_unknown")
    snap = get(snapshot_version)
    items, pos = snap.order(sort, direction)
    out = _slice(items, pos, snap, sort, direction, cursor, _clamp(size),
                 endpoint="rankings", fhash=filter_hash())
    out["_renderKey"] = (snap.version, "rankings", sort, direction, cursor, _clamp(size))
    return out


def search(q: str, *, size: int | None = None, cursor: str | None = None,
           sort: str = "score", direction: str = "desc",
           snapshot_version: str | None = None) -> dict:
    """**전체 DB 집합**에서 닉네임을 찾은 뒤 같은 정렬 기준으로 돌려준다.

    받아온 페이지 안에서 찾는 것이 아니다 — 그러면 하위권은 닉네임을 정확히 쳐도
    나오지 않는다.
    """
    if sort not in SORTS:
        raise CursorError("sort_unknown")
    if direction not in DIRECTIONS:
        raise CursorError("direction_unknown")
    snap = get(snapshot_version)
    hits = set(snap.matches(q))
    ordered, _pos = snap.order(sort, direction)
    items = [s for s in ordered if s["channelId"] in hits]
    pos = {s["channelId"]: i for i, s in enumerate(items)}
    out = _slice(items, pos, snap, sort, direction, cursor, _clamp(size),
                 endpoint="search", fhash=filter_hash(q=normalize_query(q)))
    out["query"] = q
    out["_renderKey"] = (snap.version, "search", normalize_query(q), sort, direction,
                         cursor, _clamp(size))
    return out


def live(*, size: int | None = None, cursor: str | None = None,
         snapshot_version: str | None = None) -> dict:
    """방송 중인 참가자만. 라이브도 344명 규모(gzip 약 89KB)라 페이지로 나눈다."""
    snap = get(snapshot_version)
    items = snap.live()
    pos = {s["channelId"]: i for i, s in enumerate(items)}
    out = _slice(items, pos, snap, "live", "desc", cursor, _clamp(size),
                 endpoint="live", fhash=filter_hash(live=True))
    out["liveInfo"] = snap.envelope.get("live")
    out["_renderKey"] = (snap.version, "live", cursor, _clamp(size))
    return out


def movers(*, range_: str = "1h", size: int | None = None,
           snapshot_version: str | None = None) -> dict:
    snap = get(snapshot_version)
    if range_ != "1h":
        raise CursorError("range_unsupported")
    items = (snap.envelope.get("topHeartMovers1h") or [])[:_clamp(size)]
    return {
        "snapshotVersion": snap.version, "generatedAt": snap.generated_at,
        "range": range_, "total": len(items), "items": items,
        "stale": bool(snap.envelope.get("topHeartMovers1hStale")),
        "baseAt": snap.envelope.get("topHeartMovers1hBaseAt"),
    }


def summary(*, snapshot_version: str | None = None) -> dict:
    """첫 화면에 필요한 최소 응답. 실측 gzip 약 2.0KB(전체의 0.7%)."""
    snap = get(snapshot_version)
    d = snap.envelope
    return {
        "snapshotVersion": snap.version, "generatedAt": snap.generated_at,
        "event": d.get("event"), "summary": d.get("summary"),
        "topHeartMovers1h": d.get("topHeartMovers1h"),
        "topHeartMovers1hStale": d.get("topHeartMovers1hStale"),
        "topHeartMovers1hBaseAt": d.get("topHeartMovers1hBaseAt"),
        "live": d.get("live"), "collector": d.get("collector"),
        "liveCount": len(snap.live()),
    }


def delta(*, since_version: str | None = None) -> dict:
    """버전 간 변경분. **아직 구현하지 않는다.**

    두 스냅샷의 차이를 신뢰성 있게 내려면 어떤 버전이 클라이언트에 실제로 도달했는지
    알아야 하고, 만료된 버전과의 비교를 어떻게 처리할지도 정해야 한다. 어설프게
    구현하면 클라이언트 병합이 조용히 어긋난다. 인터페이스만 고정해 둔다.
    """
    cur = latest()
    return {
        "status": "not_available",
        "reason": "snapshotVersion 간 변경 비교는 다음 단계에서 구현합니다.",
        "sinceVersion": since_version,
        "latestSnapshotVersion": cur.version if cur else None,
        "items": [],
    }
