// 확정 랭킹 로더 — 자동 재확인(차단 조건 I)의 실제 구현.
//
// ── 왜 React 밖에 있나 ──────────────────────────────────────────────────────
// "503 finalizing을 받으면 사용자가 아무것도 하지 않아도 다시 확인한다"는 계약은
// 타이머·예산·취소가 얽혀 있어 조용히 틀리기 쉽다(타이머가 두 개 돌거나, unmount
// 뒤에도 남거나, 영구 장애에서 영원히 폴링하거나). React 훅 안에 두면 DOM 없이
// 검증할 수 없으므로, 타이머와 fetch를 주입받는 평범한 클래스로 떼어 놓고
// 훅은 그것을 감싸기만 한다.
//
// ── 자동 재확인은 딱 한 경우에만 ────────────────────────────────────────────
// 서버가 **503 + status:"finalizing" + frozen:true**로 "준비 중"이라고 말했을
// 때뿐이다. 500·404·네트워크 실패·계약 불일치 503은 기다린다고 나아진다는 보장이
// 없으므로 즉시 error로 보내고 폴링하지 않는다.

import type { SingcupMain } from "./types";

// ── 응답 분류 ───────────────────────────────────────────────────────────────
// 분류와 자동 재확인은 같은 관심사(= final-ranking 응답을 해석하는 일)라 한 파일에
// 둔다. 나눠 두면 이 파일이 런타임 상대 import를 갖게 되는데, 이 저장소의 프론트
// 테스트는 번들러 없이 `node --test`로 도는 터라 확장자 규칙이 서로 어긋난다.

export type SingcupRankingKind = "final" | "live" | "finalizing" | "error";

export type Classified =
  | { kind: "final"; data: SingcupMain }
  | { kind: "live" }
  | { kind: "finalizing" }
  | { kind: "error" };

/** 서버가 "확정본을 준비 중"이라고 **명시**했을 때만 finalizing이다.
 *
 *  503이어도 본문 계약이 맞지 않으면(프록시가 만든 503, 게이트웨이 오류 등)
 *  그건 우리가 아는 상태가 아니므로 error로 본다. */
function isFinalizingBody(body: unknown): boolean {
  if (!body || typeof body !== "object") return false;
  const b = body as { status?: unknown; frozen?: unknown };
  return b.status === "finalizing" && b.frozen === true;
}

/**
 * @param status  HTTP 상태 코드. 네트워크 실패·timeout이면 null을 넘긴다.
 * @param body    파싱된 JSON. 파싱 실패면 undefined를 넘긴다.
 */
export function classifyFinalRanking(status: number | null, body: unknown): Classified {
  // 네트워크 실패 / timeout — 서버 말을 못 들었으므로 상태를 알 수 없다.
  if (status === null) return { kind: "error" };

  if (status === 503) {
    return isFinalizingBody(body) ? { kind: "finalizing" } : { kind: "error" };
  }

  if (status === 200) {
    if (!body || typeof body !== "object") return { kind: "error" };
    const b = body as { frozen?: unknown; rankingFinal?: unknown; streamers?: unknown };
    // 동결 비활성 — 기존 실시간 경로로 간다.
    if (b.frozen === false) return { kind: "live" };
    // 확정본. `streamers`까지 확인한다 — rankingFinal만 있고 목록이 없으면
    // 화면이 빈 순위를 '최종'이라고 말하게 된다.
    if (b.rankingFinal === true && Array.isArray(b.streamers)) {
      return { kind: "final", data: body as SingcupMain };
    }
    return { kind: "error" };               // 200인데 계약에 없는 모양
  }

  // 4xx·5xx 전부(404·401·403·500 …). 실시간으로 물러서지 않는다.
  return { kind: "error" };
}


/** 자동 재확인 간격의 하한. 서버 cooldown(기본 30초)보다 자주 물어도 서버는
 *  `cooldown`으로 즉시 빠지지만(DB write 0), 그래도 tight loop는 만들지 않는다. */
export const AUTO_MIN_MS = 10_000;
/** 상한 — 서버가 말도 안 되게 큰 Retry-After를 줘도 화면이 사실상 멈추지 않게 한다. */
export const AUTO_MAX_MS = 120_000;
/** `Retry-After`를 읽을 수 없을 때. 서버 기본 cooldown과 같은 값이다. */
export const AUTO_DEFAULT_MS = 30_000;
/** 자동 재확인 횟수 상한. 소진되면 error로 전환하고 수동 버튼을 준다.
 *  10회 × 최소 10초 = 최소 100초, 기본 간격이면 5분. 영구 장애에서 영원히
 *  요청하지 않기 위한 예산이다. */
export const MAX_AUTO_CHECKS = 10;

/**
 * `Retry-After` 헤더(초 단위 정수) → 밀리초. 안전 범위로 자른다.
 *
 * 0·음수·NaN·비어 있음·HTTP-date 형식은 전부 기본값으로 떨어뜨린다 — 서버가
 * 이상한 값을 줬다고 화면이 tight loop를 돌거나 영원히 멈추면 안 된다.
 */
export function parseRetryAfterMs(header: string | null | undefined): number {
  if (!header) return AUTO_DEFAULT_MS;
  const n = Number(String(header).trim());
  if (!Number.isFinite(n) || n <= 0) return AUTO_DEFAULT_MS;
  const ms = n * 1000;
  if (ms < AUTO_MIN_MS) return AUTO_MIN_MS;
  if (ms > AUTO_MAX_MS) return AUTO_MAX_MS;
  return ms;
}

export type LoaderStatus = "loading" | SingcupRankingKind;

export type LoaderSnapshot = {
  status: LoaderStatus;
  data: SingcupMain | null;
  /** 지금까지 자동으로 다시 확인한 횟수(수동 재시도로 초기화된다). */
  autoChecks: number;
};

/** 한 번의 조회 결과. 네트워크 실패는 `status: null`이다. */
export type FetchOutcome = {
  status: number | null;
  body: unknown;
  retryAfter: string | null;
};

export type LoaderDeps = {
  fetchOnce: (signal: AbortSignal) => Promise<FetchOutcome>;
  onChange: (snap: LoaderSnapshot) => void;
  /** 테스트에서 가짜 타이머를 넣기 위해 주입받는다. */
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
};

export class FinalRankingLoader {
  private readonly deps: LoaderDeps;
  private timer: unknown = null;
  private abort: AbortController | null = null;
  private disposed = false;
  private inFlight = false;
  private autoChecks = 0;
  private status: LoaderStatus = "loading";
  private data: SingcupMain | null = null;
  /** 관측·테스트용 — 실제로 나간 요청 수. */
  requests = 0;

  constructor(deps: LoaderDeps) {
    this.deps = deps;
  }

  snapshot(): LoaderSnapshot {
    return { status: this.status, data: this.data, autoChecks: this.autoChecks };
  }

  /** 첫 조회. 훅의 마운트 시점에 한 번만 부른다. */
  start(): void {
    void this.run();
  }

  /** 수동 재시도 — 자동 재확인 예산을 새로 준다. */
  retry(): void {
    if (this.disposed || this.inFlight) return;   // 연타는 한 요청만
    this.autoChecks = 0;
    this.clearTimer();
    void this.run();
  }

  /** unmount 시 호출 — 타이머를 지우고 진행 중 요청을 취소한다. */
  dispose(): void {
    this.disposed = true;
    this.clearTimer();
    this.abort?.abort();
    this.abort = null;
  }

  private clearTimer(): void {
    if (this.timer === null) return;
    (this.deps.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>)))(this.timer);
    this.timer = null;
  }

  private emit(): void {
    if (this.disposed) return;                    // 끝난 컴포넌트에 setState 금지
    this.deps.onChange(this.snapshot());
  }

  private async run(): Promise<void> {
    if (this.disposed || this.inFlight) return;
    this.inFlight = true;
    this.abort = new AbortController();
    let outcome: FetchOutcome;
    try {
      this.requests += 1;
      outcome = await this.deps.fetchOnce(this.abort.signal);
    } catch {
      // 네트워크 실패·취소. 취소는 dispose가 이미 처리했으므로 상태를 바꾸지 않는다.
      outcome = { status: null, body: undefined, retryAfter: null };
    } finally {
      this.inFlight = false;
    }
    if (this.disposed) return;

    const c = classifyFinalRanking(outcome.status, outcome.body);
    if (c.kind === "final") this.data = c.data;

    if (c.kind === "finalizing") {
      // 예산이 남아 있을 때만 다시 확인한다. 소진되면 error로 넘겨 수동 버튼을 준다 —
      // 영구 장애에서 영원히 요청하지 않기 위한 상한이다.
      if (this.autoChecks >= MAX_AUTO_CHECKS) {
        this.status = "error";
        this.emit();
        return;
      }
      this.status = "finalizing";
      this.emit();
      this.scheduleNext(parseRetryAfterMs(outcome.retryAfter));
      return;
    }

    // final / live / error — 더 이상 자동으로 묻지 않는다.
    this.clearTimer();
    this.status = c.kind;
    this.emit();
  }

  private scheduleNext(ms: number): void {
    this.clearTimer();                            // 타이머는 항상 하나뿐
    if (this.disposed) return;
    const set = this.deps.setTimer
      ?? ((fn, delay) => setTimeout(fn, delay) as unknown);
    this.timer = set(() => {
      this.timer = null;
      if (this.disposed) return;
      this.autoChecks += 1;
      void this.run();
    }, ms);
  }
}
