// 사용자 실행 mutation(설정 저장·목록 추가/삭제·토글)의 **공통 실행 규칙**.
//
// 왜 만드는가: 대시보드 6개 화면에 25개 mutation 경로가 있었는데 처리 방식이 제각각이었다.
// 실측된 결함만 추려도 —
//   · 실패해도 "저장됨"·성공 toast를 띄운다(일반 설정·관리)
//   · 실패해도 목록에서 행을 지운다(치지직 팔로우 등급)
//   · 실패하면 버튼이 "추가 중"으로 영원히 고착한다(애정도 보상 추가)
//   · 백엔드 원문(`internal`)을 그대로 화면에 싣는다(포인트 도박 설정)
//   · 연타하면 요청이 그대로 여러 번 나간다
// 화면마다 고치면 다음에 추가되는 화면에서 또 갈린다. 그래서 실행 규칙을 한 곳에 둔다.
//
// **React에 의존하지 않는다.** 훅으로 만들면 렌더러 없이는 테스트할 수 없어서,
// 이 저장소의 `node --test`(의존성 0) 관행과 맞지 않는다. 상태 기계만 여기 두고
// `useMutation`이 그것을 React 상태에 연결한다.
//
// 런타임 상대 import를 두지 않는다(번들러는 확장자 없는 경로, node --test는 확장자를
// 요구해 둘을 동시에 만족시킬 수 없다). 오류 분류는 호출자가 주입한다.

export type MutationState = {
  /** 요청이 나가 있는 중인가. 버튼 비활성·중복 클릭 차단의 근거. */
  pending: boolean;
  /** 실패 문구. 성공하면 반드시 null이 된다. */
  error: string | null;
  /** 성공 표시(“저장됨”)를 켜도 되는가. 2xx로 resolve된 직후에만 true. */
  succeeded: boolean;
};

export const IDLE: MutationState = { pending: false, error: null, succeeded: false };

export type RunnerOptions = {
  /** 오류 → 사용자 문구. `dashboardErrors.mutationErrorMessage`를 넘긴다. */
  toMessage: (err: unknown) => string;
  /** 401처럼 다른 곳(api.ts)이 이미 로그인 화면으로 보낸 경우엔 오류를 그리지 않는다. */
  isHandledElsewhere?: (err: unknown) => boolean;
  onState: (state: MutationState) => void;
};

export type RunOptions<T> = {
  /** 성공 후 서버 값을 다시 읽는 등의 확정 작업. 여기서 나는 오류는 저장 실패로 보지 않는다
   *  — 저장은 이미 성공했고, 재조회 실패로 "저장 실패"라고 말하면 거짓말이 된다. */
  onSuccess?: (result: T) => void | Promise<void>;
  /** 실패 시 되돌릴 작업(optimistic update rollback). */
  onFailure?: (err: unknown) => void;
};

export function createMutationRunner(opts: RunnerOptions) {
  let state: MutationState = IDLE;
  let inFlight: Promise<unknown> | null = null;

  const set = (next: Partial<MutationState>) => {
    state = { ...state, ...next };
    opts.onState(state);
  };

  return {
    getState: () => state,

    /** 오류 표시만 지운다(입력을 고치기 시작했을 때). */
    clearError() {
      if (state.error !== null) set({ error: null });
    },

    /** 성공 표시를 내린다(2.5초 뒤 등). */
    clearSuccess() {
      if (state.succeeded) set({ succeeded: false });
    },

    /** 요청 하나를 실행한다.
     *
     *  이미 나가 있는 요청이 있으면 **새 요청을 만들지 않고** 그것을 돌려준다.
     *  버튼 disabled만으로는 부족하다 — 렌더 사이에 들어온 두 번째 클릭이나
     *  키보드 Enter 연타는 disabled가 적용되기 전에 도달할 수 있다. */
    async run<T>(fn: () => Promise<T>, run: RunOptions<T> = {}): Promise<T | undefined> {
      if (inFlight) return inFlight as Promise<T | undefined>;

      set({ pending: true, error: null, succeeded: false });
      const p = (async () => {
        try {
          const result = await fn();
          // 성공 판정은 오직 여기 — resolve된 경우뿐이다. api.ts의 request()가
          // 2xx가 아니면 던지므로 이 지점은 HTTP 2xx와 같은 뜻이다.
          set({ pending: false, error: null, succeeded: true });
          if (run.onSuccess) {
            try { await run.onSuccess(result); } catch { /* 재조회 실패는 저장 실패가 아니다 */ }
          }
          return result;
        } catch (err) {
          run.onFailure?.(err);
          if (opts.isHandledElsewhere?.(err)) {
            // 로그인 화면으로 넘어가는 중이다. 오류 문구를 띄우면 화면이 깜빡이고 끝난다.
            set({ pending: false, error: null, succeeded: false });
          } else {
            set({ pending: false, error: opts.toMessage(err), succeeded: false });
          }
          return undefined;
        } finally {
          inFlight = null;
        }
      })();

      inFlight = p;
      return p;
    },
  };
}

export type MutationRunner = ReturnType<typeof createMutationRunner>;
