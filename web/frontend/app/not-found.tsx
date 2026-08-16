import Link from "next/link";

// 404 화면.
//
// 왜 새로 만들었나: Next의 내장 404는 **루트 레이아웃 안에서** 렌더된다. 그래서
// 우측 하단 지원 버튼이 404 위에 그대로 떠 있었다(실측 확인). 경로만으로는 404를
// 알 수 없으므로 — 어떤 경로든 404가 될 수 있다 — 화면 쪽에서 표시를 달고
// `globals.css`가 그 표시를 보고 버튼을 감춘다.
//
// 표시 이름 `data-nb-chrome="minimal"`은 "이 화면에는 사이트 공통 부가 UI를 얹지
// 말라"는 뜻이다. 앞으로 비슷한 화면이 생기면 같은 속성만 달면 된다.

export default function NotFound() {
  return (
    <main data-nb-chrome="minimal"
          className="flex min-h-screen items-center justify-center px-6">
      <div className="text-center">
        <p className="text-sm font-semibold tabular-nums text-muted">404</p>
        <h1 className="mt-2 text-xl font-bold text-fg">페이지를 찾을 수 없습니다</h1>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          주소가 바뀌었거나 삭제된 페이지입니다.
        </p>
        <Link href="/" className="btn-primary nb-tap mt-6 inline-flex text-sm">
          홈으로 돌아가기
        </Link>
      </div>
    </main>
  );
}
