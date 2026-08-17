"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Shield, Gem, Radio, BadgeCheck,
  ArrowRight, Megaphone, X,
} from "lucide-react";
import {
  TerminalMockup, PointsMockup, ChzzkEmbedMockup, ChzzkFollowVerifyMockup,
} from "@/components/marketing/Mockups";

const BOT_CLIENT_ID = process.env.NEXT_PUBLIC_DISCORD_CLIENT_ID || "YOUR_CLIENT_ID";
const INVITE_URL    = `https://discord.com/oauth2/authorize?client_id=${BOT_CLIENT_ID}&permissions=8&scope=bot%20applications.commands`;

import SiteHeader from "@/components/SiteHeader";
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

// 프로필 드롭다운은 `components/SiteHeader`의 `AuthArea`로 옮겼다.
// 여기 남겨 두면 로그인 메뉴가 두 벌이 되어, 한쪽에만 '설정'을 추가하는 식으로
// 조용히 갈라진다(예전 헤더 복제와 같은 문제다).

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
      {/* 헤더는 `components/SiteHeader`가 유일한 구현이다. 예전에는 이 자리에
          같은 마크업이 페이지마다 복제돼 있었다(로고 hit area 하나를 고치는 데
          12파일을 만져야 했다). 로그인·프로필 상태도 그쪽이 스스로 읽는다. */}
      <SiteHeader maxWidth="6xl" />

      {/* ── Hero ── */}
      <section
        className="relative flex flex-col items-center justify-center
                   min-h-[calc(100vh-60px)] px-5 text-center overflow-hidden bg-bg"
      >
        {/* 격자 배경은 걷어냈다(2026-08-14) — 화면에 선이 그대로 드러나 보였다.
            배경은 section의 `bg-bg` + 아래 radial glow만으로 만든다.
            자세한 경위는 `app/globals.css`의 'Hero grid — 제거됨' 주석. */}

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
            {/* 보조 버튼 2개는 테두리에 은은한 발광(nb-neon-glow)을 상시로 준다.
                주 CTA는 accent 채움이라 이 정도로는 가려지지 않는다. */}
            <Link
              href="/stats"
              className="nb-neon-border nb-neon-glow flex items-center gap-2 px-6 py-3
                         border border-border hover:border-accent/40 text-fg rounded-xl
                         transition-colors hover:bg-bg-hover font-medium"
            >
              <Radio size={16} style={{ color: "#00FFA3" }} /> 치지직 통계
            </Link>
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
            {/* 통계는 치지직 네온(기본값), 초대는 브랜드 색 — 목적지가 다름을 색으로 구분 */}
            <a
              href={INVITE_URL}
              target="_blank" rel="noreferrer"
              className="nb-neon-border nb-neon-glow flex items-center gap-2 px-6 py-3
                         border border-border hover:border-accent/40 text-fg rounded-xl
                         transition-colors hover:bg-bg-hover font-medium"
              style={{ "--nb-glow-from": "#38BDF8",
                       "--nb-glow-to": "#C084FC" } as React.CSSProperties}
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
        {/* 390px에서 3열 × gap-8 + text-4xl 숫자가 뷰포트를 52px 넘겨 가로 스크롤이
            생겼다(실측 scrollWidth 437 > clientWidth 385). 좁은 화면에서만 간격과
            숫자 크기를 낮춘다 — 640px 이상은 기존 그대로다.

            그래도 **340px 미만·확대 150%(CSS 뷰포트 260px)에서는 3열이 불가능하다.**
            숫자가 "3,935,120"처럼 길어 어떤 글꼴 크기로도 세 칸이 들어가지 않는다
            (실측: 페이지가 31px 넘쳤고, 원인은 이 타일의 숫자였다). 글자를 더 줄이는
            대신 열을 접는다 — WCAG reflow가 요구하는 방향이고 정보도 그대로 남는다.
            grid item의 기본 `min-width:auto`도 함께 풀어 준다. */}
        <div className="max-w-4xl mx-auto px-5 py-12 grid grid-cols-1 min-[340px]:grid-cols-3 gap-3 sm:gap-8 text-center">
          {statsDisplay.map(({ value, label }, i) => (
            <div key={label} className={`min-w-0 reveal reveal-delay-${i + 1}`}>
              <p className={`text-2xl sm:text-4xl font-bold mb-1 ${
                value
                  ? "bg-gradient-to-r from-[#5865f2] to-[#818cf8] bg-clip-text text-transparent"
                  : "text-muted/30 animate-pulse"
              }`}>
                {value ?? "—"}
              </p>
              <p className="text-xs sm:text-sm text-muted break-keep">{label}</p>
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

            {/* 확대 150%(CSS 뷰포트 260px)에서 목업 카드가 301px를 그대로 차지해
                좌우로 삐져나갔다(실측 left=-20 / right=280).
                부모가 `flex-col items-center`라 cross axis(가로)에서 자식이 stretch되지
                않아, 카드의 `w-full`이 참조할 폭이 없어 콘텐츠 폭이 된다. `w-full`을
                여기서 명시해 컨테이너 폭을 따르게 하고, `min-w-0`으로 축소도 허용한다. */}
            <div className="w-full min-w-0 flex-1 flex justify-center lg:justify-end">
              {mockup}
            </div>
          </div>
        ))}
      </section>

    </div>
  );
}
