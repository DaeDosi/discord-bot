/* PUBLIC-UX-1 계약 — 싱드컵 시즌 구조 · 간결한 공개 설명(갱신 방식 + 비공식 고지) · 이름/곡 두 줄.
 *
 * 순수 함수(시즌 해석·갱신 문구·이름 줄)는 값으로, 화면 파일은 **렌더되는 코드**(주석 제거)로
 * 검사한다 — 주석에 옛 문구가 남아 있다고 화면에 보이는 것은 아니다.
 * 실제 레이아웃(줄바꿈·overflow·CLS)은 브라우저 QA가 확인한다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  LEGACY_SEASON, UNOFFICIAL_NOTICE, collectionNotice, fmtUpdatedAt, lastUpdatedText,
  resolveSeasons, stageHint, stagesOf,
} from "./singcupSeason.ts";
import { mergeRanking, teamNames } from "./singcupOfficialMerge.ts";
import type { PikuCampaign, PikuEntry, QualifierGroupRow, QualifierRow } from "./types.ts";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
   .replace(/^\s*\/\/.*$/gm, "");
const OFFICIAL = () => read("app/stats/SingcupOfficial.tsx");

// ── 시즌 ────────────────────────────────────────────────────────────────────

const camp = (over: Partial<PikuCampaign>): PikuCampaign => ({
  campaign: "final", label: "본선", status: "active", sources: [],
  collectionStartAt: "", collectionEndAt: "", scheduleSource: "", note: "",
  available: true, entryCount: 32, lastCollectedAt: 0, lastPublishedAt: 0, lastResult: "",
  ...over,
});

test("시즌 폴백은 서버 정본과 같은 키·이름이다", () => {
  const py = read("../backend/singcup_piku_campaigns.py");
  assert.ok(py.includes(`SEASON_2026_GALAXY = "${LEGACY_SEASON.season}"`));
  assert.ok(py.includes(`"label": "${LEGACY_SEASON.label}"`));
  assert.equal(LEGACY_SEASON.label, "2026 싱드컵 갤럭시");
});

test("구 백엔드(seasons 없음)면 폴백 시즌 하나", () => {
  assert.deepEqual(resolveSeasons(undefined), [LEGACY_SEASON]);
  assert.deepEqual(resolveSeasons([]), [LEGACY_SEASON]);
  const two = [{ season: "b", label: "B", campaigns: ["x"] },
               { season: "a", label: "A", campaigns: ["y"] }];
  assert.deepEqual(resolveSeasons(two), two, "서버 순서(최근 먼저)를 그대로 쓴다");
});

test("단계 상태는 서버 값을 쓰고, 없을 때만 폴백한다", () => {
  const s = stagesOf(LEGACY_SEASON, [
    camp({ campaign: "final", stage: "final", stageState: "ended" }),
    camp({ campaign: "qualifier", stage: "qualifier", stageState: "ended" }),
  ]);
  assert.deepEqual(s.map((x) => [x.stage, x.state]), [["final", "ended"], ["qualifier", "ended"]]);
  const legacy = stagesOf(LEGACY_SEASON, null);
  assert.deepEqual(legacy.map((x) => [x.stage, x.state]),
    [["final", "in_progress"], ["qualifier", "ended"]]);
});

test("다른 시즌의 campaign은 섞이지 않는다", () => {
  const other = { season: "2027", label: "2027", campaigns: ["next_final"] };
  const s = stagesOf(other, [
    camp({ campaign: "final", stage: "final", stageState: "in_progress" }),
    camp({ campaign: "next_final", stage: "final", stageState: "upcoming" }),
  ]);
  assert.deepEqual(s.map((x) => x.campaign), ["next_final"]);
  assert.equal(s[0].state, "upcoming");
});

test("탭 문구: 본선 진행 중 / 예선 종료 — '과거 기록'은 없다", () => {
  assert.equal(stageHint("in_progress", "published"), "진행 중");
  assert.equal(stageHint("ended"), "종료");
  assert.equal(stageHint("in_progress", "unpublished"), "준비 중");
  assert.equal(stageHint("in_progress", "error"), "불러오기 실패");
  assert.equal(stageHint("ended", "unpublished"), "종료", "끝난 단계는 공개본 유무와 무관");
  assert.ok(!code(OFFICIAL()).includes("과거 기록"));
  assert.ok(!read("lib/singcupSeason.ts").includes("과거 기록"));
});

// ── 갱신 안내(PUBLIC-UX-1a) — 수집 주기와 공개 반영을 구분한다 ─────────────────

const T = 1789272571;   // 2026-09-13 13:09:31 KST
const MANUAL = { collectionMode: "manual", collectionIntervalMinutes: 0, publicUpdatePolicy: "reviewed" };
const AUTO = { collectionMode: "auto", collectionIntervalMinutes: 60, publicUpdatePolicy: "reviewed" };

test("상태 1 — MANUAL + 자동 공개 꺼짐: 운영자 확인 후 갱신, 주기 약속 없음", () => {
  const s = collectionNotice(MANUAL);
  assert.equal(s, "현재 순위는 운영자 확인 후 갱신됩니다.");
  assert.ok(!/시간마다|분마다|자동/.test(s));
});

test("상태 2 — AUTO_COLLECT + 자동 공개 꺼짐: 약 1시간마다 확인, 검토 후 반영", () => {
  const s = collectionNotice(AUTO);
  assert.equal(s, "약 1시간마다 새 투표 데이터를 확인하며, 검토 후 순위에 반영합니다.");
  assert.ok(!/순위가 .*바뀝니다|매시간 갱신/.test(s), "공개 순위가 매시간 바뀐다고 말하지 않는다");
});

test("상태 3 — AUTO_PUBLISH 모드지만 준비 불가: 서버가 reviewed를 주므로 문구는 상태 2와 같다", () => {
  // 서버 계약: AUTO_PUBLISH_READY=False이면 모드와 무관하게 publicUpdatePolicy="reviewed".
  assert.equal(collectionNotice({ ...AUTO }), collectionNotice(AUTO));
  // 자동 공개가 실제로 허용될 때만 '검토 후'가 빠진다(미래 AUTO-3).
  assert.equal(collectionNotice({ ...AUTO, publicUpdatePolicy: "automatic" }),
               "약 1시간마다 새 투표 데이터를 확인해 순위에 반영합니다.");
});

test("상태 4·5 — 구 백엔드(필드 없음)·campaigns 실패: 모르는 것을 약속하지 않는다", () => {
  assert.equal(collectionNotice(camp({})), "", "구 백엔드");
  assert.equal(collectionNotice(null), "", "응답 실패");
  assert.equal(lastUpdatedText(null), "");
});

test("상태 6 — 마지막 갱신 없음: 갱신 방식만", () => {
  assert.equal(lastUpdatedText({ lastPublishedAt: 0 }), "");
  assert.equal(collectionNotice({ ...MANUAL }), "현재 순위는 운영자 확인 후 갱신됩니다.");
});

test("상태 7·8 — 본선 미공개/예선 종료: 수집하지 않는 단계는 갱신 방식을 적지 않는다", () => {
  assert.equal(collectionNotice({ collectionMode: "none", collectionIntervalMinutes: 0,
                                  publicUpdatePolicy: "none" }), "");
  assert.equal(lastUpdatedText({ lastPublishedAt: T }), "마지막 갱신 9월 13일 오후 1:09");
  assert.equal(fmtUpdatedAt(T), "9월 13일 오후 1:09");
  assert.ok(!lastUpdatedText({ lastPublishedAt: T }).includes("Asia/Seoul"));
});

test("화면은 서버 상태로만 문구를 고르고 모드를 하드코딩하지 않는다", () => {
  const s = code(OFFICIAL());
  assert.ok(s.includes("collectionNotice(finalCampaign)"));
  assert.ok(s.includes("lastUpdatedText(finalCampaign)") && s.includes("lastUpdatedText(qualCampaign)"));
  for (const bad of ["AUTO_COLLECT", "MANUAL", "AUTO_PUBLISH", "publicRefreshMinutes",
                     "약 1시간마다", "운영자 확인 후"]) {
    assert.ok(!s.includes(bad), `화면 코드가 모드·문구를 직접 안다: ${bad}`);
  }
  // 예선(종료)은 갱신 방식을 적지 않는다.
  assert.ok(!s.includes("collectionNotice(qualCampaign)"));
});

test("공통 고지는 한 문장이고 본선·예선에서 한 번씩만 쓴다", () => {
  assert.equal(UNOFFICIAL_NOTICE,
    "공개 투표 데이터를 바탕으로 정리한 비공식 순위이며, 대회 공식 결과와 다를 수 있습니다.");
  const s = OFFICIAL();
  assert.equal((s.match(/\{UNOFFICIAL_NOTICE\}/g) ?? []).length, 2);
  assert.ok(!code(s).includes("대회 공식 결과와 다를 수 있습니다"), "고지 문장을 화면에 복제했다");
});

// ── 공개 설명 ───────────────────────────────────────────────────────────────

test("공개 화면에 운영 내부 설명이 없다", () => {
  const s = code(OFFICIAL());
  for (const bad of [
    "Asia/Seoul)", "(Asia/Seoul", "운영자 제공", "공식 공지로 확인되지 않았습니다",
    "내려받아", "다시 계산한", "마지막 수집", "자동 수집 대상", "비율·승률 수치는",
    "소급 갱신", "PIKU 페이지는", "업데이트 보류", "sourceId", "scheduleSource",
    "collectionStartAt", "note",
  ]) {
    assert.ok(!s.includes(bad), `공개 화면에 내부 설명이 남아 있다: ${bad}`);
  }
});

test("시즌 이름·단계 탭·예선 종료 안내가 화면에 있다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("season.label"), "시즌 이름을 그리지 않는다");
  assert.ok(s.includes('role="tablist"') && s.includes('role="tab"'));
  assert.ok(s.includes("aria-selected={on}") && s.includes("tabIndex={on ? 0 : -1}"));
  assert.ok(s.includes('"ArrowLeft"') && s.includes('"ArrowRight"'), "키보드 좌우 이동이 없다");
  assert.ok(s.includes("focus({ preventScroll: true })"), "탭 전환 때 스크롤이 튄다");
  assert.ok(s.includes("inset 0 -2px 0"), "선택 상태를 색으로만 전한다");
  assert.ok(s.includes("예선 결과는 기록으로 보관됩니다."));
  assert.ok(s.includes("seasons.length > 1 ?"));
  assert.ok(!/new Date\(\s*[a-zA-Z]*\.collection(Start|End)At/.test(s), "날짜 문자열로 단계를 판정한다");
});

test("갱신 안내 줄은 자리를 미리 잡고 비어 있으면 읽히지 않는다", () => {
  const s = OFFICIAL();
  assert.ok(/min-h-8[^"]*sm:min-h-4"\s*\n\s*role="status" aria-hidden=\{finalPolicy \|\| finalLast \? undefined : true\}/.test(s));
  assert.ok(/min-h-4 [^"]*" role="status"\s*\n\s*aria-hidden=\{qualLast \? undefined : true\}/.test(s));
  assert.ok(s.includes('className="whitespace-nowrap">{finalLast}'), "마지막 갱신이 중간에서 끊긴다");
});

// ── 이름 줄 fixture ─────────────────────────────────────────────────────────

test("개인: 이름 하나", () => {
  assert.deepEqual(teamNames({ displayName: "유람 Yuram", memberNames: [] }), ["유람 Yuram"]);
});

test("대표 + 팀원 1명 / 3명: 대표 먼저, 원본 순서 보존", () => {
  assert.deepEqual(teamNames({ displayName: "가", memberNames: ["나"] }), ["가", "나"]);
  assert.deepEqual(teamNames({ displayName: "조별하", memberNames: ["김니디", "슈향", "이 선"] }),
                   ["조별하", "김니디", "슈향", "이 선"]);
});

test("대표가 members에도 있으면 한 번만", () => {
  assert.deepEqual(teamNames({ displayName: "조별하", memberNames: ["김니디", "조별하", "슈향"] }),
                   ["조별하", "김니디", "슈향"]);
  assert.deepEqual(teamNames({ displayName: " 조별하 ", memberNames: ["조별하", "", "  "] }),
                   ["조별하"]);
});

test("긴 한글 이름·한글+영문 혼합 이름을 자르지 않는다", () => {
  const long = "아주아주아주아주긴한글닉네임입니다정말로길어요";
  assert.deepEqual(teamNames({ displayName: long, memberNames: ["Hello 세계 World"] }),
                   [long, "Hello 세계 World"]);
});

/** 운영 본선 공개본(2026-09-13 GET)의 실제 팀 표기. 공식 명단에서는 대표자가 뒤에 있는 경우를 섞는다. */
const REAL_TEAMS: [string, string[]][] = [
  ["조별하", ["조별하", "김니디", "슈향", "이 선"]],
  ["슨아", ["슨아", "미사키 하루"]],
  ["한 유 월", ["한 유 월", "RuriHana"]],
  ["므므네 mumune", ["므므네 mumune", "아일라 Iyla"]],
  ["초슈야", ["초슈야", "흑지로"]],
  ["PROJECT8", ["PROJECT8", "맥문동"]],
  ["공 운", ["공 운", "시 키 Siki", "김 이 든"]],
  ["치카치카 쵸케", ["치카치카 쵸케", "코네코토 스야"]],
  ["유레이 UREI", ["유레이 UREI", "이루네 IRUNE", "온하얀 ONHAYAN", "하나빈 HANAVIN"]],
];

test("실제 본선 팀: 대표자 먼저·전원·원본 순서·중복 0 (공식 표기에서 대표자가 뒤여도)", () => {
  REAL_TEAMS.forEach(([lead, members], i) => {
    const ids = members.map((_, k) => `t${i}m${k}`);
    // 공식 명단은 대표자를 **마지막**에 두고, 대표자 이름을 한 번 더 넣은 오염 입력도 섞는다.
    const official = [...members.slice(1).map((n, k) => ({ channelId: ids[k + 1], channelName: n })),
                      { channelId: ids[0], channelName: lead }] as QualifierRow[];
    const rows = [{ teamNumber: i + 1, members: official } as QualifierGroupRow];
    const ranking: PikuEntry[] = [{ rank: i + 1, channelId: ids[0], name: lead, thumbnailUrl: "",
      sourceRank: i + 1, teamMembers: members.join(", "), songTitle: "곡", artistName: "가수" }];
    const [m] = mergeRanking(rows, ranking, { mixed: true });
    const names = teamNames(m);
    assert.equal(names[0], lead, `${lead}: 대표자가 첫 번째가 아니다`);
    assert.deepEqual(new Set(names), new Set(members), `${lead}: 팀원이 빠졌다`);
    assert.equal(names.length, members.length, `${lead}: 중복이 있다`);
    // 대표자 뒤 순서는 공식 명단의 원본 순서 그대로다.
    assert.deepEqual(names.slice(1), members.slice(1), `${lead}: 원본 순서가 바뀌었다`);
  });
});

test("곡·원곡자가 비어도 행과 이름은 남는다", () => {
  const ranking: PikuEntry[] = [{
    rank: 1, channelId: "c1", name: "대표", thumbnailUrl: "", sourceRank: 1,
    teamMembers: "대표, 팀원", songTitle: "", artistName: "",
  }];
  const rows: (QualifierRow | QualifierGroupRow)[] = [{
    teamNumber: 7,
    members: [
      { channelId: "c2", channelName: "팀원" } as QualifierRow,
      { channelId: "c1", channelName: "대표" } as QualifierRow,
    ],
  } as QualifierGroupRow];
  const [m] = mergeRanking(rows, ranking, { mixed: true });
  assert.deepEqual(teamNames(m), ["대표", "팀원"], "공식 표기에서 대표가 뒤여도 대표가 먼저");
  assert.equal(m.songTitle, "");
  assert.equal(m.rank, 1);
});
