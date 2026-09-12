/* PIKU 랭킹 표의 **페이지 넘김 상태**를 읽고, 요청받은 페이지 번호를 누른다.
 *
 * 왜 필요한가: 본선 페이지(`2ut8Li`)에는 '보기 개수' 컨트롤이 없다(실측
 * `bLengthChange=false`, 10행 × 4페이지). 사람이 보려 해도 페이지 번호를 눌러야
 * 하므로, 확장도 **사람이 누르는 것과 같은 링크를 같은 순서로** 누른다.
 *
 * 지키는 것:
 *   · PIKU 내부 API를 직접 부르지 않는다. 표 크기(`page.len`)도 바꾸지 않는다.
 *     페이지의 자체 링크(`.paginate_button`)를 DOM 클릭할 뿐이다 — 그 클릭이
 *     페이지 스크립트를 통해 무엇을 요청하는지는 사람이 눌렀을 때와 같다.
 *   · 어느 페이지로 갈지는 DOM 속성 `data-nexbot-piku-goto`로 받는다.
 *     (`executeScript({files})`는 인자를 못 넘긴다.) 비어 있으면 상태만 읽는다.
 *   · 쿠키·원문 HTML을 돌려주지 않는다. 숫자와 순위 첫 값뿐이다.
 *
 * 반환: { ok, pages, current, rows, firstRank, lastRank, clicked }
 */
(() => {
  "use strict";

  const text = (el) => (el ? String(el.textContent).replace(/\s+/g, " ").trim() : "");

  try {
    const table = document.querySelector("table.dataTable") || document.querySelector("table");
    const links = [...document.querySelectorAll(".dataTables_paginate a.paginate_button")]
      .filter((a) => /^\d+$/.test(text(a)));
    const pages = links.length;
    const current = (() => {
      const c = links.find((a) => a.classList.contains("current"));
      return c ? parseInt(text(c), 10) : (pages ? 1 : 0);
    })();
    const trs = table ? [...table.querySelectorAll("tbody tr")] : [];
    const ranks = trs
      .map((tr) => parseInt(String(text(tr.children[0])).replace(/[^\d]/g, ""), 10))
      .filter((n) => Number.isInteger(n) && n > 0);

    const want = document.documentElement && document.documentElement.dataset
      ? String(document.documentElement.dataset.nexbotPikuGoto || "") : "";
    let clicked = false;
    if (want) {
      const n = parseInt(want, 10);
      const target = links.find((a) => text(a) === String(n));
      if (!target) return { ok: false, kind: "pager_failed" };
      if (n !== current) {
        target.click();          // 사람이 누르는 것과 같은 링크
        clicked = true;
      }
    }
    return {
      ok: true, pages, current, rows: ranks.length,
      firstRank: ranks.length ? Math.min(...ranks) : 0,
      lastRank: ranks.length ? Math.max(...ranks) : 0,
      clicked,
    };
  } catch (_) {
    return { ok: false, kind: "pager_failed" };
  }
})();
