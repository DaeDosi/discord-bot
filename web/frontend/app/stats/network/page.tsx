"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Bot, BarChart3, Loader2, Radio } from "lucide-react";
import { api } from "@/lib/api";
import type { RisingLiveRanking } from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import CategoryNetwork from "../CategoryNetwork";
import StatsNav from "../StatsNav";

const GREEN = "#00FFA3";
const GRAD  = `linear-gradient(135deg, ${GREEN}, #00C2FF)`;

function GradText({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ background: GRAD, WebkitBackgroundClip: "text", backgroundClip: "text", color: "transparent" }}>
      {children}
    </span>
  );
}

// 연관 관계망 독립 탭. 풀 캔버스로 뷰포트를 최대한 쓰고, Footer/서비스 소개는 두지 않는다
// (휠이 줌에 쓰이는 화면에서 세로 스크롤 콘텐츠가 있으면 스크롤 트랩이 된다).
export default function NetworkPage() {
  const [rank, setRank] = useState<RisingLiveRanking | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    api.rising.liveRanking(200)
      .then((r) => { if (alive) setRank(r); })
      .catch(() => { if (alive) setError(true); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const hasData = rank && rank.streamers.length > 0;

  return (
    // 페이지 자체 세로 스크롤을 막아 휠이 차트 줌으로만 쓰이게 한다 (스크롤 트랩 방지).
    // 좁은 화면에서는 메뉴가 위로 쌓여야 하므로 md 이상에서만 고정 높이를 적용한다.
    <div className="bg-bg text-fg md:h-screen md:overflow-hidden flex flex-col">
      <header className="border-b border-border bg-bg/80 backdrop-blur shrink-0">
        <div className="w-full px-4 md:px-6 flex items-center justify-between" style={{ height: 60 }}>
          <div className="flex items-center gap-2.5">
            <Link href="/" className="flex items-center gap-2 font-bold text-[15px] text-muted hover:text-fg transition-colors">
              <Bot size={18} className="text-accent" /> NexBot
            </Link>
            <span className="text-border">/</span>
            <Link href="/stats" className="flex items-center gap-1.5 font-extrabold text-[16px]">
              <BarChart3 size={17} style={{ color: GREEN }} /> <GradText>치지직 통계</GradText>
            </Link>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex-1 min-h-0 w-full px-4 md:px-6 py-5">
        <div className="grid grid-cols-1 md:grid-cols-[210px_1fr] gap-5 md:gap-7 h-full min-h-0">
          <StatsNav active="network" />

          <div className="min-w-0 flex flex-col min-h-0">
            <div className="mb-3 shrink-0">
              <h1 className="text-xl md:text-2xl font-extrabold tracking-tight">
                스트리머 <GradText>연관 관계망</GradText>
              </h1>
              <p className="text-xs text-muted mt-1">
                상위 카테고리와 그 카테고리를 방송 중인 주요 스트리머의 연결 관계입니다.
                노드 크기와 선 두께는 시청자 수에 비례하며, 노드를 클릭하면 같은 카테고리에서
                방송 중인 스트리머 목록을 볼 수 있습니다. 노드를 끌어다 놓으면 그 자리에 고정됩니다.
              </p>
            </div>

            {loading ? (
              <div className="flex-1 min-h-[420px] flex items-center justify-center gap-2 text-muted">
                <Loader2 size={18} className="animate-spin" /> 데이터를 불러오는 중...
              </div>
            ) : error || !hasData ? (
              <div className="flex-1 min-h-[420px] card flex flex-col items-center justify-center text-center">
                <Radio size={34} className="mb-3 opacity-30" style={{ color: GREEN }} />
                <p className="font-medium text-fg">
                  {error ? "데이터를 불러오지 못했습니다." : "관계망을 그릴 라이브 데이터가 아직 없습니다."}
                </p>
                <p className="text-sm text-muted mt-1">수집이 완료되면 관계망이 표시됩니다.</p>
              </div>
            ) : (
              // md 이상: 남은 높이를 모두 차지. 모바일: 뷰포트 기준 고정 높이로 최소 크기 보장.
              <div className="flex-1 min-h-0 h-[70vh] md:h-auto">
                <CategoryNetwork rank={rank!} height="100%" />
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
