import "server-only";
import { api, type Tenant } from "./api";

export type MerchantRails = {
  tenant: Tenant;
  /** Connectors with every credential field stored. */
  ready: string[];
  /** Connectors with some fields stored. */
  partial: string[];
  /** False when the credentials listing failed for this merchant. */
  known: boolean;
};

/** The signed-in tenant's children, each with its configured connectors.
 * Null when the API does not answer. */
export async function merchantsWithRails(token: string | null): Promise<MerchantRails[] | null> {
  const [tenants, connectors] = await Promise.all([
    api<{ children: Tenant[] }>("/v1/tenants", { session: token }),
    api<{ name: string; credential_fields: string[] }[]>("/v1/connectors"),
  ]);
  if (!tenants.ok || !connectors.ok) return null;
  return Promise.all(
    tenants.data.children.map(async (tenant) => {
      const s = await api<{ configured: Record<string, string[]> }>(
        `/v1/tenants/${encodeURIComponent(tenant.id)}/credentials`,
        { session: token },
      );
      const fields = s.ok ? s.data.configured : {};
      const ready = connectors.data
        .filter((c) => c.credential_fields.every((f) => fields[c.name]?.includes(f)))
        .map((c) => c.name);
      const partial = connectors.data
        .filter((c) => fields[c.name]?.length && !ready.includes(c.name))
        .map((c) => c.name);
      return { tenant, ready, partial, known: s.ok };
    }),
  );
}
