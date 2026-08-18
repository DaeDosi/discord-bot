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
import {
  AlertCircle, BarChart3, ExternalLink, Radio, Trophy, User, Users,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  PikuEntry, PikuRankingResponse, QualifierGroupRow, QualifierRow,
  QualifiersResponse,
} from "@/lib/types";
import { SINGCUP_QUALIFIERS } from "@/lib/singcupQualifiers";
import type { MergedRow } from "@/lib/singcupOfficialMerge";
import { memberLine, mergeRanking, songLine } from "@/lib/singcupOfficialMerge";
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
function TopCard({ item, isGroup }: { item: MergedRow; isGroup: boolean }) {
  const { rank, teamNumber, displayName } = item;
  // 공식 명단에서 못 찾은 참가자도 **행은 남는다**(순위가 사라지는 편이 더 나쁘다).
  // 그때는 프로필·클립·LIVE만 없고 순위와 이름·곡은 그대로 보인다.
  const row = item.row;
  const live = row?.live ?? null;
  const clipUrl = row?.clipUid ? `https://chzzk.naver.com/clips/${row.clipUid}` : null;
  const href = live ? `https://chzzk.naver.com/live/${item.channelId}` : clipUrl;
  // **`없음`과 `못 불러옴`은 다른 상태다.** 전자는 대표 클립이 정말 없는 것이고,
  // 후자는 URL은 있는데 이미지 요청이 실패한 것이다. 둘을 같은 문구로 뭉치면
  // 수집 장애가 "원래 없는 것"으로 보여 신고조차 들어오지 않는다.
  const [thumbFailed, setThumbFailed] = useState(false);

  const thumb = (
    <>
      {row?.clipThumbnailUrl && !thumbFailed ? (
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
    <div className="card !p-0 flex h-full flex-col overflow-hidden">
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
      {/* 하단 정보 — **1줄 닉네임 / 2줄 곡 - 가수**가 고정 구조다.
          곡·가수를 닉네임 옆에 붙이지 않는다(둘의 위계가 같아 보인다).
          LIVE·시청자는 **별도 행**으로 내려 두 줄과 겹치지 않게 한다.

          높이 통일은 `min-h`만으로는 안 된다 — LIVE 카드는 시청자 줄이 하나 더
          붙어 68px가 아니라 87px가 된다(실측). 그래서 카드를 `flex h-full flex-col`,
          이 블록을 `flex-1`로 두어 **그리드의 stretch가 하단까지 전달되게** 한다.
          그러면 한 줄의 카드 5개가 가장 큰 것에 맞춰 같은 높이가 된다. */}
      <div className="flex min-h-[68px] flex-1 flex-col p-2.5">
        <p className="truncate text-sm font-bold text-fg" title={displayName}>
          {displayName}
        </p>
        <MemberLine item={item} reserve={isGroup} className="mt-0.5" />
        <SongLine row={item} className="mt-0.5" />
        {/* 발표 시점 이름이 지금과 다르면 함께 보여 준다 — 공지와 대조할 수 있게. */}
        {row?.announcedName && row.announcedName !== row.channelName && (
          <p className="mt-0.5 truncate text-[11px] text-muted"
             title={`공지 표기: ${row.announcedName}`}>
            공지 표기 {row.announcedName}
          </p>
        )}
        {live && (
          <p className="mt-auto flex items-center gap-1 pt-1 text-[11px]"
             style={{ color: "#FF4D4D" }}>
            <Radio size={11} aria-hidden="true" /> {nf(live.concurrentViewers)}명 시청
          </p>
        )}
      </div>
    </div>
  );
}

/* ── 한 줄 보조 텍스트의 높이 계약 ───────────────────────────────────────
 * 카드·행의 보조 줄은 **값이 있든 없든 정확히 한 줄(16px)**을 차지한다.
 *
 * 조건부로 그리면 데이터가 도착할 때 행이 한 줄씩 자라고 그만큼 아래 내용이 통째로
 * 밀린다 — 뷰포트 안에서 일어나는 이동이라 그대로 CLS가 된다. 특히 곡·가수는
 * **공식 명단(`/qualifiers`)에 아예 없고 PIKU 응답에서만 온다**(실측: 공식 201행
 * 전부 곡 정보 없음). 두 응답은 따로 도착하므로 예약하지 않으면 PIKU가 늦게 올 때
 * 160행이 한꺼번에 한 줄씩 자란다.
 *
 * 값이 없을 때는 `aria-hidden`으로 접근성 트리에서 빼 둔다 — 화면 읽기 사용자가
 * 빈 줄을 읽을 이유가 없다. 넘치는 글자는 잘라 숨기지 않고 말줄임으로 처리하며
 * 전체 값은 `title`로 확인할 수 있게 둔다. */
const LINE = "h-4 truncate leading-4";

/* 곡 · 가수 한 줄. 카드와 목록이 **같은 컴포넌트**를 쓴다.
 * 문자열 계산은 `lib/singcupOfficialMerge`의 `songLine` 하나뿐이다. */
function SongLine({ row, className = "" }: {
  row: { songTitle?: string; artistName?: string }; className?: string;
}) {
  const text = songLine(row);
  return (
    <p className={`${LINE} text-[11.5px] text-muted ${className}`}
       aria-hidden={text ? undefined : true} title={text || undefined}>
      {text}
    </p>
  );
}

/* ── 그룹 멤버 보조 줄 ───────────────────────────────────────────────────
 * 대표자를 **제외한** 팀원 이름. 프로필·링크는 대표자 것 하나만 쓰고, 나머지 멤버는
 * 여기서 이름만 밝힌다 — 팀원이 화면에서 아예 사라져 있었다.
 *
 * 자리를 예약할지는 **부문**이 정한다(그룹만 `reserve`). `memberNames`가 비었는지로
 * 판단하면 1인 팀(실측 32팀 중 1팀)만 행 높이가 달라지고, 정렬이 바뀌어 그 팀이
 * 화면에 들어왔다 나갈 때마다 목록 전체 높이가 흔들린다.
 * 솔로 부문은 `reserve=false`라 줄 자체가 생기지 않는다 — 빈 자리도 만들지 않는다. */
function MemberLine({ item, reserve = false, className = "" }: {
  item: { memberNames?: string[] }; reserve?: boolean; className?: string;
}) {
  const text = memberLine(item);
  if (!text && !reserve) return null;
  return (
    <p className={`${LINE} text-[11px] text-muted/85 ${className}`}
       aria-hidden={text ? undefined : true}
       title={text ? `멤버 ${text}` : undefined}>
      {text ? <><span className="text-muted/60">멤버 </span>{text}</> : null}
    </p>
  );
}

/* ── 명단 행 ─────────────────────────────────────────────────────────────── */
function ListRow({ item, isGroup }: { item: MergedRow; isGroup: boolean }) {
  const { rank, displayName } = item;
  const row = item.row;   // 없을 수 있다 — 그래도 행은 지우지 않는다.
  const clipUrl = row?.clipUid
    ? `https://chzzk.naver.com/clips/${row.clipUid}` : null;

  /* 행에는 링크가 **둘**이고 서로 형제다.
   *   · 프로필(아바타 + 이름) → 치지직 채널
   *   · 나머지 넓은 영역(멤버 줄 + 곡 줄) → 대표 클립
   * `<a>` 안에 `<a>`를 넣지 않는다 — 중첩 링크는 브라우저가 마크업을 다시 쓰고
   * 스크린리더가 목적을 하나로 읽는다. 오른쪽 클립 아이콘은 제거했다(행 자체가
   * 클립으로 가므로 같은 목적지가 두 번 있을 이유가 없다).
   * 클립이 없으면 링크를 만들지 않고 같은 자리를 평범한 블록으로 둔다 —
   * 빈 새 창을 여는 것보다 아무 일도 일어나지 않는 편이 정직하다. */
  const body = (
    <>
      <MemberLine item={item} reserve={isGroup} />
      <SongLine row={item} />
    </>
  );

  return (
    <li className="flex min-w-0 items-center gap-2 rounded-lg border border-border
                   bg-bg-card/60 px-2.5 py-2">
      {rank !== null ? <RankBadge rank={rank} />
        : <span className="w-8 shrink-0 text-center text-[11px] tabular-nums text-muted">
            {row?.officialOrder ?? "-"}
          </span>}

      {/* 프로필 링크 — 아바타와 이름이 한 덩어리다. */}
      <a href={`https://chzzk.naver.com/${item.channelId}`} target="_blank"
         rel="noopener noreferrer"
         aria-label={`${displayName} 치지직 프로필 보기`}
         className="nb-tap group/prof flex min-w-0 max-w-[45%] shrink-0 items-center
                    gap-2 rounded transition-colors hover:text-accent
                    focus-visible:outline focus-visible:outline-2
                    focus-visible:outline-offset-2 focus-visible:outline-accent">
        {/* 프로필을 못 찾아도 자리를 비우지 않는다 — 원형 기본 아바타를 둔다. */}
        <span className="flex h-7 w-7 shrink-0 items-center justify-center
                         overflow-hidden rounded-full bg-bg-hover text-[11px] text-muted">
          {row?.channelImageUrl
            // eslint-disable-next-line @next/next/no-img-element
            ? <img src={row.channelImageUrl} alt="" width={28} height={28} loading="lazy"
                   onError={hideBrokenImage} className="h-full w-full object-cover" />
            : <User size={13} aria-hidden="true" />}
        </span>
        <span className="truncate text-sm font-semibold text-fg" title={displayName}>
          {displayName}
        </span>
      </a>

      {/* 나머지 영역 = 대표 클립. 프로필과 형제이므로 클릭이 섞이지 않는다. */}
      {clipUrl ? (
        <a href={clipUrl} target="_blank" rel="noopener noreferrer"
           aria-label={`${displayName} 대표 클립 보기`}
           className="nb-tap min-w-0 flex-1 rounded transition-colors
                      hover:text-accent focus-visible:outline focus-visible:outline-2
                      focus-visible:outline-offset-2 focus-visible:outline-accent">
          {body}
        </a>
      ) : (
        <div className="min-w-0 flex-1" data-clip="none">
          {body}
          <span className="sr-only">대표 클립 없음</span>
        </div>
      )}

      {row?.live && (
        <span className="shrink-0 text-[10px] font-bold" style={{ color: "#FF4D4D" }}>
          LIVE
        </span>
      )}
    </li>
  );
}

/* ── 로딩 스켈레톤 ───────────────────────────────────────────────────────
 * 예전에는 이 자리에 스피너 한 줄(약 200px)만 있었다. 데이터가 도착하면 그 자리에
 * 3개 부문 × (카드 5 + 행 10)이 들어서며 화면이 2,700px 넘게 길어지고, **그 아래에
 * 있던 페이지 하단 고지가 통째로 화면 밖으로 밀려났다**(실측 1440px `v=0.0866`).
 * 뷰포트 안에서 일어나는 이동이라 그대로 CLS가 된다.
 *
 * 그래서 로딩 화면이 **최종 화면과 같은 구조**를 그린다. 부문 수·카드 수(`TOP_CARDS`)·
 * 행 수(`OVERVIEW_ROWS`)는 데이터가 아니라 상수라서 미리 알 수 있고, 카드와 행은
 * 최종본과 **같은 클래스·같은 높이 계약**을 쓴다. 열 수는 같은 grid 클래스가 정하므로
 * 320px부터 1440px까지 뷰포트마다 알아서 맞는다 — 고정 px 하나로 덮지 않는다.
 * (페이지 전체에 큰 `min-height`를 씌워 빈 공간을 만드는 방식은 쓰지 않는다.)
 *
 * 빈 껍데기는 읽을 내용이 없으므로 `aria-hidden`으로 접근성 트리에서 빼고, 상태는
 * `role="status"` 문장 하나로만 알린다. */
function SkeletonBar({ className = "" }: { className?: string }) {
  return <span className={`block animate-pulse rounded bg-bg-hover ${className}`} />;
}

function DivisionSkeleton({ isGroup }: { isGroup: boolean }) {
  return (
    <section className="space-y-3" aria-hidden="true">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        {/* 제목 줄 — 실제 h3와 같은 글자 크기·간격을 쓴다. */}
        <h3 className="flex items-center gap-2 text-lg font-extrabold tracking-tight">
          <SkeletonBar className="h-[18px] w-[18px] rounded-full" />
          <SkeletonBar className="h-[18px] w-28" />
        </h3>
        <SkeletonBar className="h-4 w-40" />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: TOP_CARDS }, (_, i) => (
          <div key={i} className="card !p-0 flex h-full flex-col overflow-hidden">
            <div className="aspect-video w-full animate-pulse bg-bg-hover" />
            {/* 실제 카드 하단부와 **같은** `min-h-[68px] p-2.5`와 같은 줄 높이. */}
            <div className="flex min-h-[68px] flex-1 flex-col p-2.5">
              <SkeletonBar className="h-5 w-2/3" />
              {isGroup && <SkeletonBar className="mt-0.5 h-4 w-5/6" />}
              <SkeletonBar className="mt-0.5 h-4 w-4/5" />
            </div>
          </div>
        ))}
      </div>

      <ul className="flex flex-col gap-1.5">
        {Array.from({ length: OVERVIEW_ROWS }, (_, i) => (
          <li key={i} className="flex min-w-0 items-center gap-2 rounded-lg border
                                 border-border bg-bg-card/60 px-2.5 py-2">
            <SkeletonBar className="h-[19px] w-9 shrink-0" />
            {/* `nb-tap`을 그대로 물려받는다 — 터치 뷰포트에서만 44px 바닥이 생기므로
                (`@media (pointer: coarse)`) 스켈레톤에 빼 두면 390px에서만 행이
                10.7px 낮아져 최종본과 높이가 어긋난다(실측). 고정 px 대신 같은
                클래스를 쓰는 이유가 이것이다. */}
            <span className="nb-tap flex min-w-0 max-w-[45%] shrink-0 items-center gap-2">
              <SkeletonBar className="h-7 w-7 shrink-0 rounded-full" />
              <SkeletonBar className="h-5 w-24" />
            </span>
            <span className="nb-tap block min-w-0 flex-1">
              {isGroup && <SkeletonBar className="h-4 w-1/2" />}
              <SkeletonBar className="h-4 w-2/3" />
            </span>
          </li>
        ))}
      </ul>
    </section>
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
  /* 병합은 `lib/singcupOfficialMerge`가 전부 한다 — 여기서 다시 만들지 말 것.
   * 예전에는 이 자리에 인라인 병합이 있었고 두 가지가 깨져 있었다:
   * PIKU 항목에서 rank만 꺼내 곡·가수를 버렸고, 색인을 팀 `members[0]`만으로
   * 만든 뒤 못 찾은 행을 `filter`로 지워 그룹 32팀 중 14팀이 사라졌다.
   * **프런트에서 순위를 다시 매기지 않는다** — 동점 규칙은 서버에 있다. */
  const ordered = useMemo(() => mergeRanking(rows, ranking), [rows, ranking]);

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
          {top.map((item) => (
            <TopCard key={item.channelId} item={item} isGroup={division === "groups"} />
          ))}
        </div>
      )}

      <ul className="flex flex-col gap-1.5">
        {list.map((item) => (
          <ListRow key={item.channelId} item={item} isGroup={division === "groups"} />
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
export default function SingcupOfficial({ onRanking }: {
  /** 비공식 인기점수 랭킹으로 넘어가는 경로.
   *
   *  **두 순위는 서로 다른 것이다** — 이쪽은 PIKU 사용자 투표, 저쪽은 클립 조회수·
   *  하트로 NexBot이 계산한 값이다. 그래서 한 랭킹으로 합치지 않는다. 다만 가는
   *  길이 없으면 그 화면은 사실상 사라진 것과 같아서(실제로 그랬다) 입구를 둔다. */
  onRanking?: () => void;
} = {}) {
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
        <div className="nb-tap-gap mt-3 flex flex-wrap items-center gap-2">
          {/* 원문으로 갈 수 있어야 한다 — 명단을 대조하려는 사람에게 유일한 근거다. */}
          <a href={SINGCUP_QUALIFIERS.sourceUrl} target="_blank"
             rel="noopener noreferrer nofollow"
             className="btn-secondary nb-tap inline-flex items-center gap-1.5 text-sm">
            공식 공지 원문 보기 <ExternalLink size={13} aria-hidden="true" />
          </a>
          {onRanking && (
            <button type="button" onClick={onRanking}
                    className="btn-secondary nb-tap inline-flex items-center gap-1.5 text-sm">
              <BarChart3 size={13} aria-hidden="true" /> 비공식 인기점수 보기
            </button>
          )}
        </div>
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
          색각 이상 사용자에게 전달되지 않는다.

          이 문장은 PIKU 응답이 도착해야 나온다. 조건부로 그리면 명단(`/qualifiers`)
          보다 늦게 올 때 이 줄이 끼어들며 아래 전체가 한 줄만큼 밀린다(실측 47px).
          그래서 **문장은 항상 그리고**, 아직일 때만 `invisible`로 숨긴다 —
          `visibility: hidden`은 자리를 그대로 두면서 접근성 트리에서도 빠지므로
          `role="status"`가 빈 문장을 먼저 읽어 버리는 일이 없다. `hidden`을 쓰면
          자리까지 사라져 원래 문제로 돌아간다. 글줄 수는 실제 문장이 정하므로
          320px에서 3줄, 1440px에서 1줄로 뷰포트마다 알아서 맞는다. */}
      <div className={hasAnyRanking ? undefined : "invisible"}
           aria-hidden={hasAnyRanking ? undefined : true}>
        <p role="status" className="text-xs text-muted">
          현재 <b className="text-fg">
            {sortOptions.find((o) => o.key === sort)?.label ?? "우승 비율순"}
          </b>으로 정렬했습니다. 기준을 바꾸면 순위를 1위부터 다시 계산합니다.
          비율·승률 수치는 표시하지 않습니다.
        </p>
      </div>

      {/* ── 상태 ── */}
      {err ? (
        <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-6">
          <p className="flex items-center gap-2 text-sm font-semibold text-red-400">
            <AlertCircle size={15} aria-hidden="true" /> 명단을 불러오지 못했습니다.
          </p>
          <p className="mt-1 text-xs text-muted">{err}</p>
        </div>
      ) : loading ? (
        /* 최종 화면과 같은 구조·같은 높이 계약으로 자리를 잡아 둔다(위 주석 참고).
           스피너 한 줄로 되돌리면 하단 고지가 다시 화면 밖으로 밀린다. */
        <div className="space-y-8" aria-busy="true">
          <p role="status" className="sr-only">
            공식 예선 참가자 명단을 불러오는 중입니다.
          </p>
          {DIVISIONS.map((d) => (
            <DivisionSkeleton key={d} isGroup={d === "groups"} />
          ))}
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
