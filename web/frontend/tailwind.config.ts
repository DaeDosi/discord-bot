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
