// 대시보드 오류 분류의 단위 테스트.
//
// 이 저장소의 프론트 테스트는 `node --test lib/*.test.ts`로 의존성 없이 돈다.
// 여기서 검증하는 것은 **순수 함수**라 실제로 실행해 볼 수 있다(소스 텍스트 대조가 아니다).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyDashboardError, dashboardErrorCopy, DASHBOARD_ERROR_COPY,
} from "./dashboardErrors.ts";

// ── 상태별 분류 ──────────────────────────────────────────────────────────────
test("401·403·404·5xx·네트워크가 서로 다른 종류로 갈린다", () => {
  const kinds = [
    classifyDashboardError({ status: 401 }),
    classifyDashboardError({ status: 403 }),
    classifyDashboardError({ status: 404 }),
    classifyDashboardError({ status: 500 }),
    classifyDashboardError({ status: 503 }),
    classifyDashboardError(new TypeError("Failed to fetch")),
  ];
  assert.deepEqual(kinds,
    ["unauthorized", "forbidden", "notFound", "server", "server", "network"]);
});

test("403을 로그인 유도로 뭉개지 않는다", () => {
  // 권한 없음을 '로그인하세요'로 안내하면 사용자는 될 때까지 재로그인만 반복한다.
  const forbidden = dashboardErrorCopy({ status: 403 });
  assert.equal(forbidden.kind, "forbidden");
  assert.ok(!/로그인/.test(forbidden.title + forbidden.detail),
    "403 문구가 로그인을 요구한다");
});

test("빈 데이터와 오류가 같은 문구를 쓰지 않고, 오류에는 할 일이 있다", () => {
  // 빈 상태는 '사용자가 할 일이 없다', 오류는 '할 일이 있다'는 뜻이다. 기준본에서는
  // 목록 로드 500이 빈 상태 문구로 표시돼 둘을 구분할 수 없었다.
  const EMPTY_STATE = "관리 권한이 있는 서버가 없습니다";
  for (const [kind, copy] of Object.entries(DASHBOARD_ERROR_COPY)) {
    assert.ok(!`${copy.title} ${copy.detail}`.includes(EMPTY_STATE),
      `${kind} 문구가 빈 상태 문구와 겹친다`);
    // 403·401도 '다시 로그인'·'다른 계정'처럼 다음 행동이 있어야 막다른 화면이 아니다.
    assert.ok(copy.detail.trim().length > 0, `${kind} 에 다음 행동 안내가 없다`);
  }
});

test("모든 종류의 문구가 서로 다르다", () => {
  const titles = Object.values(DASHBOARD_ERROR_COPY).map((c) => c.title);
  assert.equal(new Set(titles).size, titles.length, "같은 문구를 쓰는 상태가 있다");
});

// ── 내부 정보 노출 금지 ──────────────────────────────────────────────────────
test("사용자에게 보이는 문구에 내부 정보가 없다", () => {
  const forbidden = [
    /uvicorn/i, /localhost:\d+/, /127\.0\.0\.1/, /railway\.app/i,
    /NEXT_PUBLIC_/, /DISCORD_CLIENT_SECRET/, /\.env/, /Traceback/i,
    /SELECT |INSERT |UPDATE /i, /\/api\//,
  ];
  for (const [kind, copy] of Object.entries(DASHBOARD_ERROR_COPY)) {
    const text = `${copy.title} ${copy.detail}`;
    for (const re of forbidden) {
      assert.ok(!re.test(text), `${kind} 문구에 ${re}가 노출된다: ${text}`);
    }
  }
});

test("서버가 준 원문 메시지를 그대로 화면에 싣지 않는다", () => {
  // 백엔드 detail은 'internal' 같은 내부 문자열일 수 있다. 분류에만 쓰고 표시하지 않는다.
  const copy = dashboardErrorCopy({ status: 500, message: "internal" });
  assert.ok(!/internal/.test(copy.title + copy.detail));
});

// ── 재시도 가능 여부 ─────────────────────────────────────────────────────────
test("권한 문제는 재시도를 권하지 않고, 일시적 실패만 권한다", () => {
  // 403에 '다시 시도' 버튼을 두면 몇 번을 눌러도 같은 화면이라 사용자가 갇힌다.
  assert.equal(dashboardErrorCopy({ status: 403 }).retryable, false);
  assert.equal(dashboardErrorCopy({ status: 401 }).retryable, false);
  assert.equal(dashboardErrorCopy({ status: 500 }).retryable, true);
  assert.equal(dashboardErrorCopy(new TypeError("Failed to fetch")).retryable, true);
});

test("알 수 없는 오류도 문구가 있고 재시도할 수 있다", () => {
  const copy = dashboardErrorCopy(undefined);
  assert.ok(copy.title.length > 0);
  assert.equal(copy.retryable, true);
});
