import { ImageResponse } from "next/og";

// /stats 전용 미리보기 이미지. 이 경로가 실제로 공유되는 곳이라 루트 기본 이미지 대신
// 통계 화면임을 알 수 있게 따로 둔다(?tab=... 쿼리는 미리보기에 영향을 주지 않는다 —
// 크롤러는 경로 기준으로 메타태그를 읽는다).
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "NEXBOT — 치지직 실시간 방송 통계 & 스트리머 분석";

const Stat = ({ label, value }: { label: string; value: string }) => (
  <div style={{
    display: "flex", flexDirection: "column", gap: 6, padding: "18px 26px",
    border: "1px solid #1F2937", borderRadius: 16, background: "#11141B",
  }}>
    <div style={{ fontSize: 20, color: "#9AA4B2" }}>{label}</div>
    <div style={{ fontSize: 34, fontWeight: 900, color: "#E8ECF3" }}>{value}</div>
  </div>
);

export default function Image() {
  return new ImageResponse(
    (
      <div style={{
        width: "100%", height: "100%", display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center", gap: 30,
        background: "#0B0D12",
        backgroundImage:
          "radial-gradient(900px 420px at 50% 30%, rgba(0,255,163,0.16), transparent 70%)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{
            width: 46, height: 46, borderRadius: 13, display: "flex",
            alignItems: "center", justifyContent: "center",
            background: "linear-gradient(135deg, #00FFA3, #06B6D4)",
            color: "#04140D", fontSize: 28, fontWeight: 900,
          }}>N</div>
          <div style={{ fontSize: 34, fontWeight: 900, color: "#9AA4B2",
                        letterSpacing: -0.5 }}>NEXBOT</div>
        </div>

        <div style={{ fontSize: 58, fontWeight: 900, color: "#E8ECF3",
                      letterSpacing: -1.5, display: "flex", gap: 16 }}>
          <span>치지직</span>
          <span style={{ color: "#00FFA3" }}>방송 통계</span>
        </div>

        {/* 무엇을 보여주는 페이지인지 한 줄로 */}
        <div style={{ display: "flex", gap: 16 }}>
          <Stat label="실시간" value="동시 시청자 추이" />
          <Stat label="카테고리" value="점유율 분석" />
          <Stat label="신규" value="스트리머 인사이트" />
        </div>

        <div style={{ fontSize: 22, color: "#6B7280" }}>nexbot.shop/stats</div>
      </div>
    ),
    size,
  );
}
