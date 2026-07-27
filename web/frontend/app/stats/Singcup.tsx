"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Trophy, ExternalLink, Play, Eye, Heart, Loader2, Radio, ClipboardList, X, ChevronDown,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  SingcupMain, SingcupStreamer, SingcupClip, SingcupRankings,
} from "@/lib/types";
import {
  Disclaimer, EventBadge, GOLD, GREEN, HEAT_GRAD, StaleBadge, StatusChip,
  fmtDateTime, fmtRange, hideBrokenImage, nf,
} from "./singcupShared";

// 싱드컵 메인 — 순위표가 아니라 '#싱드컵 태그 스트리머를 방송 썸네일로 둘러보는' 화면.
// 전체 순위는 우측 상단 '랭킹 보기'(별도 페이지), 자유게시판 버프 목록은
// '자유게시판 홍보글'(드로어)로 옮겼다.

type SortKey = "live" | "heart" | "recent" | "follower";
const SORTS: { k: SortKey; label: string }[] = [
  { k: "live",     label: "현재 라이브 우선" },
  { k: "heart",    label: "하트 많은 순" },
  { k: "recent",   label: "최근 클립" },
  { k: "follower", label: "팔로워 많은 순" },
];

function Tile({ label, value, unit, accent }:
  { label: string; value: string; unit?: string; accent?: boolean }) {
  return (
    <div className="card !p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1.5 tracking-tight">
        <span className="text-xl font-extrabold tabular-nums md:text-2xl"
              style={accent
                ? { background: HEAT_GRAD, WebkitBackgroundClip: "text",
                    backgroundClip: "text", color: "transparent" }
                : { color: "rgb(var(--color-fg-rgb))" }}>
          {value}
        </span>
        {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
      </p>
    </div>
  );
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="card !p-0 overflow-hidden">
          <div className="aspect-video w-full animate-pulse bg-bg-hover" />
          <div className="space-y-2 p-3">
            <div className="h-4 w-2/3 animate-pulse rounded bg-bg-hover" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-bg-hover" />
          </div>
        </div>
      ))}
    </div>
  );
}

// 스트리머 카드 — 라이브면 방송 정보, 아니면 대표 클립 정보를 보여준다.
function StreamerCard({ s }: { s: SingcupStreamer }) {
  const [open, setOpen] = useState(false);
  const [clips, setClips] = useState<SingcupClip[] | null>(null);
  const live = s.live;

  const toggle = () => {
    setOpen((v) => !v);
    if (!clips) {
      api.singcup.streamerClips(s.channelId)
        .then((d) => setClips(d.clips))
        .catch(() => setClips([]));
    }
  };

  return (
    <div className="card !p-0 overflow-hidden">
      {/* 썸네일 — 라이브면 방송 썸네일 자리에 클립 썸네일을 쓰되 LIVE 배지를 얹는다 */}
      <a href={live ? `https://chzzk.naver.com/live/${s.channelId}`
                    : `https://chzzk.naver.com/clips/${s.clipUid}`}
         target="_blank" rel="noopener noreferrer"
         className="group relative block aspect-video w-full overflow-hidden bg-bg-hover">
        {s.clipThumbnailUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={s.clipThumbnailUrl} alt="" loading="lazy" onError={hideBrokenImage}
               className="h-full w-full object-cover transition-transform group-hover:scale-105" />
        )}
        {live ? (
          <span className="absolute left-2 top-2 flex items-center gap-1 rounded px-1.5 py-0.5
                           text-[10px] font-bold"
                style={{ background: "#FF4D4D", color: "#fff" }}>
            <span className="nb-live-dot h-1.5 w-1.5 rounded-full bg-white" /> LIVE
          </span>
        ) : (
          <span className="absolute left-2 top-2 rounded px-1.5 py-0.5 text-[10px] font-bold"
                style={{ background: "rgba(0,0,0,0.6)", color: "#fff" }}>클립</span>
        )}
        <span className="absolute bottom-2 right-2 flex items-center gap-1 rounded px-1.5 py-0.5
                         text-[11px] font-bold"
              style={{ background: "rgba(0,0,0,0.65)", color: GOLD }}>
          <Heart size={11} /> {nf(s.heartCount)}
        </span>
      </a>

      <div className="p-3">
        {/* 스트리머 */}
        <div className="flex items-center gap-2">
          <span className="h-7 w-7 shrink-0 overflow-hidden rounded-full bg-bg-hover">
            {s.channelImageUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={s.channelImageUrl} alt="" width={28} height={28} loading="lazy"
                   onError={hideBrokenImage} className="h-full w-full object-cover" />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1">
              <a href={`https://chzzk.naver.com/${s.channelId}`} target="_blank"
                 rel="noopener noreferrer"
                 className="truncate text-sm font-bold text-fg hover:text-accent">
                {s.channelName || "-"}
              </a>
              {s.verifiedMark && (
                <span title="인증 채널" className="shrink-0 text-[10px]"
                      style={{ color: GREEN }}>✓</span>
              )}
            </span>
            <span className="block text-[11px] text-muted">
              팔로워 {nf(s.followerCount)}명
            </span>
          </span>
        </div>

        {/* 라이브면 방송 정보, 아니면 대표 클립 정보 */}
        {live ? (
          <div className="mt-2.5">
            <p className="truncate text-[13px] font-semibold text-fg" title={live.liveTitle}>
              {live.liveTitle || "방송 중"}
            </p>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-muted">
              <span className="flex items-center gap-1" style={{ color: "#FF4D4D" }}>
                <Radio size={11} /> {nf(live.concurrentViewers)}명 시청
              </span>
              {live.categoryName && <span className="truncate">{live.categoryName}</span>}
            </p>
          </div>
        ) : (
          <div className="mt-2.5">
            <p className="truncate text-[13px] font-semibold text-fg" title={s.clipTitle}>
              {s.clipTitle || "제목 없음"}
            </p>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[11px] text-muted">
              <span className="flex items-center gap-1"><Heart size={11} /> {nf(s.heartCount)}</span>
              <span className="flex items-center gap-1"><Eye size={11} /> {nf(s.viewCount)}</span>
            </p>
          </div>
        )}

        {/* 태그 클립 N개 — 누르면 그 스트리머의 다른 싱드컵 클립을 펼친다 */}
        {s.taggedClipCount > 1 ? (
          <button onClick={toggle}
                  className="mt-2.5 flex w-full items-center justify-between rounded-lg border
                             border-border px-2.5 py-1.5 text-[11px] text-muted
                             transition-colors hover:text-fg">
            싱드컵 태그 클립 {s.taggedClipCount}개
            <ChevronDown size={13} className="transition-transform"
                         style={{ transform: open ? "rotate(180deg)" : "none" }} />
          </button>
        ) : (
          <p className="mt-2.5 text-[11px] text-muted/70">싱드컵 태그 클립 1개</p>
        )}

        {open && (
          <div className="mt-2 space-y-1.5">
            {clips === null ? (
              <p className="py-2 text-center text-[11px] text-muted">불러오는 중...</p>
            ) : clips.length === 0 ? (
              <p className="py-2 text-center text-[11px] text-muted">클립이 없습니다.</p>
            ) : clips.map((c) => (
              <a key={c.clipUid} href={`https://chzzk.naver.com/clips/${c.clipUid}`}
                 target="_blank" rel="noopener noreferrer"
                 className="flex items-center gap-2 rounded-lg p-1.5 transition-colors hover:bg-bg-hover">
                <span className="h-8 w-14 shrink-0 overflow-hidden rounded bg-bg-hover">
                  {c.clipThumbnailUrl && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={c.clipThumbnailUrl} alt="" loading="lazy" onError={hideBrokenImage}
                         className="h-full w-full object-cover" />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[11px] text-fg">{c.clipTitle}</span>
                  <span className="block text-[10px] text-muted">
                    ♥ {nf(c.heartCount)} · {nf(c.viewCount)}회
                  </span>
                </span>
              </a>
            ))}
          </div>
        )}

        {/* 링크 */}
        <div className="mt-2.5 flex items-center gap-1.5">
          <a href={`https://chzzk.naver.com/clips/${s.clipUid}`} target="_blank"
             rel="noopener noreferrer"
             className="flex flex-1 items-center justify-center gap-1 rounded-lg px-2 py-1.5
                        text-xs font-bold text-[#04140d]"
             style={{ background: GREEN }}>
            <Play size={12} /> 클립
          </a>
          <a href={`https://chzzk.naver.com/${s.channelId}`} target="_blank"
             rel="noopener noreferrer"
             className="btn-secondary flex flex-1 items-center justify-center gap-1 !px-2 !py-1.5 text-xs">
            채널 <ExternalLink size={11} />
          </a>
        </div>
      </div>
    </div>
  );
}

// ── 자유게시판 홍보글 드로어 ────────────────────────────────────────────────
// 기존 자유게시판 버프 랭킹을 그대로 보조 화면으로 옮겨 왔다(수집기·API는 그대로).
function BoardDrawer({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<SingcupRankings | null>(null);

  useEffect(() => {
    let alive = true;
    api.singcup.rankings(100).then((d) => { if (alive) setData(d); }).catch(() => {
      if (alive) setData({ event: { id: "", name: "", startAt: "", endAt: "", status: "LIVE" },
                           summary: { submissionCount: 0, participantCount: 0,
                                      totalBuffCount: 0, topNickname: null },
                           collector: { lastSuccessAt: null, lastAttemptAt: null,
                                        status: "ERROR", stale: true, staleAfterMinutes: 0 },
                           rankings: [] });
    });
    return () => { alive = false; };
  }, []);

  // Esc로 닫기
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-[60] flex justify-end bg-black/60" onClick={onClose}>
      {/* 데스크톱: 우측 드로어 / 모바일: 전체 화면 */}
      <div onClick={(e) => e.stopPropagation()}
           role="dialog" aria-label="자유게시판 홍보글"
           className="flex h-full w-full flex-col border-l border-border bg-bg
                      sm:w-[520px] md:w-[620px]">
        <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
          <h3 className="flex items-center gap-2 text-base font-extrabold">
            <ClipboardList size={16} style={{ color: GOLD }} /> 자유게시판 홍보글
          </h3>
          <button onClick={onClose} aria-label="닫기"
                  className="rounded-lg p-1.5 text-muted transition-colors hover:text-fg">
            <X size={18} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4">
          <p className="text-[13px] leading-relaxed text-muted">
            치지직 라운지 자유게시판의 <b className="text-fg">[싱드컵]</b> 말머리 게시글입니다.
            게시판 <b className="text-fg">버프는 메인 예상 인기점수에 합산하지 않습니다.</b>
          </p>

          {!data ? (
            <div className="flex items-center gap-2 py-10 text-sm text-muted">
              <Loader2 size={15} className="animate-spin" /> 불러오는 중...
            </div>
          ) : data.rankings.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted">
              등록된 홍보글이 아직 없습니다.
            </p>
          ) : (
            <div className="mt-4 space-y-2">
              {data.rankings.map((e) => (
                <div key={e.feedId} className="rounded-xl border border-border p-3">
                  <div className="flex items-center gap-2">
                    <span className="w-6 shrink-0 text-center text-xs tabular-nums text-muted">
                      {e.rank}
                    </span>
                    <span className="h-7 w-7 shrink-0 overflow-hidden rounded-full bg-bg-hover">
                      {e.authorProfileImageUrl && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={e.authorProfileImageUrl} alt="" width={28} height={28}
                             loading="lazy" onError={hideBrokenImage}
                             className="h-full w-full object-cover" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm font-bold text-fg">
                      {e.authorNickname}
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="block text-base font-extrabold tabular-nums"
                            style={{ color: GOLD }}>{nf(e.buffCount)}</span>
                      <span className="block text-[10px] text-muted">버프</span>
                    </span>
                  </div>
                  <p className="mt-1.5 line-clamp-2 text-[13px] leading-snug text-muted"
                     title={e.title}>{e.title}</p>
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1
                                  text-[11px] text-muted/80">
                    <span>조회 {nf(e.viewCount)}</span>
                    <span>댓글 {nf(e.commentCount)}</span>
                    <span className="tabular-nums">{fmtDateTime(e.createdAt)}</span>
                  </div>
                  <div className="mt-2 flex items-center gap-1.5">
                    {e.clipUrl ? (
                      <a href={e.clipUrl} target="_blank" rel="noopener noreferrer"
                         className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[11px]
                                    font-bold text-[#04140d]" style={{ background: GREEN }}>
                        <Play size={11} /> 클립
                      </a>
                    ) : null}
                    <a href={e.postUrl} target="_blank" rel="noopener noreferrer"
                       className="btn-secondary flex items-center gap-1 !px-2 !py-1.5 text-[11px]">
                      원문 <ExternalLink size={10} />
                    </a>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Singcup() {
  const [data, setData] = useState<SingcupMain | null>(null);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortKey>("live");
  const [boardOpen, setBoardOpen] = useState(false);

  // ?view=board 로 상태를 유지한다(공유·새로고침 시에도 드로어가 열리도록)
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    setBoardOpen(p.get("view") === "board");
  }, []);

  const setView = useCallback((open: boolean) => {
    setBoardOpen(open);
    const url = new URL(window.location.href);
    if (open) url.searchParams.set("view", "board");
    else url.searchParams.delete("view");
    window.history.replaceState(null, "", url.toString());
  }, []);

  useEffect(() => {
    let alive = true;
    const load = () => api.singcup.main(200)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { /* 실패해도 마지막 정상 데이터를 유지한다 */ })
      .finally(() => { if (alive) setLoading(false); });
    load();
    const t = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const sorted = useMemo(() => {
    const list = [...(data?.streamers ?? [])];
    if (sort === "heart") list.sort((a, b) => b.heartCount - a.heartCount);
    else if (sort === "recent") list.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    else if (sort === "follower") list.sort((a, b) => b.followerCount - a.followerCount);
    else {
      // 기본: 현재 라이브 우선 → 대표 클립 하트 내림차순
      list.sort((a, b) => (Number(!!b.live) - Number(!!a.live)) || (b.heartCount - a.heartCount));
    }
    return list;
  }, [data, sort]);

  const ev = data?.event;
  const ended = ev?.status === "ENDED";

  return (
    <div className="space-y-5">
      {/* 상단 — 제목/요약 ↔ 버튼 2개 */}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-2xl">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="flex items-center gap-2 text-xl font-extrabold tracking-tight md:text-2xl">
              <Trophy size={20} style={{ color: GOLD }} /> 치지직 싱드컵
            </h2>
            <EventBadge />
            {ev && <StatusChip status={ev.status} />}
            {data?.collector.stale && !ended && <StaleBadge />}
          </div>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            치지직 음악/노래 카테고리에서 <b className="text-fg">#싱드컵</b> 태그가 확인된
            스트리머를 보여드립니다.
          </p>
          {ev && (
            <p className="mt-1 text-[13px] text-muted/80">
              <span className="tabular-nums">{fmtRange(ev.startAt, ev.endAt)}</span>
              <span className="mx-2 text-border">·</span>
              마지막 집계{" "}
              <span className="tabular-nums">{fmtDateTime(data?.collector.lastSuccessAt ?? null)}</span>
            </p>
          )}
        </div>

        {/* 모바일에서도 잘리지 않게 wrap + 최소 폭 확보 */}
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/stats/singcup/ranking"
                className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-bold text-[#1a1400]"
                style={{ background: GOLD }}>
            <Trophy size={15} /> 랭킹 보기
          </Link>
          <button onClick={() => setView(true)}
                  className="btn-secondary flex items-center gap-1.5 text-sm">
            <ClipboardList size={15} /> 자유게시판 홍보글
          </button>
        </div>
      </div>

      {ended && (
        <div className="rounded-xl border border-border px-3.5 py-2.5 text-sm text-fg">
          이벤트가 종료되었습니다.
          <span className="ml-1 text-muted">
            최종 집계 {fmtDateTime(data?.collector.lastSuccessAt ?? null)}
          </span>
        </div>
      )}

      {/* 요약 */}
      <div className="grid grid-cols-3 gap-3">
        <Tile label="태그 클립" value={nf(data?.summary.taggedClipCount ?? 0)} unit="개" />
        <Tile label="참가 스트리머" value={nf(data?.summary.streamerCount ?? 0)} unit="명" accent />
        <Tile label="현재 라이브" value={nf(data?.summary.liveCount ?? 0)} unit="명" />
      </div>

      {/* 정렬 */}
      <div className="flex flex-wrap items-center gap-2">
        {SORTS.map((s) => {
          const on = sort === s.k;
          return (
            <button key={s.k} onClick={() => setSort(s.k)}
              className="rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors"
              style={{ background: on ? `${GOLD}1a` : "transparent",
                       borderColor: on ? `${GOLD}59` : "rgb(var(--color-border-rgb))",
                       color: on ? GOLD : "rgb(var(--color-muted-rgb))" }}>
              {s.label}
            </button>
          );
        })}
      </div>

      {/* 카드 그리드 */}
      {loading && !data ? <GridSkeleton />
        : sorted.length === 0 ? (
          <div className="card py-16 text-center">
            <Trophy size={30} className="mx-auto mb-3 opacity-25" style={{ color: GOLD }} />
            <p className="font-medium text-fg">아직 확인된 #싱드컵 태그 클립이 없습니다.</p>
            {ev?.status === "UPCOMING" && (
              <p className="mt-1 text-sm text-muted">
                이벤트 시작 예정: <span className="tabular-nums">{fmtDateTime(ev.startAt)}</span>
              </p>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {sorted.map((s) => <StreamerCard key={s.channelId} s={s} />)}
          </div>
        )}

      <Disclaimer />

      {boardOpen && <BoardDrawer onClose={() => setView(false)} />}
    </div>
  );
}
