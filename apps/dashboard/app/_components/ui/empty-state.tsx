import type { ReactNode } from "react";
import { IconCheck } from "./icons";

/**
 * An empty state that says what the thing is for: a title, one line of
 * context, up to three plain benefits, and the one action that fills it.
 * The icon is decorative; the words carry it.
 */
export function EmptyState({ title, children, benefits, action, icon }: {
  title: string;
  children?: ReactNode;
  benefits?: string[];
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface px-6 py-10 text-center">
      {icon && (
        <span aria-hidden className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-canvas text-ink-soft">
          {icon}
        </span>
      )}
      <p className="text-base font-semibold">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-md text-sm text-ink-soft">{children}</div>}
      {benefits && benefits.length > 0 && (
        <ul className="mx-auto mt-4 inline-flex flex-col items-start gap-1.5 text-left text-sm">
          {benefits.map((b) => (
            <li key={b} className="flex items-start gap-2">
              <IconCheck className="mt-0.5 h-4 w-4 shrink-0 text-ok" />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      )}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}
