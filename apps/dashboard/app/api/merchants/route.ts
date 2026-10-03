import { api, session, type Tenant } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/** Add a merchant: a child tenant under the signed-in tenant. */
export async function POST(req: Request) {
  const body = await readBody<{ name?: unknown }>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const name = typeof body.name === "string" ? body.name.trim() : "";
  if (!name) return fail("a merchant needs a name", 400);
  const r = await api<Tenant>("/v1/tenants", { method: "POST", session: token, body: { name } });
  return r.ok ? json(r.data, 201) : fail(r.detail, r.status);
}
