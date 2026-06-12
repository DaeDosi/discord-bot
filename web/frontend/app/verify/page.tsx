"use client";
import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Bot, CheckCircle, AlertCircle, Loader2 } from "lucide-react";
import { api } from "@/lib/api";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Status = "loading" | "unauthenticated" | "ready" | "verifying" | "success" | "error";

function VerifyContent() {
  const params   = useSearchParams();
  const guildId  = params.get("guild_id") || "";
  const userId   = params.get("user_id") || "";

  const [status,      setStatus]      = useState<Status>("loading");
  const [message,     setMessage]     = useState("");
  const [username,    setUsername]    = useState("");

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
    const raw   = typeof window !== "undefined" ? localStorage.getItem("discord_user") : null;

    if (!token || !raw) {
      setStatus("unauthenticated");
      return;
    }
    try {
      const u = JSON.parse(raw);
      setUsername(u.username || "");
      setStatus("ready");
    } catch {
      setStatus("unauthenticated");
    }
  }, []);

  const goDiscordLogin = async () => {
    try {
      const d = await api.auth.getLoginUrl();
      window.location.href = d.url;
    } catch {}
  };

  const handleVerify = async () => {
    if (!guildId) {
      setStatus("error");
      setMessage("guild_id가 누락되었습니다. 디스코드 서버에서 다시 링크를 클릭해 주세요.");
      return;
    }
    setStatus("verifying");
    try {
      const token = localStorage.getItem("token");
      const res   = await fetch(`${BASE}/api/verify/${guildId}`, {
        method:  "POST",
        headers: {
          Authorization:  `Bearer ${token}`,
          "Content-Type": "application/json",
        },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "오류가 발생했습니다.");
      setStatus("success");
      setMessage(data.message || "인증이 완료되었습니다.");
    } catch (e: unknown) {
      setStatus("error");
      setMessage(e instanceof Error ? e.message : "오류가 발생했습니다.");
    }
  };

  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm bg-bg-card border border-border rounded-2xl shadow-xl p-8 flex flex-col items-center gap-6">

        <div className="flex items-center gap-2 text-xl font-bold text-fg">
          <Bot size={22} className="text-accent" />
          NexBot
        </div>

        {status === "loading" && (
          <Loader2 size={28} className="text-muted animate-spin" />
        )}

        {status === "unauthenticated" && (
          <>
            <p className="text-center text-sm text-muted leading-relaxed">
              인증을 진행하려면 먼저 Discord로 로그인해야 합니다.
            </p>
            <button
              onClick={goDiscordLogin}
              className="w-full py-2.5 bg-accent hover:bg-accent-hover text-white rounded-xl
                         text-sm font-medium transition-colors"
            >
              Discord로 로그인
            </button>
          </>
        )}

        {status === "ready" && (
          <>
            <div className="text-center space-y-2">
              <p className="text-sm text-muted leading-relaxed">
                <span className="text-fg font-medium">{username || "사용자"}</span>님,
                아래 버튼을 눌러 서버 입장을 확인하세요.
              </p>
              {!guildId && (
                <p className="text-xs text-danger">
                  ⚠️ guild_id가 없습니다. 디스코드 서버의 인증 링크를 다시 클릭해 주세요.
                </p>
              )}
            </div>
            <button
              onClick={handleVerify}
              disabled={!guildId}
              className="w-full py-2.5 bg-accent hover:bg-accent-hover disabled:opacity-40
                         text-white rounded-xl text-sm font-medium transition-colors"
            >
              📺 인증하기
            </button>
          </>
        )}

        {status === "verifying" && (
          <div className="flex flex-col items-center gap-3">
            <Loader2 size={28} className="text-accent animate-spin" />
            <p className="text-muted text-sm">인증 처리 중...</p>
          </div>
        )}

        {status === "success" && (
          <>
            <CheckCircle size={48} className="text-green-400" />
            <div className="text-center space-y-1">
              <p className="font-semibold text-fg">인증 완료!</p>
              <p className="text-sm text-muted">{message}</p>
              <p className="text-xs text-muted/60 pt-1">
                이제 디스코드 서버로 돌아가세요.
              </p>
            </div>
          </>
        )}

        {status === "error" && (
          <>
            <AlertCircle size={48} className="text-danger" />
            <div className="text-center space-y-1">
              <p className="font-semibold text-fg">오류 발생</p>
              <p className="text-sm text-muted">{message}</p>
            </div>
            <button
              onClick={() => setStatus("ready")}
              className="w-full py-2.5 border border-border text-fg rounded-xl text-sm
                         hover:bg-bg-hover transition-colors"
            >
              다시 시도
            </button>
          </>
        )}

        <Link href="/" className="text-xs text-muted hover:text-fg transition-colors">
          홈으로 돌아가기
        </Link>
      </div>
    </div>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-bg flex items-center justify-center">
        <Loader2 size={28} className="text-muted animate-spin" />
      </div>
    }>
      <VerifyContent />
    </Suspense>
  );
}
