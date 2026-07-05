"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Swords, Shield, Dices, Save, CheckCircle, AlertTriangle, ExternalLink } from "lucide-react";
import { api } from "@/lib/api";

type McEventStatus = {
  invited: boolean;
  event_name?: string;
  is_active?: boolean;
  mc_player_name?: string;
  streamer_connected?: boolean;
  triggers?: { kind: "debuff" | "buff" | "random"; trigger_text: string }[];
  items?: { item_type: "debuff" | "buff"; name: string; points_cost: number; in_random_pool: number }[];
};

const KIND_LABEL: Record<string, string> = { debuff: "디버프지급", buff: "버프지급", random: "랜덤아이템" };

export default function McEventPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [status, setStatus]   = useState<McEventStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [playerName, setPlayerName] = useState("");
  const [saving, setSaving]   = useState(false);
  const [saved, setSaved]     = useState(false);

  const load = () =>
    api.chzzk.mcEvent.status(guildId)
      .then((d) => { setStatus(d); setPlayerName(d.mc_player_name || ""); })
      .catch(() => setStatus({ invited: false }))
      .finally(() => setLoading(false));

  useEffect(() => { load(); }, [guildId]);

  const save = async () => {
    if (!playerName.trim()) return;
    setSaving(true);
    try {
      await api.chzzk.mcEvent.savePlayerName(guildId, playerName.trim());
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      alert("저장에 실패했습니다.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <p className="text-muted text-center py-10">불러오는 중...</p>;
  }

  if (!status?.invited) {
    return (
      <div className="space-y-2">
        <h1 className="page-title">이벤트 서버 (합방)</h1>
        <p className="page-subtitle">이 서버는 현재 진행 중인 마인크래프트 콜라보 이벤트에 초대되지 않았습니다.</p>
      </div>
    );
  }

  const debuffItems = status.items?.filter((i) => i.item_type === "debuff") ?? [];
  const buffItems   = status.items?.filter((i) => i.item_type === "buff") ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title flex items-center gap-2">
          <Swords size={20} className="text-accent" /> 이벤트 서버 (합방)
        </h1>
        <p className="page-subtitle">
          <span className="text-fg font-medium">{status.event_name}</span> 콜라보 이벤트에 참가 등록된 서버입니다.
          {status.is_active ? (
            <span className="ml-2 text-xs px-2 py-0.5 rounded-full bg-success/10 text-success border border-success/20">진행 중</span>
          ) : (
            <span className="ml-2 text-xs px-2 py-0.5 rounded-full bg-bg-hover text-muted border border-border">대기 중</span>
          )}
        </p>
      </div>

      {!status.streamer_connected && (
        <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 flex items-start gap-3">
          <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="text-fg font-medium">치지직 계정 연동이 필요합니다</p>
            <p className="text-muted mt-1">
              방송 채팅에서 이벤트 명령어를 받으려면 스트리머 본인이 치지직 계정을 연동해야 합니다.
            </p>
            <Link href={`/dashboard/${guildId}/chzzk`} className="text-accent text-sm inline-flex items-center gap-1 mt-2 hover:underline">
              치지직 탭에서 연동하기 <ExternalLink size={13} />
            </Link>
          </div>
        </div>
      )}

      {/* 마크 플레이어 이름 */}
      <div className="card space-y-3">
        <h2 className="section-title">내 마크 플레이어 이름</h2>
        <p className="text-sm text-muted">
          이벤트 효과가 적용될 본인의 마인크래프트 닉네임을 입력해주세요. 정확히 일치해야 효과가 제대로 적용됩니다.
        </p>
        <div className="flex items-center gap-2 max-w-sm">
          <input
            className="input flex-1 font-mono"
            placeholder="마크 닉네임"
            value={playerName}
            onChange={(e) => setPlayerName(e.target.value)}
          />
          <button onClick={save} disabled={saving || !playerName.trim()} className="btn-primary shrink-0">
            {saved ? <><CheckCircle size={15} /> 저장됨</> : <><Save size={15} /> {saving ? "저장 중..." : "저장"}</>}
          </button>
        </div>
      </div>

      {/* 채팅 명령어 안내 */}
      <div className="card space-y-3">
        <h2 className="section-title">치지직 채팅 명령어</h2>
        <p className="text-sm text-muted">
          시청자가 방송 채팅에서 아래 명령어를 입력하면 보유 포인트를 사용해 이벤트 효과를 발동합니다.
          (치지직 계정을 연동한 시청자만 가능)
        </p>
        <div className="flex flex-wrap gap-2">
          {(status.triggers ?? []).map((t, i) => (
            <span key={i} className="text-sm font-mono px-3 py-1.5 rounded-lg bg-bg border border-border text-fg">
              !{t.trigger_text} <span className="text-xs text-muted">({KIND_LABEL[t.kind]})</span>
            </span>
          ))}
          {(!status.triggers || status.triggers.length === 0) && (
            <p className="text-sm text-muted">등록된 명령어가 없습니다.</p>
          )}
        </div>
      </div>

      {/* 아이템 카탈로그 (읽기 전용) */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card space-y-3">
          <h2 className="section-title flex items-center gap-2"><Swords size={15} className="text-danger" /> 디버프 아이템</h2>
          <div className="space-y-2">
            {debuffItems.map((item, i) => (
              <div key={i} className="flex items-center justify-between bg-bg rounded-lg px-3 py-2 border border-border text-sm">
                <span className="text-fg">{item.name}</span>
                <span className="flex items-center gap-2">
                  <span className="text-accent font-semibold">{item.points_cost.toLocaleString()}P</span>
                  {!!item.in_random_pool && <Dices size={12} className="text-muted" />}
                </span>
              </div>
            ))}
            {debuffItems.length === 0 && <p className="text-sm text-muted text-center py-3">등록된 아이템이 없습니다.</p>}
          </div>
        </div>
        <div className="card space-y-3">
          <h2 className="section-title flex items-center gap-2"><Shield size={15} className="text-success" /> 버프 아이템</h2>
          <div className="space-y-2">
            {buffItems.map((item, i) => (
              <div key={i} className="flex items-center justify-between bg-bg rounded-lg px-3 py-2 border border-border text-sm">
                <span className="text-fg">{item.name}</span>
                <span className="flex items-center gap-2">
                  <span className="text-accent font-semibold">{item.points_cost.toLocaleString()}P</span>
                  {!!item.in_random_pool && <Dices size={12} className="text-muted" />}
                </span>
              </div>
            ))}
            {buffItems.length === 0 && <p className="text-sm text-muted text-center py-3">등록된 아이템이 없습니다.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}
