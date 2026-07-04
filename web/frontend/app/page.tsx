"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import {
  Bot, Shield, Gem, Radio, BadgeCheck,
  LogOut, ChevronRight, ArrowRight, Hash, Megaphone, X,
} from "lucide-react";

const BOT_CLIENT_ID = process.env.NEXT_PUBLIC_DISCORD_CLIENT_ID || "YOUR_CLIENT_ID";
const INVITE_URL    = `https://discord.com/oauth2/authorize?client_id=${BOT_CLIENT_ID}&permissions=8&scope=bot%20applications.commands`;

import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";
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
        <span className="text-sm text-fg hidden sm:block">{user.global_name || user.username}</span>
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
    { cmd: "/warn",  args: "@유저 욕설 사용",  label: "경고", labelColor: "#FEE75C" },
    { cmd: "/mute",  args: "@유저 30m 도배",   label: "뮤트", labelColor: "#EB459E" },
    { cmd: "/clear", args: "100",              label: "완료", labelColor: "#57F287" },
    { cmd: "/ban",   args: "@유저 반복 위반",   label: "차단", labelColor: "#ED4245" },
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
        {cmds.map(({ cmd, args, label, labelColor }) => (
          <div key={cmd + args} className="flex items-center gap-2">
            <span className="text-[#5865f2]/40 text-xs select-none">$</span>
            <span className="text-[#818cf8]">{cmd}</span>
            <span className="text-white/40">{args}</span>
            <span className="ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded"
                  style={{ color: labelColor, background: `${labelColor}18` }}>
              {label}
            </span>
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

// Mockup: points shop
function PointsMockup({ color = "#A855F7" }: { color?: string }) {
  const items = [
    { icon: "🎮", name: "게임 아이템",    points: 500,  stock: 3   },
    { icon: "🎫", name: "특별 역할 쿠폰", points: 1000, stock: "∞" },
    { icon: "✨", name: "닉네임 꾸미기",  points: 300,  stock: 10  },
  ];
  return (
    <div className="bg-bg-card rounded-2xl border border-border p-5 shadow-xl w-full max-w-sm space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-bold text-muted uppercase tracking-widest">포인트 상점</p>
        <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
              style={{ color, background: `${color}18` }}>
          1,250P 보유
        </span>
      </div>
      <div className="space-y-3">
        {items.map(({ icon, name, points, stock }) => (
          <div key={name} className="flex items-center gap-3 p-3 rounded-xl border border-border bg-bg">
            <span className="text-2xl leading-none">{icon}</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-fg truncate">{name}</p>
              <p className="text-[11px] text-muted">잔여 {stock}개</p>
            </div>
            <button
              className="text-[11px] font-semibold px-2.5 py-1 rounded-lg shrink-0 cursor-default"
              style={{ background: `${color}18`, color }}
            >
              {typeof points === "number" ? points.toLocaleString() : points}P
            </button>
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
        <span className="ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded"
              style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>
          LIVE
        </span>
      </div>
      <div className="p-4">
        <div className="flex gap-3">
          <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center"
               style={{ background: "rgba(3,199,90,0.15)" }}>
            <Radio size={16} style={{ color: "#03C75A" }} />
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
              <div className="rounded-md h-14 flex items-center justify-center gap-2"
                   style={{ background: "#1e1f22" }}>
                <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "#03C75A" }} />
                <span className="text-[11px] font-semibold" style={{ color: "#03C75A" }}>방송 중</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Mockup: chzzk follow verification
function ChzzkFollowVerifyMockup({ color = "#03C75A" }: { color?: string }) {
  const tiers = [
    { months: 1,  role: "팔로워",       granted: true },
    { months: 3,  role: "서포터",        granted: true },
    { months: 6,  role: "충성팬",        granted: false },
    { months: 12, role: "레전드 팬",     granted: false },
  ];
  return (
    <div className="bg-bg-card rounded-2xl border border-border p-5 shadow-xl w-full max-w-sm space-y-4">
      <div className="flex items-center gap-2.5 pb-3 border-b border-border">
        <div className="w-8 h-8 rounded-full flex items-center justify-center"
             style={{ background: `${color}20` }}>
          <BadgeCheck size={16} style={{ color }} />
        </div>
        <div>
          <p className="text-xs font-bold text-fg">치지직 팔로우 인증</p>
          <p className="text-[11px] text-muted">팔로우 기간: <span style={{ color }} className="font-semibold">4개월</span></p>
        </div>
      </div>
      <div className="space-y-2">
        {tiers.map(({ months, role, granted }) => (
          <div key={months}
               className="flex items-center gap-3 px-3 py-2 rounded-xl border"
               style={granted ? { background: `${color}10`, borderColor: `${color}30` } : { borderColor: "var(--color-border)" }}>
            <span className="text-[11px] font-semibold w-14 shrink-0"
                  style={granted ? { color } : { color: "var(--color-muted)" }}>
              {months}개월+
            </span>
            <span className="text-sm text-fg flex-1">@{role}</span>
            <div className="w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0"
                 style={granted ? { background: color, borderColor: color } : { borderColor: "var(--color-border)" }}>
              {granted && <span className="text-white text-[9px] font-bold leading-none">✓</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── 공지 배너 ────────────────────────────────────────────────────────────────
const DISMISSED_KEY = "dismissed_announcement";

function AnnouncementBanner() {
  const [message, setMessage] = useState("");
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    api.stats.announcement()
      .then((d) => {
        const msg = d.message?.trim();
        if (!msg) return;
        const dismissed = localStorage.getItem(DISMISSED_KEY);
        if (dismissed === msg) return;
        setMessage(msg);
        setVisible(true);
      })
      .catch(() => {});
  }, []);

  const dismiss = () => {
    localStorage.setItem(DISMISSED_KEY, message);
    setVisible(false);
  };

  if (!visible) return null;

  return (
    <div className="bg-accent text-white">
      <div className="max-w-6xl mx-auto px-5 py-2.5 flex items-center gap-3 text-sm">
        <Megaphone size={15} className="shrink-0" />
        <p className="flex-1 min-w-0 truncate">
          <span className="font-semibold">공지:</span> {message}
        </p>
        <button
          onClick={dismiss}
          aria-label="공지 닫기"
          className="shrink-0 p-1 rounded hover:bg-white/15 transition-colors"
        >
          <X size={15} />
        </button>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function HomePage() {
  const [user, setUser] = useState<User | null>(null);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);
  const [stats, setStats] = useState<{ guilds: number; chzzk_subscriptions: number; today_visitors: number } | null>(null);

  const typed = useTypewriter(["함께.", "연결하다.", "소통하다."]);
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

    api.stats.visit().catch(() => {});
  }, []);

  const features: {
    color: string;
    bg: string;
    icon: React.ReactNode;
    tag: string;
    title: string;
    detail: string;
    bullets: string[];
    mockup: React.ReactNode;
    flip: boolean;
  }[] = [
    {
      color:  "#ED4245",
      bg:     "rgba(237,66,69,0.12)",
      icon:   <Shield size={22} style={{ color: "#ED4245" }} />,
      tag:    "서버 보안",
      title:  "강력한 서버 관리",
      detail: "경고·뮤트·차단·메시지 삭제를 슬래시 커맨드 하나로 빠르게 처리하세요. 웹 대시보드에서 역할 권한과 자동 제재 설정을 한눈에 관리할 수 있습니다.",
      bullets: ["경고·뮤트·차단·킥", "자동 불량 단어 감지", "채널별 로그 기록"],
      mockup: <TerminalMockup />,
      flip:   false,
    },
    {
      color:  "#A855F7",
      bg:     "rgba(168,85,247,0.12)",
      icon:   <Gem size={22} style={{ color: "#A855F7" }} />,
      tag:    "포인트 시스템",
      title:  "포인트 & 상점",
      detail: "채팅 활동·애정도 레벨업·미션 완료 등 다양한 방법으로 포인트를 적립하세요. 관리자가 설정한 상점 아이템을 포인트로 교환하고, 미션 제출·승인 시스템으로 커뮤니티 참여를 활성화하세요.",
      bullets: ["채팅·애정도 레벨업 자동 포인트 적립", "미션 제출 & 관리자 승인", "포인트 상점 아이템 교환"],
      mockup: <PointsMockup color="#A855F7" />,
      flip:   true,
    },
    {
      color:  "#03C75A",
      bg:     "rgba(3,199,90,0.12)",
      icon:   <Radio size={22} style={{ color: "#03C75A" }} />,
      tag:    "실시간 알림",
      title:  "치지직 방송 알림",
      detail: "치지직 스트리머의 방송 시작을 실시간으로 감지해 Discord 채널에 자동 알림을 보냅니다. 방송 제목·카테고리·썸네일이 담긴 임베드 메시지로 팬들을 모아보세요.",
      bullets: ["방송 시작 실시간 감지", "임베드 알림 + 멘션 지원", "치지직 계정 OAuth 연동으로 간편 설정"],
      mockup: <ChzzkEmbedMockup />,
      flip:   false,
    },
    {
      color:  "#818CF8",
      bg:     "rgba(129,140,248,0.12)",
      icon:   <BadgeCheck size={22} style={{ color: "#818CF8" }} />,
      tag:    "팔로우 인증",
      title:  "치지직 팔로우 역할 시스템",
      detail: "치지직 OAuth로 로그인하면 팔로우 시작일을 기준으로 팔로우 기간을 자동 계산합니다. 1개월, 3개월, 6개월 등 최대 5개의 기간 티어를 설정해 조건에 맞는 Discord 역할을 자동으로 부여하세요.",
      bullets: ["팔로우 날짜 기반 자동 계산", "최대 5개 기간 티어 설정", "웹 대시보드에서 역할 관리"],
      mockup: <ChzzkFollowVerifyMockup color="#818CF8" />,
      flip:   true,
    },
  ];

  const statsDisplay = [
    { value: stats ? `${stats.guilds}` : null,                label: "등록 서버" },
    { value: stats ? `${stats.chzzk_subscriptions}` : null,  label: "치지직 구독" },
    { value: stats ? `${stats.today_visitors}` : null,         label: "오늘 방문자" },
  ];

  return (
    <div className="min-h-screen bg-bg text-fg">
      <AnnouncementBanner />

      {/* ── Navbar ── */}
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-6xl mx-auto px-5 flex items-center justify-between" style={{ height: 60 }}>
          <Link href="/" className="flex items-center gap-2 font-bold text-[17px] text-fg">
            <Bot size={20} className="text-accent" />
            NexBot
          </Link>
          <div className="flex items-center gap-3">
            <Link
              href="/guide"
              className="hidden sm:block text-sm text-muted hover:text-fg transition-colors mr-1"
            >
              사용 방법
            </Link>
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
      <section
        className="relative flex flex-col items-center justify-center
                   min-h-[calc(100vh-60px)] px-5 text-center overflow-hidden bg-bg"
      >
        {/* Subtle grid — adaptive opacity for dark/light */}
        <div className="absolute inset-0 pointer-events-none select-none hero-grid" />

        {/* Faint glow — centered, very low opacity */}
        <div
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2
                     w-[900px] h-[500px] rounded-full pointer-events-none"
          style={{ background: "radial-gradient(ellipse, rgba(56,189,248,0.06) 0%, rgba(192,132,252,0.04) 45%, transparent 70%)" }}
        />

        <div className="relative z-10 max-w-3xl mx-auto">
          <h1 className="text-5xl sm:text-6xl lg:text-7xl font-bold leading-tight tracking-tight mb-6
                         animate-fade-up">
            방송인과 시청자를<br />
            <span className="bg-gradient-to-r from-[#38BDF8] via-[#818cf8] to-[#C084FC]
                             bg-clip-text text-transparent">
              {typed}
              <span className="inline-block w-0.5 h-[0.85em] bg-[#818cf8]/80 ml-0.5 align-middle animate-pulse" />
            </span>
          </h1>

          <p
            className="text-lg text-muted max-w-xl mx-auto mb-10 leading-relaxed animate-fade-up"
            style={{ animationDelay: "0.1s" }}
          >
            치지직 방송 알림부터 시청자 참여 관리까지.<br />
            방송인과 시청자가 함께하는 Discord 서버를 만들어보세요.
          </p>

          <div
            className="flex flex-wrap items-center justify-center gap-3 animate-fade-up"
            style={{ animationDelay: "0.2s" }}
          >
            {user
              ? <Link href="/dashboard"
                      className="flex items-center gap-2 px-6 py-3 bg-accent hover:bg-accent-hover
                                 text-white font-semibold rounded-xl transition-colors shadow-lg shadow-accent/25">
                  대시보드 열기 <ArrowRight size={16} />
                </Link>
              : loginUrl
                ? <a href={loginUrl}
                     className="flex items-center gap-2 px-6 py-3 bg-accent hover:bg-accent-hover
                                text-white font-semibold rounded-xl transition-colors shadow-lg shadow-accent/25">
                    Discord로 시작하기 <ArrowRight size={16} />
                  </a>
                : <div className="flex items-center gap-2 px-6 py-3 bg-accent/30 text-white/40
                                  font-semibold rounded-xl cursor-wait select-none">
                    Discord로 시작하기 <ArrowRight size={16} />
                  </div>}
            <a
              href={INVITE_URL}
              target="_blank" rel="noreferrer"
              className="flex items-center gap-2 px-6 py-3 border border-border
                         hover:border-accent/40 text-fg rounded-xl transition-colors
                         hover:bg-bg-hover font-medium"
            >
              봇 초대하기
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
        <div className="text-center reveal">
          <p className="text-xl font-semibold text-accent uppercase tracking-widest mb-3">기능</p>
          <h2 className="text-3xl sm:text-4xl font-bold text-fg mb-4">하나의 봇으로 모든 것을</h2>
          <p className="text-muted max-w-lg mx-auto text-[15px]">
            서버 관리부터 방송 알림까지, NexBot 하나로 해결하세요.
          </p>
        </div>

        {features.map(({ color, bg, icon, tag, title, detail, bullets, mockup, flip }) => (
          <div
            key={title}
            className={`reveal flex flex-col ${
              flip ? "lg:flex-row-reverse" : "lg:flex-row"
            } items-center gap-12 lg:gap-20`}
          >
            <div className="flex-1 max-w-lg">
              {/* Tag */}
              <div className="flex items-center gap-3 mb-5">
                <div className="w-11 h-11 rounded-2xl flex items-center justify-center" style={{ background: bg }}>
                  {icon}
                </div>
                <span className="text-sm font-bold uppercase tracking-widest px-3 py-1 rounded-full"
                      style={{ color, background: bg }}>
                  {tag}
                </span>
              </div>

              <h3 className="text-2xl sm:text-3xl font-bold text-fg mb-4">{title}</h3>
              <p className="text-muted leading-relaxed text-[15px] mb-6">{detail}</p>

              {/* Bullet points */}
              <ul className="space-y-2.5">
                {bullets.map(b => (
                  <li key={b} className="flex items-center gap-3 text-base text-fg/80">
                    <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: color }} />
                    {b}
                  </li>
                ))}
              </ul>
            </div>

            <div className="flex-1 flex justify-center lg:justify-end">
              {mockup}
            </div>
          </div>
        ))}
      </section>

      <Footer />
    </div>
  );
}
