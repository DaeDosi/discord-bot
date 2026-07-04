"use client";
import { useParams } from "next/navigation";
import Sidebar from "@/components/Sidebar";

export default function GuildLayout({ children }: { children: React.ReactNode }) {
  const { guildId } = useParams<{ guildId: string }>();
  return (
    <div className="max-w-6xl mx-auto px-4 py-8 md:py-10 md:flex md:gap-8">
      <Sidebar guildId={guildId} />
      <div className="flex-1 min-w-0 pb-20 md:pb-0">{children}</div>
    </div>
  );
}
