"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Image from "next/image";
import { Radio, Trash2, Bell, BellOff, Save, CheckCircle, ExternalLink, Plus } from "lucide-react";
import { api } from "@/lib/api";
import type { ChzzkSubscription, Channel, Role, FollowerRoles, FollowRoleTier } from "@/lib/types";

const BACKEND = process.env.NEXT_PUBLIC_API_URL || "";

function AddStreamerForm({
  channels,
  guildId,
}: {
  channels: Channel[];
  guildId: string;
}) {
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
        <p className="text-xs text-muted">
          치지직 계정으로 로그인하면 해당 채널이 자동으로 등록됩니다.
        </p>
      </div>

      <div>
        <label className="label">알림 받을 Discord 채널 *</label>
        <select
          className="select"
          value={discordChannel}
          onChange={(e) => setDiscordChannel(e.target.value)}
        >
          <option value="">채널 선택...</option>
          {textChannels.map((c) => (
            <option key={c.id} value={c.id}>#{c.name}</option>
          ))}
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

      <button
        onClick={handleConnect}
        disabled={!discordChannel}
        className="btn-primary w-full justify-center"
      >
        <ExternalLink size={16} />
        치지직 계정으로 연동하기
      </button>
    </div>
  );
}

export default function ChzzkPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [subs, setSubs]         = useState<ChzzkSubscription[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);
  const [followerRoles, setFollowerRoles] = useState<FollowerRoles>({
    follow_role_1month:  null,
    follow_role_3month:  null,
    follow_months_tier1: 1,
    follow_months_tier2: 3,
  });
  const [savingRoles, setSavingRoles]   = useState(false);
  const [savedRoles, setSavedRoles]     = useState(false);
  const [followTiers, setFollowTiers]   = useState<FollowRoleTier[]>([]);
  const [newMonths, setNewMonths]       = useState("");
  const [newRole, setNewRole]           = useState("");
  const [addingTier, setAddingTier]     = useState(false);

  const load = async () => {
    const [s, ch, r, fr, ft] = await Promise.all([
      api.chzzk.list(guildId),
      api.guilds.channels(guildId),
      api.guilds.roles(guildId),
      api.chzzk.getFollowerRoles(guildId).catch(() => ({ follow_role_1month: null, follow_role_3month: null, follow_months_tier1: 1, follow_months_tier2: 3 })),
      api.chzzk.followTiers.list(guildId).catch(() => [] as FollowRoleTier[]),
    ]);
    setSubs(s);
    setChannels(ch);
    setRoles(r);
    setFollowerRoles(fr);
    setFollowTiers(ft);
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

  useEffect(() => {
    load();
    // 치지직 OAuth 콜백 후 success/error 파라미터 처리
    const params = new URLSearchParams(window.location.search);
    if (params.get("success") === "streamer_added") {
      window.history.replaceState({}, "", window.location.pathname);
      load();
    }
  }, [guildId]);

  const saveFollowerRoles = async () => {
    setSavingRoles(true);
    await api.chzzk.saveFollowerRoles(guildId, followerRoles).catch(() => {});
    setSavingRoles(false);
    setSavedRoles(true);
    setTimeout(() => setSavedRoles(false), 2500);
  };

  const remove = async (id: number) => {
    await api.chzzk.remove(guildId, id);
    setSubs((p) => p.filter((s) => s.id !== id));
  };

  const findChannel = (id: number) =>
    channels.find((c) => String(c.id) === String(id));

  return (
    <div className="space-y-6">
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
          return (
            <div key={sub.id} className="card flex items-center gap-4">
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
                    : <span className="badge-offline">오프라인</span>
                  }
                  {Boolean(sub.mention_everyone) && (
                    <span className="text-xs text-muted flex items-center gap-1">
                      <Bell size={11} /> @everyone
                    </span>
                  )}
                </div>
                {ch && <span className="text-xs text-muted mt-1 block">→ #{ch.name}</span>}
              </div>
              <button
                onClick={() => remove(sub.id)}
                className="p-2 text-muted hover:text-danger transition-colors rounded-lg hover:bg-danger/10 shrink-0"
              >
                <Trash2 size={16} />
              </button>
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

      {/* 스트리머 추가 (구독이 없을 때만) */}
      {subs.length === 0 && (
        <AddStreamerForm channels={channels} guildId={guildId} />
      )}

      {subs.length >= 1 && (
        <p className="text-sm text-muted text-center">
          서버당 1명만 등록 가능합니다. 기존 구독을 삭제 후 다시 연동하세요.
        </p>
      )}

      {/* 팔로워 역할 티어 관리 */}
      {subs.length > 0 && (
        <div className="card space-y-4">
          <div>
            <h2 className="font-semibold text-white">팔로우 역할 티어</h2>
            <p className="text-muted text-xs mt-1">
              치지직 OAuth 인증 시 팔로우 기간에 따라 역할을 자동으로 부여합니다.
              티어를 여러 개 추가할 수 있으며, 조건을 만족하는 모든 역할이 부여됩니다.
              <br />
              Discord에서 <code className="text-accent bg-black/20 px-1 rounded">/팔로우불러오기</code>로 기존 인증 유저에 재적용할 수 있습니다.
            </p>
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
