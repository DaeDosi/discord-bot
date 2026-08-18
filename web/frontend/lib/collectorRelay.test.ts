/* Collector relay — nexbot.shop이 Railway 백엔드로 넘기는 **두 경로만**의 계약.
 *
 * 확장은 `https://nexbot.shop/api/admin/piku/collector/ingest`로 보내는데, 그 주소는
 * Vercel 프론트다. Next.js에 해당 route가 없어 요청이 Railway에 닿기도 전에 404가
 * 났다(2026-08-18 실측). 사용자에게 Railway 원본 주소를 입력하게 하거나 확장에
 * `*.railway.app` 권한을 주는 대신, 프론트에 **최소 relay**를 둔다.
 *
 * 이 relay는 프록시가 아니다 — 경로 두 개만 허용하고, 헤더를 거의 전부 버리며,
 * 본문을 **손대지 않는다**(검증 권위는 Railway 백엔드다).
 */
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

import { MAX_BODY_BYTES, relayCollector } from "./collectorRelay.ts";

const BASE = "https://backend.example.com";

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

function req(body: string, headers: Record<string, string> = {}, method = "POST") {
  return new Request("https://nexbot.shop/api/admin/piku/collector/ingest", {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    ...(method === "GET" || method === "HEAD" ? {} : { body }),
  });
}

const PAYLOAD = JSON.stringify({ schemaVersion: 1, division: "male_solo", rows: [] });

// ── 1~2. 정확한 대상 경로 ───────────────────────────────────────────────────
test("ingest는 백엔드의 ingest 경로로 그대로 전달된다", async () => {
  const f = fakeFetch();
  const res = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t1" }), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(res.status, 200);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, `${BASE}/api/admin/piku/collector/ingest`);
  assert.equal(f.calls[0].init.method, "POST");
});

test("failure는 백엔드의 failure 경로로 전달된다", async () => {
  const f = fakeFetch();
  await relayCollector(req(JSON.stringify({ division: "male_solo", kind: "partial" })),
    "failure", { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(f.calls[0].url, `${BASE}/api/admin/piku/collector/failure`);
});

test("본문을 수정하지 않고 그대로 넘긴다 — 검증 권위는 백엔드다", async () => {
  const f = fakeFetch();
  const weird = '{"division":"male_solo","rows":[{"rank":1,"streamer":"유람"}],"x":null}';
  await relayCollector(req(weird, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(f.calls[0].init.body, weird);
});

// ── 3. POST 외 거부 ─────────────────────────────────────────────────────────
test("POST가 아니면 405이고 백엔드를 부르지 않는다", async () => {
  for (const m of ["GET", "PUT", "PATCH", "DELETE"]) {
    const f = fakeFetch();
    const res = await relayCollector(req(PAYLOAD, {}, m), "ingest",
      { apiBase: BASE, fetchImpl: f.impl });
    assert.equal(res.status, 405, `${m}이 통과했다`);
    assert.equal(f.calls.length, 0, `${m}에서 백엔드를 불렀다`);
  }
});

test("route 모듈은 POST만 내보낸다", () => {
  for (const p of ["ingest", "failure"]) {
    const src = readFileSync(
      new URL(`../app/api/admin/piku/collector/${p}/route.ts`, import.meta.url), "utf8");
    assert.ok(/export async function POST/.test(src), `${p}에 POST가 없다`);
    for (const m of ["GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
      assert.ok(!new RegExp(`export (async )?function ${m}\\b`).test(src),
        `${p}가 ${m}을 내보낸다`);
    }
  }
});

// ── 4. 임의 하위 경로 없음 ──────────────────────────────────────────────────
test("collector 아래에 동적·catch-all 경로가 없다", () => {
  const dir = new URL("../app/api/admin/piku/collector/", import.meta.url);
  const entries: string[] = readdirSync(dir);
  assert.deepEqual(entries.sort(), ["failure", "ingest"]);
  for (const e of entries) {
    assert.ok(!e.includes("["), `동적 경로가 있다: ${e}`);
  }
});

// ── 5~9. 헤더 전달·차단 ────────────────────────────────────────────────────
test("ingest는 X-Collector-Token을 전달한다", async () => {
  const f = fakeFetch();
  await relayCollector(req(PAYLOAD, { "X-Collector-Token": "secret-token" }), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  const h = new Headers(f.calls[0].init.headers as HeadersInit);
  assert.equal(h.get("x-collector-token"), "secret-token");
  assert.equal(h.get("content-type"), "application/json");
});

test("failure는 token을 요구하지도 전달하지도 않는다", async () => {
  const f = fakeFetch();
  const res = await relayCollector(
    req(JSON.stringify({ division: "male_solo", kind: "partial" }),
        { "X-Collector-Token": "should-not-pass" }), "failure",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(res.status, 200);
  const h = new Headers(f.calls[0].init.headers as HeadersInit);
  assert.equal(h.get("x-collector-token"), null);
});

test("cookie·Authorization·Origin·Referer·임의 헤더를 넘기지 않는다", async () => {
  const f = fakeFetch();
  await relayCollector(req(PAYLOAD, {
    "X-Collector-Token": "t",
    Cookie: "session=abc",
    Authorization: "Bearer xyz",
    Origin: "https://evil.example",
    Referer: "https://evil.example/page",
    "X-Forwarded-For": "1.2.3.4",
    "X-Custom-Thing": "nope",
  }), "ingest", { apiBase: BASE, fetchImpl: f.impl });

  const h = new Headers(f.calls[0].init.headers as HeadersInit);
  const passed = [...h.keys()].sort();
  assert.deepEqual(passed, ["content-type", "x-collector-token"]);
  assert.equal((f.calls[0].init as any).credentials, "omit");
});

// ── 10. 본문 상한 ───────────────────────────────────────────────────────────
test("본문이 상한을 넘으면 413이고 백엔드를 부르지 않는다", async () => {
  const f = fakeFetch();
  const big = JSON.stringify({ pad: "x".repeat(MAX_BODY_BYTES + 100) });
  const res = await relayCollector(req(big, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(res.status, 413);
  assert.equal(f.calls.length, 0);
});

test("상한은 64행 수집본을 넉넉히 수용한다", async () => {
  const rows = Array.from({ length: 64 }, (_, i) => ({
    rank: i + 1, streamer: `스트리머 이름 ${i}`, song_title: `노래 제목 ${i}`,
    artist: `가수 이름 ${i}`, win_ratio: 12.34, win_rate: 56.78,
    image_url: `https://cdn.piku.co.kr/thumbnail/${i}/very-long-file-name.jpg`,
  }));
  const body = JSON.stringify({ schemaVersion: 1, division: "male_solo",
    sourceId: "7PqH44", sourceUrl: "https://www.piku.co.kr/w/rank/7PqH44",
    collectedAt: new Date().toISOString(), rowCount: 64, rows });
  assert.ok(body.length < MAX_BODY_BYTES,
    `64행(${body.length}B)이 상한(${MAX_BODY_BYTES}B)을 넘는다`);
  const f = fakeFetch();
  const res = await relayCollector(req(body, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(res.status, 200);
});

// ── 11. Content-Type ────────────────────────────────────────────────────────
test("application/json이 아니면 415이고 백엔드를 부르지 않는다", async () => {
  for (const ct of ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]) {
    const f = fakeFetch();
    const r = new Request("https://nexbot.shop/x", {
      method: "POST", headers: { "Content-Type": ct }, body: PAYLOAD });
    const res = await relayCollector(r, "ingest", { apiBase: BASE, fetchImpl: f.impl });
    assert.equal(res.status, 415, `${ct}가 통과했다`);
    assert.equal(f.calls.length, 0);
  }
});

test("charset이 붙은 application/json은 허용한다", async () => {
  const f = fakeFetch();
  const r = new Request("https://nexbot.shop/x", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8", "X-Collector-Token": "t" },
    body: PAYLOAD });
  assert.equal((await relayCollector(r, "ingest", { apiBase: BASE, fetchImpl: f.impl })).status, 200);
});

// ── 12. timeout ─────────────────────────────────────────────────────────────
test("백엔드가 응답하지 않으면 504로 바꾼다", async () => {
  const impl = ((_u: any, init: any) => new Promise((_res, rej) => {
    init.signal.addEventListener("abort",
      () => rej(new DOMException("aborted", "AbortError")));
  })) as unknown as typeof fetch;
  const res = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: BASE, fetchImpl: impl, timeoutMs: 30 });
  assert.equal(res.status, 504);
  assert.ok((await res.json()).detail.length > 0);
});

test("백엔드 연결 자체가 실패하면 502로 바꾼다", async () => {
  const impl = (async () => { throw new TypeError("fetch failed"); }) as unknown as typeof fetch;
  const res = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: BASE, fetchImpl: impl });
  assert.equal(res.status, 502);
});

// ── 13~14. 응답 처리 ────────────────────────────────────────────────────────
test("백엔드의 의미 있는 상태 코드를 그대로 보존한다", async () => {
  for (const status of [400, 401, 403, 409, 422, 500]) {
    const f = fakeFetch({ status, body: JSON.stringify({ detail: "백엔드 사유" }) });
    const res = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t" }), "ingest",
      { apiBase: BASE, fetchImpl: f.impl });
    assert.equal(res.status, status);
    assert.equal((await res.json()).detail, "백엔드 사유");
  }
});

test("백엔드가 HTML·Traceback을 주면 안전한 문구로 치환한다", async () => {
  const nasty = "<html><body>Traceback (most recent call last):\n"
    + '  File "/app/web/backend/routers/admin_router.py", line 1481\n'
    + "RuntimeError: internal-host-9f3a.railway.internal</body></html>";
  const f = fakeFetch({ status: 500, body: nasty, contentType: "text/html" });
  const res = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(res.status, 500);
  const body = await res.text();
  for (const leak of ["Traceback", "railway", "admin_router.py", "<html", "File \""]) {
    assert.ok(!body.includes(leak), `응답에 ${leak}가 새어 나왔다`);
  }
  assert.ok(JSON.parse(body).detail.length > 0);
});

test("응답에 백엔드 주소가 절대 들어가지 않는다", async () => {
  const f = fakeFetch({ status: 502, body: `cannot reach ${BASE}/api/x`, contentType: "text/plain" });
  const res = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.ok(!(await res.text()).includes("backend.example.com"));
});

// ── 15. API base fail-closed ────────────────────────────────────────────────
test("production에서 API base가 없거나 https가 아니면 503", async () => {
  for (const bad of [undefined, "", "   ", "http://backend.example.com",
                     "not-a-url", "ftp://x.example.com"]) {
    const f = fakeFetch();
    const res = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t" }), "ingest",
      { apiBase: bad as any, fetchImpl: f.impl, isProduction: true });
    assert.equal(res.status, 503, `'${bad}'가 통과했다`);
    assert.equal(f.calls.length, 0);
  }
});

test("개발 환경에서는 localhost API base를 허용한다", async () => {
  const f = fakeFetch();
  const res = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: "http://localhost:8000", fetchImpl: f.impl, isProduction: false });
  assert.equal(res.status, 200);
  assert.equal(f.calls[0].url, "http://localhost:8000/api/admin/piku/collector/ingest");
});

// ── 16. 로그 ────────────────────────────────────────────────────────────────
test("token·payload를 로그에 출력하지 않는다", async () => {
  const seen: string[] = [];
  const orig = { log: console.log, warn: console.warn, error: console.error,
                 info: console.info, debug: console.debug };
  for (const k of Object.keys(orig) as (keyof typeof orig)[]) {
    (console as any)[k] = (...a: unknown[]) => seen.push(a.map(String).join(" "));
  }
  try {
    const f = fakeFetch({ status: 500, body: "<html>boom</html>", contentType: "text/html" });
    await relayCollector(req(JSON.stringify({ rows: [{ streamer: "유람 Yuram" }] }),
      { "X-Collector-Token": "super-secret-token" }), "ingest",
      { apiBase: BASE, fetchImpl: f.impl });
    const impl = (async () => { throw new TypeError("fetch failed"); }) as unknown as typeof fetch;
    await relayCollector(req(PAYLOAD, { "X-Collector-Token": "super-secret-token" }), "ingest",
      { apiBase: BASE, fetchImpl: impl });
  } finally {
    Object.assign(console, orig);
  }
  const blob = seen.join("\n");
  for (const leak of ["super-secret-token", "유람 Yuram", "male_solo"]) {
    assert.ok(!blob.includes(leak), `로그에 ${leak}이 남았다`);
  }
});

test("소스에 로그 호출 자체가 없다", () => {
  const src = readFileSync(new URL("./collectorRelay.ts", import.meta.url), "utf8");
  for (const bad of ["console.log", "console.error", "console.warn", "console.info"]) {
    assert.ok(!src.includes(bad), `relay에 ${bad}가 있다`);
  }
});

// ── 17. 캐시 ────────────────────────────────────────────────────────────────
test("모든 응답이 Cache-Control: no-store", async () => {
  const f = fakeFetch();
  const ok = await relayCollector(req(PAYLOAD, { "X-Collector-Token": "t" }), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(ok.headers.get("cache-control"), "no-store");
  const bad = await relayCollector(req(PAYLOAD, {}, "GET"), "ingest",
    { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(bad.headers.get("cache-control"), "no-store");
});

// ── 18. 다른 경로는 relay하지 않는다 ────────────────────────────────────────
test("허용 목록은 ingest·failure 둘뿐이다", () => {
  const src = readFileSync(new URL("./collectorRelay.ts", import.meta.url), "utf8");
  for (const forbidden of ["token", "status", "mappings", "confirm-exact",
                           "preview", "publish", "import"]) {
    assert.ok(!src.includes(`"${forbidden}"`), `relay가 ${forbidden}을 안다`);
  }
});

test("relay는 요청 경로를 백엔드 URL에 이어 붙이지 않는다", async () => {
  const f = fakeFetch();
  const r = new Request("https://nexbot.shop/api/admin/piku/collector/ingest/../../publish", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Collector-Token": "t" },
    body: PAYLOAD });
  await relayCollector(r, "ingest", { apiBase: BASE, fetchImpl: f.impl });
  assert.equal(f.calls[0].url, `${BASE}/api/admin/piku/collector/ingest`);
});
