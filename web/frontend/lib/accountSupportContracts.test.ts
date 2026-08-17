// ACCOUNT-SUPPORT — 설정·회원탈퇴·지원 메뉴·수정 요청 계약(프론트).
//
// 서버 계약은 `tests/test_account_support.py`가 본다.
// 여기서 막는 것은 화면 구조가 조용히 원복되는 것이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");
const SETTINGS = () => read("app/settings/page.tsx");
const FORM = () => read("app/support/correction/page.tsx");
const MENU = () => read("components/SupportMenu.tsx");
const HEADER = () => read("components/SiteHeader.tsx");

// ── 로그인 · 설정 진입 ──────────────────────────────────────────────────────

test("로그인 버튼이 헤더 맨 오른쪽에 있다", () => {
  const s = HEADER();
  const nav = s.indexOf("<HeaderNav />");
  const auth = s.indexOf("<AuthArea />");
  assert.ok(nav > 0 && auth > nav);
  assert.ok(s.includes("로그인"));
});

test("로그인하면 프로필 메뉴에 설정이 있다", () => {
  const s = HEADER();
  assert.ok(s.includes('href="/settings"'));
  assert.ok(s.includes('role="menuitem"'));
});

test("Discord 로그인으로 연결된다", () => {
  const s = HEADER();
  assert.ok(s.includes("api.auth.getLoginUrl()"));
  assert.ok(s.includes('href={loginUrl ?? "/login"}'));
});

// ── 로그아웃 ↔ 회원탈퇴 분리 ───────────────────────────────────────────────

test("헤더 메뉴에는 탈퇴가 없다", () => {
  const s = HEADER();
  const menu = s.split('role="menu"')[1].split("</div>")[0]
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");
  assert.ok(!menu.includes("탈퇴"), "로그아웃과 한 칸 차이면 오조작이 난다");
});

/** 주석을 뺀 코드만 — 주석 위치는 화면 순서와 무관하다. */
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

test("설정 페이지에서 탈퇴는 맨 아래 별도 영역이다", () => {
  const s = code(SETTINGS());
  // 실제 렌더 순서로 본다: 로그아웃 버튼 → 탈퇴 섹션 제목(`</h2>` 앞)
  const logout = s.indexOf("<LogOut size={14}");
  const withdraw = s.search(/회원탈퇴 요청\s*\n\s*<\/h2>/);
  assert.ok(logout > 0 && withdraw > logout, "탈퇴가 로그아웃보다 아래여야 한다");
  assert.ok(s.includes("border-t border-border pt-8"), "구분선으로 영역을 나눈다");
  assert.ok(s.includes("text-red-400"), "위험 영역임을 색으로도 표시");
  assert.ok(s.includes("AlertTriangle"), "색만으로 말하지 않는다");
});

test("로그아웃이 데이터를 지우지 않는다는 것을 밝힌다", () => {
  assert.ok(SETTINGS().includes("데이터는 그대로 남습니다"));
});

// ── 탈퇴 흐름 ───────────────────────────────────────────────────────────────

test("탈퇴 전에 무엇이 저장돼 있는지 보여 준다", () => {
  const s = SETTINGS();
  assert.ok(s.includes("이 계정과 연결된 데이터"));
  assert.ok(s.includes("me.data.classes.map"));
  assert.ok(s.includes("처리 방침 미확정"), "종류별 상태를 표시한다");
  assert.ok(s.includes("notPersonal"), "계정과 무관한 것도 구분해 알린다");
});

test("값 자체를 화면에 뿌리지 않는다", () => {
  const s = SETTINGS();
  assert.ok(s.includes("값 자체(포인트 수치·경고 사유 등)는 이"),
    "종류·건수만 보여 준다는 계약을 화면에도 적는다");
});

test("재확인 절차가 있다", () => {
  const s = SETTINGS();
  assert.ok(s.includes("confirmText === username"),
    "사용자 이름을 그대로 입력해야 통과한다");
  assert.ok(s.includes("사용자 이름"));
  assert.ok(!/onClick=\{\(\) => void submitDelete\(\)\}[\s\S]{0,80}회원탈퇴 요청하기/
    .test(s), "버튼 한 번으로 바로 실행되면 안 된다");
});

test("중복 제출을 동기 잠금으로 막는다", () => {
  const s = SETTINGS();
  assert.ok(s.includes("const inFlight = useRef(false)"));
  assert.ok(s.includes("inFlight.current"), "state 잠금은 같은 tick을 못 막는다");
});

test("불완전한 탈퇴를 완료로 표시하지 않는다", () => {
  const s = SETTINGS();
  assert.ok(s.includes('result.status === "completed"'),
    "status를 읽어야 한다 — ok:true는 접수 성공일 뿐이다");
  assert.ok(s.includes("계정과 데이터는 아직 삭제되지 않았습니다"),
    "'아직 완료되지 않았다'보다 '삭제되지 않았다'가 사실에 가깝다");
  assert.ok(s.includes("로그인과 서비스 이용이 그대로 가능합니다"));
  assert.ok(s.includes("result.blocked"), "무엇이 남았는지 알려 준다");
  assert.ok(s.includes("개인정보처리방침 보기"), "다음 절차로 안내한다");
  // 타입 주석에도 같은 경고가 있어야 한다
  assert.ok(read("lib/types.ts").includes("접수 성공**이지 탈퇴 완료가 아니다"));
});

test("웹 즉시 삭제가 막혀 있음을 감추지 않는다", () => {
  const s = SETTINGS();
  assert.ok(s.includes("me.deletion.enabled"));
  // 버튼을 누르기 **전에** 무슨 일이 일어나는지 적는다.
  assert.ok(s.includes("탈퇴 요청을 접수"));
  assert.ok(s.includes("누르더라도 계정과"));
});

test("제목과 버튼이 '요청'임을 밝힌다", () => {
  const s = SETTINGS();
  // 제목은 `<AlertTriangle/> 회원탈퇴 요청` 형태 — '요청'이 붙어 있어야 한다.
  assert.ok(/회원탈퇴 요청\s*\n\s*<\/h2>/.test(s), "제목이 '회원탈퇴'만이면 안 된다");
  assert.ok(s.includes("회원탈퇴 요청하기"));
  assert.ok(s.includes("탈퇴 요청 보내기"));
});

test("접수 실패 안내가 폼 대신 나온다(수정 요청)", () => {
  const s = FORM();
  assert.ok(s.includes("meta.accepting === false"));
  assert.ok(s.includes("지금은 수정 요청을 접수할 수 없습니다"),
    "폼을 띄워 놓고 제출할 때 503을 주면 사용자는 자기 입력이 잘못된 줄 안다");
});

// ── 지원 메뉴 ───────────────────────────────────────────────────────────────

test("지원 메뉴에 네 항목이 있다", () => {
  const s = MENU();
  for (const t of ["문의하기", "수정 요청", "서포트 서버", "공지 사항"]) {
    assert.ok(s.includes(t), `${t}가 없다`);
  }
});

test("수정 요청은 문의하기와 다른 경로다", () => {
  const s = MENU();
  assert.ok(s.includes('href="/support/correction"'));
  assert.ok(s.includes('href="/contact"'));
});

// ── 수정 요청 폼 ────────────────────────────────────────────────────────────

test("요구한 여섯 필드가 모두 있다", () => {
  const s = FORM();
  for (const l of ["클립 주소 또는 ID", "분류", "문제 설명",
                   "원하는 수정 내용", "근거 자료 주소", "답변받을 이메일"]) {
    assert.ok(s.includes(l), `${l} 필드가 없다`);
  }
  // 선택 항목을 명시한다
  assert.equal((s.match(/\(선택\)/g) ?? []).length, 3);
});

test("모든 입력에 label이 연결돼 있다", () => {
  const s = FORM();
  for (const id of ["cr-clip", "cr-cat", "cr-desc", "cr-fix", "cr-url", "cr-email"]) {
    assert.ok(s.includes(`htmlFor="${id}"`), `${id} label 연결 없음`);
    assert.ok(s.includes(`id="${id}"`), `${id} 입력 없음`);
  }
});

test("길이 한도는 서버가 준 값을 쓴다", () => {
  const s = FORM();
  assert.ok(s.includes("api.support.correctionMeta()"));
  assert.ok(s.includes("meta?.limits ?? FALLBACK_LIMITS"),
    "메타 실패로 폼을 막지 않되 한도는 서버 값이 우선이다");
  assert.ok(s.includes("maxLength={lim.description}"));
});

test("제출 중·성공·실패 세 상태를 구분한다", () => {
  const s = FORM();
  assert.ok(s.includes("보내는 중…"));
  assert.ok(s.includes('role="status"') && s.includes("접수되었습니다"));
  assert.ok(s.includes('role="alert"') && s.includes("접수하지 못했습니다"));
  assert.ok(s.includes("aria-busy={sending}"));
});

test("중복 제출을 막는다", () => {
  const s = FORM();
  assert.ok(s.includes("const inFlight = useRef(false)"));
  assert.ok(s.includes("disabled={!canSubmit}"));
  // 성공하면 폼이 사라져 같은 내용을 다시 보낼 수 없다
  assert.ok(s.includes("done !== null ?"));
});

test("실패 시 포커스를 오류로 옮긴다", () => {
  const s = FORM();
  assert.ok(s.includes("errRef.current?.focus()"));
  assert.ok(s.includes("tabIndex={-1}"), "포커스를 받을 수 있어야 한다");
});

test("공개 화면에 내부 처리 상태를 노출하지 않는다", () => {
  const s = FORM();
  assert.ok(s.includes("개별 진행 상황은 안내하지 않습니다"));
  for (const bad of ["status:", "관리자", "secret", "admin"]) {
    assert.ok(!s.includes(bad), `내부 정보가 화면에 있다: ${bad}`);
  }
});

test("보관 기간 문구를 임의로 만들지 않았다", () => {
  const s = FORM();
  for (const bad of ["6개월", "1년", "30일", "90일", "보관 기간"]) {
    assert.ok(!s.includes(bad), `방침에 없는 보관 기간을 지어냈다: ${bad}`);
  }
});

// ── 크롤링 정책 ─────────────────────────────────────────────────────────────

test("로그인 게이트·폼 화면은 크롤링에서 제외된다", () => {
  const s = read("app/robots.ts");
  assert.ok(s.includes('"/settings"'));
  assert.ok(s.includes('"/support/*"'));
  // 공개 콘텐츠 페이지는 그대로 크롤 가능해야 한다
  assert.ok(!s.includes('"/stats"'));
  assert.ok(!s.includes('"/faq"'));
});
