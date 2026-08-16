"use client";
import { useEffect, useState, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";
import { Settings, Plus, Server, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import DashboardError from "@/components/DashboardError";
import { isHandledElsewhere } from "@/lib/dashboardErrors";
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
  const [listError, setListError]   = useState<unknown>(null);

  // 실패를 빈 배열로 바꿔치기하면 "서버가 없다"와 "불러오지 못했다"가 같은 화면이 된다
  // (실측: GET /api/guilds 500 에도 '관리 권한이 있는 서버가 없습니다'가 떴다).
  const loadGuilds = useCallback(async () => {
    try {
      setListError(null);
      setGuilds(await api.guilds.list());
    } catch (e: unknown) {
      // 401은 api.ts가 이미 토큰 삭제 + /login 이동을 처리한다.
      if (!isHandledElsewhere(e)) setListError(e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
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
        <h1 className="page-title">내 서버</h1>
        <button
          onClick={() => { setRefreshing(true); loadGuilds(); }}
          disabled={refreshing}
          className="min-w-[44px] min-h-[44px] flex items-center justify-center
                     text-muted hover:text-fg transition-colors rounded-lg hover:bg-bg-hover"
          aria-label="서버 목록 새로고침"
          title="새로고침"
        >
          <RefreshCw size={16} className={refreshing ? "animate-spin" : ""} />
        </button>
      </div>
      <p className="page-subtitle mb-8">관리자 권한이 있는 서버만 표시됩니다.</p>

      {loading ? (
        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="card animate-pulse h-20 bg-bg-hover" />
          ))}
        </div>
      ) : listError ? (
        <DashboardError error={listError} onRetry={() => { setRefreshing(true); loadGuilds(); }} />
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
            <div className="text-center py-20 text-muted space-y-3">
              <Server size={40} className="mx-auto mb-3 opacity-40" />
              <p>관리 권한이 있는 서버가 없습니다.</p>
              <p className="text-sm">
                Discord에서 서버를 만들거나 관리 권한을 받은 뒤 봇을 초대해 주세요.
              </p>
              <a
                href={getBotInviteUrl()}
                target="_blank"
                rel="noreferrer"
                className="btn-primary text-sm mx-auto"
              >
                <Plus size={14} /> 봇 초대하기
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
