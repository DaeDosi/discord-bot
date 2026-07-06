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
