"use client";
import Link from "next/link";
import { Shield, Zap, Radio, Star, ArrowRight, Bot } from "lucide-react";

const features = [
  {
    icon: <Shield size={24} className="text-accent" />,
    title: "강력한 관리 기능",
    desc: "경고, 뮤트, 밴, 자동 관리까지. 서버를 안전하게 유지하세요.",
  },
  {
    icon: <Zap size={24} className="text-warning" />,
    title: "레벨링 시스템",
    desc: "채팅 활동에 따라 XP를 지급하고 레벨 보상 역할을 자동으로 부여합니다.",
  },
  {
    icon: <Radio size={24} className="text-chzzk" />,
    title: "치지직 알림",
    desc: "원하는 스트리머가 방송을 시작하면 Discord 채널에 즉시 알림을 보냅니다.",
  },
  {
    icon: <Star size={24} className="text-success" />,
    title: "반응 역할",
    desc: "이모지 반응으로 역할을 자동 부여·회수하는 셀프 역할 시스템.",
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen flex flex-col">
      {/* Navbar */}
      <nav className="border-b border-border bg-bg-card/50 backdrop-blur sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2 font-bold text-lg">
            <Bot size={22} className="text-accent" />
            <span>NexBot</span>
          </div>
          <Link href="/login" className="btn-primary text-sm">
            대시보드 시작 <ArrowRight size={16} />
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="flex-1 flex flex-col items-center justify-center text-center px-4 py-24 relative overflow-hidden">
        {/* glow */}
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2
                          w-[600px] h-[400px] bg-accent/10 rounded-full blur-3xl" />
        </div>

        <div className="relative z-10 max-w-3xl">
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium
                           bg-accent/10 text-accent border border-accent/20 mb-6">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            Discord Bot Dashboard
          </span>
          <h1 className="text-5xl font-extrabold tracking-tight mb-6 leading-tight">
            내 서버를 위한<br />
            <span className="text-accent">올인원 디스코드 봇</span>
          </h1>
          <p className="text-muted text-lg mb-10 max-w-xl mx-auto">
            관리, 레벨링, 치지직 알림까지. 웹 대시보드에서 클릭 한 번으로 모든 설정을 완성하세요.
          </p>
          <div className="flex gap-4 justify-center flex-wrap">
            <Link href="/login" className="btn-primary px-6 py-3 text-base">
              Discord로 시작하기 <ArrowRight size={18} />
            </Link>
            <a
              href={`https://discord.com/oauth2/authorize?client_id=${process.env.NEXT_PUBLIC_DISCORD_CLIENT_ID}&permissions=8&scope=bot%20applications.commands`}
              target="_blank"
              rel="noreferrer"
              className="btn-secondary px-6 py-3 text-base"
            >
              봇 초대하기
            </a>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-20 px-4 border-t border-border">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-12">주요 기능</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {features.map((f) => (
              <div key={f.title} className="card hover:border-accent/40 transition-colors">
                <div className="mb-4">{f.icon}</div>
                <h3 className="font-semibold text-white mb-2">{f.title}</h3>
                <p className="text-muted text-sm leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-4 border-t border-border text-center">
        <h2 className="text-3xl font-bold mb-4">지금 바로 시작하세요</h2>
        <p className="text-muted mb-8">무료로 봇을 초대하고 대시보드에서 설정하세요.</p>
        <Link href="/login" className="btn-primary mx-auto px-8 py-3 text-base inline-flex">
          Discord 로그인 <ArrowRight size={18} />
        </Link>
      </section>

      <footer className="border-t border-border py-6 text-center text-muted text-sm">
        © 2026 NexBot. All rights reserved.
      </footer>
    </div>
  );
}
