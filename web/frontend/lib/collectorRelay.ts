/* Collector 전용 최소 relay — **프록시가 아니다.**
 *
 * 확장은 `https://nexbot.shop/api/admin/piku/collector/...`로 보낸다. 그런데
 * `nexbot.shop`은 Vercel 프론트이고 실제 엔드포인트는 Railway 백엔드에만 있어서,
 * 요청이 백엔드에 닿기도 전에 **404**가 났다(2026-08-18 `ingest`, 2026-08-19
 * `device/pair` 실측 — 후자는 운영자가 등록 코드를 넣는 순간 바로 터졌다).
 *
 * 대안 두 가지를 버린 이유:
 *   · 운영자가 Railway 원본 주소를 직접 입력 → 배포 주소가 노출되고 CORS가 걸린다.
 *   · manifest에 `*.railway.app` 권한 추가 → 확장 권한을 넓히는 방향이라 계약 위반.
 *
 * 그래서 프론트에 **아래 표에 적힌 경로만** 뚫는다. 이 파일이 지키는 것:
 *   · 허용 경로는 `RELAY_SPECS`의 키가 **전부**다. 요청 경로를 백엔드 URL에 이어
 *     붙이지 않는다 — 대상 URL은 표에 적힌 **리터럴**로 여기서 만든다. 표에 없는
 *     종류는 백엔드를 부르지 않고 404로 닫는다.
 *   · 브라우저 헤더를 넘기지 않는다. `ingest`만 `Content-Type`+`X-Collector-Token`,
 *     나머지는 `Content-Type`만 간다. 쿠키·Authorization·Origin·Referer는 없다.
 *   · 본문을 **손대지 않는다.** 검증 권위는 Railway 백엔드다(여기서 정규화하면
 *     검증이 두 곳으로 갈라져 어긋난다).
 *   · 백엔드의 HTML·Traceback·내부 주소를 그대로 흘리지 않는다.
 *   · pairingCode·publicKey·signature·nonce·token을 로그에 남기지 않는다(그래서
 *     이 파일에 로그 호출이 **하나도 없다**).
 *
 * ## 성공 응답을 다루는 방식이 두 갈래인 이유
 *
 * `ingest`·`failure`는 확장이 **결과 문구만** 보면 되므로 성공도 `detail` 한 줄로
 * 접는다(`successFields: null`). 백엔드가 무엇을 더 돌려주든 밖으로 나가지 않는다.
 *
 * 장치 인증 4경로는 그럴 수 없다. `fingerprint`가 없으면 장치를 저장하지 못하고
 * `challengeId`·`message`가 없으면 서명할 대상이 없다. 그래서 **경로별 필드 허용
 * 목록**으로 좁힌다 — 목록에 적힌 키만, 그것도 원시값일 때만 통과한다. 백엔드가
 * 나중에 필드를 더해도 이 목록을 고치지 않는 한 밖으로 나가지 않는다.
 */

/** 64행 수집본은 20KB 안팎이다. 상한은 넉넉하되 무한하지 않게 둔다. */
export const MAX_BODY_BYTES = 256 * 1024;

/** 장치 인증 본문은 훨씬 작다 — 공개키(SPKI base64) 약 124B, P1363 서명 약 88B.
 *  수집본과 같은 상한을 줄 이유가 없어 따로 좁게 잡는다. */
export const DEVICE_MAX_BODY_BYTES = 8 * 1024;

/** 백엔드가 이 시간 안에 응답하지 않으면 504로 바꾼다. */
export const DEFAULT_TIMEOUT_MS = 15_000;

/** 허용 필드라도 이보다 긴 문자열은 버린다. 토큰·지문·서명 대상은 전부 짧다. */
const MAX_FIELD_CHARS = 4096;

export type DeviceRelayKind =
  | "device/pair" | "device/state" | "device/challenge" | "device/token";

export type RelayKind = "ingest" | "failure" | DeviceRelayKind;

interface RelaySpec {
  /** 백엔드 경로 **리터럴**. URL은 오직 이 값으로만 만들어진다. */
  readonly path: string;
  /** `ingest`만 수집 토큰 헤더를 넘긴다. */
  readonly forwardCollectorToken: boolean;
  readonly maxBodyBytes: number;
  /** 성공 응답에서 내보낼 키. `null`이면 `detail` 한 줄로 접는다. */
  readonly successFields: readonly string[] | null;
}

/** **이 표가 허용 목록 그 자체다.** 여기 없는 경로는 relay되지 않는다. */
const RELAY_SPECS: Readonly<Record<RelayKind, RelaySpec>> = {
  ingest: {
    path: "ingest", forwardCollectorToken: true,
    maxBodyBytes: MAX_BODY_BYTES, successFields: null,
  },
  failure: {
    path: "failure", forwardCollectorToken: false,
    maxBodyBytes: MAX_BODY_BYTES, successFields: null,
  },
  // ── 장치 인증. 허용 필드는 확장이 **실제로 읽는 것만** 적었다. ──
  // pair      → device.js `saveDevice({deviceId, fingerprint, deviceName: r.name})`
  // state     → sw.js `{mode: st.mode, deviceActive: !!st.deviceActive}`
  // challenge → device.js `sign(c.message)` + `{challengeId: c.challengeId}`.
  //             `nonce`는 넣지 않는다 — 서명 대상은 `message`이고, nonce를 따로
  //             내보내면 쓰지도 않는 비밀이 한 번 더 노출된다.
  // token     → popup.js `$("tok").value = t.token` / `t.ttlSeconds`
  "device/pair": {
    path: "device/pair", forwardCollectorToken: false,
    maxBodyBytes: DEVICE_MAX_BODY_BYTES,
    successFields: ["ok", "deviceId", "name", "fingerprint"],
  },
  "device/state": {
    path: "device/state", forwardCollectorToken: false,
    maxBodyBytes: DEVICE_MAX_BODY_BYTES,
    successFields: ["ok", "deviceActive", "mode"],
  },
  "device/challenge": {
    path: "device/challenge", forwardCollectorToken: false,
    maxBodyBytes: DEVICE_MAX_BODY_BYTES,
    successFields: ["ok", "challengeId", "message"],
  },
  "device/token": {
    path: "device/token", forwardCollectorToken: false,
    maxBodyBytes: DEVICE_MAX_BODY_BYTES,
    successFields: ["ok", "token", "ttlSeconds"],
  },
};

export const RELAY_KINDS = Object.keys(RELAY_SPECS) as readonly RelayKind[];

export const DEVICE_RELAY_KINDS = RELAY_KINDS.filter(
  (k): k is DeviceRelayKind => k.startsWith("device/"));

export interface RelayOptions {
  /** 백엔드 origin. 생략하면 서버 환경변수를 읽는다. */
  apiBase?: string | null;
  /** 테스트 주입용. 생략하면 전역 `fetch`. */
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  /** 생략하면 `NODE_ENV === "production"`. */
  isProduction?: boolean;
}

const HEADERS = { "Content-Type": "application/json", "Cache-Control": "no-store" };

const json = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status, headers: HEADERS });

const jsonBody = (status: number, body: Record<string, unknown>) =>
  new Response(JSON.stringify(body), { status, headers: HEADERS });

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

/** 성공 본문에서 **허용 목록에 적힌 키만** 뽑는다.
 *
 * 원시값(string·number·boolean)만 통과시킨다. 객체·배열을 통째로 넘기면 허용한
 * 키 하나 밑에 내부 구조가 딸려 나갈 수 있다. JSON이 아니거나 객체가 아니면
 * `null`을 돌려주고 호출부가 fail-closed로 닫는다.
 */
function pickAllowed(
  raw: string, contentType: string, fields: readonly string[],
): Record<string, unknown> | null {
  if (!/application\/json/i.test(contentType)) return null;
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { return null; }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const src = parsed as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of fields) {
    if (!Object.prototype.hasOwnProperty.call(src, key)) continue;
    const v = src[key];
    if (typeof v === "boolean" || (typeof v === "number" && Number.isFinite(v))) {
      out[key] = v;
    } else if (typeof v === "string" && v.length <= MAX_FIELD_CHARS) {
      out[key] = v;
    }
    // 객체·배열·null·과대 문자열은 조용히 버린다.
  }
  return out;
}

export async function relayCollector(
  request: Request, kind: RelayKind, opts: RelayOptions = {},
): Promise<Response> {
  // 종류를 **표에서 찾는다.** 문자열을 이어 붙이지 않으므로 `../`나 `%2F`가 섞인
  // 값은 여기서 그대로 미아가 된다. 런타임에 어떤 문자열이 들어와도 마찬가지다.
  const spec = Object.prototype.hasOwnProperty.call(RELAY_SPECS, kind)
    ? RELAY_SPECS[kind] : undefined;
  if (!spec) {
    return json(404, "알 수 없는 경로입니다.");
  }

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
  if (new TextEncoder().encode(body).length > spec.maxBodyBytes) {
    return json(413, "요청이 너무 큽니다.");
  }

  // 넘길 헤더를 **직접 만든다.** 원본 헤더를 복사한 뒤 지우는 방식은 새 헤더가
  // 생겼을 때 조용히 새어 나간다.
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (spec.forwardCollectorToken) {
    const token = request.headers.get("x-collector-token");
    if (token) headers["X-Collector-Token"] = token;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(),
    opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const doFetch = opts.fetchImpl ?? fetch;

  let upstream: Response;
  try {
    upstream = await doFetch(`${base}/api/admin/piku/collector/${spec.path}`, {
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
  const upstreamCt = upstream.headers.get("content-type") || "";

  // `redirect: "manual"`이라 3xx가 그대로 돌아온다. 따라가지도, 그대로 넘기지도
  // 않는다 — Location에 내부 주소가 들어 있을 수 있고, 확장에게는 의미도 없다.
  if (upstream.status >= 300 && upstream.status < 400) {
    return json(502, "수집 서버가 예상치 못한 응답을 보냈습니다.");
  }

  if (spec.successFields && upstream.status >= 200 && upstream.status < 300) {
    const picked = pickAllowed(raw, upstreamCt, spec.successFields);
    if (!picked) {
      // 성공인데 JSON이 아니다 → HTML이 성공으로 새어 나가지 않게 통째로 버린다.
      return json(502, "수집 서버 응답을 이해하지 못했습니다.");
    }
    return jsonBody(upstream.status, picked);
  }

  return json(upstream.status, safeDetail(raw, upstreamCt, upstream.status));
}
