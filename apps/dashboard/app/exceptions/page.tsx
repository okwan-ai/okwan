import { notFound } from "next/navigation";
import { api, session, tenantTree } from "@/lib/api";
import { type ApiAnswer, shownOf } from "@/lib/exceptions";
import { requireTenant } from "@/lib/guard";
import { ExceptionsPanel } from "../_components/exceptions-panel";
import { PageHeader } from "../_components/ui/page-header";

export const metadata = { title: "Open items" };

/**
 * Every finding of the workspace's merchants that still needs a look,
 * across all their checks, oldest first: one list a person works down. The
 * API keeps each item's key from run to run, so an item here is the same
 * one tomorrow, with its age, until a run no longer finds it. The account's
 * list holds each merchant's oldest items; `?merchant=` is one merchant's
 * whole list. Read from stored runs; nothing runs to draw it. A person
 * decides what an item means (Decide on each line); one explained or
 * resolved leaves the list, and `?merchant=…&state=closed` lists them.
 */
export default async function ExceptionsPage({ searchParams }: {
  searchParams: Promise<{ merchant?: string; after?: string; state?: string }>;
}) {
  await requireTenant();
  const { merchant, after, state } = await searchParams;
  const tree = await tenantTree();
  const one = typeof merchant === "string" && merchant ? merchant : null;
  const view = one && state === "closed" ? "closed" : "open";
  const query = one
    ? `?merchant=${encodeURIComponent(one)}&limit=1000${view === "closed" ? "&state=closed" : ""}`
    : typeof after === "string" && after ? `?after=${encodeURIComponent(after)}` : "";
  const r = tree
    ? await api<ApiAnswer>(`/v1/tenants/${encodeURIComponent(tree.self.id)}/exceptions${query}`, { session: await session() })
    : null;
  // A merchant outside the workspace answers 404, like one that does not exist.
  if (one && r && !r.ok && r.status === 404) notFound();
  const names: Record<string, string> = {};
  if (tree) for (const t of [tree.self, ...tree.children]) names[t.id] = t.name;
  return (
    <>
      <PageHeader
        title={view === "closed" ? "Closed items" : "Open items"}
        breadcrumb={one ? [{ href: "/exceptions", label: "Open items" }] : undefined}
        description={view === "closed"
          ? "What someone explained or resolved for this merchant, with who decided and why."
          : one
            ? "Everything this merchant's checks found that still needs a look, oldest first."
            : "Everything your merchants' checks found that still needs a look, oldest first. Decide what each one means: explain it, resolve it, or give it to someone."}
      />
      <ExceptionsPanel shown={shownOf(r && r.ok ? r.data : null, one, names, view)} />
    </>
  );
}
