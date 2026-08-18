/* PIKU 순위 ↔ 공식 명단 병합 계약.
 *
 * 운영에서 두 가지가 동시에 깨져 있었다(2026-08-19 실측).
 *
 * 1. **곡·가수가 화면에서 사라졌다.** 병합이 PIKU 항목에서 `rank`만 꺼내고
 *    나머지를 통째로 버린 뒤 공식 명단 행만 그렸다. 공식 행의 `songTitle`은
 *    운영자가 직접 입력하는 `singcup_qualifier_songs`에서 오는데 전원 비어 있다.
 *    정작 값은 **PIKU 응답에** 있었다(`songTitle`/`artistName`, 64/64/32 전부).
 *
 * 2. **그룹 14팀이 화면에서 지워졌다.** 공식 명단 색인을 팀의 `members[0]`만으로
 *    만들고, 색인에 없는 PIKU 행을 `filter`로 **버렸다**. PIKU 대표자는 PIKU
 *    문자열의 첫 이름이라 공식 표기 순서와 다를 수 있다. 실측: 32행 중 14행이
 *    `members[0]`과 불일치했고 **전원 전체 멤버 집합에는 있었다.**
 *    지워진 순위: 1, 4, 7, 13, 15, 16, 18, 19, 20, 22, 24, 28, 30, 32.
 *    1위가 지워져서 화면이 2위부터 시작했다.
 *
 * 그래서 이 모듈의 계약은 둘이다 — **PIKU 항목을 끝까지 들고 간다**,
 * **행을 절대 버리지 않는다.**
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import type { PikuEntry, QualifierGroupRow, QualifierRow } from "./types.ts";
import { indexByChannel, memberLine, mergeRanking, songLine,
         teamMembersByChannel } from "./singcupOfficialMerge.ts";

const solo = (id: string, name: string, extra: Partial<QualifierRow> = {}) => ({
  channelId: id, channelName: name, announcedName: name,
  channelImageUrl: `https://img.example/${id}.jpg`,
  clipUid: "", clipTitle: "", clipThumbnailUrl: "", live: null, officialOrder: 1,
  ...extra,
}) as unknown as QualifierRow;

const team = (n: number, members: QualifierRow[]) =>
  ({ teamNumber: n, groupEntryId: `g${n}`, members }) as QualifierGroupRow;

const pk = (rank: number, channelId: string, name: string,
            songTitle = `곡${rank}`, artistName = `가수${rank}`): PikuEntry =>
  ({ rank, channelId, name, thumbnailUrl: "", sourceRank: rank,
     songTitle, artistName });

// ── songLine ────────────────────────────────────────────────────────────────
test("곡과 가수를 ' - '로 잇는다", () => {
  assert.equal(songLine({ songTitle: "ENEMY", artistName: "ImagineDragons" }),
    "ENEMY - ImagineDragons");
  assert.equal(songLine({ songTitle: "HOLLOW HUNGER - OVERLOAD Ⅳ", artistName: "OxT" }),
    "HOLLOW HUNGER - OVERLOAD Ⅳ - OxT");
});

test("한쪽만 있으면 그것만, 둘 다 없으면 빈 문자열", () => {
  assert.equal(songLine({ songTitle: "홀로" }), "홀로");
  assert.equal(songLine({ artistName: "OxT" }), "OxT");
  assert.equal(songLine({}), "");
  assert.equal(songLine({ songTitle: "  ", artistName: " " }), "");
});

// ── 색인 ────────────────────────────────────────────────────────────────────
test("그룹 색인에는 대표자뿐 아니라 **모든 멤버**가 들어간다", () => {
  const rows = [team(1, [solo("a", "가"), solo("b", "나"), solo("c", "다")])];
  const idx = indexByChannel(rows);
  for (const id of ["a", "b", "c"]) {
    assert.ok(idx.has(id), `${id}가 색인에 없다`);
  }
});

test("솔로 부문은 각 행이 자기 channelId로 색인된다", () => {
  const idx = indexByChannel([solo("x", "엑스"), solo("y", "와이")]);
  assert.deepEqual([...idx.keys()].sort(), ["x", "y"]);
});

// ── 병합: 행을 버리지 않는다 ───────────────────────────────────────────────
test("PIKU 대표자가 공식 표기 첫 멤버가 아니어도 행이 남는다", () => {
  // 실제 운영 형태: 팀 표기는 [나, 가]인데 PIKU 대표자는 '가'다.
  const rows = [team(1, [solo("b", "나"), solo("a", "가")])];
  const merged = mergeRanking(rows, [pk(1, "a", "가")]);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].rank, 1);
  assert.equal(merged[0].channelId, "a");
  assert.equal(merged[0].teamNumber, 1);
  assert.ok(merged[0].row, "공식 행이 연결되지 않았다");
});

test("공식 명단에 아예 없는 channelId도 행을 삭제하지 않는다", () => {
  const merged = mergeRanking([solo("a", "가")], [pk(1, "a", "가"), pk(2, "zz", "모르는사람")]);
  assert.equal(merged.length, 2, "매칭 실패 행이 지워졌다");
  assert.equal(merged[1].row, null);
  assert.equal(merged[1].displayName, "모르는사람", "PIKU 이름으로라도 표시해야 한다");
  assert.equal(merged[1].rank, 2);
});

test("그룹 32행이 전부 남고 순위 집합이 정확히 1~32다", () => {
  // 절반은 대표자가 members[0]이 아니게 만든다(운영에서 14/32가 그랬다).
  const rows: QualifierGroupRow[] = [];
  const entries: PikuEntry[] = [];
  for (let i = 1; i <= 32; i++) {
    const lead = solo(`lead${i}`, `대표${i}`);
    const other = solo(`other${i}`, `팀원${i}`);
    rows.push(team(i, i % 2 === 0 ? [lead, other] : [other, lead]));
    entries.push(pk(i, `lead${i}`, `대표${i}`));
  }
  const merged = mergeRanking(rows, entries);
  assert.equal(merged.length, 32);
  assert.deepEqual(merged.map((m) => m.rank),
    Array.from({ length: 32 }, (_, i) => i + 1));
  assert.equal(merged.every((m) => m.row !== null), true, "연결되지 않은 팀이 있다");
});

test("솔로 64행이 전부 남는다", () => {
  const rows = Array.from({ length: 64 }, (_, i) => solo(`s${i}`, `이름${i}`));
  const entries = Array.from({ length: 64 }, (_, i) => pk(i + 1, `s${i}`, `이름${i}`));
  const merged = mergeRanking(rows, entries);
  assert.equal(merged.length, 64);
  assert.deepEqual(merged.map((m) => m.rank), entries.map((e) => e.rank));
});

// ── 병합: 곡·가수를 끝까지 들고 간다 ───────────────────────────────────────
test("PIKU의 곡·가수가 병합 결과에 살아남는다", () => {
  const merged = mergeRanking([solo("a", "유람 Yuram")],
    [pk(1, "a", "유람 Yuram", "ENEMY", "ImagineDragons")]);
  assert.equal(merged[0].songTitle, "ENEMY");
  assert.equal(merged[0].artistName, "ImagineDragons");
  assert.equal(songLine(merged[0]), "ENEMY - ImagineDragons");
});

test("운영자가 입력한 공식 곡 정보가 있으면 그쪽이 이긴다", () => {
  // `singcup_qualifier_songs`는 사람이 고친 값이라 자동 수집본보다 우선한다.
  const merged = mergeRanking(
    [solo("a", "가", { songTitle: "운영자곡", artistName: "운영자가수" })],
    [pk(1, "a", "가", "피쿠곡", "피쿠가수")]);
  assert.equal(merged[0].songTitle, "운영자곡");
  assert.equal(merged[0].artistName, "운영자가수");
});

test("공식 곡 정보가 비어 있으면 PIKU 값으로 채운다", () => {
  const merged = mergeRanking(
    [solo("a", "가", { songTitle: "", artistName: "  " })],
    [pk(1, "a", "가", "피쿠곡", "피쿠가수")]);
  assert.equal(merged[0].songTitle, "피쿠곡");
  assert.equal(merged[0].artistName, "피쿠가수");
});

test("그룹 1위는 조별하 / Jackpot (잭팟) / Block B (블락비)", () => {
  const rows = [team(1, [solo("x", "김니디"), solo("cbh", "조별하")])];
  const merged = mergeRanking(rows,
    [pk(1, "cbh", "조별하", "Jackpot (잭팟)", "Block B (블락비)")]);
  assert.equal(merged[0].displayName, "조별하");
  assert.equal(songLine(merged[0]), "Jackpot (잭팟) - Block B (블락비)");
});

// ── 이름·순위 규칙 ─────────────────────────────────────────────────────────
test("표시 이름은 공식 현재 닉네임을 우선한다(개명 반영)", () => {
  const merged = mergeRanking([solo("a", "새이름")], [pk(1, "a", "옛이름")]);
  assert.equal(merged[0].displayName, "새이름");
});

test("순위를 1부터 다시 매기지 않는다 — 서버 rank를 그대로 쓴다", () => {
  const merged = mergeRanking([solo("a", "가"), solo("b", "나")],
    [pk(7, "a", "가"), pk(9, "b", "나")]);
  assert.deepEqual(merged.map((m) => m.rank), [7, 9]);
});

test("순위 데이터가 없으면 공지 순서로 두고 rank는 null이다", () => {
  const merged = mergeRanking([solo("a", "가"), solo("b", "나")], null);
  assert.deepEqual(merged.map((m) => m.rank), [null, null]);
  assert.deepEqual(merged.map((m) => m.channelId), ["a", "b"]);
});

test("순위가 없을 때 그룹은 대표자 한 명만 세운다(팀당 1행)", () => {
  const merged = mergeRanking([team(1, [solo("a", "가"), solo("b", "나")])], null);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].channelId, "a");
});

// ── 상위 카드 ───────────────────────────────────────────────────────────────
test("상위 카드는 병합 결과의 앞 5개이고 필터로 줄어들지 않는다", () => {
  const rows: QualifierGroupRow[] = [];
  const entries: PikuEntry[] = [];
  for (let i = 1; i <= 10; i++) {
    // 전부 대표자가 members[0]이 아니다 — 예전 코드라면 0개가 남는다.
    rows.push(team(i, [solo(`other${i}`, `팀원${i}`), solo(`lead${i}`, `대표${i}`)]));
    entries.push(pk(i, `lead${i}`, `대표${i}`));
  }
  const top = mergeRanking(rows, entries).slice(0, 5);
  assert.deepEqual(top.map((m) => m.rank), [1, 2, 3, 4, 5]);
});

// ── 비율 비노출 ─────────────────────────────────────────────────────────────
test("병합 결과에 비율 관련 필드가 없다", () => {
  const merged = mergeRanking([solo("a", "가")], [pk(1, "a", "가")]);
  const keys = Object.keys(merged[0]);
  for (const bad of ["winRate", "matchRate", "win_rate", "match_rate", "winRatio"]) {
    assert.ok(!keys.includes(bad), `${bad}가 병합 결과에 있다`);
  }
});


/* ── UI-X: 그룹 전체 멤버 ──────────────────────────────────────────────────
 * 그룹 랭킹은 대표자만 보여 주고 나머지 팀원이 화면에서 사라졌다. PIKU 응답에는
 * 대표자 이름 하나뿐이고, 팀 전체는 **공식 qualifiers의 `members[]`**에 있다
 * (실측: 그룹 1위 대표자 조별하는 teamNumber 3에 속하고 그 팀의 공식 표기 순서는
 * 김니디 · 슈향 · 이 선 · 조별하 — 대표자가 **마지막**이다).
 *
 * 그래서 계약은: 대표자는 PIKU가 정하고, 나머지 멤버는 그 대표자가 속한 공식 팀에서
 * 대표자를 빼서 만든다. **공식 배열의 첫 멤버를 대표자로 다시 뽑지 않는다.**
 */
test("그룹 대표자를 제외한 나머지 멤버가 나온다", () => {
  // 공식 순서는 대표자가 마지막 — 운영 데이터와 같은 배치다.
  const rows = [team(3, [solo("kid", "김니디"), solo("sh", "슈향"),
                         solo("ls", "이 선"), solo("cbh", "조별하")])];
  const merged = mergeRanking(rows,
    [pk(1, "cbh", "조별하", "Jackpot (잭팟)", "Block B (블락비)")]);
  assert.equal(merged[0].displayName, "조별하");
  assert.equal(merged[0].channelId, "cbh", "대표 프로필이 조별하가 아니다");
  assert.deepEqual(merged[0].memberNames, ["김니디", "슈향", "이 선"]);
  assert.equal(memberLine(merged[0]), "김니디 · 슈향 · 이 선");
  assert.equal(songLine(merged[0]), "Jackpot (잭팟) - Block B (블락비)");
});

test("대표자 이름이 멤버 줄에 중복되지 않는다", () => {
  const rows = [team(1, [solo("a", "가"), solo("b", "나")])];
  const merged = mergeRanking(rows, [pk(1, "a", "가")]);
  assert.deepEqual(merged[0].memberNames, ["나"]);
  assert.ok(!memberLine(merged[0]).includes("가"));
});

test("공식 memberOrder가 반대여도 대표자는 PIKU 것을 쓴다", () => {
  const fwd = [team(1, [solo("a", "가"), solo("b", "나"), solo("c", "다")])];
  const rev = [team(1, [solo("c", "다"), solo("b", "나"), solo("a", "가")])];
  for (const rows of [fwd, rev]) {
    const m = mergeRanking(rows, [pk(1, "b", "나")]);
    assert.equal(m[0].displayName, "나");
    assert.equal(m[0].channelId, "b");
    assert.deepEqual([...m[0].memberNames].sort(), ["가", "다"]);
  }
});

test("멤버가 대표자 한 명뿐인 팀은 멤버 줄이 없다", () => {
  // 실측: 32팀 중 1팀은 멤버가 1명이다.
  const merged = mergeRanking([team(9, [solo("solo1", "혼자")])],
    [pk(1, "solo1", "혼자")]);
  assert.deepEqual(merged[0].memberNames, []);
  assert.equal(memberLine(merged[0]), "");
});

test("빈 이름·중복 이름은 멤버 줄에서 빠진다", () => {
  const rows = [team(1, [solo("a", "가"), solo("b", "나"), solo("c", "나"),
                         solo("d", "  ")])];
  const merged = mergeRanking(rows, [pk(1, "a", "가")]);
  assert.deepEqual(merged[0].memberNames, ["나"]);
});

test("솔로 부문은 멤버 줄이 생기지 않는다", () => {
  for (const rows of [[solo("x", "엑스")], [solo("y", "와이")]]) {
    const m = mergeRanking(rows, [pk(1, rows[0].channelId, rows[0].channelName)]);
    assert.deepEqual(m[0].memberNames, [], "솔로에 멤버가 붙었다");
    assert.equal(memberLine(m[0]), "");
  }
});

test("공식 팀을 못 찾아도 행과 이름은 남고 멤버 줄만 비운다", () => {
  const merged = mergeRanking([team(1, [solo("a", "가")])],
    [pk(1, "zz", "모르는대표")]);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].displayName, "모르는대표");
  assert.equal(merged[0].row, null);
  assert.deepEqual(merged[0].memberNames, []);
});

test("teamMembersByChannel은 모든 멤버를 팀 전체에 연결한다", () => {
  const rows = [team(1, [solo("a", "가"), solo("b", "나")]),
                team(2, [solo("c", "다")])];
  const m = teamMembersByChannel(rows);
  assert.equal(m.get("a")?.length, 2);
  assert.equal(m.get("b")?.length, 2);
  assert.equal(m.get("c")?.length, 1);
});

test("그룹 32행에서 멤버 줄이 대표자를 빼고 만들어진다", () => {
  const rows: QualifierGroupRow[] = [];
  const entries: PikuEntry[] = [];
  for (let i = 1; i <= 32; i++) {
    rows.push(team(i, [solo(`m${i}a`, `멤버${i}A`), solo(`lead${i}`, `대표${i}`)]));
    entries.push(pk(i, `lead${i}`, `대표${i}`));
  }
  const merged = mergeRanking(rows, entries);
  assert.equal(merged.length, 32);
  assert.deepEqual(merged.map((x) => x.rank),
    Array.from({ length: 32 }, (_, i) => i + 1));
  for (const x of merged) {
    assert.equal(x.memberNames.length, 1);
    assert.ok(!x.memberNames.includes(x.displayName));
  }
});

// ── memberLine ──────────────────────────────────────────────────────────────
test("멤버 구분자는 가운뎃점이다", () => {
  assert.equal(memberLine({ memberNames: ["가", "나", "다"] }), "가 · 나 · 다");
  assert.equal(memberLine({ memberNames: [] }), "");
  assert.equal(memberLine({ memberNames: ["가"] }), "가");
});
