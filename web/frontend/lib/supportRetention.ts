// 수정 요청 보관 정책(SUPPORT-POLICY-1, A 균형형 + 보정 2건)의 **공개 문구 정본**.
//
// 숫자는 서버 정본 `web/backend/support_retention.py`와 같아야 한다 — 두 쪽의 계약 테스트
// (`tests/test_support_retention.py`, `lib/supportRetention.test.ts`)가 글자 그대로 대조한다.
// 공개 폼과 개인정보처리방침이 **같은 문장**을 여기서 가져간다(화면마다 따로 적으면 갈라진다).
//
// 문구 원칙: 법률 판단처럼 단정하지 않고 **제품이 실제로 하는 일**만 적는다.
// 백업 보관 기간은 확인되지 않았으므로 숫자를 적지 않는다.
//
// 런타임 import가 없다(node --test와 번들러를 동시에 만족시키기 위해).

export const DUPLICATE_CHECK_CLEAR_DAYS = 7;
export const EMAIL_MAX_DAYS_AFTER_CREATED = 180;
export const EMAIL_DAYS_AFTER_CLOSED = 30;
export const OPEN_MAX_DAYS = 365;
export const CLOSED_DAYS = 180;
/** 요청 행 절대 상한(SUPPORT-POLICY-1b) — 새 기간이 아니라 OPEN_MAX_DAYS + CLOSED_DAYS. */
export const ABSOLUTE_MAX_DAYS = 545;

export const DAY_SECONDS = 86400;

/** 공개 폼·개인정보처리방침이 함께 쓰는 문장. */
export const CORRECTION_RETENTION_COPY = {
  purpose:
    "입력하신 내용은 수정 요청을 확인하고 처리하기 위해 저장합니다.",
  email:
    `답변받을 이메일은 선택 항목이며 회신 목적으로만 사용합니다. ` +
    `처리가 끝난 뒤 ${EMAIL_DAYS_AFTER_CLOSED}일 또는 접수 후 ${EMAIL_MAX_DAYS_AFTER_CREATED}일 중 ` +
    `먼저 오는 때에 삭제합니다.`,
  open:
    `처리되지 않은 요청은 접수 후 최대 1년(${OPEN_MAX_DAYS}일)까지 보관한 뒤 삭제합니다.`,
  closed:
    `처리가 끝난 요청(반영 완료·반영 안 함)은 처리 후 ${CLOSED_DAYS}일이 지나면 삭제합니다.`,
  absoluteMax:
    `어떤 경우에도 요청은 접수 후 ${ABSOLUTE_MAX_DAYS}일을 넘겨 보관하지 않습니다.`,
  duplicateCheck:
    `같은 요청이 반복 접수되는지 확인하는 값(원문을 알 수 없는 해시)은 접수 후 ` +
    `${DUPLICATE_CHECK_CLEAR_DAYS}일이 지나면 지웁니다. IP 주소 원문은 저장하지 않습니다.`,
  minimize:
    "문제 설명에는 실명·연락처 등 처리에 필요하지 않은 개인정보를 적지 말아 주세요.",
  deletion:
    "보관 기간 전에 삭제를 원하시면 접수 번호와 함께 문의 이메일로 요청해 주세요. " +
    "요청하신 분의 접수 건인지 확인할 수 있는 범위에서 삭제합니다.",
  timing:
    "정리 작업은 하루 한 번 실행되므로 실제 삭제는 기준 시점보다 하루 안팎 늦어질 수 있습니다.",
  backup:
    "삭제한 정보도 장애 대비 백업 사본에는 일정 기간 남아 있을 수 있습니다.",
} as const;
