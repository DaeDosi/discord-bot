// 지원 메뉴 노출 경로 계약.
//
// 이 목록이 곧 사양이다. 예전 구현은 `startsWith("/login")`이었는데, 접두 비교는
// `/logistics` 같은 **정상 페이지까지** 숨긴다. 세그먼트 단위 판정을 여기서 고정한다.
import { test } from "node:test";
import assert from "node:assert/strict";

import { shouldShowSupportMenu, isSupportMenuExcludedPath } from "./supportMenu.ts";

const 숨김 = [
  "/login",
  "/login/",
  "/login/callback",      // 하위 경로가 생겨도 함께 숨는다
  "/callback",            // 실제 OAuth 콜백 경로
  "/callback/",
  "/verify",              // 입장 인증 전용 화면
  "/verify/abc123",
  "/overlay",             // OBS 브라우저 소스
  "/overlay/gambling/tok",
  "/overlay/missions/tok",
];

const 노출 = [
  "/",
  "/stats",
  "/stats?tab=ranking",   // usePathname은 쿼리를 안 붙이지만 방어적으로 통과해야 한다
  "/stats#top",
  "/stats/singcup",
  "/stats/singcup/live",
  "/stats/streamer/abc",
  "/nexadmin",
  "/dashboard",
  "/dashboard/123456789",
  "/dashboard/123456789/chzzk",
  "/contact",
  "/faq",
  "/guide",
  "/about",
  "/privacy",
  "/terms",
  "/status",
];

test("인증·오버레이 경로에서는 숨는다", () => {
  for (const p of 숨김) {
    assert.equal(shouldShowSupportMenu(p), false, `${p}에서 지원 메뉴가 보이면 안 된다`);
  }
});

test("정상 공개 화면에서는 보인다", () => {
  for (const p of 노출) {
    assert.equal(shouldShowSupportMenu(p), true, `${p}에서 지원 메뉴가 보여야 한다`);
  }
});

test("이름이 비슷할 뿐인 정상 경로를 숨기지 않는다", () => {
  // 접두 비교로 되돌아가면 여기서 바로 깨진다.
  for (const p of ["/logistics", "/login-guide", "/verify-guide", "/callbacks",
                   "/overlays", "/loginhelp"]) {
    assert.equal(shouldShowSupportMenu(p), true, `${p}는 정상 페이지다`);
  }
});

test("경로를 모르면 숨긴다", () => {
  // 잘못 띄우면 인증 흐름을 가린다 — 모를 때는 숨기는 쪽이 안전하다.
  for (const p of [null, undefined, "", "stats", "http://x/login"]) {
    assert.equal(isSupportMenuExcludedPath(p as string | null), true);
  }
});

test("대소문자가 달라도 인증 경로는 숨는다", () => {
  assert.equal(shouldShowSupportMenu("/Login"), false);
  assert.equal(shouldShowSupportMenu("/OVERLAY/x"), false);
});
