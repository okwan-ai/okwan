"use client";

import { useState } from "react";
import { railLabel } from "@/lib/finding";
import { useTabTests } from "@/lib/tab-results";
import { ConnectorForm, type ConnectorView, TEST_LABEL } from "./connector-form";
import { Badge } from "./ui/badge";
import { SlideOver } from "./ui/dialog";

const GROUPS: { title: string; names: string[] }[] = [
  { title: "Ledger", names: ["shopify"] },
  { title: "Payment rails", names: ["stripe", "paypal", "paystack"] },
];

/** Rail tiles, grouped; Manage opens the credential form in a slide-over.
 *  `tenantKey` names whose tests the tiles show (the merchant, or "self"). */
export function ConnectionsGrid({ connectors, tenantId, tenantKey }: {
  connectors: ConnectorView[];
  tenantId?: string;
  tenantKey: string;
}) {
  const [managing, setManaging] = useState<string | null>(null);
  const tests = useTabTests();
  const grouped = new Set(GROUPS.flatMap((g) => g.names));
  const groups = [
    ...GROUPS.map((g) => ({ ...g, items: connectors.filter((c) => g.names.includes(c.name)) })),
    { title: "Other", items: connectors.filter((c) => !grouped.has(c.name)) },
  ].filter((g) => g.items.length);
  const current = connectors.find((c) => c.name === managing) ?? null;

  return (
    <div className="space-y-8">
      {groups.map((g) => (
        <section key={g.title} aria-labelledby={`group-${g.title}`}>
          <h3 id={`group-${g.title}`} className="mb-3 text-xs font-medium tracking-wide text-ink-soft uppercase">{g.title}</h3>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {g.items.map((c) => {
              const test = tests[`${tenantKey}:${c.name}`];
              return (
                <li key={c.name} className="flex flex-col rounded-xl border border-line bg-surface p-4">
                  <div className="flex items-start justify-between gap-3">
                    <p className="font-semibold">{railLabel(c.name)}</p>
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
                    className="mt-3 inline-flex min-h-11 items-center self-start rounded-lg border border-line px-3 text-sm font-medium hover:border-ink"
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
