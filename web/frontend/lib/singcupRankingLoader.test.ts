// finalizing 자동 재확인 (UI-P 차단 조건 I).
//
// 검증하려는 것은 "사용자가 아무것도 하지 않아도 두 번째 요청이 나간다"이다.
// 타이머와 fetch를 주입할 수 있게 로더를 React 밖으로 떼어 놨기 때문에 DOM 없이
// 실제 동작을 돌려볼 수 있다(브라우저 실측은 별도로 했다).
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  AUTO_DEFAULT_MS, AUTO_MAX_MS, AUTO_MIN_MS, MAX_AUTO_CHECKS,
  FinalRankingLoader, parseRetryAfterMs, type FetchOutcome,
} from "./singcupRankingLoader.ts";

const FINAL = { rankingFinal: true, rankingFinalizedAt: 1786000000, streamers: [{}] };
const FINALIZING = { status: "finalizing", frozen: true, detail: "준비 중" };

/** 가짜 타이머 — 예약된 콜백을 수동으로 돌린다(실제 초를 기다리지 않는다). */
function fakeTimers() {
  let seq = 0;
  const jobs = new Map<number, { fn: () => void; ms: number }>();
  return {
    setTimer: (fn: () => void, ms: number) => { const id = ++seq; jobs.set(id, { fn, ms }); return id; },
    clearTimer: (h: unknown) => { jobs.delete(h as number); },
    /** 예약된 것 전부 실행. 실행 중 새로 잡힌 예약은 다음 tick으로 미룬다. */
    async tick() {
      const now = [...jobs.entries()];
      for (const [id, j] of now) { jobs.delete(id); j.fn(); }
      await new Promise((r) => setTimeout(r, 0));
    },
    pending: () => jobs.size,
    delays: () => [...jobs.values()].map((j) => j.ms),
  };
}

/** 대본대로 응답하는 로더를 만든다. */
function makeLoader(script: FetchOutcome[], opts: { onChange?: (s: unknown) => void } = {}) {
  const t = fakeTimers();
  const states: string[] = [];
  let i = 0;
  const seen: FetchOutcome[] = [];
  const loader = new FinalRankingLoader({
    fetchOnce: async () => {
      const o = script[Math.min(i, script.length - 1)];
      i += 1;
      seen.push(o);
      return o;
    },
    onChange: (s) => { states.push(s.status); opts.onChange?.(s); },
    setTimer: t.setTimer,
    clearTimer: t.clearTimer,
  });
  return { loader, timers: t, states, calls: () => i, seen };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

const OK: FetchOutcome = { status: 200, body: FINAL, retryAfter: null };
const WAIT: FetchOutcome = { status: 503, body: FINALIZING, retryAfter: "10" };

// ── Retry-After 파싱 ────────────────────────────────────────────────────────
test("Retry-After를 안전한 범위로 자른다", () => {
  assert.equal(parseRetryAfterMs("30"), 30_000);
  assert.equal(parseRetryAfterMs("10"), 10_000);
  // 하한 미만 → 하한(tight loop 금지)
  assert.equal(parseRetryAfterMs("1"), AUTO_MIN_MS);
  // 상한 초과 → 상한(화면이 사실상 멈추지 않게)
  assert.equal(parseRetryAfterMs("99999"), AUTO_MAX_MS);
  // 0·음수·문자열 오류·없음 → 기본값
  for (const bad of ["0", "-5", "abc", "", null, undefined,
                     "Wed, 21 Oct 2026 07:28:00 GMT"]) {
    assert.equal(parseRetryAfterMs(bad as string | null), AUTO_DEFAULT_MS, String(bad));
  }
});

// ── 1·2. 클릭 없이 두 번째 요청 → final 전환 ────────────────────────────────
test("1·2: 503 finalizing이면 클릭 없이 다시 요청하고, 200이면 final로 바뀐다", async () => {
  const { loader, timers, states, calls } = makeLoader([WAIT, OK]);
  loader.start();
  await settle();
  assert.equal(calls(), 1);
  assert.equal(loader.snapshot().status, "finalizing");
  assert.equal(timers.pending(), 1, "자동 재확인이 예약되지 않았다");
  assert.deepEqual(timers.delays(), [10_000], "Retry-After 값을 쓰지 않았다");

  await timers.tick();                       // 사용자는 아무것도 누르지 않았다
  await settle();
  assert.equal(calls(), 2, "두 번째 요청이 자동으로 나가지 않았다");
  assert.equal(loader.snapshot().status, "final");
  assert.ok(states.includes("finalizing") && states.at(-1) === "final");
  assert.equal(timers.pending(), 0, "final인데 타이머가 남아 있다");
});

// ── 3. 다시 503이면 제한 범위에서 재예약 ────────────────────────────────────
test("3: 계속 503이면 매번 다시 예약한다", async () => {
  const { loader, timers, calls } = makeLoader([WAIT]);
  loader.start();
  await settle();
  for (let n = 0; n < 3; n++) {
    assert.equal(timers.pending(), 1, `${n}회차에 예약이 없다`);
    await timers.tick();
    await settle();
  }
  assert.equal(calls(), 4);
  assert.equal(loader.snapshot().status, "finalizing");
});

// ── 4. 상한 도달 시 error ───────────────────────────────────────────────────
test("4: 자동 재확인 상한에 닿으면 error로 넘어가 폴링을 멈춘다", async () => {
  const { loader, timers, calls } = makeLoader([WAIT]);
  loader.start();
  await settle();
  for (let n = 0; n < MAX_AUTO_CHECKS + 2; n++) {
    if (timers.pending() === 0) break;
    await timers.tick();
    await settle();
  }
  assert.equal(loader.snapshot().status, "error", "영구 503인데 계속 폴링한다");
  assert.equal(timers.pending(), 0, "error인데 타이머가 남아 있다");
  assert.ok(calls() <= MAX_AUTO_CHECKS + 1, `요청이 ${calls()}회로 상한을 넘었다`);
});

// ── 5·6. 자동 폴링을 하지 않는 응답들 ───────────────────────────────────────
test("5: 계약이 다른 503은 자동 재요청 없이 error", async () => {
  for (const body of [undefined, {}, { status: "finalizing" }, { frozen: true },
                      "<html>503</html>"]) {
    const { loader, timers, calls } = makeLoader([{ status: 503, body, retryAfter: "10" }]);
    loader.start();
    await settle();
    assert.equal(loader.snapshot().status, "error", JSON.stringify(body));
    assert.equal(timers.pending(), 0, "계약이 다른 503에 폴링을 걸었다");
    assert.equal(calls(), 1);
  }
});

test("6: network error와 일반 4xx/5xx는 자동 재요청이 없다", async () => {
  const cases: FetchOutcome[] = [
    { status: null, body: undefined, retryAfter: null },      // 네트워크 실패
    { status: 500, body: { detail: "x" }, retryAfter: null },
    { status: 404, body: {}, retryAfter: null },
    { status: 401, body: {}, retryAfter: null },
    { status: 403, body: {}, retryAfter: null },
    { status: 429, body: {}, retryAfter: "5" },
    { status: 200, body: "not json", retryAfter: null },      // 파싱 실패 흉내
  ];
  for (const c of cases) {
    const { loader, timers, calls } = makeLoader([c]);
    loader.start();
    await settle();
    assert.equal(loader.snapshot().status, "error", String(c.status));
    assert.equal(timers.pending(), 0, `${c.status}에 폴링을 걸었다`);
    assert.equal(calls(), 1);
  }
});

// ── 7·8. 타이머·요청 중복 금지 ──────────────────────────────────────────────
test("7: 여러 번 start해도 타이머는 하나, 요청도 하나", async () => {
  const { loader, timers, calls } = makeLoader([WAIT]);
  loader.start(); loader.start(); loader.start();
  await settle();
  assert.equal(calls(), 1, "동시 start가 요청을 늘렸다");
  assert.equal(timers.pending(), 1, "타이머가 여러 개 잡혔다");
});

test("8: 자동 재확인과 수동 재시도가 겹쳐도 요청은 하나", async () => {
  const { loader, timers, calls } = makeLoader([WAIT]);
  loader.start();
  await settle();
  // 예약된 자동 재확인이 아직 남은 상태에서 수동 재시도를 누른다.
  loader.retry();
  loader.retry();                             // 연타
  await settle();
  assert.equal(calls(), 2, "수동 재시도가 요청을 중복 발생시켰다");
  assert.equal(timers.pending(), 1, "수동 재시도 뒤에 타이머가 둘이 됐다");
});

test("8-b: 수동 재시도는 자동 재확인 예산을 새로 시작한다", async () => {
  const { loader, timers } = makeLoader([WAIT]);
  loader.start();
  await settle();
  for (let n = 0; n < MAX_AUTO_CHECKS + 2; n++) {
    if (timers.pending() === 0) break;
    await timers.tick();
    await settle();
  }
  assert.equal(loader.snapshot().status, "error");
  loader.retry();
  await settle();
  assert.equal(loader.snapshot().status, "finalizing", "수동 재시도로 예산이 초기화되지 않았다");
  assert.equal(timers.pending(), 1);
});

// ── 9. unmount 정리 ─────────────────────────────────────────────────────────
test("9: dispose하면 타이머가 사라지고 이후 상태 변경이 없다", async () => {
  const seen: string[] = [];
  const { loader, timers } = makeLoader([WAIT], { onChange: (s) => seen.push((s as { status: string }).status) });
  loader.start();
  await settle();
  const before = seen.length;
  loader.dispose();
  assert.equal(timers.pending(), 0, "dispose 후에도 타이머가 남았다");
  await timers.tick();
  await settle();
  assert.equal(seen.length, before, "dispose 후에도 상태가 갱신됐다");
});

test("9-b: dispose는 진행 중 요청을 취소한다", async () => {
  let aborted = false;
  const loader = new FinalRankingLoader({
    fetchOnce: (signal) => new Promise((resolve) => {
      signal.addEventListener("abort", () => { aborted = true; resolve(OK); });
    }),
    onChange: () => {},
    setTimer: () => 1,
    clearTimer: () => {},
  });
  loader.start();
  await settle();
  loader.dispose();
  await settle();
  assert.ok(aborted, "진행 중 요청이 취소되지 않았다");
});

// ── 10. final/live에서는 자동 요청 0회 ──────────────────────────────────────
test("10: final·live 상태에서는 자동 요청이 없다", async () => {
  for (const first of [OK, { status: 200, body: { frozen: false }, retryAfter: null }]) {
    const { loader, timers, calls } = makeLoader([first as FetchOutcome]);
    loader.start();
    await settle();
    assert.equal(timers.pending(), 0, "final/live인데 폴링을 예약했다");
    await timers.tick();
    await settle();
    assert.equal(calls(), 1, "final/live에서 자동 요청이 나갔다");
  }
});

// ── 11. 미처리 예외 없음 ────────────────────────────────────────────────────
test("11: fetch가 던져도 미처리 예외 없이 error로 떨어진다", async () => {
  const rejections: unknown[] = [];
  const onRej = (e: unknown) => rejections.push(e);
  process.on("unhandledRejection", onRej);
  try {
    const t = fakeTimers();
    const loader = new FinalRankingLoader({
      fetchOnce: async () => { throw new Error("boom"); },
      onChange: () => {},
      setTimer: t.setTimer,
      clearTimer: t.clearTimer,
    });
    loader.start();
    await settle();
    await settle();
    assert.equal(loader.snapshot().status, "error");
    assert.equal(t.pending(), 0);
  } finally {
    process.off("unhandledRejection", onRej);
  }
  assert.deepEqual(rejections, [], "미처리 Promise 예외가 발생했다");
});
