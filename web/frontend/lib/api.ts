export const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
    announcement: () => fetch(`${BASE}/api/stats/announcement`).then(r => r.json()) as Promise<{ message: string }>,
  },

  admin: {
    getAnnouncement: () => request<{ message: string }>("/api/admin/announcement"),
    saveAnnouncement: (message: string) =>
      request<{ ok: boolean; message: string }>("/api/admin/announcement", {
        method: "PUT",
        body: JSON.stringify({ message }),
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
    newcomers: (limit = 80) =>
      fetch(`${BASE}/api/rising/newcomers?limit=${limit}`).then(r => r.json()) as Promise<import("./types").RisingNewcomers>,
    status: () =>
      fetch(`${BASE}/api/rising/status`).then(r => r.json()) as Promise<import("./types").RisingStatus>,
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
      request<{
        registered: boolean; connected: boolean;
        last_sync_at: number | null; last_event_at: number | null;
        today_checkins: number;
        recent_checkins: { user_name: string; checked_at: number }[];
      }>(`/api/chzzk/${gid}/chat-status`),
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
    mcEvent: {
      status: (gid: string) =>
        request<{
          invited: boolean;
          event_name?: string;
          is_active?: boolean;
          mc_player_name?: string;
          streamer_connected?: boolean;
          triggers?: { kind: "debuff" | "buff" | "random"; trigger_text: string }[];
          items?: { item_type: "debuff" | "buff"; name: string; points_cost: number; in_random_pool: number }[];
        }>(`/api/chzzk/${gid}/mc-event`),
      savePlayerName: (gid: string, mc_player_name: string) =>
        request(`/api/chzzk/${gid}/mc-event`, { method: "PUT", body: JSON.stringify({ mc_player_name }) }),
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
