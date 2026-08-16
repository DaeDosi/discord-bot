// 활동 잔디 라벨 로직의 **동작** 검증 (UI-P 요구 5).
//
// `uiPolish.test.ts`는 "코드에 그 구조가 있는지"를 보지만, 월 경계·연도 경계·
// 겹침 방지는 값으로 확인해야 의미가 있다. Heatmap.tsx는 React 컴포넌트라
// 여기서 렌더링할 수 없으므로, 같은 규칙을 그대로 옮겨 놓고 검증한다.
// (규칙이 갈라지지 않도록 상수는 uiPolish.test.ts가 소스와 대조한다.)
import { test } from "node:test";
import assert from "node:assert/strict";

const MONTHS = ["1월", "2월", "3월", "4월", "5월", "6월",
                "7월", "8월", "9월", "10월", "11월", "12월"];
const DOW = ["일", "월", "화", "수", "목", "금", "토"];
const CELL = 11, GAP = 3, MIN_LABEL_GAP = 30;

const monthOf = (date: string) => Number(date.slice(5, 7)) - 1;

/** Heatmap.tsx의 monthLabels 규칙 — 경계 우선, 겹치면 뒤엣것을 버린다. */
function monthLabels(firstDates: (string | null)[]) {
  const boundaries: { i: number; text: string }[] = [];
  let firstMonth: number | null = null;
  firstDates.forEach((d, i) => {
    if (!d) return;
    const m = monthOf(d);
    if (firstMonth === null) { firstMonth = m; return; }
    const prev = firstDates[i - 1];
    if (prev && monthOf(prev) !== m) boundaries.push({ i, text: MONTHS[m] });
  });
  const out: { i: number; text: string }[] = [];
  if (firstMonth !== null
      && (!boundaries.length || boundaries[0].i * (CELL + GAP) >= MIN_LABEL_GAP)) {
    out.push({ i: 0, text: MONTHS[firstMonth] });
  }
  for (const b of boundaries) {
    const last = out[out.length - 1];
    if (last && (b.i - last.i) * (CELL + GAP) < MIN_LABEL_GAP) continue;
    out.push(b);
  }
  return out;
}

test("요일 라벨은 7개이고 일요일에서 시작한다", () => {
  assert.equal(DOW.length, 7);
  assert.equal(DOW[0], "일");   // 격자의 start는 항상 일요일이다
  assert.equal(DOW[6], "토");
  assert.ok(DOW.every((d) => d.length === 1), "한 글자여야 폭이 좁다");
});

test("월 라벨은 12개이고 1월부터 12월까지다", () => {
  assert.equal(MONTHS.length, 12);
  assert.equal(MONTHS[0], "1월");
  assert.equal(MONTHS[11], "12월");
});

test("월 인덱스는 문자열에서 직접 읽어 타임존 영향을 받지 않는다", () => {
  // `new Date("2026-03-01")`은 UTC로 해석돼 음수 오프셋 지역에서 2월이 된다.
  assert.equal(monthOf("2026-03-01"), 2);
  assert.equal(monthOf("2026-01-01"), 0);
  assert.equal(monthOf("2026-12-31"), 11);
});

test("1월 연도 경계에서 12월 다음이 1월로 넘어간다", () => {
  const cols = ["2025-12-07", "2025-12-14", "2025-12-21", "2025-12-28", "2026-01-04"];
  const out = monthLabels(cols);
  assert.deepEqual(out.map((o) => o.text), ["12월", "1월"]);
  assert.equal(out[1].i, 4);
});

test("윤년 2월 경계도 정확하다", () => {
  // 2028-02-29는 윤년의 마지막 2월 날짜다.
  const cols = ["2028-02-06", "2028-02-13", "2028-02-20", "2028-02-27", "2028-03-05"];
  const out = monthLabels(cols);
  assert.deepEqual(out.map((o) => o.text), ["2월", "3월"]);
});

test("첫 열 라벨과 다음 달이 붙으면 첫 열 라벨을 버린다", () => {
  // 실제로 화면에서 "2월3월"로 붙어 보이던 상황: 첫 열이 2월 말, 1열이 3월.
  // 이후 달들은 실제 격자처럼 4~5열 간격을 둔다(한 달은 보통 4주 이상이다).
  const cols = ["2026-02-22", "2026-03-01", "2026-03-08", "2026-03-15",
                "2026-03-22", "2026-03-29", "2026-04-05"];
  const out = monthLabels(cols);
  // 1열 * 14px = 14px < 30px 이므로 0열의 '2월' 라벨을 넣지 않는다.
  assert.deepEqual(out.map((o) => o.text), ["3월", "4월"]);
  assert.equal(out[0].i, 1);
});

test("충분히 떨어져 있으면 첫 열 라벨도 남긴다", () => {
  const cols = ["2026-02-01", "2026-02-08", "2026-02-15", "2026-02-22", "2026-03-01"];
  const out = monthLabels(cols);
  assert.deepEqual(out.map((o) => o.text), ["2월", "3월"]);
});

test("어떤 두 라벨도 최소 간격 미만으로 붙지 않는다", () => {
  // 한 달이 한 열만 걸치는 극단(월이 매 열마다 바뀌는 가상 입력)에서도
  // 남은 라벨끼리는 반드시 MIN_LABEL_GAP 이상 떨어져 있어야 한다.
  const cols = ["2026-01-04", "2026-02-01", "2026-03-01", "2026-04-05", "2026-05-03"];
  const out = monthLabels(cols);
  for (let i = 1; i < out.length; i++) {
    const gapPx = (out[i].i - out[i - 1].i) * (CELL + GAP);
    assert.ok(gapPx >= MIN_LABEL_GAP, `${out[i - 1].text}와 ${out[i].text}가 ${gapPx}px로 붙었다`);
  }
});

test("KST 자정 경계에서 서버(UTC)와 클라이언트가 같은 날을 고른다", () => {
  // 같은 순간을 UTC와 KST 어디서 읽든 오프셋을 더한 뒤 UTC 게터로 읽으면
  // 결과가 같다 — 이게 SSR/CSR 문자열 일치의 근거다.
  const KST = 9 * 60 * 60 * 1000;
  const at = (iso: string) => {
    const t = new Date(new Date(iso).getTime() + KST);
    return `${t.getUTCFullYear()}-${String(t.getUTCMonth() + 1).padStart(2, "0")}`
         + `-${String(t.getUTCDate()).padStart(2, "0")}`;
  };
  // KST 2026-08-16 00:00 == UTC 2026-08-15 15:00
  assert.equal(at("2026-08-15T15:00:00Z"), "2026-08-16");
  // 그 1분 전은 아직 8월 15일이어야 한다
  assert.equal(at("2026-08-15T14:59:00Z"), "2026-08-15");
  // 연도 경계: KST 2027-01-01 00:00 == UTC 2026-12-31 15:00
  assert.equal(at("2026-12-31T15:00:00Z"), "2027-01-01");
});
