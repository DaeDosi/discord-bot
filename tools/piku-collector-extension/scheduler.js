/* AUTO-2 — 1시간 자동 수집 스케줄러의 **로직**.
 *
 * 이 파일은 chrome API를 직접 부르지 않는다. 탭 조회·저장소·시각·네트워크를 전부
 * 주입받는다(`sw.js`가 이어 붙인다). 그래야 서비스 워커가 죽었다 살아나는 상황,
 * 절전에서 깨어나 alarm이 몰려 오는 상황, lock이 남아 있는 상황을 실제로 재현해
 * 볼 수 있다 — 그게 이 기능에서 가장 틀리기 쉬운 부분이다.
 *
 * ── 이 스케줄러가 지키는 것 ────────────────────────────────────────────────
 *  1. **MANUAL이면 자동 실행이 0이다.** 사람이 누른 실행만 돈다.
 *  2. **AUTO-2의 종착점은 draft다.** 공개(Publish)는 여기에 없다. AUTO_PUBLISH
 *     모드여도 수집만 하고 `published:false`를 돌려준다.
 *  3. **상태는 저장소에 있다.** 서비스 워커 메모리에 의존하면 워커가 죽는 순간
 *     lock과 다음 실행 시각이 사라져 회차가 겹친다.
 *  4. **정본 URL이 아니면 읽지 않는다.** AUTO-1 실측대로 host_permissions의 경로는
 *     접근을 제한하지 못하므로(오리진 단위) 여기서 코드가 직접 막는다.
 *  5. **부분 전송하지 않는다.** 행 수가 64/64/32가 아니면 그 부문을 버린다.
 *  6. **한 부문 실패가 나머지를 막지 않는다.** 다만 그 회차는 success가 아니다.
 */
"use strict";

/** 정본 소스. 이 셋 외에는 어떤 URL도 읽지 않는다. */
export const SOURCES = {
  female_solo: { id: "8jGsHE", expected: 64 },
  male_solo: { id: "7PqH44", expected: 64 },
  groups: { id: "7fXoNs", expected: 32 },
};

export const CANONICAL = Object.fromEntries(
  Object.entries(SOURCES).map(([d, s]) => [d, `https://www.piku.co.kr/w/rank/${s.id}`]),
);

const DIVISIONS = Object.keys(SOURCES);
const STATE_KEY = "sched";

const MINUTE = 60 * 1000;
/** 기본 주기. 절전에서 깨어나도 이 간격보다 자주 돌지 않는다. */
const PERIOD_MS = 60 * MINUTE;
/** lock 수명. 회차가 이보다 오래 걸리면 죽은 것으로 보고 회수한다. */
const LOCK_TTL_MS = 10 * MINUTE;
/** 연속 실패 백오프의 상한. 무한히 멀어지면 사실상 꺼진 것과 같다. */
const MAX_BACKOFF_MS = 6 * 60 * MINUTE;
/** 탭이 로딩 중일 때 기다려 볼 시간. 넘으면 그 회차는 그 부문을 포기한다. */
const SETTLE_TIMEOUT_MS = 20 * 1000;
const SETTLE_POLL_MS = 1000;

const nowSafe = (env) => (env.now ? env.now() : Date.now());

/** 전송할 내용의 지문. 같은 표를 두 번 보내지 않기 위한 것이다. */
function fingerprint(payload) {
  const head = `${payload.division}:${payload.sourceId}:${payload.rowCount}`;
  const body = payload.rows
    .map((r) => `${r.rank}|${r.streamer}|${r.song_title}|${r.artist}`)
    .join(",");
  // 짧은 비암호 해시로 충분하다 — 비밀이 아니라 "같은가"만 본다.
  let h = 0;
  const s = `${head};${body}`;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return `${head}#${(h >>> 0).toString(36)}`;
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

  /** 한 부문 수집. 실패는 `{ok:false, kind}`로 돌려주고 던지지 않는다 —
   *  한 부문 때문에 나머지가 멈추면 안 된다. */
  async function collectDivision(division, state) {
    const picked = await pickTab(division);
    if (picked.kind) return { ok: false, kind: picked.kind };

    let tab = picked.tab;
    if (tab.status !== "complete") {
      tab = await settle(division, tab);
      if (!tab) return { ok: false, kind: "loading" };
    }

    // 사용자가 켰을 때만 새로고침한다. 기본은 건드리지 않는다.
    if (state.reloadBeforeRead) {
      await env.reloadTab(tab.id);
      const after = await settle(division, { ...tab, status: "loading" });
      if (!after) return { ok: false, kind: "loading" };
      tab = after;
    }

    const res = await env.readTable(tab.id);
    if (!res || !res.ok) return { ok: false, kind: (res && res.kind) || "parse_failed" };
    const p = res.payload;
    if (!p) return { ok: false, kind: "parse_failed" };

    // 읽어 온 것이 **정말 그 부문인지** 다시 본다. 탭이 도중에 바뀌었을 수 있다.
    if (p.division !== division || p.sourceId !== SOURCES[division].id
        || p.sourceUrl !== CANONICAL[division]) {
      return { ok: false, kind: "source_mismatch" };
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
    if (env.calls) env.calls.alarms.push(nextRunAt);
    if (env.createAlarm) await env.createAlarm(PERIOD_MS);
    return write({ nextRunAt });
  }

  async function setPaused(paused) {
    return write({ paused: !!paused });
  }

  /** 한 회차. `trigger`는 `alarm`(자동) 또는 `manual`(사람). */
  async function runCycle({ trigger, mode, deviceActive }) {
    const manual = trigger === "manual";
    // 1) 모드 게이트 — 자동 실행은 MANUAL에서 돌지 않는다.
    if (!manual && mode === "MANUAL") return { skipped: "manual_mode" };
    if (!deviceActive) return { skipped: "no_active_device" };

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
    const divisions = {};
    let outcome = "failed";
    try {
      const state = await read();
      for (const d of DIVISIONS) {
        try {
          divisions[d] = await collectDivision(d, state);
        } catch {
          divisions[d] = { ok: false, kind: "aborted" };
        }
      }
      const okCount = DIVISIONS.filter((d) => divisions[d].ok).length;
      outcome = okCount === DIVISIONS.length ? "success"
        : okCount === 0 ? "failed" : "partial";

      // 지문은 성공한 부문만 갱신한다.
      const fps = { ...(state.lastFingerprint || {}) };
      for (const d of DIVISIONS) {
        if (divisions[d].ok && divisions[d].fingerprint) fps[d] = divisions[d].fingerprint;
      }
      // 다음 실행 시각: 성공이면 정상 주기, 실패면 제한된 백오프.
      const fails = outcome === "success" ? 0 : (state.consecutiveFailures || 0) + 1;
      const backoff = Math.min(MAX_BACKOFF_MS, PERIOD_MS * Math.pow(2, fails - 1));
      const wait = outcome === "success" ? PERIOD_MS : backoff;
      await write({
        lastFingerprint: fps,
        consecutiveFailures: fails,
        nextRunAt: nowSafe(env) + wait,
        lastRun: {
          startedAt, finishedAt: nowSafe(env), trigger, outcome,
          divisions: Object.fromEntries(DIVISIONS.map((d) => [d, {
            ok: !!divisions[d].ok, kind: divisions[d].kind || "",
            rows: divisions[d].rows || 0,
          }])),
        },
      });
      try { await env.report({ trigger, outcome, divisions, startedAt }); } catch { /* 보고 실패로 회차를 망치지 않는다 */ }
    } finally {
      // **어떤 경로로 빠져나가도 lock을 놓는다.** 안 그러면 다음 회차가 영영 막힌다.
      await releaseLock(token);
    }
    // AUTO-2에는 공개가 없다. 이 값은 호출부가 착각하지 않게 명시한다.
    return { outcome, divisions, published: false, startedAt };
  }

  return {
    runCycle, ensureSchedule, setPaused,
    PERIOD_MS, LOCK_TTL_MS, MAX_BACKOFF_MS,
    TAB_PATTERN: "https://www.piku.co.kr/w/rank/*",
    getState: read,
  };
}
