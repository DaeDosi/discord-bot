"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import {
  Save, CheckCircle, Shield, AlertTriangle, X, Trash2, ChevronLeft, ChevronRight, UserPlus,
} from "lucide-react";
import { api } from "@/lib/api";
import type { GuildConfig, Channel, Role, WarnUser, WarnDetail, GuildMember } from "@/lib/types";
import MemberSearch from "@/components/MemberSearch";

// ── Warning modal (list → detail) ────────────────────────────────────────────
function WarnModal({
  guildId,
  onClose,
}: {
  guildId: string;
  onClose: () => void;
}) {
  const [warnUsers, setWarnUsers]   = useState<WarnUser[]>([]);
  const [selected, setSelected]     = useState<WarnUser | null>(null);
  const [details, setDetails]       = useState<WarnDetail[]>([]);
  const [loading, setLoading]       = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const loadUsers = useCallback(() => {
    setLoading(true);
    api.moderation.warnings(guildId)
      .then(setWarnUsers)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [guildId]);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  const openUser = async (u: WarnUser) => {
    setSelected(u);
    setLoadingDetail(true);
    try {
      const d = await api.moderation.userWarnings(guildId, u.user_id);
      setDetails(d);
    } finally {
      setLoadingDetail(false);
    }
  };

  const deleteOne = async (id: number) => {
    if (!selected) return;
    await api.moderation.deleteWarning(guildId, selected.user_id, id);
    const updated = details.filter((d) => d.id !== id);
    setDetails(updated);
    if (updated.length === 0) {
      setSelected(null);
      loadUsers();
    } else {
      setWarnUsers((prev) =>
        prev.map((u) => u.user_id === selected.user_id ? { ...u, count: updated.length } : u)
      );
      setSelected((prev) => prev ? { ...prev, count: updated.length } : null);
    }
  };

  const clearAll = async () => {
    if (!selected) return;
    await api.moderation.clearWarnings(guildId, selected.user_id);
    setWarnUsers((prev) => prev.filter((u) => u.user_id !== selected.user_id));
    setSelected(null);
  };

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
              <AlertTriangle size={16} className="text-yellow-400" /> 경고 현황
            </p>
          )}
          <button onClick={onClose} className="text-muted hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 p-4">
          {/* List view */}
          {!selected && (
            <>
              {loading ? (
                <p className="text-muted text-sm text-center py-8">불러오는 중...</p>
              ) : warnUsers.length === 0 ? (
                <p className="text-muted text-sm text-center py-8">경고를 받은 유저가 없습니다.</p>
              ) : (
                <div className="space-y-2">
                  {warnUsers.map((u) => (
                    <button
                      key={u.user_id}
                      onClick={() => openUser(u)}
                      className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg
                                 bg-bg border border-border hover:border-accent/40 hover:bg-bg-hover
                                 transition-colors text-left"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-white truncate">{u.display_name}</p>
                        <p className="text-xs text-muted mt-0.5">
                          마지막 경고: {new Date(u.latest_at * 1000).toLocaleDateString("ko-KR")}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 shrink-0 ml-2">
                        <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/20">
                          {u.count}회
                        </span>
                        <ChevronRight size={14} className="text-muted" />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}

          {/* Detail view */}
          {selected && (
            <>
              <div className="mb-3">
                <p className="font-semibold text-white">{selected.display_name}</p>
                <p className="text-sm text-muted">총 경고 {selected.count}회</p>
              </div>
              {loadingDetail ? (
                <p className="text-muted text-sm text-center py-6">불러오는 중...</p>
              ) : details.length === 0 ? (
                <p className="text-muted text-sm text-center py-6">경고 내역이 없습니다.</p>
              ) : (
                <div className="space-y-2">
                  {details.map((w) => (
                    <div
                      key={w.id}
                      className="flex items-start justify-between gap-3 p-3 rounded-lg bg-bg border border-border"
                    >
                      <div className="min-w-0">
                        <p className="text-sm text-white break-words">{w.reason || "(사유 없음)"}</p>
                        <p className="text-xs text-muted mt-0.5">
                          {new Date(w.created_at * 1000).toLocaleString("ko-KR")}
                        </p>
                      </div>
                      <button
                        onClick={() => deleteOne(w.id)}
                        className="shrink-0 text-muted hover:text-red-400 transition-colors p-1"
                        title="경고 삭제"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        {selected && (
          <div className="p-4 border-t border-border shrink-0 flex justify-end gap-2">
            <button onClick={() => setSelected(null)} className="btn-secondary text-sm">닫기</button>
            <button
              onClick={clearAll}
              className="text-sm px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20
                         hover:bg-red-500/20 transition-colors flex items-center gap-1.5"
            >
              <Trash2 size={14} /> 전체 삭제
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ModerationPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [cfg, setCfg]           = useState<GuildConfig>({});
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);
  const [saving, setSaving]     = useState(false);
  const [saved, setSaved]       = useState(false);
  const [warnOpen, setWarnOpen] = useState(false);

  const [managers, setManagers]       = useState<{ user_id: string; display_name: string }[]>([]);
  const [newManager, setNewManager]   = useState<GuildMember | null>(null);
  const [addingMgr, setAddingMgr]     = useState(false);

  useEffect(() => {
    Promise.all([
      api.settings.get(guildId),
      api.guilds.channels(guildId),
      api.guilds.roles(guildId),
    ]).then(([c, ch, r]) => {
      setCfg({
        ...c,
        mod_role_id: c.mod_role_id ? String(c.mod_role_id) : "",
        log_channel: c.log_channel ? String(c.log_channel) : "",
      });
      setChannels(ch);
      setRoles(r);
    });
    api.settings.managers.list(guildId).then(setManagers).catch(() => {});
  }, [guildId]);

  const addManager = async () => {
    if (!newManager) return;
    setAddingMgr(true);
    await api.settings.managers.add(guildId, newManager.id);
    setManagers((prev) => [...prev, { user_id: newManager.id, display_name: newManager.display_name }]);
    setNewManager(null);
    setAddingMgr(false);
  };

  const removeManager = async (userId: string) => {
    await api.settings.managers.remove(guildId, userId);
    setManagers((prev) => prev.filter((m) => m.user_id !== userId));
  };

  const save = async () => {
    setSaving(true);
    await api.settings.save(guildId, cfg).catch(() => {});
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const set = (key: keyof GuildConfig) => (v: string | boolean | number) =>
    setCfg((p) => ({ ...p, [key]: v }));

  return (
    <div className="space-y-6">
      {warnOpen && <WarnModal guildId={guildId} onClose={() => setWarnOpen(false)} />}

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Shield size={20} className="text-accent" /> 관리 설정
          </h1>
          <p className="text-muted text-sm mt-1">매니저 역할, 로그 채널, 자동 관리를 설정합니다.</p>
        </div>
        <button
          onClick={() => setWarnOpen(true)}
          className="shrink-0 flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium
                     bg-yellow-500/10 text-yellow-400 border border-yellow-500/20
                     hover:bg-yellow-500/20 transition-colors"
        >
          <AlertTriangle size={15} /> 경고 현황
        </button>
      </div>

      {/* ── 기본 설정 ── */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white">기본 설정</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">매니저 역할</label>
            <select className="select" value={cfg.mod_role_id || ""} onChange={(e) => set("mod_role_id")(e.target.value)}>
              <option value="">역할 선택...</option>
              {roles.map((r) => <option key={r.id} value={r.id}>@{r.name}</option>)}
            </select>
          </div>
          <div>
            <label className="label">관리 로그 채널</label>
            <select className="select" value={cfg.log_channel || ""} onChange={(e) => set("log_channel")(e.target.value)}>
              <option value="">채널 선택...</option>
              {channels.filter((c) => c.type === 0).map((c) => <option key={c.id} value={c.id}>#{c.name}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* ── 경고 자동 처리 ── */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white">경고 자동 처리</h2>
        <p className="text-sm text-muted">0으로 설정하면 비활성화됩니다.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">자동 추방 경고 횟수</label>
            <input
              type="number" min={0} className="input"
              placeholder="0 (비활성화)"
              value={cfg.warn_kick_threshold ?? 0}
              onChange={(e) => set("warn_kick_threshold")(Number(e.target.value))}
            />
            <p className="text-sm text-muted mt-1.5">해당 횟수 도달 시 자동으로 추방합니다.</p>
          </div>
          <div>
            <label className="label">자동 차단 경고 횟수</label>
            <input
              type="number" min={0} className="input"
              placeholder="0 (비활성화)"
              value={cfg.warn_ban_threshold ?? 0}
              onChange={(e) => set("warn_ban_threshold")(Number(e.target.value))}
            />
            <p className="text-sm text-muted mt-1.5">해당 횟수 도달 시 자동으로 차단합니다.</p>
          </div>
        </div>
      </div>

      {/* ── Auto-Mod ── */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white">자동 관리 (Auto-Mod)</h2>
        <label className="flex items-center justify-between cursor-pointer p-3 rounded-lg bg-bg border border-border">
          <div>
            <p className="text-sm font-medium">자동 관리 활성화</p>
            <p className="text-sm text-muted mt-0.5">금지어·멘션 도배·스팸 자동 감지 및 처리</p>
          </div>
          <div className="relative">
            <input type="checkbox" className="sr-only peer"
              checked={cfg.automod_enabled ?? true}
              onChange={(e) => set("automod_enabled")(e.target.checked)} />
            <div className="w-10 h-6 bg-border rounded-full peer peer-checked:bg-accent transition-colors" />
            <div className="absolute left-1 top-1 w-4 h-4 bg-white rounded-full transition-transform peer-checked:translate-x-4" />
          </div>
        </label>
        <div>
          <label className="label">금지어 목록 (쉼표로 구분)</label>
          <textarea
            className="input min-h-[80px] resize-y"
            placeholder="욕설1, 욕설2, 비속어3, ..."
            value={cfg.badwords || ""}
            onChange={(e) => set("badwords")(e.target.value)}
          />
          <p className="text-sm text-muted mt-1.5">
            감지 시 메시지 삭제 + 경고 1회 자동 부여. 경고 3회 누적 시 10분 뮤트.
          </p>
        </div>
      </div>

      <button onClick={save} disabled={saving} className="btn-primary">
        {saved
          ? <><CheckCircle size={16} /> 저장됨</>
          : <><Save size={16} /> {saving ? "저장 중..." : "변경사항 저장"}</>}
      </button>

      {/* ── 개별 매니저 등록 ── */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white flex items-center gap-2">
          <UserPlus size={16} className="text-accent" /> 개별 매니저 등록
        </h2>
        <p className="text-sm text-muted">역할이 없어도 특정 멤버에게 매니저 권한을 부여합니다.</p>
        <div className="flex gap-2">
          <div className="flex-1">
            <MemberSearch guildId={guildId} value={newManager} onChange={setNewManager} placeholder="멤버 검색..." />
          </div>
          <button
            onClick={addManager}
            disabled={!newManager || addingMgr}
            className="btn-primary shrink-0 self-end"
          >
            추가
          </button>
        </div>
        {managers.length === 0 ? (
          <p className="text-sm text-muted text-center py-4">등록된 매니저가 없습니다.</p>
        ) : (
          <div className="space-y-2">
            {managers.map((m) => (
              <div
                key={m.user_id}
                className="flex items-center justify-between px-3 py-2.5 rounded-lg bg-bg border border-border"
              >
                <p className="text-sm font-medium text-white">{m.display_name}</p>
                <button
                  onClick={() => removeManager(m.user_id)}
                  className="text-muted hover:text-red-400 transition-colors p-1"
                  title="매니저 제거"
                >
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
