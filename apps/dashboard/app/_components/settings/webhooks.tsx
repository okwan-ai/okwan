"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { DeleteButton } from "../delete-button";
import { Button } from "../ui/button";
import { CopyButton } from "../ui/copy-button";

type Added = { id: string; url: string; secret: string };

/** What a person is told when an answer never arrives: the request may have
 *  been done all the same. */
const LOST = "The answer did not arrive.";

/**
 * Add an address for the account's events. The API checks it (HTTPS, port
 * 443, a public host) and answers its signing secret, which is shown here
 * once, as an API key is. "I've stored it" wipes the secret and draws the
 * form anew: React keeps a component's last state until it draws again, so
 * the form that held the secret is thrown away rather than kept beside it.
 * The list re-reads from the API as soon as the address is added.
 */
export function AddWebhook({ full }: { full: boolean }) {
  const [round, setRound] = useState(0);
  return <AddForm key={round} full={full} focus={round > 0} onStored={() => setRound((r) => r + 1)} />;
}

function AddForm({ full, focus, onStored }: { full: boolean; focus: boolean; onStored: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState<Added | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLInputElement>(null);
  const hint = useId();
  // Drawn anew after a secret was stored: focus goes back to the address.
  useEffect(() => {
    if (focus) field.current?.focus();
  }, [focus]);
  // The secret appears where the form was, and focus moves to it.
  useEffect(() => {
    if (added) panel.current?.focus();
  }, [added]);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const url = String(new FormData(e.currentTarget).get("url") ?? "").trim();
    if (!url) return;
    setBusy(true);
    setError(null);
    let res: Response;
    try {
      res = await fetch("/api/webhooks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
    } catch {
      // The address may have been added, with a secret no one has seen.
      setBusy(false);
      setError(`${LOST} If the address is now in the list below, its secret was not shown: delete it and add it again.`);
      router.refresh();
      return;
    }
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) return setError(typeof data.detail === "string" ? data.detail : "it could not be added");
    setAdded({ id: data.id, url: data.url, secret: data.secret });
    router.refresh();
  }

  function stored() {
    if (added) added.secret = "";  // the one object every copy of this state shares
    setAdded(null);
    onStored();
  }

  if (added) {
    return (
      <div ref={panel} tabIndex={-1} role="region" aria-label="Your new webhook's secret" className="rounded-xl border border-ink bg-surface p-5 outline-none">
        <p className="text-sm font-medium">
          Copy this secret now. Your code checks each event&apos;s signature with it, and Okwan can&apos;t show it again.
        </p>
        <div className="mt-3 flex items-center gap-2">
          <code className="field flex-1 overflow-x-auto font-mono text-sm whitespace-nowrap">{added.secret}</code>
          <CopyButton value={added.secret} label="Copy secret" text="Copy" />
        </div>
        <p className="mt-3 text-xs text-ink-soft">
          Events for <span className="break-all font-mono">{added.url}</span> are signed with it. Lost, it can&apos;t be
          recovered: add the address again for a new one and delete this one.
        </p>
        <Button variant="primary" className="mt-5" onClick={stored}>
          I&apos;ve stored it
        </Button>
      </div>
    );
  }

  if (full) {
    return <p className="text-sm text-ink-soft">This workspace has five addresses, the most it can have. Delete one to add another.</p>;
  }

  return (
    <form onSubmit={submit}>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-sm">
          <span className="mb-1 block font-medium">Address</span>
          <input
            ref={field}
            name="url"
            type="url"
            required
            maxLength={2048}
            inputMode="url"
            pattern="https://.+"
            title="An address that starts with https://"
            className="field font-mono"
            placeholder="https://example.com/okwan/events"
            autoComplete="off"
            spellCheck={false}
            aria-describedby={hint}
          />
        </label>
        <Button type="submit" variant="primary" disabled={busy}>{busy ? "Adding…" : "Add"}</Button>
      </div>
      <p id={hint} className="mt-2 text-xs text-ink-soft">HTTPS on port 443, to a host on the public internet.</p>
      {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
    </form>
  );
}

type Tried = { delivered: boolean; status: number | null; error: string | null };

/** What can be done to one address: send it a signed ping now (whether it
 *  is on or off), turn it back on when Okwan turned it off, delete it. */
export function WebhookActions({ id, enabled }: { id: string; enabled: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState<"test" | "enable" | null>(null);
  const [tried, setTried] = useState<Tried | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(action: "test" | "enable") {
    setBusy(action);
    setError(null);
    if (action === "test") setTried(null);
    let res: Response;
    try {
      res = await fetch(`/api/webhooks/${encodeURIComponent(id)}/${action}`, { method: "POST" });
    } catch {
      setBusy(null);
      setError(action === "test" ? `${LOST} Send the test again.` : `${LOST} The list shows whether it is on.`);
      if (action === "enable") router.refresh();
      return;
    }
    const data = await res.json().catch(() => ({}));
    setBusy(null);
    if (!res.ok) return setError(typeof data.detail === "string" ? data.detail : "something went wrong");
    if (action === "test") {
      setTried({ delivered: data.delivered === true, status: typeof data.status === "number" ? data.status : null,
                 error: typeof data.error === "string" ? data.error : null });
    } else {
      router.refresh();
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* A test goes to an address that is off too: it is how a person sees it
          answer again before turning it back on. */}
      <Button variant="secondary" onClick={() => act("test")} disabled={busy !== null}>
        {busy === "test" ? "Sending…" : "Send a test"}
      </Button>
      {!enabled && (
        <Button variant="primary" onClick={() => act("enable")} disabled={busy !== null}>
          {busy === "enable" ? "Turning on…" : "Turn back on"}
        </Button>
      )}
      <span className="ml-auto">
        <DeleteButton url={`/api/webhooks/${encodeURIComponent(id)}`} what="this webhook address" />
      </span>
      {tried && (
        <p role="status" className={`basis-full text-sm ${tried.delivered ? "text-ok" : "text-danger"}`}>
          {tried.delivered
            ? <><span aria-hidden className="mr-1 font-mono">✓</span>Delivered: your address answered {tried.status}.</>
            : <>Not delivered: {tried.error ?? (tried.status !== null ? `your address answered ${tried.status}` : "no answer")}.</>}
        </p>
      )}
      {error && <p role="alert" className="basis-full text-sm text-danger">{error}</p>}
    </div>
  );
}
