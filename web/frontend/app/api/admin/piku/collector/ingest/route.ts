import { relayCollector } from "@/lib/collectorRelay";

// 브라우저 확장 → nexbot.shop → Railway 백엔드. 계약은 `lib/collectorRelay.ts`에 있다.
// **POST만 내보낸다** — 다른 메서드는 Next가 405로 막고, 이 파일도 만들지 않는다.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request) {
  return relayCollector(request, "ingest");
}
