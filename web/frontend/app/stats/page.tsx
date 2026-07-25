"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bot, BarChart3, Users, Flame, Trophy, Radio, ArrowUpRight, Loader2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { RisingOverview, RisingStars, RisingTier, RisingBlueOcean } from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";

const GREEN = "#00FFA3"; // 치지직 네온 그린 — 통계 포털 액센트

// 체급 램프(순서형 규모 → 단일 그린 계열 명도 차). 라이징=가장 밝게(주목 대상).
const TIER_COLORS: Record<string, string> = {
  large:  "#0f6b4a",
  mid:    "#12b17a",
  rising: "#00FFA3",
};

const nf = (n: number) => n.toLocaleString("ko-KR");

type ViewKey = "overview" | "blueocean" | "stars";

const VIEWS: { key: ViewKey; label: string; desc: string; icon: React.ReactNode }[] = [
  { key: "overview",  label: "체급 지형도",     desc: "규모별 방송 분포",     icon: <Users size={17} /> },
  { key: "blueocean", label: "틈새 게임",       desc: "블루오션 카테고리",    icon: <Flame size={17} /> },
  { key: "stars",     label: "급상승 스트리머", desc: "24시간 성장률 랭킹",   icon: <Trophy size={17} /> },
];

// ── SVG 도넛 (체급 분포) ──────────────────────────────────────────────────────
function TierDonut({ tiers }: { tiers: RisingTier[] }) {
  const size = 210, stroke = 26, R = (size - stroke) / 2, C = 2 * Math.PI * R, cx = size / 2, cy = size / 2;
  const [hover, setHover] = useState<number | null>(null);
  const total = tiers.reduce((s, t) => s + t.channels, 0);

  let acc = 0;
  const segs = tiers.map((t, i) => {
    const len = (t.channel_share / 100) * C;
    const dash = Math.max(0, len - 3); // 3px 간격
    const seg = (
      <circle
        key={t.key}
        r={R} cx={cx} cy={cy} fill="none"
        stroke={TIER_COLORS[t.key]}
        strokeWidth={hover === i ? stroke + 5 : stroke}
        strokeDasharray={`${dash} ${C - dash}`}
        strokeDashoffset={-acc}
        transform={`rotate(-90 ${cx} ${cy})`}
        style={{ transition: "stroke-width .15s", cursor: "pointer" }}
        onMouseEnter={() => setHover(i)}
        onMouseLeave={() => setHover(null)}
      />
    );
    acc += len;
    return seg;
  });

  const focus = hover !== null ? tiers[hover] : null;

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
          <circle r={R} cx={cx} cy={cy} fill="none" stroke="rgb(var(--color-bg-hover-rgb))" strokeWidth={stroke} />
          {segs}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          {focus ? (
            <>
              <span className="text-2xl font-extrabold tabular-nums" style={{ color: TIER_COLORS[focus.key] }}>
                {focus.channel_share}%
              </span>
              <span className="text-xs text-muted mt-0.5">{focus.label}</span>
            </>
          ) : (
            <>
              <span className="text-2xl font-extrabold text-fg tabular-nums">{nf(total)}</span>
              <span className="text-xs text-muted mt-0.5">라이브 방송</span>
            </>
          )}
        </div>
      </div>
      {/* 범례 + 직접 라벨 */}
      <div className="grid grid-cols-3 gap-2 w-full mt-5">
        {tiers.map((t, i) => (
          <button
            key={t.key}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            className="flex flex-col items-center rounded-lg py-2 transition-colors hover:bg-bg-hover"
          >
            <span className="flex items-center gap-1.5 text-xs text-muted">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: TIER_COLORS[t.key] }} />
              {t.label}
            </span>
            <span className="text-lg font-bold text-fg tabular-nums mt-0.5">{t.channel_share}%</span>
            <span className="text-[11px] text-muted">{nf(t.channels)}개</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── SVG 수평 막대 (블루오션 / 급상승 공용) ───────────────────────────────────
function HBar({
  rows,
}: {
  rows: { label: string; value: number; valueLabel: string; sub: string; href?: string }[];
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  const [hover, setHover] = useState<number | null>(null);

  return (
    <div className="space-y-1">
      {rows.map((r, i) => {
        const pct = (r.value / max) * 100;
        const Row = (
          <div
            className="relative flex items-center gap-3 rounded-lg px-2.5 py-2 transition-colors"
            style={{ background: hover === i ? "rgb(var(--color-bg-hover-rgb))" : "transparent" }}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          >
            <span className="text-xs font-bold w-5 text-center tabular-nums shrink-0"
                  style={{ color: i < 3 ? GREEN : "rgb(var(--color-muted-rgb))" }}>
              {i + 1}
            </span>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-fg truncate flex items-center gap-1">
                  {r.label}
                  {r.href && <ArrowUpRight size={12} className="text-muted shrink-0" />}
                </span>
                <span className="text-sm font-bold tabular-nums shrink-0" style={{ color: GREEN }}>{r.valueLabel}</span>
              </div>
              {/* 막대: 얇은 마크 + 4px 둥근 데이터엔드 */}
              <div className="mt-1.5 h-2 w-full rounded bg-bg-hover overflow-hidden">
                <div className="h-full" style={{ width: `${pct}%`, background: GREEN, borderRadius: 4 }} />
              </div>
              <p className="text-[11px] text-muted mt-1 truncate">{r.sub}</p>
            </div>
          </div>
        );
        return r.href ? (
          <a key={i} href={r.href} target="_blank" rel="noopener noreferrer" className="block group">{Row}</a>
        ) : (
          <div key={i}>{Row}</div>
        );
      })}
    </div>
  );
}

function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card !p-4">
      <p className="text-xs text-muted">{label}</p>
      <p className="text-xl md:text-2xl font-extrabold text-fg mt-1 tracking-tight tabular-nums">{value}</p>
      {sub && <p className="text-[11px] text-muted mt-0.5">{sub}</p>}
    </div>
  );
}

// ── 우측 상세 패널 ────────────────────────────────────────────────────────────
function DetailPanel({
  view, ov, stars,
}: {
  view: ViewKey; ov: RisingOverview; stars: RisingStars | null;
}) {
  if (view === "overview") {
    const total = ov.summary?.live_count ?? 0;
    const rising = ov.tiers.find((t) => t.key === "rising");
    return (
      <div className="space-y-6">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <StatTile label="현재 라이브" value={nf(total)} sub="집계 시점 기준" />
          <StatTile label="전체 동시 시청자" value={nf(ov.summary?.total_viewers ?? 0)} sub="명" />
          <StatTile label="라이징 비중" value={`${rising?.channel_share ?? 0}%`} sub="1~99명 방송" />
        </div>
        <div className="card">
          <h3 className="section-title mb-1">체급별 지형도</h3>
          <p className="text-sm text-muted mb-6">라이브 방송을 동시 시청자 규모로 나눈 분포입니다.</p>
          <TierDonut tiers={ov.tiers} />
          <p className="text-[11px] text-muted/70 mt-6 text-center">
            대기업 1,000명+ · 허리층 100~999명 · 라이징 1~99명
          </p>
        </div>
      </div>
    );
  }

  if (view === "blueocean") {
    const rows = ov.blue_ocean.map((b: RisingBlueOcean) => ({
      label: b.category,
      value: b.blue_ocean_index,
      valueLabel: `${nf(b.blue_ocean_index)} /방송`,
      sub: `방송 ${nf(b.lives)}개 · 시청자 ${nf(b.viewers)}명`,
    }));
    return (
      <div className="card">
        <h3 className="section-title mb-1">실시간 틈새(블루오션) 게임 TOP 10</h3>
        <p className="text-sm text-muted mb-5">
          블루오션 지수 = 카테고리 시청자 ÷ 방송 수. 방송 1개당 평균 시청자가 많을수록 경쟁 대비 노출 기회가 큽니다.
        </p>
        {rows.length === 0
          ? <p className="text-sm text-muted py-6 text-center">표본이 충분한 카테고리가 아직 없습니다.</p>
          : <HBar rows={rows} />}
      </div>
    );
  }

  // stars
  const rows = (stars?.stars ?? []).map((s) => ({
    label: s.channel_name,
    value: s.growth_rate,
    valueLabel: `+${nf(s.growth_rate)}%`,
    sub: `${s.category || "카테고리 없음"} · ${nf(s.viewers_past)}→${nf(s.viewers_now)}명 · 팔로워 ${nf(s.follower_count)}`,
    href: `https://chzzk.naver.com/${s.chzzk_channel_id}`,
  }));
  return (
    <div className="card">
      <h3 className="section-title mb-1">급상승 스트리머</h3>
      <p className="text-sm text-muted mb-5">24시간 전 대비 동시 시청자 성장률 상위 중소형 채널(현재 1,000명 미만)입니다.</p>
      {rows.length === 0
        ? <p className="text-sm text-muted py-6 text-center">
            {stars?.note || "성장률 집계를 위한 데이터가 아직 충분히 쌓이지 않았습니다. 최소 24시간 후부터 표시됩니다."}
          </p>
        : <HBar rows={rows} />}
    </div>
  );
}

export default function StatsPage() {
  const [ov, setOv]       = useState<RisingOverview | null>(null);
  const [stars, setStars] = useState<RisingStars | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);
  const [view, setView]       = useState<ViewKey>("overview");

  useEffect(() => {
    Promise.all([api.rising.overview(), api.rising.risingStars(20)])
      .then(([o, s]) => { setOv(o); setStars(s); })
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
      {/* Navbar */}
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-6xl mx-auto px-5 flex items-center justify-between" style={{ height: 60 }}>
          <div className="flex items-center gap-2.5">
            <Link href="/" className="flex items-center gap-2 font-bold text-[15px] text-muted hover:text-fg transition-colors">
              <Bot size={18} className="text-accent" /> NexBot
            </Link>
            <span className="text-border">/</span>
            <span className="flex items-center gap-1.5 font-extrabold text-[16px]" style={{ color: GREEN }}>
              <BarChart3 size={17} /> 치지직 통계
            </span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 w-full max-w-6xl mx-auto px-5 py-8 md:py-10">
        {/* Hero */}
        <div className="mb-8">
          <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight leading-tight">
            치지직 <span style={{ color: GREEN }}>중소형 방송</span> 통계
          </h1>
          <p className="text-muted mt-2 leading-relaxed max-w-2xl text-sm md:text-base">
            대형 방송에 가려진 라이징 생태계의 지형도와 유행을 실시간으로 분석합니다.
            {collectedLabel && <span className="text-muted/70"> · 마지막 집계 {collectedLabel}</span>}
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
          // ── 좌: 옵션 / 우: 상세 시각화 ──
          <div className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-5 md:gap-7">
            {/* 좌측 옵션 레일 */}
            <aside className="md:sticky md:top-[76px] md:self-start">
              <p className="text-xs font-semibold text-muted/70 uppercase tracking-wider px-1 mb-2">분석 항목</p>
              <nav className="flex md:flex-col gap-1.5 overflow-x-auto md:overflow-visible pb-1">
                {VIEWS.map((v) => {
                  const active = view === v.key;
                  return (
                    <button
                      key={v.key}
                      onClick={() => setView(v.key)}
                      className="flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors shrink-0 md:w-full border"
                      style={{
                        background: active ? "rgba(0,255,163,0.10)" : "transparent",
                        borderColor: active ? "rgba(0,255,163,0.35)" : "transparent",
                      }}
                    >
                      <span style={{ color: active ? GREEN : "rgb(var(--color-muted-rgb))" }}>{v.icon}</span>
                      <span className="min-w-0">
                        <span className="block text-sm font-semibold" style={{ color: active ? GREEN : undefined }}>{v.label}</span>
                        <span className="block text-[11px] text-muted whitespace-nowrap">{v.desc}</span>
                      </span>
                    </button>
                  );
                })}
              </nav>
              <p className="hidden md:block text-[11px] text-muted/60 mt-4 px-1 leading-relaxed">
                약 10분 주기로 치지직 공개 라이브 목록을 수집합니다. 비공식 서비스로 실제 수치와 오차가 있을 수 있습니다.
              </p>
            </aside>

            {/* 우측 상세 */}
            <div>
              <DetailPanel view={view} ov={ov} stars={stars} />
            </div>
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
}
