"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ATTENTION, type AttentionRow, type Finding, truncated, unconfirmedRows } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import { SAMPLE_STEP } from "@/lib/sample-step";
import { FindingsTable, type MerchantFilter } from "./findings-table";
import { tickStep } from "./setup-checklist";
import { VerdictCard } from "./verdict-card";
import { buttonClass } from "./ui/button";
import { Card, CardBody, CardHeader, GRID } from "./ui/card";
import { CodeBlock } from "./ui/copy-button";
import { OutcomeSpectrum } from "./ui/outcome-spectrum";

/**
 * A finished check before anything is connected (§9 2026-11-11): the real
 * fold over a made-up store's week, drawn with the same verdict, table and
 * order drawer as a merchant's own check, so what a new account learns
 * here is what it will read later. Marked as sample data throughout, and
 * the way on is always the same: add a merchant and run its own.
 *
 * Seeing it ticks the first setup step (kept in this browser).
 */
export function SampleCheck({ finding: f, store, apiBase, account, nextHref, nextLabel }: {
  finding: Finding;
  store: string;
  apiBase: string;
  account: string;
  /** The account's own next step: add a merchant, or open one it has. */
  nextHref: string;
  nextLabel: string;
}) {
  useEffect(() => tickStep(account, SAMPLE_STEP), [account]);
  const s = f.summary;
  // One time for every row, fixed at first render.
  const [at] = useState(() => Date.now());
  const rows: AttentionRow[] = f.rows.map((r) => ({
    ...r, merchantId: "sample", merchantName: store, at, partial: f.partial || truncated(f), sample: true,
  }));
  const [filter, setFilter] = useState<MerchantFilter>(() => (f.rows.some((r) => ATTENTION.includes(r.outcome)) ? "findings" : "all"));
  const twice = s.collected_twice > 0;
  const cur = f.twice_currency;

  return (
    <div className="space-y-6">
      <p className="rounded-xl border border-line-strong bg-surface px-4 py-3 text-sm">
        <strong className="font-semibold">Sample data.</strong> Every order here is made up, and nothing of yours was read or
        stored. The check is the real one: it is what runs on a merchant&apos;s Shopify orders, PayPal and Stripe.
      </p>

      <VerdictCard
        ariaLabel="Verdict for the sample check"
        twice={twice}
        figure={twice
          ? (cur ? formatMinor(s.collected_twice_minor, cur) : `${s.collected_twice} orders`)
          : <><span aria-hidden className="mr-2 font-mono text-2xl text-ok">✓</span>None</>}
        sub={twice
          ? <>
            {s.collected_twice} order{s.collected_twice === 1 ? "" : "s"}
            {cur ? (s.overcollected_minor === s.collected_twice_minor
              ? " · all owed back to customers"
              : <> · <strong className="font-semibold">{formatMinor(s.overcollected_minor, cur)} owed back</strong></>) : null}
          </>
          : `No order taken twice in ${s.orders.toLocaleString("en-US")} checked.`}
        leftFooter={<p className="text-xs">{store} · sample data</p>}
        counts={<>
          <p>
            <span className="text-2xl font-semibold tabular-nums">{s.orders.toLocaleString("en-US")}</span>
            <span className="ml-1.5 text-sm text-ink-soft">orders checked</span>
          </p>
          <p className="text-sm text-ink-soft">
            {s.match_rate === null
              ? "Match rate withheld: some orders couldn't be verified"
              : <><span className="font-semibold text-ink tabular-nums">{(s.match_rate * 100).toFixed(1)}%</span> paid exactly once</>}
          </p>
        </>}
        spectrum={<OutcomeSpectrum summary={s} unconfirmed={unconfirmedRows(f)} animate />}
        footer={<span>Open an order to see what each system took.</span>}
      />

      <div className={GRID.mainAside}>
        <FindingsTable scope="merchant" rows={rows} apiBase={apiBase} filter={filter} onFilter={setFilter} exportName="sample-store" partial={f.partial} />
        <Card>
          <CardHeader title="Now your own" as="h2" />
          <CardBody className="space-y-3 text-sm">
            <ol className="list-decimal space-y-1.5 pl-5">
              <li>Add a merchant.</li>
              <li>Connect its Shopify, PayPal and Stripe. Okwan only reads them.</li>
              <li>Run its check. You get this page for its real orders.</li>
            </ol>
            <Link href={nextHref} className={buttonClass("primary", "w-full justify-center")}>{nextLabel}</Link>
          </CardBody>
        </Card>
      </div>

      <section aria-labelledby="sample-api" className="space-y-2">
        <h2 id="sample-api" className="text-sm font-semibold">The same result for your code</h2>
        <CodeBlock label="REST · no key needed for the sample" code={`curl -s "${apiBase}/v1/sample/reconciliations/across/rails?outcome=collected_twice"`} />
        <p className="text-xs text-ink-soft">
          For a real merchant the path drops <code className="font-mono">/sample</code> and takes a read-only key.{" "}
          <Link href="/agents" className="underline underline-offset-4 hover:text-ink">Set up a key or an agent →</Link>
        </p>
      </section>
    </div>
  );
}
