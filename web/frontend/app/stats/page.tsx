"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import {
  Bot, BarChart3, LineChart as LineIcon, ListOrdered, Gamepad2, Radio,
  TrendingUp, Loader2, Search, Circle, Sprout, ChevronDown, X,
  // 소형 스트리머 — 신규(Sprout)와 다른 아이콘. StatsNav와 같은 것을 쓴다.
  Leaf, Sparkles,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  RisingOverview, RisingTimeseries, RisingLiveRanking, RisingCategories, RisingCategory,
  RisingStars, TimeRange, CatRange, RisingSearchResult, RisingNewcomers, RisingNewcomer,
  NewcomerCategory, NewcomerGroup, NewcomerInsights,
  RisingPeriodRanking, PeriodRange, PeriodSort, RisingCategoryStreamers,
  RisingSmallRanking,
} from "@/lib/types";
import Footer from "@/components/Footer";
import CategoryRankCards from "./CategoryRankCards";
import RankingCharts from "./RankingCharts";
import TagSearch from "./TagSearch";
import PeriodAnalysis from "./PeriodAnalysis";
import Singcup from "./Singcup";
import { ViewerDistribution, TrafficHeatmap, TitleKeywordRank } from "./OverviewViz";
import { GoldenHourHeatmap, BlueOceanCards, TierDistribution, TitleKeywordCard, VacancyHours } from "./NewcomerInsightViz";
import StatsNav, { resolveTab, type Tab } from "./StatsNav";
import SiteHeader from "@/components/SiteHeader";
import StreamerAvatar from "./StreamerAvatar";
import { StreamerTagList } from "@/components/StreamerTag";
import { CARD_BORDER, CARD_DARK } from "./cardStyle";

import LineChart, { type LinePoint, type LineSeries } from "./LineChart";

// 2톤 그라데이션(그린 → 시안) — 브랜드 액센트
const GREEN = "#00FFA3";
const CYAN  = "#00C2FF";
const GRAD  = `linear-gradient(135deg, ${GREEN}, ${CYAN})`;
const PEAK_GRAD = "linear-gradient(135deg, #FF4FA3, #A855F7)"; // 피크(핑크→퍼플) 2톤
// 랭킹 테이블 컬럼별 컬러 시스템 — 컬럼끼리 색이 겹치지 않게 고정 배정
const YELLOW_GRAD = "linear-gradient(90deg, #FBBF24, #F59E0B)"; // 시청자: 골드/옐로우 (시선 집중)
const PURPLE_GRAD = "linear-gradient(90deg, #A855F7, #8B5CF6)"; // 방송시간: 소프트 퍼플 (차분)
const CYAN_GRAD   = "linear-gradient(90deg, #06B6D4, #00FFA3)"; // 팔로워: 치지직 네온 시안 → 그린

// 증감 컬러 — 컬럼 바 색(골드/퍼플/시안)과 겹치지 않게 별도 배정
const UP_GREEN = "#10B981"; // 상승
const DOWN_RED = "#EF4444"; // 하락

const DAY_MS = 24 * 3600 * 1000;

// 랭킹 TOP 3 강조 — 이모지 대신 골드/실버/브론즈 색 텍스트 + 프로필 하이라이트 링
const MEDALS = [
  { color: "#FBBF24" }, // 1위 골드
  { color: "#D1D5DB" }, // 2위 실버 (gray-300)
  { color: "#D97706" }, // 3위 브론즈 (amber-600)
] as const;

// 치지직 플랫폼 마크 — public/chzzk.png (1024px 앱 아이콘 타일: 검정 라운드 배경 +
// 네온 그린 Z). 모서리는 원본에서 이미 투명 처리돼 있어 CSS 라운딩을 덧대지 않는다.
// 랭킹 테이블 두 곳(전체/신입)이 공유하므로 로고 교체는 이 컴포넌트만 고치면 된다.
function ChzzkMark() {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src="/chzzk.png" alt="치지직" title="치지직"
         width={16} height={16} loading="lazy"
         className="w-4 h-4 shrink-0" />
  );
}

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

// 수치 셀의 비율 바 — 시청자/방송시간/팔로워가 두께·간격을 공유하도록 한 곳에서 관리.
//
// 예전엔 absolute bottom-0으로 셀 바닥에 붙였는데, td 박스는 행 높이만큼 늘어나므로
// 바가 항상 '행 바닥'에 고정되고 수치 텍스트와의 간격이 14px 이상 벌어졌다. 이제 CellCol로
// 텍스트와 함께 flex-col gap-1.5(6px)로 묶어 수치 바로 아래에 붙인다.
// 폭은 셀 콘텐츠 박스 기준이라 px-6 패딩이 그대로 컬럼 간 여백으로 남는다(바끼리 안 붙음).
function CellBar({ pct, background }: { pct: number; background: string }) {
  return (
    <span className="block h-[3px] rounded-full bg-bg-hover overflow-hidden">
      <span className="block h-full rounded-full"
            style={{ width: `${Math.max(0, Math.min(100, pct))}%`, background }} />
    </span>
  );
}

// 랭킹 표 수치 — text-base가 과해 살짝 줄이고(text-sm) 뒤에 단위를 붙인다.
function StatNum({ value, unit }: { value: number; unit: string }) {
  return (
    <span className="text-sm font-bold tabular-nums text-fg">
      {nf(value)}<span className="ml-0.5 text-[11px] font-normal text-muted">{unit}</span>
    </span>
  );
}

// 수치 + 바를 촘촘한 세로 간격으로 묶는 래퍼
function CellCol({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col gap-1.5">{children}</div>;
}

// 증감 뱃지 (null이면 '-')
function Delta({ pct }: { pct: number | null | undefined }) {
  if (pct === null || pct === undefined || !isFinite(pct)) return <span className="text-muted">–</span>;
  const up = pct >= 0;
  return (
    <span className="font-semibold tabular-nums" style={{ color: up ? UP_GREEN : DOWN_RED }}>
      {up ? "▲" : "▼"} {Math.abs(pct).toFixed(1)}%
    </span>
  );
}

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
    <span className="font-semibold tabular-nums" style={{ color: up ? UP_GREEN : DOWN_RED }}>
      {up ? "▲" : "▼"} {absPart}({up ? "+" : "-"}{Math.abs(pct).toFixed(1)}%)
    </span>
  );
}

// 카드 규격(!p-4 + 수치 타이포)은 신규 스트리머 분석의 NcTile과 동일하게 맞춘다.
// accent=true인 한 장만 그린→시안 2톤 그라데이션, 나머지 수치는 흰색.
function StatTile({ label, value, unit, sub, accent, deltaPrev, rawValue }:
  { label: string; value: string; unit?: string; sub?: string; accent?: boolean;
    deltaPrev?: number | null; rawValue?: number }) {
  return (
    <div className="card !p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1.5 tracking-tight">
        <span className="text-xl md:text-2xl font-extrabold tabular-nums text-white">
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

const METRIC_OPTS: { k: Metric; label: string }[] = [
  { k: "viewers", label: "시청자" },
  { k: "lives",   label: "라이브" },
];

// 창이 넓어질수록 버킷을 키운다 — 72시간을 10분 원본으로 그리면 432포인트라
// 경로가 톱니처럼 뭉개져 추세가 오히려 안 보인다. 값은 백엔드 _TS_RANGES와 짝이다.
const TS_RANGE_OPTS: { k: TimeRange; label: string }[] = [
  { k: "live", label: "24시간" },
  { k: "48h",  label: "2일" },
  { k: "72h",  label: "3일" },
];
const TS_RANGE_DESC: Record<string, string> = {
  live: "최근 24시간 추이 · 10분 간격",
  "48h": "최근 48시간 추이 · 30분 구간 평균",
  "72h": "최근 72시간 추이 · 1시간 구간 평균",
};

// 스트리머 검색 — 랭킹에 없는 스트리머도 검색해 개인 분석으로 이동
function StreamerSearch() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<RisingSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // 드롭다운은 '입력이 있으면' 즉시 열고 로딩 중에도 계속 열어 둔다.
  // 예전엔 fetch가 성공한 뒤에야 setOpen(true)를 해서, 타이핑할 때마다 패널이
  // 닫혔다 열리며 아래로 내려왔다 올라가는 것처럼 보였다.
  // reqId: 늦게 도착한 이전 요청이 최신 결과를 덮어쓰지 않게 하는 가드.
  const reqId = useRef(0);
  useEffect(() => {
    const kw = q.trim();
    if (kw.length < 1) { setResults([]); setOpen(false); setLoading(false); return; }
    setOpen(true);
    setLoading(true);
    const id = setTimeout(() => {
      const my = ++reqId.current;
      api.rising.search(kw)
        .then((r) => { if (my === reqId.current) setResults(r.results || []); })
        .catch(() => { if (my === reqId.current) setResults([]); })
        .finally(() => { if (my === reqId.current) setLoading(false); });
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
          className="w-full bg-bg border border-border rounded-lg pl-9 pr-3 py-2 text-sm text-fg placeholder-muted focus:outline-none focus:border-accent" />
        {/* 입력창 안 스피너는 제거했다 — 드롭다운에 '검색 중...' 행이 따로 있어
            로딩 표시가 두 개로 겹쳐 보였다. */}
      </div>
      {open && (
        <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-bg-card border border-border rounded-xl shadow-xl py-1.5 max-h-80 overflow-y-auto">
          {loading && results.length === 0 ? (
            <p className="flex items-center gap-2 px-4 py-3 text-sm text-muted">
              <Loader2 size={13} className="animate-spin" /> 검색 중...
            </p>
          ) : results.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted">결과가 없습니다.</p>
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
//
// 칩마다 테두리를 두르면 그룹이 둘 이상 나란히 놓일 때(기간 + 지표) 똑같이 생긴
// 버튼이 한 줄로 늘어서 어디까지가 한 컨트롤인지 알 수 없다. 그래서 그룹 전체를
// 하나의 '트랙'으로 감싸고, 안쪽 버튼은 테두리 없이 선택된 것만 채운다.
function Seg<T extends string>({ options, value, onChange, label }:
  { options: { k: T; label: string }[]; value: T; onChange: (v: T) => void;
    /** 무엇을 고르는 그룹인지 — 그룹이 둘 이상 나란히 놓일 때만 붙인다 */
    label?: string }) {
  return (
    <div className="flex items-center gap-1.5">
      {label && <span className="text-[11px] text-muted/60 shrink-0">{label}</span>}
      {/* 터치 영역 계약(UI-S 재사용) — **시각 크기와 hit target을 분리한다.**
          `text-xs px-2.5 py-1`은 그대로 두므로 데스크톱(fine) 모양은 픽셀 단위로
          동일하다. `.nb-tap`은 `@media (pointer: coarse)`에서만 `min-height:44px`을
          준다(실측 기준본: 29px 높이 → 44px).
          · **세로만 키운다** — 폭은 이미 전부 44px 이상이라(최소 '2일' 44px)
            가로로 늘리면 이웃과 hit area가 겹친다. UI-S가 경고한 바로 그 경우다.
          · `.nb-tap-gap`이 같은 미디어에서 간격을 2px → 8px로 벌려 오탭을 막는다.
          · `flex-wrap`이라 높이가 커져도 좁은 화면에서 가로로 넘치지 않는다
            (가로 스크롤 소유권을 만들지 않는다 — 줄바꿈으로 해결한다). */}
      <div className="nb-tap-gap flex flex-wrap items-center gap-0.5 rounded-lg
                      border border-border bg-bg-hover/40 p-0.5">
        {options.map((o) => {
          const active = value === o.k;
          return (
            <button key={o.k} onClick={() => onChange(o.k)}
              aria-pressed={active}
              className="nb-tap inline-flex items-center justify-center rounded-md
                         px-2.5 py-1 text-xs transition-colors"
              style={{ background: active ? "rgba(0,255,163,0.12)" : "transparent",
                       boxShadow: active ? "inset 0 0 0 1px rgba(0,194,255,0.4)" : "none",
                       color: active ? GREEN : "rgb(var(--color-muted-rgb))" }}>
              {o.label}
            </button>
          );
        })}
      </div>
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

// 도넛 차트 본체 — 전체/신입 점유율이 공유한다(호버 상태는 범례와 동기화하려고 부모가 소유).
// 여기엔 아크 계산과 중앙 라벨만 두고, 범례 테이블은 컬럼이 달라 각 호출부가 그린다.
function DonutChart({ slices, hover, onHover, centerLabel, centerValue, size = 190 }: {
  slices: { label: string; value: number }[];
  hover: number | null;
  onHover: (i: number | null) => void;
  centerLabel: string;
  centerValue?: string;
  size?: number;
}) {
  const stroke = 26, R = (size - stroke - 10) / 2, C = 2 * Math.PI * R, cx = size / 2, cy = size / 2;
  const total = slices.reduce((s, x) => s + x.value, 0) || 1;
  let acc = 0;
  const segs = slices.map((sl, i) => {
    const len = (sl.value / total) * C;
    const dash = Math.max(0, len - 3); // 세그먼트 사이 3px 간격
    const seg = (
      <circle key={i} r={R} cx={cx} cy={cy} fill="none" stroke={DONUT_PAL[i % DONUT_PAL.length]}
        strokeWidth={hover === i ? stroke + 5 : stroke}
        strokeDasharray={`${dash} ${C - dash}`} strokeDashoffset={-acc}
        transform={`rotate(-90 ${cx} ${cy})`} style={{ transition: "stroke-width .15s", cursor: "pointer" }}
        onMouseEnter={() => onHover(i)} onMouseLeave={() => onHover(null)} />
    );
    acc += len;
    return seg;
  });
  const focus = hover !== null && hover < slices.length ? slices[hover] : null;

  return (
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
            <span className="text-lg font-extrabold text-fg tabular-nums">{centerValue ?? nf(Math.round(total))}</span>
            <span className="text-[11px] text-muted mt-0.5">{centerLabel}</span>
          </>
        )}
      </div>
    </div>
  );
}

// 상위 N개 + '기타'로 도넛 슬라이스 구성 (전체/신입 공용)
function toSlices<T>(rows: T[], topN: number, label: (r: T) => string, value: (r: T) => number) {
  const top = rows.slice(0, topN);
  const restV = rows.slice(topN).reduce((s, r) => s + value(r), 0);
  return [
    ...top.map((r) => ({ label: label(r), value: value(r) })),
    ...(restV > 0 ? [{ label: "기타", value: restV }] : []),
  ];
}

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

  // 도넛: 상위 5 + 기타 / 범례 테이블은 상위 8개
  const slices = toSlices(data, 5, (c) => c.category, (c) => c.viewers);
  const legendRows = data.slice(0, 8);

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
            <DonutChart slices={slices} hover={hover} onHover={setHover} centerLabel="평균 시청자" />
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

// 페이지 하단 고지.
//
// 예전에는 여기에 제목 + 설명 + '통계 안내 보기' 버튼을 가진 **카드**가 있었다.
// 그 카드는 왼쪽 내비의 '통계 안내' 항목과 **같은 곳으로 가는 두 번째 진입점**이라
// 중복이었다 — 화면 맨 아래에서 같은 링크를 다시 제안하는 셈이었다.
// 카드(테두리·제목·버튼)는 걷어내고 **진입점은 왼쪽 내비 하나로 통일**한다.
//
// **문구 전체를 지우지는 않는다.** 두 가지가 남아야 한다:
//  · 법적 고지 — "비공식 서비스, 네이버·치지직과 제휴 관계 없음". 링크 중복과
//    무관하게 표시 의무가 있는 문장이라 함께 지우면 안 된다.
//  · 크롤러가 읽을 본문 — 이 페이지는 데이터를 클라이언트에서 받아오므로 서버
//    렌더링 HTML에는 로딩 스피너만 담긴다. loading/error 분기 **밖**의 본문이
//    최소 하나는 있어야 한다(루트 CLAUDE.md의 AdSense 항목과 같은 이유다).
// 그래서 카드가 아니라 **각주 한 줄**로 남긴다. 위 카드와 달리 테두리·배경이
// 없으므로 마지막 카드와의 사이에 끊긴 경계선이 생기지 않는다.
function StatsAbout() {
  return (
    <section className="mt-8 border-t border-border/60 pt-4">
      <p className="max-w-3xl text-xs leading-relaxed text-muted/80">
        NexBot은 치지직 공개 정보를 자체 수집·가공한 비공식 서비스로 네이버 및 치지직과
        제휴 관계가 없으며, 실제 치지직 화면과 값이 다를 수 있습니다. 수집 방식과 지표
        정의는{" "}
        <Link href="/stats/guide"
              className="font-semibold text-muted underline underline-offset-2
                         transition-colors hover:text-fg">
          통계 안내
        </Link>
        에서 확인하실 수 있습니다.
      </p>
    </section>
  );
}

// ── 개요 탭 ───────────────────────────────────────────────────────────────────
function OverviewTab({ ov, stars }: { ov: RisingOverview; stars: RisingStars | null }) {
  const [range, setRange] = useState<TimeRange>("live");
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
  // 뷰어쉽(hours watched) = 방송 시간 × 평균 시청자.
  // 스냅샷 1개 = 10분 구간으로 보고 Σ(동시 시청자) × (10/60)h 로 적분 — 집계 사이트의
  // hours watched와 동일한 정의. 수집 공백이 있으면 그만큼 과소 집계된다.
  // 버킷 구간에서는 값이 '구간 평균'이므로 samples(그 버킷에 들어간 사이클 수)를 곱해야
  // 원본 합과 같아진다. 원본(live)은 samples=1이라 식이 그대로 유지된다.
  const SNAP_HOURS = 10 / 60;
  const viewership = Math.round(
    (ts?.points ?? []).reduce((s, p) => s + p.total_viewers * (p.samples || 1), 0) * SNAP_HOURS);
  const dv = ov.deltas;
  const points = (ts?.points ?? []) as unknown as LinePoint[];
  const rangeDesc = TS_RANGE_DESC[ts?.range ?? range] ?? TS_RANGE_DESC.live;
  const rangeLabel = TS_RANGE_OPTS.find((o) => o.k === range)?.label ?? "24시간";

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
    // 아직 채워지는 중인 버킷은 평균이 확정치가 아니라 피크·골든타임 후보에서 뺀다
    const ps = (ts?.points ?? []).filter((p) => !p.partial);
    if (ps.length < 2) return null;
    const withAvg = ps.map((p) => ({ ...p, avg: p.total_viewers / Math.max(1, p.live_count) }));
    const peak   = withAvg.reduce((a, b) => (b.total_viewers > a.total_viewers ? b : a));
    const golden = withAvg.reduce((a, b) => (b.avg > a.avg ? b : a));
    const hm = (t: number) => new Date(t * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
    // 골든타임은 '한 시간 구간'이라 시작 시각만 쓰면("00시경") 범위가 안 보인다 →
    // 신규 분석 탭의 시간대 표기와 같은 "00:00 ~ 01:00" 형식으로 맞춘다.
    const hourRange = (t: number) => {
      const h = new Date(t * 1000).getHours();
      const p2 = (n: number) => String(n).padStart(2, "0");
      return `${p2(h)}:00 ~ ${p2((h + 1) % 24)}:00`;
    };
    return {
      peakTime: hm(peak.t), peakViewers: peak.total_viewers,
      goldenHour: hourRange(golden.t), goldenAvg: Math.round(golden.avg),
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
        <StatTile label="뷰어쉽" value={nf(viewership)} unit="시간"
          sub={`최근 ${rangeLabel} 시청 시간 (방송 시간 × 평균 시청자)`} />
      </div>

      {/* 추이 차트 — 기간(24시간/2일/3일) + 지표(시청자/방송수) + 가변 Y축 */}
      <div className="card">
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h3 className="section-title">{chartTitle}</h3>
          {/* 기간과 지표는 성격이 다른 두 컨트롤이다 — 트랙으로 묶고 간격을 벌려
              한 줄짜리 칩 5개로 읽히지 않게 한다. 모바일에서는 두 줄로 접힌다. */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <Seg options={TS_RANGE_OPTS} value={range} onChange={setRange} label="기간" />
            <Seg options={METRIC_OPTS} value={metric} onChange={setMetric} label="지표" />
          </div>
        </div>
        <p className="text-xs text-muted mb-1">{rangeDesc}</p>
        {/* 이력이 짧아도 버튼을 막지 않는다 — 확보된 구간까지 그리고 사실을 알린다 */}
        {ts?.truncated && (
          <p className="text-xs mb-1" style={{ color: "#FFC24D" }}>
            현재 수집 이력 {ts.history_hours}시간 — 선택한 기간보다 짧아 확보된 구간까지만 표시합니다.
          </p>
        )}
        {!!ts?.excluded_points && (
          <p className="text-[11px] text-muted/70 mb-1">
            수집 실패·부분 수집 {nf(ts.excluded_points)}회는 값이 왜곡되므로 제외했습니다
            (해당 구간은 선이 끊깁니다).
          </p>
        )}
        <div className="mb-4" />
        {tsLoading ? <ChartSkeleton /> : (
          <LineChart points={points} series={[chartSeries]} area dynamicY unit={chartUnit}
            tooltipItems={tooltipItems} showPeak
            stepSeconds={ts?.step_seconds ?? 600}
            tooltipNote={(p) => {
              if (!ts?.bucket_seconds) return null;
              const mins = Math.round(ts.bucket_seconds / 60);
              const base = `${mins}분 구간 평균 (수집 ${p.samples}회)`;
              return p.partial ? `${base} · 아직 집계 중인 구간입니다` : base;
            }} />
        )}
        {/* 범례 */}
        <div className="mt-3 flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
          <span className="flex items-center gap-1.5 text-[11px] text-muted">
            <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: "#FF4FA3" }} />
            피크 타임(최고 시청자)
          </span>
          {!!ts?.bucket_seconds && (
            <span className="flex items-center gap-1.5 text-[11px] text-muted">
              <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-dashed"
                    style={{ borderColor: GREEN }} />
              집계 중(미완성 구간)
            </span>
          )}
        </div>
      </div>

      {/* 요일·시간대 트래픽 히트맵 */}
      <TrafficHeatmap />

      {/* 스트리머 인사이트 */}
      <div className="card">
        <h3 className="section-title mb-1">스트리머 인사이트</h3>
        <p className="text-xs text-muted mb-4">선택 기간의 트래픽 패턴에서 뽑은 방송 전략 힌트</p>
        {insights ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {/* 세부 설명은 text-[11px]이 너무 작아 읽히지 않아 text-sm으로 키웠다 */}
            <div className="rounded-xl border border-border p-4">
              <p className="text-xs text-muted">유입 골든타임 (빈집)</p>
              <p className="text-xl font-extrabold mt-1 tabular-nums"><GradText>{insights.goldenHour}</GradText></p>
              <p className="text-sm text-muted mt-1.5 leading-relaxed">
                방송당 평균 <b className="text-fg">{nf(insights.goldenAvg)}명</b> — 경쟁 방송이 적어 노출·유입 기회가 큰 구간
              </p>
            </div>
            <div className="rounded-xl border border-border p-4">
              <p className="text-xs text-muted">피크 타임</p>
              <p className="text-xl font-extrabold mt-1 tabular-nums"><GradText grad={PEAK_GRAD}>{insights.peakTime}</GradText></p>
              <p className="text-sm text-muted mt-1.5 leading-relaxed">
                최고 동시 시청자 <b className="text-fg">{nf(insights.peakViewers)}명</b> — 플랫폼 트래픽이 가장 몰리는 시간
              </p>
            </div>
            <div className="rounded-xl border border-border p-4">
              <p className="text-xs text-muted">전체 방송당 평균 (체급 기준선)</p>
              <p className="text-xl font-extrabold mt-1 tabular-nums"><GradText>{nf(avgV)}명</GradText></p>
              <p className="text-sm text-muted mt-1.5 leading-relaxed">
                내 평균 시청자가 이보다 높으면 플랫폼 평균 이상 체급
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted py-4 text-center">인사이트 산출을 위한 데이터가 아직 부족합니다.</p>
        )}
      </div>

      {/* 시청자 체급별 분포 */}
      <ViewerDistribution />

      {/* 카테고리 점유율 (자체 시간 필터) */}
      <CategoryDonut />

      {/* 급상승 스트리머 + 인기 제목 키워드 (2열, 높이 1:1) */}
      <div className="grid grid-cols-1 items-stretch gap-5 lg:grid-cols-2">
      <div className="card flex h-full flex-col">
        <h3 className="section-title mb-1">급상승 스트리머</h3>
        <p className="text-sm text-muted mb-4">24시간 전 대비 동시 시청자 성장률 상위</p>
        {stars && stars.stars.length > 0 ? (
          // flex-1 + justify-between: 옆 칼럼(키워드 10행)이 더 길어 아래가 비던 것을
          // 행 간격으로 채운다. 카테고리는 전체 스트리머 랭킹과 같은 알약형 뱃지를 쓴다.
          <div className="flex flex-1 flex-col justify-between gap-1">
            {stars.stars.slice(0, 8).map((s, i) => (
              <a key={s.chzzk_channel_id} href={`https://chzzk.naver.com/${s.chzzk_channel_id}`}
                 target="_blank" rel="noopener noreferrer"
                 className="flex items-center gap-3 rounded-lg px-2.5 py-2 hover:bg-bg-hover transition-colors">
                <span className="w-5 shrink-0 text-center text-sm font-bold tabular-nums"
                      style={{ color: i < 3 ? GREEN : undefined }}>{i + 1}</span>
                <span className="min-w-0 flex-1 truncate text-[15px] font-semibold text-fg">{s.channel_name}</span>
                {s.category
                  ? <span className="hidden max-w-[150px] shrink-0 truncate rounded-full border border-border
                                     bg-bg-hover px-3 py-1 text-xs font-medium text-fg sm:inline-block">{s.category}</span>
                  : <span className="hidden shrink-0 text-sm text-muted sm:inline-block">-</span>}
                <span className="flex shrink-0 items-center gap-1 text-[15px] font-bold tabular-nums" style={{ color: GREEN }}>
                  <TrendingUp size={14} /> +{nf(s.growth_rate)}%
                </span>
              </a>
            ))}
          </div>
        ) : (
          <RisingSkeleton historyHours={ov.history_hours ?? 0} />
        )}
      </div>

      {/* 인기 방송 제목 키워드 TOP 10 */}
      <TitleKeywordRank />
      </div>
    </div>
  );
}

// ── 랭킹 탭 (소프트콘식 실시간 방송 랭킹 테이블) ──────────────────────────────
type SortKey = "viewers" | "followers" | "duration";
function RankingTab({ rank }: { rank: RisingLiveRanking }) {
  const [sort, setSort] = useState<SortKey>("viewers");
  const [limit, setLimit] = useState(50);

  // 전체 스트리머 랭킹은 최대 100명까지만
  const enriched = useMemo(() =>
    rank.streamers.slice(0, 100).map((s) => ({ ...s, dur: liveDuration(s.open_date) })), [rank]);

  // 검색은 상단 공용 '스트리머 검색'(StreamerSearch)이 담당 — 여기선 정렬만.
  const filtered = useMemo(() =>
    [...enriched].sort((a, b) =>
      sort === "viewers" ? b.concurrent_viewers - a.concurrent_viewers
      : sort === "followers" ? b.follower_count - a.follower_count
      : b.dur.ms - a.dur.ms),
  [enriched, sort]);

  // 프로그레스 바 기준값(리스트 전체 최대치). 방송시간만 DAY_MS 고정 기준을 쓴다.
  const maxViewers  = useMemo(() => Math.max(1, ...enriched.map((s) => s.concurrent_viewers)), [enriched]);
  const maxFollower = useMemo(() => Math.max(1, ...enriched.map((s) => s.follower_count)), [enriched]);

  const SortBtn = ({ k, label }: { k: SortKey; label: string }) => (
    <button onClick={() => setSort(k)}
      className="text-xs font-medium px-3 py-1.5 rounded-lg border transition-colors"
      style={{ background: sort === k ? "rgba(0,255,163,0.1)" : "transparent",
               borderColor: sort === k ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
               color: sort === k ? GREEN : "rgb(var(--color-muted-rgb))" }}>
      {label}
    </button>
  );

  return (
    <div className="space-y-5">
      {/* 랭킹 요약 차트 (Top 10 수평 막대 / 성장성 산점도) */}
      <RankingCharts
        rows={filtered.map((s) => ({
          ...s,
          deltaPct: s.viewers_prev && s.viewers_prev > 0
            ? ((s.concurrent_viewers - s.viewers_prev) / s.viewers_prev) * 100 : null,
          // 팔로워 증가량은 24시간 전 스냅샷이 있는 채널만 계산 가능
          yValue: s.follower_prev24h != null && s.follower_count > 0
            ? s.follower_count - s.follower_prev24h : null,
        }))}
        y={{ label: "팔로워 증가량", unit: "명", log: true, tooltip: "팔로워 증가" }}
        deltaName="직전 대비" />

      <div className="card !p-4 md:!p-5">
      {/* 컨트롤 — 정렬만 (검색바는 제거). 신규 스트리머 랭킹과 동일한 배치·크기 */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <SortBtn k="viewers" label="시청자" />
        <SortBtn k="followers" label="팔로워" />
        <SortBtn k="duration" label="방송 시간" />
      </div>

      {/* 테이블 */}
      <div className="overflow-x-auto">
        {/* 패딩(px-6)과 폰트를 키운 만큼 최소 폭도 함께 올린다 — 좁은 화면에선 가로 스크롤 */}
        <table className="w-full text-sm min-w-[720px]">
          <thead>
            <tr className="text-muted text-xs border-b border-border">
              <th className="text-left font-medium py-2 pl-2 w-12">#</th>
              <th className="text-left font-medium py-2">스트리머</th>
              <th className="text-left font-medium py-2 px-6 hidden sm:table-cell">카테고리</th>
              <th className="text-right font-medium py-2 px-6">전체 시청자</th>
              <th className="text-right font-medium py-2 px-6 hidden md:table-cell">방송시간</th>
              <th className="text-right font-medium py-2 px-6">팔로워</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, limit).map((s, i) => {
              const vwPct  = (s.concurrent_viewers / maxViewers) * 100;
              // 방송시간 바는 '최대 24시간' 고정 기준 — 1위 방송시간 대비가 아니라
              // 절대 길이를 읽을 수 있어야 하루 중 얼마나 켜뒀는지가 한눈에 들어온다.
              const durPct = s.dur.ms > 0 ? Math.min(100, (s.dur.ms / DAY_MS) * 100) : 0;
              const folPct = s.follower_count > 0 ? (s.follower_count / maxFollower) * 100 : 0;
              const medal  = MEDALS[i]; // TOP 3만 값이 있음
              const vwDelta = s.viewers_prev && s.viewers_prev > 0
                ? ((s.concurrent_viewers - s.viewers_prev) / s.viewers_prev) * 100 : null;
              const newFol = s.follower_prev24h != null ? s.follower_count - s.follower_prev24h : null;
              return (
                <tr key={s.chzzk_channel_id}
                    className="border-b border-border hover:bg-bg-hover/70 transition-colors">
                  {/* 순위 — TOP 3는 골드/실버/브론즈 강조 */}
                  <td className="py-3.5 pl-2 tabular-nums text-sm align-middle">
                    {medal
                      ? <span className="font-extrabold" style={{ color: medal.color }}>#{i + 1}</span>
                      : <span className="text-muted">{i + 1}</span>}
                  </td>

                  {/* 스트리머 — 개인 분석 대시보드로 이동 */}
                  <td className="py-3.5 align-middle">
                    <Link href={`/stats/streamer/${s.chzzk_channel_id}`}
                       className="flex items-center gap-2 group">
                      <StreamerAvatar src={s.channel_image_url} index={i}
                                      ringStyle={medal ? { boxShadow: `0 0 0 2px ${medal.color}, 0 0 8px ${medal.color}66` } : undefined} />
                      <ChzzkMark />
                      <span className="text-base font-semibold text-fg group-hover:text-accent transition-colors truncate max-w-[150px] md:max-w-none">
                        {s.channel_name}
                      </span>
                      {/* 팀/소속 태그. 목록에서는 2개까지만 보이고 나머지는 +N으로 접힌다.
                          태그가 없으면 아무것도 렌더하지 않으므로 행 높이가 지금과 같다. */}
                      <StreamerTagList tags={s.team_tags} />
                      <BarChart3 size={12} className="text-muted opacity-0 group-hover:opacity-100 shrink-0" />
                    </Link>
                  </td>

                  {/* 카테고리 — 알약형 뱃지 (테마 토큰 사용, 라이트 모드에서도 대비 유지) */}
                  <td className="py-3.5 px-6 hidden sm:table-cell align-middle">
                    {s.category_name
                      ? <span className="inline-block max-w-[150px] truncate rounded-full border border-border
                                         bg-bg-hover px-3 py-1 text-xs font-medium text-fg">{s.category_name}</span>
                      : <span className="text-muted text-sm">-</span>}
                  </td>

                  {/* 현재 시청자 — 증감 + 숫자 / 바로 아래 골드 바 (1위 대비 %) */}
                  <td className="py-3.5 px-6 align-middle" style={{ minWidth: 140 }}>
                    <CellCol>
                      <div className="flex items-center justify-end gap-1.5">
                        {vwDelta !== null && <span className="text-[11px]"><Delta pct={vwDelta} /></span>}
                        <StatNum value={s.concurrent_viewers} unit="명" />
                      </div>
                      <CellBar pct={vwPct} background={YELLOW_GRAD} />
                    </CellCol>
                  </td>

                  {/* 방송시간 — 퍼플 바 (최대 24시간 대비 비율) */}
                  <td className="py-3.5 px-6 hidden md:table-cell align-middle" style={{ minWidth: 128 }}>
                    <CellCol>
                      <div className="text-right tabular-nums text-muted text-sm">{s.dur.label}</div>
                      <CellBar pct={durPct} background={PURPLE_GRAD} />
                    </CellCol>
                  </td>

                  {/* 팔로워 — 신규 유입 + 시안 바 */}
                  <td className="py-3.5 px-6 align-middle" style={{ minWidth: 140 }}>
                    <CellCol>
                      <div className="flex items-center justify-end gap-1.5">
                        {newFol != null && newFol > 0 &&
                          <span className="text-[11px] font-semibold tabular-nums" style={{ color: "#06B6D4" }}>+{nf(newFol)}</span>}
                        {s.follower_count > 0 ? <StatNum value={s.follower_count} unit="명" /> : <span className="text-sm text-muted">-</span>}
                      </div>
                      <CellBar pct={folPct} background={CYAN_GRAD} />
                    </CellCol>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {filtered.length === 0 && (
        <p className="text-sm text-muted text-center py-6 leading-relaxed">
          검색 결과가 없습니다.<br />
          <span className="text-xs">
            채널명 일부만 입력해 보시거나, 현재 방송 중이 아닌 채널일 수 있습니다.
          </span>
        </p>
      )}
      {filtered.length > limit && (
        <div className="text-center pt-4">
          <button onClick={() => setLimit((l) => l + 50)} className="btn-secondary text-sm">
            더 보기 ({nf(filtered.length - limit)}개 남음)
          </button>
        </div>
      )}
      </div>
    </div>
  );
}

// ── 신규/라이징 탭 ────────────────────────────────────────────────────────────
// 백엔드 _CAT_MIN_LIVES와 동일 — 표본 부족 안내 문구용
const NC_CAT_MIN_LIVES = 3;
// accent=true인 카드만 그린→시안 2톤 그라데이션을 쓴다. 나머지 KPI 수치는(전체 스트리머
// 분석의 StatTile도 동일하게) 흰색 — 강조가 전부에 걸리면 강조가 아니게 되므로 1장만 남겼다.
function NcTile({ label, value, unit, accent }:
  { label: string; value: string; unit?: string; accent?: boolean }) {
  return (
    <div className="card !p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1.5 tracking-tight">
        <span className="text-xl md:text-2xl font-extrabold tabular-nums text-white">
          {accent ? <GradText>{value}</GradText> : value}
        </span>
        {unit && <span className="text-sm text-muted font-normal ml-1">{unit}</span>}
      </p>
    </div>
  );
}
function InsightCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border p-5">
      <p className="text-base font-bold text-fg">{title}</p>
      <p className="text-sm text-muted mt-2 leading-relaxed">{children}</p>
    </div>
  );
}

// 표 헤더용 도움말 — 회색 원형 '?'. 호버 미리보기 + 클릭 토글.
// 순수 CSS(group-hover/focus-within)만 쓰면 터치 기기에는 hover가 없고, Safari/Firefox는
// 폼 요소가 아닌 span을 클릭해도 포커스를 주지 않아 "눌러도 안 뜬다"가 된다.
// 실제 <button>과 open 상태로 처리해 클릭에서도 확실히 열리도록 한다.
const HELPTIP_W = 288; // w-72 — 화면 밖으로 나가지 않게 좌표 계산에 쓰인다

function HelpTip({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);   // 클릭 토글
  const [hover, setHover] = useState(false); // 호버 미리보기
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const shown = open || hover;

  // 툴팁을 버튼 '아래'에 띄운다 — 테이블 헤더 위쪽으로 열면 카드 경계에 잘리기 쉬움.
  const place = useCallback(() => {
    const el = btnRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const m = 8;
    const left = Math.max(m, Math.min(r.left + r.width / 2 - HELPTIP_W / 2, window.innerWidth - HELPTIP_W - m));
    setPos({ top: r.bottom + 8, left });
  }, []);

  useEffect(() => {
    if (!shown) return;
    place();
    // 스크롤/리사이즈 중에도 버튼을 따라다니게 (capture: 내부 스크롤 컨테이너까지 잡기 위함)
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => { window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place); };
  }, [shown, place]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || tipRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  return (
    <span className="relative inline-flex align-middle ml-1">
      <button ref={btnRef} type="button" aria-label="설명 보기" aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        className="w-4 h-4 rounded-full border border-border bg-bg-hover text-muted
                   text-[10px] font-bold leading-none flex items-center justify-center
                   cursor-help select-none transition-colors hover:text-fg
                   focus:outline-none focus:border-accent">?</button>

      {/* body 포털 + position:fixed — 테이블의 overflow-x-auto 클리핑과
          카드/헤더가 만드는 stacking context를 모두 벗어난다. */}
      {shown && pos && typeof document !== "undefined" && createPortal(
        <div ref={tipRef} role="tooltip"
          onMouseEnter={() => setHover(true)}
          onMouseLeave={() => setHover(false)}
          style={{ position: "fixed", top: pos.top, left: pos.left, width: HELPTIP_W, zIndex: 9999 }}
          className="rounded-lg border border-border bg-bg-card px-3 py-2 text-[11px]
                     font-normal leading-relaxed text-fg text-left normal-case shadow-2xl">
          {children}
        </div>,
        document.body
      )}
    </span>
  );
}

// 카테고리 점유율(신입 기준) — 전체 스트리머 분석의 CategoryDonut과 같은 구성.
// 데이터는 newcomers 응답에 함께 실려오므로 별도 요청/기간 필터가 없다.
function NewcomerCategoryDonut({ cats, label = "신입" }:
  { cats: NewcomerCategory[]; label?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const slices = toSlices(cats, 5, (c) => c.category, (c) => c.viewers);
  const legendRows = cats.slice(0, 8);
  const totalLives = cats.reduce((s, c) => s + c.lives, 0);

  return (
    <div className="card flex h-full flex-col">
      <div className="flex items-start justify-between mb-4 flex-wrap gap-2">
        <div>
          <h3 className="section-title">카테고리 점유율 ({label})</h3>
          <p className="text-xs text-muted mt-0.5">
            현재 {label} 방송의 시청자가 어떤 카테고리에 몰려 있는지 — 라이브 {nf(totalLives)}개 기준
          </p>
        </div>
      </div>

      {cats.length === 0 ? (
        <p className="text-sm text-muted py-6 text-center">{label} 카테고리 데이터가 아직 없습니다.</p>
      ) : (
        // flex-1 + items-center: 카드가 h-full로 늘어나도 내용이 위로 붙지 않고
        // 남는 높이 안에서 가운데 정렬된다(하단에 까맣게 남던 공간 제거).
        <div className="grid flex-1 grid-cols-1 items-center gap-5 md:grid-cols-12">
          <div className="md:col-span-5 flex justify-center">
            <DonutChart slices={slices} hover={hover} onHover={setHover}
                        centerLabel={`${label} 총 시청자`} />
          </div>

          <div className="md:col-span-7 overflow-x-auto">
            <table className="w-full text-sm min-w-[360px]">
              <thead>
                <tr className="text-muted text-xs border-b border-border">
                  <th className="text-left font-medium py-1.5 w-7">#</th>
                  <th className="text-left font-medium py-1.5">카테고리</th>
                  <th className="text-right font-medium py-1.5">점유율</th>
                  <th className="text-right font-medium py-1.5 hidden sm:table-cell">방송 수</th>
                  <th className="text-right font-medium py-1.5 pr-1">방송당 평균</th>
                </tr>
              </thead>
              <tbody>
                {legendRows.map((c, i) => (
                  <tr key={c.category}
                      onMouseEnter={() => setHover(i < slices.length ? i : null)} onMouseLeave={() => setHover(null)}
                      className="border-b border-border/40 transition-colors"
                      style={{ background: hover === i ? "rgb(var(--color-bg-hover-rgb))" : "transparent" }}>
                    <td className="py-1.5 tabular-nums text-muted text-xs">{i + 1}</td>
                    <td className="py-1.5">
                      <span className="flex items-center gap-2 min-w-0">
                        <span className="w-2.5 h-2.5 rounded-full shrink-0"
                              style={{ background: i < 5 ? DONUT_PAL[i] : "rgb(var(--color-muted-rgb))" }} />
                        <span className="text-fg truncate">{c.category}</span>
                      </span>
                    </td>
                    <td className="py-1.5 text-right font-semibold tabular-nums text-fg">{c.share.toFixed(1)}%</td>
                    <td className="py-1.5 text-right tabular-nums text-muted hidden sm:table-cell">{nf(c.lives)}개</td>
                    <td className="py-1.5 text-right pr-1 tabular-nums font-semibold"><GradText>{nf(c.avg_viewers)}</GradText></td>
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

// 신입 인라인 프로그레스 바 테이블 (랭킹 탭 + 분석 탭 미리보기 공용)
// (첫 방송일/추적 일차 뱃지는 이름 아래에 붙였다가 표가 지저분해져 걷어냈다.
//  first_stream_date/debut_days 는 백엔드 필터링에 계속 쓰이므로 응답에는 그대로 있다.)
function NewcomerTable({ items, maxViewers, maxFollower }:
  { items: RisingNewcomer[]; maxViewers: number; maxFollower: number }) {
  return (
    <div className="overflow-x-auto">
      {/* 레이아웃/타이포/컬러는 전체 스트리머 랭킹 테이블과 동일 규격 */}
      <table className="w-full text-sm min-w-[780px]">
        <thead>
          <tr className="text-muted text-xs border-b border-border">
            <th className="text-left font-medium py-2 pl-2 w-12">#</th>
            <th className="text-left font-medium py-2">스트리머</th>
            <th className="text-left font-medium py-2 px-6 hidden sm:table-cell">카테고리</th>
            <th className="text-right font-medium py-2 px-6">
              <span className="inline-flex items-center justify-end whitespace-nowrap">
                성장률
                <HelpTip>
                  <b className="block text-fg mb-1">성장률이란?</b>
                  지금 이 채널이 <b className="text-fg">평소보다</b> 얼마나 잘 나오고 있는지를 나타냅니다.
                  <span className="block my-1.5 rounded bg-bg-hover px-2 py-1 font-mono text-[10px] text-fg">
                    (현재 시청자 − 최근 7일 평균) ÷ 최근 7일 평균 × 100
                  </span>
                  <span className="block">
                    <b style={{ color: UP_GREEN }}>+50%</b> = 평소 평균의 1.5배가 보고 있는 중,{" "}
                    <b style={{ color: DOWN_RED }}>−20%</b> = 평소보다 적은 상태입니다.
                  </span>
                  <span className="block mt-1.5 text-muted">
                    절대 시청자 수가 아니라 <b className="text-fg">자기 자신 대비</b> 변화라, 소규모 채널도 상위권에 오를 수 있습니다.
                  </span>
                  <span className="block mt-1.5 text-muted">
                    다만 7일 평균이 <b className="text-fg">1명 미만</b>이면 1명으로 계산합니다.
                    0.3명 같은 값을 그대로 나누면 +1600% 처럼 분모 때문에 생기는 수치가
                    나와 순위가 의미를 잃기 때문입니다. 최근 7일 방송 이력이 1시간 미만이면
                    표본이 부족해 성장률을 내지 않습니다.
                  </span>
                </HelpTip>
              </span>
            </th>
            <th className="text-right font-medium py-2 px-6">시청자</th>
            <th className="text-right font-medium py-2 px-6 hidden md:table-cell">방송시간</th>
            <th className="text-right font-medium py-2 px-6">팔로워</th>
          </tr>
        </thead>
        <tbody>
          {items.map((s, i) => {
            const dur = liveDuration(s.open_date);
            const vwPct = (s.concurrent_viewers / maxViewers) * 100;
            const durPct = dur.ms > 0 ? Math.min(100, (dur.ms / DAY_MS) * 100) : 0;
            const folPct = s.follower_count > 0 ? (s.follower_count / maxFollower) * 100 : 0;
            const medal = MEDALS[i]; // TOP 3만 값이 있음
            return (
              <tr key={s.chzzk_channel_id} className="border-b border-border hover:bg-bg-hover/70 transition-colors">
                {/* 순위 — TOP 3는 골드/실버/브론즈 강조 */}
                <td className="py-3.5 pl-2 tabular-nums text-sm align-middle">
                  {medal
                    ? <span className="font-extrabold" style={{ color: medal.color }}>#{i + 1}</span>
                    : <span className="text-muted">{i + 1}</span>}
                </td>
                <td className="py-3.5 align-middle">
                  <Link href={`/stats/streamer/${s.chzzk_channel_id}`} className="flex items-center gap-2 group">
                    <StreamerAvatar src={s.channel_image_url} index={i}
                                      ringStyle={medal ? { boxShadow: `0 0 0 2px ${medal.color}, 0 0 8px ${medal.color}66` } : undefined} />
                    <ChzzkMark />
                    <span className="text-base font-semibold text-fg group-hover:text-accent transition-colors truncate max-w-[130px] md:max-w-none">{s.channel_name}</span>
                    {/* 팀/소속 태그 — 랭킹 목록과 같은 규칙(2개 + +N) */}
                    <StreamerTagList tags={s.team_tags} />
                  </Link>
                </td>
                <td className="py-3.5 px-6 hidden sm:table-cell align-middle">
                  {s.category_name
                    ? <span className="inline-block max-w-[150px] truncate rounded-full border border-border
                                       bg-bg-hover px-3 py-1 text-xs font-medium text-fg">{s.category_name}</span>
                    : <span className="text-muted text-sm">-</span>}
                </td>
                {/* 성장률 — 다른 셀은 수치+바 2줄이라 align-middle이지만 이 셀은 1줄이라
                    가운데 정렬해야 행 높이 안에서 위로 붙지 않는다. 크기도 한 단계 키움. */}
                <td className="py-3.5 px-6 text-right text-sm font-semibold align-middle whitespace-nowrap">
                  <Delta pct={s.growth_rate} />
                </td>
                {/* 시청자 — 바로 아래 골드 바 (1위 대비 %) */}
                <td className="py-3.5 px-6 align-middle" style={{ minWidth: 132 }}>
                  <CellCol>
                    <div className="text-right"><StatNum value={s.concurrent_viewers} unit="명" /></div>
                    <CellBar pct={vwPct} background={YELLOW_GRAD} />
                  </CellCol>
                </td>
                {/* 방송시간 — 퍼플 바 (최대 24시간 대비 비율) */}
                <td className="py-3.5 px-6 align-middle hidden md:table-cell" style={{ minWidth: 128 }}>
                  <CellCol>
                    <div className="text-right tabular-nums text-muted text-sm">{dur.label}</div>
                    <CellBar pct={durPct} background={PURPLE_GRAD} />
                  </CellCol>
                </td>
                {/* 팔로워 — 시안 바 */}
                <td className="py-3.5 px-6 align-middle" style={{ minWidth: 132 }}>
                  <CellCol>
                    <div className="text-right">{s.follower_count > 0 ? <StatNum value={s.follower_count} unit="명" /> : <span className="text-sm text-muted">-</span>}</div>
                    <CellBar pct={folPct} background={CYAN_GRAD} />
                  </CellCol>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── 신규 / 소형 스트리머 통계 ────────────────────────────────────────────────
// 두 그룹은 서로 독립된 축이고 교집합이 있는 게 정상이다. 예전에는 한 메뉴 안의
// 세그먼티드 컨트롤(`NcGroupToggle`)로 전환했는데, 메뉴를 가르면서 그 컨트롤은
// 제거했다 — 메뉴와 토글이 같은 일을 두 방식으로 하면 어느 쪽이 현재 상태인지
// URL과 화면이 어긋난다. **그룹 정의(60일 / 평균 10명 이하)는 바꾸지 않았다.**

// 신입 평균 체급 기준선 + 체급 구간 분포 (Row 2 우측 40%)
function BaselineCard({ ins, label }: { ins?: NewcomerInsights; label: string }) {
  const b = ins?.baseline;
  // 신입 그룹은 0~2명에 표본이 몰려 있어 상위 20%와 10% 컷이 실제로 같은 값이 되는 일이
  // 잦다. 그때 같은 수치를 두 번 쓰면 오류처럼 보이므로 문장을 하나로 합친다.
  const sameCut = !!b && b.top20_cut === b.top10_cut;
  return (
    <div className="card flex h-full flex-col">
      <h3 className="section-title">{label} 평균 체급 기준선</h3>
      <p className="mt-2 text-sm leading-relaxed text-muted">
        {b
          ? <>현재 {label} 그룹 평균은 <b className="text-fg">{nf(b.avg_viewers)}명</b>입니다.{" "}
              {sameCut
                ? <>동시 시청자 <b style={{ color: GREEN }}>{nf(b.top10_cut)}명</b>이면 이미 상위 10%권입니다 —
                    이 구간은 표본이 몰려 있어 한 명 차이로 순위가 크게 움직입니다.</>
                : <><b style={{ color: GREEN }}>{nf(b.top20_cut)}명</b> 달성 시 상위 20%,{" "}
                    <b style={{ color: GREEN }}>{nf(b.top10_cut)}명</b> 달성 시 상위 10%권에 진입하여
                    메인 노출 기회가 대폭 늘어납니다.</>}
            </>
          : "데이터가 아직 부족합니다."}
      </p>
      {/* mt-auto: 좌측 히트맵 카드와 높이를 맞출 때 분포 바가 카드 하단에 붙도록 */}
      {ins?.tiers && ins.tiers.length > 0 && (
        <div className="mt-auto"><TierDistribution tiers={ins.tiers} label={label} /></div>
      )}
    </div>
  );
}

// 소형 탭 Row 3 좌측 — 방송 '수'가 많은 카테고리 TOP 10.
// 블루오션(시청자 효율)과 반대로, 소형이 실제로 어디에 몰려 있는지(=경쟁이 센 곳)를 본다.
function SmallCategoryTop10({ cats }: { cats: NewcomerCategory[] }) {
  const top = useMemo(() => [...cats].sort((a, b) => b.lives - a.lives).slice(0, 10), [cats]);
  const max = Math.max(1, ...top.map((c) => c.lives));
  return (
    <div className="card flex h-full flex-col">
      <h3 className="section-title">소형 채널이 많이 켜는 카테고리 TOP 10</h3>
      <p className="mt-0.5 text-[11px] text-muted">
        방송 수 기준 — 사람이 몰려 있는 만큼 경쟁도 센 구간입니다.
      </p>
      {top.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted leading-relaxed">
          카테고리 데이터가 아직 없습니다.<br />
          <span className="text-xs">다음 수집(약 10분 간격)이 끝나면 표시됩니다.</span>
        </p>
      ) : (
        // flex-1 + justify-between: 옆 칼럼(성장률 리스트)이 더 길어 아래가 비던 것을
        // 행 간격을 늘려 채운다. 행 수가 같아도 리스트 쪽 행이 더 높기 때문.
        <div className="mt-4 flex flex-1 flex-col justify-between gap-3">
          {top.map((c, i) => (
            <div key={c.category}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="w-4 shrink-0 text-sm tabular-nums text-muted">{i + 1}</span>
                  <span className="truncate text-[15px] font-semibold text-fg">{c.category}</span>
                </span>
                <span className="shrink-0 text-sm tabular-nums text-muted">
                  방송 <b className="text-fg">{nf(c.lives)}</b>개 · 평균 {nf(c.avg_viewers)}명
                </span>
              </div>
              <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-bg-hover">
                <div className="h-full rounded-full"
                     style={{ width: `${(c.lives / max) * 100}%`, background: GRAD }} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// 소형 탭 Row 3 우측 — 성장률 상위 소형 채널. 좁은 칼럼이라 표 대신 압축 리스트.
function SmallGrowthList({ items }: { items: RisingNewcomer[] }) {
  // 옆 칼럼(카테고리 TOP 10)과 행 수를 맞춰 2열 높이가 어긋나지 않게 한다
  const top = useMemo(() =>
    [...items].sort((a, b) => (b.growth_rate ?? -1e9) - (a.growth_rate ?? -1e9)).slice(0, 10),
    [items]);
  return (
    <div className="card h-full">
      <h3 className="section-title">성장률 상위 소형 채널</h3>
      <p className="mt-0.5 text-[11px] text-muted">현재 시청자 vs 최근 7일 평균</p>
      {top.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted">조건에 맞는 소형 방송이 아직 없습니다.</p>
      ) : (
        <ul className="mt-3 divide-y divide-border">
          {top.map((s, i) => (
            <li key={s.chzzk_channel_id}>
              <Link href={`/stats/streamer/${s.chzzk_channel_id}`}
                    className="group flex items-center gap-2 py-2">
                <span className="w-4 shrink-0 text-xs tabular-nums text-muted">{i + 1}</span>
                <StreamerAvatar src={s.channel_image_url} index={i} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold text-fg transition-colors group-hover:text-accent">
                    {s.channel_name}
                  </span>
                  <span className="block truncate text-[11px] text-muted">
                    {s.category_name || "카테고리 없음"} · 시청자 {nf(s.concurrent_viewers)}명
                  </span>
                </span>
                <span className="shrink-0 text-sm font-semibold"><Delta pct={s.growth_rate} /></span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * 신규/소형 스트리머 **통계** 한 개 탭.
 *
 * 예전에는 두 그룹을 한 탭 안 세그먼티드 컨트롤로 전환했다. 두 그룹은 서로
 * 독립된 축이고 교집합이 있는 게 정상인데, 한 메뉴에 묶여 있어서
 *  (1) 둘 중 하나만 URL로 공유할 수 없었고
 *  (2) 뒤로가기가 메뉴 밖으로 나갔으며
 *  (3) 로딩·오류 상태가 두 그룹에 공유돼 한쪽 실패가 다른 쪽 화면을 덮었다.
 * 이제 **메뉴가 갈리고 상태도 탭마다 따로** 산다. 그룹 정의는 바꾸지 않았다.
 *
 * `initial`은 페이지 진입 시 이미 받아 둔 `group=new` 응답이다. 소형은 그 탭을
 * 처음 열 때 한 번만 받아 캐시한다(요청을 늘리지 않는다).
 */
function NewcomerStatsTab({ group, initial, onRanking }: {
  group: NewcomerGroup;
  initial?: RisingNewcomers | null;
  onRanking: () => void;
}) {
  const isSmall = group === "small";
  const [data, setData] = useState<RisingNewcomers | null>(initial ?? null);
  const [loading, setLoading] = useState(!initial);
  // 오류 상태도 탭마다 따로 산다 — 공유하면 한쪽 실패가 다른 탭을 덮는다.
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (data) return;
    let alive = true;
    setLoading(true); setErr(null);
    api.rising.newcomers(80, group)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => {
        if (alive) setErr(e instanceof Error ? e.message : "데이터를 불러오지 못했습니다.");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [group, data]);

  const title = isSmall ? "소형 스트리머 통계" : "신규 스트리머 통계";
  const Icon = isSmall ? Leaf : Sprout;

  return (
    <div className="space-y-5">
      <div className="min-w-0 max-w-2xl">
        <h2 className="flex items-center gap-2 text-xl font-extrabold tracking-tight md:text-2xl">
          <Icon size={20} style={{ color: GREEN }} /> {title}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          {isSmall
            ? `방송 경력과 무관하게 최근 평균 동시 시청자 ${data?.criteria?.small_avg_max ?? 10}명 이하인 채널입니다.`
            : `첫 방송 후 ${data?.criteria?.debut_max_days ?? 60}일 이내인 채널입니다. 첫 방송일은 치지직 채널 정보 기준이며, 아직 수집되지 않은 채널은 NexBot 최초 트랙킹 일자로 보완합니다.`}
        </p>
        {/* 두 메뉴가 서로를 배제하지 않는다는 것을 화면에서 밝힌다 —
            메뉴가 갈리면서 "둘 중 하나"로 읽힐 여지가 생겼기 때문이다. */}
        <p className="mt-1 text-xs leading-relaxed text-muted/70">
          {isSmall
            ? "신규 스트리머 통계와 기준이 다르며, 두 목록에 함께 나오는 채널이 있을 수 있습니다."
            : "소형 스트리머 통계와 기준이 다르며, 두 목록에 함께 나오는 채널이 있을 수 있습니다."}
        </p>
      </div>

      {/* 상태 셋을 서로 구분한다: 로딩 / 오류 / 빈 데이터.
          예전에는 셋 다 같은 자리에 "불러오는 중"만 보여 실패가 로딩으로 보였다. */}
      {err ? (
        <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-6">
          <p className="text-sm font-semibold text-red-400">{title}를 불러오지 못했습니다.</p>
          <p className="mt-1 text-xs text-muted">{err}</p>
        </div>
      ) : loading && !data ? (
        <div className="flex items-center justify-center gap-2 py-24 text-muted" aria-busy>
          <Loader2 size={18} className="animate-spin" /> 불러오는 중...
        </div>
      ) : !data || data.streamers.length === 0 ? (
        <p className="py-24 text-center text-sm text-muted">
          지금 조건에 해당하는 방송이 없습니다.
        </p>
      ) : (
        <div className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
          {isSmall
            ? <SmallStreamerView data={data} />
            : <NewStreamerView data={data} onRanking={onRanking} />}
        </div>
      )}
    </div>
  );
}

// ── Tab 1: 신규 스트리머 ─────────────────────────────────────────────────────
function NewStreamerView({ data, onRanking }: { data: RisingNewcomers; onRanking: () => void }) {
  const sm = data.summary;
  const ins = data.insights;
  const top = data.streamers.slice(0, 10);
  const maxViewers  = Math.max(1, ...data.streamers.map((s) => s.concurrent_viewers));
  const maxFollower = Math.max(1, ...data.streamers.map((s) => s.follower_count));

  return (
    <div className="space-y-5">
      {/* Row 1 — KPI 4카드 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <NcTile label="현재 라이브 신입" value={nf(sm?.count ?? 0)} unit="채널" />
        <NcTile label="신입 총 시청자" value={nf(sm?.total_viewers ?? 0)} unit="명" />
        {/* 정확한 첫 방송일이 있는 채널만으로 낸 평균 — 표본이 없으면 '-' */}
        <NcTile label="신입 평균 방송 경력"
                value={sm?.avg_debut_days != null ? `${nf(sm.avg_debut_days)}` : "-"}
                unit={sm?.avg_debut_days != null ? "일차" : undefined} accent />
        <NcTile label="신입 최고 동접" value={nf(sm?.peak_viewers ?? 0)} unit="명" />
      </div>

      {/* 아래 섹션은 전체 스트리머 분석 탭과 같이 전부 '풀 폭 카드 스택'이다.
          예전에는 6:4 / 5:5 2열로 나눴는데, 좌우 카드의 자연 높이가 크게 달라(히트맵은
          24칸 한 줄, 블루오션은 카드 그리드) 짧은 쪽 아래에 큰 빈 공간이 남았다.
          높이를 억지로 맞추면 그만큼이 여백으로 남을 뿐이라, 폭을 다 쓰는 쪽으로 바꿨다. */}

      {/* 24시간 노출 골든타임 — 24칸 히트맵이라 폭이 넓을수록 읽기 쉽다 */}
      {ins?.hourly && ins.hourly.length > 0
        ? <GoldenHourHeatmap hourly={ins.hourly} />
        : <div className="card"><h3 className="section-title">24시간 신입 노출 골든타임 분석</h3>
            <p className="py-8 text-center text-sm text-muted">시간대 데이터가 아직 부족합니다.</p></div>}

      {/* 신입 평균 체급 기준선 + 체급 구간 분포 */}
      <BaselineCard ins={ins} label="신입" />

      {/* 블루오션 카테고리 TOP 5 — 5칸을 한 줄로 */}
      <BlueOceanCards cats={data.categories ?? []} summary={sm} label="신입" />

      {/* 카테고리 점유율 — 도넛 5 : 테이블 7 */}
      <NewcomerCategoryDonut cats={data.categories ?? []} label="신입" />

      {/* Row 4 — 신입 라이징 TOP 10 */}
      <div className="card">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
          <h3 className="section-title flex items-center gap-1.5">
            <Sprout size={16} style={{ color: GREEN }} /> 신입 TOP 10
          </h3>
          <button onClick={onRanking} className="text-xs font-medium hover:underline" style={{ color: GREEN }}>
            전체 순위 보기 →
          </button>
        </div>
        <p className="mb-4 text-xs text-muted">성장률(현재 vs 최근 7일 평균) 상위</p>
        {top.length > 0
          ? <NewcomerTable items={top} maxViewers={maxViewers} maxFollower={maxFollower} />
          : <p className="py-4 text-center text-sm text-muted">
              첫 방송 60일 이내 조건에 맞는 라이브 방송이 아직 없습니다.
            </p>}
      </div>
    </div>
  );
}

// ── Tab 2: 소형(하꼬) 스트리머 ───────────────────────────────────────────────
function SmallStreamerView({ data }: { data: RisingNewcomers }) {
  const sm = data.summary;
  const ins = data.insights;

  return (
    <div className="space-y-5">
      {/* Row 1 — KPI 4카드 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <NcTile label="현재 라이브 소형 채널" value={nf(sm?.count ?? 0)} unit="채널" />
        <NcTile label="총 시청자" value={nf(sm?.total_viewers ?? 0)} unit="명" />
        <NcTile label="소형 평균 시청자" value={nf(sm?.avg_viewers ?? 0)} unit="명" accent />
        <NcTile label="시청자 3명 초과 비중" value={`${sm?.over3_share ?? 0}`} unit="%" />
      </div>

      {/* 신규 탭과 같은 이유로 풀 폭 스택 — 24칸 시간대 차트 옆에 짧은 카드를 붙이면
          그 아래가 통째로 빈다. 카테고리/성장률만 자연 높이가 비슷해 2열로 남겨 둔다. */}

      {/* 대기업 방종 빈집 타임 — 24칸 이중 막대라 폭이 넓을수록 읽기 쉽다 */}
      {ins?.vacancy_hourly && ins.vacancy_hourly.length > 0
        ? <VacancyHours hourly={ins.vacancy_hourly} best={ins.vacancy_best} />
        : <div className="card"><h3 className="section-title">대기업 방종 빈집 타임</h3>
            <p className="py-8 text-center text-sm text-muted">
              빈집 타임 분석용 데이터가 아직 부족합니다.
            </p></div>}

      {/* 방송 제목 키워드 유입 효율 — 3칸 비교 타일을 한 줄로 */}
      {ins?.title_keyword
        ? <TitleKeywordCard tk={ins.title_keyword} label="소형" />
        : <div className="card"><h3 className="section-title">방송 제목 키워드 유입 효율</h3>
            <p className="py-8 text-center text-sm text-muted">
              키워드 포함/미포함 그룹이 각각 5개 이상 모이면 표시됩니다.
            </p></div>}

      {/* 카테고리 TOP 5 / 성장률 상위 소형 채널 — 둘 다 5~8행 리스트라 높이가 비슷하다 */}
      <div className="grid grid-cols-1 items-stretch gap-5 lg:grid-cols-2">
        <SmallCategoryTop10 cats={data.categories ?? []} />
        <SmallGrowthList items={data.streamers} />
      </div>
    </div>
  );
}

// 신규 스트리머 랭킹 = 인라인 프로그레스 바 테이블 (top100)
type NcSort = "growth" | "duration" | "viewers";
function NewcomersRankingTab({ data }: { data: RisingNewcomers }) {
  const [sort, setSort] = useState<NcSort>("growth");
  const [limit, setLimit] = useState(40);
  const enriched = useMemo(() =>
    data.streamers.map((s) => ({ ...s, dur: liveDuration(s.open_date) })), [data]);
  const sorted = useMemo(() => {
    const a = [...enriched];
    if (sort === "duration")     a.sort((x, y) => y.dur.ms - x.dur.ms);
    else if (sort === "viewers") a.sort((x, y) => y.concurrent_viewers - x.concurrent_viewers);
    else a.sort((x, y) => (y.growth_rate ?? -1e9) - (x.growth_rate ?? -1e9));
    return a;
  }, [enriched, sort]);

  const maxViewers  = useMemo(() => Math.max(1, ...enriched.map((s) => s.concurrent_viewers)), [enriched]);
  const maxFollower = useMemo(() => Math.max(1, ...enriched.map((s) => s.follower_count)), [enriched]);

  const SortBtn = ({ k, label }: { k: NcSort; label: string }) => {
    const active = sort === k;
    return (
      <button onClick={() => setSort(k)}
        className="text-xs font-medium px-3 py-1.5 rounded-lg border transition-colors"
        style={{ background: active ? "rgba(0,255,163,0.1)" : "transparent",
                 borderColor: active ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                 color: active ? GREEN : "rgb(var(--color-muted-rgb))" }}>
        {label}
      </button>
    );
  };

  return (
    <div className="space-y-5">
      {/* 랭킹 요약 차트 — 신규는 팔로워 증가량(follower_prev24h)이 없어 Y축을 성장률로 쓴다 */}
      <RankingCharts
        rows={sorted.map((s) => ({ ...s, deltaPct: s.growth_rate, yValue: s.growth_rate }))}
        y={{ label: "성장률", unit: "%", log: false, tooltip: "성장률" }}
        deltaName="성장률" />

      <div className="card !p-4 md:!p-5">
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <SortBtn k="growth" label="급성장순" />
          <SortBtn k="duration" label="방송 시간" />
          <SortBtn k="viewers" label="시청자 순" />
        </div>

        {sorted.length === 0
          ? <p className="text-sm text-muted text-center py-8 leading-relaxed">
              조건에 맞는 신규/라이징 방송이 아직 없습니다.<br />
              <span className="text-xs">
                첫 방송 60일 이내(신규) 또는 최근 평균 시청자 10명 이하(소형) 조건으로
                지금 방송 중인 채널만 모읍니다. 시간대에 따라 비어 있을 수 있습니다.
              </span>
            </p>
          : <NewcomerTable items={sorted.slice(0, limit)} maxViewers={maxViewers} maxFollower={maxFollower} />}
        {sorted.length > limit && (
          <div className="text-center pt-4">
            <button onClick={() => setLimit((l) => l + 40)} className="btn-secondary text-sm">더 보기 ({nf(sorted.length - limit)}개 남음)</button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── 카테고리 탭 ───────────────────────────────────────────────────────────────
// 선택한 카테고리로 방송 중인 스트리머 목록 — 랭킹 테이블과 동일한 디자인 규격.
// 별도 API 없이 이미 받아둔 라이브 랭킹 스냅샷을 카테고리로 필터링한다.
// ── 카테고리별 스트리머 (탐색형) ─────────────────────────────────────────────
// 카테고리 목록은 categories(range=live, 현재 스냅샷)로 카드 그리드를 그리고,
// 카테고리를 고르면 전용 엔드포인트로 '그 카테고리 전체'를 다시 받아온다.
// (기존엔 liveRanking(200)을 프론트에서 필터링해 저시청자 방송이 통째로 빠졌다.)
const CAT_SORT_OPTS: { k: "viewers" | "lives"; label: string }[] = [
  { k: "viewers", label: "시청자순" },
  { k: "lives",   label: "방송수순" },
];

// 카테고리 대표 이미지 데이터가 없어서(수집 대상 아님) 카테고리명을 해시해 만든
// 결정적 그라데이션 타일을 썸네일 자리에 쓴다 — 같은 카테고리는 항상 같은 색이 된다.
// 카테고리 카드 — 매트 다크 단색 패널 + 테두리에만 네온 그라데이션 모션.
// 카드 안쪽에는 어떤 그라데이션/이미지 오버레이도 깔지 않는다(내부는 완전 단색).
// 흐르는 테두리는 globals.css의 .nb-neon-border(conic-gradient + mask XOR)가 담당하고,
// TOP 1~3는 .nb-podium으로 평상시에도 은은하게 고정 노출된다.
// 패널 색은 블루오션 카드와 공유한다(./cardStyle) — 두 카드가 같은 룩이어야 한다.

// TOP 1~3 정적 테두리 색(골드/실버/브론즈), 4위 이하는 차분한 다크
const CARD_RINGS = [
  "rgba(251,191,36,0.45)",
  "rgba(209,213,219,0.35)",
  "rgba(217,119,6,0.35)",
] as const;
const RANK_TEXT = ["#FBBF24", "#D1D5DB", "#D97706"] as const;

function CategoryCard({ c, rank, onPick }: { c: RisingCategory; rank: number; onPick: () => void }) {
  const podium = rank < 3;
  return (
    <button onClick={onPick} type="button"
      className={`nb-neon-border${podium ? " nb-podium" : ""} group rounded-xl border p-3.5 text-left
                  transition-colors`}
      style={{ background: CARD_DARK, borderColor: CARD_RINGS[rank] ?? CARD_BORDER }}>
      {/* 헤더: 순위 + 방송 수 글래스 뱃지 */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-extrabold tabular-nums"
              style={{ color: RANK_TEXT[rank] ?? "#6B7280" }}>#{rank + 1}</span>
        <span className="ml-auto rounded-full border px-2.5 py-1 text-[11px] font-bold backdrop-blur-md"
              style={{ background: "rgba(0,0,0,0.5)", color: GREEN, borderColor: "rgba(0,255,163,0.20)" }}>
          {nf(c.lives)}개 방송
        </span>
      </div>

      {/* 타이포 계층: 카테고리명 > 시청자 수 > 서브 설명 */}
      <h4 className="mt-2.5 truncate text-base font-bold text-white transition-colors group-hover:text-accent"
          title={c.category}>
        {c.category}
      </h4>
      <p className="mt-1 tracking-tight">
        <span className="text-xl font-extrabold tabular-nums text-white">{nf(c.viewers)}</span>
        <span className="ml-1 text-xs" style={{ color: "#9CA3AF" }}>명</span>
      </p>
      <p className="mt-0.5 text-[11px]" style={{ color: "#9CA3AF" }}>
        {nf(c.lives)}개 채널 · 방송당 {nf(c.avg_viewers)}명
      </p>
    </button>
  );
}

function CategoryStreamerList({ category, onPick }:
  { category: string | null; onPick: (c: string | null) => void }) {
  const [cats, setCats] = useState<RisingCategory[]>([]);
  const [catsLoading, setCatsLoading] = useState(true);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"viewers" | "lives">("viewers");

  const [data, setData] = useState<RisingCategoryStreamers | null>(null);
  const [listLoading, setListLoading] = useState(false);

  // 카드 그리드용 — 현재 스냅샷 기준이라 '지금 방송 중'인 카테고리만 나온다
  useEffect(() => {
    let alive = true;
    setCatsLoading(true);
    api.rising.categories("live", 200)
      .then((d) => { if (alive) setCats(d.categories || []); })
      .catch(() => { if (alive) setCats([]); })
      .finally(() => { if (alive) setCatsLoading(false); });
    return () => { alive = false; };
  }, []);

  // 선택된 카테고리의 스트리머 전체 (시청자 0명 포함, 팔로워 보강)
  useEffect(() => {
    if (!category) { setData(null); return; }
    let alive = true;
    setListLoading(true);
    api.rising.categoryStreamers(category)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setListLoading(false); });
    return () => { alive = false; };
  }, [category]);

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const list = kw ? cats.filter((c) => c.category.toLowerCase().includes(kw)) : cats;
    return [...list].sort((a, b) => (sort === "viewers" ? b.viewers - a.viewers : b.lives - a.lives));
  }, [cats, q, sort]);

  // 퀵 태그는 시청자 상위 8개
  const chips = useMemo(() => [...cats].sort((a, b) => b.viewers - a.viewers).slice(0, 8), [cats]);

  const items = useMemo(
    () => (data?.streamers ?? []).map((s) => ({ ...s, dur: liveDuration(s.open_date) })),
    [data],
  );
  const maxViewers  = Math.max(1, ...items.map((s) => s.concurrent_viewers));
  const maxFollower = Math.max(1, ...items.map((s) => s.follower_count));
  const picked = category ? cats.find((c) => c.category === category) : undefined;

  return (
    <div className="space-y-5">
      <div className="card !p-4 md:!p-5">
        <h3 className="section-title mb-1">카테고리별 스트리머</h3>
        <p className="text-xs text-muted mb-4">
          현재 치지직에서 인기 있는 카테고리를 선택하여 방송 중인 스트리머를 확인하세요.
        </p>

        {/* 인기 카테고리 퀵 태그 */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => onPick(null)}
            className="rounded-full border px-3 py-1 text-xs font-medium transition-colors"
            style={{ background: !category ? "rgba(0,255,163,0.1)" : "transparent",
                     borderColor: !category ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                     color: !category ? GREEN : "rgb(var(--color-muted-rgb))" }}>
            전체
          </button>
          {chips.map((c) => {
            const active = category === c.category;
            return (
              <button key={c.category} type="button" onClick={() => onPick(c.category)}
                className="max-w-[190px] truncate rounded-full border px-3 py-1 text-xs font-medium transition-colors"
                style={{ background: active ? "rgba(0,255,163,0.1)" : "transparent",
                         borderColor: active ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                         color: active ? GREEN : "rgb(var(--color-muted-rgb))" }}>
                {c.category}
              </button>
            );
          })}
        </div>

        {/* 검색 + 정렬 */}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="relative flex-1 min-w-[180px] max-w-xs">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="카테고리 검색"
              className="w-full rounded-lg border border-border bg-bg py-2 pl-9 pr-3 text-sm text-fg
                         placeholder-muted focus:border-accent focus:outline-none" />
          </div>
          <Seg options={CAT_SORT_OPTS} value={sort} onChange={setSort} />
        </div>

        {/* 선택 상태 요약 */}
        {category && (
          <div className="mt-4 flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold"
                  style={{ borderColor: "rgba(0,255,163,0.4)", background: "rgba(0,255,163,0.08)", color: GREEN }}>
              선택됨: {category}
              {picked && <span className="font-normal text-muted">({nf(picked.lives)}개 방송)</span>}
            </span>
            <button type="button" onClick={() => onPick(null)}
              className="inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-fg transition-colors">
              <X size={12} /> 초기화
            </button>
          </div>
        )}
      </div>

      {/* 미선택 — 인기 카테고리 카드 그리드 */}
      {!category && (
        <div className="card !p-4 md:!p-5">
          {catsLoading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-muted">
              <Loader2 size={18} className="animate-spin" /> 카테고리를 불러오는 중...
            </div>
          ) : filtered.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted">
              {q.trim() ? "검색 결과가 없습니다." : "현재 방송 중인 카테고리가 없습니다."}
            </p>
          ) : (
            <>
              <p className="mb-3 text-[11px] text-muted">
                현재 방송 중인 카테고리 {nf(filtered.length)}개 · {sort === "viewers" ? "시청자순" : "방송수순"}
              </p>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-5">
                {filtered.map((c, i) => (
                  <CategoryCard key={c.category} c={c} rank={i} onPick={() => onPick(c.category)} />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* 선택됨 — 스트리머 목록 */}
      {category && (
        <div className="card !p-4 md:!p-5">
          {listLoading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-muted">
              <Loader2 size={18} className="animate-spin" /> 스트리머를 불러오는 중...
            </div>
          ) : items.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted">
              이 카테고리로 방송 중인 스트리머가 없습니다.
            </p>
          ) : (
            <>
              <p className="mb-3 text-[11px] text-muted">
                시청자 {nf(items.length)}개 방송 · 시청자 내림차순 · 시청자 0명 방송도 모두 포함
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[820px]">
                  <thead>
                    <tr className="border-b border-border text-xs text-muted">
                      <th className="w-12 py-2 pl-2 text-left font-medium">#</th>
                      <th className="py-2 text-left font-medium">스트리머</th>
                      <th className="py-2 px-6 text-left font-medium hidden lg:table-cell">방송 제목</th>
                      <th className="py-2 px-6 text-right font-medium">전체 시청자</th>
                      <th className="py-2 px-6 text-right font-medium hidden md:table-cell">방송시간</th>
                      <th className="py-2 px-6 text-right font-medium">팔로워</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((s, i) => {
                      const vwPct  = (s.concurrent_viewers / maxViewers) * 100;
                      const durPct = s.dur.ms > 0 ? Math.min(100, (s.dur.ms / DAY_MS) * 100) : 0;
                      const folPct = s.follower_count > 0 ? (s.follower_count / maxFollower) * 100 : 0;
                      const medal  = MEDALS[i];
                      const vwDelta = s.viewers_prev && s.viewers_prev > 0
                        ? ((s.concurrent_viewers - s.viewers_prev) / s.viewers_prev) * 100 : null;
                      return (
                        <tr key={s.chzzk_channel_id} className="border-b border-border transition-colors hover:bg-bg-hover/70">
                          <td className="py-3.5 pl-2 align-top text-sm tabular-nums">
                            {medal
                              ? <span className="font-extrabold" style={{ color: medal.color }}>#{i + 1}</span>
                              : <span className="text-muted">{i + 1}</span>}
                          </td>
                          <td className="py-3.5 align-top">
                            <Link href={`/stats/streamer/${s.chzzk_channel_id}`} className="group flex items-center gap-2">
                              <StreamerAvatar src={s.channel_image_url} index={i}
                                      ringStyle={medal ? { boxShadow: `0 0 0 2px ${medal.color}, 0 0 8px ${medal.color}66` } : undefined} />
                              <ChzzkMark />
                              <span className="truncate max-w-[150px] text-base font-semibold text-fg transition-colors group-hover:text-accent md:max-w-none">
                                {s.channel_name}
                              </span>
                            </Link>
                          </td>
                          <td className="py-3.5 px-6 align-middle hidden lg:table-cell">
                            <span className="block max-w-[280px] truncate text-xs text-muted" title={s.live_title}>
                              {s.live_title || "-"}
                            </span>
                          </td>
                          <td className="py-3.5 px-6 align-top" style={{ minWidth: 140 }}>
                            <CellCol>
                              <div className="flex items-center justify-end gap-1.5">
                                {vwDelta !== null && <span className="text-[11px]"><Delta pct={vwDelta} /></span>}
                                <StatNum value={s.concurrent_viewers} unit="명" />
                              </div>
                              <CellBar pct={vwPct} background={YELLOW_GRAD} />
                            </CellCol>
                          </td>
                          <td className="py-3.5 px-6 align-top hidden md:table-cell" style={{ minWidth: 128 }}>
                            <CellCol>
                              <div className="text-right text-sm tabular-nums text-muted">{s.dur.label}</div>
                              <CellBar pct={durPct} background={PURPLE_GRAD} />
                            </CellCol>
                          </td>
                          <td className="py-3.5 px-6 align-top" style={{ minWidth: 140 }}>
                            <CellCol>
                              <div className="text-right">
                                {s.follower_count > 0
                                  ? <StatNum value={s.follower_count} unit="명" />
                                  : <span className="text-sm text-muted">-</span>}
                              </div>
                              <CellBar pct={folPct} background={CYAN_GRAD} />
                            </CellCol>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── 누적(기간) 랭킹 탭 ───────────────────────────────────────────────────────
// 실시간 랭킹과 성격이 다르다: 최신 스냅샷 한 장이 아니라 기간 전체를 집계해
// 시청 시간·방송 시간까지 줄 세운다. 자체적으로 기간/정렬을 바꿔 재조회한다.
const PERIOD_RANGE_OPTS: { k: PeriodRange; label: string }[] = [
  { k: "24h", label: "최근 24시간" },
  { k: "7d",  label: "최근 7일" },
];
const PERIOD_SORT_OPTS: { k: PeriodSort; label: string; unit: string; help: string }[] = [
  { k: "viewership",      label: "시청 시간",   unit: "시간", help: "동시 시청자 × 방송 시간의 누적값(hours watched). 규모와 지속성을 함께 반영합니다." },
  { k: "avg_viewers",     label: "평균 시청자", unit: "명",   help: "기간 내 스냅샷 평균. 방송을 짧게 해도 시청자가 많으면 높습니다." },
  { k: "peak_viewers",    label: "최고 동접",   unit: "명",   help: "기간 내 최고 동시 시청자." },
  { k: "broadcast_hours", label: "방송 시간",   unit: "시간", help: "수집된 스냅샷으로 추정한 총 송출 시간. 꾸준함을 봅니다." },
];

// ── 소형 스트리머 랭킹 ───────────────────────────────────────────────────────
// **소형 스트리머 통계와 다른 화면이다.** 통계는 성장률·요약·인사이트를 보여 주고
// 랭킹 제외를 적용하지 않는다. 이쪽은 순위라서 (1) 동시 시청자 내림차순이고
// (2) 공식 그룹 제외가 적용된다. 두 화면이 같은 사람을 다르게 다루는 것이 정상이며,
// 화면에도 그 이유를 적어 둔다.
type SmallSort = "viewers" | "avg" | "duration";
const SMALL_SORTS: { k: SmallSort; label: string }[] = [
  { k: "viewers",  label: "현재 시청자" },
  { k: "avg",      label: "7일 평균" },
  { k: "duration", label: "방송 시간" },
];

function SmallRankingTab() {
  const [data, setData] = useState<RisingSmallRanking | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [sort, setSort] = useState<SmallSort>("viewers");
  const [limit, setLimit] = useState(50);

  useEffect(() => {
    let alive = true;
    setLoading(true); setErr(null);
    api.rising.smallRanking(200)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => {
        if (alive) setErr(e instanceof Error ? e.message : "랭킹을 불러오지 못했습니다.");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const rows = useMemo(() => {
    const a = (data?.streamers ?? []).map((s) => ({ ...s, dur: liveDuration(s.open_date) }));
    if (sort === "avg") a.sort((x, y) => y.avg_viewers - x.avg_viewers);
    else if (sort === "duration") a.sort((x, y) => y.dur.ms - x.dur.ms);
    else a.sort((x, y) => y.concurrent_viewers - x.concurrent_viewers);
    return a;
  }, [data, sort]);

  const cap = data?.criteria?.small_avg_max ?? 10;
  const win = data?.criteria?.window_days ?? 7;
  // 시청자 수가 한 자릿수라 '1위 대비 비율' 막대는 의미가 없다(2명이 5명의 40%로
  // 보이는 식). 대신 기준값(cap) 대비로 그려 "소형 구간 안 어디쯤"을 읽게 한다.
  const maxFollower = useMemo(
    () => Math.max(1, ...rows.map((s) => s.follower_count)), [rows]);

  return (
    <div className="space-y-5">
      <div className="min-w-0 max-w-2xl">
        <h2 className="flex items-center gap-2 text-xl font-extrabold tracking-tight md:text-2xl">
          <Leaf size={20} style={{ color: GREEN }} /> 소형 스트리머 랭킹
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          최근 {win}일 평균 동시 시청자가 {cap}명 이하인 채널 중 지금 방송 중인 채널을
          현재 시청자 순으로 보여 줍니다. 방송 경력은 보지 않습니다.
        </p>
        <p className="mt-1 text-xs leading-relaxed text-muted/70">
          소형 스트리머 <b>통계</b>와 기준은 같지만, 랭킹에서는 운영진이 지정한
          공식 그룹 채널이 제외됩니다.
        </p>
      </div>

      {err ? (
        <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-6">
          <p className="text-sm font-semibold text-red-400">랭킹을 불러오지 못했습니다.</p>
          <p className="mt-1 text-xs text-muted">{err}</p>
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center gap-2 py-24 text-muted" aria-busy>
          <Loader2 size={18} className="animate-spin" /> 불러오는 중...
        </div>
      ) : rows.length === 0 ? (
        <p className="py-24 text-center text-sm text-muted">
          지금 방송 중인 소형 스트리머가 없습니다.
        </p>
      ) : (
        <div className="card !p-4 md:!p-5">
          {/* 터치에서는 시각 크기를 키우지 않고 히트 영역만 44px로 넓힌다(UI-S 계약).
              `nb-tap-gap`은 넓어진 히트 영역끼리 겹치지 않도록 간격을 함께 벌린다. */}
          <div className="nb-tap-gap mb-4 flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs text-muted">정렬 기준</span>
            {SMALL_SORTS.map((o) => {
              const active = sort === o.k;
              return (
                <button key={o.k} onClick={() => setSort(o.k)} aria-pressed={active}
                  className="nb-tap inline-flex items-center justify-center rounded-lg border
                             px-3 py-1.5 text-xs font-medium transition-colors"
                  style={{ background: active ? "rgba(0,255,163,0.1)" : "transparent",
                           borderColor: active ? "rgba(0,255,163,0.35)"
                             : "rgb(var(--color-border-rgb))",
                           color: active ? GREEN : "rgb(var(--color-muted-rgb))" }}>
                  {o.label}
                </button>
              );
            })}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted">
                  <th className="w-12 py-2 pl-2 text-left font-medium">#</th>
                  <th className="py-2 text-left font-medium">스트리머</th>
                  <th className="hidden px-6 py-2 text-left font-medium sm:table-cell">카테고리</th>
                  <th className="px-6 py-2 text-right font-medium">현재 시청자</th>
                  <th className="px-6 py-2 text-right font-medium">{win}일 평균</th>
                  <th className="hidden px-6 py-2 text-right font-medium md:table-cell">방송시간</th>
                  <th className="px-6 py-2 text-right font-medium">팔로워</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, limit).map((s, i) => {
                  const medal = MEDALS[i];
                  const durPct = s.dur.ms > 0 ? Math.min(100, (s.dur.ms / DAY_MS) * 100) : 0;
                  const folPct = s.follower_count > 0
                    ? (s.follower_count / maxFollower) * 100 : 0;
                  const avgPct = Math.min(100, (s.avg_viewers / Math.max(1, cap)) * 100);
                  return (
                    <tr key={s.chzzk_channel_id}
                        className="border-b border-border transition-colors hover:bg-bg-hover/70">
                      <td className="py-3.5 pl-2 align-middle text-sm tabular-nums">
                        {medal
                          ? <span className="font-extrabold" style={{ color: medal.color }}>#{i + 1}</span>
                          : <span className="text-muted">{i + 1}</span>}
                      </td>
                      <td className="py-3.5 align-middle">
                        <Link href={`/stats/streamer/${s.chzzk_channel_id}`}
                              className="group flex items-center gap-2">
                          <StreamerAvatar src={s.channel_image_url} index={i}
                            ringStyle={medal ? { boxShadow: `0 0 0 2px ${medal.color}, 0 0 8px ${medal.color}66` } : undefined} />
                          <ChzzkMark />
                          <span className="max-w-[150px] truncate text-base font-semibold text-fg transition-colors group-hover:text-accent md:max-w-none">
                            {s.channel_name}
                          </span>
                          <StreamerTagList tags={s.team_tags} />
                        </Link>
                      </td>
                      <td className="hidden px-6 py-3.5 align-middle sm:table-cell">
                        {s.category_name
                          ? <span className="inline-block max-w-[150px] truncate rounded-full border border-border bg-bg-hover px-3 py-1 text-xs font-medium text-fg">{s.category_name}</span>
                          : <span className="text-sm text-muted">-</span>}
                      </td>
                      <td className="px-6 py-3.5 align-middle" style={{ minWidth: 120 }}>
                        <div className="text-right"><StatNum value={s.concurrent_viewers} unit="명" /></div>
                      </td>
                      <td className="px-6 py-3.5 align-middle" style={{ minWidth: 130 }}>
                        <CellCol>
                          <div className="text-right text-sm tabular-nums text-muted">
                            {s.avg_viewers.toFixed(1)}명
                          </div>
                          {/* 기준값 대비 — 소형 구간(0~{cap}명) 안 어디쯤인지 */}
                          <CellBar pct={avgPct} background={YELLOW_GRAD} />
                        </CellCol>
                      </td>
                      <td className="hidden px-6 py-3.5 align-middle md:table-cell" style={{ minWidth: 128 }}>
                        <CellCol>
                          <div className="text-right text-sm tabular-nums text-muted">{s.dur.label}</div>
                          <CellBar pct={durPct} background={PURPLE_GRAD} />
                        </CellCol>
                      </td>
                      <td className="px-6 py-3.5 align-middle" style={{ minWidth: 130 }}>
                        <CellCol>
                          <div className="text-right">
                            {s.follower_count > 0
                              ? <StatNum value={s.follower_count} unit="명" />
                              : <span className="text-sm text-muted">-</span>}
                          </div>
                          <CellBar pct={folPct} background={CYAN_GRAD} />
                        </CellCol>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {rows.length > limit && (
            <div className="pt-4 text-center">
              <button onClick={() => setLimit((l) => l + 50)} className="btn-secondary text-sm">
                더 보기 ({nf(rows.length - limit)}개 남음)
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── 봉누도 (준비 중) ─────────────────────────────────────────────────────────
// **데이터 API를 부르지 않는다.** 빈 화면이나 오류처럼 보이지 않도록 무엇을 준비
// 중인지 적고, 지금 볼 수 있는 곳으로 안내한다. 서비스가 이미 시작된 것처럼
// 보이게 하는 수치·순위·표는 두지 않는다.
function BongnudoTab() {
  return (
    <div className="space-y-5">
      <div className="min-w-0 max-w-2xl">
        <h2 className="flex items-center gap-2 text-xl font-extrabold tracking-tight md:text-2xl">
          <Sparkles size={20} className="text-muted" /> 봉누도
          <span className="rounded border border-border px-1.5 py-0.5 text-[10px] font-bold text-muted">
            준비 중
          </span>
        </h2>
      </div>
      <div className="card !p-6 text-center md:!p-10">
        <Sparkles size={36} className="mx-auto mb-3 text-muted opacity-40" aria-hidden="true" />
        <p className="font-medium text-fg">아직 공개하지 않은 메뉴입니다.</p>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">
          봉누도 관련 집계는 준비 중이며, 아직 수집하거나 표시하는 데이터가 없습니다.
          공개되면 이 메뉴에서 바로 확인하실 수 있습니다.
        </p>
        <p className="mt-4 text-xs text-muted/70">
          그동안은 왼쪽 메뉴의 스트리머 통계와 랭킹을 이용해 주세요.
        </p>
      </div>
    </div>
  );
}

function PeriodRankingTab() {
  const [range, setRange] = useState<PeriodRange>("24h");
  const [sort, setSort]   = useState<PeriodSort>("viewership");
  const [data, setData]   = useState<RisingPeriodRanking | null>(null);
  const [loading, setLoading] = useState(true);
  const [limit, setLimit] = useState(50);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.rising.rankingPeriod(range, sort, 100)
      .then((d) => { if (alive) { setData(d); setLimit(50); } })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [range, sort]);

  const rows = data?.streamers ?? [];
  const sortOpt = PERIOD_SORT_OPTS.find((o) => o.k === sort)!;
  const maxMetric = Math.max(1, ...rows.map((r) => r[sort] as number));

  return (
    <div className="space-y-5">
      {/* 요약 차트 — 누적 지표에 맞춰 매핑한다.
          PeriodStreamer에는 open_date/viewers_prev가 없어 방송시간은 broadcast_hours로
          환산하고, 막대 변동률은 표시하지 않는다. */}
      <RankingCharts
        rows={rows.map((s) => ({
          chzzk_channel_id: s.chzzk_channel_id,
          channel_name: s.channel_name,
          channel_image_url: s.channel_image_url,
          concurrent_viewers: s.avg_viewers,      // 체급 = 기간 평균 시청자
          follower_count: s.follower_count,
          category_name: s.category_name,
          dur: { ms: s.broadcast_hours * 3600 * 1000, label: `${s.broadcast_hours}시간` },
          deltaPct: null,
          yValue: s.viewership,                   // 유입 대신 누적 시청 시간
        }))}
        y={{ label: "시청 시간", unit: "시간", log: true, tooltip: "시청 시간" }} />

    <div className="card !p-4 md:!p-5">
      <div className="flex items-start justify-between gap-3 mb-1 flex-wrap">
        <h3 className="section-title">기간별 누적 랭킹</h3>
        <Seg options={PERIOD_RANGE_OPTS} value={range} onChange={setRange} />
      </div>
      <p className="text-xs text-muted mb-4">
        실시간 랭킹은 &lsquo;지금 이 순간&rsquo;의 동시 시청자 순위라 잠깐 스파이크가 뜬 방송이 위로 올라옵니다.
        이 표는 선택한 기간 전체를 누적해 정렬하므로 꾸준히 방송한 채널이 드러납니다.
        {data && data.history_hours > 0 && <> (이 기간 수집 이력 {data.history_hours}시간)</>}
      </p>

      <div className="mb-4 flex items-center gap-2 flex-wrap">
        <span className="text-xs text-muted mr-1">정렬 기준</span>
        {PERIOD_SORT_OPTS.map((o) => {
          const active = sort === o.k;
          return (
            <button key={o.k} onClick={() => setSort(o.k)}
              className="text-sm font-medium px-3.5 py-1.5 rounded-lg border transition-colors"
              style={{ background: active ? "rgba(0,255,163,0.1)" : "transparent",
                       borderColor: active ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                       color: active ? GREEN : "rgb(var(--color-muted-rgb))" }}>
              {o.label}
            </button>
          );
        })}
        <span className="inline-flex items-center"><HelpTip>
          <b className="block text-fg mb-1">{sortOpt.label}</b>
          {sortOpt.help}
        </HelpTip></span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 text-muted py-16">
          <Loader2 size={18} className="animate-spin" /> 집계 중...
        </div>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted text-center py-10">
          아직 누적 집계에 쓸 데이터가 부족합니다. 수집이 쌓이면 표시됩니다.
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[760px]">
              <thead>
                <tr className="text-muted text-xs border-b border-border">
                  <th className="text-left font-medium py-2 pl-2 w-12">#</th>
                  <th className="text-left font-medium py-2">스트리머</th>
                  <th className="text-left font-medium py-2 px-6 hidden sm:table-cell">카테고리</th>
                  <th className="text-right font-medium py-2 px-6">{sortOpt.label}</th>
                  <th className="text-right font-medium py-2 px-6 hidden md:table-cell">평균 시청자</th>
                  <th className="text-right font-medium py-2 px-6 hidden lg:table-cell">최고 동접</th>
                  <th className="text-right font-medium py-2 px-6 hidden md:table-cell">방송 시간</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, limit).map((s, i) => {
                  const medal = MEDALS[i];
                  const metric = s[sort] as number;
                  return (
                    <tr key={s.chzzk_channel_id} className="border-b border-border hover:bg-bg-hover/70 transition-colors">
                      <td className="py-3.5 pl-2 tabular-nums text-sm align-top">
                        {medal
                          ? <span className="font-extrabold" style={{ color: medal.color }}>#{i + 1}</span>
                          : <span className="text-muted">{i + 1}</span>}
                      </td>
                      <td className="py-3.5 align-top">
                        <Link href={`/stats/streamer/${s.chzzk_channel_id}`} className="flex items-center gap-2 group">
                          <StreamerAvatar src={s.channel_image_url} index={i}
                                      ringStyle={medal ? { boxShadow: `0 0 0 2px ${medal.color}, 0 0 8px ${medal.color}66` } : undefined} />
                          <ChzzkMark />
                          <span className="text-base font-semibold text-fg group-hover:text-accent transition-colors truncate max-w-[150px] md:max-w-none">
                            {s.channel_name || "(이름 없음)"}
                          </span>
                          {/* 팀/소속 태그 — 랭킹 목록과 같은 규칙(2개 + +N) */}
                          <StreamerTagList tags={s.team_tags} />
                        </Link>
                      </td>
                      <td className="py-3.5 px-6 hidden sm:table-cell align-top">
                        {s.category_name
                          ? <span className="inline-block max-w-[150px] truncate rounded-full border border-border
                                             bg-bg-hover px-3 py-1 text-xs font-medium text-fg">{s.category_name}</span>
                          : <span className="text-muted text-sm">-</span>}
                      </td>
                      {/* 현재 정렬 기준 지표 — 1위 대비 비율 바를 함께 보여 준다 */}
                      <td className="py-3.5 px-6 align-top" style={{ minWidth: 140 }}>
                        <CellCol>
                          <div className="text-right"><StatNum value={metric} unit={sortOpt.unit} /></div>
                          <CellBar pct={(metric / maxMetric) * 100} background={YELLOW_GRAD} />
                        </CellCol>
                      </td>
                      <td className="py-3.5 px-6 text-right align-middle hidden md:table-cell tabular-nums text-muted text-sm">
                        {nf(s.avg_viewers)}명
                      </td>
                      <td className="py-3.5 px-6 text-right align-middle hidden lg:table-cell tabular-nums text-muted text-sm">
                        {nf(s.peak_viewers)}명
                      </td>
                      <td className="py-3.5 px-6 text-right align-middle hidden md:table-cell tabular-nums text-muted text-sm">
                        {s.broadcast_hours}시간
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {rows.length > limit && (
            <div className="text-center pt-4">
              <button onClick={() => setLimit((l) => l + 50)} className="btn-secondary text-sm">
                더 보기 ({nf(rows.length - limit)}개 남음)
              </button>
            </div>
          )}
        </>
      )}
    </div>
    </div>
  );
}

// 카테고리 분석 — 점유율 도넛 + 표(스트리머 랭킹과 동일 규격).
// 행을 누르면 표 아래에 붙이지 않고 '카테고리별 스트리머' 탭으로 넘겨 조회하게 한다
// (표가 길면 아래에 펼친 결과가 화면 밖으로 밀려 안 보이는 문제가 있었다).
function CategoryTab({ cats, onPick }: { cats: RisingCategories; onPick: (c: string) => void }) {
  const maxV = Math.max(1, ...cats.categories.map((c) => c.viewers));

  return (
    <div className="space-y-5">
      {/* 실시간 카테고리 랭킹 카드 (PODIUM 강조 + 가로 캐러셀) */}
      <div className="card !p-4 md:!p-5">
        <CategoryRankCards categories={cats.categories} />
      </div>

      {/* 전체/신규 분석 탭과 동일한 카테고리 점유율 도넛 */}
      <CategoryDonut />

      <div className="card !p-4 md:!p-5">
        <h3 className="section-title mb-1">카테고리(게임)별 현황</h3>
        <p className="text-xs text-muted mb-4">
          방송당 평균 = 시청자 ÷ 방송 수. 값이 높을수록 방송 대비 시청 유입(블루오션)이 큽니다.
          행을 클릭하면 '카테고리별 스트리머' 탭에서 해당 카테고리로 방송 중인 목록을 조회합니다.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[620px]">
            <thead>
              <tr className="text-muted text-xs border-b border-border">
                <th className="text-left font-medium py-2 pl-2 w-12">#</th>
                <th className="text-left font-medium py-2">카테고리</th>
                <th className="text-right font-medium py-2 px-6">시청자</th>
                <th className="text-right font-medium py-2 px-6 hidden sm:table-cell">방송 수</th>
                <th className="text-right font-medium py-2 px-6">방송당 평균</th>
              </tr>
            </thead>
            <tbody>
              {cats.categories.map((c, i) => {
                const medal = MEDALS[i];
                return (
                  <tr key={c.category}
                      onClick={() => onPick(c.category)}
                      className="border-b border-border hover:bg-bg-hover/70 transition-colors cursor-pointer">
                    <td className="py-3.5 pl-2 tabular-nums text-sm align-middle">
                      {medal
                        ? <span className="font-extrabold" style={{ color: medal.color }}>#{i + 1}</span>
                        : <span className="text-muted">{i + 1}</span>}
                    </td>
                    <td className="py-3.5 align-middle">
                      <span className="inline-block max-w-[180px] truncate rounded-full border border-border
                                       bg-bg-hover px-3 py-1 text-xs font-medium text-fg">{c.category}</span>
                    </td>
                    <td className="py-3.5 px-6 align-middle" style={{ minWidth: 140 }}>
                      <CellCol>
                        <div className="text-right"><StatNum value={c.viewers} unit="명" /></div>
                        <CellBar pct={(c.viewers / maxV) * 100} background={YELLOW_GRAD} />
                      </CellCol>
                    </td>
                    <td className="py-3.5 px-6 text-right align-middle hidden sm:table-cell">
                      <StatNum value={c.lives} unit="개" />
                    </td>
                    <td className="py-3.5 px-6 text-right align-middle">
                      <StatNum value={c.avg_viewers} unit="명" />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {cats.categories.length === 0 && (
          <p className="text-sm text-muted text-center py-6">카테고리 데이터가 아직 없습니다.</p>
        )}
      </div>

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
  // 그룹 접힘 상태는 StatsNav가 소유한다. 여기서는 활성 탭만 관리.
  const [tab, setTab] = useState<Tab>("overview");

  // 탭은 state로 전환되지만 ?tab= 으로 URL에도 남긴다.
  // 그래야 다른 페이지(예: /stats/singcup/live)에서 '랭킹 보기'로 돌아왔을 때
  // 기본 탭(전체 스트리머 분석)이 아니라 원래 보던 탭으로 복귀한다.
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("tab");
    // 옛 키(`newcomers_analysis`)로 공유된 링크도 살린다 — 그냥 버리면 조용히
    // 첫 탭으로 떨어져 사용자는 잘못된 주소를 받았다고 읽는다.
    const t = resolveTab(raw);
    if (!t) return;
    setTab(t);
    if (raw !== t) {
      // 주소도 새 키로 바꿔 둔다(다시 공유하면 최신 링크가 나가게).
      const url = new URL(window.location.href);
      url.searchParams.set("tab", t);
      window.history.replaceState(null, "", url.toString());
    }
  }, []);

  // 뒤로가기/앞으로가기 지원 — 탭은 URL에 남지만 예전에는 `replaceState`만 써서
  // 히스토리 항목이 쌓이지 않았고, 뒤로가기가 /stats 밖으로 나가 버렸다.
  useEffect(() => {
    const onPop = () => {
      const t = resolveTab(new URLSearchParams(window.location.search).get("tab"));
      setTab(t ?? "overview");
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const selectTab = (k: Tab) => {
    if (k === tab) return;              // 같은 탭을 다시 눌러 히스토리를 늘리지 않는다
    setTab(k);
    const url = new URL(window.location.href);
    if (k === "overview") url.searchParams.delete("tab");
    else url.searchParams.set("tab", k);
    // ?view=는 싱드컵 탭 안에서만 뜻이 있다(공식 예선 참가자 ↔ 비공식 랭킹).
    // 탭을 옮기면 그 상태는 더 이상 가리킬 화면이 없으므로 지운다. 싱드컵 탭으로
    // 들어올 때도 지워져 기본 화면(공식 명단)에서 시작한다.
    url.searchParams.delete("view");
    // **pushState다.** 탭 전환은 사용자가 되돌리고 싶어 하는 이동이다 —
    // replaceState면 뒤로가기가 /stats를 통째로 벗어난다.
    window.history.pushState(null, "", url.toString());
  };
  // '카테고리 분석' 표에서 행을 누르면 이 값이 정해지고 '카테고리별 스트리머' 탭으로 넘어간다
  const [pickedCat, setPickedCat] = useState<string | null>(null);
  // selectTab을 쓴다 — setTab을 직접 부르면 URL의 ?tab= 이 갱신되지 않는다
  const pickCategory = (c: string | null) => { setPickedCat(c); if (c) selectTab("category_streamers"); };

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
      {/* 공통 헤더 하나만 쓴다. 현재 위치는 `breadcrumb`으로 넘긴다 —
          예전에는 이 페이지만 브랜드 링크 마크업을 따로 갖고 있었다. */}
      <SiteHeader
        maxWidth="full"
        breadcrumb={
          <span className="flex items-center gap-1.5 text-[16px] font-extrabold">
            <BarChart3 size={17} style={{ color: GREEN }} aria-hidden="true" />
            <GradText>치지직 통계</GradText>
          </span>
        } />

      <main className="flex-1 w-full px-4 md:px-6 py-7 md:py-9">
        <div className="mb-6">
          {/* Beta 배지는 **공통 헤더의 '치지직 통계' 메뉴 옆 하나뿐**이다.
              예전에는 여기 제목 옆에도 있어 같은 화면에 Beta가 두 번 보였다.
              (그 배지는 상속된 line-height 때문에 정사각형으로 눌려 보이는 문제도
              있었는데, 출처를 하나로 모으면서 함께 사라졌다.) */}
          <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight leading-tight flex items-center gap-2 flex-wrap">
            <span>치지직 <GradText>방송</GradText> 통계</span>
          </h1>
          <div className="mt-2 flex items-end justify-between gap-3 flex-wrap">
            <p className="text-muted text-sm md:text-base">
              치지직 라이브 방송의 시청자·카테고리 트렌드를 실시간으로 분석합니다.
            </p>
            {/* 싱드컵 탭에는 '싱드컵 집계'가 그 화면 우측 상단에 따로 있다. 수집기가
                다른 두 시각(라이브 스냅샷 / 싱드컵 클립)이 같은 자리에 겹쳐 보이면
                어느 쪽이 맞는지 알 수 없으므로, 싱드컵 탭에서는 이쪽을 숨긴다. */}
            {/* 이 칩은 데이터가 도착해야 값이 생긴다. 예전에는 그때 처음 나타났는데,
                좁은 화면에서는 설명 문장 옆에 자리가 없어 **새 줄로 접히면서** 아래를
                밀어냈다. 그래서 값이 없을 때도 같은 자리를 비워 둔다 — `invisible`은
                레이아웃을 그대로 차지하면서 화면에서만 감춘다(`hidden`이면 자리까지
                없어져 원래대로다). 빈 칩을 읽지 않도록 `aria-hidden`을 붙이고 title도 뗀다.

                폭은 **고정 px이 아니라 `min-width`로** 잡는다. 플레이스홀더 글자로 폭을
                맞추려 했더니 실제 칩(230px)보다 좁아져(185px) 자리 예약이 부족했다 —
                날짜 길이("8월 7일" vs "12월 17일")와 글꼴 배율(125·150%)에 따라 실제 폭이
                달라지므로, 어떤 숫자를 골라도 언젠가 어긋난다. `min-w`로 바닥만 정하고
                실제 내용이 그보다 넓으면 자연히 넓어지게 둔다. `14rem`은 글꼴 배율을 따라
                같이 커지므로 확대에서도 비율이 유지된다(px 고정은 그러지 못한다).

                **단, 바닥값에 상한이 필요하다.** 확대 150%(CSS 뷰포트 260px)에서
                14rem 바닥이 그대로 살아 계산된 min-width가 266px가 되면서 페이지가
                통째로 가로로 넘쳤다(실측 sw 296~309 / cw 260). `min(14rem,100%)`는
                넉넉한 화면에서는 14rem 자리 예약을 그대로 유지하고, 좁은 화면에서만
                부모 폭까지 물러선다. `shrink-0`도 뺐다 — 그게 남아 있으면 flex가
                칩을 줄이지 못해 상한이 무의미해진다. */}
            {tab !== "singcup" && (
              <span className={"inline-flex min-w-[min(14rem,100%)] items-center justify-end gap-1"
                               + " text-muted/70 text-sm ml-auto"
                               + (collectedLabel ? "" : " invisible")}
                    aria-hidden={collectedLabel ? undefined : true}
                    title={collectedLabel
                      ? "치지직 라이브 방송 스냅샷을 마지막으로 수집한 시각입니다. 싱드컵 순위는 별도 수집기로 갱신됩니다."
                      : undefined}>
                {collectedLabel && (
                  <>
                    <Circle size={7} className="fill-current" style={{ color: GREEN }} />
                    라이브 집계 {collectedLabel}
                  </>
                )}
              </span>
            )}
          </div>
        </div>

        {/* ── 전이 상태의 높이를 실제 화면과 맞춘다 (CLS) ──────────────────────
            로딩 스피너는 `py-24`뿐이라 높이가 약 208px이었다. 데이터가 도착하면 그 자리에
            좌측 메뉴 + 본문 그리드가 들어서며 높이가 몇 배로 뛰고, **그 아래에 있던 통계
            안내 섹션과 푸터가 화면 밖으로 밀려난다.** 뷰포트 안에서 일어나는 이동이라
            그대로 CLS가 됐다(실측 768px 0.70 / 1440px 0.34).

            그래서 로딩·오류·빈 상태가 모두 같은 최소 높이를 차지하게 한다. 아래 콘텐츠가
            처음부터 접힘선 밑에서 시작하면, 데이터가 들어와도 화면 안에서 움직일 것이
            없다. 스켈레톤 행을 흉내 내 높이를 맞추는 방법도 있지만, 실제 행 수가 데이터에
            따라 달라져 어차피 정확히 맞출 수 없다 — 컨테이너 하나로 바닥을 깔아 두는 편이
            데이터 양과 무관하게 안정적이다.

            `100svh`를 쓰는 이유: 모바일 주소창이 접혔다 펴질 때 `vh`는 값이 바뀌어
            그 자체가 또 다른 이동을 만든다. `svh`는 가장 작은 뷰포트 기준이라 변하지 않는다.
            220px는 위쪽 헤더(제목 + 설명 + 집계 칩)가 차지하는 대략적인 높이다. */}
        {/* 네 상태(로딩·오류·빈 상태·정상)를 **같은 최소 높이** 위에 올린다.
            로딩에만 높이를 주면 짧은 빈 상태로 바뀔 때 아래 콘텐츠가 위로 당겨져
            같은 문제가 방향만 바꿔 다시 생긴다(실측: 빈 상태 0.0365 → 0.0807). */}
        <div className="min-h-[calc(100svh-220px)]">
        {loading ? (
          <div className="flex items-start justify-center gap-2 pt-24 text-muted">
            <Loader2 size={18} className="animate-spin" /> 통계를 불러오는 중입니다. 잠시만 기다려 주세요.
          </div>
        ) : error ? (
          // 세 상태를 문구까지 나눈다 — '불러오지 못함'(오류)과 '아직 없음'(정상 0건)은
          // 사용자가 해야 할 일이 서로 다르다.
          <div className="card text-center py-14 px-5">
            <p className="font-medium text-fg">통계를 불러오지 못했습니다.</p>
            <p className="mt-1.5 text-sm text-muted leading-relaxed">
              일시적인 네트워크 문제이거나 수집 서버가 재시작 중일 수 있습니다.
              잠시 후 새로고침하면 대부분 복구됩니다.
            </p>
            <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
              <button onClick={() => location.reload()} className="btn-secondary text-sm">새로고침</button>
              <Link href="/status" className="btn-secondary text-sm">서버 상태 확인</Link>
              <Link href="/contact" className="btn-secondary text-sm">문제 신고</Link>
            </div>
          </div>
        ) : empty ? (
          <div className="card text-center py-14 px-5">
            <Radio size={36} className="mx-auto mb-3 opacity-30" style={{ color: GREEN }} />
            <p className="font-medium text-fg">아직 집계된 데이터가 없습니다.</p>
            <p className="mt-1.5 text-sm text-muted leading-relaxed">
              통계는 약 10분 간격의 수집 결과가 쌓여야 표시됩니다. 서비스를 막 시작했거나
              수집이 잠시 멈췄던 경우 첫 집계까지 시간이 걸릴 수 있습니다.
            </p>
            <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
              <button onClick={() => location.reload()} className="btn-secondary text-sm">새로고침</button>
              <Link href="/status" className="btn-secondary text-sm">서버 상태 확인</Link>
              <Link href="/about" className="btn-secondary text-sm">서비스 소개</Link>
            </div>
          </div>
        ) : (
          /* 좌측 메뉴 폭 210px → 240px. 210px에서는 가장 긴 한글 라벨(`전체 스트리머 분석`
             ≈115px)과 LIVE 배지가 동시에 들어가지 못했다 —
             210 − pl-2.5(10) − border(2) − px-3(24) − gap-2.5 ×2(20) = 154px 안에
             아이콘 16 + 배지 ≈38이 먼저 자리를 잡아 라벨에 약 100px만 남았다.
             240px면 같은 계산에서 라벨에 약 130px이 남아 가장 긴 라벨도 여유가 생긴다.
             글꼴이 더 넓은 PC를 위한 최종 안전망은 StatsNav의 라벨 truncate다. */
          <div className="grid grid-cols-1 md:grid-cols-[240px_1fr] gap-5 md:gap-7">
            {/* 좌측 메뉴 */}
            <StatsNav active={tab} onSelect={selectTab}>
              <StreamerSearch />
            </StatsNav>

            {/* 우측 뷰 */}
            <div className="min-w-0">
              {tab === "bongnudo"                      && <BongnudoTab />}
              {tab === "singcup"                       && <Singcup />}
              {tab === "overview"           && ov   && <OverviewTab ov={ov} stars={stars} />}
              {/* 두 통계 탭은 **각자 자기 상태를 갖는다**. `key`로 강제 재마운트해
                  한쪽에서 난 오류·스크롤 위치가 다른 쪽에 남지 않게 한다. */}
              {tab === "newcomers_stats" && (
                <NewcomerStatsTab key="new" group="new" initial={news}
                                  onRanking={() => selectTab("newcomers_ranking")} />
              )}
              {tab === "small_stats" && (
                <NewcomerStatsTab key="small" group="small"
                                  onRanking={() => selectTab("small_ranking")} />
              )}
              {tab === "period_analysis"              && <PeriodAnalysis />}
              {tab === "ranking"            && rank && <RankingTab rank={rank} />}
              {tab === "newcomers_ranking"  && news && <NewcomersRankingTab data={news} />}
              {tab === "small_ranking"                && <SmallRankingTab />}
              {tab === "ranking_period"                && <PeriodRankingTab />}
              {tab === "category"           && cats && <CategoryTab cats={cats} onPick={pickCategory} />}
              {tab === "tags"                         && <TagSearch />}
              {tab === "category_streamers"          && (
                <CategoryStreamerList category={pickedCat} onPick={setPickedCat} />
              )}
            </div>
          </div>
        )}
        </div>

        {/* 서비스 소개 — 로딩/에러 분기 밖에 두어 데이터 상태와 무관하게
            항상 서버 렌더링 HTML에 포함되게 한다(크롤러가 읽는 본문). */}
        <StatsAbout />
      </main>

      <Footer />
    </div>
  );
}
