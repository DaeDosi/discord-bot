"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Trophy, ExternalLink, Play, Eye, Heart, Loader2, Radio, ClipboardList, X,
  Search, ArrowDown, ArrowUp,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  SingcupHeartMover, SingcupMain, SingcupStreamer, SingcupRankings,
} from "@/lib/types";
import {
  Delta, Disclaimer, EventBadge, GOLD, GREEN, MEDAL, RankDelta, SCORE_GRAD, ScoreFormula,
  ScoreText, StaleBadge, StatusChip, VERMILION, fmtDateTime, fmtRange, hideBrokenImage, nf,
} from "./singcupShared";

// 싱드컵 메인 = 랭킹 화면.
// 썸네일 카드로 둘러보는 화면은 '싱드컵 라이브'(/stats/singcup/live),
// 자유게시판 버프 목록은 '자유게시판 홍보글' 드로어(?view=board)로 나가 있다.

type SortKey = "score" | "heart" | "heart24h" | "view" | "follower" | "rankmove";
type SortDir = "desc" | "asc";
const SORTS: { k: SortKey; label: string }[] = [
  { k: "score",    label: "예상 인기점수" },
  { k: "heart",    label: "하트 많은 순" },
  { k: "heart24h", label: "24시간 하트 급상승" },
  { k: "view",     label: "조회수 많은 순" },
  { k: "follower", label: "팔로워 많은 순" },
  // '변동이 많은 순'이면 하트인지 점수인지 알 수 없다 → 무엇의 변동인지 이름에 넣는다
  { k: "rankmove", label: "순위 변동 많은 순" },
];
const isSort = (v: string | null): v is SortKey =>
  !!v && SORTS.some((s) => s.k === v);

/** '이 라이브 숫자는 언제 확인한 것인가' — 60초 폴링과 실제 갱신 주기가 다르다는 걸 알린다 */
function LiveFreshness({ live }: { live?: SingcupMain["live"] }) {
  if (!live?.collectedAt) return <span className="text-muted/60">집계 대기</span>;
  const min = Math.max(0, Math.round((Date.now() - new Date(live.collectedAt).getTime()) / 60000));
  return (
    <span className={live.isStale ? "text-amber-400" : "text-muted"}
          title={`전체 라이브 목록을 약 ${Math.round(live.intervalSeconds / 60)}분 주기로 확인합니다.`
                 + (live.nextExpectedAt
                    ? ` 다음 예상: ${new Date(live.nextExpectedAt).toLocaleTimeString("ko-KR",
                        { hour: "2-digit", minute: "2-digit" })}`
                    : "")}>
      {min < 1 ? "방금 확인" : `${min}분 전 확인`}{live.isStale && " · 지연"}
    </span>
  );
}

/** KPI 증감 배지 — 비교 기록이 없는 것(—)과 변화가 없는 것(±0)을 구분해서 보여준다 */
function DeltaBadge({ delta, base }: { delta: number | null; base?: string | null }) {
  const tip = delta == null
    ? "약 1시간 전 수집값이 없어 비교할 수 없습니다"
    : "현재 수집값과 약 1시간 전 수집값의 차이입니다"
      + (base ? ` (기준 ${new Date(base).toLocaleTimeString("ko-KR",
          { hour: "2-digit", minute: "2-digit" })})` : "");
  const [txt, color] =
    delta == null ? ["—", "rgb(var(--color-muted-rgb))"] as const
    : delta > 0 ? [`▲ ${nf(delta)}`, GREEN] as const
    : delta < 0 ? [`▼ ${nf(Math.abs(delta))}`, "#EF4444"] as const
    : ["±0", "rgb(var(--color-muted-rgb))"] as const;
  return (
    <span className="whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-bold tabular-nums"
          style={{ color, background: "rgb(var(--color-bg-hover-rgb) / 0.6)" }} title={tip}>
      {txt}
    </span>
  );
}

function Tile({ label, value, unit, accent, delta, windowMin, baseAt, foot }:
  { label: string; value: string; unit?: string; accent?: boolean;
    /** 1시간 전 대비 증가분. null이면 비교할 이력이 아직 없다 */
    delta?: number | null; windowMin?: number; baseAt?: string | null;
    foot?: React.ReactNode }) {
  return (
    <div className="card !p-4">
      <p className="text-sm text-muted">{label}</p>
      {/* 값 + 단위 + 증감 배지를 한 flex 행으로. 배지는 단위 오른쪽에 두되 본래 값과
          혼동되지 않게 작게 쓴다. 좁은 화면에서는 배지가 다음 줄로 내려간다. */}
      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-3 tracking-tight">
        <span className="whitespace-nowrap">
          <span className="text-xl font-extrabold tabular-nums md:text-2xl"
                style={accent
                  ? { background: SCORE_GRAD, WebkitBackgroundClip: "text",
                      backgroundClip: "text", color: "transparent" }
                  : { color: "rgb(var(--color-fg-rgb))" }}>
            {value}
          </span>
          {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
        </span>
        {delta !== undefined && <DeltaBadge delta={delta} base={baseAt} />}
      </div>
      {/* 비교 기준은 배지와 떨어지지 않게 바로 아래에 둔다 */}
      {delta !== undefined && (
        <p className="mt-0.5 whitespace-nowrap text-[11px] text-muted/70">
          {delta == null ? "비교 데이터 없음" : `${windowMin ?? 60}분 전 대비`}
        </p>
      )}
      {foot && <p className="mt-1 whitespace-nowrap text-xs tabular-nums">{foot}</p>}
    </div>
  );
}

/** 최근 1시간 하트 급상승 Top 5. 백엔드가 이미 골라 보낸 목록을 그대로 그린다
 *  (프론트에서 전체 배열로 다시 계산하면 대표 클립 교체 같은 조건이 누락된다). */
function HeartMovers({ movers, collectedAt }:
  { movers: SingcupHeartMover[]; collectedAt: string | null }) {
  return (
    <section className="card !p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          {/* '실시간'이라고 하면 초 단위로 갱신되는 것처럼 오해된다 */}
          <h2 className="flex items-center gap-1.5 text-base font-extrabold">
            <Heart size={15} style={{ color: VERMILION }} /> 최근 1시간 하트 급상승
          </h2>
          <p className="mt-0.5 text-[12px] text-muted">
            대표 클립의 하트가 1시간 동안 가장 많이 증가한 스트리머입니다.
          </p>
        </div>
        <span className="text-[11px] text-muted/70 tabular-nums">
          싱드컵 집계 {fmtDateTime(collectedAt)}
        </span>
      </div>

      {movers.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">
          최근 1시간 동안 확인된 하트 증가가 없습니다.
        </p>
      ) : (
        // 데스크톱 5열 → 태블릿 3열 → 모바일 2열. 카드 내용이 잘리지 않게 최소 폭을 준다.
        <div className="mt-3 grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
          {movers.map((m) => (
            <article key={m.channelId}
              className={`nb-rainbow-border rounded-xl p-2.5 ${m.rank === 1 ? "nb-rainbow-top" : ""}`}
              style={{ background: "rgb(var(--color-bg-card-rgb))" }}>
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-extrabold tabular-nums"
                      style={{ color: m.rank === 1 ? GOLD : "rgb(var(--color-muted-rgb))" }}>
                  #{m.rank}
                </span>
                <span className="flex items-center gap-0.5 text-xs font-extrabold tabular-nums"
                      style={{ color: GREEN }} title="최근 1시간 하트 증가량">
                  ▲ {nf(m.heartDelta1h)}
                </span>
              </div>

              <a href={`https://chzzk.naver.com/clips/${m.clipUid}`} target="_blank"
                 rel="noopener noreferrer"
                 className="mt-1.5 block aspect-video w-full overflow-hidden rounded bg-bg-hover">
                {m.clipThumbnailUrl && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={m.clipThumbnailUrl} alt="" loading="lazy" onError={hideBrokenImage}
                       className="h-full w-full object-cover" />
                )}
              </a>

              <div className="mt-1.5 flex items-center gap-1.5">
                <span className="h-5 w-5 shrink-0 overflow-hidden rounded-full bg-bg-hover">
                  {m.channelImageUrl && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={m.channelImageUrl} alt="" width={20} height={20} loading="lazy"
                         onError={hideBrokenImage} className="h-full w-full object-cover" />
                  )}
                </span>
                <a href={`https://chzzk.naver.com/${m.channelId}`} target="_blank"
                   rel="noopener noreferrer" title={m.channelName}
                   className="truncate text-[13px] font-bold text-fg hover:text-accent">
                  {m.channelName || "-"}
                </a>
                {m.live && (
                  <span className="shrink-0 rounded px-1 text-[9px] font-bold"
                        style={{ background: "rgba(255,77,77,0.15)", color: "#FF4D4D" }}>
                    LIVE
                  </span>
                )}
              </div>
              <p className="mt-0.5 truncate text-[11px] text-muted" title={m.clipTitle}>
                {m.clipTitle}
              </p>
              <p className="mt-1 flex items-center gap-1 text-[11px] tabular-nums text-muted">
                <Heart size={10} /> 현재 {nf(m.heartCount)}
                {m.heartDelta24h != null && m.heartDelta24h > 0 && (
                  <span className="ml-auto text-muted/70">24h +{nf(m.heartDelta24h)}</span>
                )}
              </p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

// 랭킹 한 줄 — 한 줄 배치는 lg(1024px)부터. sm(640px)부터 nowrap을 걸면
// 태블릿(~820px)에서 오른쪽 점수·변동·버튼 묶음이 카드 밖으로 45px가량 삐져나간다.
function Row({ s, index }: { s: SingcupStreamer; index: number }) {
  const medal = index >= 0 && index < 3 ? MEDAL[index] : null;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border p-3 lg:flex-nowrap"
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
         className="hidden h-12 w-20 shrink-0 overflow-hidden rounded bg-bg-hover lg:block">
        {s.clipThumbnailUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={s.clipThumbnailUrl} alt="" loading="lazy" onError={hideBrokenImage}
               className="h-full w-full object-cover" />
        )}
      </a>

      <span className="min-w-0 flex-1 basis-full lg:basis-auto">
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

      {/* 하트 변화량 — 점수와 버튼 사이의 독립된 칸으로 읽히도록 좌우에 구분선을 두고
          여백을 넉넉히 준다. 구분선이 없으면 버튼 쪽에 붙어 보인다. */}
      <span className="flex shrink-0 items-center justify-center gap-5 border-border
                       px-4 sm:min-w-[172px] sm:border-x">
        <span className="text-center" title="1시간 전과 비교한 하트 증감입니다. '-'는 변화가 없거나 비교할 기록이 아직 없다는 뜻입니다.">
          <span className="block whitespace-nowrap text-xs text-muted">1시간</span>
          <span className="mt-0.5 block whitespace-nowrap text-[15px]">
            <Delta value={s.heartDelta} />
          </span>
        </span>
        <span className="text-center" title="24시간 전 대비 하트 증가율입니다. 'NEW'는 24시간 전 기록이 없거나 그때 하트가 0이라 증가율을 낼 수 없다는 뜻입니다.">
          <span className="block whitespace-nowrap text-xs text-muted">24시간</span>
          <span className="mt-0.5 block whitespace-nowrap text-[15px]">
            {s.heartChangeRate24h === null
              ? <span className="font-bold" style={{ color: GOLD }}>NEW</span>
              : <Delta value={s.heartChangeRate24h} suffix="%" />}
          </span>
        </span>
      </span>

      <span className="flex shrink-0 items-center gap-1.5 lg:pl-1">
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

const PAGE_SIZE = 100;

export default function Singcup() {
  const [data, setData] = useState<SingcupMain | null>(null);
  const [loading, setLoading] = useState(true);
  const [sort, setSort] = useState<SortKey>("score");
  const [dir, setDir] = useState<SortDir>("desc");
  const [query, setQuery] = useState("");
  const [boardOpen, setBoardOpen] = useState(false);

  // ?view=board / ?sort= 로 상태를 유지한다(새로고침·뒤로가기·공유에도 그대로)
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    setBoardOpen(p.get("view") === "board");
    const s = p.get("sort");
    if (isSort(s)) setSort(s);
    if (p.get("dir") === "asc") setDir("asc");
  }, []);

  const pickSort = useCallback((k: SortKey, d: SortDir) => {
    setSort(k); setDir(d);
    const url = new URL(window.location.href);
    // 기본값은 URL에 남기지 않는다 — 공유 링크가 불필요하게 길어진다
    if (k === "score") url.searchParams.delete("sort");
    else url.searchParams.set("sort", k);
    if (d === "desc") url.searchParams.delete("dir");
    else url.searchParams.set("dir", d);
    window.history.replaceState(null, "", url.toString());
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
    // 참가자 전원을 받는다 — 검색이 이 응답 안에서만 이뤄지므로, 여기서 자르면
    // 잘린 뒤쪽 스트리머는 닉네임을 정확히 쳐도 "없습니다"가 뜬다.
    const load = () => api.singcup.main(3000)
      .then((d) => { if (alive) setData(d); })
      .catch(() => { /* 실패해도 마지막 정상 데이터를 유지한다 */ })
      .finally(() => { if (alive) setLoading(false); });
    load();
    const t = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  // 검색은 이미 받아온 참가자 목록 안에서만 한다 — 싱드컵에 등록된 사람만 찾아진다.
  // 대상은 치지직 닉네임(channelName) 하나뿐이다. 클립 제목까지 넣으면 노래 제목이
  // 걸려서 "이 사람을 찾는" 검색이 아니게 된다.
  const matched = useMemo(() => {
    const all = data?.streamers ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return all;
    return all.filter((s) => s.channelName.toLowerCase().includes(q));
  }, [data, query]);

  const rows = useMemo(() => {
    const list = [...matched];
    if (sort === "heart") list.sort((a, b) => b.heartCount - a.heartCount);
    else if (sort === "view") list.sort((a, b) => b.viewCount - a.viewCount);
    else if (sort === "follower") list.sort((a, b) => b.followerCount - a.followerCount);
    else if (sort === "heart24h") {
      list.sort((a, b) => (b.heartChangeRate24h ?? -1e9) - (a.heartChangeRate24h ?? -1e9));
    }
    else if (sort === "rankmove") {
      // '얼마나 움직였나'가 기준이므로 상승·하락을 절대값으로 함께 본다.
      // 비교 기록이 없는 항목(NEW)은 변동폭 0으로 치지 않고 정상 비교 항목 뒤로 보낸다.
      const mv = (s: SingcupStreamer) => (s.rankDelta == null ? -1 : Math.abs(s.rankDelta));
      list.sort((a, b) =>
        mv(b) - mv(a)
        || Math.abs(b.scoreDelta ?? 0) - Math.abs(a.scoreDelta ?? 0)
        || b.score - a.score
        || a.channelId.localeCompare(b.channelId));
    }
    // score는 백엔드가 매긴 순서 그대로(동점 타이브레이커까지 반영돼 있다)
    // 위 정렬은 전부 내림차순이므로, 오름차순은 뒤집기만 하면 된다.
    return dir === "asc" ? list.reverse() : list;
  }, [matched, sort, dir]);

  // 참가자를 전원 받아오므로 한 번에 다 그리면 첫 화면이 무거워진다. 목록은 나눠서
  // 보여주되, 검색·정렬은 항상 전체를 대상으로 한다(잘라놓고 찾으면 안 나온다).
  const [visible, setVisible] = useState(PAGE_SIZE);
  useEffect(() => { setVisible(PAGE_SIZE); }, [query, sort, dir]);
  const shown = rows.slice(0, visible);

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
              {/* 페이지 상단의 '라이브 집계'와 다른 수집기다 — 이름으로 구분한다 */}
              <span title="#싱드컵 클립의 조회수·하트를 마지막으로 갱신한 시각입니다. 상단의 '라이브 집계'는 별개 수집기입니다.">
                싱드컵 집계{" "}
                <span className="tabular-nums">
                  {fmtDateTime(data?.collector.lastSuccessAt ?? null)}
                </span>
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

      {/* 최근 1시간 하트 급상승 — KPI 위에 둔다(제목/설명 바로 다음) */}
      {data && (
        <HeartMovers movers={data.topHeartMovers1h}
                     collectedAt={data.collector.lastSuccessAt} />
      )}

      {/* 요약 */}
      <div className="grid grid-cols-3 gap-3">
        <Tile label="태그 클립" value={nf(data?.summary.taggedClipCount ?? 0)} unit="개"
              delta={data?.summary.taggedClipDelta ?? null}
              windowMin={data?.summary.deltaWindowMinutes}
              baseAt={data?.summary.deltaBaseAt} />
        <Tile label="참가 스트리머" value={nf(data?.summary.streamerCount ?? 0)} unit="명" accent
              delta={data?.summary.streamerDelta ?? null}
              windowMin={data?.summary.deltaWindowMinutes}
              baseAt={data?.summary.deltaBaseAt} />
        {/* 라이브는 늘고 줄기를 반복하므로 '증가분' 개념이 맞지 않는다.
            대신 '언제 확인한 값인지'를 보여준다 — 화면은 60초마다 새로 받지만 이 값은
            전체 라이브 스캔 주기(기본 10분)에 묶여 있어 그 사이에는 바뀌지 않는다. */}
        <Tile label="현재 라이브" value={nf(data?.summary.liveCount ?? 0)} unit="명"
              foot={<LiveFreshness live={data?.live} />} />
      </div>

      {/* 컨트롤 — 좌: 참가자 검색 / 우: 정렬 + 오름·내림 전환 */}
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3">
        {/* 검색: 이미 받아온 참가자 목록 안에서만 찾는다(싱드컵 등록자 전용) */}
        <div className="relative w-full sm:w-[280px]">
          <Search size={15}
                  className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
          <input
            value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="치지직 닉네임 검색"
            aria-label="싱드컵 참가 스트리머 닉네임 검색"
            className="w-full rounded-lg border border-border bg-bg py-2 pl-9 pr-8 text-sm
                       text-fg placeholder-muted focus:border-accent focus:outline-none" />
          {query && (
            <button onClick={() => setQuery("")} aria-label="검색어 지우기"
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1
                               text-muted transition-colors hover:text-fg">
              <X size={14} />
            </button>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 sm:ml-auto sm:justify-end">
          {SORTS.map((s) => {
            const on = sort === s.k;
            return (
              <button key={s.k} onClick={() => pickSort(s.k, dir)}
                className="rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors"
                style={{ background: on ? `${GOLD}1a` : "transparent",
                         borderColor: on ? `${GOLD}59` : "rgb(var(--color-border-rgb))",
                         color: on ? GOLD : "rgb(var(--color-muted-rgb))" }}>
                {s.label}
              </button>
            );
          })}
          {/* 모든 정렬은 내림차순 기준이라 오름차순은 순서를 뒤집는다 */}
          <button onClick={() => pickSort(sort, dir === "desc" ? "asc" : "desc")}
            title={dir === "desc" ? "내림차순 (높은 순) — 누르면 오름차순"
                                  : "오름차순 (낮은 순) — 누르면 내림차순"}
            aria-label={dir === "desc" ? "내림차순 정렬 중" : "오름차순 정렬 중"}
            className="flex items-center gap-1 rounded-lg border px-3 py-1.5 text-xs
                       font-medium transition-colors"
            style={{ background: `${GOLD}1a`, borderColor: `${GOLD}59`, color: GOLD }}>
            {dir === "desc" ? <ArrowDown size={13} /> : <ArrowUp size={13} />}
            {dir === "desc" ? "높은 순" : "낮은 순"}
          </button>
        </div>
      </div>

      {query && (
        <p className="text-xs text-muted">
          <b className="text-fg">{nf(matched.length)}</b>명 검색됨
          <span className="mx-1.5 text-border">·</span>
          <button onClick={() => setQuery("")} className="underline hover:text-fg">
            전체 보기
          </button>
        </p>
      )}

      {/* 표기 안내 — '1시간 -' / '24시간 NEW'가 무슨 뜻인지 화면에서 바로 알 수 있게 */}
      <p className="text-[11px] leading-relaxed text-muted/80">
        <b className="text-muted">1시간</b>은 1시간 전과 비교한 하트 증감,{" "}
        <b className="text-muted">24시간</b>은 하루 전 대비 하트 증가율입니다.{" "}
        <span className="text-muted">-</span> 는 변화가 없거나 비교할 기록이 아직 없다는 뜻이고,{" "}
        <b style={{ color: GOLD }}>NEW</b> 는 24시간 전 기록이 없어(또는 그때 하트가 0이라)
        증가율을 낼 수 없다는 뜻입니다. 수집을 막 시작한 동안에는 대부분 이렇게 표시됩니다.
      </p>

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
          {query ? (
            <>
              <p className="font-medium text-fg">
                닉네임에 &lsquo;{query}&rsquo; 가 들어가는 참가 스트리머가 없습니다.
              </p>
              <p className="mt-1 text-sm text-muted">
                싱드컵에 등록된 참가자의 <b className="text-muted">치지직 닉네임</b>만 검색됩니다.
              </p>
              <button onClick={() => setQuery("")}
                      className="btn-secondary mt-4 text-sm">검색 초기화</button>
            </>
          ) : (
            <>
              <p className="font-medium text-fg">아직 확인된 #싱드컵 태그 클립이 없습니다.</p>
              {ev?.status === "UPCOMING" && (
                <p className="mt-1 text-sm text-muted">
                  이벤트 시작 예정: <span className="tabular-nums">{fmtDateTime(ev.startAt)}</span>
                </p>
              )}
            </>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {/* 메달은 '점수 내림차순 + 검색 없음'일 때만 — 그 외에는 목록 순서가
              실제 1~3위와 다르므로 강조하면 오해를 준다 */}
          {shown.map((s, i) => (
            <Row key={s.channelId} s={s}
                 index={sort === "score" && dir === "desc" && !query ? i : -1} />
          ))}
          {rows.length > shown.length && (
            <button onClick={() => setVisible((v) => v + PAGE_SIZE)}
                    className="btn-secondary w-full py-3 text-sm">
              더 보기 <span className="tabular-nums text-muted">
                ({shown.length}/{rows.length})
              </span>
            </button>
          )}
        </div>
      )}

      <Disclaimer />

      {boardOpen && <BoardDrawer onClose={() => setView(false)} />}
    </div>
  );
}
