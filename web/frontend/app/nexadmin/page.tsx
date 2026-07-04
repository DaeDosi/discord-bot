"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import {
  Bot, Server, Radio, ShieldCheck, Search,
  Plus, X, RefreshCw, LogIn, ChevronDown, ChevronUp, LogOut,
  Megaphone, Save, CheckCircle,
} from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "";

function authHeader(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function adminFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", ...authHeader(), ...(opts?.headers ?? {}) },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "요청 실패");
  }
  return res.json();
}

// ── 타입 ──────────────────────────────────────────────────────────────────────

interface Overview {
  guild_count: number;
  total_users: number;
  chzzk_subs: number;
  verifications: number;
  today_visitors: number;
}
interface Guild { id: string; name: string; icon: string | null; chzzk_name: string | null }
interface ChzzkSub {
  id: number; guild_id: number; guild_name: string;
  chzzk_channel_id: string; chzzk_name: string; chzzk_image_url: string | null;
  discord_channel: number; mention_everyone: number; is_live: number;
  follow_months_tier1: number; follow_months_tier2: number;
}
interface VerifUser {
  guild_id: string; guild_name: string;
  user_id: string; user_name: string;
  tier_months: number;
  follow_date: string | null;
  follow_days: number;
  is_following: boolean;
  verified_at: number;
}
interface FollowUser {
  user_id: number; user_name: string; tier_months: number; verified_at: number;
}
interface FollowStat {
  sub_id: number; guild_id: number; guild_name: string | null;
  chzzk_name: string; chzzk_image_url: string | null;
  follow_months_tier1: number; follow_months_tier2: number;
  users: FollowUser[];
}
interface GuildDetail {
  id: string; name: string; icon: string | null;
  owner_id: string | null; member_count: number; description: string | null;
  chzzk: {
    chzzk_channel_id: string; chzzk_name: string; chzzk_image_url: string | null;
    discord_channel: number; notify_vod: number; notify_clip: number; notify_community: number;
    is_live: number; streamer_access_token: string | null;
  } | null;
  verif_count: number;
}
interface SearchResult {
  channelId: string; channelName: string; channelImageUrl: string | null;
  followerCount: number; openLive: boolean;
}

// ── 서브 컴포넌트 ─────────────────────────────────────────────────────────────

function StatCard({
  icon, label, sub, value, color,
}: { icon: React.ReactNode; label: string; sub?: string; value: number | null; color: string }) {
  return (
    <div className="rounded-2xl border border-border bg-bg-card p-5 flex items-center gap-4">
      <div className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0"
           style={{ background: `${color}18` }}>
        <span style={{ color }}>{icon}</span>
      </div>
      <div>
        <p className="text-2xl font-bold text-fg">
          {value === null
            ? <span className="animate-pulse text-muted text-lg">집계 중...</span>
            : value.toLocaleString()}
        </p>
        <p className="text-xs text-muted mt-0.5">{label}</p>
        {sub && <p className="text-[10px] text-muted/60 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

function GuildIcon({ guild }: { guild: Guild }) {
  if (!guild.icon) {
    return (
      <div className="w-8 h-8 rounded-full bg-bg-hover flex items-center justify-center shrink-0">
        <Server size={14} className="text-muted" />
      </div>
    );
  }
  return (
    <Image
      src={`https://cdn.discordapp.com/icons/${guild.id}/${guild.icon}.png?size=64`}
      alt={guild.name} width={32} height={32} className="rounded-full shrink-0"
    />
  );
}

function AddChzzkModal({
  guilds, onClose, onAdded,
}: { guilds: Guild[]; onClose: () => void; onAdded: () => void }) {
  const [keyword, setKeyword]       = useState("");
  const [results, setResults]       = useState<SearchResult[]>([]);
  const [searching, setSearching]   = useState(false);
  const [selected, setSelected]     = useState<SearchResult | null>(null);
  const [guildId, setGuildId]       = useState("");
  const [channelId, setChannelId]   = useState("");
  const [mentionAll, setMentionAll] = useState(false);
  const [saving, setSaving]         = useState(false);
  const [error, setError]           = useState("");
  const debounce = useRef<NodeJS.Timeout | null>(null);

  const search = (kw: string) => {
    setKeyword(kw);
    if (!kw.trim()) { setResults([]); return; }
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(async () => {
      setSearching(true);
      const res = await adminFetch<SearchResult[]>(
        `/api/admin/chzzk/search?keyword=${encodeURIComponent(kw)}`
      ).catch(() => []);
      setResults(res);
      setSearching(false);
    }, 400);
  };

  const submit = async () => {
    if (!selected || !guildId || !channelId) return;
    setSaving(true); setError("");
    try {
      await adminFetch("/api/admin/chzzk", {
        method: "POST",
        body: JSON.stringify({
          guild_id: guildId, discord_channel: channelId,
          chzzk_channel_id: selected.channelId, chzzk_name: selected.channelName,
          chzzk_image_url: selected.channelImageUrl, mention_everyone: mentionAll,
        }),
      });
      onAdded(); onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "오류 발생");
    } finally { setSaving(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         onClick={onClose}>
      <div className="bg-bg-card border border-border rounded-2xl w-full max-w-lg shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h3 className="font-semibold text-fg">치지직 구독 추가</h3>
          <button onClick={onClose} className="text-muted hover:text-fg"><X size={18} /></button>
        </div>
        <div className="p-4 space-y-4">
          {!selected ? (
            <>
              <div className="relative">
                <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input className="input pl-9" placeholder="스트리머 이름 검색..."
                       value={keyword} onChange={(e) => search(e.target.value)} autoFocus />
              </div>
              <div className="space-y-1 max-h-56 overflow-y-auto">
                {searching && <p className="text-muted text-sm text-center py-3">검색 중...</p>}
                {!searching && results.map((r) => (
                  <button key={r.channelId} onClick={() => setSelected(r)}
                          className="w-full flex items-center gap-3 p-2.5 rounded-lg hover:bg-bg-hover text-left">
                    {r.channelImageUrl
                      ? <Image src={r.channelImageUrl} alt={r.channelName} width={36} height={36} className="rounded-full shrink-0" />
                      : <div className="w-9 h-9 rounded-full bg-bg-hover shrink-0" />}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-fg truncate">{r.channelName}</p>
                      <p className="text-xs text-muted">팔로워 {r.followerCount.toLocaleString()}명</p>
                    </div>
                    {r.openLive && (
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                            style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>LIVE</span>
                    )}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-3 p-3 bg-bg rounded-lg border border-border">
                {selected.channelImageUrl
                  ? <Image src={selected.channelImageUrl} alt={selected.channelName} width={40} height={40} className="rounded-full" />
                  : <div className="w-10 h-10 rounded-full bg-bg-hover" />}
                <div className="flex-1">
                  <p className="font-medium text-fg">{selected.channelName}</p>
                  <p className="text-xs text-muted">팔로워 {selected.followerCount.toLocaleString()}명</p>
                </div>
                <button onClick={() => setSelected(null)} className="text-muted hover:text-fg"><X size={14} /></button>
              </div>
              <div>
                <label className="label">대상 서버</label>
                <select className="select" value={guildId} onChange={(e) => setGuildId(e.target.value)}>
                  <option value="">서버 선택...</option>
                  {guilds.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                </select>
              </div>
              <div>
                <label className="label">알림 채널 ID</label>
                <input className="input font-mono" placeholder="Discord 채널 ID (숫자)"
                       value={channelId} onChange={(e) => setChannelId(e.target.value)} />
                <p className="text-xs text-muted mt-1">채널 우클릭 → ID 복사</p>
              </div>
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input type="checkbox" checked={mentionAll}
                       onChange={(e) => setMentionAll(e.target.checked)}
                       className="w-4 h-4 rounded accent-accent" />
                <span className="text-sm text-fg">@everyone 멘션</span>
              </label>
              {error && <p className="text-danger text-sm">{error}</p>}
              <button onClick={submit} disabled={saving || !guildId || !channelId}
                      className="btn-primary w-full justify-center">
                <Plus size={15} /> {saving ? "추가 중..." : "구독 추가"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── 서버 상세 모달 ────────────────────────────────────────────────────────────
function GuildDetailModal({ guildId, onClose, onLeft }: { guildId: string; onClose: () => void; onLeft: () => void }) {
  const [detail, setDetail] = useState<GuildDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    adminFetch<GuildDetail>(`/api/admin/guilds/${guildId}`)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setLoading(false));
  }, [guildId]);

  const leave = async () => {
    if (!detail) return;
    if (!confirm(`정말 "${detail.name}" 서버에서 나가시겠습니까?\n이 작업은 되돌릴 수 없습니다.`)) return;
    setLeaving(true);
    try {
      await adminFetch(`/api/admin/guilds/${guildId}/leave`, { method: "DELETE" });
      onLeft();
      onClose();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "서버 나가기 실패");
      setLeaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-bg-card border border-border rounded-2xl w-full max-w-md shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h3 className="font-semibold text-fg text-sm">서버 상세 정보</h3>
          <button onClick={onClose} className="text-muted hover:text-fg"><X size={16} /></button>
        </div>

        {loading ? (
          <p className="px-4 py-10 text-center text-muted animate-pulse">불러오는 중...</p>
        ) : !detail ? (
          <p className="px-4 py-10 text-center text-danger text-sm">정보를 가져올 수 없습니다.</p>
        ) : (
          <div className="p-4 space-y-4">
            {/* 서버 헤더 */}
            <div className="flex items-center gap-3">
              {detail.icon
                ? <Image src={`https://cdn.discordapp.com/icons/${detail.id}/${detail.icon}.png?size=128`} alt={detail.name} width={56} height={56} className="rounded-2xl shrink-0" />
                : <div className="w-14 h-14 rounded-2xl bg-bg-hover flex items-center justify-center shrink-0"><Server size={22} className="text-muted" /></div>}
              <div>
                <p className="font-bold text-fg text-base">{detail.name}</p>
                {detail.description && <p className="text-xs text-muted mt-0.5 line-clamp-2">{detail.description}</p>}
              </div>
            </div>

            {/* 기본 정보 */}
            <div className="rounded-lg border border-border divide-y divide-border text-sm">
              <div className="flex justify-between px-3 py-2">
                <span className="text-muted">서버 ID</span>
                <span className="font-mono text-xs text-fg select-all">{detail.id}</span>
              </div>
              <div className="flex justify-between px-3 py-2">
                <span className="text-muted">멤버 수</span>
                <span className="text-fg font-medium">{detail.member_count.toLocaleString()}명</span>
              </div>
              <div className="flex justify-between px-3 py-2">
                <span className="text-muted">치지직 인증</span>
                <span className="text-fg font-medium">{detail.verif_count}명</span>
              </div>
              {detail.owner_id && (
                <div className="flex justify-between px-3 py-2">
                  <span className="text-muted">오너 ID</span>
                  <span className="font-mono text-xs text-fg select-all">{detail.owner_id}</span>
                </div>
              )}
            </div>

            {/* 치지직 구독 */}
            {detail.chzzk && (
              <div className="rounded-lg border border-chzzk/20 bg-chzzk/5 p-3 space-y-1.5 text-sm">
                <p className="font-semibold text-chzzk text-xs uppercase tracking-wide mb-2">치지직 구독</p>
                <div className="flex items-center gap-2">
                  {detail.chzzk.chzzk_image_url
                    ? <Image src={detail.chzzk.chzzk_image_url} alt={detail.chzzk.chzzk_name} width={28} height={28} className="rounded-full" />
                    : <div className="w-7 h-7 rounded-full bg-bg-hover" />}
                  <span className="font-medium text-fg">{detail.chzzk.chzzk_name}</span>
                  {detail.chzzk.is_live ? (
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>LIVE</span>
                  ) : null}
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs mt-1">
                  <span className="text-muted">VOD 알림</span><span className={detail.chzzk.notify_vod ? "text-accent" : "text-muted"}>{detail.chzzk.notify_vod ? "ON" : "OFF"}</span>
                  <span className="text-muted">클립 알림</span><span className={detail.chzzk.notify_clip ? "text-accent" : "text-muted"}>{detail.chzzk.notify_clip ? "ON" : "OFF"}</span>
                  <span className="text-muted">커뮤니티 알림</span><span className={detail.chzzk.notify_community ? "text-accent" : "text-muted"}>{detail.chzzk.notify_community ? "ON" : "OFF"}</span>
                  <span className="text-muted">스트리머 연동</span><span className={detail.chzzk.streamer_access_token ? "text-accent" : "text-muted"}>{detail.chzzk.streamer_access_token ? "연동됨" : "미연동"}</span>
                </div>
              </div>
            )}

            {/* 나가기 버튼 */}
            <button onClick={leave} disabled={leaving}
                    className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-danger/10 hover:bg-danger/20 text-danger border border-danger/30 transition-colors disabled:opacity-50">
              <LogOut size={14} /> {leaving ? "나가는 중..." : "이 서버에서 나가기"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── 서버 나가기 버튼 ──────────────────────────────────────────────────────────
function LeaveGuildButton({ guildId, guildName, onLeft }: { guildId: string; guildName: string; onLeft: () => void }) {
  const [leaving, setLeaving] = useState(false);

  const leave = async () => {
    if (!confirm(`정말 "${guildName}" 서버에서 나가시겠습니까?\n이 작업은 되돌릴 수 없습니다.`)) return;
    setLeaving(true);
    try {
      await adminFetch(`/api/admin/guilds/${guildId}/leave`, { method: "DELETE" });
      onLeft();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "서버 나가기 실패");
    } finally { setLeaving(false); }
  };

  return (
    <button
      onClick={(e) => { e.stopPropagation(); leave(); }}
      disabled={leaving}
      title="서버에서 나가기"
      className="p-1.5 rounded-lg text-muted hover:text-danger hover:bg-danger/10 transition-colors disabled:opacity-40"
    >
      <LogOut size={14} />
    </button>
  );
}

// ── 팔로우 현황 카드 ──────────────────────────────────────────────────────────
function FollowStatCard({ stat, onSubDeleted }: { stat: FollowStat; onSubDeleted: (subId: number) => void }) {
  const [expanded, setExpanded] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const t1 = stat.follow_months_tier1;
  const t2 = stat.follow_months_tier2;
  const guildName = stat.guild_name ?? `(알 수 없는 서버 ${stat.guild_id})`;
  const isGone = !stat.guild_name;

  const deleteSub = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`"${guildName}" 서버의 치지직 구독(${stat.chzzk_name})을 삭제하시겠습니까?`)) return;
    setDeleting(true);
    try {
      await adminFetch(`/api/admin/chzzk/${stat.sub_id}`, { method: "DELETE" });
      onSubDeleted(stat.sub_id);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "삭제 실패");
      setDeleting(false);
    }
  };

  return (
    <div className={`rounded-2xl border overflow-hidden ${isGone ? "border-danger/30" : "border-border"}`}>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 bg-bg-card/60 hover:bg-bg-hover/40 transition-colors text-left"
      >
        {stat.chzzk_image_url
          ? <Image src={stat.chzzk_image_url} alt={stat.chzzk_name} width={36} height={36} className="rounded-full shrink-0" />
          : <div className="w-9 h-9 rounded-full bg-bg-hover shrink-0 flex items-center justify-center"><Radio size={14} className="text-muted" /></div>}
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-fg text-sm">{stat.chzzk_name}</p>
          <p className={`text-xs ${isGone ? "text-danger/70" : "text-muted"}`}>
            {guildName} <span className="font-mono">({stat.guild_id})</span>
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-muted">{stat.users.length}명 인증</span>
          <span className="text-xs text-muted/60">기준: {t1}개월 / {t2}개월</span>
          <button onClick={deleteSub} disabled={deleting}
                  title="팔로우 데이터 삭제"
                  className="p-1.5 rounded-lg text-danger hover:bg-danger/10 transition-colors disabled:opacity-40">
            <X size={14} />
          </button>
          {expanded ? <ChevronUp size={16} className="text-muted" /> : <ChevronDown size={16} className="text-muted" />}
        </div>
      </button>

      {expanded && (
        stat.users.length === 0 ? (
          <p className="px-4 py-6 text-sm text-muted text-center">인증한 유저가 없습니다.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-t border-border bg-bg/60">
                <th className="text-left px-4 py-2 text-muted font-medium text-xs">유저명</th>
                <th className="text-left px-4 py-2 text-muted font-medium text-xs hidden md:table-cell">유저 ID</th>
                <th className="text-left px-4 py-2 text-muted font-medium text-xs">구독 개월</th>
                <th className="text-left px-4 py-2 text-muted font-medium text-xs hidden md:table-cell">인증 일시</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {stat.users.map((u) => (
                <tr key={u.user_id} className="hover:bg-bg-hover/30 transition-colors">
                  <td className="px-4 py-2.5 text-fg font-medium">{u.user_name}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted hidden md:table-cell">{u.user_id}</td>
                  <td className="px-4 py-2.5">
                    <span className={`text-sm font-bold ${
                      u.tier_months >= t2 ? "text-chzzk" :
                      u.tier_months >= t1 ? "text-warning" : "text-muted"
                    }`}>
                      {u.tier_months}개월
                    </span>
                    {u.tier_months >= t2 && <span className="ml-1 text-[10px] text-chzzk/70">({t2}개월+ 티어)</span>}
                    {u.tier_months >= t1 && u.tier_months < t2 && <span className="ml-1 text-[10px] text-warning/70">({t1}개월+ 티어)</span>}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted hidden md:table-cell">
                    {new Date(u.verified_at * 1000).toLocaleString("ko-KR")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}
    </div>
  );
}

// ── 인증 상세/삭제 모달 ───────────────────────────────────────────────────────
function VerifDetailModal({
  verif, onClose, onDeleted,
}: { verif: VerifUser; onClose: () => void; onDeleted: () => void }) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError]       = useState("");

  const del = async () => {
    if (!confirm(`${verif.user_name} (${verif.user_id})의 인증 기록을 삭제하시겠습니까?`)) return;
    setDeleting(true); setError("");
    try {
      await adminFetch(`/api/admin/verifications/${verif.guild_id}/${verif.user_id}`, { method: "DELETE" });
      onDeleted(); onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "삭제 실패");
    } finally { setDeleting(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         onClick={onClose}>
      <div className="bg-bg-card border border-border rounded-2xl w-full max-w-sm shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h3 className="font-semibold text-fg text-sm">인증 상세 정보</h3>
          <button onClick={onClose} className="text-muted hover:text-fg"><X size={16} /></button>
        </div>
        <div className="p-4 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            <span className="text-muted">유저명</span>
            <span className="text-fg font-medium">{verif.user_name}</span>
            <span className="text-muted">유저 ID</span>
            <span className="font-mono text-xs text-fg select-all">{verif.user_id}</span>
            <span className="text-muted">서버</span>
            <span className="text-fg">{verif.guild_name}</span>
            <span className="text-muted">서버 ID</span>
            <span className="font-mono text-xs text-fg select-all">{verif.guild_id}</span>
            <span className="text-muted">팔로우 여부</span>
            <span className={verif.is_following ? "text-accent font-semibold" : "text-danger font-semibold"}>
              {verif.is_following
                ? `팔로우 중 (${verif.follow_days}일 / ${verif.tier_months}개월)`
                : "팔로우 안 함"}
            </span>
            <span className="text-muted">인증 일시</span>
            <span className="text-fg text-xs">{new Date(verif.verified_at * 1000).toLocaleString("ko-KR")}</span>
          </div>
          {error && <p className="text-danger text-xs">{error}</p>}
          <button onClick={del} disabled={deleting}
                  className="w-full mt-2 px-3 py-2 rounded-lg text-sm font-medium bg-danger/10 hover:bg-danger/20 text-danger border border-danger/30 transition-colors disabled:opacity-50">
            {deleting ? "삭제 중..." : "인증 기록 삭제"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 공지 관리 패널 ────────────────────────────────────────────────────────────
function AnnouncementPanel() {
  const [message, setMessage]   = useState("");
  const [loading, setLoading]   = useState(true);
  const [saving, setSaving]     = useState(false);
  const [saved, setSaved]       = useState(false);
  const [error, setError]       = useState("");

  useEffect(() => {
    adminFetch<{ message: string }>("/api/admin/announcement")
      .then((d) => setMessage(d.message))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const save = async (nextMessage: string) => {
    setSaving(true); setError("");
    try {
      await adminFetch("/api/admin/announcement", {
        method: "PUT",
        body: JSON.stringify({ message: nextMessage }),
      });
      setMessage(nextMessage);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "저장 실패");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <p className="text-muted text-sm animate-pulse">불러오는 중...</p>;
  }

  return (
    <div className="max-w-xl space-y-4">
      <p className="text-sm text-muted">
        메인 페이지 상단에 <span className="text-fg font-medium">&quot;공지: 내용&quot;</span> 형태로 표시됩니다.
        비워두고 저장하면 배너가 사라집니다. 방문자는 X 버튼으로 각자 닫을 수 있습니다.
      </p>
      <div>
        <label className="label">공지 내용 (한 문장, 최대 200자)</label>
        <textarea
          className="input min-h-[80px] resize-y"
          placeholder="예: 7/10 새벽 2시~3시 서버 점검이 있습니다."
          value={message}
          maxLength={200}
          onChange={(e) => setMessage(e.target.value)}
        />
        <p className="text-xs text-muted mt-1">{message.length}/200자</p>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      <div className="flex items-center gap-2">
        <button onClick={() => save(message)} disabled={saving} className="btn-primary">
          {saved ? <><CheckCircle size={16} /> 저장됨</> : <><Save size={16} /> {saving ? "저장 중..." : "공지 저장"}</>}
        </button>
        {message && (
          <button onClick={() => save("")} disabled={saving} className="btn-secondary">
            <X size={16} /> 공지 지우기
          </button>
        )}
      </div>
    </div>
  );
}

// ── 메인 페이지 ───────────────────────────────────────────────────────────────

export default function AdminPage() {
  const [authed, setAuthed]           = useState<boolean | null>(null);
  const [overview, setOverview]       = useState<Overview | null>(null);
  const [guilds, setGuilds]           = useState<Guild[]>([]);
  const [chzzk, setChzzk]            = useState<ChzzkSub[]>([]);
  const [followStats, setFollowStats] = useState<FollowStat[] | null>(null);
  const [verifUsers, setVerifUsers]   = useState<VerifUser[] | null>(null);
  const [loading, setLoading]         = useState(true);
  const [showAdd, setShowAdd]         = useState(false);
  const [activeTab, setActiveTab]     = useState<"guilds" | "verif" | "follow" | "announcement">("guilds");
  const [refreshing, setRefreshing]   = useState(false);
  const [selectedVerif, setSelectedVerif] = useState<VerifUser | null>(null);
  const [selectedGuildId, setSelectedGuildId] = useState<string | null>(null);

  const leaveGuild = useCallback((guildId: string) => {
    setGuilds((prev) => prev.filter((g) => g.id !== guildId));
    setFollowStats((prev) => prev ? prev.filter((s) => String(s.guild_id) !== guildId) : prev);
  }, []);

  const deleteFollowSub = useCallback((subId: number) => {
    setFollowStats((prev) => prev ? prev.filter((s) => s.sub_id !== subId) : prev);
  }, []);

  const loadAll = async () => {
    setRefreshing(true);

    // ── Phase 1: 빠른 데이터 (캐시된 봇 서버 목록 기반) ──
    try {
      const [gl, ch] = await Promise.all([
        adminFetch<Guild[]>("/api/admin/guilds"),
        adminFetch<ChzzkSub[]>("/api/admin/chzzk"),
      ]);
      setGuilds(gl);
      setChzzk(ch);
      setAuthed(true);
    } catch (e: unknown) {
      if (e instanceof Error && e.message.includes("접근")) setAuthed(false);
      setLoading(false);
      setRefreshing(false);
      return;
    } finally {
      setLoading(false);  // Phase 1 끝 → UI 즉시 표시
    }

    // ── Phase 2: 느린 데이터 (Discord API 멤버 조회 포함) ──
    try {
      const [ov, fs, vu] = await Promise.all([
        adminFetch<Overview>("/api/admin/overview"),
        adminFetch<FollowStat[]>("/api/admin/follow-stats"),
        adminFetch<VerifUser[]>("/api/admin/verifications"),
      ]);
      setOverview(ov);
      setFollowStats(fs);
      setVerifUsers(vu);
    } catch {
      // 통계/팔로우 로드 실패해도 서버 목록은 이미 표시됨
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { setAuthed(false); setLoading(false); return; }
    loadAll();
  }, []);

  // 서버별로 그룹핑 — 여러 서버 유저가 한 줄로 뒤섞여 스캔하기 어려운 문제를 해소.
  // verifUsers는 verified_at DESC로 오므로, Map 삽입 순서상 가장 최근 활동이 있던 서버가 먼저 나온다.
  // 훅은 항상 최상단에서, 조건부 return보다 먼저 호출되어야 하므로 로딩/권한 분기 이전에 둔다.
  const verifByGuild = useMemo(() => {
    if (!verifUsers) return [];
    const map = new Map<string, { guildName: string; users: VerifUser[] }>();
    for (const v of verifUsers) {
      if (!map.has(v.guild_id)) map.set(v.guild_id, { guildName: v.guild_name, users: [] });
      map.get(v.guild_id)!.users.push(v);
    }
    return Array.from(map.values());
  }, [verifUsers]);

  if (loading) {
    return (
      <div className="min-h-screen bg-bg flex items-center justify-center">
        <div className="text-muted animate-pulse">로딩 중...</div>
      </div>
    );
  }

  if (authed === false) {
    return (
      <div className="min-h-screen bg-bg flex flex-col items-center justify-center gap-4">
        <ShieldCheck size={48} className="text-muted" />
        <p className="text-fg font-semibold text-lg">접근 권한이 없습니다.</p>
        <p className="text-muted text-sm">봇 오너 계정으로 로그인한 후 접근하세요.</p>
        <a href="/" className="text-accent text-sm hover:underline flex items-center gap-1">
          <LogIn size={14} /> 홈으로
        </a>
      </div>
    );
  }

  const followCount = followStats?.reduce((s, f) => s + f.users.length, 0) ?? null;
  const verifCount  = verifUsers?.length ?? null;

  const tabs = [
    { key: "guilds", label: `서버 목록 (${guilds.length})` },
    { key: "verif",  label: verifUsers === null ? "인증 현황 (로딩 중...)" : `인증 현황 (${verifCount}명)` },
    { key: "follow", label: followStats === null ? "팔로우 관리 (로딩 중...)" : `팔로우 관리 (${followCount}명)` },
    { key: "announcement", label: "공지 관리" },
  ] as const;

  return (
    <div className="min-h-screen bg-bg text-fg">
      {showAdd && (
        <AddChzzkModal guilds={guilds} onClose={() => setShowAdd(false)} onAdded={loadAll} />
      )}
      {selectedGuildId && (
        <GuildDetailModal
          guildId={selectedGuildId}
          onClose={() => setSelectedGuildId(null)}
          onLeft={() => { leaveGuild(selectedGuildId); setSelectedGuildId(null); }}
        />
      )}
      {selectedVerif && (
        <VerifDetailModal
          verif={selectedVerif}
          onClose={() => setSelectedVerif(null)}
          onDeleted={() => {
            setVerifUsers((prev) => prev
              ? prev.filter((v) => !(v.guild_id === selectedVerif.guild_id && v.user_id === selectedVerif.user_id))
              : prev
            );
            setSelectedVerif(null);
          }}
        />
      )}

      <header className="border-b border-border bg-bg-card/60 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-5 flex items-center justify-between" style={{ height: 56 }}>
          <div className="flex items-center gap-2">
            <Bot size={18} className="text-accent" />
            <span className="font-bold text-fg">NexBot 관리자 패널</span>
            <span className="ml-2 text-[10px] px-2 py-0.5 rounded-full bg-danger/10 text-danger border border-danger/20 font-semibold">
              OWNER ONLY
            </span>
          </div>
          <button onClick={loadAll} disabled={refreshing}
                  className="flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors">
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            새로고침
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-5 py-8 space-y-8">

        {/* 통계 카드 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard icon={<Server size={22} />}      label="등록 서버"   value={overview?.guild_count    ?? null} color="#5865F2" />
          <StatCard icon={<Radio size={22} />}       label="치지직 구독" value={overview?.chzzk_subs    ?? null} color="#03C75A" />
          <StatCard icon={<ShieldCheck size={22} />} label="치지직 인증" value={overview?.verifications ?? null} color="#EB459E" />
          <StatCard icon={<Bot size={22} />}         label="오늘 방문자" value={overview?.today_visitors ?? null} color="#FEE75C" />
        </div>

        {/* 탭 */}
        <div className="flex gap-1 border-b border-border">
          {tabs.map((t) => (
            <button key={t.key} onClick={() => setActiveTab(t.key)}
                    className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                      activeTab === t.key
                        ? "border-accent text-accent"
                        : "border-transparent text-muted hover:text-fg"
                    }`}>
              {t.label}
            </button>
          ))}
        </div>

        {/* ── 서버 목록 ── */}
        {activeTab === "guilds" && (
          <div className="rounded-2xl border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-bg-card/60">
                  <th className="text-left px-4 py-3 text-muted font-medium w-8">#</th>
                  <th className="text-left px-4 py-3 text-muted font-medium">서버명</th>
                  <th className="text-left px-4 py-3 text-muted font-medium">서버 ID</th>
                  <th className="text-left px-4 py-3 text-muted font-medium">치지직</th>
                  <th className="px-4 py-3 w-10"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {guilds.map((g, i) => (
                  <tr key={g.id}
                      onClick={() => setSelectedGuildId(g.id)}
                      className="hover:bg-bg-hover/40 transition-colors cursor-pointer">
                    <td className="px-4 py-3 text-muted text-xs">{i + 1}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <GuildIcon guild={g} />
                        <span className="font-medium text-fg">{g.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted select-all">{g.id}</td>
                    <td className="px-4 py-3">
                      {g.chzzk_name
                        ? <span className="text-xs px-2 py-0.5 rounded-full bg-chzzk/10 text-chzzk border border-chzzk/20">{g.chzzk_name}</span>
                        : <span className="text-xs text-muted">—</span>}
                    </td>
                    <td className="px-2 py-3 text-right">
                      <LeaveGuildButton guildId={g.id} guildName={g.name} onLeft={() => leaveGuild(g.id)} />
                    </td>
                  </tr>
                ))}
                {guilds.length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-8 text-center text-muted">서버 없음</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* ── 인증 현황 ── */}
        {activeTab === "verif" && (
          <div className="space-y-4">
            <div className="flex justify-end">
              <button onClick={() => setShowAdd(true)} className="btn-primary">
                <Plus size={15} /> 스트리머 등록
              </button>
            </div>
            <div className="rounded-2xl border border-border overflow-hidden">
              {verifUsers === null ? (
                <p className="px-4 py-12 text-center text-muted animate-pulse">불러오는 중...</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border bg-bg-card/60">
                      <th className="text-left px-4 py-3 text-muted font-medium">유저명</th>
                      <th className="text-left px-4 py-3 text-muted font-medium hidden md:table-cell">유저 ID</th>
                      <th className="text-left px-4 py-3 text-muted font-medium hidden lg:table-cell">인증 일시</th>
                    </tr>
                  </thead>
                  {verifUsers.length === 0 ? (
                    <tbody>
                      <tr><td colSpan={3} className="px-4 py-8 text-center text-muted">인증한 유저 없음</td></tr>
                    </tbody>
                  ) : (
                    verifByGuild.map(({ guildName, users }) => (
                      <tbody key={users[0].guild_id} className="divide-y divide-border">
                        <tr className="bg-bg-hover/40">
                          <td colSpan={3} className="px-4 py-2 text-xs font-semibold text-muted uppercase tracking-wider">
                            {guildName ?? `서버 ${users[0].guild_id}`} · {users.length}명
                          </td>
                        </tr>
                        {users.map((v) => (
                          <tr
                            key={`${v.guild_id}-${v.user_id}`}
                            onClick={() => setSelectedVerif(v)}
                            className="hover:bg-bg-hover/40 transition-colors cursor-pointer"
                          >
                            <td className="px-4 py-3 text-fg font-medium">{v.user_name}</td>
                            <td className="px-4 py-3 font-mono text-xs text-muted select-all hidden md:table-cell">{v.user_id}</td>
                            <td className="px-4 py-3 text-xs text-muted hidden lg:table-cell">
                              {new Date(v.verified_at * 1000).toLocaleString("ko-KR")}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    ))
                  )}
                </table>
              )}
            </div>
          </div>
        )}

        {/* ── 팔로우 관리 ── */}
        {activeTab === "follow" && (
          <div className="space-y-4">
            <p className="text-sm text-muted">
              서버별 등록된 스트리머와 치지직 인증을 완료한 유저의 팔로우 개월 수입니다.
              티어 설정은 각 서버의 <strong className="text-fg">웹 대시보드 → 치지직</strong>에서 변경하세요.
            </p>

            {followStats === null ? (
              <div className="rounded-2xl border border-border px-4 py-12 text-center text-muted animate-pulse">
                팔로우 데이터 로딩 중...
              </div>
            ) : followStats.length === 0 ? (
              <div className="rounded-2xl border border-border px-4 py-12 text-center text-muted">
                치지직 구독이 등록된 서버가 없습니다.
              </div>
            ) : (
              <div className="space-y-3">
                {followStats.map((stat) => (
                  <FollowStatCard key={stat.sub_id} stat={stat}
                    onSubDeleted={deleteFollowSub} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── 공지 관리 ── */}
        {activeTab === "announcement" && (
          <div className="space-y-4">
            <p className="text-sm text-muted flex items-center gap-2">
              <Megaphone size={15} className="text-accent" /> 메인 홈페이지 상단 공지 배너를 설정합니다.
            </p>
            <AnnouncementPanel />
          </div>
        )}

      </main>
    </div>
  );
}
