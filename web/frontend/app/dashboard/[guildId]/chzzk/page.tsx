"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Image from "next/image";
import {
  Radio, Trash2, Bell, BellOff, Save, CheckCircle,
  ExternalLink, Plus, Users, X, ChevronLeft, ChevronRight,
  ToggleLeft, ToggleRight,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ChzzkSubscription, Channel, Role, FollowerRoles, FollowRoleTier, ChzzkVerification } from "@/lib/types";

const BACKEND = process.env.NEXT_PUBLIC_API_URL || "";

// ── 팔로우 인증 현황 모달 ──────────────────────────────────────────────────────
function VerifModal({
  verifications,
  loading,
  followTiers,
  roles,
  onClose,
}: {
  verifications: ChzzkVerification[];
  loading: boolean;
  followTiers: FollowRoleTier[];
  roles: Role[];
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<ChzzkVerification | null>(null);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-bg-card border border-border rounded-xl w-full max-w-lg shadow-xl flex flex-col"
           style={{ maxHeight: "80vh" }}>
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
          {selected ? (
            <button
              onClick={() => setSelected(null)}
              className="flex items-center gap-1 text-muted hover:text-white transition-colors"
            >
              <ChevronLeft size={16} /> 목록으로
            </button>
          ) : (
            <p className="font-semibold text-white flex items-center gap-2">
              <Users size={16} className="text-accent" />
              팔로우 인원
              {!loading && (
                <span className="text-xs text-muted font-normal">({verifications.length}명)</span>
              )}
            </p>
          )}
          <button onClick={onClose} className="text-muted hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 p-4">
          {loading ? (
            <p className="text-sm text-muted text-center py-8 animate-pulse">불러오는 중...</p>
          ) : verifications.length === 0 ? (
            <p className="text-sm text-muted text-center py-8">아직 인증한 유저가 없습니다.</p>
          ) : !selected ? (
            /* 목록 */
            <div className="space-y-2">
              {verifications.map((v) => {
                const qualifiedTier = [...followTiers]
                  .sort((a, b) => b.months - a.months)
                  .find((t) => v.tier_months >= t.months);
                const tierRole = qualifiedTier ? roles.find((r) => r.id === qualifiedTier.role_id) : null;
                return (
                  <button
                    key={v.user_id}
                    onClick={() => setSelected(v)}
                    className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg
                               bg-bg border border-border hover:border-accent/40 hover:bg-bg-hover
                               transition-colors text-left"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-white truncate">{v.user_name}</p>
                      <p className="text-xs text-muted mt-0.5">
                        {v.is_following
                          ? `팔로우 ${v.follow_days}일 (${v.tier_months}개월)`
                          : "미팔로우"}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-2">
                      {tierRole && (
                        <span className="text-[11px] px-1.5 py-0.5 rounded-full border border-border"
                              style={{ color: tierRole.color ? `#${tierRole.color.toString(16).padStart(6,"0")}` : "#fff" }}>
                          @{tierRole.name}
                        </span>
                      )}
                      <ChevronRight size={14} className="text-muted" />
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            /* 상세 */
            <div className="space-y-4">
              <div>
                <p className="font-semibold text-white text-lg">{selected.user_name}</p>
                <p className="font-mono text-xs text-muted select-all mt-0.5">{selected.user_id}</p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-bg rounded-lg p-3 border border-border">
                  <p className="text-xs text-muted mb-1">팔로우 시작일</p>
                  <p className="text-sm font-medium text-white">
                    {selected.follow_date
                      ? new Date(selected.follow_date).toLocaleDateString("ko-KR")
                      : "—"}
                  </p>
                </div>
                <div className="bg-bg rounded-lg p-3 border border-border">
                  <p className="text-xs text-muted mb-1">팔로우 기간</p>
                  <p className="text-sm font-medium text-white">
                    {selected.is_following
                      ? `${selected.follow_days}일 (${selected.tier_months}개월)`
                      : "—"}
                  </p>
                </div>
              </div>
              {followTiers.length > 0 && (
                <div>
                  <p className="text-xs text-muted mb-2">역할 티어 현황</p>
                  <div className="space-y-2">
                    {[...followTiers].sort((a, b) => a.months - b.months).map((tier) => {
                      const role = roles.find((r) => r.id === tier.role_id);
                      const qualified = selected.tier_months >= tier.months;
                      return (
                        <div key={tier.id}
                             className="flex items-center gap-3 px-3 py-2 rounded-lg border"
                             style={qualified
                               ? { background: "rgba(99,102,241,0.08)", borderColor: "rgba(99,102,241,0.3)" }
                               : { borderColor: "var(--color-border)" }}>
                          <span className={`text-xs font-semibold w-16 shrink-0 ${qualified ? "text-accent" : "text-muted"}`}>
                            {tier.months}개월+
                          </span>
                          {role && (
                            <span className="text-sm text-fg flex-1">@{role.name}</span>
                          )}
                          <span className={`text-[11px] font-bold ${qualified ? "text-accent" : "text-muted"}`}>
                            {qualified ? "✓ 충족" : "미충족"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="p-4 border-t border-border shrink-0 flex justify-end">
          <button onClick={onClose} className="btn-secondary text-sm">닫기</button>
        </div>
      </div>
    </div>
  );
}

// ── 스트리머 추가 폼 ──────────────────────────────────────────────────────────
function AddStreamerForm({ channels, guildId }: { channels: Channel[]; guildId: string }) {
  const textChannels = channels.filter((c) => c.type === 0);
  const [discordChannel, setDiscordChannel] = useState("");
  const [mentionEveryone, setMentionEveryone] = useState(false);

  const handleConnect = () => {
    if (!discordChannel) return;
    let discordUserId = "";
    try {
      const raw = localStorage.getItem("discord_user");
      if (raw) discordUserId = JSON.parse(raw).id || "";
    } catch {}
    const params = new URLSearchParams({
      guild_id:         guildId,
      discord_channel:  discordChannel,
      mention_everyone: mentionEveryone ? "1" : "0",
      discord_user_id:  discordUserId,
    });
    window.location.href = `${BACKEND}/api/chzzk-auth/streamer-login?${params}`;
  };

  return (
    <div className="card border-accent/30 space-y-4">
      <div>
        <p className="font-medium text-white mb-1">치지직 스트리머 연동</p>
        <p className="text-sm text-muted">
          치지직 계정으로 로그인하면 해당 채널이 자동으로 등록됩니다.
        </p>
      </div>

      <div>
        <label className="label">알림 받을 Discord 채널 *</label>
        <select className="select" value={discordChannel} onChange={(e) => setDiscordChannel(e.target.value)}>
          <option value="">채널 선택...</option>
          {textChannels.map((c) => <option key={c.id} value={c.id}>#{c.name}</option>)}
        </select>
      </div>

      <button
        type="button"
        onClick={() => setMentionEveryone((v) => !v)}
        className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors ${
          mentionEveryone
            ? "border-accent bg-accent/10 text-accent"
            : "border-border text-muted hover:border-border/60"
        }`}
      >
        {mentionEveryone ? <Bell size={16} /> : <BellOff size={16} />}
        <span className="text-sm font-medium">
          {mentionEveryone ? "@everyone 멘션 켜짐" : "@everyone 멘션 끄기"}
        </span>
      </button>

      <button onClick={handleConnect} disabled={!discordChannel} className="btn-primary w-full justify-center">
        <ExternalLink size={16} />
        치지직 계정으로 연동하기
      </button>
    </div>
  );
}

// ── 메인 페이지 ───────────────────────────────────────────────────────────────
export default function ChzzkPage() {
  const { guildId } = useParams<{ guildId: string }>();

  const [subs, setSubs]         = useState<ChzzkSubscription[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);

  const [contentNotify, setContentNotify] = useState<{
    notify_vod: boolean; notify_clip: boolean; notify_community: boolean;
    vod_channel: string | null; clip_channel: string | null; community_channel: string | null;
  }>({ notify_vod: false, notify_clip: false, notify_community: false,
       vod_channel: null, clip_channel: null, community_channel: null });
  const [savingContent, setSavingContent] = useState(false);
  const [savedContent, setSavedContent]   = useState(false);

  const [followTiers, setFollowTiers] = useState<FollowRoleTier[]>([]);
  const [newMonths, setNewMonths]     = useState("");
  const [newRole, setNewRole]         = useState("");
  const [addingTier, setAddingTier]   = useState(false);

  const [verifications, setVerifications] = useState<ChzzkVerification[]>([]);
  const [verifOpen, setVerifOpen]         = useState(false);
  const [loadingVerif, setLoadingVerif]   = useState(false);

  const textChannels = channels.filter((c) => c.type === 0);

  const load = async () => {
    const [s, ch, r, ft, cn] = await Promise.all([
      api.chzzk.list(guildId),
      api.guilds.channels(guildId),
      api.guilds.roles(guildId),
      api.chzzk.followTiers.list(guildId).catch(() => [] as FollowRoleTier[]),
      api.chzzk.contentNotify.get(guildId).catch(() => ({
        notify_vod: false, notify_clip: false, notify_community: false,
        vod_channel: null, clip_channel: null, community_channel: null,
      })),
    ]);
    setSubs(s);
    setChannels(ch);
    setRoles(r);
    setFollowTiers(ft);
    setContentNotify(cn);
  };

  const openVerif = async () => {
    setVerifOpen(true);
    if (verifications.length === 0) {
      setLoadingVerif(true);
      const v = await api.chzzk.verifications(guildId).catch(() => [] as ChzzkVerification[]);
      setVerifications(v);
      setLoadingVerif(false);
    }
  };

  useEffect(() => {
    load();
    const params = new URLSearchParams(window.location.search);
    if (params.get("success") === "streamer_added" || params.get("success") === "token_refreshed") {
      window.history.replaceState({}, "", window.location.pathname);
      load();
    }
  }, [guildId]);

  const saveContentNotify = async () => {
    setSavingContent(true);
    await api.chzzk.contentNotify.save(guildId, contentNotify).catch(() => {});
    setSavingContent(false);
    setSavedContent(true);
    setTimeout(() => setSavedContent(false), 2500);
  };

  const addTier = async () => {
    if (!newMonths || !newRole) return;
    setAddingTier(true);
    try {
      await api.chzzk.followTiers.add(guildId, parseInt(newMonths), newRole);
      const ft = await api.chzzk.followTiers.list(guildId);
      setFollowTiers(ft);
      setNewMonths(""); setNewRole("");
    } catch {}
    setAddingTier(false);
  };

  const removeTier = async (tierId: number) => {
    await api.chzzk.followTiers.remove(guildId, tierId).catch(() => {});
    setFollowTiers((p) => p.filter((t) => t.id !== tierId));
  };

  const remove = async (id: number) => {
    await api.chzzk.remove(guildId, id);
    setSubs((p) => p.filter((s) => s.id !== id));
  };

  const findChannel = (id: number) => channels.find((c) => String(c.id) === String(id));

  const contentItems = [
    { key: "notify_vod"       as const, chKey: "vod_channel"       as const, label: "동영상 (다시보기)", desc: "새 다시보기 영상이 업로드되면 알림" },
    { key: "notify_clip"      as const, chKey: "clip_channel"       as const, label: "클립",              desc: "새 클립이 등록되면 알림" },
    { key: "notify_community" as const, chKey: "community_channel"  as const, label: "커뮤니티 게시글",   desc: "새 게시글이 작성되면 알림" },
  ];

  return (
    <div className="space-y-6">
      {verifOpen && (
        <VerifModal
          verifications={verifications}
          loading={loadingVerif}
          followTiers={followTiers}
          roles={roles}
          onClose={() => setVerifOpen(false)}
        />
      )}

      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Radio size={20} className="text-chzzk" /> 치지직 알림
          </h1>
          <p className="text-muted text-sm mt-1">
            스트리머 방송 시작 시 Discord 채널에 알림을 보냅니다.
          </p>
        </div>
      </div>

      {/* 등록된 스트리머 */}
      <div className="space-y-3">
        {subs.map((sub) => {
          const ch = findChannel(sub.discord_channel);
          const handleRelink = () => {
            let discordUserId = "";
            try {
              const raw = localStorage.getItem("discord_user");
              if (raw) discordUserId = JSON.parse(raw).id || "";
            } catch {}
            const params = new URLSearchParams({
              guild_id:         guildId,
              discord_channel:  String(sub.discord_channel),
              mention_everyone: sub.mention_everyone ? "1" : "0",
              discord_user_id:  discordUserId,
            });
            window.location.href = `${BACKEND}/api/chzzk-auth/streamer-login?${params}`;
          };
          return (
            <div key={sub.id} className="card space-y-3">
              <div className="flex items-center gap-4">
                {sub.chzzk_image_url ? (
                  <Image src={sub.chzzk_image_url} alt={sub.chzzk_name}
                         width={48} height={48} className="rounded-full shrink-0" />
                ) : (
                  <div className="w-12 h-12 rounded-full bg-bg-hover flex items-center justify-center shrink-0">
                    <Radio size={20} className="text-muted" />
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="font-semibold text-white">{sub.chzzk_name}</p>
                    {sub.is_live
                      ? <span className="badge-live"><span className="w-1.5 h-1.5 rounded-full bg-chzzk animate-pulse" />LIVE</span>
                      : <span className="badge-offline">오프라인</span>}
                    {Boolean(sub.mention_everyone) && (
                      <span className="text-xs text-muted flex items-center gap-1">
                        <Bell size={11} /> @everyone
                      </span>
                    )}
                  </div>
                  {ch && <span className="text-sm text-muted mt-1 block">→ #{ch.name}</span>}
                </div>
                <button onClick={() => remove(sub.id)}
                  className="p-2 text-muted hover:text-danger transition-colors rounded-lg hover:bg-danger/10 shrink-0">
                  <Trash2 size={16} />
                </button>
              </div>
              <div className="border-t border-border pt-3">
                <p className="text-sm text-muted mb-2">
                  팔로우 기간 조회를 위해 스트리머({sub.chzzk_name}) 본인이 아래 버튼으로 치지직 계정을 연동해야 합니다.
                </p>
                <button onClick={handleRelink}
                  className="flex items-center gap-2 text-xs px-3 py-1.5 rounded-lg border border-accent/40 text-accent hover:bg-accent/10 transition-colors">
                  <ExternalLink size={12} />
                  스트리머 팔로워 조회 연동
                </button>
              </div>
            </div>
          );
        })}

        {subs.length === 0 && (
          <div className="text-center py-10 text-muted">
            <Radio size={40} className="mx-auto mb-3 opacity-30" />
            <p className="font-medium">구독 중인 스트리머가 없습니다.</p>
            <p className="text-sm mt-1">아래에서 치지직 계정을 연동해 알림을 설정하세요.</p>
          </div>
        )}
      </div>

      {subs.length === 0 && <AddStreamerForm channels={channels} guildId={guildId} />}
      {subs.length >= 1 && (
        <p className="text-sm text-muted text-center">
          서버당 1명만 등록 가능합니다. 기존 구독을 삭제 후 다시 연동하세요.
        </p>
      )}

      {/* 콘텐츠 알림 */}
      {subs.length > 0 && (
        <div className="card space-y-4">
          <div>
            <h2 className="font-semibold text-white">콘텐츠 알림</h2>
            <p className="text-muted text-sm mt-1">
              새 콘텐츠가 등록될 때 지정한 채널에 자동으로 알림을 보냅니다.
              채널을 지정하지 않으면 방송 알림 채널이 사용됩니다.
            </p>
          </div>
          {contentItems.map(({ key, chKey, label, desc }) => (
            <div key={key} className="space-y-2">
              <div
                className="flex items-center justify-between p-3 rounded-lg border border-border hover:bg-bg-hover transition-colors cursor-pointer"
                onClick={() => setContentNotify((p) => ({ ...p, [key]: !p[key] }))}
              >
                <div>
                  <p className="text-sm font-medium text-white">{label}</p>
                  <p className="text-sm text-muted mt-0.5">{desc}</p>
                </div>
                {contentNotify[key]
                  ? <ToggleRight size={28} className="text-accent shrink-0" />
                  : <ToggleLeft  size={28} className="text-muted  shrink-0" />}
              </div>
              {contentNotify[key] && (
                <div className="ml-3">
                  <label className="label text-xs">알림 채널 (선택)</label>
                  <select
                    className="select"
                    value={contentNotify[chKey] ?? ""}
                    onChange={(e) => setContentNotify((p) => ({ ...p, [chKey]: e.target.value || null }))}
                  >
                    <option value="">방송 알림 채널 사용</option>
                    {textChannels.map((c) => <option key={c.id} value={c.id}>#{c.name}</option>)}
                  </select>
                </div>
              )}
            </div>
          ))}
          <button onClick={saveContentNotify} disabled={savingContent} className="btn-primary">
            {savedContent
              ? <><CheckCircle size={16} /> 저장됨</>
              : <><Save size={16} /> {savingContent ? "저장 중..." : "저장"}</>}
          </button>
        </div>
      )}

      {/* 팔로워 역할 지급 */}
      {subs.length > 0 && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="font-semibold text-white">팔로워 역할 지급</h2>
              <p className="text-muted text-sm mt-1">
                치지직 OAuth 인증 시 팔로우 기간에 따라 역할을 자동으로 부여합니다.
                티어를 여러 개 추가할 수 있으며, 조건을 만족하는 티어 중 가장 높은 역할이 지급됩니다.
                <br />
                Discord에서{" "}
                <code className="text-accent bg-black/20 px-1 rounded">/팔로우불러오기</code>
                로 기존 인증 유저에 재적용할 수 있습니다.
              </p>
            </div>
            <button
              onClick={openVerif}
              className="ml-4 shrink-0 flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-accent/40
                         text-accent hover:bg-accent/10 transition-colors"
            >
              <Users size={13} /> 팔로우 인원
              {verifications.length > 0 && (
                <span className="ml-1 text-[10px] bg-accent/20 rounded-full px-1.5 py-0.5 font-semibold">
                  {verifications.length}
                </span>
              )}
            </button>
          </div>

          {/* 등록된 티어 목록 */}
          <div className="space-y-2">
            {followTiers.length === 0 && (
              <p className="text-sm text-muted text-center py-3">등록된 티어가 없습니다. 아래에서 추가하세요.</p>
            )}
            {followTiers.map((tier) => {
              const role = roles.find((r) => r.id === tier.role_id);
              return (
                <div key={tier.id} className="flex items-center justify-between bg-bg rounded-lg px-4 py-3 border border-border">
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-semibold text-accent w-24">{tier.months}개월 이상</span>
                    {role && (
                      <span className="text-sm px-2 py-0.5 rounded-full border border-border"
                            style={{ color: role.color ? `#${role.color.toString(16).padStart(6, "0")}` : "#fff" }}>
                        @{role.name}
                      </span>
                    )}
                    {!role && <span className="text-xs text-muted">역할 ID: {tier.role_id}</span>}
                  </div>
                  <button onClick={() => removeTier(tier.id)}
                          className="text-muted hover:text-danger transition-colors p-1">
                    <Trash2 size={14} />
                  </button>
                </div>
              );
            })}
          </div>

          {/* 티어 추가 */}
          {followTiers.length >= 5 ? (
            <p className="text-xs text-muted text-center pt-2 border-t border-border">
              최대 5개의 티어까지만 추가할 수 있습니다.
            </p>
          ) : (
            <div className="flex gap-2 pt-2 border-t border-border flex-wrap">
              <div className="flex items-center gap-2">
                <input
                  type="number" min={1} max={120}
                  className="input w-24 text-center"
                  placeholder="개월"
                  value={newMonths}
                  onChange={(e) => setNewMonths(e.target.value)}
                />
                <span className="text-sm text-muted shrink-0">개월 이상</span>
              </div>
              <select className="select flex-1 min-w-36" value={newRole}
                      onChange={(e) => setNewRole(e.target.value)}>
                <option value="">역할 선택...</option>
                {roles.map((r) => <option key={r.id} value={r.id}>@{r.name}</option>)}
              </select>
              <button onClick={addTier} disabled={addingTier || !newMonths || !newRole}
                      className="btn-primary shrink-0">
                <Plus size={15} /> {addingTier ? "추가 중..." : "추가"}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
