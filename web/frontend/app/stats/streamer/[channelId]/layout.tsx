import type { Metadata } from "next";
import Link from "next/link";
import { BASE } from "@/lib/api";
import type { StreamerDashboard } from "@/lib/types";
import Footer from "@/components/Footer";

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
// revalidate 600s: 수집 주기가 약 10분이라 그보다 자주 재검증할 이유가 없고,
// generateMetadata와 레이아웃이 같은 URL을 부르므로 백엔드 호출은 1회로 합쳐진다.
async function getStreamer(channelId: string): Promise<StreamerDashboard | null> {
  try {
    const res = await fetch(
      `${BASE}/api/rising/streamer/${encodeURIComponent(channelId)}?days=30`,
      { next: { revalidate: 600 } },
    );
    if (!res.ok) return null;
    return (await res.json()) as StreamerDashboard;
  } catch {
    // 백엔드가 죽어도 페이지 자체는 떠야 한다 — 메타데이터만 폴백으로 넘어간다.
    return null;
  }
}

const SITE = "https://nexbot.shop";

export async function generateMetadata(
  { params }: { params: Promise<{ channelId: string }> },
): Promise<Metadata> {
  const { channelId } = await params;
  const d = await getStreamer(channelId);
  const url = `${SITE}/stats/streamer/${channelId}`;
  const name = d?.found && d.channel_name ? d.channel_name : null;

  if (!name) {
    return {
      title: "치지직 스트리머 분석 - NEXBOT 방송 통계",
      description:
        "치지직 스트리머의 동시 시청자, 방송 시간, 시청 시간, 카테고리 비중과 방송 기록을 분석합니다.",
      alternates: { canonical: url },
      robots: { index: false, follow: true }, // 데이터 없는 채널은 색인 대상에서 제외
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
  const d = await getStreamer(channelId);
  const name = d?.found && d.channel_name ? d.channel_name : null;
  const sm = d?.summary;

  // 셸(min-h-screen + Footer)을 레이아웃이 소유한다. page.tsx 안에 Footer가 있으면
  // 이 소개 섹션이 푸터 '아래'로 밀려나므로, 페이지에서 Footer를 걷어내고 여기서
  // children → 소개 → Footer 순서로 배치한다.
  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      {children}

      {/* 크롤러용 서버 렌더링 본문. page.tsx의 loading 분기 밖(형제)이라 데이터 로딩
          상태와 무관하게 항상 초기 HTML에 포함된다. */}
      <section className="w-full max-w-[1600px] mx-auto px-4 md:px-6 pb-10">
        <div className="border-t border-border pt-8">
          <h2 className="text-lg md:text-xl font-extrabold tracking-tight mb-4">
            {name ? `${name} 치지직 방송 통계 분석` : "치지직 스트리머 방송 통계 분석"}
          </h2>

          <div className="space-y-4 text-sm leading-relaxed text-muted max-w-4xl">
            {name ? (
              <p>
                이 페이지는 치지직 스트리머 <strong className="text-fg">{name}</strong> 채널의 방송 지표를
                NEXBOT이 자체 수집한 데이터로 정리한 분석 리포트입니다.
                {sm?.avg_viewers != null && <> 최근 30일 기준 평균 동시 시청자는 약 {sm.avg_viewers.toLocaleString("ko-KR")}명이며,</>}
                {sm?.peak_viewers != null && <> 같은 기간 최고 동시 시청자는 {sm.peak_viewers.toLocaleString("ko-KR")}명을 기록했습니다.</>}
                {sm?.broadcast_hours != null && <> 집계된 방송 시간은 약 {sm.broadcast_hours.toLocaleString("ko-KR")}시간입니다.</>}
                {" "}아래 대시보드에서는 이 수치들이 어떻게 변해 왔는지 시간 순서대로 확인할 수 있습니다.
              </p>
            ) : (
              <p>
                이 페이지는 치지직 스트리머 개별 채널의 방송 지표를 NEXBOT이 자체 수집한 데이터로 정리한
                분석 리포트입니다. 해당 채널이 라이브를 시작하면 약 10분 주기로 수집이 이루어지고, 이후
                아래 항목들이 순차적으로 채워집니다.
              </p>
            )}

            <div>
              <h3 className="text-fg font-bold mb-2">이 페이지에서 확인할 수 있는 지표</h3>
              <p>
                평균 동시 시청자와 최고 동시 시청자는 방송의 현재 체급을 보여 주는 기본 지표입니다.
                방송 시간은 수집된 스냅샷을 기준으로 추정한 실제 송출 시간이며, 시청 시간(누적 시청 시간)은
                동시 시청자 수와 방송 시간을 곱해 계산합니다. 같은 평균 시청자라도 방송을 길게 유지하면 시청
                시간이 늘어나기 때문에, 두 값을 함께 보면 시청자 수와 방송 길이 중 무엇이 성과를 만들었는지
                구분할 수 있습니다. 카테고리 비중은 어떤 게임이나 콘텐츠에 방송 시간을 얼마나 배분했는지
                보여 주며, 특정 카테고리에 편중되어 있는지 여러 콘텐츠를 병행하는지 판단하는 근거가 됩니다.
                방송 기록과 잔디 히트맵은 최근 방송 빈도와 공백 구간을 한눈에 보여 주므로, 방송 주기가
                일정한지 점검할 때 유용합니다.
              </p>
            </div>

            <div>
              <h3 className="text-fg font-bold mb-2">데이터를 읽을 때 주의할 점</h3>
              <p>
                모든 수치는 치지직이 공개하는 라이브 목록을 약 10분 간격으로 수집해 추정한 값입니다. 따라서
                방송 시간과 시청 시간은 10분 단위로 근사되며, 수집 공백이 있었다면 실제보다 낮게 집계될 수
                있습니다. 또한 과거 이력은 NEXBOT이 수집을 시작한 시점 이후 구간만 존재하므로, 그보다 오래된
                방송은 반영되지 않습니다. 절대 수치를 다른 채널과 직접 비교하기보다는, 같은 채널의 시간에 따른
                변화 추세를 보는 용도로 활용하시는 것을 권합니다. 다른 채널과의 상대적인 위치가 궁금하다면{" "}
                <Link href="/stats" className="text-accent hover:underline">치지직 전체 방송 통계</Link> 페이지의
                랭킹과 카테고리 점유율을 함께 참고하시기 바랍니다.
              </p>
            </div>

            <p className="text-xs text-muted/70">
              NEXBOT은 치지직 공개 정보를 자체 수집·가공한 비공식 서비스로, 네이버 및 치지직과 제휴 관계가
              없습니다. 자세한 내용은{" "}
              <Link href="/terms" className="text-accent hover:underline">이용약관</Link>과{" "}
              <Link href="/privacy" className="text-accent hover:underline">개인정보처리방침</Link>을 확인해
              주시기 바랍니다.
            </p>
          </div>
        </div>
      </section>

      <Footer />
    </div>
  );
}
