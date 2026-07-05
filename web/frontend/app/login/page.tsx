"use client";
import { useEffect, useState } from "react";
import { Bot, AlertCircle } from "lucide-react";
import { api } from "@/lib/api";

export default function LoginPage() {
  const [error, setError] = useState(false);

  useEffect(() => {
    // 이미 로그인 상태면 대시보드로 바로 이동
    if (localStorage.getItem("token") && localStorage.getItem("discord_user")) {
      window.location.replace("/dashboard");
      return;
    }

    // redirect_uri는 프론트엔드가 자체적으로 만들지 않고 항상 백엔드(/api/auth/login)가
    // 계산한 값을 그대로 사용한다 — 예전에는 이 페이지가 NEXT_PUBLIC_DISCORD_REDIRECT_URI로
    // 직접 인증 URL을 만들었는데, 홈페이지 로그인 버튼(api.auth.getLoginUrl 사용)과 서로 다른
    // redirect_uri를 보내는 바람에 어느 경로로 로그인을 시작했는지에 따라 "잘못된 OAuth
    // redirect_uri" 에러가 나거나 안 나는 문제가 있었다.
    api.auth.getLoginUrl()
      .then((d) => { window.location.href = d.url; })
      .catch(() => setError(true));
  }, []);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center px-4">
        <div className="card max-w-sm w-full text-center space-y-4">
          <AlertCircle size={40} className="text-danger mx-auto" />
          <h2 className="text-fg font-semibold">로그인을 시작할 수 없습니다</h2>
          <p className="text-muted text-sm">
            백엔드 서버에 연결하지 못했습니다.
          </p>
          <p className="text-muted text-xs leading-relaxed">
            <code className="text-xs">NEXT_PUBLIC_API_URL</code> 설정과 백엔드 서버 실행 상태를 확인해주세요.
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
        <p className="text-fg font-medium">Discord로 이동 중...</p>
        <p className="text-muted text-sm">잠시만 기다려주세요.</p>
      </div>
    </div>
  );
}
