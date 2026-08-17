"""소속 그룹 다중 색상 그라데이션 (ADMIN-GROUP 3-1).

지키려는 계약은 다섯 가지다.

1. **색상 지점은 1~8개**이고, 색은 `#RRGGBB`만 통과한다. 색이 인라인 스타일로
   들어가므로 여기서 느슨해지면 그게 곧 주입 경로다.
2. **구형 데이터가 마이그레이션 후에도 정확히 같은 색으로 보인다.** `color_stops`가
   NULL인 행은 읽을 때 3컬럼에서 합성된다 — 백필(파괴적 UPDATE)을 하지 않는다.
3. **두 표현이 갈라지지 않는다.** 어느 쪽 입력으로 써도 신형 JSON과 구형 3컬럼이
   함께 갱신된다. 구형만 읽는 코드가 계속 동작해야 하기 때문이다.
4. **위치는 정렬해 저장하고 동률은 유지한다**(hard stop 표현이 가능해야 한다).
5. 잘못된 hex·위치·개수는 **백엔드에서** 거부한다(프론트 검증만 믿지 않는다).
"""
import json

import pytest
import streamer_tags as st

import database


@pytest.fixture
def tdb(db):
    async def _clear():
        conn = await database.get_db()
        await conn.execute("DELETE FROM streamer_tag_assignments")
        await conn.execute("DELETE FROM streamer_tags")
        await conn.commit()
    db(_clear())
    st.reset_state()
    return db


async def _raw(tag_id):
    c = await database.get_db()
    return dict(await (await c.execute(
        "SELECT * FROM streamer_tags WHERE id=?", (tag_id,))).fetchone())


# ── 1) 검증 ────────────────────────────────────────────────────────────────

def test_stops_accept_three_or_more():
    s = st.clean_stops([{"color": "#ff0000", "pos": 0},
                        {"color": "#00FF00", "pos": 50},
                        {"color": "#0000ff", "pos": 100}])
    assert [x["color"] for x in s] == ["#ff0000", "#00ff00", "#0000ff"]
    assert [x["pos"] for x in s] == [0, 50, 100]


def test_stops_without_pos_are_distributed_evenly():
    assert [x["pos"] for x in st.clean_stops(["#111111"])] == [0]
    assert [x["pos"] for x in st.clean_stops(["#111111", "#222222"])] == [0, 100]
    assert [x["pos"] for x in
            st.clean_stops(["#111111", "#222222", "#333333"])] == [0, 50, 100]
    assert [x["pos"] for x in st.clean_stops(
        ["#111111", "#222222", "#333333", "#444444", "#555555"])] \
        == [0, 25, 50, 75, 100]


def test_stops_are_sorted_but_ties_keep_input_order():
    """드래그로 순서를 바꾸는 것은 정상 조작이라 거부하지 않고 정렬한다.
    동률은 유지해야 hard stop(경계가 딱 끊기는 띠)을 표현할 수 있다."""
    s = st.clean_stops([{"color": "#aaaaaa", "pos": 80},
                        {"color": "#bbbbbb", "pos": 20},
                        {"color": "#cccccc", "pos": 20}])
    assert [(x["color"], x["pos"]) for x in s] == [
        ("#bbbbbb", 20), ("#cccccc", 20), ("#aaaaaa", 80)]


def test_at_least_one_color_is_required():
    with pytest.raises(st.TagError):
        st.clean_stops([])


def test_too_many_stops_rejected():
    ok = [f"#{i}{i}{i}{i}{i}{i}" for i in range(1, 9)]      # 8개
    assert len(st.clean_stops(ok)) == st.MAX_COLOR_STOPS
    with pytest.raises(st.TagError):
        st.clean_stops(ok + ["#999999"])                    # 9개


@pytest.mark.parametrize("bad", [
    "#abc",              # 3자리 축약
    "red",               # 색 이름
    "rgb(1,2,3)",
    "var(--x)",
    "url(javascript:1)",
    "#12345g",
    "#1234567",
    "",
])
def test_bad_hex_rejected(bad):
    with pytest.raises(st.TagError):
        st.clean_stops([bad])


@pytest.mark.parametrize("pos", [-1, 101, 1000, "50", None if False else True])
def test_bad_position_rejected(pos):
    with pytest.raises(st.TagError):
        st.clean_stops([{"color": "#112233", "pos": pos}])


def test_bad_shape_rejected():
    for bad in ("nope", 5, {"color": "#112233"}, [["#112233"]]):
        with pytest.raises(st.TagError):
            st.clean_stops(bad)


def test_bad_direction_rejected():
    with pytest.raises(st.TagError):
        st.clean_style_v2(["#112233", "#445566"], "to-nowhere")
    with pytest.raises(st.TagError):
        st.clean_style_v2(["#112233"], "url(x)")


# ── 2) 하위 호환 — 구형 행은 백필 없이 그대로 보인다 ────────────────────────

def test_legacy_solid_row_synthesizes_one_stop(tdb):
    async def seed():
        c = await database.get_db()
        cur = await c.execute(
            "INSERT INTO streamer_tags (name, slug, kind, color_mode, color_start,"
            " color_end, gradient_direction, active, created_at, updated_at)"
            " VALUES ('구형단일','old-solid','team','solid','#AABBCC',NULL,"
            "'to-right',1,0,0)")
        await c.commit()
        return cur.lastrowid
    tid = tdb(seed())
    row = tdb(_raw(tid))
    assert row["color_stops"] is None, "구형 행을 백필하지 않는다"
    assert st.stops_of(row) == [{"color": "#aabbcc", "pos": 0}]


def test_legacy_two_color_row_synthesizes_two_stops(tdb):
    async def seed():
        c = await database.get_db()
        cur = await c.execute(
            "INSERT INTO streamer_tags (name, slug, kind, color_mode, color_start,"
            " color_end, gradient_direction, active, created_at, updated_at)"
            " VALUES ('구형2색','old-grad','team','gradient','#112233','#445566',"
            "'to-bottom',1,0,0)")
        await c.commit()
        return cur.lastrowid
    tid = tdb(seed())
    row = tdb(_raw(tid))
    assert row["color_stops"] is None
    assert st.stops_of(row) == [{"color": "#112233", "pos": 0},
                                {"color": "#445566", "pos": 100}]


def test_broken_json_falls_back_instead_of_crashing(tdb):
    """색은 장식이다 — 저장된 JSON이 깨졌다고 500을 내는 쪽이 더 나쁘다."""
    async def seed():
        c = await database.get_db()
        cur = await c.execute(
            "INSERT INTO streamer_tags (name, slug, kind, color_mode, color_start,"
            " color_end, gradient_direction, color_stops, active, created_at,"
            " updated_at) VALUES ('깨짐','broken','team','solid','#0f0f0f',NULL,"
            "'to-right','{not json',1,0,0)")
        await c.commit()
        return cur.lastrowid
    row = tdb(_raw(tdb(seed())))
    assert st.stops_of(row) == [{"color": "#0f0f0f", "pos": 0}]


def test_legacy_write_path_still_works_and_backfills_stops(tdb):
    """구형 본문(colorMode/Start/End)만 보내도 동작하고, 신형 표현이 함께 써진다."""
    t = tdb(st.create_tag(name="구형입력", color_mode="gradient",
                          color_start="#101010", color_end="#202020",
                          gradient_direction="to-bottom"))
    assert t["colorStops"] == [{"color": "#101010", "pos": 0},
                               {"color": "#202020", "pos": 100}]
    raw = tdb(_raw(t["id"]))
    assert json.loads(raw["color_stops"]) == t["colorStops"], "두 표현이 갈라지면 안 된다"
    assert raw["color_mode"] == "gradient" and raw["color_end"] == "#202020"


# ── 3) 신형 쓰기 — 구형 컬럼이 함께 갱신된다 ────────────────────────────────

def test_create_with_stops_keeps_legacy_columns_in_sync(tdb):
    t = tdb(st.create_tag(name="3색", color_stops=[
        {"color": "#ff0000", "pos": 0},
        {"color": "#00ff00", "pos": 40},
        {"color": "#0000ff", "pos": 100}], gradient_direction="to-bottom-right"))
    raw = tdb(_raw(t["id"]))
    # 구형 소비처가 표현할 수 있는 최선 = 양 끝
    assert raw["color_mode"] == "gradient"
    assert raw["color_start"] == "#ff0000"
    assert raw["color_end"] == "#0000ff"
    assert raw["gradient_direction"] == "to-bottom-right"
    assert len(json.loads(raw["color_stops"])) == 3
    assert t["colorStops"][1] == {"color": "#00ff00", "pos": 40}


def test_single_stop_is_solid_in_legacy_columns(tdb):
    t = tdb(st.create_tag(name="한색", color_stops=["#123456"]))
    raw = tdb(_raw(t["id"]))
    assert raw["color_mode"] == "solid" and raw["color_end"] is None
    assert t["colorStops"] == [{"color": "#123456", "pos": 0}]


def test_update_stops_replaces_previous_set(tdb):
    t = tdb(st.create_tag(name="교체", color_stops=["#111111", "#222222"]))
    u = tdb(st.update_tag(t["id"], color_stops=["#333333", "#444444", "#555555"]))
    assert [x["color"] for x in u["colorStops"]] == \
        ["#333333", "#444444", "#555555"]
    raw = tdb(_raw(t["id"]))
    assert raw["color_start"] == "#333333" and raw["color_end"] == "#555555"


def test_update_can_shrink_to_single_color(tdb):
    t = tdb(st.create_tag(name="축소", color_stops=["#111111", "#222222", "#333333"]))
    u = tdb(st.update_tag(t["id"], color_stops=["#999999"]))
    assert u["colorStops"] == [{"color": "#999999", "pos": 0}]
    assert u["colorMode"] == "solid" and u["colorEnd"] is None


def test_update_rejects_empty_stops(tdb):
    t = tdb(st.create_tag(name="빈값", color_stops=["#111111"]))
    with pytest.raises(st.TagError):
        tdb(st.update_tag(t["id"], color_stops=[]))
    assert tdb(st.get_tag(t["id"]))["color_start"] == "#111111", "실패 시 원본 유지"


def test_stops_win_over_legacy_when_both_sent(tdb):
    """편집기가 새 배열을 보냈는데 폼이 옛 필드를 함께 실어 보내도 배열이 이긴다."""
    t = tdb(st.create_tag(name="우선순위", color_mode="solid", color_start="#000000",
                          color_stops=["#abcdef", "#fedcba"]))
    assert [x["color"] for x in t["colorStops"]] == ["#abcdef", "#fedcba"]
    assert t["colorStart"] == "#abcdef"


def test_update_direction_only_keeps_stops(tdb):
    t = tdb(st.create_tag(name="방향만", color_stops=["#111111", "#222222", "#333333"],
                          gradient_direction="to-right"))
    u = tdb(st.update_tag(t["id"], gradient_direction="to-bottom"))
    assert u["gradientDirection"] == "to-bottom"
    assert len(u["colorStops"]) == 3, "방향만 바꿨는데 색이 사라지면 안 된다"


def test_update_name_only_does_not_touch_colors(tdb):
    t = tdb(st.create_tag(name="이름만", color_stops=["#111111", "#222222", "#333333"]))
    u = tdb(st.update_tag(t["id"], name="이름바꿈"))
    assert u["name"] == "이름바꿈"
    assert len(u["colorStops"]) == 3


# ── 4) 공개 응답 ────────────────────────────────────────────────────────────

def test_public_payload_always_has_stops(tdb):
    """프론트에 "없을 수도 있음" 분기를 만들지 않기 위해 항상 존재해야 한다."""
    tdb(st.create_tag(name="공개", color_stops=["#0a0a0a", "#0b0b0b", "#0c0c0c"]))
    rows = tdb(st.list_tags())
    assert rows and all(r.get("colorStops") for r in rows)
    # 구형 필드도 계속 나간다(하위 호환)
    assert all("colorMode" in r and "colorStart" in r for r in rows)


def test_tags_for_channel_carries_stops(tdb):
    t = tdb(st.create_tag(name="지정", color_stops=["#010101", "#020202", "#030303"]))
    ch = "a" * 32
    tdb(st.assign(ch, t["id"]))
    got = tdb(st.tags_for_channel(ch))
    assert got[0]["colorStops"] == [{"color": "#010101", "pos": 0},
                                    {"color": "#020202", "pos": 50},
                                    {"color": "#030303", "pos": 100}]
