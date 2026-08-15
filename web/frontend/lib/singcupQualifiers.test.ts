// 공식 예선 참가자 정적 명단의 불변식 검증.
//
// 실행: web/frontend 에서
//   node --test lib/singcupQualifiers.test.ts
// (api.test.ts와 같은 방식 — Node 24가 타입 표기를 벗기므로 러너·의존성이 없다.)
//
// 이 테스트가 지키는 것은 "공지에 실린 그대로인가"다. 명단은 대회 규칙상 확정 값이라
// 수가 하나라도 어긋나면 화면이 아니라 **데이터가 틀린 것**이고, 순위·참가 자격
// 표기가 통째로 잘못된다. 그래서 개수 계약(64/64/32팀/73링크)을 상수로 박아 둔다 —
// 명단을 다시 생성했을 때 조용히 달라지는 것을 여기서 막는다.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  SINGCUP_QUALIFIERS,
  allQualifierChannelIds,
  allSoloQualifiers,
  qualifierChannelUrl,
} from "./singcupQualifiers.ts";

const HEX32 = /^[0-9a-f]{32}$/;

const { femaleSolo, maleSolo, groups, counts } = SINGCUP_QUALIFIERS;
const groupMembers = groups.flatMap((g) => g.members);

describe("공식 명단 개수 계약", () => {
  it("여성 솔로 64 · 남성 솔로 64 · 그룹 32팀 · 그룹 멤버 링크 73", () => {
    assert.equal(femaleSolo.length, 64);
    assert.equal(maleSolo.length, 64);
    assert.equal(groups.length, 32);
    assert.equal(groupMembers.length, 73);
  });

  it("counts 필드가 실제 배열 길이와 일치한다", () => {
    // counts는 화면이 그대로 쓰는 값이라 배열과 어긋나면 표기가 거짓말이 된다.
    assert.equal(counts.femaleSolo, femaleSolo.length);
    assert.equal(counts.maleSolo, maleSolo.length);
    assert.equal(counts.groups, groups.length);
    assert.equal(counts.groupMemberLinks, groupMembers.length);
  });

  it("솔로 합계는 128명이다", () => {
    assert.equal(allSoloQualifiers().length, 128);
  });
});

describe("channel_id — 조인 키", () => {
  it("전부 32자 소문자 hex다", () => {
    for (const id of allQualifierChannelIds()) {
      assert.match(id, HEX32, `형식 위반: ${id}`);
    }
  });

  it("솔로 내부에 중복이 없다", () => {
    const ids = allSoloQualifiers().map((s) => s.channelId);
    assert.equal(new Set(ids).size, ids.length);
  });

  it("그룹 멤버 내부에 중복이 없다", () => {
    const ids = groupMembers.map((m) => m.channelId);
    assert.equal(new Set(ids).size, ids.length);
  });

  it("여성 솔로와 남성 솔로가 겹치지 않는다", () => {
    const f = new Set(femaleSolo.map((s) => s.channelId));
    const dup = maleSolo.filter((s) => f.has(s.channelId));
    assert.deepEqual(dup, []);
  });

  it("솔로와 그룹이 겹치지 않는다(중복 출전 없음)", () => {
    const solo = new Set(allSoloQualifiers().map((s) => s.channelId));
    const dup = groupMembers.filter((m) => solo.has(m.channelId));
    assert.deepEqual(dup, []);
  });

  it("전체 201개가 모두 서로 다르다", () => {
    const ids = allQualifierChannelIds();
    assert.equal(ids.length, 201);
    assert.equal(new Set(ids).size, 201);
  });
});

describe("순서와 팀 번호", () => {
  it("여성·남성 솔로의 officialOrder가 각각 1..64로 연속이다", () => {
    for (const list of [femaleSolo, maleSolo]) {
      const orders = list.map((s) => s.officialOrder).sort((a, b) => a - b);
      assert.deepEqual(orders, Array.from({ length: 64 }, (_, i) => i + 1));
    }
  });

  it("팀 번호가 1..32로 연속이다", () => {
    const nums = groups.map((g) => g.teamNumber).sort((a, b) => a - b);
    assert.deepEqual(nums, Array.from({ length: 32 }, (_, i) => i + 1));
  });

  it("팀마다 멤버가 1명 이상이고 memberOrder가 1부터 연속이다", () => {
    for (const g of groups) {
      assert.ok(g.members.length >= 1, `팀 ${g.teamNumber} 멤버 0명`);
      const orders = g.members.map((m) => m.memberOrder).sort((a, b) => a - b);
      assert.deepEqual(orders, Array.from({ length: g.members.length }, (_, i) => i + 1),
        `팀 ${g.teamNumber} memberOrder 불연속`);
    }
  });

  it("팀당 멤버 분포가 발표 그대로다(1명 1팀 / 2명 23팀 / 3명 6팀 / 4명 2팀)", () => {
    const dist: Record<number, number> = {};
    for (const g of groups) dist[g.members.length] = (dist[g.members.length] ?? 0) + 1;
    assert.deepEqual(dist, { 1: 1, 2: 23, 3: 6, 4: 2 });
  });

  it("groupEntryId가 팀마다 고유하다", () => {
    const ids = groups.map((g) => g.groupEntryId);
    assert.equal(new Set(ids).size, ids.length);
  });
});

describe("이름", () => {
  it("빈 이름이 없다", () => {
    for (const s of allSoloQualifiers()) assert.ok(s.announcedName.trim().length > 0);
    for (const m of groupMembers) assert.ok(m.announcedName.trim().length > 0);
  });
});

describe("채널 URL", () => {
  it("전부 chzzk.naver.com이고 경로가 channelId다", () => {
    // URL을 저장하지 않고 파생하므로, 규칙이 깨지면 링크가 통째로 틀어진다.
    for (const id of allQualifierChannelIds()) {
      const u = new URL(qualifierChannelUrl(id));
      assert.equal(u.protocol, "https:");
      assert.equal(u.host, "chzzk.naver.com");
      assert.equal(u.pathname, `/${id}`);
    }
  });
});

describe("출처 메타데이터", () => {
  it("공지 원문 링크가 치지직 공식 라운지다", () => {
    const u = new URL(SINGCUP_QUALIFIERS.sourceUrl);
    assert.equal(u.host, "game.naver.com");
    assert.match(u.pathname, /^\/lounge\/chzzk\/board\/detail\/\d+$/);
  });

  it("시즌 키와 제목이 비어 있지 않다", () => {
    assert.equal(SINGCUP_QUALIFIERS.seasonKey, "galaxy-2026");
    assert.ok(SINGCUP_QUALIFIERS.sourceTitle.trim().length > 0);
    assert.ok(SINGCUP_QUALIFIERS.retrievedAt.trim().length > 0);
  });
});
