"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Bot, AlertCircle, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { Suspense } from "react";

type Status = "loading" | "error";

function CallbackInner() {
  const router       = useRouter();
  const params       = useSearchParams();
  const handledRef   = useRef(false);
  const [status, setStatus]   = useState<Status>("loading");
  const [errMsg, setErrMsg]   = useState("");

  useEffect(() => {
    if (handledRef.current) return;
    handledRef.current = true;

    const error = params.get("error");
    if (error) {
      setStatus("error");
      setErrMsg(`Discord 인증 거부: ${error}`);
      return;
    }

    // 백엔드가 직접 token을 전달한 경우 (GET /auth/callback 경유)
    const token = params.get("token");
    if (token) {
      localStorage.setItem("token", token);
      // 유저 정보를 미리 캐싱해 어느 페이지에서든 즉시 로그인 상태 표시
      api.auth.me()
        .then((u) => {
          localStorage.setItem("discord_user", JSON.stringify(u));
        })
        .catch(() => { /* 실패해도 토큰은 저장됨 */ })
        .finally(() => {
          router.replace("/dashboard");
        });
      return;
    }

    // 프론트엔드가 code를 받아 백엔드에 직접 교환하는 경우
    const code = params.get("code");
    if (!code) {
      setStatus("error");
      setErrMsg("Discord에서 코드를 받지 못했습니다.");
      return;
    }

    api.auth.callback(code)
      .then(({ token }) => {
        localStorage.setItem("token", token);
        router.replace("/dashboard");
      })
      .catch((e: Error) => {
        setStatus("error");
        if (e.message.includes("fetch") || e.message.includes("network")) {
          setErrMsg("백엔드 서버에 연결할 수 없습니다.\n환경변수 NEXT_PUBLIC_API_URL을 확인해주세요.");
        } else {
          setErrMsg(e.message || "인증에 실패했습니다.");
        }
      });
  }, [params, router]);

  if (status === "error") {
    return (
      <div className="min-h-screen flex items-center justify-center px-4">
        <div className="card max-w-sm w-full text-center space-y-4">
          <AlertCircle size={40} className="text-danger mx-auto" />
          <h2 className="text-white font-semibold">로그인 실패</h2>
          <p className="text-muted text-sm whitespace-pre-line">{errMsg}</p>

          <div className="bg-bg rounded-lg p-3 text-left text-xs text-muted space-y-1">
            <p className="text-white font-medium mb-2">체크리스트</p>
            <p>① 백엔드 실행 여부 확인</p>
            <code className="block bg-black/30 rounded px-2 py-1 text-accent">
              cd web/backend && uvicorn main:app --reload --port 8000
            </code>
            <p className="mt-2">② Discord Developer Portal → Redirects</p>
            <code className="block bg-black/30 rounded px-2 py-1 text-accent">
              http://localhost:3000/callback
            </code>
            <p className="mt-2">③ .env 파일 DISCORD_CLIENT_SECRET 확인</p>
          </div>

          <button
            onClick={() => { window.location.href = "/login"; }}
            className="btn-primary justify-center w-full"
          >
            <RefreshCw size={16} /> 다시 시도
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-center space-y-3">
        <Bot size={48} className="text-accent mx-auto animate-pulse" />
        <p className="text-white font-medium">로그인 중...</p>
        <p className="text-muted text-sm">잠시만 기다려주세요.</p>
      </div>
    </div>
  );
}

export default function CallbackPage() {
  return (
    <Suspense>
      <CallbackInner />
    </Suspense>
  );
}
