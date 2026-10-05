"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { FOLD_READS, railLabel } from "@/lib/finding";
import { useTabTests } from "@/lib/tab-results";
import { ConnectorForm, type ConnectorView, TEST_LABEL } from "./connector-form";
import { Badge } from "./ui/badge";
import { BrandMark } from "./ui/brand-mark";
import { buttonClass } from "./ui/button";
import { SlideOver } from "./ui/dialog";

const GROUPS: { title: string; note?: string; names: string[] }[] = [
  { title: "Ledger", names: ["shopify"] },
  { title: "Payment rails in the check", names: ["paypal", "stripe"] },
  { title: "Not in the check yet", note: "Connected rails here are readable over REST, SQL and MCP, but the reconciliation doesn't fold them in yet.", names: ["paystack"] },
];

/** Rail tiles, grouped; Manage opens the credential form in a slide-over.
 *  `tenantKey` names whose tests the tiles show (the merchant, or "self"). */
export function ConnectionsGrid({ connectors, tenantId, tenantKey, fold = false }: {
  connectors: ConnectorView[];
  tenantId?: string;
  tenantKey: string;
  /** Show what the reconciliation still needs (a merchant's page). */
  fold?: boolean;
}) {
  // ?connect=paypal (from "Connect PayPal" anywhere) opens that form directly.
  const asked = useSearchParams().get("connect");
  const [managing, setManaging] = useState<string | null>(
    asked && connectors.some((c) => c.name === asked) ? asked : null,
  );
  // Also when the link is followed from this tab (the grid stays mounted).
  // Used once: the param is dropped from the URL, so a later save (which
  // refreshes the page) never reopens a form the user has moved on from.
  useEffect(() => {
    if (!asked) return;
    if (connectors.some((c) => c.name === asked)) setManaging(asked);
    const u = new URL(window.location.href);
    u.searchParams.delete("connect");
    window.history.replaceState(null, "", u);
  }, [asked]);
  const tests = useTabTests();
  const grouped = new Set(GROUPS.flatMap((g) => g.names));
  const groups = [
    ...GROUPS.map((g) => ({ ...g, items: g.names.flatMap((n) => connectors.filter((c) => c.name === n)) })),
    { title: "Other connectors", note: undefined, items: connectors.filter((c) => !grouped.has(c.name)) },
  ].filter((g) => g.items.length);
  const current = connectors.find((c) => c.name === managing) ?? null;

  const complete = (c: ConnectorView) => c.credential_fields.every((f) => c.stored.includes(f));
  const needed = FOLD_READS.map((name) => connectors.find((c) => c.name === name)).filter((c) => c !== undefined);
  const missing = needed.filter((c) => !complete(c));

  return (
    <div className="space-y-8">
      {fold && needed.length > 0 && (
        <section aria-label="What a check needs" className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border border-line bg-surface px-5 py-3">
          <p className="text-sm font-medium">A check needs</p>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {needed.map((c) => (
              <li key={c.name} className="inline-flex items-center gap-1.5">
                <span aria-hidden className={complete(c) ? "text-ok" : "text-ink-soft"}>{complete(c) ? "●" : "○"}</span>
                <BrandMark name={c.name} label={railLabel(c.name)} size={16} muted={!complete(c)} />
                {railLabel(c.name)}
                <span className="sr-only">{complete(c) ? " connected" : " not connected"}</span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-ink-soft tabular-nums">{needed.length - missing.length} of {needed.length}</p>
          {missing.length > 0 ? (
            <button type="button" onClick={() => setManaging(missing[0].name)} className={buttonClass("primary", "ml-auto")}>
              Connect {railLabel(missing[0].name)}
            </button>
          ) : (
            <p className="ml-auto text-sm text-ok"><span aria-hidden className="mr-1 font-mono">✓</span>Ready to check: use Run reconciliation above</p>
          )}
        </section>
      )}
      {groups.map((g) => (
        <section key={g.title} aria-labelledby={`group-${g.title.replace(/\W+/g, "-")}`}>
          <h2 id={`group-${g.title.replace(/\W+/g, "-")}`} className="mb-1 text-xs font-medium tracking-wide text-ink-soft uppercase">{g.title}</h2>
          {g.note ? <p className="mb-3 max-w-2xl text-xs text-ink-soft">{g.note}</p> : <div className="mb-3" />}
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {g.items.map((c) => {
              const test = tests[`${tenantKey}:${c.name}`];
              return (
                <li key={c.name} className="flex flex-col rounded-xl border border-line bg-surface p-4">
                  <div className="flex items-start justify-between gap-3">
                    <p className="flex items-center gap-2 font-semibold"><BrandMark name={c.name} label={railLabel(c.name)} size={18} muted={!complete(c)} />{railLabel(c.name)}</p>
                    <Status c={c} />
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-ink-soft">{c.description}</p>
                  <p className="mt-3 text-xs text-ink-soft">
                    {test ? (
                      <>Last test: <Badge tone={TEST_LABEL[test.status].tone} symbol={TEST_LABEL[test.status].symbol}>{TEST_LABEL[test.status].label}</Badge></>
                    ) : (
                      "Not tested in this session"
                    )}
                  </p>
                  <button
                    type="button"
                    onClick={() => setManaging(c.name)}
                    aria-label={`Manage ${railLabel(c.name)}`}
                    className={buttonClass("secondary", "mt-3 self-start")}
                  >
                    Manage
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      <SlideOver
        open={current !== null}
        onClose={() => setManaging(null)}
        title={current ? railLabel(current.name) : ""}
        description={current?.description}
      >
        {current && <ConnectorForm key={current.name} c={current} tenantId={tenantId} tenantKey={tenantKey} />}
      </SlideOver>
    </div>
  );
}

function Status({ c }: { c: ConnectorView }) {
  const complete = c.credential_fields.every((f) => c.stored.includes(f));
  if (complete) return <Badge tone="ok" symbol="●">Connected</Badge>;
  if (c.stored.length) return <Badge tone="warn" symbol="◐">Partial</Badge>;
  return <Badge tone="neutral" symbol="○">Not connected</Badge>;
}
