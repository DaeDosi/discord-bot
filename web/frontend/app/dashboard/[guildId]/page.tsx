"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { Save, CheckCircle, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import DashboardError from "@/components/DashboardError";
import InlineError from "@/components/InlineError";
import { useMutation } from "@/lib/useMutation";
import { isHandledElsewhere } from "@/lib/dashboardErrors";
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
  const [loading, setLoading]   = useState(true);
  const [loadError, setLoadError]   = useState<unknown>(null);
  // 저장은 공통 계약으로 — 수동 try/catch는 중복 클릭을 막지 못했다(실측: 연타 3회 → 요청 3건).
  const saveM = useMutation();

  const load = useCallback(() => {
    setLoading(true);
    setLoadError(null);
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
    }).catch((e: unknown) => {
      // 401은 api.ts가 이미 토큰 삭제 + /login 이동을 처리한다.
      if (!isHandledElsewhere(e)) setLoadError(e);
    }).finally(() => setLoading(false));
  }, [guildId]);

  useEffect(() => { load(); }, [load]);

  // 실패를 삼키고 "저장됨"을 띄우면 사용자는 저장된 줄 안다(실측: PUT 500에도 저장됨).
  const save = () => saveM.run(() => api.settings.save(guildId, cfg));

  const set = (key: keyof GuildConfig) => (v: string | boolean) =>
    setCfg((p) => ({ ...p, [key]: v }));

  const textChannels = channels.filter((c) => c.type === 0);

  const header = (
    <div>
      <h1 className="page-title">일반 설정</h1>
      <p className="page-subtitle">서버의 기본 봇 설정을 관리합니다.</p>
    </div>
  );

  // 권한이 없거나 불러오지 못했으면 폼을 그리지 않는다 — 남겨 두면 저장 버튼이
  // 눌려서 "설정한 것 같은데 반영이 안 된다"가 된다(실측: 403에도 폼이 그대로였다).
  if (loadError) {
    return (
      <div className="space-y-6">
        {header}
        <DashboardError error={loadError} onRetry={load} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {header}

      {/* 자리를 항상 잡아 둔다 — 로딩 줄이 나타났다 사라지면 아래 카드가 통째로
          위아래로 밀린다(실측 CLS 0.0191). UI-Q에서 같은 결론이 났다. */}
      <p
        className={"flex items-center gap-2 text-muted text-sm" + (loading ? "" : " invisible")}
        aria-hidden={loading ? undefined : true}
      >
        <Loader2 size={16} className={loading ? "animate-spin" : ""} />
        {loading ? "설정을 불러오는 중입니다." : " "}
      </p>

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
            options={textChannels} placeholder="채널 선택..." />
        </div>
      </div>

      {/* 환영/퇴장 메시지 내용 */}
      <div className="card space-y-4">
        <h2 className="section-title">메시지 내용</h2>
        <p className="text-muted text-base">
          사용 가능한 변수: <code className="bg-bg px-1 rounded">{"{mention}"}</code> 유저 멘션,{" "}
          <code className="bg-bg px-1 rounded">{"{username}"}</code> 유저 이름,{" "}
          <code className="bg-bg px-1 rounded">{"{server}"}</code> 서버 이름
        </p>
        <div>
          <label className="label">환영 메시지</label>
          <textarea
            className="select resize-none h-24 font-mono"
            value={cfg.welcome_message ?? ""}
            onChange={(e) => set("welcome_message")(e.target.value)}
            placeholder={"{mention}님이 **{server}**에 오셨습니다!\n\n서버의 규칙을 꼭 읽어주세요 😊"}
          />
        </div>
        <div>
          <label className="label">퇴장 메시지</label>
          <textarea
            className="select resize-none h-20 font-mono"
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
          <span className="text-base">애정도 레벨업 알림을 DM으로 전송</span>
        </label>
      </div>

      <InlineError message={saveM.error && `저장하지 못했습니다. ${saveM.error}`} />

      <button onClick={save} disabled={saveM.pending || loading} className="btn-primary">
        {saveM.succeeded ? <><CheckCircle size={16} /> 저장됨</> : <><Save size={16} /> {saveM.pending ? "저장 중..." : "변경사항 저장"}</>}
      </button>
    </div>
  );
}
