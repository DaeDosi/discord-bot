"use client";
import { Twitter, Clock } from "lucide-react";

export default function TwitterPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title flex items-center gap-2">
          <Twitter size={20} className="text-fg" /> X (트위터)
        </h1>
        <p className="page-subtitle">
          X(트위터) 알림 연동을 준비 중입니다.
        </p>
      </div>

      <div className="card text-center py-16 text-muted">
        <Twitter size={40} className="mx-auto mb-3 opacity-30" />
        <p className="font-medium text-fg">X(트위터) 알림 연동을 준비 중입니다.</p>
        <p className="text-sm mt-1 flex items-center justify-center gap-1.5">
          <Clock size={13} /> 곧 만나보실 수 있어요!
        </p>
      </div>
    </div>
  );
}
