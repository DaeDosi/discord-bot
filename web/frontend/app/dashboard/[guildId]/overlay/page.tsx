"use client";
import { MonitorPlay, Clock } from "lucide-react";

// 치지직 오버레이 기능은 현재 비활성화 상태입니다.
// 이전 구현(토큰 발급/재발급 + 도박·미션 오버레이 미리보기)은 git 히스토리에 보존돼 있으며,
// 재활성화하려면 이 파일을 복구하고 components/Sidebar.tsx의 "/overlay" 네비 항목 주석을 해제하세요.
export default function OverlayPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title flex items-center gap-2">
          <MonitorPlay size={20} className="text-muted" /> 오버레이
        </h1>
        <p className="page-subtitle">오버레이 기능은 현재 비활성화되어 있습니다.</p>
      </div>

      <div className="card text-center py-16 text-muted">
        <MonitorPlay size={40} className="mx-auto mb-3 opacity-30" />
        <p className="font-medium text-fg">오버레이 기능은 현재 비활성화되어 있습니다.</p>
        <p className="text-sm mt-1 flex items-center justify-center gap-1.5">
          <Clock size={13} /> 추후 다시 제공될 예정입니다.
        </p>
      </div>
    </div>
  );
}
