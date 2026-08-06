import type { ChatStatus } from "./types";

export const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── 공유 응답 캐시 + in-flight 중복 제거 ───────────────────────────────────────
// 왜 필요한가: 싱드컵 메인 응답은 참가자 전원(약 850KB, gzip 240KB)이라 한 번의
// 중복 호출이 그대로 전송 비용이 된다. 그런데 같은 데이터를 두 화면(랭킹 탭 /
// 라이브 페이지)이 쓰고, 탭을 옮길 때마다 컴포넌트가 다시 마운트되면서 직전 호출과
// 몇 초 차이로 또 요청이 나갔다. 컴포넌트 바깥(모듈 단위)에 두어야 마운트 수명과
// 무관하게 재사용된다.
//
// 설계 규칙 세 가지:
//  - 진행 중인 같은 요청이 있으면 그 Promise를 그대로 나눠 준다(네트워크 1회).
//  - 성공만 캐시한다. 실패는 남기지 않아 곧바로 재시도할 수 있다.
//  - 요청을 abort하지 않는다. 언마운트한 소비자 하나 때문에 같은 요청을 기다리는
//    다른 화면까지 실패하면 안 되기 때문이다(언마운트 처리는 소비자 쪽 무시로 한다).
type SharedEntry = { at: number; data: unknown };

const _sharedCache = new Map<string, SharedEntry>();
const _sharedPending = new Map<string, Promise<unknown>>();

// 만료된 지 한참 지난 항목까지 들고 있을 이유가 없다(누수 방지).
const SHARED_SWEEP_MS = 10 * 60_000;

function sweepShared(now: number) {
  for (const [key, entry] of _sharedCache) {
    if (now - entry.at > SHARED_SWEEP_MS) _sharedCache.delete(key);
  }
}

/** 캐시가 채워진 시각으로부터 지난 시간(ms). 없으면 null. */
export function sharedAge(key: string): number | null {
  const entry = _sharedCache.get(key);
  return entry ? Date.now() - entry.at : null;
}

/** 네트워크를 부르지 않고 캐시만 본다(재마운트 시 즉시 그리기용). */
export function sharedPeek<T>(key: string, ttlMs: number): T | null {
  const entry = _sharedCache.get(key);
  if (!entry || Date.now() - entry.at >= ttlMs) return null;
  return entry.data as T;
}

export function sharedGet<T>(
  key: string,
  ttlMs: number,
  fetcher: () => Promise<T>,
  opts: { force?: boolean } = {},
): Promise<T> {
  const now = Date.now();
  sweepShared(now);

  if (!opts.force) {
    const entry = _sharedCache.get(key);
    if (entry && now - entry.at < ttlMs) return Promise.resolve(entry.data as T);
  }

  // force여도 이미 나가 있는 요청에는 합류한다 — 방금 시작한 요청이라 어차피
  // 최신이고, 새로고침 버튼 연타가 그대로 중복 호출이 되는 것을 막는다.
  const pending = _sharedPending.get(key);
  if (pending) return pending as Promise<T>;

  const p = fetcher()
    .then((data) => {
      _sharedCache.set(key, { at: Date.now(), data });
      return data;
    })
    .finally(() => {
      // 성공이든 실패든 진행 중 표시는 반드시 지운다. 실패한 Promise가 남으면
      // 이후 모든 호출이 같은 실패를 영구히 되돌려 받는다.
      _sharedPending.delete(key);
    });

  _sharedPending.set(key, p);
  return p;
}

// ── 싱드컵 메인 응답의 공유 정책 ─────────────────────────────────────────────
// limit은 '반환할 스트리머 수의 상한'이지 호출 횟수가 아니다. 검색·정렬·필터가 모두
// 이 응답 안에서 이뤄지므로 참가자 수보다 낮추면 하위 랭킹과 검색이 통째로 누락된다.
// 전송량은 limit이 아니라 '호출 횟수'로 줄인다(공유 캐시 + 폴링 주기 + 304).
export const SINGCUP_MAIN_LIMIT = 3000;
// 이 시간 안의 재요청은 네트워크 없이 캐시로 답한다. 탭 전환·재마운트·두 화면 동시
// 사용이 여기서 흡수된다. 폴링 주기(5분)보다 짧아 정기 갱신을 막지는 않는다.
export const SINGCUP_MAIN_TTL_MS = 60_000;

export const singcupMainKey = (limit: number) => `singcup:main:${limit}`;

// ── 분리 API (Shadow) ────────────────────────────────────────────────────────
// 기본은 꺼져 있다. 켜지기 전까지 화면 동작은 기존 `/main` 그대로다.
export const SPLIT_API_ENABLED =
  (process.env.NEXT_PUBLIC_SINGCUP_SPLIT_API_ENABLED || "false").toLowerCase() === "true";

export type SingcupPageQuery = {
  size?: number; cursor?: string; sort?: string;
  direction?: "desc" | "asc"; snapshotVersion?: string; q?: string;
};

/** 스냅샷 만료는 조용히 최신으로 섞지 않고 호출자가 처음부터 다시 받게 한다. */
export class SnapshotExpiredError extends Error {
  latestSnapshotVersion: string | null;
  constructor(latest: string | null) {
    super("snapshot_expired");
    this.name = "SnapshotExpiredError";
    this.latestSnapshotVersion = latest;
  }
}

async function splitGet<T>(path: string, params: Record<string, unknown>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const url = `${BASE}${path}${qs.toString() ? `?${qs}` : ""}`;
  const res = await fetch(url);
  if (res.status === 409) {
    const body = await res.json().catch(() => ({}));
    throw new SnapshotExpiredError(body?.latestSnapshotVersion ?? null);
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

/** 테스트·수동 무효화용. */
export function sharedClear(key?: string) {
  if (key === undefined) {
    _sharedCache.clear();
    _sharedPending.clear();
  } else {
    _sharedCache.delete(key);
    _sharedPending.delete(key);
  }
}

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
}

/** 서버가 코드로 구분해 준 오류. 호출부가 문자열을 다시 파싱하지 않아도 되게 한다. */
export class ApiError extends Error {
  status: number;
  code: string | null;
  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** 치지직 재연동이 필요해 막힌 요청인가. */
export function isReauthRequiredError(e: unknown): boolean {
  return e instanceof ApiError && e.code === "CHZZK_REAUTH_REQUIRED";
}

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    localStorage.removeItem("token");
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    // detail은 대부분 문자열이지만, 코드로 구분해야 하는 오류는 객체로 온다
    // (예: 치지직 재연동 필요). 둘 다 받는다.
    const detail = err?.detail;
    if (detail && typeof detail === "object") {
      throw new ApiError(detail.message || `HTTP ${res.status}`, res.status,
                         detail.code ?? null);
    }
    throw new ApiError(detail || `HTTP ${res.status}`, res.status);
  }
  return res.json();
}

// ── Auth ─────────────────────────────────────────────────────────────────────
export const api = {
  auth: {
    getLoginUrl:  ()   => request<{ url: string }>("/api/auth/login"),
    callback:     (code: string) =>
      request<{ token: string; user: object }>("/api/auth/callback", {
        method: "POST",
        body:   JSON.stringify({ code }),
      }),
    me: () => request<{ id: string; username: string; global_name: string; avatar: string }>("/api/auth/me"),
  },

  guilds: {
    list:     ()            => request<import("./types").Guild[]>("/api/guilds"),
    channels: (gid: string) => request<import("./types").Channel[]>(`/api/guilds/${gid}/channels`),
    roles:    (gid: string) => request<import("./types").Role[]>(`/api/guilds/${gid}/roles`),
    searchMembers: (gid: string, query: string) =>
      request<import("./types").GuildMember[]>(`/api/guilds/${gid}/members/search?query=${encodeURIComponent(query)}`),
  },

  settings: {
    get: (gid: string) =>
      request<import("./types").GuildConfig>(`/api/settings/${gid}`),
    save: (gid: string, data: import("./types").GuildConfig) =>
      request(`/api/settings/${gid}`, { method: "PUT", body: JSON.stringify(data) }),
    levelRewards: {
      list:   (gid: string) =>
        request<import("./types").LevelReward[]>(`/api/settings/${gid}/level-rewards`),
      add:    (gid: string, data: import("./types").LevelReward) =>
        request(`/api/settings/${gid}/level-rewards`, { method: "POST", body: JSON.stringify(data) }),
      remove: (gid: string, level: number) =>
        request(`/api/settings/${gid}/level-rewards/${level}`, { method: "DELETE" }),
    },
    leaderboard: (gid: string) =>
      request<{ user_id: string; display_name?: string; xp: number; level: number }[]>(
        `/api/settings/${gid}/leaderboard`
      ),
    deleteLeaderboard: (gid: string, user_id: string) =>
      request(`/api/settings/${gid}/leaderboard/${user_id}`, { method: "DELETE" }),
    managers: {
      list:   (gid: string) =>
        request<{ user_id: string; display_name: string }[]>(`/api/settings/${gid}/managers`),
      add:    (gid: string, user_id: string) =>
        request(`/api/settings/${gid}/managers`, { method: "POST", body: JSON.stringify({ user_id }) }),
      remove: (gid: string, user_id: string) =>
        request(`/api/settings/${gid}/managers/${user_id}`, { method: "DELETE" }),
    },
    getVerification: (gid: string) =>
      request<import("./types").VerificationConfig>(`/api/settings/${gid}/verification`),
    saveVerification: (gid: string, data: import("./types").VerificationConfig) =>
      request(`/api/settings/${gid}/verification`, { method: "PUT", body: JSON.stringify(data) }),
  },

  stats: {
    get: () => fetch(`${BASE}/api/stats`).then(r => r.json()) as Promise<{
      guilds: number;
      chzzk_subscriptions: number;
      today_visitors: number;
    }>,
    visit: () => fetch(`${BASE}/api/stats/visit`, { method: "POST" })
      .then(r => r.json()) as Promise<{ today_visitors: number }>,
    announcement: () => fetch(`${BASE}/api/stats/announcement`).then(r => r.json()) as Promise<{ message: string }>,
  },

  admin: {
    getAnnouncement: () => request<{ message: string }>("/api/admin/announcement"),
    saveAnnouncement: (message: string) =>
      request<{ ok: boolean; message: string }>("/api/admin/announcement", {
        method: "PUT",
        body: JSON.stringify({ message }),
      }),

    // 싱드컵 대표 클립 수동 지정 — 전부 OWNER 전용이다(서버가 403으로 막는다).
    // sharedGet 캐시를 쓰지 않는다: 지정 직후의 상태를 봐야 하는 화면이라
    // 몇 초라도 옛 값을 보여주면 "적용했는데 안 바뀐다"로 읽힌다.
    singcupRepSearch: (q: string) =>
      request<import("./types").SingcupRepSearchResult>(
        `/api/admin/singcup/representative/search?q=${encodeURIComponent(q)}`),
    singcupRepState: (channelId: string) =>
      request<import("./types").SingcupRepState>(
        `/api/admin/singcup/representative/${encodeURIComponent(channelId)}`),
    singcupRepPreview: (channelId: string, clipInput: string) =>
      request<import("./types").SingcupRepPreview>(
        "/api/admin/singcup/representative/preview", {
          method: "POST",
          body: JSON.stringify({ channelId, clipInput }),
        }),
    singcupRepApply: (channelId: string, clipInput: string, reason: string) =>
      request<import("./types").SingcupRepApplyResult>(
        "/api/admin/singcup/representative/apply", {
          method: "POST",
          body: JSON.stringify({ channelId, clipInput, reason }),
        }),
    singcupRepClear: (channelId: string) =>
      request<import("./types").SingcupRepApplyResult>(
        "/api/admin/singcup/representative/clear", {
          method: "POST",
          body: JSON.stringify({ channelId }),
        }),

    // 클립 지표 단건 갱신. 대표 지정과 **다른 동작**이라 경로도 이름도 나눠 둔다.
    // 숫자를 보내지 않는다 — 값의 출처는 언제나 카드 API다. 서버가 clipInput에서
    // uid만 뽑아 쓰므로 임의 주소가 외부 요청으로 나가지 않는다.
    singcupClipMetricsPreview: (clipInput: string) =>
      request<import("./types").SingcupClipMetricsPreview>(
        "/api/admin/singcup/clips/metrics/preview", {
          method: "POST",
          body: JSON.stringify({ clipInput }),
        }),
    singcupClipMetricsApply: (clipInput: string) =>
      request<import("./types").SingcupClipMetricsApplyResult>(
        "/api/admin/singcup/clips/metrics/apply", {
          method: "POST",
          body: JSON.stringify({ clipInput }),
        }),
  },

  moderation: {
    warnings: (gid: string) =>
      request<import("./types").WarnUser[]>(`/api/settings/${gid}/warnings`),
    userWarnings: (gid: string, uid: string) =>
      request<import("./types").WarnDetail[]>(`/api/settings/${gid}/warnings/${uid}`),
    clearWarnings: (gid: string, uid: string) =>
      request(`/api/settings/${gid}/warnings/${uid}`, { method: "DELETE" }),
    deleteWarning: (gid: string, uid: string, wid: number) =>
      request(`/api/settings/${gid}/warnings/${uid}/${wid}`, { method: "DELETE" }),
  },

  points: {
    leaderboard: (gid: string) =>
      request<import("./types").PointsEntry[]>(`/api/points/${gid}/leaderboard`),
    adjust: (gid: string, data: { user_id: string; amount: number; reason?: string }) =>
      request(`/api/points/${gid}/adjust`, { method: "POST", body: JSON.stringify(data) }),
    missions: {
      list:   (gid: string) =>
        request<import("./types").Mission[]>(`/api/points/${gid}/missions`),
      create: (gid: string, data: { title: string; description: string; points: number; is_active: boolean }) =>
        request<{ ok: boolean; id: number }>(`/api/points/${gid}/missions`, { method: "POST", body: JSON.stringify(data) }),
      update: (gid: string, id: number, data: { title: string; description: string; points: number; is_active: boolean }) =>
        request(`/api/points/${gid}/missions/${id}`, { method: "PUT", body: JSON.stringify(data) }),
      delete: (gid: string, id: number) =>
        request(`/api/points/${gid}/missions/${id}`, { method: "DELETE" }),
    },
    submissions: {
      list:    (gid: string) =>
        request<import("./types").MissionSubmission[]>(`/api/points/${gid}/submissions`),
      approve: (gid: string, id: number) =>
        request(`/api/points/${gid}/submissions/${id}/approve`, { method: "POST" }),
      reject:  (gid: string, id: number) =>
        request(`/api/points/${gid}/submissions/${id}/reject`, { method: "POST" }),
    },
    gambling: {
      get:  (gid: string) =>
        request<{ title: string; duration: number; bet_amount: number; options: string[] }>(
          `/api/points/${gid}/gambling`
        ),
      save: (gid: string, data: { title: string; duration: number; bet_amount: number; options: string[] }) =>
        request(`/api/points/${gid}/gambling`, { method: "PUT", body: JSON.stringify(data) }),
    },
    shop: {
      items: {
        list:   (gid: string) =>
          request<import("./types").ShopItem[]>(`/api/points/${gid}/shop/items`),
        create: (gid: string, data: { name: string; description: string; image_url: string; points_cost: number; stock: number }) =>
          request<{ ok: boolean; id: number }>(`/api/points/${gid}/shop/items`, { method: "POST", body: JSON.stringify(data) }),
        update: (gid: string, id: number, data: { name: string; description: string; image_url: string; points_cost: number; stock: number }) =>
          request(`/api/points/${gid}/shop/items/${id}`, { method: "PUT", body: JSON.stringify(data) }),
        delete: (gid: string, id: number) =>
          request(`/api/points/${gid}/shop/items/${id}`, { method: "DELETE" }),
      },
      exchanges: {
        list:     (gid: string) =>
          request<import("./types").ShopExchange[]>(`/api/points/${gid}/shop/exchanges`),
        markUsed: (gid: string, id: number) =>
          request(`/api/points/${gid}/shop/exchanges/${id}/use`, { method: "POST" }),
      },
    },
  },

  // 싱드컵 이벤트 — 네이버 라운지 수집 결과(백엔드가 정규화한 형태만 내려온다)
  singcup: {
    // #싱드컵 태그 클립 — 메인/랭킹의 근거.
    // 응답이 커서(참가자 전원) 중복 호출 비용이 크다. 공유 캐시 + in-flight 합류를
    // 태워 "여러 컴포넌트가 동시에 불러도 네트워크는 1회"를 보장한다.
    // 캐시 키에 limit을 넣는다 — limit이 다르면 응답 내용도 다르므로 섞이면 안 된다.
    main: (limit = 200, opts: { force?: boolean } = {}) =>
      sharedGet<import("./types").SingcupMain>(
        singcupMainKey(limit), SINGCUP_MAIN_TTL_MS,
        () => fetch(`${BASE}/api/singcup/main?limit=${limit}`).then(r => {
          // r.ok를 보지 않으면 429/5xx의 에러 JSON({detail:...})이 정상 데이터인 척
          // 캐시에 눌러앉는다 — 그 순간 화면의 참가자 목록이 통째로 빈다.
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        }),
        opts),
    streamerClips: (channelId: string) =>
      fetch(`${BASE}/api/singcup/streamers/${encodeURIComponent(channelId)}/clips`).then(r => r.json()) as Promise<import("./types").SingcupStreamerClips>,

    // ── 분리 API (Shadow) ────────────────────────────────────────────────
    // `/main`은 참가자 전원이라 gzip 약 265KB다. 아래는 같은 데이터를 '필요한
    // 만큼만' 받는 경로로, 실측상 초기 화면이 약 30KB(−88.7%)가 된다.
    // NEXT_PUBLIC_SINGCUP_SPLIT_API_ENABLED 가 켜질 때만 화면이 이쪽을 쓴다.
    //
    // 정렬·검색은 **서버가 전체 참가자 집합을 기준으로** 수행한 결과다.
    // 받아온 페이지 안에서 다시 정렬하거나 검색하면 안 된다(하위권이 사라진다).
    split: {
      summary: (v?: string) =>
        splitGet<import("./types").SingcupSplitSummary>("/api/singcup/summary", { snapshotVersion: v }),
      rankings: (p: SingcupPageQuery = {}) =>
        splitGet<import("./types").SingcupPage>("/api/singcup/rankings-page", p),
      search: (q: string, p: SingcupPageQuery = {}) =>
        splitGet<import("./types").SingcupPage>("/api/singcup/search", { ...p, q }),
      live: (p: SingcupPageQuery = {}) =>
        splitGet<import("./types").SingcupPage>("/api/singcup/live", p),
      movers: (p: { range?: string; size?: number; snapshotVersion?: string } = {}) =>
        splitGet<import("./types").SingcupMovers>("/api/singcup/movers", p),
    },
  },

  // CHZZK Rising — 공개(비로그인) 분석 포털. 인증 불필요라 plain fetch 사용.
  rising: {
    overview: () =>
      fetch(`${BASE}/api/rising/overview`).then(r => r.json()) as Promise<import("./types").RisingOverview>,
    timeseries: (range: import("./types").TimeRange = "24h") =>
      fetch(`${BASE}/api/rising/timeseries?range=${range}`).then(r => r.json()) as Promise<import("./types").RisingTimeseries>,
    liveRanking: (limit = 200) =>
      fetch(`${BASE}/api/rising/live-ranking?limit=${limit}`).then(r => r.json()) as Promise<import("./types").RisingLiveRanking>,
    categories: (range: import("./types").CatRange = "1h", limit = 60) =>
      fetch(`${BASE}/api/rising/categories?range=${range}&limit=${limit}`).then(r => r.json()) as Promise<import("./types").RisingCategories>,
    risingStars: (limit = 20) =>
      fetch(`${BASE}/api/rising/rising-stars?limit=${limit}`).then(r => r.json()) as Promise<import("./types").RisingStars>,
    streamer: (cid: string, days = 30) =>
      fetch(`${BASE}/api/rising/streamer/${encodeURIComponent(cid)}?days=${days}`).then(r => r.json()) as Promise<import("./types").StreamerDashboard>,
    streamerDetail: (cid: string, days = 30) =>
      fetch(`${BASE}/api/rising/streamer/${encodeURIComponent(cid)}/detail?days=${days}`).then(r => r.json()) as Promise<import("./types").StreamerDetail>,
    streamerSession: (cid: string, start: number, end: number) =>
      fetch(`${BASE}/api/rising/streamer/${encodeURIComponent(cid)}/session?start=${start}&end=${end}`).then(r => r.json()) as Promise<import("./types").StreamerSessionSeries>,
    tags: (limit = 60) =>
      fetch(`${BASE}/api/rising/tags?limit=${limit}`).then(r => r.json()) as Promise<import("./types").RisingTags>,
    tagStreamers: (tag: string) =>
      fetch(`${BASE}/api/rising/tag-streamers?tag=${encodeURIComponent(tag)}`).then(r => r.json()) as Promise<import("./types").RisingTagStreamers>,
    tagEffect: (tag?: string) =>
      fetch(`${BASE}/api/rising/tag-effect${tag ? `?tag=${encodeURIComponent(tag)}` : ""}`).then(r => r.json()) as Promise<import("./types").RisingTagEffect>,
    viewerDistribution: () =>
      fetch(`${BASE}/api/rising/viewer-distribution`).then(r => r.json()) as Promise<import("./types").RisingViewerDistribution>,
    trafficHeatmap: (days = 14) =>
      fetch(`${BASE}/api/rising/traffic-heatmap?days=${days}`).then(r => r.json()) as Promise<import("./types").RisingTrafficHeatmap>,
    titleKeywords: (limit = 10) =>
      fetch(`${BASE}/api/rising/title-keywords?limit=${limit}`).then(r => r.json()) as Promise<import("./types").RisingTitleKeywords>,
    search: (keyword: string) =>
      fetch(`${BASE}/api/rising/search?keyword=${encodeURIComponent(keyword)}`).then(r => r.json()) as Promise<{ results: import("./types").RisingSearchResult[] }>,
    rankingPeriod: (range = "24h", sort = "viewership", limit = 100) =>
      fetch(`${BASE}/api/rising/ranking-period?range=${range}&sort=${sort}&limit=${limit}`).then(r => r.json()) as Promise<import("./types").RisingPeriodRanking>,
    categoryStreamers: (category: string) =>
      fetch(`${BASE}/api/rising/category-streamers?category=${encodeURIComponent(category)}`).then(r => r.json()) as Promise<import("./types").RisingCategoryStreamers>,
    newcomers: (limit = 80, group: import("./types").NewcomerGroup = "new") =>
      fetch(`${BASE}/api/rising/newcomers?limit=${limit}&group=${group}`).then(r => r.json()) as Promise<import("./types").RisingNewcomers>,
    status: () =>
      fetch(`${BASE}/api/rising/status`).then(r => r.json()) as Promise<import("./types").RisingStatus>,
    periodFilters: () =>
      fetch(`${BASE}/api/rising/period-filters`).then(r => r.json()) as Promise<import("./types").RisingPeriodFilters>,
    periodAnalysis: (q: {
      range: string; start?: string; end?: string;
      category?: string; tags?: string[]; tier?: string;
    }) => {
      const p = new URLSearchParams({ range: q.range });
      if (q.start) p.set("start", q.start);
      if (q.end) p.set("end", q.end);
      if (q.category) p.set("category", q.category);
      if (q.tags?.length) p.set("tags", q.tags.join(","));
      if (q.tier) p.set("tier", q.tier);
      return fetch(`${BASE}/api/rising/period-analysis?${p}`)
        .then(r => r.json()) as Promise<import("./types").RisingPeriodAnalysis>;
    },
  },

  chzzkAuth: {
    getLoginUrl: (gid: string) =>
      request<{ url: string }>(`/api/chzzk-auth/login-url?guild_id=${encodeURIComponent(gid)}`),
    getStreamerLoginUrl: (gid: string, discordChannel: string, mentionEveryone: boolean) =>
      request<{ url: string }>(
        `/api/chzzk-auth/streamer-login-url?guild_id=${encodeURIComponent(gid)}` +
        `&discord_channel=${encodeURIComponent(discordChannel)}` +
        `&mention_everyone=${mentionEveryone ? 1 : 0}`
      ),
  },

  chzzk: {
    search:  (keyword: string) =>
      request<import("./types").ChzzkSearchResult[]>(`/api/chzzk/search?keyword=${encodeURIComponent(keyword)}`),
    list:    (gid: string) =>
      request<import("./types").ChzzkSubscription[]>(`/api/chzzk/${gid}/subscriptions`),
    add:     (gid: string, data: object) =>
      request(`/api/chzzk/${gid}/subscriptions`, { method: "POST", body: JSON.stringify(data) }),
    update:  (gid: string, id: number, data: object) =>
      request(`/api/chzzk/${gid}/subscriptions/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    remove:  (gid: string, id: number) =>
      request(`/api/chzzk/${gid}/subscriptions/${id}`, { method: "DELETE" }),
    getFollowerRoles: (gid: string) =>
      request<import("./types").FollowerRoles>(`/api/chzzk/${gid}/follower-roles`),
    saveFollowerRoles: (gid: string, data: import("./types").FollowerRoles) =>
      request(`/api/chzzk/${gid}/follower-roles`, { method: "PUT", body: JSON.stringify(data) }),
    verifications: (gid: string) =>
      request<import("./types").ChzzkVerification[]>(`/api/chzzk/${gid}/verifications`),
    followTiers: {
      list:   (gid: string) =>
        request<import("./types").FollowRoleTier[]>(`/api/chzzk/${gid}/follow-tiers`),
      add:    (gid: string, months: number, role_id: string) =>
        request(`/api/chzzk/${gid}/follow-tiers`, { method: "POST", body: JSON.stringify({ months, role_id }) }),
      remove: (gid: string, tierId: number) =>
        request(`/api/chzzk/${gid}/follow-tiers/${tierId}`, { method: "DELETE" }),
    },
    chatCommands: {
      list:   (gid: string) =>
        request<import("./types").ChatCommand[]>(`/api/chzzk/${gid}/chat-commands`),
      create: (gid: string, data: {
        command_type: "checkin" | "reply"; trigger_text: string;
        reward_points?: number; reward_xp?: number; reply_text?: string; is_active?: boolean;
      }) =>
        request<{ ok: boolean; id: number }>(`/api/chzzk/${gid}/chat-commands`, { method: "POST", body: JSON.stringify(data) }),
      update: (gid: string, id: number, data: {
        trigger_text: string; reward_points?: number; reward_xp?: number;
        reply_text?: string; is_active?: boolean;
      }) =>
        request(`/api/chzzk/${gid}/chat-commands/${id}`, { method: "PUT", body: JSON.stringify(data) }),
      remove: (gid: string, id: number) =>
        request(`/api/chzzk/${gid}/chat-commands/${id}`, { method: "DELETE" }),
    },
    chatStatus: (gid: string) =>
      request<ChatStatus>(`/api/chzzk/${gid}/chat-status`),
    chatLog: (gid: string) =>
      request<{ direction: "in" | "out"; nickname: string; content: string; created_at: number }[]>(
        `/api/chzzk/${gid}/chat-log`
      ),
    sendChatTest: (gid: string, content: string, as_streamer: boolean) =>
      request(`/api/chzzk/${gid}/chat-test`, { method: "POST", body: JSON.stringify({ content, as_streamer }) }),
    contentNotify: {
      get:  (gid: string) =>
        request<{
          notify_vod: boolean; notify_clip: boolean; notify_community: boolean;
          vod_channel: string | null; clip_channel: string | null; community_channel: string | null;
        }>(`/api/chzzk/${gid}/content-notify`),
      save: (gid: string, data: {
        notify_vod: boolean; notify_clip: boolean; notify_community: boolean;
        vod_channel: string | null; clip_channel: string | null; community_channel: string | null;
      }) =>
        request(`/api/chzzk/${gid}/content-notify`, { method: "PUT", body: JSON.stringify(data) }),
    },
    overlay: {
      getToken: (gid: string) =>
        request<{ token: string; gambling_overlay_url: string; missions_overlay_url: string }>(
          `/api/chzzk/${gid}/overlay-token`
        ),
      regenerateToken: (gid: string) =>
        request<{ token: string; gambling_overlay_url: string; missions_overlay_url: string }>(
          `/api/chzzk/${gid}/overlay-token/regenerate`, { method: "POST" }
        ),
    },
  },
};
