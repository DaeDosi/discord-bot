const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("token");
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
    throw new Error(err.detail || `HTTP ${res.status}`);
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
    me: () => request<{ id: string; username: string; avatar: string }>("/api/auth/me"),
  },

  guilds: {
    list:     ()            => request<import("./types").Guild[]>("/api/guilds"),
    channels: (gid: string) => request<import("./types").Channel[]>(`/api/guilds/${gid}/channels`),
    roles:    (gid: string) => request<import("./types").Role[]>(`/api/guilds/${gid}/roles`),
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
      request<{ user_id: string; xp: number; level: number }[]>(
        `/api/settings/${gid}/leaderboard`
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
  },
};
