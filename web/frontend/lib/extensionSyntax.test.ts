/* 확장 스크립트가 **파싱되는지**를 고정한다.
 *
 * 2026-08-18, `popup.js:113`에서 실제 `Uncaught SyntaxError: Invalid or unexpected
 * token`이 났다. 문자열 안에 `\n`(두 글자 이스케이프)이 아니라 **진짜 개행 바이트**가
 * 들어가 문자열이 그 줄에서 닫히지 않았다. 확장은 Chrome이 로드할 때 처음 파싱되므로
 * 팝업을 열기 전까지 아무도 몰랐다.
 *
 * 기존 확장 테스트는 소스를 **문자열로 검사**하기만 했다(어떤 토큰이 있는지/없는지).
 * 그래서 "파싱조차 안 되는 파일"을 통과시켰다. 여기서는 실제로 **컴파일**한다.
 *
 * `new vm.Script(...)`는 컴파일만 하고 실행하지 않는다 — `collect.js`는 즉시
 * 실행 함수라 실행하면 DOM을 건드리려 든다. 컴파일만으로 문법은 전부 검증된다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const FILES = ["popup.js", "collect.js"];

/* CR 바이트 검사를 넣었다가 뺐다. git이 체크아웃할 때 `core.autocrlf`로 LF를
 * CRLF로 바꾸므로 **새 worktree마다 실패한다**(실측: ui-w 체크아웃 직후 실패).
 * 줄끝은 작업 트리의 산출물이지 소스 결함이 아니고, CRLF도 JS로 정상 파싱된다.
 * 잡아야 할 것은 "문자열 안의 개행"이고 그건 아래 컴파일 검사가 잡는다. */

const read = (f: string) =>
  readFileSync(new URL(`../../../tools/piku-collector-extension/${f}`, import.meta.url), "utf8");

for (const f of FILES) {
  test(`${f}는 문법 오류 없이 파싱된다`, () => {
    const src = read(f);
    assert.doesNotThrow(() => { new vm.Script(src, { filename: f }); },
      `${f}가 파싱되지 않는다 — Chrome이 확장을 로드하지 못한다`);
  });
}

test("popup.js의 404 안내가 실제 줄바꿈 이스케이프를 쓴다", () => {
  // 깨졌을 때의 모습: `? "` 뒤에 곧바로 줄이 끝난다.
  const src = read("popup.js");
  assert.ok(src.includes('"\\nNexBot에 수집 경로가 없습니다'),
    "404 안내의 줄바꿈이 이스케이프가 아니다");
  assert.ok(src.includes('"\\n이 토큰은 다시 쓰지 말고'),
    "토큰 재발급 안내의 줄바꿈이 이스케이프가 아니다");
});

/* 줄 단위로 따옴표 개수를 세어 "어느 줄인지" 알려 주는 검사를 넣었다가 뺐다.
 * 주석 안의 한글 따옴표(`그건 "이미 렌더된 것만 읽는다"는 약속`)와 URL의 `//`를
 * 오탐한다. 주석·문자열을 정확히 가르려면 결국 토크나이저가 필요한데, 그게 바로
 * 위의 `vm.Script` 컴파일이다 — 그쪽이 엄밀히 더 강하고 오탐이 없다.
 * 컴파일 실패 메시지에 파일명과 줄 번호가 함께 나오므로 진단도 충분하다.
 */
