"use client";

import Link from "next/link";
import { Fragment } from "react";
import { type Declaration, defineOnce, defineOnceLine, roleLine, writeLabel } from "@/lib/connector-meta";
import { railLabel } from "@/lib/finding";
import { Badge } from "./ui/badge";
import { BrandMark } from "./ui/brand-mark";
import { IconChevron } from "./ui/icons";

/**
 * One connector as a logo tile.
 *
 * `catalog` (Integrations): no tenant is in view, so the logo is always full
 * colour. The whole tile links to the connector's home, and its id
 * (`cat-{name}`) keeps an old /catalog#cat-paypal link landing on it. Line
 * two is its role (and a ✎ badge when it can change something), line three
 * what its one declaration produces, counted from the declaration, line
 * four who has it connected.
 *
 * A client component because the tenant mode (a merchant's Connections)
 * pops on arrival; the logo is decorative and the name beside it is the text.
 */
export function ConnectorTile(props: { mode: "catalog"; c: Declaration; coverage: string }) {
  const { c, coverage } = props;
  const label = railLabel(c.name);
  const writes = defineOnce(c).writes > 0;
  return (
    <li>
      <Link
        href={`/integrations/${encodeURIComponent(c.name)}`}
        id={`cat-${c.name}`}
        className="flex h-full scroll-mt-20 items-center gap-3 rounded-xl border border-line bg-surface p-4 hover:border-ink/40 min-[900px]:scroll-mt-6"
      >
        <BrandMark name={c.name} label={label} tile size={40} />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-semibold">{label}</span>
          <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-soft">
            {roleLine(c.name)}
            {writes && <Badge tone="warn" symbol="✎">{writeLabel(c)}</Badge>}
          </span>
          <span className="mt-1 block text-xs text-ink-soft tabular-nums">
            {/* Wraps between counts, never inside one. */}
            {defineOnceLine(c).split(" · ").map((part, i) => (
              <Fragment key={i}>
                {i > 0 && " · "}
                <span className="whitespace-nowrap">{part}</span>
              </Fragment>
            ))}
          </span>
          {coverage && <span className="mt-0.5 block text-xs text-ink-soft">{coverage}</span>}
        </span>
        <IconChevron className="shrink-0 text-ink-soft" />
      </Link>
    </li>
  );
}
