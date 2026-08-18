/* PIKU 공개 랭킹 표를 **이미 렌더된 DOM에서** 읽는다.
 *
 * 이 파일은 활성 탭에 주입되어 실행된다. 지키는 것:
 *   · PIKU에 **추가 요청을 보내지 않는다.** 화면에 이미 있는 표만 읽는다.
 *   · 쿠키·세션·헤더를 만지지 않는다(읽지도, 넘기지도 않는다).
 *   · 원문 HTML을 반환하지 않는다. 정규화된 행만 돌려준다.
 *   · 차단 화면·확인 화면이면 **즉시 중단**한다. 우회하지 않는다.
 *
 * 반환은 항상 `{ ok, ... }` 한 모양이다 — 실패를 성공처럼 보이게 하지 않는다.
 *
 * ── 실제 행 구조 (2026-08-18 canary로 확인) ─────────────────────────────────
 *   td[0] 순위          "1"
 *   td[1] 썸네일        `<img>`가 **아니라** 내부 요소의 CSS background-image
 *   td[2] 이름·곡·가수  `<strong data-no="24046001">[유람 Yuram] ENEMY - ImagineDragons</strong>`
 *                       — 셋이 **한 문자열**이다. 자식 요소도 줄바꿈도 없다.
 *   td[3] 우승 비율     "18.90%"
 *   td[4] 승률          "77.22%"
 *   td[5] 순위 추이     SVG (숫자가 들어 있을 수 있다)
 *
 * 이전 파서는 1위 행부터 `parse_failed`를 냈다. 원인이 하나가 아니었다:
 *   1. 곡·가수를 `small/span/p/div` 자식이나 줄바꿈에서 찾았다. `text()`가 이미
 *      `\s+ → " "`로 접으므로 **줄바꿈은 영원히 나오지 않는다** → 항상 빈 문자열.
 *   2. 썸네일을 `<img>`에서만 찾았다.
 *   3. 이름 칸을 `<img>` 유무로 골랐다 — 실제로는 `<img>`가 없다.
 *   4. 백분율을 오른쪽 칸 전체에서 훑어 td[5] 순위 추이 숫자가 섞일 수 있었다.
 */
(() => {
  "use strict";

  const SOURCES = {
    "8jGsHE": { division: "female_solo", expected: 64 },
    "7PqH44": { division: "male_solo", expected: 64 },
    "7fXoNs": { division: "groups", expected: 32 },
  };

  /** 차단·확인 화면 표식. 만나면 중단한다(대응하지 않는다). */
  const BLOCKED = [
    "Attention Required", "cf-browser-verification", "challenge-platform",
    "자동입력 방지", "보안문자", "reCAPTCHA", "接続がブロック",
  ];

  /** 곡과 가수를 가르는 구분자. PIKU 표기는 `곡 - 가수`다. */
  const SEP = " - ";

  const fail = (kind, message) => ({ ok: false, kind, message });

  const text = (el) => (el ? String(el.textContent).replace(/\s+/g, " ").trim() : "");

  /** 백분율 **한 칸**. 범위를 벗어나거나 숫자가 아니면 null.
   *
   * 행 전체를 훑지 않는 것이 핵심이다 — td[5]의 순위 추이 SVG에도 숫자가 있다.
   */
  const pct = (s) => {
    const t = String(s == null ? "" : s).replace(/[\s,%]/g, "");
    if (!/^-?\d+(?:\.\d+)?$/.test(t)) return null;
    const v = Number(t);
    if (!Number.isFinite(v) || v < 0 || v > 100) return null;
    return v;
  };

  /** `[스트리머] 곡 - 가수` 한 문자열을 셋으로 가른다.
   *
   * DOM에는 곡과 가수의 경계를 알려 주는 구조가 **없다**. 구분자는 ` - `(앞뒤
   * 공백이 있는 붙임표) 하나로 고정하고, 여러 번 나오면 **마지막 것**을 경계로
   * 쓴다 — 곡 제목 자체에 ` - `가 들어가는 경우가 실재하기 때문이다.
   *
   *   [피 네] HOLLOW HUNGER - OVERLOAD Ⅳ - OxT
   *     → 곡 "HOLLOW HUNGER - OVERLOAD Ⅳ" · 가수 "OxT"
   *
   * 2026-08-18 canary가 이 행에서 막혔다. 그때 계약은 "두 번 이상이면 거부"였는데,
   * 그러면 실제 남성 64행 중 **한 행 때문에 수집 전체가 멈춘다**. 가수 이름에
   * ` - `가 들어가는 쪽보다 곡 제목에 들어가는 쪽이 훨씬 흔하므로 마지막을 경계로
   * 삼는다. `Ne-Yo`처럼 공백 없는 붙임표는 구분자가 아니다.
   *
   * 그룹은 대괄호 안 문자열을 **통째로** 남긴다. 대표자를 여기서 자르지 않는다 —
   * 그 결정은 서버의 `group_lead()`가 한다(쉼표 기준 첫 번째 비어 있지 않은 이름).
   */
  const splitName = (raw) => {
    const s = String(raw || "").trim();
    const open = s.indexOf("[");
    const close = open < 0 ? -1 : s.indexOf("]", open + 1);
    if (open < 0 || close < 0) return { reason: "이름 대괄호를 찾지 못했습니다" };

    const streamer = s.slice(open + 1, close).trim();
    const rest = s.slice(close + 1).trim();

    const cut = rest.lastIndexOf(SEP);
    if (cut < 0) return { reason: "곡과 가수를 가르는 ' - '가 없습니다" };
    const songTitle = rest.slice(0, cut).trim();
    const artist = rest.slice(cut + SEP.length).trim();
    if (!streamer || !songTitle || !artist) {
      return { reason: "이름·곡·가수 중 비어 있는 값이 있습니다" };
    }
    return { streamer, songTitle, artist };
  };

  /** `url("...")` · `url(...)`에서 주소만 꺼낸다. */
  const cssUrl = (value) => {
    const m = /url\(\s*(['"]?)([^'")]+)\1\s*\)/i.exec(String(value || ""));
    return m ? m[2].trim() : "";
  };

  /** http(s)만 통과시킨다 — `javascript:`·`data:`·`blob:`·그 밖은 버린다. */
  const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? String(u).trim() : "");

  /** 썸네일: `<img>` → inline background-image → 계산된 background-image 순.
   *
   * 셋 다 없으면 빈 문자열이다. **썸네일이 없다는 이유로 행 파싱을 실패시키지
   * 않는다** — 서버 스키마도 빈 문자열을 허용한다.
   */
  const thumbnail = (cell) => {
    if (!cell) return "";
    const img = cell.querySelector ? cell.querySelector("img") : null;
    if (img) {
      const attr = img.getAttribute ? img.getAttribute("data-src") : "";
      for (const v of [img.currentSrc, img.src, attr]) {
        const u = safeUrl(v);
        if (u) return u;
      }
    }
    const nodes = [cell].concat(
      cell.querySelectorAll ? [...cell.querySelectorAll("*")] : []);
    for (const n of nodes) {
      const u = safeUrl(cssUrl(n.style && n.style.backgroundImage));
      if (u) return u;
    }
    if (typeof getComputedStyle === "function") {
      for (const n of nodes) {
        try {
          const u = safeUrl(cssUrl(getComputedStyle(n).backgroundImage));
          if (u) return u;
        } catch (_) { /* 계산 실패는 '썸네일 없음'으로 본다 */ }
      }
    }
    return "";
  };

  try {
    const m = location.pathname.match(/^\/w\/rank\/([A-Za-z0-9_-]{1,32})\/?$/);
    if (location.hostname !== "www.piku.co.kr" || !m) {
      return fail("wrong_page", "PIKU 랭킹 페이지에서 실행해 주세요.");
    }
    const sourceId = m[1];
    const meta = SOURCES[sourceId];
    if (!meta) return fail("wrong_page", "등록된 부문 주소가 아닙니다.");

    const head = document.body ? document.body.innerText.slice(0, 4000) : "";
    if (BLOCKED.some((s) => head.includes(s))) {
      return fail("blocked", "PIKU가 확인 화면을 표시했습니다. 중단합니다.");
    }

    // 표는 하나만 고른다 — 여러 개면 어느 것이 랭킹인지 알 수 없으므로 중단한다.
    const bodies = [...document.querySelectorAll("table tbody")]
      .filter((tb) => tb.querySelectorAll("tr").length >= 5);
    if (bodies.length === 0) {
      return fail("not_rendered",
        "랭킹 표를 찾지 못했습니다. 표가 모두 보이도록 스크롤한 뒤 다시 시도해 주세요.");
    }
    const tb = bodies.sort(
      (a, b) => b.querySelectorAll("tr").length - a.querySelectorAll("tr").length)[0];

    const rows = [];
    for (const tr of tb.querySelectorAll("tr")) {
      const tds = [...tr.children];
      // 순위·썸네일·이름·우승 비율·승률 다섯 칸이 있어야 데이터 행이다.
      // 광고 행·구분 행·순위 추이만 있는 행은 여기서 걸러진다.
      if (tds.length < 5) continue;

      const rank = parseInt(String(text(tds[0])).replace(/[^\d]/g, ""), 10);
      if (!Number.isInteger(rank) || rank <= 0) continue;

      // 이름 칸의 정본은 `strong[data-no]`다. **`<img>` 유무로 칸을 고르지
      // 않는다** — 실제 썸네일은 CSS background-image라 `<img>`가 아예 없다.
      const strong = tr.querySelector("strong[data-no]");
      const parsed = splitName(strong ? text(strong) : text(tds[2]));

      rows.push({
        rank,
        streamer: parsed.streamer || "",
        song_title: parsed.songTitle || "",
        artist: parsed.artist || "",
        // 비율은 **칸을 지정해서** 읽는다(위 `pct` 주석 참조).
        win_ratio: pct(text(tds[3])),
        win_rate: pct(text(tds[4])),
        image_url: thumbnail(tds[1]),
        _reason: parsed.reason || "",
      });
    }

    if (rows.length === 0) {
      return fail("not_rendered", "표에서 읽을 수 있는 행이 없습니다.");
    }
    if (rows.length !== meta.expected) {
      // 부분 데이터를 보내지 않는다 — 개수 계약은 서버도 확인하지만, 여기서
      // 먼저 끊어야 "일부만 성공"이 네트워크를 타지 않는다.
      //
      // 흔한 원인은 표의 페이지 크기다. 우리가 **직접 바꾸지 않는다** — 서버측
      // DataTables라면 선택을 바꾸는 순간 내부 API 요청이 나가고, 그건 "이미
      // 렌더된 것만 읽는다"는 약속을 깬다. 대신 무엇을 하면 되는지 알린다.
      const sel = document.querySelector("select[name$='_length'], .dataTables_length select");
      const hint = sel
        ? `표 위의 '보기 개수'를 ${meta.expected}개 이상(예: 100)으로 바꾼 뒤 다시 시도해 주세요.`
        : "표가 모두 보이도록 펼친 뒤 다시 시도해 주세요.";
      return fail("partial",
        `${meta.expected}행이어야 하는데 ${rows.length}행만 보입니다. ` + hint);
    }

    // 빠진 값이 있으면 **행 번호와 이유를 붙여** 중단한다(조용한 누락 금지).
    const bad = rows.find((r) =>
      r._reason || !r.streamer || !r.song_title || !r.artist
      || r.win_ratio === null || r.win_rate === null);
    if (bad) {
      const why = bad._reason || "값이 비어 있거나 비율을 읽지 못했습니다";
      return fail("parse_failed", `${bad.rank}위 행: ${why}.`);
    }

    // 순위는 1..N 연속이어야 하고 중복이 없어야 한다.
    const ranks = rows.map((r) => r.rank).sort((a, b) => a - b);
    for (let i = 0; i < ranks.length; i++) {
      if (ranks[i] !== i + 1) {
        return fail("parse_failed",
          `순위가 1~${meta.expected} 연속이 아닙니다(${ranks[i]}위에서 어긋남).`);
      }
    }
    // 같은 원본 행이 두 번 잡히면(표가 겹쳐 렌더된 경우) 중단한다.
    const seen = new Set();
    for (const r of rows) {
      const key = `${r.streamer} ${r.song_title} ${r.artist}`;
      if (seen.has(key)) {
        return fail("parse_failed", `같은 행이 두 번 읽혔습니다: ${r.streamer}`);
      }
      seen.add(key);
    }

    return {
      ok: true,
      payload: {
        schemaVersion: 1,
        division: meta.division,
        sourceId,
        sourceUrl: `https://www.piku.co.kr/w/rank/${sourceId}`,
        collectedAt: new Date().toISOString(),
        rowCount: rows.length,
        // 내부 판정용 `_reason`은 **여기서 떨어진다** — 전송 스키마에 자리가 없다.
        rows: rows.map((r) => ({
          rank: r.rank,
          streamer: r.streamer,
          song_title: r.song_title,
          artist: r.artist,
          win_ratio: r.win_ratio,
          win_rate: r.win_rate,
          image_url: r.image_url,
        })),
      },
    };
  } catch (e) {
    return fail("aborted", "읽는 중 오류가 발생했습니다.");
  }
})();
