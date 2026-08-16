import type { MetadataRoute } from "next";

// 정적 공개 페이지 사이트맵.
// robots.ts에서 차단한 경로(대시보드·로그인·오버레이 등)는 여기에도 넣지 않는다.
//
// 스트리머 페이지는 여기 넣지 않는다: 이 함수를 async로 만들어 백엔드에서 채널 목록을
// 받아오게 했더니 next start에서 /sitemap.xml이 404가 됐다(빌드 산출물은 정상이었지만
// 런타임에 서빙되지 않았다). 동적 목록은 별도 Route Handler(/streamers-sitemap.xml)로
// 분리하고, robots.txt에 두 사이트맵을 모두 등록한다.
const SITE = "https://nexbot.shop";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    { url: `${SITE}/`,        lastModified: now, changeFrequency: "weekly",  priority: 1.0 },
    { url: `${SITE}/stats`,   lastModified: now, changeFrequency: "hourly",  priority: 0.9 },
    // 통계 지표 안내. 전역 `/guide`(디스코드 봇 사용법)와 다른 문서다.
    { url: `${SITE}/stats/guide`, lastModified: now, changeFrequency: "monthly", priority: 0.7 },
    { url: `${SITE}/guide`,   lastModified: now, changeFrequency: "monthly", priority: 0.7 },
    { url: `${SITE}/faq`,     lastModified: now, changeFrequency: "monthly", priority: 0.6 },
    // 게시자 신원과 문의 경로를 알리는 공개 페이지. 내용이 자주 바뀌지 않아
    // 안내 문서(guide/faq)보다는 낮고 약관류보다는 높게 둔다.
    { url: `${SITE}/about`,   lastModified: now, changeFrequency: "monthly", priority: 0.5 },
    { url: `${SITE}/contact`, lastModified: now, changeFrequency: "yearly",  priority: 0.4 },
    { url: `${SITE}/terms`,   lastModified: now, changeFrequency: "yearly",  priority: 0.3 },
    { url: `${SITE}/privacy`, lastModified: now, changeFrequency: "yearly",  priority: 0.3 },
  ];
}
