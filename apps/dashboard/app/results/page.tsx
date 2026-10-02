import { requireTenant } from "@/lib/guard";

export default async function ResultsPage() {
  await requireTenant();
  return (
    <>
      <h1 className="font-display text-5xl font-light tracking-tight">Results</h1>
      <div className="card mt-10 p-8">
        <p className="font-display text-2xl">Reconciliation results land here.</p>
        <p className="mt-3 max-w-xl text-ink-soft">
          Until then, your agents can run them with your API key over the hosted MCP at{" "}
          <code className="font-mono">/mcp/</code>, using <code className="font-mono">okwan_reconcile</code>.
        </p>
      </div>
    </>
  );
}
