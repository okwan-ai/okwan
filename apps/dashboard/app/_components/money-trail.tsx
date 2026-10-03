import { type FindingRow, railLabel, sameCurrency } from "@/lib/finding";
import { formatMinor } from "@/lib/money";

/**
 * One order's money, drawn to scale: the order total from the ledger, then
 * what each rail took, stacked. A double collection is visibly twice the
 * order; the part beyond the order total is hatched. Amounts are integer
 * minor units throughout; only formatMinor turns them into text.
 *
 * The figure's caption states every number, so the bars are decoration for
 * sighted readers and hidden from assistive tech.
 */
export function MoneyTrail({ r }: { r: FindingRow }) {
  const cur = r.currency;
  // Amounts in different currencies can't share a scale; say so in words.
  if (!sameCurrency(r)) {
    return (
      <p className="text-xs text-ink">
        {r.paid.map((p) => `${railLabel(p.rail)} took ${formatMinor(p.minor, p.currency ?? cur)}`).join(" · ")} against a{" "}
        {formatMinor(r.total_minor, cur)} order: the rails took a different currency from the order, so they aren&apos;t drawn to scale.
      </p>
    );
  }
  const total = r.total_minor ?? 0;
  const takes = r.paid.filter((p) => p.minor !== null) as { rail: string; minor: number; currency: string | null }[];
  const taken = takes.reduce((n, p) => n + p.minor, 0);
  const scale = Math.max(total, taken, 1);
  const pct = (minor: number) => `${(minor / scale) * 100}%`;
  const over = taken - total;

  return (
    <figure className="min-w-0">
      <div aria-hidden className="grid grid-cols-[72px_minmax(0,1fr)] items-center gap-x-3 gap-y-2 text-[11px] text-ink-soft">
        <span>Order</span>
        <div className="relative h-5">
          <div className="absolute inset-y-0 left-0 rounded-sm border border-ink bg-surface" style={{ width: pct(total) }} />
        </div>
        <span>Taken</span>
        <div className="relative h-5 rounded-sm bg-canvas">
          {takes.length === 0 && <span className="absolute inset-0 flex items-center pl-2">no match on PayPal or Stripe</span>}
          {/* Three layers so the hatch never hides a rail's name: fills,
              then the excess hatch, then the labels on top. */}
          <div className="animate-settle absolute inset-y-0 left-0 flex" style={{ width: pct(taken) }}>
            {takes.map((p, i) => (
              <div
                key={`${p.rail}-${i}`}
                className="h-full border-r-2 border-surface bg-sky last:border-r-0"
                style={{ width: `${(p.minor / Math.max(taken, 1)) * 100}%` }}
              />
            ))}
          </div>
          {over > 0 && (
            <div className="bg-hatch-danger absolute inset-y-0 opacity-60" style={{ left: pct(total), width: pct(over) }} />
          )}
          <div className="absolute inset-y-0 left-0 flex" style={{ width: pct(taken) }}>
            {takes.map((p, i) => (
              <div
                key={`${p.rail}-${i}`}
                className="flex h-full min-w-0 items-center px-1"
                style={{ width: `${(p.minor / Math.max(taken, 1)) * 100}%` }}
              >
                <span className="truncate rounded-sm bg-surface/85 px-1 text-[11px] leading-4 font-medium text-ink">{railLabel(p.rail)}</span>
              </div>
            ))}
          </div>
          {total > 0 && <div className="absolute -inset-y-1 w-px bg-ink" style={{ left: pct(total) }} />}
        </div>
      </div>
      <figcaption className="mt-2 text-xs text-ink">
        {takes.length === 0 ? (
          <>Order total {formatMinor(r.total_minor, cur)}. No matching payment on PayPal or Stripe.</>
        ) : (
          <>
            {takes.map((p) => `${railLabel(p.rail)} took ${formatMinor(p.minor, p.currency ?? cur)}`).join(" · ")}
            {" "}of a {formatMinor(r.total_minor, cur)} order.
            {over > 0 && <> <strong className="font-semibold">{formatMinor(over, cur)} more than the order.</strong></>}
            {over < 0 && takes.length > 0 && <> {formatMinor(-over, cur)} less than the order.</>}
          </>
        )}
      </figcaption>
    </figure>
  );
}

/**
 * The trail at a glance, for lists: order total as an outline, what was
 * taken as a filled bar on the same scale, the excess hatched. Decorative:
 * the row it sits in states the same numbers in words.
 */
export function MiniTrail({ r }: { r: FindingRow }) {
  if (!sameCurrency(r)) return null;
  const total = r.total_minor ?? 0;
  const taken = r.paid.reduce((n, p) => n + (p.minor ?? 0), 0);
  const scale = Math.max(total, taken, 1);
  const pct = (minor: number) => `${(minor / scale) * 100}%`;
  return (
    <span aria-hidden className="flex w-full flex-col gap-1">
      <span className="relative block h-1.5">
        <span className="absolute inset-y-0 left-0 rounded-full border border-ink/70" style={{ width: pct(total) }} />
      </span>
      <span className="relative block h-1.5 rounded-full bg-canvas">
        <span className="absolute inset-y-0 left-0 rounded-full bg-sky" style={{ width: pct(taken) }} />
        {taken > total && <span className="bg-hatch-danger absolute inset-y-0 rounded-r-full" style={{ left: pct(total), width: pct(taken - total) }} />}
      </span>
    </span>
  );
}
