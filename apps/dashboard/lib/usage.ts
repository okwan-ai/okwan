import "server-only";
import { cache } from "react";
import { api, session, tenantTree } from "./api";
import type { Usage } from "./usage-shape";

/** GET /v1/tenants/{id}/usage: the plan the tenant is held to, the month so
 *  far, and the hourly counters for the window. Reading it is not metered.
 *  One call per tenant per render. */
export const usageFor = cache(async (tenantId: string, days = 30) =>
  api<Usage>(`/v1/tenants/${encodeURIComponent(tenantId)}/usage?days=${days}`, { session: await session() }),
);

/** The signed-in workspace's usage, or null when the API doesn't answer. */
export const myUsage = cache(async (days = 30): Promise<Usage | null> => {
  const tree = await tenantTree();
  if (!tree) return null;
  const r = await usageFor(tree.self.id, days);
  return r.ok ? r.data : null;
});
