"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft, Bot, Trophy, ExternalLink, Play, Eye, Heart, Radio, ChevronDown,
} from "lucide-react";
import { api } from "@/lib/api";
import type { SingcupMain, SingcupStreamer, SingcupClip } from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";
import {
  Disclaimer, EventBadge, GOLD, GREEN, StaleBadge, StatusChip,
  fmtDateTime, fmtRange, hideBrokenImage, nf,
} from "../../singcupShared";

// 싱드컵 라이브 — #싱드컵 태그 참가자 중 **지금 방송 중인 채널만** 보여주는 화면.
// 오프라인 참가자와 전체 순위는 싱드컵 메인(/stats 의 싱드컵 탭 = 랭킹)에서 본다.
type SortKey = "viewers" | "heart" | "recent" | "follower";
const SORTS: { k: SortKey; label: string }[] = [
  { k: "viewers",  label: "시청자 많은 순" },
  { k: "heart",    label: "하트 많은 순" },
  { k: "recent",   label: "최근 클립" },
  { k: "follower", label: "팔로워 많은 순" },
];

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

// 스트리머 카드 — 이 화면은 라이브만 넘어오지만, 데이터가 비는 경우를 대비해
// 오프라인 표시도 남겨 둔다(대표 클립 정보로 대체).
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
        {/* 메인 랭킹에서의 순위를 함께 보여줘 두 화면이 이어지게 한다 */}
        <span className="absolute bottom-2 left-2 rounded px-1.5 py-0.5 text-[10px] font-bold
                         tabular-nums"
              style={{ background: "rgba(0,0,0,0.65)", color: "#fff" }}>
          #{s.rank}
        </span>
      </a>

      <div className="p-3">
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

export default function SingcupLivePage() {
  const [data, setData] = useState<SingcupMain | null>(null);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortKey>("viewers");

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
    // 방송 중인 참가자만 남긴다
    const list = (data?.streamers ?? []).filter((s) => s.live);
    if (sort === "heart") list.sort((a, b) => b.heartCount - a.heartCount);
    else if (sort === "recent") list.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    else if (sort === "follower") list.sort((a, b) => b.followerCount - a.followerCount);
    else list.sort((a, b) =>
      (b.live?.concurrentViewers ?? 0) - (a.live?.concurrentViewers ?? 0));
    return list;
  }, [data, sort]);

  const ev = data?.event;

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="flex w-full items-center justify-between px-4 md:px-6" style={{ height: 60 }}>
          <div className="flex items-center gap-2.5">
            <Link href="/" className="flex items-center gap-2 text-[15px] font-bold text-muted
                                      transition-colors hover:text-fg">
              <Bot size={18} className="text-accent" /> NexBot
            </Link>
            <span className="text-border">/</span>
            <Link href="/stats" className="flex items-center gap-1.5 text-sm text-muted
                                           transition-colors hover:text-fg">
              <ArrowLeft size={15} /> 통계
            </Link>
            <span className="text-border">/</span>
            <span className="flex items-center gap-1.5 text-[15px] font-extrabold"
                  style={{ color: GOLD }}>
              <Radio size={16} /> 싱드컵 라이브
            </span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1600px] flex-1 space-y-5 px-4 py-6 md:px-6">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0 max-w-2xl">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-extrabold tracking-tight md:text-2xl">싱드컵 라이브</h1>
              <EventBadge />
              {ev && <StatusChip status={ev.status} />}
              {data?.collector.stale && ev?.status !== "ENDED" && <StaleBadge />}
            </div>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              <b className="text-fg">#싱드컵</b> 태그가 확인된 스트리머 중{" "}
              <b className="text-fg">지금 방송 중인 채널만</b> 보여드립니다.
              방송을 켜지 않은 참가자와 전체 순위는{" "}
              <Link href="/stats?tab=singcup" className="underline hover:text-fg">싱드컵 랭킹</Link>에서
              확인하세요.
            </p>
            {ev && (
              <p className="mt-1 text-[13px] text-muted/80">
                <span className="tabular-nums">{fmtRange(ev.startAt, ev.endAt)}</span>
                <span className="mx-2 text-border">·</span>
                마지막 집계{" "}
                <span className="tabular-nums">
                  {fmtDateTime(data?.collector.lastSuccessAt ?? null)}
                </span>
              </p>
            )}
          </div>
          <Link href="/stats?tab=singcup" className="flex items-center gap-1.5 rounded-lg px-3 py-2
                                         text-sm font-bold text-[#1a1400]"
                style={{ background: GOLD }}>
            <Trophy size={15} /> 랭킹 보기
          </Link>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <div className="card !p-4">
            <p className="text-sm text-muted">지금 방송 중</p>
            <p className="mt-1.5 text-xl font-extrabold tabular-nums md:text-2xl"
               style={{ color: "#FF4D4D" }}>
              {nf(sorted.length)}
              <span className="ml-1 text-sm font-normal text-muted">명</span>
            </p>
          </div>
          <div className="card !p-4">
            <p className="text-sm text-muted">총 시청자</p>
            <p className="mt-1.5 text-xl font-extrabold tabular-nums md:text-2xl">
              {nf(sorted.reduce((a, s) => a + (s.live?.concurrentViewers ?? 0), 0))}
              <span className="ml-1 text-sm font-normal text-muted">명</span>
            </p>
          </div>
          <div className="card !p-4">
            <p className="text-sm text-muted">전체 참가 스트리머</p>
            <p className="mt-1.5 text-xl font-extrabold tabular-nums md:text-2xl">
              {nf(data?.summary.streamerCount ?? 0)}
              <span className="ml-1 text-sm font-normal text-muted">명</span>
            </p>
          </div>
        </div>

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

        {loading && !data ? <GridSkeleton />
          : sorted.length === 0 ? (
            <div className="card py-16 text-center">
              <Radio size={30} className="mx-auto mb-3 opacity-25" style={{ color: GOLD }} />
              <p className="font-medium text-fg">지금 방송 중인 참가 스트리머가 없습니다.</p>
              <p className="mt-1 text-sm text-muted">
                {ev?.status === "UPCOMING"
                  ? `이벤트 시작 예정: ${fmtDateTime(ev.startAt)}`
                  : "참가자가 방송을 켜면 여기에 표시됩니다."}
              </p>
              <Link href="/stats?tab=singcup" className="mt-4 inline-flex items-center gap-1.5 rounded-lg
                                             px-3 py-2 text-sm font-bold text-[#1a1400]"
                    style={{ background: GOLD }}>
                <Trophy size={15} /> 전체 순위 보기
              </Link>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {sorted.map((s) => <StreamerCard key={s.channelId} s={s} />)}
            </div>
          )}

        <Disclaimer />
      </main>
      <Footer />
    </div>
  );
}
