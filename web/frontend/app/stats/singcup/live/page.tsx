"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft, Trophy, ExternalLink, Play, Eye, Heart, Radio, RefreshCw, Loader2,
} from "lucide-react";
import { api } from "@/lib/api";
import { useSingcupMain } from "@/lib/useSingcupMain";
import type { SingcupStatusResponse, SingcupStreamer } from "@/lib/types";
import Footer from "@/components/Footer";
import SiteHeader from "@/components/SiteHeader";
import {
  Disclaimer, EventBadge, GOLD, GREEN, StaleBadge, StatusChip,
  fmtDateTime, fmtRange, hideBrokenImage, nf,
} from "../../singcupShared";

// 싱드컵 라이브 — #싱드컵 태그 참가자 중 **지금 방송 중인 채널만** 보여주는 화면.
// 오프라인 참가자와 전체 순위는 싱드컵 메인(/stats 의 싱드컵 탭 = 랭킹)에서 본다.
// 정렬 상태는 URL에 싣지 않는다(이 화면은 예전부터 로컬 상태만 쓴다). 그래서
// 폐지된 ?sort=follower 링크가 들어와도 읽는 쪽이 없어 기본 정렬로 열린다.
type SortKey = "viewers" | "heart" | "recent";
const SORTS: { k: SortKey; label: string }[] = [
  { k: "viewers",  label: "시청자 많은 순" },
  { k: "heart",    label: "하트 많은 순" },
  { k: "recent",   label: "최근 클립" },
];

// 클립 UID 계약 — 백엔드 `singcup_clips.py`의 `_CLIP_UID_RE`와 **같은 규칙**이다.
// 여기서 새 규칙을 만들면 두 쪽이 조용히 갈라지므로 그대로 옮겨 적는다.
const CLIP_UID_RE = /^[A-Za-z0-9_-]{1,64}$/;

/** 대표 클립 URL. 계약을 만족하는 UID일 때만 만들고, 아니면 null이다.
 *
 *  빈 값만 막으면 `abc/def`·`abc?x=1`·`../abc`·URL 통째로 같은 값이 그대로
 *  `chzzk.naver.com/clips/...` 뒤에 붙어 엉뚱한 곳을 가리키는 링크가 된다.
 *  **trim으로 보정하거나 encodeURIComponent로 위험 문자를 살려 링크를 만들지 않는다** —
 *  값이 계약을 벗어났다는 것은 우리가 그 클립을 안다고 말할 수 없다는 뜻이다. */
function clipHref(clipUid: unknown): string | null {
  return typeof clipUid === "string" && CLIP_UID_RE.test(clipUid)
    ? `https://chzzk.naver.com/clips/${clipUid}`
    : null;
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

// 스트리머 카드 — 이 화면은 라이브만 넘어오지만, 데이터가 비는 경우를 대비해
// 오프라인 표시도 남겨 둔다(대표 클립 정보로 대체).
function StreamerCard({ s }: { s: SingcupStreamer }) {
  const live = s.live;

  // 카드는 **현재 대표 클립 하나만** 보여준다. 예전에는 여기서 owner의 나머지 클립
  // 목록을 펼쳐 보여줬는데, 참가 단위가 '스트리머 1명 = 대표 클립 1개'라 부가 목록이
  // 순위와 무관한 잡음이었다.
  //
  // 대표 클립 UID가 계약을 벗어나면 링크를 만들지 않는다(clipHref 참고). 백엔드는
  // `singcup_streamers JOIN singcup_clips ON clip_uid = representative_clip_uid`를
  // `active=1`로 걸어 조립하므로(singcup_clips.py `_load_main_uncached`) 대표가 없거나
  // 비활성·삭제 확정이면 그 owner는 응답에 **아예 들어오지 않는다.** 그래도 값이
  // 비정상이면 깨진 URL을 내보내는 대신 버튼을 잠근다.
  // **프론트에서 다른 클립을 대표로 고르지 않는다** — 대표 선정은 백엔드 계약이다.
  const clipUrl = clipHref(s.clipUid);
  const thumbUrl = live ? `https://chzzk.naver.com/live/${s.channelId}` : clipUrl;

  const thumbInner = (
    <>
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
    </>
  );

  return (
    <div className="card !p-0 overflow-hidden">
      {thumbUrl ? (
        <a href={thumbUrl} target="_blank" rel="noopener noreferrer"
           className="group relative block aspect-video w-full overflow-hidden bg-bg-hover">
          {thumbInner}
        </a>
      ) : (
        <div className="group relative block aspect-video w-full overflow-hidden bg-bg-hover">
          {thumbInner}
        </div>
      )}

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

        <div className="mt-2.5 flex items-center gap-1.5">
          {clipUrl ? (
            <a href={clipUrl} target="_blank" rel="noopener noreferrer"
               className="flex flex-1 items-center justify-center gap-1 rounded-lg px-2 py-1.5
                          text-xs font-bold text-[#04140d]"
               style={{ background: GREEN }}>
              <Play size={12} /> 클립
            </a>
          ) : (
            // 링크를 만들 수 없으면 비활성으로 두고 이유를 읽을 수 있게 남긴다.
            <span aria-disabled="true"
                  title="대표 클립 정보를 아직 확인하지 못해 이동할 수 없습니다."
                  className="flex flex-1 cursor-not-allowed items-center justify-center gap-1
                             rounded-lg border border-border px-2 py-1.5 text-xs font-bold
                             text-muted opacity-70">
              <Play size={12} /> 클립 없음
            </span>
          )}
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

/** 싱드컵 LIVE 기능이 내려갔을 때의 화면.
 *
 *  **데이터 API를 부르지 않는다.** 종료된 화면이 여전히 참가자 전원(약 850KB)을
 *  받아 오면 비용만 나가고 보여 줄 것은 없다. 빈 화면처럼 보이지 않게 무엇이
 *  끝났는지와 지금 볼 수 있는 곳을 적는다. */
function LiveRetired({ notice }: { notice: string | null }) {
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 md:px-6">
      <div className="card !p-6 text-center md:!p-10">
        <Radio size={36} className="mx-auto mb-3 text-muted opacity-40" aria-hidden="true" />
        <h1 className="text-lg font-extrabold tracking-tight">
          {notice ?? "싱드컵 LIVE 화면 제공이 종료되었습니다."}
        </h1>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">
          이벤트가 끝나 참가자 실시간 방송 목록을 더 이상 갱신하지 않습니다.
          공식 예선 참가자 명단과 방송 여부는 통계의 싱드컵 메뉴에서 계속 확인하실 수
          있습니다.
        </p>
        <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
          <Link href="/stats?tab=singcup" className="btn-primary nb-tap text-sm">
            공식 예선 참가자 보기
          </Link>
          <Link href="/stats" className="btn-secondary nb-tap text-sm">통계로</Link>
        </div>
      </div>
    </main>
  );
}

export default function SingcupLivePage() {
  // 기능이 살아 있는지 **서버에 묻는다.** 화면이 스스로 판단하면 다시 열었을 때
  // 화면만 닫힌 채로 남는다.
  const [gates, setGates] = useState<SingcupStatusResponse | null>(null);
  const [gatesLoaded, setGatesLoaded] = useState(false);
  useEffect(() => {
    let alive = true;
    api.singcup.gates()
      .then((d) => { if (alive) setGates(d); })
      .catch(() => { if (alive) setGates(null); })
      .finally(() => { if (alive) setGatesLoaded(true); });
    return () => { alive = false; };
  }, []);
  const liveOpen = gates?.gates?.liveFeatureOpen === true;

  // 랭킹 탭과 **같은 공유 캐시**를 쓴다 — 두 화면을 오가도 데이터를 다시 받지 않는다.
  // (라이브 여부는 순위와 무관하므로 응답은 참가자 전원이어야 한다. 상위 N명만 받으면
  //  하위권 라이브가 통째로 빠진다 — 그래서 limit이 아니라 호출 횟수로 비용을 줄인다.)
  //
  // **기능이 열려 있을 때만 호출한다** — `enabled`가 없으면 종료된 화면도 850KB를
  // 받아 온다.
  const { data, loading, refreshing, updatedAt, refresh } =
    useSingcupMain({ enabled: liveOpen });
  const [sort, setSort] = useState<SortKey>("viewers");

  const sorted = useMemo(() => {
    // 방송 중인 참가자만 남긴다
    const list = (data?.streamers ?? []).filter((s) => s.live);
    if (sort === "heart") list.sort((a, b) => b.heartCount - a.heartCount);
    else if (sort === "recent") list.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    else list.sort((a, b) =>
      (b.live?.concurrentViewers ?? 0) - (a.live?.concurrentViewers ?? 0));
    return list;
  }, [data, sort]);

  const ev = data?.event;

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      {/* 공통 헤더 하나만 쓴다. '통계로 돌아가기'와 현재 위치는 breadcrumb에 담는다. */}
      <SiteHeader
        maxWidth="full"
        breadcrumb={
          <span className="flex min-w-0 items-center gap-2">
            <Link href="/stats"
                  className="nb-tap flex shrink-0 items-center gap-1.5 text-sm text-muted
                             transition-colors hover:text-fg">
              <ArrowLeft size={15} aria-hidden="true" /> 통계
            </Link>
            <span className="text-border" aria-hidden="true">/</span>
            <span className="flex min-w-0 items-center gap-1.5 truncate text-[15px] font-extrabold"
                  style={{ color: GOLD }}>
              <Radio size={16} aria-hidden="true" /> 싱드컵 라이브
            </span>
          </span>
        } />

      {/* 게이트를 아직 모르는 동안에는 **아무것도 단정하지 않는다.** 종료 화면을
          먼저 보였다가 목록이 나타나면 화면이 깨진 것으로 읽힌다. */}
      {!gatesLoaded ? (
        <main className="flex flex-1 items-center justify-center py-24 text-muted"
              aria-busy="true">
          <Loader2 size={18} className="animate-spin" aria-hidden="true" />
        </main>
      ) : !liveOpen ? (
        <LiveRetired notice={gates?.notices?.live ?? null} />
      ) : (
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
                싱드컵 집계{" "}
                <span className="tabular-nums">
                  {fmtDateTime(data?.collector.lastSuccessAt ?? null)}
                </span>
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {/* 자동 갱신 5분. 라이브 여부의 원본(전체 라이브 스캔)이 10분 주기라
                화면이 원본보다 빠르다. 그래도 즉시 확인하고 싶을 때를 위한 수동 갱신. */}
            <button onClick={refresh} disabled={refreshing}
                    title={updatedAt
                      ? `${fmtDateTime(new Date(updatedAt).toISOString())}에 받은 데이터입니다. 5분마다 자동으로 갱신됩니다.`
                      : "데이터를 다시 받아옵니다."}
                    className="btn-secondary flex items-center gap-1.5 text-sm disabled:opacity-60">
              <RefreshCw size={15} className={refreshing ? "animate-spin" : ""} />
              새로고침
            </button>
            <Link href="/stats?tab=singcup" className="flex items-center gap-1.5 rounded-lg px-3 py-2
                                           text-sm font-bold text-[#1a1400]"
                  style={{ background: GOLD }}>
              <Trophy size={15} /> 랭킹 보기
            </Link>
          </div>
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
              {/* 이 화면은 '라이브 중인 참가자'만 보여 준다. 새벽처럼 아무도 켜지 않은
                  시간대에는 0명이 정상이며, 오류가 아니라는 것을 문구로 구분해 준다. */}
              <p className="mt-1 text-sm text-muted leading-relaxed">
                {ev?.status === "UPCOMING"
                  ? `이벤트 시작 예정: ${fmtDateTime(ev.startAt)}`
                  : "이 화면은 지금 방송을 켠 참가자만 보여 줍니다. 오류가 아니라 현재 켜 둔 참가자가 없는 상태이며, 방송이 시작되면 자동으로 표시됩니다."}
              </p>
              <p className="mt-1.5 text-xs text-muted/70">
                오프라인 참가자를 포함한 전체 순위는 싱드컵 랭킹에서 계속 확인하실 수 있습니다.
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
      )}
      <Footer />
    </div>
  );
}
