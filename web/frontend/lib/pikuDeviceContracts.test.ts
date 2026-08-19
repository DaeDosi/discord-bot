/* AUTO-1 — 자동 수집 장치 화면·확장의 **보안 표시 계약**.
 *
 * 여기서 막으려는 것은 조용한 원복이다. 실제 서명·토큰 흐름은 파이썬 테스트
 * (`tests/test_piku_devices.py`)와 브라우저 QA가 확인한다. 이 파일은 **화면과 확장이
 * 비밀을 다루는 방식**이 되돌아가지 않게 소스 구조를 고정한다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
const PANEL = () => read("../app/nexadmin/PikuDevicePanel.tsx");
const DEVICE = () => read("../../../tools/piku-collector-extension/device.js");
const POPUP = () => read("../../../tools/piku-collector-extension/popup.js");
const MANIFEST = () =>
  JSON.parse(read("../../../tools/piku-collector-extension/manifest.json"));
const API = () => read("./api.ts");

// ── 확장: 키 취급 ───────────────────────────────────────────────────────────
test("장치 개인키는 비추출형으로 만든다", () => {
  const s = DEVICE();
  const i = s.indexOf("crypto.subtle.generateKey");
  assert.ok(i > -1, "키 생성이 사라졌다");
  const block = s.slice(i, i + 320);
  assert.ok(/namedCurve:\s*"P-256"/.test(block), "P-256이 아니다");
  // `extractable` 인자가 false여야 한다. true가 되면 개인키가 문자열이 될 수 있다.
  assert.ok(/\n\s*false,/.test(block),
    "generateKey의 extractable이 false가 아니다 — 개인키를 꺼낼 수 있게 된다");
  assert.ok(!/exportKey\(\s*"pkcs8"/.test(s), "개인키를 export하려 한다");
  assert.ok(!/exportKey\(\s*"jwk"\s*,\s*\w*\.?privateKey/.test(s),
    "개인키를 jwk로 export하려 한다");
});

test("개인키를 chrome.storage에 넣지 않는다", () => {
  const s = DEVICE();
  // `chrome.storage`는 구조화 복제로 직렬화한다 — CryptoKey를 넣을 수 없고,
  // 넣으려 시도한다는 것 자체가 키를 문자열로 만들었다는 뜻이다.
  assert.ok(!/storage\.local\.set\([^)]*privateKey/.test(s),
    "개인키를 chrome.storage에 저장한다");
  assert.ok(s.includes("indexedDB.open"), "IndexedDB 보관이 사라졌다");
});

test("확장은 토큰·등록 코드를 저장하지 않는다", () => {
  const s = DEVICE() + POPUP();
  assert.ok(!/storage\.local\.set\([^)]*\btoken\b/.test(s),
    "수집 토큰을 저장한다 — 1회용이라 저장할 이유가 없다");
  assert.ok(!/storage\.local\.set\([^)]*pairingCode/.test(s),
    "등록 코드를 저장한다");
});

test("확장이 우리 관리 경로로만 보낸다", () => {
  const s = DEVICE();
  const i = s.indexOf("function apiUrl");
  const block = s.slice(i, i + 500);
  assert.ok(block.includes("/api/admin/piku/collector/"), "전송 경로가 고정돼 있지 않다");
  assert.ok(block.includes('u.protocol !== "https:"'), "https 강제가 사라졌다");
  assert.ok(s.includes('credentials: "omit"'), "쿠키를 함께 보내지 않는다는 계약이 사라졌다");
});

// ── 확장 권한 ───────────────────────────────────────────────────────────────
test("확장 권한이 최소로 유지된다", () => {
  const m = MANIFEST();
  // AUTO-1은 권한을 한 개도 더하지 않았다(장치 키·메타를 전부 IndexedDB에 두어
  // `storage`조차 피했다). **AUTO-2가 `alarms` 하나를 더했다** — 1시간 주기 실행에
  // `chrome.alarms`가 반드시 필요하고, 다른 수단(setTimeout)은 서비스 워커가 죽으면
  // 사라져 스케줄이 성립하지 않는다. 근거는 `docs/작업정리_2026-08-19_AUTO-2_*`와
  // `pikuSchedulerContracts.test.ts`에 있다.
  assert.deepEqual(m.permissions, ["activeTab", "scripting", "alarms"],
    `권한이 바뀌었다: ${JSON.stringify(m.permissions)}`);
  for (const banned of ["cookies", "webRequest", "history", "clipboardRead",
                        "tabs", "background", "storage"]) {
    assert.ok(!m.permissions.includes(banned),
      `${banned} 권한이 추가됐다 — 필요해진 이유를 문서와 테스트에 먼저 적을 것`);
  }
  assert.ok(!JSON.stringify(m.host_permissions).includes("<all_urls>"),
    "<all_urls>가 추가됐다");
  // 실측: host_permissions의 **경로는 executeScript를 제한하지 못한다**(오리진 단위).
  // 그래도 좁게 적어 두는 것은 의도 표현이므로 유지한다.
  assert.ok(m.host_permissions.every(
    (h: string) => h.startsWith("https://www.piku.co.kr/w/rank/")
      || h.startsWith("https://nexbot.shop/api/admin/piku/collector/")),
    `host_permissions가 넓어졌다: ${JSON.stringify(m.host_permissions)}`);
});

// ── Nexadmin 화면 ───────────────────────────────────────────────────────────
test("장치 목록에 비밀을 그리지 않는다", () => {
  const s = PANEL();
  assert.ok(!/d\.publicKey|\.pairingCode\b(?!\s*[,}])/.test(
    s.replace(/r\.pairingCode/g, "")),
    "목록에서 공개키·등록 코드를 읽는다");
  // 등록 코드는 **발급 직후 한 번만** 보여 준다.
  assert.ok(s.includes("이 화면을 벗어나면 다시 볼 수 없습니다"),
    "등록 코드가 1회성이라는 안내가 없다");
});

test("상태를 색만으로 구분하지 않는다", () => {
  const s = PANEL();
  const i = s.indexOf("function StatusBadge");
  const block = s.slice(i, i + 800);
  for (const g of ["✔", "⚠", "✖"]) {
    assert.ok(block.includes(g), `상태 글리프 ${g}가 없다`);
  }
  for (const t of ["사용 중", "등록 대기", "폐기됨"]) {
    assert.ok(block.includes(t), `상태 문구 ${t}가 없다`);
  }
});

test("AUTO_PUBLISH는 한 단계 더 확인받는다", () => {
  const s = PANEL();
  assert.ok(/confirmMode === "AUTO_PUBLISH"/.test(s), "위험 확인 단계가 없다");
  assert.ok(s.includes('role="alertdialog"'), "확인 단계가 경고로 노출되지 않는다");
  // 확인 없이 곧바로 적용되면 안 된다.
  assert.ok(/m === "AUTO_PUBLISH" && !on\s*\?\s*setConfirmMode/.test(s),
    "AUTO_PUBLISH가 확인 없이 바로 적용된다");
});

test("아직 자동 실행이 없다는 사실을 화면이 말한다", () => {
  const s = PANEL();
  assert.ok(s.includes("아직 자동 실행은 일어나지 않습니다"),
    "AUTO-2·AUTO-3 미구현 안내가 없다 — 켰는데 안 도는 것을 장애로 오해한다");
  assert.ok(s.includes("schedulerImplemented"), "서버가 준 구현 여부를 쓰지 않는다");
});

test("기본 모드가 수동이라는 표기를 유지한다", () => {
  const s = PANEL();
  assert.ok(/MANUAL:\s*"수동 \(기본\)"/.test(s), "MANUAL이 기본이라는 표기가 사라졌다");
});

// ── API 계약 ────────────────────────────────────────────────────────────────
test("장치 API가 admin 네임스페이스에 있고 경로가 고정돼 있다", () => {
  const s = API();
  for (const [fn, path] of [
    ["pikuDevices", "/api/admin/piku/collector/devices"],
    ["pikuDeviceRegister", "/api/admin/piku/collector/devices"],
    ["pikuDeviceRevoke", "/api/admin/piku/collector/devices/revoke"],
    ["pikuCollectorSetMode", "/api/admin/piku/collector/mode"],
  ]) {
    assert.ok(s.includes(`${fn}:`), `${fn}이 없다`);
    assert.ok(s.includes(`"${path}"`), `${path} 경로가 없다`);
  }
  // 대시보드가 서명·개인키를 다루면 안 된다 — 그건 확장의 일이다.
  assert.ok(!/pikuDeviceChallenge|pikuDeviceToken|signature/.test(s),
    "대시보드가 challenge·서명 경로를 부른다 — 확장만 해야 한다");
});

test("기존 수동 수집 경로가 그대로 남아 있다", () => {
  const s = API();
  for (const fn of ["pikuCollectorToken", "pikuCollectorStatus",
                    "pikuCollectorPublish", "pikuCollectorConfirmExact"]) {
    assert.ok(s.includes(`${fn}:`), `${fn}이 사라졌다 — 수동 경로는 유지해야 한다`);
  }
  const p = POPUP();
  assert.ok(p.includes('$("run")'), "수동 전송 버튼이 사라졌다");
  assert.ok(p.includes('$("tok")'), "수동 토큰 입력이 사라졌다");
});
