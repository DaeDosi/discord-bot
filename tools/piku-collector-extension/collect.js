/* PIKU 공개 랭킹 표를 **이미 렌더된 DOM에서** 읽는다.
 *
 * 이 파일은 활성 탭에 주입되어 실행된다. 지키는 것:
 *   · PIKU에 **추가 요청을 보내지 않는다.** 화면에 이미 있는 표만 읽는다.
 *   · 쿠키·세션·헤더를 만지지 않는다(읽지도, 넘기지도 않는다).
 *   · 원문 HTML을 반환하지 않는다. 정규화된 행만 돌려준다.
 *   · 차단 화면·확인 화면이면 **즉시 중단**한다. 우회하지 않는다.
 *
 * 반환은 항상 `{ ok, ... }` 한 모양이다 — 실패를 성공처럼 보이게 하지 않는다.
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

  const fail = (kind, message) => ({ ok: false, kind, message });

  const text = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");

  /** "11.66%" · "11.66 %" · "11.66" → 11.66 */
  const pct = (s) => {
    const m = String(s).replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
    if (!m) return null;
    const v = Number(m[0]);
    return Number.isFinite(v) ? v : null;
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
      if (tds.length < 4) continue;

      // 순위 — 첫 칸의 숫자.
      const rank = parseInt(String(text(tds[0])).replace(/[^\d]/g, ""), 10);
      if (!Number.isInteger(rank) || rank <= 0) continue;

      // 이름 칸에는 이름과 곡 정보가 함께 들어 있다. **문자열을 임의로 `-`로
      // 쪼개지 않는다** — 표의 실제 구조(줄바꿈·별도 요소)를 따른다.
      const nameCell = tds.find((td) => td.querySelector("img")) || tds[2] || tds[1];
      const img = nameCell ? nameCell.querySelector("img") : null;

      // 곡·가수는 별도 요소로 들어 있다(small/span/줄바꿈). 요소가 있으면 그것을
      // 쓰고, 없으면 줄 단위로 나눈다. 어느 쪽도 없으면 그 행은 버린다.
      const parts = nameCell
        ? [...nameCell.querySelectorAll("small, span, p, div")]
            .map(text).filter(Boolean)
        : [];
      const lines = text(nameCell).split("\n").map((s) => s.trim()).filter(Boolean);
      const pool = parts.length >= 2 ? parts : lines;

      const streamer = pool[0] || text(nameCell);
      const songTitle = pool[1] || "";
      const artist = pool[2] || "";

      // 비율 두 칸 — 표 오른쪽의 숫자 칸에서 순서대로 읽는다.
      const nums = tds.slice(1).map((td) => pct(text(td)))
        .filter((v) => v !== null && v >= 0 && v <= 100);
      // PIKU 표기: 앞이 우승 비율, 뒤가 승률.
      const winRatio = nums.length >= 2 ? nums[nums.length - 2] : null;
      const winRate = nums.length >= 2 ? nums[nums.length - 1] : null;

      rows.push({
        rank,
        streamer,
        song_title: songTitle,
        artist,
        win_ratio: winRatio,
        win_rate: winRate,
        image_url: img && img.src && /^https?:/.test(img.src) ? img.src : "",
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
    // 빠진 값이 있으면 중단한다(서버도 막지만 여기서 먼저 알린다).
    const bad = rows.find((r) =>
      !r.streamer || !r.song_title || !r.artist
      || r.win_ratio === null || r.win_rate === null);
    if (bad) {
      return fail("parse_failed",
        `${bad.rank}위 행에서 값을 읽지 못했습니다. 표 구조가 바뀌었을 수 있습니다.`);
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
        rows,
      },
    };
  } catch (e) {
    return fail("aborted", "읽는 중 오류가 발생했습니다.");
  }
})();
