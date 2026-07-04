"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Save, CheckCircle, ToggleLeft, ToggleRight, Eye, EyeOff, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import type { VerificationConfig, Channel, Role } from "@/lib/types";

const DEFAULT_COLOR = "#5865F2";
const DEFAULT_TITLE = "🔐 입장 인증";

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
      {description && <p className="text-sm text-muted mb-1.5">{description}</p>}
      <select className="select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>{o.name}</option>
        ))}
      </select>
    </div>
  );
}

// ── Discord 임베드 미리보기 (실제 렌더링을 근사한 모형) ──────────────────────
function DiscordEmbedPreview({
  title, color, message,
}: { title: string; color: string; message: string }) {
  const safeColor = /^#[0-9A-Fa-f]{6}$/.test(color) ? color : DEFAULT_COLOR;
  return (
    <div className="rounded-2xl overflow-hidden border border-white/8 shadow-xl w-full max-w-md"
         style={{ background: "#313338" }}>
      <div className="flex items-center gap-2 px-4 py-2.5" style={{ background: "#2b2d31" }}>
        <span className="text-[11px]" style={{ color: "#80848e" }}>#입장-인증</span>
      </div>
      <div className="p-4">
        <div className="flex gap-3">
          <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center"
               style={{ background: `${safeColor}25` }}>
            <ShieldCheck size={16} style={{ color: safeColor }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-white mb-2">NexBot</p>
            <div className="rounded-lg border-l-[4px] p-3" style={{ background: "#2b2d31", borderLeftColor: safeColor }}>
              <p className="text-white text-[15px] font-bold mb-1.5 leading-snug break-words">
                {title || DEFAULT_TITLE}
              </p>
              <p className="text-[13px] leading-relaxed break-words whitespace-pre-wrap" style={{ color: "#dbdee1" }}>
                {message || "아래 버튼을 눌러 입장을 확인해 주세요."}
              </p>
            </div>
            <button
              type="button"
              disabled
              className="mt-3 px-4 py-1.5 rounded text-sm font-medium text-white cursor-default"
              style={{ background: safeColor }}
            >
              입장 확인
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function VerificationPage() {
  const { guildId } = useParams<{ guildId: string }>();

  const [cfg, setCfg]           = useState<VerificationConfig>({
    embed_color: DEFAULT_COLOR,
    embed_title: DEFAULT_TITLE,
  });
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);
  const [saving, setSaving]     = useState(false);
  const [saved, setSaved]       = useState(false);
  const [error, setError]       = useState("");
  const [showPreview, setShowPreview] = useState(false);

  useEffect(() => {
    Promise.all([
      api.settings.getVerification(guildId),
      api.guilds.channels(guildId),
      api.guilds.roles(guildId),
    ]).then(([v, ch, r]) => {
      setCfg({
        ...v,
        verification_channel:   v.verification_channel ? String(v.verification_channel) : "",
        unverified_role_id:     v.unverified_role_id   ? String(v.unverified_role_id)   : "",
        verified_role_id:       v.verified_role_id     ? String(v.verified_role_id)     : "",
        use_chzzk_verification: Boolean(v.use_chzzk_verification),
        verification_message:   v.verification_message ?? "",
        embed_color:            v.embed_color || DEFAULT_COLOR,
        embed_title:            v.embed_title || DEFAULT_TITLE,
      });
      setChannels(ch.filter((c) => c.type === 0 || c.type === 5));
      setRoles(r);
    }).catch(() => setError("설정을 불러오는 데 실패했습니다."));
  }, [guildId]);

  const set = (key: keyof VerificationConfig) => (v: string | boolean) =>
    setCfg((p) => ({ ...p, [key]: v }));

  const handleColorText = (val: string) => {
    if (/^#?[0-9A-Fa-f]{0,6}$/.test(val)) {
      set("embed_color")(val.startsWith("#") ? val : `#${val}`);
    }
  };

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
        <h1 className="page-title">입장 인증</h1>
        <p className="page-subtitle">
          신규 멤버 입장 인증 흐름을 설정합니다.
          설정 저장 후 Discord에서 <code className="text-accent">/입장메시지설정</code> 명령어를 실행하면
          아래 설정값으로 입장 채널에 인증 임베드가 전송(또는 수정)됩니다.
        </p>
      </div>

      {error && (
        <p className="text-sm text-danger bg-danger/10 border border-danger/30 rounded-lg px-4 py-3">
          {error}
        </p>
      )}

      {/* 채널 · 역할 */}
      <div className="card space-y-4">
        <h2 className="section-title">채널 &amp; 역할</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SelectField
            label="입장 인증 채널"
            value={cfg.verification_channel || ""}
            onChange={set("verification_channel")}
            options={channels}
            placeholder="채널 선택..."
            description="인증 임베드가 전송될 채널"
          />
          <div />
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
        <h2 className="section-title">인증 방식</h2>
        <div
          className="flex items-center justify-between p-3 rounded-lg border border-border
                     hover:bg-bg-hover transition-colors cursor-pointer"
          onClick={() => set("use_chzzk_verification")(!cfg.use_chzzk_verification)}
        >
          <div>
            <p className="text-sm font-medium text-fg">치지직 연동 인증</p>
            <p className="text-sm text-muted mt-0.5">
              ON이면 네이버 로그인을 통한 치지직 인증, OFF이면 즉시 확인 버튼
            </p>
          </div>
          {cfg.use_chzzk_verification
            ? <ToggleRight size={28} className="text-accent shrink-0" />
            : <ToggleLeft  size={28} className="text-muted  shrink-0" />}
        </div>
      </div>

      {/* 임베드 디자인 */}
      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="section-title">임베드 디자인</h2>
          <button
            type="button"
            onClick={() => setShowPreview((v) => !v)}
            className="flex items-center gap-1.5 text-sm font-medium text-accent hover:text-fg transition-colors"
          >
            {showPreview ? <EyeOff size={15} /> : <Eye size={15} />}
            {showPreview ? "미리보기 닫기" : "미리보기"}
          </button>
        </div>

        <div>
          <label className="label">임베드 제목</label>
          <input
            type="text"
            className="input"
            placeholder={DEFAULT_TITLE}
            value={cfg.embed_title || ""}
            onChange={(e) => set("embed_title")(e.target.value)}
            maxLength={100}
          />
        </div>

        <div>
          <label className="label">임베드 색상</label>
          <div className="flex items-center gap-3">
            <input
              type="color"
              value={/^#[0-9A-Fa-f]{6}$/.test(cfg.embed_color || "") ? cfg.embed_color! : DEFAULT_COLOR}
              onChange={(e) => set("embed_color")(e.target.value)}
              className="w-10 h-10 rounded-lg border border-border cursor-pointer p-0.5 bg-transparent"
            />
            <input
              type="text"
              value={cfg.embed_color || ""}
              onChange={(e) => handleColorText(e.target.value)}
              className="input w-32 font-mono uppercase"
              placeholder={DEFAULT_COLOR}
              maxLength={7}
            />
            <div
              className="w-8 h-8 rounded-lg border border-border shrink-0"
              style={{ backgroundColor: /^#[0-9A-Fa-f]{6}$/.test(cfg.embed_color || "") ? cfg.embed_color : DEFAULT_COLOR }}
            />
          </div>
          <p className="text-sm text-muted mt-1.5">예: #5865F2 (Discord 파란색), #FF0000 (빨간색)</p>
        </div>

        {showPreview && (
          <div className="pt-2 border-t border-border">
            <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">
              디스코드 미리보기 (실제 렌더링과 약간 다를 수 있음)
            </p>
            <DiscordEmbedPreview
              title={cfg.embed_title || ""}
              color={cfg.embed_color || DEFAULT_COLOR}
              message={cfg.verification_message || ""}
            />
          </div>
        )}
      </div>

      {/* 입장 메시지 */}
      <div className="card space-y-3">
        <h2 className="section-title">입장 메시지</h2>
        <p className="text-sm text-muted">
          인증 임베드 본문에 표시될 텍스트입니다. 비워두면 기본 메시지가 사용됩니다.
        </p>
        <textarea
          className="input min-h-[100px] resize-y"
          placeholder="아래 버튼을 눌러 입장을 확인해 주세요."
          value={cfg.verification_message || ""}
          onChange={(e) => set("verification_message")(e.target.value)}
        />
        <p className="text-sm text-muted/60">
          메시지 수정 후 Discord에서 <code className="text-accent">/입장메시지설정</code> 을 실행하면 기존 임베드가 업데이트됩니다.
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
