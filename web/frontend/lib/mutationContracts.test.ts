// 대시보드 **전체** mutation 경로가 공통 저장 계약을 쓰는지 고정한다.
//
// ⚠️ 한계: 소스 텍스트 대조라 실행 결과를 증명하지 못한다. 계약 자체의 동작은
// `mutationRunner.test.ts`가 실제로 실행해서 확인하고, 화면 반영은 격리
// production build + 모의 백엔드로 따로 확인했다. 여기서 막는 것은 원복이다.
//
// 왜 필요한가: UI-R 1차에서 실측 재현한 2개 화면만 고쳤더니, 같은 부류가 남은
// 화면(치지직·포인트·애정도·인증·관리)에서 그대로 재발할 수 있는 상태였다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

/** 주석을 뺀 소스. 왜 이렇게 됐는지 설명하는 문장은 화면에도, 계약에도 영향이 없다.
 *  (설명 문구가 어서션에 걸려 거짓 실패를 내는 일을 겪었다) */
const readCode = (p: string) =>
  read(p).split("\n").filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join("\n");

/** 사용자 설정을 바꾸는 화면 전부. 없는 화면을 넣으면 읽기에서 바로 터진다. */
const MUTATION_PAGES = [
  "app/dashboard/[guildId]/page.tsx",              // 일반 설정
  "app/dashboard/[guildId]/moderation/page.tsx",   // 관리
  "app/dashboard/[guildId]/verification/page.tsx", // 입장 인증
  "app/dashboard/[guildId]/leveling/page.tsx",     // 애정도
  "app/dashboard/[guildId]/points/page.tsx",       // 포인트
  "app/dashboard/[guildId]/chzzk/page.tsx",        // 치지직
];

// 준비 중·비활성 화면. **되살리지 않는다** — mutation이 없어야 정상이다.
const PLACEHOLDER_PAGES = [
  "app/dashboard/[guildId]/soop/page.tsx",
  "app/dashboard/[guildId]/twitter/page.tsx",
  "app/dashboard/[guildId]/youtube/page.tsx",
  "app/dashboard/[guildId]/overlay/page.tsx",
  "app/dashboard/[guildId]/commands/page.tsx",
];

// ── 계약 1·12: 2xx일 때만 성공 ───────────────────────────────────────────────
test("mutation 실패를 빈 catch로 삼키는 화면이 없다", () => {
  for (const page of MUTATION_PAGES) {
    const s = read(page);
    // `.catch(() => {})` / `catch {}` 는 실패를 성공처럼 보이게 만든 원인이다.
    // 조회(best-effort)에도 남아 있지만, mutation 호출에 붙은 것만 잡는다.
    const bad = /(save|add|remove|create|update|delete|adjust|approve|reject|markUsed|sendChatTest|clearWarnings|deleteWarning|deleteLeaderboard)\([^)]*\)\s*\.catch\(\(\)\s*=>\s*\{\}\)/;
    assert.ok(!bad.test(s), `${page}: mutation 실패를 빈 catch로 삼킨다`);
  }
});

test("설정 저장 화면은 공통 계약(useMutation)이나 명시적 오류 상태를 쓴다", () => {
  for (const page of MUTATION_PAGES) {
    const s = read(page);
    assert.ok(/useMutation\(|setSaveError|setCmdError|setLinkError/.test(s),
      `${page}: 공통 저장 계약을 쓰지 않는다`);
  }
});

test("mutation 오류를 화면에 세우는 자리가 있다", () => {
  for (const page of MUTATION_PAGES) {
    const s = read(page);
    assert.ok(/InlineError|DashboardError/.test(s),
      `${page}: 실패를 알릴 자리가 없다`);
  }
});

// ── 계약 11: 백엔드 원문·내부 정보 노출 금지 ────────────────────────────────
test("서버 원문 메시지를 화면 상태에 그대로 넣지 않는다", () => {
  for (const page of MUTATION_PAGES) {
    const s = read(page);
    // `e.message`를 그대로 쓰면 'internal' 같은 내부 문자열이 사용자에게 간다
    // (실측: 포인트 도박 설정 저장 실패, 치지직 명령어 저장 실패).
    assert.ok(!/e instanceof Error \? e\.message/.test(s),
      `${page}: 백엔드 원문을 그대로 표시한다`);
    assert.ok(!/set\w*Error\w*\(e\.message/.test(s),
      `${page}: 백엔드 원문을 그대로 표시한다`);
  }
});

test("OAuth 오류 코드를 그대로 노출하지 않는다", () => {
  const s = read("app/dashboard/[guildId]/chzzk/page.tsx");
  assert.ok(!/`오류: \$\{error\}`/.test(s),
    "치지직 OAuth 리다이렉트의 error 코드가 그대로 화면에 나온다");
});

// ── 계약 13·14: 버튼 복구와 중복 제출 ───────────────────────────────────────
test("실패해도 진행 상태가 풀리도록 try/finally 없는 수동 플래그를 쓰지 않는다", () => {
  // 실측: 애정도 보상 추가가 실패하면 setAdding(false)에 도달하지 못해
  // 버튼이 "추가 중"으로 고착했다.
  const s = readCode("app/dashboard/[guildId]/leveling/page.tsx");
  assert.ok(!/setAdding\(/.test(s), "수동 진행 플래그가 남아 있다");
  assert.ok(/addM\.pending/.test(s), "공통 계약의 pending을 쓰지 않는다");
});

test("중복 클릭 차단이 버튼 disabled에만 의존하지 않는다", () => {
  // 렌더 사이에 들어온 두 번째 클릭은 disabled가 적용되기 전에 도달할 수 있다.
  const s = read("lib/mutationRunner.ts");
  assert.ok(/inFlight/.test(s), "진행 중 요청 공유(in-flight) 장치가 없다");
});

// ── 계약 15: optimistic update rollback / 성공 후 반영 ───────────────────────
test("삭제·승인은 성공한 뒤에만 목록에 반영한다", () => {
  // 실측: 치지직 팔로우 등급 삭제는 실패해도 목록에서 사라졌다.
  const s = read("app/dashboard/[guildId]/chzzk/page.tsx");
  assert.ok(!/followTiers\.remove\([^)]*\)\.catch\(\(\)\s*=>\s*\{\}\);\s*setFollowTiers/.test(s),
    "실패해도 목록에서 지운다");
  const tierIdx = s.indexOf("const removeTier = ");
  assert.ok(tierIdx > -1 && s.slice(tierIdx, tierIdx + 400).includes("onSuccess"),
    "삭제 반영이 onSuccess 안에 있지 않다");

  const p = read("app/dashboard/[guildId]/points/page.tsx");
  for (const fn of ["markUsed", "approve", "reject"]) {
    const i = p.indexOf(`const ${fn} = `);
    assert.ok(i > -1, `${fn}이 사라졌다`);
    assert.ok(p.slice(i, i + 500).includes("onSuccess"),
      `${fn}이 성공 여부와 무관하게 목록을 바꾼다`);
  }
});

test("rollback 지점이 계약에 존재한다", () => {
  const s = read("lib/mutationRunner.ts");
  assert.ok(/onFailure/.test(s), "실패 시 되돌릴 훅이 없다");
});

// ── 계약 16: 성공 후 서버 값 재조회 ─────────────────────────────────────────
test("가능한 화면은 저장 성공 후 서버 값을 다시 읽는다", () => {
  const v = read("app/dashboard/[guildId]/verification/page.tsx");
  assert.ok(/onSuccess:[\s\S]{0,200}getVerification/.test(v),
    "인증 설정이 저장 후 서버 값으로 확정하지 않는다");
  const p = read("app/dashboard/[guildId]/points/page.tsx");
  assert.ok(/onSuccess: \(\) => loadGambling\(\)/.test(p),
    "도박 설정이 저장 후 서버 값으로 확정하지 않는다");
});

// ── /undefined 이동 (R-5와 같은 부류) ───────────────────────────────────────
test("외부 이동 전에 url이 문자열인지 확인한다", () => {
  const s = readCode("app/dashboard/[guildId]/chzzk/page.tsx");
  // 가드 없이 곧장 이동하는 줄(= 같은 줄에 typeof 검사가 없는 줄)만 잡는다.
  const unguarded = s.split("\n")
    .filter((l) => /window\.location\.href = d\??\.url/.test(l))
    .filter((l) => !/typeof d\?\.url === "string"/.test(l));
  assert.equal(unguarded.length, 0,
    `검증 없이 이동하는 자리가 남아 있다: ${unguarded.join(" | ")}`);
  assert.ok((s.match(/typeof d\?\.url === "string"/g) || []).length >= 2,
    "치지직 연동/재연동 두 자리 모두 검증하지 않는다");
});

// ── 폼 전체 오류와 필드 검증 오류의 분리 ────────────────────────────────────
test("입력값 오류는 폼을 가리지 않고 필드 옆에 세운다", () => {
  const p = read("app/dashboard/[guildId]/points/page.tsx");
  // 옵션 2개 미만은 요청을 보내기 전에 잡는 클라이언트 검증이다.
  assert.ok(/setGamblingFieldError\("옵션은 최소 2개/.test(p),
    "클라이언트 검증이 toast로 흘러가 놓치기 쉽다");
  assert.ok(!/showToast\("옵션은 최소 2개/.test(p));
});

test("실패는 toast가 아니라 남아 있는 자리에 표시한다", () => {
  // toast는 2.5초 뒤 사라져 실패를 놓친다. 성공만 toast로 알린다.
  for (const page of MUTATION_PAGES) {
    const s = read(page);
    assert.ok(!/showToast\("저장 실패"\)|showToast\("삭제 실패"\)/.test(s),
      `${page}: 실패를 toast로만 알린다`);
  }
});

// ── 준비 중 화면은 되살리지 않는다 ──────────────────────────────────────────
test("준비 중·비활성 화면에는 mutation이 없다", () => {
  for (const page of PLACEHOLDER_PAGES) {
    const s = read(page);
    assert.ok(!/method: "(POST|PUT|PATCH|DELETE)"/.test(s), `${page}에 mutation이 생겼다`);
    assert.ok(!/api\.\w+\.\w*(save|create|update|delete|remove|add)\(/.test(s),
      `${page}에 mutation이 생겼다`);
  }
});

test("오버레이 화면은 비활성 상태로 남아 있다", () => {
  const s = read("app/dashboard/[guildId]/overlay/page.tsx");
  assert.ok(/비활성화/.test(s), "오버레이가 되살아났다 — 이번 범위에서 제외 항목이다");
  const nav = read("components/Sidebar.tsx");
  assert.ok(!/href: "\/overlay"/.test(nav) || /\/\/ *\{ href: "\/overlay"/.test(nav),
    "사이드바에 오버레이 항목이 되살아났다");
});

// ── 목록 조작 버튼의 접근 이름·터치 영역 ────────────────────────────────────
test("아이콘만 있는 삭제 버튼에 이름과 44px 영역이 있다", () => {
  // 실측: 치지직 등급 삭제 버튼이 24×24px에 접근 가능한 이름도 없었다.
  const targets: [string, RegExp][] = [
    ["app/dashboard/[guildId]/chzzk/page.tsx", /aria-label=\{`\$\{tier\.months\}개월/],
    ["app/dashboard/[guildId]/leveling/page.tsx", /aria-label=\{`레벨 \$\{r\.level\}/],
    ["app/dashboard/[guildId]/moderation/page.tsx", /aria-label="경고 삭제"/],
  ];
  for (const [page, re] of targets) {
    const s = read(page);
    assert.ok(re.test(s), `${page}: 삭제 버튼에 접근 가능한 이름이 없다`);
  }
  // p-1(=8px 패딩)만으로는 44px가 나오지 않는다.
  for (const page of MUTATION_PAGES) {
    const s = readCode(page);
    assert.ok(!/hover:text-danger transition-colors p-1"/.test(s),
      `${page}: 44px 미만 아이콘 버튼이 남아 있다`);
  }
});

test("화살표 탐색이 없으므로 거짓 listbox 계약을 붙이지 않는다", () => {
  const s = read("components/MemberSearch.tsx");
  assert.ok(!/role="listbox"|role="option"|role="combobox"/.test(s),
    "지원하지 않는 키보드 조작을 ARIA로 약속한다");
});
