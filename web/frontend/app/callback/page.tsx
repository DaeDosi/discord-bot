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
      // Discord가 준 error 코드(access_denied 등)를 그대로 노출하지 않는다 —
      // 사용자가 할 수 있는 일만 말한다.
      setErrMsg("Discord 로그인이 취소되었거나 승인되지 않았습니다.");
      return;
    }

    // 백엔드가 직접 token을 전달한 경우 (GET /auth/callback 경유)
    // 서버 접근 로그/Referer에 남지 않도록 쿼리스트링이 아닌 URL 프래그먼트(#token=...)로 전달됨.
    //
    // ?token= 쿼리스트링은 더 이상 받지 않는다. 백엔드는 프래그먼트로만 보내는데,
    // 쿼리 폴백을 남겨 두면 (a) 서버 접근 로그·Referer·브라우저 히스토리에 토큰이 남고
    // (b) 링크 한 방으로 임의 토큰을 심을 수 있는 경로가 열린다.
    const hashParams = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const token = hashParams.get("token");
    if (token) {
      // 프래그먼트를 주소창에서 즉시 제거 — 브라우저 히스토리에 남지 않도록
      if (window.location.hash) {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
      localStorage.setItem("token", token);
      // 유저 정보를 미리 캐싱해 어느 페이지에서든 즉시 로그인 상태 표시
      api.auth.me()
        .then((u) => {
          localStorage.setItem("discord_user", JSON.stringify(u));
        })
        .catch(() => { /* 실패해도 토큰은 저장됨 */ })
        .finally(() => {
          // 로그인 전 페이지(예: /verify?guild_id=...)로 복귀
          const returnUrl = localStorage.getItem("auth_return_url");
          localStorage.removeItem("auth_return_url");
          router.replace(returnUrl || "/dashboard");
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
      .catch(() => {
        setStatus("error");
        // 서버가 준 원문(예: "internal")은 사용자에게 의미가 없고 내부 정보일 수 있다.
        // 분류는 상태 코드로 하고, 화면에는 사용자가 할 수 있는 일만 적는다.
        setErrMsg("로그인 처리 중 문제가 발생했습니다. 잠시 후 다시 시도해 주세요.");
      });
  }, [params, router]);

  if (status === "error") {
    return (
      <div className="min-h-screen flex items-center justify-center px-4">
        <div className="card max-w-sm w-full text-center space-y-4">
          <AlertCircle size={40} className="text-danger mx-auto" />
          <h2 className="text-fg font-semibold">로그인 실패</h2>
          <p className="text-muted text-sm whitespace-pre-line">{errMsg}</p>

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
        <p className="text-fg font-medium">로그인 중...</p>
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
