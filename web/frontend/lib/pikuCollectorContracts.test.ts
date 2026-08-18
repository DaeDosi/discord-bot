/* 브라우저 기반 PIKU Collector — 화면·확장 계약(소스 텍스트로 확인).
 *
 * 지키는 것: 확장에 secret이 없다 · 쿠키/원문 HTML을 보내지 않는다 ·
 * 자동 기능이 기본 꺼짐으로 보인다 · 관리 화면에도 비율값이 없다 ·
 * 공개는 세 부문이 모두 준비됐을 때만 누를 수 있다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
const ext = (p: string) =>
  readFileSync(new URL(`../../../tools/piku-collector-extension/${p}`, import.meta.url), "utf8");
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const PANEL = () => read("app/nexadmin/PikuCollectorPanel.tsx");
const API = () => read("lib/api.ts");

test("관리 화면에 우승 비율·승률이 없다", () => {
  const c = code(PANEL());
  for (const bad of ["winRate", "matchRate", "win_rate", "match_rate", "winRatio"]) {
    assert.ok(!c.includes(bad), `관리 화면에 내부 비율이 있다: ${bad}`);
  }
});

test("자동 수집·자동 공개가 상태로 표시된다", () => {
  const s = PANEL();
  assert.ok(s.includes("autoCollectEnabled"));
  assert.ok(s.includes("autoPublishEnabled"));
  assert.ok(s.includes("둘 다 기본이 꺼짐입니다"), "기본이 꺼짐임을 밝힌다");
  assert.ok(s.includes("PC와 Chrome이 켜져 있을 때만"),
    "브라우저가 꺼져 있으면 갱신되지 않는다는 점을 명시한다");
});

test("공개는 세 부문이 모두 준비됐을 때만 누를 수 있다", () => {
  const s = PANEL();
  assert.ok(s.includes("!st?.publishReady"), "준비 안 되면 비활성이어야 한다");
  assert.ok(s.includes("공개할 수 없는 이유"), "막힌 이유를 알려 준다");
  assert.ok(s.includes("하나라도"), "부분 공개를 하지 않는다는 것을 밝힌다");
});

test("실패를 성공으로 꾸미지 않는다", () => {
  const c = code(PANEL());
  assert.ok(c.includes("FAILURE_LABEL"), "실패 종류를 구분해 보여 준다");
  assert.ok(c.includes('setMsg({ ok: false'), "실패는 실패로 표시한다");
});

test("api 클라이언트가 collector 경로만 부른다", () => {
  const s = API();
  for (const p of ["/api/admin/piku/collector/status",
    "/api/admin/piku/collector/token", "/api/admin/piku/collector/preview",
    "/api/admin/piku/collector/publish"]) {
    assert.ok(s.includes(p), `${p}가 없다`);
  }
  // 확장이 쓰는 ingest는 프론트에서 부르지 않는다(브라우저 확장 전용).
  assert.ok(!s.includes("/api/admin/piku/collector/ingest"));
});

test("확장 매니페스트가 최소 권한이다", () => {
  const m = JSON.parse(ext("manifest.json"));
  assert.equal(m.manifest_version, 3);
  for (const bad of ["<all_urls>", "cookies", "webRequest", "storage", "tabs"]) {
    assert.ok(!(m.permissions || []).includes(bad), `${bad} 권한을 요구한다`);
  }
  assert.ok(m.host_permissions.every((h: string) =>
    h.startsWith("https://www.piku.co.kr/") || h.startsWith("https://nexbot.shop/")));
});

test("확장에 secret이 없다", () => {
  for (const f of ["manifest.json", "popup.html", "popup.js", "collect.js"]) {
    const s = ext(f);
    for (const bad of ["SINGCUP_ADMIN_SECRET", "JWT_SECRET", "OWNER_ID", "Bearer "]) {
      assert.ok(!s.includes(bad), `${f}에 ${bad}가 있다`);
    }
  }
});

test("확장이 쿠키·저장소·원문 HTML을 다루지 않는다", () => {
  for (const f of ["popup.js", "collect.js"]) {
    const c = code(ext(f));
    for (const bad of ["document.cookie", "chrome.cookies", "chrome.storage",
      "localStorage", "innerHTML", "outerHTML"]) {
      assert.ok(!c.includes(bad), `${f}가 ${bad}를 다룬다`);
    }
  }
  assert.ok(ext("popup.js").includes('credentials: "omit"'));
});

test("확장이 PIKU에 추가 요청을 보내지 않는다", () => {
  const c = code(ext("collect.js"));
  for (const bad of ["fetch(", "XMLHttpRequest", "axios"]) {
    assert.ok(!c.includes(bad), `collect.js가 요청을 만든다: ${bad}`);
  }
});

test("확장이 차단 화면에서 멈추고 우회하지 않는다", () => {
  const s = ext("collect.js");
  assert.ok(s.includes("BLOCKED"));
  for (const bad of ["User-Agent", "solveCaptcha", "proxy"]) {
    assert.ok(!s.includes(bad), `우회 흔적: ${bad}`);
  }
});

test("확장이 곡·가수를 하이픈으로 추측하지 않는다", () => {
  const s = ext("collect.js");
  assert.ok(!s.includes('split("-")') && !s.includes("split('-')"));
  assert.ok(!s.includes('split(" - ")'));
});

test("확장이 부분 데이터를 보내지 않는다", () => {
  const s = ext("collect.js");
  assert.ok(s.includes('fail("partial"'), "행 수가 모자라면 중단한다");
  assert.ok(s.includes("meta.expected"));
});

// ── 이름 매핑 확정 UI ───────────────────────────────────────────────────────
const MAP = () => read("app/nexadmin/PikuMappingReview.tsx");

test("매핑 화면에 우승 비율·승률이 없다", () => {
  const c = code(MAP());
  for (const bad of ["winRate", "matchRate", "win_rate", "match_rate"]) {
    assert.ok(!c.includes(bad), `매핑 화면에 내부 비율이 있다: ${bad}`);
  }
});

test("상태별 표시가 구분된다", () => {
  const s = MAP();
  for (const k of ["confirmed", "suggested", "unmapped"]) {
    assert.ok(s.includes(k), `${k} 상태 표시가 없다`);
  }
  assert.ok(s.includes("중복 연결"), "중복을 별도로 알린다");
});

test("일괄 확정은 정확 일치 건수를 밝히고 누르게 한다", () => {
  const s = MAP();
  assert.ok(s.includes("pikuCollectorConfirmExact"));
  assert.ok(s.includes("정확 일치 {exact}건 확정"), "건수를 버튼에 적는다");
  // "전체 자동 확정" 같은 위험한 버튼을 두지 않는다. 주석은 뺀다 —
  // "자동 확정하지 않는다"고 적은 설명까지 걸리면 계약이 뒤집힌다.
  const c = code(s);
  for (const bad of ["전체 자동", "모두 자동", "전체 확정"]) {
    assert.ok(!c.includes(bad), `위험한 자동 확정 버튼: ${bad}`);
  }
  assert.ok(!/onClick=\{[^}]*confirmAll/.test(c));
});

test("미확정 필터와 검색이 있다", () => {
  const s = MAP();
  assert.ok(s.includes("미확정만 보기"));
  assert.ok(s.includes("onlyPending"));
  assert.ok(s.includes("매핑 검색"), "검색 입력에 이름을 준다");
});

test("후보는 목록에서 고른다(채널 id 직접 입력 아님)", () => {
  const s = MAP();
  assert.ok(s.includes("공식 참가자 검색"));
  assert.ok(s.includes("candidates"));
  assert.ok(s.includes("사용 중"), "이미 쓰인 후보를 표시한다");
  // 채널 id를 손으로 넣는 입력이 없어야 한다(오타가 곧 오연결이다).
  assert.ok(!/aria-label="채널\s*ID"/.test(s));
});

test("그룹은 전체 팀원과 대표자를 함께 보여 준다", () => {
  const s = MAP();
  assert.ok(s.includes("teamMembers"));
  assert.ok(s.includes("팀 {r.teamMembers}"), "원본 팀 문자열을 보여 준다");
});

test("좁은 화면에서는 표가 아니라 카드로 쌓인다", () => {
  const s = MAP();
  assert.ok(!s.includes("<table"), "260px에서 8열 표는 가로로 밀린다");
  assert.ok(s.includes("<ul") && s.includes("<li"));
});

test("Collector 패널이 차단 사유를 서버에서 받는다", () => {
  const s = read("app/nexadmin/PikuCollectorPanel.tsx");
  assert.ok(s.includes("st?.blockers"), "화면에서 다시 계산하지 않는다");
  assert.ok(s.includes("공개할 수 없는 이유"));
  assert.ok(s.includes("pikuCollectorPublishPreview"), "공개 전 확인이 있다");
  assert.ok(s.includes("sortLabel"), "내부 정렬 기준을 밝힌다");
});

test("토큰이 URL이나 로그에 남지 않는다", () => {
  const s = read("app/nexadmin/PikuCollectorPanel.tsx");
  // 토큰은 POST 본문/헤더로만 오간다 — 쿼리스트링에 넣지 않는다.
  assert.ok(!/token=\$\{/.test(s));
  assert.ok(!code(s).includes("console.log"), "토큰이 콘솔에 찍히면 안 된다");
  const e = code(ext("popup.js"));
  assert.ok(!e.includes("console.log"));
  assert.ok(e.includes('"X-Collector-Token"'), "헤더로만 보낸다");
});

test("확장이 페이지 크기를 스스로 바꾸지 않는다", () => {
  const s = ext("collect.js");
  // 서버측 DataTables면 선택 변경이 곧 내부 API 요청이다.
  assert.ok(!/\.value\s*=/.test(s), "select 값을 바꾸면 요청이 나갈 수 있다");
  assert.ok(s.includes("보기 개수"), "대신 운영자에게 무엇을 할지 알린다");
});

test("확장이 같은 수집을 두 번 보내지 않는다", () => {
  const s = code(ext("popup.js"));
  assert.ok(s.includes("lastSent"));
  assert.ok(s.includes("fingerprint"));
});
