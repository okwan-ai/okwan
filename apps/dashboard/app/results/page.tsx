import Link from "next/link";
import { session } from "@/lib/api";
import { requireTenant } from "@/lib/guard";
import { merchantsWithRails } from "@/lib/merchants";
import { RailChips } from "../_components/rail-chips";

export default async function ResultsPage() {
  await requireTenant();
  const merchants = await merchantsWithRails(await session());

  return (
    <>
      <h1 className="font-display text-5xl font-light tracking-tight">Results</h1>
      <p className="mt-4 mb-10 max-w-2xl text-ink-soft">
        A run reads a merchant&apos;s ledger and every rail it has connected, and says whether each order
        was paid once. Runs are not saved yet; each one reads live.
      </p>

      {!merchants ? (
        <p className="text-ink-soft">The Okwan API did not answer. Try again in a moment.</p>
      ) : merchants.length === 0 ? (
        <div className="card p-8">
          <p className="font-display text-2xl">Nothing to run yet.</p>
          <p className="mt-3 max-w-xl text-ink-soft">
            Add a merchant and connect its rails, then run its reconciliation here.
          </p>
          <Link href="/merchants" className="btn btn-primary mt-6">Add a merchant</Link>
        </div>
      ) : (
        <ul className="card divide-y divide-line">
          {merchants.map((m) => (
            <li key={m.tenant.id} className="flex flex-wrap items-center justify-between gap-4 px-6 py-4">
              <div className="min-w-0">
                <p className="font-display text-xl">{m.tenant.name}</p>
                <div className="mt-2"><RailChips m={m} /></div>
              </div>
              <Link href={`/merchants/${encodeURIComponent(m.tenant.id)}#run`} className="btn btn-secondary">
                Run
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
