"use client";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { ViewerBand, HeatCell, TitleKeyword } from "@/lib/types";

// 전체 스트리머 분석 탭의 시각화 3종. 각자 필요한 데이터만 스스로 받아온다
// (개요 5개 API의 Promise.all에 끼우면 초기 로딩이 그만큼 늦어진다).

const GREEN = "#00FFA3";
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

  // 색 단계를 배열 하나로 모은다 — **범례가 이 배열을 그대로 쓰기 위해서**다.
  // 두 곳에 따로 적어 두면 단계를 손볼 때 반드시 어긋난다.
  const HEAT_STEPS = ["rgba(0,255,163,0.10)", "rgba(0,255,163,0.24)",
                      "rgba(0,255,163,0.42)", "rgba(0,255,163,0.66)", GREEN];
  const EMPTY_CELL = "rgb(var(--color-bg-hover-rgb))";

  const shade = (c: HeatCell) => {
    if (c.samples === 0) return EMPTY_CELL;
    const r = c.avg_viewers / max;
    return r >= 0.8 ? HEAT_STEPS[4]
         : r >= 0.6 ? HEAT_STEPS[3]
         : r >= 0.4 ? HEAT_STEPS[2]
         : r >= 0.2 ? HEAT_STEPS[1]
         : HEAT_STEPS[0];
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
            {/* 라벨을 키운 만큼 최소 폭도 함께 올린다 — 좁은 화면에서 시간 숫자끼리
                겹치지 않게 하려면 셀 폭이 같이 확보돼야 한다. */}
            <div style={{ minWidth: 640 }}>
              {grid.map((row, d) => (
                <div key={d} className="mb-[2px] flex items-center gap-[2px]">
                  {/* 요일 라벨 — 셀과 세로 중심을 맞추고 오른쪽 정렬해
                      요일과 첫 셀 사이 간격이 일정해진다. */}
                  <span className="w-[30px] shrink-0 pr-1.5 text-right text-[12px] font-medium leading-none"
                        style={{ color: GRAY }}>{DOW[d]}</span>
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

              {/* 시간 라벨 — 그리드 **아래**로 내렸다. 위에 있으면 요일 라벨과
                  같은 줄에서 시작점이 어긋나 보이고, 값을 읽을 때 시선이
                  위아래로 두 번 움직인다. 요일 라벨과 같은 폭(30px)을 앞에 두어
                  첫 셀과 '0'의 중심이 정확히 맞는다. */}
              <div className="mt-1.5 flex gap-[2px]">
                <span className="w-[30px] shrink-0" aria-hidden="true" />
                {Array.from({ length: 24 }, (_, h) => (
                  <span key={h} className="flex-1 text-center text-[11px] tabular-nums leading-none"
                        style={{ color: GRAY }}>{h % 3 === 0 ? h : ""}</span>
                ))}
              </div>
            </div>
          </div>

          {/* 범례 — 오른쪽 아래. 색 단계는 `HEAT_STEPS`를 그대로 쓰므로 셀과 항상 같다.
              색만으로 뜻을 전하지 않도록 '낮음'·'높음' 글자를 양쪽에 둔다. */}
          <div className="mt-3 flex flex-wrap items-center justify-end gap-x-2 gap-y-1.5">
            <span className="text-[11px]" style={{ color: GRAY }}>낮음</span>
            <span className="flex items-center gap-[3px]"
                  role="img" aria-label="색이 짙을수록 평균 시청자가 많습니다">
              {HEAT_STEPS.map((c, i) => (
                <span key={i} className="h-3 w-3 rounded-[2px]" style={{ background: c }} />
              ))}
            </span>
            <span className="text-[11px]" style={{ color: GRAY }}>높음</span>
            <span className="ml-2 flex items-center gap-1.5 text-[11px]" style={{ color: GRAY }}>
              <span className="h-3 w-3 rounded-[2px]" style={{ background: EMPTY_CELL }} />
              수집 없음
            </span>
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
    // 색은 전부 테마 토큰을 쓴다 — 예전엔 PANEL(#181A20)/흰 글씨/흰색 반투명 트랙 같은
    // 고정 다크 값이라 라이트 모드에서 이 카드만 검은 패널로 남았다.
    <div className="card flex h-full flex-col">
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
          <div className="mt-4 flex flex-1 flex-col justify-between gap-1.5">
            {kws.map((k, i) => (
              <div key={k.keyword} className="flex items-center gap-2.5 rounded-lg bg-bg-hover p-2">
                <span className="w-5 shrink-0 text-right text-xs font-extrabold tabular-nums"
                      style={{ color: i < 3 ? GREEN : undefined }}
                      >{i + 1}</span>
                {/* 태그는 고정폭 안에서 가운데 정렬 — 왼쪽으로 쏠려 보이던 것을 맞춘다 */}
                <span className="flex w-[104px] shrink-0 items-center justify-center truncate rounded-full
                                 border px-2.5 py-1 text-xs font-medium text-fg"
                      style={{ borderColor: "rgba(0,255,163,0.30)", background: "rgba(0,255,163,0.10)" }}
                      title={k.keyword}>
                  {k.keyword}
                </span>
                <span className="h-2 flex-1 overflow-hidden rounded-full bg-bg">
                  <span className="block h-full rounded-full"
                        style={{ width: `${(k.lives / max) * 100}%`,
                                 background: `linear-gradient(90deg, ${GREEN}, #06B6D4)` }} />
                </span>
                <span className="w-[92px] shrink-0 text-right text-xs tabular-nums text-muted">
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
