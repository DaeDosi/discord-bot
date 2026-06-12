"use client";
import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

interface FaqItem {
  q: string;
  a: string | React.ReactNode;
  tags?: string[];
}

const FAQS: FaqItem[] = [
  {
    q: "역할이 부여되지 않아요.",
    tags: ["역할", "인증"],
    a: (
      <div className="space-y-2 text-sm text-muted">
        <p>봇 역할이 부여하려는 역할보다 <span className="text-white font-medium">낮은 위치</span>에 있으면 Discord가 역할 변경을 거부합니다.</p>
        <p className="font-medium text-white">해결 방법:</p>
        <ol className="list-decimal list-inside space-y-1 pl-1">
          <li>Discord 서버 설정 → <span className="text-accent">역할</span> 탭 이동</li>
          <li>봇 역할(예: NexBot)을 <span className="text-accent">미인증 역할</span>과 <span className="text-accent">인증됨 역할</span>보다 위로 드래그</li>
          <li>저장 후 다시 인증 시도</li>
        </ol>
      </div>
    ),
  },
  {
    q: "봇에 역할 관리 권한이 없다고 뜨는데요.",
    tags: ["역할", "권한"],
    a: (
      <div className="space-y-2 text-sm text-muted">
        <p>봇 역할에 <span className="text-white font-medium">역할 관리</span> 권한이 없는 경우입니다.</p>
        <p className="font-medium text-white">해결 방법:</p>
        <ol className="list-decimal list-inside space-y-1 pl-1">
          <li>Discord 서버 설정 → 역할 탭</li>
          <li>봇 역할 클릭 → 권한 탭</li>
          <li><span className="text-accent">역할 관리</span> 권한 활성화 → 저장</li>
        </ol>
      </div>
    ),
  },
  {
    q: "/입장메시지설정 명령어 실행 시 '설정 없음'이 뜨는데요.",
    tags: ["명령어", "설정"],
    a: (
      <div className="space-y-2 text-sm text-muted">
        <p>웹 대시보드에서 입장 채널이 설정되지 않은 경우입니다.</p>
        <p className="font-medium text-white">해결 방법:</p>
        <ol className="list-decimal list-inside space-y-1 pl-1">
          <li>웹 대시보드 → <span className="text-accent">입장 인증</span> 페이지</li>
          <li>입장 인증 채널 선택</li>
          <li>미인증 역할 / 인증됨 역할 선택</li>
          <li><span className="text-accent">변경사항 저장</span> 클릭</li>
          <li>Discord에서 <code className="text-accent bg-black/30 px-1 rounded">/입장메시지설정</code> 다시 실행</li>
        </ol>
      </div>
    ),
  },
  {
    q: "치지직 인증 후 '서버 설정이 없습니다'라고 뜨는데요.",
    tags: ["치지직", "인증", "설정"],
    a: (
      <div className="space-y-2 text-sm text-muted">
        <p>서버 배포 후 데이터베이스가 초기화되어 설정이 사라진 경우입니다.</p>
        <p className="font-medium text-white">해결 방법:</p>
        <ol className="list-decimal list-inside space-y-1 pl-1">
          <li>웹 대시보드 → <span className="text-accent">입장 인증</span> 페이지에서 채널·역할을 다시 선택</li>
          <li><span className="text-accent">변경사항 저장</span> 클릭 후 다시 인증 시도</li>
        </ol>
        <p className="text-xs text-muted/60 mt-2">Railway 무료 플랜은 재배포 시 파일시스템이 초기화됩니다. Volume을 연결하면 데이터가 유지됩니다.</p>
      </div>
    ),
  },
  {
    q: "네이버 로그인은 됐는데 역할이 안 받아져요.",
    tags: ["치지직", "역할", "인증"],
    a: (
      <div className="space-y-2 text-sm text-muted">
        <p>아래 순서로 확인해주세요.</p>
        <ol className="list-decimal list-inside space-y-1 pl-1">
          <li><span className="text-white font-medium">역할 위치 확인</span> — 봇 역할이 인증 역할보다 위에 있어야 합니다</li>
          <li><span className="text-white font-medium">역할 관리 권한 확인</span> — 봇 역할에 '역할 관리' 권한이 있어야 합니다</li>
          <li><span className="text-white font-medium">역할 설정 확인</span> — 대시보드 입장 인증 페이지에서 '인증됨 역할'이 선택되어 있어야 합니다</li>
          <li><span className="text-white font-medium">봇 상태 확인</span> — 봇이 온라인 상태인지 확인해주세요</li>
        </ol>
      </div>
    ),
  },
  {
    q: "메인 페이지 등록 서버 수가 실제와 다르게 표시돼요.",
    tags: ["통계"],
    a: (
      <div className="space-y-2 text-sm text-muted">
        <p>봇이 30분마다 서버 수를 DB에 기록합니다. 봇 재시작 직후에는 최신 수치가 아닐 수 있습니다.</p>
        <p className="text-xs text-muted/60">봇을 재시작하면 약 1분 이내에 최신 서버 수로 업데이트됩니다.</p>
      </div>
    ),
  },
  {
    q: "Discord 로그인 후 인증 페이지가 아닌 대시보드로 이동돼요.",
    tags: ["로그인", "인증"],
    a: (
      <div className="space-y-2 text-sm text-muted">
        <p>Discord 인증 링크를 클릭한 후 로그인 페이지에서 로그인하면 대시보드로 이동할 수 있습니다.</p>
        <p className="font-medium text-white">올바른 순서:</p>
        <ol className="list-decimal list-inside space-y-1 pl-1">
          <li>Discord 서버의 <span className="text-accent">인증하기</span> 버튼 클릭</li>
          <li>열린 페이지에서 <span className="text-accent">Discord로 로그인</span> 클릭</li>
          <li>Discord 로그인 완료 → 자동으로 인증 페이지로 복귀</li>
          <li><span className="text-accent">치지직(네이버)으로 인증하기</span> 클릭</li>
        </ol>
      </div>
    ),
  },
  {
    q: "봇이 오프라인 상태인데 명령어가 작동하지 않아요.",
    tags: ["봇", "명령어"],
    a: (
      <div className="space-y-2 text-sm text-muted">
        <p>봇이 오프라인이면 슬래시 명령어가 동작하지 않습니다.</p>
        <p className="font-medium text-white">해결 방법:</p>
        <ol className="list-decimal list-inside space-y-1 pl-1">
          <li>Railway 대시보드에서 서비스 상태 확인</li>
          <li>배포 로그에서 오류 확인</li>
          <li>환경변수(특히 <code className="text-accent bg-black/30 px-1 rounded">DISCORD_TOKEN</code>)가 올바른지 확인</li>
        </ol>
      </div>
    ),
  },
];

function FaqCard({ item }: { item: FaqItem }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="card p-0 overflow-hidden">
      <button
        className="w-full flex items-center justify-between px-5 py-4 text-left
                   hover:bg-bg-hover transition-colors"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="text-sm font-medium text-white pr-4">{item.q}</span>
        {open
          ? <ChevronUp size={16} className="text-muted shrink-0" />
          : <ChevronDown size={16} className="text-muted shrink-0" />}
      </button>
      {open && (
        <div className="px-5 pb-5 border-t border-border pt-4">
          {item.a}
        </div>
      )}
    </div>
  );
}

export default function HelpPage() {
  const [filter, setFilter] = useState("전체");

  const allTags = ["전체", ...Array.from(new Set(FAQS.flatMap((f) => f.tags ?? [])))];
  const filtered = filter === "전체" ? FAQS : FAQS.filter((f) => f.tags?.includes(filter));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-white">문제 해결 가이드</h1>
        <p className="text-muted text-sm mt-1">자주 발생하는 문제와 해결 방법을 확인하세요.</p>
      </div>

      {/* 태그 필터 */}
      <div className="flex flex-wrap gap-2">
        {allTags.map((tag) => (
          <button
            key={tag}
            onClick={() => setFilter(tag)}
            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              filter === tag
                ? "bg-accent text-white"
                : "bg-bg-card border border-border text-muted hover:text-white hover:border-accent/50"
            }`}
          >
            {tag}
          </button>
        ))}
      </div>

      {/* FAQ 목록 */}
      <div className="space-y-3">
        {filtered.map((item, i) => (
          <FaqCard key={i} item={item} />
        ))}
      </div>

      <p className="text-xs text-muted/60 text-center pt-2">
        해결되지 않는 문제는 관리자에게 문의해주세요.
      </p>
    </div>
  );
}
