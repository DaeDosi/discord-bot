// 스트리머 페이지 SSR이 쓰는 메타데이터 조회. **서버 전용 모듈**이다.
//
// 왜 따로 있나 — 실측(2026-08-01): `layout.tsx`가 메타데이터를 만들려고 무거운
// 대시보드 API(30일 시계열 포함)를 불렀고, 모든 방문자·크롤러의 SSR이 백엔드에서
// 하나의 rate-limit 버킷으로 합쳐져 429가 났다. 그때 폴백이 `robots: index=false`를
// 달았기 때문에 **크롤링당하는 순간 그 페이지가 색인에서 빠졌다.**
//
// 그래서 두 가지를 나눈다:
//   1) 무엇을 받는가  → 메타 전용 경량 응답(/meta)
//   2) 실패를 어떻게 읽는가 → 404와 429·5xx를 구분한다. 일시 장애로 noindex를 달지 않는다.

// 이 모듈은 **서버에서만** 돈다. 클라이언트 컴포넌트에서 import되면 SSR_SHARED_SECRET을
// 읽는 코드가 브라우저 번들에 섞일 수 있으므로, 그 순간 즉시 터뜨린다.
// (`server-only` 패키지를 쓰면 빌드 단계에서 막을 수 있지만 새 의존성이 필요해
//  런타임 가드로 둔다. 실제 번들 포함 여부는 production build 산출물을 검사해 고정한다.)
if (typeof window !== "undefined") {
  throw new Error("streamerMeta는 서버 전용 모듈입니다 (클라이언트에서 import 금지)");
}

// api.ts를 import하지 않는다 — 이 파일이 그쪽 의존성을 끌고 들어갈 이유가 없다.
const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type MetaStatus = "ok" | "not_found" | "rate_limited" | "unavailable" | "timeout";

export interface StreamerMeta {
  found: boolean;
  channel_id: string;
  channel_name: string | null;
  channel_image_url: string | null;
  summary: { avg_viewers: number; peak_viewers: number; broadcast_hours: number } | null;
  updated_at: number | null;
}

export interface MetaResult {
  status: MetaStatus;
  /** 정상 응답이거나, 실패했지만 직전 정상값을 재사용한 경우에만 채워진다. */
  data: StreamerMeta | null;
  /** 직전 정상값을 재사용했는가(신선하지 않다). */
  stale: boolean;
}

/** 실패했을 때 잠깐 재사용할 직전 정상값. 프로세스 안에서만 산다. */
const STALE_TTL_MS = 10 * 60_000;
const MAX_STALE_ENTRIES = 500;
const _lastGood = new Map<string, { at: number; data: StreamerMeta }>();

/** 테스트 전용 — 캐시 크기 확인. */
export function _cacheSize(): number { return _lastGood.size; }
export function _cacheHas(id: string): boolean { return _lastGood.has(id); }
export function _cacheClear(): void { _lastGood.clear(); }
export const _MAX_STALE_ENTRIES = 500;

export function rememberGood(channelId: string, data: StreamerMeta) {
  // 오래된 것부터 버린다(누수 방지). Map은 삽입 순서를 지킨다.
  if (_lastGood.size >= MAX_STALE_ENTRIES) {
    const oldest = _lastGood.keys().next().value;
    if (oldest !== undefined) _lastGood.delete(oldest);
  }
  _lastGood.delete(channelId);
  _lastGood.set(channelId, { at: Date.now(), data });
}

export function takeStale(channelId: string): StreamerMeta | null {
  const hit = _lastGood.get(channelId);
  if (!hit) return null;
  if (Date.now() - hit.at > STALE_TTL_MS) {
    _lastGood.delete(channelId);
    return null;
  }
  return hit.data;
}

/** 실패 결과는 절대 저장하지 않는다 — 429 한 번이 10분짜리 오답이 되면 안 된다. */
function fail(channelId: string, status: MetaStatus): MetaResult {
  const stale = takeStale(channelId);
  return { status, data: stale, stale: stale !== null };
}

export async function fetchStreamerMeta(channelId: string): Promise<MetaResult> {
  const secret = process.env.SSR_SHARED_SECRET;   // NEXT_PUBLIC_ 아님 → 번들에 안 들어간다
  try {
    const res = await fetch(
      `${BASE}/api/rising/streamer/${encodeURIComponent(channelId)}/meta`,
      {
        // 같은 URL·옵션이면 Next가 렌더 안에서 요청을 합치고, 600초 동안 재사용한다.
        // 별도 전역 캐시를 또 만들면 두 겹이 되어 무효화 시점이 어긋난다.
        next: { revalidate: 600 },
        headers: secret ? { "X-Internal-SSR": secret } : undefined,
      },
    );
    if (res.status === 429) return fail(channelId, "rate_limited");
    if (res.status === 404) return { status: "not_found", data: null, stale: false };
    if (!res.ok) return fail(channelId, "unavailable");

    const data = (await res.json()) as StreamerMeta;
    if (!data?.found) return { status: "not_found", data, stale: false };
    rememberGood(channelId, data);
    return { status: "ok", data, stale: false };
  } catch (e) {
    // 백엔드가 죽어도 페이지 자체는 떠야 한다. 다만 **일시 장애를 '없는 채널'로
    // 읽지 않는다** — 그게 noindex로 이어졌다.
    const timeout = (e as Error)?.name === "AbortError"
      || /timeout/i.test((e as Error)?.message ?? "");
    return fail(channelId, timeout ? "timeout" : "unavailable");
  }
}

/** 이 결과로 noindex를 달아도 되는가.
 *  **오직 '실제로 없는 채널'만이다.** 429·5xx·timeout은 잠시 후 정상이 될 수 있으므로
 *  색인에서 빼면 안 된다(그게 크롤러 폭주 때 페이지가 사라지던 원인이다). */
export function shouldNoIndex(result: MetaResult): boolean {
  return result.status === "not_found";
}
