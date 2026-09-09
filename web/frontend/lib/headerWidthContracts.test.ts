// HDR-1 — 공통 헤더가 좁은 폭에서 무너지지 않는다는 계약.
//
// 소스 텍스트를 읽는 이유는 이 저장소의 다른 프론트 테스트와 같다. 브라우저
// 실측(320/390/768/918/1024/1440 · 125%/150%)은 따로 하고, 여기서 막는 것은
// **구조가 조용히 원복되는 것**이다. 918px에서 `NexBot`이 `Ne…`로 잘리고
// `사용 방법`이 글자 단위로 세로로 쪼개졌던 회귀가 이 파일이 지키는 대상이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

const HEADER = () => read("components/SiteHeader.tsx");
const CORRECTION = () => read("app/support/correction/page.tsx");
const CONTACT = () => read("app/contact/page.tsx");

/** 주석을 걷어낸 "실제로 렌더되는 코드". 주석에도 같은 낱말이 나오므로,
 *  존재/부재를 세는 단언은 반드시 이걸 통과한 문자열로 해야 한다. */
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

// ── 1. 두 페이지가 공통 헤더를 같은 계약으로 쓴다 ────────────────────────────

test("수정 요청·문의하기는 공통 SiteHeader만 쓴다(헤더 복제 없음)", () => {
  for (const [name, s] of [["correction", CORRECTION()], ["contact", CONTACT()]] as const) {
    const c = code(s);
    assert.ok(c.includes('import SiteHeader from "@/components/SiteHeader"'),
      `${name}: 공통 헤더를 import 해야 한다`);
    assert.equal((c.match(/<SiteHeader/g) ?? []).length, 1,
      `${name}: 헤더는 한 번만 렌더한다`);
    // 자체 헤더 막대를 다시 만들면 로고·검색·프로필이 두 벌이 된다.
    assert.ok(!/<header/.test(c), `${name}: 페이지가 자기 <header>를 만들면 안 된다`);
  }
});

test("두 페이지의 헤더 레이아웃이 메인 통계 페이지와 같다", () => {
  // `/stats`는 `maxWidth="full"`이다. 예전에는 두 페이지가 본문 폭에 맞춘
  // `3xl`/`4xl`을 넘겼고, 그 좁은 컨테이너 안에 `md` 이상의 3영역이 압축되면서
  // 918px 부근부터 잘림·세로 쪼개짐이 났다.
  const stats = code(read("app/stats/page.tsx"));
  assert.ok(stats.includes('maxWidth="full"'), "기준이 되는 /stats 값이 바뀌었다");
  for (const [name, s] of [["correction", CORRECTION()], ["contact", CONTACT()]] as const) {
    const c = code(s);
    assert.ok(c.includes('<SiteHeader maxWidth="full" />'),
      `${name}: /stats와 같은 헤더 폭 계약이어야 한다`);
    assert.ok(!/maxWidth="(3xl|4xl)"/.test(c),
      `${name}: 좁은 컨테이너로 되돌리면 918px에서 다시 깨진다`);
  }
});

// ── 2. 눌려도 잘리거나 세로로 쪼개지지 않는다 ────────────────────────────────

test("좌우 칸의 하한이 max-content라 내용보다 좁아지지 않는다", () => {
  const s = HEADER();
  assert.ok(
    s.includes("md:grid-cols-[minmax(max-content,1fr)_minmax(0,2fr)_minmax(max-content,1fr)]"),
    "좌우 하한이 max-content여야 브랜드·우측 라벨이 눌리지 않는다");
  // 좌우 하한이 0이면 가운데가 자기 폭을 고집하는 순간 좌우가 눌린다.
  assert.ok(!s.includes("md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]"),
    "하한 0짜리 예전 트랙으로 되돌리면 안 된다");
});

test("`사용 방법`은 줄바꿈되지 않고 줄어들지도 않는다", () => {
  const s = code(HEADER());
  // 한국어는 **글자 사이에서도** 줄바꿈이 된다. 폭이 모자란 순간 `사용 방법`이
  // 한 글자씩 세로로 내려간 원인이 이것이다.
  const nav = s.slice(s.indexOf('aria-label="주요 메뉴"'), s.indexOf("</nav>"));
  assert.ok(nav.includes("shrink-0"), "오른쪽 칸에서 눌리면 안 된다");
  assert.ok(nav.includes("whitespace-nowrap"), "줄바꿈을 막아야 한다");
  assert.ok(nav.includes("사용 방법"), "링크 자체는 그대로 있어야 한다");
});

test("브랜드 텍스트를 말줄임표로 감추지 않는다", () => {
  const s = code(HEADER());
  assert.ok(s.includes('<span className="whitespace-nowrap">NexBot</span>'),
    "워드마크는 줄바꿈만 막는다");
  assert.ok(s.includes('<span className="whitespace-nowrap">치지직 통계</span>'),
    "현재 위치 라벨도 마찬가지다");
  // `Ne…` / `치지직 …`은 고친 게 아니라 덮은 것이다.
  assert.ok(!/truncate">NexBot/.test(s), "워드마크에 truncate 금지");
  assert.ok(!/truncate">치지직 통계/.test(s), "치지직 통계에 truncate 금지");
});

test("검색창은 남는 폭을 유동적으로 쓰고 칸 안에서 가운데 정렬된다", () => {
  const s = HEADER();
  assert.ok(s.includes("mx-auto hidden w-full min-w-0 justify-center md:flex md:max-w-[680px]"),
    "w-full + max-w + mx-auto 조합이어야 한다");
  // grid item은 기본이 stretch라, `mx-auto` 없이 `max-w`만 주면 왼쪽에 붙는다.
  assert.ok(!/md:w-\[min\(\d+vw/.test(s),
    "vw 기반 고정 폭은 컨테이너가 좁은 페이지에서 좌우를 밀어낸다");
  assert.ok(!s.includes("searchWidth"), "maxWidth별 폭 분기를 다시 들이지 않는다");
});

test("가로 넘침을 overflow-x-hidden으로 덮지 않는다", () => {
  for (const s of [HEADER(), CORRECTION(), CONTACT()]) {
    assert.ok(!code(s).includes("overflow-x-hidden"),
      "넘침을 감추면 원인이 그대로 남는다");
  }
});

// ── 3. 기존 헤더 기능이 그대로 남아 있다 ─────────────────────────────────────

test("햄버거·검색·프로필 기능과 키보드 동작이 유지된다", () => {
  const s = HEADER();
  assert.ok(s.includes("aria-expanded={burgerExpanded}") && s.includes("aria-controls={burgerControls}"),
    "햄버거 상태 전달");
  assert.ok(s.includes('role="combobox"') && s.includes('role="listbox"'),
    "검색 combobox 의미");
  assert.ok(s.includes('e.key === "ArrowDown"') && s.includes('e.key === "Escape"'),
    "검색 키보드 조작");
  assert.ok(s.includes('aria-haspopup="menu"') && s.includes('role="menu"'),
    "프로필 드롭다운");
  assert.ok(s.includes("triggerRef.current?.focus()"), "ESC 후 포커스 복귀");
  // 드롭다운은 오른쪽 끝 기준이어야 viewport 밖으로 나가지 않는다.
  assert.ok(s.includes("absolute right-0 top-full"), "드롭다운 위치 계약");
});
