"use client";
import { useState } from "react";
import { ChevronDown, LineChart as LineIcon, ListOrdered, Gamepad2, Sprout, Radio, Users, Hash, SlidersHorizontal, Trophy } from "lucide-react";

// /stats 좌측 메뉴. 탭은 URL이 아니라 state로 전환된다.
// (route 기반 항목을 지원하던 분기는 /stats/network 제거와 함께 걷어냈다.)
export type Tab =
  | "overview" | "newcomers_analysis" | "period_analysis"
  | "ranking" | "ranking_period" | "newcomers_ranking"
  | "category" | "category_streamers" | "tags"
  | "singcup";

export interface NavItem {
  key: Tab; label: string; icon: React.ReactNode; live?: boolean;
  /** 우측에 골드 'EVENT' 배지를 붙인다(기간 한정 이벤트 메뉴) */
  event?: boolean;
}

// 기간 한정 이벤트 — 그룹에 넣지 않고 검색창과 '실시간 분석' 사이에 단독으로 둔다.
export const EVENT_ITEM: NavItem = {
  key: "singcup", label: "싱드컵", icon: <Trophy size={16} />, event: true,
};

// 랭킹은 한 그룹으로 묶되 기준이 다르다는 것은 LIVE 표시로 구분한다.
// 실시간 랭킹은 최신 수집 사이클 한 장의 동시 시청자 순위라 그 순간 방송 중이었는지에
// 크게 좌우되고, 누적 랭킹은 기간 전체를 집계해 꾸준함이 반영된다.
export const NAV_GROUPS: { header: string | null; items: NavItem[] }[] = [
  { header: "실시간 분석", items: [
    { key: "overview",           label: "전체 스트리머 분석", icon: <LineIcon size={16} />, live: true },
    // 신규(첫 방송 60일 이내)와 소형(평균 시청자 10명 이하)을 한 메뉴로 통합.
    // 두 그룹은 탭 안의 세그먼티드 컨트롤로 전환한다.
    { key: "newcomers_analysis", label: "신규 & 초기 분석", icon: <Sprout size={16} />, live: true },
    // 실시간 한 장이 아니라 기간 누적이지만, '분석' 성격이 같아 같은 그룹에 둔다(LIVE 표시 없음)
    { key: "period_analysis",    label: "기간별 상세 분석",   icon: <SlidersHorizontal size={16} /> },
  ] },
  // 실시간/누적을 한 그룹으로 합치고, 실시간 기준인 항목에만 LIVE 표시를 붙인다
  { header: "랭킹", items: [
    { key: "ranking",           label: "전체 스트리머 랭킹", icon: <Radio size={16} />, live: true },
    { key: "newcomers_ranking", label: "신규 스트리머 랭킹", icon: <Sprout size={16} />, live: true },
    { key: "ranking_period",    label: "기간별 누적 랭킹",   icon: <ListOrdered size={16} /> },
  ] },
  { header: "카테고리", items: [
    { key: "category",            label: "카테고리 분석",     icon: <Gamepad2 size={16} /> },
    { key: "category_streamers",  label: "카테고리별 스트리머", icon: <Users size={16} /> },
    { key: "tags",                label: "태그 검색",         icon: <Hash size={16} /> },
  ] },
];

export const groupHeaderOf = (k: Tab): string | null =>
  NAV_GROUPS.find((g) => g.items.some((i) => i.key === k))?.header ?? null;

// 유효한 탭 키 전체 — ?tab= 로 들어온 값을 검증할 때 쓴다
export const ALL_TABS: Tab[] = [
  ...NAV_GROUPS.flatMap((g) => g.items.map((i) => i.key)),
  EVENT_ITEM.key,
];
export const isTab = (v: string | null): v is Tab =>
  !!v && (ALL_TABS as string[]).includes(v);

const GREEN = "#00FFA3";
const GRAD  = `linear-gradient(135deg, ${GREEN}, #00C2FF)`;
// 이벤트 포인트 — 사이트 기본 포인트(청록)와 충돌하지 않게 골드 계열을 쓴다
const GOLD  = "#FACC15";

export default function StatsNav({
  active, onSelect, children,
}: {
  active: Tab;
  onSelect: (k: Tab) => void;
  /** 메뉴 위에 놓을 요소 (스트리머 검색 등) */
  children?: React.ReactNode;
}) {
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => {
    const h = groupHeaderOf(active); return new Set(h ? [h] : []);
  });
  const toggleGroup = (h: string) =>
    setOpenGroups((p) => { const n = new Set(p); if (n.has(h)) n.delete(h); else n.add(h); return n; });

  const renderItem = (t: NavItem) => {
    const isActive = active === t.key;
    // 이벤트 메뉴는 활성 스타일을 그대로 쓰되 포인트 색만 골드로 바꾼다.
    const point = t.event ? GOLD : GREEN;
    return (
      <button key={t.key} onClick={() => onSelect(t.key)}
        className="relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-left transition-colors w-full border overflow-hidden"
        style={{
          background: isActive ? (t.event ? "rgba(250,204,21,0.10)" : "rgba(0,255,163,0.08)") : "transparent",
          borderColor: isActive ? (t.event ? "rgba(250,204,21,0.40)" : "rgba(0,194,255,0.35)") : "transparent",
        }}>
        {isActive && (
          <span className="absolute left-0 top-0 bottom-0 w-1 rounded-r"
                style={{ background: t.event ? GOLD : GRAD }} />
        )}
        {/* ── 레이아웃 계약 — 세 칸의 역할이 서로 다르다. 하나라도 빠지면 배지가 잘린다 ──
            · 아이콘: 고정 폭 · shrink 금지
            · 라벨:   min-width:0 + truncate — **여기가 유일하게 줄어드는 칸**이다
            · 배지:   shrink-0 (아래 t.live / t.event 분기)
            예전에는 라벨이 `whitespace-nowrap`이고 min-width가 auto(플렉스 기본값)라
            **절대 줄어들지 않았다.** 그래서 아이콘+라벨+배지 합이 210px 컬럼을 넘기는
            순간 `ml-auto` 배지가 오른쪽 밖으로 밀려났고, 버튼의 `overflow-hidden`이
            그걸 잘라냈다. 한글 라벨의 실제 폭은 그 PC가 고른 글꼴에 좌우되므로
            같은 화면 크기라도 PC마다 결과가 달랐다(그래서 재현이 어려웠다).
            `overflow-hidden`은 활성 표시 막대(`w-1`)의 둥근 모서리에 필요하므로 남긴다 —
            대신 라벨이 줄어들어 넘칠 일 자체를 없앤다. */}
        <span className="flex shrink-0 items-center"
              style={{ color: isActive ? point : "rgb(var(--color-muted-rgb))" }}>{t.icon}</span>
        <span className="min-w-0 flex-1 truncate text-sm font-semibold"
              title={t.label}
              style={{ color: isActive ? point : undefined }}>{t.label}</span>
        {t.live && (
          <span className="ml-auto flex shrink-0 items-center gap-1" title="실시간 기준">
            <span className="nb-live-dot h-1.5 w-1.5 rounded-full" style={{ background: "#FF4D4D" }} />
            <span className="text-[9px] font-bold tracking-wide" style={{ color: "#FF4D4D" }}>LIVE</span>
          </span>
        )}
        {t.event && (
          <span className="nb-event-badge ml-auto shrink-0 rounded px-1.5 py-0.5 text-[9px] font-extrabold tracking-wide"
                style={{ background: GOLD, color: "#1a1400" }} title="기간 한정 이벤트">
            EVENT
          </span>
        )}
      </button>
    );
  };

  return (
    <aside className="md:sticky md:top-[76px] md:self-start">
      {children}
      {/* 스트리머 검색 ↔ 실시간 분석 사이에 단독 이벤트 메뉴 */}
      <div className="mb-1">{renderItem(EVENT_ITEM)}</div>
      <nav className="flex flex-col gap-1">
        {NAV_GROUPS.map((g, gi) => {
          if (!g.header) return <div key={gi} className="mt-1">{g.items.map(renderItem)}</div>;
          const open = openGroups.has(g.header);
          return (
            <div key={gi} className={gi > 0 ? "mt-1" : ""}>
              <button onClick={() => toggleGroup(g.header!)}
                className="w-full flex items-center gap-1.5 px-2.5 py-2 rounded-lg text-sm font-bold text-fg hover:bg-bg-hover transition-colors">
                <ChevronDown size={15} className="transition-transform text-muted"
                             style={{ transform: open ? "none" : "rotate(-90deg)" }} />
                {g.header}
              </button>
              {open && <div className="flex flex-col gap-1 mt-1 pl-2.5">{g.items.map(renderItem)}</div>}
            </div>
          );
        })}
      </nav>
      <p className="hidden md:block text-[11px] text-muted/60 mt-4 px-1 leading-relaxed">
        약 10분 주기로 치지직 공개 라이브 목록을 수집합니다. 비공식 서비스로 실제 수치와 오차가 있을 수 있습니다.
      </p>
    </aside>
  );
}
