import type { ReactNode } from "react";

export type Tone = "danger" | "warn" | "ok" | "neutral" | "ink";

const TONE: Record<Tone, string> = {
  danger: "border-transparent bg-danger-soft text-danger",
  // Not the signal: yellow means only "these systems disagree".
  // The symbol and label carry "warn"; the stronger border sets it apart.
  warn: "border-ink-soft/40 bg-surface text-ink",
  ok: "border-transparent bg-ok-soft text-ok",
  neutral: "border-line bg-surface text-ink-soft",
  ink: "border-transparent bg-accent text-white",
};

/** Status is a symbol and a label; the tone only reinforces them. */
export function Badge({
  tone = "neutral",
  symbol,
  children,
  title,
  size = "sm",
}: {
  tone?: Tone;
  symbol?: string;
  children: ReactNode;
  title?: string;
  /** "lg": the Overview's outcome pill, as drawn. */
  size?: "sm" | "lg";
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border whitespace-nowrap ${
        size === "lg" ? "px-4 py-[7px] text-[15px] font-medium 2xl:text-[17px]" : "px-2.5 py-1 text-xs font-semibold"
      } ${TONE[tone]}`}
    >
      {symbol && <span aria-hidden className="font-mono text-[11px] leading-none">{symbol}</span>}
      {children}
    </span>
  );
}
