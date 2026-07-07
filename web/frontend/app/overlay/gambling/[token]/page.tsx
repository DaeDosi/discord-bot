"use client";
import { useParams } from "next/navigation";
import { useOverlayPoll } from "@/lib/useOverlayPoll";
import { GamblingOverlayView, type OverlayStatus } from "@/components/GamblingOverlayView";

// OBS 브라우저 소스 전용 — 로그인 세션 없이 URL의 토큰만으로 guild를 식별하는 공개 페이지.
export default function GamblingOverlayPage() {
  const { token } = useParams<{ token: string }>();
  const { data: status, error: connError } = useOverlayPoll<OverlayStatus>(
    token ? `/api/chzzk/overlay/${token}/gambling-status` : null,
    2000
  );

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
        <GamblingOverlayView status={status} connError={connError} />
      </div>
    </>
  );
}
