import type { Metadata } from "next";
import Link from "next/link";
import { fetchStreamerMeta, shouldNoIndex } from "@/lib/streamerMeta";

// 스트리머별 분석 페이지의 서버 사이드 레이어.
//
// 이게 필요한 이유:
//  1) page.tsx는 "use client"이고 데이터도 클라이언트에서 받아온다. 그래서 서버 렌더링
//     HTML에 담기는 본문이 227자뿐이었다(실측) — 크롤러에는 사실상 빈 페이지.
//  2) 상위 /stats/layout.tsx의 metadata를 상속받아 canonical이 전부
//     https://nexbot.shop/stats 로 찍혔다. 채널 페이지를 모두 /stats의 중복이라고
//     선언하는 셈이라 색인에서 제외된다. 여기서 채널별 canonical로 덮는다.
//  3) 채널명이 서버 HTML에 없으면 수천 개 페이지 본문이 전부 동일해져 중복 콘텐츠가 된다.
//     제목·설명·본문에 채널명을 넣어 페이지마다 실제로 다른 내용이 되게 한다.
//
// 데이터는 `lib/streamerMeta.ts`가 가져온다(메타 전용 경량 API + 오류 분류).
// 예전에는 여기서 무거운 대시보드 API를 부르고 실패를 전부 null로 뭉갰다 —
// 그래서 일시적인 429가 곧바로 `robots: index=false`가 됐다.
// generateMetadata와 레이아웃이 같은 URL을 부르므로 백엔드 호출은 1회로 합쳐진다.

const SITE = "https://nexbot.shop";

export async function generateMetadata(
  { params }: { params: Promise<{ channelId: string }> },
): Promise<Metadata> {
  const { channelId } = await params;
  const result = await fetchStreamerMeta(channelId);
  const d = result.data;
  const url = `${SITE}/stats/streamer/${channelId}`;
  const name = d?.found && d.channel_name ? d.channel_name : null;

  if (!name) {
    return {
      title: "치지직 스트리머 분석 - NEXBOT 방송 통계",
      description:
        "치지직 스트리머의 동시 시청자, 방송 시간, 시청 시간, 카테고리 비중과 방송 기록을 분석합니다.",
      alternates: { canonical: url },
      // **실제로 없는 채널일 때만** 색인에서 뺀다. 429·5xx·timeout은 잠시 후
      // 정상이 될 수 있는데, 그때 noindex를 달면 크롤링당하는 순간 페이지가 사라진다.
      ...(shouldNoIndex(result) ? { robots: { index: false, follow: true } } : {}),
    };
  }

  const sm = d?.summary;
  const bits: string[] = [];
  if (sm?.avg_viewers != null)     bits.push(`평균 시청자 ${sm.avg_viewers.toLocaleString("ko-KR")}명`);
  if (sm?.peak_viewers != null)    bits.push(`최고 동시 시청자 ${sm.peak_viewers.toLocaleString("ko-KR")}명`);
  if (sm?.broadcast_hours != null) bits.push(`방송 시간 ${sm.broadcast_hours.toLocaleString("ko-KR")}시간`);
  const stat = bits.length ? ` 최근 30일 ${bits.join(", ")}.` : "";

  const title = `${name} 치지직 방송 통계 - 시청자·방송시간 분석 | NEXBOT`;
  const description =
    `치지직 스트리머 ${name}의 방송 통계를 확인하세요.${stat}` +
    " 동시 시청자 추이, 누적 시청 시간, 카테고리 비중, 방송 기록을 한눈에 분석합니다.";

  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: {
      type: "profile", url, siteName: "NexBot", title, description,
      ...(d?.channel_image_url ? { images: [{ url: d.channel_image_url }] } : {}),
    },
    twitter: { card: "summary", title, description },
  };
}

export default async function StreamerLayout(
  { children, params }: { children: React.ReactNode; params: Promise<{ channelId: string }> },
) {
  const { channelId } = await params;
  const d = (await fetchStreamerMeta(channelId)).data;
  const name = d?.found && d.channel_name ? d.channel_name : null;
  const sm = d?.summary;

  // 셸(min-h-screen + Footer)을 레이아웃이 소유한다. page.tsx 안에 Footer가 있으면
  // 이 소개 섹션이 푸터 '아래'로 밀려나므로, 페이지에서 Footer를 걷어내고 여기서
  // children → 소개 → Footer 순서로 배치한다.
  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      {children}

      {/* 통계 읽는 법은 `/stats/guide` 한 곳에 있다. 예전에는 여기에 본문이 통째로
          들어 있었는데, 채널 이름과 그때의 수치가 문장에 섞여 페이지마다 다른 글이
          됐고 통계 메인 하단과도 내용이 겹쳤다. 여기서는 링크만 건다. */}
      <section className="mx-auto w-full max-w-[1600px] px-4 pb-10 md:px-6">
        <div className="rounded-xl border border-border bg-bg-card/40 px-4 py-4">
          <h2 className="text-base font-bold text-fg">이 수치는 어떻게 계산되나</h2>
          <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-muted">
            평균·최고 동시 시청자, 방송 시간, 시청 시간, 카테고리 비중, 활동 잔디가
            각각 무엇을 뜻하는지와 수집 주기로 인한 오차를 안내 페이지에 정리했습니다.
            NexBot은 치지직 공개 정보를 자체 수집·가공한 비공식 서비스로 네이버 및
            치지직과 제휴 관계가 없습니다.
          </p>
          <Link href="/stats/guide"
                className="btn-secondary nb-tap mt-3 inline-flex text-sm">
            통계 안내 보기
          </Link>
        </div>
      </section>
    </div>
  );
}
