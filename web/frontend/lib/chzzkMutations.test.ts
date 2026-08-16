// 치지직 3경로(팔로우 등급 삭제 · 채팅 토글 · 채팅 테스트 전송)의 **실행** 테스트.
//
// 왜 이 파일이 따로 있는가: 소스 텍스트 대조는 "버튼과 handler가 실제로 연결됐는지"를
// 증명하지 못한다. 검증은 세 층으로 나눈다.
//   1) 버튼 → handler        : headless Chrome + 격리 모의 백엔드(좌표 클릭, 요청 계측)
//   2) handler → 요청        : **이 파일** — fetch를 스텁해 method·URL·payload를 확인
//   3) 요청 결과 → 화면 상태 : mutationRunner.test.ts (성공 판정·rollback·연타·pending)
// 1은 CI가 없어 자동화하지 않는다. 2·3은 의존성 없이 `node --test`로 돈다.
import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import { BASE, api } from "./api.ts";
import { createMutationRunner } from "./mutationRunner.ts";
import { isHandledElsewhere, mutationErrorMessage } from "./dashboardErrors.ts";

type Call = { url: string; method: string; body: unknown };

let calls: Call[] = [];

/** fetch를 가로채 요청을 기록한다. status로 실패도 흉내 낸다. */
function stubFetch(status = 200, payload: unknown = { ok: true }) {
  calls = [];
  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    calls.push({
      url: String(url),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => payload,
    };
  }) as unknown as typeof fetch;
}

/** 브라우저가 아닌 곳에서도 api.ts가 토큰을 읽을 수 있게 한다. */
function stubStorage() {
  (globalThis as { window?: unknown }).window = { location: { href: "" } };
  (globalThis as { localStorage?: unknown }).localStorage = {
    getItem: () => "test.jwt",
    removeItem: () => {},
    setItem: () => {},
  };
}

const GUILD = "111111111111111111";

function harness() {
  const states: { pending: boolean; error: string | null; succeeded: boolean }[] = [];
  const runner = createMutationRunner({
    toMessage: mutationErrorMessage,
    isHandledElsewhere,
    onState: (s) => states.push({ ...s }),
  });
  return { runner, last: () => states[states.length - 1] };
}

beforeEach(() => { stubStorage(); stubFetch(); });

// ── A. 팔로우 등급 삭제 ─────────────────────────────────────────────────────
describe("팔로우 등급 삭제", () => {
  it("정확한 DELETE endpoint를 1회 부른다", async () => {
    stubFetch(200);
    await api.chzzk.followTiers.remove(GUILD, 7);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, "DELETE");
    assert.equal(calls[0].url, `${BASE}/api/chzzk/${GUILD}/follow-tiers/7`);
  });

  it("실패하면 목록에서 지우지 않는다", async () => {
    // 기준본은 실패를 삼킨 뒤 **무조건** 로컬 목록에서 행을 지웠다.
    stubFetch(500, { detail: "internal" });
    const h = harness();
    let tiers = [{ id: 7 }, { id: 8 }];
    await h.runner.run(() => api.chzzk.followTiers.remove(GUILD, 7), {
      onSuccess: () => { tiers = tiers.filter((t) => t.id !== 7); },
    });
    assert.deepEqual(tiers, [{ id: 7 }, { id: 8 }], "실패했는데 목록에서 사라졌다");
    assert.equal(h.last().succeeded, false);
    assert.ok(h.last().error);
  });

  it("성공하면 목록에서 지운다", async () => {
    stubFetch(200);
    const h = harness();
    let tiers = [{ id: 7 }, { id: 8 }];
    await h.runner.run(() => api.chzzk.followTiers.remove(GUILD, 7), {
      onSuccess: () => { tiers = tiers.filter((t) => t.id !== 7); },
    });
    assert.deepEqual(tiers, [{ id: 8 }]);
    assert.equal(h.last().succeeded, true);
  });

  it("연타 3회에도 요청은 1건이고, 끝나면 pending이 풀린다", async () => {
    stubFetch(500);
    const h = harness();
    const fn = () => api.chzzk.followTiers.remove(GUILD, 7);
    await Promise.all([h.runner.run(fn), h.runner.run(fn), h.runner.run(fn)]);
    assert.equal(calls.length, 1, "연타가 그대로 요청이 됐다");
    assert.equal(h.last().pending, false, "버튼이 진행 중으로 고착했다");
  });
});

// ── B. 채팅 토글 ────────────────────────────────────────────────────────────
describe("채팅 토글", () => {
  it("정확한 PATCH endpoint와 payload를 1회 보낸다", async () => {
    stubFetch(200);
    await api.chzzk.update(GUILD, 1, { chat_enabled: true });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, "PATCH");
    assert.equal(calls[0].url, `${BASE}/api/chzzk/${GUILD}/subscriptions/1`);
    assert.deepEqual(calls[0].body, { chat_enabled: true });
  });

  it("실패하면 서버 확정값 재조회를 부르지 않는다", async () => {
    // controlled input이라 재조회를 부르지 않으면 화면은 이전(서버) 값으로 남는다.
    stubFetch(403, { detail: "이 서버에 대한 관리자 권한이 없습니다." });
    const h = harness();
    let refetched = 0;
    await h.runner.run(() => api.chzzk.update(GUILD, 1, { chat_enabled: true }),
      { onSuccess: () => { refetched += 1; } });
    assert.equal(refetched, 0, "실패했는데 성공 경로가 돌았다");
    assert.equal(h.last().succeeded, false);
    assert.match(h.last().error ?? "", /권한/);
  });

  it("성공하면 서버 확정값을 다시 읽는다", async () => {
    stubFetch(200);
    const h = harness();
    let refetched = 0;
    await h.runner.run(() => api.chzzk.update(GUILD, 1, { chat_enabled: true }),
      { onSuccess: () => { refetched += 1; } });
    assert.equal(refetched, 1);
    assert.equal(h.last().succeeded, true);
  });

  it("실패 후 다시 조작할 수 있다", async () => {
    stubFetch(500);
    const h = harness();
    await h.runner.run(() => api.chzzk.update(GUILD, 1, { chat_enabled: true }));
    stubFetch(200);
    await h.runner.run(() => api.chzzk.update(GUILD, 1, { chat_enabled: true }));
    assert.equal(calls.length, 1, "재시도가 막혔다");
    assert.equal(h.last().error, null);
  });
});

// ── C. 채팅 테스트 전송 ─────────────────────────────────────────────────────
describe("채팅 테스트 전송", () => {
  it("정확한 POST endpoint와 payload를 1회 보낸다", async () => {
    stubFetch(200);
    await api.chzzk.sendChatTest(GUILD, "!출석체크", false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].method, "POST");
    assert.equal(calls[0].url, `${BASE}/api/chzzk/${GUILD}/chat-test`);
    assert.deepEqual(calls[0].body, { content: "!출석체크", as_streamer: false });
  });

  it("실패하면 입력값을 비우지 않는다", async () => {
    // 입력이 사라지면 사용자가 다시 타이핑해야 한다 — 재시도 경로가 끊긴다.
    stubFetch(429, { detail: "rate limited" });
    const h = harness();
    let input = "!출석체크";
    await h.runner.run(() => api.chzzk.sendChatTest(GUILD, input, false),
      { onSuccess: () => { input = ""; } });
    assert.equal(input, "!출석체크", "실패했는데 입력이 지워졌다");
    assert.equal(h.last().succeeded, false);
    assert.match(h.last().error ?? "", /잠시 후/);
  });

  it("성공했을 때만 입력값을 비운다", async () => {
    stubFetch(200);
    const h = harness();
    let input = "!출석체크";
    await h.runner.run(() => api.chzzk.sendChatTest(GUILD, input, false),
      { onSuccess: () => { input = ""; } });
    assert.equal(input, "");
  });
});

// ── 공통: 내부 정보가 사용자 문구로 새지 않는다 ─────────────────────────────
describe("세 경로의 오류 문구", () => {
  const cases: [number, unknown][] = [
    [400, { detail: "internal validation error" }],
    [403, { detail: "이 서버에 대한 관리자 권한이 없습니다." }],
    [409, { detail: "conflict on guild_config" }],
    [429, { detail: "rate limited" }],
    [500, { detail: "Traceback (most recent call last): main.py" }],
  ];

  for (const [status, payload] of cases) {
    it(`${status}에서 백엔드 원문·경로가 노출되지 않는다`, async () => {
      const h = harness();
      for (const call of [
        () => api.chzzk.followTiers.remove(GUILD, 7),
        () => api.chzzk.update(GUILD, 1, { chat_enabled: true }),
        () => api.chzzk.sendChatTest(GUILD, "!출석체크", false),
      ]) {
        stubFetch(status, payload);
        await h.runner.run(call);
        const msg = h.last().error ?? "";
        assert.equal(h.last().succeeded, false, `${status}에 성공 표시가 켜졌다`);
        for (const re of [/internal/, /Traceback/, /main\.py/, /guild_config/,
                          /rate limited/, /\/api\//, /localhost/]) {
          assert.ok(!re.test(msg), `${status} 문구에 ${re} 가 새어 나왔다: ${msg}`);
        }
      }
    });
  }
});
