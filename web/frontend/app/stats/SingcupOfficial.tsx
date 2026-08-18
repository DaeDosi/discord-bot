"use client";
/**
 * 싱드컵 **공식 예선 참가자** 화면.
 *
 * 이 화면이 답하는 질문은 하나다 — "누가 공식 예선에 나왔고, 사용자 투표에서는
 * 어떤 순서인가."
 *
 * **두 가지를 절대 섞지 않는다.**
 *  · 공식 발표 명단  — 치지직 공지에서 온 확정 값. 순위가 아니다.
 *  · PIKU 재계산 순위 — 우리가 PIKU 공개 데이터로 다시 매긴 순서. 공식 결과가 아니다.
 * 두 문구를 화면에 모두 적고, 순위 배지에도 출처를 붙인다.
 *
 * 표시 계약(요구):
 *  · 우승 비율·승률 **숫자를 화면에 내보내지 않는다.** 서버 응답에 아예 없다.
 *  · 조회수·하트도 표시하지 않는다.
 *  · 정렬은 `우승 비율순` / `승률순` 두 버튼이고, 바꾸면 **1위부터 다시 계산**된다
 *    (계산은 서버가 한다 — 프런트에서 다시 매기면 두 규칙이 갈라진다).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, ExternalLink, Loader2, Radio, Trophy, Users } from "lucide-react";
import { api } from "@/lib/api";
import type {
  PikuEntry, PikuRankingResponse, QualifierGroupRow, QualifierRow,
  QualifiersResponse,
} from "@/lib/types";
import { SINGCUP_QUALIFIERS } from "@/lib/singcupQualifiers";
import { GOLD, GREEN, hideBrokenImage, nf } from "./singcupShared";

const DIVISIONS = ["female_solo", "male_solo", "groups"] as const;
type Division = (typeof DIVISIONS)[number];

/** `전체` 화면에서 부문마다 보여 줄 수. 요구가 10·10·10이다. */
const OVERVIEW_ROWS = 10;
/** 부문 상단 카드 수. */
const TOP_CARDS = 5;

type Tab = "all" | Division;
const TABS: { k: Tab; label: string }[] = [
  { k: "all",         label: "전체" },
  { k: "female_solo", label: "여성 솔로" },
  { k: "male_solo",   label: "남성 솔로" },
  { k: "groups",      label: "그룹" },
];

const isGroupRow = (r: QualifierRow | QualifierGroupRow): r is QualifierGroupRow =>
  "members" in r;

/** 그룹은 팀 단위로 세지만 카드에는 대표 1명(첫 멤버)을 쓴다.
 *  팀 전체를 한 카드에 욱여넣으면 썸네일이 작아져 아무것도 안 보인다. */
function groupLead(g: QualifierGroupRow): QualifierRow | null {
  return g.members[0] ?? null;
}

/* ── 순위 배지 ───────────────────────────────────────────────────────────── */
function RankBadge({ rank }: { rank: number }) {
  // TOP 3만 색으로 구분하고, 색만으로 뜻을 전하지 않도록 숫자를 함께 둔다.
  const color = rank === 1 ? GOLD : rank === 2 ? "#C0C6D4" : rank === 3 ? "#CD7F32" : null;
  return (
    <span className="inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5
                     text-[11px] font-extrabold tabular-nums leading-none"
          style={color
            ? { color, borderColor: `${color}66`, background: `${color}14` }
            : { color: "rgb(var(--color-muted-rgb))",
                borderColor: "rgb(var(--color-border-rgb))" }}>
      {rank}위
    </span>
  );
}

/* ── TOP 카드 ────────────────────────────────────────────────────────────── */
function TopCard({ row, rank, teamNumber }: {
  row: QualifierRow; rank: number | null; teamNumber?: number;
}) {
  const live = row.live;
  const clipUrl = row.clipUid ? `https://chzzk.naver.com/clips/${row.clipUid}` : null;
  const href = live ? `https://chzzk.naver.com/live/${row.channelId}` : clipUrl;
  // **`없음`과 `못 불러옴`은 다른 상태다.** 전자는 대표 클립이 정말 없는 것이고,
  // 후자는 URL은 있는데 이미지 요청이 실패한 것이다. 둘을 같은 문구로 뭉치면
  // 수집 장애가 "원래 없는 것"으로 보여 신고조차 들어오지 않는다.
  const [thumbFailed, setThumbFailed] = useState(false);

  const thumb = (
    <>
      {row.clipThumbnailUrl && !thumbFailed ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={row.clipThumbnailUrl} alt="" loading="lazy"
             onError={() => setThumbFailed(true)}
             className="h-full w-full object-cover transition-transform
                        group-hover:scale-105" />
      ) : (
        // 빈 검은 박스를 두지 않는다 — 무엇이 없는지 적는다.
        <span className="flex h-full w-full items-center justify-center px-2
                         text-center text-[11px] text-muted">
          {thumbFailed ? "썸네일을 불러오지 못했습니다" : "클립 없음"}
        </span>
      )}
      {/* LIVE 배지 — **공식 예선 참가자에게만** 나온다(서버가 그것만 내려준다).
          `.nb-live-badge`는 UI-P에서 이 카드 전용으로 만든 클래스다(색 대비와
          `prefers-reduced-motion` 처리가 거기 들어 있다). 다른 화면의 LIVE 표현은
          별도 마크업이라 이 값의 영향을 받지 않는다. */}
      {live && (
        <span className="nb-live-badge absolute left-2 top-2 flex items-center gap-1
                         rounded px-1.5 py-0.5 text-[10px] font-bold">
          <span className="nb-live-dot h-1.5 w-1.5 rounded-full bg-current" /> LIVE
        </span>
      )}
      {rank !== null && (
        <span className="absolute bottom-2 left-2 rounded px-1.5 py-0.5 text-[10px]
                         font-extrabold tabular-nums"
              style={{ background: "rgba(0,0,0,0.7)", color: "#fff" }}>
          {rank}위
        </span>
      )}
      {teamNumber !== undefined && (
        <span className="absolute bottom-2 right-2 rounded px-1.5 py-0.5 text-[10px]
                         font-bold"
              style={{ background: "rgba(0,0,0,0.7)", color: "#fff" }}>
          {teamNumber}팀
        </span>
      )}
    </>
  );

  return (
    <div className="card !p-0 overflow-hidden">
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer"
           className="group relative block aspect-video w-full overflow-hidden bg-bg-hover">
          {thumb}
        </a>
      ) : (
        <div className="relative block aspect-video w-full overflow-hidden bg-bg-hover">
          {thumb}
        </div>
      )}
      <div className="p-2.5">
        <p className="truncate text-sm font-bold text-fg" title={row.channelName}>
          {row.channelName || row.announcedName}
        </p>
        {/* 2줄째 — 닉네임보다 작고 흐리게. 없으면 줄 자체가 사라진다. */}
        <SongLine row={row} className="mt-0.5" />
        {/* 발표 시점 이름이 지금과 다르면 함께 보여 준다 — 공지와 대조할 수 있게. */}
        {row.announcedName && row.announcedName !== row.channelName && (
          <p className="mt-0.5 truncate text-[11px] text-muted"
             title={`공지 표기: ${row.announcedName}`}>
            공지 표기 {row.announcedName}
          </p>
        )}
        {live && (
          <p className="mt-1 flex items-center gap-1 text-[11px]"
             style={{ color: "#FF4D4D" }}>
            <Radio size={11} aria-hidden="true" /> {nf(live.concurrentViewers)}명 시청
          </p>
        )}
      </div>
    </div>
  );
}

/* ── 곡 · 가수 한 줄 ──────────────────────────────────────────────────────
 * 카드와 목록이 함께 쓴다. 값이 **없으면 아무것도 그리지 않는다** — 빈 줄이나
 * 단독 `-`가 남으면 "정보가 있는데 못 불러왔다"로 읽힌다.
 * 서버가 준 `songTitle`/`artistName`만 쓰고 이름 문자열을 다시 쪼개지 않는다
 * (운영 클립 제목은 `가수 - 곡`과 `곡 - 가수`가 섞여 있어 추측이 불가능하다).
 */
export function songLine(row: { songTitle?: string; artistName?: string }): string {
  const song = (row.songTitle || "").trim();
  const artist = (row.artistName || "").trim();
  if (song && artist) return `${song} - ${artist}`;
  return song || artist || "";
}

function SongLine({ row, className = "" }: {
  row: { songTitle?: string; artistName?: string }; className?: string;
}) {
  const text = songLine(row);
  if (!text) return null;
  return (
    <p className={`truncate text-[11.5px] leading-snug text-muted ${className}`}
       title={text}>
      {text}
    </p>
  );
}

/* ── 명단 행 ─────────────────────────────────────────────────────────────── */
function ListRow({ row, rank }: { row: QualifierRow; rank: number | null }) {
  return (
    <li className="flex min-w-0 items-center gap-2 rounded-lg border border-border
                   bg-bg-card/60 px-2.5 py-2">
      {rank !== null ? <RankBadge rank={rank} />
        : <span className="w-8 shrink-0 text-center text-[11px] tabular-nums text-muted">
            {row.officialOrder}
          </span>}
      <span className="h-7 w-7 shrink-0 overflow-hidden rounded-full bg-bg-hover">
        {row.channelImageUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={row.channelImageUrl} alt="" width={28} height={28} loading="lazy"
               onError={hideBrokenImage} className="h-full w-full object-cover" />
        )}
      </span>
      <a href={`https://chzzk.naver.com/${row.channelId}`} target="_blank"
         rel="noopener noreferrer"
         className="nb-tap min-w-0 flex-1 transition-colors hover:text-accent">
        <span className="block truncate text-sm font-semibold text-fg">
          {row.channelName || row.announcedName}
        </span>
        <SongLine row={row} />
      </a>
      {row.live && (
        <span className="shrink-0 text-[10px] font-bold" style={{ color: "#FF4D4D" }}>
          LIVE
        </span>
      )}
      {row.clipUid && (
        <a href={`https://chzzk.naver.com/clips/${row.clipUid}`} target="_blank"
           rel="noopener noreferrer" aria-label={`${row.channelName} 클립 열기`}
           className="nb-tap-icon inline-flex h-8 w-8 shrink-0 items-center justify-center
                      rounded-lg text-muted transition-colors hover:bg-bg-hover
                      hover:text-fg">
          <ExternalLink size={13} aria-hidden="true" />
        </a>
      )}
    </li>
  );
}

/* ── 부문 섹션 ───────────────────────────────────────────────────────────── */
/** 정렬 탭 — 공개 토큰만 쓴다. 라벨의 "우승 비율·승률"은 **기준 이름**이고
 *  숫자는 어디에도 표시하지 않는다(정렬은 서버가 한다). */
export const SORT_TABS = [
  { key: "primary", label: "우승 비율" },
  { key: "secondary", label: "승률" },
] as const;

function DivisionSection({ division, label, rows, ranking, limit, showAll,
                           sort, onSort }: {
  division: Division;
  label: string;
  rows: (QualifierRow | QualifierGroupRow)[];
  ranking: PikuEntry[] | null;
  limit: number;
  showAll: boolean;
  /** 현재 정렬 기준(공개 토큰). PIKU dataset이 없으면 탭 자체가 나오지 않는다. */
  sort?: string;
  onSort?: (key: string) => void;
}) {
  // PIKU 순위가 있으면 그 순서로, 없으면 공지 순서로 보여 준다.
  // **프런트에서 순위를 다시 매기지 않는다** — 동점 규칙이 서버에 있고, 두 곳에서
  // 계산하면 규칙이 갈라진다.
  const byChannel = useMemo(() => {
    const m = new Map<string, QualifierRow>();
    for (const r of rows) {
      if (isGroupRow(r)) { const lead = groupLead(r); if (lead) m.set(lead.channelId, lead); }
      else m.set(r.channelId, r);
    }
    return m;
  }, [rows]);

  const teamOf = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of rows) {
      if (isGroupRow(r)) for (const mem of r.members) m.set(mem.channelId, r.teamNumber);
    }
    return m;
  }, [rows]);

  const ordered = useMemo(() => {
    if (ranking && ranking.length > 0) {
      return ranking
        .map((e) => ({ row: byChannel.get(e.channelId), rank: e.rank }))
        .filter((x): x is { row: QualifierRow; rank: number } => !!x.row);
    }
    const flat: { row: QualifierRow; rank: null }[] = [];
    for (const r of rows) {
      if (isGroupRow(r)) { const lead = groupLead(r); if (lead) flat.push({ row: lead, rank: null }); }
      else flat.push({ row: r, rank: null });
    }
    return flat;
  }, [ranking, rows, byChannel]);

  const top = ordered.slice(0, TOP_CARDS);
  const list = ordered.slice(0, showAll ? ordered.length : limit);
  const unit = division === "groups" ? "팀" : "명";

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="flex items-center gap-2 text-lg font-extrabold tracking-tight">
          {division === "groups"
            ? <Users size={18} style={{ color: GREEN }} aria-hidden="true" />
            : <Trophy size={18} style={{ color: GOLD }} aria-hidden="true" />}
          {label}
          <span className="text-sm font-normal text-muted tabular-nums">
            {nf(rows.length)}{unit}
          </span>
        </h3>
        {/* 정렬 탭 — PIKU dataset이 있을 때만 조작 가능하다.
            데이터가 없으면 버튼을 흉내 내지 않고 **현재 순서의 출처만** 밝힌다
            (가짜 0%로 줄을 세우면 없는 순위를 만들어내는 것이다). */}
        {ranking && ranking.length > 0 && onSort ? (
          <div role="tablist" aria-label={`${label} 정렬 기준`}
               className="nb-tap-gap flex items-center gap-1 text-[13px]">
            {SORT_TABS.map((o, i) => (
              <span key={o.key} className="flex items-center">
                {i > 0 && <span aria-hidden="true" className="px-1 text-muted/50">·</span>}
                <button type="button" role="tab" id={`${division}-sort-${o.key}`}
                        aria-selected={sort === o.key}
                        tabIndex={sort === o.key ? 0 : -1}
                        onClick={() => onSort(o.key)}
                        onKeyDown={(e) => {
                          if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                          e.preventDefault();
                          const next = SORT_TABS[(SORT_TABS.findIndex(
                            (x) => x.key === sort) + (e.key === "ArrowRight" ? 1 : -1)
                            + SORT_TABS.length) % SORT_TABS.length];
                          onSort(next.key);
                          document.getElementById(
                            `${division}-sort-${next.key}`)?.focus();
                        }}
                        /* 시각 크기는 텍스트 그대로 두고 **히트 영역만** 44px로
                           넓힌다(UI-S 계약). 정렬 탭은 촘촘히 붙어 있어
                           `nb-tap-gap`이 이웃과의 간격도 함께 벌린다. */
                        className={`nb-tap nb-tap-wide inline-flex items-center
                                    justify-center rounded px-2 py-1 transition-colors ${
                          sort === o.key
                            ? "font-semibold text-fg"
                            : "text-muted/70 hover:text-muted"}`}>
                  {o.label}
                </button>
              </span>
            ))}
          </div>
        ) : (
          /* 순서의 출처를 섹션마다 밝힌다 — 카드만 보면 공식 순위로 읽힌다. */
          <p className="text-[11px] text-muted/80">
            {ranking && ranking.length > 0
              ? "순서: PIKU 사용자 투표 기준 (공식 순위 아님)"
              : "순서: 치지직 공지 표기 순 (순위 아님)"}
          </p>
        )}
      </div>

      {top.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {top.map(({ row, rank }) => (
            <TopCard key={row.channelId} row={row} rank={rank}
                     teamNumber={division === "groups"
                       ? teamOf.get(row.channelId) : undefined} />
          ))}
        </div>
      )}

      <ul className="flex flex-col gap-1.5">
        {list.map(({ row, rank }) => (
          <ListRow key={row.channelId} row={row} rank={rank} />
        ))}
      </ul>
      {!showAll && ordered.length > limit && (
        <p className="text-center text-xs text-muted">
          이 부문 전체 {nf(ordered.length)}{unit} 중 {limit}{unit} 표시 —
          위 부문 버튼에서 전체를 볼 수 있습니다.
        </p>
      )}
    </section>
  );
}

/* ── 화면 ────────────────────────────────────────────────────────────────── */
export default function SingcupOfficial() {
  const [tab, setTab] = useState<Tab>("all");
  // 공개 정렬 토큰이다. **내부 컬럼명(win_rate 등)을 쓰지 않는다** —
  // 그 이름이 번들과 응답에 남으면 "어느 형태로도 노출 금지" 계약이 깨진다.
  const [sort, setSort] = useState("primary");
  const [data, setData] = useState<QualifiersResponse | null>(null);
  const [piku, setPiku] = useState<PikuRankingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true); setErr(null);
    api.singcup.qualifiers()
      .then((d) => { if (alive) setData(d); })
      .catch((e) => {
        if (alive) setErr(e instanceof Error ? e.message : "명단을 불러오지 못했습니다.");
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  // PIKU 순위는 **부가 정보**다. 실패해도 명단은 그대로 보여야 하므로 상태를 나눈다.
  useEffect(() => {
    let alive = true;
    api.singcup.pikuRanking(sort)
      .then((d) => { if (alive) setPiku(d); })
      .catch(() => { if (alive) setPiku(null); });
    return () => { alive = false; };
  }, [sort]);

  const rankingOf = useCallback((d: Division): PikuEntry[] | null => {
    const div = piku?.divisions?.[d];
    return div && div.available && div.entries.length > 0 ? div.entries : null;
  }, [piku]);

  const hasAnyRanking = DIVISIONS.some((d) => rankingOf(d));
  const sortOptions = piku?.sortOptions ?? [
    { key: "primary", label: "우승 비율순" },
    { key: "secondary", label: "승률순" },
  ];

  return (
    <div className="space-y-6">
      {/* ── 머리말 ── */}
      <div className="min-w-0 max-w-3xl">
        <h2 className="flex flex-wrap items-center gap-2 text-xl font-extrabold
                       tracking-tight md:text-2xl">
          <Trophy size={20} style={{ color: GOLD }} aria-hidden="true" />
          싱드컵 공식 예선 참가자
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          치지직이 공식 공지로 발표한 예선 참가자 명단입니다.
        </p>
        {/* 혼동 방지 — 계층을 들여쓰기가 아니라 구분선으로 만든다. */}
        <p className="mt-2 border-l-2 border-border pl-3 text-sm leading-relaxed
                      text-muted">
          공식 심사 결과나 순위가 아닙니다. 아래 순위는 NexBot이 PIKU의 공개
          사용자 투표 데이터를 내려받아 <b className="text-fg">다시 계산한 순서</b>이며,
          대회 주최 측의 발표와 무관합니다.
        </p>
        {/* 원문으로 갈 수 있어야 한다 — 명단을 대조하려는 사람에게 유일한 근거다. */}
        <a href={SINGCUP_QUALIFIERS.sourceUrl} target="_blank"
           rel="noopener noreferrer nofollow"
           className="btn-secondary nb-tap mt-3 inline-flex items-center gap-1.5 text-sm">
          공식 공지 원문 보기 <ExternalLink size={13} aria-hidden="true" />
        </a>
      </div>

      {/* ── 부문 · 정렬 ── */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="nb-tap-gap flex flex-wrap items-center gap-1.5"
             role="group" aria-label="부문 선택">
          {TABS.map((t) => {
            const on = tab === t.k;
            return (
              <button key={t.k} onClick={() => setTab(t.k)} aria-pressed={on}
                className="nb-tap rounded-lg border px-3 py-2 text-sm font-semibold
                           transition-colors"
                style={{ background: on ? "rgba(250,204,21,0.10)" : "transparent",
                         borderColor: on ? "rgba(250,204,21,0.40)"
                           : "rgb(var(--color-border-rgb))",
                         color: on ? GOLD : "rgb(var(--color-muted-rgb))" }}>
                {t.label}
              </button>
            );
          })}
        </div>

        {/* 정렬은 이제 **각 부문 제목 오른쪽**에 있다(요구). 여기 또 두면 한 화면에
            같은 컨트롤이 둘이 되고, 부문마다 기준이 다른지 같은지도 모호해진다.
            기준은 세 부문이 공유한다 — 부문을 바꿔도 고른 기준이 유지된다. */}
      </div>

      {/* 현재 정렬 기준을 문장으로도 밝힌다 — 버튼 색만으로는 무엇이 적용됐는지
          색각 이상 사용자에게 전달되지 않는다. */}
      {hasAnyRanking && (
        <p role="status" className="text-xs text-muted">
          현재 <b className="text-fg">
            {sortOptions.find((o) => o.key === sort)?.label ?? "우승 비율순"}
          </b>으로 정렬했습니다. 기준을 바꾸면 순위를 1위부터 다시 계산합니다.
          비율·승률 수치는 표시하지 않습니다.
        </p>
      )}

      {/* ── 상태 ── */}
      {err ? (
        <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-6">
          <p className="flex items-center gap-2 text-sm font-semibold text-red-400">
            <AlertCircle size={15} aria-hidden="true" /> 명단을 불러오지 못했습니다.
          </p>
          <p className="mt-1 text-xs text-muted">{err}</p>
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center gap-2 py-24 text-muted" aria-busy>
          <Loader2 size={18} className="animate-spin" aria-hidden="true" /> 불러오는 중...
        </div>
      ) : !data ? (
        <p className="py-24 text-center text-sm text-muted">표시할 명단이 없습니다.</p>
      ) : (
        <div className="space-y-8">
          {(tab === "all" ? DIVISIONS : [tab as Division]).map((d) => (
            <DivisionSection
              key={d}
              division={d}
              label={data.divisionLabels?.[d] ?? d}
              rows={(data.divisions?.[d] ?? []) as (QualifierRow | QualifierGroupRow)[]}
              ranking={rankingOf(d)}
              limit={OVERVIEW_ROWS}
              showAll={tab !== "all"}
              sort={sort}
              onSort={setSort} />
          ))}
        </div>
      )}

      {/* ── 출처 · 갱신 시각 ── */}
      {piku && (
        <p className="border-t border-border/60 pt-3 text-[11px] leading-relaxed
                      text-muted/80">
          사용자 투표 순위 출처:{" "}
          {DIVISIONS.map((d) => piku.divisions?.[d]).filter(Boolean).map((v, i) => (
            <span key={v!.division}>
              {i > 0 && " · "}
              {v!.sourceUrl ? (
                <a href={v!.sourceUrl} target="_blank" rel="noopener noreferrer nofollow"
                   className="underline underline-offset-2 hover:text-fg">
                  {v!.label}
                </a>
              ) : v!.label}
              {v!.lastSuccessAt
                ? ` (${new Date(v!.lastSuccessAt * 1000).toLocaleString("ko-KR", {
                    month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" })} 기준)`
                : " (아직 수집된 데이터 없음)"}
            </span>
          ))}
          . NexBot이 재계산한 순서이며 공식 결과가 아닙니다.
        </p>
      )}
    </div>
  );
}
