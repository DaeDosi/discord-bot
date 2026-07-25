"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bot, BarChart3, LineChart as LineIcon, ListOrdered, Gamepad2, Radio,
  TrendingUp, ArrowUpRight, Loader2, Search, Circle,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  RisingOverview, RisingTimeseries, RisingLiveRanking, RisingCategories, RisingStars,
} from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";
import LineChart, { type LinePoint } from "./LineChart";

// 2톤 그라데이션(그린 → 시안) — 브랜드 액센트
const GREEN = "#00FFA3";
const CYAN  = "#00C2FF";
const GRAD  = `linear-gradient(135deg, ${GREEN}, ${CYAN})`;
const TIER_LINE = { rising: "#00FFA3", mid: "#00C2FF", large: "#8B93A7" };

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

// 직전 대비 증감 뱃지
function Delta({ pct }: { pct: number | null }) {
  if (pct === null || !isFinite(pct)) return null;
  const up = pct >= 0;
  return (
    <span className="text-[11px] font-semibold tabular-nums" style={{ color: up ? GREEN : "#F87171" }}>
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

function StatTile({ label, value, sub, accent, delta }:
  { label: string; value: string; sub?: string; accent?: boolean; delta?: number | null }) {
  return (
    <div className="card !p-4">
      <p className="text-xs text-muted">{label}</p>
      <p className="text-xl md:text-2xl font-extrabold mt-1 tracking-tight tabular-nums">
        {accent ? <GradText>{value}</GradText> : value}
      </p>
      <p className="text-[11px] mt-0.5 flex items-center gap-1.5">
        {delta !== undefined && <Delta pct={delta} />}
        {sub && <span className="text-muted">{sub}</span>}
      </p>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-muted">
      <span className="w-2.5 h-2.5 rounded-full" style={{ background: color }} /> {label}
    </span>
  );
}

// ── 개요 탭 ───────────────────────────────────────────────────────────────────
function OverviewTab({ ov, ts, stars }: { ov: RisingOverview; ts: RisingTimeseries; stars: RisingStars | null }) {
  const rising = ov.tiers.find((t) => t.key === "rising");
  const points = ts.points as unknown as LinePoint[];

  // 직전 수집 대비 증감(%)
  const pts = ts.points;
  const delta = (key: "total_viewers" | "live_count"): number | null => {
    if (pts.length < 2) return null;
    const cur = pts[pts.length - 1][key];
    const prev = pts[pts.length - 2][key];
    if (!prev) return null;
    return ((cur - prev) / prev) * 100;
  };

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatTile label="현재 라이브" value={nf(ov.summary?.live_count ?? 0)} sub="직전 대비" delta={delta("live_count")} />
        <StatTile label="전체 동시 시청자" value={nf(ov.summary?.total_viewers ?? 0)} sub="직전 대비" accent delta={delta("total_viewers")} />
        <StatTile label="라이징 비중" value={`${rising?.channel_share ?? 0}%`} sub="1~99명 방송" />
        <StatTile label="대기업 방송" value={nf(ov.tiers.find((t) => t.key === "large")?.channels ?? 0)} sub="1,000명+" />
      </div>

      {/* 총 시청자 추이 (단일 시계열, 2톤 그라데이션) */}
      <div className="card">
        <h3 className="section-title mb-1">전체 동시 시청자 추이</h3>
        <p className="text-xs text-muted mb-4">약 10분 주기 수집 · 치지직 전체 라이브 합계</p>
        <LineChart points={points}
          series={[{ key: "total_viewers", name: "총 시청자", color: GREEN, gradient: [GREEN, CYAN] }]}
          area unit="명" />
      </div>

      {/* 체급별 방송 수 추이 (다중 시계열) */}
      <div className="card">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h3 className="section-title">체급별 방송 수 추이</h3>
          <div className="flex items-center gap-3">
            <LegendDot color={TIER_LINE.rising} label="라이징(1~99)" />
            <LegendDot color={TIER_LINE.mid} label="허리층(100~999)" />
            <LegendDot color={TIER_LINE.large} label="대기업(1000+)" />
          </div>
        </div>
        <p className="text-xs text-muted mb-4">규모별 라이브 방송 수가 시간에 따라 어떻게 변하는지</p>
        <LineChart
          points={points}
          series={[
            { key: "rising", name: "라이징", color: TIER_LINE.rising },
            { key: "mid",    name: "허리층", color: TIER_LINE.mid },
            { key: "large",  name: "대기업", color: TIER_LINE.large },
          ]}
          unit="개"
        />
      </div>

      {/* 급상승 스트리머 (컴팩트) */}
      <div className="card">
        <h3 className="section-title mb-1">급상승 스트리머</h3>
        <p className="text-xs text-muted mb-4">24시간 전 대비 시청자 성장률 상위(현재 1,000명 미만)</p>
        {!stars || stars.stars.length === 0 ? (
          <p className="text-sm text-muted py-4 text-center">
            {stars?.note || "성장률 집계용 데이터가 아직 부족합니다. 최소 24시간 후부터 표시됩니다."}
          </p>
        ) : (
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
                <td className="py-2.5 pl-2 tabular-nums text-muted text-xs">{i + 1}</td>
                <td className="py-2.5">
                  <a href={`https://chzzk.naver.com/${s.chzzk_channel_id}`} target="_blank" rel="noopener noreferrer"
                     className="font-semibold text-fg hover:text-accent transition-colors inline-flex items-center gap-1 group">
                    <span className="truncate max-w-[160px] md:max-w-none">{s.channel_name}</span>
                    <ArrowUpRight size={12} className="text-muted opacity-0 group-hover:opacity-100 shrink-0" />
                  </a>
                </td>
                <td className="py-2.5 text-right tabular-nums font-bold" style={{ color: GREEN }}>
                  {nf(s.concurrent_viewers)}
                </td>
                <td className="py-2.5 pl-4 text-muted text-xs hidden sm:table-cell truncate max-w-[140px]">
                  {s.category_name || "-"}
                </td>
                <td className="py-2.5 text-right tabular-nums text-muted text-xs hidden md:table-cell">
                  {s.dur.label}
                </td>
                <td className="py-2.5 pr-2 text-right tabular-nums text-muted text-xs">{nf(s.follower_count)}</td>
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
  const [ts, setTs]       = useState<RisingTimeseries | null>(null);
  const [rank, setRank]   = useState<RisingLiveRanking | null>(null);
  const [cats, setCats]   = useState<RisingCategories | null>(null);
  const [stars, setStars] = useState<RisingStars | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);
  const [tab, setTab]         = useState<Tab>("overview");

  useEffect(() => {
    Promise.all([
      api.rising.overview(), api.rising.timeseries(48),
      api.rising.liveRanking(200), api.rising.categories(60), api.rising.risingStars(20),
    ])
      .then(([o, t, r, c, s]) => { setOv(o); setTs(t); setRank(r); setCats(c); setStars(s); })
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
        <div className="max-w-6xl mx-auto px-5 flex items-center justify-between" style={{ height: 60 }}>
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

      <main className="flex-1 w-full max-w-6xl mx-auto px-5 py-7 md:py-9">
        <div className="mb-6">
          <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight leading-tight">
            치지직 <GradText>중소형 방송</GradText> 통계
          </h1>
          <p className="text-muted mt-2 text-sm md:text-base flex items-center gap-2 flex-wrap">
            대형 방송에 가려진 라이징 생태계를 실시간·시계열로 분석합니다.
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
              {tab === "overview" && ov && ts && <OverviewTab ov={ov} ts={ts} stars={stars} />}
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
