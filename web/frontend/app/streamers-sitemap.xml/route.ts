import { BASE } from "@/lib/api";

// 스트리머 페이지 사이트맵(동적).
// metadata 규약(app/sitemap.ts)을 async로 만들면 런타임에 404가 나서, 일반 Route Handler로
// 직접 XML을 만든다. robots.txt가 이 경로를 사이트맵으로 가리킨다.
//
// 데이터가 없는 채널은 streamer layout이 robots noindex를 내보내므로, 백엔드가
// '롤업에 방송 이력이 쌓인 채널'만 골라 준 목록만 넣는다.
const SITE = "https://nexbot.shop";
const MAX_STREAMERS = 2000;

export const revalidate = 3600;   // 수집 주기가 10분이라 1시간 캐시로 충분하다

const esc = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export async function GET() {
  let items: { id: string; last_at: number }[] = [];
  try {
    const res = await fetch(`${BASE}/api/rising/sitemap-channels?limit=${MAX_STREAMERS}`, {
      next: { revalidate },
    });
    if (res.ok) items = ((await res.json())?.channels ?? []) as typeof items;
  } catch {
    // 백엔드가 죽어도 빈 사이트맵을 정상 XML로 응답한다(크롤러에 에러를 주지 않는다)
  }

  const urls = items.map((c) => {
    const mod = c.last_at ? new Date(c.last_at * 1000).toISOString() : new Date().toISOString();
    return `<url><loc>${esc(`${SITE}/stats/streamer/${c.id}`)}</loc>` +
           `<lastmod>${mod}</lastmod><changefreq>daily</changefreq><priority>0.5</priority></url>`;
  }).join("");

  return new Response(
    `<?xml version="1.0" encoding="UTF-8"?>` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls}</urlset>`,
    { headers: { "Content-Type": "application/xml" } },
  );
}
