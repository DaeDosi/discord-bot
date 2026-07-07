"use client";
import { useParams } from "next/navigation";
import Sidebar from "@/components/Sidebar";

export default function GuildLayout({ children }: { children: React.ReactNode }) {
  const { guildId } = useParams<{ guildId: string }>();
  return (
    <div className="w-full px-4 md:px-8 py-8 md:py-10 md:flex md:gap-8">
      <Sidebar guildId={guildId} />
      <div className="flex-1 min-w-0 pb-20 md:pb-0">{children}</div>
    </div>
  );
}
