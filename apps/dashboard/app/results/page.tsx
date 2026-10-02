import { requireTenant } from "@/lib/guard";

export default async function ResultsPage() {
  await requireTenant();
  return (
    <>
      <h1 className="font-display text-5xl font-light tracking-tight">Results</h1>
      <div className="card mt-10 p-8">
        <p className="font-display text-2xl">Reconciliation results land here.</p>
        <p className="mt-3 max-w-xl text-ink-soft">
          Until then, the same results are available with your API key over REST at{" "}
          <code className="font-mono">/v1/reconciliations</code> and to agents over the hosted MCP.
        </p>
      </div>
    </>
  );
}
