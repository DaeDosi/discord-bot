"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Search, Tag as TagIcon, X, ArrowUp, ArrowDown, Check } from "lucide-react";
import { api } from "@/lib/api";
import { StreamerTagBadge } from "@/components/StreamerTag";
import type {
  StreamerTag, StreamerTagAdmin, StreamerTagSearchItem, TagGradientDirection,
} from "@/lib/types";

/** 방향 선택지 — **닫힌 목록**이다. 서버도 같은 목록으로 검증한다.
 *  자유 입력으로 바꾸지 말 것: 그 문자열이 곧 CSS에 들어간다. */
const DIRECTIONS: { value: TagGradientDirection; label: string }[] = [
  { value: "to-right",        label: "→ 오른쪽" },
  { value: "to-bottom-right", label: "↘ 오른쪽 아래" },
  { value: "to-bottom",       label: "↓ 아래" },
  { value: "to-top-right",    label: "↗ 오른쪽 위" },
];

const HEX = /^#[0-9a-fA-F]{6}$/;

/** 미리보기용 임시 태그. 저장 전이라 id가 없으므로 0을 쓴다. */
function draftTag(d: {
  name: string; colorMode: "solid" | "gradient";
  colorStart: string; colorEnd: string; gradientDirection: TagGradientDirection;
}): StreamerTag {
  return {
    id: 0, name: d.name || "태그 이름", slug: "", kind: "team",
    colorMode: d.colorMode, colorStart: d.colorStart,
    colorEnd: d.colorMode === "gradient" ? d.colorEnd : null,
    gradientDirection: d.gradientDirection,
  };
}

/** 색상 입력 한 칸 — 색상 피커와 hex 텍스트를 함께 둔다.
 *  피커만 두면 정확한 브랜드 색을 넣을 수 없고, 텍스트만 두면 고르기 불편하다. */
function ColorField({ label, value, onChange }: {
  label: string; value: string; onChange: (v: string) => void;
}) {
  const ok = HEX.test(value);
  return (
    <label className="flex min-w-0 flex-1 flex-col gap-1">
      <span className="text-xs font-semibold text-muted">{label}</span>
      <span className="flex items-center gap-2">
        <input type="color" value={ok ? value : "#38bdf8"}
               onChange={(e) => onChange(e.target.value)}
               className="h-8 w-10 shrink-0 cursor-pointer rounded border border-border bg-transparent"
               aria-label={`${label} 색상 선택`} />
        <input type="text" value={value} spellCheck={false}
               onChange={(e) => onChange(e.target.value)}
               placeholder="#38BDF8" maxLength={7}
               className={`min-w-0 flex-1 rounded-lg border bg-bg px-2 py-1.5 text-sm
                           ${ok ? "border-border" : "border-red-500/60"}`} />
      </span>
      {!ok && <span className="text-[11px] text-red-400">#RRGGBB 형식으로 입력해 주세요.</span>}
    </label>
  );
}

/** 태그 만들기 / 고치기 폼. `editing`이 있으면 수정 모드다. */
function TagForm({ editing, onDone, onCancel }: {
  editing?: StreamerTagAdmin | null;
  onDone: () => void;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(editing?.name ?? "");
  const [colorMode, setColorMode] = useState<"solid" | "gradient">(
    editing?.colorMode ?? "solid");
  const [colorStart, setColorStart] = useState(editing?.colorStart ?? "#38bdf8");
  const [colorEnd, setColorEnd] = useState(editing?.colorEnd ?? "#c084fc");
  const [dir, setDir] = useState<TagGradientDirection>(
    editing?.gradientDirection ?? "to-right");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const preview = draftTag({ name, colorMode, colorStart, colorEnd, gradientDirection: dir });
  const valid = name.trim().length > 0 && HEX.test(colorStart)
    && (colorMode === "solid" || HEX.test(colorEnd));

  const submit = async () => {
    if (!valid || busy) return;
    setBusy(true); setMsg(null);
    try {
      const body = {
        name: name.trim(), colorMode, colorStart,
        colorEnd: colorMode === "gradient" ? colorEnd : null,
        gradientDirection: dir,
      };
      if (editing) await api.admin.streamerTagUpdate(editing.id, body);
      else await api.admin.streamerTagCreate(body);
      setMsg({ ok: true, text: editing ? "수정했습니다." : "만들었습니다." });
      if (!editing) setName("");
      onDone();
    } catch (e) {
      // 서버 문구를 그대로 보여 준다 — 여기서 다시 쓰면 같은 규칙에 설명이 둘이 된다.
      setMsg({ ok: false, text: e instanceof Error ? e.message : "저장에 실패했습니다." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-xl border border-border bg-bg-card/60 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-0 flex-1 flex-col gap-1" style={{ minWidth: 180 }}>
          <span className="text-xs font-semibold text-muted">태그 이름</span>
          <input value={name} onChange={(e) => setName(e.target.value)}
                 maxLength={20} placeholder="예: 이세돌"
                 className="rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs font-semibold text-muted">색상 방식</span>
          <select value={colorMode}
                  onChange={(e) => setColorMode(e.target.value as "solid" | "gradient")}
                  className="rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm">
            <option value="solid">단일색</option>
            <option value="gradient">그라데이션</option>
          </select>
        </label>
      </div>

      <div className="flex flex-wrap items-start gap-3">
        <ColorField label={colorMode === "solid" ? "색상" : "시작 색상"}
                    value={colorStart} onChange={setColorStart} />
        {colorMode === "gradient" && (
          <>
            <ColorField label="끝 색상" value={colorEnd} onChange={setColorEnd} />
            <label className="flex flex-col gap-1">
              <span className="text-xs font-semibold text-muted">방향</span>
              <select value={dir}
                      onChange={(e) => setDir(e.target.value as TagGradientDirection)}
                      className="rounded-lg border border-border bg-bg px-2.5 py-1.5 text-sm">
                {DIRECTIONS.map((d) => (
                  <option key={d.value} value={d.value}>{d.label}</option>
                ))}
              </select>
            </label>
          </>
        )}
      </div>

      {/* 실제 화면에 쓰이는 것과 **같은 컴포넌트**로 미리 본다. 따로 그리면 드리프트한다. */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 bg-bg p-3">
        <span className="text-xs font-semibold text-muted">미리보기</span>
        <StreamerTagBadge tag={preview} />
        <span className="text-sm font-semibold text-fg">스트리머 이름</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button onClick={submit} disabled={!valid || busy}
                className="btn-primary inline-flex items-center gap-1.5 text-sm disabled:opacity-50">
          {editing ? <Check size={14} /> : <Plus size={14} />}
          {busy ? "저장 중…" : editing ? "수정 저장" : "태그 만들기"}
        </button>
        {onCancel && (
          <button onClick={onCancel} className="btn-secondary text-sm">취소</button>
        )}
        {msg && (
          <span role="status"
                className={`text-sm ${msg.ok ? "text-accent" : "text-red-400"}`}>
            {msg.text}
          </span>
        )}
      </div>
    </div>
  );
}

/** 한 스트리머의 태그 지정 카드 — 지정/해제/순서 변경. */
function StreamerRow({ item, tags, maxPerStreamer, onChanged }: {
  item: StreamerTagSearchItem;
  tags: StreamerTagAdmin[];
  maxPerStreamer: number;
  onChanged: (channelId: string, next: StreamerTag[]) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const assignedIds = useMemo(() => new Set(item.tags.map((t) => t.id)), [item.tags]);
  const assignable = tags.filter((t) => t.active && !assignedIds.has(t.id));
  const full = item.tags.length >= maxPerStreamer;

  const run = async (fn: () => Promise<{ tags: StreamerTag[] }>) => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const res = await fn();
      onChanged(item.channelId, res.tags);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "요청에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const move = (idx: number, delta: number) => {
    const next = [...item.tags];
    const j = idx + delta;
    if (j < 0 || j >= next.length) return;
    [next[idx], next[j]] = [next[j], next[idx]];
    void run(() => api.admin.streamerTagReorder(item.channelId, next.map((t) => t.id)));
  };

  return (
    <div className="space-y-2 rounded-xl border border-border bg-bg-card/60 p-3">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <span className="min-w-0 truncate text-sm font-semibold text-fg">
          {item.channelName || "(이름 미상)"}
        </span>
        <code className="shrink-0 rounded bg-bg-hover px-1.5 py-0.5 text-[11px] text-muted">
          {item.channelId.slice(0, 8)}…
        </code>
      </div>

      {/* 현재 지정 — 순서가 곧 화면 노출 순서다 */}
      {item.tags.length === 0 ? (
        <p className="text-xs text-muted">지정된 태그가 없습니다.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {item.tags.map((t, i) => (
            <li key={t.id} className="flex min-w-0 items-center gap-1.5">
              <span className="w-5 shrink-0 text-center text-[11px] tabular-nums text-muted">
                {i + 1}
              </span>
              <StreamerTagBadge tag={t} />
              {i >= 2 && (
                <span className="shrink-0 text-[11px] text-muted"
                      title={`목록 화면에서는 앞 2개만 보이고 나머지는 +N으로 접힙니다.`}>
                  (목록에서 접힘)
                </span>
              )}
              <span className="ml-auto flex shrink-0 items-center gap-1">
                <button onClick={() => move(i, -1)} disabled={busy || i === 0}
                        aria-label={`${t.name} 위로`} className="btn-secondary !px-1.5 !py-1 disabled:opacity-40">
                  <ArrowUp size={13} />
                </button>
                <button onClick={() => move(i, 1)} disabled={busy || i === item.tags.length - 1}
                        aria-label={`${t.name} 아래로`} className="btn-secondary !px-1.5 !py-1 disabled:opacity-40">
                  <ArrowDown size={13} />
                </button>
                <button onClick={() => void run(() =>
                          api.admin.streamerTagUnassign(item.channelId, t.id))}
                        disabled={busy} aria-label={`${t.name} 해제`}
                        className="btn-secondary !px-1.5 !py-1 disabled:opacity-40">
                  <X size={13} />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* 지정 — 이미 붙은 태그는 목록에서 빠진다(중복 지정 자체가 불가능하다) */}
      <div className="flex flex-wrap items-center gap-1.5">
        {full ? (
          <span className="text-xs text-muted">
            태그는 최대 {maxPerStreamer}개까지 지정할 수 있습니다.
          </span>
        ) : assignable.length === 0 ? (
          <span className="text-xs text-muted">붙일 수 있는 태그가 없습니다.</span>
        ) : (
          assignable.map((t) => (
            <button key={t.id} disabled={busy}
                    onClick={() => void run(() =>
                      api.admin.streamerTagAssign(item.channelId, t.id))}
                    className="inline-flex items-center gap-1 rounded-md border border-dashed
                               border-border px-1.5 py-0.5 text-[11px] font-semibold
                               text-muted transition-colors hover:text-fg disabled:opacity-40">
              <Plus size={11} /> {t.name}
            </button>
          ))
        )}
      </div>

      {err && <p role="alert" className="text-xs text-red-400">{err}</p>}
    </div>
  );
}

export default function StreamerTagsPanel() {
  const [tags, setTags] = useState<StreamerTagAdmin[]>([]);
  const [maxPerStreamer, setMax] = useState(5);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<StreamerTagAdmin | null>(null);
  const [showInactive, setShowInactive] = useState(false);

  const [keyword, setKeyword] = useState("");
  const [results, setResults] = useState<StreamerTagSearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setLoadErr(null);
    try {
      const res = await api.admin.streamerTagsList(true);
      setTags(res.tags);
      setMax(res.maxPerStreamer);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "태그 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const search = async () => {
    const kw = keyword.trim();
    // 최소 길이는 서버도 막지만, 여기서 먼저 막아 헛요청을 줄인다.
    if (kw.length < 2) { setSearchErr("검색어는 2자 이상이어야 합니다."); return; }
    setSearching(true); setSearchErr(null);
    try {
      const res = await api.admin.streamerTagSearch(kw);
      setResults(res.streamers);
      if (res.streamers.length === 0) setSearchErr("검색 결과가 없습니다.");
    } catch (e) {
      setSearchErr(e instanceof Error ? e.message : "검색에 실패했습니다.");
    } finally {
      setSearching(false);
    }
  };

  /** 지정/해제/순서 변경 후 서버가 준 최종 태그 목록으로 그 행만 갈아 끼운다.
   *  전체를 다시 검색하지 않는다 — 목록이 흔들려 방금 만진 행을 놓친다. */
  const onRowChanged = (channelId: string, next: StreamerTag[]) => {
    setResults((prev) => prev.map((r) =>
      r.channelId === channelId ? { ...r, tags: next } : r));
    void load();          // 태그별 지정 수를 다시 센다
  };

  const visibleTags = showInactive ? tags : tags.filter((t) => t.active);

  return (
    <div className="space-y-5">
      <p className="flex items-center gap-2 text-sm text-muted">
        <TagIcon size={15} className="text-accent" />
        스트리머에게 팀·소속 태그를 지정합니다. 지정한 태그는 랭킹 목록과 스트리머
        상세 페이지의 이름 옆에 표시됩니다.
      </p>

      {/* ── 태그 만들기 / 수정 ── */}
      <section className="space-y-2">
        <h3 className="text-sm font-bold text-fg">
          {editing ? `태그 수정 — ${editing.name}` : "태그 만들기"}
        </h3>
        <TagForm key={editing?.id ?? "new"} editing={editing}
                 onDone={() => { void load(); setEditing(null); }}
                 onCancel={editing ? () => setEditing(null) : undefined} />
      </section>

      {/* ── 태그 목록 ── */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-bold text-fg">
            태그 목록 <span className="font-normal text-muted">{visibleTags.length}</span>
          </h3>
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input type="checkbox" checked={showInactive}
                   onChange={(e) => setShowInactive(e.target.checked)} />
            비활성 포함
          </label>
        </div>

        {loading ? (
          <p className="text-sm text-muted">불러오는 중…</p>
        ) : loadErr ? (
          <p role="alert" className="text-sm text-red-400">{loadErr}</p>
        ) : visibleTags.length === 0 ? (
          <p className="text-sm text-muted">아직 만든 태그가 없습니다.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {visibleTags.map((t) => (
              <li key={t.id}
                  className="flex flex-wrap items-center gap-2 rounded-xl border
                             border-border bg-bg-card/60 px-3 py-2">
                <StreamerTagBadge tag={t} />
                {/* 상태를 색만으로 말하지 않는다 — 글자로도 적는다 */}
                {!t.active && (
                  <span className="rounded border border-border px-1.5 py-0.5 text-[11px]
                                   font-bold text-muted">비활성</span>
                )}
                <span className="text-xs text-muted">지정 {t.assignedCount}명</span>
                <span className="ml-auto flex items-center gap-1.5">
                  <button onClick={() => setEditing(t)} className="btn-secondary text-xs">
                    수정
                  </button>
                  <button
                    onClick={() => void api.admin
                      .streamerTagUpdate(t.id, { active: !t.active })
                      .then(load)}
                    className="btn-secondary text-xs"
                    title={t.active
                      ? "비활성으로 내리면 공개 화면에서 즉시 사라집니다. 지정 이력은 남습니다."
                      : "다시 활성화하면 기존 지정이 그대로 되살아납니다."}>
                    {t.active ? "비활성화" : "활성화"}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-[11px] leading-relaxed text-muted/80">
          태그는 삭제하지 않고 <b>비활성화</b>합니다. 지정 이력이 남아 있어 되돌릴 때
          아무것도 다시 입력할 필요가 없습니다.
        </p>
      </section>

      {/* ── 스트리머 검색 · 지정 ── */}
      <section className="space-y-2">
        <h3 className="text-sm font-bold text-fg">스트리머에게 지정</h3>
        <div className="flex flex-wrap items-center gap-2">
          <input value={keyword} onChange={(e) => setKeyword(e.target.value)}
                 onKeyDown={(e) => { if (e.key === "Enter") void search(); }}
                 placeholder="스트리머 이름 또는 채널 ID (2자 이상)"
                 className="min-w-0 flex-1 rounded-lg border border-border bg-bg
                            px-2.5 py-1.5 text-sm"
                 style={{ minWidth: 200 }} />
          <button onClick={() => void search()} disabled={searching}
                  className="btn-primary inline-flex items-center gap-1.5 text-sm disabled:opacity-50">
            <Search size={14} /> {searching ? "검색 중…" : "검색"}
          </button>
        </div>
        {searchErr && <p role="alert" className="text-sm text-muted">{searchErr}</p>}
        {results.length > 0 && (
          <div className="flex flex-col gap-2">
            {results.map((r) => (
              <StreamerRow key={r.channelId} item={r} tags={tags}
                           maxPerStreamer={maxPerStreamer} onChanged={onRowChanged} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
