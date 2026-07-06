"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { MonitorPlay, Copy, Check, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";

export default function OverlayPage() {
  const { guildId } = useParams<{ guildId: string }>();
  const [overlayUrl, setOverlayUrl]     = useState<string | null>(null);
  const [loading, setLoading]           = useState(true);
  const [error, setError]               = useState("");
  const [copied, setCopied]             = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const load = () =>
    api.chzzk.overlay.getToken(guildId)
      .then((d) => { setOverlayUrl(d.overlay_url); setError(""); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "불러오기 실패"))
      .finally(() => setLoading(false));

  useEffect(() => { load(); }, [guildId]);

  const copy = async () => {
    if (!overlayUrl) return;
    await navigator.clipboard.writeText(overlayUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const regenerate = async () => {
    if (!confirm("오버레이 URL을 재발급하시겠습니까? 기존에 OBS에 등록해둔 URL은 더 이상 동작하지 않습니다.")) return;
    setRegenerating(true);
    try {
      const d = await api.chzzk.overlay.regenerateToken(guildId);
      setOverlayUrl(d.overlay_url);
    } catch {}
    setRegenerating(false);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="page-title flex items-center gap-2">
          <MonitorPlay size={20} className="text-accent" /> 오버레이
        </h1>
        <p className="page-subtitle">
          OBS 브라우저 소스로 추가하면 치지직 채팅 도박(!도박) 진행 상황을 방송 화면에 실시간으로 보여줍니다.
        </p>
      </div>

      <div className="card space-y-4">
        <h2 className="section-title">도박 현황 오버레이</h2>

        {loading ? (
          <p className="text-muted text-sm">불러오는 중...</p>
        ) : error ? (
          <p className="text-sm text-danger">
            {error} — 먼저 방송 카테고리 &gt; 치지직 탭에서 스트리머 계정을 연동해주세요.
          </p>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <input
                readOnly
                className="input font-mono text-xs flex-1"
                value={overlayUrl || ""}
                onClick={(e) => (e.target as HTMLInputElement).select()}
              />
              <button onClick={copy} className="btn-secondary text-sm flex items-center gap-1.5 shrink-0">
                {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? "복사됨" : "복사"}
              </button>
            </div>

            <div className="rounded-lg bg-bg border border-border p-4 text-sm text-muted space-y-1.5">
              <p>1. OBS에서 &quot;소스 추가 → 브라우저&quot;를 선택하고 위 URL을 붙여넣으세요.</p>
              <p>2. 너비 500 / 높이 300 정도를 권장하며, 배경은 투명하게 표시됩니다.</p>
              <p>3. 치지직 채팅에서 스트리머(또는 도박 관리 권한이 있는 매니저)가 !도박을 입력하면 오버레이에 옵션과 실시간 득표수가 표시됩니다.</p>
              <p>4. 이 URL은 외부에 공개하지 마세요 — 아는 사람은 누구나 오버레이 화면을 볼 수 있습니다.</p>
            </div>

            <button
              onClick={regenerate}
              disabled={regenerating}
              className="btn-secondary text-sm flex items-center gap-1.5"
            >
              <RefreshCw size={14} className={regenerating ? "animate-spin" : ""} />
              {regenerating ? "재발급 중..." : "URL 재발급"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
