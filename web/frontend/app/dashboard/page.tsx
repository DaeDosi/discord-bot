"use client";
import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { Settings, Plus, Server } from "lucide-react";
import { api } from "@/lib/api";
import type { Guild } from "@/lib/types";

const BOT_INVITE = process.env.NEXT_PUBLIC_BOT_INVITE || "#";

function GuildCard({ guild }: { guild: Guild }) {
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
        <p className="font-semibold text-white truncate">{guild.name}</p>
        <p className="text-xs text-muted mt-0.5">
          {guild.has_bot ? "봇 설치됨" : "봇 미설치"}
        </p>
      </div>
      {guild.has_bot ? (
        <Link href={`/dashboard/${guild.id}`} className="btn-primary text-sm shrink-0">
          <Settings size={14} /> 관리
        </Link>
      ) : (
        <a
          href={`${BOT_INVITE}&guild_id=${guild.id}`}
          target="_blank"
          rel="noreferrer"
          className="btn-secondary text-sm shrink-0"
        >
          <Plus size={14} /> 봇 초대
        </a>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const [guilds, setGuilds]   = useState<Guild[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.guilds.list()
      .then(setGuilds)
      .finally(() => setLoading(false));
  }, []);

  const withBot    = guilds.filter((g) => g.has_bot);
  const withoutBot = guilds.filter((g) => !g.has_bot);

  return (
    <div className="max-w-3xl mx-auto px-4 py-10">
      <h1 className="text-2xl font-bold text-white mb-1">내 서버</h1>
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
                {withBot.map((g) => <GuildCard key={g.id} guild={g} />)}
              </div>
            </section>
          )}
          {withoutBot.length > 0 && (
            <section>
              <h2 className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">
                봇 미설치 서버
              </h2>
              <div className="space-y-3">
                {withoutBot.map((g) => <GuildCard key={g.id} guild={g} />)}
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
