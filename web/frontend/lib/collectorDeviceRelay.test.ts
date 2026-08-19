/* 장치 인증 4경로의 Vercel relay 계약.
 *
 * 실측 결함(2026-08-19): 확장에서 등록 코드를 넣고 '이 브라우저를 장치로 등록'을
 * 누르면 **요청 실패 (HTTP 404)**가 떴다. 확장은
 * `https://nexbot.shop/api/admin/piku/collector/device/pair`로 보내는데 그 주소는
 * Vercel 프론트이고, 프론트에는 `ingest`·`failure` relay **둘뿐**이었다. 요청이
 * Railway 백엔드에 닿기도 전에 Next가 404를 냈다(백엔드에는 경로가 있었다).
 *
 * 그래서 `device/pair`·`device/state`·`device/challenge`·`device/token` **넷만** 더
 * 뚫는다. 이 파일이 고정하는 것은 "뚫렸다"가 아니라 **얼마나 좁게 뚫렸는가**다.
 *
 * ingest·failure와 결정적으로 다른 점: 이 넷은 **성공 응답의 본문이 필요하다**.
 * `fingerprint`가 없으면 장치가 저장되지 않고 `challengeId`·`message`가 없으면
 * 서명할 것이 없다. 그래서 기존 relay의 `safeDetail`처럼 성공을 `detail` 한 줄로
 * 접으면 안 되고, 대신 **경로별 필드 허용 목록**으로 막는다.
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { test } from "node:test";

import {
  DEVICE_MAX_BODY_BYTES, DEVICE_RELAY_KINDS, MAX_BODY_BYTES, RELAY_KINDS,
  relayCollector, type RelayKind,
} from "./collectorRelay.ts";

const BASE = "https://backend.example.com";
const EXT = new URL("../../../tools/piku-collector-extension/", import.meta.url);
const ROUTES = new URL("../app/api/admin/piku/collector/", import.meta.url);

/** 백엔드가 실제로 돌려주는 성공 응답 형태(운영 확인본). 여분 필드가 섞여 있다.
 *  값은 전부 가짜다 — 운영 등록 코드·지문·토큰을 여기에 적지 않는다. */
const UPSTREAM: Record<string, Record<string, unknown>> = {
  "device/pair": { ok: true, deviceId: 7, name: "작업용 크롬",
                   status: "active", fingerprint: "AB12-CD34-EF56" },
  "device/state": { ok: true, deviceActive: true, deviceStatus: "active",
                    mode: "AUTO_COLLECT", autoPublishReady: false, periodMinutes: 60 },
  "device/challenge": { ok: true, challengeId: "ch_abc123", nonce: "n0nc3-s3cr3t",
                        division: "male_solo", deviceId: 7, expiresAt: 1755600000,
                        message: "nexbot-piku-device:ch_abc123:n0nc3-s3cr3t:male_solo:7" },
  "device/token": { ok: true, deviceId: 7, fingerprint: "AB12-CD34-EF56",
                    token: "tok_fake_for_tests", division: "male_solo",
                    expiresAt: 1755600600, ttlSeconds: 600 },
};

type Call = { url: string; init: RequestInit };

function fakeFetch(reply: { status?: number; body?: string; contentType?: string } = {}) {
  const calls: Call[] = [];
  const impl = (async (url: any, init: any) => {
    calls.push({ url: String(url), init });
    return new Response(reply.body ?? JSON.stringify({ ok: true }), {
      status: reply.status ?? 200,
      headers: { "Content-Type": reply.contentType ?? "application/json" },
    });
  }) as unknown as typeof fetch;
  return { impl, calls };
}

/** 해당 경로의 운영 성공 응답을 그대로 돌려주는 백엔드. */
function upstreamFor(kind: string, extra: Record<string, unknown> = {}) {
  return fakeFetch({ body: JSON.stringify({ ...UPSTREAM[kind], ...extra }) });
}

function req(body: unknown, headers: Record<string, string> = {}, method = "POST") {
  return new Request("https://frontend.example.com/api/admin/piku/collector/device/pair", {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    ...(method === "GET" || method === "HEAD"
      ? {} : { body: typeof body === "string" ? body : JSON.stringify(body) }),
  });
}

const relay = (kind: RelayKind, f: { impl: typeof fetch }, body: unknown = {},
               headers: Record<string, string> = {}) =>
  relayCollector(req(body, headers), kind, { apiBase: BASE, fetchImpl: f.impl });

// ── 1. 네 경로가 프론트에 실재한다 (404의 직접 원인) ────────────────────────
test("장치 4경로가 프론트 route로 존재하고 POST만 내보낸다", () => {
  assert.deepEqual([...DEVICE_RELAY_KINDS].sort(),
    ["device/challenge", "device/pair", "device/state", "device/token"]);
  for (const kind of DEVICE_RELAY_KINDS) {
    const src = readFileSync(new URL(`${kind}/route.ts`, ROUTES), "utf8");
    assert.ok(/export async function POST/.test(src), `${kind}에 POST가 없다`);
    assert.ok(src.includes(`"${kind}"`), `${kind} route가 자기 종류를 넘기지 않는다`);
    for (const m of ["GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
      assert.ok(!new RegExp(`export (async )?function ${m}\\b`).test(src),
        `${kind}가 ${m}을 내보낸다`);
    }
  }
});

// ── 2. 확장이 부르는 **모든** 경로가 프론트에 있다 (전수 계약) ──────────────
test("확장이 부르는 collector 경로가 전부 프론트 route로 존재한다", () => {
  const wanted = new Set<string>();
  for (const file of ["device.js", "sw.js", "popup.js", "collect.js", "scheduler.js"]) {
    let src: string;
    try { src = readFileSync(new URL(file, EXT), "utf8"); } catch { continue; }
    for (const m of src.matchAll(
      /\b(?:postJson|ingestUrl|apiUrl)\(\s*base\s*,\s*"([^"]+)"/g)) wanted.add(m[1]);
  }
  // 확장이 실제로 부르는 것은 여섯이다. 하나라도 못 찾으면 이 검사 자체가 무의미해진다.
  assert.deepEqual([...wanted].sort(),
    ["device/challenge", "device/pair", "device/state", "device/token",
     "failure", "ingest"],
    "확장 호출 경로 목록이 예상과 다르다 — 정규식이나 확장이 바뀌었다");
  for (const p of wanted) {
    assert.ok(RELAY_KINDS.includes(p as RelayKind), `relay가 ${p}를 모른다`);
    statSync(new URL(`${p}/route.ts`, ROUTES));   // 없으면 던진다 = 운영 404
  }
});

// ── 3. 각 경로가 **정확히 자기 백엔드 경로로만** 나간다 ─────────────────────
test("네 경로가 각각 정확한 백엔드 경로로 전달된다", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    const f = upstreamFor(kind);
    const res = await relay(kind, f);
    assert.equal(res.status, 200, kind);
    assert.equal(f.calls.length, 1, kind);
    assert.equal(f.calls[0].url, `${BASE}/api/admin/piku/collector/${kind}`);
    assert.equal(f.calls[0].init.method, "POST");
    assert.equal((f.calls[0].init as any).credentials, "omit");
    assert.equal((f.calls[0].init as any).cache, "no-store");
    assert.equal((f.calls[0].init as any).redirect, "manual");
  }
});

test("본문을 손대지 않고 그대로 넘긴다 — 검증 권위는 백엔드다", async () => {
  const f = upstreamFor("device/pair");
  const body = '{"pairingCode":"FAKE-CODE-0000","publicKey":"BFAKEKEY=","x":null}';
  await relayCollector(req(body), "device/pair", { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(f.calls[0].init.body, body);
});

// ── 4. 임의 경로·traversal은 통하지 않는다 ─────────────────────────────────
test("허용 목록에 없는 종류는 백엔드를 부르지 않고 404다", async () => {
  const evil = ["device/../../publish", "publish", "device", "device/",
                "../mode", "device/pair/../../import", "device%2Fpair",
                "https://evil.example/x", "device/token?x=1", "",
                "DEVICE/PAIR", "device/pair/", "toString", "__proto__", "constructor"];
  for (const kind of evil) {
    const f = fakeFetch();
    const res = await relayCollector(req({}), kind as RelayKind,
      { apiBase: BASE, fetchImpl: f.impl });
    assert.equal(f.calls.length, 0, `'${kind}'가 백엔드로 나갔다`);
    assert.equal(res.status, 404, `'${kind}'가 404가 아니다`);
  }
});

test("요청 URL 경로를 백엔드 주소에 이어 붙이지 않는다", async () => {
  const f = upstreamFor("device/pair");
  const r = new Request(
    "https://frontend.example.com/api/admin/piku/collector/device/pair/../../publish",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  await relayCollector(r, "device/pair", { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(f.calls[0].url, `${BASE}/api/admin/piku/collector/device/pair`);
});

test("device 디렉터리에 동적·catch-all 경로가 없다", () => {
  const entries = readdirSync(new URL("device/", ROUTES)).sort();
  assert.deepEqual(entries, ["challenge", "pair", "state", "token"]);
  for (const e of entries) assert.ok(!e.includes("["), `동적 경로가 있다: ${e}`);
});

// ── 5. 브라우저 헤더를 넘기지 않는다 ───────────────────────────────────────
test("장치 경로는 Content-Type만 넘긴다 — 쿠키·Authorization·수집토큰 전부 차단", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    const f = upstreamFor(kind);
    await relay(kind, f, {}, {
      Cookie: "session=abc", Authorization: "Bearer xyz",
      Origin: "https://evil.example", Referer: "https://evil.example/p",
      "X-Forwarded-For": "1.2.3.4", "X-Collector-Token": "should-not-pass",
      "X-Custom-Thing": "nope",
    });
    const h = new Headers(f.calls[0].init.headers as HeadersInit);
    assert.deepEqual([...h.keys()].sort(), ["content-type"], `${kind}가 헤더를 흘렸다`);
  }
});

// ── 6. 성공 응답: 필요한 필드가 살아남는다 ─────────────────────────────────
test("pair 성공 응답에 장치 저장에 필요한 필드가 남는다", async () => {
  const j = await (await relay("device/pair", upstreamFor("device/pair"))).json();
  // device.js: saveDevice({ deviceId: r.deviceId, fingerprint: r.fingerprint,
  //                         deviceName: r.name })  /  popup.js: `등록됐습니다 · ${r.name}`
  assert.equal(j.deviceId, 7);
  assert.equal(j.fingerprint, "AB12-CD34-EF56");
  assert.equal(j.name, "작업용 크롬");
  assert.equal(j.ok, true);
});

test("state 성공 응답에 스케줄러가 보는 필드가 남는다", async () => {
  const j = await (await relay("device/state", upstreamFor("device/state"))).json();
  // sw.js: { mode: st.mode || "MANUAL", deviceActive: !!st.deviceActive }
  assert.equal(j.mode, "AUTO_COLLECT");
  assert.equal(j.deviceActive, true);
});

test("challenge 성공 응답에 서명 대상이 남는다", async () => {
  const j = await (await relay("device/challenge", upstreamFor("device/challenge"))).json();
  // device.js: sign(c.message) → postJson(..., { challengeId: c.challengeId, signature })
  assert.equal(j.challengeId, "ch_abc123");
  assert.equal(j.message, UPSTREAM["device/challenge"].message);
});

test("token 성공 응답에 토큰과 남은 시간이 남는다", async () => {
  const j = await (await relay("device/token", upstreamFor("device/token"))).json();
  // popup.js: $("tok").value = t.token / Math.round(t.ttlSeconds / 60)
  assert.equal(j.token, "tok_fake_for_tests");
  assert.equal(j.ttlSeconds, 600);
});

test("성공 응답을 detail 한 줄로 접지 않는다 — 접으면 pairing이 다시 깨진다", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    const j = await (await relay(kind, upstreamFor(kind))).json();
    assert.ok(!("detail" in j), `${kind} 성공이 detail로 접혔다`);
    assert.ok(Object.keys(j).length >= 3, `${kind} 성공 본문이 비었다: ${Object.keys(j)}`);
  }
});

test("ingest·failure는 성공을 detail로 접는 기존 계약을 그대로 유지한다", async () => {
  for (const kind of ["ingest", "failure"] as const) {
    const f = fakeFetch({ body: JSON.stringify({ ok: true, detail: "받았습니다.",
      internalUrl: "https://internal-9f3a.railway.internal" }) });
    const j = await (await relay(kind, f)).json();
    assert.deepEqual(Object.keys(j), ["detail"], `${kind} 계약이 바뀌었다`);
  }
});

// ── 7. 성공 응답: 허용 목록 밖은 나가지 않는다 ─────────────────────────────
test("백엔드가 새 secret 필드를 더해도 자동으로 새어 나가지 않는다", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    const f = upstreamFor(kind, {
      streamerAccessToken: "LEAK-access-token",
      jwtSecret: "LEAK-jwt-secret",
      internalUrl: "https://internal-9f3a.railway.internal",
      pairingCodeHash: "LEAK-hash",
      newFieldAddedLater: "LEAK-future",
    });
    const text = await (await relay(kind, f)).text();
    for (const leak of ["LEAK-", "railway.internal"]) {
      assert.ok(!text.includes(leak), `${kind}에서 ${leak}가 새어 나갔다`);
    }
  }
});

test("challenge의 nonce는 relay를 넘어오지 않는다 — 서명 대상은 message다", async () => {
  const j = await (await relay("device/challenge",
    upstreamFor("device/challenge"))).json();
  assert.equal(j.nonce, undefined);
  // message 안에 nonce가 들어 있는 것은 정상이다(서명 대상 그 자체). 별도 필드로
  // 한 번 더 내보내지 않는다는 뜻이다.
  assert.deepEqual(Object.keys(j).sort(), ["challengeId", "message", "ok"]);
});

test("성공 응답의 키는 경로별 허용 목록과 정확히 같다", async () => {
  const expected: Record<string, string[]> = {
    "device/pair": ["deviceId", "fingerprint", "name", "ok"],
    "device/state": ["deviceActive", "mode", "ok"],
    "device/challenge": ["challengeId", "message", "ok"],
    "device/token": ["ok", "token", "ttlSeconds"],
  };
  for (const kind of DEVICE_RELAY_KINDS) {
    const j = await (await relay(kind, upstreamFor(kind))).json();
    assert.deepEqual(Object.keys(j).sort(), expected[kind], kind);
  }
});

test("상속받은 속성은 응답에 넣지 않는다 — 백엔드가 안 준 값을 지어내지 않게", async () => {
  // 변이 검사에서 `hasOwnProperty` 가드를 지워도 아무 테스트가 안 깨졌다. 그 가드가
  // 실제로 차이를 만드는 경우가 **프로토타입 오염 하나뿐**이라서다. 그 하나를 덮는다.
  // (오염은 이 프론트엔드에도 얼마든 생길 수 있고, 그때 relay가 백엔드는 준 적 없는
  //  `token`을 성공 응답에 실어 보내면 확장은 그것을 진짜 토큰으로 믿는다.)
  const proto = Object.prototype as any;
  proto.token = "POLLUTED-token";
  proto.fingerprint = "POLLUTED-fingerprint";
  try {
    // 주의: 오염된 동안에는 `JSON.parse(...)`의 결과도 그 프로토타입을 상속한다.
    // `.fingerprint`로 확인하면 relay가 아니라 오염을 보게 되므로 **직렬화된 본문**과
    // **own key 목록**만 본다.
    const f = fakeFetch({ body: JSON.stringify({ ok: true, deviceId: 7, name: "정상" }) });
    const text = await (await relay("device/pair", f)).text();
    assert.ok(!text.includes("POLLUTED"), `상속 속성이 새어 나갔다: ${text}`);
    assert.deepEqual(Object.keys(JSON.parse(text)).sort(), ["deviceId", "name", "ok"]);

    const g = fakeFetch({ body: JSON.stringify({ ok: true, ttlSeconds: 600 }) });
    const tokText = await (await relay("device/token", g)).text();
    assert.ok(!tokText.includes("POLLUTED"), `token이 지어내졌다: ${tokText}`);
    assert.deepEqual(Object.keys(JSON.parse(tokText)).sort(), ["ok", "ttlSeconds"]);
  } finally {
    delete proto.token;
    delete proto.fingerprint;
  }
});

test("허용 필드라도 객체·배열이면 버린다 — 중첩 본문이 통째로 새지 않게", async () => {
  const f = fakeFetch({ body: JSON.stringify({
    ok: true, deviceId: 7, name: "정상",
    fingerprint: { nested: "https://internal-9f3a.railway.internal", all: [1, 2] } }) });
  const text = await (await relay("device/pair", f)).text();
  assert.ok(!text.includes("railway.internal"));
  assert.equal(JSON.parse(text).fingerprint, undefined);
});

// ── 8. HTML·Traceback·내부 주소 차단 (성공·실패 양쪽) ──────────────────────
const NASTY = "<html><body>Traceback (most recent call last):\n"
  + '  File "/app/web/backend/routers/admin_router.py", line 1723\n'
  + "RuntimeError: internal-host-9f3a.railway.internal</body></html>";

test("장치 경로에서 HTML·Traceback을 그대로 흘리지 않는다", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    for (const status of [200, 400, 500]) {
      const f = fakeFetch({ status, body: NASTY, contentType: "text/html" });
      const text = await (await relay(kind, f)).text();
      for (const leak of ["Traceback", "railway", "admin_router.py", "<html", 'File "']) {
        assert.ok(!text.includes(leak), `${kind}/${status}에서 ${leak}가 새어 나갔다`);
      }
      assert.ok(JSON.parse(text).detail.length > 0, `${kind}/${status} detail이 비었다`);
    }
  }
});

test("성공(2xx)인데 JSON이 아니면 성공으로 통과시키지 않는다", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    for (const reply of [
      { status: 200, body: NASTY, contentType: "text/html" },
      { status: 200, body: "not json at all", contentType: "application/json" },
      { status: 200, body: "[1,2,3]", contentType: "application/json" },
      { status: 200, body: "null", contentType: "application/json" },
    ]) {
      assert.equal((await relay(kind, fakeFetch(reply))).status, 502,
        `${kind} ${reply.body.slice(0, 12)}`);
    }
  }
});

test("백엔드 주소가 응답에 절대 들어가지 않는다", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    const f = fakeFetch({ status: 502, body: `cannot reach ${BASE}/api/x`,
                          contentType: "text/plain" });
    const text = await (await relay(kind, f)).text();
    assert.ok(!text.includes("backend.example.com"), kind);
  }
});

test("백엔드 오류 detail은 그대로 보존한다 — 사용자가 이유를 봐야 한다", async () => {
  for (const [status, detail] of [[400, "[bad_code] 등록 코드가 올바르지 않습니다."],
                                  [429, "[rate_limited] 잠시 후 다시 시도해 주세요."]] as const) {
    const f = fakeFetch({ status, body: JSON.stringify({ detail }) });
    const res = await relay("device/pair", f);
    assert.equal(res.status, status);
    assert.equal((await res.json()).detail, detail);
  }
});

test("리다이렉트를 따라가지 않고 502로 닫는다", async () => {
  for (const status of [301, 302, 307, 308]) {
    const f = fakeFetch({ status, body: "", contentType: "text/plain" });
    assert.equal((await relay("device/pair", f)).status, 502, String(status));
  }
});

// ── 9. fail-closed ─────────────────────────────────────────────────────────
test("장치 경로도 POST가 아니면 405이고 백엔드를 부르지 않는다", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    for (const m of ["GET", "PUT", "PATCH", "DELETE"]) {
      const f = fakeFetch();
      const res = await relayCollector(req({}, {}, m), kind,
        { apiBase: BASE, fetchImpl: f.impl });
      assert.equal(res.status, 405, `${kind} ${m}`);
      assert.equal(f.calls.length, 0);
    }
  }
});

test("application/json이 아니면 415이고 백엔드를 부르지 않는다", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    for (const ct of ["text/plain", "application/x-www-form-urlencoded",
                      "multipart/form-data"]) {
      const f = fakeFetch();
      const r = new Request("https://frontend.example.com/x", {
        method: "POST", headers: { "Content-Type": ct }, body: "{}" });
      const res = await relayCollector(r, kind, { apiBase: BASE, fetchImpl: f.impl });
      assert.equal(res.status, 415, `${kind} ${ct}`);
      assert.equal(f.calls.length, 0);
    }
  }
});

test("장치 경로의 본문 상한은 수집본 상한보다 훨씬 좁다", async () => {
  assert.ok(DEVICE_MAX_BODY_BYTES < MAX_BODY_BYTES / 4,
    "장치 본문 상한이 수집본 상한과 비슷하다");
  for (const kind of DEVICE_RELAY_KINDS) {
    const f = fakeFetch();
    const big = JSON.stringify({ pad: "x".repeat(DEVICE_MAX_BODY_BYTES + 100) });
    const res = await relayCollector(req(big), kind, { apiBase: BASE, fetchImpl: f.impl });
    assert.equal(res.status, 413, kind);
    assert.equal(f.calls.length, 0, kind);
  }
});

test("상한은 실제 pairing·서명 본문을 넉넉히 수용한다", async () => {
  // P-256 공개키(SPKI base64) 약 124B, P1363 서명(64B) base64 약 88B.
  const bodies: Record<string, unknown>[] = [
    { pairingCode: "AAAA-BBBB-CCCC", publicKey: "B".repeat(200) },
    { fingerprint: "AB12-CD34-EF56", division: "male_solo", automation: true },
    { challengeId: "ch_" + "a".repeat(40), signature: "S".repeat(120) },
  ];
  for (const b of bodies) {
    assert.ok(new TextEncoder().encode(JSON.stringify(b)).length < DEVICE_MAX_BODY_BYTES);
  }
  assert.equal((await relay("device/token", upstreamFor("device/token"),
    bodies[2])).status, 200);
});

test("백엔드가 응답하지 않으면 504, 연결 실패는 502", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    const hang = ((_u: any, init: any) => new Promise((_r, rej) => {
      init.signal.addEventListener("abort",
        () => rej(new DOMException("aborted", "AbortError")));
    })) as unknown as typeof fetch;
    const t = await relayCollector(req({}), kind,
      { apiBase: BASE, fetchImpl: hang, timeoutMs: 30 });
    assert.equal(t.status, 504, kind);
    assert.ok((await t.json()).detail.length > 0);

    const dead = (async () => {
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    assert.equal((await relayCollector(req({}), kind,
      { apiBase: BASE, fetchImpl: dead })).status, 502, kind);
  }
});

test("production에서 API base가 없거나 https가 아니면 503", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    for (const bad of [undefined, "", "http://backend.example.com", "not-a-url"]) {
      const f = fakeFetch();
      const res = await relayCollector(req({}), kind,
        { apiBase: bad as any, fetchImpl: f.impl, isProduction: true });
      assert.equal(res.status, 503, `${kind} '${bad}'`);
      assert.equal(f.calls.length, 0);
    }
  }
});

test("모든 응답이 Cache-Control: no-store", async () => {
  for (const kind of DEVICE_RELAY_KINDS) {
    assert.equal((await relay(kind, upstreamFor(kind))).headers.get("cache-control"),
      "no-store");
    assert.equal((await relayCollector(req({}, {}, "GET"), kind,
      { apiBase: BASE, fetchImpl: fakeFetch().impl })).headers.get("cache-control"),
      "no-store");
  }
});

// ── 10. 흐름 전체 ──────────────────────────────────────────────────────────
test("pairing → state → challenge → token 전 구간이 relay를 통과한다", async () => {
  const seen: string[] = [];
  const impl = (async (url: any) => {
    const kind = String(url).split("/api/admin/piku/collector/")[1];
    seen.push(kind);
    return new Response(JSON.stringify(UPSTREAM[kind]),
      { status: 200, headers: { "Content-Type": "application/json" } });
  }) as unknown as typeof fetch;
  const go = (kind: RelayKind, body: unknown) =>
    relayCollector(req(body), kind, { apiBase: BASE, fetchImpl: impl })
      .then((r) => r.json());

  const paired = await go("device/pair",
    { pairingCode: "AAAA-BBBB-CCCC", publicKey: "BFAKEKEY=" });
  assert.ok(paired.fingerprint, "지문을 못 받아 장치를 저장할 수 없다");

  const state = await go("device/state", { fingerprint: paired.fingerprint });
  assert.equal(state.deviceActive, true);

  const ch = await go("device/challenge",
    { fingerprint: paired.fingerprint, division: "male_solo", automation: true });
  assert.ok(ch.challengeId && ch.message, "서명할 대상을 못 받았다");

  const tok = await go("device/token",
    { challengeId: ch.challengeId, signature: "FAKESIG" });
  assert.ok(tok.token, "토큰을 못 받았다");
  assert.deepEqual(seen,
    ["device/pair", "device/state", "device/challenge", "device/token"]);
});

// ── 11. 로그 ───────────────────────────────────────────────────────────────
test("pairingCode·publicKey·signature·nonce·token을 로그에 남기지 않는다", async () => {
  const seen: string[] = [];
  const orig = { log: console.log, warn: console.warn, error: console.error,
                 info: console.info, debug: console.debug };
  for (const k of Object.keys(orig) as (keyof typeof orig)[]) {
    (console as any)[k] = (...a: unknown[]) => seen.push(a.map(String).join(" "));
  }
  try {
    for (const kind of DEVICE_RELAY_KINDS) {
      await relay(kind, upstreamFor(kind), {
        pairingCode: "SECRET-PAIR-CODE", publicKey: "SECRET-PUBKEY",
        signature: "SECRET-SIGNATURE", challengeId: "SECRET-CHALLENGE" });
      await relayCollector(req({ pairingCode: "SECRET-PAIR-CODE" }), kind, {
        apiBase: BASE,
        fetchImpl: (async () => { throw new TypeError("fetch failed"); }) as any });
      await relay(kind, fakeFetch({ status: 500, body: NASTY, contentType: "text/html" }));
    }
  } finally {
    Object.assign(console, orig);
  }
  const blob = seen.join("\n");
  for (const leak of ["SECRET-", "n0nc3-s3cr3t", "tok_fake_for_tests", "AB12-CD34-EF56"]) {
    assert.ok(!blob.includes(leak), `로그에 ${leak}가 남았다`);
  }
});
