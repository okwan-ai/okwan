import { PathGlyph, type PathShape } from "./ui/path-glyph";

/**
 * Beside sign-in and sign-up on wide screens: what a check returns, drawn
 * with the site's path glyphs. An example, labelled as one; no account's
 * data is ever shown before sign-in.
 */
const ROWS: { shape: PathShape; verdict: string; note: string; signal?: boolean }[] = [
  { shape: "twice", verdict: "Charged twice", note: "Both rails took the full total. It is owed back.", signal: true },
  { shape: "once", verdict: "Paid once", note: "Matched on the order reference." },
  { shape: "explained", verdict: "Fee explained", note: "The processor's fee accounts for the gap exactly." },
  { shape: "unpaid", verdict: "No payment", note: "In the ledger, and on no rail that was read." },
];

export function SignedOutAside() {
  return (
    <aside aria-label="What a check returns" className="hidden rounded-[20px] bg-night p-8 text-white lg:block">
      <p className="flex items-center gap-2 font-mono text-[11px] tracking-[0.14em] text-night-soft uppercase">
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />
        What a check returns · example
      </p>
      <p className="mt-4 text-[26px] leading-tight font-semibold tracking-[-0.03em]">
        Every order, traced to the rail that took the money.
      </p>
      <ul className="mt-8 divide-y divide-night-line border-y border-night-line">
        {ROWS.map((r) => (
          <li key={r.verdict} className="flex items-center gap-5 py-4">
            <PathGlyph shape={r.shape} light className="h-9 w-24" />
            <div className="min-w-0">
              <p className="flex items-center gap-2 text-sm font-semibold">
                {r.signal && <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-signal" />}
                {r.verdict}
              </p>
              <p className="mt-0.5 text-sm text-night-soft">{r.note}</p>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-6 text-sm text-night-soft">Read-only by construction: Okwan never moves money.</p>
    </aside>
  );
}
