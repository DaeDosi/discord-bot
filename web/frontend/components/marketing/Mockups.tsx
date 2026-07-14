import { Hash, Radio, BadgeCheck, TrendingUp, Users, ExternalLink } from "lucide-react";

// 홈페이지 기능 소개 섹션과 /guide 페이지의 기능별 활용법 섹션이 공유하는 목업 컴포넌트들.
// 실제 스크린샷 대신 손으로 그린 미리보기를 쓰는 이유: 스크린샷은 UI가 바뀔 때마다
// 다시 찍어야 하지만, 이 컴포넌트들은 실제 컴포넌트 스타일(카드/컬러 토큰)을 그대로
// 따라가서 유지보수가 쉽고, 다크/라이트 테마도 자동으로 맞는다.

// Mockup: terminal (서버 관리)
export function TerminalMockup() {
  const cmds = [
    { cmd: "/warn",  args: "@유저 욕설 사용",  label: "경고", labelColor: "#FEE75C" },
    { cmd: "/mute",  args: "@유저 30m 도배",   label: "뮤트", labelColor: "#EB459E" },
    { cmd: "/clear", args: "100",              label: "완료", labelColor: "#57F287" },
    { cmd: "/ban",   args: "@유저 반복 위반",   label: "차단", labelColor: "#ED4245" },
  ];
  return (
    <div className="bg-[#0b0d14] rounded-2xl border border-white/8 p-5
                    shadow-2xl shadow-black/60 w-full max-w-sm font-mono text-sm">
      <div className="flex items-center gap-2 mb-5">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-red-500/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-yellow-400/60" />
          <div className="w-2.5 h-2.5 rounded-full bg-green-500/60" />
        </div>
        <span className="text-[11px] text-white/20 ml-2">NexBot — 서버 관리</span>
      </div>
      <div className="space-y-2.5">
        {cmds.map(({ cmd, args, label, labelColor }) => (
          <div key={cmd + args} className="flex items-center gap-2">
            <span className="text-[#5865f2]/40 text-xs select-none">$</span>
            <span className="text-[#818cf8]">{cmd}</span>
            <span className="text-white/40">{args}</span>
            <span className="ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded"
                  style={{ color: labelColor, background: `${labelColor}18` }}>
              {label}
            </span>
          </div>
        ))}
        <div className="flex items-center gap-2 opacity-30 pt-1">
          <span className="text-[#5865f2]/40 text-xs">$</span>
          <span className="inline-block w-1.5 h-4 bg-[#5865f2]/60 animate-pulse" />
        </div>
      </div>
    </div>
  );
}

// Mockup: points shop (포인트 & 상점)
export function PointsMockup({ color = "#A855F7" }: { color?: string }) {
  const items = [
    { icon: "🎮", name: "게임 아이템",    points: 500,  stock: 3   },
    { icon: "🎫", name: "특별 역할 쿠폰", points: 1000, stock: "∞" },
    { icon: "✨", name: "닉네임 꾸미기",  points: 300,  stock: 10  },
  ];
  return (
    <div className="bg-bg-card rounded-2xl border border-border p-5 shadow-xl w-full max-w-sm space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs font-bold text-muted uppercase tracking-widest">포인트 상점</p>
        <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
              style={{ color, background: `${color}18` }}>
          1,250P 보유
        </span>
      </div>
      <div className="space-y-3">
        {items.map(({ icon, name, points, stock }) => (
          <div key={name} className="flex items-center gap-3 p-3 rounded-xl border border-border bg-bg">
            <span className="text-2xl leading-none">{icon}</span>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-fg truncate">{name}</p>
              <p className="text-[11px] text-muted">잔여 {stock}개</p>
            </div>
            <button
              className="text-[11px] font-semibold px-2.5 py-1 rounded-lg shrink-0 cursor-default"
              style={{ background: `${color}18`, color }}
            >
              {typeof points === "number" ? points.toLocaleString() : points}P
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// Mockup: Discord embed (치지직 방송 알림)
export function ChzzkEmbedMockup() {
  return (
    <div className="rounded-2xl overflow-hidden border border-white/8 shadow-2xl shadow-black/60 w-full max-w-sm"
         style={{ background: "#313338" }}>
      <div className="flex items-center gap-2 px-4 py-2.5" style={{ background: "#2b2d31" }}>
        <Hash size={11} style={{ color: "#80848e" }} />
        <span className="text-[11px]" style={{ color: "#80848e" }}>알림-채널</span>
        <span className="ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded"
              style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>
          LIVE
        </span>
      </div>
      <div className="p-4">
        <div className="flex gap-3">
          <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center"
               style={{ background: "rgba(3,199,90,0.15)" }}>
            <Radio size={16} style={{ color: "#03C75A" }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold text-white">NexBot</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold text-white"
                    style={{ background: "#5865f2" }}>BOT</span>
            </div>
            <div className="rounded-lg border-l-[3px] p-3"
                 style={{ background: "#2b2d31", borderLeftColor: "#03c75a" }}>
              <p className="text-white text-[13px] font-semibold mb-1 leading-snug">
                오늘의 하이라이트 게임 방송!
              </p>
              <p className="text-[12px] mb-3" style={{ color: "#b5bac1" }}>
                스트리머님이 방송을 시작했습니다.
              </p>
              <div className="rounded-md h-14 flex items-center justify-center gap-2"
                   style={{ background: "#1e1f22" }}>
                <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "#03C75A" }} />
                <span className="text-[11px] font-semibold" style={{ color: "#03C75A" }}>방송 중</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Mockup: chzzk follow verification (치지직 팔로우 인증)
export function ChzzkFollowVerifyMockup({ color = "#03C75A" }: { color?: string }) {
  const tiers = [
    { months: 1,  role: "팔로워",       granted: true },
    { months: 3,  role: "서포터",        granted: true },
    { months: 6,  role: "충성팬",        granted: false },
    { months: 12, role: "레전드 팬",     granted: false },
  ];
  return (
    <div className="bg-bg-card rounded-2xl border border-border p-5 shadow-xl w-full max-w-sm space-y-4">
      <div className="flex items-center gap-2.5 pb-3 border-b border-border">
        <div className="w-8 h-8 rounded-full flex items-center justify-center"
             style={{ background: `${color}20` }}>
          <BadgeCheck size={16} style={{ color }} />
        </div>
        <div>
          <p className="text-xs font-bold text-fg">치지직 팔로우 인증</p>
          <p className="text-[11px] text-muted">팔로우 기간: <span style={{ color }} className="font-semibold">4개월</span></p>
        </div>
      </div>
      <div className="space-y-2">
        {tiers.map(({ months, role, granted }) => (
          <div key={months}
               className="flex items-center gap-3 px-3 py-2 rounded-xl border"
               style={granted ? { background: `${color}10`, borderColor: `${color}30` } : { borderColor: "var(--color-border)" }}>
            <span className="text-[11px] font-semibold w-14 shrink-0"
                  style={granted ? { color } : { color: "var(--color-muted)" }}>
              {months}개월+
            </span>
            <span className="text-sm text-fg flex-1">@{role}</span>
            <div className="w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0"
                 style={granted ? { background: color, borderColor: color } : { borderColor: "var(--color-border)" }}>
              {granted && <span className="text-white text-[9px] font-bold leading-none">✓</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Mockup: 애정도 레벨업 알림
export function LevelingMockup({ color = "#818CF8" }: { color?: string }) {
  return (
    <div className="rounded-2xl overflow-hidden border border-white/8 shadow-2xl shadow-black/60 w-full max-w-sm"
         style={{ background: "#313338" }}>
      <div className="flex items-center gap-2 px-4 py-2.5" style={{ background: "#2b2d31" }}>
        <Hash size={11} style={{ color: "#80848e" }} />
        <span className="text-[11px]" style={{ color: "#80848e" }}>레벨업-알림</span>
      </div>
      <div className="p-4 flex items-center gap-3">
        <div className="w-9 h-9 rounded-full flex-shrink-0 flex items-center justify-center"
             style={{ background: `${color}25` }}>
          <TrendingUp size={16} style={{ color }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-white text-[13px] font-semibold mb-1">🎉 레벨업!</p>
          <p className="text-[12px] mb-3" style={{ color: "#b5bac1" }}>
            <span style={{ color }} className="font-semibold">유저123</span>님이 <b>Lv.14</b>가 되었습니다!
          </p>
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold px-2 py-1 rounded"
                  style={{ color, background: `${color}20` }}>
              @충성팬 역할 획득
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// Mockup: 커뮤니티 홍보 카드
export function CommunityMockup({ color = "#38BDF8" }: { color?: string }) {
  return (
    <div className="bg-bg-card rounded-2xl border border-border p-5 shadow-xl w-full max-w-sm space-y-4">
      <div className="flex items-center gap-3">
        <div className="w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0"
             style={{ background: `${color}20` }}>
          <Users size={18} style={{ color }} />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-fg truncate">우리 팬카페 서버</p>
          <p className="flex items-center gap-1.5 text-[11px] text-muted truncate">
            <Radio size={11} style={{ color: "#03C75A" }} />
            치지직 스트리머 채널
            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded"
                  style={{ color: "#03C75A", background: "rgba(3,199,90,0.15)" }}>
              LIVE
            </span>
          </p>
        </div>
      </div>
      <p className="text-[12px] text-muted leading-relaxed">
        매일 저녁 게임 방송해요! 편하게 놀러오세요 :)
      </p>
      <div className="flex items-center justify-center gap-1.5 text-[12px] font-medium px-3 py-2 rounded-lg border"
           style={{ borderColor: "var(--color-border)", color }}>
        치지직 바로가기 <ExternalLink size={12} />
      </div>
    </div>
  );
}
