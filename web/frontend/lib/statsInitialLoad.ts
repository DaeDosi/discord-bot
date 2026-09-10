/* `/stats` 첫 진입의 **적재 계획**. 화면(JSX)에서 떼어 내 실제로 실행해 볼 수 있게 했다.
 *
 * 왜 모듈로 뽑았나 — 이 규칙이 깨지면 화면이 느려지는데, 그 사실은 소스를 눈으로
 * 봐서는 드러나지 않는다. 여기 있는 함수는 가짜 api를 주입해 **호출된 엔드포인트와
 * 시작 순서를 그대로 관찰**할 수 있다(`lib/statsInitialLoad.test.ts`).
 *
 * ## 배경 — 무엇이 느렸나 (운영 실측, 2026-09-10)
 *
 * 예전에는 첫 진입에서 다섯 API를 하나의 `Promise.all`에 묶고, **다섯이 전부 끝나야**
 * 본문을 그렸다. 그런데 첫 화면(개요 탭)이 쓰는 것은 `overview`·`rising-stars`
 * 둘뿐이고 나머지 셋은 다른 탭 전용이었다. 1440px 실측:
 *
 *     overview      232~1133ms   ← 개요가 쓰는 것
 *     rising-stars  244~ 621ms   ← 개요가 쓰는 것
 *     live-ranking  232~ 704ms   (랭킹 탭 전용, 117KB)
 *     categories    243~ 634ms   (카테고리 탭 전용)
 *     newcomers     244~3078ms   (신규 탭 전용)  ← 게이트를 여기까지 끌고 갔다
 *
 * 개요 데이터는 1133ms에 준비됐는데 화면은 **3078ms**까지 스피너였다.
 *
 * ## `newcomers`를 프리페치조차 하지 않는 이유
 *
 * 백엔드에 60초 TTL 캐시가 있지만 **미스 비용이 2.8~3.0초**다
 * (70초 간격 4회 측정: 2794 / 2861 / 2990 / 2927ms, 히트는 315~358ms).
 * 게다가 그 계산이 도는 동안 **같은 프로세스의 다른 요청까지 밀린다** — 한 회차에서
 * `newcomers`가 캐시 히트(383ms)였는데도 나머지 넷이 3.9초까지 늘어난 것을 실측했다.
 *
 * 그래서 이 데이터는 "나중에 조용히 받아 두는" 대상이 아니라 **그 탭을 열 때만**
 * 받는 대상이다. 백그라운드 프리페치로 되돌리면 첫 화면이 다시 느려진다.
 */

/** 탭 전용 데이터의 적재 상태.
 *
 *  `idle`을 따로 두는 이유: "아직 요청하지 않음"과 "요청했는데 응답 대기"는 다르다.
 *  둘을 합치면 탭을 열자마자 한 프레임 동안 실패 문구가 스치거나, 반대로 요청이
 *  시작되지 않았는데 영원히 스피너가 돈다. */
export type LoadState = "idle" | "loading" | "error" | "ready";

/** 탭을 열 때만 받는 데이터의 키. */
export type LazyKey = "rank" | "cats" | "news";

/** 어느 탭이 어떤 지연 데이터를 필요로 하는가.
 *
 *  **여기에 없는 탭은 지연 데이터가 필요 없다.** 자기 데이터를 스스로 받는 탭
 *  (`newcomers_stats`·`small_stats`·`period_analysis` 등)을 여기 넣지 말 것 —
 *  넣으면 같은 응답을 두 번 받게 된다. */
export const TAB_LAZY_DATA: Readonly<Record<string, LazyKey>> = {
  ranking: "rank",
  category: "cats",
  newcomers_ranking: "news",
};

/** 첫 화면 게이트가 기다리는 API 이름(경로 조각).
 *
 *  **이 목록에 무거운 것을 더하지 말 것.** 여기 들어온 API는 그대로 첫 화면의
 *  하한선이 된다. 개요 탭이 실제로 렌더에 쓰는 것만 있어야 한다. */
export const FIRST_PAINT_APIS = ["overview", "rising-stars"] as const;

/** 첫 진입에서 **보내지 않는** API. 위 배경 참고. */
export const DEFERRED_APIS = ["live-ranking", "categories", "newcomers"] as const;

/** 첫 화면 게이트가 쓰는 api 표면(주입 가능하게 최소만 선언). */
export interface FirstPaintApi<O, S> {
  overview: () => Promise<O>;
  risingStars: (limit: number) => Promise<S>;
}

/** 개요 탭이 쓰는 둘을 **병렬로** 받는다.
 *
 *  `Promise.all`이라 하나라도 실패하면 거부된다 — 개요 화면은 이 둘이 다 있어야
 *  의미가 있으므로 그게 맞다(숫자 카드만 있고 급상승이 비면 반쯤 깨진 화면이다).
 *  대신 **셋을 더 묶지 않는다**는 것이 이 함수의 계약이다. */
export function loadFirstPaint<O, S>(api: FirstPaintApi<O, S>): Promise<[O, S]> {
  return Promise.all([api.overview(), api.risingStars(20)]);
}

/** 언마운트된 뒤 도착한 응답이 상태를 건드리지 않게 감싼다.
 *
 *  **effect 스코프의 `let alive`를 쓰면 안 되는 자리가 있다.** 지연 로더 effect는
 *  `[tab, rankState, catsState, newsState]`에 의존하므로 `setState("loading")` 한 번에
 *  cleanup이 돌고 다시 실행된다. 그때 `alive=false`가 되면 **정상 응답까지 버려져**
 *  탭이 영원히 로딩에 머문다. 그래서 판정 기준은 effect 수명이 아니라
 *  **컴포넌트 수명 ref**여야 한다.
 *
 *  React 18은 언마운트 후 `setState` 경고를 없앴지만, 그래도 감싸는 이유는 경고가
 *  아니라 **낭비된 렌더와 되살아난 상태**를 막기 위해서다. */
export function whenMounted<T>(
  mounted: { readonly current: boolean },
  apply: (value: T) => void,
): (value: T) => void {
  return (value: T) => { if (mounted.current) apply(value); };
}

/** 이 탭을 열었을 때 **새로 시작해야 하는** 지연 로드 키.
 *
 *  이미 `loading`/`ready`/`error`면 다시 시작하지 않는다. 그래서 탭을 오가도
 *  같은 요청이 두 번 나가지 않는다. `error`를 재시도 대상에서 빼는 것은 의도다 —
 *  탭을 왔다 갔다 하는 것만으로 실패한 무거운 요청이 반복되면 안 된다
 *  (재시도는 화면의 '새로고침'이 담당한다). */
export function pendingTabLoads(
  tab: string,
  states: Readonly<Record<LazyKey, LoadState>>,
): LazyKey[] {
  const key = TAB_LAZY_DATA[tab];
  if (!key) return [];
  return states[key] === "idle" ? [key] : [];
}
