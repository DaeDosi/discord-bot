/* 확장 팝업 — 읽기 실행과 전송.
 *
 * **어떤 secret도 저장하지 않는다.** 토큰은 운영자가 그때그때 붙여 넣고,
 * 이 스크립트는 메모리에만 두었다가 요청 한 번에 쓰고 버린다(1회용이라 보관할
 * 이유도 없다). `chrome.storage`를 쓰지 않는 것은 의도다.
 *
 * 실패는 실패로 남긴다 — 서버의 `failure` 경로에 종류만 알린다(본문은 안 보낸다).
 */
"use strict";

const $ = (id) => document.getElementById(id);
const out = $("out");

function show(ok, text) {
  out.className = ok === null ? "" : ok ? "ok" : "bad";
  out.textContent = text;
}

/** 전송할 곳은 우리 관리 경로 하나뿐이다. 임의 주소로 새어 나가지 않게 고정한다. */
function ingestUrl(base, path) {
  let u;
  try { u = new URL(base); } catch { throw new Error("NexBot 주소가 올바르지 않습니다."); }
  if (u.protocol !== "https:" && u.hostname !== "127.0.0.1" && u.hostname !== "localhost") {
    throw new Error("https 주소만 사용할 수 있습니다.");
  }
  return `${u.origin}/api/admin/piku/collector/${path}`;
}

async function readTable() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("현재 탭을 찾지 못했습니다.");
  if (!/^https:\/\/www\.piku\.co\.kr\/w\/rank\//.test(tab.url || "")) {
    throw new Error("PIKU 랭킹 페이지에서 실행해 주세요.");
  }
  const [res] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["collect.js"],
  });
  const r = res && res.result;
  if (!r) throw new Error("표를 읽지 못했습니다.");
  return r;
}

/** 실패를 서버에 **실패로** 남긴다. 실패해도 조용히 넘어가지 않는다. */
async function reportFailure(base, division, kind) {
  if (!division) return;
  try {
    await fetch(ingestUrl(base, "failure"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ division, kind }),
    });
  } catch { /* 보고 실패까지 사용자에게 떠넘기지 않는다 */ }
}

$("check").addEventListener("click", async () => {
  show(null, "읽는 중…");
  try {
    const r = await readTable();
    if (!r.ok) {
      show(false, `${r.message}\n(종류: ${r.kind})`);
      await reportFailure($("base").value.trim(), null, r.kind);
      return;
    }
    const p = r.payload;
    show(true, `${p.division} · ${p.rowCount}행 읽음\n`
      + `1위 ${p.rows[0].streamer}\n`
      + "아직 보내지 않았습니다.");
  } catch (e) {
    show(false, e.message || String(e));
  }
});

/** 방금 보낸 수집의 지문. 같은 것을 두 번 보내지 않는다 — 토큰이 1회용이라
 *  서버가 막긴 하지만, 그때는 "실패"로 보여서 운영자가 헷갈린다. */
let lastSent = null;

$("run").addEventListener("click", async () => {
  const token = $("tok").value.trim();
  const base = $("base").value.trim();
  if (!token) { show(false, "수집 토큰을 붙여 넣어 주세요."); return; }
  $("run").disabled = true;
  show(null, "읽는 중…");
  let division = null;
  try {
    const r = await readTable();
    if (!r.ok) {
      show(false, `${r.message}\n(종류: ${r.kind})`);
      await reportFailure(base, null, r.kind);
      return;
    }
    division = r.payload.division;
    const fingerprint = `${division}:${r.payload.rowCount}:`
      + r.payload.rows.map((x) => `${x.rank}|${x.streamer}`).join(",");
    if (lastSent === fingerprint) {
      show(false, "같은 내용을 방금 보냈습니다. 표를 갱신한 뒤 다시 시도해 주세요.");
      return;
    }
    show(null, `${r.payload.rowCount}행 전송 중…`);
    const resp = await fetch(ingestUrl(base, "ingest"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Collector-Token": token },
      // 쿠키를 보내지 않는다 — 우리 서버에도, PIKU에도.
      credentials: "omit",
      body: JSON.stringify(r.payload),
    });
    const j = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      // 서버가 준 안전한 사유가 있으면 그것을 보여 준다 — "전송 실패 (HTTP 404)"만
      // 보이면 무엇을 고쳐야 하는지 알 수 없다. 404는 특히 헷갈리는데, 토큰이나
      // 표가 아니라 **NexBot 쪽 수집 경로가 아직 배포되지 않았다**는 뜻이다.
      const hint = resp.status === 404
        ? "
NexBot에 수집 경로가 없습니다(미배포). 운영자에게 알려 주세요."
        : "";
      show(false, (j.detail || `전송 실패 (HTTP ${resp.status})`) + hint
        + "
이 토큰은 다시 쓰지 말고 새로 발급받아 주세요.");
      // **자동 재시도하지 않는다.** 토큰이 1회용이라 재시도는 두 번째 실패를
      // 부르고, 실패 원인을 토큰 소진으로 덮어 버린다.
      return;
    }
    show(true, `${r.payload.rowCount}행을 보냈습니다.\n`
      + "아직 공개되지 않았습니다 — Nexadmin에서 이름 매핑을 확정한 뒤 "
      + "세 부문을 함께 공개하세요.");
    lastSent = fingerprint;
    $("tok").value = "";          // 1회용이므로 지운다
  } catch (e) {
    show(false, e.message || String(e));
    await reportFailure(base, division, "aborted");
  } finally {
    $("run").disabled = false;
  }
});
