import "server-only";
import { cache } from "react";
import { api, session, tenantTree, type Tenant } from "./api";

export type Connector = {
  name: string;
  version?: string;
  description: string;
  /** Resource → its operations, as the SDK declares them. */
  resources?: Record<string, string[]>;
  credential_fields: string[];
  probe: string | null;
  /** SQL tables this connector generates ("stripe.charges"). */
  sql_tables?: string[];
};

export type MerchantRails = {
  tenant: Tenant;
  /** Connectors with every credential field stored. */
  ready: string[];
  /** Connectors with some fields stored. */
  partial: string[];
  /** False when the credentials listing failed for this merchant. */
  known: boolean;
};

/** The connector catalog. Public and unmetered; one call per render. */
export const connectors = cache(() => api<Connector[]>("/v1/connectors"));

/** A tenant's stored credential field names, never values. One call per
 * tenant per render, so the merchant layout and its page share it. */
export const configured = cache((tenantId: string) =>
  session().then((token) =>
    api<{ configured: Record<string, string[]> }>(
      `/v1/tenants/${encodeURIComponent(tenantId)}/credentials`,
      { session: token },
    ),
  ),
);

export function railState(catalog: Connector[], fields: Record<string, string[]>) {
  const ready = catalog
    .filter((c) => c.credential_fields.every((f) => fields[c.name]?.includes(f)))
    .map((c) => c.name);
  const partial = catalog
    .filter((c) => fields[c.name]?.length && !ready.includes(c.name))
    .map((c) => c.name);
  return { ready, partial };
}

/** The signed-in tenant's children, each with its configured connectors.
 * Null when the API does not answer. Cached per render. */
export const merchantsWithRails = cache(async (): Promise<MerchantRails[] | null> => {
  const [tree, catalog] = await Promise.all([tenantTree(), connectors()]);
  if (!tree || !catalog.ok) return null;
  return Promise.all(
    tree.children.map(async (tenant) => {
      const s = await configured(tenant.id);
      return { tenant, ...railState(catalog.data, s.ok ? s.data.configured : {}), known: s.ok };
    }),
  );
});
