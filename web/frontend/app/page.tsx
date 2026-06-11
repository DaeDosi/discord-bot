"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import {
  Shield, Zap, Radio, Star, ArrowRight, Bot,
  LogOut, LayoutDashboard, ChevronDown, Users, Server, Clock,
} from "lucide-react";
import ThemeToggle from "@/components/ThemeToggle";
import type { User } from "@/lib/types";

/* ── Feature data ─────────────────────────────────────────────────────── */
const features = [
  {
    icon: <Shield size={28} className="text-accent" />,
    title: "강력한 관리 기능",
    desc: "경고, 뮤트, 밴, 자동 관리까지. 서버를 안전하게 유지하세요.",
    gradient: "from-accent/20 to-accent/5",
    border: "hover:border-accent/40",
  },
  {
    icon: <Zap size={28} className="text-warning" />,
    title: "레벨링 시스템",
    desc: "채팅 활동에 따라 XP를 지급하고 레벨 보상 역할을 자동으로 부여합니다.",
    gradient: "from-warning/20 to-warning/5",
    border: "hover:border-warning/40",
  },
  {
    icon: <Radio size={28} className="text-chzzk" />,
    title: "치지직 알림",
    desc: "원하는 스트리머가 방송을 시작하면 Discord 채널에 즉시 알림을 보냅니다.",
    gradient: "from-chzzk/20 to-chzzk/5",
    border: "hover:border-chzzk/40",
  },
  {
    icon: <Star size={28} className="text-success" />,
    title: "반응 역할",
    desc: "이모지 반응으로 역할을 자동 부여·회수하는 셀프 역할 시스템.",
    gradient: "from-success/20 to-success/5",
    border: "hover:border-success/40",
  },
];

const stats = [
  { icon: <Server size={20} className="text-accent" />, value: "50+",   label: "서버" },
  { icon: <Users size={20} className="text-chzzk"  />, value: "1,000+", label: "사용자" },
  { icon: <Clock size={20} className="text-warning"/>, value: "24/7",   label: "안정 운영" },
];

/* ── Scroll reveal hook ───────────────────────────────────────────────── */
function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>(".reveal");
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add("visible"); io.unobserve(e.target); }
      }),
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

/* ── Main component ───────────────────────────────────────────────────── */
export default function LandingPage() {
  const [user, setUser]           = useState<User | null>(null);
  const [mounted, setMounted]     = useState(false);
  const [dropOpen, setDropOpen]   = useState(false);
  const dropRef                   = useRef<HTMLDivElement>(null);

  useReveal();

  useEffect(() => {
    setMounted(true);
    try {
      const cached = localStorage.getItem("discord_user");
      const token  = localStorage.getItem("token");
      if (cached && token) setUser(JSON.parse(cached));
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (!dropOpen) return;
    const handler = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setDropOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [dropOpen]);

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("discord_user");
    setUser(null);
    setDropOpen(false);
  };

  const inviteUrl = `https://discord.com/oauth2/authorize?client_id=${process.env.NEXT_PUBLIC_DISCORD_CLIENT_ID}&permissions=8&scope=bot%20applications.commands`;

  return (
    <div className="min-h-screen flex flex-col bg-bg text-fg">

      {/* ── Navbar ─────────────────────────────────────────────────────── */}
      <nav className="border-b border-border bg-bg-card/60 backdrop-blur sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2 font-bold text-lg">
            <Bot size={22} className="text-accent" />
            <span className="text-fg">NexBot</span>
          </div>

          <div className="flex items-center gap-2">
            <ThemeToggle />

            {mounted && (
              user ? (
                /* 로그인 — 프로필 드롭다운 */
                <div className="relative pl-2 border-l border-border" ref={dropRef}>
                  <button
                    onClick={() => setDropOpen((v) => !v)}
                    className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-bg-hover transition-colors"
                  >
                    {user.avatar ? (
                      <Image src={user.avatar} alt={user.username} width={28} height={28} className="rounded-full" />
                    ) : (
                      <div className="w-7 h-7 rounded-full bg-accent/20 flex items-center justify-center">
                        <Bot size={14} className="text-accent" />
                      </div>
                    )}
                    <span className="text-sm text-fg hidden sm:block">{user.username}</span>
                    <ChevronDown size={13} className={`text-muted transition-transform duration-200 ${dropOpen ? "rotate-180" : ""}`} />
                  </button>

                  {dropOpen && (
                    <div className="absolute right-0 mt-1 w-44 rounded-xl border border-border bg-bg-card shadow-xl overflow-hidden z-50 animate-fade-in">
                      <Link
                        href="/dashboard"
                        onClick={() => setDropOpen(false)}
                        className="flex items-center gap-2 px-4 py-2.5 text-sm text-fg hover:bg-bg-hover transition-colors"
                      >
                        <LayoutDashboard size={15} className="text-accent" />
                        대시보드
                      </Link>
                      <button
                        onClick={logout}
                        className="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-danger hover:bg-bg-hover transition-colors border-t border-border"
                      >
                        <LogOut size={15} />
                        로그아웃
                      </button>
                    </div>
                  )}
                </div>
              ) : (
                <Link href="/login" className="btn-primary text-sm ml-2">
                  로그인 <ArrowRight size={15} />
                </Link>
              )
            )}
          </div>
        </div>
      </nav>

      {/* ── Hero ───────────────────────────────────────────────────────── */}
      <section className="relative flex flex-col items-center justify-center text-center px-4 pt-28 pb-24 overflow-hidden">
        {/* Background glow blobs */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="absolute -top-20 left-1/2 -translate-x-1/2 w-[700px] h-[400px] bg-accent/10 rounded-full blur-[80px]" />
          <div className="absolute top-1/2 -left-40 w-[400px] h-[400px] bg-chzzk/8 rounded-full blur-[80px]" />
          <div className="absolute top-1/3 -right-40 w-[400px] h-[400px] bg-warning/8 rounded-full blur-[80px]" />
        </div>

        <div className="relative z-10 max-w-3xl animate-fade-up">
          {/* Badge */}
          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold
                           bg-accent/10 text-accent border border-accent/25 mb-7 tracking-wide">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            All-in-One Discord Bot Dashboard
          </span>

          {/* Title */}
          <h1 className="text-5xl sm:text-6xl font-extrabold tracking-tight mb-5 leading-[1.1]">
            내 서버를 위한<br />
            <span className="bg-gradient-to-r from-accent via-accent-light to-chzzk bg-clip-text text-transparent">
              올인원 디스코드 봇
            </span>
          </h1>

          <p className="text-muted text-lg mb-10 max-w-xl mx-auto leading-relaxed">
            관리, 레벨링, 치지직 알림까지.<br className="hidden sm:block" />
            웹 대시보드에서 클릭 한 번으로 모든 설정을 완성하세요.
          </p>

          <div className="flex gap-3 justify-center flex-wrap">
            {mounted && user ? (
              <Link href="/dashboard" className="btn-primary px-7 py-3 text-base shadow-lg shadow-accent/20">
                대시보드 바로가기 <ArrowRight size={18} />
              </Link>
            ) : (
              <Link href="/login" className="btn-primary px-7 py-3 text-base shadow-lg shadow-accent/20">
                Discord로 시작하기 <ArrowRight size={18} />
              </Link>
            )}
            <a href={inviteUrl} target="_blank" rel="noreferrer"
               className="btn-secondary px-7 py-3 text-base">
              봇 초대하기
            </a>
          </div>
        </div>
      </section>

      {/* ── Stats bar ──────────────────────────────────────────────────── */}
      <section className="border-y border-border bg-bg-card/40 py-8 px-4">
        <div className="max-w-2xl mx-auto grid grid-cols-3 gap-4">
          {stats.map((s, i) => (
            <div
              key={s.label}
              className={`reveal reveal-delay-${i + 1} flex flex-col items-center gap-1.5 text-center`}
            >
              {s.icon}
              <span className="text-2xl font-bold text-fg">{s.value}</span>
              <span className="text-xs text-muted">{s.label}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ───────────────────────────────────────────────────── */}
      <section className="py-24 px-4">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14 reveal">
            <span className="text-sm font-semibold text-accent tracking-widest uppercase mb-2 block">Features</span>
            <h2 className="text-3xl sm:text-4xl font-bold text-fg mb-3">강력한 기능, 간편한 설정</h2>
            <p className="text-muted max-w-md mx-auto">복잡한 명령어 없이 웹 대시보드에서 모든 것을 관리하세요.</p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
            {features.map((f, i) => (
              <div
                key={f.title}
                className={`reveal reveal-delay-${i + 1} group relative bg-bg-card rounded-2xl border border-border p-6
                            transition-all duration-300 ${f.border} hover:shadow-xl hover:-translate-y-1 cursor-default`}
              >
                {/* gradient overlay */}
                <div className={`absolute inset-0 rounded-2xl bg-gradient-to-br ${f.gradient} opacity-0 group-hover:opacity-100 transition-opacity duration-300`} />
                <div className="relative z-10">
                  <div className="mb-4 inline-flex p-2.5 rounded-xl bg-bg">{f.icon}</div>
                  <h3 className="font-semibold text-fg mb-2">{f.title}</h3>
                  <p className="text-muted text-sm leading-relaxed">{f.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Chzzk spotlight ────────────────────────────────────────────── */}
      <section className="py-20 px-4 border-t border-border">
        <div className="max-w-4xl mx-auto reveal">
          <div className="bg-bg-card rounded-2xl border border-border overflow-hidden">
            <div className="flex flex-col md:flex-row">
              {/* Text */}
              <div className="flex-1 p-8 flex flex-col justify-center">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold
                                 bg-chzzk/10 text-chzzk border border-chzzk/25 mb-4 w-fit">
                  <span className="w-1.5 h-1.5 rounded-full bg-chzzk animate-pulse" />
                  치지직 알림
                </span>
                <h3 className="text-2xl font-bold text-fg mb-3">방송 시작 즉시<br />Discord에 알림</h3>
                <p className="text-muted text-sm leading-relaxed mb-5">
                  구독한 치지직 스트리머가 방송을 시작하면 1분 이내에 자동 알림을 전송합니다.
                  방송 제목, 카테고리, 썸네일까지 함께 표시됩니다.
                </p>
                {mounted && (
                  user
                    ? <Link href="/dashboard" className="btn-primary text-sm w-fit">대시보드에서 설정 <ArrowRight size={14} /></Link>
                    : <Link href="/login" className="btn-primary text-sm w-fit">시작하기 <ArrowRight size={14} /></Link>
                )}
              </div>

              {/* Mock embed */}
              <div className="flex-1 bg-[#36393f] dark:bg-[#2f3136] p-6 flex items-center justify-center min-h-48">
                <div className="bg-[#2f3136] dark:bg-[#292b2f] rounded-lg p-4 border-l-4 border-chzzk max-w-xs w-full shadow-lg">
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-5 h-5 rounded-full bg-chzzk/30 flex items-center justify-center">
                      <Radio size={10} className="text-chzzk" />
                    </div>
                    <span className="text-xs text-[#dcddde] font-medium">스트리머명</span>
                  </div>
                  <p className="text-[#00ffa3] text-sm font-semibold mb-1">지금 방송 중인 제목입니다</p>
                  <p className="text-[#b9bbbe] text-xs mb-3">스트리머명님이 방송을 시작했습니다.</p>
                  <div className="bg-[#40444b] rounded-md h-24 flex items-center justify-center">
                    <span className="text-[#72767d] text-xs">썸네일 이미지</span>
                  </div>
                  <div className="flex items-center gap-1 mt-2">
                    <span className="text-[#72767d] text-xs">카테고리:</span>
                    <span className="text-[#dcddde] text-xs">게임명</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────────── */}
      <footer className="border-t border-border py-8 px-4 mt-auto">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-sm text-muted">
          <div className="flex items-center gap-2 font-semibold text-fg">
            <Bot size={16} className="text-accent" />
            NexBot
          </div>
          <p>© 2026 NexBot. All rights reserved.</p>
          <div className="flex items-center gap-4">
            <a href={inviteUrl} target="_blank" rel="noreferrer" className="hover:text-fg transition-colors">봇 초대</a>
            <Link href="/login" className="hover:text-fg transition-colors">로그인</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
