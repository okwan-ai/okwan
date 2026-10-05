import Link from "next/link";
import type { ReactNode } from "react";
import { IconChevron } from "./icons";

/**
 * The policy behind a page, folded away: a few short lines in the style
 * of "How this adds up", always ending in the one place that explains how
 * Okwan handles data.
 */
export function HowItWorks({ label = "How this works", items }: { label?: string; items: ReactNode[] }) {
  return (
    <details className="group text-sm">
      <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-1 text-ink-soft underline-offset-4 hover:text-ink hover:underline [&::-webkit-details-marker]:hidden">
        {label}
        <IconChevron className="h-4 w-4 transition-transform group-open:rotate-90" />
      </summary>
      <div className="mt-2 max-w-xl space-y-1.5 rounded-lg border border-line bg-surface px-4 py-3 text-xs text-ink-soft">
        {items.map((item, i) => <p key={i}>{item}</p>)}
        <p>
          <Link href="/settings?tab=security" className="underline underline-offset-4">More in Security <span aria-hidden>→</span></Link>
        </p>
      </div>
    </details>
  );
}
