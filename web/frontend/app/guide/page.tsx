import type { Metadata } from "next";
import Link from "next/link";
import {
  Bot, LogIn, LayoutGrid, Radio, Settings2,
  ExternalLink, ChevronRight,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import Footer from "@/components/Footer";

export const metadata: Metadata = {
  title: "사용 방법 — NexBot",
  description: "NexBot을 Discord 서버에 설치하고 설정하는 방법을 안내합니다.",
};

const BOT_INVITE_URL = process.env.NEXT_PUBLIC_BOT_INVITE_URL ?? null;

const steps: {
  icon: LucideIcon;
  title: string;
  description: string;
  substeps?: string[];
  cta?: "invite";
}[] = [
  {
    icon: Bot,
    title: "봇을 서버에 초대하세요",
    description:
      "아래 버튼을 클릭해 NexBot을 Discord 서버에 추가하세요. 봇 초대에는 서버 관리자(Administrator) 권한이 필요합니다.",
    cta: "invite",
  },
  {
    icon: LogIn,
    title: "Discord 계정으로 로그인",
    description:
      "대시보드 우측 상단의 '로그인' 버튼을 클릭해 Discord 계정으로 인증합니다. 서버 관리(MANAGE_SERVER) 이상의 권한이 있는 계정으로 로그인해야 대시보드에서 서버를 관리할 수 있습니다.",
  },
  {
    icon: LayoutGrid,
    title: "관리할 서버를 선택하세요",
    description:
      "로그인 후 봇이 추가된 서버 목록이 표시됩니다. 관리할 서버를 클릭하면 해당 서버의 설정 패널로 이동합니다.",
  },
  {
    icon: Radio,
    title: "치지직 방송 알림 설정",
    description:
      "구독한 스트리머가 방송을 시작하거나 종료하면 지정한 Discord 채널에 자동으로 알림이 전송됩니다.",
    substeps: [
      "대시보드 → 서버 선택 → 치지직 탭으로 이동",
      "스트리머 이름을 검색해 원하는 채널을 선택하세요",
      "알림을 받을 Discord 채널을 지정합니다",
      "방송 시작 시 멘션할 역할을 설정하세요 (선택 사항)",
    ],
  },
  {
    icon: Settings2,
    title: "레벨링 & 서버 관리 설정",
    description:
      "채팅 활동에 따른 레벨링, 경고·뮤트 등의 자동 관리, 입장/퇴장 메시지를 원하는 대로 설정하세요.",
    substeps: [
      "레벨링 탭: 레벨업 알림 채널 및 레벨별 역할 보상 설정",
      "관리 탭: 중재자 역할 지정 (/warn, /kick, /ban 사용 권한)",
      "환영 탭: 서버 입장·퇴장 메시지 채널 설정",
      "금지어 탭: 자동 삭제할 단어 목록 설정",
    ],
  },
];

export default function GuidePage() {
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

      <main className="max-w-2xl mx-auto px-5 py-12 pb-20">
        {/* Breadcrumb */}
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full
                        border border-border bg-bg-card text-muted text-sm mb-6">
          <Link href="/" className="hover:text-fg transition-colors">홈</Link>
          <ChevronRight size={13} />
          <span className="text-fg">사용 방법</span>
        </div>

        {/* Hero */}
        <h1 className="text-3xl sm:text-4xl font-bold text-fg mb-3">시작 가이드</h1>
        <p className="text-muted text-base sm:text-lg leading-relaxed mb-12">
          몇 가지 간단한 단계로 NexBot을 설정하세요.
        </p>

        {/* Steps */}
        <div>
          {steps.map((step, i) => {
            const Icon = step.icon;
            const isLast = i === steps.length - 1;
            return (
              <div key={i} className="flex gap-5 sm:gap-6">
                {/* Step indicator */}
                <div className="flex flex-col items-center pt-1">
                  <div
                    className="w-9 h-9 rounded-full border-2 border-accent/30 bg-accent/10
                                flex items-center justify-center text-accent font-bold text-sm flex-shrink-0"
                  >
                    {i + 1}
                  </div>
                  {!isLast && (
                    <div className="w-px flex-1 bg-gradient-to-b from-border to-transparent mt-2 min-h-[1.5rem]" />
                  )}
                </div>

                {/* Card */}
                <div className="bg-bg-card border border-border rounded-2xl p-5 sm:p-6 flex-1 mb-5">
                  <div className="flex items-center gap-3 mb-3">
                    <div className="w-8 h-8 rounded-lg bg-accent/10 text-accent
                                    flex items-center justify-center flex-shrink-0">
                      <Icon size={16} />
                    </div>
                    <h3 className="font-bold text-fg text-[16px]">{step.title}</h3>
                  </div>

                  <p className="text-sm text-muted leading-relaxed">{step.description}</p>

                  {step.substeps && (
                    <ul className="mt-4 space-y-2.5">
                      {step.substeps.map((s, j) => (
                        <li key={j} className="flex items-start gap-2.5 text-sm text-muted">
                          <span
                            className="mt-0.5 w-5 h-5 rounded-md bg-bg border border-border
                                       text-[11px] font-bold text-fg/60 flex items-center justify-center flex-shrink-0"
                          >
                            {j + 1}
                          </span>
                          <span>{s}</span>
                        </li>
                      ))}
                    </ul>
                  )}

                  {step.cta === "invite" && (
                    <div className="mt-5">
                      {BOT_INVITE_URL ? (
                        <a
                          href={BOT_INVITE_URL}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-2 px-4 py-2.5 bg-accent
                                     hover:bg-accent-hover text-white text-sm font-medium
                                     rounded-lg transition-colors"
                        >
                          봇 초대하기
                          <ExternalLink size={14} />
                        </a>
                      ) : (
                        <Link
                          href="/"
                          className="inline-flex items-center gap-2 px-4 py-2.5 bg-accent
                                     hover:bg-accent-hover text-white text-sm font-medium
                                     rounded-lg transition-colors"
                        >
                          대시보드로 이동
                          <ChevronRight size={14} />
                        </Link>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* Back link */}
        <div className="mt-4 pt-8 border-t border-border">
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
