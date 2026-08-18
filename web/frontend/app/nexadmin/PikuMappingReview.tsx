"use client";
/**
 * PIKU 이름 매핑 확정 (OWNER 전용).
 *
 * **이 화면이 없으면 Publish해도 공개 순위가 빈다.** `public_ranking`은 관리자가
 * 확정(`confirmed`)한 매핑만 순위에 넣는데, 정확 일치는 `suggested`까지만
 * 만들어지기 때문이다.
 *
 * 설계 판단 셋:
 *  · **자동 확정 버튼을 두지 않는다.** 한 글자 차이로 다른 스트리머에게 붙으면
 *    순위가 통째로 틀어지고 그 오류는 공개 화면에서 보이지 않는다. 대신
 *    "정확히 일치한 N건"을 **개수와 함께** 보여 주고 운영자가 누른다.
 *  · **좁은 화면에서는 표가 아니라 카드다.** 260px에서 8열 표는 가로로 밀린다.
 *  · **우승 비율·승률을 쓰지 않는다.** 확정에 필요한 정보가 아니다.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Check, Link2, Loader2, Search, X,
} from "lucide-react";
import { api } from "@/lib/api";
import type { PikuCollectorMappings, PikuMappingRow } from "@/lib/types";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const STATE_LABEL: Record<string, string> = {
  confirmed: "확정",
  suggested: "정확 일치(미확정)",
  unmapped: "미매칭",
};

function StatePill({ row }: { row: PikuMappingRow }) {
  const tone = row.duplicate
    ? "border-red-500/40 bg-red-500/10 text-red-300"
    : row.state === "confirmed" ? "border-accent/40 bg-accent/10 text-accent"
    : row.state === "suggested" ? "border-amber-400/40 bg-amber-400/10 text-amber-300"
    : "border-border bg-transparent text-muted";
  return (
    <span className={`inline-flex shrink-0 items-center rounded-md border px-1.5
                      py-0.5 text-[11px] font-bold leading-none ${tone}`}>
      {row.duplicate ? "중복 연결" : (STATE_LABEL[row.state] ?? row.state)}
    </span>
  );
}

export default function PikuMappingReview({ division, label, onChanged }: {
  division: string;
  label: string;
  /** 확정 상태가 바뀌면 바깥 Collector 패널의 차단 사유를 다시 읽게 한다. */
  onChanged?: () => void;
}) {
  const [data, setData] = useState<PikuCollectorMappings | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [onlyPending, setOnlyPending] = useState(true);
  const [q, setQ] = useState("");
  /** 후보를 고르는 중인 행(PIKU 이름). 한 번에 하나만 연다. */
  const [editing, setEditing] = useState<string | null>(null);
  const [candQ, setCandQ] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      setData(await api.admin.pikuCollectorMappings(division));
    } catch (e) {
      setErr(errText(e));
    } finally { setLoading(false); }
  }, [division]);

  useEffect(() => { void load(); }, [load]);

  const run = async (key: string, fn: () => Promise<unknown>, ok: string) => {
    if (busy) return;
    setBusy(key); setMsg(null);
    try {
      await fn();
      setMsg({ ok: true, text: ok });
      await load();
      onChanged?.();
    } catch (e) {
      // 실패를 성공으로 꾸미지 않는다.
      setMsg({ ok: false, text: errText(e) });
    } finally { setBusy(null); }
  };

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    const needle = q.trim().toLowerCase();
    return all.filter((r) => {
      if (onlyPending && r.state === "confirmed" && !r.duplicate) return false;
      if (!needle) return true;
      return [r.pikuName, r.teamMembers, r.songTitle, r.artistName, r.officialName]
        .some((v) => (v || "").toLowerCase().includes(needle));
    });
  }, [data, onlyPending, q]);

  const candidates = useMemo(() => {
    const all = data?.candidates ?? [];
    const needle = candQ.trim().toLowerCase();
    if (!needle) return all.slice(0, 30);
    return all.filter((c) => c.name.toLowerCase().includes(needle)).slice(0, 30);
  }, [data, candQ]);

  if (loading && !data) {
    return (
      <p className="flex items-center gap-2 py-6 text-sm text-muted">
        <Loader2 size={15} className="animate-spin" aria-hidden="true" />
        {label} 매핑을 불러오는 중…
      </p>
    );
  }
  if (err) return <p role="alert" className="text-sm text-red-400">{err}</p>;
  if (!data) return null;

  const c = data.counts;
  const exact = c.suggested;

  return (
    <div className="space-y-3">
      {/* ── 현황 ── */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[13px]">
        <span className="font-bold text-fg">{data.label}</span>
        <span className="tabular-nums text-muted">
          {data.rows.length} / {data.expected}행
        </span>
        <span className="text-accent">확정 {c.confirmed}</span>
        <span className="text-amber-300">정확 일치 {c.suggested}</span>
        <span className="text-muted">미매칭 {c.unmatched}</span>
        {c.duplicate > 0 && (
          <span className="font-bold text-red-300">중복 {c.duplicate}</span>
        )}
      </div>

      {msg && (
        <p role="status"
           className={`text-[13px] ${msg.ok ? "text-accent" : "text-red-400"}`}>
          {msg.text}
        </p>
      )}

      {/* ── 일괄 확정 — 개수를 밝히고 누르게 한다 ── */}
      <div className="nb-tap-gap flex flex-wrap items-center gap-2">
        <button type="button" disabled={!!busy || exact === 0}
                onClick={() => void run("confirm",
                  () => api.admin.pikuCollectorConfirmExact(division),
                  `정확히 일치한 ${exact}건을 확정했습니다.`)}
                className="btn-primary nb-tap inline-flex items-center gap-1.5
                           text-sm disabled:opacity-40"
                title={exact > 0
                  ? `정확히 일치한 ${exact}건만 확정합니다`
                  : "확정할 정확 일치 항목이 없습니다"}>
          {busy === "confirm"
            ? <Loader2 size={13} className="animate-spin" aria-hidden="true" />
            : <Check size={13} aria-hidden="true" />}
          정확 일치 {exact}건 확정
        </button>
        {/* 체크박스 자체는 브라우저가 그리는 16px 상자다. 그것을 키우면 다른
            폼과 모양이 어긋나므로, **라벨이 히트 영역을 갖게** 한다 —
            `<label>`이 감싸고 있어 어디를 눌러도 토글된다. 체크박스에는
            `nb-tap-icon`을 주지 않는다(상자가 44px로 늘어나 흉해진다). */}
        <label className="nb-tap nb-tap-wide inline-flex cursor-pointer items-center
                          gap-1.5 rounded-lg px-2 text-[13px] text-muted
                          hover:bg-bg-hover">
          <input type="checkbox" checked={onlyPending}
                 onChange={(e) => setOnlyPending(e.target.checked)}
                 className="h-4 w-4 shrink-0 accent-current" />
          미확정만 보기
        </label>
        <span className="relative">
          <Search size={13} aria-hidden="true"
                  className="pointer-events-none absolute left-2.5 top-1/2
                             -translate-y-1/2 text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
                 aria-label={`${label} 매핑 검색`} placeholder="이름·곡 검색"
                 className="nb-tap w-[168px] rounded-lg border border-border
                            bg-bg-hover py-1.5 pl-7 pr-2 text-[13px] text-fg" />
        </span>
      </div>

      {/* ── 목록 — 좁은 화면에서는 카드로 쌓인다(표는 260px에서 밀린다) ── */}
      {rows.length === 0 ? (
        <p className="rounded-lg border border-border bg-bg-card/60 px-3 py-4
                      text-center text-[13px] text-muted">
          {onlyPending ? "미확정 항목이 없습니다." : "표시할 행이 없습니다."}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((r) => (
            <li key={r.pikuName}
                className="rounded-lg border border-border bg-bg-card/60 px-3 py-2">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="w-8 shrink-0 text-right text-[11px]
                                 tabular-nums text-muted">{r.rank}</span>
                <span className="min-w-0 flex-1 break-words text-sm font-semibold text-fg">
                  {r.pikuName}
                </span>
                <StatePill row={r} />
              </div>
              {/* 그룹은 전체 팀원 문자열을 함께 보여 준다 — 대표자만 보면
                  공식 명단과 대조할 수 없다. */}
              {r.teamMembers && (
                <p className="mt-0.5 pl-10 text-[11.5px] leading-snug text-muted">
                  팀 {r.teamMembers}
                </p>
              )}
              {(r.songTitle || r.artistName) && (
                <p className="mt-0.5 pl-10 text-[11.5px] leading-snug text-muted">
                  {[r.songTitle, r.artistName].filter(Boolean).join(" - ")}
                </p>
              )}
              <div className="mt-1 flex flex-wrap items-center gap-2 pl-10">
                <span className="text-[12px] text-muted">
                  연결 대상{" "}
                  <b className={r.officialName ? "text-fg" : "text-muted"}>
                    {r.officialName || "없음"}
                  </b>
                </span>
                <button type="button" disabled={!!busy}
                        onClick={() => {
                          setEditing(editing === r.pikuName ? null : r.pikuName);
                          setCandQ("");
                        }}
                        aria-expanded={editing === r.pikuName}
                        className="btn-secondary nb-tap inline-flex items-center
                                   gap-1 text-xs disabled:opacity-40">
                  <Link2 size={12} aria-hidden="true" /> 변경
                </button>
                {r.channelId && (
                  <button type="button" disabled={!!busy}
                          onClick={() => void run(`clear:${r.pikuName}`,
                            () => api.admin.pikuCollectorSetMapping({
                              division, pikuName: r.pikuName, channelId: null }),
                            "연결을 해제했습니다.")}
                          className="btn-secondary nb-tap inline-flex items-center
                                     gap-1 text-xs disabled:opacity-40">
                    <X size={12} aria-hidden="true" /> 해제
                  </button>
                )}
              </div>

              {/* 후보 선택 — 채널 id를 손으로 넣지 않는다(오타가 곧 오연결이다) */}
              {editing === r.pikuName && (
                <div className="mt-2 rounded-lg border border-border bg-bg-hover p-2">
                  <input value={candQ} onChange={(e) => setCandQ(e.target.value)}
                         aria-label="공식 참가자 검색" placeholder="공식 참가자 이름"
                         className="nb-tap mb-1.5 w-full rounded-lg border
                                    border-border bg-bg px-2 py-1.5 text-[13px]" />
                  <ul className="max-h-48 space-y-0.5 overflow-y-auto">
                    {candidates.map((cd) => (
                      <li key={cd.channelId}>
                        <button type="button" disabled={!!busy || cd.taken}
                                onClick={() => void run(`set:${r.pikuName}`,
                                  () => api.admin.pikuCollectorSetMapping({
                                    division, pikuName: r.pikuName,
                                    channelId: cd.channelId }),
                                  `${cd.name}에 연결했습니다.`)}
                                title={cd.taken
                                  ? "이미 다른 행에 연결돼 있습니다"
                                  : `${cd.name}에 연결`}
                                className="nb-tap flex w-full items-center
                                           justify-between gap-2 rounded px-2
                                           py-1.5 text-left text-[13px]
                                           hover:bg-bg disabled:opacity-40">
                          <span className="min-w-0 break-words text-fg">{cd.name}</span>
                          {cd.taken && (
                            <span className="shrink-0 text-[11px] text-muted">사용 중</span>
                          )}
                        </button>
                      </li>
                    ))}
                    {candidates.length === 0 && (
                      <li className="px-2 py-1.5 text-[13px] text-muted">
                        일치하는 공식 참가자가 없습니다.
                      </li>
                    )}
                  </ul>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {c.duplicate > 0 && (
        <p className="flex items-start gap-1.5 text-[13px] text-red-300">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          같은 공식 참가자에 두 행이 연결돼 있습니다. 한쪽을 해제해야 공개할 수 있습니다.
        </p>
      )}
    </div>
  );
}
