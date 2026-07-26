"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BarChart3, ScatterChart, ChevronDown } from "lucide-react";
import type { RisingStreamer } from "@/lib/types";

// 랭킹 테이블 상단 요약 차트. 외부 차트 라이브러리 없이 SVG/div로 그린다
// (vis-network 도입 때 확인한 것처럼 차트 라이브러리는 번들이 크다).

const GREEN = "#00FFA3";
const CYAN  = "#06B6D4";
const PURPLE = "#A855F7";
const UP = "#10B981", DOWN = "#EF4444";
const nf = (n: number) => n.toLocaleString("ko-KR");
const DAY_MS = 24 * 3600 * 1000;

export interface ChartRow extends RisingStreamer { dur: { ms: number; label: string } }

// ── 컴포넌트 1: Top 10 수평 막대 ─────────────────────────────────────────────
function BarPanel({ rows, onPick }: { rows: ChartRow[]; onPick: (id: string) => void }) {
  const top = rows.slice(0, 10);
  const max = Math.max(1, ...top.map((r) => r.concurrent_viewers));

  return (
    <div className="space-y-1.5">
      {top.map((r, i) => {
        const pct = (r.concurrent_viewers / max) * 100;
        const delta = r.viewers_prev && r.viewers_prev > 0
          ? ((r.concurrent_viewers - r.viewers_prev) / r.viewers_prev) * 100 : null;
        return (
          <div key={r.chzzk_channel_id} className="group relative flex items-center gap-2">
            {/* Y축: 순위 + 프로필 + 닉네임 */}
            <span className="w-5 shrink-0 text-right text-[11px] tabular-nums text-muted">{i + 1}</span>
            <span className="h-6 w-6 shrink-0 overflow-hidden rounded-full bg-bg-hover">
              {r.channel_image_url && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={r.channel_image_url} alt="" width={24} height={24} loading="lazy" className="h-full w-full object-cover" />
              )}
            </span>
            <button type="button" onClick={() => onPick(r.chzzk_channel_id)}
              className="w-[104px] shrink-0 truncate text-left text-xs font-semibold text-fg hover:text-accent transition-colors">
              {r.channel_name}
            </button>

            {/* X축: 시청자 막대 */}
            <div className="h-3.5 flex-1 overflow-hidden rounded bg-bg-hover">
              <div className="h-full rounded transition-all"
                   style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${GREEN}, ${CYAN})` }} />
            </div>

            {/* 막대 우측: 변동률 + 시청자 수 */}
            <span className="flex w-[116px] shrink-0 items-center justify-end gap-1 text-right tabular-nums">
              {delta !== null && (
                <span className="text-[10px] font-semibold" style={{ color: delta >= 0 ? UP : DOWN }}>
                  {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}%
                </span>
              )}
              <span className="text-xs font-bold text-fg">{nf(r.concurrent_viewers)}명</span>
            </span>

            {/* 호버 툴팁 — 방송 시간 / 팔로워 상세 */}
            <div className="pointer-events-none absolute left-[140px] top-full z-30 mt-1 hidden whitespace-nowrap
                            rounded-lg border border-border bg-bg-card px-3 py-2 text-[11px] leading-relaxed
                            text-fg shadow-2xl group-hover:block">
              <b className="block">{r.channel_name}</b>
              방송 시간 {r.dur.label} · 팔로워 {r.follower_count > 0 ? `${nf(r.follower_count)}명` : "미집계"}
              {r.follower_prev24h != null && (
                <> · 24h 신규 <b style={{ color: GREEN }}>+{nf(Math.max(0, r.follower_count - r.follower_prev24h))}</b></>
              )}
              {r.category_name && <><br />카테고리 {r.category_name}</>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── 컴포넌트 2: 성장성 산점도 ────────────────────────────────────────────────
// X=시청자(체급), Y=팔로워 증가량(유입), 버블 크기=방송 시간.
// X는 시청자가 수십~수만으로 자릿수가 달라 log 스케일을 쓴다(선형이면 좌측에 뭉친다).
function ScatterPanel({ rows, onPick }: { rows: ChartRow[]; onPick: (id: string) => void }) {
  const [hover, setHover] = useState<string | null>(null);

  const pts = useMemo(() => {
    // 팔로워 증가량은 24시간 전 스냅샷이 있는 채널만 계산 가능하다
    const usable = rows.filter((r) => r.follower_prev24h != null && r.follower_count > 0);
    if (usable.length === 0) return null;
    const gains = usable.map((r) => r.follower_count - (r.follower_prev24h as number));
    const yMax = Math.max(1, ...gains), yMin = Math.min(0, ...gains);
    const xVals = usable.map((r) => Math.log10(Math.max(1, r.concurrent_viewers)));
    const xMax = Math.max(...xVals), xMin = Math.min(...xVals);
    const maxDur = Math.max(1, ...usable.map((r) => r.dur.ms));
    return usable.map((r, i) => ({
      r,
      gain: gains[i],
      x: (xVals[i] - xMin) / Math.max(0.0001, xMax - xMin),
      y: (gains[i] - yMin) / Math.max(1, yMax - yMin),
      rad: 7 + (Math.min(r.dur.ms, DAY_MS) / Math.min(maxDur, DAY_MS)) * 11,
    }));
  }, [rows]);

  const W = 720, H = 340, P = { l: 46, r: 16, t: 14, b: 34 };
  if (!pts) {
    return (
      <p className="py-12 text-center text-sm text-muted">
        팔로워 증가량을 계산할 24시간 전 데이터가 아직 없습니다.
      </p>
    );
  }
  const px = (v: number) => P.l + v * (W - P.l - P.r);
  const py = (v: number) => H - P.b - v * (H - P.t - P.b);
  const hp = pts.find((p) => p.r.chzzk_channel_id === hover);

  return (
    <div className="relative">
      <div className="overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ minWidth: 560, display: "block" }}>
          <defs>
            {pts.map((p) => (
              <clipPath key={p.r.chzzk_channel_id} id={`c-${p.r.chzzk_channel_id}`}>
                <circle cx={px(p.x)} cy={py(p.y)} r={p.rad} />
              </clipPath>
            ))}
          </defs>

          {/* 격자 + 축 */}
          {[0, 0.25, 0.5, 0.75, 1].map((g) => (
            <line key={g} x1={P.l} y1={py(g)} x2={W - P.r} y2={py(g)}
                  stroke="rgb(var(--color-border-rgb))" strokeWidth="1" opacity="0.5" />
          ))}
          <text x={P.l - 8} y={py(1)} textAnchor="end" fontSize="10" fill="rgb(var(--color-muted-rgb))">많음</text>
          <text x={P.l - 8} y={py(0) + 4} textAnchor="end" fontSize="10" fill="rgb(var(--color-muted-rgb))">적음</text>
          <text x={P.l} y={H - 10} fontSize="10" fill="rgb(var(--color-muted-rgb))">시청자 적음</text>
          <text x={W - P.r} y={H - 10} textAnchor="end" fontSize="10" fill="rgb(var(--color-muted-rgb))">시청자 많음 →</text>
          <text x={12} y={P.t + 8} fontSize="10" fill="rgb(var(--color-muted-rgb))">팔로워</text>
          <text x={12} y={P.t + 20} fontSize="10" fill="rgb(var(--color-muted-rgb))">증가량</text>

          {/* 버블 — 프로필 이미지를 원형 크롭해 넣는다 */}
          {pts.map((p) => {
            const on = hover === p.r.chzzk_channel_id;
            return (
              <g key={p.r.chzzk_channel_id} style={{ cursor: "pointer" }}
                 onMouseEnter={() => setHover(p.r.chzzk_channel_id)}
                 onMouseLeave={() => setHover(null)}
                 onClick={() => onPick(p.r.chzzk_channel_id)}>
                <circle cx={px(p.x)} cy={py(p.y)} r={p.rad + 2}
                        fill={on ? GREEN : "rgba(0,255,163,0.18)"} opacity={on ? 0.5 : 1} />
                {p.r.channel_image_url && (
                  <image href={p.r.channel_image_url}
                         x={px(p.x) - p.rad} y={py(p.y) - p.rad}
                         width={p.rad * 2} height={p.rad * 2}
                         clipPath={`url(#c-${p.r.chzzk_channel_id})`}
                         preserveAspectRatio="xMidYMid slice" />
                )}
                <circle cx={px(p.x)} cy={py(p.y)} r={p.rad} fill="none"
                        stroke={on ? "#fff" : GREEN} strokeWidth={on ? 2 : 1.5} />
              </g>
            );
          })}
        </svg>
      </div>

      {hp && (
        <div className="pointer-events-none absolute z-30 rounded-lg border border-border bg-bg-card
                        px-3 py-2 text-[11px] leading-relaxed text-fg shadow-2xl"
             style={{ left: `min(${(px(hp.x) / W) * 100}%, calc(100% - 200px))`, top: py(hp.y) + 16 }}>
          <b className="block">{hp.r.channel_name}</b>
          시청자 {nf(hp.r.concurrent_viewers)}명 · 방송 {hp.r.dur.label}<br />
          팔로워 {nf(hp.r.follower_count)}명 (24h <b style={{ color: hp.gain >= 0 ? GREEN : DOWN }}>
            {hp.gain >= 0 ? "+" : ""}{nf(hp.gain)}</b>)
        </div>
      )}

      <p className="mt-2 text-[11px] text-muted">
        * 우상향에 위치할수록 시청자 대비 팔로워 유입 증가율이 높은 성장세 스트리머입니다.
        버블이 클수록 방송 시간이 깁니다 · X축은 시청자 규모 차이가 커서 로그 스케일입니다 ·
        24시간 전 팔로워가 집계된 채널만 표시됩니다.
      </p>
    </div>
  );
}

// ── 래퍼: 접기/펼치기 + 차트 전환 ────────────────────────────────────────────
export default function RankingCharts({ rows }: { rows: ChartRow[] }) {
  const [open, setOpen] = useState(true);
  const [mode, setMode] = useState<"bar" | "scatter">("bar");
  const router = useRouter();
  const pick = (id: string) => router.push(`/stats/streamer/${id}`);

  const tabBtn = (active: boolean) =>
    "inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors " +
    (active ? "" : "hover:text-fg");

  return (
    <div className="card !p-4 md:!p-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <button type="button" onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 text-left" aria-expanded={open}>
          <h3 className="section-title">랭킹 요약 차트</h3>
          <ChevronDown size={15} className="text-muted transition-transform"
                       style={{ transform: open ? "none" : "rotate(-90deg)" }} />
          <span className="ml-1 text-[11px] text-muted">{open ? "접기" : "펼치기"}</span>
        </button>

        {open && (
          <div className="flex shrink-0 items-center gap-1.5">
            <button type="button" onClick={() => setMode("bar")} className={tabBtn(mode === "bar")}
              style={{ background: mode === "bar" ? "rgba(0,255,163,0.1)" : "transparent",
                       borderColor: mode === "bar" ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                       color: mode === "bar" ? GREEN : "rgb(var(--color-muted-rgb))" }}>
              <BarChart3 size={13} /> Top 10 시청자
            </button>
            <button type="button" onClick={() => setMode("scatter")} className={tabBtn(mode === "scatter")}
              style={{ background: mode === "scatter" ? "rgba(0,255,163,0.1)" : "transparent",
                       borderColor: mode === "scatter" ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                       color: mode === "scatter" ? GREEN : "rgb(var(--color-muted-rgb))" }}>
              <ScatterChart size={13} /> 성장성 분석
            </button>
          </div>
        )}
      </div>

      {/* max-height 전환으로 접는다 — 내용이 DOM에 남아 레이아웃 점프가 적다 */}
      <div className="overflow-hidden transition-[max-height] duration-300 ease-in-out"
           style={{ maxHeight: open ? 1200 : 0 }}>
        <p className="mb-3 mt-1 text-[11px] text-muted">
          {mode === "bar"
            ? "동시 시청자 상위 10명 · 막대에 마우스를 올리면 방송 시간과 팔로워를 볼 수 있습니다."
            : "체급(시청자) 대비 팔로워 유입을 한눈에 비교합니다."}
        </p>
        {rows.length === 0
          ? <p className="py-10 text-center text-sm text-muted">랭킹 데이터가 아직 없습니다.</p>
          : mode === "bar"
            ? <BarPanel rows={rows} onPick={pick} />
            : <ScatterPanel rows={rows} onPick={pick} />}
      </div>
    </div>
  );
}
