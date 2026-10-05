import { type RunDigest, missingFor, railLabel } from "@/lib/finding";
import { formatMinor } from "@/lib/money";
import { Badge } from "./ui/badge";

type Rails = { ready: string[]; known: boolean };

/** Whether a merchant can be checked: the fold reads every rail in
 *  FOLD_READS, and a missing one fails the whole run. */
export function Readiness({ m }: { m: Rails }) {
  if (!m.known) return <Badge symbol="?">Unavailable</Badge>;
  const missing = missingFor(m);
  if (!missing.length) return <Badge tone="ok" symbol="●">Ready to check</Badge>;
  return (
    <Badge symbol="○" title={`A check reads Shopify, PayPal and Stripe. Missing: ${missing.map(railLabel).join(", ")}.`}>
      Needs {missing.map(railLabel).join(" + ")}
    </Badge>
  );
}

/** The outcome of the last run, or readiness when there is none. Orders
 *  that couldn't be verified keep a clean result from reading as clean. */
export function RunStatus({ m, d }: { m: Rails; d: RunDigest | null | undefined }) {
  if (!d) return <Readiness m={m} />;
  if (!d.ok) return <Badge tone="danger" symbol="!" title={d.detail}>Couldn&apos;t run</Badge>;
  if (d.twice > 0) return <Badge tone="danger" symbol="×2">{d.open} finding{d.open === 1 ? "" : "s"}</Badge>;
  if (d.open > 0) return <Badge tone="warn" symbol="!">{d.open} finding{d.open === 1 ? "" : "s"}</Badge>;
  if (d.unverifiable > 0) return <Badge symbol="?">{d.unverifiable} couldn&apos;t verify</Badge>;
  if (d.unconfirmed > 0) {
    return (
      <Badge tone="ok" symbol="✓?" title="Paid once; another payment provider couldn't rule out a second payment.">
        Paid once · {d.unconfirmed} not ruled out
      </Badge>
    );
  }
  return <Badge tone="ok" symbol="✓">All paid once</Badge>;
}

/** The amount owed back (taken beyond the order totals), or why there's none. */
export function owedAmount(d: RunDigest | null | undefined): string {
  if (!d || !d.ok) return "—";
  if (d.twice === 0) return "None";
  return d.currency ? formatMinor(d.overMinor, d.currency) : `${d.twice} order${d.twice === 1 ? "" : "s"}`;
}
