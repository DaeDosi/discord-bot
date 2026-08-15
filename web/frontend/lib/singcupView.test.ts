// 싱드컵 탭 화면 전환 규칙 — 특히 **예전 공유 링크 호환**을 고정한다.
//
// 실행: web/frontend 에서
//   node --test lib/singcupView.test.ts
//
// 이 테스트가 지키는 계약은 하나다: 기본 화면을 공식 명단으로 바꾸면서도
// `?tab=singcup&sort=...` 같은 이미 배포된 링크가 예전과 같은 화면을 열어야 한다.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { applySingcupView, readSingcupView } from "./singcupView.ts";

const view = (s: string) => readSingcupView(s).view;

describe("첫 화면 결정", () => {
  it("쿼리가 없으면 공식 명단이다(새 기본)", () => {
    assert.equal(view(""), "official");
    assert.equal(view("?tab=singcup"), "official");
  });

  it("view=ranking / movers / official을 그대로 연다", () => {
    assert.equal(view("?tab=singcup&view=ranking"), "ranking");
    assert.equal(view("?tab=singcup&view=movers"), "movers");
    assert.equal(view("?tab=singcup&view=official"), "official");
  });

  it("정렬만 담긴 예전 링크는 랭킹으로 연다", () => {
    // 이게 깨지면 이미 공유된 링크가 조용히 다른 화면을 연다.
    assert.equal(view("?tab=singcup&sort=heart"), "ranking");
    assert.equal(view("?tab=singcup&sort=view&dir=asc"), "ranking");
    assert.equal(view("?tab=singcup&dir=asc"), "ranking");
    assert.equal(view("?tab=singcup&sort=heart1h"), "ranking");
  });

  it("명시된 view가 sort보다 우선한다", () => {
    assert.equal(view("?tab=singcup&view=official&sort=heart"), "official");
  });

  it("폐지된 view=board는 공식으로 되돌리고 주소를 정리한다", () => {
    const d = readSingcupView("?tab=singcup&view=board");
    assert.equal(d.view, "official");
    assert.equal(d.normalize, true);
  });

  it("모르는 view 값도 공식으로 정규화한다", () => {
    for (const v of ["", "RANKING", "ranking2", "official%20"]) {
      const d = readSingcupView(`?view=${v}`);
      assert.equal(d.view, "official", `view=${v}`);
      assert.equal(d.normalize, true, `view=${v}`);
    }
  });

  it("정상 값과 예전 링크에는 정규화를 요구하지 않는다", () => {
    for (const s of ["", "?tab=singcup", "?view=ranking", "?view=movers", "?sort=heart"]) {
      assert.equal(readSingcupView(s).normalize, false, s);
    }
  });
});

describe("화면을 바꿀 때 주소 갱신", () => {
  const u = (s: string) => new URL(`https://nexbot.shop/stats${s}`);

  it("랭킹·급상승은 view=를 남긴다", () => {
    const a = u("?tab=singcup");
    applySingcupView(a, "ranking");
    assert.equal(a.searchParams.get("view"), "ranking");
    assert.equal(a.searchParams.get("tab"), "singcup");   // 탭은 건드리지 않는다

    const b = u("?tab=singcup");
    applySingcupView(b, "movers");
    assert.equal(b.searchParams.get("view"), "movers");
  });

  it("공식으로 돌아가면 view와 정렬을 함께 지운다", () => {
    // sort가 남으면 다음에 이 링크를 열 때 예전-링크 호환 규칙이 랭킹으로 되돌린다.
    const a = u("?tab=singcup&view=ranking&sort=heart&dir=asc");
    applySingcupView(a, "official");
    assert.equal(a.searchParams.get("view"), null);
    assert.equal(a.searchParams.get("sort"), null);
    assert.equal(a.searchParams.get("dir"), null);
    assert.equal(a.searchParams.get("tab"), "singcup");
  });

  it("공식으로 정리한 주소를 다시 읽으면 공식이 나온다(왕복 일관성)", () => {
    const a = u("?tab=singcup&view=ranking&sort=heart");
    applySingcupView(a, "official");
    assert.equal(readSingcupView(a.search).view, "official");
    assert.equal(readSingcupView(a.search).normalize, false);
  });

  it("랭킹으로 바꾼 주소를 다시 읽으면 랭킹이 나온다(왕복 일관성)", () => {
    const a = u("?tab=singcup");
    applySingcupView(a, "ranking");
    assert.equal(readSingcupView(a.search).view, "ranking");
  });

  it("싱드컵과 무관한 쿼리는 보존한다", () => {
    const a = u("?tab=singcup&utm_source=x");
    applySingcupView(a, "official");
    assert.equal(a.searchParams.get("utm_source"), "x");
  });
});
