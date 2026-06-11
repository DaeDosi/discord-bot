"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import {
  Shield, Zap, Radio, Star, ArrowRight, Bot, Hash,
  LogOut, LayoutDashboard, ChevronDown,
  Server, Users, Clock,
} from "lucide-react";
import ThemeToggle from "@/components/ThemeToggle";
import type { User } from "@/lib/types";

/* ── Scroll reveal ─────────────────────────────────────────────────────── */
function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>(".reveal");
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (e.isIntersecting) { e.target.classList.add("visible"); io.unobserve(e.target); }
        }),
      { threshold: 0.1, rootMargin: "0px 0px -40px 0px" }
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

/* ── Page ──────────────────────────────────────────────────────────────── */
export default function LandingPage() {
  const [user, setUser]         = useState<User | null>(null);
  const [mounted, setMounted]   = useState(false);
  const [dropOpen, setDropOpen] = useState(false);
  const dropRef                 = useRef<HTMLDivElement>(null);

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
    const h = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setDropOpen(false);
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

      {/* ── Navbar ───────────────────────────────────────────────────── */}
      <nav className="border-b border-border bg-bg/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-5 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2.5 font-bold text-lg">
            <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center">
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
                    className="flex items-center gap-2 px-2.5 py-1.5 rounded-xl hover:bg-bg-hover transition-colors ml-1"
                  >
                    {user.avatar ? (
                      <Image src={user.avatar} alt={user.username} width={26} height={26} className="rounded-full" />
                    ) : (
                      <div className="w-6.5 h-6.5 rounded-full bg-accent/20 flex items-center justify-center">
                        <Bot size={12} className="text-accent" />
                      </div>
                    )}
                    <span className="text-sm text-fg hidden sm:block">{user.username}</span>
                    <ChevronDown size={12} className={`text-muted transition-transform duration-200 ${dropOpen ? "rotate-180" : ""}`} />
                  </button>
                  {dropOpen && (
                    <div className="absolute right-0 mt-2 w-44 rounded-2xl border border-border bg-bg-card shadow-2xl overflow-hidden z-50 animate-fade-in">
                      <Link href="/dashboard" onClick={() => setDropOpen(false)}
                        className="flex items-center gap-2.5 px-4 py-3 text-sm text-fg hover:bg-bg-hover transition-colors">
                        <LayoutDashboard size={14} className="text-accent" />
                        대시보드
                      </Link>
                      <button onClick={logout}
                        className="w-full flex items-center gap-2.5 px-4 py-3 text-sm text-danger hover:bg-bg-hover transition-colors border-t border-border">
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

      {/* ── Hero ─────────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        {/* Dot grid */}
        <div className="pointer-events-none absolute inset-0" aria-hidden
          style={{
            backgroundImage: "radial-gradient(circle, rgba(88,101,242,0.13) 1px, transparent 1px)",
            backgroundSize: "30px 30px",
          }}
        />
        {/* Glow */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div className="absolute top-0 left-1/3 w-[500px] h-[500px] bg-accent/10 rounded-full blur-[120px] -translate-y-1/2" />
          <div className="absolute top-1/2 right-0 w-[400px] h-[400px] bg-chzzk/8 rounded-full blur-[100px]" />
        </div>

        <div className="relative z-10 max-w-6xl mx-auto px-5 py-24 lg:py-32
                        flex flex-col lg:flex-row items-center gap-14 lg:gap-10">

          {/* Left */}
          <div className="flex-1 text-center lg:text-left animate-fade-up">
            <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-semibold
                            bg-accent/10 text-accent border border-accent/20 mb-7">
              <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
              All-in-One Discord Bot
            </div>

            <h1 className="text-5xl sm:text-6xl lg:text-[4.25rem] font-extrabold tracking-tight leading-[1.07] mb-5">
              서버 관리의<br />
              <span className="relative inline-block">
                <span className="bg-gradient-to-r from-accent via-accent-light to-chzzk bg-clip-text text-transparent">
                  새로운 기준
                </span>
              </span>
            </h1>

            <p className="text-muted text-lg leading-relaxed mb-9 max-w-lg mx-auto lg:mx-0">
              관리, 레벨링, 치지직 방송 알림까지 — 하나의 봇, 하나의 대시보드로
              모든 것을 설정하세요.
            </p>

            <div className="flex gap-3 flex-wrap justify-center lg:justify-start mb-10">
              {mounted && user ? (
                <Link href="/dashboard"
                  className="btn-primary px-6 py-2.5 text-[15px] rounded-xl shadow-lg shadow-accent/25">
                  대시보드 <ArrowRight size={16} />
                </Link>
              ) : (
                <Link href="/login"
                  className="btn-primary px-6 py-2.5 text-[15px] rounded-xl shadow-lg shadow-accent/25">
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
                { icon: <Server size={14} className="text-accent" />, v: "50+",   l: "서버" },
                { icon: <Users  size={14} className="text-chzzk"  />, v: "1,000+",l: "사용자" },
                { icon: <Clock  size={14} className="text-warning"/>, v: "24/7",  l: "안정 운영" },
              ].map(s => (
                <div key={s.l} className="flex items-start gap-2">
                  <div className="mt-0.5">{s.icon}</div>
                  <div>
                    <div className="text-xl font-bold text-fg leading-none">{s.v}</div>
                    <div className="text-xs text-muted mt-0.5">{s.l}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Right — Discord mockup */}
          <div className="flex-shrink-0 w-full max-w-[360px] hidden md:block"
               style={{ animation: "fadeUp 0.7s ease 0.15s both" }}>
            <div className="relative">
              {/* Window chrome */}
              <div className="rounded-2xl overflow-hidden border border-white/8 shadow-2xl shadow-black/50"
                   style={{ background: "#313338" }}>
                {/* Title bar */}
                <div className="flex items-center gap-3 px-4 py-3" style={{ background: "#2b2d31" }}>
                  <div className="flex gap-1.5">
                    <div className="w-3 h-3 rounded-full" style={{ background: "#ff5f57" }} />
                    <div className="w-3 h-3 rounded-full" style={{ background: "#ffbd2e" }} />
                    <div className="w-3 h-3 rounded-full" style={{ background: "#28ca41" }} />
                  </div>
                  <div className="flex items-center gap-1.5 ml-1.5">
                    <Hash size={12} style={{ color: "#80848e" }} />
                    <span className="text-xs" style={{ color: "#80848e" }}>알림-채널</span>
                  </div>
                </div>

                {/* Messages */}
                <div className="p-4 space-y-4">
                  {/* Bot message */}
                  <div className="flex gap-3">
                    <div className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0"
                         style={{ background: "rgba(88,101,242,0.25)" }}>
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
                      <div className="rounded-lg border-l-[3px] border-chzzk overflow-hidden"
                           style={{ background: "#2b2d31" }}>
                        <div className="p-3">
                          <div className="flex items-center gap-1.5 mb-2">
                            <div className="w-4 h-4 rounded-full" style={{ background: "rgba(3,199,90,0.3)" }} />
                            <span className="text-xs font-semibold text-chzzk">스트리머이름</span>
                          </div>
                          <p className="text-white text-[13px] font-semibold leading-snug mb-1">
                            지금 진행 중인 방송 제목입니다!
                          </p>
                          <p className="text-xs mb-2.5" style={{ color: "#b5bac1" }}>
                            스트리머이름님이 방송을 시작했습니다.
                          </p>
                          <div className="rounded-md h-[72px] flex items-center justify-center"
                               style={{ background: "#1e1f22" }}>
                            <div className="flex items-center gap-1.5" style={{ color: "#4e5058" }}>
                              <Radio size={11} className="text-chzzk animate-pulse" />
                              <span className="text-xs text-chzzk font-medium">LIVE</span>
                            </div>
                          </div>
                          <div className="mt-2 text-xs" style={{ color: "#b5bac1" }}>
                            <span className="font-semibold">카테고리</span>
                            <br /><span style={{ color: "#9b9d9e" }}>게임 / 콘텐츠</span>
                          </div>
                          <p className="text-[10px] mt-1.5" style={{ color: "#4e5058" }}>chzzk.junah.dev</p>
                        </div>
                      </div>

                      <button className="mt-2 text-xs px-3 py-1.5 rounded text-white flex items-center gap-1"
                              style={{ background: "#4e5058" }}>
                        방송 바로가기 <ArrowRight size={10} />
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {/* LIVE badge */}
              <div className="absolute -top-2.5 -right-2.5 flex items-center gap-1.5
                              bg-danger text-white text-[11px] font-bold px-2.5 py-1 rounded-full
                              shadow-lg shadow-danger/30">
                <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                LIVE
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Features bento ───────────────────────────────────────────── */}
      <section className="py-24 px-5 border-t border-border">
        <div className="max-w-6xl mx-auto">

          {/* Header */}
          <div className="text-center mb-14 reveal">
            <p className="text-accent text-xs font-bold tracking-[0.2em] uppercase mb-3">Features</p>
            <h2 className="text-4xl font-bold text-fg mb-3">필요한 모든 것, 하나의 봇으로</h2>
            <p className="text-muted max-w-sm mx-auto text-[15px]">
              웹 대시보드에서 클릭 한 번으로 모든 기능을 바로 설정하세요.
            </p>
          </div>

          {/* Bento 3-col grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">

            {/* 01 Management — 2 cols */}
            <div className="reveal reveal-delay-1 md:col-span-2 group relative bg-bg-card rounded-2xl
                            border border-border hover:border-accent/35 overflow-hidden
                            transition-all duration-300 hover:shadow-xl hover:shadow-accent/5 p-7">
              <div className="absolute inset-0 bg-gradient-to-br from-accent/6 to-transparent
                              opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <span className="absolute bottom-4 right-6 text-[80px] font-black leading-none
                               text-accent/6 select-none pointer-events-none">01</span>
              <div className="relative z-10">
                <div className="w-11 h-11 rounded-xl bg-accent/12 flex items-center justify-center mb-5">
                  <Shield size={22} className="text-accent" />
                </div>
                <h3 className="text-xl font-bold text-fg mb-2">강력한 관리 기능</h3>
                <p className="text-muted text-sm leading-relaxed mb-6 max-w-sm">
                  경고, 뮤트, 밴, 자동 관리까지. 모든 로그는 지정된 채널에 자동으로 기록됩니다.
                </p>
                <div className="flex flex-wrap gap-2">
                  {["/warn", "/mute", "/ban", "/kick", "/clear"].map((c) => (
                    <span key={c}
                      className="px-2.5 py-1 rounded-lg bg-bg text-xs text-muted font-mono
                                 border border-border group-hover:border-accent/20 transition-colors">
                      {c}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* 02 Leveling — 1 col */}
            <div className="reveal reveal-delay-2 group relative bg-bg-card rounded-2xl
                            border border-border hover:border-warning/35 overflow-hidden
                            transition-all duration-300 hover:shadow-xl hover:shadow-warning/5 p-7">
              <div className="absolute inset-0 bg-gradient-to-br from-warning/6 to-transparent
                              opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <span className="absolute bottom-4 right-6 text-[80px] font-black leading-none
                               text-warning/6 select-none pointer-events-none">02</span>
              <div className="relative z-10">
                <div className="w-11 h-11 rounded-xl bg-warning/12 flex items-center justify-center mb-5">
                  <Zap size={22} className="text-warning" />
                </div>
                <h3 className="text-xl font-bold text-fg mb-2">레벨링 시스템</h3>
                <p className="text-muted text-sm leading-relaxed">
                  채팅 활동에 따라 XP를 지급하고 레벨 보상 역할을 자동으로 부여합니다.
                </p>
              </div>
            </div>

            {/* 03 Reaction Roles — 1 col */}
            <div className="reveal reveal-delay-3 group relative bg-bg-card rounded-2xl
                            border border-border hover:border-success/35 overflow-hidden
                            transition-all duration-300 hover:shadow-xl hover:shadow-success/5 p-7">
              <div className="absolute inset-0 bg-gradient-to-br from-success/6 to-transparent
                              opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <span className="absolute bottom-4 right-6 text-[80px] font-black leading-none
                               text-success/6 select-none pointer-events-none">03</span>
              <div className="relative z-10">
                <div className="w-11 h-11 rounded-xl bg-success/12 flex items-center justify-center mb-5">
                  <Star size={22} className="text-success" />
                </div>
                <h3 className="text-xl font-bold text-fg mb-2">반응 역할</h3>
                <p className="text-muted text-sm leading-relaxed">
                  이모지 반응으로 역할을 자동 부여·회수하는 셀프 역할 시스템.
                </p>
              </div>
            </div>

            {/* 04 Chzzk — 2 cols */}
            <div className="reveal reveal-delay-4 md:col-span-2 group relative bg-bg-card rounded-2xl
                            border border-border hover:border-chzzk/35 overflow-hidden
                            transition-all duration-300 hover:shadow-xl hover:shadow-chzzk/5 p-7">
              <div className="absolute inset-0 bg-gradient-to-br from-chzzk/6 to-transparent
                              opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
              <span className="absolute bottom-4 right-6 text-[80px] font-black leading-none
                               text-chzzk/6 select-none pointer-events-none">04</span>
              <div className="relative z-10 flex flex-col sm:flex-row gap-8 items-start">
                <div className="flex-1">
                  <div className="w-11 h-11 rounded-xl bg-chzzk/12 flex items-center justify-center mb-5">
                    <Radio size={22} className="text-chzzk" />
                  </div>
                  <h3 className="text-xl font-bold text-fg mb-2">치지직 방송 알림</h3>
                  <p className="text-muted text-sm leading-relaxed mb-4">
                    원하는 스트리머가 방송을 시작하면 지정된 Discord 채널에 즉시 알림을 전송합니다.
                    방송 제목, 카테고리, 썸네일까지 함께 표시됩니다.
                  </p>
                  <div className="flex flex-wrap gap-3 text-xs">
                    <span className="flex items-center gap-1.5 text-chzzk font-medium">
                      <span className="w-1.5 h-1.5 rounded-full bg-chzzk animate-pulse" />
                      실시간 감지
                    </span>
                    <span className="text-muted">방송 종료 알림 포함</span>
                    <span className="text-muted">@everyone 멘션 지원</span>
                  </div>
                </div>
              </div>
            </div>

          </div>
        </div>
      </section>

      {/* ── Footer ───────────────────────────────────────────────────── */}
      <footer className="border-t border-border py-8 px-5 mt-auto">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
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
