"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bot, BarChart3, LineChart as LineIcon, ListOrdered, Gamepad2, Radio,
  TrendingUp, ArrowUpRight, Loader2, Search, Circle,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  RisingOverview, RisingTimeseries, RisingLiveRanking, RisingCategories, RisingCategory,
  RisingStars, TimeRange,
} from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";
import LineChart, { type LinePoint, type LineSeries } from "./LineChart";

// 2톤 그라데이션(그린 → 시안) — 브랜드 액센트
const GREEN = "#00FFA3";
const CYAN  = "#00C2FF";
const GRAD  = `linear-gradient(135deg, ${GREEN}, ${CYAN})`;

const nf = (n: number) => n.toLocaleString("ko-KR");

// 그라데이션 텍스트
function GradText({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={className}
      style={{ background: GRAD, WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>
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

type Tab = "overview" | "ranking" | "category";
const TABS: { key: Tab; label: string; desc: string; icon: React.ReactNode }[] = [
  { key: "overview", label: "개요",     desc: "추이·요약",   icon: <LineIcon size={17} /> },
  { key: "ranking",  label: "랭킹",     desc: "실시간 방송",  icon: <ListOrdered size={17} /> },
  { key: "category", label: "카테고리", desc: "게임별 현황",  icon: <Gamepad2 size={17} /> },
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

function StatTile({ label, value, sub, accent, deltaPrev, delta24h }:
  { label: string; value: string; sub?: string; accent?: boolean;
    deltaPrev?: number | null; delta24h?: number | null }) {
  const hasDelta = deltaPrev !== undefined || delta24h !== undefined;
  return (
    <div className="card !p-4">
      <p className="text-xs text-muted">{label}</p>
      <p className="text-xl md:text-2xl font-extrabold mt-1 tracking-tight tabular-nums">
        {accent ? <GradText>{value}</GradText> : value}
      </p>
      {hasDelta ? (
        <div className="flex items-center gap-2 mt-1.5 text-[11px] flex-wrap">
          <span className="flex items-center gap-1"><Delta pct={deltaPrev} /><span className="text-muted">직전</span></span>
          <span className="flex items-center gap-1 border-l border-border pl-2"><Delta pct={delta24h} /><span className="text-muted">24h</span></span>
        </div>
      ) : sub ? <p className="text-[11px] text-muted mt-0.5">{sub}</p> : null}
    </div>
  );
}

type Metric = "viewers" | "lives";

const RANGE_OPTS: { k: TimeRange; label: string; desc: string }[] = [
  { k: "live", label: "실시간", desc: "최근 6시간 · 10분 간격" },
  { k: "24h",  label: "24시간", desc: "최근 24시간 · 1시간 평균" },
  { k: "7d",   label: "7일",    desc: "최근 7일 · 1시간 평균" },
];
const METRIC_OPTS: { k: Metric; label: string }[] = [
  { k: "viewers", label: "시청자 수" },
  { k: "lives",   label: "라이브 방송 수" },
];

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

// 카테고리 점유율 도넛 (상위 5 + 기타)
const DONUT_PAL = ["#00FFA3", "#1fe6bd", "#2fccce", "#38b0e0", "#4a90e2", "#6b7688"];
function CategoryDonut({ categories }: { categories: RisingCategory[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const top = categories.slice(0, 5);
  const restV = categories.slice(5).reduce((s, c) => s + c.viewers, 0);
  const slices = [
    ...top.map((c) => ({ label: c.category, value: c.viewers })),
    ...(restV > 0 ? [{ label: "기타", value: restV }] : []),
  ];
  const total = slices.reduce((s, x) => s + x.value, 0) || 1;

  const size = 200, stroke = 28, R = (size - stroke) / 2, C = 2 * Math.PI * R, cx = size / 2, cy = size / 2;
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
  const focus = hover !== null ? slices[hover] : null;

  return (
    <div className="flex flex-col sm:flex-row items-center gap-6">
      <div className="relative shrink-0" style={{ width: size, height: size }}>
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
              <span className="text-[11px] text-muted mt-0.5 max-w-[120px] truncate text-center">{focus.label}</span>
            </>
          ) : (
            <>
              <span className="text-xl font-extrabold text-fg tabular-nums">{nf(total)}</span>
              <span className="text-[11px] text-muted mt-0.5">총 시청자</span>
            </>
          )}
        </div>
      </div>
      <div className="flex-1 w-full space-y-1.5">
        {slices.map((sl, i) => (
          <div key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
            className="flex items-center gap-2.5 rounded-md px-2 py-1 transition-colors"
            style={{ background: hover === i ? "rgb(var(--color-bg-hover-rgb))" : "transparent" }}>
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: DONUT_PAL[i % DONUT_PAL.length] }} />
            <span className="text-sm text-fg truncate flex-1">{sl.label}</span>
            <span className="text-sm font-semibold tabular-nums text-fg">{((sl.value / total) * 100).toFixed(1)}%</span>
            <span className="text-[11px] text-muted tabular-nums w-20 text-right">{nf(sl.value)}명</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── 개요 탭 ───────────────────────────────────────────────────────────────────
function OverviewTab({ ov, cats, stars }: { ov: RisingOverview; cats: RisingCategories | null; stars: RisingStars | null }) {
  const [range, setRange] = useState<TimeRange>("24h");
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
  const dv = ov.deltas;
  const points = (ts?.points ?? []) as unknown as LinePoint[];
  const rangeDesc = RANGE_OPTS.find((o) => o.k === range)?.desc ?? "";

  const chartSeries: LineSeries = metric === "viewers"
    ? { key: "total_viewers", name: "총 시청자", color: GREEN, gradient: [GREEN, CYAN] }
    : { key: "live_count",    name: "방송 수",   color: CYAN,  gradient: [CYAN, GREEN] };
  const chartTitle = metric === "viewers" ? "전체 동시 시청자 추이" : "라이브 방송 수 추이";
  const chartUnit  = metric === "viewers" ? "명" : "개";

  return (
    <div className="space-y-5">
      {/* KPI */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <StatTile label="현재 라이브 방송" value={nf(liveCount)}
          deltaPrev={dv?.live_count.prev} delta24h={dv?.live_count.d24h} />
        <StatTile label="전체 동시 시청자" value={nf(totalV)} accent
          deltaPrev={dv?.total_viewers.prev} delta24h={dv?.total_viewers.d24h} />
        <StatTile label="방송당 평균 시청자" value={nf(avgV)} sub="총 시청자 ÷ 방송 수" />
      </div>

      {/* 추이 차트 — 지표 필터(시청자/방송수) + 가변 Y축, 기간 선택은 좌측 아래 */}
      <div className="card">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h3 className="section-title">{chartTitle}</h3>
          <Seg options={METRIC_OPTS} value={metric} onChange={setMetric} />
        </div>
        <p className="text-xs text-muted mb-4">{rangeDesc}</p>
        {tsLoading ? <ChartSkeleton /> : (
          <LineChart points={points} series={[chartSeries]} area dynamicY unit={chartUnit} />
        )}
        {/* 기간 선택 — 그래프 좌측 아래 */}
        <div className="mt-3">
          <Seg options={RANGE_OPTS} value={range} onChange={setRange} />
        </div>
      </div>

      {/* 카테고리 점유율 도넛 */}
      {cats && cats.categories.length > 0 && (
        <div className="card">
          <h3 className="section-title mb-1">카테고리 점유율</h3>
          <p className="text-xs text-muted mb-5">현재 전체 시청자 중 상위 카테고리(게임/토크 등) 비중</p>
          <CategoryDonut categories={cats.categories} />
        </div>
      )}

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
            {filtered.slice(0, limit).map((s, i) => (
              <tr key={s.chzzk_channel_id}
                  className="border-b border-border/50 hover:bg-bg-hover transition-colors">
                <td className="py-2.5 pl-2 tabular-nums text-muted text-sm">{i + 1}</td>
                <td className="py-2.5">
                  <a href={`https://chzzk.naver.com/${s.chzzk_channel_id}`} target="_blank" rel="noopener noreferrer"
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
                    <ArrowUpRight size={12} className="text-muted opacity-0 group-hover:opacity-100 shrink-0" />
                  </a>
                </td>
                <td className="py-2.5 text-right tabular-nums font-bold" style={{ color: GREEN }}>
                  {nf(s.concurrent_viewers)}
                </td>
                <td className="py-2.5 pl-4 text-muted text-sm hidden sm:table-cell truncate max-w-[140px]">
                  {s.category_name || "-"}
                </td>
                <td className="py-2.5 text-right tabular-nums text-muted text-sm hidden md:table-cell">
                  {s.dur.label}
                </td>
                <td className="py-2.5 pr-2 text-right tabular-nums text-muted text-sm">
                  {s.follower_count > 0 ? nf(s.follower_count) : "-"}
                </td>
              </tr>
            ))}
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
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);
  const [tab, setTab]         = useState<Tab>("overview");

  useEffect(() => {
    Promise.all([
      api.rising.overview(), api.rising.liveRanking(200),
      api.rising.categories(60), api.rising.risingStars(20),
    ])
      .then(([o, r, c, s]) => { setOv(o); setRank(r); setCats(c); setStars(s); })
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
          <p className="text-muted mt-2 text-sm md:text-base flex items-center gap-2 flex-wrap">
            치지직 라이브 방송의 시청자·카테고리 트렌드를 실시간·시계열로 분석합니다.
            {collectedLabel && (
              <span className="inline-flex items-center gap-1 text-muted/70">
                <Circle size={7} className="fill-current" style={{ color: GREEN }} /> 마지막 집계 {collectedLabel}
              </span>
            )}
          </p>
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
              {tab === "overview" && ov && <OverviewTab ov={ov} cats={cats} stars={stars} />}
              {tab === "ranking"  && rank && <RankingTab rank={rank} />}
              {tab === "category" && cats && <CategoryTab cats={cats} />}
            </div>
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
}
