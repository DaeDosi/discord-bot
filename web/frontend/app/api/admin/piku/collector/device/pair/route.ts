import { relayCollector } from "@/lib/collectorRelay";

// 장치 인증 relay. 계약(허용 경로·헤더·성공 필드 목록)은 `lib/collectorRelay.ts`.
// **POST만 내보낸다** — 다른 메서드는 라우트 자체를 만들지 않는다.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request) {
  return relayCollector(request, "device/pair");
}
