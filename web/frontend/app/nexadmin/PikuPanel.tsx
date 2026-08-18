"use client";
/**
 * PIKU 사용자 투표 순위 관리 (OWNER 전용).
 *
 * 이 화면이 하는 일은 셋이다.
 *  1. **부문 ↔ URL 매핑** — 세 URL의 부문 대응을 운영자가 직접 정한다(추측 금지).
 *  2. **갱신** — 수동 수집(실제 접속) 또는 JSON/CSV import(접속 없음).
 *  3. **이름 매핑** — PIKU 이름을 공식 예선 참가자에 **명시적으로** 연결한다.
 *     자동 유사도 매칭은 없다. 확정 전에는 순위에 들어가지 않는다.
 *
 * 화면에 **우승 비율·승률 숫자를 표시하지 않는다.** 서버 응답에도 없다 —
 * 진단에 필요한 것은 건수와 매핑 현황이다.
 */
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle, Check, Download, Link2, Loader2, RefreshCw, Upload, X,
  Eye,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  PikuAdminStatus, PikuMapping, PikuMappingsResponse,
} from "@/lib/types";

const DIVISIONS = ["female_solo", "male_solo", "groups"] as const;
const LABELS: Record<string, string> = {
  female_solo: "여성 솔로", male_solo: "남성 솔로", groups: "그룹",
};

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const fmt = (unix: number) =>
  unix ? new Date(unix * 1000).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "-";

/** 실패 종류를 사람이 읽을 문구로. **우회 방법은 알려 주지 않는다.** */
const ERROR_LABEL: Record<string, string> = {
  forbidden: "접근 거부(403) — 중단했습니다",
  rate_limited: "요청 제한(429) — 재시도 후 중단",
  challenge: "Cloudflare 확인 화면 — 중단했습니다",
  parse_failed: "표 데이터를 찾지 못함",
  empty: "결과가 비어 있음",
  bad_rate: "비율 값이 범위를 벗어남",
  missing_url: "주소 미설정",
  http_error: "응답 오류",
  timeout: "응답 지연",
  unexpected: "알 수 없는 오류",
};

export default function PikuPanel() {
  const [status, setStatus] = useState<PikuAdminStatus | null>(null);
  const [maps, setMaps] = useState<PikuMappingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [importDiv, setImportDiv] = useState<string>("female_solo");
  const [importText, setImportText] = useState("");
  const [preview, setPreview] = useState<
    { entries: number; matched: string[]; unmatched: string[];
      duplicates: string[] } | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const [s, m] = await Promise.all([
        api.admin.pikuStatus(), api.admin.pikuMappings()]);
      setStatus(s);
      setMaps(m);
      setUrls(Object.fromEntries(s.sources.map((x) => [x.division, x.url])));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const run = async (key: string, fn: () => Promise<unknown>, okText: string) => {
    if (busy) return;
    setBusy(key); setMsg(null);
    try {
      await fn();
      setMsg({ ok: true, text: okText });
      await load();
    } catch (e) {
      // 실패는 실패로 보여 준다 — 성공으로 꾸미지 않는다.
      setMsg({ ok: false, text: errText(e) });
    } finally {
      setBusy(null);
    }
  };

  const saveSources = () =>
    run("sources", () => api.admin.pikuSetSources(urls), "부문 매핑을 저장했습니다.");

  const collect = (d: string) =>
    run(`collect:${d}`, () => api.admin.pikuCollect(d),
        `${LABELS[d]} 수집을 완료했습니다.`);

  /** 실제 응답으로 **검증만** — 저장하지 않으므로 형식이 틀려도 기존 데이터가 남는다. */
  const previewLive = (d: string) =>
    run(`preview:${d}`, () => api.admin.pikuPreviewLive(d),
        `${LABELS[d]} 응답을 확인했습니다. 아직 반영하지 않았습니다.`);

  /** 세 부문 일괄 — **부분 성공을 공개하지 않는다.** */
  const collectAll = async () => {
    if (busy) return;
    setBusy("collect-all"); setMsg(null);
    try {
      const r = await api.admin.pikuCollectAll();
      if (r.published) {
        setMsg({ ok: true, text: "세 부문을 모두 반영했습니다." });
      } else {
        const failed = Object.entries(r.errors)
          .map(([d, k]) => `${LABELS[d] ?? d}(${k})`).join(", ");
        // 성공하지 않았는데 성공 UI를 보이지 않는다.
        setMsg({ ok: false,
                 text: `일부 부문이 실패해 아무것도 반영하지 않았습니다: ${failed}` });
      }
      await load();
    } catch (e) {
      setMsg({ ok: false, text: errText(e) });
    } finally { setBusy(null); }
  };

  const importBody = () => {
    const text = importText.trim();
    if (!text) return null;
    const isJson = text.startsWith("[") || text.startsWith("{");
    return isJson ? { division: importDiv, rows: JSON.parse(text) }
                  : { division: importDiv, csv: text };
  };

  const doImport = () => {
    const body = importBody();
    if (!body) { setMsg({ ok: false, text: "가져올 데이터를 붙여 넣어 주세요." }); return; }
    return run("import", () => api.admin.pikuImport(body),
      `${LABELS[importDiv]} 데이터를 반영했습니다.`);
  };

  /* 반영 **전에** 형태만 본다. 활성 dataset을 건드리지 않으므로 형식이 틀려도
     마지막 정상 데이터가 그대로 남는다. */
  const doPreview = async () => {
    const body = importBody();
    if (!body) { setMsg({ ok: false, text: "확인할 데이터를 붙여 넣어 주세요." }); return; }
    if (busy) return;
    setBusy("preview"); setMsg(null); setPreview(null);
    try {
      const r = await api.admin.pikuPreview(body);
      setPreview(r);
      setMsg({ ok: true, text: `${r.entries}건을 확인했습니다. 아직 반영하지 않았습니다.` });
    } catch (e) {
      setMsg({ ok: false, text: errText(e) });
    } finally { setBusy(null); }
  };

  const setMapping = (m: PikuMapping, channelId: string | null) =>
    run(`map:${m.division}:${m.pikuName}`,
        () => api.admin.pikuSetMapping({
          division: m.division, pikuName: m.pikuName,
          channelId, state: channelId ? "confirmed" : "unmapped" }),
        channelId ? "연결했습니다." : "연결을 해제했습니다.");

  const counts = status?.mappingCounts ?? {};
  const pending = (maps?.mappings ?? []).filter((m) => m.state !== "confirmed");

  return (
    <div className="space-y-5">
      <p className="flex items-start gap-2 text-sm text-muted">
        <Link2 size={15} className="mt-0.5 shrink-0 text-accent" aria-hidden="true" />
        <span>
          PIKU 공개 랭킹 페이지에서 사용자 투표 결과를 읽어 <b className="text-fg">순위만</b>{" "}
          화면에 씁니다. 우승 비율·승률 수치는 내부 정렬에만 쓰이며 공개 화면과 API,
          이 관리 화면 어디에도 표시하지 않습니다.
        </span>
      </p>

      {/* ── 자동 수집 상태 ── */}
      {status && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border
                        border-border bg-bg-card/60 px-4 py-3 text-sm">
          <span className="flex items-center gap-1.5">
            <span className="text-muted">자동 수집</span>
            <b className={status.autoCollectEnabled ? "text-accent" : "text-fg"}>
              {status.autoCollectEnabled ? "켜짐" : "꺼짐"}
            </b>
          </span>
          <span className="text-muted">최소 간격 {status.intervalMinutes}분</span>
          <span className="text-muted">최대 {status.maxPages}페이지</span>
          {!status.autoCollectEnabled && (
            <span className="text-xs text-muted/80">
              기본값은 꺼짐입니다. 켜려면 <code className="rounded bg-bg-hover px-1">
              PIKU_AUTO_COLLECT_ENABLED=true</code> 환경변수가 필요합니다.
            </span>
          )}
        </div>
      )}

      {err && <p role="alert" className="text-sm text-red-400">{err}</p>}
      {msg && (
        <p role="status"
           className={`text-sm ${msg.ok ? "text-accent" : "text-red-400"}`}>
          {msg.text}
        </p>
      )}

      {loading ? (
        <p className="py-8 text-center text-sm text-muted" aria-busy="true">
          <Loader2 size={16} className="mr-1.5 inline animate-spin" /> 불러오는 중…
        </p>
      ) : (
        <>
          {/* ── 1) 부문 ↔ URL ── */}
          <section className="space-y-2">
            <h3 className="text-sm font-bold text-fg">부문별 PIKU 주소</h3>
            <p className="text-xs leading-relaxed text-muted">
              세 주소가 각각 어느 부문인지 <b className="text-fg">직접 지정</b>합니다.
              추측하지 않습니다. 세 부문 모두 필요하며 같은 주소를 두 부문에 넣을 수 없습니다.
            </p>
            <div className="space-y-2">
              {DIVISIONS.map((d) => {
                const s = status?.sources.find((x) => x.division === d);
                return (
                  <div key={d} className="flex flex-wrap items-center gap-2 rounded-lg
                                          border border-border/60 bg-bg p-2.5">
                    <span className="w-20 shrink-0 text-sm font-semibold text-fg">
                      {LABELS[d]}
                    </span>
                    <input value={urls[d] ?? ""} spellCheck={false}
                           onChange={(e) => setUrls((p) => ({ ...p, [d]: e.target.value }))}
                           placeholder="https://www.piku.co.kr/w/rank/XXXXXX"
                           aria-label={`${LABELS[d]} PIKU 주소`}
                           className="min-w-0 flex-1 rounded-lg border border-border
                                      bg-bg-card px-2 py-1.5 font-mono text-sm"
                           style={{ minWidth: 220 }} />
                    {/* 확인이 먼저, 반영이 나중 — 순서를 배치에도 둔다. */}
                    <button onClick={() => void previewLive(d)}
                            disabled={!!busy || !s?.url}
                            className="btn-secondary nb-tap inline-flex shrink-0 items-center
                                       gap-1 text-xs disabled:opacity-40"
                            title={s?.url ? "저장하지 않고 응답만 확인합니다"
                                          : "주소를 먼저 저장해 주세요"}>
                      {busy === `preview:${d}`
                        ? <Loader2 size={12} className="animate-spin" />
                        : <Eye size={12} />}
                      먼저 확인
                    </button>
                    <button onClick={() => void collect(d)}
                            disabled={!!busy || !s?.url}
                            className="btn-secondary nb-tap inline-flex shrink-0 items-center
                                       gap-1 text-xs disabled:opacity-40"
                            title={s?.url ? "지금 PIKU에 접속해 갱신합니다"
                                          : "주소를 먼저 저장해 주세요"}>
                      {busy === `collect:${d}`
                        ? <Loader2 size={12} className="animate-spin" />
                        : <RefreshCw size={12} />}
                      수동 갱신
                    </button>
                    <span className="w-full text-[11px] text-muted">
                      {/* 시도와 성공을 나눠 보여 준다 — 하나로 합치면 실패가 이어져도
                          최신인 것처럼 보인다. */}
                      마지막 성공 {fmt(s?.lastSuccessAt ?? 0)} · 마지막 시도{" "}
                      {fmt(s?.lastAttemptAt ?? 0)}
                      {s?.lastErrorKind && (
                        <span className="ml-1.5 text-red-400">
                          · {ERROR_LABEL[s.lastErrorKind] ?? s.lastErrorKind}
                        </span>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
            {/* 정본과 어긋난 배치를 화면에서 먼저 알린다 — 수집이 돌기 전에
                잡아야 남성 순위가 그룹 부문에 들어가는 사고를 막는다. */}
            {(status?.sources ?? []).some((x) => x.divisionMismatch) && (
              <p role="alert"
                 className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-3
                            py-2 text-xs text-amber-300">
                부문과 주소가 서로 맞지 않습니다. 저장된 주소를 확인해 주세요 —
                이 상태로는 수집이 반영되지 않습니다.
              </p>
            )}
            <button onClick={() => void collectAll()}
                    disabled={!!busy}
                    className="btn-secondary nb-tap inline-flex items-center gap-1.5
                               text-sm disabled:opacity-40"
                    title="세 부문을 모두 수집합니다. 하나라도 실패하면 아무것도 반영하지 않습니다.">
              {busy === "collect-all"
                ? <Loader2 size={13} className="animate-spin" />
                : <RefreshCw size={13} />}
              세 부문 일괄 수집
            </button>
            <button onClick={() => void saveSources()} disabled={!!busy}
                    className="btn-primary nb-tap inline-flex items-center gap-1.5 text-sm
                               disabled:opacity-50">
              {busy === "sources" ? <Loader2 size={14} className="animate-spin" />
                : <Check size={14} />}
              부문 매핑 저장
            </button>
          </section>

          {/* ── 2) 수동 import ── */}
          <section className="space-y-2">
            <h3 className="text-sm font-bold text-fg">수동 가져오기 (JSON / CSV)</h3>
            <p className="text-xs leading-relaxed text-muted">
              PIKU에 접속하지 않고 데이터를 넣습니다. 수집과{" "}
              <b className="text-fg">같은 검증·같은 원자 교체</b>를 거치므로 형식이
              잘못되면 기존 데이터는 그대로 남습니다.
              CSV 헤더는 <code className="rounded bg-bg-hover px-1">
              name,winRate,matchRate</code> 입니다.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1.5 text-sm">
                <span className="text-muted">부문</span>
                <select value={importDiv} onChange={(e) => setImportDiv(e.target.value)}
                        className="rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm">
                  {DIVISIONS.map((d) => (
                    <option key={d} value={d}>{LABELS[d]}</option>
                  ))}
                </select>
              </label>
              {/* 확인이 먼저, 반영이 나중이다 — 순서를 그대로 배치에 둔다. */}
              <button onClick={() => void doPreview()} disabled={!!busy}
                      className="btn-secondary nb-tap inline-flex items-center gap-1.5 text-sm
                                 disabled:opacity-40">
                {busy === "preview" ? <Loader2 size={13} className="animate-spin" />
                  : <Eye size={13} />}
                먼저 확인
              </button>
              <button onClick={() => void doImport()} disabled={!!busy}
                      className="btn-secondary nb-tap inline-flex items-center gap-1.5 text-sm
                                 disabled:opacity-40">
                {busy === "import" ? <Loader2 size={13} className="animate-spin" />
                  : <Upload size={13} />}
                가져오기
              </button>
            </div>
            <textarea value={importText} onChange={(e) => setImportText(e.target.value)}
                      rows={6} spellCheck={false}
                      aria-label="가져올 JSON 또는 CSV"
                      placeholder={'name,winRate,matchRate\n고다요,62.5,51.0'}
                      className="w-full rounded-lg border border-border bg-bg px-2.5 py-2
                                 font-mono text-xs" />

            {/* 미리보기 결과 — **숫자(비율·승률)는 담지 않는다.** 확인에 필요한 것은
                "몇 건이 통과했고 누가 매칭되지 않았는가"이지 값 자체가 아니다. */}
            {preview && (
              <div className="rounded-lg border border-border bg-bg-card/60 p-3 text-xs">
                <p className="font-medium text-fg">
                  {preview.entries}건 확인됨 —{" "}
                  <span className="text-amber-400">아직 반영하지 않았습니다.</span>
                </p>
                <dl className="mt-2 grid gap-1 sm:grid-cols-3">
                  {([["참가자와 일치", preview.matched],
                     ["일치하지 않음", preview.unmatched],
                     ["중복된 이름", preview.duplicates]] as const).map(([label, list]) => (
                    <div key={label} className="min-w-0">
                      <dt className="text-muted">{label} ({list.length})</dt>
                      <dd className="mt-0.5 break-words text-fg">
                        {list.length ? list.slice(0, 8).join(", ") : "-"}
                        {list.length > 8 ? ` 외 ${list.length - 8}` : ""}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </section>

          {/* ── 3) 이름 매핑 ── */}
          <section className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-bold text-fg">
                이름 매핑{" "}
                <span className="font-normal text-muted">
                  확정 {counts.confirmed ?? 0} · 제안 {counts.suggested ?? 0} ·
                  미매핑 {counts.unmapped ?? 0}
                </span>
              </h3>
            </div>
            <p className="text-xs leading-relaxed text-muted">
              PIKU 이름과 공식 참가자 이름이 다를 수 있어{" "}
              <b className="text-fg">자동으로 확정하지 않습니다</b>. 정확히 일치하는
              이름만 후보로 제안하며, 확정하기 전에는 순위에 들어가지 않습니다.
            </p>

            {pending.length === 0 ? (
              <p className="rounded-lg border border-border/60 bg-bg p-3 text-sm text-muted">
                확정하지 않은 이름이 없습니다.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {pending.slice(0, 100).map((m) => {
                  const cands = (maps?.candidates ?? [])
                    .filter((c) => c.division === m.division);
                  const key = `map:${m.division}:${m.pikuName}`;
                  return (
                    <li key={key}
                        className="flex flex-wrap items-center gap-2 rounded-lg border
                                   border-border bg-bg-card/60 px-2.5 py-2">
                      <span className="shrink-0 rounded border border-border px-1.5 py-0.5
                                       text-[10px] font-bold text-muted">
                        {LABELS[m.division] ?? m.division}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">
                        {m.pikuName}
                      </span>
                      {m.state === "suggested" && (
                        <span className="shrink-0 rounded border border-amber-500/40
                                         px-1.5 py-0.5 text-[10px] font-bold text-amber-400">
                          이름 일치 후보
                        </span>
                      )}
                      <select defaultValue={m.channelId ?? ""}
                              aria-label={`${m.pikuName} 연결할 참가자`}
                              onChange={(e) => void setMapping(m, e.target.value || null)}
                              disabled={busy === key}
                              className="min-w-0 max-w-[16rem] flex-1 rounded-lg border
                                         border-border bg-bg px-2 py-1.5 text-sm">
                        <option value="">— 연결 안 함 —</option>
                        {cands.map((c) => (
                          <option key={c.channelId} value={c.channelId}>
                            {c.name}{c.team ? ` (${c.team}팀)` : ""}
                          </option>
                        ))}
                      </select>
                    </li>
                  );
                })}
              </ul>
            )}
            {pending.length > 100 && (
              <p className="text-xs text-muted">…외 {pending.length - 100}건</p>
            )}
          </section>

          {/* ── 4) 실행 이력 ── */}
          <section className="space-y-2">
            <h3 className="text-sm font-bold text-fg">최근 실행</h3>
            {(status?.runs ?? []).length === 0 ? (
              <p className="text-sm text-muted">아직 실행 기록이 없습니다.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-sm">
                  <thead>
                    <tr className="border-b border-border text-xs text-muted">
                      <th className="py-2 text-left font-medium">시각</th>
                      <th className="py-2 text-left font-medium">부문</th>
                      <th className="py-2 text-left font-medium">결과</th>
                      <th className="py-2 text-right font-medium">페이지</th>
                      <th className="py-2 text-right font-medium">항목</th>
                      <th className="py-2 text-left font-medium">반영</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(status?.runs ?? []).map((r, i) => (
                      <tr key={i} className="border-b border-border/60">
                        <td className="py-2 tabular-nums text-muted">{fmt(r.started_at)}</td>
                        <td className="py-2">{LABELS[r.division] ?? r.division}</td>
                        <td className="py-2">
                          {r.ok ? (
                            <span className="text-accent">정상</span>
                          ) : (
                            <span className="flex items-center gap-1 text-red-400">
                              <AlertTriangle size={12} aria-hidden="true" />
                              {ERROR_LABEL[r.error_kind] ?? r.error_kind}
                              {r.http_status ? ` (${r.http_status})` : ""}
                            </span>
                          )}
                        </td>
                        <td className="py-2 text-right tabular-nums text-muted">{r.pages}</td>
                        <td className="py-2 text-right tabular-nums text-muted">{r.entries}</td>
                        <td className="py-2">
                          {r.applied
                            ? <span className="text-accent">적용</span>
                            : <span className="text-muted">미적용</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-[11px] leading-relaxed text-muted/80">
              실패한 실행은 <b>기존 데이터를 덮지 않습니다</b>. 모든 페이지가 정상일 때만
              새 결과로 교체하며, 그 전까지는 마지막 정상 데이터가 그대로 유지됩니다.
              응답 원문은 저장하지 않습니다.
            </p>
          </section>
        </>
      )}
    </div>
  );
}
