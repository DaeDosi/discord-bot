import { relayCollector } from "@/lib/collectorRelay";

// 수집 실패를 **실패로** 남기는 경로. 토큰이 필요 없고, 토큰을 넘기지도 않는다.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function POST(request: Request) {
  return relayCollector(request, "failure");
}
