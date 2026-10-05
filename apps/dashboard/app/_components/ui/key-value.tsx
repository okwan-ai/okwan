import type { ReactNode } from "react";

/**
 * Facts as label and value, one per row: Settings (Workspace and Security),
 * the Agents endpoints, a connector's Details and "Now readable as". One
 * 200px label column everywhere; rows stack on phones.
 */
export function KeyValueList({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <dl aria-label={label} className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface">
      {children}
    </dl>
  );
}

/** One fact. `id` makes the row a link target (scroll-mt keeps it clear of the top). */
export function KeyValueRow({ label, id, children }: { label: ReactNode; id?: string; children: ReactNode }) {
  return (
    <div id={id} className="grid scroll-mt-6 gap-1 px-5 py-3 text-sm sm:grid-cols-[200px_minmax(0,1fr)]">
      <dt className="text-ink-soft">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}
