"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import {
  Gem, Plus, Edit2, Trash2, Check, X, CheckCircle,
  Trophy, ClipboardList, Users,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Mission, MissionSubmission, PointsEntry } from "@/lib/types";

type Tab = "missions" | "submissions" | "leaderboard" | "adjust";

// ── Mission form modal ────────────────────────────────────────────────────────
function MissionModal({
  initial,
  onSave,
  onClose,
}: {
  initial?: Mission;
  onSave: (data: { title: string; description: string; points: number; is_active: boolean }) => Promise<void>;
  onClose: () => void;
}) {
  const [title, setTitle]       = useState(initial?.title ?? "");
  const [desc, setDesc]         = useState(initial?.description ?? "");
  const [points, setPoints]     = useState(initial?.points ?? 0);
  const [active, setActive]     = useState(initial ? !!initial.is_active : true);
  const [saving, setSaving]     = useState(false);

  const submit = async () => {
    if (!title.trim()) return;
    setSaving(true);
    await onSave({ title: title.trim(), description: desc.trim(), points, is_active: active });
    setSaving(false);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-bg-card border border-border rounded-xl w-full max-w-md shadow-xl">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <p className="font-semibold text-white">{initial ? "미션 수정" : "미션 추가"}</p>
          <button onClick={onClose} className="text-muted hover:text-white transition-colors"><X size={18} /></button>
        </div>
        <div className="p-4 space-y-3">
          <div>
            <label className="label">미션 제목</label>
            <input className="input" placeholder="미션 이름" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div>
            <label className="label">설명</label>
            <textarea
              className="input min-h-[72px] resize-y"
              placeholder="미션 설명 (선택)"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
          </div>
          <div>
            <label className="label">포인트</label>
            <input
              type="number" min={0} className="input"
              value={points}
              onChange={(e) => setPoints(Number(e.target.value))}
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox" className="sr-only peer" checked={active}
              onChange={(e) => setActive(e.target.checked)}
            />
            <div className="relative w-9 h-5">
              <div className="w-9 h-5 bg-border rounded-full peer-checked:bg-accent transition-colors" />
              <div className="absolute left-0.5 top-0.5 w-4 h-4 bg-white rounded-full transition-transform peer-checked:translate-x-4" />
            </div>
            <span className="text-sm text-white">활성화</span>
          </label>
        </div>
        <div className="p-4 border-t border-border flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary text-sm">취소</button>
          <button onClick={submit} disabled={saving || !title.trim()} className="btn-primary text-sm">
            {saving ? "저장 중..." : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Adjust modal ──────────────────────────────────────────────────────────────
function AdjustModal({
  onSave,
  onClose,
}: {
  onSave: (data: { user_id: string; amount: number; reason: string }) => Promise<void>;
  onClose: () => void;
}) {
  const [userId, setUserId]   = useState("");
  const [amount, setAmount]   = useState(0);
  const [reason, setReason]   = useState("");
  const [saving, setSaving]   = useState(false);

  const submit = async () => {
    if (!userId.trim()) return;
    setSaving(true);
    await onSave({ user_id: userId.trim(), amount, reason: reason.trim() });
    setSaving(false);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-bg-card border border-border rounded-xl w-full max-w-sm shadow-xl">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <p className="font-semibold text-white">포인트 수동 지급</p>
          <button onClick={onClose} className="text-muted hover:text-white transition-colors"><X size={18} /></button>
        </div>
        <div className="p-4 space-y-3">
          <div>
            <label className="label">디스코드 유저 ID</label>
            <input className="input" placeholder="123456789012345678" value={userId} onChange={(e) => setUserId(e.target.value)} />
          </div>
          <div>
            <label className="label">포인트 (음수: 차감)</label>
            <input type="number" className="input" value={amount} onChange={(e) => setAmount(Number(e.target.value))} />
          </div>
          <div>
            <label className="label">사유 (선택)</label>
            <input className="input" placeholder="사유 입력..." value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
        </div>
        <div className="p-4 border-t border-border flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary text-sm">취소</button>
          <button onClick={submit} disabled={saving || !userId.trim()} className="btn-primary text-sm">
            {saving ? "처리 중..." : "지급"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function PointsPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [tab, setTab]             = useState<Tab>("missions");

  const [missions, setMissions]       = useState<Mission[]>([]);
  const [submissions, setSubmissions] = useState<MissionSubmission[]>([]);
  const [leaderboard, setLeaderboard] = useState<PointsEntry[]>([]);

  const [missionModal, setMissionModal] = useState<"new" | Mission | null>(null);
  const [adjustModal, setAdjustModal]   = useState(false);
  const [toast, setToast]               = useState("");

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2500);
  };

  const loadMissions = useCallback(() =>
    api.points.missions.list(guildId).then(setMissions).catch(() => {}), [guildId]);

  const loadSubmissions = useCallback(() =>
    api.points.submissions.list(guildId).then(setSubmissions).catch(() => {}), [guildId]);

  const loadLeaderboard = useCallback(() =>
    api.points.leaderboard(guildId).then(setLeaderboard).catch(() => {}), [guildId]);

  useEffect(() => { loadMissions(); }, [loadMissions]);
  useEffect(() => { if (tab === "submissions") loadSubmissions(); }, [tab, loadSubmissions]);
  useEffect(() => { if (tab === "leaderboard") loadLeaderboard(); }, [tab, loadLeaderboard]);

  const saveMission = async (data: { title: string; description: string; points: number; is_active: boolean }) => {
    if (missionModal === "new") {
      await api.points.missions.create(guildId, data);
    } else if (missionModal && typeof missionModal === "object") {
      await api.points.missions.update(guildId, missionModal.id, data);
    }
    setMissionModal(null);
    loadMissions();
    showToast("미션이 저장되었습니다.");
  };

  const deleteMission = async (id: number) => {
    if (!confirm("이 미션을 삭제하시겠습니까?")) return;
    await api.points.missions.delete(guildId, id);
    loadMissions();
    showToast("미션이 삭제되었습니다.");
  };

  const approve = async (id: number) => {
    await api.points.submissions.approve(guildId, id);
    setSubmissions((prev) => prev.map((s) => s.id === id ? { ...s, status: "approved" } : s));
    showToast("승인되었습니다. 포인트가 지급됩니다.");
  };

  const reject = async (id: number) => {
    await api.points.submissions.reject(guildId, id);
    setSubmissions((prev) => prev.map((s) => s.id === id ? { ...s, status: "rejected" } : s));
    showToast("거절되었습니다.");
  };

  const adjust = async (data: { user_id: string; amount: number; reason: string }) => {
    await api.points.adjust(guildId, data);
    setAdjustModal(false);
    showToast("포인트가 조정되었습니다.");
    if (tab === "leaderboard") loadLeaderboard();
  };

  const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: "missions",     label: "미션 관리",  icon: <ClipboardList size={15} /> },
    { key: "submissions",  label: "신청 현황",  icon: <CheckCircle size={15} />   },
    { key: "leaderboard",  label: "순위표",     icon: <Trophy size={15} />        },
    { key: "adjust",       label: "수동 지급",  icon: <Users size={15} />         },
  ];

  const pendingCount = submissions.filter((s) => s.status === "pending").length;

  return (
    <div className="space-y-6">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 z-50 px-4 py-2 rounded-lg bg-green-600 text-white text-sm shadow-lg">
          {toast}
        </div>
      )}

      {/* Mission modal */}
      {missionModal !== null && (
        <MissionModal
          initial={missionModal === "new" ? undefined : missionModal}
          onSave={saveMission}
          onClose={() => setMissionModal(null)}
        />
      )}

      {/* Adjust modal */}
      {adjustModal && (
        <AdjustModal onSave={adjust} onClose={() => setAdjustModal(false)} />
      )}

      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-white flex items-center gap-2">
          <Gem size={20} className="text-accent" /> 포인트 관리
        </h1>
        <p className="text-muted text-sm mt-1">미션 등록, 신청 승인, 리더보드, 포인트 수동 조정</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-bg rounded-xl p-1 border border-border w-fit flex-wrap">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors relative ${
              tab === t.key
                ? "bg-accent/15 text-accent"
                : "text-muted hover:text-white"
            }`}
          >
            {t.icon}
            {t.label}
            {t.key === "submissions" && pendingCount > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 flex items-center justify-center
                               text-[10px] font-bold rounded-full bg-accent text-white">
                {pendingCount}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── 미션 관리 ── */}
      {tab === "missions" && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-white">등록된 미션</h2>
            <button onClick={() => setMissionModal("new")} className="btn-primary text-sm flex items-center gap-1.5">
              <Plus size={14} /> 미션 추가
            </button>
          </div>

          {missions.length === 0 ? (
            <p className="text-muted text-sm text-center py-8">등록된 미션이 없습니다.</p>
          ) : (
            <div className="space-y-2">
              {missions.map((m) => (
                <div
                  key={m.id}
                  className="flex items-start justify-between gap-3 p-3 rounded-lg bg-bg border border-border"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-white truncate">{m.title}</p>
                      {!m.is_active && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-500/20 text-gray-400 border border-gray-500/20">
                          비활성
                        </span>
                      )}
                    </div>
                    {m.description && (
                      <p className="text-xs text-muted mt-0.5 line-clamp-2">{m.description}</p>
                    )}
                    <p className="text-xs text-accent font-semibold mt-1">{m.points} 포인트</p>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <button
                      onClick={() => setMissionModal(m)}
                      className="p-1.5 rounded-lg text-muted hover:text-white hover:bg-bg-hover transition-colors"
                    >
                      <Edit2 size={14} />
                    </button>
                    <button
                      onClick={() => deleteMission(m.id)}
                      className="p-1.5 rounded-lg text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 신청 현황 ── */}
      {tab === "submissions" && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-white">미션 신청 현황</h2>
            <button onClick={loadSubmissions} className="text-xs text-muted hover:text-white transition-colors">
              새로고침
            </button>
          </div>

          {submissions.length === 0 ? (
            <p className="text-muted text-sm text-center py-8">신청 내역이 없습니다.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-muted border-b border-border">
                    <th className="pb-2 font-medium">유저</th>
                    <th className="pb-2 font-medium">미션</th>
                    <th className="pb-2 font-medium">포인트</th>
                    <th className="pb-2 font-medium">신청일</th>
                    <th className="pb-2 font-medium">상태</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {submissions.map((s) => (
                    <tr key={s.id} className="hover:bg-bg-hover transition-colors">
                      <td className="py-2.5 pr-3 text-white font-medium truncate max-w-[120px]">{s.user_name}</td>
                      <td className="py-2.5 pr-3 text-muted truncate max-w-[140px]">{s.title}</td>
                      <td className="py-2.5 pr-3 text-accent font-semibold">{s.points}</td>
                      <td className="py-2.5 pr-3 text-muted text-xs whitespace-nowrap">
                        {new Date(s.submitted_at * 1000).toLocaleDateString("ko-KR")}
                      </td>
                      <td className="py-2.5">
                        {s.status === "pending" ? (
                          <div className="flex gap-1">
                            <button
                              onClick={() => approve(s.id)}
                              className="p-1 rounded-md bg-green-500/10 text-green-400 hover:bg-green-500/20 transition-colors"
                              title="승인"
                            >
                              <Check size={14} />
                            </button>
                            <button
                              onClick={() => reject(s.id)}
                              className="p-1 rounded-md bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors"
                              title="거절"
                            >
                              <X size={14} />
                            </button>
                          </div>
                        ) : s.status === "approved" ? (
                          <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/15 text-green-400 border border-green-500/20">
                            승인
                          </span>
                        ) : (
                          <span className="text-xs px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/20">
                            거절
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── 순위표 ── */}
      {tab === "leaderboard" && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-white">포인트 순위표</h2>
            <button onClick={loadLeaderboard} className="text-xs text-muted hover:text-white transition-colors">
              새로고침
            </button>
          </div>

          {leaderboard.length === 0 ? (
            <p className="text-muted text-sm text-center py-8">포인트 데이터가 없습니다.</p>
          ) : (
            <div className="space-y-2">
              {leaderboard.map((e, i) => (
                <div
                  key={e.user_id}
                  className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-bg border border-border"
                >
                  <span className={`w-6 text-center text-sm font-bold ${
                    i === 0 ? "text-yellow-400" : i === 1 ? "text-gray-300" : i === 2 ? "text-amber-600" : "text-muted"
                  }`}>
                    {i + 1}
                  </span>
                  <p className="flex-1 text-sm text-white font-medium truncate">{e.display_name}</p>
                  <p className="text-sm font-bold text-accent">{e.points.toLocaleString()} pt</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 수동 지급 ── */}
      {tab === "adjust" && (
        <div className="card space-y-4">
          <h2 className="font-semibold text-white">포인트 수동 조정</h2>
          <p className="text-muted text-sm">특정 유저에게 포인트를 지급하거나 차감합니다.</p>
          <button onClick={() => setAdjustModal(true)} className="btn-primary flex items-center gap-2">
            <Gem size={15} /> 포인트 지급 / 차감
          </button>
        </div>
      )}
    </div>
  );
}
