"use client";
import { overlayPillStyle } from "@/components/overlayPillStyle";

export interface GamblingSnapshot {
  title: string;
  options: string[];
  bet_amount: number;
  votes: number[];
  started_at: number;
}
export interface GamblingResult extends GamblingSnapshot {
  winner_index: number | null;
  settled_at: number;
}
export interface OverlayStatus {
  active: boolean;
  result: GamblingResult | null;
  title?: string;
  options?: string[];
  bet_amount?: number;
  votes?: number[];
  started_at?: number;
}

// 실제 OBS 오버레이 페이지(app/overlay/gambling/[token])와 대시보드 설정 페이지의
// 미리보기가 똑같은 렌더링 로직을 공유한다 — 둘이 따로 그리면 미리보기만 보고 "된다"고
// 착각했다가 실제 OBS에서 다르게 보이는 괴리가 생기기 쉽다.
export function GamblingOverlayView({
  status, connError,
}: { status: OverlayStatus | null; connError?: string | null }) {
  const data: (GamblingSnapshot & { winner_index?: number | null }) | null =
    status?.active
      ? {
          title: status.title || "",
          options: status.options || [],
          bet_amount: status.bet_amount || 0,
          votes: status.votes || [],
          started_at: status.started_at || 0,
        }
      : status?.result || null;

  const totalVotes = data ? data.votes.reduce((a, b) => a + b, 0) : 0;

  if (connError) {
    return (
      <div style={overlayPillStyle("rgba(237,66,69,0.85)")}>⚠️ {connError}</div>
    );
  }

  if (!data) {
    return (
      <div style={overlayPillStyle("rgba(255,255,255,0.5)")}>🎰 도박 대기 중 — 치지직 채팅에 !도박을 입력하면 표시됩니다</div>
    );
  }

  return (
    <div
      style={{
        width: "100%",
        maxWidth: 460,
        background: "rgba(15, 17, 23, 0.88)",
        border: "1px solid rgba(255,255,255,0.12)",
        borderRadius: 16,
        padding: "18px 20px",
        color: "#fff",
        boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
        fontFamily: "'Pretendard','Inter',sans-serif",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ fontSize: 18, fontWeight: 800 }}>🎰 {data.title}</span>
        <span style={{ fontSize: 12, opacity: 0.7 }}>
          {status?.active ? "진행 중" : "종료"} · 베팅 {data.bet_amount.toLocaleString()}P
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {data.options.map((opt, i) => {
          const count = data.votes[i] || 0;
          const pct = totalVotes > 0 ? Math.round((count / totalVotes) * 100) : 0;
          const isWinner = !status?.active && data.winner_index === i;
          return (
            <div
              key={i}
              style={{ position: "relative", borderRadius: 10, overflow: "hidden", background: "rgba(255,255,255,0.06)" }}
            >
              <div
                style={{
                  position: "absolute", top: 0, left: 0, bottom: 0,
                  width: `${pct}%`,
                  background: isWinner ? "rgba(255,209,102,0.28)" : "rgba(88,101,242,0.28)",
                  transition: "width 0.4s ease",
                }}
              />
              <div
                style={{
                  position: "relative",
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "8px 12px",
                }}
              >
                <span style={{ fontWeight: 600, fontSize: 14 }}>
                  {isWinner && "🏆 "}{i + 1}. {opt}
                </span>
                <span style={{ fontSize: 13, opacity: 0.85 }}>{count}표 ({pct}%)</span>
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ marginTop: 10, fontSize: 11, opacity: 0.6, textAlign: "right" }}>
        총 {totalVotes}명 참여 · 치지직 채팅에 !투표 &lt;번호&gt;
      </div>
    </div>
  );
}
