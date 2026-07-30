// 공유 캐시 + in-flight 중복 제거 테스트.
//
// 실행: web/frontend 에서
//   node --test lib/api.test.ts
// (Node 24는 타입 표기를 스스로 벗겨 내므로 별도 러너·의존성이 없다. 이 저장소에는
//  프론트 테스트 프레임워크가 없어서, 새 의존성을 들이는 대신 표준 러너를 쓴다.)
import assert from "node:assert/strict";
import { beforeEach, describe, it, mock } from "node:test";

import {
  SINGCUP_MAIN_LIMIT,
  api,
  sharedAge,
  sharedClear,
  sharedGet,
  sharedPeek,
  singcupMainKey,
} from "./api.ts";
import { shouldPollNow, shouldRefetchOnRevisit } from "./pollPolicy.ts";

const KEY = singcupMainKey(SINGCUP_MAIN_LIMIT);

function stubFetch(impl: (url: string) => Promise<unknown>) {
  const calls: string[] = [];
  globalThis.fetch = (async (url: string) => {
    calls.push(String(url));
    return impl(String(url));
  }) as unknown as typeof fetch;
  return calls;
}

const ok = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: async () => body });

describe("공유 캐시 + in-flight 중복 제거", () => {
  beforeEach(() => sharedClear());

  it("동시 10개 호출이 실제 fetch 1회만 만든다", async () => {
    let resolveIt: (v: unknown) => void = () => {};
    const gate = new Promise((r) => { resolveIt = r; });
    const calls = stubFetch(async () => {
      await gate;
      return { ok: true, status: 200, json: async () => ({ streamers: [] }) };
    });

    const all = Promise.all(
      Array.from({ length: 10 }, () => api.singcup.main(SINGCUP_MAIN_LIMIT)));
    resolveIt(null);
    const results = await all;

    assert.equal(calls.length, 1);
    // 모든 소비자가 같은 응답 객체를 받는다(각자 파싱하지 않는다)
    assert.ok(results.every((r) => r === results[0]));
  });

  it("TTL 안의 반복 호출은 네트워크가 0회", async () => {
    const calls = stubFetch(() => ok({ streamers: [] }));
    await api.singcup.main(SINGCUP_MAIN_LIMIT);
    await api.singcup.main(SINGCUP_MAIN_LIMIT);
    await api.singcup.main(SINGCUP_MAIN_LIMIT);
    assert.equal(calls.length, 1);
  });

  it("TTL이 지나면 정확히 한 번 다시 받는다", async () => {
    let n = 0;
    const fetcher = mock.fn(async () => ({ n: n++ }));
    const key = "ttl-test";
    await sharedGet(key, 20, fetcher);
    await sharedGet(key, 20, fetcher);
    await new Promise((r) => setTimeout(r, 30));
    await sharedGet(key, 20, fetcher);
    await sharedGet(key, 20, fetcher);
    assert.equal(fetcher.mock.callCount(), 2);
  });

  it("force는 캐시를 건너뛴다(수동 새로고침)", async () => {
    const calls = stubFetch(() => ok({ streamers: [] }));
    await api.singcup.main(SINGCUP_MAIN_LIMIT);
    await api.singcup.main(SINGCUP_MAIN_LIMIT, { force: true });
    assert.equal(calls.length, 2);
  });

  it("실패는 캐시에 남지 않고 곧바로 재시도된다", async () => {
    let fail = true;
    const calls = stubFetch(async () => {
      if (fail) throw new Error("network down");
      return { ok: true, status: 200, json: async () => ({ streamers: [1] }) };
    });

    await assert.rejects(() => api.singcup.main(SINGCUP_MAIN_LIMIT));
    assert.equal(sharedPeek(KEY, 60_000), null);      // 실패는 캐시되지 않는다
    fail = false;
    const good = await api.singcup.main(SINGCUP_MAIN_LIMIT);
    assert.deepEqual(good, { streamers: [1] });
    assert.equal(calls.length, 2);
  });

  it("HTTP 오류 응답을 정상 데이터로 캐시하지 않는다", async () => {
    stubFetch(async () => ({
      ok: false, status: 429, json: async () => ({ detail: "요청이 너무 많습니다." }),
    }));
    await assert.rejects(() => api.singcup.main(SINGCUP_MAIN_LIMIT), /HTTP 429/);
    assert.equal(sharedPeek(KEY, 60_000), null);
  });

  it("limit이 다르면 캐시를 공유하지 않는다", async () => {
    const calls = stubFetch((url) =>
      ok({ from: url.includes("limit=50") ? "small" : "full" }));
    const a = await api.singcup.main(50);
    const b = await api.singcup.main(SINGCUP_MAIN_LIMIT);
    assert.equal(calls.length, 2);
    assert.notDeepEqual(a, b);
  });

  it("두 화면이 같은 키를 써서 마운트가 겹쳐도 1회", async () => {
    const calls = stubFetch(() => ok({ streamers: [] }));
    // 랭킹 탭과 라이브 페이지는 같은 상수를 쓴다 → 같은 캐시 키
    await Promise.all([
      api.singcup.main(SINGCUP_MAIN_LIMIT),
      api.singcup.main(SINGCUP_MAIN_LIMIT),
    ]);
    assert.equal(calls.length, 1);
  });

  it("sharedAge가 캐시 나이를 알려 준다", async () => {
    stubFetch(() => ok({ streamers: [] }));
    assert.equal(sharedAge(KEY), null);
    await api.singcup.main(SINGCUP_MAIN_LIMIT);
    const age = sharedAge(KEY);
    assert.ok(age !== null && age >= 0 && age < 1000);
  });
});

describe("폴링 정책", () => {
  it("숨겨진 탭에서는 폴링하지 않는다", () => {
    assert.equal(shouldPollNow("hidden"), false);
    assert.equal(shouldPollNow("visible"), true);
  });

  it("복귀 시에는 오래됐을 때만 다시 받는다", () => {
    assert.equal(shouldRefetchOnRevisit(5_000, 60_000), false);
    assert.equal(shouldRefetchOnRevisit(59_999, 60_000), false);
    assert.equal(shouldRefetchOnRevisit(60_000, 60_000), true);
    assert.equal(shouldRefetchOnRevisit(null, 60_000), true);   // 캐시가 없으면 받는다
  });
});
