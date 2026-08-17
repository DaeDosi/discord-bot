"use client";
/* 그룹 분석 (UI-T 요구 2).
 *
 * **랭킹 제외와 독립이다.** `전체 스트리머 랭킹에서 제외` 옵션은 *랭킹*에서 멤버를
 * 빼는 정책이고, 그룹을 이 화면에서 숨기라는 뜻이 아니다. 공식 그룹도 여기서는
 * 보인다. 서버가 그 필드를 아예 내려보내지 않으므로 화면에서 실수로 거를 수도 없다.
 *
 * 정보 위계는 "그룹 고르기 → 그룹 요약 → 멤버"다. 요약(멤버·라이브·시청자)을 먼저
 * 두는 이유는 목록을 다 읽지 않고도 그룹 상태를 한 줄로 알 수 있어야 하기 때문이다.
 *
 * 모바일은 표를 그대로 줄이지 않고 **카드**로 바꾼다. 열을 좁히면 채널명이 두 글자로
 * 잘려 표의 의미가 사라진다(같은 이유로 `md:` 미만에서는 표 자체를 렌더하지 않는다).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Loader2, Radio, Search, Users } from "lucide-react";

import { StreamerTagBadge } from "@/components/StreamerTag";
import { api } from "@/lib/api";
import type { GroupAnalysisMember, GroupDetail, GroupSummary } from "@/lib/types";

import { fmtEpoch, liveDuration, nf } from "./singcupShared";

type SortKey = "rank" | "viewers" | "name" | "duration";
const SORTS: { k: SortKey; label: string }[] = [
  { k: "rank", label: "그룹 순위" },
  { k: "viewers", label: "현재 시청자" },
  { k: "duration", label: "방송 시간" },
  { k: "name", label: "이름" },
];

/* ── 요약 칩 ─────────────────────────────────────────────────────────────── */
function Stat({ label, value, accent }: {
  label: string; value: string; accent?: boolean;
}) {
  return (
    <div className="min-w-0 rounded-xl border border-border bg-bg-card/60 px-4 py-3">
      <p className="text-[11px] text-muted">{label}</p>
      <p className={`mt-0.5 truncate text-lg font-extrabold tabular-nums ${
        accent ? "text-accent" : "text-fg"}`}>{value}</p>
    </div>
  );
}

/* ── 멤버 한 명 ──────────────────────────────────────────────────────────── */
function MemberRow({ m }: { m: GroupAnalysisMember }) {
  const dur = m.live ? liveDuration(m.openDate) : null;
  return (
    <tr className="border-b border-border/60 last:border-0">
      <td className="w-12 py-2.5 pl-2 text-left text-sm tabular-nums text-muted">
        {m.groupRank ?? "-"}
      </td>
      <td className="py-2.5">
        <a href={`https://chzzk.naver.com/${m.live ? "live/" : ""}${m.channelId}`}
           target="_blank" rel="noopener noreferrer"
           className="nb-tap flex min-w-0 items-center gap-2 hover:text-accent">
          {m.channelImageUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={m.channelImageUrl} alt="" width={28} height={28}
                 loading="lazy" className="h-7 w-7 shrink-0 rounded-full object-cover"
                 onError={(e) => { e.currentTarget.style.display = "none"; }} />
          )}
          <span className="min-w-0 truncate text-sm font-medium">{m.channelName}</span>
          {m.live && (
            <span className="nb-live-badge shrink-0 rounded px-1 py-0.5 text-[9px] font-bold">
              LIVE
            </span>
          )}
        </a>
      </td>
      <td className="hidden px-4 py-2.5 text-left text-xs text-muted sm:table-cell">
        <span className="line-clamp-1">{m.categoryName || "-"}</span>
      </td>
      <td className="px-4 py-2.5 text-right text-sm tabular-nums">
        {/* 방송 중인데 0명인 것과 방송을 안 하는 것은 다르다. */}
        {m.live ? `${nf(m.concurrentViewers)}명` : <span className="text-muted">오프라인</span>}
      </td>
      <td className="hidden px-4 py-2.5 text-right text-xs tabular-nums text-muted md:table-cell">
        {dur ? dur.label : "-"}
      </td>
      <td className="hidden px-4 py-2.5 text-right text-xs tabular-nums text-muted lg:table-cell">
        {m.followerCount ? nf(m.followerCount) : "-"}
      </td>
    </tr>
  );
}

function MemberCard({ m }: { m: GroupAnalysisMember }) {
  const dur = m.live ? liveDuration(m.openDate) : null;
  return (
    <a href={`https://chzzk.naver.com/${m.live ? "live/" : ""}${m.channelId}`}
       target="_blank" rel="noopener noreferrer"
       className="nb-tap flex items-center gap-3 rounded-xl border border-border
                  bg-bg-card/60 p-3">
      <span className="w-6 shrink-0 text-center text-xs tabular-nums text-muted">
        {m.groupRank ?? "-"}
      </span>
      {m.channelImageUrl && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={m.channelImageUrl} alt="" width={36} height={36} loading="lazy"
             className="h-9 w-9 shrink-0 rounded-full object-cover"
             onError={(e) => { e.currentTarget.style.display = "none"; }} />
      )}
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="min-w-0 truncate text-sm font-medium">{m.channelName}</span>
          {m.live && (
            <span className="nb-live-badge shrink-0 rounded px-1 py-0.5 text-[9px] font-bold">
              LIVE
            </span>
          )}
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-muted">
          {m.live
            ? `${nf(m.concurrentViewers)}명 시청${dur ? ` · ${dur.label}` : ""}`
            : "오프라인"}
          {m.categoryName ? ` · ${m.categoryName}` : ""}
        </span>
      </span>
    </a>
  );
}

/* ── 본체 ────────────────────────────────────────────────────────────────── */
export default function GroupAnalysis() {
  const [groups, setGroups] = useState<GroupSummary[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<GroupDetail | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("rank");

  useEffect(() => {
    let alive = true;
    api.rising.groups()
      .then((d) => {
        if (!alive) return;
        setGroups(d.groups ?? []);
        if (d.groups?.length) setSelected(d.groups[0].id);
      })
      .catch(() => { if (alive) setListErr("그룹 목록을 불러오지 못했습니다."); });
    return () => { alive = false; };
  }, []);

  const load = useCallback((id: number) => {
    let alive = true;
    setLoadingDetail(true); setDetailErr(null);
    api.rising.groupDetail(id)
      .then((d) => { if (alive) setDetail(d); })
      .catch((e) => {
        if (alive) {
          setDetail(null);
          setDetailErr(e instanceof Error ? e.message : "불러오지 못했습니다.");
        }
      })
      .finally(() => { if (alive) setLoadingDetail(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => { if (selected !== null) return load(selected); }, [selected, load]);

  const rows = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const a = (detail?.members ?? []).filter(
      (m) => !kw || m.channelName.toLowerCase().includes(kw));
    const by = {
      // 순위가 없는(오프라인) 멤버는 항상 뒤로 — 0으로 섞으면 켜진 사람과 뒤엉킨다.
      rank: (x: GroupAnalysisMember, y: GroupAnalysisMember) =>
        (x.groupRank ?? 1e9) - (y.groupRank ?? 1e9),
      viewers: (x: GroupAnalysisMember, y: GroupAnalysisMember) =>
        Number(y.live) - Number(x.live) || y.concurrentViewers - x.concurrentViewers,
      duration: (x: GroupAnalysisMember, y: GroupAnalysisMember) =>
        Number(y.live) - Number(x.live)
        || liveDuration(y.openDate).ms - liveDuration(x.openDate).ms,
      name: (x: GroupAnalysisMember, y: GroupAnalysisMember) =>
        x.channelName.localeCompare(y.channelName, "ko"),
    }[sort];
    return [...a].sort(by);
  }, [detail, q, sort]);

  if (listErr) {
    return (
      <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-6">
        <p className="flex items-center gap-2 text-sm font-semibold text-red-400">
          <AlertCircle size={16} /> 그룹 목록을 불러오지 못했습니다.
        </p>
        <p className="mt-1 text-xs text-muted">잠시 후 다시 시도해 주세요.</p>
      </div>
    );
  }
  if (groups === null) {
    return (
      <div className="flex items-center justify-center gap-2 py-24 text-muted" aria-busy>
        <Loader2 size={18} className="animate-spin" /> 그룹을 불러오는 중...
      </div>
    );
  }
  if (groups.length === 0) {
    return (
      <div className="card px-5 py-14 text-center">
        <Users size={34} className="mx-auto mb-3 text-muted opacity-40" />
        <p className="font-medium text-fg">분석할 그룹이 아직 없습니다.</p>
        <p className="mt-1.5 text-sm leading-relaxed text-muted">
          그룹은 운영자가 지정하며, 소속 스트리머가 한 명 이상 있어야 여기에 나옵니다.
        </p>
      </div>
    );
  }

  const g = detail?.group;
  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-bold text-fg">그룹 분석</h2>
        <p className="mt-1 text-sm leading-relaxed text-muted">
          운영자가 지정한 소속 그룹의 멤버가 지금 얼마나 방송 중인지 봅니다.
          <b className="text-fg"> 랭킹에서 제외된 그룹도 여기서는 그대로 보입니다</b> —
          제외는 랭킹 정책이지 그룹을 숨기는 설정이 아닙니다.
        </p>
      </div>

      {/* 그룹 선택 — 개수가 적어 탭형 버튼이 드롭다운보다 빠르다. */}
      <div className="nb-tap-gap flex flex-wrap gap-2">
        {groups.map((x) => {
          const active = x.id === selected;
          return (
            <button key={x.id} onClick={() => { setSelected(x.id); setQ(""); }}
              aria-pressed={active}
              className={`nb-tap inline-flex items-center gap-2 rounded-lg border px-3
                          py-1.5 text-xs font-medium transition-colors ${
                active ? "border-accent/50 bg-accent/10 text-fg"
                       : "border-border text-muted hover:text-fg"}`}>
              <StreamerTagBadge tag={x} />
              <span className="tabular-nums opacity-70">{x.memberCount}</span>
            </button>
          );
        })}
      </div>

      {detailErr ? (
        <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-6">
          <p className="text-sm font-semibold text-red-400">{detailErr}</p>
        </div>
      ) : loadingDetail || !detail ? (
        <div className="flex items-center justify-center gap-2 py-20 text-muted" aria-busy>
          <Loader2 size={18} className="animate-spin" /> 불러오는 중...
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="멤버" value={`${nf(detail.memberCount)}명`} />
            <Stat label="현재 라이브" value={`${nf(detail.liveCount)}명`} accent />
            <Stat label="전체 동시 시청자" value={`${nf(detail.totalViewers)}명`} accent />
            <Stat label="최근 수집"
                  value={fmtEpoch(detail.collectedAt)} />
          </div>

          {detail.memberCount === 0 ? (
            <div className="card px-5 py-14 text-center">
              <p className="font-medium text-fg">
                {g?.name}에 등록된 멤버가 없습니다.
              </p>
              <p className="mt-1.5 text-sm text-muted">
                운영자가 멤버를 지정하면 여기에 표시됩니다.
              </p>
            </div>
          ) : (
            <div className="card !p-4 md:!p-5">
              <div className="nb-tap-gap mb-4 flex flex-wrap items-center gap-2">
                <label className="relative min-w-0 flex-1 sm:max-w-[240px]">
                  <Search size={14} aria-hidden="true"
                          className="pointer-events-none absolute left-3 top-1/2
                                     -translate-y-1/2 text-muted" />
                  <input value={q} onChange={(e) => setQ(e.target.value)}
                         maxLength={40} placeholder="멤버 검색"
                         aria-label="그룹 멤버 검색"
                         className="nb-tap w-full rounded-lg border border-border
                                    bg-bg-hover/40 py-1.5 pl-8 pr-3 text-xs
                                    outline-none focus:border-accent/50" />
                </label>
                <span className="text-xs text-muted">정렬</span>
                {SORTS.map((o) => {
                  const active = sort === o.k;
                  return (
                    <button key={o.k} onClick={() => setSort(o.k)} aria-pressed={active}
                      className={`nb-tap inline-flex items-center rounded-lg border px-3
                                  py-1.5 text-xs font-medium transition-colors ${
                        active ? "border-accent/50 bg-accent/10 text-accent"
                               : "border-border text-muted hover:text-fg"}`}>
                      {o.label}
                    </button>
                  );
                })}
              </div>

              {rows.length === 0 ? (
                <p className="py-14 text-center text-sm text-muted">
                  &lsquo;{q}&rsquo;와 일치하는 멤버가 없습니다.
                </p>
              ) : (
                <>
                  {/* 모바일: 카드. 표를 좁히면 채널명이 잘려 의미가 사라진다. */}
                  <div className="flex flex-col gap-2 md:hidden">
                    {rows.map((m) => <MemberCard key={m.channelId} m={m} />)}
                  </div>
                  <div className="hidden overflow-x-auto md:block">
                    <table className="w-full min-w-[560px] text-sm">
                      <thead>
                        <tr className="border-b border-border text-xs text-muted">
                          <th className="w-12 py-2 pl-2 text-left font-medium">#</th>
                          <th className="py-2 text-left font-medium">스트리머</th>
                          <th className="hidden px-4 py-2 text-left font-medium sm:table-cell">
                            카테고리
                          </th>
                          <th className="px-4 py-2 text-right font-medium">현재 시청자</th>
                          <th className="hidden px-4 py-2 text-right font-medium md:table-cell">
                            방송 시간
                          </th>
                          <th className="hidden px-4 py-2 text-right font-medium lg:table-cell">
                            팔로워
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((m) => <MemberRow key={m.channelId} m={m} />)}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {detail.truncated && (
                <p className="mt-3 text-center text-[11px] text-muted">
                  멤버가 많아 일부만 표시했습니다.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
