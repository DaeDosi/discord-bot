"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Bot, BarChart3, ArrowLeft, ExternalLink, Loader2, Lock, Radio, Flame, LayoutGrid, TrendingUp,
} from "lucide-react";
import { api } from "@/lib/api";
import type { StreamerDashboard } from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";
import LineChart, { type LinePoint } from "../../LineChart";

const GREEN = "#00FFA3";
const PURPLE = "#A855F7";
const nf = (n: number) => n.toLocaleString("ko-KR");

const SUB_TABS = ["요약", "통계", "카테고리", "랭킹", "방송기록", "구간분석"];
const HEAT_LEVELS = ["rgba(0,255,163,0.08)", "rgba(0,255,163,0.28)", "rgba(0,255,163,0.5)", "rgba(0,255,163,0.72)", "#00FFA3"];

// 반원 게이지 (상위 카테고리 비중) — 순수 SVG arc.
// 호 범위는 항상 180° 이하라 large-arc-flag는 언제나 0 (0.5 초과 시 major arc로
// 무너지던 버그 수정). sweep-flag=1로 좌→우 상단 반원, 스트로크 기반이라 radius 충돌 없음.
function SemiGauge({ share, label }: { share: number; label: string }) {
  const w = 200, sw = 20, R = 82, cx = w / 2, cy = 98;
  const h = cy + sw / 2 + 4; // 캡이 잘리지 않게 여유
  const frac = Math.max(0, Math.min(100, share)) / 100;
  const P = (deg: number): [number, number] =>
    [cx + R * Math.cos((Math.PI * deg) / 180), cy + R * Math.sin((Math.PI * deg) / 180)];
  const [lx, ly] = P(180);                 // 좌측 끝 (cx-R, cy)
  const [rx, ry] = P(360);                 // 우측 끝 (cx+R, cy)
  const [ex, ey] = P(180 + frac * 180);    // 채움 끝점
  return (
    <div className="flex flex-col items-center">
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
        <path d={`M ${lx} ${ly} A ${R} ${R} 0 0 1 ${rx} ${ry}`}
              fill="none" stroke="rgb(var(--color-bg-hover-rgb))" strokeWidth={sw} strokeLinecap="round" />
        {frac > 0.002 && (
          <path d={`M ${lx} ${ly} A ${R} ${R} 0 0 1 ${ex} ${ey}`}
                fill="none" stroke={GREEN} strokeWidth={sw} strokeLinecap="round" />
        )}
      </svg>
      <div className="-mt-9 text-center">
        <p className="text-2xl font-extrabold tabular-nums" style={{ color: GREEN }}>{share.toFixed(0)}%</p>
        <p className="text-xs text-muted truncate max-w-[160px]">{label}</p>
      </div>
    </div>
  );
}

// KPI 타일 (잠금 지원)
function Tile({ label, value, unit, delta, locked }:
  { label: string; value?: string; unit?: string; delta?: string; locked?: boolean }) {
  return (
    <div className="rounded-lg border border-border bg-bg p-3" style={{ opacity: locked ? 0.55 : 1 }}>
      <p className="text-xs text-muted flex items-center gap-1">{label} {locked && <Lock size={11} />}</p>
      <p className="text-base font-bold mt-1 tabular-nums">
        {locked ? "—" : <>{value}{unit && <span className="text-xs text-muted font-normal ml-0.5">{unit}</span>}</>}
      </p>
      {!locked && delta && <p className="text-[11px] text-muted mt-0.5">{delta}</p>}
    </div>
  );
}

// GitHub 스타일 잔디 히트맵
function Heatmap({ daily, metric }:
  { daily: NonNullable<StreamerDashboard["daily"]>; metric: "minutes" | "viewership" | "peak" | "avg_viewers" }) {
  const map = useMemo(() => {
    const m: Record<string, number> = {};
    daily.forEach((d) => { m[d.date] = d[metric]; });
    return m;
  }, [daily, metric]);

  const cells = useMemo(() => {
    const end = new Date(); end.setHours(0, 0, 0, 0);
    const start = new Date(end); start.setDate(end.getDate() - 181);
    start.setDate(start.getDate() - start.getDay()); // 일요일 정렬
    const out: { key: string; val: number }[] = [];
    for (const d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      out.push({ key, val: map[key] ?? 0 });
    }
    return out;
  }, [map]);

  const maxV = Math.max(1, ...cells.map((c) => c.val));
  const level = (v: number) => v <= 0 ? 0 : Math.min(4, 1 + Math.floor((v / maxV) * 3.999));

  return (
    <div className="overflow-x-auto">
      <div className="grid gap-[3px]" style={{ gridAutoFlow: "column", gridTemplateRows: "repeat(7, 11px)" }}>
        {cells.map((c) => (
          <div key={c.key} title={`${c.key} · ${nf(c.val)}`}
               className="rounded-[2px]" style={{ width: 11, height: 11, background: HEAT_LEVELS[level(c.val)] }} />
        ))}
      </div>
      <div className="flex items-center gap-1.5 mt-3 text-[11px] text-muted">
        적음
        {HEAT_LEVELS.map((c, i) => <span key={i} className="rounded-[2px]" style={{ width: 11, height: 11, background: c }} />)}
        많음
      </div>
    </div>
  );
}

export default function StreamerPage() {
  const { channelId } = useParams<{ channelId: string }>();
  const [days, setDays] = useState(30);
  const [heatMetric, setHeatMetric] = useState<"minutes" | "viewership" | "peak" | "avg_viewers">("minutes");
  const [data, setData] = useState<StreamerDashboard | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.rising.streamer(channelId, days)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [channelId, days]);

  const s = data?.summary;
  const topCat = s?.categories?.[0];
  const weeklyPoints = (data?.weekly ?? []).map((w) => ({ t: w.t, avg_viewers: w.avg_viewers, viewership: w.viewership })) as unknown as LinePoint[];

  // 셸(min-h-screen / Footer)은 layout.tsx가 소유한다 — 서버에서 렌더하는 SEO 소개
  // 섹션이 푸터 위에 오도록 하기 위함.
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

      <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 md:px-6 py-6 space-y-6">
        {loading ? (
          <div className="flex items-center justify-center gap-2 text-muted py-24">
            <Loader2 size={18} className="animate-spin" /> 불러오는 중...
          </div>
        ) : !data || !data.found ? (
          <div className="card text-center py-16">
            <Radio size={34} className="mx-auto mb-3 opacity-30" style={{ color: GREEN }} />
            <p className="font-medium text-fg">최근 수집된 방송 데이터가 없습니다.</p>
            <p className="text-sm text-muted mt-1">이 채널이 라이브를 켜면 수집이 시작되고, 이후 분석이 표시됩니다.</p>
          </div>
        ) : (
          <>
            {/* 헤더: 프로필 + 서브탭 */}
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <span className="w-12 h-12 rounded-xl overflow-hidden bg-bg-hover shrink-0">
                  {data.channel_image_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={data.channel_image_url} alt="" width={48} height={48} className="w-full h-full object-cover" />
                  )}
                </span>
                <div className="min-w-0">
                  <h1 className="text-lg font-bold flex items-center gap-2">
                    {data.channel_name}
                    {data.is_live && (
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>LIVE</span>
                    )}
                  </h1>
                  <p className="text-xs text-muted truncate max-w-[70vw]">{data.live_title || data.channel_id}</p>
                  {data.first_broadcast && (
                    <p className="text-[11px] text-muted/80 mt-0.5">🌱 첫 방송(추정) {data.first_broadcast.slice(0, 10)}</p>
                  )}
                </div>
                <a href={`https://chzzk.naver.com/${data.channel_id}`} target="_blank" rel="noopener noreferrer"
                   className="ml-auto btn-secondary text-xs flex items-center gap-1 shrink-0">
                  치지직 <ExternalLink size={12} />
                </a>
              </div>
              <div className="flex gap-2 border-b border-border pb-3 overflow-x-auto">
                {SUB_TABS.map((t, i) => (
                  <button key={t} disabled={i !== 0}
                    className="px-4 py-1.5 rounded-full text-sm whitespace-nowrap transition-colors"
                    style={i === 0
                      ? { background: "rgba(0,255,163,0.12)", color: GREEN, fontWeight: 700 }
                      : { color: "rgb(var(--color-muted-rgb))", opacity: 0.5 }}>
                    {t}
                  </button>
                ))}
              </div>
            </div>

            {/* 2열 그리드 (좌 30% / 우 70%) */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              {/* 좌 */}
              <div className="lg:col-span-4 space-y-4">
                <div className="card !p-5">
                  <span className="text-xs text-muted">★ 팔로워</span>
                  <div className="text-3xl font-extrabold mt-2 tabular-nums">
                    {nf(data.follower_count ?? 0)} <span className="text-sm font-normal text-muted">명</span>
                  </div>
                  <p className="text-[11px] text-muted mt-1">최근 {data.history_days ?? 0}일 데이터 · 최고 {nf(s?.max_follower ?? 0)}명</p>
                </div>

                <div className="card !p-5">
                  <span className="text-xs text-muted">카테고리별 뷰어쉽 비중</span>
                  <div className="mt-3">
                    {topCat ? <SemiGauge share={topCat.share} label={topCat.category} />
                            : <p className="text-sm text-muted text-center py-6">데이터 없음</p>}
                  </div>
                </div>

                <div className="card !p-5">
                  <span className="text-xs text-muted">최근 {data.history_days ?? 0}일 누적</span>
                  <div className="mt-2 space-y-1.5">
                    <div className="flex items-baseline justify-between">
                      <span className="text-sm text-muted">누적 뷰어쉽</span>
                      <span className="text-lg font-extrabold tabular-nums"><span style={{ color: PURPLE }}>{nf(s?.viewership ?? 0)}</span> <span className="text-xs text-muted font-normal">뷰어·h</span></span>
                    </div>
                    <div className="flex items-baseline justify-between">
                      <span className="text-sm text-muted">총 방송 시간</span>
                      <span className="text-lg font-extrabold tabular-nums">{nf(s?.broadcast_hours ?? 0)} <span className="text-xs text-muted font-normal">시간</span></span>
                    </div>
                    <div className="flex items-baseline justify-between">
                      <span className="text-sm text-muted">방송 일수</span>
                      <span className="text-lg font-extrabold tabular-nums">{nf(s?.active_days ?? 0)} <span className="text-xs text-muted font-normal">일</span></span>
                    </div>
                  </div>
                </div>
              </div>

              {/* 우 */}
              <div className="lg:col-span-8 space-y-6">
                {/* 요약 데이터 */}
                <div className="card">
                  <div className="flex justify-between items-center mb-4 flex-wrap gap-2">
                    <h3 className="font-bold text-base flex items-center gap-2"><Flame size={17} style={{ color: GREEN }} /> 스트리머 요약 데이터</h3>
                    <select value={days} onChange={(e) => setDays(Number(e.target.value))}
                      className="bg-bg border border-border text-xs rounded-lg px-3 py-1.5 focus:outline-none focus:border-accent">
                      <option value={7}>최근 7일</option>
                      <option value={14}>최근 14일</option>
                      <option value={30}>최근 30일</option>
                    </select>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                    <Tile label="동시 최고 시청자" value={nf(s?.peak_viewers ?? 0)} unit="명" />
                    <Tile label="평균 시청자" value={nf(s?.avg_viewers ?? 0)} unit="명" />
                    <Tile label="뷰어쉽" value={nf(s?.viewership ?? 0)} unit="뷰어·h" />
                    <Tile label="방송 시간" value={nf(s?.broadcast_hours ?? 0)} unit="시간" />
                    <Tile label="최고 순위" locked />
                    <Tile label="최고 팔로워" value={nf(s?.max_follower ?? 0)} unit="명" />
                    <Tile label="6분 최고 채팅" locked />
                    <Tile label="6분 평균 채팅" locked />
                  </div>
                  <p className="text-[11px] text-muted/70 mt-3">🔒 채팅·순위 지표는 아직 수집하지 않습니다. 방송시간·뷰어쉽은 10분 스냅샷 기반 추정치입니다.</p>
                </div>

                {/* 잔디 히트맵 */}
                <div className="card">
                  <div className="flex justify-between items-center mb-4 flex-wrap gap-2">
                    <h3 className="font-bold text-base flex items-center gap-2"><LayoutGrid size={17} style={{ color: GREEN }} /> 스트리머 활동 잔디</h3>
                    <div className="flex items-center gap-1">
                      {([["minutes", "방송시간"], ["viewership", "뷰어쉽"], ["peak", "최고"], ["avg_viewers", "평균"]] as const).map(([k, lab]) => (
                        <button key={k} onClick={() => setHeatMetric(k)}
                          className="text-xs px-2.5 py-1 rounded-md border transition-colors"
                          style={{ background: heatMetric === k ? "rgba(0,255,163,0.1)" : "transparent",
                                   borderColor: heatMetric === k ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                                   color: heatMetric === k ? GREEN : "rgb(var(--color-muted-rgb))" }}>
                          {lab}
                        </button>
                      ))}
                    </div>
                  </div>
                  <Heatmap daily={data.daily ?? []} metric={heatMetric} />
                  <p className="text-[11px] text-muted/70 mt-2">최근 약 6개월 격자 · 데이터는 수집 시작({data.history_days ?? 0}일 전후)부터 채워집니다.</p>
                </div>

                {/* 주별 추이 */}
                <div className="card">
                  <h3 className="font-bold text-base flex items-center gap-2 mb-4"><TrendingUp size={17} style={{ color: GREEN }} /> 주별 평균 시청자 추이</h3>
                  {weeklyPoints.length >= 2 ? (
                    <LineChart points={weeklyPoints}
                      series={[{ key: "avg_viewers", name: "주 평균 시청자", color: GREEN, gradient: [GREEN, "#00C2FF"] }]}
                      area dynamicY unit="명" />
                  ) : (
                    <p className="text-sm text-muted py-8 text-center">주별 추이를 그리기엔 데이터가 부족합니다(2주 이상 필요).</p>
                  )}
                </div>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
