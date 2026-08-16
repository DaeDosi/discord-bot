// 확정 랭킹 응답 → 화면 상태 분류 (UI-P 차단 조건 H).
//
// 핵심은 **finalizing과 error를 절대 섞지 않는 것**이다. 하나로 묶으면 진짜 장애가
// "최종 집계를 준비하고 있습니다"로 둔갑해, 사용자는 기다리면 된다고 믿고 우리는
// 장애를 못 본다.
import { test } from "node:test";
import assert from "node:assert/strict";

import { classifyFinalRanking } from "./singcupRankingLoader.ts";

const FINAL_BODY = { rankingFinal: true, rankingFinalizedAt: 1786000000, streamers: [{}] };
const FINALIZING_BODY = { status: "finalizing", frozen: true, detail: "준비 중" };

test("200 + rankingFinal + streamers → final", () => {
  const c = classifyFinalRanking(200, FINAL_BODY);
  assert.equal(c.kind, "final");
});

test("200 + frozen:false → live", () => {
  assert.equal(classifyFinalRanking(200, { frozen: false, reason: "freeze_disabled" }).kind,
    "live");
});

test("503 + 정확한 finalizing 본문 → finalizing", () => {
  assert.equal(classifyFinalRanking(503, FINALIZING_BODY).kind, "finalizing");
});

test("503이지만 본문 계약이 다르면 → error", () => {
  // 프록시·게이트웨이가 만든 503은 우리가 아는 상태가 아니다.
  for (const body of [
    undefined,                                   // JSON 파싱 실패
    null,
    "Service Unavailable",                       // HTML/텍스트
    {},                                          // 빈 객체
    { status: "finalizing" },                    // frozen 누락
    { frozen: true },                            // status 누락
    { status: "unavailable", frozen: true },     // 다른 status
    { status: "finalizing", frozen: false },
  ]) {
    assert.equal(classifyFinalRanking(503, body).kind, "error",
      `503 + ${JSON.stringify(body)} 가 finalizing으로 분류됐다`);
  }
});

test("다른 HTTP 오류는 전부 error", () => {
  for (const code of [400, 401, 403, 404, 429, 500, 502, 504]) {
    assert.equal(classifyFinalRanking(code, { detail: "x" }).kind, "error", `${code}`);
    // finalizing 모양의 본문이 와도 상태 코드가 503이 아니면 error다.
    assert.equal(classifyFinalRanking(code, FINALIZING_BODY).kind, "error", `${code}+body`);
  }
});

test("네트워크 실패·timeout → error", () => {
  assert.equal(classifyFinalRanking(null, undefined).kind, "error");
});

test("200인데 계약에 없는 모양 → error", () => {
  for (const body of [
    undefined,                                   // JSON 파싱 실패
    null,
    "ok",
    {},                                          // rankingFinal도 frozen도 없다
    { rankingFinal: true },                      // streamers 누락 → 빈 순위를 '최종'이라 말하게 된다
    { rankingFinal: true, streamers: "x" },
    { rankingFinal: false, streamers: [] },
  ]) {
    assert.equal(classifyFinalRanking(200, body).kind, "error",
      `200 + ${JSON.stringify(body)} 가 final로 분류됐다`);
  }
});

test("어떤 실패도 live로 분류되지 않는다", () => {
  // live는 서버가 frozen:false라고 **명시**할 때만이다 — 실패가 실시간 경로로
  // 새면 얼린 화면이 조용히 풀린다.
  const failures: [number | null, unknown][] = [
    [null, undefined], [500, {}], [503, {}], [404, {}], [200, {}],
    [503, FINALIZING_BODY], [200, FINAL_BODY],
  ];
  for (const [code, body] of failures) {
    const kind = classifyFinalRanking(code, body).kind;
    if (kind === "live") {
      assert.fail(`${code} + ${JSON.stringify(body)} 가 live로 분류됐다`);
    }
  }
});
