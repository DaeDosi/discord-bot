"""PIKU 이름 매핑 확정 · Publish 게이트 · 수동 import 안전성.

**왜 이 파일이 따로 있는가.** `sync_mappings`는 정확 일치도 `suggested`까지만
만들고 `public_ranking`은 `confirmed`만 쓴다. 그래서 Publish를 해도 공개 순위가
비어 있었다 — Collector가 데이터를 잘 받아 와도 화면에는 아무것도 안 나오는,
가장 큰 차단 조건이었다. 여기서 그 확정 흐름의 계약을 고정한다.

실제 PIKU를 호출하지 않는다. 전부 합성 데이터다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

import singcup_piku as piku  # noqa: E402
import singcup_qualifiers as sq  # noqa: E402
from singcup_piku import PikuError  # noqa: E402

collector = pytest.importorskip(
    "singcup_piku_collector", reason="Collector 모듈이 아직 없다")

# 같은 합성 헬퍼를 쓴다 — 두 벌로 두면 계약이 갈라진다.
# `env`는 pytest fixture라 이름을 그대로 받아야 주입된다(F811은 그 관례 때문에
# 뜨는 것이므로 파일 단위로 끈다 — 실제 재정의는 없다).
# ruff: noqa: F811
from test_piku_collector import COUNTS, env, payload, raw_row  # noqa: E402,F401


def official_names(division: str) -> list[str]:
    """공식 명단의 이름. 그룹은 **첫 멤버**(=대표자 후보)를 쓴다."""
    if division == "groups":
        return [g["members"][0]["name"] for g in sq.QUALIFIERS["groups"]]
    return [r["name"] for r in sq.QUALIFIERS[division]]


def official_channel(division: str, i: int) -> str:
    if division == "groups":
        return sq.QUALIFIERS["groups"][i]["members"][0]["channelId"]
    return sq.QUALIFIERS[division][i]["channelId"]


def matching_payload(division: str) -> dict:
    """공식 명단 이름을 그대로 쓴 페이로드 — 전부 정확 일치가 된다."""
    names = official_names(division)
    assert len(names) == COUNTS[division], f"{division} 공식 명단 수가 다르다"
    rows = []
    for i, n in enumerate(names, start=1):
        streamer = f"{n}, 팀원{i}B" if division == "groups" else n
        rows.append(raw_row(i, streamer=streamer))
    return payload(division, rows=rows)


# ── 매핑 목록 ───────────────────────────────────────────────────────────────
def test_draft_mappings_list_shape(env):
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    assert m["division"] == "female_solo"
    assert m["expected"] == 64
    assert len(m["rows"]) == 64
    r = m["rows"][0]
    for k in ("rank", "pikuName", "teamMembers", "lead", "songTitle",
              "artistName", "state", "channelId", "officialName"):
        assert k in r, f"{k}가 없다"
    # 비율은 담지 않는다 — 관리 목록에서도 값은 쓰지 않는다.
    for bad in ("winRate", "matchRate", "win_rate", "match_rate"):
        assert bad not in r, f"매핑 목록에 내부 비율이 있다: {bad}"


def test_exact_matches_become_suggested_not_confirmed(env):
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    assert m["counts"]["suggested"] == 64
    assert m["counts"]["confirmed"] == 0, "자동 확정하면 안 된다"


def test_group_mapping_uses_lead_not_team_string(env):
    env.run_until_complete(collector.save_draft(matching_payload("groups")))
    m = env.run_until_complete(collector.draft_mappings("groups"))
    # 팀 문자열이 아니라 대표자로 매칭돼야 32건이 붙는다.
    assert m["counts"]["suggested"] == 32, m["counts"]
    r = m["rows"][0]
    assert "," in r["teamMembers"], "전체 팀 문자열을 보존해야 한다"
    assert "," not in r["lead"], "대표자는 한 사람이어야 한다"
    assert r["pikuName"] == r["lead"], "매핑 키는 대표자다"


def test_unmatched_rows_are_reported(env):
    rows = [raw_row(i, streamer=f"없는사람{i}") for i in range(1, 65)]
    env.run_until_complete(collector.save_draft(payload("female_solo", rows=rows)))
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    assert m["counts"]["unmatched"] == 64
    assert m["counts"]["suggested"] == 0


def test_solo_names_with_comma_are_not_split(env):
    names = official_names("female_solo")
    rows = [raw_row(i, streamer=n) for i, n in enumerate(names, start=1)]
    rows[0]["streamer"] = "이름, 별명"
    env.run_until_complete(collector.save_draft(payload("female_solo", rows=rows)))
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    row = m["rows"][0]
    assert row["pikuName"] == "이름, 별명"
    assert row["teamMembers"] == "", "솔로에는 팀 문자열이 없다"


# ── 일괄 확정 ───────────────────────────────────────────────────────────────
def test_confirm_exact_confirms_only_suggested(env):
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    r = env.run_until_complete(collector.confirm_exact("female_solo"))
    assert r["confirmed"] == 64
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    assert m["counts"]["confirmed"] == 64
    assert m["counts"]["suggested"] == 0


def test_confirm_exact_does_not_touch_unmatched(env):
    names = official_names("female_solo")
    rows = [raw_row(i, streamer=n) for i, n in enumerate(names, start=1)]
    rows[0]["streamer"] = "존재하지않는이름"
    env.run_until_complete(collector.save_draft(payload("female_solo", rows=rows)))
    r = env.run_until_complete(collector.confirm_exact("female_solo"))
    assert r["confirmed"] == 63
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    assert m["counts"]["unmatched"] == 1


def test_no_fuzzy_auto_confirm(env):
    """한 글자만 달라도 자동 확정하지 않는다."""
    names = official_names("female_solo")
    rows = [raw_row(i, streamer=n) for i, n in enumerate(names, start=1)]
    rows[0]["streamer"] = names[0] + "x"
    env.run_until_complete(collector.save_draft(payload("female_solo", rows=rows)))
    env.run_until_complete(collector.confirm_exact("female_solo"))
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    assert m["counts"]["confirmed"] == 63
    assert m["counts"]["unmatched"] == 1


def test_source_has_no_fuzzy_matching():
    import inspect
    src = inspect.getsource(collector).lower()
    for bad in ("difflib", "sequencematcher", "levenshtein", "rapidfuzz"):
        assert bad not in src, f"유사도 매칭 흔적: {bad}"


def test_confirm_exact_rolls_back_on_failure(env, monkeypatch):
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    real = collector._write_mapping
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 10:
            raise RuntimeError("boom")
        return await real(*a, **kw)

    monkeypatch.setattr(collector, "_write_mapping", flaky)
    with pytest.raises(Exception):
        env.run_until_complete(collector.confirm_exact("female_solo"))
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    assert m["counts"]["confirmed"] == 0, "일부만 확정된 상태로 남으면 안 된다"


# ── 개별 매핑 · 중복 ────────────────────────────────────────────────────────
def test_manual_mapping_sets_confirmed(env):
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    names = official_names("female_solo")
    cid = official_channel("female_solo", 5)
    env.run_until_complete(collector.set_mapping("female_solo", names[0], cid))
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    row = next(r for r in m["rows"] if r["pikuName"] == names[0])
    assert row["state"] == "confirmed"
    assert row["channelId"] == cid


def test_duplicate_official_mapping_is_rejected(env):
    """같은 공식 참가자에게 두 PIKU 행을 붙일 수 없다."""
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    env.run_until_complete(collector.confirm_exact("female_solo"))
    names = official_names("female_solo")
    dup = official_channel("female_solo", 0)
    with pytest.raises(PikuError) as e:
        env.run_until_complete(collector.set_mapping("female_solo", names[1], dup))
    assert e.value.kind == "duplicate_channel"


def test_mapping_to_other_division_is_rejected(env):
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    names = official_names("female_solo")
    other = official_channel("male_solo", 0)
    with pytest.raises(PikuError) as e:
        env.run_until_complete(collector.set_mapping("female_solo", names[0], other))
    assert e.value.kind in ("not_qualifier", "bad_channel")


def test_clearing_mapping_returns_to_unmapped(env):
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    env.run_until_complete(collector.confirm_exact("female_solo"))
    names = official_names("female_solo")
    env.run_until_complete(collector.set_mapping("female_solo", names[0], None))
    m = env.run_until_complete(collector.draft_mappings("female_solo"))
    row = next(r for r in m["rows"] if r["pikuName"] == names[0])
    assert row["state"] == "unmapped"
    assert row["channelId"] is None


# ── Publish 게이트 ──────────────────────────────────────────────────────────
def test_publish_blocked_when_mappings_unconfirmed(env):
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(matching_payload(d)))
    st = env.run_until_complete(collector.status())
    assert st["publishReady"] is False
    assert any("확정" in b for b in st["blockers"]), st["blockers"]
    with pytest.raises(PikuError) as e:
        env.run_until_complete(collector.publish_drafts())
    assert e.value.kind == "unconfirmed"


def test_publish_ready_after_all_confirmed(env):
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(matching_payload(d)))
        env.run_until_complete(collector.confirm_exact(d))
    st = env.run_until_complete(collector.status())
    assert st["publishReady"] is True, st["blockers"]
    assert st["blockers"] == []
    env.run_until_complete(collector.publish_drafts())
    for d in COUNTS:
        pub = env.run_until_complete(piku.public_ranking(d))
        assert pub["available"] is True
        assert len(pub["entries"]) == COUNTS[d], f"{d} 공개 순위가 비었다"


def test_blockers_name_the_missing_division(env):
    env.run_until_complete(collector.save_draft(matching_payload("female_solo")))
    st = env.run_until_complete(collector.status())
    joined = " ".join(st["blockers"])
    assert "남성" in joined and "그룹" in joined


def test_published_response_has_no_internal_ratios(env):
    import json
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(matching_payload(d)))
        env.run_until_complete(collector.confirm_exact(d))
    env.run_until_complete(collector.publish_drafts())
    for d in COUNTS:
        blob = json.dumps(env.run_until_complete(piku.public_ranking(d)),
                          ensure_ascii=False)
        for bad in ("win_rate", "match_rate", "winRate", "matchRate", "winRatio"):
            assert bad not in blob, f"{d} 공개 응답에 내부 비율이 있다: {bad}"


# ── Publish 전 Preview ──────────────────────────────────────────────────────
def test_publish_preview_compares_with_active(env):
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(matching_payload(d)))
        env.run_until_complete(collector.confirm_exact(d))
    env.run_until_complete(collector.publish_drafts())
    # 두 번째 수집 — 순위를 뒤집어 변경을 만든다.
    names = official_names("female_solo")
    rows = [raw_row(i, streamer=n) for i, n in enumerate(reversed(names), start=1)]
    env.run_until_complete(collector.save_draft(payload("female_solo", rows=rows)))
    env.run_until_complete(collector.confirm_exact("female_solo"))
    pv = env.run_until_complete(collector.publish_preview())
    f = pv["divisions"]["female_solo"]
    assert f["draftRows"] == 64
    assert f["activeRows"] == 64
    assert f["changed"] > 0, "순위 변경이 잡혀야 한다"
    assert f["added"] == 0 and f["removed"] == 0
    assert "sortLabel" in pv, "내부 정렬 기준을 밝힌다"


def test_publish_preview_does_not_write(env):
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(matching_payload(d)))
    before = env.run_until_complete(collector.debug_counts())
    env.run_until_complete(collector.publish_preview())
    assert env.run_until_complete(collector.debug_counts()) == before


def test_publish_preview_reports_unconfirmed(env):
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(matching_payload(d)))
    pv = env.run_until_complete(collector.publish_preview())
    assert pv["divisions"]["female_solo"]["unconfirmed"] == 64


# ── 수동 import는 draft까지만 ──────────────────────────────────────────────
def test_manual_import_goes_to_draft_only(env):
    body = {"division": "female_solo",
            "rows": [{"name": n, "winRate": 10.0, "matchRate": 20.0,
                      "source_rank": i}
                     for i, n in enumerate(official_names("female_solo"), start=1)]}
    env.run_until_complete(collector.import_manual(body))
    pub = env.run_until_complete(piku.public_ranking("female_solo"))
    assert pub["available"] is False, "수동 import가 곧바로 공개됐다"
    st = env.run_until_complete(collector.status())
    assert st["divisions"]["female_solo"]["draftRows"] == 64


def test_manual_import_keeps_existing_active(env):
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(matching_payload(d)))
        env.run_until_complete(collector.confirm_exact(d))
    env.run_until_complete(collector.publish_drafts())
    before = env.run_until_complete(piku.active_dataset("female_solo"))
    body = {"division": "female_solo",
            "rows": [{"name": f"새이름{i}", "winRate": 1.0, "matchRate": 2.0,
                      "source_rank": i} for i in range(1, 65)]}
    env.run_until_complete(collector.import_manual(body))
    after = env.run_until_complete(piku.active_dataset("female_solo"))
    assert before["id"] == after["id"], "import가 활성본을 갈아 치웠다"


def test_manual_import_rejects_wrong_count(env):
    body = {"division": "female_solo",
            "rows": [{"name": f"n{i}", "winRate": 1.0, "matchRate": 2.0,
                      "source_rank": i} for i in range(1, 10)]}
    with pytest.raises(PikuError):
        env.run_until_complete(collector.import_manual(body))


def test_admin_router_has_no_immediate_activate_route():
    """PIKU 관리 화면에서 한 부문만 즉시 공개하는 경로를 노출하지 않는다."""
    import inspect

    from routers import admin_router as ar
    src = inspect.getsource(ar)
    for route in ('"/piku/collect"', '"/piku/collect-all"', '"/piku/import"'):
        assert route not in src, f"즉시 활성화 경로가 남아 있다: {route}"


# ── 19. 대표자는 PIKU 첫 이름이다 (공식 순서와 반대여도) ────────────────────
#
# **감사에서 드러난 결함의 회귀 테스트.** 매칭 인덱스가 공식 명단의 첫 멤버만
# 담고 있으면, 공식 `order`와 PIKU 표기 순서가 서로 반대인 팀은 대표자를 찾아도
# 연결할 곳이 없어 전부 미매칭이 된다. 대표자는 **PIKU 문자열의 첫 이름**이고,
# 그 이름을 공식 명단 **전체**에서 찾아 연결해야 한다.

def reversed_payload(division: str = "groups") -> dict:
    """공식 순서를 **뒤집어** 만든 PIKU 페이로드.

    PIKU streamer = "마지막멤버, ..., 첫멤버" 이므로 대표자는 공식 첫 멤버가
    아니라 **공식 마지막 멤버**가 된다.
    """
    rows = []
    for i, g in enumerate(sq.QUALIFIERS["groups"], start=1):
        members = [m["name"] for m in g["members"]]
        rows.append(raw_row(i, streamer=", ".join(reversed(members))))
    return payload(division, rows=rows)


def test_group_lead_is_piku_first_name_even_when_official_order_differs(env):
    """공식 순서와 반대여도 PIKU 첫 이름이 대표자로 선택된다."""
    body = reversed_payload()
    parsed = collector.parse_payload(body)
    for i, g in enumerate(sq.QUALIFIERS["groups"]):
        expected_lead = g["members"][-1]["name"]        # 뒤집었으므로 마지막
        assert parsed["rows"][i]["name"] == expected_lead, (
            f"{i}번 팀 대표자가 PIKU 첫 이름이 아니다")
        # 전체 팀 문자열은 그대로 보존된다.
        assert parsed["rows"][i]["team_members"].startswith(expected_lead)


def test_reversed_order_groups_still_match_officially(env):
    """대표자가 공식 첫 멤버가 아니어도 **공식 명단에서 찾아** 연결된다.

    이 테스트가 감사 전 코드에서는 실패한다 — 인덱스가 첫 멤버만 담았기 때문이다.
    """
    env.run_until_complete(collector.save_draft(reversed_payload()))
    m = env.run_until_complete(collector.draft_mappings("groups"))
    assert m["counts"]["unmatched"] == 0, (
        f"공식 순서가 반대인 팀이 매칭되지 않았다: {m['counts']}")
    assert m["counts"]["suggested"] == 32


def test_reversed_order_groups_link_to_the_lead_person(env):
    """연결 대상은 **대표자 본인의 채널**이어야 한다(팀의 첫 멤버가 아니다)."""
    env.run_until_complete(collector.save_draft(reversed_payload()))
    m = env.run_until_complete(collector.draft_mappings("groups"))
    by_name = {r["pikuName"]: r for r in m["rows"]}
    for g in sq.QUALIFIERS["groups"]:
        lead = g["members"][-1]
        row = by_name.get(lead["name"])
        assert row is not None, f"{lead['name']} 행이 없다"
        assert row["channelId"] == lead["channelId"], (
            f"{lead['name']}이(가) 다른 사람 채널에 연결됐다")


def test_reversed_order_groups_can_be_confirmed_and_published(env):
    """확정 → 공개까지 이어진다(공개 순위가 32건 나온다)."""
    for d in ("female_solo", "male_solo"):
        env.run_until_complete(collector.save_draft(matching_payload(d)))
        env.run_until_complete(collector.confirm_exact(d))
    env.run_until_complete(collector.save_draft(reversed_payload()))
    r = env.run_until_complete(collector.confirm_exact("groups"))
    assert r["confirmed"] == 32
    env.run_until_complete(collector.publish_drafts())
    pub = env.run_until_complete(piku.public_ranking("groups"))
    assert pub["available"] is True
    assert len(pub["entries"]) == 32


def test_group_candidates_include_every_member(env):
    """후보 목록에 팀의 **모든 멤버**가 있어야 운영자가 대표자를 고를 수 있다."""
    cands = env.run_until_complete(collector.official_candidates("groups"))
    total = sum(len(g["members"]) for g in sq.QUALIFIERS["groups"])
    assert len(cands) == total, f"후보가 {len(cands)}건뿐이다(전체 {total})"
    names = {c["name"] for c in cands}
    for g in sq.QUALIFIERS["groups"]:
        for m in g["members"]:
            assert m["name"] in names, f"{m['name']}이(가) 후보에 없다"


def test_official_index_does_not_use_member_order(env):
    """공식 `order`로 대표자를 다시 계산하지 않는다."""
    import inspect
    src = inspect.getsource(collector._official_index)
    for bad in ("memberOrder", '["order"]', "members[0]"):
        assert bad not in src, f"공식 순서로 대표자를 고르고 있다: {bad}"
