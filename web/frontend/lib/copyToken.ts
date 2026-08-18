/* 수집 토큰을 클립보드로 옮긴다. **그것만 한다.**
 *
 * 토큰은 10분·1회용이고 화면에 딱 한 번 나온다. 손으로 드래그하다 앞뒤가 잘리면
 * 그 토큰은 버려야 하고, 새로 발급받는 동안 만료 시계가 다시 돈다. 복사 버튼은
 * 그 사고를 없애려는 것이지 토큰을 어딘가에 보관하려는 것이 아니다.
 *
 * 그래서 이 파일에는 저장소·로그·네트워크 호출이 **하나도 없다**. 복사는 순전히
 * 브라우저 안의 일이라 서버 상태를 건드리지 않는다 — 복사했다고 토큰이 소비되지
 * 않는다(소비는 확장이 `ingest`를 부를 때 서버가 판정한다).
 */

/** `복사됨` → `복사`로 돌아가는 시간. */
export const COPY_RESET_MS = 2000;

export type CopyResult = "copied" | "failed";

export interface CopyDeps {
  navigator?: Pick<Navigator, "clipboard"> | Record<string, never>;
  /** Clipboard API를 못 쓸 때의 대체 경로. 성공 여부를 돌려준다. */
  fallback?: (text: string) => boolean;
}

/** `document.execCommand("copy")` 기반 대체 경로.
 *
 * http 로컬 주소·구형 브라우저·권한 거부에서 Clipboard API가 막힌다. 그때
 * "복사됐다"고 거짓말하지 않으려면 대체 경로가 필요하다. 임시 textarea는 화면
 * 밖에 두고 **즉시 제거**한다 — 토큰이 DOM에 남아 있지 않게 한다.
 */
function execCommandFallback(text: string): boolean {
  if (typeof document === "undefined") return false;
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.setAttribute("aria-hidden", "true");
  ta.style.position = "fixed";
  ta.style.top = "-1000px";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  try {
    ta.select();
    ta.setSelectionRange(0, text.length);   // 모바일 Safari는 select()만으로 부족하다
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    ta.remove();
  }
}

export async function copyToken(
  token: string, deps: CopyDeps = {},
): Promise<CopyResult> {
  const text = (token || "").trim();
  // 빈 값을 "복사됨"으로 보여 주면 붙여넣기가 조용히 빈 문자열이 된다.
  if (!text) return "failed";

  const nav = deps.navigator
    ?? (typeof navigator !== "undefined" ? navigator : undefined);
  const clip = (nav as Navigator | undefined)?.clipboard;
  if (clip && typeof clip.writeText === "function") {
    try {
      await clip.writeText(text);
      return "copied";
    } catch {
      // 권한 거부·비보안 컨텍스트. 조용히 성공으로 넘기지 않고 대체 경로로 간다.
    }
  }

  const fb = deps.fallback ?? execCommandFallback;
  return fb(text) ? "copied" : "failed";
}
