// SINGCUP-3 / PIKU — 기능 종료 표시 · 공식 참가자 화면 · PIKU 표시 계약(프론트).
//
// 백엔드 계약은 `tests/test_singcup_retirement.py`·`tests/test_singcup_piku.py`가 본다.
// 여기서 막는 것은 화면 쪽 구조가 조용히 원복되는 것이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");
const OFFICIAL = () => read("app/stats/SingcupOfficial.tsx");
const SINGCUP = () => read("app/stats/Singcup.tsx");
const LIVE = () => read("app/stats/singcup/live/page.tsx");
const PANEL = () => read("app/nexadmin/PikuPanel.tsx");

// ── 기능 종료 표시 ──────────────────────────────────────────────────────────

test("무엇이 열려 있는지 서버가 정한다", () => {
  for (const [name, s] of [["Singcup", SINGCUP()], ["live", LIVE()]] as const) {
    assert.ok(s.includes("api.singcup.gates()"),
      `${name}: 화면이 스스로 판단하면 서버에서 다시 열어도 화면만 닫힌 채 남는다`);
  }
});

test("종료 문구는 서버가 준 것을 쓴다", () => {
  assert.ok(SINGCUP().includes("gates?.notices?.unofficialRanking"));
  assert.ok(LIVE().includes("gates?.notices?.live"));
});

test("상태를 모르는 동안 종료로 단정하지 않는다", () => {
  // 종료 화면을 먼저 보였다가 목록이 나타나면 화면이 깨진 것으로 읽힌다.
  assert.ok(LIVE().includes("gatesLoaded"), "라이브 화면에 로딩 단계가 있어야 한다");
  assert.ok(/!gatesLoaded \?/.test(LIVE()));
});

test("종료 화면이 빈 화면이나 오류로 보이지 않는다", () => {
  // **UI-V에서 랭킹 쪽 계약이 바뀌었다.** 예전에는 게이트가 닫히면 랭킹을
  // 안내문 한 장(`RankingRetired`)으로 바꿨는데, 그러면 확정까지 마친 순위를
  // 볼 방법이 사라진다(데이터는 서버에 그대로 있었다). 지금은 화면을 열어 두고
  // 확정본을 읽기 전용으로 보여 주며, '수집 종료'는 배너로 알린다.
  const s = SINGCUP();
  assert.ok(!s.includes("function RankingRetired"),
    "안내문 한 장으로 대체하지 않는다");
  assert.ok(s.includes("수집 종료 · 최종 집계"), "무엇이 끝났는지 밝힌다");
  assert.ok(s.includes("기록은 지우지 않고"), "데이터가 남아 있다는 것을 알린다");
  assert.ok(s.includes("공식 예선 참가자"), "갈 곳을 준다");
  // 라이브 화면은 그대로다 — 실시간 목록은 되살릴 데이터 자체가 없다.
  const l = LIVE();
  assert.ok(l.includes("function LiveRetired"));
  assert.ok(l.includes("공식 예선 참가자 보기"));
});

test("종료된 라이브 화면은 데이터 API를 부르지 않는다", () => {
  // 종료된 화면이 참가자 전원(약 850KB)을 계속 받아 오면 비용만 나간다.
  const s = LIVE();
  assert.ok(/useSingcupMain\(\{ enabled: liveOpen \}\)/.test(s));
  const hook = read("lib/useSingcupMain.ts");
  assert.ok(hook.includes("if (!enabled) return;"), "요청 자체를 만들지 않는다");
  assert.ok(hook.includes("if (!enabled) {"), "타이머도 만들지 않는다");
});

test("확정본 기록으로 가는 길이 남아 있다", () => {
  // 데이터를 지운 것이 아니라 수집을 멈춘 것이다. 그래서 (1) 공식 화면에서
  // 랭킹으로 가는 버튼이 있고, (2) 랭킹 화면이 확정본을 그대로 그린다.
  const s = SINGCUP();
  assert.ok(s.includes("<SingcupRanking"), "랭킹 화면이 항상 렌더된다");
  assert.ok(s.includes("마지막 정상 집계 기준"));
  assert.ok(read("app/stats/SingcupOfficial.tsx").includes("비공식 인기점수 보기"),
    "공식 화면에 진입 경로가 있어야 한다");
});

// ── 공식 예선 참가자 화면 ───────────────────────────────────────────────────

test("부문이 셋으로 나뉜다", () => {
  const s = OFFICIAL();
  assert.ok(/DIVISIONS = \["female_solo", "male_solo", "groups"\]/.test(s));
  for (const l of ["여성 솔로", "남성 솔로", "그룹"]) assert.ok(s.includes(l));
});

test("전체 화면은 부문마다 10명·10명·10팀을 세로 섹션으로 보여 준다", () => {
  const s = OFFICIAL();
  assert.ok(/OVERVIEW_ROWS = 10/.test(s));
  assert.ok(s.includes("<DivisionSection"), "부문별 섹션 컴포넌트가 있어야 한다");
  assert.ok(/tab === "all" \? DIVISIONS : \[tab as Division\]/.test(s),
    "전체에서는 세 부문을 모두 세로로 쌓는다");
});

test("부문마다 TOP 1~5 독립 카드가 있다", () => {
  const s = OFFICIAL();
  assert.ok(/TOP_CARDS = 5/.test(s));
  assert.ok(s.includes("function TopCard"));
  assert.ok(s.includes("ordered.slice(0, TOP_CARDS)"));
});

test("클립 썸네일을 쓴다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("clipThumbnailUrl"));
  // 썸네일이 없을 때 빈 검은 박스를 두지 않는다
  assert.ok(s.includes("클립 없음"));
});

test("LIVE 배지는 공식 참가자 카드 전용 클래스를 쓴다", () => {
  assert.ok(OFFICIAL().includes("nb-live-badge"));
  // 다른 화면으로 번지지 않았는지는 uiPolish.test.ts가 본다
});

test("공식 결과와 재계산 순위를 문구로 구분한다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("치지직이 공식 공지로 발표한 예선 참가자 명단입니다."));
  assert.ok(s.includes("공식 심사 결과나 순위가 아닙니다."));
  assert.ok(s.includes("다시 계산한 순서"));
  assert.ok(s.includes("공식 순위 아님"), "섹션마다 순서의 출처를 밝힌다");
  assert.ok(s.includes("순위 아님"), "PIKU 데이터가 없을 때도 밝힌다");
});

test("순위는 서버가 매긴 것을 그대로 쓴다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("프런트에서 순위를 다시 매기지 않는다"),
    "두 곳에서 계산하면 동점 규칙이 갈라진다");
  assert.ok(!/\.sort\(\(a, b\) => b\.(win|match)/.test(s));
});

// ── PIKU 표시 계약 ──────────────────────────────────────────────────────────

/** 주석을 제거한 코드만 남긴다 — 주석에 단어가 나온다고 화면에 보이는 것은 아니다. */
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

test("화면에 우승 비율·승률 값을 그리지 않는다", () => {
  for (const [name, s] of [["공개", code(OFFICIAL())],
                           ["관리", code(PANEL())]] as const) {
    // 값을 렌더에 꽂는 표현이 없어야 한다: `{x.winRate}` · `{r.win_rate}` 등.
    assert.ok(!/\{[^{}]*\.(winRate|matchRate|win_rate|match_rate)[^{}]*\}/.test(s),
      `${name}: 비율 값을 렌더에 넣었다`);
    // 타입에서 꺼내 쓰는 흔적도 없어야 한다.
    assert.ok(!/(const|let)\s+\w+\s*=\s*[^;]*\.(winRate|matchRate)\b/.test(s),
      `${name}: 비율 값을 변수로 꺼냈다`);
  }
  // 관리 화면의 `winRate,matchRate`는 **CSV 헤더 형식 안내 문자열**이다.
  // 값이 아니라 "이 이름으로 열을 달라"는 설명이므로 허용한다.
  const panel = code(PANEL());
  const csvHint = (panel.match(/name,winRate,matchRate/g) ?? []).length;
  assert.equal(csvHint, 2, "CSV 형식 안내(헤더 설명 + placeholder) 외에는 없어야 한다");
  assert.equal((panel.match(/winRate/g) ?? []).length, csvHint);
});

test("조회수·하트를 표시하지 않는다", () => {
  const s = OFFICIAL();
  for (const bad of ["viewCount", "heartCount", "Heart", "Eye"]) {
    assert.ok(!s.includes(bad), `${bad}가 공식 참가자 화면에 있다`);
  }
});

test("공개 정렬 토큰에 내부 컬럼명이 없다", () => {
  // "키 이름·숫자·직렬화 문자열 어느 형태로도" 노출 금지 계약.
  const s = OFFICIAL();
  for (const bad of ["win_rate", "match_rate", "winRate", "matchRate"]) {
    const rendered = code(s).replace(/\/\*[\s\S]*?\*\//g, "");
    assert.ok(!rendered.includes(bad), `프론트에 내부 컬럼명이 있다: ${bad}`);
  }
  assert.ok(s.includes('useState("primary")'), "공개 토큰을 쓴다");
  assert.ok(s.includes('key: "secondary"'));
});

test("정렬 버튼이 둘이고 서버가 기준을 준다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("piku?.sortOptions"), "선택지는 서버가 정한다");
  assert.ok(s.includes("우승 비율순") && s.includes("승률순"), "폴백 라벨");
  assert.ok(s.includes("api.singcup.pikuRanking(sort)"),
    "정렬을 바꾸면 서버에 다시 물어 1위부터 다시 계산된다");
});

test("현재 정렬 기준을 색이 아니라 글자로도 밝힌다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes('role="status"'));
  assert.ok(s.includes("으로 정렬했습니다"));
  assert.ok(s.includes("비율·승률 수치는 표시하지 않습니다"));
  assert.ok(s.includes("aria-pressed"), "버튼 상태를 보조기기에 알린다");
});

test("PIKU 순위 실패가 명단을 가리지 않는다", () => {
  const s = OFFICIAL();
  // 명단과 순위의 상태를 나눠 둔다 — 순위는 부가 정보다.
  assert.ok(s.includes("PIKU 순위는 **부가 정보**다"));
  assert.ok(/\.catch\(\(\) => \{ if \(alive\) setPiku\(null\); \}\)/.test(s));
});

test("출처와 마지막 정상 갱신 시각을 표시한다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("lastSuccessAt"));
  assert.ok(s.includes("아직 수집된 데이터 없음"), "없을 때도 상태를 밝힌다");
  assert.ok(s.includes("sourceUrl"), "PIKU 출처 링크");
  assert.ok(s.includes('rel="noopener noreferrer nofollow"'));
});

// ── 관리 화면 ───────────────────────────────────────────────────────────────

test("부문 매핑을 관리자가 직접 지정한다", () => {
  const s = PANEL();
  assert.ok(s.includes("부문별 PIKU 주소"));
  assert.ok(s.includes("추측하지 않습니다"));
  assert.ok(s.includes("api.admin.pikuSetSources"));
});

test("수동 갱신과 수동 import 경로가 모두 있다", () => {
  const s = PANEL();
  assert.ok(s.includes("api.admin.pikuCollect"), "수동 갱신");
  assert.ok(s.includes("api.admin.pikuImport"), "JSON/CSV import");
  assert.ok(s.includes("PIKU에 접속하지 않고"), "import는 접속 없는 대체 경로다");
});

test("자동 수집이 꺼져 있음을 화면에서 알 수 있다", () => {
  const s = PANEL();
  assert.ok(s.includes("autoCollectEnabled"));
  assert.ok(s.includes("PIKU_AUTO_COLLECT_ENABLED=true"), "켜는 방법을 적는다");
  assert.ok(s.includes("기본값은 꺼짐입니다"));
});

test("매핑은 후보 목록에서 고른다(직접 입력이 아니다)", () => {
  const s = PANEL();
  assert.ok(s.includes("maps?.candidates"), "채널 id를 손으로 입력하면 오타가 난다");
  assert.ok(s.includes("자동으로 확정하지 않습니다"));
  assert.ok(s.includes("이름 일치 후보"), "제안과 확정을 구분한다");
});

test("실패 종류를 구분해 보여 주되 우회 방법을 알려 주지 않는다", () => {
  const s = PANEL();
  assert.ok(s.includes("접근 거부(403)"));
  assert.ok(s.includes("Cloudflare 확인 화면"));
  // 주석은 제외한다 — "우회 방법은 알려 주지 않는다"는 설명이지 안내가 아니다.
  const rendered = code(s);
  for (const bad of ["프록시", "우회", "User-Agent 변경", "VPN"]) {
    assert.ok(!rendered.includes(bad), `우회 안내가 화면에 있다: ${bad}`);
  }
});

test("실패가 기존 데이터를 덮지 않는다는 것을 화면에 적는다", () => {
  assert.ok(PANEL().includes("기존 데이터를 덮지 않습니다"));
  assert.ok(PANEL().includes("응답 원문은 저장하지 않습니다"));
});

test("시도와 성공 시각을 나눠 보여 준다", () => {
  const s = PANEL();
  assert.ok(s.includes("마지막 성공") && s.includes("마지막 시도"));
});
