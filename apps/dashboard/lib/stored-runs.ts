import "server-only";
import { cache } from "react";
import { api, session, tenantTree } from "./api";
import type { ApiRow, Summary } from "./finding";

/**
 * Stored reconciliation runs, as the API keeps them: every run as a
 * tenant, on any surface, with the trimmed rows okwan_recon produces.
 * Reading them is free and never runs anything; the pages that used to
 * run a fold on load read these instead.
 */
export type StoredRun = {
  id: string;
  tenant_id: string;
  kind: "across" | "pair";
  name: string;
  surface: "dashboard" | "rest" | "mcp" | string;
  status: "ok" | "failed";
  started_at: string;
  finished_at: string;
  summary: Summary | null;
  error: string | null;
  rows_total: number | null;
  /** The one currency of the collected-twice orders, when they share one. */
  twice_currency: string | null;
  /** Present on a run read by id or through `latest`; a listing omits it. */
  rows?: ApiRow[];
  has_more?: boolean;
};

export const FOLD = "rails";

/** The newest stored `rails` run for a tenant and each of its merchants,
 *  keyed by tenant id, with rows. One unmetered call per render. */
export const latestRuns = cache(async (tenantId: string): Promise<Record<string, StoredRun> | null> => {
  const r = await api<{ data: Record<string, StoredRun> }>(
    `/v1/tenants/${encodeURIComponent(tenantId)}/runs/latest?kind=across&name=${FOLD}`,
    { session: await session() },
  );
  return r.ok ? r.data.data : null;
});

/** The signed-in workspace's merchants' newest runs, or null when the API
 *  doesn't answer. */
export const myLatestRuns = cache(async (): Promise<Record<string, StoredRun> | null> => {
  const tree = await tenantTree();
  return tree ? latestRuns(tree.self.id) : null;
});

/** Run history for one tenant, newest first, without rows. */
export const runHistory = cache(async (tenantId: string, limit = 20): Promise<StoredRun[] | null> => {
  const r = await api<{ data: StoredRun[] }>(
    `/v1/tenants/${encodeURIComponent(tenantId)}/runs?name=${FOLD}&limit=${limit}`,
    { session: await session() },
  );
  return r.ok ? r.data.data : null;
});

/** One stored run with its rows; null when it doesn't exist for this tenant. */
export const storedRun = cache(async (tenantId: string, runId: string): Promise<StoredRun | null> => {
  const r = await api<StoredRun>(
    `/v1/tenants/${encodeURIComponent(tenantId)}/runs/${encodeURIComponent(runId)}`,
    { session: await session() },
  );
  return r.ok ? r.data : null;
});
