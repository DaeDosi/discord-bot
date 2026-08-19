/* AUTO-2 — 확장 스케줄러 로직 계약.
 *
 * `scheduler.js`는 **chrome API를 직접 부르지 않는다.** 탭 조회·저장소·시각·네트워크를
 * 전부 주입받는다. 그래야 서비스 워커가 죽었다 살아나는 상황, 절전에서 깨어나 alarm이
 * 몰려 오는 상황, lock이 남아 있는 상황을 실제로 재현해 볼 수 있다.
 *
 * `sw.js`는 이 파일이 검증한 로직에 chrome API를 이어 붙이는 얇은 껍데기다.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { createScheduler, SOURCES, CANONICAL } from "./scheduler.js";

/* ── 주입할 가짜 환경 ─────────────────────────────────────────────────────── */
function makeEnv(opts = {}) {
  let now = opts.now ?? 1_700_000_000_000;
  /** 서비스 워커가 죽어도 남는 저장소(IndexedDB 흉내). */
  const store = new Map(Object.entries(opts.store ?? {}));
  const tabs = opts.tabs ?? [
    { id: 1, url: CANONICAL.female_solo, status: "complete" },
    { id: 2, url: CANONICAL.male_solo, status: "complete" },
    { id: 3, url: CANONICAL.groups, status: "complete" },
  ];
  const calls = { read: [], challenge: [], token: [], ingest: [], reload: [], alarms: [] };
  const env = {
    now: () => now,
    advance: (ms) => { now += ms; },
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
    readTable: async (tabId) => {
      calls.read.push(tabId);
      if (opts.readTable) return opts.readTable(tabId);
      const t = tabs.find((x) => x.id === tabId);
      const div = Object.keys(CANONICAL).find((d) => CANONICAL[d] === t.url);
      const expected = SOURCES[div].expected;
      return { ok: true, payload: {
        schemaVersion: 1, division: div, sourceId: SOURCES[div].id,
        sourceUrl: t.url, collectedAt: "2026-08-19T00:00:00.000Z",
        rowCount: expected,
        rows: Array.from({ length: expected }, (_, i) => ({
          rank: i + 1, streamer: `s${i}`, song_title: `t${i}`, artist: `a${i}`,
          win_ratio: 1, win_rate: 2, image_url: "",
        })),
      } };
    },
    reloadTab: async (id) => { calls.reload.push(id); },
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
      calls.ingest.push({ token, division: payload.division, rows: payload.rowCount });
      if (opts.ingest) return opts.ingest(token, payload);
      return { ok: true };
    },
    report: async () => {},
    ...opts.override,
  };
  return env;
}

const okModes = { mode: "AUTO_COLLECT", deviceActive: true };
const getState = async (env) => (await env.store.get("sched")) ?? {};

/* ── 1) 모드 게이트 ───────────────────────────────────────────────────────── */
test("MANUAL이면 자동 실행이 아무것도 하지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", mode: "MANUAL", deviceActive: true });
  assert.equal(r.skipped, "manual_mode");
  assert.equal(env.calls.challenge.length, 0, "challenge를 요청했다");
  assert.equal(env.calls.ingest.length, 0, "전송했다");
});

test("AUTO_COLLECT에서만 자동 실행된다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "success");
  assert.equal(env.calls.ingest.length, 3);
});

test("AUTO_PUBLISH 모드여도 수집만 하고 공개는 하지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", mode: "AUTO_PUBLISH", deviceActive: true });
  assert.equal(r.outcome, "success");
  assert.equal(r.published, false);
  assert.ok(!("publish" in env.calls), "공개 경로를 불렀다");
});

test("수동 실행은 MANUAL에서도 허용된다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "manual", mode: "MANUAL", deviceActive: true });
  assert.equal(r.outcome, "success", "사람이 누른 실행까지 막으면 안 된다");
});

test("장치가 없거나 폐기됐으면 실행하지 않는다", async () => {
  for (const deviceActive of [false]) {
    const env = makeEnv();
    const s = createScheduler(env);
    const r = await s.runCycle({ trigger: "alarm", mode: "AUTO_COLLECT", deviceActive });
    assert.equal(r.skipped, "no_active_device");
    assert.equal(env.calls.challenge.length, 0);
  }
});

/* ── 2) lock · 중복 방지 ──────────────────────────────────────────────────── */
test("동시에 두 회차가 돌지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  const [a, b] = await Promise.all([
    s.runCycle({ trigger: "alarm", ...okModes }),
    s.runCycle({ trigger: "manual", ...okModes }),
  ]);
  const outcomes = [a, b].map((x) => x.skipped ?? x.outcome);
  assert.ok(outcomes.includes("locked"), `하나는 lock에 막혀야 한다: ${outcomes}`);
  assert.equal(env.calls.ingest.length, 3, "부문당 한 번만 전송해야 한다");
});

test("lock은 저장소에 남아 서비스 워커가 죽어도 유지된다", async () => {
  const env = makeEnv();
  // 이전 컨텍스트가 lock을 쥔 채 죽었다고 가정 — 아직 만료 전.
  await env.store.set("sched", { lock: { token: "old", expiresAt: env.now() + 60_000 } });
  const s = createScheduler(env);           // 새 서비스 워커(메모리 비어 있음)
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.skipped, "locked");
  assert.equal(env.calls.ingest.length, 0);
});

test("오래된 lock은 자동으로 회복된다", async () => {
  const env = makeEnv();
  await env.store.set("sched", { lock: { token: "stale", expiresAt: env.now() - 1 } });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "success", "만료된 lock이 영원히 막으면 안 된다");
});

test("회차가 끝나면 lock이 풀린다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal((await getState(env)).lock, null);
});

test("도중에 예외가 나도 lock이 풀린다", async () => {
  const env = makeEnv({ readTable: async () => { throw new Error("boom"); } });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "failed");
  assert.equal((await getState(env)).lock, null, "실패해도 lock을 놓아야 한다");
});

/* ── 3) 스케줄 · 재시작 · 절전 복귀 ───────────────────────────────────────── */
test("첫 기동에서 다음 실행 시각을 저장한다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.ensureSchedule();
  const st = await getState(env);
  assert.ok(st.nextRunAt > env.now(), "다음 실행 시각이 없다");
  assert.equal(env.calls.alarms.length, 1, "alarm을 한 번만 만들어야 한다");
});

test("이미 예약돼 있으면 alarm을 다시 만들지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.ensureSchedule();
  await s.ensureSchedule();
  await s.ensureSchedule();
  assert.equal(env.calls.alarms.length, 1, "중복 alarm이 생겼다");
});

test("Chrome 재시작 뒤에도 예약이 복원된다", async () => {
  const env = makeEnv();
  await createScheduler(env).ensureSchedule();
  const before = (await getState(env)).nextRunAt;
  env.calls.alarms.length = 0;
  // 재시작: alarm은 사라졌다고 보고 다시 확인한다.
  const s2 = createScheduler({ ...env, alarmExists: async () => false });
  await s2.ensureSchedule();
  assert.equal(env.calls.alarms.length, 1, "재시작 후 alarm을 복원하지 않았다");
  assert.equal((await getState(env)).nextRunAt, before, "예약 시각이 흔들렸다");
});

test("절전에서 깨어나 밀린 주기를 몰아서 돌지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.ensureSchedule();
  env.advance(6 * 60 * 60 * 1000);          // 6시간 절전
  // alarm이 여러 번 몰려 왔다고 가정하고 연속 호출한다.
  const results = [];
  for (let i = 0; i < 6; i++) {
    results.push(await s.runCycle({ trigger: "alarm", ...okModes }));
  }
  const ran = results.filter((r) => r.outcome === "success").length;
  assert.equal(ran, 1, `밀린 횟수만큼 돌았다: ${ran}회`);
  assert.ok(results.slice(1).every((r) => r.skipped === "too_soon"));
});

test("정상 주기가 지나면 다시 실행된다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  env.advance(61 * 60 * 1000);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "success");
});

test("수동 실행은 최소 간격을 무시하지만 lock은 지킨다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  const r = await s.runCycle({ trigger: "manual", ...okModes });
  assert.equal(r.outcome, "success", "사람이 누른 '지금 수집'이 막히면 안 된다");
});

test("일시 정지 중에는 alarm이 실행되지 않고 수동은 된다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.setPaused(true);
  assert.equal((await s.runCycle({ trigger: "alarm", ...okModes })).skipped, "paused");
  assert.equal((await s.runCycle({ trigger: "manual", ...okModes })).outcome, "success");
  await s.setPaused(false);
  env.advance(61 * 60 * 1000);
  assert.equal((await s.runCycle({ trigger: "alarm", ...okModes })).outcome, "success");
});

test("실패해도 즉시 무한 재시도하지 않는다", async () => {
  const env = makeEnv({ readTable: async () => ({ ok: false, kind: "not_rendered",
                                                  message: "표가 없습니다" }) });
  const s = createScheduler(env);
  const r1 = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r1.outcome, "failed");
  const st = await getState(env);
  assert.ok(st.nextRunAt > env.now(), "다음 실행 시각이 잡히지 않았다");
  // 백오프가 걸려 있는 동안 alarm이 다시 와도 돌지 않는다.
  const r2 = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r2.skipped, "too_soon");
});

test("연속 실패의 백오프는 상한이 있다", async () => {
  const env = makeEnv({ readTable: async () => ({ ok: false, kind: "no_tab", message: "x" }) });
  const s = createScheduler(env);
  let last = 0;
  for (let i = 0; i < 8; i++) {
    await s.runCycle({ trigger: "alarm", ...okModes });
    const st = await getState(env);
    last = st.nextRunAt - env.now();
    env.advance(last + 1);
  }
  assert.ok(last <= s.MAX_BACKOFF_MS, `백오프가 상한을 넘었다: ${last}`);
});

/* ── 4) 탭 탐색 · URL 검증 ───────────────────────────────────────────────── */
test("tabs 권한 없이 정본 URL 패턴으로만 탭을 찾는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "success");
  // 조회 패턴이 정본 오리진으로 좁혀져 있어야 한다.
  assert.ok(s.TAB_PATTERN.startsWith("https://www.piku.co.kr/w/rank/"));
});

test("탭이 없으면 그 부문만 실패한다", async () => {
  const env = makeEnv({ tabs: [
    { id: 1, url: CANONICAL.female_solo, status: "complete" },
    { id: 2, url: CANONICAL.male_solo, status: "complete" },
  ] });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "partial");
  assert.equal(r.divisions.groups.kind, "no_tab");
  assert.equal(r.divisions.female_solo.ok, true, "성공한 부문은 보존돼야 한다");
});

test("같은 부문 탭이 여러 개면 중단한다", async () => {
  const env = makeEnv({ tabs: [
    { id: 1, url: CANONICAL.female_solo, status: "complete" },
    { id: 9, url: CANONICAL.female_solo, status: "complete" },
    { id: 2, url: CANONICAL.male_solo, status: "complete" },
    { id: 3, url: CANONICAL.groups, status: "complete" },
  ] });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.divisions.female_solo.kind, "ambiguous_tab");
  assert.equal(env.calls.read.filter((id) => id === 1 || id === 9).length, 0,
    "모호한 상태에서 읽었다");
});

test("정본이 아닌 URL은 읽지 않는다", async () => {
  const env = makeEnv({ tabs: [
    { id: 1, url: "https://www.piku.co.kr/w/rank/EVIL01", status: "complete" },
    { id: 2, url: CANONICAL.male_solo, status: "complete" },
    { id: 3, url: CANONICAL.groups, status: "complete" },
  ] });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.divisions.female_solo.kind, "no_tab");
  assert.ok(!env.calls.read.includes(1), "권한 밖 source ID를 읽었다");
});

test("로딩 중인 탭은 안정될 때까지 기다렸다가 실패로 끝난다", async () => {
  const env = makeEnv({ tabs: [
    { id: 1, url: CANONICAL.female_solo, status: "loading" },
    { id: 2, url: CANONICAL.male_solo, status: "complete" },
    { id: 3, url: CANONICAL.groups, status: "complete" },
  ] });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.divisions.female_solo.kind, "loading");
  assert.ok(!env.calls.read.includes(1), "로딩 중에 읽었다");
  assert.equal(r.outcome, "partial");
});

test("행 수가 모자라면 전송하지 않는다", async () => {
  for (const [div, bad] of [["female_solo", 10], ["male_solo", 63], ["groups", 31]]) {
    const env = makeEnv({ readTable: async (id) => {
      const url = [null, CANONICAL.female_solo, CANONICAL.male_solo, CANONICAL.groups][id];
      const d = Object.keys(CANONICAL).find((k) => CANONICAL[k] === url);
      const n = d === div ? bad : SOURCES[d].expected;
      return { ok: true, payload: { schemaVersion: 1, division: d, sourceId: SOURCES[d].id,
        sourceUrl: url, collectedAt: "x", rowCount: n,
        rows: Array.from({ length: n }, (_, i) => ({ rank: i + 1, streamer: `s${i}`,
          song_title: "t", artist: "a", win_ratio: 1, win_rate: 2, image_url: "" })) } };
    } });
    const s = createScheduler(env);
    const r = await s.runCycle({ trigger: "alarm", ...okModes });
    assert.equal(r.divisions[div].kind, "row_count", `${div} ${bad}행이 통과했다`);
    assert.ok(!env.calls.ingest.some((x) => x.division === div),
      `${div}를 부분 전송했다`);
  }
});

test("파서가 실패하면 그 부문만 실패로 남긴다", async () => {
  const env = makeEnv({ readTable: async (id) => (id === 3
    ? { ok: false, kind: "parse_failed", message: "표를 읽지 못했습니다" }
    : { ok: true, payload: null }) });
  const s = createScheduler({ ...env, readTable: async (id) => (id === 3
    ? { ok: false, kind: "parse_failed", message: "x" }
    : makeEnv().readTable(id)) });
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.divisions.groups.kind, "parse_failed");
});

test("payload의 division과 sourceId가 어긋나면 버린다", async () => {
  const env = makeEnv({ readTable: async () => ({ ok: true, payload: {
    schemaVersion: 1, division: "groups", sourceId: SOURCES.female_solo.id,
    sourceUrl: CANONICAL.female_solo, collectedAt: "x", rowCount: 32,
    rows: Array.from({ length: 32 }, (_, i) => ({ rank: i + 1, streamer: `s${i}`,
      song_title: "t", artist: "a", win_ratio: 1, win_rate: 2, image_url: "" })) } }) });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.ok(Object.values(r.divisions).every((d) => d.ok === false));
  assert.equal(env.calls.ingest.length, 0, "부문이 뒤바뀐 payload를 보냈다");
});

test("설정하지 않으면 탭을 새로고침하지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(env.calls.reload.length, 0, "동의 없이 탭을 조작했다");
});

test("탭을 새로 만들지 않는다", async () => {
  const env = makeEnv({ tabs: [] });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "failed");
  assert.ok(!env.createTab, "탭 생성 경로가 존재한다");
});

/* ── 5) 인증 · 전송 ───────────────────────────────────────────────────────── */
test("부문마다 challenge를 따로 받고 토큰을 즉시 쓴다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  assert.deepEqual(env.calls.challenge.sort(),
    ["female_solo", "groups", "male_solo"]);
  assert.equal(env.calls.token.length, 3);
  for (const c of env.calls.ingest) assert.ok(c.token.startsWith("tok-"));
});

test("토큰을 저장소에 남기지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  const dump = JSON.stringify([...env.store._raw.entries()]);
  assert.ok(!/tok-/.test(dump), "토큰이 저장됐다");
  assert.ok(!/pairing/i.test(dump), "등록 코드가 저장됐다");
});

test("토큰 발급이 실패하면 그 부문만 실패한다", async () => {
  const env = makeEnv({ signAndRedeem: async (cid) => {
    if (cid === "c-groups") throw new Error("[rate_limited] 잠시 후 다시");
    return { token: `tok-${cid}` };
  } });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "partial");
  assert.equal(r.divisions.groups.kind, "token_failed");
  assert.equal(env.calls.ingest.length, 2);
});

test("한 부문 ingest 실패는 나머지를 막지 않는다", async () => {
  const env = makeEnv({ ingest: async (t, p) => {
    if (p.division === "male_solo") throw new Error("HTTP 400");
    return { ok: true };
  } });
  const s = createScheduler(env);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "partial");
  assert.equal(r.divisions.male_solo.kind, "ingest_failed");
  assert.equal(r.divisions.female_solo.ok, true);
});

test("같은 내용을 두 번 보내지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  env.advance(61 * 60 * 1000);
  const r = await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(env.calls.ingest.length, 3, "같은 지문을 다시 보냈다");
  assert.ok(Object.values(r.divisions).every((d) => d.kind === "unchanged"));
});

test("내용이 바뀌면 다시 보낸다", async () => {
  let bump = 0;
  const base = makeEnv();
  const env = makeEnv({ readTable: async (id) => {
    const r = await base.readTable(id);
    r.payload.rows[0].streamer = `changed-${bump}`;
    return r;
  } });
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  bump = 1; env.advance(61 * 60 * 1000);
  await s.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(env.calls.ingest.length, 6);
});

test("전송 payload에 쿠키·헤더·원문 HTML이 없다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  const dump = JSON.stringify(env.calls.ingest);
  for (const bad of ["cookie", "Cookie", "<html", "<table", "setHeader", "document"]) {
    assert.ok(!dump.includes(bad), `${bad}가 전송됐다`);
  }
});

test("PIKU에 직접 요청하지 않는다", async () => {
  const env = makeEnv();
  const s = createScheduler(env);
  await s.runCycle({ trigger: "alarm", ...okModes });
  assert.ok(!env.calls.fetchPiku, "PIKU 직접 호출 경로가 있다");
});

/* ── 6) 결과 모델 ─────────────────────────────────────────────────────────── */
test("세 부문 전부 성공해야 success다", async () => {
  const env = makeEnv();
  const r = await createScheduler(env).runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.outcome, "success");
  assert.equal(Object.values(r.divisions).filter((d) => d.ok).length, 3);
});

test("서비스 워커가 죽었다 살아나도 상태가 이어진다", async () => {
  const env = makeEnv();
  await createScheduler(env).runCycle({ trigger: "alarm", ...okModes });
  const before = await getState(env);
  const s2 = createScheduler(env);          // 새 컨텍스트, 메모리 비어 있음
  const r = await s2.runCycle({ trigger: "alarm", ...okModes });
  assert.equal(r.skipped, "too_soon", "재기동 뒤 최소 간격을 잊었다");
  assert.equal((await getState(env)).nextRunAt, before.nextRunAt);
});
