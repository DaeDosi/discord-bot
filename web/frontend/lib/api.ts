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
  },
};
