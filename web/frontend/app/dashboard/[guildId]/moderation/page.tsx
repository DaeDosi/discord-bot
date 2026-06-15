"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { Save, CheckCircle, Shield, AlertTriangle, X, Trash2, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import type { GuildConfig, Channel, Role, WarnUser, WarnDetail } from "@/lib/types";

// ── Warning detail modal ──────────────────────────────────────────────────────
function WarnModal({
  user,
  warnings,
  onClose,
  onDeleteOne,
  onClearAll,
}: {
  user: WarnUser;
  warnings: WarnDetail[];
  onClose: () => void;
  onDeleteOne: (id: number) => void;
  onClearAll: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-bg-card border border-border rounded-xl w-full max-w-md shadow-xl">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div>
            <p className="font-semibold text-white">{user.display_name}</p>
            <p className="text-xs text-muted">경고 {user.count}회</p>
          </div>
          <button onClick={onClose} className="text-muted hover:text-white transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="p-4 space-y-2 max-h-64 overflow-y-auto">
          {warnings.length === 0 ? (
            <p className="text-muted text-sm text-center py-4">경고 내역이 없습니다.</p>
          ) : (
            warnings.map((w) => (
              <div
                key={w.id}
                className="flex items-start justify-between gap-3 p-2 rounded-lg bg-bg border border-border"
              >
                <div className="min-w-0">
                  <p className="text-sm text-white break-words">{w.reason || "(사유 없음)"}</p>
                  <p className="text-xs text-muted mt-0.5">
                    {new Date(w.created_at * 1000).toLocaleString("ko-KR")}
                  </p>
                </div>
                <button
                  onClick={() => onDeleteOne(w.id)}
                  className="shrink-0 text-muted hover:text-red-400 transition-colors"
                  title="경고 삭제"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))
          )}
        </div>

        <div className="p-4 border-t border-border flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary text-sm">닫기</button>
          <button
            onClick={onClearAll}
            className="text-sm px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20
                       hover:bg-red-500/20 transition-colors"
          >
            전체 삭제
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ModerationPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [cfg, setCfg]         = useState<GuildConfig>({});
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);
  const [saving, setSaving]     = useState(false);
  const [saved, setSaved]       = useState(false);

  const [warnUsers, setWarnUsers]   = useState<WarnUser[]>([]);
  const [selected, setSelected]     = useState<WarnUser | null>(null);
  const [details, setDetails]       = useState<WarnDetail[]>([]);
  const [loadingDetails, setLoadingDetails] = useState(false);

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
    loadWarnUsers();
  }, [guildId]);

  const loadWarnUsers = useCallback(() => {
    api.moderation.warnings(guildId).then(setWarnUsers).catch(() => {});
  }, [guildId]);

  const openUser = async (u: WarnUser) => {
    setSelected(u);
    setLoadingDetails(true);
    try {
      const d = await api.moderation.userWarnings(guildId, u.user_id);
      setDetails(d);
    } finally {
      setLoadingDetails(false);
    }
  };

  const deleteOne = async (id: number) => {
    if (!selected) return;
    await api.moderation.deleteWarning(guildId, selected.user_id, id);
    const updated = details.filter((d) => d.id !== id);
    setDetails(updated);
    if (updated.length === 0) {
      setSelected(null);
      loadWarnUsers();
    } else {
      setWarnUsers((prev) =>
        prev.map((u) =>
          u.user_id === selected.user_id ? { ...u, count: updated.length } : u
        )
      );
      setSelected((prev) => prev ? { ...prev, count: updated.length } : null);
    }
  };

  const clearAll = async () => {
    if (!selected) return;
    await api.moderation.clearWarnings(guildId, selected.user_id);
    setSelected(null);
    loadWarnUsers();
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
      {selected && !loadingDetails && (
        <WarnModal
          user={selected}
          warnings={details}
          onClose={() => setSelected(null)}
          onDeleteOne={deleteOne}
          onClearAll={clearAll}
        />
      )}

      <div>
        <h1 className="text-xl font-bold text-white flex items-center gap-2">
          <Shield size={20} className="text-accent" /> 관리 설정
        </h1>
        <p className="text-muted text-sm mt-1">매니저 역할, 로그 채널, 자동 관리를 설정합니다.</p>
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

      {/* ── 경고 임계값 ── */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white">경고 자동 처리</h2>
        <p className="text-xs text-muted">0으로 설정하면 비활성화됩니다.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">자동 추방 경고 횟수</label>
            <input
              type="number" min={0} className="input"
              placeholder="0 (비활성화)"
              value={cfg.warn_kick_threshold ?? 0}
              onChange={(e) => set("warn_kick_threshold")(Number(e.target.value))}
            />
            <p className="text-xs text-muted mt-1">해당 횟수 경고 시 자동 추방</p>
          </div>
          <div>
            <label className="label">자동 차단 경고 횟수</label>
            <input
              type="number" min={0} className="input"
              placeholder="0 (비활성화)"
              value={cfg.warn_ban_threshold ?? 0}
              onChange={(e) => set("warn_ban_threshold")(Number(e.target.value))}
            />
            <p className="text-xs text-muted mt-1">해당 횟수 경고 시 자동 차단</p>
          </div>
        </div>
      </div>

      {/* ── Auto-Mod ── */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white">자동 관리 (Auto-Mod)</h2>
        <label className="flex items-center justify-between cursor-pointer p-3 rounded-lg bg-bg border border-border">
          <div>
            <p className="text-sm font-medium">자동 관리 활성화</p>
            <p className="text-xs text-muted mt-0.5">금지어·멘션 도배·스팸 자동 감지 및 처리</p>
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
          <p className="text-xs text-muted mt-1">
            감지 시 메시지 삭제 + 경고 1회 자동 부여. 경고 3회 누적 시 10분 뮤트.
          </p>
        </div>
      </div>

      <button onClick={save} disabled={saving} className="btn-primary">
        {saved
          ? <><CheckCircle size={16} /> 저장됨</>
          : <><Save size={16} /> {saving ? "저장 중..." : "변경사항 저장"}</>}
      </button>

      {/* ── 경고 현황 ── */}
      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold text-white flex items-center gap-2">
            <AlertTriangle size={16} className="text-yellow-400" /> 경고 현황
          </h2>
          <button onClick={loadWarnUsers} className="text-xs text-muted hover:text-white transition-colors">
            새로고침
          </button>
        </div>

        {warnUsers.length === 0 ? (
          <p className="text-muted text-sm text-center py-6">경고를 받은 유저가 없습니다.</p>
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
      </div>
    </div>
  );
}
