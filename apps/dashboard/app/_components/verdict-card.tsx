import type { ReactNode } from "react";

/**
 * The one drawing of a verdict: collected twice on the left (volt only when
 * there is something to act on), every order checked on the right. Used by
 * Overview (workspace) and the merchant's Findings tab, so the figure is the
 * same size on both. Presentational only: no hooks, no server-only imports,
 * and never a logo.
 */
export function VerdictCard({ ariaLabel, twice, figure, compactFigure = false, sub, details, leftFooter, counts, spectrum, footer }: {
  ariaLabel: string;
  /** Collected twice > 0: the left panel is the view's volt element. */
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
    <section aria-label={ariaLabel} className="grid overflow-hidden rounded-xl border border-line bg-surface md:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className={`flex flex-col px-5 py-5 text-ink ${twice ? "bg-volt" : "border-b border-line md:border-r md:border-b-0"}`}>
        <p className="text-xs font-medium">Collected twice</p>
        <p className={`mt-1 font-semibold tracking-tight tabular-nums ${compactFigure ? "text-3xl" : "text-4xl sm:text-5xl"}`}>{figure}</p>
        <p className="mt-2 text-sm">{sub}</p>
        {details}
        {leftFooter && <div className="mt-auto pt-3">{leftFooter}</div>}
      </div>
      <div className="flex min-w-0 flex-col gap-4 px-5 py-5">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">{counts}</div>
        {spectrum}
        {footer && <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-soft">{footer}</div>}
      </div>
    </section>
  );
}
