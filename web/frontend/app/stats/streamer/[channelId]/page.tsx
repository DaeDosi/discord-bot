"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import SiteHeader from "@/components/SiteHeader";
import { useParams } from "next/navigation";
import {
  Bot, BarChart3, ArrowLeft, ExternalLink, Loader2, Radio, Heart, Clock, Users, TrendingUp,
} from "lucide-react";
import { api } from "@/lib/api";
import { StreamerTagList } from "@/components/StreamerTag";
import type {
  StreamerDashboard, StreamerDetail, StreamerSessionSeries, StreamerDaily, StreamerHourly,
} from "@/lib/types";
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

// 첫 방송일 — 치지직이 직접 주는 값(channelHistory)은 정확하므로 그대로,
// 다시보기(VOD) 최고령 영상으로 역산한 폴백일 때만 '(추정)'을 붙인다.
// 정확한 값은 "YYYY-MM-DD HH:mm:ss"(KST)라 Date로 파싱하면 브라우저 로컬 타임존으로
// 해석돼 날짜가 하루 밀릴 수 있다 — 앞 10자를 그대로 쓴다.
function FirstBroadcast({ data }: { data: StreamerDashboard }) {
  const raw = data.first_broadcast;
  if (!raw) return <>첫 방송 정보 없음</>;

  const exact = data.first_broadcast_source === "CHZZK_CHANNEL_HISTORY";
  const [y, m, d] = raw.slice(0, 10).split("-");
  const label = y && m && d
    ? `${y}. ${Number(m)}. ${Number(d)}.`
    : new Date(raw).toLocaleDateString("ko-KR");

  return (
    <>
      <span title={exact ? `${raw} (KST) · 치지직 채널 정보 기준` : "다시보기 최고령 영상 기준 추정"}>
        첫 방송{exact ? "" : "(추정)"} {label}
      </span>
      {exact && typeof data.total_live_hours === "number" && (
        <> · 누적 방송 {nf(data.total_live_hours)}시간</>
      )}
    </>
  );
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

// 차트 위 고정 판독 줄 — 어느 막대를 가리켜도 같은 자리에서 값을 읽는다.
// (막대에 붙여 띄우는 툴팁은 막대가 얇을수록 조준이 어렵고 위치도 계속 어긋난다.)
function Readout({ title, badge, items, hint }: {
  title: string; badge?: string;
  items: { label: string; value: string; color?: string }[];
  hint?: string;
}) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 rounded-xl border border-border px-3.5 py-2.5">
      <span className="text-base font-extrabold tabular-nums text-fg">{title}</span>
      {badge && (
        <span className="rounded-full px-2 py-0.5 text-[11px] font-bold"
              style={{ color: GREEN, background: "rgba(0,255,163,0.12)" }}>{badge}</span>
      )}
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5 text-sm text-muted">
          {it.color && <span className="h-2.5 w-4 rounded-sm" style={{ background: it.color }} />}
          {it.label} <b className="tabular-nums text-fg">{it.value}</b>
        </span>
      ))}
      {hint && <span className="ml-auto text-[11px] text-muted/70">{hint}</span>}
    </div>
  );
}

// 일별 최고/평균 시청자 — 수직 2색 막대.
// 막대 자체가 아니라 '칼럼 전체'가 호버 영역이라 얇은 막대를 정확히 조준할 필요가 없다.
function DailyChart({ rows }: { rows: StreamerDaily[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...rows.map((r) => r.peak));
  // 기본 표시는 최고 동시시청자를 찍은 날 — 마우스를 떼도 판독 줄이 비지 않는다
  const bestIdx = rows.reduce((b, r, i) => (r.peak > rows[b].peak ? i : b), 0);
  const idx = hover ?? bestIdx;
  const cur = rows[idx];
  // 날짜 라벨이 겹치지 않을 만큼만 남긴다(30일이면 3일 간격)
  const step = Math.max(1, Math.ceil(rows.length / 12));

  return (
    <>
      {cur && (
        <Readout
          title={cur.date}
          badge={idx === bestIdx ? "최고 기록일" : undefined}
          items={[
            { label: "최고", value: `${nf(cur.peak)}명`, color: PURPLE },
            { label: "평균", value: `${nf(cur.avg_viewers)}명`, color: GREEN },
            { label: "방송", value: `${(cur.minutes / 60).toFixed(1)}시간` },
            { label: "뷰어쉽", value: `${nf(cur.viewership)}명·시간` },
          ]}
          hint={hover === null ? "막대에 마우스를 올리면 그날 값이 표시됩니다" : undefined}
        />
      )}
      <div className="mt-3 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <div className="flex items-end gap-1.5" style={{ minWidth: rows.length * 34 }}
             onMouseLeave={() => setHover(null)}>
          {rows.map((r, i) => {
            const on = idx === i;
            // outline-none을 걸지 않는다. 이 막대는 tabIndex={0}이라 키보드로 들어오는데,
            // onFocus가 켜는 배경(bg-hover)은 마우스 hover와 **똑같은 표시**라 키보드
            // 사용자는 자기가 어디 있는지 구분할 수 없었다. 전역 focus-visible 링이
            // hover와 구별되는 유일한 신호다.
            return (
              <div key={r.date} tabIndex={0}
                   onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)}
                   aria-label={`${r.date} 최고 ${r.peak}명 평균 ${r.avg_viewers}명`}
                   className="flex w-7 shrink-0 cursor-pointer flex-col items-center justify-end gap-1
                              rounded-md transition-colors"
                   style={{ height: 240, background: on ? "rgb(var(--color-bg-hover-rgb))" : undefined }}>
                <div className="flex h-full w-full items-end justify-center gap-[3px] px-1"
                     style={{ opacity: hover !== null && !on ? 0.4 : 1 }}>
                  <div className="w-[9px] rounded-t-[3px]"
                       style={{ height: `${(r.peak / max) * 100}%`, background: PURPLE, opacity: 0.8 }} />
                  <div className="w-[9px] rounded-t-[3px]"
                       style={{ height: `${(r.avg_viewers / max) * 100}%`, background: GRAD }} />
                </div>
                <span className="h-4 whitespace-nowrap text-[11px] tabular-nums"
                      style={{ color: on ? "rgb(var(--color-fg-rgb))" : "rgb(var(--color-muted-rgb))" }}>
                  {on || i % step === 0 ? r.date.slice(5) : ""}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}

// 시간대별 유입 — 0~23시 고정 축의 수직 막대.
// 가로 막대 목록은 '몇 시가 좋은가'를 시간 순서로 비교하기 어려웠다. 시간축을 가로로
// 두면 하루의 흐름이 그대로 보인다. 꺾은선은 방송하지 않은 시간대를 이어 버려
// 없는 데이터를 있는 것처럼 만들기 때문에 쓰지 않는다(빈 시간은 빈칸으로 남긴다).
function HourlyChart({ rows }: { rows: StreamerHourly[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const byHour = useMemo(() => {
    const m = new Map<number, StreamerHourly>();
    rows.forEach((r) => m.set(r.hour, r));
    return m;
  }, [rows]);
  const max = Math.max(1, ...rows.map((r) => r.avg_viewers));
  const bestHour = rows.reduce((b, r) => (r.avg_viewers > b.avg_viewers ? r : b), rows[0])?.hour;
  const shownHour = hover ?? bestHour ?? null;
  const cur = shownHour === null ? undefined : byHour.get(shownHour);
  const p2 = (n: number) => String(n).padStart(2, "0");

  return (
    <>
      {cur ? (
        <Readout
          title={`${p2(cur.hour)}:00 ~ ${p2((cur.hour + 1) % 24)}:00`}
          badge={cur.hour === bestHour ? "가장 잘 나온 시간대" : undefined}
          items={[
            { label: "평균", value: `${nf(cur.avg_viewers)}명`, color: GREEN },
            { label: "최고", value: `${nf(cur.peak_viewers)}명`, color: PURPLE },
            { label: "방송", value: `${cur.hours}시간` },
          ]}
          hint={hover === null ? "막대에 마우스를 올리면 그 시간대 값이 표시됩니다" : undefined}
        />
      ) : (
        <Readout title="—" items={[{ label: "방송 이력", value: "없음" }]} />
      )}
      <div className="mt-3 grid grid-cols-12 gap-1 md:grid-cols-[repeat(24,minmax(0,1fr))]"
           onMouseLeave={() => setHover(null)}>
        {Array.from({ length: 24 }, (_, h) => {
          const r = byHour.get(h);
          const on = shownHour === h;
          // 위 일별 막대와 같은 이유로 outline-none을 걸지 않는다(hover와 구분 불가).
          return (
            <div key={h} tabIndex={0}
                 onMouseEnter={() => setHover(r ? h : null)} onFocus={() => setHover(r ? h : null)}
                 aria-label={r ? `${p2(h)}시 평균 ${r.avg_viewers}명` : `${p2(h)}시 방송 없음`}
                 className="flex flex-col justify-end rounded-md transition-colors"
                 style={{ cursor: r ? "pointer" : "default",
                          background: on ? "rgb(var(--color-bg-hover-rgb))" : undefined }}>
              <div className="flex h-40 items-end justify-center px-0.5">
                <div className="w-full rounded-t-[3px] transition-opacity"
                     style={{ height: r ? `${Math.max(3, (r.avg_viewers / max) * 92)}%` : "2px",
                              background: r ? GRAD : "rgb(var(--color-bg-hover-rgb))",
                              opacity: hover !== null && !on ? 0.4 : 1 }} />
              </div>
              <span className="mt-1 block h-4 whitespace-nowrap text-center text-[11px] tabular-nums"
                    style={{ color: on ? "rgb(var(--color-fg-rgb))" : "rgb(var(--color-muted-rgb))" }}>
                {on || h % 3 === 0 ? `${h}시` : ""}
              </span>
            </div>
          );
        })}
      </div>
      <Sub>* 회색 빈칸은 그 시간대에 방송한 이력이 없다는 뜻입니다.</Sub>
    </>
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
  const sessions = detail?.sessions ?? [];
  const rankDaily = detail?.rank_daily ?? [];

  return (
    <div className="flex-1 flex flex-col">
      {/* 헤더는 `components/SiteHeader` 하나뿐이다. 이 페이지는 자체 헤더 막대를
          갖고 있었는데(높이 60px 고정 + 자체 브랜드 마크업), 공통 헤더가 바뀔 때마다
          여기만 남는 문제가 있었다. 돌아가기 링크는 본문 상단으로 옮겼다. */}
      <SiteHeader maxWidth="full" />

      <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 md:px-6 py-6 space-y-5">
        <Link href="/stats"
              className="nb-tap inline-flex items-center gap-1.5 text-sm text-muted
                         transition-colors hover:text-fg">
          <ArrowLeft size={16} aria-hidden="true" /> 통계로 돌아가기
        </Link>
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
                    {/* 팀/소속 태그 — 상세 페이지에서는 접지 않고 전부 보여 준다.
                        부모가 flex-wrap이라 좁은 화면에서는 아래 줄로 넘어간다. */}
                    <StreamerTagList tags={data.team_tags} max={99} />
                  </div>
                  {data.live_title && <p className="mt-1 truncate text-sm text-muted">{data.live_title}</p>}
                  <Sub>
                    <FirstBroadcast data={data} />
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
              {/* 예전에는 서브 탭과 기간 토글이 **한 줄에 강제**로 있었다
                  (`ml-auto` + `shrink-0`). 320px과 확대 화면에서 둘의 최소 폭 합이
                  뷰포트를 넘어 문서가 가로로 밀렸다(실측 8~68px).
                  이제 좁은 화면에서는 두 줄로 내려가고, 넓어지면 한 줄로 돌아온다. */}
              <div className="mt-4 flex flex-wrap items-center gap-y-2 border-b
                              border-border pb-3">
              <div className="nb-hscroll nb-tap-gap flex min-w-0 flex-1 gap-2
                              overflow-x-auto">
                {SUB_TABS.map((t) => {
                  const active = tab === t;
                  return (
                    <button key={t} onClick={() => setTab(t)}
                      className="nb-tap whitespace-nowrap rounded-full px-4 py-1.5
                                 text-sm transition-colors"
                      style={active
                        ? { background: "rgba(0,255,163,0.12)", color: GREEN, fontWeight: 700 }
                        : { color: "rgb(var(--color-muted-rgb))" }}>
                      {t}
                    </button>
                  );
                })}
                </div>
                {/* `ml-auto`를 뺐다 — 좁을 때 줄바꿈으로 내려오고 넓을 때만
                    오른쪽 끝으로 간다(`sm:ml-auto`). */}
                <div className="nb-tap-gap flex shrink-0 items-center gap-1 sm:ml-auto">
                  {[7, 30, 90].map((d) => (
                    <button key={d} onClick={() => setDays(d)}
                      className="nb-tap nb-tap-wide inline-flex items-center justify-center
                                 rounded-md border px-2 py-1 text-[11px] transition-colors"
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
                  <p className="mt-1 text-sm leading-relaxed text-muted">
                    <span className="inline-flex items-center gap-1.5 align-middle">
                      <span className="h-2.5 w-4 rounded-sm" style={{ background: PURPLE, opacity: 0.8 }} />
                      최고 시청자
                    </span>
                    <span className="mx-2 text-border">·</span>
                    <span className="inline-flex items-center gap-1.5 align-middle">
                      <span className="h-2.5 w-4 rounded-sm" style={{ background: GRAD }} />
                      평균 시청자
                    </span>
                    <span className="ml-2">— 방송이 있던 날만 표시됩니다.</span>
                  </p>
                  {daily.length === 0
                    ? <p className="py-10 text-center text-sm text-muted">데이터가 아직 없습니다.</p>
                    : <DailyChart rows={daily} />}
                </div>

                <div className="card">
                  <h3 className="section-title">시간대별 유입 분석</h3>
                  <p className="mt-1 text-sm leading-relaxed text-muted">
                    그 시간대에 방송을 켰을 때의 평균 시청자입니다. 하루 24시간을 가로축에 두어
                    어느 시간대가 잘 나오는지 한눈에 비교할 수 있습니다.
                  </p>
                  {hourly.length === 0
                    ? <p className="py-10 text-center text-sm text-muted">시간대 데이터가 아직 없습니다.</p>
                    : <HourlyChart rows={hourly} />}
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
