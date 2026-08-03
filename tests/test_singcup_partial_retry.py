"""카드 부분 결손(partial)에 대한 bounded retry — S4.1.

고치려는 것은 하나다. 카드 API가 200을 주면서 `content.vod`만 빠뜨리는 회차가 있고
(실측 로그: `heart_ok=true, view_ok=false, reason=view:no_vod`), 저장 계약이
"못 읽은 필드는 보존"이라 그 클립의 `view_count`는 **삽입 초기값 0**으로 남는다.
다음 기회는 다음 사이클(70분+)이고, 그동안 0이 조회수 70% 가중 점수에 진짜 0처럼
들어간다.

여기서 검증하는 계약은 넷이다.

  A. **processed 불변식** — 재시도를 몇 번 하든 클립 하나는 딱 한 번 집계된다.
     success + partial + failed == processed 가 항상 성립한다.
  B. **호출 예산** — 한 논리 작업당 외부 호출 최대 3회(최초 1 + 재시도 2).
     429/5xx/타임아웃은 `_get_json`이 이미 재시도하므로 이 계층은 손대지 않는다
     (이중 중첩 금지).
  C. **field-wise 병합** — 한 번이라도 제대로 받은 필드는 뒤 시도가 비어 와도
     버리지 않는다. missing/malformed/음수를 진짜 0으로 바꾸지 않는다.
  D. **저장** — 병합된 최종 결과를 DB에 **한 번만** 쓴다. 저장이 잠금으로 실패해도
     외부를 다시 부르지 않는다.

외부 네트워크는 전부 MockTransport로 막는다.
"""
import asyncio
import os
import time

import httpx
import pytest
import singcup_clips as sc
import singcup_sweep as sw

import database

EV = sc.EVENT_ID


# ── 카드 응답 빌더 ──────────────────────────────────────────────────────────
def _card(*, likes=None, views=None, reactions=True, vod=True, emotion=True,
          interaction=True, content=True, no_count=False):
    """카드 한 장. 각 필드를 독립적으로 없앨 수 있어야 결손 시그니처를 재현한다."""
    inter: dict | None = None
    if interaction:
        inter = {}
        if emotion:
            inter["emotion"] = {"reactions": (
                [{"reactionType": "like", "count": likes}] if reactions else None)}
    c: dict | None = None
    if content:
        c = {"description": "#싱드컵", "title": "t"}
        if vod:
            # no_count=True 는 vod 그릇은 있는데 count 키가 없는 형태(view:no_count),
            # views=<이상한 값> 은 키는 있는데 값이 깨진 형태(view:invalid_count)다.
            c["vod"] = {} if no_count else {"count": views}
    card: dict = {}
    if c is not None:
        card["content"] = c
    if inter is not None:
        card["interaction"] = inter
    return {"card": card}


class Seq:
    """카드 API 응답을 호출 순서대로 돌려주는 스텁. 호출 수를 센다.

    마지막 항목은 소진 후 계속 재사용된다 — "언제까지나 같은 결손"을 표현한다.
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0
        self.card_calls = 0

    def __call__(self, request):
        url = str(request.url)
        self.calls += 1
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json={
                "code": 200, "content": {"channelId": "own0", "channelName": "n",
                                         "channelImageUrl": "", "followerCount": 1,
                                         "verifiedMark": False}})
        if "/categories/" in url:
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": [], "page": {}}})
        if "/clips/" in url and "/detail" in url:
            return httpx.Response(200, json={"code": 200, "content": {}})
        self.card_calls += 1
        i = min(self.card_calls - 1, len(self.responses) - 1)
        r = self.responses[i]
        return httpx.Response(r[0], json=r[1]) if isinstance(r, tuple) \
            else httpx.Response(200, json=r)


def _install(handler):
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return handler


def _item(uid="c0"):
    return {"clipUID": uid, "videoId": f"v-{uid}", "recId": "{}"}


# ── 시드 ────────────────────────────────────────────────────────────────────
async def _seed(n=1, *, hearts=0, views=0, last=0):
    db = await database.get_db()
    now = int(time.time())
    for i in range(n):
        uid = f"c{i}"
        await db.execute(
            "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id,"
            " video_id, rec_id, clip_title, thumbnail_image_url, description,"
            " created_at, heart_count, view_count, duration, adult, blind_type,"
            " metrics_ok, active, missing_scan_count, first_collected_at,"
            " last_collected_at, row_updated_at, last_metrics_at, last_attempt_at,"
            " last_heart_at, last_view_at)"
            " VALUES (?,?,?,?,'','t',?,'#싱드컵',?,?,?,60,0,'',1,1,0,?,?,?,?,?,?,?)",
            (uid, EV, "own0", f"v-{uid}", f"https://t/{uid}.jpg", now - 9999,
             hearts, views, now, now, now, last, last, last, last))
    await db.execute(
        "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
        " channel_image_url, follower_count, verified_mark,"
        " representative_clip_uid, tagged_clip_count, last_channel_updated_at,"
        " row_updated_at) VALUES (?,?,'n','',0,0,?,?,?,?)",
        ("own0", EV, "c0", n, now, now))
    await db.commit()


async def _row(uid="c0"):
    db = await database.get_db()
    return dict(await (await db.execute(
        "SELECT * FROM singcup_clips WHERE clip_uid=?", (uid,))).fetchone())


# ══ B. 호출 예산 ════════════════════════════════════════════════════════════
def test_complete_first_response_costs_one_call(db):
    """첫 응답이 온전하면 재시도하지 않는다 — 정상 경로의 비용은 그대로 1회다."""
    h = _install(Seq(_card(likes=7, views=11)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 1
    assert (res["heart_count"], res["view_count"]) == (7, 11)
    assert res["metrics_ok"] is True
    assert res["attempts"] == 1 and res["retried"] == 0


def test_persistent_partial_stops_at_three_calls(db):
    """끝까지 조회수를 못 받아도 외부 호출은 **정확히 3회**(최초 1 + 재시도 2)."""
    h = _install(Seq(_card(likes=7, vod=False)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 3
    assert res["attempts"] == 3 and res["retried"] == 2
    assert res["metrics_ok"] is False
    assert (res["heart_ok"], res["view_ok"]) == (True, False)


def test_fetch_failure_is_not_retried_by_this_layer(db):
    """5xx는 `_get_json`이 이미 재시도한다. 이 계층이 또 하면 3×3=9회가 된다.

    실패한 조회에 재시도를 겹치면 장애 중인 외부에 요청을 증폭해서 보낸다.
    """
    h = _install(Seq((500, {})))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert res is None
    assert h.card_calls == sc.MAX_RETRIES          # 3 — 9가 아니다


def test_case_d_container_absent_is_not_retried(db):
    """D-2. 하트·조회수가 함께 없고 **상위 블록이 통째로 없으면** 재시도하지 않는다.

    실측: 잘못된 videoId로 부르면 vod와 like가 같이 사라진다. 입력·대상이 잘못된
    쪽이므로 다시 불러도 같은 답이 온다.
    """
    h = _install(Seq(_card(interaction=False, content=False)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 1
    assert res is not None and res["metrics_ok"] is False
    assert res["missing_reason"] == "heart:no_interaction,view:no_content"


def test_container_absent_is_not_retried(db):
    """`content` 자체가 없는 응답은 일시적 결손으로 보지 않는다(구조 이상)."""
    h = _install(Seq(_card(likes=7, content=False)))
    db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 1


def test_retry_budget_caps_total_wait(db, monkeypatch):
    """대기 예산을 넘길 재시도는 하지 않는다 — 락을 쥔 채 무한정 늘어지지 않는다."""
    monkeypatch.setattr(sc, "PARTIAL_RETRY_BUDGET_SECONDS", 0.0)
    h = _install(Seq(_card(likes=7, vod=False)))
    t0 = time.monotonic()
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 1                       # 예산 0 → 재시도 없음
    assert time.monotonic() - t0 < 1.0
    assert res["attempts"] == 1


def test_every_attempt_passes_through_the_rate_limiter(db):
    """재시도도 전역 토큰 버킷을 통과한다 — 우회하면 속도 제한이 뚫린다."""
    _install(Seq(_card(likes=7, vod=False)))
    taken = {"n": 0}

    async def acquire():
        taken["n"] += 1

    sem = asyncio.Semaphore(1)
    db(sc.fetch_card_metrics(sc._get_client(), _item(), acquire=acquire, sem=sem))
    assert taken["n"] == 3                          # 시도마다 정확히 한 토큰


def test_backoff_does_not_hold_the_concurrency_slot(db, monkeypatch):
    """백오프 대기는 세마포어 **밖**이다. 안에서 자면 동시성 슬롯이 놀게 된다."""
    monkeypatch.setattr(sc, "_retry_delay", lambda *_a, **_k: 0.05)
    _install(Seq(_card(likes=7, vod=False)))
    sem = asyncio.Semaphore(1)
    held: list[int] = []

    async def watcher():
        for _ in range(12):
            await asyncio.sleep(0.02)
            held.append(0 if sem.locked() else 1)

    async def go():
        w = asyncio.create_task(watcher())
        await sc.fetch_card_metrics(sc._get_client(), _item(), sem=sem)
        await w

    db(go())
    assert any(held), "재시도 대기 내내 세마포어가 잡혀 있었다"


def test_retry_is_cancellable(db, monkeypatch):
    """취소는 그대로 전파된다 — 사이클 종료가 재시도 대기에 막히면 안 된다."""
    monkeypatch.setattr(sc, "_retry_delay", lambda *_a, **_k: 5.0)
    _install(Seq(_card(likes=7, vod=False)))

    async def go():
        task = asyncio.create_task(
            sc.fetch_card_metrics(sc._get_client(), _item()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    db(go())


# ══ retry 판정표 (A/B/C/D) ═════════════════════════════════════════════════
# 판정의 축은 '몇 개가 비었나'(XOR)가 아니라 **'왜 비었나'**(reason)다.
#   container_absent  상위 블록 부재            → 재시도 금지
#   leaf_missing      그릇은 왔는데 숫자만 없음 → 재시도
#   leaf_invalid      숫자가 깨짐(음수·NaN 등)  → 재시도 (단 절대 0으로 저장 안 함)

def _reason_of(**kw) -> str:
    c = _card(**kw)["card"]
    h, h_ok = sc.extract_heart(c)
    v, v_ok = sc.extract_view(c)
    return sc._missing_reason(c, h_ok, v_ok)


@pytest.mark.parametrize("kw,reason,retryable,case", [
    # A. 둘 다 정상 — 결손 없음
    (dict(likes=7, views=11), "", False, "A"),
    (dict(likes=0, views=0), "", False, "A 진짜 0"),
    # B. 조회수만 결손
    (dict(likes=7, vod=False), "view:no_vod", True, "B leaf_missing"),
    (dict(likes=7, no_count=True), "view:no_count", True, "B leaf_missing"),
    (dict(likes=7, views="abc"), "view:invalid_count", True, "B leaf_invalid"),
    (dict(likes=7, views=-5), "view:invalid_count", True, "B leaf_invalid"),
    (dict(likes=7, content=False), "view:no_content", False, "B container_absent"),
    # C. 하트만 결손
    (dict(reactions=False, views=11), "heart:no_reactions", True, "C leaf_missing"),
    (dict(likes="abc", views=11), "heart:invalid_count", True, "C leaf_invalid"),
    (dict(likes=-5, views=11), "heart:invalid_count", True, "C leaf_invalid"),
    (dict(emotion=False, views=11), "heart:no_emotion", False, "C container_absent"),
    (dict(interaction=False, views=11), "heart:no_interaction", False,
     "C container_absent"),
    # D. 둘 다 결손 — **사유로 갈린다**
    (dict(reactions=False, vod=False), "heart:no_reactions,view:no_vod", True,
     "D leaf 둘 → 재시도"),
    (dict(likes="x", views="y"), "heart:invalid_count,view:invalid_count", True,
     "D invalid 둘 → 재시도"),
    (dict(reactions=False, content=False), "heart:no_reactions,view:no_content",
     False, "D leaf+container 혼합 → 금지"),
    (dict(interaction=False, vod=False), "heart:no_interaction,view:no_vod",
     False, "D container 섞임 → 금지"),
    (dict(interaction=False, content=False),
     "heart:no_interaction,view:no_content", False, "D container 둘 → 금지"),
])
def test_retry_decision_table(kw, reason, retryable, case):
    """A/B/C/D 판정표를 사유 문자열까지 포함해 고정한다."""
    assert _reason_of(**kw) == reason, case
    card = {"metrics_ok": reason == "", "missing_reason": reason}
    assert sc.is_retryable_metrics_partial(card) is retryable, case


def test_case_d_both_leaf_missing_is_retried(db):
    """D-1. 둘 다 없어도 **둘 다 leaf**면 재시도한다 — XOR였다면 놓쳤을 경우."""
    h = _install(Seq(_card(reactions=False, vod=False), _card(likes=9, views=12)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 2
    assert (res["heart_count"], res["view_count"]) == (9, 12)
    assert res["metrics_ok"] is True


def test_case_d_both_invalid_is_retried_and_never_stored_as_zero(db):
    """D. 두 필드가 malformed·음수여도 재시도하고, **끝내 0으로 저장하지 않는다.**"""
    db(_seed(1, hearts=5, views=7))
    _install(Seq(_card(likes=-1, views="nope")))
    res = db(sw.run_cycle())
    assert res["processed"] == 1
    assert (res["success"], res["partial"], res["failed"]) == (0, 0, 1)
    r = db(_row())
    assert (r["heart_count"], r["view_count"]) == (5, 7)     # 기존 값 보존
    assert r["last_heart_at"] == 0 and r["last_view_at"] == 0


def test_fetch_failure_is_never_retryable_regardless_of_reason():
    """조회 실패(card None)는 사유와 무관하게 재시도 대상이 아니다."""
    assert sc.is_retryable_metrics_partial(None) is False


def test_merge_drops_reasons_for_fields_already_obtained():
    """이미 확보한 필드의 사유는 병합 결과에서 빠진다 — 판정 오염 방지.

    하트를 이미 받아 둔 상태에서 마지막 시도가 `heart:no_interaction`을 줬다고
    재시도를 포기하면, 실제로는 조회수만 남았고 그 사유는 leaf인데도 멈춰 버린다.
    """
    prev = {"heart_count": 5, "view_count": 0, "heart_ok": True, "view_ok": False,
            "metrics_ok": False, "missing_reason": "view:no_vod"}
    new = {"heart_count": 0, "view_count": 0, "heart_ok": False, "view_ok": False,
           "metrics_ok": False,
           "missing_reason": "heart:no_interaction,view:no_vod"}
    merged = sc._merge_card(prev, new)
    assert merged["heart_ok"] is True and merged["heart_count"] == 5
    assert merged["missing_reason"] == "view:no_vod"        # heart 사유는 빠졌다
    assert sc.is_retryable_metrics_partial(merged) is True


# ══ valid_count 입력 계약 ═══════════════════════════════════════════════════
@pytest.mark.parametrize("raw,expected,why", [
    (None, None, "None"),
    (True, None, "bool은 int의 하위 타입 — 1로 새면 안 된다"),
    (False, None, "bool False가 진짜 0으로 새면 안 된다"),
    (-1, None, "음수 거부"),
    (-99999, None, "음수 거부"),
    (0, 0, "진짜 0은 유효"),
    (1, 1, "양수"),
    (1828, 1828, "양수"),
    ("345", 345, "정수형 문자열 허용(기존 동작 유지)"),
    ("0", 0, "정수형 문자열 0"),
    ("-5", None, "문자열 음수도 거부"),
    (12.0, 12, "정수와 같은 실수는 허용"),
    (12.5, None, "진짜 소수는 스키마 이상으로 본다"),
    ("12.5", None, "문자열 소수도 동일"),
    (float("nan"), None, "NaN"),
    (float("inf"), None, "+Infinity"),
    (float("-inf"), None, "-Infinity"),
    ("nan", None, "문자열 NaN"),
    ("inf", None, "문자열 Infinity"),
    ("", None, "빈 문자열"),
    ("   ", None, "공백"),
    ("abc", None, "문자열"),
    ("1.2.3", None, "파싱 불가"),
    ([], None, "list"),
    ([1], None, "list"),
    ({}, None, "dict"),
    ({"count": 1}, None, "dict"),
    (2 ** 53, 2 ** 53, "상한 경계는 허용"),
    (2 ** 53 + 1, None, "상한 초과 거부"),
    (10 ** 30, None, "과도하게 큰 정수 거부"),
])
def test_valid_count_contract(raw, expected, why):
    assert sc.valid_count(raw) == expected, why


def test_valid_count_never_turns_invalid_into_zero():
    """계약의 핵심 한 줄 — invalid가 0이 되면 unknown과 진짜 0의 구분이 무너진다."""
    for bad in (None, True, False, -1, "abc", "", [], {}, float("nan"),
                float("inf"), 12.5, 2 ** 53 + 1):
        assert sc.valid_count(bad) is None, bad    # 0이 아니라 None이어야 한다


# ══ B-2. 공통 transport 예산 (상한 3회) ════════════════════════════════════
# **HTTP 내부 재시도와 partial 재시도가 같은 예산을 나눠 쓴다.**
# 각자 상한을 가지면 곱해진다 — 이전 구현은 500 → 500 → 200(partial) 뒤에 partial
# 재시도가 2회 더 붙어 한 클립이 **5회**를 썼다. 토큰 버킷을 통과하더라도 한 클립이
# 토큰 5개를 먹으면 장애와 partial이 겹친 순간 스윕 전체가 밀린다.
#
# 불변식: actual_transport_calls <= CARD_TRANSPORT_BUDGET (=3)
#         그리고 token acquire 수 == 실제 transport 호출 수

def _run_budget(db, *responses, retry_after=None):
    """(핸들러, 결과, 토큰획득수). 실제 transport 호출과 토큰 수를 함께 잰다."""
    class H(Seq):
        def __call__(self, request):
            r = super().__call__(request)
            if retry_after is not None and r.status_code == 429:
                r.headers["Retry-After"] = str(retry_after)
            return r

    h = _install(H(*responses))
    taken = {"n": 0}

    async def acquire():
        taken["n"] += 1

    res = db(sc.fetch_card_metrics(sc._get_client(), _item(),
                                   acquire=acquire, sem=asyncio.Semaphore(2)))
    return h, res, taken["n"]


def _terminal(res) -> str:
    return ("failed" if res is None
            else "success" if res["metrics_ok"] else "partial")


P = _card(likes=7, vod=False)                    # 200 partial (view:no_vod)
F = _card(likes=7, views=11)                     # 200 full


@pytest.mark.parametrize("case,responses,transport,result", [
    ("A 첫 200 full",                 [F],                        1, "success"),
    ("B partial×2 → full",            [P, P, F],                  3, "success"),
    ("C partial 지속",                [P],                        3, "partial"),
    ("D 500,500 → full",              [(500, {}), (500, {}), F],  3, "success"),
    ("E 500,500 → partial",           [(500, {}), (500, {}), P],  3, "partial"),
    ("F 500 → partial → full",        [(500, {}), P, F],          3, "success"),
    ("G 429 → partial → full",        [(429, {}), P, F],          3, "success"),
    ("H timeout 3회",                 [(504, {})],                3, "failed"),
    ("J container_absent",            [_card(interaction=False, content=False)],
                                                                  1, "partial"),
    ("K leaf_invalid 지속",           [_card(likes=7, views="abc")], 3, "partial"),
    ("400 즉시 확정",                 [(400, {})],                1, "failed"),
    ("404 영구",                      [(404, {})],                1, "failed"),
    ("403 차단",                      [(403, {})],                1, "failed"),
])
def test_transport_budget_table(db, case, responses, transport, result):
    """실제 transport 호출 수를 표대로 고정한다. **모두 3회 이하.**"""
    h, res, tokens = _run_budget(db, *responses)
    assert h.card_calls == transport, f"{case}: {h.card_calls} != {transport}"
    assert h.card_calls <= sc.CARD_TRANSPORT_BUDGET, f"{case}: 예산 초과"
    assert tokens == h.card_calls, f"{case}: 토큰 {tokens} != 호출 {h.card_calls}"
    assert _terminal(res) == result, case
    if res is not None:
        assert res["transport_calls"] == h.card_calls


def test_case_e_makes_no_fourth_call(db):
    """E. 500 → 500 → 200(partial)에서 **네 번째 호출이 0회**임을 못 박는다.

    이전 구현이 여기서 5회를 썼다. 예산이 소진됐으면 partial이 재시도 가능한
    사유여도 추가 호출을 하지 않고 terminal partial로 끝낸다.
    """
    h, res, tokens = _run_budget(db, (500, {}), (500, {}), P, F)
    assert h.card_calls == 3, f"4번째 호출이 나갔다: {h.card_calls}"
    assert tokens == 3
    assert _terminal(res) == "partial"
    assert res["heart_count"] == 7            # 하트는 건졌다
    assert res["view_ok"] is False            # 조회수는 끝내 못 받음
    assert res["transport_calls"] == 3


def test_budget_exhaustion_consumes_no_token_or_semaphore(db):
    """예산이 0이면 토큰도 세마포어도 잡지 않는다(호출 수와 토큰 수가 정확히 일치)."""
    h = _install(Seq((500, {}), (500, {}), P))
    taken = {"n": 0}
    sem = asyncio.Semaphore(1)

    async def acquire():
        taken["n"] += 1

    db(sc.fetch_card_metrics(sc._get_client(), _item(), acquire=acquire, sem=sem))
    assert h.card_calls == 3 and taken["n"] == 3
    assert not sem.locked(), "세마포어가 잡힌 채 남았다"


def test_partial_retry_max_zero_disables_extra_calls(db):
    """`PARTIAL_RETRY_MAX=0`이면 partial 추가 재시도가 없다(예산이 남아도)."""
    h, _res, _tokens = _run_budget(db, P)
    assert h.card_calls == 3                  # 기본값 2일 때는 3회
    h2 = _install(Seq(P))
    res2 = db(sc.fetch_card_metrics(sc._get_client(), _item(), max_retries=0))
    assert h2.card_calls == 1                 # 0이면 1회로 끝
    assert _terminal(res2) == "partial"


def test_http_retry_reduces_available_partial_retries(db):
    """HTTP 내부 재시도가 예산을 먼저 쓰면 partial 재시도 가능 횟수가 줄어든다."""
    # 500(1회) → partial(2회) → 남은 예산 1 → partial 재시도 1회 → 다시 partial로 소진
    h, res, tokens = _run_budget(db, (500, {}), P, P, F)
    assert h.card_calls == 3
    assert tokens == 3
    assert _terminal(res) == "partial"        # 예산이 먼저 소진돼 full까지 못 감


def test_429_with_retry_after_shares_the_same_budget(db):
    """429 + Retry-After도 같은 예산에서 차감된다."""
    h, res, tokens = _run_budget(db, (429, {}), retry_after=0)
    assert h.card_calls == 3 and tokens == 3
    assert res is None
    assert sc._api_counter["http_429"] >= 1


def test_cancellation_makes_no_further_calls(db, monkeypatch):
    """취소 이후 추가 transport 호출 0회."""
    monkeypatch.setattr(sc, "_retry_delay", lambda *_a, **_k: 5.0)
    h = _install(Seq(P))

    async def go():
        task = asyncio.create_task(sc.fetch_card_metrics(sc._get_client(), _item()))
        await asyncio.sleep(0.05)
        before = h.card_calls
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        assert h.card_calls == before, "취소 후에도 호출이 나갔다"

    db(go())


def test_leaf_invalid_persists_without_storing_zero(db):
    """K. leaf_invalid가 끝까지 지속돼도 실제 0을 저장하지 않는다."""
    db(_seed(1, hearts=5, views=7))
    _install(Seq(_card(likes=7, views="abc")))
    res = db(sw.run_cycle())
    assert res["processed"] == 1
    _assert_invariant(res)
    r = db(_row())
    assert r["view_count"] == 7 and r["last_view_at"] == 0   # 보존
    assert r["heart_count"] == 7                             # 하트만 갱신


def test_total_wait_budget_is_bounded(db, monkeypatch):
    """총 대기가 예산을 넘지 않는다(실제 초를 재서 확인)."""
    monkeypatch.setattr(sc, "_retry_delay", lambda *_a, **_k: 0.15)
    monkeypatch.setattr(sc, "PARTIAL_RETRY_BUDGET_SECONDS", 10.0)
    _install(Seq(P))
    t0 = time.monotonic()
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    elapsed = time.monotonic() - t0
    assert res["attempts"] == 3
    assert elapsed < 10.0                       # 예산 안
    assert elapsed >= 0.30                      # 두 번의 대기가 실제로 있었다


# ══ 환경변수 계약 ══════════════════════════════════════════════════════════
@pytest.mark.parametrize("raw,expected,why", [
    (None, 2, "미설정 → 기본값"),
    ("", 2, "빈 문자열 → 기본값"),
    ("   ", 2, "공백 → 기본값"),
    ("0", 0, "0 = 비활성"),
    ("1", 1, "1"),
    ("2", 2, "2"),
    ("-1", 0, "음수 → 하한 clamp"),
    ("-999", 0, "음수 → 하한 clamp"),
    ("3", 2, "과대값 → 상한 clamp"),
    ("99999", 2, "과대값 → 상한 clamp"),
    ("abc", 2, "숫자 아님 → 기본값"),
    ("nan", 2, "NaN → 기본값"),
    ("inf", 2, "Infinity → 기본값"),
    ("-inf", 2, "-Infinity → 기본값"),
    ("1.7", 1, "소수 → 절삭"),
])
def test_partial_retry_max_env_contract(monkeypatch, raw, expected, why):
    """잘못된 값에 기동을 실패시키지 않는다 — 기본값 또는 clamp."""
    if raw is None:
        monkeypatch.delenv("SINGCUP_PARTIAL_RETRY_MAX", raising=False)
    else:
        monkeypatch.setenv("SINGCUP_PARTIAL_RETRY_MAX", raw)
    assert sc._env_int("SINGCUP_PARTIAL_RETRY_MAX", 2, 0, 2) == expected, why


def test_transport_budget_env_is_clamped(monkeypatch):
    """공통 예산도 같은 계약으로 clamp된다(상한 3을 넘길 수 없다)."""
    for raw, exp in (("5", 3), ("0", 1), ("abc", 3), ("", 3), ("2", 2)):
        monkeypatch.setenv("SINGCUP_CARD_TRANSPORT_BUDGET", raw)
        assert sc._env_int("SINGCUP_CARD_TRANSPORT_BUDGET", 3, 1, 3) == exp, raw


def test_env_warning_has_no_secrets(monkeypatch, capsys):
    """잘못된 값 경고에 비밀정보가 섞이지 않는다(이름과 값만)."""
    monkeypatch.setenv("SINGCUP_PARTIAL_RETRY_MAX", "abc")
    sc._env_int("SINGCUP_PARTIAL_RETRY_MAX", 2, 0, 2)
    out = capsys.readouterr().out
    assert "env_invalid" in out
    for banned in ("SECRET", "TOKEN", "Bearer", "@"):
        assert banned not in out


# ══ C. field-wise 병합 ══════════════════════════════════════════════════════
def test_heart_first_then_view_merges_into_full_success(db):
    """attempt1 하트만 → attempt2 조회수만 → 최종은 둘 다 있는 full success."""
    h = _install(Seq(_card(likes=135, vod=False),
                     _card(reactions=False, views=1794)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 2                        # 성공했으니 3회까지 가지 않는다
    assert (res["heart_count"], res["view_count"]) == (135, 1794)
    assert res["metrics_ok"] is True
    assert res["attempts"] == 2


def test_view_first_then_heart_merges(db):
    """반대 방향도 같다 — 결손 필드가 어느 쪽이든 대칭으로 동작한다."""
    h = _install(Seq(_card(reactions=False, views=1794),
                     _card(likes=135, vod=False)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 2
    assert (res["heart_count"], res["view_count"]) == (135, 1794)
    assert res["metrics_ok"] is True


def test_later_missing_never_erases_an_already_valid_field(db):
    """뒤 시도가 비어 왔다고 앞에서 제대로 받은 값을 버리지 않는다."""
    h = _install(Seq(_card(likes=135, vod=False),
                     _card(interaction=False, content=False),
                     _card(interaction=False, content=False)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 2                # 2번째가 재시도 불가 시그니처 → 중단
    assert res["heart_ok"] is True and res["heart_count"] == 135


def test_later_valid_value_wins(db):
    """두 시도 모두 유효하면 **최신** 값을 쓴다(그 사이 값이 올랐을 수 있다)."""
    h = _install(Seq(_card(likes=135, vod=False), _card(likes=137, views=1828)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 2
    assert (res["heart_count"], res["view_count"]) == (137, 1828)


def test_fetch_failure_after_partial_keeps_the_partial(db):
    """재시도가 통째로 실패해도 앞서 받은 하트는 살아남는다(None으로 무너지지 않는다).

    호출 수는 **공유 예산 3회**를 넘지 않는다: partial 1회 + 실패 재시도가 남은
    예산 2회를 쓰고 소진.
    """
    h = _install(Seq(_card(likes=135, vod=False), (500, {})))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert res is not None
    assert res["heart_ok"] is True and res["heart_count"] == 135
    assert res["view_ok"] is False
    assert h.card_calls == sc.CARD_TRANSPORT_BUDGET      # 3 — 1+3=4가 아니다
    assert res["transport_calls"] == 3


@pytest.mark.parametrize("raw", ["abc", None, {}, [], True, "", "1.2.3"])
def test_malformed_view_count_is_not_a_real_zero(raw):
    """malformed는 '못 읽음'이다. 0으로 정규화하면 진짜 0과 구분이 사라진다."""
    assert sc.extract_view(_card(views=raw, likes=1)["card"]) == (0, False)


def test_negative_view_count_is_rejected():
    """음수는 거부한다 — 저장되면 조회수 70% 가중 점수를 오염시킨다."""
    assert sc.extract_view(_card(views=-5, likes=1)["card"]) == (0, False)
    assert sc.extract_heart(_card(likes=-5, views=1)["card"]) == (0, False)


def test_explicit_zero_is_a_real_zero():
    """필드가 있고 값이 0이면 그건 진짜 0이다(결손이 아니다)."""
    assert sc.extract_view(_card(views=0, likes=0)["card"]) == (0, True)
    assert sc.extract_heart(_card(views=0, likes=0)["card"]) == (0, True)


def test_string_digits_still_parse():
    """기존 동작 유지 — 숫자 문자열은 그대로 받는다."""
    assert sc.extract_view(_card(views="345", likes="12")["card"]) == (345, True)


def test_actual_zero_is_not_retried(db):
    """진짜 0에 재시도를 걸면 인기 없는 클립마다 외부 호출이 3배가 된다."""
    h = _install(Seq(_card(likes=0, views=0)))
    res = db(sc.fetch_card_metrics(sc._get_client(), _item()))
    assert h.card_calls == 1 and res["metrics_ok"] is True


# ══ §5. unknown vs 진짜 0 ═══════════════════════════════════════════════════
@pytest.mark.parametrize("last_view_at,view_count,expected", [
    (0, 0, "unknown"),              # 한 번도 정상 수신 못 함
    (1000, 5, "observed"),          # 정상 수신, 값 > 0
    (1000, 0, "observed_zero"),     # 정상 수신, **진짜 0**
    (0, 5000, "observed_legacy"),   # 컬럼 도입 이전에 받은 값
])
def test_view_state_derivation(last_view_at, view_count, expected):
    """신규 스키마 없이 기존 두 컬럼에서 파생한다.

    legacy 분기가 핵심이다. `last_view_at`은 나중에 추가된 컬럼이라 이전 행은 전부
    0인데, `view_count > 0`은 **오직 `view_ok=true`일 때만** 쓰이므로 값이 0보다
    크면 과거 어느 시점에 반드시 정상 수신된 것이다.
    """
    assert sc.view_state({"last_view_at": last_view_at,
                          "view_count": view_count}) == expected


def test_last_view_at_has_exactly_one_writer():
    """`last_view_at`을 쓰는 곳이 늘어나면 위 판정식이 조용히 거짓이 된다.

    unknown 계약 전체가 "이 컬럼은 조회수를 정상 수신했을 때만 움직인다"에 걸려
    있으므로, writer 수를 테스트로 고정한다.
    """
    import pathlib
    import re
    src = pathlib.Path(sc.__file__).read_text(encoding="utf-8")
    writers = re.findall(r'"last_view_at=\?"|last_view_at\s*=\s*[^?=]', src)
    assert len(writers) == 1, f"last_view_at writer가 {len(writers)}곳이다"


def test_partial_never_touches_last_view_at(db):
    """조회수를 못 읽은 회차는 `last_view_at`을 건드리지 않는다 → unknown 유지."""
    db(_seed())
    db(sc._apply_metrics("c0", 137, 0, True, False, 12345))
    db(_commit())
    r = db(_row())
    assert r["last_view_at"] == 0 and r["view_count"] == 0
    assert r["last_heart_at"] == 12345 and r["heart_count"] == 137
    assert sc.view_state(r) == "unknown"
    assert r["last_attempt_at"] == 12345          # 시도는 기록된다
    assert r["last_metrics_at"] == 0              # 둘 다 정상일 때만


async def _commit():
    db = await database.get_db()
    await db.commit()


def test_real_zero_records_last_view_at(db):
    """진짜 0을 받으면 시각이 남아 observed_zero가 된다."""
    db(_seed())
    db(sc._apply_metrics("c0", 3, 0, True, True, 12345))
    db(_commit())
    r = db(_row())
    assert r["last_view_at"] == 12345 and r["view_count"] == 0
    assert sc.view_state(r) == "observed_zero"


# ══ A. run_cycle / processed 불변식 ═════════════════════════════════════════
def _assert_invariant(res):
    assert res["success"] + res["partial"] + res["failed"] == res["processed"], res


def test_retry_success_counts_the_clip_exactly_once(db):
    """partial → 재시도 성공이어도 processed는 1이다.

    초기 partial을 먼저 세고 나중에 되돌리는 구조였다면 여기서 2가 나온다.
    """
    db(_seed(1))
    _install(Seq(_card(likes=135, vod=False), _card(likes=135, views=1794)))
    res = db(sw.run_cycle())
    assert res["processed"] == 1
    assert (res["success"], res["partial"], res["failed"]) == (1, 0, 0)
    _assert_invariant(res)
    r = db(_row())
    assert (r["heart_count"], r["view_count"]) == (135, 1794)
    assert r["metrics_ok"] == 1 and sc.view_state(r) == "observed"


def test_exhausted_retry_counts_one_partial(db):
    """재시도를 소진해도 processed 1 / partial 1 — 3회 시도가 3건이 되지 않는다."""
    db(_seed(1))
    _install(Seq(_card(likes=135, vod=False)))
    res = db(sw.run_cycle())
    assert res["processed"] == 1
    assert (res["success"], res["partial"], res["failed"]) == (0, 1, 0)
    _assert_invariant(res)
    r = db(_row())
    assert r["heart_count"] == 135                  # 하트는 갱신됐다
    assert sc.view_state(r) == "unknown"            # 조회수는 여전히 '모름'


def test_invariant_holds_across_mixed_outcomes(db):
    """성공·부분·실패가 섞여도 불변식이 성립한다."""
    db(_seed(6))
    _install(Seq(_card(likes=1, vod=False), _card(likes=1, views=2),
                 _card(likes=1, views=2)))
    res = db(sw.run_cycle())
    assert res["processed"] == 6
    _assert_invariant(res)


def test_ranking_recompute_still_runs_after_retries(db):
    """재시도가 끼어도 뒤 단계(순위 재계산·스냅샷)가 정상 실행된다.

    스냅샷에 **병합된** 조회수가 실려야 한다 — 재시도로 얻은 값이 점수까지
    도달하지 못하면 이번 수정은 절반만 된 것이다(점수는 조회수 70% 가중).
    """
    db(_seed(1))
    _install(Seq(_card(likes=135, vod=False), _card(likes=135, views=1794)))
    db(sw.run_cycle())
    snap = db(_snapshot())
    assert snap is not None, "순위 재계산이 스냅샷을 남기지 않았다"
    assert int(snap["view_count"]) == 1794
    assert int(snap["rank"]) >= 1 and float(snap["score"]) > 0


async def _snapshot():
    db = await database.get_db()
    return await (await db.execute(
        "SELECT rank, score, view_count FROM singcup_snapshots "
        "WHERE owner_channel_id='own0' ORDER BY collected_at DESC LIMIT 1"
    )).fetchone()


def test_cancelling_the_cycle_propagates(db):
    """사이클 취소가 재시도 대기에 삼켜지지 않는다."""
    db(_seed(3))
    _install(Seq(_card(likes=1, vod=False)))

    async def go():
        task = asyncio.create_task(sw.run_cycle())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    db(go())


def test_clip_lock_ttl_covers_the_retry_budget():
    """락 TTL은 상수에서 유도된다 — 재시도를 더했으면 TTL도 따라 올라야 한다.

    유도를 갱신하지 않으면 재시도 중에 락이 조용히 만료돼 자동 스윕과 수동 갱신이
    같은 클립을 동시에 만진다.
    """
    assert sc.CLIP_LOCK_TTL >= sc._worst_clip_seconds() * 1.2
    assert sc._worst_clip_seconds() >= sc.PARTIAL_RETRY_BUDGET_SECONDS


def test_worst_clip_seconds_formula_is_explicit():
    """최악 시간 공식을 항목별로 재현해 고정한다 — **호출 수를 중복 합산하지 않는다.**

        per_call = 토큰 대기 + REQUEST_TIMEOUT
        카드     = CARD_TRANSPORT_BUDGET × per_call + HTTP 백오프 + partial 대기 예산
        상세     = MAX_RETRIES × per_call + HTTP 백오프      (별도 논리 작업)
        총합     = 카드 + 상세 + DB 쓰기 예산

    예전 공식은 `fetches × http_once`였고 `http_once`가 `MAX_RETRIES × timeout`을
    품고 있어 카드 호출을 12회분으로 셌다(실제 상한은 3회).
    """
    from database.db import BUSY_TIMEOUT_MS
    http_backoff = sum(min(sc.BACKOFF_MAX, sc.BACKOFF_BASE * (2 ** a)) + sc.BACKOFF_BASE
                       for a in range(max(0, sc.MAX_RETRIES - 1)))
    token_wait = 1.0 / max(0.01, float(os.getenv("SINGCUP_SWEEP_MIN_RATE", "0.2")))
    per_call = token_wait + sc.REQUEST_TIMEOUT
    db_attempts = max(1, int(os.getenv("SINGCUP_DB_RETRY_ATTEMPTS", "4")))
    db_base = float(os.getenv("SINGCUP_DB_RETRY_BASE_SECONDS", "0.05"))
    db_wait = (db_attempts * (BUSY_TIMEOUT_MS / 1000.0)
               + sum(db_base * (2 ** i) * 2 for i in range(db_attempts - 1)))
    card = (sc.CARD_TRANSPORT_BUDGET * per_call + http_backoff
            + sc.PARTIAL_RETRY_BUDGET_SECONDS)
    detail = sc.MAX_RETRIES * per_call + http_backoff
    assert abs(sc._worst_clip_seconds() - (card + detail + db_wait)) < 1e-6


def test_worst_clip_seconds_counts_card_calls_only_once():
    """카드 호출 몫이 **공유 예산(3)** 만큼만 반영된다 — 곱셈 중복이 없다."""
    token_wait = 1.0 / max(0.01, float(os.getenv("SINGCUP_SWEEP_MIN_RATE", "0.2")))
    per_call = token_wait + sc.REQUEST_TIMEOUT
    # 카드+상세 호출 시간의 합이 (예산 + MAX_RETRIES) × per_call 을 넘지 않아야 한다
    ceiling = (sc.CARD_TRANSPORT_BUDGET + sc.MAX_RETRIES) * per_call
    http_and_wait = sc._worst_clip_seconds()
    assert http_and_wait > ceiling            # 백오프·DB 몫이 더해져 있고
    assert http_and_wait < ceiling * 3        # 곱셈으로 부풀지는 않았다


def test_ttl_is_not_excessive():
    """과도한 TTL도 문제다 — 멈춘 클립이 필요 이상으로 오래 락을 쥔다.

    안전계수는 1.5, 상한은 유도값의 2배로 묶는다.
    """
    worst = sc._worst_clip_seconds()
    assert sc.CLIP_LOCK_TTL <= worst * 2.0
    assert sc.CLIP_LOCK_TTL < 600, "락 TTL이 10분을 넘으면 운영에서 너무 길다"


def test_ttl_shrinks_when_retry_is_disabled(monkeypatch):
    """`PARTIAL_RETRY_MAX=0`으로 끄면 유도값이 원래대로 줄어든다(비상 스위치 정합)."""
    before = sc._worst_clip_seconds()
    monkeypatch.setattr(sc, "PARTIAL_RETRY_MAX", 0)
    monkeypatch.setattr(sc, "PARTIAL_RETRY_BUDGET_SECONDS", 0.0)
    after = sc._worst_clip_seconds()
    assert after < before


def test_lock_is_released_on_exception_and_cancellation(db):
    """예외·취소 경로에서도 락이 반드시 풀린다 — 안 풀리면 그 클립이 영구 차단된다."""
    db(_seed(1))

    async def boom(*a, **k):
        raise RuntimeError("의도된 실패")

    orig = sc.fetch_card_metrics
    sc.fetch_card_metrics = boom
    try:
        res = db(sw.run_cycle())
    finally:
        sc.fetch_card_metrics = orig
    assert res["processed"] == 1 and res["failed"] == 1
    _assert_invariant(res)
    token = db(sc.acquire_clip_lock("c0", wait=0))
    assert token is not None, "실패 후에도 락이 남아 있다"
    db(sc.release_clip_lock("c0", token))


def test_next_cycle_is_not_permanently_blocked_after_failure(db):
    """한 사이클이 실패해도 다음 사이클이 같은 클립을 정상 처리한다."""
    db(_seed(1))
    _install(Seq((500, {})))
    first = db(sw.run_cycle())
    assert first["failed"] == 1
    _install(Seq(_card(likes=9, views=12)))
    # 연속 호출은 같은 초라 scheduled_at UNIQUE에 걸린다(회차 소유권 계약). 다음
    # 회차를 명시적으로 연다 — 여기서 보려는 건 '클립이 다시 처리되는가'다.
    later = int(time.time()) + 10
    second = db(sw.run_sweep(later, cutoff=later))
    assert second["processed"] == 1 and second["success"] == 1
    r = db(_row())
    assert (r["heart_count"], r["view_count"]) == (9, 12)


def test_manual_refresh_and_sweep_do_not_overlap_on_one_clip(db):
    """수동 갱신이 락을 쥔 동안 스윕은 그 클립을 건드리지 않는다(single-flight)."""
    db(_seed(1))
    _install(Seq(_card(likes=9, views=12)))
    token = db(sc.acquire_clip_lock("c0", wait=0))
    assert token is not None
    try:
        res = db(sw.run_cycle())
        assert res["processed"] == 1
        assert res["skipped"] == 1                  # 락 충돌로 건너뜀
        assert res["failed"] == 1                   # skipped ⊆ failed
        _assert_invariant(res)
        r = db(_row())
        assert r["last_attempt_at"] == 0            # DB를 건드리지 않았다
    finally:
        db(sc.release_clip_lock("c0", token))


# ══ D. 저장 ═════════════════════════════════════════════════════════════════
def test_metrics_are_written_once_per_clip(db, monkeypatch):
    """재시도가 3회여도 DB 쓰기는 1회다 — 중간 결과를 저장하지 않는다."""
    db(_seed(1))
    calls = []
    real = sc._apply_metrics

    async def spy(uid, *a, **kw):
        calls.append((uid, a[:4]))
        return await real(uid, *a, **kw)

    monkeypatch.setattr(sc, "_apply_metrics", spy)
    _install(Seq(_card(likes=135, vod=False), _card(likes=135, views=1794)))
    db(sw.run_cycle())
    assert len(calls) == 1
    assert calls[0][1] == (135, 1794, True, True)    # 병합된 최종 결과 한 번


def test_db_lock_does_not_trigger_another_external_call(db, monkeypatch):
    """저장만 실패한 경우 외부를 다시 부르지 않는다 — 메모리의 같은 결과를 쓴다."""
    db(_seed(1))
    h = _install(Seq(_card(likes=135, vod=False), _card(likes=135, views=1794)))

    async def always_locked(fn, *, what, attempts=4):
        return False

    monkeypatch.setattr(sw, "db_write", always_locked)
    res = db(sw.run_cycle())
    assert h.card_calls == 2                         # 재호출 없음
    assert res["processed"] == 1
    _assert_invariant(res)


def test_retry_success_leaves_no_row_in_the_discovery_queue(db):
    """지표 재시도는 신규 탐색 큐(`singcup_clip_retry`)를 오염시키지 않는다.

    그 큐의 소비자는 `_scan_batch`(등록 경로)다. 지표 목적 행을 넣으면 의미가 어긋난다.
    """
    db(_seed(1))
    _install(Seq(_card(likes=135, vod=False), _card(likes=135, views=1794)))
    db(sw.run_cycle())
    assert db(_retry_rows()) == 0


def test_exhausted_partial_also_leaves_the_queue_alone(db):
    """소진된 partial도 마찬가지다 — 스키마 변경 없는 계약의 핵심."""
    db(_seed(1))
    _install(Seq(_card(likes=135, vod=False)))
    db(sw.run_cycle())
    assert db(_retry_rows()) == 0


async def _retry_rows() -> int:
    db = await database.get_db()
    return (await (await db.execute(
        "SELECT COUNT(*) c FROM singcup_clip_retry")).fetchone())["c"]
