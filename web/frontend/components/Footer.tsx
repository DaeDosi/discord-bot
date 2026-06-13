import Link from "next/link";
import { Bot, Github } from "lucide-react";

function DiscordIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057.1 18.12.11 18.18.12 18.24c2.063 1.514 4.062 2.437 6.027 3.048a.077.077 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.07 13.07 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.048.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03z" />
    </svg>
  );
}

const COLUMNS: {
  heading: string;
  links: { label: string; href: string; external?: boolean }[];
}[] = [
  {
    heading: "제품",
    links: [
      { label: "기능", href: "/#features" },
      { label: "통계", href: "/#stats" },
      { label: "사용 방법", href: "/guide" },
    ],
  },
  {
    heading: "법적 고지",
    links: [
      { label: "이용약관", href: "/terms" },
      { label: "개인정보처리방침", href: "/privacy" },
      { label: "쿠키 정책", href: "#" },
    ],
  },
  {
    heading: "지원",
    links: [
      { label: "FAQ", href: "/faq" },
    ],
  },
];

export default function Footer() {
  return (
    <footer className="border-t border-border">
      <div className="max-w-6xl mx-auto px-5 pt-12 pb-8">
        {/* 4-column grid */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-8 mb-10">
          {/* Brand */}
          <div className="col-span-2 sm:col-span-1">
            <Link
              href="/"
              className="flex items-center gap-2 font-bold text-[15px] text-fg mb-3"
            >
              <Bot size={17} className="text-accent" />
              NexBot
            </Link>
            <p className="text-sm text-muted leading-relaxed mb-5">
              치지직 알림, 레벨링, 서버 관리를<br />하나의 봇으로.
            </p>
            <div className="flex items-center gap-2">
              <a
                href="https://github.com/DaeDosi/discord-bot"
                target="_blank"
                rel="noreferrer"
                aria-label="GitHub"
                className="w-8 h-8 rounded-lg border border-border flex items-center justify-center
                           text-muted hover:text-fg hover:border-accent/40 transition-colors"
              >
                <Github size={14} />
              </a>
              <a
                href="#"
                aria-label="Discord 서포트 서버"
                className="w-8 h-8 rounded-lg border border-border flex items-center justify-center
                           text-muted hover:text-fg hover:border-accent/40 transition-colors"
              >
                <DiscordIcon size={14} />
              </a>
            </div>
          </div>

          {/* Link columns */}
          {COLUMNS.map(({ heading, links }) => (
            <div key={heading}>
              <p className="text-[11px] font-bold text-muted/60 uppercase tracking-widest mb-4">
                {heading}
              </p>
              <ul className="space-y-2.5">
                {links.map(({ label, href, external }) => (
                  <li key={label}>
                    {href === "#" ? (
                      <span className="text-sm text-muted/40 cursor-default">{label}</span>
                    ) : href.startsWith("http") || external ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sm text-muted/80 hover:text-fg transition-colors"
                      >
                        {label}
                      </a>
                    ) : (
                      <Link
                        href={href}
                        className="text-sm text-muted/80 hover:text-fg transition-colors"
                      >
                        {label}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        {/* Bottom bar */}
        <div className="pt-6 border-t border-border flex flex-col sm:flex-row items-center justify-between gap-3">
          <p className="text-xs text-muted/50">© 2026 NexBot. All rights reserved.</p>
          <p className="text-xs text-muted/40">
            Built with{" "}
            <span style={{ color: "#D97706" }}>♥</span>{" "}
            <span style={{ color: "#D97706" }}>Claude AI</span>
          </p>
        </div>
      </div>
    </footer>
  );
}
