import { api, session, targetTenant } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/** Issue a key. The secret passes through once, uncached, and is not kept.
 * `?tenant=` issues for a merchant (targetTenant); the API decides whether
 * the caller may. */
export async function POST(req: Request) {
  const body = await readBody<object>(req);
  if (body instanceof Response) return body;
  const token = await session();
  const tenant = await targetTenant(req);
  if (!token || !tenant) return fail("signed out — sign in again", 401);
  const r = await api<{ key_id: string; prefix: string; secret: string }>(
    `/v1/tenants/${encodeURIComponent(tenant)}/keys`,
    { method: "POST", session: token },
  );
  return r.ok ? json(r.data, 201) : fail(r.detail, r.status);
}
