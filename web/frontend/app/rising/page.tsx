"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Bot, TrendingUp, Flame, Trophy, Radio, Users, Gamepad2, ArrowUpRight, Loader2,
} from "lucide-react";
import { api } from "@/lib/api";
import type { RisingOverview, RisingStars } from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";

const GREEN = "#00FFA3"; // CHZZK 네온 그린 — Rising 포털 액센트

const nf = (n: number) => n.toLocaleString("ko-KR");

// 체급 구간별 색상(그린 계열 명도 차)
const TIER_COLORS: Record<string, string> = {
  large:  "#0f6b4a",
  mid:    "#12b17a",
  rising: "#00FFA3",
};

function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card !p-4 md:!p-5">
      <p className="text-xs text-muted">{label}</p>
      <p className="text-2xl md:text-3xl font-extrabold text-fg mt-1 tracking-tight tabular-nums">{value}</p>
      {sub && <p className="text-xs text-muted mt-0.5">{sub}</p>}
    </div>
  );
}

function SectionHeader({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
  return (
    <div className="flex items-start gap-2.5 mb-4">
      <div className="mt-0.5" style={{ color: GREEN }}>{icon}</div>
      <div>
        <h2 className="text-xl font-bold text-fg tracking-tight">{title}</h2>
        <p className="text-sm text-muted mt-0.5">{desc}</p>
      </div>
    </div>
  );
}

// ── 체급별 지형도 ─────────────────────────────────────────────────────────────
function TierMap({ ov }: { ov: RisingOverview }) {
  const total = ov.tiers.reduce((s, t) => s + t.channels, 0);
  return (
    <section>
      <SectionHeader
        icon={<Users size={20} />}
        title="체급별 지형도"
        desc="현재 라이브 중인 방송을 동시 시청자 규모로 나눈 분포입니다."
      />
      {/* 스택 바 */}
      <div className="flex h-4 w-full overflow-hidden rounded-full bg-bg-hover mb-5">
        {ov.tiers.map((t) => (
          <div
            key={t.key}
            style={{ width: `${t.channel_share}%`, background: TIER_COLORS[t.key] }}
            title={`${t.label} ${t.channel_share}%`}
          />
        ))}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {ov.tiers.map((t) => (
          <div key={t.key} className="card !p-4">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: TIER_COLORS[t.key] }} />
              <span className="text-sm font-semibold text-fg">{t.label}</span>
            </div>
            <p className="text-3xl font-extrabold text-fg mt-2 tabular-nums">{t.channel_share}%</p>
            <p className="text-xs text-muted mt-1">
              방송 {nf(t.channels)}개 · 시청자 {nf(t.viewers)}명
            </p>
          </div>
        ))}
      </div>
      <p className="text-xs text-muted/70 mt-3">
        대기업 1,000명+ · 허리층 100~999명 · 라이징 1~99명 (전체 {nf(total)}개 방송 기준)
      </p>
    </section>
  );
}

// ── 틈새(블루오션) 게임 TOP ───────────────────────────────────────────────────
function BlueOcean({ ov }: { ov: RisingOverview }) {
  const max = Math.max(1, ...ov.blue_ocean.map((b) => b.blue_ocean_index));
  return (
    <section>
      <SectionHeader
        icon={<Flame size={20} />}
        title="실시간 틈새(블루오션) 게임 TOP 10"
        desc="방송 수 대비 시청 유입이 높은 카테고리 — 방송 1개당 평균 시청자가 많을수록 경쟁 대비 노출 기회가 큽니다."
      />
      {ov.blue_ocean.length === 0 ? (
        <p className="text-sm text-muted card">표본이 충분한 카테고리가 아직 없습니다.</p>
      ) : (
        <div className="space-y-2">
          {ov.blue_ocean.map((b, i) => (
            <div key={b.category} className="card !p-3.5 flex items-center gap-3">
              <span className="text-sm font-bold w-6 text-center tabular-nums"
                    style={{ color: i < 3 ? GREEN : undefined }}>
                {i + 1}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-fg truncate">{b.category}</span>
                  <span className="text-sm font-bold tabular-nums shrink-0" style={{ color: GREEN }}>
                    {nf(b.blue_ocean_index)}
                    <span className="text-[10px] text-muted font-normal ml-1">/ 방송</span>
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 w-full rounded-full bg-bg-hover overflow-hidden">
                  <div className="h-full rounded-full"
                       style={{ width: `${(b.blue_ocean_index / max) * 100}%`, background: GREEN }} />
                </div>
                <p className="text-[11px] text-muted mt-1">
                  방송 {nf(b.lives)}개 · 시청자 {nf(b.viewers)}명
                </p>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ── 이주의 라이징 스타 ────────────────────────────────────────────────────────
function RisingStarList({ data }: { data: RisingStars }) {
  return (
    <section>
      <SectionHeader
        icon={<Trophy size={20} />}
        title="이주의 라이징 스트리머"
        desc="24시간 전 대비 동시 시청자 성장률이 높은 중소형 채널(현재 1,000명 미만)입니다."
      />
      {data.stars.length === 0 ? (
        <p className="text-sm text-muted card">
          {data.note || "성장률 집계를 위한 데이터가 아직 충분히 쌓이지 않았습니다. 최소 24시간 후부터 표시됩니다."}
        </p>
      ) : (
        <div className="space-y-2">
          {data.stars.map((s, i) => (
            <a
              key={s.chzzk_channel_id}
              href={`https://chzzk.naver.com/${s.chzzk_channel_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="card !p-3.5 flex items-center gap-3 hover:border-accent/40 transition-colors group"
            >
              <span className="text-sm font-bold w-6 text-center tabular-nums"
                    style={{ color: i < 3 ? GREEN : undefined }}>
                {i + 1}
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-fg truncate">{s.channel_name}</span>
                  <ArrowUpRight size={13} className="text-muted opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                </div>
                <p className="text-[11px] text-muted mt-0.5 truncate">
                  {s.category || "카테고리 없음"} · 팔로워 {nf(s.follower_count)}
                </p>
              </div>
              <div className="text-right shrink-0">
                <p className="text-sm font-bold tabular-nums flex items-center gap-1 justify-end" style={{ color: GREEN }}>
                  <TrendingUp size={13} /> +{nf(s.growth_rate)}%
                </p>
                <p className="text-[11px] text-muted tabular-nums">
                  {nf(s.viewers_past)} → {nf(s.viewers_now)}명
                </p>
              </div>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}

export default function RisingPage() {
  const [ov, setOv]       = useState<RisingOverview | null>(null);
  const [stars, setStars] = useState<RisingStars | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(false);

  useEffect(() => {
    Promise.all([api.rising.overview(), api.rising.risingStars(20)])
      .then(([o, s]) => { setOv(o); setStars(s); })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, []);

  const collectedLabel = ov?.collected_at
    ? new Date(ov.collected_at * 1000).toLocaleString("ko-KR", { hour: "2-digit", minute: "2-digit", month: "long", day: "numeric" })
    : null;

  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      {/* Navbar */}
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-5xl mx-auto px-5 flex items-center justify-between" style={{ height: 60 }}>
          <div className="flex items-center gap-2.5">
            <Link href="/" className="flex items-center gap-2 font-bold text-[15px] text-muted hover:text-fg transition-colors">
              <Bot size={18} className="text-accent" /> NexBot
            </Link>
            <span className="text-border">/</span>
            <span className="flex items-center gap-1.5 font-extrabold text-[16px]" style={{ color: GREEN }}>
              <Radio size={17} /> CHZZK Rising
            </span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 w-full max-w-5xl mx-auto px-5 py-8 md:py-10 space-y-10">
        {/* Hero */}
        <div>
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight leading-tight">
            치지직 <span style={{ color: GREEN }}>라이징</span> 트렌드
          </h1>
          <p className="text-muted mt-2 leading-relaxed max-w-2xl">
            대형 방송에 가려진 중소형·라이징 스트리머 생태계의 지형도와 유행을 실시간으로 살펴보세요.
            성장 중인 채널과 경쟁이 덜한 틈새 카테고리를 한눈에.
          </p>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 text-muted py-24">
            <Loader2 size={18} className="animate-spin" /> 데이터를 불러오는 중...
          </div>
        ) : error ? (
          <div className="card text-center py-16 text-muted">
            데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.
          </div>
        ) : !ov || ov.collected_at === null ? (
          <div className="card text-center py-16">
            <Radio size={36} className="mx-auto mb-3 opacity-30" style={{ color: GREEN }} />
            <p className="font-medium text-fg">데이터를 수집하고 있습니다.</p>
            <p className="text-sm text-muted mt-1">첫 집계가 완료되면 곧 트렌드가 표시됩니다.</p>
          </div>
        ) : (
          <>
            {/* 요약 통계 */}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <StatTile label="현재 라이브 방송" value={nf(ov.summary?.live_count ?? 0)} sub="집계 시점 기준" />
              <StatTile label="전체 동시 시청자" value={nf(ov.summary?.total_viewers ?? 0)} sub="명" />
              <StatTile label="마지막 집계" value={collectedLabel ?? "-"} sub="약 10분 주기 갱신" />
            </div>

            <TierMap ov={ov} />
            <BlueOcean ov={ov} />
            {stars && <RisingStarList data={stars} />}

            <p className="text-xs text-muted/60 text-center pt-2">
              데이터 출처: 치지직 공개 라이브 목록 · 약 10분 주기 수집 · 비공식 서비스로 실제 수치와 오차가 있을 수 있습니다.
            </p>
          </>
        )}
      </main>

      <Footer />
    </div>
  );
}
