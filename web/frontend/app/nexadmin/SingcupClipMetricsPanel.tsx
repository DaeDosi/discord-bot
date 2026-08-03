"use client";

/**
 * 싱드컵 클립 지표 단건 갱신 (OWNER 전용).
 *
 * 위의 대표 클립 지정과는 **다른 동작**이다. 대표를 바꾸지 않고, 한 클립의
 * 하트·조회수를 지금 다시 읽어 온다.
 *
 * 있어야 하는 이유: 카드 API가 200을 주면서 조회수만 빠뜨리는 회차가 있고, 그때
 * 저장 계약("못 읽은 필드는 보존")대로 값이 삽입 초기값 0으로 남는다. 자동 복구는
 * 다음 사이클(70분+) 뒤라, 그동안 0이 조회수 70% 가중 점수에 진짜 0처럼 들어간다.
 *
 * 화면이 지키는 세 가지:
 *   1) **`unknown`과 진짜 0을 항상 나눠 보여준다.** 이걸 합치면 조회수 0을 앞에
 *      두고 고장인지 정상인지 판단할 수 없다 — 이 패널의 존재 이유다.
 *   2) 숫자 입력란이 없다. 값의 출처는 언제나 카드 API다.
 *   3) 몇 번째 시도에서 어떤 필드를 얻었는지 보여준다. 부분 결손이 재시도로
 *      메워졌는지, 끝내 못 받았는지가 화면에서 구분돼야 한다.
 */

import { useState } from "react";
import { AlertTriangle, Check, Loader2, RefreshCw, Search } from "lucide-react";
import { api } from "@/lib/api";
import type {
  SingcupClipMetricsApplyResult,
  SingcupClipMetricsExternal,
  SingcupClipMetricsPreview,
  SingcupClipMetricsStored,
  SingcupMetricState,
} from "@/lib/types";

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function fmtDate(unix: number): string {
  if (!unix) return "-";
  return new Date(unix * 1000).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}

const STATE_LABEL: Record<SingcupMetricState, string> = {
  unknown: "모름 (한 번도 정상 수신 못 함)",
  observed: "정상 수신",
  observed_zero: "정상 수신 · 진짜 0",
  observed_legacy: "정상 수신 (시각 미기록)",
};

/** 값 하나 + 그 값이 믿을 만한지. 둘을 떼어 놓으면 화면이 거짓말을 하게 된다. */
function MetricValue({ label, value, state, at }: {
  label: string;
  value: number;
  state: SingcupMetricState;
  at: number;
}) {
  const bad = state === "unknown";
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`text-lg font-semibold ${bad ? "text-warning" : "text-fg"}`}>
        {bad ? "—" : value.toLocaleString()}
      </div>
      <div
        className={`mt-0.5 inline-block rounded px-1.5 py-0.5 text-[10px] ${
          bad
            ? "bg-warning/10 text-warning border border-warning/30"
            : "bg-bg border border-border text-muted"
        }`}
      >
        {STATE_LABEL[state]}
      </div>
      <div className="mt-0.5 text-[11px] text-muted">{fmtDate(at)}</div>
    </div>
  );
}

function StoredCard({ stored, title }: {
  stored: SingcupClipMetricsStored;
  title: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-bg-card/60 p-3 min-w-0">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
        {title}
      </div>
      <div className="text-sm font-medium text-fg break-words">
        {stored.clipTitle || "(제목 없음)"}
      </div>
      <div className="mt-0.5 text-xs text-muted break-words">{stored.channelName}</div>
      <div className="mt-1 font-mono text-[11px] text-muted break-all">
        {stored.clipUid}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <MetricValue
          label="하트"
          value={stored.heartCount}
          state={stored.heartState}
          at={stored.lastHeartAt}
        />
        <MetricValue
          label="조회수"
          value={stored.viewCount}
          state={stored.viewState}
          at={stored.lastViewAt}
        />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5 text-[10px]">
        {[
          stored.isRepresentative ? "대표 클립" : null,
          stored.active ? "active" : "비활성",
          stored.deletionState && stored.deletionState !== "active"
            ? stored.deletionState
            : null,
          stored.blindType || null,
        ]
          .filter(Boolean)
          .map((t) => (
            <span
              key={t as string}
              className="rounded border border-border bg-bg px-1.5 py-0.5 text-muted"
            >
              {t}
            </span>
          ))}
      </div>
      <div className="mt-2 text-[11px] text-muted">
        마지막 시도 {fmtDate(stored.lastAttemptAt)}
      </div>
    </div>
  );
}

/** 시도별 관측. 재시도가 무엇을 메웠는지가 여기서 보인다. */
function ExternalCard({ ext }: { ext: SingcupClipMetricsExternal }) {
  return (
    <div className="rounded-xl border border-border bg-bg-card/60 p-3 min-w-0">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
          외부 조회 결과
        </span>
        <span className="rounded border border-border bg-bg px-1.5 py-0.5 text-[10px] text-muted">
          외부 호출 {ext.attempts} / 최대 {ext.maxAttempts}회
        </span>
        {ext.partial && (
          <span className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] text-warning">
            부분 결손 {ext.missingReason && `· ${ext.missingReason}`}
          </span>
        )}
      </div>
      {!ext.ok ? (
        <p className="text-sm text-warning">외부 조회에 실패했습니다.</p>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted">하트</div>
            <div className="text-lg font-semibold text-fg">
              {ext.heartOk ? ext.heartCount?.toLocaleString() : "못 읽음"}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted">조회수</div>
            <div
              className={`text-lg font-semibold ${ext.viewOk ? "text-fg" : "text-warning"}`}
            >
              {ext.viewOk ? ext.viewCount?.toLocaleString() : "못 읽음"}
            </div>
          </div>
        </div>
      )}
      {ext.attemptTrace.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[280px] text-left text-[11px]">
            <thead className="text-muted">
              <tr>
                <th className="py-1 pr-2 font-medium">시도</th>
                <th className="py-1 pr-2 font-medium">얻은 필드</th>
                <th className="py-1 pr-2 font-medium">하트</th>
                <th className="py-1 font-medium">조회수</th>
              </tr>
            </thead>
            <tbody className="text-fg">
              {ext.attemptTrace.map((a) => (
                <tr key={a.attempt} className="border-t border-border">
                  <td className="py-1 pr-2">{a.attempt}</td>
                  <td className="py-1 pr-2 text-muted">
                    {a.ok ? a.fieldsObserved : "조회 실패"}
                  </td>
                  <td className="py-1 pr-2">
                    {a.heartCount === null ? "—" : a.heartCount.toLocaleString()}
                  </td>
                  <td className="py-1">
                    {a.viewCount === null ? "—" : a.viewCount.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function SingcupClipMetricsPanel() {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState<"" | "preview" | "apply">("");
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState<SingcupClipMetricsPreview | null>(null);
  const [applied, setApplied] = useState<SingcupClipMetricsApplyResult | null>(null);

  async function doPreview() {
    const v = input.trim();
    if (!v || busy) return;
    setBusy("preview");
    setErr("");
    setApplied(null);
    try {
      setPreview(await api.admin.singcupClipMetricsPreview(v));
    } catch (e) {
      setPreview(null);
      setErr(errText(e));
    } finally {
      setBusy("");
    }
  }

  async function doApply() {
    // Preview 값을 보내지 않는다 — 서버가 자기 몫의 조회를 새로 한다.
    // busy 가드가 중복 클릭을 막고, 서버는 같은 clip_uid 락으로 한 번 더 막는다.
    const v = input.trim();
    if (!v || busy) return;
    setBusy("apply");
    setErr("");
    try {
      const res = await api.admin.singcupClipMetricsApply(v);
      setApplied(res);
      setPreview(null);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-bg-card p-4">
        <h2 className="text-base font-semibold text-fg">클립 지표 단건 갱신</h2>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          한 클립의 하트·조회수를 지금 다시 읽어 옵니다.{" "}
          <strong className="text-fg">대표 클립을 지정하지는 않습니다</strong> — 다만
          갱신된 지표로 순위를 다시 계산하므로, 자동 선정 규칙(하트↓ → 조회수↓)의
          결과가 바뀌면 대표도 따라 바뀔 수 있습니다. 고정하려면 위의 수동 지정을
          쓰세요. 카드 API가 조회수를 빠뜨린 회차 때문에 값이 0으로 남은 클립을 자동
          갱신(최대 70분)까지 기다리지 않고 바로잡는 용도입니다. 숫자를 직접 입력할
          수는 없습니다.
        </p>

        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          <div className="relative min-w-0 flex-1">
            <Search
              size={14}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
            />
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void doPreview();
              }}
              placeholder="클립 URL 또는 UID"
              className="w-full rounded-lg border border-border bg-bg py-2 pl-9 pr-3 text-sm text-fg placeholder:text-muted"
            />
          </div>
          <button
            onClick={() => void doPreview()}
            disabled={busy !== "" || !input.trim()}
            className="flex items-center justify-center gap-1.5 rounded-lg border border-border px-4 py-2 text-sm font-medium text-fg hover:bg-bg disabled:opacity-60"
          >
            {busy === "preview" ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Search size={14} />
            )}
            확인
          </button>
        </div>

        {err && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/5 p-3 text-sm text-danger">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <span className="min-w-0 break-words">{err}</span>
          </div>
        )}
      </div>

      {preview && (
        <div className="rounded-xl border border-border bg-bg-card p-4">
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <StoredCard stored={preview.stored} title="현재 저장값" />
            <ExternalCard ext={preview.external} />
          </div>

          <div className="mt-3 rounded-lg border border-border bg-bg p-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">
              저장 예정값
            </div>
            <div className="mt-1.5 text-sm text-fg">
              하트{" "}
              <strong>{preview.pending.heartCount.toLocaleString()}</strong>
              {preview.pending.heartWillChange && (
                <span className="ml-1 text-xs text-accent">변경됨</span>
              )}
              {" · "}
              조회수 <strong>{preview.pending.viewCount.toLocaleString()}</strong>
              {preview.pending.viewWillChange && (
                <span className="ml-1 text-xs text-accent">변경됨</span>
              )}
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-muted">
              읽지 못한 필드는 저장하지 않고 기존 값을 그대로 둡니다.
            </p>
          </div>

          {/* 대표 이동 가능성은 **적용 전에** 알려야 한다. 갱신된 지표가 자동 선정
              1등을 바꾸면 대표가 따라 움직이는데, 그걸 모르고 누르면 "왜 대표가
              바뀌었지"가 된다. override가 걸려 있으면 유지되므로 그때는 안심 문구를 준다. */}
          {preview.representativeRisk.hasOverride ? (
            <p className="mt-2 text-[11px] text-muted">
              이 참가자는 수동 대표 지정이 걸려 있어, 갱신 후에도 대표 클립이 유지됩니다.
            </p>
          ) : preview.representativeRisk.mayChangeAutoRepresentative ? (
            <div className="mt-2 flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 p-2.5 text-[11px] leading-relaxed text-warning">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span className="min-w-0">
                수동 지정이 없는 참가자입니다. 갱신된 지표로 순위를 다시 계산하면 자동
                선정 규칙(하트↓ → 조회수↓)의 결과가 바뀌어{" "}
                <strong>대표 클립이 이동할 수 있습니다.</strong> 고정하려면 위의 대표
                클립 수동 지정을 먼저 사용하세요.
              </span>
            </div>
          ) : null}

          {preview.note && (
            <p className="mt-2 text-xs text-warning">{preview.note}</p>
          )}

          <button
            onClick={() => void doApply()}
            disabled={busy !== "" || !preview.external.ok}
            className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
          >
            {busy === "apply" ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <RefreshCw size={14} />
            )}
            지금 갱신
          </button>
          <p className="mt-1.5 text-[11px] text-muted">
            적용 시 최신 값을 다시 조회합니다(위 미리보기 값을 그대로 저장하지 않습니다).
          </p>
        </div>
      )}

      {applied && (
        <div className="rounded-xl border border-accent/40 bg-accent/5 p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm font-semibold text-fg">
            <Check size={15} className="text-accent" />
            갱신 완료
            {!applied.recomputed && (
              <span className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] font-normal text-warning">
                순위 재계산은 다음 회차에 반영됩니다
              </span>
            )}
            {!applied.autoRepresentativeChanged ? (
              <span className="rounded border border-border bg-bg px-1.5 py-0.5 text-[10px] font-normal text-muted">
                대표 클립 변경 없음
                {applied.hasOverride && " (수동 지정 유지)"}
              </span>
            ) : (
              // 지정한 적이 없어도 갱신된 지표가 자동 선정 순서를 바꾸면 대표가
              // 움직인다. 조용히 넘어가면 "왜 대표가 바뀌었지"를 설명할 수 없다.
              <span className="rounded border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] font-normal text-warning">
                자동 선정 결과가 바뀌어 대표 클립이 변경됨
              </span>
            )}
          </div>
          {applied.autoRepresentativeChanged && (
            <div className="mb-3 rounded-lg border border-warning/30 bg-warning/5 p-2.5 text-[11px] leading-relaxed text-warning">
              대표 클립{" "}
              <span className="font-mono break-all">
                {applied.representativeBeforeClipUid || "(없음)"}
              </span>{" "}
              →{" "}
              <span className="font-mono break-all">
                {applied.representativeAfterClipUid || "(없음)"}
              </span>
              . 이 동작이 대표를 지정한 것이 아니라, 갱신된 지표로 자동 선정 규칙을 다시
              적용한 결과입니다. 특정 클립으로 고정하려면 위의 수동 지정을 사용하세요.
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <StoredCard stored={applied.before} title="이전" />
            <StoredCard stored={applied.after} title="이후" />
          </div>
          <div className="mt-3">
            <ExternalCard ext={applied.external} />
          </div>
        </div>
      )}
    </div>
  );
}
