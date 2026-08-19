/* AUTO-2 — 스케줄러 화면·확장의 **구조 계약**.
 *
 * 실제 스케줄 동작은 `tools/piku-collector-extension/scheduler.test.mjs`가,
 * 서버 계약은 `tests/test_piku_scheduler.py`가 본다. 이 파일이 막는 것은
 * **그 결과를 만든 구조가 조용히 원복되는 것**뿐이다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
const EXT = (f: string) => read(`../../../tools/piku-collector-extension/${f}`);
const MANIFEST = () => JSON.parse(EXT("manifest.json"));
const PANEL = () => read("../app/nexadmin/PikuAutomationPanel.tsx");
const API = () => read("./api.ts");

/** 주석 제거 — "쓰지 않는다"고 적은 주석까지 걸리면 계약이 뒤집힌다.
 *  (`tests/test_piku_collector.py`의 `_strip_js_comments`와 같은 이유다.) */
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

// ── 확장 권한 ───────────────────────────────────────────────────────────────
test("AUTO-2가 더한 권한은 alarms 하나뿐이다", () => {
  const m = MANIFEST();
  assert.deepEqual(m.permissions, ["activeTab", "scripting", "alarms"],
    `권한이 예상과 다르다: ${JSON.stringify(m.permissions)}`);
  // `tabs`는 실측으로 **필요 없다**(host_permissions만으로 query/reload/executeScript가
  // 전부 된다). 넣으면 사용자에게 "모든 탭 읽기" 경고가 뜬다.
  for (const banned of ["tabs", "cookies", "webRequest", "storage", "history",
                        "clipboardRead", "downloads"]) {
    assert.ok(!m.permissions.includes(banned),
      `${banned} 권한이 추가됐다 — 필요해진 근거를 문서·테스트에 먼저 적을 것`);
  }
  assert.ok(!JSON.stringify(m.host_permissions).includes("<all_urls>"));
  assert.ok(m.host_permissions.every((h: string) =>
    h.startsWith("https://www.piku.co.kr/w/rank/")
    || h.startsWith("https://nexbot.shop/api/admin/piku/collector/")),
    `host_permissions가 넓어졌다: ${JSON.stringify(m.host_permissions)}`);
});

test("서비스 워커가 선언돼 있고 모듈이다", () => {
  const m = MANIFEST();
  assert.equal(m.background?.service_worker, "sw.js");
  assert.equal(m.background?.type, "module", "scheduler.js를 import하려면 module이어야 한다");
});

// ── 정본 URL fail-closed ────────────────────────────────────────────────────
test("정본 source ID 세 개가 코드에 고정돼 있다", () => {
  const s = EXT("scheduler.js");
  for (const [div, id] of [["female_solo", "8jGsHE"], ["male_solo", "7PqH44"],
                           ["groups", "7fXoNs"]]) {
    assert.ok(new RegExp(`${div}:\\s*\\{\\s*id:\\s*"${id}"`).test(s),
      `${div}의 정본 ID가 ${id}가 아니다`);
  }
});

test("탭 URL을 정확히 일치로만 받는다", () => {
  const s = EXT("scheduler.js");
  // host_permissions의 경로는 접근을 제한하지 못한다(AUTO-1 실측). 코드가 막아야 한다.
  assert.ok(s.includes("t.url === want"),
    "정본 URL 정확 일치 검사가 사라졌다 — 오리진 안 아무 경로나 읽게 된다");
  assert.ok(/p\.division !== division \|\| p\.sourceId !== SOURCES\[division\]\.id/.test(s),
    "읽어 온 payload의 부문·sourceId 재확인이 사라졌다");
});

test("행 수가 맞지 않으면 전송하지 않는다", () => {
  const s = EXT("scheduler.js");
  assert.ok(/p\.rowCount !== SOURCES\[division\]\.expected/.test(s),
    "행 수 검사가 사라졌다 — 부분 전송이 가능해진다");
  assert.ok(/expected:\s*64/.test(s) && /expected:\s*32/.test(s));
});

test("탭을 새로 만들거나 함부로 새로고침하지 않는다", () => {
  const s = code(EXT("scheduler.js") + EXT("sw.js"));
  assert.ok(!/tabs\.create/.test(s), "탭 생성 경로가 생겼다");
  // 새로고침은 사용자가 켰을 때만.
  assert.ok(/if \(state\.reloadBeforeRead\)/.test(EXT("scheduler.js")),
    "새로고침이 무조건 실행된다");
});

test("PIKU에 직접 요청하지 않는다", () => {
  const s = code(EXT("scheduler.js") + EXT("sw.js"));
  assert.ok(!/fetch\(\s*["'`]https:\/\/www\.piku\.co\.kr/.test(s),
    "PIKU를 직접 fetch한다");
});

// ── lock · 상태 지속성 ──────────────────────────────────────────────────────
test("lock은 저장소 안에서 원자적으로 잡는다", () => {
  const s = EXT("scheduler.js");
  assert.ok(s.includes("env.store.swap"),
    "읽고 나서 쓰면 두 컨텍스트가 같은 순간에 들어간다");
  assert.ok(/expiresAt/.test(s), "lock 만료가 없다 — 죽은 lock이 영원히 막는다");
  assert.ok(/finally\s*\{[\s\S]{0,200}releaseLock/.test(s),
    "예외 경로에서 lock을 놓지 않는다");
});

test("스케줄 상태를 서비스 워커 메모리에 두지 않는다", () => {
  const s = EXT("scheduler.js");
  // 다음 실행 시각·lock·지문이 전부 주입된 저장소로 간다.
  for (const k of ["nextRunAt", "lastFingerprint", "consecutiveFailures"]) {
    assert.ok(s.includes(k), `${k}가 사라졌다`);
  }
  assert.ok(!/^let\s+(lock|nextRunAt)\b/m.test(s), "모듈 전역 상태를 쓴다");
});

test("서비스 워커는 IndexedDB를 쓰고 chrome.storage를 쓰지 않는다", () => {
  const s = EXT("sw.js");
  assert.ok(s.includes("indexedDB.open"), "IndexedDB 저장이 사라졌다");
  assert.ok(!/chrome\.storage/.test(code(s)),
    "chrome.storage를 쓰면 storage 권한이 필요해진다");
});

test("절전 복귀에 몰아서 돌지 않는다", () => {
  const s = EXT("scheduler.js");
  assert.ok(/skipped: "too_soon"/.test(s), "최소 간격 게이트가 사라졌다");
  assert.ok(/MAX_BACKOFF_MS/.test(s), "백오프 상한이 사라졌다");
});

// ── 모드 게이트 ─────────────────────────────────────────────────────────────
test("MANUAL이면 자동 실행이 0이다", () => {
  const s = EXT("scheduler.js");
  assert.ok(/!manual && mode === "MANUAL"/.test(s), "MANUAL 게이트가 사라졌다");
});

test("확장은 공개(Publish)를 하지 않는다", () => {
  const s = code(EXT("scheduler.js") + EXT("sw.js"));
  assert.ok(!/collector\/publish/.test(s), "확장이 공개 경로를 부른다");
  assert.ok(/published: false/.test(EXT("scheduler.js")),
    "AUTO-2에 공개가 없다는 표시가 사라졌다");
});

test("상태 조회는 challenge를 만들지 않는 전용 경로를 쓴다", () => {
  const s = EXT("sw.js");
  assert.ok(s.includes('"device/state"'),
    "상태 조회가 challenge 발급으로 되돌아갔다 — 시간당 발급이 늘어난다");
});

test("확장이 토큰을 저장하지 않는다", () => {
  const s = code(EXT("scheduler.js") + EXT("sw.js"));
  assert.ok(!/store\.set\([^)]*token/.test(s), "토큰을 저장한다");
  assert.ok(/token = null/.test(EXT("scheduler.js")), "토큰을 즉시 버리지 않는다");
});

// ── Nexadmin 화면 ───────────────────────────────────────────────────────────
test("자동 공개 선택지를 노출하지 않는다", () => {
  const s = PANEL();
  assert.ok(/\["MANUAL", "AUTO_COLLECT"\]/.test(s),
    "모드 버튼에 AUTO_PUBLISH가 들어갔다");
  assert.ok(s.includes("준비되지 않음"), "자동 공개가 아직 없다는 표시가 없다");
  assert.ok(s.includes("autoPublishReady"), "서버가 준 준비 여부를 쓰지 않는다");
});

test("부분 성공을 성공과 구분해 보여 준다", () => {
  const s = PANEL();
  assert.ok(s.includes("일부만 완료"), "partial 표기가 없다");
  assert.ok(s.includes("세 부문 완료"), "success 표기가 없다");
  // 색만으로 구분하지 않는다.
  for (const g of ["✔", "⚠", "✖"]) assert.ok(s.includes(g), `글리프 ${g}가 없다`);
});

test("실패 사유를 사람이 읽을 문장으로 바꾼다", () => {
  const s = PANEL();
  for (const k of ["no_tab", "row_count", "token_failed", "ingest_failed",
                   "ambiguous_tab", "loading"]) {
    assert.ok(s.includes(`${k}:`), `${k} 설명이 없다`);
  }
  // 모르는 분류어를 감추지 않는다.
  assert.ok(/KIND_TEXT\[r\.kind\] \?\? r\.kind/.test(s),
    "모르는 실패 사유를 숨긴다");
});

test("실행 주체가 확장이라는 사실을 화면이 밝힌다", () => {
  const s = PANEL();
  assert.ok(s.includes("Chrome 확장"), "누가 실행하는지 적혀 있지 않다");
  assert.ok(s.includes("100개 보기"), "탭 전제 조건이 적혀 있지 않다");
});

test("자동인데 장치가 없으면 경고한다", () => {
  const s = PANEL();
  assert.ok(/auto && noDevice/.test(s), "켜 놓고 안 도는 상태를 경고하지 않는다");
  assert.ok(s.includes('role="alert"'));
});

test("화면에 secret을 그리지 않는다", () => {
  const s = PANEL();
  for (const bad of ["pairingCode", "publicKey", "privateKey", "signature", "nonce"]) {
    assert.ok(!s.includes(bad), `${bad}를 화면에서 다룬다`);
  }
});

// ── API · 비퇴행 ────────────────────────────────────────────────────────────
test("자동화 조회 API가 admin 네임스페이스에 있다", () => {
  const s = API();
  assert.ok(s.includes("pikuAutomation:"), "pikuAutomation이 없다");
  assert.ok(s.includes('"/api/admin/piku/collector/automation"'));
});

test("기존 수동 수집·장치 경로가 그대로다", () => {
  const s = API();
  for (const fn of ["pikuCollectorToken", "pikuCollectorStatus", "pikuCollectorPublish",
                    "pikuDevices", "pikuDeviceRegister", "pikuDeviceRevoke"]) {
    assert.ok(s.includes(`${fn}:`), `${fn}이 사라졌다 — 수동 경로는 유지해야 한다`);
  }
  const p = read("../../../tools/piku-collector-extension/popup.js");
  assert.ok(p.includes('$("run")') && p.includes('$("tok")'),
    "확장 팝업의 수동 경로가 사라졌다");
});
