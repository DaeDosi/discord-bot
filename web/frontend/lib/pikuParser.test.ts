/* PIKU 확장 파서 — **실제 운영 DOM 구조**에 대한 회귀 테스트.
 *
 * 2026-08-18 canary에서 남성 솔로 1위 행이 `parse_failed`로 막혔다. 원인은 하나가
 * 아니라 셋이었다:
 *
 *   1. 이름·곡·가수가 `<strong data-no="...">[유람 Yuram] ENEMY - ImagineDragons</strong>`
 *      **한 문자열**인데, 파서는 자식 요소(`small/span/p/div`)나 줄바꿈에서 읽으려
 *      했다. `textContent`를 이미 `\s+ → " "`로 접어 두었으므로 줄바꿈은 **영원히
 *      나오지 않는다** — 곡·가수가 항상 빈 문자열이 됐다.
 *   2. 썸네일이 `<img>`가 아니라 CSS `background-image`다.
 *   3. 백분율을 `tds.slice(1)` 전체에서 찾아, 순위 추이 SVG의 숫자가 섞일 수 있었다.
 *
 * 이 파일은 **실제로 배포되는 `collect.js`를 그대로 실행**한다(소스 문자열 검사가
 * 아니다). 최소 DOM을 만들어 `vm`에서 평가하며, 파서가 이 가짜 DOM이 지원하지 않는
 * 선택자를 쓰면 **던져서** 조용히 통과하지 않도록 했다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SRC = readFileSync(
  new URL("../../../tools/piku-collector-extension/collect.js", import.meta.url),
  "utf8",
);

// ── 최소 DOM ────────────────────────────────────────────────────────────────
type El = {
  tagName: string;
  children: El[];
  attrs: Record<string, string>;
  style: { backgroundImage: string };
  _text: string;
  textContent: string;
  querySelector(sel: string): El | null;
  querySelectorAll(sel: string): El[];
};

function el(tagName: string, opts: Partial<{
  text: string; children: El[]; attrs: Record<string, string>; bg: string;
}> = {}): El {
  const node: El = {
    tagName: tagName.toUpperCase(),
    children: opts.children || [],
    attrs: opts.attrs || {},
    style: { backgroundImage: opts.bg || "" },
    _text: opts.text || "",
    get textContent(): string {
      return this._text + this.children.map((c) => c.textContent).join(" ");
    },
    querySelector(sel: string) { return this.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel: string) { return descendants(this).filter((n) => matches(n, sel)); },
  };
  return node;
}

function descendants(n: El): El[] {
  return n.children.flatMap((c) => [c, ...descendants(c)]);
}

/** 파서가 실제로 쓰는 선택자만 지원한다. 모르는 선택자는 던진다. */
function matches(n: El, sel: string): boolean {
  const s = sel.trim();
  if (s === "*") return true;
  if (s === "tr" || s === "img" || s === "svg" || s === "tbody") return n.tagName === s.toUpperCase();
  if (s === "strong[data-no]") return n.tagName === "STRONG" && "data-no" in n.attrs;
  if (s === "table tbody") return n.tagName === "TBODY";
  if (s.includes("_length")) return false; // '보기 개수' 셀렉트 — 이 fixture엔 없다
  throw new Error(`fixture DOM이 모르는 선택자: ${sel}`);
}

type RowSpec = {
  rank?: string;
  strong?: string | null;      // null이면 strong 없음(td[2] fallback 경로)
  nameText?: string;           // strong이 없을 때 td[2]의 텍스트
  winRatio?: string;
  matchRate?: string;
  trend?: string;              // 순위 추이 SVG 안의 숫자
  bg?: string;                 // 썸네일 background-image
  imgSrc?: string;             // <img> 썸네일
  cells?: number;              // 열 개수 조절용
};

function row(spec: RowSpec): El {
  const thumbKids: El[] = [];
  if (spec.imgSrc !== undefined) thumbKids.push(el("img", { attrs: { src: spec.imgSrc } }));
  const inner = el("div", { bg: spec.bg || "" });
  thumbKids.push(inner);

  const nameKids: El[] = [];
  if (spec.strong !== null) {
    nameKids.push(el("strong", { text: spec.strong ?? "", attrs: { "data-no": "24046001" } }));
  }

  const tds: El[] = [
    el("td", { text: spec.rank ?? "1" }),
    el("td", { children: thumbKids }),
    el("td", { text: spec.strong === null ? (spec.nameText || "") : "", children: nameKids }),
    el("td", { text: spec.winRatio ?? "18.90%" }),
    el("td", { text: spec.matchRate ?? "77.22%" }),
    el("td", { children: [el("svg", { text: spec.trend ?? "" })] }),
  ];
  return el("tr", { children: tds.slice(0, spec.cells ?? 6) });
}

function run(sourceId: string, rows: El[], hostname = "www.piku.co.kr") {
  const tbody = el("tbody", { children: rows });
  const table = el("table", { children: [tbody] });
  const body = el("body", { children: [table] });
  const document = {
    body: Object.assign(body, { innerText: "PIKU 랭킹" }),
    querySelector: (s: string) => (body.querySelectorAll(s)[0] || null),
    querySelectorAll: (s: string) => body.querySelectorAll(s),
  };
  const ctx = vm.createContext({
    location: { hostname, pathname: `/w/rank/${sourceId}` },
    document,
    getComputedStyle: (n: El) => ({ backgroundImage: n.style.backgroundImage || "none" }),
    Node: function () {},
  });
  return vm.runInContext(SRC, ctx) as
    { ok: boolean; kind?: string; message?: string; payload?: any };
}

const MALE = "7PqH44";
const FEMALE = "8jGsHE";
const GROUPS = "7fXoNs";

/** 계약 행 수를 채운 정상 fixture. */
function fullRows(n: number, mk: (i: number) => RowSpec = () => ({})): El[] {
  return Array.from({ length: n }, (_, i) =>
    row({ rank: String(i + 1), strong: `[스트리머${i + 1}] 노래${i + 1} - 가수${i + 1}`, ...mk(i) }));
}

// ── 1~3. 단일 strong 문자열 ─────────────────────────────────────────────────
test("남성: 단일 strong 문자열에서 이름·곡·가수를 분리한다", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람 Yuram] ENEMY - ImagineDragons",
                  winRatio: "18.90%", matchRate: "77.22%" });
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  const first = r.payload.rows[0];
  assert.equal(first.streamer, "유람 Yuram");
  assert.equal(first.song_title, "ENEMY");
  assert.equal(first.artist, "ImagineDragons");
  assert.equal(first.win_ratio, 18.9);
  assert.equal(first.win_rate, 77.22);
});

test("여성: 가수에 괄호·Feat.가 있어도 그대로 보존한다", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[아오도라 유키] 홀로 - 정키(Feat.김나영)" });
  const r = run(FEMALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rows[0].streamer, "아오도라 유키");
  assert.equal(r.payload.rows[0].song_title, "홀로");
  assert.equal(r.payload.rows[0].artist, "정키(Feat.김나영)");
});

test("그룹: 대괄호 안 멤버 문자열을 자르지 않고 전체 보존한다", () => {
  const rows = fullRows(32);
  rows[0] = row({ rank: "1",
    strong: "[조별하, 김니디, 슈향, 이 선] Jackpot (잭팟) - Block B (블락비)" });
  const r = run(GROUPS, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  const first = r.payload.rows[0];
  // 대표자만 잘라 보내지 않는다 — 대표자 결정은 서버의 group_lead()가 한다.
  assert.equal(first.streamer, "조별하, 김니디, 슈향, 이 선");
  assert.equal(first.song_title, "Jackpot (잭팟)");
  assert.equal(first.artist, "Block B (블락비)");
});

// ── 4. CSS 썸네일 ───────────────────────────────────────────────────────────
test("<img> 없이 CSS background-image만 있어도 썸네일을 읽는다", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람 Yuram] ENEMY - ImagineDragons",
                  bg: 'url("https://cdn.piku.co.kr/thumb/1.jpg")' });
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rows[0].image_url, "https://cdn.piku.co.kr/thumb/1.jpg");
});

test("따옴표 없는 url(...)도 읽는다", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람 Yuram] ENEMY - ImagineDragons",
                  bg: "url(https://cdn.piku.co.kr/thumb/2.jpg)" });
  const r = run(MALE, rows);
  assert.equal(r.payload.rows[0].image_url, "https://cdn.piku.co.kr/thumb/2.jpg");
});

test("javascript:·data:·blob: 썸네일은 버리고 빈 문자열로 둔다", () => {
  for (const bad of ["url(javascript:alert(1))", "url(data:image/png;base64,AAA)",
                     "url(blob:https://x/y)"]) {
    const rows = fullRows(64);
    rows[0] = row({ rank: "1", strong: "[유람 Yuram] ENEMY - ImagineDragons", bg: bad });
    const r = run(MALE, rows);
    assert.equal(r.ok, true, `${bad}에서 파싱이 통째로 실패했다`);
    assert.equal(r.payload.rows[0].image_url, "", `${bad}가 통과했다`);
  }
});

test("썸네일이 아예 없어도 나머지가 정상이면 성공한다", () => {
  const r = run(MALE, fullRows(64));
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rows[0].image_url, "");
});

// ── 5. 순위 추이 SVG 혼입 방지 ──────────────────────────────────────────────
test("순위 추이 SVG의 숫자를 비율로 오인하지 않는다", () => {
  const rows = fullRows(64);
  // 마지막 칸에 '5' 같은 숫자가 있어도 td[3]·td[4]만 써야 한다.
  rows[0] = row({ rank: "1", strong: "[유람 Yuram] ENEMY - ImagineDragons",
                  winRatio: "18.90%", matchRate: "77.22%", trend: "5" });
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rows[0].win_ratio, 18.9);
  assert.equal(r.payload.rows[0].win_rate, 77.22);
});

// ── 6~9. fail-closed ────────────────────────────────────────────────────────
test("대괄호가 없으면 fail-closed", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "유람 Yuram ENEMY - ImagineDragons" });
  const r = run(MALE, rows);
  assert.equal(r.ok, false);
  assert.equal(r.kind, "parse_failed");
});

test("곡·가수 구분자가 없으면 fail-closed", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람 Yuram] ENEMY ImagineDragons" });
  const r = run(MALE, rows);
  assert.equal(r.ok, false);
  assert.equal(r.kind, "parse_failed");
});

test("실제 46위: 곡명에 ' - '가 있으면 **마지막** 구분자가 경계다", () => {
  // 2026-08-18 canary가 찾아낸 실제 반례. 남성 솔로 46위 원문:
  //   [피 네] HOLLOW HUNGER - OVERLOAD Ⅳ - OxT
  // 예전 계약(구분자 2회 이상이면 무조건 거부)은 이 행에서 수집 전체를 막았다.
  // 실제 남성 64행 중 ' - '가 두 번인 행은 이 한 건뿐이고 나머지는 모두 한 번이다.
  const rows = fullRows(64);
  rows[45] = row({ rank: "46", strong: "[피 네] HOLLOW HUNGER - OVERLOAD Ⅳ - OxT" });
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  const at46 = r.payload.rows.find((x: any) => x.rank === 46);
  assert.equal(at46.streamer, "피 네");
  assert.equal(at46.song_title, "HOLLOW HUNGER - OVERLOAD Ⅳ");
  assert.equal(at46.artist, "OxT");
});

test("구분자가 셋 이상이어도 마지막만 경계로 쓴다", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람] A - B - C - D" });
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rows[0].song_title, "A - B - C");
  assert.equal(r.payload.rows[0].artist, "D");
});

test("구분자 하나로 끝나 가수가 없으면 fail-closed", () => {
  // 후행 ' - '는 공백 접힘·trim을 거치면 ' -'로 남아 **구분자가 아니게 된다.**
  // 그래서 '구분자 없음'으로 떨어진다 — 어느 경로든 저장하지 않는 것이 계약이다.
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람] ENEMY - " });
  const r = run(MALE, rows);
  assert.equal(r.ok, false);
  assert.equal(r.kind, "parse_failed");
});

test("구분자가 둘이고 끝이 매달린 붙임표면 마지막 경계로 갈라 통과한다", () => {
  // "[유람] HOLLOW - HUNGER - " → trim 후 "HOLLOW - HUNGER -"
  // 마지막 ' - '는 HOLLOW와 HUNGER 사이이므로 가수는 "HUNGER -"가 된다.
  // 곡·가수 어느 쪽도 비어 있지 않으므로 **계약상 정상**이다. 여기서 더 엄격한
  // 규칙(가수가 붙임표로 끝나면 거부 등)을 만들지 않는다 — 실재하는 이름을
  // 근거 없이 막게 된다.
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람] HOLLOW - HUNGER - " });
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rows[0].song_title, "HOLLOW");
  assert.equal(r.payload.rows[0].artist, "HUNGER -");
});

test("곡 자리가 비면 fail-closed", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람]  - OxT" });
  const r = run(MALE, rows);
  assert.equal(r.ok, false);
  assert.equal(r.kind, "parse_failed");
});

test("붙임표(Ne-Yo)는 구분자가 아니다", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: "[유람] So Sick - Ne-Yo" });
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rows[0].song_title, "So Sick");
  assert.equal(r.payload.rows[0].artist, "Ne-Yo");
});

test("streamer·곡·가수 중 하나라도 비면 fail-closed", () => {
  for (const s of ["[] ENEMY - ImagineDragons", "[유람]  - ImagineDragons",
                   "[유람] ENEMY - "]) {
    const rows = fullRows(64);
    rows[0] = row({ rank: "1", strong: s });
    const r = run(MALE, rows);
    assert.equal(r.ok, false, `'${s}'가 통과했다`);
    assert.equal(r.kind, "parse_failed");
  }
});

test("퍼센트가 비었거나 범위를 벗어나면 fail-closed", () => {
  for (const [wr, mr] of [["", "77.22%"], ["-1%", "77.22%"], ["101%", "77.22%"],
                          ["18.90%", "abc"]]) {
    const rows = fullRows(64);
    rows[0] = row({ rank: "1", strong: "[유람] ENEMY - ImagineDragons",
                    winRatio: wr, matchRate: mr });
    const r = run(MALE, rows);
    assert.equal(r.ok, false, `${wr}/${mr}가 통과했다`);
  }
});

// ── 10. 순위 연속·중복 ──────────────────────────────────────────────────────
test("순위가 중복이면 fail-closed", () => {
  const rows = fullRows(64);
  rows[1] = row({ rank: "1", strong: "[다른사람] 곡 - 가수" });
  const r = run(MALE, rows);
  assert.equal(r.ok, false);
});

test("순위가 1..N 연속이 아니면 fail-closed", () => {
  const rows = fullRows(64);
  rows[63] = row({ rank: "99", strong: "[끝사람] 곡 - 가수" });
  const r = run(MALE, rows);
  assert.equal(r.ok, false);
});

test("같은 원본 행이 중복되면 fail-closed", () => {
  const rows = fullRows(64);
  rows[1] = row({ rank: "2", strong: "[스트리머1] 노래1 - 가수1" });
  const r = run(MALE, rows);
  assert.equal(r.ok, false);
});

// ── 11~12. 부문 계약 ────────────────────────────────────────────────────────
test("여성 64 / 남성 64 / 그룹 32 행 계약", () => {
  assert.equal(run(FEMALE, fullRows(64)).ok, true);
  assert.equal(run(MALE, fullRows(64)).ok, true);
  assert.equal(run(GROUPS, fullRows(32)).ok, true);
  // 행 수가 다르면 부분 데이터를 만들지 않는다.
  assert.equal(run(MALE, fullRows(30)).ok, false);
  assert.equal(run(GROUPS, fullRows(64)).ok, false);
});

test("division과 sourceId는 URL에서 결정되고 서로 어긋나지 않는다", () => {
  const pairs: [string, string][] = [
    [FEMALE, "female_solo"], [MALE, "male_solo"], [GROUPS, "groups"]];
  for (const [id, div] of pairs) {
    const r = run(id, fullRows(div === "groups" ? 32 : 64));
    assert.equal(r.payload.division, div);
    assert.equal(r.payload.sourceId, id);
    assert.equal(r.payload.sourceUrl, `https://www.piku.co.kr/w/rank/${id}`);
  }
});

test("등록되지 않은 주소·호스트에서는 아무것도 읽지 않는다", () => {
  assert.equal(run("ZZZZZZ", fullRows(64)).ok, false);
  assert.equal(run(MALE, fullRows(64), "evil.example.com").ok, false);
});

// ── 14. payload에 원문·비밀이 없다 ─────────────────────────────────────────
test("payload에 쿠키·헤더·원문 HTML이 없다", () => {
  const r = run(MALE, fullRows(64));
  const keys = Object.keys(r.payload).sort();
  assert.deepEqual(keys, ["collectedAt", "division", "rowCount", "rows",
                          "schemaVersion", "sourceId", "sourceUrl"]);
  const rowKeys = Object.keys(r.payload.rows[0]).sort();
  // 정렬 순서 주의: "win_rate" < "win_ratio" (공통 접두사 뒤 e < i).
  assert.deepEqual(rowKeys, ["artist", "image_url", "rank", "song_title",
                             "streamer", "win_rate", "win_ratio"]);
  const blob = JSON.stringify(r.payload);
  for (const bad of ["cookie", "Cookie", "<td", "<tr", "<strong", "innerHTML",
                     "Authorization", "sessionid"]) {
    assert.ok(!blob.includes(bad), `payload에 ${bad}가 들어 있다`);
  }
});

// ── strong이 없을 때의 fallback ─────────────────────────────────────────────
test("strong[data-no]가 없으면 td[2] 텍스트로 fallback한다", () => {
  const rows = fullRows(64);
  rows[0] = row({ rank: "1", strong: null,
                  nameText: "[유람 Yuram] ENEMY - ImagineDragons" });
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rows[0].streamer, "유람 Yuram");
  assert.equal(r.payload.rows[0].song_title, "ENEMY");
});

test("열이 모자란 행은 데이터 행으로 보지 않는다", () => {
  const rows = fullRows(64);
  rows.push(row({ rank: "65", strong: "[광고] x - y", cells: 2 }));
  const r = run(MALE, rows);
  assert.equal(r.ok, true, `실패: ${r.kind} ${r.message}`);
  assert.equal(r.payload.rowCount, 64);
});
