"use client";
import { useEffect, useRef, useState } from "react";

// partial = 아직 채워지는 중인 마지막 버킷('집계 중'). 값은 확정치가 아니다.
export type LinePoint = { t: number; partial?: boolean } & Record<string, number>;
export interface LineSeries { key: string; name: string; color: string; gradient?: [string, string] }

/** 수집 공백에서 경로를 끊는다 — 0으로 메우거나 양끝을 직선으로 이으면 없던 추세가 생긴다.
 *  간격이 기대 간격의 1.5배를 넘으면 사이클이 최소 한 번 빠진 것으로 본다. */
export function splitSegments(points: LinePoint[], step: number): LinePoint[][] {
  if (!step || points.length === 0) return points.length ? [points] : [];
  const gap = step * 1.5;
  const out: LinePoint[][] = [[points[0]]];
  for (let i = 1; i < points.length; i++) {
    if (points[i].t - points[i - 1].t > gap) out.push([points[i]]);
    else out[out.length - 1].push(points[i]);
  }
  return out;
}

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

const fmtDate = (t: number) =>
  new Date(t * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });

const fmtFull = (t: number) =>
  new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

export default function LineChart({
  points, series, height = 220, area = false, unit = "", dynamicY = false,
  tooltipItems, showPeak = false, stepSeconds = 0, tooltipNote,
}: {
  points: LinePoint[]; series: LineSeries[]; height?: number; area?: boolean; unit?: string; dynamicY?: boolean;
  tooltipItems?: (p: LinePoint) => { label: string; value: string; color?: string }[];
  showPeak?: boolean;
  /** 기대 수집 간격(초). 주면 이보다 크게 벌어진 구간에서 선을 끊는다. */
  stepSeconds?: number;
  /** 툴팁 하단 보조 설명(구간 평균/집계 중 등) */
  tooltipNote?: (p: LinePoint) => string | null;
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

  // 자정 경계 — 창이 하루를 넘길 때(롤링 24시간 등) 날짜가 바뀌는 지점을 표시한다.
  // HH:mm 라벨만으로는 어제 13:00과 오늘 13:00을 구분할 수 없어서 필요.
  // += 86400 대신 setDate로 넘겨 DST가 있는 지역에서도 실제 자정에 맞춘다.
  const midnights: number[] = [];
  {
    const d = new Date(xMin * 1000);
    d.setHours(24, 0, 0, 0);
    while (d.getTime() / 1000 <= xMax) {
      midnights.push(Math.floor(d.getTime() / 1000));
      d.setDate(d.getDate() + 1);
    }
  }

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
  const segments = splitSegments(points, stepSeconds);

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

        {/* 자정 구분선 + 날짜 라벨 (데이터 뒤에 깔리도록 시리즈보다 먼저) */}
        {midnights.map((t) => (
          <g key={`mn-${t}`} style={{ pointerEvents: "none" }}>
            <line x1={X(t)} y1={PAD.t} x2={X(t)} y2={PAD.t + plotH}
                  stroke="rgb(var(--color-muted-rgb))" strokeWidth="1"
                  strokeDasharray="2 4" opacity="0.55" />
            <text x={Math.min(X(t) + 4, VB_W - PAD.r - 28)} y={PAD.t + 10}
                  fontSize="10" fill="rgb(var(--color-muted-rgb))">{fmtDate(t)}</text>
          </g>
        ))}

        {/* x 라벨 */}
        {xTicks.map((t, i) => (
          <text key={i} x={X(t)} y={VB_H - 8} textAnchor="middle"
                fontSize="11" fill="rgb(var(--color-muted-rgb))">{fmtTime(t)}</text>
        ))}

        {/* 시리즈 — 수집 공백에서 끊어진 구간별로 그린다 */}
        {series.map((s) => {
          const stroke = s.gradient ? `url(#stroke-${s.key})` : s.color;
          const sw = s.gradient ? 2.5 : 2;
          const base = Y(yMin);
          return (
            <g key={s.key}>
              {segments.map((seg, si) => {
                // 확정 구간과 '집계 중' 구간을 나눠 그린다 — 미완성 버킷까지 실선으로
                // 이으면 아직 안 끝난 평균이 확정치처럼 보인다.
                const solid = seg[seg.length - 1]?.partial ? seg.slice(0, -1) : seg;
                const xy = (p: LinePoint) => `${X(p.t)},${Y(p[s.key] ?? 0)}`;
                const dashed = seg[seg.length - 1]?.partial && seg.length >= 2
                  ? seg.slice(-2).map(xy).join(" ") : "";
                if (seg.length === 1) {
                  // 앞뒤가 모두 공백인 외톨이 포인트 — 선으로는 보이지 않으므로 점을 찍는다
                  return <circle key={si} cx={X(seg[0].t)} cy={Y(seg[0][s.key] ?? 0)}
                                 r="2" fill={s.color} opacity={seg[0].partial ? 0.5 : 1} />;
                }
                const pts = solid.map(xy).join(" ");
                return (
                  <g key={si}>
                    {area && solid.length >= 2 && (
                      <polygon fill={`url(#grad-${s.key})`}
                               points={`${X(solid[0].t)},${base} ${pts} ${X(solid[solid.length - 1].t)},${base}`} />
                    )}
                    {solid.length >= 2 && (
                      <polyline points={pts} fill="none" stroke={stroke} strokeWidth={sw}
                                strokeLinejoin="round" strokeLinecap="round" />
                    )}
                    {dashed && (
                      <polyline points={dashed} fill="none" stroke={stroke} strokeWidth={sw}
                                strokeDasharray="4 3" opacity="0.55" strokeLinecap="round" />
                    )}
                  </g>
                );
              })}
              {/* '집계 중' 버킷 표식 — 속이 빈 점으로 확정 포인트와 구분한다 */}
              {points.filter((p) => p.partial).map((p) => (
                <circle key={`pt-${p.t}`} cx={X(p.t)} cy={Y(p[s.key] ?? 0)} r="3.5"
                        fill="rgb(var(--color-bg-card-rgb))" stroke={s.color}
                        strokeWidth="1.5" strokeDasharray="2 1.5" opacity="0.9" />
              ))}
            </g>
          );
        })}

        {/* 피크(최고치) 마커 — 축까지 내려가는 점선 + 발광 점 (피크 시각을 명확히 표시) */}
        {showPeak && (() => {
          const s0 = series[0];
          let mi = -1, mv = -Infinity;
          // 아직 채워지는 중인 버킷은 평균이 확정치가 아니라 피크 후보에서 뺀다
          points.forEach((p, i) => {
            if (p.partial) return;
            const v = p[s0.key] ?? 0; if (v > mv) { mv = v; mi = i; }
          });
          if (mi < 0) return null;
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
          <p className="text-muted mb-1 whitespace-nowrap">
            {stepSeconds && stepSeconds > 600
              ? `${fmtFull(hp.t)} ~ ${fmtTime(hp.t + stepSeconds)}`
              : `${fmtFull(hp.t)} 기준`}
            {hp.partial && (
              <span className="ml-1.5 rounded px-1 py-px text-[10px] font-semibold"
                    style={{ background: "rgba(148,163,184,0.18)" }}>집계 중</span>
            )}
          </p>
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
          {tooltipNote?.(hp) && (
            <p className="mt-1 border-t border-border pt-1 text-[11px] text-muted">
              {tooltipNote(hp)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
