"use client";
/**
 * 싱드컵 **시즌 → 단계(본선/예선)** 순위 화면.
 *
 * 이 화면이 답하는 질문은 하나다 — "이번 시즌 본선·예선에서 사용자 투표로는 어떤 순서인가."
 *
 * **두 가지를 절대 섞지 않는다.**
 *  · 공식 발표 명단  — 치지직 공지에서 온 확정 값. 순위가 아니다.
 *  · PIKU 투표 순위  — PIKU 공개 투표 데이터로 정리한 순서. 공식 결과가 아니다.
 * 방문자에게는 이 구분과 갱신 시각만 **짧게** 알린다. 수집 방식·자동화 대상·일정 출처 같은
 * 운영 정보는 Nexadmin에만 둔다(PUBLIC-UX-1).
 *
 * 표시 계약(요구):
 *  · 우승 비율·승률 **숫자를 화면에 내보내지 않는다.** 서버 응답에 아예 없다.
 *  · 조회수·하트도 표시하지 않는다.
 *  · 정렬은 `우승 비율` / `승률` 두 탭이고, 바꾸면 **1위부터 다시 계산**된다
 *    (계산은 서버가 한다 — 프런트에서 다시 매기면 두 규칙이 갈라진다).
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle, BarChart3, ExternalLink, Radio, Trophy, User, Users,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  PikuCampaign, PikuEntry, PikuRankingResponse, PikuSeason, QualifierGroupRow, QualifierRow,
  QualifiersResponse,
} from "@/lib/types";
import { SINGCUP_QUALIFIERS } from "@/lib/singcupQualifiers";
import type { MergedRow } from "@/lib/singcupOfficialMerge";
import { mergeRanking, songLine, teamNames } from "@/lib/singcupOfficialMerge";
import type { Stage } from "@/lib/singcupStage";
import { classifyFinal, pickStage } from "@/lib/singcupStage";
import {
  STAGE_LABEL, UNOFFICIAL_NOTICE, collectionNotice, lastUpdatedText, resolveSeasons, stageHint,
  stagesOf,
} from "@/lib/singcupSeason";
import { GOLD, GREEN, hideBrokenImage, nf } from "./singcupShared";

const DIVISIONS = ["female_solo", "male_solo", "groups"] as const;
type Division = (typeof DIVISIONS)[number];
/** 본선 source key. 예선 부문이 아니라 별도 단계(campaign)다 — `DIVISIONS`에 넣지 않는다. */
const FINAL = "final";
type SectionKey = Division | typeof FINAL;

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

/* ── 순위 배지 ───────────────────────────────────────────────────────────── */
function RankBadge({ rank }: { rank: number }) {
  // TOP 3만 색으로 구분하고, 색만으로 뜻을 전하지 않도록 숫자를 함께 둔다.
  const color = rank === 1 ? GOLD : rank === 2 ? "#C0C6D4" : rank === 3 ? "#CD7F32" : null;
  return (
    <span className="inline-flex w-9 shrink-0 items-center justify-center rounded-md border
                     px-1 py-0.5 text-[11px] font-extrabold tabular-nums leading-none"
          style={color
            ? { color, borderColor: `${color}66`, background: `${color}14` }
            : { color: "rgb(var(--color-muted-rgb))",
                borderColor: "rgb(var(--color-border-rgb))" }}>
      {rank}위
    </span>
  );
}

/* ── TOP 카드 ────────────────────────────────────────────────────────────── */
function TopCard({ item }: { item: MergedRow }) {
  const { rank, teamNumber } = item;
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
          `prefers-reduced-motion` 처리가 거기 들어 있다). */}
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
      {/* 하단 정보 — **1줄 이름(대표자 먼저, 팀원 포함) / 2줄 곡 - 가수**가 고정 구조다.
          카드는 한 줄에 여러 장이 나란히 서므로 이름 줄을 말줄임으로 한 줄에 묶는다
          (전체 이름은 title). 목록 행은 줄바꿈한다 — 아래 `ListRow`.
          LIVE·시청자는 **별도 행**으로 내려 두 줄과 겹치지 않게 한다.

          높이 통일은 `min-h`만으로는 안 된다 — LIVE 카드는 시청자 줄이 하나 더
          붙는다. 그래서 카드를 `flex h-full flex-col`, 이 블록을 `flex-1`로 두어
          **그리드의 stretch가 하단까지 전달되게** 한다. */}
      <div className="flex min-h-[68px] flex-1 flex-col p-2.5">
        <NameLine item={item} />
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
 * 곡 줄은 **값이 있든 없든 정확히 한 줄(16px)**을 차지한다.
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

/* ── 이름 줄 ─────────────────────────────────────────────────────────────
 * **대표자가 항상 첫 번째**, 이어서 팀원 전원을 원본 순서대로 — 전부 굵게(PUBLIC-UX-1).
 * 예전에는 대표자만 굵게, 팀원은 회색 `멤버` 줄로 곡 줄과 같은 위계에 섞여 있었다.
 * 이름 순서·중복 제거는 `teamNames` 하나가 한다(화면에서 다시 조립하지 않는다).
 *
 * 이름 줄은 **값이 항상 있다**(대표자 이름). 그래서 솔로·팀 행의 기본 높이가 같고,
 * 예전처럼 부문에 따라 빈 멤버 줄을 예약할 필요가 없다.
 *
 * `wrap`(목록 행)이면 긴 팀 이름이 자연스럽게 줄바꿈되고, 카드는 한 줄 말줄임이다.
 * 구분점은 장식이라 읽지 않게 하고, 화면 읽기용 쉼표를 따로 둔다. */
function NameLine({ item, wrap = false, className = "" }: {
  item: { displayName: string; memberNames?: string[] }; wrap?: boolean; className?: string;
}) {
  const names = teamNames(item);
  const full = names.join(" · ");
  return (
    <p className={`text-sm font-bold leading-5 text-fg ${wrap
         ? "break-words [overflow-wrap:anywhere]" : "truncate"} ${className}`}
       title={full || undefined}>
      {names.map((n, i) => (
        <Fragment key={`${i}-${n}`}>
          {i > 0 && (
            <>
              <span aria-hidden="true" className="px-1 font-normal text-muted/60">·</span>
              <span className="sr-only">, </span>
            </>
          )}
          <span>{n}</span>
        </Fragment>
      ))}
    </p>
  );
}

/* ── 명단 행 ─────────────────────────────────────────────────────────────── */
function ListRow({ item }: { item: MergedRow }) {
  const { rank, displayName } = item;
  const row = item.row;   // 없을 수 있다 — 그래도 행은 지우지 않는다.
  const clipUrl = row?.clipUid
    ? `https://chzzk.naver.com/clips/${row.clipUid}` : null;

  /* 행에는 링크가 **둘**이고 서로 형제다.
   *   · 프로필(아바타) → 대표자의 치지직 채널
   *   · 콘텐츠 열(이름 줄 + 곡 줄) → 대표 클립
   * `<a>` 안에 `<a>`를 넣지 않는다 — 중첩 링크는 브라우저가 마크업을 다시 쓰고
   * 스크린리더가 목적을 하나로 읽는다. 팀원 이름은 링크가 아니다(팀원별 프로필을
   * 한 줄에 여러 개 두면 44px 터치 영역을 지킬 수 없고, 클립 링크와 겹친다).
   *
   * 콘텐츠 열이 **아바타 오른쪽의 한 열**이라 곡 줄은 이름 줄 아래·같은 들여쓰기에 서고,
   * 순위 배지·아바타 아래로 파고들지 않는다.
   * 클립이 없으면 링크를 만들지 않고 같은 자리를 평범한 블록으로 둔다 —
   * 빈 새 창을 여는 것보다 아무 일도 일어나지 않는 편이 정직하다. */
  const body = (
    <>
      <NameLine item={item} wrap />
      <SongLine row={item} className="mt-0.5" />
    </>
  );

  return (
    <li className="flex min-w-0 items-center gap-2 rounded-lg border border-border
                   bg-bg-card/60 px-2.5 py-2">
      {rank !== null ? <RankBadge rank={rank} />
        : <span className="w-9 shrink-0 text-center text-[11px] tabular-nums text-muted">
            {row?.officialOrder ?? "-"}
          </span>}

      {/* 프로필 링크 — 대표자 아바타. 크기는 고정이라 이름 줄바꿈과 무관하게 흔들리지 않는다. */}
      <a href={`https://chzzk.naver.com/${item.channelId}`} target="_blank"
         rel="noopener noreferrer"
         aria-label={`${displayName} 치지직 프로필 보기`}
         className="nb-tap nb-tap-icon flex shrink-0 items-center justify-center rounded-full
                    transition-opacity hover:opacity-80
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
      </a>

      {/* 콘텐츠 열 = 대표 클립. 프로필과 형제이므로 클릭이 섞이지 않는다. */}
      {clipUrl ? (
        <a href={clipUrl} target="_blank" rel="noopener noreferrer"
           className="nb-tap block min-w-0 flex-1 rounded transition-colors
                      hover:text-accent focus-visible:outline focus-visible:outline-2
                      focus-visible:outline-offset-2 focus-visible:outline-accent">
          {body}
          <span className="sr-only"> 대표 클립 보기</span>
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
 *
 * 그래서 로딩 화면이 **최종 화면과 같은 구조**를 그린다. 부문 수·카드 수(`TOP_CARDS`)·
 * 행 수(`OVERVIEW_ROWS`)는 데이터가 아니라 상수라서 미리 알 수 있고, 카드와 행은
 * 최종본과 **같은 클래스·같은 높이 계약**을 쓴다.
 *
 * 빈 껍데기는 읽을 내용이 없으므로 `aria-hidden`으로 접근성 트리에서 빼고, 상태는
 * `role="status"` 문장 하나로만 알린다. */
function SkeletonBar({ className = "" }: { className?: string }) {
  return <span className={`block animate-pulse rounded bg-bg-hover ${className}`} />;
}

function DivisionSkeleton() {
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
            {/* 실제 카드 하단부와 **같은** `min-h-[68px] p-2.5`와 같은 줄 높이(이름 20 + 곡 16). */}
            <div className="flex min-h-[68px] flex-1 flex-col p-2.5">
              <SkeletonBar className="h-5 w-2/3" />
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
                (`@media (pointer: coarse)`) 스켈레톤에 빼 두면 390px에서만 행 높이가
                최종본과 어긋난다(실측). 고정 px 대신 같은 클래스를 쓰는 이유가 이것이다. */}
            <span className="nb-tap nb-tap-icon flex shrink-0 items-center justify-center">
              <SkeletonBar className="h-7 w-7 shrink-0 rounded-full" />
            </span>
            <span className="nb-tap block min-w-0 flex-1">
              <SkeletonBar className="h-5 w-1/2" />
              <SkeletonBar className="mt-0.5 h-4 w-2/3" />
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
  division: SectionKey;
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
   * **프런트에서 순위를 다시 매기지 않는다** — 동점 규칙은 서버에 있다.
   *
   * 본선(`final`)은 한 표에 솔로와 팀이 섞여 있다(실측 팀 12 · 솔로 20). 팀 여부는
   * PIKU가 준 `teamMembers`로 정한다(`mixed`). */
  const mixed = division === FINAL;
  const ordered = useMemo(
    () => mergeRanking(rows, ranking, { mixed }), [rows, ranking, mixed]);

  const top = ordered.slice(0, TOP_CARDS);
  const list = ordered.slice(0, showAll ? ordered.length : limit);
  const unit = division === "groups" ? "팀" : mixed ? "" : "명";
  const teamCount = mixed ? ordered.filter((x) => x.teamNumber !== undefined).length : 0;

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="flex items-center gap-2 text-lg font-extrabold tracking-tight">
          {division === "groups"
            ? <Users size={18} style={{ color: GREEN }} aria-hidden="true" />
            : <Trophy size={18} style={{ color: GOLD }} aria-hidden="true" />}
          {label}
          <span className="text-sm font-normal text-muted tabular-nums">
            {mixed
              ? (ranking && ranking.length > 0
                  ? `${nf(ordered.length)} (팀 ${nf(teamCount)} · 개인 ${nf(ordered.length - teamCount)})`
                  : "")
              : `${nf(rows.length)}${unit}`}
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
                           넓힌다(UI-S 계약). 선택 상태는 색이 아니라 **굵기와 밑줄**로도
                           드러낸다 — 예전의 긴 정렬 안내 문장을 대신한다. */
                        className={`nb-tap nb-tap-wide inline-flex items-center
                                    justify-center rounded px-2 py-1 transition-colors ${
                          sort === o.key
                            ? "font-semibold text-fg underline decoration-2 underline-offset-4"
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
            <TopCard key={item.channelId} item={item} />
          ))}
        </div>
      )}

      <ul className="flex flex-col gap-1.5">
        {list.map((item) => (
          <ListRow key={item.channelId} item={item} />
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
  // 사용자가 직접 고른 단계. null이면 데이터로 정한다(아래 `pickStage`).
  const [explicitStage, setStage] = useState<Stage | null>(null);
  // 사용자가 고른 시즌. null이면 서버가 준 첫 시즌(가장 최근).
  const [seasonKey, setSeasonKey] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("all");
  // 공개 정렬 토큰이다. **내부 컬럼명(win_rate 등)을 쓰지 않는다** —
  // 그 이름이 번들과 응답에 남으면 "어느 형태로도 노출 금지" 계약이 깨진다.
  const [sort, setSort] = useState("primary");
  const [data, setData] = useState<QualifiersResponse | null>(null);
  const [piku, setPiku] = useState<PikuRankingResponse | null>(null);
  // 본선 순위는 예선과 **다른 요청·다른 상태**다. 한 상태에 섞으면 한쪽이 늦게 올 때
  // 다른 쪽 화면이 비거나 덮인다.
  const [finalRank, setFinalRank] = useState<PikuRankingResponse | null>(null);
  const [finalErr, setFinalErr] = useState(false);
  const [finalSettled, setFinalSettled] = useState(false);
  const [campaigns, setCampaigns] = useState<PikuCampaign[] | null>(null);
  const [seasonList, setSeasonList] = useState<PikuSeason[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // 시즌 → 단계. 정본은 서버이고, 구 백엔드일 때만 `lib/singcupSeason`의 폴백을 쓴다.
  const seasons = resolveSeasons(seasonList);
  const season = seasons.find((s) => s.season === seasonKey) ?? seasons[0];
  const stages = stagesOf(season, campaigns);
  const finalInfo = stages.find((s) => s.stage === "final") ?? null;
  const qualInfo = stages.find((s) => s.stage === "qualifier") ?? null;
  const finalCampaign = campaigns?.find((c) => c.campaign === finalInfo?.campaign) ?? null;
  const qualCampaign = campaigns?.find((c) => c.campaign === qualInfo?.campaign) ?? null;
  const finalCampaignKey = finalInfo?.campaign ?? FINAL;
  const finalSourceKey = finalCampaign?.sources?.[0]?.key ?? FINAL;

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

  // 본선 순위. 예선 응답에 섞어 주지 않고 따로 받는다. campaign 키는 시즌 정의에서 온다.
  useEffect(() => {
    let alive = true;
    setFinalErr(false);
    api.singcup.pikuRanking(sort, undefined, 0, finalCampaignKey)
      .then((d) => { if (alive) setFinalRank(d); })
      .catch(() => { if (alive) { setFinalRank(null); setFinalErr(true); } })
      .finally(() => { if (alive) setFinalSettled(true); });
    return () => { alive = false; };
  }, [sort, finalCampaignKey]);

  // 시즌·단계 상태와 마지막 갱신 시각. 실패해도 화면은 뜬다 — 시각 줄만 빈다.
  useEffect(() => {
    let alive = true;
    api.singcup.pikuCampaigns()
      .then((d) => {
        if (!alive) return;
        setCampaigns(d.campaigns ?? null);
        setSeasonList(d.seasons ?? null);
      })
      .catch(() => { if (alive) setCampaigns(null); });
    return () => { alive = false; };
  }, []);

  // 본선 가용성(공개본 / 미공개 / 구 백엔드 / 실패)과 그에 따른 기본 단계.
  const finalAvail = classifyFinal(finalRank, finalErr, finalSettled, finalSourceKey);
  const finalEntries: PikuEntry[] | null =
    finalAvail.state === "published" ? finalAvail.entries : null;
  const stage: Stage | null = finalInfo ? pickStage(finalAvail, explicitStage) : "qualifier";
  /** 본선 병합에 쓰는 공식 명단 — 세 부문 전체(본선은 여성·남성·그룹이 한 표에 섞여 있다). */
  const allOfficialRows = useMemo(
    () => DIVISIONS.flatMap((d) => (data?.divisions?.[d] ?? []) as (QualifierRow | QualifierGroupRow)[]),
    [data]);

  const rankingOf = useCallback((d: Division): PikuEntry[] | null => {
    const div = piku?.divisions?.[d];
    return div && div.available && div.entries.length > 0 ? div.entries : null;
  }, [piku]);

  // 갱신 안내 = [갱신 방식 한 문장] + [마지막 갱신]. 둘 다 서버 상태에서만 온다(모드 하드코딩 없음).
  const finalPolicy = collectionNotice(finalCampaign);
  const finalLast = lastUpdatedText(finalCampaign);
  const qualLast = lastUpdatedText(qualCampaign);
  const finalSourceUrl = finalRank?.divisions?.[finalSourceKey]?.sourceUrl ?? "";

  return (
    <div className="space-y-6">
      {/* ── 시즌 · 단계 ──
          시즌이 하나뿐이면 이름만 조용히 적고, 둘 이상이면 고를 수 있게 한다. */}
      <div className="space-y-2">
        {seasons.length > 1 ? (
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="singcup-season" className="text-xs font-semibold text-muted">
              시즌
            </label>
            <select id="singcup-season" value={season.season}
                    onChange={(e) => { setSeasonKey(e.target.value); setStage(null); }}
                    className="nb-tap rounded-lg border border-border bg-bg px-2.5 py-1.5
                               text-sm font-semibold text-fg focus:border-accent
                               focus:outline-none">
              {seasons.map((s) => <option key={s.season} value={s.season}>{s.label}</option>)}
            </select>
          </div>
        ) : (
          <p className="text-sm font-bold text-fg" data-testid="singcup-season">
            {season.label}
          </p>
        )}

        <div className="nb-tap-gap flex flex-wrap items-center gap-1.5" role="tablist"
             aria-label={`${season.label} 단계`}>
          {stages.map((info) => {
            const on = stage === info.stage;
            const hint = stageHint(info.state,
              info.stage === "final" ? finalAvail.state : undefined);
            return (
              <button key={info.stage} type="button" role="tab" id={`stage-${info.stage}`}
                      aria-selected={on} tabIndex={on ? 0 : -1}
                      onClick={() => setStage(info.stage)}
                      onKeyDown={(e) => {
                        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                        e.preventDefault();
                        const cur = Math.max(0, stages.findIndex((s) => s.stage === stage));
                        const next = stages[(cur + (e.key === "ArrowRight" ? 1 : -1)
                          + stages.length) % stages.length].stage;
                        setStage(next);
                        document.getElementById(`stage-${next}`)?.focus({ preventScroll: true });
                      }}
                      /* 선택 상태는 색만이 아니라 **굵기와 아래 테두리**로도 드러낸다. */
                      className={`nb-tap rounded-lg border px-3 py-2 text-sm transition-colors ${
                        on ? "font-bold" : "font-semibold"}`}
                      style={{ background: on ? "rgba(250,204,21,0.10)" : "transparent",
                               borderColor: on ? "rgba(250,204,21,0.40)"
                                 : "rgb(var(--color-border-rgb))",
                               boxShadow: on ? `inset 0 -2px 0 ${GOLD}` : undefined,
                               color: on ? GOLD : "rgb(var(--color-muted-rgb))" }}>
                {STAGE_LABEL[info.stage]}
                <span className="ml-1.5 text-[11px] font-normal opacity-80">{hint}</span>
              </button>
            );
          })}
        </div>
      </div>

      {stage === null ? (
        /* 본선 응답을 기다리는 동안 — 어느 단계도 미리 고르지 않는다(빈 본선·통째 교체 방지).
           골격은 예선 형태(3부문)로 둔다: 본선 미공개·구 백엔드·실패 모두 예선으로 끝나고,
           본선 공개 뒤에는 어차피 1섹션 32행이라 어느 쪽이든 한 번은 바뀐다 — 실측으로
           예선 골격이 두 경우의 CLS 합이 더 작았다. */
        <div className="space-y-8" aria-busy="true">
          <p role="status" className="sr-only">싱드컵 순위를 불러오는 중입니다.</p>
          {DIVISIONS.map((d) => <DivisionSkeleton key={d} />)}
        </div>
      ) : stage === "final" ? (
        /* ── 본선 ── */
        <div className="min-w-0 max-w-3xl">
          <h2 className="flex flex-wrap items-center gap-2 text-xl font-extrabold
                         tracking-tight md:text-2xl">
            <Trophy size={20} style={{ color: GOLD }} aria-hidden="true" />
            본선 순위
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            {finalSourceUrl ? (
              <a href={finalSourceUrl} target="_blank" rel="noopener noreferrer nofollow"
                 className="underline underline-offset-2 hover:text-fg">PIKU</a>
            ) : "PIKU"}{" "}
            {UNOFFICIAL_NOTICE}
          </p>
          {/* 갱신 안내 — 갱신 방식(수집 주기와 공개 반영을 구분) + 마지막 갱신 한 번.
              campaigns 응답이 늦게 오므로 자리를 미리 잡는다(좁은 화면 두 줄, 넓은 화면 한 줄). */}
          <p className="mt-1.5 min-h-8 text-[12px] leading-4 text-muted/85 sm:min-h-4"
             role="status" aria-hidden={finalPolicy || finalLast ? undefined : true}
             data-testid="final-update">
            {finalPolicy && <span>{finalPolicy}</span>}
            {finalPolicy && finalLast && " "}
            {finalLast && <span className="whitespace-nowrap">{finalLast}</span>}
          </p>
        </div>
      ) : (
        /* ── 예선 ── */
        <div className="min-w-0 max-w-3xl">
          <h2 className="flex flex-wrap items-center gap-2 text-xl font-extrabold
                         tracking-tight md:text-2xl">
            <Trophy size={20} style={{ color: GOLD }} aria-hidden="true" />
            예선 순위
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            <b className="text-fg">
              {STAGE_LABEL.qualifier} {stageHint(qualInfo?.state ?? "ended")}
            </b>
            {qualInfo?.state === "ended" ? " · 예선 결과는 기록으로 보관됩니다." : ""}
          </p>
          {/* 본선이 아직 없을 때만 — 예선이 기본 화면인 이유를 짧게 밝힌다. */}
          {finalInfo && finalAvail.state !== "published" && finalAvail.state !== "loading" && (
            <p className="mt-1 text-[12px] text-muted/85" role="status" data-testid="final-pending">
              {finalAvail.state === "error"
                ? "본선 순위를 불러오지 못했습니다."
                : "본선 순위는 준비 중입니다."}
            </p>
          )}
          <p className="mt-2 text-sm leading-relaxed text-muted">
            참가자 명단은 치지직 공식 공지 기준입니다. PIKU {UNOFFICIAL_NOTICE}
          </p>
          {/* 종료된 단계는 갱신 방식을 적지 않는다 — 마지막 갱신만. */}
          <p className="mt-1.5 min-h-4 text-[12px] leading-4 text-muted/85" role="status"
             aria-hidden={qualLast ? undefined : true} data-testid="qualifier-update">
            {qualLast}
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
      )}

      {/* ── 본선 화면 ── */}
      {stage === "final" && (
        finalAvail.state === "error" ? (
          <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-6">
            <p className="flex items-center gap-2 text-sm font-semibold text-red-400">
              <AlertCircle size={15} aria-hidden="true" /> 본선 순위를 불러오지 못했습니다.
            </p>
          </div>
        ) : loading || finalAvail.state === "loading" ? (
          <div className="space-y-8" aria-busy="true">
            <p role="status" className="sr-only">본선 순위를 불러오는 중입니다.</p>
            <DivisionSkeleton />
          </div>
        ) : !finalEntries ? (
          <div className="rounded-xl border border-border bg-bg-card/60 p-6 text-center">
            <p className="text-sm font-semibold text-fg">아직 공개된 본선 순위가 없습니다.</p>
            <p className="mt-1 text-xs text-muted">
              {finalAvail.state === "unsupported"
                ? "서비스 갱신이 진행 중입니다. 잠시 뒤 다시 확인해 주세요. "
                : "본선 순위가 공개되면 이 자리에 표시됩니다. "}
              예선 결과는{" "}
              <button type="button" onClick={() => setStage("qualifier")}
                      className="underline underline-offset-2 hover:text-fg">예선 탭</button>
              에서 볼 수 있습니다.
            </p>
          </div>
        ) : (
          <div className="space-y-8">
            <DivisionSection
              division={FINAL}
              label="파이널 본선"
              rows={allOfficialRows}
              ranking={finalEntries}
              limit={finalEntries.length}
              showAll
              sort={sort}
              onSort={setSort} />
          </div>
        )
      )}

      {/* ── 부문 · 정렬 (예선) ── */}
      {stage === "qualifier" && (<>
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
        {/* 정렬은 **각 부문 제목 오른쪽**에 있다. 기준은 세 부문이 공유한다. */}
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
            <DivisionSkeleton key={d} />
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
      </>)}
    </div>
  );
}
