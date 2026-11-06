"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import type { SupportReply } from "../api/support/route";
import { useDialog } from "./ui/dialog";
import { IconClose, IconPaperPlane } from "./ui/icons";
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

/** Opens the panel from anywhere: the top bar's Ask Okwan, the phone bar. */
export function openSupport() {
  window.dispatchEvent(new Event("okwan:support"));
}

/** `topBar`: from 2xl the right column holds the account bar above the
 *  panel, as in the owner's design; below that the page has it. */
export function SupportPanel({ topBar }: { topBar?: ReactNode }) {
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

  useEffect(() => {
    const onOpen = () => setOpen(true);
    window.addEventListener("okwan:support", onOpen);
    return () => window.removeEventListener("okwan:support", onOpen);
  }, []);

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

  const close = () => setOpen(false);

  return (
    <>
      {/* Below 2xl: a slide-over opened by the button. From 2xl: docked
          beside the page, always there, as in the owner's design. */}
      <div className={`${open ? "fixed inset-0 z-50" : "hidden"} 2xl:static 2xl:z-auto 2xl:mr-4 2xl:ml-7 2xl:block 2xl:w-[405px] 2xl:shrink-0`}>
        <div className="absolute inset-0 bg-night/40 2xl:hidden" onClick={close} aria-hidden />
        <div
          ref={panel}
          role={open ? "dialog" : "complementary"}
          aria-modal={open ? true : undefined}
          aria-labelledby="support-title"
          className="absolute inset-y-0 right-0 flex w-full max-w-[440px] flex-col 2xl:sticky 2xl:top-0 2xl:h-screen 2xl:max-w-none 2xl:pt-[22px] 2xl:pb-5"
        >
          {topBar && <div className="hidden 2xl:block">{topBar}</div>}
          <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-white shadow-xl 2xl:mt-[25px] 2xl:rounded-[30px] 2xl:border 2xl:border-white/90 2xl:bg-white/80 2xl:shadow-float 2xl:backdrop-blur-xl">
            <header className="px-[21px] pt-7 pb-4">
              <div className="flex items-start justify-between gap-3">
              <h2 id="support-title" className="text-[26px] leading-tight font-extrabold tracking-[-0.01em] 2xl:text-[30px]">Okwan Support</h2>
              <div className="-mt-1 -mr-2 flex shrink-0 items-center gap-1">
                {lines.length > 0 && (
                  <button
                    type="button"
                    onClick={restart}
                    className="inline-flex min-h-11 items-center rounded-xl px-2.5 text-[13px] font-medium text-ink-muted hover:bg-accent-soft hover:text-accent"
                  >
                    New chat
                  </button>
                )}
                <button
                  type="button"
                  onClick={close}
                  aria-label="Close support"
                  className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-xl text-ink-soft hover:bg-accent-soft hover:text-accent 2xl:hidden"
                >
                  <IconClose />
                </button>
              </div>
              </div>
              <p className="mt-1 text-[15px] text-ink-soft 2xl:text-[16px]">AI assistant · reads your account, changes nothing</p>
            </header>

            <div className="flex-1 overflow-y-auto px-[21px] pb-5" aria-live="polite">
              {lines.length === 0 ? (
                <Intro onPick={(s) => void send(s)} />
              ) : (
                <ol className="space-y-5">
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
                            className="h-1.5 w-1.5 rounded-full bg-accent motion-safe:animate-pulse"
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
              className="pt-3 pr-[13px] pb-6 pl-[21px]"
              onSubmit={(e) => {
                e.preventDefault();
                void send(draft);
              }}
            >
              <label htmlFor="support-message" className="sr-only">Message Okwan Support</label>
              <div className="flex items-end gap-3">
                <textarea
                  id="support-message"
                  data-autofocus
                  rows={1}
                  maxLength={4000}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void send(draft);
                    }
                  }}
                  placeholder="Ask about your connections, runs, keys…"
                  className="max-h-40 min-h-[50px] min-w-0 flex-1 resize-none rounded-[16px] border border-[#dcdcf2] bg-white px-4 py-[13px] text-[15px] leading-[22px] text-ink shadow-[0_6px_18px_-12px_rgba(52,40,160,0.35)] placeholder:text-ink-soft focus:border-accent focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={busy || !draft.trim()}
                  aria-label="Send"
                  className="inline-flex h-[52px] w-[52px] shrink-0 items-center justify-center rounded-[14px] bg-violet text-white shadow-lift hover:brightness-110 disabled:cursor-default disabled:hover:brightness-100"
                >
                  <IconPaperPlane />
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </>
  );
}

function Intro({ onPick }: { onPick: (s: string) => void }) {
  return (
    <div>
      <p className="text-[16px] leading-[1.4] text-ink 2xl:text-[17px]">
        Ask about your account. The assistant checks your plan, connections, runs and keys as you ask,
        and explains the fix.
      </p>
      <p className="mt-3 text-[14px] leading-snug text-ink-muted">
        Never paste keys or passwords. A person at Okwan takes over anything the assistant can&apos;t settle.
      </p>
      <ul className="mt-5 flex flex-col items-end gap-2.5">
        {STARTERS.map((s) => (
          <li key={s}>
            <button
              type="button"
              onClick={() => onPick(s)}
              className="rounded-[16px] bg-accent-soft px-[18px] py-[11px] text-left text-[16px] leading-snug font-bold text-accent-ink hover:bg-[#dccdfc]"
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
        <p className="max-w-[90%] rounded-[16px] bg-accent-soft px-[18px] py-[11px] text-[16px] leading-snug font-bold whitespace-pre-wrap text-accent-ink">
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
    <div>
      <div className="text-[16px] leading-[1.35] text-ink 2xl:text-[17px]">
        <Rich text={line.text} numbered />
      </div>
      {line.ticket && (
        <p className="mt-2 rounded-lg border border-ok/30 bg-ok-soft px-3 py-2 text-[13px] text-ok">
          Passed to a person at Okwan · <span className="font-mono">{line.ticket}</span>
        </p>
      )}
    </div>
  );
}
