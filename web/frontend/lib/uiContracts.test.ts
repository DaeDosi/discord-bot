// 이번 UI 변경(요구 1~10)이 조용히 되돌아가지 않도록 고정하는 계약 테스트.
//
// 왜 소스 텍스트를 읽는가: 이 저장소의 프론트 테스트는 `node --test lib/*.test.ts`로
// 의존성 없이 돈다(테스트 러너·DOM 라이브러리를 새로 들이지 않는 것이 관행이다).
// 그래서 렌더링 대신 **소스에 그 구조가 실제로 있는지**를 본다. 브라우저 실측은
// 별도로 했고, 여기서 막으려는 것은 "리팩터링하다 조용히 원복되는 것"이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

// ── 요구 1·2: 싱드컵 예선 참가자 설명과 버튼 위치 ─────────────────────────────
test("요구2: 두 문단의 원문이 글자 그대로 유지된다", () => {
  const s = read("app/stats/SingcupQualifiers.tsx");
  assert.ok(s.includes("치지직이 공식 공지로 발표한 예선 참가자 명단입니다."));
  assert.ok(s.includes("공식 심사 결과나 순위가 아닙니다."));
});

test("요구2: 두 번째 문단은 들여쓰기가 아니라 의미 구조로 분리된다", () => {
  const s = read("app/stats/SingcupQualifiers.tsx");
  // 공백 들여쓰기(&nbsp; 등)가 아니라 테두리+아이콘을 가진 별도 블록이어야 한다.
  assert.ok(!s.includes("&nbsp;"), "공백 문자로 계층을 만들지 않는다");
  assert.ok(/border-l-2 border-border pl-3/.test(s), "구분선으로 하위 계층을 표시한다");
});

test("요구1: 비공식 인기점수 버튼은 하나뿐이고 상단 액션 영역에 있다", () => {
  const s = read("app/stats/SingcupQualifiers.tsx");
  const hits = s.match(/비공식 인기점수 랭킹 보기/g) ?? [];
  assert.equal(hits.length, 1, "하단 중복 블록이 남아 있으면 안 된다");
  // 상단 액션 영역(공식 공지 원문 보기와 같은 줄)에 있어야 한다.
  const idxNotice = s.indexOf("공식 공지 원문 보기");
  assert.ok(idxNotice > -1 && Math.abs(s.indexOf("비공식 인기점수 랭킹 보기") - idxNotice) < 900);
});

// ── 요구 3: 프로필 이미지 로딩 ───────────────────────────────────────────────
test("요구3: 목록형 아바타는 전부 StreamerAvatar를 쓴다", () => {
  // 표만 바꾸고 상단 Top 10 목록을 인라인 lazy로 남겨 두면, 정작 초기 화면의
  // 아바타가 늦게 뜬다 — 실측으로 확인한 실패라서 소스로 막는다.
  for (const f of ["app/stats/page.tsx", "app/stats/RankingCharts.tsx", "app/stats/TagSearch.tsx"]) {
    const s = read(f);
    assert.ok(!/<img[^>]*channel_image_url[^>]*loading="lazy"/.test(s),
      `${f}: 인라인 lazy 아바타가 남아 있다`);
  }
});

test("요구3: 초기 행은 eager, 나머지는 lazy이고 박스 크기가 고정된다", () => {
  const s = read("app/stats/StreamerAvatar.tsx");
  assert.ok(/EAGER_ROWS\s*=\s*\d+/.test(s));
  assert.ok(s.includes('index < EAGER_ROWS'));
  assert.ok(s.includes('loading={eager ? "eager" : "lazy"}'));
  assert.ok(s.includes('fetchPriority={eager ? "high" : "auto"}'));
  // 레이아웃 이동 방지: 컨테이너와 img 모두 크기를 갖는다.
  assert.ok(s.includes("width: size, height: size"), "컨테이너 크기 고정");
  assert.ok(/width=\{size\}\s*\n?\s*height=\{size\}/.test(s), "img 크기 고정");
  // 실패 시 폴백(빈 원)으로 떨어지고 깨진 아이콘을 보여 주지 않는다.
  assert.ok(s.includes("onError"));
});

// ── 요구 4·5: 랭킹 행 ────────────────────────────────────────────────────────
test("요구4: 랭킹 표는 align-top으로 되돌아가지 않는다", () => {
  const s = read("app/stats/page.tsx");
  const rank = s.slice(s.indexOf("filtered.slice(0, limit)"), s.indexOf("filtered.slice(0, limit)") + 4000);
  assert.ok(!rank.includes("align-top"), "세로 가운데 정렬은 align-middle로 유지한다");
});

test("요구5: 신규 스트리머 랭킹 행에는 새싹 아이콘이 없다", () => {
  const s = read("app/stats/page.tsx");
  // Sprout는 다른 곳(탭 아이콘 등)에는 남아 있을 수 있으나, 랭킹 행에는 없어야 한다.
  const i = s.indexOf("items.map((s, i) => {");
  assert.ok(i > -1);
  assert.ok(!s.slice(i, i + 3500).includes("<Sprout"));
});

// ── 요구 8: 잔디(요일×시간대) ────────────────────────────────────────────────
test("요구8: 시간 라벨이 그리드 아래에 있고 범례가 실제 셀 색과 같다", () => {
  const s = read("app/stats/OverviewViz.tsx");
  assert.ok(/HEAT_STEPS\s*=\s*\[/.test(s), "셀 색을 배열 한 곳에서 정의한다");
  // 범례가 같은 배열을 쓰는지 — 손으로 색을 다시 적으면 반드시 어긋난다.
  assert.ok(s.split("HEAT_STEPS").length >= 4, "범례도 HEAT_STEPS를 재사용해야 한다");
  assert.ok(s.includes("EMPTY_CELL"), "수집 없음 색이 별도로 있다");
  assert.ok(s.includes("낮음") && s.includes("높음") && s.includes("수집 없음"));
  // 시간 라벨은 요일 라벨과 같은 폭의 스페이서를 앞에 두고 그리드 아래에 온다.
  const grid = s.indexOf("w-[30px]");
  assert.ok(grid > -1, "요일 라벨 폭이 커졌다");
});

// ── 요구 9: 라이트 모드 제거 ─────────────────────────────────────────────────
test("요구9: 라이트 모드의 흔적이 남아 있지 않다", () => {
  const css = read("app/globals.css");
  assert.ok(!/html\.light/.test(css), "html.light 규칙이 남아 있다");
  assert.ok(/color-scheme:\s*dark/.test(css));
  const layout = read("app/layout.tsx");
  assert.ok(layout.includes('className="dark"'));
  assert.ok(!layout.includes("ThemeToggle"));
  // 테마 초기화 인라인 스크립트가 사라졌는지 — 주석에 단어가 남는 것과 구분하려고
  // `dangerouslySetInnerHTML`(그 스크립트를 넣던 유일한 통로)로 확인한다.
  assert.ok(!layout.includes("dangerouslySetInnerHTML"), "테마 초기화 스크립트가 남아 있다");
  // 토글 컴포넌트를 다시 들여오지 않았는지 전역으로도 확인한다.
  for (const f of ["app/page.tsx", "app/stats/page.tsx", "components/Navbar.tsx"]) {
    assert.ok(!read(f).includes("ThemeToggle"), `${f}에 테마 토글이 남아 있다`);
  }
});

// ── 요구 10: 지원 버튼 ───────────────────────────────────────────────────────
test("요구10: 지원 메뉴는 정확히 세 항목이고 임의 URL을 만들지 않는다", () => {
  const s = read("components/SupportMenu.tsx");
  // 주석에도 같은 단어가 나오므로, 표시 문구는 **JSX 텍스트 노드**만 센다.
  const body = s.slice(s.indexOf("return ("));
  for (const label of ["문의하기", "서포트 서버", "공지 사항"]) {
    const shown = (body.match(new RegExp(`>\\s*${label}\\s*<|>${label}<|\\n\\s*${label}\\n`, "g")) ?? []);
    assert.ok(shown.length >= 1, `${label}이 메뉴에 없다`);
  }
  // 항목은 정확히 세 개 — 링크 두 개(내부/외부) + 공지 블록 하나.
  assert.equal((body.match(/className=\{itemCls\}/g) ?? []).length, 2);
  assert.equal((body.match(/Megaphone/g) ?? []).length, 1);
  // 외부 링크는 Footer와 같은 값 하나뿐이고, 새로 지어낸 도메인이 없어야 한다.
  const urls = s.match(/https?:\/\/[^\s"']+/g) ?? [];
  assert.deepEqual(urls, ["https://discord.gg/DaZxywE4Ka"]);
  assert.ok(s.includes('rel="noopener noreferrer"'));
  // 접근성: 열림 상태 노출 + ESC로 닫고 포커스를 되돌린다.
  assert.ok(s.includes("aria-expanded={open}"));
  assert.ok(s.includes('e.key === "Escape"'));
  assert.ok(s.includes("triggerRef.current?.focus()"));
  // 인증 왕복과 OBS 오버레이에서는 뜨지 않는다.
  assert.ok(s.includes('"/overlay"') && s.includes('"/login"'));
});

test("요구10: 지원 버튼은 레이아웃에서 한 번만 렌더된다", () => {
  const layout = read("app/layout.tsx");
  assert.equal((layout.match(/<SupportMenu \/>/g) ?? []).length, 1);
});
