import type { Metadata } from "next";
import "./globals.css";

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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <head>
        {/* 테마 깜빡임(FOUC) 방지 — 하이드레이션 전에 실행 */}
        <script dangerouslySetInnerHTML={{ __html: `
          try {
            var t = localStorage.getItem('theme');
            if (t === 'light') document.documentElement.classList.add('light');
          } catch(e) {}
        ` }} />
        {/* Google AdSense */}
        <script
          async
          src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9117458586315582"
          crossOrigin="anonymous"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
