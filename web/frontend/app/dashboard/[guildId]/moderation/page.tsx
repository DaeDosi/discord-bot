"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Save, CheckCircle, Shield } from "lucide-react";
import { api } from "@/lib/api";
import type { GuildConfig, Channel, Role } from "@/lib/types";

export default function ModerationPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [cfg, setCfg]         = useState<GuildConfig>({});
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);
  const [saving, setSaving]     = useState(false);
  const [saved, setSaved]       = useState(false);

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
  }, [guildId]);

  const save = async () => {
    setSaving(true);
    await api.settings.save(guildId, cfg).catch(() => {});
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  const set = (key: keyof GuildConfig) => (v: string | boolean) =>
    setCfg((p) => ({ ...p, [key]: v }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-white flex items-center gap-2">
          <Shield size={20} className="text-accent" /> 관리 설정
        </h1>
        <p className="text-muted text-sm mt-1">중재자 역할, 로그 채널, 자동 관리를 설정합니다.</p>
      </div>

      <div className="card space-y-4">
        <h2 className="font-semibold text-white">중재 기본 설정</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">중재자 역할</label>
            <select className="select" value={cfg.mod_role_id || ""} onChange={(e) => set("mod_role_id")(e.target.value)}>
              <option value="">역할 선택...</option>
              {roles.map((r) => <option key={r.id} value={r.id}>@{r.name}</option>)}
            </select>
          </div>
          <div>
            <label className="label">중재 로그 채널</label>
            <select className="select" value={cfg.log_channel || ""} onChange={(e) => set("log_channel")(e.target.value)}>
              <option value="">채널 선택...</option>
              {channels.map((c) => <option key={c.id} value={c.id}>#{c.name}</option>)}
            </select>
          </div>
        </div>
      </div>

      <div className="card space-y-4">
        <h2 className="font-semibold text-white">자동 관리 (Auto-Mod)</h2>
        <div className="space-y-3">
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
        </div>
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
    </div>
  );
}
