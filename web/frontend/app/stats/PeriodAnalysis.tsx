"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Loader2, Search, X, Filter, Clock, CalendarDays } from "lucide-react";
import { api } from "@/lib/api";
import type {
  RisingPeriodAnalysis, RisingPeriodFilters, PeriodTableRow,
} from "@/lib/types";

// 기간 + 카테고리 + 태그 + 체급을 조합해 추이를 보는 대시보드.
// 좌측 필터는 '분석 적용'을 눌러야 반영된다(필터를 바꿀 때마다 무거운 쿼리가
// 나가지 않도록 draft/applied 상태를 분리했다).

const GREEN = "#00FFA3";
const CYAN = "#06B6D4";
const PANEL = "#181A20";

const nf = (n: number) => Math.round(n).toLocaleString("ko-KR");
const compact = (n: number) =>
  n >= 100_000_000 ? `${(n / 100_000_000).toFixed(1)}억`
  : n >= 10_000 ? `${(n / 10_000).toFixed(1)}만`
  : n >= 1_000 ? `${(n / 1_000).toFixed(1)}천`
  : nf(n);

const DOW_LABEL = ["월", "화", "수", "목", "금", "토", "일"];
const TIERS: { key: string; label: string; hint?: string }[] = [
  { key: "all",    label: "전체" },
  { key: "rookie", label: "신입/라이징", hint: "10명 이하" },
  { key: "small",  label: "중소형",     hint: "11~100명" },
  { key: "large",  label: "대기업",     hint: "100명 초과" },
];
const RANGES = [
  { key: "today",  label: "오늘" },
  { key: "7d",     label: "지난 7일" },
  { key: "30d",    label: "지난 30일" },
  { key: "custom", label: "직접 지정" },
] as const;

interface Draft {
  range: string; start: string; end: string;
  category: string; tags: string[]; tier: string;
}
const DEFAULT_DRAFT: Draft = {
  range: "7d", start: "", end: "", category: "", tags: [], tier: "all",
};

const todayStr = () => new Date().toISOString().slice(0, 10);
const Sub = ({ children }: { children: React.ReactNode }) =>
  <p className="mt-3 text-[11px] leading-relaxed text-gray-400">{children}</p>;

// 컴포넌트 밖에 둬야 한다. FilterPanel 안에 정의하면 렌더마다 새 컴포넌트 타입이 되어
// 자식이 통째로 리마운트되고, 안에 있는 카테고리 검색 input이 한 글자 칠 때마다
// 포커스를 잃는다.
const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div className="border-t border-gray-800/80 px-4 py-4 first:border-t-0">
    <h4 className="mb-2.5 text-xs font-bold tracking-wide text-gray-300">{title}</h4>
    {children}
  </div>
);

// ── 좌측 필터 패널 ───────────────────────────────────────────────────────────
function FilterPanel({
  draft, setDraft, filters, onApply, busy, dirty,
}: {
  draft: Draft; setDraft: (d: Draft) => void;
  filters: RisingPeriodFilters | null;
  onApply: () => void; busy: boolean; dirty: boolean;
}) {
  const [catOpen, setCatOpen] = useState(false);
  const [catQuery, setCatQuery] = useState("");
  const catRef = useRef<HTMLDivElement>(null);

  // 드롭다운은 바깥을 누르면 닫는다
  useEffect(() => {
    if (!catOpen) return;
    const h = (e: MouseEvent) => {
      if (catRef.current && !catRef.current.contains(e.target as Node)) setCatOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [catOpen]);

  const cats = useMemo(() => {
    const all = filters?.categories ?? [];
    const q = catQuery.trim().toLowerCase();
    return (q ? all.filter((c) => c.toLowerCase().includes(q)) : all).slice(0, 120);
  }, [filters, catQuery]);

  const toggleTag = (t: string) =>
    setDraft({ ...draft, tags: draft.tags.includes(t)
      ? draft.tags.filter((x) => x !== t)
      : draft.tags.length >= 8 ? draft.tags : [...draft.tags, t] });

  return (
    // 데스크톱에서 사이드바는 sticky다. 필터가 길어지면서 '분석 적용' 버튼이 화면
    // 아래로 밀려, 우측 '카테고리별 상세 데이터'(100행)를 끝까지 스크롤해야 버튼이
    // 보이는 상태였다. 사이드바 높이를 뷰포트로 제한하고 가운데 필터 영역만
    // 스크롤시켜, 헤더와 버튼은 항상 보이게 한다.
    <aside className="w-full shrink-0 self-start rounded-2xl border border-gray-800/80
                      md:sticky md:top-[76px] md:flex md:max-h-[calc(100vh-92px)] md:w-[280px] md:flex-col"
           style={{ background: PANEL }}>
      <div className="flex shrink-0 items-center gap-2 px-4 py-3.5">
        <Filter size={15} style={{ color: GREEN }} />
        <span className="text-sm font-bold text-fg">분석 필터</span>
      </div>

      {/* min-h-0: flex 아이템의 기본 min-height:auto 때문에 overflow-y가 먹지 않는 것을 푼다.
          overscroll-contain: 필터 목록 끝까지 스크롤한 뒤 계속 굴리면 스크롤이 페이지로
          넘어가(스크롤 체이닝) 본문이 같이 내려가던 것을 막는다. */}
      <div className="md:min-h-0 md:flex-1 md:overflow-y-auto md:overscroll-contain">
      <Section title="기간">
        <div className="grid grid-cols-2 gap-1.5">
          {RANGES.map((r) => {
            const on = draft.range === r.key;
            return (
              <button key={r.key}
                onClick={() => setDraft({
                  ...draft, range: r.key,
                  start: draft.start || todayStr(), end: draft.end || todayStr(),
                })}
                className="rounded-lg border px-2 py-1.5 text-xs font-semibold transition-colors"
                style={{
                  background: on ? "rgba(0,255,163,0.10)" : "transparent",
                  borderColor: on ? "rgba(0,255,163,0.45)" : "rgb(55,65,81)",
                  color: on ? GREEN : "rgb(156,163,175)",
                }}>
                {r.label}
              </button>
            );
          })}
        </div>
        {draft.range === "custom" && (
          <div className="mt-2.5 flex items-center gap-1.5">
            <input type="date" value={draft.start} max={todayStr()}
                   onChange={(e) => setDraft({ ...draft, start: e.target.value })}
                   className="min-w-0 flex-1 rounded-lg border border-gray-700 bg-black/30 px-2 py-1.5 text-[11px] text-fg" />
            <span className="text-xs text-gray-500">~</span>
            <input type="date" value={draft.end} max={todayStr()}
                   onChange={(e) => setDraft({ ...draft, end: e.target.value })}
                   className="min-w-0 flex-1 rounded-lg border border-gray-700 bg-black/30 px-2 py-1.5 text-[11px] text-fg" />
          </div>
        )}
      </Section>

      <Section title="카테고리">
        <div ref={catRef} className="relative">
          <button onClick={() => setCatOpen((v) => !v)}
                  className="flex w-full items-center gap-2 rounded-lg border border-gray-700 bg-black/30 px-2.5 py-2 text-left text-xs">
            <span className="flex-1 truncate" style={{ color: draft.category ? GREEN : "rgb(156,163,175)" }}>
              {draft.category || "전체 카테고리"}
            </span>
            {draft.category && (
              <X size={13} className="shrink-0 text-gray-500 hover:text-fg"
                 onClick={(e) => { e.stopPropagation(); setDraft({ ...draft, category: "" }); }} />
            )}
            <ChevronDown size={13} className="shrink-0 text-gray-500 transition-transform"
                         style={{ transform: catOpen ? "rotate(180deg)" : "none" }} />
          </button>
          {catOpen && (
            <div className="absolute left-0 right-0 top-full z-30 mt-1 overflow-hidden rounded-lg border border-gray-700 shadow-2xl"
                 style={{ background: "#12141A" }}>
              <div className="flex items-center gap-1.5 border-b border-gray-800 px-2.5 py-2">
                <Search size={12} className="text-gray-500" />
                <input autoFocus value={catQuery} onChange={(e) => setCatQuery(e.target.value)}
                       placeholder="카테고리 검색"
                       className="w-full bg-transparent text-xs text-fg outline-none placeholder:text-gray-600" />
              </div>
              <div className="max-h-[240px] overflow-y-auto py-1">
                <button onClick={() => { setDraft({ ...draft, category: "" }); setCatOpen(false); }}
                        className="block w-full px-3 py-1.5 text-left text-xs text-gray-400 hover:bg-white/5">
                  전체 카테고리
                </button>
                {cats.map((c) => (
                  <button key={c} onClick={() => { setDraft({ ...draft, category: c }); setCatOpen(false); }}
                          className="block w-full truncate px-3 py-1.5 text-left text-xs text-fg hover:bg-white/5">
                    {c}
                  </button>
                ))}
                {cats.length === 0 && (
                  <p className="px-3 py-3 text-center text-[11px] text-gray-500">일치하는 카테고리가 없습니다.</p>
                )}
              </div>
            </div>
          )}
        </div>
      </Section>

      <Section title="태그">
        <div className="flex flex-wrap gap-1.5">
          {(filters?.tags ?? []).slice(0, 24).map((t) => {
            const on = draft.tags.includes(t.tag);
            return (
              <button key={t.tag} onClick={() => toggleTag(t.tag)} title={`라이브 ${nf(t.lives)}개`}
                className="rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors"
                style={{
                  background: on ? "rgba(0,255,163,0.12)" : "transparent",
                  borderColor: on ? "rgba(0,255,163,0.45)" : "rgb(55,65,81)",
                  color: on ? GREEN : "rgb(156,163,175)",
                }}>
                #{t.tag}
              </button>
            );
          })}
          {!filters && <span className="text-[11px] text-gray-500">불러오는 중...</span>}
          {filters && filters.tags.length === 0 &&
            <span className="text-[11px] text-gray-500">수집된 태그가 아직 없습니다.</span>}
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-gray-400">
          최근 하루 안에 해당 태그로 방송한 채널을 기준으로 거릅니다. 최대 8개까지 선택할 수 있고,
          하나라도 해당하면 포함됩니다.
        </p>
      </Section>

      <Section title="스트리머 체급">
        <div className="flex flex-col gap-1">
          {TIERS.map((t) => {
            const on = draft.tier === t.key;
            return (
              <label key={t.key}
                     className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-white/5">
                {/* sr-only input + 감싼 label 조합 — components/Switch.tsx와 같은 방식이라
                    라벨 어디를 눌러도 선택된다 */}
                <input type="radio" name="pa-tier" checked={on} className="sr-only"
                       onChange={() => setDraft({ ...draft, tier: t.key })} />
                <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border"
                      style={{ borderColor: on ? GREEN : "rgb(75,85,99)" }}>
                  {on && <span className="h-1.5 w-1.5 rounded-full" style={{ background: GREEN }} />}
                </span>
                <span className="text-xs font-semibold" style={{ color: on ? GREEN : undefined }}>{t.label}</span>
                {t.hint && <span className="ml-auto text-[10px] text-gray-500">{t.hint}</span>}
              </label>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-gray-400">
          선택한 기간의 채널 평균 동시 시청자를 기준으로 나눕니다.
        </p>
      </Section>
      </div>

      <div className="shrink-0 border-t border-gray-800/80 p-4">
        <button onClick={onApply} disabled={busy}
                className="flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-bold transition-opacity disabled:opacity-60"
                style={{ background: GREEN, color: "#000" }}>
          {busy ? <><Loader2 size={15} className="animate-spin" /> 분석 중...</> : "분석 적용"}
        </button>
        {dirty && !busy && (
          <p className="mt-2 text-center text-[11px]" style={{ color: CYAN }}>
            변경한 필터가 아직 반영되지 않았습니다.
          </p>
        )}
      </div>
    </aside>
  );
}

// ── 메인 추이 차트(이중축) ──────────────────────────────────────────────────
// LineChart는 Y축이 하나라 '시청자 + 채널 수'를 같은 축에 올리면 한쪽이 뭉갠다.
// 세 모드(시청자/채널/이중축)를 한 구현으로 처리하려고 전용 SVG로 그린다.
type ChartMode = "viewers" | "channels" | "both";

function niceMax(v: number) {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * exp;
}

function TrendChart({
  points, bucket, mode,
}: {
  points: { t: number; viewers: number; channels: number }[];
  bucket: "hour" | "day"; mode: ChartMode;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(760);
  const [hover, setHover] = useState<number | null>(null);
  const H = 260, PAD = { l: 52, r: 52, t: 16, b: 28 };

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const apply = () => setW(Math.max(320, Math.round(el.clientWidth)));
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 빈 배열이면 areaPath의 x(-1)이 NaN이 되어 path가 깨진다
  if (points.length === 0) {
    return <div ref={wrapRef} className="flex h-[260px] items-center justify-center text-sm text-muted">
      표시할 구간이 없습니다.
    </div>;
  }

  const showV = mode !== "channels";
  const showC = mode !== "viewers";
  const maxV = niceMax(Math.max(1, ...points.map((p) => p.viewers)));
  const maxC = niceMax(Math.max(1, ...points.map((p) => p.channels)));
  const iw = Math.max(1, w - PAD.l - PAD.r), ih = H - PAD.t - PAD.b;
  const x = (i: number) => PAD.l + (points.length <= 1 ? iw / 2 : (i / (points.length - 1)) * iw);
  const yV = (v: number) => PAD.t + ih - (v / maxV) * ih;
  const yC = (v: number) => PAD.t + ih - (v / maxC) * ih;

  const path = (get: (p: typeof points[number]) => number, y: (v: number) => number) =>
    points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(get(p))}`).join(" ");
  const areaPath = (get: (p: typeof points[number]) => number, y: (v: number) => number) =>
    `${path(get, y)} L${x(points.length - 1)},${PAD.t + ih} L${x(0)},${PAD.t + ih} Z`;

  const fmtX = (t: number) =>
    bucket === "hour"
      ? new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", hour12: false })
      : new Date(t * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });

  // 라벨이 겹치지 않게 표시 간격을 폭에 맞춰 정한다
  const step = Math.max(1, Math.ceil(points.length / Math.max(2, Math.floor(iw / 90))));

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * w;
    if (points.length <= 1) return setHover(0);
    const i = Math.round(((px - PAD.l) / iw) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };

  const hp = hover != null ? points[hover] : null;

  return (
    <div ref={wrapRef} className="relative w-full">
      <svg width="100%" height={H} viewBox={`0 0 ${w} ${H}`} preserveAspectRatio="none"
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <defs>
          <linearGradient id="pa-gv" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={GREEN} stopOpacity="0.28" />
            <stop offset="100%" stopColor={GREEN} stopOpacity="0" />
          </linearGradient>
          <linearGradient id="pa-gc" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={CYAN} stopOpacity="0.22" />
            <stop offset="100%" stopColor={CYAN} stopOpacity="0" />
          </linearGradient>
        </defs>

        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const y = PAD.t + ih - f * ih;
          return (
            <g key={f}>
              <line x1={PAD.l} y1={y} x2={w - PAD.r} y2={y} stroke="rgba(255,255,255,0.06)" />
              {showV && <text x={PAD.l - 8} y={y + 3.5} textAnchor="end" fontSize="10" fill={GREEN} opacity="0.75">
                {compact(maxV * f)}
              </text>}
              {showC && <text x={w - PAD.r + 8} y={y + 3.5} fontSize="10" fill={CYAN} opacity="0.75">
                {compact(maxC * f)}
              </text>}
            </g>
          );
        })}

        {points.map((p, i) => i % step === 0 && (
          <text key={p.t} x={x(i)} y={H - 8} textAnchor="middle" fontSize="10" fill="rgb(156,163,175)">
            {fmtX(p.t)}
          </text>
        ))}

        {showC && <>
          <path d={areaPath((p) => p.channels, yC)} fill="url(#pa-gc)" />
          <path d={path((p) => p.channels, yC)} fill="none" stroke={CYAN} strokeWidth="2"
                strokeLinejoin="round" strokeLinecap="round" />
        </>}
        {showV && <>
          <path d={areaPath((p) => p.viewers, yV)} fill="url(#pa-gv)" />
          <path d={path((p) => p.viewers, yV)} fill="none" stroke={GREEN} strokeWidth="2.2"
                strokeLinejoin="round" strokeLinecap="round" />
        </>}

        {hp && (
          <g>
            <line x1={x(hover!)} y1={PAD.t} x2={x(hover!)} y2={PAD.t + ih}
                  stroke="rgba(255,255,255,0.22)" strokeDasharray="3 3" />
            {showV && <circle cx={x(hover!)} cy={yV(hp.viewers)} r="4" fill={GREEN} stroke="#000" strokeWidth="1.5" />}
            {showC && <circle cx={x(hover!)} cy={yC(hp.channels)} r="4" fill={CYAN} stroke="#000" strokeWidth="1.5" />}
          </g>
        )}
      </svg>

      {hp && (
        <div className="pointer-events-none absolute top-2 rounded-lg border border-gray-700 px-2.5 py-1.5 text-[11px] shadow-xl"
             style={{ background: "#12141A",
                      left: `${Math.min(78, Math.max(2, (x(hover!) / w) * 100))}%` }}>
          <div className="mb-0.5 font-semibold text-fg">{fmtX(hp.t)}</div>
          {showV && <div style={{ color: GREEN }}>평균 동시 시청자 {nf(hp.viewers)}명</div>}
          {showC && <div style={{ color: CYAN }}>방송 채널 {nf(hp.channels)}개</div>}
        </div>
      )}
    </div>
  );
}

// ── 막대 차트(시간대/요일) ──────────────────────────────────────────────────
function BarBlock({
  title, desc, bars, note,
}: {
  title: string; desc: string; note: string;
  bars: { label: string; value: number; samples: number }[];
}) {
  const max = Math.max(1, ...bars.map((b) => b.value));
  const best = bars.reduce((a, b) => (b.value > a.value ? b : a), bars[0]);
  return (
    <div className="rounded-2xl border border-gray-800/80 p-5" style={{ background: PANEL }}>
      <h3 className="section-title">{title}</h3>
      <p className="mt-1 text-sm text-muted">{desc}</p>
      <div className="mt-4 flex h-[150px] items-end gap-[3px]">
        {bars.map((b) => {
          const on = b.label === best?.label && b.value > 0;
          return (
            <div key={b.label} className="group relative flex h-full flex-1 flex-col justify-end"
                 title={b.samples === 0 ? `${b.label}: 집계 없음` : `${b.label}: 평균 ${nf(b.value)}명`}>
              <div className="w-full rounded-t transition-all"
                   style={{
                     height: `${Math.max(b.value > 0 ? 3 : 0, (b.value / max) * 100)}%`,
                     background: on ? `linear-gradient(180deg, ${GREEN}, ${CYAN})`
                                    : "rgba(0,255,163,0.28)",
                   }} />
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex gap-[3px]">
        {bars.map((b, i) => (
          <span key={b.label} className="flex-1 text-center text-[9px] tabular-nums text-gray-500">
            {bars.length > 12 ? (i % 3 === 0 ? b.label : "") : b.label}
          </span>
        ))}
      </div>
      {best && best.value > 0 && (
        <p className="mt-3 text-xs text-muted">
          피크: <b style={{ color: GREEN }}>{best.label}</b> · 평균 {nf(best.value)}명
        </p>
      )}
      <Sub>{note}</Sub>
    </div>
  );
}

// ── 상세 데이터 테이블 ───────────────────────────────────────────────────────
type SortKey = keyof Omit<PeriodTableRow, "category">;
const COLS: { key: SortKey; label: string; fmt: (r: PeriodTableRow) => string }[] = [
  { key: "hours",         label: "총 방송 시간", fmt: (r) => `${compact(r.hours)}h` },
  { key: "peak_channels", label: "최고 / 평균 채널", fmt: (r) => `${nf(r.peak_channels)} / ${r.avg_channels.toFixed(1)}` },
  { key: "peak_viewers",  label: "최고 / 평균 시청자", fmt: (r) => `${compact(r.peak_viewers)} / ${compact(r.avg_viewers)}` },
  { key: "viewership",    label: "뷰어쉽", fmt: (r) => `${compact(r.viewership)}h` },
];
const PAGE_SIZE = 100;

function DataTable({ rows }: { rows: PeriodTableRow[] }) {
  const [sort, setSort] = useState<SortKey>("viewership");
  const [asc, setAsc] = useState(false);
  const [page, setPage] = useState(0);

  const sorted = useMemo(
    () => [...rows].sort((a, b) => (asc ? 1 : -1) * ((a[sort] as number) - (b[sort] as number))),
    [rows, sort, asc]);
  const pages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const p = Math.min(page, pages - 1);
  const view = sorted.slice(p * PAGE_SIZE, (p + 1) * PAGE_SIZE);

  const click = (k: SortKey) => {
    if (k === sort) setAsc((v) => !v); else { setSort(k); setAsc(false); }
    setPage(0);
  };

  return (
    <div className="rounded-2xl border border-gray-800/80 p-5" style={{ background: PANEL }}>
      <h3 className="section-title">카테고리별 상세 데이터</h3>
      <p className="mt-1 text-sm text-muted">
        선택한 조건에 해당하는 방송을 카테고리로 묶어 집계했습니다. 열 제목을 누르면 정렬됩니다.
      </p>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[680px] text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-xs text-gray-400">
              <th className="w-12 py-2.5 text-left font-semibold">#</th>
              <th className="py-2.5 text-left font-semibold">카테고리</th>
              {COLS.map((c) => (
                <th key={c.key} onClick={() => click(c.key)}
                    className="cursor-pointer select-none py-2.5 text-right font-semibold transition-colors hover:text-fg"
                    style={{ color: sort === c.key ? GREEN : undefined }}>
                  {c.label}
                  <span className="ml-1 text-[9px]">{sort === c.key ? (asc ? "▲" : "▼") : "↕"}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {view.map((r, i) => {
              const rank = p * PAGE_SIZE + i + 1;
              return (
                <tr key={r.category} className="border-b border-gray-800/60 last:border-0 transition-colors hover:bg-white/[0.03]">
                  <td className="py-2.5 text-left text-xs font-extrabold tabular-nums"
                      style={{ color: rank <= 3 ? GREEN : "rgb(107,114,128)" }}>{rank}</td>
                  <td className="max-w-[240px] truncate py-2.5 pr-3 font-medium text-fg" title={r.category}>
                    {r.category}
                  </td>
                  {COLS.map((c) => (
                    <td key={c.key} className="py-2.5 text-right tabular-nums"
                        style={{ color: sort === c.key ? GREEN : "rgb(209,213,219)" }}>
                      {c.fmt(r)}
                    </td>
                  ))}
                </tr>
              );
            })}
            {view.length === 0 && (
              <tr><td colSpan={6} className="py-10 text-center text-sm text-muted">조건에 맞는 데이터가 없습니다.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-2">
          <button onClick={() => setPage(Math.max(0, p - 1))} disabled={p === 0}
                  className="rounded-lg border border-gray-700 px-3 py-1.5 text-xs disabled:opacity-40">이전</button>
          <span className="text-xs tabular-nums text-muted">{p + 1} / {pages}</span>
          <button onClick={() => setPage(Math.min(pages - 1, p + 1))} disabled={p >= pages - 1}
                  className="rounded-lg border border-gray-700 px-3 py-1.5 text-xs disabled:opacity-40">다음</button>
        </div>
      )}
      <Sub>
        * 뷰어쉽은 시청 시간(동시 시청자 × 방송 시간)이며, 채널 수와 시청자 수는 시간 단위 집계의
        최고값과 평균값입니다. 한 페이지에 {PAGE_SIZE}개씩 표시합니다.
      </Sub>
    </div>
  );
}

// ── 요약 카드 ────────────────────────────────────────────────────────────────
function MetricCard({ label, value, unit, sub, accent }: {
  label: string; value: string; unit?: string; sub: string; accent?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-gray-800/80 p-4" style={{ background: PANEL }}>
      <p className="text-xs font-semibold text-gray-400">{label}</p>
      <p className="mt-1.5 flex items-baseline gap-1">
        <span className="text-2xl font-extrabold tabular-nums" style={{ color: accent ? GREEN : "#fff" }}>{value}</span>
        {unit && <span className="text-xs text-gray-400">{unit}</span>}
      </p>
      <p className="mt-1.5 text-[11px] text-gray-400">{sub}</p>
    </div>
  );
}

// ── 페이지 ───────────────────────────────────────────────────────────────────
export default function PeriodAnalysis() {
  const [draft, setDraft] = useState<Draft>(DEFAULT_DRAFT);
  const [applied, setApplied] = useState<Draft>(DEFAULT_DRAFT);
  const [filters, setFilters] = useState<RisingPeriodFilters | null>(null);
  const [data, setData] = useState<RisingPeriodAnalysis | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [mode, setMode] = useState<ChartMode>("both");

  useEffect(() => {
    let alive = true;
    api.rising.periodFilters()
      .then((d) => { if (alive) setFilters(d); })
      .catch(() => { if (alive) setFilters({ categories: [], tags: [] }); });
    return () => { alive = false; };
  }, []);

  const run = useCallback((q: Draft) => {
    setBusy(true); setErr(null);
    api.rising.periodAnalysis({
      range: q.range, start: q.start, end: q.end,
      category: q.category, tags: q.tags, tier: q.tier,
    })
      .then((d) => {
        if (d.error) { setErr(d.detail || "요청을 처리하지 못했습니다."); setData(null); }
        else { setData(d); }
      })
      .catch(() => setErr("데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요."))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => { run(DEFAULT_DRAFT); }, [run]);

  const dirty = JSON.stringify(draft) !== JSON.stringify(applied);
  const apply = () => { setApplied(draft); run(draft); };

  const s = data?.summary ?? null;
  const rangeLabel = RANGES.find((r) => r.key === applied.range)?.label ?? "";
  const peakWhen = s?.peak_at
    ? new Date(s.peak_at * 1000).toLocaleString("ko-KR",
        { month: "numeric", day: "numeric", hour: "2-digit", hour12: false })
    : "";

  return (
    <div className="flex flex-col gap-5 md:flex-row">
      <FilterPanel draft={draft} setDraft={setDraft} filters={filters}
                   onApply={apply} busy={busy} dirty={dirty} />

      <div className="min-w-0 flex-1 space-y-5">
        {/* 적용된 조건 요약 */}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted">적용 조건</span>
          {[rangeLabel, applied.category || "전체 카테고리",
            TIERS.find((t) => t.key === applied.tier)?.label ?? "전체",
            ...applied.tags.map((t) => `#${t}`)].map((chip, i) => (
            <span key={i} className="rounded-full border px-2.5 py-1 font-medium"
                  style={{ borderColor: "rgba(0,255,163,0.25)", background: "rgba(0,255,163,0.07)", color: GREEN }}>
              {chip}
            </span>
          ))}
        </div>

        {err && (
          <div className="rounded-2xl border border-red-500/30 bg-red-500/5 p-4 text-sm text-red-300">{err}</div>
        )}

        {busy && !data && (
          <div className="flex items-center gap-2 rounded-2xl border border-gray-800/80 p-10 text-sm text-muted"
               style={{ background: PANEL }}>
            <Loader2 size={16} className="animate-spin" /> 분석 중입니다...
          </div>
        )}

        {data && !s && !busy && !err && (
          <div className="rounded-2xl border border-gray-800/80 p-10 text-center text-sm text-muted" style={{ background: PANEL }}>
            선택한 조건에 해당하는 방송 이력이 없습니다. 기간을 늘리거나 필터를 줄여 보세요.
          </div>
        )}

        {s && data && (
          <>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <MetricCard label="누적 뷰어쉽" value={compact(s.viewership)} unit="시간" accent
                          sub={`${rangeLabel} 동안의 총 시청 시간`} />
              <MetricCard label="평균 동시 시청자" value={compact(s.avg_viewers)} unit="명"
                          sub={`방송 채널 평균 ${nf(s.avg_channels)}개`} />
              <MetricCard label="최고 동시 시청자" value={compact(s.peak_viewers)} unit="명"
                          sub={peakWhen ? `${peakWhen}시 기록` : "-"} />
              <MetricCard label="주요 카테고리" value={s.top_category || "-"}
                          sub={s.top_category ? `뷰어쉽 점유율 ${s.top_category_share}%` : "집계된 카테고리 없음"} />
            </div>

            <div className="rounded-2xl border border-gray-800/80 p-5" style={{ background: PANEL }}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="section-title">시청자 · 채널 수 추이</h3>
                  <p className="mt-1 text-sm text-muted">
                    {data.bucket === "hour" ? "시간" : "일"} 단위 평균입니다.
                    분석 대상 채널 {nf(s.total_channels)}개.
                  </p>
                </div>
                <div className="flex gap-1 rounded-xl border border-gray-800 p-1">
                  {([["viewers", "시청자 수"], ["channels", "방송 채널 수"], ["both", "이중축 함께"]] as const)
                    .map(([k, label]) => (
                      <button key={k} onClick={() => setMode(k)}
                              className="rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors"
                              style={{ background: mode === k ? "rgba(0,255,163,0.12)" : "transparent",
                                       color: mode === k ? GREEN : "rgb(156,163,175)" }}>
                        {label}
                      </button>
                    ))}
                </div>
              </div>
              <div className="mt-4">
                <TrendChart points={data.series} bucket={data.bucket} mode={mode} />
              </div>
              <div className="mt-2 flex flex-wrap gap-4 text-[11px] text-gray-400">
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-4 rounded" style={{ background: GREEN }} /> 평균 동시 시청자(좌축)
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-0.5 w-4 rounded" style={{ background: CYAN }} /> 방송 채널 수(우축)
                </span>
              </div>
              <Sub>
                * 각 시점의 동시 시청자는 그 시각에 방송 중이던 채널의 평균 시청자를 모두 더한 값입니다.
                수집 주기(약 10분) 사이의 변동은 반영되지 않습니다.
              </Sub>
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <BarBlock title="시간대별 평균 시청자"
                        desc="선택한 기간 중 어느 시간대에 시청자가 가장 많았는지 보여 줍니다."
                        note="* 한국 시간(KST) 기준이며, 기간 내 같은 시각의 평균입니다."
                        bars={data.hourly.map((h) => ({
                          label: `${h.hour}시`, value: h.avg_viewers, samples: h.samples }))} />
              <BarBlock title="요일별 트래픽 분포"
                        desc="요일에 따라 시청자 규모가 어떻게 달라지는지 비교합니다."
                        note="* 기간이 짧으면 해당 요일이 한 번밖에 포함되지 않아 편차가 클 수 있습니다."
                        bars={data.dow.map((d) => ({
                          label: DOW_LABEL[d.dow], value: d.avg_viewers, samples: d.samples }))} />
            </div>

            <DataTable rows={data.table} />

            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-400">
              <span className="flex items-center gap-1"><CalendarDays size={12} />
                집계 구간 {new Date(data.start * 1000).toLocaleDateString("ko-KR")} ~ {new Date(data.end * 1000).toLocaleDateString("ko-KR")}
              </span>
              <span className="flex items-center gap-1"><Clock size={12} />
                장기 집계는 시간 단위 롤업으로 보관하며, 보관 기간을 넘어선 과거 구간은 조회되지 않습니다.
              </span>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
