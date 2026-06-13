import type { Metadata } from "next";
import Link from "next/link";
import { Bot, ChevronLeft } from "lucide-react";
import Footer from "@/components/Footer";

export const metadata: Metadata = {
  title: "자주 묻는 질문 — NexBot",
  description: "NexBot에 대해 자주 묻는 질문과 답변을 확인하세요.",
};

const FAQS: { q: string; a: string }[] = [
  {
    q: "NexBot은 무료인가요?",
    a: "네, 완전 무료입니다. 별도 결제나 구독 없이 모든 기능을 이용하실 수 있습니다.",
  },
  {
    q: "봇을 서버에 초대하려면 어떻게 하나요?",
    a: "홈페이지 상단의 \"봇 초대하기 🤖\" 버튼을 클릭하면 Discord 초대 페이지로 이동합니다. 서버 관리자 계정으로 서버를 선택하고 권한을 승인하면 됩니다.",
  },
  {
    q: "치지직 알림 설정은 어떻게 하나요?",
    a: "웹 대시보드에 Discord로 로그인한 후, 서버를 선택하고 왼쪽 메뉴에서 \"치지직\" 탭을 클릭하세요. 스트리머를 검색해 알림 채널과 멘션 역할을 지정하면 방송 시작 시 자동으로 알림이 전송됩니다.",
  },
  {
    q: "봇이 오프라인 상태예요.",
    a: "서버 점검 또는 업데이트로 인해 봇이 일시적으로 재시작 중일 수 있습니다. 잠시 후 다시 확인해 주세요. 문제가 지속된다면 봇을 서버에서 퇴장시킨 후 다시 초대해 보세요.",
  },
  {
    q: "내 데이터 삭제를 요청하려면 어떻게 하나요?",
    a: "봇을 서버에서 추방(Kick)하면 해당 서버와 관련된 설정 데이터가 자동으로 삭제됩니다. 개인 데이터 삭제가 필요한 경우 운영자에게 직접 문의해 주세요.",
  },
];

export default function FaqPage() {
  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      {/* Navbar */}
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-3xl mx-auto px-5 flex items-center gap-3" style={{ height: 60 }}>
          <Link href="/" className="flex items-center gap-2 font-bold text-[17px] text-fg">
            <Bot size={20} className="text-accent" />
            NexBot
          </Link>
        </div>
      </header>

      {/* Content */}
      <main className="flex-1 max-w-3xl mx-auto w-full px-5 py-12">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg
                     transition-colors mb-8"
        >
          <ChevronLeft size={15} /> 홈으로
        </Link>

        <h1 className="text-3xl font-bold text-fg mb-2">자주 묻는 질문</h1>
        <p className="text-muted mb-10">NexBot 사용 중 궁금한 점을 확인하세요.</p>

        <div className="space-y-4">
          {FAQS.map(({ q, a }) => (
            <div key={q} className="rounded-2xl border border-border bg-bg-card p-6">
              <p className="text-fg font-semibold mb-2">{q}</p>
              <p className="text-muted text-sm leading-relaxed">{a}</p>
            </div>
          ))}
        </div>
      </main>

      <Footer />
    </div>
  );
}
