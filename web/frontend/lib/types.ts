export interface User {
  id:       string;
  username: string;
  avatar:   string;
}

export interface Guild {
  id:      string;
  name:    string;
  icon:    string | null;
  has_bot: boolean;
}

export interface GuildConfig {
  mod_role_id?:     string | null;
  welcome_channel?: string | null;
  goodbye_channel?: string | null;
  log_channel?:     string | null;
  auto_role_id?:    string | null;
  levelup_channel?: string | null;
  levelup_dm?:      boolean;
  automod_enabled?: boolean;
  badwords?:        string;
}

export interface Channel {
  id:   string;
  name: string;
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
  id:               number;
  discord_channel:  number;
  chzzk_channel_id: string;
  chzzk_name:       string;
  chzzk_image_url:  string | null;
  is_live:          number;
  mention_role_id:  number | null;
  custom_message:   string | null;
}

export interface ChzzkSearchResult {
  channelId:       string;
  channelName:     string;
  channelImageUrl: string | null;
  followerCount:   number;
  openLive:        boolean;
}
