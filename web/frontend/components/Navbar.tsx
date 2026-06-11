"use client";
import Link from "next/link";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { Bot, LogOut } from "lucide-react";
import type { User } from "@/lib/types";

interface Props { user: User | null }

export default function Navbar({ user }: Props) {
  const router = useRouter();
  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("discord_user");
    router.push("/");
  };

  return (
    <nav className="border-b border-border bg-bg-card/60 backdrop-blur sticky top-0 z-50 h-16">
      <div className="max-w-6xl mx-auto px-4 h-full flex items-center justify-between">
        <Link href="/dashboard" className="flex items-center gap-2 font-bold text-lg">
          <Bot size={22} className="text-accent" />
          NexBot
        </Link>
        {user && (
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              {user.avatar && (
                <Image
                  src={user.avatar}
                  alt={user.username}
                  width={32} height={32}
                  className="rounded-full"
                />
              )}
              <span className="text-sm text-white hidden sm:block">{user.username}</span>
            </div>
            <button
              onClick={logout}
              className="p-2 rounded-lg hover:bg-bg-hover text-muted hover:text-white transition-colors"
              title="로그아웃"
            >
              <LogOut size={16} />
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
