/* AUTO-2 → SINGCUP-FINAL-1 — 1시간 자동 수집 스케줄러의 **로직**.
 *
 * 이 파일은 chrome API를 직접 부르지 않는다. 탭 조회·저장소·시각·네트워크를 전부
 * 주입받는다(`sw.js`가 이어 붙인다). 그래야 서비스 워커가 죽었다 살아나는 상황,
 * 절전에서 깨어나 alarm이 몰려 오는 상황, lock이 남아 있는 상황을 실제로 재현해
 * 볼 수 있다 — 그게 이 기능에서 가장 틀리기 쉬운 부분이다.
 *
 * ── 이 스케줄러가 지키는 것 ────────────────────────────────────────────────
 *  1. **MANUAL이면 자동 실행이 0이다.** 사람이 누른 실행만 돈다.
 *  2. **종착점은 draft다.** 공개(Publish)는 여기에 없다. AUTO_PUBLISH 모드여도
 *     수집만 하고 `published:false`를 돌려준다.
 *  3. **상태는 저장소에 있다.** 서비스 워커 메모리에 의존하면 워커가 죽는 순간
 *     lock과 다음 실행 시각이 사라져 회차가 겹친다.
 *  4. **정본 URL이 아니면 읽지 않는다.** AUTO-1 실측대로 host_permissions의 경로는
 *     접근을 제한하지 못하므로(오리진 단위) 여기서 코드가 직접 막는다.
 *  5. **부분 전송하지 않는다.** 행 수가 기대치와 다르면 그 source를 버린다.
 *  6. **서버가 준 plan의 source만 찾는다.** 예선 탭이 없다는 이유로 본선 회차가
 *     실패하지 않는다(SINGCUP-FINAL-1). plan이 없으면 돌지 않는다.
 *
 * ── AUTO-2에서 "매시간 돌지 않았던" 원인 네 가지와 여기서의 처치 ─────────
 *  a. 예선 PIKU 페이지 셋이 삭제(404)돼 매 회차 실패 → 백오프가 6시간까지 늘었다.
 *     → plan 기반. 삭제된 예선은 plan에 없다.
 *  b. `nextRunAt = 종료시각 + 60분`인데 alarm은 생성 시각 기준 60분 주기라, 회차
 *     실행 시간만큼 어긋나 **격회차가 `too_soon`으로 스킵**됐다(사실상 2시간 주기).
 *     → 다음 실행 시각을 **예정 시각 + 주기**로 잡고, alarm을 그 시각에 다시 맞춘다.
 *  c. 회차 결과를 서버에 보고하지 않아 Nexadmin 이력이 영원히 비어 있었다.
 *     → `env.report`가 `device/run`으로 종류만 보고한다.
 *  d. 새로고침 없이 같은 DOM을 매시간 읽어 지문이 같아 `unchanged`만 반복됐다.
 *     → 읽기 전에 **항상** 탭을 새로고침한다.
 */
"use strict";

/** 정본 소스. 이 넷 외에는 어떤 URL도 읽지 않는다. 서버 레지스트리와 같은 값. */
export const SOURCES = {
  female_solo: { id: "8jGsHE", expected: 64, campaign: "qualifier", paged: false },
  male_solo: { id: "7PqH44", expected: 64, campaign: "qualifier", paged: false },
  groups: { id: "7fXoNs", expected: 32, campaign: "qualifier", paged: false },
  // 본선 — 한 표에 여성·남성·그룹 32행. '보기 개수'가 없어 페이지를 넘겨 읽는다.
  final: { id: "2ut8Li", expected: 32, campaign: "final", paged: true,
           title: "[2026 치지직 싱드컵 갤럭시] - 파이널 본선" },
};

export const CANONICAL = Object.fromEntries(
  Object.entries(SOURCES).map(([d, s]) => [d, `https://www.piku.co.kr/w/rank/${s.id}`]),
);

const STATE_KEY = "sched";

const MINUTE = 60 * 1000;
/** 기본 주기. 절전에서 깨어나도 이 간격보다 자주 돌지 않는다. */
const PERIOD_MS = 60 * MINUTE;
/** lock 수명. 회차가 이보다 오래 걸리면 죽은 것으로 보고 회수한다. */
const LOCK_TTL_MS = 10 * MINUTE;
/** 연속 실패 백오프의 상한. 무한히 멀어지면 사실상 꺼진 것과 같다. */
const MAX_BACKOFF_MS = 6 * 60 * MINUTE;
/** 탭이 로딩 중일 때 기다려 볼 시간. 넘으면 그 회차는 그 source를 포기한다. */
const SETTLE_TIMEOUT_MS = 20 * 1000;
const SETTLE_POLL_MS = 1000;
/** 페이지 넘김 뒤 표가 바뀌기를 기다리는 시간. */
const PAGE_TIMEOUT_MS = 10 * 1000;
const PAGE_POLL_MS = 250;
/** 페이지 넘김 상한. 본선은 4페이지(32행/10행)다 — 표가 이상하게 커져도 무한히 넘기지 않는다. */
const MAX_PAGES = 10;

const nowSafe = (env) => (env.now ? env.now() : Date.now());

/** 전송할 내용의 지문. 같은 표를 두 번 보내지 않기 위한 것이다. */
function fingerprint(payload) {
  const head = `${payload.campaign}:${payload.division}:${payload.sourceId}:${payload.rowCount}`;
  const body = payload.rows
    .map((r) => `${r.rank}|${r.streamer}|${r.song_title}|${r.artist}|${r.win_ratio}|${r.win_rate}`)
    .join(",");
  // 짧은 비암호 해시로 충분하다 — 비밀이 아니라 "같은가"만 본다.
  let h = 0;
  const s = `${head};${body}`;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return `${head}#${(h >>> 0).toString(36)}`;
}

/** 서버 plan을 이 확장이 아는 정본과 대조한다. 모르는 source·다른 id는 버린다. */
export function resolvePlan(plan) {
  if (!plan || typeof plan !== "object" || !Array.isArray(plan.sources)) return null;
  const campaign = String(plan.campaign || "");
  if (!campaign) return null;
  const sources = [];
  for (const p of plan.sources) {
    const key = p && p.key;
    const local = SOURCES[key];
    if (!local) continue;                                   // 모르는 source는 읽지 않는다
    if (local.campaign !== campaign) continue;              // 단계가 어긋나면 읽지 않는다
    if (p.sourceId && p.sourceId !== local.id) continue;    // 서버·확장 정본 불일치
    if (p.expected && p.expected !== local.expected) continue;
    sources.push(key);
  }
  return sources.length ? { campaign, sources } : null;
}

export function createScheduler(env) {
  const read = async () => (await env.store.get(STATE_KEY)) ?? {};
  const write = async (patch) => {
    const cur = await read();
    const next = { ...cur, ...patch };
    await env.store.set(STATE_KEY, next);
    return next;
  };

  /** lock 획득 — **저장소 안에서 원자적으로** 바꾼다.
   *
   *  읽고 나서 쓰면 두 컨텍스트가 같은 순간에 빈 lock을 보고 둘 다 들어간다.
   *  `swap`은 한 번의 트랜잭션 안에서 검사와 기록을 함께 한다.
   */
  async function acquireLock() {
    const token = `${nowSafe(env)}-${Math.random().toString(36).slice(2, 10)}`;
    const res = await env.store.swap(STATE_KEY, (cur) => {
      const st = cur ?? {};
      const lock = st.lock;
      if (lock && lock.expiresAt > nowSafe(env)) return undefined;   // 이미 잡혀 있다
      return { ...st, lock: { token, expiresAt: nowSafe(env) + LOCK_TTL_MS } };
    });
    return res.changed ? token : null;
  }

  async function releaseLock(token) {
    await env.store.swap(STATE_KEY, (cur) => {
      const st = cur ?? {};
      // 내 lock일 때만 놓는다 — 남이 회수해 간 lock을 지우면 안 된다.
      if (st.lock && st.lock.token !== token) return undefined;
      return { ...st, lock: null };
    });
  }

  /** 정본 URL에 해당하는 탭 하나를 고른다. 모호하면 고르지 않는다. */
  async function pickTab(division) {
    const want = CANONICAL[division];
    const tabs = await env.queryTabs(`https://www.piku.co.kr/w/rank/*`);
    // **정확히 일치**만 받는다. 쿼리스트링·해시가 붙은 것도 정본이 아니다.
    const exact = (tabs || []).filter((t) => t.url === want);
    if (exact.length === 0) return { kind: "no_tab" };
    if (exact.length > 1) return { kind: "ambiguous_tab" };
    return { tab: exact[0] };
  }

  /** 로딩이 끝날 때까지 제한된 시간만 기다린다. 넘으면 포기한다(부분 전송 금지). */
  async function settle(division, tab) {
    if (tab.status === "complete") return tab;
    const until = nowSafe(env) + SETTLE_TIMEOUT_MS;
    while (nowSafe(env) < until) {
      if (env.sleep) await env.sleep(SETTLE_POLL_MS); else break;
      const again = await pickTab(division);
      if (again.kind) return null;
      if (again.tab.status === "complete") return again.tab;
    }
    return null;
  }

  /** 페이지 넘김으로 읽는 source(본선). 사람이 페이지 번호를 누르듯 1→N을 돈다.
   *
   *  조각마다 행 단위 검사는 `collect.js`가 하고, **행 수·연속·중복은 여기서** 본다.
   *  하나라도 어긋나면 그 source는 통째로 버린다 — 부분 전송은 없다.
   */
  async function readPaged(division, tabId) {
    const meta = SOURCES[division];
    if (!env.pageState || !env.gotoPage) return { ok: false, kind: "pager_failed" };
    const first = await env.pageState(tabId);
    if (!first || !first.ok) return { ok: false, kind: "pager_failed" };
    const pages = Math.max(1, first.pages || 1);
    const perPage = first.rows || 0;
    if (!perPage) return { ok: false, kind: "not_rendered" };
    // 페이지 수 상한과 기대 행 수 대조 — 표 구조가 바뀌어 페이지가 늘거나 줄면 읽기 전에 끝낸다.
    if (pages > MAX_PAGES) return { ok: false, kind: "pager_failed" };
    const expectedPages = Math.ceil(meta.expected / perPage);
    if (first.current === 1 && pages !== expectedPages) {
      return { ok: false, kind: "row_count", rows: pages * perPage };
    }

    const frags = [];
    let head = null;
    let prevFirst = first.current === 1 ? first.firstRank : -1;
    for (let p = 1; p <= pages; p++) {
      if (p !== first.current || frags.length) {
        await env.gotoPage(tabId, p);
        // 표가 실제로 p페이지로 바뀔 때까지 기다린다 — 현재 페이지 표시와 **첫 순위가
        // 직전 페이지와 달라졌는지**로 본다. 기대 순위(예: 21)로 기다리면 순위가
        // 비어 있는 표에서 영원히 못 만나 `loading`으로 끝나고, 진짜 원인(`rank_gap`)이
        // 가려진다. 순위 검사는 조각을 합친 뒤 한다.
        const until = nowSafe(env) + PAGE_TIMEOUT_MS;
        let ready = false;
        while (nowSafe(env) < until) {
          if (env.sleep) await env.sleep(PAGE_POLL_MS);
          const st = await env.pageState(tabId);
          if (st && st.ok && st.current === p && st.rows > 0 && st.firstRank !== prevFirst) {
            ready = true; prevFirst = st.firstRank; break;
          }
          if (!env.sleep) break;
        }
        if (!ready) return { ok: false, kind: "loading" };
      } else {
        prevFirst = first.firstRank;
      }
      const frag = await env.readTable(tabId, { fragment: true });
      if (!frag || !frag.ok) return { ok: false, kind: (frag && frag.kind) || "parse_failed" };
      if (!frag.fragment || frag.division !== division || frag.sourceId !== meta.id
          || frag.campaign !== meta.campaign) {
        return { ok: false, kind: "source_mismatch" };
      }
      if (head && head.pageTitle !== frag.pageTitle) return { ok: false, kind: "title_mismatch" };
      head = head || frag;
      frags.push(...frag.rows);
    }
    // 읽기 전 상태(1페이지)로 돌려 둔다 — 사람이 보던 화면을 바꿔 두지 않는다.
    try { await env.gotoPage(tabId, 1); } catch { /* 되돌리기 실패는 결과와 무관 */ }

    const rows = frags.slice().sort((a, b) => a.rank - b.rank);
    if (rows.length !== meta.expected) return { ok: false, kind: "row_count", rows: rows.length };
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].rank !== i + 1) return { ok: false, kind: "rank_gap" };
    }
    const seen = new Set();
    for (const r of rows) {
      const key = `${r.streamer}\u0000${r.song_title}\u0000${r.artist}`;
      if (seen.has(key)) return { ok: false, kind: "parse_failed" };
      seen.add(key);
    }
    return {
      ok: true,
      payload: {
        schemaVersion: 1, division, campaign: meta.campaign, sourceId: meta.id,
        sourceUrl: CANONICAL[division], pageTitle: head.pageTitle,
        collectedAt: new Date(nowSafe(env)).toISOString(),
        rowCount: rows.length, rows,
      },
    };
  }

  /** 한 source 수집. 실패는 `{ok:false, kind}`로 돌려주고 던지지 않는다 —
   *  한 source 때문에 나머지가 멈추면 안 된다. */
  async function collectDivision(division, state, campaign) {
    const picked = await pickTab(division);
    if (picked.kind) return { ok: false, kind: picked.kind };

    let tab = picked.tab;
    if (tab.status !== "complete") {
      tab = await settle(division, tab);
      if (!tab) return { ok: false, kind: "loading" };
    }

    // **항상 새로고침한 뒤 읽는다.** PIKU 페이지는 스스로 갱신되지 않아, 새로고침
    // 없이는 매시간 같은 표를 읽고 지문이 같아 `unchanged`만 남는다(진단 d).
    await env.reloadTab(tab.id);
    const after = await settle(division, { ...tab, status: "loading" });
    if (!after) return { ok: false, kind: "loading" };
    tab = after;

    const res = SOURCES[division].paged
      ? await readPaged(division, tab.id)
      : await env.readTable(tab.id);
    if (!res || !res.ok) {
      return { ok: false, kind: (res && res.kind) || "parse_failed", rows: (res && res.rows) || 0 };
    }
    const p = res.payload;
    if (!p) return { ok: false, kind: "parse_failed" };

    // 읽어 온 것이 **정말 그 source인지** 다시 본다. 탭이 도중에 바뀌었을 수 있다.
    if (p.division !== division || p.sourceId !== SOURCES[division].id
        || p.sourceUrl !== CANONICAL[division]) {
      return { ok: false, kind: "source_mismatch" };
    }
    if (p.campaign !== SOURCES[division].campaign || p.campaign !== campaign) {
      return { ok: false, kind: "campaign_mismatch" };
    }
    if (SOURCES[division].title && !(p.pageTitle || "").includes(SOURCES[division].title)) {
      return { ok: false, kind: "title_mismatch" };
    }
    if (p.rowCount !== SOURCES[division].expected
        || !Array.isArray(p.rows) || p.rows.length !== SOURCES[division].expected) {
      return { ok: false, kind: "row_count", rows: p.rowCount };
    }

    // 같은 표를 두 번 보내지 않는다.
    const fp = fingerprint(p);
    if ((state.lastFingerprint || {})[division] === fp) {
      return { ok: true, kind: "unchanged", rows: p.rowCount, fingerprint: fp };
    }

    let token;
    try {
      const c = await env.getChallenge(division);
      const t = await env.signAndRedeem(c.challengeId, c.message);
      token = t.token;
    } catch {
      return { ok: false, kind: "token_failed" };
    }

    try {
      await env.ingest(token, p);
    } catch {
      return { ok: false, kind: "ingest_failed" };
    } finally {
      token = null;              // 토큰은 여기서 끝난다. 저장하지 않는다.
    }
    return { ok: true, kind: "sent", rows: p.rowCount, fingerprint: fp };
  }

  /** alarm이 없으면 만들고, 다음 실행 시각을 저장소에 남긴다.
   *  이미 예약돼 있으면 **아무것도 하지 않는다**(중복 alarm 방지). */
  async function ensureSchedule() {
    const st = await read();
    const scheduled = !!st.nextRunAt && st.nextRunAt > nowSafe(env);
    // alarm 존재를 물어볼 수 있으면 그 답을 믿는다(재시작 뒤에는 사라져 있다).
    // 물어볼 수 없으면 저장된 예약 시각을 근거로 삼는다 — 그래야 같은 컨텍스트에서
    // 여러 번 불러도 alarm을 다시 만들지 않는다.
    const alarmOk = env.alarmExists ? await env.alarmExists() : scheduled;
    if (alarmOk && scheduled) return st;
    const nextRunAt = st.nextRunAt && st.nextRunAt > nowSafe(env)
      ? st.nextRunAt : nowSafe(env) + PERIOD_MS;
    // alarm의 첫 발화를 **저장된 예정 시각에 정확히** 맞춘다 — 재시작 뒤 alarm을
    // "지금 + 60분"으로 새로 만들면 예정이 뒤로 밀리고 게이트와 어긋난다.
    if (env.createAlarm) await env.createAlarm(PERIOD_MS, nextRunAt);
    return write({ nextRunAt });
  }

  async function setPaused(paused) {
    return write({ paused: !!paused });
  }

  /** 한 회차. `trigger`는 `alarm`(자동) 또는 `manual`(사람). `plan`은 서버가 준 것. */
  async function runCycle({ trigger, mode, deviceActive, plan }) {
    const manual = trigger === "manual";
    // 1) 모드 게이트 — 자동 실행은 MANUAL에서 돌지 않는다.
    if (!manual && mode === "MANUAL") return { skipped: "manual_mode" };
    if (!deviceActive) return { skipped: "no_active_device" };
    const resolved = resolvePlan(plan);
    if (!resolved) return { skipped: "no_plan" };

    const st0 = await read();
    if (!manual && st0.paused) return { skipped: "paused" };
    // 2) 최소 간격 — 절전에서 깨어나 alarm이 몰려 와도 한 번만 돈다.
    if (!manual && st0.nextRunAt && nowSafe(env) < st0.nextRunAt) {
      return { skipped: "too_soon", nextRunAt: st0.nextRunAt };
    }

    // 3) lock — 자동과 수동, 여러 워커가 겹치지 않게.
    const token = await acquireLock();
    if (!token) return { skipped: "locked" };

    const startedAt = nowSafe(env);
    // 예정 시각: 저장된 nextRunAt이 한 주기 안에 있으면 그 값(정시 회차), 아니면
    // 지금(절전 복귀·수동). 다음 예정은 **예정 시각 + 주기**다 — 실행 시간이 누적돼
    // alarm과 어긋나던 진단 b의 처치다.
    const onTime = !!st0.nextRunAt && startedAt - st0.nextRunAt >= 0
      && startedAt - st0.nextRunAt < PERIOD_MS;
    const scheduledAt = manual ? startedAt : (onTime ? st0.nextRunAt : startedAt);
    const sources = {};
    let outcome = "failed";
    let nextRunAt = null;
    try {
      const state = await read();
      for (const d of resolved.sources) {
        try {
          sources[d] = await collectDivision(d, state, resolved.campaign);
        } catch {
          sources[d] = { ok: false, kind: "aborted" };
        }
      }
      const keys = resolved.sources;
      const okCount = keys.filter((d) => sources[d].ok).length;
      outcome = okCount === keys.length
        ? (keys.every((d) => sources[d].kind === "unchanged") ? "unchanged" : "success")
        : okCount === 0 ? "failed" : "partial";

      // 지문은 성공한 source만 갱신한다.
      const fps = { ...(state.lastFingerprint || {}) };
      for (const d of keys) {
        if (sources[d].ok && sources[d].fingerprint) fps[d] = sources[d].fingerprint;
      }
      // 다음 실행 시각: 성공이면 정상 주기, 실패면 제한된 백오프.
      const good = outcome === "success" || outcome === "unchanged";
      const fails = good ? 0 : (state.consecutiveFailures || 0) + 1;
      const backoff = Math.min(MAX_BACKOFF_MS, PERIOD_MS * Math.pow(2, fails - 1));
      const wait = good ? PERIOD_MS : backoff;
      // 수동 실행은 자동 예약을 건드리지 않는다 — 사람이 눌렀다고 정시 회차가 밀리면 안 된다.
      nextRunAt = manual && st0.nextRunAt && st0.nextRunAt > nowSafe(env)
        ? st0.nextRunAt : scheduledAt + wait;
      const finishedAt = nowSafe(env);
      await write({
        lastFingerprint: fps,
        consecutiveFailures: fails,
        nextRunAt,
        lastRun: {
          scheduledAt, startedAt, finishedAt, trigger, outcome,
          campaign: resolved.campaign,
          sources: Object.fromEntries(keys.map((d) => [d, {
            ok: !!sources[d].ok, kind: sources[d].kind || "",
            rows: sources[d].rows || 0,
          }])),
        },
        ...(good ? { lastSuccessAt: finishedAt } : { lastFailureAt: finishedAt }),
      });
      // alarm을 다음 예정 시각에 다시 맞춘다(같은 이름으로 만들면 기존 것이 대체된다).
      if (env.createAlarm) {
        try { await env.createAlarm(PERIOD_MS, nextRunAt); } catch { /* 예약 실패는 다음 ensure가 복구 */ }
      }
      try {
        await env.report({
          trigger, campaign: resolved.campaign, outcome,
          scheduledAt, startedAt, finishedAt,
          sources: Object.fromEntries(keys.map((d) => [d, {
            ok: !!sources[d].ok, kind: sources[d].kind || "", rows: sources[d].rows || 0,
          }])),
        });
      } catch { /* 보고 실패로 회차를 망치지 않는다 */ }
    } finally {
      // **어떤 경로로 빠져나가도 lock을 놓는다.** 안 그러면 다음 회차가 영영 막힌다.
      await releaseLock(token);
    }
    // 공개는 여기에 없다. 이 값은 호출부가 착각하지 않게 명시한다.
    return { outcome, campaign: resolved.campaign, sources, divisions: sources,
             published: false, scheduledAt, startedAt, nextRunAt };
  }

  return {
    runCycle, ensureSchedule, setPaused,
    PERIOD_MS, LOCK_TTL_MS, MAX_BACKOFF_MS,
    TAB_PATTERN: "https://www.piku.co.kr/w/rank/*",
    getState: read,
  };
}
