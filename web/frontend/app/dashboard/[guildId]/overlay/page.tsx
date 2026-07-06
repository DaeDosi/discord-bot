"use client";
import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { MonitorPlay, Copy, Check, RefreshCw, Dices, Target } from "lucide-react";
import { api, BASE } from "@/lib/api";
import { GamblingOverlayView, type OverlayStatus } from "@/components/GamblingOverlayView";
import { MissionsOverlayView, type MissionsOverlayStatus } from "@/components/MissionsOverlayView";

function UrlRow({ url, onCopy, copied }: { url: string; onCopy: () => void; copied: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <input
        readOnly
        className="input font-mono text-xs flex-1"
        value={url}
        onClick={(e) => (e.target as HTMLInputElement).select()}
      />
      <button onClick={onCopy} className="btn-secondary text-sm flex items-center gap-1.5 shrink-0">
        {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "복사됨" : "복사"}
      </button>
    </div>
  );
}

export default function OverlayPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [token, setToken]           = useState<string | null>(null);
  const [gamblingUrl, setGamblingUrl] = useState<string | null>(null);
  const [missionsUrl, setMissionsUrl] = useState<string | null>(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState("");
  const [copiedWhich, setCopiedWhich] = useState<"gambling" | "missions" | null>(null);
  const [regenerating, setRegenerating] = useState(false);

  const [gamblingStatus, setGamblingStatus] = useState<OverlayStatus | null>(null);
  const [gamblingError, setGamblingError]   = useState<string | null>(null);
  const [missionsStatus, setMissionsStatus] = useState<MissionsOverlayStatus | null>(null);
  const [missionsError, setMissionsError]   = useState<string | null>(null);
  const timers = useRef<ReturnType<typeof setInterval>[]>([]);

  const load = () =>
    api.chzzk.overlay.getToken(guildId)
      .then((d) => { setGamblingUrl(d.gambling_overlay_url); setMissionsUrl(d.missions_overlay_url); setToken(d.token); setError(""); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "불러오기 실패"))
      .finally(() => setLoading(false));

  useEffect(() => { load(); }, [guildId]);

  useEffect(() => {
    timers.current.forEach(clearInterval);
    timers.current = [];
    if (!token) return;

    const pollGambling = () => {
      fetch(`${BASE}/api/chzzk/overlay/${token}/gambling-status`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("서버 연결 오류"))))
        .then((d) => { setGamblingStatus(d); setGamblingError(null); })
        .catch((e: unknown) => setGamblingError(e instanceof Error ? e.message : "서버 연결 오류"));
    };
    const pollMissions = () => {
      fetch(`${BASE}/api/chzzk/overlay/${token}/missions-status`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("서버 연결 오류"))))
        .then((d) => { setMissionsStatus(d); setMissionsError(null); })
        .catch((e: unknown) => setMissionsError(e instanceof Error ? e.message : "서버 연결 오류"));
    };
    pollGambling();
    pollMissions();
    timers.current = [setInterval(pollGambling, 2000), setInterval(pollMissions, 5000)];
    return () => timers.current.forEach(clearInterval);
  }, [token]);

  const copy = async (which: "gambling" | "missions", url: string | null) => {
    if (!url) return;
    await navigator.clipboard.writeText(url);
    setCopiedWhich(which);
    setTimeout(() => setCopiedWhich(null), 2000);
  };

  const regenerate = async () => {
    if (!confirm("오버레이 URL을 재발급하시겠습니까? 기존에 OBS에 등록해둔 URL(도박·미션 모두)은 더 이상 동작하지 않습니다.")) return;
    setRegenerating(true);
    try {
      const d = await api.chzzk.overlay.regenerateToken(guildId);
      setGamblingUrl(d.gambling_overlay_url);
      setMissionsUrl(d.missions_overlay_url);
      setToken(d.token);
    } catch {}
    setRegenerating(false);
  };

  const previewBg = {
    background: "repeating-conic-gradient(#2a2d38 0% 25%, #1c1e26 0% 50%) 50% / 24px 24px",
    minHeight: 160,
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title flex items-center gap-2">
          <MonitorPlay size={20} className="text-accent" /> 오버레이
        </h1>
        <p className="page-subtitle">
          OBS 브라우저 소스로 추가하면 치지직 채팅 도박과 등록된 미션 현황을 방송 화면에 실시간으로 보여줍니다.
        </p>
      </div>

      {loading ? (
        <div className="card text-center py-10 text-muted text-sm">불러오는 중...</div>
      ) : error ? (
        <div className="card">
          <p className="text-sm text-danger">
            {error} — 먼저 방송 카테고리 &gt; 치지직 탭에서 스트리머 계정을 연동해주세요.
          </p>
        </div>
      ) : (
        <>
          {/* 도박 현황 오버레이 */}
          <div className="card space-y-4">
            <h2 className="section-title flex items-center gap-2">
              <Dices size={16} className="text-accent" /> 도박 현황 오버레이
            </h2>
            <UrlRow url={gamblingUrl || ""} onCopy={() => copy("gambling", gamblingUrl)} copied={copiedWhich === "gambling"} />
            <div className="rounded-lg bg-bg border border-border p-4 text-sm text-muted space-y-1.5">
              <p>1. OBS에서 &quot;소스 추가 → 브라우저&quot;를 선택하고 위 URL을 붙여넣으세요. (너비 500 / 높이 300 권장)</p>
              <p>2. 치지직 채팅에서 스트리머(또는 서버 관리 &gt; 관리 탭에 등록된 매니저)가 !도박을 입력하면 옵션과 실시간 득표수가 표시됩니다.</p>
            </div>
            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">미리보기</p>
              <div className="rounded-lg p-6 flex items-start justify-center" style={previewBg}>
                <GamblingOverlayView status={gamblingStatus} connError={gamblingError} />
              </div>
            </div>
          </div>

          {/* 미션 현황 오버레이 */}
          <div className="card space-y-4">
            <h2 className="section-title flex items-center gap-2">
              <Target size={16} className="text-accent" /> 미션 현황 오버레이
            </h2>
            <p className="text-sm text-muted">
              서버 관리 &gt; 포인트 &gt; 미션 관리 탭에서 등록한 활성 미션을 도네이션 미션처럼 목록으로 보여줍니다.
            </p>
            <UrlRow url={missionsUrl || ""} onCopy={() => copy("missions", missionsUrl)} copied={copiedWhich === "missions"} />
            <div>
              <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">미리보기</p>
              <div className="rounded-lg p-6 flex items-start justify-center" style={previewBg}>
                <MissionsOverlayView status={missionsStatus} connError={missionsError} />
              </div>
            </div>
          </div>

          <div className="card space-y-3">
            <p className="text-sm text-warning">
              위 URL들은 외부에 공개하지 마세요 — 아는 사람은 누구나 오버레이 화면을 볼 수 있습니다.
            </p>
            <button
              onClick={regenerate}
              disabled={regenerating}
              className="btn-secondary text-sm flex items-center gap-1.5"
            >
              <RefreshCw size={14} className={regenerating ? "animate-spin" : ""} />
              {regenerating ? "재발급 중..." : "URL 재발급 (도박 · 미션 모두 갱신)"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
