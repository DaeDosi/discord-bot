/* AUTO-1 — 이 확장을 **장치**로 만드는 부분.
 *
 * 예전에는 운영자가 10분짜리 1회용 토큰을 매번 붙여 넣었다. 그 방식은 안전하지만
 * 사람이 계속 붙어 있어야 해서 자동화가 안 된다. 그렇다고 장기 토큰을
 * `chrome.storage`에 넣어 두면 **평문이라 복제되고, 어느 PC에서 샜는지 알 수 없어
 * 개별 폐기도 불가능하다.**
 *
 * 그래서 이 파일이 하는 일은 하나다: 이 브라우저 안에 **밖으로 꺼낼 수 없는**
 * 개인키를 만들고, 그 키로 서버가 준 challenge에 서명해서 **기존 구조 그대로의**
 * 수집 토큰을 받아 온다. 토큰 구조를 우회하지 않는다.
 *
 * ── 무엇을 저장하고 무엇을 저장하지 않는가 ─────────────────────────────────
 *   IndexedDB        : CryptoKey 객체(개인키). `extractable:false`라 바이트를 꺼낼
 *                      수 없다. 실측으로 `exportKey`가 InvalidAccessError를 내고,
 *                      IndexedDB 왕복 뒤에도 `extractable:false`가 유지된다.
 *                      장치 id·지문·이름·서버 주소(전부 비밀 아님)도 **같은 곳**에 둔다.
 *   저장하지 않는 것 : 수집 토큰, pairing code, 서명 원문, 쿠키, PIKU 원문 HTML.
 *                      토큰은 받아서 그 요청 한 번에 쓰고 버린다.
 *
 * 메타데이터까지 IndexedDB에 두는 이유는 **권한을 늘리지 않기 위해서다.**
 * `chrome.storage`를 쓰면 manifest에 `storage` 권한이 필요하지만, IndexedDB는
 * 아무 권한도 요구하지 않는다. AUTO-1은 확장 권한을 **한 개도 더하지 않는다.**
 *
 * ── 잔여 위험(과장하지 않는다) ─────────────────────────────────────────────
 * 비추출형 키는 **바이트를 못 꺼낼 뿐**이다. 악성 코드가 같은 확장 컨텍스트를
 * 잡으면 그 키로 서명을 시킬 수 있다. 정확히는 "복제 없이 그 장치에서만 오용
 * 가능하고, 드러나면 그 장치만 즉시 폐기 가능"이다.
 */
"use strict";

const DB_NAME = "nexbot-piku-device";
const DB_STORE = "keys";
const KEY_ID = "device-key";
const META_ID = "device-meta";

/* 저장 위치를 IndexedDB로 고른 이유: `chrome.storage`는 **구조화 복제로 직렬화**해서
 * CryptoKey를 넣을 수 없다. IndexedDB는 CryptoKey를 객체 그대로 보관하므로
 * 개인키가 문자열이 되는 순간 자체가 없다. */
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
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, "readonly");
    const r = tx.objectStore(DB_STORE).get(key);
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

async function idbPut(key, value) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    tx.objectStore(DB_STORE).put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function idbDel(key) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(DB_STORE, "readwrite");
    tx.objectStore(DB_STORE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));

/** 장치 키를 만든다. **한 번만** 만들고, 이미 있으면 그대로 쓴다.
 *
 * `extractable=false`가 이 함수의 전부다. true로 바꾸면 개인키가 문자열로 나올 수
 * 있게 되고, 그 순간 이 설계의 근거가 사라진다.
 */
async function ensureKeyPair() {
  const existing = await idbGet(KEY_ID);
  if (existing && existing.privateKey && existing.publicKey) return existing;
  const kp = await crypto.subtle.generateKey(
    { name: "ECDSA", namedCurve: "P-256" },
    false,                               // ← 비추출형. 절대 true로 바꾸지 말 것.
    ["sign", "verify"],
  );
  await idbPut(KEY_ID, kp);
  return kp;
}

/** 서버에 등록할 공개키(SPKI, base64). 공개키는 비밀이 아니다. */
async function publicKeyB64() {
  const kp = await ensureKeyPair();
  return b64(await crypto.subtle.exportKey("spki", kp.publicKey));
}

/** challenge 서명. WebCrypto가 내는 P1363(r||s 64바이트)을 그대로 보낸다 —
 *  서버가 DER로 바꿔 검증한다. */
async function sign(message) {
  const kp = await ensureKeyPair();
  const sig = await crypto.subtle.sign(
    { name: "ECDSA", hash: "SHA-256" },
    kp.privateKey,
    new TextEncoder().encode(message),
  );
  return b64(sig);
}

/* ── 장치 메타(비밀 아님) ─────────────────────────────────────────────────── */
async function loadDevice() {
  const o = (await idbGet(META_ID)) || {};
  return {
    deviceId: o.deviceId ?? null,
    fingerprint: o.fingerprint ?? null,
    deviceName: o.deviceName ?? null,
    base: o.base ?? "https://nexbot.shop",
  };
}

async function saveDevice(patch) {
  const cur = (await idbGet(META_ID)) || {};
  await idbPut(META_ID, { ...cur, ...patch });
}

/** 장치 등록 해제(로컬). **서버 쪽 폐기는 Nexadmin에서 따로 해야 한다** —
 *  로컬에서 지운다고 서버가 그 키를 거부하게 되지는 않기 때문이다. 이 구분을
 *  화면에도 그대로 적는다. */
async function forgetDevice() {
  await idbDel(KEY_ID);
  await idbDel(META_ID);
}

/* ── 서버 호출 ────────────────────────────────────────────────────────────── */
/** 보낼 곳은 우리 관리 경로 하나뿐이다. 임의 주소로 새어 나가지 않게 고정한다. */
function apiUrl(base, path) {
  let u;
  try { u = new URL(base); } catch { throw new Error("NexBot 주소가 올바르지 않습니다."); }
  if (u.protocol !== "https:" && u.hostname !== "127.0.0.1" && u.hostname !== "localhost") {
    throw new Error("https 주소만 사용할 수 있습니다.");
  }
  return `${u.origin}/api/admin/piku/collector/${path}`;
}

async function postJson(base, path, body) {
  const resp = await fetch(apiUrl(base, path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",              // 쿠키를 보내지 않는다 — 우리 서버에도.
    body: JSON.stringify(body),
  });
  const j = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(j.detail || `요청 실패 (HTTP ${resp.status})`);
  return j;
}

/** 등록 — Nexadmin이 방금 발급한 pairing code를 소진하고 공개키를 묶는다. */
async function pair(base, pairingCode) {
  const publicKey = await publicKeyB64();
  const r = await postJson(base, "device/pair", { pairingCode, publicKey });
  await saveDevice({ deviceId: r.deviceId, fingerprint: r.fingerprint,
                     deviceName: r.name, base });
  return r;
}

/** 부문 하나에 쓸 수집 토큰을 받아 온다.
 *
 * 반환된 토큰은 **메모리에만** 두고 호출부가 곧바로 써서 버린다. 저장하지 않는다.
 */
async function fetchCollectorToken(base, division, { automation = false } = {}) {
  const { fingerprint } = await loadDevice();
  if (!fingerprint) throw new Error("이 브라우저는 아직 장치로 등록되지 않았습니다.");
  const c = await postJson(base, "device/challenge",
                           { fingerprint, division, automation });
  const signature = await sign(c.message);
  const t = await postJson(base, "device/token",
                           { challengeId: c.challengeId, signature });
  return t;             // { token, division, expiresAt, ttlSeconds, ... }
}
