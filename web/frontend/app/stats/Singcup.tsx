"use client";
import { useEffect, useState } from "react";
import { Trophy, ExternalLink, Play, Eye, MessageSquare, Loader2, Medal } from "lucide-react";
import { api } from "@/lib/api";
import type { SingcupRankings, SingcupEntry } from "@/lib/types";

// 싱드컵 이벤트 랭킹 — /stats 의 다크 대시보드 스타일(card/section-title/테마 토큰)을 그대로 쓴다.
// 원본(네이버 라운지) API는 백엔드가 감싸므로 이 파일은 정규화된 응답만 다룬다.

const GREEN = "#00FFA3";
const GOLD = "#FACC15";
const AMBER = "#FB923C";
// 버프 수치용 2톤 그라데이션 — 노란색 + 앰버(따뜻한 계열끼리라 이벤트 톤과 어울린다)
const BUFF_GRAD = `linear-gradient(135deg, ${GOLD}, ${AMBER})`;
const SILVER = "#D1D5DB";
const BRONZE = "#D97706";
const MEDAL = [GOLD, SILVER, BRONZE] as const;
const nf = (n: number) => n.toLocaleString("ko-KR");

const fmtDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("ko-KR",
    { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "-";
const fmtRange = (a: string, b: string) => {
  const f = (s: string) => new Date(s).toLocaleString("ko-KR",
    { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return `${f(a)} ~ ${f(b)}`;
};

function EventBadge() {
  return (
    <span className="nb-event-badge rounded px-1.5 py-0.5 text-[11px] font-extrabold tracking-wide"
          style={{ background: GOLD, color: "#1a1400" }}>EVENT</span>
  );
}

function StatusChip({ data }: { data: SingcupRankings }) {
  const s = data.event.status;
  const label = s === "LIVE" ? "진행 중" : s === "ENDED" ? "종료" : "시작 전";
  const color = s === "LIVE" ? GREEN : s === "ENDED" ? "rgb(var(--color-muted-rgb))" : GOLD;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs font-semibold"
          style={{ color }}>
      <span className={`h-1.5 w-1.5 rounded-full ${s === "LIVE" ? "nb-live-dot" : ""}`}
            style={{ background: color }} />
      {label}
    </span>
  );
}

function Tile({ label, value, unit, accent }:
  { label: string; value: string; unit?: string; accent?: boolean }) {
  return (
    <div className="card !p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1.5 tracking-tight">
        <span className="text-xl font-extrabold tabular-nums md:text-2xl"
              style={accent ? { color: GOLD } : { color: "rgb(var(--color-fg-rgb))" }}>
          {value}
        </span>
        {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
      </p>
    </div>
  );
}

function Skeleton() {
  // 실제 카드와 비슷한 높이로 잡아 로딩→표시 전환에서 레이아웃이 튀지 않게 한다
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card !p-4">
            <div className="h-4 w-20 animate-pulse rounded bg-bg-hover" />
            <div className="mt-3 h-7 w-16 animate-pulse rounded bg-bg-hover" />
          </div>
        ))}
      </div>
      <div className="card">
        <div className="h-5 w-32 animate-pulse rounded bg-bg-hover" />
        <div className="mt-4 space-y-2">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="h-14 animate-pulse rounded-lg bg-bg-hover" />
          ))}
        </div>
      </div>
    </div>
  );
}

// 참가작 한 건 — 데스크톱은 가로 랭킹 카드, 모바일은 세로 카드로 자연스럽게 접힌다.
function EntryCard({ e }: { e: SingcupEntry }) {
  const medal = e.rank <= 3 ? MEDAL[e.rank - 1] : null;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border p-3 transition-colors sm:flex-nowrap"
         style={{ borderColor: medal ? `${medal}66` : "rgb(var(--color-border-rgb))",
                  background: medal ? `${medal}0d` : undefined }}>
      {/* 순위 */}
      <span className="flex w-9 shrink-0 items-center justify-center gap-1">
        {medal
          ? <Medal size={18} style={{ color: medal }} />
          : <span className="text-sm tabular-nums text-muted">{e.rank}</span>}
        {medal && <span className="text-sm font-extrabold tabular-nums" style={{ color: medal }}>{e.rank}</span>}
      </span>

      {/* 작성자 */}
      <span className="h-9 w-9 shrink-0 overflow-hidden rounded-full bg-bg-hover">
        {e.authorProfileImageUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={e.authorProfileImageUrl} alt="" width={36} height={36} loading="lazy"
               className="h-full w-full object-cover" />
        )}
      </span>

      <span className="min-w-0 flex-1 basis-full sm:basis-auto">
        <span className="block truncate text-sm font-bold text-fg">{e.authorNickname || "-"}</span>
        {/* 제목은 최대 2줄 말줄임 */}
        <span className="mt-0.5 block overflow-hidden text-[13px] leading-snug text-muted"
              style={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}
              title={e.title}>
          {e.title}
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted/80">
          <span className="flex items-center gap-1"><Eye size={11} /> {nf(e.viewCount)}</span>
          <span className="flex items-center gap-1"><MessageSquare size={11} /> {nf(e.commentCount)}</span>
          <span className="tabular-nums">{fmtDate(e.createdAt)}</span>
        </span>
      </span>

      {/* 버프 — 이 페이지에서 가장 중요한 지표라 가장 크게 + 골드→앰버 2톤 그라데이션.
          사이트 기본 포인트(그린→시안)와 겹치지 않게 따뜻한 계열로만 묶었다. */}
      <span className="shrink-0 pr-1 text-right sm:pr-3">
        <span className="block text-xl font-extrabold tabular-nums md:text-2xl"
              style={{ background: BUFF_GRAD, WebkitBackgroundClip: "text",
                       backgroundClip: "text", color: "transparent" }}>
          {nf(e.buffCount)}
        </span>
        <span className="block text-[11px] text-muted">버프</span>
      </span>

      {/* 링크 */}
      <span className="flex shrink-0 items-center gap-1.5">
        {e.clipUrl ? (
          <a href={e.clipUrl} target="_blank" rel="noopener noreferrer"
             className="flex items-center gap-1 rounded-lg px-2.5 py-2 text-xs font-bold text-[#04140d]"
             style={{ background: GREEN }}>
            <Play size={12} /> 클립
          </a>
        ) : (
          <span className="flex cursor-not-allowed items-center gap-1 rounded-lg border border-border
                           px-2.5 py-2 text-xs font-medium text-muted/60" title="본문에 클립 링크가 없습니다">
            <Play size={12} /> 클립
          </span>
        )}
        <a href={e.postUrl} target="_blank" rel="noopener noreferrer"
           className="btn-secondary flex items-center gap-1 !px-2.5 !py-2 text-xs">
          원문 <ExternalLink size={11} />
        </a>
      </span>
    </div>
  );
}

export default function Singcup() {
  const [data, setData] = useState<SingcupRankings | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => api.singcup.rankings(200)
      .then((d) => { if (alive) { setData(d); setFailed(false); } })
      .catch(() => { if (alive) setFailed(true); })
      .finally(() => { if (alive) setLoading(false); });
    load();
    // 진행 중에는 화면도 주기적으로 갱신한다(수집 주기보다 넉넉하게)
    const t = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (loading && !data) return <Skeleton />;

  if (!data) {
    return (
      <div className="card py-16 text-center">
        <Trophy size={34} className="mx-auto mb-3 opacity-30" style={{ color: GOLD }} />
        <p className="font-medium text-fg">싱드컵 데이터를 불러오지 못했습니다.</p>
        <p className="mt-1 text-sm text-muted">잠시 후 다시 시도해주세요.</p>
      </div>
    );
  }

  const { event, summary, collector, rankings } = data;
  const ended = event.status === "ENDED";
  const upcoming = event.status === "UPCOMING";

  return (
    <div className="space-y-5">
      {/* 헤더 */}
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="flex items-center gap-2 text-xl font-extrabold tracking-tight md:text-2xl">
            <Trophy size={20} style={{ color: GOLD }} /> 치지직 싱드컵 랭킹
          </h2>
          <EventBadge />
          <StatusChip data={data} />
          {collector.stale && !ended && (
            <span className="rounded-full border px-2.5 py-1 text-xs font-semibold"
                  style={{ color: GOLD, borderColor: `${GOLD}66`, background: `${GOLD}14` }}
                  title={`마지막 정상 집계 후 ${collector.staleAfterMinutes}분이 지났습니다`}>
              집계 지연
            </span>
          )}
        </div>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
          치지직 라운지에서 진행 중인 싱드컵 참가작을 버프 순으로 확인합니다.{" "}
          제목이 <b className="text-fg">[싱드컵]</b> 말머리로 시작하는 게시글만 집계하며,
          말머리가 없으면 본문에 싱드컵 내용이 있어도 <b className="text-fg">포함되지 않습니다.</b>
        </p>
        <p className="mt-1 text-[13px] text-muted/80">
          <span className="tabular-nums">{fmtRange(event.startAt, event.endAt)}</span>
          <span className="mx-2 text-border">·</span>
          마지막 집계 <span className="tabular-nums">{fmtDate(collector.lastSuccessAt)}</span>
        </p>
      </div>

      {/* 수집 실패했지만 이전 데이터가 있는 경우 — 화면 전체를 오류로 바꾸지 않는다 */}
      {(failed || collector.status !== "OK") && rankings.length > 0 && (
        <div className="rounded-xl border px-3.5 py-2.5 text-sm"
             style={{ borderColor: `${GOLD}55`, background: `${GOLD}10`, color: "rgb(var(--color-fg-rgb))" }}>
          마지막 정상 집계 데이터를 표시하고 있습니다.
          <span className="ml-1 text-muted">(집계 재시도 중)</span>
        </div>
      )}

      {ended && (
        <div className="rounded-xl border border-border px-3.5 py-2.5 text-sm text-fg">
          이벤트가 종료되었습니다.
          <span className="ml-1 text-muted">최종 집계 {fmtDate(collector.lastSuccessAt)}</span>
        </div>
      )}

      {/* 요약 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="참가 게시글" value={nf(summary.submissionCount)} unit="개" />
        <Tile label="참가자" value={nf(summary.participantCount)} unit="명" />
        <Tile label="전체 버프" value={nf(summary.totalBuffCount)} unit="개" accent />
        <Tile label="현재 1위" value={summary.topNickname || "-"} />
      </div>

      {/* 랭킹 */}
      <div className="card">
        <h3 className="section-title">참가작 랭킹</h3>
        <p className="mt-0.5 text-[11px] text-muted">
          <b className="text-muted">[싱드컵]</b> 말머리 게시글만 집계 · 버프 수 내림차순 ·
          동률이면 조회수 → 등록 시각 순. 같은 참가자는 가장 버프가 높은 한 편만 표시합니다.
        </p>

        {rankings.length === 0 ? (
          <div className="py-16 text-center">
            <Trophy size={30} className="mx-auto mb-3 opacity-25" style={{ color: GOLD }} />
            <p className="font-medium text-fg">아직 등록된 싱드컵 참가작이 없습니다.</p>
            {upcoming && (
              <p className="mt-1 text-sm text-muted">
                이벤트 시작 예정: <span className="tabular-nums">{fmtDate(event.startAt)}</span>
              </p>
            )}
          </div>
        ) : (
          <div className="mt-4 space-y-2">
            {rankings.map((e) => <EntryCard key={e.feedId} e={e} />)}
          </div>
        )}
      </div>

      <p className="text-[11px] leading-relaxed text-muted/70">
        * 네이버 게임 치지직 라운지 자유게시판에서 제목이 <b className="text-muted">[싱드컵]</b>으로
        시작하는 게시글만 주기적으로 수집한 결과입니다. 말머리가 없는 게시글은 집계에서 제외되므로,
        참가작이 보이지 않는다면 제목 말머리를 확인해 주세요. 버프·조회수는 수집 시점 기준이라
        라운지 실제 수치와 약간의 시차가 있을 수 있습니다.
      </p>
    </div>
  );
}
