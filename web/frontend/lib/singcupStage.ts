/* 싱드컵 화면의 **기본 단계(본선/예선) 선택** — 단계적 배포와 본선 미공개 상태를 견디는 규칙.
 *
 * Railway(백엔드)와 Vercel(프론트)은 같은 순간에 교체되지 않는다. 그래서 본선 순위 요청은
 * 네 가지로 끝날 수 있고, 각 경우에 방문자가 **빈 화면을 기본으로 보면 안 된다**:
 *
 *   · 구 백엔드: `?campaign=final`을 모르고 예선 3부문을 돌려준다 → `divisions.final` 없음
 *   · 신 백엔드·본선 미공개: `divisions.final.available === false`, entries 0
 *   · 요청 실패(404·5xx·네트워크): 응답 없음
 *   · 신 백엔드·본선 공개: entries 1개 이상
 *
 * 마지막 경우에만 본선이 기본이다. 나머지는 **예선(기존 공개본)을 기본**으로 두고 "본선 준비 중"을
 * 따로 적는다. 사용자가 탭을 직접 누르면 그 선택이 우선한다(`explicit`).
 *
 * 응답이 오기 전에는 `null`(= 로딩 골격)이다. 첫 렌더에서 한쪽을 미리 고르면 본선이 있을 때
 * 예선 → 본선으로 통째로 바뀌며 CLS가 나고, 없을 때는 빈 본선이 먼저 보인다.
 */
import type { PikuEntry, PikuRankingResponse } from "./types";

export type Stage = "final" | "qualifier";

export type FinalAvailability =
  | { state: "loading" }
  | { state: "published"; entries: PikuEntry[] }
  | { state: "unpublished" }        // 신 백엔드, 아직 공개본 없음
  | { state: "unsupported" }        // 구 백엔드(campaign을 모름) — 배포 중간 상태
  | { state: "error" };             // 요청 실패

export function classifyFinal(
  finalRank: PikuRankingResponse | null | undefined,
  failed: boolean,
  settled: boolean,
): FinalAvailability {
  if (failed) return { state: "error" };
  if (!settled) return { state: "loading" };
  if (!finalRank || typeof finalRank !== "object") return { state: "error" };
  const div = finalRank.divisions?.final;
  if (!div) return { state: "unsupported" };
  if (div.available && Array.isArray(div.entries) && div.entries.length > 0) {
    return { state: "published", entries: div.entries };
  }
  return { state: "unpublished" };
}

/** 기본 단계. 명시 선택이 있으면 그것, 없으면 본선 공개본이 있을 때만 본선. 로딩 중엔 null. */
export function pickStage(avail: FinalAvailability, explicit: Stage | null): Stage | null {
  if (explicit) return explicit;
  if (avail.state === "loading") return null;
  return avail.state === "published" ? "final" : "qualifier";
}

/** 본선 탭 옆 힌트 문구 — 상태를 색이 아니라 글로 전한다. */
export function finalHint(avail: FinalAvailability): string {
  switch (avail.state) {
    case "published": return "진행 중";
    case "unpublished": return "준비 중";
    case "unsupported": return "준비 중";
    case "error": return "불러오기 실패";
    default: return "확인 중";
  }
}
