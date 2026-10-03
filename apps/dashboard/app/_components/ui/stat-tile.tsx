import Link from "next/link";
import type { ReactNode } from "react";

/** A KPI. `hero` is the page's one volt figure; at most one per page. */
export function StatTile({
  label,
  value,
  sub,
  hero = false,
  action,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  hero?: boolean;
  action?: { href: string; label: string };
}) {
  return (
    <div
      className={`flex min-w-0 flex-col rounded-xl border px-5 py-4 ${
        hero ? "border-volt-deep bg-volt text-ink" : "border-line bg-surface"
      }`}
    >
      <p className={`text-xs font-medium ${hero ? "text-ink" : "text-ink-soft"}`}>{label}</p>
      <p className={`mt-1 truncate font-semibold tracking-tight tabular-nums ${hero ? "text-4xl" : "text-2xl"}`}>
        {value}
      </p>
      {sub && <p className={`mt-1 text-xs ${hero ? "text-ink" : "text-ink-soft"}`}>{sub}</p>}
      {action && (
        <Link
          href={action.href}
          className="mt-auto inline-flex min-h-11 items-center gap-1 self-start pt-2 text-sm font-medium underline underline-offset-4"
        >
          {action.label} <span aria-hidden>→</span>
        </Link>
      )}
    </div>
  );
}
