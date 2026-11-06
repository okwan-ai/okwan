"use client";

import Link from "next/link";
import { useState } from "react";

type Mode = "signup" | "signin";

/** The sign-in and sign-up card, as the owner drew it: fields with their
 *  icon inside, a password you can show, one full-width action. */
export function AuthForm({ mode }: { mode: Mode }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [shown, setShown] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    const res = await fetch(mode === "signup" ? "/api/signup" : "/api/signin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(data.detail ?? "something went wrong");
    if (mode === "signup") return setSent(true);
    window.location.href = "/overview";
  }

  if (sent) {
    return (
      <div className={CARD}>
        <h2 className="text-[22px] font-bold">Check your inbox</h2>
        <p className="mt-3 text-[16px] text-ink-soft 2xl:text-[18px]">
          If this address can be registered, a verification link is on its way. Your
          account and workspace are created when you open it. The link expires in 24
          hours.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className={CARD}>
      <label className="block">
        <span className="text-[17px] font-bold 2xl:text-[21px]">Work email</span>
        <span className="relative mt-2 block">
          <span aria-hidden className="pointer-events-none absolute top-1/2 left-[19px] -translate-y-1/2 text-ink"><IconMail /></span>
          <input name="email" type="email" required autoComplete="email" className={FIELD} />
        </span>
      </label>
      <label className="mt-[26px] block">
        <span className="text-[17px] font-bold 2xl:text-[21px]">Password</span>
        <span className="relative mt-2 block">
          <span aria-hidden className="pointer-events-none absolute top-1/2 left-[19px] -translate-y-1/2 text-ink"><IconLock /></span>
          <input
            name="password"
            type={shown ? "text" : "password"}
            required
            minLength={mode === "signup" ? 12 : 1}
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            className={`${FIELD} pr-16`}
          />
          <button
            type="button"
            onClick={() => setShown((v) => !v)}
            aria-label={shown ? "Hide password" : "Show password"}
            aria-pressed={shown}
            className="absolute top-1/2 right-2 inline-flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-xl text-ink hover:bg-white/70"
          >
            <IconEye crossed={!shown} />
          </button>
        </span>
        {mode === "signup" && (
          <span className="mt-2 block text-[14px] text-ink-soft">At least 12 characters.</span>
        )}
      </label>
      {error && <p role="alert" className="mt-5 text-[15px] text-danger">{error}</p>}
      {mode === "signup" && (
        <p className="mt-5 text-[14px] text-ink-soft">
          By creating an account, you agree to Okwan&apos;s{" "}
          <a href="https://okwan.ai/terms" target="_blank" rel="noopener" className="text-[#37139a] underline underline-offset-2">Terms of Service</a>{" "}
          and{" "}
          <a href="https://okwan.ai/privacy" target="_blank" rel="noopener" className="text-[#37139a] underline underline-offset-2">Privacy Policy</a>.
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="mt-[37px] flex h-[65px] w-full items-center justify-center gap-4 rounded-[14px] bg-[linear-gradient(100deg,#6d35ec_0%,#4520b0_50%,#2e1585_100%)] text-[19px] font-semibold text-white shadow-[0_18px_34px_-18px_rgba(46,21,133,0.9)] hover:brightness-110 disabled:opacity-70 2xl:text-[23px]"
      >
        {busy ? "…" : mode === "signup" ? "Create account" : "Sign in"}
        {!busy && (
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M4.5 12h14.5" /><path d="m13.5 6 6 6-6 6" />
          </svg>
        )}
      </button>
      <p className="mt-5 text-center text-[16px] 2xl:text-[21px]">
        {mode === "signup" ? "Have an account?" : "New here?"}{" "}
        <Link
          href={mode === "signup" ? "/signup?mode=signin" : "/signup"}
          className="inline-flex min-h-11 items-center font-semibold text-[#37139a] underline underline-offset-4 hover:text-accent"
        >
          {mode === "signup" ? "Sign in" : "Create an account"}
        </Link>
      </p>
    </form>
  );
}

const CARD = "rounded-[28px] border border-white/90 bg-white/90 px-6 pt-[28px] pb-3 shadow-[0_30px_60px_-34px_rgba(52,40,160,0.35)] backdrop-blur-xl sm:px-[41px]";
const FIELD = "h-[61px] w-full rounded-[14px] border border-[#dde0f5] bg-[#ebedfd] pr-5 pl-[68px] text-[17px] text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/25 2xl:text-[21px]";

function IconMail() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2.2" /><path d="m3.6 6.4 8.4 6.4 8.4-6.4" />
    </svg>
  );
}
function IconLock() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      <rect x="5" y="10.5" width="14" height="10" rx="2.2" /><path d="M8.2 10.5V7.8a3.8 3.8 0 0 1 7.6 0v2.7" /><path d="M12 14.6v2" />
    </svg>
  );
}
function IconEye({ crossed }: { crossed: boolean }) {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M2.5 12s3.5-6.5 9.5-6.5S21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="3" />
      {crossed && <path d="M4 4.5 20 19.5" />}
    </svg>
  );
}
