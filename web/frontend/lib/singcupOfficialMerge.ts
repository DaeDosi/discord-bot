/* PIKU 순위와 공식 예선 명단을 잇는 **단 하나의 경로**.
 *
 * 예전에는 이 병합이 `SingcupOfficial.tsx` 안에 인라인으로 있었고 두 가지가
 * 동시에 깨져 있었다(2026-08-19 실측).
 *
 *   · PIKU 항목에서 `rank`만 꺼내고 나머지를 버렸다 → 곡·가수가 화면에서 사라졌다.
 *     공식 행의 곡 정보는 운영자가 직접 입력하는 값이라 전원 비어 있었고,
 *     정작 값은 PIKU 응답에 64/64/32 전부 들어 있었다.
 *   · 공식 색인을 팀의 `members[0]`만으로 만들고 색인에 없는 행을 **버렸다**
 *     → 그룹 32팀 중 14팀이 화면에서 사라졌다(1·4·7·13·15·16·18·19·20·22·24·
 *     28·30·32위). 1위가 지워져 목록이 2위부터 시작했다.
 *
 * 그래서 계약을 둘로 못 박는다.
 *
 *   1. **행을 버리지 않는다.** 공식 명단에서 못 찾아도 PIKU가 준 이름으로 남긴다.
 *      순위 하나가 조용히 사라지는 것이 프로필 사진이 없는 것보다 훨씬 나쁘다.
 *   2. **PIKU 항목을 끝까지 들고 간다.** 곡·가수는 여기서만 나온다.
 *
 * 순위는 **서버 값을 그대로** 쓴다. 프런트에서 1부터 다시 매기지 않는다 —
 * 동점 규칙이 서버에 있고, 두 곳에서 계산하면 규칙이 갈라진다.
 */
import type { PikuEntry, QualifierGroupRow, QualifierRow } from "./types";

export interface MergedRow {
  /** React key이자 팀 조회 키. PIKU 대표자의 channelId다. */
  channelId: string;
  /** 서버가 준 순위. 순위 데이터가 없으면 null(공지 순서). */
  rank: number | null;
  /** 카드·목록 1줄째. */
  displayName: string;
  /** 카드·목록 2줄째. 둘 다 비면 그 줄을 그리지 않는다. */
  songTitle: string;
  artistName: string;
  /** 프로필·대표 클립·LIVE의 출처. **없을 수 있다**(행은 그래도 남는다). */
  row: QualifierRow | null;
  /** 그룹에서만 채워진다. */
  teamNumber?: number;
  /** **대표자를 제외한** 팀원 이름. 솔로는 항상 빈 배열이다.
   *
   *  PIKU 응답에는 대표자 이름 하나뿐이라 팀 전체는 공식 `qualifiers`의 `members[]`
   *  에서 가져온다. 대표자는 PIKU가 정하고(문자열 첫 이름 → 그 사람의 channel_id),
   *  나머지는 그 대표자가 속한 공식 팀에서 대표자를 빼서 만든다.
   *  **공식 배열의 첫 멤버를 대표자로 다시 뽑지 않는다** — 실측으로 그룹 1위 팀은
   *  공식 표기가 `김니디 · 슈향 · 이 선 · 조별하`로 대표자가 마지막이다. */
  memberNames: string[];
}

const isGroupRow = (r: QualifierRow | QualifierGroupRow): r is QualifierGroupRow =>
  Array.isArray((r as QualifierGroupRow).members);

const clean = (s?: string) => (s || "").trim();

/** 곡·가수 한 줄. 값이 없으면 빈 문자열이고, 호출부는 그때 줄을 그리지 않는다.
 *
 * 이름 문자열을 다시 쪼개지 않는다 — 운영 데이터는 `가수 - 곡`과 `곡 - 가수`가
 * 섞여 있어 추측이 불가능하다. 서버가 나눠 준 두 필드만 쓴다.
 */
export function songLine(row: { songTitle?: string; artistName?: string }): string {
  const song = clean(row.songTitle);
  const artist = clean(row.artistName);
  if (song && artist) return `${song} - ${artist}`;
  return song || artist || "";
}

/** channelId → 공식 행. **그룹은 모든 멤버를 담는다.**
 *
 * 대표자만 담으면, PIKU 대표자(문자열 첫 이름)와 공식 표기 첫 멤버가 다른 팀을
 * 영영 못 찾는다. 서버의 `_official_index`도 같은 이유로 전 멤버를 담는다 —
 * 두 곳의 규칙을 일부러 맞춰 두었다.
 */
export function indexByChannel(
  rows: (QualifierRow | QualifierGroupRow)[],
): Map<string, QualifierRow> {
  const m = new Map<string, QualifierRow>();
  for (const r of rows) {
    if (isGroupRow(r)) for (const mem of r.members) m.set(mem.channelId, mem);
    else m.set(r.channelId, r);
  }
  return m;
}

/** channelId → 그 사람이 속한 팀의 **전체 멤버**(그룹 전용).
 *
 * 대표자가 팀의 몇 번째로 표기되든 팀 전체를 찾을 수 있어야 한다.
 */
export function teamMembersByChannel(
  rows: (QualifierRow | QualifierGroupRow)[],
): Map<string, QualifierRow[]> {
  const m = new Map<string, QualifierRow[]>();
  for (const r of rows) {
    if (!isGroupRow(r)) continue;
    for (const mem of r.members) m.set(mem.channelId, r.members);
  }
  return m;
}

/** 멤버 보조 줄. 값이 없으면 빈 문자열이고, 호출부는 그때 줄을 그리지 않는다. */
export function memberLine(row: { memberNames?: string[] }): string {
  return (row.memberNames || []).join(" · ");
}

/** 대표자를 뺀 팀원 이름 목록. 빈 이름·중복 이름은 버린다. */
function otherMembers(
  team: QualifierRow[] | undefined, leadChannelId: string, leadName: string,
): string[] {
  if (!team) return [];
  const out: string[] = [];
  const seen = new Set<string>([leadName]);
  for (const mem of team) {
    if (mem.channelId === leadChannelId) continue;
    const name = clean(mem.channelName) || clean(mem.announcedName);
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(name);
  }
  return out;
}

/** channelId → 팀 번호(그룹 전용). 역시 전 멤버를 담는다. */
export function teamNumberByChannel(
  rows: (QualifierRow | QualifierGroupRow)[],
): Map<string, number> {
  const m = new Map<string, number>();
  for (const r of rows) {
    if (isGroupRow(r)) for (const mem of r.members) m.set(mem.channelId, r.teamNumber);
  }
  return m;
}

/** PIKU 순위 순서로 병합한다. `ranking`이 없으면 공지 순서(rank=null). */
export function mergeRanking(
  rows: (QualifierRow | QualifierGroupRow)[],
  ranking: PikuEntry[] | null,
): MergedRow[] {
  const byChannel = indexByChannel(rows);
  const teamOf = teamNumberByChannel(rows);
  const teamMembers = teamMembersByChannel(rows);

  if (ranking && ranking.length > 0) {
    // **filter가 없는 것이 핵심이다.** 매칭 실패는 표시 품질 문제이지
    // 그 참가자가 순위에 없다는 뜻이 아니다.
    return ranking.map((e) => {
      const row = byChannel.get(e.channelId) ?? null;
      const displayName = clean(row?.channelName) || clean(row?.announcedName)
        || clean(e.name);
      return {
        channelId: e.channelId,
        rank: e.rank,
        // 공식 현재 닉네임을 우선한다(개명이 반영된 값). 못 찾으면 PIKU 표기.
        displayName,
        // 운영자가 직접 입력한 값이 있으면 그쪽이 이긴다.
        songTitle: clean(row?.songTitle) || clean(e.songTitle),
        artistName: clean(row?.artistName) || clean(e.artistName),
        row,
        teamNumber: teamOf.get(e.channelId),
        memberNames: otherMembers(
          teamMembers.get(e.channelId), e.channelId, displayName),
      };
    });
  }

  // 순위가 없을 때: 공지 순서. 그룹은 팀당 대표자 한 명만 세운다.
  const out: MergedRow[] = [];
  for (const r of rows) {
    const lead = isGroupRow(r) ? (r.members[0] ?? null) : r;
    if (!lead) continue;
    const leadName = clean(lead.channelName) || clean(lead.announcedName);
    out.push({
      channelId: lead.channelId,
      rank: null,
      displayName: leadName,
      songTitle: clean(lead.songTitle),
      artistName: clean(lead.artistName),
      row: lead,
      teamNumber: isGroupRow(r) ? r.teamNumber : undefined,
      memberNames: isGroupRow(r)
        ? otherMembers(r.members, lead.channelId, leadName) : [],
    });
  }
  return out;
}
