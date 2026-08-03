"use client";

/**
 * 싱드컵 대표 클립 수동 지정 (OWNER 전용).
 *
 * 자동 선정 규칙(하트↓ → 조회수↓ → 생성↑ → uid↑)은 **바꾸지 않는다.** 참가자가
 * 제출본을 나중에 올려 하트가 앞선 옛 클립이 대표로 잡히는 경우를 개별로 바로잡는
 * 화면이다.
 *
 * 화면이 지키는 두 가지:
 *   1) 자동 대표 · 수동 지정 · **실제 적용 중인 대표**를 항상 나눠서 보여준다.
 *      합쳐 버리면 "지정했는데 왜 다른 게 보이지"를 화면에서 설명할 수 없다.
 *   2) 적용은 Preview를 통과한 뒤 확인 단계를 거친다 — 순위가 내려갈 수 있는
 *      작업이라 한 번에 실행되면 안 된다.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2, RotateCcw, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import type {
  SingcupRepClip,
  SingcupRepPreview,
  SingcupRepSearchItem,
  SingcupRepState,
} from "@/lib/types";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function fmtDate(unix: number): string {
  if (!unix) return "-";
  return new Date(unix * 1000).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}

/** 대표 후보 카드 하나. `tone`으로 자동/지정/적용중을 구분한다. */
function ClipCard({ clip, tone, label, note }: {
  clip: SingcupRepClip | null;
  tone: "auto" | "override" | "effective";
  label: string;
  note?: string;
}) {
  const ring = tone === "effective"
    ? "border-accent/50 bg-accent/5"
    : tone === "override"
      ? "border-warning/40 bg-warning/5"
      : "border-border bg-bg-card/60";
  return (
    <div className={`rounded-xl border p-3 ${ring} min-w-0`}>
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
          {label}
        </span>
        {note && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-bg border border-border text-muted">
            {note}
          </span>
        )}
      </div>
      {clip ? (
        <div className="min-w-0">
          <div className="text-sm font-medium text-fg break-words">
            {clip.clipTitle || "(제목 없음)"}
          </div>
          <div className="mt-1 font-mono text-[11px] text-muted break-all">
            {clip.clipUid}
          </div>
          <div className="mt-1.5 text-xs text-muted">
            하트 {clip.heartCount.toLocaleString()} · 조회 {clip.viewCount.toLocaleString()}
          </div>
          <div className="text-[11px] text-muted">{fmtDate(clip.createdAt)}</div>
        </div>
      ) : (
        <div className="text-sm text-muted">없음</div>
      )}
    </div>
  );
}

export default function SingcupRepPanel() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SingcupRepSearchItem[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [state, setState] = useState<SingcupRepState | null>(null);
  const [clipInput, setClipInput] = useState("");
  const [preview, setPreview] = useState<SingcupRepPreview | null>(null);
  const [busy, setBusy] = useState<"" | "preview" | "apply" | "clear" | "state">("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const search = useCallback(async () => {
    setSearching(true);
    setError("");
    try {
      const r = await api.admin.singcupRepSearch(q);
      setResults(r.items);
    } catch (e) {
      setError(errText(e));
      setResults(null);
    } finally {
      setSearching(false);
    }
  }, [q]);

  const loadState = useCallback(async (channelId: string) => {
    setBusy("state");
    setError("");
    try {
      setState(await api.admin.singcupRepState(channelId));
    } catch (e) {
      setError(errText(e));
      setState(null);
    } finally {
      setBusy("");
    }
  }, []);

  useEffect(() => {
    if (selected) void loadState(selected);
  }, [selected, loadState]);

  // 참가자를 바꾸면 이전 참가자의 preview가 남아 있으면 안 된다 —
  // 그대로 두면 다른 사람의 검증 결과를 보고 적용하게 된다.
  useEffect(() => {
    setPreview(null);
    setConfirming(false);
    setClipInput("");
    setNotice("");
  }, [selected]);

  async function doPreview() {
    if (!selected) return;
    setBusy("preview");
    setError("");
    setNotice("");
    setConfirming(false);
    try {
      setPreview(await api.admin.singcupRepPreview(selected, clipInput));
    } catch (e) {
      setError(errText(e));
      setPreview(null);
    } finally {
      setBusy("");
    }
  }

  async function doApply() {
    if (!selected || !preview?.eligible) return;
    setBusy("apply");
    setError("");
    try {
      const r = await api.admin.singcupRepApply(selected, clipInput, "");
      setState(r.state);
      setPreview(null);
      setConfirming(false);
      setClipInput("");
      setNotice(r.recomputed
        ? `적용됐습니다. 현재 대표: ${r.effectiveRepresentativeClipUid ?? "-"}`
        : (r.note || "지정은 저장했으나 즉시 재계산에 실패했습니다."));
      void search();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy("");
    }
  }

  async function doClear() {
    if (!selected) return;
    setBusy("clear");
    setError("");
    try {
      const r = await api.admin.singcupRepClear(selected);
      setState(r.state);
      setPreview(null);
      setConfirming(false);
      setNotice(r.cleared
        ? "지정을 해제했습니다. 자동 대표로 복귀합니다."
        : "해제할 지정이 없습니다.");
      void search();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy("");
    }
  }

  const ov = state?.override ?? null;
  const autoUid = state?.autoRepresentative?.clipUid ?? null;
  const effUid = state?.effectiveRepresentativeClipUid ?? null;

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-border bg-bg-card/60 p-4 space-y-2">
        <div className="flex items-start gap-2 text-sm text-muted">
          <AlertTriangle size={16} className="text-warning flex-shrink-0 mt-0.5" />
          <p>
            자동 선정 규칙(하트↓ → 조회수↓ → 생성↑)은 그대로입니다. 여기서 지정하면
            그 규칙보다 <strong className="text-fg">이 지정이 우선</strong>합니다.
            점수는 조회수 70% + 하트 30%라 <strong className="text-fg">순위가 내려갈 수
            있습니다.</strong> 해제하면 자동 대표로 돌아갑니다.
          </p>
        </div>
      </div>

      {/* 참가자 검색 */}
      <div className="rounded-2xl border border-border bg-bg-card/60 p-4 space-y-3">
        <label htmlFor="rep-search" className="block text-sm font-medium text-fg">
          참가자 검색
        </label>
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            id="rep-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") void search(); }}
            placeholder="채널명 일부 (비우고 검색하면 전체)"
            className="flex-1 min-w-0 rounded-lg border border-border bg-bg px-3 py-2 text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
          />
          <button
            onClick={() => void search()}
            disabled={searching}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {searching ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
            검색
          </button>
        </div>

        {results && (
          <div className="max-h-64 overflow-y-auto rounded-lg border border-border divide-y divide-border">
            {results.length === 0 && (
              <div className="px-3 py-4 text-sm text-muted">결과가 없습니다.</div>
            )}
            {results.map((r) => (
              <button
                key={r.channelId}
                onClick={() => setSelected(r.channelId)}
                className={`w-full text-left px-3 py-2.5 hover:bg-bg transition-colors flex items-center justify-between gap-2 ${
                  selected === r.channelId ? "bg-accent/10" : ""
                }`}
              >
                <span className="min-w-0">
                  <span className="block text-sm text-fg truncate">{r.channelName || "(이름 없음)"}</span>
                  <span className="block font-mono text-[10px] text-muted truncate">{r.channelId}</span>
                </span>
                {r.hasOverride && (
                  <span className="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded-full bg-warning/15 text-warning border border-warning/30 font-semibold">
                    수동 지정
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          <X size={16} className="flex-shrink-0 mt-0.5" />
          <span className="break-words">{error}</span>
        </div>
      )}
      {notice && (
        <div className="flex items-start gap-2 rounded-xl border border-accent/30 bg-accent/10 px-4 py-3 text-sm text-accent">
          <Check size={16} className="flex-shrink-0 mt-0.5" />
          <span className="break-words">{notice}</span>
        </div>
      )}

      {/* 선택된 참가자 */}
      {selected && state && (
        <div className="rounded-2xl border border-border bg-bg-card/60 p-4 space-y-4">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="min-w-0">
              <div className="text-base font-semibold text-fg break-words">
                {state.channelName || "(이름 없음)"}
              </div>
              <div className="font-mono text-[11px] text-muted break-all">{state.channelId}</div>
            </div>
            <span className="text-xs text-muted">태그 클립 {state.taggedClipCount}개</span>
          </div>

          {/* 자동 / 지정 / 적용중을 항상 나눠 보여준다 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <ClipCard
              clip={state.autoRepresentative}
              tone="auto"
              label="자동 규칙 대표"
              note={autoUid && autoUid === effUid ? "현재 적용중" : undefined}
            />
            <ClipCard
              clip={ov ? (state.clips.find((c) => c.clipUid === ov.clipUid) ?? null) : null}
              tone="override"
              label="수동 지정"
              note={ov ? (ov.active ? "유효" : "무효 — 자동으로 복귀함") : "없음"}
            />
            <ClipCard
              clip={state.effectiveRepresentative}
              tone="effective"
              label="실제 적용중 대표"
              note={ov?.active ? "수동" : "자동"}
            />
          </div>

          {ov && !ov.active && (
            <div className="flex items-start gap-2 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2.5 text-xs text-warning">
              <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
              <span>
                지정한 클립({ov.clipUid})이 삭제·비활성 상태라 효력을 잃었습니다.
                자동 대표가 적용 중이며, 클립이 복구되면 지정이 다시 살아납니다.
              </span>
            </div>
          )}

          {ov && (
            <div className="flex items-center justify-between gap-2 flex-wrap text-xs text-muted">
              <span>지정 시각 {fmtDate(ov.updatedAt)}</span>
              <button
                onClick={() => void doClear()}
                disabled={busy !== ""}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-fg hover:bg-bg disabled:opacity-60"
              >
                {busy === "clear"
                  ? <Loader2 size={13} className="animate-spin" />
                  : <RotateCcw size={13} />}
                지정 해제 (자동으로 복귀)
              </button>
            </div>
          )}

          {/* 지정 입력 */}
          <div className="border-t border-border pt-4 space-y-3">
            <label htmlFor="rep-clip" className="block text-sm font-medium text-fg">
              대표로 지정할 클립
            </label>
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                id="rep-clip"
                value={clipInput}
                onChange={(e) => { setClipInput(e.target.value); setPreview(null); setConfirming(false); }}
                placeholder="https://chzzk.naver.com/clips/XXXX 또는 클립 UID"
                className="flex-1 min-w-0 rounded-lg border border-border bg-bg px-3 py-2 text-sm text-fg placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button
                onClick={() => void doPreview()}
                disabled={busy !== "" || !clipInput.trim()}
                className="flex items-center justify-center gap-1.5 rounded-lg border border-accent/40 bg-accent/10 px-4 py-2 text-sm font-medium text-accent disabled:opacity-60"
              >
                {busy === "preview" && <Loader2 size={14} className="animate-spin" />}
                Preview
              </button>
            </div>
            <p className="text-[11px] text-muted">
              치지직 클립 주소(chzzk.naver.com)만 허용합니다. 주소에서 클립 ID만 읽습니다.
            </p>

            {/* Preview 결과 */}
            {preview && (
              <div className="rounded-xl border border-border bg-bg p-3 space-y-3">
                {!preview.eligible ? (
                  <div className="flex items-start gap-2 text-sm text-danger">
                    <X size={16} className="flex-shrink-0 mt-0.5" />
                    <span className="break-words">{preview.reasonText}</span>
                  </div>
                ) : (
                  <>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <ClipCard clip={preview.currentRepresentative} tone="effective" label="현재 대표" />
                      <ClipCard clip={preview.targetClip} tone="override" label="지정할 클립" />
                    </div>

                    {preview.noop && (
                      <div className="text-xs text-muted">
                        이미 이 클립이 지정돼 있습니다 — 적용해도 달라지는 것이 없습니다.
                      </div>
                    )}

                    {preview.impact && (
                      <div className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-xs ${
                        preview.impact.rankLikelyDrops
                          ? "border-warning/40 bg-warning/10 text-warning"
                          : "border-border bg-bg-card/60 text-muted"
                      }`}>
                        <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
                        <span>
                          하트 {preview.impact.heartDelta >= 0 ? "+" : ""}{preview.impact.heartDelta},
                          {" "}조회 {preview.impact.viewDelta >= 0 ? "+" : ""}{preview.impact.viewDelta}
                          {preview.impact.rankLikelyDrops
                            ? " — 지표가 낮아져 순위가 내려갈 가능성이 큽니다."
                            : " — 순위 영향은 재계산 후 확정됩니다."}
                        </span>
                      </div>
                    )}

                    {preview.liveCheck.checked && preview.liveCheck.ok !== true && (
                      <div className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2.5 text-xs text-warning">
                        <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
                        <span>치지직 재확인: {preview.liveCheck.note || "확인하지 못했습니다."}</span>
                      </div>
                    )}
                    {preview.liveCheck.ok === true && (
                      <div className="flex items-center gap-1.5 text-xs text-muted">
                        <Check size={13} /> 치지직 상세에서 소유 채널을 다시 확인했습니다.
                      </div>
                    )}

                    {/* 확인 절차 — 한 번에 적용되지 않는다 */}
                    {!confirming ? (
                      <button
                        onClick={() => setConfirming(true)}
                        disabled={busy !== ""}
                        className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-60"
                      >
                        이 클립으로 지정하기
                      </button>
                    ) : (
                      <div className="rounded-lg border border-danger/40 bg-danger/10 p-3 space-y-2.5">
                        <p className="text-xs text-danger break-words">
                          <strong>{state.channelName}</strong>의 대표 클립을{" "}
                          <span className="font-mono">{preview.clipUid}</span>로 바꿉니다.
                          랭킹·급상승·스냅샷이 이 클립 기준으로 다시 계산됩니다. 진행할까요?
                        </p>
                        <div className="flex flex-col sm:flex-row gap-2">
                          <button
                            onClick={() => void doApply()}
                            disabled={busy !== ""}
                            className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-danger px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
                          >
                            {busy === "apply" && <Loader2 size={14} className="animate-spin" />}
                            확인, 적용합니다
                          </button>
                          <button
                            onClick={() => setConfirming(false)}
                            disabled={busy !== ""}
                            className="flex-1 rounded-lg border border-border px-4 py-2 text-sm text-fg hover:bg-bg disabled:opacity-60"
                          >
                            취소
                          </button>
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
