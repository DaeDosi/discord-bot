/* UI-T 화면 계약 — 소스 텍스트로 확인한다(DOM 런타임 의존 없이).
 *
 * 여기서 지키는 것은 "무엇이 화면에 있어야 하는가"가 아니라 **다시 깨지기 쉬운
 * 구조적 약속**이다: 중복 제거가 되돌아오지 않는지, 정책이 뒤섞이지 않는지. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
/** 주석을 걷어낸 코드만 — 주석 문구가 계약을 통과시키면 안 된다. */
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const GA = () => read("app/stats/GroupAnalysis.tsx");
const NAV = () => read("app/stats/StatsNav.tsx");
const CSS = () => read("app/globals.css");
const SUPPORT = () => read("components/SupportMenu.tsx");

// ── 그룹 분석 ───────────────────────────────────────────────────────────────

test("그룹 분석이 카테고리별 스트리머와 태그 검색 사이에 있다", () => {
  const s = NAV();
  const i = (k: string) => s.indexOf(`key: "${k}"`);
  assert.ok(i("category_streamers") > 0 && i("group") > 0 && i("tags") > 0);
  assert.ok(i("category_streamers") < i("group"), "카테고리별 스트리머 다음이어야 한다");
  assert.ok(i("group") < i("tags"), "태그 검색보다 앞이어야 한다");
});

test("그룹 분석은 랭킹 제외 정책을 읽지 않는다", () => {
  // 이 필드를 화면에서 읽는 순간 "공식 그룹이 그룹 분석에서 사라지는" 다른 정책이
  // 조용히 생긴다. 서버도 공개 응답에 넣지 않지만, 화면에서도 막아 둔다.
  const s = code(GA());
  assert.ok(!s.includes("excludeFromRanking"));
  assert.ok(!s.includes("exclude_from_ranking"));
});

test("그룹 분석이 네 상태를 서로 구분한다", () => {
  const s = GA();
  assert.ok(s.includes("그룹 목록을 불러오지 못했습니다"), "오류");
  assert.ok(s.includes("그룹을 불러오는 중"), "로딩");
  assert.ok(s.includes("분석할 그룹이 아직 없습니다"), "그룹 없음");
  assert.ok(s.includes("등록된 멤버가 없습니다"), "빈 그룹");
  assert.ok(s.includes('role="alert"'));
  assert.ok(s.includes("aria-busy"));
});

test("방송 없음과 시청자 0명을 다르게 표시한다", () => {
  const s = GA();
  assert.ok(s.includes("오프라인"), "꺼져 있는 멤버를 0명으로 뭉치지 않는다");
  assert.ok(/m\.live \?/.test(s), "live 플래그로 갈라야 한다");
});

test("모바일은 표가 아니라 카드로 바꾼다", () => {
  const s = GA();
  assert.ok(s.includes("md:hidden"), "좁은 화면 전용 카드");
  assert.ok(s.includes("hidden overflow-x-auto md:block"), "표는 md 이상에서만");
  assert.ok(s.includes("MemberCard") && s.includes("MemberRow"));
});

test("그룹 분석 조작 요소가 터치 계약을 따른다", () => {
  const s = GA();
  assert.ok(s.includes("nb-tap-gap"), "넓어진 히트 영역끼리 벌린다");
  assert.ok((s.match(/nb-tap /g) ?? []).length >= 3);
  assert.ok(s.includes("aria-pressed={active}"), "현재 선택 상태가 노출돼야 한다");
  assert.ok(s.includes('aria-label="그룹 멤버 검색"'));
});

test("멤버 검색 입력에 길이 상한이 있다", () => {
  assert.ok(/maxLength=\{40\}/.test(GA()));
});

test("공용 방송시간 계산을 재사용한다", () => {
  // 복사본을 두면 같은 방송이 화면마다 다른 시간으로 보인다.
  const s = GA();
  assert.ok(s.includes('from "./singcupShared"'));
  assert.ok(!s.includes("function liveDuration"), "자체 구현을 두지 않는다");
});

// ── 전역 scrollbar ─────────────────────────────────────────────────────────

test("scrollbar가 두 엔진 모두에 정의된다", () => {
  const s = CSS();
  assert.ok(s.includes("scrollbar-width: thin"), "Firefox");
  assert.ok(s.includes("scrollbar-color:"), "Firefox 색");
  assert.ok(s.includes("::-webkit-scrollbar-thumb"), "Chromium/WebKit");
  assert.ok(s.includes("::-webkit-scrollbar-track"));
});

test("thumb가 둥글고 hover·active 상태를 갖는다", () => {
  const s = CSS();
  const thumb = s.slice(s.indexOf("::-webkit-scrollbar-thumb {"));
  assert.ok(thumb.slice(0, 300).includes("border-radius: 999px"));
  assert.ok(s.includes("::-webkit-scrollbar-thumb:hover"));
  assert.ok(s.includes("::-webkit-scrollbar-thumb:active"));
});

test("scrollbar를 숨기거나 가짜 화살표를 만들지 않는다", () => {
  const s = code(CSS());
  // 본문·문서 스크롤바를 숨기면 콘텐츠가 더 있다는 유일한 신호가 사라진다.
  // **예외는 LNB 하나뿐**이다(UI-V 요구) — 거기는 메뉴 항목이 신호 역할을 하고,
  // 좁은 기둥에 스크롤바가 생기면 이름이 밀려 잘린다. 스크롤 자체는 살아 있다.
  // 숨김 규칙이 나오는 줄마다 그 줄이 LNB 선택자인지 확인한다.
  for (const line of s.split("\n")) {
    if (/::-webkit-scrollbar\s*\{\s*display:\s*none/.test(line)) {
      assert.ok(line.includes(".nb-shell-nav"),
        `LNB 밖에서 스크롤바를 숨긴다: ${line.trim()}`);
    }
  }
  // `-ms-overflow-style: none`은 블록 안에 있으므로 직전 선택자를 되짚는다.
  let i = s.indexOf("-ms-overflow-style: none");
  while (i !== -1) {
    assert.ok(s.lastIndexOf(".nb-shell-nav", i) > s.lastIndexOf("}", i),
      "LNB 블록 밖에서 -ms-overflow-style을 끄고 있다");
    i = s.indexOf("-ms-overflow-style: none", i + 1);
  }
  // 클릭해도 스크롤되지 않는 가짜 버튼을 만들지 않는다.
  assert.ok(!s.includes("::-webkit-scrollbar-button"));
});

test("scrollbar 폭이 잡을 수 있으면서 과하지 않다", () => {
  const m = CSS().match(/::-webkit-scrollbar\s*\{\s*width:\s*(\d+)px/);
  assert.ok(m, "폭이 정의돼야 한다");
  const w = Number(m![1]);
  assert.ok(w >= 8 && w <= 14, `폭 ${w}px — 8~14px 범위여야 한다`);
});

test("scrollbar-gutter로 레이아웃을 밀지 않는다", () => {
  // 헤더가 viewport 중앙 정렬이라 문서 폭이 바뀌면 검색창 중심이 어긋난다.
  assert.ok(!code(CSS()).includes("scrollbar-gutter"));
});

// ── Footer 제거와 법적 링크 보존 ───────────────────────────────────────────

test("공통 Footer를 어느 페이지도 렌더하지 않는다", () => {
  for (const p of ["app/page.tsx", "app/support/correction/page.tsx",
                   "app/stats/page.tsx", "app/privacy/page.tsx",
                   "app/terms/page.tsx", "app/status/page.tsx"]) {
    assert.ok(!read(p).includes("<Footer />"), `${p}에 Footer가 남아 있다`);
  }
});

test("약관·개인정보처리방침 경로가 SupportMenu에 남아 있다", () => {
  // Footer를 걷어내면서 이 링크들의 유일한 상시 경로가 여기가 됐다.
  const s = SUPPORT();
  assert.ok(s.includes('["/terms", "이용약관"]'));
  assert.ok(s.includes('"개인정보처리방침"') && s.includes('"/privacy"'));
  assert.ok(s.includes("쿠키 정책"));
});

test("법적 페이지 자체는 지우지 않았다", () => {
  assert.ok(read("app/terms/page.tsx").length > 0);
  assert.ok(read("app/privacy/page.tsx").length > 0);
});

// ── 본문 중복 제거 ─────────────────────────────────────────────────────────

test("본문의 큰 제목·설명이 사라지고 h1은 남는다", () => {
  const s = read("app/stats/page.tsx");
  assert.ok(!s.includes("치지직 라이브 방송의 시청자·카테고리 트렌드를 실시간으로 분석합니다."),
    "헤더·sidebar가 이미 말하는 문구를 본문에서 반복하지 않는다");
  assert.ok(s.includes('<h1 className="sr-only">치지직 방송 통계</h1>'),
    "heading 구조와 스크린리더용 제목은 남아야 한다");
});

test("페이지 metadata는 건드리지 않았다", () => {
  const s = read("app/stats/layout.tsx");
  assert.ok(/title/.test(s), "SEO title이 사라지면 안 된다");
});

// ── sidebar 접힘 ───────────────────────────────────────────────────────────

test("sidebar 접힘이 본문 폭을 실제로 돌려준다", () => {
  const s = read("app/stats/page.tsx");
  // UI-T에서는 grid 열 개수를 바꿨다. UI-U에서 3영역 셸(flex)로 옮기면서
  // **메뉴를 DOM에서 빼는 방식**만 남았다 — `hidden`으로 감추면 칸이 남아
  // 본문이 240px을 못 쓴다.
  assert.ok(s.includes("{navOpen && ("), "접으면 DOM에서 빠진다");
  assert.ok(s.includes('className="nb-shell-body"'), "셸이 두 칸을 나눈다");
  assert.ok(s.includes("md:shrink-0"), "메뉴는 고정폭, 본문이 남은 폭을 갖는다");
});

test("sidebar 선택이 저장되고 hydration을 깨지 않는다", () => {
  const s = read("app/stats/page.tsx");
  assert.ok(s.includes("useState(true)"), "초기값은 서버와 같아야 한다");
  assert.ok(/localStorage\.getItem\(STATS_NAV_STORAGE\)/.test(s));
  assert.ok(/localStorage\.setItem\(STATS_NAV_STORAGE/.test(s));
  // 저장값을 초기 state에 바로 넣으면 서버/클라이언트 첫 렌더가 달라진다.
  assert.ok(!/useState\(\s*localStorage/.test(s));
  assert.ok(s.includes("catch"), "시크릿 모드에서 터지면 안 된다");
});

test("햄버거가 가리키는 대상 id가 실제로 존재한다", () => {
  const s = read("app/stats/page.tsx");
  assert.ok(s.includes("controlsId: STATS_NAV_ID"));
  assert.ok(s.includes("id={STATS_NAV_ID}"), "aria-controls가 가리킬 요소가 있어야 한다");
});
