"use client";

import Link from "next/link";
import { Fragment } from "react";
import { type Declaration, defineOnce, defineOnceLine, isComplete, roleLine, roleOf, writeLabel } from "@/lib/connector-meta";
import { railLabel } from "@/lib/finding";
import { usePop, useTabTests } from "@/lib/tab-results";
import { type ConnectorView, TEST_LABEL } from "./connector-form";
import { Badge } from "./ui/badge";
import { BrandMark } from "./ui/brand-mark";
import { buttonClass } from "./ui/button";
import { IconChevron } from "./ui/icons";

type Props =
  | { mode: "tenant"; c: ConnectorView; tenantKey: string; onOpen: () => void }
  | { mode: "catalog"; c: Declaration; coverage: string };

/**
 * One connector as a logo tile.
 *
 * `tenant` (a merchant's or the workspace's Connections): the 40px logo is
 * grey on a dashed tile until every credential field is stored, then full
 * colour. A corner disc shows this tab's last test ("!" when the read
 * failed, "✓" when it worked), and the tile pops once on the connect that
 * caused it, after the sheet closes (usePop("grid")). Connect while
 * incomplete, Manage once connected; the test line appears only after a
 * test in this tab.
 *
 * `catalog` (Integrations): no tenant is in view, so the logo is always full
 * colour. The whole tile links to the connector's home, and its id
 * (`cat-{name}`) keeps an old /catalog#cat-paypal link landing on it. Line
 * two is its role (and a ✎ badge when it can change something), line three
 * what its one declaration produces, counted from the declaration, line
 * four who has it connected.
 *
 * The logo is decorative; the name beside it is the text.
 */
export function ConnectorTile(props: Props) {
  return props.mode === "tenant" ? <TenantTile {...props} /> : <CatalogTile {...props} />;
}

function TenantTile({ c, tenantKey, onOpen }: { c: ConnectorView; tenantKey: string; onOpen: () => void }) {
  const label = railLabel(c.name);
  const complete = isComplete(c);
  const test = useTabTests()[`${tenantKey}:${c.name}`];
  const { pop, onAnimationEnd } = usePop("grid", tenantKey, c.name, complete);
  const corner = test?.status === "failed" ? "alert" : test?.status === "rows" || test?.status === "empty" ? "ok" : undefined;
  const action = complete ? "Manage" : "Connect";
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface p-4">
      <BrandMark name={c.name} label={label} tile size={40} muted={!complete} corner={corner} pop={pop} onAnimationEnd={onAnimationEnd} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-semibold">{label}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-soft">
          <ConnectionStatus c={c} />
          {roleOf(c.name)}
          {defineOnce(c).writes > 0 && <Badge tone="warn" symbol="✎">{writeLabel(c)}</Badge>}
        </p>
      </div>
      <button
        type="button"
        onClick={onOpen}
        data-connector={c.name}
        aria-label={`${action} ${label}`}
        className={buttonClass(complete ? "ghost" : "secondary")}
      >
        {action}
      </button>
      {test && (
        <p className="basis-full border-t border-line pt-2 text-xs text-ink-soft">
          Last test:{" "}
          <Badge tone={TEST_LABEL[test.status].tone} symbol={TEST_LABEL[test.status].symbol}>{TEST_LABEL[test.status].label}</Badge>
        </p>
      )}
    </li>
  );
}

/** "● Connected", "◐ Partial" or "○ Not connected", from the stored field names. */
export function ConnectionStatus({ c }: { c: Pick<ConnectorView, "credential_fields" | "stored"> }) {
  if (isComplete(c)) return <Badge tone="ok" symbol="●">Connected</Badge>;
  if (c.stored.length) return <Badge tone="warn" symbol="◐">Partial</Badge>;
  return <Badge tone="neutral" symbol="○">Not connected</Badge>;
}

function CatalogTile({ c, coverage }: { c: Declaration; coverage: string }) {
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
