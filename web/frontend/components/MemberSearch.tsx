"use client";
import { useState, useRef, useCallback } from "react";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import type { GuildMember } from "@/lib/types";

export default function MemberSearch({
  guildId,
  value,
  onChange,
  placeholder = "닉네임 입력...",
}: {
  guildId: string;
  value: GuildMember | null;
  onChange: (m: GuildMember | null) => void;
  placeholder?: string;
}) {
  const [query, setQuery]     = useState("");
  const [results, setResults] = useState<GuildMember[]>([]);
  const [open, setOpen]       = useState(false);
  const [loading, setLoading] = useState(false);
  const timerRef              = useRef<ReturnType<typeof setTimeout> | null>(null);

  const search = useCallback((q: string) => {
    if (!q.trim()) { setResults([]); setOpen(false); return; }
    setLoading(true);
    api.guilds.searchMembers(guildId, q)
      .then((r) => { setResults(r); setOpen(r.length > 0); })
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, [guildId]);

  const handleInput = (v: string) => {
    setQuery(v);
    onChange(null);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => search(v), 350);
  };

  const select = (m: GuildMember) => {
    onChange(m);
    setQuery(m.display_name);
    setOpen(false);
    setResults([]);
  };

  const avatarUrl = (m: GuildMember) =>
    m.avatar
      ? `https://cdn.discordapp.com/avatars/${m.id}/${m.avatar}.png?size=32`
      : `https://cdn.discordapp.com/embed/avatars/0.png`;

  return (
    <div className="relative">
      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
        <input
          className="input pl-8"
          placeholder={placeholder}
          value={query}
          onChange={(e) => handleInput(e.target.value)}
          onFocus={() => query && results.length > 0 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
        />
        {loading && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted">검색 중...</span>
        )}
      </div>
      {open && results.length > 0 && (
        <div className="absolute z-20 mt-1 w-full bg-bg-card border border-border rounded-lg shadow-xl overflow-hidden max-h-48 overflow-y-auto">
          {results.map((m) => (
            <button
              key={m.id}
              onMouseDown={() => select(m)}
              className="w-full flex items-center gap-2 px-3 py-2 hover:bg-bg-hover transition-colors text-left"
            >
              <img src={avatarUrl(m)} alt="" className="w-6 h-6 rounded-full shrink-0" />
              <span className="text-sm text-white truncate">{m.display_name}</span>
              {m.nick && m.username !== m.display_name && (
                <span className="text-xs text-muted truncate">({m.username})</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
