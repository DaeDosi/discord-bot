"use client";
import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Image from "next/image";
import { clsx } from "clsx";
import {
  Radio, Trash2, Bell, BellOff,
  ExternalLink, Plus, Users, X, ChevronLeft, ChevronRight,
  MessageSquare, Edit2, Sparkles, RefreshCw,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ChzzkSubscription, Channel, Role, FollowerRoles, FollowRoleTier, ChzzkVerification, ChatCommand } from "@/lib/types";

type DetailTab = "streamer" | "chat-commands";

const DETAIL_TABS: { key: DetailTab; label: string }[] = [
  { key: "streamer",      label: "스트리머 설정" },
  { key: "chat-commands", label: "실시간 채팅 명령어" },
];

// ── 팔로우 인증 현황 모달 ──────────────────────────────────────────────────────
function VerifModal({
  verifications,
  loading,
  followTiers,
  roles,
  onClose,
}: {
  verifications: ChzzkVerification[];
  loading: boolean;
  followTiers: FollowRoleTier[];
  roles: Role[];
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<ChzzkVerification | null>(null);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-bg-card border border-border rounded-xl w-full max-w-lg shadow-xl flex flex-col"
           style={{ maxHeight: "80vh" }}>
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-border shrink-0">
          {selected ? (
            <button
              onClick={() => setSelected(null)}
              className="flex items-center gap-1 text-muted hover:text-fg transition-colors"
            >
              <ChevronLeft size={16} /> 목록으로
            </button>
          ) : (
            <p className="font-semibold text-fg flex items-center gap-2">
              <Users size={16} className="text-accent" />
              팔로우 인원
              {!loading && (
                <span className="text-xs text-muted font-normal">({verifications.length}명)</span>
              )}
            </p>
          )}
          <button onClick={onClose} className="text-muted hover:text-fg transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 p-4">
          {loading ? (
            <p className="text-sm text-muted text-center py-8 animate-pulse">불러오는 중...</p>
          ) : verifications.length === 0 ? (
            <p className="text-sm text-muted text-center py-8">아직 인증한 유저가 없습니다.</p>
          ) : !selected ? (
            /* 목록 */
            <div className="space-y-2">
              {verifications.map((v) => {
                const qualifiedTier = [...followTiers]
                  .sort((a, b) => b.months - a.months)
                  .find((t) => v.tier_months >= t.months);
                const tierRole = qualifiedTier ? roles.find((r) => r.id === qualifiedTier.role_id) : null;
                return (
                  <button
                    key={v.user_id}
                    onClick={() => setSelected(v)}
                    className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg
                               bg-bg border border-border hover:border-accent/40 hover:bg-bg-hover
                               transition-colors text-left"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-fg truncate">{v.user_name}</p>
                      <p className="text-xs text-muted mt-0.5">
                        {v.is_following
                          ? `팔로우 ${v.follow_days}일 (${v.tier_months}개월)`
                          : "미팔로우"}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0 ml-2">
                      {tierRole && (
                        <span className="text-[11px] px-1.5 py-0.5 rounded-full border border-border"
                              style={{ color: tierRole.color ? `#${tierRole.color.toString(16).padStart(6,"0")}` : "#fff" }}>
                          @{tierRole.name}
                        </span>
                      )}
                      <ChevronRight size={14} className="text-muted" />
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            /* 상세 */
            <div className="space-y-4">
              <div>
                <p className="font-semibold text-fg text-lg">{selected.user_name}</p>
                <p className="font-mono text-xs text-muted select-all mt-0.5">{selected.user_id}</p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-bg rounded-lg p-3 border border-border">
                  <p className="text-xs text-muted mb-1">팔로우 시작일</p>
                  <p className="text-sm font-medium text-fg">
                    {selected.follow_date
                      ? new Date(selected.follow_date).toLocaleDateString("ko-KR")
                      : "—"}
                  </p>
                </div>
                <div className="bg-bg rounded-lg p-3 border border-border">
                  <p className="text-xs text-muted mb-1">팔로우 기간</p>
                  <p className="text-sm font-medium text-fg">
                    {selected.is_following
                      ? `${selected.follow_days}일 (${selected.tier_months}개월)`
                      : "—"}
                  </p>
                </div>
              </div>
              {followTiers.length > 0 && (
                <div>
                  <p className="text-xs text-muted mb-2">역할 티어 현황</p>
                  <div className="space-y-2">
                    {[...followTiers].sort((a, b) => a.months - b.months).map((tier) => {
                      const role = roles.find((r) => r.id === tier.role_id);
                      const qualified = selected.tier_months >= tier.months;
                      return (
                        <div key={tier.id}
                             className={clsx(
                               "flex items-center gap-3 px-3 py-2 rounded-lg border",
                               qualified ? "bg-accent/10 border-accent/30" : "border-border"
                             )}>
                          <span className={`text-xs font-semibold w-16 shrink-0 ${qualified ? "text-accent" : "text-muted"}`}>
                            {tier.months}개월+
                          </span>
                          {role && (
                            <span className="text-sm text-fg flex-1">@{role.name}</span>
                          )}
                          <span className={`text-[11px] font-bold ${qualified ? "text-accent" : "text-muted"}`}>
                            {qualified ? "✓ 충족" : "미충족"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="p-4 border-t border-border shrink-0 flex justify-end">
          <button onClick={onClose} className="btn-secondary text-sm">닫기</button>
        </div>
      </div>
    </div>
  );
}

// ── 스트리머 추가 폼 ──────────────────────────────────────────────────────────
function AddStreamerForm({ channels, guildId }: { channels: Channel[]; guildId: string }) {
  const textChannels = channels.filter((c) => c.type === 0);
  const [discordChannel, setDiscordChannel] = useState("");
  const [mentionEveryone, setMentionEveryone] = useState(false);

  const handleConnect = async () => {
    if (!discordChannel) return;
    try {
      const d = await api.chzzkAuth.getStreamerLoginUrl(guildId, discordChannel, mentionEveryone);
      window.location.href = d.url;
    } catch {}
  };

  return (
    <div className="card border-accent/30 space-y-4">
      <div>
        <p className="font-medium text-fg mb-1">치지직 스트리머 연동</p>
        <p className="text-sm text-muted">
          치지직 계정으로 로그인하면 해당 채널이 자동으로 등록됩니다.
        </p>
      </div>

      <div>
        <label className="label">알림 받을 Discord 채널 *</label>
        <select className="select" value={discordChannel} onChange={(e) => setDiscordChannel(e.target.value)}>
          <option value="">채널 선택...</option>
          {textChannels.map((c) => <option key={c.id} value={c.id}>#{c.name}</option>)}
        </select>
      </div>

      <button
        type="button"
        onClick={() => setMentionEveryone((v) => !v)}
        className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors ${
          mentionEveryone
            ? "border-accent bg-accent/10 text-accent"
            : "border-border text-muted hover:border-border/60"
        }`}
      >
        {mentionEveryone ? <Bell size={16} /> : <BellOff size={16} />}
        <span className="text-sm font-medium">
          {mentionEveryone ? "@everyone 멘션 켜짐" : "@everyone 멘션 끄기"}
        </span>
      </button>

      <button onClick={handleConnect} disabled={!discordChannel} className="btn-primary w-full justify-center">
        <ExternalLink size={16} />
        치지직 계정으로 연동하기
      </button>
    </div>
  );
}

// ── 채팅 명령어 편집 모달 ─────────────────────────────────────────────────────
function ChatCommandModal({
  commandType, initial, onSave, onClose,
}: {
  commandType: "checkin" | "reply";
  initial?: ChatCommand;
  onSave: (data: { trigger_text: string; reward_points: number; reward_xp: number; reply_text: string }) => Promise<void>;
  onClose: () => void;
}) {
  const [trigger, setTrigger]     = useState(initial?.trigger_text ?? (commandType === "checkin" ? "출석체크" : ""));
  const [points, setPoints]       = useState(String(initial?.reward_points ?? (commandType === "checkin" ? 500 : 0)));
  const [xp, setXp]               = useState(String(initial?.reward_xp ?? (commandType === "checkin" ? 50 : 0)));
  const [replyText, setReplyText] = useState(initial?.reply_text ?? "");
  const [saving, setSaving]       = useState(false);

  const submit = async () => {
    if (!trigger.trim()) return;
    if (commandType === "reply" && !replyText.trim()) return;
    setSaving(true);
    await onSave({
      trigger_text:  trigger.trim(),
      reward_points: Number(points) || 0,
      reward_xp:     Number(xp) || 0,
      reply_text:    replyText.trim(),
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
          <p className="font-semibold text-fg">
            {commandType === "checkin" ? "출석체크 설정" : (initial ? "명령어 수정" : "명령어 추가")}
          </p>
          <button onClick={onClose} className="text-muted hover:text-fg transition-colors"><X size={18} /></button>
        </div>
        <div className="p-4 space-y-3">
          <div>
            <label className="label">! 뒤에 올 명령어</label>
            <div className="flex items-center gap-2">
              <span className="text-muted">!</span>
              <input
                className="input"
                placeholder={commandType === "checkin" ? "출석체크" : "핑"}
                value={trigger}
                onChange={(e) => setTrigger(e.target.value)}
              />
            </div>
            <p className="text-xs text-muted mt-1">
              {commandType === "checkin"
                ? "치지직 채팅에서 이 단어를 입력하면 1일 1회 포인트+애정도가 지급됩니다. (예: 출첵, 출석체크, 피하 등 자유롭게 변경 가능)"
                : "치지직 채팅에서 이 단어를 입력하면 아래 응답 문구를 자동으로 채팅에 전송합니다."}
            </p>
          </div>
          {commandType === "checkin" ? (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">지급 포인트</label>
                <input className="input" inputMode="numeric" value={points}
                       onChange={(e) => setPoints(e.target.value.replace(/[^0-9]/g, ""))} />
              </div>
              <div>
                <label className="label">지급 애정도(XP)</label>
                <input className="input" inputMode="numeric" value={xp}
                       onChange={(e) => setXp(e.target.value.replace(/[^0-9]/g, ""))} />
              </div>
            </div>
          ) : (
            <div>
              <label className="label">자동 응답 문구 (최대 100자)</label>
              <input
                className="input" placeholder="예: 퐁!" maxLength={100}
                value={replyText} onChange={(e) => setReplyText(e.target.value)}
              />
            </div>
          )}
        </div>
        <div className="p-4 border-t border-border flex justify-end gap-2">
          <button onClick={onClose} className="btn-secondary text-sm">취소</button>
          <button
            onClick={submit}
            disabled={saving || !trigger.trim() || (commandType === "reply" && !replyText.trim())}
            className="btn-primary text-sm"
          >
            {saving ? "저장 중..." : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 실시간 채팅 명령어 패널 ───────────────────────────────────────────────────
type ChatStatus = {
  registered: boolean; connected: boolean;
  last_sync_at: number | null; last_event_at: number | null;
  today_checkins: number;
  recent_checkins: { user_name: string; checked_at: number }[];
};

function timeAgo(unixSeconds: number | null): string {
  if (!unixSeconds) return "아직 없음";
  const diff = Date.now() / 1000 - unixSeconds;
  if (diff < 60) return "방금 전";
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return `${Math.floor(diff / 86400)}일 전`;
}

// ── 연결 상태 카드 ────────────────────────────────────────────────────────────
function ChatStatusCard({ guildId }: { guildId: string }) {
  const [status, setStatus] = useState<ChatStatus | null>(null);

  const load = () => api.chzzk.chatStatus(guildId).then(setStatus).catch(() => {});

  useEffect(() => {
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [guildId]);

  if (!status) {
    return <div className="card text-center py-6 text-muted text-sm">연결 상태 확인 중...</div>;
  }

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <span className={`w-2.5 h-2.5 rounded-full ${status.connected ? "bg-success animate-pulse" : "bg-danger"}`} />
          <p className="text-sm font-semibold text-fg">
            {status.connected ? "치지직 채팅 연결됨" : "연결 안 됨"}
          </p>
          <button onClick={load} className="text-muted hover:text-fg transition-colors" title="새로고침">
            <RefreshCw size={13} />
          </button>
        </div>
        <div className="flex items-center gap-4 text-xs text-muted">
          <span>마지막 채팅 감지: {timeAgo(status.last_event_at)}</span>
          <span>오늘 출석: <span className="text-accent font-semibold">{status.today_checkins}</span>명</span>
        </div>
      </div>
      {!status.connected && (
        <p className="text-xs text-warning">
          봇이 아직 이 채널의 채팅 세션을 확인하지 못했습니다. 스트리머 계정이 채팅 조회/쓰기 권한으로 연동돼 있는지,
          봇이 켜져 있는지 확인해주세요. (연동 화면에서 "치지직 계정으로 연동하기"를 다시 눌러야 새 권한이 적용됩니다)
        </p>
      )}
      {status.recent_checkins.length > 0 && (
        <div className="pt-2 border-t border-border space-y-1">
          <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-1">오늘 출석 기록</p>
          {status.recent_checkins.map((c, i) => (
            <div key={i} className="flex items-center justify-between text-sm">
              <span className="text-fg">{c.user_name}</span>
              <span className="text-muted text-xs">{timeAgo(c.checked_at)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type ChatLogEntry = { direction: "in" | "out"; nickname: string; content: string; created_at: number };

// ── 실시간 채팅 미리보기 (디버그용, 기본 숨김 — 버튼으로 펼침) ─────────────────
function ChzzkChatFeed({ guildId }: { guildId: string }) {
  const [open, setOpen] = useState(false);
  const [log, setLog] = useState<ChatLogEntry[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const load = () => api.chzzk.chatLog(guildId).then(setLog).catch(() => {});
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [guildId, open]);

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ block: "end" });
  }, [log, open]);

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="section-title flex items-center gap-2">
          <MessageSquare size={16} className="text-accent" /> 실시간 채팅 미리보기
        </h2>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 text-sm font-medium text-accent hover:text-fg transition-colors"
        >
          {open ? "닫기" : "미리보기 열기"}
        </button>
      </div>
      {!open ? (
        <p className="text-sm text-muted">
          치지직 채팅에서 실제로 수신된 메시지와 봇의 응답을 확인하려면 위 버튼을 눌러주세요.
        </p>
      ) : (
      <>
      <p className="text-sm text-muted">
        치지직 채팅에서 실제로 수신된 메시지와 봇이 보낸 응답을 그대로 보여줍니다. 3초마다 자동 갱신됩니다.
      </p>
      <div
        className="rounded-lg p-3 h-72 overflow-y-auto flex flex-col gap-1.5 font-mono text-[13px]"
        style={{ background: "#0b0d14" }}
      >
        {log.length === 0 ? (
          <p className="text-muted text-sm m-auto">
            아직 수신된 채팅이 없습니다. 방송 채팅창에 메시지를 입력해보세요.
          </p>
        ) : (
          log.map((l, i) => (
            <div key={i} className="flex items-baseline gap-2 leading-snug">
              <span className="text-white/20 shrink-0 text-[11px]">
                {new Date(l.created_at * 1000).toLocaleTimeString("ko-KR", { hour12: false })}
              </span>
              <span
                className="shrink-0 font-semibold"
                style={{ color: l.direction === "out" ? "#5865F2" : "#818CF8" }}
              >
                {l.nickname}
              </span>
              <span className="text-white/80 break-all">{l.content}</span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
      </>
      )}
    </div>
  );
}

function ChatCommandsPanel({ guildId }: { guildId: string }) {
  const [commands, setCommands] = useState<ChatCommand[]>([]);
  const [loading, setLoading]   = useState(true);
  const [modal, setModal]       = useState<{ type: "checkin" | "reply"; initial?: ChatCommand } | null>(null);
  const [toast, setToast]       = useState("");

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 2500); };

  const load = () =>
    api.chzzk.chatCommands.list(guildId).then(setCommands).catch(() => {}).finally(() => setLoading(false));

  useEffect(() => { load(); }, [guildId]);

  const checkinCmd = commands.find((c) => c.command_type === "checkin");
  const replyCmds  = commands.filter((c) => c.command_type === "reply");

  const save = async (data: { trigger_text: string; reward_points: number; reward_xp: number; reply_text: string }) => {
    if (!modal) return;
    try {
      if (modal.initial) {
        await api.chzzk.chatCommands.update(guildId, modal.initial.id, { ...data, is_active: true });
      } else {
        await api.chzzk.chatCommands.create(guildId, { command_type: modal.type, ...data, is_active: true });
      }
      setModal(null);
      load();
      showToast("저장되었습니다.");
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "저장 실패");
    }
  };

  const remove = async (id: number) => {
    if (!confirm("이 명령어를 삭제하시겠습니까?")) return;
    await api.chzzk.chatCommands.remove(guildId, id).catch(() => {});
    load();
  };

  if (loading) {
    return <div className="card text-center py-10 text-muted">불러오는 중...</div>;
  }

  return (
    <div className="space-y-6">
      {toast && (
        <div className="fixed top-4 right-4 z-50 px-4 py-2 rounded-lg bg-green-600 text-white text-sm shadow-lg">
          {toast}
        </div>
      )}
      {modal && (
        <ChatCommandModal
          commandType={modal.type}
          initial={modal.initial}
          onSave={save}
          onClose={() => setModal(null)}
        />
      )}

      <ChatStatusCard guildId={guildId} />
      <ChzzkChatFeed guildId={guildId} />

      {/* 출석체크 */}
      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="section-title flex items-center gap-2">
            <Sparkles size={16} className="text-accent" /> 출석체크
          </h2>
          <button
            onClick={() => setModal({ type: "checkin", initial: checkinCmd })}
            className="btn-primary text-sm flex items-center gap-1.5"
          >
            <Edit2 size={14} /> {checkinCmd ? "설정 수정" : "설정하기"}
          </button>
        </div>
        <p className="text-sm text-muted">
          치지직 생방송 채팅에서 시청자가{" "}
          <code className="bg-bg px-1.5 py-0.5 rounded text-accent text-xs">!{checkinCmd?.trigger_text || "출석체크"}</code>
          을 입력하면 (하루 1회) 포인트와 애정도를 지급합니다. 대시보드에서 치지직 계정을 연동한 시청자만 지급 대상입니다.
        </p>
        {checkinCmd ? (
          <div className="flex items-center gap-3 bg-bg rounded-lg px-4 py-3 border border-border">
            <span className="text-sm font-mono text-fg">!{checkinCmd.trigger_text}</span>
            <span className="text-sm text-accent font-semibold">+{checkinCmd.reward_points.toLocaleString()} P</span>
            <span className="text-sm text-warning font-semibold">+{checkinCmd.reward_xp.toLocaleString()} 애정도</span>
          </div>
        ) : (
          <p className="text-muted text-sm text-center py-4">아직 설정되지 않았습니다. 위 버튼으로 설정해주세요.</p>
        )}
      </div>

      {/* 추가 명령어 (자동 응답) */}
      <div className="card space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="section-title flex items-center gap-2">
            <MessageSquare size={16} className="text-accent" /> 추가 명령어 (자동 응답)
          </h2>
          {replyCmds.length < 5 && (
            <button
              onClick={() => setModal({ type: "reply" })}
              className="btn-primary text-sm flex items-center gap-1.5"
            >
              <Plus size={14} /> 명령어 추가
            </button>
          )}
        </div>
        <p className="text-sm text-muted">
          출석체크 외에 원하는 명령어를 등록하면, 치지직 채팅에 해당 단어가 입력됐을 때 지정한 문구로 자동 응답합니다. (최대 5개)
        </p>
        {replyCmds.length === 0 ? (
          <p className="text-muted text-sm text-center py-4">등록된 명령어가 없습니다.</p>
        ) : (
          <div className="space-y-2">
            {replyCmds.map((c) => (
              <div key={c.id} className="flex items-center justify-between gap-3 p-3 rounded-lg bg-bg border border-border">
                <div className="min-w-0">
                  <p className="text-sm font-mono text-fg">!{c.trigger_text}</p>
                  <p className="text-sm text-muted mt-0.5 truncate">→ {c.reply_text}</p>
                </div>
                <div className="flex gap-1 shrink-0">
                  <button onClick={() => setModal({ type: "reply", initial: c })}
                    className="p-1.5 rounded-lg text-muted hover:text-fg hover:bg-bg-hover transition-colors">
                    <Edit2 size={14} />
                  </button>
                  <button onClick={() => remove(c.id)}
                    className="p-1.5 rounded-lg text-muted hover:text-danger hover:bg-danger/10 transition-colors">
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── 메인 페이지 ───────────────────────────────────────────────────────────────
export default function ChzzkPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [detailTab, setDetailTab] = useState<DetailTab>("streamer");

  const [subs, setSubs]         = useState<ChzzkSubscription[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [roles, setRoles]       = useState<Role[]>([]);

  const [followTiers, setFollowTiers] = useState<FollowRoleTier[]>([]);
  const [newMonths, setNewMonths]     = useState("");
  const [newRole, setNewRole]         = useState("");
  const [addingTier, setAddingTier]   = useState(false);

  const [verifications, setVerifications] = useState<ChzzkVerification[]>([]);
  const [verifOpen, setVerifOpen]         = useState(false);

  const textChannels = channels.filter((c) => c.type === 0);

  const load = async () => {
    const [s, ch, r, ft, v] = await Promise.all([
      api.chzzk.list(guildId),
      api.guilds.channels(guildId),
      api.guilds.roles(guildId),
      api.chzzk.followTiers.list(guildId).catch(() => [] as FollowRoleTier[]),
      api.chzzk.verifications(guildId).catch(() => [] as ChzzkVerification[]),
    ]);
    setSubs(s);
    setChannels(ch);
    setRoles(r);
    setFollowTiers(ft);
    setVerifications(v);
  };

  const openVerif = () => setVerifOpen(true);

  useEffect(() => {
    load();
    const params = new URLSearchParams(window.location.search);
    if (params.get("success") === "streamer_added" || params.get("success") === "token_refreshed") {
      window.history.replaceState({}, "", window.location.pathname);
      load();
    }
  }, [guildId]);

  const addTier = async () => {
    if (!newMonths || !newRole) return;
    setAddingTier(true);
    try {
      await api.chzzk.followTiers.add(guildId, parseInt(newMonths), newRole);
      const ft = await api.chzzk.followTiers.list(guildId);
      setFollowTiers(ft);
      setNewMonths(""); setNewRole("");
    } catch {}
    setAddingTier(false);
  };

  const removeTier = async (tierId: number) => {
    await api.chzzk.followTiers.remove(guildId, tierId).catch(() => {});
    setFollowTiers((p) => p.filter((t) => t.id !== tierId));
  };

  const remove = async (id: number) => {
    await api.chzzk.remove(guildId, id);
    setSubs((p) => p.filter((s) => s.id !== id));
  };

  const findChannel = (id: number) => channels.find((c) => String(c.id) === String(id));

  return (
    <div className="space-y-6">
      {verifOpen && (
        <VerifModal
          verifications={verifications}
          loading={false}
          followTiers={followTiers}
          roles={roles}
          onClose={() => setVerifOpen(false)}
        />
      )}

      <div>
        <h1 className="page-title flex items-center gap-2">
          <Radio size={20} className="text-chzzk" /> 치지직
        </h1>
        <p className="page-subtitle">
          스트리머 방송 시작 시 Discord 채널에 알림을 보내고, 실시간 채팅 명령어를 설정합니다.
        </p>
      </div>

      {/* 세부 설정 탭 */}
      <div className="flex items-center gap-1 p-1 rounded-xl bg-bg-hover w-fit max-w-full overflow-x-auto">
        {DETAIL_TABS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setDetailTab(key)}
            className={clsx(
              "flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors",
              detailTab === key ? "bg-bg-card text-fg shadow-sm" : "text-muted hover:text-fg"
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {detailTab === "chat-commands" && <ChatCommandsPanel guildId={guildId} />}

      {detailTab === "streamer" && (
      <>
      {/* 등록된 스트리머 */}
      <div className="space-y-3">
        {subs.map((sub) => {
          const ch = findChannel(sub.discord_channel);
          const handleRelink = async () => {
            try {
              const d = await api.chzzkAuth.getStreamerLoginUrl(
                guildId, String(sub.discord_channel), !!sub.mention_everyone
              );
              window.location.href = d.url;
            } catch {}
          };
          return (
            <div key={sub.id} className="card space-y-3">
              <div className="flex items-center gap-4">
                {sub.chzzk_image_url ? (
                  <Image src={sub.chzzk_image_url} alt={sub.chzzk_name}
                         width={48} height={48} className="rounded-full shrink-0" />
                ) : (
                  <div className="w-12 h-12 rounded-full bg-bg-hover flex items-center justify-center shrink-0">
                    <Radio size={20} className="text-muted" />
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="font-semibold text-fg">{sub.chzzk_name}</p>
                    {sub.is_live
                      ? <span className="badge-live"><span className="w-1.5 h-1.5 rounded-full bg-chzzk animate-pulse" />LIVE</span>
                      : <span className="badge-offline">오프라인</span>}
                    {Boolean(sub.mention_everyone) && (
                      <span className="text-xs text-muted flex items-center gap-1">
                        <Bell size={11} /> @everyone
                      </span>
                    )}
                  </div>
                  {ch && <span className="text-sm text-muted mt-1 block">→ #{ch.name}</span>}
                </div>
                <button onClick={() => remove(sub.id)}
                  className="p-2 text-muted hover:text-danger transition-colors rounded-lg hover:bg-danger/10 shrink-0">
                  <Trash2 size={16} />
                </button>
              </div>
              <div className="border-t border-border pt-3">
                <p className="text-sm text-muted mb-2">
                  팔로우 기간 조회를 위해 스트리머({sub.chzzk_name}) 본인이 아래 버튼으로 치지직 계정을 연동해야 합니다.
                </p>
                <button onClick={handleRelink}
                  className="flex items-center gap-2 text-sm font-medium px-4 py-2 rounded-lg border border-accent/60 text-accent hover:bg-accent/15 hover:border-accent transition-colors">
                  <ExternalLink size={15} />
                  스트리머 팔로워 조회 연동
                </button>
              </div>
            </div>
          );
        })}

        {subs.length === 0 && (
          <div className="text-center py-10 text-muted">
            <Radio size={40} className="mx-auto mb-3 opacity-30" />
            <p className="font-medium">구독 중인 스트리머가 없습니다.</p>
            <p className="text-sm mt-1">아래에서 치지직 계정을 연동해 알림을 설정하세요.</p>
          </div>
        )}
      </div>

      {subs.length === 0 && <AddStreamerForm channels={channels} guildId={guildId} />}
      {subs.length >= 1 && (
        <p className="text-sm text-muted text-center">
          서버당 1명만 등록 가능합니다. 기존 구독을 삭제 후 다시 연동하세요.
        </p>
      )}

      {/* 팔로워 역할 지급 */}
      {subs.length > 0 && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="section-title">팔로워 역할 지급</h2>
              <p className="page-subtitle">
                치지직 OAuth 인증 시 팔로우 기간에 따라 역할을 자동으로 부여합니다.
                티어를 여러 개 추가할 수 있으며, 조건을 만족하는 티어 중 가장 높은 역할이 지급됩니다.
                <br />
                Discord에서{" "}
                <code className="text-accent bg-bg px-1 rounded">/팔로우불러오기</code>
                로 기존 인증 유저에 재적용할 수 있습니다.
              </p>
            </div>
            <button
              onClick={openVerif}
              className="ml-4 shrink-0 flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg
                         border border-chzzk/60 text-chzzk bg-chzzk/10
                         hover:bg-chzzk/20 hover:border-chzzk transition-colors"
            >
              <Users size={15} /> 팔로우 인원
              {verifications.length > 0 && (
                <span className="text-xs bg-chzzk/30 rounded-full px-2 py-0.5 font-bold">
                  {verifications.length}
                </span>
              )}
            </button>
          </div>

          {/* 등록된 티어 목록 */}
          <div className="space-y-2">
            {followTiers.length === 0 && (
              <p className="text-sm text-muted text-center py-3">등록된 티어가 없습니다. 아래에서 추가하세요.</p>
            )}
            {followTiers.map((tier) => {
              const role = roles.find((r) => r.id === tier.role_id);
              return (
                <div key={tier.id} className="flex items-center justify-between bg-bg rounded-lg px-4 py-3 border border-border">
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-semibold text-accent w-24">{tier.months}개월 이상</span>
                    {role && (
                      <span className="text-sm px-2 py-0.5 rounded-full border border-border"
                            style={{ color: role.color ? `#${role.color.toString(16).padStart(6, "0")}` : "#fff" }}>
                        @{role.name}
                      </span>
                    )}
                    {!role && <span className="text-xs text-muted">역할 ID: {tier.role_id}</span>}
                  </div>
                  <button onClick={() => removeTier(tier.id)}
                          className="text-muted hover:text-danger transition-colors p-1">
                    <Trash2 size={14} />
                  </button>
                </div>
              );
            })}
          </div>

          {/* 티어 추가 */}
          {followTiers.length >= 5 ? (
            <p className="text-xs text-muted text-center pt-2 border-t border-border">
              최대 5개의 티어까지만 추가할 수 있습니다.
            </p>
          ) : (
            <div className="flex gap-2 pt-2 border-t border-border flex-wrap">
              <div className="flex items-center gap-2">
                <input
                  type="number" min={1} max={120}
                  className="input w-24 text-center"
                  placeholder="개월"
                  value={newMonths}
                  onChange={(e) => setNewMonths(e.target.value)}
                />
                <span className="text-sm text-muted shrink-0">개월 이상</span>
              </div>
              <select className="select flex-1 min-w-36" value={newRole}
                      onChange={(e) => setNewRole(e.target.value)}>
                <option value="">역할 선택...</option>
                {roles.map((r) => <option key={r.id} value={r.id}>@{r.name}</option>)}
              </select>
              <button onClick={addTier} disabled={addingTier || !newMonths || !newRole}
                      className="btn-primary shrink-0">
                <Plus size={15} /> {addingTier ? "추가 중..." : "추가"}
              </button>
            </div>
          )}
        </div>
      )}
      </>
      )}
    </div>
  );
}
