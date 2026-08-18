/* 수집 토큰 복사 — 성공·실패·재클릭·Clipboard 미지원 계약.
 *
 * 토큰은 10분·1회용이고 **화면에 딱 한 번** 나온다. 그래서 손으로 드래그하다
 * 앞뒤가 잘리면 그 토큰은 그냥 버려야 한다. 복사 버튼은 그 사고를 없애려는 것이지
 * 토큰을 어딘가에 보관하려는 것이 아니다.
 *
 * 이 모듈이 **하지 않는 것**이 계약의 절반이다: 저장하지 않고, 로그로 남기지 않고,
 * 어디로도 보내지 않고, 복사했다고 서버 상태를 건드리지 않는다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { COPY_RESET_MS, copyToken } from "./copyToken.ts";

const TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789-_ABCDEFGHIJ";

function env(over: {
  clipboard?: { writeText: (s: string) => Promise<void> } | null;
  exec?: (cmd: string) => boolean;
} = {}) {
  const wrote: string[] = [];
  const nav = over.clipboard === null ? {} : {
    clipboard: over.clipboard ?? {
      writeText: async (s: string) => { wrote.push(s); },
    },
  };
  const calls: string[] = [];
  return {
    wrote, calls,
    deps: {
      navigator: nav as Navigator,
      fallback: (s: string) => { calls.push(s); return over.exec ? over.exec(s) : true; },
    },
  };
}

test("성공하면 토큰 전체를 한 번에 복사한다", async () => {
  const e = env();
  assert.equal(await copyToken(TOKEN, e.deps), "copied");
  assert.deepEqual(e.wrote, [TOKEN]);
  assert.equal(e.wrote[0].length, TOKEN.length, "잘려서 복사됐다");
});

test("Clipboard API가 실패하면 fallback으로 넘어간다", async () => {
  const e = env({ clipboard: { writeText: async () => { throw new Error("denied"); } } });
  assert.equal(await copyToken(TOKEN, e.deps), "copied");
  assert.deepEqual(e.calls, [TOKEN]);
});

test("Clipboard API가 아예 없어도 fallback으로 복사한다", async () => {
  const e = env({ clipboard: null });
  assert.equal(await copyToken(TOKEN, e.deps), "copied");
  assert.deepEqual(e.calls, [TOKEN]);
});

test("fallback까지 실패하면 실패를 실패로 알린다", async () => {
  const e = env({ clipboard: null, exec: () => false });
  assert.equal(await copyToken(TOKEN, e.deps), "failed");
});

test("빈 토큰은 복사하지 않는다", async () => {
  const e = env();
  assert.equal(await copyToken("", e.deps), "failed");
  assert.equal(await copyToken("   ", e.deps), "failed");
  assert.deepEqual(e.wrote, []);
});

test("여러 번 눌러도 매번 전체를 복사한다(재클릭)", async () => {
  const e = env();
  for (let i = 0; i < 3; i++) assert.equal(await copyToken(TOKEN, e.deps), "copied");
  assert.deepEqual(e.wrote, [TOKEN, TOKEN, TOKEN]);
});

test("복원 시간이 사람이 읽을 수 있는 범위다", () => {
  assert.ok(COPY_RESET_MS >= 1000 && COPY_RESET_MS <= 5000,
    `복원이 ${COPY_RESET_MS}ms — 너무 짧거나 길다`);
});

// ── 보관·전송 금지 ─────────────────────────────────────────────────────────
test("토큰을 어디에도 저장하지 않는다", () => {
  const src = readFileSync(new URL("./copyToken.ts", import.meta.url), "utf8");
  for (const bad of ["localStorage", "sessionStorage", "chrome.storage",
                     "indexedDB", "document.cookie"]) {
    assert.ok(!src.includes(bad), `copyToken이 ${bad}를 쓴다`);
  }
});

test("토큰을 로그·네트워크로 내보내지 않는다", () => {
  const src = readFileSync(new URL("./copyToken.ts", import.meta.url), "utf8");
  for (const bad of ["console.log", "console.error", "console.warn",
                     "fetch(", "sendBeacon", "XMLHttpRequest"]) {
    assert.ok(!src.includes(bad), `copyToken이 ${bad}를 쓴다`);
  }
});

test("복사가 서버 상태를 건드리지 않는다 — 토큰이 소비되지 않는다", async () => {
  // 이 모듈에는 네트워크 호출 자체가 없다(위 테스트). 여기서는 반환값이
  // 서버와 무관한 순수 결과라는 것을 고정한다.
  const e = env();
  const r = await copyToken(TOKEN, e.deps);
  assert.ok(r === "copied" || r === "failed");
  assert.equal(e.calls.length + e.wrote.length, 1, "예상 밖 부수효과가 있다");
});

// ── 화면 계약 ───────────────────────────────────────────────────────────────
test("패널의 복사 버튼이 접근성·터치 계약을 지킨다", () => {
  const src = readFileSync(
    new URL("../app/nexadmin/PikuCollectorPanel.tsx", import.meta.url), "utf8");
  assert.ok(src.includes("copyToken"), "패널이 copyToken을 쓰지 않는다");
  assert.ok(/aria-label=/.test(src), "복사 버튼에 aria-label이 없다");
  assert.ok(src.includes("복사됨"), "성공 피드백 문구가 없다");
  // 시각 크기는 작아도 히트 영역은 44px여야 한다(UI-S 계약의 `nb-tap`).
  assert.ok(/nb-tap/.test(src), "44px 히트 영역 클래스가 없다");
  // 토큰 원문을 DOM 속성으로 복제하지 않는다 — 화면에 이미 한 번 있다.
  assert.ok(!/data-token=/.test(src), "토큰을 data 속성으로 복제한다");
  assert.ok(!/value=\{token\.token\}/.test(src), "토큰을 input value로 복제한다");
});
