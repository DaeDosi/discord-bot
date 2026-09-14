"""PUBLIC-UX-1 — 수정 요청 **실제 접수 경로**(라우터)와 OWNER 처리 화면 계약.

`test_account_support.py`는 `support` 모듈을 직접 부른다. 여기서는 HTTP 경계에서
상태 코드·응답 본문·권한을 고정한다 — 화면은 이 코드로 안내 문구를 고른다.
운영 DB·운영 salt를 쓰지 않는다(conftest 임시 DB + 테스트 salt).
"""
import pytest
import support
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database

TEST_SALT = "test-salt-0123456789abcdef"
OWNER = "111111111111111111"


def _body(**over):
    b = {"category": "wrong_metric", "clipRef": "abcDEF123",
         "description": "하트 수가 실제와 다릅니다. 확인 부탁드립니다."}
    b.update(over)
    return b


@pytest.fixture
def app(db, monkeypatch):
    async def _clear():
        c = await database.get_db()
        await c.execute("DELETE FROM correction_requests")
        await c.commit()
    db(_clear())
    support.reset_state()
    monkeypatch.setenv(support.SALT_ENV, TEST_SALT)
    # SUPPORT-POLICY-1b: 접수는 보관 정책 정리가 실제로 가동 중일 때만 열린다.
    import support_retention
    monkeypatch.setenv("SUPPORT_RETENTION_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_RETENTION_DRY_RUN", "false")
    monkeypatch.setattr(support_retention, "_worker_running", True)

    import routers.account_router as acr
    import routers.admin_router as ar
    from deps import get_current_user
    monkeypatch.setattr(ar, "_OWNER_ID", OWNER)
    a = FastAPI()
    a.include_router(acr.support_router)
    a.include_router(ar.router)
    a.state._dep = get_current_user
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def _owner(client, sub=OWNER):
    client.app.dependency_overrides[client.app.state._dep] = lambda: {"sub": sub}
    return client


# ── 공개 접수 ───────────────────────────────────────────────────────────────

def test_정상_접수는_번호만_돌려준다(client):
    r = client.post("/api/support/correction", json=_body())
    assert r.status_code == 200
    assert set(r.json()) == {"ok", "id"} and r.json()["id"] > 0


@pytest.mark.parametrize("ref", [
    "https://chzzk.naver.com/clips/abcDEF123",
    "https://nexbot.shop/stats?tab=singcup",
    "abcDEF123", "clip_01-x",
])
def test_클립_주소와_ID를_받는다(client, ref):
    assert client.post("/api/support/correction", json=_body(clipRef=ref)).status_code == 200


@pytest.mark.parametrize("ref", [
    "", "그 노래 클립", "http://chzzk.naver.com/clips/x", "javascript:alert(1)",
    "https://", "a b", "<script>alert(1)</script>",
])
def test_식별할_수_없는_대상은_400(client, ref):
    r = client.post("/api/support/correction", json=_body(clipRef=ref))
    assert r.status_code == 400
    assert isinstance(r.json()["detail"], str)


def test_과대_입력은_400(client):
    r = client.post("/api/support/correction",
                    json=_body(description="가" * (support.MAX_DESCRIPTION + 1)))
    assert r.status_code == 400
    r = client.post("/api/support/correction",
                    json=_body(clipRef="a" * (support.MAX_CLIP_REF + 1)))
    assert r.status_code == 400


def test_중복은_409(client):
    assert client.post("/api/support/correction", json=_body()).status_code == 200
    r = client.post("/api/support/correction", json=_body())
    assert r.status_code == 409
    assert "이미 접수" in r.json()["detail"]


def test_잦은_제출은_429(client):
    for i in range(support.RATE_LIMIT):
        assert client.post("/api/support/correction",
                           json=_body(clipRef=f"clip{i}")).status_code == 200
    r = client.post("/api/support/correction", json=_body(clipRef="clipX"))
    assert r.status_code == 429


def test_salt_미설정은_503이고_설정_단서가_없다(client, monkeypatch):
    monkeypatch.delenv(support.SALT_ENV, raising=False)
    r = client.post("/api/support/correction", json=_body())
    assert r.status_code == 503
    assert support.SALT_ENV not in r.text and "salt" not in r.text.lower()
    meta = client.get("/api/support/correction/meta").json()
    assert meta["accepting"] is False
    assert support.SALT_ENV not in str(meta)


def test_저장소_장애는_503이고_원인을_노출하지_않는다(client, monkeypatch):
    class Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("disk I/O error at /data/bot.db INSERT INTO correction_requests")

        async def rollback(self):
            raise RuntimeError("rollback failed")

    async def fake_db():
        return Boom()
    monkeypatch.setattr(support, "get_db", fake_db)
    r = client.post("/api/support/correction", json=_body())
    assert r.status_code == 503
    for bad in ("disk", "/data", "INSERT", "correction_requests", "Traceback"):
        assert bad not in r.text


def test_스크립트_문자열은_평문_JSON으로만_돌아온다(client):
    raw = "<script>alert(1)</script> 하트 수가 실제와 다릅니다 <img src=x onerror=1>"
    r = client.post("/api/support/correction", json=_body(description=raw))
    assert r.status_code == 200
    _owner(client)
    res = client.get("/api/admin/support/corrections")
    assert res.headers["content-type"].startswith("application/json"), "HTML로 내려가지 않는다"
    assert res.json()["items"][0]["description"] == raw, "평문 그대로(변형·이스케이프 없음)"


def test_공백을_정규화한다(client):
    r = client.post("/api/support/correction", json=_body(
        clipRef="  abcSpace1  ", description="  하트   수가\t\t실제와   다릅니다  "))
    assert r.status_code == 200
    _owner(client)
    item = client.get("/api/admin/support/corrections").json()["items"][0]
    assert item["clipRef"] == "abcSpace1"
    assert item["description"] == "하트 수가 실제와 다릅니다"


def test_목록_조회_상한은_100건이다(client):
    _owner(client)
    import inspect
    src = inspect.getsource(support.list_requests)
    assert "min(int(limit or 50), 100)" in src
    assert client.get("/api/admin/support/corrections?limit=100000").status_code == 200


def test_JSON_객체가_아니면_거절한다(client):
    r = client.post("/api/support/correction", content=b"[1,2]",
                    headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)


# ── OWNER 처리 ──────────────────────────────────────────────────────────────

def test_목록은_인증이_없으면_거절한다(client):
    r = client.get("/api/admin/support/corrections")
    assert r.status_code in (401, 403)


def test_OWNER가_아니면_403(client):
    _owner(client, sub="222")
    assert client.get("/api/admin/support/corrections").status_code == 403
    assert client.post("/api/admin/support/corrections/1/status",
                       json={"status": "resolved"}).status_code == 403


def test_OWNER는_목록을_보고_해시는_보지_않는다(client):
    client.post("/api/support/correction", json=_body(email="me@example.com",
                                                    evidenceUrl="https://example.com/x"))
    client.post("/api/support/correction", json=_body(clipRef="other1"))
    _owner(client)
    r = client.get("/api/admin/support/corrections")
    assert r.status_code == 200
    body = r.json()
    assert [i["clipRef"] for i in body["items"]] == ["other1", "abcDEF123"], "최신 순"
    assert body["counts"]["received"] == 2
    assert body["items"][1]["contactEmail"] == "me@example.com"
    assert "dedupe" not in r.text and TEST_SALT not in r.text


def test_OWNER가_상태를_바꾸고_필터로_읽는다(client):
    rid = client.post("/api/support/correction", json=_body()).json()["id"]
    _owner(client)
    r = client.post(f"/api/admin/support/corrections/{rid}/status",
                    json={"status": "resolved"})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    resolved = client.get("/api/admin/support/corrections?status=resolved").json()
    assert resolved["items"][0]["id"] == rid
    assert client.get("/api/admin/support/corrections?status=received").json()["items"] == []
    assert client.post(f"/api/admin/support/corrections/{rid}/status",
                       json={"status": "deleted"}).status_code == 400
    assert client.post("/api/admin/support/corrections/999999/status",
                       json={"status": "resolved"}).status_code == 400
    assert client.get("/api/admin/support/corrections?status=nope").status_code == 400


def test_목록_쪽_나눔(client):
    for i in range(5):
        support.reset_state()
        client.post("/api/support/correction", json=_body(clipRef=f"page{i}"))
    _owner(client)
    first = client.get("/api/admin/support/corrections?limit=2").json()
    assert len(first["items"]) == 2 and first["hasMore"] is True
    last_id = first["items"][-1]["id"]
    nxt = client.get(f"/api/admin/support/corrections?limit=2&before={last_id}").json()
    assert [i["clipRef"] for i in nxt["items"]] == ["page2", "page1"]
