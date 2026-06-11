"use client";
import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import Image from "next/image";
import { Radio, Search, Plus, Trash2, X, Bell, BellOff } from "lucide-react";
import { api } from "@/lib/api";
import type { ChzzkSubscription, ChzzkSearchResult, Channel } from "@/lib/types";

function SearchModal({
  onClose,
  onSelect,
}: {
  onClose: () => void;
  onSelect: (result: ChzzkSearchResult) => void;
}) {
  const [keyword, setKeyword]   = useState("");
  const [results, setResults]   = useState<ChzzkSearchResult[]>([]);
  const [loading, setLoading]   = useState(false);
  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  const search = (kw: string) => {
    if (!kw.trim()) { setResults([]); return; }
    setLoading(true);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      const res = await api.chzzk.search(kw).catch(() => []);
      setResults(res);
      setLoading(false);
    }, 400);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-bg-card border border-border rounded-2xl w-full max-w-md shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h3 className="font-semibold text-white">치지직 채널 검색</h3>
          <button onClick={onClose} className="text-muted hover:text-white">
            <X size={18} />
          </button>
        </div>
        <div className="p-4 space-y-3">
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input
              className="input pl-9"
              placeholder="스트리머 이름 검색..."
              value={keyword}
              onChange={(e) => { setKeyword(e.target.value); search(e.target.value); }}
              autoFocus
            />
          </div>
          <div className="space-y-2 max-h-72 overflow-y-auto">
            {loading && <p className="text-muted text-sm text-center py-4">검색 중...</p>}
            {!loading && results.map((r) => (
              <button
                key={r.channelId}
                onClick={() => onSelect(r)}
                className="w-full flex items-center gap-3 p-3 rounded-lg hover:bg-bg-hover transition-colors text-left"
              >
                {r.channelImageUrl ? (
                  <Image src={r.channelImageUrl} alt={r.channelName}
                         width={40} height={40} className="rounded-full shrink-0" />
                ) : (
                  <div className="w-10 h-10 rounded-full bg-bg-hover shrink-0 flex items-center justify-center">
                    <Radio size={16} className="text-muted" />
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-white truncate">{r.channelName}</p>
                  <p className="text-xs text-muted">팔로워 {r.followerCount.toLocaleString()}명</p>
                </div>
                {r.openLive && (
                  <span className="badge-live shrink-0">
                    <span className="w-1.5 h-1.5 rounded-full bg-chzzk animate-pulse" />
                    LIVE
                  </span>
                )}
              </button>
            ))}
            {!loading && keyword && results.length === 0 && (
              <p className="text-muted text-sm text-center py-4">검색 결과가 없습니다.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function AddForm({
  selected,
  channels,
  onAdd,
  onCancel,
}: {
  selected: ChzzkSearchResult;
  channels: Channel[];
  onAdd: (data: object) => Promise<void>;
  onCancel: () => void;
}) {
  const textChannels = channels.filter((c) => c.type === 0);
  const [discordChannel, setDiscordChannel] = useState("");
  const [mentionEveryone, setMentionEveryone] = useState(false);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!discordChannel) return;
    setSaving(true);
    await onAdd({
      chzzk_channel_id: selected.channelId,
      chzzk_name:       selected.channelName,
      chzzk_image_url:  selected.channelImageUrl,
      discord_channel:  discordChannel,
      mention_everyone: mentionEveryone,
      is_live:          selected.openLive ?? false,
    });
    setSaving(false);
  };

  return (
    <div className="card border-accent/30 space-y-4">
      <div className="flex items-center gap-3">
        {selected.channelImageUrl ? (
          <Image src={selected.channelImageUrl} alt={selected.channelName}
                 width={40} height={40} className="rounded-full" />
        ) : (
          <div className="w-10 h-10 rounded-full bg-bg-hover flex items-center justify-center">
            <Radio size={16} className="text-muted" />
          </div>
        )}
        <div>
          <p className="font-medium text-white">{selected.channelName}</p>
          <p className="text-xs text-muted">알림 설정</p>
        </div>
        {selected.openLive && (
          <span className="badge-live ml-auto shrink-0">
            <span className="w-1.5 h-1.5 rounded-full bg-chzzk animate-pulse" />
            LIVE
          </span>
        )}
      </div>

      <div>
        <label className="label">알림 채널 *</label>
        <select
          className="select"
          value={discordChannel}
          onChange={(e) => setDiscordChannel(e.target.value)}
        >
          <option value="">채널 선택...</option>
          {textChannels.map((c) => (
            <option key={c.id} value={c.id}>#{c.name}</option>
          ))}
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

      <div className="flex gap-3">
        <button
          onClick={submit}
          disabled={saving || !discordChannel}
          className="btn-primary"
        >
          <Plus size={16} /> {saving ? "추가 중..." : "구독 추가"}
        </button>
        <button onClick={onCancel} className="btn-secondary">취소</button>
      </div>
    </div>
  );
}

export default function ChzzkPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [subs, setSubs]         = useState<ChzzkSubscription[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [showSearch, setShowSearch] = useState(false);
  const [selected, setSelected]     = useState<ChzzkSearchResult | null>(null);

  const load = async () => {
    const [s, ch] = await Promise.all([
      api.chzzk.list(guildId),
      api.guilds.channels(guildId),
    ]);
    setSubs(s);
    setChannels(ch);
  };

  useEffect(() => { load(); }, [guildId]);

  const handleSelect = (r: ChzzkSearchResult) => {
    setShowSearch(false);
    setSelected(r);
  };

  const handleAdd = async (data: object) => {
    await api.chzzk.add(guildId, data);
    setSelected(null);
    await load();
  };

  const remove = async (id: number) => {
    await api.chzzk.remove(guildId, id);
    setSubs((p) => p.filter((s) => s.id !== id));
  };

  const findChannel = (id: number) =>
    channels.find((c) => String(c.id) === String(id));

  return (
    <div className="space-y-6">
      {showSearch && (
        <SearchModal
          onClose={() => setShowSearch(false)}
          onSelect={handleSelect}
        />
      )}

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Radio size={20} className="text-chzzk" /> 치지직 알림
          </h1>
          <p className="text-muted text-sm mt-1">
            스트리머 방송 시작 시 Discord 채널에 알림을 보냅니다.
          </p>
        </div>
        {!selected && (
          <button onClick={() => setShowSearch(true)} className="btn-primary">
            <Plus size={16} /> 스트리머 추가
          </button>
        )}
      </div>

      {selected && (
        <AddForm
          selected={selected}
          channels={channels}
          onAdd={handleAdd}
          onCancel={() => setSelected(null)}
        />
      )}

      <div className="space-y-3">
        {subs.map((sub) => {
          const ch = findChannel(sub.discord_channel);
          return (
            <div key={sub.id} className="card flex items-center gap-4">
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
                  <p className="font-semibold text-white">{sub.chzzk_name}</p>
                  {sub.is_live
                    ? <span className="badge-live"><span className="w-1.5 h-1.5 rounded-full bg-chzzk animate-pulse" />LIVE</span>
                    : <span className="badge-offline">오프라인</span>
                  }
                  {Boolean(sub.mention_everyone) && (
                    <span className="text-xs text-muted flex items-center gap-1">
                      <Bell size={11} /> @everyone
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 mt-1 flex-wrap">
                  {ch && <span className="text-xs text-muted">→ #{ch.name}</span>}
                </div>
              </div>
              <button
                onClick={() => remove(sub.id)}
                className="p-2 text-muted hover:text-danger transition-colors rounded-lg hover:bg-danger/10 shrink-0"
              >
                <Trash2 size={16} />
              </button>
            </div>
          );
        })}

        {subs.length === 0 && !selected && (
          <div className="text-center py-16 text-muted">
            <Radio size={40} className="mx-auto mb-3 opacity-30" />
            <p className="font-medium">구독 중인 스트리머가 없습니다.</p>
            <p className="text-sm mt-1">스트리머 추가 버튼으로 알림을 설정하세요.</p>
          </div>
        )}
      </div>
    </div>
  );
}
