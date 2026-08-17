"use client";
/**
 * 사이트 공통 헤더 — 햄버거 · 브랜드 · 전역 검색 · 메뉴 · 로그인.
 *
 * **이 파일이 공개 페이지 헤더의 유일한 구현이다.** 예전에는 같은 헤더가 11개
 * 페이지에 각자 복제돼 있어서, 한 곳을 고치면 나머지 열 곳이 남았다(UI-S 회차에
 * 로고 hit area 하나를 고치는 데 12파일을 만져야 했던 이유다).
 *
 * 레이아웃 계약 — **3영역 grid**다:
 *   [☰ NexBot 치지직통계Beta] [검색] [사용 방법 · 로그인/프로필]
 * · 좌우를 `1fr`로 같게 잡아야 가운데가 "남은 공간의 중심"이 아니라
 *   **viewport의 중심**에 온다(실측: 전 뷰포트 offCenter 0).
 * · `md` 미만에서는 `auto 1fr auto`로 바꾸고 검색을 접는다 — 260px에서 세 칸을
 *   모두 펼치면 좌우가 0px까지 눌려 브랜드와 로그인이 겹쳤다.
 * · 그 폭에서는 로그인 글자도 감추고 아이콘만 남긴다(hit area 44×44는 유지).
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
  Sparkles, Trophy, ListOrdered, Gamepad2,
  BookOpen, MessageCircleQuestion, Settings,
} from "lucide-react";
import { api } from "@/lib/api";
import type { QuickSearchItem, User } from "@/lib/types";

/** 햄버거 drawer 내용 — **통계 sidebar와 같은 메뉴**다.
 *
 *  sidebar가 있는 `/stats`에서는 햄버거가 그 sidebar를 접고 펴고, sidebar가 없는
 *  페이지(홈·수정 요청 등)에서는 같은 목록을 여기 drawer로 보여 준다. 두 곳의
 *  항목이 다르면 "PC에서 본 메뉴가 폰에 없다"가 되므로 `/stats` 좌측 메뉴의
 *  최상위 묶음(봉누도·싱드컵·통계·랭킹·카테고리·통계 안내)을 그대로 따른다. */
const DRAWER_LINKS: { href: string; label: string; icon: React.ReactNode }[] = [
  { href: "/stats?tab=bongnudo",  label: "봉누도",       icon: <Sparkles size={16} /> },
  { href: "/stats?tab=singcup",   label: "싱드컵",       icon: <Trophy size={16} /> },
  { href: "/stats?tab=overview",  label: "통계",         icon: <Radio size={16} /> },
  { href: "/stats?tab=ranking",   label: "랭킹",         icon: <ListOrdered size={16} /> },
  { href: "/stats?tab=category",  label: "카테고리",     icon: <Gamepad2 size={16} /> },
  { href: "/stats/guide",         label: "통계 안내",     icon: <BookOpen size={16} /> },
];

/** 헤더 오른쪽 끝의 보조 링크. 서비스 배지는 여기 두지 않는다(왼쪽 브랜드 옆 한 곳). */
function HeaderNav() {
  // `치지직 통계 Beta`는 **왼쪽 브랜드 옆 한 곳**에만 둔다. 예전에는 여기에도
  // 같은 링크가 있어 한 헤더에 배지가 두 번 나왔다.
  return (
    <nav aria-label="주요 메뉴" className="hidden items-center gap-1 md:flex">
      <Link href="/guide"
            className="nb-tap inline-flex items-center rounded-lg px-2.5 py-2 text-sm
                       text-muted transition-colors hover:text-fg">
        사용 방법
      </Link>
    </nav>
  );
}

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
         className={`relative w-full min-w-0 ${compact ? "" : "max-w-[520px]"}`}>
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
          /* 양 끝이 완전히 둥근 capsule. `rounded-lg`(8px)로는 참고 이미지의
             pill 느낌이 나지 않는다 — 높이의 절반 이상이어야 한다. */
          className="w-full min-w-0 rounded-full border border-border bg-bg py-2 pl-10 pr-4
                     text-sm text-fg placeholder-muted transition-colors
                     focus:border-accent focus:outline-none"
        />
      </div>

      {showPanel && (
        <div className="absolute left-0 right-0 top-full z-[60] mt-1.5 max-h-80 overflow-y-auto
                        rounded-2xl border border-border bg-bg-card py-1.5 shadow-xl">
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
      /* 아주 좁은 폭(390@150% = 실폭 260px)에서는 글자를 감추고 아이콘만 남긴다.
         이 버튼이 92px을 버티면 왼쪽 워드마크와 겹쳤다(실측). `aria-label`을 항상
         두어 스크린리더에서는 어느 폭에서든 "로그인"으로 읽힌다. */
      <a href={loginUrl ?? "/login"}
         aria-disabled={!loginUrl}
         aria-label="로그인"
         className="nb-tap-icon inline-flex shrink-0 items-center justify-center gap-1.5
                    rounded-lg bg-accent px-2.5 py-2 text-sm font-medium text-white
                    transition-colors hover:bg-accent-hover xs:px-3 sm:px-4">
        <span className="hidden xs:inline">로그인</span>
        <ArrowRight size={15} aria-hidden="true" />
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

export default function SiteHeader({ maxWidth = "full", statsNav }: {
  /** 본문 폭에 맞춘다. 그 페이지 `<main>`의 `max-w-*`와 같은 값을 넘길 것. */
  maxWidth?: "full" | "6xl" | "4xl" | "3xl";
  /** 통계 sidebar를 가진 페이지가 **자기 상태를 넘겨주는** 통로.
   *
   *  햄버거는 어느 페이지에서나 "통계 내비게이션"을 연다는 뜻을 유지한다.
   *  다만 sidebar가 이미 화면에 있는 `/stats`에서는 그 sidebar를 접고 펴고,
   *  sidebar가 없는 페이지에서는 같은 메뉴를 drawer로 띄운다. 같은 버튼이
   *  페이지마다 **다른 것**을 열지 않게 하려고 상태 소유권을 하나로 모았다. */
  statsNav?: { open: boolean; onToggle: () => void; controlsId: string };
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  /** 좁은 화면에서 검색창을 펼쳤는지. `md` 이상에서는 항상 펼쳐져 있다. */
  const [searchOpen, setSearchOpen] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  const burgerRef = useRef<HTMLButtonElement>(null);
  const drawerId = useId();
  const searchId = useId();

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

  // sidebar를 가진 페이지면 그 sidebar를, 아니면 통계 메뉴 drawer를 제어한다.
  const usesSidebar = Boolean(statsNav);
  const burgerExpanded = usesSidebar ? statsNav!.open : menuOpen;
  const burgerControls = usesSidebar ? statsNav!.controlsId : drawerId;
  const onBurger = usesSidebar ? statsNav!.onToggle : () => setMenuOpen((o) => !o);
  const burgerLabel = usesSidebar
    ? (statsNav!.open ? "통계 메뉴 접기" : "통계 메뉴 펼치기")
    : (menuOpen ? "통계 메뉴 닫기" : "통계 메뉴 열기");

  return (
    <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
      {/* **3영역 grid다.** 좌우를 `1fr`로 같게 잡아야 가운데 칸이 남은 공간의
          중심이 아니라 **viewport의 중심**에 온다. flex + `flex-1` 검색창으로는
          좌우 폭이 다른 순간 검색창 중심이 그 차이의 절반만큼 밀린다(실측으로
          이미지3에서 확인된 문제다).

          최소 높이만 정하고 고정하지 않는다 — 확대(150%)에서 글자가 커져도
          내용이 잘리지 않고 헤더가 함께 자란다. */}
      <div className={`nb-tap-gap grid min-h-[60px] grid-cols-[auto_1fr_auto]
                       items-center gap-2 py-2 md:grid-cols-[1fr_auto_1fr] ${inner}`}>
        {/* ── 왼쪽: 햄버거 · NexBot · 치지직 통계 Beta ── */}
        {/* `overflow-hidden`이 있어야 자식이 셀 밖으로 삐져나가지 않는다.
            390@150%(실폭 260px)에서 워드마크가 오른쪽 묶음과 겹쳤던 자리다. */}
        <div className="flex min-w-0 items-center gap-1 overflow-hidden">
          <button ref={burgerRef} type="button"
                  onClick={onBurger}
                  aria-expanded={burgerExpanded}
                  aria-controls={burgerControls}
                  aria-label={burgerLabel}
                  className="nb-tap-icon inline-flex h-11 w-11 shrink-0 items-center
                             justify-center rounded-lg text-muted transition-colors
                             hover:bg-bg-hover hover:text-fg">
            {burgerExpanded
              ? <X size={20} aria-hidden="true" />
              : <Menu size={20} aria-hidden="true" />}
          </button>

          {/* 로봇 아이콘을 두지 않는다 — 워드마크 하나로 브랜드를 말한다.
              (아이콘 + 텍스트 + 두 번째 아이콘이 겹치면 왼쪽이 시끄러워진다) */}
          {/* `shrink-0`을 주지 않는다 — 260px 같은 폭에서 이 링크가 버티면
              오른쪽 묶음과 겹친다. `nb-brand-tap`의 min-width 44px가 하한이라
              읽을 수 없을 만큼 줄지는 않는다. */}
          <Link href="/"
                className="nb-brand-tap min-w-0 px-1 font-bold text-[17px] text-fg">
            <span className="truncate">NexBot</span>
          </Link>

          {/* `/` 구분자와 신호 아이콘 없이, 워드마크 바로 오른쪽에 붙인다.
              배지는 **이 한 곳에만** 있다(HeaderNav의 중복은 제거했다). */}
          <Link href="/stats"
                className="nb-tap ml-1 hidden min-w-0 items-center gap-1.5 rounded-lg
                           px-2 py-2 text-sm font-medium text-muted transition-colors
                           hover:text-fg sm:inline-flex">
            <span className="min-w-0 truncate">치지직 통계</span>
            <span className="shrink-0 rounded border border-accent/40 px-1 py-px
                             text-[10px] font-bold leading-none text-accent">
              Beta
            </span>
          </Link>
        </div>

        {/* ── 가운데: 전역 검색(viewport 중앙) ──
            320/390에서는 좌우 `1fr`이 0px까지 눌려 브랜드·로그인과 검색창이
            겹쳤다(실측: 320px에서 검색 263px, 남는 폭 57px). 그래서 좁은 화면은
            **접을 수 있는 검색**으로 바꾼다 — 기본은 접힘, 아이콘을 누르면
            헤더 아래 한 줄이 열린다. `md` 이상에서는 늘 펼쳐진 채 중앙 정렬이다. */}
        <div className="hidden min-w-0 justify-center md:flex">
          <GlobalSearch />
        </div>
        <span className="md:hidden" aria-hidden="true" />

        {/* ── 오른쪽: 사용 방법 · 로그인/프로필 (맨 오른쪽 정렬) ── */}
        <div className="flex min-w-0 items-center justify-end gap-1 overflow-hidden">
          <button type="button" onClick={() => setSearchOpen((o) => !o)}
                  aria-expanded={searchOpen} aria-controls={searchId}
                  aria-label={searchOpen ? "검색 닫기" : "스트리머 검색 열기"}
                  className="nb-tap-icon inline-flex h-11 w-11 shrink-0 items-center
                             justify-center rounded-lg text-muted transition-colors
                             hover:bg-bg-hover hover:text-fg md:hidden">
            {searchOpen
              ? <X size={18} aria-hidden="true" />
              : <Search size={18} aria-hidden="true" />}
          </button>
          <HeaderNav />
          <AuthArea />
        </div>
      </div>

      {/* 좁은 화면 전용 검색 줄 — 열렸을 때만 자리를 차지한다. */}
      {searchOpen && (
        <div id={searchId} className={`pb-2 md:hidden ${inner}`}>
          <GlobalSearch />
        </div>
      )}
      {/* 보조 메뉴 — 모바일에서는 drawer, 데스크톱에서도 같은 목록을 쓴다.
          목록을 화면 크기별로 다르게 두면 "PC에서 본 항목이 폰에 없다"가 된다. */}
      {!usesSidebar && menuOpen && (
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
