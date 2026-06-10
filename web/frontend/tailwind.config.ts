import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg:      { DEFAULT: "#0f1117", card: "#1a1d27", hover: "#1e2232" },
        accent:  { DEFAULT: "#5865F2", hover: "#4752C4", light: "#7289DA" },
        border:  "#2d3048",
        success: "#57F287",
        warning: "#FEE75C",
        danger:  "#ED4245",
        chzzk:   "#03C75A",
        muted:   "#8b8fa8",
      },
      fontFamily: {
        sans: ["Pretendard", "Inter", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
