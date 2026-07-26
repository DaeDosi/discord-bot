"use client";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ViewerBand, HeatCell, TitleKeyword } from "@/lib/types";

// 전체 스트리머 분석 탭의 시각화 3종. 각자 필요한 데이터만 스스로 받아온다
// (개요 5개 API의 Promise.all에 끼우면 초기 로딩이 그만큼 늦어진다).

const GREEN = "#00FFA3";
const PANEL = "#181A20";
const GRAY = "#9CA3AF";
const nf = (n: number) => n.toLocaleString("ko-KR");
const Sub = ({ children }: { children: React.ReactNode }) =>
  <p className="mt-3 text-[11px] leading-relaxed" style={{ color: GRAY }}>{children}</p>;

// ── ① 시청자 체급별 분포 ────────────────────────────────────────────────────
export function ViewerDistribution() {
  const [bands, setBands] = useState<ViewerBand[] | null>(null);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    let alive = true;
    api.rising.viewerDistribution()
      .then((d) => { if (alive) { setBands(d.bands || []); setTotal(d.total || 0); } })
      .catch(() => { if (alive) setBands([]); });
    return () => { alive = false; };
  }, []);

  const max = Math.max(1, ...(bands ?? []).map((b) => b.channels));

  return (
    <div className="card">
      <h3 className="section-title">시청자 체급별 스트리머 분포</h3>
      <p className="mt-1 text-sm text-muted">
        지금 방송 중인 채널이 어느 규모 구간에 몰려 있는지 보여 줍니다.
      </p>

      {!bands ? (
        <div className="flex items-center gap-2 py-10 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" /> 불러오는 중...
        </div>
      ) : bands.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted">데이터가 아직 없습니다.</p>
      ) : (
        <div className="mt-4 space-y-2.5">
          {bands.map((b) => (
            <div key={b.label} className="flex items-center gap-3">
              <span className="w-[74px] shrink-0 text-right text-xs text-muted">{b.label}</span>
              <div className="h-4 flex-1 overflow-hidden rounded bg-bg-hover">
                <div className="h-full rounded transition-all"
                     style={{ width: `${(b.channels / max) * 100}%`,
                              background: `linear-gradient(90deg, ${GREEN}, #06B6D4)` }} />
              </div>
              <span className="w-[110px] shrink-0 text-right text-xs tabular-nums">
                <b className="text-fg">{nf(b.channels)}</b>
                <span className="text-muted">개 · {b.share}%</span>
              </span>
            </div>
          ))}
          <Sub>* 최근 수집 사이클의 라이브 방송 {nf(total)}개 기준입니다.</Sub>
        </div>
      )}
    </div>
  );
}

// ── ② 요일×시간대 트래픽 히트맵 ─────────────────────────────────────────────
const DOW = ["월", "화", "수", "목", "금", "토", "일"];

export function TrafficHeatmap() {
  const [grid, setGrid] = useState<HeatCell[][] | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);

  useEffect(() => {
    let alive = true;
    api.rising.trafficHeatmap(14)
      .then((d) => { if (alive) setGrid(d.grid || []); })
      .catch(() => { if (alive) setGrid([]); });
    return () => { alive = false; };
  }, []);

  const max = Math.max(1, ...(grid ?? []).flat().map((c) => c.avg_viewers));
  const shade = (c: HeatCell) => {
    if (c.samples === 0) return "rgb(var(--color-bg-hover-rgb))";
    const r = c.avg_viewers / max;
    return r >= 0.8 ? GREEN
         : r >= 0.6 ? "rgba(0,255,163,0.66)"
         : r >= 0.4 ? "rgba(0,255,163,0.42)"
         : r >= 0.2 ? "rgba(0,255,163,0.24)"
         : "rgba(0,255,163,0.10)";
  };

  return (
    <div className="card">
      <h3 className="section-title">요일·시간대별 트래픽</h3>
      <p className="mt-1 text-sm text-muted">
        플랫폼 전체 동시 시청자의 요일×시간대 평균입니다. 짙을수록 시청자가 많습니다.
      </p>

      {!grid ? (
        <div className="flex items-center gap-2 py-10 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" /> 불러오는 중...
        </div>
      ) : grid.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted">집계할 수집 이력이 아직 없습니다.</p>
      ) : (
        <>
          <div className="mt-4 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            <div style={{ minWidth: 560 }}>
              {/* 시간 라벨 */}
              <div className="mb-1 flex gap-[2px] pl-[26px]">
                {Array.from({ length: 24 }, (_, h) => (
                  <span key={h} className="flex-1 text-center text-[9px] tabular-nums"
                        style={{ color: GRAY }}>{h % 3 === 0 ? h : ""}</span>
                ))}
              </div>
              {grid.map((row, d) => (
                <div key={d} className="mb-[2px] flex items-center gap-[2px]">
                  <span className="w-[24px] shrink-0 text-[10px]" style={{ color: GRAY }}>{DOW[d]}</span>
                  {row.map((c, h) => (
                    <div key={h} className="h-4 flex-1 cursor-pointer rounded-[2px] transition-transform hover:scale-125"
                         style={{ background: shade(c) }}
                         onMouseEnter={(e) => {
                           const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                           setTip({ x: r.left + r.width / 2, y: r.top,
                                    text: c.samples === 0
                                      ? `${DOW[d]} ${String(h).padStart(2, "0")}시: 수집 없음`
                                      : `${DOW[d]} ${String(h).padStart(2, "0")}시: 평균 ${nf(c.avg_viewers)}명` });
                         }}
                         onMouseLeave={() => setTip(null)} />
                  ))}
                </div>
              ))}
            </div>
          </div>
          <Sub>* 최근 14일 수집 이력 기준이며, 회색은 해당 시간대 수집 기록이 없다는 뜻입니다.</Sub>
        </>
      )}

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

// ── ③ 인기 방송 제목 키워드 TOP 10 ──────────────────────────────────────────
export function TitleKeywordRank() {
  const [kws, setKws] = useState<TitleKeyword[] | null>(null);

  useEffect(() => {
    let alive = true;
    api.rising.titleKeywords(10)
      .then((d) => { if (alive) setKws(d.keywords || []); })
      .catch(() => { if (alive) setKws([]); });
    return () => { alive = false; };
  }, []);

  const max = Math.max(1, ...(kws ?? []).map((k) => k.lives));

  return (
    <div className="card">
      <h3 className="section-title">인기 방송 제목 키워드</h3>
      <p className="mt-1 text-sm text-muted">현재 라이브 제목에서 가장 많이 쓰인 단어입니다.</p>

      {!kws ? (
        <div className="flex items-center gap-2 py-10 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" /> 불러오는 중...
        </div>
      ) : kws.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted">추출할 제목 데이터가 아직 없습니다.</p>
      ) : (
        <>
          <div className="mt-4 space-y-1.5">
            {kws.map((k, i) => (
              <div key={k.keyword} className="flex items-center gap-2.5 rounded-lg p-2"
                   style={{ background: PANEL }}>
                <span className="w-5 shrink-0 text-right text-[11px] font-extrabold tabular-nums"
                      style={{ color: i < 3 ? GREEN : GRAY }}>{i + 1}</span>
                <span className="w-[92px] shrink-0 truncate rounded-full border px-2.5 py-0.5 text-xs font-medium"
                      style={{ color: "#fff", borderColor: "rgba(0,255,163,0.25)", background: "rgba(0,255,163,0.08)" }}
                      title={k.keyword}>
                  {k.keyword}
                </span>
                <span className="h-2 flex-1 overflow-hidden rounded-full" style={{ background: "rgba(255,255,255,0.06)" }}>
                  <span className="block h-full rounded-full"
                        style={{ width: `${(k.lives / max) * 100}%`,
                                 background: `linear-gradient(90deg, ${GREEN}, #06B6D4)` }} />
                </span>
                <span className="w-[86px] shrink-0 text-right text-[11px] tabular-nums" style={{ color: GRAY }}>
                  {nf(k.lives)}개 · {nf(k.avg_viewers)}명
                </span>
              </div>
            ))}
          </div>
          <Sub>
            * 최근 수집 사이클 기준입니다. 한 제목에서 같은 단어는 한 번만 세고,
            방송 3개 이상에서 쓰인 단어만 표시합니다.
          </Sub>
        </>
      )}
    </div>
  );
}
