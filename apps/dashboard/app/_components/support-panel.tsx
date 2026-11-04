"use client";

import { useEffect, useRef, useState } from "react";
import type { SupportReply } from "../api/support/route";
import { Button } from "./ui/button";
import { useDialog } from "./ui/dialog";
import { IconClose, IconHelp, IconSend } from "./ui/icons";
import { Mark } from "./ui/mark";
import { Rich } from "./ui/rich-text";

/**
 * Okwan Support: the first responder, on every signed-in page. The
 * assistant reads this account (plan, connections, runs, keys) and changes
 * nothing; whatever it cannot settle goes to a person, and it says so.
 * The transcript is kept by the API; this panel only shows it.
 */

type Line =
  | { from: "you"; text: string }
  | { from: "okwan"; text: string; ticket?: string | null }
  | { from: "notice"; tone: "warn" | "ok" | "error"; text: string };

const STARTERS = [
  "Why is my Stripe connection failing?",
  "How do I connect Claude Code to Okwan?",
  "Why did I get a 402?",
];

export function SupportPanel() {
  const [open, setOpen] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversation, setConversation] = useState<string | null>(null);
  const panel = useDialog(open, () => setOpen(false));
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [lines, busy]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setLines((l) => [...l, { from: "you", text: message }]);
    setDraft("");
    setBusy(true);
    const res = await fetch("/api/support", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, conversation_id: conversation }),
    }).catch(() => null);
    const data = (await res?.json().catch(() => ({}))) as Partial<SupportReply> & { detail?: string };
    setBusy(false);
    if (!res || !res.ok) {
      const full = res?.status === 409;
      if (full) setConversation(null);
      setLines((l) => [...l, {
        from: "notice", tone: "error",
        text: full
          ? "This conversation is full. Your next message starts a new one."
          : data.detail ?? "Support is unreachable right now. Try again in a minute.",
      }]);
      return;
    }
    setConversation(data.conversation_id ?? null);
    setLines((l) => [
      // Show the customer their message as kept: a pasted secret does not
      // stay on screen, where a screenshot would carry it.
      ...(data.removed?.length && data.message
        ? l.map((line, i) => (i === l.length - 1 && line.from === "you" ? { ...line, text: data.message! } : line))
        : l),
      ...(data.removed?.length
        ? [{
            from: "notice" as const, tone: "warn" as const,
            text: `We removed ${data.removed.join(" and ")} from your message before anyone saw it. ` +
              "Replace it where it was issued: it is no longer safe.",
          }]
        : []),
      { from: "okwan", text: data.reply ?? "", ticket: data.ticket },
    ]);
  }

  function restart() {
    setLines([]);
    setConversation(null);
    setDraft("");
  }

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="fixed right-4 bottom-4 z-40 inline-flex min-h-12 items-center gap-2 rounded-full bg-ink px-5 text-sm font-medium text-white shadow-[0_6px_24px_rgba(0,0,0,0.18)] hover:bg-[#262626] sm:right-6 sm:bottom-6"
        >
          <IconHelp />
          Ask Okwan
        </button>
      )}
      {open && (
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-night/40" onClick={() => setOpen(false)} aria-hidden />
          <div
            ref={panel}
            role="dialog"
            aria-modal="true"
            aria-labelledby="support-title"
            className="absolute inset-y-0 right-0 flex w-full max-w-[440px] flex-col border-l border-line bg-surface shadow-xl"
          >
            <header className="flex items-start justify-between gap-3 bg-night px-5 py-4 text-white">
              <div className="flex min-w-0 items-center gap-3">
                <Mark size={26} light />
                <div className="min-w-0">
                  <h2 id="support-title" className="text-base font-semibold">Okwan Support</h2>
                  <p className="mt-0.5 text-[13px] text-night-soft">
                    AI assistant · reads your account, changes nothing
                  </p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {lines.length > 0 && (
                  <button
                    type="button"
                    onClick={restart}
                    className="inline-flex min-h-11 items-center rounded-lg px-3 text-[13px] text-night-soft hover:bg-white/10 hover:text-white"
                  >
                    New chat
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  aria-label="Close support"
                  className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-night-soft hover:bg-white/10 hover:text-white"
                >
                  <IconClose />
                </button>
              </div>
            </header>

            <div className="flex-1 overflow-y-auto bg-canvas px-5 py-5" aria-live="polite">
              {lines.length === 0 ? (
                <Intro onPick={(s) => void send(s)} />
              ) : (
                <ol className="space-y-4">
                  {lines.map((line, i) => (
                    <li key={i}>
                      <Bubble line={line} />
                    </li>
                  ))}
                  {busy && (
                    <li className="flex items-center gap-2 text-sm text-ink-muted">
                      <span className="inline-flex gap-1" aria-hidden>
                        {[0, 1, 2].map((d) => (
                          <span
                            key={d}
                            className="h-1.5 w-1.5 rounded-full bg-ink-muted motion-safe:animate-pulse"
                            style={{ animationDelay: `${d * 160}ms` }}
                          />
                        ))}
                      </span>
                      Looking at your account…
                    </li>
                  )}
                </ol>
              )}
              <div ref={end} />
            </div>

            <form
              className="border-t border-line bg-surface px-4 pt-3 pb-4"
              onSubmit={(e) => {
                e.preventDefault();
                void send(draft);
              }}
            >
              <label htmlFor="support-message" className="sr-only">Message Okwan Support</label>
              <div className="flex items-end gap-2">
                <textarea
                  id="support-message"
                  data-autofocus
                  rows={2}
                  maxLength={4000}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void send(draft);
                    }
                  }}
                  placeholder="Ask about your connections, runs, keys or plan"
                  className="max-h-40 min-h-11 flex-1 resize-none rounded-lg border border-line-strong/40 bg-surface px-3 py-2.5 text-sm text-ink placeholder:text-ink-muted focus:border-ink focus:outline-none"
                />
                <Button type="submit" variant="primary" disabled={busy || !draft.trim()} aria-label="Send">
                  <IconSend />
                </Button>
              </div>
              <p className="mt-2 text-xs text-ink-muted">
                Never paste keys or passwords. A person at Okwan takes over anything the assistant can't settle.
              </p>
            </form>
          </div>
        </div>
      )}
    </>
  );
}

function Intro({ onPick }: { onPick: (s: string) => void }) {
  return (
    <div>
      <p className="text-[15px] leading-relaxed text-ink">
        Ask about your account. The assistant checks your plan, connections, runs and keys as you ask,
        and explains the fix.
      </p>
      <ul className="mt-5 space-y-2">
        {STARTERS.map((s) => (
          <li key={s}>
            <button
              type="button"
              onClick={() => onPick(s)}
              className="w-full rounded-lg border border-line bg-surface px-4 py-3 text-left text-sm text-ink hover:border-ink/40"
            >
              {s}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Bubble({ line }: { line: Line }) {
  if (line.from === "you") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-md bg-ink px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap text-white">
          {line.text}
        </p>
      </div>
    );
  }
  if (line.from === "notice") {
    const tone = {
      warn: "border-danger/30 bg-danger-soft text-danger",
      error: "border-danger/30 bg-danger-soft text-danger",
      ok: "border-ok/30 bg-ok-soft text-ok",
    }[line.tone];
    return <p role="status" className={`rounded-lg border px-3 py-2 text-[13px] ${tone}`}>{line.text}</p>;
  }
  return (
    <div className="max-w-[92%]">
      <div className="rounded-2xl rounded-bl-md border border-line bg-surface px-4 py-3 text-sm leading-relaxed text-ink">
        <Rich text={line.text} />
      </div>
      {line.ticket && (
        <p className="mt-2 rounded-lg border border-ok/30 bg-ok-soft px-3 py-2 text-[13px] text-ok">
          Passed to a person at Okwan · <span className="font-mono">{line.ticket}</span>
        </p>
      )}
    </div>
  );
}
