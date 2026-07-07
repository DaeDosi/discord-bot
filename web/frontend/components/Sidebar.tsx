"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import {
  Settings, Heart, Shield, Radio, UserCheck, HelpCircle, ChevronLeft, ChevronDown,
  Terminal, Gem, Youtube, Tv, Twitter, Swords, MonitorPlay, Server,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import type { Guild } from "@/lib/types";

interface NavItem { href: string; label: string; shortLabel: string; icon: LucideIcon }
interface NavGroup { label: string; items: NavItem[] }

// 디스코드 서버 관리 / 방송 / SNS / 지원 네 그룹으로 분리 — 카테고리 라벨 + 구분선으로 시각적으로 나눔
const BASE_GROUPS: NavGroup[] = [
  {
    label: "서버 관리",
    items: [
      { href: "",               label: "일반 설정", shortLabel: "일반설정", icon: Settings  },
      { href: "/verification",  label: "입장 인증", shortLabel: "인증",     icon: UserCheck },
      { href: "/leveling",      label: "애정도",    shortLabel: "애정도",   icon: Heart     },
      { href: "/moderation",    label: "관리",      shortLabel: "관리",     icon: Shield    },
      { href: "/points",        label: "포인트",    shortLabel: "포인트",   icon: Gem       },
    ],
  },
  {
    label: "방송",
    items: [
      { href: "/chzzk",   label: "치지직",     shortLabel: "치지직",   icon: Radio   },
      { href: "/youtube", label: "유튜브",     shortLabel: "유튜브",   icon: Youtube },
      { href: "/soop",    label: "숲 (SOOP)",  shortLabel: "숲",       icon: Tv      },
    ],
  },
  {
    label: "SNS",
    items: [
      { href: "/twitter", label: "X (트위터)", shortLabel: "트위터", icon: Twitter },
    ],
  },
  {
    label: "지원",
    items: [
      { href: "/commands", label: "명령어",     shortLabel: "명령어",  icon: Terminal    },
      { href: "/overlay",  label: "오버레이",   shortLabel: "오버레이", icon: MonitorPlay },
      { href: "/help",     label: "문제 해결",   shortLabel: "도움말",  icon: HelpCircle  },
    ],
  },
];

// ── 서버 스위처: 현재 관리 중인 서버 이름/아이콘을 보여주고, 클릭하면 다른 등록 서버로 바로 이동 ──
function GuildSwitcher({ guildId }: { guildId: string }) {
  const [guilds, setGuilds] = useState<Guild[]>([]);
  const [open, setOpen]     = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.guilds.list().then(setGuilds).catch(() => {});
  }, []);

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const current  = guilds.find((g) => String(g.id) === String(guildId));
  const managed  = guilds.filter((g) => g.has_bot);

  return (
    <div className="relative mb-2" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg hover:bg-bg-hover transition-colors text-left"
      >
        {current?.icon ? (
          <Image src={current.icon} alt={current.name} width={28} height={28} className="rounded-lg shrink-0" />
        ) : (
          <div className="w-7 h-7 rounded-lg bg-bg-hover flex items-center justify-center shrink-0">
            <Server size={14} className="text-muted" />
          </div>
        )}
        <span className="flex-1 min-w-0 text-base font-semibold text-fg truncate">
          {current?.name || "서버 선택"}
        </span>
        <ChevronDown size={16} className={clsx("text-muted shrink-0 transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-bg-card border border-border rounded-xl shadow-xl py-1.5 max-h-80 overflow-y-auto">
          {managed.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted">불러오는 중...</p>
          ) : (
            managed.map((g) => (
              <Link
                key={g.id}
                href={`/dashboard/${g.id}`}
                onClick={() => setOpen(false)}
                className={clsx(
                  "flex items-center gap-2.5 px-3 py-2 mx-1.5 rounded-lg transition-colors",
                  String(g.id) === String(guildId) ? "bg-accent/15 text-accent" : "text-fg hover:bg-bg-hover"
                )}
              >
                {g.icon ? (
                  <Image src={g.icon} alt={g.name} width={24} height={24} className="rounded-md shrink-0" />
                ) : (
                  <div className="w-6 h-6 rounded-md bg-bg-hover flex items-center justify-center shrink-0">
                    <Server size={12} className="text-muted" />
                  </div>
                )}
                <span className="text-sm truncate">{g.name}</span>
              </Link>
            ))
          )}
          <div className="border-t border-border mt-1.5 pt-1.5 mx-1.5">
            <Link
              href="/dashboard"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-muted hover:text-fg hover:bg-bg-hover transition-colors"
            >
              <ChevronLeft size={14} /> 전체 서버 목록
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Sidebar({ guildId }: { guildId: string }) {
  const pathname = usePathname();
  const base     = `/dashboard/${guildId}`;

  // MC 콜라보 이벤트에 초대된 서버에서만 사이드바에 "이벤트 서버(합방)" 탭이 보인다
  const [mcEventInvited, setMcEventInvited] = useState(false);
  useEffect(() => {
    api.chzzk.mcEvent.status(guildId)
      .then((d) => setMcEventInvited(!!d.invited))
      .catch(() => setMcEventInvited(false));
  }, [guildId]);

  const GROUPS: NavGroup[] = BASE_GROUPS.map((group) => {
    if (group.label !== "방송" || !mcEventInvited) return group;
    return {
      ...group,
      items: [...group.items, { href: "/mc-event", label: "이벤트 서버 (합방)", shortLabel: "합방", icon: Swords }],
    };
  });

  return (
    <>
      {/* ── Desktop sidebar (md+) ── */}
      <aside className="hidden md:flex w-60 shrink-0 flex-col gap-1 pt-2">
        <GuildSwitcher guildId={guildId} />
        {GROUPS.map((group, gi) => (
          <div
            key={group.label}
            className={clsx("flex flex-col gap-1", gi > 0 && "mt-3 pt-3 border-t border-border")}
          >
            <p className="text-sm font-semibold text-muted/70 uppercase tracking-wider px-3 mb-1">
              {group.label}
            </p>
            {group.items.map((item) => {
              const href   = base + item.href;
              const active = pathname === href;
              const Icon   = item.icon;
              return (
                <Link
                  key={item.href}
                  href={href}
                  className={clsx(
                    "flex items-center gap-3 px-3 py-2 rounded-lg text-base font-medium transition-colors",
                    active
                      ? "bg-accent/15 text-accent"
                      : "text-muted hover:text-fg hover:bg-bg-hover"
                  )}
                >
                  <Icon size={17} />
                  {item.label}
                </Link>
              );
            })}
          </div>
        ))}
      </aside>

      {/* ── Mobile bottom nav (< md) ── */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 z-40
                      border-t border-border bg-bg-card/95 backdrop-blur-sm">
        <div className="flex items-stretch h-14">
          {/* Back to server list */}
          <Link
            href="/dashboard"
            className="flex-1 flex flex-col items-center justify-center gap-0.5
                       text-muted hover:text-fg transition-colors"
          >
            <ChevronLeft size={20} />
            <span className="text-[10px]">서버 목록</span>
          </Link>

          {/* Nav groups — 그룹 사이에 세로 구분선 */}
          {GROUPS.map((group, gi) => (
            <div
              key={group.label}
              className={clsx("flex items-stretch", gi > 0 && "border-l border-border")}
              style={{ flex: group.items.length }}
            >
              {group.items.map((item) => {
                const href   = base + item.href;
                const active = pathname === href;
                const Icon   = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={href}
                    className={clsx(
                      "flex-1 flex flex-col items-center justify-center gap-0.5 transition-colors",
                      active ? "text-accent" : "text-muted hover:text-fg"
                    )}
                  >
                    <Icon size={20} />
                    <span className="text-[10px] font-medium">{item.shortLabel}</span>
                  </Link>
                );
              })}
            </div>
          ))}
        </div>
      </nav>
    </>
  );
}
