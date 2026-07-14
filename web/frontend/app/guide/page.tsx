import type { Metadata } from "next";
import Link from "next/link";
import {
  Bot, LogIn, LayoutGrid, ExternalLink, ChevronRight,
  Shield, Heart, Gem, Radio, BadgeCheck, Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import Footer from "@/components/Footer";
import {
  TerminalMockup, PointsMockup, ChzzkEmbedMockup, ChzzkFollowVerifyMockup,
  LevelingMockup, CommunityMockup,
} from "@/components/marketing/Mockups";

export const metadata: Metadata = {
  title: "사용 방법 — NexBot",
  description: "NexBot의 기능별 설정 방법과 실제 활용 예시를 미리보기와 함께 안내합니다.",
};

const BOT_INVITE_URL = process.env.NEXT_PUBLIC_BOT_INVITE_URL ?? null;

// ── 빠른 시작 (3단계로 축소 — 상세 활용법은 아래 기능별 섹션에서 다룸) ──────────────
const quickStart: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: Bot,
    title: "봇을 서버에 초대하세요",
    description: "서버 관리자(Administrator) 권한으로 NexBot을 Discord 서버에 추가합니다.",
  },
  {
    icon: LogIn,
    title: "Discord 계정으로 로그인",
    description: "대시보드 우측 상단의 '로그인'을 클릭해 서버 관리(MANAGE_SERVER) 권한이 있는 계정으로 인증합니다.",
  },
  {
    icon: LayoutGrid,
    title: "관리할 서버를 선택하세요",
    description: "로그인 후 봇이 추가된 서버 목록에서 관리할 서버를 클릭하면 설정 패널로 이동합니다.",
  },
];

// ── 기능별 활용법 ──────────────────────────────────────────────────────────────
const features: {
  color: string;
  bg: string;
  icon: React.ReactNode;
  tag: string;
  title: string;
  paragraphs: string[];
  example: string;
  mockup: React.ReactNode;
  flip: boolean;
}[] = [
  {
    color: "#ED4245",
    bg:    "rgba(237,66,69,0.12)",
    icon:  <Shield size={20} style={{ color: "#ED4245" }} />,
    tag:   "서버 관리",
    title: "경고 · 뮤트 · 차단을 슬래시 커맨드로",
    paragraphs: [
      "대시보드 \"관리\" 탭에서 중재자 역할을 지정하면, 그 역할을 가진 멤버가 /warn·/mute·/ban·/clear 같은 슬래시 커맨드를 사용할 수 있습니다.",
      "경고가 누적된 유저는 대시보드에서 이력을 한눈에 조회하고 개별 삭제할 수 있습니다. \"금지어\" 탭에 단어를 등록하면 해당 단어가 포함된 메시지를 자동으로 삭제합니다.",
    ],
    example: "예: /mute @유저 30m 도배   →   30분간 채팅 금지 + 로그 채널에 기록",
    mockup: <TerminalMockup />,
    flip:   false,
  },
  {
    color: "#F87171",
    bg:    "rgba(248,113,113,0.12)",
    icon:  <Heart size={20} style={{ color: "#F87171" }} />,
    tag:   "애정도",
    title: "채팅 활동으로 쌓이는 레벨 시스템",
    paragraphs: [
      "멤버가 채팅을 칠 때마다 애정도 경험치가 쌓이고, 레벨업하면 지정한 채널에 알림이 전송됩니다(DM 전송으로 전환도 가능).",
      "\"애정도\" 탭에서 레벨 구간별로 자동 지급할 Discord 역할을 등록해두면, 레벨업과 동시에 역할이 자동으로 부여됩니다 — 등급별 배지처럼 활용할 수 있습니다.",
    ],
    example: "예: Lv.10 달성 → \"@충성팬\" 역할 자동 지급",
    mockup: <LevelingMockup color="#818CF8" />,
    flip:   true,
  },
  {
    color: "#A855F7",
    bg:    "rgba(168,85,247,0.12)",
    icon:  <Gem size={20} style={{ color: "#A855F7" }} />,
    tag:   "포인트 & 상점",
    title: "포인트를 모아 상점에서 교환",
    paragraphs: [
      "채팅 활동, 애정도 레벨업, 관리자가 등록한 미션 완료 등으로 포인트가 쌓입니다. 미션은 유저가 인증(스크린샷 등)을 제출하면 관리자가 승인하는 방식으로 운영됩니다.",
      "\"포인트\" 탭에서 상점 아이템(이름·이미지·가격·재고)을 등록하면, 유저는 모은 포인트로 교환하고 관리자는 교환 내역을 \"사용 처리\"로 관리할 수 있습니다. 채팅으로 진행하는 도박 미니게임도 같은 포인트를 사용합니다.",
    ],
    example: "예: 미션 \"오늘 방송 클립 공유하기\" 완료 승인 → +300P 지급",
    mockup: <PointsMockup color="#A855F7" />,
    flip:   false,
  },
  {
    color: "#03C75A",
    bg:    "rgba(3,199,90,0.12)",
    icon:  <Radio size={20} style={{ color: "#03C75A" }} />,
    tag:   "치지직 알림",
    title: "방송 시작을 실시간으로 감지해 알림",
    paragraphs: [
      "\"치지직\" 탭에서 스트리머를 검색해 등록하면, 방송을 시작할 때마다 지정한 Discord 채널에 방송 제목·카테고리·썸네일이 담긴 임베드 알림이 자동으로 전송됩니다. 특정 역할을 함께 멘션하도록 설정할 수도 있습니다.",
      "치지직 계정을 OAuth로 연동하면, 방송 채팅창에서 !포인트·!도박 같은 실시간 명령어와 자동 출석체크 기능까지 사용할 수 있습니다.",
    ],
    example: "예: 방송 시작 → \"#알림-채널\"에 임베드 자동 게시 + @구독자 멘션",
    mockup: <ChzzkEmbedMockup />,
    flip:   true,
  },
  {
    color: "#818CF8",
    bg:    "rgba(129,140,248,0.12)",
    icon:  <BadgeCheck size={20} style={{ color: "#818CF8" }} />,
    tag:   "팔로우 인증",
    title: "치지직 팔로우 기간으로 역할 자동 부여",
    paragraphs: [
      "\"입장 인증\" 탭에서 시청자가 자신의 치지직 계정으로 로그인하면, 팔로우 시작일을 기준으로 팔로우 기간이 자동 계산됩니다.",
      "1개월·3개월·6개월처럼 최대 5개의 기간 구간(티어)을 설정해두면, 조건을 만족하는 시청자에게 해당 Discord 역할이 자동으로 부여됩니다 — 오래된 팬을 우대하는 등급 체계를 코드 없이 만들 수 있습니다.",
    ],
    example: "예: 팔로우 4개월째 → \"@서포터\" 역할 자동 부여 (6개월 미달로 \"@충성팬\"은 보류)",
    mockup: <ChzzkFollowVerifyMockup color="#818CF8" />,
    flip:   false,
  },
  {
    color: "#38BDF8",
    bg:    "rgba(56,189,248,0.12)",
    icon:  <Users size={20} style={{ color: "#38BDF8" }} />,
    tag:   "커뮤니티",
    title: "내 서버를 공개 커뮤니티 페이지에 홍보",
    paragraphs: [
      "\"일반 설정\" 탭 하단의 \"커뮤니티 홍보 페이지\" 스위치를 켜고 짧은 소개 문구를 작성하면, 로그인 없이 누구나 볼 수 있는 nexbot.shop/community 페이지에 서버가 노출됩니다.",
      "치지직 계정이 연동된 서버는 방송 중 여부(LIVE 배지)와 함께 표시되고, 카드를 클릭하면 바로 치지직 채널로 이동할 수 있어 신규 시청자 유입에 활용할 수 있습니다.",
    ],
    example: "예: 소개 문구 \"매일 저녁 게임 방송해요!\" 등록 → 커뮤니티 페이지에 카드로 노출",
    mockup: <CommunityMockup color="#38BDF8" />,
    flip:   true,
  },
];

export default function GuidePage() {
  return (
    <div className="min-h-screen bg-bg text-fg">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-6xl mx-auto px-5 flex items-center" style={{ height: 60 }}>
          <Link href="/" className="flex items-center gap-2 font-bold text-[17px] text-fg">
            <Bot size={20} className="text-accent" />
            NexBot
          </Link>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-5 py-12 pb-24">
        {/* Breadcrumb */}
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full
                        border border-border bg-bg-card text-muted text-sm mb-6">
          <Link href="/" className="hover:text-fg transition-colors">홈</Link>
          <ChevronRight size={13} />
          <span className="text-fg">사용 방법</span>
        </div>

        {/* Hero */}
        <h1 className="page-title sm:text-4xl mb-3">사용 가이드</h1>
        <p className="text-muted text-base sm:text-lg leading-relaxed mb-12 max-w-2xl">
          몇 분이면 설정이 끝나는 빠른 시작 안내와, 각 기능을 실제로 어떻게 쓰는지
          미리보기와 함께 소개합니다.
        </p>

        {/* ── 빠른 시작 ── */}
        <section className="mb-20">
          <h2 className="section-title mb-6">빠른 시작</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-5">
            {quickStart.map((step, i) => {
              const Icon = step.icon;
              return (
                <div key={step.title} className="bg-bg-card border border-border rounded-2xl p-5">
                  <div className="flex items-center gap-2.5 mb-3">
                    <div className="w-8 h-8 rounded-full border-2 border-accent/30 bg-accent/10
                                    flex items-center justify-center text-accent font-bold text-sm flex-shrink-0">
                      {i + 1}
                    </div>
                    <Icon size={16} className="text-accent" />
                  </div>
                  <h3 className="font-bold text-fg text-[15px] mb-2">{step.title}</h3>
                  <p className="text-sm text-muted leading-relaxed">{step.description}</p>
                </div>
              );
            })}
          </div>
          {BOT_INVITE_URL && (
            <a
              href={BOT_INVITE_URL}
              target="_blank"
              rel="noreferrer"
              className="mt-6 inline-flex items-center gap-2 px-4 py-2.5 bg-accent
                         hover:bg-accent-hover text-white text-sm font-medium
                         rounded-lg transition-colors"
            >
              봇 초대하기
              <ExternalLink size={14} />
            </a>
          )}
        </section>

        {/* ── 기능별 활용법 ── */}
        <section>
          <h2 className="section-title mb-2">기능별 활용법</h2>
          <p className="text-muted text-[15px] mb-14">
            대시보드의 각 탭에서 무엇을 설정하면 실제로 무슨 일이 일어나는지 예시로 확인하세요.
          </p>

          <div className="space-y-24">
            {features.map(({ color, bg, icon, tag, title, paragraphs, example, mockup, flip }) => (
              <div
                key={title}
                className={`flex flex-col ${flip ? "lg:flex-row-reverse" : "lg:flex-row"} items-center gap-10 lg:gap-16`}
              >
                <div className="flex-1 max-w-lg">
                  <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: bg }}>
                      {icon}
                    </div>
                    <span className="text-xs font-bold uppercase tracking-widest px-3 py-1 rounded-full"
                          style={{ color, background: bg }}>
                      {tag}
                    </span>
                  </div>

                  <h3 className="text-xl sm:text-2xl font-bold text-fg mb-4">{title}</h3>

                  <div className="space-y-3 mb-4">
                    {paragraphs.map((p) => (
                      <p key={p} className="text-muted leading-relaxed text-[15px]">{p}</p>
                    ))}
                  </div>

                  <p className="text-sm font-mono px-3 py-2.5 rounded-lg bg-bg-card border border-border text-fg/80">
                    {example}
                  </p>
                </div>

                <div className="flex-1 flex justify-center lg:justify-end">
                  {mockup}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Back link */}
        <div className="mt-20 pt-8 border-t border-border">
          <Link
            href="/"
            className="text-sm text-accent hover:text-accent-hover transition-colors
                       flex items-center gap-1"
          >
            홈으로 돌아가기
            <ChevronRight size={13} />
          </Link>
        </div>
      </main>

      <Footer />
    </div>
  );
}
