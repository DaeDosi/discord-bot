import { ImageResponse } from "next/og";

// 링크 미리보기용 기본 이미지(og:image). 파일 이름이 곧 규약이라 이 파일이 있으면
// Next.js가 /opengraph-image 라우트를 만들고 메타태그를 자동으로 붙인다.
//
// 왜 그림 파일이 아니라 코드로 만드나: 디자인 에셋을 따로 관리하지 않아도 되고,
// 문구를 바꿀 때 이미지 편집기를 열 필요가 없다. 빌드 시 한 번 생성된다.
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "NexBot — 치지직 실시간 방송 통계";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%", height: "100%", display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 24,
          background: "#0B0D12",
          // 사이트의 시그니처 색(그린 → 시안)을 옅게 깐다
          backgroundImage:
            "radial-gradient(900px 420px at 50% 38%, rgba(0,255,163,0.16), transparent 70%)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <div style={{
            width: 64, height: 64, borderRadius: 18, display: "flex",
            alignItems: "center", justifyContent: "center",
            background: "linear-gradient(135deg, #00FFA3, #06B6D4)",
            color: "#04140D", fontSize: 38, fontWeight: 900,
          }}>N</div>
          <div style={{ fontSize: 60, fontWeight: 900, color: "#E8ECF3",
                        letterSpacing: -1.5 }}>
            NEXBOT
          </div>
        </div>

        <div style={{ fontSize: 46, fontWeight: 800, color: "#00FFA3",
                      letterSpacing: -1 }}>
          치지직 실시간 방송 통계
        </div>

        <div style={{ fontSize: 26, color: "#9AA4B2", textAlign: "center",
                      maxWidth: 900, lineHeight: 1.45 }}>
          시청자 추이 · 카테고리 점유율 · 신입 스트리머 인사이트
        </div>

        <div style={{ marginTop: 8, fontSize: 22, color: "#6B7280" }}>
          nexbot.shop
        </div>
      </div>
    ),
    size,
  );
}
