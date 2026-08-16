"use client";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Search, Loader2, X, Hash } from "lucide-react";
import { api } from "@/lib/api";
import type { RisingTag, TagStreamer, RisingTagEffect } from "@/lib/types";

import StreamerAvatar from "./StreamerAvatar";

// 태그 검색 — 스트리머가 방송에 붙인 태그로 라이브를 찾는다.
// 태그는 자유 입력이라 표기가 제각각이므로 기본은 부분 일치로 찾고,
// 인기 태그 칩으로 탐색도 가능하게 한다.

const GREEN = "#00FFA3";
const YELLOW_GRAD = "linear-gradient(90deg, #FBBF24, #F59E0B)";
const CYAN_GRAD = "linear-gradient(90deg, #06B6D4, #00FFA3)";
const nf = (n: number) => n.toLocaleString("ko-KR");
const DAY_MS = 24 * 3600 * 1000;
const MEDALS = [{ color: "#FBBF24" }, { color: "#D1D5DB" }, { color: "#D97706" }] as const;

function dur(openDate: string): { ms: number; label: string } {
  if (!openDate) return { ms: -1, label: "-" };
  const start = new Date(openDate.replace(" ", "T") + "+09:00").getTime();
  if (isNaN(start)) return { ms: -1, label: "-" };
  const ms = Date.now() - start;
  if (ms < 0) return { ms: -1, label: "-" };
  const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000);
  return { ms, label: h > 0 ? `${h}시간 ${m}분` : `${m}분` };
}

// ── 태그 유/무 시청자 비교 ──────────────────────────────────────────────────
const SLATE = "#64748B";

function StatRow({ label, a, b, unit, lift }:
  { label: string; a: number | null; b: number | null; unit: string; lift: number | null }) {
  const fmt = (v: number | null) => (v == null ? "-" : `${nf(v)}${unit}`);
  return (
    <div className="flex items-center gap-2 border-t border-border py-2 text-xs first:border-t-0">
      <span className="w-[92px] shrink-0 text-muted">{label}</span>
      <span className="w-[74px] text-right font-bold tabular-nums" style={{ color: GREEN }}>{fmt(a)}</span>
      <span className="text-[10px] text-muted/60">vs</span>
      <span className="w-[74px] text-right font-bold tabular-nums" style={{ color: SLATE }}>{fmt(b)}</span>
      <span className="ml-auto text-right text-[11px] font-semibold tabular-nums"
            style={{ color: lift == null ? "rgb(var(--color-muted-rgb))" : lift >= 0 ? GREEN : "#EF4444" }}>
        {lift == null ? "-" : `${lift >= 0 ? "+" : ""}${lift}%`}
      </span>
    </div>
  );
}

function TagEffect({ tag }: { tag: string | null }) {
  const [d, setD] = useState<RisingTagEffect | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.rising.tagEffect(tag ?? undefined)
      .then((r) => { if (alive) setD(r); })
      .catch(() => { if (alive) setD(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tag]);

  const a = d?.tagged, b = d?.untagged;
  const enough = a && b && a.channels > 0 && b.channels > 0;

  return (
    <div className="mt-4 rounded-xl border border-border p-4" style={{ background: "#181A20" }}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h4 className="text-sm font-bold text-white">
          {tag ? <>‘{tag}’ 태그 유입 효과</> : "태그 유입 효과 요약"}
        </h4>
        <span className="flex items-center gap-2 text-[10px]">
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: GREEN }} />
            <span style={{ color: "#9CA3AF" }}>태그 사용{a ? ` ${nf(a.channels)}개` : ""}</span></span>
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full" style={{ background: SLATE }} />
            <span style={{ color: "#9CA3AF" }}>미사용{b ? ` ${nf(b.channels)}개` : ""}</span></span>
        </span>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-6 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" /> 비교 중...
        </div>
      ) : !enough ? (
        <p className="py-6 text-center text-[11px]" style={{ color: "#9CA3AF" }}>
          비교할 방송이 부족합니다. {tag && "이 태그를 쓴 방송이 적거나, 같은 카테고리에 비교군이 없습니다."}
        </p>
      ) : (
        <div className="mt-3">
          <StatRow label="평균 동시 시청자" a={a!.avg_viewers} b={b!.avg_viewers} unit="명" lift={d!.lift.viewers} />
          <StatRow label="평균 방송 시간" a={a!.avg_hours} b={b!.avg_hours} unit="시간" lift={d!.lift.hours} />
          <StatRow label="채널당 팔로워" a={a!.avg_follower} b={b!.avg_follower} unit="명" lift={d!.lift.follower} />
          {(a!.avg_follower_gain != null || b!.avg_follower_gain != null) && (
            <StatRow label="24h 팔로워 유입" a={a!.avg_follower_gain} b={b!.avg_follower_gain}
                     unit="명" lift={d!.lift.follower_gain} />
          )}
        </div>
      )}

      <p className="mt-3 text-[11px]" style={{ color: "#9CA3AF" }}>
        * {tag
            ? "선택한 태그를 쓴 방송과, 같은 카테고리에서 그 태그를 쓰지 않은 방송을 비교합니다."
            : "태그를 하나라도 단 방송과 태그가 없는 방송을 비교합니다."}
        {" "}최신 수집 사이클의 라이브 방송 기준이며, 팔로워 유입은 24시간 전 대비입니다.
      </p>
    </div>
  );
}

export default function TagSearch() {
  const [tags, setTags] = useState<RisingTag[]>([]);
  const [tagsLoading, setTagsLoading] = useState(true);
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState<string | null>(null);
  const [rows, setRows] = useState<TagStreamer[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    api.rising.tags(80)
      .then((d) => { if (alive) setTags(d.tags || []); })
      .catch(() => { if (alive) setTags([]); })
      .finally(() => { if (alive) setTagsLoading(false); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!applied) { setRows([]); return; }
    let alive = true;
    setLoading(true);
    api.rising.tagStreamers(applied)
      .then((d) => { if (alive) setRows(d.streamers || []); })
      .catch(() => { if (alive) setRows([]); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [applied]);

  const items = useMemo(() => rows.map((s) => ({ ...s, d: dur(s.open_date) })), [rows]);
  const maxV = Math.max(1, ...items.map((s) => s.concurrent_viewers));
  const maxF = Math.max(1, ...items.map((s) => s.follower_count));

  const submit = (v?: string) => {
    const kw = (v ?? q).trim();
    if (kw) { setQ(kw); setApplied(kw); }
  };

  return (
    <div className="space-y-5">
      <div className="card !p-4 md:!p-5">
        <h3 className="section-title mb-1">태그 검색</h3>
        <p className="text-xs text-muted mb-4">
          스트리머가 방송에 붙인 태그로 현재 라이브를 찾습니다. 태그 일부만 입력해도 됩니다.
        </p>

        <form onSubmit={(e) => { e.preventDefault(); submit(); }}
              className="flex items-center gap-2 flex-wrap">
          <div className="relative min-w-[200px] flex-1 max-w-sm">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="예: 신입, 버추얼, 소통"
              className="w-full rounded-lg border border-border bg-bg py-2 pl-9 pr-3 text-sm text-fg
                         placeholder-muted focus:border-accent focus:outline-none" />
          </div>
          <button type="submit"
            className="rounded-lg px-4 py-2 text-sm font-bold text-[#04140d]"
            style={{ background: `linear-gradient(135deg, ${GREEN}, #00C2FF)` }}>
            검색
          </button>
          {applied && (
            <button type="button" onClick={() => { setApplied(null); setQ(""); }}
              className="inline-flex items-center gap-1 text-xs font-medium text-muted transition-colors hover:text-fg">
              <X size={12} /> 초기화
            </button>
          )}
        </form>

        {/* 인기 태그 칩 */}
        <div className="mt-4">
          <p className="mb-2 text-[11px] text-muted">현재 라이브에서 많이 쓰인 태그</p>
          {tagsLoading ? (
            <div className="flex items-center gap-2 py-3 text-sm text-muted">
              <Loader2 size={15} className="animate-spin" /> 태그를 불러오는 중...
            </div>
          ) : tags.length === 0 ? (
            <p className="py-3 text-sm text-muted">태그가 달린 방송이 아직 없습니다.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {tags.slice(0, 30).map((t) => {
                const on = applied === t.tag;
                return (
                  <button key={t.tag} type="button" onClick={() => submit(t.tag)}
                    className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors"
                    style={{ background: on ? "rgba(0,255,163,0.1)" : "transparent",
                             borderColor: on ? "rgba(0,255,163,0.35)" : "rgb(var(--color-border-rgb))",
                             color: on ? GREEN : "rgb(var(--color-muted-rgb))" }}>
                    <Hash size={11} />{t.tag}
                    <span className="text-[10px] text-muted/70">{t.lives}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* 태그 유/무 비교 — 선택된 태그가 있으면 그 태그 기준, 없으면 전체 요약 */}
        <TagEffect tag={applied} />
      </div>

      {applied && (
        <div className="card !p-4 md:!p-5">
          <div className="mb-4 flex items-center justify-between gap-3 flex-wrap">
            <h3 className="section-title flex items-center gap-2 flex-wrap">
              <span className="inline-flex items-center gap-1 rounded-full border border-border bg-bg-hover px-3 py-1 text-xs font-medium text-fg">
                <Hash size={11} />{applied}
              </span>
              태그가 달린 방송
            </h3>
            {!loading && <span className="text-[11px] text-muted">{nf(items.length)}개 방송</span>}
          </div>

          {loading ? (
            <div className="flex items-center justify-center gap-2 py-16 text-muted">
              <Loader2 size={18} className="animate-spin" /> 검색 중...
            </div>
          ) : items.length === 0 ? (
            <p className="py-12 text-center text-sm text-muted">
              이 태그를 단 방송을 찾지 못했습니다. 태그 일부만 입력해 보세요.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[800px]">
                <thead>
                  <tr className="border-b border-border text-xs text-muted">
                    <th className="w-12 py-2 pl-2 text-left font-medium">#</th>
                    <th className="py-2 text-left font-medium">스트리머</th>
                    <th className="py-2 px-6 text-left font-medium hidden lg:table-cell">태그</th>
                    <th className="py-2 px-6 text-right font-medium">전체 시청자</th>
                    <th className="py-2 px-6 text-right font-medium hidden md:table-cell">방송시간</th>
                    <th className="py-2 px-6 text-right font-medium">팔로워</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((s, i) => {
                    const medal = MEDALS[i];
                    return (
                      <tr key={s.chzzk_channel_id} className="border-b border-border transition-colors hover:bg-bg-hover/70">
                        <td className="py-3.5 pl-2 align-top text-sm tabular-nums">
                          {medal ? <span className="font-extrabold" style={{ color: medal.color }}>#{i + 1}</span>
                                 : <span className="text-muted">{i + 1}</span>}
                        </td>
                        <td className="py-3.5 align-top">
                          <Link href={`/stats/streamer/${s.chzzk_channel_id}`} className="group flex items-center gap-2">
                            <StreamerAvatar
                              src={s.channel_image_url} index={i}
                              ringStyle={medal ? { boxShadow: `0 0 0 2px ${medal.color}` } : undefined} />
                            <span className="truncate max-w-[150px] text-base font-semibold text-fg transition-colors group-hover:text-accent md:max-w-none">
                              {s.channel_name}
                            </span>
                          </Link>
                          <p className="mt-0.5 truncate text-[11px] text-muted max-w-[220px]">{s.category_name || "-"}</p>
                        </td>
                        <td className="py-3.5 px-6 align-middle hidden lg:table-cell">
                          <span className="flex flex-wrap gap-1">
                            {s.tags.slice(0, 4).map((t) => (
                              <span key={t} onClick={() => submit(t)}
                                className="cursor-pointer rounded-full border border-border bg-bg-hover px-2 py-0.5 text-[10px] text-muted hover:text-fg">
                                {t}
                              </span>
                            ))}
                          </span>
                        </td>
                        <td className="py-3.5 px-6 align-top" style={{ minWidth: 140 }}>
                          <div className="flex flex-col gap-1.5">
                            <div className="text-right text-sm font-bold tabular-nums text-fg">
                              {nf(s.concurrent_viewers)}<span className="ml-0.5 text-[11px] font-normal text-muted">명</span>
                            </div>
                            <span className="block h-[3px] overflow-hidden rounded-full bg-bg-hover">
                              <span className="block h-full rounded-full"
                                    style={{ width: `${(s.concurrent_viewers / maxV) * 100}%`, background: YELLOW_GRAD }} />
                            </span>
                          </div>
                        </td>
                        <td className="py-3.5 px-6 align-top hidden md:table-cell text-right text-sm tabular-nums text-muted">
                          {s.d.label}
                        </td>
                        <td className="py-3.5 px-6 align-top" style={{ minWidth: 140 }}>
                          <div className="flex flex-col gap-1.5">
                            <div className="text-right text-sm font-bold tabular-nums text-fg">
                              {s.follower_count > 0
                                ? <>{nf(s.follower_count)}<span className="ml-0.5 text-[11px] font-normal text-muted">명</span></>
                                : <span className="text-muted">-</span>}
                            </div>
                            <span className="block h-[3px] overflow-hidden rounded-full bg-bg-hover">
                              <span className="block h-full rounded-full"
                                    style={{ width: `${(s.follower_count / maxF) * 100}%`, background: CYAN_GRAD }} />
                            </span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 text-[11px] text-muted/70">
            * 최신 수집 사이클 기준이며, 태그는 스트리머가 자유롭게 입력하는 값이라 표기가
            제각각일 수 있습니다. 방송시간은 최대 {DAY_MS / 3600000}시간 기준입니다.
          </p>
        </div>
      )}
    </div>
  );
}
