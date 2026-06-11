"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import {
  Shield, Zap, Radio, Star, ArrowRight, Bot, Hash,
  LogOut, LayoutDashboard, ChevronDown, Server, Users, Clock,
} from "lucide-react";
import ThemeToggle from "@/components/ThemeToggle";
import type { User } from "@/lib/types";

/* ── Scroll reveal ──────────────────────────────────────────────────────── */
function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>(".reveal");
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) { e.target.classList.add("visible"); io.unobserve(e.target); }
        }),
      { threshold: 0.08, rootMargin: "0px 0px -50px 0px" }
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

/* ── Typewriter ─────────────────────────────────────────────────────────── */
function useTypewriter(text: string, delay = 500, speed = 75) {
  const [displayed, setDisplayed] = useState("");
  const [done, setDone]           = useState(false);
  useEffect(() => {
    let i = 0;
    const start = setTimeout(() => {
      const tick = setInterval(() => {
        i++;
        setDisplayed(text.slice(0, i));
        if (i >= text.length) { clearInterval(tick); setDone(true); }
      }, speed);
      return () => clearInterval(tick);
    }, delay);
    return () => clearTimeout(start);
  }, [text, delay, speed]);
  return { displayed, done };
}

/* ── Feature data ───────────────────────────────────────────────────────── */
const FEATURES = [
  {
    icon: Shield,
    title: "강력한 관리 기능",
    desc: "경고, 뮤트, 밴, 자동 관리까지. 로그는 지정 채널에 자동으로 기록됩니다.",
  },
  {
    icon: Zap,
    title: "레벨링 시스템",
    desc: "채팅 활동에 따라 XP를 지급하고, 레벨업 시 역할을 자동으로 부여합니다.",
  },
  {
    icon: Radio,
    title: "치지직 방송 알림",
    desc: "원하는 스트리머가 방송을 시작하면 1분 이내에 Discord 채널로 알림을 전송합니다.",
  },
  {
    icon: Star,
    title: "반응 역할",
    desc: "이모지 반응으로 역할을 자동 부여·회수하는 셀프 역할 시스템을 설정하세요.",
  },
];

/* ── Page ───────────────────────────────────────────────────────────────── */
export default function LandingPage() {
  const [user, setUser]         = useState<User | null>(null);
  const [mounted, setMounted]   = useState(false);
  const [dropOpen, setDropOpen] = useState(false);
  const dropRef                 = useRef<HTMLDivElement>(null);

  useReveal();
  const { displayed: typed, done: typeDone } = useTypewriter("새로운 기준", 700, 80);

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
    const h = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node))
        setDropOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
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

      {/* ── Navbar ────────────────────────────────────────────────────── */}
      <nav className="border-b border-border bg-bg/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-5 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5 font-bold text-lg">
            <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center shadow-md shadow-accent/30">
              <Bot size={16} className="text-white" />
            </div>
            <span className="text-fg">NexBot</span>
          </div>

          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            {mounted && (
              user ? (
                <div className="relative" ref={dropRef}>
                  <button
                    onClick={() => setDropOpen((v) => !v)}
                    className="flex items-center gap-2 px-2.5 py-1.5 rounded-xl
                               hover:bg-bg-hover transition-colors ml-1"
                  >
                    {user.avatar ? (
                      <Image src={user.avatar} alt={user.username}
                             width={26} height={26} className="rounded-full" />
                    ) : (
                      <div className="w-6 h-6 rounded-full bg-accent/20 flex items-center justify-center">
                        <Bot size={12} className="text-accent" />
                      </div>
                    )}
                    <span className="text-sm text-fg hidden sm:block">{user.username}</span>
                    <ChevronDown size={12} className={`text-muted transition-transform duration-200
                                                       ${dropOpen ? "rotate-180" : ""}`} />
                  </button>
                  {dropOpen && (
                    <div className="absolute right-0 mt-2 w-44 rounded-2xl border border-border
                                    bg-bg-card shadow-2xl overflow-hidden z-50 animate-fade-in">
                      <Link href="/dashboard" onClick={() => setDropOpen(false)}
                        className="flex items-center gap-2.5 px-4 py-3 text-sm text-fg
                                   hover:bg-bg-hover transition-colors">
                        <LayoutDashboard size={14} className="text-accent" />
                        대시보드
                      </Link>
                      <button onClick={logout}
                        className="w-full flex items-center gap-2.5 px-4 py-3 text-sm text-danger
                                   hover:bg-bg-hover transition-colors border-t border-border">
                        <LogOut size={14} />
                        로그아웃
                      </button>
                    </div>
                  )}
                </div>
              ) : (
                <Link href="/login" className="btn-primary text-sm ml-1 rounded-xl">
                  로그인 <ArrowRight size={14} />
                </Link>
              )
            )}
          </div>
        </div>
      </nav>

      {/* ── Hero ──────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        {/* Dot grid */}
        <div className="pointer-events-none absolute inset-0" aria-hidden
          style={{
            backgroundImage:
              "radial-gradient(circle, rgba(88,101,242,0.12) 1px, transparent 1px)",
            backgroundSize: "30px 30px",
          }}
        />
        {/* Ambient glow */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="absolute -top-40 left-1/3 w-[600px] h-[600px]
                          bg-accent/10 rounded-full blur-[130px] -translate-x-1/2" />
          <div className="absolute top-1/2 right-0 translate-x-1/4 w-[400px] h-[400px]
                          bg-accent/6 rounded-full blur-[100px]" />
        </div>

        <div className="relative z-10 max-w-6xl mx-auto px-5 py-24 lg:py-36
                        flex flex-col lg:flex-row items-center gap-14 lg:gap-10">

          {/* ── Left: copy ─────────────────────────────────────────────── */}
          <div className="flex-1 text-center lg:text-left animate-fade-up">
            {/* Badge */}
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs
                            font-semibold bg-accent/10 text-accent border border-accent/20 mb-7">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              Discord Bot Dashboard
            </div>

            {/* Headline */}
            <h1 className="text-5xl sm:text-6xl lg:text-[4.5rem] font-extrabold
                           tracking-tight leading-[1.07] mb-5">
              서버 관리의
              <br />
              {/* Typewriter gradient text — invisible placeholder prevents layout shift */}
              <span className="relative inline-block">
                <span className="invisible select-none">새로운 기준</span>
                <span className="absolute inset-0 bg-gradient-to-r from-accent via-[#818cf8]
                                 to-[#a5b4fc] bg-clip-text text-transparent whitespace-nowrap">
                  {typed}
                </span>
                {!typeDone && (
                  <span className="absolute text-accent/70"
                        style={{ left: `${typed.length * 1}em` }}>
                    |
                  </span>
                )}
              </span>
            </h1>

            <p className="text-muted text-lg leading-relaxed mb-9 max-w-md mx-auto lg:mx-0">
              관리, 레벨링, 치지직 방송 알림까지 —<br className="hidden sm:block" />
              하나의 봇으로 모든 것을 해결하세요.
            </p>

            {/* CTAs */}
            <div className="flex gap-3 flex-wrap justify-center lg:justify-start mb-10">
              {mounted && user ? (
                <Link href="/dashboard"
                  className="btn-primary px-6 py-2.5 text-[15px] rounded-xl
                             shadow-lg shadow-accent/20 hover:shadow-accent/30">
                  대시보드 <ArrowRight size={16} />
                </Link>
              ) : (
                <Link href="/login"
                  className="btn-primary px-6 py-2.5 text-[15px] rounded-xl
                             shadow-lg shadow-accent/20 hover:shadow-accent/30">
                  Discord로 시작 <ArrowRight size={16} />
                </Link>
              )}
              <a href={inviteUrl} target="_blank" rel="noreferrer"
                 className="btn-secondary px-6 py-2.5 text-[15px] rounded-xl">
                봇 초대하기
              </a>
            </div>

            {/* Stats */}
            <div className="flex gap-8 justify-center lg:justify-start">
              {[
                { icon: <Server size={13} className="text-accent/70" />, v: "50+",    l: "서버" },
                { icon: <Users  size={13} className="text-accent/70" />, v: "1,000+", l: "사용자" },
                { icon: <Clock  size={13} className="text-accent/70" />, v: "24/7",   l: "안정 운영" },
              ].map(({ icon, v, l }) => (
                <div key={l} className="flex items-start gap-1.5">
                  <div className="mt-1">{icon}</div>
                  <div>
                    <div className="text-xl font-bold text-fg leading-none">{v}</div>
                    <div className="text-xs text-muted mt-0.5">{l}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ── Right: Discord window mockup ─────────────────────────── */}
          <div className="flex-shrink-0 w-full max-w-[360px] hidden md:block"
               style={{ animation: "fadeUp 0.7s ease 0.2s both" }}>
            <div className="relative">
              <div className="rounded-2xl overflow-hidden border border-white/8
                              shadow-2xl shadow-black/50" style={{ background: "#313338" }}>
                {/* Window bar */}
                <div className="flex items-center gap-3 px-4 py-3"
                     style={{ background: "#2b2d31" }}>
                  <div className="flex gap-1.5">
                    {["#ff5f57","#ffbd2e","#28ca41"].map(c => (
                      <div key={c} className="w-3 h-3 rounded-full" style={{ background: c }} />
                    ))}
                  </div>
                  <div className="flex items-center gap-1.5 ml-1.5">
                    <Hash size={12} style={{ color: "#80848e" }} />
                    <span className="text-xs" style={{ color: "#80848e" }}>알림-채널</span>
                  </div>
                </div>

                {/* Message */}
                <div className="p-4">
                  <div className="flex gap-3">
                    <div className="w-10 h-10 rounded-full flex-shrink-0 flex items-center
                                    justify-center" style={{ background: "rgba(88,101,242,0.25)" }}>
                      <Bot size={18} className="text-accent" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className="text-sm font-semibold text-white">NexBot</span>
                        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded text-white"
                              style={{ background: "#5865f2" }}>BOT</span>
                        <span className="text-xs" style={{ color: "#6d6f78" }}>오늘 오후 3:42</span>
                      </div>

                      {/* Embed */}
                      <div className="rounded-lg overflow-hidden border-l-[3px]"
                           style={{ background: "#2b2d31", borderLeftColor: "#03c75a" }}>
                        <div className="p-3">
                          <div className="flex items-center gap-1.5 mb-1.5">
                            <div className="w-4 h-4 rounded-full"
                                 style={{ background: "rgba(3,199,90,0.3)" }} />
                            <span className="text-xs font-semibold"
                                  style={{ color: "#03c75a" }}>스트리머이름</span>
                          </div>
                          <p className="text-white text-[13px] font-semibold leading-snug">
                            지금 진행 중인 방송 제목입니다!
                          </p>
                          <p className="text-xs mt-1 mb-2.5" style={{ color: "#b5bac1" }}>
                            스트리머이름님이 방송을 시작했습니다.
                          </p>
                          <div className="rounded-md h-16 flex items-center justify-center"
                               style={{ background: "#1e1f22" }}>
                            <div className="flex items-center gap-1.5">
                              <Radio size={10} style={{ color: "#03c75a" }} />
                              <span className="text-xs font-medium" style={{ color: "#03c75a" }}>
                                LIVE
                              </span>
                            </div>
                          </div>
                          <div className="mt-2 text-xs" style={{ color: "#b5bac1" }}>
                            <span className="font-medium">카테고리</span>
                            <span style={{ color: "#72767d" }}> · 게임 / 콘텐츠</span>
                          </div>
                          <p className="text-[10px] mt-1.5" style={{ color: "#4e5058" }}>
                            chzzk.junah.dev
                          </p>
                        </div>
                      </div>

                      <button className="mt-2 text-xs px-3 py-1.5 rounded text-white
                                         flex items-center gap-1"
                              style={{ background: "#4e5058" }}>
                        방송 바로가기 <ArrowRight size={10} />
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {/* LIVE pill */}
              <div className="absolute -top-2.5 -right-2.5 flex items-center gap-1.5
                              bg-danger text-white text-[11px] font-bold
                              px-2.5 py-1 rounded-full shadow-lg shadow-danger/30">
                <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                LIVE
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ─────────────────────────────────────────────────── */}
      <section className="py-28 px-5 border-t border-border">
        <div className="max-w-5xl mx-auto">

          {/* Section label */}
          <div className="text-center mb-16 reveal">
            <p className="text-xs font-bold tracking-[0.25em] uppercase text-accent mb-4">
              Features
            </p>
            <h2 className="text-4xl sm:text-5xl font-extrabold text-fg tracking-tight mb-4">
              필요한 모든 것,<br />
              <span className="bg-gradient-to-r from-accent to-[#818cf8]
                               bg-clip-text text-transparent">
                하나의 봇으로
              </span>
            </h2>
            <p className="text-muted text-base max-w-sm mx-auto leading-relaxed">
              복잡한 설정 없이 웹 대시보드에서 클릭 한 번으로 모든 기능을 사용하세요.
            </p>
          </div>

          {/* 2 × 2 card grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            {FEATURES.map(({ icon: Icon, title, desc }, i) => (
              <div
                key={title}
                className={`reveal reveal-delay-${i + 1} group relative bg-bg-card rounded-2xl
                            border border-border p-7 overflow-hidden
                            hover:border-accent/30 hover:-translate-y-0.5
                            transition-all duration-250`}
              >
                {/* Hover glow */}
                <div className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100
                                transition-opacity duration-300 pointer-events-none"
                     style={{
                       background:
                         "radial-gradient(600px circle at var(--mx,50%) var(--my,50%), rgba(88,101,242,0.07), transparent 60%)",
                     }} />

                {/* Content */}
                <div className="relative z-10">
                  <div className="w-11 h-11 rounded-xl bg-accent/10 flex items-center
                                  justify-center mb-5 ring-1 ring-accent/15
                                  group-hover:bg-accent/15 transition-colors duration-200">
                    <Icon size={20} className="text-accent" />
                  </div>
                  <h3 className="text-lg font-bold text-fg mb-2.5 tracking-tight">
                    {title}
                  </h3>
                  <p className="text-muted text-sm leading-relaxed">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Footer ───────────────────────────────────────────────────── */}
      <footer className="border-t border-border py-8 px-5 mt-auto">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center
                        justify-between gap-4">
          <div className="flex items-center gap-2.5 font-semibold text-fg">
            <div className="w-7 h-7 rounded-lg bg-accent/15 flex items-center justify-center">
              <Bot size={13} className="text-accent" />
            </div>
            NexBot
          </div>
          <p className="text-sm text-muted">© 2026 NexBot. All rights reserved.</p>
          <div className="flex items-center gap-5 text-sm text-muted">
            <a href={inviteUrl} target="_blank" rel="noreferrer"
               className="hover:text-fg transition-colors">봇 초대</a>
            <Link href="/login" className="hover:text-fg transition-colors">로그인</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
