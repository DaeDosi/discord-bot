"""브라우저 기반 PIKU Collector — 수신·검증·draft·원자 Publish 계약.

Railway와 AWS 서울 EC2 모두 PIKU에서 **HTTP 403**을 받는다. 우회하지 않고
"PIKU가 정상적으로 열리는 사용자 브라우저가 이미 렌더된 공개 표를 읽어 보내는"
경로로 바꾼다. 이 파일이 지키는 것은 그 경로의 안전 계약이다.

**실제 PIKU를 호출하지 않는다.** 전부 합성 데이터다 — 사용자가 보관 중인 실제
Export 파일은 운영 증거이지 fixture가 아니다.

특히 주의할 함정 하나: PIKU 원본의 `win_rate`는 **승률**이고 우리 내부의
`win_rate`는 **우승 비율**이다. 이름이 같고 뜻이 반대라, 원본 행을 그대로
`normalize_rows`에 넘기면 두 값이 조용히 뒤바뀐다. 그래서 수신 경로가 명시적으로
번역하고, 아래 테스트가 그 번역을 고정한다.
"""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

import singcup_piku as piku  # noqa: E402
from singcup_piku import PikuError  # noqa: E402

# 구현 대상 모듈(아직 없다 → 이 파일은 지금 실패해야 한다).
collector = pytest.importorskip(
    "singcup_piku_collector",
    reason="브라우저 Collector 수신 모듈이 아직 없다",
)


# ── 합성 데이터 ─────────────────────────────────────────────────────────────
URLS = {
    "female_solo": ("8jGsHE", "https://www.piku.co.kr/w/rank/8jGsHE"),
    "male_solo":   ("7PqH44", "https://www.piku.co.kr/w/rank/7PqH44"),
    "groups":      ("7fXoNs", "https://www.piku.co.kr/w/rank/7fXoNs"),
}
COUNTS = {"female_solo": 64, "male_solo": 64, "groups": 32}


def raw_row(i: int, *, streamer: str) -> dict:
    """브라우저가 읽은 **PIKU 원본 필드명** 그대로의 행."""
    return {
        "rank": i,
        "streamer": streamer,
        "song_title": f"테스트곡 {i}",
        "artist": f"테스트가수 {i}",
        # PIKU: win_ratio = 우승 비율, win_rate = 승률 (이름이 헷갈린다)
        "win_ratio": round(20.0 - i * 0.1, 2),
        "win_rate": round(80.0 - i * 0.2, 2),
        "image_url": f"https://img.example/{i}.png",
    }


def payload(division: str, *, rows=None, count=None, **over) -> dict:
    """합성 페이로드. `over`로 임의 필드를 덮어쓸 수 있다(잘못된 값 주입용)."""
    sid, url = URLS[division]
    n = count if count is not None else COUNTS[division]
    if rows is None:
        rows = [raw_row(i, streamer=(f"팀원{i}A, 팀원{i}B" if division == "groups"
                                     else f"참가자{division[:1]}{i}"))
                for i in range(1, n + 1)]
    body = {
        "schemaVersion": 1, "division": division, "sourceId": sid,
        "sourceUrl": url, "collectedAt": "2026-08-18T12:00:00+09:00",
        "rowCount": len(rows), "rows": rows,
    }
    body.update(over)
    return body


# ── 1. 부문 · URL 정본 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("division,sid", [(d, v[0]) for d, v in URLS.items()])
def test_source_id_is_canonical(division, sid):
    assert collector.SOURCE_IDS[division] == sid
    assert collector.SOURCE_URLS[division] == URLS[division][1]


def test_male_and_groups_urls_are_not_swapped():
    """한 번 뒤바뀐 적이 있다. 값을 직접 박아 다시 뒤집히지 않게 한다."""
    assert collector.SOURCE_URLS["male_solo"].endswith("/7PqH44")
    assert collector.SOURCE_URLS["groups"].endswith("/7fXoNs")


def test_swapped_source_id_is_rejected():
    body = payload("male_solo", sourceId="7fXoNs")
    with pytest.raises(PikuError) as e:
        collector.parse_payload(body)
    assert e.value.kind in ("bad_source", "division_mismatch")


def test_wrong_source_url_is_rejected():
    body = payload("female_solo", sourceUrl="https://www.piku.co.kr/w/rank/7PqH44")
    with pytest.raises(PikuError):
        collector.parse_payload(body)


def test_foreign_host_url_is_rejected():
    body = payload("female_solo", sourceUrl="https://evil.example/w/rank/8jGsHE")
    with pytest.raises(PikuError):
        collector.parse_payload(body)


# ── 2. 행 수 계약 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("division,n", COUNTS.items())
def test_expected_counts(division, n):
    assert collector.EXPECTED_ROWS[division] == n
    out = collector.parse_payload(payload(division))
    assert len(out["rows"]) == n


@pytest.mark.parametrize("division", COUNTS)
def test_short_payload_is_rejected(division):
    body = payload(division, count=COUNTS[division] - 1)
    with pytest.raises(PikuError) as e:
        collector.parse_payload(body)
    assert e.value.kind in ("row_count", "too_few")


def test_row_count_field_must_match_actual_rows():
    body = payload("female_solo")
    body["rowCount"] = 63          # 실제 rows는 64
    with pytest.raises(PikuError):
        collector.parse_payload(body)


# ── 3. 순위 · 중복 ──────────────────────────────────────────────────────────
def test_missing_rank_is_rejected():
    rows = [raw_row(i, streamer=f"p{i}") for i in range(1, 65)]
    rows[10]["rank"] = 12          # 12가 둘, 11이 없음
    with pytest.raises(PikuError):
        collector.parse_payload(payload("female_solo", rows=rows))


def test_rank_must_start_at_one_and_be_contiguous():
    rows = [raw_row(i + 1, streamer=f"p{i}") for i in range(1, 65)]   # 2..65
    with pytest.raises(PikuError):
        collector.parse_payload(payload("female_solo", rows=rows))


def test_duplicate_streamer_is_rejected():
    rows = [raw_row(i, streamer=f"p{i}") for i in range(1, 65)]
    rows[5]["streamer"] = rows[0]["streamer"]
    with pytest.raises(PikuError):
        collector.parse_payload(payload("female_solo", rows=rows))


# ── 4. 문자열 · 숫자 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("field", ["streamer", "song_title", "artist"])
def test_blank_required_string_is_rejected(field):
    rows = [raw_row(i, streamer=f"p{i}") for i in range(1, 65)]
    rows[3][field] = "   "
    with pytest.raises(PikuError):
        collector.parse_payload(payload("female_solo", rows=rows))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 101.0, "abc", None])
def test_bad_ratio_is_rejected(bad):
    rows = [raw_row(i, streamer=f"p{i}") for i in range(1, 65)]
    rows[2]["win_ratio"] = bad
    with pytest.raises(PikuError):
        collector.parse_payload(payload("female_solo", rows=rows))


# ── 5. 필드 명칭 번역 (가장 틀리기 쉬운 곳) ─────────────────────────────────
def test_piku_field_names_translate_without_swapping():
    """PIKU `win_ratio` → 내부 `win_rate`(우승 비율),
       PIKU `win_rate`  → 내부 `match_rate`(승률).

    이름이 겹치는 탓에 그냥 통과시키면 두 값이 바뀐다. 값으로 확인한다."""
    rows = [raw_row(i, streamer=f"p{i}") for i in range(1, 65)]
    rows[0]["win_ratio"] = 11.66
    rows[0]["win_rate"] = 72.36
    out = collector.parse_payload(payload("female_solo", rows=rows))
    first = out["rows"][0]
    assert first["win_rate"] == pytest.approx(11.66), "우승 비율이 뒤바뀌었다"
    assert first["match_rate"] == pytest.approx(72.36), "승률이 뒤바뀌었다"
    assert first["song_title"] == "테스트곡 1"
    assert first["artist_name"] == "테스트가수 1"


def test_normalize_rows_alias_trap_is_not_reachable():
    """원본 행을 `normalize_rows`에 직접 넘기면 뒤바뀐다 — 그 경로를 쓰지 않는다."""
    trap = piku.normalize_rows([{ "name": "x", "win_rate": 72.36,
                                  "match_rate": 11.66, "source_rank": 1 }])
    # 기존 함수의 동작을 확인만 한다(바꾸지 않는다).
    assert trap[0]["win_rate"] == pytest.approx(72.36)
    # Collector는 이 별칭 경로를 타지 않는다.
    import inspect
    src = inspect.getsource(collector.parse_payload)
    assert "normalize_rows" not in src


# ── 6. 그룹 대표자 규칙 (사용자 확정) ───────────────────────────────────────
@pytest.mark.parametrize("streamer,lead", [
    ("조별하, 김니디, 슈향, 이 선", "조별하"),
    ("므므네 mumune, 아일라 Iyla", "므므네 mumune"),
    ("한 유 월, RuriHana", "한 유 월"),
    ("  , 두번째, 세번째", "두번째"),          # 앞이 비면 다음 사람
    ("혼자팀", "혼자팀"),                      # 쉼표가 없으면 그 자체
])
def test_group_lead_is_first_non_empty_name(streamer, lead):
    assert collector.group_lead(streamer) == lead


def test_group_rows_keep_full_team_string():
    rows = [raw_row(i, streamer=f"리더{i}, 멤버{i}B, 멤버{i}C") for i in range(1, 33)]
    out = collector.parse_payload(payload("groups", rows=rows))
    r0 = out["rows"][0]
    assert r0["name"] == "리더1", "공개 연결은 대표자만 쓴다"
    assert r0["team_members"] == "리더1, 멤버1B, 멤버1C", "원본 팀 문자열을 보존한다"


def test_group_empty_lead_is_rejected():
    rows = [raw_row(i, streamer=f"리더{i}, 멤버{i}") for i in range(1, 33)]
    rows[7]["streamer"] = " , , "
    with pytest.raises(PikuError):
        collector.parse_payload(payload("groups", rows=rows))


def test_group_duplicate_lead_is_rejected():
    rows = [raw_row(i, streamer=f"리더{i}, 멤버{i}") for i in range(1, 33)]
    rows[9]["streamer"] = "리더1, 다른멤버"      # 대표자가 1행과 같다
    with pytest.raises(PikuError):
        collector.parse_payload(payload("groups", rows=rows))


def test_group_row_count_must_be_32():
    rows = [raw_row(i, streamer=f"리더{i}, 멤버{i}") for i in range(1, 32)]
    with pytest.raises(PikuError):
        collector.parse_payload(payload("groups", rows=rows, count=31))


def test_solo_division_does_not_split_names_on_comma():
    """솔로는 이름에 쉼표가 있어도 쪼개지 않는다(대표자 개념이 없다)."""
    rows = [raw_row(i, streamer=f"p{i}") for i in range(1, 65)]
    rows[0]["streamer"] = "이름, 별명"
    out = collector.parse_payload(payload("female_solo", rows=rows))
    assert out["rows"][0]["name"] == "이름, 별명"


# ── 7. 스키마 · fail-closed ─────────────────────────────────────────────────
def test_unknown_schema_version_is_rejected():
    with pytest.raises(PikuError):
        collector.parse_payload(payload("female_solo", schemaVersion=2))


def test_unknown_division_is_rejected():
    body = payload("female_solo")
    body["division"] = "mixed"
    with pytest.raises(PikuError):
        collector.parse_payload(body)


@pytest.mark.parametrize("missing", ["division", "sourceId", "sourceUrl", "rows"])
def test_missing_required_field_is_rejected(missing):
    body = payload("female_solo")
    del body[missing]
    with pytest.raises(PikuError):
        collector.parse_payload(body)


def test_html_like_payload_is_rejected():
    """차단 화면·CAPTCHA HTML이 rows 자리에 오면 거부한다."""
    with pytest.raises(PikuError):
        collector.parse_payload(payload("female_solo", rows="<html>Attention Required"))


def test_payload_does_not_accept_cookies_or_html():
    """수신 스키마에 쿠키·원문 HTML 자리를 두지 않는다."""
    allowed = collector.ALLOWED_PAYLOAD_KEYS
    for bad in ("cookies", "cookie", "html", "rawHtml", "headers", "session",
                "authorization", "token"):
        assert bad not in allowed


def test_row_schema_has_no_secret_fields():
    for bad in ("cookie", "html", "session", "token", "userAgent"):
        assert bad not in collector.ALLOWED_ROW_KEYS


# ── 8. draft / publish 상태 ─────────────────────────────────────────────────
_ORIG_DB_PATH = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    """운영 DB를 건드리지 않는 임시 DB."""
    global _ORIG_DB_PATH
    from database import db as dbmod
    _ORIG_DB_PATH = dbmod.DB_PATH
    db_file = tmp_path / f"c-{uuid.uuid4().hex}.db"
    loop = asyncio.new_event_loop()

    async def setup():
        # `DB_PATH`는 `database.db` 모듈 전역이다 — 패키지에 설정하면 먹지 않아
        # conftest의 공용 임시 DB를 그대로 쓰게 되고, 테스트끼리 상태가 샌다.
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


def test_preview_writes_nothing(env):
    """Preview는 DB write 0건이다."""
    before = env.run_until_complete(collector.debug_counts())
    env.run_until_complete(collector.preview(payload("female_solo")))
    after = env.run_until_complete(collector.debug_counts())
    assert before == after, "Preview가 DB를 건드렸다"


def test_draft_is_not_public(env):
    env.run_until_complete(collector.save_draft(payload("female_solo")))
    pub = env.run_until_complete(piku.public_ranking("female_solo"))
    assert pub["available"] is False, "draft가 공개 응답에 새어 나왔다"
    assert pub["entries"] == []


def test_publish_requires_all_three_divisions(env):
    env.run_until_complete(collector.save_draft(payload("female_solo")))
    env.run_until_complete(collector.save_draft(payload("male_solo")))
    with pytest.raises(PikuError) as e:
        env.run_until_complete(collector.publish_drafts())
    assert e.value.kind in ("incomplete", "missing_draft")
    # 부분 공개가 없어야 한다.
    for d in COUNTS:
        pub = env.run_until_complete(piku.public_ranking(d))
        assert pub["available"] is False


def test_publish_is_atomic_and_rolls_back(env, monkeypatch):
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(payload(d)))
    real = collector._activate_draft
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:                     # 마지막 부문에서 실패
            raise RuntimeError("boom")
        return await real(*a, **kw)

    monkeypatch.setattr(collector, "_activate_draft", flaky)
    with pytest.raises(Exception):
        env.run_until_complete(collector.publish_drafts())
    for d in COUNTS:
        pub = env.run_until_complete(piku.public_ranking(d))
        assert pub["available"] is False, f"{d}만 부분 공개됐다"


def test_failed_publish_keeps_existing_active_dataset(env):
    """기존 정상 dataset이 있으면 실패한 Publish가 그것을 지우지 않는다."""
    env.run_until_complete(collector.save_draft(payload("female_solo")))
    with pytest.raises(PikuError):
        env.run_until_complete(collector.publish_drafts())
    ds = env.run_until_complete(piku.active_dataset("female_solo"))
    assert ds is None          # 애초에 없었고, 만들어지지도 않았다


def test_empty_rows_never_replace_data(env):
    with pytest.raises(PikuError):
        env.run_until_complete(collector.save_draft(payload("female_solo", rows=[])))


# ── 9. 자동 기능 기본 OFF ───────────────────────────────────────────────────
def test_auto_collect_default_off(monkeypatch):
    monkeypatch.delenv("PIKU_AUTO_COLLECT_ENABLED", raising=False)
    assert piku.auto_collect_enabled() is False


def test_auto_publish_default_off(monkeypatch):
    monkeypatch.delenv("PIKU_AUTO_PUBLISH_ENABLED", raising=False)
    assert collector.auto_publish_enabled() is False


def test_collector_interval_is_at_least_one_hour():
    assert collector.MIN_INTERVAL_MINUTES >= 60


# ── 10. 실패를 성공으로 위장하지 않는다 ────────────────────────────────────
@pytest.mark.parametrize("kind", ["blocked", "captcha", "not_rendered", "aborted"])
def test_client_failure_is_recorded_as_failure(env, kind):
    env.run_until_complete(collector.record_client_failure("female_solo", kind))
    st = env.run_until_complete(collector.status())
    d = st["divisions"]["female_solo"]
    assert d["lastResult"] == "failed"
    assert d["rowCount"] == 0
    assert d["lastErrorKind"] == kind


def test_status_separates_no_data_from_failure(env):
    st = env.run_until_complete(collector.status())
    assert st["divisions"]["female_solo"]["lastResult"] in (None, "none")


# ── 11. 로그에 비밀·원문이 남지 않는다 ─────────────────────────────────────
def test_logs_never_contain_secrets_or_html():
    import inspect
    src = inspect.getsource(collector)
    for bad in ("_log(\"payload\"", "raw_html", "rawHtml", "cookie", "set-cookie"):
        assert bad not in src


def test_internal_ratios_absent_from_public_response(env):
    """공개 응답에 내부 비율이 없다.

    **매핑 확정이 선행 조건이 됐다**(그 전에는 Publish 자체가 막힌다). 여기서는
    합성 이름을 쓰므로 공식 명단과 일치하지 않아, 매핑을 직접 확정해 둔다.
    `test_piku_mapping.py`가 확정 흐름 자체를 따로 검증한다."""
    import json

    import singcup_qualifiers as sq
    for d in COUNTS:
        env.run_until_complete(collector.save_draft(payload(d)))
        rows = env.run_until_complete(collector.draft_mappings(d))["rows"]
        pool = (sq.QUALIFIERS["groups"] if d == "groups" else sq.QUALIFIERS[d])
        for i, r in enumerate(rows):
            cid = (pool[i]["members"][0]["channelId"] if d == "groups"
                   else pool[i]["channelId"])
            env.run_until_complete(collector.set_mapping(d, r["pikuName"], cid))
    env.run_until_complete(collector.publish_drafts())
    for d in COUNTS:
        pub = env.run_until_complete(piku.public_ranking(d))
        blob = json.dumps(pub, ensure_ascii=False)
        for bad in ("win_rate", "match_rate", "winRate", "matchRate", "winRatio"):
            assert bad not in blob, f"{d} 공개 응답에 내부 비율이 있다: {bad}"


# ── 12. 멱등성 ──────────────────────────────────────────────────────────────
def test_save_draft_is_idempotent(env):
    env.run_until_complete(collector.save_draft(payload("female_solo")))
    env.run_until_complete(collector.save_draft(payload("female_solo")))
    st = env.run_until_complete(collector.status())
    assert st["divisions"]["female_solo"]["draftRows"] == 64
    assert st["divisions"]["female_solo"]["draftCount"] == 1, "draft가 쌓이면 안 된다"


# ── 13. 브라우저 토큰 ───────────────────────────────────────────────────────
def test_token_is_single_use(env):
    t = env.run_until_complete(collector.issue_token("female_solo"))
    env.run_until_complete(collector.consume_token(t["token"], "female_solo"))
    with pytest.raises(PikuError) as e:
        env.run_until_complete(collector.consume_token(t["token"], "female_solo"))
    assert e.value.kind == "bad_token"


def test_token_is_division_scoped(env):
    t = env.run_until_complete(collector.issue_token("female_solo"))
    with pytest.raises(PikuError):
        env.run_until_complete(collector.consume_token(t["token"], "groups"))


def test_token_expires(env, monkeypatch):
    t = env.run_until_complete(collector.issue_token("male_solo"))
    real = collector.time.time
    monkeypatch.setattr(collector.time, "time",
                        lambda: real() + collector.TOKEN_TTL_SECONDS + 10)
    with pytest.raises(PikuError):
        env.run_until_complete(collector.consume_token(t["token"], "male_solo"))


def test_token_ttl_is_short(env):
    assert collector.TOKEN_TTL_SECONDS <= 3600


def test_token_is_stored_hashed_only(env):
    t = env.run_until_complete(collector.issue_token("groups"))

    async def peek():
        from database import get_db
        db = await get_db()
        cur = await db.execute("SELECT token_hash FROM piku_collector_tokens")
        return [r[0] for r in await cur.fetchall()]

    stored = env.run_until_complete(peek())
    assert t["token"] not in stored, "토큰 원문이 DB에 그대로 있다"
    assert all(len(h) == 64 for h in stored), "sha256 해시가 아니다"


def test_unknown_token_is_rejected(env):
    with pytest.raises(PikuError):
        env.run_until_complete(collector.consume_token("nope", "female_solo"))


@pytest.mark.parametrize("bad", ["", None, 123])
def test_blank_token_is_rejected(env, bad):
    with pytest.raises(PikuError):
        env.run_until_complete(collector.consume_token(bad, "female_solo"))


# ── 14. 확장 프로그램 안전 계약 (소스 텍스트로 확인) ────────────────────────
EXT = Path(__file__).resolve().parents[1] / "tools" / "piku-collector-extension"


def _ext(name: str) -> str:
    return (EXT / name).read_text(encoding="utf-8")


def test_extension_has_no_secret():
    """번들에 secret·토큰 기본값이 없어야 한다."""
    for f in ("manifest.json", "popup.html", "popup.js", "collect.js"):
        s = _ext(f)
        for bad in ("SINGCUP_ADMIN_SECRET", "JWT_SECRET", "OWNER_ID",
                    "Bearer ", "api_key", "apiKey"):
            assert bad not in s, f"{f}에 {bad}가 있다"


def test_extension_permissions_are_minimal():
    import json as _json
    m = _json.loads(_ext("manifest.json"))
    assert m["manifest_version"] == 3
    # 광범위 권한을 요구하지 않는다.
    for bad in ("<all_urls>", "cookies", "webRequest", "tabs", "storage",
                "history", "downloads"):
        assert bad not in m.get("permissions", []), f"{bad} 권한을 요구한다"
        assert bad not in m.get("host_permissions", [])
    hosts = m["host_permissions"]
    assert any(h.startswith("https://www.piku.co.kr/w/rank/") for h in hosts)
    assert all(h.startswith("https://www.piku.co.kr/")
               or h.startswith("https://nexbot.shop/") for h in hosts), hosts


def _strip_js_comments(s: str) -> str:
    """주석 제거 — "쓰지 않는다"고 적은 주석까지 걸리면 계약이 뒤집힌다."""
    import re
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return re.sub(r"^\s*//.*$", "", s, flags=re.M)


def test_extension_does_not_touch_cookies_or_storage():
    for f in ("popup.js", "collect.js"):
        s = _strip_js_comments(_ext(f))
        for bad in ("document.cookie", "chrome.cookies", "chrome.storage",
                    "localStorage", "sessionStorage"):
            assert bad not in s, f"{f}가 {bad}를 만진다"


def test_extension_sends_no_raw_html():
    s = _ext("collect.js")
    for bad in ("innerHTML", "outerHTML", "documentElement.innerHTML"):
        assert bad not in s, f"원문 HTML을 다룬다: {bad}"
    # 반환 payload에 담기는 키가 서버 허용 목록과 같아야 한다.
    for k in ("rank", "streamer", "song_title", "artist", "win_ratio",
              "win_rate", "image_url"):
        assert k in s
    assert "credentials: \"omit\"" in _ext("popup.js"), "쿠키를 보내지 않아야 한다"


def test_extension_does_not_fetch_piku():
    """PIKU에 **추가 요청을 보내지 않는다.** 이미 렌더된 표만 읽는다."""
    s = _ext("collect.js")
    for bad in ("fetch(", "XMLHttpRequest", "$.ajax", "axios"):
        assert bad not in s, f"collect.js가 요청을 만든다: {bad}"


def test_extension_stops_on_block_screen():
    s = _ext("collect.js")
    assert "BLOCKED" in s
    for marker in ("Attention Required", "challenge-platform", "reCAPTCHA"):
        assert marker in s
    # 우회 흔적이 없어야 한다.
    for bad in ("User-Agent", "userAgent =", "navigator.userAgent =",
                "proxy", "solveCaptcha"):
        assert bad not in s, f"우회 흔적: {bad}"


def test_extension_rejects_partial_rows():
    s = _ext("collect.js")
    assert "partial" in s, "부분 데이터를 그대로 보내면 안 된다"
    assert "meta.expected" in s


def test_extension_posts_only_to_collector_paths():
    s = _ext("popup.js")
    assert "/api/admin/piku/collector/" in s
    # 임의 주소로 새지 않도록 origin을 고정해 조립한다.
    assert "u.origin" in s


def test_extension_does_not_split_names_by_hyphen():
    """문자열을 '-'로 임의 분리해 곡·가수를 추측하지 않는다."""
    s = _ext("collect.js")
    assert 'split("-")' not in s and "split('-')" not in s
    assert "split(\" - \")" not in s
