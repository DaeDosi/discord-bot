"use client";
import { useCallback, useEffect, useState } from "react";
import {
  Plus, Tag as TagIcon, Users, ArrowUp, ArrowDown, Check, Trash2,
} from "lucide-react";
import Switch from "@/components/Switch";
import GroupMembersDrawer from "./GroupMembersDrawer";
import { api } from "@/lib/api";
import { StreamerTagBadge, resolveStops } from "@/components/StreamerTag";
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

/** 색상 지점 상·하한의 **폴백**이다. 실제 값은 서버 응답(`maxColorStops`)을 쓴다 —
 *  서버가 유일한 권위이고, 여기 상수가 그보다 크면 저장 단계에서야 거절된다. */
const FALLBACK_MAX_STOPS = 8;

/** 편집 중인 색상 지점. `pos`를 문자열로 들고 있는 이유: 숫자로 두면 입력 중
 *  빈 칸(`""`)이 즉시 0으로 튀어 커서가 밀린다. 제출할 때만 숫자로 바꾼다. */
type DraftStop = { key: number; color: string; pos: string };

let stopKeySeq = 1;
const mkStop = (color: string, pos: number): DraftStop =>
  ({ key: stopKeySeq++, color, pos: String(pos) });

/** 저장된 그룹 → 편집용 지점 배열. 구형(단일·2색) 데이터도 여기서 흡수된다. */
function stopsFromTag(t?: StreamerTagAdmin | null): DraftStop[] {
  const raw = t ? resolveStops(t) : null;
  if (raw && raw.length) return raw.map((s) => mkStop(s.color, s.pos));
  return [mkStop("#38bdf8", 0), mkStop("#c084fc", 100)];
}

const posNum = (s: DraftStop) => {
  const n = Number(s.pos);
  return Number.isFinite(n) ? Math.min(100, Math.max(0, Math.round(n))) : 0;
};
const stopValid = (s: DraftStop) =>
  HEX.test(s.color) && s.pos.trim() !== "" && Number.isFinite(Number(s.pos))
  && Number(s.pos) >= 0 && Number(s.pos) <= 100;

/** 미리보기용 임시 그룹. 저장 전이라 id가 없으므로 0을 쓴다. */
function draftTag(name: string, stops: DraftStop[],
                  dir: TagGradientDirection): StreamerTag {
  const usable = stops.filter((s) => HEX.test(s.color));
  const list = (usable.length ? usable : [mkStop("#38bdf8", 0)])
    .map((s) => ({ color: s.color.toLowerCase(), pos: posNum(s) }))
    .sort((a, b) => a.pos - b.pos);
  return {
    id: 0, name: name || "그룹 이름", slug: "", kind: "team",
    // 구형 필드도 채워 둔다 — 배지는 `colorStops`를 보지만, 이 객체를 받는
    // 다른 코드가 구형 필드를 읽을 수 있다.
    colorMode: list.length > 1 ? "gradient" : "solid",
    colorStart: list[0].color,
    colorEnd: list.length > 1 ? list[list.length - 1].color : null,
    gradientDirection: dir,
    colorStops: list,
  };
}

/** 색상 지점 한 줄 — 색 피커 + hex + 위치(%) + 순서/삭제.
 *
 *  피커만 두면 정확한 브랜드 색을 넣을 수 없고, 텍스트만 두면 고르기 불편해
 *  **둘 다** 둔다(기존 `ColorField`의 판단을 그대로 유지했다).
 *  순서 변경을 드래그가 아니라 버튼으로 두는 이유: 드래그는 키보드·스크린리더
 *  사용자에게 조작 경로가 없고, 이 패널의 다른 목록도 이미 ↑/↓ 버튼을 쓴다. */
function StopRow({ stop, index, total, onChange, onMove, onRemove, canRemove }: {
  stop: DraftStop; index: number; total: number;
  onChange: (patch: Partial<DraftStop>) => void;
  onMove: (delta: number) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  const okColor = HEX.test(stop.color);
  const okPos = stop.pos.trim() !== "" && Number.isFinite(Number(stop.pos))
    && Number(stop.pos) >= 0 && Number(stop.pos) <= 100;
  const label = `${index + 1}번째 색상`;
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60
                   bg-bg px-2 py-2">
      <span className="w-5 shrink-0 text-center text-xs font-bold tabular-nums text-muted"
            aria-hidden="true">{index + 1}</span>

      <input type="color" value={okColor ? stop.color : "#38bdf8"}
             onChange={(e) => onChange({ color: e.target.value })}
             className="h-9 w-11 shrink-0 cursor-pointer rounded border border-border
                        bg-transparent"
             aria-label={`${label} 선택`} />

      <input type="text" value={stop.color} spellCheck={false} maxLength={7}
             onChange={(e) => onChange({ color: e.target.value })}
             placeholder="#38BDF8"
             aria-label={`${label} hex 코드`}
             aria-invalid={!okColor}
             className={`w-[7.5rem] min-w-0 flex-1 rounded-lg border bg-bg-card px-2 py-1.5
                         font-mono text-sm ${okColor ? "border-border" : "border-red-500/60"}`} />

      <span className="flex shrink-0 items-center gap-1">
        <input type="number" value={stop.pos} min={0} max={100} step={1}
               onChange={(e) => onChange({ pos: e.target.value })}
               aria-label={`${label} 위치(퍼센트)`}
               aria-invalid={!okPos}
               className={`w-16 rounded-lg border bg-bg-card px-2 py-1.5 text-right
                           text-sm tabular-nums
                           ${okPos ? "border-border" : "border-red-500/60"}`} />
        <span className="text-xs text-muted" aria-hidden="true">%</span>
      </span>

      <span className="nb-tap-gap ml-auto flex shrink-0 items-center gap-1">
        <button type="button" onClick={() => onMove(-1)} disabled={index === 0}
                className="btn-secondary nb-tap-icon inline-flex h-9 w-9 items-center justify-center p-0
                           disabled:opacity-40" title="위로"
                aria-label={`${label}을 위로 이동`}>
          <ArrowUp size={14} />
        </button>
        <button type="button" onClick={() => onMove(1)} disabled={index === total - 1}
                className="btn-secondary nb-tap-icon inline-flex h-9 w-9 items-center justify-center p-0
                           disabled:opacity-40" title="아래로"
                aria-label={`${label}을 아래로 이동`}>
          <ArrowDown size={14} />
        </button>
        <button type="button" onClick={onRemove} disabled={!canRemove}
                className="btn-secondary nb-tap-icon inline-flex h-9 w-9 items-center justify-center p-0
                           disabled:opacity-40" title={canRemove
                  ? "이 색상 지점 삭제" : "색상은 최소 1개가 필요합니다"}
                aria-label={`${label} 삭제`}>
          <Trash2 size={14} />
        </button>
      </span>

      {!okColor && (
        <span className="w-full text-[11px] text-red-400">
          #RRGGBB 형식으로 입력해 주세요.
        </span>
      )}
      {okColor && !okPos && (
        <span className="w-full text-[11px] text-red-400">
          위치는 0~100 사이 숫자여야 합니다.
        </span>
      )}
    </li>
  );
}

/** 그룹 만들기 / 고치기 폼. `editing`이 있으면 수정 모드다. */
function TagForm({ editing, maxStops, onDone, onCancel }: {
  editing?: StreamerTagAdmin | null;
  maxStops: number;
  onDone: () => void;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(editing?.name ?? "");
  // 색상 방식(단일/그라데이션) 선택은 없앴다 — **지점 개수가 곧 방식**이다
  // (1개면 단일, 2개 이상이면 그라데이션). 서버 계약도 같아서, 별도 셀렉트를
  // 두면 "그라데이션인데 색이 1개" 같은 모순 상태를 사용자가 만들 수 있었다.
  const [stops, setStops] = useState<DraftStop[]>(() => stopsFromTag(editing));
  const [dir, setDir] = useState<TagGradientDirection>(
    editing?.gradientDirection ?? "to-right");
  // 전체 스트리머 랭킹 제외 — 그룹 **이름**이 아니라 이 속성으로 판정한다.
  // 이름을 바꿔도 정책이 유지되는 것이 핵심이다(요구 6).
  const [excludeFromRanking, setExcludeFromRanking] = useState(
    editing?.excludeFromRanking ?? false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const preview = draftTag(name, stops, dir);
  const valid = name.trim().length > 0 && stops.length > 0 && stops.every(stopValid);

  const patchStop = (key: number, patch: Partial<DraftStop>) =>
    setStops((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

  const moveStop = (index: number, delta: number) =>
    setStops((prev) => {
      const to = index + delta;
      if (to < 0 || to >= prev.length) return prev;
      const next = prev.slice();
      [next[index], next[to]] = [next[to], next[index]];
      // 순서를 바꾸면 **위치도 함께 따라간다.** 색만 맞바꾸면 정렬 뒤 화면이
      // 그대로라 "버튼이 안 먹는다"로 보인다(서버가 pos로 정렬하기 때문).
      const p = next[index].pos;
      next[index] = { ...next[index], pos: next[to].pos };
      next[to] = { ...next[to], pos: p };
      return next;
    });

  const removeStop = (key: number) =>
    setStops((prev) => (prev.length <= 1 ? prev : prev.filter((s) => s.key !== key)));

  const addStop = () =>
    setStops((prev) => {
      if (prev.length >= maxStops) return prev;
      // 새 지점은 **마지막 두 지점 사이**에 넣는다. 끝에 100%로 붙이면 기존
      // 마지막 색과 겹쳐 아무 변화가 없어 보인다.
      const last = prev.length ? posNum(prev[prev.length - 1]) : 100;
      const prevPos = prev.length > 1 ? posNum(prev[prev.length - 2]) : 0;
      const mid = Math.min(100, Math.max(0, Math.round((prevPos + last) / 2)));
      const next = prev.slice();
      next.splice(Math.max(0, prev.length - 1), 0, mkStop("#a78bfa", mid));
      return next;
    });

  /** 위치를 0~100에 균등 재배치한다. 지점을 여러 개 넣다 보면 값이 뭉치는데,
   *  하나씩 고치게 두면 8개짜리에서 조작이 8번이다. */
  const distribute = () =>
    setStops((prev) => prev.map((s, i) => ({
      ...s, pos: String(prev.length === 1 ? 0
        : Math.round((i * 100) / (prev.length - 1))),
    })));

  const submit = async () => {
    if (!valid || busy) return;
    setBusy(true); setMsg(null);
    try {
      const body = {
        name: name.trim(),
        // 신형 표현만 보낸다 — 서버가 구형 컬럼을 함께 갱신한다.
        // 여기서 둘 다 보내면 어느 쪽이 진짜인지가 두 곳에 생긴다.
        colorStops: stops.map((s) => ({ color: s.color.toLowerCase(), pos: posNum(s) })),
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
        {/* 방향은 색이 2개 이상일 때만 의미가 있다 — 1개면 숨긴다.
            비활성으로 남겨 두면 "왜 안 먹지"를 유발한다. */}
        {stops.length > 1 && (
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
        )}
      </div>

      {/* ── 색상 지점 ─────────────────────────────────────────────────────── */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-xs font-semibold text-muted">
            색상 <span className="tabular-nums">{stops.length}</span>
            <span className="font-normal"> / {maxStops}</span>
            {/* 상태를 숫자로도 말한다 — 버튼 비활성만으로는 이유가 안 보인다 */}
            <span className="ml-1.5 font-normal text-muted/70">
              {stops.length === 1 ? "· 단일색" : "· 그라데이션"}
            </span>
          </h4>
          <span className="flex items-center gap-1.5">
            <button type="button" onClick={distribute} disabled={stops.length < 3}
                    className="btn-secondary text-xs"
                    title="위치를 0~100%에 균등하게 재배치합니다">
              위치 균등 배분
            </button>
            <button type="button" onClick={addStop} disabled={stops.length >= maxStops}
                    className="btn-secondary inline-flex items-center gap-1 text-xs"
                    title={stops.length >= maxStops
                      ? `색상은 최대 ${maxStops}개까지 지정할 수 있습니다`
                      : "색상 지점 추가"}>
              <Plus size={12} /> 색상 추가
            </button>
          </span>
        </div>

        {/* 실제 그라데이션 띠 — 숫자만으로는 지점 간격이 감이 안 온다.
            장식이 아니라 조작의 결과를 보여 주는 계기라 `aria-hidden`이 아니다. */}
        <div className="rounded-lg border border-border/60 bg-bg p-2">
          <div className="h-6 w-full rounded"
               style={{ background: `linear-gradient(90deg, ${
                 (preview.colorStops ?? []).map((s) => `${s.color} ${s.pos}%`).join(", ")
                 || preview.colorStart})` }}
               role="img"
               aria-label={`색상 미리보기: ${(preview.colorStops ?? [])
                 .map((s) => `${s.color} ${s.pos}%`).join(", ")}`} />
        </div>

        <ul className="flex flex-col gap-2">
          {stops.map((s, i) => (
            <StopRow key={s.key} stop={s} index={i} total={stops.length}
                     canRemove={stops.length > 1}
                     onChange={(patch) => patchStop(s.key, patch)}
                     onMove={(d) => moveStop(i, d)}
                     onRemove={() => removeStop(s.key)} />
          ))}
        </ul>
        <p className="text-[11px] leading-relaxed text-muted/80">
          색상이 1개면 단일색, 2개 이상이면 그라데이션으로 저장됩니다.
          저장 시 위치(%) 오름차순으로 정렬되며, 같은 위치를 두 번 쓰면 색이 딱 끊깁니다.
        </p>
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
  const [maxStops, setMaxStops] = useState(FALLBACK_MAX_STOPS);
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
      // 상한은 **서버가 정한다.** 응답에 없으면(구버전 백엔드) 폴백을 쓴다.
      if (typeof res.maxColorStops === "number" && res.maxColorStops > 0) {
        setMaxStops(res.maxColorStops);
      }
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
        <TagForm key={editing?.id ?? "new"} editing={editing} maxStops={maxStops}
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
