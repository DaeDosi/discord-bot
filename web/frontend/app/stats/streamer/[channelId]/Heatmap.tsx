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
// 월·요일 라벨은 **고정 한국어 배열**이다. `toLocaleDateString`을 쓰면 브라우저
// locale에 따라 "Feb"가 되기도 "2월"이 되기도 하고, 서버(Node)와 클라이언트의
// locale이 다르면 하이드레이션 때 글자가 바뀐다.
const MONTHS = ["1월", "2월", "3월", "4월", "5월", "6월",
                "7월", "8월", "9월", "10월", "11월", "12월"];
// 행 순서는 아래 그리드와 같다 — start가 항상 일요일이므로 0=일 … 6=토.
// 예전에는 월·수·금만 적었는데, 나머지 요일이 비어 있으면 어느 행이 무슨 요일인지
// 세어 봐야 했다. 한 글자면 7개가 모두 들어간다.
const DOW = ["일", "월", "화", "수", "목", "금", "토"];
const CELL = 11, GAP = 3;   // px — 칸 크기와 간격
const WEEKS = 26;           // 약 6개월
// 월 라벨이 서로 겹치지 않는 최소 간격(px). 열 간격은 14px뿐이라 "12월" 같은
// 라벨은 두 열을 넘게 차지한다 — 이걸 두지 않으면 첫 라벨과 다음 달이 붙어
// "2월3월"처럼 한 덩어리로 읽힌다(실측).
const MIN_LABEL_GAP = 30;

// KST(UTC+9) 기준의 '오늘'. 서버(UTC)와 브라우저(로컬)가 같은 순간에 같은 값을
// 내도록 오프셋을 더한 뒤 UTC 게터로 읽는다. 로컬 게터를 쓰면 실행 위치에 따라
// 하루가 밀려 SSR과 클라이언트의 격자가 어긋난다.
const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const kstToday = () => {
  const t = new Date(Date.now() + KST_OFFSET_MS);
  return new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()));
};

const ymd = (d: Date) =>
  `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;

/** "YYYY-MM-DD" → 월 인덱스(0~11). `new Date(문자열)`은 UTC로 해석돼 타임존에 따라
 *  하루가 밀리므로, 문자열을 그대로 쪼갠다(연·월 경계가 정확해진다). */
const monthOf = (date: string) => Number(date.slice(5, 7)) - 1;

export default function Heatmap({ daily, metric }: { daily: StreamerDaily[]; metric: HeatMetric }) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const opt = HEAT_METRICS.find((m) => m.k === metric)!;

  const { cols, max } = useMemo(() => {
    const map = new Map<string, StreamerDaily>();
    daily.forEach((d) => map.set(d.date, d));

    // 이번 주 토요일까지 채운 뒤 WEEKS주 뒤로 거슬러 올라간다(깃허브와 같은 주 단위 열).
    // start는 항상 일요일이 되므로 행 순서가 일~토로 고정된다(DOW 배열과 같다).
    const end = kstToday();
    end.setUTCDate(end.getUTCDate() + (6 - end.getUTCDay()));
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - (WEEKS * 7 - 1));

    const cols: { key: string; days: ({ date: string; val: number; d?: StreamerDaily } | null)[] }[] = [];
    let mx = 0;
    for (let w = 0; w < WEEKS; w++) {
      const days: ({ date: string; val: number; d?: StreamerDaily } | null)[] = [];
      for (let dow = 0; dow < 7; dow++) {
        const cur = new Date(start);
        cur.setUTCDate(start.getUTCDate() + w * 7 + dow);
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

  // 월 라벨 — 월이 실제로 바뀌는 열에만 붙이고, 서로 겹치면 뒤엣것을 버린다.
  //
  // 예전에는 (1) 0번 열에 무조건 하나 찍고 (2) 겹침 검사가 없었다. 그래서 첫 열
  // 라벨과 바로 다음 달 라벨이 14px 간격으로 붙어 "2월3월"처럼 한 덩어리로 읽혔다.
  // 이제 경계 라벨을 우선하고, 0번 열 라벨은 첫 경계와 충분히 떨어져 있을 때만 둔다.
  const monthLabels = useMemo(() => {
    const boundaries: { i: number; text: string }[] = [];
    let firstMonth: number | null = null;
    cols.forEach((c, i) => {
      const first = c.days.find(Boolean);
      if (!first) return;
      const m = monthOf(first.date);
      if (firstMonth === null) { firstMonth = m; return; }
      const prev = cols[i - 1]?.days.find(Boolean);
      if (prev && monthOf(prev.date) !== m) boundaries.push({ i, text: MONTHS[m] });
    });

    const out: { i: number; text: string }[] = [];
    // 첫 열 라벨은 '이 격자가 어느 달에서 시작하는지'를 알려 주지만, 실제 월
    // 경계보다 정보가 약하다. 붙을 것 같으면 넣지 않는다.
    if (firstMonth !== null
        && (!boundaries.length || boundaries[0].i * (CELL + GAP) >= MIN_LABEL_GAP)) {
      out.push({ i: 0, text: MONTHS[firstMonth] });
    }
    for (const b of boundaries) {
      const last = out[out.length - 1];
      if (last && (b.i - last.i) * (CELL + GAP) < MIN_LABEL_GAP) continue;
      out.push(b);
    }
    return out;
  }, [cols]);

  return (
    <div className="relative">
      <div className="overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {/* 왼쪽 여백 24px = 요일 라벨 폭 18px + 오른쪽 여백 6px(mr-1.5).
            이 값이 어긋나면 월 라벨이 해당 열 위에 오지 않는다. */}
        <div style={{ minWidth: WEEKS * (CELL + GAP) + 24 }}>
          {/* 월 라벨 */}
          <div className="relative mb-1 ml-[24px] h-3.5">
            {monthLabels.map((m) => (
              <span key={m.i} className="absolute whitespace-nowrap text-[10px] text-muted"
                    style={{ left: m.i * (CELL + GAP) }}>{m.text}</span>
            ))}
          </div>

          <div className="flex">
            {/* 요일 라벨 — 7개 모두 한 글자로 적는다. 한 글자면 폭이 좁아 다 들어가고,
                건너뛴 행이 없어 어느 행이 무슨 요일인지 세지 않아도 된다. */}
            <div className="mr-1.5 flex w-[18px] shrink-0 flex-col" style={{ gap: GAP }}>
              {DOW.map((w, i) => (
                <span key={i} className="text-[10px] leading-none text-muted"
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
