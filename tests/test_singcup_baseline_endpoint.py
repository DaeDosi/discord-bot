"""관리자 진단 엔드포인트 `/api/singcup/snapshots/baseline` 보안·계약 검증.

이 응답은 운영 내부 구조(버킷 인원·커버리지·후보 목록)를 드러내므로 공개되면
안 된다. secret이 설정되지 않은 배포에서는 '빈 문자열과 일치해 열리는' 사고를
막기 위해 아예 닫혀야 한다.
"""
import time

import pytest
import routers.singcup_router as R
import singcup_clips as sc
from fastapi import HTTPException

import database

HOUR = 3600
SECRET = "s3cr3t-for-test"


async def _seed(now: int):
    c = await database.get_db()
    t = sc.snapshot_bucket(now - HOUR) + 600
    await c.execute(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at,"
        " snapshot_bucket) VALUES (?,?,?,?,0,0,0,1,?,?)",
        (sc.EVENT_ID, "clip-a", "a", 10, t, sc.snapshot_bucket(t)))
    await c.commit()


def test_requires_secret(db, monkeypatch):
    monkeypatch.setattr(R, "ADMIN_SECRET", SECRET)
    db(_seed(int(time.time())))
    with pytest.raises(HTTPException) as e:
        db(R.snapshot_baseline(x_singcup_secret=None))
    assert e.value.status_code == 401


def test_wrong_secret_is_rejected(db, monkeypatch):
    monkeypatch.setattr(R, "ADMIN_SECRET", SECRET)
    with pytest.raises(HTTPException) as e:
        db(R.snapshot_baseline(x_singcup_secret="wrong"))
    assert e.value.status_code == 401


def test_disabled_when_secret_is_not_configured(db, monkeypatch):
    """secret 미설정 배포에서는 빈 값과 일치해 열리지 않고 503으로 닫힌다."""
    monkeypatch.setattr(R, "ADMIN_SECRET", "")
    for candidate in (None, "", "anything"):
        with pytest.raises(HTTPException) as e:
            db(R.snapshot_baseline(x_singcup_secret=candidate))
        assert e.value.status_code == 503


def test_valid_secret_returns_diagnosis(db, monkeypatch):
    monkeypatch.setattr(R, "ADMIN_SECRET", SECRET)
    now = int(time.time())
    db(_seed(now))
    out = db(R.snapshot_baseline(x_singcup_secret=SECRET))
    assert out["selected"]["selectedRows"] == 1
    assert "candidates" in out and "day24h" in out


def test_response_has_no_secret_or_personal_data(db, monkeypatch):
    monkeypatch.setattr(R, "ADMIN_SECRET", SECRET)
    db(_seed(int(time.time())))
    import json
    body = json.dumps(db(R.snapshot_baseline(x_singcup_secret=SECRET)), default=str)
    assert SECRET not in body
    for leak in ("token", "Bearer", "password", "ip_hash", "user_id"):
        assert leak not in body


def test_payload_stays_small(db, monkeypatch):
    """진단이 전체 참가자 목록 같은 대형 payload를 돌려주면 안 된다."""
    monkeypatch.setattr(R, "ADMIN_SECRET", SECRET)
    now = int(time.time())
    c = db(database.get_db())
    t = sc.snapshot_bucket(now - HOUR) + 600
    db(c.executemany(
        "INSERT INTO singcup_snapshots (event_id, clip_uid, owner_channel_id,"
        " heart_count, view_count, follower_count, score, rank, collected_at,"
        " snapshot_bucket) VALUES (?,?,?,0,0,0,0,1,?,?)",
        [(sc.EVENT_ID, f"c{i}", f"o{i}", t, sc.snapshot_bucket(t)) for i in range(3000)]))
    db(c.commit())
    import json
    body = json.dumps(db(R.snapshot_baseline(x_singcup_secret=SECRET)), default=str)
    # 후보 버킷 요약만 담기므로 참가자 수와 무관하게 작아야 한다
    assert len(body) < 8000, f"진단 응답이 너무 크다: {len(body)}B"


def test_diagnosis_does_not_write(db, monkeypatch):
    monkeypatch.setattr(R, "ADMIN_SECRET", SECRET)
    now = int(time.time())
    db(_seed(now))
    c = db(database.get_db())

    async def counts():
        out = {}
        for t in ("singcup_snapshots", "singcup_top_movers", "singcup_clips",
                  "singcup_streamers", "singcup_sweep_runs"):
            row = await (await c.execute(f"SELECT COUNT(*) n FROM {t}")).fetchone()
            out[t] = row["n"]
        return out

    before = db(counts())
    for _ in range(5):
        db(R.snapshot_baseline(x_singcup_secret=SECRET))
    assert db(counts()) == before


@pytest.mark.parametrize("bad", [0, -5, -1])
def test_invalid_window_is_clamped_not_crashing(db, monkeypatch, bad):
    monkeypatch.setattr(R, "ADMIN_SECRET", SECRET)
    db(_seed(int(time.time())))
    out = db(R.snapshot_baseline(window_minutes=bad, x_singcup_secret=SECRET))
    assert out["windowMinutes"] >= 1


def test_huge_window_is_accepted_without_error(db, monkeypatch):
    monkeypatch.setattr(R, "ADMIN_SECRET", SECRET)
    db(_seed(int(time.time())))
    out = db(R.snapshot_baseline(window_minutes=10 ** 6, x_singcup_secret=SECRET))
    assert "selected" in out
