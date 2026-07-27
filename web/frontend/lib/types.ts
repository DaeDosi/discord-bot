export interface User {
  id:          string;
  username:    string;
  global_name: string;
  avatar:      string;
}

export interface Guild {
  id:      string;
  name:    string;
  icon:    string | null;
  has_bot: boolean;
}

export interface GuildConfig {
  mod_role_id?:         string | null;
  welcome_channel?:     string | null;
  goodbye_channel?:     string | null;
  log_channel?:         string | null;
  auto_role_id?:        string | null;
  levelup_channel?:     string | null;
  levelup_dm?:          boolean;
  automod_enabled?:     boolean;
  badwords?:            string;
  welcome_message?:     string;
  goodbye_message?:     string;
  warn_kick_threshold?: number;
  warn_ban_threshold?:  number;
  points_per_level?:    number;
}

export interface WarnUser {
  user_id:     string;
  display_name: string;
  count:        number;
  latest_at:    number;
}

export interface WarnDetail {
  id:         number;
  reason:     string;
  created_at: number;
}

export interface Mission {
  id:          number;
  title:       string;
  description: string;
  points:      number;
  is_active:   number;
  created_at:  number;
}

export interface MissionSubmission {
  id:           number;
  mission_id:   number;
  user_id:      string;
  user_name:    string;
  status:       "pending" | "approved" | "rejected";
  submitted_at: number;
  title:        string;
  points:       number;
}

export interface PointsEntry {
  user_id:      string;
  display_name: string;
  points:       number;
}

export interface GuildMember {
  id:           string;
  username:     string;
  global_name:  string | null;
  nick:         string | null;
  display_name: string;
  avatar:       string | null;
}

export interface ShopItem {
  id:          number;
  name:        string;
  description: string;
  image_url:   string;
  points_cost: number;
  stock:       number;  // -1 = unlimited
  is_active:   number;
  created_at:  number;
}

export interface ShopExchange {
  id:           number;
  user_id:      string;
  user_name:    string;
  item_id:      number;
  item_name:    string;
  points_cost:  number;
  image_url:    string;
  exchanged_at: number;
  is_used:      number;
  used_at:      number | null;
}

export interface Channel {
  id:       string;
  name:     string;
  type:     number; // 0=text, 2=voice, 4=category, 5=announcement, 15=forum
  position: number;
}

export interface Role {
  id:    string;
  name:  string;
  color: number;
}

export interface LevelReward {
  level:   number;
  role_id: string;
}

export interface ChzzkSubscription {
  id:                 number;
  discord_channel:    number;
  chzzk_channel_id:  string;
  chzzk_name:        string;
  chzzk_image_url:   string | null;
  is_live:           number;
  mention_everyone:  number;
  follow_role_1month?: string | null;
  follow_role_3month?: string | null;
  chat_enabled:      boolean;
}

export interface FollowerRoles {
  follow_role_1month:  string | null;
  follow_role_3month:  string | null;
  follow_months_tier1: number;
  follow_months_tier2: number;
}

export interface VerificationConfig {
  verification_channel?:   string | null;
  unverified_role_id?:     string | null;
  verified_role_id?:       string | null;
  use_chzzk_verification?: boolean;
  verification_message?:   string;
  embed_color?:            string;
  embed_title?:            string;
}

export interface FollowRoleTier {
  id:      number;
  months:  number;
  role_id: string;
}

export interface ChzzkVerification {
  user_id:      string;
  user_name:    string;
  tier_months:  number;
  follow_date:  string | null;
  follow_days:  number;         // -1 = 팔로우 안 함
  is_following: boolean;
  verified_at:  number;
}

export interface ChzzkSearchResult {
  channelId:       string;
  channelName:     string;
  channelImageUrl: string | null;
  followerCount:   number;
  openLive:        boolean;
}

export interface ChatCommand {
  id:            number;
  command_type:  "checkin" | "reply";
  trigger_text:  string;
  reward_points: number;
  reward_xp:     number;
  reply_text:    string;
  is_active:     boolean;
}

// ── CHZZK Rising (분석 포털) ──────────────────────────────────────────────────
export interface RisingTier {
  key:           string;
  label:         string;
  channels:      number;
  viewers:       number;
  channel_share: number;
}

export interface RisingBlueOcean {
  category:         string;
  lives:            number;
  viewers:          number;
  blue_ocean_index: number;
}

export interface RisingOverview {
  collected_at:   number | null;
  tiers:          RisingTier[];
  blue_ocean:     RisingBlueOcean[];
  summary:        { live_count: number; total_viewers: number } | null;
  deltas?:        { total_viewers: RisingDelta; live_count: RisingDelta };
  history_hours?: number;
}

export interface RisingStar {
  chzzk_channel_id: string;
  channel_name:     string;
  category:         string;
  viewers_now:      number;
  viewers_past:     number;
  growth_rate:      number;
  follower_count:   number;
}

export interface RisingStars {
  collected_at: number | null;
  compared_to?: number;
  stars:        RisingStar[];
  note?:        string;
}

export interface RisingStatus {
  last_run: {
    collected_at:  number;
    live_count:    number;
    total_viewers: number;
    ok:            number;
    note:          string;
  } | null;
  total_snapshots: number;
  successful_runs: number;
  server_time:     number;
}

export type TimeRange = "live" | "24h" | "7d";
export interface RisingTimeseriesPoint {
  t:             number; // epoch seconds
  live_count:    number;
  total_viewers: number;
}
export interface RisingTimeseries {
  range:  string;
  points: RisingTimeseriesPoint[];
}
export interface RisingDelta { prev: number | null; d24h: number | null }

export interface RisingStreamer {
  rank:               number;
  chzzk_channel_id:   string;
  channel_name:       string;
  channel_image_url:  string;
  concurrent_viewers: number;
  viewers_prev:       number | null;
  category_name:      string;
  open_date:          string;
  follower_count:     number;
  follower_prev24h:   number | null;
  live_title:         string;
  adult:              boolean;
}
export interface RisingLiveRanking {
  collected_at: number | null;
  streamers:    RisingStreamer[];
}

export type CatRange = "live" | "1h" | "24h";
export interface RisingCategory {
  category:         string;
  lives:            number;
  viewers:          number;
  avg_viewers:      number;
  blue_ocean_index: number;
  share?:           number;
  change?:          number | null;
  rank?:            number;
}
export interface RisingCategories {
  collected_at: number | null;
  range?:       string;
  categories:   RisingCategory[];
}

// ── 스트리머 개인 분석 대시보드 ──────────────────────────────────────────────
export interface StreamerCategory { category: string; share: number; snapshots: number; }
export interface StreamerDaily { date: string; minutes: number; avg_viewers: number; peak: number; viewership: number; }
export interface StreamerWeekly { week: string; t: number; avg_viewers: number; peak: number; viewership: number; }
export interface StreamerSummary {
  peak_viewers:    number;
  avg_viewers:     number;
  max_follower:    number;
  broadcast_hours: number;
  viewership:      number;
  active_days:     number;
  categories:      StreamerCategory[];
}
export interface StreamerDashboard {
  found:             boolean;
  channel_id:        string;
  channel_name?:     string;
  channel_image_url: string;
  live_title?:       string;
  follower_count?:   number;
  is_live?:          boolean;
  /** 첫 방송일. source가 CHZZK_CHANNEL_HISTORY면 치지직이 준 정확한 값("YYYY-MM-DD HH:mm:ss"),
   *  VOD_ESTIMATE면 다시보기 최고령 영상 기준 추정치다. */
  first_broadcast?:        string | null;
  first_broadcast_iso?:    string | null;
  first_broadcast_source?: "CHZZK_CHANNEL_HISTORY" | "VOD_ESTIMATE" | null;
  total_live_hours?:       number | null;
  window_days?:      number;
  history_days?:     number;
  summary?:          StreamerSummary;
  daily?:            StreamerDaily[];
  weekly?:           StreamerWeekly[];
}

export interface RisingSearchResult {
  channel_id:        string;
  channel_name:      string;
  channel_image_url: string;
  follower_count:    number;
  open_live:         boolean;
}

export interface RisingNewcomer {
  chzzk_channel_id:   string;
  channel_name:       string;
  channel_image_url:  string;
  concurrent_viewers: number;
  category_name:      string;
  open_date:          string;
  follower_count:     number;
  avg_viewers:        number;
  growth_rate:        number | null;
  first_seen_days:    number;
  /** 데뷔 N일차. first_stream_source가 CHZZK면 치지직 첫 방송일 기준(정확),
   *  TRACKED면 NexBot이 이 채널을 처음 본 날 기준(보완값 — 실제보다 짧을 수 있음). */
  debut_days:          number;
  first_stream_date:   string;             // "YYYY-MM-DD" (KST)
  first_stream_source: "CHZZK" | "TRACKED";
  is_new:             boolean;
  tag_new:            boolean;
  tags:               string[];
}
/** 신규 & 초기 분석의 두 그룹 — new: 첫 방송 60일 이내 / small: 평균 시청자 10명 이하 */
export type NewcomerGroup = "new" | "small";
export interface NewcomerSummary {
  count: number; total_viewers: number; avg_viewers: number; peak_viewers: number;
  /** 신규 탭 KPI — 정확한 첫 방송일이 있는 채널만으로 낸 평균 방송 경력(일). 표본 없으면 null */
  avg_debut_days?: number | null;
  debut_sample?:   number;
  /** 소형 탭 KPI — 동시 시청자 3명 초과 채널 비중(%) */
  over3_count?: number;
  over3_share?: number;
}
export interface NewcomerInsights {
  top_category: { name: string; avg_viewers: number; lives: number } | null;
  golden_hour:  { hour: number; avg_viewers: number; uplift_pct: number;
                  samples: number; hours_covered: number } | null;
  baseline:     { avg_viewers: number; top20_cut: number; top10_cut: number;
                  next_target: number } | null;
  // 24시간 골든타임 히트맵 (항상 24칸) / 체급 구간 분포
  hourly?:      { hour: number; avg_viewers: number; channels: number; snaps: number }[];
  tiers?:       { label: string; desc: string; count: number; share: number }[];
  // 제목 유입 키워드 포함/미포함 그룹 비교
  title_keyword?: {
    with_count: number; without_count: number;
    with_avg: number; without_avg: number;
    lift_pct: number | null; keywords: string[];
  } | null;
  // 대기업 방종 '빈집 타임' (소형 탭 전용) — 시간대별 대형 채널 동시 라이브 수와
  // 소형 채널 방송당 평균 시청자. big_lives가 적을수록 시청자가 흩어지는 시간대다.
  vacancy_hourly?: { hour: number; big_lives: number; small_avg_viewers: number; snaps: number }[];
  vacancy_best?:   { hour: number; small_avg_viewers: number; big_lives: number;
                     uplift_pct: number; window_days: number; big_threshold: number } | null;
}
// 신입 기준 카테고리 점유율 — 필터를 통과한 신입 전체로 집계(streamers 절단 전)
export interface NewcomerCategory {
  category: string; viewers: number; lives: number; avg_viewers: number; share: number;
}
export interface RisingNewcomers {
  collected_at: number | null;
  group?:       NewcomerGroup;
  streamers:    RisingNewcomer[];
  summary?:     NewcomerSummary;
  insights?:    NewcomerInsights;
  categories?:  NewcomerCategory[];
  criteria?:    { debut_max_days: number; small_avg_max: number };
}

// 누적(기간) 랭킹 — 실시간 스냅샷 순위와 달리 기간 전체를 집계한 순위
export type PeriodRange = "24h" | "7d";
export type PeriodSort = "viewership" | "avg_viewers" | "peak_viewers" | "broadcast_hours";
export interface PeriodStreamer {
  chzzk_channel_id:  string;
  channel_name:      string;
  channel_image_url: string;
  category_name:     string;
  avg_viewers:       number;
  peak_viewers:      number;
  viewership:        number;  // 시청 시간(시간)
  broadcast_hours:   number;
  follower_count:    number;
  snapshots:         number;
  last_at:           number;
}
export interface RisingPeriodRanking {
  collected_at:   number | null;
  range:          PeriodRange;
  sort:           PeriodSort;
  history_hours:  number;
  streamers:      PeriodStreamer[];
}

// 카테고리별 스트리머 — 시청자 0명 포함 전체(팔로워 온디맨드 보강)
export interface CategoryStreamer {
  chzzk_channel_id:   string;
  channel_name:       string;
  channel_image_url:  string;
  concurrent_viewers: number;
  viewers_prev:       number | null;
  category_name:      string;
  open_date:          string;
  follower_count:     number;
  live_title:         string;
  adult:              boolean;
}
export interface RisingCategoryStreamers {
  collected_at: number | null;
  category:     string;
  streamers:    CategoryStreamer[];
  enriched:     number;
}

// 스트리머 상세 서브탭용 집계 (시간대/세션/랭킹 추이)
export interface StreamerHourly { hour: number; snaps: number; avg_viewers: number; peak_viewers: number; hours: number; }
export interface StreamerSession {
  start: number; end: number; hours: number; avg_viewers: number;
  peak_viewers: number; viewership: number; category: string; categories: string[];
}
export interface StreamerRankDay { date: string; rank: number; total: number; avg_viewers: number; percentile: number | null; }
export interface StreamerDetail {
  channel_id: string; window_days?: number;
  hourly: StreamerHourly[]; sessions: StreamerSession[]; rank_daily: StreamerRankDay[];
}
export interface SessionPoint { t: number; viewers: number; peak?: number; category: string; title: string; }
export interface StreamerSessionSeries { resolution: "10m" | "1h"; points: SessionPoint[]; }

// 태그 검색
export interface RisingTag { tag: string; lives: number; viewers: number; avg_viewers: number; }
export interface RisingTags { collected_at: number | null; tags: RisingTag[]; }
export interface TagStreamer {
  chzzk_channel_id: string; channel_name: string; channel_image_url: string;
  concurrent_viewers: number; viewers_prev: number | null; category_name: string;
  open_date: string; follower_count: number; live_title: string; tags: string[]; adult: boolean;
}
export interface RisingTagStreamers { collected_at: number | null; tag: string; streamers: TagStreamer[]; }

// 태그 유입 효과 비교
export interface TagGroupStats {
  channels: number; avg_viewers: number; avg_hours: number;
  avg_follower: number; avg_follower_gain: number | null;
}
export interface RisingTagEffect {
  collected_at: number | null;
  tag: string | null;
  tagged: TagGroupStats | null;
  untagged: TagGroupStats | null;
  lift: { viewers: number | null; hours: number | null;
          follower: number | null; follower_gain: number | null };
}

// 전체 분석 탭 시각화 3종
export interface ViewerBand { label: string; channels: number; share: number; }
export interface RisingViewerDistribution { collected_at: number | null; total: number; bands: ViewerBand[]; }
export interface HeatCell { avg_viewers: number; samples: number; }
export interface RisingTrafficHeatmap { days: number; grid: HeatCell[][]; }
export interface TitleKeyword { keyword: string; lives: number; viewers: number; avg_viewers: number; }
export interface RisingTitleKeywords { collected_at: number | null; keywords: TitleKeyword[]; }

// ── 기간별 상세 분석 ────────────────────────────────────────────────────────
export interface RisingPeriodFilters {
  categories: string[];
  tags: { tag: string; lives: number }[];
}
export interface PeriodPoint { t: number; viewers: number; channels: number; viewership: number }
export interface PeriodSummary {
  viewership: number; avg_viewers: number;
  peak_viewers: number; peak_at: number;
  avg_channels: number; total_channels: number;
  top_category: string; top_category_share: number;
}
export interface PeriodTableRow {
  category: string; hours: number;
  peak_channels: number; avg_channels: number;
  peak_viewers: number; avg_viewers: number; viewership: number;
}
export interface RisingPeriodAnalysis {
  range: string; start: number; end: number;
  tier: string; category: string; tags: string[];
  /** 태그 필터가 참조한 원본 스냅샷 범위(시간). 0이면 태그 필터 없음 */
  tag_scope_hours: number;
  /** 시계열 묶음 단위 — 3일 이하면 "hour" */
  bucket: "hour" | "day";
  summary: PeriodSummary | null;
  series: PeriodPoint[];
  hourly: { hour: number; avg_viewers: number; samples: number }[];
  dow: { dow: number; avg_viewers: number; samples: number }[];
  table: PeriodTableRow[];
  error?: string;
  detail?: string;
}
