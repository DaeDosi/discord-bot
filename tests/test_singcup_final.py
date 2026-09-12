"""SINGCUP-FINAL-1 — 예선/본선 campaign 분리 · 본선 수집·매핑·공개 · 동결 계약.

이 파일은 **운영 레지스트리 그대로**(예선 frozen · 본선 active) 돈다 — conftest의
legacy fixture 목록에 없다. 예선 3부문의 원자 공개 계약은 `test_piku_collector.py`
등이 예선을 잠시 살려 두고 검증한다.

**실제 PIKU를 호출하지 않는다.** 본선 32행은 합성 데이터이며 대표자 이름만 공식
명단에서 빌려 온다(매핑 정확 일치 검증용). 운영 DB를 건드리지 않는다.
"""
import asyncio
import base64
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

import singcup_piku as piku  # noqa: E402
import singcup_piku_campaigns as camps  # noqa: E402
import singcup_piku_collector as col  # noqa: E402
import singcup_piku_devices as devices  # noqa: E402
import singcup_piku_scheduler as sched  # noqa: E402
import singcup_qualifiers as sq  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils  # noqa: E402
from singcup_piku import PikuError  # noqa: E402

FINAL_URL = "https://www.piku.co.kr/w/rank/2ut8Li"
FINAL_TITLE = ("이상형 월드컵 랭킹 - [2026 치지직 싱드컵 갤럭시] - 파이널 본선"
               " Ideal type worldcup PIKU")

_ORIG_DB_PATH = None


@pytest.fixture
def env(tmp_path):
    """운영 DB를 건드리지 않는 임시 DB(다른 piku 테스트와 같은 방식)."""
    global _ORIG_DB_PATH
    from database import db as dbmod
    _ORIG_DB_PATH = dbmod.DB_PATH
    db_file = tmp_path / f"final-{uuid.uuid4().hex}.db"
    loop = asyncio.new_event_loop()

    async def setup():
        import database
        from database import db as dbmod
        if dbmod._db is not None:
            await dbmod.close_db()
        dbmod.DB_PATH = str(db_file)
        dbmod._db = None
        await database.init_db()

    loop.run_until_complete(setup())
    yield loop

    async def teardown():
        from database import db as dbmod
        await dbmod.close_db()
        dbmod.DB_PATH = _ORIG_DB_PATH
        dbmod._db = None

    loop.run_until_complete(teardown())
    loop.close()


# ── 합성 본선 데이터 ────────────────────────────────────────────────────────
def _official_leads() -> list[tuple[str, bool]]:
    """공식 명단에서 빌린 32명: 솔로 20(여 10 · 남 10) + 그룹 12(팀 첫 멤버). 실측 구성과 같다."""
    out: list[tuple[str, bool]] = []
    out += [(r["name"], False) for r in sq.QUALIFIERS["female_solo"][:10]]
    out += [(r["name"], False) for r in sq.QUALIFIERS["male_solo"][:10]]
    for g in sq.QUALIFIERS["groups"][:12]:
        members = [m["name"] for m in g["members"]]
        # PIKU 문자열은 팀원 전체를 쉼표로 잇는다. 1인 팀이면 한 명 + 가짜 팀원.
        team = ", ".join(members if len(members) > 1 else members + ["테스트팀원"])
        out.append((team, True))
    return out


def final_rows(bump: float = 0.0) -> list[dict]:
    leads = _official_leads()
    assert len(leads) == 32
    return [{
        "rank": i + 1, "streamer": name, "song_title": f"곡{i + 1}", "artist": f"가수{i + 1}",
        "win_ratio": round(20.0 - i * 0.5 + bump, 2), "win_rate": round(70.0 - i * 0.9, 2),
        "image_url": f"https://img.example/f{i + 1}.png",
    } for i, (name, _team) in enumerate(leads)]


def final_payload(rows=None, **over) -> dict:
    body = {
        "schemaVersion": 1, "division": "final", "campaign": "final", "sourceId": "2ut8Li",
        "sourceUrl": FINAL_URL, "pageTitle": FINAL_TITLE,
        "collectedAt": "2026-09-12T03:00:00.000Z",
        "rows": rows if rows is not None else final_rows(),
    }
    body["rowCount"] = len(body["rows"])
    body.update(over)
    return body


def qual_payload(division="female_solo") -> dict:
    sid = col.SOURCE_IDS[division]
    n = col.EXPECTED_ROWS[division]
    return {
        "schemaVersion": 1, "division": division, "sourceId": sid,
        "sourceUrl": f"https://www.piku.co.kr/w/rank/{sid}", "collectedAt": "x",
        "rowCount": n,
        "rows": [{"rank": i + 1, "streamer": f"q{i}", "song_title": "t", "artist": "a",
                  "win_ratio": 1.0, "win_rate": 2.0, "image_url": ""} for i in range(n)],
    }


# ── 1) 레지스트리 · 마이그레이션 ────────────────────────────────────────────
def test_registry_is_the_single_source_of_truth():
    assert camps.CAMPAIGNS["qualifier"]["status"] == "frozen"
    assert camps.CAMPAIGNS["final"]["status"] == "active"
    assert camps.ACTIVE_CAMPAIGN == "final"
    assert camps.SOURCES["final"]["sourceId"] == "2ut8Li"
    assert camps.SOURCES["final"]["url"] == FINAL_URL
    assert camps.SOURCES["final"]["expected"] == 32
    assert camps.SOURCES["final"]["title"] == "[2026 치지직 싱드컵 갤럭시] - 파이널 본선"
    # source key는 campaign을 넘어 겹치지 않는다 — 기존 (division, …) 기본키의 전제.
    keys = list(camps.SOURCES)
    assert len(keys) == len(set(keys)) == 4
    assert "final" not in camps.QUALIFIER_SOURCES
    # 다른 모듈의 정본이 갈라지지 않았다.
    assert col.SOURCE_IDS == {k: v["sourceId"] for k, v in camps.SOURCES.items()}
    assert col.EXPECTED_ROWS["final"] == 32
    assert col.DIVISIONS == ("female_solo", "male_solo", "groups")
    assert piku.PUBLIC_SOURCES == ("female_solo", "male_solo", "groups", "final")


def test_plan_only_lists_the_active_campaign():
    p = camps.plan()
    assert p["campaign"] == "final"
    assert [s["key"] for s in p["sources"]] == ["final"]
    assert p["sources"][0]["paged"] is True
    assert "nonce" not in str(p) and "token" not in str(p)


def test_migration_adds_campaign_columns_and_is_repeatable(env):
    async def go():
        import database
        from database import get_db
        from database.db import _table_columns
        db = await get_db()
        for t in ("piku_sources", "piku_datasets", "piku_mappings", "piku_collector_state",
                  "piku_collector_teams", "piku_collector_tokens",
                  "piku_collector_challenges"):
            assert "campaign" in await _table_columns(db, t), t
        cols = await _table_columns(db, "piku_auto_runs")
        assert {"campaign", "scheduled_at"} <= cols
        assert await _table_columns(db, "piku_auto_run_sources") == {
            "run_id", "campaign", "source", "ok", "kind", "row_count"}
        # 기존 행은 기본값 qualifier를 받는다(append-only).
        await db.execute("INSERT INTO piku_mappings (division, piku_name, state, updated_at)"
                         " VALUES ('female_solo','x','unmapped',0)")
        await db.commit()
        cur = await db.execute("SELECT campaign FROM piku_mappings WHERE piku_name='x'")
        assert (await cur.fetchone())[0] == "qualifier"
        # 같은 DB에 다시 초기화해도(재배포·봇/백엔드 이중 기동) 실패하지 않는다.
        for _ in range(3):
            await database.init_db()
        assert "campaign" in await _table_columns(db, "piku_sources")
    env.run_until_complete(go())


# ── 2) 본선 payload 검증 ────────────────────────────────────────────────────
def test_final_payload_parses_mixed_solo_and_team_rows():
    parsed = col.parse_payload(final_payload())
    assert parsed["division"] == "final" and parsed["campaign"] == "final"
    assert len(parsed["rows"]) == 32
    teams = [r for r in parsed["rows"] if r["team_members"]]
    solos = [r for r in parsed["rows"] if not r["team_members"]]
    assert len(teams) == 12 and len(solos) == 20
    # 팀은 쉼표 첫 이름이 대표자, 원문은 team_members에 남는다.
    t = teams[0]
    assert t["name"] == t["team_members"].split(",")[0].strip()
    # 솔로 이름은 쪼개지 않는다.
    assert all("," not in r["name"] for r in solos)
    # 우승 비율 ↔ 승률 번역이 뒤바뀌지 않았다.
    assert parsed["rows"][0]["win_rate"] == 20.0 and parsed["rows"][0]["match_rate"] == 70.0


@pytest.mark.parametrize("patch,kind", [
    ({"sourceId": "8jGsHE"}, "bad_source"),
    ({"sourceUrl": "https://www.piku.co.kr/w/rank/8jGsHE"}, "bad_source"),
    ({"campaign": "qualifier"}, "bad_campaign"),
    ({"campaign": None}, "bad_campaign"),
    ({"pageTitle": "이상형 월드컵 랭킹 - 다른 월드컵"}, "bad_title"),
    ({"pageTitle": None}, "bad_title"),
    ({"rows": final_rows()[:31], "rowCount": 31}, "row_count"),
    ({"rows": final_rows() + [dict(final_rows()[0], rank=33, streamer="추가")],
      "rowCount": 33}, "row_count"),
])
def test_final_payload_fails_closed(patch, kind):
    body = final_payload()
    body.update(patch)
    with pytest.raises(PikuError) as ei:
        col.parse_payload(body)
    assert ei.value.kind == kind


def test_final_rank_gap_duplicate_and_missing_first():
    rows = final_rows()
    rows[16]["rank"] = 33          # 17위 빠지고 33위 생김
    with pytest.raises(PikuError) as ei:
        col.parse_payload(final_payload(rows))
    assert ei.value.kind == "rank_gap"
    rows = final_rows()
    rows[1]["rank"] = 1            # 1위 중복
    with pytest.raises(PikuError) as ei:
        col.parse_payload(final_payload(rows))
    assert ei.value.kind == "duplicate_rank"
    rows = [dict(r, rank=r["rank"] + 1) for r in final_rows()]   # 2~33, 1위 없음
    with pytest.raises(PikuError) as ei:
        col.parse_payload(final_payload(rows))
    assert ei.value.kind == "rank_gap"


def test_qualifier_payload_without_campaign_still_parses():
    """기존 확장(campaign 없음)이 보낸 예선 payload는 형식상 여전히 유효하다(동결은 별개)."""
    parsed = col.parse_payload(qual_payload("female_solo"))
    assert parsed["campaign"] == "qualifier"


# ── 3) 동결 — 예선은 어떤 쓰기도 받지 않는다 ────────────────────────────────
def test_frozen_qualifier_rejects_every_write_path(env):
    async def go():
        for coro, kind in [
            (col.save_draft(qual_payload("female_solo")), "campaign_frozen"),
            (col.issue_token("groups"), "campaign_frozen"),
            (col.publish_drafts("qualifier"), "campaign_frozen"),
            (col.import_manual({"division": "male_solo", "rows": []}), "campaign_frozen"),
            (col.set_mapping("female_solo", "x", None), "campaign_frozen"),
            (col.confirm_exact("groups"), "campaign_frozen"),
            (piku.set_mapping("female_solo", "x", None, state="unmapped"), "campaign_frozen"),
        ]:
            with pytest.raises(PikuError) as ei:
                await coro
            assert ei.value.kind == kind, kind
        # 읽기는 된다 — 화면이 예선 상태를 보여 줘야 한다.
        st = await col.status("qualifier")
        assert st["frozen"] is True and st["campaign"] == "qualifier"
        assert st["publishReady"] is False
        assert any("동결" in b for b in st["blockers"])
        assert set(st["divisions"]) == {"female_solo", "male_solo", "groups"}
    env.run_until_complete(go())


def test_frozen_qualifier_active_dataset_is_untouched_by_final_publish(env):
    """예선 활성본이 있는 상태에서 본선을 공개해도 예선은 한 행도 바뀌지 않는다."""
    async def go():
        from database import get_db
        db = await get_db()
        # 예선 활성본을 직접 심는다(동결 전 상태를 흉내 낸다 — 운영 DB에 이미 있는 것).
        snap = camps._set_for_tests(active="qualifier", statuses={"qualifier": "active"})
        try:
            for d in col.DIVISIONS:
                await col.save_draft(qual_payload(d))
                await col.confirm_exact(d)
            # 정확 일치가 없어 unmapped인 행은 mapping을 직접 확정해 둔다(테스트 편의).
            for d in col.DIVISIONS:
                m = await col.draft_mappings(d)
                ids = list(col._official_names_by_channel(d))
                for i, r in enumerate(m["rows"]):
                    if r["state"] != "confirmed":
                        await col._write_mapping(d, r["pikuName"], ids[i], "confirmed")
                await db.commit()
            await col.publish_drafts("qualifier")
        finally:
            camps._restore_for_tests(snap)
        before = {}
        for d in col.DIVISIONS:
            ds = await piku.active_dataset(d)
            cur = await db.execute("SELECT count(*), sum(source_rank) FROM piku_entries"
                                   " WHERE dataset_id=?", (ds["id"],))
            before[d] = (ds["id"], tuple(await cur.fetchone()))
        # 본선 draft → 매핑 → 공개
        await col.save_draft(final_payload())
        await col.confirm_exact("final")
        r = await col.publish_drafts("final")
        assert r["published"] is True and r["campaign"] == "final"
        for d in col.DIVISIONS:
            ds = await piku.active_dataset(d)
            cur = await db.execute("SELECT count(*), sum(source_rank) FROM piku_entries"
                                   " WHERE dataset_id=?", (ds["id"],))
            assert (ds["id"], tuple(await cur.fetchone())) == before[d], d
        # 예선 공개 API도 그대로다.
        q = await piku.public_ranking("female_solo")
        assert q["available"] is True and q["campaign"] == "qualifier" and len(q["entries"]) == 64
    env.run_until_complete(go())


# ── 4) 본선 draft → 매핑 → 원자 공개 → 공개 API ────────────────────────────
def test_final_draft_maps_all_32_by_exact_match_and_publishes(env):
    async def go():
        st0 = await col.status()          # 기본 = 활성 campaign(본선)
        assert st0["campaign"] == "final" and st0["frozen"] is False
        assert list(st0["divisions"]) == ["final"]
        assert st0["divisions"]["final"]["sourceId"] == "2ut8Li"

        r = await col.save_draft(final_payload())
        assert r["campaign"] == "final" and r["published"] is False and r["rowCount"] == 32
        # 공개 전에는 공개 API에 아무것도 없다.
        pub = await piku.public_ranking("final")
        assert pub["available"] is False and pub["entries"] == []

        m = await col.draft_mappings("final")
        assert m["expected"] == 32 and len(m["rows"]) == 32
        # 세 부문 전체가 후보라 32명 전원이 정확 일치 제안이다.
        assert m["counts"]["suggested"] == 32 and m["counts"]["unmatched"] == 0
        # 후보 목록에도 세 부문 전원이 있다.
        cands = await col.official_candidates("final")
        assert len(cands) == len(col._official_names_by_channel("final")) > 200

        blockers = await col.publish_blockers("final")
        assert blockers and "미확정 32건" in blockers[0]
        with pytest.raises(PikuError) as ei:
            await col.publish_drafts("final")
        assert ei.value.kind == "unconfirmed"

        c = await col.confirm_exact("final")
        assert c["confirmed"] == 32
        assert await col.publish_blockers("final") == []
        pv = await col.publish_preview("final")
        assert pv["campaign"] == "final" and pv["publishReady"] is True
        assert pv["divisions"]["final"]["confirmed"] == 32

        r = await col.publish_drafts("final")
        assert r == {"published": True, "campaign": "final", "rows": {"final": 32}}

        pub = await piku.public_ranking("final")
        assert pub["available"] is True and pub["campaign"] == "final"
        assert pub["sourceUrl"] == FINAL_URL and pub["lastSuccessAt"] > 0
        assert [e["rank"] for e in pub["entries"]] == list(range(1, 33))
        assert pub["entries"][0]["rank"] == 1 and pub["entries"][0]["sourceRank"] == 1
        teams = [e for e in pub["entries"] if e["teamMembers"]]
        assert len(teams) == 12
        # 공개 응답에 비율·승률이 없다.
        blob = str(pub)
        assert "win_rate" not in blob and "match_rate" not in blob and "20.0" not in blob
        # 모든 항목이 공식 channel_id에 연결됐다.
        official = set(sq.ALL_CHANNEL_IDS)
        assert all(e["channelId"] in official for e in pub["entries"])
        # 곡·가수가 살아 있다.
        first = pub["entries"][0]
        assert first["songTitle"] == "곡1" and first["artistName"] == "가수1"
    env.run_until_complete(go())


def test_final_publish_replaces_previous_final_only(env):
    async def go():
        await col.save_draft(final_payload())
        await col.confirm_exact("final")
        await col.publish_drafts("final")
        first = await piku.active_dataset("final")
        # 두 번째 수집(순위 변동) → 공개 → 이전 활성본은 superseded, 예선은 무관.
        rows = final_rows(bump=5.0)
        # 1·2위를 맞바꾼다 — 우리 순위는 우승 비율로 다시 매기므로 비율도 함께 바꾼다.
        r0, r1 = rows[0], rows[1]
        rows[0] = dict(r1, rank=1, win_ratio=r0["win_ratio"])
        rows[1] = dict(r0, rank=2, win_ratio=r1["win_ratio"])
        await col.save_draft(final_payload(rows))
        await col.publish_drafts("final")
        second = await piku.active_dataset("final")
        assert second["id"] != first["id"]
        from database import get_db
        db = await get_db()
        cur = await db.execute("SELECT status FROM piku_datasets WHERE id=?", (first["id"],))
        assert (await cur.fetchone())[0] == "superseded"
        cur = await db.execute("SELECT count(*) FROM piku_datasets WHERE division IN "
                               "('female_solo','male_solo','groups')")
        assert (await cur.fetchone())[0] == 0
        pub = await piku.public_ranking("final")
        assert pub["entries"][0]["name"] == rows[0]["streamer"].split(",")[0].strip()
    env.run_until_complete(go())


def test_final_sort_recomputes_from_rank_one(env):
    async def go():
        await col.save_draft(final_payload())
        await col.confirm_exact("final")
        await col.publish_drafts("final")
        a = await piku.public_ranking("final", sort="primary")
        b = await piku.public_ranking("final", sort="secondary")
        assert [e["rank"] for e in a["entries"]] == list(range(1, 33))
        assert [e["rank"] for e in b["entries"]] == list(range(1, 33))
        assert a["sort"] == "primary" and b["sort"] == "secondary"
    env.run_until_complete(go())


def test_campaign_status_reports_both_stages(env):
    async def go():
        cs = {c["campaign"]: c for c in await piku.campaign_status()}
        assert cs["qualifier"]["status"] == "frozen"
        assert cs["final"]["status"] == "active"
        assert cs["final"]["collectionStartAt"].startswith("2026-09-10")
        assert cs["final"]["scheduleSource"] == "user_provided"
        assert cs["final"]["available"] is False and cs["final"]["lastPublishedAt"] == 0
        await col.save_draft(final_payload())
        cs = {c["campaign"]: c for c in await piku.campaign_status()}
        assert cs["final"]["lastCollectedAt"] > 0 and cs["final"]["lastPublishedAt"] == 0
        await col.confirm_exact("final")
        await col.publish_drafts("final")
        cs = {c["campaign"]: c for c in await piku.campaign_status()}
        assert cs["final"]["available"] is True and cs["final"]["lastPublishedAt"] > 0
        assert cs["final"]["entryCount"] == 32
        # 공개 status 응답에도 실린다(기존 divisions 형태는 그대로).
        ps = await piku.public_status()
        assert set(ps["divisions"]) == {"female_solo", "male_solo", "groups"}
        assert {c["campaign"] for c in ps["campaigns"]} == {"qualifier", "final"}
    env.run_until_complete(go())


# ── 5) 공개 라우트 ───────────────────────────────────────────────────────────
def test_public_ranking_route_separates_campaigns(env):
    from fastapi import HTTPException
    from routers.singcup_router import piku_campaigns, piku_ranking

    async def go():
        await col.save_draft(final_payload())
        await col.confirm_exact("final")
        await col.publish_drafts("final")
        legacy = await piku_ranking()
        assert set(legacy["divisions"]) == {"female_solo", "male_solo", "groups"}
        assert legacy["campaign"] == "qualifier"
        assert "final" not in legacy["divisions"], "본선이 예선 응답에 섞였다"
        fin = await piku_ranking(campaign="final")
        assert list(fin["divisions"]) == ["final"] and fin["campaign"] == "final"
        assert len(fin["divisions"]["final"]["entries"]) == 32
        with pytest.raises(HTTPException):
            await piku_ranking(campaign="nope")
        cs = await piku_campaigns()
        assert {c["campaign"] for c in cs["campaigns"]} == {"qualifier", "final"}
    env.run_until_complete(go())


# ── 6) 인증 — 토큰·challenge가 campaign에 묶인다 ─────────────────────────────
class FakeDevice:
    def __init__(self) -> None:
        self._key = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_b64(self) -> str:
        spki = self._key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(spki).decode()

    def sign(self, message: str) -> str:
        der = self._key.sign(message.encode(), ec.ECDSA(hashes.SHA256()))
        r, s = asym_utils.decode_dss_signature(der)
        return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode()


async def _register():
    started = await devices.register_start("본선 PC")
    dev = FakeDevice()
    done = await devices.register_finish(started["pairingCode"], dev.public_b64)
    return done["deviceId"], dev, done["fingerprint"]


def test_final_token_is_bound_to_campaign_and_source(env):
    async def go():
        t = await col.issue_token("final")
        assert t["campaign"] == "final" and t["division"] == "final"
        # 본선 토큰으로 예선 source ingest 불가(부문·campaign 둘 다 다르다).
        with pytest.raises(PikuError):
            await col.consume_token(t["token"], "groups")
        # 아직 안 쓰였으므로 본선에는 통과한다 — 그리고 1회용이다.
        await col.consume_token(t["token"], "final")
        with pytest.raises(PikuError):
            await col.consume_token(t["token"], "final")
        # 예선 토큰은 발급 자체가 막힌다(동결).
        with pytest.raises(PikuError) as ei:
            await col.issue_token("female_solo")
        assert ei.value.kind == "campaign_frozen"
    env.run_until_complete(go())


def test_challenge_message_binds_campaign_for_final(env):
    async def go():
        device_id, dev, fp = await _register()
        await devices.set_mode("AUTO_COLLECT")
        c = await devices.challenge_issue(device_id, "final", automation=True, protocol=2)
        assert c["campaign"] == "final"
        assert c["message"].startswith("nexbot-piku-collector-v2|")
        assert "|final|final|" in c["message"]
        # 서명 대상은 서버가 다시 만든다 — campaign을 바꿔치기한 문자열은 검증 실패.
        forged = c["message"].replace("|final|final|", "|qualifier|final|")
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], dev.sign(forged))
        # challenge는 실패해도 소비된다(nonce 1회).
        with pytest.raises(devices.DeviceError):
            await devices.challenge_redeem(c["challengeId"], dev.sign(c["message"]))
        # 정상 경로: 새 challenge → 서명 → 본선 토큰.
        c2 = await devices.challenge_issue(device_id, "final", automation=True, protocol=2)
        t = await devices.challenge_redeem(c2["challengeId"], dev.sign(c2["message"]))
        assert t["division"] == "final" and t["campaign"] == "final"
        # 예선 challenge는 동결이라 발급되지 않는다 — 기존 등록 장치도 예선을 못 만진다.
        with pytest.raises(devices.DeviceError) as ei:
            await devices.challenge_issue(device_id, "groups", automation=True)
        assert ei.value.code == "campaign_frozen"
        # 예선 v1 서명 형식은 코드에 그대로 남아 있다(기존 계약 보존).
        assert devices.challenge_message("c", "n", "groups", 1).startswith(
            "nexbot-piku-collector-v1|c|n|groups|1")
    env.run_until_complete(go())


def test_device_state_carries_the_plan(env):
    async def go():
        _, _, fp = await _register()
        st = await sched.device_state(fp)
        assert st["deviceActive"] is True
        assert st["plan"]["campaign"] == "final"
        assert [s["key"] for s in st["plan"]["sources"]] == ["final"]
        assert st["plan"]["sources"][0]["sourceId"] == "2ut8Li"
        # 모르는 지문도 plan은 준다(비밀이 아니다) — 장치는 비활성.
        st2 = await sched.device_state("nope")
        assert st2["deviceActive"] is False and st2["plan"]["campaign"] == "final"
    env.run_until_complete(go())


# ── 7) 회차 보고 ────────────────────────────────────────────────────────────
def test_run_report_records_sources_and_outcome(env):
    async def go():
        device_id, _, fp = await _register()
        body = {"trigger": "alarm", "campaign": "final", "scheduledAt": 1_789_000_000_000,
                "startedAt": 1_789_000_003_000, "finishedAt": 1_789_000_020_000,
                "sources": {"final": {"ok": True, "kind": "sent", "rows": 32}}}
        # 서버 시각보다 과거여야 받는다 — 위 값은 2026-09-11경이다.
        out = await sched.report_run(fp, body)
        assert out["outcome"] == "success" and out["campaign"] == "final"
        assert out["sources"] == {"final": {"campaign": "final", "ok": True, "kind": "sent",
                                            "rows": 32}}
        assert out["scheduledAt"] == 1_789_000_000 and out["startedAt"] == 1_789_000_003
        assert out["finishedAt"] == 1_789_000_020
        st = await sched.status()
        assert st["lastRun"]["id"] == out["id"] and st["plan"]["campaign"] == "final"
        devs = await devices.list_devices()
        assert devs[0]["lastSuccessAt"] > 0

        # unchanged → outcome unchanged (성공과 구분)
        out = await sched.report_run(fp, dict(body, sources={
            "final": {"ok": True, "kind": "unchanged", "rows": 32}}))
        assert out["outcome"] == "unchanged"
        # 실패 → failed, 장치 실패 기록
        out = await sched.report_run(fp, dict(body, sources={
            "final": {"ok": False, "kind": "no_tab", "rows": 0}}))
        assert out["outcome"] == "failed"
        devs = await devices.list_devices()
        assert devs[0]["lastFailureKind"] == "no_tab"
        # 모르는 종류·데이터 필드는 접힌다(HTML·토큰이 종류 자리에 실리지 않게).
        out = await sched.report_run(fp, dict(body, sources={
            "final": {"ok": False, "kind": "<html>secret</html>", "rows": 0, "html": "x"}}))
        assert out["sources"]["final"]["kind"] == "other"
        # 예선 source(동결·plan 밖)는 무시되고, plan 기대치를 못 채워 failed다.
        out = await sched.report_run(fp, dict(body, sources={
            "groups": {"ok": True, "kind": "sent", "rows": 32}}))
        assert out["outcome"] == "failed" and out["sources"] == {}
    env.run_until_complete(go())


def test_run_report_requires_active_device_and_is_rate_limited(env):
    async def go():
        body = {"trigger": "alarm", "campaign": "final",
                "sources": {"final": {"ok": True, "kind": "sent", "rows": 32}}}
        with pytest.raises(devices.DeviceError) as ei:
            await sched.report_run("unknown-fingerprint", body)
        assert ei.value.code == "device_not_active"
        device_id, _, fp = await _register()
        for bad, code in [(dict(body, trigger="cron"), "bad_trigger"),
                          (dict(body, campaign="nope"), "bad_campaign"),
                          (dict(body, sources={}), "bad_report"),
                          ("nope", "bad_report")]:
            with pytest.raises(devices.DeviceError) as ei:
                await sched.report_run(fp, bad)
            assert ei.value.code == code
        for _ in range(sched.RUN_REPORT_WINDOW_LIMIT):
            await sched.report_run(fp, body)
        with pytest.raises(devices.DeviceError) as ei:
            await sched.report_run(fp, body)
        assert ei.value.code == "rate_limited"
        await devices.revoke(device_id)
        with pytest.raises(devices.DeviceError):
            await sched.report_run(fp, body)
    env.run_until_complete(go())


def test_client_failure_accepts_final_source(env):
    async def go():
        r = await col.record_client_failure("final", "title_mismatch")
        assert r["lastResult"] == "failed"
        st = await col.status("final")
        assert st["divisions"]["final"]["lastErrorKind"] == "title_mismatch"
    env.run_until_complete(go())


def test_admin_routes_default_to_qualifier_for_legacy_clients(env):
    """관리 라우트가 campaign 인자를 받고, **생략하면 예선**이다(구 프론트 호환).

    단계적 배포에서 구 프론트(예선 3부문만 앎)가 campaign 없이 부르면 예선 상태·
    미리보기를 받고, 공개 버튼은 동결이라 거절된다 — 구 프론트가 본선을 건드릴 수 없다.
    """
    from routers.admin_router import (
        piku_collector_campaigns,
        piku_collector_publish,
        piku_collector_publish_preview,
        piku_collector_status,
    )

    async def go():
        st = await piku_collector_status(None, user={"id": 1})
        assert st["campaign"] == "qualifier" and st["frozen"] is True
        assert set(st["divisions"]) == {"female_solo", "male_solo", "groups"}
        st = await piku_collector_status("final", user={"id": 1})
        assert st["campaign"] == "final" and st["frozen"] is False
        pv = await piku_collector_publish_preview(None, user={"id": 1})
        assert pv["campaign"] == "qualifier"
        cs = await piku_collector_campaigns(user={"id": 1})
        assert {c["campaign"] for c in cs["campaigns"]} == {"qualifier", "final"}
        from fastapi import HTTPException
        # 구 프론트의 "세 부문 함께 공개"(본문 없음) → 예선 동결로 거절, 본선에 닿지 않는다.
        with pytest.raises(HTTPException) as ei:
            await piku_collector_publish(None, user={"id": 1})
        assert "[campaign_frozen]" in str(ei.value.detail)
        from routers.admin_router import CollectorPublishBody
        with pytest.raises(HTTPException) as ei:
            await piku_collector_publish(CollectorPublishBody(campaign="final"), user={"id": 1})
        assert "[missing_draft]" in str(ei.value.detail)
    env.run_until_complete(go())
