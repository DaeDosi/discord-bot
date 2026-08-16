"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Plus, Tag as TagIcon, Users, X, ArrowUp, ArrowDown, Check } from "lucide-react";
import Switch from "@/components/Switch";
import GroupMembersDrawer from "./GroupMembersDrawer";
import { api } from "@/lib/api";
import { StreamerTagBadge } from "@/components/StreamerTag";
import type {
  StreamerTag, StreamerTagAdmin, TagGradientDirection,
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

/** 미리보기용 임시 그룹. 저장 전이라 id가 없으므로 0을 쓴다. */
function draftTag(d: {
  name: string; colorMode: "solid" | "gradient";
  colorStart: string; colorEnd: string; gradientDirection: TagGradientDirection;
}): StreamerTag {
  return {
    id: 0, name: d.name || "그룹 이름", slug: "", kind: "team",
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

/** 그룹 만들기 / 고치기 폼. `editing`이 있으면 수정 모드다. */
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
  // 전체 스트리머 랭킹 제외 — 그룹 **이름**이 아니라 이 속성으로 판정한다.
  // 이름을 바꿔도 정책이 유지되는 것이 핵심이다(요구 6).
  const [excludeFromRanking, setExcludeFromRanking] = useState(
    editing?.excludeFromRanking ?? false);
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
        excludeFromRanking,
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
          <span className="text-xs font-semibold text-muted">그룹 이름</span>
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

      {/* 전체 스트리머 랭킹 제외 — `Switch`는 `<label>`로 감싸야 클릭이 먹는다
          (루트 CLAUDE.md 명시). 적용 범위를 문구로 정확히 적어 둔다. */}
      <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border/60 bg-bg p-3">
        <Switch checked={excludeFromRanking} onChange={setExcludeFromRanking} />
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-fg">전체 스트리머 랭킹에서 제외</span>
          <span className="mt-0.5 block text-xs leading-relaxed text-muted">
            이 그룹에 속한 스트리머를 <b className="text-fg">전체 스트리머 랭킹</b>에만 노출하지 않습니다.
            검색·스트리머 상세·신규 스트리머 랭킹·싱드컵·통계 수집에는 영향이 없습니다.
            그룹을 비활성화하거나 멤버에서 빼면 즉시 다시 노출됩니다.
          </span>
        </span>
      </label>

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
          {busy ? "저장 중…" : editing ? "수정 저장" : "그룹 만들기"}
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


export default function StreamerTagsPanel() {
  const [tags, setTags] = useState<StreamerTagAdmin[]>([]);
  const [maxPerStreamer, setMax] = useState(5);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [editing, setEditing] = useState<StreamerTagAdmin | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  /** 멤버 관리 drawer를 연 그룹. null이면 닫혀 있다. */
  const [membersOf, setMembersOf] = useState<StreamerTagAdmin | null>(null);


  const load = useCallback(async () => {
    setLoading(true); setLoadErr(null);
    try {
      const res = await api.admin.streamerTagsList(true);
      setTags(res.tags);
      setMax(res.maxPerStreamer);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "소속 그룹 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  /** 지정/해제/순서 변경 후 서버가 준 최종 소속 그룹 목록으로 그 행만 갈아 끼운다.
   *  전체를 다시 검색하지 않는다 — 목록이 흔들려 방금 만진 행을 놓친다. */
  const visibleTags = showInactive ? tags : tags.filter((t) => t.active);

  return (
    <div className="space-y-5">
      <p className="flex items-center gap-2 text-sm text-muted">
        <TagIcon size={15} className="text-accent" />
        스트리머에게 소속 그룹을 지정합니다. 지정한 그룹은 랭킹 목록과 스트리머
        상세 페이지의 이름 옆에 표시됩니다.
      </p>

      {/* ── 그룹 만들기 / 수정 ── */}
      <section className="space-y-2">
        <h3 className="text-sm font-bold text-fg">
          {editing ? `그룹 수정 — ${editing.name}` : "그룹 만들기"}
        </h3>
        <TagForm key={editing?.id ?? "new"} editing={editing}
                 onDone={() => { void load(); setEditing(null); }}
                 onCancel={editing ? () => setEditing(null) : undefined} />
      </section>

      {/* ── 소속 그룹 목록 ── */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-bold text-fg">
            소속 그룹 목록 <span className="font-normal text-muted">{visibleTags.length}</span>
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
          <p className="text-sm text-muted">아직 만든 소속 그룹이 없습니다.</p>
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
                <span className="text-xs text-muted">멤버 {t.assignedCount}명</span>
                <span className="ml-auto flex items-center gap-1.5">
                  {/* 이 그룹에 누가 들어 있는지 보고 고치는 유일한 경로다.
                      TAG-1에는 '스트리머 → 그룹' 방향만 있어 확인할 방법이 없었다. */}
                  <button onClick={() => setMembersOf(t)}
                          className="btn-secondary inline-flex items-center gap-1 text-xs">
                    <Users size={12} /> 멤버 관리
                  </button>
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
          그룹은 삭제하지 않고 <b>비활성화</b>합니다. 지정 이력이 남아 있어 되돌릴 때
          아무것도 다시 입력할 필요가 없습니다.
        </p>
      </section>

      {membersOf && (
        <GroupMembersDrawer group={membersOf}
                            onClose={() => setMembersOf(null)}
                            onChanged={() => void load()} />
      )}

      {/* ── '스트리머에게 그룹 지정' 섹션은 제거했다 (UI-R 요구 7) ─────────────
          같은 일을 하는 경로가 둘이었다 — 여기(스트리머 → 그룹)와 위의 '멤버 관리'
          (그룹 → 스트리머). 두 화면이 같은 데이터를 다르게 보여 주면 어느 쪽이
          최신인지 알 수 없고, 멤버 수(`assignedCount`)도 한쪽에서만 갱신됐다.
          그룹 목록에서 바로 들어가는 **'멤버 관리' 하나로 통일**한다.

          **API는 그대로 둔다** — `streamer-tags/search` · `assign` · `unassign` ·
          `reorder`는 `GroupMembersDrawer`가 계속 쓴다. 소비자가 남아 있으므로
          지우지 않는다. */}
    </div>
  );
}
