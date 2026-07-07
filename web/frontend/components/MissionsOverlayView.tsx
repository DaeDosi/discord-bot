"use client";
import { overlayPillStyle } from "@/components/overlayPillStyle";

export interface OverlayMission {
  id: number;
  title: string;
  description: string;
  points: number;
  completed_count: number;
}
export interface MissionsOverlayStatus {
  missions: OverlayMission[];
}

// 대시보드 "미션 관리"에 등록된 활성 미션을 도네이션 미션 위젯 스타일로 보여준다.
// GamblingOverlayView와 마찬가지로 실제 OBS 오버레이 페이지와 대시보드 미리보기가
// 이 컴포넌트를 그대로 공유한다.
export function MissionsOverlayView({
  status, connError,
}: { status: MissionsOverlayStatus | null; connError?: string | null }) {
  if (connError) {
    return (
      <div style={overlayPillStyle("rgba(237,66,69,0.85)")}>⚠️ {connError}</div>
    );
  }

  if (!status || status.missions.length === 0) {
    return (
      <div style={overlayPillStyle("rgba(255,255,255,0.5)")}>🎯 등록된 미션이 없습니다</div>
    );
  }

  return (
    <div
      style={{
        width: "100%",
        maxWidth: 380,
        display: "flex",
        flexDirection: "column",
        gap: 10,
        fontFamily: "'Pretendard','Inter',sans-serif",
      }}
    >
      {status.missions.map((m) => (
        <div
          key={m.id}
          style={{
            background: "rgba(15, 17, 23, 0.88)",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 14,
            padding: "14px 16px",
            color: "#fff",
            boxShadow: "0 6px 24px rgba(0,0,0,0.35)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 15, fontWeight: 800 }}>🎯 {m.title}</span>
            <span
              style={{
                fontSize: 12, fontWeight: 700, color: "#FEE75C",
                background: "rgba(254,231,92,0.14)", borderRadius: 999,
                padding: "2px 10px", whiteSpace: "nowrap",
              }}
            >
              +{m.points.toLocaleString()}P
            </span>
          </div>
          {m.description && (
            <p style={{ fontSize: 12.5, opacity: 0.75, marginTop: 6, lineHeight: 1.4 }}>{m.description}</p>
          )}
          <div style={{ marginTop: 8, fontSize: 11.5, opacity: 0.65 }}>
            🏆 달성 {m.completed_count.toLocaleString()}명
          </div>
        </div>
      ))}
    </div>
  );
}
