import Link from "next/link";

export type TabItem = { href: string; label: string; active: boolean; count?: number };

/** URL-driven tabs: each is a link, the active one marked for assistive tech. */
export function Tabs({ items, label }: { items: TabItem[]; label: string }) {
  return (
    <nav aria-label={label} className="-mx-1 overflow-x-auto border-b border-line">
      <ul className="flex min-w-max gap-1 px-1">
        {items.map((t) => (
          <li key={t.href}>
            <Link
              href={t.href}
              scroll={false}
              aria-current={t.active ? "page" : undefined}
              className={`-mb-px inline-flex min-h-11 items-center gap-2 border-b-2 px-3 text-sm font-medium ${
                t.active ? "border-ink text-ink" : "border-transparent text-ink-soft hover:text-ink"
              }`}
            >
              {t.label}
              {t.count !== undefined && (
                <span className="rounded-full bg-ink/5 px-1.5 text-xs tabular-nums text-ink-soft">{t.count}</span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
