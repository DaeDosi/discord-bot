"use client";
import { useMemo, useState } from "react";
import type { StreamerDaily } from "@/lib/types";

// 깃허브 스타일 활동 잔디.
// 요구사항대로 각진 네모(rounded-[2px]) + 촘촘한 간격(gap-[3px])이며,
// 상단 월 라벨 / 좌측 요일 라벨 / 우측 하단 범례를 갖는다.

export type HeatMetric = "minutes" | "viewership" | "peak" | "avg_viewers";

export const HEAT_METRICS: { k: HeatMetric; label: string; unit: string }[] = [
  { k: "minutes",     label: "방송시간",    unit: "시간" },
  { k: "viewership",  label: "뷰어쉽",      unit: "명·시간" },
  { k: "peak",        label: "최고 시청자", unit: "명" },
  { k: "avg_viewers", label: "평균 시청자", unit: "명" },
];

// 농도 5단계 (0단계는 빈 칸)
const LEVELS = ["rgba(0,255,163,0.10)", "rgba(0,255,163,0.30)", "rgba(0,255,163,0.52)",
                "rgba(0,255,163,0.76)", "#00FFA3"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CELL = 11, GAP = 3;   // px — 칸 크기와 간격
const WEEKS = 26;           // 약 6개월

const ymd = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export default function Heatmap({ daily, metric }: { daily: StreamerDaily[]; metric: HeatMetric }) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const opt = HEAT_METRICS.find((m) => m.k === metric)!;

  const { cols, max } = useMemo(() => {
    const map = new Map<string, StreamerDaily>();
    daily.forEach((d) => map.set(d.date, d));

    // 이번 주 일요일까지 채운 뒤 WEEKS주 뒤로 거슬러 올라간다(깃허브와 같은 주 단위 열)
    const end = new Date(); end.setHours(0, 0, 0, 0);
    end.setDate(end.getDate() + (6 - end.getDay()));
    const start = new Date(end);
    start.setDate(start.getDate() - (WEEKS * 7 - 1));

    const cols: { key: string; days: ({ date: string; val: number; d?: StreamerDaily } | null)[] }[] = [];
    let mx = 0;
    for (let w = 0; w < WEEKS; w++) {
      const days: ({ date: string; val: number; d?: StreamerDaily } | null)[] = [];
      for (let dow = 0; dow < 7; dow++) {
        const cur = new Date(start);
        cur.setDate(start.getDate() + w * 7 + dow);
        if (cur > end) { days.push(null); continue; }
        const key = ymd(cur);
        const d = map.get(key);
        // 방송시간 지표는 minutes(분)로 저장돼 있어 시간으로 환산해 표시한다
        const raw = d ? (metric === "minutes" ? d.minutes / 60 : (d[metric] as number)) : 0;
        mx = Math.max(mx, raw);
        days.push({ date: key, val: raw, d });
      }
      cols.push({ key: `w${w}`, days });
    }
    return { cols, max: mx };
  }, [daily, metric]);

  const level = (v: number) => (v <= 0 ? -1 : Math.min(4, Math.floor((v / (max || 1)) * 4.999)));
  const fmt = (v: number) => (metric === "minutes" ? v.toFixed(1) : Math.round(v).toLocaleString("ko-KR"));

  // 월 라벨: 각 열의 첫날 기준으로 월이 바뀌는 지점에만 표시
  const monthLabels = cols.map((c, i) => {
    const first = c.days.find(Boolean);
    if (!first) return null;
    const m = new Date(first.date).getMonth();
    const prev = i > 0 ? cols[i - 1].days.find(Boolean) : null;
    if (i === 0 || (prev && new Date(prev.date).getMonth() !== m)) return { i, text: MONTHS[m] };
    return null;
  }).filter(Boolean) as { i: number; text: string }[];

  return (
    <div className="relative">
      <div className="overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <div style={{ minWidth: WEEKS * (CELL + GAP) + 30 }}>
          {/* 월 라벨 */}
          <div className="relative mb-1 ml-[30px] h-3">
            {monthLabels.map((m) => (
              <span key={m.i} className="absolute text-[10px] text-muted"
                    style={{ left: m.i * (CELL + GAP) }}>{m.text}</span>
            ))}
          </div>

          <div className="flex">
            {/* 요일 라벨 (Mon/Wed/Fri) */}
            <div className="mr-1.5 flex w-[24px] shrink-0 flex-col" style={{ gap: GAP }}>
              {["", "Mon", "", "Wed", "", "Fri", ""].map((w, i) => (
                <span key={i} className="text-[9px] leading-none text-muted"
                      style={{ height: CELL, lineHeight: `${CELL}px` }}>{w}</span>
              ))}
            </div>

            {/* 주 단위 열 */}
            <div className="flex" style={{ gap: GAP }}>
              {cols.map((c) => (
                <div key={c.key} className="flex flex-col" style={{ gap: GAP }}>
                  {c.days.map((cell, i) => {
                    if (!cell) return <div key={i} style={{ width: CELL, height: CELL }} />;
                    const lv = level(cell.val);
                    return (
                      <div key={i}
                        className="rounded-[2px] cursor-pointer transition-transform hover:scale-125"
                        style={{
                          width: CELL, height: CELL,
                          background: lv < 0 ? "rgb(var(--color-bg-hover-rgb))" : LEVELS[lv],
                        }}
                        onMouseEnter={(e) => {
                          const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                          const d = cell.d;
                          setTip({
                            x: r.left + r.width / 2, y: r.top,
                            text: d
                              ? `${cell.date}: ${(d.minutes / 60).toFixed(1)}시간 방송 (평균 시청자 ${d.avg_viewers.toLocaleString("ko-KR")}명)`
                              : `${cell.date}: 방송 없음`,
                          });
                        }}
                        onMouseLeave={() => setTip(null)}
                      />
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* 범례 */}
      <div className="mt-2 flex items-center justify-end gap-1.5">
        <span className="text-[10px] text-muted">Less</span>
        {LEVELS.map((c, i) => (
          <span key={i} className="rounded-[2px]" style={{ width: CELL, height: CELL, background: c }} />
        ))}
        <span className="text-[10px] text-muted">More</span>
        <span className="ml-2 text-[10px] text-muted/70">기준: {opt.label} ({opt.unit})</span>
      </div>

      {/* 툴팁 — fixed로 띄워 overflow 컨테이너에 잘리지 않게 한다 */}
      {tip && (
        <div className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-full rounded-lg
                        border border-border bg-bg-card px-2.5 py-1.5 text-[11px] text-fg shadow-xl"
             style={{ left: tip.x, top: tip.y - 6 }}>
          {tip.text}
        </div>
      )}
    </div>
  );
}
