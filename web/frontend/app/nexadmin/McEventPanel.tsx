"use client";
import { useEffect, useState } from "react";
import { Plus, Trash2, Save, CheckCircle, Zap, Swords, Shield, Dices, Play, Terminal, Pencil, X } from "lucide-react";
import { adminFetch, type Guild } from "./page";

interface McEvent {
  id: number; name: string; is_active: number;
  mc_host: string; mc_port: number; mc_rcon_password: string; created_at: number;
}
interface McEventGuild {
  guild_id: string; mc_player_name: string; guild_name: string;
}
interface McEventCommand {
  id: number; kind: "debuff" | "buff" | "random"; trigger_text: string; is_active: number;
}
interface McEventItem {
  id: number; event_id: number; item_type: "buff" | "debuff"; name: string;
  points_cost: number; command_template: string; chat_message_template: string;
  mc_notify_command: string; in_random_pool: number; is_active: number;
}
interface McEventPurchase {
  id: number; guild_id: string; user_id: string; trigger_text: string;
  target_guild_id: string | null; points_spent: number; applied: number;
  rcon_response: string; created_at: number;
  item_name: string; guild_name: string; target_name: string | null;
}

const KIND_LABEL: Record<string, string> = { debuff: "디버프지급", buff: "버프지급", random: "랜덤아이템" };

function Toast({ message }: { message: string }) {
  if (!message) return null;
  return (
    <div className="fixed top-4 right-4 z-50 px-4 py-2 rounded-lg bg-bg-card border border-border shadow-lg text-sm text-fg">
      {message}
    </div>
  );
}

// ── 이벤트 생성 ──────────────────────────────────────────────────────────────
function CreateEventForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName]   = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await adminFetch("/api/admin/mc-events", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      setName("");
      onCreated();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "생성 실패");
    } finally { setSaving(false); }
  };

  return (
    <div className="flex items-center gap-2">
      <input className="input flex-1" placeholder="이벤트 이름 (예: 마크 합방 콜라보)"
             value={name} onChange={(e) => setName(e.target.value)} />
      <button onClick={submit} disabled={saving || !name.trim()} className="btn-primary shrink-0">
        <Plus size={15} /> 이벤트 생성
      </button>
    </div>
  );
}

// ── 연결 설정 ────────────────────────────────────────────────────────────────
function ConnectionCard({ event, onChanged }: { event: McEvent; onChanged: () => void }) {
  const [host, setHost]         = useState(event.mc_host);
  const [port, setPort]         = useState(String(event.mc_port));
  const [password, setPassword] = useState(event.mc_rcon_password);
  const [saving, setSaving]     = useState(false);
  const [saved, setSaved]       = useState(false);
  const [testing, setTesting]   = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    setHost(event.mc_host); setPort(String(event.mc_port)); setPassword(event.mc_rcon_password);
  }, [event.id]);

  const save = async () => {
    setSaving(true);
    try {
      await adminFetch(`/api/admin/mc-events/${event.id}`, {
        method: "PATCH",
        body: JSON.stringify({ mc_host: host, mc_port: parseInt(port) || 25575, mc_rcon_password: password }),
      });
      setSaved(true); setTimeout(() => setSaved(false), 2000);
      onChanged();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "저장 실패");
    } finally { setSaving(false); }
  };

  const toggleActive = async () => {
    try {
      await adminFetch(`/api/admin/mc-events/${event.id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !event.is_active }),
      });
      onChanged();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "변경 실패");
    }
  };

  const test = async () => {
    setTesting(true); setTestResult(null);
    try {
      const r = await adminFetch<{ response: string }>(`/api/admin/mc-events/${event.id}/test`, { method: "POST" });
      setTestResult(`연결 성공 — ${r.response}`);
    } catch (e: unknown) {
      setTestResult(e instanceof Error ? e.message : "연결 실패");
    } finally { setTesting(false); }
  };

  return (
    <div className="rounded-2xl border border-border p-4 space-y-4">
      <div className="flex items-center justify-between">
        <p className="font-semibold text-fg text-sm">마크 서버 연결 (RCON)</p>
        <button
          onClick={toggleActive}
          className={`text-xs font-semibold px-3 py-1.5 rounded-full border transition-colors ${
            event.is_active
              ? "border-success/40 bg-success/10 text-success"
              : "border-border text-muted hover:text-fg"
          }`}
        >
          {event.is_active ? "활성 (채팅 명령어 동작 중)" : "비활성"}
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <label className="label">호스트</label>
          <input className="input font-mono" placeholder="123.45.67.89" value={host} onChange={(e) => setHost(e.target.value)} />
        </div>
        <div>
          <label className="label">RCON 포트</label>
          <input className="input font-mono" placeholder="25575" value={port} onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, ""))} />
        </div>
        <div>
          <label className="label">RCON 비밀번호</label>
          <input className="input font-mono" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button onClick={save} disabled={saving} className="btn-primary text-sm">
          {saved ? <><CheckCircle size={14} /> 저장됨</> : <><Save size={14} /> {saving ? "저장 중..." : "연결 정보 저장"}</>}
        </button>
        <button onClick={test} disabled={testing || !host} className="btn-secondary text-sm">
          <Play size={14} /> {testing ? "테스트 중..." : "연결 테스트"}
        </button>
      </div>
      {testResult && (
        <p className={`text-xs font-mono ${testResult.startsWith("연결 성공") ? "text-success" : "text-danger"}`}>
          {testResult}
        </p>
      )}
      <p className="text-xs text-muted">
        RCON 포트는 외부에 공개하면 안 됩니다 — 마크 서버 방화벽에서 봇이 배포된 서버 IP만 허용하세요.
      </p>
    </div>
  );
}

// ── 임의 명령어 테스트 ────────────────────────────────────────────────────────
function TestCommandCard({ eventId }: { eventId: number }) {
  const [command, setCommand]   = useState("");
  const [running, setRunning]   = useState(false);
  const [result, setResult]     = useState<{ ok: boolean; text: string } | null>(null);

  const run = async () => {
    if (!command.trim()) return;
    setRunning(true); setResult(null);
    try {
      const r = await adminFetch<{ response: string }>(`/api/admin/mc-events/${eventId}/run`, {
        method: "POST", body: JSON.stringify({ command: command.trim() }),
      });
      setResult({ ok: true, text: r.response || "(빈 응답)" });
    } catch (e: unknown) {
      setResult({ ok: false, text: e instanceof Error ? e.message : "실행 실패" });
    } finally { setRunning(false); }
  };

  return (
    <div className="rounded-2xl border border-border p-4 space-y-3">
      <p className="font-semibold text-fg text-sm flex items-center gap-2">
        <Terminal size={14} className="text-accent" /> 테스트 명령어 (임의 RCON 실행)
      </p>
      <p className="text-xs text-muted">
        아이템/명령어 등록 없이 마크 서버에 바로 명령어를 보내 결과를 확인할 수 있습니다.
      </p>
      <div className="flex gap-2">
        <input
          className="input flex-1 font-mono"
          placeholder="say hello"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") run(); }}
        />
        <button onClick={run} disabled={running || !command.trim()} className="btn-secondary text-sm shrink-0">
          <Play size={14} /> {running ? "실행 중..." : "실행"}
        </button>
      </div>
      {result && (
        <p className={`text-xs font-mono whitespace-pre-wrap ${result.ok ? "text-success" : "text-danger"}`}>
          {result.text}
        </p>
      )}
    </div>
  );
}

// ── 참가 서버 ────────────────────────────────────────────────────────────────
function GuildsCard({
  eventId, guilds, participants, onChanged, showToast,
}: {
  eventId: number; guilds: Guild[]; participants: McEventGuild[];
  onChanged: () => void; showToast: (msg: string) => void;
}) {
  const [guildId, setGuildId]       = useState("");
  const [playerName, setPlayerName] = useState("");
  const [adding, setAdding]         = useState(false);

  const availableGuilds = guilds.filter((g) => !participants.some((p) => p.guild_id === g.id));

  const add = async () => {
    if (!guildId || !playerName.trim()) return;
    setAdding(true);
    try {
      await adminFetch(`/api/admin/mc-events/${eventId}/guilds`, {
        method: "POST",
        body: JSON.stringify({ guild_id: guildId, mc_player_name: playerName.trim() }),
      });
      setGuildId(""); setPlayerName("");
      onChanged();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "추가 실패");
    } finally { setAdding(false); }
  };

  const remove = async (gid: string) => {
    if (!confirm("이 서버를 이벤트 참가 목록에서 제거하시겠습니까?")) return;
    try {
      await adminFetch(`/api/admin/mc-events/${eventId}/guilds/${gid}`, { method: "DELETE" });
      showToast("삭제되었습니다.");
      onChanged();
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "삭제 실패");
    }
  };

  return (
    <div className="rounded-2xl border border-border p-4 space-y-4">
      <p className="font-semibold text-fg text-sm">참가 서버 ({participants.length}명)</p>

      <div className="space-y-2">
        {participants.map((p) => (
          <div key={p.guild_id} className="flex items-center justify-between bg-bg rounded-lg px-3 py-2 border border-border text-sm">
            <div className="flex items-center gap-3">
              <span className="font-medium text-fg">{p.guild_name}</span>
              <span className="text-xs text-muted font-mono">MC: {p.mc_player_name}</span>
            </div>
            <button onClick={() => remove(p.guild_id)} className="text-muted hover:text-danger transition-colors p-1">
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {participants.length === 0 && (
          <p className="text-sm text-muted text-center py-3">등록된 참가 서버가 없습니다.</p>
        )}
      </div>

      <div className="flex gap-2 flex-wrap pt-2 border-t border-border">
        <select className="select flex-1 min-w-36" value={guildId} onChange={(e) => setGuildId(e.target.value)}>
          <option value="">참가시킬 서버 선택...</option>
          {availableGuilds.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <input className="input w-40 font-mono" placeholder="마크 플레이어 이름" value={playerName} onChange={(e) => setPlayerName(e.target.value)} />
        <button onClick={add} disabled={adding || !guildId || !playerName.trim()} className="btn-primary shrink-0">
          <Plus size={15} /> 추가
        </button>
      </div>
    </div>
  );
}

// ── 채팅 명령어 ──────────────────────────────────────────────────────────────
function CommandsCard({
  eventId, commands, onChanged, showToast,
}: { eventId: number; commands: McEventCommand[]; onChanged: () => void; showToast: (msg: string) => void }) {
  const [kind, setKind]     = useState<"debuff" | "buff" | "random">("debuff");
  const [trigger, setTrigger] = useState("");
  const [adding, setAdding]  = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");

  const startEdit = (cmd: McEventCommand) => {
    setEditingId(cmd.id);
    setEditValue(cmd.trigger_text);
  };

  const saveEdit = async (id: number) => {
    const value = editValue.trim();
    if (!value) { setEditingId(null); return; }
    try {
      await adminFetch(`/api/admin/mc-events/${eventId}/commands/${id}`, {
        method: "PATCH", body: JSON.stringify({ trigger_text: value }),
      });
      showToast("수정되었습니다.");
      setEditingId(null);
      onChanged();
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "수정 실패");
    }
  };

  const add = async () => {
    setAdding(true);
    try {
      await adminFetch(`/api/admin/mc-events/${eventId}/commands`, {
        method: "POST",
        body: JSON.stringify({ kind, trigger_text: trigger.trim() || KIND_LABEL[kind] }),
      });
      setTrigger("");
      onChanged();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "추가 실패");
    } finally { setAdding(false); }
  };

  const remove = async (id: number) => {
    if (!confirm("이 명령어를 삭제하시겠습니까?")) return;
    try {
      await adminFetch(`/api/admin/mc-events/${eventId}/commands/${id}`, { method: "DELETE" });
      showToast("삭제되었습니다.");
      onChanged();
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "삭제 실패");
    }
  };

  const toggleActive = async (cmd: McEventCommand) => {
    await adminFetch(`/api/admin/mc-events/${eventId}/commands/${cmd.id}`, {
      method: "PATCH", body: JSON.stringify({ is_active: !cmd.is_active }),
    }).catch(() => {});
    onChanged();
  };

  return (
    <div className="rounded-2xl border border-border p-4 space-y-4">
      <p className="font-semibold text-fg text-sm">채팅 명령어</p>
      <p className="text-xs text-muted">
        치지직 채팅에서 <code className="bg-bg px-1 rounded">!</code> 뒤에 입력할 문구입니다. 종류(디버프지급/버프지급/랜덤아이템)당
        여러 개 등록할 수 있고, 문구는 자유롭게 바꿀 수 있습니다.
      </p>

      <div className="space-y-2">
        {commands.map((cmd) => (
          <div key={cmd.id} className="flex items-center justify-between bg-bg rounded-lg px-3 py-2 border border-border text-sm">
            {editingId === cmd.id ? (
              <div className="flex items-center gap-2 flex-1">
                <span className="text-xs px-2 py-0.5 rounded-full border border-border text-muted shrink-0">{KIND_LABEL[cmd.kind]}</span>
                <input
                  className="input flex-1 font-mono py-1"
                  value={editValue}
                  autoFocus
                  onChange={(e) => setEditValue(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") saveEdit(cmd.id); if (e.key === "Escape") setEditingId(null); }}
                />
                <button onClick={() => saveEdit(cmd.id)} className="p-1.5 rounded-lg text-accent hover:bg-accent/10 transition-colors shrink-0">
                  <CheckCircle size={14} />
                </button>
                <button onClick={() => setEditingId(null)} className="p-1.5 rounded-lg text-muted hover:text-fg transition-colors shrink-0">
                  <X size={14} />
                </button>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-3">
                  <span className="text-xs px-2 py-0.5 rounded-full border border-border text-muted">{KIND_LABEL[cmd.kind]}</span>
                  <span className="font-mono text-fg">!{cmd.trigger_text}</span>
                  {!cmd.is_active && <span className="text-[10px] text-muted">(비활성)</span>}
                </div>
                <div className="flex items-center gap-1">
                  <button onClick={() => startEdit(cmd)} className="p-1.5 rounded-lg text-muted hover:text-fg hover:bg-bg-hover transition-colors">
                    <Pencil size={14} />
                  </button>
                  <button onClick={() => toggleActive(cmd)} className="text-xs text-muted hover:text-fg px-2 py-1 rounded transition-colors">
                    {cmd.is_active ? "끄기" : "켜기"}
                  </button>
                  <button onClick={() => remove(cmd.id)} className="p-1.5 rounded-lg text-muted hover:text-danger hover:bg-danger/10 transition-colors">
                    <Trash2 size={14} />
                  </button>
                </div>
              </>
            )}
          </div>
        ))}
        {commands.length === 0 && <p className="text-sm text-muted text-center py-3">등록된 명령어가 없습니다.</p>}
      </div>

      <div className="flex gap-2 flex-wrap pt-2 border-t border-border">
        <select className="select w-32" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
          <option value="debuff">디버프지급</option>
          <option value="buff">버프지급</option>
          <option value="random">랜덤아이템</option>
        </select>
        <input
          className="input flex-1 min-w-32 font-mono"
          placeholder={KIND_LABEL[kind]}
          value={trigger}
          onChange={(e) => setTrigger(e.target.value)}
        />
        <button onClick={add} disabled={adding} className="btn-primary shrink-0">
          <Plus size={15} /> 명령어 추가
        </button>
      </div>
    </div>
  );
}

// ── 아이템 카탈로그 ──────────────────────────────────────────────────────────
function ItemsCard({ eventId, items, onChanged }: { eventId: number; items: McEventItem[]; onChanged: () => void }) {
  const [itemType, setItemType]     = useState<"debuff" | "buff">("debuff");
  const [name, setName]             = useState("");
  const [cost, setCost]             = useState("");
  const [template, setTemplate]     = useState("");
  const [chatTemplate, setChatTemplate] = useState("{user}님이 [{item}]을(를) 사용했습니다!");
  const [notifyCommand, setNotifyCommand] = useState("");
  const [inPool, setInPool]         = useState(true);
  const [adding, setAdding]         = useState(false);
  const [testPlayer, setTestPlayer] = useState("");
  const [testResults, setTestResults] = useState<Record<number, { ok: boolean; text: string }>>({});
  const [testingId, setTestingId]   = useState<number | null>(null);

  const runItem = (raw: string, item: McEventItem, player: string) =>
    raw.replace(/\{player\}/g, player || "TestPlayer")
       .replace(/\{item\}/g, item.name)
       .replace(/\{user\}/g, "테스트유저");

  const testItem = async (item: McEventItem) => {
    setTestingId(item.id);
    try {
      const effect = await adminFetch<{ response: string }>(`/api/admin/mc-events/${eventId}/run`, {
        method: "POST",
        body: JSON.stringify({ command: runItem(item.command_template, item, testPlayer) }),
      });
      let text = `효과: ${effect.response || "(빈 응답)"}`;
      if (item.mc_notify_command.trim()) {
        const notify = await adminFetch<{ response: string }>(`/api/admin/mc-events/${eventId}/run`, {
          method: "POST",
          body: JSON.stringify({ command: runItem(item.mc_notify_command, item, testPlayer) }),
        });
        text += `\n귓속말: ${notify.response || "(빈 응답)"}`;
      }
      setTestResults((p) => ({ ...p, [item.id]: { ok: true, text } }));
    } catch (e: unknown) {
      setTestResults((p) => ({ ...p, [item.id]: { ok: false, text: e instanceof Error ? e.message : "실행 실패" } }));
    } finally { setTestingId(null); }
  };

  const add = async () => {
    if (!name.trim() || !template.trim() || !cost) return;
    setAdding(true);
    try {
      await adminFetch(`/api/admin/mc-events/${eventId}/items`, {
        method: "POST",
        body: JSON.stringify({
          item_type: itemType, name: name.trim(), points_cost: parseInt(cost) || 0,
          command_template: template.trim(), chat_message_template: chatTemplate.trim(),
          mc_notify_command: notifyCommand.trim(), in_random_pool: inPool,
        }),
      });
      setName(""); setCost(""); setTemplate(""); setNotifyCommand("");
      setChatTemplate("{user}님이 [{item}]을(를) 사용했습니다!");
      onChanged();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "추가 실패");
    } finally { setAdding(false); }
  };

  const remove = async (id: number) => {
    if (!confirm("이 아이템을 삭제하시겠습니까?")) return;
    await adminFetch(`/api/admin/mc-events/${eventId}/items/${id}`, { method: "DELETE" }).catch(() => {});
    onChanged();
  };

  const toggleActive = async (item: McEventItem) => {
    await adminFetch(`/api/admin/mc-events/${eventId}/items/${item.id}`, {
      method: "PATCH", body: JSON.stringify({ is_active: !item.is_active }),
    }).catch(() => {});
    onChanged();
  };

  return (
    <div className="rounded-2xl border border-border p-4 space-y-4">
      <p className="font-semibold text-fg text-sm">아이템 카탈로그</p>
      <p className="text-xs text-muted">
        디버프지급은 자기 자신을 제외한 참가 서버 중 무작위 1명에게, 버프지급은 자기 자신에게 적용됩니다.
        랜덤아이템은 &quot;랜덤풀 포함&quot;으로 표시된 아이템 중에서만 무작위로 뽑힙니다.
        세 명령어 모두 <code className="bg-bg px-1 rounded">{"{player}"}</code>(대상 마크 닉네임),{" "}
        <code className="bg-bg px-1 rounded">{"{user}"}</code>(구매한 치지직 닉네임),{" "}
        <code className="bg-bg px-1 rounded">{"{item}"}</code>(아이템 이름)를 사용할 수 있습니다.
      </p>

      <div>
        <label className="label">테스트 대상 마크 플레이어 이름 (아래 테스트 실행 버튼에 사용)</label>
        <input
          className="input w-full max-w-xs font-mono"
          placeholder="TestPlayer"
          value={testPlayer}
          onChange={(e) => setTestPlayer(e.target.value)}
        />
      </div>

      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.id} className="flex items-center justify-between gap-3 bg-bg rounded-lg px-3 py-2.5 border border-border">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                {item.item_type === "debuff"
                  ? <span className="text-xs px-2 py-0.5 rounded-full bg-danger/10 text-danger border border-danger/20 flex items-center gap-1"><Swords size={11} /> 디버프</span>
                  : <span className="text-xs px-2 py-0.5 rounded-full bg-success/10 text-success border border-success/20 flex items-center gap-1"><Shield size={11} /> 버프</span>}
                <span className="text-sm font-medium text-fg">{item.name}</span>
                <span className="text-xs text-accent font-semibold">{item.points_cost.toLocaleString()}P</span>
                {!!item.in_random_pool && (
                  <span className="text-[10px] text-muted flex items-center gap-0.5"><Dices size={10} /> 랜덤풀 포함</span>
                )}
                {!item.is_active && <span className="text-[10px] text-muted">(비활성)</span>}
              </div>
              <p className="text-xs text-muted font-mono mt-1 truncate">효과: {item.command_template}</p>
              {item.mc_notify_command && (
                <p className="text-xs text-muted font-mono truncate">귓속말: {item.mc_notify_command}</p>
              )}
              {item.chat_message_template && (
                <p className="text-xs text-muted truncate">채팅 문구: {item.chat_message_template}</p>
              )}
              {testResults[item.id] && (
                <p className={`text-xs font-mono whitespace-pre-wrap mt-1 ${testResults[item.id].ok ? "text-success" : "text-danger"}`}>
                  {testResults[item.id].text}
                </p>
              )}
            </div>
            <div className="flex items-center gap-1 shrink-0">
              <button onClick={() => testItem(item)} disabled={testingId === item.id}
                      className="flex items-center gap-1 text-xs text-muted hover:text-accent px-2 py-1 rounded transition-colors">
                <Play size={12} /> {testingId === item.id ? "실행 중..." : "테스트 실행"}
              </button>
              <button onClick={() => toggleActive(item)} className="text-xs text-muted hover:text-fg px-2 py-1 rounded transition-colors">
                {item.is_active ? "끄기" : "켜기"}
              </button>
              <button onClick={() => remove(item.id)} className="p-1.5 rounded-lg text-muted hover:text-danger hover:bg-danger/10 transition-colors">
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
        {items.length === 0 && <p className="text-sm text-muted text-center py-3">등록된 아이템이 없습니다.</p>}
      </div>

      <div className="space-y-2 pt-2 border-t border-border">
        <div className="flex gap-2 flex-wrap">
          <select className="select w-28" value={itemType} onChange={(e) => setItemType(e.target.value as "debuff" | "buff")}>
            <option value="debuff">디버프</option>
            <option value="buff">버프</option>
          </select>
          <input className="input flex-1 min-w-32" placeholder="아이템 이름 (예: 실명 60초)" value={name} onChange={(e) => setName(e.target.value)} />
          <input className="input w-24" placeholder="가격" value={cost} onChange={(e) => setCost(e.target.value.replace(/[^0-9]/g, ""))} />
        </div>
        <div>
          <label className="label">효과 명령어 (마크에 실행)</label>
          <input
            className="input w-full font-mono"
            placeholder="effect give {player} minecraft:blindness 60 1"
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
          />
        </div>
        <div>
          <label className="label">대상 알림 명령어 (선택, 귓속말 등)</label>
          <input
            className="input w-full font-mono"
            placeholder="tell {player} {item}이(가) 적용되었습니다"
            value={notifyCommand}
            onChange={(e) => setNotifyCommand(e.target.value)}
          />
        </div>
        <div>
          <label className="label">치지직 채팅 안내 문구</label>
          <input
            className="input w-full"
            value={chatTemplate}
            onChange={(e) => setChatTemplate(e.target.value)}
          />
        </div>
        <div className="flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm text-muted cursor-pointer select-none">
            <input type="checkbox" checked={inPool} onChange={(e) => setInPool(e.target.checked)} className="w-4 h-4 rounded accent-accent" />
            랜덤아이템 풀에 포함
          </label>
          <button onClick={add} disabled={adding || !name.trim() || !template.trim() || !cost} className="btn-primary text-sm">
            <Plus size={14} /> 아이템 추가
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 구매 로그 ────────────────────────────────────────────────────────────────
function PurchasesCard({ purchases }: { purchases: McEventPurchase[] }) {
  return (
    <div className="rounded-2xl border border-border overflow-hidden">
      <div className="p-4 border-b border-border">
        <p className="font-semibold text-fg text-sm flex items-center gap-2"><Zap size={14} className="text-accent" /> 최근 구매/적용 로그</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-card/60">
              <th className="text-left px-4 py-2 text-muted font-medium">시간</th>
              <th className="text-left px-4 py-2 text-muted font-medium">서버</th>
              <th className="text-left px-4 py-2 text-muted font-medium">명령어</th>
              <th className="text-left px-4 py-2 text-muted font-medium">아이템</th>
              <th className="text-left px-4 py-2 text-muted font-medium">대상</th>
              <th className="text-left px-4 py-2 text-muted font-medium">결과</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {purchases.map((p) => (
              <tr key={p.id}>
                <td className="px-4 py-2 text-xs text-muted whitespace-nowrap">{new Date(p.created_at * 1000).toLocaleString("ko-KR")}</td>
                <td className="px-4 py-2 text-fg">{p.guild_name}</td>
                <td className="px-4 py-2 text-xs text-muted font-mono">!{p.trigger_text}</td>
                <td className="px-4 py-2 text-fg">{p.item_name} <span className="text-xs text-accent">({p.points_spent}P)</span></td>
                <td className="px-4 py-2 text-muted">{p.target_name ?? "자기 자신"}</td>
                <td className="px-4 py-2">
                  {p.applied
                    ? <span className="text-xs text-success">적용됨</span>
                    : <span className="text-xs text-danger" title={p.rcon_response}>실패</span>}
                </td>
              </tr>
            ))}
            {purchases.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-muted">아직 구매 기록이 없습니다.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── 메인 패널 ────────────────────────────────────────────────────────────────
export default function McEventPanel({ guilds }: { guilds: Guild[] }) {
  const [events, setEvents]           = useState<McEvent[]>([]);
  const [selectedId, setSelectedId]   = useState<number | null>(null);
  const [participants, setParticipants] = useState<McEventGuild[]>([]);
  const [commands, setCommands]       = useState<McEventCommand[]>([]);
  const [items, setItems]             = useState<McEventItem[]>([]);
  const [purchases, setPurchases]     = useState<McEventPurchase[]>([]);
  const [loading, setLoading]         = useState(true);
  const [toast, setToast]             = useState("");

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 2500); };

  const loadEvents = async () => {
    const list = await adminFetch<McEvent[]>("/api/admin/mc-events").catch(() => []);
    setEvents(list);
    setLoading(false);
    if (list.length > 0 && (selectedId === null || !list.some((e) => e.id === selectedId))) {
      setSelectedId(list[0].id);
    }
  };

  const loadDetail = async (eventId: number) => {
    const [p, c, i, pu] = await Promise.all([
      adminFetch<McEventGuild[]>(`/api/admin/mc-events/${eventId}/guilds`).catch(() => []),
      adminFetch<McEventCommand[]>(`/api/admin/mc-events/${eventId}/commands`).catch(() => []),
      adminFetch<McEventItem[]>(`/api/admin/mc-events/${eventId}/items`).catch(() => []),
      adminFetch<McEventPurchase[]>(`/api/admin/mc-events/${eventId}/purchases`).catch(() => []),
    ]);
    setParticipants(p); setCommands(c); setItems(i); setPurchases(pu);
  };

  useEffect(() => { loadEvents(); }, []);
  useEffect(() => { if (selectedId !== null) loadDetail(selectedId); }, [selectedId]);

  const selected = events.find((e) => e.id === selectedId) ?? null;

  const deleteEvent = async () => {
    if (!selected) return;
    if (!confirm(`"${selected.name}" 이벤트를 삭제하시겠습니까? (참가 서버/아이템/명령어도 함께 삭제됩니다)`)) return;
    await adminFetch(`/api/admin/mc-events/${selected.id}`, { method: "DELETE" }).catch(() => {});
    setSelectedId(null);
    loadEvents();
  };

  if (loading) {
    return <p className="text-muted text-sm animate-pulse">불러오는 중...</p>;
  }

  return (
    <div className="space-y-6">
      <Toast message={toast} />
      <p className="text-sm text-muted">
        스트리머 콜라보용 마인크래프트 연동 이벤트를 관리합니다. 참가 서버 목록/채팅 명령어/아이템 카탈로그는 이벤트 전체가 공유하고,
        각 서버의 치지직 채팅에서 등록된 명령어를 입력하면 해당 서버의 포인트를 차감하고 마크 서버에 명령을 실행합니다.
        (치지직 계정을 연동한 유저만 대상)
      </p>

      <div className="rounded-2xl border border-border p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          {events.map((e) => (
            <button
              key={e.id}
              onClick={() => setSelectedId(e.id)}
              className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-colors ${
                selectedId === e.id
                  ? "border-accent text-accent bg-accent/10"
                  : "border-border text-muted hover:text-fg"
              }`}
            >
              {e.name} {!!e.is_active && <span className="text-success">●</span>}
            </button>
          ))}
        </div>
        <CreateEventForm onCreated={loadEvents} />
      </div>

      {selected && (
        <>
          <div className="flex justify-end">
            <button onClick={deleteEvent} className="text-xs text-danger hover:underline">이벤트 삭제</button>
          </div>
          <ConnectionCard event={selected} onChanged={() => { loadEvents(); }} />
          <TestCommandCard eventId={selected.id} />
          <GuildsCard eventId={selected.id} guilds={guilds} participants={participants} onChanged={() => loadDetail(selected.id)} showToast={showToast} />
          <CommandsCard eventId={selected.id} commands={commands} onChanged={() => loadDetail(selected.id)} showToast={showToast} />
          <ItemsCard eventId={selected.id} items={items} onChanged={() => loadDetail(selected.id)} />
          <PurchasesCard purchases={purchases} />
        </>
      )}

      {events.length === 0 && (
        <p className="text-sm text-muted text-center py-6">등록된 이벤트가 없습니다. 위에서 이벤트를 먼저 생성하세요.</p>
      )}
    </div>
  );
}
