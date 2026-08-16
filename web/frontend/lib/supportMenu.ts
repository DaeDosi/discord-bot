// 우측 하단 지원 메뉴의 **노출 정책**만 담은 순수 함수.
//
// 컴포넌트에서 분리한 이유: 이 판정은 잘못되면 조용히 틀린다(인증 화면에 버튼이
// 떠도 화면은 멀쩡해 보인다). 렌더링과 떼어 놓으면 경로 목록을 테스트로 고정할 수 있다.
//
// ── 판정 단위는 **경로 세그먼트**다 ─────────────────────────────────────────
// 예전 구현은 `pathname.startsWith("/login")`이었다. 접두 비교는 `/logistics`,
// `/verify-guide` 같은 **정상 공개 페이지까지** 함께 숨긴다. 그래서 첫 세그먼트가
// 정확히 일치할 때만 제외한다.
//
// ── 여기서 다루지 않는 것 ───────────────────────────────────────────────────
// 404·error 화면은 경로만 봐서는 알 수 없다(어떤 경로든 404가 될 수 있다).
// 그쪽은 `app/not-found.tsx`·`app/error.tsx`가 다는 표시와 `globals.css`의
// 규칙이 담당한다. 이 함수에 억지로 넣으려 하지 말 것.

/** 첫 세그먼트가 이 목록에 있으면 지원 메뉴를 띄우지 않는다. */
export const SUPPORT_MENU_EXCLUDED_SEGMENTS = [
  "login",     // 로그인 시작 화면(디스코드로 넘어가는 중)
  "callback",  // OAuth 콜백 처리 화면
  "verify",    // 입장 인증 전용 화면
  "overlay",   // OBS 브라우저 소스 — 방송 화면에 버튼이 찍히면 안 된다
] as const;

/** 지원 메뉴를 **숨겨야** 하는 경로인가. */
export function isSupportMenuExcludedPath(pathname: string | null | undefined): boolean {
  // 경로를 모르는 순간에는 띄우지 않는다. 잘못 띄우는 쪽이 잘못 숨기는 쪽보다 나쁘다
  // (인증 흐름을 가린다).
  if (!pathname) return true;

  // `usePathname()`은 쿼리·해시를 붙이지 않지만, 이 함수는 단독으로도 맞아야 하므로
  // 방어적으로 잘라 낸다.
  const path = pathname.split("?")[0].split("#")[0];
  if (!path.startsWith("/")) return true;

  // "/login/" → ["", "login", ""] → 첫 세그먼트는 "login"
  const first = (path.split("/")[1] ?? "").toLowerCase();
  return (SUPPORT_MENU_EXCLUDED_SEGMENTS as readonly string[]).includes(first);
}

/** 지원 메뉴를 **띄워야** 하는 경로인가. */
export function shouldShowSupportMenu(pathname: string | null | undefined): boolean {
  return !isSupportMenuExcludedPath(pathname);
}
