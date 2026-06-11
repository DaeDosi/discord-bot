"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import {
  Bot, Shield, TrendingUp, Radio, Smile,
  LogOut, ChevronRight, ArrowRight, Hash,
} from "lucide-react";
import ThemeToggle from "@/components/ThemeToggle";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

// ── Hooks ──────────────────────────────────────────────────────────────────────

function useTypewriter(words: string[], speed = 70, pause = 1800) {
  const [display, setDisplay] = useState("");
  const [wordIdx, setWordIdx] = useState(0);
  const [charIdx, setCharIdx] = useState(0);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const word = words[wordIdx % words.length];
    const delay = deleting ? speed / 2 : charIdx === word.length ? pause : speed;
    const t = setTimeout(() => {
      if (!deleting && charIdx < word.length) {
        setDisplay(word.slice(0, charIdx + 1));
        setCharIdx(c => c + 1);
      } else if (!deleting && charIdx === word.length) {
        setDeleting(true);
      } else if (deleting && charIdx > 0) {
        setDisplay(word.slice(0, charIdx - 1));
        setCharIdx(c => c - 1);
      } else {
        setDeleting(false);
        setWordIdx(i => i + 1);
      }
    }, delay);
    return () => clearTimeout(t);
  }, [charIdx, deleting, wordIdx, words, speed, pause]);

  return display;
}

function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll<HTMLElement>(".reveal");
    const io = new IntersectionObserver(
      (entries) => entries.forEach(e => e.isIntersecting && e.target.classList.add("visible")),
      { threshold: 0.12 }
    );
    els.forEach(el => io.observe(el));
    return () => io.disconnect();
  }, []);
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function ProfileDropdown({ user }: { user: User }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("discord_user");
    window.location.href = "/";
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-full
                   hover:bg-bg-hover border border-transparent hover:border-border
                   transition-all"
      >
        {user.avatar
          ? <Image src={user.avatar} alt={user.username} width={28} height={28} className="rounded-full" />
          : <div className="w-7 h-7 rounded-full bg-accent/20 flex items-center justify-center">
              <Bot size={13} className="text-accent" />
            </div>}
        <span className="text-sm text-fg hidden sm:block">{user.username}</span>
        <ChevronRight size={13} className={`text-muted transition-transform ${open ? "rotate-90" : ""}`} />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-44 bg-bg-card border border-border rounded-xl
                        shadow-2xl shadow-black/40 py-1.5 z-50 animate-fade-in">
          <Link
            href="/dashboard"
            className="flex items-center gap-2.5 px-4 py-2.5 text-sm text-fg hover:bg-bg-hover transition-colors"
            onClick={() => setOpen(false)}
          >
            <Bot size={14} className="text-accent" /> 대시보드
          </Link>
          <div className="h-px bg-border mx-3 my-1" />
          <button
            onClick={logout}
            className="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-danger hover:bg-danger/8 transition-colors"
          >
            <LogOut size={14} /> 로그아웃
          </button>
        </div>
      )}
    </div>
  );
}

// Mockup: terminal
function TerminalMockup() {
  const cmds = [
    { cmd: "/warn", args: "@유저 욕설 사용", ok: true },
    { cmd: "/mute", args: "@유저 30m 도배", ok: true },
    { cmd: "/clear", args: "100", ok: true },
    { cmd: "/ban", args: "@유저 반복 위반", ok: true },
  ];
  return (
    <div className="bg-[#0b0d14] rounded-2xl border border-white/8 p-5
                    shadow-2xl shadow-black/60 w-full max-w-sm font-mono text-sm">
      <div className="flex items-center gap-2 mb-5">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-yellow-400/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
        </div>
        <span className="text-[11px] text-white/20 ml-2">NexBot — 서버 관리</span>
      </div>
      <div className="space-y-2.5">
        {cmds.map(({ cmd, args, ok }) => (
          <div key={cmd + args} className="flex items-center gap-2">
            <span className="text-[#5865f2]/40 text-xs select-none">$</span>
            <span className="text-[#818cf8]">{cmd}</span>
            <span className="text-white/40">{args}</span>
            {ok && <span className="ml-auto text-[#57f287] text-xs">✓</span>}
          </div>
        ))}
        <div className="flex items-center gap-2 opacity-30 pt-1">
          <span className="text-[#5865f2]/40 text-xs">$</span>
          <span className="inline-block w-1.5 h-4 bg-[#5865f2]/60 animate-pulse" />
        </div>
      </div>
    </div>
  );
}

// Mockup: leaderboard
function LeaderboardMockup() {
  const rows = [
    { medal: "🥇", name: "스타플레이어", lv: 42, xp: 8400, pct: 84 },
    { medal: "🥈", name: "레벨업러너", lv: 38, xp: 7600, pct: 76 },
    { medal: "🥉", name: "꾸준한멤버", lv: 31, xp: 6200, pct: 62 },
  ];
  return (
    <div className="bg-bg-card rounded-2xl border border-border p-5 shadow-xl w-full max-w-sm">
      <div className="flex items-center justify-between mb-5">
        <p className="text-xs font-bold text-muted uppercase tracking-widest">리더보드</p>
        <span className="text-[11px] text-accent px-2 py-0.5 rounded-full bg-accent/10">이번 주</span>
      </div>
      <div className="space-y-4">
        {rows.map(({ medal, name, lv, xp, pct }) => (
          <div key={name}>
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-base leading-none">{medal}</span>
              <span className="text-sm text-fg font-medium flex-1">{name}</span>
              <span className="text-xs text-muted">Lv.{lv}</span>
              <span className="text-xs text-muted/60">{xp.toLocaleString()} XP</span>
            </div>
            <div className="h-1.5 rounded-full bg-bg overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-[#5865f2] to-[#818cf8]"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Mockup: Discord embed (Chzzk notification)
function ChzzkEmbedMockup() {
  return (
    <div className="rounded-2xl overflow-hidden border border-white/8 shadow-2xl shadow-black/60 w-full max-w-sm"
         style={{ background: "#313338" }}>
      <div className="flex items-center gap-2 px-4 py-2.5" style={{ background: "#2b2d31" }}>
        <Hash size={11} style={{ color: "#80848e" }} />
        <span className="text-[11px]" style={{ color: "#80848e" }}>알림-채널</span>
      </div>
      <div className="p-4">
        <div className="flex gap-3">
          <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center"
               style={{ background: "rgba(88,101,242,0.2)" }}>
            <Bot size={16} style={{ color: "#5865f2" }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold text-white">NexBot</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold text-white"
                    style={{ background: "#5865f2" }}>BOT</span>
            </div>
            <div className="rounded-lg border-l-[3px] p-3"
                 style={{ background: "#2b2d31", borderLeftColor: "#03c75a" }}>
              <p className="text-white text-[13px] font-semibold mb-1 leading-snug">
                오늘의 하이라이트 게임 방송!
              </p>
              <p className="text-[12px] mb-3" style={{ color: "#b5bac1" }}>
                스트리머님이 방송을 시작했습니다.
              </p>
              <div className="rounded-md h-16 flex items-center justify-center gap-2"
                   style={{ background: "#1e1f22" }}>
                <Radio size={12} style={{ color: "#03c75a" }} />
                <span className="text-[11px]" style={{ color: "#03c75a" }}>LIVE</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Mockup: reaction roles
function ReactionRolesMockup() {
  const roles = [
    { emoji: "😊", label: "일반 멤버", active: true },
    { emoji: "🎮", label: "게이머", active: true },
    { emoji: "🎨", label: "아티스트", active: false },
    { emoji: "📢", label: "공지 수신", active: true },
  ];
  return (
    <div className="bg-bg-card rounded-2xl border border-border p-5 shadow-xl w-full max-w-sm">
      <p className="text-xs font-bold text-muted uppercase tracking-widest mb-4">역할 선택</p>
      <div className="space-y-2">
        {roles.map(({ emoji, label, active }) => (
          <div
            key={label}
            className={`flex items-center gap-3 px-3 py-2.5 rounded-xl border transition-colors ${
              active ? "bg-accent/8 border-accent/20" : "border-border"
            }`}
          >
            <span className="text-xl leading-none">{emoji}</span>
            <span className="text-sm text-fg flex-1">{label}</span>
            <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center transition-colors ${
              active ? "bg-accent border-accent" : "border-border"
            }`}>
              {active && <span className="text-white text-[9px] font-bold leading-none">✓</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function HomePage() {
  const [user, setUser] = useState<User | null>(null);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);
  const [stats, setStats] = useState<{ guilds: number; chzzk_subscriptions: number } | null>(null);

  const typed = useTypewriter(["스마트하게.", "쉽게.", "빠르게."]);
  useReveal();

  useEffect(() => {
    try {
      const raw = localStorage.getItem("discord_user");
      if (raw) setUser(JSON.parse(raw));
    } catch {}

    api.auth.getLoginUrl()
      .then(d => setLoginUrl(d.url))
      .catch(() => {});

    api.stats.get()
      .then(d => setStats(d))
      .catch(() => {});
  }, []);

  const features: {
    icon: React.ReactNode;
    title: string;
    desc: string;
    detail: string;
    mockup: React.ReactNode;
    flip: boolean;
  }[] = [
    {
      icon: <Shield size={22} className="text-accent" />,
      title: "서버 관리",
      desc: "강력한 관리 도구",
      detail:
        "경고, 뮤트, 차단, 메시지 삭제까지 슬래시 커맨드 하나로 빠르게 처리하세요. 웹 대시보드에서 권한과 설정을 한눈에 관리할 수 있습니다.",
      mockup: <TerminalMockup />,
      flip: false,
    },
    {
      icon: <TrendingUp size={22} className="text-accent" />,
      title: "레벨링 시스템",
      desc: "활동 기반 성장",
      detail:
        "채팅 활동에 따라 자동으로 XP가 쌓이고 레벨이 오릅니다. 리더보드로 멤버들의 참여도를 높이고, 레벨별 역할 보상으로 커뮤니티를 활성화하세요.",
      mockup: <LeaderboardMockup />,
      flip: true,
    },
    {
      icon: <Radio size={22} className="text-accent" />,
      title: "치지직 알림",
      desc: "실시간 방송 알림",
      detail:
        "치지직 스트리머의 방송 시작을 실시간으로 감지해 Discord 채널에 자동 알림을 보냅니다. 방송 제목, 카테고리, 썸네일이 포함된 임베드 메시지를 받아보세요.",
      mockup: <ChzzkEmbedMockup />,
      flip: false,
    },
    {
      icon: <Smile size={22} className="text-accent" />,
      title: "리액션 역할",
      desc: "셀프 역할 선택",
      detail:
        "멤버가 이모지를 클릭해 직접 역할을 선택할 수 있습니다. 관심사나 게임별 역할을 멤버 스스로 관리하게 해 서버 운영 부담을 줄이세요.",
      mockup: <ReactionRolesMockup />,
      flip: true,
    },
  ];

  const statsDisplay = [
    { value: stats ? `${stats.guilds}+` : null, label: "등록 서버" },
    { value: stats ? `${stats.chzzk_subscriptions}+` : null, label: "치지직 구독" },
    { value: "24/7", label: "안정 운영" },
  ];

  return (
    <div className="min-h-screen bg-bg text-fg">
      {/* ── Navbar ── */}
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-6xl mx-auto px-5 flex items-center justify-between" style={{ height: 60 }}>
          <Link href="/" className="flex items-center gap-2 font-bold text-[17px] text-fg">
            <Bot size={20} className="text-accent" />
            NexBot
          </Link>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            {user
              ? <ProfileDropdown user={user} />
              : loginUrl
                ? <a href={loginUrl}
                     className="flex items-center gap-1.5 text-sm font-medium px-4 py-2
                                bg-accent hover:bg-accent-hover text-white rounded-lg transition-colors">
                    로그인 <ArrowRight size={13} />
                  </a>
                : null}
          </div>
        </div>
      </header>

      {/* ── Hero ── */}
      <section className="relative flex flex-col items-center justify-center
                          min-h-[calc(100vh-60px)] px-5 text-center overflow-hidden">
        {/* Grid background */}
        <div className="absolute inset-0 pointer-events-none select-none">
          <div
            className="absolute inset-0"
            style={{
              backgroundImage:
                "linear-gradient(rgb(var(--color-border-rgb)/0.4) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--color-border-rgb)/0.4) 1px, transparent 1px)",
              backgroundSize: "60px 60px",
            }}
          />
          <div className="absolute inset-0 bg-gradient-to-b from-bg via-transparent to-bg" />
        </div>

        {/* Glow orb */}
        <div
          className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2
                     w-[600px] h-[400px] rounded-full pointer-events-none"
          style={{ background: "radial-gradient(ellipse, rgba(88,101,242,0.12) 0%, transparent 70%)" }}
        />

        <div className="relative z-10 max-w-3xl mx-auto">
          <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full
                          border border-accent/30 bg-accent/8 text-accent text-sm
                          mb-8 animate-fade-in">
            <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
            Discord 봇 대시보드
          </div>

          <h1 className="text-5xl sm:text-6xl lg:text-7xl font-bold leading-tight tracking-tight mb-6
                         animate-fade-up">
            Discord 서버를<br />
            <span className="bg-gradient-to-r from-[#5865f2] via-[#818cf8] to-[#a5b4fc]
                             bg-clip-text text-transparent">
              {typed}
              <span className="inline-block w-0.5 h-[0.85em] bg-[#818cf8]/80 ml-0.5 align-middle animate-pulse" />
            </span>
          </h1>

          <p
            className="text-lg text-muted max-w-xl mx-auto mb-10 leading-relaxed animate-fade-up"
            style={{ animationDelay: "0.1s" }}
          >
            관리, 레벨링, 치지직 알림, 리액션 역할까지.<br />
            하나의 봇으로, 하나의 대시보드로.
          </p>

          <div
            className="flex flex-wrap items-center justify-center gap-3 animate-fade-up"
            style={{ animationDelay: "0.2s" }}
          >
            {loginUrl
              ? <a href={loginUrl}
                   className="flex items-center gap-2 px-6 py-3 bg-accent hover:bg-accent-hover
                              text-white font-semibold rounded-xl transition-colors shadow-lg shadow-accent/25">
                  Discord로 시작하기 <ArrowRight size={16} />
                </a>
              : <Link href="/dashboard"
                      className="flex items-center gap-2 px-6 py-3 bg-accent hover:bg-accent-hover
                                 text-white font-semibold rounded-xl transition-colors shadow-lg shadow-accent/25">
                  대시보드 열기 <ArrowRight size={16} />
                </Link>}
            <a
              href="https://github.com"
              target="_blank" rel="noreferrer"
              className="flex items-center gap-2 px-6 py-3 border border-border
                         hover:border-accent/40 text-fg rounded-xl transition-colors
                         hover:bg-bg-hover font-medium"
            >
              GitHub
            </a>
          </div>
        </div>

        {/* Scroll indicator */}
        <div
          className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-1.5
                     text-muted/40 animate-fade-in"
          style={{ animationDelay: "0.8s" }}
        >
          <span className="text-[11px] tracking-widest uppercase">Scroll</span>
          <div className="w-px h-8 bg-gradient-to-b from-muted/30 to-transparent" />
        </div>
      </section>

      {/* ── Stats ── */}
      <section className="border-y border-border bg-bg-card/40">
        <div className="max-w-4xl mx-auto px-5 py-12 grid grid-cols-3 gap-8 text-center">
          {statsDisplay.map(({ value, label }, i) => (
            <div key={label} className={`reveal reveal-delay-${i + 1}`}>
              <p className={`text-4xl font-bold mb-1 ${
                value
                  ? "bg-gradient-to-r from-[#5865f2] to-[#818cf8] bg-clip-text text-transparent"
                  : "text-muted/30 animate-pulse"
              }`}>
                {value ?? "—"}
              </p>
              <p className="text-sm text-muted">{label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ── */}
      <section className="max-w-6xl mx-auto px-5 py-24 space-y-28">
        <div className="text-center reveal mb-4">
          <p className="text-sm font-semibold text-accent uppercase tracking-widest mb-3">기능</p>
          <h2 className="text-3xl sm:text-4xl font-bold text-fg">하나의 봇으로 모든 것을</h2>
        </div>

        {features.map(({ icon, title, desc, detail, mockup, flip }) => (
          <div
            key={title}
            className={`reveal flex flex-col ${
              flip ? "lg:flex-row-reverse" : "lg:flex-row"
            } items-center gap-12 lg:gap-20`}
          >
            <div className="flex-1 max-w-lg">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-xl bg-accent/10 flex items-center justify-center">
                  {icon}
                </div>
                <span className="text-sm font-semibold text-accent uppercase tracking-wider">{desc}</span>
              </div>
              <h3 className="text-2xl sm:text-3xl font-bold text-fg mb-4">{title}</h3>
              <p className="text-muted leading-relaxed text-[15px]">{detail}</p>
            </div>

            <div className="flex-1 flex justify-center lg:justify-end">
              {mockup}
            </div>
          </div>
        ))}
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-border">
        <div className="max-w-6xl mx-auto px-5 py-8 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm text-muted">
            <Bot size={16} className="text-accent" />
            <span className="font-semibold text-fg">NexBot</span>
            <span>— Discord 봇 대시보드</span>
          </div>
          <div className="flex items-center gap-5">
            <Link href="/privacy" className="text-sm text-muted/60 hover:text-muted transition-colors">
              개인정보처리방침
            </Link>
            <p className="text-sm text-muted/60">© 2026 NexBot. All rights reserved.</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
