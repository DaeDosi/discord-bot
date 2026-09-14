/* SUPPORT-POLICY-1 계약 — 수정 요청 보관 정책의 공개 문구 · Nexadmin 보관/삭제 화면.
 *
 * 판정자는 서버 정리 작업이다(`tests/test_support_retention.py`). 여기서는
 *  · 공개 문구의 숫자가 서버 정본과 같은지
 *  · 폼과 개인정보처리방침이 **같은 문장**을 쓰는지
 *  · Nexadmin 삭제가 확인 단계·공통 mutation 규칙을 거치는지
 * 를 고정한다.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ABSOLUTE_MAX_DAYS, CLOSED_DAYS, CORRECTION_RETENTION_COPY as COPY, DUPLICATE_CHECK_CLEAR_DAYS,
  EMAIL_DAYS_AFTER_CLOSED, EMAIL_MAX_DAYS_AFTER_CREATED, OPEN_MAX_DAYS,
} from "./supportRetention.ts";

const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), "utf8");
const code = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
   .replace(/^\s*\/\/.*$/gm, "");

test("공개 숫자는 서버 정본과 같다", () => {
  const py = read("../backend/support_retention.py");
  const days = (name: string) => {
    const m = py.match(new RegExp(`^${name} = (\\d+) \\* DAY$`, "m"));
    assert.ok(m, `${name}를 서버에서 찾지 못했다`);
    return Number(m![1]);
  };
  assert.equal(DUPLICATE_CHECK_CLEAR_DAYS, days("DEDUPE_CLEAR_AFTER"));
  assert.equal(EMAIL_MAX_DAYS_AFTER_CREATED, days("EMAIL_MAX_AFTER_CREATED"));
  assert.equal(EMAIL_DAYS_AFTER_CLOSED, days("EMAIL_AFTER_CLOSED"));
  assert.equal(OPEN_MAX_DAYS, days("OPEN_MAX_RETENTION"));
  assert.equal(CLOSED_DAYS, days("CLOSED_RETENTION"));
  // 절대 상한은 새 기간이 아니라 두 규칙의 합이다(서버도 같은 식으로 정의).
  assert.ok(py.includes("ABSOLUTE_MAX_RETENTION = OPEN_MAX_RETENTION + CLOSED_RETENTION"));
  assert.equal(ABSOLUTE_MAX_DAYS, OPEN_MAX_DAYS + CLOSED_DAYS);
  assert.equal(ABSOLUTE_MAX_DAYS, 545);
  // 사용자 승인 정책(A 균형형 + 보정) 그대로
  assert.deepEqual([DUPLICATE_CHECK_CLEAR_DAYS, EMAIL_MAX_DAYS_AFTER_CREATED, EMAIL_DAYS_AFTER_CLOSED,
                    OPEN_MAX_DAYS, CLOSED_DAYS], [7, 180, 30, 365, 180]);
});

test("문장이 실제 정리 계산과 같은 기간을 말한다", () => {
  assert.match(COPY.email, /처리가 끝난 뒤 30일 또는 접수 후 180일 중 먼저 오는 때에 삭제/);
  assert.match(COPY.email, /선택 항목/);
  assert.match(COPY.email, /회신 목적으로만/);
  assert.match(COPY.open, /접수 후 최대 1년\(365일\)/);
  assert.match(COPY.absoluteMax, /어떤 경우에도 요청은 접수 후 545일을 넘겨 보관하지 않습니다/);
  assert.match(COPY.closed, /처리 후 180일/);
  assert.match(COPY.duplicateCheck, /접수 후 7일/);
  assert.match(COPY.duplicateCheck, /IP 주소 원문은 저장하지 않습니다/);
  assert.match(COPY.deletion, /문의 이메일/);
  assert.match(COPY.minimize, /개인정보를 적지 말아/);
  // 확인되지 않은 백업 보관 기간을 숫자로 단정하지 않는다.
  assert.ok(!/\d/.test(COPY.backup), "백업 보관 기간을 숫자로 적었다");
  for (const s of Object.values(COPY)) {
    assert.ok(!/무기한|영구/.test(s), "무기한 보관 표현");
    assert.ok(!/6개월/.test(s), "문의 이메일 기간(6개월)과 섞였다");
  }
});

test("공개 폼은 정본 문장을 보여 주고 숫자를 직접 적지 않는다", () => {
  const s = read("app/support/correction/page.tsx");
  const c = code(s);
  for (const key of ["purpose", "email", "open", "closed", "absoluteMax", "deletion"]) {
    assert.ok(c.includes(`{RETENTION.${key}}`), `폼에 ${key} 안내가 없다`);
  }
  assert.ok(c.includes("{RETENTION.minimize}"), "개인정보를 적지 말라는 안내가 없다");
  assert.ok(c.includes('href="/privacy"'));
  assert.ok(!/\d+일/.test(c), "폼이 기간 숫자를 직접 적었다");
});

test("개인정보처리방침이 폼과 같은 문장을 쓰고 시행일을 갱신했다", () => {
  const s = read("app/privacy/page.tsx");
  const c = code(s);
  assert.ok(s.includes('from "@/lib/supportRetention"'));
  for (const key of ["open", "closed", "absoluteMax", "email", "duplicateCheck", "timing", "backup",
                     "deletion",
                     "purpose", "minimize"]) {
    assert.ok(c.includes(`RETENTION.${key}`), `방침에 ${key}가 없다`);
  }
  assert.ok(c.includes('const EFFECTIVE_DATE = "2026년 9월 14일"'));
  assert.ok(c.includes("수정 요청 폼으로 접수된 내용"));
  // 이메일 문의(6개월)는 그대로 두되 수정 요청과 구분된 제목을 갖는다.
  assert.ok(c.includes("이메일로 보내신 문의와 첨부 자료"));
  assert.ok(!/수정 요청[^"]*6개월/.test(c), "수정 요청에 6개월을 적었다");
});

test("Nexadmin은 서버가 계산한 예정일과 정리 모드를 그대로 보여 준다", () => {
  const p = code(read("app/nexadmin/SupportCorrectionsPanel.tsx"));
  for (const f of ["item.statusChangedAt", "item.emailRemovalDueAt", "item.deletionDueAt",
                   "item.emailClearedAt", "retention.mode"]) {
    assert.ok(p.includes(f), `${f}를 표시하지 않는다`);
  }
  assert.ok(p.includes("점검 모드"), "dry-run 상태를 숨긴다");
  // 단계적 활성화 점검용 상태(SUPPORT-POLICY-1b)
  for (const f of ["retention.enabled", "retention.dryRun", "retention.workerRunning",
                   "retention.intakeReady", "retention.saltConfigured", "retention.lastRun",
                   "retention.consecutiveFailures", "candidates.candidateDuplicateCheckClearCount",
                   "candidates.candidateEmailClearCount", "candidates.candidateDeleteCount",
                   "retention.policy.absoluteMaxDays", "item.deletionCapped"]) {
    assert.ok(p.includes(f), `${f}를 표시하지 않는다`);
  }
  assert.ok(p.includes("보관 기간이 지나 제거됨"));
  // 화면이 기간을 따로 계산하지 않는다(서버와 갈라진다).
  assert.ok(!/\*\s*86400|86400\s*\*/.test(p), "화면이 예정일을 직접 계산한다");
  const types = read("lib/types.ts");
  assert.ok(types.includes("emailRemovalDueAt: number | null"));
  assert.ok(types.includes("deletionDueAt: number | null"));
});

test("Nexadmin 삭제는 확인 단계와 공통 mutation 규칙을 거친다", () => {
  const p = code(read("app/nexadmin/SupportCorrectionsPanel.tsx"));
  assert.ok(p.includes('from "@/lib/useMutation"') && p.includes("useMutation("));
  assert.ok(p.includes("del.run(() => api.admin.deleteCorrection(item.id)"));
  // 삭제 버튼은 확인 상자를 열 뿐이고, 실제 요청은 "삭제 확정"에서만 나간다.
  const openBtn = p.indexOf("setConfirmId(item.id)");
  const confirmBtn = p.indexOf("onClick={() => removeItem(item)}");
  assert.ok(openBtn > 0 && confirmBtn > openBtn);
  assert.ok(p.includes("confirmId === item.id"));
  assert.ok(p.includes("{statusLabel(item.status)}"), "확인 문구에 상태가 없다");
  assert.ok(p.includes("<b className=\"tabular-nums\">#{item.id}</b>"), "확인 문구에 번호가 없다");
  assert.ok(p.includes("되돌릴 수 없습니다"));
  assert.ok(p.includes("disabled={del.pending}"), "삭제 확정 연타를 막지 않는다");
  assert.ok(p.includes("if (del.pending) return;"));
  assert.ok(p.includes("await load(filter)"), "삭제 후 목록·집계를 다시 읽지 않는다");
  for (const bad of ["window.confirm", "confirm(", "alert(", "dangerouslySetInnerHTML"]) {
    assert.ok(!p.includes(bad), `금지된 패턴: ${bad}`);
  }
  const api = read("lib/api.ts");
  assert.ok(api.includes("`/api/admin/support/corrections/${id}`, { method: \"DELETE\" }"));
  // 이용자용 공개 삭제 경로는 없다.
  assert.ok(!api.includes("/api/support/correction/${"));
});
