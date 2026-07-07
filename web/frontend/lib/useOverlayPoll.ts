"use client";
import { useEffect, useState } from "react";
import { BASE } from "@/lib/api";

// 오버레이(도박/미션) 현황을 주기적으로 폴링하는 공유 훅. 실제 OBS 오버레이 페이지와
// 대시보드 오버레이 설정 페이지의 미리보기가 이 훅을 그대로 재사용한다 — fetch/에러
// 처리 로직이 세 곳에 따로 복사돼 있으면 메시지/동작이 조금씩 갈라지기 쉽다.
export function useOverlayPoll<T>(path: string | null, intervalMs: number) {
  const [data, setData]   = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;

    const load = () => {
      fetch(`${BASE}${path}`)
        .then((r) => {
          if (r.status === 404) throw new Error("유효하지 않은 오버레이 URL입니다");
          if (!r.ok) throw new Error("서버 연결 오류");
          return r.json();
        })
        .then((d: T) => {
          if (cancelled) return;
          setData(d);
          setError(null);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setError(e instanceof Error ? e.message : "서버 연결 오류");
        });
    };

    load();
    const timer = setInterval(load, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [path, intervalMs]);

  return { data, error };
}
