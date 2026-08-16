// 공통 저장 계약의 상태 전환 테스트.
//
// 소스 텍스트 대조가 아니라 **실제로 실행해서** 확인한다 — runner가 React에 의존하지
// 않는 이유가 이것이다. 화면별 반영은 별도의 브라우저 fixture로 확인했다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createMutationRunner, IDLE } from "./mutationRunner.ts";
import { isHandledElsewhere, mutationErrorMessage } from "./dashboardErrors.ts";

/** api.ts의 ApiError와 같은 모양(구조만 본다). */
const httpError = (status: number, message = "internal") =>
  Object.assign(new Error(message), { status, name: "ApiError" });

function harness() {
  const states: { pending: boolean; error: string | null; succeeded: boolean }[] = [];
  const runner = createMutationRunner({
    toMessage: mutationErrorMessage,
    isHandledElsewhere,
    onState: (s) => states.push({ ...s }),
  });
  return { runner, states, last: () => states[states.length - 1] ?? IDLE };
}

// ── 200 → 성공 표시 ─────────────────────────────────────────────────────────
test("200이면 성공 표시가 켜지고 오류가 없다", async () => {
  const h = harness();
  await h.runner.run(async () => "ok");
  assert.deepEqual(h.last(), { pending: false, error: null, succeeded: true });
});

test("성공 후 서버 값 재조회가 실행된다", async () => {
  const h = harness();
  let refetched = 0;
  await h.runner.run(async () => "ok", { onSuccess: () => { refetched += 1; } });
  assert.equal(refetched, 1);
});

test("재조회가 실패해도 저장은 성공으로 남는다", async () => {
  // 저장은 이미 2xx로 끝났다. 재조회 실패로 '저장 실패'라고 말하면 거짓말이 된다.
  const h = harness();
  await h.runner.run(async () => "ok", {
    onSuccess: () => { throw new Error("refetch failed"); },
  });
  assert.equal(h.last().succeeded, true);
  assert.equal(h.last().error, null);
});

// ── 상태 코드별 ─────────────────────────────────────────────────────────────
const CASES: [number, RegExp][] = [
  [400, /입력한 값을 확인/],
  [403, /권한/],
  [404, /삭제되었거나|새로고침/],
  [409, /다른 곳에서 값이 바뀌|새로고침/],
  [429, /잠시 후/],
  [500, /잠시 후/],
  [503, /잠시 후/],
];

for (const [status, expected] of CASES) {
  test(`${status}이면 성공 표시 없이 상황에 맞는 문구가 나온다`, async () => {
    const h = harness();
    await h.runner.run(async () => { throw httpError(status); });
    const s = h.last();
    assert.equal(s.succeeded, false, `${status}에 성공 표시가 켜졌다`);
    assert.equal(s.pending, false, `${status} 후 버튼이 진행 중으로 고착했다`);
    assert.match(s.error ?? "", expected);
  });
}

test("401은 오류 문구를 띄우지 않는다(로그인 화면으로 넘어가는 중이다)", async () => {
  const h = harness();
  await h.runner.run(async () => { throw httpError(401); });
  assert.deepEqual(h.last(), { pending: false, error: null, succeeded: false });
});

test("네트워크 실패는 연결 오류로 안내한다", async () => {
  const h = harness();
  await h.runner.run(async () => { throw new TypeError("Failed to fetch"); });
  assert.equal(h.last().succeeded, false);
  assert.match(h.last().error ?? "", /네트워크/);
});

test("응답이 JSON이 아니면 일반 오류로 처리한다", async () => {
  const h = harness();
  await h.runner.run(async () => { throw new SyntaxError("Unexpected token < in JSON"); });
  assert.equal(h.last().succeeded, false);
  assert.match(h.last().error ?? "", /잠시 후/);
});

// ── 내부 정보 노출 금지 ─────────────────────────────────────────────────────
test("백엔드 원문·경로·시크릿이 사용자 문구로 새지 않는다", async () => {
  const leaky = [
    httpError(500, "internal"),
    httpError(500, 'Traceback (most recent call last): File "C:\\app\\web\\backend\\main.py"'),
    httpError(400, "https://nexbot-production.up.railway.app/api/settings/1 실패"),
    httpError(400, "DISCORD_CLIENT_SECRET=abcd1234 invalid"),
    httpError(409, "SELECT * FROM guild_config"),
  ];
  for (const err of leaky) {
    const h = harness();
    await h.runner.run(async () => { throw err; });
    const msg = h.last().error ?? "";
    for (const re of [/internal/, /Traceback/, /railway\.app/, /\/api\//,
                      /DISCORD_CLIENT_SECRET/, /SELECT /, /\.py/]) {
      assert.ok(!re.test(msg), `${re} 가 사용자 문구에 남았다: ${msg}`);
    }
  }
});

test("400의 서버 검증 문구는 안전할 때만 그대로 쓴다", async () => {
  // 서버만 아는 규칙(트리거 형식 등)을 '입력값을 확인해 주세요'로 뭉개면
  // 사용자가 무엇을 고쳐야 하는지 알 수 없다.
  const h = harness();
  await h.runner.run(async () => {
    throw httpError(400, "트리거에는 공백을 넣을 수 없습니다.");
  });
  assert.equal(h.last().error, "트리거에는 공백을 넣을 수 없습니다.");
});

// ── 중복 제출 ───────────────────────────────────────────────────────────────
test("연타 3회에도 요청은 1건만 나간다", async () => {
  const h = harness();
  let calls = 0;
  let release: (v: string) => void = () => {};
  const gate = new Promise<string>((r) => { release = r; });
  const fn = () => { calls += 1; return gate; };

  const a = h.runner.run(fn);
  const b = h.runner.run(fn);
  const c = h.runner.run(fn);
  release("ok");
  await Promise.all([a, b, c]);

  assert.equal(calls, 1, "중복 클릭이 그대로 요청이 됐다");
  assert.equal(h.last().succeeded, true);
});

test("요청이 끝난 뒤에는 다시 보낼 수 있다", async () => {
  const h = harness();
  let calls = 0;
  const fn = async () => { calls += 1; return "ok"; };
  await h.runner.run(fn);
  await h.runner.run(fn);
  assert.equal(calls, 2, "한 번 성공하면 영영 다시 못 보내는 상태가 됐다");
});

// ── 실패 복구 · rollback ────────────────────────────────────────────────────
test("실패 후 버튼이 다시 눌리는 상태로 돌아온다", async () => {
  const h = harness();
  await h.runner.run(async () => { throw httpError(500); });
  assert.equal(h.last().pending, false);
  let called = false;
  await h.runner.run(async () => { called = true; return "ok"; });
  assert.equal(called, true, "실패 후 재시도가 막혔다");
  assert.equal(h.last().error, null, "성공했는데 이전 오류가 남아 있다");
});

test("실패하면 optimistic update를 되돌릴 기회를 준다", async () => {
  const h = harness();
  let rolledBack = false;
  await h.runner.run(async () => { throw httpError(500); },
    { onFailure: () => { rolledBack = true; } });
  assert.equal(rolledBack, true);
});

test("성공하면 rollback을 부르지 않는다", async () => {
  const h = harness();
  let rolledBack = false;
  await h.runner.run(async () => "ok", { onFailure: () => { rolledBack = true; } });
  assert.equal(rolledBack, false);
});

test("새 요청을 시작하면 이전 오류·성공 표시가 지워진다", async () => {
  const h = harness();
  await h.runner.run(async () => { throw httpError(500); });
  assert.ok(h.last().error);
  const p = h.runner.run(async () => "ok");
  assert.deepEqual(h.last(), { pending: true, error: null, succeeded: false });
  await p;
});

test("clearError는 오류만 지우고 성공 표시를 만들지 않는다", async () => {
  const h = harness();
  await h.runner.run(async () => { throw httpError(500); });
  h.runner.clearError();
  assert.deepEqual(h.last(), { pending: false, error: null, succeeded: false });
});
