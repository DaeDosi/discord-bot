"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import { Settings, Zap, Shield, Radio, UserCheck, HelpCircle, ChevronLeft, Terminal, Gem } from "lucide-react";
import type { LucideIcon } from "lucide-react";

const ITEMS: { href: string; label: string; shortLabel: string; icon: LucideIcon }[] = [
  { href: "",               label: "일반 설정",    shortLabel: "일반설정", icon: Settings    },
  { href: "/verification",  label: "입장 인증",    shortLabel: "인증",     icon: UserCheck   },
  { href: "/leveling",      label: "레벨업",       shortLabel: "레벨업",   icon: Zap         },
  { href: "/moderation",    label: "관리",         shortLabel: "관리",     icon: Shield      },
  { href: "/points",        label: "포인트",       shortLabel: "포인트",   icon: Gem         },
  { href: "/chzzk",         label: "방송설정",     shortLabel: "방송설정", icon: Radio       },
  { href: "/commands",      label: "명령어",       shortLabel: "명령어",   icon: Terminal    },
  { href: "/help",          label: "문제 해결",    shortLabel: "도움말",   icon: HelpCircle  },
];

export default function Sidebar({ guildId, guildName }: { guildId: string; guildName?: string }) {
  const pathname = usePathname();
  const base     = `/dashboard/${guildId}`;

  return (
    <>
      {/* ── Desktop sidebar (md+) ── */}
      <aside className="hidden md:flex w-56 shrink-0 flex-col gap-1 pt-2">
        <Link
          href="/dashboard"
          className="flex items-center gap-2 text-muted hover:text-fg text-sm px-3 py-2 rounded-lg
                     hover:bg-bg-hover transition-colors mb-2"
        >
          <ChevronLeft size={14} /> 서버 목록
        </Link>
        {guildName && (
          <p className="text-xs font-semibold text-muted uppercase tracking-wider px-3 mb-1 truncate">
            {guildName}
          </p>
        )}
        {ITEMS.map((item) => {
          const href   = base + item.href;
          const active = pathname === href;
          const Icon   = item.icon;
          return (
            <Link
              key={item.href}
              href={href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors",
                active
                  ? "bg-accent/15 text-accent"
                  : "text-muted hover:text-fg hover:bg-bg-hover"
              )}
            >
              <Icon size={16} />
              {item.label}
            </Link>
          );
        })}
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

          {/* Nav items */}
          {ITEMS.map((item) => {
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
      </nav>
    </>
  );
}
