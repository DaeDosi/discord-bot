"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Image from "next/image";
import { clsx } from "clsx";
import {
  Radio, Trash2, Bell, BellOff,
  ExternalLink, Plus, Users, X, ChevronLeft, ChevronRight,
  Youtube, Tv, Clock,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import type { ChzzkSubscription, Channel, Role, FollowerRoles, FollowRoleTier, ChzzkVerification } from "@/lib/types";

type Platform = "chzzk" | "youtube" | "soop";

const PLATFORM_TABS: { key: Platform; label: string; icon: LucideIcon }[] = [
  { key: "chzzk",   label: "치지직 (Chzzk)",   icon: Radio   },
  { key: "youtube", label: "유튜브 (YouTube)", icon: Youtube },
  { key: "soop",    label: "숲 (SOOP)",        icon: Tv      },
];

// ── 플랫폼 준비중 안내 ────────────────────────────────────────────────────────
function ComingSoonPanel({ label, Icon }: { label: string; Icon: LucideIcon }) {
  return (
    <div className="card text-center py-16 text-muted">
      <Icon size={40} className="mx-auto mb-3 opacity-30" />
      <p className="font-medium text-fg">{label} 알림 연동을 준비 중입니다.</p>
      <p className="text-sm mt-1 flex items-center justify-center gap-1.5">
        <Clock size={13} /> 곧 만나보실 수 있어요!
      </p>
    </div>
  );
}

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
              className="flex items-center gap-1 text-muted hover:text-fg transition-colors"
            >
              <ChevronLeft size={16} /> 목록으로
            </button>
          ) : (
            <p className="font-semibold text-fg flex items-center gap-2">
              <Users size={16} className="text-accent" />
              팔로우 인원
              {!loading && (
                <span className="text-xs text-muted font-normal">({verifications.length}명)</span>
              )}
            </p>
          )}
          <button onClick={onClose} className="text-muted hover:text-fg transition-colors">
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
                      <p className="text-sm font-medium text-fg truncate">{v.user_name}</p>
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
                <p className="font-semibold text-fg text-lg">{selected.user_name}</p>
                <p className="font-mono text-xs text-muted select-all mt-0.5">{selected.user_id}</p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-bg rounded-lg p-3 border border-border">
                  <p className="text-xs text-muted mb-1">팔로우 시작일</p>
                  <p className="text-sm font-medium text-fg">
                    {selected.follow_date
                      ? new Date(selected.follow_date).toLocaleDateString("ko-KR")
                      : "—"}
                  </p>
                </div>
                <div className="bg-bg rounded-lg p-3 border border-border">
                  <p className="text-xs text-muted mb-1">팔로우 기간</p>
                  <p className="text-sm font-medium text-fg">
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
                             className={clsx(
                               "flex items-center gap-3 px-3 py-2 rounded-lg border",
                               qualified ? "bg-accent/10 border-accent/30" : "border-border"
                             )}>
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

  const handleConnect = async () => {
    if (!discordChannel) return;
    try {
      const d = await api.chzzkAuth.getStreamerLoginUrl(guildId, discordChannel, mentionEveryone);
      window.location.href = d.url;
    } catch {}
  };

  return (
    <div className="card border-accent/30 space-y-4">
      <div>
        <p className="font-medium text-fg mb-1">치지직 스트리머 연동</p>
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
  const [platform, setPlatform] = useState<Platform>("chzzk");

  const [subs, setSubs]         = useState<ChzzkSubscription[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);

  const [followTiers, setFollowTiers] = useState<FollowRoleTier[]>([]);
  const [newMonths, setNewMonths]     = useState("");
  const [newRole, setNewRole]         = useState("");
  const [addingTier, setAddingTier]   = useState(false);

  const [verifications, setVerifications] = useState<ChzzkVerification[]>([]);
  const [verifOpen, setVerifOpen]         = useState(false);

  const textChannels = channels.filter((c) => c.type === 0);

  const load = async () => {
    const [s, ch, r, ft, v] = await Promise.all([
      api.chzzk.list(guildId),
      api.guilds.channels(guildId),
      api.guilds.roles(guildId),
      api.chzzk.followTiers.list(guildId).catch(() => [] as FollowRoleTier[]),
      api.chzzk.verifications(guildId).catch(() => [] as ChzzkVerification[]),
    ]);
    setSubs(s);
    setChannels(ch);
    setRoles(r);
    setFollowTiers(ft);
    setVerifications(v);
  };

  const openVerif = () => setVerifOpen(true);

  useEffect(() => {
    load();
    const params = new URLSearchParams(window.location.search);
    if (params.get("success") === "streamer_added" || params.get("success") === "token_refreshed") {
      window.history.replaceState({}, "", window.location.pathname);
      load();
    }
  }, [guildId]);

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

  return (
    <div className="space-y-6">
      {verifOpen && (
        <VerifModal
          verifications={verifications}
          loading={false}
          followTiers={followTiers}
          roles={roles}
          onClose={() => setVerifOpen(false)}
        />
      )}

      <div>
        <h1 className="page-title flex items-center gap-2">
          <Radio size={20} className="text-chzzk" /> 방송설정
        </h1>
        <p className="page-subtitle">
          스트리머 방송 시작 시 Discord 채널에 알림을 보냅니다.
        </p>
      </div>

      {/* 플랫폼 탭 */}
      <div className="flex items-center gap-1 p-1 rounded-xl bg-bg-hover w-fit max-w-full overflow-x-auto">
        {PLATFORM_TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setPlatform(key)}
            className={clsx(
              "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors",
              platform === key ? "bg-bg-card text-fg shadow-sm" : "text-muted hover:text-fg"
            )}
          >
            <Icon size={15} /> {label}
          </button>
        ))}
      </div>

      {platform === "youtube" && <ComingSoonPanel label="유튜브" Icon={Youtube} />}
      {platform === "soop" && <ComingSoonPanel label="숲(SOOP)" Icon={Tv} />}

      {platform === "chzzk" && (
      <>
      {/* 등록된 스트리머 */}
      <div className="space-y-3">
        {subs.map((sub) => {
          const ch = findChannel(sub.discord_channel);
          const handleRelink = async () => {
            try {
              const d = await api.chzzkAuth.getStreamerLoginUrl(
                guildId, String(sub.discord_channel), !!sub.mention_everyone
              );
              window.location.href = d.url;
            } catch {}
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
                    <p className="font-semibold text-fg">{sub.chzzk_name}</p>
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
                  className="flex items-center gap-2 text-sm font-medium px-4 py-2 rounded-lg border border-accent/60 text-accent hover:bg-accent/15 hover:border-accent transition-colors">
                  <ExternalLink size={15} />
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

      {/* 팔로워 역할 지급 */}
      {subs.length > 0 && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="section-title">팔로워 역할 지급</h2>
              <p className="page-subtitle">
                치지직 OAuth 인증 시 팔로우 기간에 따라 역할을 자동으로 부여합니다.
                티어를 여러 개 추가할 수 있으며, 조건을 만족하는 티어 중 가장 높은 역할이 지급됩니다.
                <br />
                Discord에서{" "}
                <code className="text-accent bg-bg px-1 rounded">/팔로우불러오기</code>
                로 기존 인증 유저에 재적용할 수 있습니다.
              </p>
            </div>
            <button
              onClick={openVerif}
              className="ml-4 shrink-0 flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg
                         border border-chzzk/60 text-chzzk bg-chzzk/10
                         hover:bg-chzzk/20 hover:border-chzzk transition-colors"
            >
              <Users size={15} /> 팔로우 인원
              {verifications.length > 0 && (
                <span className="text-xs bg-chzzk/30 rounded-full px-2 py-0.5 font-bold">
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
      </>
      )}
    </div>
  );
}
