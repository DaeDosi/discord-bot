"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { Save, CheckCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { GuildConfig, Channel, Role } from "@/lib/types";

function SelectField({
  label, value, onChange, options, placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { id: string; name: string }[];
  placeholder: string;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>{o.name}</option>
        ))}
      </select>
    </div>
  );
}

export default function GeneralSettingsPage() {
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
        mod_role_id:     c.mod_role_id     ? String(c.mod_role_id)     : "",
        welcome_channel: c.welcome_channel ? String(c.welcome_channel) : "",
        goodbye_channel: c.goodbye_channel ? String(c.goodbye_channel) : "",
        log_channel:     c.log_channel     ? String(c.log_channel)     : "",
        auto_role_id:    c.auto_role_id    ? String(c.auto_role_id)    : "",
        levelup_channel: c.levelup_channel ? String(c.levelup_channel) : "",
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
        <h1 className="text-xl font-bold text-white">일반 설정</h1>
        <p className="text-muted text-sm mt-1">서버의 기본 봇 설정을 관리합니다.</p>
      </div>

      {/* 채널 설정 */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white">채널 설정</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SelectField label="환영 메시지 채널" value={cfg.welcome_channel || ""} onChange={set("welcome_channel")}
            options={channels} placeholder="채널 선택..." />
          <SelectField label="퇴장 메시지 채널" value={cfg.goodbye_channel || ""} onChange={set("goodbye_channel")}
            options={channels} placeholder="채널 선택..." />
          <SelectField label="중재 로그 채널"   value={cfg.log_channel || ""}     onChange={set("log_channel")}
            options={channels} placeholder="채널 선택..." />
          <SelectField label="레벨업 알림 채널" value={cfg.levelup_channel || ""} onChange={set("levelup_channel")}
            options={channels} placeholder="현재 채널 (기본)" />
        </div>
      </div>

      <button onClick={save} disabled={saving} className="btn-primary">
        {saved ? <><CheckCircle size={16} /> 저장됨</> : <><Save size={16} /> {saving ? "저장 중..." : "변경사항 저장"}</>}
      </button>
    </div>
  );
}
