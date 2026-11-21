"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { type ApiDecision, decisionShown, type DecisionShown, type Line, type View } from "@/lib/exceptions";
import { Button } from "./ui/button";
import { SlideOver } from "./ui/dialog";

type Choice = "explained" | "resolved" | "assigned" | "open";

/** What a person can decide, from each list. */
const CHOICES: Record<View, { value: Choice; label: string; hint: string }[]> = {
  open: [
    { value: "explained", label: "Explain it", hint: "Nothing to fix. Say why, and it leaves the list." },
    { value: "resolved", label: "Resolve it", hint: "Fixed where it started. Say how, and it leaves the list." },
    { value: "assigned", label: "Give it to someone", hint: "It stays open, with their name on it." },
  ],
  closed: [{ value: "open", label: "Open it again", hint: "Say why, and it is back on the list." }],
};

const OWNER_MAX = 80;
const NOTE_MAX = 1000;

/** A person's own text (a name, a note), drawn as data in an element and a
 *  direction of its own, never inside a sentence the page writes. */
export function Written({ value, what }: { value: string; what: "owner" | "note" | "by" }) {
  return what === "note"
    ? <bdi dir="auto" data-note className="whitespace-pre-line break-words">{value}</bdi>
    : <bdi dir="auto" data-person={what} className="font-medium text-ink">{value}</bdi>;
}

/** One decision, in a person's words: who decided what, how, when, and why. */
export function Decided({ d }: { d: DecisionShown }) {
  return (
    <div className="text-sm text-ink-soft" data-decision={d.status}>
      <p>
        {d.said}
        {d.status === "assigned" && d.owner && <> <Written value={d.owner} what="owner" /></>}
        {" by "}<Written value={d.by} what="by" /> {d.how}{d.on && <>, {d.on}</>}
      </p>
      {d.note && (
        <p className="mt-1 rounded-lg bg-canvas px-3 py-2 text-ink"><Written value={d.note} what="note" /></p>
      )}
      {d.changed && <p className="mt-1 font-medium text-danger" data-changed>{d.changed}</p>}
    </div>
  );
}

function History({ path }: { path: string }) {
  const [found, setFound] = useState<DecisionShown[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    fetch(path, { cache: "no-store" })
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        if (!live) return;
        if (!r.ok) return setFailed(true);
        setFound(((data.data ?? []) as ApiDecision[]).map(decisionShown));
      })
      .catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [path]);
  return (
    <section className="mt-8 border-t border-line pt-5" aria-label="What was decided">
      <h3 className="text-sm font-semibold text-ink">What was decided, newest first</h3>
      {failed ? (
        <p className="mt-2 text-sm text-ink-soft">The history didn&apos;t load. Try again in a moment.</p>
      ) : found === null ? (
        <p className="mt-2 text-sm text-ink-soft">Loading…</p>
      ) : found.length === 0 ? (
        <p className="mt-2 text-sm text-ink-soft">No one has decided anything about this item yet.</p>
      ) : (
        <ol className="mt-3 grid gap-4" data-history>
          {found.map((d, n) => <li key={n}><Decided d={d} /></li>)}
        </ol>
      )}
    </section>
  );
}

function DecideForm({ line, view, onDone }: { line: Line; view: View; onDone: () => void }) {
  const choices = CHOICES[view];
  const [choice, setChoice] = useState<Choice>(choices[0].value);
  const [owner, setOwner] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // One key for this decision: a retry after a lost answer sends it again,
  // and the API answers with the decision already made, never a second.
  const idem = useMemo(() => crypto.randomUUID(), []);
  const needsOwner = choice === "assigned";
  const ready = needsOwner ? owner.trim() !== "" : note.trim() !== "";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(line.decide, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idem },
        body: JSON.stringify({ status: choice, owner: owner.trim() || undefined, note: note.trim() || undefined }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return setError(data.detail ?? "The decision was not recorded. Try again.");
      onDone();
    } catch {
      setError("The decision was not recorded: the dashboard could not reach Okwan. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-5" data-decide-form>
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-semibold text-ink">What do you decide?</legend>
        {choices.map((c) => (
          <label key={c.value} className="flex cursor-pointer items-start gap-3 rounded-xl border border-line px-4 py-3 has-[:checked]:border-accent has-[:checked]:bg-accent-soft">
            <input type="radio" name="choice" value={c.value} checked={choice === c.value}
              onChange={() => setChoice(c.value)} className="mt-1" />
            <span>
              <span className="block text-sm font-semibold text-ink">{c.label}</span>
              <span className="block text-sm text-ink-soft">{c.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {needsOwner && (
        <label className="grid gap-1.5 text-sm">
          <span className="font-semibold text-ink">Give it to</span>
          <input name="owner" value={owner} onChange={(e) => setOwner(e.target.value)} maxLength={OWNER_MAX}
            required autoComplete="off" className="min-h-11 rounded-xl border border-line bg-surface px-3" />
        </label>
      )}
      <label className="grid gap-1.5 text-sm">
        <span className="font-semibold text-ink">{needsOwner ? "A note (if you want one)" : "Why"}</span>
        <textarea name="note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={NOTE_MAX}
          required={!needsOwner} rows={4} className="rounded-xl border border-line bg-surface px-3 py-2" />
        <span className="text-ink-soft">
          Kept with the item as you write it, with your account&apos;s name and the date. It is never changed later.
        </span>
      </label>
      {error && <p role="alert" className="text-sm font-medium text-danger">{error}</p>}
      <div className="flex justify-end">
        <Button type="submit" variant="primary" disabled={!ready || busy}>
          {busy ? "Recording…" : "Record the decision"}
        </Button>
      </div>
    </form>
  );
}

/** The item's Decide button, and the panel it opens: the choices, the
 *  reason, and what was decided before. */
export function DecideItem({ line, view }: { line: Line; view: View }) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  return (
    <>
      <Button onClick={() => setOpen(true)} data-decide>{view === "open" ? "Decide" : "Open again"}</Button>
      <SlideOver open={open} onClose={() => setOpen(false)} title={view === "open" ? "Decide this item" : "Open this item again"}
        description={<>{line.what} · {line.check}{line.amount && <> · {line.amount}</>}</>}>
        <DecideForm line={line} view={view} onDone={() => { setOpen(false); router.refresh(); }} />
        <History path={line.decide} />
      </SlideOver>
    </>
  );
}
