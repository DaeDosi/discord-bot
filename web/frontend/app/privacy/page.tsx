import Link from "next/link";
import { Bot, ChevronRight } from "lucide-react";
import Footer from "@/components/Footer";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "개인정보처리방침 — NexBot",
  description: "NexBot 서비스의 개인정보 수집·이용·보호에 관한 방침입니다.",
};

const EFFECTIVE_DATE = "2026년 7월 14일";

const sections = [
  {
    id: "1",
    title: "수집하는 개인정보 항목",
    content: (
      <div className="space-y-3">
        <p className="text-muted leading-relaxed">
          NexBot은 서비스 제공을 위해 아래 최소한의 정보를 수집합니다.
        </p>
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-border">
              <th className="py-2.5 pr-4 text-left text-fg font-semibold w-1/3">항목</th>
              <th className="py-2.5 text-left text-fg font-semibold">설명</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {[
              ["Discord 사용자 ID", "로그인 인증 및 세션 관리"],
              ["Discord 서버(길드) ID", "서버별 설정 저장 및 봇 기능 제공"],
              ["치지직 채널 ID", "방송 알림 구독 및 팔로우 인증 기능 제공"],
              ["Discord 서버 채널·역할 ID", "알림 채널, 역할 부여 기능 설정"],
              ["치지직 팔로우 기간(개월)", "OAuth 인증 시 팔로우 날짜 기준으로 산출하여 역할 부여에 사용, DB에 저장"],
              ["치지직 OAuth 액세스 토큰", "팔로우 날짜 조회 시 일회성 사용, 서버에 영구 저장하지 않음"],
              ["포인트 적립 내역", "채팅·애정도·미션 활동에 따른 서버별 포인트 잔액 저장 및 상점 교환에 사용"],
              ["커뮤니티 홍보 소개 문구", "관리자가 직접 작성하며, 공개 설정 시 로그인 없이 누구나 볼 수 있는 커뮤니티 페이지에 노출"],
            ].map(([item, desc]) => (
              <tr key={item}>
                <td className="py-2.5 pr-4 text-fg">{item}</td>
                <td className="py-2.5 text-muted">{desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-muted text-sm">
          * 이름, 이메일, 전화번호 등 민감한 개인정보는 수집하지 않습니다.
        </p>
      </div>
    ),
  },
  {
    id: "2",
    title: "개인정보 수집 방법",
    content: (
      <ul className="space-y-2 text-muted leading-relaxed list-none">
        {[
          "Discord OAuth2 로그인을 통한 자동 수집",
          "치지직 OAuth2 인증 시 팔로우 날짜 자동 조회 및 저장",
          "봇 슬래시 명령어 및 치지직 채팅 명령어 실행 시 서버·채널·역할 ID, 포인트 잔액 자동 저장",
          "웹 대시보드에서 사용자가 직접 설정·입력 (커뮤니티 홍보 소개 문구 포함)",
        ].map((item) => (
          <li key={item} className="flex items-start gap-2">
            <ChevronRight size={14} className="text-accent mt-0.5 flex-shrink-0" />
            {item}
          </li>
        ))}
      </ul>
    ),
  },
  {
    id: "3",
    title: "개인정보 수집 및 이용 목적",
    content: (
      <ul className="space-y-2 text-muted leading-relaxed list-none">
        {[
          "Discord 봇 서비스 제공 (서버 관리, 레벨링, 환영/퇴장 메시지 등)",
          "치지직 방송 시작 알림 발송 및 실시간 채팅 명령어(포인트 조회, 채팅 도박 등) 처리",
          "치지직 팔로우 인증 — 팔로우 기간 확인 후 Discord 역할 자동 부여",
          "포인트 & 상점 시스템 운영 — 포인트 적립·차감 및 상점 아이템 교환 처리",
          "관리자가 공개로 설정한 서버를 커뮤니티 홍보 페이지에 노출",
          "서버별 설정 저장 및 웹 대시보드 표시",
          "로그인 인증 및 세션 유지",
        ].map((item) => (
          <li key={item} className="flex items-start gap-2">
            <ChevronRight size={14} className="text-accent mt-0.5 flex-shrink-0" />
            {item}
          </li>
        ))}
      </ul>
    ),
  },
  {
    id: "4",
    title: "개인정보 보유 및 이용기간",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>
          수집된 정보는 서비스 이용 중단 요청 시 즉시 삭제됩니다.
        </p>
        <div className="bg-bg rounded-xl border border-border p-4 space-y-2">
          <p className="text-fg font-medium text-sm">자동 삭제 조건</p>
          <ul className="space-y-1.5 text-sm list-none">
            {[
              "봇을 Discord 서버에서 추방(Kick)하면 해당 서버의 모든 데이터가 자동 삭제됩니다.",
              "웹 대시보드 로그인 세션(JWT 토큰)은 7일 후 자동 만료됩니다.",
            ].map((item) => (
              <li key={item} className="flex items-start gap-2">
                <ChevronRight size={13} className="text-accent mt-0.5 flex-shrink-0" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      </div>
    ),
  },
  {
    id: "5",
    title: "개인정보의 제3자 제공",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>
          NexBot은 원칙적으로 수집한 개인정보를 제3자에게 제공하지 않습니다.
          다만, 서비스 기능 제공을 위해 아래 외부 API와 연동됩니다.
        </p>
        <div className="space-y-2">
          {[
            {
              name: "Discord API",
              url: "https://discord.com/privacy",
              desc: "메시지 전송, 역할 부여, 채널 정보 조회 등 봇 핵심 기능에 사용",
            },
            {
              name: "치지직(CHZZK) API / OAuth",
              url: "https://chzzk.naver.com",
              desc: "방송 시작 여부 감지, 방송 정보 조회, 팔로우 날짜 조회(OAuth)에 사용. 액세스 토큰은 일회성으로만 사용하며 서버에 저장하지 않음",
            },
          ].map(({ name, url, desc }) => (
            <div key={name} className="bg-bg rounded-xl border border-border p-4">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-fg font-medium text-sm">{name}</span>
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent text-xs hover:underline"
                >
                  정책 보기 ↗
                </a>
              </div>
              <p className="text-sm">{desc}</p>
            </div>
          ))}
        </div>
      </div>
    ),
  },
  {
    id: "6",
    title: "Google AdSense 및 쿠키",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>
          본 서비스는 Google AdSense 광고 서비스를 사용합니다.
          Google은 쿠키(Cookie)를 사용하여 사용자의 방문 기록, 관심사 등을 분석하고
          맞춤 광고를 제공할 수 있습니다.
        </p>
        <div className="bg-bg rounded-xl border border-border p-4 space-y-2 text-sm">
          {[
            "Google이 사용하는 쿠키에는 개인을 직접 식별하는 정보가 포함되지 않습니다.",
            "브라우저 설정에서 쿠키를 비활성화할 수 있으나, 일부 서비스 기능이 제한될 수 있습니다.",
            "Google의 개인정보 처리 방식은 Google 개인정보처리방침(policies.google.com/privacy)에서 확인할 수 있습니다.",
          ].map((item) => (
            <div key={item} className="flex items-start gap-2">
              <ChevronRight size={13} className="text-accent mt-0.5 flex-shrink-0" />
              <span>{item}</span>
            </div>
          ))}
        </div>
      </div>
    ),
  },
  {
    id: "7",
    title: "개인정보 보호 조치",
    content: (
      <ul className="space-y-2 text-muted leading-relaxed list-none">
        {[
          "로그인 세션은 JWT(JSON Web Token) 방식으로 관리하며, 서버에 세션 정보를 저장하지 않습니다.",
          "데이터베이스 접근은 인증된 서버 내부에서만 가능합니다.",
          "Discord 액세스 토큰은 JWT 내에 암호화되어 저장되며 외부에 노출되지 않습니다.",
          "치지직 OAuth 액세스 토큰은 팔로우 날짜 조회 후 즉시 폐기하며, DB에 영구 저장하지 않습니다.",
          "HTTPS를 통해 모든 통신이 암호화됩니다.",
        ].map((item) => (
          <li key={item} className="flex items-start gap-2">
            <ChevronRight size={14} className="text-accent mt-0.5 flex-shrink-0" />
            {item}
          </li>
        ))}
      </ul>
    ),
  },
  {
    id: "8",
    title: "개인정보 삭제 요청",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>
          개인정보 삭제를 원하시면 아래 방법을 이용하세요.
        </p>
        <div className="space-y-2">
          {[
            {
              title: "자동 삭제",
              desc: "NexBot을 Discord 서버에서 추방하면 해당 서버와 관련된 모든 데이터(설정, XP, 경고 기록 등)가 자동으로 삭제됩니다.",
            },
            {
              title: "수동 삭제 요청",
              desc: "GitHub 이슈를 통해 특정 데이터의 삭제를 요청할 수 있습니다. 요청 시 Discord 사용자 ID 또는 서버 ID를 명시해주세요.",
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
    id: "9",
    title: "문의",
    content: (
      <div className="space-y-3 text-muted leading-relaxed">
        <p>
          개인정보 처리에 관한 문의, 불만 사항은 아래 채널을 이용해주세요.
        </p>
        <div className="bg-bg rounded-xl border border-border p-4">
          <p className="text-fg font-medium text-sm mb-2">GitHub Issues</p>
          <a
            href="https://github.com/DaeDosi/discord-bot/issues"
            target="_blank"
            rel="noreferrer"
            className="text-accent text-sm hover:underline"
          >
            github.com/DaeDosi/discord-bot/issues ↗
          </a>
          <p className="text-xs mt-2">
            이슈 제목에 <code className="bg-bg-hover px-1 py-0.5 rounded text-fg">[개인정보]</code> 를 포함해 작성해주세요.
          </p>
        </div>
      </div>
    ),
  },
];

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-bg text-fg">
      {/* ── Navbar ── */}
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-4xl mx-auto px-5 flex items-center" style={{ height: 60 }}>
          <Link href="/" className="flex items-center gap-2 font-bold text-[17px] text-fg">
            <Bot size={20} className="text-accent" />
            NexBot
          </Link>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-5 py-12 pb-20">
        {/* 헤더 */}
        <div className="mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full
                          border border-border bg-bg-card text-muted text-sm mb-5">
            <Link href="/" className="hover:text-fg transition-colors">홈</Link>
            <ChevronRight size={13} />
            <span className="text-fg">개인정보처리방침</span>
          </div>
          <h1 className="page-title sm:text-4xl mb-3">개인정보처리방침</h1>
          <p className="text-muted">
            시행일: <span className="text-fg">{EFFECTIVE_DATE}</span>
            <span className="mx-3 text-border">|</span>
            서비스명: <span className="text-fg">NexBot</span>
            <span className="mx-3 text-border">|</span>
            운영자: <span className="text-fg">개인 (비공개)</span>
          </p>
        </div>

        {/* 안내 박스 */}
        <div className="bg-accent/8 border border-accent/20 rounded-2xl p-5 mb-10">
          <p className="text-sm text-fg leading-relaxed">
            NexBot(이하 &quot;서비스&quot;)은 이용자의 개인정보를 소중히 여기며,
            「개인정보 보호법」을 준수합니다. 본 방침은 서비스가 수집하는 정보의 종류,
            사용 방법, 보호 조치를 설명합니다.
          </p>
        </div>

        {/* 목차 */}
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
                  {title}
                </a>
              </li>
            ))}
          </ol>
        </nav>

        {/* 본문 */}
        <div className="space-y-10">
          {sections.map(({ id, title, content }) => (
            <section key={id} id={`section-${id}`} className="scroll-mt-20">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-7 h-7 rounded-lg bg-accent/10 flex items-center justify-center flex-shrink-0">
                  <span className="text-accent text-xs font-bold">{id}</span>
                </div>
                <h2 className="text-lg font-bold text-fg">{title}</h2>
              </div>
              <div className="pl-10">{content}</div>
            </section>
          ))}
        </div>

        {/* 하단 */}
        <div className="mt-16 pt-8 border-t border-border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
          <p className="text-sm text-muted">
            본 방침은 <span className="text-fg">{EFFECTIVE_DATE}</span>부터 시행됩니다.
            내용 변경 시 사이트 공지 또는 GitHub을 통해 안내합니다.
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
