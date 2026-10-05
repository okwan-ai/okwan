import type { AriaAttributes, ElementType, ReactNode } from "react";

/**
 * Every bordered block on a page is a Card, a Table, an EmptyState (dashed),
 * the navy AgentPanel or the VerdictCard. A Card is a surface with a hairline;
 * `flush` clips its corners for a table or list that runs edge to edge.
 */
export function Card({
  as = "section",
  flush = false,
  className = "",
  children,
  ...rest
}: {
  as?: "section" | "div" | "article" | "aside" | "li";
  flush?: boolean;
  className?: string;
  children: ReactNode;
  id?: string;
  role?: string;
} & AriaAttributes) {
  const Tag: ElementType = as;
  return (
    <Tag className={`rounded-xl border border-line bg-surface${flush ? " overflow-hidden" : ""}${className ? ` ${className}` : ""}`} {...rest}>
      {children}
    </Tag>
  );
}

/** The card's name, an optional one-line description, and its actions.
 *  Inside a page Section the title is an h3. */
export function CardHeader({
  title,
  description,
  actions,
  as = "h2",
  id,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  as?: "h2" | "h3";
  id?: string;
}) {
  const H = as;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
      <div className="min-w-0">
        <H id={id} className="text-sm font-semibold">{title}</H>
        {description && <div className="mt-0.5 text-xs text-ink-soft">{description}</div>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Padded body, or a divided list (each row pads itself) when `list`. */
export function CardBody({ list = false, className = "", children }: { list?: boolean; className?: string; children: ReactNode }) {
  if (list) return <ul className={`divide-y divide-line${className ? ` ${className}` : ""}`}>{children}</ul>;
  return <div className={`px-5 py-4${className ? ` ${className}` : ""}`}>{children}</div>;
}

export function CardFooter({ children }: { children: ReactNode }) {
  return <div className="border-t border-line px-5 py-3 text-xs text-ink-soft">{children}</div>;
}

/** The only card grids. gap-3 card grids are retired. */
export const GRID = {
  tiles: "grid gap-4 sm:grid-cols-2 xl:grid-cols-3",
  stats: "grid grid-cols-2 gap-4 lg:grid-cols-4",
  mainAside: "grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]",
} as const;
