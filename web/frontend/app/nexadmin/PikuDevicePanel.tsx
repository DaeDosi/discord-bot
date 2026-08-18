"use client";
/**
 * AUTO-1 — 자동 수집 **장치 등록·폐기**와 **모드** 화면.
 *
 * 이 화면이 답해야 하는 질문은 셋이다.
 *   · 지금 자동화가 켜져 있나? (그리고 켜면 실제로 무엇이 도나?)
 *   · 어떤 PC가 등록돼 있고, 마지막으로 언제 살아 있었나?
 *   · 문제가 생기면 어디를 눌러 끊나?
 *
 * **AUTO-1 시점에는 스케줄러도 자동 공개도 아직 없다.** 모드를 자동으로 바꿔도
 * 저절로 도는 것은 없다. 그 사실을 숨기지 않고 화면에 그대로 적는다 — "켰는데
 * 아무 일도 안 일어난다"를 장애로 오해하는 것이 더 나쁘다.
 *
 * 표시 규칙: 상태를 **색만으로** 구분하지 않는다. 글리프(✔ ⚠ ✖ ·)와 한국어 문장을
 * 함께 둔다. 색각 이상 사용자와 흑백 출력에서도 같은 뜻이 전달돼야 한다.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  PikuCollectorMode, PikuDevice, PikuDevicesResponse,
} from "@/lib/types";

const MODE_LABEL: Record<PikuCollectorMode, string> = {
  MANUAL: "수동 (기본)",
  AUTO_COLLECT: "자동 수집",
  AUTO_PUBLISH: "자동 수집 + 자동 공개",
};

const MODE_DESC: Record<PikuCollectorMode, string> = {
  MANUAL: "지금과 같습니다. 자동 수집·자동 공개를 하지 않습니다.",
  AUTO_COLLECT: "정해진 주기로 세 부문을 읽어 draft까지만 저장합니다. "
    + "이름 매핑과 공개는 계속 사람이 확인합니다.",
  AUTO_PUBLISH: "세 부문 수집·검증·매핑이 모두 통과한 경우에만 한 번에 공개합니다. "
    + "하나라도 실패하면 아무것도 공개하지 않습니다.",
};

/** 상태 배지 — 색 + 글리프 + 문장. 셋 다 있어야 한다. */
function StatusBadge({ status }: { status: PikuDevice["status"] }) {
  const map = {
    active: { glyph: "✔", text: "사용 중", cls: "border-green-500/40 bg-green-500/10 text-green-300" },
    pending: { glyph: "⚠", text: "등록 대기", cls: "border-amber-500/40 bg-amber-500/10 text-amber-300" },
    revoked: { glyph: "✖", text: "폐기됨", cls: "border-red-500/40 bg-red-500/10 text-red-300" },
  }[status];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-md border
                      px-1.5 py-0.5 text-[11px] font-bold ${map.cls}`}>
      <span aria-hidden="true">{map.glyph}</span>{map.text}
    </span>
  );
}

const fmt = (ts: number) =>
  ts > 0 ? new Date(ts * 1000).toLocaleString("ko-KR", {
    month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "없음";

export default function PikuDevicePanel() {
  const [data, setData] = useState<PikuDevicesResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  /** 방금 발급된 등록 코드. **서버는 다시 주지 않는다** — 새로고침하면 사라진다. */
  const [issued, setIssued] = useState<{ code: string; name: string; expiresAt: number } | null>(null);
  /** AUTO_PUBLISH는 되돌리기 어려운 선택이라 한 단계 더 확인받는다. */
  const [confirmMode, setConfirmMode] = useState<PikuCollectorMode | null>(null);

  const load = useCallback(async () => {
    try { setData(await api.admin.pikuDevices()); setErr(null); }
    catch (e) { setErr(e instanceof Error ? e.message : "장치 목록을 불러오지 못했습니다."); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const register = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const r = await api.admin.pikuDeviceRegister(name.trim());
      setIssued({ code: r.pairingCode, name: r.name, expiresAt: r.expiresAt });
      setName("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "등록을 시작하지 못했습니다.");
    } finally { setBusy(false); }
  };

  const revoke = async (d: PikuDevice) => {
    setBusy(true);
    try { await api.admin.pikuDeviceRevoke(d.id); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : "폐기하지 못했습니다."); }
    finally { setBusy(false); }
  };

  const applyMode = async (mode: PikuCollectorMode) => {
    setBusy(true);
    try { await api.admin.pikuCollectorSetMode(mode); setConfirmMode(null); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : "모드를 바꾸지 못했습니다."); }
    finally { setBusy(false); }
  };

  if (err && !data) {
    return (
      <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-5">
        <p className="text-sm font-semibold text-red-400">✖ 장치 정보를 불러오지 못했습니다.</p>
        <p className="mt-1 text-xs text-muted">{err}</p>
        <button onClick={() => void load()}
                className="btn-secondary nb-tap mt-3 text-sm">다시 시도</button>
      </div>
    );
  }
  if (!data) return <p className="py-10 text-center text-sm text-muted">불러오는 중…</p>;

  const notReady = !data.schedulerImplemented || !data.autoPublishImplemented;

  return (
    /* `pb-24`는 장식이 아니다 — 사이트 공통 '지원 메뉴' 플로팅 버튼이 화면 우하단에
       고정돼 있어서, 마지막 장치 행의 **폐기 버튼과 겹친다**(768px 실측: 폐기 버튼
       t=980·l=656 위에 FAB t=948·l=692가 올라앉았다). 폐기는 사고가 났을 때 누르는
       버튼이라 가려지면 안 된다. FAB 높이만큼 바닥을 비워 둔다. */
    <div className="space-y-6 pb-24">
      {/* ── 지금 무엇이 도는가 ── */}
      <section className="card space-y-3">
        <h3 className="text-base font-extrabold">자동화 모드</h3>
        {/* 가장 먼저 읽혀야 하는 사실 — 아직 자동으로 도는 것이 없다. */}
        {notReady && (
          <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2
                        text-[12.5px] leading-relaxed text-amber-200">
            <b>⚠ 아직 자동 실행은 일어나지 않습니다.</b> 지금 단계(AUTO-1)에서는 장치 등록과
            토큰 자동 발급까지만 준비됐습니다. 1시간 주기 수집(AUTO-2)과 자동 공개(AUTO-3)는
            다음 단계에서 추가됩니다. 모드를 바꿔도 값만 기록됩니다.
          </p>
        )}
        <div className="flex flex-wrap gap-2" role="group" aria-label="자동화 모드 선택">
          {data.modes.map((m) => {
            const on = data.mode === m;
            return (
              <button key={m} type="button" aria-pressed={on}
                      disabled={busy}
                      onClick={() => (m === "AUTO_PUBLISH" && !on
                        ? setConfirmMode(m) : void applyMode(m))}
                      className={`nb-tap rounded-lg border px-3 py-2 text-sm font-semibold
                                  transition-colors focus-visible:outline
                                  focus-visible:outline-2 focus-visible:outline-offset-2
                                  focus-visible:outline-accent ${
                        on ? "border-accent/50 bg-accent/10 text-fg"
                           : "border-border text-muted hover:text-fg"}`}>
                <span aria-hidden="true">{on ? "● " : "○ "}</span>{MODE_LABEL[m]}
              </button>
            );
          })}
        </div>
        <p className="text-xs leading-relaxed text-muted">{MODE_DESC[data.mode]}</p>

        {confirmMode === "AUTO_PUBLISH" && (
          <div role="alertdialog" aria-label="자동 공개 확인"
               className="rounded-lg border border-red-500/40 bg-red-500/5 p-3">
            <p className="text-sm font-semibold text-red-300">
              ✖ 자동 공개를 켜면 사람 확인 없이 공개 순위가 바뀔 수 있습니다.
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              안전 게이트(64/64/32 · 세 부문 동일 회차 · 전원 매핑 확정 · 변동량 임계값)를
              모두 통과할 때만 실행되고, 하나라도 실패하면 기존 공개본이 그대로 유지됩니다.
              그래도 되돌리려면 이 화면에서 다시 <b>수동</b>으로 바꿔야 합니다.
            </p>
            <div className="nb-tap-gap mt-3 flex flex-wrap gap-2">
              <button onClick={() => void applyMode("AUTO_PUBLISH")} disabled={busy}
                      className="btn-secondary nb-tap text-sm !border-red-500/50 !text-red-200">
                이해했고 자동 공개를 켭니다
              </button>
              <button onClick={() => setConfirmMode(null)}
                      className="btn-secondary nb-tap text-sm">취소</button>
            </div>
          </div>
        )}
      </section>

      {/* ── 등록 ── */}
      <section className="card space-y-3">
        <h3 className="text-base font-extrabold">장치 등록</h3>
        <p className="text-xs leading-relaxed text-muted">
          자동 수집을 돌릴 PC마다 따로 등록합니다. 등록 코드를 발급한 뒤 그 PC의 확장
          팝업에 입력하면, 확장이 <b className="text-fg">브라우저 밖으로 꺼낼 수 없는 키</b>를
          만들고 공개키만 서버에 보냅니다. 개인키는 서버에 오지 않습니다.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-0 flex-1">
            <span className="mb-1 block text-xs font-semibold text-muted">장치 이름</span>
            <input value={name} onChange={(e) => setName(e.target.value)}
                   maxLength={40} placeholder="예: 거실 PC"
                   className="w-full min-h-[44px] rounded-lg border border-border
                              bg-bg-card px-3 text-sm outline-none
                              focus-visible:outline focus-visible:outline-2
                              focus-visible:outline-offset-2 focus-visible:outline-accent" />
          </label>
          <button onClick={() => void register()} disabled={busy || !name.trim()}
                  className="btn-primary nb-tap text-sm">등록 코드 발급</button>
        </div>

        {issued && (
          <div className="rounded-lg border border-accent/40 bg-accent/5 p-3">
            <p className="text-sm font-semibold text-fg">
              ✔ {issued.name} 등록 코드
            </p>
            {/* 코드는 서버가 다시 주지 않는다. 그 사실을 함께 적는다. */}
            <p className="mt-2 select-all font-mono text-2xl font-extrabold tracking-[0.2em]">
              {issued.code}
            </p>
            <p className="mt-2 text-xs leading-relaxed text-muted">
              {fmt(issued.expiresAt)}까지 유효하며 <b className="text-fg">한 번만</b> 쓸 수 있습니다.
              이 화면을 벗어나면 다시 볼 수 없습니다 — 필요하면 새로 발급하세요.
            </p>
          </div>
        )}
      </section>

      {/* ── 목록 ── */}
      <section className="space-y-2">
        <h3 className="text-base font-extrabold">
          등록된 장치 <span className="text-sm font-normal text-muted tabular-nums">
            {data.deviceCount}대 (사용 중 {data.activeCount})
          </span>
        </h3>
        {data.devices.length === 0 ? (
          <p className="rounded-lg border border-border bg-bg-card/60 px-3 py-6
                        text-center text-sm text-muted">
            아직 등록된 장치가 없습니다. 위에서 등록 코드를 발급하세요.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {data.devices.map((d) => (
              <li key={d.id}
                  className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5
                             rounded-lg border border-border bg-bg-card/60 px-3 py-2.5">
                <StatusBadge status={d.status} />
                <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">
                  {d.name}
                </span>
                {d.status !== "revoked" && (
                  <button onClick={() => void revoke(d)} disabled={busy}
                          className="btn-secondary nb-tap shrink-0 text-xs
                                     !border-red-500/40 !text-red-300">
                    폐기
                  </button>
                )}
                <dl className="w-full min-w-0 grid grid-cols-2 gap-x-4 gap-y-0.5
                               text-[11.5px] leading-snug text-muted sm:grid-cols-4">
                  <div className="col-span-2 min-w-0 sm:col-span-4">
                    <dt className="inline font-semibold">지문 </dt>
                    <dd className="inline font-mono break-all">
                      {d.fingerprint || "— (등록 대기)"}
                    </dd>
                  </div>
                  <div><dt className="inline font-semibold">마지막 접속 </dt>
                    <dd className="inline tabular-nums">{fmt(d.lastSeenAt)}</dd></div>
                  <div><dt className="inline font-semibold">마지막 성공 </dt>
                    <dd className="inline tabular-nums">{fmt(d.lastSuccessAt)}</dd></div>
                  <div><dt className="inline font-semibold">마지막 실패 </dt>
                    <dd className="inline tabular-nums">
                      {fmt(d.lastFailureAt)}{d.lastFailureKind ? ` (${d.lastFailureKind})` : ""}
                    </dd></div>
                  <div><dt className="inline font-semibold">폐기 </dt>
                    <dd className="inline tabular-nums">{fmt(d.revokedAt)}</dd></div>
                </dl>
              </li>
            ))}
          </ul>
        )}
        <p className="text-[11px] leading-relaxed text-muted/80">
          지문은 공개키의 해시라 화면에 띄워도 안전합니다. 확장 팝업에 보이는 지문과
          같은지 눈으로 대조하세요 — 다르면 다른 PC가 등록된 것입니다.
        </p>
      </section>

      {err && (
        <p role="alert" className="rounded-lg border border-red-500/40 bg-red-500/5
                                   px-3 py-2 text-xs text-red-300">✖ {err}</p>
      )}
    </div>
  );
}
