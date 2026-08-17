"use client";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  SINGCUP_MAIN_LIMIT,
  SINGCUP_MAIN_TTL_MS,
  api,
  sharedAge,
  sharedPeek,
  singcupMainKey,
} from "./api";
import { shouldPollNow, shouldRefetchOnRevisit } from "./pollPolicy";
import type { SingcupMain } from "./types";

// 폴링 주기. 예전에는 60초였는데, 이 응답은 참가자 전원(약 850KB, gzip 240KB)이라
// 화면을 열어 둔 사람 수 × 분당 1회가 그대로 전송 비용이 됐다(실측 분당 63회).
// 데이터가 바뀌는 속도를 보면 60초는 애초에 과했다 — 라이브 여부는 10분 주기
// 수집이 원본이고, 하트 지표도 클립당 60분 주기로 갱신된다. 5분이면 화면이
// 원본보다 빠르다.
export const SINGCUP_POLL_MS = 5 * 60_000;
// 탭에 돌아왔을 때 이 시간보다 최근에 받은 데이터면 다시 부르지 않는다.
const REVISIT_STALE_MS = 60_000;

const KEY = singcupMainKey(SINGCUP_MAIN_LIMIT);

export type SingcupMainState = {
  data: SingcupMain | null;
  /** 첫 데이터가 아직 없는 동안에만 true(폴링 중에는 화면이 깜빡이지 않는다) */
  loading: boolean;
  /** 수동 새로고침이 진행 중인지 */
  refreshing: boolean;
  /** 지금 보고 있는 데이터를 받은 시각(ms). 화면에 '몇 분 전'을 쓰기 위한 값 */
  updatedAt: number | null;
  refresh: () => void;
};

/**
 * 싱드컵 메인 데이터 구독 — 랭킹 탭과 라이브 페이지가 **같은 공유 캐시**를 쓴다.
 *
 * 두 화면이 각자 폴링하던 시절에는 같은 데이터를 두 번 받아 갔고, 탭을 오갈 때마다
 * 재마운트가 곧바로 한 번 더 호출했다. 정책을 훅 하나에 모아 두 화면이 절대
 * 어긋나지 않게 한다.
 *
 * 정책:
 *  - 진입 시 1회(캐시가 신선하면 네트워크 없이 그 값)
 *  - 보이는 동안 5분마다 1회
 *  - `document.visibilityState === "hidden"`이면 폴링하지 않는다
 *  - 다시 보이게 됐을 때는 마지막 수신이 60초를 넘겼을 때만 1회
 *  - 수동 새로고침은 캐시를 건너뛴다
 */
export function useSingcupMain(opts: {
  /** 꺼져 있으면 **네트워크를 전혀 쓰지 않는다.** 기본값은 켜짐(기존 호출부 호환).
   *
   *  기능이 종료된 화면이 여전히 참가자 전원(약 850KB)을 받아 오면 비용만 나가고
   *  보여 줄 것은 없다. 화면에서 `if (!open) return <Retired/>`로 막아도 훅은
   *  이미 마운트된 뒤라 요청이 나간다 — 그래서 훅 안에서 막는다. */
  enabled?: boolean;
} = {}): SingcupMainState {
  const enabled = opts.enabled !== false;
  const [data, setData] = useState<SingcupMain | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [refreshing, setRefreshing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  // 언마운트 뒤 setState를 막는 용도. StrictMode(개발)에서 effect가 두 번 도는 것을
  // 견디도록 effect 시작 시 다시 true로 되돌린다.
  const alive = useRef(true);

  const load = useCallback(async (force = false) => {
    if (!enabled) return;              // 요청 자체를 만들지 않는다
    if (force) setRefreshing(true);
    try {
      const d = await api.singcup.main(SINGCUP_MAIN_LIMIT, { force });
      if (!alive.current) return;
      setData(d);
      // 이 컴포넌트가 요청을 보냈는지와 무관하게 '데이터를 받은 시각'을 쓴다.
      // 다른 화면이 채운 캐시를 그대로 받은 경우도 있기 때문이다.
      const age = sharedAge(KEY);
      setUpdatedAt(Date.now() - (age ?? 0));
    } catch {
      // 실패해도 마지막 정상 데이터를 유지한다. 실패는 캐시에 남지 않으므로
      // 다음 주기(또는 수동 새로고침)에 그대로 재시도된다.
    } finally {
      if (alive.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [enabled]);

  useEffect(() => {
    alive.current = true;
    // 꺼져 있으면 타이머도 만들지 않는다. `load` 안에서만 막으면 5분마다 빈 호출이
    // 도는 타이머가 남는다.
    if (!enabled) {
      setLoading(false);
      return () => { alive.current = false; };
    }

    // 재마운트 즉시 그리기 — 캐시가 신선하면 로딩 화면을 거치지 않는다.
    const cached = sharedPeek<SingcupMain>(KEY, SINGCUP_MAIN_TTL_MS);
    if (cached) {
      setData(cached);
      setLoading(false);
      setUpdatedAt(Date.now() - (sharedAge(KEY) ?? 0));
    }
    // 캐시가 신선하면 이 호출은 네트워크 없이 끝난다(sharedGet이 판단).
    void load();

    const timer = window.setInterval(() => {
      if (!shouldPollNow(document.visibilityState)) return;
      void load();
    }, SINGCUP_POLL_MS);

    const onVisible = () => {
      if (!shouldPollNow(document.visibilityState)) return;
      if (!shouldRefetchOnRevisit(sharedAge(KEY), REVISIT_STALE_MS)) return;
      void load();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      alive.current = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load, enabled]);

  const refresh = useCallback(() => { void load(true); }, [load]);

  return { data, loading, refreshing, updatedAt, refresh };
}
