// UI-S: 공통 클릭 영역 · 확대 reflow 계약.
//
// ⚠️ 한계: 소스 텍스트 대조는 **브라우저 reflow를 증명하지 못한다.** 실제 측정은
// headless Chrome + CDP(`scratchpad/uis/reflow.mjs`)로 따로 했고, 기준본에서 재현된
// 값과 수정 후 값을 전후 비교했다. 이 파일이 막는 것은 그 구조가 조용히 원복되는 것뿐이다.
// 이 테스트 통과를 reflow PASS로 읽지 말 것.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

/** 홈으로 돌아가는 NexBot 브랜드 링크가 있는 파일 전부.
 *  헤더가 11곳에 복제돼 있어 한 곳만 고치면 나머지가 남는다. */
const BRAND_FILES = [
  "app/about/page.tsx",
  "app/contact/page.tsx",
  "app/faq/page.tsx",
  "app/guide/page.tsx",
  "app/page.tsx",
  "app/privacy/page.tsx",
  "app/stats/page.tsx",
  "app/stats/singcup/live/page.tsx",
  "app/status/page.tsx",
  "app/terms/page.tsx",
  "components/Navbar.tsx",
  "components/Footer.tsx",
];

// ── 공통 로고 hit area ──────────────────────────────────────────────────────
test("브랜드 링크 hit area 클래스가 CSS에 한 번만 정의된다", () => {
  const css = read("app/globals.css");
  // 실측: 로고 링크가 105×33 / 88×26 / 79×23px 이었다(44px 미만).
  assert.ok(/\.nb-brand-tap\s*\{/.test(css), "공통 hit area 클래스가 없다");
  assert.equal((css.match(/\.nb-brand-tap\s*\{/g) || []).length, 1,
    "같은 클래스가 여러 번 정의됐다");
  const block = css.slice(css.indexOf(".nb-brand-tap"), css.indexOf(".nb-brand-tap") + 300);
  assert.ok(/min-height:\s*44px/.test(block), "최소 높이 44px 계약이 없다");
  assert.ok(/min-width:\s*44px/.test(block), "최소 폭 44px 계약이 없다");
});

test("hit area는 포인터 종류와 무관하게 적용된다", () => {
  // 기존 .nb-tap 계열은 `@media (pointer: coarse)` 안에만 있다. 로고는 헤더 좌측
  // 끝에 단독으로 있어 이웃과 겹칠 위험이 없으므로 데스크톱에도 적용한다.
  const css = read("app/globals.css");
  const i = css.indexOf(".nb-brand-tap");
  const coarse = css.indexOf("@media (pointer: coarse)");
  const coarseEnd = css.indexOf("\n}", coarse);
  assert.ok(!(i > coarse && i < coarseEnd),
    "로고 hit area가 coarse 포인터 전용 블록 안에 있다 — 데스크톱에서 적용되지 않는다");
});

test("브랜드 링크 11곳 전부가 공통 클래스를 쓴다", () => {
  for (const f of BRAND_FILES) {
    const s = read(f);
    assert.ok(/nb-brand-tap/.test(s), `${f}: 브랜드 링크에 공통 hit area가 없다`);
  }
});

test("브랜드 링크에 접근 가능한 이름이 있다", () => {
  for (const f of BRAND_FILES) {
    const s = read(f);
    const i = s.indexOf("nb-brand-tap");
    const block = s.slice(Math.max(0, i - 400), i + 400);
    // 아이콘 + "NexBot" 텍스트가 이름이 된다. 텍스트를 지우면 이름이 사라진다.
    assert.ok(/NexBot/.test(block), `${f}: 브랜드 링크에서 이름이 사라졌다`);
  }
});

test("로고 아이콘 크기를 hit area 때문에 늘리지 않는다", () => {
  // 클릭 영역만 넓히고 시각적 크기는 그대로 둔다.
  const css = read("app/globals.css");
  const i = css.indexOf(".nb-brand-tap");
  const block = css.slice(i, i + 300);
  assert.ok(!/font-size/.test(block), "hit area 클래스가 글꼴 크기를 건드린다");
  assert.ok(!/transform|scale/.test(block), "hit area 클래스가 로고를 늘린다");
});

// ── 확대 reflow ─────────────────────────────────────────────────────────────
test("/stats 라이브 집계 칩이 좁은 화면에서 뷰포트를 넘지 않는다", () => {
  // 실측(운영, 확대 150% → vw 260): min-width 266px 로 계산돼 sw 296~309.
  // UI-Q의 CLS 자리 예약은 유지하되, 뷰포트보다 넓어지지는 않게 한다.
  const s = read("app/stats/page.tsx");
  assert.ok(/min-w-\[min\(14rem,100%\)\]/.test(s),
    "칩의 최소 폭이 뷰포트 폭으로 제한되지 않는다");
  assert.ok(!/min-w-\[14rem\]/.test(s), "제한 없는 고정 min-width가 남아 있다");
  const i = s.indexOf("min-w-[min(14rem,100%)]");
  const block = s.slice(i, i + 200);
  assert.ok(!/shrink-0/.test(block),
    "칩이 shrink-0이라 좁은 화면에서 줄어들지 못한다");
});

test("치지직 팔로워 역할 헤더가 좁은 화면에서 줄바꿈된다", () => {
  // 실측(vw 260/320): `ml-4 shrink-0` 버튼 그룹이 260px를 그대로 차지해 sw 388~389.
  const s = read("app/dashboard/[guildId]/chzzk/page.tsx");
  const i = s.indexOf("{/* 팔로워 역할 지급 */}");
  assert.ok(i > -1, "팔로워 역할 지급 섹션이 사라졌다");
  const block = s.slice(i, i + 1200);
  assert.ok(/flex flex-wrap items-center justify-between/.test(block),
    "헤더가 좁은 화면에서 줄바꿈되지 않는다");
  assert.ok(/min-w-0 flex-1/.test(block), "설명 영역에 min-w-0이 없어 줄어들지 못한다");
  // 주석은 뺀다 — 왜 고쳤는지 설명하는 문장에 옛 클래스명이 들어 있다.
  assert.ok(!/className="[^"]*ml-4 shrink-0/.test(block),
    "wrap과 충돌하는 고정 좌측 여백이 남아 있다");
});

test("홈 히어로의 flex child가 줄어들 수 있다", () => {
  // 실측(vw 260): 포인트 상점 카드가 left=-20 / w=301 로 양쪽으로 삐져나갔다.
  const s = read("app/page.tsx");
  const i = s.indexOf("flex-1 flex justify-center lg:justify-end");
  assert.ok(i > -1, "히어로 목업 컨테이너가 사라졌다");
  // 클래스 순서에 의존하지 않도록 같은 className 문자열 안에서 확인한다.
  const open = s.lastIndexOf('className="', i);
  const block = s.slice(open, i + 60);
  assert.ok(/min-w-0/.test(block),
    "flex child의 기본 min-width:auto 때문에 내용이 컨테이너를 밀어낸다");
});

// ── 증상을 가리는 수정 금지 ─────────────────────────────────────────────────
test("페이지 전체 overflow를 hidden으로 덮지 않는다", () => {
  const css = read("app/globals.css");
  assert.ok(!/(html|body)[^{]*\{[^}]*overflow-x:\s*hidden/.test(css),
    "html/body에 overflow-x:hidden을 걸어 증상을 감춘다");
  for (const f of ["app/layout.tsx", "app/stats/layout.tsx"]) {
    const s = read(f);
    assert.ok(!/overflow-x-hidden/.test(s), `${f}: overflow를 숨겨 결함을 감춘다`);
  }
});

test("탭·내비의 가로 스크롤은 그 컴포넌트가 소유한다", () => {
  // 페이지가 통째로 가로 스크롤되면 안 되고, 넘치는 내비는 자기 안에서 스크롤한다.
  const s = read("components/Sidebar.tsx");
  assert.ok(/overflow-x-auto/.test(s), "하단 내비가 자기 스크롤을 갖지 않는다");
  assert.ok(/min-w-\[44px\]/.test(s), "내비 항목의 44px 계약이 사라졌다");
});

// ── 고정 UI가 본문을 가리거나 넓히지 않는다 ─────────────────────────────────
test("고정 지원 메뉴가 페이지 폭을 늘리지 않는다", () => {
  // 실측(vw 320): fixed 컨테이너가 right=332로 잡혀 페이지가 351px이 됐다.
  const s = read("components/SupportMenu.tsx");
  const i = s.indexOf("fixed right-4");
  assert.ok(i > -1, "지원 메뉴 고정 컨테이너가 사라졌다");
  const block = s.slice(i, i + 200);
  assert.ok(/max-w-\[calc\(100vw-2rem\)\]/.test(block),
    "고정 메뉴가 뷰포트 폭을 넘지 않도록 제한되지 않는다");
});

// ── 기존 계약 회귀 방지 ─────────────────────────────────────────────────────
test("기존 nb-tap 계열 계약이 그대로 남아 있다", () => {
  const css = read("app/globals.css");
  assert.ok(/\.nb-tap\s*\{\s*min-height:\s*44px/.test(css));
  assert.ok(/\.nb-tap-icon\s*\{\s*min-width:\s*44px;\s*min-height:\s*44px/.test(css));
});

test("색상·테마를 건드리지 않는다", () => {
  const css = read("app/globals.css");
  assert.ok(/color-scheme:\s*dark/.test(css), "다크 고정 선언이 사라졌다");
  assert.ok(!/html\.light/.test(css), "라이트 모드가 되살아났다");
});

// 브랜드 링크 목록이 실제 파일과 어긋나지 않는지(새 헤더가 늘면 잡힌다)
test("브랜드 링크 목록이 실제 소스와 일치한다", () => {
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(join(ROOT, dir))) {
      const rel = `${dir}/${name}`;
      if (statSync(join(ROOT, rel)).isDirectory()) { walk(rel); continue; }
      if (!name.endsWith(".tsx")) continue;
      const s = read(rel);
      // href="/" 링크 안에 NexBot 이 있는 파일만
      const re = /href="\/"[\s\S]{0,320}?<\/Link>/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(s))) {
        if (m[0].includes("NexBot")) { found.push(rel); break; }
      }
    }
  };
  walk("app"); walk("components");
  const missing = found.filter((f) => !BRAND_FILES.includes(f));
  assert.deepEqual(missing, [],
    `브랜드 링크가 있는데 목록에 없는 파일: ${missing.join(", ")}`);
});
