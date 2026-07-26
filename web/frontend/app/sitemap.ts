import type { MetadataRoute } from "next";

// 애드센스/검색 크롤러가 공개 페이지를 빠짐없이 찾도록 사이트맵을 제공한다.
// robots.ts에서 차단한 경로(대시보드·로그인·오버레이 등)는 여기에도 넣지 않는다.
const SITE = "https://nexbot.shop";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    { url: `${SITE}/`,        lastModified: now, changeFrequency: "weekly",  priority: 1.0 },
    { url: `${SITE}/stats`,   lastModified: now, changeFrequency: "hourly",  priority: 0.9 },
    { url: `${SITE}/guide`,   lastModified: now, changeFrequency: "monthly", priority: 0.7 },
    { url: `${SITE}/faq`,     lastModified: now, changeFrequency: "monthly", priority: 0.6 },
    { url: `${SITE}/terms`,   lastModified: now, changeFrequency: "yearly",  priority: 0.3 },
    { url: `${SITE}/privacy`, lastModified: now, changeFrequency: "yearly",  priority: 0.3 },
  ];
}
