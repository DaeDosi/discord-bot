// 대시보드 API 실패를 **사용자가 할 일 기준으로** 분류한다.
//
// 왜 한 곳에 모으는가: 지금까지 각 화면이 실패를 `.catch(() => {})`로 삼키거나
// 빈 배열로 바꿔치기해서, 401·403·500·네트워크 오류가 전부 "데이터가 없습니다"와
// 똑같이 보였다(실측: 길드 목록 500 → "관리 권한이 있는 서버가 없습니다", 권한 없는
// 길드 403 → 설정 폼이 정상처럼 그려짐). 백엔드는 이미 상태 코드를 정확히 나눠
// 주고 있었고(deps.py: 401/403/503), 버려지던 쪽은 프론트였다.
//
// 서버가 준 `detail` 문자열은 **분류에만** 쓰고 화면에 싣지 않는다. 운영 백엔드의
// detail은 사용자에게 의미 없는 내부 문자열일 수 있다.
//
// 런타임 상대 import를 두지 않는다 — `node --test`는 확장자를 요구하고 번들러는
// 확장자 없는 경로를 원해서 둘을 동시에 만족시킬 수 없다. 그래서 ApiError를
// import하지 않고 `status`가 있는지로 **구조만** 본다.

export type DashboardErrorKind =
  | "invalid"        // 400/422 — 사용자가 넣은 값이 문제다
  | "unauthorized"   // 401 — 로그인이 풀렸다
  | "forbidden"      // 403 — 로그인은 됐지만 이 서버의 관리자가 아니다
  | "notFound"       // 404 — 대상이 없다
  | "conflict"       // 409 — 다른 변경과 부딪혔다. 다시 읽어야 한다
  | "rateLimited"    // 429 — 너무 자주 보냈다
  | "server"         // 5xx — 서버 쪽 일시적 실패
  | "network"        // 요청 자체가 나가지 못했다
  | "unknown";       // 상태를 알 수 없다(JSON 파싱 실패 포함)

export type DashboardErrorCopy = {
  kind: DashboardErrorKind;
  title: string;
  /** 화면 전체를 대체하는 오류 표면에서 쓴다(조회 실패 맥락). */
  detail: string;
  /** 저장·삭제 같은 **사용자 실행 동작**이 실패했을 때 쓴다. 조회 맥락 문구를 그대로
   *  쓰면 "설정을 볼 수 있습니다" 처럼 상황과 어긋난 안내가 된다. */
  actionDetail: string;
  /** 같은 요청을 다시 보내면 결과가 달라질 수 있는가.
   *  권한 문제에 '다시 시도'를 두면 몇 번을 눌러도 같은 화면이라 사용자가 갇힌다. */
  retryable: boolean;
};

export const DASHBOARD_ERROR_COPY: Record<DashboardErrorKind, Omit<DashboardErrorCopy, "kind">> = {
  invalid: {
    title: "입력값을 확인해 주세요",
    detail: "요청 값이 올바르지 않습니다.",
    actionDetail: "입력한 값을 확인한 뒤 다시 저장해 주세요.",
    retryable: false,
  },
  unauthorized: {
    title: "로그인이 필요합니다",
    detail: "세션이 만료되었습니다. 다시 로그인해 주세요.",
    actionDetail: "로그인이 만료되었습니다. 다시 로그인한 뒤 시도해 주세요.",
    retryable: false,
  },
  forbidden: {
    title: "이 서버를 관리할 권한이 없습니다",
    detail: "서버 관리 권한(서버 관리 또는 관리자)이 있는 계정으로만 설정을 볼 수 있습니다.",
    actionDetail: "서버 관리 권한(서버 관리 또는 관리자)이 있는 계정으로만 변경할 수 있습니다.",
    retryable: false,
  },
  notFound: {
    title: "대상을 찾을 수 없습니다",
    detail: "주소가 잘못되었거나 봇이 더 이상 이 서버에 없습니다.",
    actionDetail: "이미 삭제되었거나 봇이 서버에서 나갔을 수 있습니다. 새로고침해 주세요.",
    retryable: false,
  },
  conflict: {
    title: "다른 변경과 충돌했습니다",
    detail: "그 사이에 값이 바뀌었습니다. 새로고침한 뒤 다시 확인해 주세요.",
    actionDetail: "그 사이에 다른 곳에서 값이 바뀌었습니다. 새로고침한 뒤 다시 저장해 주세요.",
    retryable: false,
  },
  rateLimited: {
    title: "요청이 너무 잦습니다",
    detail: "잠시 후 다시 시도해 주세요.",
    actionDetail: "잠시 후 다시 시도해 주세요.",
    retryable: true,
  },
  server: {
    title: "일시적인 문제가 발생했습니다",
    detail: "잠시 후 다시 시도해 주세요. 계속되면 지원 서버로 알려 주세요.",
    actionDetail: "잠시 후 다시 시도해 주세요. 계속되면 지원 서버로 알려 주세요.",
    retryable: true,
  },
  network: {
    title: "연결하지 못했습니다",
    detail: "네트워크 상태를 확인한 뒤 다시 시도해 주세요.",
    actionDetail: "네트워크 상태를 확인한 뒤 다시 시도해 주세요.",
    retryable: true,
  },
  unknown: {
    title: "처리하지 못했습니다",
    detail: "잠시 후 다시 시도해 주세요.",
    actionDetail: "잠시 후 다시 시도해 주세요.",
    retryable: true,
  },
};

/** 오류 객체에서 HTTP 상태를 꺼낸다. 없으면 null(= 네트워크/알 수 없음). */
function statusOf(err: unknown): number | null {
  if (err && typeof err === "object" && "status" in err) {
    const s = (err as { status: unknown }).status;
    if (typeof s === "number") return s;
  }
  return null;
}

export function classifyDashboardError(err: unknown): DashboardErrorKind {
  const status = statusOf(err);
  if (status === 400 || status === 422) return "invalid";
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "notFound";
  if (status === 409) return "conflict";
  if (status === 429) return "rateLimited";
  if (status !== null && status >= 500) return "server";
  if (status !== null) return "unknown";

  // 상태가 없으면 요청 자체가 실패했을 가능성이 높다. fetch는 이때 TypeError를 던진다.
  if (err instanceof TypeError) return "network";
  const msg = err instanceof Error ? err.message : "";
  // 응답 본문이 JSON이 아니면 SyntaxError가 난다 — 서버 상태를 알 수 없으므로 일반 오류다.
  if (err instanceof SyntaxError) return "unknown";
  if (/fetch|network|Load failed/i.test(msg)) return "network";
  return "unknown";
}

export function dashboardErrorCopy(err: unknown): DashboardErrorCopy {
  const kind = classifyDashboardError(err);
  return { kind, ...DASHBOARD_ERROR_COPY[kind] };
}

/** 사용자 실행 동작(저장·삭제·토글)이 실패했을 때 한 줄로 보여 줄 문구. */
export function mutationErrorMessage(err: unknown): string {
  const copy = dashboardErrorCopy(err);
  // 400은 서버가 "무엇이 잘못됐는지"를 사용자 언어로 알려 주는 유일한 경우다
  // (예: 트리거에 공백을 쓸 수 없습니다). 그 값이 안전할 때만 그대로 쓴다.
  if (copy.kind === "invalid") {
    const field = safeFieldMessage(err);
    if (field) return field;
  }
  return copy.actionDetail;
}

// 서버 메시지에 이런 게 섞여 있으면 사용자에게 보여 주지 않는다.
const UNSAFE_DETAIL = [
  /https?:\/\//i, /\/api\//, /[A-Za-z]:\\/, /\.py\b/, /\.env\b/,
  /Traceback/i, /Exception|Error:/i, /SELECT |INSERT |UPDATE |DELETE FROM/i,
  /localhost|127\.0\.0\.1|railway\.app/i, /[A-Z_]{6,}=/,
];

/** 400 응답의 `detail`을 필드 옆 검증 문구로 쓸 수 있는지 판정한다.
 *
 *  왜 통째로 막지 않는가: 트리거 유효성처럼 **서버만 아는 규칙**이 있고, 그걸
 *  "입력값을 확인해 주세요"로 뭉개면 사용자가 무엇을 고쳐야 할지 알 수 없다.
 *  대신 내부 정보로 보이는 문자열과 지나치게 긴 값은 거른다. */
export function safeFieldMessage(err: unknown): string | null {
  if (classifyDashboardError(err) !== "invalid") return null;
  const raw = err instanceof Error ? err.message : "";
  const msg = raw.trim();
  if (!msg || msg.length > 120) return null;
  if (/^HTTP \d+$/.test(msg)) return null;
  // 이 프로젝트의 사용자용 검증 문구는 전부 한국어다(백엔드 라우터 관례). 한글이 없으면
  // 개발자용 문자열로 본다 — 실측으로 `internal` 같은 값이 그대로 새어 나왔다.
  if (!/[가-힣]/.test(msg)) return null;
  if (UNSAFE_DETAIL.some((re) => re.test(msg))) return null;
  return msg;
}

/** 401은 api.ts가 이미 토큰 삭제 + /login 이동을 처리한다. 화면에 오류를 그릴 필요가 없다. */
export function isHandledElsewhere(err: unknown): boolean {
  return classifyDashboardError(err) === "unauthorized";
}
