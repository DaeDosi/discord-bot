"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Trophy, ExternalLink, Play, Eye, Heart, Radio, X,
  Search, ArrowDown, ArrowUp, RefreshCw,
} from "lucide-react";
import { useSingcupMain } from "@/lib/useSingcupMain";
import type {
  SingcupHeartMover, SingcupMain, SingcupStreamer,
} from "@/lib/types";
import {
  Delta, Disclaimer, EventBadge, GOLD, GREEN, MEDAL, RankDelta, SCORE_GRAD, ScoreFormula,
  ScoreText, StaleBadge, StatusChip, VERMILION, fmtDateTime, fmtRange, hideBrokenImage, nf,
} from "./singcupShared";

// 싱드컵 메인 = 랭킹 화면.
// 썸네일 카드로 둘러보는 화면은 '싱드컵 라이브'(/stats/singcup/live)로 나가 있다.
// '자유게시판 홍보글' 드로어(?view=board)는 제거됐다 — 백엔드 API와 계약은
// docs/작업정리_2026-08-02_싱드컵_자유게시판_홍보글_API.md 에 보존돼 있다.

type SortKey = "score" | "heart" | "heart1h" | "view";
type SortDir = "desc" | "asc";
const SORTS: { k: SortKey; label: string }[] = [
  { k: "score",    label: "예상 인기점수" },
  { k: "heart",    label: "하트 많은 순" },
  // 기준 스냅샷 대비 '증가량'. 실제 비교 간격은 회차마다 달라서 카드 쪽에 분 단위로
  // 같이 적는다(제목에 '1시간'을 박아두면 37분 비교를 1시간이라고 말하게 된다).
  { k: "heart1h",  label: "하트 급상승" },
  { k: "view",     label: "조회수 많은 순" },
];
const isSort = (v: string | null): v is SortKey =>
  !!v && SORTS.some((s) => s.k === v);

// 폐지된 정렬(하트 24시간 급상승 / 팔로워 많은 순)이 담긴 기존 공유 링크·뒤로가기
// 이력이 남아 있다. `isSort`가 false를 주는 값은 전부 기본 정렬로 되돌리고 URL에서도
// 지운다 — 그냥 무시하면 주소창과 화면 상태가 서로 어긋난다.

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

// ── 급상승 카드의 시각 표시용 순수 함수 ───────────────────────────────────────
// 이 값들은 전부 백엔드에서 온 문자열이라 프론트가 신뢰할 근거가 없다. 한 곳이라도
// 파싱에 실패하면 화면에 "Invalid Date"나 "NaN분"이 그대로 찍히므로, 파싱과 표시를
// 작은 함수로 떼어 각 단계에서 null로 떨어지게 한다.

/** 파싱 가능한 시각이면 epoch ms, 아니면 null. `new Date("x")`는 던지지 않고
 *  NaN을 주기 때문에 호출부마다 검사가 흩어지는 걸 막는다. */
function parseAt(v?: string | null): number | null {
  if (!v) return null;
  const t = new Date(v).getTime();
  return Number.isFinite(t) ? t : null;
}

/** 싱드컵 이벤트 시각은 전부 KST 기준이다. 타임존을 지정하지 않으면 해외에서 보는
 *  브라우저가 자기 로컬 시각으로 바꿔 그려서 "02:06 기준"이 다른 시각이 된다. */
const KST = "Asia/Seoul";
function hhmmKst(ms: number): string {
  return new Date(ms).toLocaleTimeString("ko-KR",
    { hour: "2-digit", minute: "2-digit", timeZone: KST });
}
/** 날짜 경계를 넘긴 값은 시:분만 보면 오늘 것으로 오해된다 — 월·일을 같이 적는다. */
function mdhmKst(ms: number): string {
  return new Date(ms).toLocaleString("ko-KR",
    { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: KST });
}

/** 비교 간격(분). 두 값이 모두 유효하고 computedAt이 뒤설 때만 양수를 준다.
 *  시계 역전(computedAt < baseAt)이나 0분은 표시할 값이 아니므로 null이다. */
function gapMinutes(baseAt?: string | null, computedAt?: string | null): number | null {
  const b = parseAt(baseAt), c = parseAt(computedAt);
  if (b == null || c == null) return null;
  const m = Math.round((c - b) / 60000);
  return m > 0 ? m : null;
}

/** 실제 비교 간격이 55~65분일 때만 '약 1시간'이라고 부를 수 있다.
 *  실측(2026-08-02 02:43 KST): baseAt 02:06:23 / computedAt 02:43:44 → 37분 21초인데
 *  화면은 "최근 1시간"이라고 적고 있었다. 기준 버킷은 `find_reference_baseline`이
 *  거리 기준으로 고르므로 회차마다 달라진다 — 제목에 '1시간'을 고정으로 박아 두면
 *  주기적으로 거짓말을 하게 된다. */
const isAboutAnHour = (gap: number | null) => gap != null && gap >= 55 && gap <= 65;

/** "02:06 기준 · 02:43 계산 · 비교 37분" — 실제 비교 간격을 숨기지 않는다.
 *  유효한 부분만 골라 그린다(기준만 유효하면 기준만 나온다). */
function MoversMeta({ baseAt, computedAt }: { baseAt?: string | null;
                                              computedAt?: string | null }) {
  const b = parseAt(baseAt);
  if (b == null) return null;
  const c = parseAt(computedAt);
  const gap = gapMinutes(baseAt, computedAt);
  return (
    <span className="tabular-nums">
      {hhmmKst(b)} 기준
      {c != null && <> · {hhmmKst(c)} 계산</>}
      {gap != null && <> · 비교 {gap}분</>}
    </span>
  );
}

function HeartMovers({ movers, baseAt, computedAt, stale, collector }: {
  movers: SingcupHeartMover[];
  baseAt?: string | null;
  computedAt?: string | null;
  stale?: boolean;
  collector?: SingcupMain["collector"];
}) {
  // 두 stale은 다른 것이다. `stale`(topHeartMovers1hStale)은 '이번 구간에 새 목록을
  // 계산하지 못해 직전 정상 집계를 재사용했다'는 뜻뿐이다 — 기준선 부재, 대표 클립
  // 교체, metrics recovering 제외, 양수 후보 없음 등 여러 사유가 전부 이 하나로
  // 합쳐져 오므로 **원인을 지목할 수 없다.** 화면 문구에서 원인을 열거하지 말 것
  // (일부만 적으면 그 자체가 틀린 진단이 된다).
  // `collector.stale`은 수집기 전체가 마지막 성공 이후 오래됐다는 뜻이다.
  // 하나로 합치면 어느 쪽이 문제인지 알 수 없다.
  const collectorStale = !!collector?.stale;
  const lastOk = parseAt(collector?.lastSuccessAt);
  const aboutAnHour = isAboutAnHour(gapMinutes(baseAt, computedAt));
  return (
    <section className="card !p-4">
      <div>
        {/* '실시간'이라고 하면 초 단위로 갱신되는 것처럼 오해된다.
            '1시간'도 마찬가지로 실제 비교 간격이 그만큼일 때만 쓴다. */}
        <h2 className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-base font-extrabold">
          <Heart size={15} style={{ color: VERMILION }} />
          {aboutAnHour ? "최근 약 1시간 하트 급상승" : "기준 시각 대비 하트 급상승"}
          {stale && (
            <span className="rounded px-1.5 py-0.5 text-[10px] font-bold"
                  style={{ background: `${GOLD}26`, color: GOLD }}
                  title="현재 구간에서 새 급상승 목록을 계산하지 못해 직전 정상 집계를 보여주고 있습니다. 다음 정상 집계가 완료되면 자동으로 갱신됩니다.">
              이전 집계
            </span>
          )}
          {collectorStale && (
            <span className="rounded px-1.5 py-0.5 text-[10px] font-bold"
                  style={{ background: "rgba(245,158,11,0.15)", color: "#F59E0B" }}
                  title="수집기의 마지막 성공 이후 시간이 오래 지났습니다.">
              수집 지연
            </span>
          )}
        </h2>
        {/* 집계 시각은 페이지 우측 상단에 하나만 둔다 — 같은 값을 두 번 보이면 잡음이다 */}
        <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[12px] text-muted">
          <span>대표 클립의 하트가 가장 많이 증가한 스트리머입니다.</span>
          <MoversMeta baseAt={baseAt} computedAt={computedAt} />
          {collectorStale && lastOk != null && (
            <span className="tabular-nums" style={{ color: "#F59E0B" }}>
              · 마지막 정상 수집 {mdhmKst(lastOk)}
            </span>
          )}
        </p>
      </div>

      {movers.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">
          {parseAt(baseAt) != null
            ? "이 기준 시각 이후 확인된 하트 증가가 없습니다."
            : "비교할 수 있는 기준 시각이 아직 없습니다."}
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
                      style={{ color: GREEN }} title="기준 시각 이후 하트 증가량">
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
      {/* 24시간 증가율 칸을 걷어내면서 최소 폭도 같이 줄인다 — 그대로 두면 한 칸만
          남은 자리에 172px 공백이 생긴다. */}
      <span className="flex shrink-0 items-center justify-center border-border
                       px-4 sm:min-w-[92px] sm:border-x">
        <span className="text-center" title="기준 스냅샷과 비교한 하트 증감입니다. '-'는 변화가 없거나 비교할 기록이 아직 없다는 뜻입니다.">
          <span className="block whitespace-nowrap text-xs text-muted">하트 증감</span>
          <span className="mt-0.5 block whitespace-nowrap text-[15px]">
            <Delta value={s.heartDelta} />
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

const PAGE_SIZE = 50;
// 한 번에 보여줄 페이지 번호 개수. 참가자가 수백 명이면 페이지가 10개를 넘으므로
// 전부 늘어놓지 않고 현재 페이지 주변만 보여준다(모바일에서 줄이 넘치지 않게).
const PAGE_WINDOW = 5;

/** 페이지 번호 이동 — 좌우 끝에 처음/마지막으로 가는 버튼을 둔다. */
function Pager({ page, total, onChange, rangeFrom, rangeTo, totalRows }:
  { page: number; total: number; onChange: (p: number) => void;
    rangeFrom: number; rangeTo: number; totalRows: number }) {
  if (total <= 1) return null;
  const half = Math.floor(PAGE_WINDOW / 2);
  let start = Math.max(1, page - half);
  const end = Math.min(total, start + PAGE_WINDOW - 1);
  start = Math.max(1, end - PAGE_WINDOW + 1);   // 끝에서도 창 크기를 유지한다
  const nums = Array.from({ length: end - start + 1 }, (_, i) => start + i);

  const btn = "flex h-9 min-w-9 items-center justify-center rounded-lg border px-2.5" +
              " text-sm tabular-nums transition-colors disabled:opacity-35" +
              " disabled:cursor-not-allowed";
  const plain = { borderColor: "rgb(var(--color-border-rgb))",
                  color: "rgb(var(--color-muted-rgb))" };
  const active = { background: `${GOLD}1a`, borderColor: `${GOLD}59`, color: GOLD };

  return (
    <nav className="mt-4 flex flex-col items-center gap-2" aria-label="페이지 이동">
      <div className="flex flex-wrap items-center justify-center gap-1.5">
        <button onClick={() => onChange(1)} disabled={page === 1}
                className={btn} style={plain} aria-label="첫 페이지로" title="첫 페이지">
          «
        </button>
        <button onClick={() => onChange(page - 1)} disabled={page === 1}
                className={btn} style={plain} aria-label="이전 페이지" title="이전">
          ‹
        </button>
        {/* 창 밖의 앞쪽이 있으면 1페이지가 있다는 걸 알려준다 */}
        {start > 1 && <span className="px-1 text-sm text-muted/60">…</span>}
        {nums.map((n) => (
          <button key={n} onClick={() => onChange(n)}
                  className={`${btn} font-bold`} style={n === page ? active : plain}
                  aria-current={n === page ? "page" : undefined}>
            {n}
          </button>
        ))}
        {end < total && <span className="px-1 text-sm text-muted/60">…</span>}
        <button onClick={() => onChange(page + 1)} disabled={page === total}
                className={btn} style={plain} aria-label="다음 페이지" title="다음">
          ›
        </button>
        <button onClick={() => onChange(total)} disabled={page === total}
                className={btn} style={plain} aria-label="마지막 페이지로" title="마지막 페이지">
          »
        </button>
      </div>
      <p className="text-[11px] tabular-nums text-muted/70">
        {nf(rangeFrom)}–{nf(rangeTo)} / 전체 {nf(totalRows)}명 · {page}/{total} 페이지
      </p>
    </nav>
  );
}

export default function Singcup() {
  // 데이터 구독·폴링 정책은 라이브 페이지와 공유한다(lib/useSingcupMain).
  const { data, loading, refreshing, updatedAt, refresh } = useSingcupMain();
  const [sort, setSort] = useState<SortKey>("score");
  const [dir, setDir] = useState<SortDir>("desc");
  const [query, setQuery] = useState("");

  // ?sort= 로 상태를 유지한다(새로고침·뒤로가기·공유에도 그대로).
  // 폐지된 `?view=board`(자유게시판 홍보글 드로어)가 담긴 링크가 남아 있으므로,
  // 읽지는 않되 주소창에서는 한 번 지운다 — 열리지도 않는 상태가 URL에 남아 있으면
  // 공유했을 때 무엇이 열릴지 오해하게 된다. 정리는 이 effect에서 **1회**만 하고
  // replaceState라 뒤로가기 이력을 늘리지 않는다.
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const s = p.get("sort");
    const stale = p.has("view") || (!!s && !isSort(s));
    if (isSort(s)) setSort(s);
    if (stale) {
      const url = new URL(window.location.href);
      url.searchParams.delete("view");
      if (s && !isSort(s)) {
        // 폐지된 정렬이든 오타든 기본 정렬로 정규화한다(sort/dir 함께 제거).
        url.searchParams.delete("sort");
        url.searchParams.delete("dir");
      }
      window.history.replaceState(null, "", url.toString());
      if (s && !isSort(s)) return;   // 기본 정렬로 되돌렸으니 dir도 읽지 않는다
    }
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
    else if (sort === "heart1h") {
      // 기준 스냅샷 이후 하트를 가장 많이 받은 순. 비교 기록이 없으면(대표 클립 교체 등)
      // 0으로 치지 않고 정상 비교 항목 뒤로 보낸다 — 0과 '모름'은 다르다.
      const d = (s: SingcupStreamer) => (s.heartDelta == null ? -1 : s.heartDelta);
      list.sort((a, b) =>
        d(b) - d(a)
        || b.heartCount - a.heartCount
        || b.score - a.score
        || a.channelId.localeCompare(b.channelId));
    }
    // score는 백엔드가 매긴 순서 그대로(동점 타이브레이커까지 반영돼 있다)
    // 위 정렬은 전부 내림차순이므로, 오름차순은 뒤집기만 하면 된다.
    return dir === "asc" ? list.reverse() : list;
  }, [matched, sort, dir]);

  // 참가자를 전원 받아오므로 한 번에 다 그리면 첫 화면이 무거워진다. 목록은 나눠서
  // 보여주되, 검색·정렬은 항상 전체를 대상으로 한다(잘라놓고 찾으면 안 나온다).
  const [page, setPage] = useState(1);
  const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  // 검색·정렬이 바뀌면 목록이 통째로 달라지므로 1페이지로 되돌린다
  useEffect(() => { setPage(1); }, [query, sort, dir]);
  // 목록이 줄어 현재 페이지가 사라진 경우(검색 등) 마지막 페이지로 당긴다
  useEffect(() => { setPage((p) => Math.min(p, totalPages)); }, [totalPages]);
  const shown = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const listTop = useRef<HTMLDivElement>(null);
  const goPage = useCallback((p: number) => {
    setPage(p);
    // 페이지만 바뀌고 화면은 목록 끝에 머물면 뭐가 바뀐 건지 알 수 없다
    listTop.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, []);

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
        </div>

        {/* 우측 열: 집계 시각·수집 기간이 위, 버튼 묶음이 아래.
            페이지 헤더의 '라이브 집계'는 전체 라이브 스캔 시각이라 이 탭에서는
            혼동만 주므로 숨긴다(page.tsx). */}
        {/* shrink-0은 sm 이상에서만. 모바일(390px)에서는 이 열이 줄어들 수 있어야
            아래 버튼 줄의 flex-wrap이 실제로 동작한다 — shrink-0이면 열 너비가
            내용 폭(버튼 3개 = 456px)으로 고정돼 화면이 가로로 밀린다. */}
        <div className="flex flex-col items-start gap-2 sm:shrink-0 sm:items-end">
          <div className="text-[12px] leading-relaxed text-muted/80 sm:text-right">
            <p title="#싱드컵 클립의 조회수·하트를 마지막으로 갱신한 시각입니다.">
              싱드컵 집계{" "}
              <span className="tabular-nums">
                {fmtDateTime(data?.collector.lastSuccessAt ?? null)}
              </span>
            </p>
            {ev && (
              <p className="tabular-nums" title="이 기간에 올라온 클립만 집계합니다.">
                수집 기간 {fmtRange(ev.startAt, ev.endAt)}
              </p>
            )}
          </div>
          {/* 모바일에서도 잘리지 않게 wrap */}
          <div className="flex flex-wrap items-center gap-2">
            {/* 자동 갱신은 5분 주기다(응답이 참가자 전원이라 잦은 자동 호출은 그대로
                전송 비용이 된다). 그 사이에 바로 최신을 보고 싶은 사람을 위해 수동
                갱신을 둔다 — 이 버튼만 캐시를 건너뛴다. */}
            <button onClick={refresh} disabled={refreshing}
                    title={updatedAt
                      ? `${fmtDateTime(new Date(updatedAt).toISOString())}에 받은 데이터입니다. 5분마다 자동으로 갱신됩니다.`
                      : "데이터를 다시 받아옵니다."}
                    className="btn-secondary flex items-center gap-1.5 text-sm disabled:opacity-60">
              <RefreshCw size={15} className={refreshing ? "animate-spin" : ""} />
              새로고침
            </button>
            <Link href="/stats/singcup/live"
                  className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-bold text-[#1a1400]"
                  style={{ background: GOLD }}>
              <Radio size={15} /> 싱드컵 라이브
            </Link>
          </div>
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

      {/* 하트 급상승 — KPI 위에 둔다(제목/설명 바로 다음) */}
      {data && (
        <HeartMovers movers={data.topHeartMovers1h}
                     baseAt={data.topHeartMovers1hBaseAt}
                     computedAt={data.topHeartMovers1hComputedAt}
                     stale={data.topHeartMovers1hStale}
                     collector={data.collector} />
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
            대신 '언제 확인한 값인지'를 보여준다 — 화면은 5분마다 새로 받지만 이 값은
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

      {/* 표기 안내 — '하트 증감 -'이 무슨 뜻인지 화면에서 바로 알 수 있게 */}
      <p className="text-[11px] leading-relaxed text-muted/80">
        <b className="text-muted">하트 증감</b>은 직전 기준 스냅샷과 비교한 하트 증가량입니다.
        비교 기준 시각과 실제 비교 간격은 위 <b className="text-muted">하트 급상승</b> 카드에
        적혀 있습니다.{" "}
        <span className="text-muted">-</span> 는 변화가 없거나 비교할 기록이 아직 없다는 뜻입니다.
        수집을 막 시작한 동안에는 대부분 이렇게 표시됩니다.
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
        <div className="space-y-2" ref={listTop}>
          {/* 메달은 '점수 내림차순 + 검색 없음'일 때만 — 그 외에는 목록 순서가
              실제 1~3위와 다르므로 강조하면 오해를 준다 */}
          {shown.map((s, i) => (
            <Row key={s.channelId} s={s}
                 // 메달은 '점수 내림차순 + 검색 없음'의 1페이지에서만.
                 // 2페이지 첫 줄에 금메달이 붙으면 그 사람이 1위처럼 보인다.
                 index={sort === "score" && dir === "desc" && !query && page === 1
                        ? i : -1} />
          ))}
          <Pager page={page} total={totalPages} onChange={goPage}
                 rangeFrom={(page - 1) * PAGE_SIZE + 1}
                 rangeTo={(page - 1) * PAGE_SIZE + shown.length}
                 totalRows={rows.length} />
        </div>
      )}

      <Disclaimer />

    </div>
  );
}
