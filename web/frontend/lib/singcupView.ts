// 싱드컵 탭의 화면 전환 규칙 — 순수 함수만 둔다.
//
// 컴포넌트에서 떼어 놓은 이유는 `lib/pollPolicy.ts`와 같다: 이 규칙의 값어치는
// 전부 "예전 링크를 어떻게 해석하는가"에 있고, 그건 렌더링 없이 검증할 수 있어야
// 한다(lib/singcupView.test.ts).
//
// 기본 화면은 **공식 예선 참가자**(치지직 발표)이고, 보조가 **NexBot 비공식 랭킹**이다.
// 주의할 점은 새 기본값이 기존 공유 링크를 조용히 깨뜨릴 수 있다는 것이다 —
// `?tab=singcup&sort=heart` 같은 **정렬만 담긴 링크**가 이미 밖에 나가 있는데, 그것을
// 공식 화면으로 열면 정렬이 무시돼 링크가 고장 난 것처럼 보인다. 그래서 `view`가
// 없더라도 `sort`/`dir`가 있으면 랭킹으로 보낸다.

export type SingcupView = "official" | "ranking" | "movers";

export const isSingcupView = (v: string | null): v is SingcupView =>
  v === "official" || v === "ranking" || v === "movers";

export interface SingcupViewDecision {
  view: SingcupView;
  /** true면 URL의 `view=`가 해석 불가라 주소창에서 지워야 한다. */
  normalize: boolean;
}

/**
 * 주소의 쿼리스트링만 보고 첫 화면을 정한다.
 *
 * - `view=official|ranking|movers` → 그대로
 * - 모르는 `view=`(폐지된 `view=board` 등) → 공식 + 주소창 정리
 * - `view` 없고 `sort`/`dir` 있음 → 랭킹(예전 공유 링크 호환)
 * - 그 외 → 공식
 */
export function readSingcupView(search: string): SingcupViewDecision {
  const p = new URLSearchParams(search);
  const raw = p.get("view");
  if (raw !== null && !isSingcupView(raw)) return { view: "official", normalize: true };
  if (isSingcupView(raw)) return { view: raw, normalize: false };
  if (p.has("sort") || p.has("dir")) return { view: "ranking", normalize: false };
  return { view: "official", normalize: false };
}

/**
 * 화면을 바꿀 때 주소를 어떻게 고칠지. URL 객체를 제자리에서 수정한다.
 *
 * 기본값(공식)은 주소에 남기지 않는다 — 공유 링크가 길어질 뿐이다. 그리고 공식
 * 화면에는 정렬 개념이 없으므로 `sort`/`dir`도 함께 지운다. 남겨 두면 다음에 그
 * 링크를 열 때 위의 예전-링크 호환 규칙이 랭킹으로 되돌려 버린다.
 */
export function applySingcupView(url: URL, view: SingcupView): void {
  if (view === "official") {
    url.searchParams.delete("view");
    url.searchParams.delete("sort");
    url.searchParams.delete("dir");
  } else {
    url.searchParams.set("view", view);
  }
}
