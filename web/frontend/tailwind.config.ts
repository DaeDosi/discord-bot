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
      // 320px 미만은 없다시피 하지만, 390px을 150% 확대하면 **실폭 260px**이 된다.
      // 그 구간에서만 헤더 라벨을 접어야 해서 기본 브레이크포인트(sm=640)로는
      // 표현할 수 없는 단계가 하나 필요했다.
      screens: { xs: "380px" },
      colors: {
        // CSS variable colors — RGB channel format supports /opacity modifiers
        bg: {
          DEFAULT: "rgb(var(--color-bg-rgb) / <alpha-value>)",
          card:    "rgb(var(--color-bg-card-rgb) / <alpha-value>)",
          hover:   "rgb(var(--color-bg-hover-rgb) / <alpha-value>)",
        },
        border: "rgb(var(--color-border-rgb) / <alpha-value>)",
        muted:  "rgb(var(--color-muted-rgb) / <alpha-value>)",
        fg:     "rgb(var(--color-fg-rgb) / <alpha-value>)",
        // Hardcoded brand colors (opacity already works)
        accent:  { DEFAULT: "#5865F2", hover: "#4752C4", light: "#7289DA" },
        // 통계 화면의 네온 그린(#00FFA3). `accent`는 **Discord 블루**,
        // `chzzk`는 이미 **네이버 그린(#03C75A)**으로 잡혀 있어 둘 다 쓸 수 없다.
        // 이 저장소가 그래프·글로우에서 리터럴로 반복하던 값이라 이름을 붙인다.
        neon:    "#00FFA3",
        success: "#57F287",
        warning: "#FEE75C",
        danger:  "#ED4245",
        chzzk:   "#03C75A",
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
      },
      animation: {
        "fade-up": "fadeUp 0.6s ease forwards",
        "fade-in": "fadeIn 0.4s ease forwards",
        "float":   "float 3s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
