"use client";
import { useEffect, useState, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";
import { Settings, Plus, Server, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { Guild } from "@/lib/types";

const CLIENT_ID = process.env.NEXT_PUBLIC_DISCORD_CLIENT_ID || "";

function getBotInviteUrl(guildId?: string) {
  const base = `https://discord.com/oauth2/authorize?client_id=${CLIENT_ID}&permissions=8&scope=bot%20applications.commands`;
  if (!guildId) return base;
  return `${base}&guild_id=${guildId}&disable_guild_select=true`;
}

function GuildCard({ guild, onInvite }: { guild: Guild; onInvite: (guildId: string) => void }) {
  return (
    <div className="card flex items-center gap-4 hover:border-accent/30 transition-colors">
      {guild.icon ? (
        <Image
          src={guild.icon}
          alt={guild.name}
          width={52} height={52}
          className="rounded-xl shrink-0"
        />
      ) : (
        <div className="w-[52px] h-[52px] rounded-xl bg-bg-hover flex items-center justify-center shrink-0">
          <Server size={24} className="text-muted" />
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-fg text-base truncate">{guild.name}</p>
        <p className="text-sm text-muted mt-0.5">
          {guild.has_bot ? "봇 설치됨" : "봇 미설치"}
        </p>
      </div>
      {guild.has_bot ? (
        <Link href={`/dashboard/${guild.id}`} className="btn-primary text-sm shrink-0">
          <Settings size={14} /> 관리
        </Link>
      ) : (
        <button
          onClick={() => onInvite(guild.id)}
          className="btn-secondary text-sm shrink-0"
        >
          <Plus size={14} /> 봇 초대
        </button>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const [guilds, setGuilds]   = useState<Guild[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadGuilds = useCallback(async () => {
    const data = await api.guilds.list().catch(() => []);
    setGuilds(data);
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => { loadGuilds(); }, [loadGuilds]);

  // 초대 창을 팝업으로 열고, 닫히면 자동으로 목록 갱신
  const handleInvite = (guildId: string) => {
    const url = getBotInviteUrl(guildId);
    const popup = window.open(url, "bot-invite", "width=500,height=800");
    if (!popup) {
      // 팝업 차단 시 새 탭으로 열기
      window.open(url, "_blank");
      return;
    }
    const timer = setInterval(() => {
      if (popup.closed) {
        clearInterval(timer);
        setRefreshing(true);
        // 봇이 서버에 반영되는 시간을 고려해 1.5초 후 갱신
        setTimeout(() => loadGuilds(), 1500);
      }
    }, 500);
  };

  const withBot    = guilds.filter((g) => g.has_bot);
  const withoutBot = guilds.filter((g) => !g.has_bot);

  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-2xl font-bold text-white">내 서버</h1>
        <button
          onClick={() => { setRefreshing(true); loadGuilds(); }}
          disabled={refreshing}
          className="p-2 text-muted hover:text-white transition-colors rounded-lg hover:bg-bg-hover"
          title="새로고침"
        >
          <RefreshCw size={16} className={refreshing ? "animate-spin" : ""} />
        </button>
      </div>
      <p className="text-muted mb-8">관리자 권한이 있는 서버만 표시됩니다.</p>

      {loading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="card animate-pulse h-20 bg-bg-hover" />
          ))}
        </div>
      ) : (
        <div className="space-y-8">
          {withBot.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">
                봇 설치된 서버
              </h2>
              <div className="space-y-3">
                {withBot.map((g) => <GuildCard key={g.id} guild={g} onInvite={handleInvite} />)}
              </div>
            </section>
          )}
          {withoutBot.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">
                봇 미설치 서버
              </h2>
              <div className="space-y-3">
                {withoutBot.map((g) => <GuildCard key={g.id} guild={g} onInvite={handleInvite} />)}
              </div>
            </section>
          )}
          {guilds.length === 0 && (
            <div className="text-center py-20 text-muted">
              <Server size={40} className="mx-auto mb-3 opacity-40" />
              <p>관리 권한이 있는 서버가 없습니다.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
