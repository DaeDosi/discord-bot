"use client";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { BarChart3, ScatterChart, ChevronDown, RotateCcw, ZoomIn, ZoomOut } from "lucide-react";

import StreamerAvatar from "./StreamerAvatar";

// 랭킹 테이블 상단 요약 차트. 외부 차트 라이브러리 없이 SVG/div로 그린다
// (vis-network 도입 때 확인했듯 차트 라이브러리는 번들이 크다).
// 전체 스트리머 랭킹과 신규 스트리머 랭킹이 공유하므로, Y축 지표를 주입받는다:
//   전체 → 24시간 팔로워 증가량 / 신규 → 성장률(%). RisingNewcomer에는
//   follower_prev24h가 없어 같은 축을 만들 수 없기 때문이다.

const GREEN = "#00FFA3";
const CYAN  = "#06B6D4";
const UP = "#10B981", DOWN = "#EF4444";
const nf = (n: number) => n.toLocaleString("ko-KR");
const DAY_MS = 24 * 3600 * 1000;

export interface ChartRow {
  chzzk_channel_id: string;
  channel_name: string;
  channel_image_url: string;
  concurrent_viewers: number;
  follower_count: number;
  category_name: string;
  dur: { ms: number; label: string };
  /** 막대 우측 변동률 — 전체 랭킹은 직전 수집 대비, 신규 랭킹은 성장률 */
  deltaPct?: number | null;
  /** 산점도 Y값 — 전체는 팔로워 증가량(명), 신규는 성장률(%) */
  yValue?: number | null;
}

export interface YAxisSpec {
  label: string;          // 예: '팔로워 증가량'
  unit: string;           // 예: '명' / '%'
  /** 값 차이가 커서 로그 스케일을 쓸지 (팔로워 증가량=true, 성장률=false) */
  log: boolean;
  tooltip: string;        // 툴팁 항목 이름
}

// 부호를 유지하는 로그 — 0과 음수를 다룰 수 있어야 한다(팔로워 감소/성장률 하락)
const slog = (v: number) => Math.sign(v) * Math.log10(1 + Math.abs(v));
const compact = (v: number) => {
  const a = Math.abs(v);
  if (a >= 10000) return `${(v / 1000).toFixed(0)}k`;
  if (a >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return `${Math.round(v)}`;
};
const signed = (v: number) => (v > 0 ? `+${compact(v)}` : compact(v));

// ── 막대 툴팁 ────────────────────────────────────────────────────────────────
// 예전에는 행 안에 absolute + group-hover로 띄웠는데, 이 카드의 접기/펼치기
// 애니메이터가 `overflow:hidden` + `max-height`를 걸고 있어서 9~10위처럼 아래쪽
// 행의 툴팁이 그 경계에서 잘렸다. clipping context 안에서는 z-index를 아무리
// 올려도 빠져나갈 수 없으므로, body로 Portal을 보내고 position:fixed로 띄운다.
const TIP_GAP = 8;      // 앵커와의 간격
const TIP_EDGE = 8;     // 뷰포트 가장자리 최소 여백

function BarTooltip({ anchor, children }:
  { anchor: HTMLElement | null; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const place = useCallback(() => {
    const el = ref.current;
    if (!el || !anchor) return;
    const a = anchor.getBoundingClientRect();
    const { width: w, height: h } = el.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;

    // 아래를 기본으로 하되 공간이 모자라면 위로 뒤집는다(8~10위 대응).
    // 위도 모자라면 더 넓은 쪽에 붙이고 화면 안으로 clamp한다.
    const below = a.bottom + TIP_GAP;
    const above = a.top - h - TIP_GAP;
    let top = below;
    if (below + h > vh - TIP_EDGE) {
      top = above >= TIP_EDGE
        ? above
        : (a.top > vh - a.bottom ? TIP_EDGE : vh - h - TIP_EDGE);
    }
    top = Math.max(TIP_EDGE, Math.min(top, vh - h - TIP_EDGE));

    // 가로: 막대가 시작되는 지점에 맞추되 좌우로 삐져나가지 않게 clamp
    const left = Math.max(TIP_EDGE, Math.min(a.left + 140, vw - w - TIP_EDGE));
    setPos({ top, left });
  }, [anchor]);

  // 레이아웃 확정 직후 측정 → 첫 프레임부터 올바른 위치에 뜬다(깜빡임 없음)
  useLayoutEffect(() => { setPos(null); place(); }, [place]);

  // 스크롤·리사이즈로 앵커가 움직이면 다시 계산한다(fixed라 따라오지 않는다).
  // 캡처 단계로 듣어 내부 스크롤 컨테이너의 스크롤도 놓치지 않는다.
  useEffect(() => {
    if (!anchor) return;
    const on = () => place();
    window.addEventListener("scroll", on, true);
    window.addEventListener("resize", on);
    return () => {
      window.removeEventListener("scroll", on, true);
      window.removeEventListener("resize", on);
    };
  }, [anchor, place]);

  if (!anchor || typeof document === "undefined") return null;
  return createPortal(
    <div ref={ref} role="tooltip"
      // pointer-events:none — 툴팁이 커서를 덮으면 hover가 풀렸다 걸렸다 하며 깜빡인다
      className="pointer-events-none fixed whitespace-nowrap rounded-lg border border-border
                 bg-bg-card px-3 py-2 text-[11px] leading-relaxed text-fg shadow-2xl"
      style={{ top: pos?.top ?? 0, left: pos?.left ?? 0, zIndex: 60,
               visibility: pos ? "visible" : "hidden" }}>
      {children}
    </div>,
    document.body);
}

// ── 컴포넌트 1: Top 10 수평 막대 ─────────────────────────────────────────────
function BarPanel({ rows, onPick, deltaName }:
  { rows: ChartRow[]; onPick: (id: string) => void; deltaName: string }) {
  const top = rows.slice(0, 10);
  // 어떤 행의 툴팁을 띄울지 — 앵커 엘리먼트를 들고 있어야 스크롤 후 재계산이 된다
  const [hot, setHot] = useState<{ id: string; el: HTMLElement } | null>(null);
  const touching = useRef(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // 모바일: 바깥을 누르면 닫는다(마우스가 없어 mouseleave가 오지 않는다).
  // 판정 기준은 '패널 바깥'이어야 한다. 눌린 행(hot.el)만 기준으로 삼으면, 다른 행을
  // 탭했을 때 이 document 리스너가 행의 React 핸들러보다 나중에 돌면서 방금 연 툴팁을
  // 곧바로 닫아버린다(행 간 전환이 안 되고 깜빡이기만 한다).
  useEffect(() => {
    if (!hot) return;
    const close = (e: Event) => {
      if (!panelRef.current?.contains(e.target as Node)) setHot(null);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [hot]);

  // 목록이 바뀌어(정렬 변경 등) 앵커가 사라진 행을 가리키고 있을 수 있다
  const hotRow = hot ? top.find((r) => r.chzzk_channel_id === hot.id) : null;

  // 막대 길이는 '상위 10명 안에서의 상대 위치'로 그린다.
  // 0부터 최대값까지 절대 스케일로 그리면 값이 서로 비슷할 때(예: 1위 1800% / 2위 1700%,
  // 또는 시청자 12,000 / 11,800) 막대가 전부 꽉 찬 것처럼 보여 순위 차이가 안 읽힌다.
  // 최소값에도 바닥 폭(MIN_W)을 줘서 꼴찌 막대가 사라지지 않게 한다.
  const MIN_W = 14;
  const vals = top.map((r) => r.concurrent_viewers);
  const max = Math.max(...vals, 1), min = Math.min(...vals);
  const barPct = (v: number) =>
    max === min ? 100 : MIN_W + ((v - min) / (max - min)) * (100 - MIN_W);

  return (
    <div ref={panelRef} className="space-y-3">
      {top.map((r, i) => (
        <div key={r.chzzk_channel_id} className="relative flex items-center gap-2"
          // 마우스: 행에 들어오면 열고 나가면 닫는다. 행 사이를 빠르게 오가도
          // 앵커(el)가 그 행으로 즉시 교체되므로 이전 스트리머가 남지 않는다.
          onMouseEnter={(e) => {
            if (touching.current) return;
            setHot({ id: r.chzzk_channel_id, el: e.currentTarget });
          }}
          onMouseLeave={() => { if (!touching.current) setHot(null); }}
          // 터치: 탭하면 열리고, 바깥을 누르면 닫힌다(위 useEffect)
          onPointerDown={(e) => {
            if (e.pointerType !== "touch") return;
            touching.current = true;
            const el = e.currentTarget;
            setHot((p) => (p?.id === r.chzzk_channel_id ? null : { id: r.chzzk_channel_id, el }));
          }}>
          <span className="w-5 shrink-0 text-right text-[11px] tabular-nums text-muted">{i + 1}</span>
          {/* 이 Top 10 목록은 랭킹 표보다 **위**에 있어 초기 화면을 차지한다.
              표만 eager로 바꾸고 여기를 lazy로 두면 정작 보이는 아바타가
              늦게 뜬다(실측으로 확인). 같은 컴포넌트로 통일한다. */}
          <StreamerAvatar src={r.channel_image_url} index={i} />
          <button type="button" onClick={() => onPick(r.chzzk_channel_id)}
            className="w-[64px] shrink-0 truncate text-left text-xs font-semibold text-fg
                       transition-colors hover:text-accent sm:w-[88px] md:w-[104px]">
            {r.channel_name}
          </button>

          {/* min-w — 고정폭(이름 + 우측 지표)의 합이 좁은 화면의 카드 폭을 넘으면
              flex-1인 이 트랙이 0px로 짜부라져 막대가 아예 사라진다. */}
          <div className="h-3.5 min-w-[36px] flex-1 overflow-hidden rounded bg-bg-hover">
            <div className="h-full rounded transition-all"
                 style={{ width: `${barPct(r.concurrent_viewers)}%`,
                          background: `linear-gradient(90deg, ${GREEN}, ${CYAN})` }} />
          </div>

          {/* 증감률과 시청자 수는 별도 요소 + gap으로 띄운다(문자열 공백 금지).
              폭은 '고정'이어야 한다 — min-width로 두면 1위처럼 여섯 자리(128,400명)인
              행만 이 칸이 넓어지고, 그만큼 flex-1인 막대 트랙이 짧아져 1위 막대만
              안쪽에서 끝난다. 칸을 고정하면 모든 행의 막대 길이 기준이 같아진다. */}
          <div className="flex w-[112px] shrink-0 items-center justify-end gap-2
                          tabular-nums sm:w-[150px] sm:gap-3 md:w-[176px] md:gap-4">
            <span className="flex-1 whitespace-nowrap text-right text-[11px] font-semibold
                             sm:text-[13px] md:text-sm"
                  style={{ color: r.deltaPct == null || r.deltaPct === 0
                                  ? "rgb(var(--color-muted-rgb))"
                                  : r.deltaPct > 0 ? UP : DOWN }}>
              {r.deltaPct == null ? "-"
                : r.deltaPct === 0 ? "0.0%"
                : `${r.deltaPct > 0 ? "▲" : "▼"} ${Math.abs(r.deltaPct).toFixed(1)}%`}
            </span>
            <span className="w-[60px] shrink-0 whitespace-nowrap text-right text-[12px]
                             font-extrabold text-fg sm:w-[72px] sm:text-sm
                             md:w-[86px] md:text-[15px]">
              {nf(r.concurrent_viewers)}명
            </span>
          </div>
        </div>
      ))}

      {hotRow && (
        <BarTooltip anchor={hot!.el}>
          <b className="block">{hotRow.channel_name}</b>
          방송 시간 {hotRow.dur.label} · 팔로워{" "}
          {hotRow.follower_count > 0 ? `${nf(hotRow.follower_count)}명` : "미집계"}
          {hotRow.deltaPct != null && <> · {deltaName} {hotRow.deltaPct >= 0 ? "+" : ""}{hotRow.deltaPct.toFixed(1)}%</>}
          {hotRow.category_name && <><br />카테고리 {hotRow.category_name}</>}
        </BarTooltip>
      )}
    </div>
  );
}

// ── 컴포넌트 2: 성장성 산점도 ────────────────────────────────────────────────
const W = 900, H = 580, P = { l: 84, r: 60, t: 54, b: 60 };
const MAX_RAD = 18 * 1.3;   // 버블 최대 반지름(호버 확대 1.3배 포함)
const EDGE_PAD = 15;        // 원 전체가 박스 안에 들어오도록 강제할 여백
// 한 화면에 그릴 최대 노드 수 — 누적 랭킹은 300개를 받아오는데 전부 그리면 렉이 걸린다
const MAX_NODES = 150;

// 4분면 정의 — 중앙값 십자선 기준. 사용자가 스트리머 포지션을 바로 읽을 수 있게
// 구역마다 이름을 붙인다. X=체급(시청자), Y=유입(팔로워 증가/성장률).
const QUADRANTS = [
  { key: "q1", text: "슈퍼 라이징 (대세)",   color: "#00FFA3", at: "tr" },
  { key: "q2", text: "라이징 루키 (유망주)", color: "#22D3EE", at: "tl" },
  { key: "q3", text: "초기 탐색 구간",       color: "#9CA3AF", at: "bl" },
  { key: "q4", text: "콘크리트 충성층",      color: "#C084FC", at: "br" },
] as const;

// SVG에는 텍스트 자동 크기 측정이 없어 뱃지 폭을 추정한다.
// 한글/이모지는 라틴 문자보다 넓어 따로 계산한다(fontSize 10 기준).
function badgeWidth(text: string) {
  let w = 0;
  for (const ch of text) {
    if (/[가-힣]/.test(ch)) w += 10.5;           // 한글
    else if (ch.codePointAt(0)! > 0x1000) w += 12;        // 이모지 등
    else w += 5.4;
  }
  return Math.round(w) + 18;
}

function ScatterPanel({ rows, onPick, y }:
  { rows: ChartRow[]; onPick: (id: string) => void; y: YAxisSpec }) {
  const [hover, setHover] = useState<string | null>(null);
  // 보이는 영역(정규화 0~1 도메인). 기본값 0~1 = 전체가 한 화면에 들어온 상태.
  // 휠 확대/축소가 이 도메인을 바꾸므로 눈금 라벨도 함께 정확해진다.
  const [view, setView] = useState({ x0: 0, x1: 1, y0: 0, y1: 1 });
  const svgRef = useRef<SVGSVGElement>(null);

  const data = useMemo(() => {
    const usable = rows.filter((r) => r.yValue != null && isFinite(r.yValue));
    if (usable.length === 0) return null;

    const xr = usable.map((r) => Math.log10(Math.max(1, r.concurrent_viewers)));
    const yr = usable.map((r) => (y.log ? slog(r.yValue as number) : (r.yValue as number)));
    const maxDur = Math.max(1, ...usable.map((r) => r.dur.ms));

    // 도메인을 '중앙값 기준 대칭'으로 만든다.
    //
    // 이게 핵심 수정이다. 예전에는 [min,max]를 그대로 0~1에 매핑하고 십자선을 중앙값 위치에
    // 찍었는데, 시청자 분포가 심하게 한쪽으로 쏠려 있어(대부분 소규모) 십자선이 구석으로
    // 밀리고 한 분면만 비정상적으로 커졌다. 중앙값이 항상 도메인 정중앙(0.5)에 오도록
    // 반폭을 잡으면 십자선이 뷰포트 정중앙에 놓이면서 4분면이 정확히 균등 면적이 되고,
    // '중앙값보다 크다/작다'는 분면의 의미도 그대로 유지된다.
    //
    // 양끝에는 PAD(20%)의 여유를 둔다 — 최외곽 노드가 버블 반지름만큼 잘리던 문제 해결.
    const PAD = 0.2;
    const mid = (a: number[]) => { const s = [...a].sort((p, q) => p - q); return s[Math.floor(s.length / 2)]; };
    // 중앙값 양쪽에 '각각 다른 스케일'을 쓰는 구간 선형 매핑.
    // 양쪽 반폭을 같게 잡으면(대칭 도메인) 중앙값이 0.5에 오긴 하지만, 데이터가 한쪽으로
    // 쏠려 있을 때 반대편 절반이 통째로 비어 캔버스를 절반밖에 못 쓴다.
    // 아래→0~0.5, 위→0.5~1 로 각각 펼치면 중앙값은 정확히 0.5에 고정되면서 사방을 꽉 채운다.
    const axis = (vals: number[]) => {
      const c = mid(vals);
      const lo0 = Math.min(...vals), hi0 = Math.max(...vals);
      const lo = lo0 - Math.max(1e-6, c - lo0) * PAD;   // 양끝 20% 여유 — 버블 반지름 잘림 방지
      const hi = hi0 + Math.max(1e-6, hi0 - c) * PAD;
      return (v: number) => v <= c
        ? 0.5 * ((v - lo) / Math.max(1e-6, c - lo))
        : 0.5 + 0.5 * ((v - c) / Math.max(1e-6, hi - c));
    };
    const nx = axis(xr), ny = axis(yr);

    const pts = usable.map((r, i) => {
      // 지터: 완전히 같은 좌표에 뭉치는 것을 막되 채널 id로 결정론적으로 만들어
      // 리렌더마다 위치가 흔들리지 않게 한다(±3px 상당).
      // 도메인에 20% 여유가 있어 더 이상 0~1로 클램프하지 않는다(클램프가 곧 가장자리 겹침이었다).
      let h = 0;
      for (let k = 0; k < r.chzzk_channel_id.length; k++) h = (h * 31 + r.chzzk_channel_id.charCodeAt(k)) >>> 0;
      // 지터 방향/세기는 채널별로 고정하고, 실제 적용량은 렌더 시 확대 배율에 맞춰 키운다
      const ju = ((h % 100) / 100) - 0.5;
      const jv = (((h >> 7) % 100) / 100) - 0.5;
      return {
        r, yv: r.yValue as number,
        x: nx(xr[i]), yn: ny(yr[i]), ju, jv,
        rad: 7 + (Math.min(r.dur.ms, DAY_MS) / Math.min(maxDur, DAY_MS)) * 11,
      };
    });

    // 축 눈금 — 데이터 값으로 만들어 정규화 좌표로 변환(줌해도 라벨이 맞는다)
    const xTickVals = [1, 10, 100, 1000, 10000, 100000].filter(
      (v) => Math.log10(v) >= Math.min(...xr) - 0.3 && Math.log10(v) <= Math.max(...xr) + 0.3);
    const xTicks = xTickVals.map((v) => ({ n: nx(Math.log10(v)), text: compact(v) }));

    const rawYMin = Math.min(...usable.map((r) => r.yValue as number));
    const rawYMax = Math.max(...usable.map((r) => r.yValue as number));
    const cand = y.log
      ? [-10000, -1000, -100, 0, 100, 1000, 10000, 100000]
      : [-100, -50, 0, 50, 100, 200, 500, 1000, 2000];
    const yTicks = cand
      .filter((v) => v >= rawYMin - Math.abs(rawYMin) * 0.05 && v <= rawYMax + Math.abs(rawYMax) * 0.05)
      .map((v) => ({ n: ny(y.log ? slog(v) : v), text: `${signed(v)}${y.unit === "%" ? "%" : ""}` }));

    // 도메인을 중앙값 대칭으로 잡았으므로 십자선은 항상 정확히 0.5 = 뷰포트 정중앙이다.
    // 결과적으로 4분면 면적이 1:1:1:1로 균등하게 나뉜다.
    return { pts, xTicks, yTicks, medX: 0.5, medY: 0.5 };
  }, [rows, y]);

  if (!data) {
    return (
      <p className="py-12 text-center text-sm text-muted">
        {y.label}을 계산할 데이터가 아직 없습니다.
      </p>
    );
  }

  // 정규화 좌표 → 화면 좌표 (현재 보이는 도메인 기준)
  const sx = (n: number) => P.l + ((n - view.x0) / (view.x1 - view.x0)) * (W - P.l - P.r);
  const sy = (n: number) => H - P.b - ((n - view.y0) / (view.y1 - view.y0)) * (H - P.t - P.b);
  // 여유 없이 엄격히 판정한다. 예전엔 ±0.02를 허용해서, 보이는 범위를 살짝 벗어난
  // 눈금/격자선이 플롯 밖(축 라벨 영역)에 최대 15px까지 그려졌다.
  const inView = (n: number, a: number, b: number) => n >= a && n <= b;
  // Semantic zoom: 확대할수록(보이는 도메인이 좁아질수록) 지터를 키워
  // 뭉쳐 있던 노드가 자동으로 벌어지게 한다. 기본 배율에서는 겹침 완화 수준으로만 작동.
  const spread = 0.012 * Math.min(1, view.x1 - view.x0) ** 0.6;
  const jitX = (p: { ju: number }) => p.ju * spread;
  const jitY = (p: { jv: number }) => p.jv * spread;

  // 노드 중심이 경계에 붙어도 원 '전체'가 박스 안에 남도록 강제한다.
  // clip-path를 없앤 대신 이 보정이 잘림을 막는다.
  // 노드는 '플롯 사각형' 안에만 머문다. 예전엔 SVG 전체(W,H) 기준으로 가둬서
  // 확대하면 축 라벨과 4분면 뱃지 위로 버블이 삐져나왔다.
  const clampX = (v: number, rad: number) =>
    Math.min(W - P.r - rad, Math.max(P.l + rad, v));
  const clampY = (v: number, rad: number) =>
    Math.min(H - P.b - rad, Math.max(P.t + rad, v));

  // 확대/축소는 버튼으로만 한다.
  // 휠에 줌을 걸면 두 가지 중 하나를 반드시 잃는다: preventDefault를 하면 차트 위에서
  // 페이지 스크롤이 막히고(우측 스크롤바로만 이동 가능해짐), 하지 않으면 줌과 스크롤이
  // 동시에 일어난다. 그래서 휠은 브라우저에 그대로 넘기고 줌은 명시적 버튼으로 분리했다.
  const zoomBy = (k: number) => setView((v) => {
    const cx = (v.x0 + v.x1) / 2, cy = (v.y0 + v.y1) / 2;   // 화면 중심 기준 확대/축소
    const w = Math.min(1, Math.max(0.05, (v.x1 - v.x0) * k));
    const h = Math.min(1, Math.max(0.05, (v.y1 - v.y0) * k));
    const x0 = Math.min(1 - w, Math.max(0, cx - w / 2));
    const y0 = Math.min(1 - h, Math.max(0, cy - h / 2));
    return { x0, x1: x0 + w, y0, y1: y0 + h };
  });

  // 드래그 이동(팬) — 확대한 상태에서 다른 구역을 보기 위해 필요하다
  const drag = useRef<{ mx: number; my: number; v: typeof view } | null>(null);
  const [dragging, setDragging] = useState(false);
  // 포인터 이벤트 + setPointerCapture를 쓰는 이유:
  //  - preventDefault로 텍스트 선택과 <image>의 네이티브 드래그를 막는다.
  //    이 둘이 브라우저의 자동 스크롤을 유발해 드래그 중 화면이 갑자기 튀었다.
  //  - 캡처를 걸면 커서가 SVG 밖으로 나가도 이벤트가 계속 들어와, 드래그가 끊긴 채
  //    dragging 상태만 남는 문제도 사라진다.
  const onDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
    drag.current = { mx: e.clientX, my: e.clientY, v: view };
    setDragging(true);
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current, svg = svgRef.current;
    if (!d || !svg) return;
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    const w = d.v.x1 - d.v.x0, h = d.v.y1 - d.v.y0;
    const dx = ((e.clientX - d.mx) / rect.width) * w;
    const dy = ((e.clientY - d.my) / rect.height) * h;
    const x0 = Math.min(1 - w, Math.max(0, d.v.x0 - dx));
    const y0 = Math.min(1 - h, Math.max(0, d.v.y0 + dy));
    setView({ x0, x1: x0 + w, y0, y1: y0 + h });
  };
  const endDrag = (e?: React.PointerEvent) => {
    if (e) {
      const el = e.currentTarget as SVGSVGElement;
      if (el.hasPointerCapture?.(e.pointerId)) el.releasePointerCapture(e.pointerId);
    }
    drag.current = null;
    setDragging(false);
  };

  const zoomed = view.x0 !== 0 || view.x1 !== 1 || view.y0 !== 0 || view.y1 !== 1;

  // clip-path를 없앴으므로 '잘라내는' 대신 보이는 영역 밖 노드를 아예 '걸러낸다'.
  // 걸러내지 않으면 clampX/Y가 화면 밖 노드를 전부 경계에 밀어붙여 테두리에 쌓인다.
  // 여유(MARGIN)를 둬서 경계에 반쯤 걸친 노드는 그대로 보이게 한다.
  // 보이는 노드 계산은 view/spread에만 의존한다. 예전에는 매 렌더마다 다시 돌았는데,
  // 호버는 상태 변경이라 렌더를 유발한다 → 마우스를 움직일 때마다 수백 개 노드를
  // 필터·정렬하고 clipPath까지 새로 만들어 눈에 띄게 버벅였다.
  const visible = useMemo(() => {
    const MARGIN = 0.02;
    const inside = data.pts.filter((p) => {
      const px = p.x + p.ju * spread, py = p.yn + p.jv * spread;
      return px >= view.x0 - MARGIN && px <= view.x1 + MARGIN
          && py >= view.y0 - MARGIN && py <= view.y1 + MARGIN;
    });
    // 화면에 수백 개를 한꺼번에 그리면 겹쳐서 읽히지도 않고 렌더 비용만 커진다.
    // 시청자 규모 상위부터 잘라 낸다(확대하면 그 구역의 노드가 다시 채워진다).
    return inside.length > MAX_NODES
      ? [...inside].sort((a, b) => b.r.concurrent_viewers - a.r.concurrent_viewers).slice(0, MAX_NODES)
      : inside;
  }, [data, view, spread]);

  // z-order: 예전에는 호버할 때마다 배열을 재정렬했는데, 배열 정체성이 바뀌면 React가
  // 보이는 노드 전부를 다시 렌더해 버벅였다. 이제 목록은 고정 순서로 그리고,
  // 호버된 노드만 맨 위에 한 번 더 덧그린다(SVG는 문서 순서대로 페인트).
  const hp = visible.find((p) => p.r.chzzk_channel_id === hover);

  return (
    <div className="relative">
      {/* 확대 / 축소 / 전체 보기 — 휠 대신 명시적 버튼으로 분리했다 */}
      <div className="mb-1 flex items-center justify-end gap-1.5">
        <span className="mr-1 text-[11px] text-muted">
          {zoomed ? "드래그해서 이동할 수 있습니다" : "확대하면 드래그로 이동할 수 있습니다"}
        </span>
        <button type="button" onClick={() => zoomBy(1 / 1.4)} title="확대"
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border
                     text-muted transition-colors hover:text-fg hover:bg-bg-hover">
          <ZoomIn size={13} />
        </button>
        <button type="button" onClick={() => zoomBy(1.4)} title="축소"
          disabled={!zoomed}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border
                     text-muted transition-colors hover:text-fg hover:bg-bg-hover
                     disabled:opacity-30 disabled:hover:bg-transparent">
          <ZoomOut size={13} />
        </button>
        <button type="button" onClick={() => setView({ x0: 0, x1: 1, y0: 0, y1: 1 })}
          disabled={!zoomed}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px]
                     text-muted transition-colors hover:text-fg disabled:opacity-30">
          <RotateCcw size={11} /> 전체 보기
        </button>
      </div>

      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" height={H}
           className="overflow-visible"
           style={{ display: "block", touchAction: "none", userSelect: "none",
                    WebkitUserSelect: "none",
                    cursor: dragging ? "grabbing" : zoomed ? "grab" : "default" }}
           onPointerDown={onDown} onPointerMove={onMove}
           onPointerUp={endDrag} onPointerCancel={endDrag}
           onMouseLeave={() => setHover(null)}>
        <defs>
          {/* 4분면 미세 배경 — 각 구역 바깥쪽 모서리로 갈수록 옅게 진해진다 */}
          {QUADRANTS.map((q) => {
            const [x1, y1, x2, y2] =
              q.at === "tr" ? [0, 1, 1, 0] : q.at === "tl" ? [1, 1, 0, 0]
              : q.at === "bl" ? [1, 0, 0, 1] : [0, 0, 1, 1];
            return (
              <linearGradient key={q.key} id={`${q.key}-grad`} x1={x1} y1={y1} x2={x2} y2={y2}>
                <stop offset="0%" stopColor={q.color} stopOpacity="0.02" />
                <stop offset="100%" stopColor={q.color} stopOpacity={q.key === "q1" ? "0.16" : "0.10"} />
              </linearGradient>
            );
          })}
          {/* 호버 오버레이 전용 클립 — 한 번에 하나뿐이라 재생성 비용이 없다 */}
          {hp && (() => {
            const rr = hp.rad * 1.3;
            return (
              <clipPath id="c-hover">
                <circle cx={clampX(sx(hp.x + hp.ju * spread), rr)}
                        cy={clampY(sy(hp.yn + hp.jv * spread), rr)} r={rr} />
              </clipPath>
            );
          })()}
          {visible.map((p) => (
            <clipPath key={p.r.chzzk_channel_id} id={`c-${p.r.chzzk_channel_id}`}>
              {/* 반지름은 호버와 무관하게 rad로 고정한다. 호버마다 defs를 다시 만들면
                  보이는 노드 전부의 clipPath가 재생성돼 버벅인다.
                  이미지도 같은 rad로 그려야 정사각형 모서리가 원 밖으로 드러나지 않는다. */}
              <circle cx={clampX(sx(p.x + p.ju * spread), p.rad)}
                      cy={clampY(sy(p.yn + p.jv * spread), p.rad)} r={p.rad} />
            </clipPath>
          ))}
        </defs>

        {/* clip-path 없음 — 가장자리 노드가 반쯤 잘리던 원인이라 제거했다.
            대신 아래 clampXY로 원 전체가 박스 안에 들어오도록 좌표를 보정한다. */}
        <g>
          {/* 4분면 배경 — 중앙값 십자선을 기준으로 나뉘며 줌/팬을 따라간다 */}
          {(() => {
            const mx = Math.min(W - P.r, Math.max(P.l, sx(data.medX)));
            const my = Math.min(H - P.b, Math.max(P.t, sy(data.medY)));
            const L = P.l, R = W - P.r, T = P.t, B = H - P.b;
            const rects = [
              { k: "q1", x: mx, y: T,  w: R - mx, h: my - T },
              { k: "q2", x: L,  y: T,  w: mx - L, h: my - T },
              { k: "q3", x: L,  y: my, w: mx - L, h: B - my },
              { k: "q4", x: mx, y: my, w: R - mx, h: B - my },
            ];
            return rects.map((r) => (
              <rect key={r.k} x={r.x} y={r.y} width={Math.max(0, r.w)} height={Math.max(0, r.h)}
                    fill={`url(#${r.k}-grad)`} />
            ));
          })()}

          {/* 눈금 격자 */}
          {data.yTicks.filter((t) => inView(t.n, view.y0, view.y1)).map((t, i) => (
            <line key={`gy${i}`} x1={P.l} y1={sy(t.n)} x2={W - P.r} y2={sy(t.n)}
                  stroke="rgb(var(--color-border-rgb))" strokeWidth="1" opacity="0.45" />
          ))}
          {data.xTicks.filter((t) => inView(t.n, view.x0, view.x1)).map((t, i) => (
            <line key={`gx${i}`} x1={sx(t.n)} y1={P.t} x2={sx(t.n)} y2={H - P.b}
                  stroke="rgb(var(--color-border-rgb))" strokeWidth="1" opacity="0.3" />
          ))}

          {/* 4분면 십자 가이드(중앙값).
              clip-path를 제거한 뒤로 확대/이동하면 기준선이 플롯 밖(축 라벨·뱃지 영역)까지
              그려졌다. 기준값이 보이는 범위를 벗어나면 아예 그리지 않는다 — 화면에 없는
              경계를 선으로 표시할 이유가 없다. */}
          {inView(data.medX, view.x0, view.x1) && (
            <line x1={sx(data.medX)} y1={P.t} x2={sx(data.medX)} y2={H - P.b}
                  stroke="rgb(var(--color-muted-rgb))" strokeWidth="1" strokeDasharray="3 3" opacity="0.55" />
          )}
          {inView(data.medY, view.y0, view.y1) && (
            <line x1={P.l} y1={sy(data.medY)} x2={W - P.r} y2={sy(data.medY)}
                  stroke="rgb(var(--color-muted-rgb))" strokeWidth="1" strokeDasharray="3 3" opacity="0.55" />
          )}

          {/* 4분면 라벨 뱃지 — 데이터가 아니라 플롯 모서리에 고정한다.
              중앙값 선을 따라 움직이게 하면 확대 시 화면 밖으로 밀려 안 보인다. */}
          {QUADRANTS.map((q) => {
            const bw = badgeWidth(q.text), bh = 20, pad = 8;
            const x = q.at === "tr" || q.at === "br" ? W - P.r - pad - bw : P.l + pad;
            const yTop = P.t + pad;
            const yBot = H - P.b - pad - bh;
            const yy = q.at === "tr" || q.at === "tl" ? yTop : yBot;
            return (
              <g key={q.key} style={{ pointerEvents: "none" }}>
                <rect x={x} y={yy} width={bw} height={bh} rx={10}
                      fill={q.color} fillOpacity="0.10"
                      stroke={q.color} strokeOpacity="0.28" strokeWidth="1" />
                <text x={x + bw / 2} y={yy + 14} textAnchor="middle" fontSize="10"
                      fill={q.color} fillOpacity="0.95">{q.text}</text>
              </g>
            );
          })}

          {/* 버블 */}
          {visible.map((p) => {
            const on = hover === p.r.chzzk_channel_id;
            const rr = p.rad;   // 호버 확대는 아래 오버레이가 담당
            const cxp = clampX(sx(p.x + jitX(p)), rr), cyp = clampY(sy(p.yn + jitY(p)), rr);
            return (
              // onMouseLeave가 없으면 SVG를 완전히 벗어나기 전까지 툴팁이 계속 남는다.
              // 겹친 버블 사이를 옮길 때는 leave(null) 뒤 enter(다음 id)가 이어서 실행되므로
              // 깜빡임 없이 대상만 바뀐다.
              <g key={p.r.chzzk_channel_id} style={{ cursor: "pointer" }}
                 onMouseEnter={() => setHover(p.r.chzzk_channel_id)}
                 onMouseLeave={() => setHover((cur) => (cur === p.r.chzzk_channel_id ? null : cur))}
                 onClick={() => onPick(p.r.chzzk_channel_id)}>
                <circle cx={cxp} cy={cyp} r={rr + 2}
                        fill={on ? GREEN : "rgba(0,255,163,0.16)"} opacity={on ? 0.45 : 1} />
                {p.r.channel_image_url && (
                  <image href={p.r.channel_image_url}
                         x={cxp - rr} y={cyp - rr} width={rr * 2} height={rr * 2}
                         clipPath={`url(#c-${p.r.chzzk_channel_id})`}
                         preserveAspectRatio="xMidYMid slice" />
                )}
                <circle cx={cxp} cy={cyp} r={rr} fill="none"
                        stroke={on ? "#fff" : GREEN} strokeWidth={on ? 2 : 1.5} />
              </g>
            );
          })}

          {/* 호버 강조 오버레이 — 목록을 재정렬하지 않고 위에 덧그려 최상단으로 올린다 */}
          {hp && (() => {
            const rr = hp.rad * 1.3;
            const cxp = clampX(sx(hp.x + jitX(hp)), rr), cyp = clampY(sy(hp.yn + jitY(hp)), rr);
            return (
              <g style={{ pointerEvents: "none" }}>
                <circle cx={cxp} cy={cyp} r={rr + 2} fill={GREEN} opacity={0.45} />
                {hp.r.channel_image_url && (
                  <image href={hp.r.channel_image_url} x={cxp - rr} y={cyp - rr}
                         width={rr * 2} height={rr * 2}
                         clipPath="url(#c-hover)"
                         preserveAspectRatio="xMidYMid slice" />
                )}
                <circle cx={cxp} cy={cyp} r={rr} fill="none" stroke="#fff" strokeWidth={2} />
              </g>
            );
          })()}
        </g>

        {/* 축 — 클립 밖에 그려 항상 보이게 한다 */}
        <line x1={P.l} y1={H - P.b} x2={W - P.r} y2={H - P.b} stroke="rgb(var(--color-border-rgb))" />
        <line x1={P.l} y1={P.t} x2={P.l} y2={H - P.b} stroke="rgb(var(--color-border-rgb))" />

        {data.yTicks.filter((t) => inView(t.n, view.y0, view.y1)).map((t, i) => (
          <text key={`ty${i}`} x={P.l - 7} y={sy(t.n) + 3} textAnchor="end" fontSize="10"
                fill="rgb(var(--color-muted-rgb))">{t.text}</text>
        ))}
        {data.xTicks.filter((t) => inView(t.n, view.x0, view.x1)).map((t, i) => (
          <text key={`tx${i}`} x={sx(t.n)} y={H - P.b + 14} textAnchor="middle" fontSize="10"
                fill="rgb(var(--color-muted-rgb))">{t.text}</text>
        ))}

        {/* 축 제목 — Y축은 세로로 회전해 눈금 라벨과 겹치지 않게 한다 */}
        <text x={14} y={(P.t + H - P.b) / 2} fontSize="10" fill="rgb(var(--color-muted-rgb))"
              textAnchor="middle" transform={`rotate(-90 14 ${(P.t + H - P.b) / 2})`}>
          {y.label} ({y.unit})
        </text>
        <text x={(P.l + W - P.r) / 2} y={H - 6} textAnchor="middle" fontSize="10"
              fill="rgb(var(--color-muted-rgb))">현재 시청자 (명, 로그 스케일)</text>
      </svg>

      {/* 다크 툴팁 */}
      {hp && (
        <div className="pointer-events-none absolute z-40 w-[210px] rounded-xl border border-border
                        bg-bg-card/95 p-3 shadow-2xl backdrop-blur"
             style={{ left: `min(${(clampX(sx(hp.x + jitX(hp)), hp.rad) / W) * 100}%, calc(100% - 220px))`,
                      top: Math.min(clampY(sy(hp.yn + jitY(hp)), hp.rad) + 22, H - 40) }}>
          <div className="flex items-center gap-2">
            <span className="h-7 w-7 shrink-0 overflow-hidden rounded-full bg-bg-hover">
              {hp.r.channel_image_url && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={hp.r.channel_image_url} alt="" width={28} height={28} className="h-full w-full object-cover" />
              )}
            </span>
            <b className="truncate text-xs text-fg">{hp.r.channel_name}</b>
          </div>
          <dl className="mt-2 space-y-0.5 text-[11px]">
            <div className="flex justify-between gap-2">
              <dt className="text-muted">현재 시청자</dt>
              <dd className="tabular-nums text-fg">{nf(hp.r.concurrent_viewers)}명</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted">{y.tooltip}</dt>
              <dd className="tabular-nums font-semibold" style={{ color: hp.yv >= 0 ? GREEN : DOWN }}>
                {hp.yv >= 0 ? "+" : ""}{nf(Math.round(hp.yv))}{y.unit}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-muted">방송 시간</dt>
              <dd className="tabular-nums text-fg">{hp.r.dur.label}</dd>
            </div>
          </dl>
        </div>
      )}

      <p className="mt-2 text-[11px] text-muted">
        * 1분면(슈퍼 라이징)과 2분면(라이징 루키)에 위치할수록 체급 대비 {y.label}이 높은
        성장세 스트리머입니다. 4분면(콘크리트 충성층)은 체급은 크지만 신규 유입이 완만한 구간입니다.
      </p>
      <p className="mt-1 text-[11px] text-muted/70">
        점선은 중앙값 기준 4분면 · 버블이 클수록 방송 시간이 깁니다 · 기본 화면에 전체가
        모두 표시됩니다 · 우측 상단 버튼으로 확대/축소하고, 확대한 뒤에는 드래그로 이동합니다
        (확대하면 뭉친 노드가 자동으로 벌어집니다){y.log && " · 값 차이가 커서 두 축 모두 로그 스케일"}.
      </p>
    </div>
  );
}

// ── 래퍼: 접기/펼치기 + 차트 전환 ────────────────────────────────────────────
export default function RankingCharts({ rows, y, deltaName = "변동률" }:
  { rows: ChartRow[]; y: YAxisSpec; deltaName?: string }) {
  const [open, setOpen] = useState(true);
  const [mode, setMode] = useState<"bar" | "scatter">("bar");
  const router = useRouter();
  const pick = (id: string) => router.push(`/stats/streamer/${id}`);

  const tab = (active: boolean) => ({
    background: active ? "rgba(0,255,163,0.1)" : "transparent",
    borderColor: active ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
    color: active ? GREEN : "rgb(var(--color-muted-rgb))",
  });

  return (
    <div className="card !p-4 md:!p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 text-left" aria-expanded={open}>
          <h3 className="section-title">랭킹 요약 차트</h3>
          <ChevronDown size={15} className="text-muted transition-transform"
                       style={{ transform: open ? "none" : "rotate(-90deg)" }} />
          <span className="ml-1 text-[11px] text-muted">{open ? "접기" : "펼치기"}</span>
        </button>

        {open && (
          <div className="flex shrink-0 items-center gap-1.5">
            <button type="button" onClick={() => setMode("bar")}
              className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors"
              style={tab(mode === "bar")}>
              <BarChart3 size={13} /> Top 10 시청자
            </button>
            <button type="button" onClick={() => setMode("scatter")}
              className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors"
              style={tab(mode === "scatter")}>
              <ScatterChart size={13} /> 성장성 분석
            </button>
          </div>
        )}
      </div>

      <div className="overflow-hidden transition-[max-height] duration-300 ease-in-out"
           style={{ maxHeight: open ? 1800 : 0 }}>
        <p className="mb-3 mt-1 text-[11px] text-muted">
          {mode === "bar"
            ? "상위 10명 · 막대는 이 10명 안에서의 상대 길이입니다(1위가 가장 김) · 마우스를 올리면 방송 시간과 팔로워를 볼 수 있습니다."
            : `체급(시청자) 대비 ${y.label}을 한눈에 비교합니다.`}
        </p>
        {rows.length === 0
          ? <p className="py-10 text-center text-sm text-muted">랭킹 데이터가 아직 없습니다.</p>
          : mode === "bar"
            ? <BarPanel rows={rows} onPick={pick} deltaName={deltaName} />
            : <ScatterPanel rows={rows} onPick={pick} y={y} />}
      </div>
    </div>
  );
}
