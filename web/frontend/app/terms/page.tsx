import type { Metadata } from "next";
import Link from "next/link";
import { Bot, ChevronRight } from "lucide-react";
import Footer from "@/components/Footer";

export const metadata: Metadata = {
  title: "이용약관 — NexBot",
  description: "NexBot 서비스 이용에 관한 약관입니다.",
};

const EFFECTIVE_DATE = "2026년 7월 14일";

const sections = [
  {
    id: "1",
    title: "목적",
    content: (
      <p className="text-muted leading-relaxed">
        본 약관은 NexBot(이하 &quot;서비스&quot;)이 제공하는 Discord 봇 및 웹 대시보드 서비스의
        이용 조건 및 절차, 이용자와 운영자의 권리·의무 및 책임사항을 규정함을 목적으로 합니다.
      </p>
    ),
  },
  {
    id: "2",
    title: "서비스 내용",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>서비스는 다음과 같은 기능을 제공합니다.</p>
        <ul className="space-y-2">
          {[
            "치지직(Chzzk) 방송 알림 서비스 — 구독한 스트리머의 방송 시작 시 Discord 채널에 자동 알림",
            "치지직 팔로우 인증 시스템 — Chzzk OAuth 인증을 통해 팔로우 기간을 확인하고, 설정된 기간 조건에 따라 Discord 역할을 자동 부여 (최대 5개 티어 설정 가능)",
            "치지직 실시간 채팅 명령어 — 방송 채팅창에서 포인트 조회, 채팅 기반 도박·투표, 관리자가 등록한 커스텀 출석체크/응답 명령어 등을 처리",
            "포인트 & 상점 시스템 — 채팅·애정도 활동, 미션 완료로 포인트를 적립하고 관리자가 등록한 상점 아이템과 교환",
            "Discord 서버 레벨링 시스템 — 채팅 활동에 따른 XP 적립, 레벨업 알림, 역할 보상",
            "서버 관리 기능 — 경고(/warn), 뮤트(/mute), 차단(/ban) 등 중재 도구, 환영/퇴장 메시지 설정",
            "커뮤니티 홍보 페이지 — 관리자가 공개로 설정한 서버 정보(서버명·아이콘·소개 문구)를 로그인 없이 누구나 볼 수 있는 공개 페이지에 노출",
            "웹 대시보드를 통한 설정 관리 — 서버별 봇 설정을 브라우저에서 직관적으로 관리",
          ].map((item) => (
            <li key={item} className="flex items-start gap-2.5">
              <span className="mt-1 w-1.5 h-1.5 rounded-full bg-accent/60 flex-shrink-0" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    ),
  },
  {
    id: "3",
    title: "이용 조건",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>서비스를 이용하려면 다음 조건을 충족해야 합니다.</p>
        <ul className="space-y-2">
          {[
            "유효한 Discord 계정을 보유하고 있어야 합니다.",
            "웹 대시보드 설정은 해당 Discord 서버에 봇을 초대한 서버 관리자(MANAGE_SERVER 이상 권한 보유자)만 이용할 수 있습니다.",
            "Discord 이용약관(Terms of Service) 및 커뮤니티 가이드라인을 준수해야 합니다.",
            "만 14세 미만 이용자는 법정대리인의 동의를 받아야 합니다.",
          ].map((item) => (
            <li key={item} className="flex items-start gap-2.5">
              <span className="mt-1 w-1.5 h-1.5 rounded-full bg-accent/60 flex-shrink-0" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    ),
  },
  {
    id: "4",
    title: "금지 행위",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>이용자는 다음 행위를 해서는 안 됩니다.</p>
        <ul className="space-y-2">
          {[
            "서비스를 스팸, 어뷰징, 사기 등 악의적 목적으로 사용하는 행위",
            "타인의 Discord 서버에 무단 접근을 시도하거나 권한을 우회하는 행위",
            "서비스를 대상으로 크롤링, 스크래핑, 자동화 도구(봇)를 사용하는 행위",
            "서비스의 정상적인 운영을 방해하거나 서버에 과도한 부하를 주는 행위",
            "운영자의 지식재산권을 침해하거나 서비스를 무단으로 복제·배포하는 행위",
          ].map((item) => (
            <li key={item} className="flex items-start gap-2.5">
              <span className="mt-1 w-1.5 h-1.5 rounded-full bg-danger/60 flex-shrink-0" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
        <p className="text-sm">
          위 금지 행위가 확인되면 사전 통보 없이 서비스 이용이 제한될 수 있습니다.
        </p>
      </div>
    ),
  },
  {
    id: "5",
    title: "서비스 중단 및 변경",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <div className="space-y-2">
          {[
            {
              title: "서비스 중단",
              desc: "운영자는 서비스 점검, 운영상의 사정, 또는 불가피한 사유로 인해 사전 고지 없이 서비스를 일시 중단하거나 영구 종료할 수 있습니다.",
            },
            {
              title: "무료 서비스",
              desc: "NexBot은 현재 무료로 제공되는 서비스입니다. 서비스 중단·변경·오류로 인해 발생한 손해에 대해 운영자는 별도의 손해배상 책임을 지지 않습니다.",
            },
            {
              title: "약관 변경",
              desc: "운영자는 본 약관을 변경할 수 있으며, 변경 시 GitHub 저장소 또는 서비스 공지를 통해 안내합니다. 변경 후 계속 이용하면 변경된 약관에 동의한 것으로 간주합니다.",
            },
          ].map(({ title, desc }) => (
            <div key={title} className="bg-bg rounded-xl border border-border p-4">
              <p className="text-fg font-medium text-sm mb-1">{title}</p>
              <p className="text-sm">{desc}</p>
            </div>
          ))}
        </div>
      </div>
    ),
  },
  {
    id: "6",
    title: "면책조항",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>운영자는 다음 사유로 발생한 손해에 대해 책임을 지지 않습니다.</p>
        <ul className="space-y-2">
          {[
            "Discord API 또는 치지직(Chzzk) API 장애·변경으로 인한 알림 누락 또는 기능 오작동",
            "Discord 서버 정책 변경으로 인해 봇 기능이 제한되거나 중단되는 경우",
            "이용자 본인의 부주의나 약관 위반으로 인해 발생한 손해",
            "제3자의 해킹, 바이러스, 불법 행위 등 불가항력적인 사유로 발생한 손해",
            "서비스 이용 또는 이용 불가로 인해 발생한 기대 이익 손실",
          ].map((item) => (
            <li key={item} className="flex items-start gap-2.5">
              <span className="mt-1 w-1.5 h-1.5 rounded-full bg-muted/40 flex-shrink-0" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    ),
  },
  {
    id: "7",
    title: "개인정보",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>
          서비스의 개인정보 수집·이용·보호에 관한 사항은{" "}
          <Link
            href="/privacy"
            className="text-accent hover:underline"
          >
            개인정보처리방침
          </Link>
          에서 확인하실 수 있습니다. 본 약관과 개인정보처리방침이 상충하는 경우,
          개인정보 관련 사항에 대해서는 개인정보처리방침이 우선 적용됩니다.
        </p>
      </div>
    ),
  },
  {
    id: "8",
    title: "준거법 및 관할",
    content: (
      <p className="text-muted leading-relaxed">
        본 약관은 대한민국 법률에 따라 해석·적용됩니다. 서비스 이용과 관련하여 분쟁이 발생한
        경우, 운영자와 이용자는 성실히 협의하여 해결하며, 협의가 이루어지지 않을 경우
        관련 법령에 따른 관할 법원에서 해결합니다.
      </p>
    ),
  },
];

export default function TermsPage() {
  return (
    <div className="min-h-screen bg-bg text-fg">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-4xl mx-auto px-5 flex items-center" style={{ height: 60 }}>
          <Link href="/" className="flex items-center gap-2 font-bold text-[17px] text-fg">
            <Bot size={20} className="text-accent" />
            NexBot
          </Link>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-5 py-12 pb-20">
        {/* Breadcrumb */}
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full
                        border border-border bg-bg-card text-muted text-sm mb-5">
          <Link href="/" className="hover:text-fg transition-colors">홈</Link>
          <ChevronRight size={13} />
          <span className="text-fg">이용약관</span>
        </div>

        <h1 className="page-title sm:text-4xl mb-3">이용약관</h1>
        <p className="text-muted">
          시행일: <span className="text-fg">{EFFECTIVE_DATE}</span>
          <span className="mx-3 text-border">|</span>
          서비스명: <span className="text-fg">NexBot</span>
          <span className="mx-3 text-border">|</span>
          운영자: <span className="text-fg">개인 (비공개)</span>
        </p>

        {/* Notice */}
        <div className="bg-accent/8 border border-accent/20 rounded-2xl p-5 mt-8 mb-10">
          <p className="text-sm text-fg leading-relaxed">
            NexBot 서비스를 이용하시기 전에 본 약관을 반드시 읽어주세요.
            서비스를 이용함으로써 본 약관에 동의한 것으로 간주됩니다.
          </p>
        </div>

        {/* TOC */}
        <nav className="bg-bg-card border border-border rounded-2xl p-5 mb-10">
          <p className="text-sm font-semibold text-muted uppercase tracking-wider mb-3">목차</p>
          <ol className="space-y-1.5">
            {sections.map(({ id, title }) => (
              <li key={id}>
                <a
                  href={`#section-${id}`}
                  className="flex items-center gap-2 text-sm text-muted hover:text-fg transition-colors group"
                >
                  <span className="w-5 h-5 rounded-md bg-accent/10 text-accent text-[11px]
                                   font-bold flex items-center justify-center flex-shrink-0
                                   group-hover:bg-accent/20 transition-colors">
                    {id}
                  </span>
                  제{id}조 {title}
                </a>
              </li>
            ))}
          </ol>
        </nav>

        {/* Sections */}
        <div className="space-y-10">
          {sections.map(({ id, title, content }) => (
            <section key={id} id={`section-${id}`} className="scroll-mt-20">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-7 h-7 rounded-lg bg-accent/10 flex items-center justify-center flex-shrink-0">
                  <span className="text-accent text-xs font-bold">{id}</span>
                </div>
                <h2 className="text-lg font-bold text-fg">제{id}조 {title}</h2>
              </div>
              <div className="pl-10">{content}</div>
            </section>
          ))}
        </div>

        {/* Footer note */}
        <div className="mt-16 pt-8 border-t border-border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <p className="text-sm text-muted">
            본 약관은 <span className="text-fg">{EFFECTIVE_DATE}</span>부터 시행됩니다.
            내용 변경 시 GitHub 또는 서비스 공지를 통해 안내합니다.
          </p>
          <Link
            href="/"
            className="text-sm text-accent hover:text-accent-hover transition-colors flex items-center gap-1 flex-shrink-0"
          >
            홈으로 돌아가기 <ChevronRight size={13} />
          </Link>
        </div>
      </main>

      <Footer />
    </div>
  );
}
