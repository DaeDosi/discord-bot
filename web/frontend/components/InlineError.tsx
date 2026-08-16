"use client";

/** 폼 안에서 **동작 하나가** 실패했을 때의 한 줄 알림.
 *
 *  `DashboardError`와 역할이 다르다: 저쪽은 화면 전체가 실패해 아무것도 조작할 수
 *  없을 때 폼을 대체하고, 이쪽은 폼은 멀쩡한데 이번 저장만 실패했을 때 붙는다.
 *  둘을 섞으면 "저장 한 번 실패했다고 설정 화면이 통째로 사라지는" 화면이 된다. */
export default function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className="text-sm text-danger bg-danger/10 border border-danger/30 rounded-lg px-4 py-3"
    >
      {message}
    </p>
  );
}
