"use client";
import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { BASE } from "@/lib/api";
import { MissionsOverlayView, type MissionsOverlayStatus } from "@/components/MissionsOverlayView";

// OBS 브라우저 소스 전용 — 로그인 세션 없이 URL의 토큰만으로 guild를 식별하는 공개 페이지.
export default function MissionsOverlayPage() {
  const { token } = useParams<{ token: string }>();
  const [status, setStatus] = useState<MissionsOverlayStatus | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const load = () => {
      fetch(`${BASE}/api/chzzk/overlay/${token}/missions-status`)
        .then((r) => {
          if (r.status === 404) throw new Error("유효하지 않은 오버레이 URL입니다");
          if (!r.ok) throw new Error("서버 연결 오류");
          return r.json();
        })
        .then((d) => { setStatus(d); setConnError(null); })
        .catch((e: unknown) => setConnError(e instanceof Error ? e.message : "서버 연결 오류"));
    };
    load();
    timerRef.current = setInterval(load, 5000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [token]);

  return (
    <>
      <style>{`html, body { background: transparent !important; }`}</style>
      <div
        style={{
          width: "100vw",
          minHeight: "100vh",
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "center",
          padding: 16,
        }}
      >
        <MissionsOverlayView status={status} connError={connError} />
      </div>
    </>
  );
}
