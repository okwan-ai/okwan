import type { ComponentProps, ReactNode } from "react";

/** Tables scroll inside their own box, never the page. `flush` drops the
 *  box's border and surface for a table that sits inside a Card; the Card
 *  clips its corners, so the scroll region's focus ring is drawn inside. */
export function Table({ children, minWidth = 640, label, flush = false }: {
  children: ReactNode;
  minWidth?: number;
  label?: string;
  flush?: boolean;
}) {
  return (
    <div
      className={flush ? "overflow-x-auto focus-visible:-outline-offset-2" : "overflow-x-auto rounded-[22px] border border-line/80 bg-surface shadow-float"}
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
      className={`border-b border-line bg-accent-soft/40 px-5 py-3 text-[13px] font-semibold whitespace-nowrap text-ink ${className}`}
      {...rest}
    />
  );
}

export function Td({ className = "", ...rest }: ComponentProps<"td">) {
  return <td className={`px-5 py-3.5 align-middle ${className}`} {...rest} />;
}
