import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg:      { DEFAULT: "var(--color-bg)", card: "var(--color-bg-card)", hover: "var(--color-bg-hover)" },
        accent:  { DEFAULT: "#5865F2", hover: "#4752C4", light: "#7289DA" },
        border:  "var(--color-border)",
        success: "#57F287",
        warning: "#FEE75C",
        danger:  "#ED4245",
        chzzk:   "#03C75A",
        muted:   "var(--color-muted)",
        fg:      "var(--color-fg)",
      },
      fontFamily: {
        sans: ["Pretendard", "Inter", "sans-serif"],
      },
      keyframes: {
        fadeUp: {
          "0%":   { opacity: "0", transform: "translateY(24px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        fadeIn: {
          "0%":   { opacity: "0" },
          "100%": { opacity: "1" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0px)" },
          "50%":      { transform: "translateY(-8px)" },
        },
        shimmer: {
          "0%":   { backgroundPosition: "-200% center" },
          "100%": { backgroundPosition: "200% center" },
        },
      },
      animation: {
        "fade-up":   "fadeUp 0.6s ease forwards",
        "fade-in":   "fadeIn 0.4s ease forwards",
        "float":     "float 3s ease-in-out infinite",
        "shimmer":   "shimmer 2.5s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
