import type { Metadata } from "next";
import Link from "next/link";
import { Bot, ChevronLeft, CheckCircle, AlertCircle, Clock } from "lucide-react";
import Footer from "@/components/Footer";

export const metadata: Metadata = {
  title: "서버 상태 — NexBot",
  description: "NexBot 서비스 운영 상태 및 장애 현황을 확인합니다.",
};

type ServiceStatus = "operational" | "degraded" | "outage" | "maintenance";

const SERVICES: { name: string; description: string; status: ServiceStatus }[] = [
  { name: "Discord 봇",     description: "명령어 처리 및 서버 관리 기능", status: "operational" },
  { name: "웹 API",         description: "대시보드 백엔드 서비스",         status: "operational" },
  { name: "웹 대시보드",    description: "관리 대시보드 웹 인터페이스",    status: "operational" },
  { name: "치지직 모니터",  description: "방송 알림 및 팔로우 인증 연동",  status: "operational" },
];

const INCIDENTS: {
  date: string;
  title: string;
  description: string;
  resolved: boolean;
}[] = [
  // 장애가 없을 경우 비어있음. 장애 발생 시 여기에 추가.
];

const STATUS_META: Record<ServiceStatus, { label: string; color: string; icon: React.ReactNode }> = {
  operational:  { label: "정상",      color: "text-green-400",  icon: <CheckCircle size={16} className="text-green-400" />  },
  degraded:     { label: "성능 저하", color: "text-yellow-400", icon: <AlertCircle size={16} className="text-yellow-400" /> },
  outage:       { label: "장애",      color: "text-red-400",    icon: <AlertCircle size={16} className="text-red-400" />    },
  maintenance:  { label: "점검 중",   color: "text-blue-400",   icon: <Clock       size={16} className="text-blue-400" />   },
};

function overallStatus(services: typeof SERVICES): ServiceStatus {
  if (services.some(s => s.status === "outage"))       return "outage";
  if (services.some(s => s.status === "degraded"))     return "degraded";
  if (services.some(s => s.status === "maintenance"))  return "maintenance";
  return "operational";
}

const overall = overallStatus(SERVICES);
const OVERALL_BANNERS: Record<ServiceStatus, { bg: string; text: string; label: string }> = {
  operational:  { bg: "bg-green-500/10 border border-green-500/20",  text: "text-green-400",  label: "모든 시스템이 정상 운영 중입니다." },
  degraded:     { bg: "bg-yellow-500/10 border border-yellow-500/20", text: "text-yellow-400", label: "일부 서비스에 성능 저하가 발생하고 있습니다." },
  outage:       { bg: "bg-red-500/10 border border-red-500/20",      text: "text-red-400",    label: "일부 서비스에 장애가 발생하고 있습니다." },
  maintenance:  { bg: "bg-blue-500/10 border border-blue-500/20",    text: "text-blue-400",   label: "현재 점검이 진행 중입니다." },
};
const banner = OVERALL_BANNERS[overall];

export default function StatusPage() {
  return (
    <div className="min-h-screen bg-bg text-fg flex flex-col">
      <header className="sticky top-0 z-50 border-b border-border bg-bg/80 backdrop-blur">
        <div className="max-w-3xl mx-auto px-5 flex items-center gap-3" style={{ height: 60 }}>
          <Link href="/" className="flex items-center gap-2 font-bold text-[17px] text-fg">
            <Bot size={20} className="text-accent" />
            NexBot
          </Link>
        </div>
      </header>

      <main className="flex-1 max-w-3xl mx-auto w-full px-5 py-12">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors mb-8"
        >
          <ChevronLeft size={15} /> 홈으로
        </Link>

        <h1 className="text-3xl font-bold text-fg mb-2">서버 상태</h1>
        <p className="text-muted mb-8">NexBot 서비스의 운영 현황 및 장애 내역을 확인하세요.</p>

        {/* Overall status banner */}
        <div className={`flex items-center gap-3 px-5 py-4 rounded-xl mb-8 ${banner.bg}`}>
          {STATUS_META[overall].icon}
          <p className={`font-semibold ${banner.text}`}>{banner.label}</p>
        </div>

        {/* Services */}
        <div className="space-y-3 mb-10">
          {SERVICES.map((svc) => {
            const meta = STATUS_META[svc.status];
            return (
              <div
                key={svc.name}
                className="flex items-center justify-between px-5 py-4 rounded-xl bg-bg-card border border-border"
              >
                <div>
                  <p className="font-medium text-fg">{svc.name}</p>
                  <p className="text-sm text-muted mt-0.5">{svc.description}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0 ml-4">
                  {meta.icon}
                  <span className={`text-sm font-semibold ${meta.color}`}>{meta.label}</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Incidents */}
        <h2 className="text-xl font-bold text-fg mb-4">장애 / 공지 내역</h2>
        {INCIDENTS.length === 0 ? (
          <div className="rounded-xl bg-bg-card border border-border px-5 py-10 text-center">
            <CheckCircle size={32} className="mx-auto mb-3 text-green-400 opacity-60" />
            <p className="text-muted">최근 장애 내역이 없습니다.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {INCIDENTS.map((inc) => (
              <div key={inc.title} className="rounded-xl bg-bg-card border border-border p-5">
                <div className="flex items-center gap-2 mb-2">
                  {inc.resolved
                    ? <CheckCircle size={15} className="text-green-400" />
                    : <AlertCircle size={15} className="text-red-400" />}
                  <p className="font-semibold text-fg">{inc.title}</p>
                  <span className="ml-auto text-xs text-muted">{inc.date}</span>
                </div>
                <p className="text-sm text-muted leading-relaxed">{inc.description}</p>
                <p className={`text-xs mt-2 font-medium ${inc.resolved ? "text-green-400" : "text-red-400"}`}>
                  {inc.resolved ? "해결됨" : "처리 중"}
                </p>
              </div>
            ))}
          </div>
        )}
      </main>

      <Footer />
    </div>
  );
}
