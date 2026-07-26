"use client";
import { useId, useState } from "react";
import { BookOpen, ChevronDown } from "lucide-react";

// SEO 서술형 텍스트용 접이식 카드.
//
// 크롤러 관련 핵심 제약: 본문을 조건부 렌더링하거나 display:none / hidden 으로 감추면
// 초기 HTML에서 텍스트가 사라져 애드센스·검색 크롤러가 읽지 못한다(이 컴포넌트를 만든
// 목적 자체가 무효화됨). 그래서 본문은 항상 DOM에 렌더하고 max-height + overflow로만
// 시각적으로 접는다. aria-hidden도 쓰지 않는다 — 접힌 상태도 문서에 존재해야 한다.
export default function CollapsibleAbout({
  title, children, defaultOpen = false,
}: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();

  return (
    <section className="mt-10 rounded-xl border border-border bg-bg-card/40">
      <h2>
        <button type="button" onClick={() => setOpen((v) => !v)}
          aria-expanded={open} aria-controls={panelId}
          className="w-full flex items-center gap-2.5 px-4 md:px-5 py-4 text-left
                     font-extrabold tracking-tight hover:bg-bg-hover/50 rounded-xl transition-colors">
          <BookOpen size={17} className="shrink-0 text-accent" />
          <span className="flex-1 text-base md:text-lg">{title}</span>
          <span className="flex items-center gap-1 text-xs font-medium text-muted shrink-0">
            {open ? "접기" : "자세히 보기"}
            <ChevronDown size={15} className="transition-transform"
                         style={{ transform: open ? "rotate(180deg)" : "none" }} />
          </span>
        </button>
      </h2>

      {/* max-height 전환만으로 접는다 — 텍스트는 접힌 상태에도 DOM에 그대로 존재한다.
          펼칠 때는 넉넉한 상한을 줘서 내용 길이에 상관없이 잘리지 않게 한다. */}
      <div id={panelId}
           className="overflow-hidden transition-[max-height] duration-500 ease-in-out"
           style={{ maxHeight: open ? 4000 : 0 }}>
        <div className="px-4 md:px-5 pb-5 pt-0">{children}</div>
      </div>
    </section>
  );
}
