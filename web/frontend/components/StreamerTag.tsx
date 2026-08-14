"use client";
import type { StreamerTag } from "@/lib/types";

/** 목록 행에서 한 번에 보여 줄 소속 그룹 수. 나머지는 `+N`으로 접는다.
 *  백엔드 `streamer_tags.LIST_VISIBLE_TAGS`와 같은 값을 유지할 것. */
export const LIST_VISIBLE_TAGS = 2;

/** 그라데이션 방향 → CSS 각도.
 *
 *  **서버가 준 문자열을 CSS에 그대로 넣지 않는다.** 백엔드가 이미 닫힌 목록으로
 *  검증하지만, 화면에서도 아는 키만 각도로 바꾼다 — 두 겹이라야 한쪽이 느슨해져도
 *  임의 CSS가 스타일에 도달하지 못한다. 모르는 값은 기본값으로 떨어진다. */
const ANGLE: Record<string, string> = {
  "to-right": "90deg",
  "to-bottom-right": "135deg",
  "to-bottom": "180deg",
  "to-top-right": "45deg",
};

/** `#RRGGBB`만 통과시킨다. 아니면 회색으로 떨어뜨린다(화면이 깨지지 않게). */
const HEX = /^#[0-9a-fA-F]{6}$/;
const safeColor = (v: string | null | undefined, fallback: string) =>
  v && HEX.test(v) ? v : fallback;

const FALLBACK_START = "#38BDF8";
const FALLBACK_END = "#C084FC";

/* ── 글자색 — 테마마다 다른 값이 필요하다 ──────────────────────────────────
   배지 배경은 태그 색을 12%만 깐 것이라, 실제로 글자 뒤에 오는 것은 **카드 배경**이다
   (다크 #1a1d27 / 라이트 #ffffff). 그래서 한 가지 색으로는 양쪽을 만족할 수 없다:
   운영자가 고른 색이 아주 어두우면(#123456) 다크에서 안 보이고, 아주 밝으면(#FACC15)
   라이트에서 안 보인다.
   해결은 **색상(hue)은 그대로 두고 명도만 테마별 안전 구간으로 끌어오는 것**이다.
   두 값을 CSS 변수로 내보내고, `globals.css`가 `html.light`에서 두 번째 값을 쓴다
   (테마를 JS로 읽으면 하이드레이션 때 색이 한 번 튄다). */

function hexToHsl(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  if (!d) return [0, 0, l];
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? ((g - b) / d + (g < b ? 6 : 0))
          : max === g ? (b - r) / d + 2
          : (r - g) / d + 4;
  return [h / 6, s, l];
}

function hslToHex(h: number, s: number, l: number): string {
  const f = (n: number) => {
    const k = (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    const v = l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
    return Math.round(v * 255).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

/** WCAG 상대 휘도. **HSL의 L과 다르다** — 이 차이가 중요하다:
 *  고채도 노랑(#FACC15)은 HSL L이 0.53에 불과한데 실제 휘도는 0.65다.
 *  HSL L만 눌러서는 흰 배경에서 절대 대비가 안 나온다(실측 2.00:1). */
function relLuminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const f = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f((n >> 16) & 255) + 0.7152 * f((n >> 8) & 255) + 0.0722 * f(n & 255);
}

/** 색상·채도는 그대로 두고 **휘도만** 목표에 맞춘다(이분 탐색 12회면 충분).
 *  `dir === "down"`이면 목표 이하로, `"up"`이면 목표 이상으로 민다. */
function targetLuminance(hex: string, target: number, dir: "up" | "down"): string {
  if (dir === "down" ? relLuminance(hex) <= target : relLuminance(hex) >= target) return hex;
  const [h, s] = hexToHsl(hex);
  let lo = 0, hi = 1;
  for (let i = 0; i < 12; i++) {
    const mid = (lo + hi) / 2;
    if (relLuminance(hslToHex(h, s, mid)) < target) lo = mid; else hi = mid;
  }
  // dir=down이면 목표 이하여야 하므로 아래쪽(lo), up이면 위쪽(hi)을 고른다.
  return hslToHex(h, s, dir === "down" ? lo : hi);
}

/* 대비 4.5:1(WCAG AA 본문)을 만족시키는 휘도 경계.
   **기준 배경은 카드가 아니라 페이지 배경이다.** 카드가 반투명(`bg-bg-card/60`)인
   화면이 있어 글자 뒤에 실제로 깔리는 것은 `--color-bg`쪽이다.
     다크  #0f1117 (휘도 0.0114) → 글자 휘도 ≥ 4.5×(0.0114+0.05) − 0.05 = 0.226
     라이트 #f0f2f5 (휘도 0.878) → 글자 휘도 ≤ (0.928/4.5) − 0.05 = 0.156
   여유를 조금 둔다. 순백 카드 위에서는 이보다 더 좋아지기만 한다. */
const DARK_MIN_LUM = 0.245;
const LIGHT_MAX_LUM = 0.150;

/** 다크 카드(#1a1d27) 위에서 AA를 만족하는 글자색 */
const forDark = (hex: string) => targetLuminance(hex, DARK_MIN_LUM, "up");
/** 라이트 카드(#ffffff) 위에서 AA를 만족하는 글자색 */
const forLight = (hex: string) => targetLuminance(hex, LIGHT_MAX_LUM, "down");

/**
 * 스트리머 소속 그룹 배지 하나.
 *
 * 사용자에게는 "소속 그룹"이라 부르지만 내부 식별자(`StreamerTag`·`team_tags`·
 * `streamer_tags`)는 그대로 둔다 — 운영 데이터와 API 계약을 끌고 가지 않기 위해서다.
 *
 * 모양은 **가로로 긴 둥근 직사각형**이다 — 완전한 캡슐(`rounded-full`)은 사이트의
 * 다른 pill(카테고리·상태)과 구별되지 않아 일부러 피했다.
 *
 * 단일색: 옅은 배경 + 같은 색 테두리 + 진한 글씨.
 * 그라데이션: 같은 계열의 옅은 배경 위에 **글씨에도 같은 그라데이션**을 입힌다.
 * `background-clip: text`를 지원하지 않는 환경에서는 `color` 폴백이 그대로 보인다
 * (그래서 `color`를 먼저 주고 그 위에 clip을 얹는다 — 순서를 바꾸면 폴백이 사라진다).
 */
export function StreamerTagBadge({ tag, className = "" }: {
  tag: StreamerTag;
  className?: string;
}) {
  const start = safeColor(tag.colorStart, FALLBACK_START);
  const isGradient = tag.colorMode === "gradient" && !!tag.colorEnd;
  const end = isGradient ? safeColor(tag.colorEnd, FALLBACK_END) : start;
  const angle = ANGLE[tag.gradientDirection] ?? ANGLE["to-right"];

  // 글자색은 테마마다 다르다(위 주석). CSS 변수 두 개로 내보내고 선택은 CSS가 한다.
  const fgDark = forDark(start), fgLight = forLight(start);
  const endDark = forDark(end), endLight = forLight(end);

  const style = {
    // 배경은 아주 옅게(12%) — 태그가 목록에서 튀면 정작 이름이 안 읽힌다.
    background: isGradient
      ? `linear-gradient(${angle}, ${start}1F, ${end}1F) padding-box`
      : `${start}1F`,
    borderColor: `${start}59`,
    "--nb-tag-fg": fgDark,
    "--nb-tag-fg-l": fgLight,
    // 그라데이션 글자용 — 같은 이유로 테마별 두 벌
    "--nb-tag-grad": `linear-gradient(${angle}, ${fgDark}, ${endDark})`,
    "--nb-tag-grad-l": `linear-gradient(${angle}, ${fgLight}, ${endLight})`,
  } as React.CSSProperties;

  return (
    <span
      className={`nb-tag inline-flex max-w-[10rem] shrink-0 items-center rounded-md border
                  px-1.5 py-0.5 text-[11px] font-bold leading-tight ${className}`}
      style={style}
      title={tag.name}
      /* 색만으로 뜻을 전하지 않도록 무엇인지 읽어 주는 라벨을 붙인다.
         배지에는 그룹명만 보이고, 스크린리더에만 "소속 그룹:"이 붙는다 —
         치지직 **방송 태그**와 헷갈리지 않게 하는 게 목적이다. */
      aria-label={`소속 그룹: ${tag.name}`}
    >
      {isGradient ? (
        // `nb-tag-grad`가 background-clip:text를 건다. 지원하지 않는 환경에서는
        // 부모(.nb-tag)에서 상속된 `color`가 그대로 보이므로 폴백이 성립한다 —
        // 그래서 여기에 color를 다시 쓰지 않는다(쓰면 폴백이 그 값으로 고정된다).
        <span className="nb-tag-grad truncate">{tag.name}</span>
      ) : (
        <span className="truncate">{tag.name}</span>
      )}
    </span>
  );
}

/**
 * 소속 그룹 목록 — 정해진 개수만 보여 주고 나머지는 `+N`.
 *
 * 레이아웃 계약:
 * · `shrink-0`인 태그가 이름을 밀어내지 않도록 **이 컨테이너가 줄어든다**
 *   (`min-w-0`). 이름 쪽에도 `truncate`가 있어야 완성된다.
 * · `flex-wrap`이라 좁은 화면에서는 아래로 접힌다 — 가로 스크롤을 만들지 않는다.
 * · 태그가 없으면 **아무것도 렌더하지 않는다**(빈 span도 남기지 않는다).
 *   그래야 태그 없는 스트리머의 행 높이가 지금과 완전히 같다.
 */
export function StreamerTagList({ tags, max = LIST_VISIBLE_TAGS, className = "" }: {
  tags?: StreamerTag[] | null;
  max?: number;
  className?: string;
}) {
  if (!tags || tags.length === 0) return null;
  const shown = tags.slice(0, max);
  const rest = tags.length - shown.length;
  return (
    <span className={`inline-flex min-w-0 flex-wrap items-center gap-1 ${className}`}>
      {shown.map((t) => <StreamerTagBadge key={t.id} tag={t} />)}
      {rest > 0 && (
        <span className="shrink-0 rounded-md border border-border px-1.5 py-0.5
                         text-[11px] font-bold leading-tight text-muted"
              title={tags.slice(max).map((t) => t.name).join(", ")}>
          +{rest}
        </span>
      )}
    </span>
  );
}
