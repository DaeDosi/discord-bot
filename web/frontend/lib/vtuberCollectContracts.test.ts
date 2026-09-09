// VTUBER-1 — '버튜버' 그룹 수집 화면의 계약.
//
// 화면 쪽 계약만 여기서 본다(그룹 생성·태그 판정·신선도 게이트 자체는
// `tests/test_vtuber_group_collect.py`가 실제로 실행해 검증한다).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

const PANEL = () => read("app/nexadmin/StreamerTagsPanel.tsx");
const API = () => read("lib/api.ts");

/** 주석을 걷어낸 "실제로 렌더되는 코드". 주석에도 같은 낱말이 나오므로,
 *  존재/부재를 세는 단언은 반드시 이걸 통과한 문자열로 해야 한다. */
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

// ── 4. '버튜버' 수집 버튼 ────────────────────────────────────────────────────

test("수집 버튼은 진행 중 비활성화되고 같은 tick 중복 클릭도 막힌다", () => {
  const s = PANEL();
  const c = code(s);
  assert.ok(c.includes("버튜버 태그 라이브 수집"), "버튼 문구");
  assert.ok(c.includes("disabled={busy}") && c.includes("aria-busy={busy}"),
    "진행 중에는 버튼이 잠기고 그 사실이 보조기술에도 전달돼야 한다");
  // `busy` state는 다음 렌더에야 반영된다 — 같은 tick의 두 번째 클릭은 ref로 막는다.
  assert.ok(c.includes("const inFlight = useRef(false)")
    && c.includes("if (inFlight.current) return"),
    "ref 잠금이 없으면 빠른 두 번 클릭이 두 요청이 된다");
  assert.ok(c.includes("수집 중…"), "진행 상태 표시");
});

test("성공 후 멤버 수를 서버 응답으로 갱신한다", () => {
  const c = code(PANEL());
  assert.ok(c.includes("assignedCount: res.memberCount"),
    "화면 숫자는 서버가 준 값이어야 한다(직접 더하지 않는다)");
  assert.ok(c.includes("if (res.groupCreated) void load()"),
    "이번에 만들어진 그룹은 목록에 행 자체가 없으므로 그때만 다시 부른다");
});

test("신규 0을 실패처럼 보여주지 않는다", () => {
  const c = code(PANEL());
  assert.ok(c.includes("새로 추가할 스트리머가 없습니다. 기존 멤버 ${res.alreadyPresent}명"),
    "할 일이 없었을 뿐이라는 문구가 있어야 한다");
  assert.ok(c.includes("라이브 버튜버 후보 ${res.liveCandidates}명 · 신규 추가 ${res.added}명"),
    "추가가 있었을 때의 문구");
});

test("실패는 실패로 표시하고 재시도 가능 여부를 알린다", () => {
  const c = code(PANEL());
  assert.ok(c.includes('role="alert"'), "오류는 즉시 읽혀야 한다");
  assert.ok(c.includes("다시 시도해 주세요"), "재시도 가능 여부를 말한다");
  assert.ok(c.includes("setRes(null)"),
    "실패했는데 직전 성공 결과가 남아 있으면 성공처럼 읽힌다");
  assert.ok(c.includes("VTUBER_ERROR_LABEL[e.kind] ?? e.kind"),
    "모르는 실패 종류를 숨기지 않는다");
});

test("수집 API는 POST이고 그룹 이름을 클라이언트가 정하지 않는다", () => {
  const c = code(API());
  const i = c.indexOf("streamerTagsCollectVtuber");
  assert.ok(i > 0, "API 클라이언트에 수집 함수가 있어야 한다");
  // 다음 항목까지 삼키면 그쪽의 `body:`가 잡힌다 — 이 정의 하나만 자른다.
  const block = c.slice(i, c.indexOf("}),", i) + 3);
  assert.ok(block.includes('"/api/admin/streamer-tags/vtuber/collect"'), "고정 경로");
  assert.ok(block.includes('method: "POST"'), "mutation이므로 POST");
  assert.ok(!block.includes("body:"), "이름·태그를 본문으로 보내지 않는다");
});

test("그룹이 없어도 수집 영역이 보인다", () => {
  const c = code(PANEL());
  // 목록 행에만 버튼을 달면 그룹이 없는 최초 상태에서 만들 방법이 사라진다.
  assert.ok(c.includes("<VtuberCollectSection group={vtuberGroup}"),
    "목록과 무관하게 항상 렌더되는 영역이어야 한다");
  assert.ok(c.includes("아직 그룹이 없습니다"), "없을 때의 상태 표시");
  assert.ok(c.includes('const VTUBER_GROUP_NAME = "버튜버"'),
    "그룹 이름은 상수 하나로 둔다");
});

test("수집 영역이 좁은 폭에서 넘치지 않는다", () => {
  const c = code(PANEL());
  const i = c.indexOf("function VtuberCollectSection");
  const block = c.slice(i, c.indexOf("export default function StreamerTagsPanel"));
  assert.ok(block.includes("flex flex-wrap items-center gap-2"),
    "320px에서는 버튼이 아랫줄로 내려가야 한다");
  assert.ok(block.includes("whitespace-nowrap text-xs"), "버튼 글자는 쪼개지지 않는다");
  assert.ok(!block.includes("overflow-x-hidden"), "넘침을 감추지 않는다");
});

test("이 화면은 멤버를 지우는 동작을 새로 만들지 않는다", () => {
  const c = code(PANEL());
  const i = c.indexOf("function VtuberCollectSection");
  const block = c.slice(i, c.indexOf("export default function StreamerTagsPanel"));
  for (const forbidden of ["streamerTagUnassign", "method: \"DELETE\"", "unassign"]) {
    assert.ok(!block.includes(forbidden),
      `수집 영역은 추가 전용이다 (${forbidden} 발견)`);
  }
});

// ── 5. 라이브 신선도로 막힌 상태 (fail-closed) ───────────────────────────────

test("막힘(409)과 일시적 오류를 화면이 구분한다", () => {
  const c = code(PANEL());
  // 하나로 뭉치면 "잠시 후 다시 시도"만 반복하게 된다 — 수집기가 회복되기
  // 전까지는 몇 번을 눌러도 결과가 같다.
  assert.ok(c.includes("const [blocked, setBlocked]"), "막힘 상태를 따로 들어야 한다");
  assert.ok(c.includes('e.code !== "no_live_snapshot" && e.code !== "stale_live_snapshot"'),
    "서버 code로 판정한다(문구 매칭 금지)");
  assert.ok(c.includes("그룹과 멤버는 변경되지 않았습니다"),
    "DB write가 0이었다는 사실을 운영자에게 알려야 한다");
  // 막힘 분기 **자체**만 잘라서 본다. 창을 문자 수로 잡으면 바로 뒤의 일시적
  // 오류 분기까지 삼켜 늘 실패한다.
  const branch = c.slice(c.indexOf("{blocked ? ("), c.indexOf(") : err ? ("));
  assert.ok(branch.length > 0 && !branch.includes("잠시 후"),
    "막힘 상태에 '잠시 후 재시도'를 붙이지 않는다");
  assert.ok(c.includes("{err} 잠시 후 다시 시도해 주세요."),
    "일시적 오류 쪽은 재시도 안내를 유지한다");
});

test("막힘 상태에서는 직전 성공 결과를 남기지 않는다", () => {
  const c = code(PANEL());
  assert.ok(c.includes("setBusy(true); setErr(null); setBlocked(null);"),
    "실행 시작 때 두 오류 상태를 모두 비운다");
  assert.ok(c.includes("{!err && !blocked && res && res.errors.length > 0 && ("),
    "막혔는데 이전 실행의 부분 실패 목록이 남으면 안 된다");
  assert.ok(c.includes("{res && !err && !blocked && ("),
    "막혔는데 이전 실행의 수집 시각이 남으면 안 된다");
});

test("막힘 안내에 마지막 수집 시각을 KST로 보여 준다", () => {
  const c = code(PANEL());
  assert.ok(c.includes('timeZone: "Asia/Seoul"'), "이 패널의 다른 시각 표기와 같은 방식");
  assert.ok(c.includes("blocked.collectedAt != null"),
    "회차가 아예 없는 경우(null)에는 시각을 붙이지 않는다");
});

test("ApiError가 구조화 detail을 그대로 전달한다", () => {
  const c = code(API());
  // 새 예외 계층을 만들지 않고 기존 `{code, message}` 계약에 값만 얹는다.
  assert.ok(c.includes("detail: Record<string, unknown> | null;"), "detail 필드");
  assert.ok(c.includes("detail.code ?? null, detail)"),
    "객체 detail을 그대로 실어 보내야 화면이 collectedAt을 읽을 수 있다");
});

test("수집 결과 타입이 신선도 값을 포함한다", () => {
  const c = code(read("lib/types.ts"));
  const i = c.indexOf("interface VtuberCollectResult");
  const block = c.slice(i, c.indexOf("}", i));
  for (const f of ["collectedAt", "ageSeconds", "maxAgeSeconds"]) {
    assert.ok(block.includes(f), `VtuberCollectResult에 ${f}가 없다`);
  }
  assert.ok(c.includes("interface VtuberCollectBlocked"), "막힘 detail 타입");
  assert.ok(c.includes('"no_live_snapshot" | "stale_live_snapshot"'), "코드 두 가지");
});

// ── 6. relay 경로 계약 (대시보드 API는 relay를 타지 않는다) ──────────────────
//
// `app/api/**`의 relay는 **브라우저 확장** 때문에 있다. 확장은 `nexbot.shop`으로
// 보낼 수밖에 없어서(Railway 원본 주소 노출·확장 권한 확대를 피하려고) 그 경로만
// 프론트에 뚫어 뒀다. 대시보드는 사정이 다르다 — `lib/api.ts`가 `BASE`
// (`NEXT_PUBLIC_API_URL`)를 붙여 **Railway를 직접** 부르고, 인증은 쿠키가 아니라
// `Authorization` 헤더라 CORS(`*`)로 그대로 통과한다.
//
// 그래서 새 수집 API에는 relay 파일이 필요 없다. 이 묶음은 그 판단을 고정한다.

test("대시보드 admin 호출은 BASE를 붙여 백엔드로 직접 간다", () => {
  const c = code(API());
  assert.ok(c.includes('export const BASE = process.env.NEXT_PUBLIC_API_URL'),
    "절대 URL 기준이 유지돼야 한다");
  assert.ok(c.includes("await fetch(`${BASE}${path}`"), "request()는 BASE를 붙인다");
  assert.ok(c.includes('headers["Authorization"] = `Bearer ${token}`'),
    "인증은 쿠키가 아니라 헤더다 — 동일 오리진일 필요가 없다");
  // 새 함수도 같은 `request()`를 쓴다(별도 fetch 경로를 만들지 않았다).
  const i = c.indexOf("streamerTagsCollectVtuber");
  const block = c.slice(i, c.indexOf("}),", i) + 3);
  assert.ok(block.includes("request<"), "전용 fetch를 새로 만들지 않는다");
});

test("relay는 확장 전용 6경로뿐이고 수집 API를 포함하지 않는다", () => {
  const relay = read("lib/collectorRelay.ts");
  // 허용 목록은 `RELAY_SPECS`의 키가 전부다(`RelayKind` 두 별칭이 그 목록이다).
  for (const kind of ["ingest", "failure", "device/pair", "device/state",
                      "device/challenge", "device/token"]) {
    assert.ok(relay.includes(`"${kind}"`), `기존 relay 종류 ${kind}가 사라졌다`);
  }
  assert.ok(!/vtuber/i.test(relay), "수집 API를 relay 표에 넣지 않는다");
  assert.ok(!/streamer-tags/.test(relay), "소속 그룹 경로는 relay 대상이 아니다");
});

test("프론트에 수집 API용 route handler를 만들지 않는다", () => {
  // 있으면 같은 자원을 두 경로가 서빙하게 되고, OWNER JWT를 프론트가 중계하는
  // 새 신뢰 경계가 생긴다(기존 relay는 Authorization을 **넘기지 않는다**).
  const dir = join(ROOT, "app/api");
  const walk = (d: string): string[] =>
    readdirSync(d, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? walk(join(d, e.name)) : [join(d, e.name)]);
  // 경로 구분자를 정규화한다(Windows 백슬래시).
  const files = walk(dir).map((f) => f.split(String.fromCharCode(92)).join("/"));
  assert.equal(files.length, 6, `app/api route handler는 6개여야 한다: ${files}`);
  assert.ok(files.every((f) => f.includes("/piku/collector/")),
    "확장 수집 경로 외의 route handler가 생겼다");
  assert.ok(!files.some((f) => f.includes("streamer-tags") || f.includes("vtuber")),
    "수집 API용 프록시를 만들지 않는다");
});

test("범용 프록시(rewrite/catch-all)를 만들지 않는다", () => {
  const cfg = read("next.config.ts");
  assert.ok(!/rewrites|redirects\s*\(/.test(cfg), "next.config에 rewrite가 없어야 한다");
  assert.ok(cfg.includes("NEXT_PUBLIC_API_URL"), "백엔드 주소는 여전히 환경변수다");
});
