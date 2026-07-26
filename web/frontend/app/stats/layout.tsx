import type { Metadata } from "next";

// /stats 전용 메타데이터. page.tsx가 "use client"라서 그쪽에서는 metadata를 export할 수
// 없다(Next.js 제약) — 서버 컴포넌트인 이 레이아웃에서 지정해 루트 레이아웃 값을 덮는다.
export const metadata: Metadata = {
  title: "NEXBOT - 치지직 실시간 방송 통계 & 스트리머 분석 대시보드",
  description:
    "치지직 라이브 방송의 실시간 시청자 추이, 카테고리 점유율, 신입 스트리머 성장 인사이트를 한눈에 분석하고 전략을 세워보세요.",
  keywords: [
    "치지직 통계", "치지직 시청자", "치지직 랭킹", "스트리머 분석",
    "치지직 신입 스트리머", "카테고리 점유율", "동시 시청자", "방송 통계",
  ],
  alternates: { canonical: "https://nexbot.shop/stats" },
  openGraph: {
    type: "website",
    url: "https://nexbot.shop/stats",
    siteName: "NexBot",
    title: "NEXBOT - 치지직 실시간 방송 통계 & 스트리머 분석 대시보드",
    description:
      "치지직 라이브 방송의 실시간 시청자 추이, 카테고리 점유율, 신입 스트리머 성장 인사이트를 한눈에 분석하고 전략을 세워보세요.",
  },
  twitter: {
    card: "summary_large_image",
    title: "NEXBOT - 치지직 실시간 방송 통계 & 스트리머 분석 대시보드",
    description:
      "치지직 라이브 방송의 실시간 시청자 추이, 카테고리 점유율, 신입 스트리머 성장 인사이트를 한눈에 분석하고 전략을 세워보세요.",
  },
};

export default function StatsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
