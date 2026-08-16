"use client";
import { useState, useRef, useCallback } from "react";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { isHandledElsewhere } from "@/lib/dashboardErrors";
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
  // 검색 실패를 빈 결과로 바꾸면 "그런 멤버가 없습니다"와 구분되지 않는다.
  const [failed, setFailed]   = useState(false);
  const timerRef              = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef              = useRef<HTMLInputElement>(null);

  const search = useCallback((q: string) => {
    if (!q.trim()) { setResults([]); setOpen(false); setFailed(false); return; }
    setLoading(true);
    setFailed(false);
    api.guilds.searchMembers(guildId, q)
      .then((r) => { setResults(r); setOpen(true); })
      .catch((e: unknown) => {
        // 401은 api.ts가 로그인 화면으로 보낸다. 그 밖의 실패만 알린다.
        if (!isHandledElsewhere(e)) { setResults([]); setFailed(true); setOpen(true); }
      })
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
          ref={inputRef}
          className="input pl-8"
          placeholder={placeholder}
          value={query}
          onChange={(e) => handleInput(e.target.value)}
          onFocus={() => query && (results.length > 0 || failed) && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          // ESC로 닫고 포커스는 입력에 그대로 둔다. 화살표 탐색은 없으므로
          // listbox/option role을 붙이지 않는다 — 있지도 않은 조작을 약속하게 된다.
          onKeyDown={(e) => {
            if (e.key === "Escape" && open) { e.stopPropagation(); setOpen(false); inputRef.current?.focus(); }
          }}
        />
        {loading && (
          <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted">검색 중...</span>
        )}
      </div>
      {open && (
        <div className="absolute z-20 mt-1 w-full bg-bg-card border border-border rounded-lg shadow-xl overflow-hidden max-h-48 overflow-y-auto">
          {failed ? (
            <p role="status" className="px-3 py-2.5 text-sm text-danger">
              멤버를 검색하지 못했습니다. 잠시 후 다시 시도해 주세요.
            </p>
          ) : results.length === 0 ? (
            <p role="status" className="px-3 py-2.5 text-sm text-muted">검색 결과가 없습니다.</p>
          ) : results.map((m) => (
            <button
              key={m.id}
              onMouseDown={() => select(m)}
              className="w-full min-h-[44px] flex items-center gap-2 px-3 py-2 hover:bg-bg-hover transition-colors text-left"
            >
              <img src={avatarUrl(m)} alt="" className="w-6 h-6 rounded-full shrink-0" />
              <span className="text-sm text-fg truncate">{m.display_name}</span>
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
