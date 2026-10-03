import type { ReactNode } from "react";

export type Tone = "danger" | "warn" | "ok" | "neutral" | "ink";

const TONE: Record<Tone, string> = {
  danger: "border-danger/30 bg-danger-soft text-danger",
  // Not volt: volt is for primary actions and the one hero figure (§2).
  // The symbol and label carry "warn"; the stronger border sets it apart.
  warn: "border-ink-soft/50 bg-surface text-ink",
  ok: "border-ok/30 bg-ok-soft text-ok",
  neutral: "border-line bg-canvas text-ink-soft",
  ink: "border-ink bg-ink text-canvas",
};

/** Status is a symbol and a label; the tone only reinforces them. */
export function Badge({
  tone = "neutral",
  symbol,
  children,
  title,
}: {
  tone?: Tone;
  symbol?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${TONE[tone]}`}
    >
      {symbol && <span aria-hidden className="font-mono text-[11px] leading-none">{symbol}</span>}
      {children}
    </span>
  );
}
