"use client";
import { useCallback, useMemo, useState } from "react";
import { Award, ExternalLink, Eye, Heart, Info, Radio, Search, Users, X } from "lucide-react";

import {
  SINGCUP_QUALIFIERS,
  allSoloQualifiers,
  qualifierChannelUrl,
  type QualifierCategory,
  type QualifierGroup,
} from "@/lib/singcupQualifiers";
import { useSingcupMain } from "@/lib/useSingcupMain";
import type { SingcupStreamer } from "@/lib/types";
import { GOLD, GREEN, hideBrokenImage, nf } from "./singcupShared";

// 싱드컵 **공식 예선 참가자** 화면 — 이 탭의 기본 화면이다.
//
// 여기 실리는 명단은 치지직 공식 공지가 정본이고(lib/singcupQualifiers), NexBot이
// 계산한 인기점수와는 아무 관계가 없다. 그래서 이 화면에는 **순위 번호를 붙이지
// 않는다** — 공식 발표에 없는 서열을 우리가 만들어 붙이는 순간, 보는 사람은 그것을
// 대회 결과로 읽는다. 카드에 적는 하트·조회수는 "이 사람의 #싱드컵 클립 지표"라는
// 사실 진술이지 심사 결과가 아니다.
//
// NexBot 점수 랭킹은 같은 탭의 `?view=ranking`(Singcup.tsx)에 그대로 남아 있다.
//
// 데이터는 `useSingcupMain`(공유 캐시·5분 폴링)을 그대로 쓴다. 이 화면 때문에
// 새 fetch 루프를 만들면 안 된다 — `/api/singcup/main`은 참가자 전원이 실려 응답이
// 크고, 호출 횟수가 곧 전송 비용이다(자세한 정책은 루트 CLAUDE.md).
//
// **조인은 channel_id로만 한다.** 발표 이름과 현재 채널명은 다를 수 있고(개명),
// 이름으로 맞추면 동명이인이 서로의 지표를 가져간다. 매칭되지 않은 참가자는 지어내지
// 않고 "통계 준비 중"으로 둔다 — `/main`은 `#싱드컵` 태그 클립을 올린 사람만 담기
// 때문에, 클립을 올리지 않은 참가자가 여기 없는 것은 정상이다(실측 2026-08-15:
// 솔로 128명은 128/128 매칭, 그룹 멤버는 73링크 중 53).

// 골드는 **배경으로만** 쓰고 글자는 이 어두운 색을 얹는다. 골드(#FACC15)를 글자
// 색으로 쓰면 라이트 테마의 흰 배경에서 대비가 1.4:1까지 떨어져 사실상 읽히지
// 않는다(실측). 기존 `EventBadge`가 같은 이유로 같은 조합을 쓴다.
const ON_GOLD = "#1a1400";

type Filter = "all" | QualifierCategory | "groups";

const FILTERS: { k: Filter; label: string }[] = [
  { k: "all", label: "전체" },
  { k: "female_solo", label: "여성 솔로" },
  { k: "male_solo", label: "남성 솔로" },
  { k: "groups", label: "그룹" },
];

/** 검색어 정규화 — 공백을 모두 지우고 소문자로. 발표 이름에는 "공 운", "시 키 Siki"
 *  처럼 글자 사이 공백이 흔해서, 공백을 남겨두면 "공운"으로는 찾을 수 없다. */
const norm = (s: string) => s.replace(/\s+/g, "").toLowerCase();

/** 참가자 한 명의 현재 지표. `/main`에 없으면 null이고 화면은 "통계 준비 중"이 된다. */
type Stat = SingcupStreamer | null;

function LiveDot() {
  // 색은 globals.css의 `.nb-live-badge`가 테마별로 정한다 — 라이트에서 네온 초록 글자가
  // 같은 색 12% 배경 위에 얹혀 사실상 보이지 않았다(실측 1.0:1). 아이콘과 'LIVE' 글자를
  // 함께 두는 것은 색만으로 상태를 전달하지 않기 위해서다.
  return (
    <span className="nb-live-badge inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-bold">
      <Radio size={10} /> LIVE
    </span>
  );
}

/** 지표 줄 — 매칭된 참가자에게만 붙는다. 순위·점수는 싣지 않는다(파일 상단 주석). */
function StatLine({ s }: { s: SingcupStreamer }) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted">
      {/* 하트 아이콘 색은 `.nb-heart-icon`이 테마별로 정한다 — 골드 #FACC15는 흰 카드
          위에서 1.37:1까지 떨어져 흐릿했다(실측). 숫자는 text-muted 그대로 둔다(5.3:1↑). */}
      <span className="inline-flex items-center gap-1" title="대표 클립의 하트 수">
        <Heart size={12} className="nb-heart-icon" /> <span className="tabular-nums">{nf(s.heartCount)}</span>
      </span>
      <span className="inline-flex items-center gap-1" title="대표 클립의 조회수">
        <Eye size={12} /> <span className="tabular-nums">{nf(s.viewCount)}</span>
      </span>
      <span title="#싱드컵 태그가 확인된 클립 수">
        클립 <span className="tabular-nums">{nf(s.taggedClipCount)}</span>
      </span>
      {s.live && <LiveDot />}
    </div>
  );
}

/** 아직 `/main`에 없는 참가자. 공식 선정 사실은 그대로 두고 현재 상태만 적는다. */
function PendingLine() {
  return (
    <p className="mt-1 text-[12px] text-muted/70">
      통계 준비 중 — <span className="text-muted">#싱드컵</span> 태그 클립이 아직 집계되지 않았습니다.
    </p>
  );
}

/** 아직 지표를 받는 중. **"집계되지 않았다"고 말하지 않는다** — 첫 응답이 오기 전까지는
 *  매칭 여부를 알 수 없는데, 그 사이 PendingLine을 보여주면 201명 전원이 "집계 안 됨"으로
 *  단정된다(실측: 응답 4초 지연 시 201건, 도착 후 20건). `/main`은 참가자 전원이 실려
 *  운영에서도 1.4초가 걸리므로 이 구간은 실제로 눈에 보인다. */
function LoadingLine() {
  return <p className="mt-1 text-[12px] text-muted/60">통계 불러오는 중…</p>;
}

/** 참가자 한 명. 솔로 카드와 그룹 카드의 멤버 줄이 같은 컴포넌트를 쓴다. */
function Person({ announcedName, channelId, stat, loading, compact = false }: {
  announcedName: string; channelId: string; stat: Stat; loading: boolean; compact?: boolean;
}) {
  // 발표 이후 채널명이 바뀐 경우 둘 다 보여준다 — 발표 이름만 두면 지금 그 채널을
  // 찾을 수 없고, 현재 이름만 두면 공지와 대조가 안 된다.
  const renamed = !!stat && norm(stat.channelName) !== norm(announcedName);
  return (
    <div className="flex min-w-0 items-start gap-2.5">
      <a href={qualifierChannelUrl(channelId)} target="_blank" rel="noopener noreferrer"
         className={`shrink-0 overflow-hidden rounded-full bg-bg-hover ${compact ? "h-8 w-8" : "h-10 w-10"}`}
         aria-hidden="true" tabIndex={-1}>
        {stat?.channelImageUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={stat.channelImageUrl} alt="" loading="lazy" onError={hideBrokenImage}
               className="h-full w-full object-cover" />
        )}
      </a>
      <div className="min-w-0 flex-1">
        <a href={qualifierChannelUrl(channelId)} target="_blank" rel="noopener noreferrer"
           className="inline-flex items-center gap-1 font-bold hover:underline">
          <span className="break-all">{announcedName}</span>
          <ExternalLink size={12} className="shrink-0 opacity-60" />
        </a>
        {renamed && (
          <p className="text-[12px] text-muted">
            현재 채널명 <span className="text-fg">{stat!.channelName}</span>
          </p>
        )}
        {stat ? <StatLine s={stat} /> : loading ? <LoadingLine /> : <PendingLine />}
      </div>
    </div>
  );
}

function GroupCard({ g, statOf, loading }: {
  g: QualifierGroup; statOf: (id: string) => Stat; loading: boolean;
}) {
  return (
    <div className="rounded-xl border border-border bg-bg-card p-3.5 transition-colors
                    hover:border-muted/40 focus-within:border-muted/40">
      <div className="mb-2.5 flex items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12px] font-extrabold"
              style={{ background: GOLD, color: ON_GOLD }}>
          <Users size={12} /> 예선 그룹 {g.teamNumber}팀
        </span>
        <span className="text-[12px] text-muted">{g.members.length}명</span>
      </div>
      <div className="space-y-3">
        {g.members.map((m) => (
          <Person key={m.channelId} announcedName={m.announcedName}
                  channelId={m.channelId} stat={statOf(m.channelId)} loading={loading} compact />
        ))}
      </div>
    </div>
  );
}

export default function SingcupQualifiers({ onRanking }: { onRanking: () => void }) {
  // 랭킹 화면과 같은 공유 캐시를 구독한다(새 폴링 루프를 만들지 않는다).
  const { data, loading } = useSingcupMain();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");

  const q = norm(query);

  // channel_id → 현재 지표. 이름은 쓰지 않는다.
  const statOf = useMemo(() => {
    const m = new Map<string, SingcupStreamer>();
    for (const s of data?.streamers ?? []) m.set(s.channelId, s);
    return (id: string): Stat => m.get(id) ?? null;
  }, [data]);

  const solos = useMemo(() => allSoloQualifiers(), []);

  // 검색 대상은 공식 발표 이름과 **현재 치지직 이름** 둘 다. 개명한 참가자를 어느
  // 이름으로 찾아도 나와야 한다. useCallback으로 묶어야 아래 useMemo의 의존성이
  // 정직해진다(매 렌더 새 함수면 목록을 매번 다시 만든다).
  const hit = useCallback((announcedName: string, id: string) => {
    if (!q) return true;
    if (norm(announcedName).includes(q)) return true;
    const st = statOf(id);
    return !!st && norm(st.channelName).includes(q);
  }, [q, statOf]);

  const shownSolos = useMemo(() => {
    const base = filter === "female_solo" || filter === "male_solo"
      ? solos.filter((s) => s.category === filter)
      : filter === "groups" ? [] : solos;
    return base
      .filter((s) => hit(s.announcedName, s.channelId))
      .sort((a, b) =>
        a.category === b.category
          ? a.officialOrder - b.officialOrder
          : a.category === "female_solo" ? -1 : 1);
  }, [solos, filter, hit]);

  const shownGroups = useMemo(() => {
    if (filter === "female_solo" || filter === "male_solo") return [];
    // 팀 안에 검색어와 맞는 멤버가 하나라도 있으면 팀을 통째로 보여준다 — 팀에서 한
    // 명만 잘라내 보여주면 그 사람이 솔로 참가자처럼 읽힌다.
    return SINGCUP_QUALIFIERS.groups.filter((g) =>
      g.members.some((m) => hit(m.announcedName, m.channelId)));
  }, [filter, hit]);

  const totalShown = shownSolos.length + shownGroups.length;
  const { counts, sourceUrl, sourceTitle } = SINGCUP_QUALIFIERS;

  return (
    <div className="space-y-5">
      {/* Hero */}
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="flex items-center gap-2 text-xl font-extrabold tracking-tight md:text-2xl">
            <Award size={20} style={{ color: GOLD }} /> 싱드컵 갤럭시 시즌 공식 예선 참가자
          </h2>
          <span className="inline-flex shrink-0 items-center rounded-md px-2 py-1 text-xs font-extrabold leading-none"
                style={{ background: GOLD, color: ON_GOLD }}>
            공식 발표
          </span>
        </div>
        {/* ── 설명 2단 ────────────────────────────────────────────────────────
            첫 문장은 **무엇인가**(공식 명단), 둘째 문장은 **오해 방지 안내**다.
            성격이 다르므로 한 문단에 이어 붙이지 않는다. 들여쓰기를 공백 문자로
            만들지 않고 별도 블록 + 왼쪽 보더로 구조를 준다 — 공백은 화면 폭이
            바뀌면 정렬이 무너지고 스크린리더에도 의미가 전달되지 않는다. */}
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-fg">
          치지직이 공식 공지로 발표한 예선 참가자 명단입니다.
        </p>
        <p className="mt-2 flex max-w-3xl items-start gap-2 border-l-2 border-border pl-3
                      text-[13px] leading-relaxed text-muted">
          <Info size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            아래 하트·조회수는 참가자의 <b className="text-fg">#싱드컵</b> 클립 지표일 뿐이며,{" "}
            <b className="text-fg">공식 심사 결과나 순위가 아닙니다.</b>
          </span>
        </p>
      </div>

      {/* ── 액션 그룹 ────────────────────────────────────────────────────────
          '공식 공지 원문'과 '비공식 인기점수 랭킹'은 성격이 정반대다(치지직 공식 ↔
          NexBot 자체 계산). 같은 줄에 두되 **무게를 다르게** 준다 —
          공식 링크는 본문 강조, 비공식은 보조 버튼 + '비공식' 문구를 앞세운다.
          좁은 화면에서는 flex-wrap으로 자연스럽게 다음 줄로 내려간다. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 nb-tap-gap">
        {/* 링크 글자는 본문 색으로 두고 아이콘만 골드로 둔다 — 골드 글자는 라이트
            테마에서 읽히지 않는다(위 ON_GOLD 주석). */}
        <a href={sourceUrl} target="_blank" rel="noopener noreferrer"
           className="nb-tap inline-flex items-center gap-1.5 text-sm font-bold text-fg underline underline-offset-2 hover:opacity-80">
          <ExternalLink size={14} style={{ color: GOLD }} aria-hidden="true" /> 공식 공지 원문 보기
          <span className="sr-only">({sourceTitle}) 새 창에서 열림</span>
        </a>
        <button type="button" onClick={onRanking}
                className="btn-secondary nb-tap ml-auto text-sm"
                title="NexBot이 공개 조회수와 하트로 계산한 랭킹입니다. 공식 심사 결과와는 무관합니다.">
          비공식 인기점수 랭킹 보기
        </button>
      </div>

      {/* 인원 요약 — 그룹은 팀 단위로 센다. 73은 링크 항목 수라서 인원 합계로 쓰지 않는다. */}
      <div className="grid grid-cols-3 gap-2.5">
        {[
          { label: "여성 솔로", value: counts.femaleSolo, unit: "명" },
          { label: "남성 솔로", value: counts.maleSolo, unit: "명" },
          { label: "그룹", value: counts.groups, unit: "팀" },
        ].map((t) => (
          <div key={t.label} className="rounded-xl border border-border bg-bg-card p-3 text-center">
            <p className="text-[12px] text-muted">{t.label}</p>
            <p className="mt-0.5 text-xl font-extrabold tabular-nums md:text-2xl">
              {t.value}<span className="ml-0.5 text-sm font-bold text-muted">{t.unit}</span>
            </p>
          </div>
        ))}
      </div>

      {/* 필터 + 검색 */}
      <div className="flex flex-wrap items-center gap-2">
        {/* nb-tap-gap: 터치 입력에서만 탭 사이를 벌린다(gap 1.5 = 7px는 손가락에 너무 좁다) */}
        <div role="tablist" aria-label="참가 부문" className="nb-tap-gap flex flex-wrap gap-1.5">
          {FILTERS.map((f) => {
            const on = filter === f.k;
            return (
              <button key={f.k} role="tab" aria-selected={on} type="button"
                      onClick={() => setFilter(f.k)}
                      className={`nb-tap inline-flex items-center rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                        on ? "border-transparent font-extrabold" : "border-border text-muted hover:text-fg"}`}
                      style={on ? { background: GOLD, color: ON_GOLD } : undefined}>
                {/* 선택 상태를 색상만으로 나타내지 않는다 — 체크 표시와 굵기로도 구분된다 */}
                {on && <span aria-hidden="true">✓ </span>}{f.label}
              </button>
            );
          })}
        </div>

        <div className="relative ml-auto min-w-0 flex-1 sm:max-w-xs">
          <Search size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <input value={query} onChange={(e) => setQuery(e.target.value)}
                 placeholder="참가자 이름 검색"
                 aria-label="참가자 이름으로 검색 (공식 발표 이름 또는 현재 채널명)"
                 className="w-full rounded-lg border border-border bg-bg py-1.5 pl-8 pr-8 text-sm outline-none focus:border-muted" />
          {query && (
            <button type="button" onClick={() => setQuery("")} aria-label="검색어 지우기"
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-fg">
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* 지표를 못 받은 상태를 한 번만 알린다. 공식 명단 자체는 정적이라 그대로 남지만,
          말없이 전원이 "통계 준비 중"이면 대회 데이터가 사라진 것처럼 읽힌다. 카드마다
          같은 말을 반복하지 않고 여기서 한 줄로 설명한다. */}
      {!loading && !data && (
        <p className="rounded-xl border border-border px-4 py-3 text-sm leading-relaxed text-muted">
          지금은 <b className="text-fg">클립 지표를 불러오지 못했습니다.</b> 아래 공식 예선 참가자
          명단은 치지직 공식 발표 그대로이며 영향받지 않습니다. 잠시 후 새로고침하면 지표가 함께 표시됩니다.
        </p>
      )}

      {query && (
        <p className="text-sm text-muted">
          검색 결과 <b className="text-fg tabular-nums">{totalShown}</b>건
          {shownGroups.length > 0 && <span className="text-muted/70"> (그룹 {shownGroups.length}팀 포함)</span>}
        </p>
      )}

      {totalShown === 0 ? (
        <div className="card px-5 py-12 text-center">
          <p className="font-medium text-fg">검색 결과가 없습니다.</p>
          <p className="mt-1.5 text-sm leading-relaxed text-muted">
            공식 발표 이름과 현재 채널명 모두에서 찾지 못했습니다. 공지 원문의 표기를 확인해 보세요.
          </p>
          <button type="button" onClick={() => { setQuery(""); setFilter("all"); }}
                  className="btn-secondary mt-4 text-sm">전체 명단 보기</button>
        </div>
      ) : (
        <div className="space-y-5">
          {shownSolos.length > 0 && (
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
              {shownSolos.map((s) => (
                <div key={s.channelId}
                     className="rounded-xl border border-border bg-bg-card p-3.5 transition-colors
                                hover:border-muted/40 focus-within:border-muted/40">
                  <Person announcedName={s.announcedName} channelId={s.channelId}
                          stat={statOf(s.channelId)} loading={loading} />
                </div>
              ))}
            </div>
          )}

          {shownGroups.length > 0 && (
            <div>
              {shownSolos.length > 0 && (
                <h3 className="mb-2.5 text-sm font-extrabold text-muted">예선 그룹 엔트리</h3>
              )}
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
                {shownGroups.map((g) => (
                  <GroupCard key={g.groupEntryId} g={g} statOf={statOf} loading={loading} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}

    </div>
  );
}
