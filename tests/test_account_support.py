"""계정 설정·회원탈퇴·수정 요청 (ACCOUNT-SUPPORT).

지키려는 계약:

1. **불완전한 탈퇴를 성공으로 표시하지 않는다.** 정책이 확정되지 않은 데이터는
   지우지 않고, 응답이 그 사실을 명시한다(`status="blocked_pending_policy"`).
2. **본인만** 요청할 수 있고, 재확인을 통과해야 한다.
3. 수정 요청은 **서버가** 검증한다(길이·URL·이메일·분류).
4. **이메일과 본문을 로그에 출력하지 않는다.**
5. 중복 제출은 **DB가** 막는다.
"""
import inspect
import json

import account as acct
import pytest
import support

import database

#: 테스트용 소금. **길이·형식 규칙을 만족하는 값**이어야 접수가 열린다.
TEST_SALT = "test-salt-0123456789abcdef"


@pytest.fixture
def adb(db, monkeypatch):
    """수정 요청은 소금이 설정돼야 접수된다(fail-closed).

    이 픽스처는 **정상 설정 상태**를 만든다. 미설정·잘못된 값의 동작은
    아래 `TestSalt`가 소금을 직접 지우고 확인한다.
    """
    async def _clear():
        c = await database.get_db()
        for t in ("account_deletion_requests", "correction_requests"):
            try:
                await c.execute(f"DELETE FROM {t}")
            except Exception:
                pass
        await c.commit()
    db(_clear())
    support.reset_state()
    monkeypatch.setenv(support.SALT_ENV, TEST_SALT)
    return db


UID = "123456789012345678"


# ── 1) 탈퇴가 실제로 차단된다 ───────────────────────────────────────────────

def test_기본값에서_삭제가_꺼져_있다(monkeypatch):
    monkeypatch.delenv("ACCOUNT_DELETION_ENABLED", raising=False)
    assert acct.deletion_enabled() is False


def test_모든_데이터_종류가_정책_미확정이다():
    """하나라도 delete로 바꾸려면 개인정보처리방침과 대조해야 한다."""
    assert all(s["policy"] == "pending_policy" for s in acct.DATA_CLASSES)
    assert len(acct.blocked_classes()) == len(acct.DATA_CLASSES)


def test_탈퇴_요청이_접수되지만_완료가_아니다(adb):
    res = adb(acct.request_deletion(UID, "tester"))
    assert res["ok"] is True
    assert res["status"] == "blocked_pending_policy", "완료로 꾸미면 안 된다"
    assert res["deleted"] == {}
    assert res["blocked"], "무엇이 막혔는지 알려 줘야 한다"
    assert "이메일로만" in res["reason"]


def test_탈퇴_요청이_아무것도_지우지_않는다(adb):
    """실제로 행이 남아 있는지 확인한다 — 응답만 믿지 않는다."""
    async def _seed():
        c = await database.get_db()
        await c.execute(
            "INSERT OR REPLACE INTO user_points (guild_id, user_id, points)"
            " VALUES (1, ?, 500)", (int(UID),))
        await c.execute(
            "INSERT OR REPLACE INTO user_xp (guild_id, user_id, xp, level)"
            " VALUES (1, ?, 100, 3)", (int(UID),))
        await c.commit()

    async def _count():
        c = await database.get_db()
        a = await (await c.execute(
            "SELECT COUNT(*) n FROM user_points WHERE user_id=?", (int(UID),))).fetchone()
        b = await (await c.execute(
            "SELECT COUNT(*) n FROM user_xp WHERE user_id=?", (int(UID),))).fetchone()
        return a["n"], b["n"]

    adb(_seed())
    before = adb(_count())
    adb(acct.request_deletion(UID, "tester"))
    assert adb(_count()) == before, "탈퇴 요청이 데이터를 지웠다"


def test_요청이_감사_가능하게_기록된다(adb):
    adb(acct.request_deletion(UID, "tester", reason="더 이상 쓰지 않음"))
    last = adb(acct.recent_request(UID))
    assert last and last["status"] == "blocked_pending_policy"

    async def _row():
        c = await database.get_db()
        return dict(await (await c.execute(
            "SELECT * FROM account_deletion_requests ORDER BY id DESC LIMIT 1"
        )).fetchone())
    r = adb(_row())
    assert r["user_id"] == UID
    assert json.loads(r["blocked_classes"]), "막힌 종류가 남아야 한다"


def test_플래그만_켜도_정책_미확정이면_지우지_않는다(adb, monkeypatch):
    """`ACCOUNT_DELETION_ENABLED=true` 하나로 열리면 이중 관문이 아니다."""
    monkeypatch.setenv("ACCOUNT_DELETION_ENABLED", "true")
    res = adb(acct.request_deletion(UID, "tester"))
    assert res["status"] == "blocked_pending_policy"
    assert res["deleted"] == {}


def test_인벤토리가_종류와_건수만_준다(adb):
    async def _seed():
        c = await database.get_db()
        await c.execute(
            "INSERT OR REPLACE INTO user_points (guild_id, user_id, points)"
            " VALUES (1, ?, 777)", (int(UID),))
        await c.commit()
    adb(_seed())
    inv = adb(acct.inventory(UID))
    blob = json.dumps(inv, ensure_ascii=False)
    assert "777" not in blob, "값 자체를 API로 흘리면 안 된다"
    pts = [c for c in inv["classes"] if c["label"] == "서버 포인트"][0]
    assert pts["count"] == 1
    assert pts["policy"] == "pending_policy"
    # 개인 식별자를 담지 않는 것도 화면에서 구분할 수 있어야 한다
    assert any("방문 집계" in n["label"] for n in inv["notPersonal"])


def test_잘못된_사용자_id로도_500이_나지_않는다(adb):
    inv = adb(acct.inventory("not-a-number"))
    assert inv["total"] == 0


def test_삭제_실행_함수가_pending은_건드리지_않는다(adb):
    async def _seed():
        c = await database.get_db()
        await c.execute(
            "INSERT OR REPLACE INTO user_points (guild_id, user_id, points)"
            " VALUES (1, ?, 1)", (int(UID),))
        await c.commit()
    adb(_seed())
    out = adb(acct._execute_deletion(UID))
    assert out == {}, "pending_policy 종류를 지웠다"


# ── 2) 수정 요청 검증 ───────────────────────────────────────────────────────

def _body(**over):
    b = {"category": "wrong_metric", "clipRef": "abc123",
         "description": "하트 수가 실제와 다릅니다. 확인 부탁드립니다."}
    b.update(over)
    return b


def test_정상_접수(adb):
    res = adb(support.submit(_body(), submitter="s1"))
    assert res["ok"] is True and res["id"] > 0


def test_분류가_닫힌_목록이다(adb):
    for bad in ("", "nope", None, 5, "<script>"):
        with pytest.raises(support.SupportError):
            adb(support.submit(_body(category=bad), submitter="s1"))


def test_필수_항목이_비면_거절한다(adb):
    with pytest.raises(support.SupportError):
        adb(support.submit(_body(clipRef=""), submitter="s1"))
    with pytest.raises(support.SupportError):
        adb(support.submit(_body(description=""), submitter="s1"))


def test_설명이_너무_짧으면_거절한다(adb):
    with pytest.raises(support.SupportError) as e:
        adb(support.submit(_body(description="짧음"), submitter="s1"))
    assert "이상" in str(e.value)


def test_길이_상한이_서버에서_강제된다(adb):
    with pytest.raises(support.SupportError):
        adb(support.submit(_body(description="가" * (support.MAX_DESCRIPTION + 1)),
                           submitter="s1"))
    with pytest.raises(support.SupportError):
        adb(support.submit(_body(clipRef="a" * (support.MAX_CLIP_REF + 1)),
                           submitter="s1"))


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "http://example.com", "ftp://x/y",
    "data:text/html,<script>", "//evil.com", "example.com",
])
def test_근거_주소는_https만_통과한다(adb, bad):
    with pytest.raises(support.SupportError):
        adb(support.submit(_body(evidenceUrl=bad), submitter="s1"))


def test_https_주소는_통과한다(adb):
    res = adb(support.submit(_body(evidenceUrl="https://chzzk.naver.com/clips/x"),
                             submitter="s1"))
    assert res["ok"]


@pytest.mark.parametrize("bad", ["not-an-email", "a@b", "@b.com", "a b@c.com",
                                 "a@b." , "a@@b.com"])
def test_이메일_형식을_검증한다(adb, bad):
    with pytest.raises(support.SupportError):
        adb(support.submit(_body(email=bad), submitter="s1"))


def test_이메일은_선택_항목이다(adb):
    assert adb(support.submit(_body(email=""), submitter="s1"))["ok"]


def test_HTML과_스크립트가_그대로_저장되지_않는다(adb):
    adb(support.submit(_body(
        description="<script>alert(1)</script> 하트 수가 이상합니다"), submitter="s1"))

    async def _row():
        c = await database.get_db()
        return dict(await (await c.execute(
            "SELECT description FROM correction_requests ORDER BY id DESC LIMIT 1"
        )).fetchone())
    d = adb(_row())["description"]
    assert "<script>" not in d and "</script>" not in d
    # 이스케이프하지 않는다 — React가 텍스트로 렌더하므로 &lt;가 보이면 안 된다
    assert "&lt;" not in d


def test_제로폭_문자와_제어문자를_걷어낸다(adb):
    adb(support.submit(_body(description="하트​수가 이상합니다 확인요청"),
                       submitter="s1"))

    async def _row():
        c = await database.get_db()
        return (await (await c.execute(
            "SELECT description FROM correction_requests ORDER BY id DESC LIMIT 1"
        )).fetchone())["description"]
    d = adb(_row())
    assert "​" not in d and "" not in d


# ── 3) 중복·rate limit ──────────────────────────────────────────────────────

def test_같은_내용_중복_제출은_DB가_막는다(adb):
    adb(support.submit(_body(), submitter="s1"))
    with pytest.raises(support.SupportError) as e:
        adb(support.submit(_body(), submitter="s1"))
    assert "이미 접수" in str(e.value)


def test_다른_사람의_같은_제보는_막지_않는다(adb):
    """같은 문제의 다중 제보는 중복이 아니다."""
    adb(support.submit(_body(), submitter="s1"))
    assert adb(support.submit(_body(), submitter="s2"))["ok"]


def test_필드_경계가_다르면_다른_요청이다(adb):
    """구분자 없이 이으면 clip='ab',desc='c'와 clip='a',desc='bc'가 충돌한다."""
    a = support._dedupe_key("s", "other", "ab", "c" * 20)
    b = support._dedupe_key("s", "other", "a", "bc" + "c" * 18)
    assert a != b


def test_rate_limit이_적용된다(adb):
    for i in range(support.RATE_LIMIT):
        adb(support.submit(_body(clipRef=f"clip{i}"), submitter="rl"))
    with pytest.raises(support.SupportError) as e:
        adb(support.submit(_body(clipRef="clipX"), submitter="rl"))
    assert "너무 잦" in str(e.value)


def test_rate_limit은_제출자별이다(adb):
    for i in range(support.RATE_LIMIT):
        adb(support.submit(_body(clipRef=f"a{i}"), submitter="u1"))
    assert adb(support.submit(_body(clipRef="b0"), submitter="u2"))["ok"]


# ── 4) 로그에 개인정보가 없다 ───────────────────────────────────────────────

def test_로그에_이메일과_본문이_찍히지_않는다(adb, capsys):
    email = "very-unique-address@example.com"
    secret = "여기에만 있는 아주 특이한 문장입니다"
    adb(support.submit(_body(description=secret, email=email), submitter="s1"))
    out = capsys.readouterr().out
    assert email not in out, "로그에 이메일이 찍혔다"
    assert secret not in out, "로그에 본문이 찍혔다"
    # 접수 사실과 길이는 남는다
    assert "correction_received" in out
    assert "descriptionLength" in out


def test_메타가_서버_한도를_준다():
    lim = support.limits()
    assert lim["description"] == support.MAX_DESCRIPTION
    assert lim["descriptionMin"] == support.MIN_DESCRIPTION
    assert [c["key"] for c in support.categories()] == list(support.CATEGORIES)


def test_공개_응답에_내부_처리_상태가_없다(adb):
    res = adb(support.submit(_body(), submitter="s1"))
    # 라우터가 id만 돌려준다(아래는 모듈 반환값이라 status가 있지만, 라우터에서 걸러진다)
    from routers import account_router as ar
    src = inspect.getsource(ar.submit_correction)
    assert 'return {"ok": True, "id": res["id"]}' in src
    assert res["id"] > 0


def test_보관_기간_문구를_임의로_만들지_않았다():
    """방침에 없는 기간을 코드가 지어내면 그게 곧 잘못된 고지가 된다."""
    src = inspect.getsource(support)
    for bad in ("6개월 보관", "1년 보관", "30일 후 삭제", "90일"):
        assert bad not in src
    assert "이 모듈이 정하지 않는다" in src


# ── 5) SUPPORT_HASH_SALT 계약 (fail-closed) ────────────────────────────────
#
# 저장소에 고정된 공용 소금을 두지 않는다. 공개된 소금은 소금이 아니다 —
# 후보 IP를 넣어 돌려 보면 해시가 맞춰진다. 프로세스마다 임의 소금을 만드는
# 선택지도 있었지만, 중복 차단이 DB 유니크 인덱스에 **영속**되므로 소금이
# 재시작·replica마다 달라지면 중복 차단이 조용히 무력화된다. 그래서 fail-closed다.

class TestSalt:
    def test_기본값이_코드에_박혀_있지_않다(self):
        src = inspect.getsource(support)
        # 예전에 쓰던 하드코딩 기본값이 되살아나면 안 된다.
        assert 'os.getenv("SUPPORT_HASH_SALT", ' not in src
        assert 'getenv(SALT_ENV, ' not in src
        assert "nexbot-support" in src, "차단 목록에는 남아 있어야 한다"
        # 차단 목록 안에 있는지(기본값이 아니라)
        assert "nexbot-support" in support._SALT_BLOCKLIST

    def test_미설정이면_접수를_막는다(self, adb, monkeypatch):
        monkeypatch.delenv(support.SALT_ENV, raising=False)
        assert support.salt_configured() is False
        with pytest.raises(support.SupportUnavailable):
            adb(support.submit(_body(), submitter="s1"))

    @pytest.mark.parametrize("bad", [
        "", "   ", "\t\n",
        "short", "nexbot-support", "changeme", "CHANGEME", "secret",
        "your-secret-here", "0" * 16, "test",
    ])
    def test_빈값과_예제값은_유효한_secret이_아니다(self, adb, monkeypatch, bad):
        monkeypatch.setenv(support.SALT_ENV, bad)
        assert support.salt_configured() is False
        with pytest.raises(support.SupportUnavailable):
            adb(support.submit(_body(), submitter="s1"))

    def test_정상_secret이면_접수된다(self, adb, monkeypatch):
        monkeypatch.setenv(support.SALT_ENV, TEST_SALT)
        assert support.salt_configured() is True
        assert adb(support.submit(_body(), submitter="s1"))["ok"]

    def test_길이_하한이_강제된다(self, adb, monkeypatch):
        monkeypatch.setenv(support.SALT_ENV, "a" * (support.SALT_MIN_LENGTH - 1))
        assert support.salt_configured() is False
        monkeypatch.setenv(support.SALT_ENV, "a" * support.SALT_MIN_LENGTH)
        assert support.salt_configured() is True

    def test_미설정_거절이_검증보다_먼저다(self, adb, monkeypatch):
        """입력이 조금이라도 처리되기 전에 끊는다."""
        monkeypatch.delenv(support.SALT_ENV, raising=False)
        # 분류가 틀렸어도 SupportError가 아니라 SupportUnavailable이 나와야 한다
        with pytest.raises(support.SupportUnavailable):
            adb(support.submit(_body(category="없는분류"), submitter="s1"))

    def test_소금이_응답과_로그에_나오지_않는다(self, adb, monkeypatch, capsys):
        monkeypatch.setenv(support.SALT_ENV, TEST_SALT)
        res = adb(support.submit(_body(), submitter="s1"))
        out = capsys.readouterr().out
        assert TEST_SALT not in out, "로그에 소금이 찍혔다"
        assert TEST_SALT not in json.dumps(res, ensure_ascii=False)

    def test_미설정_안내에_설정_단서가_없다(self, adb, monkeypatch):
        """어떤 점이 틀렸는지 알리면 설정값 추측 단서가 된다."""
        monkeypatch.setenv(support.SALT_ENV, "short")
        try:
            adb(support.submit(_body(), submitter="s1"))
        except support.SupportUnavailable as e:
            msg = str(e)
        assert support.SALT_ENV not in msg
        assert "길이" not in msg and "16" not in msg

    def test_원문_IP가_DB에_저장되지_않는다(self, adb, monkeypatch):
        """제출자 식별자는 `client_ip.resolve()['id']`(날짜 회전 해시)뿐이다."""
        monkeypatch.setenv(support.SALT_ENV, TEST_SALT)
        ip_like = "203.0.113.77"
        adb(support.submit(_body(), submitter="abc123def456"))

        async def _dump():
            c = await database.get_db()
            rows = await (await c.execute("SELECT * FROM correction_requests")).fetchall()
            return "\n".join(json.dumps(dict(r), ensure_ascii=False, default=str)
                             for r in rows)

        dump = adb(_dump())
        assert ip_like not in dump
        assert "abc123def456" not in dump, "제출자 식별자 원본도 저장하지 않는다"
        assert TEST_SALT not in dump

    def test_라우터가_원문_IP를_만들지_않는다(self):
        """`client_ip.resolve`는 해시만 준다 — 라우터가 IP를 다시 뽑으면 안 된다."""
        from routers import account_router as ar
        src = inspect.getsource(ar._submitter_key)
        assert '.get("id")' in src
        for bad in ("x-forwarded-for", "request.client.host", '.get("ip")'):
            assert bad not in src, f"라우터가 원문 IP를 다룬다: {bad}"
        # 소금은 한 곳에서만 섞는다
        assert "SALT" not in src and "hashlib" not in src

    def test_다중_요청_rate_limit이_동작한다(self, adb, monkeypatch):
        monkeypatch.setenv(support.SALT_ENV, TEST_SALT)
        for i in range(support.RATE_LIMIT):
            adb(support.submit(_body(clipRef=f"rl{i}"), submitter="ratelimited"))
        with pytest.raises(support.SupportError):
            adb(support.submit(_body(clipRef="rlX"), submitter="ratelimited"))

    def test_소금을_바꾸면_중복_이력이_이어지지_않는다(self, adb, monkeypatch):
        """한계를 계약으로 못박는다 — 소금 회전은 곧 중복 이력 초기화다."""
        monkeypatch.setenv(support.SALT_ENV, TEST_SALT)
        adb(support.submit(_body(), submitter="s1"))
        with pytest.raises(support.SupportError):
            adb(support.submit(_body(), submitter="s1"))
        monkeypatch.setenv(support.SALT_ENV, "another-salt-fedcba9876543210")
        support.reset_state()
        assert adb(support.submit(_body(), submitter="s1"))["ok"], \
            "소금이 바뀌면 같은 내용도 새 요청으로 접수된다(문서화된 한계)"

    def test_메타는_막혀_있어도_응답한다(self, monkeypatch):
        """화면이 폼 대신 안내를 그릴 수 있어야 한다."""
        monkeypatch.delenv(support.SALT_ENV, raising=False)
        assert support.categories() and support.limits()
        assert support.salt_configured() is False


# ── 6) 회원탈퇴 차단 재검증 (E) ─────────────────────────────────────────────

class TestDeletionBlocked:
    async def _counts(self):
        c = await database.get_db()
        out = {}
        for t, col in (("user_points", "user_id"), ("user_xp", "user_id"),
                       ("warnings", "user_id"), ("chzzk_verifications", "user_id")):
            r = await (await c.execute(
                f"SELECT COUNT(*) n FROM {t} WHERE {col}=?", (int(UID),))).fetchone()
            out[t] = int(r["n"] or 0)
        return out

    async def _seed_all(self):
        c = await database.get_db()
        uid = int(UID)
        await c.execute("INSERT OR REPLACE INTO user_points (guild_id, user_id, points)"
                        " VALUES (1,?,900)", (uid,))
        await c.execute("INSERT OR REPLACE INTO user_xp (guild_id, user_id, xp, level)"
                        " VALUES (1,?,50,2)", (uid,))
        await c.execute("INSERT INTO warnings (guild_id, user_id, mod_id, reason,"
                        " created_at) VALUES (1,?,2,'테스트',0)", (uid,))
        await c.execute("INSERT OR REPLACE INTO chzzk_verifications (guild_id, user_id,"
                        " verified_at) VALUES (1,?,0)", (uid,))
        await c.commit()

    def test_재시도해도_데이터가_삭제되지_않는다(self, adb):
        """3회 반복 요청 후에도 네 테이블의 행 수가 그대로여야 한다."""
        adb(self._seed_all())
        before = adb(self._counts())
        assert before == {"user_points": 1, "user_xp": 1, "warnings": 1,
                          "chzzk_verifications": 1}
        for _ in range(3):
            res = adb(acct.request_deletion(UID, "tester"))
            assert res["status"] == "blocked_pending_policy"
            assert res["deleted"] == {}
        assert adb(self._counts()) == before, "반복 요청이 데이터를 지웠다"

    def test_요청_기록만_생성된다(self, adb):
        adb(self._seed_all())
        for _ in range(3):
            adb(acct.request_deletion(UID, "tester"))

        async def _n():
            c = await database.get_db()
            r = await (await c.execute(
                "SELECT COUNT(*) n FROM account_deletion_requests WHERE user_id=?",
                (UID,))).fetchone()
            return int(r["n"])
        # 중복 요청 정책: **막지 않고 매번 기록한다.**
        # 사용자가 다시 눌렀다는 사실 자체가 감사에 필요한 정보이고, 요청은
        # 파괴적이지 않아 중복이 해를 끼치지 않는다(rate limit이 폭주는 막는다).
        assert _n and adb(_n()) == 3
        assert adb(self._counts())["user_points"] == 1

    def test_중복_요청_정책이_코드에_적혀_있다(self):
        src = inspect.getsource(acct.request_deletion)
        assert "INSERT INTO account_deletion_requests" in src
        assert "ON CONFLICT" not in src, "중복 요청을 막지 않는 것이 현재 정책이다"

    def test_rate_limit이_폭주를_막는다(self, adb):
        """중복 기록을 허용하는 대신 속도는 제한한다."""
        from routers import account_router as ar
        src = inspect.getsource(ar.delete_account)
        assert "support._rate_limit" in src
        assert "account_delete:" in src

    def test_UI가_완료로_읽힐_문구를_쓰지_않는다(self):
        from pathlib import Path
        p = (Path(__file__).resolve().parents[1] / "web" / "frontend" / "app"
             / "settings" / "page.tsx")
        s = p.read_text(encoding="utf-8")
        assert "계정과 데이터는 아직 삭제되지 않았습니다" in s
        assert "탈퇴 요청을 접수" in s
        assert "회원탈퇴 요청" in s, "제목이 '요청'임을 밝혀야 한다"
        # 완료로 읽힐 문구가 blocked 경로에 없어야 한다
        blocked = s.split('result.status === "completed" ? (')[1].split(") : (")[1]
        for bad in ("탈퇴 처리가 완료", "삭제되었습니다", "탈퇴가 완료"):
            assert bad not in blocked, f"차단 경로에 완료 문구가 있다: {bad}"

    def test_실행_함수는_여전히_잠겨_있다(self, adb, monkeypatch):
        """정책 확정 전까지 `_execute_deletion`이 실제로 지우면 안 된다."""
        monkeypatch.setenv("ACCOUNT_DELETION_ENABLED", "true")
        adb(self._seed_all())
        before = adb(self._counts())
        assert adb(acct._execute_deletion(UID)) == {}
        assert adb(self._counts()) == before
