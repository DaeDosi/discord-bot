/* PUBLIC-UX-1 계약 — 수정 요청 복구(공개 폼 · 오류 구분 · Nexadmin 처리 탭).
 *
 * 입력 안내 규칙은 값으로, 화면은 렌더되는 코드(주석 제거)로 본다. 판정자는 서버다
 * (`tests/test_public_ux1_support.py`). 사용자 입력은 **평문**이고 어디서도 HTML로 해석하지 않는다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { clipRefProblem, correctionErrorMessage } from "./correctionForm.ts";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
   .replace(/^\s*\/\/.*$/gm, "");

test("대상 식별자 안내는 서버 규칙과 같은 모양이다", () => {
  for (const ok of ["https://chzzk.naver.com/clips/abc", "https://nexbot.shop/stats?tab=singcup",
                    "abcDEF123", "clip_01-x"]) {
    assert.equal(clipRefProblem(ok), "", ok);
  }
  for (const bad of ["", "그 노래 클립", "http://chzzk.naver.com/clips/x",
                     "javascript:alert(1)", "https://", "a b", "<script>"]) {
    assert.notEqual(clipRefProblem(bad), "", bad);
  }
  // 서버 정규식과 글자 그대로 대조한다(한쪽만 바뀌면 안내와 판정이 갈라진다).
  const py = read("../backend/support.py");
  const ts = read("lib/correctionForm.ts");
  assert.ok(py.includes('_CLIP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,100}$")'));
  assert.ok(ts.includes("const CLIP_ID_RE = /^[A-Za-z0-9_-]{2,100}$/;"));
});

test("실패 문구는 상태별로 할 일을 알려 주고 서버 원문은 400에서만 쓴다", () => {
  assert.equal(correctionErrorMessage(400, "문제 설명은(는) 10자 이상"), "문제 설명은(는) 10자 이상");
  assert.match(correctionErrorMessage(409, "x"), /이미 접수/);
  assert.match(correctionErrorMessage(429, "x"), /다시 보내/);
  assert.match(correctionErrorMessage(503, "Traceback SQL"), /일시적/);
  assert.ok(!correctionErrorMessage(503, "Traceback SQL").includes("Traceback"));
  assert.ok(!correctionErrorMessage(500, "Internal SQL").includes("SQL"));
  assert.match(correctionErrorMessage(0, ""), /연결하지 못했습니다/);
});

test("수정 요청 화면이 장애·미설정·접수 가능을 구분하고 설정 이름을 노출하지 않는다", () => {
  const s = read("app/support/correction/page.tsx");
  assert.ok(s.includes('metaState === "failed"') && s.includes("다시 시도"));
  assert.ok(s.includes("meta.accepting === false"));
  assert.ok(s.includes("clipRefProblem(clipRef)"));
  assert.ok(s.includes("aria-invalid"), "잘못된 식별자를 보조기기에 알리지 않는다");
  for (const bad of ["SUPPORT_HASH_SALT", "salt", "환경변수", "dangerouslySetInnerHTML"]) {
    assert.ok(!s.includes(bad), `공개 화면에 내부 설정 단서가 있다: ${bad}`);
  }
  assert.ok(s.includes("그 밖의 문의") && s.includes('href="/contact"'));
});

test("Nexadmin 수정 요청 탭은 OWNER API만 쓰고 입력을 텍스트 노드로만 그린다", () => {
  const p = read("app/nexadmin/SupportCorrectionsPanel.tsx");
  const page = read("app/nexadmin/page.tsx");
  assert.ok(page.includes('{ key: "support_corrections", label: "수정 요청" }'));
  assert.ok(page.includes('activeTab === "support_corrections" && <SupportCorrectionsPanel />'));
  assert.ok(p.includes("api.admin.supportCorrections(") && p.includes("api.admin.setCorrectionStatus("));
  const c = code(p);
  for (const bad of ["dangerouslySetInnerHTML", "innerHTML", "DOMParser", "insertAdjacentHTML"]) {
    assert.ok(!c.includes(bad), `요청 내용을 HTML로 해석할 수 있다: ${bad}`);
  }
  // 사용자 입력 필드는 JSX 텍스트 자식으로만 들어간다.
  for (const f of ["item.description", "item.desiredFix", "item.contactEmail"]) {
    assert.ok(c.includes(`>{${f}}<`), `${f}가 텍스트 노드로 렌더되지 않는다`);
  }
  assert.ok(p.includes('rel="noopener noreferrer nofollow"'));
  assert.ok(/isHttps = \(v: string\) => \/\^https:/.test(p), "https가 아닌 값을 링크로 만든다");
  assert.ok(p.includes("status: prev"), "상태 변경 실패 시 되돌리지 않는다");
  assert.ok(read("lib/api.ts").includes("/api/admin/support/corrections"));
});

test("서버는 평문을 저장하고 태그를 걷어내 다시 조립하지 않는다", () => {
  const py = read("../backend/support.py");
  assert.ok(!py.includes("_TAG_RE"), "태그 제거 정규식이 되살아났다");
  assert.ok(py.includes("평문으로 저장하고 HTML로 해석하지 않는다"));
});
