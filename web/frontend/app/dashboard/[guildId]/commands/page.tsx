"use client";
import { Terminal } from "lucide-react";

interface Cmd { name: string; desc: string; admin?: boolean }
interface Category { label: string; color: string; cmds: Cmd[] }

const CATEGORIES: Category[] = [
  {
    label: "서버 관리",
    color: "#ED4245",
    cmds: [
      { name: "/경고",       desc: "멤버에게 경고를 부여합니다",             admin: true },
      { name: "/경고내역",   desc: "멤버의 경고 내역을 확인합니다",           admin: true },
      { name: "/경고초기화", desc: "멤버의 경고를 모두 초기화합니다",         admin: true },
      { name: "/추방",       desc: "멤버를 서버에서 추방합니다",              admin: true },
      { name: "/차단",       desc: "멤버를 서버에서 영구 차단합니다",         admin: true },
      { name: "/차단해제",   desc: "차단된 유저를 해제합니다",                admin: true },
      { name: "/뮤트",       desc: "멤버에게 타임아웃을 적용합니다",          admin: true },
      { name: "/뮤트해제",   desc: "멤버의 타임아웃을 해제합니다",            admin: true },
      { name: "/청소",       desc: "채널의 메시지를 일괄 삭제합니다",         admin: true },
    ],
  },
  {
    label: "리액션 역할",
    color: "#EB459E",
    cmds: [
      { name: "/반응역할",     desc: "메시지에 리액션 역할을 추가합니다",    admin: true },
      { name: "/반응역할제거", desc: "메시지의 리액션 역할을 제거합니다",    admin: true },
      { name: "/반응역할목록", desc: "서버의 리액션 역할 목록을 확인합니다" },
    ],
  },
  {
    label: "입장 인증",
    color: "#57F287",
    cmds: [
      { name: "/입장메시지설정", desc: "입장 인증 임베드를 채널에 전송합니다", admin: true },
    ],
  },
  {
    label: "레벨링",
    color: "#FEE75C",
    cmds: [
      { name: "/랭크",    desc: "자신(또는 멤버)의 레벨·XP를 확인합니다" },
      { name: "/리더보드", desc: "서버 레벨 랭킹 상위 10명을 확인합니다" },
      { name: "/xp설정",  desc: "멤버의 XP를 직접 설정합니다",              admin: true },
    ],
  },
  {
    label: "치지직",
    color: "#03C75A",
    cmds: [
      { name: "/치지직설정",      desc: "웹 대시보드 치지직 알림 설정 링크를 표시합니다", admin: true },
      { name: "/치지직알림테스트", desc: "등록된 치지직 알림을 테스트 전송합니다",         admin: true },
    ],
  },
  {
    label: "시청자 참여 (시참)",
    color: "#5865F2",
    cmds: [
      { name: "/시참등록",     desc: "시참 대기열에 등록합니다" },
      { name: "/시참취소",     desc: "시참 등록을 취소합니다" },
      { name: "/시참확인",     desc: "본인의 대기 번호·현황을 확인합니다" },
      { name: "/시참목록",     desc: "현재 시참 대기열 전체를 확인합니다" },
      { name: "/시참시작",     desc: "시참 모집을 시작합니다",                   admin: true },
      { name: "/시참종료",     desc: "시참 모집을 종료하고 대기열을 초기화합니다", admin: true },
      { name: "/시참호출",     desc: "대기열에서 n명을 호출합니다",               admin: true },
      { name: "/시참건너뛰기", desc: "유저를 대기열에서 제거합니다",              admin: true },
    ],
  },
  {
    label: "기타",
    color: "#8B8FA8",
    cmds: [
      { name: "/명령어", desc: "NexBot의 모든 명령어를 페이지로 확인합니다" },
    ],
  },
];

export default function CommandsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-white flex items-center gap-2">
          <Terminal size={20} className="text-accent" /> 명령어 목록
        </h1>
        <p className="text-muted text-sm mt-1">
          NexBot의 모든 슬래시 명령어를 확인합니다. Discord에서{" "}
          <code className="text-accent bg-black/20 px-1 rounded">/명령어</code>
          {" "}를 입력해도 볼 수 있습니다.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4">
        {CATEGORIES.map(({ label, color, cmds }) => (
          <div key={label} className="card space-y-3">
            <h2 className="font-semibold text-white flex items-center gap-2">
              <span
                className="w-2.5 h-2.5 rounded-full inline-block shrink-0"
                style={{ background: color }}
              />
              {label}
            </h2>
            <div className="divide-y divide-border/50">
              {cmds.map(({ name, desc, admin }) => (
                <div key={name} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
                  <code className="text-base font-mono font-semibold px-2 py-0.5 rounded shrink-0
                                   text-fg bg-bg-hover">
                    {name}
                  </code>
                  <span className="text-base text-muted flex-1">{desc}</span>
                  {admin && (
                    <span className="text-xs font-medium px-1.5 py-0.5 rounded shrink-0
                                     bg-danger/10 text-danger border border-danger/20">
                      관리자
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
