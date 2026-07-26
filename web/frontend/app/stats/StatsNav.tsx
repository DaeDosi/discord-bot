"use client";
import { useState } from "react";
import { ChevronDown, LineChart as LineIcon, ListOrdered, Gamepad2, Sprout, Radio, Users } from "lucide-react";

// /stats 좌측 메뉴. 탭은 URL이 아니라 state로 전환된다.
// (route 기반 항목을 지원하던 분기는 /stats/network 제거와 함께 걷어냈다.)
export type Tab =
  | "overview" | "newcomers_analysis"
  | "ranking" | "ranking_period" | "newcomers_ranking"
  | "category" | "category_streamers";

export interface NavItem { key: Tab; label: string; icon: React.ReactNode }

// 랭킹을 '실시간'과 '누적'으로 분리한 이유:
// 실시간 랭킹은 최신 수집 사이클 한 장의 동시 시청자 순위라, 마침 그 순간 방송 중이었는지에
// 크게 좌우된다(잠깐 스파이크가 뜬 방송이 상위로 올라옴). 누적 랭킹은 기간 전체를 집계해
// 시청 시간·방송 시간 기준으로 줄 세우므로 꾸준함이 반영된다. 두 순위의 성격이 달라
// 한 표에 섞으면 어느 기준인지 알 수 없어 분리했다.
export const NAV_GROUPS: { header: string | null; items: NavItem[] }[] = [
  { header: "실시간 분석", items: [
    { key: "overview",           label: "전체 스트리머 분석", icon: <LineIcon size={16} /> },
    { key: "newcomers_analysis", label: "신규 스트리머 분석", icon: <Sprout size={16} /> },
  ] },
  { header: "실시간 랭킹", items: [
    { key: "ranking",           label: "전체 스트리머 랭킹", icon: <Radio size={16} /> },
    { key: "newcomers_ranking", label: "신규 스트리머 랭킹", icon: <Sprout size={16} /> },
  ] },
  { header: "누적 랭킹", items: [
    { key: "ranking_period", label: "기간별 누적 랭킹", icon: <ListOrdered size={16} /> },
  ] },
  { header: "카테고리", items: [
    { key: "category",            label: "카테고리 분석",     icon: <Gamepad2 size={16} /> },
    { key: "category_streamers",  label: "카테고리별 스트리머", icon: <Users size={16} /> },
  ] },
];

export const groupHeaderOf = (k: Tab): string | null =>
  NAV_GROUPS.find((g) => g.items.some((i) => i.key === k))?.header ?? null;

const GREEN = "#00FFA3";
const GRAD  = `linear-gradient(135deg, ${GREEN}, #00C2FF)`;

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
    return (
      <button key={t.key} onClick={() => onSelect(t.key)}
        className="relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-left transition-colors w-full border overflow-hidden"
        style={{
          background: isActive ? "rgba(0,255,163,0.08)" : "transparent",
          borderColor: isActive ? "rgba(0,194,255,0.35)" : "transparent",
        }}>
        {isActive && <span className="absolute left-0 top-0 bottom-0 w-1 rounded-r" style={{ background: GRAD }} />}
        <span style={{ color: isActive ? GREEN : "rgb(var(--color-muted-rgb))" }}>{t.icon}</span>
        <span className="block text-sm font-semibold whitespace-nowrap" style={{ color: isActive ? GREEN : undefined }}>{t.label}</span>
      </button>
    );
  };

  return (
    <aside className="md:sticky md:top-[76px] md:self-start">
      {children}
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
