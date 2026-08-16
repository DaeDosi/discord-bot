import type { Metadata } from "next";
import Link from "next/link";
import { Bot, ChevronLeft, Mail, MessageSquare, Github } from "lucide-react";
import Footer from "@/components/Footer";

// 문의 경로 — 목적별로 권장 채널을 나눠 안내한다.
// 정적 서버 컴포넌트라 초기 HTML에 그대로 담긴다(폼·클라이언트 호출 없음).
export const metadata: Metadata = {
  title: "문의 — NexBot 치지직 통계",
  description:
    "NexBot 서비스 오류 신고, 통계 데이터 정정 요청, 클립·이미지 저작권 관련 요청, 광고 및 일반 문의 경로를 안내합니다.",
  alternates: { canonical: "https://nexbot.shop/contact" },
};

const EMAIL = "dnxodud5542@gmail.com";
const DISCORD = "https://discord.gg/DaZxywE4Ka";
const GITHUB = "https://github.com/DaeDosi/discord-bot";

// 목적별 권장 채널. '어디로 보내야 하는지'를 먼저 정하고 그다음에 주소를 알려 준다 —
// 채널을 나열만 하면 사용자가 매번 고민하게 된다.
const PURPOSES: { title: string; body: string; channel: "email" | "discord" | "github" }[] = [
  {
    title: "서비스 오류 신고",
    body: "화면이 비어 보이거나 숫자가 갱신되지 않는 등 사용 중 문제가 생긴 경우입니다. 가장 빠르게 확인할 수 있는 곳은 Discord 서포트 서버입니다.",
    channel: "discord",
  },
  {
    title: "통계 데이터 정정 요청",
    body: "표시된 시청자·팔로워·순위·하트 수치가 실제와 다르다고 판단되는 경우입니다. 어떤 값이 어떻게 다른지 확인할 수 있도록 이메일로 접수합니다.",
    channel: "email",
  },
  {
    title: "클립·이미지·저작권 관련 삭제 또는 정정 요청",
    body: "썸네일·클립·채널 정보 등 게시된 콘텐츠의 삭제나 정정이 필요한 경우입니다. 권리 관계를 확인해야 하므로 이메일로 접수합니다.",
    channel: "email",
  },
  {
    title: "광고 및 일반 문의",
    body: "서비스 운영, 제휴, 광고와 관련한 문의입니다. 이메일로 보내 주시기 바랍니다.",
    channel: "email",
  },
  {
    title: "기술적 버그 제보",
    body: "재현 절차가 분명하고 공개해도 되는 코드·동작 관련 문제라면 GitHub 저장소의 이슈로 남겨 주시면 기록이 함께 남습니다.",
    channel: "github",
  },
];

const CHANNEL_LABEL = { email: "이메일", discord: "Discord 서포트 서버", github: "GitHub" } as const;

const INCLUDE = [
  "문제가 발생한 페이지 주소(URL)",
  "관련 스트리머 또는 채널 이름",
  "관련 클립 주소(URL)",
  "문제를 확인한 날짜와 시각",
  "화면 캡처",
  "요청하시는 내용",
];

const DO_NOT_SEND = [
  "비밀번호",
  "OAuth 토큰",
  "API 키",
  "Discord 토큰",
  "인증 쿠키",
  "주민등록번호 등 요청 처리에 필요하지 않은 개인정보",
];

export default function ContactPage() {
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
        <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight">문의</h1>
        <p className="mt-3 text-sm md:text-base leading-relaxed text-muted">
          NexBot 서비스에 대한 문의는 아래 경로로 접수합니다. 내용에 따라 확인 방법이 달라
          권장 채널을 나눠 두었습니다. 서비스와 데이터의 성격은{" "}
          <Link href="/about" className="text-accent hover:underline">서비스 소개</Link>에서
          확인하실 수 있습니다.
        </p>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">문의 목적별 안내</h2>
        <div className="mt-4 space-y-3">
          {PURPOSES.map(({ title, body, channel }) => (
            <section key={title} className="rounded-xl border border-border bg-bg-card/40 p-4">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h3 className="font-bold text-[15px]">{title}</h3>
                {/* 색만으로 구분하지 않도록 채널 이름을 글자로 적는다 */}
                <span className="rounded border border-border px-1.5 py-0.5 text-[11px] font-bold text-muted">
                  권장: {CHANNEL_LABEL[channel]}
                </span>
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{body}</p>
            </section>
          ))}
        </div>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">연락처</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <a href={`mailto:${EMAIL}`}
             className="rounded-xl border border-border bg-bg-card/40 p-4 transition-colors hover:border-accent/40">
            <span className="flex items-center gap-2 font-bold text-[15px]">
              <Mail size={16} className="text-accent" /> 이메일
            </span>
            <span className="mt-1.5 block break-all text-sm text-accent">{EMAIL}</span>
            <span className="mt-1 block text-xs text-muted">정정·저작권·광고·일반 문의</span>
          </a>
          <a href={DISCORD} target="_blank" rel="noreferrer"
             className="rounded-xl border border-border bg-bg-card/40 p-4 transition-colors hover:border-accent/40">
            <span className="flex items-center gap-2 font-bold text-[15px]">
              <MessageSquare size={16} className="text-accent" /> Discord 서포트 서버
            </span>
            <span className="mt-1.5 block break-all text-sm text-accent">discord.gg/DaZxywE4Ka</span>
            <span className="mt-1 block text-xs text-muted">빠른 오류 신고·이용 문의</span>
          </a>
          <a href={GITHUB} target="_blank" rel="noreferrer"
             className="rounded-xl border border-border bg-bg-card/40 p-4 transition-colors hover:border-accent/40">
            <span className="flex items-center gap-2 font-bold text-[15px]">
              <Github size={16} className="text-accent" /> GitHub
            </span>
            <span className="mt-1.5 block break-all text-sm text-accent">github.com/DaeDosi/discord-bot</span>
            <span className="mt-1 block text-xs text-muted">공개 가능한 버그 제보</span>
          </a>
        </div>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">문의할 때 함께 알려 주시면 좋은 정보</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          아래 정보가 있으면 같은 화면을 재현해 확인하기가 쉬워집니다. 전부 갖추지 않아도 됩니다.
        </p>
        <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted">
          {INCLUDE.map((x) => <li key={x}>{x}</li>)}
        </ul>

        <h2 className="mt-10 text-xl font-extrabold tracking-tight">보내지 말아 주세요</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          아래 정보는 문의를 처리하는 데 필요하지 않습니다. 전달받지 않으며, 실수로 보내신 경우
          계정 쪽에서 즉시 무효화해 주시기 바랍니다.
        </p>
        <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted">
          {DO_NOT_SEND.map((x) => <li key={x}>{x}</li>)}
        </ul>

        <p className="mt-8 rounded-lg border border-border px-3.5 py-3 text-sm leading-relaxed text-muted">
          문의는 운영자가 확인하는 대로 순차적으로 처리합니다. 개인이 운영하는 서비스라
          <b className="text-fg"> 답변 시점을 약속드리지는 않습니다.</b> 접수된 내용은 문의 처리
          목적으로만 사용하며, 처리 기준은{" "}
          <Link href="/privacy" className="text-accent hover:underline">개인정보처리방침</Link>을
          따릅니다.
        </p>
      </main>

      <Footer />
    </div>
  );
}
