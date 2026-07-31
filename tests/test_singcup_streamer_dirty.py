"""랭킹 스트리머 행의 '바뀐 행만 쓰기' 판정 — 운영 payload 그대로.

배경(실측 2026-08-01 Railway): 배포 직후 `streamers_upserted`가
`1155/1155 · 1156/1156 · 1156/1156 · 1156/1156 · 1156/1156 · 1156/1156`으로
**6회 연속 considered == written**이었다. 워밍업이 아니라 구조적 결함이다.

원인은 `last_channel_updated_at = now if info else 0`이 변경 판정 집합
(`_STREAMER_FIELDS`)에 들어 있었던 것이다. `fetch_channel`은 **캐시 적중 시에도
info dict를 돌려주므로** 이 필드는 매 회차 그 회차의 `now`로 새로 찍히고, 그래서
데이터가 하나도 안 바뀌어도 전원이 '바뀐 행'이 됐다.

**이 파일이 존재하는 이유**: 기존 벤치·테스트가 이걸 못 잡은 것은
`last_channel_updated_at=0`을 넣어 운영 payload를 재현하지 못했기 때문이다
(0이면 `upd`가 False라 기존 값이 유지되어 '안 바뀜'으로 판정된다). 그러니 여기서는
반드시 **truthy 채널 info + 매 호출마다 다른 now**로 `recompute_ranking`을 돌린다.
`_upsert_streamers_bulk`를 직접 부르지 않는 것도 같은 이유다 — payload를 만드는
쪽이 결함의 절반이었다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import singcup_clips as sc

KST = timezone(timedelta(hours=9))
NOW_TS = int(datetime(2026, 7, 28, 12, 0, 0, tzinfo=KST).timestamp())


# ── 헬퍼 ───────────────────────────────────────────────────────────────────
async def _seed_clips(specs: list[tuple[str, str, int, int]]):
    """(clip_uid, owner_channel_id, heart, view) 목록을 한 번에 심는다.

    `owner_channel_name`/`owner_channel_image_url`을 **비워 둔다** — 그래야
    이름·이미지가 채널 API(info) 쪽에서 오고, 그 변화를 시험할 수 있다.
    (운영에서도 목록 응답이 비면 이 경로를 탄다.)
    """
    db = await sc.get_db()
    await db.executemany(
        "INSERT INTO singcup_clips (clip_uid, event_id, owner_channel_id, video_id,"
        " rec_id, clip_title, thumbnail_image_url, description, created_at,"
        " heart_count, view_count, duration, adult, blind_type, metrics_ok,"
        " owner_channel_name, owner_channel_image_url, owner_verified, active,"
        " missing_scan_count, first_collected_at, last_collected_at, row_updated_at,"
        " deletion_state, deletion_first_at, deletion_last_at, deletion_reason)"
        " VALUES (?,?,?,?,'','제목','','#싱드컵',?,?,?,60,0,'',1,'','',0,1,0,?,?,?,"
        " 'active',0,0,'')",
        [(uid, sc.EVENT_ID, owner, f"v{uid}", NOW_TS, heart, view,
          NOW_TS, NOW_TS, NOW_TS) for uid, owner, heart, view in specs])
    await db.commit()


def _info(i: int) -> dict:
    """`fetch_channel`이 캐시 적중 때 돌려주는 것과 같은 모양의 dict."""
    return {"channel_name": f"스트리머{i}", "channel_image_url": f"img{i}",
            "follower_count": 100 + i, "verified_mark": 0}


class Channels:
    """`fetch_channel` 대역. 운영의 캐시 적중처럼 **항상 truthy dict**를 돌려준다."""

    def __init__(self, n: int):
        self.data = {f"o{i:05d}": _info(i) for i in range(n)}
        self.missing: set[str] = set()
        self.calls = 0

    def install(self, monkeypatch):
        async def fetch_channel(client, channel_id):
            self.calls += 1
            if channel_id in self.missing:
                return None            # 채널 API 실패 — recompute는 {}로 받는다
            return self.data.get(channel_id)
        monkeypatch.setattr(sc, "fetch_channel", fetch_channel)
        return self


def _watch(monkeypatch) -> list[dict]:
    """`_upsert_streamers_bulk`의 집계를 회차별로 모은다(로그와 같은 값)."""
    stats: list[dict] = []
    orig = sc._upsert_streamers_bulk

    async def wrapped(rows, now):
        st = await orig(rows, now)
        stats.append(st)
        return st

    monkeypatch.setattr(sc, "_upsert_streamers_bulk", wrapped)
    return stats


async def _read_all():
    db = await sc.get_db()
    return {r["channel_id"]: dict(r) for r in await (await db.execute(
        "SELECT channel_id, channel_name, channel_image_url, follower_count, "
        "verified_mark, representative_clip_uid, tagged_clip_count, "
        "last_channel_updated_at FROM singcup_streamers")).fetchall()}


# ── 1. 핵심 회귀 — 운영 payload로 2회차 written == 0 ────────────────────────
@pytest.mark.parametrize("n", [1155, 4000])
def test_second_recompute_writes_nothing_when_data_is_unchanged(db, monkeypatch, n):
    """운영에서 6회 연속 1155/1155가 나온 그 조건을 그대로 재현한다.

    이 테스트가 실패로 돌아가는 유일한 조건은 `last_channel_updated_at`이 다시
    변경 판정에 들어가는 것이다 — 그러면 written이 다시 n이 된다.
    """
    ch = Channels(n).install(monkeypatch)
    db(_seed_clips([(f"c{i:05d}", f"o{i:05d}", i, i * 2) for i in range(n)]))
    stats = _watch(monkeypatch)

    db(sc.recompute_ranking(NOW_TS))                  # 최초 — 전원 신규
    assert stats[-1] == {"considered": n, "written": n}
    first = db(_read_all())
    assert len(first) == n, "누락"

    db(sc.recompute_ranking(NOW_TS + 600))            # 데이터 동일, 시각만 다름
    assert stats[-1] == {"considered": n, "written": 0}, \
        "값이 안 바뀌었는데 다시 썼다 — last_channel_updated_at이 판정에 들어갔다"

    db(sc.recompute_ranking(NOW_TS + 1200))           # 한 번 더 — 계속 0이어야 한다
    assert stats[-1] == {"considered": n, "written": 0}

    after = db(_read_all())
    assert after == first, "쓰지 않았는데 저장값이 달라졌다"
    assert ch.calls == n * 3


# ── 2. 필드별 — 실제로 바뀐 한 행만 쓰고, 저장값도 실제로 바뀐다 ────────────
def _setup_one(db, monkeypatch, n=50):
    ch = Channels(n).install(monkeypatch)
    db(_seed_clips([(f"c{i:05d}", f"o{i:05d}", i, i * 2) for i in range(n)]))
    stats = _watch(monkeypatch)
    db(sc.recompute_ranking(NOW_TS))
    db(sc.recompute_ranking(NOW_TS + 60))
    assert stats[-1]["written"] == 0, "출발점부터 안정 상태여야 한다"
    return ch, stats


def test_nickname_change_writes_exactly_one_row(db, monkeypatch):
    ch, stats = _setup_one(db, monkeypatch)
    ch.data["o00007"]["channel_name"] = "새이름"
    db(sc.recompute_ranking(NOW_TS + 120))
    assert stats[-1]["written"] == 1
    rows = db(_read_all())
    assert rows["o00007"]["channel_name"] == "새이름"
    assert rows["o00008"]["channel_name"] == "스트리머8", "옆 행이 영향받았다"


def test_follower_change_writes_exactly_one_row(db, monkeypatch):
    ch, stats = _setup_one(db, monkeypatch)
    ch.data["o00007"]["follower_count"] = 999_999
    db(sc.recompute_ranking(NOW_TS + 120))
    assert stats[-1]["written"] == 1
    assert db(_read_all())["o00007"]["follower_count"] == 999_999


def test_image_change_writes_exactly_one_row(db, monkeypatch):
    ch, stats = _setup_one(db, monkeypatch)
    ch.data["o00007"]["channel_image_url"] = "https://img/new.png"
    db(sc.recompute_ranking(NOW_TS + 120))
    assert stats[-1]["written"] == 1
    assert db(_read_all())["o00007"]["channel_image_url"] == "https://img/new.png"


def test_verified_mark_change_writes_exactly_one_row(db, monkeypatch):
    ch, stats = _setup_one(db, monkeypatch)
    ch.data["o00007"]["verified_mark"] = 1
    db(sc.recompute_ranking(NOW_TS + 120))
    assert stats[-1]["written"] == 1
    assert db(_read_all())["o00007"]["verified_mark"] == 1


def test_representative_clip_change_writes_exactly_one_row(db, monkeypatch):
    """하트가 더 높은 새 클립이 들어오면 대표가 바뀐다(tagged_clip_count도 함께)."""
    _ch, stats = _setup_one(db, monkeypatch)
    db(_seed_clips([("zzzzz", "o00007", 10_000, 10)]))
    db(sc.recompute_ranking(NOW_TS + 120))
    assert stats[-1]["written"] == 1
    row = db(_read_all())["o00007"]
    assert row["representative_clip_uid"] == "zzzzz"
    assert row["tagged_clip_count"] == 2


def test_tagged_clip_count_change_writes_exactly_one_row(db, monkeypatch):
    """대표는 그대로 두고 클립 수만 늘린다 — 하트가 낮은 클립을 추가한다."""
    _ch, stats = _setup_one(db, monkeypatch)
    before = db(_read_all())["o00007"]["representative_clip_uid"]
    db(_seed_clips([("aaaaa", "o00007", 0, 0)]))
    db(sc.recompute_ranking(NOW_TS + 120))
    assert stats[-1]["written"] == 1
    row = db(_read_all())["o00007"]
    assert row["tagged_clip_count"] == 2
    assert row["representative_clip_uid"] == before, "대표가 바뀌면 안 되는 경우다"


# ── 3. 채널 API 실패 — 기존 값을 덮어쓰지 않고, 쓰지도 않는다 ───────────────
def test_missing_channel_info_neither_writes_nor_overwrites(db, monkeypatch):
    """info가 비면 `last_channel_updated_at=0` → 이름·이미지·팔로워는 기존 값 유지.

    유지되는 값이므로 '바뀐 행'도 아니다 — 장애 때 전원 재기록이 나면 안 된다.
    """
    ch, stats = _setup_one(db, monkeypatch)
    before = db(_read_all())
    ch.missing.add("o00007")
    db(sc.recompute_ranking(NOW_TS + 120))
    assert stats[-1]["written"] == 0, "API 실패가 쓰기를 유발했다"
    assert db(_read_all()) == before

    # 실패가 계속돼도 값이 상하지 않는다
    db(sc.recompute_ranking(NOW_TS + 180))
    assert stats[-1]["written"] == 0
    row = db(_read_all())["o00007"]
    assert row["channel_name"] == "스트리머7"
    assert row["channel_image_url"] == "img7"
    assert row["follower_count"] == 107


def test_recovery_after_failure_writes_only_if_value_differs(db, monkeypatch):
    """실패 후 같은 값으로 복구되면 여전히 쓰지 않고, 다른 값이면 쓴다."""
    ch, stats = _setup_one(db, monkeypatch)
    ch.missing.add("o00007")
    db(sc.recompute_ranking(NOW_TS + 120))
    ch.missing.clear()
    db(sc.recompute_ranking(NOW_TS + 180))
    assert stats[-1]["written"] == 0, "같은 값으로 돌아온 것뿐이다"
    ch.data["o00007"]["follower_count"] = 1
    db(sc.recompute_ranking(NOW_TS + 240))
    assert stats[-1]["written"] == 1
    assert db(_read_all())["o00007"]["follower_count"] == 1


# ── 4. 중복·누락 0, 트랜잭션 보장 유지 ─────────────────────────────────────
def test_no_duplicate_or_missing_rows_across_repeated_recomputes(db, monkeypatch):
    n = 300
    Channels(n).install(monkeypatch)
    db(_seed_clips([(f"c{i:05d}", f"o{i:05d}", i, i * 2) for i in range(n)]))
    for k in range(5):
        db(sc.recompute_ranking(NOW_TS + k * 60))

    async def counts():
        conn = await sc.get_db()
        total = (await (await conn.execute(
            "SELECT COUNT(*) c FROM singcup_streamers")).fetchone())["c"]
        distinct = (await (await conn.execute(
            "SELECT COUNT(DISTINCT channel_id) c FROM singcup_streamers")).fetchone())["c"]
        return total, distinct

    total, distinct = db(counts())
    assert total == distinct == n


def test_recompute_still_commits_exactly_once(db, monkeypatch):
    """쓰는 양을 줄여도 **트랜잭션은 하나**여야 한다 — 부분 랭킹이 보이면 안 된다."""
    n = 100
    Channels(n).install(monkeypatch)
    db(_seed_clips([(f"c{i:05d}", f"o{i:05d}", i, i * 2) for i in range(n)]))

    async def go():
        conn = await sc.get_db()
        orig = conn.commit
        calls = []

        async def counting():
            calls.append(1)
            await orig()

        monkeypatch.setattr(conn, "commit", counting)
        await sc.recompute_ranking(NOW_TS)          # 전원 신규 — 실제로 쓴다
        assert calls == [1], f"COMMIT {len(calls)}회 — 하나여야 한다"
        calls.clear()
        await sc.recompute_ranking(NOW_TS + 60)     # 쓸 게 없어도 COMMIT은 한 번
        assert calls == [1]

    db(go())


# ── 5. 판정 집합 자체를 고정한다 ───────────────────────────────────────────
def test_last_channel_updated_at_is_not_a_dirty_field():
    """되돌리기 방지. 이 필드가 다시 들어오면 written == considered로 회귀한다."""
    assert "last_channel_updated_at" not in sc._STREAMER_FIELDS
    assert set(sc._STREAMER_FIELDS) == {
        "channel_name", "channel_image_url", "follower_count", "verified_mark",
        "representative_clip_uid", "tagged_clip_count"}
