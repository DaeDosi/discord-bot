import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NexBot Dashboard",
  description: "올인원 디스코드 봇 관리 대시보드",
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
      </head>
      <body>{children}</body>
    </html>
  );
}
