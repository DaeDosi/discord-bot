import type { Metadata } from "next";
import "./globals.css";
import SupportMenu from "@/components/SupportMenu";

const SITE = "https://nexbot.shop";
const DESC = "치지직 방송 알림, 레벨링, 서버 관리를 하나의 대시보드로";

export const metadata: Metadata = {
  // 이게 없으면 상대 경로 이미지가 절대 URL로 바뀌지 않아 og:image가 무시된다.
  // 링크 미리보기를 읽는 쪽(디스코드·카카오톡 등)은 절대 URL만 받는다.
  metadataBase: new URL(SITE),
  title: "NexBot - 디스코드 봇 대시보드",
  description: DESC,
  // 루트에 openGraph가 없으면 하위에서 지정하지 않은 페이지는 미리보기가 통째로 비었다.
  openGraph: {
    type: "website",
    url: SITE,
    siteName: "NexBot",
    locale: "ko_KR",
    title: "NexBot - 디스코드 봇 대시보드",
    description: DESC,
  },
  twitter: {
    card: "summary_large_image",
    title: "NexBot - 디스코드 봇 대시보드",
    description: DESC,
  },
};

// 라이트 모드를 제공하지 않는다 — 테마 전환 UI·저장값·초기화 스크립트를 모두 걷어냈다.
// `.light` 클래스를 붙이는 코드가 남아 있지 않으므로 예전 localStorage 값이 있어도
// 다크로 뜬다. 초기화 스크립트가 사라져 테마 flash도 구조적으로 생기지 않는다.
// (부수 효과: `<head>`를 하이드레이션 전에 건드리던 스크립트가 없어졌다.)
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className="dark">
      <head>
        {/* Google AdSense */}
        <script
          async
          src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9117458586315582"
          crossOrigin="anonymous"
        />
      </head>
      <body>
        {children}
        {/* 우측 하단 지원 버튼 — 여기서 **한 번만** 렌더한다.
            제외 경로(인증 왕복·OBS 오버레이) 판단은 컴포넌트가 스스로 한다. */}
        <SupportMenu />
      </body>
    </html>
  );
}
