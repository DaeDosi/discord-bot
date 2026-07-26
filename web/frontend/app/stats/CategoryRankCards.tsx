"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Crown, ChevronLeft, ChevronRight } from "lucide-react";
import type { RisingCategory } from "@/lib/types";

// 실시간 카테고리 랭킹 카드 — 다크 글래스모피즘 + 네온 그린 포인트.
// 가로 스크롤은 유지하되 OS 스크롤바를 숨기고 상단 화살표로 이동한다(휠/스와이프는 그대로).

const GREEN = "#00FFA3";
const nf = (n: number) => n.toLocaleString("ko-KR");

// PODIUM 등급별 강조. 4위 이하는 기본 다크 반투명 카드.
const PODIUM = [
  { ring: "rgba(251,191,36,0.5)",  glow: "rgba(251,191,36,0.10)", text: "#FBBF24", label: "1위" },
  { ring: "rgba(209,213,219,0.4)", glow: "rgba(209,213,219,0.07)", text: "#D1D5DB", label: "2위" },
  { ring: "rgba(217,119,6,0.4)",   glow: "rgba(217,119,6,0.08)",  text: "#D97706", label: "3위" },
] as const;

function RankCard({ c, i, total }: { c: RisingCategory; i: number; total: number }) {
  const p = i < 3 ? PODIUM[i] : null;
  const share = total > 0 ? (c.viewers / total) * 100 : 0;

  return (
    <div
      className="relative shrink-0 w-[210px] snap-start rounded-xl border p-3.5 backdrop-blur
                 transition-colors hover:border-accent/50"
      style={{
        borderColor: p ? p.ring : "rgb(var(--color-border-rgb))",
        background: p
          ? `linear-gradient(160deg, ${p.glow}, rgba(var(--color-bg-card-rgb),0.75))`
          : "rgba(var(--color-bg-card-rgb),0.75)",
      }}
    >
      {/* 헤더: 순위 + 카테고리명 + 플랫폼 뱃지 */}
      <div className="flex items-center gap-1.5">
        <span className="inline-flex items-center gap-1 text-sm font-extrabold tabular-nums"
              style={{ color: p ? p.text : "rgb(var(--color-muted-rgb))" }}>
          {i === 0 && <Crown size={13} />}#{i + 1}
        </span>
        {/* 플랫폼 마크 — 현재 수집원은 치지직뿐이라 고정. SOOP 추가 시 여기서 분기 */}
        <span className="ml-auto shrink-0" title="치지직">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/chzzk.png" alt="치지직" width={14} height={14} className="h-3.5 w-3.5" />
        </span>
      </div>

      <h4 className="mt-1.5 truncate text-sm font-bold text-fg" title={c.category}>{c.category}</h4>

      {/* 중앙 지표: 동시 시청자 */}
      <p className="mt-2 text-lg font-bold tabular-nums text-fg">
        {nf(c.viewers)}<span className="ml-0.5 text-[11px] font-normal text-muted">명</span>
      </p>

      {/* 하단 서브 지표 — 작고 흐리게 */}
      <p className="mt-0.5 text-[11px] text-muted">
        {nf(c.lives)}개 채널 · 방송당 {nf(c.avg_viewers)}명
      </p>

      {/* 전체 시청자 중 점유율 — 카드 최하단 2px 네온 바 */}
      <div className="mt-3 h-[2px] w-full overflow-hidden rounded-full bg-bg-hover">
        <div className="h-full rounded-full"
             style={{ width: `${Math.min(100, share)}%`,
                      background: `linear-gradient(90deg, ${GREEN}, #00C2FF)`,
                      boxShadow: `0 0 6px ${GREEN}` }} />
      </div>
      <p className="mt-1 text-[10px] text-muted/70">점유율 {share.toFixed(1)}%</p>
    </div>
  );
}

export default function CategoryRankCards({ categories }: { categories: RisingCategory[] }) {
  const scRef = useRef<HTMLDivElement>(null);
  const [atStart, setAtStart] = useState(true);
  const [atEnd, setAtEnd] = useState(false);

  const sync = useCallback(() => {
    const el = scRef.current;
    if (!el) return;
    setAtStart(el.scrollLeft <= 2);
    setAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 2);
  }, []);

  useEffect(() => {
    sync();
    const el = scRef.current;
    if (!el) return;
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => ro.disconnect();
  }, [sync, categories]);

  const slide = (dir: -1 | 1) => {
    const el = scRef.current;
    if (!el) return;
    // 카드 폭(210) + gap(12) 단위로 3장씩 이동
    el.scrollBy({ left: dir * (210 + 12) * 3, behavior: "smooth" });
  };

  const top = categories.slice(0, 20);
  const total = categories.reduce((s, c) => s + c.viewers, 0);
  const arrowBtn = "rounded-lg border border-border bg-bg-card/80 p-1.5 text-muted transition-colors " +
                   "hover:text-fg hover:bg-bg-hover disabled:opacity-30 disabled:hover:bg-transparent";

  if (top.length === 0) {
    return <p className="py-8 text-center text-sm text-muted">실시간 카테고리 데이터가 아직 없습니다.</p>;
  }

  return (
    <div>
      <div className="mb-3 flex items-end justify-between gap-3">
        <div>
          <h3 className="section-title">실시간 카테고리 랭킹</h3>
          <p className="mt-0.5 text-[11px] text-muted">
            현재 수집 사이클 기준 동시 시청자 상위 카테고리 · 약 10분 주기 갱신
          </p>
        </div>
        {/* OS 스크롤바 대신 화살표 컨트롤 */}
        <div className="flex shrink-0 items-center gap-1.5">
          <button type="button" onClick={() => slide(-1)} disabled={atStart}
                  className={arrowBtn} aria-label="이전">
            <ChevronLeft size={15} />
          </button>
          <button type="button" onClick={() => slide(1)} disabled={atEnd}
                  className={arrowBtn} aria-label="다음">
            <ChevronRight size={15} />
          </button>
        </div>
      </div>

      {/* scrollbar-hide 유틸이 없어 인라인 스타일로 숨긴다(휠/스와이프는 그대로 동작) */}
      <div ref={scRef} onScroll={sync}
           className="flex snap-x snap-mandatory gap-3 overflow-x-auto pb-1
                      [scrollbar-width:none] [-ms-overflow-style:none]
                      [&::-webkit-scrollbar]:hidden">
        {top.map((c, i) => <RankCard key={c.category} c={c} i={i} total={total} />)}
      </div>
    </div>
  );
}
