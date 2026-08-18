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

// ── 그룹 멤버 줄 ────────────────────────────────────────────────────────────
test("멤버 줄은 공용 memberLine 하나만 쓴다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes('memberLine'), "memberLine을 쓰지 않는다");
  assert.ok(s.includes("function MemberLine"), "MemberLine 컴포넌트가 없다");
  // 문자열 조립을 화면에서 다시 하지 않는다.
  assert.ok(!/memberNames\s*\.\s*join/.test(s),
    "화면에서 멤버 문자열을 다시 조립한다 — memberLine을 쓸 것");
});

test("카드와 목록이 같은 MemberLine을 쓴다", () => {
  const s = OFFICIAL();
  const uses = s.match(/<MemberLine\b/g) || [];
  assert.equal(uses.length, 2, `MemberLine 사용이 ${uses.length}곳 — 카드·목록 2곳이어야 한다`);
});

const MEMBER_BODY = () => {
  const s = OFFICIAL();
  const i = s.indexOf("function MemberLine");
  return s.slice(i, i + 900);
};

test("솔로 부문에는 멤버 줄이 아예 생기지 않는다", () => {
  // `reserve`가 false면(= 솔로) 값이 없을 때 줄 자체를 그리지 않는다 —
  // 빈 자리조차 만들지 않는다.
  assert.ok(MEMBER_BODY().includes("if (!text && !reserve) return null;"),
    "솔로에서도 멤버 줄 자리를 만든다");
});

test("그룹은 1인 팀이어도 같은 높이 계약을 지킨다", () => {
  const s = OFFICIAL();
  // 예약 여부는 **부문**이 정한다. `memberNames.length`로 판단하면 32팀 중 1팀뿐인
  // 1인 팀만 행 높이가 달라지고, 정렬이 바뀌어 그 팀이 화면에 들어왔다 나갈 때마다
  // 목록 전체 높이가 흔들린다.
  assert.ok(/<MemberLine[^/]*reserve=\{isGroup\}/.test(s),
    "멤버 줄 예약이 부문이 아니라 데이터로 결정된다");
  assert.ok(/isGroup=\{division === "groups"\}/.test(s),
    "isGroup을 부문에서 내려 주지 않는다");
  // 1인 팀에도 '멤버'라는 라벨만 떠 있으면 안 된다 — 값이 있을 때만 라벨을 붙인다.
  assert.ok(/text \? <><span className="text-muted\/60">멤버 <\/span>\{text\}<\/> : null/
    .test(MEMBER_BODY()), "값이 없는데도 '멤버' 라벨을 그린다");
});

test("보이지 않는 보조 줄은 접근성 트리에 노출되지 않는다", () => {
  for (const body of [MEMBER_BODY(), (() => {
    const s = OFFICIAL(); const i = s.indexOf("function SongLine");
    return s.slice(i, i + 700);
  })()]) {
    assert.ok(/aria-hidden=\{text \? undefined : true\}/.test(body),
      "빈 줄이 화면 읽기 프로그램에 남는다");
  }
});

test("멤버 줄에 전체 값을 확인할 수단이 있다", () => {
  const body = MEMBER_BODY();
  assert.ok(/title=\{text \? `멤버 \$\{text\}` : undefined\}/.test(body),
    "title이 없거나 빈 줄에도 툴팁이 남는다");
});

// ── 카드·행 높이 계약 (CLS) ────────────────────────────────────────────────
test("보조 줄은 값 유무와 무관하게 정확히 한 줄을 차지한다", () => {
  const s = OFFICIAL();
  // 곡·가수는 공식 명단에 없고 PIKU 응답에서만 온다(실측 201행 전부 없음).
  // 조건부로 그리면 PIKU가 늦게 도착할 때 160행이 한꺼번에 한 줄씩 자란다.
  assert.ok(/const LINE = "h-4 truncate leading-4";/.test(s),
    "공용 한 줄 높이 계약(LINE)이 사라졌다");
  const song = s.slice(s.indexOf("function SongLine"), s.indexOf("function SongLine") + 700);
  assert.ok(!/if \(!text\) return null;/.test(song),
    "곡 줄이 다시 조건부 렌더로 돌아갔다 — 데이터 도착 때 행이 자란다");
  assert.ok(song.includes("${LINE}"), "곡 줄이 공용 높이 계약을 쓰지 않는다");
  assert.ok(MEMBER_BODY().includes("${LINE}"), "멤버 줄이 공용 높이 계약을 쓰지 않는다");
  // 두 줄 클램프는 길이에 따라 높이가 달라져 계약이 깨진다.
  assert.ok(!s.includes("WebkitLineClamp"), "줄 수 클램프가 되살아났다 — 높이가 흔들린다");
});

test("로딩 스켈레톤이 최종 화면과 같은 구조·같은 상수를 쓴다", () => {
  const s = OFFICIAL();
  assert.ok(s.includes("function DivisionSkeleton"), "로딩 스켈레톤이 없다");
  const sk = s.slice(s.indexOf("function DivisionSkeleton"),
                     s.indexOf("/* ── 부문 섹션"));
  // 카드 수·행 수는 최종 화면과 **같은 상수**에서 와야 한다(하드코딩 금지).
  assert.ok(sk.includes("length: TOP_CARDS"), "카드 수가 TOP_CARDS와 묶여 있지 않다");
  assert.ok(sk.includes("length: OVERVIEW_ROWS"), "행 수가 OVERVIEW_ROWS와 묶여 있지 않다");
  // 열 수는 같은 grid 클래스가 정한다 — 뷰포트마다 자동으로 맞는다.
  assert.ok(sk.includes("grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"),
    "스켈레톤 카드 열 수가 최종본과 다르다");
  // 카드 하단부·행의 높이 계약이 최종본과 같아야 한다.
  assert.ok(sk.includes("min-h-[68px]"), "스켈레톤 카드 하단 높이가 최종본과 다르다");
  assert.ok(sk.includes("px-2.5 py-2"), "스켈레톤 행 여백이 최종본과 다르다");
  assert.ok(sk.includes("h-7 w-7"), "스켈레톤 아바타 크기가 최종본과 다르다");
  // 그룹만 멤버 줄 자리를 갖는다 — 최종본과 같은 규칙.
  assert.ok(sk.includes("{isGroup && <SkeletonBar"), "스켈레톤이 부문을 구분하지 않는다");
  // 빈 껍데기는 읽히면 안 된다.
  assert.ok(sk.includes('aria-hidden="true"'), "스켈레톤이 접근성 트리에 노출된다");
});

test("로딩 상태가 스피너 한 줄로 되돌아가지 않는다", () => {
  const s = OFFICIAL();
  const i = s.indexOf(") : loading ? (");
  const body = s.slice(i, s.indexOf(") : !data ? (", i));
  assert.ok(body.includes("DivisionSkeleton"), "로딩이 다시 스피너 하나가 됐다");
  assert.ok(!body.includes("py-24"), "로딩이 다시 짧은 블록이 됐다 — 아래가 밀린다");
  // 상태는 문장 하나로만 알린다.
  assert.ok(/role="status" className="sr-only"/.test(body), "로딩 상태를 알리지 않는다");
  assert.ok(body.includes('aria-busy="true"'), "aria-busy가 없다");
});

test("정렬 안내 문장이 자리를 미리 잡아 둔다", () => {
  const s = OFFICIAL();
  const i = s.indexOf("현재 <b className=\"text-fg\">");
  const block = s.slice(Math.max(0, i - 700), i + 200);
  // 조건부 렌더로 돌아가면 PIKU 응답이 늦게 올 때 이 줄이 끼어들며 아래가 밀린다.
  assert.ok(!/\{hasAnyRanking && \(\s*<p role="status"/.test(s),
    "정렬 안내가 다시 조건부 렌더가 됐다");
  assert.ok(block.includes('hasAnyRanking ? undefined : "invisible"'),
    "값이 없을 때 자리를 비워 두지 않는다");
  assert.ok(block.includes("aria-hidden={hasAnyRanking ? undefined : true}"),
    "빈 문장이 접근성 트리에 남는다");
  // hidden은 자리까지 없애 원래 문제로 되돌아간다.
  assert.ok(!/className=\{hasAnyRanking \? undefined : "hidden"\}/.test(block),
    "hidden을 써서 자리가 사라진다");
});

test("이동을 overflow로 감추지 않는다", () => {
  const s = OFFICIAL();
  assert.ok(!/overflow-x-hidden/.test(s), "overflow-x-hidden으로 넘침을 감춘다");
  // 카드 썸네일의 `overflow-hidden`은 이미지 라운드 처리용이라 허용한다.
  // 텍스트 줄에 붙는 overflow는 금지 — 잘라 숨기는 대신 말줄임을 쓴다.
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
