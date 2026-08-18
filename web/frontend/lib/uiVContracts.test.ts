/* UI-V 화면 계약 — 소스 텍스트로 확인한다(DOM 런타임 의존 없이).
 *
 * 여기 담는 것은 이번에 고친 결함이 **같은 모양으로 다시 나지 않게** 하는 약속이다:
 *   · LNB — 스크롤은 되고 스크롤바만 숨긴다 / 접기 토글 없음 / 말줄임 없음
 *   · 스크롤 소유권 — 본문은 window, sidebar만 자기 스크롤
 *   · 프로필 메뉴 — 조상이 잘라 내지 않는다(그게 버튼을 밀어냈다)
 *   · 비공식 인기점수 — 수집만 멈추고 화면·데이터는 남긴다
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
/** 주석을 걷어낸 코드만 — 주석 문구가 계약을 통과시키면 안 된다. */
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const CSS = () => read("app/globals.css");
const NAV = () => read("app/stats/StatsNav.tsx");
const STATS = () => read("app/stats/page.tsx");
const HEADER = () => read("components/SiteHeader.tsx");
const SINGCUP = () => read("app/stats/Singcup.tsx");
const OFFICIAL = () => read("app/stats/SingcupOfficial.tsx");

// ── LNB: 스크롤은 되고 스크롤바만 숨긴다 ────────────────────────────────────

test("LNB는 스크롤 가능하고 스크롤바만 시각적으로 숨긴다", () => {
  const css = code(CSS());
  const nav = css.slice(css.indexOf(".nb-shell-nav {"));
  const block = nav.slice(0, nav.indexOf("}"));
  assert.ok(block.includes("overflow-y: auto"),
    "휠·터치·키보드로 계속 스크롤돼야 한다");
  assert.ok(block.includes("scrollbar-width: none"));
  assert.ok(css.includes(".nb-shell-nav::-webkit-scrollbar { display: none; }"));
  assert.ok(!/overflow(-y)?: hidden/.test(block),
    "overflow:hidden은 아래 항목에 닿을 방법을 없앤다 — 금지");
});

test("전체 문서 스크롤바까지 숨기지 않는다", () => {
  const css = code(CSS());
  // 숨김 규칙은 `.nb-shell-nav`에만 붙어야 한다.
  const hides = css.match(/[^\n]*::-webkit-scrollbar\s*\{\s*display:\s*none/g) ?? [];
  for (const h of hides) {
    assert.ok(h.includes(".nb-shell-nav"),
      `전역 스크롤바를 숨기는 규칙이 있다: ${h.trim()}`);
  }
});

// ── LNB: 정적 섹션 ──────────────────────────────────────────────────────────

test("그룹 접기/펼치기 토글이 없다", () => {
  const nav = code(NAV());
  assert.ok(!nav.includes("openGroups"), "접힘 상태를 들고 있으면 안 된다");
  assert.ok(!nav.includes("toggleGroup"));
  assert.ok(!nav.includes("ChevronDown"), "펼침 화살표가 남아 있으면 안 된다");
});

test("그룹은 구분선 + 제목의 정적 섹션이다", () => {
  const nav = code(NAV());
  assert.ok(nav.includes("<section"), "그룹은 section이어야 한다");
  assert.ok(nav.includes("aria-labelledby"), "제목과 섹션이 연결돼야 한다");
  assert.ok(nav.includes("border-t"), "구분선이 앞 그룹을 닫는다");
  assert.ok(/<h2[^>]*id=\{`nb-lnb-/.test(nav), "그룹 제목은 h2다");
});

test("메뉴 이름에서 통계·랭킹 접미사를 뺐다", () => {
  const nav = NAV();
  for (const bad of ["전체 스트리머 통계", "신규 스트리머 통계", "소형 스트리머 통계",
    "전체 스트리머 랭킹", "신규 스트리머 랭킹", "소형 스트리머 랭킹"]) {
    assert.ok(!nav.includes(`label: "${bad}"`),
      `그룹 제목이 문맥을 주므로 라벨에서 접미사를 반복하지 않는다: ${bad}`);
  }
  for (const good of ["전체 스트리머", "신규 스트리머", "소형 스트리머"]) {
    assert.ok(nav.includes(`label: "${good}"`), `${good} 라벨이 있어야 한다`);
  }
});

test("LNB 라벨에 말줄임을 걸지 않는다", () => {
  const nav = code(NAV());
  assert.ok(!nav.includes("truncate"),
    "메뉴 이름이 잘리면 그 메뉴가 무엇인지 읽을 수 없다");
  assert.ok(!/className="[^"]*overflow-hidden[^"]*"[^>]*>\s*\{t\.icon/.test(nav));
  const item = nav.slice(nav.indexOf("nb-lnb-item"));
  assert.ok(!item.slice(0, 400).includes("overflow-hidden"),
    "항목 버튼의 overflow-hidden이 라벨을 잘랐다 — 되살리지 말 것");
});

test("그룹 분석은 카테고리별 스트리머와 태그 검색 사이다", () => {
  const nav = NAV();
  const i = (k: string) => nav.indexOf(`key: "${k}"`);
  assert.ok(i("category_streamers") < i("group"));
  assert.ok(i("group") < i("tags"));
});

// ── 스크롤 소유권 ───────────────────────────────────────────────────────────

test("본문은 window가 스크롤하고 자체 컨테이너가 아니다", () => {
  const css = code(CSS());
  const main = css.slice(css.indexOf(".nb-shell-main {"));
  assert.ok(!/overflow/.test(main.slice(0, main.indexOf("}"))));
});

test("sidebar는 데스크톱에서 헤더 아래 sticky다", () => {
  const s = code(STATS());
  assert.ok(s.includes("md:sticky"), "데스크톱에서 sticky여야 한다");
  assert.ok(s.includes("md:top-[var(--nb-header-h)]"),
    "헤더 높이를 변수로 따라가야 확대에서도 맞는다");
  assert.ok(s.includes("md:max-h-[calc(100svh-var(--nb-header-h))]"),
    "높이를 스스로 제한해야 sticky가 걸리고 아래 항목에 닿을 수 있다");
  assert.ok(!s.includes("md:static"), "static이면 sticky를 덮어쓴다");
});

test("셸 body가 flex-start라 sidebar가 늘어나지 않는다", () => {
  const css = code(CSS());
  const body = css.slice(css.indexOf(".nb-shell-body {"));
  assert.ok(body.slice(0, body.indexOf("}")).includes("align-items: flex-start"),
    "stretch면 aside가 본문 높이만큼 늘어나 sticky가 걸릴 여지가 없다");
});

test("헤더는 sticky top-0이다", () => {
  assert.ok(/className="sticky top-0 z-\d+/.test(HEADER()));
});

test("모바일 drawer만 body 스크롤을 잠그고 닫으면 푼다", () => {
  const s = code(STATS());
  const eff = s.slice(s.indexOf("const mobile = window.matchMedia"));
  assert.ok(eff.includes('document.body.style.overflow = "hidden"'));
  assert.ok(eff.includes("document.body.style.overflow = prev"),
    "닫을 때 원래 값으로 되돌려야 한다");
  assert.ok(eff.includes("if (!mobile.matches) return"),
    "데스크톱에서 잠그면 본문을 읽을 수 없다");
});

// ── 프로필 메뉴 ─────────────────────────────────────────────────────────────

test("프로필 메뉴는 trigger 아래 우측에 절대 배치된다", () => {
  const h = code(HEADER());
  const menu = h.slice(h.indexOf('role="menu"'));
  const cls = menu.slice(0, 400);
  assert.ok(cls.includes("absolute"));
  assert.ok(cls.includes("right-0"), "헤더 오른쪽 끝이라 왼쪽으로 펼쳐야 잘리지 않는다");
  assert.ok(cls.includes("top-full"), "trigger 바로 아래");
  assert.ok(/mt-\d/.test(cls), "trigger와 간격을 둔다");
  assert.ok(/z-\[?(5[0-9]|60|[6-9]\d|\d{3,})\]?/.test(cls), "z-index가 50 이상이어야 한다");
});

test("프로필 메뉴를 담는 칸이 overflow로 잘리지 않는다", () => {
  const h = code(HEADER());
  // 이 칸에 overflow-hidden이 있으면 스크롤 컨테이너가 되고, 메뉴 열 때 첫 항목
  // 포커스가 그 칸을 안쪽으로 스크롤해 **버튼이 헤더 밖으로 밀려났다**(실측 top 11 → -55).
  const i = h.indexOf('justify-end gap-1');
  assert.ok(i > 0, "오른쪽 칸을 찾지 못했다");
  const cell = h.slice(h.lastIndexOf("<div", i), i + 40);
  assert.ok(!cell.includes("overflow-hidden"),
    "오른쪽 칸의 overflow-hidden은 프로필 버튼을 밀어낸다 — 되살리지 말 것");
});

test("프로필 메뉴의 닫기·포커스 복귀가 남아 있다", () => {
  const h = code(HEADER());
  assert.ok(h.includes('e.key !== "Escape"'), "ESC로 닫아야 한다");
  assert.ok(h.includes("triggerRef.current?.focus()"), "닫은 뒤 포커스를 되돌린다");
  assert.ok(h.includes('document.addEventListener("mousedown", onDown)'),
    "바깥 클릭으로 닫아야 한다");
  assert.ok(h.includes("signingOut"), "중복 클릭을 막는다");
  assert.ok(h.includes("signOutError"), "실패 상태를 구분한다");
});

// ── 비공식 인기점수: 화면은 남기고 수집만 멈춘다 ────────────────────────────

test("게이트가 닫혀도 랭킹 화면을 안내문으로 대체하지 않는다", () => {
  const s = code(SINGCUP());
  assert.ok(!s.includes("RankingRetired"),
    "'제공 종료' 한 장으로 바꾸면 확정된 순위를 볼 방법이 사라진다");
  assert.ok(!/if \(!rankingOpen\)/.test(s), "게이트로 화면을 숨기지 않는다");
  assert.ok(s.includes("<SingcupRanking"), "랭킹 화면은 항상 렌더된다");
});

test("확정본을 볼 때 수집 종료를 명시한다", () => {
  const s = SINGCUP();
  assert.ok(s.includes("수집 종료 · 최종 집계"));
  assert.ok(s.includes("마지막 정상 집계 기준"));
  assert.ok(s.includes("collectionNotice"), "문구는 서버가 준 것을 쓴다");
  // 배너는 확정본일 때만 — 진행 중인 집계에 '종료'를 붙이면 틀린 설명이 된다.
  assert.ok(/\{final && \(/.test(s));
});

test("공식 참가자 화면에서 비공식 인기점수로 갈 수 있다", () => {
  const o = code(OFFICIAL());
  assert.ok(o.includes("onRanking"), "진입 경로가 없으면 그 화면은 사라진 것과 같다");
  assert.ok(OFFICIAL().includes("비공식 인기점수 보기"));
  const s = code(SINGCUP());
  assert.ok(s.includes("<SingcupOfficial onRanking="), "실제로 연결돼 있어야 한다");
});

test("두 순위를 하나로 합치지 않는다", () => {
  const s = code(SINGCUP());
  const o = code(OFFICIAL());
  // PIKU 순위는 공식 화면이, 인기점수는 랭킹 화면이 각각 그린다.
  assert.ok(o.includes("pikuRanking"), "공식 화면만 PIKU를 읽는다");
  assert.ok(!s.includes("pikuRanking"), "랭킹 화면은 PIKU를 섞지 않는다");
  assert.ok(!o.includes("useSingcupRanking"), "공식 화면은 인기점수를 섞지 않는다");
});

// ── 공개 화면에 내부 비율값이 없다(회귀 방지) ───────────────────────────────

test("공개 화면 코드에 우승 비율·승률 원본이 없다", () => {
  for (const [name, src] of [["Singcup", SINGCUP()], ["SingcupOfficial", OFFICIAL()]] as const) {
    const c = code(src);
    assert.ok(!/\{[^{}]*\.(winRate|matchRate|win_rate|match_rate)[^{}]*\}/.test(c),
      `${name}이 비율값을 렌더에 꽂고 있다`);
  }
});

// ── Nexadmin: PIKU 403 안내 ─────────────────────────────────────────────────

test("403이면 무엇이 막혔는지 알리고 수집 버튼을 잠근다", () => {
  const s = read("app/nexadmin/PikuPanel.tsx");
  const c = code(s);
  assert.ok(c.includes("isForbidden"), "403을 다른 실패와 구분해야 한다");
  assert.ok(c.includes("setBlocked(true)"));
  assert.ok(s.includes("PIKU 서버가 수집 서버의 접근을 거부했습니다(403)"));
  assert.ok(s.includes("자동 수집은 계속"), "자동 수집 상태를 함께 밝힌다");
  assert.ok(s.includes("반복 시도하지 마세요"));
  assert.ok(s.includes("수동 JSON/CSV 가져오기"), "쓸 수 있는 대안을 준다");
  assert.ok(s.includes("저장·반영되지 않았습니다"),
    "실패한 확인이 반영되지 않았음을 명시한다");
  // 서버 직접 수집 버튼은 아예 제거됐으므로 잠글 대상이 없다. 남은 것은
  // 안내뿐이고, 403 상태를 감지하는 로직은 그대로 있어야 한다.
  assert.ok(!c.includes("api.admin.pikuCollect("), "직접 수집 버튼이 남아 있다");
});

test("403 안내가 우회 방법을 알려 주지 않는다", () => {
  // 주석은 뺀다 — "우회하지 않는다"고 적은 주석까지 걸리면 계약이 뒤집힌다.
  const c = code(read("app/nexadmin/PikuPanel.tsx"));
  for (const bad of ["User-Agent", "프록시", "proxy", "VPN", "쿠키"]) {
    assert.ok(!c.includes(bad), `우회 수단을 화면 문구에 적었다: ${bad}`);
  }
});
