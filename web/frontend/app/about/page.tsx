import type { Metadata } from "next";
import Link from "next/link";
import { Bot, ChevronLeft } from "lucide-react";
import Footer from "@/components/Footer";

// 서비스 소개 — 운영 주체·데이터 출처·비공식성을 한곳에서 밝힌다.
// 전부 정적 서버 컴포넌트라 초기 HTML에 그대로 담긴다(클라이언트 호출 없음).
export const metadata: Metadata = {
  title: "서비스 소개 — NexBot 치지직 통계",
  description:
    "NexBot이 제공하는 치지직 방송 통계 서비스의 목적, 데이터 출처, 비공식 서비스로서의 한계와 문의 경로를 안내합니다.",
  alternates: { canonical: "https://nexbot.shop/about" },
};

const SERVICES: { title: string; body: string }[] = [
  {
    title: "라이브 방송 통계",
    body: "치지직이 공개하는 라이브 방송 목록을 주기적으로 수집해, 플랫폼 전체의 동시 시청자 수와 동시 방송 수가 시간에 따라 어떻게 변하는지 기록합니다. 한 시점의 숫자가 아니라 추이를 남기는 것이 목적입니다.",
  },
  {
    title: "카테고리 분석",
    body: "어떤 카테고리에 시청자가 모여 있는지, 방송 수 대비 시청자가 많은 카테고리는 어디인지 계산합니다. 방송 주제를 정할 때 '시청자가 많은 곳'과 '경쟁이 적은 곳'을 나눠 보기 위한 것입니다.",
  },
  {
    title: "스트리머 랭킹",
    body: "동시 시청자, 팔로워, 방송 시간 등 기준을 바꿔 가며 순위를 확인할 수 있습니다. 직전 수집 대비 증감도 함께 보여 주어 지금이 오르는 중인지 내리는 중인지 알 수 있습니다.",
  },
  {
    title: "신규·소형 스트리머 분석",
    body: "첫 방송 후 60일 이내인 채널과, 최근 평균 동시 시청자가 10명 이하인 소형 채널을 따로 모아 봅니다. 절대 시청자 수로는 상위권에 오를 수 없는 채널도 자기 자신의 최근 평균 대비 변화로 비교할 수 있게 하기 위한 것입니다.",
  },
  {
    title: "기간별 분석",
    body: "하루·일주일 같은 구간을 놓고 누적 시청 시간과 평균 시청자를 함께 봅니다. 특정 방송 하나가 아니라 기간 전체의 흐름을 판단할 때 씁니다.",
  },
  {
    title: "싱드컵 이벤트 통계",
    body: "기간 한정 이벤트의 참가 클립과 하트·조회수 변화를 모아 순위와 급상승을 계산합니다. 이벤트 기간에만 운영됩니다.",
  },
];

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      <header className="border-b border-border">
        <div className="max-w-4xl mx-auto px-5 h-14 flex items-center gap-2.5">
          <Link href="/" className="nb-brand-tap flex items-center gap-2 font-bold text-[15px] text-muted hover:text-fg transition-colors">
            <ChevronLeft size={16} /> <Bot size={17} className="text-accent" /> NexBot
          </Link>
        </div>
      </header>

      <main className="flex-1 w-full max-w-4xl mx-auto px-5 py-10 md:py-14">
        <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight">서비스 소개</h1>
        <p className="mt-3 text-sm md:text-base leading-relaxed text-muted">
          NexBot은 치지직(CHZZK)에서 방송하는 스트리머와 시청자가 방송의 흐름을 숫자로
          확인할 수 있게 만든 통계 서비스입니다. 방송을 오래 해도 &ldquo;오늘은 사람이 많았나&rdquo;를
          감으로만 판단하게 되는 경우가 많고, 이제 시작한 채널은 비교할 기준 자체가 없습니다.
          공개된 방송 정보를 시간에 따라 쌓아 두면 그 판단을 수치로 바꿀 수 있다는 것이
          이 서비스를 만든 이유입니다. 통계는{" "}
          <Link href="/stats" className="text-accent hover:underline">치지직 방송 통계</Link>에서
          로그인 없이 볼 수 있습니다.
        </p>

        {/* 아래 네 문단은 운영자가 직접 작성한 내용이다. 맞춤법과 문장 연결만 다듬고
            의미를 바꾸지 않는다. 없는 경험·후기·사례를 지어내지 말 것. */}
        <h2 className="mt-10 text-xl font-extrabold tracking-tight">서비스를 만든 이유</h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted">
          <p>
            스트리머들이 자신의 데이터를 보고 방송 개선과 스케줄 관리 등 방송 성장에 도움을
            받으셨으면 해서 이 서비스를 시작했습니다.
          </p>
          <p>
            치지직은 스트리밍을 중심으로 하는 플랫폼이라 통계를 확인하기가 어렵게 되어 있습니다.
            여러 수치를 한눈에 볼 수 없었고, 특히 신입 스트리머에게는 자신을 보여 줄 기회가
            잘 보이지 않았습니다. 그 부분을 넘어서 보려고 만든 것이 이 통계입니다.
          </p>
        </div>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">이렇게 활용하실 수 있습니다</h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted">
          <p>
            스트리머와 시청자가 통계와 수치 요약을 보고, 스트리머는 지금이 방송하기 좋은
            시간대인지 아닌지를 판단해 스케줄을 관리하실 수 있습니다. 광고 대행사처럼 시청자
            분포를 봐야 하는 경우에도, 현재 치지직에 시청자가 어떻게 분포해 있는지 확인하고
            스트리머에게 몇 시에 광고 방송을 요청할지 정하는 등 여러 방식으로 쓰일 수 있습니다.
          </p>
        </div>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">운영 원칙</h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted">
          <p>
            더 나은 방향으로 데이터를 제공하기 위해 계속 개선하고 있습니다. 현재는 취미로
            운영하고 있어 어려움이 있지만, 더 나은 방송 생태계를 위해 앞으로도 개선해 나갈
            예정입니다.
          </p>
        </div>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">제공하는 기능</h2>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {SERVICES.map(({ title, body }) => (
            <section key={title} className="rounded-xl border border-border bg-bg-card/40 p-4">
              <h3 className="font-bold text-[15px]">{title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{body}</p>
            </section>
          ))}
        </div>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">데이터 출처와 한계</h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted">
          <p>
            이 서비스의 수치는 치지직이 <b className="text-fg">공개하는 정보</b>를 저희가 자체적으로
            수집·가공한 것입니다. 공식 파트너 API가 아니라 공개된 데이터를 읽어 오는 방식이므로,
            원본이 응답을 지연하거나 값을 수정하거나 일부 항목을 내려 주지 않으면
            <b className="text-fg"> 실제 치지직 화면과 수치가 다를 수 있습니다.</b>
          </p>
          <p>
            과거 이력은 저희가 수집을 시작한 시점 이후 구간만 존재합니다. 그 이전의 방송 기록은
            복원할 수 없습니다. 또한 수집은 일정 주기로 이뤄지므로, 주기 사이에 짧게 켜졌다 꺼진
            방송은 기록에 남지 않을 수 있습니다. 기능별 실제 수집 주기는{" "}
            <Link href="/stats" className="text-accent hover:underline">통계 페이지의 서비스 소개</Link>에
            정리해 두었습니다.
          </p>
          <p className="rounded-lg border border-border px-3.5 py-3">
            NexBot은 <b className="text-fg">비공식 서비스</b>이며 NAVER 및 치지직과
            <b className="text-fg"> 제휴·후원·위탁 관계가 없습니다.</b> 치지직의 상표와 콘텐츠에 대한
            권리는 각 권리자에게 있습니다.
          </p>
        </div>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">운영 주체와 문의</h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted">
          <p>
            이 서비스는 <b className="text-fg">NexBot 운영자</b>가 개인적으로 개발·운영합니다.
            서비스 코드 일부는{" "}
            <a href="https://github.com/DaeDosi/discord-bot" target="_blank" rel="noreferrer"
               className="text-accent hover:underline">GitHub 저장소</a>에 공개돼 있습니다.
          </p>
          <p>
            통계 수치가 실제와 다르다고 판단되는 경우, 클립·이미지·저작권과 관련해 삭제나 정정이
            필요한 경우, 그 밖의 오류·광고·운영 문의는{" "}
            <Link href="/contact" className="text-accent hover:underline">문의 페이지</Link>에서
            접수합니다. 서비스 이용과 데이터 처리 기준은{" "}
            <Link href="/terms" className="text-accent hover:underline">이용약관</Link>과{" "}
            <Link href="/privacy" className="text-accent hover:underline">개인정보처리방침</Link>에
            정리돼 있습니다.
          </p>
        </div>
      </main>

      <Footer />
    </div>
  );
}
