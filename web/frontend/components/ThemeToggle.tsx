"use client";
import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export default function ThemeToggle() {
  const [isLight, setIsLight] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setIsLight(localStorage.getItem("theme") === "light");
  }, []);

  const toggle = () => {
    const next = isLight ? "dark" : "light";
    localStorage.setItem("theme", next);
    document.documentElement.classList.toggle("light", next === "light");
    setIsLight(next === "light");
  };

  if (!mounted) return <div className="w-8 h-8" />;

  return (
    <button
      onClick={toggle}
      /* nb-tap-icon: 터치 입력에서만 44×44px. 31×31px 정사각 아이콘은 손가락으로
         가장 놓치기 쉬운 형태다(데스크톱은 현행 유지). */
      className="nb-tap-icon inline-flex items-center justify-center p-2 rounded-lg hover:bg-bg-hover transition-colors text-muted hover:text-fg"
      title={isLight ? "다크 모드로 전환" : "라이트 모드로 전환"}
      aria-label={isLight ? "다크 모드로 전환" : "라이트 모드로 전환"}
    >
      {isLight ? <Moon size={16} /> : <Sun size={16} />}
    </button>
  );
}
