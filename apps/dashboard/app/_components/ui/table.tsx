import type { ComponentProps, ReactNode } from "react";

/** Tables scroll inside their own box, never the page. `flush` drops the
 *  box's border and surface for a table that sits inside a Card. */
export function Table({ children, minWidth = 640, label, flush = false }: {
  children: ReactNode;
  minWidth?: number;
  label?: string;
  flush?: boolean;
}) {
  return (
    <div
      className={flush ? "overflow-x-auto" : "overflow-x-auto rounded-xl border border-line bg-surface"}
      role="region"
      aria-label={label}
      tabIndex={label ? 0 : undefined}
    >
      <table className="w-full text-left text-sm" style={{ minWidth }}>
        {children}
      </table>
    </div>
  );
}

export function Th({ className = "", ...rest }: ComponentProps<"th">) {
  return (
    <th
      scope="col"
      className={`border-b border-line bg-canvas/60 px-5 py-2.5 text-xs font-medium whitespace-nowrap text-ink-soft ${className}`}
      {...rest}
    />
  );
}

export function Td({ className = "", ...rest }: ComponentProps<"td">) {
  return <td className={`px-5 py-3 align-middle ${className}`} {...rest} />;
}
