"""Collector 스키마는 **기동 시** 만들어진다 — 요청이 만들지 않는다.

이 파일이 생긴 이유는 실제 결함 하나다. Collector 테이블 세 개를 모듈이 필요할
때 `CREATE TABLE IF NOT EXISTS`로 만들고 있었다. 그래서:

  · Nexadmin의 Collector 탭을 **열기만 해도** 운영 SQLite에 DDL이 나갔다.
  · 조회(GET)가 read-only가 아니었다 — 스윕이 도는 동안의 lock 경합에
    쓰기 요청을 하나 더 얹는다.
  · 토큰 테이블은 첫 토큰 요청 전까지 아예 존재하지 않았다.

계약을 뒤집는다: **세 테이블과 인덱스는 `init_db()`가 만들고, Collector 코드에는
DDL이 한 줄도 없다.** 조회 계열은 스키마도 데이터도 바꾸지 않는다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

collector = pytest.importorskip("singcup_piku_collector")

import database  # noqa: E402

TABLES = ("piku_collector_state", "piku_collector_teams", "piku_collector_tokens")
INDEXES = ("idx_piku_collector_tokens_expires",)


async def _objects(kind: str) -> dict[str, str]:
    db = await database.get_db()
    cur = await db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type=?", (kind,))
    return {r[0]: (r[1] or "") for r in await cur.fetchall()}


async def _snapshot() -> dict:
    """스키마 + 데이터 + 스키마 버전. 조회 전후로 이게 같아야 한다."""
    db = await database.get_db()
    snap: dict = {
        "tables": await _objects("table"),
        "indexes": await _objects("index"),
        "schema_version": (await (await db.execute(
            "PRAGMA schema_version")).fetchone())[0],
        "user_version": (await (await db.execute(
            "PRAGMA user_version")).fetchone())[0],
    }
    for t in TABLES:
        cur = await db.execute(f"SELECT * FROM {t} ORDER BY 1")
        snap[t] = [tuple(r) for r in await cur.fetchall()]
    return snap


# ── 기동 ────────────────────────────────────────────────────────────────────
def test_startup_creates_the_three_collector_tables(db):
    tables = db(_objects("table"))
    for t in TABLES:
        assert t in tables, f"{t}가 기동 시 만들어지지 않았다"


def test_startup_creates_the_token_expiry_index(db):
    indexes = db(_objects("index"))
    for i in INDEXES:
        assert i in indexes, f"{i}가 기동 시 만들어지지 않았다"


def test_startup_is_idempotent_on_an_existing_db(db):
    """이미 스키마가 있는 DB에 startup을 반복해도 성공해야 한다."""
    before = db(_snapshot())
    for _ in range(3):
        db(database.init_db())
    after = db(_snapshot())
    for t in TABLES:
        assert after[t] == before[t], f"{t}의 데이터가 재기동으로 바뀌었다"
    assert after["tables"].keys() == before["tables"].keys()
    assert after["indexes"].keys() == before["indexes"].keys()


def test_startup_inserts_no_rows_into_collector_tables(db):
    """빈 테이블만 만든다 — 토큰·draft·entry 행을 자동으로 넣지 않는다."""
    conn = db(database.get_db())

    async def count(t):
        return (await (await conn.execute(f"SELECT count(*) FROM {t}")).fetchone())[0]

    for t in TABLES:
        assert db(count(t)) == 0, f"{t}에 기동만으로 행이 생겼다"


def test_token_table_exists_before_any_token_request(db):
    """토큰 요청을 **하지 않은** 상태에서도 테이블이 있어야 한다.

    예전에는 첫 발급 요청이 테이블을 만들었다. 그러면 발급 자체가 DDL을 동반한다.
    """
    async def columns():
        conn = await database.get_db()
        cur = await conn.execute("PRAGMA table_info(piku_collector_tokens)")
        return {r[1] for r in await cur.fetchall()}

    assert {"token_hash", "division", "expires_at",
            "used_at", "created_at"} <= db(columns())


# ── 조회는 아무것도 쓰지 않는다 ─────────────────────────────────────────────
def test_status_get_changes_neither_schema_nor_data(db):
    before = db(_snapshot())
    db(collector.status())
    assert db(_snapshot()) == before


def test_mapping_list_get_changes_neither_schema_nor_data(db):
    before = db(_snapshot())
    for division in collector.DIVISIONS:
        db(collector.draft_mappings(division))
        db(collector.official_candidates(division))
    assert db(_snapshot()) == before


def test_publish_preview_get_changes_neither_schema_nor_data(db):
    before = db(_snapshot())
    try:
        db(collector.publish_preview())
    except Exception:
        # 수집본이 없으면 막히는 게 정상이다. 관심사는 write 여부뿐이다.
        pass
    assert db(_snapshot()) == before


def test_payload_preview_changes_neither_schema_nor_data(db):
    """`preview`는 POST지만 검증만 한다 — 형식이 틀려도 DB는 그대로다."""
    before = db(_snapshot())
    with pytest.raises(Exception):
        db(collector.preview({"division": "female_solo"}))
    assert db(_snapshot()) == before


# ── DDL이 모듈에서 사라졌다 ─────────────────────────────────────────────────
def test_collector_module_contains_no_ddl():
    """조회 경로만 고쳐 두면 다음에 또 되살아난다. 모듈 전체를 고정한다.

    검사 대상은 **실제로 실행되는 문자열**이다. 주석은 AST에 남지 않고,
    docstring은 명시적으로 뺀다 — "예전에는 `CREATE TABLE`을 돌렸다, 되살리지
    말 것"이라고 적어 둔 설명까지 위반으로 잡으면 설명을 지우게 된다.
    SQL은 전부 문자열 리터럴이므로 이 검사는 비어 있지 않다.
    """
    import ast

    path = (Path(__file__).resolve().parents[1] / "web" / "backend"
            / "singcup_piku_collector.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = node.body[0] if node.body else None
            if (isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant)
                    and isinstance(doc.value.value, str)):
                docstrings.add(id(doc.value))

    literals = [n.value.lower() for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstrings]
    assert literals, "문자열 리터럴을 하나도 못 찾았다 — 검사가 비어 있다"

    for banned in ("create table", "create index", "create unique index",
                   "alter table", "drop table"):
        offenders = [t for t in literals if banned in t]
        assert not offenders, f"Collector 모듈에 '{banned}'이 남아 있다"


def test_collector_schema_is_declared_in_the_central_migration():
    src = (Path(__file__).resolve().parents[1] / "database" / "db.py").read_text(
        encoding="utf-8")
    for name in TABLES + INDEXES:
        assert name in src, f"{name}이 중앙 migration에 없다"
