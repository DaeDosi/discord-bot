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
 *  · 보관 정책(SUPPORT-POLICY-1): 행마다 이메일 제거·자동 삭제 **예정일은 서버가 계산한 값**을
 *    그대로 보여 준다(화면이 따로 계산하면 실제 정리와 갈라진다). 정리 작업이 dry-run이면
 *    그 사실을 숨기지 않는다 — 예정일이 지나도 지워지지 않기 때문이다.
 *  · 수동 삭제는 되돌릴 수 없어 **확인 단계**를 거친다(번호·상태를 다시 보여 준다).
 *    실행 규칙은 공통 `useMutation`(연타 차단·실패 시 성공 표시 안 함)을 따른다.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ExternalLink, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useMutation } from "@/lib/useMutation";
import type { CorrectionItem, CorrectionList } from "@/lib/types";

const fmt = (unix: number) => new Date(unix * 1000).toLocaleString("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit",
});

const isHttps = (v: string) => /^https:\/\/[^\s]+$/i.test(v);

/** 정리 건강 상태(서버 `phase`) → 운영자 문구. 공개 화면에는 쓰지 않는다. */
const PHASE_TEXT: Record<string, string> = {
  ready: "정상 — 실제 정리가 최근에 성공했습니다",
  awaiting_first_cleanup: "보관 정책 초기 점검 중이며 잠시 후 접수가 열립니다.",
  stale: "마지막 성공한 정리가 너무 오래됐습니다 — 접수를 닫았습니다",
  worker_stopped: "정리 워커가 멈췄습니다 — 접수를 닫았습니다",
  dry_run: "점검 모드라 실제 정리 전입니다 — 접수를 닫아 둡니다",
  disabled: "정리가 꺼져 있어 접수를 닫아 둡니다",
};

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
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const del = useMutation(0);
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

  const removeItem = (item: CorrectionItem) => {
    if (del.pending) return;
    del.run(() => api.admin.deleteCorrection(item.id), {
      onSuccess: async () => {
        setConfirmId(null);
        await load(filter);
      },
      onFailure: (e) => {
        // 이미 지워진 번호(404) — 목록이 낡았다는 뜻이라 다시 읽는다.
        if (e instanceof ApiError && e.status === 404) load(filter).catch(() => {});
      },
    });
  };

  const statuses = list?.statuses ?? [];
  const statusLabel = (key: string) => statuses.find((s) => s.key === key)?.label ?? key;
  const retention = list?.retention;
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

      {retention && (
        <div className="rounded-xl border border-border p-3 text-xs leading-relaxed text-muted">
          <p>
            보관 정책: 미처리 요청은 접수 후 {retention.policy.openMaxDays}일, 처리한 요청은 처리 후{" "}
            {retention.policy.closedDays}일에 자동 삭제되며, 어떤 경우에도 접수 후{" "}
            {retention.policy.absoluteMaxDays}일을 넘기지 않습니다. 이메일은 처리 후{" "}
            {retention.policy.emailDaysAfterClosed}일 또는 접수 후{" "}
            {retention.policy.emailMaxDaysAfterCreated}일 중 먼저 오는 때에 지워집니다.
          </p>
          {retention.mode === "apply" ? (
            <p className="mt-1">자동 정리가 하루 한 번 실행됩니다.</p>
          ) : (
            <p className="mt-1 font-semibold text-amber-400">
              자동 정리는 아직 점검 모드입니다 — 예정일이 지나도 지우지 않고 대상 건수만 셉니다.
            </p>
          )}
          {/* 단계적 활성화 점검용 상태(값은 서버가 준다). 요청 내용·이메일·해시는 없다. */}
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            <dt>정리 설정</dt>
            <dd>
              사용 {retention.enabled ? "켜짐" : "꺼짐"} · dry-run {retention.dryRun ? "켜짐" : "꺼짐"} ·
              워커 {retention.workerRunning ? "가동 중" : "멈춤"}
            </dd>
            <dt>건강 상태</dt>
            <dd className={retention.cleanupHealthy ? "" : "text-amber-400"}>
              {PHASE_TEXT[retention.phase] ?? retention.phase}
            </dd>
            <dt>공개 접수</dt>
            <dd>
              {retention.intakeReady && retention.saltConfigured
                ? "열림"
                : `닫힘 (${[!retention.intakeReady && "정리 준비 안 됨",
                           !retention.saltConfigured && "접수 설정 없음"].filter(Boolean).join(" · ")})`}
            </dd>
            <dt>마지막 성공</dt>
            <dd className="tabular-nums">
              {retention.lastSuccessfulRunAt === null
                ? "이 서버가 시작된 뒤 실제 정리 성공 없음"
                : `${fmt(retention.lastSuccessfulRunAt)} (${retention.policy.healthFreshnessHours}시간 안에 다시 성공해야 접수 유지)`}
            </dd>
            <dt>마지막 정리</dt>
            <dd className="tabular-nums">
              {retention.lastRun === null
                ? "이 서버가 시작된 뒤 아직 실행하지 않았습니다"
                : retention.lastRun.ok
                  ? `${fmt(retention.lastRun.at)} · ${retention.lastRun.mode === "apply" ? "처리" : "대상(점검)"} ` +
                    `중복 확인값 ${retention.lastRun.duplicateChecksCleared} · 이메일 ${retention.lastRun.emailCleared} · ` +
                    `삭제 ${retention.lastRun.rowsDeleted}`
                  : `${fmt(retention.lastRun.at)} · 실패`}
              {retention.consecutiveFailures > 0 && (
                <span className="ml-1 text-amber-400">(연속 실패 {retention.consecutiveFailures}회)</span>
              )}
            </dd>
            <dt>지금 정리 대상</dt>
            <dd className="tabular-nums">
              중복 확인값 {retention.candidates.candidateDuplicateCheckClearCount} · 이메일{" "}
              {retention.candidates.candidateEmailClearCount} · 삭제 {retention.candidates.candidateDeleteCount}
              {retention.candidates.invalidTimestampCount > 0 &&
                ` · 시각 오류 ${retention.candidates.invalidTimestampCount}`}
            </dd>
            {retention.candidates.invalidTimestampCount > 0 && (<>
              <dt>시각 오류</dt>
              <dd className="tabular-nums text-amber-400">
                접수 시각 {retention.candidates.invalidCreatedAtCount}건(자동 정리 제외) · 상태 시각{" "}
                {retention.candidates.invalidStatusChangedAtCount}건(접수 후 {retention.policy.absoluteMaxDays}일 상한은 적용)
              </dd>
            </>)}
          </dl>
        </div>
      )}

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
              <div className="flex flex-wrap items-center gap-2">
                <span className="whitespace-nowrap text-xs tabular-nums text-muted">{fmt(item.createdAt)}</span>
                <label className="sr-only" htmlFor={`cr-status-${item.id}`}>
                  #{item.id} 처리 상태
                </label>
                <select id={`cr-status-${item.id}`} value={item.status}
                        disabled={savingId !== null || del.pending}
                        onChange={(e) => changeStatus(item, e.target.value)}
                        className="nb-tap rounded-lg border border-border bg-bg px-2 py-1 text-sm
                                   disabled:opacity-60">
                  {statuses.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
                {savingId === item.id && (
                  <Loader2 size={14} className="animate-spin text-muted" aria-label="저장 중" />
                )}
                <button type="button" onClick={() => { del.clearError(); setConfirmId(item.id); }}
                        disabled={savingId !== null || del.pending}
                        aria-label={`#${item.id} 삭제`}
                        className="nb-tap inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg
                                   border border-border px-2 py-1 text-sm text-muted hover:text-red-400
                                   disabled:opacity-50">
                  <Trash2 size={13} aria-hidden="true" /> 삭제
                </button>
              </div>
            </div>
            {confirmId === item.id && (
              <div role="group" aria-label={`#${item.id} 삭제 확인`}
                   className="rounded-lg border border-red-500/40 bg-red-500/5 p-3">
                <p className="text-sm text-fg">
                  <b className="tabular-nums">#{item.id}</b> ({statusLabel(item.status)}) 요청을 지금 삭제합니다.
                  되돌릴 수 없습니다.
                </p>
                {del.error && <p role="alert" className="mt-1 text-xs text-red-400">{del.error}</p>}
                <div className="mt-2 flex flex-wrap gap-2">
                  <button type="button" onClick={() => removeItem(item)} disabled={del.pending}
                          aria-busy={del.pending}
                          className="nb-tap inline-flex items-center gap-1.5 rounded-lg bg-red-500 px-3
                                     py-1.5 text-sm font-semibold text-white disabled:opacity-60">
                    {del.pending && <Loader2 size={13} className="animate-spin" aria-hidden="true" />}
                    {del.pending ? "삭제 중…" : "삭제 확정"}
                  </button>
                  <button type="button" onClick={() => setConfirmId(null)} disabled={del.pending}
                          className="btn-secondary nb-tap text-sm disabled:opacity-60">
                    취소
                  </button>
                </div>
              </div>
            )}
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
              {item.contactEmail ? (<>
                <dt className="text-muted">회신 이메일</dt>
                <dd className="min-w-0 break-all">
                  <span>{item.contactEmail}</span>
                  {item.emailRemovalDueAt !== null && (
                    <span className="block text-xs text-muted">
                      {fmt(item.emailRemovalDueAt)} 제거 예정
                    </span>
                  )}
                </dd>
              </>) : item.emailClearedAt !== null ? (<>
                <dt className="text-muted">회신 이메일</dt>
                <dd className="min-w-0 text-muted">
                  보관 기간이 지나 제거됨 ({fmt(item.emailClearedAt)})
                </dd>
              </>) : null}
              <dt className="text-muted">상태 변경</dt>
              <dd className="min-w-0 tabular-nums text-muted">{fmt(item.statusChangedAt)}</dd>
              <dt className="text-muted">자동 삭제</dt>
              <dd className="min-w-0 tabular-nums text-muted">
                {item.deletionDueAt !== null
                  ? `${fmt(item.deletionDueAt)} 예정${item.deletionCapped ? " (접수 후 최대 보관 기간)" : ""}`
                  : "시각 정보가 올바르지 않아 자동 정리 대상이 아닙니다"}
              </dd>
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
