import Link from "next/link";
import type { ReactNode, Ref } from "react";

/**
 * The phone form of a table: one divided list, shown below sm, where the
 * desktop table sits in "hidden sm:block". Plain component, so a server
 * page and a client table can both use it.
 */
export function RowCardList({ label, children }: { label: string; children: ReactNode }) {
  return (
    <ul aria-label={label} className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-surface sm:hidden">
      {children}
    </ul>
  );
}

/**
 * One row as three lines: the title and its money, then badges or logos,
 * then a sentence and a meta line. The whole card is one link or one
 * button, so nothing interactive may sit inside it.
 */
export function RowCard({
  href,
  onClick,
  buttonRef,
  title,
  money,
  line2,
  sentence,
  meta,
}: {
  href?: string;
  onClick?: () => void;
  buttonRef?: Ref<HTMLButtonElement>;
  title: ReactNode;
  money?: { value: string; label?: string; soft?: boolean };
  line2?: ReactNode;
  sentence?: ReactNode;
  meta?: ReactNode;
}) {
  const cls = "block w-full px-4 py-3 text-left";
  const body = (
    <>
      <span className="flex items-center justify-between gap-3">
        <span className="min-w-0">{title}</span>
        {money && (
          <span className={`text-right text-sm font-semibold tabular-nums${money.soft ? " text-ink-soft" : ""}`}>
            {money.value}
            {money.label && <span className="block text-xs font-normal text-ink-soft">{money.label}</span>}
          </span>
        )}
      </span>
      {line2 && <span className="mt-1.5 flex flex-wrap items-center gap-2">{line2}</span>}
      {sentence && <span className="mt-1 block text-sm">{sentence}</span>}
      {meta && <span className="mt-1 block text-xs text-ink-soft">{meta}</span>}
    </>
  );
  return (
    <li>
      {href ? (
        <Link href={href} className={cls}>{body}</Link>
      ) : onClick ? (
        <button type="button" ref={buttonRef} onClick={onClick} className={cls}>{body}</button>
      ) : (
        <div className={cls}>{body}</div>
      )}
    </li>
  );
}
