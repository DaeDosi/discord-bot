// 레이아웃 안정성(CLS) 계약 — UI-Q.
//
// ⚠️ 한계: 이 테스트는 **CSS 레이아웃을 증명하지 못한다.** 실제 CLS는 브라우저가
// 렌더링해 봐야 알 수 있고, 그건 headless Chrome + CDP로 따로 측정했다
// (기준본 768px 0.6998 → 수정본 0.0195, 1440px 0.3378 → 0.0007).
// 여기서 막으려는 것은 그 측정 결과를 만들어 낸 **구조가 조용히 원복되는 것**뿐이다.
// 브라우저 QA를 이 파일로 대체하지 말 것.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");
const PAGE = "app/stats/page.tsx";

// ── 전이 상태의 높이 예약 ───────────────────────────────────────────────────
test("로딩·오류·빈 상태·정상이 같은 최소 높이 위에 놓인다", () => {
  const s = read(PAGE);
  // 로딩에만 높이를 주면 짧은 빈 상태로 바뀔 때 위로 당겨져 문제가 방향만 바꿔 재발한다
  // (실측으로 겪었다: 빈 상태 0.0365 → 0.0807).
  const i = s.indexOf('min-h-[calc(100svh-220px)]');
  assert.ok(i > -1, "전이 상태의 최소 높이 예약이 사라졌다");
  const around = s.slice(i, i + 400);
  assert.ok(around.includes("{loading ? ("),
    "최소 높이가 네 상태를 감싸는 위치에 있지 않다");
});

test("최소 높이는 vh가 아니라 svh를 쓴다", () => {
  const s = read(PAGE);
  // 모바일 주소창이 접혔다 펴질 때 vh는 값이 바뀌어 그 자체가 또 다른 이동을 만든다.
  assert.ok(s.includes("100svh"), "svh를 써야 주소창 변화에 흔들리지 않는다");
  assert.ok(!/min-h-\[calc\(100vh-/.test(s), "vh 기반 최소 높이가 남아 있다");
});

// ── 라이브 집계 칩의 자리 예약 ──────────────────────────────────────────────
test("collectedLabel이 없어도 칩이 자리를 차지한다", () => {
  const s = read(PAGE);
  const i = s.indexOf('min-w-[14rem]');
  assert.ok(i > -1, "칩의 폭 바닥(min-width) 계약이 사라졌다");
  const block = s.slice(Math.max(0, i - 400), i + 700);
  // 조건부 렌더로 되돌아가면(= 값이 있을 때만 span을 그리면) 자리 예약이 사라진다.
  assert.ok(!/\{collectedLabel && tab !== "singcup" &&/.test(s),
    "칩이 다시 조건부 렌더로 돌아갔다 — 나타날 때 줄이 늘어난다");
  assert.ok(block.includes("invisible"), "값이 없을 때 자리를 비워 두지 않는다");
  assert.ok(!block.includes("hidden\""), "hidden은 자리까지 없애 원래 문제로 되돌아간다");
});

test("칩 폭은 고정 px이 아니라 min-width 계약이다", () => {
  const s = read(PAGE);
  // 고정 폭은 날짜 길이·글꼴 배율에 취약하다(실측: 플레이스홀더 185px < 실제 230px).
  assert.ok(/min-w-\[\d+rem\]/.test(s), "rem 기반 min-width여야 확대에서 함께 커진다");
  assert.ok(!/w-\[\d+px\]/.test(s.slice(s.indexOf("라이브 집계") - 600,
                                        s.indexOf("라이브 집계") + 200)),
    "칩에 고정 px 폭이 들어갔다");
});

test("빈 칩은 스크린리더에 노출되지 않고 초점도 받지 않는다", () => {
  const s = read(PAGE);
  const i = s.indexOf('min-w-[14rem]');
  const block = s.slice(i, i + 800);
  assert.ok(block.includes("aria-hidden={collectedLabel ? undefined : true}"),
    "값이 없을 때 aria-hidden이 붙지 않는다");
  // title도 값이 있을 때만 — 빈 칩에 툴팁이 뜨면 거짓 정보가 된다.
  assert.ok(block.includes("title={collectedLabel"), "빈 칩에 title이 남아 있다");
  // 링크·버튼·tabIndex가 없어야 탭 순서가 데이터 도착 전후로 달라지지 않는다.
  assert.ok(!/tabIndex/.test(block), "칩에 tabIndex가 생겼다");
  assert.ok(!/<(a|button)\b/.test(block), "칩 안에 초점 받는 요소가 생겼다");
});

test("빈 값이 사용자에게 날짜처럼 읽히지 않는다", () => {
  const s = read(PAGE);
  const i = s.indexOf('min-w-[14rem]');
  const block = s.slice(i, i + 800);
  // 예전에는 "라이브 집계 0월 0일 오전 00:00"이라는 가짜 날짜로 폭을 맞췄다.
  assert.ok(!/0월 0일|00:00/.test(block), "가짜 날짜가 플레이스홀더로 남아 있다");
  // 값이 없으면 칩 내용을 아예 비운다(폭은 min-width가 지킨다).
  assert.ok(block.includes("{collectedLabel && ("), "값이 없을 때도 내용을 그린다");
});

// ── 기존 계약 회귀 방지 ─────────────────────────────────────────────────────
test("랭킹 탭·검색·LIVE·그룹 표시 계약이 유지된다", () => {
  const s = read(PAGE);
  assert.ok(s.includes("카테고리(게임)별 현황"), "카테고리 표가 사라졌다");
  assert.ok(s.includes('href="/stats/guide"'), "통계 안내 링크가 사라졌다");
  // UI-P에서 고친 카테고리 행 정렬이 되돌아가지 않았는지
  const ci = s.indexOf("카테고리(게임)별 현황");
  assert.ok(!s.slice(ci, ci + 3200).includes("align-top"),
    "카테고리 행이 다시 위쪽 정렬로 돌아갔다");
  // 서비스 소개 요약 블록은 로딩 분기 밖에 있어야 크롤러가 읽는다
  assert.ok(s.indexOf("<StatsAbout />") > s.indexOf("min-h-[calc(100svh-220px)]"),
    "StatsAbout이 전이 상태 컨테이너 안으로 들어갔다");
});
