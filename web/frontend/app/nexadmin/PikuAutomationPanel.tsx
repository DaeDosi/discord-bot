"use client";
/**
 * AUTO-2 — 자동 수집 **상태** 화면.
 *
 * 이 화면이 답해야 하는 질문:
 *   · 지금 자동으로 돌고 있나? 다음은 언제인가?
 *   · 마지막 회차는 세 부문 다 됐나, 일부만 됐나, 다 실패했나?
 *   · 안 되면 무엇 때문인가? 어디를 눌러 멈추나?
 *
 * **AUTO-2의 종착점은 draft다.** 공개(Publish)는 여기에 없고 AUTO-3이 만든다.
 * 그래서 자동 공개 선택지를 **아예 노출하지 않는다** — 서버가 `autoPublishReady:
 * false`를 주고, 화면은 그 값을 근거로 "준비되지 않음"이라고 적는다.
 *
 * 실행 자체는 **확장이** 한다(브라우저와 PC가 켜져 있어야 한다). 이 화면은 그 결과를
 * 읽을 뿐이고 '지금 수집'도 확장 팝업에 있다. 그 사실을 숨기지 않고 적어 둔다 —
 * 대시보드에서 눌렀는데 아무 일도 안 일어나는 것이 가장 헷갈린다.
 *
 * 표시 규칙: 상태를 **색만으로** 구분하지 않는다. 글리프와 한국어 문장을 함께 둔다.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  PikuAutoRun, PikuAutomationStatus, PikuCollectorMode, PikuRunOutcome,
} from "@/lib/types";

const DIVISION_LABEL: Record<string, string> = {
  female_solo: "여성 솔로", male_solo: "남성 솔로", groups: "그룹",
};
const EXPECTED: Record<string, number> = {
  female_solo: 64, male_solo: 64, groups: 32,
};

/** 실패 분류어를 사람이 읽을 문장으로. 모르는 값은 그대로 보여 준다(감추지 않는다). */
const KIND_TEXT: Record<string, string> = {
  sent: "전송 완료",
  unchanged: "직전과 같은 내용이라 보내지 않음",
  no_tab: "해당 부문 탭이 열려 있지 않음",
  ambiguous_tab: "같은 부문 탭이 여러 개라 중단",
  loading: "페이지가 아직 로딩 중",
  row_count: "행 수가 부족함 (100개 보기 상태인지 확인)",
  source_mismatch: "탭 주소와 부문이 어긋남",
  parse_failed: "표를 읽지 못함",
  token_failed: "수집 토큰을 받지 못함",
  ingest_failed: "NexBot 전송에 실패",
  aborted: "실행 중 중단됨",
};

const OUTCOME: Record<PikuRunOutcome, { glyph: string; text: string; cls: string }> = {
  running: { glyph: "•", text: "진행 중", cls: "border-border bg-bg-hover text-muted" },
  success: { glyph: "✔", text: "세 부문 완료",
             cls: "border-green-500/40 bg-green-500/10 text-green-300" },
  partial: { glyph: "⚠", text: "일부만 완료",
             cls: "border-amber-500/40 bg-amber-500/10 text-amber-300" },
  failed: { glyph: "✖", text: "실패",
            cls: "border-red-500/40 bg-red-500/10 text-red-300" },
};

const fmt = (ts: number) =>
  ts > 0 ? new Date(ts * 1000).toLocaleString("ko-KR", {
    month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "없음";

function OutcomeBadge({ outcome }: { outcome: PikuRunOutcome }) {
  const m = OUTCOME[outcome] ?? OUTCOME.running;
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 rounded-md border
                      px-1.5 py-0.5 text-[11px] font-bold ${m.cls}`}>
      <span aria-hidden="true">{m.glyph}</span>{m.text}
    </span>
  );
}

/** 한 회차의 세 부문 결과. 성공/실패를 **글리프 + 문장**으로 함께 적는다. */
function RunRow({ run }: { run: PikuAutoRun }) {
  return (
    <li className="rounded-lg border border-border bg-bg-card/60 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <OutcomeBadge outcome={run.outcome} />
        <span className="text-[11.5px] tabular-nums text-muted">{fmt(run.startedAt)}</span>
        <span className="rounded border border-border px-1 text-[10.5px] text-muted">
          {run.trigger === "manual" ? "수동" : "자동"}
        </span>
      </div>
      <dl className="mt-1.5 grid grid-cols-1 gap-x-4 gap-y-0.5 text-[11.5px]
                     leading-snug text-muted sm:grid-cols-3">
        {Object.keys(EXPECTED).map((d) => {
          const r = run.divisions[d];
          const ok = r?.ok;
          return (
            <div key={d} className="min-w-0">
              <dt className="inline font-semibold">
                <span aria-hidden="true">{ok ? "✔ " : "✖ "}</span>
                {DIVISION_LABEL[d]}{" "}
              </dt>
              <dd className="inline break-words">
                {ok ? `${r.rows}/${EXPECTED[d]}행` : "실패"}
                {r?.kind ? ` — ${KIND_TEXT[r.kind] ?? r.kind}` : ""}
              </dd>
            </div>
          );
        })}
      </dl>
    </li>
  );
}

export default function PikuAutomationPanel() {
  const [data, setData] = useState<PikuAutomationStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setData(await api.admin.pikuAutomation()); setErr(null); }
    catch (e) { setErr(e instanceof Error ? e.message : "자동화 상태를 불러오지 못했습니다."); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const setMode = async (mode: PikuCollectorMode) => {
    setBusy(true);
    try { await api.admin.pikuCollectorSetMode(mode); await load(); }
    catch (e) { setErr(e instanceof Error ? e.message : "모드를 바꾸지 못했습니다."); }
    finally { setBusy(false); }
  };

  if (err && !data) {
    return (
      <div role="alert" className="rounded-xl border border-red-500/40 bg-red-500/5 p-5">
        <p className="text-sm font-semibold text-red-400">✖ 자동화 상태를 불러오지 못했습니다.</p>
        <p className="mt-1 text-xs text-muted">{err}</p>
        <button onClick={() => void load()} className="btn-secondary nb-tap mt-3 text-sm">
          다시 시도
        </button>
      </div>
    );
  }
  if (!data) return <p className="py-10 text-center text-sm text-muted">불러오는 중…</p>;

  const noDevice = data.activeDeviceCount === 0;
  const auto = data.mode !== "MANUAL";

  return (
    /* `pb-24`는 사이트 공통 '지원 메뉴' 플로팅 버튼이 마지막 행을 가리기 때문이다
       (AUTO-1에서 실측했다). 여기서도 같은 이유로 바닥을 비워 둔다. */
    <div className="space-y-6 pb-24">
      {/* ── 지금 무엇이 도는가 ── */}
      <section className="card space-y-3">
        <h3 className="text-base font-extrabold">자동 수집</h3>

        {/* 가장 먼저 읽혀야 하는 사실 — 실행 주체가 브라우저다. */}
        <p className="rounded-lg border border-border bg-bg-hover/40 px-3 py-2
                      text-[12.5px] leading-relaxed text-muted">
          자동 수집은 <b className="text-fg">등록된 PC의 Chrome 확장</b>이 실행합니다.
          그 PC와 브라우저가 켜져 있고 PIKU 세 부문 탭이 <b className="text-fg">100개 보기</b>
          상태로 열려 있어야 합니다. 이 화면은 결과를 보여 주고 모드를 바꿉니다 —
          <b className="text-fg"> 지금 수집</b>과 일시 정지는 확장 팝업에 있습니다.
        </p>

        <div className="flex flex-wrap gap-2" role="group" aria-label="자동화 모드">
          {(["MANUAL", "AUTO_COLLECT"] as PikuCollectorMode[]).map((m) => {
            const on = data.mode === m;
            return (
              <button key={m} type="button" aria-pressed={on} disabled={busy}
                      onClick={() => void setMode(m)}
                      className={`nb-tap rounded-lg border px-3 py-2 text-sm font-semibold
                                  transition-colors focus-visible:outline
                                  focus-visible:outline-2 focus-visible:outline-offset-2
                                  focus-visible:outline-accent ${
                        on ? "border-accent/50 bg-accent/10 text-fg"
                           : "border-border text-muted hover:text-fg"}`}>
                <span aria-hidden="true">{on ? "● " : "○ "}</span>
                {m === "MANUAL" ? "수동 (기본)" : "자동 수집"}
              </button>
            );
          })}
          {/* 자동 공개는 **선택지로도 두지 않는다.** 있는데 눌리지 않는 버튼은
              "곧 될 것"으로 읽혀서 더 나쁘다. */}
          <span className="inline-flex items-center gap-1 rounded-lg border border-border
                           px-3 py-2 text-sm text-muted/60"
                title="AUTO-3에서 안전 게이트와 함께 추가됩니다">
            <span aria-hidden="true">🔒</span> 자동 공개 — 준비되지 않음
          </span>
        </div>

        <p className="text-xs leading-relaxed text-muted">
          {auto
            ? `정해진 주기(약 ${data.periodMinutes}분)로 세 부문을 읽어 draft까지만 저장합니다. `
              + "이름 매핑과 공개는 계속 사람이 확인합니다."
            : "지금은 수동입니다. 확장이 자동으로 실행하지 않습니다."}
        </p>

        {auto && noDevice && (
          <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10
                                     px-3 py-2 text-[12.5px] leading-relaxed text-amber-200">
            <b>⚠ 자동 수집이 켜져 있지만 등록된 장치가 없습니다.</b> 아무것도 실행되지
            않습니다. ‘PIKU 자동화 장치’ 탭에서 PC를 먼저 등록하세요.
          </p>
        )}
        {!data.autoPublishReady && (
          <p className="text-[11px] leading-relaxed text-muted/80">
            🔒 자동 공개(AUTO_PUBLISH)는 아직 없습니다. 세 부문이 모두 모여도
            <b className="text-fg"> 공개는 사람이</b> 합니다.
          </p>
        )}
      </section>

      {/* ── 장치 ── */}
      <section className="space-y-2">
        <h3 className="text-base font-extrabold">
          자동 수집 장치{" "}
          <span className="text-sm font-normal text-muted tabular-nums">
            {data.activeDeviceCount}대
          </span>
        </h3>
        {noDevice ? (
          <p className="rounded-lg border border-border bg-bg-card/60 px-3 py-5
                        text-center text-sm text-muted">
            사용 중인 장치가 없습니다. 장치를 폐기하면 자동 수집도 즉시 멈춥니다.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {data.activeDevices.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1
                                        rounded-lg border border-border bg-bg-card/60
                                        px-3 py-2.5">
                <span className="inline-flex shrink-0 items-center gap-1 rounded-md border
                                 border-green-500/40 bg-green-500/10 px-1.5 py-0.5
                                 text-[11px] font-bold text-green-300">
                  <span aria-hidden="true">✔</span>사용 중
                </span>
                <span className="min-w-0 flex-1 truncate text-sm font-semibold text-fg">
                  {d.name}
                </span>
                <span className="w-full font-mono text-[11px] text-muted break-all">
                  지문 {d.fingerprint} · 마지막 접속 {fmt(d.lastSeenAt)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ── 최근 회차 ── */}
      <section className="space-y-2">
        <h3 className="text-base font-extrabold">최근 실행</h3>
        {data.recentRuns.length === 0 ? (
          <p className="rounded-lg border border-border bg-bg-card/60 px-3 py-5
                        text-center text-sm text-muted">
            아직 실행 기록이 없습니다.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {data.recentRuns.map((r) => <RunRow key={r.id} run={r} />)}
          </ul>
        )}
        <p className="text-[11px] leading-relaxed text-muted/80">
          <b className="text-fg">일부만 완료</b>는 성공한 부문의 draft만 저장됐다는
          뜻입니다. 세 부문이 같은 회차에 모두 모여야 공개할 수 있습니다.
        </p>
      </section>

      {err && (
        <p role="alert" className="rounded-lg border border-red-500/40 bg-red-500/5
                                   px-3 py-2 text-xs text-red-300">✖ {err}</p>
      )}
    </div>
  );
}
