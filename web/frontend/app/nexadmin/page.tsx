"use client";
import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import {
  Bot, Server, Users, Radio, ShieldCheck, Search,
  Trash2, Plus, X, RefreshCw, LogIn,
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
}
interface Verification {
  guild_id: number; user_id: number; tier_months: number; verified_at: number;
}
interface SearchResult {
  channelId: string; channelName: string; channelImageUrl: string | null;
  followerCount: number; openLive: boolean;
}

// ── 서브 컴포넌트 ─────────────────────────────────────────────────────────────

function StatCard({
  icon, label, value, color,
}: { icon: React.ReactNode; label: string; value: number | null; color: string }) {
  return (
    <div className="rounded-2xl border border-border bg-bg-card p-5 flex items-center gap-4">
      <div className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0"
           style={{ background: `${color}18` }}>
        <span style={{ color }}>{icon}</span>
      </div>
      <div>
        <p className="text-2xl font-bold text-white">
          {value === null ? <span className="animate-pulse text-muted">—</span> : value.toLocaleString()}
        </p>
        <p className="text-xs text-muted mt-0.5">{label}</p>
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
      alt={guild.name}
      width={32} height={32}
      className="rounded-full shrink-0"
    />
  );
}

function AddChzzkModal({
  guilds,
  onClose,
  onAdded,
}: {
  guilds: Guild[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const [keyword, setKeyword]         = useState("");
  const [results, setResults]         = useState<SearchResult[]>([]);
  const [searching, setSearching]     = useState(false);
  const [selected, setSelected]       = useState<SearchResult | null>(null);
  const [guildId, setGuildId]         = useState("");
  const [channelId, setChannelId]     = useState("");
  const [mentionAll, setMentionAll]   = useState(false);
  const [saving, setSaving]           = useState(false);
  const [error, setError]             = useState("");
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
          guild_id:         guildId,
          discord_channel:  channelId,
          chzzk_channel_id: selected.channelId,
          chzzk_name:       selected.channelName,
          chzzk_image_url:  selected.channelImageUrl,
          mention_everyone: mentionAll,
        }),
      });
      onAdded();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "오류 발생");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         onClick={onClose}>
      <div className="bg-bg-card border border-border rounded-2xl w-full max-w-lg shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h3 className="font-semibold text-white">치지직 구독 추가 (관리자)</h3>
          <button onClick={onClose} className="text-muted hover:text-white"><X size={18} /></button>
        </div>
        <div className="p-4 space-y-4">
          {/* 스트리머 검색 */}
          {!selected ? (
            <>
              <div className="relative">
                <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  className="input pl-9"
                  placeholder="스트리머 이름 검색..."
                  value={keyword}
                  onChange={(e) => search(e.target.value)}
                  autoFocus
                />
              </div>
              <div className="space-y-1 max-h-56 overflow-y-auto">
                {searching && <p className="text-muted text-sm text-center py-3">검색 중...</p>}
                {!searching && results.map((r) => (
                  <button key={r.channelId}
                    onClick={() => setSelected(r)}
                    className="w-full flex items-center gap-3 p-2.5 rounded-lg hover:bg-bg-hover text-left">
                    {r.channelImageUrl
                      ? <Image src={r.channelImageUrl} alt={r.channelName} width={36} height={36} className="rounded-full shrink-0" />
                      : <div className="w-9 h-9 rounded-full bg-bg-hover shrink-0" />}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white truncate">{r.channelName}</p>
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
                  <p className="font-medium text-white">{selected.channelName}</p>
                  <p className="text-xs text-muted">팔로워 {selected.followerCount.toLocaleString()}명</p>
                </div>
                <button onClick={() => setSelected(null)} className="text-muted hover:text-white">
                  <X size={14} />
                </button>
              </div>

              <div>
                <label className="label">대상 서버</label>
                <select className="select" value={guildId} onChange={(e) => setGuildId(e.target.value)}>
                  <option value="">서버 선택...</option>
                  {guilds.map((g) => (
                    <option key={g.id} value={g.id}>{g.name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="label">알림 채널 ID</label>
                <input
                  className="input font-mono"
                  placeholder="Discord 채널 ID (숫자)"
                  value={channelId}
                  onChange={(e) => setChannelId(e.target.value)}
                />
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

// ── 메인 페이지 ───────────────────────────────────────────────────────────────

export default function AdminPage() {
  const [authed, setAuthed]           = useState<boolean | null>(null);
  const [overview, setOverview]       = useState<Overview | null>(null);
  const [guilds, setGuilds]           = useState<Guild[]>([]);
  const [chzzk, setChzzk]            = useState<ChzzkSub[]>([]);
  const [verifs, setVerifs]           = useState<Verification[]>([]);
  const [loading, setLoading]         = useState(true);
  const [showAdd, setShowAdd]         = useState(false);
  const [activeTab, setActiveTab]     = useState<"guilds" | "chzzk" | "verifs">("guilds");
  const [refreshing, setRefreshing]   = useState(false);

  const loadAll = async () => {
    setRefreshing(true);
    try {
      const [ov, gl, ch, vf] = await Promise.all([
        adminFetch<Overview>("/api/admin/overview"),
        adminFetch<Guild[]>("/api/admin/guilds"),
        adminFetch<ChzzkSub[]>("/api/admin/chzzk"),
        adminFetch<Verification[]>("/api/admin/verifications"),
      ]);
      setOverview(ov); setGuilds(gl); setChzzk(ch); setVerifs(vf);
      setAuthed(true);
    } catch (e: unknown) {
      if (e instanceof Error && e.message.includes("접근")) {
        setAuthed(false);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) { setAuthed(false); setLoading(false); return; }
    loadAll();
  }, []);

  const deleteSub = async (id: number) => {
    if (!confirm("구독을 삭제하시겠습니까?")) return;
    await adminFetch(`/api/admin/chzzk/${id}`, { method: "DELETE" }).catch(() => {});
    setChzzk((p) => p.filter((s) => s.id !== id));
  };

  // ── 로딩 / 미인증 화면 ──
  if (loading) {
    return (
      <div className="min-h-screen bg-bg flex items-center justify-center">
        <div className="text-muted animate-pulse">로딩 중...</div>
      </div>
    );
  }

  if (authed === false) {
    const myId = (() => {
      try {
        const token = localStorage.getItem("token");
        if (!token) return null;
        const payload = JSON.parse(atob(token.split(".")[1]));
        return payload.sub as string;
      } catch { return null; }
    })();
    return (
      <div className="min-h-screen bg-bg flex flex-col items-center justify-center gap-4">
        <ShieldCheck size={48} className="text-muted" />
        <p className="text-fg font-semibold text-lg">접근 권한이 없습니다.</p>
        <p className="text-muted text-sm">봇 오너 계정으로 로그인한 후 접근하세요.</p>
        {myId && (
          <div className="bg-bg-card border border-border rounded-xl px-5 py-3 text-center space-y-1">
            <p className="text-xs text-muted">현재 로그인된 Discord ID</p>
            <p className="font-mono text-sm text-white select-all">{myId}</p>
            <p className="text-xs text-muted">이 값이 서버의 <code className="text-accent">OWNER_ID</code> 환경변수와 일치해야 합니다.</p>
          </div>
        )}
        <a href="/" className="text-accent text-sm hover:underline flex items-center gap-1">
          <LogIn size={14} /> 홈으로
        </a>
      </div>
    );
  }

  const tabs = [
    { key: "guilds", label: `서버 목록 (${guilds.length})` },
    { key: "chzzk",  label: `치지직 구독 (${chzzk.length})` },
    { key: "verifs", label: `팔로우 인증 (${verifs.length})` },
  ] as const;

  return (
    <div className="min-h-screen bg-bg text-fg">
      {showAdd && (
        <AddChzzkModal
          guilds={guilds}
          onClose={() => setShowAdd(false)}
          onAdded={loadAll}
        />
      )}

      {/* 헤더 */}
      <header className="border-b border-border bg-bg-card/60 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-5 flex items-center justify-between" style={{ height: 56 }}>
          <div className="flex items-center gap-2">
            <Bot size={18} className="text-accent" />
            <span className="font-bold text-fg">NexBot 관리자 패널</span>
            <span className="ml-2 text-[10px] px-2 py-0.5 rounded-full bg-danger/10 text-danger border border-danger/20 font-semibold">
              OWNER ONLY
            </span>
          </div>
          <button
            onClick={loadAll}
            disabled={refreshing}
            className="flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors"
          >
            <RefreshCw size={14} className={refreshing ? "animate-spin" : ""} />
            새로고침
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-5 py-8 space-y-8">

        {/* 통계 카드 */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
          <StatCard icon={<Server size={22} />}    label="등록 서버"      value={overview?.guild_count    ?? null} color="#5865F2" />
          <StatCard icon={<Users size={22} />}     label="총 유저 (상위30)" value={overview?.total_users  ?? null} color="#57F287" />
          <StatCard icon={<Radio size={22} />}     label="치지직 구독"    value={overview?.chzzk_subs    ?? null} color="#03C75A" />
          <StatCard icon={<ShieldCheck size={22} />} label="치지직 인증"  value={overview?.verifications ?? null} color="#EB459E" />
          <StatCard icon={<Bot size={22} />}       label="오늘 방문자"    value={overview?.today_visitors ?? null} color="#FEE75C" />
        </div>

        {/* 탭 */}
        <div className="flex gap-1 border-b border-border">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
                activeTab === t.key
                  ? "border-accent text-accent"
                  : "border-transparent text-muted hover:text-fg"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* ── 서버 목록 탭 ── */}
        {activeTab === "guilds" && (
          <div className="rounded-2xl border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-bg-card/60">
                  <th className="text-left px-4 py-3 text-muted font-medium">#</th>
                  <th className="text-left px-4 py-3 text-muted font-medium">서버</th>
                  <th className="text-left px-4 py-3 text-muted font-medium hidden md:table-cell">ID</th>
                  <th className="text-left px-4 py-3 text-muted font-medium">치지직</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {guilds.map((g, i) => (
                  <tr key={g.id} className="hover:bg-bg-hover/40 transition-colors">
                    <td className="px-4 py-3 text-muted text-xs">{i + 1}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <GuildIcon guild={g} />
                        <span className="font-medium text-white truncate max-w-[180px]">{g.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted hidden md:table-cell">{g.id}</td>
                    <td className="px-4 py-3">
                      {g.chzzk_name
                        ? <span className="text-xs px-2 py-0.5 rounded-full bg-chzzk/10 text-chzzk border border-chzzk/20">{g.chzzk_name}</span>
                        : <span className="text-xs text-muted">—</span>}
                    </td>
                  </tr>
                ))}
                {guilds.length === 0 && (
                  <tr><td colSpan={4} className="px-4 py-8 text-center text-muted">서버 없음</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* ── 치지직 구독 탭 ── */}
        {activeTab === "chzzk" && (
          <div className="space-y-4">
            <div className="flex justify-end">
              <button onClick={() => setShowAdd(true)} className="btn-primary">
                <Plus size={15} /> 구독 추가
              </button>
            </div>

            <div className="rounded-2xl border border-border overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-bg-card/60">
                    <th className="text-left px-4 py-3 text-muted font-medium">스트리머</th>
                    <th className="text-left px-4 py-3 text-muted font-medium">서버</th>
                    <th className="text-left px-4 py-3 text-muted font-medium hidden md:table-cell">채널 ID</th>
                    <th className="text-left px-4 py-3 text-muted font-medium">상태</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {chzzk.map((sub) => (
                    <tr key={sub.id} className="hover:bg-bg-hover/40 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {sub.chzzk_image_url ? (
                            <Image src={sub.chzzk_image_url} alt={sub.chzzk_name}
                                   width={32} height={32} className="rounded-full shrink-0" />
                          ) : (
                            <div className="w-8 h-8 rounded-full bg-bg-hover shrink-0" />
                          )}
                          <span className="font-medium text-white">{sub.chzzk_name}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-muted truncate max-w-[120px]">{sub.guild_name}</td>
                      <td className="px-4 py-3 font-mono text-xs text-muted hidden md:table-cell">{sub.discord_channel}</td>
                      <td className="px-4 py-3">
                        {sub.is_live
                          ? <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>LIVE</span>
                          : <span className="text-[10px] text-muted">오프라인</span>}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button onClick={() => deleteSub(sub.id)}
                                className="p-1.5 text-muted hover:text-danger transition-colors rounded-lg hover:bg-danger/10">
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {chzzk.length === 0 && (
                    <tr><td colSpan={5} className="px-4 py-8 text-center text-muted">구독 없음</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── 팔로우 인증 탭 ── */}
        {activeTab === "verifs" && (
          <div className="space-y-3">
            <p className="text-sm text-muted">
              치지직 OAuth 인증을 완료한 유저 목록입니다. <code className="text-accent bg-black/20 px-1 rounded">tier_months</code>는 인증 당시 기록된 구독 개월 수입니다.
            </p>
            <div className="rounded-2xl border border-border overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-bg-card/60">
                    <th className="text-left px-4 py-3 text-muted font-medium">서버 ID</th>
                    <th className="text-left px-4 py-3 text-muted font-medium">유저 ID</th>
                    <th className="text-left px-4 py-3 text-muted font-medium">구독 개월</th>
                    <th className="text-left px-4 py-3 text-muted font-medium hidden md:table-cell">인증 일시</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {verifs.map((v, i) => (
                    <tr key={i} className="hover:bg-bg-hover/40 transition-colors">
                      <td className="px-4 py-3 font-mono text-xs text-muted">{v.guild_id}</td>
                      <td className="px-4 py-3 font-mono text-xs text-white">{v.user_id}</td>
                      <td className="px-4 py-3">
                        <span className={`text-sm font-bold ${
                          v.tier_months >= 3 ? "text-chzzk" :
                          v.tier_months >= 1 ? "text-warning" : "text-muted"
                        }`}>
                          {v.tier_months}개월
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted hidden md:table-cell">
                        {new Date(v.verified_at * 1000).toLocaleString("ko-KR")}
                      </td>
                    </tr>
                  ))}
                  {verifs.length === 0 && (
                    <tr><td colSpan={4} className="px-4 py-8 text-center text-muted">인증 데이터 없음</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

      </main>
    </div>
  );
}
