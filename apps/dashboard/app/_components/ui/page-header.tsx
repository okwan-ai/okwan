import Link from "next/link";
import { Fragment, type ReactNode } from "react";

export type Crumb = { href: string; label: string };

/**
 * The page's title row: an optional trail back, the Fraunces title (with
 * an optional icon), one line of description, a meta row, and at most one
 * primary and one secondary action on the right, top-aligned with the title.
 * Buttons that spend requests carry their own cost note.
 *
 * `description` is one plain sentence (at most 140 characters), clamped to
 * two lines; anything richer belongs in `meta`.
 */
export function PageHeader({
  title,
  icon,
  breadcrumb,
  description,
  meta,
  actions,
}: {
  title: ReactNode;
  icon?: ReactNode;
  breadcrumb?: Crumb[];
  description?: string;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  const back = breadcrumb?.[breadcrumb.length - 1];
  return (
    <header className="mb-8 flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
      <div className="min-w-0 max-w-2xl">
        {breadcrumb && back && (
          <>
            <nav aria-label="Breadcrumb" className="mb-2 hidden text-sm text-ink-soft sm:block">
              <ol className="flex items-center gap-1.5">
                {breadcrumb.map((c) => (
                  <Fragment key={c.href}>
                    <li><Link href={c.href} className="hover:text-ink hover:underline">{c.label}</Link></li>
                    <li aria-hidden>/</li>
                  </Fragment>
                ))}
                <li aria-current="page" className="truncate text-ink">{title}</li>
              </ol>
            </nav>
            <Link href={back.href} className="mb-2 inline-flex min-h-11 items-center gap-1 text-sm text-ink-soft hover:text-ink sm:hidden">
              <span aria-hidden>‹</span> {back.label}
            </Link>
          </>
        )}
        <div className="flex items-center gap-3">
          {icon}
          <h1 className="font-display text-3xl font-normal tracking-tight sm:text-4xl">{title}</h1>
        </div>
        {description && <p className="mt-2 line-clamp-2 text-sm text-ink-soft">{description}</p>}
        {meta && <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-soft">{meta}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-start gap-2 pt-1 max-sm:w-full">{actions}</div>}
    </header>
  );
}

/** A titled part of a page. `id` makes it a link target; the first Section
 *  on a page sits flush with what precedes it. */
export function Section({
  title,
  description,
  aside,
  id,
  children,
}: {
  title: string;
  description?: string;
  aside?: ReactNode;
  id?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="mt-10 scroll-mt-6 first:mt-0">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-ink-soft">{description}</p>}
        </div>
        {aside && <div className="flex items-center gap-2 text-sm text-ink-soft">{aside}</div>}
      </div>
      {children}
    </section>
  );
}
