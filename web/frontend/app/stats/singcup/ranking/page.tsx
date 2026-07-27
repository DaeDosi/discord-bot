"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft, Bot, Trophy, ExternalLink, Play, Eye, Heart, Radio,
} from "lucide-react";
import { api } from "@/lib/api";
import type { SingcupMain, SingcupStreamer } from "@/lib/types";
import ThemeToggle from "@/components/ThemeToggle";
import Footer from "@/components/Footer";
import {
  Delta, Disclaimer, EventBadge, GOLD, GREEN, HEAT_GRAD, MEDAL, RankDelta, ScoreFormula,
  StaleBadge, StatusChip, fmtDateTime, fmtRange, hideBrokenImage, nf,
} from "../../singcupShared";

// 싱드컵 랭킹 — 메인('둘러보기')과 분리된 별도 화면.
// 정렬/점수 계산은 백엔드가 끝낸 값을 쓰고, 여기서는 표시 정렬만 바꾼다.

type SortKey = "score" | "heart" | "heart24h" | "view" | "follower";
const SORTS: { k: SortKey; label: string }[] = [
  { k: "score",    label: "예상 인기점수" },
  { k: "heart",    label: "하트 많은 순" },
  { k: "heart24h", label: "24시간 하트 급상승" },
  { k: "view",     label: "조회수 많은 순" },
  { k: "follower", label: "팔로워 많은 순" },
];

function Row({ s, index }: { s: SingcupStreamer; index: number }) {
  const medal = index < 3 ? MEDAL[index] : null;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border p-3 sm:flex-nowrap"
         style={{ borderColor: medal ? `${medal}66` : "rgb(var(--color-border-rgb))",
                  background: medal ? `${medal}0d` : undefined }}>
      {/* 순위 + 변화 */}
      <span className="flex w-12 shrink-0 flex-col items-center">
        <span className="text-base font-extrabold tabular-nums"
              style={{ color: medal ?? "rgb(var(--color-muted-rgb))" }}>
          {s.rank}
        </span>
        <RankDelta value={s.rankDelta} isNew={s.isNew} />
      </span>

      {/* 클립 썸네일 */}
      <a href={`https://chzzk.naver.com/clips/${s.clipUid}`} target="_blank"
         rel="noopener noreferrer"
         className="hidden h-12 w-20 shrink-0 overflow-hidden rounded bg-bg-hover sm:block">
        {s.clipThumbnailUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={s.clipThumbnailUrl} alt="" loading="lazy" onError={hideBrokenImage}
               className="h-full w-full object-cover" />
        )}
      </a>

      {/* 스트리머 + 클립 제목 */}
      <span className="min-w-0 flex-1 basis-full sm:basis-auto">
        <span className="flex items-center gap-1.5">
          <span className="h-6 w-6 shrink-0 overflow-hidden rounded-full bg-bg-hover">
            {s.channelImageUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={s.channelImageUrl} alt="" width={24} height={24} loading="lazy"
                   onError={hideBrokenImage} className="h-full w-full object-cover" />
            )}
          </span>
          <span className="truncate text-sm font-bold text-fg">{s.channelName || "-"}</span>
          {s.verifiedMark && <span style={{ color: GREEN }} title="인증 채널">✓</span>}
          {s.live && (
            <span className="flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[9px] font-bold"
                  style={{ background: "rgba(255,77,77,0.15)", color: "#FF4D4D" }}>
              <Radio size={9} /> LIVE
            </span>
          )}
        </span>
        <span className="mt-0.5 block truncate text-[13px] text-muted" title={s.clipTitle}>
          {s.clipTitle}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[11px] text-muted/80">
          <span>팔로워 {nf(s.followerCount)}</span>
          <span className="flex items-center gap-1"><Eye size={11} /> {nf(s.viewCount)}</span>
          <span className="flex items-center gap-1"><Heart size={11} /> {nf(s.heartCount)}</span>
          <span className="tabular-nums">{fmtDateTime(s.createdAt)}</span>
        </span>
      </span>

      {/* 점수 분해 */}
      <span className="shrink-0 text-right">
        <span className="block text-xl font-extrabold tabular-nums md:text-2xl"
              style={{ background: HEAT_GRAD, WebkitBackgroundClip: "text",
                       backgroundClip: "text", color: "transparent" }}>
          {s.score.toFixed(2)}
        </span>
        <span className="block text-[10px] text-muted">비공식 예상 인기점수</span>
        <span className="mt-0.5 block text-[10px] tabular-nums text-muted/80">
          조회 {s.viewScore.toFixed(1)}/70 · 하트 {s.heartScore.toFixed(1)}/30
        </span>
      </span>

      {/* 변화량 */}
      <span className="shrink-0 text-right text-[11px]">
        <span className="block text-muted">
          직전 <Delta value={s.heartDelta} />
        </span>
        <span className="block text-muted">
          24시간{" "}
          {s.heartChangeRate24h === null
            ? <span className="font-bold" style={{ color: GOLD }}>NEW</span>
            : <Delta value={s.heartChangeRate24h} suffix="%" />}
        </span>
      </span>

      {/* 링크 */}
      <span className="flex shrink-0 items-center gap-1.5">
        <a href={`https://chzzk.naver.com/clips/${s.clipUid}`} target="_blank"
           rel="noopener noreferrer"
           className="flex items-center gap-1 rounded-lg px-2.5 py-2 text-xs font-bold text-[#04140d]"
           style={{ background: GREEN }}>
          <Play size={12} /> 클립
        </a>
        <a href={`https://chzzk.naver.com/${s.channelId}`} target="_blank"
           rel="noopener noreferrer"
           className="btn-secondary flex items-center gap-1 !px-2.5 !py-2 text-xs">
          채널 <ExternalLink size={11} />
        </a>
      </span>
    </div>
  );
}

export default function SingcupRankingPage() {
  const [data, setData] = useState<SingcupMain | null>(null);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortKey>("score");

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

  const rows = useMemo(() => {
    const list = [...(data?.streamers ?? [])];
    if (sort === "heart") list.sort((a, b) => b.heartCount - a.heartCount);
    else if (sort === "view") list.sort((a, b) => b.viewCount - a.viewCount);
    else if (sort === "follower") list.sort((a, b) => b.followerCount - a.followerCount);
    else if (sort === "heart24h") {
      list.sort((a, b) => (b.heartChangeRate24h ?? -1e9) - (a.heartChangeRate24h ?? -1e9));
    }
    // score는 백엔드가 매긴 순서 그대로(동점 타이브레이커까지 반영돼 있다)
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
              <Trophy size={16} /> 싱드컵 랭킹
            </span>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1400px] flex-1 space-y-5 px-4 py-6 md:px-6">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0 max-w-2xl">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-extrabold tracking-tight md:text-2xl">
                치지직 싱드컵 랭킹
              </h1>
              <EventBadge />
              {ev && <StatusChip status={ev.status} />}
              {data?.collector.stale && ev?.status !== "ENDED" && <StaleBadge />}
            </div>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              공개 조회수와 하트 수로 계산한 <b className="text-fg">비공식 예상 인기점수</b> 순위입니다.
              스트리머마다 가장 높은 하트를 받은 클립 하나만 집계합니다.
            </p>
            <div className="mt-1">
              <ScoreFormula />
            </div>
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
          <Link href="/stats" className="btn-secondary flex items-center gap-1.5 text-sm">
            <ArrowLeft size={15} /> 싱드컵 메인
          </Link>
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

        {loading && !data ? (
          <div className="space-y-2">
            {Array.from({ length: 10 }, (_, i) => (
              <div key={i} className="h-20 animate-pulse rounded-xl bg-bg-hover" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="card py-16 text-center">
            <Trophy size={30} className="mx-auto mb-3 opacity-25" style={{ color: GOLD }} />
            <p className="font-medium text-fg">아직 순위를 계산할 클립이 없습니다.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {rows.map((s, i) => <Row key={s.channelId} s={s} index={sort === "score" ? i : -1} />)}
          </div>
        )}

        <Disclaimer />
      </main>
      <Footer />
    </div>
  );
}
