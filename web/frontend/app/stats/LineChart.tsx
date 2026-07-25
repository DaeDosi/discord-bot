"use client";
import { useRef, useState } from "react";

export type LinePoint = { t: number } & Record<string, number>;
export interface LineSeries { key: string; name: string; color: string; gradient?: [string, string] }

const VB_W = 760;      // viewBox 내부 좌표(고정) — CSS로 100% 스케일
const PAD  = { l: 46, r: 14, t: 14, b: 26 };

function niceCeil(v: number): number {
  if (v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * pow;
}

const fmtCompact = (n: number) =>
  n >= 10000 ? `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)}만`
  : n >= 1000 ? `${(n / 1000).toFixed(1)}천`
  : `${n}`;

const fmtTime = (t: number) =>
  new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });

const fmtFull = (t: number) =>
  new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

export default function LineChart({
  points, series, height = 220, area = false, unit = "",
}: {
  points: LinePoint[]; series: LineSeries[]; height?: number; area?: boolean; unit?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  if (points.length < 2) {
    return (
      <div className="flex items-center justify-center text-sm text-muted rounded-lg bg-bg-hover/40"
           style={{ height }}>
        시계열 데이터가 아직 부족합니다. 수집이 쌓이면 추이가 그려집니다.
      </div>
    );
  }

  const VB_H = height;
  const plotW = VB_W - PAD.l - PAD.r;
  const plotH = VB_H - PAD.t - PAD.b;

  const xMin = points[0].t;
  const xMax = points[points.length - 1].t;
  const xRange = Math.max(1, xMax - xMin);
  const rawMax = Math.max(1, ...points.flatMap((p) => series.map((s) => p[s.key] ?? 0)));
  const yMax = niceCeil(rawMax);

  const X = (t: number) => PAD.l + ((t - xMin) / xRange) * plotW;
  const Y = (v: number) => PAD.t + (1 - v / yMax) * plotH;

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(yMax * f));
  const xTickCount = Math.min(5, points.length);
  const xTicks = Array.from({ length: xTickCount }, (_, i) =>
    points[Math.round((i / (xTickCount - 1)) * (points.length - 1))].t);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
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
    <div className="relative w-full">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        width="100%" height={height}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        style={{ display: "block", overflow: "visible" }}
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

        {/* y 그리드 + 라벨 */}
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.l} y1={Y(v)} x2={VB_W - PAD.r} y2={Y(v)}
                  stroke="rgb(var(--color-border-rgb))" strokeWidth="1" opacity="0.55" />
            <text x={PAD.l - 8} y={Y(v) + 3} textAnchor="end"
                  fontSize="11" fill="rgb(var(--color-muted-rgb))">{fmtCompact(v)}</text>
          </g>
        ))}

        {/* x 라벨 */}
        {xTicks.map((t, i) => (
          <text key={i} x={X(t)} y={VB_H - 8} textAnchor="middle"
                fontSize="11" fill="rgb(var(--color-muted-rgb))">{fmtTime(t)}</text>
        ))}

        {/* 시리즈 */}
        {series.map((s) => {
          const pts = points.map((p) => `${X(p.t)},${Y(p[s.key] ?? 0)}`).join(" ");
          const areaPts = `${X(points[0].t)},${Y(0)} ${pts} ${X(points[points.length - 1].t)},${Y(0)}`;
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
          className="absolute pointer-events-none z-10 rounded-lg border border-border bg-bg-card px-3 py-2 shadow-xl text-xs"
          style={{
            left: `${(X(hp.t) / VB_W) * 100}%`,
            top: 0,
            transform: X(hp.t) > VB_W / 2 ? "translate(-108%, 0)" : "translate(8px, 0)",
          }}
        >
          <p className="text-muted mb-1">{fmtFull(hp.t)}</p>
          {series.map((s) => (
            <p key={s.key} className="flex items-center gap-1.5 tabular-nums">
              <span className="w-2 h-2 rounded-full" style={{ background: s.color }} />
              <span className="text-fg font-medium">{s.name}</span>
              <span className="text-fg ml-auto pl-3">{(hp[s.key] ?? 0).toLocaleString("ko-KR")}{unit}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
