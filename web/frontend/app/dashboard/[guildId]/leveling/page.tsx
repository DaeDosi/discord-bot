"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Plus, Trash2, Zap, Trophy, X } from "lucide-react";
import { api } from "@/lib/api";
import type { LevelReward, Role } from "@/lib/types";

export default function LevelingPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [rewards, setRewards]   = useState<LevelReward[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);
  const [lb, setLb]             = useState<{ user_id: string; display_name?: string; xp: number; level: number }[]>([]);
  const [newLevel, setNewLevel] = useState("");
  const [newRole, setNewRole]   = useState("");
  const [adding, setAdding]     = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadLb = () =>
    api.settings.leaderboard(guildId).then(setLb).catch(() => {});

  useEffect(() => {
    Promise.all([
      api.settings.levelRewards.list(guildId),
      api.guilds.roles(guildId),
      api.settings.leaderboard(guildId),
    ]).then(([r, roles, lb]) => { setRewards(r); setRoles(roles); setLb(lb); });
  }, [guildId]);

  const addReward = async () => {
    if (!newLevel || !newRole) return;
    setAdding(true);
    await api.settings.levelRewards.add(guildId, { level: parseInt(newLevel), role_id: newRole });
    const r = await api.settings.levelRewards.list(guildId);
    setRewards(r);
    setNewLevel(""); setNewRole("");
    setAdding(false);
  };

  const remove = async (level: number) => {
    await api.settings.levelRewards.remove(guildId, level);
    setRewards((p) => p.filter((r) => r.level !== level));
  };

  const deleteLbEntry = async (userId: string) => {
    setDeletingId(userId);
    await api.settings.deleteLeaderboard(guildId, userId).catch(() => {});
    setLb((p) => p.filter((e) => String(e.user_id) !== String(userId)));
    setDeletingId(null);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-white">레벨업 시스템</h1>
        <p className="text-muted text-sm mt-1">레벨 보상 역할 및 리더보드를 관리합니다.</p>
      </div>

      {/* 레벨 보상 */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white flex items-center gap-2">
          <Zap size={16} className="text-warning" /> 레벨 보상 역할
        </h2>
        <div className="space-y-2">
          {rewards.map((r) => {
            const role = roles.find((ro) => String(ro.id) === String(r.role_id));
            return (
              <div key={r.level}
                className="flex items-center justify-between bg-bg rounded-lg px-4 py-3 border border-border">
                <div className="flex items-center gap-3">
                  <span className="text-sm font-semibold text-accent w-20">레벨 {r.level}</span>
                  {role && (
                    <span className="text-sm px-2 py-0.5 rounded-full border border-border"
                          style={{ color: role.color ? `#${role.color.toString(16).padStart(6,"0")}` : "#fff" }}>
                      @{role.name}
                    </span>
                  )}
                </div>
                <button onClick={() => remove(r.level)} className="text-muted hover:text-danger transition-colors p-1">
                  <Trash2 size={14} />
                </button>
              </div>
            );
          })}
          {rewards.length === 0 && (
            <p className="text-muted text-sm text-center py-4">등록된 레벨 보상이 없습니다.</p>
          )}
        </div>

        {/* 추가 폼 */}
        <div className="flex flex-col md:flex-row gap-3 pt-2 border-t border-border">
          <input
            type="number" min="1" max="500"
            className="input md:w-28"
            placeholder="레벨"
            value={newLevel}
            onChange={(e) => setNewLevel(e.target.value)}
          />
          <select className="select md:flex-1" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
            <option value="">역할 선택...</option>
            {roles.map((r) => <option key={r.id} value={r.id}>@{r.name}</option>)}
          </select>
          <button onClick={addReward} disabled={adding || !newLevel || !newRole} className="btn-primary">
            <Plus size={16} /> 추가
          </button>
        </div>
      </div>

      {/* 리더보드 */}
      <div className="card space-y-3">
        <h2 className="font-semibold text-white flex items-center gap-2">
          <Trophy size={16} className="text-warning" /> 서버 리더보드 (상위 20)
        </h2>
        <div className="space-y-2">
          {lb.map((entry, i) => (
            <div key={entry.user_id}
              className="flex items-center gap-3 bg-bg rounded-lg px-4 py-2.5 border border-border
                         hover:border-danger/30 group transition-colors">
              <span className={`text-sm font-bold w-8 ${i < 3 ? "text-warning" : "text-muted"}`}>
                #{i + 1}
              </span>
              <span className="text-sm text-white flex-1 truncate">{entry.display_name || entry.user_id}</span>
              <span className="text-xs text-muted">Lv.{entry.level}</span>
              <span className="text-xs text-accent font-medium">{entry.xp.toLocaleString()} XP</span>
              <button
                onClick={() => deleteLbEntry(String(entry.user_id))}
                disabled={deletingId === String(entry.user_id)}
                title="XP 삭제"
                className="opacity-0 group-hover:opacity-100 text-muted hover:text-danger transition-all p-1 shrink-0"
              >
                <X size={13} />
              </button>
            </div>
          ))}
          {lb.length === 0 && (
            <p className="text-muted text-sm text-center py-4">아직 XP 데이터가 없습니다.</p>
          )}
        </div>
      </div>
    </div>
  );
}
