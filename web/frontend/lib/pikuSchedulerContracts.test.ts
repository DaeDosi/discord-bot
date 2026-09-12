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

test("탭을 새로 만들지 않고, 읽기 전에는 항상 새로고침한다", () => {
  const s = code(EXT("scheduler.js") + EXT("sw.js"));
  assert.ok(!/tabs\.create/.test(s), "탭 생성 경로가 생겼다");
  // SINGCUP-FINAL-1: 새로고침은 **항상** 한다. AUTO-2의 "사용자가 켰을 때만"은
  // 매시간 같은 DOM을 읽어 지문이 같아 `unchanged`만 반복되는 결함(진단 d)이었다.
  assert.ok(!/reloadBeforeRead/.test(EXT("scheduler.js")), "선택형 새로고침이 남아 있다");
  assert.ok(/await env\.reloadTab\(tab\.id\)/.test(EXT("scheduler.js")),
    "읽기 전 새로고침이 없다");
});

// ── SINGCUP-FINAL-1 ─────────────────────────────────────────────────────────
test("본선 source가 확장 정본에 있고 plan 기반으로만 읽는다", () => {
  const s = EXT("scheduler.js");
  assert.ok(/final:\s*\{\s*id:\s*"2ut8Li",\s*expected:\s*32/.test(s), "본선 정본이 없다");
  assert.ok(/export function resolvePlan/.test(s), "plan 해석기가 없다");
  assert.ok(/skipped: "no_plan"/.test(s), "plan이 없을 때 돌지 않는 게이트가 없다");
  assert.ok(/for \(const d of resolved\.sources\)/.test(s), "plan의 source만 도는 루프가 아니다");
  assert.ok(!/for \(const d of DIVISIONS\)/.test(s), "예선 3부문을 고정으로 돈다");
});

test("다음 예정은 '예정 시각 + 주기'이고 alarm을 그 시각에 다시 맞춘다 (결함 b)", () => {
  const s = EXT("scheduler.js");
  assert.ok(/scheduledAt \+ wait/.test(s), "다음 예정이 종료 시각 기준이다");
  assert.ok(/createAlarm\(PERIOD_MS, nextRunAt\)/.test(s), "alarm을 예정 시각에 맞추지 않는다");
  assert.ok(/when \? \{ when \} : \{\}/.test(EXT("sw.js")), "sw.js가 alarm `when`을 넘기지 않는다");
});

test("회차 결과를 device/run으로 보고하되 데이터·토큰은 싣지 않는다 (결함 c)", () => {
  const sw = EXT("sw.js");
  assert.ok(/postJson\(base, "device\/run"/.test(sw), "회차 보고 경로가 없다");
  const m = /report: \(r\) => postJson\(base, "device\/run", \{([\s\S]*?)\}\)/.exec(sw);
  assert.ok(m, "보고 본문을 찾지 못했다");
  for (const bad of ["payload", "rows:", "token", "streamer"]) {
    assert.ok(!m![1].includes(bad), `보고 본문에 ${bad}가 실린다`);
  }
});

test("본선은 페이지를 넘겨 읽고 pager는 페이지 크기·내부 API를 건드리지 않는다", () => {
  const pager = EXT("pager.js");
  assert.ok(/paginate_button/.test(pager), "페이지 링크를 누르는 경로가 없다");
  assert.ok(!/page\.len\(|x\.php|fetch\(|XMLHttpRequest/.test(pager),
    "pager가 내부 API나 페이지 크기를 만진다");
  assert.ok(/readPaged/.test(EXT("scheduler.js")), "페이지 넘김 읽기가 없다");
  assert.ok(/kind: "rank_gap"/.test(EXT("scheduler.js")), "합친 뒤 순위 연속 검사가 없다");
});

test("popup에 '지금 테스트 수집' 1회 실행이 있고 공개 경로는 없다", () => {
  const html = EXT("popup.html");
  const js = EXT("popup.js");
  assert.ok(/id="runnow"/.test(html), "테스트 수집 버튼이 없다");
  assert.ok(/공개 안 함/.test(html), "공개하지 않는다는 문구가 없다");
  assert.ok(/type: "run-now"/.test(js), "run-now 메시지가 없다");
  assert.ok(/\$\("runnow"\)\.disabled = true/.test(js), "중복 클릭 방지가 없다");
  assert.ok(!/collector\/publish/.test(js + EXT("sw.js")), "확장이 공개 경로를 부른다");
});

test("확장 권한은 여전히 activeTab·scripting·alarms 셋뿐이다", () => {
  const m = JSON.parse(EXT("manifest.json"));
  assert.deepEqual(m.permissions, ["activeTab", "scripting", "alarms"]);
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
  assert.ok(s.includes("plan 전부 완료"), "success 표기가 없다");
  assert.ok(s.includes("표가 그대로"), "unchanged 표기가 없다");
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
  assert.ok(s.includes("활성 plan의 PIKU 탭"), "탭 전제 조건이 적혀 있지 않다");
  assert.ok(s.includes("예선 탭은 필요 없습니다"), "예선 탭이 불필요하다는 사실을 밝히지 않는다");
});

test("자동인데 장치가 없으면 경고한다", () => {
  const s = PANEL();
  assert.ok(/auto && noDevice/.test(s), "켜 놓고 안 도는 상태를 경고하지 않는다");
  assert.ok(s.includes('role="alert"'));
});

test("화면에 secret을 그리지 않는다", () => {
  const s = PANEL();
  // `bad_signature`는 **실패 종류의 이름**이지 값이 아니다 — 그 한 낱말만 예외로 둔다.
  const code = s.split("bad_signature:").join("failure_kind_label:");
  for (const bad of ["pairingCode", "publicKey", "privateKey", "signature", "nonce"]) {
    assert.ok(!code.includes(bad), `${bad}를 화면에서 다룬다`);
  }
  // 발급된 테스트 허가 코드는 **응답에서 받은 값을 한 번 그려 줄 뿐** 저장하지 않는다.
  assert.ok(!/localStorage|sessionStorage/.test(s), "허가 코드를 저장한다");
  assert.ok(/setGrant\(null\)/.test(s), "새 발급 전에 이전 코드를 지우지 않는다");
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

// ── SINGCUP-FINAL-1b: MANUAL 테스트 허가 · 실패 종류 · invocation 멱등성 ──────
test("확장은 누가 눌렀는지 그대로 말한다 — alarm만 automation:true", () => {
  const sw = EXT("sw.js");
  assert.ok(/automation: !!opts\.automation/.test(sw), "challenge가 automation을 고정한다");
  assert.ok(/automation: trigger === "alarm"/.test(sw), "trigger로 automation을 정하지 않는다");
  assert.ok(/opts\.testGrant \? \{ testGrant: opts\.testGrant \} : \{\}/.test(sw),
    "테스트 허가를 challenge에 넘기지 않는다");
  // 허가·토큰을 저장소에 남기지 않는다.
  assert.ok(!/store\.set\([^)]*(testGrant|grant)/.test(sw));
});

test("popup에 테스트 허가 입력란이 있고 1회용이라 전송 후 지운다", () => {
  const html = EXT("popup.html");
  const js = EXT("popup.js");
  assert.ok(/id="grant"/.test(html), "허가 입력란이 없다");
  assert.ok(/Nexadmin에서 발급/.test(html));
  assert.ok(/testGrant: grant \|\| undefined/.test(js), "허가를 워커에 넘기지 않는다");
  assert.ok(/\$\("grant"\)\.value = "";/.test(js), "1회용 코드를 비우지 않는다");
});

test("한 번의 클릭 = 하나의 invocationId = 서버 run 1건", () => {
  const js = EXT("popup.js");
  const sw = EXT("sw.js");
  const sched = EXT("scheduler.js");
  assert.ok(/if \(runInFlight\) return;/.test(js), "응답 대기 중 재클릭을 막지 않는다");
  assert.ok(/\$\("runnow"\)\.disabled = true;/.test(js), "첫 클릭 즉시 잠그지 않는다");
  assert.ok(/const invocationId = `manual-\$\{Date\.now\(\)\}/.test(js), "invocationId를 만들지 않는다");
  assert.ok(/const inFlight = new Map\(\)/.test(sw) && /inFlight\.has\(invocationId\)/.test(sw),
    "워커가 같은 invocation을 합류시키지 않는다");
  assert.ok(/invocationId: r\.invocationId/.test(sw), "보고에 invocationId가 없다");
  assert.ok(/invocationId: invocation/.test(sched), "회차 결과에 invocationId가 없다");
});

test("challenge/token 실패를 정규화된 종류로 나눈다(원문 노출 없음)", () => {
  const sched = EXT("scheduler.js");
  assert.ok(/export function classifyAuthError/.test(sched));
  for (const k of ["manual_mode", "test_grant_required", "device_not_active",
                   "protocol_too_old", "bad_signature", "challenge_expired",
                   "token_rejected", "relay_unavailable", "timeout"]) {
    assert.ok(sched.includes(k), `${k} 종류가 없다`);
  }
  assert.ok(!/kind: "token_failed"/.test(sched), "여전히 token_failed로 뭉갠다");
  // 팝업·Nexadmin이 같은 종류를 사람 문장으로 보여 준다.
  const popup = EXT("popup.js");
  const panel = readFileSync(new URL("../app/nexadmin/PikuAutomationPanel.tsx", import.meta.url), "utf8");
  for (const k of ["manual_mode", "test_grant_required", "protocol_too_old", "relay_unavailable"]) {
    assert.ok(popup.includes(`${k}:`), `popup에 ${k} 설명이 없다`);
    assert.ok(panel.includes(`${k}:`), `Nexadmin에 ${k} 설명이 없다`);
  }
  // 허가 실패는 조치가 서로 다르다(발급·재입력·재발급) → 네 갈래를 모두 보여 준다.
  for (const k of ["test_grant_required", "test_grant_invalid",
                   "test_grant_expired", "test_grant_used"]) {
    assert.ok(sched.includes(k), `scheduler가 ${k}를 종류로 인정하지 않는다`);
    assert.ok(popup.includes(`${k}:`), `popup에 ${k} 설명이 없다`);
    assert.ok(panel.includes(`${k}:`), `Nexadmin에 ${k} 설명이 없다`);
  }
  // 사람에게 보여 주는 **문구 표 안에** 내부 값이 섞이지 않는다.
  for (const src of [popup, panel]) {
    const table = /KIND_TEXT[^{]*\{([\s\S]*?)^\};/m.exec(src)?.[1] ?? "";
    assert.ok(table.length > 200, "KIND_TEXT 표를 찾지 못했다");
    // `bad_signature`는 종류 **이름**이라 괜찮다. 막는 것은 값과 내부 구조다.
    assert.ok(!/challengeId|publicKey|token_hash|grant_hash|SELECT |piku_|Traceback/.test(table),
              "실패 문구에 내부 값이 섞였다");
  }
});

test("실패해도 허가가 소비된다는 점을 두 화면이 밝힌다", () => {
  const html = readFileSync(new URL("../../../tools/piku-collector-extension/popup.html",
                                    import.meta.url), "utf8");
  const panel = readFileSync(new URL("../app/nexadmin/PikuAutomationPanel.tsx", import.meta.url), "utf8");
  assert.ok(/실패해도 코드는 소비/.test(html), "팝업이 1회 소비를 밝히지 않는다");
  assert.ok(/실패해도 코드는 소비/.test(panel), "Nexadmin이 1회 소비를 밝히지 않는다");
});

test("Nexadmin이 MANUAL에서 테스트 허가를 발급한다(자동 공개는 여전히 없음)", () => {
  const panel = readFileSync(new URL("../app/nexadmin/PikuAutomationPanel.tsx", import.meta.url), "utf8");
  assert.ok(/pikuDeviceTestGrant/.test(panel), "허가 발급 경로를 부르지 않는다");
  assert.ok(/!auto && !noDevice/.test(panel), "MANUAL·장치 있을 때만 보여 주지 않는다");
  assert.ok(/한 번만/.test(panel) && /10분/.test(panel), "1회용·만료를 밝히지 않는다");
  assert.ok(!/collector\/publish/.test(panel), "자동화 화면에서 공개를 부른다");
  const api = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
  assert.ok(/devices\/test-grant/.test(api));
});
