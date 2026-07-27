// /stats 의 '매트 다크 패널' 카드 공통 값.
//
// 카테고리 카드(CategoryCard)와 블루오션 카드가 같은 룩을 써야 해서 값을 한 곳에 둔다.
// 카드 안쪽에는 어떤 그라데이션/이미지 오버레이도 깔지 않는다(내부는 완전 단색).
// 흐르는 테두리는 globals.css의 .nb-neon-border 가 담당한다:
//   - .nb-neon-border            : 호버/포커스 때만 네온이 흐른다(클릭 가능한 카드용)
//   - .nb-neon-border.nb-neon-always : 상시로 흐른다(클릭할 수 없는 표시용 카드)
export const CARD_DARK = "#181A20";
// 4위 이하 카테고리 카드와 같은 차분한 테두리색
export const CARD_BORDER = "rgba(31,41,55,0.80)";
// 카드 내부 보조 텍스트(회색) — 다크 패널 위에서만 쓰이므로 테마 변수 대신 고정색
export const CARD_SUB_TEXT = "#9CA3AF";
