import "server-only";
import { cache } from "react";
import { api, session } from "./api";
import type { ImportFile, Layout } from "./imports";

/** A merchant's imported files, newest first. One call per render, shared
 *  by the merchant layout (the tab's count) and the Files tab. */
export const importedFiles = cache((tenantId: string) =>
  session().then((token) =>
    api<{ imports: ImportFile[] }>(`/v1/tenants/${encodeURIComponent(tenantId)}/imports`, { session: token }),
  ),
);

/** A merchant's saved column mappings, by name. */
export const savedLayouts = cache((tenantId: string) =>
  session().then((token) =>
    api<{ layouts: Layout[] }>(`/v1/tenants/${encodeURIComponent(tenantId)}/import-layouts`, { session: token }),
  ),
);
