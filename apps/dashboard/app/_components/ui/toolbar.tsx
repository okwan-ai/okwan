import type { ComponentProps, ReactNode } from "react";
import { IconSearch } from "./icons";

/**
 * The row above a list: filters on the left, search and selects on the
 * right. On phones the filters scroll sideways in one row with a right-edge
 * fade, and the right slot becomes a two-column grid on its own row.
 */
export function Toolbar({ filters, children }: { filters?: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      {filters && (
        <div className="-mx-4 flex min-w-0 overflow-x-auto px-4 [scrollbar-width:none] [mask-image:linear-gradient(to_right,black_85%,transparent)] sm:mx-0 sm:px-0 sm:[mask-image:none]">
          {filters}
        </div>
      )}
      {children && (
        <div className="ml-auto flex flex-wrap items-center gap-2 max-sm:grid max-sm:w-full max-sm:grid-cols-2">{children}</div>
      )}
    </div>
  );
}

/** A labelled search field for the toolbar's right slot. */
export function ToolbarSearch({ label, className = "", ...input }: ComponentProps<"input"> & { label: string }) {
  return (
    <label className="relative">
      <span className="sr-only">{label}</span>
      <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-ink-soft" />
      <input type="search" className={`field w-full py-2 pl-8 sm:w-56${className ? ` ${className}` : ""}`} {...input} />
    </label>
  );
}

/** A select for the toolbar's right slot. Label it with aria-label or a <label>. */
export function ToolbarSelect({ className = "", ...select }: ComponentProps<"select">) {
  return <select className={`field w-full py-2 sm:w-auto sm:min-w-44${className ? ` ${className}` : ""}`} {...select} />;
}
