// UI-P 요구 1·3·4·5·6의 구조 계약.
//
// 이 저장소의 프론트 테스트는 의존성 없이 `node --test lib/*.test.ts`로 돈다.
// 렌더링 대신 소스에 그 구조가 실제로 있는지를 본다 — 여기서 막으려는 것은
// "리팩터링하다 조용히 원복되는 것"이다. 동작 자체는 브라우저로 따로 확인했다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");
/** 주석을 뺀 코드만 본다 — "영문 월을 쓰지 않는다"를 설명하는 주석이 그 검사에
 *  걸리면, 이유를 적어 둘수록 테스트가 깨지는 이상한 상황이 된다. */
const code = (p: string) =>
  read(p).replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

// ── 요구 1: 제목 ────────────────────────────────────────────────────────────
test("요구1: 'NexBot 비공식 인기점수 랭킹'이 어디에도 남지 않는다", () => {
  for (const f of ["app/stats/Singcup.tsx", "app/stats/SingcupQualifiers.tsx",
                   "app/stats/singcup/live/page.tsx", "app/stats/page.tsx",
                   "app/layout.tsx", "app/sitemap.ts"]) {
    assert.ok(!read(f).includes("NexBot 비공식 인기점수 랭킹"), `${f}에 옛 제목이 있다`);
  }
});

test("요구1: 새 제목이 정확히 쓰인다", () => {
  const s = read("app/stats/Singcup.tsx");
  assert.ok(/\/>\s*비공식 인기점수 랭킹\s*\n?\s*<\/h2>/.test(s),
    "제목이 '비공식 인기점수 랭킹'이어야 한다");
});

test("요구1: 비공식 안내와 계산 공식은 그대로 남는다", () => {
  const s = read("app/stats/Singcup.tsx");
  assert.ok(s.includes("공식 심사 결과와 다를 수 있습니다."));
  assert.ok(s.includes("<ScoreFormula />"), "계산 공식 블록을 지우지 않는다");
  assert.ok(s.includes("비공식 예상 인기점수"), "서비스 주체·비공식 표현 유지");
});

// ── 요구 2: 동결 (프론트 쪽 계약) ───────────────────────────────────────────
test("요구2: 랭킹 화면은 확정본 훅을 쓴다", () => {
  const s = read("app/stats/Singcup.tsx");
  assert.ok(s.includes("useSingcupRanking"), "확정본 훅을 써야 한다");
  assert.ok(!/import \{ useSingcupMain \}/.test(s),
    "랭킹 화면이 /main 훅으로 되돌아갔다 — 참가자 화면까지 얼어붙는다");
});

test("요구2: 공식 예선 참가자 화면은 최신 지표(/main)를 계속 쓴다", () => {
  const s = read("app/stats/SingcupQualifiers.tsx");
  assert.ok(s.includes("useSingcupMain"), "참가자 화면까지 확정본으로 바꾸면 안 된다");
});

test("요구2: 확정본에서는 갱신 중으로 읽히는 표시를 끈다", () => {
  const s = read("app/stats/Singcup.tsx");
  // 새로고침 버튼은 **확정본에서만** 감춘다 — 눌러도 같은 값이면 오해만 준다.
  // 동결 비활성(기존 실시간 경로)에서는 예전처럼 있어야 하므로, 버튼이 사라진 게
  // 아니라 `!final` 안에 들어 있는지를 본다.
  const i = s.indexOf("<RefreshCw");
  assert.ok(i > -1, "실시간 경로의 새로고침 버튼까지 사라졌다(기존 기능 회귀)");
  assert.ok(s.slice(Math.max(0, i - 700), i).includes("{!final && ("),
    "확정본에서도 새로고침 버튼이 뜬다");
  // stale/수집 지연 배지는 final일 때 꺼져야 한다.
  assert.ok(s.includes("!final && stale"), "'이전 집계' 배지가 확정본에서도 뜬다");
  assert.ok(s.includes("const collectorStale = !final &&"),
    "'수집 지연' 배지가 확정본에서도 뜬다");
  assert.ok(s.includes("최종 집계 기준"), "최종 집계 기준 시각 문구가 있어야 한다");
});

// ── 요구 3: 하트·LIVE ──────────────────────────────────────────────────────
test("요구3: 하트는 빨간색이고 LIVE는 빨간색 + pulse다", () => {
  const css = read("app/globals.css");
  assert.ok(/\.nb-heart-icon\s*\{\s*color:\s*#F43F5E/.test(css), "하트가 빨강이 아니다");
  assert.ok(/\.nb-live-badge\s*\{[\s\S]*color:\s*#FF4D5E/.test(css), "LIVE가 빨강이 아니다");
  assert.ok(css.includes("animation: nb-live-pulse"), "LIVE pulse가 없다");
  assert.ok(css.includes("@keyframes nb-live-pulse"));
});

test("요구3: pulse는 점멸이 아니고 글자가 항상 읽힌다", () => {
  const css = read("app/globals.css");
  const kf = css.slice(css.indexOf("@keyframes nb-live-pulse"));
  const opacities = [...kf.slice(0, 300).matchAll(/opacity:\s*([\d.]+)/g)].map((m) => Number(m[1]));
  assert.ok(opacities.length >= 2);
  assert.ok(Math.min(...opacities) >= 0.6,
    "opacity가 너무 낮게 떨어지면 글자를 읽을 수 없다(점멸 금지)");
});

test("요구3: reduced-motion에서 애니메이션이 완전히 꺼진다", () => {
  const css = read("app/globals.css");
  const idx = css.lastIndexOf("@media (prefers-reduced-motion: reduce)");
  const block = css.slice(idx, idx + 300);
  assert.ok(block.includes(".nb-live-badge"));
  assert.ok(/animation:\s*none/.test(block));
});

test("요구3: 적용 범위는 공식 예선 참가자 카드뿐이다", () => {
  // 다른 화면의 LIVE 표현까지 물들이면 전역 변경이 된다.
  for (const f of ["app/stats/StatsNav.tsx", "app/stats/singcup/live/page.tsx",
                   "app/stats/page.tsx"]) {
    assert.ok(!read(f).includes("nb-live-badge"), `${f}가 이 클래스를 쓰면 범위가 넓어진다`);
  }
  assert.ok(read("app/stats/SingcupQualifiers.tsx").includes("nb-live-badge"));
});

// ── 요구 4: 통계 안내 페이지 ────────────────────────────────────────────────
test("요구4: 안내 페이지가 존재하고 필수 항목을 담는다", () => {
  assert.ok(existsSync(join(ROOT, "app/stats/guide/page.tsx")));
  const s = read("app/stats/guide/page.tsx");
  for (const must of ["데이터를 어떻게 모으나", "주요 지표의 뜻", "전체 통계 화면 읽는 법",
                      "스트리머 상세 통계 읽는 법", "활동 잔디", "뷰어십", "카테고리",
                      "약 10분 간격", "제휴 관계가 없습니다"]) {
    assert.ok(s.includes(must), `안내 페이지에 '${must}'가 없다`);
  }
  assert.ok(s.includes('href="/terms"') && s.includes('href="/privacy"'), "법적 링크 누락");
  assert.ok(s.includes("metadata"), "metadata가 없다");
  assert.ok(s.includes("canonical"), "canonical 정책이 없다");
});

test("요구4: 긴 설명 블록이 두 화면에서 제거됐다", () => {
  const stats = read("app/stats/page.tsx");
  const streamer = read("app/stats/streamer/[channelId]/layout.tsx");
  for (const s of [stats, streamer]) {
    assert.ok(!s.includes("CollapsibleAbout"), "긴 설명 블록이 남아 있다");
  }
  assert.ok(!stats.includes("NEXBOT 치지직 방송 통계 서비스 소개"));
  assert.ok(!streamer.includes("치지직 방송 통계 분석"),
    "스트리머 이름이 섞인 동적 제목 블록이 남아 있다");
});

test("요구4: 두 화면에서 안내 페이지로 가는 링크가 있다", () => {
  for (const f of ["app/stats/page.tsx", "app/stats/streamer/[channelId]/layout.tsx",
                   "app/stats/StatsNav.tsx"]) {
    assert.ok(read(f).includes('href="/stats/guide"'), `${f}에 안내 링크가 없다`);
  }
});

test("요구4: 안내 페이지에 특정 스트리머 이름·수치를 넣지 않는다", () => {
  const s = read("app/stats/guide/page.tsx");
  // 동적 보간이 있으면 페이지마다 달라지는 글이 된다.
  assert.ok(!/\{name\}|\{sm\?\.|toLocaleString/.test(s));
});

test("요구4: sitemap에 안내 페이지가 있고 전역 /guide와 구분된다", () => {
  const s = read("app/sitemap.ts");
  assert.ok(s.includes("${SITE}/stats/guide"));
  assert.ok(s.includes("${SITE}/guide"), "디스코드 봇 가이드를 지우지 않는다");
});

// ── 요구 5: 활동 잔디 한국어 ────────────────────────────────────────────────
test("요구5: 요일 7개가 모두 한국어 한 글자다", () => {
  const s = read("app/stats/streamer/[channelId]/Heatmap.tsx");
  assert.ok(/const DOW = \["일", "월", "화", "수", "목", "금", "토"\]/.test(s));
  assert.ok(!/"Mon"|"Wed"|"Fri"/.test(s), "영문 요일이 남아 있다");
});

test("요구5: 월 12개가 한국어다", () => {
  const s = read("app/stats/streamer/[channelId]/Heatmap.tsx");
  for (let m = 1; m <= 12; m++) assert.ok(s.includes(`"${m}월"`), `${m}월 누락`);
  assert.ok(!/"Jan"|"Feb"|"Dec"/.test(code("app/stats/streamer/[channelId]/Heatmap.tsx")),
    "영문 월이 남아 있다");
});

test("요구5: 브라우저 locale에 의존하지 않는다", () => {
  const s = read("app/stats/streamer/[channelId]/Heatmap.tsx");
  // toLocaleDateString은 실행 환경 locale에 따라 결과가 달라져 SSR/CSR이 어긋난다.
  assert.ok(!code("app/stats/streamer/[channelId]/Heatmap.tsx").includes("toLocaleDateString"),
    "locale 의존 포맷을 쓰면 안 된다");
  assert.ok(s.includes("KST_OFFSET_MS"), "KST 기준 고정이 없다");
  // 로컬 게터를 쓰면 실행 위치에 따라 하루가 밀린다.
  assert.ok(!/\.getMonth\(\)|\.getDate\(\)|setHours\(/.test(s.replace(/getUTC\w+\(\)/g, "")),
    "로컬 시간 게터가 남아 있다");
});

test("요구5: 월 라벨 겹침 방지 로직이 있다", () => {
  const s = read("app/stats/streamer/[channelId]/Heatmap.tsx");
  assert.ok(s.includes("MIN_LABEL_GAP"));
  assert.ok(/MIN_LABEL_GAP = \d+/.test(s));
  assert.ok(s.includes("continue"), "간격이 좁을 때 건너뛰는 분기가 없다");
});

test("요구5: 범례와 잔디 색상은 그대로 둔다", () => {
  const s = read("app/stats/streamer/[channelId]/Heatmap.tsx");
  assert.ok(s.includes("Less") && s.includes("More"), "범례를 이번 범위에서 바꾸지 않는다");
  assert.ok(s.includes('"rgba(0,255,163,0.10)"'), "잔디 색 단계를 바꾸지 않는다");
});

// ── 요구 6: 카테고리 현황 행 정렬 ───────────────────────────────────────────
test("요구6: 카테고리(게임)별 현황 행이 수직 가운데다", () => {
  const s = read("app/stats/page.tsx");
  const i = s.indexOf("카테고리(게임)별 현황");
  assert.ok(i > -1);
  const block = s.slice(i, i + 3200);
  assert.ok(!block.includes("align-top"), "이 표에 align-top이 남아 있다");
  assert.ok((block.match(/align-middle/g) ?? []).length >= 5);
});

test("요구6: margin 임시 수정으로 맞추지 않는다", () => {
  const s = read("app/stats/page.tsx");
  const i = s.indexOf("카테고리(게임)별 현황");
  const block = s.slice(i, i + 3200);
  assert.ok(!/mt-\[|margin-top|marginTop/.test(block),
    "행 정렬을 margin으로 맞추면 행 높이가 바뀔 때 다시 깨진다");
});

// ── 요구 2 보강: fail-closed · snapshot 범위 · 현재 LIVE ────────────────────
test("요구2: 확정본이 없으면 실시간 값으로 물러서지 않는다", () => {
  const s = read("lib/useSingcupRanking.ts");
  // 503/실패에서 useSingcupMain 값을 내보내면 얼린 화면이 조용히 풀린다.
  assert.ok(s.includes('status === "live"'),
    "실시간 경로는 서버가 frozen:false라고 말할 때만이어야 한다");
  // 실시간 값을 쓰는 return은 live 분기 **하나뿐**이어야 한다.
  assert.equal((s.match(/data: live\.data/g) ?? []).length, 1,
    "실시간 데이터를 쓰는 분기가 둘 이상이다");
  assert.ok(s.includes("data: null, loading: status === \"loading\""),
    "loading/finalizing/error에서 실시간 데이터가 새어 나간다");
  // 분류는 순수 함수가 하고, live 판정도 그쪽에 있다(테스트가 따로 있다).
  const cls = read("lib/singcupRankingLoader.ts");
  assert.ok(cls.includes("b.frozen === false"), "동결 여부를 서버 응답으로 판단해야 한다");
});

test("요구2: 준비 중 화면이 실시간 순위를 그리지 않는다", () => {
  const s = read("app/stats/Singcup.tsx");
  assert.ok(s.includes('status === "finalizing"'), "준비 중 분기가 없다");
  const i = s.indexOf('status === "finalizing"');
  const block = s.slice(i, i + 1600);
  assert.ok(block.includes("최종 집계를 준비하고 있습니다"));
  assert.ok(!block.includes("rows.map"), "준비 중 화면에 순위 목록이 그려진다");
});

test("요구2: 확정본에 운영 상태를 담지 않는다(백엔드 계약)", () => {
  const s = read("../backend/singcup_final.py");
  assert.ok(s.includes('_RUNTIME_KEYS = ("live", "collector")'));
  assert.ok(s.includes('_RUNTIME_ENTRY_KEYS = ("isLive", "live", "liveTitle")'));
  assert.ok(s.includes('summary.pop("liveCount"'), "현재 라이브 수가 얼어붙는다");
});

test("요구2: 확정본 화면에서 현재 라이브 타일을 감춘다", () => {
  const s = read("app/stats/Singcup.tsx");
  const i = s.indexOf('<Tile label="현재 라이브"');
  assert.ok(i > -1);
  assert.ok(s.slice(Math.max(0, i - 400), i).includes("{!final && ("),
    "확정본에서도 '현재 라이브' 타일이 뜬다 — 얼린 운영 상태를 현재처럼 보여준다");
});

test("요구2: 플래그 계약이 코드에 명시돼 있다", () => {
  const s = read("../backend/singcup_final.py");
  assert.ok(s.includes('_TRUE = {"true", "1", "yes", "on"}'));
  assert.ok(s.includes('_FALSE = {"false", "0", "no", "off"}'));
  assert.ok(s.includes("미설정 = 기본 동결"), "기본값 계약이 명시돼 있지 않다");
  assert.ok(s.includes("fail-closed"), "이상값 처리 정책이 없다");
});

// ── 차단 조건 H: finalizing과 error 분리 ────────────────────────────────────
test("요구2: 훅이 오류를 finalizing으로 뭉개지 않는다", () => {
  const hook = read("lib/useSingcupRanking.ts");
  const loader = read("lib/singcupRankingLoader.ts");
  // 훅은 상태를 스스로 단정하지 않는다 — 분류·타이머·예산은 전부 로더가 맡는다.
  assert.ok(hook.includes("FinalRankingLoader"), "로더에 위임해야 한다");
  assert.ok(!/setStatus\(|setSnap\(\{/.test(hook.replace("setSnap(s)", "")),
    "훅이 직접 상태를 단정한다 — 실제 장애가 준비 중으로 둔갑한다");
  assert.ok(hook.includes("retry") && hook.includes("retrying"), "재시도 계약이 없다");
  // 중복 요청 방지는 로더가 갖는다.
  assert.ok(loader.includes("if (this.disposed || this.inFlight) return;"),
    "중복 요청 방지가 없다");
  assert.ok(loader.includes("classifyFinalRanking"), "분류 함수를 쓰지 않는다");
});

test("요구2: error 화면은 문구가 다르고 재시도 버튼이 있다", () => {
  const s = read("app/stats/Singcup.tsx");
  assert.ok(s.includes("최종 집계를 불러오지 못했습니다"), "error 문구가 없다");
  assert.ok(s.includes("최종 집계를 준비하고 있습니다"), "finalizing 문구가 없다");
  assert.ok(s.includes("최종 집계 다시 불러오기"), "재시도 버튼이 없다");
  assert.ok(s.includes("disabled={retrying}"), "재시도 버튼 중복 클릭이 막히지 않는다");
});

test("요구2: 실패 화면에 내부 정보를 노출하지 않는다", () => {
  const s = read("app/stats/Singcup.tsx");
  const i = s.indexOf('status === "finalizing" || status === "error"');
  assert.ok(i > -1);
  const block = s.slice(i, i + 2600);
  // 상태 코드·엔드포인트·오류 객체를 화면에 찍으면 내부 정보가 샌다.
  for (const bad of ["httpStatus", "err.message", "/api/", "stack", "BASE"]) {
    assert.ok(!block.includes(bad), `실패 화면에 ${bad}가 노출된다`);
  }
});

// ── 차단 조건 G: 자동 회복 ──────────────────────────────────────────────────
test("요구2: 확정본 부재 시 재시도를 한 번만 예약한다(백엔드 계약)", () => {
  const s = read("../backend/singcup_final.py");
  assert.ok(s.includes("def schedule_finalize_if_needed"), "재시도 경로가 없다");
  assert.ok(s.includes("_finalize_task is not None and not _finalize_task.done()"),
    "동시 요청에서 task가 여러 개 생긴다");
  assert.ok(/left = cooldown_remaining\(\)\s*\n\s*if left > 0:/.test(s),
    "cooldown 가드가 없다 — 요청마다 DB를 두드린다");
  // cooldown 스킵도 기록하되 창마다 한 번만 — 요청마다 남기면 로그가 폭증한다.
  assert.ok(s.includes('_log("finalize_skipped_cooldown"'), "cooldown 스킵 로그가 없다");
  assert.ok(s.includes("_cooldown_logged_for != _last_attempt_at"),
    "cooldown 로그가 요청마다 쌓인다");
  assert.ok(s.includes("RETRY_AFTER_SECONDS = int(FINALIZE_COOLDOWN_SECONDS)"),
    "Retry-After와 cooldown이 어긋난다");
});

test("요구2: 라우터가 재시도를 예약하되 기다리지 않는다", () => {
  const s = read("../backend/routers/singcup_router.py");
  const block = s.split('@router.get("/final-ranking")')[1].split("@router.get")[0];
  assert.ok(block.includes("schedule_finalize_if_needed"), "재시도 예약이 없다");
  // await하면 무거운 확정 계산이 공개 GET을 붙잡는다.
  assert.ok(!block.includes("await singcup_final.schedule"), "요청이 확정 계산을 기다린다");
  assert.ok(block.includes("RETRY_AFTER_SECONDS"));
});

test("요구2: 관측 로그에 민감정보 필드를 허용하지 않는다", () => {
  const s = read("../backend/singcup_final.py");
  const i = s.indexOf("def _log(");
  assert.ok(i > -1, "관측 로그 함수가 없다");
  const block = s.slice(i, i + 900);
  // 허용 목록 방식이어야 한다 — 통과 목록이 없으면 무엇이든 실린다.
  assert.ok(block.includes('for k in ("attempt", "outcome", "cooldownSeconds", "durationMs", "errorType", "bytes")'),
    "로그 필드가 허용 목록으로 제한되지 않는다");
  assert.ok(block.includes("type(") === false, "로그 함수 안에서 예외 객체를 직접 다루면 안 된다");
});

// ── 차단 조건 I: finalizing 자동 재확인 ─────────────────────────────────────
test("요구2: finalizing이면 클릭 없이 자동으로 다시 확인한다", () => {
  const s = read("lib/singcupRankingLoader.ts");
  assert.ok(s.includes("scheduleNext"), "자동 재확인 예약이 없다");
  assert.ok(s.includes("parseRetryAfterMs(outcome.retryAfter)"),
    "Retry-After를 자동 간격으로 쓰지 않는다");
  // finalizing에서만 예약해야 한다 — 다른 실패에 폴링을 걸면 장애를 못 본다.
  const i = s.indexOf('if (c.kind === "finalizing")');
  assert.ok(i > -1);
  assert.ok(s.slice(i, i + 700).includes("this.scheduleNext("));
});

test("요구2: 자동 재확인에 상한과 정리 계약이 있다", () => {
  const s = read("lib/singcupRankingLoader.ts");
  assert.ok(/MAX_AUTO_CHECKS = \d+/.test(s), "무한 폴링 상한이 없다");
  assert.ok(s.includes("this.autoChecks >= MAX_AUTO_CHECKS"), "상한 검사가 없다");
  assert.ok(s.includes("this.clearTimer();"), "타이머 정리가 없다");
  assert.ok(s.includes("this.abort?.abort()"), "진행 중 요청 취소가 없다");
  assert.ok(s.includes("if (this.disposed) return;"), "unmount 후 setState를 막지 않는다");
  assert.ok(s.includes("this.inFlight"), "중복 요청 가드가 없다");
});

test("요구2: 훅이 unmount에서 로더를 정리한다", () => {
  const s = read("lib/useSingcupRanking.ts");
  assert.ok(s.includes("loader.dispose()"), "unmount 정리가 없다");
  assert.ok(s.includes("{ signal }"), "AbortSignal을 fetch에 넘기지 않는다");
});

test("요구2: 서버가 Retry-After를 교차 출처로 노출한다", () => {
  const s = read("../backend/routers/singcup_router.py");
  assert.ok(s.includes('"Access-Control-Expose-Headers": "Retry-After"'),
    "브라우저가 Retry-After를 읽지 못해 서버 의도가 무시된다");
});

test("요구2: finalizing 문구가 자동 재확인을 알린다", () => {
  const s = read("app/stats/Singcup.tsx");
  assert.ok(s.includes("자동으로 다시 확인해"), "자동 재확인 안내가 없다");
});
