"use client";
import { useMemo, useState } from "react";
import type { NewcomerInsights, NewcomerCategory, NewcomerSummary } from "@/lib/types";
import { CARD_BORDER, CARD_DARK, CARD_SUB_TEXT } from "./cardStyle";

// 신규 스트리머 분석 탭의 인사이트 시각화 3종.
// 데이터는 모두 newcomers 응답에 함께 실려 오므로 별도 요청이 없다.

const GREEN = "#00FFA3";
const nf = (n: number) => n.toLocaleString("ko-KR");
const Sub = ({ children }: { children: React.ReactNode }) =>
  <p className="mt-2 text-[11px] leading-relaxed text-muted">{children}</p>;

// ── ① 24시간 골든타임 히트맵 ────────────────────────────────────────────────
export function GoldenHourHeatmap({ hourly }: { hourly: NonNullable<NewcomerInsights["hourly"]> }) {
  // 색은 '0~최고' 비율이 아니라 '24시간 중 최저~최고 사이의 상대 위치'로 정한다.
  //
  // 예전에는 avg/max 를 썼다. 이 지표는 수천 채널을 시간별로 평균낸 값이라 시간대 차이가
  // 7.1 대 7.6 처럼 작게 나오고, 그러면 모든 칸의 비율이 0.9~1.0에 몰려 전 구간이
  // 최고 색(진한 초록) 하나로 칠해졌다(= 정보량 0). 최저~최고로 정규화하면 같은 데이터에서
  // 시간대별 강약이 드러난다. 값 자체는 툴팁과 평균 대비 편차로 보여 준다.
  const { min, span, avg, peakLabel } = useMemo(() => {
    const vals = hourly.filter((h) => h.snaps > 0).map((h) => h.avg_viewers);
    const mn = vals.length ? Math.min(...vals) : 0;
    const mx = vals.length ? Math.max(...vals) : 0;
    const mean = vals.length ? vals.reduce((s, v) => s + v, 0) / vals.length : 0;
    const sp = mx - mn;
    // 피크타임 라벨: 상대 위치 0.75 이상인 시간을 연속 구간으로 묶는다.
    // (예전엔 절대 비율 0.75라 값이 평평하면 24시간 전체가 '피크타임'으로 잡혔다.)
    const rel = (v: number) => (sp > 0 ? (v - mn) / sp : 0);
    const hot = hourly.filter((h) => h.snaps > 0 && rel(h.avg_viewers) >= 0.75)
                      .map((h) => h.hour).sort((a, b) => a - b);
    if (hot.length === 0) return { min: mn, span: sp, avg: mean, peakLabel: null };
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
    return { min: mn, span: sp, avg: mean,
             peakLabel: `${p2(s)}:00 ~ ${p2((e + 1) % 24)}:00` };
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
              // span=0(24시간 값이 전부 동일)일 때는 강약이 없으므로 중간 톤 하나로 칠한다.
              const r = span > 0 ? (h.avg_viewers - min) / span : 0.5;
              const bg = h.snaps === 0 ? "rgb(var(--color-bg-hover-rgb))"
                : r >= 0.8 ? GREEN
                : r >= 0.5 ? "rgba(0,255,163,0.55)"
                : r >= 0.25 ? "rgba(0,255,163,0.28)"
                : "rgba(0,255,163,0.12)";
              const dev = avg > 0 ? Math.round((h.avg_viewers / avg - 1) * 100) : 0;
              return (
                <div key={h.hour} className="group relative">
                  <div className="h-9 cursor-pointer rounded-sm transition-transform hover:scale-110"
                       style={{ background: bg }} />
                  <span className="mt-0.5 block whitespace-nowrap text-center text-[9px] tabular-nums text-muted/70">
                    {h.hour % 3 === 0 ? `${h.hour}시` : ""}
                  </span>
                  {/* 호버 툴팁 */}
                  <div className="pointer-events-none absolute bottom-11 left-1/2 z-20 hidden -translate-x-1/2
                                  whitespace-nowrap rounded-lg border border-border bg-bg-card px-2.5 py-1.5
                                  text-[10px] text-fg shadow-2xl group-hover:block">
                    {String(h.hour).padStart(2, "0")}시: 신입 평균 <b>{nf(h.avg_viewers)}명</b>
                    {h.snaps > 0 && avg > 0 && (
                      <> · 평균 대비{" "}
                        <b style={{ color: dev >= 0 ? GREEN : "#EF4444" }}>
                          {dev >= 0 ? "+" : ""}{dev}%
                        </b>
                      </>
                    )}
                    {" "}(경쟁 채널 {nf(h.channels)}개)
                  </div>
                </div>
              );
            })}
          </div>

          {/* 색 범례 — 절대 수치가 아니라 24시간 안에서의 상대 위치라는 걸 명시한다 */}
          <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted">
            <span className="flex items-center gap-2">
              낮음 {span > 0 && <b className="tabular-nums text-fg">{nf(min)}명</b>}
              {["rgba(0,255,163,0.12)", "rgba(0,255,163,0.28)", "rgba(0,255,163,0.55)", GREEN].map((c) => (
                <span key={c} className="h-3 w-5 rounded-sm" style={{ background: c }} />
              ))}
              {span > 0 && <b className="tabular-nums text-fg">{nf(min + span)}명</b>} 높음
            </span>
            <span className="flex items-center gap-2">
              <span className="h-3 w-5 rounded-sm" style={{ background: "rgb(var(--color-bg-hover-rgb))" }} />
              수집된 신입 방송 없음
            </span>
          </div>

          <Sub>
            * 색은 <b className="text-fg">24시간 중 상대적인 강약</b>입니다(그 시간대 값이
            최저면 가장 옅고 최고면 가장 짙음). 수천 개 방송을 시간별로 평균낸 값이라
            시간대 차이 자체는 작게 나오므로, 절대 수치보다 어느 시간이 상대적으로 유리한지를
            보는 데 쓰세요. 실제 수치와 평균 대비 편차는 칸에 마우스를 올리면 나옵니다.
            최근 24시간 집계 기준입니다.
          </Sub>
        </>
      )}
    </div>
  );
}

// ── 대기업 방종 '빈집 타임' (소형 탭) ───────────────────────────────────────
// 시간대별로 대형 채널 동시 라이브 수(막대, 회색)와 소형 채널 방송당 평균 시청자(막대, 네온)를
// 겹쳐 보여 준다. 대형이 적은데 소형 평균이 높은 시간이 노려볼 만한 '빈집'이다.
const BAR_MAX_PCT = 88;  // 최고값 막대가 트랙 경계에 닿아 잘려 보이지 않도록 남기는 여유
const BAR_MIN_PCT = 3;   // 0은 아니지만 아주 작은 값이 완전히 사라지지 않게 하는 바닥
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

  // 값은 떠다니는 툴팁 대신 차트 위 '고정 판독 줄'에 쓴다.
  // 막대가 커지면서 툴팁을 절대 위치로 띄우는 방식은 위치가 계속 어긋나고, 24칸 중
  // 가장자리 칸에서는 카드 밖으로 넘쳤다. 자리를 고정해 두면 어느 칸을 가리켜도
  // 같은 곳에서 읽을 수 있고, 마우스를 떼면 '빈집 타임'이 기본으로 표시된다.
  const [hoverHour, setHoverHour] = useState<number | null>(null);
  const shownHour = hoverHour ?? best?.hour ?? null;
  const shown = shownHour === null ? null : hourly.find((h) => h.hour === shownHour) ?? null;

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

          {/* 고정 판독 줄 — 가리킨 시간대(없으면 빈집 타임)의 값을 항상 같은 자리에 쓴다 */}
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1.5 rounded-xl border border-border px-3.5 py-2.5">
            {shown ? (
              <>
                <span className="text-base font-extrabold tabular-nums text-fg">
                  {p2(shown.hour)}:00 ~ {p2((shown.hour + 1) % 24)}:00
                </span>
                {shown.hour === best?.hour && (
                  <span className="rounded-full px-2 py-0.5 text-[11px] font-bold"
                        style={{ color: GREEN, background: "rgba(0,255,163,0.12)" }}>빈집 타임</span>
                )}
                <span className="flex items-center gap-1.5 text-sm text-muted">
                  <span className="h-2.5 w-4 rounded-sm" style={{ background: "rgba(148,163,184,0.35)" }} />
                  대형 <b className="tabular-nums text-fg">{shown.big_lives}개</b>
                </span>
                <span className="flex items-center gap-1.5 text-sm text-muted">
                  <span className="h-2.5 w-4 rounded-sm" style={{ background: GREEN }} />
                  소형 평균 <b className="tabular-nums text-fg">{nf(shown.small_avg_viewers)}명</b>
                </span>
                <span className="ml-auto text-[11px] text-muted/70">
                  {hoverHour === null ? "그래프에 마우스를 올리면 그 시간대 값이 표시됩니다" : ""}
                </span>
              </>
            ) : (
              <span className="text-sm text-muted">그래프에 마우스를 올리면 시간대별 값이 표시됩니다.</span>
            )}
          </div>

          {/* 막대 높이는 트랙의 최대 BAR_MAX_PCT까지만 쓴다. 100%를 쓰면 최고값 막대가
              트랙 위/아래 경계에 정확히 닿아 잘려 보였다. 0이 아닌 아주 작은 값은
              BAR_MIN_PCT 바닥을 줘서 아예 사라지지 않게 한다. */}
          <div className="mt-3 grid grid-cols-12 gap-1 md:grid-cols-[repeat(24,minmax(0,1fr))]"
               onMouseLeave={() => setHoverHour(null)}>
            {hourly.map((h) => {
              const bigPct = h.big_lives > 0
                ? Math.max(BAR_MIN_PCT, (h.big_lives / maxBig) * BAR_MAX_PCT) : 0;
              const smallPct = h.small_avg_viewers > 0
                ? Math.max(BAR_MIN_PCT, (h.small_avg_viewers / maxSmall) * BAR_MAX_PCT) : 0;
              const on = shownHour === h.hour;
              // `outline-none + focus-visible:ring-1`을 걷어낸다. 이 저장소의
              // tailwind.config에는 ringColor가 없어 Tailwind 기본 파랑(#3b82f6)이
              // 나왔는데, 사이트 accent(#5865F2)도 아니고 다른 어떤 포커스 표시와도
              // 달랐다. 전역 focus-visible 계약 하나로 통일한다.
              return (
                <div key={h.hour} tabIndex={0}
                     onMouseEnter={() => setHoverHour(h.hour)}
                     onFocus={() => setHoverHour(h.hour)}
                     aria-label={`${p2(h.hour)}시 대형 ${h.big_lives}개 소형 평균 ${h.small_avg_viewers}명`}
                     className="flex cursor-pointer flex-col justify-end rounded-sm transition-colors"
                     style={{ background: on ? "rgba(255,255,255,0.05)" : undefined }}>
                  {/* 위: 대형 채널 동시 라이브 수(회색) / 아래: 소형 평균 시청자(네온) */}
                  <div className="flex h-28 items-end justify-center">
                    <div className="w-full rounded-sm transition-opacity"
                         style={{ height: `${bigPct}%`, background: "rgba(148,163,184,0.35)",
                                  opacity: hoverHour !== null && !on ? 0.45 : 1 }} />
                  </div>
                  {/* 두 트랙 사이 기준선 — 위/아래 막대가 어디서 갈라지는지 보이게 */}
                  <div className="my-1 h-px w-full" style={{ background: "rgb(var(--color-border-rgb))" }} />
                  <div className="flex h-28 items-start justify-center">
                    <div className="w-full rounded-sm transition-opacity"
                         style={{ height: `${smallPct}%`,
                                  background: h.hour === best?.hour ? GREEN : "rgba(0,255,163,0.45)",
                                  opacity: hoverHour !== null && !on ? 0.45 : 1 }} />
                  </div>
                  <span className="mt-1 block whitespace-nowrap text-center text-[9px] tabular-nums"
                        style={{ color: on ? "rgb(var(--color-fg-rgb))" : undefined }}>
                    <span className={on ? "" : "text-muted/70"}>
                      {on ? `${h.hour}시` : h.hour % 3 === 0 ? `${h.hour}시` : ""}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm text-muted">
            <span className="flex items-center gap-2">
              <span className="h-3 w-5 rounded-sm" style={{ background: "rgba(148,163,184,0.35)" }} />
              대형 채널 동시 라이브 수 (위)
            </span>
            <span className="flex items-center gap-2">
              <span className="h-3 w-5 rounded-sm" style={{ background: GREEN }} />
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
export function BlueOceanCards({ cats, summary, label = "신입" }:
  { cats: NewcomerCategory[]; summary?: NewcomerSummary; label?: string }) {
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

      {/* 카드 룩은 '카테고리별 스트리머' 탭의 CategoryCard와 동일한 매트 다크 패널.
          다만 이 카드는 클릭 대상이 아니라 호버 유도가 의미 없어서, 테두리 네온을
          nb-neon-glow 로 상시 노출한다(회전이 아니라 밝기만 은은하게 오르내린다). */}
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        {top.map((c) => (
          <div key={c.category}
               className="nb-neon-border nb-neon-glow rounded-xl border p-3.5"
               style={{ background: CARD_DARK, borderColor: CARD_BORDER }}>
            <p className="truncate text-xs font-bold text-white" title={c.category}>{c.category}</p>
            <p className="mt-1.5 tracking-tight">
              <span className="text-xl font-extrabold tabular-nums" style={{ color: GREEN }}>
                x{c.index.toFixed(1)}
              </span>
            </p>
            <p className="mt-1 text-[11px]" style={{ color: CARD_SUB_TEXT }}>
              채널당 {nf(c.avg_viewers)}명
            </p>
            <p className="text-[11px]" style={{ color: CARD_SUB_TEXT }}>방송 {nf(c.lives)}개</p>
          </div>
        ))}
      </div>
      {/* mt-auto: 카드가 h-full로 늘어날 때 각주가 하단에 붙어 여백이 위로 흡수된다 */}
      <div className="mt-auto">
        <Sub>
          * 블루오션 지수 = 카테고리 채널당 평균 시청자 ÷ {label} 전체 평균({nf(base)}명).
          x2.0이면 같은 방송을 켜도 평균보다 2배 많은 시청자가 들어온다는 뜻입니다.
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
