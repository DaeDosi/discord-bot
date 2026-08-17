"use client";
/**
 * 수정 요청 폼.
 *
 * 지키는 것:
 *  · **검증은 서버가 한다.** 여기 검사는 사용자를 돕기 위한 것이고, 통과 여부의
 *    판정자가 아니다. 한도(길이)도 서버에서 받아 쓴다 — 프런트 상수로 두면 갈라진다.
 *  · **중복 제출 방지** — 전송 중에는 버튼이 잠기고, 성공하면 폼이 닫힌다.
 *  · **상태를 셋으로 나눈다**: 제출 중 / 성공 / 실패. 하나로 뭉치면 실패가
 *    제출 중으로 보인다.
 *  · 모바일에서 입력 영역이 잘리지 않게 한 열로 쌓고, 키보드가 올라와도 버튼에
 *    닿을 수 있도록 폼 하단에 둔다.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertCircle, Check, Loader2, PencilLine } from "lucide-react";
import SiteHeader from "@/components/SiteHeader";
import { api } from "@/lib/api";
import type { CorrectionMeta } from "@/lib/types";

const FALLBACK_LIMITS = {
  clipRef: 200, description: 2000, descriptionMin: 10,
  desiredFix: 1000, evidenceUrl: 300, email: 254,
};

export default function CorrectionPage() {
  const [meta, setMeta] = useState<CorrectionMeta | null>(null);
  const [category, setCategory] = useState("");
  const [clipRef, setClipRef] = useState("");
  const [description, setDescription] = useState("");
  const [desiredFix, setDesiredFix] = useState("");
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [email, setEmail] = useState("");
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // 같은 tick의 두 번째 제출을 막는다 — state는 다음 렌더에야 반영된다.
  const inFlight = useRef(false);
  const errRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    api.support.correctionMeta()
      .then((m) => { if (alive) { setMeta(m); setCategory(m.categories[0]?.key ?? ""); } })
      .catch(() => { /* 한도는 폴백을 쓴다 — 메타 실패로 폼을 막지 않는다 */ });
    return () => { alive = false; };
  }, []);

  const lim = meta?.limits ?? FALLBACK_LIMITS;
  const canSubmit = !!category && clipRef.trim().length > 0
    && description.trim().length >= lim.descriptionMin && !sending;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || inFlight.current) return;
    inFlight.current = true;
    setSending(true); setErr(null);
    try {
      const res = await api.support.submitCorrection({
        category, clipRef: clipRef.trim(), description: description.trim(),
        desiredFix: desiredFix.trim() || undefined,
        evidenceUrl: evidenceUrl.trim() || undefined,
        email: email.trim() || undefined,
      });
      setDone(res.id);
    } catch (e2) {
      // 실패를 성공으로 꾸미지 않는다. 서버 문구를 그대로 쓴다.
      setErr(e2 instanceof Error ? e2.message : "접수에 실패했습니다.");
      // 오류로 포커스를 옮겨 스크린리더가 즉시 읽게 한다.
      requestAnimationFrame(() => errRef.current?.focus());
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  };

  const field = "w-full rounded-lg border border-border bg-bg px-3 py-2.5 text-sm " +
    "text-fg placeholder-muted transition-colors focus:border-accent focus:outline-none";
  const label = "block text-sm font-semibold text-fg";
  const hint = "mt-1 block text-xs leading-relaxed text-muted";

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <SiteHeader maxWidth="3xl" />

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-5">
        <h1 className="flex items-center gap-2 text-2xl font-extrabold tracking-tight">
          <PencilLine size={22} className="text-accent" aria-hidden="true" /> 수정 요청
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          클립 정보나 순위 표시가 실제와 다르면 알려 주세요. 어떤 클립인지 특정할 수
          있어야 확인이 가능하므로 클립 주소나 ID를 함께 적어 주시기 바랍니다.
          일반 문의는{" "}
          <Link href="/contact" className="underline underline-offset-2 hover:text-fg">
            문의하기
          </Link>
          를 이용해 주세요.
        </p>

        {meta && meta.accepting === false ? (
          /* 접수가 설정되지 않은 배포 — **폼을 띄우지 않는다.** 입력을 다 받은 뒤
             503을 주면 사용자는 자기가 뭘 잘못 적었다고 읽는다. */
          <div role="status"
               className="mt-8 rounded-xl border border-amber-500/40 bg-amber-500/5 p-6">
            <p className="text-sm font-semibold text-amber-400">
              지금은 수정 요청을 접수할 수 없습니다.
            </p>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              접수 기능이 준비되지 않았습니다. 급한 정정이 필요하시면{" "}
              <Link href="/contact" className="underline underline-offset-2 hover:text-fg">
                문의하기
              </Link>
              를 이용해 주세요.
            </p>
          </div>
        ) : done !== null ? (
          /* 성공 — 폼을 닫고 접수 번호를 준다(중복 제출을 구조적으로 막는다) */
          <div role="status"
               className="mt-8 rounded-xl border border-accent/40 bg-accent/5 p-6">
            <p className="flex items-center gap-2 text-sm font-semibold text-accent">
              <Check size={16} aria-hidden="true" /> 접수되었습니다.
            </p>
            <p className="mt-2 text-sm leading-relaxed text-muted">
              접수 번호 <b className="text-fg tabular-nums">#{done}</b> 입니다.
              확인 후 필요한 경우에만 회신하며, 개별 진행 상황은 안내하지 않습니다.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Link href="/stats" className="btn-primary nb-tap text-sm">통계로</Link>
              <button onClick={() => { setDone(null); setClipRef(""); setDescription("");
                                       setDesiredFix(""); setEvidenceUrl(""); }}
                      className="btn-secondary nb-tap text-sm">
                다른 내용 더 알리기
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={submit} className="mt-8 space-y-5" noValidate>
            {err && (
              <div ref={errRef} tabIndex={-1} role="alert"
                   className="rounded-xl border border-red-500/40 bg-red-500/5 p-4">
                <p className="flex items-center gap-2 text-sm font-semibold text-red-400">
                  <AlertCircle size={15} aria-hidden="true" /> 접수하지 못했습니다.
                </p>
                <p className="mt-1 text-xs text-muted">{err}</p>
              </div>
            )}

            <div>
              <label htmlFor="cr-clip" className={label}>
                클립 주소 또는 ID <span className="text-red-400" aria-hidden="true">*</span>
              </label>
              <input id="cr-clip" value={clipRef} required maxLength={lim.clipRef}
                     onChange={(e) => setClipRef(e.target.value)}
                     placeholder="https://chzzk.naver.com/clips/… 또는 클립 ID"
                     className={`${field} mt-1.5`} />
              <span className={hint}>어떤 클립인지 특정할 수 있어야 확인이 가능합니다.</span>
            </div>

            <div>
              <label htmlFor="cr-cat" className={label}>
                분류 <span className="text-red-400" aria-hidden="true">*</span>
              </label>
              <select id="cr-cat" value={category} required
                      onChange={(e) => setCategory(e.target.value)}
                      className={`${field} mt-1.5`}>
                {(meta?.categories ?? []).map((c) => (
                  <option key={c.key} value={c.key}>{c.label}</option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="cr-desc" className={label}>
                문제 설명 <span className="text-red-400" aria-hidden="true">*</span>
              </label>
              <textarea id="cr-desc" value={description} required rows={5}
                        maxLength={lim.description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder="무엇이 어떻게 다른지 적어 주세요."
                        className={`${field} mt-1.5`} />
              <span className={hint}>
                최소 {lim.descriptionMin}자 ·{" "}
                <span className="tabular-nums">{description.length}</span>/{lim.description}
              </span>
            </div>

            <div>
              <label htmlFor="cr-fix" className={label}>
                원하는 수정 내용 <span className="font-normal text-muted">(선택)</span>
              </label>
              <textarea id="cr-fix" value={desiredFix} rows={3} maxLength={lim.desiredFix}
                        onChange={(e) => setDesiredFix(e.target.value)}
                        className={`${field} mt-1.5`} />
            </div>

            <div>
              <label htmlFor="cr-url" className={label}>
                근거 자료 주소 <span className="font-normal text-muted">(선택)</span>
              </label>
              <input id="cr-url" type="url" value={evidenceUrl} maxLength={lim.evidenceUrl}
                     onChange={(e) => setEvidenceUrl(e.target.value)}
                     placeholder="https://…" inputMode="url"
                     className={`${field} mt-1.5`} />
              <span className={hint}>https:// 로 시작하는 주소만 접수됩니다.</span>
            </div>

            <div>
              <label htmlFor="cr-email" className={label}>
                답변받을 이메일 <span className="font-normal text-muted">(선택)</span>
              </label>
              <input id="cr-email" type="email" value={email} maxLength={lim.email}
                     onChange={(e) => setEmail(e.target.value)}
                     placeholder="회신이 필요할 때만 적어 주세요" inputMode="email"
                     autoComplete="email" className={`${field} mt-1.5`} />
              <span className={hint}>
                회신 목적으로만 사용합니다. 적지 않아도 접수됩니다.
              </span>
            </div>

            <div className="flex flex-wrap items-center gap-3 pt-1">
              <button type="submit" disabled={!canSubmit} aria-busy={sending}
                      className="btn-primary nb-tap inline-flex items-center gap-1.5
                                 text-sm disabled:opacity-50">
                {sending && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
                {sending ? "보내는 중…" : "수정 요청 보내기"}
              </button>
              <Link href="/stats" className="btn-secondary nb-tap text-sm">취소</Link>
            </div>
          </form>
        )}
      </main>

    </div>
  );
}
