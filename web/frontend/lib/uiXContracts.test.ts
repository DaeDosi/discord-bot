/* UI-X 계약 — 그룹 멤버 표시 + 랭킹 행 클릭 구조.
 *
 * 행에는 링크가 **둘**이고 서로 형제여야 한다. 프로필(아바타+이름)은 치지직 채널로,
 * 나머지 넓은 영역은 대표 클립으로 간다. 예전에는 이름·곡 전체가 프로필 링크였고
 * 오른쪽에 클립 아이콘이 따로 있었다.
 *
 * 여기서 고정하는 것은 **구조와 접근성 계약**이다. 실제 href·클릭 분리·키보드 이동은
 * 브라우저 QA가 확인한다(소스 검사만으로는 중첩 여부를 증명할 수 없다).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const OFFICIAL = () =>
  readFileSync(new URL("../app/stats/SingcupOfficial.tsx", import.meta.url), "utf8");
const MERGE = () =>
  readFileSync(new URL("./singcupOfficialMerge.ts", import.meta.url), "utf8");

// ── 이름 줄 (PUBLIC-UX-1이 UI-X의 '멤버 줄'을 대체) ─────────────────────────
// 예전: 대표자만 굵게 + 회색 `멤버 …` 보조 줄(곡 줄과 같은 위계로 섞였다).
// 지금: **대표자 먼저, 팀원 전원 이어서, 전부 굵게** 한 줄 → 그 아래 회색 곡 줄.
test("이름 줄은 공용 teamNames 하나만 쓴다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("function NameLine"), "NameLine 컴포넌트가 없다");
  assert.ok(s.includes("teamNames(item)"), "teamNames를 쓰지 않는다");
  // 문자열 조립을 화면에서 다시 하지 않는다.
  assert.ok(!/memberNames\s*\.\s*join/.test(s),
    "화면에서 멤버 문자열을 다시 조립한다 — teamNames를 쓸 것");
  assert.ok(!s.includes("function MemberLine"), "옛 멤버 보조 줄이 남아 있다");
});

test("카드와 목록이 같은 NameLine을 쓰고 목록만 줄바꿈한다", () => {
  const s = OFFICIAL();
  const uses = s.match(/<NameLine\b[^>]*>/g) || [];
  assert.equal(uses.length, 2, `NameLine 사용이 ${uses.length}곳 — 카드·목록 2곳이어야 한다`);
  assert.equal(uses.filter((u) => /\bwrap\b/.test(u)).length, 1, "목록 행만 wrap이어야 한다");
});

const NAME_BODY = () => {
  const s = OFFICIAL();
  const i = s.indexOf("function NameLine");
  return s.slice(i, i + 1100);
};

test("'멤버' 접두어 없이 모든 이름을 굵게 그린다", () => {
  const body = NAME_BODY();
  assert.ok(body.includes("font-bold"), "이름 줄이 굵지 않다");
  assert.ok(!/>멤버 </.test(body) && !body.includes("멤버 </span>"), "'멤버' 접두어가 남아 있다");
  // 구분점은 장식이고, 화면 읽기용 쉼표가 따로 있다.
  assert.ok(body.includes('aria-hidden="true"') && body.includes('sr-only">, <'),
    "이름 사이 구분이 보조기기에 전달되지 않는다");
});

test("긴 이름은 목록에서 줄바꿈하고 카드에서는 말줄임 + 전체 값 title", () => {
  const body = NAME_BODY();
  assert.ok(body.includes("[overflow-wrap:anywhere]"), "공백 없는 긴 이름이 넘친다");
  assert.ok(body.includes('"truncate"'), "카드 이름 줄이 한 줄로 묶이지 않는다");
  assert.ok(/title=\{full \|\| undefined\}/.test(body), "전체 이름을 확인할 수단이 없다");
});

test("솔로·팀 행의 기본 높이가 데이터와 무관하다", () => {
  const s = OFFICIAL();
  // 이름 줄은 항상 값이 있다(대표자) → 부문별 빈 줄 예약이 필요 없다.
  assert.ok(!s.includes("reserveMembers"), "부문별 멤버 줄 예약이 남아 있다");
  assert.ok(!/isGroup=\{/.test(s), "행/카드가 아직 부문 플래그로 높이를 바꾼다");
});

test("값이 없는 곡 줄은 접근성 트리에 노출되지 않는다", () => {
  const s = OFFICIAL(); const i = s.indexOf("function SongLine");
  assert.ok(/aria-hidden=\{text \? undefined : true\}/.test(s.slice(i, i + 700)),
    "빈 줄이 화면 읽기 프로그램에 남는다");
});

// ── 카드·행 높이 계약 (CLS) ────────────────────────────────────────────────
test("곡 줄은 값 유무와 무관하게 정확히 한 줄을 차지한다", () => {
  const s = OFFICIAL();
  assert.ok(/const LINE = "h-4 truncate leading-4";/.test(s),
    "공용 한 줄 높이 계약(LINE)이 사라졌다");
  const song = s.slice(s.indexOf("function SongLine"), s.indexOf("function SongLine") + 700);
  assert.ok(!/if \(!text\) return null;/.test(song),
    "곡 줄이 다시 조건부 렌더로 돌아갔다 — 데이터 도착 때 행이 자란다");
  assert.ok(song.includes("${LINE}"), "곡 줄이 공용 높이 계약을 쓰지 않는다");
  assert.ok(!s.includes("WebkitLineClamp"), "줄 수 클램프가 되살아났다 — 높이가 흔들린다");
});

test("로딩 스켈레톤이 최종 화면과 같은 구조·같은 상수를 쓴다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("function DivisionSkeleton"), "로딩 스켈레톤이 없다");
  const sk = s.slice(s.indexOf("function DivisionSkeleton"),
                     s.indexOf("/* ── 부문 섹션"));
  assert.ok(sk.includes("length: TOP_CARDS"), "카드 수가 TOP_CARDS와 묶여 있지 않다");
  assert.ok(sk.includes("length: OVERVIEW_ROWS"), "행 수가 OVERVIEW_ROWS와 묶여 있지 않다");
  assert.ok(sk.includes("grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"),
    "스켈레톤 카드 열 수가 최종본과 다르다");
  assert.ok(sk.includes("min-h-[68px]"), "스켈레톤 카드 하단 높이가 최종본과 다르다");
  assert.ok(sk.includes("px-2.5 py-2"), "스켈레톤 행 여백이 최종본과 다르다");
  assert.ok(sk.includes("h-7 w-7"), "스켈레톤 아바타 크기가 최종본과 다르다");
  // 이름 줄(20px) + 곡 줄(16px) — 최종본과 같은 두 줄.
  assert.ok(sk.includes('"h-5 w-1/2"') && sk.includes('"mt-0.5 h-4 w-2/3"'),
    "스켈레톤 행의 두 줄 높이가 최종본과 다르다");
  assert.ok(sk.includes("nb-tap nb-tap-icon"), "스켈레톤 아바타가 최종본의 터치 영역을 따르지 않는다");
  assert.ok(sk.includes('aria-hidden="true"'), "스켈레톤이 접근성 트리에 노출된다");
});

test("로딩 상태가 스피너 한 줄로 되돌아가지 않는다", () => {
  const s = OFFICIAL();
  const i = s.indexOf(") : loading ? (");
  const body = s.slice(i, s.indexOf(") : !data ? (", i));
  assert.ok(body.includes("DivisionSkeleton"), "로딩이 다시 스피너 하나가 됐다");
  assert.ok(!body.includes("py-24"), "로딩이 다시 짧은 블록이 됐다 — 아래가 밀린다");
  assert.ok(/role="status" className="sr-only"/.test(body), "로딩 상태를 알리지 않는다");
  assert.ok(body.includes('aria-busy="true"'), "aria-busy가 없다");
});

test("갱신 줄이 자리를 미리 잡아 둔다", () => {
  // campaigns 응답은 순위보다 늦게 올 수 있다. 조건부로 그리면 도착 순간 아래가 밀린다.
  const s = OFFICIAL();
  // PUBLIC-UX-1a: 본선은 갱신 방식 + 마지막 갱신(좁은 화면 두 줄 예약), 예선은 마지막 갱신 한 줄.
  assert.ok(s.includes('data-testid="final-update"') && /min-h-8 [^"]*sm:min-h-4/.test(s),
    "본선 갱신 안내가 자리를 예약하지 않는다");
  assert.ok(/min-h-4 [^"]*" role="status"\s*\n\s*aria-hidden=\{qualLast \? undefined : true\}/.test(s),
    "예선 갱신 줄이 자리를 예약하지 않는다");
  assert.ok(!/\{hasAnyRanking/.test(s), "옛 정렬 안내 문장 분기가 남아 있다");
});

test("이동을 overflow로 감추지 않는다", () => {
  const s = OFFICIAL();
  assert.ok(!/overflow-x-hidden/.test(s), "overflow-x-hidden으로 넘침을 감춘다");
  assert.ok(!/overflow: "hidden"/.test(s), "텍스트 줄을 잘라 숨긴다");
});

test("대표자 제외·중복 제거는 병합 모듈이 한다", () => {
  const m = MERGE();
  assert.ok(m.includes("function otherMembers"), "otherMembers가 없다");
  assert.ok(m.includes("mem.channelId === leadChannelId"), "대표자를 빼지 않는다");
  assert.ok(m.includes("seen.has(name)"), "중복 이름을 지우지 않는다");
  // 공식 배열의 첫 멤버를 대표자로 다시 뽑으면 안 된다.
  assert.ok(!/members\[0\][^\n]*lead/i.test(m),
    "공식 첫 멤버를 대표자로 다시 뽑는다");
});

// ── 행 링크 구조 ────────────────────────────────────────────────────────────
test("행의 두 링크가 형제이고 중첩되지 않는다", () => {
  const s = OFFICIAL();
  const i = s.indexOf("function ListRow");
  const body = s.slice(i, s.indexOf("/* ── 로딩 스켈레톤", i));
  // 프로필 링크가 닫힌 뒤에 클립 링크가 열려야 한다.
  const prof = body.indexOf("치지직 프로필 보기");
  const closeProf = body.indexOf("</a>", prof);
  const clip = body.indexOf("대표 클립 보기");
  assert.ok(prof > 0 && clip > 0, "두 링크 중 하나가 없다");
  assert.ok(closeProf > 0 && closeProf < clip,
    "클립 링크가 프로필 링크 안에 있다(중첩)");
  // 링크 안에 버튼을 넣지 않는다.
  assert.ok(!/<a[^>]*>[\s\S]{0,600}<button/.test(body), "링크 안에 버튼이 있다");
});

test("행 우측 클립 아이콘이 제거됐다", () => {
  const s = OFFICIAL();
  const i = s.indexOf("function ListRow");
  const body = s.slice(i, s.indexOf("/* ── 로딩 스켈레톤", i));
  assert.ok(!body.includes("ExternalLink"), "행에 외부 링크 아이콘이 남아 있다");
  assert.ok(!body.includes("클립 열기"), "옛 아이콘 aria-label이 남아 있다");
});

test("두 링크의 aria-label이 목적을 구분한다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("치지직 프로필 보기"), "프로필 링크 aria-label이 없다");
  assert.ok(s.includes("대표 클립 보기"), "클립 링크 aria-label이 없다");
});

test("외부 링크 보안 계약과 focus-visible을 유지한다", () => {
  const s = OFFICIAL();
  const i = s.indexOf("function ListRow");
  const body = s.slice(i, s.indexOf("/* ── 로딩 스켈레톤", i));
  // 주석에도 `<a>`라는 글자가 나오므로 **href가 붙은 실제 앵커만** 센다.
  const anchors = body.match(/<a href=[\s\S]*?>/g) || [];
  assert.equal(anchors.length, 2,
    `행에 실제 <a href>가 ${anchors.length}개 — 2개여야 한다`);
  for (const a of anchors) {
    assert.ok(a.includes('target="_blank"'), "target이 없다");
    assert.ok(a.includes('rel="noopener noreferrer"'), "rel이 없다");
    assert.ok(a.includes("focus-visible:outline"), "focus-visible이 없다");
    assert.ok(a.includes("nb-tap"), "44px 히트 영역 클래스가 없다");
  }
});

test("클립이 없으면 링크를 만들지 않고 접근 가능하게 알린다", () => {
  const s = OFFICIAL();
  const i = s.indexOf("function ListRow");
  const body = s.slice(i, s.indexOf("/* ── 로딩 스켈레톤", i));
  assert.ok(body.includes("clipUrl ? ("), "클립 유무 분기가 없다");
  assert.ok(body.includes("대표 클립 없음"), "비활성 상태를 알리지 않는다");
  assert.ok(body.includes('data-clip="none"'), "비활성 표식이 없다");
  // 빈 href로 새 창을 열지 않는다.
  assert.ok(!/href=\{clipUrl \|\| ""\}/.test(body), "빈 href를 만든다");
});

test("프로필 링크가 행 폭을 독점하지 않는다", () => {
  // 프로필이 flex-1을 먹으면 클릭 가능한 클립 영역이 사라진다.
  const s = OFFICIAL();
  const i = s.indexOf("치지직 프로필 보기");
  const prof = s.slice(i, i + 500);
  assert.ok(prof.includes("shrink-0") || prof.includes("max-w-"),
    "프로필 링크 폭이 제한돼 있지 않다");
  assert.ok(!prof.includes("flex-1"), "프로필 링크가 flex-1을 먹는다");
});

// ── 비퇴행 ──────────────────────────────────────────────────────────────────
test("곡 줄 계약은 그대로다", () => {
  const s = OFFICIAL();
  const uses = s.match(/<SongLine\b/g) || [];
  assert.equal(uses.length, 2, "SongLine이 카드·목록 2곳에 있어야 한다");
});

test("내부 비율 이름을 화면 코드에 쓰지 않는다", () => {
  const s = OFFICIAL();
  for (const bad of ["winRate", "matchRate", "win_ratio"]) {
    // 주석은 허용하지만 코드에서 참조하면 안 된다.
    assert.ok(!new RegExp(`item\\.${bad}|row\\.${bad}`).test(s),
      `${bad}를 화면에서 읽는다`);
  }
});
