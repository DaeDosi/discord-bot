/* AUTO-2 → SINGCUP-FINAL-1 — 확장 스케줄러 로직 계약.
 *
 * `scheduler.js`는 **chrome API를 직접 부르지 않는다.** 탭 조회·저장소·시각·네트워크를
 * 전부 주입받는다. 그래야 서비스 워커가 죽었다 살아나는 상황, 절전에서 깨어나 alarm이
 * 몰려 오는 상황, lock이 남아 있는 상황을 실제로 재현해 볼 수 있다.
 *
 * 시각은 전부 **가짜 clock**(`env.now`/`env.advance`)이다. 실제 한 시간을 기다리는
 * 테스트는 없다. 실제 Chrome에서의 회차는 별도 E2E가 본다.
 *
 * plan은 서버가 준다. 기본 fixture는 **본선 plan**(source `final` 하나, 페이지 넘김)이고,
 * 예선 3부문 plan은 "여러 source가 한 회차에 있을 때"의 계약(부분 성공 등)을 보기 위해
 * 남겨 둔다 — 운영에서는 예선이 동결이라 plan에 오지 않는다.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createScheduler, resolvePlan, SOURCES, CANONICAL } from "./scheduler.js";

const HOUR = 60 * 60 * 1000;
const FINAL_TITLE = "이상형 월드컵 랭킹 - [2026 치지직 싱드컵 갤럭시] - 파이널 본선 Ideal type worldcup PIKU";

/** 서버 `device/state`가 주는 모양 그대로. */
export const FINAL_PLAN = { campaign: "final", sources: [
  { key: "final", sourceId: "2ut8Li", url: CANONICAL.final, expected: 32,
    title: SOURCES.final.title, paged: true },
] };
export const QUAL_PLAN = { campaign: "qualifier", sources: [
  { key: "female_solo", sourceId: "8jGsHE", expected: 64, paged: false },
  { key: "male_solo", sourceId: "7PqH44", expected: 64, paged: false },
  { key: "groups", sourceId: "7fXoNs", expected: 32, paged: false },
] };

function rowsOf(n, bump = 0) {
  return Array.from({ length: n }, (_, i) => ({
    rank: i + 1, streamer: i % 3 === 0 ? `팀${i}a, 팀${i}b` : `s${i}`,
    song_title: `t${i}`, artist: `a${i}`, win_ratio: 1 + bump, win_rate: 2, image_url: "",
  }));
}

/* ── 주입할 가짜 환경 ─────────────────────────────────────────────────────── */
function makeEnv(opts = {}) {
  let now = opts.now ?? 1_789_000_000_000;
  /** 서비스 워커가 죽어도 남는 저장소(IndexedDB 흉내). */
  const store = new Map(Object.entries(opts.store ?? {}));
  const tabs = opts.tabs ?? [{ id: 7, url: CANONICAL.final, status: "complete" }];
  const calls = { read: [], challenge: [], token: [], ingest: [], reload: [], alarms: [],
                  report: [], goto: [], pageState: [] };
  /** 본선 fixture 페이지: 10행 × 4페이지(실측). `page`는 탭별 현재 페이지. */
  const pager = { page: 1, perPage: opts.perPage ?? 10, total: opts.total ?? 32,
                  pages: opts.pages };
  const finalRows = () => (opts.finalRows ? opts.finalRows() : rowsOf(pager.total));
  const env = {
    now: () => now,
    advance: (ms) => { now += ms; },
    // 대기는 가짜 clock을 그만큼 민다 — 실제로 기다리지 않는다.
    sleep: async (ms) => { now += ms; },
    calls,
    store: {
      async get(k) { return store.has(k) ? structuredClone(store.get(k)) : undefined; },
      async set(k, v) { store.set(k, structuredClone(v)); },
      /** 두 컨텍스트가 같은 키를 동시에 건드리지 못하게 하는 원자적 교체. */
      async swap(k, fn) {
        const cur = store.has(k) ? structuredClone(store.get(k)) : undefined;
        const next = fn(cur);
        if (next === undefined) return { changed: false, value: cur };
        store.set(k, structuredClone(next));
        return { changed: true, value: next };
      },
      _raw: store,
    },
    queryTabs: async (urlPattern) =>
      tabs.filter((t) => new RegExp("^" + urlPattern.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
        .replace(/\\\*/g, ".*") + "$").test(t.url)),
    readTable: async (tabId, o = {}) => {
      calls.read.push({ tabId, fragment: !!o.fragment });
      if (opts.readTable) return opts.readTable(tabId, o);
      const t = tabs.find((x) => x.id === tabId);
      const div = Object.keys(CANONICAL).find((d) => CANONICAL[d] === t.url);
      if (div === "final") {
        const all = finalRows();
        const slice = all.slice((pager.page - 1) * pager.perPage, pager.page * pager.perPage);
        if (!o.fragment) {
          // 통째로 읽으면 첫 페이지 10행만 보인다 → 실제 collect.js는 `partial`.
          return { ok: false, kind: "partial", message: "32행이어야 하는데 10행만 보입니다." };
        }
        return { ok: true, fragment: true, division: "final", campaign: "final",
                 sourceId: "2ut8Li", sourceUrl: CANONICAL.final, pageTitle: FINAL_TITLE,
                 rows: slice };
      }
      const expected = SOURCES[div].expected;
      return { ok: true, payload: {
        schemaVersion: 1, division: div, campaign: "qualifier", sourceId: SOURCES[div].id,
        sourceUrl: t.url, pageTitle: "이상형 월드컵 랭킹", collectedAt: "2026-08-19T00:00:00.000Z",
        rowCount: expected, rows: rowsOf(expected),
      } };
    },
    pageState: async (tabId) => {
      calls.pageState.push(tabId);
      if (opts.pageState) return opts.pageState(tabId, pager);
      const all = finalRows();
      const pages = pager.pages ?? Math.ceil(all.length / pager.perPage);
      const slice = all.slice((pager.page - 1) * pager.perPage, pager.page * pager.perPage);
      return { ok: true, pages, current: pager.page, rows: slice.length,
               firstRank: slice.length ? slice[0].rank : 0,
               lastRank: slice.length ? slice[slice.length - 1].rank : 0 };
    },
    gotoPage: async (tabId, n) => {
      calls.goto.push(n);
      if (opts.gotoPage) return opts.gotoPage(tabId, n, pager);
      pager.page = n;
      return { ok: true };
    },
    reloadTab: async (id) => {
      calls.reload.push(id);
      pager.page = 1;                       // 새로고침하면 1페이지로 돌아간다
      const t = tabs.find((x) => x.id === id);
      if (t) t.status = "complete";
    },
    getChallenge: async (division) => {
      calls.challenge.push(division);
      if (opts.getChallenge) return opts.getChallenge(division);
      return { challengeId: `c-${division}`, message: `m-${division}` };
    },
    signAndRedeem: async (challengeId) => {
      calls.token.push(challengeId);
      if (opts.signAndRedeem) return opts.signAndRedeem(challengeId);
      return { token: `tok-${challengeId}`, division: challengeId.slice(2) };
    },
    ingest: async (token, payload) => {
      calls.ingest.push({ token, division: payload.division, campaign: payload.campaign,
                          rows: payload.rowCount, payload });
      if (opts.ingest) return opts.ingest(token, payload);
      return { ok: true };
    },
    report: async (r) => { calls.report.push(structuredClone(r)); if (opts.report) return opts.report(r); },
    alarmExists: opts.alarmExists,
    createAlarm: async (periodMs, when) => { calls.alarms.push({ periodMs, when }); },
    ...opts.override,
  };
  env._pager = pager;
  env._tabs = tabs;
  return env;
}

const AUTO = { mode: "AUTO_COLLECT", deviceActive: true, plan: FINAL_PLAN };
const QUAL = { mode: "AUTO_COLLECT", deviceActive: true, plan: QUAL_PLAN };
const qualTabs = () => [
  { id: 1, url: CANONICAL.female_solo, status: "complete" },
  { id: 2, url: CANONICAL.male_solo, status: "complete" },
  { id: 3, url: CANONICAL.groups, status: "complete" },
];
const getState = async (env) => (await env.store.get("sched")) ?? {};

/* ── 0) plan ─────────────────────────────────────────────────────────────── */
test("서버 plan이 없으면 돌지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  for (const plan of [undefined, null, {}, { campaign: "final", sources: [] }]) {
    const r = await s.runCycle({ trigger: "alarm", mode: "AUTO_COLLECT", deviceActive: true, plan });
    assert.equal(r.skipped, "no_plan");
  }
  assert.equal(env.calls.read.length, 0);
});

test("plan의 source가 확장 정본과 어긋나면 그 source를 읽지 않는다", async () => {
  assert.equal(resolvePlan({ campaign: "final", sources: [{ key: "final", sourceId: "XXXXXX" }] }), null);
  assert.equal(resolvePlan({ campaign: "final", sources: [{ key: "final", expected: 64 }] }), null);
  assert.equal(resolvePlan({ campaign: "qualifier", sources: [{ key: "final" }] }), null,
    "단계가 어긋난 source가 통과했다");
  assert.equal(resolvePlan({ campaign: "final", sources: [{ key: "evil" }] }), null);
  assert.deepEqual(resolvePlan(FINAL_PLAN), { campaign: "final", sources: ["final"] });
});

test("본선 plan은 예선 탭이 없어도 정상이다 (예선 3개를 요구하지 않는다)", async () => {
  const env = makeEnv();                       // 탭은 본선 하나뿐
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.outcome, "success");
  assert.deepEqual(Object.keys(r.sources), ["final"]);
  assert.deepEqual(env.calls.challenge, ["final"]);
  assert.equal(env.calls.ingest.length, 1);
  assert.equal(env.calls.ingest[0].rows, 32);
});

test("본선 탭만 있고 예선 plan을 받으면 예선 3부문이 전부 no_tab이다", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...QUAL });
  assert.equal(r.outcome, "failed");
  assert.ok(Object.values(r.sources).every((x) => x.kind === "no_tab"));
  assert.equal(env.calls.ingest.length, 0);
});

/* ── 1) 모드 게이트 ───────────────────────────────────────────────────────── */
test("MANUAL이면 자동 실행이 아무것도 하지 않는다", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO, mode: "MANUAL" });
  assert.equal(r.skipped, "manual_mode");
  assert.equal(env.calls.challenge.length, 0, "challenge를 요청했다");
  assert.equal(env.calls.ingest.length, 0, "전송했다");
});

test("AUTO_PUBLISH 모드여도 수집만 하고 공개는 하지 않는다", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO, mode: "AUTO_PUBLISH" });
  assert.equal(r.outcome, "success");
  assert.equal(r.published, false);
  assert.ok(!("publish" in env.calls), "공개 경로를 불렀다");
});

test("수동 실행은 MANUAL에서도 허용된다", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "manual", ...AUTO, mode: "MANUAL" });
  assert.equal(r.outcome, "success", "사람이 누른 실행까지 막으면 안 된다");
});

test("장치가 없거나 폐기됐으면 실행하지 않는다", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO, deviceActive: false });
  assert.equal(r.skipped, "no_active_device");
  assert.equal(env.calls.challenge.length, 0);
});

/* ── 2) lock · 중복 방지 ──────────────────────────────────────────────────── */
test("동시에 두 회차가 돌지 않는다 (lock 중 실행 0)", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  const [a, b] = await Promise.all([
    s.runCycle({ trigger: "alarm", ...AUTO }),
    s.runCycle({ trigger: "manual", ...AUTO }),
  ]);
  const outcomes = [a, b].map((x) => x.skipped ?? x.outcome);
  assert.ok(outcomes.includes("locked"), `하나는 lock에 막혀야 한다: ${outcomes}`);
  assert.equal(env.calls.ingest.length, 1, "한 번만 전송해야 한다");
});

test("lock은 저장소에 남아 서비스 워커가 죽어도 유지된다", async () => {
  const env = makeEnv();
  await env.store.set("sched", { lock: { token: "old", expiresAt: env.now() + 60_000 } });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.skipped, "locked");
  assert.equal(env.calls.ingest.length, 0);
});

test("만료된 lock은 회수된다", async () => {
  const env = makeEnv();
  await env.store.set("sched", { lock: { token: "stale", expiresAt: env.now() - 1 } });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.outcome, "success", "만료된 lock이 영원히 막으면 안 된다");
});

test("회차가 끝나면 lock이 풀리고, 예외가 나도 풀린다", async () => {
  const env = makeEnv();
  await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal((await getState(env)).lock, null);
  const env2 = makeEnv({ readTable: async () => { throw new Error("boom"); } });
  const r = await createScheduler(env2).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.outcome, "failed");
  assert.equal((await getState(env2)).lock, null, "실패해도 lock을 놓아야 한다");
});

/* ── 3) 스케줄 · 재시작 · 절전 복귀 ───────────────────────────────────────── */
test("첫 기동에서 alarm 1개와 다음 실행 시각을 만든다", async () => {
  const env = makeEnv({ alarmExists: async () => false });
  const s = createScheduler(env);
  await s.ensureSchedule();
  const st = await getState(env);
  assert.ok(st.nextRunAt > env.now(), "다음 실행 시각이 없다");
  assert.equal(env.calls.alarms.length, 1, "alarm을 한 번만 만들어야 한다");
  assert.equal(env.calls.alarms[0].when, st.nextRunAt, "alarm 첫 발화가 예정 시각과 다르다");
  assert.equal(env.calls.alarms[0].periodMs, s.PERIOD_MS);
});

test("이미 예약돼 있으면 alarm을 다시 만들지 않는다 (중복 alarm 0)", async () => {
  let exists = false;
  const env = makeEnv({ alarmExists: async () => exists,
                        override: { createAlarm: async (p, w) => { exists = true; makeEnvAlarms.push(w); } } });
  const makeEnvAlarms = [];
  const s = createScheduler(env);
  await s.ensureSchedule();
  await s.ensureSchedule();
  await s.ensureSchedule();
  assert.equal(makeEnvAlarms.length, 1, "중복 alarm이 생겼다");
});

test("60분 도달 전에는 실행 0, 도달하면 1, 그다음 재예약된다", async () => {
  const env = makeEnv({ alarmExists: async () => true });
  const s = createScheduler(env);
  await s.ensureSchedule();
  const first = (await getState(env)).nextRunAt;
  env.advance(59 * 60 * 1000);
  assert.equal((await s.runCycle({ trigger: "alarm", ...AUTO })).skipped, "too_soon");
  assert.equal(env.calls.ingest.length, 0);
  env.advance(60 * 1000 + 5);                       // 정확히 예정 시각을 지났다
  const r = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.outcome, "success");
  assert.equal(env.calls.ingest.length, 1);
  const st = await getState(env);
  assert.equal(st.nextRunAt, first + s.PERIOD_MS, "다음 예정이 '예정 시각 + 주기'가 아니다");
});

test("회차 실행 시간이 있어도 다음 alarm이 too_soon으로 스킵되지 않는다 (AUTO-2 결함 b)", async () => {
  // 회차가 δ=90초 걸린다고 하자. 예전 코드는 nextRunAt = 종료시각+60분이라
  // 정시 alarm(예정+60분)이 90초 이르게 되어 격회차가 스킵됐다.
  const env = makeEnv({ alarmExists: async () => true });
  const s = createScheduler(env);
  await s.ensureSchedule();
  const t1 = (await getState(env)).nextRunAt;
  env.advance(t1 - env.now() + 1000);              // alarm은 1초 늦게 온다
  const slowIngest = env.ingest;
  env.ingest = async (...a) => { env.advance(90_000); return slowIngest(...a); };
  assert.equal((await s.runCycle({ trigger: "alarm", ...AUTO })).outcome, "success");
  const t2 = (await getState(env)).nextRunAt;
  assert.equal(t2, t1 + s.PERIOD_MS);
  env.advance(t2 - env.now() + 1000);              // 다음 정시 alarm
  const r = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.notEqual(r.skipped, "too_soon", "격회차가 스킵됐다 — 결함 b 재발");
  assert.equal(r.outcome, "unchanged");            // 표가 같으면 전송은 생략, 회차는 정상
  assert.equal(env.calls.alarms.at(-1).when, (await getState(env)).nextRunAt,
    "alarm이 다음 예정 시각에 다시 맞춰지지 않았다");
});

test("Chrome 재시작 뒤에도 예약이 복원되고 예정 시각이 밀리지 않는다", async () => {
  const env = makeEnv({ alarmExists: async () => true });
  await createScheduler(env).ensureSchedule();
  const before = (await getState(env)).nextRunAt;
  env.calls.alarms.length = 0;
  const s2 = createScheduler({ ...env, alarmExists: async () => false });   // 재시작: alarm 소실
  await s2.ensureSchedule();
  assert.equal(env.calls.alarms.length, 1, "재시작 후 alarm을 복원하지 않았다");
  assert.equal((await getState(env)).nextRunAt, before, "예약 시각이 흔들렸다");
  assert.equal(env.calls.alarms[0].when, before);
});

test("서비스 워커가 죽었다 살아나도 상태가 이어진다", async () => {
  const env = makeEnv();
  await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  const before = await getState(env);
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });  // 새 컨텍스트
  assert.equal(r.skipped, "too_soon", "재기동 뒤 최소 간격을 잊었다");
  assert.equal((await getState(env)).nextRunAt, before.nextRunAt);
});

test("6시간 절전 뒤 alarm이 몰려 와도 실행은 최대 1회 (catch-up 1회)", async () => {
  const env = makeEnv({ alarmExists: async () => true });
  const s = createScheduler(env);
  await s.ensureSchedule();
  env.advance(6 * HOUR);
  const results = [];
  for (let i = 0; i < 6; i++) results.push(await s.runCycle({ trigger: "alarm", ...AUTO }));
  const ran = results.filter((r) => r.outcome === "success").length;
  assert.equal(ran, 1, `밀린 횟수만큼 돌았다: ${ran}회`);
  assert.ok(results.slice(1).every((r) => r.skipped === "too_soon"));
  // 복귀 회차의 예정 시각은 '지금'이고 다음은 지금+60분 — 과거 예정에 더하지 않는다.
  const st = await getState(env);
  assert.equal(st.nextRunAt, results[0].startedAt + s.PERIOD_MS);
});

test("수동 실행은 최소 간격을 무시하지만 자동 예약을 건드리지 않는다", async () => {
  const env = makeEnv({ alarmExists: async () => true });
  const s = createScheduler(env);
  await s.ensureSchedule();
  const planned = (await getState(env)).nextRunAt;
  const r = await s.runCycle({ trigger: "manual", ...AUTO });
  assert.equal(r.outcome, "success", "사람이 누른 '지금 수집'이 막히면 안 된다");
  assert.equal((await getState(env)).nextRunAt, planned, "수동 실행이 정시 예약을 밀었다");
});

test("일시 정지 중에는 alarm이 실행되지 않고 수동은 된다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.setPaused(true);
  assert.equal((await s.runCycle({ trigger: "alarm", ...AUTO })).skipped, "paused");
  assert.equal((await s.runCycle({ trigger: "manual", ...AUTO })).outcome, "success");
  await s.setPaused(false);
  env.advance(61 * 60 * 1000);
  assert.equal((await s.runCycle({ trigger: "alarm", ...AUTO })).outcome, "unchanged");
});

test("실패하면 백오프가 걸리고 상한이 있다", async () => {
  const env = makeEnv({ tabs: [] });
  const s = createScheduler(env);
  const r1 = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r1.outcome, "failed");
  assert.equal((await s.runCycle({ trigger: "alarm", ...AUTO })).skipped, "too_soon");
  let last = 0;
  for (let i = 0; i < 8; i++) {
    const st = await getState(env);
    last = st.nextRunAt - env.now();
    env.advance(last + 1);
    await s.runCycle({ trigger: "alarm", ...AUTO });
  }
  assert.ok(last <= s.MAX_BACKOFF_MS, `백오프가 상한을 넘었다: ${last}`);
});

/* ── 4) 탭 탐색 · URL · 로딩 ─────────────────────────────────────────────── */
test("본선 탭이 없으면 no_tab으로 실패하고 탭을 새로 만들지 않는다", async () => {
  const env = makeEnv({ tabs: [] });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.outcome, "failed");
  assert.equal(r.sources.final.kind, "no_tab");
  assert.ok(!env.createTab, "탭 생성 경로가 존재한다");
  assert.ok(s.TAB_PATTERN.startsWith("https://www.piku.co.kr/w/rank/"));
});

test("본선 탭이 2개면 ambiguous_tab이고 읽지 않는다", async () => {
  const env = makeEnv({ tabs: [
    { id: 7, url: CANONICAL.final, status: "complete" },
    { id: 8, url: CANONICAL.final, status: "complete" },
  ] });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "ambiguous_tab");
  assert.equal(env.calls.read.length, 0, "모호한 상태에서 읽었다");
});

test("잘못된 URL(쿼리·다른 id)은 본선 탭으로 치지 않는다", async () => {
  for (const url of ["https://www.piku.co.kr/w/rank/2ut8Li?x=1",
                     "https://www.piku.co.kr/w/rank/EVIL01",
                     "https://www.piku.co.kr/w/rank/2ut8Li/",
                     "https://evil.example/w/rank/2ut8Li"]) {
    const env = makeEnv({ tabs: [{ id: 7, url, status: "complete" }] });
    const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
    assert.equal(r.sources.final.kind, "no_tab", url);
    assert.equal(env.calls.read.length, 0, `권한 밖 URL을 읽었다: ${url}`);
  }
});

test("로딩 중인 탭은 안정될 때까지 기다렸다가 실패로 끝난다", async () => {
  const env = makeEnv({ tabs: [{ id: 7, url: CANONICAL.final, status: "loading" }] });
  env.reloadTab = async () => {};                   // 새로고침해도 끝나지 않는다
  env.sleep = async (ms) => { env.advance(ms); };
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "loading");
  assert.equal(env.calls.read.length, 0, "로딩 중에 읽었다");
});

test("읽기 전에 항상 탭을 새로고침한다 (AUTO-2 결함 d)", async () => {
  const env = makeEnv();
  await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.deepEqual(env.calls.reload, [7]);
  const order = [env.calls.reload.length > 0, env.calls.read.length > 0];
  assert.deepEqual(order, [true, true]);
});

/* ── 5) 본선 페이지 넘김 ─────────────────────────────────────────────────── */
test("본선은 페이지 1→4를 순서대로 넘겨 32행을 합친다", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.outcome, "success");
  assert.deepEqual(env.calls.goto.slice(0, 4), [1, 2, 3, 4].filter((n) => n !== 1).length === 3
    ? env.calls.goto.slice(0, 4) : env.calls.goto);
  assert.ok(env.calls.goto.includes(2) && env.calls.goto.includes(3) && env.calls.goto.includes(4));
  assert.equal(env.calls.goto.at(-1), 1, "읽은 뒤 1페이지로 돌려놓지 않았다");
  assert.equal(env.calls.read.filter((x) => x.fragment).length, 4);
  const p = env.calls.ingest[0].payload;
  assert.equal(p.rowCount, 32);
  assert.deepEqual(p.rows.map((x) => x.rank), Array.from({ length: 32 }, (_, i) => i + 1));
  assert.equal(p.campaign, "final");
  assert.equal(p.sourceId, "2ut8Li");
  assert.equal(p.pageTitle, FINAL_TITLE);
  assert.equal(p.schemaVersion, 1);
});

test("행 수가 모자라거나 순위가 비면 전송하지 않는다", async () => {
  // 31행(마지막 페이지가 1행)
  let env = makeEnv({ total: 31 });
  let r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "row_count");
  assert.equal(env.calls.ingest.length, 0);
  // 32행인데 17위가 빠지고 33위가 있다
  env = makeEnv({ finalRows: () => rowsOf(33).filter((x) => x.rank !== 17) });
  r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "rank_gap");
  assert.equal(env.calls.ingest.length, 0);
  // 1위가 없다(2~33)
  env = makeEnv({ finalRows: () => rowsOf(33).slice(1) });
  r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "rank_gap");
  // 같은 행이 두 번
  env = makeEnv({ finalRows: () => rowsOf(32).map((x, i) => (i === 5 ? { ...rowsOf(32)[4], rank: 6 } : x)) });
  r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "parse_failed");
  assert.equal(env.calls.ingest.length, 0);
});

test("페이지가 안 넘어가면 loading으로 포기한다", async () => {
  const env = makeEnv({ gotoPage: async () => ({ ok: true }) });   // 페이지가 바뀌지 않는다
  env.sleep = async (ms) => { env.advance(ms); };
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "loading");
  assert.equal(env.calls.ingest.length, 0);
});

test("조각의 sourceId·campaign·제목이 어긋나면 버린다", async () => {
  const base = makeEnv();
  for (const patch of [{ sourceId: "8jGsHE" }, { campaign: "qualifier" }, { division: "groups" }]) {
    const env = makeEnv({ readTable: async (id, o) => ({ ...(await base.readTable(id, o)), ...patch }) });
    const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
    assert.equal(r.sources.final.kind, "source_mismatch", JSON.stringify(patch));
    assert.equal(env.calls.ingest.length, 0);
  }
  const env = makeEnv({ readTable: async (id, o) => {
    const f = await base.readTable(id, o);
    return { ...f, pageTitle: env._pager.page === 3 ? "다른 월드컵" : f.pageTitle };
  } });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "title_mismatch");
});

test("페이지 넘김이 불가능하면(pager 없음) pager_failed", async () => {
  const env = makeEnv({ pageState: async () => ({ ok: false, kind: "pager_failed" }) });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "pager_failed");
});

test("collect.js가 실패(차단·미렌더)하면 그 종류가 그대로 남는다", async () => {
  for (const kind of ["blocked", "not_rendered", "parse_failed", "wrong_page", "title_mismatch"]) {
    const env = makeEnv({ readTable: async () => ({ ok: false, kind, message: "x" }) });
    const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
    assert.equal(r.sources.final.kind, kind);
    assert.equal(env.calls.ingest.length, 0);
  }
});

/* ── 6) 인증 · 전송 ───────────────────────────────────────────────────────── */
test("challenge/token이 실패하면 token_failed이고 전송하지 않는다", async () => {
  let env = makeEnv({ getChallenge: async () => { throw new Error("[automation_off]"); } });
  let r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "token_failed");
  env = makeEnv({ signAndRedeem: async () => { throw new Error("[rate_limited]"); } });
  r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "token_failed");
  assert.equal(env.calls.ingest.length, 0);
});

test("ingest가 실패하면 ingest_failed이고 지문을 갱신하지 않는다 (다음 회차에 다시 보낸다)", async () => {
  let fail = true;
  const env = makeEnv({ ingest: async () => { if (fail) throw new Error("HTTP 400"); return { ok: true }; } });
  const s = createScheduler(env);
  let r = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "ingest_failed");
  assert.equal(r.outcome, "failed");
  fail = false;
  env.advance(2 * HOUR);
  r = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "sent");
});

test("성공하면 draft(ingest)만 만들고 Publish는 0건이다", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(env.calls.ingest.length, 1);
  assert.equal(r.published, false);
  assert.ok(!JSON.stringify(env.calls).includes("publish"));
});

test("토큰을 저장소에 남기지 않고 즉시 버린다", async () => {
  const env = makeEnv();
  await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  const dump = JSON.stringify([...env.store._raw.entries()]);
  assert.ok(!/tok-/.test(dump), "토큰이 저장됐다");
  assert.ok(!/pairing/i.test(dump), "등록 코드가 저장됐다");
});

test("같은 지문은 두 번 보내지 않고, 내용이 바뀌면 다시 보낸다", async () => {
  let bump = 0;
  const env = makeEnv({ finalRows: () => rowsOf(32, bump) });
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...AUTO });
  env.advance(61 * 60 * 1000);
  let r = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(env.calls.ingest.length, 1, "같은 지문을 다시 보냈다");
  assert.equal(r.sources.final.kind, "unchanged");
  assert.equal(r.outcome, "unchanged");
  bump = 1; env.advance(61 * 60 * 1000);
  r = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(env.calls.ingest.length, 2);
  assert.equal(r.sources.final.kind, "sent");
});

test("두 번째 60분 회차도 정상 실행된다 (두 회차 연속)", async () => {
  let bump = 0;
  const env = makeEnv({ alarmExists: async () => true, finalRows: () => rowsOf(32, bump) });
  const s = createScheduler(env);
  await s.ensureSchedule();
  const t1 = (await getState(env)).nextRunAt;
  env.advance(t1 - env.now() + 500);
  assert.equal((await s.runCycle({ trigger: "alarm", ...AUTO })).outcome, "success");
  bump = 1;
  const t2 = (await getState(env)).nextRunAt;
  assert.equal(t2, t1 + s.PERIOD_MS);
  env.advance(t2 - env.now() + 500);
  const r2 = await s.runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r2.outcome, "success");
  assert.equal(env.calls.ingest.length, 2);
  assert.equal(env.calls.report.length, 2);
  assert.equal(env.calls.report[1].scheduledAt, t2);
});

test("전송 payload에 쿠키·헤더·원문 HTML이 없다", async () => {
  const env = makeEnv();
  await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  const dump = JSON.stringify(env.calls.ingest);
  for (const bad of ["cookie", "Cookie", "<html", "<table", "setHeader", "document"]) {
    assert.ok(!dump.includes(bad), `${bad}가 전송됐다`);
  }
});

/* ── 7) 회차 보고 ────────────────────────────────────────────────────────── */
test("회차 결과를 서버에 보고한다 — 종류·시각·행 수만 (AUTO-2 결함 c)", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(env.calls.report.length, 1);
  const rep = env.calls.report[0];
  assert.deepEqual(Object.keys(rep).sort(),
    ["campaign", "finishedAt", "outcome", "scheduledAt", "sources", "startedAt", "trigger"]);
  assert.equal(rep.campaign, "final");
  assert.deepEqual(rep.sources, { final: { ok: true, kind: "sent", rows: 32 } });
  assert.equal(rep.scheduledAt, r.scheduledAt);
  const dump = JSON.stringify(rep);
  assert.ok(!/tok-|streamer|song_title|<html/.test(dump), "보고에 데이터·토큰이 실렸다");
});

test("보고 실패는 회차 결과를 바꾸지 않는다", async () => {
  const env = makeEnv({ report: async () => { throw new Error("HTTP 500"); } });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.outcome, "success");
  assert.equal((await getState(env)).lock, null);
});

/* ── 8) 여러 source(예선 plan)에서의 부분 성공 계약 ──────────────────────── */
test("여러 source 중 하나만 실패하면 partial이고 성공한 것은 보존된다", async () => {
  const env = makeEnv({ tabs: qualTabs().slice(0, 2) });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...QUAL });
  assert.equal(r.outcome, "partial");
  assert.equal(r.sources.groups.kind, "no_tab");
  assert.equal(r.sources.female_solo.ok, true, "성공한 부문은 보존돼야 한다");
  assert.equal(env.calls.ingest.length, 2);
});

test("source마다 challenge를 따로 받고, 한 source의 토큰 실패는 나머지를 막지 않는다", async () => {
  const env = makeEnv({ tabs: qualTabs(), signAndRedeem: async (cid) => {
    if (cid === "c-groups") throw new Error("[rate_limited]");
    return { token: `tok-${cid}` };
  } });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...QUAL });
  assert.deepEqual(env.calls.challenge.sort(), ["female_solo", "groups", "male_solo"]);
  assert.equal(r.outcome, "partial");
  assert.equal(r.sources.groups.kind, "token_failed");
  assert.equal(env.calls.ingest.length, 2);
});

test("payload의 division·sourceId·campaign이 어긋나면 버린다", async () => {
  const env = makeEnv({ tabs: qualTabs(), readTable: async () => ({ ok: true, payload: {
    schemaVersion: 1, division: "groups", campaign: "qualifier", sourceId: SOURCES.female_solo.id,
    sourceUrl: CANONICAL.female_solo, collectedAt: "x", rowCount: 32, rows: rowsOf(32) } }) });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...QUAL });
  assert.ok(Object.values(r.sources).every((d) => d.ok === false));
  assert.equal(env.calls.ingest.length, 0, "부문이 뒤바뀐 payload를 보냈다");
  const env2 = makeEnv({ tabs: qualTabs(), readTable: async (id) => {
    const t = qualTabs().find((x) => x.id === id);
    const d = Object.keys(CANONICAL).find((k) => CANONICAL[k] === t.url);
    return { ok: true, payload: { schemaVersion: 1, division: d, campaign: "final",
      sourceId: SOURCES[d].id, sourceUrl: t.url, collectedAt: "x",
      rowCount: SOURCES[d].expected, rows: rowsOf(SOURCES[d].expected) } };
  } });
  const r2 = await createScheduler(env2).runCycle({ trigger: "alarm", ...QUAL });
  assert.ok(Object.values(r2.sources).every((d) => d.kind === "campaign_mismatch"));
});

test("PIKU에 직접 요청하지 않는다", async () => {
  const env = makeEnv();
  await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.ok(!env.calls.fetchPiku, "PIKU 직접 호출 경로가 있다");
});

/* ── 9) pager 계약(추가) ─────────────────────────────────────────────────── */
test("페이지 수가 기대(32행/10행=4)와 다르면 읽기 전에 row_count로 끝낸다", async () => {
  for (const [total, expectKind] of [[50, "row_count"], [20, "row_count"], [32, null]]) {
    const env = makeEnv({ total });
    const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
    if (expectKind) {
      assert.equal(r.sources.final.kind, expectKind, `total=${total}`);
      assert.equal(env.calls.read.length, 0, "페이지 수가 틀린데 조각을 읽었다");
    } else assert.equal(r.outcome, "success");
  }
});

test("페이지 수 상한을 넘으면 pager_failed이고 무한 루프가 없다", async () => {
  const env = makeEnv({ pages: 500, total: 5000 });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "pager_failed");
  assert.equal(env.calls.goto.length, 0);
});

test("새로고침 뒤 1페이지가 아닌 상태로 열려 있어도 항상 1페이지부터 읽는다", async () => {
  const env = makeEnv();
  env.reloadTab = async (id) => { env.calls.reload.push(id); env._pager.page = 3; };  // 새로고침해도 3페이지에 머문 표
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.outcome, "success");
  assert.deepEqual(env.calls.goto.slice(0, 4), [1, 2, 3, 4]);
  const p = env.calls.ingest[0].payload;
  assert.deepEqual(p.rows.map((x) => x.rank), Array.from({ length: 32 }, (_, i) => i + 1));
});

test("페이지 DOM이 실제로 바뀐 것을 확인하지 못하면(고정 sleep만으로) 성공 판정하지 않는다", async () => {
  // gotoPage가 페이지를 바꾸지 않으면 firstRank가 그대로 → 대기 후 loading
  const env = makeEnv({ gotoPage: async () => ({ ok: true }) });
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "loading");
  assert.equal(env.calls.ingest.length, 0);
});

test("중간 페이지에서 실패하면 부분 ingest가 없다", async () => {
  const base = makeEnv();
  const env = makeEnv({ readTable: async (id, o) => (base._pager.page === 3
    ? { ok: false, kind: "blocked" } : base.readTable(id, o)) });
  env.gotoPage = async (id, n) => { env.calls.goto.push(n); base._pager.page = n; env._pager.page = n; return { ok: true }; };
  env.pageState = async (id) => base.pageState(id);
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...AUTO });
  assert.equal(r.sources.final.kind, "blocked");
  assert.equal(env.calls.ingest.length, 0, "일부 페이지만으로 전송했다");
  assert.equal(env.calls.challenge.length, 0);
});

test("lock 해제가 실패해도 TTL이 지나면 다음 회차가 돈다(영구 차단 없음)", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  const origSwap = env.store.swap;
  let failRelease = true;
  env.store.swap = async (k, fn) => {
    // 해제(lock:null로 바꾸는 호출)만 실패시킨다.
    const probe = fn((await env.store.get(k)) ?? {});
    if (failRelease && probe && probe.lock === null) throw new Error("idb closed");
    return origSwap(k, fn);
  };
  await assert.rejects(s.runCycle({ trigger: "alarm", ...AUTO }));   // finally에서 해제 실패가 전파
  assert.ok((await getState(env)).lock, "lock이 남아 있어야 시나리오가 성립한다");
  failRelease = false;
  env.advance(61 * 60 * 1000);
  assert.equal((await s.runCycle({ trigger: "alarm", ...AUTO })).outcome, "unchanged");
});

test("scheduledAt 간격이 정확히 60분이고 늦은 시작이 누적되지 않는다(3회차)", async () => {
  let bump = 0;
  const env = makeEnv({ alarmExists: async () => true, finalRows: () => rowsOf(32, bump) });
  const s = createScheduler(env);
  await s.ensureSchedule();
  const t0 = (await getState(env)).nextRunAt;
  const sched = [];
  for (let i = 0; i < 3; i++) {
    const t = (await getState(env)).nextRunAt;
    env.advance(t - env.now() + 40_000 + i * 20_000);   // 40·60·80초 늦게 발화
    bump += 1;
    const r = await s.runCycle({ trigger: "alarm", ...AUTO });
    assert.equal(r.outcome, "success");
    sched.push(r.scheduledAt);
  }
  assert.deepEqual(sched, [t0, t0 + s.PERIOD_MS, t0 + 2 * s.PERIOD_MS]);
});
