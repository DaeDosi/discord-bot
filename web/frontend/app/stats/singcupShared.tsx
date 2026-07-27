"use client";
import { Trophy } from "lucide-react";
import type { SingcupStatus } from "@/lib/types";

// 싱드컵 메인/랭킹이 함께 쓰는 조각들. 두 화면에서 문구와 색이 갈리지 않게 한 곳에 둔다.

// 골드는 이벤트 아이덴티티(배지·버튼·메달)에만 쓰고, 점수 강조는 다홍(vermilion)→앰버
// 2톤으로 따로 둔다. 사이트 기본 포인트(그린→시안)와도, 골드 UI와도 확실히 구분된다.
export const GOLD = "#FACC15";
export const VERMILION = "#FF6B4A";
export const AMBER = "#FFC24D";
export const GREEN = "#00FFA3";
/** 비공식 예상 인기점수 강조용 2톤 그라데이션 */
export const SCORE_GRAD = `linear-gradient(135deg, ${VERMILION}, ${AMBER})`;
export const MEDAL = [GOLD, "#D1D5DB", "#D97706"] as const;

/** 점수 그라데이션을 입힌 인라인 텍스트 — 본문에서 '비공식 예상 인기점수'를 강조할 때 */
export function ScoreText({ children }: { children: React.ReactNode }) {
  return (
    <b style={{ background: SCORE_GRAD, WebkitBackgroundClip: "text",
                backgroundClip: "text", color: "transparent" }}>{children}</b>
  );
}

export const nf = (n: number) => n.toLocaleString("ko-KR");

export const fmtDateTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("ko-KR",
    { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "-";

export const fmtRange = (a: string, b: string) => {
  const f = (s: string) => new Date(s).toLocaleString("ko-KR",
    { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return `${f(a)} ~ ${f(b)}`;
};

export function EventBadge() {
  return (
    <span className="nb-event-badge rounded px-1.5 py-0.5 text-[11px] font-extrabold tracking-wide"
          style={{ background: GOLD, color: "#1a1400" }}>EVENT</span>
  );
}

export function StatusChip({ status }: { status: SingcupStatus }) {
  const label = status === "LIVE" ? "진행 중" : status === "ENDED" ? "종료" : "시작 전";
  const color = status === "LIVE" ? GREEN
    : status === "ENDED" ? "rgb(var(--color-muted-rgb))" : GOLD;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs font-semibold"
          style={{ color }}>
      <span className={`h-1.5 w-1.5 rounded-full ${status === "LIVE" ? "nb-live-dot" : ""}`}
            style={{ background: color }} />
      {label}
    </span>
  );
}

export function StaleBadge() {
  return (
    <span className="rounded-full border px-2.5 py-1 text-xs font-semibold"
          style={{ color: GOLD, borderColor: `${GOLD}66`, background: `${GOLD}14` }}
          title="마지막 정상 집계 이후 시간이 꽤 지났습니다">
      집계 지연
    </span>
  );
}

/** 비공식 통계임을 밝히는 고지. 메인·랭킹 두 화면에 모두 넣는다. */
export function Disclaimer() {
  return (
    <div className="rounded-xl border border-border p-4">
      <p className="flex items-center gap-1.5 text-sm font-bold text-fg">
        <Trophy size={14} style={{ color: GOLD }} /> 비공식 통계 안내
      </p>
      <p className="mt-2 text-[13px] leading-relaxed text-muted">
        본 페이지는 치지직 또는 네이버에서 <b className="text-fg">공식 운영하는 서비스가 아닙니다.</b>{" "}
        음악/노래 카테고리에서 <b className="text-fg">#싱드컵</b> 태그가 확인된 클립을 기준으로
        집계한 비공식 통계입니다. 남성 솔로·여성 솔로·그룹 파트 및 실제 네이버폼 제출 여부는
        공개 데이터만으로 구분할 수 없어 <b className="text-fg">파트를 나누지 않고 전체를 하나의
        그룹으로</b> 계산합니다.
      </p>
      <p className="mt-2 text-[13px] leading-relaxed text-muted">
        표시되는 <b className="text-fg">비공식 예상 인기점수</b>는 공개 조회수와 하트 수로 계산한
        참고값입니다. 공식 예선에서 쓰는 유효 시청자 수 및 실제 선발 점수와 차이가 있을 수 있습니다.
      </p>
    </div>
  );
}

/** 점수 산식 설명 — 랭킹 화면 상단에 둔다 */
export function ScoreFormula() {
  return (
    <p className="text-[11px] leading-relaxed text-muted/80">
      비공식 예상 인기점수 = (대표 클립 조회수 ÷ 전체 최고 조회수) × 70
      + (대표 클립 하트 ÷ 전체 최고 하트) × 30
    </p>
  );
}

/** 이미지 로드 실패 시 회색 배경만 남기고 깨진 아이콘을 숨긴다 */
export const hideBrokenImage = (e: React.SyntheticEvent<HTMLImageElement>) => {
  e.currentTarget.style.display = "none";
};

export function Delta({ value, suffix = "" }: { value: number | null; suffix?: string }) {
  if (value === null || value === 0) return <span className="text-muted">-</span>;
  const up = value > 0;
  return (
    <span className="tabular-nums font-semibold" style={{ color: up ? GREEN : "#EF4444" }}>
      {up ? "+" : ""}{nf(value)}{suffix}
    </span>
  );
}

export function RankDelta({ value, isNew }: { value: number | null; isNew: boolean }) {
  if (isNew) {
    return <span className="text-[11px] font-bold" style={{ color: GOLD }}>NEW</span>;
  }
  if (value === null || value === 0) return <span className="text-muted">-</span>;
  const up = value > 0;
  return (
    <span className="text-[11px] font-bold tabular-nums"
          style={{ color: up ? GREEN : "#EF4444" }}>
      {up ? "▲" : "▼"} {Math.abs(value)}
    </span>
  );
}
