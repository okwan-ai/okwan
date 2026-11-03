import type { ReactNode } from "react";
import { PathGlyph } from "./ui/path-glyph";

/**
 * The one drawing of a verdict: collected twice on the left (night, with the
 * signal dot and the fork path, only when there is something to act on),
 * every order checked on the right. Used by
 * Overview (workspace) and the merchant's Findings tab, so the figure is the
 * same size on both. Presentational only: no hooks, no server-only imports,
 * and never a logo.
 */
export function VerdictCard({ ariaLabel, twice, figure, compactFigure = false, sub, details, leftFooter, counts, spectrum, footer }: {
  ariaLabel: string;
  /** Collected twice > 0: the left panel is the view's night element. */
  twice: boolean;
  figure: ReactNode;
  /** Two or more currencies side by side. */
  compactFigure?: boolean;
  sub: ReactNode;
  details?: ReactNode;
  leftFooter?: ReactNode;
  counts: ReactNode;
  spectrum: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <section aria-label={ariaLabel} className="grid overflow-hidden rounded-[14px] border border-line bg-surface shadow-[0_1px_2px_rgba(0,0,0,0.04)] md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div
        className={`relative flex flex-col px-6 py-6 ${twice
          // Slot content inherits: links and summaries go white, controls light.
          ? "border-night bg-night text-white [&_a]:text-white [&_button]:text-night-ink [&_summary]:text-white"
          : "border-b border-line text-ink md:border-r md:border-b-0"}`}
      >
        <div className="flex items-start justify-between gap-4">
          <p className={`flex items-center gap-2 text-xs font-medium ${twice ? "text-night-soft" : "text-ink-soft"}`}>
            {twice && <span aria-hidden className="h-2 w-2 rounded-full bg-signal" />}
            Collected twice
          </p>
          {twice && <PathGlyph shape="twice" light className="-mt-1 -mr-1 h-9 w-24" />}
        </div>
        <p className={`mt-2 font-semibold tracking-[-0.03em] tabular-nums ${compactFigure ? "text-3xl" : "text-4xl sm:text-[52px] sm:leading-[1.05]"}`}>{figure}</p>
        <p className={`mt-2 text-sm ${twice ? "text-night-ink" : ""}`}>{sub}</p>
        {details}
        {leftFooter && <div className="mt-auto pt-4">{leftFooter}</div>}
      </div>
      <div className="flex min-w-0 flex-col gap-4 px-6 py-6">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">{counts}</div>
        {spectrum}
        {footer && <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-soft">{footer}</div>}
      </div>
    </section>
  );
}
