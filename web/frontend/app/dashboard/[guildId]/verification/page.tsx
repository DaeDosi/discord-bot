"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Save, CheckCircle, ToggleLeft, ToggleRight } from "lucide-react";
import { api } from "@/lib/api";
import type { VerificationConfig, Channel, Role } from "@/lib/types";

function SelectField({
  label, value, onChange, options, placeholder, description,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { id: string; name: string }[];
  placeholder: string;
  description?: string;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      {description && <p className="text-xs text-muted mb-1.5">{description}</p>}
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>{o.name}</option>
        ))}
      </select>
    </div>
  );
}

export default function VerificationPage() {
  const { guildId } = useParams<{ guildId: string }>();

  const [cfg, setCfg]           = useState<VerificationConfig>({});
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);
  const [saving, setSaving]     = useState(false);
  const [saved, setSaved]       = useState(false);
  const [error, setError]       = useState("");

  useEffect(() => {
    Promise.all([
      api.settings.getVerification(guildId),
      api.guilds.channels(guildId),
      api.guilds.roles(guildId),
    ]).then(([v, ch, r]) => {
      setCfg({
        ...v,
        verification_channel: v.verification_channel ? String(v.verification_channel) : "",
        unverified_role_id:   v.unverified_role_id   ? String(v.unverified_role_id)   : "",
        verified_role_id:     v.verified_role_id     ? String(v.verified_role_id)     : "",
        use_chzzk_verification: Boolean(v.use_chzzk_verification),
        verification_message:   v.verification_message ?? "",
      });
      setChannels(ch.filter((c) => c.type === 0 || c.type === 5));
      setRoles(r);
    }).catch(() => setError("설정을 불러오는 데 실패했습니다."));
  }, [guildId]);

  const set = (key: keyof VerificationConfig) => (v: string | boolean) =>
    setCfg((p) => ({ ...p, [key]: v }));

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await api.settings.saveVerification(guildId, cfg);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch {
      setError("저장에 실패했습니다. 다시 시도해주세요.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-white">입장 인증</h1>
        <p className="text-muted text-sm mt-1">
          신규 멤버 입장 인증 흐름을 설정합니다.
          채널·역할 초기 지정은 Discord에서 <code className="text-accent">/setup</code> 명령어로,
          임베드 전송은 <code className="text-accent">/embed</code> 명령어로 실행하세요.
        </p>
      </div>

      {error && (
        <p className="text-sm text-danger bg-danger/10 border border-danger/30 rounded-lg px-4 py-3">
          {error}
        </p>
      )}

      {/* 채널 · 역할 */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white">채널 &amp; 역할</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SelectField
            label="입장 인증 채널"
            value={cfg.verification_channel || ""}
            onChange={set("verification_channel")}
            options={channels}
            placeholder="채널 선택..."
            description="인증 임베드가 전송될 채널"
          />
          <div /> {/* spacer */}
          <SelectField
            label="미인증 역할"
            value={cfg.unverified_role_id || ""}
            onChange={set("unverified_role_id")}
            options={roles}
            placeholder="역할 선택..."
            description="입장 즉시 자동 부여되는 역할"
          />
          <SelectField
            label="인증됨 역할"
            value={cfg.verified_role_id || ""}
            onChange={set("verified_role_id")}
            options={roles}
            placeholder="역할 선택..."
            description="인증 완료 후 부여되는 역할"
          />
        </div>
      </div>

      {/* 인증 방식 */}
      <div className="card space-y-4">
        <h2 className="font-semibold text-white">인증 방식</h2>
        <div
          className="flex items-center justify-between p-3 rounded-lg border border-border
                     hover:bg-bg-hover transition-colors cursor-pointer"
          onClick={() => set("use_chzzk_verification")(!cfg.use_chzzk_verification)}
        >
          <div>
            <p className="text-sm font-medium text-white">치지직 연동 인증</p>
            <p className="text-xs text-muted mt-0.5">
              ON이면 웹사이트 치지직 인증 링크 버튼, OFF이면 즉시 확인 버튼
            </p>
          </div>
          {cfg.use_chzzk_verification
            ? <ToggleRight size={28} className="text-accent shrink-0" />
            : <ToggleLeft  size={28} className="text-muted  shrink-0" />}
        </div>
      </div>

      {/* 입장 메시지 */}
      <div className="card space-y-3">
        <h2 className="font-semibold text-white">입장 메시지</h2>
        <p className="text-xs text-muted">
          인증 임베드 본문에 표시될 텍스트입니다. 비워두면 기본 메시지가 사용됩니다.
        </p>
        <textarea
          className="input min-h-[100px] resize-y"
          placeholder="아래 버튼을 눌러 입장을 확인해 주세요."
          value={cfg.verification_message || ""}
          onChange={(e) => set("verification_message")(e.target.value)}
        />
        <p className="text-xs text-muted/60">
          메시지 수정 후 Discord에서 <code className="text-accent">/embed</code> 를 실행하면 기존 임베드가 업데이트됩니다.
        </p>
      </div>

      <button onClick={save} disabled={saving} className="btn-primary">
        {saved
          ? <><CheckCircle size={16} /> 저장됨</>
          : <><Save size={16} /> {saving ? "저장 중..." : "변경사항 저장"}</>}
      </button>
    </div>
  );
}
