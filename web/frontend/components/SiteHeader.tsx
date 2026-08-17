"use client";
/**
 * 사이트 공통 헤더 — 햄버거 · 브랜드 · 전역 검색 · 메뉴 · 로그인.
 *
 * **이 파일이 공개 페이지 헤더의 유일한 구현이다.** 예전에는 같은 헤더가 11개
 * 페이지에 각자 복제돼 있어서, 한 곳을 고치면 나머지 열 곳이 남았다(UI-S 회차에
 * 로고 hit area 하나를 고치는 데 12파일을 만져야 했던 이유다).
 *
 * 레이아웃 계약 — 한 줄이고, **줄어드는 칸은 검색뿐이다**:
 *   [☰] [로고] [검색 — flex-1 min-w-0] [메뉴(lg+)] [로그인]
 * · 로고·햄버거·로그인은 `shrink-0`이라 절대 찌그러지지 않는다.
 * · 검색만 `min-w-0`으로 줄어든다 → 320px과 150% 확대에서도 가로 overflow가 없다.
 * · 브랜드 텍스트는 `sm` 미만에서 `sr-only`로 내린다. 접근 가능한 이름("NexBot")은
 *   그대로 남고 hit area(`.nb-brand-tap`, 44×44)도 유지된다.
 *
 * 접근성 계약:
 * · 햄버거 `aria-expanded`/`aria-controls`, ESC 닫기, 닫을 때 버튼으로 포커스 복귀
 * · 검색은 combobox/listbox 의미를 갖고 ↑/↓/Enter/ESC로 조작된다
 * · 드롭다운 항목은 `role="option"` + `aria-selected`
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import Image from "next/image";
import {
  Bot, Menu, X, Search, Radio, Loader2, ArrowRight, LogOut, ChevronRight,
  BookOpen, MessageCircleQuestion, Settings,
} from "lucide-react";
import { api } from "@/lib/api";
import type { QuickSearchItem, User } from "@/lib/types";

/** 햄버거 안에 들어가는 보조 메뉴. 헤더 막대에는 자리가 없고, 여기 모아 두면
 *  좁은 화면에서도 같은 항목에 도달할 수 있다. */
const DRAWER_LINKS: { href: string; label: string; icon: React.ReactNode }[] = [
  { href: "/stats",       label: "치지직 통계",   icon: <Radio size={16} /> },
  { href: "/stats/guide", label: "통계 안내",     icon: <BookOpen size={16} /> },
  { href: "/guide",       label: "사용 방법",     icon: <BookOpen size={16} /> },
  { href: "/faq",         label: "자주 묻는 질문", icon: <MessageCircleQuestion size={16} /> },
  { href: "/about",       label: "서비스 소개",   icon: <Bot size={16} /> },
  { href: "/status",      label: "서버 상태",     icon: <Radio size={16} /> },
];

/** 헤더 막대에 직접 노출하는 메뉴. `치지직 통계` **오른쪽**에 Beta 배지가 붙는다
 *  — 페이지 제목 옆의 배지와 중복되지 않도록 출처를 여기 하나로 모았다. */
function HeaderNav() {
  return (
    <nav aria-label="주요 메뉴" className="hidden items-center gap-1 lg:flex">
      <Link href="/stats"
            className="nb-tap inline-flex items-center gap-1.5 rounded-lg px-2.5 py-2
                       text-sm font-medium text-muted transition-colors hover:text-fg">
        <Radio size={14} style={{ color: "#00FFA3" }} aria-hidden="true" />
        치지직 통계
        <span className="rounded border border-accent/40 px-1 py-px text-[10px]
                         font-bold leading-none text-accent">
          Beta
        </span>
      </Link>
      <Link href="/guide"
            className="nb-tap inline-flex items-center rounded-lg px-2.5 py-2 text-sm
                       text-muted transition-colors hover:text-fg">
        사용 방법
      </Link>
    </nav>
  );
}

// ── 전역 검색 ───────────────────────────────────────────────────────────────

function GlobalSearch({ compact = false }: { compact?: boolean }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [items, setItems] = useState<QuickSearchItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState(-1);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // 늦게 도착한 이전 요청이 최신 결과를 덮어쓰지 않게 하는 가드.
  const reqId = useRef(0);
  const listId = useId();

  useEffect(() => {
    const kw = q.trim();
    if (kw.length < 1) { setItems([]); setOpen(false); setLoading(false); setErr(null); return; }
    setOpen(true); setLoading(true); setErr(null);
    // 디바운스 — 타이핑마다 요청하면 헤더가 사이트 전역이라 부하가 곱해진다.
    const t = setTimeout(() => {
      const my = ++reqId.current;
      api.rising.quickSearch(kw)
        .then((r) => { if (my === reqId.current) { setItems(r.results || []); setActive(-1); } })
        .catch((e) => {
          if (my !== reqId.current) return;
          setItems([]);
          setErr(e instanceof Error ? e.message : "검색에 실패했습니다.");
        })
        .finally(() => { if (my === reqId.current) setLoading(false); });
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const go = useCallback((it: QuickSearchItem) => {
    setOpen(false); setQ("");
    router.push(`/stats/streamer/${it.channel_id}`);
  }, [router]);

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      // 헤더 밖으로 전파하지 않는다 — 드로어가 함께 닫히면 조작이 어긋난다.
      if (open) { e.stopPropagation(); setOpen(false); }
      return;
    }
    if (!open || items.length === 0) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => (i + 1) % items.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => (i <= 0 ? items.length : i) - 1); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); go(items[active]); }
  };

  const showPanel = open && q.trim().length > 0;

  return (
    <div ref={boxRef}
         className={`relative min-w-0 flex-1 ${compact ? "" : "max-w-md"}`}>
      <div className="relative">
        <Search size={15} aria-hidden="true"
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onFocus={() => { if (q.trim()) setOpen(true); }}
          onKeyDown={onKey}
          type="search"
          // 서버가 정한 상한과 맞춘다(초과분은 서버가 어차피 거절한다).
          maxLength={40}
          placeholder="스트리머 검색"
          aria-label="스트리머 검색"
          role="combobox"
          aria-expanded={showPanel}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          className="w-full min-w-0 rounded-lg border border-border bg-bg py-2 pl-9 pr-3
                     text-sm text-fg placeholder-muted transition-colors
                     focus:border-accent focus:outline-none"
        />
      </div>

      {showPanel && (
        <div className="absolute left-0 right-0 top-full z-[60] mt-1 max-h-80 overflow-y-auto
                        rounded-xl border border-border bg-bg-card py-1.5 shadow-xl">
          {/* 세 상태를 서로 구분한다: 로딩 / 오류 / 결과 없음.
              하나로 뭉치면 실패가 로딩으로 보인다. */}
          {loading ? (
            <p className="flex items-center gap-2 px-3 py-2.5 text-sm text-muted" aria-busy="true">
              <Loader2 size={14} className="animate-spin" aria-hidden="true" /> 검색 중…
            </p>
          ) : err ? (
            <p role="alert" className="px-3 py-2.5 text-sm text-red-400">{err}</p>
          ) : items.length === 0 ? (
            <p className="px-3 py-2.5 text-sm text-muted">
              검색 결과가 없습니다.
              <span className="mt-0.5 block text-xs text-muted/70">
                채널명 일부만 입력해 보세요. 아직 수집되지 않은 채널일 수 있습니다.
              </span>
            </p>
          ) : (
            <ul id={listId} role="listbox" aria-label="검색 결과">
              {items.map((it, i) => (
                <li key={it.channel_id} id={`${listId}-${i}`} role="option"
                    aria-selected={i === active}>
                  <button type="button"
                          onMouseEnter={() => setActive(i)}
                          onClick={() => go(it)}
                          className={`nb-tap flex w-full items-center gap-2 px-3 py-2 text-left
                                      transition-colors ${i === active ? "bg-bg-hover" : ""}`}>
                    <span className="h-6 w-6 shrink-0 overflow-hidden rounded-full bg-bg-hover">
                      {it.channel_image_url
                        // eslint-disable-next-line @next/next/no-img-element
                        ? <img src={it.channel_image_url} alt="" width={24} height={24}
                               loading="lazy" className="h-full w-full object-cover" />
                        : null}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-fg">
                      {it.channel_name || "(이름 미상)"}
                    </span>
                    {/* 상태를 색만으로 말하지 않는다 — 글자로도 적는다 */}
                    {it.open_live && (
                      <span className="shrink-0 text-[10px] font-bold" style={{ color: "#FF4D4D" }}>
                        LIVE
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// ── 로그인 / 프로필 ─────────────────────────────────────────────────────────

function AuthArea() {
  const [user, setUser] = useState<User | null>(null);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem("discord_user");
      if (raw) setUser(JSON.parse(raw));
    } catch { /* 손상된 값이면 로그아웃 상태로 둔다 */ }
    api.auth.getLoginUrl().then((d) => setLoginUrl(d.url)).catch(() => {});
  }, []);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("discord_user");
    window.location.href = "/";
  };

  if (!user) {
    // 로그인 URL을 아직 못 받았어도 **자리를 비우지 않는다.** 비워 두면 응답이
    // 도착하는 순간 헤더가 흔들린다(레이아웃 시프트).
    return (
      <a href={loginUrl ?? "/login"}
         aria-disabled={!loginUrl}
         className="nb-tap inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-accent
                    px-3 py-2 text-sm font-medium text-white transition-colors
                    hover:bg-accent-hover sm:px-4">
        로그인 <ArrowRight size={13} aria-hidden="true" />
      </a>
    );
  }

  return (
    <div className="relative shrink-0" ref={ref}>
      <button onClick={() => setOpen((o) => !o)}
              aria-expanded={open} aria-haspopup="menu"
              aria-label={`${user.global_name || user.username} 계정 메뉴`}
              className="nb-tap flex items-center gap-2 rounded-full border border-transparent
                         px-2 py-1.5 transition-all hover:border-border hover:bg-bg-hover sm:px-3">
        {user.avatar
          ? <Image src={user.avatar} alt="" width={28} height={28} className="rounded-full" />
          : <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent/20">
              <Bot size={13} className="text-accent" aria-hidden="true" />
            </span>}
        <span className="hidden max-w-[8rem] truncate text-sm text-fg sm:block">
          {user.global_name || user.username}
        </span>
        <ChevronRight size={13} aria-hidden="true"
                      className={`text-muted transition-transform ${open ? "rotate-90" : ""}`} />
      </button>

      {open && (
        <div role="menu"
             className="absolute right-0 top-full z-[60] mt-2 w-48 rounded-xl border
                        border-border bg-bg-card py-1.5 shadow-2xl shadow-black/40">
          <Link href="/dashboard" role="menuitem" onClick={() => setOpen(false)}
                className="nb-tap flex items-center gap-2.5 px-4 py-2.5 text-sm text-fg
                           transition-colors hover:bg-bg-hover">
            <Bot size={14} className="text-accent" aria-hidden="true" /> 대시보드
          </Link>
          <Link href="/settings" role="menuitem" onClick={() => setOpen(false)}
                className="nb-tap flex items-center gap-2.5 px-4 py-2.5 text-sm text-fg
                           transition-colors hover:bg-bg-hover">
            <Settings size={14} className="text-muted" aria-hidden="true" /> 설정
          </Link>
          <div className="mx-3 my-1 h-px bg-border" />
          {/* 로그아웃과 회원탈퇴는 **시각·기능적으로 분리한다.**
              탈퇴는 설정 페이지 맨 아래에만 둔다 — 여기 나란히 두면 오조작이 난다. */}
          <button onClick={logout} role="menuitem"
                  className="nb-tap flex w-full items-center gap-2.5 px-4 py-2.5 text-sm
                             text-danger transition-colors hover:bg-danger/8">
            <LogOut size={14} aria-hidden="true" /> 로그아웃
          </button>
        </div>
      )}
    </div>
  );
}

// ── 헤더 ────────────────────────────────────────────────────────────────────

/** 헤더 안쪽 폭 — **각 페이지의 `<main>`과 같은 값을 쓴다.** 다르면 로고와 본문의
 *  왼쪽 정렬이 어긋나 페이지마다 헤더가 미세하게 다른 위치에 놓인다. */
const INNER: Record<string, string> = {
  full: "w-full px-4 md:px-6",
  "6xl": "mx-auto w-full max-w-6xl px-4 sm:px-5",
  "4xl": "mx-auto w-full max-w-4xl px-4 sm:px-5",
  "3xl": "mx-auto w-full max-w-3xl px-4 sm:px-5",
};

export default function SiteHeader({ breadcrumb, maxWidth = "full" }: {
  /** 로고 오른쪽에 붙는 현재 위치 표시(예: `/stats`의 "치지직 통계"). */
  breadcrumb?: React.ReactNode;
  /** 본문 폭에 맞춘다. 그 페이지 `<main>`의 `max-w-*`와 같은 값을 넘길 것. */
  maxWidth?: "full" | "6xl" | "4xl" | "3xl";
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  const burgerRef = useRef<HTMLButtonElement>(null);
  const drawerId = useId();

  // ESC 닫기 + 포커스 순환 + 닫을 때 햄버거로 복귀.
  useEffect(() => {
    if (!menuOpen) return;
    // ref 값을 effect 안에서 잡아 둔다 — cleanup이 도는 시점에는 `.current`가
    // 이미 다른 노드(또는 null)일 수 있어 포커스 복귀가 조용히 실패한다.
    const burger = burgerRef.current;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setMenuOpen(false); return; }
      if (e.key !== "Tab") return;
      const nodes = drawerRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";   // 뒤 화면이 같이 움직이면 조작이 어긋난다
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
      burger?.focus();
    };
  }, [menuOpen]);

  const inner = INNER[maxWidth] ?? INNER.full;

  return (
    <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
      {/* 최소 높이만 정하고 고정하지 않는다 — 확대(150%)에서 글자가 커져도
          내용이 잘리지 않고 헤더가 함께 자란다. */}
      <div className={`nb-tap-gap flex min-h-[60px] items-center gap-2 py-2 ${inner}`}>
        <button ref={burgerRef} type="button"
                onClick={() => setMenuOpen((o) => !o)}
                aria-expanded={menuOpen}
                aria-controls={drawerId}
                aria-label={menuOpen ? "메뉴 닫기" : "메뉴 열기"}
                className="nb-tap-icon inline-flex h-11 w-11 shrink-0 items-center justify-center
                           rounded-lg text-muted transition-colors hover:bg-bg-hover hover:text-fg">
          {menuOpen ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
        </button>

        <Link href="/"
              className="nb-brand-tap shrink-0 gap-2 font-bold text-[17px] text-fg">
          <Bot size={20} className="text-accent" aria-hidden="true" />
          {/* 좁은 화면에서는 글자를 감추되 **이름은 남긴다** — 링크의 접근 가능한
              이름이 사라지면 스크린리더에 "링크"로만 읽힌다. */}
          <span className="ml-1.5 hidden sm:inline">NexBot</span>
          <span className="sr-only sm:hidden">NexBot</span>
        </Link>

        {breadcrumb && (
          <span className="hidden min-w-0 shrink items-center gap-2 md:flex">
            <span className="text-border" aria-hidden="true">/</span>
            <span className="min-w-0 truncate">{breadcrumb}</span>
          </span>
        )}

        {/* 가운데 — **유일하게 줄어드는 칸**이다 */}
        <GlobalSearch />

        <HeaderNav />

        {/* 실제 오른쪽 끝 */}
        <AuthArea />
      </div>

      {/* 보조 메뉴 — 모바일에서는 drawer, 데스크톱에서도 같은 목록을 쓴다.
          목록을 화면 크기별로 다르게 두면 "PC에서 본 항목이 폰에 없다"가 된다. */}
      {menuOpen && (
        <>
          <div className="fixed inset-0 top-0 z-40 bg-black/50"
               onMouseDown={() => setMenuOpen(false)} aria-hidden="true" />
          <div ref={drawerRef} id={drawerId} role="dialog" aria-modal="true"
               aria-label="보조 메뉴"
               className="absolute left-0 right-0 top-full z-50 border-b border-border
                          bg-bg-card shadow-2xl">
            <div className={`py-3 ${inner}`}>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-muted/70">
                  메뉴
                </span>
                <button type="button" onClick={() => setMenuOpen(false)}
                        aria-label="메뉴 닫기"
                        className="nb-tap-icon inline-flex h-9 w-9 items-center justify-center
                                   rounded-lg text-muted hover:bg-bg-hover hover:text-fg">
                  <X size={16} aria-hidden="true" />
                </button>
              </div>
              <ul className="nb-tap-gap grid grid-cols-1 gap-1 sm:grid-cols-2">
                {DRAWER_LINKS.map((l) => (
                  <li key={l.href}>
                    <Link href={l.href} onClick={() => setMenuOpen(false)}
                          className="nb-tap flex items-center gap-2.5 rounded-lg px-3 py-2.5
                                     text-sm font-medium text-fg transition-colors
                                     hover:bg-bg-hover">
                      <span className="shrink-0 text-muted" aria-hidden="true">{l.icon}</span>
                      <span className="min-w-0 truncate">{l.label}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </>
      )}
    </header>
  );
}
