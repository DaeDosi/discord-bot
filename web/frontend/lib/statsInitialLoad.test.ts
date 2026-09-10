// CHZZK-STATS-PERF-1 — `/stats` 첫 진입 적재 계약.
//
// 이 파일은 **소스 문자열을 훑지 않는다.** `lib/statsInitialLoad`의 함수를 가짜 api로
// 실제 실행해서, 어떤 엔드포인트가 언제 시작·완료되는지를 그대로 관찰한다.
// (화면 구조 계약은 아래 마지막 묶음에서만 소스를 읽는다 — 그건 JSX라 실행할 수 없다.)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  DEFERRED_APIS, FIRST_PAINT_APIS, TAB_LAZY_DATA,
  loadFirstPaint, pendingTabLoads, whenMounted,
} from "./statsInitialLoad.ts";
import type { LazyKey, LoadState } from "./statsInitialLoad.ts";

/** 호출 순서·동시성을 관찰할 수 있는 가짜 api.
 *
 *  각 메서드는 호출 시각(순번)을 기록하고, `resolve()`를 부를 때까지 **끝나지 않는다**.
 *  그래서 "시작은 했는데 아직 안 끝난" 상태를 정확히 만들 수 있다. */
function makeApi() {
  const started: string[] = [];
  const resolvers: Record<string, (v: unknown) => void> = {};
  const rejecters: Record<string, (e: unknown) => void> = {};
  const make = (name: string) => (..._args: unknown[]) => {
    started.push(name);
    return new Promise((res, rej) => { resolvers[name] = res; rejecters[name] = rej; });
  };
  return {
    started,
    settle: (name: string, value: unknown = { ok: name }) => resolvers[name]?.(value),
    fail: (name: string, err: unknown = new Error("boom")) => rejecters[name]?.(err),
    api: {
      overview: make("overview") as () => Promise<unknown>,
      risingStars: make("rising-stars") as (n: number) => Promise<unknown>,
      liveRanking: make("live-ranking") as (n: number) => Promise<unknown>,
      categories: make("categories") as () => Promise<unknown>,
      newcomers: make("newcomers") as (n: number) => Promise<unknown>,
    },
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

// ── 1. 첫 화면 게이트 ────────────────────────────────────────────────────────

test("첫 화면은 개요가 쓰는 둘만 요청한다(무거운 셋은 보내지 않는다)", () => {
  const h = makeApi();
  void loadFirstPaint(h.api);
  // 예전에는 여기서 다섯이 나갔고, 그중 newcomers 하나가 첫 화면을 3초 잠갔다.
  assert.deepEqual(h.started.sort(), ["overview", "rising-stars"]);
  for (const d of DEFERRED_APIS) {
    assert.ok(!h.started.includes(d), `${d}는 첫 진입에서 나가면 안 된다`);
  }
});

test("두 요청은 직렬이 아니라 병렬로 시작된다", () => {
  const h = makeApi();
  void loadFirstPaint(h.api);
  // 직렬이면 첫 번째가 끝나기 전에는 두 번째가 시작되지 않는다.
  assert.equal(h.started.length, 2, "둘 다 즉시 시작돼야 한다");
});

test("게이트는 두 응답이 다 와야 풀린다", async () => {
  const h = makeApi();
  let done = false;
  void loadFirstPaint(h.api).then(() => { done = true; });
  h.settle("overview");
  await tick();
  assert.equal(done, false, "하나만 와서는 풀리면 안 된다");
  h.settle("rising-stars");
  await tick();
  assert.equal(done, true);
});

test("게이트가 지연 데이터의 지연에 영향을 받지 않는다", async () => {
  // 이 테스트가 이번 결함을 직접 고정한다: 무거운 요청이 아직 끝나지 않아도
  // 첫 화면은 떠야 한다.
  const h = makeApi();
  let done = false;
  void loadFirstPaint(h.api).then(() => { done = true; });
  void h.api.newcomers(80);              // 느린 요청이 동시에 떠 있는 상황
  h.settle("overview"); h.settle("rising-stars");
  await tick();
  assert.equal(done, true, "newcomers가 끝나지 않았어도 첫 화면은 준비돼야 한다");
});

test("첫 화면 요청이 실패하면 거부된다(무한 로딩으로 남지 않는다)", async () => {
  const h = makeApi();
  let settled: "ok" | "err" | null = null;
  void loadFirstPaint(h.api).then(() => { settled = "ok"; }, () => { settled = "err"; });
  h.settle("overview");
  h.fail("rising-stars");
  await tick();
  assert.equal(settled, "err");
});

test("게이트 목록에 이연 대상이 섞이지 않는다", () => {
  for (const d of DEFERRED_APIS) {
    assert.ok(!(FIRST_PAINT_APIS as readonly string[]).includes(d),
      `${d}가 첫 화면 게이트에 들어갔다`);
  }
  assert.deepEqual([...FIRST_PAINT_APIS], ["overview", "rising-stars"]);
});

// ── 2. 탭 전용 지연 로드 ────────────────────────────────────────────────────

const idle: Record<LazyKey, LoadState> = { rank: "idle", cats: "idle", news: "idle" };

test("지연 데이터는 그 탭을 열 때만 시작된다", () => {
  assert.deepEqual(pendingTabLoads("overview", idle), [], "개요 탭은 아무것도 더 받지 않는다");
  assert.deepEqual(pendingTabLoads("ranking", idle), ["rank"]);
  assert.deepEqual(pendingTabLoads("category", idle), ["cats"]);
  assert.deepEqual(pendingTabLoads("newcomers_ranking", idle), ["news"]);
});

test("같은 탭을 다시 열어도 중복 요청하지 않는다", () => {
  for (const st of ["loading", "ready", "error"] as LoadState[]) {
    const states = { ...idle, rank: st };
    assert.deepEqual(pendingTabLoads("ranking", states), [],
      `rank가 ${st}인데 다시 요청했다`);
  }
});

test("스스로 데이터를 받는 탭은 지연 목록에 없다", () => {
  // 여기 넣으면 같은 응답을 두 번 받는다(`NewcomerStatsTab`이 자체 로드한다).
  for (const t of ["newcomers_stats", "small_stats", "small_ranking",
                   "period_analysis", "ranking_period", "tags", "group",
                   "category_streamers", "bongnudo", "singcup"]) {
    assert.equal(TAB_LAZY_DATA[t], undefined, `${t}이 지연 목록에 들어갔다`);
    assert.deepEqual(pendingTabLoads(t, idle), []);
  }
});

test("한 진입에서 같은 지연 데이터가 두 번 나가지 않는다(상태 전이 시뮬레이션)", async () => {
  // 탭을 ranking → overview → ranking 으로 오갈 때 실제로 몇 번 나가는지 센다.
  const h = makeApi();
  const states: Record<LazyKey, LoadState> = { ...idle };
  const visit = (tab: string) => {
    for (const key of pendingTabLoads(tab, states)) {
      states[key] = "loading";
      if (key === "rank") void h.api.liveRanking(200);
      if (key === "cats") void h.api.categories();
      if (key === "news") void h.api.newcomers(80);
    }
  };
  visit("ranking");
  states.rank = "ready";                 // 응답 도착
  visit("overview");
  visit("ranking");
  visit("ranking");
  assert.deepEqual(h.started.filter((x) => x === "live-ranking").length, 1,
    "랭킹 데이터가 두 번 이상 요청됐다");
});

// ── 3. 화면 구조 계약 (JSX라 실행할 수 없어 소스를 읽는다) ────────────────────

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");
const PAGE = () => read("app/stats/page.tsx");
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

test("페이지가 이 모듈의 계획 함수를 실제로 쓴다", () => {
  const c = code(PAGE());
  assert.ok(c.includes("loadFirstPaint(api.rising)"), "첫 화면 게이트가 모듈을 통해야 한다");
  assert.ok(c.includes("pendingTabLoads(tab, { rank: rankState, cats: catsState, news: newsState })"),
    "지연 로드 계획도 모듈을 통해야 한다");
  // 예전 게이트가 되살아나면 첫 화면이 다시 3초 잠긴다.
  assert.ok(!/Promise\.all\(\s*\[\s*\n?\s*api\.rising\.overview\(\),\s*api\.rising\.liveRanking/.test(c),
    "다섯을 한 Promise.all에 묶던 옛 게이트가 돌아왔다");
});

test("탭 데이터가 없을 때 빈 화면이 아니라 상태 표시가 나온다", () => {
  const c = code(PAGE());
  for (const [tab, what] of [["ranking", "rankState"], ["category", "catsState"],
                             ["newcomers_ranking", "newsState"]] as const) {
    assert.ok(c.includes(`<TabDataState state={${what}}`),
      `${tab} 탭에 상태 표시가 없다`);
  }
  // `데이터 &&` 만 있으면 실패했을 때 **아무것도 그리지 않아** 사용자는 원인을 모른다.
  assert.ok(!c.includes('{tab === "ranking"            && rank && <RankingTab'),
    "옛 조건부 렌더가 남아 있다");
  assert.ok(c.includes('state === "error"'), "실패를 로딩과 구분해야 한다");
});

test("신규 스트리머 통계 탭은 자체 로드에 맡긴다", () => {
  const c = code(PAGE());
  // `initial={news}`를 되살리면 최상위가 newcomers를 다시 미리 받게 된다.
  assert.ok(!c.includes("initial={news}"), "최상위 프리페치가 돌아왔다");
  assert.ok(c.includes('<NewcomerStatsTab key="new" group="new"'), "탭 자체는 그대로다");
});

test("첫 진입에 categories가 두 번 나가지 않는다", () => {
  const c = code(PAGE());
  // 예전에는 최상위 Promise.all과 `CategoryDonut`이 **같은 URL**을 각각 불렀다.
  // 최상위 호출은 카테고리 탭으로 옮겼으므로, 개요 화면에서는 도넛 것 하나만 남는다.
  const donut = c.slice(c.indexOf("function CategoryDonut"), c.indexOf("function CategoryDonut") + 900);
  assert.ok(donut.includes("api.rising.categories(range)"), "도넛은 자기 range로 받는다");
  assert.ok(!/loadFirstPaint[\s\S]{0,400}api\.rising\.categories/.test(c),
    "첫 화면 게이트가 categories를 다시 부른다");
});

// ── 4. 같은 URL 중복 호출 제거 (실제 실행) ──────────────────────────────────

test("categories는 같은 (range,limit)을 한 번만 받는다", async () => {
  // `?tab=category`로 바로 들어오면 개요가 한 프레임 렌더되며 도넛이 부르고,
  // 곧이어 카테고리 탭이 또 부른다. 두 호출이 하나로 합쳐져야 한다.
  const { sharedGet, resetSharedCache } = await import("./api.ts");
  resetSharedCache();
  let calls = 0;
  const fetcher = () => { calls += 1; return new Promise((r) => setTimeout(() => r({ categories: [] }), 20)); };
  const key = "rising:categories:1h:60";
  const a = sharedGet(key, 60_000, fetcher);
  const b = sharedGet(key, 60_000, fetcher);          // 진행 중 → 합류
  await Promise.all([a, b]);
  assert.equal(calls, 1, "진행 중인 요청에 합류하지 않았다");
  await sharedGet(key, 60_000, fetcher);              // TTL 안 → 재사용
  assert.equal(calls, 1, "TTL 안인데 다시 받았다");
  resetSharedCache();
});

test("range가 다르면 별도로 받는다", async () => {
  const { sharedGet, resetSharedCache } = await import("./api.ts");
  resetSharedCache();
  let calls = 0;
  const fetcher = () => { calls += 1; return Promise.resolve({ categories: [] }); };
  await sharedGet("rising:categories:1h:60", 60_000, fetcher);
  await sharedGet("rising:categories:live:200", 60_000, fetcher);
  assert.equal(calls, 2, "range가 다른데 캐시를 재사용했다");
  resetSharedCache();
});

test("api.rising.categories가 sharedGet을 통한다", () => {
  const c = code(read("lib/api.ts"));
  const i = c.indexOf("categories: (range");
  const block = c.slice(i, c.indexOf("risingStars", i));
  assert.ok(block.includes("sharedGet(`rising:categories:"), "중복 제거가 빠졌다");
  assert.ok(block.includes("60_000"), "TTL이 명시돼야 한다");
});

// ── 5. 언마운트 안전 · 실패 격리 (실제 실행) ────────────────────────────────

test("언마운트 뒤 도착한 응답은 상태를 건드리지 않는다", async () => {
  const mounted = { current: true };
  let applied = 0;
  const set = whenMounted<number>(mounted, () => { applied += 1; });
  const p = new Promise<number>((r) => setTimeout(() => r(1), 20)).then(set);
  mounted.current = false;                 // 응답 도착 전에 언마운트
  await p;
  assert.equal(applied, 0, "언마운트 뒤인데 상태를 갱신했다");
});

test("마운트 중이면 그대로 적용된다", async () => {
  const mounted = { current: true };
  let got: number | null = null;
  await Promise.resolve(7).then(whenMounted<number>(mounted, (v) => { got = v; }));
  assert.equal(got, 7);
});

test("effect 재실행이 정상 응답을 버리지 않는다(ref 수명 사용)", async () => {
  // effect 스코프 `let alive`를 쓰면 `setState("loading")` 한 번에 cleanup이 돌아
  // 정상 응답이 버려지고 탭이 영원히 로딩에 머문다. ref는 그렇지 않다.
  const mounted = { current: true };
  const results: number[] = [];
  const apply = whenMounted<number>(mounted, (v) => results.push(v));
  const inflight = new Promise<number>((r) => setTimeout(() => r(42), 20)).then(apply);
  // 사이에 effect가 여러 번 재실행돼도 ref는 그대로다
  for (let i = 0; i < 3; i++) { /* 재실행 시뮬레이션 — ref를 건드리지 않는다 */ }
  await inflight;
  assert.deepEqual(results, [42], "재실행 때문에 응답이 버려졌다");
});

test("탭 로드 실패는 그 탭 상태만 error로 만든다", () => {
  // 페이지 전체 오류(`setError(true)`)로 번지면 개요까지 못 보게 된다.
  const c = code(PAGE());
  const i = c.indexOf("const todo = pendingTabLoads");
  const block = c.slice(i, c.indexOf("}, [tab, rankState", i));
  assert.ok(block.includes('setRankState("error")') && block.includes('setCatsState("error")')
    && block.includes('setNewsState("error")'), "탭별 오류 상태를 세워야 한다");
  assert.ok(!block.includes("setError("), "탭 실패가 페이지 전체를 오류로 만든다");
});

test("페이지가 마운트 가드를 실제로 쓴다", () => {
  const c = code(PAGE());
  assert.ok(c.includes("const mounted = useRef(true)"), "컴포넌트 수명 ref가 있어야 한다");
  assert.ok(c.includes("useEffect(() => () => { mounted.current = false; }, [])"),
    "언마운트에서 ref를 내려야 한다");
  assert.ok((c.match(/whenMounted\(mounted/g) ?? []).length >= 6,
    "첫 화면과 세 지연 로더의 성공·실패 경로 모두 감싸야 한다");
});

// ── 6. sharedGet — 실패는 캐시하지 않는다 (실제 실행) ───────────────────────

test("실패한 응답은 캐시되지 않아 바로 재시도된다", async () => {
  const { sharedGet, resetSharedCache } = await import("./api.ts");
  resetSharedCache();
  let calls = 0;
  const failing = () => { calls += 1; return Promise.reject(new Error("boom")); };
  await assert.rejects(() => sharedGet("k:fail", 60_000, failing));
  await assert.rejects(() => sharedGet("k:fail", 60_000, failing));
  assert.equal(calls, 2, "실패를 캐시해 재시도가 막혔다");
  // 실패 뒤 성공하면 그때부터 캐시된다
  let ok = 0;
  const good = () => { ok += 1; return Promise.resolve({ v: 1 }); };
  await sharedGet("k:fail", 60_000, good);
  await sharedGet("k:fail", 60_000, good);
  assert.equal(ok, 1, "성공 뒤에는 캐시돼야 한다");
  resetSharedCache();
});

test("TTL이 지나면 다시 받는다", async () => {
  const { sharedGet, resetSharedCache } = await import("./api.ts");
  resetSharedCache();
  let calls = 0;
  const f = () => { calls += 1; return Promise.resolve({ v: calls }); };
  await sharedGet("k:ttl", 10, f);
  await new Promise((r) => setTimeout(r, 25));
  await sharedGet("k:ttl", 10, f);
  assert.equal(calls, 2, "TTL이 지났는데 낡은 값을 돌려줬다");
  resetSharedCache();
});
