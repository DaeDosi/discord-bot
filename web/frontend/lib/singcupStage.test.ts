/* SINGCUP-FINAL-1 — 단계적 배포 4조합에서 기본 단계 선택(`lib/singcupStage`).
 *
 * Railway·Vercel은 같은 순간에 교체되지 않는다. 본선 응답의 네 가지 결말 각각에서
 * 방문자가 **빈 본선을 기본으로 보지 않는지**를 고정한다. */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { classifyFinal, finalHint, pickStage } from "./singcupStage.ts";
import type { PikuRankingResponse } from "./types.ts";

const entry = { rank: 1, channelId: "a", name: "x", thumbnailUrl: "", sourceRank: 1, teamMembers: "" };
const newBackendPublished: PikuRankingResponse = {
  sort: "primary", campaign: "final", sortOptions: [], autoCollectEnabled: false,
  divisions: { final: { division: "final", label: "파이널 본선", sort: "primary",
    entries: [entry], available: true, unmappedCount: 0, lastSuccessAt: 1 } },
};
const newBackendUnpublished: PikuRankingResponse = {
  ...newBackendPublished,
  divisions: { final: { division: "final", label: "파이널 본선", sort: "primary",
    entries: [], available: false, unmappedCount: 0, lastSuccessAt: 0 } },
};
/** 구 백엔드는 `?campaign=final`을 무시하고 예선 3부문을 돌려준다. */
const oldBackend: PikuRankingResponse = {
  sort: "primary", sortOptions: [], autoCollectEnabled: false,
  divisions: {
    female_solo: { division: "female_solo", label: "여성 솔로", sort: "primary", entries: [entry], available: true, unmappedCount: 0, lastSuccessAt: 1 },
    male_solo: { division: "male_solo", label: "남성 솔로", sort: "primary", entries: [entry], available: true, unmappedCount: 0, lastSuccessAt: 1 },
    groups: { division: "groups", label: "그룹", sort: "primary", entries: [entry], available: true, unmappedCount: 0, lastSuccessAt: 1 },
  },
};

test("신 프론트 + 신 백엔드 · 본선 공개 → 본선 기본", () => {
  const a = classifyFinal(newBackendPublished, false, true);
  assert.equal(a.state, "published");
  assert.equal(pickStage(a, null), "final");
  assert.equal(finalHint(a), "진행 중");
});

test("신 프론트 + 신 백엔드 · 본선 미공개 → 예선 기본 + 준비 중", () => {
  const a = classifyFinal(newBackendUnpublished, false, true);
  assert.equal(a.state, "unpublished");
  assert.equal(pickStage(a, null), "qualifier");
  assert.equal(finalHint(a), "준비 중");
});

test("신 프론트 + 구 백엔드(campaign 모름) → 예선 기본, 오류 아님", () => {
  const a = classifyFinal(oldBackend, false, true);
  assert.equal(a.state, "unsupported");
  assert.equal(pickStage(a, null), "qualifier");
  assert.equal(finalHint(a), "준비 중");
});

test("본선 요청 실패(404·5xx·네트워크) → 예선 기본, 예선 화면은 오류가 아니다", () => {
  const a = classifyFinal(null, true, true);
  assert.equal(a.state, "error");
  assert.equal(pickStage(a, null), "qualifier");
  assert.equal(finalHint(a), "불러오기 실패");
});

test("응답 전에는 어느 단계도 고르지 않는다(빈 본선·통째 교체·hydration 불일치 방지)", () => {
  const a = classifyFinal(null, false, false);
  assert.equal(a.state, "loading");
  assert.equal(pickStage(a, null), null);
});

test("사용자가 직접 고른 탭이 데이터보다 우선한다", () => {
  assert.equal(pickStage(classifyFinal(newBackendPublished, false, true), "qualifier"), "qualifier");
  assert.equal(pickStage(classifyFinal(newBackendUnpublished, false, true), "final"), "final");
});

test("본선 draft만 있는 상태는 공개 응답에 없다(available=false) → 미공개와 같다", () => {
  // 서버는 `status='active'`만 공개한다. draft(building)는 available=false·entries 0으로 온다.
  const a = classifyFinal(newBackendUnpublished, false, true);
  assert.equal(a.state, "unpublished");
});

test("화면이 이 규칙을 실제로 쓴다(인라인 기본값 금지)", () => {
  const src = readFileSync(new URL("../app/stats/SingcupOfficial.tsx", import.meta.url), "utf8");
  assert.ok(/pickStage\(finalAvail, explicitStage\)/.test(src), "pickStage를 쓰지 않는다");
  assert.ok(!/useState<Stage>\("final"\)/.test(src), "본선을 무조건 기본으로 둔다");
  assert.ok(/stage === null \?/.test(src), "응답 전 로딩 골격이 없다");
  assert.ok(/data-testid="final-pending"/.test(src), "예선 기본일 때 본선 준비 중 안내가 없다");
});

test("구 프론트 + 신 백엔드: 관리 status/publish의 campaign 기본값은 예선(legacy)이다", () => {
  const src = readFileSync(new URL("../../backend/routers/admin_router.py", import.meta.url), "utf8");
  assert.ok(/col\.status\(campaign or "qualifier"\)/.test(src));
  assert.ok(/or "qualifier"\)\}/.test(src), "publish 기본이 예선이 아니다");
  assert.ok(/col\.publish_preview\(campaign or "qualifier"\)/.test(src));
});
