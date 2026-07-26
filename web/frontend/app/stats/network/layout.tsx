import type { Metadata } from "next";

// 상위 /stats/layout.tsx의 canonical(https://nexbot.shop/stats)을 그대로 상속하면
// 이 페이지가 /stats의 중복으로 선언돼 색인에서 제외된다 — 자체 canonical로 덮는다.
export const metadata: Metadata = {
  title: "치지직 스트리머 연관 관계망 - 카테고리별 방송 네트워크 | NEXBOT",
  description:
    "치지직 상위 카테고리와 해당 카테고리를 방송 중인 스트리머의 연결 관계를 네트워크 그래프로 시각화합니다. 같은 카테고리에서 경쟁하는 스트리머를 한눈에 확인하세요.",
  alternates: { canonical: "https://nexbot.shop/stats/network" },
  openGraph: {
    type: "website",
    url: "https://nexbot.shop/stats/network",
    siteName: "NexBot",
    title: "치지직 스트리머 연관 관계망 - 카테고리별 방송 네트워크 | NEXBOT",
    description:
      "치지직 상위 카테고리와 해당 카테고리를 방송 중인 스트리머의 연결 관계를 네트워크 그래프로 시각화합니다.",
  },
};

export default function NetworkLayout({ children }: { children: React.ReactNode }) {
  return children;
}
