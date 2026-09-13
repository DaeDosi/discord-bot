/* 싱드컵 **시즌 → 단계** 화면 규칙 (PUBLIC-UX-1).
 *
 * 정본은 서버(`singcup_piku_campaigns.SEASONS`·`CAMPAIGNS`)다. 이 모듈은 응답을 화면용으로
 * 해석만 한다 — 시즌 이름·단계 상태를 컴포넌트마다 다시 적지 않게 한 곳에 모은다.
 *
 * 상태는 **날짜로 추측하지 않는다.** `stageState`는 서버가 준 값이고, 대회가 끝나면 서버
 * 정본 한 줄을 바꾸는 것으로 화면 전체가 따라온다.
 *
 * 구 백엔드(배포 중간)는 `seasons`·`stageState`를 모른다. 그때만 아래 `LEGACY_SEASON`을
 * 쓴다 — 폴백 정의는 **이 파일 한 곳**뿐이다.
 */
import type { PikuCampaign, PikuSeason } from "./types";

export type StageKind = "final" | "qualifier";
export type StageState = "upcoming" | "in_progress" | "ended";

/** 구 백엔드용 폴백. 서버 정본과 같은 값이어야 한다(계약 테스트가 대조한다). */
export const LEGACY_SEASON: PikuSeason = {
  season: "2026-galaxy",
  label: "2026 싱드컵 갤럭시",
  campaigns: ["final", "qualifier"],
};
const LEGACY_STAGE: Record<string, { stage: StageKind; stageState: StageState }> = {
  final: { stage: "final", stageState: "in_progress" },
  qualifier: { stage: "qualifier", stageState: "ended" },
};

export const STAGE_LABEL: Record<StageKind, string> = { final: "본선", qualifier: "예선" };

const STATE_LABEL: Record<StageState, string> = {
  upcoming: "예정", in_progress: "진행 중", ended: "종료",
};

const isStageState = (v: unknown): v is StageState =>
  v === "upcoming" || v === "in_progress" || v === "ended";

/** 응답의 시즌 목록. 없거나 비었으면 폴백 한 개. */
export function resolveSeasons(seasons: PikuSeason[] | null | undefined): PikuSeason[] {
  const ok = (seasons ?? []).filter((s) =>
    s && typeof s.season === "string" && typeof s.label === "string"
    && Array.isArray(s.campaigns));
  return ok.length > 0 ? ok : [LEGACY_SEASON];
}

export interface StageInfo {
  stage: StageKind;
  campaign: string;
  state: StageState;
}

/** 시즌 안의 단계 목록(본선이 먼저). campaign 응답이 아직 없으면 폴백 상태를 쓴다. */
export function stagesOf(season: PikuSeason, campaigns: PikuCampaign[] | null): StageInfo[] {
  const out: StageInfo[] = [];
  for (const key of season.campaigns) {
    const c = campaigns?.find((x) => x.campaign === key);
    const legacy = LEGACY_STAGE[key];
    const stage = (c?.stage === "final" || c?.stage === "qualifier") ? c.stage : legacy?.stage;
    if (!stage) continue;                      // 화면이 모르는 단계 종류는 그리지 않는다
    const state = isStageState(c?.stageState) ? c.stageState
      : (legacy?.stageState ?? "in_progress");
    out.push({ stage, campaign: key, state });
  }
  return out;
}

/** 단계 탭 옆 짧은 상태 문구. 진행 중인 본선은 공개본 유무가 먼저다. */
export function stageHint(state: StageState,
                          finalAvailability?: "published" | "unpublished" | "unsupported"
                            | "error" | "loading"): string {
  if (state === "in_progress" && finalAvailability && finalAvailability !== "published") {
    if (finalAvailability === "error") return "불러오기 실패";
    if (finalAvailability === "loading") return "확인 중";
    return "준비 중";
  }
  return STATE_LABEL[state];
}

/** `9월 13일 오후 1:09`. 시간대 이름은 적지 않는다(한국 대회 페이지다). */
export function fmtUpdatedAt(unixSec: number): string {
  return new Date(unixSec * 1000).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul", month: "long", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
}

/** 방문자용 고지 — 비공식 순위라는 사실 **한 문장**. 본선·예선이 같이 쓴다. */
export const UNOFFICIAL_NOTICE =
  "공개 투표 데이터를 바탕으로 정리한 비공식 순위이며, 대회 공식 결과와 다를 수 있습니다.";

/** 갱신 방식 한 문장(PUBLIC-UX-1a). **서버가 준 실제 상태로만** 고른다.
 *
 *  수집 주기와 공개 반영을 구분한다 — 자동 수집은 draft까지만 만들고 공개는 검토 후다.
 *  그래서 `auto`여도 "매시간 순위가 바뀐다"고 쓰지 않는다. 필드가 없으면(구 백엔드·응답 실패)
 *  모르는 것을 약속하지 않도록 빈 문자열이다. */
export function collectionNotice(
  c: Pick<PikuCampaign, "collectionMode" | "collectionIntervalMinutes" | "publicUpdatePolicy">
    | null | undefined,
): string {
  if (!c) return "";
  const minutes = Number(c.collectionIntervalMinutes) || 0;
  const automatic = c.publicUpdatePolicy === "automatic";
  if (c.collectionMode === "auto" && minutes > 0) {
    const every = minutes % 60 === 0 ? `약 ${minutes / 60}시간마다` : `약 ${minutes}분마다`;
    return automatic
      ? `${every} 새 투표 데이터를 확인해 순위에 반영합니다.`
      : `${every} 새 투표 데이터를 확인하며, 검토 후 순위에 반영합니다.`;
  }
  if (c.collectionMode === "manual") {
    return "현재 순위는 운영자 확인 후 갱신됩니다.";
  }
  return "";
}

/** `마지막 갱신 9월 13일 오후 1:09`. 공개본이 없으면 빈 문자열. */
export function lastUpdatedText(c: Pick<PikuCampaign, "lastPublishedAt"> | null | undefined): string {
  return c?.lastPublishedAt ? `마지막 갱신 ${fmtUpdatedAt(c.lastPublishedAt)}` : "";
}
