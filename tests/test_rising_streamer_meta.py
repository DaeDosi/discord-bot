"""SSR 메타데이터 전용 경량 API와 SSR 전용 rate-limit 버킷 (P1-D).

실측(2026-08-01): 스트리머 페이지의 SSR이 메타데이터를 만들려고 **전체 대시보드**를
받아 갔고, 모든 방문자·크롤러의 SSR이 하나의 rate-limit 버킷으로 합쳐져 429가 났다.
그때 폴백이 `robots: index=false`를 달았으므로 **크롤링당하는 순간 색인에서 빠졌다.**

여기서 고정하는 것:
  - 메타 응답에는 시계열·세션·카테고리가 없다(작고 싸다)
  - 인덱스를 실제로 탄다
  - 본문이 바뀌면 ETag도 바뀐다 / 같으면 304
  - 서버임이 증명될 때만 SSR 버킷을 쓴다(자동 면제 없음)
  - 일반 사용자의 heavy 제한은 그대로다
"""
import importlib
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import database

CHANNEL = "abc123def456"
HOUR = 3600


@pytest.fixture
def client(db):
    import routers.rising_router as rr
    app = FastAPI()
    app.include_router(rr.router)
    return TestClient(app)


async def _seed(channel_id=CHANNEL, *, hours=5, viewers=100, peak=200, name="테스트채널"):
    conn = await database.get_db()
    await conn.execute("DELETE FROM rising_hourly_rollup")
    await conn.execute("DELETE FROM rising_live_snapshots")
    now = int(time.time())
    for i in range(hours):
        await conn.execute(
            """INSERT INTO rising_hourly_rollup
               (chzzk_channel_id, hour_ts, snaps, sum_viewers, peak_viewers,
                max_follower, category_name, channel_name)
               VALUES (?,?,?,?,?,?,?,?)""",
            (channel_id, now - i * HOUR, 6, viewers * 6, peak, 1000, "게임", name))
    await conn.commit()


async def _set_name(channel_id, name):
    conn = await database.get_db()
    await conn.execute(
        """INSERT INTO rising_live_snapshots
           (chzzk_channel_id, collected_at, channel_name, category_name,
            concurrent_viewers, follower_count, live_title)
           VALUES (?,?,?,?,?,?,?)""",
        (channel_id, int(time.time()), name, "게임", 100, 1000, "제목"))
    await conn.commit()


# ── 1. 응답 모양 ───────────────────────────────────────────────────────────
def test_meta_returns_only_what_ssr_needs(db, client):
    db(_seed())
    body = client.get(f"/api/rising/streamer/{CHANNEL}/meta").json()
    assert body["found"] is True
    assert body["channel_id"] == CHANNEL
    assert body["channel_name"] == "테스트채널"
    assert set(body["summary"]) == {"avg_viewers", "peak_viewers", "broadcast_hours"}
    assert body["summary"]["avg_viewers"] == 100
    assert body["summary"]["peak_viewers"] == 200
    assert body["summary"]["broadcast_hours"] == 5.0      # 5시간 × 6스냅 × 10분
    assert body["updated_at"]
    # 무거운 필드가 하나도 없어야 한다
    for heavy in ("daily", "weekly", "categories", "first_broadcast",
                  "total_live_hours", "history_days", "live_title"):
        assert heavy not in body, heavy


def test_unknown_channel(db, client):
    db(_seed())
    body = client.get("/api/rising/streamer/nosuchchannel/meta").json()
    assert body["found"] is False
    assert body["summary"] is None
    assert body["channel_name"] is None


def test_meta_has_no_secrets_or_personal_data(db, client):
    db(_seed())
    text = client.get(f"/api/rising/streamer/{CHANNEL}/meta").text.lower()
    for bad in ("token", "secret", "authorization", "cookie", "user_id", "ip"):
        assert bad not in text, bad


def test_live_snapshot_name_wins(db, client):
    """롤업 이름은 갱신이 늦다 — 최신 스냅샷 이름을 우선한다."""
    db(_seed(name="옛이름"))
    db(_set_name(CHANNEL, "새이름"))
    assert client.get(f"/api/rising/streamer/{CHANNEL}/meta").json()["channel_name"] == "새이름"


# ── 2. 인덱스를 실제로 타는가 ──────────────────────────────────────────────
def test_meta_query_uses_the_channel_index(db):
    async def plan():
        conn = await database.get_db()
        rows = await (await conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT SUM(snaps), SUM(sum_viewers), MAX(peak_viewers), MAX(hour_ts) "
            "FROM rising_hourly_rollup WHERE chzzk_channel_id=? AND hour_ts >= ?",
            (CHANNEL, 0))).fetchall()
        return " ".join(str(dict(r)) for r in rows)

    detail = db(plan())
    assert "idx_rising_roll" in detail, detail
    assert "SCAN rising_hourly_rollup" not in detail, f"전체 스캔이다: {detail}"


def test_name_lookup_uses_the_snapshot_index(db):
    async def plan():
        conn = await database.get_db()
        rows = await (await conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT channel_name FROM rising_live_snapshots "
            "WHERE chzzk_channel_id=? ORDER BY collected_at DESC LIMIT 1",
            (CHANNEL,))).fetchall()
        return " ".join(str(dict(r)) for r in rows)

    detail = db(plan())
    assert "idx_rising_snap_channel" in detail, detail


# ── 3. 캐시와 ETag ─────────────────────────────────────────────────────────
def test_cache_headers(db, client):
    db(_seed())
    r = client.get(f"/api/rising/streamer/{CHANNEL}/meta")
    cc = r.headers["Cache-Control"]
    assert "public" in cc and "s-maxage=600" in cc and "stale-while-revalidate" in cc
    assert r.headers["ETag"].startswith('"')


def test_same_data_same_etag(db, client):
    db(_seed())
    a = client.get(f"/api/rising/streamer/{CHANNEL}/meta").headers["ETag"]
    b = client.get(f"/api/rising/streamer/{CHANNEL}/meta").headers["ETag"]
    assert a == b


@pytest.mark.parametrize("mutate", ["name", "viewers", "peak"])
def test_any_field_change_changes_the_etag(db, client, mutate):
    """일부 필드로만 ETag를 만들면 값이 바뀌어도 옛 메타가 계속 쓰인다."""
    db(_seed())
    before = client.get(f"/api/rising/streamer/{CHANNEL}/meta").headers["ETag"]
    if mutate == "name":
        db(_set_name(CHANNEL, "바뀐이름"))
    elif mutate == "viewers":
        db(_seed(viewers=999))
    else:
        db(_seed(peak=9999))
    after = client.get(f"/api/rising/streamer/{CHANNEL}/meta").headers["ETag"]
    assert after != before


def test_if_none_match_returns_304_without_body(db, client):
    db(_seed())
    etag = client.get(f"/api/rising/streamer/{CHANNEL}/meta").headers["ETag"]
    r = client.get(f"/api/rising/streamer/{CHANNEL}/meta",
                   headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert not r.content
    assert r.headers["ETag"] == etag
    assert "s-maxage=600" in r.headers["Cache-Control"]


def test_stale_etag_returns_200(db, client):
    db(_seed())
    r = client.get(f"/api/rising/streamer/{CHANNEL}/meta",
                   headers={"If-None-Match": '"stale"'})
    assert r.status_code == 200


# ── 4. 응답 크기 ───────────────────────────────────────────────────────────
def test_meta_is_small(db, client):
    db(_seed(hours=24 * 30))                     # 30일 가득
    meta = client.get(f"/api/rising/streamer/{CHANNEL}/meta")
    full = client.get(f"/api/rising/streamer/{CHANNEL}?days=30")
    assert len(meta.content) < 512, len(meta.content)
    assert len(meta.content) < len(full.content) / 10, (
        f"meta {len(meta.content)}B vs full {len(full.content)}B")


# ── 5. rate limit 버킷 ─────────────────────────────────────────────────────
@pytest.fixture
def limited(monkeypatch):
    """제한값을 작게 줄여 로드한 미들웨어."""
    monkeypatch.setenv("SSR_SHARED_SECRET", "s3cr3t-for-test")
    monkeypatch.setenv("RATE_LIMIT_SSR", "5")
    monkeypatch.setenv("RATE_LIMIT_HEAVY", "3")
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", "4")
    import rate_limit
    importlib.reload(rate_limit)
    app = FastAPI()
    app.add_middleware(rate_limit.RateLimitMiddleware)

    @app.get("/api/rising/streamer/{cid}/meta")
    async def _meta(cid: str):
        return {"ok": cid}

    @app.get("/api/rising/streamer/{cid}")
    async def _full(cid: str):
        return {"ok": cid}

    yield TestClient(app)
    importlib.reload(rate_limit)


def _hdr(secret="s3cr3t-for-test"):
    return {"X-Internal-SSR": secret, "X-Forwarded-For": "9.9.9.9"}


def test_trusted_ssr_uses_its_own_bucket(limited):
    """일반 heavy 한도(3)를 넘어서도 SSR은 자기 한도(5)까지 간다."""
    for i in range(5):
        assert limited.get(f"/api/rising/streamer/c{i}/meta",
                           headers=_hdr()).status_code == 200, i
    r = limited.get("/api/rising/streamer/cX/meta", headers=_hdr())
    assert r.status_code == 429
    assert r.headers["Retry-After"]
    assert r.headers["X-RateLimit-Limit"] == "5"


def test_ssr_bucket_does_not_leak_into_user_bucket(limited):
    """SSR이 자기 한도를 다 써도 일반 사용자는 멀쩡해야 한다."""
    for i in range(5):
        limited.get(f"/api/rising/streamer/c{i}/meta", headers=_hdr())
    r = limited.get("/api/rising/streamer/u1",
                    headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 200


def test_wrong_or_missing_secret_is_a_normal_user(limited):
    """헤더만 붙이면 통과되면 안 된다 — 아무나 붙일 수 있다."""
    for hdrs in ({"X-Internal-SSR": "wrong", "X-Forwarded-For": "5.5.5.5"},
                 {"X-Forwarded-For": "6.6.6.6"}):
        ok = 0
        for i in range(6):
            if limited.get(f"/api/rising/streamer/c{i}/meta", headers=hdrs).status_code == 200:
                ok += 1
        assert ok == 4, f"{hdrs}: 일반 버킷(4)이어야 하는데 {ok}"


def test_heavy_limit_unchanged_for_detail(limited):
    """일반 상세 API 제한은 손대지 않았다."""
    hdrs = {"X-Forwarded-For": "7.7.7.7"}
    for i in range(3):
        assert limited.get(f"/api/rising/streamer/d{i}", headers=hdrs).status_code == 200
    assert limited.get("/api/rising/streamer/dX", headers=hdrs).status_code == 429


def test_no_secret_configured_means_no_trust(monkeypatch):
    """시크릿 미설정 시 서버가 죽으면 안 되고, SSR 자동 면제도 없어야 한다."""
    monkeypatch.delenv("SSR_SHARED_SECRET", raising=False)
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", "2")
    import rate_limit
    importlib.reload(rate_limit)
    app = FastAPI()
    app.add_middleware(rate_limit.RateLimitMiddleware)

    @app.get("/api/rising/streamer/{cid}/meta")
    async def _meta(cid: str):
        return {"ok": cid}

    c = TestClient(app)
    hdrs = {"X-Internal-SSR": "anything", "X-Forwarded-For": "8.8.8.8"}
    assert c.get("/api/rising/streamer/a/meta", headers=hdrs).status_code == 200
    assert c.get("/api/rising/streamer/b/meta", headers=hdrs).status_code == 200
    assert c.get("/api/rising/streamer/c/meta", headers=hdrs).status_code == 429
    importlib.reload(rate_limit)


def test_secret_never_appears_in_responses(limited):
    r = limited.get("/api/rising/streamer/c1/meta", headers=_hdr())
    assert "s3cr3t-for-test" not in r.text
    assert "s3cr3t-for-test" not in json.dumps(dict(r.headers))


# ── 6. 경로 분류 정확성 ────────────────────────────────────────────────────
@pytest.mark.parametrize("path,is_meta", [
    ("/api/rising/streamer/abc123/meta", True),
    ("/api/rising/streamer/ABC_-123/meta", True),
    ("/api/rising/streamer/abc123", False),
    ("/api/rising/streamer/abc123/detail", False),
    ("/api/rising/streamer/abc123/session", False),
    ("/api/rising/metadata/abc", False),
    ("/api/rising/streamer/abc123/meta/extra", False),
    ("/api/rising/streamer/abc123/meta/", False),
    ("/api/rising/streamer//meta", False),
    ("/api/rising/streamer/abc/../meta", False),
    ("/api/rising/streamer/%2Fmeta/meta", False),   # 인코딩 우회
])
def test_meta_path_classifier(path, is_meta):
    """substring 검사면 metadata·쿼리·인코딩 우회가 전부 SSR 그룹으로 샌다."""
    import rate_limit
    assert rate_limit._is_meta(path) is is_meta, path


def test_query_string_cannot_fake_meta(limited):
    """path만 보므로 쿼리에 /meta를 넣어도 heavy 그대로다(3회에서 막힌다)."""
    hdrs = {"X-Forwarded-For": "4.4.4.4", "X-Internal-SSR": "s3cr3t-for-test"}
    codes = [limited.get(f"/api/rising/streamer/q{i}?x=/meta", headers=hdrs).status_code
             for i in range(4)]
    assert codes.count(200) == 3 and codes[-1] == 429


# ── 7. SSR 상한 값 검증 ────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("", 240), ("0", 240), ("-5", 240), ("abc", 240),
    ("999999", 240), ("120", 120), ("240", 240),
])
def test_ssr_limit_validation(monkeypatch, raw, expected):
    """설정 실수 하나로 제한이 사실상 풀리면 안 된다."""
    import rate_limit
    monkeypatch.setenv("RATE_LIMIT_SSR", raw)
    assert rate_limit._ssr_limit() == expected


def test_empty_secret_never_authenticates(monkeypatch):
    """빈 문자열끼리 비교해 통과하는 경우가 없어야 한다."""
    monkeypatch.setenv("SSR_SHARED_SECRET", "")
    import rate_limit
    importlib.reload(rate_limit)

    class _Req:
        headers = {"x-internal-ssr": ""}

    assert rate_limit._is_trusted_ssr(_Req()) is False
    importlib.reload(rate_limit)


# ── 8. ETag 안정성 ─────────────────────────────────────────────────────────
def test_repeated_calls_are_byte_identical(db, client):
    """updated_at을 처리 시각으로 만들면 매 요청 ETag가 바뀌어 304가 영영 안 된다."""
    db(_seed())
    first = client.get(f"/api/rising/streamer/{CHANNEL}/meta")
    for _ in range(9):
        again = client.get(f"/api/rising/streamer/{CHANNEL}/meta")
        assert again.content == first.content
        assert again.headers["ETag"] == first.headers["ETag"]
    r = client.get(f"/api/rising/streamer/{CHANNEL}/meta",
                   headers={"If-None-Match": first.headers["ETag"]})
    assert r.status_code == 304


def test_updated_at_comes_from_the_data(db, client):
    db(_seed())
    body = client.get(f"/api/rising/streamer/{CHANNEL}/meta").json()
    now = int(time.time())
    assert body["updated_at"] <= now
    # 최신 hour_ts(= 방금 seed한 시각)와 같은 눈금이어야 한다
    assert now - body["updated_at"] < 120
