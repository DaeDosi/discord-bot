"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { Save, CheckCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { GuildConfig, Channel } from "@/lib/types";

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

  const [cfg, setCfg]           = useState<GuildConfig>({});
  const [channels, setChannels] = useState<Channel[]>([]);
  const [saving, setSaving]     = useState(false);
  const [saved, setSaved]       = useState(false);

  useEffect(() => {
    Promise.all([
      api.settings.get(guildId),
      api.guilds.channels(guildId),
    ]).then(([c, ch]) => {
      setCfg({
        ...c,
        welcome_channel: c.welcome_channel ? String(c.welcome_channel) : "",
        goodbye_channel: c.goodbye_channel ? String(c.goodbye_channel) : "",
        log_channel:     c.log_channel     ? String(c.log_channel)     : "",
        levelup_channel: c.levelup_channel ? String(c.levelup_channel) : "",
      });
      setChannels(ch);
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

  const textChannels = channels.filter((c) => c.type === 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title">일반 설정</h1>
        <p className="page-subtitle">서버의 기본 봇 설정을 관리합니다.</p>
      </div>

      {/* 채널 설정 */}
      <div className="card space-y-4">
        <h2 className="section-title">채널 설정</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SelectField label="환영 메시지 채널" value={cfg.welcome_channel || ""} onChange={set("welcome_channel")}
            options={textChannels} placeholder="채널 선택..." />
          <SelectField label="퇴장 메시지 채널" value={cfg.goodbye_channel || ""} onChange={set("goodbye_channel")}
            options={textChannels} placeholder="채널 선택..." />
          <SelectField label="중재 로그 채널"   value={cfg.log_channel || ""}     onChange={set("log_channel")}
            options={textChannels} placeholder="채널 선택..." />
          <SelectField label="애정도 레벨업 알림 채널" value={cfg.levelup_channel || ""} onChange={set("levelup_channel")}
            options={textChannels} placeholder="현재 채널 (기본)" />
        </div>
      </div>

      {/* 환영/퇴장 메시지 내용 */}
      <div className="card space-y-4">
        <h2 className="section-title">메시지 내용</h2>
        <p className="text-muted text-sm">
          사용 가능한 변수: <code className="bg-bg px-1 rounded">{"{mention}"}</code> 유저 멘션,{" "}
          <code className="bg-bg px-1 rounded">{"{username}"}</code> 유저 이름,{" "}
          <code className="bg-bg px-1 rounded">{"{server}"}</code> 서버 이름
        </p>
        <div>
          <label className="label">환영 메시지</label>
          <textarea
            className="select resize-none h-24 font-mono text-sm"
            value={cfg.welcome_message ?? ""}
            onChange={(e) => set("welcome_message")(e.target.value)}
            placeholder={"{mention}님이 **{server}**에 오셨습니다!\n\n서버의 규칙을 꼭 읽어주세요 😊"}
          />
        </div>
        <div>
          <label className="label">퇴장 메시지</label>
          <textarea
            className="select resize-none h-20 font-mono text-sm"
            value={cfg.goodbye_message ?? ""}
            onChange={(e) => set("goodbye_message")(e.target.value)}
            placeholder="**{username}**님이 서버를 떠났습니다."
          />
        </div>
      </div>

      {/* 레벨업 DM */}
      <div className="card space-y-4">
        <h2 className="section-title">애정도 레벨업 알림</h2>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={cfg.levelup_dm ?? false}
            onChange={(e) => set("levelup_dm")(e.target.checked)}
            className="w-4 h-4 accent-accent"
          />
          <span className="text-sm">애정도 레벨업 알림을 DM으로 전송</span>
        </label>
      </div>

      <button onClick={save} disabled={saving} className="btn-primary">
        {saved ? <><CheckCircle size={16} /> 저장됨</> : <><Save size={16} /> {saving ? "저장 중..." : "변경사항 저장"}</>}
      </button>
    </div>
  );
}
