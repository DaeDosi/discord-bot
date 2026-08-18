"""공식 예선 참가자의 프로필·대표 클립 연결 (UI-T 요구 6).

**실제로 있었던 결함:** `_qualifier_side_data`가 `singcup_streamers`를
`s.owner_channel_id`로 조회했다. 그 테이블의 키는 `channel_id`이고
`owner_channel_id`는 `singcup_clips`/`singcup_snapshots` 쪽 이름이다.
SQLite가 `no such column`을 던졌지만 `except Exception: pass`가 삼켜서
**참가자 64명 전원의 프로필·클립이 조용히 빈 값**이 됐다. 화면에는 `클립 없음`
카드만 반복됐고, 데이터는 DB에 멀쩡히 있었다(확정본 1,640행에 64명 전원 존재).

그래서 이 파일이 지키는 계약은 다섯 가지다.

1. **채널 id로 연결한다.** 이름 유사도로 붙이지 않는다.
2. **override가 자동 선정을 이긴다.** 해제(`cleared_at`)된 override는 무시한다.
3. **유효한 대표 클립이 없을 때만** 클립이 비어야 한다.
4. **프로필이 없어도 참가자는 사라지지 않는다.**
5. 실패를 조용히 삼키지 않는다 — 컬럼 이름이 틀리면 테스트가 깨져야 한다.
"""
import time

import pytest

import database

# 이미지3에 `클립 없음`으로 찍혀 있던 실제 참가자들 — 회귀 fixture.
CASES = [
    ("54cf8e05daaaa9577ad0f211d495dc95", "고다요"),
    ("f42e97f59c3177b8686dccfbf90792dd", "김아테 l Ate"),
    ("79ef1f5274b48fcb2de41b8ac8ea7ca1", "냐오 NYAO"),
    ("4a0493dd6f3542b99943c39848ad1045", "리모 RIMO"),
    ("54bdb327ed6039db869d15b0e5eec394", "린시"),
]
EVENT = "singcup-2026"


@pytest.fixture
def qdb(db):
    async def _clear():
        c = await database.get_db()
        for t in ("singcup_streamers", "singcup_clips",
                  "singcup_representative_overrides",
                  "singcup_qualifier_songs"):
            try:
                await c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await c.commit()
    db(_clear())
    return db


async def _seed_streamer(cid, name, *, image="", rep_uid=None):
    c = await database.get_db()
    await c.execute(
        "INSERT OR REPLACE INTO singcup_streamers (channel_id, event_id,"
        " channel_name, channel_image_url, follower_count, verified_mark,"
        " representative_clip_uid, tagged_clip_count, last_channel_updated_at,"
        " row_updated_at) VALUES (?,?,?,?,0,0,?,1,0,?)",
        (cid, EVENT, name, image, rep_uid, int(time.time())))
    await c.commit()


async def _seed_clip(uid, cid, *, title="노래", thumb="https://t/x.jpg",
                     active=1, owner_img=""):
    """실제 컬럼명은 `clip_title`/`thumbnail_image_url`이다 — 줄이지 말 것."""
    c = await database.get_db()
    names = {r[1] for r in await (await c.execute(
        "PRAGMA table_info(singcup_clips)")).fetchall()}
    now = int(time.time())
    base = {"clip_uid": uid, "event_id": EVENT, "owner_channel_id": cid,
            "clip_title": title, "thumbnail_image_url": thumb, "active": active,
            "created_at": now, "first_collected_at": now,
            "last_collected_at": now, "row_updated_at": now,
            "owner_channel_image_url": owner_img}
    keys = [k for k in base if k in names]
    await c.execute(
        f"INSERT OR REPLACE INTO singcup_clips ({','.join(keys)})"
        f" VALUES ({','.join('?' * len(keys))})", tuple(base[k] for k in keys))
    await c.commit()


async def _side(ids):
    """(live, clips) — 곡 맵은 별도 테스트가 본다."""
    from routers.singcup_router import _qualifier_side_data
    live, clips, _songs = await _qualifier_side_data(set(ids))
    return live, clips


async def _songs(ids):
    from routers.singcup_router import _qualifier_side_data
    _live, _clips, songs = await _qualifier_side_data(set(ids))
    return songs


# ── 1) 결함 자체 ────────────────────────────────────────────────────────────

class TestRegression:
    def test_다섯_참가자_전원의_프로필과_대표클립이_연결된다(self, qdb):
        """이미지3에서 전원 `클립 없음`이었던 바로 그 5명."""
        async def _go():
            for i, (cid, name) in enumerate(CASES):
                await _seed_streamer(cid, name, image=f"https://img/{i}.png",
                                     rep_uid=f"clip{i}")
                await _seed_clip(f"clip{i}", cid, title=f"{name} 노래",
                                 thumb=f"https://thumb/{i}.jpg")
            return await _side([c for c, _ in CASES])

        _, clips = qdb(_go())
        assert len(clips) == 5, "채널 id 연결이 끊겼다"
        for i, (cid, name) in enumerate(CASES):
            got = clips[cid]
            assert got["channelImageUrl"] == f"https://img/{i}.png"
            assert got["clipUid"] == f"clip{i}"
            assert got["clipTitle"] == f"{name} 노래"
            assert got["clipThumbnailUrl"] == f"https://thumb/{i}.jpg"

    def test_컬럼_이름이_틀리면_조용히_비지_않는다(self, qdb):
        """`except: pass`가 컬럼 오류를 삼켜 전원 빈 값이 됐던 회귀."""
        async def _go():
            cid, name = CASES[0]
            await _seed_streamer(cid, name, image="https://img/a.png",
                                 rep_uid="c1")
            await _seed_clip("c1", cid)
            return await _side([cid])

        _, clips = qdb(_go())
        assert clips, "side data가 통째로 비었다 — 쿼리가 예외로 죽고 있다"

    def test_API_응답까지_이어진다(self, qdb):
        """함수만이 아니라 실제 엔드포인트 응답에 실리는지 본다."""
        import singcup_qualifiers as sq
        from routers.singcup_router import qualifiers as ep

        cid = sq.QUALIFIERS["female_solo"][0]["channelId"]

        async def _go():
            await _seed_streamer(cid, "이름", image="https://img/z.png",
                                 rep_uid="cz")
            await _seed_clip("cz", cid, title="타이틀",
                             thumb="https://thumb/z.jpg")
            return await ep(division="female_solo")

        out = qdb(_go())
        row = [r for r in out["divisions"]["female_solo"]
               if r["channelId"] == cid][0]
        assert row["channelImageUrl"] == "https://img/z.png"
        assert row["clipUid"] == "cz"
        assert row["clipThumbnailUrl"] == "https://thumb/z.jpg"


# ── 2) override 우선순위 ────────────────────────────────────────────────────

class TestOverride:
    def test_override가_자동_대표를_이긴다(self, qdb):
        async def _go():
            cid = CASES[0][0]
            await _seed_streamer(cid, "고다요", rep_uid="auto")
            await _seed_clip("auto", cid, title="자동", thumb="https://t/a.jpg")
            await _seed_clip("manual", cid, title="수동", thumb="https://t/m.jpg")
            c = await database.get_db()
            await c.execute(
                "INSERT INTO singcup_representative_overrides (event_id,"
                " owner_channel_id, override_clip_uid, created_at, updated_at)"
                " VALUES (?,?,?,0,0)", (EVENT, cid, "manual"))
            await c.commit()
            return await _side([cid])

        _, clips = qdb(_go())
        got = clips[CASES[0][0]]
        assert got["clipUid"] == "manual", "운영자 지정이 무시됐다"
        assert got["clipTitle"] == "수동"
        assert got["isOverride"] is True

    def test_해제된_override는_무시된다(self, qdb):
        """해제는 행 삭제가 아니라 `cleared_at` 기록이다(이력 보존)."""
        async def _go():
            cid = CASES[0][0]
            await _seed_streamer(cid, "고다요", rep_uid="auto")
            await _seed_clip("auto", cid, title="자동")
            await _seed_clip("manual", cid, title="수동")
            c = await database.get_db()
            await c.execute(
                "INSERT INTO singcup_representative_overrides (event_id,"
                " owner_channel_id, override_clip_uid, created_at, updated_at,"
                " cleared_at) VALUES (?,?,?,0,0,99)", (EVENT, cid, "manual"))
            await c.commit()
            return await _side([cid])

        _, clips = qdb(_go())
        assert clips[CASES[0][0]]["clipUid"] == "auto"
        assert clips[CASES[0][0]]["isOverride"] is False


# ── 3) 빈 값의 조건 ─────────────────────────────────────────────────────────

class TestEmptyIsEarned:
    def test_대표_클립이_없을_때만_비어야_한다(self, qdb):
        async def _go():
            cid = CASES[1][0]
            await _seed_streamer(cid, "김아테 l Ate", image="https://img/b.png",
                                 rep_uid=None)
            return await _side([cid])

        _, clips = qdb(_go())
        got = clips[CASES[1][0]]
        assert got["clipUid"] is None, "없는 클립을 만들어내면 안 된다"
        assert got["channelImageUrl"] == "https://img/b.png", \
            "클립이 없다고 프로필까지 잃으면 안 된다"

    def test_비활성_클립은_대표로_쓰지_않는다(self, qdb):
        async def _go():
            cid = CASES[2][0]
            await _seed_streamer(cid, "냐오 NYAO", rep_uid="dead")
            await _seed_clip("dead", cid, active=0)
            return await _side([cid])

        _, clips = qdb(_go())
        assert clips[CASES[2][0]]["clipUid"] is None

    def test_프로필이_없어도_참가자는_남는다(self, qdb):
        from routers.singcup_router import qualifiers as ep

        out = qdb(ep(division="female_solo"))
        rows = out["divisions"]["female_solo"]
        assert len(rows) == 64, "이미지가 없다고 명단이 줄면 안 된다"
        assert all(r["channelId"] for r in rows)

    def test_프로필은_클립_소유자_정보로_대체된다(self, qdb):
        """대표 클립이 없어도 그 채널의 클립에 실린 프로필을 예비로 쓴다."""
        async def _go():
            cid = CASES[3][0]
            await _seed_streamer(cid, "리모 RIMO", image="", rep_uid=None)
            await _seed_clip("any", cid, owner_img="https://img/fallback.png")
            return await _side([cid])

        _, clips = qdb(_go())
        got = clips[CASES[3][0]]
        assert got["channelImageUrl"] == "https://img/fallback.png"


# ── 4) 이름으로 매핑하지 않는다 ─────────────────────────────────────────────

class TestNoNameMatching:
    def test_이름이_같아도_id가_다르면_붙지_않는다(self, qdb):
        """이름 유사도 매핑은 순위를 통째로 틀리게 만든다 — 절대 금지."""
        async def _go():
            await _seed_streamer("f" * 32, "고다요", image="https://img/x.png",
                                 rep_uid="other")
            await _seed_clip("other", "f" * 32)
            return await _side([CASES[0][0]])

        _, clips = qdb(_go())
        assert CASES[0][0] not in clips, "이름만 같은 다른 채널이 붙었다"

    def test_조회는_id_집합으로_좁힌다(self):
        """전체를 읽고 파이썬에서 거르면 응답만 작아지고 DB 부하는 그대로다."""
        import inspect

        from routers import singcup_router as sr
        src = inspect.getsource(sr._qualifier_side_data)
        assert "IN ({marks})" in src
        assert "s.channel_id" in src, "테이블 키는 channel_id다"
        assert "s.owner_channel_id" not in src, \
            "singcup_streamers에는 owner_channel_id가 없다"

    def test_실패를_조용히_삼키지_않는다(self):
        import inspect

        from routers import singcup_router as sr
        src = inspect.getsource(sr._qualifier_side_data)
        assert "except Exception:\n        pass" not in src, \
            "침묵이 컬럼명 오류를 오래 숨겼다"
        assert src.count("print(") >= 2, "두 조인 모두 실패를 남겨야 한다"


# ── 5) 종료된 비공식 랭킹과 무관해야 한다 ───────────────────────────────────

class TestRetirementIndependence:
    def test_비공식_랭킹_종료가_참가자_읽기를_막지_않는다(self, qdb, monkeypatch):
        """게이트는 *비공식 인기 순위*를 닫는 것이지 명단 읽기를 닫는 게 아니다."""
        import singcup_collector as sc
        from routers.singcup_router import qualifiers as ep

        monkeypatch.setenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", "false")
        monkeypatch.setenv("SINGCUP_LIVE_FEATURE_ENABLED", "false")
        assert sc.unofficial_ranking_open() is False

        async def _go():
            cid = CASES[4][0]
            await _seed_streamer(cid, "린시", image="https://img/l.png",
                                 rep_uid="lc")
            await _seed_clip("lc", cid, thumb="https://thumb/l.jpg")
            return await ep(division="female_solo")

        out = qdb(_go())
        row = [r for r in out["divisions"]["female_solo"]
               if r["channelId"] == CASES[4][0]][0]
        assert row["clipThumbnailUrl"] == "https://thumb/l.jpg"
        assert out["counts"]["female_solo"] == 64


# ── 6) 곡·가수는 명시적으로 저장된 값만 쓴다 ────────────────────────────────
#
# 운영 클립 제목은 형식이 제각각이다(실측):
#   "[싱드컵] 솔지 - 오늘따라 비가와서 그런가봐"  → 가수 - 곡
#   "어른 - 손디아"                               → 곡 - 가수
#   "Cheek to cheek"                              → 구분자 없음
# 같은 " - "를 두고 순서가 반대라 문자열만으로는 어느 쪽이 곡인지 알 수 없다.

class TestSongMetadata:
    @staticmethod
    async def _seed_song(cid, song, artist, source="admin"):
        c = await database.get_db()
        await c.execute(
            "INSERT OR REPLACE INTO singcup_qualifier_songs"
            " (channel_id, song_title, artist_name, source, updated_at)"
            " VALUES (?,?,?,?,?)", (cid, song, artist, source, int(time.time())))
        await c.commit()

    def test_저장된_곡과_가수가_응답에_실린다(self, qdb):
        import singcup_qualifiers as sq
        from routers.singcup_router import qualifiers as ep
        cid = sq.QUALIFIERS["female_solo"][0]["channelId"]

        async def _go():
            await self._seed_song(cid, "오늘따라 비가와서 그런가봐", "솔지")
            return await ep(division="female_solo")

        row = [r for r in qdb(_go())["divisions"]["female_solo"]
               if r["channelId"] == cid][0]
        assert row["songTitle"] == "오늘따라 비가와서 그런가봐"
        assert row["artistName"] == "솔지"

    def test_값이_없으면_빈_문자열이다(self, qdb):
        """없는 정보를 만들어내지 않는다 — 화면은 이 줄을 그리지 않는다."""
        from routers.singcup_router import qualifiers as ep

        rows = qdb(ep(division="female_solo"))["divisions"]["female_solo"]
        assert all(r["songTitle"] == "" for r in rows)
        assert all(r["artistName"] == "" for r in rows)

    def test_클립_제목을_쪼개지_않는다(self, qdb):
        """제목에 ` - `가 있어도 곡·가수로 나누지 않는다(순서를 알 수 없다)."""
        import singcup_qualifiers as sq
        from routers.singcup_router import qualifiers as ep
        cid = sq.QUALIFIERS["female_solo"][0]["channelId"]

        async def _go():
            await _seed_streamer(cid, "이름", rep_uid="c1")
            await _seed_clip("c1", cid, title="[싱드컵] 솔지 - 오늘따라 비가와서")
            return await ep(division="female_solo")

        row = [r for r in qdb(_go())["divisions"]["female_solo"]
               if r["channelId"] == cid][0]
        assert row["clipTitle"].startswith("[싱드컵]")
        assert row["songTitle"] == "", "제목을 쪼개 곡을 추측했다"
        assert row["artistName"] == "", "제목을 쪼개 가수를 추측했다"

    def test_운영자_입력이_PIKU보다_우선한다(self, qdb):
        import singcup_qualifiers as sq
        cid = sq.QUALIFIERS["female_solo"][0]["channelId"]

        async def _go():
            await self._seed_song(cid, "피쿠곡", "피쿠가수", source="piku")
            await self._seed_song(cid, "운영자곡", "운영자가수", source="admin")
            return await _songs([cid])

        got = qdb(_go())[cid]
        assert got["songTitle"] == "운영자곡"
        assert got["songSource"] == "admin"

    def test_조회는_id_집합으로_좁힌다(self):
        import inspect

        from routers import singcup_router as sr
        src = inspect.getsource(sr._qualifier_side_data)
        assert "singcup_qualifier_songs" in src
        assert src.count("IN ({marks})") >= 3, "곡 조회도 id로 좁혀야 한다"
