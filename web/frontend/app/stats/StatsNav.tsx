"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, LineChart as LineIcon, ListOrdered, Gamepad2, Sprout, Share2 } from "lucide-react";

// /stats 와 /stats/network 가 공유하는 좌측 메뉴.
//
// /stats 안의 탭들은 URL이 아니라 state로 전환되는데, 연관 관계망만 독립 라우트(/stats/network)다.
// 그래서 항목을 두 종류로 나눈다:
//   route 있는 항목  -> router.push (다른 페이지로 이동)
//   route 없는 항목  -> 같은 페이지의 탭 전환(onSelect). /stats/network에 있을 때는
//                       /stats?tab=<key> 로 돌아가야 하므로 onSelect가 없으면 그렇게 이동한다.
export type Tab =
  | "overview" | "newcomers_analysis" | "ranking" | "newcomers_ranking" | "category" | "network";

export interface NavItem { key: Tab; label: string; icon: React.ReactNode; route?: string }

export const NAV_GROUPS: { header: string | null; items: NavItem[] }[] = [
  { header: "실시간 분석", items: [
    { key: "overview",           label: "전체 스트리머 분석", icon: <LineIcon size={16} /> },
    { key: "newcomers_analysis", label: "신규 스트리머 분석", icon: <Sprout size={16} /> },
    { key: "network",            label: "스트리머 연관 관계망", icon: <Share2 size={16} />, route: "/stats/network" },
  ] },
  { header: "랭킹", items: [
    { key: "ranking",           label: "전체 스트리머 랭킹", icon: <ListOrdered size={16} /> },
    { key: "newcomers_ranking", label: "신규 스트리머 랭킹", icon: <Sprout size={16} /> },
  ] },
  { header: "카테고리", items: [
    { key: "category", label: "카테고리 분석", icon: <Gamepad2 size={16} /> },
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
  /** 같은 페이지에서 탭을 바꿀 수 있을 때만 전달 (/stats). 없으면 /stats?tab= 로 이동 */
  onSelect?: (k: Tab) => void;
  /** 메뉴 위에 놓을 요소 (스트리머 검색 등) */
  children?: React.ReactNode;
}) {
  const router = useRouter();
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => {
    const h = groupHeaderOf(active); return new Set(h ? [h] : []);
  });
  const toggleGroup = (h: string) =>
    setOpenGroups((p) => { const n = new Set(p); if (n.has(h)) n.delete(h); else n.add(h); return n; });

  const go = (t: NavItem) => {
    if (t.route) { router.push(t.route); return; }
    if (onSelect) { onSelect(t.key); return; }
    router.push(`/stats?tab=${t.key}`);
  };

  const renderItem = (t: NavItem) => {
    const isActive = active === t.key;
    return (
      <button key={t.key} onClick={() => go(t)}
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
