import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "cdn.discordapp.com" },
      { protocol: "https", hostname: "media.discordapp.net" },
      { protocol: "https", hostname: "ssl.pstatic.net" },
      { protocol: "https", hostname: "nng.pstatic.net" },
      { protocol: "https", hostname: "nng-phinf.pstatic.net" },
      { protocol: "https", hostname: "*.pstatic.net" },
    ],
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
    NEXT_PUBLIC_BOT_INVITE: process.env.NEXT_PUBLIC_BOT_INVITE || "",
  },
};

export default nextConfig;
