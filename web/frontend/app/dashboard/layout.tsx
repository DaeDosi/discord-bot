"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Navbar from "@/components/Navbar";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

const USER_CACHE_KEY = "discord_user";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(() => {
    // 새로고침 시 캐시된 유저 정보를 즉시 표시
    if (typeof window === "undefined") return null;
    try {
      const cached = localStorage.getItem(USER_CACHE_KEY);
      return cached ? JSON.parse(cached) : null;
    } catch { return null; }
  });

  useEffect(() => {
    if (!localStorage.getItem("token")) {
      router.replace("/login");
      return;
    }
    api.auth.me()
      .then((u) => {
        localStorage.setItem(USER_CACHE_KEY, JSON.stringify(u));
        setUser(u);
      })
      .catch((e: Error) => {
        // 401은 api.ts에서 이미 토큰 삭제 + /login 이동 처리됨
        // 네트워크 오류일 때는 토큰을 유지하고 캐시된 유저 정보를 그대로 사용
        if (e.message === "Unauthorized") {
          router.replace("/login");
        }
      });
  }, [router]);

  return (
    <div className="min-h-screen flex flex-col">
      <Navbar user={user} />
      <main className="flex-1">{children}</main>
    </div>
  );
}
