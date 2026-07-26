"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Crosshair, Tag, Camera, X, ArrowRight } from "lucide-react";
import type { RisingLiveRanking, RisingStreamer } from "@/lib/types";

// 카테고리 ↔ 스트리머 연관 관계망 (독립 탭 /stats/network 전용).
//
// vis-network는 window/document에 직접 붙는 순수 클라이언트 라이브러리라 SSR이 불가능해서
// 모듈 자체를 effect 안에서 동적 import 한다. gzip 전 600KB대라 화면에 들어올 때까지 미룬다.
//
// 요구사항의 D3 용어 ↔ vis-network 대응:
//   fx/fy 고정            -> nodes.update({ fixed: { x: true, y: true } })
//   paint-order: stroke   -> font.strokeWidth / font.strokeColor (캔버스라 CSS가 아님)
//   charge/repulsion 강화 -> physics.forceAtlas2Based.gravitationalConstant (더 음수로)
//   최소 간격             -> springLength 확장 + avoidOverlap

const GREEN  = "#00FFA3";
const CYAN   = "#00C2FF";
const PURPLE = "#A855F7";
const STROKE = "#0F1015"; // 라벨 뒤 어두운 아웃라인

const CAT_COLORS = [PURPLE, CYAN];
// 라벨 공통 폰트 — 크기와 무관하게 모든 노드에 같은 정책을 적용한다.
// 캔버스 렌더링이라 CSS paint-order/stroke가 아니라 vis-network 폰트 옵션으로 아웃라인을 준다.
const LABEL_FONT = {
  color: "#FFFFFF", size: 12, face: "inherit", bold: { color: "#FFFFFF", size: 12, face: "inherit" },
  strokeWidth: 3, strokeColor: STROKE, vadjust: 2,
} as const;

type Slim = Pick<RisingStreamer, "chzzk_channel_id" | "channel_name" | "concurrent_viewers" | "category_name" | "channel_image_url">;

export interface CategoryNetworkProps {
  rank: RisingLiveRanking;
  topCategories?: number;
  perCategory?: number;
  /** 캔버스 높이 CSS 값 */
  height?: string | number;
}

interface Selected {
  id: string;
  name: string;
  viewers: number;
  category: string;
  neighbors: Slim[];
}

export default function CategoryNetwork({
  rank, topCategories = 6, perCategory = 6, height = 750,
}: CategoryNetworkProps) {
  const boxRef = useRef<HTMLDivElement>(null);
  // vis-network 인스턴스는 타입이 무거워서 필요한 메서드만 좁혀 보관한다
  const netRef = useRef<any>(null);
  const dsRef = useRef<{ nodes: any; edges: any } | null>(null);
  const router = useRouter();

  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [visible, setVisible] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  // 화면에 들어올 때까지 무거운 청크 로드를 미룬다
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) { setVisible(true); io.disconnect(); }
    }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // ── 그래프 데이터 ─────────────────────────────────────────────────────────
  // 스트리머는 방송 중인 카테고리 1개에만 속하므로 실제로는 이분 그래프다.
  // 그래서 '연결된 스트리머'는 1홉 이웃(=카테고리)이 아니라 같은 카테고리를 공유하는
  // 2홉 이웃으로 계산한다 — 인포 카드가 기대하는 의미가 이쪽이다.
  const graph = useMemo(() => {
    const byCat = new Map<string, Slim[]>();
    for (const s of rank.streamers) {
      const c = s.category_name?.trim();
      if (!c) continue;
      (byCat.get(c) ?? byCat.set(c, []).get(c)!).push({
        chzzk_channel_id: s.chzzk_channel_id, channel_name: s.channel_name,
        concurrent_viewers: s.concurrent_viewers, category_name: c,
        channel_image_url: s.channel_image_url,
      });
    }

    const cats = [...byCat.entries()]
      .map(([name, list]) => ({
        name,
        list: [...list].sort((a, b) => b.concurrent_viewers - a.concurrent_viewers),
        viewers: list.reduce((s, x) => s + x.concurrent_viewers, 0),
      }))
      .sort((a, b) => b.viewers - a.viewers)
      .slice(0, topCategories);

    const maxCatV = Math.max(1, ...cats.map((c) => c.viewers));
    const shownPerCat = new Map<string, Slim[]>();
    cats.forEach((c) => shownPerCat.set(c.name, c.list.slice(0, perCategory)));
    const shown = [...shownPerCat.values()].flat();
    const maxStV = Math.max(1, ...shown.map((s) => s.concurrent_viewers));

    const nodes: Record<string, unknown>[] = [];
    const edges: Record<string, unknown>[] = [];

    cats.forEach((c, i) => {
      const color = CAT_COLORS[i % CAT_COLORS.length];
      nodes.push({
        id: `cat:${c.name}`, label: c.name, kind: "category", catName: c.name,
        size: 30 + (c.viewers / maxCatV) * 28,
        shape: "dot",
        color: { background: color, border: color, highlight: { background: color, border: "#fff" } },
        font: { ...LABEL_FONT, size: 15 },
        title: `${c.name}\n시청자 ${c.viewers.toLocaleString("ko-KR")}명 · 방송 ${c.list.length}개`,
      });

      (shownPerCat.get(c.name) ?? []).forEach((s) => {
        const size = 11 + (s.concurrent_viewers / maxStV) * 22;
        nodes.push({
          id: `st:${s.chzzk_channel_id}`, label: s.channel_name, kind: "streamer",
          channelId: s.chzzk_channel_id, catName: c.name,
          viewers: s.concurrent_viewers, size,
          // 프로필 이미지를 원형 크롭해 노드로 쓰고, 테두리는 민트 링(borderWidth는 전역 2px).
          // 이미지가 없는 채널은 기존 단색 원으로 폴백한다.
          ...(s.channel_image_url
            ? { shape: "circularImage", image: s.channel_image_url, brokenImage: undefined }
            : { shape: "dot" }),
          color: { background: GREEN, border: GREEN, highlight: { background: GREEN, border: "#fff" } },
          font: { ...LABEL_FONT },
          title: `${s.channel_name}\n시청자 ${s.concurrent_viewers.toLocaleString("ko-KR")}명\n클릭하면 상세 정보`,
        });
        edges.push({
          id: `e:${c.name}:${s.chzzk_channel_id}`,
          from: `cat:${c.name}`, to: `st:${s.chzzk_channel_id}`,
          width: 1 + (s.concurrent_viewers / maxStV) * 6,
          color: { color: "rgba(0,255,163,.22)", highlight: GREEN },
        });
      });
    });

    return { nodes, edges, shownPerCat };
  }, [rank, topCategories, perCategory]);

  // 라벨 원본을 보관 — 토글/소형노드 숨김에서 되돌리기 위함
  const labelsRef = useRef<Map<string, string>>(new Map());

  // ── 인스턴스 생성 ─────────────────────────────────────────────────────────
  useEffect(() => {
    let disposed = false;
    const el = boxRef.current;
    if (!el || graph.nodes.length === 0 || !visible) return;

    (async () => {
      try {
        const { Network, DataSet } = await import("vis-network/standalone");
        if (disposed) return;

        // DataSet 제네릭을 좁히면 update() 부분 갱신이 never[]로 추론돼 막힌다 — 느슨하게 둔다
        const nodesDS: any = new DataSet(graph.nodes as any[]);
        const edgesDS: any = new DataSet(graph.edges as any[]);
        labelsRef.current = new Map(graph.nodes.map((n) => [n.id as string, n.label as string]));

        const net = new Network(el, { nodes: nodesDS, edges: edgesDS } as never, {
          layout: { improvedLayout: false },
          physics: {
            solver: "forceAtlas2Based",
            forceAtlas2Based: {
              // 반발력 강화(-70 -> -220) + 스프링 길이 1.5배(130 -> 195)로 중앙 뭉침 완화
              gravitationalConstant: -220,
              springLength: 195,
              springConstant: 0.035,
              avoidOverlap: 0.6, // 노드 간 최소 간격 확보
              damping: 0.5,
            },
            stabilization: { iterations: 400, fit: true },
          },
          nodes: { borderWidth: 2, shadow: { enabled: true, size: 14, color: "rgba(0,0,0,.5)" } },
          edges: { smooth: { enabled: true, type: "continuous", roundness: 0.4 }, selectionWidth: 2 },
          interaction: {
            hover: true, tooltipDelay: 120, keyboard: false,
            dragNodes: true, dragView: true, zoomView: true,
          },
        } as never);

        netRef.current = net;
        dsRef.current = { nodes: nodesDS, edges: edgesDS };

        // 안정화가 끝난 뒤에 소형 노드 라벨을 정리한다(초기 렌더 깜빡임 방지)
        net.once("stabilizationIterationsDone", () => {
          if (!disposed) { net.setOptions({ physics: { enabled: false } } as never); applyLabels(true); }
        });

        // 드래그로 옮긴 노드는 그 자리에 고정 (fx/fy 고정에 해당)
        net.on("dragEnd", (p: { nodes: string[] }) => {
          if (!p.nodes?.length) return;
          nodesDS.update(p.nodes.map((id) => ({ id, fixed: { x: true, y: true } })));
        });

        // 클릭 — 스트리머면 선택/하이라이트, 카테고리면 포커스, 빈 곳이면 선택 해제
        net.on("click", (p: { nodes: string[] }) => {
          const id = p.nodes?.[0];
          if (!id) { clearSelection(); return; }
          const n = nodesDS.get(id) as any;
          if (n?.kind === "streamer") selectStreamer(n);
          else {
            clearSelection();
            net.focus(id, { scale: 1.1, animation: { duration: 400, easingFunction: "easeInOutQuad" } });
          }
        });

        net.on("hoverNode", () => { el.style.cursor = "pointer"; });
        net.on("blurNode",  () => { el.style.cursor = "default"; });

        setReady(true);
      } catch {
        if (!disposed) setFailed(true);
      }
    })();

    return () => {
      disposed = true;
      netRef.current?.destroy();
      netRef.current = null;
      dsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, visible]);

  // 라벨 표시 정책: 토글 ON이면 노드 크기·줌 배율과 무관하게 전부 표시, OFF면 전부 숨김.
  // (예전엔 소형 노드를 숨겼는데 작은 스트리머 닉네임이 아예 안 보여 토글이 무의미했다.)
  const applyLabels = useCallback((on: boolean) => {
    const ds = dsRef.current?.nodes;
    if (!ds) return;
    ds.update((ds.get() as any[]).map((n) => ({
      id: n.id,
      label: on ? labelsRef.current.get(n.id) ?? "" : undefined,
    })));
  }, []);

  useEffect(() => { if (ready) applyLabels(showLabels); }, [showLabels, ready, applyLabels]);

  // ── 선택/하이라이트 ───────────────────────────────────────────────────────
  const dim = (on: boolean, keepNodes?: Set<string>, keepEdges?: Set<string>) => {
    const ds = dsRef.current;
    if (!ds) return;
    ds.nodes.update((ds.nodes.get() as any[]).map((n) => {
      const keep = !on || keepNodes?.has(n.id);
      return {
        id: n.id,
        opacity: keep ? 1 : 0.15,
        // 흐리게 할 때 라벨도 같이 죽인다. color는 손대지 않는다 —
        // circularImage 노드의 color를 덮어쓰면 프로필 이미지 링이 깨진다.
        font: { ...n.font, color: keep ? LABEL_FONT.color : "rgba(235,240,245,.18)" },
      };
    }));
    ds.edges.update((ds.edges.get() as any[]).map((e) => ({
      id: e.id,
      color: { color: !on || keepEdges?.has(e.id) ? "rgba(0,255,163,.55)" : "rgba(120,130,145,.06)", highlight: GREEN },
    })));
  };

  const selectStreamer = (n: any) => {
    const cat: string = n.catName;
    const peers = (graph.shownPerCat.get(cat) ?? []).filter((s) => s.chzzk_channel_id !== n.channelId);
    setSelected({ id: n.id, name: labelsRef.current.get(n.id) ?? n.label ?? "", viewers: n.viewers ?? 0, category: cat, neighbors: peers });
    const keepNodes = new Set<string>([n.id, `cat:${cat}`, ...peers.map((p) => `st:${p.chzzk_channel_id}`)]);
    const keepEdges = new Set<string>([`e:${cat}:${n.channelId}`, ...peers.map((p) => `e:${cat}:${p.chzzk_channel_id}`)]);
    dim(true, keepNodes, keepEdges);
  };

  const clearSelection = () => { setSelected(null); dim(false); };

  // ── 컨트롤 ────────────────────────────────────────────────────────────────
  const resetView = () => {
    const net = netRef.current;
    if (!net) return;
    // 드래그로 고정된 노드도 함께 풀어 초기 배치로 복구
    const ds = dsRef.current?.nodes;
    if (ds) ds.update((ds.get() as any[]).map((n) => ({ id: n.id, fixed: { x: false, y: false } })));
    clearSelection();
    net.fit({ animation: { duration: 500, easingFunction: "easeInOutQuad" } });
  };

  const savePng = () => {
    const net = netRef.current;
    // vis-network는 캔버스를 network.canvas.frame.canvas 로 노출한다
    const canvas: HTMLCanvasElement | undefined = net?.canvas?.frame?.canvas;
    if (!canvas) return;
    // 캔버스는 투명 배경이라 그대로 저장하면 흰 배경에서 라벨이 안 보인다 — 어두운 배경을 깐다
    const out = document.createElement("canvas");
    out.width = canvas.width; out.height = canvas.height;
    const ctx = out.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = STROKE;
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(canvas, 0, 0);
    // 프로필 이미지가 치지직 CDN(외부 도메인)에서 오므로, 그쪽이 CORS 헤더를 주지 않으면
    // 캔버스가 tainted 상태가 되어 toDataURL이 SecurityError를 던진다. 저장 실패를 조용히
    // 삼키면 버튼이 고장난 것처럼 보이므로 사용자에게 이유를 알린다.
    try {
      const a = document.createElement("a");
      a.href = out.toDataURL("image/png");
      a.download = `nexbot-network-${new Date().toISOString().slice(0, 10)}.png`;
      a.click();
      setSaveError(null);
    } catch {
      setSaveError("프로필 이미지의 보안 정책(CORS) 때문에 이미지 저장이 차단되었습니다. 닉네임 라벨을 끄지 않고도 화면 캡처로 저장할 수 있습니다.");
    }
  };

  if (graph.nodes.length === 0) {
    return <p className="text-sm text-muted text-center py-10">관계망을 그릴 라이브 데이터가 아직 없습니다.</p>;
  }

  const btn = "inline-flex items-center gap-1.5 rounded-lg border border-border bg-bg-card/80 px-3 py-1.5 text-xs font-medium text-muted hover:text-fg hover:bg-bg-hover transition-colors backdrop-blur";

  return (
    <div className="relative w-full rounded-xl border border-border bg-bg-hover/20 overflow-hidden"
         style={{ height: typeof height === "number" ? `${height}px` : height }}>
      {/* 캔버스. overscroll-contain으로 휠이 페이지 스크롤로 새어나가지 않게 한다
          (vis-network가 zoomView로 휠을 잡아 줌에 쓴다). */}
      <div ref={boxRef} className="absolute inset-0 [overscroll-behavior:contain]" />

      {/* 상단 컨트롤 바 */}
      <div className="absolute top-3 right-3 z-10 flex flex-wrap items-center justify-end gap-2">
        <button type="button" onClick={resetView} className={btn} title="줌/이동과 고정된 노드를 초기 상태로">
          <Crosshair size={14} /> 화면 중앙 리셋
        </button>
        <button type="button" onClick={() => setShowLabels((v) => !v)} className={btn}
                title="스트리머 닉네임 라벨 표시/숨김">
          <Tag size={14} /> 닉네임 {showLabels ? "ON" : "OFF"}
        </button>
        <button type="button" onClick={savePng} className={btn} title="현재 화면을 PNG로 저장">
          <Camera size={14} /> 이미지 저장
        </button>
      </div>

      {/* 좌측 하단 상세 인포 카드 */}
      {selected && (
        <div className="absolute bottom-3 left-3 z-10 w-[290px] max-w-[calc(100%-24px)] rounded-xl
                        border border-border bg-bg-card/95 p-4 shadow-2xl backdrop-blur">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <p className="font-extrabold text-fg truncate">{selected.name}</p>
              <p className="text-[11px] text-muted mt-0.5">
                {selected.category} · 시청자 {selected.viewers.toLocaleString("ko-KR")}명
              </p>
            </div>
            <button type="button" onClick={clearSelection}
                    className="shrink-0 text-muted hover:text-fg transition-colors" title="닫기">
              <X size={15} />
            </button>
          </div>

          <p className="mt-3 text-xs text-fg">
            연결된 스트리머 수: <b style={{ color: GREEN }}>{selected.neighbors.length}</b>
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted">
            {selected.neighbors.length > 0
              ? <>연결된 스트리머: {selected.neighbors.map((n) => n.channel_name).join(", ")}</>
              : "같은 카테고리에서 방송 중인 다른 스트리머가 없습니다."}
          </p>

          <button type="button"
            onClick={() => router.push(`/stats/streamer/${selected.id.replace(/^st:/, "")}`)}
            className="mt-3 w-full inline-flex items-center justify-center gap-1.5 rounded-lg
                       px-3 py-2 text-xs font-bold text-[#04140d] transition-opacity hover:opacity-90"
            style={{ background: `linear-gradient(135deg, ${GREEN}, ${CYAN})` }}>
            개인 분석 보기 <ArrowRight size={13} />
          </button>
        </div>
      )}

      {/* PNG 저장 실패 안내 */}
      {saveError && (
        <div className="absolute top-16 right-3 z-20 w-[280px] rounded-lg border border-border
                        bg-bg-card/95 p-3 text-[11px] leading-relaxed text-muted shadow-2xl backdrop-blur">
          <div className="flex items-start gap-2">
            <span className="flex-1">{saveError}</span>
            <button type="button" onClick={() => setSaveError(null)}
                    className="shrink-0 text-muted hover:text-fg" title="닫기"><X size={13} /></button>
          </div>
        </div>
      )}

      {/* 우측 하단 조작 가이드 */}
      <p className="absolute bottom-3 right-3 z-10 text-[11px] text-muted/60 pointer-events-none">
        마우스 휠로 확대/축소가 가능합니다
      </p>

      {!ready && !failed && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">관계망 계산 중…</div>
      )}
      {failed && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">관계망을 불러오지 못했습니다.</div>
      )}
    </div>
  );
}
