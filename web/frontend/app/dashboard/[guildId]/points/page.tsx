"use client";
import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import {
  Gem, Plus, Edit2, Trash2, Check, X, CheckCircle,
  Trophy, ClipboardList, Users, ShoppingBag, Image as ImageIcon,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Mission, MissionSubmission, PointsEntry, ShopItem, ShopExchange, GuildMember } from "@/lib/types";
import MemberSearch from "@/components/MemberSearch";

type Tab = "missions" | "submissions" | "leaderboard" | "adjust" | "shop";

// ── Mission form modal ────────────────────────────────────────────────────────
function MissionModal({
  initial,
  onSave,
  onClose,
}: {
  initial?: Mission;
  onSave: (data: { title: string; description: string; points: number }) => Promise<void>;
  onClose: () => void;
}) {
  const [title, setTitle]   = useState(initial?.title ?? "");
  const [desc, setDesc]     = useState(initial?.description ?? "");
  const [points, setPoints] = useState(String(initial?.points ?? ""));
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!title.trim()) return;
    setSaving(true);
    await onSave({ title: title.trim(), description: desc.trim(), points: Number(points) || 0 });
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
            <label className="label">설명 (선택)</label>
            <textarea
              className="input min-h-[72px] resize-y"
              placeholder="미션 설명..."
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
          </div>
          <div>
            <label className="label">지급 포인트</label>
            <input
              className="input"
              placeholder="0"
              inputMode="numeric"
              value={points}
              onChange={(e) => setPoints(e.target.value.replace(/[^0-9]/g, ""))}
            />
          </div>
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

// ── Shop item form modal ──────────────────────────────────────────────────────
function ShopItemModal({
  initial,
  onSave,
  onClose,
}: {
  initial?: ShopItem;
  onSave: (data: { name: string; description: string; image_url: string; points_cost: number; stock: number }) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName]         = useState(initial?.name ?? "");
  const [desc, setDesc]         = useState(initial?.description ?? "");
  const [imageUrl, setImageUrl] = useState(initial?.image_url ?? "");
  const [cost, setCost]         = useState(String(initial?.points_cost ?? ""));
  const [stock, setStock]       = useState(initial?.stock === -1 ? "" : String(initial?.stock ?? ""));
  const [saving, setSaving]     = useState(false);

  const submit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    await onSave({
      name: name.trim(),
      description: desc.trim(),
      image_url: imageUrl.trim(),
      points_cost: Number(cost) || 0,
      stock: stock === "" || stock === "0" ? -1 : Number(stock),
    });
    setSaving(false);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-bg-card border border-border rounded-xl w-full max-w-md shadow-xl">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <p className="font-semibold text-white">{initial ? "아이템 수정" : "아이템 추가"}</p>
          <button onClick={onClose} className="text-muted hover:text-white transition-colors"><X size={18} /></button>
        </div>
        <div className="p-4 space-y-3">
          <div>
            <label className="label">아이템 이름</label>
            <input className="input" placeholder="예: 애교권, 굿즈 등" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <label className="label">설명 (선택)</label>
            <textarea
              className="input min-h-[60px] resize-y"
              placeholder="아이템 설명..."
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
            />
          </div>
          <div>
            <label className="label flex items-center gap-1"><ImageIcon size={13} /> 이미지 URL (선택)</label>
            <input
              className="input"
              placeholder="https://..."
              value={imageUrl}
              onChange={(e) => setImageUrl(e.target.value)}
            />
            {imageUrl && (
              <div className="mt-2 w-16 h-16 rounded-lg overflow-hidden border border-border bg-bg">
                <img src={imageUrl} alt="" className="w-full h-full object-cover"
                     onError={(e) => (e.currentTarget.style.display = "none")} />
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">필요 포인트</label>
              <input
                className="input" placeholder="0" inputMode="numeric"
                value={cost}
                onChange={(e) => setCost(e.target.value.replace(/[^0-9]/g, ""))}
              />
            </div>
            <div>
              <label className="label">재고 (빈칸=무제한)</label>
              <input
                className="input" placeholder="무제한" inputMode="numeric"
                value={stock}
                onChange={(e) => setStock(e.target.value.replace(/[^0-9]/g, ""))}
              />
            </div>
          </div>
        </div>
        <div className="p-4 border-t border-border flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary text-sm">취소</button>
          <button onClick={submit} disabled={saving || !name.trim()} className="btn-primary text-sm">
            {saving ? "저장 중..." : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Adjust modal ──────────────────────────────────────────────────────────────
function AdjustModal({
  guildId,
  onSave,
  onClose,
}: {
  guildId: string;
  onSave: (data: { user_id: string; amount: number; reason: string }) => Promise<void>;
  onClose: () => void;
}) {
  const [member, setMember] = useState<GuildMember | null>(null);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!member) return;
    setSaving(true);
    await onSave({ user_id: member.id, amount: Number(amount) || 0, reason: reason.trim() });
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
            <label className="label">서버 멤버 검색</label>
            <MemberSearch guildId={guildId} value={member} onChange={setMember} />
          </div>
          <div>
            <label className="label">포인트 (음수: 차감)</label>
            <input
              className="input" placeholder="예: 100 또는 -50"
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/[^0-9\-]/g, ""))}
            />
          </div>
          <div>
            <label className="label">사유 (선택)</label>
            <input className="input" placeholder="사유 입력..." value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
        </div>
        <div className="p-4 border-t border-border flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary text-sm">취소</button>
          <button onClick={submit} disabled={saving || !member} className="btn-primary text-sm">
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
  const [tab, setTab] = useState<Tab>("missions");

  const [missions, setMissions]       = useState<Mission[]>([]);
  const [submissions, setSubmissions] = useState<MissionSubmission[]>([]);
  const [leaderboard, setLeaderboard] = useState<PointsEntry[]>([]);
  const [shopItems, setShopItems]     = useState<ShopItem[]>([]);
  const [exchanges, setExchanges]     = useState<ShopExchange[]>([]);

  const [missionModal, setMissionModal] = useState<"new" | Mission | null>(null);
  const [shopModal, setShopModal]       = useState<"new" | ShopItem | null>(null);
  const [adjustModal, setAdjustModal]   = useState(false);
  const [toast, setToast]               = useState("");

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 2500); };

  const loadMissions    = useCallback(() => api.points.missions.list(guildId).then(setMissions).catch(() => {}), [guildId]);
  const loadSubmissions = useCallback(() => api.points.submissions.list(guildId).then(setSubmissions).catch(() => {}), [guildId]);
  const loadLeaderboard = useCallback(() => api.points.leaderboard(guildId).then(setLeaderboard).catch(() => {}), [guildId]);
  const loadShopItems   = useCallback(() => api.points.shop.items.list(guildId).then(setShopItems).catch(() => {}), [guildId]);
  const loadExchanges   = useCallback(() => api.points.shop.exchanges.list(guildId).then(setExchanges).catch(() => {}), [guildId]);

  useEffect(() => { loadMissions(); }, [loadMissions]);
  useEffect(() => { if (tab === "submissions") loadSubmissions(); }, [tab, loadSubmissions]);
  useEffect(() => { if (tab === "leaderboard") loadLeaderboard(); }, [tab, loadLeaderboard]);
  useEffect(() => { if (tab === "shop") { loadShopItems(); loadExchanges(); } }, [tab, loadShopItems, loadExchanges]);

  const saveMission = async (data: { title: string; description: string; points: number }) => {
    const payload = { ...data, is_active: true };
    if (missionModal === "new") {
      await api.points.missions.create(guildId, payload);
    } else if (missionModal && typeof missionModal === "object") {
      await api.points.missions.update(guildId, missionModal.id, payload);
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

  const saveShopItem = async (data: { name: string; description: string; image_url: string; points_cost: number; stock: number }) => {
    if (shopModal === "new") {
      await api.points.shop.items.create(guildId, data);
    } else if (shopModal && typeof shopModal === "object") {
      await api.points.shop.items.update(guildId, shopModal.id, data);
    }
    setShopModal(null);
    loadShopItems();
    showToast("아이템이 저장되었습니다.");
  };

  const deleteShopItem = async (id: number) => {
    if (!confirm("이 아이템을 삭제하시겠습니까?")) return;
    await api.points.shop.items.delete(guildId, id);
    loadShopItems();
    showToast("아이템이 삭제되었습니다.");
  };

  const markUsed = async (id: number) => {
    await api.points.shop.exchanges.markUsed(guildId, id);
    setExchanges((prev) => prev.map((e) => e.id === id ? { ...e, is_used: 1 } : e));
    showToast("사용 처리되었습니다.");
  };

  const approve = async (id: number) => {
    await api.points.submissions.approve(guildId, id);
    setSubmissions((prev) => prev.map((s) => s.id === id ? { ...s, status: "approved" } : s));
    showToast("승인 완료. 포인트가 지급됩니다.");
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

  const pendingCount = submissions.filter((s) => s.status === "pending").length;

  const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: "missions",    label: "미션 관리", icon: <ClipboardList size={14} /> },
    { key: "submissions", label: "신청 현황", icon: <CheckCircle size={14} />   },
    { key: "leaderboard", label: "순위표",    icon: <Trophy size={14} />        },
    { key: "adjust",      label: "수동 지급", icon: <Users size={14} />         },
    { key: "shop",        label: "상점",      icon: <ShoppingBag size={14} />   },
  ];

  return (
    <div className="space-y-6">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 z-50 px-4 py-2 rounded-lg bg-green-600 text-white text-sm shadow-lg">
          {toast}
        </div>
      )}

      {missionModal !== null && (
        <MissionModal
          initial={missionModal === "new" ? undefined : missionModal}
          onSave={saveMission}
          onClose={() => setMissionModal(null)}
        />
      )}
      {shopModal !== null && (
        <ShopItemModal
          initial={shopModal === "new" ? undefined : shopModal}
          onSave={saveShopItem}
          onClose={() => setShopModal(null)}
        />
      )}
      {adjustModal && (
        <AdjustModal guildId={guildId} onSave={adjust} onClose={() => setAdjustModal(false)} />
      )}

      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-white flex items-center gap-2">
          <Gem size={20} className="text-accent" /> 포인트 관리
        </h1>
        <p className="text-muted text-sm mt-1">미션, 신청 승인, 리더보드, 포인트 상점 관리</p>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-1 bg-bg rounded-xl p-1 border border-border w-fit">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              tab === t.key ? "bg-accent/15 text-accent" : "text-muted hover:text-white"
            }`}
          >
            {t.icon}
            {t.label}
            {t.key === "submissions" && pendingCount > 0 && (
              <span className="absolute -top-1 -right-1 w-4 h-4 flex items-center justify-center text-[10px] font-bold rounded-full bg-accent text-white">
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
                <div key={m.id} className="flex items-start justify-between gap-3 p-3 rounded-lg bg-bg border border-border">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-white truncate">{m.title}</p>
                    {m.description && <p className="text-sm text-muted mt-0.5 line-clamp-2">{m.description}</p>}
                    <p className="text-sm text-accent font-semibold mt-1">{m.points.toLocaleString()} 포인트</p>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <button onClick={() => setMissionModal(m)}
                      className="p-1.5 rounded-lg text-muted hover:text-white hover:bg-bg-hover transition-colors">
                      <Edit2 size={14} />
                    </button>
                    <button onClick={() => deleteMission(m.id)}
                      className="p-1.5 rounded-lg text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors">
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
            <button onClick={loadSubmissions} className="text-sm text-muted hover:text-white transition-colors">새로고침</button>
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
                    <th className="pb-2 font-medium">처리</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {submissions.map((s) => (
                    <tr key={s.id} className="hover:bg-bg-hover transition-colors">
                      <td className="py-2.5 pr-3 text-white font-medium truncate max-w-[100px]">{s.user_name}</td>
                      <td className="py-2.5 pr-3 text-muted truncate max-w-[130px]">{s.title}</td>
                      <td className="py-2.5 pr-3 text-accent font-semibold">{s.points.toLocaleString()}</td>
                      <td className="py-2.5 pr-3 text-muted text-xs whitespace-nowrap">
                        {new Date(s.submitted_at * 1000).toLocaleDateString("ko-KR")}
                      </td>
                      <td className="py-2.5">
                        {s.status === "pending" ? (
                          <div className="flex gap-1">
                            <button onClick={() => approve(s.id)}
                              className="p-1 rounded-md bg-green-500/10 text-green-400 hover:bg-green-500/20 transition-colors" title="승인">
                              <Check size={14} />
                            </button>
                            <button onClick={() => reject(s.id)}
                              className="p-1 rounded-md bg-red-500/10 text-red-400 hover:bg-red-500/20 transition-colors" title="거절">
                              <X size={14} />
                            </button>
                          </div>
                        ) : s.status === "approved" ? (
                          <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/15 text-green-400 border border-green-500/20">승인</span>
                        ) : (
                          <span className="text-xs px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/20">거절</span>
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
            <button onClick={loadLeaderboard} className="text-sm text-muted hover:text-white transition-colors">새로고침</button>
          </div>
          {leaderboard.length === 0 ? (
            <p className="text-muted text-sm text-center py-8">포인트 데이터가 없습니다.</p>
          ) : (
            <div className="space-y-2">
              {leaderboard.map((e, i) => (
                <div key={e.user_id} className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-bg border border-border">
                  <span className={`w-6 text-center text-sm font-bold ${
                    i === 0 ? "text-yellow-400" : i === 1 ? "text-gray-300" : i === 2 ? "text-amber-600" : "text-muted"
                  }`}>{i + 1}</span>
                  <p className="flex-1 text-sm text-white font-medium truncate">{e.display_name}</p>
                  <p className="text-sm font-bold text-accent">{e.points.toLocaleString()} P</p>
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
          <p className="text-muted text-sm">특정 멤버에게 포인트를 지급하거나 차감합니다.</p>
          <button onClick={() => setAdjustModal(true)} className="btn-primary flex items-center gap-2">
            <Gem size={15} /> 포인트 지급 / 차감
          </button>
        </div>
      )}

      {/* ── 상점 ── */}
      {tab === "shop" && (
        <div className="space-y-6">
          {/* 아이템 관리 */}
          <div className="card space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-white flex items-center gap-2">
                <ShoppingBag size={16} className="text-accent" /> 아이템 관리
              </h2>
              <button onClick={() => setShopModal("new")} className="btn-primary text-sm flex items-center gap-1.5">
                <Plus size={14} /> 아이템 추가
              </button>
            </div>
            <p className="text-sm text-muted">
              디스코드에서 <code className="bg-bg px-1.5 py-0.5 rounded text-accent text-xs">/포인트상점</code> 명령어로 멤버에게 상점을 공개합니다.
            </p>
            {shopItems.length === 0 ? (
              <p className="text-muted text-sm text-center py-8">등록된 아이템이 없습니다.</p>
            ) : (
              <div className="space-y-2">
                {shopItems.map((item) => (
                  <div key={item.id} className="flex items-start justify-between gap-3 p-3 rounded-lg bg-bg border border-border">
                    <div className="flex gap-3 items-start min-w-0">
                      {item.image_url ? (
                        <img src={item.image_url} alt="" className="w-12 h-12 rounded-lg object-cover shrink-0 border border-border" />
                      ) : (
                        <div className="w-12 h-12 rounded-lg bg-bg-hover border border-border shrink-0 flex items-center justify-center">
                          <ShoppingBag size={20} className="text-muted" />
                        </div>
                      )}
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-white truncate">{item.name}</p>
                        {item.description && <p className="text-sm text-muted mt-0.5 line-clamp-1">{item.description}</p>}
                        <div className="flex items-center gap-3 mt-1">
                          <span className="text-sm font-semibold text-accent">{item.points_cost.toLocaleString()} P</span>
                          <span className="text-xs text-muted">
                            재고: {item.stock === -1 ? "무제한" : `${item.stock}개`}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex gap-1 shrink-0">
                      <button onClick={() => setShopModal(item)}
                        className="p-1.5 rounded-lg text-muted hover:text-white hover:bg-bg-hover transition-colors">
                        <Edit2 size={14} />
                      </button>
                      <button onClick={() => deleteShopItem(item.id)}
                        className="p-1.5 rounded-lg text-muted hover:text-red-400 hover:bg-red-500/10 transition-colors">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 교환 내역 */}
          <div className="card space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-white">교환 내역</h2>
              <button onClick={loadExchanges} className="text-sm text-muted hover:text-white transition-colors">새로고침</button>
            </div>
            {exchanges.length === 0 ? (
              <p className="text-muted text-sm text-center py-6">교환 내역이 없습니다.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-muted border-b border-border">
                      <th className="pb-2 font-medium">유저</th>
                      <th className="pb-2 font-medium">아이템</th>
                      <th className="pb-2 font-medium">포인트</th>
                      <th className="pb-2 font-medium">교환일</th>
                      <th className="pb-2 font-medium">상태</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {exchanges.map((ex) => (
                      <tr key={ex.id} className="hover:bg-bg-hover transition-colors">
                        <td className="py-2.5 pr-3 text-white font-medium truncate max-w-[100px]">{ex.user_name}</td>
                        <td className="py-2.5 pr-3">
                          <div className="flex items-center gap-2">
                            {ex.image_url && (
                              <img src={ex.image_url} alt="" className="w-6 h-6 rounded object-cover shrink-0" />
                            )}
                            <span className="text-muted truncate max-w-[110px]">{ex.item_name}</span>
                          </div>
                        </td>
                        <td className="py-2.5 pr-3 text-accent font-semibold">{ex.points_cost.toLocaleString()}</td>
                        <td className="py-2.5 pr-3 text-muted text-xs whitespace-nowrap">
                          {new Date(ex.exchanged_at * 1000).toLocaleDateString("ko-KR")}
                        </td>
                        <td className="py-2.5">
                          {ex.is_used ? (
                            <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/15 text-green-400 border border-green-500/20">사용됨</span>
                          ) : (
                            <button
                              onClick={() => markUsed(ex.id)}
                              className="text-xs px-2 py-0.5 rounded-full bg-bg-hover text-muted border border-border hover:border-accent/40 hover:text-white transition-colors"
                            >
                              미사용 → 처리
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
