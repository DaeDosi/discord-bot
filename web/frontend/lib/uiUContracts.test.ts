/* UI-U 화면 계약 — 소스 텍스트로 확인한다(DOM 런타임 의존 없이).
 *
 * 여기 담는 것은 "다시 깨지기 쉬운 구조적 약속"이다: 셸의 스크롤 소유권,
 * 햄버거 아이콘 고정, 검색 진입점 단일화, 곡 정보 추측 금지, 원본 비율 비노출. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
/** 주석을 걷어낸 코드만 — 주석 문구가 계약을 통과시키면 안 된다. */
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const HEADER = () => read("components/SiteHeader.tsx");
const STATS = () => read("app/stats/page.tsx");
const NAV = () => read("app/stats/StatsNav.tsx");
const CSS = () => read("app/globals.css");
const OFFICIAL = () => read("app/stats/SingcupOfficial.tsx");
const MERGE = () => read("lib/singcupOfficialMerge.ts");

// ── 1) 스크롤 소유권 (UI-V에서 계약이 바뀌었다) ─────────────────────────────
//
// **본문은 window가 스크롤한다.** 예전에는 헤더/메뉴/본문 셋이 각자 스크롤했는데,
// 그러면 브라우저 오른쪽의 진짜 스크롤바가 사라지고 본문 안쪽에 또 하나가 생겨
// 페이지가 얼마나 남았는지 읽을 수 없었다. 지금은 sidebar만 자기 스크롤을 갖는다.

test("셸이 헤더 높이를 변수로 계산한다", () => {
  const css = CSS();
  assert.ok(css.includes("--nb-header-h"), "헤더 높이 변수가 있어야 한다");
  assert.ok(HEADER().includes('"--nb-header-h"'),
    "헤더가 자기 실제 높이를 알려야 확대에서도 맞는다");
});

test("셸이 문서 스크롤을 빼앗지 않는다", () => {
  const css = code(CSS());
  const shell = css.slice(css.indexOf(".nb-shell {"), css.indexOf(".nb-shell-body"));
  assert.ok(!shell.includes("height: 100svh"),
    "셸이 화면 높이를 고정하면 문서가 스크롤을 잃는다");
  assert.ok(!shell.includes("overflow: hidden"),
    "셸의 overflow:hidden은 window 스크롤을 없앤다");
});

test("본문은 자체 스크롤 컨테이너가 아니다", () => {
  const css = code(CSS());
  const main = css.slice(css.indexOf(".nb-shell-main {"));
  const block = main.slice(0, main.indexOf("}"));
  assert.ok(!/overflow/.test(block),
    "본문에 overflow를 주면 window 스크롤바와 이중이 된다");
});

test("sidebar만 자기 스크롤을 갖고 스크롤바는 숨긴다", () => {
  const css = code(CSS());
  const nav = css.slice(css.indexOf(".nb-shell-nav {"));
  const block = nav.slice(0, nav.indexOf("}"));
  assert.ok(block.includes("overflow-y: auto"), "메뉴는 스스로 스크롤해야 한다");
  assert.ok(block.includes("scrollbar-width: none"), "Firefox에서 스크롤바를 숨긴다");
  assert.ok(!/overflow(-y)?: hidden/.test(block),
    "overflow:hidden으로 막으면 아래 항목에 닿을 방법이 사라진다");
  assert.ok(css.includes(".nb-shell-nav::-webkit-scrollbar { display: none; }"),
    "Chromium/WebKit에서도 스크롤바만 숨긴다");
});

test("전역 스크롤바는 숨기지 않는다(본문 스크롤의 유일한 신호다)", () => {
  const css = code(CSS());
  // `::-webkit-scrollbar { width: 10px }` 같은 전역 규칙이 살아 있어야 한다.
  assert.ok(/::-webkit-scrollbar \{ width: 10px/.test(css));
  assert.ok(css.includes("scrollbar-color: var(--nb-scroll-thumb)"),
    "트랙·thumb 대비를 팔레트에서 준다");
});

test("본문의 가로 overflow를 숨겨 은폐하지 않는다", () => {
  const css = code(CSS());
  const main = css.slice(css.indexOf(".nb-shell-main {"));
  assert.ok(!main.slice(0, main.indexOf("}")).includes("overflow-x"));
});

test("stats 페이지가 셸을 쓰고 sidebar가 본문의 형제다", () => {
  const s = STATS();
  assert.ok(s.includes('className="nb-shell bg-bg text-fg"'));
  assert.ok(s.includes('className="nb-shell-body"'));
  assert.ok(s.includes("nb-shell-nav"), "sidebar가 셸 칸이어야 한다");
  assert.ok(s.includes('className="nb-shell-main'), "본문도 셸 칸이어야 한다");
  // aside가 main보다 **앞**에 있어야 형제로 나란히 선다.
  assert.ok(s.indexOf("nb-shell-nav") < s.indexOf('className="nb-shell-main'));
});

test("StatsNav가 자체 sticky를 갖지 않는다", () => {
  // 셸이 스크롤 소유권을 가지므로 sticky를 겹치면 기준이 둘이 된다.
  const s = code(NAV());
  assert.ok(!s.includes("md:sticky"), "셸과 sticky가 싸운다");
  assert.ok(!s.includes("<aside"), "셸의 aside와 중첩된다");
});

test("모바일 drawer가 잠금·ESC·포커스 복귀를 갖는다", () => {
  const s = STATS();
  assert.ok(s.includes('matchMedia("(max-width: 767px)")'), "모바일에서만 적용");
  assert.ok(s.includes('document.body.style.overflow = "hidden"'), "배경 스크롤 잠금");
  assert.ok(/e\.key !== "Escape"/.test(s), "ESC 닫기");
  assert.ok(s.includes('header button[aria-controls='), "포커스 복귀 대상");
});

// ── 2) 햄버거 ───────────────────────────────────────────────────────────────

test("햄버거 아이콘이 상태와 무관하게 하나다", () => {
  const s = code(HEADER());
  assert.ok(s.includes("<Menu size={20}"), "햄버거 아이콘이 있어야 한다");
  // 열림 상태에서 X로 바꾸는 분기 자체를 없앴다(CSS로 숨기는 게 아니다).
  assert.ok(!/burgerExpanded\s*\?\s*<X/.test(s), "X 분기가 남아 있다");
  assert.ok(!/menuOpen\s*\?\s*<X/.test(s));
});

test("햄버거가 상태와 대상을 노출하고 44px를 지킨다", () => {
  const s = HEADER();
  assert.ok(s.includes("aria-expanded={burgerExpanded}"));
  assert.ok(s.includes("aria-controls={burgerControls}"));
  assert.ok(s.includes("nb-tap-icon"), "44×44 hit area");
  assert.ok(s.includes("h-11 w-11"));
});

test("햄버거가 왼쪽 여백을 줄여 붙는다", () => {
  assert.ok(/-ml-1\.5 flex min-w-0 items-center/.test(HEADER()));
});

// ── 3) 치지직 통계 · Beta ───────────────────────────────────────────────────

test("치지직 통계가 순백이고 워드마크와 같은 크기다", () => {
  const s = HEADER();
  // 크기는 NexBot과 **같게**(둘 다 17px) 두고, 브랜드 우선순위는 굵기로만
  // 구분한다(bold 700 / medium 500). 색은 `text-fg`(#EBEFF6)가 아니라 순백이다.
  assert.ok(/text-\[17px\] font-medium text-white/.test(s),
    "지시값은 #FFFFFF이고 크기는 워드마크와 같은 급이다");
  assert.ok(/font-bold text-\[17px\] text-fg/.test(s), "NexBot은 bold 17px");
});

test("Beta가 네온 그린 토큰을 쓰고 하나뿐이다", () => {
  const s = HEADER();
  assert.ok(s.includes("text-neon"), "accent(Discord 블루)를 쓰면 서비스가 뒤바뀐다");
  assert.ok(s.includes("border-neon/50") && s.includes("bg-neon/10"), "대비 확보");
  assert.equal((code(s).match(/Beta/g) ?? []).length, 1, "출처는 한 곳뿐");
});

test("neon 토큰이 #00FFA3이고 기존 토큰과 충돌하지 않는다", () => {
  const t = read("tailwind.config.ts");
  assert.ok(/neon:\s*"#00FFA3"/.test(t));
  assert.ok(/accent:\s*\{\s*DEFAULT:\s*"#5865F2"/.test(t), "accent는 Discord 블루 유지");
  assert.ok(/chzzk:\s*"#03C75A"/.test(t), "chzzk는 네이버 그린 유지");
});

// ── 4) 검색 ─────────────────────────────────────────────────────────────────

test("검색이 3영역 grid의 가운데 칸이고 폭이 넓다", () => {
  const s = HEADER();
  assert.ok(s.includes("md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]"),
    "좌우가 같은 비율이어야 viewport 중앙이다");
  // 폭은 **헤더 칸**이 정한다. 뷰포트 기준 고정값은 본문 폭이 좁은 페이지
  // (`maxWidth="3xl"`)에서 컨테이너를 넘어 좌우와 겹쳤다(실측 1쌍).
  assert.ok(s.includes('maxWidth === "full"'), "컨테이너 폭에 따라 달라져야 한다");
  assert.ok(s.includes("md:w-[min(52vw,680px)]"), "넓은 레이아웃");
  assert.ok(s.includes("md:w-[min(40vw,420px)]"), "좁은 레이아웃");
  assert.ok(s.includes("rounded-full"), "pill 형태");
});

test("sidebar 검색이 제거되고 진입점이 헤더 하나다", () => {
  const s = STATS();
  assert.ok(!s.includes("function StreamerSearch()"), "sidebar 검색 컴포넌트가 남아 있다");
  assert.ok(!s.includes("<StreamerSearch />"));
  assert.ok(!code(NAV()).includes("children"), "검색을 꽂던 슬롯도 없앴다");
  // 검색 **API**까지 지우지는 않았다(다른 화면이 쓴다).
  assert.ok(read("lib/api.ts").includes("quickSearch"));
});

// ── 5) 프로필 드롭다운 ──────────────────────────────────────────────────────

test("드롭다운이 menu 의미와 키보드 계약을 갖는다", () => {
  const s = HEADER();
  assert.ok(s.includes('aria-haspopup="menu"'));
  assert.ok(s.includes("aria-controls={open ? menuId : undefined}"));
  assert.ok(s.includes('role="menu"'));
  assert.ok((s.match(/role="menuitem"/g) ?? []).length >= 2, "대시보드·로그아웃");
  assert.ok(s.includes("triggerRef.current?.focus()"), "닫으면 trigger로 복귀");
  assert.ok(s.includes('menuRef.current?.querySelector'), "열면 첫 항목으로 이동");
});

test("드롭다운이 대시보드와 로그아웃을 갖고 잘리지 않는다", () => {
  const s = HEADER();
  assert.ok(s.includes('href="/dashboard"'), "기존 dashboard 경로 재사용");
  assert.ok(s.includes("max-w-[calc(100vw-1.5rem)]"), "viewport 밖으로 잘리지 않는다");
});

test("로그아웃이 기존 인증 방식을 쓰고 실패를 구분한다", () => {
  const s = HEADER();
  assert.ok(s.includes('localStorage.removeItem("token")'), "기존 토큰 방식 재사용");
  assert.ok(!/fetch\(.*logout/i.test(s), "새 인증 엔드포인트를 만들지 않는다");
  assert.ok(s.includes("signOutError"), "실패 상태가 있어야 한다");
  assert.ok(s.includes("로그아웃하지 못했습니다"));
  assert.ok(s.includes("signingOut"), "중복 클릭 방지");
});

// ── 6) 싱드컵 2줄 정보 ──────────────────────────────────────────────────────

test("곡·가수를 서버 필드에서만 읽는다", () => {
  // 병합 로직은 `lib/singcupOfficialMerge.ts`로 옮겼다(그룹 14팀 삭제 결함 수정).
  // 의미 검증은 그 모듈의 동작 테스트가 하고, 여기서는 출처 계약만 고정한다.
  const s = MERGE() + OFFICIAL();
  assert.ok(s.includes("songTitle") && s.includes("artistName"));
  // 이름/제목 문자열을 다시 쪼개지 않는다(운영 데이터는 순서가 뒤섞여 있다).
  assert.ok(!/clipTitle\s*\.\s*split/.test(s));
  assert.ok(!/channelName\s*\.\s*split/.test(s));
});

test("곡 정보가 없으면 줄 자체를 그리지 않는다", () => {
  assert.ok(OFFICIAL().includes("if (!text) return null;"),
    "빈 줄이나 단독 `-`가 남으면 '못 불러왔다'로 읽힌다");
  assert.ok(/return song \|\| artist \|\| "";/.test(MERGE()), "한쪽만 있으면 그것만");
});

test("카드와 목록 양쪽에 2줄이 들어간다", () => {
  const s = OFFICIAL();
  assert.ok((s.match(/<SongLine /g) ?? []).length >= 2, "카드·목록 모두");
});

// ── 7) 정렬 탭 ──────────────────────────────────────────────────────────────

test("정렬 탭이 부문마다 있고 tab 의미를 갖는다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes('role="tablist"'));
  assert.ok(s.includes('role="tab"'));
  assert.ok(s.includes("aria-selected={sort === o.key}"));
  assert.ok(s.includes("SORT_TABS"), "우승 비율 · 승률");
  assert.ok(s.includes('{ key: "primary", label: "우승 비율" }'));
  assert.ok(s.includes('{ key: "secondary", label: "승률" }'));
});

test("정렬 탭이 키보드로 조작되고 44px를 지킨다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes('e.key !== "ArrowLeft" && e.key !== "ArrowRight"'));
  assert.ok(s.includes("tabIndex={sort === o.key ? 0 : -1}"), "roving tabindex");
  assert.ok(s.includes("nb-tap nb-tap-wide"), "가로·세로 모두 44px");
  assert.ok(CSS().includes(".nb-tap-wide { min-width: 44px; }"));
});

test("정렬 컨트롤이 화면에 하나뿐이다", () => {
  const s = code(OFFICIAL());
  assert.ok(!s.includes('aria-label="순위 정렬 기준"'),
    "상단 중복 정렬 버튼이 남아 있다");
});

test("PIKU 데이터가 없으면 정렬 탭 대신 출처를 밝힌다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("ranking && ranking.length > 0 && onSort ?"),
    "데이터가 없으면 조작할 수 없어야 한다");
  assert.ok(s.includes("순서: 치지직 공지 표기 순 (순위 아님)"), "fallback 유지");
});

test("정렬은 서버가 하고 화면은 다시 매기지 않는다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("api.singcup.pikuRanking(sort)"), "기준이 바뀌면 서버에 다시 묻는다");
  assert.ok(!/\.sort\(\(a, b\) => b\.(win|match)/.test(s), "프런트 재정렬 금지");
});

test("원본 비율이 화면 코드에 없다", () => {
  // 주석에는 "내부 컬럼명은 쓰지 않는다"는 설명이 나오므로 **코드만** 본다.
  const s = code(OFFICIAL());
  for (const bad of ["winRate", "matchRate", "win_rate", "match_rate", "winRatio"]) {
    assert.ok(!s.includes(bad), `${bad}가 화면 코드에 있다`);
  }
});

// ── 8) 반응형 결함 수정 ─────────────────────────────────────────────────────

test("히트맵이 고정 640px로 부모를 밀지 않는다", () => {
  const s = read("app/stats/OverviewViz.tsx");
  assert.ok(!s.includes("minWidth: 640"), "고정 최소폭이 확대에서 문서를 밀었다");
  assert.ok(s.includes("min-w-[min(640px,100%)]"), "뷰포트를 넘지 않는 선에서만");
  assert.ok(!s.includes("[&::-webkit-scrollbar]:hidden"), "스크롤바를 숨기지 않는다");
});

test("랭킹 차트 고정폭이 좁은 화면에서 줄어든다", () => {
  // 값 자체(92px)를 고정하지 않는다 — 지켜야 할 것은 **기본 폭이 xs 이상보다
  // 좁고, 넓어지면 원래 폭으로 돌아온다**는 관계다. 92px도 390@150%(260px)에서는
  // 여전히 넘쳤고(실측 39px) 지금은 76px이다.
  const s = read("app/stats/RankingCharts.tsx");
  const base = s.match(/w-\[(\d+)px\] shrink-0 items-center justify-end/);
  assert.ok(base, "지표 칸의 기본 폭을 찾지 못했다");
  assert.ok(Number(base![1]) <= 92, `기본 폭이 너무 넓다: ${base![1]}px`);
  assert.ok(s.includes("xs:w-[112px]"), "넓어지면 원래 폭으로 돌아온다");
  // 토글 묶음은 줄어들 수 있어야 wrap이 실제로 동작한다.
  assert.ok(!s.includes("nb-tap-gap flex shrink-0 flex-wrap"),
    "shrink-0 + flex-wrap은 max-content로 굳어 줄바꿈이 일어나지 않는다");
});

test("스트리머 상세의 탭과 기간 토글이 줄바꿈된다", () => {
  const s = read("app/stats/streamer/[channelId]/page.tsx");
  assert.ok(s.includes("flex flex-wrap items-center gap-y-2"), "한 줄 강제를 풀었다");
  assert.ok(s.includes("sm:ml-auto"), "넓을 때만 오른쪽 끝");
  assert.ok(!/className="ml-auto flex shrink-0 items-center gap-1"/.test(s));
  assert.ok(s.includes("nb-tap nb-tap-wide"), "기간 토글 44px");
});

test("카테고리 이전·다음이 44px를 갖는다", () => {
  assert.ok(read("app/stats/CategoryRankCards.tsx").includes("nb-tap-icon"));
});

test("약관·개인정보 목차 링크가 44px를 갖는다", () => {
  for (const p of ["app/privacy/page.tsx", "app/terms/page.tsx"]) {
    const s = read(p);
    assert.ok(s.includes("nb-tap flex items-center gap-2 text-sm"), `${p} 목차`);
    assert.ok(s.includes('className="nb-tap-gap"'), `${p} 간격`);
  }
});

// ── 9) 공통 헤더 적용 ───────────────────────────────────────────────────────

test("대상 페이지가 모두 공통 헤더를 쓴다", () => {
  const pages = ["app/page.tsx", "app/stats/page.tsx", "app/contact/page.tsx",
    "app/guide/page.tsx", "app/support/correction/page.tsx",
    "app/stats/streamer/[channelId]/page.tsx", "app/stats/guide/page.tsx",
    "app/privacy/page.tsx", "app/terms/page.tsx"];
  for (const p of pages) {
    const s = read(p);
    assert.ok(s.includes("<SiteHeader"), `${p}가 공통 헤더를 쓰지 않는다`);
    // 자체 헤더 막대를 다시 만들지 않는다.
    assert.ok(!/<header className="sticky top-0 z-50/.test(s), `${p}에 복제 헤더가 있다`);
  }
});
