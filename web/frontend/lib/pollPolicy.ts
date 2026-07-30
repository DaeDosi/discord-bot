// 폴링 판단 규칙만 따로 뽑아 둔다 — 훅 안에 두면 React 없이는 검증할 수 없다.
// (타이머·이벤트 리스너 배선은 훅에 남고, '부를지 말지'는 여기서 결정한다.)

/** 배경 탭에서는 요청을 만들지 않는다.
 *
 *  브라우저는 숨겨진 탭의 타이머를 완전히 멈추지 않고 1분 이하로만 늦춘다. 그래서
 *  이 검사가 없으면 아무도 보고 있지 않은 화면이 계속 큰 응답을 받아 간다. */
export function shouldPollNow(visibility: DocumentVisibilityState): boolean {
  return visibility === "visible";
}

/** 탭에 돌아왔을 때 다시 받아올지.
 *
 *  잠깐 다른 탭을 봤다 온 것뿐이면 부르지 않는다 — 탭을 오갈 때마다 요청이 나가면
 *  폴링 주기를 늘린 의미가 없다. 캐시가 아예 없을 때(null)는 받아야 한다. */
export function shouldRefetchOnRevisit(
  ageMs: number | null,
  staleMs: number,
): boolean {
  if (ageMs === null) return true;
  return ageMs >= staleMs;
}
