"use client";
/**
 * 브라우저 기반 PIKU Collector 관리 (OWNER 전용).
 *
 * **왜 브라우저인가.** Railway와 AWS 서울 EC2에서 PIKU에 접속하면 둘 다 403이다.
 * 우회하지 않기로 했으므로, PIKU가 정상적으로 열리는 운영자 브라우저가 이미
 * 렌더된 공개 표를 읽어 보낸다. 서버는 받기만 하고 PIKU에 직접 요청하지 않는다.
 *
 * **이 화면이 답하는 질문은 셋이다.**
 *  1. 지금 세 부문에 수집본이 있는가(그리고 몇 행인가)
 *  2. 공개할 수 있는가 — 없다면 **무엇 때문에 막혔는가**
 *  3. 공개하면 무엇이 바뀌는가
 *
 * 화면에 우승 비율·승률 숫자를 쓰지 않는다. 서버 응답에도 없다.
 */
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, Check, ClipboardCopy, Eye, ListChecks, Loader2, RefreshCw,
  ShieldAlert, Upload,
} from "lucide-react";
import { COPY_RESET_MS, copyToken } from "@/lib/copyToken";
import { api } from "@/lib/api";
import type { PikuCollectorStatus, PikuPublishPreview } from "@/lib/types";
import PikuMappingReview from "./PikuMappingReview";

/* ── 단계(campaign) — SINGCUP-FINAL-1 ─────────────────────────────────────
 * 화면은 한 번에 한 단계만 보여 준다. **본선이 기본**이고 예선은 동결(갱신 보류)이라
 * 수집 토큰·매핑·공개 버튼이 전부 잠긴다. source 목록은 서버 상태(`divisions`)의
 * 키를 그대로 쓴다 — 예선 3부문/본선 1source를 화면이 따로 알 필요가 없다. */
type Campaign = "final" | "qualifier";
const CAMPAIGNS: { k: Campaign; label: string; hint: string }[] = [
  { k: "final", label: "본선", hint: "진행 중" },
  { k: "qualifier", label: "예선", hint: "동결 · 갱신 보류" },
];

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const fmt = (unix: number) =>
  unix ? new Date(unix * 1000).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })
       : "-";
/** 단계에서 보여 줄 source 키 — 서버 응답 순서를 그대로 쓴다. */
const sourceKeys = (st: PikuCollectorStatus | null) => Object.keys(st?.divisions ?? {});

/** 상태를 **한 단어**로. 색만으로 뜻을 전하지 않도록 문구를 함께 둔다. */
const RESULT_LABEL: Record<string, string> = {
  draft: "수집됨 (공개 전)",
  published: "공개됨",
  failed: "실패",
};
/** 브라우저 쪽 실패 종류. **우회 방법은 적지 않는다.** */
const FAILURE_LABEL: Record<string, string> = {
  blocked: "PIKU가 접근을 거부함",
  captcha: "확인 화면이 떠서 중단함",
  not_rendered: "표를 찾지 못함",
  aborted: "사용자가 중단함",
};

function StatePill({ d }: { d: { lastResult: string | null; lastErrorKind: string } }) {
  const r = d.lastResult;
  const tone = r === "failed" ? "border-red-500/40 bg-red-500/10 text-red-300"
    : r === "published" ? "border-accent/40 bg-accent/10 text-accent"
    : r === "draft" ? "border-border bg-bg-hover text-fg"
    : "border-border bg-transparent text-muted";
  return (
    <span className={`inline-flex shrink-0 items-center rounded-md border px-1.5
                      py-0.5 text-[11px] font-bold leading-none ${tone}`}>
      {r ? (RESULT_LABEL[r] ?? r) : "수집 없음"}
      {r === "failed" && d.lastErrorKind
        ? ` · ${FAILURE_LABEL[d.lastErrorKind] ?? d.lastErrorKind}` : ""}
    </span>
  );
}

export default function PikuCollectorPanel() {
  const [st, setSt] = useState<PikuCollectorStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // 복사 결과는 버튼 문구에만 쓴다. 토큰 자체는 어디에도 보관하지 않는다.
  const [copied, setCopied] = useState<"copied" | "failed" | null>(null);
  const [token, setToken] = useState<{ division: string; token: string;
                                       expiresAt: number } | null>(null);
  /** 매핑 검토를 펼친 부문. 한 번에 하나만 연다 — 세 개를 동시에 펴면
   *  160행이 한 화면에 쌓여 무엇을 보고 있는지 알 수 없다. */
  const [openDiv, setOpenDiv] = useState<string | null>(null);
  const [pv, setPv] = useState<PikuPublishPreview | null>(null);
  const [campaign, setCampaign] = useState<Campaign>("final");

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      setSt(await api.admin.pikuCollectorStatus(campaign));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setLoading(false);
    }
  }, [campaign]);

  useEffect(() => { setPv(null); setToken(null); setOpenDiv(null); void load(); }, [load]);
  // 동결이거나 백엔드가 단계를 모르면(구 백엔드) 쓰기 버튼을 잠근다.
  const frozen = !!st?.frozen || (!!st && !st.campaign);

  const issue = async (division: string) => {
    if (busy) return;
    setBusy(`token:${division}`); setMsg(null); setToken(null);
    try {
      const t = await api.admin.pikuCollectorToken(division);
      setToken({ division, token: t.token, expiresAt: t.expiresAt });
      setMsg({ ok: true,
               text: `${t.ttlSeconds / 60}분 동안 한 번만 쓸 수 있는 토큰입니다.` });
    } catch (e) {
      setMsg({ ok: false, text: errText(e) });
    } finally { setBusy(null); }
  };

  const publish = async () => {
    if (busy) return;
    setBusy("publish"); setMsg(null);
    try {
      const r = await api.admin.pikuCollectorPublish(campaign);
      setMsg({ ok: true, text: `${st?.campaignLabel ?? campaign}을(를) 공개했습니다. (${
        Object.values(r.rows).join(" / ")}행)` });
      await load();
    } catch (e) {
      // 실패를 성공으로 꾸미지 않는다.
      setMsg({ ok: false, text: errText(e) });
      await load();
    } finally { setBusy(null); }
  };

  if (loading && !st) {
    return (
      <p className="flex items-center gap-2 py-8 text-sm text-muted">
        <Loader2 size={15} className="animate-spin" aria-hidden="true" />
        Collector 상태를 불러오는 중…
      </p>
    );
  }

  // 차단 사유는 **서버가 준 것**을 그대로 쓴다. 화면에서 다시 계산하면 규칙이
  // 두 벌이 되고, 서버가 막는 이유와 화면이 말하는 이유가 갈라진다.
  const blockers = st?.blockers ?? [];

  return (
    <div className="space-y-5">
      <p className="flex items-start gap-2 text-sm text-muted">
        <Upload size={15} className="mt-0.5 shrink-0 text-accent" aria-hidden="true" />
        <span>
          수집 서버에서는 PIKU가 <b className="text-fg">403</b>으로 막혀 있습니다.
          대신 PIKU가 정상적으로 열리는 <b className="text-fg">이 브라우저</b>가 공개
          랭킹 표를 읽어 보냅니다. 서버는 받기만 하고 PIKU에 직접 요청하지 않습니다.
        </span>
      </p>

      {/* ── 단계 선택 ── */}
      <div className="nb-tap-gap flex flex-wrap items-center gap-1.5" role="tablist"
           aria-label="싱드컵 단계">
        {CAMPAIGNS.map((c) => {
          const on = campaign === c.k;
          return (
            <button key={c.k} type="button" role="tab" aria-selected={on}
                    tabIndex={on ? 0 : -1} onClick={() => setCampaign(c.k)}
                    className={`nb-tap rounded-lg border px-3 py-2 text-sm font-semibold
                                transition-colors ${on
                      ? "border-accent/50 bg-accent/10 text-fg"
                      : "border-border text-muted hover:text-fg"}`}>
              {c.label}
              <span className="ml-1.5 text-[11px] font-normal opacity-80">{c.hint}</span>
            </button>
          );
        })}
      </div>

      {/* 구 백엔드(배포 중간)는 campaign을 모른다 — 응답에 `campaign`이 없으면 그 사실을 적고
          공개를 잠근다. 새 백엔드가 뜨면 이 안내는 사라진다. */}
      {st && !st.campaign && (
        <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10
                                    px-3 py-2 text-[12.5px] leading-relaxed text-amber-200">
          <b>⚠ 백엔드가 아직 단계(예선/본선)를 모릅니다.</b> 서비스 갱신이 끝날 때까지
          이 화면은 예선 상태만 보여 주고 공개는 잠깁니다.
        </p>
      )}
      {frozen && (
        <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10
                                    px-3 py-2 text-[12.5px] leading-relaxed text-amber-200">
          <b>⚠ {st?.campaignLabel}은(는) 동결 상태입니다.</b> 새 수집·매핑 변경·공개가
          서버에서 거절됩니다(예선 마지막 구간 수집 누락, 소급 갱신 보류). 공개 중인 예선
          데이터는 그대로 유지됩니다.
        </p>
      )}

      {/* ── 자동 기능 상태 — 기본이 꺼짐이라는 사실을 먼저 밝힌다 ── */}
      {st && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl
                        border border-border bg-bg-card/60 px-4 py-3 text-sm">
          <span className="flex items-center gap-1.5">
            <span className="text-muted">자동 수집</span>
            <b className={st.autoCollectEnabled ? "text-accent" : "text-fg"}>
              {st.autoCollectEnabled ? "켜짐" : "꺼짐"}
            </b>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="text-muted">자동 공개</span>
            <b className={st.autoPublishEnabled ? "text-accent" : "text-fg"}>
              {st.autoPublishEnabled ? "켜짐" : "꺼짐"}
            </b>
          </span>
          <span className="text-muted">최소 간격 {st.minIntervalMinutes}분</span>
          <span className="text-xs text-muted/80">
            둘 다 기본이 꺼짐입니다. 자동 수집을 켜도{" "}
            <b className="text-fg">이 PC와 Chrome이 켜져 있을 때만</b> 갱신됩니다 —
            꺼져 있으면 그 시간대는 건너뜁니다.
          </span>
        </div>
      )}

      {err && <p role="alert" className="text-sm text-red-400">{err}</p>}
      {msg && (
        <p role="status"
           className={`text-sm ${msg.ok ? "text-accent" : "text-red-400"}`}>
          {msg.text}
        </p>
      )}

      {/* ── source별 상태 ── */}
      <div className="space-y-2">
        {sourceKeys(st).map((d) => {
          const v = st?.divisions[d];
          if (!v) return null;
          const ok = v.draftReady;
          return (
            <div key={d}
                 className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl
                            border border-border bg-bg-card/60 px-4 py-3">
              <span className="min-w-0 flex-1 text-sm font-bold text-fg">
                {v.label}
                <span className="ml-1.5 font-mono text-[11px] font-normal text-muted">
                  {v.sourceId}
                </span>
              </span>
              <StatePill d={v} />
              <span className="text-[13px] tabular-nums text-muted">
                수집 <b className={ok ? "text-accent" : "text-fg"}>{v.draftRows}</b>
                {" / "}{v.expected}행
              </span>
              <span className="hidden text-[12px] text-muted sm:inline">
                공개 중 {v.activeEntryCount}행 · {fmt(v.lastAt)}
              </span>
              <button type="button" onClick={() => void issue(d)}
                      disabled={!!busy || frozen}
                      className="btn-secondary nb-tap inline-flex shrink-0
                                 items-center gap-1 text-xs disabled:opacity-40"
                      title={frozen ? "동결된 단계에는 토큰을 발급하지 않습니다"
                        : "이 부문 수집에 쓸 1회용 토큰을 발급합니다"}>
                {busy === `token:${d}`
                  ? <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                  : <ClipboardCopy size={12} aria-hidden="true" />}
                수집 토큰
              </button>
              {v.draftCount > 0 && (
                <button type="button"
                        onClick={() => setOpenDiv(openDiv === d ? null : d)}
                        aria-expanded={openDiv === d}
                        className="btn-secondary nb-tap inline-flex shrink-0
                                   items-center gap-1 text-xs">
                  <ListChecks size={12} aria-hidden="true" />
                  {openDiv === d ? "매핑 닫기" : "매핑 검토"}
                </button>
              )}
              {openDiv === d && (
                <div className="w-full border-t border-border pt-3">
                  <PikuMappingReview division={d} label={v.label} readOnly={frozen}
                                     onChanged={() => void load()} />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ── 발급된 토큰 ── */}
      {token && (
        <div className="rounded-xl border border-accent/40 bg-accent/8 px-4 py-3">
          <p className="text-sm font-semibold text-fg">
            {st?.divisions[token.division]?.label} 수집 토큰
          </p>
          <p className="mt-1 text-[13px] leading-relaxed text-muted">
            확장 프로그램에 붙여 넣으세요. <b className="text-fg">한 번만</b> 쓸 수 있고{" "}
            {fmt(token.expiresAt)}에 만료됩니다. 이 값은 다시 볼 수 없습니다.
          </p>
          {/* 토큰 원문은 화면에 **한 번만** 둔다 — 복사 버튼은 그 값을 읽어 갈 뿐
              input value나 data 속성으로 복제하지 않는다. */}
          <div className="mt-2 flex items-start gap-2">
            <code className="min-w-0 flex-1 overflow-x-auto rounded-lg bg-bg-hover
                             px-3 py-2 font-mono text-[12px] text-fg">
              {token.token}
            </code>
            <button type="button"
                    onClick={async () => {
                      setCopied(await copyToken(token.token) === "copied"
                        ? "copied" : "failed");
                      window.setTimeout(() => setCopied(null), COPY_RESET_MS);
                    }}
                    aria-label="수집 토큰을 클립보드에 복사"
                    aria-live="polite"
                    className="nb-tap shrink-0 rounded-lg border border-border
                               bg-bg-card px-3 py-2 text-[13px] font-semibold
                               text-fg transition-colors hover:bg-bg-hover">
              {copied === "copied" ? "복사됨"
                : copied === "failed" ? "복사 실패" : "복사"}
            </button>
          </div>
          {copied === "failed" && (
            <p className="mt-1 text-[12px] text-muted">
              브라우저가 복사를 막았습니다. 위 값을 직접 선택해 복사해 주세요.
            </p>
          )}
        </div>
      )}

      {/* ── 공개 ── */}
      <div className="rounded-xl border border-border bg-bg-card/60 px-4 py-3">
        <p className="text-sm font-semibold text-fg">공개 — {st?.campaignLabel ?? campaign}</p>
        <p className="mt-1 text-[13px] leading-relaxed text-muted">
          이 단계의 source를 <b className="text-fg">하나의 작업으로</b> 공개합니다. 하나라도
          실패하면 기존 공개 데이터가 그대로 남습니다. 본선을 공개해도 예선 공개본은
          바뀌지 않습니다(단계가 분리돼 있습니다). 자동 공개는 없습니다.
        </p>
        {blockers.length > 0 && (
          <p className="mt-2 flex flex-wrap items-start gap-1.5 text-[13px] text-muted">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400"
                           aria-hidden="true" />
            <span>공개할 수 없는 이유 — <b className="text-fg">{blockers.join(", ")}</b></span>
          </p>
        )}
        <div className="nb-tap-gap mt-3 flex flex-wrap items-center gap-2">
        <button type="button" disabled={!!busy}
                onClick={() => void (async () => {
                  setBusy("preview"); setMsg(null);
                  try { setPv(await api.admin.pikuCollectorPublishPreview(campaign)); }
                  catch (e) { setMsg({ ok: false, text: errText(e) }); }
                  finally { setBusy(null); }
                })()}
                className="btn-secondary nb-tap inline-flex items-center gap-1.5
                           text-sm disabled:opacity-40"
                title="공개하면 무엇이 바뀌는지 봅니다. 저장하지 않습니다.">
          {busy === "preview"
            ? <Loader2 size={13} className="animate-spin" aria-hidden="true" />
            : <Eye size={13} aria-hidden="true" />}
          공개 전 확인
        </button>
        <button type="button" onClick={() => void publish()}
                disabled={!!busy || !st?.publishReady || frozen}
                className="btn-primary nb-tap mt-3 inline-flex items-center gap-1.5
                           text-sm disabled:opacity-40"
                title={frozen ? "동결된 단계는 공개할 수 없습니다"
                  : st?.publishReady
                    ? "이 단계의 수집본을 공개합니다"
                    : "이 단계의 수집본이 모두 있어야 공개할 수 있습니다"}>
          {busy === "publish"
            ? <Loader2 size={13} className="animate-spin" aria-hidden="true" />
            : <Check size={13} aria-hidden="true" />}
          {st?.campaignLabel ?? campaign} 공개
        </button>
        </div>

        {/* 공개하면 무엇이 바뀌는지. 값이 아니라 **변화**를 보여 준다. */}
        {pv && (
          <div className="mt-3 rounded-lg border border-border bg-bg-hover p-3">
            <p className="text-[13px] text-muted">
              내부 정렬 기준 <b className="text-fg">{pv.sortLabel}</b>
              {" — 숫자는 공개 화면·API 어디에도 나가지 않습니다."}
            </p>
            <ul className="mt-1.5 space-y-1 text-[13px]">
              {Object.keys(pv.divisions).map((d) => {
                const x = pv.divisions[d];
                if (!x) return null;
                return (
                  <li key={d} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <b className="text-fg">{x.label}</b>
                    <span className="tabular-nums text-muted">
                      수집 {x.draftRows} / 공개 중 {x.activeRows}
                    </span>
                    <span className="text-muted">순위 변경 {x.changed}</span>
                    <span className="text-muted">신규 {x.added} · 빠짐 {x.removed}</span>
                    <span className={x.unconfirmed ? "text-amber-300" : "text-accent"}>
                      미확정 {x.unconfirmed}
                    </span>
                    {x.duplicate > 0 && (
                      <span className="font-bold text-red-300">중복 {x.duplicate}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => void load()} disabled={!!busy}
                className="btn-secondary nb-tap inline-flex items-center gap-1.5
                           text-sm disabled:opacity-40">
          <RefreshCw size={13} aria-hidden="true" /> 상태 새로고침
        </button>
        <span className="flex items-center gap-1.5 text-[12px] text-muted">
          <ShieldAlert size={13} className="shrink-0" aria-hidden="true" />
          확장 프로그램에는 어떤 secret도 저장하지 않습니다. 쿠키·원문 HTML도 보내지
          않습니다.
        </span>
      </div>
    </div>
  );
}
