"use client";
import { useMemo } from "react";
import type { NewcomerInsights, NewcomerCategory, NewcomerSummary } from "@/lib/types";

// 신규 스트리머 분석 탭의 인사이트 시각화 3종.
// 데이터는 모두 newcomers 응답에 함께 실려 오므로 별도 요청이 없다.

const GREEN = "#00FFA3";
const nf = (n: number) => n.toLocaleString("ko-KR");
const Sub = ({ children }: { children: React.ReactNode }) =>
  <p className="mt-2 text-[11px] leading-relaxed text-muted">{children}</p>;

// ── ① 24시간 골든타임 히트맵 ────────────────────────────────────────────────
export function GoldenHourHeatmap({ hourly }: { hourly: NonNullable<NewcomerInsights["hourly"]> }) {
  const { max, peakLabel } = useMemo(() => {
    const mx = Math.max(1, ...hourly.map((h) => h.avg_viewers));
    // 피크타임 라벨: 최고 시간대를 중심으로 연속된 상위 구간(최고의 75% 이상)을 묶는다
    const hot = hourly.filter((h) => h.avg_viewers >= mx * 0.75).map((h) => h.hour).sort((a, b) => a - b);
    if (hot.length === 0) return { max: mx, peakLabel: null };
    // 자정을 넘는 연속 구간(예: 22,23,0,1)도 하나로 보이도록 순환 기준으로 최장 구간을 찾는다
    let best = [hot[0]], cur = [hot[0]];
    for (let i = 1; i < hot.length; i++) {
      if (hot[i] === hot[i - 1] + 1) cur.push(hot[i]);
      else { if (cur.length > best.length) best = cur; cur = [hot[i]]; }
    }
    if (cur.length > best.length) best = cur;
    if (hot.includes(0) && hot.includes(23)) {
      const tail: number[] = [], head: number[] = [];
      for (let h = 23; hot.includes(h); h--) tail.unshift(h);
      for (let h = 0; hot.includes(h); h++) head.push(h);
      if (tail.length + head.length > best.length) best = [...tail, ...head];
    }
    const s = best[0], e = best[best.length - 1];
    const p2 = (n: number) => String(n).padStart(2, "0");
    return { max: mx, peakLabel: `${p2(s)}:00 ~ ${p2((e + 1) % 24)}:00` };
  }, [hourly]);

  const hasData = hourly.some((h) => h.snaps > 0);

  return (
    <div className="card">
      <div className="mb-3 flex items-center justify-between gap-2 flex-wrap">
        <h3 className="section-title">24시간 신입 노출 골든타임 분석</h3>
        {peakLabel && (
          <span className="rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
                style={{ color: GREEN, background: "rgba(0,255,163,0.10)" }}>
            피크타임: {peakLabel}
          </span>
        )}
      </div>

      {!hasData ? (
        <p className="py-8 text-center text-sm text-muted">시간대 데이터가 아직 부족합니다.</p>
      ) : (
        <>
          {/* Tailwind 기본 grid-cols는 12까지라 md:grid-cols-24는 무효(12칸 2줄이 된다).
              임의값 문법으로 24칸을 만든다 — 모바일은 12칸 2줄이 오히려 읽기 좋다. */}
          <div className="grid grid-cols-12 gap-1 md:grid-cols-[repeat(24,minmax(0,1fr))]">
            {hourly.map((h) => {
              const r = h.avg_viewers / max;
              const bg = h.snaps === 0 ? "rgb(var(--color-bg-hover-rgb))"
                : r >= 0.8 ? GREEN
                : r >= 0.5 ? "rgba(0,255,163,0.55)"
                : r >= 0.25 ? "rgba(0,255,163,0.28)"
                : "rgba(0,255,163,0.12)";
              return (
                <div key={h.hour} className="group relative">
                  <div className="h-8 cursor-pointer rounded-sm transition-transform hover:scale-110"
                       style={{ background: bg }} />
                  <span className="mt-0.5 block text-center text-[9px] tabular-nums text-muted/70">
                    {h.hour % 3 === 0 ? h.hour : ""}
                  </span>
                  {/* 호버 툴팁 */}
                  <div className="pointer-events-none absolute bottom-10 left-1/2 z-20 hidden -translate-x-1/2
                                  whitespace-nowrap rounded-lg border border-border bg-bg-card px-2.5 py-1.5
                                  text-[10px] text-fg shadow-2xl group-hover:block">
                    {String(h.hour).padStart(2, "0")}시: 신입 평균 <b>{nf(h.avg_viewers)}명</b>
                    {" "}(경쟁 채널 {nf(h.channels)}개)
                  </div>
                </div>
              );
            })}
          </div>
          <Sub>
            * 초록색이 짙을수록 신입 방송 대비 시청자 유입이 높은 골든타임 시간대입니다.
            최근 24시간 집계 기준이며, 회색은 해당 시간대에 수집된 신입 방송이 없다는 뜻입니다.
          </Sub>
        </>
      )}
    </div>
  );
}

// ── ② 블루오션 카테고리 TOP 5 ───────────────────────────────────────────────
export function BlueOceanCards({ cats, summary }:
  { cats: NewcomerCategory[]; summary?: NewcomerSummary }) {
  // 블루오션 지수 = 카테고리 채널당 평균 시청자 ÷ 신입 전체 평균.
  // 표본이 1~2개면 우연히 높게 나오므로 방송 3개 이상만 후보로 둔다(인사이트 카드와 같은 기준).
  const base = Math.max(1, summary?.avg_viewers ?? 1);
  const top = useMemo(() =>
    cats.filter((c) => c.lives >= 3)
        .map((c) => ({ ...c, index: c.avg_viewers / base }))
        .sort((a, b) => b.index - a.index)
        .slice(0, 5),
    [cats, base]);

  if (top.length === 0) {
    return (
      <div className="card">
        <h3 className="section-title">블루오션 카테고리 TOP 5</h3>
        <p className="py-8 text-center text-sm text-muted">
          신입 방송이 3개 이상인 카테고리가 아직 없습니다.
        </p>
      </div>
    );
  }

  return (
    <div className="card">
      <h3 className="section-title">블루오션 카테고리 TOP 5</h3>
      <p className="mt-0.5 text-[11px] text-muted">
        신입 방송 수 대비 시청자가 많은 카테고리 — 경쟁이 적고 노출 효율이 좋은 구간입니다.
      </p>

      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        {top.map((c, i) => (
          <div key={c.category}
               className="rounded-xl border p-3"
               style={{ background: "rgba(0,255,163,0.04)",
                        borderColor: i === 0 ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))" }}>
            <p className="truncate text-xs font-bold text-fg" title={c.category}>{c.category}</p>
            <p className="mt-1.5 tracking-tight">
              <span className="text-lg font-extrabold tabular-nums" style={{ color: GREEN }}>
                {c.index.toFixed(1)}x
              </span>
            </p>
            <p className="mt-0.5 text-[11px] text-muted">채널당 {nf(c.avg_viewers)}명</p>
            <p className="text-[11px] text-muted/70">방송 {nf(c.lives)}개</p>
          </div>
        ))}
      </div>
      <Sub>
        * 블루오션 지수 = 카테고리 채널당 평균 시청자 ÷ 신입 전체 평균({nf(base)}명).
        2.0x면 같은 방송을 켜도 평균보다 2배 많은 시청자가 들어온다는 뜻입니다.
        표본 왜곡을 막기 위해 신입 방송 3개 이상인 카테고리만 집계합니다.
      </Sub>
    </div>
  );
}

// ── ③ 신입 체급 구간별 분포 ─────────────────────────────────────────────────
const TIER_COLORS = ["rgba(107,114,128,0.65)", "rgba(0,194,255,0.65)", "rgba(0,255,163,0.55)", GREEN];

export function TierDistribution({ tiers }: { tiers: NonNullable<NewcomerInsights["tiers"]> }) {
  const total = tiers.reduce((s, t) => s + t.count, 0);
  if (total === 0) return null;

  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="text-xs font-semibold text-fg">신입 체급 구간별 분포</p>

      {/* 수평 스택 바 */}
      <div className="mt-2 flex h-3 w-full overflow-hidden rounded-full bg-bg-hover">
        {tiers.map((t, i) => (
          <div key={t.label} className="group relative h-full transition-all"
               style={{ width: `${t.share}%`, background: TIER_COLORS[i] }}>
            <div className="pointer-events-none absolute bottom-5 left-1/2 z-20 hidden -translate-x-1/2
                            whitespace-nowrap rounded-lg border border-border bg-bg-card px-2.5 py-1.5
                            text-[10px] text-fg shadow-2xl group-hover:block">
              {t.label} ({t.desc}) · {nf(t.count)}명 · {t.share}%
            </div>
          </div>
        ))}
      </div>

      {/* 범례 */}
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-4">
        {tiers.map((t, i) => (
          <div key={t.label} className="flex items-center gap-1.5 min-w-0">
            <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: TIER_COLORS[i] }} />
            <span className="truncate text-[11px] text-muted">
              <b className="text-fg">{t.label}</b> {t.share}%
            </span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-muted/70">
        * 현재 라이브 중인 신입이 어느 구간에 몰려 있는지 보여 줍니다. 오른쪽 구간으로 갈수록
        신입 상위권입니다.
      </p>
    </div>
  );
}
