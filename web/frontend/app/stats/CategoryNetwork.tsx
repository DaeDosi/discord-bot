"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { RisingLiveRanking } from "@/lib/types";

// 카테고리 ↔ 스트리머 연관 관계망.
// vis-network는 window/document에 직접 붙는 순수 클라이언트 라이브러리라 SSR이 불가능하다.
// 그래서 모듈 자체를 effect 안에서 동적 import 한다(next/dynamic + ssr:false와 같은 효과인데,
// 라이브러리 인스턴스를 ref로 직접 다뤄야 해서 이 방식이 더 단순하다).

const GREEN  = "#00FFA3";
const CYAN   = "#00C2FF";
const PURPLE = "#A855F7";

// 카테고리 노드는 보라↔시안을 번갈아 써서 인접 카테고리가 구분되게 한다.
const CAT_COLORS = [PURPLE, CYAN];

export interface CategoryNetworkProps {
  rank: RisingLiveRanking;
  /** 상위 몇 개 카테고리를 중앙 노드로 삼을지 */
  topCategories?: number;
  /** 카테고리별로 붙일 스트리머 수 상한 */
  perCategory?: number;
  height?: number;
}

export default function CategoryNetwork({
  rank, topCategories = 6, perCategory = 6, height = 460,
}: CategoryNetworkProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  const netRef = useRef<{ destroy: () => void } | null>(null);
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  // vis-network는 gzip 전 600KB대 청크다. 개요 탭이 기본 탭이라 그냥 두면 첫 방문에
  // 무조건 받아가므로, 관계망이 화면에 들어올 때까지 동적 import를 미룬다.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") { setVisible(true); return; } // 구형 브라우저 폴백
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setVisible(true); io.disconnect(); }
    }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // ── 그래프 데이터 구성 ────────────────────────────────────────────────────
  // 라이브 랭킹 스냅샷만으로 만든다(별도 API 없음). 스트리머는 방송 중인 카테고리
  // 하나에만 속하므로 실제로는 이분 그래프(bipartite) 형태가 된다.
  const graph = useMemo(() => {
    const byCat = new Map<string, typeof rank.streamers>();
    for (const s of rank.streamers) {
      const c = s.category_name?.trim();
      if (!c) continue; // 카테고리 미설정 방송은 관계망에서 제외
      const arr = byCat.get(c) ?? [];
      arr.push(s);
      byCat.set(c, arr);
    }

    // 카테고리 정렬 기준은 '총 시청자' — 방송 수가 많아도 시청자가 적으면 중앙에 둘 이유가 없다
    const cats = [...byCat.entries()]
      .map(([name, list]) => ({
        name,
        list: [...list].sort((a, b) => b.concurrent_viewers - a.concurrent_viewers),
        viewers: list.reduce((s, x) => s + x.concurrent_viewers, 0),
      }))
      .sort((a, b) => b.viewers - a.viewers)
      .slice(0, topCategories);

    const maxCatV = Math.max(1, ...cats.map((c) => c.viewers));
    const shown = cats.flatMap((c) => c.list.slice(0, perCategory));
    const maxStV = Math.max(1, ...shown.map((s) => s.concurrent_viewers));

    type Node = Record<string, unknown>;
    const nodes: Node[] = [];
    const edges: Record<string, unknown>[] = [];

    cats.forEach((c, i) => {
      const color = CAT_COLORS[i % CAT_COLORS.length];
      nodes.push({
        id: `cat:${c.name}`,
        label: c.name,
        // 중앙(카테고리) 노드: 총 시청자에 비례, 스트리머 노드보다 확실히 크게
        size: 26 + (c.viewers / maxCatV) * 26,
        shape: "dot",
        color: { background: color, border: color, highlight: { background: color, border: "#ffffff" } },
        font: { color: "#ffffff", size: 15, face: "inherit", strokeWidth: 3, strokeColor: "rgba(0,0,0,.65)" },
        // 툴팁(title)은 카테고리 요약 정보
        title: `${c.name}\n시청자 ${c.viewers.toLocaleString("ko-KR")}명 · 방송 ${c.list.length}개`,
        kind: "category",
      });

      c.list.slice(0, perCategory).forEach((s) => {
        nodes.push({
          id: `st:${s.chzzk_channel_id}`,
          label: s.channel_name,
          // 외곽(스트리머) 노드: 시청자 수 비례
          size: 10 + (s.concurrent_viewers / maxStV) * 20,
          shape: "dot",
          color: { background: GREEN, border: GREEN, highlight: { background: GREEN, border: "#ffffff" } },
          font: { color: "rgba(235,240,245,.92)", size: 12, face: "inherit", strokeWidth: 3, strokeColor: "rgba(0,0,0,.65)" },
          title: `${s.channel_name}\n시청자 ${s.concurrent_viewers.toLocaleString("ko-KR")}명\n클릭하면 개인 분석으로 이동`,
          kind: "streamer",
          channelId: s.chzzk_channel_id,
        });
        edges.push({
          from: `cat:${c.name}`,
          to: `st:${s.chzzk_channel_id}`,
          // 시청자가 많을수록 두껍게
          width: 1 + (s.concurrent_viewers / maxStV) * 6,
          color: { color: "rgba(0,255,163,.22)", highlight: GREEN },
        });
      });
    });

    return { nodes, edges, catCount: cats.length };
  }, [rank, topCategories, perCategory]);

  // ── vis-network 인스턴스 ──────────────────────────────────────────────────
  useEffect(() => {
    let disposed = false;
    const el = boxRef.current;
    if (!el || graph.nodes.length === 0 || !visible) return;

    (async () => {
      try {
        // standalone 빌드는 vis-data를 이미 번들해서 DataSet까지 re-export 한다
        // (peer 빌드를 쓰면 vis-data를 별도 의존성으로 받아야 함).
        const { Network, DataSet } = await import("vis-network/standalone");
        if (disposed) return;

        const net = new Network(
          el,
          { nodes: new DataSet(graph.nodes as never[]), edges: new DataSet(graph.edges as never[]) },
          {
            layout: { improvedLayout: false }, // 노드가 수십 개면 improvedLayout이 매우 느려진다
            physics: {
              solver: "forceAtlas2Based",
              forceAtlas2Based: { gravitationalConstant: -70, springLength: 130, springConstant: 0.05 },
              stabilization: { iterations: 220, fit: true },
            },
            nodes: { borderWidth: 2, shadow: { enabled: true, size: 12, color: "rgba(0,0,0,.45)" } },
            edges: { smooth: { enabled: true, type: "continuous", roundness: 0.4 }, selectionWidth: 2 },
            interaction: { hover: true, tooltipDelay: 120, navigationButtons: false, keyboard: false },
          } as never,
        );

        // 노드 클릭 — 스트리머면 개인 분석으로 이동, 카테고리면 포커스(툴팁은 hover가 담당)
        net.on("click", (p: { nodes: string[] }) => {
          const id = p.nodes?.[0];
          if (!id) return;
          const n = graph.nodes.find((x) => x.id === id) as { kind?: string; channelId?: string } | undefined;
          if (n?.kind === "streamer" && n.channelId) router.push(`/stats/streamer/${n.channelId}`);
          else net.focus(id, { scale: 1.15, animation: { duration: 400, easingFunction: "easeInOutQuad" } });
        });
        // 커서 힌트
        net.on("hoverNode", () => { el.style.cursor = "pointer"; });
        net.on("blurNode",  () => { el.style.cursor = "default"; });

        netRef.current = net;
        setReady(true);
      } catch {
        if (!disposed) setFailed(true);
      }
    })();

    return () => {
      disposed = true;
      netRef.current?.destroy();
      netRef.current = null;
    };
  }, [graph, router, visible]);

  if (graph.nodes.length === 0) {
    return <p className="text-sm text-muted text-center py-10">관계망을 그릴 라이브 데이터가 아직 없습니다.</p>;
  }

  return (
    <div className="relative">
      <div ref={boxRef} style={{ height }} className="w-full rounded-xl bg-bg-hover/30" />
      {!ready && !failed && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">
          관계망 계산 중…
        </div>
      )}
      {failed && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">
          관계망을 불러오지 못했습니다.
        </div>
      )}
    </div>
  );
}
