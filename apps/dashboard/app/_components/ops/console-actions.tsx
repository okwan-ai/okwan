"use client";

import { useRouter } from "next/navigation";
import { useId, useState } from "react";
import { Button } from "../ui/button";
import { IconLock, IconSend } from "../ui/icons";

async function post(path: string, body: unknown = {}): Promise<{ ok: boolean; detail?: string; data?: Record<string, unknown> }> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return res.ok ? { ok: true, data } : { ok: false, detail: data.detail ?? "something went wrong" };
}

/** The second factor: six digits from the authenticator app, for 30 minutes. */
export function UnlockForm({ email }: { email: string }) {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hint = useId();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const r = await post("/api/ops/unlock", { code });
    setBusy(false);
    if (!r.ok) {
      setCode("");
      return setError(r.detail ?? "that code didn't work");
    }
    router.refresh();
  }

  return (
    <form onSubmit={submit} className="max-w-md rounded-xl border border-line bg-surface p-6">
      <div className="flex items-center gap-2 text-ink-soft"><IconLock /> <span className="text-sm">Locked</span></div>
      <h2 className="mt-3 text-lg font-semibold tracking-tight">Enter your authenticator code</h2>
      <p id={hint} className="mt-1 text-sm text-ink-soft">
        The 6-digit code for Okwan ({email}) in your authenticator app. The console stays open for 30 minutes.
      </p>
      <label className="mt-5 block text-sm font-medium" htmlFor="ops-code">Code</label>
      <div className="mt-1.5 flex flex-wrap items-center gap-3">
        <input
          id="ops-code"
          className="field w-40 font-mono text-lg tracking-[0.3em]"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="\d{6}"
          maxLength={7}
          required
          autoFocus
          aria-describedby={hint}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/[^\d]/g, "").slice(0, 6))}
        />
        <Button type="submit" variant="primary" disabled={busy || code.length !== 6}>
          {busy ? "Checking…" : "Unlock"}
        </Button>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
    </form>
  );
}

export function LockButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      variant="secondary"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        await post("/api/ops/lock");
        router.refresh();
      }}
    >
      <IconLock /> Lock now
    </Button>
  );
}

/**
 * The reply, prefilled with the assistant's draft. Sending emails it to the
 * customer from Okwan Support; their answer comes back to the owner's inbox.
 */
export function ReplyEditor({ ticketId, draft, contact, status }: {
  ticketId: string;
  draft: string;
  contact: string | null;
  status: "open" | "answered" | "resolved";
}) {
  const router = useRouter();
  const [body, setBody] = useState(draft);
  const [busy, setBusy] = useState<null | "send" | "resolve" | "status">(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const note = useId();

  async function send(resolve: boolean) {
    setBusy(resolve ? "resolve" : "send");
    setError(null);
    setDone(null);
    const r = await post(`/api/ops/tickets/${ticketId}/reply`, { body, resolve });
    setBusy(null);
    if (!r.ok) return setError(r.detail ?? "the reply was not sent");
    setDone(`Sent to ${contact}.${resolve ? " The ticket is resolved." : ""}`);
    setBody("");
    router.refresh();
  }

  async function setStatus(next: "open" | "resolved") {
    setBusy("status");
    setError(null);
    setDone(null);
    const r = await post(`/api/ops/tickets/${ticketId}/status`, { status: next });
    setBusy(null);
    if (!r.ok) return setError(r.detail ?? "the status didn't change");
    setDone(next === "resolved" ? "Resolved." : "Reopened.");
    router.refresh();
  }

  return (
    <section aria-labelledby="reply-title" className="rounded-xl border border-line bg-surface p-5 sm:p-6">
      <h2 id="reply-title" className="text-base font-semibold">Reply to the customer</h2>
      {contact ? (
        <>
          <p id={note} className="mt-1 text-sm text-ink-soft">
            To <span className="font-medium text-ink">{contact}</span>, from Okwan Support. Their answer comes to your inbox.
            The assistant&apos;s draft is filled in; edit it before sending.
          </p>
          <label htmlFor="reply-body" className="sr-only">Reply</label>
          <textarea
            id="reply-body"
            className="field mt-4 w-full resize-y text-sm leading-relaxed"
            rows={Math.min(24, Math.max(8, draft.split("\n").length + 2))}
            value={body}
            maxLength={8000}
            aria-describedby={note}
            onChange={(e) => setBody(e.target.value)}
          />
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="primary" disabled={busy !== null || !body.trim()} onClick={() => send(false)}>
              <IconSend /> {busy === "send" ? "Sending…" : "Send reply"}
            </Button>
            <Button variant="secondary" disabled={busy !== null || !body.trim()} onClick={() => send(true)}>
              {busy === "resolve" ? "Sending…" : "Send and resolve"}
            </Button>
            <span className="flex-1" />
            {status === "resolved" ? (
              <Button variant="ghost" disabled={busy !== null} onClick={() => setStatus("open")}>Reopen</Button>
            ) : (
              <Button variant="ghost" disabled={busy !== null} onClick={() => setStatus("resolved")}>Resolve without replying</Button>
            )}
          </div>
        </>
      ) : (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-ink-soft">This account has no sign-in address, so there is no one to email.</p>
          {status === "resolved" ? (
            <Button variant="secondary" disabled={busy !== null} onClick={() => setStatus("open")}>Reopen</Button>
          ) : (
            <Button variant="secondary" disabled={busy !== null} onClick={() => setStatus("resolved")}>Resolve</Button>
          )}
        </div>
      )}
      <div aria-live="polite">
        {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
        {done && <p className="mt-3 text-sm text-ok">{done}</p>}
      </div>
    </section>
  );
}
