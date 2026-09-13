/* 수정 요청 폼의 **입력 안내·오류 문구** 규칙(PUBLIC-UX-1).
 *
 * 판정자는 서버(`web/backend/support.py`)다. 여기 규칙은 사용자가 보내기 전에 고칠 수
 * 있게 돕는 **같은 모양의 안내**일 뿐이다 — 서버 규칙을 바꾸면 이 파일도 함께 바꾼다
 * (계약 테스트가 두 정규식을 대조한다).
 *
 * 런타임 import가 없다(`node --test`로 바로 돈다).
 */

/** 서버 `_URL_RE`와 같은 모양. https만. */
const URL_RE = /^https:\/\/[A-Za-z0-9.-]+(?::\d{1,5})?(?:\/[^\s]*)?$/;
/** 서버 `_CLIP_ID_RE`와 같은 모양. */
const CLIP_ID_RE = /^[A-Za-z0-9_-]{2,100}$/;

/** 대상 식별자의 문제. 문제가 없으면 빈 문자열. */
export function clipRefProblem(raw: string): string {
  const v = raw.trim();
  if (!v) return "클립 주소 또는 ID를 입력해 주세요.";
  if (/^https:\/\//i.test(v)) {
    return URL_RE.test(v) ? "" : "주소 형식이 올바르지 않습니다.";
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(v)) return "https:// 로 시작하는 주소만 받습니다.";
  return CLIP_ID_RE.test(v) ? ""
    : "클립 주소(https://로 시작) 또는 클립 ID(영문·숫자)를 입력해 주세요.";
}

/** 제출 실패 → 사용자 문구. `status` 0은 연결 실패다.
 *
 *  400(입력 오류)만 서버 문구를 그대로 쓴다 — 서버가 만든 고정 안내라 내부 정보가 없다.
 *  나머지는 **할 수 있는 일**을 기준으로 이 파일이 정한다(서버 원문에 기대지 않는다). */
export function correctionErrorMessage(status: number, serverMessage: string): string {
  switch (status) {
    case 400:
      return serverMessage || "입력한 내용을 다시 확인해 주세요.";
    case 409:
      return "같은 내용이 이미 접수되어 있습니다. 새로 알릴 내용이 있으면 설명을 보태 주세요.";
    case 429:
      return "짧은 시간에 요청이 많았습니다. 10분쯤 뒤에 다시 보내 주세요.";
    case 503:
      return "일시적으로 접수할 수 없습니다. 잠시 후 다시 시도해 주세요.";
    case 0:
      return "접수 서버에 연결하지 못했습니다. 네트워크를 확인한 뒤 다시 시도해 주세요.";
    default:
      return "접수하지 못했습니다. 잠시 후 다시 시도해 주세요.";
  }
}
