"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { Bot, ChevronLeft, ChevronRight, Radio, Users, ExternalLink } from "lucide-react";
import Footer from "@/components/Footer";
import { api } from "@/lib/api";

interface CommunityEntry {
  guild_id: string;
  name: string;
  icon: string | null;
  description: string;
  invite_url: string | null;
  chzzk_channel_id: string | null;
  chzzk_name: string | null;
  chzzk_image_url: string | null;
  chzzk_is_live: boolean;
}

function CommunityCard({ entry }: { entry: CommunityEntry }) {
  const chzzkUrl = entry.chzzk_channel_id
    ? `https://chzzk.naver.com/${entry.chzzk_channel_id}`
    : null;
  const inviteUrl = entry.invite_url || null;

  return (
    <div className="card flex flex-col gap-4">
      <div className="flex items-center gap-3">
        {entry.icon ? (
          <Image src={entry.icon} alt={entry.name} width={48} height={48} className="rounded-xl shrink-0" />
        ) : (
          <div className="w-12 h-12 rounded-xl bg-bg-hover flex items-center justify-center shrink-0">
            <Users size={20} className="text-muted" />
          </div>
        )}
        <div className="min-w-0">
          <p className="font-semibold text-fg truncate">{entry.name}</p>
          {entry.chzzk_name && (
            <p className="flex items-center gap-1.5 text-xs text-muted truncate">
              <Radio size={12} className={entry.chzzk_is_live ? "text-[#03C75A]" : ""} />
              {entry.chzzk_name}
              {entry.chzzk_is_live && (
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded"
                      style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>
                  LIVE
                </span>
              )}
            </p>
          )}
        </div>
      </div>

      {entry.description && (
        <p className="text-sm text-muted leading-relaxed line-clamp-4">{entry.description}</p>
      )}

      {(chzzkUrl || inviteUrl) ? (
        <div className="mt-auto flex flex-col gap-2">
          {inviteUrl && (
            <a
              href={inviteUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center justify-center gap-1.5 text-sm font-medium
                         px-4 py-2 rounded-lg bg-[#5865F2] hover:bg-[#4752C4] text-white transition-colors"
            >
              디스코드 참여하기 <ExternalLink size={13} />
            </a>
          )}
          {chzzkUrl && (
            <a
              href={chzzkUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center justify-center gap-1.5 text-sm font-medium
                         px-4 py-2 rounded-lg border border-border hover:border-accent/40 hover:bg-bg-hover
                         text-fg transition-colors"
            >
              치지직 바로가기 <ExternalLink size={13} />
            </a>
          )}
        </div>
      ) : (
        <p className="mt-auto text-xs text-muted/60 italic">
          아직 이동 링크가 등록되지 않았습니다.
        </p>
      )}
    </div>
  );
}

export default function CommunityPage() {
  const [entries, setEntries] = useState<CommunityEntry[] | null>(null);

  useEffect(() => {
    api.community.list().then(setEntries).catch(() => setEntries([]));
  }, []);

  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-6xl mx-auto px-5 flex items-center" style={{ height: 60 }}>
          <Link href="/" className="flex items-center gap-2 font-bold text-[17px] text-fg">
            <Bot size={20} className="text-accent" />
            NexBot
          </Link>
        </div>
      </header>

      <main className="flex-1 max-w-6xl mx-auto w-full px-5 py-12 pb-20">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors mb-8"
        >
          <ChevronLeft size={15} /> 홈으로
        </Link>

        <h1 className="page-title mb-3">커뮤니티</h1>
        <p className="text-muted text-base sm:text-lg leading-relaxed max-w-2xl mb-10">
          NexBot을 사용하는 스트리머·디스코드 서버를 소개하는 공간입니다. 서버 관리자가
          직접 공개로 설정한 서버만 여기에 노출되며, 각 서버의 방송 채널로 바로 이동할 수 있습니다.
          내 서버를 알리고 싶다면 대시보드의 &quot;일반 설정&quot;에서 커뮤니티 페이지 공개를
          켜고 소개 문구를 작성해보세요.
        </p>

        {entries === null ? (
          <div className="text-muted animate-pulse py-12 text-center">불러오는 중...</div>
        ) : entries.length === 0 ? (
          <div className="rounded-xl bg-bg-card border border-border px-5 py-16 text-center">
            <Users size={32} className="mx-auto mb-3 text-muted opacity-60" />
            <p className="text-muted">아직 공개된 서버가 없습니다.</p>
            <p className="text-muted text-sm mt-1">가장 먼저 커뮤니티에 등록해보세요!</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {entries.map((e) => (
              <CommunityCard key={e.guild_id} entry={e} />
            ))}
          </div>
        )}

        <div className="mt-16 pt-8 border-t border-border">
          <Link
            href="/guide"
            className="text-sm text-accent hover:text-accent-hover transition-colors flex items-center gap-1"
          >
            NexBot 사용 방법 알아보기
            <ChevronRight size={13} />
          </Link>
        </div>
      </main>

      <Footer />
    </div>
  );
}
