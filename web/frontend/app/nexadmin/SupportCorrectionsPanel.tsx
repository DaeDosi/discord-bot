"use client";
/**
 * 수정 요청 처리(OWNER) — PUBLIC-UX-1.
 *
 * 공개 폼은 접수만 한다. 접수된 요청을 **볼 곳이 없으면 기능이 없는 것과 같다** —
 * 그래서 목록·상태 변경을 여기 둔다.
 *
 * 지키는 것:
 *  · 사용자 입력은 **텍스트로만** 그린다(`dangerouslySetInnerHTML` 없음).
 *  · 링크는 `https://`만 링크로 만든다(서버도 https만 받지만 한 겹 더 막는다).
 *  · 상태 변경은 행마다 한 번에 하나 — 요청 중엔 그 행의 선택을 잠근다.
 *    실패하면 **화면 값을 되돌리고** 이유를 적는다(성공한 척하지 않는다).
 *  · 이메일은 회신 목적으로 받은 값이라 이 화면에서만 보인다.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { CorrectionItem, CorrectionList } from "@/lib/types";

const fmt = (unix: number) => new Date(unix * 1000).toLocaleString("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit",
});

const isHttps = (v: string) => /^https:\/\/[^\s]+$/i.test(v);

function MaybeLink({ value }: { value: string }) {
  if (!value) return <span className="text-muted">-</span>;
  return isHttps(value) ? (
    <a href={value} target="_blank" rel="noopener noreferrer nofollow"
       className="inline-flex max-w-full items-center gap-1 break-all underline
                  underline-offset-2 hover:text-accent">
      {value} <ExternalLink size={11} aria-hidden="true" className="shrink-0" />
    </a>
  ) : <span className="break-all">{value}</span>;
}

export default function SupportCorrectionsPanel() {
  const [filter, setFilter] = useState("");
  const [list, setList] = useState<CorrectionList | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [rowErr, setRowErr] = useState<{ id: number; msg: string } | null>(null);
  const seq = useRef(0);

  const load = useCallback(async (status: string, before = 0) => {
    const my = ++seq.current;
    setLoading(true); setLoadErr(null);
    try {
      const r = await api.admin.supportCorrections(status, before);
      if (my !== seq.current) return;
      setList((prev) => before && prev
        ? { ...r, items: [...prev.items, ...r.items] } : r);
    } catch (e) {
      if (my !== seq.current) return;
      setLoadErr(e instanceof ApiError ? e.message : "목록을 불러오지 못했습니다.");
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => { load(filter); }, [filter, load]);

  const changeStatus = async (item: CorrectionItem, next: string) => {
    if (savingId !== null || next === item.status) return;
    const prev = item.status;
    setSavingId(item.id); setRowErr(null);
    // 낙관적으로 바꾸되 실패하면 되돌린다.
    setList((l) => l && { ...l, items: l.items.map((x) =>
      x.id === item.id ? { ...x, status: next } : x) });
    try {
      await api.admin.setCorrectionStatus(item.id, next);
      // 개수·필터 목록이 달라지므로 다시 읽는다(실패해도 저장은 이미 성공이다).
      load(filter).catch(() => {});
    } catch (e) {
      setList((l) => l && { ...l, items: l.items.map((x) =>
        x.id === item.id ? { ...x, status: prev } : x) });
      setRowErr({ id: item.id,
                  msg: e instanceof ApiError ? e.message : "상태를 바꾸지 못했습니다." });
    } finally {
      setSavingId(null);
    }
  };

  const statuses = list?.statuses ?? [];
  const total = statuses.reduce((n, s) => n + (list?.counts?.[s.key] ?? 0), 0);

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">수정 요청</h2>
          <p className="text-xs text-muted">
            공개 수정 요청 폼으로 접수된 건입니다. 상태는 운영 기록용이며 요청자에게 보이지 않습니다.
          </p>
        </div>
        <button type="button" onClick={() => load(filter)} disabled={loading}
                className="btn-secondary nb-tap inline-flex items-center gap-1.5 text-sm
                           disabled:opacity-50">
          <RefreshCw size={13} aria-hidden="true" className={loading ? "animate-spin" : ""} />
          새로고침
        </button>
      </div>

      <div className="nb-tap-gap flex flex-wrap gap-1.5" role="group" aria-label="처리 상태 필터">
        {[{ key: "", label: "전체" }, ...statuses].map((s) => {
          const on = filter === s.key;
          const n = s.key ? (list?.counts?.[s.key] ?? 0) : total;
          return (
            <button key={s.key || "all"} type="button" aria-pressed={on}
                    onClick={() => setFilter(s.key)}
                    className={`nb-tap rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                      on ? "border-accent bg-accent/10 font-semibold text-fg"
                         : "border-border text-muted hover:text-fg"}`}>
              {s.label} <span className="tabular-nums">{n}</span>
            </button>
          );
        })}
      </div>

      {loadErr && (
        <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-4">
          <p className="flex items-center gap-2 text-sm font-semibold text-red-400">
            <AlertCircle size={14} aria-hidden="true" /> {loadErr}
          </p>
        </div>
      )}

      {list && list.items.length === 0 && !loading && !loadErr && (
        <p className="rounded-xl border border-border p-6 text-center text-sm text-muted">
          해당하는 수정 요청이 없습니다.
        </p>
      )}

      <ul className="space-y-3">
        {(list?.items ?? []).map((item) => (
          <li key={item.id} className="card space-y-2 !p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold">
                <span className="tabular-nums">#{item.id}</span>
                <span className="ml-2 text-muted">{item.categoryLabel}</span>
              </p>
              <div className="flex items-center gap-2">
                <span className="text-xs tabular-nums text-muted">{fmt(item.createdAt)}</span>
                <label className="sr-only" htmlFor={`cr-status-${item.id}`}>
                  #{item.id} 처리 상태
                </label>
                <select id={`cr-status-${item.id}`} value={item.status}
                        disabled={savingId !== null}
                        onChange={(e) => changeStatus(item, e.target.value)}
                        className="nb-tap rounded-lg border border-border bg-bg px-2 py-1 text-sm
                                   disabled:opacity-60">
                  {statuses.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
                {savingId === item.id && (
                  <Loader2 size={14} className="animate-spin text-muted" aria-label="저장 중" />
                )}
              </div>
            </div>
            {rowErr?.id === item.id && (
              <p role="alert" className="text-xs text-red-400">{rowErr.msg}</p>
            )}
            <dl className="grid gap-x-3 gap-y-1 text-sm sm:grid-cols-[8rem_1fr]">
              <dt className="text-muted">대상</dt>
              <dd className="min-w-0"><MaybeLink value={item.clipRef} /></dd>
              <dt className="text-muted">문제 설명</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-words">{item.description}</dd>
              {item.desiredFix && (<>
                <dt className="text-muted">원하는 수정</dt>
                <dd className="min-w-0 whitespace-pre-wrap break-words">{item.desiredFix}</dd>
              </>)}
              {item.evidenceUrl && (<>
                <dt className="text-muted">근거 자료</dt>
                <dd className="min-w-0"><MaybeLink value={item.evidenceUrl} /></dd>
              </>)}
              {item.contactEmail && (<>
                <dt className="text-muted">회신 이메일</dt>
                <dd className="min-w-0 break-all">{item.contactEmail}</dd>
              </>)}
            </dl>
          </li>
        ))}
      </ul>

      {list?.hasMore && (
        <button type="button" disabled={loading}
                onClick={() => load(filter, list.items[list.items.length - 1]?.id ?? 0)}
                className="btn-secondary nb-tap w-full text-sm disabled:opacity-50">
          {loading ? "불러오는 중…" : "더 보기"}
        </button>
      )}
    </section>
  );
}
