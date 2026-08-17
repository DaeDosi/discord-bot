// NAV-STATS — 전역 헤더(햄버거·검색·Beta 배지·로그인) 계약.
//
// 소스 텍스트를 읽는 이유는 이 저장소의 다른 프론트 테스트와 같다.
// 브라우저 실측(320/390/768/1440 · 125%/150%)은 별도로 하고, 여기서 막는 것은
// 구조가 조용히 원복되는 것이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");
const H = () => read("components/SiteHeader.tsx");

// ── 구조 ────────────────────────────────────────────────────────────────────

test("좁은 폭에서 헤더 항목이 서로 겹치지 않는다", () => {
  const s = H();
  // 예전 계약은 "브랜드·햄버거·로그인은 shrink-0"이었다. 3영역 grid로 바꾸면서
  // 그대로 두었더니 390@150%(실폭 260px)에서 워드마크와 로그인이 실제로 겹쳤다.
  // 그래서 **버티는 것은 44px 터치 하한뿐**이고, 그 위로는 줄어들 수 있어야 한다.
  assert.ok(/h-11 w-11 shrink-0/.test(s), "햄버거는 44×44 고정");
  assert.ok(/nb-brand-tap min-w-0/.test(s), "워드마크는 44px까지 줄어든다");
  assert.ok(!/nb-brand-tap shrink-0/.test(s), "워드마크가 버티면 겹친다");
  assert.ok((s.match(/overflow-hidden/g) ?? []).length >= 2,
    "좌우 묶음이 셀 밖으로 나가지 않아야 한다");
  assert.ok(s.includes('<span className="hidden xs:inline">로그인</span>'),
    "아주 좁은 폭에서는 로그인 글자를 접는다");
  assert.ok(s.includes('aria-label="로그인"'),
    "글자를 접어도 접근 가능한 이름은 남아야 한다");
});

test("헤더 높이를 고정하지 않는다(확대에서 글자가 잘리지 않게)", () => {
  const s = H();
  assert.ok(s.includes("min-h-[60px]"), "최소 높이만 정한다");
  assert.ok(!/style=\{\{ height: 60 \}\}/.test(s),
    "고정 height는 150% 확대에서 내용을 잘라낸다");
});

test("브랜드는 아이콘 없이 워드마크 하나로 표시된다", () => {
  const s = H();
  assert.ok(s.includes("nb-brand-tap"), "44px hit area 유지");
  // 예전에는 좁은 화면에서 글자를 `sr-only`로 내리고 로봇 아이콘만 남겼다.
  // 이제 아이콘을 없앴으므로 **어느 폭에서도 글자가 그대로 보인다**.
  assert.ok(s.includes('<span className="truncate">NexBot</span>'),
    "워드마크 텍스트가 항상 보여야 한다");
  assert.ok(!/<Bot size=\{20\}/.test(s),
    "브랜드 옆 로봇 아이콘은 제거됐다");
});

test("브랜드 옆에 치지직 통계 Beta가 붙고 구분자·신호 아이콘이 없다", () => {
  const s = H();
  const bar = s.slice(s.indexOf("grid-cols-[1fr_auto_1fr]"));
  assert.ok(bar.includes("치지직 통계"), "브랜드 옆에 현재 서비스명이 있어야 한다");
  assert.ok(!/<span className="text-border" aria-hidden="true">\/<\/span>/.test(s),
    "`/` 구분자는 제거됐다");
  assert.ok(!/<Radio size=\{14\}/.test(s), "신호 아이콘은 제거됐다");
  // 주석에도 "Beta"라는 낱말이 나오므로 **렌더되는 코드만** 센다.
  const code = s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  assert.equal((code.match(/Beta/g) ?? []).length, 1, "Beta 배지 출처는 하나뿐이다");
});

test("헤더는 3영역 grid라 검색이 viewport 중앙에 온다", () => {
  const s = H();
  // flex + flex-1로는 좌우 폭이 다른 순간 검색창 중심이 그 차이의 절반만큼 밀린다.
  assert.ok(s.includes("grid-cols-[1fr_auto_1fr]"),
    "좌우를 같은 1fr로 잡아야 가운데가 화면 중앙이다");
  assert.ok(/justify-end/.test(s), "오른쪽 묶음은 끝에 붙는다");
});

test("검색창은 완전히 둥근 capsule이다", () => {
  const s = H();
  assert.ok(/rounded-full border border-border bg-bg/.test(s),
    "rounded-lg(8px)로는 pill 형태가 되지 않는다");
});

// ── 햄버거 ──────────────────────────────────────────────────────────────────

test("햄버거에 열림 상태와 대상이 노출된다", () => {
  const s = H();
  assert.ok(s.includes("aria-expanded={burgerExpanded}"));
  assert.ok(s.includes("aria-controls={burgerControls}"));
  assert.ok(s.includes("aria-label={burgerLabel}"),
    "상태에 따라 이름이 바뀌어야 한다");
  // 라벨은 어느 분기에서도 "통계 메뉴"를 말한다 — 같은 버튼이 페이지마다
  // 다른 것을 여는 것처럼 읽히면 안 된다.
  assert.ok(s.includes("통계 메뉴 접기") && s.includes("통계 메뉴 펼치기"));
  assert.ok(s.includes("통계 메뉴 닫기") && s.includes("통계 메뉴 열기"));
});

test("햄버거의 대상은 sidebar 유무로만 갈린다", () => {
  const s = H();
  assert.ok(s.includes("const usesSidebar = Boolean(statsNav)"),
    "상태 소유권이 하나로 모여 있어야 한다");
  assert.ok(/burgerExpanded = usesSidebar \? statsNav!\.open : menuOpen/.test(s));
  assert.ok(s.includes("{!usesSidebar && menuOpen && ("),
    "sidebar가 있는 페이지에서는 drawer가 뜨지 않는다");
});

test("아이콘이 열림/닫힘에 따라 바뀐다", () => {
  const s = H();
  assert.ok(/burgerExpanded\s*\?\s*<X size=\{20\}/.test(s),
    "열렸는데 햄버거 그대로면 닫는 방법이 보이지 않는다");
});

test("drawer 목록이 통계 sidebar와 같은 묶음이다", () => {
  const s = H();
  for (const label of ["봉누도", "싱드컵", "통계", "랭킹", "카테고리", "통계 안내"]) {
    assert.ok(s.includes(`label: "${label}"`), `drawer에 ${label}가 없다`);
  }
});

test("드로어가 ESC로 닫히고 포커스가 순환하며 버튼으로 복귀한다", () => {
  const s = H();
  assert.ok(s.includes('if (e.key === "Escape") { setMenuOpen(false); return; }'));
  assert.ok(s.includes('e.key !== "Tab"'), "포커스 순환 처리가 있어야 한다");
  assert.ok(s.includes("const burger = burgerRef.current"),
    "cleanup 시점에 ref가 바뀌어 있을 수 있다 — effect 안에서 잡아 둔다");
  assert.ok(s.includes("burger?.focus()"), "닫을 때 원래 버튼으로 돌아간다");
});

test("드로어가 열리면 배경 스크롤을 잠근다", () => {
  const s = H();
  assert.ok(s.includes('document.body.style.overflow = "hidden"'));
  assert.ok(s.includes("document.body.style.overflow = prev"), "반드시 되돌린다");
});

test("보조 메뉴 목록이 화면 크기별로 갈리지 않는다", () => {
  const s = H();
  assert.ok(s.includes("DRAWER_LINKS"), "목록은 한 벌이다");
  // 목록을 화면 크기별로 다르게 두면 "PC에서 본 항목이 폰에 없다"가 된다
  assert.ok(!/DRAWER_LINKS_MOBILE|DRAWER_LINKS_DESKTOP/.test(s));
});

// ── 전역 검색 ───────────────────────────────────────────────────────────────

test("검색은 combobox/listbox 의미를 갖는다", () => {
  const s = H();
  for (const a of ['role="combobox"', "aria-expanded={showPanel}", "aria-controls={listId}",
                   'aria-autocomplete="list"', "aria-activedescendant",
                   'role="listbox"', 'role="option"', "aria-selected={i === active}"]) {
    assert.ok(s.includes(a), `${a}가 없다`);
  }
});

test("키보드로 조작된다", () => {
  const s = H();
  assert.ok(s.includes('e.key === "ArrowDown"'));
  assert.ok(s.includes('e.key === "ArrowUp"'));
  assert.ok(s.includes('e.key === "Enter"'));
  assert.ok(s.includes('e.key === "Escape"'));
  // ESC가 헤더 밖으로 새면 드로어까지 함께 닫혀 조작이 어긋난다
  assert.ok(s.includes("e.stopPropagation()"));
});

test("디바운스가 있고 늦게 온 응답이 최신 결과를 덮지 않는다", () => {
  const s = H();
  assert.ok(s.includes("}, 300);"), "디바운스 300ms");
  assert.ok(s.includes("clearTimeout(t)"), "입력이 이어지면 이전 타이머를 취소한다");
  assert.ok(s.includes("const reqId = useRef(0)"));
  assert.ok(s.includes("my === reqId.current"), "경합 가드");
});

test("로딩·오류·결과 없음을 서로 구분한다", () => {
  const s = H();
  assert.ok(s.includes("검색 중…"));
  assert.ok(s.includes('role="alert"'));
  assert.ok(s.includes("검색 결과가 없습니다"));
  assert.ok(s.includes('aria-busy="true"'));
});

test("검색은 전용 로컬 API를 쓴다(외부 호출 경로가 아니다)", () => {
  const s = H();
  assert.ok(s.includes("api.rising.quickSearch("));
  assert.ok(!s.includes("api.rising.search("),
    "외부 치지직 API를 부르는 경로를 헤더에서 쓰면 한도가 즉시 소진된다");
  const api = read("lib/api.ts");
  assert.ok(api.includes("/api/rising/quick-search"));
  assert.ok(api.includes("검색 요청이 너무 잦습니다"), "429를 사람이 읽는 문구로 바꾼다");
});

test("입력 길이 상한이 서버와 맞춰져 있다", () => {
  assert.ok(/maxLength=\{40\}/.test(H()));
});

test("검색 결과를 누르면 스트리머 상세로 간다", () => {
  assert.ok(H().includes("`/stats/streamer/${it.channel_id}`"));
});

// ── 메뉴 · Beta 배지 ────────────────────────────────────────────────────────

test("Beta 배지는 '치지직 통계' 오른쪽에 붙고 출처가 하나다", () => {
  const s = H();
  const i = s.indexOf("치지직 통계");
  const j = s.indexOf("Beta", i);
  assert.ok(i >= 0 && j > i, "치지직 통계 뒤에 Beta가 와야 한다");
  assert.equal((s.match(/>\s*Beta\s*</g) || []).length, 1, "배지가 두 벌이면 안 된다");
});

test("페이지 제목 옆 Beta 배지는 제거됐다(단일 출처)", () => {
  const s = read("app/stats/page.tsx");
  assert.ok(!/>Beta</.test(s),
    "헤더와 제목 양쪽에 있으면 같은 화면에 Beta가 두 번 보인다");
});

// ── 로그인 ──────────────────────────────────────────────────────────────────

test("로그인 영역이 헤더의 마지막 요소다", () => {
  const s = H();
  const nav = s.indexOf("<HeaderNav />");
  const auth = s.indexOf("<AuthArea />");
  assert.ok(nav > 0 && auth > nav, "로그인이 실제 오른쪽 끝에 있어야 한다");
});

test("로그인 URL을 못 받아도 자리를 비우지 않는다", () => {
  const s = H();
  assert.ok(s.includes('href={loginUrl ?? "/login"}'),
    "비워 두면 응답이 도착할 때 헤더가 흔들린다(레이아웃 시프트)");
});

test("프로필 메뉴에 설정이 있고 탈퇴와 분리돼 있다", () => {
  const s = H();
  assert.ok(s.includes('href="/settings"'));
  assert.ok(s.includes("로그아웃"));
  // 주석에서는 '회원탈퇴'를 설명하지만 **메뉴 항목으로는 없어야** 한다.
  // 헤더 메뉴에 나란히 두면 로그아웃과 한 칸 차이라 오조작이 난다.
  const menu = s.split('role="menu"')[1].split("</div>")[0]
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");     // 주석은 화면에 나오지 않는다
  assert.ok(!menu.includes("회원탈퇴") && !menu.includes("탈퇴"),
    "탈퇴는 설정 페이지 맨 아래에만 둔다");
  assert.ok(s.includes('aria-haspopup="menu"'));
});

test("손상된 localStorage가 헤더를 깨뜨리지 않는다", () => {
  const s = H();
  assert.ok(/catch \{ \/\* 손상된 값이면 로그아웃 상태로 둔다 \*\/ \}/.test(s));
});
