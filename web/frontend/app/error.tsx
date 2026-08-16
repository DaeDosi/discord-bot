"use client";

// 오류 화면. `not-found.tsx`와 같은 이유로 존재한다 — 오류 화면에도 루트 레이아웃이
// 적용돼 우측 하단 지원 버튼이 그대로 떠 있었다. `data-nb-chrome="minimal"`을 달아
// `globals.css`가 감추게 한다.
//
// 동작은 Next 기본과 같게 유지한다: 다시 시도(`reset`) 하나만 제공하고, 오류 내용은
// 화면에 노출하지 않는다(내부 정보가 새어 나갈 수 있다).

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <main data-nb-chrome="minimal"
          className="flex min-h-screen items-center justify-center px-6">
      <div className="text-center">
        <h1 className="text-xl font-bold text-fg">화면을 불러오지 못했습니다</h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          잠시 후 다시 시도해 주세요.
        </p>
        <button type="button" onClick={reset}
                className="btn-primary nb-tap mt-6 inline-flex text-sm">
          다시 시도
        </button>
      </div>
    </main>
  );
}
