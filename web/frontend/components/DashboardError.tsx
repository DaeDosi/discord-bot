"use client";
import { AlertCircle, RefreshCw, ShieldOff } from "lucide-react";
import { dashboardErrorCopy } from "@/lib/dashboardErrors";

/** 대시보드 화면 하나가 통째로 실패했을 때의 공통 표면.
 *
 *  빈 상태와 섞이지 않게 **오류 전용**으로만 쓴다. 재시도 버튼은 다시 눌러서
 *  결과가 달라질 수 있는 오류(5xx·네트워크)에만 붙인다 — 403에 붙이면 몇 번을
 *  눌러도 같은 화면이라 사용자가 갇힌다. */
export default function DashboardError(
  { error, onRetry }: { error: unknown; onRetry?: () => void },
) {
  const copy = dashboardErrorCopy(error);
  const Icon = copy.kind === "forbidden" ? ShieldOff : AlertCircle;

  return (
    <div role="alert" className="card text-center py-12 px-6 space-y-3">
      <Icon size={36} className="mx-auto text-danger" />
      <p className="text-fg font-semibold text-lg">{copy.title}</p>
      <p className="text-muted text-sm max-w-md mx-auto">{copy.detail}</p>
      {copy.retryable && onRetry && (
        <button onClick={onRetry} className="btn-secondary text-sm mx-auto mt-2">
          <RefreshCw size={14} /> 다시 시도
        </button>
      )}
    </div>
  );
}
