"use client";
import { useCallback, useMemo, useRef, useState } from "react";
import { createMutationRunner, IDLE, type MutationState, type RunOptions } from "@/lib/mutationRunner";
import { isHandledElsewhere, mutationErrorMessage } from "@/lib/dashboardErrors";

/** `mutationRunner`를 React 상태에 연결하는 얇은 껍데기.
 *
 *  규칙은 전부 `mutationRunner`에 있다(그래야 렌더러 없이 테스트할 수 있다).
 *  여기서는 상태 반영과 성공 표시 자동 해제만 한다. */
export function useMutation(successResetMs = 2500) {
  const [state, setState] = useState<MutationState>(IDLE);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runner = useMemo(() => createMutationRunner({
    toMessage: mutationErrorMessage,
    isHandledElsewhere,
    onState: setState,
  }), []);

  const run = useCallback(<T,>(fn: () => Promise<T>, opts?: RunOptions<T>) => {
    if (timer.current) clearTimeout(timer.current);
    return runner.run(fn, opts).then((r) => {
      if (runner.getState().succeeded && successResetMs > 0) {
        timer.current = setTimeout(() => runner.clearSuccess(), successResetMs);
      }
      return r;
    });
  }, [runner, successResetMs]);

  return {
    pending: state.pending,
    error: state.error,
    succeeded: state.succeeded,
    run,
    clearError: runner.clearError,
  };
}
