// 인증 대시보드 QA(UI-R)에서 **실측으로 재현한** 결함이 조용히 되돌아가지 않게 고정한다.
//
// ⚠️ 한계: 소스 텍스트 대조라 렌더 결과를 증명하지 못한다. 각 항목의 실제 재현은
// headless Chrome + 격리 모의 백엔드로 따로 했고(기준본에서 전부 재현됨), 여기서
// 막으려는 것은 리팩터링 중 원복이다. 브라우저 QA를 이 파일로 대체하지 말 것.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

const GENERAL = "app/dashboard/[guildId]/page.tsx";
const MODERATION = "app/dashboard/[guildId]/moderation/page.tsx";
const GUILD_LIST = "app/dashboard/page.tsx";
const SIDEBAR = "components/Sidebar.tsx";
const NAVBAR = "components/Navbar.tsx";
const LOGIN = "app/login/page.tsx";
const CALLBACK = "app/callback/page.tsx";

// ── R-1: 저장 실패가 "저장됨"으로 보이던 문제 ────────────────────────────────
// 재현(기준본): 모의 백엔드가 PUT /api/settings/{gid} 에 500을 줘도 버튼이 "저장됨"으로
// 바뀌고 화면 어디에도 오류 표시가 없었다.
for (const page of [GENERAL, MODERATION]) {
  test(`${page}: 저장 실패를 성공으로 표시하지 않는다`, () => {
    const s = read(page);
    assert.ok(!/settings\.save\([^)]*\)\.catch\(\(\)\s*=>\s*\{\}\)/.test(s),
      "저장 실패를 빈 catch로 삼킨다 — 사용자에게 성공처럼 보인다");
    // 성공 표시는 공통 계약이 2xx로 resolve된 뒤에만 켜는 saveM.succeeded 하나뿐이다.
    // (수동 try/catch는 중복 클릭을 막지 못했다 — 실측: 연타 3회 → 요청 3건)
    assert.ok(/const save = \(\) => saveM\.run\(/.test(s),
      "저장이 공통 계약(useMutation)을 거치지 않는다");
    assert.ok(/saveM\.succeeded\s*\?/.test(s), "성공 표시가 계약 상태와 묶여 있지 않다");
    assert.ok(!/setSaved\(true\)/.test(s), "수동 성공 플래그가 남아 있다");
  });
}

// ── R-2: 403/500 로드 실패에 아무 화면도 없던 문제 ───────────────────────────
// 재현(기준본): 권한 없는 길드 id로 직접 들어가면 API가 403인데도 설정 폼이 그대로
// 그려지고 저장 버튼까지 눌린다.
test("일반 설정: 불러오기 실패에 오류 화면이 있다", () => {
  const s = read(GENERAL);
  assert.ok(/dashboardErrorCopy|DashboardError/.test(s),
    "공통 오류 분류를 쓰지 않는다");
  assert.ok(/\.catch\(\s*\(?e/.test(s) || /catch\s*\(e/.test(s),
    "불러오기 실패를 잡지 않는다(처리되지 않은 거부)");
});

test("권한 없음일 때 설정 폼과 저장 버튼을 그리지 않는다", () => {
  const s = read(GENERAL);
  // 오류 화면을 먼저 return 해야 한다. 폼을 남겨 두면 저장을 눌러 볼 수 있다.
  const guard = s.indexOf("loadError");
  assert.ok(guard > -1, "불러오기 오류 상태가 없다");
  assert.ok(/if \(loadError\)[\s\S]{0,200}return/.test(s),
    "오류일 때 조기 return으로 폼을 대체하지 않는다");
});

// ── R-3: 길드 목록 로드 실패가 빈 상태와 구분되지 않던 문제 ──────────────────
// 재현(기준본): GET /api/guilds 가 500이어도 "관리 권한이 있는 서버가 없습니다."가 떴다.
test("길드 목록: 로드 실패와 0건이 서로 다른 화면이다", () => {
  const s = read(GUILD_LIST);
  assert.ok(!/guilds\.list\(\)\.catch\(\(\)\s*=>\s*\[\]\)/.test(s),
    "실패를 빈 배열로 바꿔치기해 빈 상태와 구분이 사라진다");
  assert.ok(/listError/.test(s), "목록 오류 상태가 없다");
  assert.ok(s.indexOf("관리 권한이 있는 서버가 없습니다") > -1, "빈 상태 문구가 사라졌다");
});

test("길드 목록: 빈 상태가 봇 초대 경로를 제시한다", () => {
  const s = read(GUILD_LIST);
  // 주석이 아니라 화면에 그려지는 자리를 본다.
  const i = s.indexOf("<p>관리 권한이 있는 서버가 없습니다");
  assert.ok(i > -1, "빈 상태 문구를 화면에서 찾지 못했다");
  const block = s.slice(i, i + 900);
  assert.ok(/getBotInviteUrl\(\)/.test(block),
    "서버가 0개일 때 다음에 할 일(봇 초대)이 없다");
});

// ── R-4·R-5: 로그인 경로 ─────────────────────────────────────────────────────
// 재현(기준본): /api/auth/login 이 url 없는 200을 주면 /undefined 로 이동해 404가 났다.
test("로그인: url이 없으면 이동하지 않고 오류로 처리한다", () => {
  const s = read(LOGIN);
  assert.ok(!/\.then\(\(d\) => \{ window\.location\.href = d\.url; \}\)/.test(s),
    "응답 검증 없이 이동한다 — url이 없으면 /undefined 로 간다");
  assert.ok(/typeof d\?\.url === "string"|typeof d\.url === "string"/.test(s),
    "url이 문자열인지 확인하지 않는다");
});

test("로그인·콜백 오류 화면에 운영자용 내부 정보가 없다", () => {
  // 재현(기준본): 콜백 실패 화면이 uvicorn 명령, http://localhost:8000/api/auth/callback,
  // .env DISCORD_CLIENT_SECRET 을 최종 사용자에게 그대로 보여 줬다.
  const forbidden = [
    /uvicorn/i, /localhost:\d+/, /NEXT_PUBLIC_API_URL/, /DISCORD_CLIENT_SECRET/,
    /DISCORD_REDIRECT_URI/, /\.env/, /developers\.discord/i,
  ];
  for (const page of [LOGIN, CALLBACK]) {
    // 주석은 제외한다 — 왜 이렇게 됐는지 설명하는 문장은 화면에 나가지 않고,
    // 지우면 다음 사람이 같은 실수를 되풀이한다.
    const s = read(page)
      .split("\n")
      .filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line))
      .join("\n");
    for (const re of forbidden) {
      assert.ok(!re.test(s), `${page} 에 ${re} 가 남아 있다`);
    }
  }
});

test("콜백: 서버 원문 메시지를 화면에 그대로 싣지 않는다", () => {
  const s = read(CALLBACK);
  assert.ok(!/setErrMsg\(e\.message/.test(s),
    "백엔드 detail('internal' 등)이 사용자 화면에 그대로 나온다");
});

test("로그인 오류 화면에 재시도 경로가 있다", () => {
  const s = read(LOGIN);
  assert.ok(/다시 시도/.test(s), "재시도 없이 막다른 화면이다");
});

// ── R-6·R-7: 길드 전환 드롭다운 ──────────────────────────────────────────────
test("드롭다운: 실패·0건을 '불러오는 중'으로 표시하지 않는다", () => {
  const s = read(SIDEBAR);
  const i = s.indexOf("managed.length === 0");
  assert.ok(i > -1, "드롭다운 빈 목록 분기가 사라졌다");
  assert.ok(!/managed\.length === 0 \? \(\s*<p[^>]*>\s*불러오는 중/.test(s),
    "로드 실패·0건이 영원히 '불러오는 중...'으로 남는다");
  assert.ok(/guildsState|switcherState/.test(s),
    "로딩·오류·완료를 구분하는 상태가 없다");
});

test("드롭다운: ARIA와 ESC 계약", () => {
  const s = read(SIDEBAR);
  // 재현(기준본): aria-expanded·aria-haspopup·role 이 전부 없고 ESC로 닫히지 않았다.
  assert.ok(/aria-expanded=\{open\}/.test(s), "aria-expanded가 없다");
  assert.ok(/aria-haspopup/.test(s), "aria-haspopup이 없다");
  assert.ok(/role="menu"/.test(s), "메뉴 role이 없다");
  assert.ok(/"Escape"/.test(s), "ESC로 닫히지 않는다");
  assert.ok(/\.focus\(\)/.test(s), "닫은 뒤 포커스가 트리거로 돌아오지 않는다");
});

// ── R-8: 아이콘 전용 버튼의 접근 가능한 이름 ─────────────────────────────────
test("아이콘만 있는 버튼에 접근 가능한 이름이 있다", () => {
  // title 속성은 접근 가능한 이름으로 신뢰할 수 없다(실측: 이름 없는 컨트롤로 잡혔다).
  for (const [page, label] of [[NAVBAR, "로그아웃"], [GUILD_LIST, "새로고침"]] as const) {
    const s = read(page);
    assert.ok(new RegExp(`aria-label="[^"]*${label}[^"]*"`).test(s),
      `${page} 의 ${label} 버튼에 aria-label이 없다`);
    // 실측: 두 버튼 모두 35×35px 이었다(44px 계약 위반).
    assert.ok(/min-w-\[44px\] min-h-\[44px\]/.test(s),
      `${page} 의 아이콘 버튼이 44px 계약을 지키지 않는다`);
  }
});

// ── R-10: 모바일 하단 내비의 터치 타겟 ───────────────────────────────────────
test("모바일 하단 내비 항목이 44px 아래로 눌리지 않는다", () => {
  const s = read(SIDEBAR);
  // 재현(기준본): 390px에서 12개 항목이 각각 32px 폭이었다(44px 계약 위반).
  // 12 × 44 = 528px 이라 390px 안에 다 넣을 수 없다 — 가로 스크롤이 정답이고,
  // overflow를 hidden으로 덮어 결함을 감추는 것은 금지다.
  assert.ok(/min-w-\[44px\]/.test(s), "항목 최소 폭 계약이 없다");
  assert.ok(/overflow-x-auto/.test(s), "넘치는 항목에 접근할 방법이 없다");
  assert.ok(!/overflow-x-hidden/.test(s), "overflow를 숨겨 결함을 감춘다");
});

// ── 멤버 검색 드롭다운(§4) ──────────────────────────────────────────────────
test("멤버 검색은 로딩·빈 결과·실패를 구분한다", () => {
  const s = read("components/MemberSearch.tsx");
  assert.ok(!/\.catch\(\(\) => setResults\(\[\]\)\)/.test(s),
    "검색 실패가 '결과 없음'과 구분되지 않는다");
  assert.ok(/검색 결과가 없습니다/.test(s), "빈 결과 문구가 없다");
  assert.ok(/검색하지 못했습니다/.test(s), "실패 문구가 없다");
  assert.ok(/"Escape"/.test(s), "ESC로 닫히지 않는다");
  assert.ok(/min-h-\[44px\]/.test(s), "결과 항목이 44px 터치 영역을 지키지 않는다");
});
