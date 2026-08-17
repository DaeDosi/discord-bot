// NAV-STATS — 통계 메뉴 분리·명칭 변경·소형 랭킹·봉누도·중복 안내 제거 계약.
//
// 소스 텍스트를 읽는 이유는 이 저장소의 다른 프론트 테스트와 같다
// (`node --test lib/*.test.ts`, 러너·DOM 라이브러리를 새로 들이지 않는다).
// 여기서 막으려는 것은 "리팩터링하다 조용히 원복되는 것"이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");
const NAV = () => read("app/stats/StatsNav.tsx");
const PAGE = () => read("app/stats/page.tsx");

// ── 명칭 ────────────────────────────────────────────────────────────────────

test("'전체 스트리머 분석'이 '전체 스트리머 통계'로 바뀐다", () => {
  const s = NAV();
  assert.ok(s.includes('label: "전체 스트리머 통계"'));
  assert.ok(!s.includes('label: "전체 스트리머 분석"'));
});

test("통합 메뉴가 두 독립 메뉴로 갈린다", () => {
  const s = NAV();
  assert.ok(s.includes('label: "신규 스트리머 통계"'));
  assert.ok(s.includes('label: "소형 스트리머 통계"'));
  assert.ok(!s.includes('label: "신규 & 초기 분석"'),
    "통합 메뉴가 남아 있으면 URL 공유와 뒤로가기가 예전 문제로 되돌아간다");
});

test("두 메뉴는 서로 다른 탭 키를 갖는다", () => {
  const s = NAV();
  assert.ok(/"newcomers_stats"/.test(s) && /"small_stats"/.test(s));
  assert.ok(/key: "newcomers_stats"/.test(s) && /key: "small_stats"/.test(s));
});

test("아이콘이 서로 다르다(나란히 놓이므로 구분돼야 한다)", () => {
  const s = NAV();
  const nc = s.split('key: "newcomers_stats"')[1].split("},")[0];
  const sm = s.split('key: "small_stats"')[1].split("},")[0];
  assert.ok(nc.includes("<Sprout"), "신규는 Sprout");
  assert.ok(sm.includes("<Seedling"), "소형은 다른 아이콘");
});

// ── 그룹 정의는 바뀌지 않았다 ───────────────────────────────────────────────

test("신규·소형의 정의를 바꾸지 않는다", () => {
  const s = PAGE();
  // 신규 = 첫 방송 60일 이내(시청자 규모 무관), 소형 = 최근 평균 10명 이하(경력 무관)
  assert.ok(s.includes("debut_max_days"), "신규 기준은 서버 값을 쓴다");
  assert.ok(s.includes("small_avg_max"), "소형 기준도 서버 값을 쓴다");
  assert.ok(s.includes("방송 경력과 무관하게"), "소형 정의 문구 유지");
  assert.ok(s.includes("첫 방송 후"), "신규 정의 문구 유지");
});

test("두 그룹의 교집합이 정상임을 화면에서 밝힌다", () => {
  const s = PAGE();
  assert.ok(s.includes("두 목록에 함께 나오는 채널이 있을 수 있습니다"),
    "메뉴가 갈리면서 '둘 중 하나'로 읽힐 여지가 생겼다");
});

// ── 상태 분리 ───────────────────────────────────────────────────────────────

test("탭마다 로딩·오류·빈 데이터 상태가 따로 산다", () => {
  const s = PAGE();
  const tab = s.split("function NewcomerStatsTab")[1].split("\nfunction ")[0];
  assert.ok(tab.includes("const [err, setErr]"), "오류 상태가 탭 안에 있어야 한다");
  assert.ok(tab.includes("role=\"alert\""), "오류는 오류로 보여 준다");
  assert.ok(tab.includes("aria-busy"), "로딩을 보조기기에도 알린다");
  assert.ok(tab.includes("지금 조건에 해당하는 방송이 없습니다"),
    "빈 데이터와 로딩을 구분한다");
});

test("두 통계 탭은 key로 재마운트돼 상태가 섞이지 않는다", () => {
  const s = PAGE();
  assert.ok(/<NewcomerStatsTab key="new"/.test(s));
  assert.ok(/<NewcomerStatsTab key="small"/.test(s));
});

test("세그먼티드 토글은 제거됐다(같은 일을 두 방식으로 하지 않는다)", () => {
  const s = PAGE();
  assert.ok(!s.includes("function NcGroupToggle"),
    "메뉴와 토글이 공존하면 URL과 화면이 어긋난다");
  assert.ok(!s.includes("NC_GROUP_TABS"));
});

// ── URL · 뒤로가기 · 북마크 호환 ────────────────────────────────────────────

test("옛 탭 키는 새 키로 안전하게 넘어간다", () => {
  const s = NAV();
  assert.ok(s.includes("LEGACY_TAB_ALIASES"));
  assert.ok(/newcomers_analysis: "newcomers_stats"/.test(s),
    "옛 링크를 버리면 조용히 첫 탭으로 떨어진다");
  assert.ok(s.includes("export const resolveTab"));
});

test("탭 전환은 pushState라 뒤로가기가 통계 안에서 동작한다", () => {
  const s = PAGE();
  assert.ok(s.includes("window.history.pushState"),
    "replaceState만 쓰면 뒤로가기가 /stats를 통째로 벗어난다");
  assert.ok(s.includes('window.addEventListener("popstate"'),
    "뒤로가기로 돌아온 탭을 화면에 반영해야 한다");
  assert.ok(/if \(k === tab\) return;/.test(s),
    "같은 탭을 다시 눌러 히스토리를 늘리지 않는다");
});

test("직접 URL 접근이 옛 키도 새 키로 정리한다", () => {
  const s = PAGE();
  assert.ok(s.includes("resolveTab(raw)"));
  assert.ok(/if \(raw !== t\)/.test(s), "주소도 새 키로 바꿔 둔다");
});

// ── 소형 스트리머 랭킹 ──────────────────────────────────────────────────────

test("소형 스트리머 랭킹 메뉴가 랭킹 그룹에 있다", () => {
  const s = NAV();
  const rank = s.split('header: "랭킹"')[1].split("] }")[0];
  assert.ok(rank.includes('key: "small_ranking"'));
  assert.ok(rank.includes('label: "소형 스트리머 랭킹"'));
});

test("소형 랭킹은 전용 API를 쓴다(통계 응답을 재활용하지 않는다)", () => {
  assert.ok(read("lib/api.ts").includes("smallRanking:"));
  assert.ok(read("lib/api.ts").includes("/api/rising/small-ranking"));
  const s = PAGE();
  assert.ok(s.includes("api.rising.smallRanking("),
    "통계 응답을 재활용하면 제외 정책이 적용되지 않은 목록이 랭킹에 나온다");
});

test("소형 랭킹이 통계와 다른 이유를 화면에 적는다", () => {
  const s = PAGE();
  const tab = s.split("function SmallRankingTab")[1].split("\nfunction ")[0];
  assert.ok(tab.includes("공식 그룹 채널이 제외됩니다"));
  assert.ok(tab.includes("현재 시청자 순"), "정렬 기준을 밝힌다");
});

test("소형 랭킹 기준값은 서버가 준다", () => {
  const s = PAGE();
  const tab = s.split("function SmallRankingTab")[1].split("\nfunction ")[0];
  assert.ok(tab.includes("data?.criteria?.small_avg_max"),
    "프론트에 숫자를 복사해 두면 설명만 옛날 값이 된다");
  assert.ok(tab.includes("data?.criteria?.window_days"));
});

test("소형 랭킹도 로딩·오류·빈 데이터를 구분한다", () => {
  const s = PAGE();
  const tab = s.split("function SmallRankingTab")[1].split("\nfunction ")[0];
  assert.ok(tab.includes('role="alert"'));
  assert.ok(tab.includes("aria-busy"));
  assert.ok(tab.includes("지금 방송 중인 소형 스트리머가 없습니다"));
});

// ── 봉누도 (준비 중) ────────────────────────────────────────────────────────

test("봉누도 메뉴가 싱드컵 위에 있다", () => {
  const s = NAV();
  assert.ok(s.includes("UPCOMING_ITEM"));
  assert.ok(/label: "봉누도"/.test(s));
  const block = s.split("<div className=\"mb-1 flex flex-col gap-1\">")[1].split("</div>")[0];
  assert.ok(block.indexOf("UPCOMING_ITEM") < block.indexOf("EVENT_ITEM"),
    "봉누도가 싱드컵보다 위에 렌더돼야 한다");
});

test("봉누도는 준비 중임을 누르기 전에 알린다", () => {
  const s = NAV();
  assert.ok(s.includes("upcoming?: boolean"));
  assert.ok(s.includes("준비 중"), "배지가 없으면 눌러 보고 나서야 알게 된다");
});

test("봉누도 화면은 데이터 API를 부르지 않는다", () => {
  const s = PAGE();
  const tab = s.split("function BongnudoTab")[1].split("\nfunction ")[0];
  assert.ok(!tab.includes("api."), "준비 중 화면이 API를 부르면 안 된다");
  assert.ok(!tab.includes("useEffect"), "데이터 로딩 경로가 있으면 안 된다");
  assert.ok(tab.includes("아직 공개하지 않은 메뉴입니다"),
    "빈 화면이나 오류처럼 보이지 않아야 한다");
  // 서비스가 시작된 것처럼 오인시키는 수치·순위·표를 두지 않는다
  assert.ok(!/<table/.test(tab) && !/순위/.test(tab));
});

// ── 중복 통계 안내 제거 ─────────────────────────────────────────────────────

test("하단 '치지직 통계 안내' 카드가 제거된다", () => {
  const s = PAGE();
  assert.ok(!s.includes('<h2 className="text-base font-bold text-fg">치지직 통계 안내</h2>'),
    "왼쪽 내비와 같은 곳으로 가는 두 번째 진입점이었다");
  // 카드 안의 버튼도 함께 사라진다(주석의 설명 문구는 코드가 아니다).
  const about = s.split("function StatsAbout")[1].split("\n// ")[0];
  assert.ok(!about.includes("통계 안내 보기"), "중복 버튼도 함께 사라져야 한다");
  assert.ok(!about.includes("btn-secondary"), "버튼이 아니라 각주 링크여야 한다");
});

test("스트리머 상세의 안내는 유지한다(중복이 아니다)", () => {
  // 같은 기준을 적용한 결과다: `/stats`의 카드는 **왼쪽 내비에 같은 진입점이
  // 이미 있어서** 중복이었다. 스트리머 상세에는 왼쪽 내비가 없어 이 카드가
  // 그 페이지의 유일한 진입점이고, 내용도 그 페이지 지표(잔디·시청 시간)에 대한
  // 다른 글이다. 지우면 안내로 갈 길이 사라진다.
  const s = read("app/stats/streamer/[channelId]/layout.tsx");
  assert.ok(s.includes("이 수치는 어떻게 계산되나"));
  assert.ok(s.includes('href="/stats/guide"'));
  assert.ok(s.includes("제휴 관계가 없습니다"), "법적 고지도 함께 유지된다");
});

test("왼쪽 내비의 통계 안내 진입점은 유지된다", () => {
  const s = NAV();
  assert.ok(s.includes('href="/stats/guide"'));
  assert.ok(s.includes("통계 안내"));
});

test("법적·측정상 고지는 지우지 않는다", () => {
  const s = PAGE();
  assert.ok(s.includes("네이버 및 치지직과"), "제휴 관계 고지 유지");
  assert.ok(s.includes("비공식 서비스"));
  assert.ok(s.includes("실제 치지직 화면과 값이 다를 수 있습니다"), "측정 주의 문구 유지");
  // 크롤러가 읽을 본문은 loading/error 분기 밖에 남아야 한다
  assert.ok(s.includes("<StatsAbout />"));
});

test("카드를 걷어낸 자리에 끊긴 경계선이 남지 않는다", () => {
  const s = PAGE();
  const about = s.split("function StatsAbout")[1].split("\n// ")[0];
  assert.ok(!about.includes("rounded-xl border border-border"),
    "카드 테두리가 남으면 마지막 카드와 이중 경계가 된다");
  assert.ok(about.includes("border-t border-border/60"), "각주용 구분선 하나만 둔다");
});

test("안내 페이지 자체는 유지되고 갈라진 메뉴를 반영한다", () => {
  const g = read("app/stats/guide/page.tsx");
  assert.ok(g.includes("신규 스트리머 통계"));
  assert.ok(g.includes("소형 스트리머 통계"));
  assert.ok(!g.includes("신규 &amp; 초기 분석"), "없어진 메뉴 이름이 남아 있으면 안 된다");
});
