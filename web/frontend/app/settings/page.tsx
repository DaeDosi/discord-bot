"use client";
/**
 * 계정 설정 — 내 정보 · 저장된 데이터 · **회원탈퇴**.
 *
 * 화면 구조에서 지키는 것:
 *  · **로그아웃과 회원탈퇴를 시각·기능적으로 분리한다.** 로그아웃은 위쪽 일반
 *    영역에, 탈퇴는 **맨 아래** 별도 경고 영역에 둔다. 나란히 두면 오조작이 난다.
 *  · 탈퇴 전에 **무엇이 저장돼 있는지**와 **무엇이 실제로 일어나는지**를 보여 준다.
 *  · **완료로 꾸미지 않는다.** 서버가 `status`를 주고 화면은 그 값을 그대로 읽는다.
 *    지금은 개인정보처리방침이 '삭제는 이메일로만'이라 웹 즉시 삭제가 막혀 있고,
 *    그 사실을 감추지 않고 그대로 알린다.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import {
  AlertTriangle, Check, Loader2, LogOut, Shield, Trash2, UserRound,
} from "lucide-react";
import SiteHeader from "@/components/SiteHeader";
import { api } from "@/lib/api";
import type { AccountMe, AccountDeleteResult } from "@/lib/types";

export default function SettingsPage() {
  const [me, setMe] = useState<AccountMe | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [openDelete, setOpenDelete] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [reason, setReason] = useState("");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<AccountDeleteResult | null>(null);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      setMe(await api.account.me());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "계정 정보를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("discord_user");
    window.location.href = "/";
  };

  const username = me?.user.username ?? "";
  const canDelete = confirmText === username && username.length > 0 && !sending;

  const submitDelete = async () => {
    if (!canDelete || inFlight.current) return;
    inFlight.current = true;
    setSending(true); setDeleteErr(null);
    try {
      setResult(await api.account.requestDelete(confirmText, reason.trim()));
      await load();
    } catch (e) {
      setDeleteErr(e instanceof Error ? e.message : "요청에 실패했습니다.");
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <SiteHeader maxWidth="3xl" />

      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 sm:px-5">
        <h1 className="text-2xl font-extrabold tracking-tight">설정</h1>

        {err ? (
          <div role="alert"
               className="mt-6 rounded-xl border border-red-500/40 bg-red-500/5 p-6">
            <p className="text-sm font-semibold text-red-400">
              계정 정보를 불러오지 못했습니다.
            </p>
            <p className="mt-1 text-xs text-muted">{err}</p>
            <Link href="/login" className="btn-secondary nb-tap mt-3 inline-flex text-sm">
              다시 로그인
            </Link>
          </div>
        ) : loading ? (
          <p className="flex items-center gap-2 py-16 text-muted" aria-busy="true">
            <Loader2 size={18} className="animate-spin" aria-hidden="true" /> 불러오는 중…
          </p>
        ) : me ? (
          <div className="mt-6 space-y-8">
            {/* ── 내 정보 ── */}
            <section className="space-y-3">
              <h2 className="flex items-center gap-2 text-base font-bold">
                <UserRound size={17} className="text-muted" aria-hidden="true" /> 내 정보
              </h2>
              <div className="flex flex-wrap items-center gap-3 rounded-xl border
                              border-border bg-bg-card/60 p-4">
                {me.user.avatar
                  ? <Image src={me.user.avatar} alt="" width={48} height={48}
                           className="rounded-full" />
                  : <span className="flex h-12 w-12 items-center justify-center
                                     rounded-full bg-accent/20">
                      <UserRound size={20} className="text-accent" aria-hidden="true" />
                    </span>}
                <span className="min-w-0">
                  <span className="block truncate text-sm font-bold text-fg">
                    {me.user.globalName || me.user.username}
                  </span>
                  <span className="block truncate text-xs text-muted">
                    @{me.user.username}
                  </span>
                </span>
                {/* 로그아웃 — **일반 영역**에 둔다. 탈퇴와 멀리 떨어뜨린다. */}
                <button onClick={logout}
                        className="btn-secondary nb-tap ml-auto inline-flex items-center
                                   gap-1.5 text-sm">
                  <LogOut size={14} aria-hidden="true" /> 로그아웃
                </button>
              </div>
              <p className="text-xs leading-relaxed text-muted">
                로그아웃은 이 브라우저의 로그인 상태만 해제합니다. 데이터는 그대로 남습니다.
              </p>
            </section>

            {/* ── 저장된 데이터 ── */}
            <section className="space-y-3">
              <h2 className="flex items-center gap-2 text-base font-bold">
                <Shield size={17} className="text-muted" aria-hidden="true" />
                이 계정과 연결된 데이터
              </h2>
              <p className="text-xs leading-relaxed text-muted">
                종류와 건수만 표시합니다. 값 자체(포인트 수치·경고 사유 등)는 이
                화면에서 제공하지 않습니다.
              </p>
              <ul className="flex flex-col gap-1.5">
                {me.data.classes.map((c) => (
                  <li key={c.label}
                      className="flex flex-wrap items-center gap-2 rounded-lg border
                                 border-border bg-bg-card/60 px-3 py-2">
                    <span className="min-w-0 flex-1 text-sm text-fg">{c.label}</span>
                    <span className="shrink-0 text-sm tabular-nums text-muted">
                      {c.count}건
                    </span>
                    {c.policy === "pending_policy" && (
                      <span className="shrink-0 rounded border border-border px-1.5 py-0.5
                                       text-[10px] font-bold text-muted"
                            title={c.note}>
                        처리 방침 미확정
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              <div className="rounded-lg border border-border/60 bg-bg p-3">
                <p className="text-xs font-semibold text-fg">계정과 연결되지 않는 것</p>
                <ul className="mt-1 space-y-0.5">
                  {me.data.notPersonal.map((n) => (
                    <li key={n.label} className="text-xs leading-relaxed text-muted">
                      · <b className="text-fg/80">{n.label}</b> — {n.note}
                    </li>
                  ))}
                </ul>
              </div>
            </section>

            {/* ── 회원탈퇴 — **맨 아래, 별도 경고 영역** ── */}
            <section className="space-y-3 border-t border-border pt-8">
              {/* 제목·버튼 모두 **'요청'**이다. 지금은 요청 접수까지만 동작하고
                  실제 삭제는 정책 확정 전까지 실행되지 않으므로, '회원탈퇴'라고만
                  적으면 누르면 탈퇴가 되는 것으로 읽힌다. */}
              <h2 className="flex items-center gap-2 text-base font-bold text-red-400">
                <AlertTriangle size={17} aria-hidden="true" /> 회원탈퇴 요청
              </h2>

              {result ? (
                /* **완료로 꾸미지 않는다.** 서버가 준 status를 그대로 읽는다. */
                <div role="status"
                     className={`rounded-xl border p-5 ${
                       result.status === "completed"
                         ? "border-accent/40 bg-accent/5"
                         : "border-amber-500/40 bg-amber-500/5"}`}>
                  {result.status === "completed" ? (
                    <>
                      <p className="flex items-center gap-2 text-sm font-semibold text-accent">
                        <Check size={15} aria-hidden="true" /> 탈퇴 처리가 완료되었습니다.
                      </p>
                      <button onClick={logout}
                              className="btn-secondary nb-tap mt-3 text-sm">
                        로그아웃
                      </button>
                    </>
                  ) : (
                    <>
                      {/* **완료로 읽힐 여지를 남기지 않는다.** 계정과 데이터가
                          아직 그대로라는 것을 첫 문장에서 못박는다. */}
                      <p className="text-sm font-semibold text-amber-400">
                        요청은 접수되었지만{" "}
                        <b>계정과 데이터는 아직 삭제되지 않았습니다.</b>
                      </p>
                      <p className="mt-2 text-sm leading-relaxed text-muted">
                        {result.reason}
                      </p>
                      <p className="mt-2 text-sm leading-relaxed text-muted">
                        지금 상태에서는 로그인과 서비스 이용이 그대로 가능합니다.
                        삭제가 실제로 진행되면 별도로 안내드립니다.
                      </p>
                      {result.blocked?.length > 0 && (
                        <>
                          <p className="mt-3 text-xs font-semibold text-fg">
                            처리 방침이 확정되지 않아 삭제하지 않은 항목
                          </p>
                          <ul className="mt-1 flex flex-wrap gap-1.5">
                            {result.blocked.map((b) => (
                              <li key={b}
                                  className="rounded border border-border px-1.5 py-0.5
                                             text-[11px] text-muted">{b}</li>
                            ))}
                          </ul>
                        </>
                      )}
                      <p className="mt-3 text-xs leading-relaxed text-muted">
                        데이터 삭제를 원하시면 개인정보처리방침에 안내된 방법으로 요청해
                        주세요.{" "}
                        <Link href="/privacy"
                              className="underline underline-offset-2 hover:text-fg">
                          개인정보처리방침 보기
                        </Link>
                      </p>
                    </>
                  )}
                </div>
              ) : (
                <>
                  <div className="rounded-xl border border-red-500/30 bg-red-500/5 p-4">
                    {/* 버튼을 누르면 무슨 일이 일어나는지 **먼저** 적는다.
                        "탈퇴하면 ~된다"가 아니라 "지금은 접수만 된다"가 사실이다. */}
                    {!me.deletion.enabled && (
                      <p className="text-sm font-semibold text-amber-400">
                        이 버튼은 <b>탈퇴 요청을 접수</b>합니다. 누르더라도 계정과
                        데이터는 삭제되지 않습니다.
                      </p>
                    )}
                    <p className="mt-2 text-sm leading-relaxed text-muted">
                      <b className="text-fg">서버(길드)에 남아 있는 기록</b>은 그 서버
                      운영 기록이기도 해서, 어떤 항목을 삭제·보존할지 아직 정해지지
                      않았습니다. 위 목록의 &lsquo;처리 방침 미확정&rsquo; 표시를
                      확인해 주세요. 실제 삭제는 개인정보처리방침에 안내된 절차를 따릅니다.
                    </p>
                  </div>

                  {!openDelete ? (
                    <button onClick={() => setOpenDelete(true)}
                            className="nb-tap inline-flex items-center gap-1.5 rounded-lg
                                       border border-red-500/50 px-3 py-2 text-sm
                                       font-semibold text-red-400 transition-colors
                                       hover:bg-red-500/10">
                      <Trash2 size={14} aria-hidden="true" /> 회원탈퇴 요청하기
                    </button>
                  ) : (
                    <div className="space-y-3 rounded-xl border border-red-500/40 bg-bg-card/60 p-4">
                      {deleteErr && (
                        <p role="alert" className="text-sm text-red-400">{deleteErr}</p>
                      )}
                      <label htmlFor="confirm" className="block text-sm font-semibold text-fg">
                        확인을 위해 사용자 이름{" "}
                        <code className="rounded bg-bg-hover px-1 py-0.5">{username}</code>
                        을(를) 그대로 입력해 주세요.
                      </label>
                      <input id="confirm" value={confirmText} autoComplete="off"
                             onChange={(e) => setConfirmText(e.target.value)}
                             className="w-full rounded-lg border border-border bg-bg px-3
                                        py-2.5 text-sm focus:border-accent focus:outline-none" />
                      <label htmlFor="reason" className="block text-sm font-semibold text-fg">
                        사유 <span className="font-normal text-muted">(선택)</span>
                      </label>
                      <textarea id="reason" value={reason} rows={2} maxLength={200}
                                onChange={(e) => setReason(e.target.value)}
                                className="w-full rounded-lg border border-border bg-bg px-3
                                           py-2.5 text-sm focus:border-accent focus:outline-none" />
                      <div className="flex flex-wrap items-center gap-2">
                        <button onClick={() => void submitDelete()} disabled={!canDelete}
                                aria-busy={sending}
                                className="nb-tap inline-flex items-center gap-1.5 rounded-lg
                                           bg-red-500 px-3 py-2 text-sm font-semibold
                                           text-white transition-colors hover:bg-red-600
                                           disabled:opacity-40">
                          {sending && <Loader2 size={14} className="animate-spin" />}
                          {sending ? "요청 중…" : "탈퇴 요청 보내기"}
                        </button>
                        <button onClick={() => { setOpenDelete(false); setConfirmText(""); }}
                                className="btn-secondary nb-tap text-sm">취소</button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </section>
          </div>
        ) : null}
      </main>

    </div>
  );
}
