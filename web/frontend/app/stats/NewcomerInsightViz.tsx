"use client";
import { useMemo } from "react";
import type { NewcomerInsights, NewcomerCategory, NewcomerSummary } from "@/lib/types";

// 신규 스트리머 분석 탭의 인사이트 시각화 3종.
// 데이터는 모두 newcomers 응답에 함께 실려 오므로 별도 요청이 없다.

const GREEN = "#00FFA3";
const nf = (n: number) => n.toLocaleString("ko-KR");
const Sub = ({ children }: { children: React.ReactNode }) =>
  <p className="mt-2 text-[11px] leading-relaxed text-muted">{children}</p>;

// ── ① 24시간 골든타임 히트맵 ────────────────────────────────────────────────
export function GoldenHourHeatmap({ hourly }: { hourly: NonNullable<NewcomerInsights["hourly"]> }) {
  const { max, peakLabel } = useMemo(() => {
    const mx = Math.max(1, ...hourly.map((h) => h.avg_viewers));
    // 피크타임 라벨: 최고 시간대를 중심으로 연속된 상위 구간(최고의 75% 이상)을 묶는다
    const hot = hourly.filter((h) => h.avg_viewers >= mx * 0.75).map((h) => h.hour).sort((a, b) => a - b);
    if (hot.length === 0) return { max: mx, peakLabel: null };
    // 자정을 넘는 연속 구간(예: 22,23,0,1)도 하나로 보이도록 순환 기준으로 최장 구간을 찾는다
    let best = [hot[0]], cur = [hot[0]];
    for (let i = 1; i < hot.length; i++) {
      if (hot[i] === hot[i - 1] + 1) cur.push(hot[i]);
      else { if (cur.length > best.length) best = cur; cur = [hot[i]]; }
    }
    if (cur.length > best.length) best = cur;
    if (hot.includes(0) && hot.includes(23)) {
      const tail: number[] = [], head: number[] = [];
      for (let h = 23; hot.includes(h); h--) tail.unshift(h);
      for (let h = 0; hot.includes(h); h++) head.push(h);
      if (tail.length + head.length > best.length) best = [...tail, ...head];
    }
    const s = best[0], e = best[best.length - 1];
    const p2 = (n: number) => String(n).padStart(2, "0");
    return { max: mx, peakLabel: `${p2(s)}:00 ~ ${p2((e + 1) % 24)}:00` };
  }, [hourly]);

  const hasData = hourly.some((h) => h.snaps > 0);

  return (
    <div className="card flex h-full flex-col">
      <div className="mb-3 flex items-center justify-between gap-2 flex-wrap">
        <h3 className="section-title">24시간 신입 노출 골든타임 분석</h3>
        {peakLabel && (
          <span className="rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
                style={{ color: GREEN, background: "rgba(0,255,163,0.10)" }}>
            피크타임: {peakLabel}
          </span>
        )}
      </div>

      {!hasData ? (
        <p className="py-8 text-center text-sm text-muted">시간대 데이터가 아직 부족합니다.</p>
      ) : (
        <>
          {/* Tailwind 기본 grid-cols는 12까지라 md:grid-cols-24는 무효(12칸 2줄이 된다).
              임의값 문법으로 24칸을 만든다 — 모바일은 12칸 2줄이 오히려 읽기 좋다. */}
          <div className="grid grid-cols-12 gap-1 md:grid-cols-[repeat(24,minmax(0,1fr))]">
            {hourly.map((h) => {
              const r = h.avg_viewers / max;
              const bg = h.snaps === 0 ? "rgb(var(--color-bg-hover-rgb))"
                : r >= 0.8 ? GREEN
                : r >= 0.5 ? "rgba(0,255,163,0.55)"
                : r >= 0.25 ? "rgba(0,255,163,0.28)"
                : "rgba(0,255,163,0.12)";
              return (
                <div key={h.hour} className="group relative">
                  <div className="h-8 cursor-pointer rounded-sm transition-transform hover:scale-110"
                       style={{ background: bg }} />
                  <span className="mt-0.5 block text-center text-[9px] tabular-nums text-muted/70">
                    {h.hour % 3 === 0 ? h.hour : ""}
                  </span>
                  {/* 호버 툴팁 */}
                  <div className="pointer-events-none absolute bottom-10 left-1/2 z-20 hidden -translate-x-1/2
                                  whitespace-nowrap rounded-lg border border-border bg-bg-card px-2.5 py-1.5
                                  text-[10px] text-fg shadow-2xl group-hover:block">
                    {String(h.hour).padStart(2, "0")}시: 신입 평균 <b>{nf(h.avg_viewers)}명</b>
                    {" "}(경쟁 채널 {nf(h.channels)}개)
                  </div>
                </div>
              );
            })}
          </div>
          <Sub>
            * 초록색이 짙을수록 신입 방송 대비 시청자 유입이 높은 골든타임 시간대입니다.
            최근 24시간 집계 기준이며, 회색은 해당 시간대에 수집된 신입 방송이 없다는 뜻입니다.
          </Sub>
        </>
      )}
    </div>
  );
}

// ── 대기업 방종 '빈집 타임' (소형 탭) ───────────────────────────────────────
// 시간대별로 대형 채널 동시 라이브 수(막대, 회색)와 소형 채널 방송당 평균 시청자(막대, 네온)를
// 겹쳐 보여 준다. 대형이 적은데 소형 평균이 높은 시간이 노려볼 만한 '빈집'이다.
export function VacancyHours({ hourly, best }: {
  hourly: NonNullable<NewcomerInsights["vacancy_hourly"]>;
  best: NonNullable<NewcomerInsights["vacancy_best"]> | null | undefined;
}) {
  const { maxBig, maxSmall, hasData } = useMemo(() => ({
    maxBig:   Math.max(1, ...hourly.map((h) => h.big_lives)),
    maxSmall: Math.max(1, ...hourly.map((h) => h.small_avg_viewers)),
    hasData:  hourly.some((h) => h.snaps > 0),
  }), [hourly]);
  const p2 = (n: number) => String(n).padStart(2, "0");

  return (
    <div className="card flex h-full flex-col">
      <div className="mb-3 flex items-center justify-between gap-2 flex-wrap">
        <h3 className="section-title">대기업 방종 빈집 타임</h3>
        {best && (
          <span className="rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
                style={{ color: GREEN, background: "rgba(0,255,163,0.10)" }}>
            빈집 타임: {p2(best.hour)}:00 ~ {p2((best.hour + 1) % 24)}:00
          </span>
        )}
      </div>

      {!hasData ? (
        <p className="py-8 text-center text-sm text-muted">
          빈집 타임 분석용 데이터가 아직 부족합니다. 최근 7일치가 쌓이면 표시됩니다.
        </p>
      ) : (
        <>
          {best && (
            <p className="text-sm text-muted">
              최근 {best.window_days}일 기준,{" "}
              <b className="rounded-md px-1.5 py-0.5 font-extrabold tabular-nums"
                 style={{ color: GREEN, background: "rgba(0,255,163,0.12)" }}>
                {p2(best.hour)}:00 ~ {p2((best.hour + 1) % 24)}:00
              </b>{" "}
              시간대는 대형 채널이 평균 <b className="text-fg">{best.big_lives}개</b>만 켜져 있는데
              소형 채널 방송당 평균 시청자는 <b className="text-fg">{nf(best.small_avg_viewers)}명</b>
              {best.uplift_pct > 0 && <> (평소 대비 <b style={{ color: GREEN }}>+{best.uplift_pct}%</b>)</>}
              으로 가장 높았습니다.
            </p>
          )}

          <div className="mt-4 grid grid-cols-12 gap-1 md:grid-cols-[repeat(24,minmax(0,1fr))]">
            {hourly.map((h) => (
              <div key={h.hour} className="group relative flex flex-col justify-end">
                {/* 위: 대형 채널 동시 라이브 수(회색) / 아래: 소형 평균 시청자(네온) */}
                <div className="flex h-10 items-end justify-center">
                  <div className="w-full rounded-sm"
                       style={{ height: `${(h.big_lives / maxBig) * 100}%`,
                                background: "rgba(148,163,184,0.35)" }} />
                </div>
                <div className="mt-0.5 flex h-10 items-start justify-center">
                  <div className="w-full rounded-sm"
                       style={{ height: `${(h.small_avg_viewers / maxSmall) * 100}%`,
                                background: h.hour === best?.hour ? GREEN : "rgba(0,255,163,0.45)" }} />
                </div>
                <span className="mt-0.5 block text-center text-[9px] tabular-nums text-muted/70">
                  {h.hour % 3 === 0 ? h.hour : ""}
                </span>
                <div className="pointer-events-none absolute bottom-[5.5rem] left-1/2 z-20 hidden -translate-x-1/2
                                whitespace-nowrap rounded-lg border border-border bg-bg-card px-2.5 py-1.5
                                text-[10px] text-fg shadow-2xl group-hover:block">
                  {p2(h.hour)}시: 대형 <b>{h.big_lives}개</b> · 소형 평균 <b>{nf(h.small_avg_viewers)}명</b>
                </div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex items-center gap-4 text-[11px] text-muted">
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-3 rounded-sm" style={{ background: "rgba(148,163,184,0.35)" }} />
              대형 채널 동시 라이브 수 (위)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-3 rounded-sm" style={{ background: GREEN }} />
              소형 채널 방송당 평균 시청자 (아래)
            </span>
          </div>
          <Sub>
            * 대형은 시간당 평균 시청자 {nf(best?.big_threshold ?? 1000)}명 이상인 방송입니다.
            위 막대가 짧고 아래 막대가 긴 시간대일수록 대형 방송이 적어 시청자가 흩어지는 구간입니다.
            상관 지표일 뿐 인과는 아닙니다.
          </Sub>
        </>
      )}
    </div>
  );
}

// ── 제목 키워드 효율 ────────────────────────────────────────────────────────
export function TitleKeywordCard({ tk, label = "신입" }:
  { tk: NonNullable<NewcomerInsights["title_keyword"]>; label?: string }) {
  const up = (tk.lift_pct ?? 0) >= 0;
  return (
    <div className="card flex h-full flex-col">
      <h3 className="section-title">방송 제목 키워드 유입 효율</h3>
      <p className="mt-1 text-sm text-muted">
        방송 제목에 유입 키워드({tk.keywords.join(", ")})가 있는 {label}과 없는 {label}의 평균 시청자 비교입니다.
      </p>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-xl border p-4" style={{ borderColor: "rgba(0,255,163,0.35)", background: "rgba(0,255,163,0.05)" }}>
          <p className="text-xs text-muted">키워드 포함</p>
          <p className="mt-1 text-xl font-extrabold tabular-nums" style={{ color: GREEN }}>
            {nf(tk.with_avg)}<span className="ml-1 text-xs font-normal text-muted">명</span>
          </p>
          <p className="mt-0.5 text-[11px] text-muted">방송 {nf(tk.with_count)}개</p>
        </div>
        <div className="rounded-xl border border-border p-4">
          <p className="text-xs text-muted">미포함</p>
          <p className="mt-1 text-xl font-extrabold tabular-nums text-fg">
            {nf(tk.without_avg)}<span className="ml-1 text-xs font-normal text-muted">명</span>
          </p>
          <p className="mt-0.5 text-[11px] text-muted">방송 {nf(tk.without_count)}개</p>
        </div>
        <div className="rounded-xl border border-border p-4">
          <p className="text-xs text-muted">차이</p>
          <p className="mt-1 text-xl font-extrabold tabular-nums"
             style={{ color: tk.lift_pct == null ? undefined : up ? GREEN : "#EF4444" }}>
            {tk.lift_pct == null ? "-" : `${up ? "+" : ""}${tk.lift_pct}%`}
          </p>
          <p className="mt-0.5 text-[11px] text-muted">미포함 그룹 대비</p>
        </div>
      </div>

      <div className="mt-auto">
        <Sub>
          * 키워드를 넣으면 시청자가 는다는 인과가 아니라, 그런 제목을 쓰는 방송이 평균적으로
          어떤 성과를 냈는지를 보여 주는 상관 지표입니다. 양쪽 그룹이 각각 5개 이상일 때만 계산합니다.
        </Sub>
      </div>
    </div>
  );
}

// ── ② 블루오션 카테고리 TOP 5 ───────────────────────────────────────────────
// dense: 2열 레이아웃의 좁은 칼럼에 들어갈 때 — 5칸 가로 배치 대신 2칸으로 접는다.
export function BlueOceanCards({ cats, summary, dense = false, label = "신입" }:
  { cats: NewcomerCategory[]; summary?: NewcomerSummary; dense?: boolean; label?: string }) {
  // 블루오션 지수 = 카테고리 채널당 평균 시청자 ÷ 신입 전체 평균.
  // 표본이 1~2개면 우연히 높게 나오므로 방송 3개 이상만 후보로 둔다(인사이트 카드와 같은 기준).
  const base = Math.max(1, summary?.avg_viewers ?? 1);
  const top = useMemo(() =>
    cats.filter((c) => c.lives >= 3)
        .map((c) => ({ ...c, index: c.avg_viewers / base }))
        .sort((a, b) => b.index - a.index)
        .slice(0, 5),
    [cats, base]);

  if (top.length === 0) {
    return (
      <div className="card h-full">
        <h3 className="section-title">블루오션 카테고리 TOP 5</h3>
        <p className="py-8 text-center text-sm text-muted">
          {label} 방송이 3개 이상인 카테고리가 아직 없습니다.
        </p>
      </div>
    );
  }

  return (
    <div className="card flex h-full flex-col">
      <h3 className="section-title">블루오션 카테고리 TOP 5</h3>
      <p className="mt-0.5 text-[11px] text-muted">
        {label} 방송 수 대비 시청자가 많은 카테고리 — 경쟁이 적고 노출 효율이 좋은 구간입니다.
      </p>

      {/* dense(2열 레이아웃의 좁은 칼럼)에서는 카드가 세로로 5칸 쌓이면서 우측 패널보다
          훨씬 길어져 아래에 큰 빈 공간을 만들었다 → 3열 2행으로 접고 패딩/폰트를 줄인다. */}
      <div className={`mt-4 grid gap-2 ${dense ? "grid-cols-2 sm:grid-cols-3" : "grid-cols-2 md:grid-cols-5"}`}>
        {top.map((c, i) => (
          <div key={c.category}
               className={`rounded-xl border ${dense ? "p-2.5" : "p-3"}`}
               style={{ background: "rgba(0,255,163,0.04)",
                        borderColor: i === 0 ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))" }}>
            <p className={`truncate font-bold text-fg ${dense ? "text-[11px]" : "text-xs"}`}
               title={c.category}>{c.category}</p>
            <p className="mt-1 tracking-tight">
              <span className={`font-extrabold tabular-nums ${dense ? "text-base" : "text-lg"}`}
                    style={{ color: GREEN }}>
                {c.index.toFixed(1)}x
              </span>
            </p>
            <p className={`mt-0.5 text-muted ${dense ? "text-[10px]" : "text-[11px]"}`}>
              채널당 {nf(c.avg_viewers)}명
            </p>
            <p className={`text-muted/70 ${dense ? "text-[10px]" : "text-[11px]"}`}>
              방송 {nf(c.lives)}개
            </p>
          </div>
        ))}
      </div>
      {/* mt-auto: 카드가 h-full로 늘어날 때 각주가 하단에 붙어 여백이 위로 흡수된다 */}
      <div className="mt-auto">
        <Sub>
          * 블루오션 지수 = 카테고리 채널당 평균 시청자 ÷ {label} 전체 평균({nf(base)}명).
          2.0x면 같은 방송을 켜도 평균보다 2배 많은 시청자가 들어온다는 뜻입니다.
          표본 왜곡을 막기 위해 {label} 방송 3개 이상인 카테고리만 집계합니다.
        </Sub>
      </div>
    </div>
  );
}

// ── ③ 신입 체급 구간별 분포 ─────────────────────────────────────────────────
const TIER_COLORS = ["rgba(107,114,128,0.65)", "rgba(0,194,255,0.65)", "rgba(0,255,163,0.55)", GREEN];

export function TierDistribution({ tiers, label = "신입" }:
  { tiers: NonNullable<NewcomerInsights["tiers"]>; label?: string }) {
  const total = tiers.reduce((s, t) => s + t.count, 0);
  if (total === 0) return null;

  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="text-xs font-semibold text-fg">{label} 체급 구간별 분포</p>

      {/* 수평 스택 바 */}
      <div className="mt-2 flex h-3 w-full overflow-hidden rounded-full bg-bg-hover">
        {tiers.map((t, i) => (
          <div key={t.label} className="group relative h-full transition-all"
               style={{ width: `${t.share}%`, background: TIER_COLORS[i] }}>
            <div className="pointer-events-none absolute bottom-5 left-1/2 z-20 hidden -translate-x-1/2
                            whitespace-nowrap rounded-lg border border-border bg-bg-card px-2.5 py-1.5
                            text-[10px] text-fg shadow-2xl group-hover:block">
              {t.label} ({t.desc}) · {nf(t.count)}명 · {t.share}%
            </div>
          </div>
        ))}
      </div>

      {/* 범례 */}
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 sm:grid-cols-4">
        {tiers.map((t, i) => (
          <div key={t.label} className="flex items-center gap-1.5 min-w-0">
            <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: TIER_COLORS[i] }} />
            <span className="truncate text-[11px] text-muted">
              <b className="text-fg">{t.label}</b> {t.share}%
            </span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-muted/70">
        * 현재 라이브 중인 {label}이 어느 구간에 몰려 있는지 보여 줍니다. 오른쪽 구간으로 갈수록
        {label} 상위권입니다.
      </p>
    </div>
  );
}
