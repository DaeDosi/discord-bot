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
  /** Discord 스노플레이크 — **문자열**이다. number로 두면 2^53을 넘어
   *  정밀도가 깎인다(utils/ids.py 참고). */
  discord_channel:    string;
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

/** 치지직 OAuth 토큰 상태. 값은 백엔드 `utils/oauth_backoff.py`의 상수와 같다.
 *  `null`은 "아직 스트리머 연동을 한 적 없음"이다 — 'ok'와 반드시 구분해야 한다. */
export type ChzzkTokenState = "ok" | "retrying" | "reauth_required" | "disabled";

export interface ChatStatus {
  registered:      boolean;
  connected:       boolean;
  last_sync_at:    number | null;
  last_event_at:   number | null;
  today_checkins:  number;
  recent_checkins: { user_name: string; checked_at: number }[];
  // 재연동 안내용. 토큰 값은 어떤 형태로도 오지 않는다.
  token_state:           ChzzkTokenState | null;
  reauth_required:       boolean;
  streamer_linked:       boolean;
  token_fail_count:      number;
  token_last_fail_at:    number | null;
  token_last_error_code: string | null;
  token_last_success_at: number | null;
  token_next_try_at:     number | null;
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

export type TimeRange = "live" | "24h" | "48h" | "72h" | "7d";
export interface RisingTimeseriesPoint {
  t:             number; // epoch seconds (버킷이면 구간 시작)
  live_count:    number;
  total_viewers: number;
  samples:       number;  // 이 버킷에 들어간 정상 수집 사이클 수 (원본은 1)
  partial:       boolean; // 아직 채워지는 중인 마지막 버킷 = '집계 중'
}
export interface RisingTimeseries {
  range:           TimeRange;
  window_seconds:  number;
  bucket_seconds:  number;  // 0이면 원본 10분 간격
  step_seconds:    number;  // 이 간격을 크게 벗어나면 수집 공백 → 선을 끊는다
  history_hours:   number;  // 확보된 수집 이력
  truncated:       boolean; // 이력이 요청 창보다 짧다
  excluded_points: number;  // 실패·부분실패로 그래프에서 제외한 사이클 수
  points:          RisingTimeseriesPoint[];
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
  /** 팀/소속 태그. `tags`(치지직 방송 태그)와 **다른 필드**다. 없으면 빈 배열. */
  team_tags?:         import("./types").StreamerTag[];
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
  /** 팀/소속 태그 — 상세 페이지 이름 옆. `tags`(방송 태그)와 다른 필드다. */
  team_tags?:        import("./types").StreamerTag[];
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
  /** 치지직 **방송 태그** 문자열. 아래 team_tags와 다른 개념이다. */
  tags:               string[];
  /** 운영자가 붙인 팀/소속 태그. 없으면 빈 배열. */
  team_tags?:         import("./types").StreamerTag[];
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
  /** 팀/소속 태그. `tags`(치지직 방송 태그)와 **다른 필드**다. 없으면 빈 배열. */
  team_tags?:        import("./types").StreamerTag[];
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

// ── 싱드컵 이벤트 ────────────────────────────────────────────────────────────
// 백엔드(singcup_collector)가 네이버 라운지 응답을 정규화해 내려준다.
// 프론트는 원본 API 구조를 전혀 모른다.
export type SingcupStatus = "UPCOMING" | "LIVE" | "ENDED";

// ── 싱드컵: 클립 기반 메인/랭킹 ─────────────────────────────────────────────
// 자유게시판 버프 순위(GET /api/singcup/rankings)와는 별개 데이터다. 그쪽 UI는
// 제거됐고 타입도 같이 지웠다 — 백엔드 API와 응답 계약은
// docs/작업정리_2026-08-02_싱드컵_자유게시판_홍보글_API.md 에 보존돼 있다.
// 백엔드가 대표 클립·점수·변화량·현재 라이브까지 계산해 내려주므로
// 프론트에서 다시 그룹화하거나 정렬 기준을 재계산하지 않는다.
export interface SingcupLive {
  liveTitle:         string;
  concurrentViewers: number;
  categoryName:      string;
}
export interface SingcupHeartMover {
  rank: number;
  channelId: string; channelName: string; channelImageUrl: string;
  clipUid: string; clipTitle: string; clipThumbnailUrl: string;
  heartCount: number;
  /** 동일 clipUid의 약 1시간 전 하트와의 차이(양수만 노출된다) */
  heartDelta1h: number;
  score: number;
  live: SingcupLive | null;
}

export interface SingcupStreamer {
  rank:               number;
  channelId:          string;
  channelName:        string;
  channelImageUrl:    string;
  followerCount:      number;
  verifiedMark:       boolean;
  taggedClipCount:    number;
  clipUid:            string;
  clipTitle:          string;
  clipThumbnailUrl:   string;
  heartCount:         number;
  viewCount:          number;
  createdAt:          string;
  /** 조회수 환산 점수(0~70) */
  viewScore:          number;
  /** 하트 환산 점수(0~30) */
  heartScore:         number;
  /** 비공식 예상 인기점수(0~100) */
  score:              number;
  /** 1시간 전 하트와의 차이. 그 사이 대표 클립이 바뀌었으면 null(다른 영상끼리 뺄 수 없다) */
  heartDelta:         number | null;
  /** 1시간 전 예상 인기점수 순위 - 현재 순위. 양수면 순위 상승 */
  rankDelta:          number | null;
  /** 1시간 전 예상 인기점수와의 차이 */
  scoreDelta:         number | null;
  isNew:              boolean;
  live:               SingcupLive | null;
}
export interface SingcupMain {
  event: { id: string; startAt: string; endAt: string; status: SingcupStatus };
  summary: {
    taggedClipCount: number; streamerCount: number; liveCount: number;
    /** 1시간 전 대비 증가분 — 기준 시각 근처에 수집 회차가 없으면 null(0이 아니다) */
    taggedClipDelta: number | null; streamerDelta: number | null;
    deltaWindowMinutes: number;
    /** 실제로 비교한 수집 회차 시각 */
    deltaBaseAt: string | null;
  };
  /** 기준 스냅샷 대비 하트 급상승 — 스트리머당 현재 대표 클립 하나만, 동일 clipUid끼리
   *  비교. 실제 비교 간격은 회차마다 다르므로 baseAt/computedAt으로 화면에 함께 적는다. */
  topHeartMovers1h: SingcupHeartMover[];
  /** true면 지금 계산한 값이 아니라 '직전 정상 집계'다. 백엔드는 이번 구간의 목록이
   *  비면 사유를 가리지 않고 이 경로를 타므로, 이 값이 뜻하는 것은 **'새 목록을
   *  계산하지 못했다'는 사실 하나뿐**이다 — 기준선 부재·대표 클립 교체·recovering
   *  제외·양수 후보 없음 등이 모두 여기로 합쳐지며 원인은 구분되지 않는다.
   *  화면에 '이전 집계' 배지로 표시하되 원인을 열거하지 말 것. */
  topHeartMovers1hStale?: boolean;
  topHeartMovers1hBaseAt?: string | null;
  topHeartMovers1hComputedAt?: string | null;
  /** 후보 계산을 **마지막으로 실제 실행한** 시각. ComputedAt과 다르다 —
   *  stale일 때 ComputedAt은 옛 집계 자신의 시각이라 멈춰 있지만 이 값은 계속
   *  전진한다. "계산이 멈춘 것"과 "계산했지만 후보가 없는 것"을 구분하는 근거다.
   *  캐시된 응답을 다시 줄 때는 함께 캐시돼 바뀌지 않는다(요청 시각이 아니다). */
  topHeartMovers1hEvaluatedAt?: string | null;
  /** 이번 평가에서 실제로 하트가 증가한 owner 수. **화면 카드 수와 다르다** —
   *  fallback 중이면 카드는 옛 결과이고 이 값은 0이다. 상위 5개로 자르기 전 값이라
   *  6명 이상이면 5보다 클 수 있다. */
  topHeartMovers1hPositiveCount?: number;
  /** 라이브 표시의 신선도. 싱드컵 수집기가 아니라 전체 라이브 스캔 주기에 묶여 있다. */
  live: {
    collectedAt: string | null; nextExpectedAt: string | null;
    intervalSeconds: number; isStale: boolean;
  };
  collector: { lastSuccessAt: string | null; stale: boolean };
  streamers: SingcupStreamer[];
}
export interface SingcupClip {
  clipUid: string; clipTitle: string; clipThumbnailUrl: string;
  heartCount: number; viewCount: number; duration: number; createdAt: string;
}
export interface SingcupStreamerClips { channelId: string; clips: SingcupClip[] }

// ── 싱드컵 분리 API (Shadow) ────────────────────────────────────────────────
// 전송량 대책으로 `/main`을 쪼갠 경로의 응답. 정렬·검색은 서버가 **전체 참가자
// 집합** 기준으로 수행한 결과이며, items는 그중 한 페이지일 뿐이다.
export interface SingcupPage {
  snapshotVersion: string;
  generatedAt: string | null;
  /** 전체 결과 수(현재 페이지 수가 아니다) */
  total: number;
  items: SingcupStreamer[];
  nextCursor: string | null;
  hasMore: boolean;
  sort: string;
  direction: string;
  /** search 응답에만 있음 */
  query?: string;
  /** live 응답에만 있음 */
  liveInfo?: SingcupMain["live"];
}
export interface SingcupSplitSummary {
  snapshotVersion: string;
  generatedAt: string | null;
  event: SingcupMain["event"];
  summary: SingcupMain["summary"];
  topHeartMovers1h: SingcupHeartMover[];
  topHeartMovers1hStale?: boolean;
  topHeartMovers1hBaseAt?: string | null;
  live: SingcupMain["live"];
  collector: SingcupMain["collector"];
  liveCount: number;
}
export interface SingcupMovers {
  snapshotVersion: string;
  generatedAt: string | null;
  range: string;
  total: number;
  items: SingcupHeartMover[];
  stale: boolean;
  baseAt: string | null;
}

// ── 싱드컵 대표 클립 수동 지정 (Nexadmin, OWNER 전용) ────────────────────────
// 화면이 세 가지를 **구분해서** 보여줘야 하므로 타입에서도 나눠 둔다.
//   autoRepresentative      자동 규칙이 골랐을 클립 (규칙은 바뀌지 않는다)
//   override                사람이 지정한 클립 (없으면 null)
//   effectiveRepresentative 실제로 적용 중인 대표 = 저장된 값
// 셋을 하나로 합치면 "지정했는데 왜 자동이 보이지"를 화면에서 설명할 수 없다.
export interface SingcupRepClip {
  clipUid: string;
  clipTitle: string;
  heartCount: number;
  viewCount: number;
  createdAt: number;
  thumbnailImageUrl: string;
}

export interface SingcupRepState {
  channelId: string;
  channelName: string;
  channelImageUrl: string;
  taggedClipCount: number;
  autoRepresentative: SingcupRepClip | null;
  effectiveRepresentative: SingcupRepClip | null;
  effectiveRepresentativeClipUid: string | null;
  override: {
    clipUid: string;
    reason: string;
    updatedAt: number;
    /** 지정한 클립이 삭제·비활성이면 행은 남되 효력을 잃는다(자동으로 복귀). */
    active: boolean;
  } | null;
  clips: SingcupRepClip[];
}

export interface SingcupRepSearchItem {
  channelId: string;
  channelName: string;
  channelImageUrl: string;
  taggedClipCount: number;
  effectiveRepresentativeClipUid: string | null;
  hasOverride: boolean;
  overrideClipUid: string | null;
}

export interface SingcupRepSearchResult {
  eventId: string;
  query: string;
  items: SingcupRepSearchItem[];
}

export interface SingcupRepPreview {
  clipUid: string;
  channelId: string;
  eligible: boolean;
  reason: string;
  reasonText: string;
  /** 이미 같은 클립이 지정돼 있다 — 적용해도 바뀌는 것이 없다. */
  noop: boolean;
  currentRepresentative: SingcupRepClip | null;
  targetClip: SingcupRepClip | null;
  /** 점수는 조회 70% + 하트 30%라 지정으로 순위가 내려갈 수 있다. */
  impact: {
    heartDelta: number;
    viewDelta: number;
    rankLikelyDrops: boolean;
  } | null;
  /** 치지직 상세 재확인. ok=null은 외부 장애(지정을 막지는 않는다). */
  liveCheck: {
    checked: boolean;
    ok: boolean | null;
    note: string;
    clipTitle?: string;
    blindType?: string;
  };
  state: SingcupRepState;
}

export interface SingcupRepApplyResult {
  ok: boolean;
  clipUid?: string;
  cleared?: boolean;
  recomputed: boolean;
  note?: string;
  effectiveRepresentativeClipUid?: string | null;
  state: SingcupRepState;
}

/**
 * 클립 지표 단건 갱신 — 대표 지정과는 **별개 동작**이다.
 *
 * `viewState`가 이 화면의 존재 이유다. 저장된 조회수 0이 '한 번도 못 읽음'인지
 * '진짜 0'인지 구분되지 않으면, 0을 앞에 두고 고장인지 정상인지 판단할 수 없다.
 */
export type SingcupMetricState =
  | "unknown"          // 한 번도 정상 수신하지 못함
  | "observed"         // 정상 수신, 값 > 0
  | "observed_zero"    // 정상 수신, 진짜 0
  | "observed_legacy"; // 컬럼 도입 이전에 수신된 값

export interface SingcupClipMetricsStored {
  clipUid: string;
  eventId: string;
  ownerChannelId: string;
  channelName: string;
  clipTitle: string;
  heartCount: number;
  viewCount: number;
  metricsOk: boolean;
  lastAttemptAt: number;
  lastHeartAt: number;
  lastViewAt: number;
  lastMetricsAt: number;
  metricsRecoveredAt: number;
  viewState: SingcupMetricState;
  heartState: SingcupMetricState;
  active: boolean;
  deletionState: string;
  blindType: string;
  isRepresentative: boolean;
  /** 이 참가자의 현재 대표 clip UID(이 클립이 아닐 수도 있다). */
  ownerRepresentativeClipUid: string | null;
  /** 수동 대표 override가 걸려 있으면 재계산 후에도 대표가 유지된다. */
  hasOverride: boolean;
  overrideClipUid: string | null;
}

export interface SingcupClipMetricsAttempt {
  attempt: number;
  ok: boolean;
  fieldsObserved: string;
  heartCount: number | null;
  viewCount: number | null;
  missingReason: string;
}

export interface SingcupClipMetricsExternal {
  ok: boolean;
  attempts: number;
  maxAttempts: number;
  heartCount: number | null;
  viewCount: number | null;
  heartOk: boolean;
  viewOk: boolean;
  partial: boolean;
  missingReason: string;
  attemptTrace: SingcupClipMetricsAttempt[];
}

export interface SingcupClipMetricsPreview {
  clipUid: string;
  stored: SingcupClipMetricsStored;
  external: SingcupClipMetricsExternal;
  pending: {
    heartCount: number;
    viewCount: number;
    heartWillChange: boolean;
    viewWillChange: boolean;
  };
  /** 갱신하면 자동 대표가 움직일 수 있는가(override가 있으면 유지된다). */
  representativeRisk: {
    hasOverride: boolean;
    overrideClipUid: string | null;
    currentRepresentativeClipUid: string | null;
    mayChangeAutoRepresentative: boolean;
  };
  note: string;
}

export interface SingcupClipMetricsApplyResult {
  ok: boolean;
  clipUid: string;
  recomputed: boolean;
  before: SingcupClipMetricsStored;
  after: SingcupClipMetricsStored;
  external: SingcupClipMetricsExternal;
  /** 자동 선정 결과가 바뀌어 대표가 이동했는가. 변경이 없으면 false. */
  autoRepresentativeChanged: boolean;
  representativeBeforeClipUid: string | null;
  representativeAfterClipUid: string | null;
  hasOverride: boolean;
  /** 하위 호환 — `!autoRepresentativeChanged`와 같다. */
  representativeUnchanged: boolean;
}

// ── 스트리머 팀/소속 태그 (TAG-1) ────────────────────────────────────────────
//
// **치지직 방송 태그(`RisingTag` 계열)와 다른 개념이다.** 그쪽은 방송마다 바뀌는
// 수집값이고, 이건 운영자가 채널에 붙이는 소속 라벨이다. 합치지 말 것.
//
// 이 블록은 **파일 맨 끝에 붙인다** — 다른 대기 중인 작업이 같은 자리(EOF)를 쓰면
// 충돌하지만, 중간에 끼워 넣는 것보다는 재배치가 쉽다.

export type TagColorMode = "solid" | "gradient";
export type TagGradientDirection =
  | "to-right" | "to-bottom-right" | "to-bottom" | "to-top-right";

/** 공개 화면이 받는 최소 필드. 운영 메타(active·시각)는 오지 않는다. */
export interface StreamerTag {
  id: number;
  name: string;
  slug: string;
  kind: string;
  colorMode: TagColorMode;
  colorStart: string;              // #RRGGBB
  colorEnd: string | null;         // gradient일 때만
  gradientDirection: TagGradientDirection;
}

/** 관리 화면(OWNER)에서만 오는 확장 필드. */
export interface StreamerTagAdmin extends StreamerTag {
  active: boolean;
  createdAt: number;
  updatedAt: number;
  assignedCount: number;
  /** 이 그룹 멤버를 **전체 스트리머 랭킹에서만** 뺄지. 운영 응답에만 있다. */
  excludeFromRanking: boolean;
}

export interface StreamerTagListResponse {
  tags: StreamerTagAdmin[];
  maxPerStreamer: number;
  gradientDirections: TagGradientDirection[];
  version: number;
}

export interface StreamerTagSearchItem {
  channelId: string;
  channelName: string | null;
  lastSeen: number;
  /** 이 스트리머가 이미 속한 소속 그룹들 — 검색 결과에서 "추가됨" 판정에 쓴다. */
  tags: StreamerTag[];
  /** 수집기 메모리 맵에서 온 프로필 이미지. 없으면 빈 문자열(외부 호출 0회). */
  channelImageUrl?: string;
}

export interface StreamerTagMutation {
  ok: boolean;
  channelId: string;
  tagId: number;
  tags: StreamerTag[];
  created?: boolean;
  removed?: boolean;
}

// ── TAG-2: 소속 그룹 멤버 관리 ───────────────────────────────────────────────
// **DB 테이블명과 공개 API 필드(`team_tags`)는 그대로다.** 사용자에게 보이는
// 한국어만 "소속 그룹"으로 바뀌었고, 내부 식별자는 tag/team 계열을 유지한다 —
// 억지로 group으로 개명하면 운영 데이터와 API 계약까지 끌고 가야 한다.

export interface GroupMember {
  channelId: string;
  channelName: string | null;
  displayOrder: number;
  channelImageUrl: string;
}

export interface GroupMemberPage {
  tagId: number;
  tag: StreamerTagAdmin | StreamerTag & { active?: boolean };
  items: GroupMember[];
  /** 하위 호환 별칭 — 서버가 `items`와 같은 배열을 함께 준다. */
  streamers: GroupMember[];
  total: number;
  limit: number;
  offset: number;
  hasMore: boolean;
}
