// ADMIN-GROUP — 다중 색상 그라데이션(3-1)과 멤버 추가 상태 동기화(3-2) 계약.
//
// 왜 소스 텍스트를 읽는가: 이 저장소의 프론트 테스트는 `node --test lib/*.test.ts`로
// 의존성 없이 돈다(테스트 러너·DOM 라이브러리를 새로 들이지 않는 것이 관행이다).
// 여기서 막으려는 것은 "리팩터링하다 조용히 원복되는 것"이고, 실제 동작은 브라우저
// QA와 백엔드 pytest(`tests/test_streamer_tag_gradient.py`)가 따로 본다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(ROOT, p), "utf8");

// ── 3-1) 다중 그라데이션 ────────────────────────────────────────────────────

test("배지는 색상 지점 배열로 그린다(colorMode 2색 근사를 신뢰하지 않는다)", () => {
  const s = read("components/StreamerTag.tsx");
  assert.ok(s.includes("export function resolveStops"),
    "지점 계산은 한 곳(resolveStops)에서만 한다");
  assert.ok(/const isGradient = stops\.length > 1/.test(s),
    "그라데이션 판정은 지점 개수로 한다 — colorMode는 3색 이상을 2색으로 근사한다");
  assert.ok(!/tag\.colorMode === "gradient" && !!tag\.colorEnd/.test(s),
    "구형 판정이 남아 있으면 3번째 색이 조용히 사라진다");
});

test("그라데이션 문자열에 지점 위치(%)가 들어간다", () => {
  const s = read("components/StreamerTag.tsx");
  assert.ok(/\$\{s\.pos\}%/.test(s), "각 지점의 위치가 CSS에 반영돼야 한다");
  assert.ok(s.includes("function gradientOf"), "생성 지점이 하나여야 한다");
});

test("색은 여전히 #RRGGBB만 통과한다(주입 경로 차단)", () => {
  const s = read("components/StreamerTag.tsx");
  assert.ok(/const HEX = \/\^#\[0-9a-fA-F\]\{6\}\$\//.test(s));
  assert.ok(s.includes("HEX.test"), "지점 배열도 같은 검사를 통과해야 한다");
  // 방향은 닫힌 목록 → 각도로만 변환한다. 서버 문자열을 CSS에 그대로 넣지 않는다.
  assert.ok(/ANGLE\[tag\.gradientDirection\] \?\? ANGLE\["to-right"\]/.test(s));
});

test("구형 데이터는 백필 없이 읽을 때 합성된다", () => {
  const s = read("components/StreamerTag.tsx");
  assert.ok(s.includes('tag.colorMode === "gradient" && tag.colorEnd'),
    "colorStops가 없는 캐시된 옛 응답에서도 색이 나와야 한다");
  assert.ok(/return \[\{ color: start, pos: 0 \}\]/.test(s),
    "최소 1개 지점은 항상 남는다 — 0개면 투명 배지다");
});

test("편집기는 지점 추가·삭제·순서변경·위치를 모두 제공한다", () => {
  const s = read("app/nexadmin/StreamerTagsPanel.tsx");
  for (const fn of ["const addStop", "const removeStop", "const moveStop",
                    "const distribute"]) {
    assert.ok(s.includes(fn), `${fn}이 있어야 한다`);
  }
  assert.ok(s.includes("색상 추가"), "추가 버튼");
  assert.ok(/type="number"[\s\S]{0,120}min=\{0\} max=\{100\}/.test(s),
    "위치는 0~100 숫자 입력이다");
});

test("최소 1개 색상은 항상 남는다(프론트에서도 막는다)", () => {
  const s = read("app/nexadmin/StreamerTagsPanel.tsx");
  assert.ok(/prev\.length <= 1 \? prev :/.test(s),
    "마지막 한 개는 삭제되지 않아야 한다");
  assert.ok(s.includes("canRemove"), "버튼도 비활성으로 이유를 보여 준다");
});

test("지점 개수 상한은 서버 값을 쓴다", () => {
  const panel = read("app/nexadmin/StreamerTagsPanel.tsx");
  assert.ok(panel.includes("res.maxColorStops"),
    "프론트 상수로 두면 서버 상한과 조용히 갈라진다");
  assert.ok(/prev\.length >= maxStops/.test(panel), "상한에서 추가를 막는다");
  const types = read("lib/types.ts");
  assert.ok(types.includes("maxColorStops"), "응답 타입에도 있어야 한다");
});

test("편집기는 신형 표현만 보낸다(진실의 출처가 둘이 되지 않게)", () => {
  const s = read("app/nexadmin/StreamerTagsPanel.tsx");
  assert.ok(/colorStops: stops\.map/.test(s));
  // 구형 3필드를 함께 보내면 서버에서 어느 쪽이 이기는지가 화면마다 달라진다.
  assert.ok(!/colorMode,\s*colorStart,/.test(s),
    "구형 필드를 같이 보내지 않는다");
});

test("색상 방식 셀렉트는 지점 개수로 대체됐다", () => {
  const s = read("app/nexadmin/StreamerTagsPanel.tsx");
  assert.ok(!s.includes('<option value="solid">단일색</option>'),
    "방식 셀렉트가 남아 있으면 '그라데이션인데 색 1개' 모순 상태가 만들어진다");
  assert.ok(s.includes("색상이 1개면 단일색, 2개 이상이면 그라데이션"),
    "규칙을 화면에서 설명해야 한다");
});

test("미리보기는 실제 배지 컴포넌트를 쓴다(따로 그리면 드리프트한다)", () => {
  const s = read("app/nexadmin/StreamerTagsPanel.tsx");
  assert.ok(s.includes("<StreamerTagBadge tag={preview} />"));
  assert.ok(s.includes("resolveStops"), "구형 데이터 흡수도 같은 함수로 한다");
});

test("지점 행 버튼은 44px 터치 영역 프리미티브를 재사용한다", () => {
  const s = read("app/nexadmin/StreamerTagsPanel.tsx");
  assert.ok(s.includes("nb-tap-icon"), "기존 클래스를 쓴다 — 새로 만들지 않는다");
  assert.ok(s.includes("nb-tap-gap"), "타깃이 커진 만큼 간격도 벌린다");
});

test("지점 입력에 접근 가능한 이름이 있다", () => {
  const s = read("app/nexadmin/StreamerTagsPanel.tsx");
  for (const label of ["hex 코드", "위치(퍼센트)", "위로 이동", "아래로 이동", "삭제"]) {
    assert.ok(s.includes(label), `aria-label에 "${label}"이 있어야 한다`);
  }
  assert.ok(s.includes("aria-invalid"), "오류 상태를 보조기기에도 알린다");
});

// ── 3-2) 멤버 추가 상태 동기화 ──────────────────────────────────────────────

test("추가됨 판정은 서버가 준 태그 목록이 우선이다", () => {
  const s = read("app/nexadmin/GroupMembersDrawer.tsx");
  assert.ok(/const already = r\.tags\.some\(\(t\) => t\.id === group\.id\)\s*\|\|\s*memberIds\.has/
    .test(s), "현재 페이지(30명)만 보면 31번째부터 판정이 틀린다");
  assert.ok(s.includes("const patchSearchRow"),
    "성공 응답으로 그 줄을 갈아 끼워야 버튼이 즉시 바뀐다");
  assert.ok(/patchSearchRow\(id, res\.tags/.test(s));
});

test("제거하면 다시 추가할 수 있어야 한다", () => {
  const s = read("app/nexadmin/GroupMembersDrawer.tsx");
  assert.ok(s.includes("const removeMember"));
  // 제거도 같은 patch 경로를 타야 검색 결과가 "추가"로 돌아간다.
  assert.ok(s.split("patchSearchRow(id,").length >= 3,
    "추가와 제거 양쪽에서 검색 결과를 갱신해야 한다");
  assert.ok(s.includes("void removeMember(t)"), "제거 확인도 같은 경로를 쓴다");
});

test("중복 요청은 동기 잠금(ref)으로 막는다", () => {
  const s = read("app/nexadmin/GroupMembersDrawer.tsx");
  assert.ok(s.includes("const inFlight = useRef<Set<string>>"),
    "state 잠금은 다음 렌더에야 반영돼 같은 tick의 두 번째 클릭을 못 막는다");
  assert.ok(s.split("inFlight.current.has(id)").length >= 3,
    "추가·제거 양쪽에 잠금이 있어야 한다");
  assert.ok(s.includes("inFlight.current.delete(id)"), "finally에서 반드시 푼다");
});

test("행마다 진행 상태를 보여 준다", () => {
  const s = read("app/nexadmin/GroupMembersDrawer.tsx");
  assert.ok(s.includes("const [pending, setPending]"));
  assert.ok(s.includes('"추가 중…"'), "반응이 없다는 인상을 없앤다");
  assert.ok(s.includes("aria-busy={isPending}"), "보조기기에도 알린다");
});

test("멤버 수는 그룹 전체 수와 검색 결과 수를 분리한다", () => {
  const s = read("app/nexadmin/GroupMembersDrawer.tsx");
  assert.ok(s.includes("const [groupTotal, setGroupTotal]"));
  assert.ok(s.includes("const [listTotal, setListTotal]"));
  assert.ok(/if \(!opts\.q\) setGroupTotal\(res\.total\)/.test(s),
    "걸러진 수로 그룹 전체 수를 덮어쓰면 '수가 안 늘었다'로 보인다");
  assert.ok(/setGroupTotal\(\(n\) => n \+ 1\)/.test(s), "추가 시 즉시 반영");
  assert.ok(/setGroupTotal\(\(n\) => Math\.max\(0, n - 1\)\)/.test(s), "제거 시 즉시 반영");
});

test("실패를 성공으로 꾸미지 않는다", () => {
  const s = read("app/nexadmin/GroupMembersDrawer.tsx");
  assert.ok(s.includes('"멤버 추가에 실패했습니다."'));
  assert.ok(s.includes('"멤버 제거에 실패했습니다."'));
  // 추가는 낙관적 반영을 쓰지 않는다 → 실패 시 되돌릴 화면 상태가 없다.
  assert.ok(s.includes("낙관적 반영을 쓰지 않는다"),
    "그 판단의 이유가 코드에 남아 있어야 한다");
});

test("순서 변경은 낙관적 반영이므로 실패 시 되돌린다", () => {
  const s = read("app/nexadmin/GroupMembersDrawer.tsx");
  assert.ok(/const before = members/.test(s), "되돌릴 원본을 잡아 둔다");
  assert.ok(s.split("setMembers(before)").length >= 3,
    "busy 충돌과 요청 실패 양쪽에서 되돌려야 한다");
  assert.ok(s.includes('"순서 변경에 실패했습니다."'));
});
