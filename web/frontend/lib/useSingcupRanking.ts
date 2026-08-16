"use client";
import { useCallback, useEffect, useRef, useState } from "react";

import { BASE } from "./api";
import { FinalRankingLoader, type LoaderSnapshot } from "./singcupRankingLoader";
import { useSingcupMain } from "./useSingcupMain";
import type { SingcupMain } from "./types";

// 비공식 인기점수 랭킹 화면의 데이터 구독.
//
// ── 왜 `useSingcupMain`과 나뉘어 있나 ───────────────────────────────────────
// 랭킹 화면(`?view=ranking`)과 공식 예선 참가자 화면(`?view=official`)은 원래
// `/api/singcup/main` 하나를 함께 썼다. 그런데 요구는 정반대다:
//
//   · 랭킹  — 이벤트가 끝났으니 순위·하트·급상승을 **얼린다**
//   · 참가자 — 하트·조회수는 **계속 최신값**을 보여준다
//
// `/main`을 얼리면 참가자 화면까지 굳는다. 그래서 랭킹만 확정본 경로를 쓴다.
//
// ── 네 가지 상태 ────────────────────────────────────────────────────────────
//   final      200 + rankingFinal        확정 랭킹
//   live       200 + frozen:false        동결 비활성 — 기존 실시간 경로
//   finalizing 503 + status:"finalizing" 확정본 준비 중 (자동 재확인)
//   error      그 밖의 전부              장애 (수동 재시도)
//
// **어느 실패 경로에서도 실시간 값으로 물러서지 않는다.**
//
// 타이머·예산·취소가 얽힌 자동 재확인 로직은 `singcupRankingLoader.ts`에 React와
// 분리해 두었다 — DOM 없이 검증할 수 있어야 조용히 틀리는 것을 막을 수 있다.
// 이 훅은 그 로더를 마운트 수명에 묶기만 한다.

export type SingcupRankingStatus = LoaderSnapshot["status"];

export type SingcupRankingState = {
  data: SingcupMain | null;
  status: SingcupRankingStatus;
  loading: boolean;
  /** 확정본을 보고 있는가. true면 화면은 '집계 종료'로 그린다. */
  final: boolean;
  /** 확정 시각(epoch ms). 확정본일 때만 값이 있다. */
  finalizedAt: number | null;
  /** 실시간 경로(동결 비활성)의 수동 새로고침. 확정본에서는 화면이 버튼을 감춘다. */
  refresh: () => void;
  refreshing: boolean;
  /** error 상태에서 확정본을 다시 불러온다(자동 재확인 예산도 새로 시작). */
  retry: () => void;
  /** 재시도 요청이 진행 중인가 — 버튼을 비활성화해 중복 요청을 막는다. */
  retrying: boolean;
};

export function useSingcupRanking(): SingcupRankingState {
  const [snap, setSnap] = useState<LoaderSnapshot>(
    { status: "loading", data: null, autoChecks: 0 });
  const [retrying, setRetrying] = useState(false);
  const loaderRef = useRef<FinalRankingLoader | null>(null);

  useEffect(() => {
    const loader = new FinalRankingLoader({
      fetchOnce: async (signal) => {
        // 확정본은 변하지 않으므로 브라우저 캐시를 그대로 쓴다(재방문 시 네트워크 0).
        // 준비 중(503)에는 서버가 `no-store`를 붙여 캐시되지 않는다.
        const r = await fetch(`${BASE}/api/singcup/final-ranking`, { signal });
        let body: unknown;
        try { body = await r.json(); } catch { body = undefined; }
        // 교차 출처라 서버가 `Access-Control-Expose-Headers`로 열어 줘야 읽힌다.
        // 못 읽으면 null이 되고 로더가 안전한 기본 간격을 쓴다.
        return { status: r.status, body, retryAfter: r.headers.get("Retry-After") };
      },
      onChange: (s) => { setSnap(s); setRetrying(false); },
    });
    loaderRef.current = loader;
    loader.start();
    // unmount 시 타이머 제거 + 진행 중 요청 취소
    return () => { loader.dispose(); loaderRef.current = null; };
  }, []);

  const retry = useCallback(() => {
    setRetrying(true);
    loaderRef.current?.retry();
  }, []);

  // 훅은 조건부로 부를 수 없으므로 항상 호출하되, 실시간 경로일 때만 값을 쓴다.
  // `useSingcupMain`은 공유 캐시를 쓰므로 참가자 화면이 이미 받아 둔 데이터가
  // 있으면 네트워크가 더 나가지 않는다.
  const live = useSingcupMain();

  const status = snap.status;
  const base = { status, retry, retrying };

  if (status === "final" && snap.data) {
    return {
      ...base, data: snap.data, loading: false, final: true,
      finalizedAt: snap.data.rankingFinalizedAt
        ? snap.data.rankingFinalizedAt * 1000 : null,
      refresh: () => {}, refreshing: false,
    };
  }
  if (status === "live") {
    // 동결 비활성 = 기존 실시간 랭킹 그대로. 수동 새로고침도 예전처럼 동작한다.
    return { ...base, data: live.data, loading: live.loading, final: false,
             finalizedAt: null, refresh: live.refresh, refreshing: live.refreshing };
  }
  // loading / finalizing / error — 실시간 데이터를 절대 내보내지 않는다.
  return { ...base, data: null, loading: status === "loading", final: false,
           finalizedAt: null, refresh: () => {}, refreshing: false };
}
