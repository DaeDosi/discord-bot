"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import {
  Settings, Zap, Shield, Radio, ChevronLeft, Smile,
} from "lucide-react";

const ITEMS = [
  { href: "",           label: "일반 설정",   icon: <Settings size={16} /> },
  { href: "/leveling",  label: "레벨링",      icon: <Zap size={16} /> },
  { href: "/moderation",label: "관리",        icon: <Shield size={16} /> },
  { href: "/chzzk",     label: "치지직",      icon: <Radio size={16} /> },
];

export default function Sidebar({ guildId, guildName }: { guildId: string; guildName?: string }) {
  const pathname = usePathname();
  const base     = `/dashboard/${guildId}`;

  return (
    <aside className="w-56 shrink-0 flex flex-col gap-1 pt-2">
      <Link
        href="/dashboard"
        className="flex items-center gap-2 text-muted hover:text-white text-sm px-3 py-2 rounded-lg
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
        return (
          <Link
            key={item.href}
            href={href}
            className={clsx(
              "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors",
              active
                ? "bg-accent/15 text-accent"
                : "text-muted hover:text-white hover:bg-bg-hover"
            )}
          >
            {item.icon}
            {item.label}
          </Link>
        );
      })}
    </aside>
  );
}
