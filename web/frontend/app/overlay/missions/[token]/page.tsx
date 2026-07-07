"use client";
import { useParams } from "next/navigation";
import { useOverlayPoll } from "@/lib/useOverlayPoll";
import { MissionsOverlayView, type MissionsOverlayStatus } from "@/components/MissionsOverlayView";

// OBS 브라우저 소스 전용 — 로그인 세션 없이 URL의 토큰만으로 guild를 식별하는 공개 페이지.
export default function MissionsOverlayPage() {
  const { token } = useParams<{ token: string }>();
  const { data: status, error: connError } = useOverlayPoll<MissionsOverlayStatus>(
    token ? `/api/chzzk/overlay/${token}/missions-status` : null,
    5000
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
        <MissionsOverlayView status={status} connError={connError} />
      </div>
    </>
  );
}
