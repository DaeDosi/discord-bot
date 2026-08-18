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

// ── 1) 3영역 독립 스크롤 ────────────────────────────────────────────────────

test("셸이 헤더 높이를 변수로 계산한다", () => {
  const css = CSS();
  assert.ok(css.includes("--nb-header-h"), "헤더 높이 변수가 있어야 한다");
  const shell = css.slice(css.indexOf(".nb-shell {"));
  assert.ok(shell.slice(0, 220).includes("height: 100svh"),
    "vh가 아니라 svh — 모바일 주소창이 접힐 때 값이 변하면 스크롤이 튄다");
  assert.ok(HEADER().includes('setProperty(\n      "--nb-header-h"')
    || HEADER().includes('"--nb-header-h"'),
    "헤더가 자기 실제 높이를 알려야 확대에서도 맞는다");
});

test("두 칸이 각자 스크롤하고 min-height가 0이다", () => {
  const css = CSS();
  for (const sel of [".nb-shell-body", ".nb-shell-nav", ".nb-shell-main"]) {
    assert.ok(css.includes(sel), `${sel}가 없다`);
  }
  // min-height:0이 없으면 grid 안에서 자식 높이만큼 늘어나 스크롤이 안 생긴다.
  const pair = css.slice(css.indexOf(".nb-shell-nav,"));
  assert.ok(pair.slice(0, 200).includes("min-height: 0"));
  assert.ok(pair.slice(0, 200).includes("overflow-y: auto"));
});

test("본문의 가로 overflow를 숨겨 은폐하지 않는다", () => {
  const css = code(CSS());
  // `.nb-shell`의 `overflow: hidden`은 **세로 이중 스크롤**을 막는 것이다.
  // 본문의 가로는 각 화면이 스스로 책임진다 — 여기서 덮으면 진짜 overflow가
  // 보이지 않게 될 뿐 고쳐지지 않는다.
  const main = css.slice(css.indexOf(".nb-shell-main { overscroll"));
  assert.ok(!main.slice(0, 120).includes("overflow-x"));
  // 반대로 메뉴 칸은 가로로 넘칠 일이 없어 잘라도 된다(넘치면 그게 버그다).
  assert.ok(css.includes(".nb-shell-nav { overflow-x: hidden;"));
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
  const s = OFFICIAL();
  assert.ok(s.includes("row.songTitle") && s.includes("row.artistName"));
  // 이름/제목 문자열을 다시 쪼개지 않는다(운영 데이터는 순서가 뒤섞여 있다).
  assert.ok(!/clipTitle\s*\.\s*split/.test(s));
  assert.ok(!/channelName\s*\.\s*split/.test(s));
});

test("곡 정보가 없으면 줄 자체를 그리지 않는다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("if (!text) return null;"),
    "빈 줄이나 단독 `-`가 남으면 '못 불러왔다'로 읽힌다");
  assert.ok(/return song \|\| artist \|\| "";/.test(s), "한쪽만 있으면 그것만");
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
  const s = read("app/stats/RankingCharts.tsx");
  assert.ok(s.includes("w-[92px]"), "112px은 260px 화면에서 부모를 밀었다");
  assert.ok(s.includes("xs:w-[112px]"), "넓어지면 원래 폭으로 돌아온다");
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
