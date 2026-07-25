"use client";
import { useEffect, useRef, useState } from "react";

export type LinePoint = { t: number } & Record<string, number>;
export interface LineSeries { key: string; name: string; color: string; gradient?: [string, string] }

const PAD  = { l: 14, r: 14, t: 16, b: 26 };

// "예쁜" 눈금 알고리즘 — 일정 간격(1/2/2.5/5 ×10ⁿ)의 둥근 눈금으로 정렬한다.
function niceNum(x: number, round: boolean): number {
  if (x <= 0) return 1;
  const exp = Math.floor(Math.log10(x));
  const f = x / Math.pow(10, exp);
  let nf: number;
  if (round) nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
  else       nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return nf * Math.pow(10, exp);
}

function niceScale(min: number, max: number, maxTicks = 5): { nMin: number; nMax: number; ticks: number[] } {
  if (max <= min) max = min + 1;
  const step = niceNum((max - min) / Math.max(1, maxTicks - 1), true) || 1;
  const nMin = Math.floor(min / step) * step;
  const nMax = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = nMin; v <= nMax + step * 0.5; v += step) ticks.push(Math.round(v));
  return { nMin, nMax, ticks };
}

const fmtTime = (t: number) =>
  new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });

const fmtFull = (t: number) =>
  new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

export default function LineChart({
  points, series, height = 220, area = false, unit = "", dynamicY = false,
  tooltipItems, showPeak = false,
}: {
  points: LinePoint[]; series: LineSeries[]; height?: number; area?: boolean; unit?: string; dynamicY?: boolean;
  tooltipItems?: (p: LinePoint) => { label: string; value: string; color?: string }[];
  showPeak?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  // 컨테이너 실제 폭을 측정해 viewBox 폭과 일치시킨다 — preserveAspectRatio로 인한
  // 레터박스/좌표 오프셋(툴팁이 커서와 어긋나던 버그)을 원천 차단.
  const [w, setW] = useState(760);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const apply = () => setW(Math.max(320, Math.round(el.clientWidth)));
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (points.length < 2) {
    return (
      <div ref={wrapRef} className="flex items-center justify-center text-sm text-muted rounded-lg bg-bg-hover/40"
           style={{ height }}>
        시계열 데이터가 아직 부족합니다. 수집이 쌓이면 추이가 그려집니다.
      </div>
    );
  }

  const VB_W = w;
  const VB_H = height;
  const plotW = VB_W - PAD.l - PAD.r;
  const plotH = VB_H - PAD.t - PAD.b;

  const xMin = points[0].t;
  const xMax = points[points.length - 1].t;
  const xRange = Math.max(1, xMax - xMin);

  const allVals = points.flatMap((p) => series.map((s) => p[s.key] ?? 0));
  const dataMax = Math.max(1, ...allVals);
  const dataMin = Math.min(...allVals);

  // 가변 Y축(dynamicY): 데이터 범위를 예쁜 눈금 단위로 스냅해 일정 간격으로 정렬.
  // 고정은 0부터.
  const { nMin, nMax, ticks: yTicks } = dynamicY && dataMax > dataMin
    ? niceScale(dataMin, dataMax)
    : niceScale(0, dataMax);
  const yMin = nMin, yMax = nMax;
  const yRange = Math.max(1, yMax - yMin);

  const X = (t: number) => PAD.l + ((t - xMin) / xRange) * plotW;
  const Y = (v: number) => PAD.t + (1 - (v - yMin) / yRange) * plotH;

  // X축 눈금: 정시(1시간) 간격. 창이 넓으면 2/3/6/12/24시간으로 자동 확장(눈금 ~8개 이하 유지).
  const HOUR = 3600;
  const xStep = [1, 2, 3, 6, 12, 24, 48].map((h) => h * HOUR).find((s) => xRange / s <= 8) ?? 48 * HOUR;
  const xTicks: number[] = [];
  for (let t = Math.ceil(xMin / xStep) * xStep; t <= xMax; t += xStep) xTicks.push(t);
  if (xTicks.length === 0) xTicks.push(xMin, xMax);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * VB_W;
    const tGuess = xMin + ((px - PAD.l) / plotW) * xRange;
    let best = 0, bestD = Infinity;
    for (let i = 0; i < points.length; i++) {
      const d = Math.abs(points[i].t - tGuess);
      if (d < bestD) { bestD = d; best = i; }
    }
    setHover(best);
  };

  const hp = hover !== null ? points[hover] : null;

  return (
    <div ref={wrapRef} className="relative w-full">
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        width="100%" height={height}
        preserveAspectRatio="none"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        style={{ display: "block" }}
      >
        <defs>
          {series.map((s) => (
            <linearGradient key={s.key} id={`grad-${s.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor={s.gradient ? s.gradient[0] : s.color} stopOpacity="0.24" />
              <stop offset="100%" stopColor={s.gradient ? s.gradient[1] : s.color} stopOpacity="0" />
            </linearGradient>
          ))}
          {/* 2톤 그라데이션 라인 스트로크(수평) */}
          {series.filter((s) => s.gradient).map((s) => (
            <linearGradient key={`stroke-${s.key}`} id={`stroke-${s.key}`} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%"   stopColor={s.gradient![0]} />
              <stop offset="100%" stopColor={s.gradient![1]} />
            </linearGradient>
          ))}
        </defs>

        {/* y 그리드 (숫자 라벨 없이 가로선만) */}
        {yTicks.map((v) => (
          <line key={v} x1={PAD.l} y1={Y(v)} x2={VB_W - PAD.r} y2={Y(v)}
                stroke="rgb(var(--color-border-rgb))" strokeWidth="1" opacity="0.5" />
        ))}

        {/* x 라벨 */}
        {xTicks.map((t, i) => (
          <text key={i} x={X(t)} y={VB_H - 8} textAnchor="middle"
                fontSize="11" fill="rgb(var(--color-muted-rgb))">{fmtTime(t)}</text>
        ))}

        {/* 시리즈 */}
        {series.map((s) => {
          const pts = points.map((p) => `${X(p.t)},${Y(p[s.key] ?? 0)}`).join(" ");
          const base = Y(yMin);
          const areaPts = `${X(points[0].t)},${base} ${pts} ${X(points[points.length - 1].t)},${base}`;
          return (
            <g key={s.key}>
              {area && <polygon points={areaPts} fill={`url(#grad-${s.key})`} />}
              <polyline points={pts} fill="none"
                        stroke={s.gradient ? `url(#stroke-${s.key})` : s.color}
                        strokeWidth={s.gradient ? 2.5 : 2}
                        strokeLinejoin="round" strokeLinecap="round" />
            </g>
          );
        })}

        {/* 피크(최고치) 마커 — 축까지 내려가는 점선 + 발광 점 (피크 시각을 명확히 표시) */}
        {showPeak && (() => {
          const s0 = series[0];
          let mi = 0, mv = -Infinity;
          points.forEach((p, i) => { const v = p[s0.key] ?? 0; if (v > mv) { mv = v; mi = i; } });
          const px = X(points[mi].t), py = Y(mv);
          return (
            <g style={{ pointerEvents: "none" }}>
              <line x1={px} y1={py} x2={px} y2={PAD.t + plotH}
                    stroke="#FF4FA3" strokeWidth="1.5" strokeDasharray="3 3" opacity="0.5" />
              <circle cx={px} cy={py} r="9" fill="#FF4FA3" opacity="0.18" />
              <circle cx={px} cy={py} r="4.5" fill="#FF4FA3"
                      stroke="rgb(var(--color-bg-card-rgb))" strokeWidth="2" />
            </g>
          );
        })()}

        {/* 크로스헤어 */}
        {hp && (
          <g>
            <line x1={X(hp.t)} y1={PAD.t} x2={X(hp.t)} y2={VB_H - PAD.b}
                  stroke="rgb(var(--color-muted-rgb))" strokeWidth="1" opacity="0.5" strokeDasharray="3 3" />
            {series.map((s) => (
              <circle key={s.key} cx={X(hp.t)} cy={Y(hp[s.key] ?? 0)} r="3.5"
                      fill="rgb(var(--color-bg-card-rgb))" stroke={s.color} strokeWidth="2" />
            ))}
          </g>
        )}
      </svg>

      {/* 툴팁 */}
      {hp && (
        <div
          className="absolute pointer-events-none z-10 rounded-lg border border-border bg-bg-card px-3 py-2 shadow-xl text-xs whitespace-nowrap"
          style={{
            left: `${(X(hp.t) / VB_W) * 100}%`,
            top: 0,
            width: "max-content",
            maxWidth: "none",
            transform: X(hp.t) > VB_W / 2 ? "translate(-108%, 0)" : "translate(8px, 0)",
          }}
        >
          <p className="text-muted mb-1 whitespace-nowrap">{fmtFull(hp.t)} 기준</p>
          {tooltipItems ? tooltipItems(hp).map((it, i) => (
            <p key={i} className="flex items-center gap-1.5 tabular-nums whitespace-nowrap">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: it.color ?? "transparent" }} />
              <span className="text-fg font-medium">{it.label}</span>
              <span className="text-fg ml-auto pl-4">{it.value}</span>
            </p>
          )) : series.map((s) => (
            <p key={s.key} className="flex items-center gap-1.5 tabular-nums whitespace-nowrap">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.color }} />
              <span className="text-fg font-medium">{s.name}</span>
              <span className="text-fg ml-auto pl-4">{(hp[s.key] ?? 0).toLocaleString("ko-KR")}{unit}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
