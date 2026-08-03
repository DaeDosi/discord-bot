"""OWNER 전용 싱드컵 클립 지표 단건 갱신 — Preview / Apply.

이 통로가 있는 이유는 하나다. 카드 API가 200을 주면서 조회수만 빠뜨리는 회차가
있고, 그때 저장 계약("못 읽은 필드는 보존")대로 값이 삽입 초기값 0으로 남는다.
자동 복구는 다음 사이클(70분+) 뒤라, 그동안 0이 조회수 70% 가중 점수에 진짜 0처럼
들어간다. 여기서 고정하는 계약은 다섯이다.

  ① **권한** — OWNER가 아니면 아무것도 못 한다.
  ② **입력 축소** — 임의 URL로 나가지 않는다(SSRF). 숫자 직접 입력은 없다.
  ③ **single-flight** — 자동 스윕과 **같은** 클립 락을 잡는다(중복 클릭 포함).
  ④ **대표 불변** — 지표 갱신이 대표 클립을 바꾸지 않는다.
  ⑤ **비노출** — 응답에 토큰·시크릿·내부 식별자가 새지 않는다.
"""
import time

import httpx
import pytest
import singcup_clips as sc
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database

OWNER = "111111111111111111"
EV = sc.EVENT_ID
UID = "Qn64362ayN"


def _card(*, likes=None, views=None, reactions=True, vod=True):
    inter = {"emotion": {"reactions": (
        [{"reactionType": "like", "count": likes}] if reactions else None)}}
    content = {"description": "#싱드컵", "title": "[싱드컵] 기다린 만큼, 더"}
    if vod:
        content["vod"] = {"count": views}
    return {"card": {"content": content, "interaction": inter}}


class Seq:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.card_calls = 0

    def __call__(self, request):
        url = str(request.url)
        if "/service/v1/channels/" in url:
            return httpx.Response(200, json={
                "code": 200, "content": {"channelId": "own0", "channelName": "김 재 우",
                                         "channelImageUrl": "", "followerCount": 1,
                                         "verifiedMark": False}})
        if "/categories/" in url:
            return httpx.Response(200, json={"code": 200,
                                             "content": {"data": [], "page": {}}})
        self.card_calls += 1
        i = min(self.card_calls - 1, len(self.responses) - 1)
        r = self.responses[i]
        return httpx.Response(r[0], json=r[1]) if isinstance(r, tuple) \
            else httpx.Response(200, json=r)


def _install(h):
    sc._client = httpx.AsyncClient(transport=httpx.MockTransport(h))
    return h


@pytest.fixture
def client(db, monkeypatch):
    import routers.admin_router as ar
    from deps import get_current_user

    monkeypatch.setattr(ar, "_OWNER_ID", OWNER)
    ar._metrics_hits.clear()
    app = FastAPI()
    app.include_router(ar.router)
    app.state._dep = get_current_user
    c = TestClient(app, raise_server_exceptions=True)
    c.app.dependency_overrides[get_current_user] = lambda: {"sub": OWNER}
    return c


def _anon(client):
    from deps import get_current_user
    client.app.dependency_overrides[get_current_user] = lambda: {"sub": "999"}
    return client


async def _seed(*, hearts=137, views=0, last_view_at=0, rep=UID):
    """진단에서 관측된 상태 그대로 — 하트는 최신인데 조회수만 0/unknown."""
    conn = await database.get_db()
    now = int(time.time())
    await conn.execute(
        "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
        " rec_id, clip_title, thumbnail_image_url, description, created_at,"
        " heart_count, view_count, duration, adult, blind_type, metrics_ok, active,"
        " deletion_state, missing_scan_count, first_collected_at, last_collected_at,"
        " row_updated_at, last_metrics_at, last_attempt_at, last_heart_at, last_view_at)"
        " VALUES (?,?,?,?,'{}',?,'','#싱드컵',?,?,?,60,0,'',0,1,'active',0,?,?,?,0,?,?,?)",
        (UID, EV, "own0", "vid-1", "[싱드컵] 기다린 만큼, 더", now - 9999,
         hearts, views, now, now, now, now, now, last_view_at))
    await conn.execute(
        "INSERT INTO singcup_streamers (channel_id, event_id, channel_name,"
        " channel_image_url, follower_count, verified_mark, representative_clip_uid,"
        " tagged_clip_count, last_channel_updated_at, row_updated_at)"
        " VALUES (?,?,'김 재 우','',0,0,?,1,?,?)", ("own0", EV, rep, now, now))
    await conn.commit()


async def _row():
    conn = await database.get_db()
    return dict(await (await conn.execute(
        "SELECT * FROM singcup_clips WHERE clip_uid=?", (UID,))).fetchone())


# ── ① 권한 ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["preview", "apply"])
def test_non_owner_is_rejected(client, db, path):
    db(_seed())
    _install(Seq(_card(likes=137, views=1828)))
    r = _anon(client).post(f"/api/admin/singcup/clips/metrics/{path}",
                           json={"clipInput": UID})
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["preview", "apply"])
def test_non_owner_causes_no_external_call(client, db, path):
    """권한 검사는 외부 호출 **앞**이다 — 아니면 인증 없이 외부를 두드릴 수 있다."""
    db(_seed())
    h = _install(Seq(_card(likes=137, views=1828)))
    _anon(client).post(f"/api/admin/singcup/clips/metrics/{path}",
                       json={"clipInput": UID})
    assert h.card_calls == 0


# ── ② 입력 축소 (SSRF) ─────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    "http://evil.example.com/clips/abc",            # https 아님
    "https://evil.example.com/clips/abc",           # 다른 호스트
    "https://chzzk.naver.com.evil.com/clips/abc",   # 접미사 위장
    "https://chzzk.naver.com:8080/clips/abc",       # 포트 지정
    "https://127.0.0.1/clips/abc",                  # 내부 주소
    "../../etc/passwd", "", "a" * 500,
])
def test_hostile_input_is_rejected_before_any_fetch(client, db, bad):
    db(_seed())
    h = _install(Seq(_card(likes=137, views=1828)))
    r = client.post("/api/admin/singcup/clips/metrics/preview",
                    json={"clipInput": bad})
    assert r.status_code == 400
    assert h.card_calls == 0


def test_canonical_clip_url_is_accepted(client, db):
    db(_seed())
    _install(Seq(_card(likes=137, views=1828)))
    r = client.post("/api/admin/singcup/clips/metrics/preview",
                    json={"clipInput": f"https://chzzk.naver.com/clips/{UID}"})
    assert r.status_code == 200 and r.json()["clipUid"] == UID


def test_unknown_clip_is_404(client, db):
    db(_seed())
    _install(Seq(_card(likes=1, views=1)))
    r = client.post("/api/admin/singcup/clips/metrics/preview",
                    json={"clipInput": "NotAClipUid1"})
    assert r.status_code == 404


def test_body_has_no_numeric_metric_input(client, db):
    """숫자를 직접 넣어 저장할 수 없다 — 값의 출처는 언제나 카드 API다."""
    db(_seed())
    _install(Seq(_card(likes=137, views=1828)))
    r = client.post("/api/admin/singcup/clips/metrics/apply",
                    json={"clipInput": UID, "viewCount": 999999,
                          "heartCount": 999999})
    assert r.status_code == 200
    assert db(_row())["view_count"] == 1828            # 999999가 아니다


# ── Preview ────────────────────────────────────────────────────────────────
def test_preview_writes_nothing(client, db):
    db(_seed())
    before = db(_row())
    _install(Seq(_card(likes=137, views=1828)))
    r = client.post("/api/admin/singcup/clips/metrics/preview",
                    json={"clipInput": UID})
    assert r.status_code == 200
    assert db(_row()) == before                        # 한 글자도 바뀌지 않았다


def test_preview_separates_unknown_from_real_zero(client, db):
    """진단에서 가장 아쉬웠던 값 — 조회수 0이 '모름'인지 '진짜 0'인지."""
    db(_seed(views=0, last_view_at=0))
    _install(Seq(_card(likes=137, views=1828)))
    body = client.post("/api/admin/singcup/clips/metrics/preview",
                       json={"clipInput": UID}).json()
    assert body["stored"]["viewState"] == "unknown"
    assert body["stored"]["heartState"] == "observed"
    assert body["pending"]["viewCount"] == 1828
    assert body["pending"]["viewWillChange"] is True


def test_preview_reports_attempts_and_which_attempt_gave_what(client, db):
    """부분 결손 → 재시도로 채워진 경로가 그대로 보여야 한다."""
    db(_seed())
    _install(Seq(_card(likes=137, vod=False), _card(likes=137, views=1828)))
    ext = client.post("/api/admin/singcup/clips/metrics/preview",
                      json={"clipInput": UID}).json()["external"]
    assert ext["attempts"] == 2 and ext["maxAttempts"] == 3
    assert ext["partial"] is False
    assert ext["attemptTrace"][0]["fieldsObserved"] == "heart"
    assert ext["attemptTrace"][0]["viewCount"] is None
    assert ext["attemptTrace"][1]["viewCount"] == 1828


def test_preview_call_budget_is_bounded(client, db):
    db(_seed())
    h = _install(Seq(_card(likes=137, vod=False)))
    ext = client.post("/api/admin/singcup/clips/metrics/preview",
                      json={"clipInput": UID}).json()["external"]
    assert h.card_calls == 3 and ext["attempts"] == 3
    assert ext["partial"] is True and ext["missingReason"] == "view:no_vod"


def test_preview_survives_external_failure(client, db):
    """외부가 죽어도 500이 아니라 '저장할 값 없음'으로 알려준다."""
    db(_seed())
    _install(Seq((500, {})))
    body = client.post("/api/admin/singcup/clips/metrics/preview",
                       json={"clipInput": UID}).json()
    assert body["external"]["ok"] is False
    assert body["pending"]["viewCount"] == 0           # 기존 값 보존
    assert body["note"]


# ── Apply ──────────────────────────────────────────────────────────────────
def test_apply_persists_and_clears_unknown(client, db):
    db(_seed(views=0, last_view_at=0))
    _install(Seq(_card(likes=137, views=1828)))
    body = client.post("/api/admin/singcup/clips/metrics/apply",
                       json={"clipInput": UID}).json()
    assert body["ok"] is True
    assert body["before"]["viewState"] == "unknown"
    assert body["after"]["viewState"] == "observed"
    r = db(_row())
    assert (r["heart_count"], r["view_count"]) == (137, 1828)
    assert r["last_view_at"] > 0 and r["metrics_ok"] == 1


def test_apply_does_a_fresh_fetch_not_a_stale_preview(client, db):
    """Preview 값을 들고 있다가 저장하지 않는다 — Apply는 자기 몫을 새로 조회한다."""
    db(_seed())
    h = _install(Seq(_card(likes=137, views=1828), _card(likes=140, views=1900)))
    client.post("/api/admin/singcup/clips/metrics/preview", json={"clipInput": UID})
    assert h.card_calls == 1
    client.post("/api/admin/singcup/clips/metrics/apply", json={"clipInput": UID})
    r = db(_row())
    assert (r["heart_count"], r["view_count"]) == (140, 1900)   # 두 번째 응답


def test_apply_partial_saves_only_the_field_it_read(client, db):
    """조회수를 끝내 못 받으면 하트만 저장하고 조회수는 건드리지 않는다."""
    db(_seed(hearts=100, views=0, last_view_at=0))
    _install(Seq(_card(likes=137, vod=False)))
    body = client.post("/api/admin/singcup/clips/metrics/apply",
                       json={"clipInput": UID}).json()
    r = db(_row())
    assert r["heart_count"] == 137
    assert r["view_count"] == 0 and r["last_view_at"] == 0
    assert body["after"]["viewState"] == "unknown"     # 여전히 '모름'
    assert body["external"]["partial"] is True


def test_apply_refuses_to_write_when_nothing_valid_came_back(client, db):
    db(_seed())
    before = db(_row())
    _install(Seq((500, {})))
    r = client.post("/api/admin/singcup/clips/metrics/apply",
                    json={"clipInput": UID})
    assert r.status_code == 502
    assert db(_row()) == before                        # 실패는 아무것도 쓰지 않는다


def test_apply_never_writes_a_malformed_value_as_zero(client, db):
    """malformed 응답이 진짜 0으로 둔갑해 저장되면 순위가 오염된다."""
    db(_seed(hearts=137, views=1828, last_view_at=999))
    _install(Seq(_card(likes=137, views="not-a-number")))
    client.post("/api/admin/singcup/clips/metrics/apply", json={"clipInput": UID})
    r = db(_row())
    assert r["view_count"] == 1828 and r["last_view_at"] == 999


# ── ③ single-flight ────────────────────────────────────────────────────────
def test_apply_is_blocked_while_the_clip_is_locked(client, db):
    """자동 스윕이 쥔 것과 **같은** 락이다 — 겹치면 409로 물러난다."""
    db(_seed())
    h = _install(Seq(_card(likes=137, views=1828)))
    token = db(sc.acquire_clip_lock(UID, wait=0))
    assert token is not None
    try:
        r = client.post("/api/admin/singcup/clips/metrics/apply",
                        json={"clipInput": UID})
        assert r.status_code == 409
        assert h.card_calls == 0                       # 외부로 나가지도 않는다
    finally:
        db(sc.release_clip_lock(UID, token))


def test_apply_releases_the_lock_even_on_failure(client, db):
    """실패 경로에서 락이 남으면 그 클립이 다음 사이클부터 계속 건너뛰어진다."""
    db(_seed())
    _install(Seq((500, {})))
    client.post("/api/admin/singcup/clips/metrics/apply", json={"clipInput": UID})
    token = db(sc.acquire_clip_lock(UID, wait=0))
    assert token is not None, "실패 후에도 락이 잡혀 있다"
    db(sc.release_clip_lock(UID, token))


def test_rate_limit_caps_external_calls(client, db, monkeypatch):
    import routers.admin_router as ar
    monkeypatch.setattr(ar, "_METRICS_LIMIT", 3)
    db(_seed())
    _install(Seq(_card(likes=137, views=1828)))
    codes = [client.post("/api/admin/singcup/clips/metrics/preview",
                         json={"clipInput": UID}).status_code for _ in range(5)]
    assert codes.count(200) == 3 and codes.count(429) == 2


# ── ④ 대표 불변 ────────────────────────────────────────────────────────────
def test_apply_does_not_change_the_representative(client, db):
    """지표 갱신은 대표 지정과 **별개 동작**이다."""
    db(_seed(rep=UID))
    _install(Seq(_card(likes=137, views=1828)))
    body = client.post("/api/admin/singcup/clips/metrics/apply",
                       json={"clipInput": UID}).json()
    assert body["representativeUnchanged"] is True
    assert db(_rep()) == UID


async def _rep():
    conn = await database.get_db()
    r = await (await conn.execute(
        "SELECT representative_clip_uid FROM singcup_streamers "
        "WHERE channel_id='own0'")).fetchone()
    return r["representative_clip_uid"]


def test_preview_warns_when_auto_representative_may_shift(client, db):
    """override가 없고 값이 바뀔 예정이면 **적용 전에** 경고한다."""
    db(_seed(rep=UID))
    db(_second_clip())
    _install(Seq(_card(likes=137, views=1828)))
    risk = client.post("/api/admin/singcup/clips/metrics/preview",
                       json={"clipInput": UID}).json()["representativeRisk"]
    assert risk["hasOverride"] is False
    assert risk["mayChangeAutoRepresentative"] is True
    assert risk["currentRepresentativeClipUid"] == UID


def test_preview_says_representative_is_pinned_when_override_exists(client, db):
    """override가 있으면 재계산이 그것을 우선하므로 경고 대신 유지 안내를 준다."""
    import singcup_overrides as so
    db(_seed(rep=UID))
    db(_second_clip())
    db(so.set_override("own0", UID, reason="테스트", event_id=EV))
    _install(Seq(_card(likes=137, views=1828)))
    risk = client.post("/api/admin/singcup/clips/metrics/preview",
                       json={"clipInput": UID}).json()["representativeRisk"]
    assert risk["hasOverride"] is True
    assert risk["overrideClipUid"] == UID
    assert risk["mayChangeAutoRepresentative"] is False


def test_apply_reports_representative_uid_before_and_after(client, db):
    """변경이 없을 때도 전후 clip UID를 돌려준다(계약 고정)."""
    db(_seed(rep=UID))
    _install(Seq(_card(likes=137, views=1828)))
    body = client.post("/api/admin/singcup/clips/metrics/apply",
                       json={"clipInput": UID}).json()
    assert body["autoRepresentativeChanged"] is False
    assert body["representativeBeforeClipUid"] == UID
    assert body["representativeAfterClipUid"] == UID
    assert body["representativeUnchanged"] is True      # 하위 호환 필드


def test_apply_on_a_non_representative_clip(client, db):
    """대표가 아닌 클립을 갱신해도 동작한다 — 대표는 그대로.

    (형제 클립의 하트가 훨씬 높아 순서가 뒤집히지 않는 경우)
    """
    db(_seed(hearts=1, views=1, rep="OtherClip1"))
    db(_second_clip())                       # 하트 99999 = 대표
    _install(Seq(_card(likes=2, views=3)))
    body = client.post("/api/admin/singcup/clips/metrics/apply",
                       json={"clipInput": UID}).json()
    assert body["before"]["isRepresentative"] is False
    assert body["after"]["isRepresentative"] is False
    assert body["autoRepresentativeChanged"] is False
    assert db(_rep()) == "OtherClip1"
    r = db(_row())
    assert (r["heart_count"], r["view_count"]) == (2, 3)


def test_failed_apply_leaves_representative_untouched(client, db):
    """Apply가 502로 실패하면 대표 상태도 지표도 그대로다."""
    db(_seed(rep=UID))
    db(_second_clip())
    before_rep = db(_rep())
    before_row = db(_row())
    _install(Seq((500, {})))
    r = client.post("/api/admin/singcup/clips/metrics/apply",
                    json={"clipInput": UID})
    assert r.status_code == 502
    assert db(_rep()) == before_rep
    assert db(_row()) == before_row


def test_apply_invalidates_main_cache(client, db):
    """저장 후 `/main` 캐시를 무효화한다 — 안 하면 공개 랭킹이 옛 값을 계속 준다."""
    db(_seed())
    _install(Seq(_card(likes=137, views=1828)))
    calls = {"n": 0}
    real = sc.invalidate_main_cache
    sc.invalidate_main_cache = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1),
                                                real(*a, **k))[1]
    try:
        client.post("/api/admin/singcup/clips/metrics/apply", json={"clipInput": UID})
    finally:
        sc.invalidate_main_cache = real
    assert calls["n"] >= 1


def test_auto_representative_may_shift_and_is_reported(client, db):
    """지정하지 않아도 **자동 선정 결과**는 바뀔 수 있다 — 그 사실을 응답에 남긴다.

    지표 갱신은 대표를 직접 쓰지 않지만, 갱신된 값으로 순위를 다시 계산하므로
    자동 규칙(하트↓ → 조회수↓)의 1등이 달라지면 대표도 따라 움직인다. 브라우저 QA에서
    실제로 관측됐다. 조용히 넘어가면 "왜 대표가 바뀌었지"를 설명할 수 없으므로
    `representativeUnchanged=false`로 보고하는 것까지가 계약이다.
    """
    db(_seed(rep=UID))
    db(_second_clip())                       # 하트 99999 — 자동 규칙의 새 1등
    _install(Seq(_card(likes=1, views=1)))
    body = client.post("/api/admin/singcup/clips/metrics/apply",
                       json={"clipInput": UID}).json()
    assert body["ok"] is True
    assert body["autoRepresentativeChanged"] is True
    assert body["representativeBeforeClipUid"] == UID
    assert body["representativeAfterClipUid"] == "OtherClip1"
    assert body["hasOverride"] is False
    assert db(_rep()) == "OtherClip1"


def test_manual_override_is_preserved_across_apply(client, db):
    """수동 대표 override가 걸려 있으면 지표 갱신 후 재계산에도 그대로 남는다."""
    import singcup_overrides as so
    db(_seed(rep=UID))
    db(_second_clip())
    db(so.set_override("own0", UID, reason="테스트", event_id=EV))
    _install(Seq(_card(likes=1, views=1)))
    body = client.post("/api/admin/singcup/clips/metrics/apply",
                       json={"clipInput": UID}).json()
    assert db(_rep()) == UID          # 하트가 더 높은 클립이 있어도 override 유지
    assert body["autoRepresentativeChanged"] is False
    assert body["hasOverride"] is True
    assert body["after"]["isRepresentative"] is True


async def _second_clip():
    conn = await database.get_db()
    now = int(time.time())
    await conn.execute(
        "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
        " rec_id, clip_title, thumbnail_image_url, description, created_at,"
        " heart_count, view_count, duration, adult, blind_type, metrics_ok, active,"
        " deletion_state, missing_scan_count, first_collected_at, last_collected_at,"
        " row_updated_at) VALUES (?,?,?,?,'{}','other','','#싱드컵',?,?,?,60,0,'',1,1,"
        "'active',0,?,?,?)",
        ("OtherClip1", EV, "own0", "vid-2", now - 5000, 99999, 99999, now, now, now))
    await conn.commit()


# ── ⑤ 비노출 ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["preview", "apply"])
def test_response_leaks_no_secrets_or_internals(client, db, path, monkeypatch):
    monkeypatch.setenv("SINGCUP_ADMIN_SECRET", "TOP_SECRET_VALUE")
    db(_seed())
    _install(Seq(_card(likes=137, views=1828)))
    text = client.post(f"/api/admin/singcup/clips/metrics/{path}",
                       json={"clipInput": UID}).text
    for banned in ("TOP_SECRET_VALUE", "_video_id", "_rec_id", "vid-1",
                   "Authorization", "api-videohub", "seedMediaId"):
        assert banned not in text, f"{banned}가 응답에 노출됐다"
