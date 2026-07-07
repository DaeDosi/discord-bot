import type { CSSProperties } from "react";

// 오버레이가 대기/오류 상태일 때 보여주는 작은 알약(pill) 배지 스타일.
// GamblingOverlayView와 MissionsOverlayView가 공유한다.
export function overlayPillStyle(color: string): CSSProperties {
  return {
    fontFamily: "'Pretendard','Inter',sans-serif",
    fontSize: 12,
    color,
    background: "rgba(15,17,23,0.55)",
    border: "1px solid rgba(255,255,255,0.1)",
    borderRadius: 999,
    padding: "6px 14px",
    display: "inline-block",
  };
}
