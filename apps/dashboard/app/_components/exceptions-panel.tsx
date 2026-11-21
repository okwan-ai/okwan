"use client";

import Link from "next/link";
import type { Line, Shown, View } from "@/lib/exceptions";
import { Decided, DecideItem } from "./decide-item";
import { ButtonLink } from "./ui/button";
import { Card } from "./ui/card";
import { EmptyState } from "./ui/empty-state";
import { IconAlert } from "./ui/icons";

/** Open this long or more, an item is drawn as overdue. */
const OLD_DAYS = 7;

/**
 * A merchant's own words (its name, a reference from its file or rail),
 * drawn as data: in an element and a direction of their own, never inside
 * a sentence the page writes. The text arrives with what cannot be seen
 * already spelled out (lib/exceptions.ts).
 */
function Theirs({ value, what }: { value: string; what: "name" | "ref" }) {
  return what === "name"
    ? <bdi dir="ltr" data-merchant-name>{value}</bdi>
    : <bdi dir="ltr" data-file-ref className="break-all rounded bg-canvas px-1.5 py-0.5 font-mono text-[13px]">{value}</bdi>;
}

function Item({ line, view }: { line: Line; view: View }) {
  const old = line.days !== null && line.days >= OLD_DAYS;
  return (
    <li className="grid gap-x-6 gap-y-2 px-5 py-4 sm:grid-cols-[minmax(0,1fr)_auto]" data-exception={line.key}>
      <div className="min-w-0">
        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="font-semibold text-ink">{line.what}</span>
          {line.refs.map((r) => (
            <span key={r.label} className="inline-flex min-w-0 items-baseline gap-1.5 text-sm text-ink-soft">
              {r.label} <Theirs value={r.value} what="ref" />
            </span>
          ))}
        </p>
        <p className="mt-1 text-sm text-ink-soft">
          <Link href={line.href} className="font-medium text-ink underline-offset-4 hover:underline">
            <Theirs value={line.merchant} what="name" />
          </Link>
          <span aria-hidden> · </span>
          {line.check}
          {line.dated && <><span aria-hidden> · </span>dated {line.dated}</>}
          {line.firstSeen && <><span aria-hidden> · </span>first seen {line.firstSeen}</>}
        </p>
        {line.decision && <div className="mt-2"><Decided d={line.decision} brief /></div>}
        {line.undecidable && <p className="mt-2 text-sm text-ink-soft" data-undecidable>{line.undecidable}</p>}
      </div>
      <div className="flex items-baseline gap-4 sm:flex-col sm:items-end sm:gap-1">
        {line.amount && <span className="font-mono text-[15px] font-semibold tabular-nums text-ink">{line.amount}</span>}
        {view === "open" && line.open && (
          <span className={`text-sm ${old ? "font-semibold text-danger" : "text-ink-soft"}`}>
            {line.open === "Today" ? "Open since today" : `Open ${line.open}`}
          </span>
        )}
        {!line.undecidable && <DecideItem line={line} view={view} />}
      </div>
    </li>
  );
}

/**
 * The list itself. A client component drawn from data, like the Files tab,
 * so no part of it can reach the browser after the element that holds it.
 */
export function ExceptionsPanel({ shown }: { shown: Shown }) {
  if (shown.state === "unavailable") {
    return <p className="text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</p>;
  }
  return (
    <div className="grid gap-6">
      {shown.notices.length > 0 && (
        <Card as="div" className="px-5 py-4 text-sm">
          <ul className="grid gap-1.5">
            {shown.notices.map((n) => (
              <li key={`${n.merchantId}:${n.text}`} className="text-ink-soft">
                <Theirs value={n.merchant} what="name" />: {n.text}
                {n.href && n.link && (
                  <> <Link href={n.href} className="font-medium text-ink underline underline-offset-4 hover:no-underline">{n.link}</Link></>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {shown.otherHref && (shown.view === "closed" || shown.closed > 0) && (
        <p className="text-sm">
          <Link href={shown.otherHref} className="font-medium text-ink underline underline-offset-4 hover:no-underline" data-other-view>
            {shown.view === "open"
              ? `See the ${shown.closed.toLocaleString("en-US")} item${shown.closed === 1 ? "" : "s"} someone explained or resolved`
              : "Back to the open items"}
          </Link>
        </p>
      )}
      {shown.total === 0 && shown.view === "closed" ? (
        <EmptyState icon={<IconAlert />} title="Nothing is closed">
          An item someone explains or resolves is listed here, with who decided and why.
        </EmptyState>
      ) : shown.total === 0 ? (
        <EmptyState
          icon={<IconAlert />}
          title={shown.checks === 0 ? "No check has run yet" : "Nothing is open"}
          action={shown.checks === 0 ? <ButtonLink href="/merchants">Go to merchants</ButtonLink> : undefined}
        >
          {shown.checks === 0
            ? "Run a check on a merchant, and anything it finds that needs a look is listed here until a later run no longer finds it, or someone explains or resolves it."
            : "Nothing the newest run of every check found still needs a look."}
        </EmptyState>
      ) : (
        <Card as="section" flush aria-label={shown.view === "open" ? "Open items" : "Closed items"}>
          <p className="border-b border-line px-5 py-3 text-sm text-ink-soft">
            {shown.merchant && <><Theirs value={shown.merchant.name} what="name" />: </>}
            {shown.total.toLocaleString("en-US")} {shown.view === "open" ? "open" : "closed"} item{shown.total === 1 ? "" : "s"}, oldest first.
            {shown.left > 0 && ` ${shown.lines.length.toLocaleString("en-US")} are listed here.`}
          </p>
          <ul className="divide-y divide-line">
            {shown.lines.map((line) => <Item key={line.key} line={line} view={shown.view} />)}
          </ul>
          {shown.nextHref && (
            <p className="border-t border-line px-5 py-3 text-sm">
              <Link href={shown.nextHref} className="font-medium text-ink underline underline-offset-4 hover:no-underline">
                The next 50 merchants
              </Link>
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
