"use client";
import { Youtube, Clock } from "lucide-react";

export default function YoutubePage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title flex items-center gap-2">
          <Youtube size={20} className="text-danger" /> 유튜브
        </h1>
        <p className="page-subtitle">
          유튜브 방송/영상 알림 연동을 준비 중입니다.
        </p>
      </div>

      <div className="card text-center py-16 text-muted">
        <Youtube size={40} className="mx-auto mb-3 opacity-30" />
        <p className="font-medium text-fg">유튜브 알림 연동을 준비 중입니다.</p>
        <p className="text-sm mt-1 flex items-center justify-center gap-1.5">
          <Clock size={13} /> 곧 만나보실 수 있어요!
        </p>
      </div>
    </div>
  );
}
