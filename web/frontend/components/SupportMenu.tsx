"use client";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ExternalLink, HelpCircle, Mail, Megaphone, MessageSquare, X } from "lucide-react";

import { api } from "@/lib/api";
import { shouldShowSupportMenu } from "@/lib/supportMenu";

// 우측 하단 지원 버튼.
//
// ── 배치 원칙 ───────────────────────────────────────────────────────────────
// `app/layout.tsx`에서 **한 번만** 렌더한다. 화면마다 붙이면 같은 버튼이 두 번
// 그려지거나 화면별로 동작이 갈라진다.
//
// 어떤 경로에서 띄우지 않는지는 `lib/supportMenu.ts`가 정한다(순수 함수 + 테스트).
// 404·error 화면에서 숨기는 것은 경로로 판정할 수 없어서 `globals.css`가 맡는다 —
// `app/not-found.tsx`·`app/error.tsx`가 다는 `data-nb-chrome="minimal"` 표시를 보고
// 감춘다. 실제로 이 화면들에서 버튼이 떠 있는 것을 실측으로 확인해 막은 것이다.
//
// ── 가리지 않기 ─────────────────────────────────────────────────────────────
// 페이지 하단 콘텐츠(푸터·표 마지막 행)를 덮지 않도록 `Footer`에 하단 여백을 주는
// 대신, 버튼 자체를 작게 두고 스크롤을 막지 않는다(`pointer-events`는 버튼에만).
// iOS 홈 인디케이터를 피하려고 `env(safe-area-inset-*)`를 더한다.

const SUPPORT_DISCORD = "https://discord.gg/DaZxywE4Ka"; // components/Footer.tsx와 같은 값

export default function SupportMenu() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeLoaded, setNoticeLoaded] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();   // 닫으면 포커스를 trigger로 되돌린다
  }, []);

  // 바깥 클릭 · ESC
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  // 공지는 **열 때 한 번만** 가져온다 — 모든 페이지에서 상시 호출하면 공개 API에
  // 불필요한 트래픽이 된다.
  useEffect(() => {
    if (!open || noticeLoaded) return;
    setNoticeLoaded(true);
    api.stats.announcement()
      .then((d) => setNotice((d.message || "").trim() || null))
      .catch(() => setNotice(null));
  }, [open, noticeLoaded]);

  if (!shouldShowSupportMenu(pathname)) return null;

  const itemCls =
    "nb-tap flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm text-fg " +
    "transition-colors hover:bg-bg-hover focus-visible:bg-bg-hover";

  return (
    <div
      ref={rootRef}
      data-nb-support=""
      /* 고정 요소라도 내용이 뷰포트보다 넓으면 페이지 스크롤 폭을 늘린다
         (실측 320px: 컨테이너 right=332 → 페이지 sw 351). 좌우 여백 1rem씩을 뺀 값으로
         상한을 두면 본문을 가리지도, 페이지를 넓히지도 않는다. */
      className="fixed right-4 z-40 flex max-w-[calc(100vw-2rem)] flex-col items-end gap-2"
      style={{
        bottom: "calc(1rem + env(safe-area-inset-bottom, 0px))",
        right: "calc(1rem + env(safe-area-inset-right, 0px))",
      }}
    >
      {open && (
        <div
          id={panelId}
          role="dialog"
          aria-label="지원"
          className="w-[min(17rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-border
                     bg-bg-card p-1.5 shadow-2xl shadow-black/40"
        >
          <Link href="/contact" className={itemCls} onClick={() => setOpen(false)}>
            <Mail size={16} className="shrink-0 text-muted" aria-hidden="true" />
            문의하기
          </Link>

          <a href={SUPPORT_DISCORD} target="_blank" rel="noopener noreferrer"
             className={itemCls} onClick={() => setOpen(false)}>
            <MessageSquare size={16} className="shrink-0 text-muted" aria-hidden="true" />
            <span className="flex-1">서포트 서버</span>
            <ExternalLink size={13} className="shrink-0 text-muted" aria-hidden="true" />
            <span className="sr-only">새 창에서 열림</span>
          </a>

          {/* 공지 사항 — 전용 공개 페이지가 없어 **기존 공개 API의 현재 공지**를
              그대로 보여 준다. 없는 URL을 만들지 않기 위한 선택이다. */}
          <div className="mt-1 border-t border-border px-3 pb-1.5 pt-2.5">
            <p className="flex items-center gap-2 text-sm font-medium text-fg">
              <Megaphone size={16} className="shrink-0 text-muted" aria-hidden="true" />
              공지 사항
            </p>
            <p aria-live="polite" className="mt-1.5 text-[13px] leading-relaxed text-muted">
              {!noticeLoaded ? "불러오는 중입니다."
                : notice ?? "현재 등록된 공지가 없습니다."}
            </p>
          </div>
        </div>
      )}

      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={open ? "지원 메뉴 닫기" : "지원 메뉴 열기"}
        className="nb-tap-icon flex h-12 w-12 items-center justify-center rounded-full
                   bg-accent text-white shadow-lg shadow-accent/30 transition-colors
                   hover:bg-accent-hover"
      >
        {open ? <X size={20} aria-hidden="true" /> : <HelpCircle size={22} aria-hidden="true" />}
      </button>
    </div>
  );
}
