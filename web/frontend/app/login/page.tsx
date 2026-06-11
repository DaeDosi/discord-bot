"use client";
import { useEffect, useState } from "react";
import { Bot, AlertCircle } from "lucide-react";

function buildOAuthUrl(): string {
  const clientId    = process.env.NEXT_PUBLIC_DISCORD_CLIENT_ID ?? "";
  const redirectUri = process.env.NEXT_PUBLIC_DISCORD_REDIRECT_URI ?? "http://localhost:3000/callback";
  const params = new URLSearchParams({
    client_id:     clientId,
    redirect_uri:  redirectUri,
    response_type: "code",
    scope:         "identify guilds",
  });
  return `https://discord.com/api/oauth2/authorize?${params}`;
}

export default function LoginPage() {
  const [error, setError] = useState(false);

  useEffect(() => {
    const clientId = process.env.NEXT_PUBLIC_DISCORD_CLIENT_ID;
    if (!clientId) { setError(true); return; }

    // 이미 로그인 상태면 대시보드로 바로 이동
    if (localStorage.getItem("token") && localStorage.getItem("discord_user")) {
      window.location.replace("/dashboard");
      return;
    }

    const t = setTimeout(() => {
      window.location.href = buildOAuthUrl();
    }, 300);
    return () => clearTimeout(t);
  }, []);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center px-4">
        <div className="card max-w-sm w-full text-center space-y-4">
          <AlertCircle size={40} className="text-danger mx-auto" />
          <h2 className="text-white font-semibold">설정 필요</h2>
          <p className="text-muted text-sm">
            <code className="bg-bg px-1.5 py-0.5 rounded text-accent text-xs">
              NEXT_PUBLIC_DISCORD_CLIENT_ID
            </code>
            가 설정되지 않았습니다.
          </p>
          <p className="text-muted text-xs leading-relaxed">
            <code className="text-xs">web/frontend/.env.local</code> 파일을 생성하고<br />
            Discord Client ID를 입력해주세요.
          </p>
          <a
            href="https://discord.com/developers/applications"
            target="_blank"
            rel="noreferrer"
            className="btn-primary justify-center text-sm"
          >
            Developer Portal 열기
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center space-y-3">
        <Bot size={48} className="text-accent mx-auto animate-pulse" />
        <p className="text-white font-medium">Discord로 이동 중...</p>
        <p className="text-muted text-sm">잠시만 기다려주세요.</p>
      </div>
    </div>
  );
}
