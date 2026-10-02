import { requireTenant } from "@/lib/guard";
import { IssueKey } from "../_components/issue-key";

export default async function KeyPage() {
  await requireTenant();
  return (
    <>
      <h1 className="font-display text-5xl font-light tracking-tight">API key</h1>
      <p className="mt-4 mb-10 max-w-2xl text-ink-soft">Issued once, shown once.</p>
      <IssueKey />
    </>
  );
}
