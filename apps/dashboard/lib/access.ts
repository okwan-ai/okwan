import "server-only";
import { cache } from "react";
import type { Access } from "./access-shape";
import { api, session } from "./api";

/** The newest reads the Access log tab shows. The API allows up to 1,000. */
export const ACCESS_LIMIT = 200;

/** GET /v1/tenants/{id}/access: every read of the tenant's data and its
 *  merchants', newest first, and the alerts they raised. Reading it is not
 *  metered and is not itself logged. One call per tenant per render. */
export const accessFor = cache(async (tenantId: string, days = 30) =>
  api<Access>(`/v1/tenants/${encodeURIComponent(tenantId)}/access?days=${days}&limit=${ACCESS_LIMIT}`, {
    session: await session(),
  }),
);
