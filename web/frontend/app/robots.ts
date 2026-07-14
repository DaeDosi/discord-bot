import type { MetadataRoute } from "next";

// nexadmin/dashboard/login 등은 로그인 없이는 빈 화면/로딩만 뜨는 앱 화면이고,
// overlay는 사람이 아니라 OBS가 불러가는 위젯, /status는 정책이 명시한 "알림·이동
// 목적으로 사용되는 화면"에 해당한다. 사이트 전역(app/layout.tsx)에 AdSense 스크립트가
// 로드되는데 이 페이지들엔 게시자 콘텐츠가 없어 "Google 게재 광고 / 게시자 콘텐츠 없음"
// 정책 경고의 원인이 됐다 — 크롤링 대상에서 제외한다.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: [
        "/nexadmin",
        "/dashboard",
        "/dashboard/*",
        "/login",
        "/callback",
        "/verify",
        "/overlay/*",
        "/status",
      ],
    },
  };
}
