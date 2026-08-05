#!/usr/bin/env python3
"""싱드컵 한국 조회수 복구 poller — AWS 서울 EC2에서 실행한다.

**왜 한국에서 돌아야 하나.** 하트는 치지직 카드 응답의
`interaction.emotion.reactions`에 있고 조회수는 `content.vod.count`에 있다.
`krOnlyViewing=true`(한국 전용 재생) 클립을 Railway 해외 리전에서 부르면 HTTP는
200인데 **`content.vod` 블록이 통째로 빠진다**. 하트 블록은 남으므로 하트만
갱신되고 조회수는 계속 못 읽는다. 조회수가 0으로 응답된 것이 아니라 **컨테이너가
누락된 것**이라 상태는 `observed_zero`가 아니라 `unknown`이고, 0으로 저장하면 안
된다. 한국에서 같은 API를 불러야 실제 값을 받을 수 있다.

**이 프로세스가 하지 않는 것.**
  - 인바운드 포트를 열지 않는다(서버가 아니다).
  - 운영 DB에 접근하지 않는다. DB 경로도 접속 문자열도 SQL도 갖고 있지 않다.
  - 값을 판정하지 않는다. 검증과 저장은 전부 Railway가 한다.
  - 재시도를 무한히 하지 않는다. 남은 일은 lease 만료 후 다음 회차가 가져간다.

표준 라이브러리만 쓴다 — EC2에 패키지를 설치하지 않기 위해서다.

`Type=oneshot` + timer로 돌기 때문에 **회차 사이에 메모리가 남지 않는다.**
그래서 429의 Retry-After처럼 다음 실행까지 지켜야 하는 상태는 `StateDirectory`
아래 파일에 남긴다(그 파일에는 시각 하나만 들어간다).
"""
import email.utils
import hashlib
import hmac
import json
import os
import signal
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone

TASKS_PATH = "/api/internal/singcup/kr-poller/tasks"
RESULTS_PATH = "/api/internal/singcup/kr-poller/results"

# 상류 호스트는 상수다. task로 URL을 받지 않으므로 SSRF 표면이 없다.
CARD_URL = "https://api-videohub.naver.com/shortformhub/feeds/v5/card"
DETAIL_URL = "https://api.chzzk.naver.com/service/v1/clips/{uid}/detail"

# Retry-After가 없거나 해석되지 않을 때의 보수적 기본값과 상한.
RETRY_AFTER_DEFAULT = 600
RETRY_AFTER_MAX = 3600

_stop = False
# 회차 하드 상한(monotonic). run_once()가 채운다.
_run_deadline = [None]


def log(**kw):
    """clipUid·상태·소요시간·시도 횟수만 남긴다.

    URL 쿼리·서명·nonce·secret·토큰·IP·응답 본문은 남기지 않는다 — journald는
    평문이고 이 값들은 한 번 새면 회수할 수 없다.
    """
    print(json.dumps(kw, ensure_ascii=False), flush=True)


# ── 환경변수 (안전 파싱) ───────────────────────────────────────────────────
# 잘못된 값이 들어와도 **import와 기동이 죽지 않는다**. 값의 원문은 로그에 남기지
# 않는다 — 환경변수에 실수로 다른 비밀이 들어갔을 때 그것이 새는 통로가 된다.
def _int_env(name, default, lo, hi):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        log(event="krp_bad_env", name=name, reason="not_an_integer",
            using_default=default)
        return default
    if not (lo <= value <= hi):
        log(event="krp_bad_env", name=name, reason="out_of_range",
            min=lo, max=hi, using_default=default)
        return default
    return value


def _float_env(name, default, lo, hi):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        log(event="krp_bad_env", name=name, reason="not_a_number",
            using_default=default)
        return default
    # NaN은 어떤 비교에도 False라 범위 검사만으로는 통과해 버린다. 먼저 거른다.
    if value != value or value in (float("inf"), float("-inf")):
        log(event="krp_bad_env", name=name, reason="not_finite",
            using_default=default)
        return default
    if not (lo <= value <= hi):
        log(event="krp_bad_env", name=name, reason="out_of_range",
            min=lo, max=hi, using_default=default)
        return default
    return value


API_BASE = (os.environ.get("KRP_API_BASE") or "").rstrip("/")
SECRET = os.environ.get("SINGCUP_KR_POLLER_SECRET", "")
BATCH = _int_env("KRP_BATCH", 25, 1, 25)
# **1 req/s를 넘지 않는다.** 상한을 여기서 잘라 두면 환경변수 실수가 상류에
# 부하를 만들 수 없다.
RATE = _float_env("KRP_RATE_PER_SECOND", 1.0, 0.01, 1.0)
MAX_RETRIES = _int_env("KRP_MAX_RETRIES", 2, 0, 2)

# ── 제한 시간은 **상대별로 다르다** ────────────────────────────────────────
# 하나의 값으로 묶어 두었다가 첫 운영 회차가 죽었다. 치지직 조회 25건은 전부
# 200으로 성공했는데 마지막 `POST /results` 응답을 기다리다 10초를 넘겨
# `TimeoutError`가 났다. 결과 endpoint는 25건을 순차 검증하며 clip lock을 잡고
# DB에 반영한 뒤 마지막에 `recompute_ranking()`까지 돌린다 — 조회 한 건과는
# 애초에 시간 규모가 다르다.
#
# 그렇다고 **전부 늘리면 안 된다.** 치지직 쪽 제한을 키우면 응답 없는 상류
# 하나가 회차를 잡아먹고 그 시간이 `_get_bounded`의 재시도와 곱해진다.
# 늘려야 하는 것은 결과 제출 하나뿐이다.
_LEGACY_TIMEOUT = _float_env("KRP_TIMEOUT_SECONDS", 10.0, 1.0, 60.0)
# 치지직 detail/card 조회. 기존 값을 그대로 유지한다.
CHZZK_TIMEOUT = _float_env("KRP_CHZZK_TIMEOUT_SECONDS", _LEGACY_TIMEOUT, 1.0, 60.0)
# Railway `POST /tasks`. lease 발급만 하므로 짧아도 된다.
CONTROL_TIMEOUT = _float_env("KRP_CONTROL_TIMEOUT_SECONDS", _LEGACY_TIMEOUT, 1.0, 60.0)
# Railway `POST /results`. 25건 반영 + 순위 재계산을 포괄해야 한다.
RESULTS_TIMEOUT = _float_env("KRP_RESULTS_TIMEOUT_SECONDS", 60.0, 5.0, 180.0)

# ── 실행 상한과 파생 예산 ──────────────────────────────────────────────────
# 아래 두 값은 **바깥 세계가 정한 상수**다. 코드가 이것을 모르면 개별 범위
# 검사를 통과한 조합이 유닛 상한을 조용히 넘긴다(예: control 60 + results 180 +
# budget 600 = 840초 > 300초). 그래서 관측 예산은 환경변수를 그대로 쓰지 않고
# **남는 시간에서 역산해 잘라 낸다.**
UNIT_START_LIMIT = 300.0        # systemd `TimeoutStartSec`과 같은 값
LEASE_SECONDS_HINT = 600.0      # 서버 `SINGCUP_KRP_LEASE_SECONDS` 기본값
# systemd 여유. 프로세스 기동·인터프리터 로드·JSON 직렬화·systemd 스케줄링
# 오차가 여기에 들어간다. 예전에는 20초였는데, 그러면 각 단계 최악값의 합이
# `TimeoutStartSec`과 **정확히 같아져** 여유가 0이었다 — 정상 실행도 강제 종료될
# 수 있다. 실측 canary가 약 35초로 끝나므로 50초를 남겨도 관측이 부족하지 않다.
SAFETY_MARGIN = 50.0

# **회차 전체의 하드 상한.** 개별 단계 timeout의 단순 합이 아니라 이 하나의
# monotonic deadline이 상한을 정한다. 합산 방식은 각 단계가 최악에 못 미쳐도
# 계산상으로만 커져 예산을 낭비하고, 반대로 단계가 늘어나면 조용히 상한을 넘는다.
RUN_BUDGET = UNIT_START_LIMIT - SAFETY_MARGIN        # 250초

# 관측 단계 예산. 이것이 없으면 상류가 느릴 때 관측만으로 상한을 다 써 버리고
# **결과를 제출하지 못한 채** 죽는다 — 그러면 그 회차 관측이 통째로 버려지고
# lease만 소모된다. 예산이 끝나면 지금까지 모은 결과를 제출하고 정상 종료한다.
# 남은 후보는 lease 만료 후 다음 회차가 가져간다.
#
# **하드 예산이다.** deadline은 `observe()` → `_get_bounded()` → `_get()`까지
# 내려가고 각 요청 timeout과 backoff·rate sleep이 남은 시간으로 잘린다.
# 시작 전에만 확인하는 소프트 제한이면 마지막 한 건이 detail 3회 + card 3회를
# 통째로 더 돌아 상한을 넘긴다.
# ── `/tasks` 전용 503 재시도 ────────────────────────────────────────────
# 실측(2026-08-05 UTC 03:24:37 / 03:25:21 / 03:36:22): 세 번 모두
# `db_locked_giveup what=krp_nonce attempts=2 budgetSeconds=0.8` →
# `krp_nonce_db_busy` → `/tasks` 503. 같은 시각 일반 collector도
# discover/recheck/deletion이 database_locked로 실패했다 — KRP만의 문제가
# 아니라 **전역 SQLite 쓰기 경합**이고, 근본 원인(장기 write lock 보유자)은
# 별도 감사 대상이다. 여기서는 그 짧은 경합 창만 넘긴다.
#
# nonce가 기록되지 않았으므로 lease도 발급되지 않았다 → 중복 처리·데이터 손상
# 없음. 그래서 재시도가 안전하다. **busy timeout 상향이나 nonce 우회로 덮지
# 않는다** — 그건 검증을 깎는 것이지 경합을 없애는 게 아니다.
#
# `KRP_MAX_RETRIES`를 재사용하지 않는 이유: 그것은 `_get_bounded`의 **치지직
# 5xx/timeout** 재시도 횟수다. 상대도 실패 종류도 다른 값을 하나로 묶으면 한쪽을
# 조정할 때 다른 쪽이 조용히 따라 움직인다.
TASKS_RETRY_MAX = _int_env("KRP_TASKS_RETRY_MAX", 2, 0, 3)
# 대기에 쓸 수 있는 총 시간. 개별 Retry-After가 아무리 커도 이 예산을 넘지 않는다.
TASKS_RETRY_BUDGET = _float_env("KRP_TASKS_RETRY_BUDGET_SECONDS", 60.0, 5.0, 120.0)
# 이보다 긴 Retry-After는 "지금 다시 두드릴 상황이 아니다"로 보고 fail-closed한다.
TASKS_RETRY_AFTER_MAX = _float_env("KRP_TASKS_RETRY_AFTER_MAX_SECONDS", 30.0, 1.0, 60.0)

_RAW_OBSERVE_BUDGET = _float_env("KRP_OBSERVE_BUDGET_SECONDS", 180.0, 30.0, 600.0)
_MAX_OBSERVE = (RUN_BUDGET - CONTROL_TIMEOUT * (TASKS_RETRY_MAX + 1)
                - TASKS_RETRY_BUDGET - RESULTS_TIMEOUT)
OBSERVE_BUDGET = max(5.0, min(_RAW_OBSERVE_BUDGET, _MAX_OBSERVE))
if OBSERVE_BUDGET < _RAW_OBSERVE_BUDGET:
    # 조용히 줄이지 않는다 — 왜 적게 처리했는지 로그로 설명돼야 한다.
    log(event="krp_budget_clamped", requested=_RAW_OBSERVE_BUDGET,
        using=OBSERVE_BUDGET, unit_limit=UNIT_START_LIMIT)


class BudgetExhausted(Exception):
    """관측 예산이 끝났다.

    **외부 실패가 아니다.** 조회수 0으로도, `partial` 결과로도 기록하지 않는다 —
    그렇게 하면 관측하지 못한 것이 관측 결과로 굳는다. 호출부는 이것을 받으면
    그 클립을 결과에 넣지 않고 회차를 접는다.
    """


# Railway 수집기와 같은 값을 쓴다(응답이 출발지에 따라 달라지는지 보려면 요청은
# 같아야 한다).
UA = os.environ.get("SINGCUP_USER_AGENT", "NexBot-SingcupCollector/1.0")


def state_path():
    """`StateDirectory=krpoller` → `/var/lib/krpoller`. 테스트는 KRP_STATE_DIR로 덮는다."""
    base = os.environ.get("STATE_DIRECTORY") or os.environ.get("KRP_STATE_DIR") or ""
    # systemd는 여러 디렉터리를 콜론으로 이어 줄 수 있다. 첫 번째만 쓴다.
    base = base.split(os.pathsep)[0] if base else ""
    if not base:
        return ""
    return os.path.join(base, "state.json")


def read_next_allowed_at():
    """다음 허용 시각. 파일이 없거나 손상됐으면 0(= 제한 없음)."""
    path = state_path()
    if not path:
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        value = data.get("next_allowed_at")
        return int(value) if isinstance(value, int) and value > 0 else 0
    except Exception:                                    # noqa: BLE001
        return 0


def write_next_allowed_at(ts):
    """원자적으로 저장한다. **시각 하나만 쓴다** — URL·clipUid·secret·token·IP 없음."""
    path = state_path()
    if not path:
        return False
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"next_allowed_at": int(ts)}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)                            # 같은 파일시스템 → 원자적
        return True
    except Exception:                                    # noqa: BLE001
        log(event="krp_state_write_failed")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def parse_retry_after(value, now):
    """`Retry-After` → 다음 허용 시각(epoch).

    delta-seconds와 HTTP-date 둘 다 받는다. 해석되지 않거나 음수·과대값이면
    보수적 기본값으로 떨어진다 — 상류가 준 값을 못 믿겠다고 해서 곧바로 다시
    두드리면 그게 더 나쁘다.
    """
    if value is not None:
        s = str(value).strip()
        if s.isdigit():                                  # "-5"는 여기 안 걸린다
            secs = int(s)
            if secs > 0:
                return now + min(secs, RETRY_AFTER_MAX)
        elif s:
            try:
                dt = email.utils.parsedate_to_datetime(s)
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    delta = int(dt.timestamp()) - now
                    if delta > 0:
                        return now + min(delta, RETRY_AFTER_MAX)
            except Exception:                            # noqa: BLE001
                pass
    return now + RETRY_AFTER_DEFAULT


def _on_term(_sig, _frm):
    global _stop
    _stop = True


# ── Railway 호출 (서명) ────────────────────────────────────────────────────
def _sign(ts, method, path, raw):
    msg = "%s\n%s\n%s\n%s" % (ts, method, path, hashlib.sha256(raw).hexdigest())
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


def call_api(path, payload, timeout=None):
    """Railway 내부 API 호출. `timeout`은 **호출부가 상대에 맞게 정한다**.

    timestamp·nonce·signature는 매 호출마다 새로 만든다. 같은 payload를 다시
    보내더라도 nonce가 달라 replay(401)로 막히지 않는다.
    """
    raw = json.dumps(payload, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    nonce = hashlib.sha256(os.urandom(32)).hexdigest()[:32]
    req = urllib.request.Request(
        API_BASE + path, data=raw, method="POST",
        headers={"Content-Type": "application/json",
                 "X-KRP-Timestamp": ts,
                 "X-KRP-Nonce": nonce,
                 "X-KRP-Signature": _sign(ts, "POST", path, raw)})
    with urllib.request.urlopen(
            req, timeout=CONTROL_TIMEOUT if timeout is None else timeout,
            context=ssl.create_default_context()) as r:
        return json.loads(r.read().decode("utf-8"))


# ── 치지직 호출 ────────────────────────────────────────────────────────────
class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__("429")
        self.retry_after = retry_after


def run_deadline():
    """이 회차가 끝나야 하는 monotonic 시각. `run_once()`가 시작할 때 정한다."""
    return _run_deadline[0]


def _remaining(deadline):
    """남은 예산(초). deadline이 None이면 제한 없음을 뜻하는 None."""
    return None if deadline is None else deadline - time.monotonic()


def _get(url, referer, timeout=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json", "Referer": referer})
    try:
        with urllib.request.urlopen(
                req, timeout=CHZZK_TIMEOUT if timeout is None else timeout,
                context=ssl.create_default_context()) as r:
            return r.getcode(), json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(e.headers.get("Retry-After"))
        return e.code, None


def _get_bounded(url, referer, deadline=None):
    """5xx·timeout만 제한적으로 재시도한다. 429는 즉시 위로 올린다.

    `deadline`을 주면 **그 시각을 넘기지 않는다** — 매 시도 전에 남은 시간을
    확인하고, 요청 timeout과 backoff를 남은 시간으로 자른다. 남은 시간이 없으면
    새 요청을 시작하지 않고 `BudgetExhausted`로 빠져나온다.
    """
    last = (None, None)
    for attempt in range(MAX_RETRIES + 1):
        left = _remaining(deadline)
        if left is not None and left <= 0:
            raise BudgetExhausted()
        timeout = CHZZK_TIMEOUT if left is None else min(CHZZK_TIMEOUT, left)
        try:
            status, body = _get(url, referer, timeout)
            if status is not None and status < 500:
                return status, body, attempt + 1
            last = (status, body)
        except RateLimited:
            raise
        except Exception:                                # noqa: BLE001
            last = (None, None)
        if attempt < MAX_RETRIES and not _stop:
            backoff = min(4.0, 1.0 * (2 ** attempt))
            left = _remaining(deadline)
            if left is not None:
                if left <= 0:
                    raise BudgetExhausted()
                # 남은 예산보다 길게 자면 그 자체가 초과다.
                backoff = min(backoff, left)
            time.sleep(backoff)
    # 재시도를 다 쓰고 나왔는데 예산도 끝났다면 그것은 **상류 실패가 아니라
    # 예산 종료**다. 여기서 (None, None)을 돌려주면 호출부가 그것을 partial
    # 관측으로 기록해 버린다 — 관측하지 못한 것이 관측 결과로 굳는다.
    if deadline is not None and _remaining(deadline) <= 0:
        raise BudgetExhausted()
    return last[0], last[1], MAX_RETRIES + 1


def observe(task, deadline=None):
    """한 클립을 관측한다. 조회수를 못 읽으면 **0으로 만들지 않고** partial로 보고한다.

    `deadline`을 넘기면 `BudgetExhausted`가 올라간다. 그 클립은 결과에 담지
    않는다 — 관측하지 못한 것을 관측 결과로 만들지 않기 위해서다.
    """
    uid = task["clipUid"]
    referer = "https://chzzk.naver.com/clips/" + urllib.parse.quote(uid, safe="")
    t0 = time.time()

    def _result(**kw):
        # leaseToken을 반드시 함께 돌려준다 — Railway가 "이 task를 실제로 발급받은
        # 쪽인가"를 task 단위로 확인한다. 서명만으로는 서명 키를 가진 쪽이 자기가
        # 받지 않은 taskId에 결과를 밀어 넣는 것까지 막지 못한다.
        base = {"clipUid": uid, "taskId": task["taskId"],
                "leaseToken": task.get("leaseToken", ""),
                "observedAt": int(time.time()), "heartCount": None,
                "_ms": int((time.time() - t0) * 1000)}
        base.update(kw)
        return base

    video_id = task.get("videoId") or ""
    rec_id = task.get("recId") or ""
    attempts = 0
    if not video_id:
        status, body, n = _get_bounded(DETAIL_URL.format(
            uid=urllib.parse.quote(uid, safe="")), referer, deadline)
        attempts += n
        content = (body or {}).get("content") or {}
        video_id = content.get("videoId") or ""
        rec_id = rec_id or content.get("recId") or ""
        if not video_id:
            return _result(httpStatus=status or 0, viewState="partial",
                           viewCount=None, attempts=attempts)

    params = urllib.parse.urlencode({
        "seedType": "SPECIFIC", "serviceType": "CHZZK", "seedMediaId": video_id,
        "mediaType": "VOD", "panelType": "sdk_chzzk", "referer": referer,
        "recType": "CHZZK", "recId": rec_id, "enableReverse": "false",
        "adAllowed": "Y", "clickNsc": "chzzk_category_clip",
        "clickArea": "clip_item", "deviceType": "html5_pc"})
    status, body, n = _get_bounded(CARD_URL + "?" + params, referer, deadline)
    attempts += n

    card = (body or {}).get("card") if isinstance(body, dict) else None
    vod = ((card or {}).get("content") or {}).get("vod")
    view = vod.get("count") if isinstance(vod, dict) else None
    reactions = (((card or {}).get("interaction") or {}).get("emotion")
                 or {}).get("reactions") or []
    likes = [r.get("count") for r in reactions
             if isinstance(r, dict) and r.get("reactionType") == "like"]
    # `content.vod`가 없으면 그것이 곧 지역 차단 신호다. 여기서 0을 만들지 않는다.
    ok = status == 200 and isinstance(vod, dict) and view is not None
    return _result(httpStatus=status or 0,
                   viewState="observed" if ok else "partial",
                   viewCount=view if ok else None,
                   heartCount=likes[0] if likes else None,
                   attempts=attempts)


# ── 한 회차 ────────────────────────────────────────────────────────────────
def _retry_after_seconds(err):
    """503의 `Retry-After`가 **명시적이고 상식적일 때만** 대기 초를 돌려준다.

    없거나 문자열·0·음수·과도하면 None — 상류가 준 값을 못 믿겠다고 곧바로 다시
    두드리면 그게 더 나쁘다. HTTP-date는 여기서 받지 않는다(이 경로의 상류는
    항상 delta-seconds를 준다).
    """
    raw = None
    try:
        raw = err.headers.get("Retry-After")
    except Exception:                                # noqa: BLE001
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isdigit():                           # "-5"·"abc"는 여기서 걸린다
        return None
    secs = int(text)
    if secs <= 0 or secs > TASKS_RETRY_AFTER_MAX:
        return None
    return float(secs)


def lease_tasks_with_retry():
    """`/tasks`만 제한적으로 다시 두드린다. 실패는 실패로 남긴다.

    매 시도는 `call_api()`를 새로 부르므로 **timestamp·nonce·signature가 전부 새로
    만들어진다** — 같은 nonce를 재전송하면 replay(401)로 막히고, 그건 재시도가
    아니라 사고다.
    """
    spent = 0.0
    for attempt in range(TASKS_RETRY_MAX + 1):
        left = _remaining(run_deadline())
        # 남은 시간이 요청 하나도 못 담으면 **새 요청을 시작하지 않는다.**
        # 0초·음수 timeout으로 소켓을 여는 일이 없어야 한다.
        if left is not None and left <= 1.0:
            log(event="krp_deadline_exhausted", stage="tasks",
                remaining_seconds=round(max(0.0, left), 3))
            raise BudgetExhausted()
        try:
            timeout = (CONTROL_TIMEOUT if left is None
                       else min(CONTROL_TIMEOUT, left))
            return call_api(TASKS_PATH, {"limit": BATCH}, timeout=timeout)
        except urllib.error.HTTPError as e:
            # 503만, 그것도 유효한 Retry-After가 있을 때만 다시 시도한다.
            # 400/401/403/409/429는 다시 보내도 결과가 같거나 더 나쁘다.
            wait = _retry_after_seconds(e) if e.code == 503 else None
            left = _remaining(run_deadline())
            # 대기까지 하고 나면 관측·제출을 할 수 없는 상황이면 기다리지 않는다.
            no_room = (left is not None
                       and left - wait <= RESULTS_TIMEOUT + 5.0) if wait else True
            if (wait is None or attempt >= TASKS_RETRY_MAX
                    or spent + wait > TASKS_RETRY_BUDGET or no_room or _stop):
                log(event="krp_tasks_retry", attempt=attempt + 1,
                    max_attempts=TASKS_RETRY_MAX + 1, final_result="giveup")
                raise
            log(event="krp_tasks_retry", attempt=attempt + 1,
                max_attempts=TASKS_RETRY_MAX + 1, wait_seconds=wait,
                final_result="retry")
            time.sleep(wait)
            spent += wait
    raise RuntimeError("unreachable")


def run_once():
    # **회차 시작점이 유일한 기준이다.** 이후 모든 단계가 이 하나의 monotonic
    # deadline에서 남은 시간을 받아 쓰므로, 단계가 늘어도 상한이 흔들리지 않는다.
    _run_deadline[0] = time.monotonic() + RUN_BUDGET
    tasks = lease_tasks_with_retry().get("tasks") or []
    if not tasks:
        log(event="krp_idle", count=0)
        return 0
    log(event="krp_tasks", count=len(tasks))

    results = []
    interval = 1.0 / RATE if RATE > 0 else 0.0
    # **wall clock이 아니라 단조 시계다.** NTP 보정이나 시각 변경이 예산을
    # 늘리거나 줄이면 상한 계산이 그대로 거짓말이 된다.
    # 관측 예산은 **남은 시간에서 제출 몫을 뺀 것**과 설정값 중 작은 쪽이다.
    # tasks 재시도로 시간을 썼다면 그만큼 관측이 줄어든다 — 합산 예산이었다면
    # 여기서 이미 상한을 넘었을 상황이다.
    left_now = _remaining(run_deadline())
    observe_left = min(OBSERVE_BUDGET, left_now - RESULTS_TIMEOUT)
    if observe_left <= 1.0:
        # 관측할 시간이 없다. 조용히 0건으로 끝내면 **상한 초과가 정상 종료로
        # 보인다** — 구조화된 명시적 실패로 끝낸다(lease는 만료 후 회수된다).
        log(event="krp_deadline_exhausted", stage="observe",
            remaining_seconds=round(max(0.0, left_now), 3))
        raise BudgetExhausted()
    deadline = time.monotonic() + observe_left
    for i, t in enumerate(tasks):
        if _stop:
            log(event="krp_stopped", done=len(results))
            break
        # 예산을 넘었으면 **관측을 멈추고 지금까지의 결과를 제출한다.**
        # 여기서 그냥 계속하면 상한에 걸려 제출 없이 죽고, 그 회차의 관측이
        # 통째로 버려진다. 로그에는 개수만 남긴다 — clipUid·taskId·leaseToken은
        # 남기지 않는다.
        if _remaining(deadline) <= 0:
            log(event="krp_budget_exhausted", done=len(results),
                remaining=len(tasks) - i)
            break
        if i and interval:
            # rate 간격도 남은 예산을 넘겨 자지 않는다.
            time.sleep(min(interval, max(0.0, _remaining(deadline))))
            if _remaining(deadline) <= 0:
                log(event="krp_budget_exhausted", done=len(results),
                    remaining=len(tasks) - i)
                break
        try:
            r = observe(t, deadline)
        except BudgetExhausted:
            # 진행 중이던 요청이 예산 경계에서 끊겼다. 이 클립은 결과에 넣지
            # 않는다 — lease가 열린 채 만료돼 다음 회차가 가져간다.
            log(event="krp_budget_exhausted", done=len(results),
                remaining=len(tasks) - i)
            break
        except RateLimited as e:
            # 다음 실행까지 지켜야 하므로 **먼저 저장**한다. 그 뒤에 제출하다가
            # 죽어도 제한 시각은 남는다.
            now = int(time.time())
            until = parse_retry_after(e.retry_after, now)
            saved = write_next_allowed_at(until)
            # 남은 task는 제출하지 않는다 — lease 만료 후 자연히 회수된다.
            log(event="krp_rate_limited", wait_seconds=until - now,
                state_saved=saved, done=len(results))
            break
        ms = r.pop("_ms", 0)
        log(event="krp_observed", clip_uid=r["clipUid"], state=r["viewState"],
            status=r["httpStatus"], ms=ms, attempts=r["attempts"])
        results.append(r)

    if not results:
        return 0
    # **결과 제출만 긴 제한을 쓴다.** 서버가 25건을 순차 반영하고 순위를 다시
    # 계산하는 동안 기다려야 하기 때문이다.
    left_submit = _remaining(run_deadline())
    if left_submit <= 1.0:
        # 제출할 시간이 없다. **부분 제출이나 성공 위장을 새로 만들지 않는다** —
        # lease가 만료되면 다음 회차가 같은 후보를 가져간다.
        log(event="krp_deadline_exhausted", stage="results",
            remaining_seconds=round(max(0.0, left_submit), 3))
        raise BudgetExhausted()
    out = call_api(RESULTS_PATH, {"results": results},
                   timeout=min(RESULTS_TIMEOUT, left_submit))
    log(event="krp_submitted", sent=len(results),
        stored=out.get("stored"), accepted=out.get("accepted"),
        rejected=len(out.get("rejected") or []))
    return out.get("stored") or 0


def main():
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    if not API_BASE or not SECRET:
        # 값을 찍지 않는다 — 어느 쪽이 비었는지만 알린다.
        log(event="krp_misconfigured", has_base=bool(API_BASE),
            has_secret=bool(SECRET))
        return 2
    now = int(time.time())
    until = read_next_allowed_at()
    if until > now:
        # 이전 회차가 429를 받았다. **치지직도 Railway도 부르지 않고** 끝낸다.
        # RuntimeMaxSec 안에서 그냥 잠들어 버티는 방식은 쓰지 않는다.
        log(event="krp_backoff_wait", wait_seconds=until - now)
        return 0
    try:
        run_once()
    except urllib.error.HTTPError as e:
        log(event="krp_api_error", status=e.code)
        return 1
    except BudgetExhausted:
        # 회차 상한에 걸렸다. systemd가 죽이기 전에 **스스로** 끝낸다.
        log(event="krp_failed", kind="BudgetExhausted")
        return 1
    except Exception as e:                               # noqa: BLE001
        log(event="krp_failed", kind=type(e).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
