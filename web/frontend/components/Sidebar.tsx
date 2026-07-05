"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import { Settings, Heart, Shield, Radio, UserCheck, HelpCircle, ChevronLeft, Terminal, Gem, Youtube, Tv, Swords } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "@/lib/api";

interface NavItem { href: string; label: string; shortLabel: string; icon: LucideIcon }
interface NavGroup { label: string; items: NavItem[] }

// 디스코드 서버 관리 / 방송 설정 / 지원 세 그룹으로 분리 — 카테고리 라벨 + 구분선으로 시각적으로 나눔
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
    label: "지원",
    items: [
      { href: "/commands", label: "명령어",   shortLabel: "명령어", icon: Terminal   },
      { href: "/help",     label: "문제 해결", shortLabel: "도움말", icon: HelpCircle },
    ],
  },
];

export default function Sidebar({ guildId, guildName }: { guildId: string; guildName?: string }) {
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
      <aside className="hidden md:flex w-56 shrink-0 flex-col gap-1 pt-2">
        <Link
          href="/dashboard"
          className="flex items-center gap-2 text-muted hover:text-fg text-base font-medium px-3 py-2.5 rounded-lg
                     hover:bg-bg-hover transition-colors mb-2"
        >
          <ChevronLeft size={18} /> 서버 목록
        </Link>
        {guildName && (
          <p className="text-xs font-semibold text-muted uppercase tracking-wider px-3 mb-1 truncate">
            {guildName}
          </p>
        )}
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
