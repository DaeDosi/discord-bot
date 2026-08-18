/* Collector 전용 최소 relay — **프록시가 아니다.**
 *
 * 확장은 `https://nexbot.shop/api/admin/piku/collector/ingest`로 보낸다. 그런데
 * `nexbot.shop`은 Vercel 프론트이고 실제 엔드포인트는 Railway 백엔드에만 있어서,
 * 요청이 백엔드에 닿기도 전에 **404**가 났다(2026-08-18 실측).
 *
 * 대안 두 가지를 버린 이유:
 *   · 운영자가 Railway 원본 주소를 직접 입력 → 배포 주소가 노출되고 CORS가 걸린다.
 *   · manifest에 `*.railway.app` 권한 추가 → 확장 권한을 넓히는 방향이라 계약 위반.
 *
 * 그래서 프론트에 **경로 두 개만** 뚫는다. 이 파일이 지키는 것:
 *   · 허용 경로는 `ingest`·`failure` **둘뿐**이다. 요청 경로를 백엔드 URL에 이어
 *     붙이지 않는다 — 대상 URL은 호출부가 넘긴 종류에서 **여기서 만든다**.
 *   · 브라우저 헤더를 넘기지 않는다. `ingest`는 `Content-Type`+`X-Collector-Token`,
 *     `failure`는 `Content-Type`만 간다. 쿠키·Authorization·Origin·Referer는 없다.
 *   · 본문을 **손대지 않는다.** 검증 권위는 Railway 백엔드다(여기서 정규화하면
 *     검증이 두 곳으로 갈라져 어긋난다).
 *   · 백엔드의 HTML·Traceback·내부 주소를 그대로 흘리지 않는다.
 *   · token·payload를 로그에 남기지 않는다(그래서 이 파일에 로그 호출이 없다).
 */

/** 64행 수집본은 20KB 안팎이다. 상한은 넉넉하되 무한하지 않게 둔다. */
export const MAX_BODY_BYTES = 256 * 1024;

/** 백엔드가 이 시간 안에 응답하지 않으면 504로 바꾼다. */
export const DEFAULT_TIMEOUT_MS = 15_000;

export type RelayKind = "ingest" | "failure";

export interface RelayOptions {
  /** 백엔드 origin. 생략하면 서버 환경변수를 읽는다. */
  apiBase?: string | null;
  /** 테스트 주입용. 생략하면 전역 `fetch`. */
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  /** 생략하면 `NODE_ENV === "production"`. */
  isProduction?: boolean;
}

const json = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });

/** production에서는 https만 받는다. 개발에서는 localhost를 허용한다. */
function resolveBase(raw: string | null | undefined, isProd: boolean): string | null {
  const s = (raw ?? "").trim();
  if (!s) return null;
  let u: URL;
  try { u = new URL(s); } catch { return null; }
  if (u.protocol === "https:") return u.origin;
  if (!isProd && u.protocol === "http:"
      && (u.hostname === "localhost" || u.hostname === "127.0.0.1")) return u.origin;
  return null;
}

/** 백엔드 응답에서 **안전한 detail만** 꺼낸다.
 *
 * JSON이고 `detail`이 짧은 문자열일 때만 그대로 쓴다. HTML·Traceback·긴 본문은
 * 통째로 버리고 우리 문구로 바꾼다 — 내부 경로와 호스트명이 그쪽에 섞여 있다.
 */
function safeDetail(raw: string, contentType: string, status: number): string {
  const fallback = status < 400
    ? "수집 서버가 요청을 받았습니다."
    : status >= 500
      ? "수집 서버에서 오류가 발생했습니다. 잠시 후 새 토큰으로 다시 시도해 주세요."
      : "수집 서버가 요청을 거부했습니다.";
  if (!/application\/json/i.test(contentType)) return fallback;
  try {
    const parsed = JSON.parse(raw);
    const d = parsed && parsed.detail;
    if (typeof d === "string" && d.length > 0 && d.length <= 300
        && !/https?:\/\//i.test(d) && !/Traceback|File "|\.py"|<[a-z]/i.test(d)) {
      return d;
    }
  } catch { /* JSON이 아니면 우리 문구를 쓴다 */ }
  return fallback;
}

export async function relayCollector(
  request: Request, kind: RelayKind, opts: RelayOptions = {},
): Promise<Response> {
  if (request.method !== "POST") {
    return json(405, "POST만 허용됩니다.");
  }

  const isProd = opts.isProduction
    ?? (typeof process !== "undefined" && process.env.NODE_ENV === "production");
  const base = resolveBase(
    opts.apiBase !== undefined
      ? opts.apiBase
      : (typeof process !== "undefined" ? process.env.NEXT_PUBLIC_API_URL : null),
    isProd,
  );
  if (!base) {
    // 주소를 모르는 채로 통과시키면 "보낸 것 같은데 아무 데도 안 간" 상태가 된다.
    return json(503, "수집 서버 주소가 설정되지 않았습니다. 운영자에게 문의해 주세요.");
  }

  const ct = request.headers.get("content-type") || "";
  if (!/^application\/json\s*(;|$)/i.test(ct.trim())) {
    return json(415, "application/json 본문만 허용됩니다.");
  }

  const body = await request.text();
  // 바이트 기준으로 잰다 — 한글은 문자 수보다 3배 크다.
  if (new TextEncoder().encode(body).length > MAX_BODY_BYTES) {
    return json(413, "요청이 너무 큽니다.");
  }

  // 넘길 헤더를 **직접 만든다.** 원본 헤더를 복사한 뒤 지우는 방식은 새 헤더가
  // 생겼을 때 조용히 새어 나간다.
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (kind === "ingest") {
    const token = request.headers.get("x-collector-token");
    if (token) headers["X-Collector-Token"] = token;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(),
    opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const doFetch = opts.fetchImpl ?? fetch;

  let upstream: Response;
  try {
    upstream = await doFetch(`${base}/api/admin/piku/collector/${kind}`, {
      method: "POST",
      headers,
      body,
      credentials: "omit",
      cache: "no-store",
      redirect: "manual",
      signal: controller.signal,
    });
  } catch (e) {
    const aborted = e instanceof Error
      && (e.name === "AbortError" || e.name === "TimeoutError");
    return aborted
      ? json(504, "수집 서버가 제때 응답하지 않았습니다. 새 토큰으로 다시 시도해 주세요.")
      : json(502, "수집 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.");
  } finally {
    clearTimeout(timer);
  }

  const raw = await upstream.text().catch(() => "");
  return json(upstream.status,
    safeDetail(raw, upstream.headers.get("content-type") || "", upstream.status));
}
