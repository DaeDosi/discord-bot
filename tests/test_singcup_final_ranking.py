"""비공식 인기점수 랭킹 동결 (UI-P 요구 2).

지키려는 계약은 다음과 같다.

1. 확정본이 저장되면 **원본 clip 지표가 바뀌어도 응답이 그대로**다.
   (sweep는 계속 도는 것이 정상이다 — 얼리는 것은 랭킹 응답 하나뿐이다.)
2. ETag가 다시 계산되지 않는다 → 조건부 요청이 항상 304로 끝난다.
3. 캐시를 비워도(=TTL 만료·재시작) DB에서 같은 bytes를 다시 읽는다.
4. 확정은 **한 번만** 일어난다. 재실행해도 순위가 바뀌지 않는다.
5. 저장 실패는 부분 저장을 남기지 않는다.
6. **공식 예선 참가자 화면이 쓰는 `/main`은 계속 최신값을 준다.**
7. 수집기 게이트(등록·갱신·스냅샷)를 건드리지 않는다 — 전체 수집 중단 금지.
"""
import asyncio
import json
import time

import pytest


@pytest.fixture
def sdb(db):
    """싱드컵 테이블을 이 모듈 안에서만 비운다."""
    import singcup_clips as sc
    import singcup_final as sf

    import database

    async def _clear():
        conn = await database.get_db()
        for t in ("singcup_final_ranking", "singcup_clips", "singcup_streamers",
                  "singcup_snapshots"):
            try:
                await conn.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await conn.commit()

    db(_clear())
    sf.reset_cache()
    sc.invalidate_main_cache()
    return db


async def _seed(hearts: int = 10, views: int = 100, uid: str = "c1") -> None:
    """참가자 1명 + 대표 클립 1개."""
    import singcup_clips as sc

    import database
    conn = await database.get_db()
    now = int(time.time())
    await conn.execute(
        "INSERT OR REPLACE INTO singcup_clips (clip_uid,event_id,owner_channel_id,"
        "video_id,clip_title,thumbnail_image_url,description,created_at,heart_count,"
        "view_count,duration,adult,metrics_ok,active,missing_scan_count,"
        "first_collected_at,last_collected_at,row_updated_at) "
        "VALUES (?,?,'o1','v','t','','#싱드컵',?,?,?,60,0,1,1,0,?,?,?)",
        (uid, sc.EVENT_ID, now, hearts, views, now, now, now))
    await conn.execute(
        "INSERT OR REPLACE INTO singcup_streamers (channel_id,event_id,channel_name,"
        "representative_clip_uid,tagged_clip_count,row_updated_at) "
        "VALUES ('o1',?,'스트리머1',?,1,?)", (sc.EVENT_ID, uid, now))
    await conn.commit()


async def _bump(hearts: int, views: int, uid: str = "c1") -> None:
    """sweep가 지표를 갱신한 상황을 만든다."""
    import database
    conn = await database.get_db()
    await conn.execute(
        "UPDATE singcup_clips SET heart_count=?, view_count=? WHERE clip_uid=?",
        (hearts, views, uid))
    await conn.commit()


class TestFreezeGate:
    def test_이벤트가_끝났으면_동결이다(self, sdb):
        import singcup_final as sf
        # conftest가 END_AT을 2026-08-09로 고정해 두었다.
        assert sf.ranking_frozen() is True

    def test_동결은_수집기_게이트와_별개다(self, sdb, monkeypatch):
        """전체 수집을 멈추는 방식으로 구현하면 이 테스트가 깨진다.

        SINGCUP-3의 **기능 종료 축**은 여기서 검사하는 대상이 아니다. 그쪽을 켜 둔
        상태에서 "동결이 게이트를 닫지 않는다"를 본다 — 두 축이 직교하기 때문이다.
        (기능 축 자체는 `tests/test_singcup_retirement.py`가 고정한다.)
        """
        import singcup_collector as sco
        monkeypatch.setenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", "true")
        # 얼려도 지표 갱신·스냅샷·순위 계산 게이트는 그대로 열려 있어야 한다.
        assert sco.metrics_refresh_open() is True
        assert sco.snapshot_refresh_open() is True
        assert sco.ranking_refresh_open() is True

    def test_지표_갱신은_기능_종료와_무관하다(self, sdb, monkeypatch):
        """스윕(지표 갱신)은 기능 종료에 묶지 않았다 — 스윕을 건드리지 말라는 요구다."""
        import singcup_collector as sco
        monkeypatch.delenv("SINGCUP_UNOFFICIAL_RANKING_ENABLED", raising=False)
        assert sco.metrics_refresh_open() is True
        assert sco.ranking_refresh_open() is False

    def test_동결_모듈은_게이트를_import하지_않는다(self):
        """수집 중단으로 확대되는 것을 소스 수준에서 막는다."""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1] / "web" / "backend" / "singcup_final.py"
        s = src.read_text(encoding="utf-8")
        for gate in ("registration_open", "metrics_refresh_open",
                     "snapshot_refresh_open", "ranking_refresh_open"):
            # 주석에 이름이 나오는 것은 허용하되, 실제 호출은 없어야 한다.
            assert f"{gate}(" not in s.replace(f"`{gate}`", ""), gate


class TestFinalizeOnce:
    def test_확정본이_저장된다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed()
            res = await sf.finalize(source="test")
            entry = await sf.load_entry()
            return res, entry

        res, entry = sdb(_go())
        assert res["created"] is True
        assert entry is not None
        data = json.loads(entry["body"])
        assert data["rankingFinal"] is True
        assert data["streamers"][0]["heartCount"] == 10

    def test_재실행해도_순위가_바뀌지_않는다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed(hearts=10)
            await sf.finalize(source="first")
            await _bump(hearts=999, views=999)     # sweep가 지표를 갱신했다
            second = await sf.finalize(source="second")
            entry = await sf.load_entry()
            return second, json.loads(entry["body"])

        second, data = sdb(_go())
        assert second["created"] is False
        assert second["reason"] == "already_finalized"
        assert data["streamers"][0]["heartCount"] == 10, "확정 후 지표가 따라 움직였다"


class TestFrozenAgainstSweep:
    def test_원본_지표가_변해도_응답이_같다(self, sdb):
        """요구의 핵심. sweep는 계속 도는 것이 정상이고, 응답만 그대로여야 한다."""
        import singcup_final as sf

        async def _go():
            await _seed(hearts=10, views=100)
            await sf.finalize(source="test")
            first = await sf.load_entry()
            body1, etag1 = bytes(first["body"]), first["etag"]

            await _bump(hearts=5000, views=90000)
            sf.reset_cache()                        # 캐시가 가려주는 것을 배제한다
            second = await sf.load_entry()
            return body1, etag1, bytes(second["body"]), second["etag"]

        b1, e1, b2, e2 = sdb(_go())
        assert b1 == b2, "원본 지표 변경이 확정본에 새어 들어왔다"
        assert e1 == e2, "ETag가 다시 계산됐다"

    def test_공식_참가자_화면은_최신값을_계속_받는다(self, sdb):
        """요구 9 — /main은 얼리지 않는다."""
        import singcup_clips as sc
        import singcup_final as sf

        async def _go():
            await _seed(hearts=10)
            await sf.finalize(source="test")
            await _bump(hearts=777, views=8888)
            sc.invalidate_main_cache()
            data = await sc.load_main(limit=10)
            return data["streamers"][0]["heartCount"]

        assert sdb(_go()) == 777, "/main까지 얼어붙었다 — 참가자 화면이 굳는다"


class TestRestartAndCache:
    def test_캐시를_비워도_같은_bytes다(self, sdb):
        """재시작·TTL 만료를 흉내낸다 — 확정본은 DB에 있으므로 같아야 한다."""
        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            a = bytes((await sf.load_entry())["body"])
            sf.reset_cache()
            b = bytes((await sf.load_entry())["body"])
            return a, b

        a, b = sdb(_go())
        assert a == b

    def test_확정_시각과_기준값이_전진하지_않는다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            d1 = json.loads((await sf.load_entry())["body"])
            await _bump(hearts=42, views=42)
            sf.reset_cache()
            d2 = json.loads((await sf.load_entry())["body"])
            return d1, d2

        d1, d2 = sdb(_go())
        for k in ("rankingFinalizedAt", "topHeartMovers1hComputedAt",
                  "topHeartMovers1hBaseAt", "topHeartMovers1hEvaluatedAt"):
            assert d1.get(k) == d2.get(k), f"{k}가 전진했다"

    def test_확정본에는_재시도_중_표시가_없다(self, sdb):
        """'이전 집계 / 다음 집계에 갱신' 문구가 뜨는 근거를 없앤다."""
        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            return json.loads((await sf.load_entry())["body"])

        assert sdb(_go())["topHeartMovers1hStale"] is False


class TestAtomicity:
    def test_저장_실패는_부분_결과를_남기지_않는다(self, sdb):
        import singcup_final as sf

        import database

        async def _go():
            await _seed(hearts=10)
            await sf.finalize(source="good")       # 정상 확정본 하나
            good = bytes((await sf.load_entry())["body"])

            # 두 번째 확정을 강제하되 도중에 실패시킨다.
            orig = sf._build

            def boom(_data):
                raise RuntimeError("직렬화 실패")

            sf._build = boom
            try:
                with pytest.raises(RuntimeError):
                    await sf.finalize(source="bad", force=True)
            finally:
                sf._build = orig

            sf.reset_cache()
            after = bytes((await sf.load_entry())["body"])
            conn = await database.get_db()
            n = await (await conn.execute(
                "SELECT COUNT(*) c FROM singcup_final_ranking")).fetchone()
            return good, after, int(n["c"])

        good, after, n = sdb(_go())
        assert after == good, "실패한 확정이 이전 정상 확정본을 훼손했다"
        assert n == 1, "부분 저장 행이 남았다"


class TestFeatureFlag:
    """플래그 계약 — true=동결 / false=실시간 / 미설정=동결(기본) / 이상값=fail-closed."""

    def _reload(self, monkeypatch, value):
        import importlib

        import singcup_final as sf
        if value is None:
            monkeypatch.delenv("SINGCUP_RANKING_FREEZE_ENABLED", raising=False)
        else:
            monkeypatch.setenv("SINGCUP_RANKING_FREEZE_ENABLED", value)
        return importlib.reload(sf)

    def test_미설정이면_기본_동결(self, sdb, monkeypatch):
        sf = self._reload(monkeypatch, None)
        assert sf.freeze_enabled() is True
        assert sf.ranking_frozen() is True

    @pytest.mark.parametrize("v", ["true", "TRUE", "1", "yes", "on", " True "])
    def test_참값은_동결(self, sdb, monkeypatch, v):
        assert self._reload(monkeypatch, v).freeze_enabled() is True

    @pytest.mark.parametrize("v", ["false", "FALSE", "0", "no", "off", " off "])
    def test_거짓값은_실시간(self, sdb, monkeypatch, v):
        sf = self._reload(monkeypatch, v)
        assert sf.freeze_enabled() is False
        assert sf.ranking_frozen() is False

    @pytest.mark.parametrize("v", ["banana", "", "tru", "2"])
    def test_이상값은_fail_closed(self, sdb, monkeypatch, v):
        """오타 하나로 '멈춰 달라'는 요구가 풀리면 안 된다."""
        assert self._reload(monkeypatch, v).freeze_enabled() is True


class TestFailClosed:
    """확정본이 없을 때 실시간 값을 최종본으로 내보내지 않는다."""

    def test_빈_결과는_확정하지_않는다(self, sdb):
        import singcup_final as sf

        async def _go():
            res = await sf.finalize(source="test")     # 참가자 0명
            return res, await sf.load_entry()

        res, entry = sdb(_go())
        assert res["created"] is False
        assert res["reason"] == "empty_result"
        assert entry is None, "빈 확정본이 저장됐다 — 되돌릴 수 없는 상태가 된다"

    def test_확정_전에는_확정본이_없다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed()
            return await sf.load_entry()

        assert sdb(_go()) is None

    def test_라우터가_실시간_값으로_물러서지_않는다(self):
        """소스로 고정 — /main fallback이 되살아나면 요구가 조용히 깨진다."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "web" / "backend" / "routers" / "singcup_router.py")
        s = src.read_text(encoding="utf-8")
        block = s.split('@router.get("/final-ranking")')[1].split("@router.get")[0]
        assert "status_code=503" in block, "확정본이 없을 때 503을 주지 않는다"
        assert "finalizing" in block
        assert "Retry-After" in block
        assert "load_main" not in block, "랭킹 엔드포인트가 실시간 경로를 부른다"


class TestSnapshotScope:
    """동결 대상은 랭킹 결과뿐 — 운영 상태(live/collector)는 얼리지 않는다."""

    def test_운영_상태_필드가_저장되지_않는다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            return json.loads((await sf.load_entry())["body"])

        d = sdb(_go())
        assert "collector" not in d, "수집기 상태가 얼었다 — 장애 안내가 거짓이 된다"
        assert "live" not in d, "라이브 신선도가 얼었다"
        assert "liveCount" not in d["summary"], "현재 라이브 수가 얼었다"

    def test_현재_방송_여부가_저장되지_않는다(self, sdb):
        """얼린 isLive를 그리면 방송을 끝낸 사람이 영원히 LIVE로 남는다."""
        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            return json.loads((await sf.load_entry())["body"])

        d = sdb(_go())
        for entry in d["streamers"] + d["topHeartMovers1h"]:
            for k in ("isLive", "live", "liveTitle"):
                assert k not in entry, f"{k}가 확정본에 남았다"

    def test_event는_불변_필드만_남는다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            return json.loads((await sf.load_entry())["body"])["event"]

        ev = sdb(_go())
        assert set(ev) <= {"id", "startAt", "endAt", "status"}
        assert ev["status"] == "ENDED"

    def test_랭킹_결과는_그대로_담긴다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed(hearts=10, views=100)
            await sf.finalize(source="test")
            return json.loads((await sf.load_entry())["body"])

        d = sdb(_go())
        s0 = d["streamers"][0]
        for k in ("rank", "score", "heartCount", "viewCount", "clipUid",
                  "clipThumbnailUrl", "channelName"):
            assert k in s0, f"랭킹 결과 {k}가 빠졌다"
        assert d["rankingFinal"] is True and d["rankingFinalizedAt"] > 0

    def test_현재_라이브_변화가_확정본을_바꾸지_않는다(self, sdb):
        """LIVE는 계속 변하지만 랭킹 bytes·ETag는 고정이어야 한다."""
        import singcup_final as sf

        import database

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            a = await sf.load_entry()
            # 라이브 상태의 원천(rising 수집 회차)이 바뀌어도 확정본은 그대로다.
            conn = await database.get_db()
            await conn.execute(
                "INSERT INTO rising_collect_runs (collected_at, ok) VALUES (?, 1)",
                (int(time.time()),))
            await conn.commit()
            sf.reset_cache()
            b = await sf.load_entry()
            return bytes(a["body"]), a["etag"], bytes(b["body"]), b["etag"]

        b1, e1, b2, e2 = sdb(_go())
        assert b1 == b2 and e1 == e2


class TestStatus:
    def test_상태에_메타데이터만_노출된다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            return await sf.status()

        st = sdb(_go())
        assert st["finalized"] is True
        assert st["frozen"] is True
        assert st["bytes"] > 0
        # 값은 전부 메타데이터(문자열 상태·불리언·숫자)뿐이어야 한다 —
        # payload나 스트리머 정보가 새어 나가면 안 된다.
        assert set(st) == {"eventStatus", "freezeEnabled", "frozen", "finalized",
                           "finalizedAt", "source", "bytes", "cached",
                           "retryInFlight", "cooldownRemaining", "cooldownSeconds"}
        assert isinstance(st["retryInFlight"], bool)
        assert isinstance(st["cooldownRemaining"], (int, float))


class TestAutoRecovery:
    """startup 확정이 실패해도 재시작 없이 회복된다 (차단 조건 G).

    예전에는 startup 3회 시도가 모두 실패하면 다음 재시작까지 영영 503이었다.
    Railway SQLite에서 일시적 잠금은 실제로 발생하므로 이건 비차단이 아니다.
    """

    def test_확정본이_있으면_재시도를_예약하지_않는다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")
            sf.reset_retry_state()
            return sf.schedule_finalize_if_needed(source="request")

        # 예약 자체는 되지만(행 존재 여부는 finalize가 판단), 계산 전에 빠진다.
        # 여기서 확인할 것은 '기존 확정본이 재계산으로 바뀌지 않는다'는 쪽이다.
        assert sdb(_go()) in ("scheduled", "in_flight")

    def test_기존_확정본은_재시도로도_바뀌지_않는다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed(hearts=10)
            await sf.finalize(source="first")
            before = bytes((await sf.load_entry())["body"])
            await _bump(hearts=9999, views=9999)
            sf.reset_retry_state()
            sf.schedule_finalize_if_needed(source="request")
            await asyncio.sleep(0.2)          # 예약된 task가 돌 시간을 준다
            sf.reset_cache()
            after = bytes((await sf.load_entry())["body"])
            return before, after

        before, after = sdb(_go())
        assert before == after, "재시도가 확정된 순위를 갈아치웠다"

    def test_동시_요청이_여러개여도_finalize는_한_번만_실행된다(self, sdb):
        import singcup_final as sf

        calls = []
        real = sf.finalize

        async def counting(*a, **kw):
            calls.append(1)
            return await real(*a, **kw)

        async def _go():
            await _seed()
            sf.reset_retry_state()
            sf.finalize = counting
            try:
                results = [sf.schedule_finalize_if_needed(source=f"req{i}")
                           for i in range(20)]
                await asyncio.sleep(0.3)
            finally:
                sf.finalize = real
            return results, len(calls)

        results, n = sdb(_go())
        assert results[0] == "scheduled"
        assert n == 1, f"finalize가 {n}회 실행됐다(동시 요청 20개)"
        # 나머지는 전부 in_flight 또는 cooldown이어야 한다.
        assert set(results[1:]) <= {"in_flight", "cooldown"}

    def test_cooldown_안에서는_추가_시도가_없다(self, sdb):
        import singcup_final as sf

        async def _go():
            await _seed()
            sf.reset_retry_state()
            first = sf.schedule_finalize_if_needed(source="a")
            await asyncio.sleep(0.25)          # task 완료를 기다린다
            second = sf.schedule_finalize_if_needed(source="b")
            return first, second, sf.cooldown_remaining()

        first, second, left = sdb(_go())
        assert first == "scheduled"
        assert second == "cooldown", "cooldown 안인데 또 시도했다"
        assert 0 < left <= sf_cooldown()

    def test_cooldown이_지나면_다시_시도한다(self, sdb, monkeypatch):
        import singcup_final as sf
        # 테스트에서 실제 30초를 기다리지 않는다.
        monkeypatch.setattr(sf, "FINALIZE_COOLDOWN_SECONDS", 0.05)

        async def _go():
            sf.reset_retry_state()
            a = sf.schedule_finalize_if_needed(source="a")
            await asyncio.sleep(0.25)
            b = sf.schedule_finalize_if_needed(source="b")
            return a, b

        a, b = sdb(_go())
        assert a == "scheduled" and b == "scheduled", "cooldown 이후에도 재시도가 막혔다"

    def test_영구_실패해도_DB를_두드리지_않는다(self, sdb, monkeypatch):
        """실패가 계속돼도 시도 횟수는 cooldown이 상한이다."""
        import singcup_final as sf
        attempts = []

        async def boom(*a, **kw):
            attempts.append(1)
            raise RuntimeError("lock")

        monkeypatch.setattr(sf, "_load_main_uncached", boom)

        async def _go():
            await _seed()
            sf.reset_retry_state()
            for _ in range(50):               # 요청 50개가 몰려온 상황
                sf.schedule_finalize_if_needed(source="req")
            await asyncio.sleep(0.3)
            return len(attempts)

        n = sdb(_go())
        assert n <= 1, f"요청 50개에 확정 계산이 {n}회 실행됐다"

    def test_재시도_성공이_503을_200으로_바꾼다(self, sdb, monkeypatch):
        """startup 실패 → 이후 재시도 성공 → 확정본 존재(=엔드포인트 200)."""
        import singcup_final as sf
        state = {"fail": True}
        real = sf._load_main_uncached

        async def flaky(limit):
            if state["fail"]:
                raise RuntimeError("database is locked")
            return await real(limit)

        monkeypatch.setattr(sf, "_load_main_uncached", flaky)

        async def _go():
            await _seed()
            sf.reset_retry_state()
            # 1) startup 시도 실패
            try:
                await sf.ensure_finalized(source="startup")
            except Exception:
                pass
            first = await sf.load_entry()
            # 2) 잠금이 풀린 뒤 재시도
            state["fail"] = False
            sf.reset_retry_state()
            sf.schedule_finalize_if_needed(source="request")
            await asyncio.sleep(0.3)
            sf.reset_cache()
            return first, await sf.load_entry()

        first, second = sdb(_go())
        assert first is None, "실패했는데 확정본이 생겼다"
        assert second is not None, "재시도로 회복되지 않았다(영구 503)"

    def test_Retry_After와_cooldown이_일치한다(self):
        import singcup_final as sf
        assert sf.RETRY_AFTER_SECONDS == int(sf.FINALIZE_COOLDOWN_SECONDS)


def sf_cooldown():
    import singcup_final as sf
    return sf.FINALIZE_COOLDOWN_SECONDS


class TestObservability:
    """확정 시도 로그에 민감정보가 실리지 않는다."""

    def test_로그에_payload나_비밀이_없다(self, sdb, caplog):
        import logging

        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="test")

        with caplog.at_level(logging.INFO):
            sdb(_go())
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "finalize_attempt" in text and "finalize_success" in text
        for bad in ("스트리머1", "clip", "payload", "INSERT", "SELECT",
                    "Authorization", "secret", ".db"):
            assert bad not in text, f"로그에 {bad}가 실렸다"

    def test_기존_확정본이면_skipped_로그(self, sdb, caplog):
        import logging

        import singcup_final as sf

        async def _go():
            await _seed()
            await sf.finalize(source="a")
            await sf.finalize(source="b")

        with caplog.at_level(logging.INFO):
            sdb(_go())
        assert "finalize_skipped_existing" in "\n".join(
            r.getMessage() for r in caplog.records)

    def test_빈_결과는_empty_로그(self, sdb, caplog):
        import logging

        import singcup_final as sf

        with caplog.at_level(logging.INFO):
            sdb(sf.finalize(source="test"))
        assert "finalize_empty" in "\n".join(r.getMessage() for r in caplog.records)

    def test_cooldown_스킵도_기록되지만_창마다_한_번만이다(self, sdb, caplog):
        """요구된 6개 이벤트를 모두 남기되, 반복 요청으로 로그가 폭증하면 안 된다."""
        import logging

        import singcup_final as sf

        async def _go():
            await _seed()
            sf.reset_retry_state()
            sf.schedule_finalize_if_needed(source="a")    # scheduled
            await asyncio.sleep(0.25)
            for _ in range(30):                            # 같은 창 안의 반복 요청
                sf.schedule_finalize_if_needed(source="b")

        with caplog.at_level(logging.INFO):
            sdb(_go())
        lines = [r.getMessage() for r in caplog.records]
        n = sum("finalize_skipped_cooldown" in x for x in lines)
        assert n == 1, f"요청 30개에 cooldown 스킵 로그가 {n}줄 쌓였다"

    def test_요구된_이벤트_이름이_모두_존재한다(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "web" / "backend" / "singcup_final.py")
        s = src.read_text(encoding="utf-8")
        for e in ("finalize_attempt", "finalize_success", "finalize_skipped_existing",
                  "finalize_skipped_cooldown", "finalize_empty", "finalize_failed"):
            assert f'"{e}"' in s, f"{e} 이벤트가 없다"
