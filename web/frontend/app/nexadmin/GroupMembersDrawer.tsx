"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import { StreamerTagBadge } from "@/components/StreamerTag";
import type {
  GroupMember, StreamerTag, StreamerTagAdmin, StreamerTagSearchItem,
} from "@/lib/types";

const PAGE = 30;

/** 채널 이미지 — 없으면 회색 원. 외부 도메인이라 `next/image` 대신 `img`를 쓴다
 *  (관리 화면 전용이고 최적화 대상이 아니다). */
function Avatar({ src, alt }: { src?: string; alt: string }) {
  return (
    <span className="h-7 w-7 shrink-0 overflow-hidden rounded-full bg-bg-hover">
      {src
        // eslint-disable-next-line @next/next/no-img-element
        ? <img src={src} alt="" width={28} height={28} loading="lazy"
               className="h-full w-full object-cover" />
        : <span className="sr-only">{alt}</span>}
    </span>
  );
}

/**
 * 소속 그룹 멤버 관리 — 우측 drawer.
 *
 * TAG-1에는 "스트리머 → 그룹" 방향만 있어서 "이 그룹에 누가 들어 있나"를 볼 방법이
 * 없었다. 이 화면이 그 반대 방향을 연다.
 *
 * 접근성 계약:
 * · `role="dialog"` + `aria-modal` + 제목 연결
 * · 열릴 때 첫 요소로 포커스, **닫을 때 원래 버튼으로 복귀**
 * · ESC로 닫기, 포커스가 dialog 밖으로 나가지 않게 순환
 * · 배경 스크롤 잠금 (모바일에서 뒤 화면이 같이 움직이면 조작이 어긋난다)
 */
export default function GroupMembersDrawer({ group, onClose, onChanged }: {
  group: StreamerTagAdmin;
  onClose: () => void;
  /** 멤버 수가 바뀌면 목록 화면의 "멤버 N명"을 갱신한다. */
  onChanged: () => void;
}) {
  const [members, setMembers] = useState<GroupMember[]>([]);
  /** 현재 목록(검색어가 있으면 그 결과)의 총 개수 — "더 보기" 판단용. */
  const [listTotal, setListTotal] = useState(0);
  /** **그룹 전체** 멤버 수. 헤더의 "멤버 N명"은 이 값이다.
   *
   *  예전에는 `total` 하나로 둘 다 표현했는데, 멤버 검색어가 걸린 상태에서
   *  추가하면 헤더가 *걸러진 수*로 바뀌어 "멤버 수가 안 늘었다"로 보였다.
   *  두 수는 축이 다르므로 끝까지 분리해서 들고 간다. */
  const [groupTotal, setGroupTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** 지금 요청이 나가 있는 채널 id — **행마다** 잠근다.
   *  전역 `busy` 하나로는 "어느 줄을 누른 건지"가 화면에 드러나지 않는다. */
  const [pending, setPending] = useState<Set<string>>(new Set());
  /** 중복 요청 차단용 **동기** 잠금. `busy`(state)는 다음 렌더에야 반영되므로
   *  같은 tick에 두 번 눌린 클릭을 막지 못한다 — 실제로 중복 추가가 났다. */
  const inFlight = useRef<Set<string>>(new Set());

  const [memberQ, setMemberQ] = useState("");
  const [addQ, setAddQ] = useState("");
  const [addResults, setAddResults] = useState<StreamerTagSearchItem[] | null>(null);
  const [addErr, setAddErr] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState<GroupMember | null>(null);

  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<Element | null>(null);

  // 열릴 때의 포커스 위치를 기억해 두고 닫을 때 되돌린다.
  useEffect(() => {
    openerRef.current = document.activeElement;
    closeRef.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
      (openerRef.current as HTMLElement | null)?.focus?.();
    };
  }, []);

  // ESC 닫기 + 포커스 순환
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab") return;
      const nodes = panelRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input, [href], select, textarea, [tabindex]:not([tabindex="-1"])');
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const load = useCallback(async (opts: { q?: string; append?: boolean } = {}) => {
    const offset = opts.append ? members.length : 0;
    if (!opts.append) setLoading(true);
    setErr(null);
    try {
      const res = await api.admin.streamerTagMembers(group.id, {
        q: opts.q ?? undefined, limit: PAGE, offset });
      setMembers((prev) => (opts.append ? [...prev, ...res.items] : res.items));
      setListTotal(res.total);
      // 검색어가 없을 때의 total만이 **그룹 전체 수**다. 걸러진 수로 덮어쓰면
      // 헤더가 검색할 때마다 줄어들어 멤버가 사라진 것처럼 보인다.
      if (!opts.q) setGroupTotal(res.total);
      setHasMore(res.hasMore);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "멤버 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [group.id, members.length]);

  // 그룹이 바뀔 때만 처음부터 다시 읽는다. `load`를 의존성에 넣으면 그 안의
  // `members.length`(더 보기용 offset) 때문에 매 렌더마다 재조회가 돈다.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void load(); }, [group.id]);

  const memberIds = useMemo(() => new Set(members.map((m) => m.channelId)), [members]);

  const run = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await fn();
      await load({ q: memberQ.trim().length >= 2 ? memberQ.trim() : undefined });
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "요청에 실패했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const markPending = (id: string, on: boolean) =>
    setPending((prev) => {
      const next = new Set(prev);
      if (on) next.add(id); else next.delete(id);
      return next;
    });

  /** 검색 결과의 그 줄만 **서버가 방금 준 최종 태그 목록**으로 갈아 끼운다.
   *
   *  예전에는 `r.tags`가 검색 시점 그대로 얼어 있었고, "추가됨" 판정을 현재
   *  페이지(`memberIds`, 최대 30명)로 보조했다. 그래서 멤버가 30명을 넘거나
   *  멤버 검색어가 걸려 있으면 방금 추가한 사람이 어느 쪽에도 안 잡혀
   *  버튼이 계속 "추가"로 남았다. 서버 응답을 그대로 받아 적으면 그 두 경우가
   *  모두 사라진다 — 판정의 근거가 화면 상태가 아니라 서버가 되기 때문이다. */
  const patchSearchRow = (channelId: string, tags: StreamerTag[]) =>
    setAddResults((prev) => prev && prev.map(
      (r) => (r.channelId === channelId ? { ...r, tags } : r)));

  /** 멤버 추가 — 성공했을 때만 화면 상태를 바꾼다(낙관적 반영을 쓰지 않는다).
   *
   *  이 조작은 왕복이 짧고 실패가 드물지 않다(중복·권한). 낙관적으로 "추가됨"을
   *  먼저 보여 주면 실패 시 되돌리는 순간 사용자는 자기가 뭘 잘못 눌렀다고 읽는다.
   *  대신 **그 줄만** 진행 중으로 잠가 반응이 없다는 인상을 없앤다. */
  const addMember = async (r: StreamerTagSearchItem) => {
    const id = r.channelId;
    // state가 아니라 ref로 막는다 — 같은 tick의 두 번째 클릭은 아직 리렌더 전이라
    // `pending`으로는 걸러지지 않는다(실측된 중복 추가 경로).
    if (inFlight.current.has(id)) return;
    inFlight.current.add(id);
    markPending(id, true);
    setErr(null);
    try {
      const res = await api.admin.streamerTagAssign(id, group.id);
      patchSearchRow(id, res.tags ?? []);
      // 헤더 수는 **즉시** 올린다. 아래 load()가 확정하지만, 검색어가 걸려 있으면
      // 그 응답이 그룹 전체 수를 담지 않으므로 여기서 반영해 두어야 한다.
      setGroupTotal((n) => n + 1);
      await load({ q: memberQ.trim().length >= 2 ? memberQ.trim() : undefined });
      onChanged();
    } catch (e) {
      // 실패는 실패로 보여 준다 — 성공으로 꾸미지 않는다. 화면 상태를 아직
      // 바꾸지 않았으므로 되돌릴 것도 없다(그게 낙관적 반영을 안 쓴 이유다).
      setErr(e instanceof Error ? e.message : "멤버 추가에 실패했습니다.");
    } finally {
      inFlight.current.delete(id);
      markPending(id, false);
    }
  };

  /** 멤버 제거 — 제거하면 검색 결과의 그 줄은 다시 "추가"로 돌아가야 한다. */
  const removeMember = async (m: GroupMember) => {
    const id = m.channelId;
    if (inFlight.current.has(id)) return;
    inFlight.current.add(id);
    markPending(id, true);
    setErr(null);
    try {
      const res = await api.admin.streamerTagUnassign(id, group.id);
      patchSearchRow(id, res.tags ?? []);
      setGroupTotal((n) => Math.max(0, n - 1));
      await load({ q: memberQ.trim().length >= 2 ? memberQ.trim() : undefined });
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "멤버 제거에 실패했습니다.");
    } finally {
      inFlight.current.delete(id);
      markPending(id, false);
    }
  };

  const searchMembers = () => {
    const q = memberQ.trim();
    if (q && q.length < 2) { setErr("검색어는 2자 이상이어야 합니다."); return; }
    void load({ q: q || undefined });
  };

  const searchToAdd = async () => {
    const q = addQ.trim();
    if (q.length < 2) { setAddErr("검색어는 2자 이상이어야 합니다."); setAddResults(null); return; }
    setSearching(true); setAddErr(null);
    try {
      const res = await api.admin.streamerTagSearch(q);
      setAddResults(res.streamers);
      if (res.streamers.length === 0) setAddErr("검색 결과가 없습니다.");
    } catch (e) {
      setAddErr(e instanceof Error ? e.message : "검색에 실패했습니다.");
      setAddResults(null);
    } finally {
      setSearching(false);
    }
  };

  /** 순서 변경 — **그룹 안의** 멤버 순서다(스트리머 기준 reorder와 축이 다르다).
   *  현재 페이지의 최종 순서를 통째로 보낸다: 중간 상태가 저장되지 않아
   *  새로고침해도 순서가 뒤틀리지 않는다. */
  const move = (idx: number, delta: number) => {
    const j = idx + delta;
    if (j < 0 || j >= members.length) return;
    const before = members;                 // 실패하면 여기로 되돌린다
    const next = [...members];
    [next[idx], next[j]] = [next[j], next[idx]];
    setMembers(next);                       // 낙관적 반영 — 응답 후 load가 확정한다
    void (async () => {
      if (busy) { setMembers(before); return; }
      setBusy(true); setErr(null);
      try {
        await api.admin.streamerTagMemberReorder(
          group.id, next.map((m) => m.channelId));
        await load({ q: memberQ.trim().length >= 2 ? memberQ.trim() : undefined });
        onChanged();
      } catch (e) {
        // **되돌린다.** 예전에는 실패해도 바뀐 순서가 화면에 남아, 새로고침 전까지
        // 저장되지 않은 순서를 저장된 것으로 읽었다.
        setMembers(before);
        setErr(e instanceof Error ? e.message : "순서 변경에 실패했습니다.");
      } finally {
        setBusy(false);
      }
    })();
  };

  const nf = (n: number) => n.toLocaleString("ko-KR");

  return (
    <div className="fixed inset-0 z-[100] flex justify-end"
         onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="absolute inset-0 bg-black/60" aria-hidden />
      <div ref={panelRef} role="dialog" aria-modal="true"
           aria-labelledby="group-members-title"
           className="relative flex h-full w-full flex-col overflow-hidden border-l
                      border-border bg-bg shadow-2xl sm:w-[min(34rem,100vw)]">
        {/* ── 헤더 ── */}
        <div className="flex items-start gap-2 border-b border-border px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 id="group-members-title"
                className="flex min-w-0 flex-wrap items-center gap-2 text-base font-extrabold">
              <StreamerTagBadge tag={group as StreamerTag} />
              <span className="min-w-0 truncate">{group.name}</span>
              {!group.active && (
                <span className="shrink-0 rounded border border-border px-1.5 py-0.5
                                 text-[11px] font-bold text-muted">비활성</span>
              )}
            </h2>
            <p className="mt-1 text-xs text-muted">
              멤버 <b className="text-fg tabular-nums">{nf(groupTotal)}</b>명
              {memberQ.trim() && listTotal !== groupTotal && (
                /* 검색 중임을 밝힌다 — 아래 목록 수와 헤더 수가 달라 보이는 이유다 */
                <span className="ml-1.5 text-muted/80">
                  · 검색 결과 <span className="tabular-nums">{nf(listTotal)}</span>명
                </span>
              )}
            </p>
          </div>
          <button ref={closeRef} onClick={onClose} aria-label="닫기"
                  className="btn-secondary shrink-0 !px-2 !py-1.5">
            <X size={15} />
          </button>
        </div>

        {!group.active && (
          <p role="status"
             className="border-b border-border bg-bg-hover px-4 py-2 text-xs text-muted">
            현재 공개 화면에 표시되지 않는 그룹입니다. 멤버는 계속 관리할 수 있습니다.
          </p>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 space-y-5">
          {/* ── 멤버 추가 ── */}
          <section className="space-y-2">
            <h3 className="text-sm font-bold text-fg">멤버 추가</h3>
            <div className="flex flex-wrap items-center gap-2">
              <input value={addQ} onChange={(e) => setAddQ(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter") void searchToAdd(); }}
                     placeholder="스트리머 이름 또는 채널 ID (2자 이상)"
                     aria-label="추가할 스트리머 검색"
                     className="min-w-0 flex-1 rounded-lg border border-border bg-bg
                                px-2.5 py-1.5 text-sm" style={{ minWidth: 160 }} />
              <button onClick={() => void searchToAdd()} disabled={searching}
                      className="btn-primary inline-flex shrink-0 items-center gap-1.5 text-sm
                                 disabled:opacity-50">
                <Search size={14} /> {searching ? "검색 중…" : "검색"}
              </button>
            </div>
            {addErr && <p role="alert" className="text-xs text-muted">{addErr}</p>}
            {addResults && addResults.length > 0 && (
              <ul className="flex flex-col gap-1.5">
                {addResults.map((r) => {
                  // 판정 근거는 **서버가 준 이 채널의 태그 목록**이 우선이다.
                  // `memberIds`는 현재 페이지(최대 30명)뿐이라 보조로만 쓴다 —
                  // 이것만 보면 31번째부터는 추가해도 "추가" 그대로였다.
                  const already = r.tags.some((t) => t.id === group.id)
                    || memberIds.has(r.channelId);
                  const isPending = pending.has(r.channelId);
                  return (
                    <li key={r.channelId}
                        className="flex min-w-0 items-center gap-2 rounded-lg border
                                   border-border bg-bg-card/60 px-2.5 py-1.5">
                      <Avatar src={r.channelImageUrl} alt={r.channelName ?? ""} />
                      <span className="min-w-0 flex-1 truncate text-sm">
                        {r.channelName || "(이름 미상)"}
                      </span>
                      {already ? (
                        /* 색만으로 상태를 말하지 않는다 — 글자로 적는다.
                           `aria-disabled`가 아니라 텍스트라 스크린리더도 읽는다. */
                        <span className="shrink-0 rounded border border-border px-1.5 py-0.5
                                         text-[11px] font-bold text-muted">추가됨</span>
                      ) : (
                        <button disabled={isPending}
                                aria-busy={isPending}
                                aria-label={`${r.channelName ?? r.channelId}을(를) ${group.name}에 추가`}
                                onClick={() => void addMember(r)}
                                className="btn-secondary inline-flex shrink-0 items-center gap-1
                                           text-xs disabled:opacity-40">
                          <Plus size={12} /> {isPending ? "추가 중…" : "추가"}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {/* ── 현재 멤버 ── */}
          <section className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-bold text-fg">현재 멤버</h3>
              <div className="flex min-w-0 flex-1 items-center gap-1.5 sm:max-w-[16rem]">
                <input value={memberQ} onChange={(e) => setMemberQ(e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter") searchMembers(); }}
                       placeholder="멤버 검색" aria-label="멤버 검색"
                       className="min-w-0 flex-1 rounded-lg border border-border bg-bg
                                  px-2 py-1 text-xs" />
                <button onClick={searchMembers} className="btn-secondary shrink-0 !px-2 !py-1"
                        aria-label="멤버 검색 실행">
                  <Search size={13} />
                </button>
              </div>
            </div>

            {err && <p role="alert" className="text-sm text-red-400">{err}</p>}

            {loading ? (
              <p className="py-6 text-center text-sm text-muted" aria-busy>불러오는 중…</p>
            ) : members.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted">
                {memberQ.trim() ? "조건에 맞는 멤버가 없습니다." : "아직 멤버가 없습니다."}
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {members.map((m, i) => (
                  <li key={m.channelId}
                      className="flex min-w-0 items-center gap-2 rounded-lg border
                                 border-border bg-bg-card/60 px-2.5 py-1.5">
                    <span className="w-6 shrink-0 text-center text-[11px] tabular-nums text-muted">
                      {i + 1 + 0}
                    </span>
                    <Avatar src={m.channelImageUrl} alt={m.channelName ?? ""} />
                    <span className="min-w-0 flex-1 truncate text-sm">
                      {m.channelName || "(이름 미상)"}
                      <code className="ml-1.5 rounded bg-bg-hover px-1 py-0.5 text-[10px] text-muted">
                        {m.channelId.slice(0, 8)}…
                      </code>
                    </span>
                    <span className="flex shrink-0 items-center gap-1">
                      <button onClick={() => move(i, -1)} disabled={busy || i === 0}
                              aria-label={`${m.channelName ?? m.channelId} 위로`}
                              className="btn-secondary !px-1.5 !py-1 disabled:opacity-40">
                        <ArrowUp size={13} />
                      </button>
                      <button onClick={() => move(i, 1)} disabled={busy || i === members.length - 1}
                              aria-label={`${m.channelName ?? m.channelId} 아래로`}
                              className="btn-secondary !px-1.5 !py-1 disabled:opacity-40">
                        <ArrowDown size={13} />
                      </button>
                      <button onClick={() => setConfirmRemove(m)} disabled={busy}
                              aria-label={`${m.channelName ?? m.channelId} 제거`}
                              className="btn-secondary !px-1.5 !py-1 disabled:opacity-40">
                        <X size={13} />
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {members.length > 1 && (
              <p className="text-[11px] leading-relaxed text-muted/80">
                순서는 이 그룹 안에서의 표시 순서입니다. 여러 그룹에 속한 스트리머는
                공개 화면의 배지 순서도 함께 바뀝니다.
              </p>
            )}

            {hasMore && (
              <button onClick={() => void load({ append: true,
                        q: memberQ.trim().length >= 2 ? memberQ.trim() : undefined })}
                      className="btn-secondary w-full text-sm">
                더 보기 ({nf(members.length)} / {nf(listTotal)})
              </button>
            )}
          </section>
        </div>

        {/* ── 제거 확인 ── */}
        {confirmRemove && (
          <div className="border-t border-border bg-bg-card px-4 py-3" role="alertdialog"
               aria-label="멤버 제거 확인">
            <p className="text-sm text-fg">
              <b>{confirmRemove.channelName || confirmRemove.channelId.slice(0, 8)}</b>
              을(를) 이 그룹에서 제거할까요?
            </p>
            <p className="mt-1 text-xs text-muted">
              그룹과 스트리머의 연결만 끊습니다. 그룹 자체와 다른 멤버는 그대로입니다.
            </p>
            <div className="mt-2 flex items-center gap-2">
              <button disabled={pending.has(confirmRemove.channelId)}
                      onClick={() => {
                        const t = confirmRemove; setConfirmRemove(null);
                        // 제거도 추가와 **같은 경로**를 탄다 — 검색 결과의 그 줄이
                        // 다시 "추가"로 돌아가야 하기 때문이다(`patchSearchRow`).
                        void removeMember(t);
                      }}
                      className="btn-primary text-sm disabled:opacity-50">제거</button>
              <button onClick={() => setConfirmRemove(null)}
                      className="btn-secondary text-sm">취소</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
