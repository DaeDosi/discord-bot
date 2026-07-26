"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Bot, BarChart3, ArrowLeft, ExternalLink, Loader2, Radio, Heart, Clock, Users, TrendingUp,
} from "lucide-react";
import { api } from "@/lib/api";
import type { StreamerDashboard, StreamerDetail, StreamerSessionSeries } from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import LineChart, { type LinePoint } from "../../LineChart";
import Heatmap, { HEAT_METRICS, type HeatMetric } from "./Heatmap";

const GREEN = "#00FFA3";
const CYAN  = "#00C2FF";
const GRAD  = `linear-gradient(135deg, ${GREEN}, ${CYAN})`;
const PURPLE = "#A855F7";
const nf = (n: number) => n.toLocaleString("ko-KR");
const CAT_PAL = ["#00FFA3", "#1fe6bd", "#2fccce", "#38b0e0", "#4a90e2", "#6b7688"];

const SUB_TABS = ["요약", "통계", "카테고리", "랭킹", "방송기록", "구간분석"] as const;
type SubTab = typeof SUB_TABS[number];

function GradText({ children }: { children: React.ReactNode }) {
  return <span style={{ background: GRAD, WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>{children}</span>;
}

// 서브 설명글 공통 — 작고 흐리게
function Sub({ children }: { children: React.ReactNode }) {
  return <p className="mt-1 text-[11px] leading-relaxed text-muted">{children}</p>;
}

function Kpi({ icon, label, value, unit, sub }:
  { icon: React.ReactNode; label: string; value: string; unit?: string; sub?: React.ReactNode }) {
  return (
    <div className="card !p-4">
      <p className="flex items-center gap-1.5 text-xs text-muted">{icon}{label}</p>
      <p className="mt-1.5 tracking-tight">
        <span className="text-xl md:text-2xl font-extrabold tabular-nums"><GradText>{value}</GradText></span>
        {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
      </p>
      {sub && <Sub>{sub}</Sub>}
    </div>
  );
}

// 수직 막대 그래프 (일별 최고/평균 시청자)
function BarPair({ rows }: { rows: { label: string; peak: number; avg: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.peak));
  return (
    <div className="overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      <div className="flex items-end gap-2" style={{ minWidth: rows.length * 26, height: 190 }}>
        {rows.map((r) => (
          <div key={r.label} className="group relative flex w-5 shrink-0 flex-col items-center justify-end gap-1" style={{ height: "100%" }}>
            <div className="relative flex h-full w-full items-end justify-center gap-[2px]">
              <div className="w-[7px] rounded-t-[2px]" style={{ height: `${(r.peak / max) * 100}%`, background: PURPLE, opacity: 0.75 }} />
              <div className="w-[7px] rounded-t-[2px]" style={{ height: `${(r.avg / max) * 100}%`, background: GRAD }} />
            </div>
            <span className="text-[9px] text-muted">{r.label}</span>
            <div className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1 hidden -translate-x-1/2 whitespace-nowrap
                            rounded border border-border bg-bg-card px-2 py-1 text-[10px] text-fg shadow-xl group-hover:block">
              최고 {nf(r.peak)}명 · 평균 {nf(r.avg)}명
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function StreamerPage() {
  const { channelId } = useParams<{ channelId: string }>();
  const [days, setDays] = useState(30);
  const [heatMetric, setHeatMetric] = useState<HeatMetric>("minutes");
  const [tab, setTab] = useState<SubTab>("요약");

  const [data, setData] = useState<StreamerDashboard | null>(null);
  const [detail, setDetail] = useState<StreamerDetail | null>(null);
  const [loading, setLoading] = useState(true);

  const [pickedSession, setPickedSession] = useState<{ start: number; end: number } | null>(null);
  const [series, setSeries] = useState<StreamerSessionSeries | null>(null);
  const [seriesLoading, setSeriesLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    // 두 요청을 분리한다. detail(시간대·세션·랭킹 추이)은 무거운데(실측 수 초)
    // Promise.all로 묶으면 가벼운 streamer(0.7초)까지 기다리게 돼 페이지 전체가 멈춰 보였다.
    // 이제 본문은 streamer가 오는 즉시 그려지고, detail은 해당 탭에서 개별로 채워진다.
    api.rising.streamer(channelId, days)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    setDetail(null);
    api.rising.streamerDetail(channelId, days)
      .then((dt) => { if (alive) setDetail(dt); })
      .catch(() => { if (alive) setDetail(null); });
    return () => { alive = false; };
  }, [channelId, days]);

  // 구간분석 — 선택된 방송 1건의 시청자 추이
  useEffect(() => {
    if (!pickedSession) { setSeries(null); return; }
    let alive = true;
    setSeriesLoading(true);
    api.rising.streamerSession(channelId, pickedSession.start, pickedSession.end)
      .then((s) => { if (alive) setSeries(s); })
      .catch(() => { if (alive) setSeries(null); })
      .finally(() => { if (alive) setSeriesLoading(false); });
    return () => { alive = false; };
  }, [channelId, pickedSession]);

  const s = data?.summary;
  const daily = data?.daily ?? [];
  const weekly = data?.weekly ?? [];

  // 직전 기간 대비 — daily를 반으로 갈라 후반/전반 평균을 비교한다
  const trend = useMemo(() => {
    if (daily.length < 4) return null;
    const half = Math.floor(daily.length / 2);
    const mean = (a: typeof daily, k: "avg_viewers" | "peak" | "viewership" | "minutes") =>
      a.reduce((x, d) => x + (d[k] as number), 0) / (a.length || 1);
    const pct = (cur: number, prev: number) => (prev > 0 ? Math.round((cur / prev - 1) * 100) : null);
    return {
      avg:   pct(mean(daily.slice(half), "avg_viewers"), mean(daily.slice(0, half), "avg_viewers")),
      peak:  pct(mean(daily.slice(half), "peak"),        mean(daily.slice(0, half), "peak")),
      vship: pct(mean(daily.slice(half), "viewership"),  mean(daily.slice(0, half), "viewership")),
      mins:  pct(mean(daily.slice(half), "minutes"),     mean(daily.slice(0, half), "minutes")),
    };
  }, [daily]);

  const deltaText = (p: number | null | undefined) =>
    p == null ? "직전 기간 비교 데이터 부족"
      : <>직전 기간 대비 <b style={{ color: p >= 0 ? GREEN : "#EF4444" }}>{p >= 0 ? "+" : ""}{p}%</b></>;

  const fmtDT = (t: number) => new Date(t * 1000).toLocaleString("ko-KR",
    { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  const fmtT = (t: number) => new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });

  const hourly = detail?.hourly ?? [];
  const maxHourAvg = Math.max(1, ...hourly.map((h) => h.avg_viewers));
  const sessions = detail?.sessions ?? [];
  const rankDaily = detail?.rank_daily ?? [];

  return (
    <div className="flex-1 flex flex-col">
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="w-full px-4 md:px-6 flex items-center justify-between" style={{ height: 60 }}>
          <div className="flex items-center gap-2.5">
            <Link href="/stats" className="flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors">
              <ArrowLeft size={16} /> 통계
            </Link>
            <span className="text-border">/</span>
            <span className="flex items-center gap-1.5 font-extrabold text-[15px]" style={{ color: GREEN }}>
              <BarChart3 size={16} /> 스트리머 분석
            </span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 md:px-6 py-6 space-y-5">
        {loading ? (
          <div className="flex items-center justify-center gap-2 text-muted py-24">
            <Loader2 size={18} className="animate-spin" /> 불러오는 중...
          </div>
        ) : !data || !data.found ? (
          <div className="card text-center py-16">
            <Radio size={34} className="mx-auto mb-3 opacity-30" style={{ color: GREEN }} />
            <p className="font-medium text-fg">최근 수집된 방송 데이터가 없습니다.</p>
            <Sub>이 채널이 라이브를 켜면 수집이 시작되고, 이후 분석이 표시됩니다.</Sub>
          </div>
        ) : (
          <>
            {/* ── 프로필 배너 ─────────────────────────────────────────── */}
            <div className="card !p-5">
              <div className="flex items-start gap-4 flex-wrap">
                <span className="h-16 w-16 shrink-0 overflow-hidden rounded-2xl bg-bg-hover"
                      style={{ boxShadow: data.is_live ? `0 0 0 2px ${GREEN}, 0 0 14px ${GREEN}55` : undefined }}>
                  {data.channel_image_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={data.channel_image_url} alt="" width={64} height={64} className="h-full w-full object-cover" />
                  )}
                </span>

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h1 className="truncate text-xl md:text-2xl font-extrabold tracking-tight">{data.channel_name}</h1>
                    {data.is_live
                      ? <span className="rounded px-1.5 py-0.5 text-[10px] font-bold" style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>LIVE</span>
                      : <span className="rounded px-1.5 py-0.5 text-[10px] font-bold text-muted" style={{ background: "rgb(var(--color-bg-hover-rgb))" }}>OFF</span>}
                  </div>
                  {data.live_title && <p className="mt-1 truncate text-sm text-muted">{data.live_title}</p>}
                  <Sub>
                    {data.first_broadcast
                      ? <>첫 방송(추정) {new Date(data.first_broadcast).toLocaleDateString("ko-KR")}</>
                      : <>첫 방송 정보 없음</>}
                    {" · "}팔로워 {nf(data.follower_count ?? 0)}명
                    {typeof data.history_days === "number" && <> · 수집 이력 {data.history_days}일</>}
                  </Sub>
                </div>

                <div className="flex shrink-0 items-center gap-2">
                  <a href={`https://chzzk.naver.com/${data.channel_id}`} target="_blank" rel="noopener noreferrer"
                     className="btn-secondary flex items-center gap-1 text-xs">
                    치지직 채널 <ExternalLink size={12} />
                  </a>
                  <a href={`https://chzzk.naver.com/${data.channel_id}`} target="_blank" rel="noopener noreferrer"
                     className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs font-bold text-[#04140d]"
                     style={{ background: GRAD }}>
                    <Heart size={12} /> 팔로우
                  </a>
                </div>
              </div>

              {/* 서브 탭 */}
              <div className="mt-4 flex gap-2 overflow-x-auto border-b border-border pb-3
                              [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                {SUB_TABS.map((t) => {
                  const active = tab === t;
                  return (
                    <button key={t} onClick={() => setTab(t)}
                      className="whitespace-nowrap rounded-full px-4 py-1.5 text-sm transition-colors"
                      style={active
                        ? { background: "rgba(0,255,163,0.12)", color: GREEN, fontWeight: 700 }
                        : { color: "rgb(var(--color-muted-rgb))" }}>
                      {t}
                    </button>
                  );
                })}
                <div className="ml-auto flex shrink-0 items-center gap-1">
                  {[7, 30, 90].map((d) => (
                    <button key={d} onClick={() => setDays(d)}
                      className="rounded-md border px-2 py-1 text-[11px] transition-colors"
                      style={{ background: days === d ? "rgba(0,255,163,0.1)" : "transparent",
                               borderColor: days === d ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                               color: days === d ? GREEN : "rgb(var(--color-muted-rgb))" }}>
                      {d}일
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* ── KPI 4열 ─────────────────────────────────────────────── */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Kpi icon={<Users size={12} />} label="평균 시청자" value={nf(s?.avg_viewers ?? 0)} unit="명"
                   sub={deltaText(trend?.avg)} />
              <Kpi icon={<TrendingUp size={12} />} label="최고 동시 시청자" value={nf(s?.peak_viewers ?? 0)} unit="명"
                   sub={deltaText(trend?.peak)} />
              <Kpi icon={<BarChart3 size={12} />} label="누적 뷰어쉽" value={nf(s?.viewership ?? 0)} unit="명·시간"
                   sub={<>시청자 수 × 방송 시간 · {deltaText(trend?.vship)}</>} />
              <Kpi icon={<Clock size={12} />} label="총 방송 시간" value={nf(s?.broadcast_hours ?? 0)} unit="시간"
                   sub={<>방송 {s?.active_days ?? 0}일 · {deltaText(trend?.mins)}</>} />
            </div>

            {/* ── ① 요약 ─────────────────────────────────────────────── */}
            {tab === "요약" && (
              <>
                <div className="card">
                  <div className="mb-3 flex items-start justify-between gap-3 flex-wrap">
                    <div>
                      <h3 className="section-title">활동 잔디</h3>
                      <Sub>최근 26주 · 칸이 진할수록 해당 지표가 높은 날입니다.</Sub>
                    </div>
                    <div className="flex shrink-0 items-center gap-1 flex-wrap">
                      {HEAT_METRICS.map((m) => (
                        <button key={m.k} onClick={() => setHeatMetric(m.k)}
                          className="rounded-md border px-2.5 py-1 text-[11px] transition-colors"
                          style={{ background: heatMetric === m.k ? "rgba(0,255,163,0.1)" : "transparent",
                                   borderColor: heatMetric === m.k ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                                   color: heatMetric === m.k ? GREEN : "rgb(var(--color-muted-rgb))" }}>
                          {m.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <Heatmap daily={daily} metric={heatMetric} />
                </div>

                <div className="card">
                  <h3 className="section-title">카테고리 점유율</h3>
                  <Sub>스냅샷 수 기준 — 방송 시간을 어느 카테고리에 배분했는지 보여 줍니다.</Sub>
                  {(s?.categories ?? []).length === 0 ? (
                    <p className="py-8 text-center text-sm text-muted">카테고리 데이터가 아직 없습니다.</p>
                  ) : (
                    <div className="mt-4 space-y-2.5">
                      {(s?.categories ?? []).map((c, i) => (
                        <div key={c.category}>
                          <div className="flex items-center justify-between gap-2 text-xs">
                            <span className="flex min-w-0 items-center gap-2">
                              <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: CAT_PAL[i % CAT_PAL.length] }} />
                              <span className="truncate text-fg">{c.category}</span>
                            </span>
                            <span className="shrink-0 tabular-nums font-semibold text-fg">{c.share.toFixed(1)}%</span>
                          </div>
                          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bg-hover">
                            <div className="h-full rounded-full" style={{ width: `${c.share}%`, background: CAT_PAL[i % CAT_PAL.length] }} />
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}

            {/* ── ② 통계 ─────────────────────────────────────────────── */}
            {tab === "통계" && (
              <>
                <div className="card">
                  <h3 className="section-title">일별 시청자 추이</h3>
                  <Sub>보라 막대 = 최고 시청자, 그린 막대 = 평균 시청자. 방송이 있던 날만 표시됩니다.</Sub>
                  {daily.length === 0 ? <p className="py-10 text-center text-sm text-muted">데이터가 아직 없습니다.</p> : (
                    <div className="mt-4">
                      <BarPair rows={daily.map((d) => ({ label: d.date.slice(5), peak: d.peak, avg: d.avg_viewers }))} />
                    </div>
                  )}
                </div>

                <div className="card">
                  <h3 className="section-title">시간대별 유입 분석</h3>
                  <Sub>그 시간대에 방송을 켰을 때의 평균 시청자입니다. 방송한 시간대만 나옵니다.</Sub>
                  {hourly.length === 0 ? <p className="py-10 text-center text-sm text-muted">시간대 데이터가 아직 없습니다.</p> : (
                    <div className="mt-4 space-y-1.5">
                      {hourly.map((h) => (
                        <div key={h.hour} className="flex items-center gap-2">
                          <span className="w-12 shrink-0 text-right text-[11px] tabular-nums text-muted">
                            {String(h.hour).padStart(2, "0")}시
                          </span>
                          <div className="h-3 flex-1 overflow-hidden rounded bg-bg-hover">
                            <div className="h-full rounded" style={{ width: `${(h.avg_viewers / maxHourAvg) * 100}%`, background: GRAD }} />
                          </div>
                          <span className="w-24 shrink-0 text-right text-[11px] tabular-nums text-muted">
                            {nf(h.avg_viewers)}명 · {h.hours}h
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {weekly.length > 1 && (
                  <div className="card">
                    <h3 className="section-title">주별 추이</h3>
                    <Sub>주 단위 평균 시청자와 누적 뷰어쉽입니다.</Sub>
                    <div className="mt-4">
                      <LineChart points={weekly as unknown as LinePoint[]} area dynamicY unit="명"
                        series={[{ key: "avg_viewers", name: "평균 시청자", color: GREEN, gradient: [GREEN, CYAN] }]}
                        tooltipItems={(p) => [
                          { label: "평균 시청자", value: `${nf(p.avg_viewers)}명`, color: GREEN },
                          { label: "뷰어쉽", value: `${nf(p.viewership)}명·시간`, color: CYAN },
                        ]} />
                    </div>
                  </div>
                )}
              </>
            )}

            {/* ── ③ 카테고리 ─────────────────────────────────────────── */}
            {tab === "카테고리" && (
              <div className="card">
                <h3 className="section-title">카테고리별 성과</h3>
                <Sub>누적 방송 시간과 점유율. 스냅샷 1개를 10분으로 환산했습니다.</Sub>
                {(s?.categories ?? []).length === 0 ? (
                  <p className="py-10 text-center text-sm text-muted">카테고리 데이터가 아직 없습니다.</p>
                ) : (
                  <div className="mt-4 overflow-x-auto">
                    <table className="w-full text-sm min-w-[520px]">
                      <thead>
                        <tr className="border-b border-border text-xs text-muted">
                          <th className="w-10 py-2 pl-2 text-left font-medium">#</th>
                          <th className="py-2 text-left font-medium">카테고리</th>
                          <th className="py-2 px-6 text-right font-medium">점유율</th>
                          <th className="py-2 px-6 text-right font-medium">누적 방송 시간</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(s?.categories ?? []).map((c, i) => (
                          <tr key={c.category} className="border-b border-border hover:bg-bg-hover/70 transition-colors">
                            <td className="py-3 pl-2 text-xs tabular-nums text-muted">{i + 1}</td>
                            <td className="py-3">
                              <span className="flex items-center gap-2">
                                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: CAT_PAL[i % CAT_PAL.length] }} />
                                <span className="truncate text-fg">{c.category}</span>
                              </span>
                            </td>
                            <td className="py-3 px-6 text-right tabular-nums font-semibold text-fg">{c.share.toFixed(1)}%</td>
                            <td className="py-3 px-6 text-right tabular-nums text-muted">
                              {(c.snapshots * 10 / 60).toFixed(1)}시간
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* ── ④ 랭킹 ─────────────────────────────────────────────── */}
            {tab === "랭킹" && (
              <div className="card">
                <h3 className="section-title">체급 순위 추이</h3>
                <Sub>
                  그날 방송한 전체 채널 중 평균 시청자 기준 순위입니다. 숫자가 작을수록 상위입니다.
                  롤업 보관 기간(기본 8일) 안의 날짜만 계산됩니다.
                </Sub>
                {rankDaily.length === 0 ? (
                  <p className="py-10 text-center text-sm text-muted">순위를 계산할 데이터가 아직 없습니다.</p>
                ) : (
                  <>
                    <div className="mt-4 overflow-x-auto">
                      <table className="w-full text-sm min-w-[520px]">
                        <thead>
                          <tr className="border-b border-border text-xs text-muted">
                            <th className="py-2 pl-2 text-left font-medium">날짜</th>
                            <th className="py-2 px-6 text-right font-medium">순위</th>
                            <th className="py-2 px-6 text-right font-medium">상위 %</th>
                            <th className="py-2 px-6 text-right font-medium">평균 시청자</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...rankDaily].reverse().map((r) => (
                            <tr key={r.date} className="border-b border-border hover:bg-bg-hover/70 transition-colors">
                              <td className="py-3 pl-2 tabular-nums text-fg">{r.date}</td>
                              <td className="py-3 px-6 text-right tabular-nums font-bold text-fg">
                                {nf(r.rank)}<span className="ml-0.5 text-[11px] font-normal text-muted">/ {nf(r.total)}</span>
                              </td>
                              <td className="py-3 px-6 text-right tabular-nums font-semibold" style={{ color: GREEN }}>
                                {r.percentile != null ? `상위 ${r.percentile}%` : "-"}
                              </td>
                              <td className="py-3 px-6 text-right tabular-nums text-muted">{nf(r.avg_viewers)}명</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <Sub>
                      팔로워 증가 랭킹은 아직 제공하지 않습니다 — 팔로워는 시청자 상위 채널만 주기적으로
                      보강하므로 전체 채널의 증가량을 같은 기준으로 비교할 수 없습니다.
                    </Sub>
                  </>
                )}
              </div>
            )}

            {/* ── ⑤ 방송기록 ─────────────────────────────────────────── */}
            {tab === "방송기록" && (
              <div className="card">
                <h3 className="section-title">방송 기록</h3>
                <Sub>
                  연속된 수집 구간을 하나의 방송으로 묶은 타임라인입니다. 수집이 1시간 단위라
                  시작·종료 시각은 시(時) 단위 근사입니다. 행을 클릭하면 구간분석으로 넘어갑니다.
                </Sub>
                {sessions.length === 0 ? (
                  <p className="py-10 text-center text-sm text-muted">방송 기록이 아직 없습니다.</p>
                ) : (
                  <div className="mt-4 space-y-2">
                    {sessions.map((v) => (
                      <button key={v.start} type="button"
                        onClick={() => { setPickedSession({ start: v.start, end: v.end }); setTab("구간분석"); }}
                        className="w-full rounded-xl border border-border p-3 text-left transition-colors hover:bg-bg-hover/70">
                        <div className="flex items-center justify-between gap-3 flex-wrap">
                          <span className="text-sm font-semibold text-fg tabular-nums">
                            {fmtDT(v.start)} ~ {fmtT(v.end)}
                          </span>
                          <span className="flex items-center gap-2 flex-wrap">
                            {v.categories.map((c) => (
                              <span key={c} className="rounded-full border border-border bg-bg-hover px-2.5 py-0.5 text-[11px] text-fg">{c}</span>
                            ))}
                          </span>
                        </div>
                        <Sub>
                          {v.hours}시간 방송 · 최고 {nf(v.peak_viewers)}명 · 평균 {nf(v.avg_viewers)}명 ·
                          뷰어쉽 {nf(v.viewership)}명·시간
                        </Sub>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* ── ⑥ 구간분석 ─────────────────────────────────────────── */}
            {tab === "구간분석" && (
              <div className="card">
                <h3 className="section-title">구간 분석</h3>
                <Sub>
                  방송 1건의 시청자 변화입니다. 최근 방송은 10분 간격 원본으로, 그보다 오래된 방송은
                  1시간 간격 집계로 표시됩니다(원본 보관 기간이 지나면 10분 해상도가 남지 않습니다).
                </Sub>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <select
                    value={pickedSession ? String(pickedSession.start) : ""}
                    onChange={(e) => {
                      const v = sessions.find((x) => String(x.start) === e.target.value);
                      setPickedSession(v ? { start: v.start, end: v.end } : null);
                    }}
                    className="max-w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm text-fg focus:border-accent focus:outline-none">
                    <option value="">방송을 선택하세요…</option>
                    {sessions.map((v) => (
                      <option key={v.start} value={v.start}>
                        {fmtDT(v.start)} ~ {fmtT(v.end)} ({v.hours}h, 최고 {nf(v.peak_viewers)}명)
                      </option>
                    ))}
                  </select>
                  {series && (
                    <span className="rounded-full border border-border bg-bg-hover px-2.5 py-0.5 text-[11px] text-muted">
                      해상도 {series.resolution === "10m" ? "10분" : "1시간"}
                    </span>
                  )}
                </div>

                <div className="mt-4">
                  {!pickedSession ? (
                    <p className="py-12 text-center text-sm text-muted">위에서 방송을 선택하면 시청자 추이가 표시됩니다.</p>
                  ) : seriesLoading ? (
                    <div className="flex items-center justify-center gap-2 py-12 text-muted">
                      <Loader2 size={18} className="animate-spin" /> 구간을 불러오는 중...
                    </div>
                  ) : !series || series.points.length < 2 ? (
                    <p className="py-12 text-center text-sm text-muted">이 구간의 상세 데이터가 남아 있지 않습니다.</p>
                  ) : (
                    <LineChart
                      points={series.points.map((p) => ({ t: p.t, viewers: p.viewers })) as unknown as LinePoint[]}
                      series={[{ key: "viewers", name: "시청자", color: GREEN, gradient: [GREEN, CYAN] }]}
                      area dynamicY showPeak unit="명" height={260} />
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
