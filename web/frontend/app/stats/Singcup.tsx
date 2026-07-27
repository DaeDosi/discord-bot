"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Trophy, ExternalLink, Play, Eye, Heart, Loader2, Radio, ClipboardList, X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { SingcupMain, SingcupStreamer, SingcupRankings } from "@/lib/types";
import {
  Delta, Disclaimer, EventBadge, GOLD, GREEN, MEDAL, RankDelta, SCORE_GRAD, ScoreFormula,
  ScoreText, StaleBadge, StatusChip, fmtDateTime, fmtRange, hideBrokenImage, nf,
} from "./singcupShared";

// 싱드컵 메인 = 랭킹 화면.
// 썸네일 카드로 둘러보는 화면은 '싱드컵 라이브'(/stats/singcup/live),
// 자유게시판 버프 목록은 '자유게시판 홍보글' 드로어(?view=board)로 나가 있다.

type SortKey = "score" | "heart" | "heart24h" | "view" | "follower";
const SORTS: { k: SortKey; label: string }[] = [
  { k: "score",    label: "예상 인기점수" },
  { k: "heart",    label: "하트 많은 순" },
  { k: "heart24h", label: "24시간 하트 급상승" },
  { k: "view",     label: "조회수 많은 순" },
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
                ? { background: SCORE_GRAD, WebkitBackgroundClip: "text",
                    backgroundClip: "text", color: "transparent" }
                : { color: "rgb(var(--color-fg-rgb))" }}>
          {value}
        </span>
        {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
      </p>
    </div>
  );
}

// 랭킹 한 줄 — 데스크톱은 가로 배치, 모바일은 자연스럽게 접힌다.
function Row({ s, index }: { s: SingcupStreamer; index: number }) {
  const medal = index >= 0 && index < 3 ? MEDAL[index] : null;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border p-3 sm:flex-nowrap"
         style={{ borderColor: medal ? `${medal}66` : "rgb(var(--color-border-rgb))",
                  background: medal ? `${medal}0d` : undefined }}>
      <span className="flex w-12 shrink-0 flex-col items-center">
        <span className="text-base font-extrabold tabular-nums"
              style={{ color: medal ?? "rgb(var(--color-muted-rgb))" }}>
          {s.rank}
        </span>
        <RankDelta value={s.rankDelta} isNew={s.isNew} />
      </span>

      <a href={`https://chzzk.naver.com/clips/${s.clipUid}`} target="_blank"
         rel="noopener noreferrer"
         className="hidden h-12 w-20 shrink-0 overflow-hidden rounded bg-bg-hover sm:block">
        {s.clipThumbnailUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={s.clipThumbnailUrl} alt="" loading="lazy" onError={hideBrokenImage}
               className="h-full w-full object-cover" />
        )}
      </a>

      <span className="min-w-0 flex-1 basis-full sm:basis-auto">
        <span className="flex items-center gap-1.5">
          <span className="h-6 w-6 shrink-0 overflow-hidden rounded-full bg-bg-hover">
            {s.channelImageUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={s.channelImageUrl} alt="" width={24} height={24} loading="lazy"
                   onError={hideBrokenImage} className="h-full w-full object-cover" />
            )}
          </span>
          <a href={`https://chzzk.naver.com/${s.channelId}`} target="_blank"
             rel="noopener noreferrer"
             className="truncate text-sm font-bold text-fg hover:text-accent">
            {s.channelName || "-"}
          </a>
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

      <span className="shrink-0 text-right">
        <span className="block text-xl font-extrabold tabular-nums md:text-2xl"
              style={{ background: SCORE_GRAD, WebkitBackgroundClip: "text",
                       backgroundClip: "text", color: "transparent" }}>
          {s.score.toFixed(2)}
        </span>
        <span className="block text-[10px] text-muted">비공식 예상 인기점수</span>
        <span className="mt-0.5 block whitespace-nowrap text-[10px] tabular-nums text-muted/80">
          조회 {s.viewScore.toFixed(1)}/70 · 하트 {s.heartScore.toFixed(1)}/30
        </span>
      </span>

      {/* 변화량 — 세로로 겹쳐 두면 '직전 -' / '24시간 NEW'가 좁게 눌려 읽기 어려웠다.
          라벨 위 / 값 아래의 작은 블록 두 개를 가로로 나란히 두고 최소 폭을 확보한다. */}
      <span className="flex shrink-0 items-center justify-end gap-4 sm:min-w-[132px]">
        <span className="text-center">
          <span className="block whitespace-nowrap text-[10px] text-muted">직전</span>
          <span className="mt-0.5 block whitespace-nowrap text-xs">
            <Delta value={s.heartDelta} />
          </span>
        </span>
        <span className="text-center">
          <span className="block whitespace-nowrap text-[10px] text-muted">24시간</span>
          <span className="mt-0.5 block whitespace-nowrap text-xs">
            {s.heartChangeRate24h === null
              ? <span className="font-bold" style={{ color: GOLD }}>NEW</span>
              : <Delta value={s.heartChangeRate24h} suffix="%" />}
          </span>
        </span>
      </span>

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

// ── 자유게시판 홍보글 드로어 ────────────────────────────────────────────────
// 기존 자유게시판 버프 랭킹을 보조 화면으로 옮겨 왔다(수집기·API는 그대로).
function BoardDrawer({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<SingcupRankings | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    api.singcup.rankings(100)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, []);

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
            게시판 <b className="text-fg">버프는 예상 인기점수에 합산하지 않습니다.</b>
          </p>

          {failed ? (
            <p className="py-10 text-center text-sm text-muted">
              홍보글을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.
            </p>
          ) : !data ? (
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
                    {e.clipUrl && (
                      <a href={e.clipUrl} target="_blank" rel="noopener noreferrer"
                         className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-[11px]
                                    font-bold text-[#04140d]" style={{ background: GREEN }}>
                        <Play size={11} /> 클립
                      </a>
                    )}
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
  const [sort, setSort] = useState<SortKey>("score");
  const [boardOpen, setBoardOpen] = useState(false);

  // ?view=board 로 상태를 유지한다(새로고침·공유에도 드로어가 열리도록)
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
  const ended = ev?.status === "ENDED";

  return (
    <div className="space-y-5">
      {/* 상단 — 제목/요약 ↔ 버튼 2개 */}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-2xl">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="flex items-center gap-2 text-xl font-extrabold tracking-tight md:text-2xl">
              <Trophy size={20} style={{ color: GOLD }} /> 치지직 싱드컵 랭킹
            </h2>
            <EventBadge />
            {ev && <StatusChip status={ev.status} />}
            {data?.collector.stale && !ended && <StaleBadge />}
          </div>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            치지직 음악/노래 카테고리에서 <b className="text-fg">#싱드컵</b> 태그가 확인된 클립을
            공개 조회수와 하트 수로 계산한 <ScoreText>비공식 예상 인기점수</ScoreText> 순위입니다.
            스트리머마다 가장 높은 하트를 받은 클립 하나만 집계합니다.
          </p>
          <div className="mt-1"><ScoreFormula /></div>
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

        {/* 모바일에서도 잘리지 않게 wrap */}
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/stats/singcup/live"
                className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-bold text-[#1a1400]"
                style={{ background: GOLD }}>
            <Radio size={15} /> 싱드컵 라이브
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

      {/* 랭킹 */}
      {loading && !data ? (
        <div className="space-y-2">
          {Array.from({ length: 10 }, (_, i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-bg-hover" />
          ))}
        </div>
      ) : rows.length === 0 ? (
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
        <div className="space-y-2">
          {rows.map((s, i) => <Row key={s.channelId} s={s} index={sort === "score" ? i : -1} />)}
        </div>
      )}

      <Disclaimer />

      {boardOpen && <BoardDrawer onClose={() => setView(false)} />}
    </div>
  );
}
