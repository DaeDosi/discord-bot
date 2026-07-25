"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Bot, BarChart3, LineChart as LineIcon, ListOrdered, Gamepad2, Radio,
  TrendingUp, Loader2, Search, Circle, Sprout, Lock,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  RisingOverview, RisingTimeseries, RisingLiveRanking, RisingCategories, RisingCategory,
  RisingStars, TimeRange, CatRange, RisingSearchResult, RisingNewcomers,
} from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";
import LineChart, { type LinePoint, type LineSeries } from "./LineChart";

// 2톤 그라데이션(그린 → 시안) — 브랜드 액센트
const GREEN = "#00FFA3";
const CYAN  = "#00C2FF";
const GRAD  = `linear-gradient(135deg, ${GREEN}, ${CYAN})`;
const PEAK_GRAD = "linear-gradient(135deg, #FF4FA3, #A855F7)"; // 피크(핑크→퍼플) 2톤
const YELLOW_GRAD = "linear-gradient(135deg, #FDE047, #F59E0B)"; // 현재 시청자(노랑→앰버) 2톤 — 증감 초록/빨강과 구분

const nf = (n: number) => n.toLocaleString("ko-KR");

// 그라데이션 텍스트 (grad 미지정 시 브랜드 그린→시안)
function GradText({ children, className, grad = GRAD }: { children: React.ReactNode; className?: string; grad?: string }) {
  return (
    <span className={className}
      style={{ background: grad, WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>
      {children}
    </span>
  );
}

// 증감 뱃지 (null이면 '-')
function Delta({ pct }: { pct: number | null | undefined }) {
  if (pct === null || pct === undefined || !isFinite(pct)) return <span className="text-muted">–</span>;
  const up = pct >= 0;
  return (
    <span className="font-semibold tabular-nums" style={{ color: up ? GREEN : "#F87171" }}>
      {up ? "▲" : "▼"} {Math.abs(pct).toFixed(1)}%
    </span>
  );
}

type Tab = "overview" | "ranking" | "category" | "newcomers";
const TABS: { key: Tab; label: string; desc: string; icon: React.ReactNode }[] = [
  { key: "overview",  label: "실시간 분석", desc: "추이·요약",    icon: <LineIcon size={17} /> },
  { key: "newcomers", label: "신규 스트리머 분석", desc: "하꼬·신입 발굴", icon: <Sprout size={17} /> },
  { key: "ranking",   label: "랭킹",       desc: "실시간 방송",   icon: <ListOrdered size={17} /> },
  { key: "category",  label: "카테고리",   desc: "게임별 현황",   icon: <Gamepad2 size={17} /> },
];

// 방송시간(KST 문자열 → 경과) 계산
function liveDuration(openDate: string): { ms: number; label: string } {
  if (!openDate) return { ms: -1, label: "-" };
  const iso = openDate.replace(" ", "T") + "+09:00";
  const start = new Date(iso).getTime();
  if (isNaN(start)) return { ms: -1, label: "-" };
  const ms = Date.now() - start;
  if (ms < 0) return { ms: -1, label: "-" };
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return { ms, label: h > 0 ? `${h}시간 ${m}분` : `${m}분` };
}

// KPI 증감: 절대 변화량 + 변동률 (예: ▲ 12,340명(+2.5%))
function KpiDelta({ pct, cur, unit }: { pct?: number | null; cur?: number; unit?: string }) {
  if (pct === null || pct === undefined || !isFinite(pct)) return <span className="text-muted">–</span>;
  const up = pct >= 0;
  let absPart = "";
  if (cur != null && 1 + pct / 100 !== 0) {
    const change = Math.round(cur - cur / (1 + pct / 100));
    absPart = `${nf(Math.abs(change))}${unit ?? ""}`;
  }
  return (
    <span className="font-semibold tabular-nums" style={{ color: up ? GREEN : "#F87171" }}>
      {up ? "▲" : "▼"} {absPart}({up ? "+" : "-"}{Math.abs(pct).toFixed(1)}%)
    </span>
  );
}

function StatTile({ label, value, unit, sub, accent, deltaPrev, rawValue }:
  { label: string; value: string; unit?: string; sub?: string; accent?: boolean;
    deltaPrev?: number | null; rawValue?: number }) {
  return (
    <div className="card !p-5">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1.5 tracking-tight">
        <span className="text-xl md:text-2xl font-extrabold tabular-nums">
          {accent ? <GradText>{value}</GradText> : value}
        </span>
        {unit && <span className="text-sm text-muted font-normal ml-1">{unit}</span>}
      </p>
      {deltaPrev !== undefined ? (
        <p className="flex items-center gap-1 mt-2 text-xs">
          <KpiDelta pct={deltaPrev} cur={rawValue} unit={unit} /><span className="text-muted">직전 대비</span>
        </p>
      ) : sub ? <p className="text-xs text-muted mt-1.5">{sub}</p> : null}
    </div>
  );
}

type Metric = "viewers" | "lives";

const RANGE_DESC = "최근 6시간 · 10분 간격";
const METRIC_OPTS: { k: Metric; label: string }[] = [
  { k: "viewers", label: "시청자" },
  { k: "lives",   label: "라이브" },
];

// 스트리머 검색 — 랭킹에 없는 스트리머도 검색해 개인 분석으로 이동
function StreamerSearch() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<RisingSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const kw = q.trim();
    if (kw.length < 1) { setResults([]); setOpen(false); return; }
    const id = setTimeout(() => {
      setLoading(true);
      api.rising.search(kw)
        .then((r) => { setResults(r.results || []); setOpen(true); })
        .catch(() => setResults([]))
        .finally(() => setLoading(false));
    }, 350);
    return () => clearTimeout(id);
  }, [q]);

  useEffect(() => {
    const onClick = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  return (
    <div className="relative mb-5" ref={ref}>
      <p className="text-xs font-semibold text-muted/70 uppercase tracking-wider px-1 mb-2">스트리머 검색</p>
      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => results.length > 0 && setOpen(true)}
          placeholder="채널명으로 검색"
          className="w-full bg-bg border border-border rounded-lg pl-9 pr-8 py-2 text-sm text-fg placeholder-muted focus:outline-none focus:border-accent" />
        {loading && <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted animate-spin" />}
      </div>
      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-bg-card border border-border rounded-xl shadow-xl py-1.5 max-h-80 overflow-y-auto">
          {results.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted">{loading ? "검색 중..." : "결과가 없습니다."}</p>
          ) : results.map((c) => (
            <Link key={c.channel_id} href={`/stats/streamer/${c.channel_id}`} onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 px-3 py-2 mx-1.5 rounded-lg hover:bg-bg-hover transition-colors">
              <span className="w-7 h-7 rounded-full overflow-hidden bg-bg-hover shrink-0">
                {c.channel_image_url && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={c.channel_image_url} alt="" width={28} height={28} className="w-full h-full object-cover" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="text-sm font-semibold text-fg truncate flex items-center gap-1.5">
                  {c.channel_name}
                  {c.open_live && <span className="text-[9px] font-bold px-1 rounded" style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>LIVE</span>}
                </span>
                <span className="block text-[11px] text-muted">팔로워 {nf(c.follower_count)}</span>
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// 범용 세그먼트 버튼 그룹 (지표 필터 / 기간 선택 공용)
function Seg<T extends string>({ options, value, onChange }:
  { options: { k: T; label: string }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="flex items-center gap-1">
      {options.map((o) => {
        const active = value === o.k;
        return (
          <button key={o.k} onClick={() => onChange(o.k)}
            className="text-xs px-2.5 py-1 rounded-md border transition-colors"
            style={{ background: active ? "rgba(0,255,163,0.1)" : "transparent",
                     borderColor: active ? "rgba(0,194,255,0.4)" : "rgb(var(--color-border-rgb))",
                     color: active ? GREEN : "rgb(var(--color-muted-rgb))" }}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function ChartSkeleton({ height = 220 }: { height?: number }) {
  return <div className="rounded-lg bg-bg-hover/50 animate-pulse" style={{ height }} />;
}

// 24시간 미만 축적 상태의 급상승 섹션 — 진행률 바 + 스켈레톤
function RisingSkeleton({ historyHours }: { historyHours: number }) {
  const pctv = Math.min(100, Math.round((historyHours / 24) * 100));
  const remain = Math.max(0, 24 - historyHours);
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-2">
        <span className="text-muted">데이터 수집 중… 약 {remain.toFixed(1)}시간 후 표시됩니다</span>
        <span className="tabular-nums font-semibold" style={{ color: GREEN }}>{pctv}%</span>
      </div>
      <div className="h-2 rounded-full bg-bg-hover overflow-hidden mb-5">
        <div className="h-full rounded-full transition-all" style={{ width: `${pctv}%`, background: GRAD }} />
      </div>
      <div className="space-y-2.5">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="flex items-center gap-3 animate-pulse">
            <div className="w-4 h-4 rounded bg-bg-hover shrink-0" />
            <div className="h-4 rounded bg-bg-hover" style={{ width: `${45 - i * 6}%` }} />
            <div className="h-4 rounded bg-bg-hover w-16 ml-auto" />
          </div>
        ))}
      </div>
    </div>
  );
}

// 카테고리 점유율 — 시간 필터 + 도넛(좌 ~40%) + 확장 범례 테이블(우 ~60%)
const DONUT_PAL = ["#00FFA3", "#1fe6bd", "#2fccce", "#38b0e0", "#4a90e2", "#6b7688"];
const CAT_RANGE_OPTS: { k: CatRange; label: string }[] = [
  { k: "live", label: "실시간" },
  { k: "1h",   label: "최근 1시간" },
  { k: "24h",  label: "24시간 평균" },
];

function CategoryDonut() {
  const [range, setRange] = useState<CatRange>("1h");
  const [data, setData] = useState<RisingCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.rising.categories(range)
      .then((d) => { if (alive) setData(d.categories || []); })
      .catch(() => { if (alive) setData([]); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [range]);

  // 도넛: 상위 5 + 기타
  const top = data.slice(0, 5);
  const restV = data.slice(5).reduce((s, c) => s + c.viewers, 0);
  const slices = [
    ...top.map((c) => ({ label: c.category, value: c.viewers })),
    ...(restV > 0 ? [{ label: "기타", value: restV }] : []),
  ];
  const total = slices.reduce((s, x) => s + x.value, 0) || 1;
  const legendRows = data.slice(0, 8); // 범례 테이블은 상위 8개

  const size = 190, stroke = 26, R = (size - stroke - 10) / 2, C = 2 * Math.PI * R, cx = size / 2, cy = size / 2;
  let acc = 0;
  const segs = slices.map((sl, i) => {
    const len = (sl.value / total) * C;
    const dash = Math.max(0, len - 3);
    const seg = (
      <circle key={i} r={R} cx={cx} cy={cy} fill="none" stroke={DONUT_PAL[i % DONUT_PAL.length]}
        strokeWidth={hover === i ? stroke + 5 : stroke}
        strokeDasharray={`${dash} ${C - dash}`} strokeDashoffset={-acc}
        transform={`rotate(-90 ${cx} ${cy})`} style={{ transition: "stroke-width .15s", cursor: "pointer" }}
        onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
    );
    acc += len;
    return seg;
  });
  const focus = hover !== null && hover < slices.length ? slices[hover] : null;

  return (
    <div className="card">
      <div className="flex items-start justify-between mb-4 flex-wrap gap-2">
        <div>
          <h3 className="section-title">카테고리 점유율</h3>
          <p className="text-xs text-muted mt-0.5">전체 시청자 중 상위 카테고리(게임/토크 등) 비중</p>
        </div>
        <Seg options={CAT_RANGE_OPTS} value={range} onChange={setRange} />
      </div>

      {loading ? <ChartSkeleton height={200} /> : data.length === 0 ? (
        <p className="text-sm text-muted py-6 text-center">카테고리 데이터가 아직 없습니다.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-12 gap-5 items-center">
          {/* 좌: 도넛 (~40%) */}
          <div className="md:col-span-5 flex justify-center">
            <div className="relative" style={{ width: size, height: size }}>
              <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
                <circle r={R} cx={cx} cy={cy} fill="none" stroke="rgb(var(--color-bg-hover-rgb))" strokeWidth={stroke} />
                {segs}
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                {focus ? (
                  <>
                    <span className="text-xl font-extrabold tabular-nums" style={{ color: DONUT_PAL[hover! % DONUT_PAL.length] }}>
                      {((focus.value / total) * 100).toFixed(1)}%
                    </span>
                    <span className="text-[11px] text-muted mt-0.5 max-w-[110px] truncate text-center">{focus.label}</span>
                  </>
                ) : (
                  <>
                    <span className="text-lg font-extrabold text-fg tabular-nums">{nf(Math.round(total))}</span>
                    <span className="text-[11px] text-muted mt-0.5">평균 시청자</span>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* 우: 범례 테이블 (~60%) */}
          <div className="md:col-span-7 overflow-x-auto">
            <table className="w-full text-sm min-w-[360px]">
              <thead>
                <tr className="text-muted text-xs border-b border-border">
                  <th className="text-left font-medium py-1.5 w-7">#</th>
                  <th className="text-left font-medium py-1.5">카테고리</th>
                  <th className="text-right font-medium py-1.5">점유율</th>
                  <th className="text-right font-medium py-1.5 hidden sm:table-cell">평균 시청자</th>
                  <th className="text-right font-medium py-1.5 pr-1">1시간 전</th>
                </tr>
              </thead>
              <tbody>
                {legendRows.map((c, i) => (
                  <tr key={c.category}
                      onMouseEnter={() => setHover(i < slices.length ? i : null)} onMouseLeave={() => setHover(null)}
                      className="border-b border-border/40 transition-colors"
                      style={{ background: hover === i ? "rgb(var(--color-bg-hover-rgb))" : "transparent" }}>
                    <td className="py-1.5 tabular-nums text-muted text-xs">{c.rank ?? i + 1}</td>
                    <td className="py-1.5">
                      <span className="flex items-center gap-2 min-w-0">
                        <span className="w-2.5 h-2.5 rounded-full shrink-0"
                              style={{ background: i < 5 ? DONUT_PAL[i] : "rgb(var(--color-muted-rgb))" }} />
                        <span className="text-fg truncate">{c.category}</span>
                      </span>
                    </td>
                    <td className="py-1.5 text-right font-semibold tabular-nums text-fg">{(c.share ?? 0).toFixed(1)}%</td>
                    <td className="py-1.5 text-right tabular-nums text-muted hidden sm:table-cell">{nf(c.avg_viewers)}명</td>
                    <td className="py-1.5 text-right pr-1 text-[11px]"><Delta pct={c.change} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── 개요 탭 ───────────────────────────────────────────────────────────────────
function OverviewTab({ ov, stars }: { ov: RisingOverview; stars: RisingStars | null }) {
  const range: TimeRange = "live"; // 기간 옵션 제거 — 실시간(최근 6시간)만 사용
  const [ts, setTs] = useState<RisingTimeseries | null>(null);
  const [tsLoading, setTsLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setTsLoading(true);
    api.rising.timeseries(range)
      .then((d) => { if (alive) setTs(d); })
      .catch(() => { if (alive) setTs(null); })
      .finally(() => { if (alive) setTsLoading(false); });
    return () => { alive = false; };
  }, [range]);

  const [metric, setMetric] = useState<Metric>("viewers");

  const liveCount = ov.summary?.live_count ?? 0;
  const totalV    = ov.summary?.total_viewers ?? 0;
  const avgV      = liveCount ? Math.round(totalV / liveCount) : 0;
  const peakV     = (ts?.points ?? []).reduce((m, p) => Math.max(m, p.total_viewers), totalV);
  const dv = ov.deltas;
  const points = (ts?.points ?? []) as unknown as LinePoint[];
  const rangeDesc = RANGE_DESC;

  const chartSeries: LineSeries = metric === "viewers"
    ? { key: "total_viewers", name: "시청자", color: GREEN, gradient: [GREEN, CYAN] }
    : { key: "live_count",    name: "라이브", color: CYAN,  gradient: [CYAN, GREEN] };
  const chartTitle = metric === "viewers" ? "전체 동시 시청자 추이" : "동시 라이브 추이";
  const chartUnit  = metric === "viewers" ? "명" : "개";

  // 리치 툴팁: 지표와 무관하게 시청자/라이브/평균을 함께 표기
  const tooltipItems = (p: LinePoint) => [
    { label: "시청자", value: `${nf(p.total_viewers)}명`, color: GREEN },
    { label: "라이브", value: `${nf(p.live_count)}개`, color: CYAN },
    { label: "평균",   value: `${nf(Math.round(p.total_viewers / Math.max(1, p.live_count)))}명` },
  ];

  // 인사이트: 피크(최고 시청자) 시각 + 골든타임(방송당 평균 최고) + 전체 평균 기준
  const insights = (() => {
    const ps = ts?.points ?? [];
    if (ps.length < 2) return null;
    const withAvg = ps.map((p) => ({ ...p, avg: p.total_viewers / Math.max(1, p.live_count) }));
    const peak   = withAvg.reduce((a, b) => (b.total_viewers > a.total_viewers ? b : a));
    const golden = withAvg.reduce((a, b) => (b.avg > a.avg ? b : a));
    const hm = (t: number) => new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
    const hour = (t: number) => new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", hour12: false });
    return {
      peakTime: hm(peak.t), peakViewers: peak.total_viewers,
      goldenHour: hour(golden.t), goldenAvg: Math.round(golden.avg),
    };
  })();

  return (
    <div className="space-y-5">
      {/* KPI */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatTile label="현재 라이브 방송" value={nf(liveCount)} unit="채널"
          rawValue={liveCount} deltaPrev={dv?.live_count.prev} />
        <StatTile label="전체 동시 시청자" value={nf(totalV)} unit="명" accent
          rawValue={totalV} deltaPrev={dv?.total_viewers.prev} />
        <StatTile label="방송당 평균 시청자" value={nf(avgV)} unit="명" sub="총 시청자 ÷ 방송 수" />
        <StatTile label="뷰어쉽" value={nf(peakV)} unit="명" sub="최근 6시간 최고 동접" />
      </div>

      {/* 추이 차트 — 지표 필터(시청자/방송수) + 가변 Y축, 기간 선택은 좌측 아래 */}
      <div className="card">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h3 className="section-title">{chartTitle}</h3>
          <Seg options={METRIC_OPTS} value={metric} onChange={setMetric} />
        </div>
        <p className="text-xs text-muted mb-4">{rangeDesc}</p>
        {tsLoading ? <ChartSkeleton /> : (
          <LineChart points={points} series={[chartSeries]} area dynamicY unit={chartUnit}
            tooltipItems={tooltipItems} showPeak />
        )}
        {/* 피크 마커 범례 */}
        <div className="mt-3 flex items-center justify-end">
          <span className="flex items-center gap-1.5 text-[11px] text-muted">
            <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: "#FF4FA3" }} />
            피크 타임(최고 시청자)
          </span>
        </div>
      </div>

      {/* 스트리머 인사이트 */}
      <div className="card">
        <h3 className="section-title mb-1">스트리머 인사이트</h3>
        <p className="text-xs text-muted mb-4">선택 기간의 트래픽 패턴에서 뽑은 방송 전략 힌트</p>
        {insights ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="rounded-xl border border-border p-4">
              <p className="text-xs text-muted">유입 골든타임 (빈집)</p>
              <p className="text-xl font-extrabold mt-1"><GradText>{insights.goldenHour}경</GradText></p>
              <p className="text-[11px] text-muted mt-1 leading-relaxed">
                방송당 평균 <b className="text-fg">{nf(insights.goldenAvg)}명</b> — 경쟁 방송이 적어 노출·유입 기회가 큰 구간
              </p>
            </div>
            <div className="rounded-xl border border-border p-4">
              <p className="text-xs text-muted">피크 타임</p>
              <p className="text-xl font-extrabold mt-1"><GradText grad={PEAK_GRAD}>{insights.peakTime}</GradText></p>
              <p className="text-[11px] text-muted mt-1 leading-relaxed">
                최고 동시 시청자 <b className="text-fg">{nf(insights.peakViewers)}명</b> — 플랫폼 트래픽이 가장 몰리는 시간
              </p>
            </div>
            <div className="rounded-xl border border-border p-4">
              <p className="text-xs text-muted">전체 방송당 평균 (체급 기준선)</p>
              <p className="text-xl font-extrabold mt-1"><GradText>{nf(avgV)}명</GradText></p>
              <p className="text-[11px] text-muted mt-1 leading-relaxed">
                내 평균 시청자가 이보다 높으면 플랫폼 평균 이상 체급
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted py-4 text-center">인사이트 산출을 위한 데이터가 아직 부족합니다.</p>
        )}
      </div>

      {/* 카테고리 점유율 (자체 시간 필터) */}
      <CategoryDonut />

      {/* 급상승 스트리머 */}
      <div className="card">
        <h3 className="section-title mb-1">급상승 스트리머</h3>
        <p className="text-xs text-muted mb-4">24시간 전 대비 동시 시청자 성장률 상위</p>
        {stars && stars.stars.length > 0 ? (
          <div className="space-y-1.5">
            {stars.stars.slice(0, 8).map((s, i) => (
              <a key={s.chzzk_channel_id} href={`https://chzzk.naver.com/${s.chzzk_channel_id}`}
                 target="_blank" rel="noopener noreferrer"
                 className="flex items-center gap-3 rounded-lg px-2.5 py-2 hover:bg-bg-hover transition-colors">
                <span className="text-xs font-bold w-5 text-center tabular-nums"
                      style={{ color: i < 3 ? GREEN : undefined }}>{i + 1}</span>
                <span className="text-sm font-semibold text-fg truncate flex-1">{s.channel_name}</span>
                <span className="text-[11px] text-muted truncate hidden sm:block">{s.category || "-"}</span>
                <span className="text-sm font-bold tabular-nums flex items-center gap-1" style={{ color: GREEN }}>
                  <TrendingUp size={12} /> +{nf(s.growth_rate)}%
                </span>
              </a>
            ))}
          </div>
        ) : (
          <RisingSkeleton historyHours={ov.history_hours ?? 0} />
        )}
      </div>
    </div>
  );
}

// ── 랭킹 탭 (소프트콘식 실시간 방송 랭킹 테이블) ──────────────────────────────
type SortKey = "viewers" | "followers" | "duration";
function RankingTab({ rank }: { rank: RisingLiveRanking }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("viewers");
  const [limit, setLimit] = useState(50);

  const enriched = useMemo(() =>
    rank.streamers.map((s) => ({ ...s, dur: liveDuration(s.open_date) })), [rank]);

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    let list = enriched;
    if (kw) list = list.filter((s) =>
      s.channel_name.toLowerCase().includes(kw) || s.category_name.toLowerCase().includes(kw));
    const sorted = [...list].sort((a, b) =>
      sort === "viewers" ? b.concurrent_viewers - a.concurrent_viewers
      : sort === "followers" ? b.follower_count - a.follower_count
      : b.dur.ms - a.dur.ms);
    return sorted;
  }, [enriched, q, sort]);

  // 프로그레스 바 기준값(리스트 전체 최대치)
  const DAY_MS = 24 * 3600 * 1000;
  const maxViewers  = useMemo(() => Math.max(1, ...enriched.map((s) => s.concurrent_viewers)), [enriched]);
  const maxFollower = useMemo(() => Math.max(1, ...enriched.map((s) => s.follower_count)), [enriched]);
  const maxDur      = useMemo(() => Math.max(DAY_MS, ...enriched.map((s) => (s.dur.ms > 0 ? s.dur.ms : 0))), [enriched, DAY_MS]);

  const SortBtn = ({ k, label }: { k: SortKey; label: string }) => (
    <button onClick={() => setSort(k)}
      className="text-xs px-2.5 py-1 rounded-md border transition-colors"
      style={{ background: sort === k ? "rgba(0,255,163,0.1)" : "transparent",
               borderColor: sort === k ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
               color: sort === k ? GREEN : "rgb(var(--color-muted-rgb))" }}>
      {label}
    </button>
  );

  return (
    <div className="card !p-4 md:!p-5">
      {/* 컨트롤 */}
      <div className="flex items-center justify-between gap-3 mb-4 flex-wrap">
        <div className="relative flex-1 min-w-[180px] max-w-xs">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input value={q} onChange={(e) => { setQ(e.target.value); setLimit(50); }}
            placeholder="스트리머·게임 검색"
            className="w-full bg-bg border border-border rounded-lg pl-9 pr-3 py-2 text-sm text-fg placeholder-muted focus:outline-none focus:border-accent" />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-muted mr-1">정렬</span>
          <SortBtn k="viewers" label="시청자" />
          <SortBtn k="followers" label="팔로워" />
          <SortBtn k="duration" label="방송시간" />
        </div>
      </div>

      {/* 테이블 */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[560px]">
          <thead>
            <tr className="text-muted text-xs border-b border-border">
              <th className="text-left font-medium py-2 pl-2 w-10">#</th>
              <th className="text-left font-medium py-2">스트리머</th>
              <th className="text-right font-medium py-2">현재 시청자</th>
              <th className="text-left font-medium py-2 pl-4 hidden sm:table-cell">카테고리</th>
              <th className="text-right font-medium py-2 hidden md:table-cell">방송시간</th>
              <th className="text-right font-medium py-2 pr-2">팔로워</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, limit).map((s, i) => {
              const vwPct  = (s.concurrent_viewers / maxViewers) * 100;
              const durPct = s.dur.ms > 0 ? (s.dur.ms / maxDur) * 100 : 0;
              const folPct = s.follower_count > 0 ? (s.follower_count / maxFollower) * 100 : 0;
              const vwDelta = s.viewers_prev && s.viewers_prev > 0
                ? ((s.concurrent_viewers - s.viewers_prev) / s.viewers_prev) * 100 : null;
              const newFol = s.follower_prev24h != null ? s.follower_count - s.follower_prev24h : null;
              return (
                <tr key={s.chzzk_channel_id}
                    className="border-b border-border/50 hover:bg-bg-hover transition-colors">
                  <td className="py-2.5 pl-2 tabular-nums text-muted text-sm align-top">{i + 1}</td>

                  {/* 스트리머 — 개인 분석 대시보드로 이동 */}
                  <td className="py-2.5 align-top">
                    <Link href={`/stats/streamer/${s.chzzk_channel_id}`}
                       className="flex items-center gap-2 group">
                      <span className="w-6 h-6 rounded-full overflow-hidden bg-bg-hover shrink-0">
                        {s.channel_image_url && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={s.channel_image_url} alt="" width={24} height={24}
                               loading="lazy" className="w-full h-full object-cover" />
                        )}
                      </span>
                      <span className="font-semibold text-fg group-hover:text-accent transition-colors truncate max-w-[150px] md:max-w-none">
                        {s.channel_name}
                      </span>
                      <BarChart3 size={12} className="text-muted opacity-0 group-hover:opacity-100 shrink-0" />
                    </Link>
                  </td>

                  {/* 현재 시청자 — 증감(초록/빨강) + 흰 숫자 + 노란 2톤 바 */}
                  <td className="py-2.5 pr-1 align-top" style={{ minWidth: 120 }}>
                    <div className="flex items-center justify-end gap-1.5">
                      {vwDelta !== null && <span className="text-[10px]"><Delta pct={vwDelta} /></span>}
                      <span className="font-bold tabular-nums text-fg">{nf(s.concurrent_viewers)}</span>
                    </div>
                    <div className="mt-1.5 h-[3px] rounded-full bg-bg-hover overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${vwPct}%`, background: YELLOW_GRAD }} />
                    </div>
                  </td>

                  {/* 카테고리 — 뱃지 */}
                  <td className="py-2.5 pl-4 hidden sm:table-cell align-top">
                    {s.category_name
                      ? <span className="inline-block bg-bg-hover text-fg px-2 py-1 rounded-md text-xs truncate max-w-[130px]">{s.category_name}</span>
                      : <span className="text-muted text-sm">-</span>}
                  </td>

                  {/* 방송시간 — 보라 바 (우측 정렬, 폭 제한, 팔로워와 간격 확보) */}
                  <td className="py-2.5 pr-6 hidden md:table-cell align-top" style={{ minWidth: 110 }}>
                    <div className="text-right tabular-nums text-muted text-sm">{s.dur.label}</div>
                    <div className="mt-1.5 h-[3px] rounded-full bg-bg-hover overflow-hidden ml-auto" style={{ maxWidth: 84 }}>
                      <div className="h-full rounded-full" style={{ width: `${durPct}%`, background: "#A855F7" }} />
                    </div>
                  </td>

                  {/* 팔로워 — 시안 바 + 신규 유입 (좌측 간격 확보, 우측 정렬, 폭 제한) */}
                  <td className="py-2.5 pl-6 pr-2 align-top" style={{ minWidth: 120 }}>
                    <div className="flex items-center justify-end gap-1.5">
                      {newFol != null && newFol > 0 &&
                        <span className="text-[10px] font-semibold tabular-nums" style={{ color: "#06B6D4" }}>+{nf(newFol)}</span>}
                      <span className="tabular-nums text-fg text-sm">{s.follower_count > 0 ? nf(s.follower_count) : "-"}</span>
                    </div>
                    <div className="mt-1.5 h-[3px] rounded-full bg-bg-hover overflow-hidden ml-auto" style={{ maxWidth: 92 }}>
                      <div className="h-full rounded-full" style={{ width: `${folPct}%`, background: "#06B6D4" }} />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {filtered.length === 0 && (
        <p className="text-sm text-muted text-center py-6">검색 결과가 없습니다.</p>
      )}
      {filtered.length > limit && (
        <div className="text-center pt-4">
          <button onClick={() => setLimit((l) => l + 50)} className="btn-secondary text-sm">
            더 보기 ({nf(filtered.length - limit)}개 남음)
          </button>
        </div>
      )}
    </div>
  );
}

// ── 신규/라이징 탭 ────────────────────────────────────────────────────────────
type NcSort = "growth" | "duration" | "debut";
function NewcomersTab({ data }: { data: RisingNewcomers }) {
  const [sort, setSort] = useState<NcSort>("growth");
  const [limit, setLimit] = useState(50);
  const enriched = useMemo(() =>
    data.streamers.map((s) => ({ ...s, dur: liveDuration(s.open_date) })), [data]);
  const sorted = useMemo(() => {
    const a = [...enriched];
    if (sort === "duration") a.sort((x, y) => y.dur.ms - x.dur.ms);
    else if (sort === "debut") a.sort((x, y) => x.first_seen_days - y.first_seen_days);
    else a.sort((x, y) => (y.growth_rate ?? -1e9) - (x.growth_rate ?? -1e9));
    return a;
  }, [enriched, sort]);

  const SortBtn = ({ k, label, locked }: { k?: NcSort; label: string; locked?: boolean }) => {
    const active = !locked && sort === k;
    return (
      <button disabled={locked} onClick={() => k && setSort(k)}
        className="text-xs px-2.5 py-1 rounded-md border transition-colors flex items-center gap-1"
        style={{ background: active ? "rgba(0,255,163,0.1)" : "transparent",
                 borderColor: active ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                 color: active ? GREEN : "rgb(var(--color-muted-rgb))", opacity: locked ? 0.5 : 1,
                 cursor: locked ? "not-allowed" : "pointer" }}>
        {label}{locked && <Lock size={10} />}
      </button>
    );
  };

  return (
    <div className="card !p-4 md:!p-5">
      <div className="flex items-center gap-1.5 mb-2 flex-wrap">
        <span className="text-xs text-muted mr-1">정렬</span>
        <SortBtn label="🔥 소통 화력순" locked />
        <SortBtn k="growth" label="📈 급성장순" />
        <SortBtn k="duration" label="⏱️ 열정 방송순" />
        <SortBtn k="debut" label="🆕 신규 등장순" />
      </div>
      <p className="text-[11px] text-muted/70 mb-4">
        최근 평균 시청자 50명 미만 또는 신입 태그 · 팔로워 500명 이하 · 시청자 3명 이상 · 🔒 소통 화력은 채팅 미수집으로 잠금
      </p>

      {sorted.length === 0 ? (
        <p className="text-sm text-muted text-center py-8">조건에 맞는 신규/라이징 방송이 아직 없습니다.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[600px]">
            <thead>
              <tr className="text-muted text-xs border-b border-border">
                <th className="text-left font-medium py-2 pl-2 w-8">#</th>
                <th className="text-left font-medium py-2">스트리머</th>
                <th className="text-right font-medium py-2">시청자</th>
                <th className="text-right font-medium py-2">성장률</th>
                <th className="text-right font-medium py-2 pl-4 hidden md:table-cell">방송시간</th>
                <th className="text-right font-medium py-2 hidden sm:table-cell" title="우리 수집기가 이 채널을 처음 관측한 시점(최대 14일). 치지직 개설일/첫방송일은 공개 API로 제공되지 않음">첫 등장</th>
                <th className="text-right font-medium py-2 pr-2">팔로워</th>
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, limit).map((s, i) => (
                <tr key={s.chzzk_channel_id} className="border-b border-border/50 hover:bg-bg-hover transition-colors">
                  <td className="py-2.5 pl-2 tabular-nums text-muted text-sm align-middle">{i + 1}</td>
                  <td className="py-2.5">
                    <Link href={`/stats/streamer/${s.chzzk_channel_id}`} className="flex items-center gap-2 group">
                      <Sprout size={14} className="shrink-0" style={{ color: GREEN }} />
                      <span className="w-6 h-6 rounded-full overflow-hidden bg-bg-hover shrink-0">
                        {s.channel_image_url && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={s.channel_image_url} alt="" width={24} height={24} loading="lazy" className="w-full h-full object-cover" />
                        )}
                      </span>
                      <span className="font-semibold text-fg group-hover:text-accent transition-colors truncate max-w-[140px] md:max-w-none">{s.channel_name}</span>
                    </Link>
                  </td>
                  <td className="py-2.5 text-right tabular-nums font-bold text-fg">{nf(s.concurrent_viewers)}</td>
                  <td className="py-2.5 text-right text-[11px]"><Delta pct={s.growth_rate} /></td>
                  <td className="py-2.5 pl-4 text-right tabular-nums text-muted text-sm hidden md:table-cell">{s.dur.label}</td>
                  <td className="py-2.5 text-right tabular-nums text-muted text-sm hidden sm:table-cell">{s.first_seen_days}일 전</td>
                  <td className="py-2.5 pr-2 text-right tabular-nums text-muted text-sm">{s.follower_count > 0 ? nf(s.follower_count) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {sorted.length > limit && (
        <div className="text-center pt-4">
          <button onClick={() => setLimit((l) => l + 50)} className="btn-secondary text-sm">더 보기 ({nf(sorted.length - limit)}개 남음)</button>
        </div>
      )}
    </div>
  );
}

// ── 카테고리 탭 ───────────────────────────────────────────────────────────────
function CategoryTab({ cats }: { cats: RisingCategories }) {
  const maxV = Math.max(1, ...cats.categories.map((c) => c.viewers));
  return (
    <div className="card !p-4 md:!p-5">
      <h3 className="section-title mb-1">카테고리(게임)별 현황</h3>
      <p className="text-xs text-muted mb-4">
        방송당 평균 = 시청자 ÷ 방송 수. 값이 높을수록 방송 대비 시청 유입(블루오션)이 큽니다.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[520px]">
          <thead>
            <tr className="text-muted text-xs border-b border-border">
              <th className="text-left font-medium py-2 pl-2 w-10">#</th>
              <th className="text-left font-medium py-2">카테고리</th>
              <th className="text-right font-medium py-2">시청자</th>
              <th className="text-right font-medium py-2 hidden sm:table-cell">방송 수</th>
              <th className="text-right font-medium py-2 pr-2">방송당 평균</th>
            </tr>
          </thead>
          <tbody>
            {cats.categories.map((c, i) => (
              <tr key={c.category} className="border-b border-border/50 hover:bg-bg-hover transition-colors">
                <td className="py-2.5 pl-2 tabular-nums text-muted text-xs">{i + 1}</td>
                <td className="py-2.5">
                  <div className="font-semibold text-fg truncate max-w-[160px] md:max-w-none">{c.category}</div>
                  <div className="mt-1 h-1.5 rounded-full bg-bg-hover overflow-hidden max-w-[220px]">
                    <div className="h-full rounded-full" style={{ width: `${(c.viewers / maxV) * 100}%`, background: GRAD }} />
                  </div>
                </td>
                <td className="py-2.5 text-right tabular-nums font-bold text-fg">{nf(c.viewers)}</td>
                <td className="py-2.5 text-right tabular-nums text-muted text-xs hidden sm:table-cell">{nf(c.lives)}</td>
                <td className="py-2.5 pr-2 text-right tabular-nums font-bold"><GradText>{nf(c.avg_viewers)}</GradText></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cats.categories.length === 0 && (
        <p className="text-sm text-muted text-center py-6">카테고리 데이터가 아직 없습니다.</p>
      )}
    </div>
  );
}

export default function StatsPage() {
  const [ov, setOv]       = useState<RisingOverview | null>(null);
  const [rank, setRank]   = useState<RisingLiveRanking | null>(null);
  const [cats, setCats]   = useState<RisingCategories | null>(null);
  const [stars, setStars] = useState<RisingStars | null>(null);
  const [news, setNews]   = useState<RisingNewcomers | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);
  const [tab, setTab]         = useState<Tab>("overview");

  useEffect(() => {
    Promise.all([
      api.rising.overview(), api.rising.liveRanking(200),
      api.rising.categories(), api.rising.risingStars(20), api.rising.newcomers(80),
    ])
      .then(([o, r, c, s, nc]) => { setOv(o); setRank(r); setCats(c); setStars(s); setNews(nc); })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, []);

  const collectedLabel = useMemo(() =>
    ov?.collected_at
      ? new Date(ov.collected_at * 1000).toLocaleString("ko-KR", { hour: "2-digit", minute: "2-digit", month: "long", day: "numeric" })
      : null,
  [ov]);

  const empty = !ov || ov.collected_at === null;

  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="w-full px-4 md:px-6 flex items-center justify-between" style={{ height: 60 }}>
          <div className="flex items-center gap-2.5">
            <Link href="/" className="flex items-center gap-2 font-bold text-[15px] text-muted hover:text-fg transition-colors">
              <Bot size={18} className="text-accent" /> NexBot
            </Link>
            <span className="text-border">/</span>
            <span className="flex items-center gap-1.5 font-extrabold text-[16px]">
              <BarChart3 size={17} style={{ color: GREEN }} /> <GradText>치지직 통계</GradText>
            </span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 w-full px-4 md:px-6 py-7 md:py-9">
        <div className="mb-6">
          <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight leading-tight">
치지직 <GradText>방송</GradText> 통계
          </h1>
          <div className="mt-2 flex items-end justify-between gap-3 flex-wrap">
            <p className="text-muted text-sm md:text-base">
              치지직 라이브 방송의 시청자·카테고리 트렌드를 실시간으로 분석합니다.
            </p>
            {collectedLabel && (
              <span className="inline-flex items-center gap-1 text-muted/70 text-sm shrink-0 ml-auto">
                <Circle size={7} className="fill-current" style={{ color: GREEN }} /> 마지막 집계 {collectedLabel}
              </span>
            )}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 text-muted py-24">
            <Loader2 size={18} className="animate-spin" /> 데이터를 불러오는 중...
          </div>
        ) : error ? (
          <div className="card text-center py-16 text-muted">데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.</div>
        ) : empty ? (
          <div className="card text-center py-16">
            <Radio size={36} className="mx-auto mb-3 opacity-30" style={{ color: GREEN }} />
            <p className="font-medium text-fg">데이터를 수집하고 있습니다.</p>
            <p className="text-sm text-muted mt-1">첫 집계가 완료되면 곧 통계가 표시됩니다.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-[210px_1fr] gap-5 md:gap-7">
            {/* 좌측 메뉴 */}
            <aside className="md:sticky md:top-[76px] md:self-start">
              <StreamerSearch />
              <p className="text-xs font-semibold text-muted/70 uppercase tracking-wider px-1 mb-2">분석 메뉴</p>
              <nav className="flex md:flex-col gap-1.5 overflow-x-auto md:overflow-visible pb-1">
                {TABS.map((t) => {
                  const active = tab === t.key;
                  return (
                    <button key={t.key} onClick={() => setTab(t.key)}
                      className="relative flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors shrink-0 md:w-full border overflow-hidden"
                      style={{
                        background: active ? "rgba(0,255,163,0.08)" : "transparent",
                        borderColor: active ? "rgba(0,194,255,0.35)" : "transparent",
                      }}>
                      {active && <span className="absolute left-0 top-0 bottom-0 w-1 rounded-r" style={{ background: GRAD }} />}
                      <span style={{ color: active ? GREEN : "rgb(var(--color-muted-rgb))" }}>{t.icon}</span>
                      <span className="min-w-0">
                        <span className="block text-sm font-semibold" style={{ color: active ? GREEN : undefined }}>{t.label}</span>
                        <span className="block text-[11px] text-muted whitespace-nowrap">{t.desc}</span>
                      </span>
                    </button>
                  );
                })}
              </nav>
              <p className="hidden md:block text-[11px] text-muted/60 mt-4 px-1 leading-relaxed">
                약 10분 주기로 치지직 공개 라이브 목록을 수집합니다. 비공식 서비스로 실제 수치와 오차가 있을 수 있습니다.
              </p>
            </aside>

            {/* 우측 뷰 */}
            <div className="min-w-0">
              {tab === "overview"  && ov && <OverviewTab ov={ov} stars={stars} />}
              {tab === "newcomers" && news && <NewcomersTab data={news} />}
              {tab === "ranking"   && rank && <RankingTab rank={rank} />}
              {tab === "category"  && cats && <CategoryTab cats={cats} />}
            </div>
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
}
