/* AUTO-2 — 서비스 워커. **로직은 `scheduler.js`에 있고 여기는 chrome API 접착부다.**
 *
 * 이 파일이 얇아야 하는 이유: 서비스 워커는 아무 때나 죽고 아무 때나 되살아난다.
 * 그 위에서 lock·다음 실행 시각·중복 방지를 검증하려면 chrome API를 흉내 내야 하는데
 * 그건 실제 동작의 증거가 못 된다. 그래서 판단은 전부 주입 가능한 로직으로 빼고,
 * 여기서는 **저장소·탭·시각·네트워크를 이어 붙이기만** 한다.
 *
 * 저장은 IndexedDB다(`device.js`와 같은 DB). `chrome.storage`를 쓰지 않는 이유는
 * AUTO-1과 같다 — 권한을 늘리지 않기 위해서다. AUTO-2가 새로 요구하는 권한은
 * `alarms` 하나뿐이다.
 */
"use strict";

import { createScheduler, CANONICAL } from "./scheduler.js";

const ALARM = "piku-auto-collect";
const DB_NAME = "nexbot-piku-device";
const DB_STORE = "keys";
const SCHED_KEY = "sched";

/* ── IndexedDB (device.js와 같은 저장소) ─────────────────────────────────── */
function openDb() {
  return new Promise((resolve, reject) => {
    const q = indexedDB.open(DB_NAME, 1);
    q.onupgradeneeded = () => q.result.createObjectStore(DB_STORE);
    q.onsuccess = () => resolve(q.result);
    q.onerror = () => reject(q.error);
  });
}

async function idbGet(key) {
  const db = await openDb();
  return new Promise((res, rej) => {
    const tx = db.transaction(DB_STORE, "readonly");
    const r = tx.objectStore(DB_STORE).get(key);
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}

async function idbPut(key, value) {
  const db = await openDb();
  return new Promise((res, rej) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    tx.objectStore(DB_STORE).put(value, key);
    tx.oncomplete = res;
    tx.onerror = () => rej(tx.error);
  });
}

/** 검사와 기록을 **한 트랜잭션 안에서** 한다.
 *
 *  lock이 이것 하나에 달려 있다. 읽고 나서 쓰면 두 컨텍스트가 같은 순간에 빈 lock을
 *  보고 둘 다 들어간다 — 서비스 워커가 두 번 깨어나는 상황이 실제로 있다.
 */
async function idbSwap(key, fn) {
  const db = await openDb();
  return new Promise((res, rej) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    const store = tx.objectStore(DB_STORE);
    const g = store.get(key);
    let changed = false;
    let value;
    g.onsuccess = () => {
      const next = fn(g.result);
      value = next === undefined ? g.result : next;
      if (next !== undefined) { store.put(next, key); changed = true; }
    };
    tx.oncomplete = () => res({ changed, value });
    tx.onerror = () => rej(tx.error);
  });
}

/* ── 장치 자격 증명 (AUTO-1 계약 그대로) ─────────────────────────────────── */
const META_ID = "device-meta";
const KEY_ID = "device-key";

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));

function apiUrl(base, path) {
  const u = new URL(base);
  if (u.protocol !== "https:" && u.hostname !== "127.0.0.1" && u.hostname !== "localhost") {
    throw new Error("https 주소만 사용할 수 있습니다.");
  }
  return `${u.origin}/api/admin/piku/collector/${path}`;
}

async function postJson(base, path, body, headers = {}) {
  const resp = await fetch(apiUrl(base, path), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    credentials: "omit",              // 쿠키를 보내지 않는다.
    body: JSON.stringify(body),
  });
  const j = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(j.detail || `요청 실패 (HTTP ${resp.status})`);
  return j;
}

async function signMessage(message) {
  const kp = await idbGet(KEY_ID);
  if (!kp || !kp.privateKey) throw new Error("장치 키가 없습니다.");
  const sig = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" }, kp.privateKey,
    new TextEncoder().encode(message));
  return b64(sig);
}

/* ── 스케줄러에 주입할 환경 ──────────────────────────────────────────────── */
function makeEnv(meta) {
  const base = meta.base || "https://nexbot.shop";
  return {
    now: () => Date.now(),
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    store: {
      get: (k) => idbGet(k),
      set: (k, v) => idbPut(k, v),
      swap: (k, fn) => idbSwap(k, fn),
    },
    // **`tabs` 권한 없이** host_permissions만으로 동작한다(AUTO-1 실측).
    queryTabs: (pattern) => chrome.tabs.query({ url: pattern }),
    reloadTab: (id) => chrome.tabs.reload(id),
    readTable: async (tabId) => {
      const [res] = await chrome.scripting.executeScript({
        target: { tabId }, files: ["collect.js"],
      });
      return (res && res.result) || { ok: false, kind: "parse_failed" };
    },
    getChallenge: (division) => postJson(base, "device/challenge", {
      fingerprint: meta.fingerprint, division, automation: true,
    }),
    signAndRedeem: async (challengeId, message) => {
      const signature = await signMessage(message);
      return postJson(base, "device/token", { challengeId, signature });
    },
    ingest: (token, payload) =>
      postJson(base, "ingest", payload, { "X-Collector-Token": token }),
    report: async () => { /* AUTO-2에서는 회차 보고를 서버로 보내지 않는다 */ },
    alarmExists: async () => !!(await chrome.alarms.get(ALARM)),
    createAlarm: async (periodMs) => {
      await chrome.alarms.create(ALARM, { periodInMinutes: periodMs / 60000 });
    },
  };
}

/** 실행에 필요한 것을 모은다. 하나라도 없으면 **돌지 않는다.** */
async function context() {
  const meta = (await idbGet(META_ID)) || {};
  if (!meta.fingerprint) return { deviceActive: false, meta };
  const base = meta.base || "https://nexbot.shop";
  // 모드와 장치 상태는 **서버가** 정한다. 확장이 기억한 값을 믿지 않는다 —
  // 운영자가 Nexadmin에서 MANUAL로 되돌렸거나 장치를 폐기했을 수 있다.
  //
  // 전용 상태 경로를 쓴다. 예전에는 challenge를 하나 발급해 보고 상태를 짐작했는데,
  // 그러면 시간당 발급이 3회가 아니라 4회가 되고 속도 제한 계산이 흐려진다.
  try {
    const st = await postJson(base, "device/state", { fingerprint: meta.fingerprint });
    return { meta, mode: st.mode || "MANUAL", deviceActive: !!st.deviceActive };
  } catch {
    // 서버에 닿지 못하면 **돌지 않는다.** 모르는 상태에서 자동 실행하지 않는다.
    return { meta, mode: "MANUAL", deviceActive: false };
  }
}

async function runOnce(trigger) {
  const { meta, mode, deviceActive } = await context();
  if (!meta.fingerprint) return { skipped: "no_active_device" };
  const s = createScheduler(makeEnv(meta));
  return s.runCycle({ trigger, mode, deviceActive });
}

/* ── chrome 이벤트 ───────────────────────────────────────────────────────── */
async function ensure() {
  const meta = (await idbGet(META_ID)) || {};
  const s = createScheduler(makeEnv(meta));
  await s.ensureSchedule();
}

// 설치·브라우저 재시작 모두에서 예약을 되살린다. `ensureSchedule`이 중복을 막는다.
chrome.runtime.onInstalled.addListener(() => { void ensure(); });
chrome.runtime.onStartup.addListener(() => { void ensure(); });

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name !== ALARM) return;
  void runOnce("alarm");
});

// 팝업이 부르는 것들. 서비스 워커가 잠들어 있어도 이 메시지가 깨운다.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    try {
      if (msg?.type === "run-now") reply(await runOnce("manual"));
      else if (msg?.type === "state") {
        const meta = (await idbGet(META_ID)) || {};
        const s = createScheduler(makeEnv(meta));
        await s.ensureSchedule();
        reply({ ok: true, state: await s.getState(), canonical: CANONICAL });
      } else if (msg?.type === "pause") {
        const meta = (await idbGet(META_ID)) || {};
        await createScheduler(makeEnv(meta)).setPaused(!!msg.paused);
        reply({ ok: true });
      } else reply({ ok: false, error: "unknown" });
    } catch (e) {
      reply({ ok: false, error: String(e.message || e) });
    }
  })();
  return true;                         // 비동기 응답을 쓰겠다는 신호
});
